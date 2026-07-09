"""Unit tests for b2ctl.burnin — self-test parsing, ETA, background scan,
state file, multi-disk run + live view, verdict."""
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

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

# The recommended-polling-time label spans TWO lines in real smartctl output —
# the ETA parser must bridge them. 90 min * 40% remaining = 36 min ETA.
_ATA_RUNNING_ETA = """Extended self-test routine
recommended polling time: 	(  90) minutes.

Self-test execution status:      ( 249) Self-test routine in progress...
                                        40% of test remaining.
"""

_SAS_RUNNING = "Background self-test in progress ... 20% complete\n"

_SAS_DONE = """SMART Self-test log
Num  Test              Status                 segment  LifeTime  LBA_first_err
# 1  Background long   Completed                   -   50450                 -
"""

# Real R740xd SAS output (bug report v0.18.0): newest row is a bare 'Completed'
# (SAS success has NO 'without error'), older rows are user-aborted. The newest
# must win and grade PASS, and the parsed status must be clean 'Completed' — not
# the greedy old capture that swallowed the '- 41722 -' tail.
_SAS_DONE_REAL = """SMART Self-test log
Num  Test              Status                 segment  LifeTime  LBA_first_err [SK ASC ASQ]
     Description                              number   (hours)
# 1  Background long   Completed                   -   41723                 - [-   -    -]
# 2  Background long   Aborted (by user command)   8   41722                 - [-   -    -]
"""


class TestParseSelftest(unittest.TestCase):
    """Pure parser shared by selftest_status() and smart.read()."""

    def test_ata_running_pct(self):
        st = burnin.parse_selftest(_ATA_RUNNING)
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 60)          # 100 - 40 remaining
        self.assertIsNone(st["eta_min"])         # no polling-time line here

    def test_ata_running_eta_from_two_line_polling_time(self):
        st = burnin.parse_selftest(_ATA_RUNNING_ETA)
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 60)
        self.assertEqual(st["eta_min"], 36)      # 90 * 40/100

    def test_sas_running_complete(self):
        st = burnin.parse_selftest(_SAS_RUNNING)
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 20)

    def test_done_not_running(self):
        st = burnin.parse_selftest(_ATA_DONE)
        self.assertFalse(st["running"])
        self.assertEqual(st["pct"], 100)
        self.assertIsNone(st["eta_min"])


class TestSelftestStatus(unittest.TestCase):

    def test_done_without_error(self):
        with patch.object(burnin, "_run", return_value=_ATA_DONE):
            st = burnin.selftest_status("/dev/sda")
        self.assertFalse(st["running"])
        self.assertEqual(st["pct"], 100)
        self.assertIn("without error", st["result"].lower())

    def test_running_ata_remaining_has_eta_key(self):
        with patch.object(burnin, "_run", return_value=_ATA_RUNNING_ETA):
            st = burnin.selftest_status("/dev/sda")
        self.assertTrue(st["running"])
        self.assertEqual(st["pct"], 60)
        self.assertEqual(st["eta_min"], 36)

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

    def test_sas_done_result_is_clean(self):
        # v0.18.0: the status column is parsed cleanly (not the greedy old capture
        # that trailed '- 41723 -' into the result string).
        with patch.object(burnin, "_run", return_value=_SAS_DONE_REAL):
            st = burnin.selftest_status("/dev/sda")
        self.assertEqual(st["result"], "Completed")   # newest row (# 1), clean

    def test_sas_real_output_assess_pass(self):
        # The reported bug: a healthy SAS disk whose newest self-test is bare
        # 'Completed' was graded FAIL. It must be PASS now.
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=41723)
        with patch.object(burnin, "_run", return_value=_SAS_DONE_REAL):
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(reasons, [])

    def test_selftest_status_aborted_ignores_history(self):
        # F-030: current abort must not be masked by a stale passing log row.
        with patch.object(burnin, "_run", return_value=_ATA_ABORTED_STALE):
            st = burnin.selftest_status("/dev/sda")
        self.assertFalse(st["running"])
        self.assertIn("aborted", st["result"].lower())
        self.assertNotIn("without error", st["result"].lower())

    def test_aborted_selftest_makes_assess_fail(self):
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=10000)
        with patch.object(burnin, "_run", return_value=_ATA_ABORTED_STALE):
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("self-test" in r.lower() for r in reasons))


