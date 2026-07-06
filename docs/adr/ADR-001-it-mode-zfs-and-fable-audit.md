# ADR-001 — IT-mode/HBA on Proxmox ZFS-on-root; layering after the Fable5 audit

- **Status:** Accepted
- **Date:** 2026-07-06
- **Supersedes:** the original v0.1 assumption ("OS on hardware RAID1, no ZFS boot pool")

## Context

b2ctl began as a health monitor for a PERC-owned hardware-RAID box. The fleet has
since changed and the tool has grown two co-equal backends plus a full ZFS
lifecycle. This ADR records the architecture that is now true on hardware, and
the structural decisions taken while resolving the **Fable5 code-review audit**
(`reviews/REVIEW_FABLE_001.md`, 123 findings) in v0.9.0-itmode.

## Decision — deployment architecture (the original assumption is void)

- The two production nodes are **Dell R620, Proxmox VE 9.2 (Debian 13, ZFS 2.4)**,
  PERC H710 mini **crossflashed to IT/HBA mode** (presents as LSI SAS9207-8i /
  SAS2308). **Disks are raw.** A third class of box (R640, PERC H730P) runs the
  controller in **RAID mode**.
- Storage is **ZFS-on-root**: `rpool` is a 2-way mirror on `-part3` of by-id
  devices (the Proxmox boot pool); `tank` is **raidz1 (3× 870 EVO) + 1 hot spare**.
  There is no hardware RAID1 OS volume and no "no boot pool" — that early
  assumption is void.
- Consequence: `tank` tolerates only ONE failed disk and its resilver reads all
  surviving members; the hot-spare auto-resilver / `zpool replace` is the recovery
  path, and raidz vdevs cannot be removed or converted in place. rpool boot-disk
  replacement still needs a manual `proxmox-boot-tool format/init` on the new ESP —
  b2ctl surfaces this, never automates it.

## Decision — module layering (Fable5 audit, v0.9.0-itmode)

The audit surfaced layering inversions and duplication. The resolved layering,
bottom-up, is:

1. **`_version.py`** owns `__version__` (F-066). `__init__`/`cli` import it, so
   reading the version no longer loads the whole application graph. Bump the
   version *here*.
2. **`common.py`** is the bottom layer and now also owns the **dry-run flag**
   (`DRY_RUN`/`set_dry_run`/`is_dry_run`, F-098) — previously it lived in the
   interactive `watch` module and was read by `raid_actions`/`burnin`, an
   inversion. `cli.main` and watch's `[t]` toggle both sync `common`.
3. **`blockdev.py`** owns backend-agnostic block-device listing (`lsblk_pairs`,
   `EXCLUDE`, `vd_usage`, F-099) — moved out of the IT-mode `hba` module that
   `watch`/`core` were reaching into.
4. **`baymap.assign_bays`** is the single serial-match→remap loop both backends'
   `attach_bays` delegate to (F-084).
5. **`zfs_actions.py`** is the public CLI contract for the ZFS lifecycle
   (offload/replace/create/destroy/swap/demote), mirroring `raid_actions.py` for
   PERC. cli calls these public functions instead of `watch._cmd_*` privates, and
   they return a real process exit code (F-070) — lifecycle subcommands no longer
   always exit 0.

Cross-cutting safety decisions from the audit:

- **Audit log is append-only** (`safety.ops.jsonl`): `end_op` appends an end
  record rather than rewriting the file (no truncation-loss window, F-093), keeps
  an in-memory pending entry so a full/read-only `/var` still yields a result and
  post-op verification (F-092), and builds rollback hints from **named** old/new
  device fields, not fragile positional cmd indices (F-091).
- **Multi-controller correctness:** `Disk.ctrl` is threaded through every PERC
  action so a two-controller box targets `/c<ctrl>`, not a hardcoded `/c0`
  (F-085); audited commands are built by `hba_raid.build_cmd` so the log matches
  what ran (F-089).
- **Read path stays side-effect-free** (§9): `core.scan_light`/targeted
  `scan_one` avoid the full SMART fan-out for locate/resolve/hotplug paths
  (F-079/F-102); `can_detach/can_offline/detach_safety` accept a shared `topo`
  snapshot to avoid redundant `zpool status` in one guarded flow (F-107).
- **Last-redundancy policy:** offload/demote of a 2-way mirror leg (e.g. rpool)
  warns and requires typing the pool name rather than proceeding or hard-refusing
  (Task C decision).

## Consequences

- The simulation harness (`sim/`) now models the RAID backend (perccli VD/PD/
  rebuild tables + `smartctl -d megaraid` passthrough), a time-based resilver
  (reads are side-effect-free), replacing-N/spare-N intermediate vdevs, and
  `offline/online` state — so both backends and the whole lifecycle are exercised
  on a laptop (F-114/F-115/F-116/F-117/F-118/F-123).
- Version bumped to **0.9.0-itmode**. Full unit suite green; sim validated for
  IT and RAID modes.
