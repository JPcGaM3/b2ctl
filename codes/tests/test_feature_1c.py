import unittest
from unittest.mock import patch, MagicMock
from b2ctl import zfs

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

from b2ctl import hba, smart
from b2ctl.common import Disk

class TestHbaSmart(unittest.TestCase):

    @patch('b2ctl.hba.run')
    def test_sas2ircu_display(self, mock_run):
        mock_run.return_value = """LSI Corporation SAS2 IR Configuration Utility.
Version 20.00.00.00 (2014.09.18)
Copyright (c) 2008-2014 LSI Corporation. All rights reserved.

Read configuration has been initiated for controller 0
------------------------------------------------------------------------
Controller information
------------------------------------------------------------------------
  Controller type                         : SAS2308_2
  BIOS version                            : 07.39.02.00
  Firmware version                        : 20.00.07.00
  Channel description                     : 1 Serial Attached SCSI
  Initiator ID                            : 0
  Maximum physical devices                : 1023
  Concurrent commands supported           : 10240
  Slot                                    : 1
  Segment                                 : 0
  Bus                                     : 2
  Device                                  : 0
  Function                                : 0
  RAID Support                            : No
------------------------------------------------------------------------
IR Volume information
------------------------------------------------------------------------
------------------------------------------------------------------------
Physical device information
------------------------------------------------------------------------
Initiator at ID #0

Device is a Hard disk
  Enclosure #                             : 1
  Slot #                                  : 0
  SAS Address                             : 4433221-1-0000-0000
  State                                   : Ready (RDY)
  Size (in MB)/(in sectors)               : 953869/1953525167
  Manufacturer                            : ATA
  Model Number                            : Samsung SSD 870
  Firmware Revision                       : 2B6Q
  Serial No                               : S74ZNS0W582280E
  GUID                                    : 5002538e404b9d0b
  Protocol                                : SATA
  Drive Type                              : SATA_SSD
"""
        mapping = hba.bay_map(0)
        self.assertIn("S74ZNS0W582280E", mapping)
        self.assertEqual(mapping["S74ZNS0W582280E"], "1:0")

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

if __name__ == '__main__':
    unittest.main()
