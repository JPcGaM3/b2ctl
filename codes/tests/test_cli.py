"""Unit tests for b2ctl.cli — log reading + rollback messaging."""
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from b2ctl import common
from b2ctl import zfs
from b2ctl import cli as cli_mod


def _mock_hardware(stack: ExitStack) -> SimpleNamespace:
    """Patch every hardware-facing seam a read verb might touch, so `cli.main`
    runs on a dev laptop with no real disks/controller/PERC (ADR-007's machine
    contract must be testable without hardware). Mirrors the idiom already used
    by test_core.py's scan() patches and test_schema.py's TestBackendJson.
    Returns the fake backend in case a test wants to tweak it further."""
    fake_bk = SimpleNamespace(name="it", bay_source=None, raid_volumes=lambda: [])
    stack.enter_context(patch("b2ctl.core.scan", return_value=[]))
    stack.enter_context(patch("b2ctl.core.scan_light", return_value=[]))
    stack.enter_context(patch("b2ctl.zfs.list_pools", return_value=[]))
    stack.enter_context(patch("b2ctl.backend.get_backend", return_value=fake_bk))
    stack.enter_context(patch("b2ctl.config.controller_mode", return_value="it"))
    stack.enter_context(patch("b2ctl.hba_raid.have_tool", return_value=False))
    stack.enter_context(patch("b2ctl.hba_raid.foreign_config", return_value=[]))
    stack.enter_context(patch("b2ctl.hba_raid.foreign_bays", return_value=set()))
    stack.enter_context(patch("b2ctl.maint.load_events", return_value=[]))
    stack.enter_context(patch("b2ctl.safety.load_log", return_value=[]))
    stack.enter_context(patch("b2ctl.config.load_bay_map", return_value=[]))
    stack.enter_context(patch("b2ctl.config.bay_map_write_path",
                              return_value="/nonexistent/b2ctl-test/bay_map.json"))
    return fake_bk


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
                           ("maint", ["health", "sde"])):
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
            args = cli.build_parser().parse_args(["maint", "health", "--cancel", "1:0", "1:1"])
            rc = args.func(args)
        mock_cancel.assert_called_once()
        self.assertEqual(mock_cancel.call_args[0][0], ["1:0", "1:1"])
        self.assertEqual(rc, 0)

    def test_burnin_cancel_all_dispatches(self):
        import b2ctl.cli as cli
        with patch("b2ctl.burnin.cancel_all", return_value=0) as mock_ca, \
             patch("b2ctl.burnin.cancel") as mock_c:
            args = cli.build_parser().parse_args(["maint", "health", "--cancel-all"])
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
    """`maint health` (formerly burnin): multiple targets + --status re-attach."""

    def test_accepts_multiple_targets(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["maint", "health", "1:4", "1:5", "--scan"])
        self.assertEqual(ns.target, ["1:4", "1:5"])
        self.assertTrue(ns.scan)

    def test_status_flag_calls_status_view(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["maint", "health", "--status"])
        with patch("b2ctl.burnin.status_view", return_value=0) as sv, \
             patch("b2ctl.burnin.run_multi") as rm:
            rc = cli._burnin(ns)
        self.assertEqual(rc, 0)
        sv.assert_called_once()
        rm.assert_not_called()

    def test_multi_target_dispatches_run_multi(self):
        from b2ctl import cli
        ns = cli.build_parser().parse_args(["maint", "health", "1:4", "1:5"])
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

    def test_over_provision_confirms_wipe_before_touching_disk(self):
        # §9: declining the over-provision wipe confirm must WIPE nothing, add
        # nothing, and return 1 — the confirm precedes any destructive call.
        import b2ctl.cli as cli
        with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
             patch("b2ctl.core.scan_light", return_value=[]), \
             patch("b2ctl.common.confirm", return_value=False), \
             patch("b2ctl.zfs.wipe", return_value=(True, "")) as wipe, \
             patch("b2ctl.zfs.partition", return_value=(True, "x-part1")) as part, \
             patch("b2ctl.zfs.add_cache", return_value=(True, "")) as add:
            args = cli.build_parser().parse_args(
                ["cache-add", "tank", "sde", "--size", "512G"])
            rc = args.func(args)
        wipe.assert_not_called()
        part.assert_not_called()
        add.assert_not_called()
        self.assertEqual(rc, 1)

    def test_over_provision_wipes_then_partitions_on_confirm(self):
        # confirm=True: wipe runs before partition, cache added with -part1 token.
        import b2ctl.cli as cli
        calls = []
        with patch("b2ctl.cli._resolve_devs", return_value=["/dev/disk/by-id/x"]), \
             patch("b2ctl.core.scan_light", return_value=[]), \
             patch("b2ctl.common.confirm", return_value=True), \
             patch("b2ctl.zfs.wipe", side_effect=lambda *a, **k: calls.append("wipe") or (True, "")), \
             patch("b2ctl.zfs.partition",
                   side_effect=lambda *a, **k: calls.append("part") or (True, "x-part1")), \
             patch("b2ctl.zfs.add_cache", return_value=(True, "")) as add:
            args = cli.build_parser().parse_args(
                ["cache-add", "tank", "sde", "--size", "512G"])
            args.func(args)
        self.assertEqual(calls, ["wipe", "part"])          # wipe strictly before partition
        add.assert_called_once_with("tank", ["x-part1"], dry_run=False)

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

    # ---- v0.18.0: unified `maint` subcommands ----
    def test_maint_scrub_dispatches(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.zfs_actions.scrub", return_value=0) as m:
            args = cli.build_parser().parse_args(["maint", "scrub", "tank"])
            args.func(args)
        m.assert_called_once_with("tank")

    def test_maint_trim_dispatches(self):
        import b2ctl.cli as cli
        with patch("b2ctl.cli.zfs_actions.trim", return_value=0) as m:
            args = cli.build_parser().parse_args(["maint", "trim"])
            args.func(args)
        m.assert_called_once_with(None)

    def test_maint_health_dispatches_run_multi(self):
        import b2ctl.cli as cli
        args = cli.build_parser().parse_args(["maint", "health", "1:4", "--scan"])
        with patch("b2ctl.burnin.run_multi", return_value=0) as rm, \
             patch("b2ctl.maint.log_event") as le, \
             patch("b2ctl.spec.load", return_value={}):
            args.func(args)
        self.assertEqual(rm.call_args[0][0], ["1:4"])
        self.assertTrue(rm.call_args.kwargs.get("do_scan"))
        # started event logged so `maint --log` sees CLI-launched health-checks
        self.assertEqual(le.call_args[0][:3], ("health", "1:4", "started"))
        self.assertIn("badblocks", le.call_args[0][3])

    def test_maint_health_logs_per_target(self):
        import b2ctl.cli as cli
        args = cli.build_parser().parse_args(["maint", "health", "1:4", "sde"])
        with patch("b2ctl.burnin.run_multi", return_value=0), \
             patch("b2ctl.maint.log_event") as le, \
             patch("b2ctl.spec.load", return_value={}):
            args.func(args)
        targets = [c[0][1] for c in le.call_args_list]
        self.assertEqual(targets, ["1:4", "sde"])
        # long (no --short) + no --scan -> plain long self-test
        self.assertEqual(le.call_args_list[0][0][3], "smartctl -t long")

    def test_maint_health_dryrun_no_maint_log(self):
        # dry-run must not write a phantom 'started' event to the maint log.
        import b2ctl.cli as cli
        args = cli.build_parser().parse_args(["maint", "health", "1:4"])
        with patch("b2ctl.burnin.run_multi", return_value=0), \
             patch("b2ctl.watch._DRY_RUN", True), \
             patch("b2ctl.maint.log_event") as le, \
             patch("b2ctl.spec.load", return_value={}):
            args.func(args)
        le.assert_not_called()

    def test_burnin_verb_removed(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["burnin", "sde"])

    def test_maint_root_gating(self):
        import b2ctl.cli as cli
        # bare `maint` + `maint health --status` are read-only -> exempt;
        # `maint scrub|trim|health <dev>` mutate -> root required.
        for argv, exempt in ((["maint"], True),
                             (["maint", "--log"], True),
                             (["maint", "health", "--status"], True),
                             (["maint", "scrub", "tank"], False),
                             (["maint", "trim"], False),
                             (["maint", "health", "1:4"], False)):
            with self.subTest(argv=argv):
                ns = cli.build_parser().parse_args(argv)
                self.assertEqual(cli._needs_root(ns), not exempt)

    def test_raid_foreign_routes_to_raid_actions(self):
        """F-135: `raid-foreign` maps to the show/import/clear contract."""
        import b2ctl.cli as cli
        for argv, action, ctrl in ((["raid-foreign"], "show", None),
                                   (["raid-foreign", "--import"], "import", None),
                                   (["raid-foreign", "--clear"], "clear", None),
                                   (["raid-foreign", "--clear", "-c", "1"], "clear", 1)):
            with self.subTest(argv=argv):
                ns = cli.build_parser().parse_args(argv)
                with patch("b2ctl.raid_actions.foreign", return_value=0) as fn:
                    ns.func(ns)
                fn.assert_called_once_with(action, ctrl)

    def test_raid_foreign_import_and_clear_are_exclusive(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["raid-foreign", "--import", "--clear"])

    def test_status_pager_flags_parse(self):
        import b2ctl.cli as cli
        ns = cli.build_parser().parse_args(["status", "--full", "--no-pager"])
        self.assertTrue(ns.full)
        self.assertTrue(ns.no_pager)
        plain = cli.build_parser().parse_args(["status"])
        self.assertFalse(plain.full)
        self.assertFalse(plain.no_pager)

    def test_raid_foreign_root_gating(self):
        """Bare `raid-foreign` is `perccli /cN/fall show` — read-only (§9)."""
        import b2ctl.cli as cli
        for argv, exempt in ((["raid-foreign"], True),
                             (["raid-foreign", "-c", "1"], True),
                             (["raid-foreign", "--import"], False),
                             (["raid-foreign", "--clear"], False)):
            with self.subTest(argv=argv):
                ns = cli.build_parser().parse_args(argv)
                self.assertEqual(cli._needs_root(ns), not exempt)


class TestPager(unittest.TestCase):
    """F-137: cli._page hands tall output to a pager, but must never eat it."""

    _TALL = "\n".join(f"line {i}" for i in range(200))

    def _run(self, text, *, tty, no_pager=False, pager="less", which=True,
             lines=24, spawn=None):
        import b2ctl.cli as cli

        class _Out:
            def isatty(self):
                return tty

        env = {"PAGER": pager} if pager is not None else {}
        with patch.object(cli.sys, "stdout", _Out()), \
             patch.dict(os.environ, env, clear=False), \
             patch.object(cli.shutil, "which",
                          return_value="/usr/bin/less" if which else None), \
             patch.object(cli.shutil, "get_terminal_size",
                          return_value=os.terminal_size((100, lines))), \
             patch("subprocess.run", side_effect=spawn) as sp, \
             patch("builtins.print") as pr:
            if pager is None:
                os.environ.pop("PAGER", None)
            cli._page(text, no_pager=no_pager)
        return sp, pr

    def test_non_tty_never_pages(self):
        sp, pr = self._run(self._TALL, tty=False)
        sp.assert_not_called()
        pr.assert_called_once()

    def test_no_pager_flag_prints_plain(self):
        sp, pr = self._run(self._TALL, tty=True, no_pager=True)
        sp.assert_not_called()
        pr.assert_called_once()

    def test_short_output_is_not_paged(self):
        sp, pr = self._run("one\ntwo", tty=True)
        sp.assert_not_called()
        pr.assert_called_once()

    def test_tall_output_goes_to_the_pager(self):
        sp, pr = self._run(self._TALL, tty=True)
        sp.assert_called_once()
        self.assertEqual(sp.call_args.args[0][0], "less")
        pr.assert_not_called()

    def test_missing_pager_binary_falls_back_to_print(self):
        sp, pr = self._run(self._TALL, tty=True, which=False)
        sp.assert_not_called()
        pr.assert_called_once()          # output is never lost

    def test_unspawnable_pager_falls_back_to_print(self):
        sp, pr = self._run(self._TALL, tty=True, spawn=OSError("boom"))
        sp.assert_called_once()
        pr.assert_called_once()


class TestJsonEnvelopeEveryReadVerb(unittest.TestCase):
    """v0.22.0 machine contract (ADR-007): every read verb must put ONE JSON
    envelope on stdout and nothing else — a stray print() from anywhere on the
    read path corrupts the stream for an MCP/web client (F-139). Table-driven
    over the whole read surface; `_mock_hardware` stands in for the disks/
    controller so this runs with no real hardware."""

    ENVELOPE_KEYS = {"schema_version", "ok", "command", "data", "warnings", "error"}

    # (argv, expected command field)
    CASES = (
        (["status", "--json"], "status"),
        (["disks", "--json"], "disks"),
        (["pools", "--json"], "pools"),
        (["volumes", "--json"], "volumes"),
        (["bays", "--json"], "bays"),
        (["check", "--json"], "check"),
        (["log", "--json"], "log"),
        (["version", "--json"], "version"),
        (["raid-foreign", "--json"], "raid-foreign"),
        (["maint", "--log", "--json"], "maint"),
        (["config", "show", "--json"], "config"),
    )

    def setUp(self):
        common.set_json_mode(False)
        common.take_warnings()

    def tearDown(self):
        common.set_json_mode(False)
        common.take_warnings()

    def test_every_read_verb_emits_one_parseable_envelope(self):
        import b2ctl.cli as cli
        for argv, expected_command in self.CASES:
            with self.subTest(argv=argv):
                with ExitStack() as stack:
                    stack.enter_context(patch("os.geteuid", return_value=0))
                    _mock_hardware(stack)
                    buf = io.StringIO()
                    with patch("sys.stdout", buf):
                        rc = cli.main(argv)
                raw = buf.getvalue()
                # The critical assertion: json.loads on the WHOLE capture proves
                # nothing else landed on stdout alongside the envelope.
                out = json.loads(raw)
                self.assertEqual(set(out), self.ENVELOPE_KEYS)
                self.assertEqual(out["command"], expected_command)
                self.assertIs(out["ok"], True)
                self.assertEqual(out["schema_version"], 1)
                self.assertEqual(out["error"], None)
                self.assertEqual(rc, 0)


class TestJsonWarningsCaptured(unittest.TestCase):
    """--json must route non-fatal notices into the envelope's warnings[]
    instead of printing them (ADR-007) — exercised through cli.main() end to
    end, on top of jsonout's own unit coverage in test_jsonout.py."""

    def setUp(self):
        common.set_json_mode(False)
        common.take_warnings()

    def tearDown(self):
        common.set_json_mode(False)
        common.take_warnings()

    def _run(self, argv, scan_side_effect):
        import b2ctl.cli as cli
        with ExitStack() as stack:
            stack.enter_context(patch("os.geteuid", return_value=0))
            _mock_hardware(stack)
            # override the no-op scan() from _mock_hardware with one that warns
            stack.enter_context(patch("b2ctl.core.scan", side_effect=scan_side_effect))
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(argv)
        return rc, json.loads(buf.getvalue())

    def test_warning_lands_in_envelope_and_stdout_still_parses(self):
        def scan(*_a, **_kw):
            common.warn("bay_map.json unreadable — using scrambled raw slots")
            return []
        rc, out = self._run(["status", "--json"], scan)
        self.assertEqual(rc, 0)
        self.assertEqual(out["warnings"],
                         ["bay_map.json unreadable — using scrambled raw slots"])

    def test_same_message_twice_dedups_to_one_entry(self):
        # common.warn() dedups within one run (F-139) — assert that holds
        # through the full cli.main() path, not just the unit-level warn() call.
        def scan(*_a, **_kw):
            common.warn("dup notice")
            common.warn("dup notice")
            return []
        _, out = self._run(["status", "--json"], scan)
        self.assertEqual(out["warnings"], ["dup notice"])

    def test_warnings_do_not_leak_into_the_next_command(self):
        def warning_scan(*_a, **_kw):
            common.warn("first-run only")
            return []
        rc1, out1 = self._run(["status", "--json"], warning_scan)
        self.assertEqual(out1["warnings"], ["first-run only"])
        rc2, out2 = self._run(["status", "--json"], lambda *_a, **_kw: [])
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(out2["warnings"], [])


class TestJsonFlagPositionMatrix(unittest.TestCase):
    """F-139 / ADR-007: --json is declared BOTH globally (on the top parser)
    and on every subparser (`_add_json_flag` recurses into `maint`/`config`),
    each with default=argparse.SUPPRESS.

    Why SUPPRESS: argparse merges the top-level namespace with the matched
    subparser's own defaults. If a subparser declared `--json` with its
    ordinary `default=False`, that False would UNCONDITIONALLY overwrite the
    True the top-level `--json` already set — so `b2ctl --json status` would
    silently fall through to the table renderer instead of the envelope. With
    default=SUPPRESS the subparser copy only ever sets the attribute when
    --json is literally present at THAT position, so whichever position
    actually supplies the flag wins and neither copy can clobber the other.
    This table is the regression test for exactly that trap.
    """

    CASES = (
        (["--json", "status"], True),
        (["status", "--json"], True),
        (["status"], False),
        (["--json", "maint", "--log"], True),
        (["maint", "--log", "--json"], True),
        (["--json", "config", "show"], True),
        (["config", "show", "--json"], True),
    )

    def test_json_resolves_the_same_regardless_of_position(self):
        import b2ctl.cli as cli
        for argv, expected in self.CASES:
            with self.subTest(argv=argv):
                ns = cli.build_parser().parse_args(argv)
                self.assertEqual(bool(getattr(ns, "json", False)), expected)


class TestJsonLocateMutexBothPositions(unittest.TestCase):
    """F-069: --locate (blinks a physical LED) and --json (machine output)
    must never combine. argparse's mutually-exclusive group lives INSIDE the
    `status` subparser, so it only catches `status --json --locate` — it
    cannot see `--json status --locate`, where --json is supplied by the
    top-level parser instead (a mutex group can't span parsers). cli._status()
    re-checks at runtime so the rule holds at BOTH positions (ADR-007)."""

    def test_json_after_status_rejected_by_argparse(self):
        import b2ctl.cli as cli
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["status", "--json", "--locate"])

    def test_json_before_status_caught_at_runtime(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=0):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["--json", "status", "--locate"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "INVALID_ARG")


