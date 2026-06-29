"""Unit tests for b2ctl.baymap — panel-based bay_map.json parsing + remap."""
import unittest
from unittest.mock import mock_open, patch

import b2ctl.baymap as baymap

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


class TestLoad(unittest.TestCase):

    def test_old_dict_format_ignored(self):
        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data='{"reverse_slots": true}')):
            assert baymap.load() == []

    def test_panel_list_loads(self):
        import json
        data = json.dumps([_FRONT, _BACK])
        with patch("b2ctl.config.bay_map_path", return_value="/x/bay_map.json"), \
             patch("b2ctl.baymap.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=data)):
            panels = baymap.load()
        assert isinstance(panels, list) and len(panels) == 2

    def test_missing_file(self):
        with patch("b2ctl.config.bay_map_path", return_value="/x/none.json"), \
             patch("b2ctl.baymap.os.path.exists", return_value=False):
            assert baymap.load() == []


if __name__ == "__main__":
    unittest.main()
