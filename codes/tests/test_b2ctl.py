"""Comprehensive unit tests for b2ctl (IT-mode).

Tests every module with mocked subprocess/OS calls:
  - common.py  : Disk dataclass, assess() health-level logic
  - zfs.py     : topology parsing, membership, pool actions
  - ui.py      : table/pool/detail rendering, human_size, disk_label
  - watch.py   : offload guard, swap re-add-as-spare, create validation, demote
  - spec.py    : SSD TBW model lookup
  - hba.py     : bay remapping
  - smart.py   : ATA/SAS/NVMe SMART parsing, endurance calculation

Run:  cd codes && python3 -m pytest tests/test_b2ctl.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import replace as dc_replace

from b2ctl.common import Disk, assess, RANK
from b2ctl import zfs, ui, spec
from b2ctl import hba
from b2ctl import smart


# ========================================================================== #
# Helpers / Fixtures
# ========================================================================== #

def _disk(**kw) -> Disk:
    """Shorthand to build a Disk with sensible defaults."""
    defaults = dict(dev="/dev/sda", by_id="/dev/disk/by-id/wwn-0x123",
                    model="Samsung SSD 870", serial="S74ZNS0W000001",
                    iface="SAS", readable=True, health="PASSED",
                    pool="tank", vdev="raidz1-0", vdev_state="ONLINE",
                    poh=18000, wear_val=99, realloc=0, end_left=98.0,
                    written_tb=10.0, tbw_rating=600.0)
    defaults.update(kw)
    return Disk(**defaults)


# ========================================================================== #
# TestDiskAssessment — common.assess()
# ========================================================================== #

class TestDiskAssessment:
    """Tests for the assess() health-level assignment logic."""

    def test_normal_healthy_disk(self):
        d = _disk()
        assess(d)
        assert d.level == "NORMAL"
        assert d.reasons == []

    def test_config_unassigned_disk(self):
        d = _disk(pool=None, vdev=None, vdev_state=None)
        assess(d)
        assert d.level == "CONFIG"
        assert any("unassigned" in r for r in d.reasons)

    def test_critical_smart_failed(self):
        d = _disk(health="FAILED")
        assess(d)
        assert d.level == "CRITICAL"
        assert any("FAILED" in r for r in d.reasons)

    def test_critical_bad_sectors(self):
        d = _disk(realloc=5)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("reallocated" in r or "defects" in r for r in d.reasons)

    def test_critical_pending_sectors(self):
        d = _disk(pending=3)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("pending" in r for r in d.reasons)

    def test_critical_uncorrectable(self):
        d = _disk(uncorr=1)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("uncorrectable" in r for r in d.reasons)

    def test_critical_low_endurance(self):
        d = _disk(end_left=5.0)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("endurance" in r for r in d.reasons)

    def test_warning_low_endurance(self):
        d = _disk(end_left=20.0)
        assess(d)
        assert d.level == "WARNING"
        assert any("endurance" in r for r in d.reasons)

    def test_critical_smart_unreadable(self):
        d = _disk(readable=False)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("unreadable" in r for r in d.reasons)

    def test_warning_degraded_vdev(self):
        d = _disk(vdev_state="DEGRADED")
        assess(d)
        assert d.level == "WARNING"
        assert any("DEGRADED" in r for r in d.reasons)

    def test_critical_faulted_vdev(self):
        d = _disk(vdev_state="FAULTED")
        assess(d)
        assert d.level == "CRITICAL"
        assert any("FAULTED" in r for r in d.reasons)

    def test_critical_low_wear(self):
        d = _disk(wear_val=5)
        assess(d)
        assert d.level == "CRITICAL"
        assert any("wear" in r for r in d.reasons)

    def test_warning_low_wear(self):
        d = _disk(wear_val=20)
        assess(d)
        assert d.level == "WARNING"
        assert any("wear" in r for r in d.reasons)

    def test_multiple_reasons_highest_level_wins(self):
        d = _disk(health="FAILED", end_left=20.0, vdev_state="DEGRADED")
        assess(d)
        assert d.level == "CRITICAL"
        assert len(d.reasons) >= 2


# ========================================================================== #
# TestZfsTopologyParsing
# ========================================================================== #

_MIRROR_STATUS = """\
  pool: rpool
 state: ONLINE
