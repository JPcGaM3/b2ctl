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
- **Dell HBA330/H330 Mini** (LSI SAS3008, IT firmware, kernel driver `mpt3sas`) —
  a third box shape, supported since **v0.19.0**. `sas2ircu` speaks SAS2 only and
  is **blind** to a SAS3 chip, but `perccli` manages the card, so auto-detect must
  not read "sas2ircu sees nothing" as "therefore this is a PERC RAID box". The OS,
  not the controller, owns the disks: they are raw `/dev/sd*` and are read with
  plain `smartctl -a /dev/sdX`, **never** through `-d megaraid` (mpt3sas exposes no
  MegaRAID ioctl, so the passthrough cannot work there by construction). Only the
  bay map comes from perccli — see §9.1/§9.2.
- Proxmox VE on ZFS-on-root (`rpool` mirror) + a data pool (`tank`, **raidz1** =
  RAID5, 3× Samsung 870 EVO 1TB + 1 hot spare). Pools created with
  `/dev/disk/by-id/ata-*` members, `ashift=12`, `compression=lz4`, `atime=off`,
  `xattr=sa`. (raidz1 resilver reads all surviving members and tolerates only
  ONE failed disk — slower/more stressful than a mirror resilver.)
- Python **stdlib only** (no pip deps). Runs as root.
- Required binaries: `smartctl`, `zpool`, `lsblk`. Optional: `sas2ircu`
  (bay numbers only), `ledmon`/`ledctl` (nicer locate LEDs), `wipefs`/`sgdisk`
  (wipe action). LED locate works without any of them via the dd fallback.
  Since **v0.19.0** a SAS backplane needs *no* vendor tool for bays either — the
  kernel's SAS transport class reports them (§3.3a), at zero subprocess cost.

Only **storcli** was removed entirely (an LSI tool blind to a PERC — it caused
false detection). `perccli` + `smartctl -d megaraid,<DID>` still drive b2ctl's
co-equal **RAID backend** (auto-detected — see §9 and the §11 deltas). On this
IT/HBA box neither is needed, because the disks are raw and read directly with
`smartctl -a /dev/sdX` + `sas2ircu`.

---

## 2. Module map

