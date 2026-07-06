"""Unit tests for b2ctl.raid_actions — guided PERC (perccli) workflows.

Covers the F-007 (rebuild disambiguation), F-016 (perccli uses the raw
controller enc:slot, not the display bay) fixes, plus the replace/offline
ordering and dry-run early return that previously had no test at all (F-088).
"""
import unittest
from unittest.mock import patch, MagicMock

import b2ctl.raid_actions as ra
import b2ctl.hba_raid as hba_raid
import b2ctl.common as common
from helpers import _disk


def _raid_backend():
    bk = MagicMock()
    bk.name = "raid"
    return bk


def _member(**kw):
    d = _disk(**kw)
    d.array_type = "HW"
    d.array_name = "vd0/raid1"
    d.pd_state = kw.get("pd_state", "Onln")
    return d


class TestPickMember(unittest.TestCase):
    """F-048: picking by the shared VD block device must not match an arbitrary
    member; only per-drive identifiers (bay/ctrl_slot/serial) select a member."""

    def _mk(self):
        a = _member(serial="SNA"); a.dev = "/dev/sda"; a.bay = "32:0"; a.ctrl_slot = "32:0"
        b = _member(serial="SNB"); b.dev = "/dev/sda"; b.bay = "32:1"; b.ctrl_slot = "32:1"
        return [a, b]

    def test_shared_dev_does_not_match(self):
        self.assertIsNone(ra._pick_member(self._mk(), "sda"))
        self.assertIsNone(ra._pick_member(self._mk(), "/dev/sda"))

    def test_bay_matches_exact_member(self):
        self.assertEqual(ra._pick_member(self._mk(), "32:1").serial, "SNB")

    def test_serial_matches_exact_member(self):
        self.assertEqual(ra._pick_member(self._mk(), "SNA").bay, "32:0")


class TestPdSelectorGuard(unittest.TestCase):
    def test_pd_rejects_display_bay_label(self):
        # F-016: a non-numeric bay label must never become a perccli selector.
        with self.assertRaises(ValueError):
            hba_raid._pd("PCIe2:0")
        with self.assertRaises(ValueError):
            hba_raid._pd("front-3")

    def test_pd_accepts_enc_slot(self):
        self.assertEqual(hba_raid._pd("32:1", 0), "/c0/e32/s1")


class TestRaidReplace(unittest.TestCase):

    def setUp(self):
        self.p = [
            patch("b2ctl.backend.get_backend", return_value=_raid_backend()),
            patch("b2ctl.raid_actions._confirm", return_value=True),
            patch("b2ctl.raid_actions._dry", return_value=False),
            patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"),
            patch("b2ctl.raid_actions.safety.end_op"),
            patch("builtins.input", return_value=""),
            patch("b2ctl.raid_actions.time.sleep"),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_replace_uses_ctrl_slot_not_display_bay(self):
        # F-016: display bay is remapped to "99:99" but the perccli action must
        # target the raw controller enc:slot "32:1".
        d = _member(bay="99:99", serial="M1")
        d.ctrl_slot = "32:1"
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")) as so, \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")), \
             patch("b2ctl.hba_raid.pd_state", return_value="Onln"), \
             patch("b2ctl.hba_raid.rebuild_progress", return_value={"pct": 100.0, "done": True}):
            ra.replace("99:99")
        so.assert_called_once_with("32:1", 0, dry_run=False)   # ctrl threaded (F-085)

    def test_replace_starts_rebuild_when_controller_idle(self):
        # F-007: PD reads 'Not in progress' but is UGood (never rebuilt) -> we
        # must issue start_rebuild, and only declare success once it is Onln.
        d = _member(bay="32:1", serial="M1")
        d.ctrl_slot = "32:1"
        # pd_state: first (pre-rebuild) UGood -> triggers start_rebuild; then Onln
        pd_states = iter(["UGood", "Onln"])
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")), \
             patch("b2ctl.hba_raid.pd_state", side_effect=lambda *a, **k: next(pd_states)), \
             patch("b2ctl.hba_raid.rebuild_progress", return_value={"pct": 100.0, "done": True}), \
             patch("b2ctl.hba_raid.start_rebuild", return_value=(True, "")) as sr:
            rc = ra.replace("32:1")
        sr.assert_called_once_with("32:1", 0)   # ctrl threaded (F-085)
        self.assertEqual(rc, 0)

    def test_replace_dry_run_no_rebuild(self):
        d = _member(bay="32:1", serial="M1")
        d.ctrl_slot = "32:1"
        with patch("b2ctl.raid_actions._dry", return_value=True), \
             patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")), \
             patch("b2ctl.hba_raid.start_rebuild") as sr:
            rc = ra.replace("32:1")
        sr.assert_not_called()
        self.assertEqual(rc, 0)

    def test_replace_refused_in_it_mode(self):
        it = MagicMock(); it.name = "it"
        with patch("b2ctl.backend.get_backend", return_value=it):
            rc = ra.replace("32:1")
        self.assertEqual(rc, 1)