class TestAssess(unittest.TestCase):

    def _patch_status(self, result="Completed without error"):
        return patch.object(burnin, "selftest_status",
                            return_value={"running": False, "pct": 100,
                                          "result": result, "eta_min": None})

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

    def test_sas_bare_completed_is_pass(self):
        # SAS success is bare 'Completed' (no 'without error') — must NOT FAIL.
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=10000)
        with self._patch_status(result="Completed"):
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(reasons, [])

    def test_poh_no_warn_by_default(self):
        # POH warning is opt-in now (health.<type>.poh_warn defaults to None)
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=45000)
        with self._patch_status():
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "PASS")
        self.assertFalse(any("power-on" in r.lower() for r in reasons))

    def test_poh_warns_when_configured(self):
        d = _disk(health="PASSED", uncorr=0, realloc=0, poh=45000)   # is_ssd=True
        cfg = {"ssd": {"poh_warn": 40000}, "hdd": {"poh_warn": None}}
        with self._patch_status(), \
             patch("b2ctl.config.health_config", return_value=cfg):
            verdict, reasons = burnin.assess(d)
        self.assertEqual(verdict, "WARN")
        self.assertTrue(any("power-on" in r.lower() for r in reasons))

    def test_warn_on_grown_defects(self):
        d = _disk(health="PASSED", uncorr=0, realloc=3, poh=10000)
        with self._patch_status():
            verdict, _ = burnin.assess(d)
        self.assertEqual(verdict, "WARN")


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


class TestStartScan(unittest.TestCase):
    """badblocks spawned as a detached, read-only host process."""

    def test_read_only_argv_no_write_flag(self):
        fake = Mock(pid=4321)
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin.subprocess, "Popen", return_value=fake) as pop:
            pid, log = burnin.start_scan("/dev/sdb", "SER1")
        self.assertEqual(pid, 4321)
        cmd = pop.call_args[0][0]
        self.assertEqual(cmd[:4], ["badblocks", "-sv", "-b", "4096"])
        self.assertIn("/dev/sdb", cmd)
        self.assertNotIn("-w", cmd)              # never destructive
        # detached so Ctrl-C in the live view leaves it running
        self.assertTrue(pop.call_args[1].get("start_new_session"))
        self.assertIn("SER1", os.path.basename(log))

    def test_dry_run_starts_nothing(self):
        with patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin.subprocess, "Popen") as pop:
            pid, log = burnin.start_scan("/dev/sdb", "SER1", dry_run=True)
        self.assertIsNone(pid)
        self.assertEqual(log, "")
        pop.assert_not_called()


class TestScanProgress(unittest.TestCase):

    def test_parse_badblocks_log(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("Checking for bad blocks (read-only test):  "
                    "12.50% done, 0:03 elapsed. (0/0/0 errors) "
                    "55.00% done, 0:11 elapsed. (2/0/0 errors)")
            path = f.name
        try:
            pct, bad = burnin._parse_badblocks_log(path)
        finally:
            os.unlink(path)
        self.assertEqual(pct, 55)
        self.assertEqual(bad, 2)

    def test_scan_progress_none_when_no_pid(self):
        sp = burnin.scan_progress({"scan_pid": None, "scan_log": None})
        self.assertFalse(sp["running"])
        self.assertIsNone(sp["pct"])

    def test_scan_progress_eta_from_elapsed(self):
        # 50% done, 10 min elapsed -> ~10 min remaining.
        rec = {"scan_pid": 999, "scan_log": "/x", "started": 1000.0}
        with patch.object(burnin, "_pid_alive", return_value=True), \
             patch.object(burnin, "_parse_badblocks_log", return_value=(50, 0)), \
             patch.object(burnin, "_now", return_value=1000.0 + 600):
            sp = burnin.scan_progress(rec)
        self.assertTrue(sp["running"])
        self.assertEqual(sp["pct"], 50)
        self.assertEqual(sp["eta_min"], 10)


class TestPidAlive(unittest.TestCase):

    def test_self_is_alive(self):
        self.assertTrue(burnin._pid_alive(os.getpid()))

    def test_reaped_child_is_dead(self):
        import subprocess
        p = subprocess.Popen(["true"])
        p.wait()                                  # reap it -> gone
        self.assertFalse(burnin._pid_alive(p.pid))


class TestState(unittest.TestCase):

    def test_save_load_roundtrip_under_safety_dir(self):
        recs = [{"serial": "S1", "dev": "/dev/sdb", "bay": "1:4"}]
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp):
            burnin.save_state(recs)
            # lands beside the audit log, so sim's LOG_DIR patch redirects it
            self.assertTrue(os.path.exists(os.path.join(tmp, "burnin.json")))
            self.assertEqual(burnin.load_state(), recs)

    def test_load_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp):
            self.assertEqual(burnin.load_state(), [])


