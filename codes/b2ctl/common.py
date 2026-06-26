"""b2ctl.common — shared primitives for both backends.

Colours, external-command execution, the Disk model, and the health-level
assessment. No other b2ctl module depends on anything above this one.

Two backends share this model: IT/HBA (crossflashed PERC → LSI SAS2308, raw
disks, SMART direct, LEDs via sas2ircu) and RAID (Dell PERC via perccli, member
SMART via `smartctl -d megaraid`). The Disk model carries both ZFS membership
(pool/vdev) and hardware-RAID fields (array_type/array_name/smart_dtype/did/
pd_state); a HW member is treated as 'assigned' and graded by its PERC PD state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field

# ---- colours (kept identical to the reference script's palette) ---------- #
R = "\033[1;31m"; Y = "\033[1;33m"; G = "\033[1;32m"
C = "\033[1;36m"; B = "\033[1;34m"; N = "\033[0m"

# ---- health levels -------------------------------------------------------- #
RANK = {"NORMAL": 0, "CONFIG": 1, "WARNING": 2, "CRITICAL": 3}
LEVEL_COLOR = {"CRITICAL": R, "WARNING": Y, "CONFIG": C, "NORMAL": G}

# endurance / wear thresholds (percent remaining)
END_WARN = 30
END_CRIT = 10


def die(msg: str) -> None:
    print(f"{R}[-] {msg}{N}", file=sys.stderr)
    sys.exit(1)


def need_root() -> None:
    if os.geteuid() != 0:
        die("run as root (smartctl / sas2ircu / zpool need it): sudo b2ctl ...")


def run(args, timeout: int = 30) -> str:
    """Run a command (list form, no shell) and return stdout ('' on failure)."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def run_check(args, timeout: int = 120, *, op_id=None, dry_run: bool = False):
    """Run a state-changing command; return (ok, combined_output)."""
    # dry-run: suppress write cmds, pass read cmds through
    if dry_run:
        try:
            from . import safety as _safety
            is_write = bool(args) and args[0] in _safety.WRITE_CMDS
        except ImportError:
            is_write = False
        if is_write:
            print(f"[DRY-RUN] would run: {' '.join(str(a) for a in args)}")
            return True, ""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# Disk model — one physical drive as seen on an HBA
# --------------------------------------------------------------------------- #
@dataclass
class Disk:
    dev: str                       # /dev/sdX
    by_id: str = ""                # /dev/disk/by-id/ata-... (stable)
    bay: str | None = None         # enclosure:slot from sas2ircu, e.g. "1:4"
    size_bytes: int | None = None
    model: str = ""
    serial: str = ""
    iface: str = ""                # SATA / SAS
    is_ssd: bool = True
    readable: bool = False         # SMART responded
    health: str = "UNKNOWN"        # PASSED / FAILED / NOREAD
    poh: int | None = None         # power-on hours
    wear_val: int | None = None    # SSD life remaining %, from SMART attr
    realloc: int = 0               # reallocated sectors / grown defects
    pending: int = 0
    uncorr: int = 0
    lba_written: int | None = None
    written_tb: float | None = None
    tbw_rating: float | None = None
    end_left: float | None = None  # TBW-based endurance remaining %
    pool_token: str | None = None  # exact leaf token from `zpool status -P`, e.g. wwn-...-part1
    pool: str | None = None
    vdev: str | None = None
    vdev_state: str | None = None  # ONLINE / DEGRADED / FAULTED / AVAIL ...
    level: str = "NORMAL"
    reasons: list = field(default_factory=list)
    spare_replacing: str | None = None
    # RAID-mode (PERC) fields — empty/None on IT-mode so existing behaviour is unchanged
    array_type: str = ""           # "HW" (PERC VD member) | "SW" (derived from pool) | ""
    array_name: str = ""           # HW only, e.g. "vd0/raid1"
    smart_dtype: str = ""          # smartctl -d arg, e.g. "megaraid,7"
    did: int | None = None         # megaraid device id
    pd_state: str = ""             # perccli PD state: Onln/Rbld/JBOD/UGood/Failed

    @property
    def in_pool(self) -> bool:
        return self.pool is not None

    @property
    def is_spare(self) -> bool:
        return self.vdev is not None and "spare" in self.vdev


# --------------------------------------------------------------------------- #
# Assessment — turn raw signals into a level + human reasons
# --------------------------------------------------------------------------- #
_BAD_VDEV = {"FAULTED", "UNAVAIL", "REMOVED", "OFFLINE", "DEGRADED"}


def assess(d: Disk) -> None:
    """Set d.level and d.reasons from its SMART + ZFS state."""
    level = "NORMAL"
    reasons: list[str] = []

    def bump(newlvl: str, msg: str) -> None:
        nonlocal level
        if RANK[newlvl] > RANK[level]:
            level = newlvl
        reasons.append(msg)

    # ZFS membership state
    if d.vdev_state and d.vdev_state.upper() in _BAD_VDEV:
        sev = "WARNING" if d.vdev_state.upper() == "DEGRADED" else "CRITICAL"
        bump(sev, f"vdev state={d.vdev_state}")
    elif d.array_type == "HW":
        # Hardware-RAID member: the controller owns it, so it's "assigned".
        # Level follows the PERC physical-drive state, not pool membership.
        st = (d.pd_state or "").upper()
        if st and st not in ("ONLN", "ONLINE", "OPTL", "OPTIMAL"):
            sev = "WARNING" if st in ("RBLD", "REBUILD") else "CRITICAL"
            bump(sev, f"PD state={d.pd_state}")
    elif not d.in_pool and not d.is_spare:
        bump("CONFIG", "unassigned (not in any pool — add to a pool or set as spare)")

    # SMART
    if not d.readable:
        bump("CRITICAL", "SMART unreadable")
    else:
        if d.health == "FAILED":
            bump("CRITICAL", "SMART health=FAILED")
        if d.realloc > 0:
            bump("CRITICAL", f"reallocated/defects={d.realloc}")
        if d.pending > 0:
            bump("CRITICAL", f"pending sectors={d.pending}")
        if d.uncorr > 0:
            bump("CRITICAL", f"uncorrectable errors={d.uncorr}")
        # endurance via TBW
        if d.end_left is not None:
            if d.end_left < END_CRIT:
                bump("CRITICAL", f"endurance left {d.end_left:.1f}%")
            elif d.end_left < END_WARN:
                bump("WARNING", f"endurance left {d.end_left:.1f}%")
        # wear via SMART normalized value
        if d.wear_val is not None:
            if d.wear_val < END_CRIT:
                bump("CRITICAL", f"wear left {d.wear_val}%")
            elif d.wear_val < END_WARN:
                bump("WARNING", f"wear left {d.wear_val}%")

    d.level = level
    d.reasons = reasons
