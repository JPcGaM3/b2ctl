"""b2ctl.hba_raid — RAID-mode backend (perccli).

Supports Dell PERC controllers in RAID mode. Physical drives behind a RAID
virtual disk are enumerated from `perccli /cN/vall show all` and read via SMART
passthrough (`smartctl -d megaraid,<DID>`); JBOD/non-RAID disks appear in lsblk
as /dev/sdX. The virtual disk itself is reported by raid_volumes(), not as a
disk row.

perccli64 and perccli are the same tool (64-bit binary name vs copied name).
storcli was dropped — it is blind to a PERC and only caused false detection.
"""
from __future__ import annotations

import glob
import os
import re

from .common import Disk, run, run_check

CONTROLLER = 0
# Dell PERC speaks perccli; storcli (LSI) is blind to a PERC and only caused
# false RAID detection — dropped. RAID = perccli, IT = sas2ircu.
_TOOL_CANDIDATES = ("perccli64", "perccli")

_tool_cache: str | None = None
_have_tool_cache: bool | None = None
_hba_personality_cache: bool | None = None


def _reset_caches() -> None:
    """Clear the per-process perccli memos (tests / a forced re-probe, F-040)."""
    global _tool_cache, _have_tool_cache, _hba_personality_cache
    _tool_cache = None
    _have_tool_cache = None
    _hba_personality_cache = None


def _ctrlcount(tool: str) -> int | None:
    """Return the controller count a tool reports, or None if it can't run."""
    out = run([tool, "show", "ctrlcount"])
    if not out:
        return None
    m = re.search(r"Controller Count\s*=\s*(\d+)", out)
    return int(m.group(1)) if m else 0


def _pick_tool() -> str:
    """Resolve the storcli/perccli binary that actually sees a controller.

    A tool can run yet report 0 controllers (storcli is blind to a PERC), so we
    prefer the first candidate reporting a non-zero controller count and only
    fall back to a runnable-but-0 tool, then to a bare name.
    """
    from . import config as _cfg
    fallback: str | None = None
    for name in _TOOL_CANDIDATES:
        t = _cfg.tool(name)
        cnt = _ctrlcount(t)
        if cnt is None:
            continue
        if cnt > 0:
            return t
        fallback = fallback or t
    return fallback or _cfg.tool("perccli64")


def _tool() -> str:
    """Return (and cache) the resolved storcli/perccli path."""
    global _tool_cache
    if _tool_cache is None:
        _tool_cache = _pick_tool()
    return _tool_cache


def build_cmd(*parts) -> list[str]:
    """Full argv for a perccli action: resolved tool binary + parts.

    Used by BOTH the runner and the audit trail (raid_actions.begin_op) so
    ops.jsonl records the exact command that ran — not a hand-written 'perccli
    …' literal that drifts from the real _tool() path / controller index (F-089).
    """
    return [_tool(), *parts]


def have_tool() -> bool:
    """Return True if some storcli/perccli binary reports a controller.

    Memoized: one RAID-mode scan probed `show ctrlcount` up to ~10x, and perccli
    is slow (F-040/F-041). Cleared by _reset_caches (tests / hotplug refresh)."""
    global _have_tool_cache
    if _have_tool_cache is None:
        from . import config as _cfg
        _have_tool_cache = any((_ctrlcount(_cfg.tool(n)) or 0) > 0
                               for n in _TOOL_CANDIDATES)
    return _have_tool_cache


def _list_controllers() -> list[int]:
    """Return list of controller indices from `perccli show ctrlcount`."""
    t = _tool()
    out = run([t, "show", "ctrlcount"])
    m = re.search(r"Controller Count\s*=\s*(\d+)", out)
    count = int(m.group(1)) if m else 1
    return list(range(count))


def _ctrl_indices(controller: int | None = None) -> list[int]:
    """Resolve which controller indices to query, honouring config."""
    from . import config as _cfg
    if controller is not None:
        return [controller]
    setting = _cfg.controller_index_setting()
    if setting == "all":
        return _list_controllers()
    try:
        return [int(setting)]
    except (TypeError, ValueError):
        # F-039: malformed controller.index -> fall back to detection, don't crash.
        return _list_controllers()


def bay_map(controller: int | None = None) -> dict:
    """Return serial -> 'enc:slot' for all JBOD/RAID-array disks.

    Uses `perccli /c<n>/eall/sall show all` and parses the Drive Detailed
    Information section for SN and EID:Slt.
    """
    t = _tool()
    mapping: dict[str, str] = {}
    for idx in _ctrl_indices(controller):
        out = run([t, f"/c{idx}/eall/sall", "show", "all"])
        _parse_bay_map(out, mapping)
    return mapping


# Any per-drive section header, e.g. "Drive /c0/e9/s0 Device attributes :",
# "Drive /c0/e9/s0 - Detailed Information :" or a bare "Drive /c0/e9/s0 :".
# Deliberately loose: only SOME perccli builds label the section 'Device
# attributes', and requiring that literal made every SN unreadable on a Dell
# HBA330 — which is what left each PD looking 'hidden' (F-133).
_DRIVE_HDR = re.compile(r"\s*Drive\s+/c\d+/e(\d+)/s(\d+)\b")


