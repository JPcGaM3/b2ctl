"""Unit tests for b2ctl.watch — offload guard, swap, create, demote, op flow."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from helpers import _disk
from b2ctl.common import Disk


# ========================================================================== #
# Offload guard — _replace_onto_spare return value + _cmd_offload assign gating
# ========================================================================== #

class TestWatchOffloadGuard:
    """Tests for the offload bug fix: _replace_onto_spare return value guards."""

    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=False)
    def test_replace_onto_spare_returns_false_on_decline(self, _mock_confirm_op, _mock_begin, _mock_end):
        from b2ctl.watch import _replace_onto_spare
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is False

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(False, "some zfs error"))
    def test_replace_onto_spare_returns_false_on_zfs_failure(self, _mrc, _mc, _mb, _me, mock_zfs, _ml):
        from b2ctl.watch import _replace_onto_spare
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is False

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(True, ""))
    def test_replace_onto_spare_returns_true_on_success(self, _mrc, _mc, _mb, _me, mock_zfs, _ml):
        from b2ctl.watch import _replace_onto_spare
        mock_zfs.poll_resilver_status.return_value = {"completed": True, "done": 100.0, "eta": "", "has_errors": False}
        mock_zfs.topology.return_value = {}
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is True

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._replace_onto_spare", return_value=False)
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_cmd_offload_does_not_assign_when_replace_declines(self, mock_ask,
                                                                mock_core,
                                                                mock_replace,
                                                                mock_assign):
        from b2ctl.watch import _cmd_offload
        d = _disk(bay="1:4", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        _cmd_offload({})
        mock_assign.assert_not_called()

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._replace_onto_spare", return_value=True)
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_cmd_offload_assigns_when_replace_succeeds(self, mock_ask,
                                                        mock_core,
                                                        mock_replace,
                                                        mock_assign):
        from b2ctl.watch import _cmd_offload
        d = _disk(bay="1:4", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        _cmd_offload({})
        mock_assign.assert_called_once()


# ========================================================================== #
# Swap — re-add old disk as spare instead of blink
# ========================================================================== #

class TestWatchSwap:
    """Tests for the swap fix: re-add old disk as spare instead of blink."""

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_swap_readds_as_spare_on_success(self, mock_ask, _mc, mock_core,
                                              mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank", pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        mock_zfs.swap_to_spare.return_value = (True, "")
        mock_zfs.poll_resilver_status.return_value = {"completed": True, "done": 100.0, "eta": "", "has_errors": False}
        mock_zfs.topology.return_value = {}
        mock_zfs.add_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:7) Samsung SSD 870 (TEST)"

        _cmd_swap({})

        mock_zfs.add_spare.assert_called_once_with("tank", d.by_id or d.dev, dry_run=False)

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_swap_no_spare_available(self, mock_ask, mock_core, capsys):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        _cmd_swap({})
        captured = capsys.readouterr()
        assert "no AVAIL spare" in captured.out

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_swap_candidate_list_excludes_spare(self, mock_ask, _mc, mock_core,
                                                 mock_zfs, mock_ui, capsys):
        # a spare (pool set, vdev=spares) must NOT appear as a swap source.
        from b2ctl.watch import _cmd_swap
        member = _disk(bay="1:4", vdev="raidz1-0", pool="tank",
                       by_id="/dev/disk/by-id/wwn-member")
        spare = _disk(bay="1:7", vdev="spares", vdev_state="AVAIL", pool="tank",
                      dev="/dev/sde", serial="SPARE1",
                      by_id="/dev/disk/by-id/wwn-spare",
                      pool_token="/dev/disk/by-id/wwn-spare")
        mock_core.scan.return_value = [member, spare]
        mock_zfs.swap_to_spare.return_value = (True, "")
        mock_zfs.poll_resilver_status.return_value = {"completed": True, "done": 100.0, "eta": "", "has_errors": False}
        mock_zfs.topology.return_value = {}
        mock_zfs.add_spare.return_value = (True, "")
        mock_ui.disk_label.side_effect = lambda d: f"({d.bay})"
        mock_ask.return_value = "1"   # pick first candidate
        _cmd_swap({})
        out = capsys.readouterr().out
        # only the raidz member is listed (one candidate line), spare is not
        assert "[1] (1:4)" in out
        assert "[2]" not in out
        # the swap operated on the member, not the spare
        args = mock_zfs.swap_to_spare.call_args[0]
        assert args[1] == "/dev/disk/by-id/wwn-member"

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=False)
    @patch("b2ctl.watch._ask")
    def test_swap_cancelled_on_decline(self, mock_ask, _mc, mock_core,
                                        mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        mock_ui.disk_label.return_value = "(1:7) Test"
        _cmd_swap({})
        mock_zfs.swap_to_spare.assert_not_called()


class TestWatchSwapDemoteFlow(unittest.TestCase):
    """End-to-end command flow for _cmd_swap / _cmd_demote against current code.

    (Replaces the stale feature_1b tests that targeted an older _cmd_swap that
    read spares via zfs.spares() and had no topology-lingering detach step.)
    """

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.time.sleep")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="1")
    def test_swap_polls_resilver_then_detaches_lingering(self, _ask, _mc, mock_core,
                                                          mock_zfs, _sleep, mock_ui):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0", pool="tank",
                  by_id="/dev/disk/by-id/sda", pool_token="/dev/disk/by-id/sda")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL", pool="tank",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        mock_core.scan.return_value = [d, spare]
        mock_zfs.swap_to_spare.return_value = (True, "")
        mock_zfs.poll_resilver_status.side_effect = [
            {"done": 50.0, "eta": "00:10:00", "completed": False},
            {"done": 100.0, "eta": "", "completed": True},
        ]
        # old token still present in topology → detach path must run
        mock_zfs.topology.return_value = {
            "/dev/disk/by-id/sda": {"pool": "tank", "token": "/dev/disk/by-id/sda"}
        }
        mock_zfs.detach.return_value = (True, "")
        mock_zfs.add_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:7) Samsung SSD 870 (TEST)"

        _cmd_swap({})

        mock_zfs.swap_to_spare.assert_called_once_with(
            "tank", "/dev/disk/by-id/sda", "/dev/disk/by-id/wwn-0xSPARE", dry_run=False)
        self.assertEqual(mock_zfs.poll_resilver_status.call_count, 2)
        mock_zfs.detach.assert_called_once_with("tank", "/dev/disk/by-id/sda", dry_run=False)

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="1")
    def test_demote_success_calls_can_detach_and_demote(self, _ask, _mc, mock_core,
                                                         mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0", pool="tank",
                  by_id="/dev/disk/by-id/sda", pool_token=None)
        mock_core.scan.return_value = [d]
        mock_zfs.can_detach.return_value = True
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:0) Test"
        _cmd_demote({})
        mock_zfs.can_detach.assert_called_once_with("tank", "/dev/disk/by-id/sda")
        mock_zfs.demote_to_spare.assert_called_once_with(
            "tank", "/dev/disk/by-id/sda", dry_run=False)


# ========================================================================== #
# Create — pool creation validation
# ========================================================================== #

class TestWatchCreate:
    """Tests for pool creation validation."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_create_pool_validates_min_disks(self, mock_ask, _mc, mock_core,
                                              mock_zfs, capsys):
        from b2ctl.watch import _cmd_create
        import b2ctl.zfs as real_zfs
        d = _disk(pool=None, vdev=None, vdev_state=None)
        mock_core.scan.return_value = [d]
        mock_ask.side_effect = ["1", "mypool", "raidz2"]
        mock_zfs.MIN_DISKS = real_zfs.MIN_DISKS
        _cmd_create({})
        captured = capsys.readouterr()
        assert "need at least" in captured.out
        mock_zfs.create_pool.assert_not_called()

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_create_pool_prompts_props_and_passes_defaults(self, mock_ask, _mc,
                                                           mock_core, mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_create
        import b2ctl.zfs as real_zfs
        d1 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sda", by_id="/d/a")
        d2 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sdb",
                   serial="S2", by_id="/d/b")
        mock_core.scan.return_value = [d1, d2]
        mock_zfs.MIN_DISKS = real_zfs.MIN_DISKS
        mock_zfs.DEFAULT_POOL_OPTS = real_zfs.DEFAULT_POOL_OPTS
        mock_zfs.DEFAULT_FS_OPTS = real_zfs.DEFAULT_FS_OPTS
        mock_zfs.has_zfs_label.return_value = False
        mock_zfs.create_pool.return_value = (True, "")
        mock_zfs.install_pool_cron.return_value = (True, "/etc/cron.d/b2ctl-tank")
        # pick "1 2", name, raid type, then Enter for ashift + autotrim choice + 6 fs
        mock_ask.side_effect = ["1 2", "tank", "mirror"] + [""] * 8
        _cmd_create({})
        kwargs = mock_zfs.create_pool.call_args.kwargs
        # autotrim default choice = off (Monthly) -> cron installed
        assert kwargs["pool_opts"]["ashift"] == "12"
        assert kwargs["pool_opts"]["autotrim"] == "off"
        assert kwargs["fs_opts"] == real_zfs.DEFAULT_FS_OPTS
        mock_zfs.install_pool_cron.assert_called_once_with("tank", dry_run=False)

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_create_pool_autotrim_on_skips_cron(self, mock_ask, _mc, mock_core,
                                                mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_create
        import b2ctl.zfs as real_zfs
        d1 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sda", by_id="/d/a")
        d2 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sdb",
                   serial="S2", by_id="/d/b")
        mock_core.scan.return_value = [d1, d2]
        mock_zfs.MIN_DISKS = real_zfs.MIN_DISKS
        mock_zfs.DEFAULT_POOL_OPTS = real_zfs.DEFAULT_POOL_OPTS
        mock_zfs.DEFAULT_FS_OPTS = real_zfs.DEFAULT_FS_OPTS
        mock_zfs.has_zfs_label.return_value = False
        mock_zfs.create_pool.return_value = (True, "")
        # ashift blank, autotrim choice "2" (on), 6 fs blank
        mock_ask.side_effect = ["1 2", "tank", "mirror", "", "2"] + [""] * 6
        _cmd_create({})
        assert mock_zfs.create_pool.call_args.kwargs["pool_opts"]["autotrim"] == "on"
        mock_zfs.install_pool_cron.assert_not_called()

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_create_pool_invalid_raid_type(self, mock_ask, mock_core,
                                            mock_zfs, capsys):
        from b2ctl.watch import _cmd_create
        d1 = _disk(pool=None, vdev=None, vdev_state=None)
        d2 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sdb",
                   serial="S2")
        mock_core.scan.return_value = [d1, d2]
        mock_ask.side_effect = ["1 2", "mypool", "raidz99"]
        _cmd_create({})
        captured = capsys.readouterr()
        assert "invalid raid type" in captured.out
        mock_zfs.create_pool.assert_not_called()


