"""Unit tests for b2ctl.hba — bay remapping, sas2ircu DISPLAY, bm reuse."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from b2ctl import hba
from b2ctl.common import Disk


class TestHbaBayMapping:
    """Tests for bay remapping logic."""

    def test_remap_with_explicit_map(self):
        cfg = {"map": {"1:0": "1:7", "1:7": "1:0"}}
        assert hba._remap("1:0", cfg) == "1:7"
        assert hba._remap("1:7", cfg) == "1:0"

    def test_remap_with_reverse_slots(self):
        cfg = {"reverse_slots": True, "slots_per_enclosure": 8}
        assert hba._remap("1:0", cfg) == "1:7"
        assert hba._remap("1:3", cfg) == "1:4"
        assert hba._remap("1:7", cfg) == "1:0"

    def test_remap_no_config_identity(self):
        assert hba._remap("1:4", {}) == "1:4"
        assert hba._remap("1:0", {}) == "1:0"


class TestHbaBayMapDisplay(unittest.TestCase):
    """sas2ircu DISPLAY → serial:bay mapping."""

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


class TestHbaBmReuse:
    """attach_bays / get_ghost_disks reuse a provided bay map (no re-query)."""

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_attach_bays_reuses_provided_bm(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d], bm={"SN001": "1:0"})
        mock_bm.assert_not_called()
        assert d.bay == "1:0"

    @patch("b2ctl.hba.bay_map", return_value={})
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_attach_bays_default_calls_bay_map(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d])
        mock_bm.assert_called_once()

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_get_ghost_disks_reuses_provided_bm(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        ghosts = hba.get_ghost_disks([d], bm={"SN999": "1:7"})
        mock_bm.assert_not_called()
        assert len(ghosts) == 1
        assert ghosts[0].serial == "SN999"
