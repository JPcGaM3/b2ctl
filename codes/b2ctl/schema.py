"""b2ctl.schema — explicit wire-format projections for the machine contract.

b2ctl is being driven by a service on the box that shells out to it and forwards results to a web UI, so every command must
return JSON with a stable, deliberate wire format. Before this module the only
JSON in the product was `status --json` (cli.py:63-64):
`json.dumps([vars(d) for d in disks], default=str)` — a raw dataclass dump.
`vars()` puts every internal field on the wire, including transient scan-only
state, and a rename during a refactor silently reshapes a published API.

Each function here builds its dict from a named field list instead, so
publishing a field is a decision someone made, not a side effect (ADR-007).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Disk — projects a common.Disk
# --------------------------------------------------------------------------- #
# Grouped to mirror the field comments in common.py (identity/health/topology/
# perc/verdict/selftest). Deliberately NOT on the wire (see ADR-007):
#   pool_token                          - internal `zpool status -P` leaf
#                                          token, not a client concern
#   selftest_running/_pct/_eta          - transient mid-scan progress, only
#                                          meaningful while a test is running;
#                                          long-running progress gets its own
#                                          verb later
#   spare_replacing                     - derivable, and its shape is not
#                                          settled yet
# Also not listed here (internal implementation detail, not requested for the
# wire contract): ctrl, smart_dtype, lba_written.
DISK_FIELDS = (
    # identity
    "dev", "ctrl_dev", "bay", "ctrl_slot", "serial", "wwn", "by_id", "model",
    "size_bytes", "iface", "is_ssd",
    # health
    "health", "readable", "poh", "wear_val", "end_left", "written_tb",
    "tbw_rating", "end_source", "end_left_spec",
    "realloc", "pending", "uncorr", "cmd_timeout",
    # topology — pool_known says whether `pool: null` means "free" or "we could
    # not ask"; a client that skips it will treat an unanswered zpool as an empty
    # one, which is the mistake b2ctl itself made (F-143)
    "pool", "pool_known", "vdev", "vdev_state", "array_type", "array_name",
    # perc
    "pd_state", "pd_foreign", "did",
    # verdict
    "level", "reasons",
    # selftest — last COMPLETED test only (see the exclusions above)
    "selftest_last_result", "selftest_last_poh",
)


def disk_json(d) -> dict:
    """Project a common.Disk onto DISK_FIELDS.

    A plain getattr per field, not a defaulting `.get()`: Disk is a dataclass
    where every field always exists, and its own defaults already encode the
    None-vs-"" distinction (e.g. `bay: str | None = None` means "unknown",
    `by_id: str = ""` means "empty") — carrying the attribute straight
    through preserves that on the wire without extra rules here.
    """
    return {f: getattr(d, f) for f in DISK_FIELDS}


# --------------------------------------------------------------------------- #
# Pool — projects a zfs.list_pools() entry (+ core.pool_maint() merge)
# --------------------------------------------------------------------------- #
# `level` here is a REDUNDANCY TYPE (mirror/raidz1/stripe/... from
# `zfs.pool_level`) — NOT the same concept as `DISK_FIELDS["level"]`, which is
# a HEALTH VERDICT (NORMAL/CONFIG/WARNING/CRITICAL from `common.assess`). Same
# key name, disjoint meaning, kept only because the CLI table renderer already
# reads `level` this way and renaming would break it for a naming preference
# (schema_version stays 1). `redundancy` (F-150c) carries the identical value
# under an unambiguous name — NEW clients should read `redundancy`, not
# `level`, for this field.
#
# `last_scrub_ts`/`last_trim_ts` (F-150c) are the raw ISO-8601 timestamps
# behind the human `last_scrub`/`last_trim` strings (see `core.pool_maint`) —
# None when there is no history, never "" or "never", so a client can
# `datetime.fromisoformat()` them directly instead of parsing "6h ago" prose.
POOL_FIELDS = ("name", "level", "redundancy", "health", "size", "alloc", "free",
               "last_scrub", "last_scrub_ts", "last_trim", "last_trim_ts")


def pool_json(p: dict) -> dict:
    """Project a pool dict onto POOL_FIELDS.

    `p` is one entry from `zfs.list_pools()` (name/size/alloc/free/health/...)
    optionally merged with `core.pool_maint()`'s last_scrub/last_trim(_ts).
    `.get()` throughout so a caller that has not merged pool_maint() in yet
    still gets a complete, valid dict — missing keys read back as None, not a
    KeyError.

    `redundancy` defaults to `p["level"]` when not separately supplied: every
    existing caller only ever sets `level` (from `zfs.pool_level`), so this is
    the one place that has to know the two keys are the same fact under two
    names (see the POOL_FIELDS comment) rather than requiring every call site
    to duplicate it.
    """
    out = {f: p.get(f) for f in POOL_FIELDS}
    out["redundancy"] = p.get("redundancy", p.get("level"))
    return out


# --------------------------------------------------------------------------- #
# Volume — projects an hba_raid.raid_volumes() entry
# --------------------------------------------------------------------------- #
VOLUME_FIELDS = ("vd", "name", "raid", "state", "size", "members", "controller")


def volume_json(v: dict) -> dict:
    """Project a hardware-RAID volume dict onto VOLUME_FIELDS.

    `v` is one entry from `hba_raid.raid_volumes()`. `.get()` throughout for
    the same reason as pool_json — an incomplete row degrades to None fields,
    never a KeyError.
    """
    return {f: v.get(f) for f in VOLUME_FIELDS}


# --------------------------------------------------------------------------- #
# Op — projects one safety audit entry onto the wire (F-150)
# --------------------------------------------------------------------------- #
# A mutating verb's result used to be `{"log": "<ANSI human narration>"}` — a
# text blob, which is presence rather than format. The structured record already
# existed: safety.begin_op/end_op build exactly this dict at 15 call sites and
# write it to ops.jsonl, where the caller never sees it. This publishes it.
#
# Deliberately NOT on the wire: `stdout`/`stderr` (raw tool output, already
# summarised by status/exit_code and available in data.log), and `snapshot_path`
# (a path on the b2ctl host, meaningless to a remote reader).
OP_FIELDS = (
    "op_id", "op", "status", "exit_code",
    "disk_serial", "disk_bay", "dev_path", "old_dev", "new_dev",
    "pool", "vdev", "cmds", "rollback_hint",
    "started_at", "ended_at",
)


def op_json(e: dict) -> dict:
    """Project one safety audit entry onto OP_FIELDS.

    `.get()` throughout: an entry captured mid-flight (begin_op written, end_op
    not yet) legitimately has no `ended_at`/`exit_code`, and a client reading
    `status == "pending"` should see nulls rather than a KeyError.
    """
    return {f: e.get(f) for f in OP_FIELDS}


# --------------------------------------------------------------------------- #
# Backend — describes which backend/verbs are active
# --------------------------------------------------------------------------- #
def backend_json() -> dict:
    """Describe the active backend so a client can decide which verbs apply.

    Every probe here is optional, and this function must never raise or block:
    `backend.get_backend()` can call `common.die()` (a SystemExit) when no
    HBA/RAID tool is found at all, and reading the controller personality is a
    real subprocess call. A read verb crashing because a controller happens to
    be absent is worse than one that reports null fields, so every probe is
    wrapped and degrades to None/"" on failure instead of propagating.
    """
    name = None
    bay_source = None
    try:
        from . import backend as _backend
        bk = _backend.get_backend()
        name = bk.name or None
        bay_source = getattr(bk, "bay_source", None)  # ITBackend only
    except (Exception, SystemExit):
        pass

    mode = None
    try:
        from . import config as _cfg
        mode = _cfg.controller_mode()
    except Exception:
        pass

    # The binary that actually backs the active backend: sas2ircu for an
    # ITBackend using its native bay source, perccli otherwise (RaidBackend,
    # or an ITBackend that fell back to perccli for bays, e.g. an HBA330).
    tool = None
    try:
        from . import config as _cfg
        if name == "it":
            tool_name = "perccli" if bay_source == "perccli" else "sas2ircu"
        elif name == "raid":
            tool_name = "perccli"
        else:
            tool_name = None
        if tool_name:
            tool = _cfg.tool(tool_name)
    except Exception:
        pass

    # Personality is a perccli-only concept (RAID-Mode vs HBA-Mode switch on
    # 13G+ PERCs) — only probe it once perccli's own presence check passes,
    # never on a box that has no perccli at all.
    personality = ""
    try:
        from . import hba_raid as _hba_raid
        if _hba_raid.have_tool():
            personality = _hba_raid._personality()
    except Exception:
        pass

    return {"name": name, "mode": mode, "bay_source": bay_source,
            "tool": tool, "personality": personality}
