"""b2ctl.hba_raid — RAID-mode backend (storcli64 / perccli64).

Supports Dell PERC controllers in RAID mode. Both JBOD and RAID-array
virtual drives are enumerated. JBOD disks appear in lsblk as /dev/sdX;
virtual drives appear as a synthetic entry with dev="-" and health="RAID_VD".

Tested CLI: storcli64 / perccli64 (same syntax, different binary name).
"""
from __future__ import annotations

import glob
import json
import os
import re

from .common import Disk, run, run_check

CONTROLLER = 0
_TOOL_CANDIDATES = ("storcli64", "storcli", "perccli64", "perccli")


def _tool() -> str:
    """Return resolved path for the first available storcli/perccli binary."""
    from . import config as _cfg
    for name in _TOOL_CANDIDATES:
        t = _cfg.tool(name)
        if run([t, "show", "ctrlcount"]):
            return t
    # fallback to storcli64 (will fail with clear error if not present)
    return _cfg.tool("storcli64")


def have_tool() -> bool:
    """Return True if any storcli/perccli binary is functional."""
    from . import config as _cfg
    for name in _TOOL_CANDIDATES:
        t = _cfg.tool(name)
        if run([t, "show", "ctrlcount"]):
            return True
    return False


def _list_controllers() -> list[int]:
    """Return list of controller indices from `storcli show ctrlcount`."""
    t = _tool()
    out = run([t, "show", "ctrlcount"])
    m = re.search(r"Controller Count\s*=\s*(\d+)", out)
    count = int(m.group(1)) if m else 1
    return list(range(count))


def bay_map(controller: int | None = None) -> dict:
    """Return serial -> 'enc:slot' for all JBOD/RAID-array disks.

    Uses `storcli64 /c<n>/eall/sall show all` and parses the
    Drive Detailed Information section for SN and EID:Slt.
    """
    from . import config as _cfg
    t = _tool()
    indices: list[int]
    setting = _cfg.controller_index_setting()
    if controller is not None:
        indices = [controller]
    elif setting == "all":
        indices = _list_controllers()
    else:
        indices = [int(setting)]

    mapping: dict[str, str] = {}
    for idx in indices:
        out = run([t, f"/c{idx}/eall/sall", "show", "all"])
        _parse_bay_map(out, mapping)
    return mapping


def _parse_bay_map(text: str, mapping: dict) -> None:
    """Parse storcli `show all` output into {serial: 'enc:slot'}."""
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


def enumerate_disks() -> list[Disk]:
    """Return one Disk per physical JBOD block device (same as IT-mode)."""
    from . import hba
    # For JBOD disks, lsblk sees them just like IT-mode — reuse hba logic
    return hba.enumerate_disks()


def _load_bay_map_cfg() -> dict:
    """Load bay_map.json remapping config."""
    from . import config as _cfg
    path = _cfg.bay_map_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _remap(raw_bay: str, cfg: dict) -> str:
    """Same remap logic as hba.py."""
    if not cfg:
        return raw_bay
    table = cfg.get("map")
    if table and raw_bay in table:
        return table[raw_bay]
    if cfg.get("reverse_slots"):
        n = int(cfg.get("slots_per_enclosure", 8))
        try:
            enc, slot = raw_bay.split(":")
            return f"{enc}:{(n - 1) - int(slot)}"
        except (ValueError, AttributeError):
            return raw_bay
    return raw_bay


def attach_bays(disks: list[Disk], controller: int | None = None, bm=None) -> None:
    """Fill disk.bay from storcli, same algorithm as hba.attach_bays."""
    if not have_tool():
        return
    cfg = _load_bay_map_cfg()
    if bm is None:
        bm = bay_map(controller)
    for d in disks:
        if d.serial:
            if d.serial in bm:
                d.bay = _remap(bm[d.serial], cfg)
            else:
                for bm_serial, bay_val in bm.items():
                    if d.serial.startswith(bm_serial) or bm_serial.startswith(d.serial):
                        d.bay = _remap(bay_val, cfg)
                        break


def get_ghost_disks(disks: list[Disk], controller: int | None = None, bm=None) -> list[Disk]:
    """Find HBA-visible disks that the OS rejected (same algorithm as hba.py)."""
    if not have_tool():
        return []
    cfg = _load_bay_map_cfg()
    if bm is None:
        bm = bay_map(controller)
    os_serials = [d.serial for d in disks if d.serial]
    ghosts = []
    for serial, raw_bay in bm.items():
        matched = any(
            os_sn.startswith(serial) or serial.startswith(os_sn)
            for os_sn in os_serials
        )
        if not matched:
            d = Disk(dev="-")
            d.bay = _remap(raw_bay, cfg)
            d.serial = serial
            d.model = "(Ghost / OS Rejected)"
            d.health = "GHOST"
            d.level = "CRITICAL"
            d.reasons = ["OS_REJECTED"]
            ghosts.append(d)
    return ghosts


def udev_rescue_ghost(serial: str) -> bool:
    """Same udev rescue as IT-mode (sgX path is controller-independent)."""
    from . import hba
    return hba.udev_rescue_ghost(serial)


def locate(enc_slot: str, on: bool, controller: int = CONTROLLER) -> tuple[bool, str]:
    """Turn locate LED on/off via storcli.

    enc_slot format: 'enc:slot' e.g. '32:0'
    """
    t = _tool()
    enc, slot = enc_slot.split(":")
    action = "start" if on else "stop"
    return run_check([t, f"/c{controller}/e{enc}/s{slot}", "set", "locate", action])
