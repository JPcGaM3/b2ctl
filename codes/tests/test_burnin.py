"""Unit tests for b2ctl.burnin — self-test parsing, verdict, read-only scan."""
import unittest
from unittest.mock import patch

import b2ctl.burnin as burnin
from helpers import _disk


# Real ATA `smartctl -a` output carries BOTH the execution-status block (the
# CURRENT test) and the persistent self-test log table (history). The parser
# must read the former, not the latter.
_ATA_DONE = """Self-test execution status:      (   0) The previous self-test routine completed
                                        without error or no self-test has ever
                                        been run.

SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)
# 1  Extended offline    Completed without error       00%      18000
"""

# The killer case (F-030): current test ABORTED, but an old history row still
# says "Completed without error". The gate must FAIL, not read the stale row.
_ATA_ABORTED_STALE = """Self-test execution status:      (  25) The previous self-test routine was
                                        aborted by the host.

SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)
# 1  Extended offline    Completed without error       00%      18000
# 2  Short offline       Completed without error       00%      17900
"""

_ATA_RUNNING = """Self-test execution status:      ( 249) Self-test routine in progress...
                                        40% of test remaining.
"""

_SAS_RUNNING = "Background self-test in progress ... 20% complete\n"

_SAS_DONE = """SMART Self-test log
Num  Test              Status                 segment  LifeTime  LBA_first_err
# 1  Background long   Completed                   -   50450                 -
"""


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

    def test_sas_done_reads_log_row(self):
        with patch.object(burnin, "_run", return_value=_SAS_DONE):
            st = burnin.selftest_status("/dev/sda")
        self.assertFalse(st["running"])
        self.assertIn("completed", st["result"].lower())

    def test_selftest_status_aborted_ignores_history(self):
        # F-030: current abort must not be masked by a stale passing log row.
        with patch.object(burnin, "_run", return_value=_ATA_ABORTED_STALE):
            st = burnin.selftest_status("/dev/sda")
        self.assertFalse(st["running"])
        self.assertIn("aborted", st["result"].lower())
        self.assertNotIn("without error", st["result"].lower())

    def test_aborted_selftest_makes_assess_fail(self):
        # end-to-end: an aborted current test -> assess() verdict FAIL
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=10000)
        with patch.object(burnin, "_run", return_value=_ATA_ABORTED_STALE):
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("self-test" in r.lower() for r in reasons))


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

    def test_selftest_passes_megaraid_dtype(self):
        # F-011: RAID-mode passthrough needs -d <dtype> to actually start.
        seen = {}
        with patch.object(burnin, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            burnin.start_selftest("/dev/sda", "long", "megaraid,7")
        self.assertEqual(seen["cmd"],
                         ["smartctl", "-t", "long", "-d", "megaraid,7", "/dev/sda"])


class TestRunFlow(unittest.TestCase):
    """F-067 — burnin.run() orchestration: target resolution, the in-pool
    refusal guard, dry-run early return, and the FAIL -> exit 1 mapping."""

    def test_refuses_in_pool_member(self):
        d = _disk(dev="/dev/sdb", pool="tank", vdev="raidz1-0")   # in a pool
        with patch.object(burnin, "start_selftest") as start:
            rc = burnin.run(d)
        self.assertEqual(rc, 1)
        start.assert_not_called()                # never self-test an active member

    def test_resolves_string_target_by_serial(self):
        target = _disk(dev="/dev/sdh", serial="SER-NEW", pool=None,
                       vdev=None, vdev_state=None)
        started = {}
        with patch("b2ctl.core.scan", return_value=[target]), \
             patch("b2ctl.spec.load", return_value={}), \
             patch.object(burnin, "start_selftest",
                          side_effect=lambda dev, *a, **k: (started.setdefault("dev", dev), (True, ""))[1]), \
             patch.object(burnin, "_wait_selftest", return_value=True), \
             patch("b2ctl.smart.read"), \
             patch.object(burnin, "assess", return_value=("PASS", [])):
            rc = burnin.run("SER-NEW")
        self.assertEqual(rc, 0)
        self.assertEqual(started["dev"], "/dev/sdh")   # resolved the right disk

    def test_dry_run_skips_wait(self):
        d = _disk(dev="/dev/sdh", pool=None, vdev=None, vdev_state=None)
        with patch.object(burnin, "start_selftest", return_value=(True, "")), \
             patch.object(burnin, "_wait_selftest") as wait, \
             patch("b2ctl.smart.read"):
            rc = burnin.run(d, dry_run=True)
        self.assertEqual(rc, 0)
        wait.assert_not_called()                 # dry-run returns before polling

    def test_fail_verdict_exits_1(self):
        d = _disk(dev="/dev/sdh", pool=None, vdev=None, vdev_state=None)
        with patch.object(burnin, "start_selftest", return_value=(True, "")), \
             patch.object(burnin, "_wait_selftest", return_value=True), \
             patch("b2ctl.smart.read"), \
             patch.object(burnin, "assess", return_value=("FAIL", ["uncorrected errors = 3"])):
            rc = burnin.run(d)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
