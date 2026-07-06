# TASKS.md — b2ctl work queue

> **STATUS (v0.9.0-itmode):** Fable5 audit (`reviews/REVIEW_FABLE_001.md`, 123
> findings) **DONE** — all P0–P4 resolved, the 4 Appendix-B refuted findings left
> untouched. Blueprint: `prompts/FIX_fable5_audit.md`; architecture: `docs/adr/
> ADR-001`. Full unit suite green; sim validated IT + RAID. Version now lives in
> `codes/b2ctl/_version.py`. **Next:** verify on hardware (both nodes), then
> resume the FEATURE queue below.

## FABLE5 AUDIT — 123 code-review findings [DONE, v0.9.0-itmode]

Fixed every finding in `reviews/REVIEW_FABLE_001.md` (P0×9, P1×17, P2×39, P3×54,
P4×4). Highlights: append-only audit log + named rollback hints (safety), public
`zfs_actions`/`blockdev`/`_version` modules, `Disk.ctrl` multi-controller PERC
actions, `scan_light`/targeted `scan_one`, `Disk.is_poolable`, shared
`baymap.assign_bays`, spec/smart/spares parsing fixes, and a RAID-capable sim
(perccli VD/PD/rebuild, megaraid SMART, time-based resilver, replacing-N/offline
state). Docs (user-guide en/th locate syntax, module map, ADR-001) updated.
See `prompts/FIX_fable5_audit.md` for the per-finding blueprint and test plan.

---

> **STATUS (v0.2.1-itmode):** HOTFIX 1, 2, 3 are **DONE**; code-review hotfix (10 findings) **IN PROGRESS**.
> HOTFIX 1, 2, 3 original notes: (implemented & mock-tested;
> docs updated). The live queue now starts at **FEATURE 1c**. The hotfix specs
> below are kept as the record of what changed — verify them on hardware, then
> proceed to the features.
>
> Verify on the box after redeploy:
> - `b2ctl status` → BAY shows physical numbers (OS in 0–1, data in 4–7), POOL
>   shows `pool/vdev-N` (e.g. `tank/raidz1-0`, `tank/spares`).
> - `b2ctl locate <serial>` → exactly one bay's LED/activity blinks ~5s.
> - If a bay number is still off, edit `bay_map.json` (reverse rule or explicit map).

## FEATURE — ledctl locate backend + dd fallback [DONE, v0.8.7-itmode]

Raw-disk locate now prefers the backplane's dedicated locate LED via `ledctl`
(SGPIO/SES), falling back to the `dd` activity read. Chain = perccli → ledctl → dd
by applicability; a PERC VD member stays **perccli-only** (no /dev fallback — would
light the whole VD = wrong bay).
- locate.py: `_ledctl` (`ledctl locate=/locate_off=`), `_have_ledctl`; `blink`
  prefers ledctl, always `locate_off` in a `finally`, else `_dd_read`. LED-only.
- config.py: `ledctl` in tool_paths + soft `validate()` row (else dd fallback).
- cli.py: locate help notes perccli/ledctl/dd.
- +5 tests; docs (user-guide en/th, devops §3.7 + module map, CLAUDE §5).

## REMOVED — locate LED pulse [v0.8.8-itmode]

The v0.8.6 `--pulse` feature (LED on/off rhythm) was **removed** at the operator's
request — locate is back to a single steady blink for `seconds`. Dropped
`locate._pulse`, the `on`/`off` params on `blink`/`blink_disk`, `cli._parse_pulse`
+ `--pulse`, and the watch pulse/duration prompts (revert `_cmd_locate` to steady).
Version → 0.8.8-itmode; −8 tests (286 pass). (ledctl/perccli/dd routing unchanged.)

## FIX — bay_map/ssd_spec directory-independence + `b2ctl update` sync [DONE, v0.8.5-itmode]

Symptom (real box): NVMe bays mapped only when `b2ctl` ran from the source
checkout dir; raw BDF elsewhere. Root cause: `python -m b2ctl` prepends cwd to
`sys.path`, so the checkout's `b2ctl/` package shadowed the installed `/opt/b2ctl`
(the two copies ship different `bay_map.json`).
- install.sh launcher: `PYTHONSAFEPATH=1` → cwd not on `sys.path`; installed copy
  always wins (code path).
- config.py: `_resource_path` = override > `/etc/b2ctl/<file>` > bundled; added
  `ssd_spec_path()` (+ `ssd_spec_path` config key); `validate()` reports both files.
