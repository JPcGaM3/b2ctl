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
from datetime import datetime

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


def add_log(pool: str, devs: list[str], *, raid_type: str | None = None,
            dry_run: bool = False):
    """Add a SLOG (separate ZIL). Topology (v0.17.0):
       None     -> legacy auto: mirror if >1 dev else single (back-compat)
       "single" -> plain log vdev(s), no redundancy
       "mirror" -> mirrored log
       "raid10" -> stripe of mirrors (log mirror a b mirror c d)
    HARD ZFS CONSTRAINT: a log vdev is single / mirror / stripe-of-mirrors only.
    raidz (raid5) is INVALID for a log vdev — rejected here, never runs a command.
    Caller warns on PLP / non-mirror."""
    if raid_type in ("raidz1", "raidz2", "raidz3", "raid5"):
        return False, f"raidz is invalid for a SLOG vdev ({raid_type})"
    if raid_type == "raid10":
        if len(devs) < 4 or len(devs) % 2:
            return False, "raid10 SLOG needs an even number of disks (>= 4)"
        spec = _mirror_pairs(devs)
    elif raid_type == "mirror":
        if len(devs) < 2:
            return False, "mirror SLOG needs >= 2 disks"
        spec = ["mirror", *devs]
    elif raid_type == "single":
        spec = list(devs)
    else:                                        # None -> legacy auto
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


# --------------------------------------------------------------------------- #
# Manual maintenance — scrub / trim (kernel owns the op; these return at once)
# --------------------------------------------------------------------------- #
def start_scrub(pool: str, *, dry_run: bool = False):
    """`zpool scrub <pool>` — the kernel runs it in the background."""
    return run_check([_tool("zpool"), "scrub", pool], dry_run=dry_run)


def start_trim(pool: str, *, dry_run: bool = False):
    """`zpool trim <pool>` — the kernel runs it in the background."""
    return run_check([_tool("zpool"), "trim", pool], dry_run=dry_run)


def poll_scrub_status(pool: str) -> dict:
    """Parse `zpool status <pool>` into scrub progress. Sibling of
    poll_resilver_status, keyed on the `scan: scrub` line.

    Returns {done, eta, completed, has_errors, ok}. NOTE: the
    'scrub repaired ... with N errors' line PERSISTS after the scrub finishes
    (until the next scrub), so completed=True means 'not currently scrubbing' —
    exactly what _wait_scrub needs (we just issued the scrub). `ok` is False on
    empty output so a failed poll is never read as done. A resilver line
    ('resilvered ...') never matches, so it is not mistaken for a scrub."""
    out = run([_tool("zpool"), "status", pool])
    res = {"done": 0.0, "eta": "", "completed": False, "has_errors": False, "ok": True}
    if not out.strip():
        res["ok"] = False
        return res
    low = out.lower()
    if "scrub in progress" in low:
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
    m_done = re.search(r'scrub repaired\b.*?with (\d+) errors', out)
    if m_done:
        res["completed"] = True
        res["done"] = 100.0
        res["has_errors"] = int(m_done.group(1)) > 0
    return res


def poll_trim_status(pool: str) -> dict:
    """Best-effort trim progress from `zpool status -t <pool>`.

    TRIM is NOT on the `scan:` line — each LEAF carries a per-vdev annotation.
    Returns {trimming, done, states, ok} where states maps a leaf token ->
    'trimming'|'untrimmed'|'trimmed'|'unsupported'. The exact `-t` annotation is
    OpenZFS-version-dependent, so `done` (percent) is best-effort and may be None
    even while trimming."""
    out = run([_tool("zpool"), "status", "-t", pool])
    res = {"trimming": False, "done": None, "states": {}, "ok": bool(out.strip())}
    for line in out.splitlines():
        m = re.search(r"\((trimming|untrimmed|trimmed|trim unsupported)"
                      r"(?:,?\s*(\d+(?:\.\d+)?)%)?\)", line, re.I)
        if not m:
            continue
        tok = line.split()[0]
        state = m.group(1).lower().replace("trim unsupported", "unsupported")
        res["states"][tok] = state
        if state == "trimming":
            res["trimming"] = True
            if m.group(2):
                res["done"] = float(m.group(2))
    return res


def last_scrub_date(pool: str) -> str | None:
    """Last-scrub completion time from `zpool status <pool>`, as an ISO-8601
    string (uniform with maint.jsonl / safety timestamps) so callers can
    rel_time() it. The scan line reads e.g. `scan: scrub repaired 0B in 00:01:23
    with 0 errors on Tue Jul  8 03:00:00 2026`. Returns None if there is no
    completed scrub. ZFS keeps only the MOST RECENT scrub — history is in
    maint.jsonl, not here (§9: this is a pure read, never writes)."""
    out = run([_tool("zpool"), "status", pool])
    if not out:
        return None
    m = re.search(r'scrub repaired\b.*?\bon\s+(.+?)\s*$', out, re.M)
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return datetime.strptime(raw, "%a %b %d %H:%M:%S %Y").isoformat(timespec="seconds")
    except ValueError:
        return raw


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


