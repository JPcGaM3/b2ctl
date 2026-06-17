"""b2ctl.zfs — ZFS inspection and actions.

Reads `zpool list` and `zpool status -P -v` to map every leaf device to its
(pool, vdev, state), and wraps the lifecycle actions: add-spare, replace,
attach-mirror, offline, swap-to-spare, and disk wipe. All mutating actions go
through run_check so callers can surface success/failure.
"""

from __future__ import annotations

import os
import re
import subprocess

from .common import run, run_check

_VDEV_RE = re.compile(r"^\s+(mirror|raidz1|raidz2|raidz3|draid\d*|spare|"
                      r"replacing|log|cache|special|dedup)[-\w]*\b")
_LEAF_RE = re.compile(r"^\s+(\S+)\s+(ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL|"
                      r"REMOVED|AVAIL|INUSE)\b")


def list_pools() -> list[dict]:
    out = run(["zpool", "list", "-H", "-o",
               "name,size,alloc,free,health,frag,cap"])
    pools = []
    for line in out.splitlines():
        c = line.split("\t")
        if len(c) >= 7:
            pools.append({"name": c[0], "size": c[1], "alloc": c[2],
                          "free": c[3], "health": c[4], "frag": c[5], "cap": c[6]})
    return pools


def topology() -> dict:
    """Return {device_path: {'pool','vdev','state'}} for every leaf.

    Indexed by both the -P leaf path and its realpath so callers can match a
    by-id link or a /dev/sdX.
    """
    topo: dict[str, dict] = {}
    for p in list_pools():
        out = run(["zpool", "status", "-P", "-v", p["name"]])
        _parse(p["name"], out, topo)
    return topo


def _parse(pool: str, text: str, topo: dict) -> None:
    in_cfg = False
    # Stack of (indent, vdev_name) so nested sub-vdevs (spare-0, replacing-0
    # inside raidz1-0) don't steal sibling leaves at the parent level.
    vdev_stack: list[tuple[int, str]] = [(0, pool)]
    for line in text.splitlines():
        st = line.strip()
        if not st:
            continue
        if st.startswith("config:"):
            in_cfg = True
            continue
        if not in_cfg:
            continue
        if st.startswith("errors:"):
            break
        indent = len(line) - len(line.lstrip())
        mv = _VDEV_RE.match(line)
        if mv:
            while len(vdev_stack) > 1 and vdev_stack[-1][0] >= indent:
                vdev_stack.pop()
            vdev_stack.append((indent, st.split()[0]))
            continue
        ml = _LEAF_RE.match(line)
        if ml:
            token, state = ml.group(1), ml.group(2)
            if token == pool:
                continue
            vdev = next(
                (vn for vi, vn in reversed(vdev_stack) if vi < indent),
                pool,
            )
            entry = {"pool": pool, "vdev": vdev, "state": state, "token": token}
            topo[token] = entry
            if token.startswith("/"):
                try:
                    topo[os.path.realpath(token)] = entry
                except OSError:
                    pass


def attach_membership(disks, topo: dict) -> None:
    leaves = list({id(e): e for e in topo.values()}.values())
    for d in disks:
        member = None
        cands = [d.by_id, d.dev]
        if d.by_id:
            cands.extend([d.by_id + "-part1", d.by_id + "-part3"])
            try:
                cands.append(os.path.realpath(d.by_id))
                cands.append(os.path.realpath(d.by_id + "-part1"))
                cands.append(os.path.realpath(d.by_id + "-part3"))
            except OSError:
                pass
        try:
            cands.extend([os.path.realpath(d.dev), d.dev + "1", d.dev + "3"])
            cands.append(os.path.realpath(d.dev + "1"))
            cands.append(os.path.realpath(d.dev + "3"))
        except OSError:
            pass
        for cand in cands:
            if cand and cand in topo:
                member = topo[cand]
                break
        # robust fallback: the by-id leaf token embeds the disk serial
        # (also catches rpool's "...-part3" leaves)
        if member is None and d.serial and d.dev != "-" and d.health != "GHOST":
            for e in leaves:
                if d.serial in e["token"]:
                    member = e
                    break
        if member:
            d.pool_token = member["token"]
            d.pool = member["pool"]
            d.vdev = member["vdev"]
            d.vdev_state = member["state"]


def degraded_leaves() -> list[dict]:
    """Leaves that need replacing (FAULTED/UNAVAIL/REMOVED/OFFLINE)."""
    bad = []
    topo = topology()
    seen = set()
    for entry in topo.values():
        key = (entry["pool"], entry["token"])
        if key in seen:
            continue
        seen.add(key)
        if entry["state"] in ("FAULTED", "UNAVAIL", "REMOVED", "OFFLINE"):
            bad.append(entry)
    return bad


