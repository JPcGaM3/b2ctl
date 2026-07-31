"""Unit tests for b2ctl.core — scan() pipeline (bay map, smart, ghost rescue)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from b2ctl import core as _core_mod
from b2ctl import zfs as _zfs_mod
from b2ctl.common import Disk


def _fixture_disks(n: int = 8) -> list[Disk]:
    """N non-ghost inventory disks (default health 'UNKNOWN' != 'GHOST')."""
    return [Disk(dev=f"/dev/sd{c}", serial=f"S{i}")
            for i, c in zip(range(n), "abcdefghijklmnop")]


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
        _core_mod.scan(rescue=True)
        assert mock_bk.udev_rescue_ghost.call_count == 2

    # F-005 — the read path (default scan) must NOT fire udevadm rescue
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_status_scan_never_calls_udev_rescue(self, mock_spec, mock_backend_mod,
                                                 mock_smart, mock_zfs):
        ghost = Disk(dev="-", serial="G1", health="GHOST")
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = []
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.return_value = [ghost]
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        result = _core_mod.scan()                         # default: rescue=False
        mock_bk.udev_rescue_ghost.assert_not_called()
        ghosts = [d for d in result if d.health == "GHOST"]
        assert len(ghosts) == 1
        assert any("rescue in watch" in r or "[u]dev" in r for r in ghosts[0].reasons)

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
        result = _core_mod.scan(rescue=True)
        ghosts = [d for d in result if d.health == "GHOST"]
        assert len(ghosts) == 1
        assert ghosts[0].serial == "G2"
        assert "OS_REJECTED" in ghosts[0].reasons


class TestAssembleStorage:
    """assemble_storage unifies HW volumes (top) + SW pools (bottom)."""

    def test_hw_above_sw_with_usage_and_names(self):
        from b2ctl.common import Disk
        hw = Disk(dev="-"); hw.ctrl_dev = "/dev/sdb"     # F-136: handle, not node
        hw.array_type = "HW"; hw.array_name = "vd0/raid1"
        vols = [{"vd": "0", "raid": "RAID1", "state": "Optl",
                 "size": "640.0 GB", "name": "MainSSD", "members": 2}]
        pools = [{"name": "tank", "size": "928G", "alloc": "598M",
                  "free": "927G", "health": "ONLINE"}]
        with patch("b2ctl.blockdev.vd_usage", return_value=(12_884_901_888, 687_194_767_360)), \
             patch("b2ctl.zfs.pool_level", return_value="mirror"):
            rows = _core_mod.assemble_storage([hw], pools, vols)
        assert [r["kind"] for r in rows] == ["HW", "SW"]
        hw_row, sw_row = rows
        assert hw_row["name"] == "MainSSD"
        assert hw_row["level"] == "raid1"
        assert hw_row["used"] != "-" and hw_row["free"] != "-"
        assert sw_row["name"] == "tank" and sw_row["level"] == "mirror"
        assert sw_row["used"] == "598M"

    def test_two_vds_measure_their_own_block_device(self):
        """F-136 field bug: every HW member reported the same `dev`, so both
        volumes resolved to one device and printed identical used/free."""
        from b2ctl.common import Disk
        a = Disk(dev="-"); a.ctrl_dev = "/dev/sdq"
        a.array_type = "HW"; a.array_name = "vd0/raid1"
        b = Disk(dev="-"); b.ctrl_dev = "/dev/sdr"
        b.array_type = "HW"; b.array_name = "vd1/raid10"
        vols = [{"vd": "0", "raid": "RAID1", "state": "Optl",
                 "size": "430.0 GB", "name": "MainSSD"},
                {"vd": "1", "raid": "RAID10", "state": "Optl",
                 "size": "6.399 TB", "name": "SAS-SSD"}]
        seen = []

        def _usage(dev):
            seen.append(dev)
            return (10 * 2**30, 100 * 2**30) if dev == "/dev/sdq" \
                else (500 * 2**30, 900 * 2**30)

        with patch("b2ctl.blockdev.vd_usage", side_effect=_usage):
            rows = _core_mod.assemble_storage([a, b], [], vols)
        assert seen == ["/dev/sdq", "/dev/sdr"]      # each volume, its own device
        assert rows[0]["used"] != rows[1]["used"]
        assert rows[0]["free"] != rows[1]["free"]

    def test_hw_usage_dash_when_unmounted(self):
        from b2ctl.common import Disk
        hw = Disk(dev="-"); hw.ctrl_dev = "/dev/sdb"     # F-136: handle, not node
        hw.array_type = "HW"; hw.array_name = "vd0/raid1"
        vols = [{"vd": "0", "raid": "RAID1", "state": "Optl", "size": "640.0 GB",
                 "name": "MainSSD"}]
        with patch("b2ctl.blockdev.vd_usage", return_value=None):
            rows = _core_mod.assemble_storage([hw], [], vols)
        assert rows[0]["used"] == "-" and rows[0]["free"] == "-"


class TestScanConcurrency:
    """SMART pools: direct/IT-mode targets read one-thread-per-disk (F-077,
    min(16, N)); megaraid passthrough targets (d.smart_dtype set) read at a small
    configurable cap so 16-way probes don't saturate the one PERC and time out."""

    @patch("b2ctl.core.ThreadPoolExecutor")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_smart_pool_sized_to_targets(self, mock_spec, mock_backend_mod,
                                         mock_smart, mock_zfs, mock_tpe):
        disks = _fixture_disks(8)
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = disks
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.return_value = []         # no ghost pool created
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        _core_mod.scan()
        # The only ThreadPoolExecutor built (no ghosts) is the SMART pool, sized
        # to the 8 non-ghost targets — not the old fixed max_workers=4.
        mock_tpe.assert_called_once_with(max_workers=8)

    @patch("b2ctl.core.ThreadPoolExecutor")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_no_smart_pool_when_no_targets(self, mock_spec, mock_backend_mod,
                                           mock_smart, mock_zfs, mock_tpe):
        # all-ghost / no-disk edge: each pool skips an empty group, so NO
        # ThreadPoolExecutor is built (and no max_workers=0 ValueError).
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
        mock_tpe.assert_not_called()

    @patch("b2ctl.config.smart_config", return_value={"timeout": 10, "megaraid_workers": 4})
    @patch("b2ctl.core.ThreadPoolExecutor")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_megaraid_targets_use_capped_pool_direct_stay_wide(
            self, mock_spec, mock_backend_mod, mock_smart, mock_zfs, mock_tpe, _sc):
        # 3 direct (no smart_dtype) + 5 megaraid (smart_dtype set): the direct pool
        # is sized to its group (min(16,3)=3); the megaraid pool is capped at the
        # configured megaraid_workers (min(4,5)=4), NOT 5.
        direct = _fixture_disks(3)
        mega = [Disk(dev="/dev/sda", serial=f"M{i}", smart_dtype=f"megaraid,{i}")
                for i in range(5)]
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = direct + mega
        mock_bk.attach_bays.return_value = None
        mock_bk.get_ghost_disks.return_value = []
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        _core_mod.scan()
        sizes = [c.kwargs["max_workers"] for c in mock_tpe.call_args_list]
        assert sizes == [3, 4]          # direct wide (3), megaraid capped (4)