class TestSnapshot(unittest.TestCase):

    def test_done_when_neither_running(self):
        rec = {"dev": "/dev/sdb", "serial": "S1", "bay": "1:4", "do_scan": False}
        with patch.object(burnin, "selftest_status",
                          return_value={"running": False, "pct": 100,
                                        "result": "", "eta_min": None}):
            rows = burnin.burnin_snapshot([rec])
        self.assertTrue(rows[0]["done"])
        self.assertFalse(rows[0]["st_running"])

    def test_scan_row_populated(self):
        rec = {"dev": "/dev/sdb", "serial": "S1", "bay": "1:4",
               "do_scan": True, "scan_pid": 1, "scan_log": "/x", "started": 0.0}
        with patch.object(burnin, "selftest_status",
                          return_value={"running": True, "pct": 30,
                                        "result": "", "eta_min": 12}), \
             patch.object(burnin, "scan_progress",
                          return_value={"pct": 18, "eta_min": 40,
                                        "running": True, "bad": 0}):
            rows = burnin.burnin_snapshot([rec])
        self.assertFalse(rows[0]["done"])        # self-test still running
        self.assertEqual(rows[0]["st_pct"], 30)
        self.assertEqual(rows[0]["sc_pct"], 18)


class TestLiveView(unittest.TestCase):

    def test_calls_finish_when_all_done(self):
        running = {"done": False}
        done = {"done": True}
        with patch.object(burnin, "burnin_snapshot",
                          side_effect=[[running], [done]]), \
             patch.object(burnin, "_finish") as fin, \
             patch("b2ctl.ui.render_burnin_view", return_value="X"):
            burnin.live_view([{"dev": "/dev/sdb"}], sleep=Mock())
        fin.assert_called_once()

    def test_ctrl_c_detaches_and_saves_state(self):
        with patch.object(burnin, "burnin_snapshot", side_effect=KeyboardInterrupt), \
             patch.object(burnin, "save_state") as save:
            burnin.live_view([{"dev": "/dev/sdb"}], sleep=Mock())
        save.assert_called_once()                # left running, state persisted


