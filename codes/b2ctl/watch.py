"""b2ctl.watch — interactive disk-management loop.

Shows the health table, then watches for hot-plugged / pulled disks every few
seconds. When a new disk appears it pauses and asks what to do (spare /
replace / wipe / skip). At any time you can type a command:

    r  refresh the table
    s  swap a wearing disk onto a hot spare
    l  locate (blink) a bay's LED
    q  quit

Built on select() so keystrokes and hotplug events are both handled in one
loop with no extra dependencies.
"""

from __future__ import annotations

import os
import select
import sys
import time

from . import core, hba, zfs, spec, locate
from .common import Disk, R, Y, G, C, N
from . import ui


def _pool_dev(d) -> str:
    """Identifier to use for a device that is ALREADY in a pool."""
    return d.pool_token or d.by_id or d.dev

POLL = 2.0


def _block_devs() -> set:
    devs = set()
    for row in hba._lsblk_pairs("NAME,TYPE"):
        name = row.get("NAME", "")
        if row.get("TYPE") == "disk" and not name.startswith(hba._EXCLUDE):
            devs.add(name)
    return devs


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _pick_pool() -> str | None:
    pools = zfs.list_pools()
    if not pools:
        print(f"{Y}  no ZFS pools found{N}")
        return None
    if len(pools) == 1:
        return pools[0]["name"]
    for i, p in enumerate(pools, 1):
        print(f"    [{i}] {p['name']} ({p['health']})")
    sel = _ask("  pool #> ")
    try:
        return pools[int(sel) - 1]["name"]
    except (ValueError, IndexError):
        return None


def _confirm(msg: str) -> bool:
    return _ask(f"{Y}  {msg} [y/N]> {N}").lower() in ("y", "yes")


def _confirm_op(op, disk_from, disk_to, pool, vdev, cmds, snap_path=None):
    """Enhanced confirmation box showing op, disk IDs, and exact commands."""
    import textwrap
    width = 52
    border = "─" * width
    print(f"┌─ CONFIRM OPERATION {border[:width-20]}┐")

    def _row(label, val):
        line = f"│ {label:<8} {val}"
        print(line[:width+1].ljust(width + 1) + "│")

    _row("Op:", op)
    if disk_from:
        _row("From:", f"bay {disk_from.bay} │ {disk_from.serial} │ {disk_from.pool or 'AVAILABLE'}")
    if disk_to:
        _row("To:", f"bay {disk_to.bay} │ {disk_to.serial} │ {disk_to.pool or 'AVAILABLE'}")
    _row("Pool:", f"{pool}/{vdev}")
    print(f"│{'':^{width}}│")
    print(f"│ {'Will run:':<{width-1}}│")
    for cmd in cmds:
        joined = " ".join(cmd)
        chunks = textwrap.wrap(joined, width - 4) or [joined]
        for chunk in chunks:
            print(f"│   {chunk:<{width-3}}│")
    if snap_path:
        snap_short = snap_path[-44:] if len(snap_path) > 44 else snap_path
        _row("Snap:", snap_short)
    print(f"└{'─'*width}┘")
    ans = input("Proceed? [y/N]: ").strip().lower()
    return ans in ("y", "yes")


# --------------------------------------------------------------------------- #
# event: a new disk appeared
# --------------------------------------------------------------------------- #
def _handle_new_disk(dev: str, tbw) -> None:
    time.sleep(2)  # let udev/SMART settle
    d = core.scan_one(dev, tbw)
    print(f"\n{G}╔══ NEW DISK DETECTED: {dev} ═══════════════════════{N}")
    print(ui.render_new_disk(d))
    print(f"{G}╚════════════════════════════════════════════════════{N}")
    if not d.by_id:
        print(f"{Y}  no stable by-id yet — skipping (re-insert if needed){N}")
        return
    _assign_free_disk(d, tbw)


