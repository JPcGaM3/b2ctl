"""Unit tests for b2ctl.watch — offload guard, swap, create, demote, op flow."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import pytest

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

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(True, ""))
    def test_replace_member_stops_on_errored_resilver(self, _mrc, _mc, _mb, mock_end,
                                                      mock_zfs, mock_locate):
        # F-009: an errored resilver must NOT detach the old disk or light its
        # pull-LED (CLAUDE.md §9). _replace_onto_spare must return False.
        from b2ctl.watch import _replace_onto_spare
        mock_zfs.poll_resilver_status.return_value = {
            "completed": True, "done": 100.0, "eta": "", "has_errors": True, "ok": True}
        d = _disk(bay="1:4")
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        with patch("b2ctl.watch.time.sleep"):
            result = _replace_onto_spare(d, spare)
        assert result is False
        mock_zfs.detach.assert_not_called()
        mock_locate.blink_disk.assert_not_called()
        # op recorded as failure
        assert mock_end.call_args.args[1] is False

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

    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_swap_readds_as_spare_on_success(self, mock_ask, _mc, mock_core,
                                              mock_zfs, mock_ui, _cop, _safety):
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
        # F-057: the swap is audited (begin_op + end_op each fire once)
        assert _safety.begin_op.call_count == 1
        assert _safety.end_op.call_count == 1

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

    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_swap_candidate_list_excludes_spare(self, mock_ask, _mc, mock_core,
                                                 mock_zfs, mock_ui, _cop, _safety, capsys):
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

    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.time.sleep")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="1")
    def test_swap_polls_resilver_then_detaches_lingering(self, _ask, _mc, mock_core,
                                                          mock_zfs, _sleep, mock_ui,
                                                          _cop, _safety):
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
    def test_demote_success_calls_detach_safety_and_demote(self, _ask, _mc, mock_core,
                                                            mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0", pool="tank",
                  by_id="/dev/disk/by-id/sda", pool_token=None)
        mock_core.scan.return_value = [d]
        mock_zfs.detach_safety.return_value = "ok"
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:0) Test"
        _cmd_demote({})
        mock_zfs.detach_safety.assert_called_once_with("tank", "/dev/disk/by-id/sda")
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
        mock_zfs.install_pool_timers.return_value = (True, "enabled …")
        # pick "1 2", name, raid type, then Enter for ashift + autotrim choice + 6 fs
        mock_ask.side_effect = ["1 2", "tank", "mirror"] + [""] * 8
        _cmd_create({})
        kwargs = mock_zfs.create_pool.call_args.kwargs
        # autotrim default choice = off -> scrub + trim timers enabled
        assert kwargs["pool_opts"]["ashift"] == "12"
        assert kwargs["pool_opts"]["autotrim"] == "off"
        assert kwargs["fs_opts"] == real_zfs.DEFAULT_FS_OPTS
        mock_zfs.install_pool_timers.assert_called_once_with(
            "tank", include_trim=True, dry_run=False)

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_create_pool_autotrim_on_still_installs_scrub_only_timer(
            self, mock_ask, _mc, mock_core, mock_zfs, mock_ui):
        # Scrub must run monthly regardless of the autotrim choice — only the trim
        # timer is skipped when autotrim=on (ZFS already trims continuously).
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
        mock_zfs.install_pool_timers.return_value = (True, "enabled …")
        # ashift blank, autotrim choice "2" (on), 6 fs blank
        mock_ask.side_effect = ["1 2", "tank", "mirror", "", "2"] + [""] * 6
        _cmd_create({})
        assert mock_zfs.create_pool.call_args.kwargs["pool_opts"]["autotrim"] == "on"
        mock_zfs.install_pool_timers.assert_called_once_with(
            "tank", include_trim=False, dry_run=False)

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


class TestHandleNewDisk:
    """F-019: a re-seated pool member must not be offered the free/WIPE menu."""

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.time.sleep")
    def test_refuses_in_pool_member(self, _sleep, mock_core, mock_ui, mock_assign):
        from b2ctl.watch import _handle_new_disk
        d = _disk(pool="rpool", vdev="mirror-0", vdev_state="ONLINE",
                  by_id="/dev/disk/by-id/ata-X")
        mock_core.scan_one.return_value = d
        mock_ui.render_new_disk.return_value = ""
        _handle_new_disk("/dev/sdb", {})
        mock_assign.assert_not_called()

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.time.sleep")
    def test_offers_menu_for_free_disk(self, _sleep, mock_core, mock_ui, mock_assign):
        from b2ctl.watch import _handle_new_disk
        d = _disk(pool=None, vdev=None, vdev_state=None,
                  by_id="/dev/disk/by-id/ata-Y", smart_dtype="")
        mock_core.scan_one.return_value = d
        mock_ui.render_new_disk.return_value = ""
        _handle_new_disk("/dev/sdb", {})
        mock_assign.assert_called_once()


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
        mock_zfs.detach_safety.return_value = "refuse"
        _cmd_demote({})
        captured = capsys.readouterr()
        assert "refuse" in captured.out
        mock_zfs.demote_to_spare.assert_not_called()

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
        mock_zfs.detach_safety.return_value = "ok"
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:0) Test"
        _cmd_demote({})
        mock_zfs.demote_to_spare.assert_called_once()

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_demote_last_redundancy_requires_pool_name(self, mock_ask, mock_core,
                                                        mock_zfs, mock_ui):
        # F-023: detaching the last mirror leg must require typing the pool name.
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0", pool="rpool")
        mock_core.scan.return_value = [d]
        mock_zfs.detach_safety.return_value = "last_redundancy"
        mock_ui.disk_label.return_value = "(1:0) Test"
        # menu pick "1", then a WRONG pool name -> cancelled
        mock_ask.side_effect = ["1", "wrongname"]
        _cmd_demote({})
        mock_zfs.demote_to_spare.assert_not_called()
        # menu pick "1", then the correct pool name -> proceeds
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ask.side_effect = ["1", "rpool"]
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

    @patch("b2ctl.watch.time.sleep")
    @patch("b2ctl.watch.blockdev")
    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_returns_immediately_when_present(self, mock_hba, mock_bd, _sleep):
        mock_hba.run.return_value = ""
        mock_bd.lsblk_pairs.return_value = [
            {"TYPE": "disk", "SERIAL": "SN123", "NAME": "sda"}
        ]
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN123", timeout=20)
        assert result == "/dev/sda"
        assert mock_bd.lsblk_pairs.call_count == 1     # found on first poll

    @patch("b2ctl.watch.time.sleep")
    @patch("b2ctl.watch.blockdev")
    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_polls_until_device_appears(self, mock_hba, mock_bd, _sleep):
        # F-053: the device appears only on the 3rd poll — must keep waiting.
        mock_hba.run.return_value = ""
        mock_bd.lsblk_pairs.side_effect = [
            [], [],
            [{"TYPE": "disk", "SERIAL": "SN123", "NAME": "sdc"}],
        ]
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN123", timeout=20)
        assert result == "/dev/sdc"
        assert mock_bd.lsblk_pairs.call_count == 3

    @patch("b2ctl.watch.time.monotonic")
    @patch("b2ctl.watch.time.sleep")
    @patch("b2ctl.watch.blockdev")
    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_returns_none_after_deadline(self, mock_hba, mock_bd, _sleep, mock_mono):
        mock_hba.run.return_value = ""
        mock_bd.lsblk_pairs.return_value = []
        mock_mono.side_effect = [0.0, 1.0, 99.0]   # start, then past the 5 s deadline
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN999", timeout=5)
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
    def test_destroy_confirmed_destroys_and_disables_timers(self, _ask, _mc, mock_core,
                                                            mock_zfs, _safety, mock_ui):
        from b2ctl.watch import _cmd_destroy
        mock_zfs.list_pools.return_value = [{"name": "tank", "size": "1T", "health": "ONLINE"}]
        mock_core.scan.return_value = []
        mock_zfs.destroy_pool.return_value = (True, "")
        mock_zfs.remove_pool_timers.return_value = (True, "disabled …")
        _cmd_destroy({}, target="tank")
        mock_zfs.destroy_pool.assert_called_once_with("tank", dry_run=False)
        mock_zfs.remove_pool_timers.assert_called_once_with("tank", dry_run=False)

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
        mock_zfs.remove_pool_timers.assert_not_called()


class TestRefreshStorageSummary(unittest.TestCase):
    """watch _cmd_refresh renders the unified Storage summary — hardware above
    software (parity with the CLI `status` path)."""

    def _run_refresh(self, vols, pools):
        import io
        import contextlib
        from b2ctl.watch import _cmd_refresh

        class _Bk:
            def raid_volumes(self_inner):
                return vols
        with patch("b2ctl.core.scan", return_value=[]), \
             patch("b2ctl.zfs.list_pools", return_value=pools), \
             patch("b2ctl.zfs.pool_level", return_value="mirror"), \
             patch("b2ctl.watch._backend.get_backend", return_value=_Bk()):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _cmd_refresh({})
            return buf.getvalue()

    def test_hardware_above_software(self):
        vols = [{"vd": "0", "raid": "raid1", "state": "Optl",
                 "size": "640.0 GB", "members": 2, "name": "MainSSD"}]
        pools = [{"name": "tank", "size": "928G", "alloc": "598M",
                  "free": "927G", "health": "ONLINE"}]
        out = self._run_refresh(vols, pools)
        self.assertIn("Storage summary", out)
        self.assertIn("MainSSD", out)
        self.assertLess(out.index("MainSSD"), out.index("tank"))

    def test_software_only(self):
        pools = [{"name": "tank", "size": "928G", "alloc": "598M",
                  "free": "927G", "health": "ONLINE"}]
        out = self._run_refresh([], pools)
        self.assertIn("Storage summary", out)
        self.assertIn("tank", out)


class TestExtendAndBurnin(unittest.TestCase):
    """_cmd_extend (L2ARC/SLOG add + aux remove) and _cmd_burnin dispatch."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_extend_add_cache(self, mock_ask, _mc, mock_core, mock_zfs):
        from b2ctl.watch import _cmd_extend
        mock_zfs.list_pools.return_value = [{"name": "tank", "health": "ONLINE"}]
        mock_zfs.add_cache.return_value = (True, "")
        free = _disk(dev="/dev/sde", pool=None, vdev=None, smart_dtype="")
        mock_core.scan.return_value = [free]
        mock_ask.side_effect = ["1", "1"]            # action=cache, pick disk #1
        _cmd_extend({})
        mock_zfs.add_cache.assert_called_once()
        self.assertEqual(mock_zfs.add_cache.call_args[0][0], "tank")

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_extend_add_log_mirror(self, mock_ask, _mc, mock_core, mock_zfs):
        from b2ctl.watch import _cmd_extend
        mock_zfs.list_pools.return_value = [{"name": "tank", "health": "ONLINE"}]
        mock_zfs.add_log.return_value = (True, "")
        a = _disk(dev="/dev/sdx", pool=None, vdev=None, smart_dtype="")
        b = _disk(dev="/dev/sdy", serial="DIFF", pool=None, vdev=None, smart_dtype="")
        mock_core.scan.return_value = [a, b]
        mock_ask.side_effect = ["2", "1 2"]          # action=log, pick both
        _cmd_extend({})
        mock_zfs.add_log.assert_called_once()

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_extend_remove_aux(self, mock_ask, _mc, mock_zfs):
        from b2ctl.watch import _cmd_extend
        mock_zfs.list_pools.return_value = [{"name": "tank", "health": "ONLINE"}]
        mock_zfs.topology.return_value = {
            "/dev/sde": {"pool": "tank", "vdev": "cache", "token": "sde", "state": "ONLINE"}}
        mock_zfs.remove_vdev.return_value = (True, "")
        mock_ask.side_effect = ["3", "1"]            # action=remove, pick #1
        _cmd_extend({})
        mock_zfs.remove_vdev.assert_called_once_with("tank", "sde", dry_run=False)

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_burnin_dispatch(self, mock_ask, mock_core):
        from b2ctl.watch import _cmd_burnin
        free = _disk(dev="/dev/sde", pool=None, vdev=None, smart_dtype="")
        mock_core.scan.return_value = [free]
        mock_ask.side_effect = ["1"]
        with patch("b2ctl.burnin.run_multi", return_value=0) as mock_run, \
             patch("b2ctl.burnin.load_state", return_value=[]), \
             patch("b2ctl.watch._confirm", side_effect=[True, False]):
            _cmd_burnin({})
        mock_run.assert_called_once()

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_create_raid10_rejects_odd(self, mock_ask, mock_core, mock_zfs):
        from b2ctl.watch import _cmd_create
        mock_zfs.MIN_DISKS = {"raid10": 4}
        a = _disk(dev="/dev/a", pool=None, vdev=None, smart_dtype="")
        b = _disk(dev="/dev/b", serial="B", pool=None, vdev=None, smart_dtype="")
        c = _disk(dev="/dev/c", serial="C", pool=None, vdev=None, smart_dtype="")
        mock_core.scan.return_value = [a, b, c]
        mock_ask.side_effect = ["1 2 3", "tank"]     # pick 3 disks, name
        _cmd_create({}, raid_type="raid10")
        mock_zfs.create_pool.assert_not_called()     # odd count rejected


