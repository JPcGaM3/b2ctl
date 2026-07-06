"""b2ctl.locate — find a physical disk by its LED, addressed by DEVICE.

The Dell-12G-on-LSI-IT-mode backplane reports scrambled slot numbers, and
``sas2ircu ... LOCATE <slot>`` lights a whole range of bays instead of one, so we
never address the LED by slot. Backend chain, most-dedicated first:
  * PERC VD member / UGood (`is_perc_pd`) -> perccli `start/stop locate` by
    enc:slot ONLY. No /dev-based fallback: a member shares /dev/sda, so ledctl/dd
    there would light the whole VD (wrong bay).
  * raw disk (own /dev node)              -> ledctl (SGPIO/SES dedicated locate
    LED) if installed, else dd activity read (READ ONLY, if=dev of=/dev/null).

`ledctl locate` is an SES *identify blink*, not a solid LED — it only toggles the
locate indicator on/off. Default blink is ~5 seconds, then it stops.
"""

from __future__ import annotations
import subprocess
import time

from .common import run_check
from . import config as _cfg

DEFAULT_SECONDS = 5
_DEVNULL = subprocess.DEVNULL


def _dd_read(dev: str, seconds: int) -> bool:
    """Sequential read for `seconds` -> activity LED flickers. Read-only.

    Returns True only if the read sustained the full duration (TimeoutExpired,
    which is the SUCCESS case here). A dd that exits early or a missing binary
    means the LED never lit, so blink() must report that, not a false 'done'
    (F-046)."""
    try:
        subprocess.run([_cfg.tool("dd"), f"if={dev}", "of=/dev/null", "bs=1M", "iflag=direct"],
                       stdout=_DEVNULL, stderr=_DEVNULL, timeout=seconds)
        return False  # exited before the timeout -> couldn't sustain the read
    except subprocess.TimeoutExpired:
        return True   # ran the full duration -> LED was active
    except OSError:
        return False  # dd missing / device gone


def _ledctl(dev: str, on: bool) -> tuple[bool, str]:
    """Toggle the dedicated locate LED via ledctl (SGPIO/SES). LED-only, safe."""
    verb = "locate" if on else "locate_off"
    return run_check([_cfg.tool("ledctl"), f"{verb}={dev}"])


def _have_ledctl() -> bool:
    import shutil
    return shutil.which(_cfg.tool("ledctl")) is not None


def blink(dev: str, seconds: int = DEFAULT_SECONDS) -> tuple[bool, str]:
    """Blink one raw disk for `seconds`, then stop. Returns (ok, method).

    Prefers ledctl (dedicated locate LED) and falls back to the dd activity read
    when ledctl is absent or cannot drive the device. The LED is ALWAYS left off
    at the end.
    """
    if _have_ledctl():
        ok, _ = _ledctl(dev, True)          # light + support probe
        if ok:
            try:
                time.sleep(seconds)
            finally:
                _ledctl(dev, False)         # ALWAYS leave it off
            return True, "ledctl"
        # ledctl present but couldn't drive this dev -> safe fallback
    ok = _dd_read(dev, seconds)
    return ok, "dd"


def is_perc_pd(disk) -> bool:
    """True if this Disk is a PERC physical drive (member OR Unconfigured-Good).

    Such disks share the VD block device (/dev/sdX) and are addressed by their
    enc:slot bay, not by a block device. `pd_state` is set for every perccli PD
    (members 'Onln', spares 'UGood', etc.); `array_type=='HW'` for VD members.
    """
    return bool(disk.bay) and (getattr(disk, "array_type", "") == "HW"
                               or bool(getattr(disk, "pd_state", "")))


def is_resilvering(disk) -> bool:
    """True when lighting this disk's LED is unsafe because it is actively
    rebuilding/resilvering (CLAUDE.md §9: never light an LED on a resilvering
    disk). A FAULTED/UNAVAIL/OFFLINE/REMOVED leaf is the legitimate pull target
    even mid-resilver, so it returns False.

    Triggers on (a) a PERC PD in Rbld/Rebuild, or (b) a healthy leaf nested in an
    active replacing-*/spare-* sub-vdev while its pool's resilver is not complete.
    """
    if (getattr(disk, "pd_state", "") or "").upper() in ("RBLD", "REBUILD"):
        return True
    vdev = getattr(disk, "vdev", "") or ""
    state = (getattr(disk, "vdev_state", "") or "").upper()
    if (vdev.startswith("replacing") or vdev.startswith("spare")) and \
            state not in ("FAULTED", "UNAVAIL", "OFFLINE", "REMOVED"):
        pool = getattr(disk, "pool", None)
        if pool:
            from . import zfs
            if not zfs.poll_resilver_status(pool).get("completed", True):
                return True
    return False


def blink_disk(disk, seconds: int = DEFAULT_SECONDS, *, force: bool = False) -> tuple[bool, str]:
    """Blink a Disk's bay LED, routed by backend.

    PERC physical drives (VD members and Unconfigured-Good spares) have no
    per-member block device — they share the VD's /dev/sdX — so a dd/ledctl read
    would blink the wrong bay. Light the slot LED via perccli (by enc:slot) only.
    Everything else uses ledctl (else dd) on the device.

    Refuses a resilvering/rebuilding disk unless force=True (used only by the
    post-resilver 'pull this bay' prompt).
    """
    if not force and is_resilvering(disk):
        return False, "resilvering"
    if is_perc_pd(disk):
        from . import hba_raid
        cs = getattr(disk, "ctrl_slot", "") or disk.bay   # raw enc:slot, not the display bay (F-016)
        ctrl = getattr(disk, "ctrl", None)                # target this PD's controller (F-085)
        ctrl = ctrl if ctrl is not None else hba_raid.CONTROLLER
        ok, _ = hba_raid.locate(cs, True, ctrl)
        if ok:
            try:
                time.sleep(seconds)
            finally:
                hba_raid.locate(cs, False, ctrl)   # never leave the LED latched on (F-047)
        return ok, "perccli"
    return blink(disk.dev, seconds)

# blink_many (a raw dd fan-out over device paths) was removed: it dd-read the
# shared PERC VD device, blinking the wrong bay (F-017). `status --locate` now
# routes each at-risk disk through blink_disk (perccli by enc:slot for PERC PDs,
# ledctl/dd for raw disks) via a thread pool — see cli._status.
