"""b2ctl.safety — audit trail, snapshots, rollback hints, post-op verification."""
from __future__ import annotations

import json
import os
from datetime import datetime

from .common import run_check, R, Y, G, N

LOG_DIR  = "/var/log/b2ctl"
SNAP_DIR = "/var/log/b2ctl/snapshots"
LOG_FILE = "/var/log/b2ctl/ops.jsonl"

# Commands whose first token is considered a write op (suppressed in dry-run)
WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}

# Rollback hint table: op -> lambda(entry) -> hint_str or None
_ROLLBACK: dict = {
    "offline":   lambda e: f"zpool online {e['pool']} {e['dev_path']}",
    "add_spare": lambda e: f"zpool remove {e['pool']} {e['dev_path']}",
    "replace":   lambda e: (
        f"zpool replace {e['pool']} {e['dev_path']} "
        + (e.get("old_dev_path", e['dev_path']))
    ),
    "demote":    lambda e: (
        f"zpool attach {e['pool']} <remaining-member> {e['dev_path']}"
    ),
    "create":    lambda e: f"zpool destroy {e['pool']}  # WARNING: destroys all data",
}
_NO_ROLLBACK = {"wipefs", "sgdisk", "wipe"}


def begin_op(
    op: str,
    serial: str,
    bay,
    dev_path: str,
    pool: str,
    vdev: str,
    cmds: list[list[str]],
) -> str:
    """Write pending audit entry + snapshot. Return op_id."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        os.makedirs(SNAP_DIR, exist_ok=True)
    except OSError:
        pass
    now = datetime.now()
    op_id = now.strftime("%Y%m%d-%H%M%S") + f"-{op}"
    entry = {
        "op_id":       op_id,
        "op":          op,
        "disk_serial": serial,
        "disk_bay":    bay,
        "dev_path":    dev_path,
        "pool":        pool,
        "vdev":        vdev,
        "cmds":        cmds,
        "status":      "pending",
        "exit_code":   None,
        "stdout":      "",
        "stderr":      "",
        "started_at":  now.isoformat(timespec="seconds"),
        "ended_at":    None,
        "rollback_hint": None,
        "snapshot_path": None,
    }
    snap_path = _capture_snapshot(op_id, pool, dev_path)
    if snap_path:
        entry["snapshot_path"] = snap_path
    _append_jsonl(entry)
    return op_id


def end_op(op_id: str, success: bool, stdout: str, stderr: str, exit_code: int,
           *, dry_run: bool = False) -> None:
    """Update audit entry status + print rollback hint."""
    entry = _load_entry(op_id)
    if entry is None:
        return
    entry["status"]    = "dry_run" if dry_run else ("ok" if success else "fail")
    entry["exit_code"] = exit_code
    entry["stdout"]    = stdout
    entry["stderr"]    = stderr
    entry["ended_at"]  = datetime.now().isoformat(timespec="seconds")

    hint = _build_rollback_hint(entry)
    entry["rollback_hint"] = hint
    _rewrite_entry(op_id, entry)
    _print_op_result(entry, hint)
    if success:
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
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _load_entry(op_id: str) -> dict | None:
    try:
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("op_id") == op_id:
                        return e
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return None


def _rewrite_entry(op_id: str, updated: dict) -> None:
    """Rewrite the matching line in ops.jsonl."""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
        with open(LOG_FILE, "w") as f:
            for line in lines:
                try:
                    e = json.loads(line)
                    if e.get("op_id") == op_id:
                        f.write(json.dumps(updated) + "\n")
                    else:
                        f.write(line)
                except json.JSONDecodeError:
                    f.write(line)
    except OSError:
        pass


def _print_op_result(entry: dict, hint: str | None) -> None:
    status = entry.get("status", "")
    pool   = entry.get("pool", "")
    vdev   = entry.get("vdev", "")
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
    if not pool or not serial:
        return
    ok, out = run_check(["zpool", "status", pool], timeout=15)
    if not ok:
        return
    op_checks = {
        "offline":   lambda o: serial not in o or "OFFLINE" in o,
        "replace":   lambda o: "resilver" in o or "resilvered" in o,
        "add_spare": lambda o: serial in o,
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
    """Return last N entries from ops.jsonl."""
    entries = []
    try:
        with open(LOG_FILE) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return entries[-last:]


def find_entry(op_id: str) -> dict | None:
    return _load_entry(op_id)