class TestWipeGhostByIdGuard(unittest.TestCase):
    """F-054: a freshly-wiped disk with no by-id must not reach _assign_free_disk."""

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.hba")
    @patch("b2ctl.watch._wait_for_block_device", return_value="/dev/sdh")
    @patch("b2ctl.watch._confirm", return_value=True)
    def test_skips_assign_without_by_id(self, _cf, _wfb, mock_hba, mock_zfs,
                                        mock_core, mock_assign):
        from b2ctl.watch import _wipe_ghost
        mock_hba.find_sg_for_ghost.return_value = "/dev/sg3"
        mock_zfs.wipe_sg.return_value = (True, "")
        mock_zfs.wipe.return_value = (True, "")
        mock_core.scan_one.return_value = Disk(dev="/dev/sdh", by_id="")   # no by-id yet
        d = _disk(dev="-", by_id="", serial="SNGHOST", bay="1:3", health="GHOST")
        _wipe_ghost(d, {})
        mock_assign.assert_not_called()

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.hba")
    @patch("b2ctl.watch._wait_for_block_device", return_value="/dev/sdh")
    @patch("b2ctl.watch._confirm", return_value=True)
    def test_assigns_when_by_id_present(self, _cf, _wfb, mock_hba, mock_zfs,
                                        mock_core, mock_assign):
        from b2ctl.watch import _wipe_ghost
        mock_hba.find_sg_for_ghost.return_value = "/dev/sg3"
        mock_zfs.wipe_sg.return_value = (True, "")
        mock_zfs.wipe.return_value = (True, "")
        mock_core.scan_one.return_value = Disk(dev="/dev/sdh",
                                               by_id="/dev/disk/by-id/ata-X")
        d = _disk(dev="-", by_id="", serial="SNGHOST", bay="1:3", health="GHOST")
        _wipe_ghost(d, {})
        mock_assign.assert_called_once()


