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
        with patch("b2ctl.raid_actions._require_raid", return_value=True), \
             patch("builtins.input", return_value="n"), \
             patch("b2ctl.hba_raid.del_vd") as del_mock:
            rc = ra.delete_vd(0)
        assert rc == 1
        del_mock.assert_not_called()

    def test_create_vd_requires_second_confirm(self):
        import b2ctl.raid_actions as ra
        # first confirm yes, second no -> cancelled, no perccli
        with patch("b2ctl.raid_actions._require_raid", return_value=True), \
             patch("builtins.input", side_effect=["y", "n"]), \
             patch("b2ctl.hba_raid.add_vd") as add_mock:
            rc = ra.create_vd("raid1", ["32:0", "32:1"])
        assert rc == 1
        add_mock.assert_not_called()

    def test_create_vd_honors_dry_run(self):
        # --dry-run / [t]oggle must reach the perccli wrapper (no real mutation).
        import b2ctl.raid_actions as ra
        import b2ctl.watch as watch
        watch._DRY_RUN = True
        try:
            with patch("b2ctl.raid_actions._require_raid", return_value=True), \
                 patch("builtins.input", side_effect=["y", "y"]), \
                 patch("b2ctl.hba_raid.add_vd", return_value=(True, "")) as add_mock:
                ra.create_vd("raid1", ["32:0", "32:1"])
            add_mock.assert_called_once_with("raid1", ["32:0", "32:1"], dry_run=True)
        finally:
            watch._DRY_RUN = False

    def test_raid_action_refused_in_it_mode(self):
        import b2ctl.raid_actions as ra

        class _Fake:
            name = "it"
        with patch("b2ctl.backend.get_backend", return_value=_Fake()), \
             patch("b2ctl.hba_raid.add_vd") as add_mock:
            rc = ra.create_vd("raid1", ["1:0", "1:1"])
        assert rc == 1
        add_mock.assert_not_called()

    def test_assign_perc_jbod_path_calls_set_jbod(self):
        import b2ctl.raid_actions as ra
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda"); d.bay = "32:4"; d.pd_state = "UGood"
        # menu choice 2 (set JBOD), then confirm 'y'
        with patch("b2ctl.raid_actions._require_raid", return_value=True), \
             patch("builtins.input", side_effect=["2", "y"]), \
             patch("b2ctl.hba_raid.set_jbod", return_value=(True, "")) as jbod_mock, \
             patch("subprocess.run"):
            rc = ra.assign_perc(d, [d])
        assert rc == 0
        jbod_mock.assert_called_once_with("32:4", dry_run=False)

    def test_assign_perc_create_path_calls_add_vd(self):
        import b2ctl.raid_actions as ra
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda"); d.bay = "32:4"; d.pd_state = "UGood"
        d2 = Disk(dev="/dev/sda"); d2.bay = "32:5"; d2.pd_state = "UGood"
        # choice 3 (create), pick both drives, level raid1, two create confirms
        with patch("b2ctl.raid_actions._require_raid", return_value=True), \
             patch("builtins.input", side_effect=["3", "1 2", "raid1", "y", "y"]), \
             patch("b2ctl.hba_raid.add_vd", return_value=(True, "")) as add_mock:
            ra.assign_perc(d, [d, d2])
        add_mock.assert_called_once_with("raid1", ["32:4", "32:5"], dry_run=False)


