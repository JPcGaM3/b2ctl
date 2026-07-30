"""Unit tests for b2ctl.schema — the wire-format projections (ADR-007).

b2ctl is driven by an MCP server and a web UI now, so these lock down the
field lists schema.py exposes on the wire (DISK_FIELDS/POOL_FIELDS/
VOLUME_FIELDS) and the always-safe backend_json() probe, independent of the
envelope shape covered by test_jsonout.py.
"""
from __future__ import annotations

import dataclasses
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from b2ctl import schema
from b2ctl.common import Disk
from helpers import _disk


class TestDiskJson(unittest.TestCase):

    def test_returns_exactly_disk_fields(self):
        d = _disk()
        self.assertEqual(set(schema.disk_json(d)), set(schema.DISK_FIELDS))

    def test_excluded_fields_are_absent(self):
        # Deliberate ADR-007 exclusions, each for its own reason:
        #  pool_token                  - internal `zpool status -P` leaf token,
        #                                not a client concern
        #  selftest_running/_pct/_eta  - transient mid-scan progress, only
        #                                meaningful while a test is running
        #  spare_replacing             - derivable, and its shape is not settled
        #  smart_dtype/ctrl/lba_written - internal implementation detail, never
        #                                requested for the wire contract
        d = _disk()
        wire = schema.disk_json(d)
        for name in ("pool_token", "selftest_running", "selftest_pct",
                     "selftest_eta", "spare_replacing", "smart_dtype", "ctrl",
                     "lba_written"):
            with self.subTest(field=name):
                self.assertNotIn(name, wire)

    def test_disk_fields_all_exist_on_the_dataclass(self):
        """The guard that matters most: DISK_FIELDS is a hand-picked subset of
        Disk's real attributes. A rename/typo in DISK_FIELDS would getattr() a
        nonexistent name and blow up on the next scan; a new Disk field must
        NOT silently change the wire format just by being added. Assert every
        exposed name is real, while Disk is free to carry extra unexposed
        fields (e.g. the exclusions above)."""
        real_fields = {f.name for f in dataclasses.fields(Disk)}
        for name in schema.DISK_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, real_fields)
        # DISK_FIELDS is a proper subset, not the full field set — Disk keeps
        # internal-only fields off the wire on purpose.
        self.assertTrue(set(schema.DISK_FIELDS) < real_fields)

    def test_none_stays_none(self):
        d = _disk(bay=None)
        self.assertIsNone(schema.disk_json(d)["bay"])

    def test_empty_string_stays_empty_string(self):
        d = _disk(by_id="")
        self.assertEqual(schema.disk_json(d)["by_id"], "")

    def test_values_pass_through_unchanged(self):
        d = _disk(dev="/dev/sdz", poh=12345, level="WARNING",
                   reasons=["endurance left 25.0%"])
        wire = schema.disk_json(d)
        self.assertEqual(wire["dev"], "/dev/sdz")
        self.assertEqual(wire["poh"], 12345)
        self.assertEqual(wire["level"], "WARNING")
        self.assertEqual(wire["reasons"], ["endurance left 25.0%"])

    def test_json_serialisable_fully_populated_disk(self):
        d = _disk()
        json.dumps(schema.disk_json(d), default=str)   # must not raise

    def test_json_serialisable_bare_disk(self):
        d = Disk(dev="-")
        json.dumps(schema.disk_json(d), default=str)   # must not raise


class TestPoolJson(unittest.TestCase):

    def test_returns_exactly_pool_fields(self):
        p = {f: f"v-{f}" for f in schema.POOL_FIELDS}
        self.assertEqual(set(schema.pool_json(p)), set(schema.POOL_FIELDS))

    def test_missing_keys_degrade_to_none_not_keyerror(self):
        p = {"name": "tank", "health": "ONLINE"}
        wire = schema.pool_json(p)
        self.assertEqual(wire["name"], "tank")
        self.assertEqual(wire["health"], "ONLINE")
        for f in schema.POOL_FIELDS:
            if f not in p:
                with self.subTest(field=f):
                    self.assertIsNone(wire[f])

    def test_extra_input_keys_are_dropped(self):
        p = {f: f"v-{f}" for f in schema.POOL_FIELDS}
        p["not_a_wire_field"] = "should be dropped"
        self.assertNotIn("not_a_wire_field", schema.pool_json(p))


class TestVolumeJson(unittest.TestCase):

    def test_returns_exactly_volume_fields(self):
        v = {f: f"v-{f}" for f in schema.VOLUME_FIELDS}
        self.assertEqual(set(schema.volume_json(v)), set(schema.VOLUME_FIELDS))

    def test_missing_keys_degrade_to_none_not_keyerror(self):
        v = {"vd": "vd0", "raid": "raid1"}
        wire = schema.volume_json(v)
        self.assertEqual(wire["vd"], "vd0")
        self.assertEqual(wire["raid"], "raid1")
        for f in schema.VOLUME_FIELDS:
            if f not in v:
                with self.subTest(field=f):
                    self.assertIsNone(wire[f])

    def test_extra_input_keys_are_dropped(self):
        v = {f: f"v-{f}" for f in schema.VOLUME_FIELDS}
        v["not_a_wire_field"] = "should be dropped"
        self.assertNotIn("not_a_wire_field", schema.volume_json(v))