class TestOfflineReplaceNewDiskDetection(unittest.TestCase):
    """F-056: with bay=None the replacement is found by a new serial, never an
    arbitrary pre-existing free disk."""

    @patch("b2ctl.watch._replace_member")
    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch._ask", return_value="")
    def test_no_bay_uses_serial_diff_not_arbitrary(self, _ask, _cop, mock_core,
                                                   mock_zfs, _safety, _ui, _loc,
                                                   mock_replace):
        from b2ctl.watch import _offline_and_replace
        d = _disk(bay=None, pool="tank", serial="OLD",
                  by_id="/dev/disk/by-id/wwn-old", pool_token="/dev/disk/by-id/wwn-old")
        old_scratch = _disk(bay=None, pool=None, vdev=None, serial="SCRATCH",
                            dev="/dev/sdd", by_id="/dev/disk/by-id/wwn-scratch",
                            smart_dtype="")
        new = _disk(bay=None, pool=None, vdev=None, serial="NEWDISK",
                    dev="/dev/sde", by_id="/dev/disk/by-id/wwn-new", smart_dtype="")
        mock_zfs.can_offline.return_value = True
        mock_zfs.offline.return_value = (True, "")
        # before-insert scan: only the scratch disk is free; after: scratch + new
        mock_core.scan.side_effect = [
            [d, old_scratch],
            [d, old_scratch, new],
        ]
        _offline_and_replace(d, {})
        mock_replace.assert_called_once()
        picked = mock_replace.call_args[0][1]
        self.assertEqual(picked.serial, "NEWDISK")   # not the scratch disk


