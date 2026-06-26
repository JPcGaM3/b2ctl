"""b2ctl.ui — table + details output, styled after the reference script.

A wide fixed-column table (one row per disk) followed by detail blocks: pool
summary, disks needing config (unassigned), and disks needing attention with
their reasons. Colour follows the level (green/cyan/yellow/red).
"""

from __future__ import annotations

from .common import Disk, R, Y, G, C, N, LEVEL_COLOR


def fmt_poh(poh) -> str:
    return "N/A" if poh is None else f"{poh}h(~{poh/8766:.1f}y)"


def disk_label(d) -> str:
    """Human-readable one-line id for prompts: (bay) model (serial)."""
    return f"({d.bay or '?'}) {d.model or '?'} ({d.serial or 'N/A'})"


def human_size(num) -> str:
    if not num:
        return "-"
    v = float(num)
    for u in ("B", "K", "M", "G", "T", "P"):
        if v < 1024 or u == "P":
            return f"{v:.0f}{u}" if u == "B" else f"{v:.1f}{u}"
        v /= 1024
    return f"{v:.1f}P"


def color_level(level: str) -> str:
    return LEVEL_COLOR.get(level, G) + f"{level:<8}" + N


def _status_cell(d: Disk) -> str:
    """Return 10-visible-char STATUS cell with ANSI color."""
    if d.is_spare:
        if d.vdev_state == "AVAIL":
            return G + f"{'AVAIL':<10}" + N
        if d.vdev_state == "INUSE":
            tgt = f"→{d.spare_replacing}" if d.spare_replacing else ""
            return Y + f"{('INUSE' + tgt)[:10]:<10}" + N
        return f"{(d.vdev_state or ''):<10}"
    if d.in_pool and d.vdev_state:
        raw = f"{d.vdev_state[:10]:<10}"
        if d.vdev_state == "ONLINE":
            return G + raw + N
        if d.vdev_state == "DEGRADED":
            return Y + raw + N
        if d.vdev_state in ("FAULTED", "REMOVED", "UNAVAIL", "OFFLINE"):
            return R + raw + N
        return raw
    return f"{'':10}"


def render_table(disks: list[Disk]) -> str:
    hdr = (f"{'BAY':<6}{'DEV':<10}{'IF':<5}{'MODEL':<24}{'SERIAL':<18}"
           f"{'POWER_ON':<14}{'WEAR(used)':<11}{'END(left)':<11}"
           f"{'WRITTEN':<19}{'BAD':<6}{'HEALTH':<9}{'POOL/ARRAY':<21}{'STATUS':<10}{'LEVEL'}")
    lines = ["=" * 182, hdr, "-" * 182]
    for d in disks:
        wear_used = "N/A" if d.wear_val is None else f"{100 - d.wear_val}%"
        end_left = "N/A" if d.end_left is None else f"{d.end_left:.1f}%"
        if d.written_tb is None:
            written = "N/A"
        elif d.is_ssd:
            cap = f"/{d.tbw_rating:.0f}TBW" if d.tbw_rating else "/?"
            written = f"{d.written_tb:.2f}TB{cap}"
        else:
            written = f"{d.written_tb:.2f}TB (HDD)"
        # POOL cell encodes the array type: SW = ZFS, HW = PERC virtual disk.
        if d.pool:
            pool = f"SW:{d.pool}" + (f"/{d.vdev}" if d.vdev else "")
        elif d.array_type == "HW":
            pool = f"HW:{d.array_name}"
        else:
            pool = "-"
        lines.append(
            f"{(d.bay or '-'):<6}{d.dev.replace('/dev/',''):<10}"
            f"{(d.iface or '?'):<5}{(d.model or '?')[:23]:<24}"
            f"{(d.serial or 'N/A')[:17]:<18}{fmt_poh(d.poh):<14}"
            f"{wear_used:<11}{end_left:<11}{written:<19}{d.realloc:<6}"
            f"{d.health:<9}{pool[:20]:<21}{_status_cell(d)}{color_level(d.level)}")
    lines.append("=" * 182)
    return "\n".join(lines)


def render_pools(pools: list[dict]) -> str:
    if not pools:
        return ""
    lines = ["Pools:"]
    for p in pools:
        bad = p["health"] != "ONLINE"
        tag = R if bad else G
        lines.append(f"  {tag}{p['name']:<10}{p['size']:<8}{p['alloc']:<8}"
                     f"free={p['free']:<8}{p['health']:<10}cap={p['cap']}{N}"
                     f"{'  <-- not ONLINE' if bad else ''}")
    return "\n".join(lines)


def render_raid_volumes(vols: list[dict]) -> str:
    """Render the hardware (PERC) RAID volumes table. Empty string if none."""
    if not vols:
        return ""
    lines = ["RAID volumes (hardware):"]
    for v in vols:
        bad = not str(v.get("state", "")).lower().startswith("optl")
        tag = R if bad else G
        lines.append(
            f"  {tag}vd{v.get('vd','?'):<3} {str(v.get('raid','?')):<7}"
            f"{str(v.get('state','?')):<7} {str(v.get('size','?')):<11}"
            f"members={v.get('members','?')}{N}"
            f"{('  ' + v['name']) if v.get('name') else ''}")
    return "\n".join(lines)


def render_details(disks: list[Disk], pools: list[dict] | None = None) -> str:
    out = []
    config = [d for d in disks if d.level == "CONFIG"]
    risky = [d for d in disks if d.level in ("WARNING", "CRITICAL")]
    bad_pools = [p for p in (pools or []) if p.get("health") != "ONLINE"]

    if config:
        out.append(f"{C}===== disks needing config (unassigned) ====={N}")
        for d in config:
            out.append(f"{C}- bay {d.bay or '?'} {d.dev} "
                       f"({d.model or '?'}, SN {d.serial or 'N/A'}) [CONFIG]{N}")
            for r in d.reasons:
                out.append(f"    - {r}")

    if risky:
        out.append(f"{R}===== disks needing attention ====={N}")
        for d in risky:
            tag = R if d.level == "CRITICAL" else Y
            out.append(f"{tag}- bay {d.bay or '?'} {d.dev} "
                       f"(SN {d.serial or 'N/A'}) [{d.level}]{N}")
            for r in d.reasons:
                out.append(f"    - {r}")

    if bad_pools:
        out.append(f"{R}===== pools needing attention ====={N}")
        for p in bad_pools:
            out.append(f"{R}- pool {p['name']}: {p['health']} "
                       f"(not ONLINE — a member may be missing/resilvering){N}")

    if not config and not risky and not bad_pools:
        out.append(f"{G}[OK] all disks healthy and assigned{N}")
    elif not config and not risky and bad_pools:
        out.append(f"{Y}[!] disks readable but a pool is not ONLINE — see above{N}")
    return "\n".join(out)


def render_new_disk(d: Disk) -> str:
    """One-disk panel shown when a hot-plugged disk is detected."""
    size = human_size(d.size_bytes)
    end = "N/A" if d.end_left is None else f"{d.end_left:.1f}% left"
    wear = "N/A" if d.wear_val is None else f"{100 - d.wear_val}% used"
    return (f"{C}  device : {d.dev}  ({d.by_id or 'no by-id'}){N}\n"
            f"  model  : {d.model or '?'}   SN {d.serial or 'N/A'}\n"
            f"  bay    : {d.bay or '?'}   size {size}   {d.iface or '?'}"
            f"   {'SSD' if d.is_ssd else 'HDD'}\n"
            f"  health : {d.health}   wear {wear}   endurance {end}")
