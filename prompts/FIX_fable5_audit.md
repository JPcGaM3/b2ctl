# FIX — Fable5 code-review audit (v0.9.0-itmode)

Blueprint for resolving **all 123 findings** of `reviews/REVIEW_FABLE_001.md`
(P0×9, P1×17, P2×39, P3×54, P4×4). Appendix B lists 4 **refuted** findings that
must NOT be "fixed". Version bumped `0.8.8-itmode → 0.9.0-itmode`.

## Ground rules honored

- Python **stdlib only**; `run()`/`run_check()` list-form (no `shell=True`);
  mutations act on **by-id**, never `/dev/sdX`; English code/messages.
- CLAUDE.md §9 safety preserved: read path side-effect-free; every mutating
  action explicit `[y/N]` naming device+pool+operation; wipe adds a serial
  warning; never light an LED on a resilvering/rebuilding disk; never
  auto-detach without confirmation; never touch Proxmox boot config.
- Green-gate after each batch: `cd codes && python3 -m py_compile b2ctl/*.py &&
  python3 -m pytest tests/ -q`. When a fix "breaks" a test that mocked the wrong
  shape, fix the **fixture**, never the code.
- **Last-redundancy policy** (F-023/F-024): warn + require typing the pool name
  (not a hard refuse).

## Batches (per Appendix A ordering)

- **Batch 1 — shared safety infra:** dry-run write-gate by `os.path.basename`
  (F-004/F-008), `config.load` robustness (F-014), central resilver LED guard in
  `locate.py` (F-006 + callers F-001/F-002/F-020).
- **Batch 2 — resilver/replace cluster:** `poll_resilver_status` positive-match
  completion (F-025), `_replace_member` honors the wait result (F-009), fold
  `_cmd_swap` into the shared flow (F-057), bounded `_wait_resilver` (F-055),
  `prune_orphan_crons` no-op on empty `list_pools` (F-063).
- **Batch 3 — remaining P0/P1:** cache/log op confirms (F-003), rollback dry-run
  (F-013), hotplug in-pool guard (F-019), extend pool picker (F-021),
  Ctrl-C/EOF at prompts (F-022), `can_detach`/`can_offline` guards (F-023/F-024),
  RAID replace/route/backend-detect/burnin fixes (F-007/F-016/F-017/F-010/F-011/
  F-012), read-path udev move (F-005), ghost-serial match (F-015), SAS FAILURE →
  CRITICAL (F-018).
- **Batch 4 — P2 by file:** per-scan subprocess caching (F-028/F-037/F-040/F-041),
  smart raw-value + LBA-unit parsing (F-050/F-051), `set_mode` atomic (F-075),
  installer hardening (F-043/F-044/F-045/F-086/F-087/F-111), install.sh trap +
  quoting (F-064/F-109/F-110/F-112/F-122), config/int guards (F-027/F-029/F-034/
  F-039), and the rest.
- **Batch 5 — P3 structure + test-gaps** (this pass): see below.

## Batch 5 / P3 structural work (the architectural changes)

| Finding | Change | Files |
|--------|--------|-------|
| F-066 | `__version__` → `b2ctl/_version.py`; `__init__`/`cli` import it | `_version.py`, `__init__.py`, `cli.py`, CLAUDE §10 |
| F-070 | public `zfs_actions` module (offload/replace/create/destroy/swap/demote → int); `_cmd_*` return bool | `zfs_actions.py`, `watch.py`, `cli.py` |
| F-079 | targeted `scan_one` (one lsblk + SMART on the target only) | `core.py` |
| F-102 | `scan_light` (no SMART) for locate/resolve | `core.py`, `cli.py`, `watch.py` |
| F-099 | shared `blockdev` module (lsblk_pairs/EXCLUDE/vd_usage) | `blockdev.py`, `hba.py`, `watch.py`, `core.py` |
| F-103 | `Disk.is_poolable` replaces 4 duplicated filters | `common.py`, `watch.py` |
| F-107 | `can_detach/can_offline/detach_safety(…, topo=None)` shared snapshot | `zfs.py`, `watch.py` |
| F-084 | `baymap.assign_bays` shared serial-match loop | `baymap.py`, `hba.py`, `hba_raid.py` |
| F-085 | `Disk.ctrl` threaded through all PERC actions (`/c<ctrl>`) | `common.py`, `hba_raid.py`, `raid_actions.py`, `locate.py` |
| F-089 | `hba_raid.build_cmd` so audit == executed | `hba_raid.py`, `raid_actions.py` |
| F-090 | Ctrl-C/EOF at the insert prompt aborts with `end_op(False)`, returns 1 | `raid_actions.py` |
| F-091/092/093 | append-only ops.jsonl, in-memory fallback, named old/new dev hints | `safety.py` |
| F-098 | dry-run flag owned by `common` | `common.py` (already), `cli.py`, `watch.py`, `raid_actions.py` |
| F-095 | SAS "total uncorrected errors" → `d.uncorr` | `smart.py` |
| F-097 | `spec.lookup` exact→longest→unambiguous, else None | `spec.py` |
| F-105 | `spares()` dedup by token | `zfs.py` |
| F-081 | `_by_id_index` nvme-uuid rank + shortest-friendly tie-break | `hba.py` |
| F-106/F-083 | delete `add_mirror`, `resilver_status`, dead `_lsblk_pairs`, `render_raid_volumes` | `zfs.py`, `hba_raid.py`, `ui.py` |
| F-115–F-118, F-114, F-123, F-065 | sim: perccli VD/PD/rebuild + megaraid SMART + PERC-VD lsblk; time-based resilver; replacing-N/spare-N; offline/online state; atomic state; NVMe bay addressing; raid10 | `sim/bin/*`, `sim/_simstate.py`, `sim/simctl` |

## Test plan

One test file per module (CLAUDE §8). Every finding with a "How to verify the
fix" got its named test. New test files: `test_raid_actions.py`,
`test_blockdev.py`, `test_zfs_actions.py`. Fixtures for the new parse paths
(NVMe SMART, SAS uncorrected-errors, perccli PD/VD tables) live in `helpers.py`
/ `test_sim_smoke.py`. `conftest.py` autouse resets probe/config/baymap memos.

## Definition of done

Code compiles + full pytest green; sim validated IT and RAID modes; reader
(user-guide-en/th) + DevOps docs updated for the locate-syntax drift (F-119) and
module map; ADR-001 records the layering; `_version.py` bumped to `0.9.0-itmode`.
