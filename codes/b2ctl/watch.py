"""b2ctl.watch — interactive disk-management loop.

Shows the health table, then watches for hot-plugged / pulled disks every few
seconds. When a new disk appears it pauses and asks what to do (spare /
replace / wipe / skip). At any time you can type a command:

    r  refresh          a  assign a free disk       o  offload onto a spare
    s  swap onto spare  d  demote a mirror leg       t  toggle dry-run
    n  new pool         e  extend (cache/log/raid)   b  burn-in a disk
    u  udev-rescue      x  destroy a pool            l  locate a bay LED
    q  quit

Built on select() so keystrokes and hotplug events are both handled in one
loop with no extra dependencies.
"""

from __future__ import annotations

import select
import sys
import time

from . import core, hba, zfs, spec, locate, safety, common, blockdev
from . import backend as _backend
from .common import R, Y, G, C, N, run_check
from . import ui

_DRY_RUN: bool = False


def _toggle_dry_run() -> None:
    global _DRY_RUN
    _DRY_RUN = not _DRY_RUN
    common.set_dry_run(_DRY_RUN)      # keep the bottom-layer owner in sync (F-098)
    state = f"{Y}ON{N}" if _DRY_RUN else f"{G}OFF{N}"
    print(f"[DRY-RUN MODE: {state}]")


def _pool_dev(d) -> str:
    """Identifier to use for a device that is ALREADY in a pool."""
    return d.pool_token or d.by_id or d.dev

POLL = 2.0


def _block_devs():
    """Set of (name, serial) identity tuples for hot-plug diffing.

    Keying on serial (not the bare name) catches a pull+insert that reuses the
    same /dev/sdX while watch was blocked in a prompt — name-only diffing sees
    current == baseline and misses it entirely (F-059). Returns None when lsblk
    yields nothing (a transient failure), so the caller skips the diff instead of
    reporting every disk removed then re-detected as NEW."""
    rows = blockdev.lsblk_pairs("NAME,SERIAL,TYPE")     # shared listing (F-099)
    if not rows:
        return None
    devs = set()
    for row in rows:
        name = row.get("NAME", "")
        if row.get("TYPE") == "disk" and not name.startswith(blockdev.EXCLUDE):
            devs.add((name, (row.get("SERIAL") or "").strip()))
    return devs


def _one_based(sel) -> int:
    """Convert a 1-based menu selection to a 0-based index. Raises IndexError for
    0 or negatives so the caller's `except IndexError` rejects them — plain
    `int(sel) - 1` would turn '0' into -1 and silently pick the LAST item in a
    destructive flow (F-052)."""
    i = int(sel)                 # ValueError on non-numeric -> caller catches
    if i < 1:
        raise IndexError(sel)
    return i - 1


def _pick_indices(sel, n: int) -> list:
    """Parse a space-separated 1-based menu selection into deduped 0-based indices
    (order preserved). Rejects 0/negative via `_one_based` (F-052) AND any index
    past the list end, so a stray '0' never wraps to the LAST item in a wipe /
    create flow. One authority for the multi-select parse shared by assign /
    new-pool / extend / burn-in — it had been hand-copied and one copy dropped the
    F-052 guard. Raises ValueError/IndexError so the caller's `except` rejects the
    whole line."""
    out = []
    for x in sel.split():
        i = _one_based(x)        # ValueError (non-numeric) / IndexError (<1)
        if i >= n:
            raise IndexError(x)
        if i not in out:
            out.append(i)
    return out


def _ask(prompt: str) -> str:
    # EOF (Ctrl-D) and Ctrl-C at any prompt return '' (a safe decline) instead
    # of crashing watch with a traceback (F-022).
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _pick_pool() -> str | None:
    pools = zfs.list_pools()
    if not pools:
        print(f"{Y}  no ZFS pools found{N}")
        return None
    if len(pools) == 1:
        return pools[0]["name"]
    for i, p in enumerate(pools, 1):
        print(f"    [{i}] {p['name']} ({p['health']})")
    sel = _ask("  pool #> ")
    try:
        return pools[_one_based(sel)]["name"]
    except (ValueError, IndexError):
        return None


def _confirm(msg: str) -> bool:
    return _ask(f"{Y}  {msg} [y/N]> {N}").lower() in ("y", "yes")


def _confirm_op(op, disk_from, disk_to, pool, vdev, cmds, snap_path=None):
    """Enhanced confirmation box showing op, disk IDs, and exact commands."""
    import textwrap
    width = 52
    border = "─" * width
    print(f"┌─ CONFIRM OPERATION {border[:width-20]}┐")

    def _row(label, val):
        line = f"│ {label:<8} {val}"
        print(line[:width+1].ljust(width + 1) + "│")

    _row("Op:", op)
    if disk_from:
        _row("From:", f"bay {disk_from.bay} │ {disk_from.serial} │ {disk_from.pool or 'AVAILABLE'}")
    if disk_to:
        _row("To:", f"bay {disk_to.bay} │ {disk_to.serial} │ {disk_to.pool or 'AVAILABLE'}")
    _row("Pool:", f"{pool}/{vdev}")
    print(f"│{'':^{width}}│")
    print(f"│ {'Will run:':<{width-1}}│")
    for cmd in cmds:
        joined = " ".join(cmd)
        chunks = textwrap.wrap(joined, width - 4, break_on_hyphens=False) or [joined]
        for chunk in chunks:
            print(f"│   {chunk:<{width-3}}│")
    if snap_path:
        snap_short = snap_path[-44:] if len(snap_path) > 44 else snap_path
        _row("Snap:", snap_short)
    print(f"└{'─'*width}┘")
    return _ask("Proceed? [y/N]: ").lower() in ("y", "yes")


# --------------------------------------------------------------------------- #
# event: a new disk appeared
# --------------------------------------------------------------------------- #
def _handle_new_disk(dev: str, tbw) -> None:
    # Wait for udev to finish enumerating the by-id links rather than a fixed
    # 2 s sleep, which under a slow udev queue aborted with 'no stable by-id'
    # while a plain fast insert wasted 2 s (F-100).
    hba.run(["udevadm", "settle", "--timeout=10"])
    d = core.scan_one(dev, tbw)
    # A busy udev queue (backplane reset after a pull, several disks inserted at
    # once — the Task-B replenish moment) may not have created the by-id symlink
    # yet. Settle + rescan ONCE more before telling the operator to re-insert a
    # perfectly good disk (F-100). Cap the retry at one: scan_one is not free.
    if not d.by_id:
        hba.run(["udevadm", "settle", "--timeout=10"])
        d = core.scan_one(dev, tbw)
    print(f"\n{G}╔══ NEW DISK DETECTED: {dev} ═══════════════════════{N}")
    print(ui.render_new_disk(d))
    print(f"{G}╚════════════════════════════════════════════════════{N}")
    if not d.by_id:
        print(f"{Y}  no stable by-id yet — skipping (re-insert if needed){N}")
        return
    # F-019: a re-seated pool member (or the replacement inserted mid-offload,
    # already resilvered in) is NOT free — never offer the WIPE menu for it, or
    # `sgdisk --zap-all` would destroy an active member's GPT.
    if d.in_pool or d.is_spare:
        print(f"{C}  already {d.vdev_state or 'a member'} in {d.pool}/{d.vdev} — "
              f"no action (this disk is in use, not free).{N}")
        return
    _assign_free_disk(d, tbw)


