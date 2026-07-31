"""b2ctl.jsonout — the machine-readable envelope every `--json` command emits.

b2ctl is driven by an MCP server and a web UI as well as by an operator, so a
program has to be able to read every answer AND every failure. Before this, the
only JSON in the product was `status --json`, which dumped `vars(Disk)` straight
out: no version, no pools/volumes, and any field rename silently broke clients.

One shape for success and failure alike (ADR-007)::

    {"schema_version": 1, "ok": true,  "command": "status",
     "data": {...}, "warnings": [], "error": null}

    {"schema_version": 1, "ok": false, "command": "destroy",
     "data": null, "warnings": [], "error": {"code": "POOL_NOT_FOUND",
                                             "message": "no pool named tonk"}}

The keys never vary, so a client parses once and branches on `ok` / `error.code`
— never on prose, which is free to change. Exit codes stay 0/1; the envelope
carries the detail.
"""
from __future__ import annotations

import json
import sys

# Bump ONLY on a breaking change — a removed or renamed field, or a changed
# meaning. Adding a key is backward compatible and keeps the version (ADR-007).
SCHEMA_VERSION = 1

# Stable error codes. Clients branch on these; `message` is human text and may
# be reworded at any time.
ERR_NO_BACKEND = "NO_BACKEND"         # no HBA/RAID tool could be detected
ERR_TOOL_MISSING = "TOOL_MISSING"     # a required binary is absent/unrunnable
ERR_POOL_NOT_FOUND = "POOL_NOT_FOUND"
ERR_DISK_NOT_FOUND = "DISK_NOT_FOUND"
ERR_NEEDS_ROOT = "NEEDS_ROOT"
ERR_INVALID_ARG = "INVALID_ARG"
ERR_PARSE_ERROR = "PARSE_ERROR"       # a vendor tool's output could not be read
ERR_UNSUPPORTED = "UNSUPPORTED"       # right shape, wrong mode (e.g. raid-* on IT)
ERR_OP_FAILED = "OP_FAILED"           # a mutating command ran and did not succeed
                                      # (or the operator declined) — `data.log`
                                      # carries what it printed while running


def _envelope(command: str, *, ok: bool, data, error, warnings) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "command": command,
        "data": data,
        "warnings": list(warnings or []),
        "error": error,
    }


def _write(payload: dict) -> None:
    """Write the envelope as the ONLY thing on stdout.

    Anything else printed in JSON mode corrupts the stream, which is why the
    read path routes its notices through common.warn() into `warnings` instead
    of printing them (ADR-007).
    """
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def emit(command: str, data=None, *, warnings=None) -> int:
    """Emit a success envelope; return the process exit code (0).

    Pending warnings are drained automatically, so a caller never has to
    remember to collect them.
    """
    from .common import take_warnings
    collected = take_warnings()
    if warnings:
        collected += list(warnings)
    _write(_envelope(command, ok=True, data=data, error=None, warnings=collected))
    return 0


def fail(command: str, code: str, message: str, *, data=None) -> int:
    """Emit a failure envelope; return the process exit code (1).

    `data` stays available on failure — a partial result is often exactly what
    lets a client explain what went wrong.
    """
    from .common import take_warnings
    _write(_envelope(command, ok=False, data=data,
                     error={"code": code, "message": message},
                     warnings=take_warnings()))
    return 1
