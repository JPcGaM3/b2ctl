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
        import b2ctl.safety as safety
        safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}
        with patch("subprocess.run") as mock_run:
            ok, out = common.run_check(["zpool", "replace", "tank", "x", "y"], dry_run=True)
        mock_run.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(out, "")

    def test_dry_run_read_cmd_executes(self):
        # "smartctl" is not in WRITE_CMDS — it is a read-only cmd that must
        # pass through even when dry_run=True.
        import b2ctl.common as common
        import b2ctl.safety as safety
        safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="pool: tank\n", stderr="")
            ok, out = common.run_check(["smartctl", "-a", "/dev/sda"], dry_run=True)
        mock_run.assert_called_once()
        self.assertTrue(ok)

    def test_dry_run_false_write_cmd_executes(self):
        import b2ctl.common as common
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            ok, out = common.run_check(["zpool", "offline", "tank", "x"])
        mock_run.assert_called_once()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