class TestBaySort:
    """F-078: numeric-aware bay sort so '0:2' precedes '0:10' (not lexicographic);
    free-form / bay-less labels fall back to a stable string key."""

    def test_bay_sort_numeric(self):
        disks = [Disk(dev="/dev/sda", bay="0:1"),
                 Disk(dev="/dev/sdb", bay="0:10"),
                 Disk(dev="/dev/sdc", bay="0:2")]
        ordered = sorted(disks, key=_core_mod._bay_sort_key)
        assert [d.bay for d in ordered] == ["0:1", "0:2", "0:10"]

    def test_bay_sort_freeform_and_missing_are_stable(self):
        # a PCIe/free-form label and a bay-less disk must sort deterministically
        # without raising (no int() on a non-numeric slot).
        disks = [Disk(dev="/dev/sdb", bay=None),
                 Disk(dev="/dev/sda", bay="PCIe2:0")]
        ordered = sorted(disks, key=_core_mod._bay_sort_key)
        assert [d.dev for d in ordered] == ["/dev/sda", "/dev/sdb"]


class TestScanOne:
    """F-079: scan_one is targeted — it reads SMART for ONLY the hot-plugged dev,
    not the whole fleet, and falls back to a bare Disk when lsblk shows nothing."""

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_one_reads_smart_only_for_target(self, mock_spec, mock_backend_mod,
                                                  mock_smart, mock_zfs):
        disks = _fixture_disks(8)
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = disks
        mock_zfs.topology.return_value = {}
        mock_spec.load.return_value = {}
        result = _core_mod.scan_one("/dev/sdd", {})
        # exactly one SMART read (for the target), not 8
        assert mock_smart.read.call_count == 1
        assert mock_smart.read.call_args[0][0].dev == "/dev/sdd"
        assert result.dev == "/dev/sdd"

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_one_returns_bare_disk_when_not_found(self, mock_spec, mock_backend_mod,
                                                       mock_smart, mock_zfs):
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.enumerate_disks.return_value = [Disk(dev="/dev/sda", serial="S0")]
        result = _core_mod.scan_one("/dev/sdz", {})
        assert isinstance(result, Disk)
        assert result.dev == "/dev/sdz"
        mock_smart.read.assert_not_called()


