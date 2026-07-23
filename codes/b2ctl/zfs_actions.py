"""b2ctl.zfs_actions — public entry points for the ZFS lifecycle workflows.

The interactive workflows are implemented in watch (the select() loop module).
The CLI subcommands (offload / replace / create / destroy / swap / demote) call
THESE public functions instead of reaching into watch's underscore-private
`_cmd_*` handlers, so the CLI↔workflow contract is explicit: a watch refactor
that renames an internal breaks here in one obvious place, not in six scattered
cli handlers with a runtime AttributeError (F-070). This mirrors raid_actions,
the RAID-mode counterpart.

Each function returns a process exit code — 0 when the operation completed, 1
when it was cancelled or failed — fixing the old 'lifecycle wrappers always exit
0' so scripts and cron can detect a failed offload/replace/etc.
"""
from __future__ import annotations

from . import watch, spec


def _rc(ok) -> int:
    return 0 if ok else 1


def _tbw(tbw):
    return tbw if tbw is not None else spec.load()


def offload(tbw=None) -> int:
    return _rc(watch._cmd_offload(_tbw(tbw)))


def replace(tbw=None) -> int:
    return _rc(watch._cmd_replace(_tbw(tbw)))


def create(tbw=None, *, raid10: bool = False) -> int:
    return _rc(watch._cmd_create(_tbw(tbw), raid_type="raid10" if raid10 else None))


def destroy(pool: str | None = None, tbw=None) -> int:
    return _rc(watch._cmd_destroy(_tbw(tbw), target=pool))


def swap(tbw=None) -> int:
    return _rc(watch._cmd_swap(_tbw(tbw)))


def demote(tbw=None) -> int:
    return _rc(watch._cmd_demote(_tbw(tbw)))


def scrub(pool: str | None = None, tbw=None) -> int:
    """Manual scrub. `pool` skips the pool prompt (CLI passed it)."""
    return _rc(watch._cmd_maint(_tbw(tbw), action="scrub", pool=pool))


def trim(pool: str | None = None, tbw=None) -> int:
    """Manual TRIM. `pool` skips the pool prompt (CLI passed it)."""
    return _rc(watch._cmd_maint(_tbw(tbw), action="trim", pool=pool))


def cache_replace(pool: str, old: str, new: str) -> int:
    """Repair a degraded L2ARC cache leaf (remove old + add new)."""
    return _aux_replace(pool, old, new, klass="cache")


def log_replace(pool: str, old: str, new: str) -> int:
    """Repair a degraded SLOG log leaf (replace, or remove+add if fully gone)."""
    return _aux_replace(pool, old, new, klass="log")


def _aux_replace(pool: str, old: str, new: str, *, klass: str) -> int:
    from . import zfs
    from .common import R, N
    leaves = zfs.aux_leaves(pool)
    # Prefer an EXACT token match; only fall back to a substring match. Then,
    # among the matches, prefer a DEGRADED leaf — otherwise an ambiguous token on
    # a mirrored SLOG of identical SSDs (legs share a long common substring) could
    # aim a destructive `zpool replace` at the HEALTHY leg. Refuse if still
    # ambiguous so the operator names the exact leaf token.
    matches = ([l for l in leaves if l["token"] == old]
               or [l for l in leaves if old in l["token"]])
    if not matches:
        print(f"{R}[-] '{old}' is not a cache/log leaf on '{pool}' — "
              f"check `zpool status {pool}`.{N}")
        return 1
    degraded = [l for l in matches if l["degraded"]]
    pick = degraded or matches
    if len(pick) > 1:
        toks = ", ".join(l["token"] for l in pick)
        print(f"{R}[-] '{old}' is ambiguous — matches {len(pick)} devices ({toks}); "
              f"pass the exact leaf token from `zpool status -P {pool}`.{N}")
        return 1
    leaf = pick[0]
    if leaf["klass"] != klass:
        print(f"{R}[-] '{old}' is a {leaf['klass']} device, not {klass}; use "
              f"{leaf['klass']}-replace.{N}")
        return 1
    # watch._repair_aux reads the ambient watch._DRY_RUN, same as the other verbs.
    return _rc(watch._repair_aux(pool, leaf, None, new_token=new))
