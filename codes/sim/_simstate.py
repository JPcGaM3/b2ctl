"""Shared state engine for the b2ctl simulation harness.

A single JSON file (``B2CTL_STATE``, default ``sim/state.json``) is the source
of truth for a fake 6-disk server: disk inventory + ZFS pools. The fake
binaries in ``sim/bin/`` read/write this file so real b2ctl runs unchanged.

Stdlib only.
"""
from __future__ import annotations

import json
import os
import time

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
            # 2x NVMe (back PCIe panel) — no enc:slot; the bay comes from
            # bay_map.json (serial -> PCIe2:N). Unassigned, like a fresh card.
            _disk("nvme0n1", "S7U9NU0Y401069K", "Samsung SSD 990 EVO Plus 4TB",
                  None, None, size=4_000_787_030_016, tran="nvme",
                  poh=2, wear=100, lba_written=0),
            _disk("nvme1n1", "S7U9NU0Y400872E", "Samsung SSD 990 EVO Plus 4TB",
                  None, None, size=4_000_787_030_016, tran="nvme",
                  poh=10, wear=100, lba_written=0),
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
    except FileNotFoundError:
        return default_state()          # before `simctl init` — pristine layout
    except json.JSONDecodeError as exc:
        # A genuinely corrupt file must not silently reset the operator's
        # scenario to defaults (F-114). Since save() is atomic, a decode error
        # can only mean a real corruption, so fail loudly.
        raise SystemExit(f"[sim] corrupt state file {STATE_PATH}: {exc}")


def save(state: dict) -> None:
    # Atomic: write a tmp file in the same dir then os.replace, so a concurrent
    # reader (watch polling while simctl mutates) never sees a torn write (F-114).
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


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


def _nvme_map() -> list:
    """The sim bay_map.json nvme panel entries [{serial, bay}, ...] (F-123)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bay_map.json")
    try:
        with open(path) as f:
            panels = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for p in panels if isinstance(panels, list) else []:
        if isinstance(p, dict) and p.get("type") == "nvme":
            out += [e for e in (p.get("map") or []) if isinstance(e, dict)]
    return out


def _nvme_bay_serial(bay: str):
    """Serial mapped to a non-numeric bay label (e.g. 'PCIe2:0'), or None."""
    return next((e.get("serial") for e in _nvme_map() if e.get("bay") == bay), None)


def nvme_label_for_serial(serial):
    """Inverse: the relabelled bay for an NVMe serial, or None."""
    if not serial:
        return None
    return next((e.get("bay") for e in _nvme_map() if e.get("serial") == serial), None)


def disk_by_bay(state: dict, bay: str):
    """bay = 'enc:slot' e.g. '1:5'; also a relabelled NVMe bay like 'PCIe2:0'."""
    try:
        enc, slot = (int(x) for x in bay.split(":"))
    except ValueError:
        # Non-numeric bay (NVMe relabel): resolve via the bay_map serial (F-123).
        sn = _nvme_bay_serial(bay)
        return disk_by_token(state, sn) if sn else None
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


def resilver_seed() -> dict:
    """A time-based resilver record (F-116).

    pct is DERIVED from wall-clock elapsed (see resilver_pct), so `zpool status`
    reads never advance it — the old +50%-per-read made progress read-count-based
    and let a resilver complete after two arbitrary reads, masking Task-B
    ordering bugs. Override the duration with B2CTL_SIM_RESILVER_SECS (default 8)
    so pytest can keep it near-instant or slow it down deterministically."""
    try:
        secs = float(os.environ.get("B2CTL_SIM_RESILVER_SECS", "8"))
    except ValueError:
        secs = 8.0
    return {"start": time.time(), "secs": secs}


def resilver_pct(res) -> int:
    """Percent complete of a resilver record, derived from wall-clock (F-116).
    Falls back to a legacy {'pct': N} record so old state.json files still work."""
    if not res:
        return 0
    if "secs" in res and "start" in res:
        secs = res["secs"] or 1
        return min(100, int(100 * (time.time() - res["start"]) / secs))
    return int(res.get("pct", 0))


def raid_pds(state: dict) -> list:
    """PERC-visible physical drives for RAID mode (F-115).

    Present drives that have an enc:slot and are NOT NVMe (a PERC cannot see the
    back-panel NVMe card). Ordered by (enc, slot) so the list index is a stable
    megaraid DID that both the fake perccli (PD rows) and the fake smartctl
    (`-d megaraid,<DID>` passthrough) agree on."""
    ds = [d for d in state["disks"]
          if d.get("present") and d.get("enc") is not None and d.get("tran") != "nvme"]
    return sorted(ds, key=lambda d: (d["enc"], d["slot"]))


# How many of the raid_pds are configured into the default synthetic VD (vd0);
# the rest surface as Unconfigured-Good so both PD paths get exercised (F-115).
RAID_VD_SIZE = 3


def raid10_groups(pool: dict) -> list:
    """Mirror groups of a raid10 pool: stored 'groups', else members paired in
    order (backward-compatible with old state.json files lacking 'groups')."""
    g = pool.get("groups")
    if g:
        return g
    m = pool["members"]
    return [m[i:i + 2] for i in range(0, len(m), 2)]


def parity_of(pool: dict) -> int:
    t = pool["type"]
    if t == "raidz1":
        return 1
    if t == "raidz2":
        return 2
    if t == "mirror":
        return max(0, len(pool["members"]) - 1)
    if t == "raid10":
        return 1  # tolerates one disk per mirror group (health is group-aware)
    return 0  # stripe


def assigned_names(state: dict) -> set:
    out = set()
    for p in state["pools"]:
        out.update(p["members"], p["spares"])
        for r in p.get("replacements", []):
            out.update((r["removed"], r["spare"]))
    return out