class TestReplaceFlow(unittest.TestCase):
    """F-088/F-089/F-090: end-to-end ordering, audit fidelity and interrupt
    handling of the guided replace+rebuild workflow.

    _require_raid/_confirm are patched (RAID gate + [y/N] passed); time.sleep is
    patched so _wait_rebuild polls without real delay. dry-run state is reset in
    tearDown so common.set_dry_run(True) in one test can't leak into the next."""

    def setUp(self):
        self.p = [
            patch("b2ctl.raid_actions._require_raid", return_value=True),
            patch("b2ctl.raid_actions._confirm", return_value=True),
            patch("b2ctl.raid_actions.time.sleep"),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        common.set_dry_run(False)

    def _member_disk(self):
        d = _member(bay="32:1", serial="M1")
        d.ctrl_slot = "32:1"
        return d

    def test_offline_missing_rebuild_led_order(self):
        # F-088: the destructive sequence must fire in exactly this order —
        # offline -> missing -> LED on -> start rebuild -> LED off. pd_state
        # reads UGood first (so a rebuild is kicked off), then Onln (so
        # _wait_rebuild confirms completion and replace() returns 0).
        d = self._member_disk()
        order = []

        def _off(*a, **k):
            order.append("set_offline"); return (True, "")

        def _miss(*a, **k):
            order.append("set_missing"); return (True, "")

        def _reb(*a, **k):
            order.append("start_rebuild"); return (True, "")

        def _loc(cs, on, ctrl=0, **k):
            order.append("locate_on" if on else "locate_off"); return (True, "")

        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", side_effect=_off), \
             patch("b2ctl.hba_raid.set_missing", side_effect=_miss), \
             patch("b2ctl.hba_raid.start_rebuild", side_effect=_reb), \
             patch("b2ctl.hba_raid.locate", side_effect=_loc), \
             patch("b2ctl.hba_raid.pd_state", side_effect=iter(["UGood", "Onln"])), \
             patch("b2ctl.hba_raid.rebuild_progress",
                   return_value={"pct": 100.0, "done": True}), \
             patch("builtins.input", return_value=""), \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op"):
            rc = ra.replace("32:1")
        self.assertEqual(
            order,
            ["set_offline", "set_missing", "locate_on", "start_rebuild", "locate_off"])
        self.assertEqual(rc, 0)

    def test_set_missing_failure_ends_op_and_aborts(self):
        # F-088: set_missing failing short-circuits — the op is ended with
        # success=False, no rebuild is attempted, no LED is lit, and rc is 1.
        d = self._member_disk()
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(False, "device busy")), \
             patch("b2ctl.hba_raid.start_rebuild") as sr, \
             patch("b2ctl.hba_raid.locate") as loc, \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op") as eo, \
             patch("builtins.input", return_value=""):
            rc = ra.replace("32:1")
        sr.assert_not_called()
        loc.assert_not_called()          # never lit the LED on a failed prep
        self.assertEqual(rc, 1)
        self.assertFalse(eo.call_args.args[1])   # end_op(success=False)

    def test_dry_run_skips_rebuild(self):
        # F-088: under --dry-run the flow previews but never starts/waits on a
        # rebuild, and returns 0.
        common.set_dry_run(True)
        d = self._member_disk()
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")), \
             patch("b2ctl.hba_raid.start_rebuild") as sr, \
             patch("b2ctl.raid_actions._wait_rebuild") as wr, \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op"), \
             patch("builtins.input", return_value=""):
            rc = ra.replace("32:1")
        sr.assert_not_called()
        wr.assert_not_called()
        self.assertEqual(rc, 0)

    def test_replace_aborts_on_interrupt_at_insert_prompt(self):
        # F-090: Ctrl-C at the 'insert the new drive' prompt must NOT fall
        # through to the rebuild logic. It ends the op with success=False,
        # returns 1, and the finally block still turns the locate LED off.
        d = self._member_disk()
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")) as loc, \
             patch("b2ctl.hba_raid.start_rebuild") as sr, \
             patch("b2ctl.raid_actions._wait_rebuild") as wr, \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op") as eo, \
             patch("builtins.input", side_effect=KeyboardInterrupt):
            rc = ra.replace("32:1")
        sr.assert_not_called()
        wr.assert_not_called()
        self.assertEqual(rc, 1)
        self.assertFalse(eo.call_args.args[1])          # end_op(success=False)
        self.assertEqual(loc.call_count, 2)             # LED lit, then finally off
        self.assertTrue(loc.call_args_list[0].args[1])  # locate ON
        self.assertFalse(loc.call_args_list[1].args[1]) # locate OFF (finally)

    def test_audit_cmds_match_executed(self):
        # F-089: the cmds handed to safety.begin_op are built with the same
        # hba_raid.build_cmd()/_tool() the wrappers execute — so the audit trail
        # equals what actually ran (offline + missing), not a hand-written
        # 'perccli ...' literal.
        common.set_dry_run(True)
        d = self._member_disk()
        executed = []

        def _rc(cmd, **k):
            executed.append(cmd); return (True, "")

        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.run_check", side_effect=_rc), \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1") as bo, \
             patch("b2ctl.raid_actions.safety.end_op"), \
             patch("builtins.input", return_value=""):
            rc = ra.replace("32:1")
        self.assertEqual(rc, 0)
        cmds_logged = bo.call_args.args[6]              # begin_op(...,cmds,...)
        self.assertEqual(len(cmds_logged), 2)
        # the logged offline+missing cmds are byte-for-byte the first two
        # commands run_check actually executed
        self.assertEqual(cmds_logged, executed[:2])
        self.assertEqual(cmds_logged[0][1:], ["/c0/e32/s1", "set", "offline"])
        self.assertEqual(cmds_logged[1][1:], ["/c0/e32/s1", "set", "missing"])


