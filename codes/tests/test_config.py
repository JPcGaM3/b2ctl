"""Unit tests for b2ctl.config — tool path resolution, defaults, mode/index."""
from __future__ import annotations

from unittest.mock import patch


class TestConfig:
    """Tests for config.py — tool resolution, missing file defaults."""

    def setup_method(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None

    def test_tool_returns_config_override_when_set(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"sas2ircu": "/custom/sas2ircu"},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        assert cfg_mod.tool("sas2ircu") == "/custom/sas2ircu"

    def test_tool_fallback_to_which_when_empty(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"smartctl": ""},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        with patch("b2ctl.config.shutil.which", return_value="/usr/sbin/smartctl"):
            result = cfg_mod.tool("smartctl")
        assert result == "/usr/sbin/smartctl"

    def test_tool_falls_back_to_bare_name_when_not_in_path(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"sas2ircu": ""},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        with patch("b2ctl.config.shutil.which", return_value=None):
            result = cfg_mod.tool("sas2ircu")
        assert result == "sas2ircu"

    def test_bay_map_path_returns_config_override(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "/srv/bay_map.json",
        }
        assert cfg_mod.bay_map_path() == "/srv/bay_map.json"

    def test_bay_map_path_returns_bundled_when_empty(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }
        p = cfg_mod.bay_map_path()
        assert p.endswith("bay_map.json")

    def test_load_returns_defaults_when_no_file(self):
        import b2ctl.config as cfg_mod
        with patch("b2ctl.config.os.path.exists", return_value=False):
            cfg = cfg_mod.load()
        assert cfg["controller"]["mode"] == "auto"
        assert cfg["controller"]["index"] == "all"
        assert cfg["tool_paths"]["sas2ircu"] == ""
        assert cfg["bay_map_path"] == ""

    def test_controller_mode_returns_auto_by_default(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None
        with patch("b2ctl.config.os.path.exists", return_value=False):
            assert cfg_mod.controller_mode() == "auto"

    def test_controller_index_setting_returns_all_by_default(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None
        with patch("b2ctl.config.os.path.exists", return_value=False):
            assert cfg_mod.controller_index_setting() == "all"

    def test_set_mode_writes_and_rereads(self):
        import json
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            cfg_mod.set_mode("raid")
            with open(path) as f:
                assert json.load(f)["controller"]["mode"] == "raid"
            assert cfg_mod.controller_mode() == "raid"
            # preserves existing keys + flips mode
            cfg_mod.set_mode("it")
            assert cfg_mod.controller_mode() == "it"
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_mode_rejects_invalid(self):
        import b2ctl.config as cfg_mod
        try:
            cfg_mod.set_mode("bogus")
            assert False, "expected ValueError"
        except ValueError:
            pass
