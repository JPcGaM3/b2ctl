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


if __name__ == "__main__":
    unittest.main()