config:

\tNAME                                      STATE     READ WRITE CKSUM
\trpool                                     ONLINE       0     0     0
\t  mirror-0                                ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xAAA-part3       ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xBBB-part3       ONLINE       0     0     0

errors: No known data errors
"""

_RAIDZ_STATUS = """\
  pool: tank
 state: ONLINE
config:

\tNAME                                    STATE     READ WRITE CKSUM
\ttank                                    ONLINE       0     0     0
\t  raidz1-0                              ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xCCC           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xDDD           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xEEE           ONLINE       0     0     0
\tspares
\t    /dev/disk/by-id/wwn-0xFFF           AVAIL

errors: No known data errors
"""

_DEGRADED_STATUS = """\
  pool: tank
 state: DEGRADED
config:

\tNAME                                    STATE     READ WRITE CKSUM
\ttank                                    DEGRADED     0     0     0
\t  raidz1-0                              DEGRADED     0     0     0
\t    /dev/disk/by-id/wwn-0xCCC           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xDDD           FAULTED      0     0     0
\t    /dev/disk/by-id/wwn-0xEEE           ONLINE       0     0     0

errors: No known data errors
"""

_RESILVER_DONE = """\
  pool: tank
 state: ONLINE
  scan: resilvered 500M in 00:00:05 with 0 errors on Mon Jun 16 10:00:00 2025
"""

_RESILVER_PROGRESS = """\
  pool: tank
 state: ONLINE
  scan: resilver in progress since Mon Jun 16 10:00:00 2025
    123M resilvered, 45.2% done, 00:03:21 to go
