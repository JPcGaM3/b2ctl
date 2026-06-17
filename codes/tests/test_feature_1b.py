import unittest
from unittest.mock import patch, call
from b2ctl import zfs, watch, core
from b2ctl.common import Disk

class TestFeature1b(unittest.TestCase):

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
        mock_detach.assert_called_once_with("tank", "dev1")
        mock_add_spare.assert_called_once_with("tank", "dev1")

    @patch('b2ctl.zfs.detach')
    @patch('b2ctl.zfs.add_spare')
    def test_demote_to_spare_detach_fails(self, mock_add_spare, mock_detach):
        mock_detach.return_value = (False, "error")

        ok, out = zfs.demote_to_spare("tank", "dev1")

        self.assertFalse(ok)
        self.assertEqual(out, "error")
        mock_detach.assert_called_once_with("tank", "dev1")
        mock_add_spare.assert_not_called()

    @patch('b2ctl.watch.core.scan')
    @patch('b2ctl.watch._ask')
    @patch('b2ctl.watch._confirm')
    @patch('b2ctl.watch.zfs.spares')
    @patch('b2ctl.watch.zfs.swap_to_spare')
    @patch('b2ctl.watch.zfs.poll_resilver_status')
    @patch('b2ctl.watch.zfs.detach')
    @patch('b2ctl.watch.locate.blink')
    @patch('b2ctl.watch.time.sleep')
    def test_cmd_swap_success(
        self, mock_sleep, mock_blink, mock_detach, mock_poll, mock_swap, mock_spares,
        mock_confirm, mock_ask, mock_scan
    ):
        d1 = Disk("/dev/sda")
        d1.level = "WARNING"
        d1.pool = "tank"
        d1.by_id = "/dev/disk/by-id/sda"
        
        mock_scan.return_value = [d1]
        mock_ask.return_value = "1"
        mock_confirm.return_value = True
        mock_spares.return_value = ["spare1"]
        mock_swap.return_value = (True, "")
        mock_detach.return_value = (True, "")
        
        mock_poll.side_effect = [
            {"done": 50.0, "eta": "00:10:00", "completed": False},
            {"done": 100.0, "eta": "", "completed": True}
        ]
        
        watch._cmd_swap(None)
        
        mock_swap.assert_called_once_with("tank", "/dev/disk/by-id/sda", "spare1")
        self.assertEqual(mock_poll.call_count, 2)
        mock_detach.assert_called_once_with("tank", "/dev/disk/by-id/sda")
        mock_blink.assert_called_once_with("/dev/sda", watch.locate.DEFAULT_SECONDS)

    @patch('b2ctl.watch.core.scan')
    @patch('b2ctl.watch._ask')
    @patch('b2ctl.watch._confirm')
    @patch('b2ctl.watch.zfs.can_detach')
    @patch('b2ctl.watch.zfs.demote_to_spare')
    def test_cmd_demote_success(
        self, mock_demote, mock_can_detach, mock_confirm, mock_ask, mock_scan
    ):
        d1 = Disk("/dev/sda")
        d1.pool = "tank"
        d1.by_id = "/dev/disk/by-id/sda"
        
        mock_scan.return_value = [d1]
        mock_ask.return_value = "1"
        mock_can_detach.return_value = True
        mock_confirm.return_value = True
        mock_demote.return_value = (True, "")
        
        watch._cmd_demote(None)
        
        mock_can_detach.assert_called_once_with("tank", "/dev/disk/by-id/sda")
        mock_demote.assert_called_once_with("tank", "/dev/disk/by-id/sda")

if __name__ == '__main__':
    unittest.main()