class TestOffline(unittest.TestCase):
    """F-088: offline() marks the member offline+missing and only lights the
    locate LED once that prep actually succeeded."""

    def setUp(self):
        self.p = [
            patch("b2ctl.raid_actions._require_raid", return_value=True),
            patch("b2ctl.raid_actions._confirm", return_value=True),
        ]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        common.set_dry_run(False)

    def test_led_on_only_after_success(self):
        d = _member(bay="32:1", serial="M1")
        d.ctrl_slot = "32:1"
        # failure: set_missing fails -> LED must NOT be lit, rc 1
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(False, "busy")), \
             patch("b2ctl.hba_raid.locate") as loc, \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op"):
            rc = ra.offline("32:1")
        loc.assert_not_called()
        self.assertEqual(rc, 1)
        # success: LED lit with on=True, rc 0
        with patch("b2ctl.raid_actions.core.scan", return_value=[d]), \
             patch("b2ctl.hba_raid.set_offline", return_value=(True, "")), \
             patch("b2ctl.hba_raid.set_missing", return_value=(True, "")), \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")) as loc, \
             patch("b2ctl.raid_actions.safety.begin_op", return_value="op1"), \
             patch("b2ctl.raid_actions.safety.end_op"):
            rc = ra.offline("32:1")
        loc.assert_called_once()
        self.assertTrue(loc.call_args.args[1])   # locate ON
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