"""


class TestZfsTopologyParsing:
    """Tests for ZFS topology parsing, membership, and pool inspection."""

    def test_parse_mirror_pool(self):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        assert "/dev/disk/by-id/wwn-0xAAA-part3" in topo
        assert "/dev/disk/by-id/wwn-0xBBB-part3" in topo
        e = topo["/dev/disk/by-id/wwn-0xAAA-part3"]
        assert e["pool"] == "rpool"
        assert e["vdev"] == "mirror-0"
        assert e["state"] == "ONLINE"

    def test_parse_raidz1_with_spares(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        assert "/dev/disk/by-id/wwn-0xCCC" in topo
        assert "/dev/disk/by-id/wwn-0xDDD" in topo
        assert "/dev/disk/by-id/wwn-0xEEE" in topo
        spare = topo["/dev/disk/by-id/wwn-0xFFF"]
        assert spare["vdev"] == "spares"  # note: parser stores the vdev keyword
        assert spare["state"] == "AVAIL"

    def test_parse_mixed_pools(self):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        pools = {e["pool"] for e in topo.values()}
        assert "rpool" in pools
        assert "tank" in pools

    def test_attach_membership_by_id(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        d = _disk(by_id="/dev/disk/by-id/wwn-0xCCC", pool=None, vdev=None,
                  vdev_state=None, pool_token=None)
        zfs.attach_membership([d], topo)
        assert d.pool == "tank"
        assert d.vdev == "raidz1-0"
        assert d.vdev_state == "ONLINE"

    def test_attach_membership_by_serial_fallback(self):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        d = _disk(by_id="/dev/disk/by-id/totally-different", serial="wwn-0xCCC",
                  pool=None, vdev=None, vdev_state=None, pool_token=None)
        zfs.attach_membership([d], topo)
        assert d.pool == "tank"

    @patch("b2ctl.zfs.topology")
    def test_degraded_leaves_finds_faulted(self, mock_topo):
        topo = {}
        zfs._parse("tank", _DEGRADED_STATUS, topo)
        mock_topo.return_value = topo
        bad = zfs.degraded_leaves()
        assert len(bad) == 1
        assert bad[0]["state"] == "FAULTED"
        assert bad[0]["pool"] == "tank"

    @patch("b2ctl.zfs.topology")
    def test_degraded_leaves_ignores_online(self, mock_topo):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        mock_topo.return_value = topo
        bad = zfs.degraded_leaves()
        assert len(bad) == 0

    @patch("b2ctl.zfs.topology")
    def test_can_detach_mirror_with_other_online(self, mock_topo):
        topo = {}
        zfs._parse("rpool", _MIRROR_STATUS, topo)
        mock_topo.return_value = topo
        result = zfs.can_detach("rpool", "/dev/disk/by-id/wwn-0xAAA-part3")
        assert result is True

    @patch("b2ctl.zfs.topology")
    def test_can_detach_raidz_always_false(self, mock_topo):
        topo = {}
        zfs._parse("tank", _RAIDZ_STATUS, topo)
        mock_topo.return_value = topo
        result = zfs.can_detach("tank", "/dev/disk/by-id/wwn-0xCCC")
        assert result is False

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_completed(self, mock_run):
        mock_run.return_value = _RESILVER_DONE
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is True
        assert st["done"] == 100.0

    @patch("b2ctl.zfs.run")
    def test_poll_resilver_in_progress(self, mock_run):
        mock_run.return_value = _RESILVER_PROGRESS
        st = zfs.poll_resilver_status("tank")
        assert st["completed"] is False
        assert st["done"] == pytest.approx(45.2)
        assert "03:21" in st["eta"]


# ========================================================================== #
# TestUI
# ========================================================================== #

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


# ========================================================================== #
# TestSpecLookup
# ========================================================================== #

class TestSpecLookup:
    """Tests for SSD TBW model matching."""

    def test_exact_match(self):
        table = {"samsung ssd 870 evo 1tb": 600}
        assert spec.lookup("Samsung SSD 870 EVO 1TB", table) == 600

    def test_substring_match(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("Samsung SSD 870 EVO 1TB", table) == 600

    def test_no_match_returns_none(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("WD Black", table) is None

    def test_case_insensitive(self):
        table = {"samsung ssd 860 pro 1tb": 1200}
        assert spec.lookup("SAMSUNG SSD 860 PRO 1TB", table) == 1200

    def test_empty_model_returns_none(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("", table) is None


# ========================================================================== #
# TestWatchOffloadGuard
# ========================================================================== #

class TestWatchOffloadGuard:
    """Tests for the offload bug fix: _replace_onto_spare return value guards."""

    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=False)
    def test_replace_onto_spare_returns_false_on_decline(self, _mock_confirm_op, _mock_begin, _mock_end):
        from b2ctl.watch import _replace_onto_spare
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is False

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(False, "some zfs error"))
    def test_replace_onto_spare_returns_false_on_zfs_failure(self, _mrc, _mc, _mb, _me, mock_zfs, _ml):
        from b2ctl.watch import _replace_onto_spare
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is False

    @patch("b2ctl.watch.locate")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.safety.end_op")
    @patch("b2ctl.safety.begin_op", return_value="test-op-id")
    @patch("b2ctl.watch._confirm_op", return_value=True)
    @patch("b2ctl.watch.run_check", return_value=(True, ""))
    def test_replace_onto_spare_returns_true_on_success(self, _mrc, _mc, _mb, _me, mock_zfs, _ml):
        from b2ctl.watch import _replace_onto_spare
        mock_zfs.poll_resilver_status.return_value = {"completed": True, "done": 100.0, "eta": ""}
        mock_zfs.topology.return_value = {}
        d = _disk()
        spare = _disk(dev="/dev/sdb", serial="SPARE1", vdev="spares",
                      pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        result = _replace_onto_spare(d, spare)
        assert result is True

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._replace_onto_spare", return_value=False)
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_cmd_offload_does_not_assign_when_replace_declines(self, mock_ask,
                                                                mock_core,
                                                                mock_replace,
                                                                mock_assign):
        from b2ctl.watch import _cmd_offload
        d = _disk(bay="1:4", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        _cmd_offload({})
        mock_assign.assert_not_called()

    @patch("b2ctl.watch._assign_free_disk")
    @patch("b2ctl.watch._replace_onto_spare", return_value=True)
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_cmd_offload_assigns_when_replace_succeeds(self, mock_ask,
                                                        mock_core,
                                                        mock_replace,
                                                        mock_assign):
        from b2ctl.watch import _cmd_offload
        d = _disk(bay="1:4", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        _cmd_offload({})
        mock_assign.assert_called_once()


# ========================================================================== #
# TestWatchSwap
# ========================================================================== #

class TestWatchSwap:
    """Tests for the swap fix: re-add old disk as spare instead of blink."""

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_swap_readds_as_spare_on_success(self, mock_ask, _mc, mock_core,
                                              mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank", pool_token="/dev/disk/by-id/wwn-0xSPARE",
                      by_id="/dev/disk/by-id/wwn-0xSPARE")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        mock_zfs.swap_to_spare.return_value = (True, "")
        mock_zfs.poll_resilver_status.return_value = {"completed": True, "done": 100.0, "eta": ""}
        mock_zfs.topology.return_value = {}
        mock_zfs.add_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:7) Samsung SSD 870 (TEST)"

        _cmd_swap({})

        mock_zfs.add_spare.assert_called_once_with("tank", d.by_id or d.dev)

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_swap_no_spare_available(self, mock_ask, mock_core, capsys):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        _cmd_swap({})
        captured = capsys.readouterr()
        assert "no AVAIL spare" in captured.out

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=False)
    @patch("b2ctl.watch._ask")
    def test_swap_cancelled_on_decline(self, mock_ask, _mc, mock_core,
                                        mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_swap
        d = _disk(bay="1:7", vdev="raidz1-0")
        spare = _disk(dev="/dev/sdb", vdev="spares", vdev_state="AVAIL",
                      pool="tank")
        mock_core.scan.return_value = [d, spare]
        mock_ask.return_value = "1"
        mock_ui.disk_label.return_value = "(1:7) Test"
        _cmd_swap({})
        mock_zfs.swap_to_spare.assert_not_called()


# ========================================================================== #
# TestWatchCreate
# ========================================================================== #

class TestWatchCreate:
    """Tests for pool creation validation."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_create_pool_validates_min_disks(self, mock_ask, _mc, mock_core,
                                              mock_zfs, capsys):
        from b2ctl.watch import _cmd_create
        import b2ctl.zfs as real_zfs
        d = _disk(pool=None, vdev=None, vdev_state=None)
        # Override in_pool property by setting pool=None
        mock_core.scan.return_value = [d]
        mock_ask.side_effect = ["1", "mypool", "raidz2"]
        mock_zfs.MIN_DISKS = real_zfs.MIN_DISKS
        _cmd_create({})
        captured = capsys.readouterr()
        assert "need at least" in captured.out
        mock_zfs.create_pool.assert_not_called()

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_create_pool_invalid_raid_type(self, mock_ask, mock_core,
                                            mock_zfs, capsys):
        from b2ctl.watch import _cmd_create
        d1 = _disk(pool=None, vdev=None, vdev_state=None)
        d2 = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sdb",
                   serial="S2")
        mock_core.scan.return_value = [d1, d2]
        mock_ask.side_effect = ["1 2", "mypool", "raidz99"]
        _cmd_create({})
        captured = capsys.readouterr()
        assert "invalid raid type" in captured.out
        mock_zfs.create_pool.assert_not_called()


