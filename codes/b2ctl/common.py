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
import re
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


# ---- JSON-mode warning collector (single owner at the bottom layer) ------- #
# b2ctl --json must put the JSON envelope alone on stdout: a stray print() from
# a read-path warning (spec.py/baymap.py) would corrupt the stream for the
# MCP/web client (F-139). Same shape/placement as the dry-run flag above.
JSON_MODE = False
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_pending_warnings: list[str] = []


def set_json_mode(value: bool) -> None:
    global JSON_MODE
    JSON_MODE = bool(value)


def is_json_mode() -> bool:
    return JSON_MODE


def warn(msg: str) -> None:
    """In JSON mode append to the pending list; otherwise print as today."""
    if JSON_MODE:
        plain = _ANSI_RE.sub("", msg)
        if plain not in _pending_warnings:     # F-139: dedup within one run
            _pending_warnings.append(plain)
    else:
        print(msg)


def take_warnings() -> list:
    """Return the pending JSON-mode warnings and clear them."""
    out = list(_pending_warnings)
    _pending_warnings.clear()
    return out


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
# Every prompt in b2ctl funnels through ask()/confirm() (watch._ask is the single
# input() in that module; raid_actions delegates here too), which makes this the
# ONE place a machine caller can be served without touching 90-odd call sites.
#
# `--confirm yes` / `--confirm <target>` switches the process non-interactive:
# confirms auto-approve, and a prompt that still has no answer raises
# NonInteractive rather than blocking an MCP/web client forever. §9 is preserved,
# not weakened: the operator still states intent explicitly, once per command,
# instead of once per prompt (ADR-007 phase 2).
AUTO_CONFIRM: str | None = None      # None = interactive


class NonInteractive(Exception):
    """A prompt was reached in --confirm mode with no answer available.

    Carries what was being asked so the CLI can tell the caller exactly which
    argument to supply. Guessing a default for an unanswered question on a
    destructive path is how you destroy the wrong pool, so this is deliberately
    an error, not a fallback.
    """

    def __init__(self, prompt: str, hint: str = ""):
        self.prompt = prompt.strip()
        self.hint = hint
        super().__init__(f"non-interactive: unanswered prompt {self.prompt!r}"
                         + (f" — supply {hint}" if hint else ""))


def set_auto_confirm(value: str | None) -> None:
    global AUTO_CONFIRM
    AUTO_CONFIRM = value


def is_non_interactive() -> bool:
    return AUTO_CONFIRM is not None


def ask(prompt: str, *, default: str | None = None, hint: str = "") -> str:
    """Prompt for a line of input; return '' on EOF (Ctrl-D) or Ctrl-C.

    In --confirm mode there is nobody to type: `default` is used when the caller
    supplied one, otherwise NonInteractive is raised naming the prompt.
    """
    if is_non_interactive():
        if default is not None:
            return default
        raise NonInteractive(prompt, hint)
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def confirm(msg: str) -> bool:
    """Yes/No confirm; accepts 'y'/'yes' (case-insensitive). Default No.

    Returns False on EOF/Ctrl-C so an interrupted destructive prompt is a
    safe decline, never a traceback. Auto-approves in --confirm mode.
    """
    if is_non_interactive():
        return True
    return ask(f"{msg} [y/N] ").lower() in ("y", "yes")


def confirm_target(prompt: str, target: str) -> bool:
    """The 'type the pool name to confirm' second gate, machine-callable.

    Interactive: unchanged — the operator must type `target` exactly.
    `--confirm yes`: satisfied.
    `--confirm <value>`: satisfied ONLY when <value> == target, so a caller that
    names what it is destroying cannot have that intent applied to a different
    pool by a mis-parsed command.
    """
    if is_non_interactive():
        return AUTO_CONFIRM in ("yes", target)
    return ask(prompt) == target


