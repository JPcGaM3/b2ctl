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


def start_selftest(dev: str, kind: str = "long", dtype: str = "", *, dry_run: bool = False):
    """`smartctl -t long|short [-d <dtype>] <dev>` — kicks off a background test.

    dtype (e.g. 'megaraid,7') is required for RAID-mode passthrough; without it a
    self-test on the shared VD device either fails or addresses the wrong drive,
    yet selftest_status/_wait_selftest poll WITH -d — so the poll would read a
    stale log for a test that never ran (F-011)."""
    sc = _cfg.tool("smartctl")
    cmd = [sc, "-t", kind] + (["-d", dtype] if dtype else []) + [dev]
    return run_check(cmd, dry_run=dry_run)


def selftest_status(dev: str, dtype: str = "") -> dict:
    """Parse `smartctl -a` self-test state -> {running, pct, result}.

    pct = percent COMPLETE (0..100). `running` False once the test finished;
    `result` is the human string (e.g. 'Completed without error') or "".

    Only the CURRENT test's state is read: ATA from the 'Self-test execution
    status' block, SAS from the newest self-test log row. The full output is
    never scanned, or a stale HISTORICAL log entry (a previous owner's passing
    test) would mask a current abort and yield a false burn-in PASS (F-030).
    """
    sc = _cfg.tool("smartctl")
    cmd = [sc, "-a"] + (["-d", dtype] if dtype else []) + [dev]
    out = _run(cmd)
    # in-progress: ATA "... 40% of test remaining." / SAS "... 20% complete"
    m = re.search(r"(\d+)%\s+of\s+test\s+remaining", out, re.I)
    if m:
        return {"running": True, "pct": 100 - int(m.group(1)), "result": ""}
    m = re.search(r"Self[- ]test.*?(\d+)%\s+complete", out, re.I)
    if m:
        return {"running": True, "pct": int(m.group(1)), "result": ""}
    res, running = _ata_exec_status(out)
    if res is None:                       # no ATA header -> SAS log table
        res = _sas_selftest_result(out)
    if running:
        return {"running": True, "pct": 0, "result": ""}
    return {"running": False, "pct": 100, "result": res or ""}


_ABORT_WORDS = ("aborted", "interrupted", "fatal", "failure", "failed")


def _ata_exec_status(out: str):
    """Parse the ATA 'Self-test execution status' block only.

    Returns (result, still_running). result is None when no such header exists
    (the drive is SAS). An aborted/interrupted/fatal current test returns a
    NON-EMPTY string so assess() grades it FAIL instead of silently passing.
    """
    m = re.search(r"Self-test execution status:(.*?)(?:\n\s*\n|\nSMART )",
                  out, re.S | re.I)
    if not m:
        return None, False
    block = " ".join(m.group(1).split())          # collapse wrapped lines
    low = block.lower()
    if "in progress" in low:
        return "", True
    if any(w in low for w in _ABORT_WORDS):
        return block, False                        # non-empty -> FAIL
    if "without error" in low:
        return "Completed without error", False
    return (block or "unknown self-test state"), False


def _sas_selftest_result(out: str) -> str:
    """Newest SAS self-test log row (# 1) status, or '' if none."""
    for line in out.splitlines():
        if re.match(r"#\s*\d+\s", line):
            low = line.lower()
            if "in progress" in low:
                return ""
            m = re.search(r"(completed[\w ,:-]*|aborted[\w ,:-]*|failed[\w ,:-]*)"
                          r"(?:\s{2,}|$)", line, re.I)
            return m.group(1).strip() if m else line.strip()
    return ""


def read_scan(dev: str, *, dry_run: bool = False):
    """Full read-only surface scan. `badblocks -sv -b 4096 <dev>` (NO -w).

    No deadline: a full 1 TB read takes hours, so the old 600 s timeout killed
    every real scan and then misreported the abort as disk errors (F-012)."""
    bb = _cfg.tool("badblocks")
    return run_check([bb, "-sv", "-b", "4096", dev], dry_run=dry_run, timeout=None)


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
    # Pass the megaraid dtype so a RAID-mode passthrough self-test actually starts
    # (and matches the poll's -d) — F-011.
    ok, out = start_selftest(d.dev, kind, d.smart_dtype, dry_run=dry_run)
    if not ok:
        print(f"{R}[-] could not start self-test: {out}{N}")
        return 1
    if dry_run:
        print(f"{Y}[dry-run] would run {kind} self-test"
              + (" + read-surface scan" if do_scan else "") + f" on {d.dev}{N}")
        return 0
    _wait_selftest(d.dev, d.smart_dtype)
    scan_failed = False
    if do_scan:
        print(f"{Y}  read-surface scan (badblocks, read-only) — may take hours...{N}")
        sok, sout = read_scan(d.dev)
        if not sok:
            scan_failed = True
            print(f"{R}  ✗ read-surface scan failed / found bad sectors: {sout}{N}")

    # Re-read SMART after the test, then judge.
    from . import smart
    smart.read(d, tbw_table if tbw_table is not None else spec.load())
    verdict, reasons = assess(d)
    if scan_failed:
        # A failed surface scan must not be a silent 'PASS' — fold it into the
        # verdict (F-012). It cannot upgrade a FAIL, only add a reason/WARN.
        reasons = reasons + ["read-surface scan failed or reported bad sectors"]
        if verdict == "PASS":
            verdict = "WARN"
    colour = {"PASS": G, "WARN": Y, "FAIL": R}[verdict]
    print(f"{colour}  [{verdict}] {d.dev}{N}")
    for r in reasons:
        print(f"    - {r}")
    if verdict == "PASS":
        print(f"{G}  ✔ safe to add to a pool.{N}")
    return 0 if verdict != "FAIL" else 1
