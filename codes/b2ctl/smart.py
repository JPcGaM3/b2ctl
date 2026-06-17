"""b2ctl.smart — SMART reading over a raw HBA (no megaraid passthrough).

Reads ``smartctl -a /dev/sdX`` directly. Handles SATA/ATA and SAS, SSD and
HDD. Endurance for SSDs is derived two ways: the SMART wear attribute
(normalised "life remaining"), and a TBW estimate (bytes written vs rated TBW
from the spec table). HDDs report wear/endurance as N/A and lean on
reallocated/pending/defect counts instead.
"""

from __future__ import annotations

import re

from .common import Disk, run
from . import spec as spec_mod

LBA_BYTES = 512
WEAR_ATTR_IDS = [177, 233, 202, 231, 173, 169, 232]


def _smartctl(dev: str) -> str:
    from . import config as _cfg
    _sc = _cfg.tool("smartctl")
    for dtype in (None, "sat", "scsi"):
        cmd = [_sc, "-a", dev] if dtype is None \
            else [_sc, "-a", "-d", dtype, dev]
        o = run(cmd)
        if o and ("ATTRIBUTE_NAME" in o or "Health Status" in o
                  or "SMART overall-health" in o):
            return o
    return ""


def read(d: Disk, tbw_table: dict) -> None:
    """Populate SMART-derived fields on a Disk in place."""
    out = _smartctl(d.dev)
    if not out:
        d.readable = False
        d.health = "NOREAD"
        return
    d.readable = True

    # rotation -> HDD vs SSD (overrides lsblk if present)
    if re.search(r"Rotation Rate:\s*\d+\s*rpm", out, re.I):
        d.is_ssd = False
    elif re.search(r"Rotation Rate:\s*Solid State", out, re.I):
        d.is_ssd = True

    # overall health
    m = re.search(r"test result:\s*(\w+)", out)
    if m:
        d.health = m.group(1).upper()
    else:
        m = re.search(r"SMART Health Status:\s*(\w+)", out)
        if m:
            d.health = "PASSED" if m.group(1).upper() == "OK" else m.group(1).upper()

    if "ATTRIBUTE_NAME" in out:
        _parse_ata(d, out)
    elif "NVMe" in out and "SMART/Health Information" in out:
        _parse_nvme(d, out)
    else:
        _parse_sas(d, out)

    _endurance(d, tbw_table)


def _parse_ata(d: Disk, out: str) -> None:
    d.iface = d.iface or "SATA"
    m = re.search(r"Device Model:\s*(.+)", out)
    if m and not d.model:
        d.model = m.group(1).strip()
    m = re.search(r"Serial Number:\s*(.+)", out)
    if m and not d.serial:
        d.serial = m.group(1).strip()
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 10 and p[0].isdigit():
            aid = int(p[0])
            try:
                val = int(p[3])
                raw = int(re.sub(r"\D", "", p[9]) or 0)
            except ValueError:
                continue
            if aid in WEAR_ATTR_IDS and d.wear_val is None:
                d.wear_val = val
            if aid == 9:
                d.poh = raw
            if aid == 241:
                d.lba_written = raw
            if aid == 5:
                d.realloc = raw
            if aid == 197:
                d.pending = raw
            if aid in (187, 188, 198):
                d.uncorr = max(d.uncorr, raw)


def _parse_sas(d: Disk, out: str) -> None:
    d.iface = d.iface or "SAS"
    m = re.search(r"Serial number:\s*(.+)", out, re.I)
    if m and not d.serial:
        d.serial = m.group(1).strip()
    ven = re.search(r"^Vendor:\s*(.+)", out, re.M)
    prod = re.search(r"^Product:\s*(.+)", out, re.M)
    model = " ".join(x.group(1).strip() for x in (ven, prod) if x).strip()
    if model and not d.model:
        d.model = model
    m = re.search(r"Percentage used endurance indicator:\s*(\d+)", out)
    if m:
        d.wear_val = max(0, 100 - int(m.group(1)))
    m = re.search(r"(?:Accumulated power on time, hours:minutes|"
                  r"number of hours powered up)\D*(\d+)", out)
    if m:
        d.poh = int(m.group(1))
    mw = re.search(r"^write:\s+(?:\d+\s+){5}(\d+(?:\.\d+)?)", out, re.M)
    if mw:
        d.lba_written = int((float(mw.group(1)) * 1e9) / LBA_BYTES)
    md = re.search(r"Elements in grown defect list:\s*(\d+)", out, re.I)
    if md:
        d.realloc = int(md.group(1))


def _parse_nvme(d: Disk, out: str) -> None:
    d.iface = d.iface or "NVMe"
    m = re.search(r"Model Number:\s*(.+)", out)
    if m and not d.model:
        d.model = m.group(1).strip()
    m = re.search(r"Serial Number:\s*(.+)", out)
    if m and not d.serial:
        d.serial = m.group(1).strip()
    m = re.search(r"Percentage Used:\s*(\d+)", out)
    if m:
        d.wear_val = max(0, 100 - int(m.group(1)))
    m = re.search(r"Power On Hours:\s*([\d,]+)", out)
    if m:
        d.poh = int(m.group(1).replace(",", ""))
    m = re.search(r"Data Units Written:\s*([\d,]+)", out)
    if m:
        # NVMe units are 1000 * 512 bytes, so exactly 1000 LBAs
        d.lba_written = int(m.group(1).replace(",", "")) * 1000
    m = re.search(r"Media and Data Integrity Errors:\s*([\d,]+)", out)
    if m:
        d.uncorr = max(d.uncorr, int(m.group(1).replace(",", "")))


def _endurance(d: Disk, tbw_table: dict) -> None:
    if d.lba_written is not None:
        d.written_tb = d.lba_written * LBA_BYTES / 1e12
    if not d.is_ssd:
        d.wear_val = None       # HDD: wear is meaningless
        return
    d.tbw_rating = spec_mod.lookup(d.model, tbw_table)
    if d.tbw_rating and d.written_tb is not None:
        d.end_left = max(0.0, min(100.0,
                         (d.tbw_rating - d.written_tb) / d.tbw_rating * 100))
