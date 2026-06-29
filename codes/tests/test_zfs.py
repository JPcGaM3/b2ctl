"""Unit tests for b2ctl.zfs — topology parsing, membership, actions, resilver."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from helpers import (_disk, _MIRROR_STATUS, _RAIDZ_STATUS, _DEGRADED_STATUS,
                     _RESILVER_DONE, _RESILVER_PROGRESS, _RESILVER_DONE_WITH_ERRORS,
                     _SPARE_N_STATUS)
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
    def test_can_detach_mirror_with_other_online(self, mock_topo):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        mock_topo.return_value = topo
        result = zfs.can_detach("rpool", "/dev/disk/by-id/wwn-0xAAA-part3")
        assert result is True

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
        assert zfs.MIN_DISKS["raidz1"] == 3
        assert zfs.MIN_DISKS["raidz2"] == 4


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
    def test_destroy_pool(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        zfs.destroy_pool("tank")
        mock_run_check.assert_called_once_with(["zpool", "destroy", "tank"], dry_run=False)


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


class TestZfsPoolCron(unittest.TestCase):

    def test_install_pool_cron_content(self):
        from unittest.mock import patch, mock_open
        m = mock_open()
        with patch("b2ctl.config.tool", return_value="/usr/sbin/zpool"), \
             patch("b2ctl.zfs.os.makedirs"), patch("b2ctl.zfs.os.chmod"), \
             patch("builtins.open", m):
            ok, path = zfs.install_pool_cron("tank")
        assert ok and path == "/etc/cron.d/b2ctl-tank"
        written = "".join(c.args[0] for c in m().write.call_args_list)
        assert "/usr/sbin/zpool trim tank" in written
        assert "/usr/sbin/zpool scrub tank" in written
        assert "24 0 1-7 * *" in written and "24 0 8-14 * *" in written
        assert "\\%w" in written           # cron-escaped date format

    def test_install_pool_cron_dry_run(self):
        from unittest.mock import patch
        with patch("b2ctl.config.tool", return_value="/usr/sbin/zpool"), \
             patch("builtins.open") as o:
            ok, msg = zfs.install_pool_cron("tank", dry_run=True)
        assert ok and "dry-run" in msg
        o.assert_not_called()

    def test_remove_pool_cron(self):
        from unittest.mock import patch
        with patch("b2ctl.zfs.os.path.exists", return_value=True), \
             patch("b2ctl.zfs.os.remove") as rm:
            ok, path = zfs.remove_pool_cron("tank")
        assert ok and path == "/etc/cron.d/b2ctl-tank"
        rm.assert_called_once_with("/etc/cron.d/b2ctl-tank")

    def test_prune_orphan_crons(self):
        from unittest.mock import patch
        files = ["/etc/cron.d/b2ctl-tank", "/etc/cron.d/b2ctl-ghost"]
        with patch("b2ctl.zfs.list_pools", return_value=[{"name": "tank"}]), \
             patch("b2ctl.zfs.glob.glob", return_value=files), \
             patch("b2ctl.zfs.os.remove") as rm:
            removed = zfs.prune_orphan_crons()
        assert removed == ["/etc/cron.d/b2ctl-ghost"]
        rm.assert_called_once_with("/etc/cron.d/b2ctl-ghost")


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
    def test_can_detach_mirror_safe(self, mock_topo):
        mock_topo.return_value = {
            "dev1": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev1"},
            "dev2": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev2"},
        }
        self.assertTrue(zfs.can_detach("tank", "dev1"))

    @patch('b2ctl.zfs.topology')
    def test_can_detach_mirror_unsafe(self, mock_topo):
        mock_topo.return_value = {
            "dev1": {"pool": "tank", "vdev": "mirror-0", "state": "ONLINE", "token": "dev1"},
            "dev2": {"pool": "tank", "vdev": "mirror-0", "state": "OFFLINE", "token": "dev2"},
        }
        self.assertFalse(zfs.can_detach("tank", "dev1"))

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


if __name__ == "__main__":
    unittest.main()