class TestStartupPruneDryRun(unittest.TestCase):
    """F-058: `b2ctl --dry-run watch` must not disable timers at startup."""

    def test_prune_receives_dry_run_flag(self):
        import b2ctl.watch as watch
        watch._DRY_RUN = True
        try:
            with patch("b2ctl.watch.zfs") as mz, \
                 patch("b2ctl.watch._cmd_refresh"), \
                 patch("b2ctl.watch._block_devs", return_value=set()), \
                 patch("b2ctl.watch.select.select", return_value=([sys.stdin], [], [])), \
                 patch("b2ctl.watch.sys.stdin") as mstdin:
                mz.prune_orphan_timers.return_value = []
                mstdin.readline.return_value = "q\n"
                watch.run()
            mz.prune_orphan_timers.assert_called_once_with(dry_run=True)
        finally:
            watch._DRY_RUN = False


class TestHotplugIdentity(unittest.TestCase):
    """F-059: hot-plug diff keys on (name, serial), catching a same-name swap."""

    @patch("b2ctl.watch.blockdev")
    def test_same_name_serial_change_detected(self, mock_bd):
        import b2ctl.watch as watch
        mock_bd.EXCLUDE = ("loop", "sr")
        mock_bd.lsblk_pairs.return_value = [
            {"NAME": "sdc", "SERIAL": "SN1", "TYPE": "disk"}]
        baseline = watch._block_devs()
        mock_bd.lsblk_pairs.return_value = [
            {"NAME": "sdc", "SERIAL": "SN2", "TYPE": "disk"}]
        current = watch._block_devs()
        gone, new = baseline - current, current - baseline
        self.assertEqual({n for n, _ in gone}, {"sdc"})
        self.assertEqual({n for n, _ in new}, {"sdc"})

    @patch("b2ctl.watch.blockdev")
    def test_empty_lsblk_returns_none(self, mock_bd):
        import b2ctl.watch as watch
        mock_bd.lsblk_pairs.return_value = []
        self.assertIsNone(watch._block_devs())


# ========================================================================== #
# F-100 — _handle_new_disk settles udev before scan_one (no fixed sleep(2))
# ========================================================================== #

