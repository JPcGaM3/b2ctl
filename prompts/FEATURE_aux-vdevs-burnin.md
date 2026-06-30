# FEATURE — ZFS aux vdevs (SLOG/L2ARC) + RAID10 create + disk burn-in

**Status:** DONE (v0.8.0-itmode). Mock + sim verified, 241 unit tests pass.

## Why

A new **R740XD 24-bay central ZFS/NFS storage** box is built from a runbook whose
pool layout is **RAID10 (stripe of mirrors) + SLOG (mirrored PLP SSD) + L2ARC
(NVMe cache)**, with a **second-hand-disk burn-in** gate (STEP 02) before disks
enter the pool. b2ctl previously emitted only single-vdev pools, had no
`zpool add cache|log`, and no SMART self-test trigger.

## Affected files

| file | change |
|------|--------|
| `b2ctl/zfs.py` | `add_cache`, `add_log` (≥2→mirror), `remove_vdev`; `create_pool` raid10 branch; `MIN_DISKS["raid10"]=4` |
| `b2ctl/burnin.py` | **new** — `start_selftest`, `selftest_status`, `read_scan`, `assess`, `_wait_selftest`, `run` |
| `b2ctl/cli.py` | version→`0.8.0`; `_resolve_devs`; handlers `_cache_add/_cache_rm/_log_add/_log_rm/_burnin`; `_create` `--raid10`; parsers `cache-add/cache-rm/log-add/log-rm/burnin`, `create --raid10` |
| `b2ctl/watch.py` | `_cmd_create(raid_type=None)` + raid10 prompt/guard; `_avail_for_aux`; `_cmd_extend`; `_cmd_burnin`; `_MENU` + dispatch `[e]`/`[b]` |
| `sim/bin/zpool` | `cmd_add` cache/log; `cmd_create` repeated `mirror` + skip `-o k=v`; `cmd_remove` aux; `cmd_destroy`; status renders log/cache |
| `sim/bin/smartctl` | `-t long\|short` start + self-test log line |
| `sim/bin/badblocks` | **new** fake (read-only, refuses `-w`) |
| tests | `test_zfs` (aux+raid10), `test_burnin` (new), `test_watch` (extend/burnin/raid10 guard), `test_cli` (parsers+dry-run) |

## Signatures

```python
# zfs.py
def add_cache(pool, devs, *, dry_run=False) -> (bool, str)         # zpool add -f <p> cache <devs>
def add_log(pool, devs, *, dry_run=False) -> (bool, str)          # ... log [mirror] <devs>
def remove_vdev(pool, dev, *, dry_run=False) -> (bool, str)       # zpool remove <p> <token>
def create_pool(name, raid_type, devs, *, pool_opts=None, fs_opts=None, dry_run=False)
    # raid_type=="raid10": even,>=4 -> mirror a b mirror c d ...

# burnin.py
def start_selftest(dev, kind="long", *, dry_run=False)
def selftest_status(dev, dtype="") -> {"running","pct","result"}
def read_scan(dev, *, dry_run=False)                              # badblocks -sv -b 4096 (NO -w)
def assess(disk) -> (verdict, reasons)                            # FAIL>WARN>PASS, POH_WARN=40000
def run(target, tbw_table=None, *, do_scan=False, kind="long", dry_run=False) -> int
```

## Guards / safety

- L2ARC cache: unguarded (loss = cache miss). SLOG: warn+confirm on single
  (non-mirrored) device; always remind PLP (not SMART-detectable → warning).
- Burn-in is read-only: only `smartctl -t` (self-test) + read-only `badblocks`;
  refuses disks already `in_pool`.
- All mutating wrappers thread `dry_run` → `run_check`; CLI reads `watch._DRY_RUN`
  (set from global `--dry-run`), watch reads it directly.
- by-id only (`_resolve_devs` maps bay/serial/dev → by_id; never `/dev/sdX`).

## Test plan (implemented)

- `create_pool(raid10, 4 devs)` → `… name mirror a b mirror c d`; odd/<4 → `(False,…)`.
- `add_cache`/`add_log`(1=no mirror, 2=mirror)/`remove_vdev` exact argv + dry_run.
- `selftest_status` parses ATA `% remaining` / SAS `% complete` / "Completed
  without error"; `assess` FAIL(uncorr/self-test), WARN(POH/defects), PASS;
  `read_scan` argv has no `-w`.
- watch `_cmd_extend` cache/log/remove dispatch; `_cmd_burnin` calls `burnin.run`;
  `_cmd_create(raid_type="raid10")` rejects odd count.
- cli parsers exist; `cache-add` honors dry-run; `create --raid10` flag parses.

## Sim walk (no hardware)

`sim/simctl init`; free a disk via `sim/bin/zpool remove tank sde`; then
`sim/run cache-add tank sde` / `cache-rm` / `burnin sde --short`;
`sim/run destroy tank` → `create --raid10` (state `type=raid10`, 4 members);
`--dry-run log-add` previews `zpool add -f tank log mirror …`. Restore with
`sim/simctl init`.

## Out of scope

Runbook STEP 01 (Proxmox install) and STEP 04 dataset/`arc_max` tuning — OS and
dataset config, not disk lifecycle.
