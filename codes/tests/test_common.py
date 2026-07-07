"""Unit tests for b2ctl.common — assess() health levels + run_check dry-run."""
from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from helpers import _disk
from b2ctl.common import assess


# ========================================================================== #
# assess() — health-level assignment logic
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

    def test_ugood_perc_drive_is_config_not_critical(self):
        # A HIDDEN PERC Unconfigured-Good drive (megaraid passthrough) is
        # available, not a ghost/critical.
        d = _disk(pool=None, vdev=None, vdev_state=None)
        d.pd_state = "UGood"; d.smart_dtype = "megaraid,4"
        assess(d)
        assert d.level == "CONFIG"
        assert any("Unconfigured Good" in r for r in d.reasons)

    def test_failed_perc_drive_is_critical(self):
        d = _disk(pool=None, vdev=None, vdev_state=None)
        d.pd_state = "Failed"; d.smart_dtype = "megaraid,4"
        assess(d)
        assert d.level == "CRITICAL"
        assert any("PD state=Failed" in r for r in d.reasons)

    def test_jbod_exposed_drive_is_plain_unassigned(self):
        # A JBOD'd drive has its own /dev/sdX (no megaraid passthrough) — it is a
        # normal unassigned disk that ZFS can pool, not a hidden PERC drive.
        d = _disk(pool=None, vdev=None, vdev_state=None, dev="/dev/sdb")
        d.pd_state = "JBOD"   # smart_dtype stays "" (read direct)
        assess(d)
        assert d.level == "CONFIG"
        assert any("unassigned" in r for r in d.reasons)

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
# run_check() — dry-run gating of write commands
# ========================================================================== #

