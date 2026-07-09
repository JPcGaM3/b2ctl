"""b2ctl.ui — table + details output, styled after the reference script.

A wide fixed-column table (one row per disk) followed by detail blocks: pool
summary, disks needing config (unassigned), and disks needing attention with
their reasons. Colour follows the level (green/cyan/yellow/red).
"""

from __future__ import annotations

from .common import Disk, R, Y, G, C, N, LEVEL_COLOR

# Total rule width of the per-disk table. Bumped from 184 -> 196 when the
# HEALTH_CHK column (12 wide) was added (v0.17.0); keep the rules/sub-headers in
# lockstep with the header/row format strings below.
TABLE_W = 196


def fmt_poh(poh) -> str:
    return "N/A" if poh is None else f"{poh}h(~{poh/8766:.1f}y)"


def fmt_eta(minutes) -> str:
    """Compact remaining-time label: '~8m', '~1h10m', '' when unknown."""
    if minutes is None:
        return ""
    m = max(0, int(minutes))
    return f"~{m}m" if m < 60 else f"~{m // 60}h{m % 60:02d}m"


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
    # Free disk running a burn-in self-test -> show TEST xx% in the blank cell.
    if d.selftest_running and d.selftest_pct is not None:
        return Y + f"{('TEST ' + str(d.selftest_pct) + '%')[:10]:<10}" + N
    return f"{'':10}"


def _health_chk_cell(d: Disk) -> str:
    """12-visible-char HEALTH_CHK cell: last COMPLETED long self-test, read
    passively from the drive's self-test log (selftest_last_*), so it reflects
    the last long test whoever triggered it (burn-in / [m] health-check / a manual
    smartctl). Age is POH-relative (`hPOH` suffix), NOT wall-clock — the drive
    logs power-on hours, not a date. '-' when none recorded."""
    if not d.selftest_last_result:
        return f"{'-':<12}"
    ok = "without error" in d.selftest_last_result.lower()
    tag = "OK" if ok else "ERR"
    age = ""
    if (d.selftest_last_poh is not None and d.poh is not None
            and d.poh >= d.selftest_last_poh):
        age = f" {d.poh - d.selftest_last_poh}hPOH"
    return (G if ok else R) + f"{(tag + age)[:12]:<12}" + N


def _disk_row(d: Disk) -> str:
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
    return (
        f"{(d.bay or '-'):<8}{d.dev.replace('/dev/',''):<10}"
        f"{(d.iface or '?'):<5}{(d.model or '?')[:23]:<24}"
        f"{(d.serial or 'N/A')[:17]:<18}{fmt_poh(d.poh):<14}"
        f"{wear_used:<11}{end_left:<11}{written:<19}{d.realloc:<6}"
        f"{d.health:<9}{pool[:20]:<21}{_status_cell(d)}"
        f"{_health_chk_cell(d)}{color_level(d.level)}")


def _subhdr(label: str) -> str:
    s = f"--- {label} "
    return C + s + "-" * max(0, TABLE_W - len(s)) + N


def render_table(disks: list[Disk]) -> str:
    hdr = (f"{'BAY':<8}{'DEV':<10}{'IF':<5}{'MODEL':<24}{'SERIAL':<18}"
           f"{'POWER_ON':<14}{'WEAR(used)':<11}{'END(left)':<11}"
           f"{'WRITTEN':<19}{'BAD':<6}{'HEALTH':<9}{'POOL/ARRAY':<21}{'STATUS':<10}"
           f"{'HEALTH_CHK':<12}{'LEVEL'}")
    lines = ["=" * TABLE_W, hdr, "-" * TABLE_W]
    # Group hardware (PERC RAID volume members) above software (ZFS + free),
    # but only when both kinds are present — single-type boxes stay flat.
    hw = [d for d in disks if d.array_type == "HW"]
    sw = [d for d in disks if d.array_type != "HW"]
    if hw and sw:
        lines.append(_subhdr("Hardware (PERC RAID)"))
        lines += [_disk_row(d) for d in hw]
        lines.append(_subhdr("Software (ZFS / unassigned)"))
        lines += [_disk_row(d) for d in sw]
    else:
        lines += [_disk_row(d) for d in hw + sw]
    lines.append("=" * TABLE_W)
    return "\n".join(lines)