# ========================================================================== #
# TestWatchDemote
# ========================================================================== #

class TestWatchDemote:
    """Tests for demote command."""

    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._ask")
    def test_demote_refuses_non_detachable(self, mock_ask, mock_core,
                                            mock_zfs, capsys):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        mock_zfs.can_detach.return_value = False
        _cmd_demote({})
        captured = capsys.readouterr()
        assert "refuse" in captured.out

    @patch("b2ctl.watch.ui")
    @patch("b2ctl.watch.zfs")
    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._confirm", return_value=True)
    @patch("b2ctl.watch._ask")
    def test_demote_calls_demote_to_spare(self, mock_ask, _mc, mock_core,
                                           mock_zfs, mock_ui):
        from b2ctl.watch import _cmd_demote
        d = _disk(vdev="mirror-0")
        mock_core.scan.return_value = [d]
        mock_ask.return_value = "1"
        mock_zfs.can_detach.return_value = True
        mock_zfs.demote_to_spare.return_value = (True, "")
        mock_ui.disk_label.return_value = "(1:0) Test"
        _cmd_demote({})
        mock_zfs.demote_to_spare.assert_called_once()


# ========================================================================== #
# TestHbaBayMapping
# ========================================================================== #

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


# ========================================================================== #
# TestSmartParsing
# ========================================================================== #