def _parse_detail(text: str, mapping: dict, key: str, norm=None) -> None:
    """Bind each `<key> = <value>` line to the nearest preceding Drive header.

    Shared by the serial map and the WWN map: both walk the same
    `/cX/eall/sall show all` detail sections, differing only in the field they
    pick up. current_slot is cleared after a hit so a second value in the same
    section cannot re-bind the slot.
    """
    field = re.compile(rf"\s*{key}\s*=\s*(\S+)")
    current_slot: str | None = None
    for line in text.splitlines():
        m = _DRIVE_HDR.match(line)
        if m:
            current_slot = f"{m.group(1)}:{m.group(2)}"
            continue
        if current_slot:
            m2 = field.match(line)
            if m2:
                val = norm(m2.group(1)) if norm else m2.group(1)
                if val:
                    mapping[val] = current_slot
                current_slot = None


def enclosure_ids(controller: int | None = None) -> list[int]:
    """Distinct enclosure numbers the controller reports for its physical drives.

    Display only: used to prefix a sysfs-derived slot so the bay LABEL keeps the
    enclosure the operator already sees ('9:0'), never to address anything —
    perccli actions always take Disk.ctrl_slot, the raw locator (F-134).
    """
    encs = set()
    for idx in _ctrl_indices(controller):
        for pd in _parse_pd_rows(run([_tool(), f"/c{idx}/eall/sall", "show", "all"])):
            enc, _, _slot = pd["bay"].partition(":")
            if enc.isdigit():
                encs.add(int(enc))
    return sorted(encs)


def _parse_bay_map(text: str, mapping: dict) -> None:
    """Parse perccli `show all` output into {serial: 'enc:slot'}."""
    _parse_detail(text, mapping, "SN")


def _parse_wwn_map(text: str, mapping: dict) -> None:
    """Parse perccli `show all` output into {normalised WWN: 'enc:slot'}.

    A serial-independent join key. lsblk reports no SERIAL for enterprise SAS
    drives until SMART runs, so serial alone cannot tell an exposed PD from a
    hidden one on the scan path (F-133).
    """
    _parse_detail(text, mapping, "WWN", norm=_norm_wwn)


def _norm_wwn(value: str) -> str:
    """Normalise a WWN for cross-tool compare: lowercase hex, no '0x'/separators.

    lsblk prints '0x5000c500a1b2c3d4'; perccli prints '5000C500A1B2C3D4'.
    """
    s = (value or "").strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return re.sub(r"[^0-9a-f]", "", s)


def _norm_model(model: str) -> str:
    return re.sub(r"\s+", " ", (model or "").strip()).upper()


_MODEL_MIN = 8          # shortest prefix allowed to claim two models are the same


def _model_match(pd_model: str, dev_model: str) -> bool:
    """True if two model strings describe the same drive.

    perccli truncates its Model column ('Samsung SSD 860') while lsblk reports
    the full string ('Samsung SSD 860 PRO 1TB'), so this is a prefix compare in
    either direction — never plain equality. The _MODEL_MIN floor stops a
    severely truncated column from matching everything: a bare prefix test made
    ('S', 'Samsung SSD 870 EVO 1TB') True, which would suppress arbitrary drives.
    """
    a, b = _norm_model(pd_model), _norm_model(dev_model)
    if not a or not b or min(len(a), len(b)) < _MODEL_MIN:
        return False
    return a.startswith(b) or b.startswith(a)


# perccli prints BINARY units under decimal labels: an 860 PRO 1TB
# (1_024_209_543_168 B) shows as '953.869 GB' = 953.869 GiB, and a 2.4 TB SAS HDD
# (2_400_476_274_688 B) as '2.182 TB' = 2.182 TiB. Parse them as powers of 1024.
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGTP])B", re.I)
_SIZE_POW = {"K": 1, "M": 2, "G": 3, "T": 4, "P": 5}


def _pd_size_bytes(size: str) -> int | None:
    m = _SIZE_RE.search(size or "")
    if not m:
        return None
    try:
        return int(float(m.group(1)) * (1024 ** _SIZE_POW[m.group(2).upper()]))
    except ValueError:
        return None


def _size_match(pd_size: str, dev_bytes) -> bool:
    """True when a PD's size agrees with a block device's, within 10%.

    Returns True when either side is unknown — this only ever NARROWS the
    model-based suppression, it must never widen it. The tolerance absorbs
    rounding and reserved-area differences while still separating a 960 GB SSD
    from a 2.4 TB HDD.
    """
    a = _pd_size_bytes(pd_size)
    if a is None or not dev_bytes:
        return True
    return abs(a - dev_bytes) <= 0.10 * max(a, dev_bytes)


# (hba_raid._lsblk_pairs was a dead duplicate of hba._lsblk_pairs — removed,
# F-083. The RAID backend reuses hba.enumerate_disks / hba._lsblk_pairs.)


# Block-device models that mean "this is a PERC virtual disk, not a real disk".
_PERC_VD_MARKERS = ("PERC", "MEGARAID", "AVAGO", "LSI", "VIRTUAL DISK")


