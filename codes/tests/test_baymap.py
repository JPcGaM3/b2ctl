"""Unit tests for b2ctl.baymap — panel-based bay_map.json parsing + remap."""
import unittest
from unittest.mock import mock_open, patch

import b2ctl.baymap as baymap
from b2ctl.common import Disk

_FRONT = {"panel": "front", "type": "sas", "reverse_slots": True,
          "slots_per_enclosure": 8, "map": {}}
_BACK = {"panel": "back", "type": "nvme",
         "map": [{"bdf": "d8:00.0", "bay": "PCIe2:0"},
                 {"bdf": "d9:00.0", "bay": "PCIe2:1"}]}


class TestRemapSlot(unittest.TestCase):

    def test_reverse_slots(self):
        p = [_FRONT]
        self.assertEqual(baymap.remap_slot("32:0", p), "32:7")
        self.assertEqual(baymap.remap_slot("32:3", p), "32:4")

    def test_explicit_map_override(self):
        p = [{"panel": "front", "type": "sas", "map": {"32:0": "A1"}}]
        self.assertEqual(baymap.remap_slot("32:0", p), "A1")

    def test_identity_when_no_panel(self):
        self.assertEqual(baymap.remap_slot("32:0", []), "32:0")


class TestRemapNvme(unittest.TestCase):

    def test_maps_bdf_to_bay(self):
        self.assertEqual(baymap.remap_nvme("d8:00.0", [_BACK]), "PCIe2:0")
        self.assertEqual(baymap.remap_nvme("d9:00.0", [_BACK]), "PCIe2:1")

    def test_identity_when_unmapped(self):
        self.assertEqual(baymap.remap_nvme("da:00.0", [_BACK]), "da:00.0")
        self.assertEqual(baymap.remap_nvme("d8:00.0", []), "d8:00.0")

    def test_maps_by_id_substring(self):
        panel = {"panel": "back", "type": "nvme",
                 "map": [{"by-id": "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX", "bay": "PCIe2:0"}]}
        full = "/dev/disk/by-id/nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345"
        self.assertEqual(baymap.remap_nvme("", [panel], by_id=full), "PCIe2:0")

    def test_maps_by_serial(self):
        panel = {"panel": "back", "type": "nvme",
                 "map": [{"serial": "S7XXNS0W123", "bay": "PCIe2:5"}]}
        self.assertEqual(baymap.remap_nvme("", [panel], serial="S7XXNS0W123"), "PCIe2:5")

    def test_precedence_by_id_over_bdf(self):
        # a by-id entry matches this drive; a later bdf entry also would —
        # the by-id entry (listed first, higher precedence) wins.
        panel = {"panel": "back", "type": "nvme",
                 "map": [{"by-id": "nvme-Model_SERA", "bay": "BY-ID-BAY"},
                         {"bdf": "d8:00.0", "bay": "BDF-BAY"}]}
        bay = baymap.remap_nvme("d8:00.0", [panel],
                                by_id="/dev/disk/by-id/nvme-Model_SERA-1")
        self.assertEqual(bay, "BY-ID-BAY")

    def test_bdf_still_matches_when_no_by_id(self):
        panel = {"panel": "back", "type": "nvme",
                 "map": [{"bdf": "d8:00.0", "bay": "BDF-BAY"}]}
        self.assertEqual(baymap.remap_nvme("d8:00.0", [panel], serial="X"), "BDF-BAY")


class _Stat:
    def __init__(self, mtime): self.st_mtime_ns = mtime


