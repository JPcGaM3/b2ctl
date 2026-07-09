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

    def test_tool_systemctl_override_and_default(self):
        # systemctl is a first-class tool (maintenance timers); override + default
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {"systemctl": "/bin/systemctl"},
            "controller": {"mode": "auto", "index": "all"}, "bay_map_path": "",
        }
        assert cfg_mod.tool("systemctl") == "/bin/systemctl"
        assert "systemctl" in cfg_mod._DEFAULTS["tool_paths"]

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
        assert cfg["smart"] == {"timeout": 10, "megaraid_workers": 4}

    def test_load_honors_smart_overrides(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"smart": {"timeout": 25, "megaraid_workers": 2}}, f)
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            cfg = cfg_mod.load()
            assert cfg["smart"] == {"timeout": 25, "megaraid_workers": 2}
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_smart_ignores_bad_values(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            # bool / non-int / non-positive must be rejected per key -> defaults kept
            json.dump({"smart": {"timeout": True, "megaraid_workers": "4"}}, f)
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            cfg = cfg_mod.load()
            assert cfg["smart"] == {"timeout": 10, "megaraid_workers": 4}
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_smart_config_getter_survives_partial_cache(self):
        # a manually-set cache missing 'smart' must not KeyError smart_config()
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {"tool_paths": {}, "controller": {"mode": "auto", "index": "all"}}
        try:
            assert cfg_mod.smart_config() == {"timeout": 10, "megaraid_workers": 4}
        finally:
            cfg_mod._cache = None

    def test_health_defaults(self):
        import b2ctl.config as cfg_mod
        with patch("b2ctl.config.os.path.exists", return_value=False):
            h = cfg_mod.load()["health"]
        assert h["ssd"]["realloc_crit"] == 0          # SSD: any realloc -> CRIT
        assert h["ssd"]["realloc_warn"] is None
        assert h["ssd"]["endurance_crit"] == 20
        assert h["hdd"]["realloc_warn"] == 50
        assert h["hdd"]["realloc_crit"] == 200
        assert h["hdd"]["pending_warn"] == 0
        assert h["ssd"]["poh_warn"] is None and h["hdd"]["poh_warn"] is None

    def test_health_overrides_and_na_disables(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"health": {
                "hdd": {"realloc_warn": 80, "realloc_crit": "N/A"},   # crit disabled
                "ssd": {"poh_warn": 40000},                            # enable POH
            }}, f)
        old = cfg_mod.CONFIG_PATH
        cfg_mod.CONFIG_PATH = path
        try:
            cfg_mod._cache = None
            h = cfg_mod.load()["health"]
            assert h["hdd"]["realloc_warn"] == 80          # overridden
            assert h["hdd"]["realloc_crit"] is None        # "N/A" -> disabled
            assert h["hdd"]["pending_warn"] == 0           # untouched key keeps default
            assert h["ssd"]["poh_warn"] == 40000           # enabled
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_health_config_getter_survives_partial_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {"tool_paths": {}, "controller": {"mode": "auto", "index": "all"}}
        try:
            hc = cfg_mod.health_config()
            assert hc["ssd"]["realloc_crit"] == 0
            assert hc["hdd"]["realloc_warn"] == 50
        finally:
            cfg_mod._cache = None

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


class TestConfigPools:
    """v0.17.0: per-pool maintenance record (pools) + sticky pool_defaults."""

    def setup_method(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = None

    def _swap(self, cfg_mod, path):
        cfg_mod.CONFIG_PATH = path
        cfg_mod._cache = None

    def test_defaults_when_no_file(self):
        import b2ctl.config as cfg_mod
        with patch("b2ctl.config.os.path.exists", return_value=False):
            cfg = cfg_mod.load()
        assert cfg["pools"] == {}
        assert cfg["pool_defaults"] == {"autotrim": "off", "autoscrub": False}

    def test_load_roundtrips_pools(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"pools": {"tank": {"autotrim": "off", "autoscrub": False},
                                 "rpool": {"autotrim": "on", "autoscrub": True}}}, f)
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            assert cfg_mod.pool_settings("tank") == {"autotrim": "off", "autoscrub": False}
            assert cfg_mod.pool_settings("rpool")["autoscrub"] is True
            assert cfg_mod.pool_settings("absent") == {}
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_malformed_pools_falls_back(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"pools": ["not", "a", "dict"]}, f)     # wrong shape
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            assert cfg_mod.load()["pools"] == {}
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_pool_settings_preserves_other_keys(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"tool_paths": {"zpool": "/usr/sbin/zpool"},
                       "pools": {"rpool": {"autotrim": "on", "autoscrub": True}}}, f)
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            cfg_mod.set_pool_settings("tank", autotrim="off", autoscrub=False)
            with open(path) as f:
                data = json.load(f)
            assert data["pools"]["tank"] == {"autotrim": "off", "autoscrub": False}
            assert data["pools"]["rpool"]["autoscrub"] is True          # preserved
            assert data["tool_paths"]["zpool"] == "/usr/sbin/zpool"     # preserved
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_remove_pool_settings(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            json.dump({"pools": {"tank": {"autotrim": "off", "autoscrub": False},
                                 "rpool": {"autotrim": "on", "autoscrub": True}}}, f)
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            cfg_mod.remove_pool_settings("tank")
            with open(path) as f:
                data = json.load(f)
            assert "tank" not in data["pools"]
            assert "rpool" in data["pools"]           # only the named one removed
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_pool_settings_refuses_malformed_file(self):
        import os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        with open(path, "w") as f:
            f.write("{ this is not json")
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            import pytest
            with pytest.raises(ValueError):
                cfg_mod.set_pool_settings("tank", autotrim="off", autoscrub=False)
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None

    def test_set_pool_defaults_sticky(self):
        import json, os, tempfile
        import b2ctl.config as cfg_mod
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        old = cfg_mod.CONFIG_PATH
        self._swap(cfg_mod, path)
        try:
            cfg_mod.set_pool_defaults(autotrim="on", autoscrub=True)
            assert cfg_mod.pool_defaults() == {"autotrim": "on", "autoscrub": True}
        finally:
            cfg_mod.CONFIG_PATH = old
            cfg_mod._cache = None
