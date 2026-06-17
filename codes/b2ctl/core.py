"""b2ctl.core — assemble the full disk picture in one place.

scan() does the whole pipeline: enumerate raw disks -> map physical bays
(sas2ircu) -> read SMART -> attach ZFS pool/vdev membership -> assess level.
Both the one-shot `status` view and the interactive `watch` loop build on it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import backend as _backend_mod, smart, zfs, spec
from .common import Disk, assess


def scan(tbw_table=None) -> list[Disk]:
    bk = _backend_mod.get_backend()
    bm = bk.bay_map() if bk.have_tool() else {}
    disks = bk.enumerate_disks()
    bk.attach_bays(disks, bm=bm)

    potential_ghosts = bk.get_ghost_disks(disks, bm=bm)
    rescued_any = False
    survivors: list[Disk] = []
    if potential_ghosts:
        with ThreadPoolExecutor(max_workers=len(potential_ghosts)) as executor:
            results = list(executor.map(bk.udev_rescue_ghost,
                                        [g.serial for g in potential_ghosts]))
        rescued_any = any(results)
        survivors = [g for g, ok in zip(potential_ghosts, results) if not ok]
        for g in survivors:
            g.reasons = ["OS_REJECTED", "udevadm rescue failed — wipe via [a]ssign"]

    if rescued_any:
        disks = bk.enumerate_disks()
        bk.attach_bays(disks, bm=bm)
        survivors = bk.get_ghost_disks(disks, bm=bm)
        for g in survivors:
            g.reasons = ["OS_REJECTED", "udevadm rescue failed — wipe via [a]ssign"]
    disks.extend(survivors)

    tbw_table = tbw_table if tbw_table is not None else spec.load()
    smart_targets = [d for d in disks if d.health != "GHOST"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda d: smart.read(d, tbw_table), smart_targets))

    # Enterprise SAS drives often have no SERIAL in lsblk (udev doesn't query it).
    # SMART now has the real serial. Re-run bay assignment so those disks get a bay,
    # then drop ghosts whose serial is now covered by a real block device.
    bk.attach_bays([d for d in disks if d.health != "GHOST"], bm=bm)
    real_serials = {d.serial for d in disks if d.health != "GHOST" and d.serial}
    disks = [d for d in disks
             if not (d.health == "GHOST" and d.serial in real_serials)]

    zfs.attach_membership(disks, zfs.topology())

    inuse_spare_pools = {d.pool for d in disks
                         if d.is_spare and d.vdev_state == "INUSE" and d.pool}
    if inuse_spare_pools:
        tok_to_bay = {d.pool_token: d.bay
                      for d in disks if d.pool_token and d.bay}
        for pool in inuse_spare_pools:
            replacing_map = zfs.spares_replacing(pool)
            for d in disks:
                if d.pool == pool and d.is_spare and d.pool_token in replacing_map:
                    replaced_tok = replacing_map[d.pool_token]
                    d.spare_replacing = tok_to_bay.get(replaced_tok)

    for d in disks:
        if d.health != "GHOST":
            assess(d)
    disks.sort(key=lambda d: (d.bay or "zz", d.dev))
    return disks


def scan_one(dev: str, tbw_table) -> Disk:
    """Build a single Disk (used when a hot-plugged device appears)."""
    for d in scan(tbw_table):
        if d.dev == dev:
            return d
    # fall back to a bare disk if it vanished again
    return Disk(dev=dev)
