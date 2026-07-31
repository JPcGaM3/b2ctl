"""b2ctl.cli — command-line entrypoint for the IT-mode (HBA) build.

Subcommands:
    status [--locate] [--json]   one-shot health table + details
    watch                        interactive hotplug-aware loop
    locate <target> [seconds]    blink ONE disk's LED (~5s), by device
    offload                      safely detach or resilver a disk to offload it
    version
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import core, watch, zfs, spec, locate as locatemod, common
from . import zfs_actions
from . import backend as _backend_mod, config as _cfg_mod
from . import installer as _installer_mod
from .common import need_root, run, R, Y, G, C, N
from . import ui
from ._version import __version__      # single source of truth (F-066)


def _page(text: str, no_pager: bool = False) -> None:
    """Print `text`, handing it to a pager when it would scroll off the screen.

    Only when stdout is a terminal: a pipe or a redirect must get the plain text
    (and its full width) or `b2ctl status > report.txt` silently changes shape.
    `less -SRFX` chops long lines so the wide table scrolls SIDEWAYS instead of
    wrapping into unreadable stripes (-S), keeps the level colours (-R), skips
    paging if it turns out to fit (-F), and leaves the output on screen (-X).
    $PAGER wins when set — `PAGER=cat` disables paging (F-137).
    """
    import shlex
    import subprocess as _sp
    try:
        tty = sys.stdout.isatty()
    except (AttributeError, ValueError):
        tty = False
    if no_pager or not tty:
        print(text)
        return
    if len(text.splitlines()) < shutil.get_terminal_size().lines:
        print(text)
        return
    argv = shlex.split(os.environ.get("PAGER") or "less -SRFX")
    if not argv or not shutil.which(argv[0]):
        print(text)                     # no pager installed — never lose output
        return
    try:
        _sp.run(argv, input=text, text=True)
    except OSError:
        print(text)


def _status(args) -> int:
    # F-069 says --locate (a physical side effect) and --json (machine output)
    # must not combine. The argparse mutex only covers `status --json --locate`;
    # it CANNOT see `b2ctl --json status --locate`, because a mutually exclusive
    # group lives in one parser and --json is now also global. Re-check here so
    # the rule holds at every position (ADR-007).
    if getattr(args, "json", False) and getattr(args, "locate", False):
        from . import jsonout
        return jsonout.fail("status", jsonout.ERR_INVALID_ARG,
                            "--locate blinks physical LEDs and cannot be "
                            "combined with --json")
    tbw = spec.load()
    disks = core.scan(tbw)
    if args.json:
        from . import jsonout, schema
        return jsonout.emit("status", {
            "backend": schema.backend_json(),
            "disks": [schema.disk_json(d) for d in disks],
            "pools": _pools_payload(),
            "volumes": _volumes_payload(),
            "summary": core.assemble_storage(disks, zfs.list_pools(),
                                             _raid_volumes()),
        })
    width = None if getattr(args, "full", False) else ui.auto_width()
    # A silent zpool must not cost the operator the disk table — that table is
    # how they diagnose WHY it went silent. core.scan() has already warned and
    # marked every disk pool-unknown, so the pool/summary blocks degrade to empty
    # rather than the whole verb dying (F-143). The machine face does NOT degrade:
    # the JSON branch above lets ZfsUnavailable reach main() and become a
    # TOOL_MISSING envelope, because 'pools: []' with ok:true is a lie.
    try:
        pools = zfs.list_pools()
    except zfs.ZfsUnavailable:
        pools = []
    vols = _backend_mod.get_backend().raid_volumes()
    # Rendered as ONE string so the pager decision is made on the real height —
    # table + summary + details together are what overflows, not any one block.
    _page("\n".join([
        ui.render_table(disks, width),
        ui.render_storage(core.assemble_storage(disks, pools, vols)),
        ui.render_details(disks, pools),
    ]), no_pager=getattr(args, "no_pager", False) or getattr(args, "full", False))

    if args.locate:
        # F-001/F-002: skip ghosts (no /dev node) and rebuilding/resilvering
        # disks (CLAUDE.md §9), and route each survivor through blink_disk — PERC
        # PDs light their slot LED via perccli by enc:slot, raw disks via
        # ledctl/dd — never a raw dd fan-out on the shared VD device.
        # A PERC PD now also reports dev='-' (F-136) but IS locatable — perccli
        # lights its slot by enc:slot. Gate on "has no way to be found" (ghost),
        # not on "has no device node", or every failing hardware member would be
        # silently skipped here.
        risky = [d for d in disks
                 if d.level in ("WARNING", "CRITICAL")
                 and (d.dev not in ("-", "") or locatemod.is_perc_pd(d))
                 and d.health != "GHOST"
                 and not locatemod.is_resilvering(d)]
        if not risky:
            print(f"{G}[OK] nothing at risk to blink (ghosts/resilvering disks skipped){N}")
            return 0
        bays = ", ".join(d.bay or d.dev for d in risky)
        print(f"{Y}[!] blinking {args.seconds}s on: {bays}{N}")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, len(risky))) as ex:
            results = list(ex.map(lambda d: locatemod.blink_disk(d, args.seconds), risky))
        lit = sum(1 for ok, _ in results if ok)
        print(f"{G}[+] blinked {lit}/{len(risky)} disk(s){N}")
    return 0


# --------------------------------------------------------------------------- #
# Read-verb payloads for the machine contract (ADR-007). Each reuses the same
# scan/list path the table renderer uses — the JSON face adds no new probing.
# --------------------------------------------------------------------------- #

def _raid_volumes() -> list:
    """Hardware volumes, or [] on a box whose controller/tool is absent.

    A read verb must never die because there is no PERC: `get_backend()` calls
    common.die() (SystemExit) when no tool is found at all (ADR-007)."""
    try:
        return _backend_mod.get_backend().raid_volumes()
    except (Exception, SystemExit):
        return []


def _pools_payload() -> list:
    """ZFS pools with the level and maintenance columns merged in.

    `zfs.list_pools()` alone carries no `level` and no scrub/trim history — the
    table gets those from `zfs.pool_level` + `core.pool_maint`, so the wire
    format has to merge them too or every pool reports level: null.
    """
    from . import schema
    out = []
    for p in zfs.list_pools():
        row = dict(p)
        row["level"] = zfs.pool_level(p["name"])
        row.update(core.pool_maint(p["name"]))
        out.append(schema.pool_json(row))
    return out


def _volumes_payload() -> list:
    from . import schema
    return [schema.volume_json(v) for v in _raid_volumes()]


def _disks(args) -> int:
    """`b2ctl disks` — every disk with SMART, machine-readable."""
    from . import jsonout, schema
    disks = core.scan(spec.load())
    if not getattr(args, "json", False):
        print(ui.render_table(disks, ui.auto_width()))
        return 0
    return jsonout.emit("disks", {"disks": [schema.disk_json(d) for d in disks]})


def _pools(args) -> int:
    """`b2ctl pools` — pool health without a SMART scan (cheap to poll)."""
    from . import jsonout
    data = _pools_payload()
    if not getattr(args, "json", False):
        print(ui.render_pools(zfs.list_pools()))
        return 0
    return jsonout.emit("pools", {"pools": data})


def _volumes(args) -> int:
    """`b2ctl volumes` — hardware RAID volumes; [] in IT mode."""
    from . import jsonout
    data = _volumes_payload()
    if not getattr(args, "json", False):
        for v in data:
            print(f"vd{v.get('vd')}  {v.get('name') or '-':<16} "
                  f"{str(v.get('raid') or '?'):<8} {v.get('state') or '?':<6} "
                  f"{v.get('size') or '-'}")
        if not data:
            print("no hardware RAID volumes (IT/HBA mode, or no controller)")
        return 0
    return jsonout.emit("volumes", {"volumes": data})


def _bays_payload() -> dict:
    """Current bay labelling: the panel rules, and what each disk resolves to."""
    from . import baymap
    disks = core.scan_light()           # identity only — bays never need SMART
    rows = []
    raws = []
    for d in disks:
        raw = d.ctrl_slot or ""
        rows.append({"bay": d.bay, "raw_slot": raw or None, "dev": d.dev,
                     "serial": d.serial or None, "model": d.model or None})
        if raw:
            raws.append(raw)
    return {"panels": _cfg_mod.load_bay_map(),
            "path": _cfg_mod.bay_map_path(),
            "write_path": _cfg_mod.bay_map_write_path(),
            "detected_slots": baymap.detect_slots(raws),
            "disks": rows}


def _bays(args) -> int:
    """`b2ctl bays` — inspect and fix front-panel bay labelling.

    Read is side-effect-free (§9). The write forms exist so a web UI / MCP server
    can fix numbering without hand-editing bay_map.json, which is what an
    operator had to do before (F-140).
    """
    from . import jsonout
    as_json = getattr(args, "json", False)

    if getattr(args, "calibrate", False):
        return _bays_calibrate(as_json)

    try:
        if getattr(args, "set_reverse", None) is not None:
            panels = _cfg_mod.set_front_reverse(args.set_reverse == "on",
                                                slots=getattr(args, "slots", None))
        elif getattr(args, "set", None):
            raw, _, label = str(args.set).partition("=")
            if not label:
                raise ValueError("expected RAW=LABEL, e.g. --set 32:0=32:7")
            panels = _cfg_mod.set_bay_label(raw.strip(), label.strip())
        elif getattr(args, "clear", False):
            panels = _cfg_mod.clear_bay_map()
        else:
            panels = None                       # read-only form
    except ValueError as exc:
        if as_json:
            return jsonout.fail("bays", jsonout.ERR_INVALID_ARG, str(exc))
        print(f"{R}[-] {exc}{N}")
        return 1
    except OSError as exc:
        if as_json:
            return jsonout.fail("bays", jsonout.ERR_NEEDS_ROOT,
                                f"cannot write {_cfg_mod.bay_map_write_path()}: {exc}")
        print(f"{R}[-] cannot write {_cfg_mod.bay_map_write_path()} — "
              f"run as root ({exc}){N}")
        return 1

    if panels is not None and not as_json:
        print(f"{G}[+] wrote {_cfg_mod.bay_map_write_path()}{N}")

    data = _bays_payload()
    if as_json:
        return jsonout.emit("bays", data)
    print(f"bay_map: {data['path']}")
    print(f"  {'BAY':<10}{'RAW':<10}{'DEV':<12}{'SERIAL':<20}MODEL")
    for r in data["disks"]:
        print(f"  {str(r['bay'] or '-'):<10}{str(r['raw_slot'] or '-'):<10}"
              f"{str(r['dev']):<12}{str(r['serial'] or '-'):<20}{r['model'] or '-'}")
    if data["detected_slots"]:
        print(f"  detected slot counts: {data['detected_slots']}")
    return 0


def _bays_calibrate(as_json: bool) -> int:
    """Blink each bay, ask which slot lit, then propose a rule and write it.

    Replaces the old "calibrate with b2ctl locate <serial>, then hand-edit JSON"
    loop. Interactive by nature, so it refuses under --json rather than hanging
    a machine caller on a prompt (ADR-007).
    """
    from . import jsonout
    if as_json:
        return jsonout.fail("bays", jsonout.ERR_UNSUPPORTED,
                            "--calibrate is interactive; use --set-reverse/--set "
                            "for non-interactive changes")
    # Only real enc:slot drives. An NVMe's bay is a PCIe address ("PCIe2:0")
    # relabelled by the `type:nvme` back panel — the reverse-slots rule does not
    # apply to it, and splitting it would feed "PCIe2" in as an enclosure number.
    import re as _re
    disks = [d for d in core.scan_light()
             if _re.fullmatch(r"\d+:\d+", d.ctrl_slot or d.bay or "")]
    if not disks:
        print(f"{Y}[!] no enc:slot drives to calibrate (NVMe bays are set in the "
              f"`type:nvme` back panel of bay_map.json, not by slot reversal){N}")
        return 1
    print(f"{C}Calibrating {len(disks)} bay(s). For each drive b2ctl blinks its "
          f"LED — type the slot number you SEE lit, or blank to skip.{N}")
    observed = {}
    for d in disks:
        raw = d.ctrl_slot or d.bay
        print(f"  blinking {ui.disk_label(d)} (raw {raw}) ...")
        if not common.is_dry_run():
            locatemod.blink_disk(d, 3)
        ans = common.ask("    which slot lit up? > ")
        if ans.isdigit():
            observed[raw] = int(ans)
    if not observed:
        print("nothing observed — cancelled")
        return 1

    encs = {raw.split(":")[0] for raw in observed}
    reversal = None
    if len(encs) == 1:
        enc = encs.pop()
        n = max(max(int(r.split(":")[1]) for r in observed),
                max(observed.values())) + 1
        if all((n - 1) - int(raw.split(":")[1]) == seen
               for raw, seen in observed.items()):
            reversal = (enc, n)

    if reversal:
        enc, n = reversal
        print(f"{G}  every bay matches a clean mirror-reversal across {n} slots.{N}")
        if not common.confirm(f"  set reverse_slots=true, slots_per_enclosure={n}?"):
            print("cancelled")
            return 1
        path = _cfg_mod.bay_map_write_path()
        try:
            _cfg_mod.set_front_reverse(True, slots=n)
        except OSError as exc:
            print(f"{R}[-] cannot write {path} — run as root ({exc}){N}")
            return 1
        print(f"{G}[+] wrote {path}{N}")
        return 0

    # Not a clean reversal — write the observed permutation verbatim. An explicit
    # map wins over reverse_slots in baymap.remap_slot, so this is exact.
    print(f"{Y}  not a clean reversal — writing {len(observed)} explicit "
          f"map entries instead.{N}")
    for raw, seen in sorted(observed.items()):
        print(f"    {raw} -> {raw.split(':')[0]}:{seen}")
    if not common.confirm("  write these entries?"):
        print("cancelled")
        return 1
    try:
        for raw, seen in observed.items():
            _cfg_mod.set_bay_label(raw, f"{raw.split(':')[0]}:{seen}")
    except (OSError, ValueError) as exc:
        print(f"{R}[-] cannot write: {exc}{N}")
        return 1
    print(f"{G}[+] wrote {_cfg_mod.bay_map_write_path()}{N}")
    return 0


def _progress(args) -> int:
    """`b2ctl progress` — what is running right now, without blocking on it.

    A scrub takes hours and a resilver longer; an MCP/web caller cannot hold a
    request open for that. Everything here is a PURE READ of state the kernel and
    the controller already publish, so polling is cheap and side-effect-free (§9).
    """
    from . import jsonout
    from . import burnin as _burnin_mod
    items = []

    for p in zfs.list_pools():
        name = p["name"]
        try:
            sc = zfs.poll_scrub_status(name)
        except Exception:
            sc = {}
        if sc.get("in_progress"):
            items.append({"kind": "scrub", "target": name,
                          "pct": sc.get("done"), "eta": sc.get("eta"),
                          "state": "running"})
        try:
            tr = zfs.poll_trim_status(name)
        except Exception:
            tr = {}
        if tr.get("trimming"):
            items.append({"kind": "trim", "target": name,
                          "pct": tr.get("done"), "eta": None, "state": "running"})

    # Hardware rebuilds, one per PERC member that reports one in flight.
    try:
        from . import hba_raid
        if hba_raid.have_tool():
            for d in core.scan_light():
                if d.array_type == "HW" and d.ctrl_slot:
                    rb = hba_raid.rebuild_progress(d.ctrl_slot,
                                                   d.ctrl if d.ctrl is not None
                                                   else hba_raid.CONTROLLER)
                    if rb.get("in_progress"):
                        items.append({"kind": "rebuild", "target": d.ctrl_slot,
                                      "pct": rb.get("pct"), "eta": None,
                                      "state": "running"})
    except Exception:
        pass

    # Health-check (burn-in) runs detached and keeps its own state file — a flat
    # list of per-disk records, so `--status` can re-attach after a Ctrl-C.
    try:
        for rec in _burnin_mod.load_state():
            st = _burnin_mod.selftest_status(rec["dev"], rec.get("dtype", ""))
            items.append({"kind": "health-check",
                          "target": rec.get("bay") or rec.get("dev"),
                          "dev": rec.get("dev"), "serial": rec.get("serial") or None,
                          "pct": st.get("pct"),
                          "eta": ui.fmt_eta(st.get("eta_min")) or None,
                          "state": "running" if st.get("running") else "done"})
    except Exception:
        pass

    if getattr(args, "json", False):
        return jsonout.emit("progress", {"running": items})
    if not items:
        print("nothing running")
        return 0
    for it in items:
        pct = "-" if it["pct"] is None else f"{it['pct']}%"
        print(f"  {it['kind']:<14}{str(it['target']):<20}{pct:<8}{it.get('eta') or ''}")
    return 0


def _mutation_precheck(args):
    """Resolve a named pool/disk BEFORE a --json mutation runs (F-146).

    Without this, a typo'd pool/disk name fell all the way through to the
    underlying verb's own interactive 'no such pool'/picker handling, which
    declines under --confirm and comes back as a generic OP_FAILED — a client
    cannot tell "you named something that doesn't exist" from "the operation
    ran and failed". `destroy`/`scrub`/`trim` name a pool; `offload`/`replace`/
    `swap`/`demote`/`locate` name a disk via --disk (locate: positional
    `target`). `maint scrub`/`maint trim` share `_scrub`/`_trim` but arrive
    here with cmd='maint' (the top parser's dest), so check `maint_cmd` too —
    the envelope still reports `command: 'maint'`, matching what
    `_json_mutation` would have emitted for it anyway.

    A pool name is checked against `zfs.list_pools()`, which RAISES
    ZfsUnavailable (not an empty list) when zpool does not answer — that
    propagates straight out to main()'s existing handler, which reports
    TOOL_MISSING. Catching it here and reporting POOL_NOT_FOUND would tell a
    client the pool is gone when b2ctl merely could not look (F-143's rule).

    Returns an envelope rc to short-circuit the mutation, or None to proceed.
    """
    from . import jsonout
    cmd = getattr(args, "cmd", "?")
    verb = getattr(args, "maint_cmd", None) if cmd == "maint" else cmd

    if verb in ("destroy", "scrub", "trim"):
        pool = getattr(args, "pool", None)
        if pool is None:                # omitted -> today's interactive picker
            return None
        names = {p["name"] for p in zfs.list_pools()}
        if pool not in names:
            return jsonout.fail(cmd, jsonout.ERR_POOL_NOT_FOUND,
                                f"no pool named '{pool}'")
        return None

    if verb in ("offload", "replace", "swap", "demote", "locate"):
        target = getattr(args, "target" if verb == "locate" else "disk", None)
        if target is None:              # omitted -> today's interactive picker
            return None
        disks = core.scan_light()       # identity only, no SMART needed (F-102)
        hit = any(target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", ""),
                             d.by_id) for d in disks)
        if not hit:
            return jsonout.fail(cmd, jsonout.ERR_DISK_NOT_FOUND,
                                f"no disk matches '{target}'")
        return None

    return None


def _json_mutation(args) -> int:
    """Run a MUTATING verb under --json and wrap whatever it printed.

    Read verbs build their own envelope. Mutating ones narrate as they work —
    confirm boxes, resilver bars, per-step results — all to stdout, which would
    shred the envelope. Capturing it here and returning it as `data.log` keeps
    that narration available to a web UI without rewriting several hundred
    print() calls across watch/raid_actions/safety (ADR-007 phase 2).
    """
    import contextlib
    import io
    from . import jsonout
    precheck_rc = _mutation_precheck(args)
    if precheck_rc is not None:
        return precheck_rc
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = args.func(args)
    cmd = getattr(args, "cmd", "?")
    log = buf.getvalue()
    if rc == 0:
        return jsonout.emit(cmd, {"log": log})
    # A non-zero rc here means the operation failed OR was declined at a confirm;
    # both are "did not happen", and the log says which.
    return jsonout.fail(cmd, jsonout.ERR_OP_FAILED,
                        "the command did not complete — see data.log",
                        data={"log": log})


def _watch(_args) -> int:
    return watch.run()


def _locate(args) -> int:
    disks = core.scan_light()       # locate only needs identity + topology (F-102)
    d = next((x for x in disks if args.target in
              (x.bay, x.serial, x.dev, x.dev.replace("/dev/", ""))), None)
    if d is None:
        print(f"{R}[-] could not resolve '{args.target}' to a disk{N}")
        return 1
    if d.dev == "-" or d.health == "GHOST":
        print(f"{R}[-] cannot locate a GHOST disk (OS rejected it, no /dev node){N}")
        return 1
    where = f"bay {d.bay}" if locatemod.is_perc_pd(d) else d.dev
    print(f"{Y}[*] blinking {where} for {args.seconds}s ...{N}")
    ok, method = locatemod.blink_disk(d, args.seconds)
    if method == "resilvering":
        print(f"{R}[-] refuse: '{args.target}' is resilvering/rebuilding — never pull "
              f"a disk mid-resilver (CLAUDE.md §9){N}")
        return 1
    print((G + f"[+] done (via {method})" if ok else R + "[-] failed") + N)
    return 0 if ok else 1


# ZFS lifecycle subcommands go through the public zfs_actions contract (not
# watch's underscore-privates) and propagate a real exit code (F-070).
def _disk_arg(args):
    """The --disk token, or None to keep today's interactive picker (ADR-007)."""
    return getattr(args, "disk", None)


def _offload(args) -> int:
    return zfs_actions.offload(target=_disk_arg(args))


def _replace(args) -> int:
    return zfs_actions.replace(target=_disk_arg(args))


def _create(args) -> int:
    disks = [t for t in (getattr(args, "disks", None) or "").split(",") if t.strip()]
    return zfs_actions.create(raid10=getattr(args, "raid10", False),
                              raid_type=getattr(args, "type", None),
                              disks=[t.strip() for t in disks] or None,
                              name=getattr(args, "name", None))


def _destroy(args) -> int:
    return zfs_actions.destroy(pool=getattr(args, "pool", None))


def _swap(args) -> int:
    return zfs_actions.swap(target=_disk_arg(args))


def _demote(args) -> int:
    return zfs_actions.demote(target=_disk_arg(args))


def _scrub(args) -> int:
    return zfs_actions.scrub(getattr(args, "pool", None))


def _trim(args) -> int:
    return zfs_actions.trim(getattr(args, "pool", None))


def _maint(args) -> int:
    """`b2ctl maint --log` — read-only maintenance history (scrub/trim/health)."""
    from . import maint as _maint_mod
    events = _maint_mod.load_events(last=getattr(args, "last", 30))
    if getattr(args, "json", False):
        from . import jsonout
        return jsonout.emit("maint", {"events": events})
    if not events:
        print("No maintenance events logged yet.")
        return 0
    print(f"\n{'WHEN':<21} {'KIND':<7} {'TARGET':<20} {'STATUS':<8} DETAIL")
    print("─" * 90)
    for e in events:
        st = e.get("status", "?")
        color = G if st == "ok" else (R if st == "fail" else Y)
        print(f"{e.get('ts', ''):<21} {e.get('kind', ''):<7} "
              f"{str(e.get('target', ''))[:19]:<20} {color}{st:<8}{N} "
              f"{e.get('detail', '')}")
    print()
    return 0


def _resolve_devs(tokens, *, strict: bool = False):
    """Map bay/serial/dev/by-id tokens to stable by-id paths.

    strict=True (the pool ADD paths, cache-add/log-add) enforces §9 'always act
    on by-id, never /dev/sdX': it returns None (after printing why) if a token
    does not resolve to a scanned disk, or resolves to a disk with no by-id link
    yet — so a freshly hot-plugged disk is never added under an unstable /dev/sdX
    that shuffles on reboot (F-032). The rm paths stay permissive: a raw zpool
    leaf token that matches no disk is passed straight to `zpool remove`.

    F-144: strict mode used to take `next(...)` — the FIRST match — silently.
    `watch._resolve_target` (the interactive equivalent) refuses an ambiguous
    match instead of guessing; mirror that here, since guessing which disk the
    operator meant is exactly how the wrong disk gets wiped (§9). Permissive
    mode is unchanged — a raw leaf token deliberately passes through verbatim.
    """
    disks = core.scan_light()       # resolution needs by-id/bay/serial, not SMART (F-102)
    out = []
    for t in tokens:
        if strict:
            matches = [d for d in disks if t in (d.bay, d.serial, d.dev,
                       d.dev.replace("/dev/", ""), d.by_id)]
            if not matches:
                print(f"{R}[-] '{t}' matches no disk — check the bay/serial, or "
                      f"wait for udev if just inserted.{N}")
                return None
            if len(matches) > 1:
                labels = ", ".join(ui.disk_label(m) for m in matches)
                print(f"{R}[-] '{t}' is ambiguous — matches {len(matches)} disks: "
                      f"{labels}{N}")
                return None
            match = matches[0]
            if not match.by_id:
                print(f"{R}[-] {match.dev} has no stable by-id link yet — wait for "
                      f"udev / re-insert before adding it to a pool.{N}")
                return None
            out.append(match.by_id)
        else:
            match = next((d for d in disks if t in (d.bay, d.serial, d.dev,
                          d.dev.replace("/dev/", ""), d.by_id)), None)
            out.append((match.by_id or match.dev) if match else t)
    return out


def _confirm_pool_op(op: str, pool: str, devs: list[str]) -> bool:
    """Confirm a pool-mutating aux-vdev op, printing the RESOLVED by-id devices,
    pool, and operation (CLAUDE.md §9). Auto-proceeds under --dry-run (run_check
    then prints the [DRY-RUN] preview and mutates nothing)."""
    if watch._DRY_RUN:
        return True
    from .common import confirm
    print(f"{Y}[?] {op} on pool '{pool}': {' '.join(devs)}{N}")
    return confirm(f"    {op}?")


def _partition_devs(devs, size):
    """Over-provision the CLI aux-add paths: WIPE then partition each resolved
    by-id device to `size`, returning the -part1 tokens (or None on failure).
    The wipe is mandatory — `sgdisk -n 1:0:+<size>` needs a clean GPT or it places
    partition 1 past a stale one (the used-disk `partition failed` bug, F-132).

    F-144: the size used to be validated only INSIDE zfs.partition, i.e. AFTER
    the wipe loop had already destroyed the first disk's partition table — a
    typo'd --size wiped a disk and then failed. Mirror watch._maybe_partition:
    parse `size` and check it against every target's Disk.size_bytes BEFORE
    anything is touched, so an invalid/oversized size rejects the whole batch
    up front."""
    from . import zfs
    from .common import confirm
    sizes = {d.by_id: d.size_bytes for d in core.scan_light() if d.by_id}
    req = zfs.parse_size(size)
    if req is None:
        print(f"{R}[-] invalid size '{size}'{N}")
        return None
    for dev in devs:
        sz = sizes.get(dev)
        if sz and req > sz:
            print(f"{R}[-] size '{size}' exceeds {dev} ({sz} bytes){N}")
            return None
    # §9: the wipe is destructive, so confirm it HERE — before any device is
    # touched — not at the later add-cache/add-log prompt (which runs post-wipe).
    print(f"{Y}[!] over-provision will WIPE then partition: {', '.join(devs)}{N}")
    if not confirm("wipe and partition these disk(s)?"):
        print(f"{Y}[-] cancelled{N}")
        return None
    out = []
    for dev in devs:
        wok, wout = zfs.wipe(dev, dry_run=watch._DRY_RUN)
        if not wok:
            print(f"{R}[-] wipe {dev}: {wout}{N}")
            return None
        ok, part = zfs.partition(dev, size, max_bytes=sizes.get(dev),
                                 dry_run=watch._DRY_RUN)
        if not ok:
            print(f"{R}[-] partition {dev}: {part}{N}")
            return None
        out.append(part)
    return out


def _cache_add(args) -> int:
    from . import zfs
    devs = _resolve_devs(args.devs, strict=True)
    if devs is None:
        return 1
    if getattr(args, "size", None):
        devs = _partition_devs(devs, args.size)
        if devs is None:
            return 1
    if not _confirm_pool_op("add L2ARC cache", args.pool, devs):
        print(f"{Y}[-] cancelled{N}"); return 1
    ok, out = zfs.add_cache(args.pool, devs, dry_run=watch._DRY_RUN)
    print((f"{G}[+] L2ARC cache added to {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _cache_rm(args) -> int:
    from . import zfs
    dev = _resolve_devs([args.dev])[0]
    if not _confirm_pool_op("remove cache device", args.pool, [dev]):
        print(f"{Y}[-] cancelled{N}"); return 1
    ok, out = zfs.remove_vdev(args.pool, dev, dry_run=watch._DRY_RUN)
    print((f"{G}[+] removed from {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _log_add(args) -> int:
    from . import zfs
    devs = _resolve_devs(args.devs, strict=True)
    if devs is None:
        return 1
    if getattr(args, "size", None):
        devs = _partition_devs(devs, args.size)
        if devs is None:
            return 1
    # single/mirror/raid10 only — raidz is invalid for a log vdev (add_log rejects it).
    raid_type = ("raid10" if getattr(args, "raid10", False)
                 else "mirror" if getattr(args, "mirror", False) else None)
    if len(devs) == 1:
        print(f"{Y}[!] SLOG not mirrored: losing this log device can lose "
              f"in-flight sync writes.{N}")
    print(f"{Y}[!] ensure this SSD has Power-Loss Protection (PLP).{N}")
    if not _confirm_pool_op("add SLOG log", args.pool, devs):
        print(f"{Y}[-] cancelled{N}"); return 1
    ok, out = zfs.add_log(args.pool, devs, raid_type=raid_type, dry_run=watch._DRY_RUN)
    print((f"{G}[+] SLOG added to {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _log_rm(args) -> int:
    from . import zfs
    dev = _resolve_devs([args.dev])[0]
    if not _confirm_pool_op("remove log device", args.pool, [dev]):
        print(f"{Y}[-] cancelled{N}"); return 1
    ok, out = zfs.remove_vdev(args.pool, dev, dry_run=watch._DRY_RUN)
    print((f"{G}[+] removed from {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _cache_replace(args) -> int:
    old = _resolve_devs([args.old])[0]            # permissive: raw leaf token OK
    new = _resolve_devs([args.new], strict=True)  # strict: new must be a real by-id disk
    if new is None:
        return 1
    return zfs_actions.cache_replace(args.pool, old, new[0])


def _log_replace(args) -> int:
    old = _resolve_devs([args.old])[0]
    new = _resolve_devs([args.new], strict=True)
    if new is None:
        return 1
    return zfs_actions.log_replace(args.pool, old, new[0])


def _burnin(args) -> int:
    from . import burnin
    if args.cancel_all:
        return burnin.cancel_all(dry_run=watch._DRY_RUN)
    if args.cancel:
        return burnin.cancel(args.cancel, dry_run=watch._DRY_RUN)
    if args.status:
        return burnin.status_view()
    if not args.target:
        print(f"{R}maint health: give one or more target disks, or --status{N}",
              file=sys.stderr)
        return 2
    # Mirror the interactive [m]aint health path: record a 'started' event per
    # target so `b2ctl maint --log` shows health-checks regardless of entry point.
    from . import maint
    kind = "short" if args.short else "long"
    detail = f"smartctl -t {kind}" + (" + badblocks" if args.scan else "")
    if not watch._DRY_RUN:                    # dry-run must not pollute the maint log
        for t in args.target:
            maint.log_event("health", t, "started", detail)
    return burnin.run_multi(args.target, spec.load(), do_scan=args.scan,
                            kind=kind, dry_run=watch._DRY_RUN)


def _raid_replace(args) -> int:
    from . import raid_actions
    return raid_actions.replace(getattr(args, "target", None))


def _raid_offline(args) -> int:
    from . import raid_actions
    return raid_actions.offline(args.target)


def _raid_create(args) -> int:
    from . import raid_actions
    drives = [s for s in (args.drives or "").split(",") if s]
    return raid_actions.create_vd(args.level, drives)


def _raid_del(args) -> int:
    from . import raid_actions
    return raid_actions.delete_vd(args.vd)


def _version(args) -> int:
    from . import jsonout
    if getattr(args, "json", False):
        # schema_version rides along so a client can negotiate the wire format
        # without parsing the product version (ADR-007).
        return jsonout.emit("version", {"version": __version__,
                                        "schema_version": jsonout.SCHEMA_VERSION})
    print(f"b2ctl {__version__}")
    return 0


def _raid_foreign(args) -> int:
    from . import raid_actions
    action = ("import" if getattr(args, "do_import", False)
              else "clear" if getattr(args, "clear", False) else "show")
    ctrl = getattr(args, "controller", None)
    if getattr(args, "json", False):
        from . import hba_raid, jsonout
        if action != "show":
            # Import/clear mutate the controller and go through the interactive
            # confirms; machine-driven mutation lands in phase 2 (ADR-007).
            return jsonout.fail("raid-foreign", jsonout.ERR_UNSUPPORTED,
                                "--import/--clear are interactive; JSON output "
                                "covers the read form only in this release")
        c = ctrl if ctrl is not None else hba_raid.CONTROLLER
        try:
            groups = hba_raid.foreign_config(c)
            bays = sorted(hba_raid.foreign_bays(c))
        except (Exception, SystemExit):
            return jsonout.fail("raid-foreign", jsonout.ERR_TOOL_MISSING,
                                "perccli is not available on this host")
        return jsonout.emit("raid-foreign",
                            {"controller": c, "groups": groups, "bays": bays})
    return raid_actions.foreign(action, ctrl)


def _check(_args) -> int:
    """Check all required tools and show environment summary."""
    if getattr(_args, "json", False):
        from . import jsonout, schema
        import shutil as _sh
        tools = []
        for name in ("smartctl", "lsblk", "zpool", "sas2ircu", "perccli",
                     "perccli64", "ledctl", "badblocks", "sgdisk", "wipefs"):
            path = _cfg_mod.tool(name)
            tools.append({"name": name, "path": _sh.which(path) or None,
                          "present": bool(_sh.which(path))})
        return jsonout.emit("check", {
            "root": os.geteuid() == 0,
            "backend": schema.backend_json(),
            "tools": tools,
        })
    ok_mark = f"{G}[✔]{N}"
    fail_mark = f"{R}[✗]{N}"
    warn_mark = f"{Y}[!]{N}"

    print(f"\n{C}[b2ctl environment check]{N}")

    # root check
    if os.geteuid() == 0:
        print(f"  {ok_mark} Running as root")
    else:
        print(f"  {warn_mark} Not running as root (some checks may fail)")

    # tool checks with version. perccli64/perccli are the same tool (64-bit
    # binary vs copied name) — show one row resolving to whichever exists.
    import shutil as _shutil
    _TOOL_VERSION_ARGS = {
        "smartctl":  ["--version"],
        "sas2ircu":  ["list"],
        "perccli":   ["show", "ctrlcount"],
        "zpool":     ["version"],
        "wipefs":    ["--version"],
        "sgdisk":    ["--version"],
        "udevadm":   ["--version"],
        "dd":        ["--version"],
    }

    for tname, ver_args in _TOOL_VERSION_ARGS.items():
        if tname == "perccli":
            path = _shutil.which("perccli") or _shutil.which("perccli64") \
                or _cfg_mod.tool("perccli")
        else:
            path = _cfg_mod.tool(tname)
        out = run([path] + ver_args)
        if out:
            ver = out.splitlines()[0][:60] if out else ""
            print(f"  {ok_mark} {tname:<12} {path:<40} ({ver.strip()})")
        else:
            hint = ""
            if tname == "sas2ircu":
                if os.path.isfile(path):
                    hint = " (binary exists but won't execute — run: apt-get install -y libc6-i386)"
                else:
                    hint = " (needed for IT/HBA mode)"
            elif tname == "perccli":
                hint = " (needed for RAID mode)"
            print(f"  {fail_mark} {tname:<12} not found{hint}")

    # backend detection
    print()
    try:
        bk = _backend_mod.get_backend()
        print(f"  {ok_mark} Detected backend: {bk.name.upper()}-mode")
        if bk.have_tool():
            bm = bk.bay_map()
            # bm is serial->'enc:slot', so its values are BAYS, not controllers;
            # report the mapped-disk count and distinct enclosures (F-071).
            encl = {v.split(":")[0] for v in bm.values() if ":" in v}
            print(f"  {ok_mark} Bays mapped: {len(bm)} disks"
                  + (f" across {len(encl)} enclosure(s)" if encl else ""))
    except SystemExit:
        print(f"  {fail_mark} Backend detection failed — set controller.mode in config")

    # config file status
    cfg_path = _cfg_mod.CONFIG_PATH
    if os.path.exists(cfg_path):
        print(f"  {ok_mark} Config: {cfg_path}")
    else:
        print(f"  {warn_mark} Config: {cfg_path} (missing — using defaults, run 'b2ctl config init' to create)")

    return 0


def _install(args) -> int:
    """`b2ctl install` — 1:1 mirror of `./install.sh`:

      (no flag)     base report (no download, no root)   == ./install.sh
      --with-tools  download + install sas2ircu+perccli   == ./install.sh --with-tools
      --perc        perccli  + controller.mode=raid       == ./install.sh --perc
      --flash       sas2ircu + controller.mode=it         == ./install.sh --flash
      --tool TOOL   install just that one tool
    """
    print()
    print(f"{C}[b2ctl install]{N}")

    def _need_root() -> bool:
        if os.geteuid() != 0:
            print(f"{R}[-] this install action requires root{N}")
            return False
        return True

    if getattr(args, "perc", False):
        if not _need_root():
            return 1
        _installer_mod.install_profile("perc")
    elif getattr(args, "flash", False):
        if not _need_root():
            return 1
        _installer_mod.install_profile("flash")
    elif getattr(args, "with_tools", False):
        if not _need_root():
            return 1
        _installer_mod.install_tools(["sas2ircu", "perccli"])
    elif getattr(args, "tool", None):
        if not _need_root():
            return 1
        _installer_mod.install_tools([args.tool])
    else:
        _installer_mod.install_base()          # no download, no root needed
    print()
    return 0


# Operator-editable data files that `b2ctl update` syncs into /etc/b2ctl and
# binds via config (bundled name, /etc destination, config key).
_MANAGED = [
    ("bay_map.json",  _cfg_mod.STD_BAY_MAP,  "bay_map_path"),
    ("ssd_spec.json", _cfg_mod.STD_SSD_SPEC, "ssd_spec_path"),
]


def _sync_resource(bundled_name: str, dest: str, force: bool) -> str:
    """Copy a bundled data file to its /etc destination without clobbering
    operator edits. Returns one of: created / current / customized-kept /
    updated (backup .bak) / missing-bundled. A file that differs from the bundled
    copy is treated as operator-customized and preserved unless force=True (then
    backed up)."""
    import shutil as _shutil
    import filecmp
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", bundled_name))
    if not os.path.exists(src):
        # install.sh deploys bay_map.json only if present, so a checkout without
        # it is a legitimate install — don't crash `b2ctl update` (F-072).
        return "missing-bundled"
    if not os.path.exists(dest):
        _shutil.copy2(src, dest)
        return "created"
    if filecmp.cmp(src, dest, shallow=False):
        return "current"
    if force:
        _shutil.copy2(dest, dest + ".bak")
        _shutil.copy2(src, dest)
        return "updated (backup .bak)"
    return "customized-kept"


def _update(args) -> int:
    """Validate config, then (as root) sync bay_map/ssd_spec into /etc/b2ctl."""
    force = getattr(args, "force", False) or getattr(args, "export_bay_map", False)

    print(f"\n{C}[b2ctl update]{N}")
    results = _cfg_mod.validate()
    _STATUS_COLOR = {"ok": G, "warn": Y, "error": R}
    _STATUS_ICON  = {"ok": "[✔]", "warn": "[i]", "error": "[✗]"}
    for field, status, msg in results:
        color = _STATUS_COLOR.get(status, N)
        icon  = _STATUS_ICON.get(status, "[?]")
        print(f"  {color}{icon}{N} {field:<12} {msg}")

    if os.geteuid() != 0:
        print(f"\n  {Y}[i]{N} run as root to sync {_cfg_mod.STD_DIR} "
              f"(bay_map, ssd_spec) + bind config")
        print()
        return 0

    if getattr(args, "export_bay_map", False):
        print(f"\n  {Y}[i]{N} note: plain `b2ctl update` now syncs bay_map + ssd_spec")

    import json as _json
    os.makedirs(_cfg_mod.STD_DIR, exist_ok=True)
    cfg_path = _cfg_mod.CONFIG_PATH
    try:
        with open(cfg_path) as f:
            cfg = _json.load(f)
    except FileNotFoundError:
        cfg = _cfg_mod.load()
    except _json.JSONDecodeError as exc:
        # F-147 companion bug: a corrupt config used to be silently REWRITTEN
        # with load()'s all-defaults fallback here, discarding whatever the
        # operator had in tool_paths/controller.mode/pools. Refuse instead —
        # the file needs a human, not a guess (matches config._load_for_write's
        # rule for every other config writer).
        print(f"\n  {R}[-] {cfg_path} is not valid JSON ({exc}) — refusing to "
              f"touch it. Fix or remove the file, then re-run `b2ctl update`.{N}")
        return 1
    except OSError as exc:
        print(f"\n  {R}[-] cannot read {cfg_path}: {exc}{N}")
        return 1

    print(f"\n{C}[sync {_cfg_mod.STD_DIR}]{N}")
    _SYNC_ICON = {"created": f"{G}[✔]{N}", "current": f"{G}[✔]{N}",
                  "customized-kept": f"{Y}[i]{N}", "missing-bundled": f"{Y}[i]{N}"}
    for bundled_name, dest, key in _MANAGED:
        state = _sync_resource(bundled_name, dest, force)
        # Bind the /etc path only if a file is actually there — a missing bundled
        # copy with no /etc file must not point config at a nonexistent path (F-072).
        if state != "missing-bundled" or os.path.exists(dest):
            cfg[key] = dest  # bind to the absolute /etc path (directory-independent)
        icon = _SYNC_ICON.get(state, f"{G}[✔]{N}")
        note = "  (use --force to overwrite)" if state == "customized-kept" else ""
        if state == "missing-bundled":
            note = "  (no bundled copy — skipped)"
        print(f"  {icon} {os.path.basename(dest):<14} {state}{note}  →  {dest}")

    # F-147: a plain truncating open(...,"w") here regressed the crash-safety
    # every other config writer gets from atomic_write_json (ENOSPC/crash mid-
    # write could leave a truncated file that load() then reads as all-
    # defaults). Clear the in-process cache afterwards the way config.set_mode
    # does, so a later read in the same process sees what was just written.
    _cfg_mod.atomic_write_json(cfg_path, cfg)
    _cfg_mod._cache = None
    print(f"\n  {G}[✔]{N} config bound: bay_map_path, ssd_spec_path → {_cfg_mod.STD_DIR}")
    print(f"      Edit those files freely — install.sh won't overwrite them; "
          f"`b2ctl update` keeps your edits.")
    print()
    return 0


def _config_show(args) -> int:
    if getattr(args, "json", False):
        from . import jsonout
        return jsonout.emit("config", {
            "config": _cfg_mod.load(),
            "paths": {"config": _cfg_mod.CONFIG_PATH,
                      "bay_map": _cfg_mod.bay_map_path(),
                      "ssd_spec": _cfg_mod.ssd_spec_path()},
        })
    print(_cfg_mod.as_json())
    return 0


def _config_init(_args) -> int:
    path = _cfg_mod.CONFIG_PATH
    if os.path.exists(path):
        print(f"{Y}[!] {path} already exists. Delete it first to regenerate.{N}")
        return 1
    # 'config' is exempt from the root gate, so a non-root user reaches here and
    # the write fails with PermissionError — surface the house-style one-liner
    # instead of a raw traceback (F-034).
    try:
        cfg = _cfg_mod.load()
        # F-148: the writer that CREATES the file must obey the same rules as
        # every writer that later rewrites it — atomic, and 0600 rather than
        # whatever the umask gives. This is the file whose tool_paths become root
        # execution and which config.load() now trust-checks (F-147); it was the
        # last open(...,"w") + json.dump in the package. atomic_write_json does
        # its own makedirs and raises OSError on a permission failure, so the
        # non-root one-liner below (F-034) still fires unchanged.
        _cfg_mod.atomic_write_json(path, cfg)
    except OSError as exc:
        print(f"{R}[-] cannot write {path} — run as root ({exc}){N}")
        return 1
    print(f"{G}[+] Written: {path}{N}")
    print(f"    Edit tool_paths to override binary locations.")
    print(f"    Set controller.mode to 'it' or 'raid' to skip auto-detection.")
    return 0


def _log_cmd(args):
    from . import safety
    entries = safety.load_log(last=getattr(args, "last", 20))
    if getattr(args, "json", False):
        from . import jsonout
        return jsonout.emit("log", {"entries": entries})
    if not entries:
        print("No operations logged yet.")
        return 0
    print(f"\n{'OP_ID':<28} {'OP':<10} {'BAY':<4} {'SERIAL':<16} {'POOL':<8} {'STATUS':<7} {'STARTED'}")
    print("─" * 100)
    for e in entries:
        status = e.get("status", "?")
        color = G if status == "ok" else (R if status == "fail" else Y)
        print(
            f"{e.get('op_id',''):<28} "
            f"{e.get('op',''):<10} "
            f"{str(e.get('disk_bay','')):<4} "
            f"{e.get('disk_serial',''):<16} "
            f"{e.get('pool',''):<8} "
            f"{color}{status:<7}{N} "
            f"{e.get('started_at','')}"
        )
    print()


def _rollback_cmd(op_id: str) -> int:
    """Reverse a logged operation. Always returns an explicit int (F-146) —
    every path used to fall off the end returning None, which `_json_mutation`
    reads as a non-zero rc and reports OP_FAILED even when the rollback ran
    and succeeded."""
    from . import safety
    entry = safety.find_entry(op_id)
    if entry is None:
        print(f"Op not found: {op_id}")
        return 1
    hint = entry.get("rollback_hint")
    if not hint:
        snap = entry.get("snapshot_path", "")
        print("Op not reversible.")
        if snap:
            print(f"  See snapshot: {snap}")
        return 1
    # F-013: the create-op hint ends with a '# WARNING …' comment; strip it
    # before splitting so the comment words never become command tokens.
    cmd = hint.split("#", 1)[0].split()
    if not cmd:
        print("  Rollback hint is empty after stripping its comment — resolve manually.")
        return 1
    if any(t.startswith("<") and t.endswith(">") for t in cmd):
        print("  Rollback hint contains unresolved placeholders — resolve manually:")
        print(f"     {hint}")
        return 1
    # F-146: the hint is free-text prose for irreversible ops (e.g. aux-repair's
    # "aux vdev repair on tank: verify `zpool status tank` — …"), and naive
    # whitespace-splitting turns THAT into an argv and hands it to run_check
    # with no verb allowlist. Gate cmd[0]'s basename on the same WRITE_CMDS the
    # rest of the project already trusts for this exact purpose (dry-run gating)
    # — anything else is refused before it ever reaches a subprocess.
    if os.path.basename(cmd[0]) not in safety.WRITE_CMDS:
        print(f"  Rollback hint's command '{cmd[0]}' is not a recognised "
              f"write command — refusing to run it. Resolve manually:")
        print(f"     {hint}")
        return 1
    print(f"\nOp:       {entry.get('op')}  ({entry.get('started_at','')})")
    print(f"Disk:     bay {entry.get('disk_bay')} | {entry.get('disk_serial')}")
    print(f"Pool:     {entry.get('pool')}/{entry.get('vdev')}")
    print(f"Rollback: {hint}\n")
    # F-146: rollback was the one prompt in the product still using a raw
    # input() (never caught EOF, never honored --confirm). common.confirm
    # covers both.
    if not common.confirm("Execute rollback?"):
        print("Cancelled.")
        return 1
    from .common import run_check
    # F-013: honor --dry-run so a rollback PREVIEW never runs the stored zpool cmd.
    dry = watch._DRY_RUN
    rb_op_id = safety.begin_op(
        f"rollback-{entry.get('op')}", entry.get("disk_serial", ""),
        entry.get("disk_bay"), entry.get("dev_path", ""),
        entry.get("pool", ""), entry.get("vdev", ""), [cmd], dry_run=dry
    )
    ok, out = run_check(cmd, dry_run=dry)
    safety.end_op(rb_op_id, ok, out, "" if ok else out, 0 if ok else 1, dry_run=dry)
    print(f"{'✓' if ok else '✗'} rollback {'complete' if ok else 'failed'}")
    if not ok:
        print(f"  {R}{out}{N}")
    return 0 if ok else 1


def _pos_int(s: str) -> int:
    """argparse type: a strictly-positive int. Rejects '-1'/'0' so a negative
    blink duration can't crash time.sleep and leak dd readers (F-073)."""
    v = int(s)
    if v <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return v


_JSON_HELP = ("machine-readable output: a single JSON envelope on stdout "
              "(schema_version/ok/data/warnings/error) and nothing else")

_CONFIRM_HELP = ("answer the confirmation prompts without asking, so a program "
                 "can drive a mutating command. `yes` approves; naming the "
                 "TARGET (pool/bay/controller) additionally requires it to match "
                 "what is about to be changed. Omit it and b2ctl prompts exactly "
                 "as it does today.")


def _add_global_flags(parser) -> None:
    """Accept the process-wide flags on every subcommand too (F-139 / ADR-007).

    Declared only on the top-level parser they work as `b2ctl --json status` but
    NOT as `b2ctl status --json`, because argparse hands everything after the verb
    to the subparser — and `b2ctl offload --confirm yes` is exactly how an
    operator types it.

    default=SUPPRESS is load-bearing. A subparser's own default would OVERWRITE
    the value the top-level flag already set, so `b2ctl --json status` would
    silently parse as json=False. With SUPPRESS the subcommand copy only sets the
    attribute when the flag is actually present, so both positions work.
    """
    for args_, kwargs_ in (
            (("--json",), {"action": "store_true", "help": _JSON_HELP}),
            (("--dry-run",), {"action": "store_true", "dest": "dry_run",
                              "help": "preview write commands without executing them"}),
            (("--confirm",), {"metavar": "yes|TARGET", "help": _CONFIRM_HELP})):
        try:
            parser.add_argument(*args_, default=argparse.SUPPRESS, **kwargs_)
        except argparse.ArgumentError:
            pass                # this subcommand already declares it itself
    for act in parser._actions:                     # recurse into `maint`, `config`
        if isinstance(act, argparse._SubParsersAction):
            for sp in act.choices.values():
                _add_global_flags(sp)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="b2ctl",
                                description="ZFS/HBA disk health & lifecycle "
                                            "(IT-mode, LSI SAS2308)")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="preview write commands without executing them")
    p.add_argument("--json", action="store_true", default=False, help=_JSON_HELP)
    p.add_argument("--confirm", metavar="yes|TARGET", default=None,
                   help=_CONFIRM_HELP)
    sub = p.add_subparsers(dest="cmd")

    st = sub.add_parser("status", help="health table + details")
    # --locate is a physical side effect; --json is machine output. Rejecting the
    # combination is honester than silently dropping the LED intent (F-069).
    st_mode = st.add_mutually_exclusive_group()
    st_mode.add_argument("--locate", action="store_true",
                         help="blink LEDs on at-risk disks for a few seconds")
    # default=SUPPRESS for the same reason as _add_json_flag: a plain default here
    # would overwrite the value `b2ctl --json status` already set (ADR-007).
    st_mode.add_argument("--json", action="store_true",
                         default=argparse.SUPPRESS, help=_JSON_HELP)
    st.add_argument("--seconds", type=_pos_int, default=locatemod.DEFAULT_SECONDS,
                    help="blink duration in seconds (default 5, must be > 0)")
    st.add_argument("--full", action="store_true",
                    help="every column at full width, no pager (for copy/paste)")
    st.add_argument("--no-pager", action="store_true",
                    help="never page, even when the output is taller than the screen")
    st.set_defaults(func=_status, emits_json=True)

    # Granular read verbs for the machine contract (ADR-007): a client polling
    # pool health should not pay for a full SMART scan the way `status` does.
    sub.add_parser("disks", help="per-disk health (SMART scan)").set_defaults(func=_disks, emits_json=True)
    sub.add_parser("pools", help="ZFS pool health (no SMART scan)").set_defaults(func=_pools, emits_json=True)
    sub.add_parser("volumes", help="hardware RAID volumes ([] in IT mode)").set_defaults(func=_volumes, emits_json=True)

    by = sub.add_parser("bays", help="inspect / fix front-panel bay labelling")
    by_act = by.add_mutually_exclusive_group()
    by_act.add_argument("--calibrate", action="store_true",
                        help="blink each bay, ask which slot lit, write the rule")
    by_act.add_argument("--set-reverse", choices=("on", "off"), dest="set_reverse",
                        help="mirror-reverse the front panel's slot numbering")
    by_act.add_argument("--set", metavar="RAW=LABEL",
                        help="explicit relabel, e.g. --set 32:0=32:7 (wins over --set-reverse)")
    by_act.add_argument("--clear", action="store_true",
                        help="drop all bay customisation (other panels untouched)")
    by.add_argument("--slots", type=_pos_int,
                    help="bay count for --set-reverse (omit to auto-detect from "
                         "the drives present)")
    by.set_defaults(func=_bays, emits_json=True)

    # Long operations (scrub/trim/rebuild/health-check) run for hours. A machine
    # caller starts them and polls this instead of holding a request open.
    sub.add_parser("progress",
                   help="what is running now (scrub / trim / rebuild / health-check)"
                   ).set_defaults(func=_progress, emits_json=True)

    w = sub.add_parser("watch", help="interactive hotplug-aware loop")
    w.set_defaults(func=_watch)

    lo = sub.add_parser("locate",
                        help="blink ONE disk's LED (perccli / ledctl, else dd)")
    lo.add_argument("target", help="bay label (1:4), serial, sdX, or /dev/sdX")
    lo.add_argument("seconds", nargs="?", type=_pos_int,
                    default=locatemod.DEFAULT_SECONDS,
                    help="blink duration in seconds (default 5, must be > 0)")
    lo.set_defaults(func=_locate)

    off = sub.add_parser("offload", help="safely detach or resilver a disk to offload it")
    off.add_argument("--disk", metavar="TARGET", help="disk to act on: bay / serial / dev / by-id. Omit it and b2ctl shows the picker as it does today.")
    off.set_defaults(func=_offload)

    re_cmd = sub.add_parser("replace", help="simulate-fail and replace onto spare")
    re_cmd.add_argument("--disk", metavar="TARGET", help="disk to act on: bay / serial / dev / by-id. Omit it and b2ctl shows the picker as it does today.")
    re_cmd.set_defaults(func=_replace)

    cr = sub.add_parser("create", help="create a new zfs pool")
    cr.add_argument("--raid10", action="store_true",
                    help="stripe of mirrors (RAID10) from an even number of disks")
    cr.add_argument("--disks", metavar="A,B,C",
                    help="comma-separated members: bay / serial / dev / by-id "
                         "(omit for the interactive picker)")
    cr.add_argument("--type", metavar="LEVEL",
                    help="mirror / raidz1 / raidz2 / raid10 / single")
    cr.add_argument("--name", metavar="POOL", help="pool name")
    cr.set_defaults(func=_create)

    ds = sub.add_parser("destroy", help="destroy a zfs pool (DESTRUCTIVE) + disable its maintenance timers")
    ds.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    ds.set_defaults(func=_destroy)

    sw = sub.add_parser("swap", help="swap wearing disk onto spare")
    sw.add_argument("--disk", metavar="TARGET", help="disk to act on: bay / serial / dev / by-id. Omit it and b2ctl shows the picker as it does today.")
    sw.set_defaults(func=_swap)

    de = sub.add_parser("demote", help="demote mirror leg to spare")
    de.add_argument("--disk", metavar="TARGET", help="disk to act on: bay / serial / dev / by-id. Omit it and b2ctl shows the picker as it does today.")
    de.set_defaults(func=_demote)

    # manual maintenance: scrub / trim (per-pool) + history view
    scr = sub.add_parser("scrub", help="start a manual scrub on a pool")
    scr.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    scr.set_defaults(func=_scrub)

    trm = sub.add_parser("trim", help="start a manual TRIM on a pool")
    trm.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    trm.set_defaults(func=_trim)

    # `maint` is the single maintenance surface (v0.18.0): history view (no sub /
    # --log) plus scrub / trim / health subcommands. Top-level `scrub`/`trim` above
    # remain as back-compat aliases.
    mnt = sub.add_parser("maint",
                         help="maintenance: scrub / trim / health-check + history")
    mnt.add_argument("--log", action="store_true",
                     help="show the maintenance history log (maint.jsonl)")
    mnt.add_argument("--last", type=int, default=30, metavar="N",
                     help="show last N events (default 30)")
    mnt.set_defaults(func=_maint, maint_cmd=None, emits_json=True)
    msub = mnt.add_subparsers(dest="maint_cmd")

    m_scr = msub.add_parser("scrub", help="start a manual scrub on a pool")
    m_scr.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    m_scr.set_defaults(func=_scrub, emits_json=False)

    m_trm = msub.add_parser("trim", help="start a manual TRIM on a pool")
    m_trm.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    m_trm.set_defaults(func=_trim, emits_json=False)

    m_hl = msub.add_parser("health",
                           help="health-check disk(s): SMART long self-test (+ scan) + verdict")
    m_hl.add_argument("target", nargs="*",
                      help="bay / serial / dev of disk(s) (space-separated)")
    m_hl.add_argument("--scan", action="store_true",
                      help="also run a full read-surface scan (badblocks, read-only)")
    m_hl.add_argument("--short", action="store_true",
                      help="short self-test instead of long")
    m_hl.add_argument("--status", action="store_true",
                      help="show live status of in-flight health-checks (re-attach)")
    m_hl.add_argument("--cancel", nargs="+", metavar="TARGET",
                      help="cancel in-flight health-check on the given bay/serial/dev")
    m_hl.add_argument("--cancel-all", action="store_true",
                      help="cancel ALL in-flight health-checks")
    m_hl.set_defaults(func=_burnin, emits_json=False)

    # aux vdevs: L2ARC cache + SLOG log
    ca = sub.add_parser("cache-add", help="add L2ARC read-cache device(s) to a pool")
    ca.add_argument("pool")
    ca.add_argument("devs", nargs="+", help="bay/serial/dev of cache device(s)")
    ca.add_argument("--size", help="over-provision: partition each device to this "
                                   "size (e.g. 512G) and add the -part1; default full disk")
    ca.set_defaults(func=_cache_add)

    crm = sub.add_parser("cache-rm", help="remove an L2ARC cache device from a pool")
    crm.add_argument("pool")
    crm.add_argument("dev", help="cache leaf token / bay / serial / dev")
    crm.set_defaults(func=_cache_rm)

    la = sub.add_parser("log-add",
                        help="add a SLOG (2 devs default to a mirror; --mirror/--raid10 to force)")
    la.add_argument("pool")
    la.add_argument("devs", nargs="+", help="bay/serial/dev of log device(s)")
    la.add_argument("--size", help="over-provision: partition each device to this "
                                   "size (e.g. 32G) and add the -part1; default full disk")
    la_topo = la.add_mutually_exclusive_group()
    la_topo.add_argument("--mirror", action="store_true",
                         help="mirrored SLOG (redundant)")
    la_topo.add_argument("--raid10", action="store_true",
                         help="stripe-of-mirrors SLOG (even # of disks >= 4)")
    la.set_defaults(func=_log_add)

    lrm = sub.add_parser("log-rm", help="remove a SLOG device from a pool")
    lrm.add_argument("pool")
    lrm.add_argument("dev", help="log leaf token / bay / serial / dev")
    lrm.set_defaults(func=_log_rm)

    crp = sub.add_parser("cache-replace",
                         help="repair a degraded L2ARC cache device (remove old + add new)")
    crp.add_argument("pool")
    crp.add_argument("old", help="degraded cache leaf token / bay / serial / dev")
    crp.add_argument("new", help="replacement disk: bay / serial / dev / by-id")
    crp.set_defaults(func=_cache_replace)

    lrp = sub.add_parser("log-replace",
                         help="repair a degraded SLOG log device (replace, or remove+add)")
    lrp.add_argument("pool")
    lrp.add_argument("old", help="degraded log leaf token / bay / serial / dev")
    lrp.add_argument("new", help="replacement disk: bay / serial / dev / by-id")
    lrp.set_defaults(func=_log_replace)

    # NOTE: `burnin` merged into `maint health` (v0.18.0) — see the `maint` parser.

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=_version, emits_json=True)

    # check
    chk = sub.add_parser("check", help="verify tools and environment on this server")
    chk.set_defaults(func=_check, emits_json=True)

    # config
    cfg_p = sub.add_parser("config", help="manage /etc/b2ctl/config.json")
    cfg_sub = cfg_p.add_subparsers(dest="config_cmd")
    cfg_show = cfg_sub.add_parser("show", help="print current config")
    cfg_show.set_defaults(func=_config_show, emits_json=True)
    cfg_init = cfg_sub.add_parser("init", help="write default config to /etc/b2ctl/config.json")
    cfg_init.set_defaults(func=_config_init)
    cfg_p.set_defaults(func=lambda a: (print(f"{Y}  usage: b2ctl config show|init{N}") or 0))

    log_p = sub.add_parser("log", help="show operation history")
    log_p.add_argument("--last", type=int, default=20,
                       metavar="N", help="show last N entries (default 20)")
    log_p.set_defaults(func=lambda a: _log_cmd(a), emits_json=True)

    rb_p = sub.add_parser("rollback", help="reverse a logged operation")
    rb_p.add_argument("op_id", help="op_id from b2ctl log output")
    rb_p.set_defaults(func=lambda a: _rollback_cmd(a.op_id))

    # install
    inst_p = sub.add_parser("install",
                            help="report tool status; with flags, download+install "
                                 "(sas2ircu/perccli)")
    inst_grp = inst_p.add_mutually_exclusive_group()
    inst_grp.add_argument("--with-tools", dest="with_tools", action="store_true",
                          help="download + install both tools (sas2ircu + perccli)")
    inst_grp.add_argument("--perc", action="store_true",
                          help="install perccli + set controller.mode=raid")
    inst_grp.add_argument("--flash", action="store_true",
                          help="install sas2ircu + set controller.mode=it")
    inst_grp.add_argument("--tool", choices=["sas2ircu", "perccli"],
                          metavar="TOOL", help="install only this tool")
    inst_p.set_defaults(func=_install)

    # update
    upd_p = sub.add_parser(
        "update",
        help="validate config + sync bay_map/ssd_spec to /etc/b2ctl (as root)")
    upd_p.add_argument("--force", action="store_true",
                       help="overwrite operator-customized files (keeps a .bak)")
    upd_p.add_argument("--export-bay-map", action="store_true",
                       help="(deprecated) alias of --force; plain update now syncs both files")
    upd_p.set_defaults(func=_update)

    # RAID-mode (PERC) actions
    rr_p = sub.add_parser("raid-replace",
                          help="guided replace+rebuild of a hardware RAID member")
    rr_p.add_argument("target", nargs="?", help="bay/serial of the member (prompts if omitted)")
    rr_p.set_defaults(func=_raid_replace)

    ro_p = sub.add_parser("raid-offline",
                          help="mark a hardware RAID member offline+missing (prep to pull)")
    ro_p.add_argument("target", help="bay/serial of the member")
    ro_p.set_defaults(func=_raid_offline)

    rc_p = sub.add_parser("raid-create", help="create a hardware RAID virtual disk (DESTRUCTIVE)")
    rc_p.add_argument("--level", required=True, help="raid level, e.g. raid1/raid5")
    rc_p.add_argument("--drives", required=True, help="comma list of enc:slot, e.g. 32:0,32:1")
    rc_p.set_defaults(func=_raid_create)

    rd_p = sub.add_parser("raid-del", help="delete a hardware RAID virtual disk (DESTRUCTIVE)")
    rd_p.add_argument("vd", type=int, help="virtual disk number, e.g. 0")
    rd_p.set_defaults(func=_raid_del)

    rf_p = sub.add_parser("raid-foreign",
                          help="show / import / clear a PERC foreign configuration "
                               "(a foreign drive is refused JBOD, hot-spare and "
                               "volume-create until it is resolved)")
    rf_act = rf_p.add_mutually_exclusive_group()
    rf_act.add_argument("--import", dest="do_import", action="store_true",
                        help="import the foreign config — CONTROLLER-WIDE")
    rf_act.add_argument("--clear", action="store_true",
                        help="discard the foreign config — CONTROLLER-WIDE, DESTRUCTIVE")
    rf_p.add_argument("-c", "--controller", type=int, default=None,
                      help="controller index (default 0)")
    rf_p.set_defaults(func=_raid_foreign, emits_json=True)

    _add_global_flags(p)        # after every subparser exists (ADR-007)
    return p


# disks/pools/volumes/bays are NOT listed: they probe real hardware (smartctl,
# zpool, sas2ircu/perccli), exactly like `status`, so they need root for the same
# reason. Being machine-readable does not make a probe cheaper (ADR-007).
# F-146: `rollback` is NOT here — it runs a stored write command (`zpool`/
# `wipefs`/`sgdisk`/…), so it was the one exempt verb that actually MUTATES.
_ROOT_EXEMPT = ("version", "check", "config", "log", "rollback",
                "install", "update", "maint", "raid-foreign")


def _needs_root(args) -> bool:
    """Read-only commands run without root. `maint` is special: the bare history
    view (no subcommand / --log) and `maint health --status` are read-only; the
    mutating subcommands (scrub / trim / health <dev>) need root.
    `raid-foreign` is the same shape: bare = `perccli /cN/fall show` (read-only),
    --import/--clear mutate the controller."""
    cmd = getattr(args, "cmd", None)
    if cmd not in _ROOT_EXEMPT:
        return True
    if cmd == "raid-foreign":
        return bool(getattr(args, "do_import", False)
                    or getattr(args, "clear", False))
    if cmd != "maint":
        return False                          # other exempt cmds never need root
    mc = getattr(args, "maint_cmd", None)
    if mc is None:                            # `maint` / `maint --log` = history view
        return False
    if mc == "health" and getattr(args, "status", False):
        return False                          # re-attach view only
    return True                               # maint scrub|trim|health <dev> mutate


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dry_run", False):
        from . import watch as _watch
        _watch._DRY_RUN = True
        common.set_dry_run(True)      # bottom-layer owner read by raid_actions/burnin
    want_json = bool(getattr(args, "json", False))
    if not getattr(args, "cmd", None):
        # Bare `b2ctl` defaults to status. Re-parsing REPLACES args, so carry the
        # flag over by hand or `b2ctl --json` would silently print a table.
        args = parser.parse_args(["status"])
        args.json = want_json
    # Set BEFORE any handler runs: read-path modules route notices through
    # common.warn(), which must already know to collect instead of print, or the
    # first warning lands on stdout and corrupts the envelope (ADR-007).
    common.set_json_mode(want_json)
    common.set_auto_confirm(getattr(args, "confirm", None))
    if _needs_root(args):
        if want_json and os.geteuid() != 0:
            # need_root() dies to stderr with a bare exit code — useless to a
            # machine caller, which must get the same envelope as any other
            # failure so it can branch on error.code (ADR-007).
            from . import jsonout
            return jsonout.fail(getattr(args, "cmd", "?"), jsonout.ERR_NEEDS_ROOT,
                                "run as root (smartctl / sas2ircu / zpool need it)")
        need_root()
    import contextlib
    import io
    # F-146: common.die() is a SystemExit, not an Exception — under --json its
    # message went straight to the REAL stderr and the process exited with
    # NOTHING on stdout for a machine caller to parse (e.g. get_backend() dying
    # because no HBA/RAID tool is on the box). Capture stderr ONLY under --json
    # so the die() text can be folded into the envelope's message instead of
    # lost, and so a caller reading only stdout still sees total silence on the
    # error path too. The human face is untouched: without --json this context
    # manager is a no-op and stderr prints live, exactly as before.
    stderr_buf = io.StringIO()
    stderr_ctx = contextlib.redirect_stderr(stderr_buf) if want_json else contextlib.nullcontext()
    try:
        with stderr_ctx:
            # A read verb builds its own envelope (emits_json). Everything else
            # is a mutation whose narration has to be captured instead of printed.
            if want_json and not getattr(args, "emits_json", False):
                cmd = getattr(args, "cmd", None)
                if cmd == "watch":
                    from . import jsonout
                    return jsonout.fail("watch", jsonout.ERR_UNSUPPORTED,
                                        "watch is an interactive terminal loop and "
                                        "has no machine-readable form")
                if cmd == "maint" and getattr(args, "maint_cmd", None) == "health":
                    from . import jsonout
                    if getattr(args, "status", False):
                        # Pure read: build the envelope straight from the
                        # PURE-READ status_payload() and return. Never reach
                        # burnin.live_view()/status_view() here — those redraw
                        # in a `while True` loop and would hang the request
                        # forever (F-146; this is the one verb `_needs_root`
                        # advertises as safe to poll).
                        from . import burnin
                        return jsonout.emit("maint",
                                            {"health": burnin.status_payload()})
                    # Starting a NEW health-check is machine-callable: burn-in has
                    # been non-blocking by design since ADR-002 (it exits 0 once
                    # the tests are STARTED), and burnin._unwatched() now skips
                    # the live view under --json instead of hanging the request
                    # for the hours a long self-test takes (F-146). So it falls
                    # through to _json_mutation like any other mutation, and the
                    # caller polls `maint health --status --json`.
                return _json_mutation(args)
            return args.func(args)
    except common.NonInteractive as exc:
        # --confirm was given but a prompt still had no answer. Name it, so the
        # caller learns WHICH argument to supply instead of the command hanging
        # or b2ctl guessing on a destructive path (ADR-007 phase 2).
        msg = (f"{exc.prompt} — this command still needs that answer; "
               f"supply {exc.hint}" if exc.hint else
               f"{exc.prompt} — this command cannot run non-interactively yet")
        if want_json:
            from . import jsonout
            return jsonout.fail(getattr(args, "cmd", "?"),
                                jsonout.ERR_INVALID_ARG, msg)
        print(f"{R}[-] {msg}{N}", file=sys.stderr)
        return 1
    except zfs.ZfsUnavailable as exc:
        # zpool did not answer. Every verb that reports or mutates pools reaches
        # here rather than presenting an empty machine as fact (F-143). Both faces
        # say the same thing: rc 1, and a machine caller gets a code it can branch
        # on instead of `pools: []` with ok:true.
        msg = (f"zpool did not answer ({exc}) — the pool picture is unknown, "
               f"so this command cannot report or change it")
        if want_json:
            from . import jsonout
            return jsonout.fail(getattr(args, "cmd", "?"),
                                jsonout.ERR_TOOL_MISSING, msg)
        print(f"{R}[-] {msg}{N}", file=sys.stderr)
        return 1
    except SystemExit as exc:
        # F-146: the ONLY production call site reaching here unguarded is
        # backend.get_backend() -> common.die() when neither sas2ircu nor
        # perccli can be used (core.scan()/scan_light() call it unconditionally,
        # before any --json branch gets a chance to degrade gracefully). Without
        # --json, re-raise so a human sees exactly what they see today (die()
        # already printed to the real stderr, and the process exits with die()'s
        # own code) — this handler must never change that path.
        if not want_json:
            raise
        from . import jsonout
        # die() colours its line for a terminal; a machine caller must not get
        # raw ANSI inside a JSON string. common.warn() strips the same way for
        # warnings[] — error.message gets the same treatment (F-146).
        stderr_text = common.strip_ansi(stderr_buf.getvalue()).strip()
        code = (jsonout.ERR_NO_BACKEND
                if "no hba/raid tool" in stderr_text.lower()
                else jsonout.ERR_TOOL_MISSING)
        msg = stderr_text or (
            f"b2ctl exited before completing (code {exc.code}) — a required "
            f"tool could not be used; run `b2ctl check` for details")
        return jsonout.fail(getattr(args, "cmd", "?"), code, msg)
    except KeyboardInterrupt:
        # F-022: Ctrl-C at any prompt exits cleanly, not with a traceback.
        # F-146: under --json, ANSI text on the real stdout/stderr is useless to
        # a machine caller (and stdout must carry exactly one envelope) — report
        # it as a normal failed-mutation envelope instead. The human path keeps
        # 130, the conventional SIGINT exit code operators/scripts may rely on;
        # ADR-007's 0/1 contract is for --json only.
        if want_json:
            from . import jsonout
            return jsonout.fail(getattr(args, "cmd", "?"), jsonout.ERR_OP_FAILED,
                                "interrupted (SIGINT) before the command completed")
        print(f"\n{Y}[-] interrupted{N}")
        return 130
    except Exception as exc:
        # F-146: an unhandled bug used to crash a --json caller with a bare
        # traceback on stdout instead of an envelope. A human keeps today's full
        # traceback (re-raise) — this is strictly the machine face's safety net.
        if not want_json:
            raise
        from . import jsonout
        return jsonout.fail(getattr(args, "cmd", "?"), jsonout.ERR_PARSE_ERROR,
                            str(exc) or exc.__class__.__name__)


if __name__ == "__main__":
    sys.exit(main())
