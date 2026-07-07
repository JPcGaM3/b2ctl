"""Unit tests for b2ctl.blockdev — the lsblk -P KEY="value" parser (F-080).

CLAUDE.md §6: lsblk MUST use -P (KEY="value" pairs) because positional parsing
breaks on models with spaces ("Samsung SSD 870 EVO 1TB"). Every enumerate test
mocks the parsed dicts, so this is the one regression net over the raw parse.
"""
import unittest
from unittest.mock import patch

from b2ctl import blockdev


class TestLsblkPairs(unittest.TestCase):
    """F-080: lsblk_pairs() parses raw KEY="value" lines robustly."""

    @patch("b2ctl.blockdev.run")
    def test_parses_model_with_spaces_and_empty_values(self, mock_run):
        # Verbatim `lsblk -P` output: MODEL has spaces, SERIAL is empty ("").
        mock_run.return_value = (
            'NAME="sda" MODEL="Samsung SSD 870 EVO 1TB" SERIAL="" TRAN="sas"\n'
            'NAME="nvme0n1" MODEL="Samsung SSD 990 EVO Plus 4TB" '
            'SERIAL="S7XXNS0W123" TRAN="nvme"\n'
        )
        rows = blockdev.lsblk_pairs("NAME,MODEL,SERIAL,TRAN")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["NAME"], "sda")
        self.assertEqual(rows[0]["MODEL"], "Samsung SSD 870 EVO 1TB")
        self.assertEqual(rows[0]["SERIAL"], "")           # empty value preserved
        self.assertEqual(rows[0]["TRAN"], "sas")
        self.assertEqual(rows[1]["MODEL"], "Samsung SSD 990 EVO Plus 4TB")
        self.assertEqual(rows[1]["SERIAL"], "S7XXNS0W123")

    @patch("b2ctl.blockdev.run")
    def test_blank_lines_skipped(self, mock_run):
        mock_run.return_value = 'NAME="sda" MODEL="X"\n\n   \n'
        rows = blockdev.lsblk_pairs("NAME,MODEL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"NAME": "sda", "MODEL": "X"})

    @patch("b2ctl.blockdev.run")
    def test_cmd_uses_dash_P_pairs_format(self, mock_run):
        # Dropping -P would reintroduce the CLAUDE.md §6 spaces-in-MODEL bug.
        mock_run.return_value = ""
        blockdev.lsblk_pairs("NAME,MODEL,SERIAL")
        argv = mock_run.call_args[0][0]
        self.assertIn("-P", argv)
        self.assertIn("NAME,MODEL,SERIAL", argv)          # cols threaded through -o


class TestVdUsage(unittest.TestCase):
    """blockdev.vd_usage() — mounted-fs usage of a (PERC virtual) block device."""

    @patch("b2ctl.blockdev.run")
    def test_returns_largest_mounted_fs(self, mock_run):
        # The unmounted parent + a small /boot + the large root fs: root wins.
        mock_run.return_value = (
            'NAME="sda" FSUSED="" FSSIZE="" MOUNTPOINT=""\n'
            'NAME="sda1" FSUSED="536870912" FSSIZE="1073741824" MOUNTPOINT="/boot"\n'
            'NAME="sda2" FSUSED="107374182400" FSSIZE="214748364800" MOUNTPOINT="/"\n'
        )
        self.assertEqual(blockdev.vd_usage("/dev/sda"),
                         (107374182400, 214748364800))

    @patch("b2ctl.blockdev.run")
    def test_returns_none_when_nothing_mounted(self, mock_run):
        mock_run.return_value = 'NAME="sda" FSUSED="" FSSIZE="" MOUNTPOINT=""\n'
        self.assertIsNone(blockdev.vd_usage("/dev/sda"))

    @patch("b2ctl.blockdev.run")
    def test_non_numeric_fssize_skipped(self, mock_run):
        mock_run.return_value = (
            'NAME="sda1" FSUSED="x" FSSIZE="bogus" MOUNTPOINT="/"\n'
        )
        self.assertIsNone(blockdev.vd_usage("/dev/sda"))

    @patch("b2ctl.blockdev.run")
    def test_cmd_uses_dash_P(self, mock_run):
        mock_run.return_value = ""
        blockdev.vd_usage("/dev/sda")
        argv = mock_run.call_args[0][0]
        self.assertIn("-P", argv)
        self.assertIn("/dev/sda", argv)


if __name__ == "__main__":
    unittest.main()