def _is_perc_vd(model: str) -> bool:
    m = (model or "").upper()
    return any(mark in m for mark in _PERC_VD_MARKERS)


def _parse_pd_rows(text: str) -> list[dict]:
    """Extract physical-drive rows from any perccli table (vall or eall/sall).

    Row: `EID:Slt DID State DG Size(2 tok) Intf Med SED PI SeSz Model… Sp`.
    Returns {"bay","did","state","dg","size","intf","med","model"} per drive.
    DG is "-" for unconfigured (UGood/JBOD) drives.
    """
    pds: list[dict] = []
    for line in text.splitlines():
        tok = line.split()
        if len(tok) >= 12 and re.match(r"^\d+:\d+$", tok[0]):
            pds.append({
                "bay": tok[0], "did": tok[1], "state": tok[2], "dg": tok[3],
                "size": f"{tok[4]} {tok[5]}", "intf": tok[6], "med": tok[7],
                "model": " ".join(tok[11:-1]),
            })
    return pds


def _is_foreign(row: dict) -> bool:
    """True when a parsed PD row carries a FOREIGN config (DG column == 'F').

    perccli's State and DG columns are INDEPENDENT axes: State says whether the
    drive belongs to a VD ('UGood' = it does not), DG says which drive group owns
    it — and 'F' means "foreign metadata from some other controller/array". The
    firmware refuses every transition on such a drive (set jbod / add
    hotsparedrive / add vd) with 'Operation not allowed'. The one authority for
    the test, so no enumerate path can forget it (F-135).
    """
    return (row.get("dg") or "").strip().upper() == "F"


def _parse_vall(text: str) -> tuple[list[dict], list[dict]]:
    """Parse `perccli /cN/vall show all`.

    Returns (volumes, members):
      volumes: {"vd","raid","state","size","name"}
      members: {"bay","did","state","dg","size","intf","med","model","vd","raid"}

    Member rows come from the per-VD 'PDs for VD N' table, e.g.::

        EID:Slt DID State DG     Size Intf Med SED PI SeSz Model              Sp
        32:0      0 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870... U

    Model is multi-word; parse positionally (4 fixed cols, size = 2 tokens,
    model = middle, Sp = last token).
    """
    vols: list[dict] = []
    members: list[dict] = []
    cur_vd: str | None = None
    cur_raid: str | None = None
    for line in text.splitlines():
        s = line.strip()
        mv = re.match(r"/c\d+/v(\d+)\s*:", s)
        if mv:
            cur_vd = mv.group(1)
            continue
        tok = s.split()
        # VD summary row: "0/0 RAID1 Optl RW Yes RWBD - OFF 640.0 GB MainSSD"
        if (len(tok) >= 10 and re.match(r"^\d+/\d+$", tok[0])
                and tok[1].upper().startswith("RAID")):
            vd = tok[0].split("/")[1]
            cur_raid = tok[1]
            vols.append({"vd": vd, "raid": tok[1], "state": tok[2],
                         "size": f"{tok[8]} {tok[9]}",
                         "name": " ".join(tok[10:])})
            continue
        # PD row: starts "EID:Slt DID State DG  Size Unit Intf Med ..."
        if len(tok) >= 12 and re.match(r"^\d+:\d+$", tok[0]):
            members.append({
                "bay": tok[0], "did": tok[1], "state": tok[2], "dg": tok[3],
                "size": f"{tok[4]} {tok[5]}", "intf": tok[6], "med": tok[7],
                "model": " ".join(tok[11:-1]), "vd": cur_vd, "raid": cur_raid,
            })
    return vols, members


def _vall_data() -> tuple[list[dict], list[dict]]:
    """Run perccli vall for every controller; return (volumes, members)."""
    t = _tool()
    vols_all: list[dict] = []
    members_all: list[dict] = []
    for idx in _ctrl_indices():
        vols, members = _parse_vall(run([t, f"/c{idx}/vall", "show", "all"]))
        for v in vols:
            v["controller"] = idx
        for m in members:
            m["controller"] = idx
        vols_all += vols
        members_all += members
    return vols_all, members_all


# --------------------------------------------------------------------------- #
# Controller personality — does the CONTROLLER own the storage, or the OS?
# --------------------------------------------------------------------------- #

def _megaraid_driver_present() -> bool:
    """True when a megaraid_sas SCSI host exists.

    `smartctl -d megaraid,<DID>` goes through the MegaRAID SAS ioctl, so it
    works only against a megaraid_sas host. A Dell HBA330/H330 binds mpt3sas —
    perccli still manages the card, but RAID-mode SMART is impossible there by
    construction, which is the decisive (and free) personality signal.
    """
    for path in glob.glob("/sys/class/scsi_host/host*/proc_name"):
        try:
            with open(path) as f:
                if f.read().strip() == "megaraid_sas":
                    return True
        except OSError:
            continue
    return False


