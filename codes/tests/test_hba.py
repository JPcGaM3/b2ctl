"""Unit tests for b2ctl.hba — bay remapping, sas2ircu DISPLAY, bm reuse."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from b2ctl import hba
from b2ctl.common import Disk


class TestHbaNvmePcie:
    """NVMe bay = PCIe BDF from sysfs (remap logic now lives in baymap)."""

    def test_nvme_pcie_parses_address(self):
        from unittest.mock import patch, mock_open
        with patch("builtins.open", mock_open(read_data="0000:d8:00.0\n")):
            assert hba._nvme_pcie("nvme0n1") == "d8:00.0"

    def test_nvme_pcie_non_nvme(self):
        assert hba._nvme_pcie("sda") is None


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

    @patch('b2ctl.hba.run')
    def test_bay_map_ignores_enclosure_services_device(self, mock_run):
        # F-036: an SES section carries Enclosure/Slot/Serial too — its serial
        # must NOT enter the map (else a permanent phantom GHOST row).
        mock_run.return_value = """Physical device information
Device is a Hard disk
  Enclosure #                             : 1
  Slot #                                  : 0
  Serial No                               : DISKSERIAL01
  Drive Type                              : SATA_SSD

Device is a Enclosure services device
  Enclosure #                             : 1
  Slot #                                  : 8
  Serial No                               : 7A00FG2
  Protocol                                : SAS
"""
        mapping = hba.bay_map(0)
        self.assertIn("DISKSERIAL01", mapping)
        self.assertNotIn("7A00FG2", mapping)      # backplane SEP serial excluded

    @patch('b2ctl.hba.run')
    def test_ses_serial_produces_no_ghost(self, mock_run):
        mock_run.return_value = """Device is a Enclosure services device
  Enclosure #                             : 1
  Slot #                                  : 8
  Serial No                               : 7A00FG2
"""
        bm = hba.bay_map(0)
        ghosts = hba.get_ghost_disks([Disk(dev="/dev/sda", serial="REAL")], bm=bm)
        self.assertEqual(ghosts, [])


class TestHbaBmReuse:
    """attach_bays / get_ghost_disks reuse a provided bay map (no re-query)."""

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.baymap.load", return_value=[])
    def test_attach_bays_reuses_provided_bm(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d], bm={"SN001": "1:0"})
        mock_bm.assert_not_called()
        assert d.bay == "1:0"

    @patch("b2ctl.hba.bay_map", return_value={})
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.baymap.load", return_value=[])
    def test_attach_bays_default_calls_bay_map(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d])
        mock_bm.assert_called_once()

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.baymap.load", return_value=[])
    def test_get_ghost_disks_reuses_provided_bm(self, _load, _has, mock_bm):
        d = Disk(dev="/dev/sda", serial="SN001")
        ghosts = hba.get_ghost_disks([d], bm={"SN999": "1:7"})
        mock_bm.assert_not_called()
        assert len(ghosts) == 1
        assert ghosts[0].serial == "SN999"

    @patch("b2ctl.hba.run")
    @patch("b2ctl.baymap.load", return_value=[])
    def test_attach_bays_with_bm_spawns_no_sas2ircu(self, _load, mock_run):
        # F-037: a provided bm proves the tool works — do not probe `sas2ircu list`
        d = Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d], bm={"SN001": "2:3"})
        mock_run.assert_not_called()
        assert d.bay == "2:3"


class TestHaveSas2ircuMemo:
    """F-037: have_sas2ircu() is probed once per process, then cached."""

    def test_probe_memoized(self):
        hba._reset_have_cache()
        table = "  0  SAS2308_2     ...\n"
        with patch("b2ctl.hba.run", return_value=table) as mock_run, \
             patch("b2ctl.config.tool", return_value="sas2ircu"):
            assert hba.have_sas2ircu() is True
            assert hba.have_sas2ircu() is True
        assert mock_run.call_count == 1        # second call hit the memo
        hba._reset_have_cache()


class TestVdUsage(unittest.TestCase):
    """vd_usage reads lsblk FS columns of a VD block device's mounted FS."""

    def test_picks_largest_mounted_fs(self):
        out = (
            'NAME="sdb" FSUSED="" FSSIZE="" MOUNTPOINT=""\n'
            'NAME="sdb1" FSUSED="1048576" FSSIZE="104857600" MOUNTPOINT="/boot"\n'
            'NAME="sdb2" FSUSED="12884901888" FSSIZE="687194767360" MOUNTPOINT="/mnt/data"\n')
        with patch("b2ctl.blockdev.run", return_value=out):     # impl now in blockdev (F-099)
            self.assertEqual(hba.vd_usage("/dev/sdb"), (12884901888, 687194767360))

    def test_none_when_nothing_mounted(self):
        out = ('NAME="sdb" FSUSED="" FSSIZE="" MOUNTPOINT=""\n'
               'NAME="sdb1" FSUSED="" FSSIZE="" MOUNTPOINT=""\n')
        with patch("b2ctl.blockdev.run", return_value=out):
            self.assertIsNone(hba.vd_usage("/dev/sdb"))


