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


_cache: tuple | None = None      # ((path, mtime_ns), panels)


def load() -> list:
    """Return the panel list from bay_map.json, or [] (identity remap).

    Cached on (path, mtime_ns): one scan calls this ~4x, and a torn mid-edit
    read parsed at 4 instants gave half the pipeline panels and half [] (F-028).
    The mtime key auto-invalidates on an operator edit; a plain lru_cache would
    never see the change.
    """
    global _cache
    from . import config as _cfg
    path = _cfg.bay_map_path()
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return []                          # missing / unreadable -> identity remap
    key = (path, mtime)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[!] {path}: {exc} — using scrambled raw slots (fix bay_map.json)")
        return []
    if isinstance(data, dict):
        # Pre-0.8 flat format ({"reverse_slots":…}/{"map":…}) is no longer read.
        print(f"[!] {path}: old bay_map format — migrate to the panel list; ignoring")
        panels: list = []
    else:
        panels = data if isinstance(data, list) else []
    _cache = (key, panels)
    return panels


def _panels(panels: list, ptype: str) -> list:
    return [p for p in panels if isinstance(p, dict) and p.get("type") == ptype]


def serial_match(a: str, b: str) -> bool:
    """Fuzzy serial equality: both non-empty and one is a prefix of the other.

    Tools truncate serials differently (lsblk vs sas2ircu vs SMART), so an exact
    compare misses real matches. Shared by hba/hba_raid bay attach, ghost
    detection, and core.scan's ghost-drop filter (must all agree)."""
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def assign_bays(disks: list, bm: dict, panels: list) -> None:
    """Fill each disk's display bay from a serial->'enc:slot' map (F-084).

    The one authoritative copy of the serial-match-then-remap loop that both
    backends' attach_bays used to duplicate verbatim (the exact fuzzy-serial area
    CLAUDE.md §6 flags as regression-prone). Exact serial hit wins; else the
    fuzzy prefix match (serial_match); the matched enc:slot is remapped to the
    chassis label via the front (sas) panel. Only d.bay (the display label) is
    touched — RAID-mode actions target d.ctrl_slot, which is left untouched.
    """
    for d in disks:
        if not d.serial:
            continue
        if d.serial in bm:
            d.bay = remap_slot(bm[d.serial], panels)
            continue
        for bm_serial, bay_val in bm.items():
            if serial_match(d.serial, bm_serial):
                d.bay = remap_slot(bay_val, panels)
                break


def remap_slot(enc_slot: str, panels: list) -> str:
    """Remap a sas/PERC 'enc:slot' via a front (type=sas) panel.

    Explicit "map" override wins; else the reverse-slots rule; else identity.
    """
    for p in _panels(panels, "sas"):
        table = p.get("map") or {}
        if isinstance(table, dict) and enc_slot in table:
            return table[enc_slot]
        if p.get("reverse_slots"):
            try:
                n = int(p.get("slots_per_enclosure", 8))
                enc, slot = enc_slot.split(":")
                return f"{enc}:{(n - 1) - int(slot)}"
            except (ValueError, AttributeError, TypeError):
                pass
    return enc_slot


def remap_nvme(bdf: str, panels: list, *, by_id: str = "", serial: str = "") -> str:
    """Remap an NVMe drive's bay via a back (type=nvme) panel.

    A map entry may key on `by-id` (substring of /dev/disk/by-id/nvme-…),
    `serial`, or `bdf`; precedence by-id > serial > bdf. First matching entry
    wins. Returns the entry's `bay`, else the bdf (identity)."""
    for p in _panels(panels, "nvme"):
        for d in (p.get("map") or []):
            if not isinstance(d, dict):     # F-029: skip a malformed non-dict entry
                continue
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
