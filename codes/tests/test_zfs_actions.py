"""Unit tests for b2ctl.zfs_actions — the public CLI<->workflow contract (F-070).

zfs_actions wraps watch's underscore-private _cmd_* handlers, mapping their new
bool return to a process exit code (0 = ok, 1 = cancel/fail) so scripts and cron
can detect a failed offload/replace/etc. When no tbw table is supplied the
wrapper loads it via spec.load().
"""
import unittest
from unittest.mock import patch

from b2ctl import zfs_actions


class TestZfsActionsExitCodes(unittest.TestCase):
    """Each wrapper returns 0 when the workflow succeeds, 1 when it fails."""

    def test_simple_wrappers_map_bool_to_exit_code(self):
        for name in ("offload", "replace", "swap", "demote"):
            with self.subTest(action=name):
                with patch("b2ctl.zfs_actions.spec.load", return_value={}), \
                     patch(f"b2ctl.zfs_actions.watch._cmd_{name}",
                           return_value=True):
                    self.assertEqual(getattr(zfs_actions, name)(), 0)
                with patch("b2ctl.zfs_actions.spec.load", return_value={}), \
                     patch(f"b2ctl.zfs_actions.watch._cmd_{name}",
                           return_value=False):
                    self.assertEqual(getattr(zfs_actions, name)(), 1)

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_create", return_value=True)
    def test_create_success_returns_0(self, mock_cmd, mock_load):
        self.assertEqual(zfs_actions.create(), 0)

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_destroy", return_value=False)
    def test_destroy_failure_returns_1(self, mock_cmd, mock_load):
        self.assertEqual(zfs_actions.destroy(), 1)


class TestZfsActionsSpecLoad(unittest.TestCase):
    """spec.load() is used to supply the TBW table only when tbw is not given."""

    @patch("b2ctl.zfs_actions.spec.load", return_value={"tbw": 1.0})
    @patch("b2ctl.zfs_actions.watch._cmd_offload", return_value=True)
    def test_loads_spec_when_no_tbw(self, mock_cmd, mock_load):
        zfs_actions.offload()
        mock_load.assert_called_once_with()
        mock_cmd.assert_called_once_with({"tbw": 1.0})     # loaded table forwarded

    @patch("b2ctl.zfs_actions.spec.load")
    @patch("b2ctl.zfs_actions.watch._cmd_offload", return_value=True)
    def test_uses_given_tbw_without_loading(self, mock_cmd, mock_load):
        table = {"given": 2.0}
        zfs_actions.offload(table)
        mock_load.assert_not_called()
        mock_cmd.assert_called_once_with(table)


class TestZfsActionsArgForwarding(unittest.TestCase):
    """create(raid10=...) and destroy(pool=...) thread their args through."""

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_create", return_value=True)
    def test_create_raid10_passes_raid_type(self, mock_cmd, mock_load):
        self.assertEqual(zfs_actions.create(raid10=True), 0)
        mock_cmd.assert_called_once_with({}, raid_type="raid10")

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_create", return_value=True)
    def test_create_default_raid_type_none(self, mock_cmd, mock_load):
        zfs_actions.create()
        mock_cmd.assert_called_once_with({}, raid_type=None)

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_destroy", return_value=True)
    def test_destroy_passes_target(self, mock_cmd, mock_load):
        self.assertEqual(zfs_actions.destroy(pool="tank"), 0)
        mock_cmd.assert_called_once_with({}, target="tank")


