"""b2ctl.backend — auto-detect and cache the right HBA/RAID backend.

Backends:
  ITBackend   -- wraps hba.py  (sas2ircu, IT/HBA mode)
  RaidBackend -- wraps hba_raid.py (perccli, RAID mode + smartctl -d megaraid)

Detection order (mode=auto):
  1. Try sas2ircu list   -> IT
  2. Try perccli show    -> RAID
  3. Neither: die with hint
"""
from __future__ import annotations

import re

from .common import Disk, run


class Backend:
    """Common interface for all HBA/RAID backends."""
    name: str = ""

    def have_tool(self) -> bool:
        return False

    def bay_map(self, controller: int | None = None) -> dict:
        return {}

    def attach_bays(self, disks: list, bm=None) -> None:
        pass

    def get_ghost_disks(self, disks: list, bm=None) -> list:
        return []

    def udev_rescue_ghost(self, serial: str) -> bool:
        return False

    def enumerate_disks(self) -> list:
        return []

    def raid_volumes(self) -> list:
        """Hardware RAID volumes (RAID backend only); [] for IT/HBA."""
        return []


# --------------------------------------------------------------------------- #
# IT-mode backend (wraps hba.py)
# --------------------------------------------------------------------------- #

class ITBackend(Backend):
    name = "it"

    def have_tool(self) -> bool:
        from . import hba
        return hba.have_sas2ircu()

    def _all_indices(self) -> list[int]:
        """Return controller indices per config."""
        from . import config as _cfg
        setting = _cfg.controller_index_setting()
        if setting == "all":
            return _detect_sas2ircu_controllers() or [0]
        return [int(setting)]

    def bay_map(self, controller: int | None = None) -> dict:
        from . import hba
        indices = [controller] if controller is not None else self._all_indices()
        result: dict = {}
        for idx in indices:
            result.update(hba.bay_map(idx))
        return result

    def attach_bays(self, disks: list, bm=None) -> None:
        from . import hba
        hba.attach_bays(disks, bm=bm)

    def get_ghost_disks(self, disks: list, bm=None) -> list:
        from . import hba
        return hba.get_ghost_disks(disks, bm=bm)

    def udev_rescue_ghost(self, serial: str) -> bool:
        from . import hba
        return hba.udev_rescue_ghost(serial)

    def enumerate_disks(self) -> list:
        from . import hba
        return hba.enumerate_disks()


# --------------------------------------------------------------------------- #
# RAID-mode backend (wraps hba_raid.py)
# --------------------------------------------------------------------------- #

class RaidBackend(Backend):
    name = "raid"

    def have_tool(self) -> bool:
        from . import hba_raid
        return hba_raid.have_tool()

    def bay_map(self, controller: int | None = None) -> dict:
        from . import hba_raid
        return hba_raid.bay_map(controller)

    def attach_bays(self, disks: list, bm=None) -> None:
        from . import hba_raid
        hba_raid.attach_bays(disks, bm=bm)

    def get_ghost_disks(self, disks: list, bm=None) -> list:
        from . import hba_raid
        return hba_raid.get_ghost_disks(disks, bm=bm)

    def udev_rescue_ghost(self, serial: str) -> bool:
        from . import hba_raid
        return hba_raid.udev_rescue_ghost(serial)

    def enumerate_disks(self) -> list:
        from . import hba_raid
        return hba_raid.enumerate_disks()

    def raid_volumes(self) -> list:
        from . import hba_raid
        return hba_raid.raid_volumes()


# --------------------------------------------------------------------------- #
# Detection + cache
# --------------------------------------------------------------------------- #

_backend_cache: Backend | None = None


def get_backend() -> Backend:
    global _backend_cache
    if _backend_cache is None:
        _backend_cache = _detect_backend()
    return _backend_cache


def _detect_backend() -> Backend:
    from . import config as _cfg
    from .common import die
    mode = _cfg.controller_mode()
    if mode == "it":
        return ITBackend()
    if mode == "raid":
        return RaidBackend()
    import os as _os, shutil as _shutil, sys as _sys
    # auto-detect: try sas2ircu first
    sas = _cfg.tool("sas2ircu")
    if run([sas, "list"]):
        return ITBackend()
    # Binary exists but can't execute? (32-bit sas2ircu needs libc6-i386)
    # Prefer IT-mode over RAID when the sas2ircu binary is present — on a
    # crossflashed PERC the box is IT/HBA even if perccli is also installed.
    _sas_path = _shutil.which(sas) or sas
    if _os.path.isfile(_sas_path):
        print(
            f"[!] sas2ircu found at {_sas_path} but failed to execute.\n"
            f"    Fix: apt-get install -y libc6-i386\n"
            f"    Forcing IT-mode — set controller.mode='it' in config to suppress.",
            file=_sys.stderr,
        )
        return ITBackend()
    # try perccli (the PERC RAID tool); a controller count > 0 means RAID mode
    from . import hba_raid
    if hba_raid.have_tool():
        return RaidBackend()
    die(
        "No HBA/RAID tool found. Install sas2ircu (IT/HBA mode) or "
        "perccli (RAID mode), or set tool_paths in "
        "/etc/b2ctl/config.json and set controller.mode to 'it' or 'raid'."
    )
    return ITBackend()  # unreachable -- die() exits


def _detect_sas2ircu_controllers() -> list[int]:
    """Parse `sas2ircu list` output for controller indices."""
    from . import config as _cfg
    out = run([_cfg.tool("sas2ircu"), "list"])
    return [int(m) for m in re.findall(r"^\s*(\d+)\s+SAS", out, re.MULTILINE)]
