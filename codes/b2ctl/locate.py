"""b2ctl.locate — find a physical disk by its LED, addressed by DEVICE.

The Dell-12G-on-LSI-IT-mode backplane reports scrambled slot numbers, and
``sas2ircu ... LOCATE <slot>`` lights a whole range of bays instead of one. So
we never address the LED by slot. We use dd activity read: a universal fallback
where a few seconds of sequential READ makes the bay's activity LED flicker.
READ ONLY (if=dev of=/dev/null).

Default blink is ~5 seconds, then it stops on its own.
"""

from __future__ import annotations
import subprocess

from .common import run_check

DEFAULT_SECONDS = 5
_DEVNULL = subprocess.DEVNULL





def _dd_read(dev: str, seconds: int) -> None:
    """Sequential read for `seconds` -> activity LED flickers. Read-only."""
    try:
        subprocess.run(["dd", f"if={dev}", "of=/dev/null", "bs=1M", "iflag=direct"],
                       stdout=_DEVNULL, stderr=_DEVNULL, timeout=seconds)
    except subprocess.TimeoutExpired:
        pass  # expected: we ran for the full duration then stopped


def blink(dev: str, seconds: int = DEFAULT_SECONDS) -> tuple[bool, str]:
    """Blink one disk for `seconds`, then stop. Returns (ok, method)."""
    _dd_read(dev, seconds)
    return True, "dd"


def blink_disk(disk, seconds: int = DEFAULT_SECONDS) -> tuple[bool, str]:
    """Blink a Disk's bay LED, routed by backend.

    HW-RAID members have no per-member block device (they share the VD's
    /dev/sdX), so a dd read would blink the wrong thing — light the slot LED via
    perccli (by enc:slot) instead. Everything else uses the dd activity read.
    """
    if getattr(disk, "array_type", "") == "HW" and disk.bay:
        import time
        from . import hba_raid
        ok, _ = hba_raid.locate(disk.bay, True)
        if ok:
            time.sleep(seconds)
            hba_raid.locate(disk.bay, False)
        return ok, "perccli"
    return blink(disk.dev, seconds)


def blink_many(devs: list[str], seconds: int = DEFAULT_SECONDS) -> str:
    """Blink several disks at once for `seconds`, then stop."""
    import time
    procs = [subprocess.Popen(["dd", f"if={d}", "of=/dev/null", "bs=1M", "iflag=direct"],
                              stdout=_DEVNULL, stderr=_DEVNULL) for d in devs]
    time.sleep(seconds)
    for p in procs:
        p.kill()
    for p in procs:
        p.wait()
    return "dd"
