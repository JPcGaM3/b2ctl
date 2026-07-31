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

import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

from . import config as _cfg
from .common import R, Y, G, C, N, run as _run, run_check, selftest_passed, warn


# Thresholds (from the hosting-platform runbook).
POH_WARN = 40000            # power-on hours: priority-down past this
POLL_SECS = 2.5             # live-view refresh cadence


# --------------------------------------------------------------------------- #
# Self-test: trigger + parse
# --------------------------------------------------------------------------- #
def _smart_target(d) -> str:
    """Device string to hand smartctl for this Disk. Mirrors smart.read()'s rule
    (smart.py): since v0.21.0 a PERC physical drive behind a virtual disk has NO
    OS device node (`Disk.dev == '-'`) — smartctl must instead open the per-VD
    megaraid ioctl handle in `Disk.ctrl_dev` via `-d <smart_dtype>`. burn-in used
    to build every smartctl command against `d.dev` regardless, which on a
    RAID-mode box meant literally addressing '-' (F-145)."""
    return d.ctrl_dev if d.smart_dtype and d.ctrl_dev else d.dev


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
    # PID-reuse guard (F-145): os.kill(pid, 0) succeeding only proves SOME
    # process owns that pid now, not that it is still OUR badblocks — across a
    # reboot or just a busy box, a reused pid would pin this health-check as
    # "running" forever. _is_our_badblocks answers False when it cannot verify
    # (no /proc, e.g. a macOS dev box) — the safe side here is "not running",
    # never claiming a process we can't identify.
    running = _pid_alive(pid) and _is_our_badblocks(pid, rec.get("dev", ""))
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
        dev = rec["dev"]                          # badblocks target (F-145: NOT smartctl's)
        smart_dev = rec.get("smart_dev") or dev    # falls back for pre-v0.19 state files
        dtype = rec.get("dtype", "")
        cmd = [sc, "-X"] + (["-d", dtype] if dtype else []) + [smart_dev]
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
        with _state_lock():
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
    """Judge a scanned Disk. Returns (verdict, reasons). FAIL > WARN > PASS.

    A disk that never answered SMART at all used to fall through every check
    below (all zero/None) and come out PASS — "safe to add to a pool" for a
    drive nothing was actually read from. common.assess() (the main status-table
    grader) already treats `not d.readable` as CRITICAL ('SMART unreadable');
    this is burn-in agreeing with it instead of being the one grader that
    ignores it (F-145). Decisive and checked first: nothing else here is
    meaningful about a drive that gave back no data."""
    if not d.readable or d.health == "NOREAD":
        return "FAIL", ["SMART did not answer (drive unreadable) — cannot vet this disk"]

    reasons: list[str] = []
    verdict = "PASS"
    if d.health == "FAILED":
        verdict = "FAIL"; reasons.append("SMART health = FAILED")
    if d.uncorr and d.uncorr > 0:
        verdict = "FAIL"; reasons.append(f"uncorrected errors = {d.uncorr}")
    st = selftest_status(_smart_target(d), d.smart_dtype)
    if st["result"]:
        if not selftest_passed(st["result"]):
            verdict = "FAIL"; reasons.append(f"self-test: {st['result']}")
    elif not st["running"]:
        # Empty result + nothing running: selftest_status cannot tell "never
        # tested" from "smartctl gave us nothing for the current test" here, so
        # this must not silently read as a pass either (F-145) — it just isn't
        # the same certainty as a completed test, hence WARN not FAIL.
        if verdict != "FAIL":
            verdict = "WARN"
        reasons.append("no self-test verdict available (empty result, none running)")
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
    """Best-effort atomic write; a burn-in still runs if state can't be saved.

    Uses a per-call unique temp name (`tempfile.mkstemp`) rather than a fixed
    `.tmp` path — two b2ctl processes (e.g. the web service alongside an operator)
    writing at once used to race on the SAME tmp file, so one process's
    `os.replace` could publish the OTHER's half-written/interleaved content
    (F-145). Still just one atomic `os.replace`; callers serialise the
    surrounding read-modify-write with `_state_lock()`."""
    d = _state_dir()
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".burnin-", suffix=".tmp", dir=d)
    except OSError:
        return
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(records, f, indent=2)
        os.replace(tmp, _state_path())
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


