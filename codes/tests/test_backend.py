"""Unit tests for b2ctl.backend — detection, caching, backend class names."""
from __future__ import annotations

from unittest.mock import patch


class TestBackend:
    """Tests for backend.py — detection, caching, backend class names."""

    def setup_method(self):
        import b2ctl.backend as bk_mod
        import b2ctl.config as cfg_mod
        bk_mod._backend_cache = None
        cfg_mod._cache = None

    def _it_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "it", "index": "all"},
            "bay_map_path": "",
        }

    def _raid_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "raid", "index": "all"},
            "bay_map_path": "",
        }

    def _auto_mode_cache(self):
        import b2ctl.config as cfg_mod
        cfg_mod._cache = {
            "tool_paths": {k: "" for k in ("sas2ircu","storcli","storcli64","perccli","perccli64","smartctl","lsblk","zpool","wipefs","sgdisk","udevadm","dd")},
            "controller": {"mode": "auto", "index": "all"},
            "bay_map_path": "",
        }

    def test_it_backend_name_is_it(self):
        from b2ctl.backend import ITBackend
        assert ITBackend().name == "it"

    def test_raid_backend_name_is_raid(self):
        from b2ctl.backend import RaidBackend
        assert RaidBackend().name == "raid"

    def test_get_backend_returns_it_when_mode_it(self):
        import b2ctl.backend as bk_mod
        self._it_mode_cache()
        bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.ITBackend)

    def test_get_backend_returns_raid_when_mode_raid(self):
        import b2ctl.backend as bk_mod
        self._raid_mode_cache()
        bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_get_backend_autodetects_it_via_sas2ircu(self):
        import b2ctl.backend as bk_mod
        self._auto_mode_cache()
        with patch("b2ctl.backend.run", return_value="  0  SAS2308"):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.ITBackend)

    def test_get_backend_autodetects_raid_via_perccli(self):
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()

        # sas2ircu list → empty (no IT tool); perccli reports a controller.
        with patch("b2ctl.backend.run", return_value=""), \
             patch.object(raid_mod, "have_tool", return_value=True):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_get_backend_is_cached(self):
        import b2ctl.backend as bk_mod
        self._it_mode_cache()
        bk1 = bk_mod.get_backend()
        bk2 = bk_mod.get_backend()
        assert bk1 is bk2
