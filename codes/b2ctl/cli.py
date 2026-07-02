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
import sys

from . import core, watch, zfs, spec, locate as locatemod
from . import backend as _backend_mod, config as _cfg_mod
from . import installer as _installer_mod
from .common import need_root, run, R, Y, G, C, N
from . import ui

__version__ = "0.8.7-itmode"


def _resolve_dev(target: str, disks=None):
    """Resolve a bay label / serial / sdX / /dev path to a /dev device."""
    if target.startswith("/dev/"):
        return target
    disks = disks if disks is not None else core.scan()
    for d in disks:
        if target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", "")):
            return d.dev
    return None


def _status(args) -> int:
    tbw = spec.load()
    disks = core.scan(tbw)
    if args.json:
        print(json.dumps([vars(d) for d in disks], indent=2, default=str))
        return 0
    print(ui.render_table(disks))
    pools = zfs.list_pools()
    vols = _backend_mod.get_backend().raid_volumes()
    print(ui.render_storage(core.assemble_storage(disks, pools, vols)))
    print(ui.render_details(disks, pools))

    if args.locate:
        risky = [d for d in disks if d.level in ("WARNING", "CRITICAL")]
        if not risky:
            print(f"{G}[OK] nothing at risk — no LED lit{N}")
            return 0
        devs = [d.dev for d in risky]
        bays = ", ".join(d.bay or d.dev for d in risky)
        print(f"{Y}[!] blinking {args.seconds}s on: {bays}{N}")
        method = locatemod.blink_many(devs, args.seconds)
        print(f"{G}[+] done (via {method}){N}")
    return 0


def _watch(_args) -> int:
    return watch.run()


def _parse_pulse(s: str) -> tuple[float, float]:
    """Parse '--pulse ON:OFF' (seconds) -> (on, off). Raises ValueError if bad."""
    on_s, _, off_s = s.partition(":")
    on, off = float(on_s), float(off_s)
    if on <= 0 or off <= 0:
        raise ValueError("on and off must both be > 0")
    return on, off


def _locate(args) -> int:
    on = off = 0.0
    if getattr(args, "pulse", None):
        try:
            on, off = _parse_pulse(args.pulse)
        except ValueError:
            print(f"{R}[-] bad --pulse '{args.pulse}' (want ON:OFF, e.g. 2:2){N}")
            return 1
    disks = core.scan()
    d = next((x for x in disks if args.target in
              (x.bay, x.serial, x.dev, x.dev.replace("/dev/", ""))), None)
    if d is None:
        print(f"{R}[-] could not resolve '{args.target}' to a disk{N}")
        return 1
    where = f"bay {d.bay}" if locatemod.is_perc_pd(d) else d.dev
    rhythm = f" pulse {on:g}s/{off:g}s" if on and off else ""
    print(f"{Y}[*] blinking {where} for {args.seconds}s{rhythm} ...{N}")
    ok, method = locatemod.blink_disk(d, args.seconds, on, off)
    print((G + f"[+] done (via {method})" if ok else R + "[-] failed") + N)
    return 0 if ok else 1


def _offload(_args) -> int:
    watch._cmd_offload(spec.load())
    return 0


def _replace(_args) -> int:
    watch._cmd_replace(spec.load())
    return 0


def _create(args) -> int:
    watch._cmd_create(spec.load(),
                      raid_type="raid10" if getattr(args, "raid10", False) else None)
    return 0


def _destroy(args) -> int:
    watch._cmd_destroy(spec.load(), target=getattr(args, "pool", None))
    return 0


def _swap(_args) -> int:
    watch._cmd_swap(spec.load())
    return 0


def _demote(_args) -> int:
    watch._cmd_demote(spec.load())
    return 0