class TestHandleNewDiskUdevSettle:
    """F-100: the hotplug handler runs `udevadm settle` (not a fixed sleep) before
    scan_one, and when the stable by-id link has not appeared it skips the assign
    menu rather than acting on an unstable /dev/sdX (CLAUDE.md §9)."""

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.hba")
    def test_settles_udev_before_scan_one(self, mock_hba, mock_core, mock_ui, mock_assign):
        from b2ctl.watch import _handle_new_disk
        mock_core.scan_one.return_value = _disk(
            dev="/dev/sdb", by_id="/dev/disk/by-id/ata-NEW",
            pool=None, vdev=None, vdev_state=None, smart_dtype="")
        mock_ui.render_new_disk.return_value = ""
        _handle_new_disk("/dev/sdb", {})
        mock_hba.run.assert_called_once_with(["udevadm", "settle", "--timeout=10"])
        mock_core.scan_one.assert_called_once()
        mock_assign.assert_called_once()

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.hba")
    def test_skips_assign_when_by_id_still_missing(self, mock_hba, mock_core, mock_ui, mock_assign):
        from b2ctl.watch import _handle_new_disk
        mock_core.scan_one.return_value = Disk(dev="/dev/sdb", by_id="")
        mock_ui.render_new_disk.return_value = ""
        _handle_new_disk("/dev/sdb", {})
        mock_assign.assert_not_called()
        # F-100: by-id still empty after the settle+rescan retry -> scanned twice
        assert mock_core.scan_one.call_count == 2

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch.hba")
    def test_handle_new_disk_retries_when_by_id_missing(self, mock_hba, mock_core,
                                                        mock_ui, mock_assign):
        from b2ctl.watch import _handle_new_disk
        # by-id empty on the 1st scan_one, present on the 2nd -> settle + rescan
        # once before assigning.
        mock_core.scan_one.side_effect = [
            Disk(dev="/dev/sdb", by_id=""),
            _disk(dev="/dev/sdb", by_id="/dev/disk/by-id/ata-X",
                  pool=None, vdev=None, vdev_state=None, smart_dtype=""),
        ]
        mock_ui.render_new_disk.return_value = ""
        _handle_new_disk("/dev/sdb", {})
        assert mock_core.scan_one.call_count == 2
        mock_assign.assert_called_once()


# ========================================================================== #
# F-101 — _assign_free_disk mutating menu choices (add-spare / replace / add / wipe)
# ========================================================================== #

class TestAssignFreeDisk:
    """F-101: exercise the mutating menu choices of _assign_free_disk — exact argv
    for the destructive commands, the rpool proxmox-boot-tool warning, and that
    declining a confirmation never reaches the mutation."""

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="2")
    def test_choice2_add_spare_uses_by_id(self, _ask, _pick, _confirm, mock_zfs, _ui):
        from b2ctl.watch import _assign_free_disk
        d = _disk(dev="/dev/sdb", by_id="/dev/disk/by-id/ata-NEW",
                  pool=None, vdev=None, vdev_state=None, smart_dtype="")
        mock_zfs.add_spare.return_value = (True, "")
        _assign_free_disk(d, {})
        mock_zfs.add_spare.assert_called_once_with(
            "tank", "/dev/disk/by-id/ata-NEW", dry_run=False)

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.safety")
    @patch("b2ctl.watch._wait_resilver", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(True, ""))
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch._ask")
    def test_choice3_replace_degraded_cmd_and_rpool_boot_warning(
            self, mock_ask, mock_zfs, _cop, mock_rc, _wait, _safety, _loc, _ui, capsys):
        from b2ctl.watch import _assign_free_disk
        d = _disk(dev="/dev/sdb", by_id="/dev/disk/by-id/ata-NEW",
                  pool=None, vdev=None, vdev_state=None, smart_dtype="")
        # a FAULTED rpool (mirror) leaf is the replace target
        mock_zfs.degraded_leaves.return_value = [
            {"pool": "rpool", "token": "/dev/disk/by-id/wwn-OLD-part3",
             "state": "FAULTED", "vdev": "mirror-0"}]
        mock_zfs.topology.return_value = {}     # nothing lingering to detach
        mock_zfs.spares.return_value = []
        mock_ask.side_effect = ["3", "1"]       # menu action "3", pick leaf #1
        _assign_free_disk(d, {})
        # exact argv: zpool replace -f <pool> <old leaf token> <new by-id>
        assert mock_rc.call_args[0][0] == [
            "zpool", "replace", "-f", "rpool",
            "/dev/disk/by-id/wwn-OLD-part3", "/dev/disk/by-id/ata-NEW"]
        # rpool target must surface the proxmox-boot-tool reminder (§9)
        out = capsys.readouterr().out
        assert "proxmox-boot-tool" in out

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.run_check")
    @patch("b2ctl.watch._confirm", return_value=False)
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="5")
    def test_choice5_single_disk_add_requires_confirm(self, _ask, _pick, _confirm,
                                                      mock_rc, _ui):
        from b2ctl.watch import _assign_free_disk
        d = _disk(dev="/dev/sdb", by_id="/dev/disk/by-id/ata-NEW",
                  pool=None, vdev=None, vdev_state=None, smart_dtype="")
        _assign_free_disk(d, {})
        # declining the no-redundancy warning must never run `zpool add -f`
        mock_rc.assert_not_called()

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch._confirm", return_value=False)
    @patch("b2ctl.watch._ask", return_value="6")
    def test_choice6_wipe_declined_never_wipes(self, _ask, _confirm, mock_zfs, _ui):
        from b2ctl.watch import _assign_free_disk
        d = _disk(dev="/dev/sdb", by_id="/dev/disk/by-id/ata-NEW",
                  pool=None, vdev=None, vdev_state=None, smart_dtype="")
        _assign_free_disk(d, {})
        mock_zfs.wipe.assert_not_called()


