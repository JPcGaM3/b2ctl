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

    def test_detect_ignores_sas2ircu_error_banner(self):
        # F-010: sas2ircu banner + 'MPTLib2 Error 1' (no controller table) on a
        # RAID box must NOT select IT-mode when perccli reports a controller.
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()
        banner = ("LSI Corporation SAS2 IR Configuration Utility.\n"
                  "Version 20.00.00.00\n"
                  "SAS2IRCU: MPTLib2 Error 1\n")
        with patch("b2ctl.backend.run", return_value=banner), \
             patch.object(raid_mod, "have_tool", return_value=True), \
             patch.object(raid_mod, "is_hba_personality", return_value=False):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_get_backend_autodetects_raid_via_perccli(self):
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()

        # sas2ircu list → empty (no IT tool); perccli reports a RAID controller
        # that owns the storage (F-133: perccli alone no longer implies RAID).
        with patch("b2ctl.backend.run", return_value=""), \
             patch.object(raid_mod, "have_tool", return_value=True), \
             patch.object(raid_mod, "is_hba_personality", return_value=False):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_hba_personality_card_selects_it_with_perccli_bays(self):
        # F-133: a Dell HBA330/H330 answers perccli but hands raw disks to the
        # OS — IT-style enumeration, perccli only as the bay/LED source.
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()
        with patch("b2ctl.backend.run", return_value=""), \
             patch.object(raid_mod, "have_tool", return_value=True), \
             patch.object(raid_mod, "is_hba_personality", return_value=True):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.ITBackend)
        assert bk.bay_source == "perccli"

    def test_raid_personality_card_still_selects_raid(self):
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()
        with patch("b2ctl.backend.run", return_value=""), \
             patch.object(raid_mod, "have_tool", return_value=True), \
             patch.object(raid_mod, "is_hba_personality", return_value=False):
            bk = bk_mod.get_backend()
        assert isinstance(bk, bk_mod.RaidBackend)

    def test_it_backend_perccli_bay_source_delegates_bay_map(self):
        import b2ctl.backend as bk_mod
        import b2ctl.hba_raid as raid_mod
        bk = bk_mod.ITBackend(bay_source="perccli")
        with patch.object(raid_mod, "bay_map", return_value={"SN1": "9:0"}) as bm:
            assert bk.bay_map() == {"SN1": "9:0"}
        bm.assert_called_once()

    def test_it_backend_perccli_bay_source_enumerates_via_lsblk(self):
        # enumeration + SMART stay IT-style; only bays come from perccli.
        import b2ctl.backend as bk_mod
        bk = bk_mod.ITBackend(bay_source="perccli")
        with patch("b2ctl.hba.enumerate_disks", return_value=["sentinel"]) as en:
            assert bk.enumerate_disks() == ["sentinel"]
        en.assert_called_once()

    def test_it_backend_falls_back_to_perccli_when_sas2ircu_blind(self):
        # An operator who forced controller.mode='it' on an HBA330 still gets
        # bays: have_tool() flips the bay source instead of returning False.
        import b2ctl.backend as bk_mod
        import b2ctl.hba as hba_mod
        import b2ctl.hba_raid as raid_mod
        bk = bk_mod.ITBackend()
        with patch.object(hba_mod, "have_sas2ircu", return_value=False), \
             patch.object(raid_mod, "have_tool", return_value=True):
            assert bk.have_tool() is True
        assert bk.bay_source == "perccli"

    def test_detect_end_to_end_on_real_hba330_output(self):
        """F-133 integration: only `run` is faked, with the REAL command output
        from the field HBA330 Mini. Everything else is the production path."""
        import b2ctl.backend as bk_mod
        import b2ctl.hba as hba_mod
        import b2ctl.hba_raid as raid_mod
        self._auto_mode_cache()
        hba_mod._reset_have_cache()
        raid_mod._reset_caches()

        show = ("Controller = 0\nProduct Name = Dell HBA330 Mini\n"
                "Driver Name = mpt3sas\nPhysical Drives = 9\n")

        def fake_run(cmd, *a, **kw):
            argv = " ".join(cmd)
            if "sas2ircu" in argv:
                # SAS2 tool is blind to a SAS3008: banner, zero controllers.
                return ("LSI Corporation SAS2 IR Configuration Utility.\n"
                        "SAS2IRCU: MPTLib2 Error 1\n")
            if "ctrlcount" in argv:
                return "Controller Count = 1"
            if "/c0/vall" in argv:
                return ("Controller = 0\nStatus = Failure\n"
                        "Description = No VDs have been configured.\n")
            if argv.endswith("/c0 show"):
                return show
            return ""

        with patch("b2ctl.backend.run", side_effect=fake_run), \
             patch("b2ctl.hba.run", side_effect=fake_run), \
             patch("b2ctl.hba_raid.run", side_effect=fake_run), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            bk = bk_mod.get_backend()
        hba_mod._reset_have_cache()
        raid_mod._reset_caches()
        assert isinstance(bk, bk_mod.ITBackend)      # NOT RaidBackend
        assert bk.bay_source == "perccli"

    def test_get_backend_is_cached(self):
        import b2ctl.backend as bk_mod
        self._it_mode_cache()
        bk1 = bk_mod.get_backend()
        bk2 = bk_mod.get_backend()
        assert bk1 is bk2