class TestJsonNeedsRootEnvelope(unittest.TestCase):
    """A machine caller can't read stderr text: NEEDS_ROOT must come back as
    part of the envelope (rc 1 + error.code), not a bare die()-to-stderr exit
    that only a terminal operator could read (ADR-007)."""

    def test_non_root_json_returns_needs_root_envelope(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=1000):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["disks", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "NEEDS_ROOT")


class TestJsonBaysVerb(unittest.TestCase):
    """`bays` (ADR-007): a malformed --set value and the interactive-only
    forms (--calibrate; raid-foreign --clear, mutation is phase 2) must refuse
    cleanly under --json instead of hanging a machine caller on a prompt or a
    ValueError traceback."""

    def test_set_without_equals_is_invalid_arg(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=0):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["bays", "--set", "32:0", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "INVALID_ARG")

    def test_calibrate_json_is_unsupported(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=0):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["bays", "--calibrate", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "UNSUPPORTED")

    def test_raid_foreign_clear_json_is_unsupported(self):
        import b2ctl.cli as cli
        with patch("os.geteuid", return_value=0):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["raid-foreign", "--clear", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "UNSUPPORTED")

    def test_read_form_returns_expected_data_keys(self):
        import b2ctl.cli as cli
        with ExitStack() as stack:
            stack.enter_context(patch("os.geteuid", return_value=0))
            stack.enter_context(patch("b2ctl.config.load_bay_map", return_value=[]))
            stack.enter_context(patch("b2ctl.config.bay_map_write_path",
                                      return_value="/nonexistent/b2ctl-test/bay_map.json"))
            stack.enter_context(patch("b2ctl.core.scan_light", return_value=[]))
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["bays", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertIs(out["ok"], True)
        self.assertEqual(set(out["data"]),
                         {"panels", "path", "write_path", "detected_slots", "disks"})


if __name__ == "__main__":
    unittest.main()


class TestSilentZpoolIsNotAnEmptyMachine(unittest.TestCase):
    """F-143: `zpool` not answering must be a typed failure, never `pools: []`
    with ok:true. A poller that trusts an empty list will conclude the machine
    has no pools at the exact moment it has lost sight of them."""

    def setUp(self):
        common.set_json_mode(False)
        common.take_warnings()

    tearDown = setUp

    def _run(self, argv, stack):
        stack.enter_context(patch("os.geteuid", return_value=0))
        _mock_hardware(stack)
        stack.enter_context(patch("b2ctl.zfs.list_pools",
                                  side_effect=zfs.ZfsUnavailable("zpool: not found")))
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_mod.main(argv)
        return rc, buf.getvalue()

    def test_pools_json_reports_tool_missing_not_an_empty_list(self):
        with ExitStack() as stack:
            rc, raw = self._run(["pools", "--json"], stack)
        out = json.loads(raw)                    # still ONE parseable envelope
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "TOOL_MISSING")
        self.assertEqual(out["command"], "pools")
        self.assertEqual(out["data"], None)
        self.assertEqual(rc, 1)

    def test_status_json_fails_rather_than_publishing_a_partial_picture(self):
        with ExitStack() as stack:
            rc, raw = self._run(["status", "--json"], stack)
        out = json.loads(raw)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "TOOL_MISSING")
        self.assertEqual(rc, 1)

    def test_human_status_still_prints_the_disk_table(self):
        # The operator is looking at `status` BECAUSE something is wrong — the
        # disk table is how they diagnose it, so the human face degrades (empty
        # pool/summary blocks) instead of dying. Safety comes from is_poolable,
        # not from withholding the table.
        with ExitStack() as stack:
            stack.enter_context(patch("os.geteuid", return_value=0))
            _mock_hardware(stack)
            stack.enter_context(patch("b2ctl.zfs.list_pools",
                                      side_effect=zfs.ZfsUnavailable("boom")))
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["status"])
        self.assertEqual(rc, 0)
        self.assertIn("BAY", buf.getvalue())     # the table rendered


