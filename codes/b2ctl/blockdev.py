"""b2ctl.blockdev — shared, backend-agnostic block-device listing (lsblk).

`lsblk_pairs`, `EXCLUDE` and `vd_usage` used to live in hba (the IT-mode module)
and were reached into by watch's hotplug baseline and core's VD-usage — a
layering leak that broke the Backend abstraction (F-099). They belong here,
above both backends: hba, watch and core all import from this module, so a third
backend (or a rename of hba internals) no longer breaks hotplug at runtime.

Keep the `-P` (KEY="value") form — CLAUDE.md §6: positional lsblk parsing breaks
because MODEL contains spaces.
"""
from __future__ import annotations

import glob
import os
import re

from .common import run

# Kernel SAS transport class: every end device exposes the bay its expander
# reports, and the block device hangs off the SAME node.
SAS_DEVICE_DIR = "/sys/class/sas_device"

# Device-name prefixes that are never physical disks (loop/zram/dm/md/...).
EXCLUDE = ("loop", "sr", "ram", "zd", "dm-", "md")
_PAIR_RE = re.compile(r'(\w+)="(.*?)"')


def _tool(name: str) -> str:
    """Resolve a binary through config.tool() so tool_paths overrides for
    lsblk are honored (F-035)."""
    from . import config as _cfg
    return _cfg.tool(name)


def lsblk_pairs(cols: str) -> list[dict]:
    """Parse `lsblk -P` KEY="value" lines into dicts (robust to spaces)."""
    out = run([_tool("lsblk"), "-dnb", "-P", "-o", cols])
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append(dict(_PAIR_RE.findall(line)))
    return rows


def sas_bay_slots() -> dict:
    """Return {'/dev/sdX': slot} straight from the kernel's SAS transport class.

    `/sys/class/sas_device/end_device-*/bay_identifier` is the slot the expander
    reports for that end device, and its block device lives under the SAME sysfs
    node — so this is an EXACT device->slot map with no serial matching anywhere.
    That removes the whole failure class F-133 was: lsblk publishes no SERIAL for
    enterprise SAS drives until SMART runs, and tools truncate serials
    differently, so every serial-keyed bay map can miss. This one cannot (F-134).

    Nodes with no block device drop out on their own — which is how the SES
    enclosure processor (a real end device, bay 24 on the field HBA330) excludes
    itself, the same phantom-row hazard F-036 had to special-case for sas2ircu.

    Returns {} when there is no SAS transport (pure SATA/NVMe box) or when the
    backplane reports one constant for every drive (a useless map).
    """
    slots: dict[str, int] = {}
    for end in sorted(glob.glob(os.path.join(SAS_DEVICE_DIR, "end_device-*"))):
        try:
            with open(os.path.join(end, "bay_identifier")) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if not raw.isdigit():
            continue
        names = sorted(os.path.basename(p) for p in
                       glob.glob(os.path.join(end, "device", "target*", "*", "block", "*")))
        if not names:
            continue                       # enclosure processor / no block device
        slots[f"/dev/{names[0]}"] = int(raw)
    if len(slots) > 1 and len(set(slots.values())) == 1:
        return {}                          # backplane reports a constant bay
    return slots


def vd_usage(dev: str) -> tuple[int, int] | None:
    """Return (used_bytes, size_bytes) of the mounted filesystem on a block
    device (e.g. a PERC virtual disk presented as /dev/sdX), or None if nothing
    is mounted. Includes children (the FS usually lives on a partition), so this
    does NOT use `-d`. If several filesystems are mounted, the largest wins.
    """
    out = run([_tool("lsblk"), "-b", "-P", "-o", "NAME,FSUSED,FSSIZE,MOUNTPOINT", dev])
    best = None
    for line in out.splitlines():
        if not line.strip():
            continue
        row = dict(_PAIR_RE.findall(line))
        if row.get("MOUNTPOINT") and row.get("FSSIZE"):
            try:
                size = int(row["FSSIZE"])
                used = int(row.get("FSUSED") or 0)
            except ValueError:
                continue
            if best is None or size > best[1]:
                best = (used, size)
    return best
