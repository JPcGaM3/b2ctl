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


class TestGhostSerialDomainGuard(unittest.TestCase):
    """F-133: enterprise SAS drives expose no lsblk SERIAL until SMART runs, so
    a bay map keyed on real serials matches nothing and EVERY entry would become
    a phantom GHOST row."""

    @patch("b2ctl.baymap.load", return_value=[])
    def test_all_entries_ghosting_against_serialless_disks_returns_none(self, _load):
        disks = [Disk(dev="/dev/sda"), Disk(dev="/dev/sdb")]      # no serials yet
        bm = {"WBM066HP": "9:0", "WBM06F90": "9:1"}
        self.assertEqual(hba.get_ghost_disks(disks, bm=bm), [])

    @patch("b2ctl.baymap.load", return_value=[])
    def test_surplus_ghosts_are_still_reported(self, _load):
        # F-133 review: the guard must not blank the whole list. Two serial-less
        # disks cannot explain THREE would-be ghosts, so the map is reporting at
        # least one genuinely OS-rejected drive — show them rather than hide it.
        disks = [Disk(dev="/dev/sda"), Disk(dev="/dev/sdb")]
        bm = {"WBM066HP": "9:0", "WBM06F90": "9:1", "WBM00W6L": "9:2"}
        self.assertEqual(len(hba.get_ghost_disks(disks, bm=bm)), 3)

    @patch("b2ctl.baymap.load", return_value=[])
    def test_real_ghost_still_reported_when_serials_are_known(self, _load):
        disks = [Disk(dev="/dev/sda", serial="WBM066HP")]
        bm = {"WBM066HP": "9:0", "WBM06F90": "9:1"}
        ghosts = hba.get_ghost_disks(disks, bm=bm)
        self.assertEqual([g.serial for g in ghosts], ["WBM06F90"])


class TestSysfsBayFallback(unittest.TestCase):
    """F-134: the kernel's SAS transport class maps device -> slot with no serial
    join. It fills what the vendor map could not, and never overrides it."""

    # The real field HBA330 map: bays 0-6 -> sda-sdg, 22/23 -> sdh/sdi.
    _SLOTS = {f"/dev/sd{n}": i for i, n in enumerate("abcdefg")}
    _SLOTS.update({"/dev/sdh": 22, "/dev/sdi": 23})

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba.have_sas2ircu", return_value=False)
    def test_fills_bays_when_vendor_map_is_empty(self, _has, _load):
        disks = [Disk(dev="/dev/sda"), Disk(dev="/dev/sdh")]
        with patch("b2ctl.blockdev.sas_bay_slots", return_value=self._SLOTS):
            hba.attach_bays(disks, bm={}, enc_hint=9)
        self.assertEqual([d.bay for d in disks], ["9:0", "9:22"])

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba.have_sas2ircu", return_value=False)
    def test_vendor_label_always_wins(self, _has, _load):
        d = Disk(dev="/dev/sda", serial="SN001")
        with patch("b2ctl.blockdev.sas_bay_slots", return_value=self._SLOTS):
            hba.attach_bays([d], bm={"SN001": "1:7"})
        self.assertEqual(d.bay, "1:7")          # sas2ircu/perccli label untouched

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba.have_sas2ircu", return_value=False)
    def test_enclosure_borrowed_from_the_vendor_map(self, _has, _load):
        # A partially-successful vendor map must not produce mixed prefixes:
        # sdb takes its enclosure from the entry sda already got.
        disks = [Disk(dev="/dev/sda", serial="SN001"), Disk(dev="/dev/sdb")]
        with patch("b2ctl.blockdev.sas_bay_slots", return_value=self._SLOTS):
            hba.attach_bays(disks, bm={"SN001": "9:0"})
        self.assertEqual([d.bay for d in disks], ["9:0", "9:1"])

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba.have_sas2ircu", return_value=False)
    def test_no_sas_transport_is_a_no_op(self, _has, _load):
        d = Disk(dev="/dev/nvme0n1")
        with patch("b2ctl.blockdev.sas_bay_slots", return_value={}):
            hba.attach_bays([d], bm={})
        self.assertIsNone(d.bay)


