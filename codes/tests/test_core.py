"""Unit tests for b2ctl.core — scan() pipeline (bay map, smart, ghost rescue)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from b2ctl import core as _core_mod
from b2ctl.common import Disk


class TestScanPipeline:
    """Verify the scan() pipeline performance + correctness fixes."""

    # Fix 1 — bay_map() queried only once per scan
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_calls_bay_map_only_once(self, mock_spec, mock_backend_mod, mock_smart, mock_zfs):
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = []
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.return_value = []
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        _core_mod.scan()
        assert mock_bk.bay_map.call_count == 1

    # Fix 2 — smartctl read for all non-ghost disks
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_smart_called_for_all_non_ghost_disks(self, mock_spec, mock_backend_mod,
                                                         mock_smart, mock_zfs):
        real1 = Disk(dev="/dev/sda", serial="R1")
        real2 = Disk(dev="/dev/sdb", serial="R2")
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = [real1, real2]
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.return_value = []
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        _core_mod.scan()
        assert mock_smart.read.call_count == 2

    # Fix 3 — parallel ghost rescue
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_rescues_all_ghost_serials(self, mock_spec, mock_backend_mod,
                                             mock_smart, mock_zfs):
        ghost1 = Disk(dev="-", serial="G1", health="GHOST")
        ghost2 = Disk(dev="-", serial="G2", health="GHOST")
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = []
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.side_effect = [[ghost1, ghost2], []]
        mock_bk.udev_rescue_ghost.return_value = True
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        _core_mod.scan()
        assert mock_bk.udev_rescue_ghost.call_count == 2

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_survivors_after_partial_rescue(self, mock_spec, mock_backend_mod,
                                                  mock_smart, mock_zfs):
        ghost1 = Disk(dev="-", serial="G1", health="GHOST")
        ghost2 = Disk(dev="-", serial="G2", health="GHOST")
        ghost2_new = Disk(dev="-", serial="G2", health="GHOST")
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = []
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.side_effect = [[ghost1, ghost2], [ghost2_new]]
        mock_bk.udev_rescue_ghost.side_effect = lambda s: s == "G1"
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        result = _core_mod.scan()
        ghosts = [d for d in result if d.health == "GHOST"]
        assert len(ghosts) == 1
        assert ghosts[0].serial == "G2"
        assert "OS_REJECTED" in ghosts[0].reasons


class TestAssembleStorage:
    """assemble_storage unifies HW volumes (top) + SW pools (bottom)."""

    def test_hw_above_sw_with_usage_and_names(self):
        from b2ctl.common import Disk
        hw = Disk(dev="/dev/sdb"); hw.array_type = "HW"; hw.array_name = "vd0/raid1"
        vols = [{"vd": "0", "raid": "RAID1", "state": "Optl",
                 "size": "640.0 GB", "name": "MainSSD", "members": 2}]
        pools = [{"name": "tank", "size": "928G", "alloc": "598M",
                  "free": "927G", "health": "ONLINE"}]
        with patch("b2ctl.hba.vd_usage", return_value=(12_884_901_888, 687_194_767_360)), \
             patch("b2ctl.zfs.pool_level", return_value="mirror"):
            rows = _core_mod.assemble_storage([hw], pools, vols)
        assert [r["kind"] for r in rows] == ["HW", "SW"]
        hw_row, sw_row = rows
        assert hw_row["name"] == "MainSSD"
        assert hw_row["level"] == "raid1"
        assert hw_row["used"] != "-" and hw_row["free"] != "-"
        assert sw_row["name"] == "tank" and sw_row["level"] == "mirror"
        assert sw_row["used"] == "598M"

    def test_hw_usage_dash_when_unmounted(self):
        from b2ctl.common import Disk
        hw = Disk(dev="/dev/sdb"); hw.array_type = "HW"; hw.array_name = "vd0/raid1"
        vols = [{"vd": "0", "raid": "RAID1", "state": "Optl", "size": "640.0 GB",
                 "name": "MainSSD"}]
        with patch("b2ctl.hba.vd_usage", return_value=None):
            rows = _core_mod.assemble_storage([hw], [], vols)
        assert rows[0]["used"] == "-" and rows[0]["free"] == "-"