def _personality(controller: int = CONTROLLER) -> str:
    """Controller personality from `perccli /cN show` ('' when not reported).

    13G+ PERCs expose a switchable personality: 'RAID-Mode' / 'HBA-Mode'. A Dell
    HBA330 Mini prints NO personality line at all — it has no switch, it is IT
    firmware permanently — so '' is a normal answer here, not an error.
    """
    out = run([_tool(), f"/c{controller}", "show"])
    m = (re.search(r"Current Personality\s*=\s*(\S+)", out)
         or re.search(r"^\s*Personality\s*=\s*(\S+)", out, re.M))
    return m.group(1).strip().upper() if m else ""


def _driver_name(controller: int = CONTROLLER) -> str:
    """Kernel driver the controller is bound to, as perccli itself reports it.

    `perccli /c0 show` on a Dell HBA330 Mini prints `Driver Name = mpt3sas`;
    a PERC in RAID mode prints `megaraid_sas`. Anything other than megaraid_sas
    means the MegaRAID SAS ioctl does not exist for this card, so
    `smartctl -d megaraid,<DID>` cannot work — the decisive personality signal,
    and more portable than reading sysfs (F-133).
    """
    out = run([_tool(), f"/c{controller}", "show"])
    m = re.search(r"^\s*Driver Name\s*=\s*(\S+)", out, re.M)
    return m.group(1).strip() if m else ""


def is_hba_personality() -> bool:
    """True when perccli manages the card but the OS — not it — owns the disks.

    That is a Dell HBA330/H330, or a PERC switched to HBA-Mode / with every
    drive in JBOD. RAID-mode enumeration is wrong for such a card in two ways:
    it synthesises one Disk per controller PD (which duplicates a block device
    the OS already exposes) and reads each through a megaraid passthrough that
    does not exist. On an HBA330 that turned 9 real drives into 18 rows, half of
    them phantom `/dev/sda` entries with no serial and NOREAD health (F-133).

    Memoized — the probe costs up to three perccli round-trips and perccli is
    slow (F-040). Cleared by _reset_caches().
    """
    global _hba_personality_cache
    if _hba_personality_cache is None:
        _hba_personality_cache = _probe_hba_personality()
    return _hba_personality_cache


def _probe_hba_personality() -> bool:
    if not have_tool():
        return False
    # A virtual disk is definitive and is checked FIRST: the sysfs driver probe
    # below reads False wherever /sys is absent (a dev box, the sim harness), and
    # a real RAID controller must never be misclassified there.
    vols, _members = _vall_data()
    if vols:
        return False                    # the controller owns storage
    idxs = _ctrl_indices()
    # A controller that NAMES its own personality is authoritative in BOTH
    # directions. Reading 'RAID-Mode' only as "not HBA" and then falling through
    # to a heuristic classified a freshly-wiped H730P (no VD yet) as an HBA and
    # locked the operator out of every raid-* verb (F-133 review).
    pers = [_personality(i) for i in idxs]
    if any(p.startswith("RAID") for p in pers):
        return False
    if any(p.startswith("HBA") for p in pers):
        return True
    named = [d for d in (_driver_name(i) for i in idxs) if d]
    if named:
        # perccli named the driver: trust it over sysfs. Anything but
        # megaraid_sas (mpt3sas on an HBA330/HBA355) has no MegaRAID ioctl.
        if all(d != "megaraid_sas" for d in named):
            return True
    elif not _megaraid_driver_present():
        return True                     # no megaraid_sas host => no passthrough
    # Last resort — a megaraid_sas card with no VD and no personality string.
    # HBA-like only if EVERY physical drive it reports already resolves to an OS
    # block device. Comparing raw counts instead (len(lsblk) >= len(pds)) let an
    # unrelated BOSS mirror and two NVMe outvote two hidden PERC drives.
    pds: list[dict] = []
    bm: dict = {}
    wm: dict = {}
    for idx in idxs:
        text = run([_tool(), f"/c{idx}/eall/sall", "show", "all"])
        pds += _parse_pd_rows(text)
        _parse_bay_map(text, bm)
        _parse_wwn_map(text, wm)
    if not pds:
        return False
    bay_to_sn = {bay: sn for sn, bay in bm.items()}
    bay_to_wwn = {bay: w for w, bay in wm.items()}
    from . import blockdev
    from .baymap import serial_match
    rows = [r for r in blockdev.lsblk_pairs("NAME,TYPE,SERIAL,WWN")
            if r.get("TYPE") == "disk"
            and not r.get("NAME", "").startswith(blockdev.EXCLUDE)]
    os_sn = {(r.get("SERIAL") or "").strip() for r in rows} - {""}
    os_wwn = {_norm_wwn(r.get("WWN") or "") for r in rows} - {""}
    for pd in pds:
        sn = bay_to_sn.get(pd["bay"], "")
        wwn = bay_to_wwn.get(pd["bay"], "")
        if sn and any(serial_match(sn, s) for s in os_sn):
            continue
        if wwn and wwn in os_wwn:
            continue
        return False                    # this PD is hidden => the controller owns it
    return True


def _match_os_disk(sn: str, wwn: str, by_sn: dict, by_wwn: dict) -> Disk | None:
    """Resolve the block device a controller PD ALREADY appears as, or None.

    Serial first (exact, then the project's fuzzy prefix rule), then WWN.
    """
    from .baymap import serial_match
    if sn:
        d = by_sn.get(sn)
        if d is None:
            d = next((x for s, x in by_sn.items() if serial_match(sn, s)), None)
        if d is not None:
            return d
    if wwn:
        d = by_wwn.get(wwn)
        if d is not None:
            return d
    return None


