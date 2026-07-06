"""Unit tests for b2ctl.ui — table/pool/detail rendering + format helpers."""
from __future__ import annotations

from helpers import _disk
from b2ctl import ui
from b2ctl.common import assess


class TestUI:
    """Tests for table/pool rendering and utility functions."""

    def test_disk_label_format(self):
        d = _disk(bay="1:4", model="Samsung SSD 870", serial="S74ZNS0W000001")
        label = ui.disk_label(d)
        assert "(1:4)" in label
        assert "Samsung SSD 870" in label
        assert "S74ZNS0W000001" in label

    def test_disk_label_missing_bay(self):
        d = _disk(bay=None)
        label = ui.disk_label(d)
        assert "(?)" in label

    def test_fmt_poh_with_value(self):
        result = ui.fmt_poh(18000)
        assert "18000h" in result
        assert "y)" in result

    def test_fmt_poh_none(self):
        assert ui.fmt_poh(None) == "N/A"

    def test_human_size_bytes(self):
        assert ui.human_size(1024) == "1.0K"

    def test_human_size_gb(self):
        result = ui.human_size(1024 ** 3)
        assert "G" in result

    def test_human_size_tb(self):
        result = ui.human_size(1024 ** 4)
        assert "T" in result

    def test_human_size_none(self):
        assert ui.human_size(None) == "-"

    def test_render_table_has_all_columns(self):
        d = _disk(bay="1:0")
        table = ui.render_table([d])
        for col in ["BAY", "DEV", "IF", "MODEL", "SERIAL", "POWER_ON",
                     "WEAR(used)", "END(left)", "WRITTEN", "BAD", "HEALTH",
                     "POOL", "LEVEL"]:
            assert col in table

    def test_render_table_shows_disk_data(self):
        d = _disk(bay="1:4", serial="TESTSERIAL")
        table = ui.render_table([d])
        assert "1:4" in table
        assert "TESTSERIAL" in table

    def test_render_pools_online(self):
        pools = [{"name": "tank", "size": "2.72T", "alloc": "1.72G",
                  "free": "2.72T", "health": "ONLINE", "frag": "0%", "cap": "0%"}]
        result = ui.render_pools(pools)
        assert "tank" in result
        assert "ONLINE" in result

    def test_render_pools_degraded_shows_warning(self):
        pools = [{"name": "tank", "size": "2.72T", "alloc": "1.72G",
                  "free": "2.72T", "health": "DEGRADED", "frag": "0%", "cap": "0%"}]
        result = ui.render_pools(pools)
        assert "not ONLINE" in result

    def test_render_details_all_ok(self):
        d = _disk(level="NORMAL")
        assess(d)
        result = ui.render_details([d])
        assert "all disks healthy" in result

    def test_render_details_with_config_and_critical(self):
        d1 = _disk(pool=None, vdev=None, vdev_state=None)
        assess(d1)
        d2 = _disk(health="FAILED", dev="/dev/sdb", serial="DEAD1")
        assess(d2)
        result = ui.render_details([d1, d2])
        assert "config" in result.lower() or "CONFIG" in result
        assert "attention" in result.lower()

    def test_render_details_warns_when_pool_not_online(self):
        # disks all NORMAL but pool DEGRADED (e.g. a member was pulled) — the
        # summary must NOT claim everything is healthy.
        d = _disk(level="NORMAL")
        assess(d)
        pools = [{"name": "tank", "size": "2.72T", "alloc": "1.71G",
                  "free": "2.72T", "health": "DEGRADED", "frag": "0%", "cap": "0%"}]
        result = ui.render_details([d], pools)
        assert "all disks healthy" not in result
        assert "not ONLINE" in result
        assert "pools needing attention" in result

    def test_render_details_ok_when_pools_online(self):
        d = _disk(level="NORMAL")
        assess(d)
        pools = [{"name": "tank", "size": "2.72T", "alloc": "1.71G",
                  "free": "2.72T", "health": "ONLINE", "frag": "0%", "cap": "0%"}]
        result = ui.render_details([d], pools)
        assert "all disks healthy" in result