# ========================================================================== #
# Demote — guard + happy path
# ========================================================================== #

class TestWatchDemote:
    """Tests for demote command."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_demote_refuses_non_detachable(self, mock_ask, mock_core,
                                            mock_zfs, capsys):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        mock_zfs.can_detach.return_value = False
        _cmd_demote({})
        captured = capsys.readouterr()
        assert "refuse" in captured.out

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_demote_calls_demote_to_spare(self, mock_ask, _mc, mock_core,
                                           mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        mock_zfs.can_detach.return_value = True
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:0) Test"
        _cmd_demote({})
        mock_zfs.demote_to_spare.assert_called_once()


# ========================================================================== #
# _assign_free_disk choice 4 — avoid double scan when all_disks provided
# ========================================================================== #

class TestWatchAssignChoice4:

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="4")
    def test_assign_choice4_skips_scan_when_all_disks_provided(
            self, _ask, _pick, mock_core):
        from b2ctl.watch import _assign_free_disk
        d = Disk(dev="/dev/sdb", by_id="/dev/disk/by-id/wwn-test")
        _assign_free_disk(d, {}, all_disks=[])
        mock_core.scan.assert_not_called()

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="4")
    def test_assign_choice4_calls_scan_when_all_disks_none(
            self, _ask, _pick, mock_core):
        from b2ctl.watch import _assign_free_disk
        mock_core.scan.return_value = []
        d = Disk(dev="/dev/sdb", by_id="/dev/disk/by-id/wwn-test")
        _assign_free_disk(d, {}, all_disks=None)
        mock_core.scan.assert_called_once()


# ========================================================================== #
# _wait_for_block_device — udevadm settle replaces lsblk poll loop
# ========================================================================== #

class TestWatchWaitForBlock:

    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_calls_settle_once(self, mock_hba):
        mock_hba.run.return_value = ""
        mock_hba._lsblk_pairs.return_value = [
            {"TYPE": "disk", "SERIAL": "SN123", "NAME": "sda"}
        ]
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN123", timeout=20)
        mock_hba.run.assert_called_once_with(["udevadm", "settle", "--timeout=20"])
        assert mock_hba._lsblk_pairs.call_count == 1
        assert result == "/dev/sda"

    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_returns_none_when_missing(self, mock_hba):
        mock_hba.run.return_value = ""
        mock_hba._lsblk_pairs.return_value = []
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN999", timeout=5)
        mock_hba.run.assert_called_once_with(["udevadm", "settle", "--timeout=5"])
        assert result is None


# ========================================================================== #
# _confirm_op — interactive confirmation prompt
# ========================================================================== #

class TestConfirmOp(unittest.TestCase):

    def _make_disk(self, bay=3, serial="S3EV123", model="Samsung 870 EVO 1TB",
                   by_id="/dev/disk/by-id/wwn-0xDEAD", pool="tank", vdev="raidz1-0"):
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


# ========================================================================== #
# Dry-run toggle ('t' key)
# ========================================================================== #

class TestDryRunToggle(unittest.TestCase):

    def test_t_key_sets_dry_run(self):
        import b2ctl.watch as watch
        watch._DRY_RUN = False
        watch._toggle_dry_run()
        self.assertTrue(watch._DRY_RUN)
        watch._toggle_dry_run()
        self.assertFalse(watch._DRY_RUN)


# ========================================================================== #
# Op wrapping — _replace_onto_spare brackets the op with begin_op/end_op
# ========================================================================== #

class TestOpWrapping(unittest.TestCase):

    def test_replace_onto_spare_calls_begin_end_op(self):
        import b2ctl.watch as watch
        import b2ctl.safety as safety
        from b2ctl.common import Disk

        disk = Disk.__new__(Disk)
        disk.serial = "S1"; disk.bay = 1; disk.pool = "tank"
        disk.vdev = "raidz1-0"; disk.pool_token = "/dev/disk/by-id/old"
        disk.by_id = "/dev/disk/by-id/old"; disk.dev = "/dev/sda"

        spare = Disk.__new__(Disk)
        spare.serial = "S2"; spare.bay = 2; spare.pool = "tank"
        spare.vdev = "spares"; spare.pool_token = "/dev/disk/by-id/spare"
        spare.by_id = "/dev/disk/by-id/spare"; spare.dev = "/dev/sdb"

        with patch.object(watch, "_confirm_op", return_value=True), \
             patch("b2ctl.zfs.replace", return_value=None), \
             patch("b2ctl.zfs.poll_resilver_status", return_value=None), \
             patch.object(safety, "begin_op", return_value="20260617-replace") as mock_begin, \
             patch.object(safety, "end_op") as mock_end:
            watch._replace_onto_spare(disk, spare)

        mock_begin.assert_called_once()
        mock_end.assert_called_once()
        call_args = mock_end.call_args[0]
        self.assertEqual(call_args[0], "20260617-replace")


class TestWatchDryRunPropagation(unittest.TestCase):
    """fix 1: zfs.wipe() in _wipe_ghost must receive dry_run=_DRY_RUN."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.hba")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=False)
    def test_wipe_ghost_cancelled_does_not_call_wipe(self, _mc, mock_core,
                                                      mock_hba, mock_zfs):
        from b2ctl.watch import _wipe_ghost
        d = _disk(serial="S1", bay="1:4")
        mock_hba.find_sg_for_ghost.return_value = "/dev/sg0"
        mock_zfs.wipe_sg.return_value = (True, "")
        _wipe_ghost(d, {})
        mock_zfs.wipe.assert_not_called()

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.hba")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._wait_for_block_device", return_value="/dev/sda")
    @patch("b2ctl.watch.core.scan_one")
    @patch("b2ctl.watch._assign_free_disk")
    def test_wipe_ghost_passes_dry_run_to_wipe(self, _mafd, _mscan, _mwbdev,
                                                _mc, mock_core, mock_hba, mock_zfs):
        import b2ctl.watch as watch_mod
        from b2ctl.watch import _wipe_ghost
        d = _disk(serial="S1", bay="1:4")
        mock_hba.find_sg_for_ghost.return_value = "/dev/sg0"
        mock_zfs.wipe_sg.return_value = (True, "")
        mock_zfs.wipe.return_value = (True, "")
        watch_mod._DRY_RUN = True
        try:
            _wipe_ghost(d, {})
        finally:
            watch_mod._DRY_RUN = False
        mock_zfs.wipe.assert_called_once()
        _, kwargs = mock_zfs.wipe.call_args
        assert kwargs.get("dry_run") is True


