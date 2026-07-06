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

# rated TBW in terabytes-written
_DEFAULT_TBW = {
    "samsung ssd 860 pro 1tb": 1200,
    "samsung ssd 870 evo 1tb": 600,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def load() -> dict:
    from . import config
    table = {_norm(k): float(v) for k, v in _DEFAULT_TBW.items()}
    path = config.ssd_spec_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                for k, v in json.load(f).items():
                    table[_norm(k)] = float(v)
        except Exception as e:
            print(f"{Y}[!] cannot read {path}: {e} (using defaults){N}")
    return table


def lookup(model: str, table: dict):
    """Rated TBW for a model, or None (unknown) if the match is ambiguous.

    Preference order (F-097): exact normalized match; then the LONGEST spec key
    contained in the model (most specific capacity wins); finally, for a
    truncated model contained in a key, return a value only if every candidate
    agrees — otherwise None, so a 16-char SCSI-truncated 'Samsung SSD 870' never
    silently picks the 1TB rating for a 4TB drive and shows a false 0% endurance.
    """
    m = _norm(model)
    if not m:
        return None
    if m in table:
        return table[m]
    # spec key contained in the model -> pick the longest (most specific) key
    contained = [(k, v) for k, v in table.items() if k and k in m]
    if contained:
        return max(contained, key=lambda kv: len(kv[0]))[1]
    # model contained in a key (truncated model) -> only if unambiguous
    candidates = {v for k, v in table.items() if k and m in k}
    if len(candidates) == 1:
        return next(iter(candidates))
    return None