_ATA_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Device Model:     Samsung SSD 870 EVO 1TB
Serial Number:    S74ZNS0W582303N
Firmware Version: SVT02B6Q
User Capacity:    1,000,204,886,016 bytes

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 1
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       18238
177 Wear_Leveling_Count     0x0013   099   099   005    Pre-fail  Always       -       1
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
241 Total_LBAs_Written      0x0032   099   099   000    Old_age   Always       -       19305985024
"""

_SAS_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Vendor:               SAMSUNG
Product:              MZ7LH1T9HMLT
Serial number:        S4F2NY0M105699
Device type:          disk

Percentage used endurance indicator: 1%
Accumulated power on time, hours:minutes 50451:14

write:  Total                          Secs   GBytes    MBytes  IOs  MBytes/s
  Total: 114131199 231456.8  118551.6   1024.0        5                  0.51

Elements in grown defect list: 0

SMART Health Status: OK
"""


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


# ========================================================================== #
# TestZfsActions (command building, mocked subprocess)
# ========================================================================== #

class TestZfsActions:
    """Tests for ZFS action wrappers (all subprocess calls mocked)."""

    @patch("b2ctl.zfs.run_check")
    def test_add_spare_command(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.add_spare("tank", "/dev/disk/by-id/wwn-0xABC")
        mock_rc.assert_called_with(["zpool", "add", "-f", "tank", "spare",
                                    "/dev/disk/by-id/wwn-0xABC"])
        assert ok

    @patch("b2ctl.zfs.run_check")
    def test_replace_command(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.replace("tank", "old-dev", "new-dev")
        mock_rc.assert_called_with(["zpool", "replace", "-f", "tank",
                                    "old-dev", "new-dev"])

    @patch("b2ctl.zfs.run_check")
    def test_create_pool_raidz1(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.create_pool("backup", "raidz1", ["/dev/sda", "/dev/sdb", "/dev/sdc"])
        args = mock_rc.call_args[0][0]
        assert "zpool" in args
        assert "create" in args
        assert "raidz1" in args
        assert "/dev/sda" in args

    @patch("b2ctl.zfs.run_check")
    def test_create_pool_stripe_no_raid_keyword(self, mock_rc):
        mock_rc.return_value = (True, "")
        zfs.create_pool("fast", "stripe", ["/dev/sda"])
        args = mock_rc.call_args[0][0]
        assert "stripe" not in args
        assert "/dev/sda" in args

    @patch("b2ctl.zfs.run_check")
    def test_demote_to_spare_calls_detach_then_add(self, mock_rc):
        mock_rc.return_value = (True, "")
        ok, _ = zfs.demote_to_spare("rpool", "/dev/disk/by-id/wwn-0xBBB-part3")
        assert mock_rc.call_count == 2
        first_call = mock_rc.call_args_list[0][0][0]
        second_call = mock_rc.call_args_list[1][0][0]
        assert "detach" in first_call
        assert "spare" in second_call

    def test_min_disks_constants(self):
        assert zfs.MIN_DISKS["stripe"] == 1
        assert zfs.MIN_DISKS["mirror"] == 2
        assert zfs.MIN_DISKS["raidz1"] == 2
        assert zfs.MIN_DISKS["raidz2"] == 4


# ========================================================================== #
# TestPerformanceFixes — 6 perf/correctness fixes (previous session)
# ========================================================================== #

from b2ctl import core as _core_mod
from b2ctl.common import Disk as _Disk


class TestPerformanceFixes:
    """Verify the 6 performance + correctness fixes without real hardware."""

    # ------------------------------------------------------------------ #
    # Fix 1 — bay_map() caching via bm= param
    # ------------------------------------------------------------------ #

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_attach_bays_reuses_provided_bm(self, _load, _has, mock_bm):
        d = _Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d], bm={"SN001": "1:0"})
        mock_bm.assert_not_called()
        assert d.bay == "1:0"

    @patch("b2ctl.hba.bay_map", return_value={})
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_attach_bays_default_calls_bay_map(self, _load, _has, mock_bm):
        d = _Disk(dev="/dev/sda", serial="SN001")
        hba.attach_bays([d])
        mock_bm.assert_called_once()

    @patch("b2ctl.hba.bay_map")
    @patch("b2ctl.hba.have_sas2ircu", return_value=True)
    @patch("b2ctl.hba._load_bay_map", return_value={})
    def test_get_ghost_disks_reuses_provided_bm(self, _load, _has, mock_bm):
        d = _Disk(dev="/dev/sda", serial="SN001")
        ghosts = hba.get_ghost_disks([d], bm={"SN999": "1:7"})
        mock_bm.assert_not_called()
        assert len(ghosts) == 1
        assert ghosts[0].serial == "SN999"

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

    # ------------------------------------------------------------------ #
    # Fix 2 — parallel smartctl (correctness: all non-ghost disks read)
    # ------------------------------------------------------------------ #

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_smart_called_for_all_non_ghost_disks(self, mock_spec, mock_backend_mod,
                                                         mock_smart, mock_zfs):
        real1 = _Disk(dev="/dev/sda", serial="R1")
        real2 = _Disk(dev="/dev/sdb", serial="R2")
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

    # ------------------------------------------------------------------ #
    # Fix 3 — parallel ghost rescue
    # ------------------------------------------------------------------ #

    @patch("b2ctl.core.zfs")
    @patch("b2ctl.core.smart")
    @patch("b2ctl.core._backend_mod")
    @patch("b2ctl.core.spec")
    def test_scan_rescues_all_ghost_serials(self, mock_spec, mock_backend_mod,
                                             mock_smart, mock_zfs):
        ghost1 = _Disk(dev="-", serial="G1", health="GHOST")
        ghost2 = _Disk(dev="-", serial="G2", health="GHOST")
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
        ghost1 = _Disk(dev="-", serial="G1", health="GHOST")
        ghost2 = _Disk(dev="-", serial="G2", health="GHOST")
        ghost2_new = _Disk(dev="-", serial="G2", health="GHOST")
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

    # ------------------------------------------------------------------ #
    # Fix 4 — udevadm settle replaces lsblk poll loop
    # ------------------------------------------------------------------ #

    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_calls_settle_once(self, mock_hba):
        mock_hba.run.return_value = ""
        mock_hba._lsblk_pairs.return_value = [
            {"TYPE": "disk", "SERIAL": "SN123", "NAME": "sda"}
        ]
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN123", timeout=20)
        mock_hba.run.assert_called_once_with(["udevadm", "settle", "--timeout=20"])
        assert mock_hba._lsblk_pairs.call_count == 1
        assert result == "/dev/sda"

    @patch("b2ctl.watch.hba")
    def test_wait_for_block_device_returns_none_when_missing(self, mock_hba):
        mock_hba.run.return_value = ""
        mock_hba._lsblk_pairs.return_value = []
        from b2ctl.watch import _wait_for_block_device
        result = _wait_for_block_device("SN999", timeout=5)
        mock_hba.run.assert_called_once_with(["udevadm", "settle", "--timeout=5"])
        assert result is None

    # ------------------------------------------------------------------ #
    # Fix 5 — all_disks= param avoids double scan in choice 4
    # ------------------------------------------------------------------ #

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="4")
    def test_assign_choice4_skips_scan_when_all_disks_provided(
            self, _ask, _pick, mock_core):
        from b2ctl.watch import _assign_free_disk
        d = _Disk(dev="/dev/sdb", by_id="/dev/disk/by-id/wwn-test")
        _assign_free_disk(d, {}, all_disks=[])
        mock_core.scan.assert_not_called()

    @patch("b2ctl.watch.core")
    @patch("b2ctl.watch._pick_pool", return_value="tank")
    @patch("b2ctl.watch._ask", return_value="4")
    def test_assign_choice4_calls_scan_when_all_disks_none(
            self, _ask, _pick, mock_core):
        from b2ctl.watch import _assign_free_disk
        mock_core.scan.return_value = []
        d = _Disk(dev="/dev/sdb", by_id="/dev/disk/by-id/wwn-test")
        _assign_free_disk(d, {}, all_disks=None)
        mock_core.scan.assert_called_once()