class TestArrayColumn:
    def test_pool_cell_sw_prefix(self):
        from b2ctl import ui
        d = _disk()
        d.pool = "tank"; d.vdev = "raidz1-0"
        assert "SW:tank/raidz1-0" in ui.render_table([d])

    def test_pool_cell_hw_prefix(self):
        from b2ctl import ui
        d = _disk()
        d.pool = None; d.array_type = "HW"; d.array_name = "vd0/raid1"
        assert "HW:vd0/raid1" in ui.render_table([d])

    def test_nvme_bay_renders_pcie_label(self):
        from b2ctl import ui
        d = _disk(dev="/dev/nvme0n1")
        d.bay = "PCIe2:0"
        out = ui.render_table([d])
        assert "PCIe2:0" in out and "nvme0n1" in out

    def test_render_table_groups_hw_above_sw(self):
        hw = _disk(dev="/dev/sdb", serial="HW1", pool=None, vdev=None)
        hw.array_type = "HW"; hw.array_name = "vd0/raid1"
        sw = _disk(dev="/dev/sda", serial="SW1", pool="tank", vdev="mirror-0")
        out = ui.render_table([hw, sw])
        assert "Hardware (PERC RAID)" in out
        assert "Software (ZFS / unassigned)" in out
        # hardware sub-header appears before software sub-header
        assert out.index("Hardware (PERC RAID)") < out.index("Software (ZFS")

    def test_render_table_flat_when_software_only(self):
        sw = _disk(dev="/dev/sda", pool="tank", vdev="mirror-0")
        out = ui.render_table([sw])
        assert "Hardware (PERC RAID)" not in out

    def test_render_storage_hw_above_sw(self):
        rows = [
            {"kind": "HW", "name": "MainSSD", "level": "raid1", "state": "Optl",
             "size": "640.0 GB", "used": "12.0G", "free": "628.0G"},
            {"kind": "SW", "name": "tank", "level": "mirror", "state": "ONLINE",
             "size": "928G", "used": "598M", "free": "927G"},
        ]
        out = ui.render_storage(rows)
        assert "Storage summary" in out
        assert "MainSSD" in out and "tank" in out
        assert out.index("MainSSD") < out.index("tank")

    def test_render_storage_empty(self):
        assert ui.render_storage([]) == ""


class TestBurninProgress:
    def test_fmt_eta_minutes(self):
        assert ui.fmt_eta(8) == "~8m"
        assert ui.fmt_eta(90) == "~1h30m"
        assert ui.fmt_eta(0) == "~0m"
        assert ui.fmt_eta(None) == ""

    def test_status_cell_shows_test_pct_for_free_disk(self):
        d = _disk(pool=None, vdev=None, vdev_state=None)
        d.selftest_running = True
        d.selftest_pct = 62
        assert "TEST 62%" in ui._status_cell(d)

    def test_status_cell_blank_when_not_testing(self):
        d = _disk(pool=None, vdev=None, vdev_state=None)
        assert ui._status_cell(d).strip() == ""

    def test_in_pool_disk_shows_state_not_test(self):
        # a pool member must render ONLINE, never TEST% (burn-in refuses members)
        d = _disk(pool="tank", vdev="raidz1-0", vdev_state="ONLINE")
        d.selftest_running = True
        d.selftest_pct = 50
        assert "ONLINE" in ui._status_cell(d)
        assert "TEST" not in ui._status_cell(d)

    def test_render_details_adds_selftest_line(self):
        d = _disk(pool=None, vdev=None, vdev_state=None)
        assess(d)                                # -> CONFIG (unassigned)
        d.selftest_running = True
        d.selftest_pct = 45
        d.selftest_eta = "~40m"
        out = ui.render_details([d])
        assert "self-test running: 45%" in out
        assert "~40m remaining" in out

    def test_render_burnin_view_two_bars(self):
        row = {"bay": "1:4", "dev": "/dev/sdb", "serial": "S1",
               "st_running": True, "st_pct": 60, "st_eta": 36, "do_scan": True,
               "sc_running": True, "sc_pct": 18, "sc_eta": 270, "sc_bad": 0,
               "done": False}
        out = ui.render_burnin_view([row])
        assert "SELF-TEST" in out and "SURFACE SCAN" in out
        assert "60%" in out and "18%" in out
        assert "~36m" in out and "~4h30m" in out

    def test_render_burnin_row_scan_na_when_no_scan(self):
        row = {"bay": "1:4", "dev": "/dev/sdb", "serial": "S1",
               "st_running": True, "st_pct": 10, "st_eta": None, "do_scan": False,
               "sc_running": False, "sc_pct": None, "sc_eta": None, "sc_bad": 0,
               "done": False}
        assert "n/a" in ui.render_burnin_row(row)

    def test_render_burnin_row_done_with_bad(self):
        row = {"bay": "1:4", "dev": "/dev/sdb", "serial": "S1",
               "st_running": False, "st_pct": 100, "st_eta": None, "do_scan": True,
               "sc_running": False, "sc_pct": 100, "sc_eta": None, "sc_bad": 3,
               "done": True}
        assert "done (3 bad)" in ui.render_burnin_row(row)
