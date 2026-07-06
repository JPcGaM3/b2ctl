"""pytest bootstrap for the b2ctl test suite.

Run from the package root:  cd codes && python3 -m pytest tests/

Puts this tests/ directory on sys.path so every per-module test file can do
`from helpers import _disk, _RAIDZ_STATUS, ...` for the shared Disk factory and
sample command outputs.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(autouse=True)
def _reset_probe_memos():
    """The backend probes are memoized per-process (F-037/F-040); clear them
    between tests so a mocked `run` in one test can't leak into the next."""
    import b2ctl.hba as _hba
    import b2ctl.hba_raid as _raid
    import b2ctl.config as _cfg
    import b2ctl.baymap as _baymap
    _hba._reset_have_cache()
    _raid._reset_caches()
    _cfg._cache = None            # a partial _cache set by one test must not leak
    _baymap._cache = None
    yield
    _hba._reset_have_cache()
    _raid._reset_caches()
    _cfg._cache = None
    _baymap._cache = None