def render_storage(rows: list[dict]) -> str:
    """Unified storage summary — hardware rows above software rows (caller orders).
    Columns mean the same for both; HW used/free come from a mounted VD filesystem
    (else '-'), SW from `zpool list`."""
    if not rows:
        return ""
    out = ["Storage summary:",
           f"  {'TYPE':<5}{'NAME':<16}{'LEVEL':<9}{'STATE':<10}"
           f"{'SIZE':<10}{'USED':<10}{'FREE':<10}{'SCRUB':<12}{'TRIM'}"]
    for r in rows:
        st = str(r.get("state", "?"))
        low = st.lower()
        col = G if (low.startswith("optl") or low == "online") else (
            Y if low == "degraded" else R)
        # SCRUB/TRIM are per-pool (SW rows); HW VDs show '-'. Values are
        # pre-formatted display strings ('2d ago' / '') set by core.pool_maint.
        out.append(
            f"  {r['kind']:<5}{str(r['name'])[:15]:<16}{str(r['level'])[:8]:<9}"
            f"{col}{st[:9]:<10}{N}{str(r['size'])[:9]:<10}"
            f"{str(r['used'])[:9]:<10}{str(r['free'])[:9]:<10}"
            f"{(r.get('last_scrub') or '-'):<12}{r.get('last_trim') or '-'}")
    return "\n".join(out)


def render_pools(pools: list[dict]) -> str:
    if not pools:
        return ""
    lines = ["Pools:"]
    for p in pools:
        bad = p["health"] != "ONLINE"
        tag = R if bad else G
        # scrub/trim are pre-formatted display strings ('2d ago' / '') set by
        # core.pool_maint; shown only when the caller enriched the pool dict.
        maint = ""
        if "last_scrub" in p or "last_trim" in p:
            maint = (f"  scrub={(p.get('last_scrub') or '-'):<9}"
                     f"trim={p.get('last_trim') or '-'}")
        lines.append(f"  {tag}{p['name']:<10}{p['size']:<8}{p['alloc']:<8}"
                     f"free={p['free']:<8}{p['health']:<10}cap={p['cap']}{N}"
                     f"{maint}{'  <-- not ONLINE' if bad else ''}")
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
            if d.selftest_running and d.selftest_pct is not None:
                eta = f", {d.selftest_eta} remaining" if d.selftest_eta else ""
                out.append(f"    - self-test running: {d.selftest_pct}% complete{eta}")
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


# --------------------------------------------------------------------------- #
# Burn-in live progress (multi-disk: self-test + surface-scan bars + ETA)
# --------------------------------------------------------------------------- #
def _bar(pct, width: int = 14) -> str:
    if pct is None:
        return "[" + "-" * width + "]"
    filled = max(0, min(width, int(round(pct / 100.0 * width))))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _progress_cell(running: bool, pct, eta_min, done: bool, *, na: bool = False,
                   bad: int = 0) -> str:
    """One 'test' column: a bar+%+ETA while running, else a terminal label."""
    if na:
        return "n/a"
    if running:
        return f"{_bar(pct)} {(pct if pct is not None else 0):>3}%  {fmt_eta(eta_min):<7}"
    if pct is not None or done:
        return f"done ({bad} bad)" if bad else "done"
    return "-"


def render_burnin_row(row: dict) -> str:
    dev = row["dev"].replace("/dev/", "")
    st = _progress_cell(row["st_running"], row["st_pct"], row["st_eta"], row["done"])
    sc = _progress_cell(row["sc_running"], row["sc_pct"], row["sc_eta"], row["done"],
                        na=not row["do_scan"], bad=row.get("sc_bad") or 0)
    return f" {(row.get('bay') or '?'):<8}{dev:<10}{st:<30}{sc}"


def render_burnin_view(rows: list[dict]) -> str:
    """Header + one row per disk (line count = 1 + len(rows), for the redraw)."""
    hdr = f" {'BAY':<8}{'DISK':<10}{'SELF-TEST':<30}SURFACE SCAN (badblocks)"
    return "\n".join([hdr] + [render_burnin_row(r) for r in rows])
