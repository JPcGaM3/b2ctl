"""Unit tests for b2ctl.spec — SSD TBW model lookup."""
from __future__ import annotations

from b2ctl import spec


class TestSpecLookup:
    """Tests for SSD TBW model matching."""

    def test_exact_match(self):
        table = {"samsung ssd 870 evo 1tb": 600}
        assert spec.lookup("Samsung SSD 870 EVO 1TB", table) == 600

    def test_substring_match(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("Samsung SSD 870 EVO 1TB", table) == 600

    def test_no_match_returns_none(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("WD Black", table) is None

    def test_case_insensitive(self):
        table = {"samsung ssd 860 pro 1tb": 1200}
        assert spec.lookup("SAMSUNG SSD 860 PRO 1TB", table) == 1200

    def test_empty_model_returns_none(self):
        table = {"samsung ssd 870": 600}
        assert spec.lookup("", table) is None

    def test_reverse_match_short_model_in_long_key(self):
        # fix 4: lsblk may emit "Samsung SSD 870 EVO" without capacity suffix
        # but spec key is "samsung ssd 870 evo 1tb" → m in k must catch it
        table = {"samsung ssd 870 evo 1tb": 600}
        assert spec.lookup("Samsung SSD 870 EVO", table) == 600

    def test_reverse_match_does_not_false_positive(self):
        table = {"samsung ssd 870 evo 1tb": 600}
        assert spec.lookup("WD Red", table) is None

    def test_truncated_model_ambiguous_returns_none(self):
        # F-097: a capacity-less model contained in two different-capacity keys
        # must NOT silently pick the first-inserted rating — ambiguous -> None,
        # so a 16-char SCSI-truncated model never shows a false 0% endurance.
        table = {"samsung ssd 870 evo 1tb": 600, "samsung ssd 870 evo 2tb": 1200}
        assert spec.lookup("Samsung SSD 870 EVO", table) is None

    def test_longest_contained_key_wins(self):
        # F-097: when the full model contains several spec keys, the most
        # specific (longest) capacity key wins — not the first inserted.
        table = {"samsung ssd 870": 500, "samsung ssd 870 evo 1tb": 600}
        assert spec.lookup("Samsung SSD 870 EVO 1TB Series", table) == 600


class TestSpecLoadPath:
    """spec.load() resolves the file via config.ssd_spec_path()."""

    def test_load_reads_config_ssd_spec_path_and_merges(self):
        import json
        import os
        import tempfile
        from unittest.mock import patch
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "ssd_spec.json")
        with open(path, "w") as f:
            json.dump({"acme nvme 4tb": 3000}, f)
        with patch("b2ctl.config.ssd_spec_path", return_value=path):
            table = spec.load()
        # custom entry from the file...
        assert table["acme nvme 4tb"] == 3000.0
        # ...merged over the built-in defaults (still present)
        assert table["samsung ssd 870 evo 1tb"] == 600.0