class TestWatchAuditOrdering(unittest.TestCase):
    """fix 7: begin_op must not fire before _confirm_op in replace paths."""

    def test_replace_onto_spare_cancelled_does_not_call_begin_op(self):
        from b2ctl import watch, safety
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        with patch.object(watch, "_confirm_op", return_value=False), \
             patch.object(safety, "begin_op") as mock_begin:
            result = watch._replace_onto_spare(d, spare)
        assert result is False
        mock_begin.assert_not_called()


class TestAssignRaidAware(unittest.TestCase):
    """SAFETY + feature: a HIDDEN PERC drive (smart_dtype set, dev=/dev/sda) must
    never reach the ZFS wipe/assign flow; a UGood one routes to the RAID menu."""

    @patch("b2ctl.raid_actions.assign_perc", return_value=0)
    @patch("b2ctl.watch._wipe_ghost")
    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._ask", return_value="1")
    @patch("b2ctl.watch.core")
    def test_member_excluded_ugood_routed_to_raid(self, mock_core, _ask,
                                                   assign_mock, wipe_mock, perc_mock):
        import b2ctl.watch as watch
        member = Disk(dev="/dev/sda"); member.array_type = "HW"
        member.bay = "32:0"; member.smart_dtype = "megaraid,0"; member.pd_state = "Onln"
        ugood = Disk(dev="/dev/sda"); ugood.bay = "32:4"
        ugood.smart_dtype = "megaraid,4"; ugood.pd_state = "UGood"
        mock_core.scan.return_value = [member, ugood]
        watch._cmd_assign({})
        # only the UGood drive is selectable ([1]) -> routed to the RAID menu;
        # the HW member is not offered; ZFS wipe/assign never touched /dev/sda.
        perc_mock.assert_called_once()
        assert perc_mock.call_args[0][0] is ugood
        assign_mock.assert_not_called()
        wipe_mock.assert_not_called()

    @patch("b2ctl.raid_actions.assign_perc")
    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._ask", return_value="1")
    @patch("b2ctl.watch.core")
    def test_jbod_disk_routes_to_zfs(self, mock_core, _ask, assign_mock, perc_mock):
        import b2ctl.watch as watch
        # a JBOD'd drive: own /dev/sdb, no smart_dtype -> ZFS-assignable
        jbod = Disk(dev="/dev/sdb"); jbod.bay = "32:4"; jbod.pd_state = "JBOD"
        mock_core.scan.return_value = [jbod]
        watch._cmd_assign({})
        assign_mock.assert_called_once()
        perc_mock.assert_not_called()


