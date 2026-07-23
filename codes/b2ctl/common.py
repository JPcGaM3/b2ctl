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
# Health thresholds now live in config (health.ssd / health.hdd), type-aware and
# operator-tunable; a None threshold disables that check. See config._DEFAULTS.

# ---- dry-run flag (single owner at the bottom layer) ---------------------- #
# The mode used to live in watch.py, forcing action modules (raid_actions,
# burnin) to `import watch` — the interactive UI — just to read a flag (F-098).
# It lives here now; cli/watch set it, everyone reads via is_dry_run().
DRY_RUN = False


def set_dry_run(value: bool) -> None:
    global DRY_RUN
    DRY_RUN = bool(value)


def is_dry_run() -> bool:
    return DRY_RUN


def die(msg: str) -> None:
    print(f"{R}[-] {msg}{N}", file=sys.stderr)
    sys.exit(1)


def need_root() -> None:
    if os.geteuid() != 0:
        die("run as root (smartctl / sas2ircu / zpool need it): sudo b2ctl ...")


def run(args, timeout: int = 30, *, none_on_timeout: bool = False):
    """Run a command (list form, no shell) and return stdout ('' on failure).

    With none_on_timeout=True the caller opts in to a sentinel: a
    subprocess.TimeoutExpired returns None (distinguishable from '' for a
    nonzero exit / other error). Callers that do out.splitlines() unguarded
    (e.g. zfs.list_pools) MUST NOT set this — the default keeps the str contract
    (F-049)."""
    try:
        r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout)
        return r.stdout
    except subprocess.TimeoutExpired:
        return None if none_on_timeout else ""
    except Exception:
        return ""


def run_check(args, timeout: int | None = 120, *, op_id=None, dry_run: bool = False):
    """Run a state-changing command; return (ok, combined_output)."""
    # dry-run: suppress write cmds, pass read cmds through
    if dry_run:
        try:
            from . import safety as _safety
            # Match the basename so a config-resolved absolute tool path
            # (/usr/sbin/perccli64) is gated exactly like the bare name.
            is_write = bool(args) and os.path.basename(str(args[0])) in _safety.WRITE_CMDS
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