def enumerate_disks() -> list[Disk]:
    """Return Disks for PERC RAID members + JBOD/direct block devices.

    RAID members live behind a virtual disk and are invisible to lsblk, so they
    are synthesised from perccli with `dev` pointing at a controller block
    device and `smart_dtype = "megaraid,<DID>"` (SMART read via passthrough).
    The virtual-disk block device itself (model 'PERC …') is dropped from the
    disk rows — it is reported separately by `raid_volumes()`.
    """
    from . import hba
    raw = hba.enumerate_disks()
    if not have_tool():
        return raw

    _vols, members = _vall_data()
    perc_devs = [d for d in raw if _is_perc_vd(d.model)]
    perc_dev_set = {d.dev for d in perc_devs}
    # Any block device on the controller is a valid megaraid SMART target.
    ctrl_dev = (perc_devs[0].dev if perc_devs
                else (raw[0].dev if raw else "/dev/sda"))
    # Fetch eall/sall ONCE per controller and reuse the text for both the
    # serial map and the non-member PD pass — the old code ran it twice, plus a
    # third time inside bay_map() (F-040/F-041).
    t = _tool()
    eall_by_ctrl = {idx: run([t, f"/c{idx}/eall/sall", "show", "all"])
                    for idx in _ctrl_indices()}
    bm: dict = {}
    wm: dict = {}
    for text in eall_by_ctrl.values():
        _parse_bay_map(text, bm)
        _parse_wwn_map(text, wm)
    bay_to_sn = {bay: sn for sn, bay in bm.items()}
    bay_to_wwn = {bay: w for w, bay in wm.items()}

    member_disks: list[Disk] = []
    member_bays = set()
    for m in members:
        d = Disk(dev=ctrl_dev)
        d.bay = m["bay"]
        d.ctrl_slot = m["bay"]          # raw perccli enc:slot (never remapped)
        d.ctrl = m.get("controller")    # which /cN this PD lives on (F-085)
        d.did = int(m["did"]) if str(m["did"]).isdigit() else None
        d.smart_dtype = f"megaraid,{m['did']}"
        d.model = m["model"]
        d.serial = bay_to_sn.get(m["bay"], "")
        d.is_ssd = (m["med"].upper() == "SSD")
        d.iface = m["intf"]
        d.array_type = "HW"
        d.array_name = f"vd{m['vd']}/{(m['raid'] or '').lower()}"
        d.pd_state = m["state"]
        d.pd_foreign = _is_foreign(m)
        member_disks.append(d)
        member_bays.add(m["bay"])

    # Non-member physical drives the PERC sees (UGood/JBOD/Failed). A drive the
    # OS ALREADY exposes must tag that block device; only a drive the controller
    # genuinely hides is synthesised with megaraid SMART. Getting that test
    # wrong duplicates the whole fleet (F-133), so it joins on serial, then WWN,
    # then refuses to synthesise anything an OS disk could plausibly be.
    # The VD's own block device is never a physical drive — keep it out of every
    # join table so it can't absorb a PD.
    os_disks = [d for d in raw if d.dev not in perc_dev_set]
    by_sn = {d.serial: d for d in os_disks if d.serial}
    by_wwn = {}
    for d in os_disks:
        w = _norm_wwn(d.wwn)
        if w:
            by_wwn[w] = d
    claimed: set[int] = set()
    pending: list[tuple] = []

    # PASS 1 — join every non-member PD to the block device it already IS.
    # This must finish before any suppression decision: `claimed` is only
    # complete once every PD has had its turn, and deciding mid-loop made a
    # drive's very existence depend on enc:slot iteration order (F-133 review).
    for idx in _ctrl_indices():
        for pd in _parse_pd_rows(eall_by_ctrl.get(idx, "")):
            if pd["bay"] in member_bays:
                continue
            sn = bay_to_sn.get(pd["bay"], "")
            wwn = bay_to_wwn.get(pd["bay"], "")
            target = _match_os_disk(sn, wwn, by_sn, by_wwn)
            if target is not None:              # OS-exposed JBOD: tag the real disk
                target.bay, target.pd_state = pd["bay"], pd["state"]
                target.pd_foreign = _is_foreign(pd)
                target.ctrl_slot, target.ctrl = pd["bay"], idx
                claimed.add(id(target))
                continue
            pending.append((idx, pd, sn, wwn))

    # PASS 2 — synthesise what stayed unmatched. The model/size refusal applies
    # ONLY to a PD perccli could not identify at all (no SN and no WWN): that is
    # the HBA330 case this exists for. A PD that HAS an identity which simply
    # matches no OS disk is genuinely hidden behind the controller and must keep
    # its row — dropping it removed real UGood/Failed drives from `status` and
    # from the raid-create/hotspare pickers (F-133 review).
    for idx, pd, sn, wwn in pending:
        if not sn and not wwn and any(
                id(r) not in claimed and _model_match(pd["model"], r.model)
                and _size_match(pd["size"], r.size_bytes) for r in os_disks):
            continue                            # this PD IS one of those OS disks
        d = Disk(dev=ctrl_dev)                  # hidden drive: synthesise + megaraid SMART
        d.bay = pd["bay"]
        d.ctrl_slot = pd["bay"]                 # raw perccli enc:slot (never remapped)
        d.ctrl = idx                            # which /cN this PD lives on (F-085)
        d.did = int(pd["did"]) if str(pd["did"]).isdigit() else None
        d.smart_dtype = f"megaraid,{pd['did']}"
        d.model = pd["model"]
        d.serial = sn
        d.is_ssd = (pd["med"].upper() == "SSD")
        d.iface = pd["intf"]
        d.pd_state = pd["state"]                # array_type stays "" (not in an array)
        d.pd_foreign = _is_foreign(pd)
        member_disks.append(d)

    # Keep lsblk disks that are NOT a PERC virtual disk (JBOD/non-RAID + NVMe).
    raw_kept = [d for d in raw if d.dev not in perc_dev_set]
    return member_disks + raw_kept