- spec.py: `load()` resolves via `config.ssd_spec_path()`.
- cli.py: plain `b2ctl update` (root) syncs `bay_map.json`+`ssd_spec.json` into
  `/etc/b2ctl/` via `filecmp` (created/current/customized-kept), binds both paths
  in config; `--force` overwrites customized files (keeps `.bak`);
  `--export-bay-map` = deprecated alias.
- +13 tests (279 pass); docs (user-guide en/th, devops §7.3 + troubleshooting).

## FIX — install-command parity [DONE, v0.8.3-itmode]

`./install.sh` and `b2ctl install` now share one contract (no-flag = b2ctl only /
`--with-tools` = both tools / `--perc`/`--flash` = profile+mode):
- install.sh: dropped the eager `libc6-i386` apt step → plain `./install.sh` does
  no apt/download (deps install only via `install_tools` when a tool is added).
- installer.py: `install_base()` (no-download status report). cli.py: added
  `--with-tools`, base dispatch, root checked only in acting branches.
- +5 tests (254 pass); docs (user-guide en/th, devops §7.2, CLAUDE §3) updated.

## FEATURE — NVMe bay_map by by-id/serial + sim NVMe + watch RAID-volumes [DONE, v0.8.1-0.8.2]

- baymap.remap_nvme matches NVMe by by-id/serial/bdf (precedence by-id>serial>bdf);
  `_by_id_index` prefers nvme-<model>_<serial> over nvme-eui.
- watch `_cmd_refresh` renders the hardware RAID volumes table (parity with status).
- sim: +2 NVMe disks, NVMe-format smartctl, serial-keyed PCIe bay in sim/bay_map.json.

## FEATURE — ZFS aux vdevs (SLOG/L2ARC) + RAID10 + burn-in [DONE, v0.8.0-itmode]

Spec: `prompts/FEATURE_aux-vdevs-burnin.md`. For the R740XD central ZFS/NFS
storage runbook (STEP 02–03). Shipped, mock + sim verified (241 tests pass):

- **L2ARC cache**: `zfs.add_cache` / `remove_vdev`; CLI `cache-add`/`cache-rm`;
  watch `[e]xtend` → 1. Unguarded (cache loss = harmless).
- **SLOG log**: `zfs.add_log` (≥2 devs → mirrored); CLI `log-add`/`log-rm`;
  watch `[e]xtend` → 2. Warns on single (non-mirrored) log + always reminds PLP.
- **RAID10**: `create_pool(raid_type="raid10")` emits repeated `mirror` pairs;
  CLI `create --raid10`; watch `[n]ew-pool` raid10 (even count, shows pairs).
- **Burn-in**: `burnin.py` (`start_selftest`/`selftest_status`/`read_scan`/
  `assess`); CLI `burnin <bay|dev> [--scan] [--short]`; watch `[b]urnin`.
  Read-only (self-test + read-only `badblocks`, never `-w`).
- All honor `--dry-run`. Sim updated (`zpool` cache/log/raid10/`destroy`,
  `smartctl -t`, fake `badblocks`); fixed a pre-existing sim `create` bug (didn't
  skip `-o k=v` property values). Docs: user-guide en/th, devops-guide,
  test-checklist (215→241). Version → **v0.8.0-itmode**.

## FEATURE — RAID-mode support (Dell PERC) + dual-backend [DONE, validate on R640]

Spec: `prompts/FEATURE_raid_mode.md`. Bumped to **v0.6.0-itmode**.

b2ctl is now dual-backend: IT/HBA (sas2ircu, ZFS) **and** RAID (perccli, hardware
RAID). storcli removed everywhere. Shipped:

- perccli tool-pick (non-zero controller count), member enumeration from
  `/cN/vall show all`, SMART via `-d megaraid,<DID>`, HW members as Disk rows.
- `POOL/ARRAY` column `HW:`/`SW:` prefix + hardware RAID-volumes table.
- Install profiles: `b2ctl install --perc` (perccli+mode=raid) / `--flash`
  (sas2ircu+mode=it); `config.set_mode()`; installer reconciled to `cp -f
  /usr/sbin` (matches install.sh). `--perc`/`--flash` in install.sh too.
- RAID actions (guarded + audited): `b2ctl locate` (perccli for HW members),
  `raid-replace` (guided offline→missing→LED→rebuild w/ live progress),
  `raid-offline`, `raid-create`, `raid-del`.

**Validate on the R640** (mutating perccli ops + rebuild-progress parser are
untested on CI): `b2ctl check` → perccli, ≥1 controller; `b2ctl status` → 2
members (32:0/32:1, SMART via megaraid, `HW:vd0/raid1`), VD in volumes table,
NVMe direct, no false GHOST; `b2ctl locate 32:0 on`; then the raid-* flows.
Also capture `perccli /c0/eall/sall show all` with a JBOD disk to finalise the
JBOD tag path.

