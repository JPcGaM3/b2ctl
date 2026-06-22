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