| module | responsibility | external commands |
|--------|-----------------|-------------------|
| `common.py` | colours, `run()`/`run_check()`, `Disk` model, `assess()`, `selftest_passed()` (v0.18.0 shared ATA/SAS/NVMe self-test grader) | none |
| `spec.py` | load/lookup TBW ratings (`ssd_spec.json`) | none |
| `hba.py` | enumerate disks, by-id index, bay map + **remap** | `lsblk`, `sas2ircu` |
| `blockdev.py` | shared block listing (`lsblk_pairs`, `EXCLUDE`, `vd_usage`) + `sas_bay_slots()` — kernel bay→device map, **no subprocess** (v0.19.0) | `lsblk` |
| `baymap.py` | `bay_map.json` panels, shared serial-match bay loop, `assign_sysfs_bays()` fallback (v0.19.0) | none |
| `backend.py` | auto-detect + dispatch `ITBackend`/`RaidBackend`; `ITBackend.bay_source` (v0.19.0) | `sas2ircu`, `perccli` |
| `hba_raid.py` | RAID backend: PERC enumeration/actions, controller personality probe (v0.19.0) | `perccli`, `smartctl` |
| `locate.py` | LED locate: perccli (PERC PD) / ledctl → dd (raw), timed | `perccli`, `ledctl`, `dd` |
| `smart.py` | direct SMART read + parse, endurance | `smartctl` |
| `zfs.py` | pool/topology parse, membership, actions, scrub/trim, partition, timers | `zpool`, `wipefs`, `sgdisk`, `udevadm`, `systemctl` |
| `maint.py` | append-only maintenance history (`maint.jsonl`) + `rel_time()` (v0.17.0) | none |
| `core.py` | the `scan()` pipeline, `pool_maint()` scrub/trim display strings | (composes the above) |
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
lsblk -dnb -P -o NAME,SIZE,SERIAL,MODEL,TRAN,ROTA,TYPE,WWN
```
- `-P` emits `KEY="value"` pairs; parsed with `(\w+)="(.*?)"`. **This is
  deliberate** — positional parsing breaks because MODEL contains spaces
  (e.g. `Samsung SSD 870 EVO 1TB`).
- Keep rows where `TYPE=disk`; drop names starting with
  `loop/sr/ram/zd/dm-/md`.
- `ROTA=0` ⇒ SSD. `SIZE` is bytes. `TRAN` ⇒ iface (SATA/SAS).
- `WWN` (added **v0.19.0**) ⇒ `Disk.wwn`, e.g. `0x5000c500a1b2c3d4`. A
  **serial-independent** join key: enterprise SAS drives report no `SERIAL` to
  lsblk until `smart.read()` fills it in, so the RAID backend's PD→block-device
  match cannot rely on serial alone (§9.3). Compared after
  `hba_raid._norm_wwn()` (lowercase hex, `0x`/separators stripped) because perccli
  prints the same value as `5000C500A1B2C3D4`.

### 3.2 Stable names — `hba._by_id_index()`
Walks `/dev/disk/by-id`, `realpath`s each link, and keeps the
highest-priority link per real device: `ata-` > `scsi-SATA` > `wwn-` >
`scsi-` > `nvme-<model>_<serial>` > `nvme-eui.<hex>`. Skips `*-part*`. This
`by_id` is what every `zpool` action uses, so a disk keeps a stable name across
reboots/reslots. The NVMe model link is ranked above `nvme-eui.*` so `d.by_id`
is the human-readable one operators put in `bay_map.json` (the `nvme-eui.` rule
must be listed before `nvme-` in `rank`, since both share the `nvme-` prefix).

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

If `sas2ircu list` returns nothing, `bm` is `{}` — since **v0.19.0**
`attach_bays()` no longer returns early (`hba.py:213-216`), because the kernel
fallback in §3.3a still knows every slot. `bay` stays `None` only when that is
empty too (pure SATA/NVMe box).

**Bays without sas2ircu (v0.19.0).** On an HBA330/H330 `sas2ircu` is blind to the
SAS3008, so `ITBackend(bay_source="perccli")` sources the same
`serial -> "enc:slot"` map from
```
perccli /c<n>/eall/sall show all
```
via `hba_raid.bay_map()` instead. Everything downstream is unchanged — serial
match, `bay_map.json` remap, and locate, because each drive still has its own
block device. `ITBackend.attach_bays()` / `get_ghost_disks()` pre-fill `bm`
themselves in that mode (`backend.py:107-127`) so `hba` never probes sas2ircu
behind the backend's back.

**NVMe bays — `baymap.remap_nvme()`.** NVMe has no enc:slot; its raw bay is the
PCIe BDF (`hba._nvme_pcie()` reads `/sys/class/nvme/<ctrl>/address`, drops the
`0000:` domain). A back/`type:nvme` panel relabels it; each map entry keys on
`by-id` (substring of the drive's `/dev/disk/by-id/nvme-…` link), `serial`, or
`bdf`, matched in **precedence by-id > serial > bdf**. Remap runs even when the
BDF is unavailable, so a by-id/serial entry still labels the drive. `hba_raid`
reuses `hba.enumerate_disks`, so NVMe in RAID mode is covered with no extra code.

### 3.3a Bay fallback — kernel SAS transport class (v0.19.0)

Two sysfs reads, **no subprocess at all** — `blockdev.sas_bay_slots()`
(`blockdev.SAS_DEVICE_DIR = /sys/class/sas_device`):

```
/sys/class/sas_device/end_device-*/bay_identifier             -> slot number
/sys/class/sas_device/end_device-*/device/target*/*/block/*   -> sdX
```

`bay_identifier` is the slot the **expander** reports for that end device, and
the block device hangs off the *same* sysfs node, so the result is an exact
`{"/dev/sdX": slot}` map with **no serial anywhere**. That sidesteps the whole
failure class a serial-keyed bay map has: lsblk publishes no `SERIAL` for
enterprise SAS drives until `smart.read()` runs (§3.1), and each tool truncates
serials differently (F-134).

Field evidence, HBA330 box (`bkp02`) — an exact 1:1 with the perccli PD table:

| `bay_identifier` | block device under the node | perccli EID:Slt |
|---|---|---|
| 0 … 6 | `sda` … `sdg` | `9:0` … `9:6` |
| 22 | `sdh` | `9:22` |
| 23 | `sdi` | `9:23` |
| 24 | *(none)* | *(no PD — SES enclosure processor)* |

Parsing rules:

- Unreadable nodes and a non-numeric `bay_identifier` are skipped.
- A node with **no** block device drops out on its own — that is how the SES
  enclosure processor (bay 24 above) excludes itself. Structural, not a special
  case: the same phantom-row hazard needed an explicit filter for sas2ircu
  (F-036).
- `{}` is returned when there is no SAS transport (pure SATA/NVMe box) **or**
  when more than one device is found and they all report the *same* bay — a
  backplane that reports a constant would otherwise label the whole chassis `0:0`.
- `/sys/class/enclosure/` is deliberately **not** read: on the field box the SES
  driver is not bound and that tree is empty, so `sas_device` is the only usable
  path.

**Bay-source precedence** — `hba.attach_bays()` (`hba.py:209-216`):

1. **Vendor map first.** `baymap.assign_bays(disks, bm, panels)` runs first and
   its labels always win, whether `bm` came from `sas2ircu DISPLAY` or from
   `perccli … eall/sall`. An R620's sas2ircu + `reverse_slots` output is
   unchanged from v0.18.0.
2. **sysfs fills the remainder, never replaces.** `baymap.assign_sysfs_bays()`
   skips any disk that already has a `bay` (`baymap.py:120-123`), so it only
   covers what the serial join could not reach: a SAS drive before SMART
   published its serial, or a perccli build that prints no per-drive detail
   section. No existing box's labels change.
3. **Enclosure prefix is borrowed, not invented.** The kernel reports a bare
   slot. `hba._enc_hint(bm, override)` takes the enclosure from the first vendor
   map value (`enc:slot`), else the backend's hint, else `"0"` — so a disk that
   switches from a vendor bay to a sysfs bay never changes the number the
   operator reads. `ITBackend.attach_bays()` supplies that hint from
   `hba_raid.enclosure_ids()` when `bay_source="perccli"` **and** the serial map
   came back empty, and only when the PD table names exactly one enclosure
   (`backend.py:114-120`) — an ambiguous multi-enclosure chassis falls back to
   `"0"` rather than guessing. Result on `bkp02`: `9:0 … 9:23`, not `0:0 … 0:23`.

The composed `enc:slot` then goes through the **same** `baymap.remap_slot()`
front-panel rule as every vendor bay, so `bay_map.json` keeps working unchanged.
`enclosure_ids()` is display-only — perccli *actions* still address a drive by
`Disk.ctrl_slot`, the raw locator (§9.2).

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
- **Self-test log → HEALTH_CHK (v0.17.0).** The SAME `-a` output is parsed by
  `smart._parse_selftest_log(out) -> (result, lifetime_hours)`: the newest long
  self-test row whose description matches `Extended` (ATA / NVMe) or
  `Background long` (SAS), taking its status column + the last all-digit column
  (LifeTime/Power_on_Hours). Three table shapes are handled — ATA/SAS `# N …`
  rows, and NVMe's `Self-test Log (NVMe Log 0x06)` block with bare-index rows
  (no `#`); a shared `_selftest_row()` splits columns once the index is stripped.
  Fills
  `Disk.selftest_last_result` / `Disk.selftest_last_poh` — **zero extra
  subprocess**. Short/conveyance rows are ignored. `ui._health_chk_cell` renders
  `OK`/`ERR` + ` <poh - selftest_last_poh>hPOH` (power-on-hours-relative, NOT
  wall-clock — the drive logs the test against lifetime hours). This is passive:
  it reflects the last long test whoever fired it (`[m]` health-check, or a manual
  `smartctl`). **All three dialects — ATA / SAS / NVMe — are handled; NVMe was
  never unsupported.**
- **Pass/fail classifier — `common.selftest_passed(result)` (v0.18.0).** The single
  authority both `ui._health_chk_cell` (HEALTH_CHK) and `burnin.assess` (burn-in
  verdict) call. Dialect-tolerant: `"without error"` → pass (ATA success);
  `fail`/`abort`/`interrupt`/`fatal`/`unknown`/`unable` → fail; else `"completed"` →
  pass. That last clause is the **fix**: SAS reports self-test success as the bare
  word **`Completed`** (no `without error` suffix), so the old
  `"without error" in result` check graded every healthy SAS disk `ERR`/`FAIL`.
  `burnin._sas_selftest_result` was also fixed to split the SAS log row on 2+-space
  columns and return the clean status token (`Completed`) instead of the greedy old
  capture that swallowed the trailing `-  41724  -`. An empty result string is NOT a
  pass (callers treat `''` as "no test on record" before calling).

**Scan concurrency (v0.11.1).** `core.scan` reads SMART on **two** thread pools:
direct/IT-mode targets (`smart_dtype` empty) one-thread-per-disk up to 16 (F-077);
and **megaraid passthrough** targets (`smartctl -a -d megaraid,<DID> /dev/sda`,
RAID mode) at a small cap (`smart.megaraid_workers`, default 4). Megaraid probes
all funnel through ONE PERC that serializes IOCTLs, so 16-way saturates it and
slow disks exceed `smart.timeout` (default 10 s) → the probe times out → `NOREAD`.
A megaraid timeout is **retried once** (usually just queueing behind siblings); an
IT-mode timeout is not (F-049). Both knobs live in `config['smart']` — raise the
timeout / lower the workers on a box with slow or dying SAS disks.

**Never `-d megaraid` on an HBA330/H330 (v0.19.0).** The megaraid pool only ever
holds rows `RaidBackend` synthesised (`smart_dtype = "megaraid,<DID>"`). A Dell
HBA330/H330 binds **mpt3sas**, which implements no MegaRAID ioctl, so
`smartctl -a -d megaraid,<DID> /dev/sdX` cannot succeed there — every probe returns
`NOREAD` ⇒ `CRITICAL`. That is precisely the field bug F-133 fixes (9 physical
drives, 18 rows, 9 phantom `/dev/sda` rows). Such a box now runs `ITBackend`, so
`smart_dtype` stays empty and every disk is read with plain
`smartctl -a /dev/sdX` on its own block device — the same direct path as a
crossflashed H710.

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
| add L2ARC cache | `zpool add -f <pool> cache <by-id...>` (`zfs.add_cache`; never a topology — L2ARC can't be mirrored/raidz) |
| add SLOG log | `zpool add -f <pool> log <spec>` (`zfs.add_log`, v0.17.0 `raid_type=`): `single` → `<devs>`; `mirror` (≥2) → `mirror <devs>`; `raid10` (even ≥4) → `mirror a b mirror c d …`; `None` → legacy auto (mirror if >1). **raidz REJECTED** → `(False, "raidz is invalid for a SLOG vdev …")`, no command run |
| remove aux vdev | `zpool remove <pool> <token>` (`zfs.remove_vdev`; cache/log/spare leaf) |
| attach | `zpool attach <pool> <existing> <new>` |
| create pool | `zpool create ...` (checks `wipefs -n` for existing labels first) |
| create RAID10 | `zpool create ... <name> mirror a b mirror c d ...` (repeated `mirror` from disk pairs, shared `zfs._mirror_pairs`) |
| over-provision partition | **wipe first** (`zfs.wipe`) then `sgdisk -n 1:0:+<size> -t 1:bf01 <dev>` → **mandatory** `udevadm settle` (`zfs.partition`; returns the `-part1` token). Wipe-before is the v0.18.0 fix (F-132) |
| manual scrub | `zpool scrub <pool>` (`zfs.start_scrub`; kernel runs it in the background) |
| manual trim | `zpool trim <pool>` (`zfs.start_trim`) |
| wipe | `zpool labelclear -f <dev>` → `wipefs -a <dev>` → `sgdisk --zap-all <dev>` |

**Aux vdevs (runbook STEP 03).** L2ARC `cache` loss is harmless (cache miss),
so it is added unguarded and never given a topology (L2ARC can't be mirrored or
raidz). SLOG `log` holds in-flight sync writes: since **v0.17.0** `add_log` takes
an explicit `raid_type` (`single`/`mirror`/`raid10`, `None` = legacy auto-mirror
on ≥2) — watch `[e]xtend → [2]` prompts it when ≥2 devs, CLI `log-add
--mirror|--raid10`; **raidz is rejected** (a log vdev cannot be raidz). The
workflow still warns on a single (non-mirrored) log and always reminds the
operator to use a **PLP** SSD (PLP is not reliably exposed by SMART, so it is a
warning, not a gate). All honor `--dry-run`. CLI: `b2ctl
cache-add|cache-rm|log-add|log-rm <pool> <dev…>`; watch: `[e]xtend`.

**Over-provisioning (v0.17.0, first partition-creation in the repo).** When a
`--size` (CLI) / "size to use per device" prompt (watch) is given, `zfs.partition(
dev, size, *, type_code="bf01", max_bytes=None, dry_run=)` runs `sgdisk -n
1:0:+<size> -t 1:bf01 <dev>` then a **mandatory** `run([_tool("udevadm"),
"settle"])` (skipped under dry-run) — the `-part1` by-id symlink appears
asynchronously, so without settle the follow-up `zpool add … -part1` races and
fails. It returns the first-partition token from `zfs._part1_path` (by-id →
`-part1`, nvme/mmcblk/loop → `p1`, else `1`; string convention only, never
`os.path.exists`, so it resolves under dry-run). `zfs.parse_size` turns
`32G`/`512M`/`1.5T` into bytes. **Blank size = whole disk** (idiomatic
`wholedisk=on`, unchanged). `sgdisk` is already in `safety.WRITE_CMDS` → dry-run
gated. No perf difference vs whole disk when aligned (sgdisk's 1 MiB default) with
`ashift=12`; the reserve buys SSD endurance/consistency, costs capacity (ADR-003).

**Wipe-before-partition (v0.18.0 fix, F-132).** `sgdisk -n 1:0:+<size>` places
partition 1 at the first free aligned sector, so a **stale GPT** on a used disk
pushed it past the old partition — `Could not create partition 1 from <big sector>`
(overlap, or past end-of-disk on a small drive). Both over-provision sites now
**warn + confirm the wipe up front (§9), then wipe each disk before
`zfs.partition`**, reusing `zfs.wipe` (`zpool labelclear` + `wipefs -a` + `sgdisk
--zap-all`). **Declining the confirm aborts and touches NOTHING** — the wipe never
runs:
- `watch._maybe_partition(disks, indices, size)` — validates sizes, prints a §9
  WIPE warning naming each disk, asks **one** confirm, then (if accepted) wipe →
  partition per disk, aborting on any wipe/partition failure.
- `cli._partition_devs(devs, size)` — prints `[!] over-provision will WIPE then
  partition: <devs>` and asks `confirm("wipe and partition these disk(s)?")`
  **before touching any disk** (matching the watch flow); on accept, wipe → partition
  each resolved by-id device. This is the up-front confirm — it is NOT deferred to
  the later `add-cache`/`add-log` prompt.
- In `watch._cmd_create`, when a size is given the over-provision path **replaces**
  the old whole-disk dirty-wipe block (`zfs.has_zfs_label` → confirm → `zfs.wipe`),
  so there is no double wipe/confirm; a blank size keeps that whole-disk path.

So over-provisioning on **both** `b2ctl create --size` and `cache-add`/`log-add
--size` (as well as the watch prompts) warns + confirms the wipe first, then wipes →
partitions → hands ZFS the `-part1` token; a declined confirm wipes nothing.

**Aux-vdev repair (v0.14.0).** When an L2ARC cache disk or one leg of a mirrored
SLOG dies, pull it, insert a new disk, and repair through the tool. Enumerated by
`zfs.aux_leaves(pool)` (cache/log leaves tagged `klass`/`mirror_leg`/`degraded`);
the shared core is `watch._repair_aux(pool, leaf, new, new_token=…)`, which
branches by class + leaf state:

| case | commands (list-form, all through `run_check`) | resilver |
|------|-----------------------------------------------|----------|
| **cache** (any state) | `zpool remove <pool> <old>` → `zpool add -f <pool> cache <new>` | no (L2ARC is volatile; it cannot be `zpool replace`d) |
| **SLOG mirror leg** (`vdev=mirror-*`) | `zpool replace -f <pool> <old-leg> <new>` | yes — `_wait_resilver()` polls `poll_resilver_status()` |
| **SLOG single, gone** (state `REMOVED`/`UNAVAIL`) | `zpool remove <pool> <old>` → `zpool add -f <pool> log <new>` | no |
| **SLOG single, present** (FAULTED/DEGRADED) | `zpool replace -f <pool> <old> <new>` | yes |

`replace` is chosen over `attach`+`detach` for a mirror leg deliberately: it is
atomic and never exposes a hand-picked *detach* target, so a mistyped device can't
destroy the surviving good leg (the operator only ever names the disk to *add*).
The op is audited as `"aux-repair"` (`safety.begin_op`/`end_op`, `details=
{old_dev,new_dev}`); `_post_op_verify` passes if `zpool status` shows a resilver
marker **or** the new device token; the `_ROLLBACK["aux-repair"]` hint is advisory
(cache loss is harmless, a SLOG mirror keeps redundancy — no auto-rollback). Honors
`--dry-run`. CLI: `b2ctl cache-replace|log-replace <pool> <old> <new>` (the `new`
disk resolves strictly to a by-id, §9; `old` is permissive so a raw leaf token
passes through). watch: `[e]xtend → [4]`.

### 3.6a Disk health-check (burn-in engine) — `burnin.py` (runbook STEP 02, read-only vetting)

> **v0.18.0:** the burn-in **engine** (`burnin.py`) is unchanged, but its
> **surface** merged into `maint`. The standalone `b2ctl burnin` verb and the watch
> `[b]` key are **gone**; disk vetting is now `b2ctl maint health` /
> `[m]aint → [3] health-check` (`watch._maint_health`, renamed from `_cmd_burnin`).
> See §3.6b and **ADR-004** for the merge rationale (both ran `smartctl -t long`).

**Multi-disk & non-blocking (v0.10.0).** `run_multi(targets, …)` vets several disks
at once and returns to the prompt while they run; a state file makes it
re-attachable. See **ADR-002** for the background-process/state-file architecture.

| step | command / mechanism |
|------|---------|
| start self-test | `smartctl -t long\|short [-d <dtype>] <dev>` (`start_selftest`) — runs on the drive firmware, returns immediately |
| start surface scan (opt) | `subprocess.Popen(["badblocks","-sv","-b","4096",<dev>], stderr=<logfile>, start_new_session=True)` — **read-only, never `-w`**, detached (`start_scan`) |
| self-test progress | `smartctl -a <dev>` → `parse_selftest()` reads `% of test remaining` (ATA) / `% complete` (SAS); ETA = `Extended self-test routine recommended polling time: (N) minutes` × remaining% (`_selftest_eta_min`, ATA only) |
| scan progress | tail the badblocks logfile for the last `NN% done` (`_parse_badblocks_log`); liveness via `os.kill(pid,0)` + `waitpid(WNOHANG)` reaping (`_pid_alive`); ETA computed from our own elapsed time (`scan_progress`) |
| live view | `live_view()` redraws `ui.render_burnin_view()` every ~2.5 s (ANSI cursor-up + clear). **Ctrl-C detaches** (saves state, keeps running); it does NOT abort |
| verdict | on completion `_finish()` re-reads SMART (`core.scan_one`) + `assess(disk)` → FAIL (uncorrected>0 / self-test error via `common.selftest_passed`, v0.18.0), WARN (grown defects / surface-scan bad blocks / power-on hours **if** `health.<type>.poh_warn` is set — off by default, v0.13.0), else PASS |
| cancel (v0.12.0) | `cancel(targets)` / `cancel_all()` — per record: **abort self-test** `smartctl -X [-d <dtype>] <dev>` (`_cancel_records`) + **stop scan** `os.kill(scan_pid, SIGTERM)`, but only after `_is_our_badblocks(pid,dev)` confirms `/proc/<pid>/cmdline` is our `badblocks <dev>` (PID-reuse guard) — then drop the record from state. Honors `--dry-run`. Both are read-only/abort ops; nothing is written |

- **State file:** `os.path.join(safety.LOG_DIR, "burnin.json")` (records keyed by
  serial: dev/bay/dtype/kind/do_scan/scan_pid/scan_log/started), plus per-disk
  `scan-<serial>.log`. Path is read at call time so the sim's `safety.LOG_DIR`
  monkeypatch redirects it to `sim/var/` (`save_state`/`load_state`).
- **Re-entrancy:** `run_multi` polls `selftest_status` first and **never restarts**
  a disk already under a self-test; `_finish` prunes completed records from state.
- **Exit code note:** because the run is backgrounded, `b2ctl maint health <disk>`
  exits `0` once the tests are *started* — the PASS/WARN/FAIL verdict is shown later
  in the live view / `--status`, not encoded in the exit code (the old single-disk
  synchronous FAIL→exit-1 is gone).

CLI (v0.18.0, was `b2ctl burnin`) `b2ctl maint health <bay\|dev> [<bay\|dev> …]
[--scan] [--short]`, `b2ctl maint health --status` (re-attach), and `b2ctl maint
health --cancel <bay\|dev …>` / `--cancel-all`; watch `[m]aint → [3]` opens a menu
when a health-check is in flight (`[v]`iew / `[c]`ancel-one / cancel-`[a]`ll /
`[n]`ew). **Both paths vet FREE/SPARE disks only:** the watch selection lists free
disks (`_avail_for_aux`), and the CLI `maint health <dev…>` also **refuses** an
in-pool member — both go through `run_multi` → `_poolable_target`, which prints
`maint health vets free/spare disks; <dev> is in pool '<pool>' — self-test it with
\`smartctl -t long\` directly`. (To self-test an active member, run `smartctl -t
long <by-id>` in a shell; the member's HEALTH_CHK still updates passively from
`smartctl -a`.) Starting a health-check writes a `"health"`/`"started"` event to
`maint.jsonl` on **both** paths (`watch._maint_health` and CLI `_burnin`),
**skipped under `--dry-run`**. The only writes are the self-test trigger and a
read-only `badblocks`; nothing on the disk is modified. On Ctrl-C,
`burnin.live_view` prints `b2ctl maint health --status` to re-attach
(`burnin.py:427`).

### 3.6b Manual maintenance — scrub / trim / health-check + history (v0.17.0)

Manual scrub/trim is the **primary** maintenance path (scheduled timers are
secondary and default off — ADR-003). The kernel owns each op, so there is **no
Popen / state file** (unlike burn-in): b2ctl issues the command and polls.

| step | command / mechanism |
|------|---------------------|
| start scrub | `zpool scrub <pool>` (`zfs.start_scrub`) — returns at once, kernel scrubs in the background |
| start trim | `zpool trim <pool>` (`zfs.start_trim`) |
| scrub progress | `zpool status <pool>` → `zfs.poll_scrub_status` (sibling of `poll_resilver_status`): matches the `scan: scrub` line — `scrub in progress … N% done … <hh:mm:ss> to go`, or `scrub repaired … with N errors` = completed. `ok=False` on empty output; a resilver line never matches |
| trim progress | `zpool status -t <pool>` → `zfs.poll_trim_status`: per-leaf `(trimming\|untrimmed\|trimmed\|trim unsupported)` + optional `%`. **Coarse / OpenZFS-version-dependent** — `done` may be None even while trimming |
| last-scrub date | `zpool status <pool>` → `zfs.last_scrub_date`: parses the scan line's `on <ctime>` with `datetime.strptime(raw, "%a %b %d %H:%M:%S %Y")` → ISO string. **Pure read (§9).** ZFS keeps only the latest scrub → older runs live only in `maint.jsonl` |
| health-check | `smartctl -t long [-d <dtype>] <dev>` (reuses `burnin.start_selftest`) on picked disk(s) — non-blocking; the verdict lands in the drive's self-test log → HEALTH_CHK |

**Live watch (`watch._wait_scrub`/`_wait_trim`).** 2 s poll with a `% done` bar.
`_wait_scrub(pool, baseline)` captures the pre-scrub `last_scrub_date` and accepts
a "completed" reading only once that date **advances** past `baseline` — so the
persisted OLD `scrub repaired` line isn't mistaken for the scrub just issued.
**Ctrl-C DETACHES** (the kernel keeps running); 5 consecutive unreadable
`zpool status` polls stop the watch. All honor `--dry-run`.

**History — `maint.py` + `maint.jsonl`.** A dedicated **append-only** log beside
`ops.jsonl`/`burnin.json`, resolved from `safety.LOG_DIR` **at call time** so the
sim/test `safety.LOG_DIR` redirect catches it for free (ADR-002 pattern). It
copies safety's O_APPEND-of-one-line idiom (POSIX-atomic), **not** the ops schema.
Record:

```json
{"ts": "2026-07-08T03:00:00", "kind": "scrub", "target": "tank", "status": "ok",
 "detail": "completed at 2026-07-08T02:58:41"}
```

- `kind` ∈ `scrub`/`trim`/`health`; `target` = pool name (scrub/trim) or disk
  serial (health); `status` ∈ `started`/`ok`/`fail`; `ts` =
  `datetime.now().isoformat(timespec="seconds")` (naive-local, like safety).
- API: `log_event(kind, target, status, detail)` (best-effort — a write failure
  never aborts the op, the kernel already ran it), `load_events(last=N)`,
  `last_event(kind, target)`, `rel_time(iso_or_epoch) -> "2m ago"`.

**Read-path purity (§9).** The pools SCRUB column is a **pure live read**
(`core.pool_maint` → `zfs.last_scrub_date`, fallback `maint.last_event("scrub").ts`;
TRIM only from `maint.last_event("trim")` — ZFS has no live last-trim date).
Writing a completion record for a background/timer scrub happens **only** in
`watch._reconcile_scrub_history` (called from watch's refresh, which already
mutates via `prune_orphan_timers`), deduped on the ISO date embedded in `detail`,
and honoring `--dry-run`. **`status`/`top` never write.**

**Surfaces (v0.18.0 — `maint` is the single surface).** watch `[m]aint` →
`_cmd_maint(tbw, *, action=None, pool=None)` prompts `[1] scrub [2] trim
[3] health-check` and branches on action first: scrub/trim per-pool,
`health` → `_maint_health` (the burn-in engine, §3.6a). CLI has one `maint` verb
with subcommands plus the bare history view:

| CLI | handler | mutates? | root? |
|-----|---------|----------|-------|
| `b2ctl maint` / `b2ctl maint --log [--last N]` | `_maint` (reads `maint.jsonl`) | no | exempt |
| `b2ctl maint scrub [<pool>]` | `_scrub` → `zfs_actions.scrub` | yes | **root** |
| `b2ctl maint trim [<pool>]` | `_trim` → `zfs_actions.trim` | yes | **root** |
| `b2ctl maint health <dev…> [--scan] [--short] [--cancel …] [--cancel-all]` | `_burnin` → `burnin.run_multi/cancel` | yes | **root** |
| `b2ctl maint health --status` | `_burnin` → `burnin.status_view` | no (re-attach) | exempt |
| `b2ctl scrub\|trim [<pool>]` | back-compat aliases of `maint scrub/trim` | yes | **root** |

Root gating is `cli._needs_root(args)`: `maint` is in `_ROOT_EXEMPT`, but the
function special-cases it — bare `maint`/`--log` (`args.maint_cmd is None`) and
`maint health --status` are exempt; `maint scrub|trim|health <dev>` return True
(need root). `zfs_actions.scrub(pool)`/`trim(pool)` still delegate to
`_cmd_maint(action=…, pool=…)`.

### 3.7 LED locate — `locate.py`
`sas2ircu ... LOCATE <slot>` is **not used** — on this backplane it lights a
whole range of bays, and the slot numbers are scrambled anyway. `blink_disk()`
picks the most-dedicated indicator, **perccli → ledctl → dd**, by applicability:

1. **PERC PD** (`is_perc_pd`: VD member / UGood) → `hba_raid.locate(enc:slot, on)`
   (`perccli start/stop locate`) **only**. If perccli fails, report failed — **no
   `/dev` fallback**: a member shares `/dev/sda`, so ledctl/dd there would light
   the whole VD (all members' bays = wrong bay).
2. **raw disk** (own `/dev` node) → `blink(dev, …)`:
   - **ledctl** (v0.8.7) if `shutil.which(ledctl)` (`_have_ledctl`) — the
     backplane's dedicated locate LED via SGPIO/SES: `_ledctl(dev, on)` runs
     `ledctl locate=<dev>` / `ledctl locate_off=<dev>`. The first `locate=` doubles
     as a support probe; the LED is **always turned off in a `finally`**.
   - **dd** fallback (`_blink_dd`) if ledctl is absent or can't drive the device
     (`ledctl locate=` returned non-zero): `dd if=<dev> of=/dev/null bs=1M` for N
     seconds (READ ONLY) — the activity LED flickers, nothing to switch off.

`ledctl` needs SGPIO/SES; PERC VD members (no per-drive node) and non-VMD M.2
NVMe won't drive it → dd fallback. Default blink is 5 s then auto-stop.
`b2ctl locate <bay|serial|sdX> [secs]` resolves any identifier to the device
first and prints `via {perccli|ledctl|dd}`. `b2ctl status --locate` blinks all
at-risk disks at once (`blink_many`, still dd). **Invariants: LED-only (never a
writing command; `dd` `of=` is always `/dev/null`); always end with the locate
LED off.**

---

## 4. The scan pipeline (`core.scan()`)

```
enumerate_disks (lsblk -P)
  → attach_bays (sas2ircu DISPLAY — or perccli eall/sall when bay_source='perccli', §9.2; by serial;
                 then /sys/class/sas_device for whatever is still bay-less, §3.3a; remapped via bay_map.json)
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

**Serial-domain guard (v0.19.0, `hba.py:244-256`).** Enterprise SAS drives expose
no lsblk `SERIAL` until `smart.read()` runs, and `scan()` computes ghosts *before*
the SMART fan-out — so on the first pass a serial-keyed bay map can match nothing
and every entry looks like a rejected disk. The guard fires only when **both** hold:

1. `_matched_any(bm, os_serials)` is false — **no** bay-map serial lines up with
   **any** OS disk serial. One hit proves the two tools agree on the serial
   format, which makes the remaining misses trustworthy as real ghosts.
2. The serial-less block devices already present can account for all of them:
   `unidentified = len([d for d in disks if d.dev != "-" and not d.serial])` is
   non-zero **and** `len(ghosts) <= unidentified`.

Then it is a scan-ordering artefact and `[]` is returned. A **surplus** —
more would-be ghosts than unidentified disks — cannot be explained away, so the
whole list is reported rather than hiding a real OS_REJECTED drive.

> The first cut of this guard blanked the ghost list outright whenever condition 1
> held plus *any* serial-less disk existed, which permanently disabled
> OS_REJECTED detection on every box whose drives publish no lsblk serial (F-133
> review). It is now the narrow artefact filter described above: a drive the OS
> genuinely rejected (foreign RAID metadata — the reason the ghost concept exists)
> still raises its GHOST/CRITICAL row and the `[u]dev rescue` prompt whenever any
> serial did line up, no serial-less disk is left to explain it, or the ghosts
> outnumber the serial-less disks.

---

## 5. The watch loop (`watch.run()`)

A single `select.select([sys.stdin], [], [], 2.0)` loop:

1. Print disk table + **Storage summary** + details once (`_cmd_refresh`), the
   same blocks the CLI `status` path prints. The summary
   (`ui.render_storage(core.assemble_storage(disks, pools, vols))`) is one
   unified table with **hardware rows above software**:
   - `core.assemble_storage` maps each PERC volume (`backend.raid_volumes()`) to
     its block device via the HW member disks and reads used/free from
     `hba.vd_usage(dev)` (lsblk FS columns of the mounted VD, else `-`); each ZFS
     pool (`zfs.list_pools()`) gets its level from `zfs.pool_level()` and used/free
     from `zpool list`.
   - The disk table itself (`ui.render_table`) groups rows under
     `--- Hardware (PERC RAID) ---` / `--- Software (ZFS) ---` sub-headers when
     both kinds are present (single-type boxes stay flat).
   - IT-only box: no volumes → the summary is just the software (pool) rows.
2. Snapshot block devices (`_block_devs()` via `lsblk -P NAME,TYPE`).
3. Each iteration:
   - If stdin is ready → read a line → dispatch
     `r/a/o/s/d/t/n/e/m/u/x/l/q` (`e`=extend cache/log/raid, `m`=maint
     scrub/trim/health, `u`=udev-rescue, `x`=destroy).
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

**`[a]ssign` multi-select (v0.11.0).** `_cmd_assign` parses space-separated
indices via the shared `watch._pick_indices(sel, n)` helper (built on
`_one_based`): rejects `0`/negatives (F-052) and out-of-range, dedupes, order
preserved — reused by assign / `[n]ew-pool` / `[e]xtend` / `[m]aint` health-check, which
closed a pre-existing F-052 gap where `[n]ew-pool`/`[e]xtend` let `0` select the
LAST disk (`list[-1]`) and wipe it. A single pick keeps the existing per-disk
menu; 2+ picks open a **homogeneous** batch menu (candidates are tagged by
category — `zfs` / `ghost` / `perc` — and mixing types is refused with a per-type
count). PERC-UGood batch (`raid_actions.assign_perc_batch`) loops
`hba_raid.set_jbod` / `hba_raid.add_hotspare` per drive (or one `create_vd` for
"one volume from all"); free-disk batch (`watch._assign_free_disks_batch`) loops
`zfs.add_spare` / `zfs.wipe`. Each looped PERC mutation gets its own
`safety.begin_op/end_op`; every batch confirm **lists the selected devices**
(model+serial) before the `[y/N]` (§9 device-readback); and create-VD **refuses**
a selection spanning two controllers (a single VD is controller-local). All honor
`--dry-run`.

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

`safety.WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd", "perccli", "perccli64",
"smartctl", "badblocks", "systemctl"}` — any `run_check` call whose `args[0]` is
in this set is classified as mutating. Everything else is read-only. This set
governs both dry-run suppression and pre-op snapshot triggering. The last five
entries cover RAID-mode actions (`perccli`), self-test/surface-scan triggers
(`smartctl -t`, `badblocks`), and maintenance-timer enable/disable (`systemctl`);
their read-only sub-commands go through `run()`, not `run_check`, so they are not
gated.

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

Downloads archives for `sas2ircu`, `perccli64` from Google Drive, then extracts and
installs the binaries (storcli was dropped — LSI tool, blind to a PERC). Runs after
the main b2ctl install; each tool is independent. Downloads are deleted on EXIT via
`trap`.

**Download step — `download_tools(dest)`:**

- Checks for `curl` (preferred) or `wget`; aborts with `[✗]` if neither found.
- Downloads 2 archives to a temp dir using:
  `https://drive.usercontent.google.com/download?export=download&confirm=t&id=<FILE_ID>`
  (modern Google Drive endpoint — `confirm=t` bypasses the virus-scan warning page).
- Validates each download: if the file is < 1 KB it was likely an HTML error page —
  prints `[✗] <name>: download too small` and aborts.
- File IDs are hardcoded constants at the top of `install.sh`:
  `_GDRIVE_SAS2IRCU`, `_GDRIVE_PERCCLI`.

**apt prerequisites installed automatically:**

| package | why |
|---------|-----|
| `alien` | converts perccli `.rpm` → `.deb` |
| `unzip` | extracts `.zip` archives (sas2ircu) |

**Extraction chain per tool:**

| tool | archive | method | binary dest |
|------|---------|--------|-------------|
| `sas2ircu` | `SAS2IRCU_P20.zip` | `unzip` → find `x86-64_rel/sas2ircu` (falls back to `x86_rel`) | `/usr/local/sbin/sas2ircu` |
| `perccli64` | `perccli_7.1-007.0127_linux.tar.gz` | `tar` → `alien --to-deb` RPM → `dpkg-deb -x` | `/usr/local/sbin/perccli64` + symlink `perccli` |

`dpkg-deb -x` extracts binary contents without touching the package database.
A tmpdir is created via `mktemp -d` and cleaned on EXIT via `trap`.

**Error handling:** missing archive or failed extraction prints `[✗] <tool>: reason`
and continues. Never aborts the b2ctl package install above it.

### §7.2 `b2ctl install` — 1:1 mirror of `./install.sh`

`b2ctl install` (`cli.py::_install` → `b2ctl/installer.py`) reproduces the
`./install.sh` contract, flag-for-flag. The package itself is already deployed (we
are running from it), so the no-flag form does **not** redeploy — it reports
status. Everything else matches:

| `b2ctl install …` | `installer` call | tools | mode | root |
|-------------------|------------------|-------|------|:----:|
| *(no flag)* | `install_base()` | — (report only, no download) | — | no |
| `--with-tools` | `install_tools(["sas2ircu","perccli"])` | both | — | yes |
| `--perc` | `install_profile("perc")` | perccli | `raid` | yes |
| `--flash` | `install_profile("flash")` | sas2ircu | `it` | yes |
| `--tool TOOL` | `install_tools([TOOL])` | one | — | yes |

The flags are an `argparse` mutually-exclusive group. `install_base()` needs no
root (it only reads `tool_ok()` + `config.controller_mode()`); the acting branches
check `os.geteuid()==0` individually. Each tool is independent — one failure does
not abort others.

| tool | archive | method | binary path |
|------|---------|--------|------------|
| sas2ircu | SAS2IRCU_P20.zip (zip) | unzip `*x86*_rel/sas2ircu` → `cp -f` + chmod | `/usr/sbin/sas2ircu` |
| perccli | perccli.tar.gz | `alien --scripts -i *.rpm` → `cp -f perccli64` | `/usr/sbin/perccli` |

`install_tools()` first runs `ensure_prereqs()` (`dpkg --add-architecture i386`,
`apt-get install -y alien libc6-i386`, verifying the 32-bit loader actually
exists). Downloads use `urllib.request` (stdlib) from Google Drive; < 1 KB = HTML
error page → `[✗]`. Temp dir cleaned via `try/finally`.

> `./install.sh` (no flag) installs **only** the b2ctl package — no `apt`, no
> downloads. The apt prerequisites are installed by `install_tools()` only when a
> tool is actually being added (`--with-tools`/`--perc`/`--flash`).

### §7.3 `b2ctl update` — config validation + resource sync

```bash
b2ctl update            # non-root: validate config + report status only
sudo b2ctl update       # root: also sync bay_map.json + ssd_spec.json -> /etc/b2ctl/ + bind config
sudo b2ctl update --force   # overwrite operator-customized files (saves .bak first)
```

`b2ctl update` reads the active config and reports per-item status:
- `[✔]` — config parses OK, tool found, data file is a config override or the `/etc` standard
- `[i]` — warn: config missing (defaults), tool not found, data file is the bundled fallback
- `[✗]` — error: JSON parse error, data file missing

**Resource sync (root only).** For each managed file `(bay_map.json, ssd_spec.json)`
`cli._sync_resource()` compares the bundled copy with `/etc/b2ctl/<file>` via
`filecmp.cmp(src, dest, shallow=False)`:
- dest missing → copy → `created`
- identical → `current` (no write)
- differs (operator-customized) → **preserved** as `customized-kept` unless
  `--force`, which backs up to `<file>.bak` then overwrites → `updated (backup .bak)`

After syncing, it writes `bay_map_path` / `ssd_spec_path` (absolute `/etc/b2ctl/`
paths) into `/etc/b2ctl/config.json` so resolution is directory-independent.
`--export-bay-map` is a deprecated alias of `--force`. The `/etc/b2ctl/` copies
are never touched by `install.sh`.

**Why directory-independence needed two fixes (v0.8.5).**
1. *Code path* — the launcher runs `python -m b2ctl`, and `python -m` prepends
   the cwd to `sys.path[0]` ahead of `PYTHONPATH`. Running from a directory that
   contains a `b2ctl/` package (the source checkout) silently shadowed the
   installed `/opt/b2ctl`. The launcher now sets `PYTHONSAFEPATH=1` (Python ≥3.11)
   so cwd is not prepended → the installed copy always wins.
2. *Data path* — `config.bay_map_path()` / `ssd_spec_path()` resolve
   **override > `/etc/b2ctl/<file>` > bundled `__file__`-relative**
   (`config._resource_path`). The `__file__` fallback is cwd/copy-sensitive; the
   `/etc` standard is absolute, so preferring it (and `b2ctl update` binding it in
   config) makes the mapping load the same file from any directory.

---

## 8. Troubleshooting

| symptom | cause / fix |
|---------|-------------|
| table empty, pools show | `lsblk` not in `-P` mode or MODEL spaces — confirm `enumerate_disks` uses `-P`; check `lsblk -dnb -P -o NAME,...` by hand |
| BAY all `-` | `sas2ircu` missing or can't execute; bays are optional (locate still works by serial/dev). If `b2ctl check` shows "binary exists but won't execute", run `apt-get install -y libc6-i386` — sas2ircu is a 32-bit ELF |
| BAY all `-` (RAID-mode detected despite IT HBA) | a crossflashed PERC H710 may still answer `perccli show ctrlcount`, so auto-detect can pick RaidBackend if sas2ircu can't run. Fix: `apt-get install libc6-i386` so sas2ircu executes (→ forces IT), or set `controller.mode = "it"` in `/etc/b2ctl/config.json` |
| 18 rows for 9 drives — half claim `DEV=/dev/sda`, `SERIAL N/A`, `HEALTH NOREAD`, `LEVEL CRITICAL` | pre-v0.19.0 on a Dell HBA330/H330: sas2ircu is blind to the SAS3008, so auto-detect fell through to `RaidBackend`, which synthesised one row per controller PD and read it through a megaraid passthrough mpt3sas does not implement (F-133). Fix: upgrade to ≥ v0.19.0 — `is_hba_personality()` (§9.1) now gates the choice; stop-gap on an old build: `controller.mode = "it"` |
| `raid-create` / `raid-del` / `raid-replace` refused with "hardware-RAID actions require RAID mode … This box is IT/HBA" on a real PERC | pre-fix `is_hba_personality()`: with no VD to look at (fresh box, or right after `raid-del`) it ignored its own `RAID-Mode` string and tie-broke on raw counts, letting NVMe/BOSS/USB disks that are not on the controller outvote the hidden PERC drives → classified HBA. Fixed in v0.19.0 — an explicit `RAID-Mode` now short-circuits to RAID and rung 4 resolves each PD individually (§9.1). If a controller reports neither a VD, a personality nor an unresolved PD, set `controller.mode = "raid"` in `/etc/b2ctl/config.json` — the probe is then skipped entirely |
| a drive perccli lists (UGood/Failed) is missing from `b2ctl status` in RAID mode | pre-fix same-model refusal in `hba_raid.enumerate_disks()`: it fired for identified PDs and decided mid-loop, so with identical models the drop was enc:slot-order dependent. Fixed in v0.19.0 by the two-pass join (§9.3) — the refusal now only touches a PD perccli gave **no** `SN` and **no** `WWN` for. If a drive still vanishes, check that `perccli /c<n>/eall/sall show all` prints an `SN =`/`WWN =` line for that slot |
| no GHOST row on a box whose drives report no lsblk `SERIAL` | the serial-domain guard in `hba.get_ghost_disks()` (§4) only suppresses when **no** bay-map serial matched **any** OS serial *and* `len(ghosts) <= ` the number of serial-less block devices — a scan-ordering artefact before SMART runs. A surplus is always reported. If the counts do balance and you still suspect a rejected drive, re-check after a full scan (`b2ctl status`, SMART has run by then) or compare the controller drive list against `lsblk` |
| `set jbod` (or hot-spare / add-vd) fails: `ErrCd 255 Operation not allowed` | the PERC refuses the transition. Two causes share that message. **(a)** the drive carries a **foreign config** — perccli marks it `DG = F` while `State` still reads `UGood`; run `b2ctl raid-foreign` (or `perccli /cN/fall show`), then `--import` / `--clear` (§9.4). **(b)** the controller's JBOD policy is off or unsupported — `perccli /cN show all` → `JBOD = OFF` / `Support JBOD = No`; fix with `perccli /cN set jbod=on`, which b2ctl deliberately will not run for you (controller-wide policy). From v0.20.0 b2ctl refuses (a) up front and prints which of the two it found |
| a drive reads "available (Unconfigured Good) — set JBOD for ZFS" but nothing works on it | pre-v0.20.0: `enumerate_disks()` copied `pd_state` but dropped the `DG` column, so a foreign drive was indistinguishable from a free one (F-135). Fixed by `Disk.pd_foreign` (§9.4). On an older build, check `perccli /cN/fall show` by hand |
| every hardware-RAID row shows the SAME `DEV`, and two VDs report identical `USED`/`FREE` | pre-v0.21.0: one `ctrl_dev` was resolved from `perc_devs[0]` and stamped on every member of every volume, so `assemble_storage` measured one filesystem twice (F-136, §9.5). Fixed by `Disk.ctrl_dev` + the NAA/size VD→device join. On an older build, cross-check by hand: `perccli /c0/vall show all \| grep -i 'naa\|Name'` against `lsblk -o NAME,WWN,SIZE,MODEL` |
| hardware-RAID rows show `DEV = -` | **expected from v0.21.0** — a PD behind a virtual disk has no device node. Identify it by BAY, which every b2ctl action uses anyway (§9.5) |
| table wraps into unreadable stripes, or scrolls off the top | pre-v0.21.0 the width was a hardcoded 196 with no terminal awareness (F-137, §9.6). From v0.21.0 columns are shed to fit and `status` pages through `less`. If a *piped* run looks truncated, check for an exported `COLUMNS` — `ui.auto_width()` honours it ahead of the isatty check |
| BAY numbers wrong | edit `bay_map.json` (reverse rule or explicit map); recalibrate with `b2ctl locate <serial>` |
| BAY all `-` on a SAS box with no vendor tool | the kernel fallback (§3.3a) needs `/sys/class/sas_device/end_device-*` — confirm `mpt3sas`/`mpt2sas` is loaded and that `bay_identifier` is readable and not the **same** value for every drive (a constant is rejected as a useless map). SATA-only and NVMe boxes have no SAS transport at all: bays there come from the vendor map / the `type:nvme` panel |
| bays read `0:0 … 0:23` where perccli says `9:0 … 9:23` | the sysfs slots got the default enclosure prefix: the PD table named more than one enclosure, so `ITBackend.attach_bays()` refuses to guess (§3.3a step 3). Fix cosmetically with an explicit `map` in the front `type:sas` panel of `bay_map.json` |
| BAY mapping works in one directory but not another (raw BDF elsewhere) | pre-v0.8.5 `python -m` cwd-shadowing: running from the source checkout loaded that copy's `bay_map.json`. Fix: `sudo b2ctl update` (bind `/etc/b2ctl/bay_map.json` in config) and redeploy so the launcher has `PYTHONSAFEPATH=1` |
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
| `"it"` | `ITBackend()` — no subprocess run (the bay source can still flip, §9.2) |
| `"raid"` | `RaidBackend()` — no subprocess run |
| `"auto"` (default) | probe order below |

**Auto-detection probe order:**

1. `sas2ircu list` — a real controller table (`^\s*\d+\s+SAS`) → `ITBackend()`. (Merely non-empty stdout is not enough: sas2ircu on a RAID box prints its banner + `MPTLib2 Error 1`, which the pre-F-010 truthy test misread as IT.)
2. sas2ircu binary exists but produced no output at all (failed to execute) → warn on stderr ("apt-get install -y libc6-i386") and **force `ITBackend()`** (prevents false RAID detection on a crossflashed H710).
3. `perccli64 show ctrlcount` / `perccli show ctrlcount` → `Controller Count = N` with N > 0 (`hba_raid.have_tool()`, memoized) → ask **`hba_raid.is_hba_personality()`** (§9.1), because "perccli answers" is **not** the same as "the controller owns the disks":
   - `True` → `ITBackend(bay_source="perccli")` — a Dell HBA330/H330, or a PERC in HBA-Mode.
   - `False` → `RaidBackend()`.
4. None found → `die()` with an install hint.

(storcli is never probed — it was dropped because it responds to a crossflashed PERC and caused false RAID detection.)

`_backend_cache` stores the result; `setup_method` in tests clears it via
`bk_mod._backend_cache = None` to keep tests isolated.

Each backend's `name` attribute is `"it"` or `"raid"` and is used by
`b2ctl check` to report which backend was detected. `ITBackend(bay_source=
"perccli")` still reports `"it"`, so `raid_actions._require_raid()` refuses the
PERC lifecycle verbs on such a box — correct for a real HBA330 (there is no array
to manage), but see the caveat under §9.1.

### 9.1 Controller personality — `hba_raid.is_hba_personality()` (v0.19.0)

Answers one question: **does the controller own the storage, or does the OS?**
Treating "perccli replies" as "hardware RAID" is what turned 9 drives into 18 rows
on an HBA330 (F-133) — `hba_raid.enumerate_disks()` synthesised one `Disk` per
controller PD on top of the block devices lsblk already listed, and read each of
them through a megaraid passthrough that does not exist on mpt3sas.
`_probe_hba_personality()` walks four signals; the first rung that decides wins,
and every perccli read covers **all** `_ctrl_indices()`, never a hardcoded `/c0`:

| # | signal | command / read | verdict |
|---|--------|----------------|---------|
| 1 | virtual disks | `perccli /c<n>/vall show all` (every controller, `_vall_data()`) | any VD → **RAID**. Checked **first** so a host without `/sys` (dev laptop, sim harness) can never misclassify a real RAID controller |
| 2 | personality | `perccli /c<n> show` → `Current Personality = …` (or `Personality = …`) | starts with `RAID` → **RAID**; starts with `HBA` → **HBA**. **Authoritative in both directions**: a controller that names its own personality is believed and the probe stops there. A Dell HBA330 prints no personality line at all — it has no switch, it is IT firmware permanently — so `''` is a normal answer, not an error |
| 3 | kernel driver | `perccli /c<n> show` → `Driver Name = …`; if perccli names none anywhere, glob `/sys/class/scsi_host/host*/proc_name` and look for `megaraid_sas` | every named driver ≠ `megaraid_sas` (i.e. `mpt3sas` on an HBA330/HBA355) → **HBA**; no `Driver Name` **and** no `megaraid_sas` host in sysfs → **HBA**. No MegaRAID ioctl ⇒ `-d megaraid` is impossible ⇒ the controller cannot be driven as RAID |
| 4 | per-PD resolution | PDs + `SN =`/`WWN =` maps from `perccli /c<n>/eall/sall show all` vs `lsblk -dnb -P -o NAME,TYPE,SERIAL,WWN` (`blockdev.lsblk_pairs`, `TYPE=disk` minus `blockdev.EXCLUDE` = `loop/sr/ram/zd/dm-/md`) | no PDs at all → **RAID**; else **every** PD must resolve to an OS block device, by fuzzy serial (`baymap.serial_match`) or normalised WWN → **HBA**. A single unresolved PD ⇒ the controller is hiding that drive ⇒ **RAID** |

The verdict is memoized in `_hba_personality_cache` — perccli is slow (F-040) and
the probe costs one `vall` per controller, up to two `/c<n> show` calls per
controller (rungs 2 and 3 do not share the output), plus one `eall/sall` per
controller if it reaches rung 4. `hba_raid._reset_caches()` clears it together
with `_tool_cache`/`_have_tool_cache`; tests call it in `setup_method`.

> **Two rungs were rebuilt after the F-133 review** — the first cut read rung 2
> only *positively* (`startswith("HBA")`) and tie-broke on raw counts
> (`len(os_disks) >= len(pds)`), which locked an operator out of a real PERC. A
> freshly-wiped H730P in **RAID-Mode with no VD** (new box, or straight after
> `b2ctl raid-del`) fell past its own explicit `RAID-Mode` string into the tie-break,
> where an unrelated BOSS-S1 mirror + 2 NVMe — block devices that are not on the
> controller at all — outvoted the hidden PERC drives. `_detect_backend()` returned
> `ITBackend(bay_source="perccli")`, so `raid_actions._require_raid()` refused
> `raid-create`, `raid-del`, `raid-replace`, `raid-offline` **and** watch's
> `[a]ssign` → `assign_perc` menu (locate / set JBOD / create volume / hot spare)
> with "hardware-RAID actions require RAID mode (perccli). This box is IT/HBA" —
> precisely the actions needed in that state. Rung 2 now vetoes, and rung 4 counts
> only drives the controller itself reports.

> **Residual limitation — rung 4 reads a hidden drive as RAID, whatever hid it.**
> On a PERC in HBA-Mode whose firmware prints no personality string and binds
> `megaraid_sas`, a drive the OS genuinely rejected (foreign RAID metadata) leaves
> one PD unresolved, so the box is classified RAID. That is the safe direction —
> RAID mode still shows the drive — but if it is wrong for your box, set
> `controller.mode = "raid"` (or `"it"`) in `/etc/b2ctl/config.json`:
> `_detect_backend()` then returns the backend directly and never probes.

### 9.2 `ITBackend.bay_source` (v0.19.0)

`bay_source` selects only **who answers "which bay is this drive in"**. Disk
enumeration, SMART, ZFS lifecycle and LEDs are identical in both modes, because in
both the OS owns raw block devices:

| `bay_source` | chosen when | bay map from | enumeration + SMART |
|--------------|-------------|--------------|---------------------|
| `"sas2ircu"` (default) | `sas2ircu list` showed a controller table, or `controller.mode="it"` on a box where sas2ircu runs | `sas2ircu <c> DISPLAY` → `hba.bay_map()` | `lsblk` + direct `smartctl -a /dev/sdX` |
| `"perccli"` | auto-detect got `is_hba_personality() == True`, or `have_tool()` flipped it (below) | `perccli /c<n>/eall/sall show all` → `hba_raid.bay_map()` | identical — `lsblk` + direct `smartctl -a /dev/sdX`, **never** `-d megaraid` |

`ITBackend.have_tool()` (`backend.py:68-81`) flips `bay_source` to `"perccli"` on
the fly when `hba.have_sas2ircu()` is false but `hba_raid.have_tool()` is true.
That rescues an operator who forced `controller.mode = "it"` on an HBA330, who
would otherwise lose bays entirely; both probes are memoized, so it costs nothing
after the first scan. LEDs need no special-casing — every drive has its own block
device, so `locate.py`'s ledctl → dd path blinks the right bay.

**Neither source is required (v0.19.0, F-134).** Whatever the chosen `bay_source`
leaves unlabelled is filled from the kernel SAS transport class (§3.3a) — that
runs after `assign_bays()` in both rows above, so a vendor label always wins and no
existing box's numbers move. With `bay_source="perccli"` and an **empty** serial
map, `ITBackend.attach_bays()` also borrows the enclosure number from
`hba_raid.enclosure_ids()` (used only when the PD table names exactly one
enclosure) so the sysfs slots render as `9:0 … 9:23`, matching what
`perccli /c<n>/eall/sall show all` prints. `enclosure_ids()` never addresses
anything — perccli actions keep using `Disk.ctrl_slot`, the raw locator.

### 9.3 PD → OS block device join (`hba_raid.enumerate_disks()`, v0.19.0)

RAID mode still synthesises a `Disk` per **hidden** controller PD (`dev = ctrl_dev`,
`smart_dtype = "megaraid,<DID>"`), but a PD the OS already exposes (JBOD) must
**tag that block device** instead. The old test was `if sn and sn in raw_serials`,
with `raw_serials` coming from lsblk — it never fired when lsblk had no serial yet,
which is how one phantom row per drive appeared. `_match_os_disk(sn, wwn, by_sn,
by_wwn)` now joins in this order:

1. exact serial → 2. `baymap.serial_match()` fuzzy prefix → 3. normalised WWN
   (`_norm_wwn`, from `_parse_wwn_map`).

The VD's own block device (`perc_dev_set`) is excluded from every join table so it
cannot absorb a PD. Both maps are parsed from the SAME
`perccli /c<n>/eall/sall show all` text already fetched once per controller
(F-040/F-041): `_parse_detail()` binds `SN = …` / `WWN = …` to the nearest preceding
`Drive /cN/eE/sS` header, and `_DRIVE_HDR` now matches **any** such header —
requiring the literal `Device attributes` made every SN unreadable on an HBA330,
which is what left each PD looking "hidden" in the first place.

**Two passes, not one (F-133 review).** The non-member PD loop is split, because a
suppression decision needs the *complete* set of claims:

- **Pass 1** — join every non-member PD via `_match_os_disk()`. A hit tags the real
  block device (`bay`, `pd_state`, `ctrl_slot`, `ctrl`) and records `id(target)` in
  `claimed`. Nothing is synthesised and nothing is dropped in this pass.
- **Pass 2** — synthesise a `Disk` for everything left in `pending`. Only here does
  the model/size refusal apply, and only to a PD perccli could **not identify at
  all** (no `SN` *and* no `WWN`) — the HBA330 case this guard exists for. A PD that
  *has* an identity which simply matches no OS disk is definitively hidden behind
  the controller and always keeps its row.

The first cut decided mid-loop and fired for identified PDs too, which deleted real
Unconfigured-Good / Failed drives from `b2ctl status`, from `b2ctl check` and from
watch's `[a]ssign` PERC list (`raid_avail`, filtered on `smart_dtype` + `pd_state`)
— so set JBOD / add hot spare could not reach them. Because `claimed` was
incomplete while the loop ran, a drive's very existence depended on enc:slot
iteration order. The real layout that hit it is one b2ctl's own `assign_perc` set-JBOD
flow creates: one drive JBOD-exposed, an identical-model sibling still hidden.

The refusal itself was also narrowed, so it can only ever suppress *less*:

| guard | rule | why |
|-------|------|-----|
| `_model_match(pd_model, dev_model)` | prefix compare in **either** direction on `_norm_model` (upper, whitespace-collapsed), plus a `_MODEL_MIN = 8` floor on the shorter string | perccli truncates its Model column (`Samsung SSD 860` vs lsblk's `Samsung SSD 860 PRO 1TB`), so equality is wrong — but a bare prefix test made `("S", "Samsung SSD 870 EVO 1TB")` true, i.e. one severely truncated column suppressing arbitrary drives |
| `_size_match(pd_size, dev_bytes)` | `_pd_size_bytes()` parses perccli's size as **powers of 1024** and compares within **10 %** | perccli prints BINARY sizes under decimal labels: `953.869 GB` = 953.869 GiB for an 860 PRO 1TB (1 024 209 543 168 B), `2.182 TB` = 2.182 TiB for a 2 400 476 274 688 B SAS drive. The tolerance absorbs rounding/reserved areas while still separating a 960 GB SSD from a 2.4 TB HDD |

`_size_match` returns **True** when either side is unknown — an unparseable size
must never widen the suppression, only narrow it.

### 9.4 Foreign configs (`Disk.pd_foreign`, v0.20.0 / F-135, ADR-006)

perccli's PD table has two **independent** axes that b2ctl collapsed into one:

| column | question it answers | values |
|--------|---------------------|--------|
| `State` | is the drive in a VD? | `Onln` / `Rbld` / `UGood` / `JBOD` / `Failed` … |
| `DG`    | which drive group owns it? | a number, `-` (none), **`F` = foreign** |

A **foreign** drive carries RAID metadata written by another controller/array. It
still reports `State = UGood`, but the firmware refuses every transition on it —
`set jbod`, `add hotsparedrive`, `add vd` — with the generic
`ErrCd 255 Operation not allowed`.

`_parse_pd_rows()` had always captured `dg`, but `enumerate_disks()` copied only
`state` onto the `Disk`. The consequence chained all the way to the operator:
`common.assess()` graded the drive *"available (Unconfigured Good) — set JBOD for
ZFS"*, `watch._cmd_assign` listed it as assignable, `raid_actions.assign_perc`
offered `[2] set JBOD`, and the refusal was printed as a raw vendor dump with no
interpretation. b2ctl advertised a drive the controller considers locked.

**Propagation.** `hba_raid._is_foreign(row)` (`dg.strip().upper() == "F"`) is the
one authority, applied at all three sites that already copy `pd_state`: the VD
member loop, the PASS 1 OS-exposed tagger, and the PASS 2 synthesiser. Zero extra
subprocesses — `dg` is in text already fetched.

**Probes** (all read-only, `run()` not `run_check()`, and never called from
`core.scan()` — perccli is slow enough that its probes are memoised, F-040/F-041):

| function | command | note |
|----------|---------|------|
| `foreign_config(c)` | `perccli /cN/fall show` | one row per foreign **drive group** (see below). Keyed on rows inside the `FOREIGN CONFIGURATION` section, never the Status line: several builds answer "no foreign configuration present" with `Status = Failure` |
| `foreign_bays(c)` | `perccli /cN/eall/sall show all` | enc:slots of every PD flagged `DG = F`. **Not** from `fall` — see the drive-group note |
| `jbod_capability(c)` | `perccli /cN show all` | `Support JBOD = Yes\|No` + `JBOD = ON\|OFF`. `None` = not printed (an HBA330 prints neither). **Reported only** — b2ctl never runs `set jbod=on` |
| `explain_error(out, d=, controller=)` | — | matches `operation not allowed` / `errcd 255`, then prints the checked causes in the order they bite, marking the first hit `<-- this` |

**Actions.** `import_foreign()` → `/cN/fall import`; `clear_foreign()` →
`/cN/fall del`. Both via `build_cmd()` + `run_check()` so ops.jsonl records the
real argv (F-089); `perccli`/`perccli64` were already in `safety.WRITE_CMDS`, so
`--dry-run` gates them with no change there.

**Scope.** MegaRAID exposes **no per-drive** import or clear — `/cN/fall` is the
only selector, so a clear discards *every* foreign config on that controller. This
is b2ctl's first action whose blast radius exceeds the target the operator picked,
hence ADR-006: print the full affected set first, confirm at **controller** scope,
and require a type-the-controller-number second confirm. `raid_actions._run_foreign()`
is the single implementation, shared by watch's `[5]` and the CLI verb so the
guards cannot drift apart.

**A foreign config is a DRIVE GROUP, not a drive (v0.21.1 / F-138).** This is the
single most misleading thing about the `fall` output. Real hardware
(`cmp01`, H730P Mini):

```
DG EID:Slot Type   State     Size NoVDs
 0 -        RAID10 Frgn  3.491 TB     1        <-- EID:Slot is '-'
