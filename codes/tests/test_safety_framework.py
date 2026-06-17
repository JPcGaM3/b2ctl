# codes/tests/test_safety_framework.py
import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Patch log dirs to temp before importing safety


class TestBeginOp(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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


class TestRunCheckDryRun(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_dry_run_write_cmd_no_subprocess(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import b2ctl.common as common
        import b2ctl.safety as safety
        safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}
        with patch("subprocess.run") as mock_run:
            ok, out = common.run_check(["zpool", "replace", "tank", "x", "y"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(out, "")

    def test_dry_run_read_cmd_executes(self):
        # "smartctl" is not in WRITE_CMDS — it is a read-only cmd that must
        # pass through even when dry_run=True.
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import b2ctl.common as common
        import b2ctl.safety as safety
        safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="pool: tank\n", stderr="")
            ok, out = common.run_check(["smartctl", "-a", "/dev/sda"], dry_run=True)
        mock_run.assert_called_once()
        self.assertTrue(ok)

    def test_dry_run_false_write_cmd_executes(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            ok, out = common.run_check(["zpool", "offline", "tank", "x"])
        mock_run.assert_called_once()
        self.assertTrue(ok)


class TestConfirmOp(unittest.TestCase):

    def _make_disk(self, bay=3, serial="S3EV123", model="Samsung 870 EVO 1TB",
                   by_id="/dev/disk/by-id/wwn-0xDEAD", pool="tank", vdev="raidz1-0"):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from b2ctl.common import Disk
        d = Disk.__new__(Disk)
        d.bay = bay; d.serial = serial; d.model = model
        d.by_id = by_id; d.pool = pool; d.vdev = vdev
        d.dev = "/dev/sda"; d.pool_token = by_id
        return d

    def test_confirm_op_yes(self):
        import b2ctl.watch as watch
        disk = self._make_disk()
        cmds = [["zpool", "replace", "tank", "/dev/disk/by-id/old", "/dev/disk/by-id/new"]]
        with patch("builtins.input", return_value="y"):
            result = watch._confirm_op("replace", disk, None, "tank", "raidz1-0", cmds)
        self.assertTrue(result)

    def test_confirm_op_no(self):
        import b2ctl.watch as watch
        disk = self._make_disk()
        cmds = [["zpool", "offline", "tank", "/dev/disk/by-id/x"]]
        with patch("builtins.input", return_value="n"):
            result = watch._confirm_op("offline", disk, None, "tank", "raidz1-0", cmds)
        self.assertFalse(result)

    def test_confirm_op_shows_device_path(self):
        import b2ctl.watch as watch
        import io
        disk = self._make_disk()
        cmds = [["zpool", "replace", "tank", "/dev/disk/by-id/wwn-0xDEAD", "/dev/disk/by-id/wwn-0xBEEF"]]
        with patch("builtins.input", return_value="n"):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                watch._confirm_op("replace", disk, None, "tank", "raidz1-0", cmds)
                output = mock_out.getvalue()
        self.assertIn("/dev/disk/by-id/wwn-0xDEAD", output)
        self.assertIn("/dev/disk/by-id/wwn-0xBEEF", output)
        self.assertIn("replace", output)
