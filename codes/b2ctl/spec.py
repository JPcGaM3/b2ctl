"""b2ctl.spec — SSD endurance (TBW) spec table.

Endurance-remaining for SSDs is computed from total bytes written vs the
drive's rated TBW. Ratings live in ssd_spec.json next to the package (override
or extend the built-in defaults); model match is case/space-insensitive and
substring-based.
"""

from __future__ import annotations

import json
import os
import re

from .common import Y, N

_SPEC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "ssd_spec.json")

# rated TBW in terabytes-written
_DEFAULT_TBW = {
    "samsung ssd 860 pro 1tb": 1200,
    "samsung ssd 870 evo 1tb": 600,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def load() -> dict:
    table = {_norm(k): float(v) for k, v in _DEFAULT_TBW.items()}
    path = os.path.abspath(_SPEC_FILE)
    if os.path.exists(path):
        try:
            with open(path) as f:
                for k, v in json.load(f).items():
                    table[_norm(k)] = float(v)
        except Exception as e:
            print(f"{Y}[!] cannot read {path}: {e} (using defaults){N}")
    return table


def lookup(model: str, table: dict):
    m = _norm(model)
    if not m:
        return None
    for k, v in table.items():
        if k and k in m:
            return v
    return None