class TestLoad(unittest.TestCase):

    def setUp(self):
        baymap._cache = None

    def test_old_dict_format_ignored(self):
        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.stat", return_value=_Stat(1)), \
             patch("builtins.open", mock_open(read_data='{"reverse_slots": true}')):
            assert baymap.load() == []

    def test_panel_list_loads(self):
        import json
        data = json.dumps([_FRONT, _BACK])
        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.stat", return_value=_Stat(1)), \
             patch("builtins.open", mock_open(read_data=data)):
            panels = baymap.load()
        assert isinstance(panels, list) and len(panels) == 2

    def test_missing_file(self):
        with patch("b2ctl.config.bay_map_path", return_value="/x/none.json"), \
             patch("b2ctl.baymap.os.stat", side_effect=OSError):
            assert baymap.load() == []

    def test_malformed_json_warns_and_returns_empty(self):
        # F-028/F-029: a truncated/corrupt file must not raise on the read path.
        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.stat", return_value=_Stat(1)), \
             patch("builtins.open", mock_open(read_data='[{"panel": "front"')):
            assert baymap.load() == []


class TestLoadCache(unittest.TestCase):
    """F-028: parsed panels cached until the file's mtime changes."""

    def setUp(self):
        baymap._cache = None

    def test_cached_until_mtime_changes(self):
        import json
        data = json.dumps([_FRONT])
        opened = {"n": 0}

        def _open(*a, **k):
            opened["n"] += 1
            return mock_open(read_data=data)(*a, **k)

        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.stat", return_value=_Stat(100)), \
             patch("builtins.open", _open):
            baymap.load()
            baymap.load()
        assert opened["n"] == 1                # second load hit the cache

        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.stat", return_value=_Stat(200)), \
             patch("builtins.open", _open):
            baymap.load()
        assert opened["n"] == 2                # mtime changed -> re-read


class TestAssignBays(unittest.TestCase):
    """F-084: the shared serial-match-then-remap loop (assign_bays).

    Exact serial hit wins; else a fuzzy prefix match via serial_match; the
    matched enc:slot is remapped via remap_slot. Only d.bay is touched."""

    def _d(self, serial):
        return Disk(dev="/dev/sda", serial=serial)

    def test_assign_bays_exact_match(self):
        d = self._d("S74ZNS0W111")
        baymap.assign_bays([d], {"S74ZNS0W111": "32:3"}, [])   # [] -> identity remap
        self.assertEqual(d.bay, "32:3")

    def test_assign_bays_prefix_fallback(self):
        # A disk serial that is a truncation OR an extension of a bm key still
        # matches via the fuzzy bidirectional-prefix rule.
        d_trunc = self._d("S74ZNS0W")               # bm key is longer (truncated)
        d_ext = self._d("S4F2NY0M105699EXTRA")      # bm key is a prefix (extended)
        bm = {"S74ZNS0W582303N": "32:0", "S4F2NY0M105699": "32:1"}
        baymap.assign_bays([d_trunc, d_ext], bm, [])
        self.assertEqual(d_trunc.bay, "32:0")
        self.assertEqual(d_ext.bay, "32:1")

    def test_assign_bays_exact_preferred_over_fuzzy(self):
        # Both keys would fuzzy-prefix-match, but the exact hit must win.
        d = self._d("S1234")
        baymap.assign_bays([d], {"S1234": "EXACT", "S12": "FUZZY"}, [])
        self.assertEqual(d.bay, "EXACT")

    def test_assign_bays_remaps_via_front_panel(self):
        # The matched enc:slot is remapped through the front (sas) panel.
        d = self._d("S74ZNS0W111")
        panels = [{"panel": "front", "type": "sas", "reverse_slots": True,
                   "slots_per_enclosure": 8, "map": {}}]
        baymap.assign_bays([d], {"S74ZNS0W111": "32:0"}, panels)
        self.assertEqual(d.bay, "32:7")             # reverse-slots applied

    def test_assign_bays_blank_serial_skipped(self):
        d = self._d("")
        baymap.assign_bays([d], {"X": "1:1"}, [])
        self.assertIsNone(d.bay)                     # untouched (default None)

    def test_assign_bays_no_match_leaves_bay_untouched(self):
        d = self._d("UNRELATED")
        baymap.assign_bays([d], {"S74ZNS0W582303N": "32:0"}, [])
        self.assertIsNone(d.bay)


if __name__ == "__main__":
    unittest.main()