class TestWatchBurnin(unittest.TestCase):
    """[b]urnin multi-select (space list) -> burnin.run_multi."""

    @patch("b2ctl.burnin.run_multi")
    @patch("b2ctl.burnin.load_state", return_value=[])
    @patch("b2ctl.watch._confirm")
    @patch("b2ctl.watch._ask")
    @patch("b2ctl.watch._avail_for_aux")
    def test_multi_select_calls_run_multi(self, mock_avail, mock_ask,
                                          mock_confirm, _load, mock_run_multi):
        from b2ctl import watch
        a = _disk(dev="/dev/sdb", serial="A", pool=None, vdev=None, vdev_state=None)
        b = _disk(dev="/dev/sdc", serial="B", pool=None, vdev=None, vdev_state=None)
        c = _disk(dev="/dev/sdd", serial="C", pool=None, vdev=None, vdev_state=None)
        mock_avail.return_value = [a, b, c]
        mock_ask.return_value = "1 3"               # pick 1st and 3rd
        mock_confirm.side_effect = [True, False]    # burn-in yes, scan no
        watch._cmd_burnin({})
        mock_run_multi.assert_called_once()
        picks = mock_run_multi.call_args[0][0]
        self.assertEqual([d.serial for d in picks], ["A", "C"])
        self.assertEqual(mock_run_multi.call_args.kwargs.get("do_scan"), False)

    @patch("b2ctl.burnin.status_view")
    @patch("b2ctl.burnin.run_multi")
    @patch("b2ctl.burnin.load_state",
           return_value=[{"serial": "X", "dev": "/dev/sdb", "bay": "1:0"}])
    @patch("b2ctl.watch._ask", return_value="v")
    def test_reattach_view_when_burnin_in_flight(self, _ask, _load,
                                                 mock_run_multi, mock_status):
        from b2ctl import watch
        watch._cmd_burnin({})
        mock_status.assert_called_once()            # [v] viewed the in-flight run
        mock_run_multi.assert_not_called()          # did not start a new one

    @patch("b2ctl.burnin.cancel", return_value=0)
    @patch("b2ctl.burnin.run_multi")
    @patch("b2ctl.burnin.load_state",
           return_value=[{"serial": "X", "dev": "/dev/sdb", "bay": "1:0"}])
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", side_effect=["c", "1"])
    def test_reattach_cancel_one(self, _ask, _confirm, _load,
                                 mock_run_multi, mock_cancel):
        from b2ctl import watch
        watch._cmd_burnin({})
        mock_cancel.assert_called_once()
        self.assertEqual(mock_cancel.call_args[0][0], ["X"])   # cancel by serial
        mock_run_multi.assert_not_called()

    @patch("b2ctl.burnin.cancel_all", return_value=0)
    @patch("b2ctl.burnin.run_multi")
    @patch("b2ctl.burnin.load_state",
           return_value=[{"serial": "X", "dev": "/dev/sdb", "bay": "1:0"}])
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask", return_value="a")
    def test_reattach_cancel_all(self, _ask, _confirm, _load,
                                 mock_run_multi, mock_cancel_all):
        from b2ctl import watch
        watch._cmd_burnin({})
        mock_cancel_all.assert_called_once()
        mock_run_multi.assert_not_called()


