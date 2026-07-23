"""b2ctl.maint — persistent maintenance history (scrub / trim / health).

A dedicated append-only log `maint.jsonl` beside the safety audit trail so a
manual scrub/trim/health-check leaves a durable record (detail + date-time) the
`b2ctl maint --log` view and the status columns can read back. Kept OUT of
`ops.jsonl` (that schema is op/rollback-centric) — this copies safety's
open-append idiom, not its schema.

The path is read from `safety.LOG_DIR` AT CALL TIME so the sim harness's
`safety.LOG_DIR` monkeypatch (and unit-test redirect) also redirects this file,
exactly like burnin.json (ADR-002).

Timestamps use `datetime.now().isoformat(timespec="seconds")` — the safety.py
house style, naive-local, sortable, human-readable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

# Event kinds and statuses (free-form strings, but keep these canonical).
KINDS = ("scrub", "trim", "health")


def _state_dir() -> str:
    from . import safety                 # read at call time -> inherits sim redirect
    return safety.LOG_DIR


def _path() -> str:
    return os.path.join(_state_dir(), "maint.jsonl")


def log_event(kind: str, target: str, status: str, detail: str = "") -> dict:
    """Append one maintenance event and return the record. Best-effort: a write
    failure never aborts the maintenance action itself (the kernel op still ran).

    kind   : "scrub" | "trim" | "health"
    target : pool name (scrub/trim) or disk serial (health)
    status : "started" | "ok" | "fail"
    detail : free-form (errors repaired, verdict, self-test result, ...)."""
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "target": target,
        "status": status,
        "detail": detail,
    }
    try:
        os.makedirs(_state_dir(), exist_ok=True)
        # O_APPEND of one small line is atomic on POSIX, so concurrent b2ctl
        # processes never interleave (mirrors safety._append_jsonl).
        with open(_path(), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec


def load_events(last: int | None = None) -> list[dict]:
    """Read maint.jsonl newest-last. `last` caps to the most recent N. Tolerant:
    a missing file or a bad line yields [] / skips the line."""
    out: list[dict] = []
    try:
        with open(_path()) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        return []
    return out[-last:] if last else out


def last_event(kind: str, target: str) -> dict | None:
    """Most recent event of `kind` for `target` (by append order), or None."""
    match = None
    for rec in load_events():
        if rec.get("kind") == kind and rec.get("target") == target:
            match = rec
    return match


def rel_time(ts) -> str:
    """Human 'time ago' for an ISO-8601 string or an epoch number. Returns '' for
    a falsy value; echoes the raw string if it can't be parsed."""
    if not ts:
        return ""
    try:
        when = (datetime.fromtimestamp(float(ts)) if isinstance(ts, (int, float))
                else datetime.fromisoformat(str(ts)))
    except (ValueError, OSError, OverflowError):
        return str(ts)
    secs = (datetime.now() - when).total_seconds()
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"
