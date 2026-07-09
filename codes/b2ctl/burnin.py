"""b2ctl.burnin — second-hand-disk burn-in gate (read-only health vetting).

Runbook STEP 02: before a disk enters a pool, run a SMART long self-test and an
optional full read-surface scan, then judge it against fixed thresholds. Every
external call here is read-only or a self-test trigger — `badblocks` runs in
`-sv` (read) mode, NEVER `-w` (write/destructive).

Burn-in is **multi-disk and non-blocking** (v0.10.0): several disks are vetted at
once and the self-tests run on drive firmware while `badblocks` runs as a detached
host process. Progress is shown in a live per-disk view (self-test + scan bars +
ETA); leaving the view (Ctrl-C) keeps everything running and re-attachable via a
small state file (`burnin.json` under the audit dir) — see ADR-002.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

from . import config as _cfg
from .common import R, Y, G, C, N, run as _run, run_check, selftest_passed


# Thresholds (from the hosting-platform runbook).
POH_WARN = 40000            # power-on hours: priority-down past this
POLL_SECS = 2.5             # live-view refresh cadence


# --------------------------------------------------------------------------- #
# Self-test: trigger + parse
# --------------------------------------------------------------------------- #
def start_selftest(dev: str, kind: str = "long", dtype: str = "", *, dry_run: bool = False):
    """`smartctl -t long|short [-d <dtype>] <dev>` — kicks off a background test.

    dtype (e.g. 'megaraid,7') is required for RAID-mode passthrough; without it a
    self-test on the shared VD device either fails or addresses the wrong drive,
    yet selftest_status polls WITH -d — so the poll would read a stale log for a
    test that never ran (F-011)."""
    sc = _cfg.tool("smartctl")
    cmd = [sc, "-t", kind] + (["-d", dtype] if dtype else []) + [dev]
    return run_check(cmd, dry_run=dry_run)


def parse_selftest(out: str) -> dict:
    """Pure parser of a `smartctl -a` blob -> {running, pct, eta_min}.

    pct is percent COMPLETE (0..100). Shared by selftest_status() (the standalone
    poll) and smart.read() (the status-table path), so the status table costs NO
    extra subprocess — the self-test lines are already in the -a output smart.read
    fetches. ATA reports '% of test remaining'; SAS reports '% complete'."""
    m = re.search(r"(\d+)%\s+of\s+test\s+remaining", out, re.I)
    if m:
        pct, running = 100 - int(m.group(1)), True
    else:
        m = re.search(r"Self[- ]test.*?(\d+)%\s+complete", out, re.I)
        if m:
            pct, running = int(m.group(1)), True
        else:
            _res, running = _ata_exec_status(out)   # 'in progress' with no percent
            pct = 0 if running else 100
    eta = _selftest_eta_min(out, pct) if running else None
    return {"running": running, "pct": pct, "eta_min": eta}


def _selftest_eta_min(out: str, pct_complete: int) -> int | None:
    """Minutes remaining from the drive's recommended polling time (ATA only).

    `smartctl -a` includes the -c capabilities section, which carries
    'Extended self-test routine recommended polling time: (N) minutes'. A static
    estimate, but reliable — unlike the ZFS resilver ETA (CLAUDE.md §6). SAS/NVMe
    usually lack the line -> None (the view shows % without an ETA)."""
    # In real `smartctl` output the label spans two lines ("Extended self-test
    # routine\nrecommended polling time: ( N) minutes"), so bridge with \s+.
    m = re.search(r"Extended self-test routine\s+recommended polling time:\s*"
                  r"\(\s*(\d+)\s*\)\s*minutes", out, re.I)
    if not m:
        m = re.search(r"Short self-test routine\s+recommended polling time:\s*"
                      r"\(\s*(\d+)\s*\)\s*minutes", out, re.I)
    if not m:
        return None
    remaining = max(0, 100 - pct_complete)
    return int(round(int(m.group(1)) * remaining / 100.0))


def selftest_status(dev: str, dtype: str = "") -> dict:
    """Poll `smartctl -a` -> {running, pct, result, eta_min}.

    Only the CURRENT test's state is read: ATA from the 'Self-test execution
    status' block, SAS from the newest self-test log row. The full output is
    never scanned for a stale HISTORICAL log entry (a previous owner's passing
    test) that would mask a current abort and yield a false burn-in PASS (F-030).
    """
    sc = _cfg.tool("smartctl")
    out = _run([sc, "-a"] + (["-d", dtype] if dtype else []) + [dev])
    st = parse_selftest(out)
    if st["running"]:
        return {"running": True, "pct": st["pct"], "result": "", "eta_min": st["eta_min"]}
    res, _ = _ata_exec_status(out)
    if res is None:                       # no ATA header -> SAS log table
        res = _sas_selftest_result(out)
    return {"running": False, "pct": 100, "result": res or "", "eta_min": None}


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
    """Newest SAS self-test log row (# 1) STATUS column, or '' if none.

    SAS columns are 2+-space separated: '# N  <test-desc>  <status>  <segment>
    <lifetime> ...'. Take the status column (index 1 after dropping '# N') so the
    result is a clean 'Completed' / 'Aborted (by user command)' — NOT the greedy
    old capture that swallowed the trailing '- 41724 -'. selftest_passed() then
    grades it (SAS success is bare 'Completed')."""
    for line in out.splitlines():
        if not re.match(r"#\s*\d+\s", line):
            continue
        if "in progress" in line.lower():
            return ""
        body = re.sub(r"^#\s*\d+\s+", "", line.strip())   # drop the '# N' index
        cols = re.split(r"\s{2,}", body)                  # 2+-space columns
        return cols[1].strip() if len(cols) >= 2 else body.strip()
    return ""


# --------------------------------------------------------------------------- #
# Surface scan: badblocks as a detached host process
# --------------------------------------------------------------------------- #
def start_scan(dev: str, serial: str = "", *, dry_run: bool = False):
    """Spawn `badblocks -sv -b 4096 <dev>` (read-only, NO -w) as a detached process.

    Returns (pid, logfile) — or (None, "") on dry-run / failure. badblocks writes
    'NN.NN% done' to stderr, captured to a logfile so a later/other b2ctl process
    can tail-parse progress. start_new_session detaches it so Ctrl-C in the live
    view leaves it running (the whole point). No -w: a full 1 TB read takes hours,
    but it never writes to the disk (F-012)."""
    bb = _cfg.tool("badblocks")
    if dry_run:
        return None, ""
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        log = _scan_log_path(serial or dev)
        fh = open(log, "wb")
    except OSError as e:
        print(f"{R}  could not open scan log for {dev}: {e}{N}")
        return None, ""
    try:
        p = subprocess.Popen([bb, "-sv", "-b", "4096", dev],
                             stdout=subprocess.DEVNULL, stderr=fh,
                             start_new_session=True)
    finally:
        fh.close()                        # the child holds its own dup of the fd
    return p.pid, log


def scan_progress(rec: dict) -> dict:
    """Read a record's badblocks progress -> {pct, eta_min, running, bad}.

    ETA is computed from OUR OWN elapsed time (not badblocks' version-dependent
    output): remaining = elapsed * (100-pct)/pct."""
    pid, log = rec.get("scan_pid"), rec.get("scan_log")
    if not pid or not log:
        return {"pct": None, "eta_min": None, "running": False, "bad": 0}
    running = _pid_alive(pid)
    pct, bad = _parse_badblocks_log(log)
    eta = None
    if running and pct and pct > 0:
        elapsed_min = (_now() - float(rec.get("started") or _now())) / 60.0
        eta = int(round(elapsed_min * (100 - pct) / pct))
    return {"pct": pct, "eta_min": eta, "running": running, "bad": bad}


def _parse_badblocks_log(log: str):
    """Last 'NN.NN% done' + total error count from a badblocks stderr logfile."""
    try:
        with open(log, "rb") as f:
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None, 0
    pcts = re.findall(r"([\d.]+)%\s+done", data)
    pct = int(float(pcts[-1])) if pcts else None
    errs = re.findall(r"\((\d+)/(\d+)/(\d+)\s+errors\)", data)
    bad = sum(int(x) for x in errs[-1]) if errs else 0
    return pct, bad


def _pid_alive(pid: int) -> bool:
    """True if pid is a live process. Reaps our own finished children so a zombie
    (which os.kill(pid,0) would still report as 'existing') reads as done."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                       # exists, owned by another user
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            return False                  # was our zombie child, now reaped
    except ChildProcessError:
        pass                              # not our child (re-attach case)
    return True


# --------------------------------------------------------------------------- #
# Cancel: abort self-test + kill badblocks + drop from state
# --------------------------------------------------------------------------- #
def _is_our_badblocks(pid: int, dev: str) -> bool:
    """Guard against PID reuse: only SIGTERM a pid whose /proc cmdline is really
    our `badblocks <dev>` scan. Returns False when /proc is unavailable — never
    kill a pid we cannot verify."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return False
    return (any(b"badblocks" in a for a in argv)
            and any(dev.encode() in a for a in argv))


def _cancel_records(recs: list, *, dry_run: bool = False) -> int:
    """Abort the self-test (`smartctl -X`) and kill the badblocks scan for each
    record, then drop them from state. Returns 0 if any were cancelled, else 1."""
    if not recs:
        return 1
    sc = _cfg.tool("smartctl")
    for rec in recs:
        dev = rec["dev"]
        dtype = rec.get("dtype", "")
        cmd = [sc, "-X"] + (["-d", dtype] if dtype else []) + [dev]
        if dry_run:
            print(f"[DRY-RUN] would run: {' '.join(cmd)}")
        else:
            run_check(cmd)                # abort self-test; harmless if already done
        pid = rec.get("scan_pid")
        if pid and _pid_alive(pid) and _is_our_badblocks(pid, dev):
            if dry_run:
                print(f"[DRY-RUN] would SIGTERM badblocks pid {pid} ({dev})")
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        print(f"{Y}  [cancelled] bay {rec.get('bay') or '?'} {dev} "
              f"({rec.get('serial') or '?'}){N}")
    if not dry_run:
        keys = {(r.get("serial"), r["dev"]) for r in recs}
        save_state([r for r in load_state()
                    if (r.get("serial"), r.get("dev")) not in keys])
    return 0


def cancel(targets: list, *, dry_run: bool = False) -> int:
    """Cancel in-flight burn-in(s) matching bay / serial / dev string(s)."""
    records = load_state()
    if not records:
        print(f"{Y}  no burn-in in progress{N}")
        return 1
    matched: list = []
    for t in targets:
        m = next((r for r in records if t in (
                    r.get("bay"), r.get("serial"), r.get("dev"),
                    (r.get("dev") or "").replace("/dev/", ""))), None)
        if m is None:
            print(f"{R}[-] no in-flight burn-in matches '{t}'{N}")
        elif m not in matched:
            matched.append(m)
    if not matched:
        return 1
    return _cancel_records(matched, dry_run=dry_run)


def cancel_all(*, dry_run: bool = False) -> int:
    """Cancel every in-flight burn-in."""
    records = load_state()
    if not records:
        print(f"{Y}  no burn-in in progress{N}")
        return 1
    return _cancel_records(records, dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #
def assess(d) -> tuple[str, list[str]]:
    """Judge a scanned Disk. Returns (verdict, reasons). FAIL > WARN > PASS."""
    reasons: list[str] = []
    verdict = "PASS"
    if d.health == "FAILED":
        verdict = "FAIL"; reasons.append("SMART health = FAILED")
    if d.uncorr and d.uncorr > 0:
        verdict = "FAIL"; reasons.append(f"uncorrected errors = {d.uncorr}")
    st = selftest_status(d.dev, d.smart_dtype)
    if st["result"] and not selftest_passed(st["result"]):
        verdict = "FAIL"; reasons.append(f"self-test: {st['result']}")
    if verdict != "FAIL":
        if d.realloc and d.realloc > 0:
            verdict = "WARN"; reasons.append(f"grown defects/reallocated = {d.realloc}")
        # POH warning is opt-in via config (health.<type>.poh_warn); None = off.
        poh_warn = _cfg.health_config()["ssd" if d.is_ssd else "hdd"].get("poh_warn")
        if poh_warn is not None and d.poh and d.poh > poh_warn:
            verdict = "WARN"; reasons.append(f"power-on hours = {d.poh} (> {poh_warn})")
    return verdict, reasons


# --------------------------------------------------------------------------- #
# State file (re-attach) — lives beside the safety audit log so sim's
# `safety.LOG_DIR` monkeypatch redirects it to sim/var (ADR-002).
# --------------------------------------------------------------------------- #
def _state_dir() -> str:
    from . import safety                  # read at call time -> inherits sim redirect
    return safety.LOG_DIR


def _state_path() -> str:
    return os.path.join(_state_dir(), "burnin.json")


def _scan_log_path(key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key or "disk")
    return os.path.join(_state_dir(), f"scan-{safe}.log")


def load_state() -> list:
    try:
        with open(_state_path()) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_state(records: list) -> None:
    """Best-effort atomic write; a burn-in still runs if state can't be saved."""
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        path = _state_path()
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(records, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Live view + orchestration
# --------------------------------------------------------------------------- #
def burnin_snapshot(records: list) -> list[dict]:
    """One poll of every record -> row dicts for ui.render_burnin_view."""
    rows = []
    for rec in records:
        st = selftest_status(rec["dev"], rec.get("dtype", ""))
        row = {
            "bay": rec.get("bay"), "dev": rec["dev"], "serial": rec.get("serial", ""),
            "st_running": st["running"], "st_pct": st["pct"], "st_eta": st.get("eta_min"),
            "do_scan": bool(rec.get("do_scan")),
        }
        if rec.get("do_scan"):
            sc = scan_progress(rec)
            row.update(sc_running=sc["running"], sc_pct=sc["pct"],
                       sc_eta=sc["eta_min"], sc_bad=sc["bad"])
        else:
            row.update(sc_running=False, sc_pct=None, sc_eta=None, sc_bad=0)
        row["done"] = (not row["st_running"]) and (not row["sc_running"])
        rows.append(row)
    return rows


def live_view(records: list, *, sleep=None) -> None:
    """Redraw per-disk self-test + scan progress until all done or Ctrl-C.

    Ctrl-C DETACHES (saves state, leaves everything running) — it does not abort."""
    from . import ui
    _sleep = sleep or time.sleep
    if not records:
        print(f"{Y}  no burn-in in progress{N}")
        return
    print(f"{C}  live burn-in — Ctrl-C to leave running in background{N}")
    prev_lines = 0
    try:
        while True:
            rows = burnin_snapshot(records)
            block = ui.render_burnin_view(rows)
            if prev_lines:
                sys.stdout.write(f"\033[{prev_lines}F\033[J")   # up + clear-to-end
            sys.stdout.write(block + "\n")
            sys.stdout.flush()
            prev_lines = block.count("\n") + 1
            if all(r["done"] for r in rows):
                _finish(records)
                return
            _sleep(POLL_SECS)
    except KeyboardInterrupt:
        save_state(records)
        print(f"\n{Y}  left running in background — "
              f"`b2ctl maint health --status` to re-attach{N}")


def _finish(records: list) -> None:
    """All burn-ins complete: print per-disk verdict, drop them from state."""
    from . import core, spec
    tbw = spec.load()
    for rec in records:
        d = core.scan_one(rec["dev"], tbw)
        verdict, reasons = assess(d)
        if rec.get("do_scan"):
            bad = scan_progress(rec)["bad"]
            if bad and bad > 0:
                reasons = reasons + [f"read-surface scan: {bad} bad block(s)"]
                if verdict == "PASS":
                    verdict = "WARN"
        colour = {"PASS": G, "WARN": Y, "FAIL": R}[verdict]
        print(f"{colour}  [{verdict}] bay {rec.get('bay') or '?'} {rec['dev']} "
              f"({d.serial or rec.get('serial', '')}){N}")
        for r in reasons:
            print(f"    - {r}")
        if verdict == "PASS":
            print(f"{G}    ✔ safe to add to a pool.{N}")
    done_serials = {rec.get("serial") for rec in records}
    save_state([r for r in load_state() if r.get("serial") not in done_serials])


def _resolve_targets(targets: list, tbw) -> list:
    """Resolve a mix of Disk objects and bay/serial/dev strings to Disks (1 scan)."""
    from . import core
    disks = [t for t in targets if not isinstance(t, str)]
    strs = [t for t in targets if isinstance(t, str)]
    if strs:
        alld = core.scan(tbw)
        for t in strs:
            m = next((c for c in alld if t in (c.bay, c.serial, c.dev,
                      c.dev.replace("/dev/", ""), c.by_id)), None)
            if m is None:
                print(f"{R}[-] no disk matches '{t}'{N}")
            else:
                disks.append(m)
    return disks


def run_multi(targets, tbw_table: dict | None = None, *,
              do_scan: bool = False, kind: str = "long", dry_run: bool = False) -> int:
    """Start a long self-test (+ optional surface scan) on every selected disk,
    then show the live progress view. Non-blocking: leaving keeps them running."""
    from . import spec
    tbw = tbw_table if tbw_table is not None else spec.load()
    disks = _resolve_targets(list(targets), tbw)
    disks = [d for d in disks if _poolable_target(d)]
    if not disks:
        return 1

    records = load_state()
    active_serials = {r.get("serial") for r in records}
    started_any = False
    for d in disks:
        # Re-entrancy: never restart a disk already under a self-test (F-011 spirit).
        st = selftest_status(d.dev, d.smart_dtype)
        if st["running"] or d.serial in active_serials:
            print(f"{Y}  {d.dev} (bay {d.bay or '?'}) already under a self-test "
                  f"— reporting, not restarting.{N}")
            continue
        print(f"{C}Burn-in {d.dev} (bay {d.bay or '?'}) {d.model} ({d.serial}){N}")
        ok, out = start_selftest(d.dev, kind, d.smart_dtype, dry_run=dry_run)
        if not ok:
            print(f"{R}[-] could not start self-test on {d.dev}: {out}{N}")
            continue
        rec = {"serial": d.serial, "dev": d.dev, "bay": d.bay,
               "dtype": d.smart_dtype, "kind": kind, "do_scan": do_scan,
               "scan_pid": None, "scan_log": None, "started": _now()}
        if do_scan and not dry_run:
            rec["scan_pid"], rec["scan_log"] = start_scan(d.dev, d.serial)
        records.append(rec)
        active_serials.add(d.serial)
        started_any = True

    if dry_run:
        print(f"{Y}[dry-run] would burn-in {len(disks)} disk(s)"
              + (" + read-surface scan" if do_scan else "") + f" ({kind} self-test){N}")
        return 0
    if not records:
        return 1
    if started_any or records:
        save_state(records)
    live_view(records)
    return 0


def status_view() -> int:
    """Re-attach: show the live view / verdicts for any in-flight burn-ins."""
    records = load_state()
    if not records:
        print(f"{Y}  no burn-in in progress{N}")
        return 0
    live_view(records)
    return 0


def _poolable_target(d) -> bool:
    """A health-check target must be a free disk, never an in-pool member (the
    PASS/WARN/FAIL 'safe to add to a pool' verdict + surface scan are meaningless
    on an active member). To self-test a member, run `smartctl -t long` directly."""
    if d.in_pool:
        print(f"{R}[-] maint health vets free/spare disks; {d.dev} is in pool "
              f"'{d.pool}' — self-test it with `smartctl -t long` directly.{N}")
        return False
    return True


def run(target, tbw_table: dict | None = None, *,
        do_scan: bool = False, kind: str = "long", dry_run: bool = False) -> int:
    """Single-disk burn-in — thin wrapper over run_multi([target])."""
    return run_multi([target], tbw_table, do_scan=do_scan, kind=kind, dry_run=dry_run)
