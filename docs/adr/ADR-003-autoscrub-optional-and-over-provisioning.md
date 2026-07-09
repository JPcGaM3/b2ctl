# ADR-003 — Autoscrub opt-in (default OFF), over-provisioning, and a maintenance history

- **Status:** Accepted
- **Date:** 2026-07-08
- **Version:** v0.17.0-itmode
- **Relates to:** ADR-001 (layering / read-path purity), ADR-002 (state-file redirect),
  `prompts/FEATURE_slog-overprovision-maintenance.md`, and the v0.16.0 systemd-timer
  change it partly reverses (`prompts/FEATURE_systemd-timers.md`).

## Context

v0.16.0 made the per-pool **scrub timer always-on**: `create` unconditionally
enabled `zfs-scrub-monthly@<pool>.timer` because scrub is the only checksum
verify + self-heal, and losing scheduled scrubs silently (the v0.15.0 bug) was
the failure we were guarding against.

The operator then asked for the opposite default. On these boxes **manual
scrub/trim is the primary maintenance path** — scrubs are run deliberately (and
watched), not left to a monthly timer whose window can collide with production
load. The same request bundled two more gaps:

1. **SLOG add had no topology choice** — `zfs.add_log` silently auto-mirrored on
   ≥2 devices; the operator wants to pick single / mirror / raid10, like
   pool-create.
2. **b2ctl always handed WHOLE disks to ZFS** — a PLP SLOG / L2ARC SSD benefits
   from **over-provisioning** (give ZFS e.g. 32 GiB of a large SSD, leave the
   rest as spare area for wear-leveling). That needs partition creation, which
   the repo had never done.
3. **No maintenance history + no visibility** — a scrub/trim/health-check left no
   durable record, and the status tables showed neither a pool's last scrub/trim
   nor a disk's last self-test result.

This ADR records the three architectural decisions in that bundle. The purely
mechanical parts (prompts, CLI verbs, column wiring) are in the blueprint; only
the decisions with a lasting tradeoff are recorded here.

## Decision

### 1. Autoscrub is opt-in, default OFF — reversing v0.16.0's always-on scrub

`create` now asks an explicit **autoscrub** question that defaults to **off**;
`zfs.install_pool_timers` gained `include_scrub=` (parallel to `include_trim=`)
and enables `zfs-scrub-monthly@<pool>.timer` only when the operator says yes.
autotrim keeps its own default-off. The default is seeded from a sticky
`config.pool_defaults()` (`{"autoscrub": False}`) — there is no in-code
`AUTOSCRUB_DEFAULT` constant; the config record is the single source.

**This is a deliberate safety tradeoff.** A pool with no scheduled scrub
accumulates **undetected bitrot** — silent on-disk corruption that a scrub would
find and (on a redundant vdev) self-heal. We accept that risk because:

- **Manual scrub is now the primary self-heal path**, promoted to a first-class
  action: watch `[m]aint` and `b2ctl scrub <pool>` / `b2ctl trim <pool>`.
- **Visibility replaces the silent timer.** The pools table shows each pool's
  **last scrub** (read live from `zpool status`) and **last trim**; the per-disk
  table shows a **HEALTH_CHK** column. An operator who never scrubs now *sees* a
  stale "last scrub" instead of trusting an invisible schedule.
- On `create` with autoscrub off, b2ctl prints an explicit
  `[!] autoscrub OFF — no monthly self-heal scheduled … run 'b2ctl scrub <pool>'`
  warning so the choice is never silent.

`install_pool_timers` returns `ok=True` when scrub was **not** requested (there is
nothing to fail on), unlike v0.16.0 where a missing scrub timer was the failure.

### 2. Over-provisioning — b2ctl's first partition-creation, whole-disk stays default

New `zfs.partition(dev, size, *, type_code="bf01", max_bytes=None, dry_run=False)`
runs `sgdisk -n 1:0:+<size> -t 1:bf01 <dev>` followed by a **mandatory**
`udevadm settle`, and returns the first-partition token (`zfs._part1_path`: by-id
→ `-part1`, nvme/mmcblk/loop → `p1`, else `1`). The settle is not optional: the
`-part1` by-id symlink appears asynchronously, so without it the caller's
`zpool add … -part1` races and fails. `zfs.parse_size` turns `32G`/`512M`/`1.5T`
into bytes for validation. This is the **first time b2ctl creates a partition** —
every prior ZFS action handed over whole disks.