# --------------------------------------------------------------------------- #
# Disk model — one physical drive as seen on an HBA
# --------------------------------------------------------------------------- #
@dataclass
class Disk:
    dev: str                       # /dev/sdX, or "-" when the OS cannot see this
                                   # disk at all (a PERC PD behind a virtual disk,
                                   # or a ghost). Display identity ONLY — never a
                                   # SMART target for such a disk, see ctrl_dev.
    by_id: str = ""                # /dev/disk/by-id/ata-... (stable)
    bay: str | None = None         # enclosure:slot from sas2ircu, e.g. "1:4"
    size_bytes: int | None = None
    model: str = ""
    serial: str = ""
    wwn: str = ""                  # lsblk/controller WWN, e.g. "0x5000c500a1b2c3d4".
                                   # A serial-INDEPENDENT join key: enterprise SAS
                                   # drives report no SERIAL to lsblk until SMART
                                   # runs, which broke every serial-only PD match
                                   # against the OS's block devices (F-133).
    iface: str = ""                # SATA / SAS
    is_ssd: bool = True
    readable: bool = False         # SMART responded
    health: str = "UNKNOWN"        # PASSED / FAILED / NOREAD
    poh: int | None = None         # power-on hours
    wear_val: int | None = None    # SSD life remaining %, from SMART attr
    realloc: int = 0               # reallocated sectors / grown defects
    pending: int = 0
    uncorr: int = 0
    cmd_timeout: int = 0           # ATA attr 188 Command_Timeout. NOT a media
                                   # error — the command did not come back in
                                   # time, which points at the cable / backplane
                                   # / expander / power, not the platter. It used
                                   # to be folded into `uncorr`, so a cabling
                                   # fault was reported as lost data and sent the
                                   # operator to buy a disk (F-141). SAS/NVMe
                                   # never set this: their counters are genuine
                                   # uncorrectables.
    lba_written: int | None = None
    written_tb: float | None = None
    tbw_rating: float | None = None
    end_left: float | None = None  # Remaining rated write endurance %, PREFERRING
                                   # the drive's own indicator — the same value
                                   # iDRAC/OMSA reports as "Remaining Rated Write
                                   # Endurance". Falls back to the TBW estimate
                                   # when the drive reports nothing (F-142).
    end_source: str = ""           # "drive" | "spec" | "" (nothing to go on)
    end_left_spec: float | None = None   # the TBW-table estimate, computed
                                         # whenever possible so the two sources
                                         # stay comparable (write amplification
                                         # makes them drift apart over time)
    pool_token: str | None = None  # exact leaf token from `zpool status -P`, e.g. wwn-...-part1
    pool: str | None = None
    pool_known: bool = True        # did zpool actually ANSWER when we asked?
                                   # `pool is None` used to carry two meanings —
                                   # "this disk is free" and "we never found out" —
                                   # so a hung or missing zpool made every live
                                   # member look unassigned and offered the running
                                   # boot mirror up for wipe (F-143). core.scan()
                                   # clears this for every disk when zfs raises
                                   # ZfsUnavailable; is_poolable then refuses.
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
    pd_foreign: bool = False       # perccli DG column == "F": the drive carries a
                                   # FOREIGN config (RAID metadata from another
                                   # controller/array). An axis INDEPENDENT of
                                   # pd_state — a foreign drive still reads UGood,
                                   # but firmware refuses every transition (set
                                   # jbod / hotspare / add vd) with 'Operation not
                                   # allowed' until it is imported or cleared (F-135)
    ctrl_dev: str = ""             # megaraid ioctl HANDLE — the file smartctl opens
                                   # for `-d megaraid,<DID>`. Any block device on
                                   # the same controller works, so this is NOT this
                                   # disk's device node: a PD behind a VD has none.
                                   # Kept apart from `dev` because one field
                                   # carrying both meanings printed the same
                                   # /dev/sdX on every hardware row and made two
                                   # VDs resolve to one filesystem (F-136).
                                   # Set per-VD where the volume can be resolved.
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

        pool_known comes FIRST: 'not in a pool' is only a fact once zpool has
        answered. Without it an unanswered probe reads as 'nothing is in a pool',
        which is the most dangerous possible default here (F-143).
        """
        return (self.pool_known and not self.in_pool and self.dev != "-"
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
    elif d.pd_foreign:
        # A FOREIGN physical drive is locked by the controller, and stays locked
        # whether it is hidden behind the VD's block device or already exposed —
        # so this is tested BEFORE the smart_dtype (hidden) branch. Reported as
        # CONFIG, not CRITICAL: the drive is healthy, its configuration is what
        # blocks it. Without this, perccli's DG=F was invisible and b2ctl offered
        # a `set jbod` the firmware answers 'Operation not allowed' (F-135).
        bump("CONFIG", "FOREIGN config on this drive — the controller refuses "
                       "JBOD / hot-spare / volume-create until it is imported or "
                       "cleared (assign -> [5], or perccli /cN/fall)")
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
    elif not d.pool_known:
        # zpool never answered, so "not in a pool" was never established. Saying
        # "unassigned — add to a pool" here would tell the operator to do the one
        # thing b2ctl is now refusing, about a disk that may well be a live rpool
        # member. Name the real problem instead (F-143).
        bump("CONFIG", "pool membership UNKNOWN — zpool did not answer, so this "
                       "disk cannot be graded as free; fix ZFS, then re-run")
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
        # Kept out of the loop above so the reason can say what it actually
        # means: the loop only formats "label=value", and the whole point of
        # splitting 188 off is telling the operator to check a cable rather than
        # order a disk (F-141).
        lvl = _grade_high(d.cmd_timeout, h.get("cmdto_warn"), h.get("cmdto_crit"))
        if lvl:
            bump(lvl, f"command timeouts={d.cmd_timeout} — usually cabling / "
                      f"backplane / power, not the media")
        if d.end_left is not None:
            lvl = _grade_low(d.end_left, h.get("endurance_warn"), h.get("endurance_crit"))
            if lvl:
                bump(lvl, f"endurance left {d.end_left:.1f}%")
        # Only when it is a DIFFERENT number: once end_left is drive-sourced it
        # IS wear_val, and both bands default to 30/20, so grading both emitted
        # two near-identical reasons for one fact (F-142).
        if d.wear_val is not None and d.end_source != "drive":
            lvl = _grade_low(d.wear_val, h.get("wear_warn"), h.get("wear_crit"))
            if lvl:
                bump(lvl, f"wear left {d.wear_val}%")

    d.level = level
    d.reasons = reasons
