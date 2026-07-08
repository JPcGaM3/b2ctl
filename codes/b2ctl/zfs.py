"""b2ctl.zfs — ZFS inspection and actions.

Reads `zpool list` and `zpool status -P -v` to map every leaf device to its
(pool, vdev, state), and wraps the lifecycle actions: add-spare, replace,
attach-mirror, offline, swap-to-spare, and disk wipe. All mutating actions go
through run_check so callers can surface success/failure.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess

from .common import run, run_check


def _tool(name: str) -> str:
    """Resolve a binary through config.tool() so operator tool_paths overrides
    are honored on the destructive/read paths, not only in the cron writer
    (F-035). Falls back to shutil.which/bare name, keeping the sim PATH harness."""
    from . import config as _cfg
    return _cfg.tool(name)


_VDEV_RE = re.compile(r"^\s+(mirror|raidz1|raidz2|raidz3|draid\d*|spare|"
                      r"replacing|log|cache|special|dedup)[-\w]*\b")
_LEAF_RE = re.compile(r"^\s+(\S+)\s+(ONLINE|DEGRADED|FAULTED|OFFLINE|UNAVAIL|"
                      r"REMOVED|AVAIL|INUSE)\b")


def list_pools() -> list[dict]:
    out = run([_tool("zpool"), "list", "-H", "-o",
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
        out = run([_tool("zpool"), "status", "-P", "-v", p["name"]])
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
            # top-level data vdev (direct child of the pool root): a leaf nested
            # in a spare-*/replacing-* sub-vdev still belongs to this vdev for
            # redundancy accounting (can_offline/can_detach).
            top_vdev = vdev_stack[1][1] if len(vdev_stack) > 1 else vdev
            entry = {"pool": pool, "vdev": vdev, "state": state, "token": token,
                     "top_vdev": top_vdev}
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


_AUX_DEGRADED = ("FAULTED", "UNAVAIL", "REMOVED", "OFFLINE", "DEGRADED")


def aux_leaves(pool: str | None = None) -> list[dict]:
    """Cache (L2ARC) + log (SLOG) leaves, tagged for the repair flow.

    Returns one dict per (pool, token) leaf whose TOP vdev is cache/log:
      {pool, token, vdev, top_vdev, state, klass, mirror_leg, degraded}
      klass      : "cache" | "log"
      mirror_leg : True for a leg of a MIRRORED SLOG (vdev='mirror-N' under logs)
      degraded   : state in FAULTED/UNAVAIL/REMOVED/OFFLINE/DEGRADED
    Dedupe by (pool, token) — _parse indexes every leaf twice (path + realpath),
    same as degraded_leaves(). `pool` filters to one pool when given.
    """
    out: list[dict] = []
    seen: set = set()
    for e in topology().values():
        if pool is not None and e["pool"] != pool:
            continue
        top = e.get("top_vdev", e["vdev"])
        # A top-level data leaf of a stripe/single-disk pool has top_vdev == the
        # pool name; guard it so a pool NAMED e.g. 'logbackup'/'cache-pool' isn't
        # misread as an aux vdev (mirrors pool_level()'s `top == pool` guard).
        if top == e["pool"]:
            continue
        klass = "cache" if "cache" in top else "log" if "log" in top else None
        if klass is None:
            continue
        key = (e["pool"], e["token"])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "pool": e["pool"], "token": e["token"], "vdev": e["vdev"],
            "top_vdev": top, "state": e["state"], "klass": klass,
            "mirror_leg": klass == "log" and e["vdev"].startswith("mirror"),
            "degraded": e["state"] in _AUX_DEGRADED,
        })
    return out


def pool_level(pool: str) -> str:
    """Data-vdev redundancy type for a pool: 'mirror' / 'raidz1' / ...,
    'mixed' if several differ, 'stripe' if there is no redundant data vdev.

    Derived from topology() vdev names (e.g. 'mirror-0' -> 'mirror'), excluding
    the auxiliary classes (cache/log/spare/special/dedup).
    """
    _AUX = ("cache", "log", "spare", "special", "dedup")
    levels = set()
    for e in topology().values():
        if e["pool"] != pool:
            continue
        # Classify by the TOP-level vdev: a mirrored SLOG's leaves carry
        # vdev='mirror-1' but their top vdev is 'logs', so they must be excluded
        # from the DATA redundancy level (else the pool reads 'mixed') — F-060.
        top = e.get("top_vdev", e["vdev"])
        if top == pool or any(a in top for a in _AUX):
            continue
        levels.add(re.sub(r"-\d+$", "", top))
    if not levels:
        return "stripe"
    if len(levels) == 1:
        return next(iter(levels))
    return "mixed"


def spares(pool: str) -> list[str]:
    """AVAIL spare tokens in a pool (de-duplicated).

    _parse indexes each leaf under BOTH its token and its realpath, so iterating
    topo.values() yields the same spare twice — dedupe by token (F-105)."""
    out = run([_tool("zpool"), "status", "-P", "-v", pool])
    topo: dict = {}
    _parse(pool, out, topo)
    seen: set = set()
    result = []
    for e in topo.values():
        if "spare" in e["vdev"] and e["state"] == "AVAIL" and e["token"] not in seen:
            seen.add(e["token"])
            result.append(e["token"])
    return result


def spares_replacing(pool: str) -> dict[str, str]:
    """Return {spare_token: replaced_token} for replacements where the replaced
    leaf has a bad state (REMOVED/FAULTED/UNAVAIL/OFFLINE).

    Parses replacing-N vdevs from zpool status. Returns {} if none in progress.
    """
    out = run([_tool("zpool"), "status", "-P", "-v", pool])
    result: dict[str, str] = {}
    in_cfg = False
    in_replacing = False
    replacing_indent = 0
    replacing_leaves: list[tuple[str, str]] = []

    for line in out.splitlines():
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

        if in_replacing:
            ml = _LEAF_RE.match(line)
            if ml and indent > replacing_indent:
                replacing_leaves.append((ml.group(1), ml.group(2)))
                if len(replacing_leaves) == 2:
                    (t0, s0), (t1, s1) = replacing_leaves
                    _bad = {"REMOVED", "FAULTED", "UNAVAIL", "OFFLINE"}
                    if s0 in _bad:
                        result[t1] = t0
                    elif s1 in _bad:
                        result[t0] = t1
                    in_replacing = False
                    replacing_leaves = []
                continue
            if indent <= replacing_indent:
                in_replacing = False
                replacing_leaves = []

        mv = _VDEV_RE.match(line)
        if mv and (st.startswith("replacing") or st.startswith("spare-")):
            in_replacing = True
            replacing_indent = indent
            replacing_leaves = []

    return result


# --------------------------------------------------------------------------- #
# Actions (mutating) — return (ok, output)
# --------------------------------------------------------------------------- #
def add_spare(pool: str, dev: str, *, dry_run: bool = False):
    return run_check([_tool("zpool"), "add", "-f", pool, "spare", dev], dry_run=dry_run)


def add_cache(pool: str, devs: list[str], *, dry_run: bool = False):
    """Add L2ARC cache device(s). Loss is harmless (cache miss); not mirrored."""
    return run_check([_tool("zpool"), "add", "-f", pool, "cache", *devs], dry_run=dry_run)


def add_log(pool: str, devs: list[str], *, dry_run: bool = False):
    """Add a SLOG (separate ZIL). 2+ devs -> mirrored log; caller warns on PLP."""
    spec = (["mirror", *devs] if len(devs) > 1 else list(devs))
    return run_check([_tool("zpool"), "add", "-f", pool, "log", *spec], dry_run=dry_run)


def remove_vdev(pool: str, dev: str, *, dry_run: bool = False):
    """Remove an aux vdev (cache/log/spare leaf) by token. `zpool remove`."""
    return run_check([_tool("zpool"), "remove", pool, dev], dry_run=dry_run)


def attach(pool: str, existing: str, new: str, *, dry_run: bool = False):
    return run_check([_tool("zpool"), "attach", "-f", pool, existing, new], dry_run=dry_run)


def replace(pool: str, old: str, new: str, *, dry_run: bool = False):
    return run_check([_tool("zpool"), "replace", "-f", pool, old, new], dry_run=dry_run)


def detach(pool: str, dev: str, *, dry_run: bool = False):
    return run_check([_tool("zpool"), "detach", pool, dev], dry_run=dry_run)


def detach_safety(pool: str, dev_token: str, topo: dict | None = None) -> str:
    """Classify detaching a mirror leg (Task C guard):

      "ok"              — safe: >=2 other ONLINE members remain (redundancy intact
                          after the detach)
      "last_redundancy" — exactly one ONLINE sibling: the detach leaves a lone,
                          non-redundant vdev (e.g. the 2-way rpool). Allowed only
                          behind an explicit typed confirm by the caller.
      "refuse"          — not a detachable plain mirror leg (raidz / stripe /
                          spare-*/replacing-* child), or no ONLINE sibling at all.

    Accepts a pre-built `topo` snapshot so a caller running several guards in one
    interactive flow does not spawn a fresh `zpool status` per check (F-107).
    """
    if topo is None:
        topo = topology()
    entry = next((e for e in topo.values()
                  if e["pool"] == pool and e["token"] == dev_token), None)
    if not entry or "mirror" not in entry["vdev"]:
        return "refuse"
    vdev = entry["vdev"]
    online_others = {e["token"] for e in topo.values()
                     if e["pool"] == pool and e["vdev"] == vdev
                     and e["token"] != dev_token and e["state"] == "ONLINE"}
    if len(online_others) >= 2:
        return "ok"
    if len(online_others) == 1:
        return "last_redundancy"
    return "refuse"


def can_detach(pool: str, dev_token: str, topo: dict | None = None) -> bool:
    """True only when a detach is safe WITHOUT removing the last redundancy.

    A 2-way mirror (one ONLINE sibling) now returns False — that case is
    'last_redundancy' and must be routed through detach_safety() so the caller
    can warn + require a typed confirm (Task C)."""
    return detach_safety(pool, dev_token, topo) == "ok"


def offline(pool: str, dev: str, *, dry_run: bool = False):
    """`zpool offline <pool> <dev>` — take a member offline (pool -> DEGRADED)."""
    return run_check([_tool("zpool"), "offline", pool, dev], dry_run=dry_run)


def can_offline(pool: str, dev_token: str, topo: dict | None = None) -> bool:
    """True if offlining dev keeps the pool importable.

    The disk's vdev must be redundant (raidz/mirror) AND every OTHER member
    currently ONLINE — so going to DEGRADED is safe. False for stripe/single
    (no redundancy) or an already-degraded vdev (offlining a 2nd could fail it).

    Accepts a shared `topo` snapshot to avoid re-running `zpool status` when
    several guards fire in one flow (F-107).
    """
    if topo is None:
        topo = topology()
    entry = next((e for e in topo.values()
                  if e["pool"] == pool and e["token"] == dev_token), None)
    if not entry:
        return False
    # Group by the TOP-level data vdev so a FAULTED original nested in a
    # spare-*/replacing-* sub-vdev still counts as a non-ONLINE member of this
    # vdev — otherwise an already-degraded raidz1 would approve a 2nd outage.
    top = entry.get("top_vdev", entry["vdev"])
    if "raidz" not in top and "mirror" not in top:
        return False
    others = [e for e in topo.values()
              if e["pool"] == pool and e.get("top_vdev", e["vdev"]) == top
              and e["token"] != dev_token]
    return bool(others) and all(e["state"] == "ONLINE" for e in others)


def demote_to_spare(pool: str, dev_token: str, *, dry_run: bool = False) -> tuple[bool, str]:
    ok, out = detach(pool, dev_token, dry_run=dry_run)
    if not ok:
        return False, out
    ok2, out2 = add_spare(pool, dev_token, dry_run=dry_run)
    if not ok2:
        # F-061: the detach succeeded but re-adding as spare failed — the disk is
        # now DETACHED and free (not stranding pool data, but not a spare either).
        # Make the recovery explicit rather than reporting a bare failure.
        return False, (f"detached OK but 'add spare' failed: {out2}. {dev_token} is "
                       f"now free — retry: zpool add {pool} spare {dev_token}")
    return ok2, out2


def swap_to_spare(pool: str, member: str, spare: str, *, dry_run: bool = False):
    """Proactively move a still-alive member onto an AVAIL spare."""
    return run_check([_tool("zpool"), "replace", pool, member, spare], dry_run=dry_run)


def poll_resilver_status(pool: str) -> dict:
    """Parse `zpool status <pool>` into resilver progress.

    Returns {done, eta, completed, has_errors, ok}. Completion is matched
    POSITIVELY on the 'resilvered ... with N errors' scan line — an in-progress
    resilver (which also contains the word 'resilvered' but says 'resilver in
    progress', and early on has 'no estimated completion time' rather than
    'to go') must NOT be read as completed. `ok` is False when zpool status
    produced no output, so a caller never treats a failed poll as done.
    """
    out = run([_tool("zpool"), "status", pool])
    res = {"done": 0.0, "eta": "", "completed": False, "has_errors": False, "ok": True}
    if not out.strip():
        res["ok"] = False
        return res
    low = out.lower()
    if "resilver in progress" in low:
        m_done = re.search(r'(\d+(?:\.\d+)?)%\s*done', out)
        if m_done:
            res["done"] = float(m_done.group(1))
        if "no estimated completion time" in low:
            res["eta"] = "unknown"
        else:
            m_eta = re.search(r'((?:\d+\s*days?\s*)?\d{2}:\d{2}:\d{2})\s*to go', out)
            if m_eta:
                res["eta"] = m_eta.group(1).strip()
        return res
    m_done = re.search(r'resilvered\b.*?with (\d+) errors', out)
    if m_done:
        res["completed"] = True
        res["done"] = 100.0
        res["has_errors"] = int(m_done.group(1)) > 0
    return res


def wipe_sg(sg_dev: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Zero first 40 MB of a SCSI generic device to erase RAID metadata.

    Uses subprocess directly (not run_check) so dd's status=progress output
    flows live to the terminal via stderr.
    """
    if dry_run:
        return True, f"dry-run: would zero 40 MB on {sg_dev}"
    from . import config as _cfg
    try:
        r = subprocess.run(
            [_cfg.tool("dd"), "if=/dev/zero", f"of={sg_dev}", "bs=4M", "count=10",
             "conv=fsync", "status=progress"],
            stdout=subprocess.PIPE,
            stderr=None,   # let dd progress stream to terminal
            timeout=300,   # fsync on a degraded disk can be slow
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        # F-026: a hung/failed dd must not crash watch mid ghost-wipe.
        return False, f"dd failed: {exc}"
    if r.returncode != 0:
        return False, "dd returned non-zero"
    sg_name = os.path.basename(sg_dev)
    rescan = f"/sys/class/scsi_generic/{sg_name}/device/rescan"
    try:
        with open(rescan, "w") as f:
            f.write("1")
    except OSError:
        pass
    run_check([_tool("udevadm"), "trigger", "--action=add", "--subsystem-match=block"])
    return True, "zeroed 40 MB, rescan triggered"


def wipe(dev: str, *, dry_run: bool = False):
    """Make a disk blank for a fresh pool: clear ZFS label, signatures, GPT.

    `zpool labelclear` legitimately exits non-zero on a disk with no ZFS label
    (the common wipe case), so its result is best-effort. A wipefs or sgdisk
    failure IS surfaced — reporting success on sgdisk alone let a disk keep a
    live signature into pool create (F-108)."""
    run_check([_tool("zpool"), "labelclear", "-f", dev], dry_run=dry_run)   # best-effort
    wok, wout = run_check([_tool("wipefs"), "-a", dev], dry_run=dry_run)
    sok, sout = run_check([_tool("sgdisk"), "--zap-all", dev], dry_run=dry_run)
    if wok and sok:
        return True, sout or wout
    fails = [m for m in (None if wok else f"wipefs: {wout}",
                         None if sok else f"sgdisk: {sout}") if m]
    return False, "; ".join(fails)


MIN_DISKS = {"stripe": 1, "mirror": 2, "raid10": 4, "raidz1": 3, "raidz2": 4}

def has_zfs_label(dev: str) -> bool:
    """True if `dev` already carries a ZFS label / known signature.

    Fail-**closed** (F-062): if the wipefs probe itself errors (missing binary,
    device busy, permission) we cannot prove the disk is blank, so we report it
    as labelled. create's guard then warns + asks before wiping, instead of
    silently treating an unprobable disk as empty and clobbering live data."""
    ok, out = run_check([_tool("wipefs"), "-n", dev])
    if not ok:
        return True  # probe failed -> assume a signature may be present
    lines = [x for x in out.splitlines() if x.strip() and not x.startswith("DEVICE") and not x.startswith("offset")]
    return len(lines) > 0


# SSD-optimised pool/dataset defaults. pool-level go to `zpool -o`, dataset-level
# to `-O`. dnodesize=auto + acltype=posixacl are the standard Linux complements
# to xattr=sa; recordsize is workload-tunable per dataset later.
DEFAULT_POOL_OPTS = {"ashift": "12", "autotrim": "on"}
DEFAULT_FS_OPTS = {"compression": "lz4", "atime": "off", "xattr": "sa",
                   "dnodesize": "auto", "acltype": "posixacl", "recordsize": "128K"}


def create_pool(name: str, raid_type: str, devs: list[str], *,
                pool_opts: dict | None = None, fs_opts: dict | None = None,
                dry_run: bool = False) -> tuple[bool, str]:
    po = DEFAULT_POOL_OPTS if pool_opts is None else pool_opts
    fo = DEFAULT_FS_OPTS if fs_opts is None else fs_opts
    cmd = [_tool("zpool"), "create", "-f"]
    for k, v in po.items():
        cmd += ["-o", f"{k}={v}"]
    for k, v in fo.items():
        cmd += ["-O", f"{k}={v}"]
    if raid_type == "raid10":
        if len(devs) < 4 or len(devs) % 2:
            return False, "raid10 needs an even number of disks (>= 4)"
        vdev_args: list[str] = []
        for i in range(0, len(devs), 2):
            vdev_args += ["mirror", devs[i], devs[i + 1]]
    elif raid_type == "stripe":
        vdev_args = list(devs)
    else:                       # mirror / raidz1 / raidz2 / raidz3
        vdev_args = [raid_type, *devs]
    cmd.append(name)
    cmd.extend(vdev_args)
    return run_check(cmd, dry_run=dry_run)


def destroy_pool(pool: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """`zpool destroy <pool>` — DESTRUCTIVE. Caller must confirm."""
    return run_check([_tool("zpool"), "destroy", pool], dry_run=dry_run)


# --------------------------------------------------------------------------- #
# Per-pool maintenance cron (monthly TRIM 1st Sunday + SCRUB 2nd Sunday)
# --------------------------------------------------------------------------- #
def _cron_path(pool: str) -> str:
    return "/etc/cron.d/b2ctl-" + re.sub(r"[^A-Za-z0-9_-]", "_", pool)


def install_pool_cron(pool: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Write /etc/cron.d/b2ctl-<pool>: monthly TRIM (1st Sun) + SCRUB (2nd Sun).

    Calls zpool directly. The 1-7 / 8-14 day-of-month windows combined with
    `date +%w == 0` lock each run to the first / second Sunday. zpool's absolute
    path is resolved so cron's minimal PATH still finds it.
    """
    from . import config as _cfg
    zpool = _cfg.tool("zpool")
    path = _cron_path(pool)
    content = (
        f"# b2ctl ZFS maintenance for pool '{pool}' — auto-generated\n"
        "PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n"
        "# TRIM: first Sunday of each month\n"
        f'24 0 1-7 * * root [ "$(date +\\%w)" -eq 0 ] && {zpool} trim {pool}\n'
        "# SCRUB: second Sunday of each month\n"
        f'24 0 8-14 * * root [ "$(date +\\%w)" -eq 0 ] && {zpool} scrub {pool}\n'
    )
    if dry_run:
        return True, f"[dry-run] would write {path}"
    try:
        os.makedirs("/etc/cron.d", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o644)
        return True, path
    except OSError as exc:
        return False, str(exc)


def remove_pool_cron(pool: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Remove a pool's maintenance cron (no-op if absent)."""
    path = _cron_path(pool)
    if dry_run:
        return True, f"[dry-run] would remove {path}"
    try:
        if os.path.exists(path):
            os.remove(path)
        return True, path
    except OSError as exc:
        return False, str(exc)


def prune_orphan_crons(*, dry_run: bool = False) -> list[str]:
    """Delete b2ctl-<pool> crons whose pool no longer exists. Returns paths removed.

    Guarded: if `zpool list` cannot be queried (transient failure) we refuse to
    prune, so a momentary error never deletes every maintenance cron. A genuine
    zero-pool box still prunes (the query succeeds and returns no pools).
    """
    crons = glob.glob("/etc/cron.d/b2ctl-*")
    if not crons:
        return []
    ok, _ = run_check([_tool("zpool"), "list", "-H", "-o", "name"])
    if not ok:
        return []
    live = {_cron_path(p["name"]) for p in list_pools()}
    removed: list[str] = []
    for path in crons:
        if path in live:
            continue
        if not dry_run:
            try:
                os.remove(path)
            except OSError:
                continue
        removed.append(path)
    return removed

