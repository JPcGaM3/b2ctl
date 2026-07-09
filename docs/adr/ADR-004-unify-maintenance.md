# ADR-004 — Unify maintenance: burn-in folded into `maint`, manual-only TRIM, shared self-test classifier

- **Status:** Accepted
- **Date:** 2026-07-09
- **Version:** v0.18.0-itmode
- **Relates to:** ADR-003 (autoscrub opt-in / over-provisioning — this ADR extends
  its "manual maintenance is primary" line to TRIM and to disk vetting), ADR-002
  (background burn-in state file, reused unchanged),
  `prompts/FEATURE_unify-maintenance-v0.18.md`.

## Context

v0.18.0 is a **real-hardware fix + consolidation** release: every change came from
R740xd operator feedback after v0.17.0 ran on the box. Two of the items are
correctness bugs that only surfaced on real SAS/used disks; the rest tighten the
maintenance surface that ADR-003 introduced.

1. **Healthy SAS disks graded ERR / FAIL.** ADR-003 added the HEALTH_CHK column and
   the burn-in verdict; both decided "did the self-test pass?" with a bare
   `"without error" in result` check. ATA reports success as
   `Completed without error`, but **SAS reports it as bare `Completed`** — so every
   healthy SAS disk showed `ERR` in HEALTH_CHK and `FAIL` in burn-in. The SAS
   self-test-log row parser also captured greedily, swallowing the trailing
   `-  41724  -` into the status token.
2. **Over-provisioning failed on used disks.** ADR-003's `zfs.partition` ran
   `sgdisk -n 1:0:+<size>` on the raw device. That places partition 1 at the first
   free aligned sector, so a **stale GPT** from prior use pushed it past the old
   partition — `Could not create partition 1 from <big sector>`, and on a small
   drive, past end-of-disk.
3. **Two overlapping maintenance surfaces.** `[m]aint`'s health-check and the
   standalone `[b]urnin` command **both ran `smartctl -t long`** on a disk. The
   operator asked for one maintenance surface, not two verbs that do the same thing.
4. **Asymmetric TRIM default.** ADR-003 made autoscrub opt-in (OFF ⇒ no timer,
   manual scrub is primary) but left autotrim OFF ⇒ a monthly `zfs-trim` timer. The
   operator wanted the same rule for both: OFF means **manual-only**.

## Decision

### 1. One shared self-test pass/fail classifier

New `common.selftest_passed(result: str) -> bool`, the single authority both
`ui._health_chk_cell` (HEALTH_CHK column) and `burnin.assess` (burn-in verdict) now
call. Logic, dialect-tolerant:

- `"without error"` in the string → **pass** (ATA success — the word "error"
  appears yet it is a pass).
- any of `fail` / `abort` / `interrupt` / `fatal` / `unknown` / `unable` → **fail**.
- otherwise `"completed"` in the string → **pass** (**SAS success = bare
  `Completed`**).
- an empty string is **not** a pass — callers treat `''` as "no test on record"
  *before* calling.

`burnin._sas_selftest_result` was also fixed: it drops the `# N` index, splits the
row on **2+-space columns**, and returns the clean status token (`Completed` /
`Aborted (by user command)`) instead of the greedy old capture. Confirmed against
the box:

```
# 1  Background long   Completed                   -   41724                 - [-   -    -]
```

The three self-test-**log** dialects that feed the passive HEALTH_CHK column live in
`smart._parse_selftest_log` (unchanged since v0.17.0): ATA/SAS `#`-indexed rows and
the NVMe `Self-test Log (NVMe Log 0x06)` block. **NVMe was already handled** — the
bug was only in the pass/fail verdict, never in NVMe support.

### 2. Wipe before partitioning (over-provision)

Over-provisioning now **wipes each disk before `zfs.partition`**, reusing the
existing `zfs.wipe` (= `zpool labelclear` + `wipefs -a` + `sgdisk --zap-all`). A
clean GPT means `sgdisk -n 1:0:+<size> -t 1:bf01` always places partition 1 at the
first aligned sector with no stale partition to collide with.

- `watch._maybe_partition` validates the sizes, prints a §9 WIPE warning naming
  each disk, asks **one** confirm, then wipes → partitions per disk, aborting on any
  wipe/partition failure.
- `cli._partition_devs` prints a WIPE warning, wipes each resolved by-id device,
  then partitions.
- In `watch._cmd_create`, when a size is given the over-provision path **replaces**
  the old whole-disk dirty-wipe block, so there is no double wipe/confirm. A blank
  size keeps the whole-disk path (and its own dirty-disk wipe prompt) unchanged.

### 3. Burn-in folded into `maint`; standalone command dropped

There is now **one maintenance surface**. Burn-in and health-check were the same
`smartctl -t long` engine, so they are merged:

