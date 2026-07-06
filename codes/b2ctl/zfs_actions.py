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
