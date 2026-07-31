"""Unit tests for b2ctl.schema — the wire-format projections (ADR-007).

b2ctl is driven by a service on the box that shells out to it and forwards results to a web UI now, so these lock down the
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
from b2ctl import jsonout
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


class TestPoolJsonRedundancyAndTimestamps(unittest.TestCase):
    """F-150c: `level` (redundancy type, e.g. mirror/raidz1) is a DIFFERENT
    concept from DISK_FIELDS' `level` (health verdict) but shares the wire
    key for renderer compat; `redundancy` carries the same value under an
    unambiguous name for new clients. `last_scrub_ts`/`last_trim_ts` are the
    raw ISO-8601 timestamps behind the human `last_scrub`/`last_trim`
    strings, so a machine client can sort/age without parsing prose."""

    def test_redundancy_mirrors_level_when_not_separately_supplied(self):
        p = {"name": "tank", "level": "raidz1"}
        wire = schema.pool_json(p)
        self.assertEqual(wire["level"], "raidz1")
        self.assertEqual(wire["redundancy"], "raidz1")

    def test_redundancy_is_none_when_level_is_missing(self):
        wire = schema.pool_json({"name": "tank"})
        self.assertIsNone(wire["level"])
        self.assertIsNone(wire["redundancy"])

    def test_schema_version_stays_1_for_the_additive_field(self):
        self.assertEqual(jsonout.SCHEMA_VERSION, 1)

    def test_ts_fields_round_trip_through_fromisoformat(self):
        import datetime
        ts = "2026-07-08T03:00:00"
        p = {"name": "tank", "last_scrub_ts": ts, "last_trim_ts": ts}
        wire = schema.pool_json(p)
        self.assertEqual(datetime.datetime.fromisoformat(wire["last_scrub_ts"]),
                          datetime.datetime.fromisoformat(ts))
        self.assertEqual(datetime.datetime.fromisoformat(wire["last_trim_ts"]),
                          datetime.datetime.fromisoformat(ts))

    def test_ts_fields_are_none_not_empty_string_when_absent(self):
        wire = schema.pool_json({"name": "tank"})
        self.assertIsNone(wire["last_scrub_ts"])
        self.assertIsNone(wire["last_trim_ts"])
        # And the human strings are untouched by this change.
        self.assertIsNone(wire["last_scrub"])
        self.assertIsNone(wire["last_trim"])


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


class TestPoolKnownOnTheWire(unittest.TestCase):
    """F-143: a client must be able to tell `pool: null` = free from
    `pool: null` = we could not ask. Without pool_known it will treat an
    unanswered zpool as an empty one — the exact mistake b2ctl itself made."""

    def test_pool_known_is_published(self):
        self.assertIn("pool_known", schema.DISK_FIELDS)
        d = Disk(dev="/dev/sda")
        self.assertIs(schema.disk_json(d)["pool_known"], True)

    def test_it_travels_next_to_pool(self):
        # Adjacent in the projection so anyone reading the field list sees the
        # qualifier right beside the thing it qualifies.
        fields = list(schema.DISK_FIELDS)
        self.assertEqual(fields[fields.index("pool") + 1], "pool_known")

    def test_unknown_membership_serialises_as_false(self):
        d = Disk(dev="/dev/sda")
        d.pool_known = False
        row = schema.disk_json(d)
        self.assertIs(row["pool_known"], False)
        self.assertIsNone(row["pool"])
        json.dumps(row)                       # still a plain JSON scalar

    def test_schema_version_did_not_bump_for_an_additive_field(self):
        # ADR-007: additive changes keep schema_version at 1.
        self.assertEqual(jsonout.SCHEMA_VERSION, 1)


class TestVersionDocDrift(unittest.TestCase):
    """CLAUDE.md is the project contract; a stale version line there sent six
    releases' worth of readers to the wrong baseline (§1 said v0.18.0 while
    _version.py said v0.24.0).

    CLAUDE.md is gitignored — it is the maintainer's working handover, not a
    shipped file — so this SKIPS when it is absent rather than failing a clean
    checkout. It still fires where it matters: the working tree where the
    version is actually bumped.
    """

    def test_claude_md_names_the_current_version(self):
        import os
        from b2ctl._version import __version__
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        path = os.path.join(root, "CLAUDE.md")
        if not os.path.exists(path):
            self.skipTest("CLAUDE.md not present (gitignored working doc)")
        with open(path) as f:
            text = f.read()
        self.assertIn(__version__, text,
                      f"CLAUDE.md does not mention {__version__} — bump §1")