def spares(pool: str) -> list[str]:
    """AVAIL spare tokens in a pool."""
    out = run(["zpool", "status", "-P", "-v", pool])
    topo: dict = {}
    _parse(pool, out, topo)
    return [e["token"] for e in topo.values()
            if "spare" in e["vdev"] and e["state"] == "AVAIL"]


# --------------------------------------------------------------------------- #
# Actions (mutating) — return (ok, output)
# --------------------------------------------------------------------------- #
def add_spare(pool: str, dev: str):
    return run_check(["zpool", "add", "-f", pool, "spare", dev])


def add_mirror(pool: str, dev_a: str, dev_b: str):
    return run_check(["zpool", "add", "-f", pool, "mirror", dev_a, dev_b])


def attach(pool: str, existing: str, new: str):
    return run_check(["zpool", "attach", "-f", pool, existing, new])


def replace(pool: str, old: str, new: str):
    return run_check(["zpool", "replace", "-f", pool, old, new])



def detach(pool: str, dev: str):
    return run_check(["zpool", "detach", pool, dev])


def can_detach(pool: str, dev_token: str) -> bool:
    topo = topology()
    vdev = None
    for token, entry in topo.items():
        if entry["pool"] == pool and entry["token"] == dev_token:
            vdev = entry["vdev"]
            break
    if not vdev:
        return False
    if "raidz" in vdev:
        return False
    if "mirror" in vdev:
        members = [e for e in topo.values() if e["pool"] == pool and e["vdev"] == vdev]
        online_others = [e for e in members if e["token"] != dev_token and e["state"] == "ONLINE"]
        if not online_others:
            return False
    return True


def demote_to_spare(pool: str, dev_token: str) -> tuple[bool, str]:
    ok, out = detach(pool, dev_token)
    if not ok:
        return False, out
    return add_spare(pool, dev_token)


def swap_to_spare(pool: str, member: str, spare: str):
    """Proactively move a still-alive member onto an AVAIL spare."""
    return run_check(["zpool", "replace", pool, member, spare])


def resilver_status(pool: str) -> str | None:
    out = run(["zpool", "status", pool])
    m = re.search(r"(resilver|scan:).*?(\d+\.\d+%|in progress.*)", out)
    return m.group(0).strip() if m else None


def poll_resilver_status(pool: str) -> dict:
    out = run(["zpool", "status", pool])
    res = {"done": 0.0, "eta": "", "completed": False}
    if "resilvered" in out and "with 0 errors" in out:
        res["completed"] = True
        res["done"] = 100.0
        return res
    m_done = re.search(r'(\d+\.\d+)%\s*done', out)
    if m_done:
        res["done"] = float(m_done.group(1))
    m_eta = re.search(r'((?:\d+\s*days?\s*)?\d{2}:\d{2}:\d{2})\s*to go', out)
    if m_eta:
        res["eta"] = m_eta.group(1).strip()
    return res


def wipe_sg(sg_dev: str) -> tuple[bool, str]:
    """Zero first 40 MB of a SCSI generic device to erase RAID metadata.

    Uses subprocess directly (not run_check) so dd's status=progress output
    flows live to the terminal via stderr.
    """
    r = subprocess.run(
        ["dd", "if=/dev/zero", f"of={sg_dev}", "bs=4M", "count=10",
         "conv=fsync", "status=progress"],
        stdout=subprocess.PIPE,
        stderr=None,   # let dd progress stream to terminal
        timeout=120,
    )
    if r.returncode != 0:
        return False, "dd returned non-zero"
    sg_name = os.path.basename(sg_dev)
    rescan = f"/sys/class/scsi_generic/{sg_name}/device/rescan"
    try:
        with open(rescan, "w") as f:
            f.write("1")
    except OSError:
        pass
    run_check(["udevadm", "trigger", "--action=add", "--subsystem-match=block"])
    return True, "zeroed 40 MB, rescan triggered"


def wipe(dev: str):
    """Make a disk blank for a fresh pool: clear ZFS label, signatures, GPT."""
    run_check(["zpool", "labelclear", "-f", dev])
    run_check(["wipefs", "-a", dev])
    return run_check(["sgdisk", "--zap-all", dev])


MIN_DISKS = {"stripe": 1, "mirror": 2, "raidz1": 2, "raidz2": 4}

def has_zfs_label(dev: str) -> bool:
    """True if `dev` already carries a ZFS label / known signature."""
    ok, out = run_check(["wipefs", "-n", dev])
    if not ok:
        return False
    lines = [x for x in out.splitlines() if x.strip() and not x.startswith("DEVICE") and not x.startswith("offset")]
    return len(lines) > 0


def create_pool(name: str, raid_type: str, devs: list[str]) -> tuple[bool, str]:
    cmd = ["zpool", "create", "-f", "-o", "ashift=12", "-O", "compression=lz4", "-O", "atime=off", "-O", "xattr=sa", "-o", "autotrim=on", name]
    if raid_type != "stripe":
        cmd.append(raid_type)
    cmd.extend(devs)
    return run_check(cmd)