def raid_volumes() -> list[dict]:
    """Return hardware RAID volumes for the volumes table (empty if none)."""
    if not have_tool():
        return []
    vols, members = _vall_data()
    counts: dict[tuple, int] = {}
    for m in members:
        counts[(m.get("controller"), m["vd"])] = \
            counts.get((m.get("controller"), m["vd"]), 0) + 1
    out = []
    for v in vols:
        v = dict(v)
        v["members"] = counts.get((v.get("controller"), v["vd"]), 0)
        out.append(v)
    return out


def attach_bays(disks: list[Disk], controller: int | None = None, bm=None) -> None:
    """Fill disk.bay from perccli via the shared baymap.assign_bays loop (F-084)."""
    from . import baymap
    # A populated bm proves the tool works; only probe when nothing was passed
    # (a direct call, not core.scan) — F-041.
    if bm is None and not have_tool():
        return
    panels = baymap.load()
    if bm is None:
        bm = bay_map(controller)
    baymap.assign_bays(disks, bm, panels)      # shared serial-match loop (F-084)


def get_ghost_disks(disks: list[Disk], controller: int | None = None, bm=None) -> list[Disk]:
    """No ghosts in RAID mode.

    The IT-mode "ghost" concept means a disk the HBA sees but the OS rejected
    (RAID metadata). Under a PERC in RAID mode the controller *deliberately*
    hides non-VD drives from the OS — that is normal, and such drives are
    surfaced as available (UGood/JBOD) by `enumerate_disks()`, not as ghosts.
    """
    return []


def udev_rescue_ghost(serial: str) -> bool:
    """Same udev rescue as IT-mode (sgX path is controller-independent)."""
    from . import hba
    return hba.udev_rescue_ghost(serial)


def _pd(enc_slot: str, controller: int = CONTROLLER) -> str:
    """Return the perccli physical-drive selector '/cC/eE/sS' for an enc:slot.

    Rejects a non-numeric enc:slot (e.g. a remapped display bay label leaking in)
    so a mutating action can never silently target the wrong physical slot
    (F-016). Callers pass Disk.ctrl_slot — the raw controller locator — not the
    display bay.
    """
    if not re.fullmatch(r"\d+:\d+", enc_slot or ""):
        raise ValueError(
            f"invalid controller enc:slot {enc_slot!r} — refusing to build a "
            f"perccli selector from a display bay label")
    enc, slot = enc_slot.split(":")
    return f"/c{controller}/e{enc}/s{slot}"


