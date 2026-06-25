"""Unit tests for b2ctl.safety — begin_op/end_op JSONL log + snapshot dir."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestBeginOp(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_begin_op_writes_pending_entry(self):
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "/dev/disk/by-id/old", "/dev/disk/by-id/new"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            op_id = safety.begin_op("replace", "ABC123", 3, "/dev/disk/by-id/wwn-x", "tank", "raidz1-0", cmds)
        self.assertTrue(op_id.endswith("-replace"))
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["op"], "replace")
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["disk_serial"], "ABC123")
        self.assertEqual(entry["disk_bay"], 3)
        self.assertEqual(entry["cmds"], cmds)

    def test_end_op_updates_entry_ok(self):
        safety = self._import_safety()
        cmds = [["zpool", "offline", "tank", "/dev/disk/by-id/x"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("offline", "XYZ", 5, "/dev/disk/by-id/x", "tank", "raidz1-0", cmds)
                safety.end_op(op_id, True, "success output", "", 0)
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["exit_code"], 0)
        self.assertEqual(entry["stdout"], "success output")

    def test_end_op_updates_entry_fail(self):
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "x", "y"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("replace", "ZZZ", 2, "/dev/disk/by-id/z", "tank", "raidz1-0", cmds)
                safety.end_op(op_id, False, "", "error text", 1)
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["exit_code"], 1)
        self.assertEqual(entry["stderr"], "error text")

    def test_snapshot_dir_created(self):
        safety = self._import_safety()
        os.makedirs(self.snap_dir, exist_ok=True)
        cmds = [["zpool", "add", "tank", "spare", "/dev/disk/by-id/x"]]
        with patch("b2ctl.safety.run_check", return_value=(True, "pool: tank\nstate: ONLINE")):
            op_id = safety.begin_op("add_spare", "AAA", 1, "/dev/disk/by-id/x", "tank", "spares", cmds)
        snap_path = os.path.join(self.snap_dir, f"{op_id}.txt")
        self.assertTrue(os.path.exists(snap_path))

    def test_dry_run_skips_snapshot(self):
        safety = self._import_safety()
        os.makedirs(self.snap_dir, exist_ok=True)
        cmds = [["zpool", "replace", "tank", "a", "b"]]
        with patch.object(safety, "_capture_snapshot") as mock_snap:
            op_id = safety.begin_op("replace", "S1", 3, "/dev/disk/by-id/x",
                                    "tank", "raidz1-0", cmds, dry_run=True)
        mock_snap.assert_not_called()                       # no capture on dry-run
        self.assertFalse(os.path.exists(os.path.join(self.snap_dir, f"{op_id}.txt")))
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertIsNone(entry["snapshot_path"])


class TestEndOpDryRun(unittest.TestCase):
    """dry-run end_op must not render an error icon, suggest a rollback, or
    run the live post-op verification."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_dry_run_clean_output_and_no_verify(self):
        import io
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "a", "b"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            op_id = safety.begin_op("replace", "S1", 3, "/dev/disk/by-id/x",
                                    "tank", "raidz1-0", cmds)
        with patch.object(safety, "_post_op_verify") as mock_verify:
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                safety.end_op(op_id, True, "", "", 0, dry_run=True)
        text = out.getvalue()
        mock_verify.assert_not_called()          # no live re-scan on dry-run
        self.assertIn("dry-run", text)
        self.assertNotIn("✗", text)              # no red error icon
        self.assertNotIn("Rollback", text)       # nothing to roll back
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["status"], "dry_run")


if __name__ == "__main__":
    unittest.main()
