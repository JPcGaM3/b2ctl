"""b2ctl.hba — the HBA (IT-mode) backend.

On a crossflashed LSI SAS9207-8i there is no RAID controller CLI: disks are
raw. This module enumerates them straight from the kernel (lsblk), resolves
stable /dev/disk/by-id names, maps each disk to its physical bay via
``sas2ircu DISPLAY`` (serial -> enclosure:slot), and drives the locate LED via
``sas2ircu LOCATE``. Everything degrades gracefully if sas2ircu is absent
(bay shows as '-').
"""

from __future__ import annotations

import glob
import os
import re

from .common import Disk, run

CONTROLLER = 0          # sas2ircu controller index
_EXCLUDE = ("loop", "sr", "ram", "zd", "dm-", "md")
_PAIR_RE = re.compile(r'(\w+)="(.*?)"')


def _lsblk_pairs(cols: str) -> list[dict]:
    """Parse `lsblk -P` KEY="value" lines into dicts (robust to spaces)."""
    out = run(["lsblk", "-dnb", "-P", "-o", cols])
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append(dict(_PAIR_RE.findall(line)))
    return rows


def _nvme_pcie(name: str) -> str | None:
    """PCIe BDF for an nvme namespace, e.g. 'nvme0n1' -> 'd8:00.0' (domain dropped)."""
    m = re.match(r"(nvme\d+)", name)
    if not m:
        return None
    try:
        with open(f"/sys/class/nvme/{m.group(1)}/address") as f:
            addr = f.read().strip()
    except OSError:
        return None
    return addr.split(":", 1)[1] if addr.startswith("0000:") else addr


def enumerate_disks() -> list[Disk]:
    """Return one Disk per physical block device (sd*/nvme*)."""
    from . import baymap
    byid = _by_id_index()
    panels = baymap.load()
    disks: list[Disk] = []
    for row in _lsblk_pairs("NAME,SIZE,SERIAL,MODEL,TRAN,ROTA,TYPE"):
        name = row.get("NAME", "")
        if row.get("TYPE") != "disk" or name.startswith(_EXCLUDE):
            continue
        dev = f"/dev/{name}"
        d = Disk(dev=dev)
        try:
            d.size_bytes = int(row.get("SIZE") or 0) or None
        except ValueError:
            d.size_bytes = None
        d.serial = (row.get("SERIAL") or "").strip()
        d.model = (row.get("MODEL") or "").strip()
        d.iface = (row.get("TRAN") or "").strip().upper()
        d.is_ssd = (row.get("ROTA") == "0")
        d.by_id = byid.get(os.path.realpath(dev), "")
        # NVMe has no enclosure:slot — use its PCIe address (relabel-able via the
        # back/type=nvme panel in bay_map.json).
        if name.startswith("nvme"):
            bdf = _nvme_pcie(name)
            if bdf:
                d.bay = baymap.remap_nvme(bdf, panels)
        disks.append(d)
    return disks


def _by_id_index() -> dict:
    """Map realpath(/dev/sdX) -> preferred /dev/disk/by-id link (ata- > wwn-)."""
    index: dict[str, str] = {}
    bydir = "/dev/disk/by-id"
    if not os.path.isdir(bydir):
        return index
    rank = {"ata-": 0, "scsi-SATA": 1, "wwn-": 2, "scsi-": 3}

    def score(name: str) -> int:
        for pfx, s in rank.items():
            if name.startswith(pfx):
                return s
        return 9

    best: dict[str, tuple] = {}
    for name in os.listdir(bydir):
        if "-part" in name:
            continue
        link = os.path.join(bydir, name)
        try:
            real = os.path.realpath(link)
        except OSError:
            continue
        s = score(name)
        if real not in best or s < best[real][0]:
            best[real] = (s, link)
    for real, (_, link) in best.items():
        index[real] = link
    return index


# --------------------------------------------------------------------------- #
# sas2ircu — physical bay mapping + LED locate
# --------------------------------------------------------------------------- #
def have_sas2ircu() -> bool:
    from . import config as _cfg
    return bool(run([_cfg.tool("sas2ircu"), "list"]))


def bay_map(controller: int = CONTROLLER) -> dict:
    """serial -> 'enclosure:slot' from `sas2ircu <c> DISPLAY`."""
    from . import config as _cfg
    out = run([_cfg.tool("sas2ircu"), str(controller), "DISPLAY"])
    mapping: dict[str, str] = {}
    enc = slot = serial = None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"Enclosure #\s*:\s*(\d+)", s)
        if m:
            enc = m.group(1); slot = serial = None; continue
        m = re.match(r"Slot #\s*:\s*(\d+)", s)
        if m:
            slot = m.group(1); continue
        m = re.match(r"Serial No\s*:\s*(\S+)", s)
        if m:
            serial = m.group(1)
            if enc is not None and slot is not None:
                mapping[serial] = f"{enc}:{slot}"
    return mapping