class TestRunMulti(unittest.TestCase):

    def _free(self, **kw):
        return _disk(pool=None, vdev=None, vdev_state=None, **kw)

    def test_refuses_in_pool_member(self):
        d = _disk(dev="/dev/sdb", pool="tank", vdev="raidz1-0")
        with patch.object(burnin, "_resolve_targets", return_value=[d]), \
             patch.object(burnin, "start_selftest") as start, \
             patch.object(burnin, "live_view") as view:
            rc = burnin.run_multi([d])
        self.assertEqual(rc, 1)
        start.assert_not_called()
        view.assert_not_called()

    def test_starts_and_enters_view(self):
        d = self._free(dev="/dev/sdh", serial="NEW1")
        saved = {}
        with patch.object(burnin, "_resolve_targets", return_value=[d]), \
             patch.object(burnin, "load_state", return_value=[]), \
             patch.object(burnin, "selftest_status",
                          return_value={"running": False, "pct": 100,
                                        "result": "", "eta_min": None}), \
             patch.object(burnin, "start_selftest", return_value=(True, "")) as start, \
             patch.object(burnin, "save_state",
                          side_effect=lambda r: saved.setdefault("recs", r)), \
             patch.object(burnin, "live_view") as view:
            rc = burnin.run_multi([d])
        self.assertEqual(rc, 0)
        start.assert_called_once()
        view.assert_called_once()
        self.assertEqual(saved["recs"][0]["serial"], "NEW1")

    def test_does_not_restart_running_disk(self):
        d = self._free(dev="/dev/sdh", serial="RUN1")
        existing = [{"serial": "RUN1", "dev": "/dev/sdh", "do_scan": False}]
        with patch.object(burnin, "_resolve_targets", return_value=[d]), \
             patch.object(burnin, "load_state", return_value=existing), \
             patch.object(burnin, "selftest_status",
                          return_value={"running": True, "pct": 40,
                                        "result": "", "eta_min": 5}), \
             patch.object(burnin, "start_selftest") as start, \
             patch.object(burnin, "save_state"), \
             patch.object(burnin, "live_view") as view:
            rc = burnin.run_multi([d])
        self.assertEqual(rc, 0)
        start.assert_not_called()                # re-entrant: never restart
        view.assert_called_once()

    def test_dry_run_skips_view(self):
        d = self._free(dev="/dev/sdh", serial="NEW1")
        with patch.object(burnin, "_resolve_targets", return_value=[d]), \
             patch.object(burnin, "load_state", return_value=[]), \
             patch.object(burnin, "selftest_status",
                          return_value={"running": False, "pct": 100,
                                        "result": "", "eta_min": None}), \
             patch.object(burnin, "start_selftest", return_value=(True, "")) as start, \
             patch.object(burnin, "save_state") as save, \
             patch.object(burnin, "live_view") as view:
            rc = burnin.run_multi([d], dry_run=True)
        self.assertEqual(rc, 0)
        start.assert_called_once()               # start_selftest is dry-run aware
        self.assertEqual(start.call_args.kwargs.get("dry_run"), True)
        view.assert_not_called()
        save.assert_not_called()


class TestFinish(unittest.TestCase):

    def test_pass_verdict_prunes_state(self):
        rec = {"serial": "S1", "dev": "/dev/sdb", "bay": "1:4", "do_scan": False}
        kept = {}
        with patch("b2ctl.core.scan_one", return_value=_disk(serial="S1")), \
             patch("b2ctl.spec.load", return_value={}), \
             patch.object(burnin, "assess", return_value=("PASS", [])), \
             patch.object(burnin, "load_state", return_value=[rec]), \
             patch.object(burnin, "save_state",
                          side_effect=lambda r: kept.setdefault("recs", r)):
            burnin._finish([rec])
        self.assertEqual(kept["recs"], [])       # completed record removed

    def test_scan_bad_folds_pass_to_warn(self):
        rec = {"serial": "S1", "dev": "/dev/sdb", "bay": "1:4", "do_scan": True}
        printed = []
        with patch("b2ctl.core.scan_one", return_value=_disk(serial="S1")), \
             patch("b2ctl.spec.load", return_value={}), \
             patch.object(burnin, "assess", return_value=("PASS", [])), \
             patch.object(burnin, "scan_progress",
                          return_value={"pct": 100, "eta_min": None,
                                        "running": False, "bad": 3}), \
             patch.object(burnin, "load_state", return_value=[rec]), \
             patch.object(burnin, "save_state"), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            burnin._finish([rec])
        joined = " ".join(printed)
        self.assertIn("WARN", joined)
        self.assertIn("bad block", joined)


class TestStatusView(unittest.TestCase):

    def test_empty(self):
        with patch.object(burnin, "load_state", return_value=[]), \
             patch.object(burnin, "live_view") as view:
            rc = burnin.status_view()
        self.assertEqual(rc, 0)
        view.assert_not_called()

    def test_reattaches(self):
        recs = [{"serial": "S1", "dev": "/dev/sdb"}]
        with patch.object(burnin, "load_state", return_value=recs), \
             patch.object(burnin, "live_view") as view:
            rc = burnin.status_view()
        self.assertEqual(rc, 0)
        view.assert_called_once_with(recs)