class TestSasBaySlots(unittest.TestCase):
    """blockdev.sas_bay_slots — sysfs parsing, SEP exclusion, constant-bay veto."""

    def _fake_sysfs(self, tmp, entries):
        """entries: {end_device_name: (bay_text, block_name_or_None)}"""
        import os
        for name, (bay, blk) in entries.items():
            base = os.path.join(tmp, name)
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "bay_identifier"), "w") as f:
                f.write(bay + "\n")
            if blk:
                os.makedirs(os.path.join(base, "device", "target1:0:0",
                                         "1:0:0:0", "block", blk), exist_ok=True)
        return tmp

    def test_parses_bays_and_drops_the_enclosure_processor(self):
        import tempfile
        from b2ctl import blockdev
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_sysfs(tmp, {
                "end_device-11:0:0": ("0", "sda"),
                "end_device-11:0:7": ("22", "sdh"),
                "end_device-11:0:8": ("24", None),      # SES processor, no block dev
            })
            with patch.object(blockdev, "SAS_DEVICE_DIR", tmp):
                slots = blockdev.sas_bay_slots()
        self.assertEqual(slots, {"/dev/sda": 0, "/dev/sdh": 22})

    def test_constant_bay_backplane_is_rejected(self):
        import tempfile
        from b2ctl import blockdev
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_sysfs(tmp, {
                "end_device-11:0:0": ("0", "sda"),
                "end_device-11:0:1": ("0", "sdb"),
            })
            with patch.object(blockdev, "SAS_DEVICE_DIR", tmp):
                self.assertEqual(blockdev.sas_bay_slots(), {})

    def test_missing_dir_is_empty(self):
        from b2ctl import blockdev
        with patch.object(blockdev, "SAS_DEVICE_DIR", "/nonexistent/sas"):
            self.assertEqual(blockdev.sas_bay_slots(), {})


class TestEnumerateWwn(unittest.TestCase):
    """F-133: WWN is a serial-independent join key between lsblk and perccli."""

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba._by_id_index", return_value={})
    @patch("b2ctl.hba._lsblk_pairs")
    def test_wwn_captured_from_lsblk(self, mock_lsblk, _byid, _load):
        mock_lsblk.return_value = [{
            "NAME": "sda", "TYPE": "disk", "SIZE": "0", "SERIAL": "",
            "MODEL": "DL2400MM0159", "TRAN": "sas", "ROTA": "1",
            "WWN": "0x5000c500a1b2c3d4"}]
        disks = hba.enumerate_disks()
        self.assertEqual(disks[0].wwn, "0x5000c500a1b2c3d4")

    @patch("b2ctl.baymap.load", return_value=[])
    @patch("b2ctl.hba._by_id_index", return_value={})
    @patch("b2ctl.hba._lsblk_pairs")
    def test_wwn_column_requested(self, mock_lsblk, _byid, _load):
        mock_lsblk.return_value = []
        hba.enumerate_disks()
        self.assertIn("WWN", mock_lsblk.call_args[0][0])


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

    def test_timeout_is_not_an_answer_and_is_not_latched(self):
        # F-143: run(none_on_timeout=True) returns None when the 32-bit sas2ircu
        # never replied. Memoizing that hid every bay and ghost disk for the rest
        # of a watch session after ONE slow probe under a resilver.
        hba._reset_have_cache()
        with patch("b2ctl.hba.run", return_value=None) as mock_run, \
             patch("b2ctl.config.tool", return_value="sas2ircu"):
            assert hba.have_sas2ircu() is False
            assert hba.have_sas2ircu() is False
            assert hba._HAVE_CACHE is None      # nothing latched
        assert mock_run.call_count == 2         # re-probed, did not trust the memo
        hba._reset_have_cache()

    def test_a_definite_no_is_still_latched(self):
        # An absent binary answers '' every time — that IS an answer, so the
        # F-037 memo must survive. Only "no reply" is treated as unknown.
        hba._reset_have_cache()
        with patch("b2ctl.hba.run", return_value="") as mock_run, \
             patch("b2ctl.config.tool", return_value="sas2ircu"):
            assert hba.have_sas2ircu() is False
            assert hba.have_sas2ircu() is False
        assert mock_run.call_count == 1
        hba._reset_have_cache()

    def test_timeout_then_success_answers_true(self):
        # The point of not latching: the next probe can still succeed.
        hba._reset_have_cache()
        with patch("b2ctl.hba.run", side_effect=[None, "  0  SAS2308_2  ...\n"]), \
             patch("b2ctl.config.tool", return_value="sas2ircu"):
            assert hba.have_sas2ircu() is False
            assert hba.have_sas2ircu() is True
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