class TestJsonCrashSafety(unittest.TestCase):
    """F-146: a --json call must always answer with an envelope, even when the
    process would otherwise die/crash/interrupt before completing."""

    def test_system_exit_under_json_becomes_an_envelope(self):
        # backend.get_backend() -> common.die() (SystemExit, NOT an Exception)
        # used to leave a --json caller with nothing at all on stdout.
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan", side_effect=SystemExit(1)):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["status", "--json"])
        out = json.loads(buf.getvalue())          # the critical assertion
        self.assertIs(out["ok"], False)
        self.assertIn(out["error"]["code"], ("NO_BACKEND", "TOOL_MISSING"))
        self.assertEqual(rc, 1)

    def test_system_exit_without_json_still_propagates_unchanged(self):
        # The human face must see byte-for-byte today's behaviour: SystemExit
        # is not swallowed, it propagates exactly as before.
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan", side_effect=SystemExit(1)):
            with self.assertRaises(SystemExit):
                cli_mod.main(["status"])

    def test_unexpected_exception_becomes_parse_error_not_a_traceback(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan", side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["status", "--json"])
        out = json.loads(buf.getvalue())
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "PARSE_ERROR")
        self.assertIn("boom", out["error"]["message"])
        self.assertEqual(rc, 1)

    def test_sigint_under_json_emits_envelope_not_ansi(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan", side_effect=KeyboardInterrupt):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["status", "--json"])
        out = json.loads(buf.getvalue())
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "OP_FAILED")
        self.assertEqual(rc, 1)

    def test_sigint_human_path_still_130(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan", side_effect=KeyboardInterrupt), \
             patch("sys.stdout", new_callable=io.StringIO):
            rc = cli_mod.main(["status"])
        self.assertEqual(rc, 130)


class TestMaintHealthJsonNeverBlocks(unittest.TestCase):
    """F-146: `maint health --status --json` used to route into `_json_mutation`,
    which runs the handler inside a redirected stdout while it calls burnin's
    live view (a `while True` redraw loop) — the request never returns. Starting
    a NEW health-check under --json ends in the same mandatory live_view() call
    inside burnin.run_multi()."""

    def test_status_json_returns_and_never_touches_the_live_view(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.burnin.status_payload",
                   return_value=[{"dev": "/dev/sde", "verdict": "PASS"}]), \
             patch("b2ctl.burnin.live_view",
                   side_effect=AssertionError("must never call live_view")), \
             patch("b2ctl.burnin.status_view",
                   side_effect=AssertionError("must never call status_view")):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["maint", "health", "--status", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertIs(out["ok"], True)
        self.assertEqual(out["data"]["health"],
                         [{"dev": "/dev/sde", "verdict": "PASS"}])

    def test_starting_a_new_check_under_json_starts_and_returns(self):
        """Starting a health-check IS machine-callable. burn-in has been
        non-blocking by design since ADR-002 — it exits 0 once the tests are
        STARTED and the verdict is read later from --status — so the fix is to
        skip the live view, not to refuse the verb (F-146)."""
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.burnin.run_multi", return_value=0) as rm, \
             patch("b2ctl.burnin.live_view",
                   side_effect=AssertionError("must never call live_view")):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["maint", "health", "1:4", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertIs(out["ok"], True)
        rm.assert_called_once()                  # it really did start

    def test_burnin_skips_the_live_view_when_nobody_is_watching(self):
        # The guard itself, at the burnin layer: json mode or --confirm means
        # there is no terminal to redraw for.
        from b2ctl import burnin, common
        try:
            common.set_json_mode(True)
            self.assertTrue(burnin._unwatched())
            common.set_json_mode(False)
            common.set_auto_confirm("yes")
            self.assertTrue(burnin._unwatched())
            common.set_auto_confirm(None)
            self.assertFalse(burnin._unwatched())     # a real terminal: attach
        finally:
            common.set_json_mode(False)
            common.set_auto_confirm(None)
            common.take_warnings()


class TestRollbackReturnsExplicitInt(unittest.TestCase):
    """F-146: every path through _rollback_cmd must return an explicit int —
    it used to fall off the end returning None on SUCCESS, which
    `_json_mutation` reads as a non-zero rc and reports OP_FAILED even though
    the rollback ran and worked."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_entry(self, entry):
        import b2ctl.safety as safety
        safety.LOG_FILE = os.path.join(self.tmp, "ops.jsonl")
        with open(safety.LOG_FILE, "w") as f:
            f.write(json.dumps(entry) + "\n")

    def test_json_rollback_success_emits_ok_true(self):
        import b2ctl.cli as cli
        self._write_entry({
            "op_id": "20260617-replace", "op": "replace",
            "disk_serial": "X", "disk_bay": 1, "pool": "tank",
            "status": "ok", "started_at": "2026-06-17T10:00:00",
            "dev_path": "/dev/disk/by-id/x", "vdev": "raidz1-0",
            "cmds": [], "exit_code": 0, "stdout": "", "stderr": "",
            "ended_at": None,
            "rollback_hint": "zpool detach tank /dev/disk/by-id/x",
            "snapshot_path": None,
        })
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.common.run_check", return_value=(True, "detached")), \
             patch("b2ctl.safety.begin_op", return_value="rb-1"), \
             patch("b2ctl.safety.end_op"):
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli.main(["rollback", "20260617-replace",
                              "--json", "--confirm", "yes"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 0)
        self.assertIs(out["ok"], True)

    def test_rollback_hint_outside_write_cmds_is_refused_and_nothing_runs(self):
        # F-146: the rollback hint for an irreversible op is english PROSE
        # ("aux vdev repair on tank: verify `zpool status tank` — …"), which
        # naive whitespace-splitting turns into an argv with no verb allowlist.
        import b2ctl.cli as cli
        self._write_entry({
            "op_id": "20260617-auxrepair", "op": "aux-repair",
            "disk_serial": "X", "disk_bay": 1, "pool": "tank",
            "status": "ok", "started_at": "2026-06-17T10:00:00",
            "dev_path": "", "vdev": "cache",
            "cmds": [], "exit_code": 0, "stdout": "", "stderr": "",
            "ended_at": None,
            "rollback_hint": "aux vdev repair on tank: verify `zpool status tank`",
            "snapshot_path": None,
        })
        with patch("b2ctl.common.run_check") as run_mock, \
             patch("b2ctl.safety.begin_op") as begin_mock:
            rc = cli._rollback_cmd("20260617-auxrepair")
        self.assertEqual(rc, 1)
        run_mock.assert_not_called()
        begin_mock.assert_not_called()

    def test_rollback_stays_root_exempt(self):
        # F-146 dropped rollback from _ROOT_EXEMPT because it mutates; F-149 put
        # it back. It worked without root before (the stored zpool command fails
        # on its own if you lack the privilege), and "what already worked must
        # keep working" outranks tidying the exempt list. The mutation is still
        # gated by the WRITE_CMDS allowlist and a confirm.
        import b2ctl.cli as cli
        ns = cli.build_parser().parse_args(["rollback", "some-op"])
        self.assertFalse(cli._needs_root(ns))


class TestJsonPoolAndDiskNotFound(unittest.TestCase):
    """F-146: resolve a named pool/disk BEFORE handing a --json mutation off,
    so an unknown target reports POOL_NOT_FOUND/DISK_NOT_FOUND instead of the
    generic OP_FAILED `_json_mutation` would otherwise report once the
    underlying verb's own interactive handling declines."""

    def test_json_destroy_nonexistent_pool_is_pool_not_found(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.zfs.list_pools", return_value=[{"name": "tank"}]), \
             patch("b2ctl.zfs_actions.destroy") as destroy_mock:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["destroy", "tonk", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "POOL_NOT_FOUND")
        destroy_mock.assert_not_called()

    def test_json_locate_unknown_target_is_disk_not_found(self):
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.core.scan_light", return_value=[]), \
             patch("b2ctl.locate.blink_disk") as blink_mock:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["locate", "bay-99", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "DISK_NOT_FOUND")
        blink_mock.assert_not_called()

    def test_silent_zpool_is_tool_missing_not_pool_not_found(self):
        # F-143's rule extends here: an unanswered zpool is UNKNOWN, never
        # "not found" — never let this precheck report POOL_NOT_FOUND for it.
        with patch("os.geteuid", return_value=0), \
             patch("b2ctl.zfs.list_pools",
                   side_effect=zfs.ZfsUnavailable("boom")), \
             patch("b2ctl.zfs_actions.destroy") as destroy_mock:
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["destroy", "tank", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertEqual(out["error"]["code"], "TOOL_MISSING")
        destroy_mock.assert_not_called()


class TestPartitionDevsPreflight(unittest.TestCase):
    """F-144: the size must be validated for every target BEFORE any disk is
    wiped, not only inside zfs.partition (i.e. after the wipe already ran)."""

    def test_oversized_size_never_calls_wipe(self):
        import b2ctl.cli as cli
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sde", by_id="/dev/disk/by-id/x", size_bytes=100)
        with patch("b2ctl.core.scan_light", return_value=[d]), \
             patch("b2ctl.zfs.wipe") as wipe_mock, \
             patch("b2ctl.zfs.partition") as part_mock:
            out = cli._partition_devs(["/dev/disk/by-id/x"], "1T")
        self.assertIsNone(out)
        wipe_mock.assert_not_called()
        part_mock.assert_not_called()

    def test_invalid_size_never_calls_wipe(self):
        import b2ctl.cli as cli
        with patch("b2ctl.core.scan_light", return_value=[]), \
             patch("b2ctl.zfs.wipe") as wipe_mock, \
             patch("b2ctl.zfs.partition") as part_mock:
            out = cli._partition_devs(["/dev/disk/by-id/x"], "not-a-size")
        self.assertIsNone(out)
        wipe_mock.assert_not_called()
        part_mock.assert_not_called()


class TestResolveDevsStrictAmbiguous(unittest.TestCase):
    """F-144: strict resolution used to take the FIRST match silently —
    mirror watch._resolve_target and refuse an ambiguous token instead."""

    def test_strict_refuses_when_token_matches_two_disks(self):
        import b2ctl.cli as cli
        from b2ctl.common import Disk
        d1 = Disk(dev="/dev/sde", by_id="/dev/disk/by-id/a", serial="DUP")
        d2 = Disk(dev="/dev/sdf", by_id="/dev/disk/by-id/b", serial="DUP")
        with patch("b2ctl.cli.core.scan_light", return_value=[d1, d2]):
            self.assertIsNone(cli._resolve_devs(["DUP"], strict=True))


class TestUpdateCorruptConfigRefuses(unittest.TestCase):
    """F-146/F-147: a corrupt config.json used to be silently REWRITTEN with
    load()'s all-defaults fallback, discarding tool_paths/controller.mode/
    pools. `b2ctl update` must refuse and leave the file untouched instead."""

    def test_corrupt_config_refuses_and_does_not_rewrite(self):
        import b2ctl.cli as cli
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        cfg_path = os.path.join(tmp, "config.json")
        with open(cfg_path, "w") as f:
            f.write("{not valid json")
        original = open(cfg_path).read()
        with patch.object(cfg_mod, "CONFIG_PATH", cfg_path), \
             patch.object(cfg_mod, "STD_DIR", tmp), \
             patch("b2ctl.config.validate", return_value=[]), \
             patch("os.geteuid", return_value=0):
            args = cli.build_parser().parse_args(["update"])
            rc = args.func(args)
        self.assertEqual(rc, 1)
        with open(cfg_path) as f:
            self.assertEqual(f.read(), original)


class TestEnvelopeStringsAreCleanForMachines(unittest.TestCase):
    """F-146: error.message is a JSON string a client may log, display or match
    on. common.die() colours its line for a terminal, so the captured text has
    to be stripped the same way warnings[] already is."""

    def test_no_ansi_in_error_message_when_the_backend_dies(self):
        from b2ctl import common as _common
        with ExitStack() as stack:
            stack.enter_context(patch("os.geteuid", return_value=0))
            stack.enter_context(patch(
                "b2ctl.backend.get_backend",
                side_effect=lambda *a, **k: _common.die("no HBA/RAID tool usable")))
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = cli_mod.main(["status", "--json"])
        out = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertNotIn("\x1b", out["error"]["message"])
        self.assertIn("no HBA/RAID tool", out["error"]["message"])

    def test_strip_ansi_is_the_shared_helper(self):
        self.assertEqual(common.strip_ansi("\x1b[1;31mred\x1b[0m"), "red")
        self.assertEqual(common.strip_ansi("plain"), "plain")


class TestConfigInitWritesLikeEveryOtherWriter(unittest.TestCase):
    """F-148: `config init` CREATES /etc/b2ctl/config.json — the file whose
    tool_paths become root execution and which config.load() trust-checks
    (F-147). It was the last open(...,"w") + json.dump in the package, so it
    created the file at umask mode and non-atomically while every writer that
    later rewrote it used atomic_write_json at 0600."""

    def setUp(self):
        import tempfile
        from b2ctl import config as _cfg
        self.tmp = tempfile.mkdtemp()
        self._old_path = _cfg.CONFIG_PATH
        _cfg.CONFIG_PATH = os.path.join(self.tmp, "b2ctl", "config.json")
        _cfg._cache = None
        self._umask = os.umask(0)          # the hostile case

    def tearDown(self):
        import shutil
        from b2ctl import config as _cfg
        os.umask(self._umask)
        _cfg.CONFIG_PATH = self._old_path
        _cfg._cache = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_created_config_mode_is_stated_not_inherited_from_umask(self):
        # F-148 routed this through atomic_write_json; F-149 set that helper's
        # default to 0644. 0600 broke non-root `b2ctl config show` SILENTLY —
        # config.load() swallows PermissionError and returns defaults, so the
        # answer was wrong rather than refused, on a verb that is root-exempt
        # by design.
        from b2ctl import config as _cfg
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_mod.main(["config", "init"])
        self.assertEqual(rc, 0)
        self.assertEqual(os.stat(_cfg.CONFIG_PATH).st_mode & 0o777, 0o644)

    def test_a_non_root_reader_gets_the_real_config_not_defaults(self):
        # The regression F-149 is really about: the file must be READABLE by the
        # operator who is allowed to run `config show` without sudo.
        from b2ctl import config as _cfg
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cli_mod.main(["config", "init"])
        mode = os.stat(_cfg.CONFIG_PATH).st_mode & 0o777
        self.assertTrue(mode & 0o044, f"config.json is {oct(mode)} — not readable")

    def test_the_content_is_unchanged_and_reads_back(self):
        # Anti-overcorrection: swapping the writer must not alter what is written.
        from b2ctl import config as _cfg
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cli_mod.main(["config", "init"])
        with open(_cfg.CONFIG_PATH) as f:
            written = json.load(f)
        self.assertIn("tool_paths", written)
        _cfg._cache = None
        self.assertEqual(_cfg.load()["controller"], written["controller"])

    def test_existing_config_is_still_refused(self):
        from b2ctl import config as _cfg
        os.makedirs(os.path.dirname(_cfg.CONFIG_PATH), exist_ok=True)
        with open(_cfg.CONFIG_PATH, "w") as f:
            f.write('{"keep": "me"}')
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_mod.main(["config", "init"])
        self.assertEqual(rc, 1)                       # early return intact
        with open(_cfg.CONFIG_PATH) as f:
            self.assertEqual(json.load(f), {"keep": "me"})   # not clobbered
