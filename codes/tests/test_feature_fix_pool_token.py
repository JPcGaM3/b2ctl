import unittest
from unittest.mock import patch, MagicMock
from b2ctl.common import Disk
from b2ctl import zfs, watch

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
            mock_poll.return_value = {"completed": True, "done": 100.0, "eta": ""}
            mock_topo.return_value = topo

            # mock sys.stdout.write and flush
            with patch("sys.stdout.write"), patch("sys.stdout.flush"), patch("builtins.print"):
                watch._cmd_replace(None)

            mock_run_check.assert_called_with(
                ["zpool", "replace", "tank", "/dev/disk/by-id/wwn-0xABC-part1", "/dev/disk/by-id/wwn-SPARE"],
                dry_run=False
            )

if __name__ == "__main__":
    unittest.main()