def attach_bays(disks: list[Disk], controller: int = CONTROLLER, bm=None) -> None:
    """Fill disk.bay from sas2ircu, matching on serial, then remap to the
    physical chassis label.

    On Dell 12G backplanes (R620/R720) flashed to LSI IT mode the controller
    reports scrambled slot numbers: the Dell firmware's slot-translation map is
    gone, so LSI shows the raw, reordered values. This is a known issue. The
    bay is display-only here (LEDs are driven by device, not slot — see
    locate.py), so we remap purely for the human label using bay_map.json.
    """
    from . import baymap
    if not have_sas2ircu():
        return
    panels = baymap.load()
    if bm is None:
        bm = bay_map(controller)
    for d in disks:
        if d.serial:
            if d.serial in bm:
                d.bay = baymap.remap_slot(bm[d.serial], panels)
            else:
                for bm_serial, bay in bm.items():
                    if d.serial.startswith(bm_serial) or bm_serial.startswith(d.serial):
                        d.bay = baymap.remap_slot(bay, panels)
                        break


def get_ghost_disks(disks: list[Disk], controller: int = CONTROLLER, bm=None) -> list[Disk]:
    """Find physical disks that the HBA sees but Linux rejected (no block dev)."""
    from . import baymap
    if not have_sas2ircu():
        return []
    panels = baymap.load()
    if bm is None:
        bm = bay_map(controller)
    os_serials = [d.serial for d in disks if d.serial]
    ghosts = []
    for serial, raw_bay in bm.items():
        matched = False
        for os_serial in os_serials:
            if os_serial.startswith(serial) or serial.startswith(os_serial):
                matched = True
                break
        if not matched:
            d = Disk(dev="-")
            d.bay = baymap.remap_slot(raw_bay, panels)
            d.serial = serial
            d.model = "(Ghost / OS Rejected)"
            d.health = "GHOST"
            d.level = "CRITICAL"
            d.reasons = ["OS_REJECTED"]
            ghosts.append(d)
    return ghosts


def _read_sg_serial(sg_path: str, sg_dev: str) -> str:
    """Try VPD page 80 from sysfs, then smartctl -i as fallback."""
    vpd_path = os.path.join(sg_path, "device", "vpd_pg80")
    if os.path.exists(vpd_path):
        try:
            data = open(vpd_path, "rb").read()
            if len(data) >= 4:
                length = int.from_bytes(data[2:4], "big")
                return data[4:4 + length].decode("ascii", errors="replace").strip()
        except OSError:
            pass
    from . import config as _cfg
    out = run([_cfg.tool("smartctl"), "-i", sg_dev])
    for line in out.splitlines():
        if "Serial Number" in line:
            return line.split(":", 1)[-1].strip()
    return ""


def find_sg_for_ghost(serial: str) -> str | None:
    """Return /dev/sgX matching serial, or None.

    Scans ALL sg devices — does not filter by block-device presence because
    dm-multipath can claim a disk (creating dm-X in the block dir) while the
    disk is still accessible via its sg node for direct writes.
    """
    for sg_path in sorted(glob.glob("/sys/class/scsi_generic/sg*")):
        sg_name = os.path.basename(sg_path)
        sg_dev = f"/dev/{sg_name}"
        sg_serial = _read_sg_serial(sg_path, sg_dev)
        if sg_serial and serial and (serial in sg_serial or sg_serial in serial):
            return sg_dev
    return None


def udev_rescue_ghost(serial: str) -> bool:
    """Attempt udev rescue for a disk that sas2ircu sees but lsblk doesn't.

    Triggers the sg device's SCSI parent in udev, waits for settle, then checks
    if lsblk now shows a disk with this serial. Returns True if rescued.
    """
    sg = find_sg_for_ghost(serial)
    if sg:
        sg_name = os.path.basename(sg)
        dev_path = f"/sys/class/scsi_generic/{sg_name}/device"
        if os.path.exists(dev_path):
            run(["udevadm", "trigger", "--action=add", dev_path])
    run(["udevadm", "settle", "--timeout=3"])
    for row in _lsblk_pairs("NAME,SERIAL,TYPE"):
        if row.get("TYPE") == "disk" and row.get("SERIAL", "").strip() == serial:
            return True
    return False


# bay_map.json parsing + remap now live in b2ctl.baymap (shared by both backends).