class TestScanLight:
    """F-102: scan_light enumerates + attaches bays + ZFS membership but reads
    NO SMART (the locate / token-resolution paths never use wear/TBW/health)."""

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    def test_scan_light_reads_no_smart(self, mock_backend_mod, mock_smart, mock_zfs):
        disks = _fixture_disks(8)
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = disks
        mock_bk.get_ghost_disks.return_value = []
        mock_zfs.topology.return_value = {}
        result = _core_mod.scan_light()
        # zero smartctl fan-out — the whole point of F-102
        mock_smart.read.assert_not_called()
        # identity + membership pipeline still ran (bay attach + zpool topology)
        mock_bk.attach_bays.assert_called()
        mock_zfs.attach_membership.assert_called_once()
        assert len(result) == 8


class TestPoolMembershipUnknown:
    """F-143: an unanswered zpool marks every disk pool-UNKNOWN, never free.

    common.run() collapses 'binary missing', 'timed out' and 'returned nothing'
    into '', so list_pools() used to report a silent zpool as a machine with no
    pools. Every live member then read as unassigned and turned up in the
    free-disk menus of [a]ssign / [c]reate.
    """

    def _backend(self, mock_backend_mod, disks):
        mock_bk = MagicMock()
        mock_backend_mod.get_backend.return_value = mock_bk
        mock_bk.have_tool.return_value = True
        mock_bk.bay_map.return_value = {}
        mock_bk.enumerate_disks.return_value = disks
        mock_bk.get_ghost_disks.return_value = []
        mock_bk.attach_bays.return_value = None
        return mock_bk

    def _dead_zfs(self, mock_zfs):
        # The except clause needs the REAL exception class; only topology fails.
        mock_zfs.ZfsUnavailable = _zfs_mod.ZfsUnavailable
        mock_zfs.topology.side_effect = _zfs_mod.ZfsUnavailable("zpool: not found")
        return mock_zfs

    @patch("b2ctl.core.warn")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_marks_every_disk_pool_unknown(self, _mock_spec, mock_backend_mod,
                                                _mock_smart, mock_zfs, mock_warn):
        disks = _fixture_disks(4)
        self._backend(mock_backend_mod, disks)
        self._dead_zfs(mock_zfs)
        result = _core_mod.scan()
        assert result and all(not d.pool_known for d in result)
        # is_poolable is the single authority every menu filters on — with the
        # pool picture unknown it must answer False for all of them.
        assert not any(d.is_poolable for d in result)
        # exactly one warning, naming the cause; warn() is also the JSON-mode
        # collector, so this is what reaches an MCP client in warnings[]
        assert mock_warn.call_count == 1
        assert "zpool did not answer" in mock_warn.call_args.args[0]

    @patch("b2ctl.core.warn")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    def test_scan_light_marks_every_disk_pool_unknown(self, mock_backend_mod,
                                                      _mock_smart, mock_zfs, _mock_warn):
        # scan_light feeds locate / token resolution — it must refuse for the
        # same reason scan() does, or the cheap path becomes the unguarded one.
        disks = _fixture_disks(3)
        self._backend(mock_backend_mod, disks)
        self._dead_zfs(mock_zfs)
        result = _core_mod.scan_light()
        assert result and all(not d.pool_known for d in result)

    @patch("b2ctl.core.warn")
    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_a_live_zpool_leaves_pool_known_true(self, _mock_spec, mock_backend_mod,
                                                 _mock_smart, mock_zfs, _mock_warn):
        # The guard must not fire on the normal path: a disk that zpool says is
        # unassigned is still assignable.
        disks = _fixture_disks(2)
        self._backend(mock_backend_mod, disks)
        mock_zfs.ZfsUnavailable = _zfs_mod.ZfsUnavailable
        mock_zfs.topology.return_value = {}
        result = _core_mod.scan()
        assert all(d.pool_known for d in result)
        assert all(d.is_poolable for d in result)
