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
            "ssd_spec_path": "",
        }
        with patch("b2ctl.config.os.path.exists", return_value=False):
            p = cfg_mod.bay_map_path()
        assert p.endswith("bay_map.json")

    def test_bay_map_path_prefers_etc_standard_over_bundled(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
            "ssd_spec_path": "",
        }
        # no override + /etc file present -> the absolute standard path wins
        with patch("b2ctl.config.os.path.exists", return_value=True):
            assert cfg_mod.bay_map_path() == cfg_mod.STD_BAY_MAP

    def test_ssd_spec_path_returns_config_override(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
            "ssd_spec_path": "/srv/ssd_spec.json",
        }
        assert cfg_mod.ssd_spec_path() == "/srv/ssd_spec.json"

    def test_ssd_spec_path_prefers_etc_then_bundled(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
            "ssd_spec_path": "",
        }
        with patch("b2ctl.config.os.path.exists", return_value=True):
            assert cfg_mod.ssd_spec_path() == cfg_mod.STD_SSD_SPEC
        with patch("b2ctl.config.os.path.exists", return_value=False):
            assert cfg_mod.ssd_spec_path().endswith("ssd_spec.json")

    def test_load_honors_ssd_spec_path_override(self):
        import json
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            json.dump({"ssd_spec_path": "/data/ssd_spec.json"}, f)
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            cfg = cfg_mod.load()
            assert cfg["ssd_spec_path"] == "/data/ssd_spec.json"
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_validate_labels_override_and_etc_ok_bundled_warn(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"sas2ircu": "", "perccli": "", "smartctl": "", "zpool": ""},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "/srv/bay_map.json",          # override -> ok
            "ssd_spec_path": "",                          # -> bundled -> warn
        }

        def _exists(p):
            # /etc data files absent (force bundled); override + bundled present
            return not p.startswith(cfg_mod.STD_DIR)

        with patch("b2ctl.config.subprocess.run", side_effect=FileNotFoundError), \
             patch("b2ctl.config.os.path.exists", side_effect=_exists):
            rows = {name: status for name, status, _ in cfg_mod.validate()}
        assert rows["bay_map"] == "ok"      # config override
        assert rows["ssd_spec"] == "warn"   # bundled fallback

    def test_load_returns_defaults_when_no_file(self):
        import b2ctl.config as cfg_mod
        with patch("b2ctl.config.os.path.exists", return_value=False):
            cfg = cfg_mod.load()
        assert cfg["controller"]["mode"] == "auto"
        assert cfg["controller"]["index"] == "all"
        assert cfg["tool_paths"]["sas2ircu"] == ""
        assert cfg["bay_map_path"] == ""

    def test_load_survives_wrong_shape_config(self):
        # F-014: valid JSON of the wrong shape must fall back to defaults, not
        # crash every command with AttributeError.
        import json
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            for bad in ('{"tool_paths": "/usr/sbin"}',
                        '{"controller": "raid"}',
                        '[1, 2, 3]',
                        '"just a string"'):
                with open(path, "w") as f:
                    f.write(bad)
                cfg_mod._cache = None
                cfg = cfg_mod.load()   # must not raise
                assert cfg["controller"]["mode"] == "auto"
                assert cfg["tool_paths"]["sas2ircu"] == ""
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_load_partial_good_section_still_applies(self):
        # A good controller section applies even though tool_paths is malformed.
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            with open(path, "w") as f:
                f.write('{"tool_paths": "oops", "controller": {"mode": "raid"}}')
            cfg_mod._cache = None
            cfg = cfg_mod.load()
            assert cfg["controller"]["mode"] == "raid"
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

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

    def test_set_mode_malformed_file_raises_and_preserves(self):
        # F-075: an unparseable existing config must NOT be silently reset to
        # {"controller": {...}} (erasing tool_paths/bay_map_path). set_mode
        # raises ValueError and leaves the file byte-identical.
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            f.write('{"tool_paths": {"zpool": "/x"},}')   # trailing comma -> invalid
        with open(path, "rb") as f:
            before = f.read()
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            try:
                cfg_mod.set_mode("it")
                assert False, "expected ValueError on malformed config"
            except ValueError:
                pass
            with open(path, "rb") as f:
                assert f.read() == before          # left untouched, not clobbered
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_mode_non_dict_toplevel_raises(self):
        # F-075: a valid-JSON but non-object top level (a list) must be refused,
        # not crash setdefault with AttributeError.
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            f.write("[1, 2, 3]")
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            try:
                cfg_mod.set_mode("raid")
                assert False, "expected ValueError on non-object top level"
            except ValueError:
                pass
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_mode_preserves_existing_keys(self):
        # F-075: a successful write flips mode and preserves every other key.
        import json
        import os
        import tempfile
        import b2ctl.config as cfg_mod
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as f:
            json.dump({"tool_paths": {"zpool": "/usr/sbin/zpool"},
                       "bay_map_path": "/srv/bay_map.json",
                       "controller": {"mode": "auto", "index": "0"}}, f)
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            cfg_mod.set_mode("raid")
            with open(path) as f:
                data = json.load(f)
            assert data["controller"]["mode"] == "raid"
            assert data["controller"]["index"] == "0"           # preserved
            assert data["tool_paths"]["zpool"] == "/usr/sbin/zpool"
            assert data["bay_map_path"] == "/srv/bay_map.json"
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None
