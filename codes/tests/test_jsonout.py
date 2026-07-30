"""Unit tests for b2ctl.jsonout — the machine contract's envelope (ADR-007).

b2ctl is driven by an MCP server and a web UI, so these assert the shape a client
binds to: identical keys whether the command succeeded or failed, a version to
negotiate on, and errors as data rather than prose on stderr.
"""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from b2ctl import jsonout
from b2ctl import common


def _capture(fn):
    """Run fn() with stdout captured; return (rc, parsed_json, raw_text)."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        rc = fn()
    raw = buf.getvalue()
    return rc, json.loads(raw), raw


class TestEnvelopeShape(unittest.TestCase):

    def setUp(self):
        common.take_warnings()          # drain anything a previous test left

    def test_success_shape_and_exit_code(self):
        rc, out, _ = _capture(lambda: jsonout.emit("status", {"disks": []}))
        self.assertEqual(rc, 0)
        self.assertEqual(out["schema_version"], jsonout.SCHEMA_VERSION)
        self.assertIs(out["ok"], True)
        self.assertEqual(out["command"], "status")
        self.assertEqual(out["data"], {"disks": []})
        self.assertIsNone(out["error"])
        self.assertEqual(out["warnings"], [])

    def test_failure_shape_and_exit_code(self):
        rc, out, _ = _capture(
            lambda: jsonout.fail("destroy", jsonout.ERR_POOL_NOT_FOUND,
                                 "no pool named tonk"))
        self.assertEqual(rc, 1)
        self.assertIs(out["ok"], False)
        self.assertIsNone(out["data"])
        self.assertEqual(out["error"]["code"], "POOL_NOT_FOUND")
        self.assertIn("tonk", out["error"]["message"])

    def test_both_outcomes_carry_identical_keys(self):
        """A client parses once and branches on `ok` — the key set must not
        depend on the outcome."""
        _, ok_out, _ = _capture(lambda: jsonout.emit("status", {}))
        _, err_out, _ = _capture(
            lambda: jsonout.fail("status", jsonout.ERR_NO_BACKEND, "none"))
        self.assertEqual(set(ok_out), set(err_out))

    def test_failure_may_still_carry_partial_data(self):
        rc, out, _ = _capture(
            lambda: jsonout.fail("disks", jsonout.ERR_TOOL_MISSING,
                                 "sas2ircu absent", data={"disks": [1]}))
        self.assertEqual(rc, 1)
        self.assertEqual(out["data"], {"disks": [1]})

    def test_output_is_the_only_thing_on_stdout(self):
        """Anything else printed in JSON mode corrupts the stream (ADR-007)."""
        _, _, raw = _capture(lambda: jsonout.emit("version", {"version": "x"}))
        self.assertEqual(raw.strip()[0], "{")
        self.assertEqual(raw.strip()[-1], "}")
        json.loads(raw)                 # parses whole, nothing appended


class TestWarningsAreDrained(unittest.TestCase):

    def setUp(self):
        common.set_json_mode(True)
        common.take_warnings()

    def tearDown(self):
        common.set_json_mode(False)
        common.take_warnings()

    def test_pending_warnings_land_in_the_envelope(self):
        common.warn("bay_map.json unreadable")
        _, out, _ = _capture(lambda: jsonout.emit("status", {}))
        self.assertEqual(out["warnings"], ["bay_map.json unreadable"])

    def test_warnings_do_not_leak_into_the_next_command(self):
        common.warn("first")
        _capture(lambda: jsonout.emit("status", {}))
        _, out, _ = _capture(lambda: jsonout.emit("pools", {}))
        self.assertEqual(out["warnings"], [])

    def test_explicit_warnings_are_appended_to_collected_ones(self):
        common.warn("collected")
        _, out, _ = _capture(
            lambda: jsonout.emit("status", {}, warnings=["explicit"]))
        self.assertEqual(out["warnings"], ["collected", "explicit"])

    def test_failure_also_drains_warnings(self):
        common.warn("noted")
        _, out, _ = _capture(
            lambda: jsonout.fail("status", jsonout.ERR_NO_BACKEND, "none"))
        self.assertEqual(out["warnings"], ["noted"])


class TestErrorCodes(unittest.TestCase):

    def test_codes_are_stable_strings(self):
        """Clients branch on these; they are part of the contract, not prose."""
        for name, value in (
                ("ERR_NO_BACKEND", "NO_BACKEND"),
                ("ERR_TOOL_MISSING", "TOOL_MISSING"),
                ("ERR_POOL_NOT_FOUND", "POOL_NOT_FOUND"),
                ("ERR_DISK_NOT_FOUND", "DISK_NOT_FOUND"),
                ("ERR_NEEDS_ROOT", "NEEDS_ROOT"),
                ("ERR_INVALID_ARG", "INVALID_ARG"),
                ("ERR_PARSE_ERROR", "PARSE_ERROR"),
                ("ERR_UNSUPPORTED", "UNSUPPORTED")):
            with self.subTest(name=name):
                self.assertEqual(getattr(jsonout, name), value)


if __name__ == "__main__":
    unittest.main()
