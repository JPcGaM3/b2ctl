"""b2ctl.core — assemble the full disk picture in one place.

scan() does the whole pipeline: enumerate raw disks -> map physical bays
(sas2ircu) -> read SMART -> attach ZFS pool/vdev membership -> assess level.
Both the one-shot `status` view and the interactive `watch` loop build on it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import backend as _backend_mod, baymap, smart, zfs, spec
from .common import Disk, assess, warn, Y, N


def _attach_membership(disks: list[Disk]) -> None:
    """Attach ZFS membership, or mark every disk pool-UNKNOWN if zpool is silent.

    The ONE place the unknown state is set. Every 'is this disk free?' question in
    the codebase already routes through Disk.is_poolable, so clearing pool_known
    here makes watch's [a]ssign / [c]reate / aux-vdev menus refuse without any of
    them growing a check of their own (F-143). warn() is the JSON-mode collector,
    so the web service gets the same sentence in warnings[] rather than a
    confidently empty disk list.
    """
    try:
        zfs.attach_membership(disks, zfs.topology())
    except zfs.ZfsUnavailable as exc:
        warn(f"{Y}⚠ zpool did not answer ({exc}) — pool membership is UNKNOWN. "
             f"Disk assignment and pool creation are disabled until it does.{N}")
        for d in disks:
            d.pool_known = False


def _bay_sort_key(d: Disk):
    """Numeric-aware bay sort so '0:2' precedes '0:10' (not lexicographic).

    A bay like 'enc:slot' sorts by (enc, 0, int(slot)); free-form labels
    (PCIe2:0, map overrides) and bay-less disks fall back to a string key so the
    order stays stable (F-078)."""
    bay = d.bay or "zz"
    enc, _, slot = bay.rpartition(":")
    if enc and slot.isdigit():
        return (enc, 0, int(slot), d.dev)
    return (bay, 1, 0, d.dev)


def scan(tbw_table=None, *, rescue: bool = False) -> list[Disk]:
    """Assemble the full disk picture.

    rescue=False (the DEFAULT, used by the read path `status`/`scan_one`) never
    touches udev: ghost disks are surfaced as rows tagged 'run [u]dev rescue in
    watch', so the read path stays side-effect-free (CLAUDE.md §9). Only watch
    passes rescue=True — after an explicit [y/N] — to fire the udevadm trigger.
    """
    bk = _backend_mod.get_backend()
    bm = bk.bay_map() if bk.have_tool() else {}
    disks = bk.enumerate_disks()
    bk.attach_bays(disks, bm=bm)

    potential_ghosts = bk.get_ghost_disks(disks, bm=bm)
    survivors: list[Disk] = list(potential_ghosts)
    if potential_ghosts and rescue:
        with ThreadPoolExecutor(max_workers=len(potential_ghosts)) as executor:
            results = list(executor.map(bk.udev_rescue_ghost,
                                        [g.serial for g in potential_ghosts]))
        survivors = [g for g, ok in zip(potential_ghosts, results) if not ok]
        for g in survivors:
            g.reasons = ["OS_REJECTED", "udevadm rescue failed — wipe via [a]ssign"]
        if any(results):
            disks = bk.enumerate_disks()
            bk.attach_bays(disks, bm=bm)
            survivors = bk.get_ghost_disks(disks, bm=bm)
            for g in survivors:
                g.reasons = ["OS_REJECTED", "udevadm rescue failed — wipe via [a]ssign"]
    else:
        for g in survivors:
            g.reasons = ["OS_REJECTED",
                         "run [u]dev rescue in watch to recover, or wipe via [a]ssign"]
    disks.extend(survivors)

    tbw_table = tbw_table if tbw_table is not None else spec.load()
    smart_targets = [d for d in disks if d.health != "GHOST"]
    # Direct smartctl on independent /dev/sdX is subprocess-wait bound, so one
    # thread per disk is safe (F-077). But megaraid passthrough (d.smart_dtype set)
    # all funnels through ONE PERC that serializes IOCTLs — 16-way there saturates
    # the controller and slow disks blow the per-probe timeout -> NOREAD. Read the
    # direct group wide, the megaraid group at a small configurable cap.
    from . import config as _cfg
    mega = [d for d in smart_targets if d.smart_dtype]
    direct = [d for d in smart_targets if not d.smart_dtype]

    def _read_pool(targets, workers):
        if not targets:
            return
        with ThreadPoolExecutor(max_workers=min(workers, max(1, len(targets)))) as executor:
            list(executor.map(lambda d: smart.read(d, tbw_table), targets))

    _read_pool(direct, 16)
    _read_pool(mega, _cfg.smart_config().get("megaraid_workers", 4))

    # Enterprise SAS drives often have no SERIAL in lsblk (udev doesn't query it).
    # SMART now has the real serial. Re-run bay assignment so those disks get a bay,
    # then drop ghosts whose serial is now covered by a real block device.
    bk.attach_bays([d for d in disks if d.health != "GHOST"], bm=bm)
    real_serials = [d.serial for d in disks if d.health != "GHOST" and d.serial]
    # Drop a ghost once a real block device covers its serial. Use fuzzy
    # (prefix) matching to agree with ghost CREATION (get_ghost_disks), so a SAS
    # drive whose sas2ircu serial is a truncation of its SMART serial doesn't
    # linger as a permanent phantom CRITICAL row (F-015).
    disks = [d for d in disks
             if not (d.health == "GHOST" and d.serial
                     and any(baymap.serial_match(d.serial, rs) for rs in real_serials))]

    _attach_membership(disks)

    inuse_spare_pools = {d.pool for d in disks
                         if d.is_spare and d.vdev_state == "INUSE" and d.pool}
    if inuse_spare_pools:
        for pool in inuse_spare_pools:
            tok_to_bay = {d.pool_token: d.bay
                          for d in disks if d.pool == pool and d.pool_token and d.bay}
            replacing_map = zfs.spares_replacing(pool)
            for d in disks:
                if (d.pool == pool and d.is_spare and d.vdev_state == "INUSE"
                        and d.pool_token in replacing_map):
                    replaced_tok = replacing_map[d.pool_token]
                    d.spare_replacing = tok_to_bay.get(replaced_tok)

    for d in disks:
        if d.health != "GHOST":
            assess(d)
    disks.sort(key=_bay_sort_key)
    return disks


def scan_light(tbw_table=None) -> list[Disk]:
    """Enumerate + bay-map + ZFS membership, WITHOUT reading SMART.

    For paths that only need identity + topology (locate, token resolution):
    they match on bay/serial/dev/by_id and check vdev membership for the
    resilvering-LED guard, but never read wear/TBW/health — so the per-disk
    smartctl fan-out is pure waste there (F-102). Still goes through the backend,
    so RAID-mode disks get smart_dtype/pd_state/ctrl_slot (is_perc_pd works), and
    ghosts are surfaced as dev == '-' rows so the poolable/resolve filters behave.
    tbw_table is accepted for signature symmetry with scan() and ignored.
    """
    bk = _backend_mod.get_backend()
    bm = bk.bay_map() if bk.have_tool() else {}
    disks = bk.enumerate_disks()
    bk.attach_bays(disks, bm=bm)
    ghosts = bk.get_ghost_disks(disks, bm=bm)
    for g in ghosts:
        g.reasons = ["OS_REJECTED",
                     "run [u]dev rescue in watch to recover, or wipe via [a]ssign"]
    disks.extend(ghosts)
    _attach_membership(disks)
    disks.sort(key=_bay_sort_key)
    return disks


def assemble_storage(disks: list[Disk], pools: list[dict],
                     vols: list[dict]) -> list[dict]:
    """Unified storage rows for the summary table — hardware (PERC VDs) first,
    then software (ZFS pools). Each row: kind/name/level/state/size/used/free.

    HW usage comes from lsblk's filesystem columns on the VD's block device
    (mounted → used/free, else '-'); SW usage from `zpool list` (alloc/free).
    """
    from . import blockdev
    from .ui import human_size
    rows: list[dict] = []
    for v in vols or []:
        vd = str(v.get("vd", "?"))
        # ctrl_dev, not dev: a VD member has no device node (dev='-'), and its
        # ctrl_dev is resolved PER VOLUME. Asking members for `dev` used to give
        # every volume the same /dev/sdX, so two VDs measured one filesystem and
        # printed identical used/free (F-136).
        dev = next((d.ctrl_dev for d in disks if d.array_type == "HW"
                    and d.array_name.startswith(f"vd{vd}/") and d.ctrl_dev), None)
        used = free = "-"
        usage = blockdev.vd_usage(dev) if dev else None   # shared VD-usage (F-099)
        if usage:
            u, sz = usage
            used, free = human_size(u), human_size(sz - u)
        rows.append({"kind": "HW", "name": v.get("name") or f"vd{vd}",
                     "level": str(v.get("raid", "?")).lower(),
                     "state": v.get("state", "?"), "size": v.get("size", "-"),
                     "used": used, "free": free})
    for p in pools or []:
        row = {"kind": "SW", "name": p["name"],
               "level": zfs.pool_level(p["name"]), "state": p["health"],
               "size": p["size"], "used": p["alloc"], "free": p["free"]}
        row.update(pool_maint(p["name"]))
        rows.append(row)
    return rows


def pool_maint(name: str) -> dict:
    """Per-pool scrub/trim display strings + raw timestamps for the
    maintenance columns.

    PURE READ (CLAUDE.md §9): last scrub is read LIVE from `zpool status`
    (`zfs.last_scrub_date`), falling back to the maint.jsonl history; last trim
    comes only from maint.jsonl (ZFS exposes no live last-trim date). Never
    writes — reconciling background scrubs into history happens only in watch's
    refresh and the `maint --log` view, never on the read path.

    `last_scrub_ts`/`last_trim_ts` (F-150c) carry the raw ISO-8601 string
    `rel_time()` was given (or None when there is no history) so a machine
    client can sort/compare/age without parsing the human `"6h ago"` prose;
    the human strings stay as-is for the table renderer."""
    from . import maint
    scrub_iso = zfs.last_scrub_date(name)
    if not scrub_iso:
        ev = maint.last_event("scrub", name)
        scrub_iso = ev.get("ts") if ev else None
    tev = maint.last_event("trim", name)
    trim_iso = tev.get("ts") if tev else None
    return {"last_scrub": maint.rel_time(scrub_iso) if scrub_iso else "",
            "last_trim": maint.rel_time(trim_iso) if trim_iso else "",
            "last_scrub_ts": scrub_iso or None,
            "last_trim_ts": trim_iso or None}


def scan_one(dev: str, tbw_table=None, *, serial: str = "") -> Disk:
    """Build a single Disk for one device WITHOUT a full fleet scan.

    Enumerates the inventory once (one lsblk + by-id index — cheap), then reads
    SMART and matches ZFS membership for ONLY the target device. The old version
    ran the whole scan() (SMART on every disk in a thread pool, ghost rescue,
    double bay-attach) and discarded all but one Disk, blocking the watch select
    loop for seconds on each hotplug (F-079). Falls back to a bare Disk(dev) when
    lsblk shows nothing yet (device still OS-rejected / vanished), as before.

    `serial`, when the caller knows it, is the PRIMARY key. Matching on `dev`
    alone is not a lookup on a RAID box: since v0.21.0 every PERC physical drive
    behind a virtual disk reports `dev == '-'` (F-136), so `x.dev == '-'` matches
    the FIRST hidden drive rather than the one asked for — and burn-in's verdict
    is computed from whatever that returned, i.e. a neighbour's SMART (F-148).
    `dev` stays the fallback for the hot-plug path, which has a real /dev/sdX and
    no serial yet; it is skipped for '-'/'' so a missed serial cannot silently
    fall back into the same arbitrary match.
    """
    bk = _backend_mod.get_backend()
    disks = bk.enumerate_disks()
    d = None
    if serial:
        d = next((x for x in disks if x.serial and x.serial == serial), None)
    if d is None and dev not in ("", "-"):
        d = next((x for x in disks if x.dev == dev), None)
    if d is None:
        return Disk(dev=dev)
    bm = bk.bay_map() if bk.have_tool() else {}
    bk.attach_bays([d], bm=bm)
    tbw_table = tbw_table if tbw_table is not None else spec.load()
    smart.read(d, tbw_table)
    _attach_membership([d])
    if d.health != "GHOST":
        assess(d)
    return d
