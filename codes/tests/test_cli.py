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
        import b2ctl.common as common
        common.set_dry_run(True)
        try:
            with patch("b2ctl.raid_actions._require_raid", return_value=True), \
                 patch("builtins.input", side_effect=["y", "y"]), \
                 patch("b2ctl.hba_raid.add_vd", return_value=(True, "")) as add_mock:
                ra.create_vd("raid1", ["32:0", "32:1"])
            add_mock.assert_called_once_with("raid1", ["32:0", "32:1"], 0, dry_run=True)
        finally:
            common.set_dry_run(False)

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
        jbod_mock.assert_called_once_with("32:4", 0, dry_run=False)

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
        add_mock.assert_called_once_with("raid1", ["32:4", "32:5"], 0, dry_run=False)


class TestAuxAndBurninCommands(unittest.TestCase):
    """cache/log/burnin subcommands parse and honor --dry-run."""

    def test_parser_has_aux_and_burnin(self):
        import b2ctl.cli as cli
        p = cli.build_parser()
        for cmd, extra in (("cache-add", ["tank", "sde"]), ("cache-rm", ["tank", "sde"]),
                           ("log-add", ["tank", "sde"]), ("log-rm", ["tank", "sde"]),
                           ("cache-replace", ["tank", "old", "sde"]),
                           ("log-replace", ["tank", "old", "sde"]),
                           ("burnin", ["sde"])):
            ns = p.parse_args([cmd] + extra)
            assert hasattr(ns, "func")

    def test_aux_replace_dispatches_resolved_tokens(self):
        # old resolves permissively, new resolves strictly; both flow to the action
        import b2ctl.cli as cli

        def _resolve(tokens, *, strict=False):
            return ["/dev/disk/by-id/NEW"] if strict else ["/dev/disk/by-id/OLD"]

        for verb, action in (("cache-replace", "cache_replace"),
                             ("log-replace", "log_replace")):
            with self.subTest(verb=verb):
                with patch("b2ctl.cli._resolve_devs", side_effect=_resolve), \
                     patch(f"b2ctl.zfs_actions.{action}", return_value=0) as mock_act:
                    args = cli.build_parser().parse_args([verb, "tank", "old", "sde"])
                    rc = args.func(args)
                mock_act.assert_called_once_with(
                    "tank", "/dev/disk/by-id/OLD", "/dev/disk/by-id/NEW")
                self.assertEqual(rc, 0)

    def test_aux_replace_aborts_when_new_unresolved(self):
        # strict resolution of the NEW disk fails -> return 1, never dispatch
        import b2ctl.cli as cli

        def _resolve(tokens, *, strict=False):
            return None if strict else ["/dev/disk/by-id/OLD"]

        with patch("b2ctl.cli._resolve_devs", side_effect=_resolve), \
             patch("b2ctl.zfs_actions.log_replace") as mock_act:
            args = cli.build_parser().parse_args(["log-replace", "tank", "old", "sdX"])
            rc = args.func(args)
        mock_act.assert_not_called()
        self.assertEqual(rc, 1)

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
             patch("b2ctl.common.confirm", return_value=True), \
             patch("b2ctl.zfs.add_log", return_value=(True, "")) as mock_add:
            args = cli.build_parser().parse_args(["log-add", "tank", "sde"])
            args.func(args)
        mock_add.assert_called_once_with("tank", ["/dev/disk/by-id/x"],
                                         raid_type=None, dry_run=False)

    def test_cache_add_requires_confirmation(self):
        # F-003: declining the prompt must NOT mutate the pool.
        import b2ctl.cli as cli
        with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
             patch("b2ctl.common.confirm", return_value=False), \
             patch("b2ctl.zfs.add_cache", return_value=(True, "")) as mock_add:
            args = cli.build_parser().parse_args(["cache-add", "tank", "sde"])
            rc = args.func(args)
        mock_add.assert_not_called()
        assert rc == 1

    def test_log_rm_requires_confirmation(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
             patch("b2ctl.common.confirm", return_value=False), \
             patch("b2ctl.zfs.remove_vdev", return_value=(True, "")) as mock_rm:
            args = cli.build_parser().parse_args(["log-rm", "tank", "sde"])
            rc = args.func(args)
        mock_rm.assert_not_called()
        assert rc == 1

    def test_burnin_cancel_dispatches_targets(self):
        import b2ctl.cli as cli
        with patch("b2ctl.burnin.cancel", return_value=0) as mock_cancel:
            args = cli.build_parser().parse_args(["burnin", "--cancel", "1:0", "1:1"])
            rc = args.func(args)
        mock_cancel.assert_called_once()
        self.assertEqual(mock_cancel.call_args[0][0], ["1:0", "1:1"])
        self.assertEqual(rc, 0)

    def test_burnin_cancel_all_dispatches(self):
        import b2ctl.cli as cli
        with patch("b2ctl.burnin.cancel_all", return_value=0) as mock_ca, \
             patch("b2ctl.burnin.cancel") as mock_c:
            args = cli.build_parser().parse_args(["burnin", "--cancel-all"])
            rc = args.func(args)
        mock_ca.assert_called_once()
        mock_c.assert_not_called()
        self.assertEqual(rc, 0)


class TestResolveDevsStrict(unittest.TestCase):
    """F-032: add paths must not pass unresolved / by-id-less tokens to zpool."""

    def test_strict_aborts_on_unresolved(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.core.scan_light", return_value=[]):
            self.assertIsNone(cli._resolve_devs(["sdX"], strict=True))

    def test_strict_aborts_on_empty_by_id(self):
        import b2ctl.cli as cli
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sdh", by_id="", serial="SNH")
        with patch("b2ctl.cli.core.scan_light", return_value=[d]):
            self.assertIsNone(cli._resolve_devs(["SNH"], strict=True))

    def test_strict_returns_by_id_when_present(self):
        import b2ctl.cli as cli
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sdh", by_id="/dev/disk/by-id/ata-X", serial="SNH")
        with patch("b2ctl.cli.core.scan_light", return_value=[d]):
            self.assertEqual(cli._resolve_devs(["SNH"], strict=True),
                             ["/dev/disk/by-id/ata-X"])

    def test_non_strict_passes_unresolved_through(self):
        # rm paths keep verbatim pass-through for raw zpool leaf tokens
        import b2ctl.cli as cli
        with patch("b2ctl.cli.core.scan_light", return_value=[]):
            self.assertEqual(cli._resolve_devs(["cache-leaf-token"]),
                             ["cache-leaf-token"])

    def test_cache_add_aborts_when_resolution_fails(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli._resolve_devs", return_value=None), \
             patch("b2ctl.zfs.add_cache") as mock_add:
            args = cli.build_parser().parse_args(["cache-add", "tank", "sdX"])
            rc = args.func(args)
        mock_add.assert_not_called()
        assert rc == 1


class TestConfigInitNonRoot(unittest.TestCase):
    """F-034: config init as non-root prints a clean error, no traceback."""

    def test_config_init_permission_error_clean(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.os.path.exists", return_value=False), \
             patch("b2ctl.cli.os.makedirs", side_effect=PermissionError("denied")):
            rc = cli._config_init(None)
        assert rc == 1

    def test_config_init_open_permission_error_clean(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.os.path.exists", return_value=False), \
             patch("b2ctl.cli.os.makedirs"), \
             patch("builtins.open", side_effect=PermissionError("denied")):
            rc = cli._config_init(None)
        assert rc == 1


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

    def test_sync_resource_missing_bundled(self):
        # F-072: an absent bundled source returns "missing-bundled" instead of
        # crashing with FileNotFoundError; nothing is written.
        import b2ctl.cli as cli
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "bay_map.json")
        state = cli._sync_resource("b2ctl_absent_bundle.json", dest, force=False)
        assert state == "missing-bundled"
        assert not os.path.exists(dest)

    def test_update_skips_binding_missing_bundled(self):
        # F-072: `b2ctl update` completes over a _MANAGED entry with no bundled
        # source (no traceback), does not bind config to a nonexistent path, and
        # still syncs the remaining present entry.
        import b2ctl.cli as cli
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        dest_absent = os.path.join(tmp, "absent.json")
        dest_spec = os.path.join(tmp, "ssd_spec.json")
        cfg_path = os.path.join(tmp, "config.json")
        managed = [("b2ctl_absent_bundle.json", dest_absent, "bay_map_path"),
                   ("ssd_spec.json", dest_spec, "ssd_spec_path")]
        with patch.object(cli, "_MANAGED", managed), \
             patch.object(cfg_mod, "CONFIG_PATH", cfg_path), \
             patch.object(cfg_mod, "STD_DIR", tmp), \
             patch("b2ctl.config.validate", return_value=[]), \
             patch("os.geteuid", return_value=0):
            args = cli.build_parser().parse_args(["update"])
            args.func(args)                       # must not raise
        assert not os.path.exists(dest_absent)    # missing bundled -> no copy
        assert os.path.exists(dest_spec)          # present entry still synced
        with open(cfg_path) as f:
            cfg = json.load(f)
        assert cfg["ssd_spec_path"] == dest_spec
        assert cfg.get("bay_map_path", "") != dest_absent   # not bound to a phantom


class TestLocate(unittest.TestCase):
    """`b2ctl locate` resolves a disk and blinks it (steady)."""

    def test_locate_calls_blink_disk_with_seconds(self):
        import b2ctl.cli as cli
        from b2ctl.common import Disk
        d = Disk(dev="/dev/nvme0n1")
        d.serial = "S1"
        with patch("b2ctl.core.scan_light", return_value=[d]), \
             patch("b2ctl.locate.blink_disk", return_value=(True, "ledctl")) as bd:
            ns = cli.build_parser().parse_args(["locate", "S1", "6"])
            rc = ns.func(ns)
        assert rc == 0
        bd.assert_called_once_with(d, 6)

    def test_locate_parser_has_no_pulse(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["locate", "S1", "--pulse", "2:2"])

    def test_locate_seconds_rejects_negative(self):
        # F-073: a negative blink duration must be rejected at parse time via the
        # _pos_int type, so time.sleep never crashes and leaks dd readers.
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["locate", "S1", "-1"])


class TestStatusParser(unittest.TestCase):
    """status subparser: --json vs --locate exclusivity + positive --seconds."""

    def test_status_json_locate_rejected(self):
        # F-069: --json and --locate are mutually exclusive (silently dropping
        # the LED intent behind machine output is dishonest).
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["status", "--json", "--locate"])

    def test_status_seconds_rejects_negative(self):
        # F-073: --seconds uses the _pos_int type too.
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["status", "--locate", "--seconds", "-1"])


class TestCheckOutput(unittest.TestCase):
    """F-071: `b2ctl check` reports mapped disks + enclosures, not the number of
    distinct bay labels mislabelled as 'Controllers found'."""

    def test_check_reports_bays_mapped_not_controllers(self):
        import b2ctl.cli as cli

        class _FakeBackend:
            name = "it"
            def have_tool(self):
                return True
            def bay_map(self):
                return {f"SN{i}": f"1:{i}" for i in range(6)}   # 6 disks, one enclosure

        with patch("b2ctl.cli.run", return_value=""), \
             patch("b2ctl.backend.get_backend", return_value=_FakeBackend()), \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            cli._check(None)
            text = out.getvalue()
        self.assertIn("Bays mapped: 6 disks", text)
        self.assertIn("across 1 enclosure(s)", text)
        self.assertNotIn("Controllers found", text)


class TestBurninCli(unittest.TestCase):
    """burnin: multiple targets (nargs) + --status re-attach."""

    def test_accepts_multiple_targets(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["burnin", "1:4", "1:5", "--scan"])
        self.assertEqual(ns.target, ["1:4", "1:5"])
        self.assertTrue(ns.scan)

    def test_status_flag_calls_status_view(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["burnin", "--status"])
        with patch("b2ctl.burnin.status_view", return_value=0) as sv, \
             patch("b2ctl.burnin.run_multi") as rm:
            rc = cli._burnin(ns)
        self.assertEqual(rc, 0)
        sv.assert_called_once()
        rm.assert_not_called()

    def test_multi_target_dispatches_run_multi(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["burnin", "1:4", "1:5"])
        with patch("b2ctl.burnin.run_multi", return_value=0) as rm, \
             patch("b2ctl.spec.load", return_value={}):
            cli._burnin(ns)
        self.assertEqual(rm.call_args[0][0], ["1:4", "1:5"])


class TestMaintVerbs(unittest.TestCase):
    """v0.17.0: scrub / trim / maint --log + aux --size / log-add topology flags."""

    def test_scrub_parse_and_dispatch(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.zfs_actions.scrub", return_value=0) as m:
            args = cli.build_parser().parse_args(["scrub", "tank"])
            rc = args.func(args)
        m.assert_called_once_with("tank")
        self.assertEqual(rc, 0)

    def test_trim_parse_and_dispatch(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.zfs_actions.trim", return_value=0) as m:
            args = cli.build_parser().parse_args(["trim"])       # pool optional
            args.func(args)
        m.assert_called_once_with(None)

    def test_maint_log_reads_events(self):
        import b2ctl.cli as cli
        rows = [{"ts": "2026-07-08T03:00:00", "kind": "scrub", "target": "tank",
                 "status": "ok", "detail": "done"}]
        with patch("b2ctl.maint.load_events", return_value=rows) as m:
            args = cli.build_parser().parse_args(["maint", "--log"])
            rc = args.func(args)
        m.assert_called_once()
        self.assertEqual(rc, 0)

    def test_log_add_raid10_flag(self):
        import b2ctl.cli as cli
        args = cli.build_parser().parse_args(["log-add", "tank", "a", "b", "--raid10"])
        self.assertTrue(args.raid10)
        self.assertFalse(args.mirror)

    def test_log_add_mirror_raid10_mutually_exclusive(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["log-add", "tank", "a", "--mirror", "--raid10"])

    def test_cache_add_size_flag(self):
        import b2ctl.cli as cli
        args = cli.build_parser().parse_args(["cache-add", "tank", "sde", "--size", "512G"])
        self.assertEqual(args.size, "512G")

    def test_scrub_requires_root_maint_exempt(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.need_root") as nr, \
             patch("b2ctl.cli.zfs_actions.scrub", return_value=0):
            cli.main(["scrub", "tank"])
        nr.assert_called_once()                 # scrub mutates -> root required
        with patch("b2ctl.cli.need_root") as nr2, \
             patch("b2ctl.maint.load_events", return_value=[]):
            cli.main(["maint", "--log"])
        nr2.assert_not_called()                 # read-only history view -> exempt


if __name__ == "__main__":
    unittest.main()
