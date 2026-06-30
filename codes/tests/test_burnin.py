"""Unit tests for b2ctl.burnin — self-test parsing, verdict, read-only scan."""
import unittest
from unittest.mock import patch

import b2ctl.burnin as burnin
from helpers import _disk


_ATA_DONE = """SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)
# 1  Extended offline    Completed without error       00%      18000
"""

_ATA_RUNNING = """Self-test execution status:      ( 249) Self-test routine in progress...
                                        40% of test remaining.
"""

_SAS_RUNNING = "Background self-test in progress ... 20% complete\n"


class TestSelftestStatus(unittest.TestCase):

    def test_done_without_error(self):
        with patch.object(burnin, "_run", return_value=_ATA_DONE):
            st = burnin.selftest_status("/dev/sda")
        self.assertFalse(st["running"])
        self.assertEqual(st["pct"], 100)
        self.assertIn("without error", st["result"].lower())

    def test_running_ata_remaining(self):
        with patch.object(burnin, "_run", return_value=_ATA_RUNNING):
            st = burnin.selftest_status("/dev/sda")
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 60)          # 100 - 40 remaining

    def test_running_sas_complete(self):
        with patch.object(burnin, "_run", return_value=_SAS_RUNNING):
            st = burnin.selftest_status("/dev/sda")
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 20)


class TestAssess(unittest.TestCase):

    def _patch_status(self, result="Completed without error"):
        return patch.object(burnin, "selftest_status",
                            return_value={"running": False, "pct": 100, "result": result})

    def test_pass(self):
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=10000)
        with self._patch_status():
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(reasons, [])

    def test_fail_on_uncorrected(self):
        d = _disk(health="PASSED", uncorr=5, realloc=0, poh=10000)
        with self._patch_status():
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("uncorrected" in r for r in reasons))

    def test_fail_on_selftest_error(self):
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=10000)
        with self._patch_status(result="Completed: read failure"):
            verdict, _ = burnin.assess(d)
        self.assertEqual(verdict, "FAIL")

    def test_warn_on_high_poh(self):
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=45000)
        with self._patch_status():
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "WARN")
        self.assertTrue(any("power-on" in r.lower() for r in reasons))

    def test_warn_on_grown_defects(self):
        d = _disk(health="PASSED", uncorr=0, realloc=3, poh=10000)
        with self._patch_status():
            verdict, _ = burnin.assess(d)
        self.assertEqual(verdict, "WARN")


class TestReadScan(unittest.TestCase):

    def test_read_only_argv_no_write_flag(self):
        seen = {}
        with patch.object(burnin, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            burnin.read_scan("/dev/sdb")
        cmd = seen["cmd"]
        self.assertEqual(cmd[:4], ["badblocks", "-sv", "-b", "4096"])
        self.assertIn("/dev/sdb", cmd)
        self.assertNotIn("-w", cmd)              # never destructive


class TestStartSelftest(unittest.TestCase):

    def test_long_selftest_argv(self):
        seen = {}
        with patch.object(burnin, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            burnin.start_selftest("/dev/sdb", "long")
        self.assertEqual(seen["cmd"], ["smartctl", "-t", "long", "/dev/sdb"])


if __name__ == "__main__":
    unittest.main()
