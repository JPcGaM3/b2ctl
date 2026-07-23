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
- Proxmox VE on ZFS-on-root (`rpool` mirror) + a data pool (`tank`, **raidz1** =
  RAID5, 3× Samsung 870 EVO 1TB + 1 hot spare). Pools created with
  `/dev/disk/by-id/ata-*` members, `ashift=12`, `compression=lz4`, `atime=off`,
  `xattr=sa`. (raidz1 resilver reads all surviving members and tolerates only
  ONE failed disk — slower/more stressful than a mirror resilver.)
- Python **stdlib only** (no pip deps). Runs as root.
- Required binaries: `smartctl`, `zpool`, `lsblk`. Optional: `sas2ircu`
  (bay numbers only), `ledmon`/`ledctl` (nicer locate LEDs), `wipefs`/`sgdisk`
  (wipe action). LED locate works without any of them via the dd fallback.

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

If `sas2ircu list` returns nothing the step is skipped and `bay` stays `None`.

**NVMe bays — `baymap.remap_nvme()`.** NVMe has no enc:slot; its raw bay is the
PCIe BDF (`hba._nvme_pcie()` reads `/sys/class/nvme/<ctrl>/address`, drops the
`0000:` domain). A back/`type:nvme` panel relabels it; each map entry keys on
`by-id` (substring of the drive's `/dev/disk/by-id/nvme-…` link), `serial`, or
`bdf`, matched in **precedence by-id > serial > bdf**. Remap runs even when the
BDF is unavailable, so a by-id/serial entry still labels the drive. `hba_raid`
reuses `hba.enumerate_disks`, so NVMe in RAID mode is covered with no extra code.

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
| BAY numbers wrong | edit `bay_map.json` (reverse rule or explicit map); recalibrate with `b2ctl locate <serial>` |
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
| `"it"` | `ITBackend()` — no subprocess run |
| `"raid"` | `RaidBackend()` — no subprocess run |
| `"auto"` (default) | probe order below |

**Auto-detection probe order:**

1. `sas2ircu list` — if stdout is non-empty → `ITBackend`.
2. sas2ircu binary exists but failed to execute → warn stderr ("apt-get install -y libc6-i386") and **force `ITBackend`** (prevents false RAID detection on crossflashed H710).
3. `perccli64 show ctrlcount` → non-empty → `RaidBackend`.
4. `perccli show ctrlcount` → non-empty → `RaidBackend`.
5. None found → `die()` with an install hint.

(storcli is never probed — it was dropped because it responds to a crossflashed PERC and caused false RAID detection.)

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
| `endurance_warn` / `endurance_crit` | `30` / `20` | `null` / `null` |
| `wear_warn` / `wear_crit` | `30` / `20` | `null` / `null` |
| `poh_warn` (burn-in) | `null` (off) | `null` (off) |

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
| `perccli64 /c<n>/eall/sall show all` | enumerate all drives and their EID:Slot for the bay map (also works with `perccli`) |
| `perccli64 /c<n>/e<enc>/s<slot> set locate start` | turn on locate LED for one drive slot |
| `perccli64 /c<n>/e<enc>/s<slot> set locate stop` | turn off locate LED for one drive slot |
| `perccli64 show ctrlcount` | probe for RAID controller presence (also used in auto-detection) |
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
python3 sim/simctl mode it|raid   # switch backend (sas2ircu ↔ perccli)
python3 sim/simctl show           # disks + pools + mode
```

| aspect | note |
|--------|------|
| backends | both — `simctl mode it` (sas2ircu) / `mode raid` (perccli) |
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
| locate | `perccli /cN/eE/sS start|stop locate` (verb first) |
| offline / missing | `perccli /cN/eE/sS set offline` → `set missing` |
| rebuild | `perccli /cN/eE/sS start rebuild`; progress `… show rebuild` (`NN%`) |
| create VD | `perccli /cN add vd type=raidL drives=e:s,e:s` |
| delete VD | `perccli /cN/vV del force` |

> All perccli mutating actions honor `--dry-run` / the watch `[t]oggle` (preview
> the command, no mutation) — the `dry_run` flag is threaded `raid_actions` →
> `hba_raid.*` → `run_check`, same as the ZFS actions.
>
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
  or an explicit `map` dict; from `sas2ircu DISPLAY` / `perccli … show all`.
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
