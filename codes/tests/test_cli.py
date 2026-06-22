"""Unit tests for b2ctl.cli — log reading + rollback messaging."""
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestCliLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_log_command_reads_jsonl(self):
        import b2ctl.safety as safety
        import b2ctl.cli as cli  # noqa: F401 (ensures cli imports cleanly)
        safety.LOG_FILE = os.path.join(self.tmp, "ops.jsonl")
        entries = [
            {"op_id": "20260617-100000-replace", "op": "replace", "disk_serial": "S1",
             "disk_bay": 1, "pool": "tank", "status": "ok", "started_at": "2026-06-17T10:00:00",
             "dev_path": "/dev/disk/by-id/x", "vdev": "raidz1-0",
             "cmds": [], "exit_code": 0, "stdout": "", "stderr": "",
             "ended_at": "2026-06-17T10:00:05", "rollback_hint": None, "snapshot_path": None},
        ]
        with open(safety.LOG_FILE, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        loaded = safety.load_log(last=10)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["op"], "replace")


class TestCliRollback(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_rollback_irreversible_prints_message(self):
        import b2ctl.safety as safety
        import b2ctl.cli as cli
        safety.LOG_FILE = os.path.join(self.tmp, "ops.jsonl")
        entry = {
            "op_id": "20260617-wipefs", "op": "wipefs",
            "disk_serial": "X", "disk_bay": 1, "pool": "tank",
            "status": "ok", "started_at": "2026-06-17T10:00:00",
            "dev_path": "/dev/disk/by-id/x", "vdev": "spares",
            "cmds": [], "exit_code": 0, "stdout": "", "stderr": "",
            "ended_at": None, "rollback_hint": None,
            "snapshot_path": "/var/log/b2ctl/snapshots/20260617-wipefs.txt",
        }
        with open(safety.LOG_FILE, "w") as f:
            f.write(json.dumps(entry) + "\n")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cli._rollback_cmd("20260617-wipefs")
            output = mock_out.getvalue()
        self.assertIn("not reversible", output.lower())


if __name__ == "__main__":
    unittest.main()
