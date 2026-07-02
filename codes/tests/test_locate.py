"""Unit tests for b2ctl.locate — blink_disk backend routing."""
import unittest
from unittest.mock import patch

import b2ctl.locate as locate
from b2ctl.common import Disk


class TestBlinkDisk(unittest.TestCase):

    def test_hw_member_uses_perccli(self):
        d = Disk(dev="/dev/sda")
        d.array_type = "HW"
        d.bay = "32:0"
        with patch("b2ctl.hba_raid.locate", return_value=(True, "")) as loc, \
             patch("time.sleep"):
            ok, method = locate.blink_disk(d, seconds=5)
        assert ok and method == "perccli"
        # LED on then off, by enc:slot
        assert loc.call_args_list[0].args == ("32:0", True)
        assert loc.call_args_list[1].args == ("32:0", False)

    def test_ugood_perc_drive_uses_perccli(self):
        # A UGood spare (array_type="" but pd_state set + a bay) is still a PERC
        # PD sharing /dev/sda — must blink its slot LED via perccli, not dd.
        d = Disk(dev="/dev/sda")
        d.pd_state = "UGood"
        d.bay = "32:4"
        with patch("b2ctl.hba_raid.locate", return_value=(True, "")) as loc, \
             patch("time.sleep"):
            ok, method = locate.blink_disk(d, seconds=5)
        assert ok and method == "perccli"
        assert loc.call_args_list[0].args == ("32:4", True)
        assert loc.call_args_list[1].args == ("32:4", False)

    def test_direct_disk_uses_dd(self):
        d = Disk(dev="/dev/nvme0n1")            # array_type "" by default
        with patch("b2ctl.locate._have_ledctl", return_value=False), \
             patch("b2ctl.locate._dd_read") as dd:
            ok, method = locate.blink_disk(d, seconds=5)
        assert ok and method == "dd"
        dd.assert_called_once_with("/dev/nvme0n1", 5)

    def test_hw_without_bay_falls_back_to_dd(self):
        d = Disk(dev="/dev/sda")
        d.array_type = "HW"
        d.bay = None
        with patch("b2ctl.locate._have_ledctl", return_value=False), \
             patch("b2ctl.locate._dd_read") as dd:
            ok, method = locate.blink_disk(d, seconds=3)
        assert method == "dd"
        dd.assert_called_once_with("/dev/sda", 3)


class TestBlinkSteady(unittest.TestCase):
    """Steady blink (no pulse) — dd fallback path."""

    def test_blink_steady_calls_dd_read_once(self):
        with patch("b2ctl.locate._have_ledctl", return_value=False), \
             patch("b2ctl.locate._dd_read") as dd:
            ok, method = locate.blink("/dev/sdx", seconds=5)
        assert (ok, method) == (True, "dd")
        dd.assert_called_once_with("/dev/sdx", 5)


class TestLedctl(unittest.TestCase):
    """Raw-disk locate prefers ledctl, falls back to dd, always leaves LED off."""

    def test_steady_uses_ledctl_and_turns_off_after(self):
        with patch("b2ctl.locate._have_ledctl", return_value=True), \
             patch("b2ctl.locate._ledctl", return_value=(True, "")) as led, \
             patch("b2ctl.locate.time.sleep"):
            ok, method = locate.blink("/dev/sdx", seconds=5)
        assert (ok, method) == (True, "ledctl")
        seq = [c.args for c in led.call_args_list]
        assert seq[0] == ("/dev/sdx", True)     # lit
        assert seq[-1] == ("/dev/sdx", False)   # left off

    def test_falls_back_to_dd_when_ledctl_cannot_drive(self):
        with patch("b2ctl.locate._have_ledctl", return_value=True), \
             patch("b2ctl.locate._ledctl", return_value=(False, "")), \
             patch("b2ctl.locate._dd_read") as dd:
            ok, method = locate.blink("/dev/sdx", seconds=5)
        assert (ok, method) == (True, "dd")
        dd.assert_called_once_with("/dev/sdx", 5)

    def test_ledctl_led_left_off_even_if_blink_raises(self):
        with patch("b2ctl.locate._have_ledctl", return_value=True), \
             patch("b2ctl.locate._ledctl", return_value=(True, "")) as led, \
             patch("b2ctl.locate.time.sleep", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                locate.blink("/dev/sdx", seconds=4)
        # the finally must have turned the LED off
        assert ("/dev/sdx", False) == [c.args for c in led.call_args_list][-1]

    def test_perc_pd_never_uses_ledctl(self):
        # a PERC member must stay on perccli even if ledctl is installed
        d = Disk(dev="/dev/sda")
        d.array_type = "HW"
        d.bay = "32:4"
        with patch("b2ctl.locate._have_ledctl", return_value=True), \
             patch("b2ctl.locate._ledctl") as led, \
             patch("b2ctl.hba_raid.locate", return_value=(True, "")), \
             patch("time.sleep"):
            ok, method = locate.blink_disk(d, seconds=5)
        assert method == "perccli"
        led.assert_not_called()


if __name__ == "__main__":
    unittest.main()
