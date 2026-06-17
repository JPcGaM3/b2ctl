import unittest
from unittest.mock import patch
from b2ctl import zfs

class TestZfsCreatePool(unittest.TestCase):

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_mirror(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        ok, out = zfs.create_pool("tank", "mirror", ["/dev/sda", "/dev/sdb"])
        self.assertTrue(ok)
        mock_run_check.assert_called_once_with([
            "zpool", "create", "-f", "-o", "ashift=12", "-O", "compression=lz4", 
            "-O", "atime=off", "-O", "xattr=sa", "-o", "autotrim=on", "tank", 
            "mirror", "/dev/sda", "/dev/sdb"
        ])

    @patch('b2ctl.zfs.run_check')
    def test_create_pool_stripe(self, mock_run_check):
        mock_run_check.return_value = (True, "")
        ok, out = zfs.create_pool("tank", "stripe", ["/dev/sda", "/dev/sdb"])
        self.assertTrue(ok)
        mock_run_check.assert_called_once_with([
            "zpool", "create", "-f", "-o", "ashift=12", "-O", "compression=lz4", 
            "-O", "atime=off", "-O", "xattr=sa", "-o", "autotrim=on", "tank", 
            "/dev/sda", "/dev/sdb"
        ])

if __name__ == '__main__':
    unittest.main()
