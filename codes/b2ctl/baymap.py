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

from . import common

_cache: tuple | None = None      # ((path, mtime_ns), panels)
_slot_warned: set = set()        # (enclosure, n) pairs already warned about
                                  # (F-140) — remap_slot runs per-drive, so
                                  # this keeps the out-of-range notice to once
                                  # per enclosure/count, not once per disk.


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
        common.warn(f"[!] {path}: {exc} — using scrambled raw slots (fix bay_map.json)")
        return []
    if isinstance(data, dict):
        # Pre-0.8 flat format ({"reverse_slots":…}/{"map":…}) is no longer read.
        common.warn(f"[!] {path}: old bay_map format — migrate to the panel list; ignoring")
        panels: list = []
    else:
        panels = data if isinstance(data, list) else []
    _cache = (key, panels)
    _slot_warned.clear()   # F-140: a re-parsed file may fix/change the counts
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


def detect_slots(enc_slots) -> dict:
    """{enclosure: slot_count} from an iterable of 'enc:slot' strings —
    max slot seen + 1. Ignores malformed entries.

    Auto-detects the per-enclosure slot count (F-140) for chassis wider than
    the 8-slot default (R740xd/HBA330: 24 bays, enc:slot up to '32:23').
    """
    highest: dict[str, int] = {}
    for es in enc_slots:
        try:
            enc, slot_s = str(es).split(":")
            slot = int(slot_s)
        except (ValueError, AttributeError, TypeError):
            continue
        if enc not in highest or slot > highest[enc]:
            highest[enc] = slot
    return {enc: n + 1 for enc, n in highest.items()}


def assign_bays(disks: list, bm: dict, panels: list) -> None:
    """Fill each disk's display bay from a serial->'enc:slot' map (F-084).

    The one authoritative copy of the serial-match-then-remap loop that both
    backends' attach_bays used to duplicate verbatim (the exact fuzzy-serial area
    CLAUDE.md §6 flags as regression-prone). Exact serial hit wins; else the
    fuzzy prefix match (serial_match); the matched enc:slot is remapped to the
    chassis label via the front (sas) panel. Only d.bay (the display label) is
    touched — RAID-mode actions target d.ctrl_slot, which is left untouched.
    """
    slots_hint = detect_slots(bm.values())     # F-140: auto slot count per enc
    for d in disks:
        if not d.serial:
            continue
        if d.serial in bm:
            d.bay = remap_slot(bm[d.serial], panels, slots_hint)
            continue
        for bm_serial, bay_val in bm.items():
            if serial_match(d.serial, bm_serial):
                d.bay = remap_slot(bay_val, panels, slots_hint)
                break


def assign_sysfs_bays(disks: list, panels: list, enc: str = "0",
                      slots: dict | None = None) -> None:
    """Fill any disk still WITHOUT a bay from the kernel's SAS transport class.

    Runs after assign_bays, never instead of it: a vendor tool's label always
    wins where it exists, so an R620's sas2ircu + reverse_slots output is
    untouched. This only covers what the serial join could not reach — a SAS
    drive before SMART has published its serial, or a perccli build that prints
    no per-drive detail section (F-134).

    `enc` is a display prefix only; the caller passes the enclosure number the
    vendor tool already uses so labels stay '9:0', not '0:0'. Slot numbers come
    from blockdev.sas_bay_slots(), which needs no serial at all, and the result
    goes through the same front-panel remap as every other bay.
    """
    from . import blockdev
    if slots is None:
        slots = blockdev.sas_bay_slots()
    if not slots:
        return
    slots_hint = detect_slots(f"{enc}:{s}" for s in slots.values())  # F-140
    for d in disks:
        if d.bay or d.dev not in slots:
            continue
        d.bay = remap_slot(f"{enc}:{slots[d.dev]}", panels, slots_hint)


def remap_slot(enc_slot: str, panels: list, slots_hint: dict | None = None) -> str:
    """Remap a sas/PERC 'enc:slot' via a front (type=sas) panel.

    Explicit "map" override wins; else the reverse-slots rule; else identity.

    The reverse-slots slot count is, in order: the panel's own
    `slots_per_enclosure` if set; else `slots_hint[enclosure]` (auto-detected
    by the caller from every disk it is about to map, via detect_slots());
    else 8. A slot outside [0, n) would flip the reverse math negative — e.g.
    slot 12 against the 8-slot default renders as the bogus '32:-5' on a
    24-bay R740xd/HBA330 chassis — so that case SKIPS the rule (identity label)
    and warns once per (enclosure, n) rather than once per disk (F-140).
    """
    for p in _panels(panels, "sas"):
        table = p.get("map") or {}
        if isinstance(table, dict) and enc_slot in table:
            return table[enc_slot]
        if p.get("reverse_slots"):
            try:
                enc, slot_s = enc_slot.split(":")
                slot = int(slot_s)
                guessed = False
                if "slots_per_enclosure" in p:
                    n = int(p["slots_per_enclosure"])
                elif slots_hint and enc in slots_hint:
                    n = int(slots_hint[enc])
                    guessed = True
                else:
                    n = 8
                if guessed:
                    # Auto-detect only ever sees POPULATED slots, and the count
                    # is a property of the chassis. Three disks in a 24-bay
                    # backplane detect as 3 and reverse to 0->2 instead of
                    # 0->23 — right-looking and wrong. Say so once; a wrong bay
                    # label sends someone to pull the wrong drive (§9, F-140).
                    key = ("guess", enc, n)
                    if key not in _slot_warned:
                        _slot_warned.add(key)
                        common.warn(
                            f"bay_map.json: reverse_slots is on for enclosure "
                            f"{enc} with no slots_per_enclosure — assuming {n} "
                            f"from the drives present. Pin the real bay count "
                            f"if the chassis is not fully populated.")
                if slot < 0 or slot >= n:
                    warn_key = (enc, n)
                    if warn_key not in _slot_warned:
                        _slot_warned.add(warn_key)
                        common.warn(
                            f"bay_map.json: slots_per_enclosure={n} but slot "
                            f"{slot} seen on enclosure {enc} — reverse rule skipped")
                    return enc_slot
                return f"{enc}:{(n - 1) - slot}"
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