def _assign_free_disk(d, tbw, all_disks=None) -> None:
    print(f"\n{G}  Disk {d.by_id or d.dev} is free.{N}")
    print("  What do you want to do with it?")
    print("    [1] Prepare for physical removal (Blink LED)")
    print("    [2] Add to a pool as hot SPARE")
    print("    [3] REPLACE a degraded/faulted disk in a pool")
    print("    [4] ATTACH to an existing disk (convert to/expand mirror)")
    print("    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)")
    print("    [6] WIPE it blank (for a new pool)")
    print("    [s] skip / decide later")
    choice = _ask("  action> ")

    if choice == "1":
        print(G + f"  ✔ Blinking {d.bay if locate.is_perc_pd(d) else d.dev}..." + N)
        locate.blink_disk(d, locate.DEFAULT_SECONDS)
    elif choice == "2":
        pool = _pick_pool()
        if pool and _confirm(f"add {ui.disk_label(d)} to '{pool}' as spare?"):
            ok, out = zfs.add_spare(pool, d.by_id or d.dev, dry_run=_DRY_RUN)
            print((G + "  ✔ added as spare" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "3":
        bad = zfs.degraded_leaves()
        if not bad:
            print(f"{Y}  no degraded/faulted devices to replace{N}")
            return
        for i, e in enumerate(bad, 1):
            print(f"    [{i}] {e['pool']}: {e['token']} ({e['state']})")
        sel = _ask("  replace #> ")
        try:
            tgt = bad[_one_based(sel)]
        except (ValueError, IndexError):
            print(f"{Y}  cancelled{N}"); return
        pool = tgt["pool"]
        if pool == "rpool":
            print(f"\n{Y}  ⚠ rpool: after replace completes, run on new disk:{N}")
            print(f"       proxmox-boot-tool format <new-ESP-partition>")
            print(f"       proxmox-boot-tool init   <new-ESP-partition>")
        vdev = tgt.get("vdev", "unknown")
        cmds = [["zpool", "replace", "-f", pool, tgt["token"], d.by_id or d.dev]]
        if not _confirm_op("replace", None, d, pool, vdev, cmds):
            return
        op_id = safety.begin_op("replace", d.serial, d.bay, tgt["token"], pool, vdev, cmds,
                                details={"old_dev": tgt["token"], "new_dev": d.by_id or d.dev},
                                dry_run=_DRY_RUN)
        ok, out = run_check(cmds[0], dry_run=_DRY_RUN)
        if not ok:
            print(R + f"  ✗ failed: {out}" + N)
            safety.end_op(op_id, False, "", out, 1, dry_run=_DRY_RUN)
            return
        print(G + "  ✔ replace started — resilvering" + N)
        ok_resilver = True if _DRY_RUN else _wait_resilver(pool)
        if not ok_resilver:
            print(f"{Y}  resilver did not complete cleanly — NOT detaching the old "
                  f"member. Recover via: zpool status {pool}{N}")
            safety.end_op(op_id, False, out, "resilver incomplete or had errors", 1,
                          dry_run=_DRY_RUN)
            return
        old_token = tgt["token"]
        _detach_if_lingers(pool, old_token)
        avail = zfs.spares(pool)
        if avail:
            print(G + f"  ✔ spare restored to AVAIL: {', '.join(avail)}" + N)
        safety.end_op(op_id, True, out, "", 0, dry_run=_DRY_RUN)
    elif choice == "4":
        pool = _pick_pool()
        if pool:
            in_pool = [x for x in (all_disks if all_disks is not None else core.scan(tbw)) if x.pool == pool]
            if not in_pool:
                print(f"{Y}  no disks found in pool '{pool}'{N}")
                return
            for i, x in enumerate(in_pool, 1):
                print(f"    [{i}] {ui.disk_label(x)} (vdev {x.vdev})")
            sel = _ask("  attach to which #> ")
            try:
                tgt = in_pool[_one_based(sel)]
            except (ValueError, IndexError):
                print(f"{Y}  cancelled{N}"); return
            if _confirm(f"attach {ui.disk_label(d)} to {ui.disk_label(tgt)} in '{pool}'?"):
                ok, out = zfs.attach(pool, tgt.by_id or tgt.dev, d.by_id or d.dev, dry_run=_DRY_RUN)
                print((G + "  ✔ attached" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "5":
        pool = _pick_pool()
        if pool:
            if _confirm(f"Adding a single disk vdev means if this disk fails, the ENTIRE pool is lost. Proceed?"):
                ok, out = run_check(["zpool", "add", "-f", pool, d.by_id or d.dev], dry_run=_DRY_RUN)
                print((G + "  ✔ added" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "6":
        print(f"{R}  WIPE erases ALL data on {d.dev} (SN {d.serial or '?'}){N}")
        if _confirm(f"really wipe {ui.disk_label(d)}?"):
            ok, out = zfs.wipe(d.by_id or d.dev, dry_run=_DRY_RUN)
            print((G + "  ✔ wiped blank" if ok else R + f"  ✗ failed: {out}") + N)
    else:
        print("  skipped")


def _assign_free_disks_batch(disks, tbw) -> None:
    """Batch actions for 2+ free (ZFS-poolable) disks picked in [a]ssign.

    Only actions that are meaningful applied to many disks at once: blink LEDs,
    add all as hot spares to one pool, or wipe all blank. REPLACE / ATTACH /
    ADD-single are inherently 1-to-1 and stay in the single-disk menu
    (_assign_free_disk)."""
    print(f"\n{G}  {len(disks)} free disks selected.{N}")
    print("  Batch action applied to ALL of them:")
    print("    [1] Blink LED on each (prepare for physical removal)")
    print("    [2] Add all to a pool as hot SPARE")
    print("    [3] WIPE all blank")
    print("    [s] skip / decide later")
    choice = _ask("  action> ")

    if choice == "1":
        for d in disks:
            print(G + f"  ✔ Blinking {d.bay if locate.is_perc_pd(d) else d.dev}..." + N)
            locate.blink_disk(d, locate.DEFAULT_SECONDS)
        return
    if choice == "2":
        pool = _pick_pool()
        if not pool:
            print(f"{Y}  cancelled{N}"); return
        for d in disks:
            print(f"    {ui.disk_label(d)}")
        if not _confirm(f"add these {len(disks)} disk(s) to '{pool}' as hot spare?"):
            print(f"{Y}  cancelled{N}"); return
        ok_n = 0
        for d in disks:
            ok, out = zfs.add_spare(pool, d.by_id or d.dev, dry_run=_DRY_RUN)
            print((G + f"  ✔ {ui.disk_label(d)} spare" if ok
                   else R + f"  ✗ {ui.disk_label(d)}: {out}") + N)
            if ok:
                ok_n += 1
        print(f"  {ok_n} ok / {len(disks) - ok_n} failed")
        return
    if choice == "3":
        print(f"{R}  WIPE erases ALL data on these {len(disks)} disks:{N}")
        for d in disks:
            print(f"    {d.dev} (SN {d.serial or '?'})")
        if not _confirm(f"really wipe all {len(disks)} disks?"):
            print(f"{Y}  cancelled{N}"); return
        ok_n = 0
        for d in disks:
            ok, out = zfs.wipe(d.by_id or d.dev, dry_run=_DRY_RUN)
            print((G + f"  ✔ {ui.disk_label(d)} wiped" if ok
                   else R + f"  ✗ {ui.disk_label(d)}: {out}") + N)
            if ok:
                ok_n += 1
        print(f"  {ok_n} ok / {len(disks) - ok_n} failed")
        return
    print("  skipped")


def _wait_for_block_device(serial: str, timeout: int = 20) -> str | None:
    """Poll until a disk with this serial appears, or the deadline passes.

    wipe_sg queues an ASYNCHRONOUS SCSI rescan, so a single `udevadm settle` +
    one lsblk check races the kernel and aborts the ghost-wipe with a misleading
    're-insert or reboot' message even though the block device shows up 1-2 s
    later. Deadline-poll on the monotonic clock instead (F-053)."""
    deadline = time.monotonic() + timeout
    while True:
        hba.run(["udevadm", "settle", "--timeout=1"])
        for row in blockdev.lsblk_pairs("NAME,SERIAL,TYPE"):     # shared listing (F-099)
            if row.get("TYPE") == "disk" and row.get("SERIAL", "").strip() == serial:
                return f"/dev/{row['NAME']}"
        if time.monotonic() >= deadline:
            return None
        print(".", end="", flush=True)
        time.sleep(1)


def _wipe_ghost(d, tbw) -> None:
    print(f"\n{Y}  Disk SN {d.serial} (bay {d.bay or '?'}) was rejected by the OS.{N}")
    print(f"  Likely cause: RAID metadata from a hardware RAID card.")
    print(f"  Solution: zero the RAID signatures via SCSI generic device, then wipe.")

    print(f"\n{C}  Locating SCSI generic device for SN {d.serial}...{N}")
    sg = hba.find_sg_for_ghost(d.serial)
    if not sg:
        print(R + "  ✗ no /dev/sgX found for this disk." + N)
        print(Y + "  Try re-inserting the disk, or check: ls /dev/sg*" + N)
        return
    print(G + f"  ✔ Found {sg}" + N)

    if not _confirm(f"WIPE {sg} (bay {d.bay or '?'}, SN {d.serial})? Erases ALL RAID metadata."):
        print("  cancelled")
        return

    print(f"\n  {C}[1/3]{N} Zeroing 40 MB RAID metadata on {sg} ...")
    ok, msg = zfs.wipe_sg(sg, dry_run=_DRY_RUN)
    if not ok:
        print(R + f"\n  ✗ failed: {msg}" + N)
        return
    print(G + "\n  ✔ zeroed." + N)

    print(f"  {C}[2/3]{N} Waiting for OS to recognize disk", end="", flush=True)
    sdx = _wait_for_block_device(d.serial, timeout=20)
    if not sdx:
        print(f"\n{Y}  Disk didn't appear within 20 s. Try re-inserting or rebooting.{N}")
        return
    print(G + f"\n  ✔ appeared as {sdx}" + N)

    print(f"  {C}[3/3]{N} Running full wipe on {sdx} ...")
    ok2, out2 = zfs.wipe(sdx, dry_run=_DRY_RUN)
    if ok2:
        print(G + f"  ✔ done — {sdx} is clean" + N)
    else:
        print(R + f"  ✗ wipe failed: {out2}" + N)
        return

    # The wipe re-fired udev change events that momentarily drop/recreate the
    # by-id links. Wait for them before offering pool actions — never add a disk
    # under an unstable /dev/sdX (§9), mirroring _handle_new_disk's guard (F-054).
    hba.run(["udevadm", "settle", "--timeout=10"])
    d2 = core.scan_one(sdx, tbw)
    if not d2.by_id:
        print(f"{Y}  {sdx} has no stable by-id link yet — re-run [a]ssign once "
              f"udev settles before adding it to a pool.{N}")
        return
    _assign_free_disk(d2, tbw)


def _cmd_assign(tbw) -> None:
    from . import raid_actions
    disks = core.scan(tbw)
    # ZFS-assignable = Disk.is_poolable (F-103): not in a pool, owns a real block
    # device, and NOT a hidden PERC drive sharing the VD's /dev/sda. One authority
    # for the invariant, so no site can forget the smart_dtype guard.
    zfs_avail = [d for d in disks if d.is_poolable]
    ghosts = [d for d in disks if d.health == "GHOST"]
    # Hidden Unconfigured-Good PERC drives — actionable via the hardware-RAID menu
    # (set JBOD for ZFS, create a volume, or add as a hot spare).
    raid_avail = [d for d in disks if d.smart_dtype and d.array_type != "HW"
                  and d.pd_state.upper() in ("UGOOD", "READY", "UGUNSP")]
    # Tag each candidate with its category so a multi-select classifies picks by
    # tag, not dataclass __eq__ (two disks with equal fields must not collide).
    # The three lists are disjoint (is_poolable excludes smart_dtype/ghosts), so
    # every pick lands in exactly one category. Display order is unchanged.
    tagged = ([(d, "zfs") for d in zfs_avail]
              + [(d, "ghost") for d in ghosts]
              + [(d, "perc") for d in raid_avail])
    if not tagged:
        print(f"{Y}  no unassigned disks available to assign{N}")
        return
    for i, (d, cat) in enumerate(tagged, 1):
        if cat == "ghost":
            print(f"    [{i}] {R}[GHOST]{N} bay {d.bay or '?'} (SN {d.serial or '?'}) — needs wipe")
        elif cat == "perc":
            print(f"    [{i}] bay {d.bay or '?'} ({d.model}, SN {d.serial or '?'}) "
                  f"{C}(PERC Unconfigured-Good){N}")
        else:
            print(f"    [{i}] bay {d.bay or '?'} {d.dev} ({d.model}, SN {d.serial or '?'})")
    sel = _ask("  assign which #> (space-separated for batch) ")
    # Parse one or more 1-based indices (shared _pick_indices: reject <1, dedupe).
    try:
        picks = [tagged[i] for i in _pick_indices(sel, len(tagged))]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled or invalid selection{N}"); return
    if not picks:
        print(f"{Y}  cancelled{N}"); return

    if len(picks) == 1:                          # unchanged single-disk routing
        d, cat = picks[0]
        if cat == "perc":
            raid_actions.assign_perc(d, raid_avail)
        elif cat == "ghost":
            _wipe_ghost(d, tbw)
        else:
            _assign_free_disk(d, tbw, all_disks=disks)
        return

    # 2+ picks → batch. A batch must be homogeneous: each category has a different
    # action set (and the PERC path is RAID-mode-gated). Mixing → refuse with counts.
    cats = {cat for _, cat in picks}
    if len(cats) > 1:
        counts = ", ".join(f"{sum(1 for _, c in picks if c == k)} {label}"
                           for k, label in (("perc", "PERC"), ("zfs", "free"), ("ghost", "ghost"))
                           if any(c == k for _, c in picks))
        print(f"{Y}  mixed disk types selected ({counts}) — batch actions differ "
              f"per type. Select one type at a time.{N}")
        return
    chosen = [d for d, _ in picks]
    cat = cats.pop()
    if cat == "perc":
        raid_actions.assign_perc_batch(chosen, raid_avail)
    elif cat == "ghost":
        for g in chosen:
            _wipe_ghost(g, tbw)
    else:
        _assign_free_disks_batch(chosen, tbw)


# --------------------------------------------------------------------------- #
# event: a disk was pulled
# --------------------------------------------------------------------------- #
def _handle_removed(devs: set) -> None:
    for dev in sorted(devs):
        print(f"\n{Y}■ disk removed: /dev/{dev}{N}")
    print(f"{C}  current pool health:{N}")
    print(ui.render_pools(zfs.list_pools()))


# --------------------------------------------------------------------------- #
# typed commands
# --------------------------------------------------------------------------- #
def _cmd_refresh(tbw) -> None:
    disks = core.scan(tbw)
    pools = zfs.list_pools()
    print("\n" + ui.render_table(disks))
    vols = _backend.get_backend().raid_volumes()
    print(ui.render_storage(core.assemble_storage(disks, pools, vols)))
    print(ui.render_details(disks, pools))





def _cmd_offload(tbw) -> bool:
    """Return True only when the offload actually mutated the pool (F-070 — feeds
    the CLI exit code). Cancels / guard-refusals / failures return False."""
    disks = core.scan(tbw)
    in_pool = [d for d in disks if d.in_pool and d.pool]
    if not in_pool:
        print(f"{Y}  no in-pool disks to offload{N}")
        return False
    for i, d in enumerate(in_pool, 1):
        print(f"    [{i}] bay {d.bay or '?'} {d.dev} in {d.pool} (vdev {d.vdev})")
    sel = _ask("  offload which #> ")
    try:
        d = in_pool[_one_based(sel)]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return False

    if d.vdev == "spares":
        if _confirm(f"This disk is a hot spare. Remove {ui.disk_label(d)} from '{d.pool}'?"):
            ok, out = run_check(["zpool", "remove", d.pool, _pool_dev(d)], dry_run=_DRY_RUN)
            if not ok:
                print(R + f"  ✗ failed: {out}" + N)
                return False
            print(G + "  ✔ removed from pool" + N)
            _assign_free_disk(d, tbw)
            return True
        return False

    # One topology snapshot shared by both read-only guards in this flow (F-107).
    # The mutating path (_offline_and_replace) takes its own fresh snapshot
    # immediately before offlining, so the §9 safety semantics are preserved.
    topo = zfs.topology()
    detach_state = zfs.detach_safety(d.pool, _pool_dev(d), topo)
    if detach_state == "last_redundancy":
        print(f"{R}  ⚠ detaching {ui.disk_label(d)} removes the LAST redundancy of "
              f"'{d.pool}' — it becomes a single-disk vdev.{N}")
        if d.pool == "rpool":
            print(f"{Y}    rpool is the boot pool; a later disk failure makes the node "
                  f"unbootable. Prefer [r]eplace over offload here.{N}")
        if _ask(f"  type the pool name '{d.pool}' to detach anyway> ") == d.pool:
            ok, out = zfs.detach(d.pool, _pool_dev(d), dry_run=_DRY_RUN)
            print((G + "  ✔ detached" if ok else R + f"  ✗ failed: {out}") + N)
            if ok:
                _assign_free_disk(d, tbw)
            return ok
        print(f"{Y}  cancelled{N}")
        return False
    if detach_state == "ok":
        if _confirm(f"This disk is in a mirror. Detach {ui.disk_label(d)} instantly?"):
            ok, out = zfs.detach(d.pool, _pool_dev(d), dry_run=_DRY_RUN)
            if not ok:
                print(R + f"  ✗ failed: {out}" + N)
                return False
            print(G + "  ✔ detached" + N)
            _assign_free_disk(d, tbw)
            return True
        return False

    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if spares:
        if _replace_onto_spare(d, spares[0]):
            _assign_free_disk(d, tbw)
            return True
        return False
    # No spare: offline (degrade) + replace a new disk in the same bay, but only
    # if the vdev is redundant enough that offlining won't fail the pool.
    if zfs.can_offline(d.pool, _pool_dev(d), topo):
        _offline_and_replace(d, tbw)
        return True
    print(f"{Y}  no AVAIL spare, and offlining {ui.disk_label(d)} would risk "
          f"failing '{d.pool}' — add a spare or fix redundancy first{N}")
    return False


def _cmd_locate(tbw) -> None:
    disks = core.scan_light(tbw)     # locate needs identity + topology only (F-102)
    target = _ask("  locate which (bay/serial/sdX)> ")
    if not target:
        return
    chosen = None
    for d in disks:
        if target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", "")):
            chosen = d
            break
    if chosen is None:
        print(f"{Y}  could not resolve '{target}'{N}")
        return
    if chosen.dev == "-":
        print(f"{R}  cannot locate a GHOST disk (OS rejected it, no /dev node){N}")
        return
    where = f"bay {chosen.bay}" if locate.is_perc_pd(chosen) else chosen.dev
    print(f"{Y}  blinking {where} for {locate.DEFAULT_SECONDS}s ...{N}")
    ok, method = locate.blink_disk(chosen)   # refuses a resilvering/rebuilding disk (F-006/F-020)
    if method == "resilvering":
        print(f"{R}  refuse: {ui.disk_label(chosen)} is resilvering/rebuilding — "
              f"never pull a disk mid-resilver (CLAUDE.md §9){N}")
    else:
        print((G + f"  ✔ done (via {method})" if ok else R + "  ✗ failed") + N)



def _wait_resilver(pool: str) -> bool:
    """Poll until a resilver finishes. Returns True ONLY on clean completion.

    Returns False on completed-with-errors, on Ctrl-C (the resilver keeps running
    in the background — the caller must NOT detach/pull), or if `zpool status`
    is unreadable for several polls in a row (never spins forever at 0%).
    """
    fails = 0
    try:
        while True:
            time.sleep(2)
            st = zfs.poll_resilver_status(pool)
            if not st.get("ok", True):
                fails += 1
                if fails >= 5:
                    sys.stdout.write(
                        f"\r{R}  ✗ can't read `zpool status {pool}` — stopped watching; "
                        f"check it manually{N}\n")
                    return False
                continue
            fails = 0
            if st["completed"]:
                if st.get("has_errors"):
                    sys.stdout.write(
                        f"\r{R}  ✗ resilver completed WITH ERRORS — run: zpool status {pool}{N}\n")
                    return False
                sys.stdout.write(f"\r{G}  ✔ resilver completed{N}                    \n")
                return True
            sys.stdout.write(
                f"\r{Y}  resilvering... {st['done']}% done, ETA {st['eta'] or '?'}{N}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        sys.stdout.write(
            f"\n{Y}  stopped watching — resilver continues in the background; "
            f"check: zpool status {pool}{N}\n")
        return False


def _detach_if_lingers(pool: str, old_token: str) -> None:
    topo = zfs.topology()
    if any(e["pool"] == pool and e["token"] == old_token for e in topo.values()):
        ok_d, out_d = zfs.detach(pool, old_token, dry_run=_DRY_RUN)
        print((G + f"  ✔ detached {old_token}" if ok_d
               else R + f"  ✗ detach failed: {out_d}") + N)


def _replace_member(d, new, *, detach_old=False, pull_led=False) -> bool:
    """Replace pool member `d` with disk `new` (its pool_token/by_id), then
    resilver. Shared by spare-replace and spare-less in-place replace.
    """
    pool = d.pool
    new_dev = getattr(new, "pool_token", None) or new.by_id or new.dev
    cmds = [["zpool", "replace", "-f", pool, _pool_dev(d), new_dev]]
    if not _confirm_op("replace", d, new, pool, d.vdev, cmds):
        return False
    op_id = safety.begin_op("replace", d.serial, d.bay, _pool_dev(d), pool, d.vdev, cmds,
                            details={"old_dev": _pool_dev(d), "new_dev": new_dev},
                            dry_run=_DRY_RUN)
    ok, out = run_check(cmds[0], dry_run=_DRY_RUN)
    if not ok:
        print(R + f"  ✗ failed: {out}" + N)
        safety.end_op(op_id, False, "", out, 1, dry_run=_DRY_RUN)
        return False
    print(G + "  ✔ replace started — resilvering" + N)
    ok_resilver = True if _DRY_RUN else _wait_resilver(pool)
    if not ok_resilver:
        # CLAUDE.md §9: never auto-detach / never light an LED after a resilver
        # that errored or is still running — the old disk may hold the only copy
        # of unreconstructed blocks. Leave the replacing vdev intact.
        print(f"{Y}  resilver did not complete cleanly — NOT detaching {ui.disk_label(d)} "
              f"or lighting its LED. Recover via: zpool status {pool}{N}")
        safety.end_op(op_id, False, out, "resilver incomplete or had errors", 1,
                      dry_run=_DRY_RUN)
        return False
    if detach_old:
        _detach_if_lingers(pool, _pool_dev(d))
        if pull_led and not _DRY_RUN:
            print(f"{Y}  please pull bay {d.bay or '?'} ... blinking LED{N}")
            locate.blink_disk(d, locate.DEFAULT_SECONDS, force=True)
    safety.end_op(op_id, True, out, "", 0, dry_run=_DRY_RUN)
    return True


def _replace_onto_spare(d, spare) -> bool:
    return _replace_member(d, spare, detach_old=True, pull_led=True)


def _offline_and_replace(d, tbw) -> None:
    """Spare-less offload: offline a member (pool -> DEGRADED), then replace it
    with a new disk inserted in the SAME bay. Guarded so it can't fail the pool.
    """
    pool = d.pool
    if not zfs.can_offline(pool, _pool_dev(d)):
        print(f"{R}  refuse: '{pool}' is not fully redundant right now — offlining "
              f"{ui.disk_label(d)} could fail the pool. Fix the other disk first.{N}")
        return
    print(f"{Y}  '{pool}' will go DEGRADED (online, NO redundancy) until the new "
          f"disk finishes resilvering.{N}")
    cmds = [["zpool", "offline", pool, _pool_dev(d)]]
    if not _confirm_op("offline", d, None, pool, d.vdev, cmds):
        return
    op_id = safety.begin_op("offline", d.serial, d.bay, _pool_dev(d), pool, d.vdev, cmds, dry_run=_DRY_RUN)
    ok, out = zfs.offline(pool, _pool_dev(d), dry_run=_DRY_RUN)
    safety.end_op(op_id, ok, "", "" if ok else out, 0 if ok else 1, dry_run=_DRY_RUN)
    if not ok:
        print(R + f"  ✗ offline failed: {out}" + N)
        return
    print(G + f"  ✔ {ui.disk_label(d)} offlined — pool DEGRADED" + N)

    def _free(scan):
        return [x for x in scan if x.is_poolable and x.serial != d.serial]

    # Snapshot the free-disk serials BEFORE the replacement goes in, so the new
    # disk is identified by a newly-appeared serial — not by bay equality, which
    # matches ANY free disk when d.bay is None (unmapped/no sas2ircu) and would
    # replace an arbitrary pre-existing scratch disk into the pool (F-056).
    before = {x.serial for x in _free(core.scan(tbw)) if x.serial}
    if not _DRY_RUN:
        print(f"{Y}  pull bay {d.bay or '?'} and insert the replacement into the SAME bay.{N}")
        locate.blink_disk(d, locate.DEFAULT_SECONDS)
    _ask("  press Enter once the new disk is inserted> ")
    after = _free(core.scan(tbw))
    new = None
    if d.bay is not None:
        new = next((x for x in after if x.bay == d.bay), None)
    if new is None:                             # fall back to serial-identity
        new = next((x for x in after if x.serial and x.serial not in before), None)
    if not new:
        print(f"{Y}  couldn't auto-detect the new disk. Leave watch running (it "
              f"auto-detects an insert), or use [a]ssign option 3. The member "
              f"stays OFFLINE meanwhile.{N}")
        return
    _replace_member(d, new)


def _cmd_replace(tbw) -> bool:
    """Return True only on a completed replace (F-070 — feeds the CLI exit code)."""
    disks = core.scan(tbw)
    # you replace an active member onto a spare — a spare is not itself a
    # replace target, so exclude spares from the candidate list.
    in_pool = [d for d in disks if d.in_pool and d.pool and not d.is_spare]
    if not in_pool:
        print(f"{Y}  no in-pool disks to replace{N}"); return False
    for i, d in enumerate(in_pool, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  replace which #> ")
    try:
        d = in_pool[_one_based(sel)]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return False

    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if not spares:
        print(f"{Y}  pool '{d.pool}' has no AVAIL spare — add one first{N}")
        return False

    return _replace_onto_spare(d, spares[0])


def _cmd_create(tbw, raid_type=None) -> bool:
    """Return True only on a created pool (F-070 — feeds the CLI exit code)."""
    # Disk.is_poolable excludes HIDDEN PERC drives (megaraid passthrough → shared
    # /dev/sda) and ghosts; a JBOD'd drive owns its own /dev/sdX and is poolable (F-103).
    available = [d for d in core.scan(tbw) if d.is_poolable]
    if not available:
        print(f"{Y}  no available disks to create pool{N}")
        return False
    for i, d in enumerate(available, 1):
        print(f"    [{i}] {d.dev} (bay {d.bay or '?'})")
    sel = _ask("  pick disks (space-separated #)> ")
    try:
        indices = _pick_indices(sel, len(available))
        devs = [available[i].by_id or available[i].dev for i in indices]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled or invalid selection{N}")
        return False
    if not devs:
        return False
    name = _ask("  pool name> ")
    if not name:
        return False
    if raid_type is None:
        raid_type = _ask("  raid type (stripe, mirror, raid10, raidz1, raidz2) "
                         "[mirror]> ") or "mirror"
    if raid_type not in ("stripe", "mirror", "raid10", "raidz1", "raidz2"):
        print(f"{R}  invalid raid type{N}")
        return False
    min_disks = zfs.MIN_DISKS.get(raid_type, 1)
    if len(devs) < min_disks:
        print(f"{R}  error: need at least {min_disks} disks for {raid_type}{N}")
        return False
    if raid_type == "raid10":
        if len(devs) % 2:
            print(f"{R}  error: raid10 needs an even number of disks{N}")
            return False
        print(f"{C}  mirror pairs:{N}")
        for i in range(0, len(devs), 2):
            print(f"    mirror {available[indices[i]].dev} + {available[indices[i + 1]].dev}")

    # Pool properties — Enter accepts the recommended SSD default.
    print(f"{C}  pool properties (press Enter for the recommended SSD default):{N}")
    _HINTS = {"recordsize": "128K general | DB 16K (PG 8K) | media 1M | "
                            "VM 64-128K; per-dataset, changeable later"}
    pool_opts = dict(zfs.DEFAULT_POOL_OPTS)
    fs_opts = dict(zfs.DEFAULT_FS_OPTS)
    # ashift (generic), then autotrim as an explicit choice.
    pool_opts["ashift"] = _ask(f"    ashift [{pool_opts['ashift']}]> ") or pool_opts["ashift"]
    print("    autotrim: [1] off — scrub + trim monthly systemd timers (recommended)")
    print("              [2] on  — continuous (ZFS handles trim); scrub-monthly "
          "timer only")
    autotrim_on = (_ask("    choose [1]> ") or "1") == "2"
    pool_opts["autotrim"] = "on" if autotrim_on else "off"
    for k in fs_opts:
        if k in _HINTS:
            print(f"      ({_HINTS[k]})")
        fs_opts[k] = _ask(f"    {k} [{fs_opts[k]}]> ") or fs_opts[k]

    dirty = [available[i] for i in indices if zfs.has_zfs_label(available[i].by_id or available[i].dev)]
    if dirty:
        print(f"{Y}  WARNING: The following disks already contain data/labels:{N}")
        for disk in dirty:
            print(f"    - {ui.disk_label(disk)}")
        if not _confirm("these disks already contain data/labels — wipe and continue?"):
            return False
        for disk in dirty:
            zfs.wipe(disk.by_id or disk.dev, dry_run=_DRY_RUN)

    props = " ".join(f"{k}={v}" for k, v in {**pool_opts, **fs_opts}.items())
    print(f"{C}  -> {props}{N}")
    if not _confirm(f"create pool '{name}' ({raid_type}) with {len(devs)} disks?"):
        return False
    ok, out = zfs.create_pool(name, raid_type, devs, pool_opts=pool_opts,
                              fs_opts=fs_opts, dry_run=_DRY_RUN)
    print((G + "  ✔ pool created" if ok else R + f"  ✗ failed: {out}") + N)
    if ok:
        # Scrub is independent of autotrim — it's the only thing that verifies
        # checksums / self-heals, so the scrub timer always enables. The trim timer
        # enables only when autotrim is off (autotrim=on already trims continuously).
        include_trim = not autotrim_on
        okc, outc = zfs.install_pool_timers(name, include_trim=include_trim, dry_run=_DRY_RUN)
        print((G + f"  ✔ maintenance timers: {outc}" if okc
               else Y + f"  [!] scrub timer NOT scheduled: {outc}") + N)
    return ok


def _cmd_destroy(tbw, target=None) -> bool:
    """Return True only on a destroyed pool (F-070 — feeds the CLI exit code)."""
    pools = zfs.list_pools()
    if not pools:
        print(f"{Y}  no ZFS pools to destroy{N}")
        return False
    pool = target
    if pool is None:
        for i, p in enumerate(pools, 1):
            print(f"    [{i}] {p['name']} ({p['size']}, {p['health']})")
        sel = _ask("  destroy which #> ")
        try:
            pool = pools[_one_based(sel)]["name"]
        except (ValueError, IndexError):
            print(f"{Y}  cancelled{N}"); return False
    elif pool not in [p["name"] for p in pools]:
        print(f"{R}  no such pool '{pool}'{N}"); return False

    members = [d for d in core.scan(tbw) if d.pool == pool]
    if members:
        print(f"{C}  members:{N}")
        for d in members:
            print(f"    - {ui.disk_label(d)}")
    print(f"{R}  [!] destroying '{pool}' ERASES ALL DATA on it. This cannot be undone.{N}")
    if not _confirm(f"destroy pool '{pool}'?"):
        print("  cancelled"); return False
    if _ask(f"  type the pool name '{pool}' to confirm> ") != pool:
        print(f"{Y}  name did not match — cancelled{N}"); return False

    op_id = safety.begin_op("destroy", "", "", "", pool, pool,
                            [["zpool", "destroy", pool]], dry_run=_DRY_RUN)
    ok, out = zfs.destroy_pool(pool, dry_run=_DRY_RUN)
    if ok:
        okc, outc = zfs.remove_pool_timers(pool, dry_run=_DRY_RUN)
        print((G + f"  ✔ pool '{pool}' destroyed; timers disabled" if okc
               else G + f"  ✔ pool '{pool}' destroyed" + Y + f" (timers: {outc})") + N)
    else:
        print(f"{R}  ✗ failed: {out}{N}")
    safety.end_op(op_id, ok, out, "" if ok else out, 0 if ok else 1, dry_run=_DRY_RUN)
    return ok


def _cmd_swap(tbw) -> bool:
    """Return True only on a completed swap (F-070 — feeds the CLI exit code)."""
    disks = core.scan(tbw)
    # swap moves an ACTIVE pool member onto a spare — a spare itself is not a
    # valid swap source, so exclude spares from the candidate list.
    candidates = [d for d in disks if d.in_pool and d.pool and not d.is_spare]
    if not candidates:
        print(f"{Y}  no in-pool disks to swap{N}")
        return False
    for i, d in enumerate(candidates, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  swap which #> ")
    try:
        d = candidates[_one_based(sel)]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return False

    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if not spares:
        print(f"{Y}  no AVAIL spare in pool '{d.pool}'{N}")
        return False

    # Swap runs three mutating commands; audit them like every other lifecycle
    # action (begin_op/end_op + a command preview) instead of a bare one-line
    # confirm, so `b2ctl log`/`rollback` can see and reverse it (F-057).
    spare = spares[0]
    spare_tok = spare.pool_token or spare.by_id
    readd_tok = d.by_id or d.dev
    cmds = [["zpool", "replace", d.pool, _pool_dev(d), spare_tok],
            ["zpool", "detach", d.pool, _pool_dev(d)],
            ["zpool", "add", "-f", d.pool, "spare", readd_tok]]
    if not _confirm_op("swap", d, spare, d.pool, d.vdev, cmds):
        print("  cancelled"); return False
    op_id = safety.begin_op("swap", d.serial, d.bay, _pool_dev(d), d.pool, d.vdev,
                            cmds, dry_run=_DRY_RUN)
    ok, out = zfs.swap_to_spare(d.pool, _pool_dev(d), spare_tok, dry_run=_DRY_RUN)
    if not ok:
        safety.end_op(op_id, False, "", out, 1, dry_run=_DRY_RUN)
        print(R + f"  ✗ failed: {out}" + N)
        return False
    print(G + "  ✔ swap started — resilvering onto spare" + N)
    ok_resilver = True if _DRY_RUN else _wait_resilver(d.pool)
    if not ok_resilver:
        # Do NOT detach or re-add the old disk as a spare — it may still be a
        # member of the replacing/spare vdev holding unreconstructed blocks.
        print(f"{Y}  resilver did not complete cleanly — NOT detaching or re-adding "
              f"{ui.disk_label(d)}. Recover via: zpool status {d.pool}{N}")
        safety.end_op(op_id, False, out, "resilver incomplete or had errors", 1,
                      dry_run=_DRY_RUN)
        return False
    _detach_if_lingers(d.pool, _pool_dev(d))
    safety.end_op(op_id, True, out, "", 0, dry_run=_DRY_RUN)

    # Re-adding the freed disk as a spare is a distinct mutation — confirm it on
    # its own so a decline leaves the disk detached rather than silently added.
    # The swap itself already succeeded, so the command's exit status is True
    # regardless of whether the operator opts to re-add the freed disk.
    if not _confirm(f"re-add {ui.disk_label(d)} as a hot spare in '{d.pool}'?"):
        print(f"{Y}  left {ui.disk_label(d)} detached — add later with [a]ssign or: "
              f"zpool add -f {d.pool} spare {readd_tok}{N}")
        return True
    ok_s, out_s = zfs.add_spare(d.pool, readd_tok, dry_run=_DRY_RUN)
    if ok_s:
        print(G + f"  ✔ {ui.disk_label(d)} is now a hot spare in '{d.pool}'" + N)
    else:
        print(R + f"  ✗ failed to re-add as spare: {out_s}" + N)
    return True


def _cmd_demote(tbw) -> bool:
    """Return True only on a completed demote (F-070 — feeds the CLI exit code)."""
    disks = core.scan(tbw)
    mirror_members = [d for d in disks if d.in_pool and d.vdev and "mirror" in d.vdev]
    if not mirror_members:
        print(f"{Y}  no mirror members available to demote{N}")
        return False
    for i, d in enumerate(mirror_members, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  demote which #> ")
    try:
        d = mirror_members[_one_based(sel)]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return False

    state = zfs.detach_safety(d.pool, _pool_dev(d))
    if state == "refuse":
        print(f"{Y}  refuse: not a detachable mirror leg / no ONLINE sibling remains{N}")
        return False
    if state == "last_redundancy":
        print(f"{R}  ⚠ this removes the LAST redundancy of '{d.pool}' — it becomes a "
              f"single-disk vdev with no mirror.{N}")
        if d.pool == "rpool":
            print(f"{Y}    rpool is the boot pool; a later boot-disk failure would make "
                  f"the node unbootable and unrecoverable.{N}")
        if _ask(f"  type the pool name '{d.pool}' to demote anyway> ") != d.pool:
            print(f"{Y}  cancelled{N}"); return False
    elif not _confirm(f"demote {ui.disk_label(d)} in '{d.pool}' to a hot spare?"):
        return False
    ok, out = zfs.demote_to_spare(d.pool, _pool_dev(d), dry_run=_DRY_RUN)
    if ok:
        print(G + "  ✔ demoted to spare" + N)
    else:
        print(R + f"  ✗ failed: {out}" + N)
    return ok


def _avail_for_aux(tbw):
    """Poolable free disks (JBOD'd / raw, not already in a pool)."""
    return [d for d in core.scan(tbw) if d.is_poolable]      # F-103


def _cmd_extend(tbw) -> None:
    """Add an L2ARC cache or SLOG log vdev to an existing pool, or remove one."""
    # F-021: zfs.list_pools() returns list-of-dicts — reuse _pick_pool(), which
    # returns the pool NAME string, instead of comparing typed input against dicts.
    pool = _pick_pool()
    if not pool:
        print(f"{Y}  cancelled{N}"); return
    print("  [1] add L2ARC cache (read cache; loss = harmless)")
    print("  [2] add SLOG log   (sync-write accel; mirror + PLP recommended)")
    print("  [3] remove a cache/log device")
    print("  [4] replace/repair a degraded cache/log device")
    choice = _ask("  action> ")

    if choice in ("1", "2"):
        avail = _avail_for_aux(tbw)
        if not avail:
            print(f"{Y}  no free disks available{N}"); return
        for i, d in enumerate(avail, 1):
            print(f"    [{i}] {d.dev} (bay {d.bay or '?'})")
        sel = _ask("  pick disk(s) (space-separated #)> ")
        try:
            devs = [avail[i].by_id or avail[i].dev
                    for i in _pick_indices(sel, len(avail))]
        except (ValueError, IndexError):
            print(f"{Y}  cancelled or invalid selection{N}"); return
        if not devs:
            return
        if choice == "1":
            if _confirm(f"add {len(devs)} L2ARC cache device(s) to '{pool}'?"):
                ok, out = zfs.add_cache(pool, devs, dry_run=_DRY_RUN)
                print((G + "  ✔ cache added" if ok else R + f"  ✗ failed: {out}") + N)
        else:
            if len(devs) == 1:
                print(f"{Y}  [!] SLOG not mirrored: losing this log device can lose "
                      f"in-flight sync writes.{N}")
                if not _confirm("add a NON-mirrored SLOG anyway?"):
                    return
            print(f"{Y}  [!] ensure the SSD(s) have Power-Loss Protection (PLP).{N}")
            if _confirm(f"add SLOG ({'mirror' if len(devs) > 1 else 'single'}) to '{pool}'?"):
                ok, out = zfs.add_log(pool, devs, dry_run=_DRY_RUN)
                print((G + "  ✔ SLOG added" if ok else R + f"  ✗ failed: {out}") + N)
        return

    if choice == "3":
        topo = zfs.topology()
        # Classify aux by TOP vdev so a mirrored SLOG (leaves vdev='mirror-1',
        # top='logs') is visible; remove a mirrored aux by its mirror vdev name,
        # a single one by its device token (F-060).
        units = {}
        for e in topo.values():
            if e["pool"] != pool:
                continue
            top = e.get("top_vdev", e["vdev"])
            if "cache" in top or "log" in top:
                vdev = e["vdev"]
                units[vdev if vdev.startswith("mirror") else e["token"]] = None
        aux = sorted(units)
        if not aux:
            print(f"{Y}  no cache/log devices on '{pool}'{N}"); return
        for i, t in enumerate(aux, 1):
            print(f"    [{i}] {t}")
        try:
            tok = aux[_one_based(_ask("  remove which #> "))]
        except (ValueError, IndexError):
            print(f"{Y}  cancelled{N}"); return
        if _confirm(f"remove '{tok}' from '{pool}'?"):
            ok, out = zfs.remove_vdev(pool, tok, dry_run=_DRY_RUN)
            print((G + "  ✔ removed" if ok else R + f"  ✗ failed: {out}") + N)
        return

    if choice == "4":
        _repair_aux_interactive(tbw, pool)
        return
    print(f"{Y}  cancelled{N}")


def _repair_aux_interactive(tbw, pool: str) -> None:
    """Pick a degraded cache/log leaf + a free disk, then _repair_aux()."""
    bad = [l for l in zfs.aux_leaves(pool) if l["degraded"]]
    if not bad:
        print(f"{Y}  no degraded cache/log device on '{pool}'{N}"); return
    for i, l in enumerate(bad, 1):
        kind = "SLOG mirror-leg" if l["mirror_leg"] else l["klass"]
        print(f"    [{i}] {kind:14} {l['token']}  {R}{l['state']}{N}")
    try:
        leaf = bad[_one_based(_ask("  repair which #> "))]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return
    avail = _avail_for_aux(tbw)
    if not avail:
        print(f"{Y}  no free disks available{N}"); return
    for i, d in enumerate(avail, 1):
        print(f"    [{i}] {d.dev} (bay {d.bay or '?'})")
    try:
        new = avail[_one_based(_ask("  replacement disk #> "))]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return
    _repair_aux(pool, leaf, new)


def _repair_aux(pool: str, leaf: dict, new=None, *, new_token: str | None = None) -> bool:
    """Repair a degraded aux (cache/log) leaf with a fresh disk. Branch by class:

      cache             -> zpool remove <old> ; zpool add cache <new>  (no resilver)
      SLOG mirror-leg   -> zpool replace <old> <new>                   (brief resilver)
      SLOG single, gone -> zpool remove <old> ; zpool add log <new>    (REMOVED/UNAVAIL)
      SLOG single, here -> zpool replace <old> <new>

    L2ARC can't be `zpool replace`d, so cache is always remove+add. `replace` is
    chosen over attach+detach for a mirror leg — it never exposes a hand-picked
    destroy target, so a mistyped detach can't kill the surviving leg (safer).

    `new` is an optional Disk (interactive path); `new_token` a resolved by-id
    string (CLI path). At least one must supply a device.
    """
    new_token = new_token or ((new.by_id or new.dev) if new else None)
    if not new_token:
        print(f"{R}  ✗ no replacement device{N}"); return False
    old, klass = leaf["token"], leaf["klass"]

    if klass == "cache":
        cmds = [["zpool", "remove", pool, old],
                ["zpool", "add", "-f", pool, "cache", new_token]]
    elif leaf.get("mirror_leg"):
        cmds = [["zpool", "replace", "-f", pool, old, new_token]]
    elif leaf["state"] in ("REMOVED", "UNAVAIL"):
        # single SLOG fully gone: `replace` of a vanished device is unreliable.
        cmds = [["zpool", "remove", pool, old],
                ["zpool", "add", "-f", pool, "log", new_token]]
    else:
        cmds = [["zpool", "replace", "-f", pool, old, new_token]]
    resilver = any(c[1] == "replace" for c in cmds)

    verb = "replace" if resilver else "remove+add"
    if not _confirm(f"repair {klass} on '{pool}': {verb} {old} -> {new_token}?"):
        print(f"{Y}  cancelled{N}"); return False

    op_id = safety.begin_op(
        "aux-repair", getattr(new, "serial", "") or "", getattr(new, "bay", "") or "",
        new_token, pool, leaf["vdev"], cmds,
        details={"old_dev": old, "new_dev": new_token}, dry_run=_DRY_RUN)
    out = ""
    for c in cmds:
        ok, out = run_check(c, dry_run=_DRY_RUN)
        if not ok:
            print(R + f"  ✗ failed: {out}" + N)
            safety.end_op(op_id, False, "", out, 1, dry_run=_DRY_RUN)
            return False
    if resilver:
        print(G + "  ✔ replace started — resilvering" + N)
        ok_res = True if _DRY_RUN else _wait_resilver(pool)
        if not ok_res:
            print(f"{Y}  resilver did not complete cleanly — check: zpool status {pool}{N}")
            safety.end_op(op_id, False, out, "resilver incomplete or had errors", 1,
                          dry_run=_DRY_RUN)
            return False
    else:
        print(G + f"  ✔ {klass} repaired" + N)
    safety.end_op(op_id, True, out, "", 0, dry_run=_DRY_RUN)
    return True


def _cmd_burnin(tbw) -> None:
    """Vet free disk(s) with SMART long self-tests (+ optional surface scan).

    Multi-select (space-separated, like [n]ew-pool); non-blocking — the live view
    can be left (Ctrl-C) with everything still running. Re-attaches an in-flight
    burn-in first if one exists."""
    from . import burnin
    state = burnin.load_state()
    if state:
        print(f"  {len(state)} burn-in(s) in progress.")
        print("    [v] view live status   [c] cancel one   [a] cancel all   [n] start new")
        ch = _ask("  action> ").lower()
        if ch == "v":
            burnin.status_view(); return
        if ch == "a":
            if _confirm(f"cancel ALL {len(state)} burn-in(s)?"):
                burnin.cancel_all(dry_run=_DRY_RUN)
            return
        if ch == "c":
            for i, r in enumerate(state, 1):
                print(f"    [{i}] bay {r.get('bay') or '?'} {r['dev']} ({r.get('serial') or '?'})")
            try:
                r = state[_one_based(_ask("  cancel which #> "))]
            except (ValueError, IndexError):
                print(f"{Y}  cancelled{N}"); return
            if _confirm(f"cancel burn-in on bay {r.get('bay') or '?'} {r['dev']}?"):
                burnin.cancel([r.get("serial") or r["dev"]], dry_run=_DRY_RUN)
            return
        if ch != "n":
            return                          # unknown key = do nothing (safe)
        # [n] falls through to start a new burn-in
    avail = _avail_for_aux(tbw)
    if not avail:
        print(f"{Y}  no free disks to burn in{N}"); return
    for i, d in enumerate(avail, 1):
        print(f"    [{i}] {d.dev} (bay {d.bay or '?'}) {d.model}")
    sel = _ask("  burn in which #> (space-separated) ")
    try:
        picks = [avail[i] for i in _pick_indices(sel, len(avail))]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled or invalid selection{N}"); return
    if not picks:
        print(f"{Y}  cancelled{N}"); return
    if not _confirm(f"burn-in {len(picks)} disk(s) (long self-test)?"):
        print(f"{Y}  cancelled{N}"); return
    do_scan = _confirm("also run a full read-surface scan (badblocks, read-only, hours)?")
    burnin.run_multi(picks, tbw, do_scan=do_scan, dry_run=_DRY_RUN)


def _cmd_udev_rescue(tbw) -> None:
    """Explicitly attempt a udev rescue for OS-rejected ghost disks.

    The read path never does this (CLAUDE.md §9); it only happens here, behind a
    [y/N], because it fires `udevadm trigger`/`settle` on the device.
    """
    ghosts = [d for d in core.scan(tbw) if d.health == "GHOST" and d.serial]
    if not ghosts:
        print(f"{Y}  no ghost (OS-rejected) disks to rescue{N}")
        return
    for g in ghosts:
        print(f"    ghost bay {g.bay or '?'} serial {g.serial}")
    if not _confirm(f"run udevadm trigger/settle to rescue {len(ghosts)} ghost disk(s)?"):
        print(f"{Y}  cancelled{N}"); return
    after = core.scan(tbw, rescue=True)
    remaining = [d for d in after if d.health == "GHOST" and d.serial]
    recovered = len(ghosts) - len(remaining)
    if recovered > 0:
        print(G + f"  ✔ rescued {recovered} disk(s)" + N)
    else:
        print(f"{Y}  no disks recovered — reseat physically or wipe via [a]ssign{N}")
    _cmd_refresh(tbw)


_MENU = (f"{C}[r]{N}efresh  {C}[a]{N}ssign  {C}[o]{N}ffload  {C}[s]{N}wap  {C}[d]{N}emote  {C}[t]{N}oggle-dryrun  "
         f"{C}[n]{N}ew-pool  {C}[e]{N}xtend  {C}[b]{N}urnin  {C}[u]{N}dev-rescue  {C}[x]{N}destroy-pool  {C}[l]{N}ocate  {C}[q]{N}uit   (or hot-plug)")


def run() -> int:
    tbw = spec.load()
    # Disable maintenance timers for pools destroyed outside b2ctl (manual zpool
    # destroy). Honor --dry-run: the documented `b2ctl --dry-run watch` preview must
    # not disable a real timer at startup (F-058).
    for u in zfs.prune_orphan_timers(dry_run=_DRY_RUN):
        verb = "would disable" if _DRY_RUN else "disabled"
        print(f"{Y}  {verb} stale timer {u} (pool no longer exists){N}")
    _cmd_refresh(tbw)
    print("\n" + _MENU)
    baseline = _block_devs() or set()
    sys.stdout.write("b2ctl> "); sys.stdout.flush()
    while True:
        try:
            r, _, _ = select.select([sys.stdin], [], [], POLL)
        except KeyboardInterrupt:
            print("\nbye"); return 0
        needs_prompt = False
        if r:
            raw = sys.stdin.readline()
            if raw == "":                       # EOF (Ctrl-D) — quit, don't busy-loop
                print("\nbye"); return 0
            cmd = raw.strip().lower()
            try:
                if cmd in ("q", "quit", "exit"):
                    print("bye"); return 0
                elif cmd in ("r", ""):
                    _cmd_refresh(tbw)
                elif cmd in ("a", "assign"):
                    _cmd_assign(tbw)
                elif cmd in ("o", "offload"):
                    _cmd_offload(tbw)
                elif cmd in ("s", "swap"):
                    _cmd_swap(tbw)
                elif cmd in ("d", "demote"):
                    _cmd_demote(tbw)
                elif cmd in ("t", "dryrun"):
                    _toggle_dry_run()
                elif cmd == "n":
                    _cmd_create(tbw)
                elif cmd in ("e", "extend"):
                    _cmd_extend(tbw)
                elif cmd in ("b", "burnin"):
                    _cmd_burnin(tbw)
                elif cmd in ("u", "rescue"):
                    _cmd_udev_rescue(tbw)
                elif cmd == "x":
                    _cmd_destroy(tbw)
                elif cmd == "l":
                    _cmd_locate(tbw)
                else:
                    print(f"{Y}  unknown command{N}")
            except KeyboardInterrupt:
                # Ctrl-C inside a command aborts that command, not the whole session.
                print(f"\n{Y}  (cancelled — back to prompt){N}")
            needs_prompt = True

        current = _block_devs()
        if current is not None:                 # None = transient lsblk failure
            new, gone = current - baseline, baseline - current
            if gone or new:
                print("")
                if gone:
                    _handle_removed({n for n, _ in gone})
                for name in sorted(n for n, _ in new):
                    _handle_new_disk(f"/dev/{name}", tbw)
                needs_prompt = True
            baseline = current

        if needs_prompt:
            print(_MENU)
            sys.stdout.write("b2ctl> "); sys.stdout.flush()