- **Watch:** the `[b]urnin` key is **removed** from the menu. `[m]aint` now offers
  `[1] scrub  [2] trim  [3] health-check`, where **health-check IS the full former
  burn-in engine** — multi-select long self-test + optional read-only `badblocks`
  surface scan + PASS/WARN/FAIL verdict, non-blocking, live view, Ctrl-C-detach,
  re-attach. The watch function is `watch._maint_health` (renamed from
  `_cmd_burnin`); the old self-test-only `_maint_health_check` was deleted.
- **CLI:** the `b2ctl burnin` verb is **removed**. The `maint` verb is the surface:
  - `b2ctl maint scrub [<pool>]`   — mutates (root)
  - `b2ctl maint trim  [<pool>]`   — mutates (root)
  - `b2ctl maint health <dev…> [--scan] [--short] [--status] [--cancel …] [--cancel-all]`
    — mutates (root); `--status` re-attach is exempt
  - `b2ctl maint` / `b2ctl maint --log [--last N]` — read-only history (exempt)
- **Back-compat:** top-level `b2ctl scrub <pool>` and `b2ctl trim <pool>` are **kept
  as aliases** of `maint scrub` / `maint trim`.
- **Root gating** is `cli._needs_root(args)`: bare `maint` / `maint --log` and
  `maint health --status` are exempt; `maint scrub|trim|health <dev>` need root.
- **health-check vets FREE/SPARE disks only — on BOTH paths.** The watch selection
  lists free disks (`_avail_for_aux`); the CLI `maint health <dev…>` also refuses an
  in-pool member — both flow through `burnin.run_multi` → `burnin._poolable_target`,
  which prints `maint health vets free/spare disks; <dev> is in pool '<pool>' —
  self-test it with \`smartctl -t long\` directly`. To self-test an *active pool
  member*, the operator runs `smartctl -t long <by-id>` in a shell (b2ctl does not
  trigger it); the HEALTH_CHK column still renders passively for members from
  `smartctl -a`, it just isn't re-triggered from inside b2ctl.
- Starting a health-check records a `"health"` / `"started"` event to `maint.jsonl`
  (on both the watch and CLI paths; **skipped under `--dry-run`**).

### 4. Manual-only TRIM — the monthly trim timer is dropped

Create now calls `zfs.install_pool_timers(name, include_scrub=autoscrub_on,
include_trim=False)` **always**. `autotrim OFF` therefore means **manual-only** (no
timer), symmetric with `autoscrub OFF`; `autotrim ON` means inline `zpool
autotrim=on`. This **reverses** the v0.16.0 / v0.17.0 rule "autotrim off → monthly
`zfs-trim-monthly@<pool>.timer`". The `include_trim=` parameter still exists on
`install_pool_timers` for API completeness, but create never requests it. On create
with autotrim off you now see:

```
[!] autotrim OFF — TRIM manually via `b2ctl maint trim <pool>`
```

The create prompts were also re-worded and **re-ordered so autotrim and autoscrub
are both `[1] off` (default) / `[2] on`** for consistency:

```
autotrim:  [1] off — manual TRIM via [m]aint / `b2ctl maint trim` (recommended)
           [2] on  — zpool autotrim=on (ZFS trims inline)
autoscrub: [1] off — manual scrub via [m]aint / `b2ctl maint scrub` (recommended)
           [2] on  — monthly zfs-scrub timer (self-heals silent bitrot)
```

## Consequences

- **Migration — commands renamed.** `b2ctl burnin <dev…>` → `b2ctl maint health
  <dev…>`; `b2ctl burnin --status/--cancel/--cancel-all` → `b2ctl maint health
  --status/--cancel/--cancel-all`. The watch **`[b]` key is gone** — use `[m]aint →
  [3] health-check`. `b2ctl scrub`/`b2ctl trim` still work (aliases).
- **TRIM scheduling flips again.** A freshly created pool with autotrim off has **no
  trim timer** — the operator TRIMs manually (`b2ctl maint trim`, or the pool's
  `autotrim=on` trims inline). Existing pools keep whatever timers they were created
  with; `watch` still prunes orphan timers at startup.
- **Over-provisioning is now safe on used disks** but is **more destructive up
  front**: the disk is wiped before it is partitioned, behind an explicit
  §9 WIPE confirm. A blank size is unchanged (whole disk).
- **Healthy SAS disks read correctly** — HEALTH_CHK shows `OK …hPOH` and burn-in
  returns PASS. No behavior change for ATA/NVMe (they already matched `without
  error`).
- **No new external tools or state files.** `zfs.wipe`/`zfs.partition`/`smartctl
  -t long`/`badblocks -sv`/`maint.jsonl`/`burnin.json` are all unchanged from
  v0.17.0; only the call sites and the classifier moved.
- Version bumped to **0.18.0-itmode**; unit suite green (**677 passed, 14
  subtests**); e2e-verified on the R740xd (incl. a real X357 SAS regression:
  `smart.read`→`assess` yields `uncorr=0` / PASS despite `Non-medium error count:
  1061`).
