"""Unit tests for b2ctl.maint — the scrub/trim/health history log + rel_time."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from b2ctl import maint


class TestMaintLog(unittest.TestCase):
    """maint.jsonl round-trip, redirected via safety.LOG_DIR (like burnin.json)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p = patch("b2ctl.safety.LOG_DIR", self.tmp)
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_log_and_load_roundtrip(self):
        maint.log_event("scrub", "tank", "started", "")
        rec = maint.log_event("scrub", "tank", "ok", "completed at X")
        assert rec["kind"] == "scrub" and rec["status"] == "ok"
        assert "ts" in rec
        evs = maint.load_events()
        assert len(evs) == 2
        assert evs[-1]["status"] == "ok" and evs[-1]["detail"] == "completed at X"
        # the file lives under the redirected LOG_DIR (sim/test redirect works)
        assert os.path.exists(os.path.join(self.tmp, "maint.jsonl"))

    def test_last_event_picks_most_recent_matching(self):
        maint.log_event("trim", "tank", "started", "")
        maint.log_event("scrub", "tank", "ok", "scrubbed")
        maint.log_event("trim", "tank", "ok", "done")
        last = maint.last_event("trim", "tank")
        assert last["status"] == "ok" and last["detail"] == "done"
        assert maint.last_event("scrub", "rpool") is None

    def test_load_events_last_cap(self):
        for i in range(5):
            maint.log_event("scrub", "tank", "ok", str(i))
        capped = maint.load_events(last=2)
        assert len(capped) == 2 and capped[-1]["detail"] == "4"

    def test_load_missing_file_is_empty(self):
        assert maint.load_events() == []
        assert maint.last_event("scrub", "tank") is None


class TestRelTime(unittest.TestCase):
    def test_buckets(self):
        now = datetime.now()
        assert maint.rel_time("") == ""
        assert maint.rel_time((now - timedelta(seconds=5)).isoformat()).endswith("s ago")
        assert maint.rel_time((now - timedelta(minutes=5)).isoformat()).endswith("m ago")
        assert maint.rel_time((now - timedelta(hours=5)).isoformat()).endswith("h ago")
        assert maint.rel_time((now - timedelta(days=3)).isoformat()).endswith("d ago")

    def test_unparseable_echoes_raw(self):
        assert maint.rel_time("not-a-date") == "not-a-date"

    def test_epoch_accepted(self):
        import time
        assert maint.rel_time(time.time() - 120).endswith("m ago")


if __name__ == "__main__":
    unittest.main()