class TestOfflineReplace(unittest.TestCase):
    """Spare-less offload: offline (degrade) + replace-in-place, guarded."""

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    def test_refuses_when_not_redundant(self, mock_core, mock_zfs, mock_ui):
        from b2ctl.watch import _offline_and_replace
        d = _disk(pool="tank", vdev="raidz1-0", by_id="/d/a", pool_token="/d/a")
        mock_zfs.can_offline.return_value = False
        _offline_and_replace(d, {})
        mock_zfs.offline.assert_not_called()

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch._wait_resilver")
    @patch("b2ctl.watch.run_check", return_value=(True, ""))
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch._ask", return_value="")
    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    def test_happy_path_offlines_then_replaces(self, mock_core, mock_zfs, _safety,
                                               _ask, _confirm, mock_rc, _wait, mock_ui, _loc):
        from b2ctl.watch import _offline_and_replace
        old = _disk(pool="tank", vdev="raidz1-0", bay="1:4",
                    by_id="/d/old", pool_token="/d/old", serial="OLD")
        new = _disk(pool=None, vdev=None, vdev_state=None, bay="1:4",
                    dev="/dev/sdz", by_id="/d/new", serial="NEW")
        new.pool_token = None
        mock_zfs.can_offline.return_value = True
        mock_zfs.offline.return_value = (True, "")
        mock_core.scan.return_value = [new]
        _offline_and_replace(old, {})
        mock_zfs.offline.assert_called_once_with("tank", "/d/old", dry_run=False)
        # replace runs via run_check with the resolved new by-id
        mock_rc.assert_called_once_with(
            ["zpool", "replace", "-f", "tank", "/d/old", "/d/new"], dry_run=False)