## CODE-REVIEW HOTFIX — 10 findings from branch code review [DONE]

Spec: `prompts/FEATURE_code_review_fixes.md`

| # | File | Severity | Finding |
|---|------|----------|---------|
| 1 | watch.py:270 | CRITICAL | wipe() missing dry_run=_DRY_RUN — real wipe in dry-run mode |
| 2 | zfs.py:267 | HIGH | resilver errors treated as success (has_errors never checked by caller) |
| 3 | watch.py:168+ | HIGH | end_op cancel/fail paths missing dry_run=_DRY_RUN |
| 4 | spec.py:49 | HIGH | reverse match `m in k` removed — TBW lookup fails for short model strings |
| 5 | watch.py:396 | MED | _replace_onto_spare missing -f flag |
| 6 | cli.py:289 | MED | rollback hint placeholders exec'd as cmd args |
| 7 | watch.py:166 | MED | begin_op before _confirm_op — audit entry on cancelled op |
| 8 | zfs.py:196 | MED | spares_replacing() misses spare-N containers (hot spare auto-activate) |
| 9 | config.py:125 | LOW | subprocess imported inline, bypasses run_check() convention |
| 10 | watch.py | CLEANUP | resilver loop triplicated — extract _wait_resilver() helper |

---

Read `CLAUDE.md` first for full project context, conventions, and safety rules.
Do the three **HOTFIXes** first (small, independent, fix observed bugs on real
hardware), then the **FEATUREs** in the order 1c → 1b → 1a. Every deliverable
follows the house rules: stdlib only, English code, confirm + by-id on mutating
actions, and update the two docs (reader + DevOps) when behaviour changes; bump
the version in `cli.py`.

Observed real output that motivates the hotfixes:
```
BAY   DEV   ...  POOL          LEVEL
1:0   sde        tank/spares   NORMAL      <- bay label reversed; vdev index lost
1:1   sdd        tank/raidz1   NORMAL      <- should be tank/raidz1-0
...
1:7   sdf        rpool/mirror  NORMAL      <- should be rpool/mirror-0
```

---

## HOTFIX 1 — bay number is reversed vs the physical chassis

**Problem.** `sas2ircu DISPLAY` reports enclosure:slot that is mirror-reversed
from the physical bay labels on this backplane: displayed slot 0 is physically
bay 7, displayed 7 is physically 0 (and so on across the 8-bay backplane).

**Where.** `hba.py` → `attach_bays()` / `bay_map()`. The bay is **display-only**
once HOTFIX 2 lands (LED no longer uses the slot), so this is a presentation
remap — apply it where `d.bay` is set.

**Fix.** Add a configurable remap, applied after reading sas2ircu, default =
identity (so installs where it's already correct don't change):

1. New optional config file `bay_map.json` next to `ssd_spec.json`, loaded by a
   new `spec`-style loader (e.g. `hba._load_bay_map()`). Support **either**:
   - explicit table (most reliable on odd backplanes):
     ```json
     { "map": { "1:0": "1:7", "1:1": "1:6", "1:2": "1:5", "1:3": "1:4",
                "1:4": "1:3", "1:5": "1:2", "1:6": "1:1", "1:7": "1:0" } }
     ```
   - or a rule (this backplane is a clean reversal, so this is enough):
     ```json
     { "reverse_slots": true, "slots_per_enclosure": 8 }
     ```
     → physical_slot = (slots_per_enclosure - 1) - reported_slot, per enclosure.
2. `attach_bays()` sets `d.bay` to the **remapped** label. Keep the raw reported
   value only if any sas2ircu call still needs it (after HOTFIX 2, nothing does).

**Calibrate.** Once HOTFIX 2 works, run `b2ctl locate <serial-or-dev>`, watch
which physical bay blinks, and confirm the map. For this server the reversal
rule above is correct; ship `bay_map.json` with it as the example.

**Test.** Unit-test the remap: feed a fake sas2ircu DISPLAY + a reverse rule,
assert `1:0→1:7`, `1:3→1:4`, etc. Identity when no config present.

**Done.** Table shows physical bay numbers; DevOps doc gains a "bay remap /
calibration" section.

---

## HOTFIX 2 — locate LED: stop using sas2ircu slot LOCATE; blink one disk ~5 s

**Problem.** `sas2ircu <c> LOCATE <enc:slot> ON` lights a whole range of bays
(0–5), not the single requested bay — unreliable on this backplane.

