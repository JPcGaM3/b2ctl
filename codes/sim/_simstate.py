"""Shared state engine for the b2ctl simulation harness.

A single JSON file (``B2CTL_STATE``, default ``sim/state.json``) is the source
of truth for a fake 6-disk server: disk inventory + ZFS pools. The fake
binaries in ``sim/bin/`` read/write this file so real b2ctl runs unchanged.

Stdlib only.
"""
from __future__ import annotations

import json
import os

STATE_PATH = os.environ.get(
    "B2CTL_STATE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"),
)


def _disk(name, serial, model, enc, slot, *, size=1000204886016, tran="sas",
          rota=0, poh=20000, wear=99, lba_written=18_000_000_000,
          realloc=0, pending=0, present=True, dirty=False):
    return dict(name=name, serial=serial, model=model, size=size, tran=tran,
                rota=rota, enc=enc, slot=slot, poh=poh, wear=wear,
                lba_written=lba_written, realloc=realloc, pending=pending,
                present=present, dirty=dirty)


def default_state() -> dict:
    """A realistic R620: rpool mirror (2x 860 PRO) + tank raidz1 (3x 870 EVO) + 1 spare."""
    return {
        "mode": "it",
        "disks": [
            _disk("sdf", "S5G8NE0MA10474H", "Samsung SSD 860 PRO 1TB", 1, 0,
                  poh=51055, lba_written=19_650_000_000),
            _disk("sda", "S5G8NE0MA10478T", "Samsung SSD 860 PRO 1TB", 1, 1,
                  poh=51056, lba_written=20_060_000_000),
            _disk("sdb", "S74ZNS0W537278Y", "Samsung SSD 870 EVO 1TB", 1, 4,
                  poh=22926, lba_written=18_960_000_000),
            _disk("sdc", "S74ZNS0W533737E", "Samsung SSD 870 EVO 1TB", 1, 5,
                  poh=18277, lba_written=19_290_000_000),
            _disk("sdd", "S74ZNS0W582278Y", "Samsung SSD 870 EVO 1TB", 1, 6,
                  poh=18281, lba_written=19_360_000_000),
            _disk("sde", "S74ZNS0W582280E", "Samsung SSD 870 EVO 1TB", 1, 7,
                  poh=18281, lba_written=1_970_000_000),
        ],
        "pools": [
            {"name": "rpool", "type": "mirror", "members": ["sdf", "sda"],
             "spares": [], "replacements": [], "resilver": None},
            {"name": "tank", "type": "raidz1", "members": ["sdb", "sdc", "sdd"],
             "spares": ["sde"], "replacements": [], "resilver": None},
        ],
    }


# --------------------------------------------------------------------------- #
# load / save
# --------------------------------------------------------------------------- #

def load() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_state()


def save(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# --------------------------------------------------------------------------- #
# lookups
# --------------------------------------------------------------------------- #

def disk_by_name(state: dict, name: str):
    name = name.replace("/dev/", "")
    for d in state["disks"]:
        if d["name"] == name:
            return d
    return None


def disk_by_token(state: dict, token: str):
    """Resolve a pool member token (/dev/sdX or sdX or serial) to a disk."""
    t = token.replace("/dev/", "")
    for d in state["disks"]:
        if d["name"] == t or d["serial"] == t:
            return d
    return None


def disk_by_bay(state: dict, bay: str):
    """bay = 'enc:slot' e.g. '1:5'."""
    try:
        enc, slot = (int(x) for x in bay.split(":"))
    except ValueError:
        return None
    for d in state["disks"]:
        if d["enc"] == enc and d["slot"] == slot:
            return d
    return None


def pool_of(state: dict, name: str):
    for p in state["pools"]:
        if name in p["members"] or name in p["spares"]:
            return p
        for r in p.get("replacements", []):
            if name in (r["removed"], r["spare"]):
                return p
    return None


def parity_of(pool: dict) -> int:
    t = pool["type"]
    if t == "raidz1":
        return 1
    if t == "raidz2":
        return 2
    if t == "mirror":
        return max(0, len(pool["members"]) - 1)
    return 0  # stripe


def assigned_names(state: dict) -> set:
    out = set()
    for p in state["pools"]:
        out.update(p["members"], p["spares"])
        for r in p.get("replacements", []):
            out.update((r["removed"], r["spare"]))
    return out
