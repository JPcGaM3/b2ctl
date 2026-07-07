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


def _reset_caches() -> None:
    """Clear the per-process perccli memos (tests / a forced re-probe, F-040)."""
    global _tool_cache, _have_tool_cache
    _tool_cache = None
    _have_tool_cache = None


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


def _parse_bay_map(text: str, mapping: dict) -> None:
    """Parse perccli `show all` output into {serial: 'enc:slot'}."""
    # Pattern: "Drive /c<n>/e<enc>/s<slot> Device attributes"
    # followed by "SN = <serial>"
    current_slot: str | None = None
    for line in text.splitlines():
        m = re.match(r"\s*Drive\s+/c\d+/e(\d+)/s(\d+)\s+Device", line)
        if m:
            current_slot = f"{m.group(1)}:{m.group(2)}"
            continue
        if current_slot:
            m2 = re.match(r"\s*SN\s*=\s*(\S+)", line)
            if m2:
                mapping[m2.group(1)] = current_slot
                current_slot = None


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
    for text in eall_by_ctrl.values():
        _parse_bay_map(text, bm)
    bay_to_sn = {bay: sn for sn, bay in bm.items()}

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
        member_disks.append(d)
        member_bays.add(m["bay"])

    # Non-member physical drives the PERC sees (UGood/JBOD/Failed). These are NOT
    # ghosts — the controller just hides them from the OS. Surface them as real
    # disks (megaraid SMART) so they show health + state, not false GHOST rows.
    raw_serials = {d.serial for d in raw if d.serial}
    for idx in _ctrl_indices():
        for pd in _parse_pd_rows(eall_by_ctrl.get(idx, "")):
            if pd["bay"] in member_bays:
                continue
            sn = bay_to_sn.get(pd["bay"], "")
            if sn and sn in raw_serials:        # OS-exposed JBOD: tag the real disk
                for r in raw:
                    if r.serial == sn:
                        r.bay, r.pd_state, r.ctrl_slot = pd["bay"], pd["state"], pd["bay"]
                        r.ctrl = idx
                continue
            d = Disk(dev=ctrl_dev)              # hidden drive: synthesise + megaraid SMART
            d.bay = pd["bay"]
            d.ctrl_slot = pd["bay"]            # raw perccli enc:slot (never remapped)
            d.ctrl = idx                       # which /cN this PD lives on (F-085)
            d.did = int(pd["did"]) if str(pd["did"]).isdigit() else None
            d.smart_dtype = f"megaraid,{pd['did']}"
            d.model = pd["model"]
            d.serial = sn
            d.is_ssd = (pd["med"].upper() == "SSD")
            d.iface = pd["intf"]
            d.pd_state = pd["state"]            # array_type stays "" (not in an array)
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


def del_vd(vd: int, controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Delete a virtual disk (DESTRUCTIVE): `perccli /cC/vV del force`."""
    return run_check(build_cmd(f"/c{controller}/v{vd}", "del", "force"), dry_run=dry_run)