def _assign_free_disk(d, tbw, all_disks=None) -> None:
    print(f"\n{G}  Disk {d.by_id or d.dev} is free.{N}")
    print("  What do you want to do with it?")
    print("    [1] Prepare for physical removal (Blink LED)")
    print("    [2] Add to a pool as hot SPARE")
    print("    [3] REPLACE a degraded/faulted disk in a pool")
    print("    [4] ATTACH to an existing disk (convert to/expand mirror)")
    print("    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)")
    print("    [6] WIPE it blank (for a new pool)")
    print("    [s] skip / decide later")
    choice = _ask("  action> ")

    if choice == "1":
        print(G + f"  ✔ Blinking {d.dev}..." + N)
        locate.blink(d.dev, locate.DEFAULT_SECONDS)
    elif choice == "2":
        pool = _pick_pool()
        if pool and _confirm(f"add {ui.disk_label(d)} to '{pool}' as spare?"):
            ok, out = zfs.add_spare(pool, d.by_id or d.dev)
            print((G + "  ✔ added as spare" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "3":
        bad = zfs.degraded_leaves()
        if not bad:
            print(f"{Y}  no degraded/faulted devices to replace{N}")
            return
        for i, e in enumerate(bad, 1):
            print(f"    [{i}] {e['pool']}: {e['token']} ({e['state']})")
        sel = _ask("  replace #> ")
        try:
            tgt = bad[int(sel) - 1]
        except (ValueError, IndexError):
            print(f"{Y}  cancelled{N}"); return
        if _confirm(f"replace {tgt['token']} in '{tgt['pool']}' with {ui.disk_label(d)}?"):
            ok, out = zfs.replace(tgt["pool"], tgt["token"], d.by_id or d.dev)
            if not ok:
                print(R + f"  ✗ failed: {out}" + N)
                return
            print(G + "  ✔ replace started — resilvering" + N)
            pool = tgt["pool"]
            while True:
                time.sleep(2)
                st = zfs.poll_resilver_status(pool)
                if st["completed"]:
                    sys.stdout.write(f"\r{G}  ✔ resilver completed{N}                    \n")
                    break
                sys.stdout.write(f"\r{Y}  resilvering... {st['done']}% done, ETA {st['eta']}{N}")
                sys.stdout.flush()
            # detach lingering REMOVED token if still in topology
            old_token = tgt["token"]
            topo = zfs.topology()
            if any(e["pool"] == pool and e["token"] == old_token for e in topo.values()):
                ok_d, out_d = zfs.detach(pool, old_token)
                if ok_d:
                    print(G + f"  ✔ detached old token {old_token}" + N)
                else:
                    print(R + f"  ✗ detach failed: {out_d}" + N)
            avail = zfs.spares(pool)
            if avail:
                print(G + f"  ✔ spare restored to AVAIL: {', '.join(avail)}" + N)
    elif choice == "4":
        pool = _pick_pool()
        if pool:
            in_pool = [x for x in (all_disks if all_disks is not None else core.scan(tbw)) if x.pool == pool]
            if not in_pool:
                print(f"{Y}  no disks found in pool '{pool}'{N}")
                return
            for i, x in enumerate(in_pool, 1):
                print(f"    [{i}] {ui.disk_label(x)} (vdev {x.vdev})")
            sel = _ask("  attach to which #> ")
            try:
                tgt = in_pool[int(sel) - 1]
            except (ValueError, IndexError):
                print(f"{Y}  cancelled{N}"); return
            if _confirm(f"attach {ui.disk_label(d)} to {ui.disk_label(tgt)} in '{pool}'?"):
                ok, out = zfs.attach(pool, tgt.by_id or tgt.dev, d.by_id or d.dev)
                print((G + "  ✔ attached" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "5":
        pool = _pick_pool()
        if pool:
            if _confirm(f"Adding a single disk vdev means if this disk fails, the ENTIRE pool is lost. Proceed?"):
                ok, out = zfs.run_check(["zpool", "add", "-f", pool, d.by_id or d.dev])
                print((G + "  ✔ added" if ok else R + f"  ✗ failed: {out}") + N)
    elif choice == "6":
        print(f"{R}  WIPE erases ALL data on {d.dev} (SN {d.serial or '?'}){N}")
        if _confirm(f"really wipe {ui.disk_label(d)}?"):
            ok, out = zfs.wipe(d.by_id or d.dev)
            print((G + "  ✔ wiped blank" if ok else R + f"  ✗ failed: {out}") + N)
    else:
        print("  skipped")


def _wait_for_block_device(serial: str, timeout: int = 20) -> str | None:
    """Wait for udev queue to drain, then check lsblk once for the serial."""
    hba.run(["udevadm", "settle", f"--timeout={timeout}"])
    for row in hba._lsblk_pairs("NAME,SERIAL,TYPE"):
        if row.get("TYPE") == "disk" and row.get("SERIAL", "").strip() == serial:
            return f"/dev/{row['NAME']}"
    return None


def _wipe_ghost(d, tbw) -> None:
    print(f"\n{Y}  Disk SN {d.serial} (bay {d.bay or '?'}) was rejected by the OS.{N}")
    print(f"  Likely cause: RAID metadata from a hardware RAID card.")
    print(f"  Solution: zero the RAID signatures via SCSI generic device, then wipe.")

    print(f"\n{C}  Locating SCSI generic device for SN {d.serial}...{N}")
    sg = hba.find_sg_for_ghost(d.serial)
    if not sg:
        print(R + "  ✗ no /dev/sgX found for this disk." + N)
        print(Y + "  Try re-inserting the disk, or check: ls /dev/sg*" + N)
        return
    print(G + f"  ✔ Found {sg}" + N)

    if not _confirm(f"WIPE {sg} (bay {d.bay or '?'}, SN {d.serial})? Erases ALL RAID metadata."):
        print("  cancelled")
        return

    print(f"\n  {C}[1/3]{N} Zeroing 40 MB RAID metadata on {sg} ...")
    ok, msg = zfs.wipe_sg(sg)
    if not ok:
        print(R + f"\n  ✗ failed: {msg}" + N)
        return
    print(G + "\n  ✔ zeroed." + N)

    print(f"  {C}[2/3]{N} Waiting for OS to recognize disk", end="", flush=True)
    sdx = _wait_for_block_device(d.serial, timeout=20)
    if not sdx:
        print(f"\n{Y}  Disk didn't appear within 20 s. Try re-inserting or rebooting.{N}")
        return
    print(G + f"\n  ✔ appeared as {sdx}" + N)

    print(f"  {C}[3/3]{N} Running full wipe on {sdx} ...")
    ok2, out2 = zfs.wipe(sdx)
    if ok2:
        print(G + f"  ✔ done — {sdx} is clean" + N)
    else:
        print(R + f"  ✗ wipe failed: {out2}" + N)
        return

    time.sleep(1)
    d2 = core.scan_one(sdx, tbw)
    _assign_free_disk(d2, tbw)


def _cmd_assign(tbw) -> None:
    disks = core.scan(tbw)
    avail = [d for d in disks if not d.in_pool and d.dev != "-"]
    ghosts = [d for d in disks if d.health == "GHOST"]
    avail_all = avail + ghosts
    if not avail_all:
        print(f"{Y}  no unassigned disks available to assign{N}")
        return
    for i, d in enumerate(avail_all, 1):
        if d.health == "GHOST":
            print(f"    [{i}] {R}[GHOST]{N} bay {d.bay or '?'} (SN {d.serial or '?'}) — needs wipe")
        else:
            print(f"    [{i}] bay {d.bay or '?'} {d.dev} ({d.model}, SN {d.serial or '?'})")
    sel = _ask("  assign which #> ")
    try:
        d = avail_all[int(sel) - 1]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return
    if d.health == "GHOST":
        _wipe_ghost(d, tbw)
    else:
        _assign_free_disk(d, tbw, all_disks=disks)


# --------------------------------------------------------------------------- #
# event: a disk was pulled
# --------------------------------------------------------------------------- #
def _handle_removed(devs: set) -> None:
    for dev in sorted(devs):
        print(f"\n{Y}■ disk removed: /dev/{dev}{N}")
    print(f"{C}  current pool health:{N}")
    print(ui.render_pools(zfs.list_pools()))


# --------------------------------------------------------------------------- #
# typed commands
# --------------------------------------------------------------------------- #
def _cmd_refresh(tbw) -> None:
    disks = core.scan(tbw)
    print("\n" + ui.render_table(disks))
    print(ui.render_pools(zfs.list_pools()))
    print(ui.render_details(disks))





def _cmd_offload(tbw) -> None:
    disks = core.scan(tbw)
    in_pool = [d for d in disks if d.in_pool and d.pool]
    if not in_pool:
        print(f"{Y}  no in-pool disks to offload{N}")
        return
    for i, d in enumerate(in_pool, 1):
        print(f"    [{i}] bay {d.bay or '?'} {d.dev} in {d.pool} (vdev {d.vdev})")
    sel = _ask("  offload which #> ")
    try:
        d = in_pool[int(sel) - 1]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return

    if d.vdev == "spares":
        if _confirm(f"This disk is a hot spare. Remove {ui.disk_label(d)} from '{d.pool}'?"):
            ok, out = zfs.run_check(["zpool", "remove", d.pool, _pool_dev(d)])
            if not ok:
                print(R + f"  ✗ failed: {out}" + N)
                return
            print(G + "  ✔ removed from pool" + N)
            _assign_free_disk(d, tbw)
        return

    if zfs.can_detach(d.pool, _pool_dev(d)):
        if _confirm(f"This disk is in a mirror. Detach {ui.disk_label(d)} instantly?"):
            ok, out = zfs.detach(d.pool, _pool_dev(d))
            if not ok:
                print(R + f"  ✗ failed: {out}" + N)
                return
            print(G + "  ✔ detached" + N)
            _assign_free_disk(d, tbw)
            return

    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if not spares:
        print(f"{Y}  pool '{d.pool}' has no AVAIL spare to offload onto — add one first{N}")
        return
    if _replace_onto_spare(d, spares[0]):
        _assign_free_disk(d, tbw)


def _cmd_locate(tbw) -> None:
    disks = core.scan(tbw)
    target = _ask("  locate which (bay/serial/sdX)> ")
    if not target:
        return
    dev = None
    for d in disks:
        if target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", "")):
            dev = d.dev
            break
    if not dev:
        print(f"{Y}  could not resolve '{target}'{N}")
        return
    if dev == "-":
        print(f"{R}  cannot locate a GHOST disk (OS rejected it, no /dev node){N}")
        return
    print(f"{Y}  blinking {dev} for {locate.DEFAULT_SECONDS}s ...{N}")
    ok, method = locate.blink(dev)
    print((G + f"  ✔ done (via {method})" if ok else R + "  ✗ failed") + N)



def _replace_onto_spare(d, spare) -> bool:
    pool = d.pool
    if _confirm(f"Replace {ui.disk_label(d)} onto spare {ui.disk_label(spare)}?"):
        ok, out = zfs.replace(pool, _pool_dev(d), spare.pool_token or spare.by_id)
        if not ok:
            print(R + f"  ✗ failed: {out}" + N)
            return False
        print(G + "  ✔ replace started — resilvering onto spare" + N)
        while True:
            time.sleep(2)
            st = zfs.poll_resilver_status(pool)
            if st["completed"]:
                sys.stdout.write(f"\r{G}  ✔ resilver completed 100%{N}                 \n")
                break
            sys.stdout.write(f"\r{Y}  resilvering... {st['done']}% done, ETA {st['eta']}{N}")
            sys.stdout.flush()
        
        topo = zfs.topology()
        old_token = _pool_dev(d)
        lingers = any(e["pool"] == pool and e["token"] == old_token for e in topo.values())
        if lingers:
            ok_d, out_d = zfs.detach(pool, old_token)
            if ok_d:
                print(G + f"  ✔ detached old disk {d.dev}" + N)
            else:
                print(R + f"  ✗ failed to detach old disk: {out_d}" + N)
        
        print(f"{Y}  please pull bay {d.bay or '?'} ... blinking LED{N}")
        locate.blink(d.dev, locate.DEFAULT_SECONDS)
        return True
    return False


def _cmd_replace(tbw) -> None:
    disks = core.scan(tbw)
    in_pool = [d for d in disks if d.in_pool and d.pool]
    if not in_pool:
        print(f"{Y}  no in-pool disks to replace{N}"); return
    for i, d in enumerate(in_pool, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  replace which #> ")
    try:
        d = in_pool[int(sel) - 1]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return
    
    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if not spares:
        print(f"{Y}  pool '{d.pool}' has no AVAIL spare — add one first{N}")
        return
    
    _replace_onto_spare(d, spares[0])


def _cmd_create(tbw) -> None:
    available = [d for d in core.scan(tbw) if not d.in_pool]
    if not available:
        print(f"{Y}  no available disks to create pool{N}")
        return
    for i, d in enumerate(available, 1):
        print(f"    [{i}] {d.dev} (bay {d.bay or '?'})")
    sel = _ask("  pick disks (space-separated #)> ")
    try:
        indices = [int(x) - 1 for x in sel.split()]
        devs = [available[i].by_id or available[i].dev for i in indices]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled or invalid selection{N}")
        return
    if not devs:
        return
    name = _ask("  pool name> ")
    if not name:
        return
    raid_type = _ask("  raid type (stripe, mirror, raidz1, raidz2) [mirror]> ") or "mirror"
    if raid_type not in ("stripe", "mirror", "raidz1", "raidz2"):
        print(f"{R}  invalid raid type{N}")
        return
    min_disks = zfs.MIN_DISKS.get(raid_type, 1)
    if len(devs) < min_disks:
        print(f"{R}  error: need at least {min_disks} disks for {raid_type}{N}")
        return
    
    dirty = [available[i] for i in indices if zfs.has_zfs_label(available[i].by_id or available[i].dev)]
    if dirty:
        print(f"{Y}  WARNING: The following disks already contain data/labels:{N}")
        for disk in dirty:
            print(f"    - {ui.disk_label(disk)}")
        if not _confirm("these disks already contain data/labels — wipe and continue?"):
            return
        for disk in dirty:
            zfs.wipe(disk.by_id or disk.dev)

    if _confirm(f"create pool '{name}' ({raid_type}) with {len(devs)} disks?"):
        ok, out = zfs.create_pool(name, raid_type, devs)
        print((G + "  ✔ pool created" if ok else R + f"  ✗ failed: {out}") + N)


def _cmd_swap(tbw) -> None:
    disks = core.scan(tbw)
    candidates = [d for d in disks if d.in_pool and d.pool]
    if not candidates:
        print(f"{Y}  no in-pool disks to swap{N}")
        return
    for i, d in enumerate(candidates, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  swap which #> ")
    try:
        d = candidates[int(sel) - 1]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return
    
    spares = [x for x in disks if x.vdev == "spares" and x.vdev_state == "AVAIL" and x.pool == d.pool]
    if not spares:
        print(f"{Y}  no AVAIL spare in pool '{d.pool}'{N}")
        return
    
    if _confirm(f"swap {ui.disk_label(d)} onto spare {ui.disk_label(spares[0])}?"):
        ok, out = zfs.swap_to_spare(d.pool, _pool_dev(d), spares[0].pool_token or spares[0].by_id)
        if not ok:
            print(R + f"  ✗ failed: {out}" + N)
            return
        print(G + "  ✔ swap started — resilvering onto spare" + N)
        while True:
            time.sleep(2)
            st = zfs.poll_resilver_status(d.pool)
            if st["completed"]:
                sys.stdout.write(f"\r{G}  ✔ resilver completed 100%{N}                 \n")
                break
            sys.stdout.write(f"\r{Y}  resilvering... {st['done']}% done, ETA {st['eta']}{N}")
            sys.stdout.flush()
        
        topo = zfs.topology()
        old_token = _pool_dev(d)
        lingers = any(e["pool"] == d.pool and e["token"] == old_token for e in topo.values())
        if lingers:
            ok_d, out_d = zfs.detach(d.pool, old_token)
            if ok_d:
                print(G + f"  ✔ detached old disk {d.dev}" + N)
            else:
                print(R + f"  ✗ failed to detach old disk: {out_d}" + N)
        
        ok_s, out_s = zfs.add_spare(d.pool, d.by_id or d.dev)
        if ok_s:
            print(G + f"  ✔ {ui.disk_label(d)} is now a hot spare in '{d.pool}'" + N)
        else:
            print(R + f"  ✗ failed to re-add as spare: {out_s}" + N)


def _cmd_demote(tbw) -> None:
    disks = core.scan(tbw)
    mirror_members = [d for d in disks if d.in_pool and d.vdev and "mirror" in d.vdev]
    if not mirror_members:
        print(f"{Y}  no mirror members available to demote{N}")
        return
    for i, d in enumerate(mirror_members, 1):
        print(f"    [{i}] {ui.disk_label(d)} in {d.pool}")
    sel = _ask("  demote which #> ")
    try:
        d = mirror_members[int(sel) - 1]
    except (ValueError, IndexError):
        print(f"{Y}  cancelled{N}"); return

    if not zfs.can_detach(d.pool, _pool_dev(d)):
        print(f"{Y}  refuse: not a detachable mirror leg / would break redundancy{N}")
        return
    
    if _confirm(f"demote {ui.disk_label(d)} in '{d.pool}' to a hot spare?"):
        ok, out = zfs.demote_to_spare(d.pool, _pool_dev(d))
        if ok:
            print(G + "  ✔ demoted to spare" + N)
        else:
            print(R + f"  ✗ failed: {out}" + N)


_MENU = (f"{C}[r]{N}efresh  {C}[a]{N}ssign  {C}[o]{N}ffload  {C}[s]{N}wap  {C}[d]{N}emote  {C}[n]{N}ew-pool  {C}[l]{N}ocate  "
         f"{C}[q]{N}uit   (or hot-plug)")


def run() -> int:
    tbw = spec.load()
    _cmd_refresh(tbw)
    print("\n" + _MENU)
    baseline = _block_devs()
    sys.stdout.write("b2ctl> "); sys.stdout.flush()
    while True:
        try:
            r, _, _ = select.select([sys.stdin], [], [], POLL)
        except KeyboardInterrupt:
            print("\nbye"); return 0
        needs_prompt = False
        if r:
            cmd = sys.stdin.readline().strip().lower()
            if cmd in ("q", "quit", "exit"):
                print("bye"); return 0
            elif cmd in ("r", ""):
                _cmd_refresh(tbw)
            elif cmd in ("a", "assign"):
                _cmd_assign(tbw)
            elif cmd in ("o", "offload"):
                _cmd_offload(tbw)
            elif cmd in ("s", "swap"):
                _cmd_swap(tbw)
            elif cmd in ("d", "demote"):
                _cmd_demote(tbw)
            elif cmd == "n":
                _cmd_create(tbw)
            elif cmd == "l":
                _cmd_locate(tbw)
            else:
                print(f"{Y}  unknown command{N}")
            needs_prompt = True

        current = _block_devs()
        new, gone = current - baseline, baseline - current
        if gone or new:
            print("")
            if gone:
                _handle_removed(gone)
            for dev in sorted(new):
                _handle_new_disk(f"/dev/{dev}", tbw)
            needs_prompt = True
        baseline = current

        if needs_prompt:
            print(_MENU)
            sys.stdout.write("b2ctl> "); sys.stdout.flush()