def locate(enc_slot: str, on: bool, controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Turn the locate LED on/off via perccli. enc_slot e.g. '32:0'.

    perccli syntax is verb-first: `/cC/eE/sS start locate` / `... stop locate`.
    """
    action = "start" if on else "stop"
    return run_check(build_cmd(_pd(enc_slot, controller), action, "locate"),
                     dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Mutating PERC actions (perccli). Callers MUST confirm first; each is audited
# by the cli/watch layer. Defensive output parsing — validate on hardware.
# --------------------------------------------------------------------------- #
def set_offline(enc_slot: str, controller: int = CONTROLLER, *,
                dry_run: bool = False) -> tuple[bool, str]:
    """Mark a physical drive offline (prepare to fail it out)."""
    return run_check(build_cmd(_pd(enc_slot, controller), "set", "offline"), dry_run=dry_run)


def set_missing(enc_slot: str, controller: int = CONTROLLER, *,
                dry_run: bool = False) -> tuple[bool, str]:
    """Mark an offline drive as missing so it can be pulled."""
    return run_check(build_cmd(_pd(enc_slot, controller), "set", "missing"), dry_run=dry_run)


def start_rebuild(enc_slot: str, controller: int = CONTROLLER, *,
                  dry_run: bool = False) -> tuple[bool, str]:
    """Start a rebuild onto the drive in enc:slot."""
    return run_check(build_cmd(_pd(enc_slot, controller), "start", "rebuild"), dry_run=dry_run)


def pd_state(enc_slot: str, controller: int = CONTROLLER) -> str:
    """Current PD state (Onln/Rbld/UGood/Offln/...) for an enc:slot, or ''.

    Used to disambiguate rebuild_progress's 'Not in progress' — which reads the
    same whether a rebuild finished (PD Onln) or never started (PD still
    UGood/Offln). Re-parses the live PD table (F-007).
    """
    out = run([_tool(), f"/c{controller}/eall/sall", "show", "all"])
    for pd in _parse_pd_rows(out):
        if pd["bay"] == enc_slot:
            return pd["state"]
    return ""


# --------------------------------------------------------------------------- #
# Foreign-config + JBOD-policy probes (READ-ONLY — run(), never run_check).
#
# A drive carrying a foreign config is refused EVERY state transition by the
# firmware, so these answer "why did perccli say 'Operation not allowed'?" and
# feed the assign pre-flight. Deliberately NOT called from core.scan(): perccli
# is slow enough that its probes are already memoised (F-040/F-041), and the
# enumerate path gets the same answer for free from the PD table's DG column.
# --------------------------------------------------------------------------- #

_SIZE_TOKEN = re.compile(r"([\d.]+\s*[KMGTP]B)", re.I)


def foreign_config(controller: int = CONTROLLER) -> list[dict]:
    """Parse `perccli /cN/fall show` -> [{"dg","bay","type","state","size"}].

    The presence of enc:slot ROWS is the signal, never the Status line: several
    perccli builds answer "no foreign configuration present" with
    `Status = Failure`, so keying on that would report a foreign config on every
    healthy controller.

    Parsed by locating the enc:slot token rather than by fixed column index — the
    DID column is present in some builds and absent in others.
    """
    out = run([_tool(), f"/c{controller}/fall", "show"])
    rows: list[dict] = []
    for line in out.splitlines():
        tok = line.split()
        idx = next((i for i, t in enumerate(tok) if re.fullmatch(r"\d+:\d+", t)), None)
        if idx is None:
            continue
        rest = tok[idx + 1:]
        ti = next((i for i, t in enumerate(rest) if t.upper().startswith("RAID")), None)
        size = _SIZE_TOKEN.search(line)
        rows.append({
            "dg": tok[idx - 1] if idx else "",
            "bay": tok[idx],
            "type": rest[ti] if ti is not None else "",
            "state": rest[ti + 1] if ti is not None and len(rest) > ti + 1 else "",
            "size": size.group(1) if size else "",
        })
    return rows


def foreign_bays(controller: int | None = None) -> set[str]:
    """Every enc:slot holding a foreign config, across the configured controllers."""
    bays: set[str] = set()
    for idx in _ctrl_indices(controller):
        bays.update(r["bay"] for r in foreign_config(idx))
    return bays


def jbod_capability(controller: int = CONTROLLER) -> dict:
    """{"supported": bool|None, "enabled": bool|None} from `perccli /cN show all`.

    Two different gates with the same failure message: a controller may not
    support JBOD at all (PERC 11 in RAID personality), or support it with the
    policy switched off. None means the field was not printed — an HBA330 prints
    neither, because its drives are raw already. b2ctl only REPORTS this; it
    never flips the policy, since that is controller-wide and the operator's call.
    """
    out = run([_tool(), f"/c{controller}", "show", "all"])
    sup = re.search(r"Support\s+JBOD\s*=\s*(\S+)", out, re.I)
    # '^\s*JBOD =' cannot match the 'Support JBOD =' line above: after the line
    # start comes 'Support', not 'JBOD'.
    ena = re.search(r"^\s*(?:Enable\s+)?JBOD\s*=\s*(\S+)", out, re.I | re.M)
    return {"supported": _yes(sup), "enabled": _yes(ena)}


def _yes(m) -> bool | None:
    if not m:
        return None
    return m.group(1).strip().upper() in ("YES", "ON", "TRUE", "ENABLED")


_NOT_ALLOWED = ("operation not allowed", "errcd 255")


def explain_error(out: str, *, d=None, controller: int | None = None) -> str:
    """Translate a perccli refusal into its real causes ('' when unrecognised).

    perccli reports every policy refusal as the same opaque 'ErrCd 255 Operation
    not allowed', which sent an operator off-tool to diagnose a foreign config by
    hand (F-135). The causes are checked in the order they actually bite.
    """
    if not any(m in (out or "").lower() for m in _NOT_ALLOWED):
        return ""
    ctrl = controller
    if ctrl is None:
        ctrl = getattr(d, "ctrl", None)
    if ctrl is None:
        ctrl = CONTROLLER
    bay = getattr(d, "ctrl_slot", "") or getattr(d, "bay", "") or "?"
    foreign = bool(getattr(d, "pd_foreign", False)) or bay in foreign_bays(ctrl)
    cap = jbod_capability(ctrl)
    def _mark(v):                       # the first YES is the one to act on
        return "  <-- this" if v else ""
    lines = ["why: the PERC refuses this transition. Checked:",
             f"  - foreign config on {bay:<10} -> "
             f"{'YES' if foreign else 'no'}{_mark(foreign)}"]
    if cap["enabled"] is not None:
        off = cap["enabled"] is False
        lines.append(f"  - controller {ctrl} JBOD policy  -> "
                     f"{'ON' if cap['enabled'] else 'OFF'}{_mark(off and not foreign)}")
    if cap["supported"] is not None:
        unsup = cap["supported"] is False
        lines.append(f"  - Support JBOD             -> "
                     f"{'Yes' if cap['supported'] else 'No'}"
                     f"{_mark(unsup and not foreign)}")
    if foreign:
        lines.append(f"  fix: assign -> [5] Foreign config, or "
                     f"`perccli /c{ctrl}/fall show` then `... del`")
    elif cap["supported"] is False:
        lines.append("  fix: this controller has no JBOD/non-RAID mode — build a "
                     "hardware volume instead, or switch its personality to HBA "
                     "(DESTRUCTIVE, deletes every VD).")
    elif cap["enabled"] is False:
        lines.append(f"  fix: `perccli /c{ctrl} set jbod=on` (controller-wide "
                     f"policy — b2ctl will not flip it for you)")
    return "\n".join(lines)


def rebuild_progress(enc_slot: str, controller: int = CONTROLLER) -> dict:
    """Parse `perccli /cC/eE/sS show rebuild`.

    Returns {"pct": float, "done": bool, "in_progress": bool}. Real perccli/
    storcli prints a table row ('/c0/e32/s4  28  In progress  0 Minutes') whose
    percent is a BARE integer under a 'Progress%' header — no trailing '%' — so
    the table-row match is tried first and the '%'-suffixed MegaCli form is kept
    only as a fallback (F-042). 'in_progress' lets the replace guard tell a
    28%-underway rebuild from a not-yet-started one.
    """
    out = run([_tool(), _pd(enc_slot, controller), "show", "rebuild"])
    low = out.lower()
    m = re.search(r"^/c\d+/e\d+/s\d+\s+(\d+(?:\.\d+)?)\s", out, re.M)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", out)      # MegaCli-style fallback
    pct = float(m.group(1)) if m else 0.0
    in_progress = re.search(r"(?<!not )in progress", low) is not None
    done = ("not in progress" in low) or pct >= 100.0
    return {"pct": pct, "done": done, "in_progress": in_progress}


def _raid_token(level: str) -> str:
    """Normalise a level to perccli's r-form: raid1 / r1 / 1 -> 'r1'."""
    lv = level.lower().strip()
    if lv.startswith("raid"):
        lv = lv[4:]
    return "r" + lv.lstrip("r")


def add_vd(level: str, drives: list[str], controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Create a virtual disk: `perccli /cC add vd rN drives=e:s,e:s`.

    perccli takes the level as r0/r1/r5/... (not type=raidN).
    """
    return run_check(build_cmd(f"/c{controller}", "add", "vd",
                     _raid_token(level), f"drives={','.join(drives)}"), dry_run=dry_run)


def add_hotspare(enc_slot: str, dg=None, controller: int = CONTROLLER, *,
                 dry_run: bool = False) -> tuple[bool, str]:
    """Add a drive as a hot spare: `perccli /cC/eE/sS add hotsparedrive [DGs=<dg>]`.

    dg=None -> global spare; dg=<n> -> dedicated to that drive group.
    """
    cmd = build_cmd(_pd(enc_slot, controller), "add", "hotsparedrive")
    if dg is not None and str(dg) != "":
        cmd.append(f"DGs={dg}")
    return run_check(cmd, dry_run=dry_run)


def set_jbod(enc_slot: str, controller: int = CONTROLLER, *,
             dry_run: bool = False) -> tuple[bool, str]:
    """Expose a drive to the OS for software RAID/ZFS: `perccli /cC/eE/sS set jbod`.

    The drive leaves the controller's RAID management and appears as /dev/sdX.
    """
    return run_check(build_cmd(_pd(enc_slot, controller), "set", "jbod"), dry_run=dry_run)


def import_foreign(controller: int = CONTROLLER, *,
                   dry_run: bool = False) -> tuple[bool, str]:
    """Import every foreign config on a controller: `perccli /cN/fall import`.

    CONTROLLER-WIDE. MegaRAID exposes no per-drive form of this — /cN/fall is the
    only selector — so the caller MUST confirm at controller scope and show the
    full affected set first (ADR-006).
    """
    return run_check(build_cmd(f"/c{controller}/fall", "import"), dry_run=dry_run)


def clear_foreign(controller: int = CONTROLLER, *,
                  dry_run: bool = False) -> tuple[bool, str]:
    """Discard every foreign config on a controller: `perccli /cN/fall del`.

    CONTROLLER-WIDE and DESTRUCTIVE: the foreign array becomes unimportable and
    its drives drop to plain Unconfigured-Good. Same scope caveat as
    import_foreign — confirm at controller scope, never per drive (ADR-006).
    """
    return run_check(build_cmd(f"/c{controller}/fall", "del"), dry_run=dry_run)


def del_vd(vd: int, controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Delete a virtual disk (DESTRUCTIVE): `perccli /cC/vV del force`."""
    return run_check(build_cmd(f"/c{controller}/v{vd}", "del", "force"), dry_run=dry_run)