class TestCancel(unittest.TestCase):
    """cancel: abort self-test (smartctl -X) + kill badblocks + drop from state."""

    def _rec(self, **kw):
        r = {"serial": "SER1", "dev": "/dev/sdb", "bay": "1:0", "dtype": "",
             "kind": "long", "do_scan": True, "scan_pid": 4321,
             "scan_log": "x.log", "started": 1.0}
        r.update(kw)
        return r

    def test_cancel_aborts_selftest_kills_scan_and_drops_state(self):
        seen = {}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch.object(burnin, "_pid_alive", return_value=True), \
             patch.object(burnin, "_is_our_badblocks", return_value=True), \
             patch.object(burnin.os, "kill") as mock_kill:
            burnin.save_state([self._rec()])
            rc = burnin.cancel(["1:0"])
            remaining = burnin.load_state()
        self.assertEqual(rc, 0)
        self.assertEqual(seen["cmd"], ["smartctl", "-X", "/dev/sdb"])
        mock_kill.assert_called_once_with(4321, burnin.signal.SIGTERM)
        self.assertEqual(remaining, [])

    def test_cancel_abort_argv_includes_megaraid_dtype(self):
        seen = {}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch.object(burnin, "_pid_alive", return_value=False):
            burnin.save_state([self._rec(dtype="megaraid,7", scan_pid=None, do_scan=False)])
            burnin.cancel(["SER1"])
        self.assertEqual(seen["cmd"],
                         ["smartctl", "-X", "-d", "megaraid,7", "/dev/sdb"])

    def test_cancel_dry_run_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin, "run_check") as mock_rc, \
             patch.object(burnin, "_pid_alive", return_value=True), \
             patch.object(burnin, "_is_our_badblocks", return_value=True), \
             patch.object(burnin.os, "kill") as mock_kill:
            burnin.save_state([self._rec()])
            rc = burnin.cancel(["1:0"], dry_run=True)
            remaining = burnin.load_state()
        self.assertEqual(rc, 0)
        mock_rc.assert_not_called()            # no smartctl -X in dry-run
        mock_kill.assert_not_called()          # no SIGTERM
        self.assertEqual(len(remaining), 1)    # state untouched

    def test_cancel_no_match_returns_1_state_intact(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch.object(burnin, "run_check") as mock_rc:
            burnin.save_state([self._rec()])
            rc = burnin.cancel(["9:9"])
            remaining = burnin.load_state()
        self.assertEqual(rc, 1)
        mock_rc.assert_not_called()
        self.assertEqual(len(remaining), 1)

    def test_cancel_all_clears_state(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp), \
             patch("b2ctl.config.tool", side_effect=lambda n: n), \
             patch.object(burnin, "run_check", return_value=(True, "")), \
             patch.object(burnin, "_pid_alive", return_value=False):
            burnin.save_state([self._rec(serial="A", dev="/dev/sda", scan_pid=None),
                               self._rec(serial="B", dev="/dev/sdb", scan_pid=None)])
            rc = burnin.cancel_all()
            remaining = burnin.load_state()
        self.assertEqual(rc, 0)
        self.assertEqual(remaining, [])

    def test_cancel_all_no_state_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch("b2ctl.safety.LOG_DIR", tmp):
            self.assertEqual(burnin.cancel_all(), 1)

    def test_is_our_badblocks_guards_pid_reuse(self):
        from unittest.mock import mock_open
        ours = b"badblocks\x00-sv\x00-b\x004096\x00/dev/sdb\x00"
        with patch("builtins.open", mock_open(read_data=ours)):
            self.assertTrue(burnin._is_our_badblocks(4321, "/dev/sdb"))
        with patch("builtins.open", mock_open(read_data=b"sleep\x00100\x00")):
            self.assertFalse(burnin._is_our_badblocks(4321, "/dev/sdb"))
        with patch("builtins.open", side_effect=OSError):
            self.assertFalse(burnin._is_our_badblocks(4321, "/dev/sdb"))


if __name__ == "__main__":
    unittest.main()
