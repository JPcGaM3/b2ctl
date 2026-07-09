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


SMART_TIMEOUT = 10   # per-probe default; overridable via config['smart']['timeout'] (F-049)


def _smartctl(dev: str, dtype: str = "") -> str:
    from . import config as _cfg
    _sc = _cfg.tool("smartctl")
    timeout = _cfg.smart_config().get("timeout", SMART_TIMEOUT)
    # When a device type is forced (RAID-mode "megaraid,7"), the drive is a hidden
    # member with no valid raw identity: try only the passthrough forms, never the
    # auto-detect ladder (that would read the shared VD node and misattribute the
    # VD's SMART to a dead member — F-049). IT-mode uses the raw ladder.
    if dtype:
        attempts: list[str | None] = [dtype, f"sat+{dtype}"]
    else:
        attempts = [None, "sat", "scsi"]
    # megaraid passthrough shares one controller: a probe can time out purely from
    # queueing behind other probes, so retry it ONCE. A raw/IT-mode disk that times
    # out is genuinely hung — don't re-probe it (F-049).
    tries = 2 if dtype else 1
    for dt in attempts:
        cmd = [_sc, "-a", dev] if dt is None \
            else [_sc, "-a", "-d", dt, dev]
        o = None
        for _ in range(tries):
            o = run(cmd, timeout=timeout, none_on_timeout=True)
            if o is not None:
                break                     # got a response (timeout is the only retry trigger)
        if o is None:
            break  # still timed out after retries — a hung drive; give up (F-049)
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

    # Surface a running burn-in self-test in the status table, reusing THIS -a
    # output — no extra subprocess (parse_selftest is pure). See burnin.py.
    from . import burnin, ui
    stt = burnin.parse_selftest(out)
    if stt["running"]:
        d.selftest_running = True
        d.selftest_pct = stt["pct"]
        d.selftest_eta = ui.fmt_eta(stt["eta_min"])

    # Last COMPLETED long self-test from the drive's OWN self-test LOG (same -a
    # output, no extra subprocess). Feeds the HEALTH_CHK column passively — the
    # drive persists this across reboots, indexed by power-on hours (v0.17.0).
    res, hrs = _parse_selftest_log(out)
    if res:
        d.selftest_last_result = res
        d.selftest_last_poh = hrs


def _parse_selftest_log(out: str) -> tuple[str, int | None]:
    """Return (result, lifetime_hours) of the most recent COMPLETED long
    self-test from the SMART self-test LOG, or ('', None).

    'long' = ATA 'Extended' or SAS 'Background long' or NVMe 'Extended'. smartctl
    lists newest first, so the first matching row wins. Three table shapes are
    handled (best-effort / version-tolerant):
      ATA/SAS  '# N  Extended offline  Completed …  … LifeTime(hours)'
      NVMe     'Self-test Log (NVMe Log 0x06)' header, then bare-index rows
               ' N  Extended  Completed without error  <Power_on_Hours> …'
    In every shape, once the leading index is stripped the columns split on
    2+-space gaps and _selftest_row() reads the description (col 0), status
    (col 1) and the last all-digit column as lifetime hours. Short/conveyance
    tests are ignored — only the long test counts as a health check."""
    in_nvme = False
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("#"):                          # ATA / SAS row
            r = _selftest_row(re.sub(r"^#\s*\d+\s+", "", s))
            if r:
                return r
        elif re.search(r"Self-test Log \(NVMe", s, re.I):
            in_nvme = True                             # NVMe self-test block opens
        elif in_nvme and re.match(r"\d", s):           # NVMe row: bare index, no '#'
            r = _selftest_row(re.sub(r"^\d+\s+", "", s))
            if r:
                return r
    return "", None


def _selftest_row(body: str) -> tuple[str, int | None] | None:
    """Parse ONE self-test-log row body (leading index already stripped): columns
    split on 2+-space gaps -> (status, lifetime_hours) for a LONG test, else
    None (short/conveyance rows, or too few columns)."""
    cols = re.split(r"\s{2,}", body)                   # columns are 2+-space gaps
    if len(cols) < 2 or not re.search(r"extended|background long", cols[0], re.I):
        return None
    hours = None
    for c in reversed(cols):
        if c.strip().isdigit():
            hours = int(c.strip())
            break
    return cols[1].strip(), hours


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