class TestBackendJson(unittest.TestCase):
    """backend_json() must report which backend/verbs are active without ever
    blocking or raising — it sits on the read path (ADR-007)."""

    def test_returns_the_five_documented_keys(self):
        fake_bk = SimpleNamespace(name="it", bay_source="sas2ircu")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="auto"), \
             patch("b2ctl.config.tool", return_value="/usr/sbin/sas2ircu"), \
             patch("b2ctl.hba_raid.have_tool", return_value=False):
            result = schema.backend_json()
        self.assertEqual(set(result), {"name", "mode", "bay_source", "tool",
                                        "personality"})

    def test_it_mode_native_bay_source_resolves_sas2ircu(self):
        fake_bk = SimpleNamespace(name="it", bay_source="sas2ircu")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="it"), \
             patch("b2ctl.config.tool",
                   side_effect=lambda n: f"/usr/sbin/{n}") as mock_tool, \
             patch("b2ctl.hba_raid.have_tool", return_value=False):
            result = schema.backend_json()
        mock_tool.assert_called_once_with("sas2ircu")
        self.assertEqual(result["tool"], "/usr/sbin/sas2ircu")

    def test_it_mode_perccli_bay_source_resolves_perccli(self):
        # An HBA330 ITBackend that fell back to perccli for bays (F-133) is
        # still backed by the perccli binary, not sas2ircu.
        fake_bk = SimpleNamespace(name="it", bay_source="perccli")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="it"), \
             patch("b2ctl.config.tool",
                   side_effect=lambda n: f"/usr/sbin/{n}") as mock_tool, \
             patch("b2ctl.hba_raid.have_tool", return_value=True), \
             patch("b2ctl.hba_raid._personality", return_value="HBA-Mode"):
            result = schema.backend_json()
        mock_tool.assert_called_once_with("perccli")
        self.assertEqual(result["tool"], "/usr/sbin/perccli")
        self.assertEqual(result["personality"], "HBA-Mode")

    def test_raid_mode_resolves_perccli_and_has_no_bay_source(self):
        fake_bk = SimpleNamespace(name="raid")   # RaidBackend has no bay_source
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="raid"), \
             patch("b2ctl.config.tool",
                   side_effect=lambda n: f"/usr/sbin/{n}") as mock_tool, \
             patch("b2ctl.hba_raid.have_tool", return_value=False):
            result = schema.backend_json()
        mock_tool.assert_called_once_with("perccli")
        self.assertIsNone(result["bay_source"])

    def test_personality_only_probed_when_perccli_present(self):
        fake_bk = SimpleNamespace(name="raid")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="raid"), \
             patch("b2ctl.config.tool", return_value="/usr/sbin/perccli"), \
             patch("b2ctl.hba_raid.have_tool", return_value=False), \
             patch("b2ctl.hba_raid._personality") as mock_personality:
            result = schema.backend_json()
        mock_personality.assert_not_called()
        self.assertEqual(result["personality"], "")

    def test_never_raises_when_get_backend_raises_systemexit(self):
        """common.die() (called when no HBA/RAID tool exists at all) raises
        SystemExit, which is NOT an Exception subclass — a bare `except
        Exception` around get_backend() would let it propagate and crash a
        read-only wire-format call. This is the exact trap ADR-007 guards
        against with `except (Exception, SystemExit)`."""
        with patch("b2ctl.backend.get_backend", side_effect=SystemExit(1)), \
             patch("b2ctl.config.controller_mode", return_value="auto"), \
             patch("b2ctl.hba_raid.have_tool", return_value=False):
            result = schema.backend_json()   # must not raise / exit
        self.assertIsNone(result["name"])
        self.assertIsNone(result["bay_source"])
        self.assertIsNone(result["tool"])

    def test_never_raises_when_controller_mode_raises(self):
        fake_bk = SimpleNamespace(name="it", bay_source="sas2ircu")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode",
                   side_effect=RuntimeError("boom")), \
             patch("b2ctl.config.tool", return_value="/usr/sbin/sas2ircu"), \
             patch("b2ctl.hba_raid.have_tool", return_value=False):
            result = schema.backend_json()   # must not raise
        self.assertIsNone(result["mode"])
        self.assertEqual(result["name"], "it")   # sibling probe unaffected

    def test_never_raises_when_have_tool_raises(self):
        fake_bk = SimpleNamespace(name="raid")
        with patch("b2ctl.backend.get_backend", return_value=fake_bk), \
             patch("b2ctl.config.controller_mode", return_value="raid"), \
             patch("b2ctl.config.tool", return_value="/usr/sbin/perccli"), \
             patch("b2ctl.hba_raid.have_tool",
                   side_effect=RuntimeError("boom")):
            result = schema.backend_json()   # must not raise
        self.assertEqual(result["personality"], "")


if __name__ == "__main__":
    unittest.main()
