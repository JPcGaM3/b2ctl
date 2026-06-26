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

__version__ = "0.5.0-itmode"


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
    print(ui.render_pools(pools))
    vols = _backend_mod.get_backend().raid_volumes()
    if vols:
        print(ui.render_raid_volumes(vols))
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


def _locate(args) -> int:
    disks = core.scan()
    d = next((x for x in disks if args.target in
              (x.bay, x.serial, x.dev, x.dev.replace("/dev/", ""))), None)
    if d is None:
        print(f"{R}[-] could not resolve '{args.target}' to a disk{N}")
        return 1
    # HW RAID members have no per-member block device — drive the LED via perccli.
    if d.array_type == "HW":
        from . import hba_raid
        import time
        print(f"{Y}[*] locate LED ON for bay {d.bay} ({args.seconds}s)...{N}")
        ok, out = hba_raid.locate(d.bay, True)
        if ok:
            time.sleep(args.seconds)
            hba_raid.locate(d.bay, False)
        print((G + "[+] done (via perccli)" if ok else R + f"[-] failed: {out}") + N)
        return 0 if ok else 1
    print(f"{Y}[*] blinking {d.dev} for {args.seconds}s ...{N}")
    ok, method = locatemod.blink(d.dev, args.seconds)
    print((G + f"[+] done (via {method})" if ok
           else R + "[-] failed") + N)
    return 0 if ok else 1


def _offload(_args) -> int:
    watch._cmd_offload(spec.load())
    return 0


def _replace(_args) -> int:
    watch._cmd_replace(spec.load())
    return 0


def _create(_args) -> int:
    watch._cmd_create(spec.load())
    return 0


def _swap(_args) -> int:
    watch._cmd_swap(spec.load())
    return 0


def _demote(_args) -> int:
    watch._cmd_demote(spec.load())
    return 0


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
    """Download and install tool binaries from Google Drive."""
    if os.geteuid() != 0:
        print(f"{R}[-] b2ctl install requires root{N}")
        return 1
    print()
    print(f"{C}[b2ctl install]{N}")
    if getattr(args, "perc", False):
        _installer_mod.install_profile("perc")
    elif getattr(args, "flash", False):
        _installer_mod.install_profile("flash")
    else:
        tools = [args.tool] if getattr(args, "tool", None) else None
        _installer_mod.install_tools(tools)
    print()
    return 0


def _update(args) -> int:
    """Validate config and report tool/bay_map status."""
    export = getattr(args, "export_bay_map", False)
    if export and os.geteuid() != 0:
        print(f"{R}[-] b2ctl update --export-bay-map requires root{N}")
        return 1

    print(f"\n{C}[b2ctl update]{N}")
    results = _cfg_mod.validate()
    _STATUS_COLOR = {"ok": G, "warn": Y, "error": R}
    _STATUS_ICON  = {"ok": "[✔]", "warn": "[i]", "error": "[✗]"}
    for field, status, msg in results:
        color = _STATUS_COLOR.get(status, N)
        icon  = _STATUS_ICON.get(status, "[?]")
        print(f"  {color}{icon}{N} {field:<12} {msg}")

    if export:
        import shutil as _shutil
        import json as _json
        src = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bay_map.json"))
        dest = "/etc/b2ctl/bay_map.json"
        os.makedirs("/etc/b2ctl", exist_ok=True)
        _shutil.copy2(src, dest)
        cfg_path = _cfg_mod.CONFIG_PATH
        try:
            with open(cfg_path) as f:
                cfg = _json.load(f)
        except (OSError, _json.JSONDecodeError):
            cfg = _cfg_mod.load()
        cfg["bay_map_path"] = dest
        with open(cfg_path, "w") as f:
            _json.dump(cfg, f, indent=2)
        print(f"\n{G}[✔] bay_map exported to {dest}{N}")
        print(f"    config updated: bay_map_path = \"{dest}\"")
        print(f"    Edit {dest} freely — install.sh won't overwrite it.")
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

    lo = sub.add_parser("locate", help="blink ONE disk's LED (by bay/serial/dev)")
    lo.add_argument("target", help="bay label (1:4), serial, sdX, or /dev/sdX")
    lo.add_argument("seconds", nargs="?", type=int,
                    default=locatemod.DEFAULT_SECONDS,
                    help="blink duration (default 5)")
    lo.set_defaults(func=_locate)

    off = sub.add_parser("offload", help="safely detach or resilver a disk to offload it")
    off.set_defaults(func=_offload)

    re_cmd = sub.add_parser("replace", help="simulate-fail and replace onto spare")
    re_cmd.set_defaults(func=_replace)

    cr = sub.add_parser("create", help="create a new zfs pool")
    cr.set_defaults(func=_create)

    sw = sub.add_parser("swap", help="swap wearing disk onto spare")
    sw.set_defaults(func=_swap)

    de = sub.add_parser("demote", help="demote mirror leg to spare")
    de.set_defaults(func=_demote)

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
    inst_grp.add_argument("--perc", action="store_true",
                          help="install perccli + set controller.mode=raid")
    inst_grp.add_argument("--flash", action="store_true",
                          help="install sas2ircu + set controller.mode=it")
    inst_grp.add_argument("--tool", choices=["sas2ircu", "perccli"],
                          metavar="TOOL", help="install only this tool (default: all missing)")
    inst_p.set_defaults(func=_install)

    # update
    upd_p = sub.add_parser("update", help="validate config and report tool status")
    upd_p.add_argument("--export-bay-map", action="store_true",
                       help="copy bundled bay_map.json to /etc/b2ctl/ and update config")
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