# ========================================================================== #
# TestConfig — config.py tool path resolution, defaults, mode/index
# ========================================================================== #

import importlib

class TestConfig:
    """Tests for config.py — tool resolution, missing file defaults."""

    def setup_method(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None

    def test_tool_returns_config_override_when_set(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"sas2ircu": "/custom/sas2ircu"},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        assert cfg_mod.tool("sas2ircu") == "/custom/sas2ircu"

    def test_tool_fallback_to_which_when_empty(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"smartctl": ""},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        with patch("b2ctl.config.shutil.which", return_value="/usr/sbin/smartctl"):
            result = cfg_mod.tool("smartctl")
        assert result == "/usr/sbin/smartctl"

    def test_tool_falls_back_to_bare_name_when_not_in_path(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"sas2ircu": ""},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        with patch("b2ctl.config.shutil.which", return_value=None):
            result = cfg_mod.tool("sas2ircu")
        assert result == "sas2ircu"

    def test_bay_map_path_returns_config_override(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "/srv/bay_map.json",
        }
        assert cfg_mod.bay_map_path() == "/srv/bay_map.json"

    def test_bay_map_path_returns_bundled_when_empty(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        p = cfg_mod.bay_map_path()
        assert p.endswith("bay_map.json")

    def test_load_returns_defaults_when_no_file(self):
        import b2ctl.config as cfg_mod
        with patch("b2ctl.config.os.path.exists", return_value=False):
            cfg = cfg_mod.load()
        assert cfg["controller"]["mode"] == "auto"
        assert cfg["controller"]["index"] == "all"
        assert cfg["tool_paths"]["sas2ircu"] == ""
        assert cfg["bay_map_path"] == ""

    def test_controller_mode_returns_auto_by_default(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None
        with patch("b2ctl.config.os.path.exists", return_value=False):
            assert cfg_mod.controller_mode() == "auto"

    def test_controller_index_setting_returns_all_by_default(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None
        with patch("b2ctl.config.os.path.exists", return_value=False):
            assert cfg_mod.controller_index_setting() == "all"


# ========================================================================== #
# TestBackend — backend detection, caching, backend types
# ========================================================================== #

class TestBackend:
    """Tests for backend.py — detection, caching, backend class names."""

    def setup_method(self):
        import b2ctl.backend as bk_mod
        import b2ctl.config as cfg_mod
        bk_mod._backend_cache = None
        cfg_mod._cache = None

    def _it_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "it", "index": "all"},
            "bay_map_path": "",
        }

    def _raid_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "raid", "index": "all"},
            "bay_map_path": "",
        }

    def _auto_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }

    def test_it_backend_name_is_it(self):
        from b2ctl.backend import ITBackend
        assert ITBackend().name == "it"

    def test_raid_backend_name_is_raid(self):
        from b2ctl.backend import RaidBackend
        assert RaidBackend().name == "raid"

    def test_get_backend_returns_it_when_mode_it(self):
        import b2ctl.backend as bk_mod
        self._it_mode_cache()
        bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.ITBackend)

    def test_get_backend_returns_raid_when_mode_raid(self):
        import b2ctl.backend as bk_mod
        self._raid_mode_cache()
        bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_get_backend_autodetects_it_via_sas2ircu(self):
        import b2ctl.backend as bk_mod
        self._auto_mode_cache()
        with patch("b2ctl.backend.run", return_value="  0  SAS2308"):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.ITBackend)

    def test_get_backend_autodetects_raid_via_storcli(self):
        import b2ctl.backend as bk_mod
        self._auto_mode_cache()

        def _mock_run(cmd):
            # sas2ircu list → empty; storcli64 show ctrlcount → match
            if cmd[-1] == "list":
                return ""
            return "Number of Controllers = 1"

        with patch("b2ctl.backend.run", side_effect=_mock_run):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_get_backend_is_cached(self):
        import b2ctl.backend as bk_mod
        self._it_mode_cache()
        bk1 = bk_mod.get_backend()
        bk2 = bk_mod.get_backend()
        assert bk1 is bk2

