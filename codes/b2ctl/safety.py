"""b2ctl.safety — audit trail, snapshots, rollback hints, post-op verification."""
from __future__ import annotations

import json
import os
from datetime import datetime

from .common import run_check, R, Y, G, N

LOG_DIR  = "/var/log/b2ctl"
SNAP_DIR = "/var/log/b2ctl/snapshots"
LOG_FILE = "/var/log/b2ctl/ops.jsonl"

# Commands whose basename is considered a write op (suppressed in dry-run).
# Matched by os.path.basename in common.run_check, so config-resolved absolute
# paths (e.g. /usr/sbin/perccli64) and bare names both hit. perccli/smartctl/
# badblocks mutate hardware (set offline/missing, -t self-test, surface scan) and
# are threaded through run_check with dry_run=, so they MUST be listed here or a
# --dry-run preview executes them for real (RAID-mode replace/destroy, burn-in).
WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd",
              "perccli", "perccli64", "smartctl", "badblocks",
              "systemctl"}      # enable/disable maintenance timers (read subcmds use run())

# Rollback hint table: op -> lambda(entry) -> hint_str or None.
# The 'replace' hint prefers the named old_dev/new_dev fields recorded at
# begin_op time (F-091); it only falls back to positional cmd indices for
# entries written before those fields existed. Never index into cmds when the
# named fields are present — a future cmd-shape change (extra flag) would move
# the positions and produce a plausible-but-wrong executable rollback.
_ROLLBACK: dict = {
    "offline":   lambda e: f"zpool online {e['pool']} {e['dev_path']}",
    "add_spare": lambda e: f"zpool remove {e['pool']} {e['dev_path']}",
    "replace":   lambda e: (
        f"zpool replace {e['pool']} {e['new_dev']} {e['old_dev']}"
        if e.get("new_dev") and e.get("old_dev")
        else f"zpool replace {e['pool']} {e['cmds'][0][5]} {e['cmds'][0][4]}"
        if e.get("cmds") and len(e["cmds"][0]) > 5
        else f"zpool replace {e['pool']} <new-disk> {e['dev_path']}"
    ),
    "demote":    lambda e: (
        f"zpool attach {e['pool']} <remaining-member> {e['dev_path']}"
    ),
    "create":    lambda e: f"zpool destroy {e['pool']}  # WARNING: destroys all data",
    "aux-repair": lambda e: (
        f"aux vdev repair on {e['pool']}: verify `zpool status {e['pool']}` — "
        f"cache loss is harmless, a SLOG mirror keeps redundancy; no auto-rollback"
    ),
}
_NO_ROLLBACK = {"wipefs", "sgdisk", "wipe"}

# In-memory copy of every op's entry, keyed by op_id (F-092). end_op falls back
# to this when the on-disk log is unwritable (disk full / read-only /var) so the
# op result, rollback hint and post-op verification still run.
_PENDING: dict = {}
_log_warned = False


