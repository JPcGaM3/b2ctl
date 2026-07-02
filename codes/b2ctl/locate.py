"""b2ctl.locate — find a physical disk by its LED, addressed by DEVICE.

The Dell-12G-on-LSI-IT-mode backplane reports scrambled slot numbers, and
``sas2ircu ... LOCATE <slot>`` lights a whole range of bays instead of one, so we
never address the LED by slot. Backend chain, most-dedicated first:
  * PERC VD member / UGood (`is_perc_pd`) -> perccli `start/stop locate` by
    enc:slot ONLY. No /dev-based fallback: a member shares /dev/sda, so ledctl/dd
    there would light the whole VD (wrong bay).
  * raw disk (own /dev node)              -> ledctl (SGPIO/SES dedicated locate
    LED) if installed, else dd activity read (READ ONLY, if=dev of=/dev/null).

`ledctl locate` is an SES *identify blink*, not a solid LED — no tool makes a
healthy drive's LED solid or dark; it only toggles the locate indicator on/off.
Default blink is ~5 seconds, then it stops. An optional pulse (on/off seconds)
beats the LED in a distinct rhythm for the whole duration.
"""

from __future__ import annotations
import subprocess
import time

from .common import run_check
from . import config as _cfg

DEFAULT_SECONDS = 5
_DEVNULL = subprocess.DEVNULL


def _pulse(total: float, on: float, off: float, active, idle) -> None:
    """Alternate active(dur)/idle(dur) for `total` seconds, clamped to the end.

    `active`/`idle` each take a duration; active drives the LED (read / locate on),
    idle leaves it dark. Durations are trimmed so the loop never overruns `total`.
    """
    end = time.monotonic() + total
    while True:
        rem = end - time.monotonic()
        if rem <= 0:
            break
        active(min(on, rem))
        rem = end - time.monotonic()
        if rem <= 0:
            break
        idle(min(off, rem))

def _dd_read(dev: str, seconds: int) -> None:
    """Sequential read for `seconds` -> activity LED flickers. Read-only."""
    try:
        subprocess.run(["dd", f"if={dev}", "of=/dev/null", "bs=1M", "iflag=direct"],
                       stdout=_DEVNULL, stderr=_DEVNULL, timeout=seconds)
    except subprocess.TimeoutExpired:
        pass  # expected: we ran for the full duration then stopped


def _ledctl(dev: str, on: bool) -> tuple[bool, str]:
    """Toggle the dedicated locate LED via ledctl (SGPIO/SES). LED-only, safe."""
    verb = "locate" if on else "locate_off"
    return run_check([_cfg.tool("ledctl"), f"{verb}={dev}"])


def _have_ledctl() -> bool:
    import shutil
    return shutil.which(_cfg.tool("ledctl")) is not None


def _blink_dd(dev: str, seconds: int, on: float, off: float) -> None:
    """dd activity-LED locate (fallback). on/off -> pulse of read/idle."""
    if on > 0 and off > 0:
        _pulse(seconds, on, off, lambda d: _dd_read(dev, d), time.sleep)
    else:
        _dd_read(dev, seconds)


def blink(dev: str, seconds: int = DEFAULT_SECONDS,
          on: float = 0, off: float = 0) -> tuple[bool, str]:
    """Blink one raw disk for `seconds`, then stop. Returns (ok, method).

    Prefers ledctl (dedicated locate LED, clean on/off) and falls back to the dd
    activity read when ledctl is absent or cannot drive the device. on>0 and off>0
    pulse the LED (on seconds lit, off seconds dark); else steady on for the
    duration. The LED is ALWAYS left off at the end.
    """
    if _have_ledctl():
        ok, _ = _ledctl(dev, True)          # probe + would-be first "on"
        if ok:
            try:
                if on > 0 and off > 0:
                    _ledctl(dev, False)     # reset; _pulse drives clean cycles

                    def _active(d):
                        _ledctl(dev, True)
                        time.sleep(d)
                        _ledctl(dev, False)

                    _pulse(seconds, on, off, _active, time.sleep)
                else:
                    time.sleep(seconds)     # LED already on from the probe
            finally:
                _ledctl(dev, False)         # ALWAYS leave it off
            return True, "ledctl"
        # ledctl present but couldn't drive this dev -> safe fallback
    _blink_dd(dev, seconds, on, off)
    return True, "dd"


def is_perc_pd(disk) -> bool:
    """True if this Disk is a PERC physical drive (member OR Unconfigured-Good).

    Such disks share the VD block device (/dev/sdX) and are addressed by their
    enc:slot bay, not by a block device. `pd_state` is set for every perccli PD
    (members 'Onln', spares 'UGood', etc.); `array_type=='HW'` for VD members.
    """
    return bool(disk.bay) and (getattr(disk, "array_type", "") == "HW"
                               or bool(getattr(disk, "pd_state", "")))


def blink_disk(disk, seconds: int = DEFAULT_SECONDS,
               on: float = 0, off: float = 0) -> tuple[bool, str]:
    """Blink a Disk's bay LED, routed by backend.

    PERC physical drives (VD members and Unconfigured-Good spares) have no
    per-member block device — they share the VD's /dev/sdX — so a dd read would
    blink the wrong bay. Light the slot LED via perccli (by enc:slot). Everything
    else uses the dd activity read on the device.

    on>0 and off>0 pulse the LED (on seconds lit, off seconds dark) for the whole
    duration; otherwise the LED stays lit steadily for `seconds`.
    """
    if is_perc_pd(disk):
        from . import hba_raid
        if on > 0 and off > 0:
            state = {"ok": True}

            def _active(d):
                ok, _ = hba_raid.locate(disk.bay, True)
                state["ok"] = ok
                time.sleep(d)
                hba_raid.locate(disk.bay, False)

            _pulse(seconds, on, off, _active, time.sleep)
            return state["ok"], "perccli"
        ok, _ = hba_raid.locate(disk.bay, True)
        if ok:
            time.sleep(seconds)
            hba_raid.locate(disk.bay, False)
        return ok, "perccli"
    return blink(disk.dev, seconds, on, off)


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