@contextlib.contextmanager
def _state_lock():
    """Advisory lock serialising burnin.json's read-modify-write across
    processes (F-145): run_multi / _finish / _cancel_records all do
    `load_state() -> mutate -> save_state()`, and with two b2ctl processes
    (an on-box service + an operator's own CLI, say) racing that window, the
    second writer's save clobbers the first's — orphaning a detached
    badblocks scan the state file no longer names.

    A lock FILE beside burnin.json (not the state file itself — save_state
    replaces that atomically). Degrades to running unlocked, with a warning,
    if the lock can't be taken: losing an update is bad, but a health-check
    that deadlocks or crashes because /var/log/b2ctl is unwritable is worse."""
    fh = None
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        fh = open(_state_path() + ".lock", "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError as e:
        warn(f"burn-in state lock unavailable ({e}) — proceeding without it")
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------- #
# Live view + orchestration
# --------------------------------------------------------------------------- #
def burnin_snapshot(records: list) -> list[dict]:
    """One poll of every record -> row dicts for ui.render_burnin_view."""
    rows = []
    for rec in records:
        # smart_dev (F-145): the smartctl target, which for a PERC PD differs
        # from rec["dev"] (badblocks' target / display identity, "-" when the
        # OS has no node for it). Falls back to "dev" for state files written
        # before this field existed.
        st = selftest_status(rec.get("smart_dev") or rec["dev"], rec.get("dtype", ""))
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


def _record_verdict(rec: dict, tbw) -> tuple[str, list[str], object]:
    """Full PASS/WARN/FAIL verdict for a COMPLETED record: re-scan the disk
    fresh (core.scan_one) and fold in the badblocks bad-block count. Shared by
    `_finish` (which also prunes state) and `status_payload` (read-only, no
    pruning) so the two can never disagree about what "done" means (F-145)."""
    from . import core
    # Resolve by SERIAL, not by dev: a PERC PD has dev == '-' and so does every
    # other one, so scan_one(dev) would grade this record from an arbitrary
    # neighbour's SMART (F-148). The record has carried the serial since v0.10.0.
    d = core.scan_one(rec["dev"], tbw, serial=rec.get("serial", ""))
    verdict, reasons = assess(d)
    if rec.get("do_scan"):
        bad = scan_progress(rec)["bad"]
        if bad and bad > 0:
            reasons = reasons + [f"read-surface scan: {bad} bad block(s)"]
            if verdict == "PASS":
                verdict = "WARN"
    return verdict, reasons, d


def _finish(records: list) -> None:
    """All burn-ins complete: print per-disk verdict, drop them from state."""
    from . import spec
    tbw = spec.load()
    for rec in records:
        verdict, reasons, d = _record_verdict(rec, tbw)
        colour = {"PASS": G, "WARN": Y, "FAIL": R}[verdict]
        print(f"{colour}  [{verdict}] bay {rec.get('bay') or '?'} {rec['dev']} "
              f"({d.serial or rec.get('serial', '')}){N}")
        for r in reasons:
            print(f"    - {r}")
        if verdict == "PASS":
            print(f"{G}    ✔ safe to add to a pool.{N}")
    # Composite (serial, dev) key (F-145): several enterprise SAS drives report
    # NO serial until SMART actually runs, so keying on serial alone made ONE
    # serial-less record's completion drop EVERY OTHER serial-less record too —
    # same fix as _cancel_records already had.
    done_keys = {(rec.get("serial"), rec.get("dev")) for rec in records}
    with _state_lock():
        save_state([r for r in load_state()
                    if (r.get("serial"), r.get("dev")) not in done_keys])


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

    # Locked for the whole load -> mutate -> save window (F-145), released
    # before live_view (which can run for hours and must not hold the state
    # file locked against another b2ctl process the whole time).
    with _state_lock():
        records = load_state()
        active_serials = {r.get("serial") for r in records}
        started_any = False
        for d in disks:
            target = _smart_target(d)          # F-145: smartctl's device, not d.dev
            # Re-entrancy: never restart a disk already under a self-test (F-011 spirit).
            st = selftest_status(target, d.smart_dtype)
            if st["running"] or d.serial in active_serials:
                print(f"{Y}  {d.dev} (bay {d.bay or '?'}) already under a self-test "
                      f"— reporting, not restarting.{N}")
                continue
            print(f"{C}Burn-in {d.dev} (bay {d.bay or '?'}) {d.model} ({d.serial}){N}")
            ok, out = start_selftest(target, kind, d.smart_dtype, dry_run=dry_run)
            if not ok:
                print(f"{R}[-] could not start self-test on {d.dev}: {out}{N}")
                continue
            rec = {"serial": d.serial, "dev": d.dev, "smart_dev": target, "bay": d.bay,
                   "dtype": d.smart_dtype, "kind": kind, "do_scan": do_scan,
                   "scan_pid": None, "scan_log": None, "started": _now()}
            if do_scan and not dry_run:
                # badblocks needs a real block device. A PERC PD behind a VD has
                # none (dev == '-') — scanning its ctrl_dev would read the whole
                # VIRTUAL DISK, i.e. the wrong media entirely. The firmware
                # self-test above still covers it (F-145).
                if d.dev in ("", "-"):
                    print(f"{Y}  no block device for bay {d.bay or '?'} — "
                          f"self-test only, surface scan skipped.{N}")
                else:
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
    if _unwatched():
        return 0
    live_view(records)
    return 0


def _unwatched() -> bool:
    """True when there is nobody at the terminal to render a live view for.

    burn-in has always been non-blocking BY DESIGN (ADR-002: it exits 0 once the
    tests are STARTED, and the verdict is read later from --status). The live
    view is the interactive convenience on top of that, and it is a `while True`
    redraw loop — under --json it would hang the request for the hours a long
    self-test takes, while the captured-stdout buffer grew without bound. Skip
    it and point at the pollable form instead (F-146).
    """
    from . import common
    if not (common.is_json_mode() or common.is_non_interactive()):
        return False
    if not common.is_json_mode():
        print(f"{Y}  started — not attaching the live view. Poll it with "
              f"`b2ctl maint health --status` or `b2ctl progress`.{N}")
    return True


def status_view() -> int:
    """Re-attach: show the live view / verdicts for any in-flight burn-ins."""
    records = load_state()
    if not records:
        print(f"{Y}  no burn-in in progress{N}")
        return 0
    if _unwatched():
        return 0
    live_view(records)
    return 0


def _poolable_target(d) -> bool:
    """A health-check target must be free/spare and genuinely reachable — never
    an in-pool member (the PASS/WARN/FAIL 'safe to add to a pool' verdict +
    surface scan are meaningless on an active member; self-test one directly
    with `smartctl -t long`), never a disk zpool never answered for, and never
    a hidden PERC member with no OS device node (`dev == '-'`) smartctl could
    be pointed at wrong (F-145).

    `d.is_poolable` is the single authority for "free disk ZFS may be handed"
    (F-103), but it is deliberately NOT the whole rule here. It excludes any disk
    with `smart_dtype` set, because ZFS must never `sgdisk --zap-all` a PERC
    member sharing the VD's block device — burn-in does no such thing. Vetting a
    PERC Unconfigured-Good drive before adding it to a volume is exactly what
    this verb is for, and `smartctl -t long -d megaraid,<DID> <ctrl_dev>` reads
    it fine (the same path the status table already uses). So the real gate is
    "do we have a device smartctl can open" — which is what `_smart_target`
    answers, and what `d.dev` alone could not (F-145)."""
    if d.is_poolable or (d.smart_dtype and _smart_target(d) not in ("", "-")
                         and not d.in_pool and d.pool_known
                         and d.health != "GHOST"):
        return True
    if d.in_pool:
        reason = f"is in pool '{d.pool}' — self-test it with `smartctl -t long` directly"
    elif not d.pool_known:
        reason = "pool membership is UNKNOWN (zpool did not answer) — fix ZFS, then re-run"
    elif d.health == "GHOST":
        reason = "is a ghost (seen before but not present now)"
    elif _smart_target(d) in ("", "-"):
        reason = ("has no device smartctl can open (no OS node, and no controller "
                  "handle for megaraid passthrough)")
    else:
        reason = "is not a free/poolable disk"
    print(f"{R}[-] maint health vets free/spare disks; {d.dev} {reason}.{N}")
    return False


def run(target, tbw_table: dict | None = None, *,
        do_scan: bool = False, kind: str = "long", dry_run: bool = False) -> int:
    """Single-disk burn-in — thin wrapper over run_multi([target])."""
    return run_multi([target], tbw_table, do_scan=do_scan, kind=kind, dry_run=dry_run)


def status_payload() -> list[dict]:
    """JSON-serialisable snapshot of every in-flight/completed burn-in record —
    the data behind an upcoming `b2ctl maint health --status --json`.

    Built from the SAME `burnin_snapshot()` the live terminal view renders, so
    the two can never show a different picture of what's running. PURE READ:
    unlike `status_view()`, this never starts, cancels, or prunes state, and it
    does one pass over the records (no polling loop) — a done record additionally
    gets its full PASS/WARN/FAIL verdict via the same `_record_verdict()` helper
    `_finish()` uses, just without `_finish()`'s state mutation."""
    from . import spec
    records = load_state()
    rows = burnin_snapshot(records)
    tbw = None
    payload = []
    for rec, row in zip(records, rows):
        entry = dict(row)
        entry["verdict"], entry["reasons"] = None, []
        if row["done"]:
            if tbw is None:
                tbw = spec.load()
            verdict, reasons, _d = _record_verdict(rec, tbw)
            entry["verdict"], entry["reasons"] = verdict, reasons
        payload.append(entry)
    return payload