# ---- interactive prompts (shared; never raise on EOF / Ctrl-C) ------------ #
def ask(prompt: str) -> str:
    """Prompt for a line of input; return '' on EOF (Ctrl-D) or Ctrl-C."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def confirm(msg: str) -> bool:
    """Yes/No confirm; accepts 'y'/'yes' (case-insensitive). Default No.

    Returns False on EOF/Ctrl-C so an interrupted destructive prompt is a
    safe decline, never a traceback.
    """
    return ask(f"{msg} [y/N] ").lower() in ("y", "yes")


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
    ctrl_slot: str = ""            # raw controller enc:slot for perccli actions,
                                   # kept separate from the (possibly remapped) bay label
    ctrl: int | None = None        # perccli controller index this PD lives on;
                                   # None -> default (0). Actions target /c<ctrl> (F-085)
    # Burn-in self-test progress (transient). Set by smart.read() from the SAME
    # `smartctl -a` output it already fetches — no extra subprocess on the scan path.
    selftest_running: bool = False
    selftest_pct: int | None = None    # percent COMPLETE (0..100) of the running test
    selftest_eta: str = ""             # formatted time remaining, e.g. "~1h10m" or ""
    # Last COMPLETED extended (long) self-test, read passively from the SAME
    # `smartctl -a` self-test LOG (no extra subprocess). Indexed by power-on hours,
    # NOT wall-clock — the HEALTH_CHK column renders it POH-relative (v0.17.0).
    selftest_last_result: str = ""     # e.g. "Completed without error" or ""
    selftest_last_poh: int | None = None  # lifetime hours at which that test ran

    @property
    def in_pool(self) -> bool:
        return self.pool is not None

    @property
    def is_poolable(self) -> bool:
        """True if this disk may be handed to a ZFS mutation (wipe/add/replace).

        The single authority for the 'free, poolable disk' invariant that was
        copy-pasted across watch's assign/create/aux/offload filters (F-103). A
        HIDDEN PERC member shares the VD's /dev/sda (smart_dtype set) and MUST
        never reach `sgdisk --zap-all` — that would destroy the OS's hardware VD.
        Ghosts have dev == '-'.
        """
        return (not self.in_pool and self.dev != "-"
                and not self.smart_dtype and self.health != "GHOST")

    @property
    def is_spare(self) -> bool:
        # Only the pool's spares SECTION (vdev == "spares"), never a transient
        # spare-N/replacing-N sub-vdev — the FAULTED original leaf nested under
        # spare-1 during activation must stay a regular member so it renders red
        # and remains a [r]eplace/[s]wap candidate (F-074).
        return self.vdev == "spares"


# --------------------------------------------------------------------------- #
# Assessment — turn raw signals into a level + human reasons
# --------------------------------------------------------------------------- #
_BAD_VDEV = {"FAULTED", "UNAVAIL", "REMOVED", "OFFLINE", "DEGRADED"}


def _grade_high(value, warn, crit):
    """Grade a signal where a HIGHER value is worse (defect counts). A None
    threshold disables that band. Returns 'CRITICAL' / 'WARNING' / None."""
    if crit is not None and value > crit:
        return "CRITICAL"
    if warn is not None and value > warn:
        return "WARNING"
    return None


def _grade_low(value, warn, crit):
    """Grade a signal where a LOWER value is worse (endurance/wear % remaining).
    A None threshold disables that band. Returns 'CRITICAL' / 'WARNING' / None."""
    if crit is not None and value < crit:
        return "CRITICAL"
    if warn is not None and value < warn:
        return "WARNING"
    return None


def selftest_passed(result: str) -> bool:
    """True if a SMART self-test result string means SUCCESS.

    Handles both dialects: ATA success is 'Completed without error', SAS success
    is bare 'Completed' (no 'without error' suffix — the v0.17.0 bug that graded
    every healthy SAS disk as ERR/FAIL). Any fail/abort/interrupt/fatal/unknown
    token is a failure; an empty string is NOT a pass (callers treat '' as 'no
    test on record' before calling)."""
    low = (result or "").lower()
    if "without error" in low:           # ATA success (contains 'error', still a pass)
        return True
    if any(w in low for w in ("fail", "abort", "interrupt", "fatal", "unknown", "unable")):
        return False
    return "completed" in low            # SAS success = 'Completed'


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
    elif d.pd_state and d.smart_dtype:
        # A HIDDEN PERC physical drive (read via megaraid passthrough, shares the
        # VD's /dev/sdX): UGood/Failed/etc. — available, not a ghost. An EXPOSED
        # JBOD drive has its own block device (smart_dtype == "") and falls
        # through to the normal "unassigned" path below, so ZFS can pool it.
        st = d.pd_state.upper()
        if st in ("UGOOD", "READY", "UGUNSP"):
            bump("CONFIG", "available (Unconfigured Good) — set JBOD for ZFS, "
                           "or add to a RAID volume (raid-create)")
        elif st in ("OFFLN", "FAILED", "UBAD", "MISSING"):
            bump("CRITICAL", f"PD state={d.pd_state}")
        else:
            bump("CONFIG", f"PD state={d.pd_state}")
    elif not d.in_pool and not d.is_spare:
        bump("CONFIG", "unassigned (not in any pool — add to a pool or set as spare)")

    # SMART
    if not d.readable:
        bump("CRITICAL", "SMART unreadable")
    else:
        # Any readable health that is neither PASSED nor UNKNOWN (unparsed) is a
        # drive-declared failure/prediction — grade CRITICAL. Covers SAS
        # 'FAILURE PREDICTION THRESHOLD EXCEEDED' and future parse variants.
        if d.health not in ("PASSED", "UNKNOWN"):
            bump("CRITICAL", f"SMART health={d.health}")
        # Type-aware, config-tunable thresholds (health.ssd / health.hdd). SSD/NVMe
        # (is_ssd) stay strict (any bad sector CRITICAL); HDDs get tolerance bands.
        # A None threshold disables that check.
        from . import config as _cfg
        h = _cfg.health_config()["ssd" if d.is_ssd else "hdd"]
        for sig, val, label in (("realloc", d.realloc, "reallocated/defects"),
                                ("pending", d.pending, "pending sectors"),
                                ("uncorr", d.uncorr, "uncorrectable errors")):
            lvl = _grade_high(val, h.get(f"{sig}_warn"), h.get(f"{sig}_crit"))
            if lvl:
                bump(lvl, f"{label}={val}")
        if d.end_left is not None:
            lvl = _grade_low(d.end_left, h.get("endurance_warn"), h.get("endurance_crit"))
            if lvl:
                bump(lvl, f"endurance left {d.end_left:.1f}%")
        if d.wear_val is not None:
            lvl = _grade_low(d.wear_val, h.get("wear_warn"), h.get("wear_crit"))
            if lvl:
                bump(lvl, f"wear left {d.wear_val}%")

    d.level = level
    d.reasons = reasons