Total foreign drive groups = 1
```

The group is a 2-drive RAID10 (3.491 TB = 2 × 1.745 TB) with only one member
present, so there is no single slot to name. The first cut of `foreign_config()`
located rows by matching an `enc:slot` token, parsed **zero** rows here, and the
`[5]` menu answered "no foreign configuration" while the drive stayed locked.

Consequences baked into the design:

- `foreign_config()` anchors on the `FOREIGN CONFIGURATION` header, then takes
  rows whose first token is a DG number **and** which name a `RAID*` type. That
  rejects the column header, the `NoVDs - …|DG - Diskgroup` legend and
  `Total foreign drive groups = 1`. `bay` is `''` for a spanning group; the older
  single-drive shape still fills it.
- `foreign_bays()` reads the **PD table** (`DG = F`), the only place a slot is
  always named.
- `_foreign_menu()` / `foreign()` gate on **either** source. A build whose `fall`
  table we cannot parse still reaches import/clear, driven by the PD flags —
  never a dead end while a PD is flagged `F`.
- Confirms count **drive groups**, not drives, and list the affected bays.
- The sim emits this shape verbatim. It previously printed an invented
  single-drive table, which is exactly why the sim passed while hardware failed.

**Refusal is pre-flight and all-or-nothing.** `_refuse_foreign(targets, what)`
rejects the *whole* selection if any pick is foreign, before perccli is called. A
partial batch reporting "2 ok / 1 failed" reproduces exactly the ambiguity this
fixes.

**Root gating.** `raid-foreign` joins `_ROOT_EXEMPT` with the same shape as
`maint`: the bare form is a read-only `fall show` (§9 read path), while
`--import`/`--clear` require root.

### 9.5 VD → block device, and what `Disk.dev` means (v0.21.0 / F-136)

`Disk.dev` used to carry two meanings at once — the device node to *display*, and
the file `smartctl -d megaraid,<DID>` opens. For a PD behind a virtual disk only
the second exists, and `enumerate_disks` resolved it **once**:

```python
ctrl_dev = perc_devs[0].dev          # first PERC block device found
...
d = Disk(dev=ctrl_dev)               # ...stamped on every member of every VD
```

On a two-volume box that printed the same `/dev/sdq` on every hardware row and —
worse — made `core.assemble_storage` resolve both volumes to one block device, so
`vd_usage()` measured one filesystem twice and both volumes reported identical
`USED`/`FREE`.

**The split.** `Disk.dev` is now the OS device node or `"-"` when there is none;
`Disk.ctrl_dev` is the megaraid ioctl handle. Consumers follow the meaning they
actually want:

| consumer | field | why |
|---|---|---|
| `ui` DEV column | `dev` | `-` is the truth for a hidden PD; identify it by BAY |
| `smart.read()` | `ctrl_dev` when `smart_dtype` is set | passing `dev` would hand smartctl a literal `-` |
| `core.assemble_storage()` | `ctrl_dev` | per-volume, so each VD measures its own filesystem |
| `locate.blink_disk()` | neither | PERC PDs return via the perccli/enc:slot path first |
| `cli._status --locate` | `dev` **or** `is_perc_pd(d)` | the ghost filter (`dev not in ('-','')`) would otherwise skip every failing hardware member |

**The join** (`_vd_dev_map`, keyed `"<controller>:<vd>"` because two controllers
can each own a `v0`), in order of certainty:

1. **`SCSI NAA Id` ↔ lsblk `WWN`**, both through `_norm_wwn` — exact. The NAA is
   parsed out of the `VDn Properties` block of `perccli /cN/vall show all`, text
   `_vall_data()` already fetches, so this costs no extra subprocess.
2. **Size** — `_pd_size_bytes` + `_size_match` (v0.19). Note the guard: those
   answer *True when either side is unknown*, which is right where they narrow
   F-133's suppression but wrong here, where an unparseable size would let a VD
   claim the first free device at random. `_vd_dev_map` therefore requires **both**
   sizes to be known before trusting a size match.
3. **Nothing** — the caller keeps the controller-wide `ctrl_dev`, so a single-VD
   box behaves exactly as before.

A device is claimed at most once, so two same-size volumes cannot both grab it.

### 9.6 Terminal-aware table (v0.21.0 / F-137)

`ui.TABLE_W` was a hand-maintained `196` and nothing in b2ctl had ever called
`get_terminal_size()` or `isatty()`. A 24-disk box overflowed both axes.

- **One column spec.** `ui._COLUMNS` is a list of
  `(key, header, width, render, drop_rank)`. The header and the row are generated
  from the same list, replacing two independent format strings that had to agree
  on fifteen widths by hand. `TABLE_W` is now `sum(width)`.
- **`render` returns the finished cell**, already padded to `width` *visible*
  chars. That is deliberate: `_status_cell` / `_health_chk_cell` / `color_level`
  embed ANSI escapes, so the layout engine must never `len()` a rendered cell.
- **`drop_rank`** — `0` = never dropped (BAY, MODEL, SERIAL, HEALTH, POOL/ARRAY,
  LEVEL: which disk, and is it OK). Others shed in ascending order:
  WRITTEN → POWER_ON → END(left) → WEAR(used) → HEALTH_CHK → IF → STATUS → BAD →
  DEV. A very narrow terminal overflows slightly rather than losing identity.
- **`ui.auto_width()`** returns `$COLUMNS`, else the terminal width, else **None
  when stdout is not a tty**. That last guard matters: `get_terminal_size()`
  answers its `(80, 24)` fallback for a pipe, so honouring it would silently
  reshape `b2ctl status > report.txt`.
- **`render_table(disks, max_width=None)`** stays pure — `None` = unlimited.
  Callers decide; `--full` is a caller-side choice.
- **`cli._page()`** pipes to `$PAGER`, else `less -SRFX`, only when stdout is a
  tty *and* the output is taller than the screen. `-S` chops long lines so the
  wide table scrolls sideways instead of wrapping; `-R` keeps the level colours;
  `-F` quits if it fits; `-X` leaves the output on screen. A missing or
  unspawnable pager falls back to `print` — output is never lost.
- **`watch` never pages.** It owns the terminal for its `select()` hotplug loop;
  handing that to `less` would freeze the poll. It gets column fitting only.

---

## 9.7 The machine contract — `--json` (v0.22.0 / ADR-007)

b2ctl is driven by an MCP server and a web UI as well as by an operator, so every
read verb emits a versioned envelope and every failure is data.

### The envelope

```json
{ "schema_version": 1, "ok": true,  "command": "status",
  "data": {...}, "warnings": [], "error": null }