class TestWatchDestroy(unittest.TestCase):
    """destroy: zpool destroy + remove cron, gated by name-confirm."""

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="tank")   # type the pool name
    def test_destroy_confirmed_destroys_and_removes_cron(self, _ask, _mc, mock_core,
                                                         mock_zfs, _safety, mock_ui):
        from b2ctl.watch import _cmd_destroy
        mock_zfs.list_pools.return_value = [{"name": "tank", "size": "1T", "health": "ONLINE"}]
        mock_core.scan.return_value = []
        mock_zfs.destroy_pool.return_value = (True, "")
        mock_zfs.remove_pool_cron.return_value = (True, "/etc/cron.d/b2ctl-tank")
        _cmd_destroy({}, target="tank")
        mock_zfs.destroy_pool.assert_called_once_with("tank", dry_run=False)
        mock_zfs.remove_pool_cron.assert_called_once_with("tank", dry_run=False)

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="wrongname")
    def test_destroy_name_mismatch_aborts(self, _ask, _mc, mock_core, mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_destroy
        mock_zfs.list_pools.return_value = [{"name": "tank", "size": "1T", "health": "ONLINE"}]
        mock_core.scan.return_value = []
        _cmd_destroy({}, target="tank")
        mock_zfs.destroy_pool.assert_not_called()
        mock_zfs.remove_pool_cron.assert_not_called()


if __name__ == "__main__":
    unittest.main()
