"""b2ctl.burnin — second-hand-disk burn-in gate (read-only health vetting).

Runbook STEP 02: before a disk enters a pool, run a SMART long self-test and an
optional full read-surface scan, then judge it against fixed thresholds. Every
external call here is read-only or a self-test trigger — `badblocks` runs in
`-sv` (read) mode, NEVER `-w` (write/destructive).
"""
from __future__ import annotations

import re
import sys
import time

from . import config as _cfg
from .common import R, Y, G, C, N, run as _run, run_check


# Thresholds (from the hosting-platform runbook).
POH_WARN = 40000            # power-on hours: priority-down past this


def start_selftest(dev: str, kind: str = "long", *, dry_run: bool = False):
    """`smartctl -t long|short <dev>` — kicks off a background self-test."""
    sc = _cfg.tool("smartctl")
    return run_check([sc, "-t", kind, dev], dry_run=dry_run)


def selftest_status(dev: str, dtype: str = "") -> dict:
    """Parse `smartctl -a` self-test state -> {running, pct, result}.

    pct = percent COMPLETE (0..100). `running` False once the test finished;
    `result` is the human string (e.g. 'Completed without error') or "".
    """
    sc = _cfg.tool("smartctl")
    cmd = [sc, "-a"] + (["-d", dtype] if dtype else []) + [dev]
    out = _run(cmd)
    # ATA: "... in progress ... 40% of test remaining."
    m = re.search(r"(\d+)%\s+of\s+test\s+remaining", out, re.I)
    if m:
        return {"running": True, "pct": 100 - int(m.group(1)), "result": ""}
    # SAS: "Self test in progress ... 20% complete"
    m = re.search(r"Self[- ]test.*?(\d+)%\s+complete", out, re.I)
    if m:
        return {"running": True, "pct": int(m.group(1)), "result": ""}
    res = ""
    m = re.search(r"(?:completed without error|self-test routine in progress|"
                  r"completed:?\s*read failure|completed:?\s*[\w ]+)", out, re.I)
    if m:
        res = m.group(0).strip()
    return {"running": False, "pct": 100, "result": res}


def read_scan(dev: str, *, dry_run: bool = False):
    """Full read-only surface scan. `badblocks -sv -b 4096 <dev>` (NO -w)."""
    bb = _cfg.tool("badblocks")
    return run_check([bb, "-sv", "-b", "4096", dev], dry_run=dry_run, timeout=600)


def assess(d) -> tuple[str, list[str]]:
    """Judge a scanned Disk. Returns (verdict, reasons). FAIL > WARN > PASS."""
    reasons: list[str] = []
    verdict = "PASS"
    if d.health == "FAILED":
        verdict = "FAIL"; reasons.append("SMART health = FAILED")
    if d.uncorr and d.uncorr > 0:
        verdict = "FAIL"; reasons.append(f"uncorrected errors = {d.uncorr}")
    st = selftest_status(d.dev, d.smart_dtype)
    if st["result"] and "without error" not in st["result"].lower() \
            and "in progress" not in st["result"].lower():
        verdict = "FAIL"; reasons.append(f"self-test: {st['result']}")
    if verdict != "FAIL":
        if d.realloc and d.realloc > 0:
            verdict = "WARN"; reasons.append(f"grown defects/reallocated = {d.realloc}")
        if d.poh and d.poh > POH_WARN:
            verdict = "WARN"; reasons.append(f"power-on hours = {d.poh} (> {POH_WARN})")
    return verdict, reasons


def _wait_selftest(dev: str, dtype: str = "") -> bool:
    """Poll self-test progress and render a bar until it finishes."""
    try:
        while True:
            time.sleep(3)
            st = selftest_status(dev, dtype)
            pct = st["pct"]
            filled = int(pct // 5)
            bar = "#" * filled + "-" * (20 - filled)
            sys.stdout.write(f"\r{Y}  self-test {dev}: [{bar}] {pct:.0f}%{N}")
            sys.stdout.flush()
            if not st["running"]:
                sys.stdout.write(f"\r{G}  ✔ self-test finished on {dev}{' ' * 20}{N}\n")
                return True
    except KeyboardInterrupt:
        sys.stdout.write(f"\n{Y}  (stopped watching; self-test continues on the disk){N}\n")
        return False


def run(target, tbw_table: dict | None = None, *,
        do_scan: bool = False, kind: str = "long", dry_run: bool = False) -> int:
    """Guided burn-in: resolve disk -> long self-test -> optional read scan ->
    verdict. `target` is a Disk, or a bay/serial/dev string to resolve."""
    from . import core, spec
    d = target
    if isinstance(target, str):
        d = None
        for cand in core.scan(tbw_table if tbw_table is not None else spec.load()):
            if target in (cand.bay, cand.serial, cand.dev,
                          cand.dev.replace("/dev/", ""), cand.by_id):
                d = cand
                break
        if d is None:
            print(f"{R}[-] no disk matches '{target}'{N}")
            return 1
    if d.in_pool:
        print(f"{R}[-] {d.dev} is in pool '{d.pool}' — burn-in is for spare/new disks.{N}")
        return 1

    print(f"{C}Burn-in {d.dev} (bay {d.bay or '?'}) {d.model} ({d.serial}){N}")
    ok, out = start_selftest(d.dev, kind, dry_run=dry_run)
    if not ok:
        print(f"{R}[-] could not start self-test: {out}{N}")
        return 1
    if dry_run:
        print(f"{Y}[dry-run] would run {kind} self-test"
              + (" + read-surface scan" if do_scan else "") + f" on {d.dev}{N}")
        return 0
    _wait_selftest(d.dev, d.smart_dtype)
    if do_scan:
        print(f"{Y}  read-surface scan (badblocks, read-only) — may take hours...{N}")
        sok, sout = read_scan(d.dev)
        if not sok:
            print(f"{R}  ✗ read scan reported errors: {sout}{N}")

    # Re-read SMART after the test, then judge.
    from . import smart
    smart.read(d, tbw_table if tbw_table is not None else spec.load())
    verdict, reasons = assess(d)
    colour = {"PASS": G, "WARN": Y, "FAIL": R}[verdict]
    print(f"{colour}  [{verdict}] {d.dev}{N}")
    for r in reasons:
        print(f"    - {r}")
    if verdict == "PASS":
        print(f"{G}  ✔ safe to add to a pool.{N}")
    return 0 if verdict != "FAIL" else 1