class TestAuxAndBurninCommands(unittest.TestCase):
    """cache/log/burnin subcommands parse and honor --dry-run."""

    def test_parser_has_aux_and_burnin(self):
        import b2ctl.cli as cli
        p = cli.build_parser()
        for cmd, extra in (("cache-add", ["tank", "sde"]), ("cache-rm", ["tank", "sde"]),
                           ("log-add", ["tank", "sde"]), ("log-rm", ["tank", "sde"]),
                           ("burnin", ["sde"])):
            ns = p.parse_args([cmd] + extra)
            assert hasattr(ns, "func")

    def test_create_raid10_flag_parses(self):
        import b2ctl.cli as cli
        ns = cli.build_parser().parse_args(["create", "--raid10"])
        assert ns.raid10 is True

    def test_cache_add_honors_dry_run(self):
        import b2ctl.cli as cli
        import b2ctl.watch as watch
        watch._DRY_RUN = True
        try:
            with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
                 patch("b2ctl.zfs.add_cache", return_value=(True, "")) as mock_add:
                args = cli.build_parser().parse_args(["cache-add", "tank", "sde"])
                args.func(args)
            mock_add.assert_called_once_with("tank", ["/dev/disk/by-id/x"], dry_run=True)
        finally:
            watch._DRY_RUN = False

    def test_log_add_single_warns_and_calls(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
             patch("b2ctl.zfs.add_log", return_value=(True, "")) as mock_add:
            args = cli.build_parser().parse_args(["log-add", "tank", "sde"])
            args.func(args)
        mock_add.assert_called_once_with("tank", ["/dev/disk/by-id/x"], dry_run=False)


class TestInstallParity(unittest.TestCase):
    """`b2ctl install` mirrors ./install.sh: base / --with-tools / --perc / --flash."""

    def test_with_tools_flag_parses(self):
        import b2ctl.cli as cli
        ns = cli.build_parser().parse_args(["install", "--with-tools"])
        assert ns.with_tools is True

    def test_with_tools_and_perc_mutually_exclusive(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["install", "--with-tools", "--perc"])

    def test_dispatch_with_tools_installs_both(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.installer.install_tools") as it, \
             patch("b2ctl.installer.install_base") as ib:
            args = cli.build_parser().parse_args(["install", "--with-tools"])
            args.func(args)
        it.assert_called_once_with(["sas2ircu", "perccli"])
        ib.assert_not_called()

    def test_dispatch_no_flag_is_base(self):
        import b2ctl.cli as cli
        with patch("b2ctl.installer.install_base") as ib, \
             patch("b2ctl.installer.install_tools") as it:
            args = cli.build_parser().parse_args(["install"])
            args.func(args)
        ib.assert_called_once_with()
        it.assert_not_called()


class TestUpdateSync(unittest.TestCase):
    """`b2ctl update` syncs bay_map/ssd_spec to /etc without clobbering edits."""

    def _bundled(self, name):
        # the real bundled files live next to the b2ctl package (codes/<name>)
        import b2ctl.cli as cli
        return os.path.abspath(os.path.join(os.path.dirname(cli.__file__), "..", name))

    def test_sync_resource_created_when_missing(self):
        import b2ctl.cli as cli
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "bay_map.json")
        state = cli._sync_resource("bay_map.json", dest, force=False)
        assert state == "created"
        assert os.path.exists(dest)

    def test_sync_resource_current_when_identical(self):
        import shutil
        import b2ctl.cli as cli
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "bay_map.json")
        shutil.copy2(self._bundled("bay_map.json"), dest)
        assert cli._sync_resource("bay_map.json", dest, force=False) == "current"

    def test_sync_resource_customized_kept_without_force(self):
        import b2ctl.cli as cli
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "bay_map.json")
        with open(dest, "w") as f:
            f.write('{"operator": "edited"}')
        state = cli._sync_resource("bay_map.json", dest, force=False)
        assert state == "customized-kept"
        with open(dest) as f:
            assert "operator" in f.read()          # edits preserved
        assert not os.path.exists(dest + ".bak")   # nothing backed up

    def test_sync_resource_force_overwrites_with_backup(self):
        import b2ctl.cli as cli
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "bay_map.json")
        with open(dest, "w") as f:
            f.write('{"operator": "edited"}')
        state = cli._sync_resource("bay_map.json", dest, force=True)
        assert state.startswith("updated")
        assert os.path.exists(dest + ".bak")       # old copy backed up
        with open(dest + ".bak") as f:
            assert "operator" in f.read()

    def test_update_root_syncs_both_and_binds_config(self):
        import b2ctl.cli as cli
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        dest_bay = os.path.join(tmp, "bay_map.json")
        dest_spec = os.path.join(tmp, "ssd_spec.json")
        cfg_path = os.path.join(tmp, "config.json")
        managed = [("bay_map.json", dest_bay, "bay_map_path"),
                   ("ssd_spec.json", dest_spec, "ssd_spec_path")]
        with patch.object(cli, "_MANAGED", managed), \
             patch.object(cfg_mod, "CONFIG_PATH", cfg_path), \
             patch.object(cfg_mod, "STD_DIR", tmp), \
             patch("b2ctl.config.validate", return_value=[]), \
             patch("os.geteuid", return_value=0):
            args = cli.build_parser().parse_args(["update"])
            args.func(args)
        assert os.path.exists(dest_bay) and os.path.exists(dest_spec)
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert cfg["bay_map_path"] == dest_bay
        assert cfg["ssd_spec_path"] == dest_spec

    def test_update_non_root_does_not_write(self):
        import b2ctl.cli as cli
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        dest_bay = os.path.join(tmp, "bay_map.json")
        cfg_path = os.path.join(tmp, "config.json")
        managed = [("bay_map.json", dest_bay, "bay_map_path")]
        with patch.object(cli, "_MANAGED", managed), \
             patch.object(cfg_mod, "CONFIG_PATH", cfg_path), \
             patch.object(cfg_mod, "STD_DIR", tmp), \
             patch("b2ctl.config.validate", return_value=[]), \
             patch("os.geteuid", return_value=1000):
            args = cli.build_parser().parse_args(["update"])
            args.func(args)
        assert not os.path.exists(dest_bay)     # non-root skips the sync
        assert not os.path.exists(cfg_path)

    def test_update_parser_has_force(self):
        import b2ctl.cli as cli
        ns = cli.build_parser().parse_args(["update", "--force"])
        assert ns.force is True


if __name__ == "__main__":
    unittest.main()