class TestRunCheckDryRun(unittest.TestCase):

    def test_dry_run_write_cmd_no_subprocess(self):
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            ok, out = common.run_check(["zpool", "replace", "tank", "x", "y"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(out, "")

    def test_dry_run_read_cmd_executes(self):
        # "lsblk" is not in WRITE_CMDS — a read-only cmd passes through even
        # when dry_run=True.
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="tank\n", stderr="")
            ok, out = common.run_check(["lsblk", "-P", "/dev/sda"], dry_run=True)
        mock_run.assert_called_once()
        self.assertTrue(ok)

    def test_dry_run_false_write_cmd_executes(self):
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            ok, out = common.run_check(["zpool", "offline", "tank", "x"])
        mock_run.assert_called_once()
        self.assertTrue(ok)

    def test_dry_run_perccli_absolute_path_suppressed(self):
        # F-004/F-008: a config-resolved absolute perccli path must be gated by
        # basename, so a --dry-run RAID replace never really offlines a member.
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            ok, out = common.run_check(
                ["/usr/sbin/perccli64", "/c0/e32/s4", "set", "offline"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(out, "")

    def test_dry_run_bare_perccli_suppressed(self):
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            ok, _ = common.run_check(["perccli", "/c0/v0", "del", "force"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)

    def test_dry_run_smartctl_selftest_suppressed(self):
        # burnin.start_selftest runs `smartctl -t long` via run_check — a write.
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            ok, _ = common.run_check(["smartctl", "-t", "long", "/dev/sda"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)


# ========================================================================== #
# Disk properties — is_spare / is_poolable invariants
# ========================================================================== #

class TestDiskProperties:
    """Disk.is_spare (F-074) and Disk.is_poolable (F-103) contract tests."""

    def test_is_spare_excludes_spare_n_member(self):
        # F-074: only the pool's spares SECTION (vdev == "spares") is a hot
        # spare. The FAULTED original leaf nested under a transient spare-N
        # vdev during activation must stay a regular member, not a spare.
        assert _disk(vdev="spare-1", vdev_state="REMOVED").is_spare is False
        assert _disk(vdev="spares", vdev_state="AVAIL").is_spare is True

    def test_disk_is_poolable(self):
        # F-103: the single authority for the "free, poolable disk" invariant.
        from b2ctl.common import Disk
        # hidden PERC member shares the VD's /dev/sda (smart_dtype set) -> never
        # poolable (would sgdisk --zap-all the OS's hardware VD).
        assert Disk(dev="/dev/sda", smart_dtype="megaraid,4").is_poolable is False
        # GHOST has no /dev node (dev == "-").
        assert Disk(dev="-", health="GHOST").is_poolable is False
        # JBOD'd raw disk with its own block device -> poolable.
        assert Disk(dev="/dev/sdb").is_poolable is True
        # a pool member is not free.
        assert Disk(dev="/dev/sdc", pool="tank").is_poolable is False


# ========================================================================== #
# Dry-run single source of truth (F-098)
# ========================================================================== #

class TestDryRunSingleSource(unittest.TestCase):
    """F-098: the dry-run flag lives at the bottom layer (common); mid-layer
    action modules read it via common.is_dry_run() — no import of the
    interactive watch UI just to read a flag."""

    def test_dry_run_single_source(self):
        import b2ctl.common as common
        import b2ctl.raid_actions as raid_actions
        common.set_dry_run(True)
        try:
            self.assertTrue(common.is_dry_run())
            self.assertTrue(raid_actions._dry())   # observed without importing watch
        finally:
            common.set_dry_run(False)
        self.assertFalse(raid_actions._dry())


class TestTypeAwareThresholds:
    """assess() bad-sector grading splits SSD/NVMe (strict) vs HDD (banded),
    driven by the config `health` section (defaults)."""

    def test_ssd_any_realloc_is_critical(self):
        d = _disk(realloc=1, is_ssd=True)
        assess(d)
        assert d.level == "CRITICAL"

    def test_hdd_few_defects_is_normal(self):
        d = _disk(realloc=10, is_ssd=False)         # <= 50 warn threshold
        assess(d)
        assert d.level == "NORMAL"

    def test_hdd_defects_over_warn_is_warning(self):
        d = _disk(realloc=60, is_ssd=False)
        assess(d)
        assert d.level == "WARNING"
        assert any("reallocated" in r or "defects" in r for r in d.reasons)

    def test_hdd_defects_over_crit_is_critical(self):
        d = _disk(realloc=300, is_ssd=False)
        assess(d)
        assert d.level == "CRITICAL"

    def test_hdd_pending_is_warning_not_critical(self):
        d = _disk(pending=1, is_ssd=False)
        assess(d)
        assert d.level == "WARNING"

    def test_uncorrectable_is_critical_on_hdd_too(self):
        d = _disk(uncorr=1, is_ssd=False)
        assess(d)
        assert d.level == "CRITICAL"

    def test_hdd_has_no_endurance_or_wear_grading(self):
        # HDD endurance/wear thresholds default to None -> not graded at all
        d = _disk(end_left=5.0, wear_val=5, is_ssd=False)
        assess(d)
        assert d.level == "NORMAL"

    def test_ssd_endurance_critical_at_20(self):
        d = _disk(end_left=15.0, is_ssd=True)       # < 20 crit
        assess(d)
        assert d.level == "CRITICAL"

    def test_ssd_endurance_warning_between_20_and_30(self):
        d = _disk(end_left=25.0, is_ssd=True)       # 20..30 -> WARNING
        assess(d)
        assert d.level == "WARNING"

    def test_config_null_threshold_disables_band(self):
        # health.hdd.realloc_crit = None -> a huge defect count grades only WARNING
        d = _disk(realloc=300, is_ssd=False)
        cfg = {"ssd": {}, "hdd": {"realloc_warn": 50, "realloc_crit": None,
                                  "pending_warn": None, "pending_crit": None,
                                  "uncorr_warn": None, "uncorr_crit": None,
                                  "endurance_warn": None, "endurance_crit": None,
                                  "wear_warn": None, "wear_crit": None}}
        with patch("b2ctl.config.health_config", return_value=cfg):
            assess(d)
        assert d.level == "WARNING"


if __name__ == "__main__":
    unittest.main()
