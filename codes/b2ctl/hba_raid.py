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


def have_tool() -> bool:
    """Return True if some storcli/perccli binary reports a controller."""
    from . import config as _cfg
    for name in _TOOL_CANDIDATES:
        cnt = _ctrlcount(_cfg.tool(name))
        if cnt and cnt > 0:
            return True
    return False


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
    return _list_controllers() if setting == "all" else [int(setting)]


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


def _lsblk_pairs(cols: str) -> list[dict]:
    """Parse lsblk -P KEY="value" lines (reuse same logic as hba.py)."""
    from . import config as _cfg
    import re as _re
    _PAIR_RE = _re.compile(r'(\w+)="(.*?)"')
    out = run([_cfg.tool("lsblk"), "-dnb", "-P", "-o", cols])
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append(dict(_PAIR_RE.findall(line)))
    return rows


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
    # enc:slot -> serial (from eall/sall) so members carry SN before SMART runs.
    bay_to_sn = {bay: sn for sn, bay in bay_map().items()}

    member_disks: list[Disk] = []
    member_bays = set()
    for m in members:
        d = Disk(dev=ctrl_dev)
        d.bay = m["bay"]
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
    t = _tool()
    for idx in _ctrl_indices():
        for pd in _parse_pd_rows(run([t, f"/c{idx}/eall/sall", "show", "all"])):
            if pd["bay"] in member_bays:
                continue
            sn = bay_to_sn.get(pd["bay"], "")
            if sn and sn in raw_serials:        # OS-exposed JBOD: tag the real disk
                for r in raw:
                    if r.serial == sn:
                        r.bay, r.pd_state = pd["bay"], pd["state"]
                continue
            d = Disk(dev=ctrl_dev)              # hidden drive: synthesise + megaraid SMART
            d.bay = pd["bay"]
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
    """Fill disk.bay from perccli, same algorithm as hba.attach_bays."""
    from . import baymap
    if not have_tool():
        return
    panels = baymap.load()
    if bm is None:
        bm = bay_map(controller)
    for d in disks:
        if d.serial:
            if d.serial in bm:
                d.bay = baymap.remap_slot(bm[d.serial], panels)
            else:
                for bm_serial, bay_val in bm.items():
                    if d.serial.startswith(bm_serial) or bm_serial.startswith(d.serial):
                        d.bay = baymap.remap_slot(bay_val, panels)
                        break


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
    """Return the perccli physical-drive selector '/cC/eE/sS' for an enc:slot."""
    enc, slot = enc_slot.split(":")
    return f"/c{controller}/e{enc}/s{slot}"


def locate(enc_slot: str, on: bool, controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Turn the locate LED on/off via perccli. enc_slot e.g. '32:0'.

    perccli syntax is verb-first: `/cC/eE/sS start locate` / `... stop locate`.
    """
    action = "start" if on else "stop"
    return run_check([_tool(), _pd(enc_slot, controller), action, "locate"],
                     dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Mutating PERC actions (perccli). Callers MUST confirm first; each is audited
# by the cli/watch layer. Defensive output parsing — validate on hardware.
# --------------------------------------------------------------------------- #
def set_offline(enc_slot: str, controller: int = CONTROLLER, *,
                dry_run: bool = False) -> tuple[bool, str]:
    """Mark a physical drive offline (prepare to fail it out)."""
    return run_check([_tool(), _pd(enc_slot, controller), "set", "offline"], dry_run=dry_run)


def set_missing(enc_slot: str, controller: int = CONTROLLER, *,
                dry_run: bool = False) -> tuple[bool, str]:
    """Mark an offline drive as missing so it can be pulled."""
    return run_check([_tool(), _pd(enc_slot, controller), "set", "missing"], dry_run=dry_run)


def start_rebuild(enc_slot: str, controller: int = CONTROLLER, *,
                  dry_run: bool = False) -> tuple[bool, str]:
    """Start a rebuild onto the drive in enc:slot."""
    return run_check([_tool(), _pd(enc_slot, controller), "start", "rebuild"], dry_run=dry_run)


def rebuild_progress(enc_slot: str, controller: int = CONTROLLER) -> dict:
    """Parse `perccli /cC/eE/sS show rebuild`.

    Returns {"pct": float, "done": bool}. 'done' is True when the controller
    reports the drive is no longer in a rebuild ('Not in progress'/100%).
    """
    out = run([_tool(), _pd(enc_slot, controller), "show", "rebuild"])
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", out)
    pct = float(m.group(1)) if m else 0.0
    done = ("not in progress" in out.lower()) or pct >= 100.0
    return {"pct": pct, "done": done}


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
    return run_check([_tool(), f"/c{controller}", "add", "vd",
                      _raid_token(level), f"drives={','.join(drives)}"], dry_run=dry_run)


def add_hotspare(enc_slot: str, dg=None, controller: int = CONTROLLER, *,
                 dry_run: bool = False) -> tuple[bool, str]:
    """Add a drive as a hot spare: `perccli /cC/eE/sS add hotsparedrive [DGs=<dg>]`.

    dg=None -> global spare; dg=<n> -> dedicated to that drive group.
    """
    cmd = [_tool(), _pd(enc_slot, controller), "add", "hotsparedrive"]
    if dg is not None and str(dg) != "":
        cmd.append(f"DGs={dg}")
    return run_check(cmd, dry_run=dry_run)


def set_jbod(enc_slot: str, controller: int = CONTROLLER, *,
             dry_run: bool = False) -> tuple[bool, str]:
    """Expose a drive to the OS for software RAID/ZFS: `perccli /cC/eE/sS set jbod`.

    The drive leaves the controller's RAID management and appears as /dev/sdX.
    """
    return run_check([_tool(), _pd(enc_slot, controller), "set", "jbod"], dry_run=dry_run)


def del_vd(vd: int, controller: int = CONTROLLER, *,
           dry_run: bool = False) -> tuple[bool, str]:
    """Delete a virtual disk (DESTRUCTIVE): `perccli /cC/vV del force`."""
    return run_check([_tool(), f"/c{controller}/v{vd}", "del", "force"], dry_run=dry_run)
