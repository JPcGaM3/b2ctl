"""Unit tests for b2ctl.zfs — topology parsing, membership, actions, resilver."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from helpers import (_disk, _MIRROR_STATUS, _RAIDZ_STATUS, _DEGRADED_STATUS,
                     _RESILVER_DONE, _RESILVER_PROGRESS, _RESILVER_DONE_WITH_ERRORS,
                     _SPARE_N_STATUS, _AUX_STATUS, _AUX_SINGLE_LOG_STATUS)
from b2ctl import zfs, watch, core
from b2ctl.common import Disk


# ========================================================================== #
# Topology parsing + membership + inspection
# ========================================================================== #

class TestZfsTopologyParsing:
    """Tests for ZFS topology parsing, membership, and pool inspection."""

    def test_parse_mirror_pool(self):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        assert "/dev/disk/by-id/wwn-0xAAA-part3" in topo
        assert "/dev/disk/by-id/wwn-0xBBB-part3" in topo
        e = topo["/dev/disk/by-id/wwn-0xAAA-part3"]
        assert e["pool"] == "rpool"
        assert e["vdev"] == "mirror-0"
        assert e["state"] == "ONLINE"

    def test_parse_raidz1_with_spares(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        assert "/dev/disk/by-id/wwn-0xCCC" in topo
        assert "/dev/disk/by-id/wwn-0xDDD" in topo
        assert "/dev/disk/by-id/wwn-0xEEE" in topo
        spare = topo["/dev/disk/by-id/wwn-0xFFF"]
        assert spare["vdev"] == "spares"  # note: parser stores the vdev keyword
        assert spare["state"] == "AVAIL"

    def test_parse_mixed_pools(self):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        pools = {e["pool"] for e in topo.values()}
        assert "rpool" in pools
        assert "tank" in pools

    def test_attach_membership_by_id(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        d = _disk(by_id="/dev/disk/by-id/wwn-0xCCC", pool=None, vdev=None,
                  vdev_state=None, pool_token=None)
        zfs.attach_membership([d], topo)
        assert d.pool == "tank"
        assert d.vdev == "raidz1-0"
        assert d.vdev_state == "ONLINE"

    def test_attach_membership_by_serial_fallback(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        d = _disk(by_id="/dev/disk/by-id/totally-different", serial="wwn-0xCCC",
                  pool=None, vdev=None, vdev_state=None, pool_token=None)
        zfs.attach_membership([d], topo)
        assert d.pool == "tank"

    @patch("b2ctl.zfs.topology")
    def test_degraded_leaves_finds_faulted(self, mock_topo):
        topo = {}
        zfs._parse("tank", _DEGRADED_STATUS, topo)
        mock_topo.return_value = topo
        bad = zfs.degraded_leaves()
        assert len(bad) == 1
        assert bad[0]["state"] == "FAULTED"
        assert bad[0]["pool"] == "tank"

    @patch("b2ctl.zfs.topology")
    def test_degraded_leaves_ignores_online(self, mock_topo):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        mock_topo.return_value = topo
        bad = zfs.degraded_leaves()
        assert len(bad) == 0

    @patch("b2ctl.zfs.topology")
    def test_can_detach_two_way_mirror_is_last_redundancy(self, mock_topo):
        # F-023: detaching a leg of the 2-way rpool mirror is NOT silently safe;
        # it removes the last redundancy and must be flagged, not approved.
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        mock_topo.return_value = topo
        assert zfs.can_detach("rpool", "/dev/disk/by-id/wwn-0xAAA-part3") is False
        assert zfs.detach_safety("rpool", "/dev/disk/by-id/wwn-0xAAA-part3") == "last_redundancy"

    @patch("b2ctl.zfs.topology")
    def test_can_detach_raidz_always_false(self, mock_topo):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        mock_topo.return_value = topo
        result = zfs.can_detach("tank", "/dev/disk/by-id/wwn-0xCCC")
        assert result is False

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_completed(self, mock_run):
        mock_run.return_value = _RESILVER_DONE
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is True
        assert st["done"] == 100.0

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_in_progress(self, mock_run):
        mock_run.return_value = _RESILVER_PROGRESS
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is False
        assert st["done"] == pytest.approx(45.2)
        assert "03:21" in st["eta"]

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_has_errors_key_in_progress(self, mock_run):
        # fix 2: has_errors must always be present (not just on completion)
        mock_run.return_value = _RESILVER_PROGRESS
        st = zfs.poll_resilver_status("tank")
        assert "has_errors" in st
        assert st["has_errors"] is False

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_completed_no_errors(self, mock_run):
        mock_run.return_value = _RESILVER_DONE
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is True
        assert st["has_errors"] is False

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_completed_with_errors(self, mock_run):
        mock_run.return_value = _RESILVER_DONE_WITH_ERRORS
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is True
        assert st["has_errors"] is True

    @patch("b2ctl.zfs.run")
    def test_spares_replacing_spare_n_container(self, mock_run):
        # fix 8: hot spare auto-activates as spare-N vdev, not replacing-N
        mock_run.return_value = _SPARE_N_STATUS
        result = zfs.spares_replacing("tank")
        assert "/dev/disk/by-id/wwn-0xFFF" in result
        assert result["/dev/disk/by-id/wwn-0xFFF"] == "/dev/disk/by-id/wwn-0xDDD"


# ========================================================================== #
# Resilver status parsing (extra real-world dumps)
# ========================================================================== #

class TestZfsResilver(unittest.TestCase):

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_status_in_progress(self, mock_run):
        mock_run.return_value = """  pool: tank
 state: DEGRADED