class TestZfsActionsAuxReplace(unittest.TestCase):
    """cache_replace / log_replace: find the aux leaf, guard its class, delegate
    to watch._repair_aux, map its bool to an exit code."""

    def _leaf(self, klass):
        return {"token": "/dev/disk/by-id/ata-OLD", "klass": klass,
                "vdev": "mirror-1" if klass == "log" else "cache",
                "top_vdev": "logs" if klass == "log" else "cache",
                "state": "FAULTED", "mirror_leg": klass == "log", "degraded": True}

    @patch("b2ctl.zfs_actions.watch._repair_aux", return_value=True)
    @patch("b2ctl.zfs.aux_leaves")
    def test_log_replace_success_forwards_new_token(self, mock_leaves, mock_repair):
        mock_leaves.return_value = [self._leaf("log")]
        rc = zfs_actions.log_replace("tank", "/dev/disk/by-id/ata-OLD",
                                     "/dev/disk/by-id/ata-NEW")
        self.assertEqual(rc, 0)
        _, kw = mock_repair.call_args
        self.assertEqual(kw["new_token"], "/dev/disk/by-id/ata-NEW")

    @patch("b2ctl.zfs_actions.watch._repair_aux", return_value=True)
    @patch("b2ctl.zfs.aux_leaves")
    def test_cache_replace_success(self, mock_leaves, mock_repair):
        mock_leaves.return_value = [self._leaf("cache")]
        self.assertEqual(
            zfs_actions.cache_replace("tank", "/dev/disk/by-id/ata-OLD", "new"), 0)

    @patch("b2ctl.zfs_actions.watch._repair_aux")
    @patch("b2ctl.zfs.aux_leaves", return_value=[])
    def test_missing_leaf_returns_1(self, mock_leaves, mock_repair):
        self.assertEqual(zfs_actions.log_replace("tank", "nope", "new"), 1)
        mock_repair.assert_not_called()

    @patch("b2ctl.zfs_actions.watch._repair_aux")
    @patch("b2ctl.zfs.aux_leaves")
    def test_class_mismatch_returns_1(self, mock_leaves, mock_repair):
        # log_replace pointed at a CACHE token must refuse, not repair
        mock_leaves.return_value = [self._leaf("cache")]
        self.assertEqual(
            zfs_actions.log_replace("tank", "/dev/disk/by-id/ata-OLD", "new"), 1)
        mock_repair.assert_not_called()

    @patch("b2ctl.zfs_actions.watch._repair_aux", return_value=False)
    @patch("b2ctl.zfs.aux_leaves")
    def test_repair_failure_returns_1(self, mock_leaves, mock_repair):
        mock_leaves.return_value = [self._leaf("log")]
        self.assertEqual(
            zfs_actions.log_replace("tank", "/dev/disk/by-id/ata-OLD", "new"), 1)

    # --- ambiguous / wrong-target guards (review #1/#6) -------------------- #

    def _leg(self, tok, *, degraded):
        return {"token": tok, "klass": "log", "vdev": "mirror-1",
                "top_vdev": "logs", "state": "FAULTED" if degraded else "ONLINE",
                "mirror_leg": True, "degraded": degraded}

    @patch("b2ctl.zfs_actions.watch._repair_aux", return_value=True)
    @patch("b2ctl.zfs.aux_leaves")
    def test_degraded_leg_preferred_over_healthy(self, mock_leaves, mock_repair):
        # a substring token that matches BOTH legs must target the FAULTED one,
        # never the surviving healthy leg (a destructive replace).
        healthy = self._leg("/dev/disk/by-id/ata-LOGA", degraded=False)
        dead = self._leg("/dev/disk/by-id/ata-LOGB", degraded=True)
        mock_leaves.return_value = [healthy, dead]
        rc = zfs_actions.log_replace("tank", "ata-LOG", "new")   # matches both
        self.assertEqual(rc, 0)
        self.assertEqual(mock_repair.call_args.args[1]["token"],
                         "/dev/disk/by-id/ata-LOGB")

    @patch("b2ctl.zfs_actions.watch._repair_aux")
    @patch("b2ctl.zfs.aux_leaves")
    def test_ambiguous_match_refused(self, mock_leaves, mock_repair):
        # two degraded legs both match -> refuse, don't guess a destructive target
        mock_leaves.return_value = [self._leg("/dev/disk/by-id/ata-LOGA", degraded=True),
                                    self._leg("/dev/disk/by-id/ata-LOGB", degraded=True)]
        self.assertEqual(zfs_actions.log_replace("tank", "ata-LOG", "new"), 1)
        mock_repair.assert_not_called()

    @patch("b2ctl.zfs_actions.watch._repair_aux", return_value=True)
    @patch("b2ctl.zfs.aux_leaves")
    def test_exact_token_preferred_over_substring(self, mock_leaves, mock_repair):
        # 'old' is the exact token of one leaf AND a substring of another
        exact = self._leg("/dev/disk/by-id/ata-LOG", degraded=True)
        other = self._leg("/dev/disk/by-id/ata-LOG2", degraded=False)
        mock_leaves.return_value = [exact, other]
        rc = zfs_actions.log_replace("tank", "/dev/disk/by-id/ata-LOG", "new")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_repair.call_args.args[1]["token"],
                         "/dev/disk/by-id/ata-LOG")


class TestZfsActionsScrubTrim(unittest.TestCase):
    """scrub/trim wrappers: exit-code mapping + action/pool forwarding to _cmd_maint."""

    def test_scrub_trim_exit_codes(self):
        for name, action in (("scrub", "scrub"), ("trim", "trim")):
            with self.subTest(action=name):
                with patch("b2ctl.zfs_actions.spec.load", return_value={}), \
                     patch("b2ctl.zfs_actions.watch._cmd_maint", return_value=True):
                    self.assertEqual(getattr(zfs_actions, name)("tank"), 0)
                with patch("b2ctl.zfs_actions.spec.load", return_value={}), \
                     patch("b2ctl.zfs_actions.watch._cmd_maint", return_value=False):
                    self.assertEqual(getattr(zfs_actions, name)("tank"), 1)

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_maint", return_value=True)
    def test_scrub_forwards_action_and_pool(self, mock_cmd, _load):
        zfs_actions.scrub("tank")
        mock_cmd.assert_called_once_with({}, action="scrub", pool="tank")

    @patch("b2ctl.zfs_actions.spec.load", return_value={})
    @patch("b2ctl.zfs_actions.watch._cmd_maint", return_value=True)
    def test_trim_forwards_action_and_pool(self, mock_cmd, _load):
        zfs_actions.trim("tank")
        mock_cmd.assert_called_once_with({}, action="trim", pool="tank")


if __name__ == "__main__":
    unittest.main()