{ "schema_version": 1, "ok": false, "command": "status",
  "data": null, "warnings": [],
  "error": { "code": "INVALID_ARG", "message": "--locate ... cannot be combined with --json" } }
```

The key set never varies by outcome. `b2ctl/jsonout.py` is the only thing that
builds one: `emit(command, data, warnings=)` → rc 0, `fail(command, code,
message, data=)` → rc 1. Exit codes stay 0/1; the envelope carries the detail.

Clients branch on `error.code` — a closed set (`NO_BACKEND`, `TOOL_MISSING`,
`POOL_NOT_FOUND`, `DISK_NOT_FOUND`, `NEEDS_ROOT`, `INVALID_ARG`, `PARSE_ERROR`,
`UNSUPPORTED`) — **never** on `message`, which is free to be reworded.

`schema_version` bumps only on a break: adding a key keeps the version, removing
or renaming one bumps it.

### `--json` is global, and two argparse traps

`--json` sits beside `--dry-run` on the top-level parser, and `_add_json_flag()`
walks every subparser (recursively, so `maint scrub` and `config show` get it
too) adding a copy.

Both copies use **`default=argparse.SUPPRESS`**, and that is load-bearing: a
subparser's ordinary default OVERWRITES whatever the top-level flag already set,
so `b2ctl --json status` would silently parse as `json=False`. With SUPPRESS the
subcommand only sets the attribute when the flag is actually typed, and both
`b2ctl --json <verb>` and `b2ctl <verb> --json` work. `status` declares its own
`--json` inside the F-069 mutex group and needed the same treatment.

Second trap: an argparse mutually exclusive group **cannot span parsers**, so the
F-069 rule ("`--locate` is a physical side effect, `--json` is machine output —
never together") is silently bypassed by `b2ctl --json status --locate`. `_status`
re-checks it at runtime and returns `INVALID_ARG`.

Bare `b2ctl` re-parses as `status`, which REPLACES the namespace — `main()`
carries `--json` across by hand.

### `--json` implies total stdout silence

The envelope must be the only thing on stdout. The read path was audited:
`baymap.py` (×2) and `spec.py` (×1) wrote notices there. They now call
`common.warn()`, which prints exactly as before in terminal mode and appends to
`warnings[]` in JSON mode (ANSI stripped, deduplicated). `take_warnings()` is
drained by `jsonout.emit`/`fail`, so a handler never has to remember to collect.

`safety.py`'s ten stdout writes are on the mutation path — untouched here.

### Projections, never `vars()`

`b2ctl/schema.py` builds each dict from a named tuple of fields
(`DISK_FIELDS`/`POOL_FIELDS`/`VOLUME_FIELDS`). Publishing a field is a decision;
a new internal field on `Disk` can no longer reach the wire by accident.
Deliberately excluded: `pool_token`, `selftest_running`/`_pct`/`_eta`,
`spare_replacing`, `smart_dtype`, `ctrl`, `lba_written`.

`backend_json()` must never raise or block. Note it catches
`(Exception, SystemExit)`: `backend.get_backend()` calls `common.die()` →
`sys.exit()` when no HBA/RAID tool exists at all, and **`SystemExit` is not an
`Exception` subclass**.

### Read verbs

| verb | `data` keys |
|---|---|
| `status` | `backend, disks, pools, volumes, summary` |
| `disks` | `disks` (SMART scan) |
| `pools` | `pools` (no SMART — cheap to poll) |
| `volumes` | `volumes` (`[]` in IT mode) |
| `check` | `root, backend, tools` |
| `log` | `entries` (ops.jsonl) |
| `maint --log` | `events` (maint.jsonl) |
| `raid-foreign` | `controller, groups, bays` |
| `config show` | `config, paths` |
| `version` | `version, schema_version` |

`pools` merges `zfs.pool_level()` and `core.pool_maint()` into each row —
`zfs.list_pools()` alone carries neither, so without the merge every pool would
report `level: null`.

**Mutating verbs still prompt** and are not part of the contract yet; an MCP
server must not call them until phase 2 (v0.23.0). `raid-foreign --import/--clear`
with `--json` returns `UNSUPPORTED` rather than hanging on a confirm.

## 9.8 Machine-callable mutations — `--confirm` (v0.23.0 / ADR-007 phase 2)

### One gate, not ninety-two edits

`watch.py` contains **exactly one `input()`**, inside `_ask()`; its 38 `_ask`, 28
`_confirm` and 5 `_confirm_op` sites all funnel through it. `raid_actions.py` had
10 direct calls. So the whole prompt surface is served by three functions in
`common.py`:

| function | interactive | `--confirm` |
|---|---|---|
| `confirm(msg)` | `[y/N]` as today | returns True |
| `ask(prompt, default=, hint=)` | prompts | returns `default`, else raises `NonInteractive` |
| `confirm_target(prompt, target)` | must type `target` | `yes` passes; `<value>` passes only if `== target` |

`--confirm` is global, beside `--json` and `--dry-run`. `common.AUTO_CONFIRM`
holds its value; `is_non_interactive()` is the switch.

**An unanswered prompt is an error, never a guess.** `NonInteractive` carries the
prompt text and a `hint`; `cli.main` converts it to an `INVALID_ARG` envelope
naming the argument to supply. Defaulting "which disk?" on a destructive path is
how you destroy the wrong one, so only prompts that already document a default
(`[raid1]`, "blank = global spare") get one.

Two call sites deserve their own note:

- `raid_actions`'s *"press Enter once the new drive is inserted"* is a **wait, not
  a question**. It keeps its raw `input()` and its own
  `except (EOFError, KeyboardInterrupt)` — routing it through `common.ask` would
  swallow the Ctrl-C that F-090 relies on to abort cleanly. It is simply skipped
  when non-interactive.
- `_confirm_op` still prints its box, then returns True without prompting.

### Target selection

`watch._cmd_destroy(tbw, target=None)` was already the pattern: `None` prompts,
a value skips the prompt, an unmatched value errors. `_cmd_offload`/`_cmd_replace`/
`_cmd_swap`/`_cmd_demote` gained the same `target=`, and `_cmd_create` gained
`disks=`/`name=`. Resolution matches on bay / serial / dev / by-id and **refuses
an ambiguous match** rather than picking one. `zfs_actions` exposes them as
keyword arguments; the CLI surfaces `--disk` and `--disks/--type/--name`.

### Mutating verbs under `--json`

Mutating commands narrate as they work — confirm boxes, resilver bars, per-step
results — all to stdout, which would shred the envelope. Read verbs build their
own and are tagged `emits_json=True` in `set_defaults`; everything else runs
inside `cli._json_mutation`, which captures stdout and returns it as `data.log`,
with `OP_FAILED` when the command did not complete. That avoided rewriting several
hundred `print()` calls. `safety.py`'s ten writes moved behind `common.warn()`.

Watch out: `maint`'s subcommands inherit the parent parser's defaults, so
`m_scr`/`m_trm`/`m_hl` set `emits_json=False` explicitly — otherwise `maint scrub`
would inherit `maint --log`'s read-verb tag and never get wrapped.

`watch` itself returns `UNSUPPORTED` under `--json`.

### Long operations — `b2ctl progress`

Mutating verbs return when the operation is *started*; `progress` polls. Pure
read (§9) of state the kernel and controller already publish:
`zfs.poll_scrub_status`, `zfs.poll_trim_status`, `hba_raid.rebuild_progress`, and
burn-in's `burnin.json` via `load_state()` + `selftest_status()`.

`poll_scrub_status` gained an explicit **`in_progress`** key. It is NOT the
inverse of `completed`: the `scrub repaired …` line persists until the next scrub,
so a pool that has *never* been scrubbed also reports `completed=False`.
`progress` announced a phantom scrub on every such pool until the positive signal
existed.

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

### SMART scan tuning — `config.smart_config()` (v0.11.1)

`config['smart']` = `{"timeout": <sec>, "megaraid_workers": <n>}` (defaults
`10` / `4`). `timeout` is the per-probe `smartctl` timeout; `megaraid_workers`
caps concurrent megaraid passthrough probes (one PERC serializes them — see §3.4).
Int-guarded per key: a non-int / non-positive / bool hand-edit is ignored and the
default kept. Tune on a box whose SAS disks read slowly or intermittently `NOREAD`:

```json
{ "smart": { "timeout": 25, "megaraid_workers": 2 } }
```

### Health thresholds — `config.health_config()` (v0.13.0)

`config['health']` is split by disk type — `ssd` (SSD **and** NVMe, `Disk.is_ssd`)
vs `hdd` — and read by `common.assess()` (table LEVEL) and `burnin.assess()` (POH).
**A threshold of `null` / `"N/A"` / any non-integer DISABLES that check**
(`_norm_threshold`); omitting a key keeps its default. Defect signals
(`realloc`/`pending`/`uncorr`) grade with `>` (`_grade_high`); endurance/wear grade
with `<` (`_grade_low`, % remaining). Defaults:

| signal | SSD / NVMe | HDD |
|--------|-----------|-----|
| `realloc_warn` / `realloc_crit` | `null` / `0` (any → CRITICAL) | `50` / `200` |
| `pending_warn` / `pending_crit` | `null` / `0` (any → CRITICAL) | `0` / `null` (→ WARNING) |
| `uncorr_warn` / `uncorr_crit` | `null` / `0` (any → CRITICAL) | `null` / `0` (any → CRITICAL) |
| `cmdto_warn` / `cmdto_crit` (v0.23.1) | `0` / `null` (any → WARNING) | `0` / `null` (any → WARNING) |
| `endurance_warn` / `endurance_crit` | `30` / `20` | `null` / `null` |
| `wear_warn` / `wear_crit` | `30` / `20` | `null` / `null` |
| `poh_warn` (burn-in) | `null` (off) | `null` (off) |

**`uncorr` vs `cmdto` — why one is fatal and the other is not (F-141).**
`uncorr` is zero-tolerance on *both* types because an uncorrectable read means
the drive tried ECC, retried, and gave up: that data is already lost. `realloc`
gets HDD tolerance bands precisely because a *successfully* remapped sector is
normal wear; an uncorrectable is not.

`cmd_timeout` (ATA attribute **188 Command_Timeout**) is a different failure
domain entirely — the command did not return in time, which points at the cable,
backplane, expander, power or controller, not the platter. It used to be folded
into `uncorr` alongside 187/198 (`smart.py`), so a cabling fault was reported as
`uncorrectable errors=N` at CRITICAL and sent operators to order a replacement
disk. It is now its own signal, graded WARNING, with a reason that names the
likely cause.

The split is **ATA-only**: SAS reads column 7 of the error-counter log
(`Total uncorrected errors`) and NVMe reads `Media and Data Integrity Errors` —
both genuine uncorrectables, both still feeding `uncorr`, with `cmd_timeout`
left at 0. Some vendors pack three counters into attribute 188's raw value, so it
can read large; harmless, since it only warns.

`Disk.cmd_timeout` is on the machine-contract wire. Adding it kept
`schema_version` at 1, per ADR-007's rule that additions are backward compatible.

Example — loosen HDD grading, tighten SSD endurance, enable the burn-in POH warn:

```json
{ "health": {
    "hdd": { "realloc_warn": 100, "realloc_crit": 500 },
    "ssd": { "endurance_crit": 25, "poh_warn": 40000 } } }
