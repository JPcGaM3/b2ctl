# b2ctl (IT-mode) — DevOps Guide

Operational reference for the IT-mode/HBA build: every external command it
runs, how each output is parsed, the scan pipeline, the safety model, and the
deltas from the RAID-mode build. Audience: whoever maintains or debugs b2ctl on
the R620s.

> 📌 Note: See also: [`walkthrough.md`](walkthrough.md) — step-by-step
> "press X → see Y" walkthrough with real server outputs;
> [`test-checklist.md`](test-checklist.md) — pass/fail test report.

---

## Table of Contents

1. [Environment assumptions](#1-environment-assumptions)
2. [Module map](#2-module-map)
3. [Every subprocess, and how it is parsed](#3-every-subprocess-and-how-it-is-parsed)
4. [The scan pipeline](#4-the-scan-pipeline-corescan)
5. [The watch loop](#5-the-watch-loop-watchrun)
6. [Safety model](#6-safety-model)
7. [Deployment](#7-deployment)
8. [Troubleshooting](#8-troubleshooting)
9. [Backend detection](#9-backend-detection-backendpy)
10. [Config file](#10-config-file-configpy)
11. [Deltas from the RAID-mode build](#11-deltas-from-the-raid-mode-build-adr-001)
12. [Simulation harness](#12-simulation-harness-codessim)

---

## 1. Environment assumptions

- PERC H710 mini crossflashed to **IT mode** → LSI SAS9207-8i (SAS2308),
  firmware `0x2214` IT. No RAID controller CLI; disks are raw `/dev/sd*`.
- Proxmox VE on ZFS-on-root (`rpool` mirror) + a data pool (`tank`, RAID10 =
  striped mirrors). Pools created with `/dev/disk/by-id/ata-*` members,
  `ashift=12`, `compression=lz4`, `atime=off`, `xattr=sa`.
- Python **stdlib only** (no pip deps). Runs as root.
- Required binaries: `smartctl`, `zpool`, `lsblk`. Optional: `sas2ircu`
  (bay numbers only), `ledmon`/`ledctl` (nicer locate LEDs), `wipefs`/`sgdisk`
  (wipe action). LED locate works without any of them via the dd fallback.

There is intentionally **no storcli/perccli and no `smartctl -d megaraid`** —
those only work behind a RAID controller and are removed in this build.

---

## 2. Module map

| module | responsibility | external commands |
|--------|-----------------|-------------------|
| `common.py` | colours, `run()`/`run_check()`, `Disk` model, `assess()` | none |
| `spec.py` | load/lookup TBW ratings (`ssd_spec.json`) | none |
| `hba.py` | enumerate disks, by-id index, bay map + **remap** | `lsblk`, `sas2ircu` |
| `locate.py` | device-based LED locate (ledctl -> dd), timed | `ledctl`, `dd` |
| `smart.py` | direct SMART read + parse, endurance | `smartctl` |
| `zfs.py` | pool/topology parse, membership, actions | `zpool`, `wipefs`, `sgdisk` |
| `core.py` | the `scan()` pipeline | (composes the above) |
| `ui.py` | table / pools / details / new-disk rendering | none |
| `watch.py` | interactive select()-loop, event + command handlers | `lsblk` (poll) |
| `cli.py` | argparse, subcommand dispatch, `--locate` blink | — |
| `safety.py` | audit trail, pre-op snapshots, rollback hints, post-op verify | `zpool`, `smartctl` (snapshot only) |

`run()` is list-form `subprocess.run` (no shell), 30 s timeout, returns stdout
or `''`. `run_check()` is for mutating actions: returns `(ok, stdout+stderr)`.
Extended signature: `run_check(args, timeout=120, *, op_id=None, dry_run=False)`.
When `dry_run=True` and `args[0]` is in `safety.WRITE_CMDS`, prints
`[DRY-RUN] would run: ...` and returns `(True, "")` without executing.
Read commands still run in dry-run mode.

---

## 3. Every subprocess, and how it is parsed

### 3.1 Disk enumeration — `hba.enumerate_disks()`
```
lsblk -dnb -P -o NAME,SIZE,SERIAL,MODEL,TRAN,ROTA,TYPE
```
- `-P` emits `KEY="value"` pairs; parsed with `(\w+)="(.*?)"`. **This is
  deliberate** — positional parsing breaks because MODEL contains spaces
  (e.g. `Samsung SSD 870 EVO 1TB`).
- Keep rows where `TYPE=disk`; drop names starting with
  `loop/sr/ram/zd/dm-/md`.
- `ROTA=0` ⇒ SSD. `SIZE` is bytes. `TRAN` ⇒ iface (SATA/SAS).

### 3.2 Stable names — `hba._by_id_index()`
Walks `/dev/disk/by-id`, `realpath`s each link, and keeps the
highest-priority link per real device: `ata-` > `scsi-SATA` > `wwn-` >
`scsi-`. Skips `*-part*`. This `by_id` is what every `zpool` action uses, so a
disk keeps a stable name across reboots/reslots.

### 3.3 Physical bay — `hba.attach_bays()` / `bay_map()`
```
sas2ircu 0 DISPLAY
```
Parsed line-by-line, tracking `Enclosure #`, `Slot #`, `Serial No`; produces
`serial -> "enc:slot"`. Matched to disks by **serial**, then **remapped** to the
physical chassis label via `bay_map.json` (`hba._remap()`).

**Why remap:** Dell 12G backplanes (R620/R720) on LSI IT firmware report
scrambled slot numbers — the Dell slot-translation map is absent in LSI
firmware (a known issue; confirmed by the H710 IT-mode flash author). `bay_map.json`
takes either an explicit `{"map": {"1:0":"1:7", ...}}` (raw->physical) or a rule
`{"reverse_slots": true, "slots_per_enclosure": 8}`. No file -> identity. The bay
is **display-only** (LEDs key off the device, not the slot), so a wrong map is
cosmetic, never dangerous. Calibrate with `b2ctl locate <serial>`.

If `sas2ircu list` returns nothing the step is skipped and `bay` stays `None`.

### 3.4 SMART — `smart.read()`
```
smartctl -a /dev/sdX          # tried first (auto device type)
smartctl -a -d sat /dev/sdX   # fallback
smartctl -a -d scsi /dev/sdX  # fallback
```
- Health: `test result:\s*(\w+)` (ATA) or `SMART Health Status:` (SAS, `OK`→
  `PASSED`).
- SSD vs HDD refined from `Rotation Rate:` (`Solid State` vs `N rpm`).
- **ATA attributes** (`ATTRIBUTE_NAME` table present): id=col0, normalised
  VALUE=col3, RAW=col9.
  - wear: first of ids `[177,233,202,231,173,169,232]` → `wear_val`
  - `9`→POH, `241`→LBAs written, `5`→reallocated, `197`→pending,
    `187/188/198`→uncorrectable (max).
- **SAS** (no attribute table): `Percentage used endurance indicator` →
  `wear_val = 100 - used`; power-on hours; `write:` log line field 6 (GB) →
  LBAs; `Elements in grown defect list` → reallocated.
- Endurance: `written_tb = lba_written * 512 / 1e12`;
  `tbw_rating` from `spec.lookup(model)`;
  `end_left = clamp((rating - written)/rating * 100, 0, 100)`. HDDs force
  `wear_val=None`.

### 3.5 ZFS topology — `zfs.topology()`
```
zpool list -H -o name,size,alloc,free,health,frag,cap
zpool status -P -v <pool>      # for each pool
```
- `-P` prints full device paths (the by-id leaves). `_parse()` walks the
  `config:`→`errors:` block. A line matching the vdev regex
  (`mirror|raidz*|spare|replacing|log|cache|special|dedup`) sets the current
  vdev; a leaf line matching `<token> <STATE>` records
  `{pool, vdev, state, token}`.
- Each leaf is indexed under **both** its `-P` token and its `realpath`, so
  `attach_membership()` can match a disk by `by_id`, `dev`, or either
  realpath. That is why membership still resolves whether the pool was built
  with by-id or `/dev/sdX`.

`zfs.spares_replacing(pool)` — called from `core.scan()` for any pool that has at
least one INUSE spare. Parses `zpool status -P -v <pool>` again (one extra call per
resilvering pool, which is rare). Finds `replacing-N` vdev blocks; returns
`{spare_token: replaced_token}` where the replaced leaf has state
`REMOVED`/`FAULTED`/`UNAVAIL`/`OFFLINE`. Used by `ui._status_cell()` to show
`INUSE→bay` in the STATUS column.

### 3.6 Mutating actions — `zfs.*` (all via `run_check`)
| action | command |
|--------|---------|
| add spare | `zpool add <pool> spare <by-id>` |
| replace faulted | `zpool replace <pool> <old-token> <by-id>` |
| swap-to-spare | `zpool replace <pool> <member> <spare-token>` |
| demote-to-spare | `zpool detach <pool> <member>` → `zpool add <pool> spare <by-id>` |
| add mirror vdev | `zpool add <pool> mirror <a> <b>` |
| attach | `zpool attach <pool> <existing> <new>` |
| create pool | `zpool create ...` (checks `wipefs -n` for existing labels first) |
| wipe | `zpool labelclear -f <dev>` → `wipefs -a <dev>` → `sgdisk --zap-all <dev>` |

### 3.7 LED locate — `locate.py` (by DEVICE, never by slot)
`sas2ircu ... LOCATE <slot>` is **not used** — on this backplane it lights a
whole range of bays, and the slot numbers are scrambled anyway. Locate is keyed
to the `/dev/sdX` device, backend chain:
1. fallback: `dd if=/dev/sdX of=/dev/null bs=1M` for N seconds (READ ONLY) — the
   activity LED flickers; nothing to switch off.
Default blink is 5 s then auto-stop. `b2ctl locate <bay|serial|sdX> [secs]`
resolves any identifier to the device first. `b2ctl status --locate` blinks all
at-risk disks at once (`blink_many`). **Invariant: never construct a writing
`dd` (`of=` is always `/dev/null`).**

---

## 4. The scan pipeline (`core.scan()`)

```
enumerate_disks (lsblk -P)
  → attach_bays (sas2ircu DISPLAY, by serial; remapped via bay_map.json)
  → smart.read per disk (smartctl direct)
  → attach_membership (zpool status -P, by by-id/dev/realpath)
  → spares_replacing (zpool status -P -v, per pool with INUSE spares — sets Disk.spare_replacing to bay of replaced disk)
  → assess per disk (set LEVEL + reasons)
  → sort by (bay, dev)
```

`assess()` precedence (highest wins): vdev FAULTED/UNAVAIL/REMOVED/OFFLINE or
SMART-unreadable or FAILED or bad/pending/uncorrectable>0 or endurance<10% or
wear<10% or health="GHOST" (OS rejected device) ⇒ **CRITICAL**; vdev DEGRADED or endurance<30% or wear<30% ⇒
**WARNING**; not in any pool and not a spare ⇒ **CONFIG**; else **NORMAL**.
Thresholds: `END_WARN=30`, `END_CRIT=10` in `common.py`.

Ghost disks are detected by `hba.get_ghost_disks()`. They are drives seen by the HBA but rejected by the OS (no `/dev/sdX` node). They are tagged with `dev="-"` and `health="GHOST"`.

---

## 5. The watch loop (`watch.run()`)

A single `select.select([sys.stdin], [], [], 2.0)` loop:

1. Print table + pools + details once (`_cmd_refresh`).
2. Snapshot block devices (`_block_devs()` via `lsblk -P NAME,TYPE`).
3. Each iteration:
   - If stdin is ready → read a line → dispatch `r/a/o/s/d/n/t/l/q`.
   - Re-snapshot devices. `new = current - baseline`,
     `gone = baseline - current`.
   - For each `gone` → `_handle_removed()` (report + reprint pool health).
   - For each `new` → `_handle_new_disk()`:
     `sleep 2` (udev/SMART settle) → `core.scan_one()` → render panel → prompt
     `[1] spare / [2] replace / [3] wipe / [s] skip` → confirm → run action.
   - `baseline = current`.

Keystrokes and hotplug share one loop with no extra deps; the 2 s `select`
timeout doubles as the poll interval. While a `_handle_new_disk` prompt is open
(blocking `input()`), polling pauses — acceptable since the operator is at the
console.

---

## 6. Safety model

### 6.1 Core invariants

- **Read path is side-effect-free.** `status` only runs `lsblk`/`smartctl`/
  `zpool status|list`/`sas2ircu DISPLAY`.
- **Every mutating action is confirmed** with an enhanced box dialog that shows
  the full `/dev/disk/by-id/` path, pool, vdev, and the exact commands that will
  run. `wipe` adds an extra serial-level warning.
- Actions always use the **by-id** name, never the unstable `/dev/sdX`, so a
  reslotted disk can't be acted on by accident.
- b2ctl never deletes data, never touches access controls, never edits Proxmox
  boot config. Boot-disk (rpool) replacement still needs
  `proxmox-boot-tool format/init` on the new ESP **manually** — b2ctl resilvered
  the ZFS side but does not run proxmox-boot-tool.

### 6.2 Write-command allowlist

`safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}` — any `run_check`
call whose `args[0]` is in this set is classified as mutating. Everything else
is read-only. This set governs both dry-run suppression and pre-op snapshot
triggering.

### 6.3 Dry-run mode

Activated by `--dry-run` global CLI flag or by the `t` keystroke in watch.
`watch._DRY_RUN` module-level bool is toggled by `_toggle_dry_run()`. All
`run_check` calls in watch receive `dry_run=watch._DRY_RUN`.

When dry-run is active:
- Write commands: print `[DRY-RUN] would run: <cmd>`, return `(True, "")`.
- Read commands: execute normally (real disk state shown).
- Audit entry written with `status: "dry_run"`.
- No physical side effects: the replace flow skips the locate-LED blink
  (`watch._replace_onto_spare` gates it behind `if not _DRY_RUN`).
- No disk writes: `safety.begin_op(..., dry_run=True)` skips `_capture_snapshot`
  — no pre-op snapshot file is written under `/var/log/b2ctl/snapshots/`.
- `safety.end_op` skips `_post_op_verify()` (no live re-scan / false rollback
  prompt), and `_print_op_result` renders a neutral line
  `• <op> dry-run preview — nothing changed (...)` instead of the red `✗`
  / rollback hint used for real ops.

### 6.4 Audit trail — `/var/log/b2ctl/ops.jsonl`

JSONL (one JSON object per line, append-only). Each entry written by
`safety.begin_op()` (status `"pending"`) and updated by `safety.end_op()`
(status `"ok"` / `"fail"` / `"dry_run"`).

Schema:

<details>
<summary>📋 View ops.jsonl Schema</summary>

<pre>
{
  "op_id":        "20260617-143022-replace",
  "op":           "replace",
  "disk_serial":  "S3EVNX0K123456",
  "disk_bay":     "1:4",
  "dev_path":     "/dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W...",
  "pool":         "tank",
  "vdev":         "raidz1-0",
  "cmds":         [["zpool", "replace", "tank", "/dev/disk/by-id/old", "/dev/disk/by-id/new"]],
  "status":       "ok",
  "exit_code":    0,
  "stdout":       "...",
  "stderr":       "",
  "started_at":   "2026-06-17T14:30:22",
  "ended_at":     "2026-06-17T14:30:23",
  "rollback_hint":"zpool replace tank /dev/disk/by-id/&lt;new&gt; /dev/disk/by-id/&lt;old&gt;",
  "snapshot_path":"/var/log/b2ctl/snapshots/20260617-143022-replace.txt"
}
</pre>
</details>

`op_id` format: `YYYYMMDD-HHMMSS-<op>` (second-granularity; collision possible
if two ops fire in the same second, which is safe because ops are sequential).

Read via `b2ctl log [--last N]`. Rendered by `cli._log_cmd()`.

### 6.5 Pre-op snapshots — `/var/log/b2ctl/snapshots/<op_id>.txt`

Captured inside `safety.begin_op()` before any write command runs. Runs and
concatenates:
- `zpool status <pool>`
- `zpool list -v`
- `zfs list`
- `smartctl -a <dev>` for the affected disk

Stored under `SNAP_DIR = /var/log/b2ctl/snapshots`. If the directory is not
writable, the snapshot is silently skipped — b2ctl must not crash on read-only
log dirs (all `os.makedirs` calls are wrapped in `try/except OSError: pass`).

### 6.6 Enhanced confirmation dialog — `watch._confirm_op()`

Called before every destructive action in `watch.py`. Draws a bordered box using
stdlib `textwrap.wrap(..., break_on_hyphens=False)` (the `break_on_hyphens=False`
parameter is critical — by-id names like `ata-Samsung_SSD_870_EVO_1TB_...` must
not be split at hyphens). Box width is 60 chars.

Returns `True` if user types `y`, `False` on any other input (including bare
Enter). Callers must check the return value and abort if `False`.

### 6.7 Rollback hints

Stored as `rollback_hint` string in each audit entry. Printed by `end_op()` after
the op completes.

| op                          | rollback cmd                                            |
|-----------------------------|---------------------------------------------------------|
| `offline`                   | `zpool online <pool> <dev_path>`                        |
| `add_spare`                 | `zpool remove <pool> <dev_path>`                        |
| `replace`                   | `zpool replace <pool> <new_dev> <old_dev>`              |
| `demote`                    | `zpool attach <pool> <remaining_member> <dev_path>`     |
| `create`                    | `zpool destroy <pool>` (printed with red warning)       |
| `wipefs` / `wipe` / `sgdisk`| `""` — no rollback (destruction is permanent)           |

`b2ctl rollback <op_id>` reads `ops.jsonl`, finds the entry, confirms with the
same box dialog, and executes `rollback_hint` via `run_check`. The rollback is
itself recorded as a new audit entry.

### 6.8 Post-op verification

Runs inside `end_op()` after the subprocess exits. Re-calls `zpool status` on
the affected pool and checks the expected state was reached:

| op          | expected state                          |
|-------------|------------------------------------------|
| `replace`   | new disk appears in target vdev          |
| `add_spare` | spare count in pool increased by 1       |
| `offline`   | leaf state shows `OFFLINE`               |

If the check fails:
```
⚠ Post-op check FAILED: <reason>
  Expected state not reached. See snapshot:
  /var/log/b2ctl/snapshots/<op_id>.txt
  Run: b2ctl rollback <op_id>
```

---

## 7. Deployment

```bash
cd codes && sudo ./install.sh
# package   -> /opt/b2ctl/b2ctl
# spec      -> /opt/b2ctl/ssd_spec.json
# launcher  -> /usr/local/sbin/b2ctl  (exec env PYTHONPATH=/opt/b2ctl python3 -P -m b2ctl)
# log dirs  -> /var/log/b2ctl/
#              /var/log/b2ctl/snapshots/
```
`ssd_spec.json` overrides/extends the built-in TBW defaults; model match is
case/space-insensitive substring. Add new SSD models here as you buy them.

The log directory `/var/log/b2ctl/` is created by `install.sh` (`mkdir -p`).
If it disappears or permissions change, b2ctl logs to `/dev/null` silently
(all `os.makedirs` calls in `safety.py` are wrapped in `try/except OSError: pass`).
To reset manually: `sudo mkdir -p /var/log/b2ctl/snapshots && sudo chown root:root /var/log/b2ctl`

### 7.1 `--with-tools` flag

```bash
cd codes && sudo ./install.sh --with-tools
```

Downloads archives for `sas2ircu`, `storcli64`, `perccli64` from Google Drive, then
extracts and installs the binaries. Runs after the main b2ctl install; each tool is
independent. Downloads are deleted on EXIT via `trap`.

**Download step — `download_tools(dest)`:**

- Checks for `curl` (preferred) or `wget`; aborts with `[✗]` if neither found.
- Downloads 3 archives to a temp dir using:
  `https://drive.usercontent.google.com/download?export=download&confirm=t&id=<FILE_ID>`
  (modern Google Drive endpoint — `confirm=t` bypasses the virus-scan warning page).
- Validates each download: if the file is < 1 KB it was likely an HTML error page —
  prints `[✗] <name>: download too small` and aborts.
- File IDs are hardcoded constants at the top of `install.sh`:
  `_GDRIVE_SAS2IRCU`, `_GDRIVE_STORCLI`, `_GDRIVE_PERCCLI`.

**apt prerequisites installed automatically:**

| package | why |
|---------|-----|
| `alien` | converts perccli `.rpm` → `.deb` |
| `unzip` | extracts `.zip` archives (sas2ircu, storcli) |

**Extraction chain per tool:**

| tool | archive | method | binary dest |
|------|---------|--------|-------------|
| `sas2ircu` | `SAS2IRCU_P20.zip` | `unzip` → find `x86-64_rel/sas2ircu` (falls back to `x86_rel`) | `/usr/local/sbin/sas2ircu` |
| `storcli64` | `007.3703.0000.0000_MR 7.37_Storcli.zip` | double-unzip → `dpkg-deb -x` Ubuntu DEB | `/usr/local/sbin/storcli64` + symlink `storcli` |
| `perccli64` | `perccli_7.1-007.0127_linux.tar.gz` | `tar` → `alien --to-deb` RPM → `dpkg-deb -x` | `/usr/local/sbin/perccli64` + symlink `perccli` |

`dpkg-deb -x` extracts binary contents without touching the package database.
A tmpdir is created via `mktemp -d` and cleaned on EXIT via `trap`.

**Error handling:** missing archive or failed extraction prints `[✗] <tool>: reason`
and continues. Never aborts the b2ctl package install above it.

### §7.2 `b2ctl install` — post-install tool management

After b2ctl is installed, tool binaries can be installed or reinstalled without re-running `install.sh`:

```bash
sudo b2ctl install                  # install all missing tools
sudo b2ctl install --tool sas2ircu  # install one specific tool
```

Implemented in `b2ctl/installer.py`. Each tool is independent — one failure does not abort others.

| tool | archive | method | binary path |
|------|---------|--------|------------|
| sas2ircu | SAS2IRCU_P20.zip (zip) | `cp + chmod` | `/usr/local/bin/sas2ircu` |
| storcli | storcli.zip (zip) | `dpkg -i Ubuntu/*.deb` | `/opt/MegaRAID/storcli/storcli64` → `/usr/local/bin/storcli` |
| perccli | perccli.tar.gz | `alien --scripts -i *.rpm` | `/opt/MegaRAID/perccli/perccli64` → `/usr/local/bin/perccli` |

Downloads use `urllib.request` (stdlib) from Google Drive. Size validation: < 1 KB = HTML error page → aborts with `[✗]`. Temp dir cleaned up via `try/finally`.

### §7.3 `b2ctl update` — config validation and bay_map export

```bash
b2ctl update                        # validate config, report tool status (no root needed)
sudo b2ctl update --export-bay-map  # copy bundled bay_map.json to /etc/b2ctl/
```

`b2ctl update` reads the active config and reports per-item status:
- `[✔]` — config file parses OK, tool found, bay_map exists
- `[i]` — warn: config missing (using defaults), tool not found, bay_map is bundled
- `[✗]` — error: JSON parse error, bay_map file missing

`--export-bay-map`: copies `/opt/b2ctl/bay_map.json` → `/etc/b2ctl/bay_map.json` and writes `bay_map_path` into `/etc/b2ctl/config.json`. The `/etc/b2ctl/` copy is never overwritten by `install.sh`.

---

## 8. Troubleshooting

| symptom | cause / fix |
|---------|-------------|
| table empty, pools show | `lsblk` not in `-P` mode or MODEL spaces — confirm `enumerate_disks` uses `-P`; check `lsblk -dnb -P -o NAME,...` by hand |
| BAY all `-` | `sas2ircu` missing or can't execute; bays are optional (locate still works by serial/dev). If `b2ctl check` shows "binary exists but won't execute", run `apt-get install -y libc6-i386` — sas2ircu is a 32-bit ELF |
| BAY all `-` (RAID-mode detected despite IT HBA) | crossflashed PERC H710 responds to storcli's management plane; auto-detect sees storcli and picks RaidBackend. Fix: `apt-get install libc6-i386` so sas2ircu executes, or set `controller.mode = "it"` in `/etc/b2ctl/config.json` |
| BAY numbers wrong | edit `bay_map.json` (reverse rule or explicit map); recalibrate with `b2ctl locate <serial>` |
| locate lights many bays | you're on old sas2ircu-slot locate; this build uses device-based locate — rebuild/redeploy |
| POOL `-` for in-pool disk | by-id/dev mismatch — verify `zpool status -P` leaf paths resolve (`realpath`) to the same `/dev/sdX` lsblk reports |
| END(left) `N/A` on SSD | model not in `ssd_spec.json` / no `241 Total_LBAs_Written` attr; add the rating |
| `swap` says no spare | pool has no `AVAIL` spare — add one (`[2]`) first |
| action fails | read the `✗ failed: <output>` line — it's the raw `zpool` stderr |
| `b2ctl log` shows nothing | `/var/log/b2ctl/ops.jsonl` missing — run `install.sh` or `mkdir -p /var/log/b2ctl` |
| snapshots not written | `/var/log/b2ctl/snapshots/` not writable — check permissions; b2ctl silently skips if not writable |
| `b2ctl rollback` says "not reversible" | wipe/wipefs ops have no rollback — check the snapshot at the path shown |
| post-op check FAILED after replace | ZFS might still be resilvering — `zpool status tank` to confirm; retry rollback only if resilver never starts |

---

## 9. Backend detection (`backend.py`)

`get_backend()` returns a cached `Backend` instance. On first call it runs
`_detect_backend()`:

| `controller.mode` config value | result |
|-------------------------------|--------|
| `"it"` | `ITBackend()` — no subprocess run |
| `"raid"` | `RaidBackend()` — no subprocess run |
| `"auto"` (default) | probe order below |

**Auto-detection probe order:**

1. `sas2ircu list` — if stdout is non-empty → `ITBackend`.
2. sas2ircu binary exists but failed to execute → warn stderr ("apt-get install -y libc6-i386") and **force `ITBackend`** (prevents false RAID detection on crossflashed H710).
3. `storcli64 show ctrlcount` → non-empty → `RaidBackend`.
4. `storcli show ctrlcount` → non-empty → `RaidBackend`.
5. `perccli64 show ctrlcount` → non-empty → `RaidBackend`.
6. `perccli show ctrlcount` → non-empty → `RaidBackend`.
7. None found → `die()` with an install hint.

`_backend_cache` stores the result; `setup_method` in tests clears it via
`bk_mod._backend_cache = None` to keep tests isolated.

Each backend's `name` attribute is `"it"` or `"raid"` and is used by
`b2ctl check` to report which backend was detected.

---

## 10. Config file (`config.py`)

Config file: `/etc/b2ctl/config.json`. **Optional** — missing or malformed
falls back silently to all defaults. Never written by `config.py` itself;
`cli._config_init()` writes it.

### Tool path resolution — `config.tool(name)`

Priority:

1. Non-empty `tool_paths.<name>` in config file → use as-is.
2. `shutil.which(name)` → resolved absolute path.
3. Bare `name` → let the OS resolve at subprocess exec time.

The `_cache` module-level dict is populated once by `load()` on first call and
reused. Tests that need a clean state must set `cfg_mod._cache = None` in
`setup_method`.

### Subprocesses added for RAID-mode (new in v0.5.0)

| command | purpose |
|---------|---------|
| `storcli64 /c<n>/eall/sall show all` | enumerate all drives and their EID:Slot for the bay map (also works with storcli, perccli64, perccli) |
| `storcli64 /c<n>/e<enc>/s<slot> set locate start` | turn on locate LED for one drive slot |
| `storcli64 /c<n>/e<enc>/s<slot> set locate stop` | turn off locate LED for one drive slot |
| `storcli64 show ctrlcount` | probe for RAID controller presence (also used in auto-detection) |
| `sas2ircu list` | probe for IT/HBA controller presence (existing; now also used in auto-detection) |

---

## 11. Deltas from the RAID-mode build (ADR-001)

The original ADR assumed *OS on hardware RAID1, data disks behind storcli VDs,
no boot pool*. IT mode invalidates that:

- storcli backend → **HBA backend** (`hba.py`): `lsblk` + direct `smartctl` +
  `sas2ircu`, no megaraid, no VD→NAA mapping chain.
- "drive state" came from the controller; now it comes from **ZFS vdev state**
  + SMART. `CONFIG` now means "not in a pool" (was "UGood/needs config").
- locate moved from `storcli start locate` to **device-based** ledctl/dd
  (sas2ircu slot LOCATE abandoned — scrambled slots + range-lighting bug).
- New: ZFS-on-root awareness — rpool is a pool like any other, and boot-disk
  replacement defers to `proxmox-boot-tool` (documented, not automated).
- New capability: interactive hotplug watch (no analogue in the RAID build,
  where the controller handled inserts itself).

Update ADR-001 accordingly when this build supersedes the RAID-mode one.

---

## 12. Simulation harness (`codes/sim/`)

b2ctl talks to hardware **only** through `run()`/`run_check()` (subprocess). That
seam lets you run the *real, unmodified* b2ctl against a simulated 6-disk server
on a laptop — no hardware, SSH, or root. `sim/bin/` holds fake
`zpool`/`lsblk`/`sas2ircu`/`storcli`/`perccli`/`smartctl`/… that read and mutate
`sim/state.json`; `sim/run` is a launcher that sets `PATH`, points `B2CTL_STATE`
at the state, fakes root (`os.geteuid → 0`), uses an identity bay map, selects
the backend from `state.mode`, and redirects the audit trail to `sim/var/`.

```bash
cd codes
python3 sim/simctl init           # default: rpool mirror + tank raidz1 + 1 spare
python3 sim/run status            # real b2ctl, fake disks
python3 sim/run watch             # swap/replace/offload/create — state.json mutates
python3 sim/simctl pull 1:5       # remove a disk (spare auto-resilvers if present)
python3 sim/simctl insert 1:5     # re-insert → watch sees NEW DISK DETECTED
python3 sim/simctl dirty 1:5      # mark old data/labels (create wipe-warning path)
python3 sim/simctl mode it|raid   # switch backend (sas2ircu ↔ storcli/perccli)
python3 sim/simctl show           # disks + pools + mode
```

| aspect | note |
|--------|------|
| backends | both — `simctl mode it` (sas2ircu) / `mode raid` (storcli/perccli) |
| audit isolation | sim writes `sim/var/ops.jsonl` + `sim/var/snapshots/`, **never** `/var/log/b2ctl/` → impossible to confuse with real ops; `b2ctl log`/`rollback` work in the sim |
| limitations | `by_id=""` (uses `/dev/sdX` tokens, not `ata-`/`wwn-`), LED locate = message only, models b2ctl logic/flow — **not** real ZFS (no checksum/scrub/real resilver timing) |
| smoke test | `tests/test_sim_smoke.py` drives `sim/run` via subprocess |

b2ctl source (`b2ctl/*.py`) is **unchanged** — everything sim-specific lives in
`sim/` (fake binaries + launcher). Full detail: `codes/sim/README.md`.

---

## RAID mode (Dell PERC) — every subprocess

b2ctl auto-detects (or `controller.mode=raid`) and drives **perccli**. storcli is
gone (blind to a PERC). Enumeration + SMART:

| step | command | parsed for |
|------|---------|-----------|
| tool pick | `perccli show ctrlcount` | `Controller Count = N` (>0 wins) |
| members | `perccli /cN/vall show all` | VD row (raid/state/size/name) + `PDs for VD n` (EID:Slt, DID, State, Med, Model) |
| bay→serial | `perccli /cN/eall/sall show all` | `Drive /cN/eE/sS` + `SN =` |
| member SMART | `smartctl -a -d megaraid,<DID> /dev/sda` | ATA attrs (POH, LBAs written, wear), `test result: PASSED` |
| VD block dev | `lsblk -dnb -P` MODEL contains `PERC` | which `/dev/sdX` is the virtual disk (dropped from rows) |

Actions (each `[y/N]`-guarded + audited via `safety.begin_op/end_op`):

| op | command |
|----|---------|
| locate | `perccli /cN/eE/sS set locate start|stop` |
| offline / missing | `perccli /cN/eE/sS set offline` → `set missing` |
| rebuild | `perccli /cN/eE/sS start rebuild`; progress `… show rebuild` (`NN%`) |
| create VD | `perccli /cN add vd type=raidL drives=e:s,e:s` |
| delete VD | `perccli /cN/vV del force` |

> Mutating ops + the rebuild-progress parser are **defensive** — validate on the
> R640. ADR: b2ctl is now **dual-backend** (IT/HBA via sas2ircu + ZFS; RAID via
> perccli + `smartctl -d megaraid`). On HW RAID the **controller** owns the
> array, so lifecycle is perccli-driven, not ZFS — that is why the old IT-only
> ban on `perccli`/`-d megaraid` was lifted.

### Install profiles

`b2ctl install --perc` → perccli + `controller.mode=raid`;
`b2ctl install --flash` → sas2ircu + `controller.mode=it`
(same flags on `./install.sh`). Binaries `cp -f` to `/usr/sbin` so they survive
deletion of `/opt/MegaRAID` or the download dir. `config.set_mode()` is the only
writer of `/etc/b2ctl/config.json`.