def _resolve_devs(tokens) -> list[str]:
    """Map bay/serial/dev/by-id tokens to stable by-id paths (never /dev/sdX)."""
    disks = core.scan(spec.load())
    out = []
    for t in tokens:
        match = next((d for d in disks if t in (d.bay, d.serial, d.dev,
                      d.dev.replace("/dev/", ""), d.by_id)), None)
        out.append((match.by_id or match.dev) if match else t)
    return out


def _cache_add(args) -> int:
    from . import zfs
    devs = _resolve_devs(args.devs)
    ok, out = zfs.add_cache(args.pool, devs, dry_run=watch._DRY_RUN)
    print((f"{G}[+] L2ARC cache added to {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _cache_rm(args) -> int:
    from . import zfs
    ok, out = zfs.remove_vdev(args.pool, _resolve_devs([args.dev])[0], dry_run=watch._DRY_RUN)
    print((f"{G}[+] removed from {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _log_add(args) -> int:
    from . import zfs
    devs = _resolve_devs(args.devs)
    if len(devs) == 1:
        print(f"{Y}[!] SLOG not mirrored: losing this log device can lose "
              f"in-flight sync writes.{N}")
    print(f"{Y}[!] ensure this SSD has Power-Loss Protection (PLP).{N}")
    ok, out = zfs.add_log(args.pool, devs, dry_run=watch._DRY_RUN)
    print((f"{G}[+] SLOG added to {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _log_rm(args) -> int:
    from . import zfs
    ok, out = zfs.remove_vdev(args.pool, _resolve_devs([args.dev])[0], dry_run=watch._DRY_RUN)
    print((f"{G}[+] removed from {args.pool}" if ok else f"{R}[-] {out}") + N)
    return 0 if ok else 1


def _burnin(args) -> int:
    from . import burnin
    return burnin.run(args.target, spec.load(), do_scan=args.scan,
                      kind="short" if args.short else "long",
                      dry_run=watch._DRY_RUN)


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


def _check(_args) -> int:
    """Check all required tools and show environment summary."""
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
            print(f"  {ok_mark} Controllers found: {len(set(bm.values()) or {0})} "
                  f"({len(bm)} disks in bay map)")
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
    updated (backup .bak). A file that differs from the bundled copy is treated
    as operator-customized and preserved unless force=True (then backed up)."""
    import shutil as _shutil
    import filecmp
    src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", bundled_name))
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
    except (OSError, _json.JSONDecodeError):
        cfg = _cfg_mod.load()

    print(f"\n{C}[sync {_cfg_mod.STD_DIR}]{N}")
    _SYNC_ICON = {"created": f"{G}[✔]{N}", "current": f"{G}[✔]{N}",
                  "customized-kept": f"{Y}[i]{N}"}
    for bundled_name, dest, key in _MANAGED:
        state = _sync_resource(bundled_name, dest, force)
        cfg[key] = dest  # bind to the absolute /etc path (directory-independent)
        icon = _SYNC_ICON.get(state, f"{G}[✔]{N}")
        note = "  (use --force to overwrite)" if state == "customized-kept" else ""
        print(f"  {icon} {os.path.basename(dest):<14} {state}{note}  →  {dest}")

    with open(cfg_path, "w") as f:
        _json.dump(cfg, f, indent=2)
    print(f"\n  {G}[✔]{N} config bound: bay_map_path, ssd_spec_path → {_cfg_mod.STD_DIR}")
    print(f"      Edit those files freely — install.sh won't overwrite them; "
          f"`b2ctl update` keeps your edits.")
    print()
    return 0


def _config_show(_args) -> int:
    print(_cfg_mod.as_json())
    return 0


def _config_init(_args) -> int:
    path = _cfg_mod.CONFIG_PATH
    if os.path.exists(path):
        print(f"{Y}[!] {path} already exists. Delete it first to regenerate.{N}")
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cfg = _cfg_mod.load()
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"{G}[+] Written: {path}{N}")
    print(f"    Edit tool_paths to override binary locations.")
    print(f"    Set controller.mode to 'it' or 'raid' to skip auto-detection.")
    return 0


def _log_cmd(args):
    from . import safety
    entries = safety.load_log(last=getattr(args, "last", 20))
    if not entries:
        print("No operations logged yet.")
        return
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


def _rollback_cmd(op_id: str):
    from . import safety
    entry = safety.find_entry(op_id)
    if entry is None:
        print(f"Op not found: {op_id}")
        return
    hint = entry.get("rollback_hint")
    if not hint:
        snap = entry.get("snapshot_path", "")
        print("Op not reversible.")
        if snap:
            print(f"  See snapshot: {snap}")
        return
    print(f"\nOp:       {entry.get('op')}  ({entry.get('started_at','')})")
    print(f"Disk:     bay {entry.get('disk_bay')} | {entry.get('disk_serial')}")
    print(f"Pool:     {entry.get('pool')}/{entry.get('vdev')}")
    print(f"Rollback: {hint}\n")
    ans = input("Execute rollback? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("Cancelled.")
        return
    from .common import run_check
    cmd = hint.split()
    if any(t.startswith("<") and t.endswith(">") for t in cmd):
        print("  Rollback hint contains unresolved placeholders — resolve manually:")
        print(f"     {hint}")
        return
    rb_op_id = safety.begin_op(
        f"rollback-{entry.get('op')}", entry.get("disk_serial", ""),
        entry.get("disk_bay"), entry.get("dev_path", ""),
        entry.get("pool", ""), entry.get("vdev", ""), [cmd]
    )
    ok, out = run_check(cmd)
    safety.end_op(rb_op_id, ok, out, "" if ok else out, 0 if ok else 1)
    print(f"{'✓' if ok else '✗'} rollback {'complete' if ok else 'failed'}")
    if not ok:
        print(f"  {R}{out}{N}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="b2ctl",
                                description="ZFS/HBA disk health & lifecycle "
                                            "(IT-mode, LSI SAS2308)")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="preview write commands without executing them")
    sub = p.add_subparsers(dest="cmd")

    st = sub.add_parser("status", help="health table + details")
    st.add_argument("--locate", action="store_true",
                    help="blink LEDs on at-risk disks for a few seconds")
    st.add_argument("--seconds", type=int, default=locatemod.DEFAULT_SECONDS,
                    help="blink duration (default 5)")
    st.add_argument("--json", action="store_true", help="machine-readable output")
    st.set_defaults(func=_status)

    w = sub.add_parser("watch", help="interactive hotplug-aware loop")
    w.set_defaults(func=_watch)

    lo = sub.add_parser("locate",
                        help="blink ONE disk's LED (perccli / ledctl, else dd)")
    lo.add_argument("target", help="bay label (1:4), serial, sdX, or /dev/sdX")
    lo.add_argument("seconds", nargs="?", type=int,
                    default=locatemod.DEFAULT_SECONDS,
                    help="blink duration (default 5)")
    lo.add_argument("--pulse", metavar="ON:OFF",
                    help="beat the LED ON:OFF seconds (e.g. 2:2) for <seconds>")
    lo.set_defaults(func=_locate)

    off = sub.add_parser("offload", help="safely detach or resilver a disk to offload it")
    off.set_defaults(func=_offload)

    re_cmd = sub.add_parser("replace", help="simulate-fail and replace onto spare")
    re_cmd.set_defaults(func=_replace)

    cr = sub.add_parser("create", help="create a new zfs pool")
    cr.add_argument("--raid10", action="store_true",
                    help="stripe of mirrors (RAID10) from an even number of disks")
    cr.set_defaults(func=_create)

    ds = sub.add_parser("destroy", help="destroy a zfs pool (DESTRUCTIVE) + remove its cron")
    ds.add_argument("pool", nargs="?", help="pool name (prompts if omitted)")
    ds.set_defaults(func=_destroy)

    sw = sub.add_parser("swap", help="swap wearing disk onto spare")
    sw.set_defaults(func=_swap)

    de = sub.add_parser("demote", help="demote mirror leg to spare")
    de.set_defaults(func=_demote)

    # aux vdevs: L2ARC cache + SLOG log
    ca = sub.add_parser("cache-add", help="add L2ARC read-cache device(s) to a pool")
    ca.add_argument("pool")
    ca.add_argument("devs", nargs="+", help="bay/serial/dev of cache device(s)")
    ca.set_defaults(func=_cache_add)

    crm = sub.add_parser("cache-rm", help="remove an L2ARC cache device from a pool")
    crm.add_argument("pool")
    crm.add_argument("dev", help="cache leaf token / bay / serial / dev")
    crm.set_defaults(func=_cache_rm)

    la = sub.add_parser("log-add", help="add a SLOG (2 devs = mirrored log)")
    la.add_argument("pool")
    la.add_argument("devs", nargs="+", help="bay/serial/dev of log device(s)")
    la.set_defaults(func=_log_add)

    lrm = sub.add_parser("log-rm", help="remove a SLOG device from a pool")
    lrm.add_argument("pool")
    lrm.add_argument("dev", help="log leaf token / bay / serial / dev")
    lrm.set_defaults(func=_log_rm)

    bi = sub.add_parser("burnin", help="vet a spare/new disk (SMART long self-test)")
    bi.add_argument("target", help="bay / serial / dev of the disk to burn in")
    bi.add_argument("--scan", action="store_true",
                    help="also run a full read-surface scan (badblocks, read-only)")
    bi.add_argument("--short", action="store_true",
                    help="short self-test instead of long")
    bi.set_defaults(func=_burnin)

    v = sub.add_parser("version", help="print version")
    v.set_defaults(func=lambda _a: (print(f"b2ctl {__version__}") or 0))

    # check
    chk = sub.add_parser("check", help="verify tools and environment on this server")
    chk.set_defaults(func=_check)

    # config
    cfg_p = sub.add_parser("config", help="manage /etc/b2ctl/config.json")
    cfg_sub = cfg_p.add_subparsers(dest="config_cmd")
    cfg_show = cfg_sub.add_parser("show", help="print current config")
    cfg_show.set_defaults(func=_config_show)
    cfg_init = cfg_sub.add_parser("init", help="write default config to /etc/b2ctl/config.json")
    cfg_init.set_defaults(func=_config_init)
    cfg_p.set_defaults(func=lambda a: (print(f"{Y}  usage: b2ctl config show|init{N}") or 0))

    log_p = sub.add_parser("log", help="show operation history")
    log_p.add_argument("--last", type=int, default=20,
                       metavar="N", help="show last N entries (default 20)")
    log_p.set_defaults(func=lambda a: _log_cmd(a))

    rb_p = sub.add_parser("rollback", help="reverse a logged operation")
    rb_p.add_argument("op_id", help="op_id from b2ctl log output")
    rb_p.set_defaults(func=lambda a: _rollback_cmd(a.op_id))

    # install
    inst_p = sub.add_parser("install",
                            help="download and install tool binaries (sas2ircu/perccli)")
    inst_grp = inst_p.add_mutually_exclusive_group()
    inst_grp.add_argument("--with-tools", dest="with_tools", action="store_true",
                          help="download + install both tools (sas2ircu + perccli)")
    inst_grp.add_argument("--perc", action="store_true",
                          help="install perccli + set controller.mode=raid")
    inst_grp.add_argument("--flash", action="store_true",
                          help="install sas2ircu + set controller.mode=it")
    inst_grp.add_argument("--tool", choices=["sas2ircu", "perccli"],
                          metavar="TOOL", help="install only this tool (default: all missing)")
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

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "dry_run", False):
        from . import watch as _watch
        _watch._DRY_RUN = True
    if not getattr(args, "cmd", None):
        args = parser.parse_args(["status"])
    if args.cmd not in ("version", "check", "config", "log", "rollback", "install", "update"):
        need_root()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
