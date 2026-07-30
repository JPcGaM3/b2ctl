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
    """OS-owned disks: lsblk enumeration + direct smartctl, ZFS lifecycle.

    `bay_source` selects who answers "which bay is this drive in":

      'sas2ircu' — a crossflashed LSI IT HBA (R620 H710 -> SAS2308).
      'perccli'  — a Dell HBA330/H330, or a PERC in HBA-Mode. sas2ircu speaks
                   SAS2 only and is blind to the SAS3 chip, but perccli manages
                   the card fine. The disks are still raw /dev/sdX, so ONLY the
                   bay map moves to perccli — enumeration and SMART stay IT-style
                   (F-133). LEDs need no special-casing: every drive has its own
                   block device, so locate's ledctl/dd path blinks the right bay.
    """
    name = "it"

    def __init__(self, bay_source: str = "sas2ircu"):
        self.bay_source = bay_source

    def have_tool(self) -> bool:
        from . import hba, hba_raid
        if self.bay_source == "perccli":
            return hba_raid.have_tool()
        if hba.have_sas2ircu():
            return True
        # F-133: an operator who forced controller.mode='it' on an HBA330 would
        # otherwise lose bays entirely — sas2ircu cannot see a SAS3 card. Flip to
        # perccli as the bay source instead of reporting "no tool". Both probes
        # are memoized, so this costs nothing after the first scan.
        if hba_raid.have_tool():
            self.bay_source = "perccli"
            return True
        return False

    def _all_indices(self) -> list[int]:
        """Return controller indices per config."""
        from . import config as _cfg
        setting = _cfg.controller_index_setting()
        if setting == "all":
            return _detect_sas2ircu_controllers() or [0]
        try:
            return [int(setting)]
        except (TypeError, ValueError):
            # F-027: a malformed controller.index must fall back to defaults, not
            # crash every scan (config's 'malformed -> defaults' contract).
            return _detect_sas2ircu_controllers() or [0]

    def bay_map(self, controller: int | None = None) -> dict:
        if self.bay_source == "perccli":
            from . import hba_raid
            return hba_raid.bay_map(controller)
        from . import hba
        indices = [controller] if controller is not None else self._all_indices()
        result: dict = {}
        for idx in indices:
            result.update(hba.bay_map(idx))
        return result

    def attach_bays(self, disks: list, bm=None) -> None:
        from . import hba
        enc = None
        if self.bay_source == "perccli":
            from . import hba_raid
            if bm is None:
                bm = self.bay_map()      # never let hba probe sas2ircu here
            if not bm:
                # perccli printed no per-drive detail section, so there is no
                # serial map to label with. The sysfs fallback still knows every
                # slot; borrow perccli's enclosure number so the labels match
                # what `perccli /cN show` prints (F-134).
                encs = hba_raid.enclosure_ids()
                enc = encs[0] if len(encs) == 1 else None
        hba.attach_bays(disks, bm=bm, enc_hint=enc)

    def get_ghost_disks(self, disks: list, bm=None) -> list:
        from . import hba
        if bm is None and self.bay_source == "perccli":
            bm = self.bay_map()
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
    # auto-detect: sas2ircu must report an ACTUAL controller, not merely print
    # output — sas2ircu on a RAID box prints its banner + 'MPTLib2 Error 1' to
    # stdout, which the old truthy test misread as IT-mode (F-010).
    sas = _cfg.tool("sas2ircu")
    out = run([sas, "list"])
    if re.findall(r"^\s*(\d+)\s+SAS", out, re.MULTILINE):
        return ITBackend()                       # a controller table => IT/HBA
    if not out.strip():
        # No output at all: the binary is absent, or present-but-can't-execute
        # (32-bit sas2ircu needs libc6-i386). Present -> force IT (crossflashed
        # PERC boxes are IT/HBA even if perccli is also installed).
        _sas_path = _shutil.which(sas) or sas
        if _os.path.isfile(_sas_path):
            print(
                f"[!] sas2ircu found at {_sas_path} but failed to execute.\n"
                f"    Fix: apt-get install -y libc6-i386\n"
                f"    Forcing IT-mode — set controller.mode='it' in config to suppress.",
                file=_sys.stderr,
            )
            return ITBackend()
    # sas2ircu ran but reported zero controllers -> perccli. Answering perccli is
    # NOT the same as being a RAID controller: a Dell HBA330/H330 (SAS3008, IT
    # firmware) answers it too while handing raw disks straight to the OS. Ask
    # who owns the storage before picking a backend — treating an HBA330 as RAID
    # duplicated every drive and killed its SMART (F-133).
    from . import hba_raid
    if hba_raid.have_tool():
        if hba_raid.is_hba_personality():
            return ITBackend(bay_source="perccli")
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
