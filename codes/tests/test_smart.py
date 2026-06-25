"""Unit tests for b2ctl.smart — ATA/SAS parsing + endurance calculation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from helpers import _disk, _ATA_OUTPUT, _SAS_OUTPUT
from b2ctl import smart
from b2ctl.common import Disk


class TestSmartParsing:
    """Tests for ATA and SAS SMART parsing."""

    def test_parse_ata_extracts_all_fields(self):
        d = Disk(dev="/dev/sda")
        smart._parse_ata(d, _ATA_OUTPUT)
        assert d.model == "Samsung SSD 870 EVO 1TB"
        assert d.serial == "S74ZNS0W582303N"
        assert d.poh == 18238
        assert d.wear_val == 99
        assert d.realloc == 0
        assert d.pending == 0
        assert d.lba_written == 19305985024

    def test_parse_sas_extracts_all_fields(self):
        d = Disk(dev="/dev/sda")
        smart._parse_sas(d, _SAS_OUTPUT)
        assert "SAMSUNG" in d.model
        assert d.serial == "S4F2NY0M105699"
        assert d.poh == 50451
        assert d.wear_val == 99  # 100 - 1%
        assert d.realloc == 0

    def test_endurance_calculation(self):
        d = _disk(lba_written=19305985024, is_ssd=True, model="Samsung SSD 870")
        tbw_table = {"samsung ssd 870": 600}
        smart._endurance(d, tbw_table)
        assert d.written_tb is not None
        assert d.written_tb == pytest.approx(19305985024 * 512 / 1e12, abs=0.01)
        assert d.tbw_rating == 600
        assert d.end_left is not None
        assert 0 <= d.end_left <= 100

    def test_endurance_hdd_no_wear(self):
        d = _disk(is_ssd=False, wear_val=50)
        smart._endurance(d, {})
        assert d.wear_val is None  # HDD: wear is meaningless


class TestSmartRead(unittest.TestCase):
    """Full smart.read() path against a realistic ATA smartctl dump."""

    @patch('b2ctl.smart.run')
    def test_smartctl_ata(self, mock_run):
        mock_run.return_value = """smartctl 7.2 2020-12-30 r5155 [x86_64-linux-5.15.0-76-generic] (local build)
Copyright (C) 2002-20, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Model Family:     Samsung based SSDs
Device Model:     Samsung SSD 870 EVO 1TB
Serial Number:    S74ZNS0W582280E
LU WWN Device Id: 5 002538 e404b9d0b
Firmware Version: SVT02B6Q
User Capacity:    1,000,204,886,016 bytes [1.00 TB]
Sector Size:      512 bytes logical/physical
Rotation Rate:    Solid State Device
Form Factor:      2.5 inches
TRIM Command:     Available, deterministic, zeroed
Device is:        In smartctl database [for details use: -P show]
ATA Version is:   ACS-4 T13/3232-D revision 5
SATA Version is:  SATA 3.3, 6.0 Gb/s (current: 6.0 Gb/s)
Local Time is:    Wed Jun 10 12:00:00 2026 UTC
SMART support is: Available - device has SMART capability.
SMART support is: Enabled

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 1
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       1234
 12 Power_Cycle_Count       0x0032   099   099   000    Old_age   Always       -       56
177 Wear_Leveling_Count     0x0013   095   095   000    Pre-fail  Always       -       45
179 Used_Rsvd_Blk_Cnt_Tot   0x0013   100   100   010    Pre-fail  Always       -       0
181 Program_Fail_Cnt_Total  0x0032   100   100   010    Old_age   Always       -       0
182 Erase_Fail_Count_Total  0x0032   100   100   010    Old_age   Always       -       0
183 Runtime_Bad_Block       0x0013   100   100   010    Pre-fail  Always       -       0
187 Uncorrectable_Error_Cnt 0x0032   100   100   000    Old_age   Always       -       0
190 Airflow_Temperature_Cel 0x0032   065   054   000    Old_age   Always       -       35
195 ECC_Error_Rate          0x001a   200   200   000    Old_age   Always       -       0
199 CRC_Error_Count         0x003e   100   100   000    Old_age   Always       -       0
235 POR_Recovery_Count      0x0012   099   099   000    Old_age   Always       -       32
241 Total_LBAs_Written      0x0032   099   099   000    Old_age   Always       -       123456789
"""
        d = Disk(dev="/dev/sda")
        smart.read(d, {})
        self.assertTrue(d.readable)
        self.assertTrue(d.is_ssd)
        self.assertEqual(d.health, "PASSED")
        self.assertEqual(d.model, "Samsung SSD 870 EVO 1TB")
        self.assertEqual(d.serial, "S74ZNS0W582280E")
        self.assertEqual(d.wear_val, 95)
        self.assertEqual(d.poh, 1234)
        self.assertEqual(d.lba_written, 123456789)
        self.assertEqual(d.realloc, 0)
        self.assertEqual(d.uncorr, 0)


if __name__ == "__main__":
    unittest.main()