**Where.** New module `locate.py`; rewire `hba.locate()` callers (`cli.py`
`locate`/`--locate`, `watch.py` `_cmd_locate`, and the future replace workflow).

**Fix.** Drive the LED **by device, not by slot**, with a backend chain:

1. **`dd` activity-LED blink — universal fallback (no SES/SGPIO needed).**
   READ ONLY: `timeout <secs> dd if=/dev/sdX of=/dev/null bs=1M iflag=direct`.
   The bay's activity LED flickers heavily for the duration; nothing to turn off.
   **Never write** — `if=` the device, `of=/dev/null`. This matches the user's
   "blink ~5 s then stop" requirement exactly.

Default duration **5 seconds**, auto-off. Interface:
`b2ctl locate <bay|serial|/dev/sdX> [seconds]` — resolve any of those to the
device via `core.scan()`/`hba`, then act on the device. Drop slot-based locate
as the default (optionally keep `--sas2ircu` as a last-resort flag, off by
default).

**Test.** Mock absent → assert the `dd if=/dev/sdX of=/dev/null` (read-only,
timeout) fallback. Assert duration default 5 s and that a write `dd` is never
constructed.

**Done.** Single-disk blink works on this backplane; DevOps doc updated with the
dd chain and the read-only safety note; reader guide locate section
updated.

---

## HOTFIX 3 — POOL column drops the vdev index

**Problem.** Table shows `tank/raidz1`, `rpool/mirror` instead of
`tank/raidz1-0`, `rpool/mirror-0`. `tank/spares` is fine.

**Where.** `ui.py` → `render_table()`. Current code strips the index:
```python
if d.vdev:
    pool = f"{pool}/{d.vdev.split('-')[0]}"   # BUG: drops "-0"/"-1"
...
f"{d.health:<9}{pool[:13]:<14}{color_level(d.level)}"
```

**Fix.** Use the full vdev id and widen the column so `rpool/mirror-0`
(14 chars) fits:
```python
pool = d.pool or "-"
if d.vdev:
    pool = f"{pool}/{d.vdev}"
...
f"{d.health:<9}{pool[:16]:<17}{color_level(d.level)}"
```
Update the header `{'POOL':<17}` and bump the two rule lines (`=`/`-`) by +3
to keep them aligned.

**Test.** Render a disk with `vdev='raidz1-0'` → cell contains `tank/raidz1-0`;
`vdev='mirror-0'` → `rpool/mirror-0`; spare `vdev='spares'` → `tank/spares`.

**Done.** Column shows `pool/vdev` with index; widths aligned.

---

## FEATURE 1c — simulate-fail → replace-onto-spare, with live progress (do first)

Full spec in `CLAUDE.md` §7 Task B. Summary: pick an in-pool disk to "fail" →
`zpool offline` → `zpool replace <pool> <offlined> <spare>` → **live resilver
progress bar (% done) + ZFS ETA countdown** (poll `zpool status`, parse
`(\d+\.\d+)%\s*done` and `(?:(\d+)\s*days?\s*)?(\d{2}:\d{2}:\d{2})\s*to go`,
handle the "resilvered … with 0 errors" completion line) → on done
`zpool detach` the old disk and **blink the bay to pull (HOTFIX 2 locate)** →
detect the new inserted disk → offer `zpool add <pool> spare <new by-id>`.

Note for this hardware: `tank` is **raidz1** now, so resilver reads all members
and is slower than a mirror; the pool tolerates only one failure during the
window. On a near-empty pool resilver is near-instant — write ~50–100 GB of test
data first to actually see the bar. Confirm every mutating step ([y/N], by-id).

## FEATURE 1b — demote an active mirror member to spare (guarded)

Full spec in `CLAUDE.md` §7 Task C. `zpool detach` a mirror leg then
`zpool add … spare`. **Refuse or hard-double-confirm** if detaching would leave
a vdev with no redundancy. Applies to mirror vdevs (rpool); raidz vdevs have no
detachable legs, so the action must be rejected cleanly for `tank/raidz1-0`.

## FEATURE 1a — move/convert disks between pools / RAID layouts (design + safe subset)

Full spec in `CLAUDE.md` §7 Task D. mirror/RAID10: `add mirror` / `detach` /
`remove <vdev>` with redundancy checks. **raidz cannot convert in place** —
implement only the correct path (create new pool + `zfs send | zfs recv` +
destroy old) as a documented, confirmed procedure; never imply an in-place raidz
conversion exists. Write an ADR before the destructive parts.

## OPTIONAL — `b2ctl top`

**Status:** Deferred.

Full spec in `CLAUDE.md` §7 Task E. Read-only curses auto-refresh monitor; keep
all interactive actions in `watch`.