status: One or more devices is currently being resilvered.  The pool will
        continue to function, possibly in a degraded state.
action: Wait for the resilver to complete.
  scan: resilver in progress since Wed Jun 10 06:40:00 2026
        100G scanned at 500M/s, 50G issued at 250M/s, 1000G total
        50.0G resilvered, 5.00% done, 01:03:20 to go
"""
        status = zfs.poll_resilver_status("tank")
        self.assertIsNotNone(status)
        self.assertEqual(status["done"], 5.0)
        self.assertEqual(status["eta"], "01:03:20")
        self.assertFalse(status["completed"])

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_status_completed(self, mock_run):
        mock_run.return_value = """  pool: tank
 state: ONLINE
  scan: resilvered 1.23T in 05:43:21 with 0 errors on Wed Jun 10 12:23:21 2026
"""
        status = zfs.poll_resilver_status("tank")
        self.assertIsNotNone(status)
        self.assertTrue(status["completed"])
        self.assertEqual(status["done"], 100.0)

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_no_eta_still_in_progress(self, mock_run):
        # F-025: 'resilvered ... N% done, no estimated completion time' is an
        # IN-PROGRESS resilver, not completed-with-errors.
        from helpers import _RESILVER_NO_ETA
        mock_run.return_value = _RESILVER_NO_ETA
        st = zfs.poll_resilver_status("tank")
        self.assertFalse(st["completed"])
        self.assertEqual(st["done"], 24.83)
        self.assertFalse(st["has_errors"])
        self.assertEqual(st["eta"], "unknown")

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_empty_output_not_completed(self, mock_run):
        # F-025/F-055: a failed `zpool status` must not read as completed.
        mock_run.return_value = ""
        st = zfs.poll_resilver_status("tank")
        self.assertFalse(st["completed"])
        self.assertFalse(st["ok"])

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_scrub_not_treated_as_resilver(self, mock_run):
        mock_run.return_value = """  pool: tank
  scan: scrub in progress since Wed Jun 10 06:40:00 2026
        10.0% done, 00:30:00 to go
"""
        st = zfs.poll_resilver_status("tank")
        self.assertFalse(st["completed"])

    @patch('b2ctl.zfs.run')
    def test_poll_resilver_status_days_eta(self, mock_run):
        mock_run.return_value = """  pool: tank
  scan: resilver in progress since Wed Jun 10 06:40:00 2026
        1.50% done, 2 days 05:10:00 to go
