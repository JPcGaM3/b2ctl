"""b2ctl.raid_actions — guided, guarded PERC (perccli) mutating workflows.

These run only in RAID mode. Every action confirms with an explicit [y/N]
naming controller + VD + physical drive, and is audited via
safety.begin_op/end_op. Destructive ops cannot be exercised on CI hardware, so
they are written defensively and guarded with double confirms — validate on the
real controller.
"""
from __future__ import annotations

import sys
import time

from . import core, hba_raid, safety, spec
from .common import R, Y, G, C, N
from .ui import disk_label


def _confirm(msg: str) -> bool:
    try:
        return input(f"{Y}{msg} [y/N] {N}").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _require_raid() -> bool:
    """Hardware-RAID (perccli) actions only apply in RAID mode."""
    from . import backend
    if backend.get_backend().name != "raid":
        print(f"{R}[-] hardware-RAID actions require RAID mode (perccli). "
              f"This box is IT/HBA — use the ZFS actions (assign/new-pool) instead.{N}")
        return False
    return True


def _hw_members(disks) -> list:
    return [d for d in disks if d.array_type == "HW"]


def _pick_member(disks, target):
    for d in _hw_members(disks):
        if target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", "")):
            return d
    return None


def _wait_rebuild(bay: str) -> bool:
    """Poll perccli rebuild progress and render a bar until done."""
    try:
        while True:
            time.sleep(3)
            st = hba_raid.rebuild_progress(bay)
            pct = st["pct"]
            filled = int(pct // 5)
            bar = "#" * filled + "-" * (20 - filled)
            sys.stdout.write(f"\r{Y}  rebuilding {bay}: [{bar}] {pct:.0f}%{N}")
            sys.stdout.flush()
            if st["done"]:
                sys.stdout.write(f"\r{G}  ✔ rebuild complete on {bay}{' ' * 24}{N}\n")
                return True
    except KeyboardInterrupt:
        sys.stdout.write(f"\n{Y}  (stopped watching; rebuild continues on the controller){N}\n")
        return False


def replace(target: str | None = None) -> int:
    """Guided replace+rebuild of a hardware RAID member."""
    if not _require_raid():
        return 1
    disks = core.scan(spec.load())
    members = _hw_members(disks)
    if not members:
        print(f"{R}[-] no hardware RAID members found (is this a PERC RAID box?){N}")
        return 1

    d = _pick_member(disks, target) if target else None
    if d is None:
        print(f"{C}Hardware RAID members:{N}")
        for i, m in enumerate(members):
            print(f"  {i}) {disk_label(m)}  [{m.array_name}, PD {m.pd_state}]")
        try:
            d = members[int(input("pick # to replace: ").strip())]
        except (ValueError, IndexError, EOFError, KeyboardInterrupt):
            print(f"{R}[-] cancelled{N}")
            return 1

    if not _confirm(f"Replace {disk_label(d)} on {d.array_name}? "
                    "the controller will fail it out and rebuild onto the new drive"):
        print("cancelled")
        return 1

    cmds = [["perccli", hba_raid._pd(d.bay), "set", "offline"],
            ["perccli", hba_raid._pd(d.bay), "set", "missing"]]
    op_id = safety.begin_op("raid_replace", d.serial, d.bay, d.dev,
                            d.array_name, d.array_name, cmds)
    ok, out = hba_raid.set_offline(d.bay)
    if ok:
        ok, out = hba_raid.set_missing(d.bay)
    if not ok:
        safety.end_op(op_id, False, "", out, 1)
        print(f"{R}[-] failed: {out}{N}")
        return 1

    hba_raid.locate(d.bay, True)
    print(f"{Y}[!] LED ON at bay {d.bay} — pull that drive, insert the replacement.{N}")
    try:
        input("press Enter once the new drive is inserted... ")
    except (EOFError, KeyboardInterrupt):
        print()

    st = hba_raid.rebuild_progress(d.bay)
    if not st["done"] and st["pct"] == 0.0:
        hba_raid.start_rebuild(d.bay)
    done = _wait_rebuild(d.bay)
    hba_raid.locate(d.bay, False)
    safety.end_op(op_id, done, "", "", 0 if done else 1)
    print((G + "[+] replace complete" if done
           else Y + "[-] rebuild not confirmed finished — check: perccli /cX/vall show") + N)
    return 0 if done else 1


def offline(target: str) -> int:
    """Mark a member offline + missing and light its LED (prep to pull)."""
    if not _require_raid():
        return 1
    disks = core.scan(spec.load())
    d = _pick_member(disks, target)
    if d is None:
        print(f"{R}[-] '{target}' is not a hardware RAID member{N}")
        return 1
    if not _confirm(f"Offline {disk_label(d)} on {d.array_name}? "
                    "this removes redundancy until rebuilt"):
        print("cancelled")
        return 1
    cmds = [["perccli", hba_raid._pd(d.bay), "set", "offline"],
            ["perccli", hba_raid._pd(d.bay), "set", "missing"]]
    op_id = safety.begin_op("raid_offline", d.serial, d.bay, d.dev,
                            d.array_name, d.array_name, cmds)
    ok, out = hba_raid.set_offline(d.bay)
    if ok:
        ok, out = hba_raid.set_missing(d.bay)
    safety.end_op(op_id, ok, "", out, 0 if ok else 1)
    if ok:
        hba_raid.locate(d.bay, True)
        print(f"{G}[+] {d.bay} offline+missing; LED ON — pull it. "
              f"Stop LED later with: b2ctl locate {d.bay} off{N}")
    else:
        print(f"{R}[-] failed: {out}{N}")
    return 0 if ok else 1


def create_vd(level: str, drives: list[str]) -> int:
    """Create a virtual disk (wipes the member drives)."""
    if not _require_raid():
        return 1
    if not level or not drives:
        print(f"{R}[-] need --level and --drives{N}")
        return 1
    print(f"{R}[!] creating {level} on drives {', '.join(drives)} "
          f"DESTROYS any data on them.{N}")
    if not _confirm(f"Create {level} VD on {', '.join(drives)}?"):
        print("cancelled")
        return 1
    if not _confirm("Are you absolutely sure? (second confirm)"):
        print("cancelled")
        return 1
    cmds = [["perccli", "/c0", "add", "vd", hba_raid._raid_token(level),
             f"drives={','.join(drives)}"]]
    op_id = safety.begin_op("raid_create", "", ",".join(drives), "",
                            level, level, cmds)
    ok, out = hba_raid.add_vd(level, drives)
    safety.end_op(op_id, ok, out, "" if ok else out, 0 if ok else 1)
    print((G + "[+] VD created" if ok else R + f"[-] failed: {out}") + N)
    return 0 if ok else 1


def assign_perc(d, candidates: list) -> int:
    """Interactive menu for an Unconfigured-Good PERC drive (from watch [a]ssign).

    Offers both software RAID (set JBOD -> /dev/sdX -> ZFS) and hardware RAID
    (create a volume / add as a hot spare), plus locate. `candidates` is the list
    of other available UGood drives (for building a multi-disk volume).
    """
    if not _require_raid():
        return 1
    print(f"  {C}PERC drive {disk_label(d)} [{d.pd_state}]{N}")
    print("    [1] Locate LED (blink the bay)")
    print("    [2] Use for ZFS / software RAID  (set JBOD — exposes it as /dev/sdX)")
    print("    [3] CREATE a hardware RAID volume (perccli)")
    print("    [4] Add as hardware HOT SPARE")
    print("    [s] skip / decide later")
    choice = input("  action> ").strip().lower()

    if choice == "1":
        from . import locate as _loc
        _loc.blink_disk(d)
        return 0

    if choice == "2":
        if not _confirm(f"set {disk_label(d)} to JBOD (expose to the OS for ZFS)?"):
            print("cancelled")
            return 1
        ok, out = hba_raid.set_jbod(d.bay)
        if ok:
            import subprocess
            subprocess.run(["udevadm", "settle", "--timeout=10"], check=False)
            print(f"{G}  ✔ {d.bay} set to JBOD — it should now appear as /dev/sdX.\n"
                  f"    Press [r]efresh, then use [n]ew-pool or [a]ssign to add it to a ZFS pool.{N}")
        else:
            print(f"{R}  ✗ failed: {out}{N}")
        return 0 if ok else 1

    if choice == "3":
        picks = [d]
        if candidates:
            print("  drives for the volume:")
            for i, c in enumerate(candidates, 1):
                print(f"    [{i}] {disk_label(c)}")
            sel = input("  pick (space-separated #, blank = just this drive)> ").strip()
            if sel:
                try:
                    picks = [candidates[int(x) - 1] for x in sel.split()]
                except (ValueError, IndexError):
                    print(f"{Y}  invalid selection — cancelled{N}")
                    return 1
        level = input("  raid level (raid0/raid1/raid5/raid10) [raid1]> ").strip() or "raid1"
        return create_vd(level, [p.bay for p in picks])

    if choice == "4":
        dg = input("  drive-group # to protect (blank = global spare)> ").strip()
        tgt = f"DG{dg}" if dg else "global"
        if not _confirm(f"add {disk_label(d)} as a hot spare ({tgt})?"):
            print("cancelled")
            return 1
        cmds = [["perccli", hba_raid._pd(d.bay), "add", "hotsparedrive"]
                + ([f"DGs={dg}"] if dg else [])]
        op_id = safety.begin_op("raid_hotspare", d.serial, d.bay, d.dev, tgt, tgt, cmds)
        ok, out = hba_raid.add_hotspare(d.bay, dg or None)
        safety.end_op(op_id, ok, out, "" if ok else out, 0 if ok else 1)
        print((G + "  ✔ hot spare added" if ok else R + f"  ✗ failed: {out}") + N)
        return 0 if ok else 1

    return 0


def delete_vd(vd: int) -> int:
    """Delete a virtual disk (DESTRUCTIVE — all data lost)."""
    if not _require_raid():
        return 1
    print(f"{R}[!] deleting vd{vd} ERASES ALL DATA on that volume.{N}")
    if not _confirm(f"Delete vd{vd}?"):
        print("cancelled")
        return 1
    if not _confirm(f"Type-y again to confirm permanent deletion of vd{vd}:"):
        print("cancelled")
        return 1
    cmds = [["perccli", f"/c0/v{vd}", "del", "force"]]
    op_id = safety.begin_op("raid_del_vd", "", "", "", f"vd{vd}", f"vd{vd}", cmds)
    ok, out = hba_raid.del_vd(int(vd))
    safety.end_op(op_id, ok, out, "" if ok else out, 0 if ok else 1)
    print((G + f"[+] vd{vd} deleted" if ok else R + f"[-] failed: {out}") + N)
    return 0 if ok else 1