**Whole-disk remains the default; a partition is created ONLY when a size is
entered.** A blank size keeps the idiomatic ZFS `wholedisk=on` path unchanged.
Rationale: on Linux ZFS, **partition-vs-whole-disk makes no performance
difference when aligned** (sgdisk defaults to a 1 MiB start) with `ashift=12`.
Over-provisioning does not buy throughput; it buys **SSD endurance and
sustained-write consistency** by leaving spare area, at the cost of usable
capacity. That is a per-device judgement (mainly for a PLP SLOG / L2ARC), so it
is opt-in, never a forced reserve. The prompts are: create ("size to use per
disk"), extend cache/log ("size to use per device"), and CLI `cache-add --size`
/ `log-add --size`. On create the partition step runs **after** the dirty-disk
wipe (whose `sgdisk --zap-all` would otherwise destroy the partition) and
**before** `create_pool`.

### 3. Maintenance history (`maint.jsonl`) + a passive self-test-log HEALTH source

A new single-responsibility module `b2ctl.maint` owns an **append-only
`maint.jsonl`** under `safety.LOG_DIR`. The path is resolved at call time so the
sim harness's `safety.LOG_DIR` monkeypatch and the unit-test redirect both catch
it for free — the same pattern as burnin.json (ADR-002). It copies safety's
open-append idiom (not its op/rollback schema). Records are
`{ts, kind, target, status, detail}` with ISO-8601 local timestamps; `kind` ∈
`scrub`/`trim`/`health`; `target` = pool name (scrub/trim) or disk serial
(health); `status` ∈ `started`/`ok`/`fail`. `maint.rel_time()` renders an ISO or
epoch stamp as `"2m ago"` / `"3d ago"`.

**Two independent HEALTH sources, two time bases — deliberately not conflated:**

- The per-disk **HEALTH_CHK column** is **passive and POH-relative**. `smart.read`
  parses the drive's own **self-test LOG** out of the `smartctl -a` output it
  already fetches (zero extra subprocess) into `Disk.selftest_last_result` /
  `Disk.selftest_last_poh`. `ui._health_chk_cell` renders `OK`/`ERR` plus the age
  in **power-on hours** (e.g. `OK 120hPOH`) — because the drive logs the test
  against its lifetime hours, not a wall-clock date. This column reflects the last
  long test *whoever* fired it (burn-in, `[m]` health-check, or a manual
  `smartctl`), and it never reads `maint.jsonl`.
- The wall-clock **started/ok/fail records** live only in `maint.jsonl` and are
  shown by `b2ctl maint --log`.

A POH delta must **never** be passed through `rel_time` as if it were wall-clock,
and a wall-clock timestamp must never be shown as `hPOH` — the two are kept in
separate render paths.

**Read-path purity (CLAUDE.md §9) is preserved.** The pools "last scrub" column
is a **pure live read** of `zfs.last_scrub_date` (which parses the `scan: scrub
repaired … on <ctime>` line — ZFS keeps only the *latest*, so history must be
stored separately). Writing a completion record into `maint.jsonl` for a
background/timer scrub happens ONLY in mutating contexts — `watch`'s refresh
(`_reconcile_scrub_history`, which already mutates at startup via
`prune_orphan_timers`). `status`/`top` never write.

### ZFS constraints honored (not "fixed")

- **A log vdev cannot be raidz.** A valid SLOG is single / mirror /
  stripe-of-mirrors only. `add_log(raid_type="raidz1"|…|"raid5")` returns
  `(False, "raidz is invalid for a SLOG vdev …")` — no command runs, and the UI
  never offers raidz for a SLOG.
- **An L2ARC cache cannot be mirrored or raidz** — it is always a set of
  independent devices. `add_cache` is unchanged and never prompts for a topology.
- **Trim has no live last-run date** even on real ZFS, and `zpool status -t` trim
  annotation is OpenZFS-version-dependent — so the last-trim column reads only
  from `maint.jsonl` and the trim poller stays coarse.

## Consequences

- **Default behavior flips from v0.16.0.** A freshly created pool has **no
  scheduled scrub** unless the operator opts in. Existing pools are unaffected
  (their timers were enabled at their own create time). The bitrot risk is
  mitigated, not eliminated, by manual scrub + HEALTH visibility.
- **b2ctl now writes partition tables** (`sgdisk`, already in
  `safety.WRITE_CMDS` → dry-run gated) and depends on `udevadm settle` after
  every partition. A partition is created only on an explicit size.
- **New durable state file** `maint.jsonl` beside `ops.jsonl` / `burnin.json`;
  sim/test redirected the same way. Best-effort — a write failure never aborts
  the maintenance action itself (the kernel op already ran).
- **New config sections** `pools` (per-pool `{autotrim, autoscrub}`, written on
  create, removed on destroy) and sticky `pool_defaults` that pre-fill the create
  prompts; `set_mode`'s persistence was refactored into shared
  `_load_for_write` / `_atomic_write` helpers.
- Version bumped to **0.17.0-itmode**; unit suite green (650 passed, 8 subtests);
  sim e2e exercises create-with-size, `log-add --raid10 --size`, `scrub`/`trim`,
  and `maint --log`.