"""
        status = zfs.poll_resilver_status("tank")
        self.assertIsNotNone(status)
        self.assertFalse(status["completed"])
        self.assertEqual(status["done"], 1.5)
        self.assertEqual(status["eta"], "2 days 05:10:00")


# ========================================================================== #
# Action wrappers (command building, mocked subprocess)
# ========================================================================== #

class TestZfsActions:
    """Tests for ZFS action wrappers (all subprocess calls mocked)."""

    @patch("b2ctl.zfs.run_check")
    def test_add_spare_command(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.add_spare("tank", "/dev/disk/by-id/wwn-0xABC")
        mock_rc.assert_called_with(["zpool", "add", "-f", "tank", "spare",
                                    "/dev/disk/by-id/wwn-0xABC"], dry_run=False)
        assert ok

    @patch("b2ctl.zfs.run_check")
    def test_replace_command(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.replace("tank", "old-dev", "new-dev")
        mock_rc.assert_called_with(["zpool", "replace", "-f", "tank",
                                    "old-dev", "new-dev"], dry_run=False)

    @patch("b2ctl.zfs.run_check")
    def test_create_pool_raidz1(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.create_pool("backup", "raidz1", ["/dev/sda", "/dev/sdb", "/dev/sdc"])
        args = mock_rc.call_args[0][0]
        assert "zpool" in args
        assert "create" in args
        assert "raidz1" in args
        assert "/dev/sda" in args

    @patch("b2ctl.zfs.run_check")
    def test_create_pool_stripe_no_raid_keyword(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.create_pool("fast", "stripe", ["/dev/sda"])
        args = mock_rc.call_args[0][0]
        assert "stripe" not in args
        assert "/dev/sda" in args

    @patch("b2ctl.zfs.run_check")
    def test_demote_to_spare_calls_detach_then_add(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.demote_to_spare("rpool", "/dev/disk/by-id/wwn-0xBBB-part3")
        assert mock_rc.call_count == 2
        first_call = mock_rc.call_args_list[0][0][0]
        second_call = mock_rc.call_args_list[1][0][0]
        assert "detach" in first_call
        assert "spare" in second_call

    def test_min_disks_constants(self):
        assert zfs.MIN_DISKS["stripe"] == 1
        assert zfs.MIN_DISKS["mirror"] == 2
        assert zfs.MIN_DISKS["raid10"] == 4
        assert zfs.MIN_DISKS["raidz1"] == 3
        assert zfs.MIN_DISKS["raidz2"] == 4


class TestZfsToolOverride(unittest.TestCase):
    """F-035: tool_paths.zpool override must reach the destructive call sites,
    not only the cron writer."""

    @patch("b2ctl.zfs.run_check")
    def test_zpool_override_used_in_add_spare(self, mock_rc):
        mock_rc.return_value = (True, "")
        with patch("b2ctl.config.tool", side_effect=lambda n: "/custom/zpool" if n == "zpool" else n):
            zfs.add_spare("tank", "/dev/disk/by-id/wwn-0xABC")
        argv = mock_rc.call_args[0][0]
        assert argv[0] == "/custom/zpool"

    @patch("b2ctl.zfs.run_check")
    def test_wipefs_override_used_in_wipe(self, mock_rc):
        mock_rc.return_value = (True, "")
        seen = []
        with patch("b2ctl.config.tool",
                   side_effect=lambda n: {"wipefs": "/x/wipefs", "sgdisk": "/x/sgdisk",
                                          "zpool": "/x/zpool"}.get(n, n)):
            zfs.wipe("/dev/sdz")
        argv0s = [c[0][0][0] for c in mock_rc.call_args_list]
        assert "/x/wipefs" in argv0s and "/x/sgdisk" in argv0s


class TestZfsWipe(unittest.TestCase):
    """F-108/F-062: wipe() aggregates real deletion steps; has_zfs_label
    fails closed so an unprobable disk is never treated as blank."""

    def _rc(self, results):
        """results keyed by argv[0..1] -> (ok, out)."""
        def fake(cmd, **kw):
            for key, val in results.items():
                if cmd[:len(key)] == list(key):
                    return val
            return (True, "")
        return fake

    def test_wipe_success_needs_wipefs_and_sgdisk(self):
        with patch("b2ctl.zfs.run_check", side_effect=self._rc({
                ("zpool", "labelclear"): (False, "no label"),   # best-effort
                ("wipefs",): (True, ""),
                ("sgdisk",): (True, "zapped")})):
            ok, msg = zfs.wipe("/dev/sdz")
        assert ok

    def test_wipe_fails_if_sgdisk_fails_even_when_labelclear_ok(self):
        # regression F-108: sgdisk alone must not report the whole wipe done
        with patch("b2ctl.zfs.run_check", side_effect=self._rc({
                ("zpool", "labelclear"): (True, ""),
                ("wipefs",): (True, ""),
                ("sgdisk",): (False, "busy")})):
            ok, msg = zfs.wipe("/dev/sdz")
        assert not ok and "sgdisk" in msg

    def test_wipe_fails_if_wipefs_fails(self):
        with patch("b2ctl.zfs.run_check", side_effect=self._rc({
                ("wipefs",): (False, "EBUSY"),
                ("sgdisk",): (True, "")})):
            ok, msg = zfs.wipe("/dev/sdz")
        assert not ok and "wipefs" in msg

    def test_labelclear_failure_tolerated(self):
        # a disk with no ZFS label -> labelclear non-zero is normal, not a wipe failure
        with patch("b2ctl.zfs.run_check", side_effect=self._rc({
                ("zpool", "labelclear"): (False, "failed to read label"),
                ("wipefs",): (True, ""),
                ("sgdisk",): (True, "")})):
            ok, _ = zfs.wipe("/dev/sdz")
        assert ok

    def test_has_zfs_label_fails_closed_on_probe_error(self):
        with patch("b2ctl.zfs.run_check", return_value=(False, "wipefs: not found")):
            assert zfs.has_zfs_label("/dev/sdz") is True

    def test_has_zfs_label_true_when_signature_listed(self):
        out = "DEVICE OFFSET TYPE\nsdz 0x438 zfs_member\n"
        with patch("b2ctl.zfs.run_check", return_value=(True, out)):
            assert zfs.has_zfs_label("/dev/sdz") is True

    def test_has_zfs_label_false_when_blank(self):
        with patch("b2ctl.zfs.run_check", return_value=(True, "")):
            assert zfs.has_zfs_label("/dev/sdz") is False


class TestZfsDemoteCompensation(unittest.TestCase):
    """F-061: demote_to_spare surfaces a retry hint if 'add spare' fails
    after the detach already succeeded (disk now floating, not in any vdev)."""

    @patch("b2ctl.zfs.run_check")
    def test_add_spare_failure_reports_retry(self, mock_rc):
        mock_rc.side_effect = [(True, ""), (False, "pool busy")]
        ok, msg = zfs.demote_to_spare("rpool", "/dev/disk/by-id/wwn-part3")
        assert not ok
        assert "add" in msg and "spare" in msg and "rpool" in msg


# ========================================================================== #
# create_pool full command assertions
# ========================================================================== #

class TestZfsCreatePool(unittest.TestCase):

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_mirror(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        ok, out = zfs.create_pool("tank", "mirror", ["/dev/sda", "/dev/sdb"])
        self.assertTrue(ok)
        cmd = mock_run_check.call_args[0][0]
        for flag, kv in (("-o", "ashift=12"), ("-o", "autotrim=on"),
                         ("-O", "compression=lz4"), ("-O", "atime=off"),
                         ("-O", "xattr=sa"), ("-O", "dnodesize=auto"),
                         ("-O", "acltype=posixacl"), ("-O", "recordsize=128K")):
            self.assertIn(kv, cmd)
            self.assertEqual(cmd[cmd.index(kv) - 1], flag)
        self.assertEqual(cmd[-4:], ["tank", "mirror", "/dev/sda", "/dev/sdb"])

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_stripe(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        ok, out = zfs.create_pool("tank", "stripe", ["/dev/sda", "/dev/sdb"])
        self.assertTrue(ok)
        cmd = mock_run_check.call_args[0][0]
        self.assertNotIn("stripe", cmd)          # stripe = no raid keyword
        self.assertEqual(cmd[-3:], ["tank", "/dev/sda", "/dev/sdb"])

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_custom_opts(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        zfs.create_pool("tank", "mirror", ["/dev/sda", "/dev/sdb"],
                        fs_opts={"compression": "zstd", "recordsize": "16K"})
        cmd = mock_run_check.call_args[0][0]
        self.assertIn("compression=zstd", cmd)
        self.assertIn("recordsize=16K", cmd)
        self.assertNotIn("compression=lz4", cmd)

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_raid10_stripe_of_mirrors(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        ok, _ = zfs.create_pool("tank", "raid10",
                                ["/dev/a", "/dev/b", "/dev/c", "/dev/d"])
        self.assertTrue(ok)
        cmd = mock_run_check.call_args[0][0]
        self.assertEqual(cmd[-7:], ["tank", "mirror", "/dev/a", "/dev/b",
                                    "mirror", "/dev/c", "/dev/d"])

    def test_create_pool_raid10_rejects_odd(self):
        ok, msg = zfs.create_pool("tank", "raid10", ["/dev/a", "/dev/b", "/dev/c"])
        self.assertFalse(ok)
        self.assertIn("even", msg)

    def test_create_pool_raid10_rejects_too_few(self):
        ok, msg = zfs.create_pool("tank", "raid10", ["/dev/a", "/dev/b"])
        self.assertFalse(ok)


class TestPoolLevel(unittest.TestCase):
    """pool_level derives the data-vdev redundancy type, ignoring aux vdevs."""

    def _topo(self, entries):
        return {str(i): e for i, e in enumerate(entries)}

    def test_mirror(self):
        topo = self._topo([{"pool": "rpool", "vdev": "mirror-0"},
                           {"pool": "rpool", "vdev": "mirror-0"}])
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.pool_level("rpool"), "mirror")

    def test_raidz1_ignores_cache_log(self):
        topo = self._topo([{"pool": "tank", "vdev": "raidz1-0"},
                           {"pool": "tank", "vdev": "cache"},
                           {"pool": "tank", "vdev": "log"}])
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.pool_level("tank"), "raidz1")

    def test_mixed(self):
        topo = self._topo([{"pool": "tank", "vdev": "mirror-0"},
                           {"pool": "tank", "vdev": "raidz1-1"}])
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.pool_level("tank"), "mixed")

    def test_stripe_when_no_redundant_vdev(self):
        topo = self._topo([{"pool": "fast", "vdev": "fast"}])
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.pool_level("fast"), "stripe")


class TestZfsAuxVdevs(unittest.TestCase):
    """L2ARC cache + SLOG log + aux removal wrappers."""

    @patch('b2ctl.zfs.run_check')
    def test_add_cache(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.add_cache("tank", ["/dev/nvme0n1"])
        mock_rc.assert_called_with(
            ["zpool", "add", "-f", "tank", "cache", "/dev/nvme0n1"], dry_run=False)

    @patch('b2ctl.zfs.run_check')
    def test_add_log_single_no_mirror(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.add_log("tank", ["/dev/ssd0"])
        mock_rc.assert_called_with(
            ["zpool", "add", "-f", "tank", "log", "/dev/ssd0"], dry_run=False)

    @patch('b2ctl.zfs.run_check')
    def test_add_log_two_devs_mirrored(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.add_log("tank", ["/dev/ssd0", "/dev/ssd1"])
        mock_rc.assert_called_with(
            ["zpool", "add", "-f", "tank", "log", "mirror", "/dev/ssd0", "/dev/ssd1"],
            dry_run=False)

    @patch('b2ctl.zfs.run_check')
    def test_remove_vdev(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.remove_vdev("tank", "/dev/nvme0n1", dry_run=True)
        mock_rc.assert_called_with(
            ["zpool", "remove", "tank", "/dev/nvme0n1"], dry_run=True)

    @patch('b2ctl.zfs.run_check')
    def test_destroy_pool(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        zfs.destroy_pool("tank")
        mock_run_check.assert_called_once_with(["zpool", "destroy", "tank"], dry_run=False)


class TestZfsAuxLeaves(unittest.TestCase):
    """aux_leaves() tags cache/log leaves for the repair flow (F-aux-repair)."""

    def _leaves(self, status, pool=None):
        topo = {}
        zfs._parse("tank", status, topo)
        with patch("b2ctl.zfs.topology", return_value=topo):
            return zfs.aux_leaves(pool)

    def test_classifies_cache_and_mirrored_log(self):
        leaves = self._leaves(_AUX_STATUS)
        by_tok = {l["token"]: l for l in leaves}
        # only cache/log leaves — the 3 raidz data disks are excluded
        self.assertEqual(len(leaves), 3)
        self.assertNotIn("/dev/disk/by-id/wwn-0xCCC", by_tok)

        cache = by_tok["/dev/disk/by-id/ata-CACHE"]
        self.assertEqual(cache["klass"], "cache")
        self.assertFalse(cache["mirror_leg"])
        self.assertTrue(cache["degraded"])          # UNAVAIL

        good_leg = by_tok["/dev/disk/by-id/ata-LOGA"]
        self.assertEqual(good_leg["klass"], "log")
        self.assertTrue(good_leg["mirror_leg"])     # vdev='mirror-1' under logs
        self.assertFalse(good_leg["degraded"])      # ONLINE

        dead_leg = by_tok["/dev/disk/by-id/ata-LOGB"]
        self.assertTrue(dead_leg["mirror_leg"])
        self.assertTrue(dead_leg["degraded"])       # FAULTED
        self.assertEqual(dead_leg["vdev"], "mirror-1")

    def test_single_log_is_not_a_mirror_leg(self):
        leaves = self._leaves(_AUX_SINGLE_LOG_STATUS)
        self.assertEqual(len(leaves), 1)
        leaf = leaves[0]
        self.assertEqual(leaf["klass"], "log")
        self.assertFalse(leaf["mirror_leg"])        # vdev='logs', not 'mirror-*'
        self.assertTrue(leaf["degraded"])           # UNAVAIL

    def test_pool_filter(self):
        # a leaf on another pool is filtered out when pool= is given
        topo = {}
        zfs._parse("tank", _AUX_STATUS, topo)
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.aux_leaves("rpool"), [])
            self.assertEqual(len(zfs.aux_leaves("tank")), 3)

    def test_dedupes_by_pool_token(self):
        # _parse indexes /dev paths under both token and realpath; aux_leaves
        # must not emit the same (pool, token) twice.
        leaves = self._leaves(_AUX_STATUS)
        toks = [l["token"] for l in leaves]
        self.assertEqual(len(toks), len(set(toks)))

    def test_stripe_pool_named_like_aux_is_not_misclassified(self):
        # a single-disk/stripe pool's data leaf has top_vdev == pool name; a pool
        # NAMED with a 'cache'/'log' substring must NOT have its data disk read as
        # an aux vdev (top == pool guard).
        status = ("  pool: cachebox\n state: ONLINE\nconfig:\n\n"
                  "\tNAME        STATE\n"
                  "\tcachebox    ONLINE\n"
                  "\t  /dev/disk/by-id/ata-ONLY   ONLINE\n\n"
                  "errors: No known data errors\n")
        topo = {}
        zfs._parse("cachebox", status, topo)
        with patch("b2ctl.zfs.topology", return_value=topo):
            self.assertEqual(zfs.aux_leaves("cachebox"), [])


class TestZfsOffline(unittest.TestCase):

    @patch('b2ctl.zfs.run_check')
    def test_offline_cmd(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.offline("tank", "/dev/disk/by-id/x")
        mock_rc.assert_called_once_with(
            ["zpool", "offline", "tank", "/dev/disk/by-id/x"], dry_run=False)

    def _topo(self, states):
        # states: {token: state} for a raidz1-0 vdev in pool tank
        return {t: {"pool": "tank", "vdev": "raidz1-0", "token": t, "state": s}
                for t, s in states.items()}

    def test_can_offline_true_when_all_others_online(self):
        topo = self._topo({"a": "ONLINE", "b": "ONLINE", "c": "ONLINE"})
        with patch("b2ctl.zfs.topology", return_value=topo):
            assert zfs.can_offline("tank", "a") is True

    def test_can_offline_false_when_another_member_down(self):
        topo = self._topo({"a": "ONLINE", "b": "OFFLINE", "c": "ONLINE"})
        with patch("b2ctl.zfs.topology", return_value=topo):
            assert zfs.can_offline("tank", "a") is False

    def test_can_offline_false_for_stripe(self):
        topo = {"a": {"pool": "tank", "vdev": "tank", "token": "a", "state": "ONLINE"}}
        with patch("b2ctl.zfs.topology", return_value=topo):
            assert zfs.can_offline("tank", "a") is False


class TestZfsPoolTimers(unittest.TestCase):
    """Per-pool maintenance via distro systemd timers (v0.16.0, replaces cron)."""

    def _cmds(self, rc):
        return [c.args[0] for c in rc.call_args_list]

    def test_install_enables_scrub_and_trim(self):
        from unittest.mock import patch
        with patch("b2ctl.zfs._timer_template_exists", return_value=True), \
             patch("b2ctl.zfs.run_check", return_value=(True, "")) as rc, \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            ok, msg = zfs.install_pool_timers("tank")
        assert ok
        cmds = self._cmds(rc)
        enables = [c[3] for c in cmds if c[1] == "enable"]
        assert "zfs-scrub-monthly@tank.timer" in enables
        assert "zfs-trim-monthly@tank.timer" in enables
        # each enabled kind suppresses the distro all-pools cron (no double-scrub)
        props = [c[2] for c in cmds if c[:2] == ["zpool", "set"]]
        assert "org.debian:periodic-scrub=disable" in props
        assert "org.debian:periodic-trim=disable" in props

    def test_install_scrub_only_when_no_trim(self):
        # include_trim=False (autotrim=on): scrub timer + scrub property only
        from unittest.mock import patch
        with patch("b2ctl.zfs._timer_template_exists", return_value=True), \
             patch("b2ctl.zfs.run_check", return_value=(True, "")) as rc, \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            ok, msg = zfs.install_pool_timers("tank", include_trim=False)
        assert ok
        cmds = self._cmds(rc)
        assert [c[3] for c in cmds if c[1] == "enable"] == ["zfs-scrub-monthly@tank.timer"]
        props = [c[2] for c in cmds if c[:2] == ["zpool", "set"]]
        assert props == ["org.debian:periodic-scrub=disable"]     # no trim property

    def test_install_missing_trim_template_still_enables_scrub(self):
        # scrub template present, trim absent (autotrim=off): scrub enables + its
        # property disabled; trim warns; ok stays True (scrub IS scheduled).
        from unittest.mock import patch
        with patch("b2ctl.zfs._timer_template_exists", side_effect=lambda k: k == "scrub"), \
             patch("b2ctl.zfs.run_check", return_value=(True, "")) as rc, \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            ok, msg = zfs.install_pool_timers("tank", include_trim=True)
        assert ok is True
        cmds = self._cmds(rc)
        assert [c[3] for c in cmds if c[1] == "enable"] == ["zfs-scrub-monthly@tank.timer"]
        assert [c[2] for c in cmds if c[:2] == ["zpool", "set"]] == \
               ["org.debian:periodic-scrub=disable"]     # no trim property when no trim timer
        assert "trim" in msg.lower()

    def test_install_property_set_failure_keeps_ok(self):
        # a failed `zpool set` is best-effort (worst case: an extra scrub, never a
        # gap) — it must NOT flip ok to False.
        from unittest.mock import patch

        def rc(args, *a, **k):
            return (False, "permission denied") if args[1] == "set" else (True, "")
        with patch("b2ctl.zfs._timer_template_exists", return_value=True), \
             patch("b2ctl.zfs.run_check", side_effect=rc), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            ok, msg = zfs.install_pool_timers("tank", include_trim=False)
        assert ok is True
        assert "periodic-scrub" in msg

    def test_install_template_missing_warns_and_enables_nothing(self):
        # no zfs-scrub-monthly@.timer on the box -> ok=False, nothing enabled (the
        # silent-scrub-gap the fix guards against)
        from unittest.mock import patch
        with patch("b2ctl.zfs._timer_template_exists", return_value=False), \
             patch("b2ctl.zfs.run_check") as rc:
            ok, msg = zfs.install_pool_timers("tank")
        assert ok is False
        rc.assert_not_called()
        assert "template not found" in msg

    def test_install_threads_dry_run(self):
        from unittest.mock import patch
        with patch("b2ctl.zfs._timer_template_exists", return_value=True), \
             patch("b2ctl.zfs.run_check", return_value=(True, "")) as rc, \
             patch("b2ctl.config.tool", return_value="systemctl"):
            zfs.install_pool_timers("tank", dry_run=True)
        assert rc.call_args_list and all(
            c.kwargs.get("dry_run") is True for c in rc.call_args_list)

    def test_template_exists_probe_reads_list_unit_files(self):
        # probe goes through run() (read-only, never dry-run-gated)
        from unittest.mock import patch
        with patch("b2ctl.zfs.run", return_value="zfs-scrub-monthly@.timer disabled"), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            assert zfs._timer_template_exists("scrub") is True
        with patch("b2ctl.zfs.run", return_value=""), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            assert zfs._timer_template_exists("trim") is False

    def test_remove_disables_both_timers(self):
        from unittest.mock import patch
        with patch("b2ctl.zfs.run_check", return_value=(True, "")) as rc, \
             patch("b2ctl.config.tool", return_value="systemctl"):
            ok, msg = zfs.remove_pool_timers("tank")
        assert ok
        units = [c[3] for c in self._cmds(rc)]
        assert "zfs-scrub-monthly@tank.timer" in units
        assert "zfs-trim-monthly@tank.timer" in units
        assert all(c[:3] == ["systemctl", "disable", "--now"] for c in self._cmds(rc))

    def test_remove_reports_failure(self):
        # a genuine disable failure must surface ok=False (not a green success)
        from unittest.mock import patch
        with patch("b2ctl.zfs.run_check", return_value=(False, "D-Bus down")), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            ok, msg = zfs.remove_pool_timers("tank")
        assert ok is False
        assert "disable failed" in msg

    def test_prune_disables_orphans_keeps_live(self):
        # `live` must come from the guarded `zpool list` output (out2), NOT a second
        # list_pools() call — patch list_pools to blow up to prove it's unused.
        from unittest.mock import patch
        listing = ("zfs-scrub-monthly@tank.timer loaded active waiting X\n"
                   "zfs-scrub-monthly@ghost.timer loaded active waiting X\n"
                   "zfs-trim-monthly@ghost.timer loaded active waiting X\n")

        def fake_rc(args, *a, **k):
            if args[1] == "list":                 # zpool list -H -o name
                return True, "rpool\ntank\n"
            return True, ""                       # systemctl disable
        with patch("b2ctl.zfs.run", return_value=listing), \
             patch("b2ctl.zfs.run_check", side_effect=fake_rc), \
             patch("b2ctl.zfs.list_pools", side_effect=AssertionError("must not call list_pools")), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            disabled = zfs.prune_orphan_timers()
        assert set(disabled) == {"zfs-scrub-monthly@ghost.timer",
                                 "zfs-trim-monthly@ghost.timer"}
        assert "zfs-scrub-monthly@tank.timer" not in disabled

    def test_prune_skips_when_zpool_list_fails(self):
        # F-063: a transient `zpool list` failure must NOT disable every timer.
        from unittest.mock import patch
        with patch("b2ctl.zfs.run", return_value="zfs-scrub-monthly@ghost.timer loaded active waiting X"), \
             patch("b2ctl.zfs.run_check", return_value=(False, "error")), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            assert zfs.prune_orphan_timers() == []

    def test_prune_no_units_returns_empty(self):
        from unittest.mock import patch
        with patch("b2ctl.zfs.run", return_value=""), \
             patch("b2ctl.config.tool", return_value="systemctl"):
            assert zfs.prune_orphan_timers() == []


# ========================================================================== #
# can_detach guard + demote_to_spare orchestration
# ========================================================================== #

class TestZfsCanDetachDemote(unittest.TestCase):

    @patch('b2ctl.zfs.topology')
    def test_can_detach_raidz(self, mock_topo):
        mock_topo.return_value = {
            "dev1": {"pool": "tank", "vdev": "raidz1-0", "state": "ONLINE", "token": "dev1"},
            "dev2": {"pool": "tank", "vdev": "raidz1-0", "state": "ONLINE", "token": "dev2"},
        }
        self.assertFalse(zfs.can_detach("tank", "dev1"))

    @patch('b2ctl.zfs.topology')
    def test_can_detach_three_way_mirror_safe(self, mock_topo):
        # 3-way mirror: detaching one leg leaves 2 ONLINE members = still
        # redundant, so it is genuinely safe.
        mock_topo.return_value = {
            "dev1": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev1"},
            "dev2": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev2"},
            "dev3": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev3"},
        }
        self.assertTrue(zfs.can_detach("tank", "dev1"))
        self.assertEqual(zfs.detach_safety("tank", "dev1"), "ok")

    @patch('b2ctl.zfs.topology')
    def test_can_detach_mirror_unsafe(self, mock_topo):
        mock_topo.return_value = {
            "dev1": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev1"},
            "dev2": {"pool": "tank", "vdev": "mirror-0", "state": "OFFLINE", "token": "dev2"},
        }
        self.assertFalse(zfs.can_detach("tank", "dev1"))
        self.assertEqual(zfs.detach_safety("tank", "dev1"), "refuse")

    @patch('b2ctl.zfs.topology')
    def test_can_offline_false_during_spare_rebuild(self, mock_topo):
        # F-024: a REMOVED original nested in spare-1 must block offlining a 2nd
        # member of the same raidz1 (already-degraded vdev, zero margin).
        topo = {}
        zfs._parse("tank", _SPARE_N_STATUS, topo)
        mock_topo.return_value = topo
        # wwn-0xCCC is a healthy direct member of raidz1-0; wwn-0xDDD is REMOVED
        # inside spare-1 (same top_vdev raidz1-0). Offlining CCC must be refused.
        assert zfs.can_offline("tank", "/dev/disk/by-id/wwn-0xCCC") is False

    @patch('b2ctl.zfs.detach')
    @patch('b2ctl.zfs.add_spare')
    def test_demote_to_spare(self, mock_add_spare, mock_detach):
        mock_detach.return_value = (True, "")
        mock_add_spare.return_value = (True, "")

        ok, out = zfs.demote_to_spare("tank", "dev1")

        self.assertTrue(ok)
        mock_detach.assert_called_once_with("tank", "dev1", dry_run=False)
        mock_add_spare.assert_called_once_with("tank", "dev1", dry_run=False)

    @patch('b2ctl.zfs.detach')
    @patch('b2ctl.zfs.add_spare')
    def test_demote_to_spare_detach_fails(self, mock_add_spare, mock_detach):
        mock_detach.return_value = (False, "error")

        ok, out = zfs.demote_to_spare("tank", "dev1")

        self.assertFalse(ok)
        self.assertEqual(out, "error")
        mock_detach.assert_called_once_with("tank", "dev1", dry_run=False)
        mock_add_spare.assert_not_called()


# ========================================================================== #
# pool_token: attach_membership maps whole-disk by_id to the -part leaf token
# ========================================================================== #

class TestFeatureFixPoolToken(unittest.TestCase):
    def test_pool_token_assigned_and_used(self):
        # 1. Build a topo whose member token is part1
        topo = {
            "/dev/disk/by-id/wwn-0xABC-part1": {
                "pool": "tank",
                "vdev": "raidz1-0",
                "state": "ONLINE",
                "token": "/dev/disk/by-id/wwn-0xABC-part1"
            }
        }

        # 2. Disk whose by_id is the whole-disk
        d = Disk(dev="/dev/sdb", by_id="/dev/disk/by-id/wwn-0xABC", serial="12345")

        # 3. Assert attach_membership sets pool_token
        zfs.attach_membership([d], topo)
        self.assertEqual(d.pool_token, "/dev/disk/by-id/wwn-0xABC-part1")
        self.assertEqual(d.pool, "tank")

        with patch("b2ctl.zfs.detach") as mock_detach, \
             patch("b2ctl.zfs.poll_resilver_status") as mock_poll, \
             patch("b2ctl.zfs.topology") as mock_topo, \
             patch("b2ctl.core.scan") as mock_scan, \
             patch("b2ctl.watch._confirm_op", return_value=True), \
             patch("b2ctl.watch.run_check") as mock_run_check, \
             patch("b2ctl.safety.begin_op", return_value="test-op-id"), \
             patch("b2ctl.safety.end_op"), \
             patch("b2ctl.watch._ask", return_value="1"), \
             patch("b2ctl.locate.blink"):

            spare_disk = Disk(dev="/dev/sdc", by_id="/dev/disk/by-id/wwn-SPARE", serial="67890", pool="tank", vdev="spares", vdev_state="AVAIL")
            mock_scan.return_value = [d, spare_disk]
            mock_run_check.return_value = (True, "")
            mock_detach.return_value = (True, "")
            mock_poll.return_value = {"completed": True, "done": 100.0, "eta": "", "has_errors": False}
            mock_topo.return_value = topo

            # mock sys.stdout.write and flush
            with patch("sys.stdout.write"), patch("sys.stdout.flush"), patch("builtins.print"):
                watch._cmd_replace(None)

            mock_run_check.assert_called_with(
                ["zpool", "replace", "-f", "tank", "/dev/disk/by-id/wwn-0xABC-part1", "/dev/disk/by-id/wwn-SPARE"],
                dry_run=False
            )


# ========================================================================== #
# F-104 / F-105 / F-107 — read-side helpers, spares dedup, shared-topo guards
# ========================================================================== #

class TestListPools:
    """F-104: list_pools() parses the tab-separated `zpool list -H` dump."""

    @patch("b2ctl.zfs.run")
    def test_parses_tab_output(self, mock_run):
        mock_run.return_value = (
            "rpool\t952G\t120G\t832G\tONLINE\t3%\t12%\n"
            "tank\t2.72T\t1.20T\t1.52T\tONLINE\t8%\t44%\n"
        )
        pools = zfs.list_pools()
        assert [p["name"] for p in pools] == ["rpool", "tank"]
        assert pools[0] == {"name": "rpool", "size": "952G", "alloc": "120G",
                            "free": "832G", "health": "ONLINE", "frag": "3%",
                            "cap": "12%"}
        assert pools[1]["health"] == "ONLINE" and pools[1]["cap"] == "44%"

    @patch("b2ctl.zfs.run")
    def test_short_lines_ignored(self, mock_run):
        # A locale-/space-mangled line lacking the 7 tab fields must be dropped,
        # never half-parsed into a bogus pool (blast radius: prune_orphan_crons
        # deletes crons of any pool NOT in list_pools()).
        mock_run.return_value = "rpool 952G ONLINE\n\n"
        assert zfs.list_pools() == []


class TestSparesReadHelper:
    """F-104/F-105: spares() returns AVAIL spare tokens, de-duplicated."""

    @patch("b2ctl.zfs.run")
    def test_avail_tokens_from_raidz_status(self, mock_run):
        mock_run.return_value = _RAIDZ_STATUS
        assert zfs.spares("tank") == ["/dev/disk/by-id/wwn-0xFFF"]

    @patch("b2ctl.zfs.run", return_value="ignored")
    def test_spares_dedup_token_and_realpath_alias(self, mock_run):
        # F-105: _parse indexes each leaf under BOTH its token and its realpath,
        # so on real hardware topo.values() yields the same spare twice. spares()
        # must return the token exactly once. Register one entry under two keys.
        entry = {"pool": "tank", "vdev": "spares", "state": "AVAIL",
                 "token": "/dev/disk/by-id/wwn-0xFFF", "top_vdev": "spares"}

        def _fake_parse(pool, text, topo):
            topo["/dev/disk/by-id/wwn-0xFFF"] = entry   # by-id token
            topo["/dev/sdz"] = entry                     # realpath alias -> same dict

        with patch("b2ctl.zfs._parse", side_effect=_fake_parse):
            assert zfs.spares("tank") == ["/dev/disk/by-id/wwn-0xFFF"]


class TestHasZfsLabelReadSide:
    """F-104: has_zfs_label() parses `wipefs -n`, failing CLOSED on probe error."""

    def test_label_lines_detected(self):
        out = "DEVICE OFFSET TYPE UUID LABEL\nsdz 0x438 zfs_member  \n"
        with patch("b2ctl.zfs.run_check", return_value=(True, out)):
            assert zfs.has_zfs_label("/dev/sdz") is True

    def test_fails_closed_on_probe_error(self):
        with patch("b2ctl.zfs.run_check", return_value=(False, "not found")):
            assert zfs.has_zfs_label("/dev/sdz") is True


class TestZfsCommandShapes:
    """F-104: pin the argv of attach()/swap_to_spare() — swap_to_spare must NOT
    carry -f (unlike replace()), or it would force-replace where ZFS refuses."""

    @patch("b2ctl.zfs.run_check")
    def test_swap_to_spare_has_no_force_flag(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.swap_to_spare("tank", "/dev/disk/by-id/member",
                          "/dev/disk/by-id/spare")
        mock_rc.assert_called_once_with(
            ["zpool", "replace", "tank", "/dev/disk/by-id/member",
             "/dev/disk/by-id/spare"], dry_run=False)
        assert "-f" not in mock_rc.call_args[0][0]

    @patch("b2ctl.zfs.run_check")
    def test_attach_command_has_force(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.attach("rpool", "/dev/disk/by-id/existing", "/dev/disk/by-id/new")
        mock_rc.assert_called_once_with(
            ["zpool", "attach", "-f", "rpool", "/dev/disk/by-id/existing",
             "/dev/disk/by-id/new"], dry_run=False)


class TestSharedTopoSnapshot:
    """F-107: can_detach/can_offline/detach_safety accept a pre-built topo so an
    interactive flow does not spawn a fresh `zpool status` per guard."""

    @patch("b2ctl.zfs.topology")
    def test_can_detach_and_can_offline_accept_shared_topo(self, mock_topo):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        member = "/dev/disk/by-id/wwn-0xCCC"
        # raidz1 member: detach refused, but offline safe (others all ONLINE).
        assert zfs.can_detach("tank", member, topo=topo) is False
        assert zfs.detach_safety("tank", member, topo=topo) == "refuse"
        assert zfs.can_offline("tank", member, topo=topo) is True
        # The whole point of F-107: no fresh topology() subprocess was spawned.
        mock_topo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