# --------------------------------------------------------------------------- #
# Over-provisioning — create a single partition so ZFS gets less than the whole
# disk (reserve spare area for SSD wear-leveling). First partition-creation site
# in b2ctl; otherwise ZFS is always handed whole disks (v0.17.0).
# --------------------------------------------------------------------------- #
_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([KMGT])?B?\s*$", re.I)
_MULT = {None: 1, "K": 2 ** 10, "M": 2 ** 20, "G": 2 ** 30, "T": 2 ** 40}


def parse_size(s: str) -> int | None:
    """'32G'/'512M'/'1.5T'/'1048576' -> bytes; None if unparseable (caller rejects)."""
    m = _SIZE_RE.match(s or "")
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return int(val * _MULT[(m.group(2) or "").upper() or None])


def _part1_path(dev: str) -> str:
    """First-partition device token for a whole-disk device — STRING CONVENTION
    only, never os.path.exists (must resolve under --dry-run, where no partition
    is created). by-id/by-path symlink -> '<link>-part1'; nvme/mmcblk/loop ->
    '<dev>p1'; /dev/sdX|vdX|hdX -> '<dev>1'."""
    if "/dev/disk/by-id/" in dev or "/dev/disk/by-path/" in dev:
        return dev + "-part1"
    if re.search(r"(nvme\d+n\d+|mmcblk\d+|loop\d+)$", os.path.basename(dev)):
        return dev + "p1"
    return dev + "1"


def partition(dev: str, size: str, *, type_code: str = "bf01",
              max_bytes: int | None = None, dry_run: bool = False) -> tuple[bool, str]:
    """Create a single partition of `size` on `dev`; return (ok, part1_path).

    Runs `sgdisk -n 1:0:+<size> -t 1:<type_code> <dev>` (bf01 = the conventional
    ZFS type code; cosmetic, gates nothing), then a MANDATORY `udevadm settle` so
    the `-part1` by-id symlink exists before the caller's `zpool add ...-part1`
    (the link appears asynchronously — without settle the add races and fails).
    The primary size<=disk validation lives in the caller (which holds
    Disk.size_bytes); `max_bytes` is an optional defensive bound."""
    if max_bytes is not None:
        req = parse_size(size)
        if req is None:
            return False, f"unparseable size '{size}'"
        if req > max_bytes:
            return False, f"requested {size} exceeds disk capacity"
    ok, out = run_check([_tool("sgdisk"), "-n", f"1:0:+{size}",
                         "-t", f"1:{type_code}", dev], dry_run=dry_run)
    if not ok:
        return False, out
    if not dry_run:                      # nothing was created under dry-run
        run([_tool("udevadm"), "settle"])
    return True, _part1_path(dev)


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


def _mirror_pairs(devs: list[str]) -> list[str]:
    """['a','b','c','d'] -> ['mirror','a','b','mirror','c','d'] (stripe of mirrors).
    Shared by create_pool(raid10) and add_log(raid10). Caller validates even>=4."""
    out: list[str] = []
    for i in range(0, len(devs), 2):
        out += ["mirror", devs[i], devs[i + 1]]
    return out


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
        vdev_args = _mirror_pairs(devs)
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
# Per-pool maintenance via distro systemd timers (zfsutils-linux ships these
# templates DISABLED; we enable one instance per pool):
#   zfs-scrub-monthly@<pool>.timer   — always (checksum verify + self-heal)
#   zfs-trim-monthly@<pool>.timer    — only when autotrim=off (else ZFS trims live)
# `enable --now` schedules the next OnCalendar run; it does NOT kick off an
# immediate scrub. SCRUB is independent of autotrim — it's the only thing that
# self-heals, so it always runs monthly regardless of the trim choice.
# --------------------------------------------------------------------------- #
_TIMER_KINDS = ("scrub", "trim")
_TIMER_RE = re.compile(r"zfs-(?:scrub|trim)-monthly@(.+)\.timer")


def _timer_unit(kind: str, pool: str) -> str:
    return f"zfs-{kind}-monthly@{pool}.timer"


# Debian/Proxmox `zfsutils-linux` ALSO ships /etc/cron.d/zfsutils-linux, which
# scrubs/trims EVERY online pool monthly, gated by these per-pool user properties
# (default `auto` = enabled). Left alone, that cron + our per-pool timer would
# DOUBLE-schedule. When a timer enables we set the matching property to `disable`
# so the distro all-pools cron skips this pool → the timer is the single schedule.
# `org.debian:*` is a plain user property, settable/harmless on any box even where
# the Debian scripts aren't installed. Only suppressed AFTER a timer enables (never
# leaving a pool with neither), and it dies with the pool on destroy (no restore).
_PERIODIC_PROP = {"scrub": "org.debian:periodic-scrub",
                  "trim": "org.debian:periodic-trim"}