class TestPickIndices(unittest.TestCase):
    """Shared multi-select parse `_pick_indices` (F-052 guard + dedupe)."""

    def test_multi_dedupe_order_preserved(self):
        from b2ctl.watch import _pick_indices
        self.assertEqual(_pick_indices("2 3 4", 5), [1, 2, 3])
        self.assertEqual(_pick_indices("2 2 3", 5), [1, 2])   # dedupe, keep order
        self.assertEqual(_pick_indices("1", 5), [0])
        self.assertEqual(_pick_indices("", 5), [])

    def test_rejects_zero_negative_nonnumeric_and_out_of_range(self):
        from b2ctl.watch import _pick_indices
        for bad in ("0", "-1", "x", "1 0", "1 x", "6", "1 6"):   # 6 > n=5
            with self.assertRaises((ValueError, IndexError)):
                _pick_indices(bad, 5)

    def test_zero_never_wraps_to_last(self):
        # F-052: '0' -> index -1 must raise, never silently pick list[-1]
        from b2ctl.watch import _pick_indices
        with self.assertRaises(IndexError):
            _pick_indices("0", 3)


class TestAssignMultiSelect(unittest.TestCase):
    """[a]ssign multi-select: 1 pick -> single per-disk menu (unchanged); 2+ picks
    -> batch, homogeneous only. Mirrors the burnin space-separated idiom."""

    @staticmethod
    def _free(dev, sn):
        d = Disk(dev=dev); d.serial = sn; d.bay = "b"; return d

    @staticmethod
    def _perc(sn):
        d = Disk(dev="/dev/sda"); d.serial = sn
        d.smart_dtype = f"megaraid,{sn}"; d.pd_state = "UGood"; return d

    @patch("b2ctl.watch._assign_free_disks_batch")
    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.raid_actions.assign_perc_batch")
    @patch("b2ctl.watch._ask", return_value="1 2")
    @patch("b2ctl.watch.core")
    def test_batch_zfs_routes_to_free_batch(self, mock_core, _ask, perc_b, single, free_b):
        import b2ctl.watch as watch
        a = self._free("/dev/sdb", "A"); b = self._free("/dev/sdc", "B")
        mock_core.scan.return_value = [a, b]
        watch._cmd_assign({})
        free_b.assert_called_once()
        self.assertEqual(free_b.call_args[0][0], [a, b])
        single.assert_not_called()
        perc_b.assert_not_called()

    @patch("b2ctl.watch._assign_free_disks_batch")
    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._ask", return_value="2 2")
    @patch("b2ctl.watch.core")
    def test_dedupe_collapses_to_single(self, mock_core, _ask, single, free_b):
        import b2ctl.watch as watch
        a = self._free("/dev/sdb", "A"); b = self._free("/dev/sdc", "B")
        mock_core.scan.return_value = [a, b]
        watch._cmd_assign({})
        single.assert_called_once()             # 1 unique pick -> old menu
        self.assertIs(single.call_args[0][0], b)   # index 2 -> b
        free_b.assert_not_called()

    @patch("b2ctl.watch._assign_free_disks_batch")
    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch.core")
    def test_reject_zero_and_nonnumeric(self, mock_core, single, free_b):
        import b2ctl.watch as watch
        a = self._free("/dev/sdb", "A")
        mock_core.scan.return_value = [a]
        for bad in ("0", "x", "1 0", "1 x"):
            with patch("b2ctl.watch._ask", return_value=bad):
                watch._cmd_assign({})
        single.assert_not_called()
        free_b.assert_not_called()

    @patch("b2ctl.raid_actions.assign_perc_batch")
    @patch("b2ctl.watch._assign_free_disks_batch")
    @patch("b2ctl.watch._ask", return_value="1 2")
    @patch("b2ctl.watch.core")
    def test_mixed_categories_refused(self, mock_core, _ask, free_b, perc_b):
        import b2ctl.watch as watch
        free = self._free("/dev/sdb", "F")      # [1] zfs
        perc = self._perc("4")                  # [2] perc  (display order: zfs, ghost, perc)
        mock_core.scan.return_value = [free, perc]
        watch._cmd_assign({})
        free_b.assert_not_called()
        perc_b.assert_not_called()

    @patch("b2ctl.raid_actions.assign_perc_batch", return_value=0)
    @patch("b2ctl.watch._ask", return_value="1 2")
    @patch("b2ctl.watch.core")
    def test_batch_perc_routes_to_perc_batch(self, mock_core, _ask, perc_b):
        import b2ctl.watch as watch
        p1 = self._perc("1"); p2 = self._perc("2")
        mock_core.scan.return_value = [p1, p2]
        watch._cmd_assign({})
        perc_b.assert_called_once()
        self.assertEqual(perc_b.call_args[0][0], [p1, p2])