```

### Per-pool maintenance settings — `pools` / `pool_defaults` (v0.17.0)

Two new config sections record the create-time maintenance intent:

```json
{ "pools":         { "tank": { "autotrim": "off", "autoscrub": false } },
  "pool_defaults": { "autotrim": "off", "autoscrub": false } }
```

- `pools.<name>` — per-pool `{autotrim, autoscrub}`, written by
  `config.set_pool_settings(name, *, autotrim, autoscrub)` on a successful create,
  dropped by `config.remove_pool_settings(name)` on destroy. Read via
  `config.pool_settings(name)`.
- `pool_defaults` — sticky `{autotrim, autoscrub}` that pre-fills the next create's
  prompts; `config.pool_defaults()` reads it (defaults `autotrim="off"`,
  `autoscrub=False`), `config.set_pool_defaults(*, autotrim, autoscrub)` updates it
  after each create. **This is the only source of the autoscrub default-OFF** — there
  is no `AUTOSCRUB_DEFAULT` constant in code.
- Both are **shape-guarded** in `load()` (a non-dict `pools`, or a per-pool value
  that isn't a dict, falls back to `{}` for that entry — the module's
  malformed→defaults contract).

Every single-setting writer (`set_mode`, `set_pool_settings`,
`set_pool_defaults`, `remove_pool_settings`) now shares `config._load_for_write()`
(reads the file preserving **all** existing keys; **raises** on an unparseable /
non-object file rather than clobbering it — F-075) and `config._atomic_write(data)`
(tmp-in-same-dir + `os.replace`, so a crash/ENOSPC can't leave a truncated config
read as all-defaults), then clears `_cache`.

### Subprocesses added for RAID-mode (new in v0.5.0)

| command | purpose |
|---------|---------|
| `perccli64 /c<n>/eall/sall show all` | enumerate all drives and their EID:Slot for the bay map (also works with `perccli`); since v0.19.0 the same text also yields the `WWN =` map, the enclosure numbers `hba_raid.enclosure_ids()` lends to a sysfs-derived bay (§3.3a), and is the bay source for `ITBackend(bay_source="perccli")` |
| `perccli64 /c<n>/e<enc>/s<slot> set locate start` | turn on locate LED for one drive slot |
| `perccli64 /c<n>/e<enc>/s<slot> set locate stop` | turn off locate LED for one drive slot |
| `perccli64 show ctrlcount` | probe for RAID controller presence (also used in auto-detection) |
| `perccli64 /c<n> show` | **v0.19.0** — controller personality (`Current Personality`) + `Driver Name`, rungs 2–3 of the HBA-vs-RAID decision (§9.1); run per controller in `_ctrl_indices()`, once per rung |
| `perccli64 /c<n>/fall show` | **v0.20.0** — foreign-configuration listing (§9.4). Read-only; runs only on the assign pre-flight / error path, never in `core.scan()` |
| `perccli64 /c<n> show all` | **v0.20.0** — `Support JBOD` / `JBOD` policy, for diagnosing `Operation not allowed` (§9.4). Read-only; b2ctl never writes this policy |
| `sas2ircu list` | probe for IT/HBA controller presence (existing; now also used in auto-detection) |

Two **non-subprocess** system reads join them (v0.19.0) — pure `glob` + `open()`,
no fork, no tool required:

| read | function | used for |
|------|----------|----------|
| `/sys/class/scsi_host/host*/proc_name` | `hba_raid._megaraid_driver_present()` | looks for `megaraid_sas`; the rung-3 fallback when perccli prints no `Driver Name` (§9.1). Reads `False` wherever `/sys` is absent (dev laptop, sim harness) — which is why the VD check runs first |
| `/sys/class/sas_device/end_device-*/bay_identifier` + `…/device/target*/*/block/*` | `blockdev.sas_bay_slots()` | the kernel bay→device map that fills any bay the vendor map left empty (§3.3a). Costs nothing per scan and needs neither sas2ircu nor perccli |

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

### 11a. v0.9.0 audit deltas (Fable5 review) — for maintainers

Structural/behavioral changes from resolving `reviews/REVIEW_FABLE_001.md`
(see `docs/adr/ADR-001` and `prompts/FIX_fable5_audit.md`):

- **Version** lives in `b2ctl/_version.py` (not `cli.py`) — importing the version
  no longer loads the whole app graph. Bump it there.
- **Lifecycle CLI subcommands are now scriptable:** `offload/replace/create/
  destroy/swap/demote` return a real exit code (`0` = op completed, `1` =
  cancelled/failed) via the new public `zfs_actions` module — they no longer
  always exit 0. cron/scripts can gate on `$?`.
- **Audit log is append-only:** `/var/log/b2ctl/ops.jsonl` gets one *begin* line
  and one *end* line per op; `b2ctl log`/`rollback` merge them by `op_id`
  (last-record-wins). A crash mid-op can no longer truncate history, and a
  full/read-only `/var` still yields a result + post-op check (in-memory
  fallback). Rollback hints are built from recorded `old_dev`/`new_dev`, not
  positional argv indices.
- **PERC actions target the member's controller** (`Disk.ctrl` → `/c<ctrl>`),
  and the audited command equals what runs (`hba_raid.build_cmd`).
- **New shared modules:** `blockdev.py` (lsblk listing/`vd_usage`, moved out of
  `hba`), `zfs_actions.py` (public ZFS-lifecycle contract), `_version.py`. Read
  path stays side-effect-free via `core.scan_light`/targeted `scan_one`.
- **locate syntax** is `b2ctl locate <bay|serial|dev> [secs]` — a timed blink,
  always left off; there is no latched `on`/`off` verb (§9).

---

## 12. Simulation harness (`codes/sim/`)

b2ctl talks to hardware **only** through `run()`/`run_check()` (subprocess). That
seam lets you run the *real, unmodified* b2ctl against a simulated 8-disk server
(6 SATA/SAS + 2 NVMe) on a laptop — no hardware, SSH, or root. `sim/bin/` holds
fake `zpool`/`lsblk`/`sas2ircu`/`perccli`/`smartctl`/… that read and mutate
`sim/state.json`; `sim/run` is a launcher that sets `PATH`, points `B2CTL_STATE`
at the state, fakes root (`os.geteuid → 0`), uses an identity bay map, selects
the backend from `state.mode`, and redirects the audit trail to `sim/var/`.

Since v0.9.0 the harness models **both backends and the full lifecycle**: RAID
mode presents a synthetic PERC vd0 (perccli VD/PD/rebuild tables + `smartctl -d
megaraid` passthrough, front drives hidden behind the VD); resilver progress is
**time-based** (`zpool status` reads are side-effect-free — set
`B2CTL_SIM_RESILVER_SECS` to slow it down for stepping through Task-B), a replace
creates a real `replacing-N`/`spare-N` intermediate vdev until detach/completion,
and `offline`/`online` change pool state. `sim/state.json` writes are atomic
(tmp + `os.replace`); a corrupt state file fails loudly instead of silently
resetting.

```bash
cd codes
python3 sim/simctl init           # default: rpool mirror + tank raidz1 + 1 spare
python3 sim/run status            # real b2ctl, fake disks
python3 sim/run watch             # swap/replace/offload/create — state.json mutates
python3 sim/simctl pull 1:5       # remove a disk (spare auto-resilvers if present)
python3 sim/simctl insert 1:5     # re-insert → watch sees NEW DISK DETECTED
python3 sim/simctl dirty 1:5      # mark old data/labels (create wipe-warning path)
python3 sim/simctl foreign 1:7    # give a PERC drive a FOREIGN config (RAID mode)
python3 sim/simctl mode it|raid   # switch backend (sas2ircu ↔ perccli)
python3 sim/simctl show           # disks + pools + mode
```

| aspect | note |
|--------|------|
| backends | both — `simctl mode it` (sas2ircu) / `mode raid` (perccli) |
| audit isolation | sim writes `sim/var/ops.jsonl` + `sim/var/snapshots/`, **never** `/var/log/b2ctl/` → impossible to confuse with real ops; `b2ctl log`/`rollback` work in the sim |
| two virtual disks (v0.21.0) | the fake perccli builds **vd0 + vd1** (`_simstate.RAID_VDS`), each with a `SCSI NAA Id`, a matching lsblk `WWN`, its own byte size and its own mounted filesystem. With a single VD the F-136 bug is invisible — every member resolved to the same device and nothing looked wrong; two volumes with different `USED`/`FREE` are what proves the fix |
| failure paths (v0.20.0) | `simctl foreign <bay>` is the fake controller's **first modelled refusal**: the PD row gets `DG=F`, `/cN/fall show` lists it, and `set jbod` on that slot returns the real `ErrCd 255 Operation not allowed` text with exit 1 instead of the blanket success. `/cN/fall import\|del` clears the flag, controller-wide |
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
| personality (v0.19.0) | `perccli /cN show` (every controller) | `Current Personality = RAID-Mode\|HBA-Mode` — believed in **both** directions, `RAID-Mode` alone keeps the box on this backend; else `Driver Name = megaraid_sas\|mpt3sas`. The §9.1 gate that decides RAID vs IT before this table is used at all |
| members | `perccli /cN/vall show all` | VD row (raid/state/size/name) + `PDs for VD n` (EID:Slt, DID, State, Med, Model) |
| bay→serial | `perccli /cN/eall/sall show all` | **any** `Drive /cN/eE/sS` header + `SN =` (v0.19.0: the header no longer has to say `Device attributes`) |
| bay→WWN (v0.19.0) | same text, no extra command | `WWN =` per drive → the serial-independent PD↔block-device join (§9.3) |
| foreign flag (v0.20.0) | same text, no extra command | the PD row's **DG** column: `F` → `Disk.pd_foreign` (§9.4) |
| VD → block device (v0.21.0) | `perccli /cN/vall show all`, already fetched | `SCSI NAA Id` per VD → joined to lsblk `WWN` so each volume gets its own `Disk.ctrl_dev` (§9.5) |
| member SMART | `smartctl -a -d megaraid,<DID> /dev/sda` | ATA attrs (POH, LBAs written, wear), `test result: PASSED` |
| VD block dev | `lsblk -dnb -P` MODEL contains `PERC` | which `/dev/sdX` is the virtual disk (dropped from rows, and excluded from every join table) |

Actions (each `[y/N]`-guarded + audited via `safety.begin_op/end_op`):

| op | command |
|----|---------|
| locate | `perccli /cN/eE/sS start|stop locate` (verb first) |
| offline / missing | `perccli /cN/eE/sS set offline` → `set missing` |
| rebuild | `perccli /cN/eE/sS start rebuild`; progress `… show rebuild` (`NN%`) |
| set JBOD | `perccli /cN/eE/sS set jbod` — refused on a foreign drive, see §9.4 |
| hot spare | `perccli /cN/eE/sS add hotsparedrive [DGs=n]` |
| create VD | `perccli /cN add vd type=raidL drives=e:s,e:s` |
| delete VD | `perccli /cN/vV del force` |
| foreign show (v0.20.0) | `perccli /cN/fall show` — **read-only**, `run()` not `run_check()` |
| foreign import (v0.20.0) | `perccli /cN/fall import` — CONTROLLER-WIDE |
| foreign clear (v0.20.0) | `perccli /cN/fall del` — CONTROLLER-WIDE, destructive |
| JBOD policy (v0.20.0) | `perccli /cN show all` — read-only; `Support JBOD =`, `JBOD =`. b2ctl **never** runs `set jbod=on` |

> All perccli mutating actions honor `--dry-run` / the watch `[t]oggle` (preview
> the command, no mutation) — the `dry_run` flag is threaded `raid_actions` →
> `hba_raid.*` → `run_check`, same as the ZFS actions.
>
> Mutating ops + the rebuild-progress parser are **defensive** — validate on the
> R640. ADR: b2ctl is now **dual-backend** (IT/HBA via sas2ircu + ZFS; RAID via
> perccli + `smartctl -d megaraid`). On HW RAID the **controller** owns the
> array, so lifecycle is perccli-driven, not ZFS — that is why the old IT-only
> ban on `perccli`/`-d megaraid` was lifted.
>
> **v0.19.0:** answering perccli no longer implies RAID. A Dell HBA330/H330 (or a
> PERC in HBA-Mode) runs the **IT** backend with perccli as the bay source only
> (§9.1/§9.2) — none of the commands in this section apply there, and its disks are
> read with plain `smartctl -a /dev/sdX`, never `-d megaraid`.

### Install profiles

`b2ctl install --perc` → perccli + `controller.mode=raid`;
`b2ctl install --flash` → sas2ircu + `controller.mode=it`
(same flags on `./install.sh`). Binaries `cp -f` to `/usr/sbin` so they survive
deletion of `/opt/MegaRAID` or the download dir. `config.set_mode()` is the only
writer of `/etc/b2ctl/config.json`.

---

## ZFS pool lifecycle + maintenance timers

`create` (`[n]ew-pool` / `b2ctl create`) prompts an over-provision **size** (blank
= whole disk; see the over-provisioning note in §3.6), then each pool property with
an SSD-optimal default (`ashift=12`, `compression=lz4`, `atime=off`, `xattr=sa`,
`dnodesize=auto`, `acltype=posixacl`, `recordsize=128K`) and two independent
**autotrim** + **autoscrub** choices.

Maintenance is scheduled via the **distro systemd timer templates** shipped
(disabled) by `zfsutils-linux` — b2ctl enables one instance per pool (v0.16.0;
replaces the previous `/etc/cron.d/b2ctl-<pool>` writer).

**autoscrub is opt-in, default OFF (v0.17.0 — REVERSES v0.16.0's always-on scrub,
ADR-003).** SCRUB reads every allocated block, verifies checksums, and self-heals —
the actual bad-sector/bitrot defense — but the scrub timer now enables **only** when
the operator says yes at the create prompt (default off; seeded from
`config.pool_defaults()`, no in-code `AUTOSCRUB_DEFAULT`). When off, b2ctl prints a
`[!] autoscrub OFF …` warning and manual `b2ctl maint scrub`/`[m]aint` (§3.6b) is the
primary self-heal path.

**TRIM timer DROPPED — `autotrim off` is now MANUAL-ONLY (v0.18.0, ADR-004,
REVERSES v0.16.0/v0.17.0).** TRIM (tells the SSD which blocks are free) is now
symmetric with scrub: `autotrim off` installs **no timer** — the operator TRIMs via
`[m]aint` / `b2ctl maint trim <pool>`; `autotrim on` sets `zpool autotrim=on` (ZFS
trims inline). `create` therefore calls `install_pool_timers(name,
include_scrub=autoscrub_on, include_trim=False)` — **always `include_trim=False`**.
On create with autotrim off you see `[!] autotrim OFF — TRIM manually via
\`b2ctl maint trim <pool>\``.

- **autoscrub on** → `systemctl enable --now zfs-scrub-monthly@<pool>.timer`.
- **autoscrub off** → **no scrub timer** (manual scrub is primary).
- **autotrim on/off** → **never a trim timer** (`autotrim=on` = inline; `off` =
  manual). The only pool timer b2ctl installs on create is the scrub timer.

`enable --now` starts the *timer* (schedules the next `OnCalendar` run — appears in
`systemctl list-timers`); it does NOT kick off an immediate scrub.
`zfs.install_pool_timers(pool, *, include_scrub=True, include_trim=True,
dry_run=False)` still takes `include_trim=` for API completeness (and
`remove_pool_timers` still disables both kinds, so a pool created by an older b2ctl
with a trim timer is cleaned up on destroy), but **create never requests trim**. `ok`
reflects the **scrub** timer specifically, but when scrub was NOT requested there is
nothing to fail on, so `ok=True` — unlike v0.16.0 where a missing scrub timer was the
failure.

**No double-scrub with the Debian cron.** `zfsutils-linux` also ships
`/etc/cron.d/zfsutils-linux`, which scrubs/trims **every** online pool monthly,
gated by the per-pool user properties `org.debian:periodic-scrub` /
`org.debian:periodic-trim` (default `auto` = enabled). Left alone, that cron plus our
per-pool scrub timer would schedule the pool twice. So immediately **after** the
scrub timer enables, `install_pool_timers` runs `zpool set
org.debian:periodic-scrub=disable <pool>` — the distro all-pools cron then skips this
pool and the per-pool timer is the single schedule. (Since create no longer installs a
trim timer, the `periodic-trim` disable is not triggered on create — the Debian trim
cron, if enabled, still covers the pool.) This is best-effort (a failed `zpool set`
warns but does not flip `ok` — worst case is one extra scrub, never a gap), and needs
no restore on destroy (the property dies with the pool). `org.debian:*` is a plain
user property — settable and harmless even on a box where the Debian scripts aren't
installed.

**Template-missing → warn, no fallback.** A read-only probe
(`_timer_template_exists` → `systemctl list-unit-files zfs-<kind>-monthly@.timer`,
via `run()` so it's never dry-run-gated) checks the template exists first. If it
doesn't (non-standard ZFS build), b2ctl **warns and enables nothing** — the operator
must install `zfsutils-linux` or schedule manually; b2ctl does not fall back to cron.
`systemctl` is in `safety.WRITE_CMDS`, so `enable`/`disable` (through `run_check`) are
suppressed under `--dry-run`; the read probes use `run()` and still execute.

`destroy` (`[x]` / `b2ctl destroy <pool>`) runs `zpool destroy <pool>` behind a
double-confirm (must type the pool name; ALL-DATA-LOST warning; audited via
`safety.begin_op/end_op`), then `zfs.remove_pool_timers` best-effort `systemctl
disable --now` for the pool's scrub + trim timers, and `config.remove_pool_settings(
pool)` to drop the per-pool record from `/etc/b2ctl/config.json` (v0.17.0).

**Per-pool config (v0.17.0).** On a successful create, watch records the pool's
maintenance intent with `config.set_pool_settings(name, autotrim=…, autoscrub=…)`
(→ `pools.<name>` in `config.json`) and refreshes the sticky
`config.set_pool_defaults(…)` (→ `pool_defaults`, which pre-fills the next create's
prompts). See §10.

Pools destroyed **outside** b2ctl (manual `zpool destroy`) leave stale enabled
timers; `b2ctl watch` **prunes orphan timers** at startup
(`prune_orphan_timers` enumerates active `zfs-{scrub,trim}-monthly@*.timer`
instances via `systemctl list-units` and `disable --now`s those whose pool is absent
from `zpool list`; guarded so a transient `zpool list` failure disables nothing).

### bay_map.json (panel schema) + NVMe PCIe bay

`b2ctl.baymap` is the single parser/remapper (used by both `hba` and
`hba_raid`). `bay_map.json` is a **list of panels**:

- `type: sas` (front) — `enc:slot` remap via `reverse_slots`/`slots_per_enclosure`
  or an explicit `map` dict; from `sas2ircu DISPLAY` / `perccli … show all`, and
  since v0.19.0 also from `/sys/class/sas_device/…/bay_identifier` for any disk
  those two left without a bay (§3.3a — same panel, same remap, vendor wins).
- `type: nvme` (back, 1+) — `map: [{bdf, bay}]`; the raw bay is the PCIe BDF read
  from `/sys/class/nvme/<ctrl>/address` (domain stripped), set in
  `hba.enumerate_disks`.

The pre-0.8 flat dict format is no longer read (logged + ignored → identity).

### Spare-less offload (offline → degrade → replace in place)

`[o]ffload` on a pool member, when there is **no AVAIL spare**:

- `zfs.can_offline(pool, dev)` gate — the member's vdev must be redundant
  (raidz/mirror) and every OTHER member ONLINE. Refuses on a stripe/single or an
  already-degraded vdev (so a second offline can't fault the pool).
- `zpool offline <pool> <dev>` → pool **DEGRADED** (online, no redundancy); LED on.
- Operator pulls the bay, inserts a new disk in the SAME bay; b2ctl matches it by
  bay (`not in_pool`, `smart_dtype==""`) and runs `zpool replace -f <pool> <old>
  <new-by-id>` + resilver. Audited as `offline` then `replace`
  (`safety.begin_op/end_op`; rollback hint `offline`→`zpool online`).
