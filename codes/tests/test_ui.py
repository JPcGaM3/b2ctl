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

    def test_render_raid_volumes(self):
        from b2ctl import ui
        out = ui.render_raid_volumes([
            {"vd": "0", "raid": "RAID1", "state": "Optl",
             "size": "640.0 GB", "name": "MainSSD", "members": 2}])
        assert "vd0" in out and "RAID1" in out and "members=2" in out

    def test_render_raid_volumes_empty(self):
        from b2ctl import ui
        assert ui.render_raid_volumes([]) == ""
