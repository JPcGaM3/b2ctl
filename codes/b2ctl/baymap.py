"""b2ctl.baymap — parse bay_map.json (panel list) + remap raw locators.

Schema (a list of physical panels):

    [
      { "panel": "front", "type": "sas",
        "reverse_slots": true, "slots_per_enclosure": 8,
        "map": {} },                       # optional enc:slot -> label overrides
      { "panel": "back", "type": "nvme",
        "map": [ {"by-id": "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7..", "bay": "PCIe2:0"},
                 {"serial": "S7XXNS0W123", "bay": "PCIe2:1"},
                 {"bdf": "d8:00.0", "bay": "PCIe2:2"} ] }
    ]

front (type=sas) covers the PERC backplane and the PERC-flashed sas2ircu HBA
(both addressed as enc:slot). back (type=nvme) maps a drive to a custom bay
label by `by-id`, `serial`, or PCIe `bdf` (precedence by-id > serial > bdf);
one or more back panels are allowed.
"""
from __future__ import annotations

import json
import os


def load() -> list:
    """Return the panel list from bay_map.json, or [] (identity remap)."""
    from . import config as _cfg
    path = _cfg.bay_map_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return []
    if isinstance(data, dict):
        # Pre-0.8 flat format ({"reverse_slots":…}/{"map":…}) is no longer read.
        print(f"[!] {path}: old bay_map format — migrate to the panel list; ignoring")
        return []
    return data if isinstance(data, list) else []


def _panels(panels: list, ptype: str) -> list:
    return [p for p in panels if p.get("type") == ptype]


def remap_slot(enc_slot: str, panels: list) -> str:
    """Remap a sas/PERC 'enc:slot' via a front (type=sas) panel.

    Explicit "map" override wins; else the reverse-slots rule; else identity.
    """
    for p in _panels(panels, "sas"):
        table = p.get("map") or {}
        if isinstance(table, dict) and enc_slot in table:
            return table[enc_slot]
        if p.get("reverse_slots"):
            n = int(p.get("slots_per_enclosure", 8))
            try:
                enc, slot = enc_slot.split(":")
                return f"{enc}:{(n - 1) - int(slot)}"
            except (ValueError, AttributeError):
                pass
    return enc_slot


def remap_nvme(bdf: str, panels: list, *, by_id: str = "", serial: str = "") -> str:
    """Remap an NVMe drive's bay via a back (type=nvme) panel.

    A map entry may key on `by-id` (substring of /dev/disk/by-id/nvme-…),
    `serial`, or `bdf`; precedence by-id > serial > bdf. First matching entry
    wins. Returns the entry's `bay`, else the bdf (identity)."""
    for p in _panels(panels, "nvme"):
        for d in (p.get("map") or []):
            tgt = d.get("by-id")
            if tgt and by_id and tgt in by_id:
                return d.get("bay", bdf)
            tgt = d.get("serial")
            if tgt and serial and (tgt == serial or tgt in serial):
                return d.get("bay", bdf)
            tgt = d.get("bdf")
            if tgt and bdf and tgt == bdf:
                return d.get("bay", bdf)
    return bdf
