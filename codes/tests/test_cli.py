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


class TestCliRollbackPlaceholders(unittest.TestCase):
    """fix 6: rollback hints with placeholder tokens must not be exec'd."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_rollback_placeholder_hint_prints_warning_not_execute(self):
        import b2ctl.safety as safety
        import b2ctl.cli as cli
        safety.LOG_FILE = os.path.join(self.tmp, "ops.jsonl")
        entry = {
            "op_id": "20260617-replace", "op": "replace",
            "disk_serial": "X", "disk_bay": 1, "pool": "tank",
            "status": "ok", "started_at": "2026-06-17T10:00:00",
            "dev_path": "/dev/disk/by-id/x", "vdev": "raidz1-0",
            "cmds": [], "exit_code": 0, "stdout": "", "stderr": "",
            "ended_at": None,
            "rollback_hint": "zpool replace tank <new-disk> /dev/disk/by-id/x",
            "snapshot_path": None,
        }
        with open(safety.LOG_FILE, "w") as f:
            f.write(json.dumps(entry) + "\n")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out, \
             patch("builtins.input", return_value="y"), \
             patch("b2ctl.safety.begin_op") as mock_begin:
            cli._rollback_cmd("20260617-replace")
            output = mock_out.getvalue()
        assert "placeholder" in output.lower()
        mock_begin.assert_not_called()


class TestRaidCommands(unittest.TestCase):
    """RAID subcommands parse and destructive ops respect the confirm guard."""

    def test_parser_has_raid_subcommands(self):
        import b2ctl.cli as cli
        p = cli.build_parser()
        for cmd in ("raid-replace", "raid-offline", "raid-create", "raid-del"):
            ns = p.parse_args([cmd] + (["32:0"] if cmd in ("raid-offline",) else
                                       (["0"] if cmd == "raid-del" else
                                        (["--level", "raid1", "--drives", "32:0,32:1"]
                                         if cmd == "raid-create" else []))))
            assert hasattr(ns, "func")

    def test_delete_vd_cancelled_does_not_call_perccli(self):
        import b2ctl.raid_actions as ra
        with patch("builtins.input", return_value="n"), \
             patch("b2ctl.hba_raid.del_vd") as del_mock:
            rc = ra.delete_vd(0)
        assert rc == 1
        del_mock.assert_not_called()

    def test_create_vd_requires_second_confirm(self):
        import b2ctl.raid_actions as ra
        # first confirm yes, second no -> cancelled, no perccli
        with patch("builtins.input", side_effect=["y", "n"]), \
             patch("b2ctl.hba_raid.add_vd") as add_mock:
            rc = ra.create_vd("raid1", ["32:0", "32:1"])
        assert rc == 1
        add_mock.assert_not_called()

    def test_assign_perc_jbod_path_calls_set_jbod(self):
        import b2ctl.raid_actions as ra
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda"); d.bay = "32:4"; d.pd_state = "UGood"
        # menu choice 2 (set JBOD), then confirm 'y'
        with patch("builtins.input", side_effect=["2", "y"]), \
             patch("b2ctl.hba_raid.set_jbod", return_value=(True, "")) as jbod_mock, \
             patch("subprocess.run"):
            rc = ra.assign_perc(d, [d])
        assert rc == 0
        jbod_mock.assert_called_once_with("32:4")

    def test_assign_perc_create_path_calls_add_vd(self):
        import b2ctl.raid_actions as ra
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda"); d.bay = "32:4"; d.pd_state = "UGood"
        d2 = Disk(dev="/dev/sda"); d2.bay = "32:5"; d2.pd_state = "UGood"
        # choice 3 (create), pick both drives, level raid1, two create confirms
        with patch("builtins.input", side_effect=["3", "1 2", "raid1", "y", "y"]), \
             patch("b2ctl.hba_raid.add_vd", return_value=(True, "")) as add_mock:
            ra.assign_perc(d, [d, d2])
        add_mock.assert_called_once_with("raid1", ["32:4", "32:5"])


if __name__ == "__main__":
    unittest.main()