class TestRepairAux(unittest.TestCase):
    """_repair_aux branches by leaf class/state and honors dry-run."""

    NEW = "/dev/disk/by-id/ata-NEW"

    def _new_disk(self):
        return _disk(dev="/dev/sdz", by_id=self.NEW, serial="NEW1", bay="1:9",
                     pool=None, vdev=None, vdev_state=None, pool_token=None)

    @staticmethod
    def _leaf(klass, *, vdev, state, mirror_leg):
        return {"token": "/dev/disk/by-id/ata-OLD", "klass": klass, "vdev": vdev,
                "top_vdev": "logs" if klass == "log" else "cache", "state": state,
                "mirror_leg": mirror_leg, "degraded": True}

    def _run(self, leaf, *, dry=False, rc_ok=True, resilver=True):
        import b2ctl.watch as watch
        with patch("b2ctl.watch._confirm", return_value=True), \
             patch("b2ctl.watch.run_check", return_value=(rc_ok, "" if rc_ok else "err")) as mrc, \
             patch("b2ctl.watch._wait_resilver", return_value=resilver) as mwait, \
             patch("b2ctl.safety.begin_op", return_value="op1"), \
             patch("b2ctl.safety.end_op") as mend, \
             patch.object(watch, "_DRY_RUN", dry):
            ok = watch._repair_aux("tank", leaf, self._new_disk())
        cmds = [c.args[0] for c in mrc.call_args_list]
        return ok, cmds, mrc, mwait, mend

    def test_cache_is_remove_then_add_no_resilver(self):
        leaf = self._leaf("cache", vdev="cache", state="UNAVAIL", mirror_leg=False)
        ok, cmds, _mrc, mwait, mend = self._run(leaf)
        self.assertTrue(ok)
        self.assertEqual(cmds, [
            ["zpool", "remove", "tank", "/dev/disk/by-id/ata-OLD"],
            ["zpool", "add", "-f", "tank", "cache", self.NEW]])
        mwait.assert_not_called()                       # cache never resilvers
        self.assertIs(mend.call_args.args[1], True)     # audited success

    def test_log_mirror_leg_is_replace_and_waits(self):
        leaf = self._leaf("log", vdev="mirror-1", state="FAULTED", mirror_leg=True)
        ok, cmds, _mrc, mwait, _mend = self._run(leaf)
        self.assertTrue(ok)
        self.assertEqual(cmds, [
            ["zpool", "replace", "-f", "tank", "/dev/disk/by-id/ata-OLD", self.NEW]])
        mwait.assert_called_once_with("tank")

    def test_single_log_gone_is_remove_then_add_log(self):
        leaf = self._leaf("log", vdev="logs", state="REMOVED", mirror_leg=False)
        ok, cmds, _mrc, mwait, _mend = self._run(leaf)
        self.assertTrue(ok)
        self.assertEqual(cmds, [
            ["zpool", "remove", "tank", "/dev/disk/by-id/ata-OLD"],
            ["zpool", "add", "-f", "tank", "log", self.NEW]])
        mwait.assert_not_called()

    def test_single_log_present_is_replace(self):
        leaf = self._leaf("log", vdev="logs", state="FAULTED", mirror_leg=False)
        ok, cmds, _mrc, mwait, _mend = self._run(leaf)
        self.assertTrue(ok)
        self.assertEqual(cmds, [
            ["zpool", "replace", "-f", "tank", "/dev/disk/by-id/ata-OLD", self.NEW]])
        mwait.assert_called_once_with("tank")

    def test_dry_run_gates_writes_and_skips_resilver(self):
        leaf = self._leaf("log", vdev="mirror-1", state="FAULTED", mirror_leg=True)
        ok, _cmds, mrc, mwait, _mend = self._run(leaf, dry=True)
        self.assertTrue(ok)
        # every write threaded dry_run=True; resilver wait skipped under dry-run
        for call in mrc.call_args_list:
            self.assertTrue(call.kwargs.get("dry_run"))
        mwait.assert_not_called()

    def test_failed_command_aborts_and_audits_failure(self):
        leaf = self._leaf("cache", vdev="cache", state="UNAVAIL", mirror_leg=False)
        ok, cmds, _mrc, _mwait, mend = self._run(leaf, rc_ok=False)
        self.assertFalse(ok)
        self.assertEqual(len(cmds), 1)                  # stops at first failure
        self.assertIs(mend.call_args.args[1], False)

    def test_errored_resilver_returns_false(self):
        leaf = self._leaf("log", vdev="mirror-1", state="FAULTED", mirror_leg=True)
        ok, _cmds, _mrc, _mwait, mend = self._run(leaf, resilver=False)
        self.assertFalse(ok)
        self.assertIs(mend.call_args.args[1], False)

    def test_decline_confirm_does_not_begin_op(self):
        import b2ctl.watch as watch
        leaf = self._leaf("cache", vdev="cache", state="UNAVAIL", mirror_leg=False)
        with patch("b2ctl.watch._confirm", return_value=False), \
             patch("b2ctl.watch.run_check") as mrc, \
             patch("b2ctl.safety.begin_op") as mbegin:
            self.assertFalse(watch._repair_aux("tank", leaf, self._new_disk()))
        mbegin.assert_not_called()
        mrc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
