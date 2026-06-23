# TASKS.md — b2ctl work queue

> **STATUS (v0.2.1-itmode):** HOTFIX 1, 2, 3 are **DONE** (implemented & mock-tested;
> docs updated). The live queue now starts at **FEATURE 1c**. The hotfix specs
> below are kept as the record of what changed — verify them on hardware, then
> proceed to the features.
>
> Verify on the box after redeploy:
> - `b2ctl status` → BAY shows physical numbers (OS in 0–1, data in 4–7), POOL
>   shows `pool/vdev-N` (e.g. `tank/raidz1-0`, `tank/spares`).
> - `b2ctl locate <serial>` → exactly one bay's LED/activity blinks ~5s.
> - If a bay number is still off, edit `bay_map.json` (reverse rule or explicit map).

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