class TestByIdIndexNvmePreference(unittest.TestCase):
    """NVMe model link (nvme-<model>_<serial>) preferred over nvme-eui.<hex>."""

    @patch("b2ctl.hba.os.path.realpath")
    @patch("b2ctl.hba.os.listdir")
    @patch("b2ctl.hba.os.path.isdir", return_value=True)
    def test_prefers_model_link_over_eui(self, _isdir, mock_ls, mock_real):
        mock_ls.return_value = ["nvme-eui.0025385991b1c0f4",
                                "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345"]
        mock_real.side_effect = lambda p: "/dev/nvme0n1"   # both point at same dev
        idx = hba._by_id_index()
        self.assertTrue(idx["/dev/nvme0n1"].endswith(
            "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345"))

    @patch("b2ctl.hba.os.path.realpath")
    @patch("b2ctl.hba.os.listdir")
    @patch("b2ctl.hba.os.path.isdir", return_value=True)
    def test_by_id_index_prefers_friendly_nvme_over_uuid_and_suffixed(
            self, _isdir, mock_ls, mock_real):
        # F-081: the un-suffixed friendly nvme-<model>_<serial> link must beat
        # nvme-uuid.*, nvme-eui.* AND the systemd >=256 namespace-suffixed
        # nvme-..._1 duplicate — deterministically, whatever os.listdir order.
        import itertools
        friendly = "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345"
        links = [
            "nvme-uuid.86f1e3aa-0000-0000-0000-000000000000",
            friendly + "_1",                       # namespace-suffixed duplicate
            friendly,                              # the friendly winner
            "nvme-eui.0025385991b1c0f4",
        ]
        mock_real.side_effect = lambda p: "/dev/nvme0n1"   # all point at one dev
        for order in itertools.permutations(links):
            mock_ls.return_value = list(order)
            idx = hba._by_id_index()
            self.assertTrue(
                idx["/dev/nvme0n1"].endswith(friendly),
                f"friendly link lost for listdir order {order}")


class TestEnumerateNvmeByIdBay(unittest.TestCase):
    """NVMe bay set from a by-id map entry even when there is no PCIe BDF."""

    @patch("b2ctl.hba._nvme_pcie", return_value=None)
    @patch("b2ctl.baymap.load")
    @patch("b2ctl.hba._by_id_index")
    @patch("b2ctl.hba._lsblk_pairs")
    def test_nvme_bay_from_by_id(self, mock_lsblk, mock_byid, mock_load, _pcie):
        mock_lsblk.return_value = [{
            "NAME": "nvme0n1", "TYPE": "disk", "SIZE": "0",
            "SERIAL": "S7XX12345", "MODEL": "Samsung SSD 990 EVO Plus 4TB",
            "TRAN": "nvme", "ROTA": "0"}]
        link = "/dev/disk/by-id/nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345"
        mock_byid.return_value = {"/dev/nvme0n1": link}
        mock_load.return_value = [{"panel": "back", "type": "nvme",
            "map": [{"by-id": "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345",
                     "bay": "PCIe2:0"}]}]
        with patch("b2ctl.hba.os.path.realpath", return_value="/dev/nvme0n1"):
            disks = hba.enumerate_disks()
        self.assertEqual(disks[0].bay, "PCIe2:0")
