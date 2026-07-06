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


SMART_TIMEOUT = 10   # per-probe; a hung disk must not stall the whole scan (F-049)


def _smartctl(dev: str, dtype: str = "") -> str:
    from . import config as _cfg
    _sc = _cfg.tool("smartctl")
    # When a device type is forced (RAID-mode "megaraid,7"), the drive is a hidden
    # member with no valid raw identity: try only the passthrough forms, never the
    # auto-detect ladder (that would read the shared VD node and misattribute the
    # VD's SMART to a dead member — F-049). IT-mode uses the raw ladder.
    if dtype:
        attempts: list[str | None] = [dtype, f"sat+{dtype}"]
    else:
        attempts = [None, "sat", "scsi"]
    for dt in attempts:
        cmd = [_sc, "-a", dev] if dt is None \
            else [_sc, "-a", "-d", dt, dev]
        o = run(cmd, timeout=SMART_TIMEOUT, none_on_timeout=True)
        if o is None:
            break  # the device timed out; retrying the same hung drive only stalls
        if o and ("ATTRIBUTE_NAME" in o or "Health Status" in o
                  or "SMART overall-health" in o):
            return o
    return ""


def read(d: Disk, tbw_table: dict) -> None:
    """Populate SMART-derived fields on a Disk in place."""
    out = _smartctl(d.dev, d.smart_dtype)
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
        # SCSI/SAS: the status can be a multi-word phrase, e.g.
        # 'FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]'. Anything that
        # is not a bare 'OK' means the drive itself is predicting failure, so map
        # it to FAILED (which assess() grades CRITICAL) rather than storing the
        # first word 'FAILURE', which no rule matched (F-018).
        m = re.search(r"SMART Health Status:\s*(.+)", out)
        if m:
            status = m.group(1).strip()
            d.health = "PASSED" if status.upper() == "OK" else "FAILED"

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
    if m:
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
            except ValueError:
                continue
            # Take only the LEADING integer of the raw token: composite raws like
            # Seagate's '29229h+18m+27.459s' or '30 (Min/Max 25/45)' must not have
            # every digit concatenated into one absurd number (F-050).
            rm = re.match(r"\d+", p[9])
            if not rm:
                continue
            raw = int(rm.group())
            if aid in WEAR_ATTR_IDS and d.wear_val is None:
                d.wear_val = val
            if aid == 9:
                d.poh = raw
            if aid == 241:
                # Attribute 241 units vary by vendor; the name (p[1]) reveals them.
                # Normalise everything to 512-byte LBAs so _endurance's LBA_BYTES
                # math is correct for Intel (32MiB) / SanDisk (GB) drives (F-051).
                d.lba_written = _lba241(p[1], raw)
            if aid == 5:
                d.realloc = raw
            if aid == 197:
                d.pending = raw
            if aid in (187, 188, 198):
                d.uncorr = max(d.uncorr, raw)


def _lba241(name: str, raw: int) -> int:
    """Convert an attribute-241 raw to 512-byte LBA-equivalents by unit name."""
    n = (name or "").lower()
    if "32mib" in n:
        return raw * (32 * 1024 * 1024) // LBA_BYTES
    if "gib" in n:
        return raw * (1024 ** 3) // LBA_BYTES
    if "gb" in n:
        return raw * (10 ** 9) // LBA_BYTES
    return raw   # plain Total_LBAs_Written already in 512-byte sectors


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
    # SAS error-counter log: column 7 of read/write/verify rows is 'total
    # uncorrected errors' — the standard media-failure signal (F-095). Column 6
    # (GB processed) has a decimal, so use \S+ for the skipped columns.
    for mu in re.finditer(r"^(?:read|write|verify):\s+(?:\S+\s+){6}(\d+)", out, re.M):
        d.uncorr = max(d.uncorr, int(mu.group(1)))


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