def _timer_template_exists(kind: str) -> bool:
    """Read-only probe (via run(), never dry-run-gated) — is the distro timer
    TEMPLATE installed? A box without zfsutils' timer units can't be scheduled."""
    tmpl = f"zfs-{kind}-monthly@.timer"
    out = run([_tool("systemctl"), "list-unit-files", tmpl]) or ""
    return tmpl in out


def install_pool_timers(pool: str, *, include_scrub: bool = True,
                        include_trim: bool = True,
                        dry_run: bool = False) -> tuple[bool, str]:
    """Enable the per-pool maintenance timers. Returns (ok, summary).

    Enables `zfs-scrub-monthly@<pool>.timer` when `include_scrub` (autoscrub=on)
    and `zfs-trim-monthly@<pool>.timer` when `include_trim` (autotrim=off). If a
    template unit is missing on this box we WARN and enable nothing for that kind
    (no cron fallback). `ok` reflects the SCRUB timer specifically — a pool with
    no scheduled scrub is the failure we care about; when scrub was NOT requested
    (v0.17.0 autoscrub OFF, ADR-003) there is nothing to fail on, so `ok` is True.
    """
    kinds = (["scrub"] if include_scrub else []) + (["trim"] if include_trim else [])
    enabled, warns = [], []
    scrub_ok = not include_scrub          # nothing to fail on if scrub not requested
    for kind in kinds:
        unit = _timer_unit(kind, pool)
        if not _timer_template_exists(kind):
            warns.append(f"zfs-{kind}-monthly@.timer template not found — "
                         f"no {kind} scheduled (install zfsutils-linux)")
            continue
        ok, out = run_check([_tool("systemctl"), "enable", "--now", unit],
                            dry_run=dry_run)
        if ok:
            enabled.append(unit)
            if kind == "scrub":
                scrub_ok = True
            # suppress the distro all-pools cron for THIS kind to avoid a double
            # schedule (best-effort — a failure just means a possible extra run,
            # never a gap, so it doesn't flip `ok`).
            prop = _PERIODIC_PROP[kind]
            okp, outp = run_check([_tool("zpool"), "set", f"{prop}=disable", pool],
                                  dry_run=dry_run)
            if not okp:
                warns.append(f"{prop}: {outp}")
        else:
            warns.append(f"{unit}: {out}")
    parts = []
    if enabled:
        parts.append("enabled " + ", ".join(enabled))
    if warns:
        parts.append("; ".join(warns))
    return scrub_ok, ("; ".join(parts) or "no timers enabled")


def remove_pool_timers(pool: str, *, dry_run: bool = False) -> tuple[bool, str]:
    """Disable a pool's maintenance timers. `ok` is False only when a disable
    genuinely FAILS — disabling a never-enabled instance is a systemd no-op that
    exits 0, so a scrub-only pool (autotrim=on) still reports success."""
    disabled, failed = [], []
    for kind in _TIMER_KINDS:
        unit = _timer_unit(kind, pool)
        ok, _ = run_check([_tool("systemctl"), "disable", "--now", unit],
                          dry_run=dry_run)
        (disabled if ok else failed).append(unit)
    if failed:
        return False, "disable failed: " + ", ".join(failed)
    return True, ("disabled " + ", ".join(disabled) if disabled
                  else "no timers to disable")


def prune_orphan_timers(*, dry_run: bool = False) -> list[str]:
    """Disable zfs-{scrub,trim}-monthly@<pool>.timer instances whose pool no longer
    exists. Returns unit names disabled.

    Guarded: if `zpool list` cannot be queried (transient failure) we refuse to
    prune, so a momentary error never disables every maintenance timer (F-063).
    Best-effort: a parse/enumeration failure yields no action, never a crash.
    """
    out = run([_tool("systemctl"), "list-units", "--type=timer", "--all",
               "--no-legend", "--plain",
               "zfs-scrub-monthly@*", "zfs-trim-monthly@*"]) or ""
    units = {}
    for line in out.splitlines():
        tok = line.strip().split()
        if not tok:
            continue
        m = _TIMER_RE.fullmatch(tok[0])
        if m:
            units[tok[0]] = m.group(1)
    if not units:
        return []
    # Build `live` from the SAME guarded query — never a second, unguarded
    # list_pools() call: if that one transiently timed out/failed it would return
    # an empty set and we'd disable EVERY live pool's timers (F-063). `zpool list
    # -H -o name` is one pool name per line.
    ok, out2 = run_check([_tool("zpool"), "list", "-H", "-o", "name"])
    if not ok:
        return []
    live = {ln.strip() for ln in out2.splitlines() if ln.strip()}
    disabled: list[str] = []
    for unit, pool in units.items():
        if pool in live:
            continue
        if not dry_run:
            okd, _ = run_check([_tool("systemctl"), "disable", "--now", unit])
            if not okd:
                continue
        disabled.append(unit)
    return disabled