def begin_op(
    op: str,
    serial: str,
    bay,
    dev_path: str,
    pool: str,
    vdev: str,
    cmds: list[list[str]],
    *,
    details: dict | None = None,
    dry_run: bool = False,
) -> str:
    """Write pending audit entry + snapshot. Return op_id.

    `details` may carry named fields (currently old_dev/new_dev for replace) so
    rollback hints are built from names, not fragile positional cmd indices.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(SNAP_DIR, exist_ok=True)
    except OSError:
        pass
    now = datetime.now()
    op_id = now.strftime("%Y%m%d-%H%M%S") + f"-{now.microsecond:06d}-{op}"
    entry = {
        "op_id":       op_id,
        "op":          op,
        "disk_serial": serial,
        "disk_bay":    bay,
        "dev_path":    dev_path,
        "pool":        pool,
        "vdev":        vdev,
        "cmds":        cmds,
        "old_dev":     None,
        "new_dev":     None,
        "status":      "pending",
        "exit_code":   None,
        "stdout":      "",
        "stderr":      "",
        "started_at":  now.isoformat(timespec="seconds"),
        "ended_at":    None,
        "rollback_hint": None,
        "snapshot_path": None,
    }
    if details:
        for k in ("old_dev", "new_dev"):
            if details.get(k):
                entry[k] = details[k]
    # dry-run is a pure preview — don't write a pre-op snapshot to disk.
    snap_path = None if dry_run else _capture_snapshot(op_id, pool, dev_path)
    if snap_path:
        entry["snapshot_path"] = snap_path
    _PENDING[op_id] = entry
    _append_jsonl(entry)
    return op_id


def end_op(op_id: str, success: bool, stdout: str, stderr: str, exit_code: int,
           *, dry_run: bool = False) -> None:
    """Update audit entry status + print rollback hint.

    Falls back to the in-memory pending entry when the on-disk log is
    unwritable, so the operator still gets the result, hint and post-op
    verification even on a full/read-only root (F-092). Appends an 'end' record
    rather than rewriting the whole file (F-093) — load merges begin+end by
    op_id (last record wins).
    """
    entry = _load_entry(op_id) or _PENDING.get(op_id)
    if entry is None:
        return
    entry["status"]    = "dry_run" if dry_run else ("ok" if success else "fail")
    entry["exit_code"] = exit_code
    entry["stdout"]    = stdout
    entry["stderr"]    = stderr
    entry["ended_at"]  = datetime.now().isoformat(timespec="seconds")

    hint = _build_rollback_hint(entry)
    entry["rollback_hint"] = hint
    _PENDING[op_id] = entry
    _append_jsonl(entry)
    _print_op_result(entry, hint)
    # dry-run changed nothing — skip the live post-op verification (it would
    # re-scan the real pool and could falsely warn / suggest a rollback).
    if success and not dry_run:
        _post_op_verify(entry)


def _build_rollback_hint(entry: dict) -> str | None:
    op = entry.get("op", "")
    if op in _NO_ROLLBACK:
        return None
    fn = _ROLLBACK.get(op)
    if fn:
        try:
            return fn(entry)
        except Exception:
            return None
    return None


def _capture_snapshot(op_id: str, pool: str, dev_path: str) -> str | None:
    """Capture zpool status + smartctl to snapshot file. Return path or None."""
    try:
        os.makedirs(SNAP_DIR, exist_ok=True)
    except OSError:
        pass
    lines = [f"=== b2ctl pre-op snapshot: {op_id} ===\n"]
    for cmd in (
        ["zpool", "status", pool],
        ["zpool", "list", "-v"],
        ["zfs", "list"],
        ["smartctl", "-a", dev_path],
    ):
        ok, out = run_check(cmd, timeout=15)
        lines.append(f"\n--- {' '.join(cmd)} ---\n{out}\n")
    path = os.path.join(SNAP_DIR, f"{op_id}.txt")
    try:
        with open(path, "w") as f:
            f.write("".join(lines))
        return path
    except OSError:
        return None


def _append_jsonl(entry: dict) -> None:
    # O_APPEND of one small line is atomic on POSIX, so concurrent b2ctl
    # processes never interleave (F-093) — no locking or rewrite needed.
    global _log_warned
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        if not _log_warned:
            _log_warned = True
            print(f"{Y}⚠ audit log unwritable ({LOG_FILE}): {exc}{N}")


def _load_entry(op_id: str) -> dict | None:
    """Return the merged entry for op_id (last matching record wins), or None.

    Append-only log: begin_op writes one record, end_op appends another with the
    same op_id — the later record carries the final status/hint.
    """
    found = None
    try:
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("op_id") == op_id:
                        found = {**found, **e} if found else e
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return found


def _print_op_result(entry: dict, hint: str | None) -> None:
    status = entry.get("status", "")
    pool   = entry.get("pool", "")
    vdev   = entry.get("vdev", "")
    # dry-run: nothing was executed — don't render a red ✗ or a rollback hint.
    if status == "dry_run":
        print(f"{Y}• {entry.get('op')} dry-run preview — nothing changed ({pool}/{vdev}){N}")
        return
    icon   = f"{G}✓{N}" if status == "ok" else f"{R}✗{N}"
    print(f"{icon} {entry.get('op')} complete ({pool}/{vdev})")
    snap = entry.get("snapshot_path")
    if hint:
        print(f"  Rollback : {hint}")
    if snap:
        print(f"  Snapshot : {snap}")
    if status == "fail":
        print(f"  {R}stderr   : {entry.get('stderr','')}{N}")


def _post_op_verify(entry: dict) -> None:
    """Re-scan pool state and warn if expected state not reached."""
    op   = entry.get("op", "")
    pool = entry.get("pool", "")
    serial = entry.get("disk_serial", "")
    # aux-repair from the CLI verbs carries no Disk (serial ""), so it must NOT be
    # gated on serial — its check keys on the new device token instead. Every
    # other op always records a serial, so keep requiring it for them.
    if not pool or (not serial and op != "aux-repair"):
        return
    ok, out = run_check(["zpool", "status", pool], timeout=15)
    if not ok:
        return
    # aux-repair: a `replace` path resilvers (look for the marker); a cache/log
    # remove+add path leaves no resilver, so fall back to the new device token.
    new_dev = os.path.basename(entry.get("new_dev") or entry.get("dev_path", ""))
    op_checks = {
        "offline":   lambda o: serial not in o or "OFFLINE" in o,
        "replace":   lambda o: "resilver" in o or "resilvered" in o,
        "add_spare": lambda o: serial in o,
        "aux-repair": lambda o: ("resilver" in o or "resilvered" in o
                                 or (new_dev and new_dev in o)),
    }
    check = op_checks.get(op)
    if check and not check(out):
        snap = entry.get("snapshot_path", "")
        op_id = entry.get("op_id", "")
        print(f"{Y}⚠ Post-op check FAILED for {op} (serial {serial}){N}")
        if snap:
            print(f"  See snapshot: {snap}")
        print(f"  Run: b2ctl rollback {op_id}")


def load_log(last: int = 20) -> list[dict]:
    """Return the last N ops from ops.jsonl, merging each op's begin+end records.

    Append-only means one op spans two lines (F-093); merge them by op_id
    (last record wins) so callers see one final entry per op.
    """
    merged: dict = {}
    try:
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    op_id = e.get("op_id")
                    if op_id:
                        merged[op_id] = {**merged.get(op_id, {}), **e}
                except json.JSONDecodeError:
                    pass
    except PermissionError:
        print("  [!] cannot read log — run as root")
    except OSError:
        pass
    return list(merged.values())[-last:]


def find_entry(op_id: str) -> dict | None:
    return _load_entry(op_id)
