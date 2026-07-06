# REVIEW_FABLE_001 — Full audit of b2ctl

| | |
|---|---|
| **Reviewed commit** | `48ce2f7` (branch `dev`) — "docs: drop locate --pulse (v0.8.8)" |
| **Review date** | 2026-07-05 |
| **Reviewer** | Claude Fable 5 — multi-agent audit (13 finders + 3 gap hunters + adversarial verifiers, every finding independently re-checked against the code) |
| **Baseline** | `python3 -m py_compile b2ctl/*.py` clean; `python3 -m pytest tests/ -q` = **286 passed** |
| **Scope** | `codes/b2ctl/*.py` (deep), `codes/install.sh`, `codes/sim/`, `codes/tests/` (coverage gaps), docs consistency |

Every finding below is **latent**: the current test suite does not catch it. Raw pipeline: 175+18 raw findings -> 167 after dedupe -> 160 CONFIRMED / 3 PLAUSIBLE / 4 REFUTED (dropped; listed in Appendix B).

## How to use this report (instructions for the fixing model)

- Each finding has a stable ID (`F-001`...). Work in priority order unless the *Fix ordering* section says otherwise.
- `Location` line numbers were verified against commit `48ce2f7`. If the file has drifted, re-locate via the quoted evidence, not the line number.
- **Verdict = CONFIRMED**: an independent verifier traced the defect in code; trust the analysis. **Verdict = PLAUSIBLE**: likely real but not fully traced — re-verify before fixing, and skip with a note if it does not reproduce.
- Every fix must keep the project constraints: **Python stdlib only** (no pip), `run()`/`run_check()` list-form subprocess (no `shell=True`), by-id device paths for actions, English code/messages, and the CLAUDE.md section 9 safety rules.
- Each finding names the test to add. Test layout: one file per module (`tests/test_<module>.py`), shared fixtures in `tests/helpers.py`. After each batch: `cd codes && python3 -m py_compile b2ctl/*.py && python3 -m pytest tests/ -q`.
- Several findings note that existing tests mock the wrong data shape — fix the test fixture together with the code, in the same change.

## Severity rubric

| Level | Meaning |
|---|---|
| P0 (Critical) | Data-loss / pool-fault risk, violation of CLAUDE.md section 9 safety rules, crash on a main path |
| P1 (High) | Wrong behavior on realistic input, broken action path, parser that misreads real tool output |
| P2 (Medium) | Error-handling gaps, warnings, performance issues with user-visible effect |
| P3 (Low) | Maintainability/structure/scalability debt, minor perf, test-coverage gaps |
| P4 (Suggestion) | Style/naming/docs polish — no performance or behavior effect |

## Statistics

Total **123** findings — **P0 (Critical)**: 9 · **P1 (High)**: 17 · **P2 (Medium)**: 39 · **P3 (Low)**: 54 · **P4 (Suggestion)**: 4

By category: bug: 71 · test-gap: 12 · warning: 10 · maintainability: 10 · performance: 9 · structure: 4 · docs: 3 · scalability: 2 · improvement: 2

Hottest files: `codes/b2ctl/watch.py`: 20 · `codes/b2ctl/cli.py`: 15 · `codes/b2ctl/zfs.py`: 13 · `codes/b2ctl/hba_raid.py`: 9 · `codes/install.sh`: 7 · `codes/b2ctl/smart.py`: 6 · `codes/b2ctl/core.py`: 5 · `codes/b2ctl/raid_actions.py`: 5 · `codes/b2ctl/safety.py`: 5 · `codes/b2ctl/hba.py`: 5

## Summary table (work queue)

| ID | Pri | Verdict | Location | Title |
|---|---|---|---|---|
| F-001 | P0 | CONF | `codes/b2ctl/cli.py:51` | status --locate blinks LEDs on WARNING disks including rebuilding PERC members, violating the never-LED-a-resilvering-disk rule; the branch has zero tests |
| F-002 | P0 | CONF | `codes/b2ctl/cli.py:58` | status --locate dd-blinks raw d.dev via blink_many: wrong bays on RAID mode, blinks rebuilding disks and ghosts |
| F-003 | P0 | CONF | `codes/b2ctl/cli.py:126` | cache-add/cache-rm/log-add/log-rm mutate pools with zero [y/N] confirmation, violating section 9 |
| F-004 | P0 | CONF | `codes/b2ctl/common.py:59` | run_check dry-run gate fails open for perccli mutations: args[0] exact-token match misses resolved tool paths and perccli is absent from WRITE_CMDS |
| F-005 | P0 | CONF | `codes/b2ctl/core.py:27` | Read path (b2ctl status) automatically fires udevadm trigger/settle for every ghost candidate, violating the section-9 'read path is side-effect-free' rule |
| F-006 | P0 | CONF | `codes/b2ctl/locate.py:78` | No resilvering/rebuilding guard anywhere in the LED-locate path (violates CLAUDE.md section 9: never light an LED on a resilvering disk) |
| F-007 | P0 | CONF | `codes/b2ctl/raid_actions.py:125` | raid replace never starts the rebuild and reports false success, because 'Not in progress' is treated as done |
| F-008 | P0 | CONF | `codes/b2ctl/safety.py:15` | Dry-run executes destructive perccli commands for real: WRITE_CMDS omits perccli and is matched against args[0] verbatim |
| F-009 | P0 | CONF | `codes/b2ctl/watch.py:445` | _replace_member ignores _wait_resilver result and auto-detaches old member + lights pull-LED even when resilver ended with errors or is still running |
| F-010 | P1 | CONF | `codes/b2ctl/backend.py:151` | Backend auto-detection trusts non-empty stdout, so sas2ircu's error banner selects IT mode on a RAID box |
| F-011 | P1 | CONF | `codes/b2ctl/burnin.py:25` | start_selftest never passes -d <dtype>, so RAID-mode (megaraid passthrough) burn-in cannot start the self-test it later polls |
| F-012 | P1 | CONF | `codes/b2ctl/burnin.py:56` | read_scan hard timeout of 600 s kills badblocks after 10 minutes although a full surface scan takes hours, then misreports it as disk errors |
| F-013 | P1 | CONF | `codes/b2ctl/cli.py:449` | b2ctl --dry-run rollback executes the real zpool command — dry-run not propagated |
| F-014 | P1 | CONF | `codes/b2ctl/config.py:56` | load() crashes every b2ctl command on valid-JSON-but-wrong-shape config, breaking the 'malformed -> defaults' contract |
| F-015 | P1 | CONF | `codes/b2ctl/core.py:53` | Ghost-row cleanup after SMART uses exact serial equality while ghost creation used prefix matching — SAS drives with no lsblk SERIAL and a truncated sas2ircu serial stay as permanent phantom CRITICAL GHOST rows |
| F-016 | P1 | CONF | `codes/b2ctl/hba_raid.py:325` | attach_bays overwrites Disk.bay with the bay_map.json display label, but every perccli action (_pd: locate/set_offline/set_missing/start_rebuild/set_jbod/add_hotspare) uses Disk.bay as the controller enc:slot selector |
| F-017 | P1 | CONF | `codes/b2ctl/locate.py:99` | blink_many bypasses the perccli routing and dd-reads d.dev — in RAID mode it flickers the whole VD instead of one bay |
| F-018 | P1 | CONF | `codes/b2ctl/smart.py:62` | SAS failing health status ('FAILURE PREDICTION THRESHOLD EXCEEDED') is stored as 'FAILURE', which assess() never flags — dying SAS drive shows LEVEL NORMAL |
| F-019 | P1 | CONF | `codes/b2ctl/watch.py:125` | _handle_new_disk declares any hot-plugged device 'free' and offers the wipe menu even when the disk is an active pool member |
| F-020 | P1 | CONF | `codes/b2ctl/watch.py:399` | _cmd_locate blinks any disk with no resilver/vdev-state guard, violating the 'never light an LED on a resilvering disk' rule |
| F-021 | P1 | CONF | `codes/b2ctl/watch.py:712` | _cmd_extend treats zfs.list_pools() dicts as pool-name strings — [e]xtend is unusable on the real two-pool servers |
| F-022 | P1 | CONF | `codes/b2ctl/watch.py:800` | KeyboardInterrupt is only caught around select(); Ctrl-C at any prompt or during a resilver wait crashes watch with a traceback (and _confirm_op's bare input() also dies on Ctrl-D) |
| F-023 | P1 | CONF | `codes/b2ctl/zfs.py:280` | can_detach approves detaching a leg of a 2-way mirror (rpool), defeating the Task C 'refuse if no redundancy remains' guard |
| F-024 | P1 | CONF | `codes/b2ctl/zfs.py:303` | can_offline ignores members nested in spare-N/replacing-N sub-vdevs, approving a second outage on an already-degraded raidz1 |
| F-025 | P1 | CONF | `codes/b2ctl/zfs.py:329` | poll_resilver_status misreads an in-progress resilver with 'no estimated completion time' as completed-with-errors |
| F-026 | P1 | CONF | `codes/b2ctl/zfs.py:351` | Destructive wipe paths untested: wipe_sg (raw dd zeroing, uncaught TimeoutExpired) and wipe (ignores labelclear/wipefs failures) have no unit tests |
| F-027 | P2 | CONF | `codes/b2ctl/backend.py:63` | Unguarded int() on controller.index config crashes every scan (status/watch) with a traceback |
| F-028 | P2 | CONF | `codes/b2ctl/baymap.py:33` | bay_map.json re-read and re-parsed 4 times per scan (no cache, no shared panels) |
| F-029 | P2 | CONF | `codes/b2ctl/baymap.py:58` | Malformed bay_map panel entries crash core.scan: int() outside the try and non-dict list entries |
| F-030 | P2 | CONF | `codes/b2ctl/burnin.py:46` | selftest_status result regex scans the whole smartctl output, so a stale 'Completed without error' from the drive's HISTORICAL self-test log masks a current aborted test — false burn-in PASS |
| F-031 | P2 | CONF | `codes/b2ctl/cli.py:76` | b2ctl locate lacks the ghost-disk guard watch has and reports false success |
| F-032 | P2 | CONF | `codes/b2ctl/cli.py:119` | _resolve_devs passes unresolved tokens and /dev/sdX fallbacks straight into zpool mutations, despite its 'never /dev/sdX' contract |
| F-033 | P2 | CONF | `codes/b2ctl/cli.py:359` | b2ctl --dry-run update / install perform real writes and downloads |
| F-034 | P2 | CONF | `codes/b2ctl/cli.py:384` | b2ctl config init as non-root crashes with an unhandled PermissionError traceback |
| F-035 | P2 | CONF | `codes/b2ctl/config.py:31` | tool_paths overrides for zpool/wipefs/sgdisk/dd/udevadm (and hba.py's lsblk) are dead config keys — every runtime call site uses the bare command name |
| F-036 | P2 | CONF | `codes/b2ctl/hba.py:159` | sas2ircu bay_map() records Serial No from non-disk DISPLAY sections (Enclosure services device), creating a permanent phantom GHOST CRITICAL row |
| F-037 | P2 | CONF | `codes/b2ctl/hba.py:178` | attach_bays/get_ghost_disks re-probe sas2ircu on every call — 5 `sas2ircu list` spawns per scan, 4 redundant |
| F-038 | P2 | CONF | `codes/b2ctl/hba.py:252` | find_sg_for_ghost / _read_sg_serial — the code that picks WHICH /dev/sgX gets dd-zeroed — is untested, including its loose bidirectional substring serial match and binary VPD-80 parsing |
| F-039 | P2 | CONF | `codes/b2ctl/hba_raid.py:90` | Malformed controller.index in config crashes scan with an uncaught ValueError, contradicting config.py's 'malformed → defaults apply' contract |
| F-040 | P2 | CONF | `codes/b2ctl/hba_raid.py:247` | RAID-mode scan re-runs perccli ~15 times per status: bay_map recomputed, eall/sall fetched twice, vols parsed then discarded |
| F-041 | P2 | CONF | `codes/b2ctl/hba_raid.py:272` | One RAID-mode scan re-runs identical perccli commands 4-6 times (eall/sall twice, ctrlcount ~5x, vall twice per status), and IT-mode re-runs `sas2ircu list` per have_sas2ircu() call |
| F-042 | P2 | PLAU | `codes/b2ctl/hba_raid.py:396` | rebuild_progress percent regex requires 'NN%', but real perccli 'show rebuild' prints a bare integer under a 'Progress%' header — progress is always parsed as 0.0 |
| F-043 | P2 | CONF | `codes/b2ctl/installer.py:58` | Downloaded root-run binaries are never integrity-verified (only a 1 KB size check) and the download has no timeout |
| F-044 | P2 | CONF | `codes/b2ctl/installer.py:187` | install_tools catches only RuntimeError — a network failure in urlretrieve tracebacks out of `b2ctl install --with-tools` |
| F-045 | P2 | CONF | `codes/b2ctl/installer.py:227` | install_profile persists controller.mode even when the tool install failed, forcing a backend the box cannot serve |
| F-046 | P2 | CONF | `codes/b2ctl/locate.py:64` | blink() reports success ('done via dd') even when dd fails instantly, misleading a physical pull |
| F-047 | P2 | CONF | `codes/b2ctl/locate.py:90` | perccli locate path leaves the LED latched on if interrupted — no try/finally, unlike the ledctl path |
| F-048 | P2 | CONF | `codes/b2ctl/raid_actions.py:49` | _pick_member matches the shared controller block device, so 'sda' silently selects an arbitrary healthy RAID member |
| F-049 | P2 | CONF | `codes/b2ctl/smart.py:33` | smartctl attempt chain retries a hung disk with 30 s timeout per attempt — one dying disk stalls the whole scan up to 90-150 s |
| F-050 | P2 | CONF | `codes/b2ctl/smart.py:88` | ATA raw-value parse concatenates all digits — formatted raws like Seagate's '29229h+18m+27.459s' become garbage |
| F-051 | P2 | CONF | `codes/b2ctl/smart.py:96` | Attribute 241 assumed to be 512-byte LBAs regardless of attribute name — TBW math is wrong for vendors reporting 32MiB/GB units |
| F-052 | P2 | CONF | `codes/b2ctl/watch.py:72` | Menu index parsing accepts 0 and negative numbers, silently selecting the last list item in destructive flows |
| F-053 | P2 | PLAU | `codes/b2ctl/watch.py:218` | _wait_for_block_device does not wait: one `udevadm settle` + a single lsblk check races the asynchronous SCSI rescan started by wipe_sg |
| F-054 | P2 | CONF | `codes/b2ctl/watch.py:266` | _wipe_ghost hands the freshly-wiped disk to _assign_free_disk without the by-id guard, allowing pool actions on unstable /dev/sdX |
| F-055 | P2 | CONF | `codes/b2ctl/watch.py:406` | _wait_resilver loops forever with no escape when zpool status output is unavailable or unparsable |
| F-056 | P2 | CONF | `codes/b2ctl/watch.py:485` | _offline_and_replace matches the replacement disk by bay equality, so bay=None (no sas2ircu / unmapped) matches any free disk |
| F-057 | P2 | CONF | `codes/b2ctl/watch.py:658` | _cmd_swap duplicates _replace_member's replace+wait+detach flow but skips the safety audit trail and command-preview confirmation |
| F-058 | P2 | CONF | `codes/b2ctl/watch.py:792` | watch startup calls prune_orphan_crons() without propagating _DRY_RUN, so the documented preview mode `b2ctl --dry-run watch` deletes real /etc/cron.d files |
| F-059 | P2 | CONF | `codes/b2ctl/watch.py:835` | Hotplug diff keys on device NAME only, so a pull+insert that reuses the same /dev/sdX while watch is blocked in a prompt is completely invisible |
| F-060 | P2 | CONF | `codes/b2ctl/zfs.py:155` | Mirrored SLOG leaves are classified as data vdevs: pool_level reports 'mixed' and the extend/remove path cannot see or remove them |
| F-061 | P2 | CONF | `codes/b2ctl/zfs.py:312` | demote_to_spare is a non-atomic detach-then-add with no compensation — an add_spare failure strands the mirror one-legged with no re-attach path |
| F-062 | P2 | CONF | `codes/b2ctl/zfs.py:384` | has_zfs_label fails OPEN (wipefs error == 'no label'), silently bypassing the dirty-disk guard before pool create; wipe() also ignores labelclear/wipefs failures |
| F-063 | P2 | CONF | `codes/b2ctl/zfs.py:479` | prune_orphan_crons deletes ALL b2ctl maintenance crons when `zpool list` transiently fails — runs unguarded at every watch startup |
| F-064 | P2 | CONF | `codes/install.sh:181` | No cleanup trap: any failure inside install_tools aborts under set -e, leaking the mktemp dir and silently skipping the --perc/--flash mode write |
| F-065 | P2 | CONF | `codes/sim/bin/zpool:21` | Sim does not model raid10: pools created with `mirror a b mirror c d` render as a flat stripe and go SUSPENDED after one pull |
| F-066 | P3 | CONF | `codes/b2ctl/__init__.py:2` | Package __init__ imports cli for __version__, so importing ANY b2ctl submodule loads the entire application and sets a circular-import trap |
| F-067 | P3 | CONF | `codes/b2ctl/burnin.py:114` | burnin.run() orchestration is untested: target resolution, the in-pool refusal guard, dry-run early return, and the FAIL exit code |
| F-068 | P3 | CONF | `codes/b2ctl/cli.py:27` | _resolve_dev is dead code while the same target-resolution logic is hand-copied in three places |
| F-069 | P3 | CONF | `codes/b2ctl/cli.py:43` | status --json silently ignores --locate/--seconds |
| F-070 | P3 | CONF | `codes/b2ctl/cli.py:82` | Six CLI subcommands call watch's underscore-private workflow functions — all ZFS lifecycle logic is trapped inside the 848-line interactive module |
| F-071 | P3 | CONF | `codes/b2ctl/cli.py:241` | b2ctl check reports the number of unique bay labels as 'Controllers found' |
| F-072 | P3 | CONF | `codes/b2ctl/cli.py:313` | b2ctl update (as root) crashes with an unhandled FileNotFoundError traceback when a bundled data file is absent — install.sh explicitly treats bay_map.json as optional |
| F-073 | P3 | CONF | `codes/b2ctl/cli.py:467` | No validation of --seconds/seconds: negative value crashes and leaks dd readers |
| F-074 | P3 | CONF | `codes/b2ctl/common.py:117` | Disk.is_spare substring test ('"spare" in vdev') misclassifies the faulted member sitting under a transient spare-N vdev as a hot spare |
| F-075 | P3 | CONF | `codes/b2ctl/config.py:142` | set_mode silently discards all existing config on a malformed file and writes non-atomically |
| F-076 | P3 | CONF | `codes/b2ctl/config.py:179` | validate() does not catch subprocess.TimeoutExpired from the tool probe, crashing `b2ctl update` when a probed binary hangs |
| F-077 | P3 | CONF | `codes/b2ctl/core.py:44` | SMART thread pool hardcoded to max_workers=4 serialises the 8-bay scan into two waves |
| F-078 | P3 | CONF | `codes/b2ctl/core.py:73` | Disk table sort on bay is lexicographic, so double-digit slots order as 0:1, 0:10, 0:11, 0:2 |
| F-079 | P3 | CONF | `codes/b2ctl/core.py:110` | scan_one() runs the entire fleet scan (all-disk SMART + sas2ircu + zpool) to build one hot-plugged Disk |
| F-080 | P3 | CONF | `codes/b2ctl/hba.py:26` | The lsblk -P KEY="value" parser (_lsblk_pairs) — the section-6 'lsblk must use -P' gotcha — has no regression test; enumerate tests bypass it by mocking _lsblk_pairs |
| F-081 | P3 | CONF | `codes/b2ctl/hba.py:111` | _by_id_index NVMe rank ties: nvme-uuid.* and namespace-suffixed nvme-<model>_<serial>_1 links score equal to the friendly link, so by_id is decided by os.listdir order |
| F-082 | P3 | CONF | `codes/b2ctl/hba_raid.py:107` | hba_raid._parse_bay_map (perccli 'Drive .../eE/sS Device attributes' + 'SN =' parser) is untested — every enumeration test patches bay_map() wholesale |
| F-083 | P3 | CONF | `codes/b2ctl/hba_raid.py:124` | Dead code left in the tree: hba_raid._lsblk_pairs (unused duplicate), zfs.resilver_status, zfs.add_mirror, ui.render_raid_volumes (kept alive only by its own test) |
| F-084 | P3 | CONF | `codes/b2ctl/hba_raid.py:315` | attach_bays serial-prefix matching is copy-pasted between the two backends — the docstring itself admits the duplication |
| F-085 | P3 | CONF | `codes/b2ctl/hba_raid.py:350` | RAID mutating actions hardcode controller 0 while enumeration is multi-controller aware — Disk drops the controller index |
| F-086 | P3 | CONF | `codes/b2ctl/installer.py:106` | tarfile.extractall without filter — tar path traversal writes outside the temp dir as root on Python 3.13 |
| F-087 | P3 | CONF | `codes/b2ctl/installer.py:148` | Prerequisite package sets drifted between install.sh and installer.ensure_prereqs, breaking the claimed '1:1 mirror' install contract |
| F-088 | P3 | CONF | `codes/b2ctl/raid_actions.py:73` | raid_actions.replace() and offline() — the guided destructive PERC workflows (set offline+missing, LED, rebuild wait) — have zero tests and no tests/test_raid_actions.py exists |
| F-089 | P3 | CONF | `codes/b2ctl/raid_actions.py:100` | Audit command lists are hand-written duplicates of what hba_raid actually executes — and have already drifted (binary name, hardcoded /c0) |
| F-090 | P3 | CONF | `codes/b2ctl/raid_actions.py:116` | Ctrl-C/EOF at the 'insert the new drive' prompt is swallowed and the replace flow continues |
| F-091 | P3 | CONF | `codes/b2ctl/safety.py:22` | Rollback hint for 'replace' indexes positionally into the caller's cmd list (cmds[0][4]/[5]) — fragile cross-module coupling on an EXECUTABLE hint |
| F-092 | P3 | CONF | `codes/b2ctl/safety.py:84` | end_op silently no-ops when the audit entry cannot be read back — op result, rollback hint, and post-op verification all skipped |
| F-093 | P3 | CONF | `codes/b2ctl/safety.py:164` | end_op rewrites the entire unbounded ops.jsonl on every operation completion |
| F-094 | P3 | CONF | `codes/b2ctl/safety.py:199` | _post_op_verify and the rollback-hint builders are never executed by any test — every safety test patches them out |
| F-095 | P3 | CONF | `codes/b2ctl/smart.py:122` | SAS error-counter log's 'total uncorrected errors' column is never parsed, so d.uncorr stays 0 for SAS drives |
| F-096 | P3 | CONF | `codes/b2ctl/smart.py:130` | smart._parse_nvme and the NVMe dispatch branch are completely untested — no NVMe smartctl fixture exists anywhere in tests/ |
| F-097 | P3 | CONF | `codes/b2ctl/spec.py:47` | Bidirectional substring TBW lookup lets a truncated model match the wrong capacity's rating, with dict insertion order deciding ties |
| F-098 | P3 | CONF | `codes/b2ctl/watch.py:28` | Global dry-run state lives in the interactive watch module and is written by cli and read by raid_actions/burnin — layering inversion |
| F-099 | P3 | CONF | `codes/b2ctl/watch.py:47` | watch's hotplug baseline reaches into hba private internals (_lsblk_pairs/_EXCLUDE), bypassing the Backend abstraction |
| F-100 | P3 | CONF | `codes/b2ctl/watch.py:117` | _handle_new_disk uses a fixed time.sleep(2) instead of udev settle, so a slow udev queue makes the hotplug flow abort with a misleading 're-insert' message |
| F-101 | P3 | CONF | `codes/b2ctl/watch.py:148` | _assign_free_disk mutating menu choices 2/3/4/5/6 (add-spare, replace-degraded, attach, single-disk add, wipe) are untested — only the choice-4 scan-reuse optimization is asserted |
| F-102 | P3 | CONF | `codes/b2ctl/watch.py:382` | Locate and token-resolution paths run a full SMART scan whose data they never use |
| F-103 | P3 | CONF | `codes/b2ctl/watch.py:521` | The 'poolable free disk' invariant (not in_pool, dev != '-', no smart_dtype) is enforced by three duplicated inline filters instead of the Disk contract |
| F-104 | P3 | CONF | `codes/b2ctl/zfs.py:25` | zfs read-side helpers untested: list_pools() tab parser, spares(), has_zfs_label(), and the attach/add_mirror/swap_to_spare command shapes |
| F-105 | P3 | CONF | `codes/b2ctl/zfs.py:170` | spares() returns duplicated tokens because topology entries are indexed under both token and realpath |
| F-106 | P3 | CONF | `codes/b2ctl/zfs.py:235` | Dead code: add_mirror and resilver_status are defined but never called anywhere |
| F-107 | P3 | CONF | `codes/b2ctl/zfs.py:268` | _cmd_offload builds full zpool topology 3-4 times back-to-back within one guarded flow |
| F-108 | P3 | CONF | `codes/b2ctl/zfs.py:373` | wipe() ignores labelclear/wipefs failures and reports success based on sgdisk alone |
| F-109 | P3 | CONF | `codes/install.sh:29` | Unknown flags are silently ignored and flag combinations are order-dependent (--with-tools --perc installs perccli only; reversed order installs both) |
| F-110 | P3 | CONF | `codes/install.sh:60` | Download validation is a 1 KB size check only, and --flash/--perc still set controller.mode even when the tool install visibly failed |
| F-111 | P3 | CONF | `codes/install.sh:83` | install_tools registers the i386 dpkg architecture and installs libc6-i386/alien/unzip regardless of which tool subset was requested |
| F-112 | P3 | CONF | `codes/install.sh:145` | `cp -r` into an existing /opt/b2ctl never removes stale modules and ships the dev machine's __pycache__, so upgrades are not idempotent |
| F-113 | P3 | CONF | `codes/install.sh:165` | install.sh has zero automated checks — not even bash -n — leaving the section-6 "$bin" quoting gotcha and the launcher heredoc unguarded |
| F-114 | P3 | CONF | `codes/sim/_simstate.py:73` | Non-atomic save() plus load() silently falling back to default_state() lets a concurrent read clobber or shadow the whole sim state |
| F-115 | P3 | CONF | `codes/sim/bin/perccli:22` | Fake perccli never emits VD/PD tables or rebuild %, so the whole RAID-mode lifecycle is unexercised and raid_actions._wait_rebuild hangs forever in sim |
| F-116 | P3 | CONF | `codes/sim/bin/zpool:122` | Fake `zpool status` mutates sim state (resilver +50% per read) — the read path has side effects and progress is consumed by unrelated reads |
| F-117 | P3 | CONF | `codes/sim/bin/zpool:150` | Fake `zpool replace` finalizes instantly instead of creating a replacing-N vdev, masking Task-B detach/finalize bugs |
| F-118 | P3 | CONF | `codes/sim/bin/zpool:300` | Fake zpool silently no-ops `offline`/`online`, so the guarded spare-less offload path is never actually exercised in sim |
| F-119 | P3 | CONF | `docs/user-guide-en.md:855` | Docs (and CLAUDE.md §3) document `b2ctl locate <bay> on`, but the CLI only accepts an integer seconds argument — the documented latch-on form errors out |
| F-120 | P4 | CONF | `codes/b2ctl/cli.py:569` | install --tool help claims 'default: all missing' contradicting the actual no-flag behavior; module docstring lists 5 of 24 subcommands |
| F-121 | P4 | CONF | `codes/b2ctl/watch.py:7` | Module docstring documents 4 of the 12 watch commands, and `os`/`Disk` imports are dead |
| F-122 | P4 | CONF | `codes/install.sh:13` | Google Drive file IDs and download base URL are duplicated verbatim between install.sh and b2ctl/installer.py |
| F-123 | P4 | CONF | `codes/sim/_simstate.py:105` | disk_by_bay only parses int:int bays, so simctl cannot address the NVMe disks by their displayed bay (PCIe2:0) and pull prints 'bay None:None' |

# P0 (Critical) findings

## F-001 — status --locate blinks LEDs on WARNING disks including rebuilding PERC members, violating the never-LED-a-resilvering-disk rule; the branch has zero tests

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/cli.py:51`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

cli.py:51 `risky = [d for d in disks if d.level in ("WARNING", "CRITICAL")]` then :58 `locatemod.blink_many(devs, args.seconds)`. common.py:146 grades a rebuilding PERC member WARNING (`sev = "WARNING" if st in ("RBLD", "REBUILD")`), and blink_many dd-reads d.dev, which for a PERC member is the shared VD device /dev/sda. No test in tests/test_cli.py exercises _status at all (only parser/log/rollback/update tests); test_sim_smoke runs status without --locate.

**Failure scenario**

R640 RAID box mid-rebuild after raid-replace: the rebuilding member is level WARNING, so `b2ctl status --locate` lights/flickers its bay (and, via dd on the shared /dev/sda, the whole VD's activity LEDs). Operator follows the LED and pulls the rebuilding drive -> VD failure/data loss. Direct violation of CLAUDE.md section 6/9 (never light an LED on a rebuilding/resilvering disk).

**Suggested fix**

In _status, exclude disks whose pd_state is Rbld/Rebuild or whose vdev shows resilvering from `risky`, and route PERC PDs through locate.blink_disk (perccli by enc:slot) instead of blink_many/dd on the VD device; skip ghost rows (dev == '-').

*Verifier note:* Fix is right and stdlib-only. Concretely: in _status, filter risky to exclude d.pd_state.upper() in ('RBLD','REBUILD') and d.dev == '-', and iterate locatemod.blink_disk(d, args.seconds) per disk instead of blink_many(devs) — blink_disk already routes PERC PDs to perccli by enc:slot and raw disks to ledctl/dd. (Per-disk blinking is sequential rather than simultaneous; if simultaneity matters, split risky into PERC PDs -> perccli and raw disks -> blink_many.) Add a resilver check for the IT-mode side (skip disks in a 'replacing' vdev during an active scan).

**How to verify the fix**

tests/test_cli.py::TestStatusLocate — build one Rbld HW member + one FAULTED raw disk via helpers._disk, patch core.scan/locate.blink_many/locate.blink_disk, run `status --locate`, assert the rebuilding member is never blinked and the FAULTED disk uses the per-disk backend routing.

<details><summary>Verification trace</summary>

Traced end to end: cli.py:51 selects level in (WARNING, CRITICAL); common.py:146 grades pd_state RBLD/REBUILD as WARNING, so a rebuilding PERC member is selected. hba_raid.py:252 sets every VD member's Disk.dev to ctrl_dev (the shared VD block device, e.g. /dev/sda), and cli.py:55/58 passes d.dev straight to blink_many, which dd-reads it — exactly the routing locate.py:6-8's own docstring forbids ('a member shares /dev/sda, so ledctl/dd there would light the whole VD'). The correct per-backend router blink_disk (locate.py:78, perccli by enc:slot) exists and is used by the `locate` subcommand but bypassed by _status. Ghost rows (hba.py:211, Disk(dev='-'), level CRITICAL) also land in devs, spawning a failing dd. Test claims verified: grep of tests/test_cli.py shows zero _status coverage (only log/rollback/locate/parser tests) and test_sim_smoke never passes --locate. Direct CLAUDE.md section 6/9 violation ('never light an LED on a rebuilding/resilvering disk') with a pull-the-rebuilding-drive -> VD-loss consequence: P0.

</details>

## F-002 — status --locate dd-blinks raw d.dev via blink_many: wrong bays on RAID mode, blinks rebuilding disks and ghosts

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/cli.py:58`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: status --locate blinks rebuilding/degraded disks and dd-reads the shared PERC VD device, violating the LED and PERC-routing rules
- **Also independently reported as**: status --locate dd-blinks every WARNING/CRITICAL disk with no resilver or backend check

**Evidence**

`devs = [d.dev for d in risky]` (line 55) then `method = locatemod.blink_many(devs, args.seconds)` (line 58). blink_many (locate.py:96) spawns `dd if=<dev>` directly with no is_perc_pd routing — locate.py's own docstring says a PERC member shares /dev/sda so "ledctl/dd there would light the whole VD (wrong bay)". RAID members are built as Disk(dev=ctrl_dev) (hba_raid.py:252), and assess() grades pd_state Rbld as WARNING (common.py:146), so a rebuilding member lands in `risky`. Ghost disks (dev == "-", CRITICAL) also land in the list.

**Failure scenario**

R640 during a raid-replace rebuild: operator runs `b2ctl status --locate` to find the failed disk. Every risky member maps to the shared /dev/sda, so N concurrent dd readers hammer the degraded VD and ALL its bays flicker — including the rebuilding member (section 9: never light an LED on a resilvering disk). The operator, told LEDs mark at-risk disks, can pull a wrong blinking member and fault the RAID5 VD. On IT mode, a ghost entry yields `dd if=-` which fails silently while the command prints "[+] done (via dd)".

**Suggested fix**

In _status: filter risky to exclude d.dev == "-" and rebuilding/resilvering members (pd_state in Rbld/Rebuild, or spare_replacing/vdev resilver flags); dedupe devices; route each disk through locatemod.blink_disk (is_perc_pd-aware, prefers ledctl) instead of blink_many on raw devs.

*Verifier note:* Fix is right. Additionally dedupe the dev list (RAID members all share ctrl_dev) and note blink_disk is sequential (5s per disk) — either accept the serialization or parallelize with threading (stdlib) for the multi-disk case. | Merged duplicate's note: Fix direction correct: filter risky (exclude pd_state RBLD, dev == '-', and members of an actively-resilvering 'replacing'/'spare' vdev) and route each survivor through locate.blink_disk, which already does the perccli-by-enc:slot routing for PERC PDs. This finding duplicates id 88 — one fix in _status resolves both. | Merged duplicate's note: Simplest correct fix: route each risky disk through the existing locatemod.blink_disk (it already does perccli enc:slot routing + ledctl→dd fallback), running them via concurrent.futures.ThreadPoolExecutor (stdlib, already used in core.py) instead of the raw blink_many dd fan-out; skip d.health == "GHOST" and d.dev in ("-", ""); before blinking, skip disks whose pool's `zpool status` scan line shows a resilver/scrub in progress (zfs module already parses scan state for the swap progress UI — reuse it). The try/except OSError around Popen is fine but low value.

**How to verify the fix**

tests/test_cli.py::test_status_locate_skips_rebuilding_and_ghosts — using helpers._disk build a HW member with pd_state='Rbld', a ghost with dev='-', and a FAULTED raw disk; mock locatemod; assert only the FAULTED disk is blinked and via blink_disk, not blink_many.

<details><summary>Verification trace</summary>

Traced: cli.py:55 builds devs=[d.dev for d in risky] and line 58 calls blink_many, which (locate.py:96-106) spawns raw `dd if=<dev>` per entry with no is_perc_pd routing — locate.py's own module docstring (lines 6-8) states a PERC member shares /dev/sda so dd there 'would light the whole VD (wrong bay)'. hba_raid.py:252/281 confirms every RAID member is Disk(dev=ctrl_dev), and common.py:146 grades pd_state RBLD as WARNING, so a rebuilding member lands in risky and N duplicate dd readers hit the shared VD device. Ghosts are Disk(dev="-") with level CRITICAL hardcoded (hba.py:211-216), so `dd if=-` is spawned, fails instantly, and _status still prints '[+] done (via dd)'. Contrast: single-disk paths (cli._locate:76, watch._cmd_locate:399) correctly route through blink_disk. Rated P1 not P0: the section-9 LED rule concerns the dedicated locate LED and the pool-fault outcome needs a further operator mistake, but the RAID-mode behavior is simply wrong on realistic input.

</details>

## F-003 — cache-add/cache-rm/log-add/log-rm mutate pools with zero [y/N] confirmation, violating section 9

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/cli.py:126`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

_cache_add: `ok, out = zfs.add_cache(args.pool, devs, dry_run=watch._DRY_RUN)` (also lines 133, 145, 152). zfs.add_cache/add_log run `zpool add -f` (force over existing labels/filesystems) and remove_vdev runs `zpool remove`. The interactive watch [e]xtend path calls _confirm() before each of these (watch.py:734/744/761); the CLI path never prompts. CLAUDE.md section 9: "Every mutating action: explicit [y/N] naming device + pool + operation" — raid-create/raid-del/destroy all confirm, these four do not.

**Failure scenario**

Operator means free bay 1:5 but types `b2ctl cache-add tank 1:4`, where 1:4 holds an exported backup pool or foreign filesystem (shows CONFIG/unassigned). _resolve_devs resolves it, no prompt appears, and `zpool add -f tank cache <by-id>` immediately clobbers the disk's labels — data loss with no chance to abort. Similarly `b2ctl log-rm tank <wrong-token>` fires `zpool remove` on whatever token was given, unconfirmed.

**Suggested fix**

In _cache_add/_cache_rm/_log_add/_log_rm, before calling zfs, print the resolved by-id device list + pool + operation and require input("... [y/N] ") == 'y' (reuse the raid_actions._confirm pattern or lift watch._confirm into common); return 1 on decline. Keep dry_run propagation as is.

*Verifier note:* Fix is correct and stdlib-only. Print the RESOLVED by-id list (not the raw args) plus pool and operation in the prompt, since _resolve_devs may have mapped a bay/serial to a different device than the operator imagines; skip or auto-annotate the prompt when watch._DRY_RUN is set. Lifting watch._confirm into common.py avoids a third confirm implementation.

**How to verify the fix**

tests/test_cli.py::test_cache_add_requires_confirmation — monkeypatch builtins.input to return 'n' and mock zfs.add_cache; call cli._cache_add; assert add_cache was NOT called and return code is 1. Mirror tests for cache-rm/log-add/log-rm.

<details><summary>Verification trace</summary>

Traced cli.py _cache_add (126), _cache_rm (133), _log_add (145), _log_rm (152): none prompt before calling zfs.add_cache/add_log/remove_vdev. zfs.py:241/247 run `zpool add -f` (forces over foreign labels/exported pools) and zfs.py:252 runs `zpool remove`. The interactive watch._cmd_extend path _confirm()s before each of these same calls, and sibling CLI destructive commands (destroy via watch._cmd_destroy, raid-create/raid-del via raid_actions._confirm, rollback via input()) all confirm — so unconfirmed execution here is an oversight, not intentional design; docs (user-guide-en.md:440, devops-guide.md:186) say nothing about the CLI path skipping confirmation. Also verified _resolve_devs (cli.py:119) passes unresolved tokens straight through to zpool, so a typo'd token reaches `zpool add -f`/`zpool remove` unprompted. Direct violation of CLAUDE.md section 9 with a data-loss path (-f clobbers an exported pool's labels).

</details>

## F-004 — run_check dry-run gate fails open for perccli mutations: args[0] exact-token match misses resolved tool paths and perccli is absent from WRITE_CMDS

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/common.py:59`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: --dry-run does not suppress perccli (or any config-path-resolved) mutations — destructive commands execute for real while UI prints 'nothing changed'

**Evidence**

is_write = bool(args) and args[0] in _safety.WRITE_CMDS  — WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"} (safety.py:15). hba_raid.set_offline/set_missing/start_rebuild/del_vd all call run_check([_tool(), ...], dry_run=dr) where _tool() is config.tool()'s shutil.which() result, e.g. "/usr/local/bin/perccli64" — never in the set, so is_write=False and the command executes.

**Failure scenario**

On the R640 RAID box the operator previews the guided replace with --dry-run. raid_actions.replace_disk passes dry_run=True down to hba_raid.set_offline → run_check(["/usr/local/bin/perccli64", "/c0/e32/s4", "set", "offline"], dry_run=True) → gate misses → perccli really marks the healthy member offline, then the next 'dry-run' step sets it missing: the live RAID volume degrades and drops a member during what the UI reports as "[DRY-RUN] would run" / "nothing changed". Direct violation of the CLAUDE.md §9 mutation-safety contract.

**Suggested fix**

In run_check, match os.path.basename(args[0]) (stdlib os.path) against _safety.WRITE_CMDS, and add "perccli", "perccli64", "storcli64" (and "udevadm") to WRITE_CMDS in safety.py. Safer still: invert to a read-only allowlist (smartctl/lsblk/sas2ircu 'show'-style reads) so unknown commands are suppressed by default under dry-run.

*Verifier note:* Fix is correct but note two refinements: (1) storcli64 is dead code in this repo (_TOOL_CANDIDATES = perccli64/perccli only) — adding it is harmless but unnecessary; udevadm's only run_check call (zfs.py:367) never passes dry_run so it is not strictly needed. (2) basename matching still misses a config tool_paths override whose basename differs (e.g. /root/perccli-7.19), so the read-only allowlist inversion (suppress unknown commands under dry-run) is the robust variant — all stdlib (os.path.basename). Also add smartctl/badblocks for burnin (see finding 102). | Merged duplicate's note: Fix direction correct: compare os.path.basename(str(args[0])) and extend safety.WRITE_CMDS with 'perccli', 'perccli64', 'smartctl', 'badblocks' (all stdlib). Basename matching still misses a config tool_paths override with a nonstandard basename, so the read-only-allowlist inversion (suppress any command not on an explicit read allowlist when dry_run=True) is the safer permanent shape — no currently dry_run-threaded call is a pure read, so nothing breaks. If dedup is wanted, merge with #49 as one finding.

**How to verify the fix**

tests/test_common.py::TestRunCheckDryRun — add test_dry_run_perccli_path_suppressed: patch subprocess.run, call common.run_check(["/usr/local/bin/perccli64", "/c0/e32/s4", "set", "offline"], dry_run=True), assert subprocess.run not called and ok is True.

<details><summary>Verification trace</summary>

Traced the full chain: run_check (common.py:59) gates dry-run on `args[0] in _safety.WRITE_CMDS` = {zpool, wipefs, sgdisk, dd} (safety.py:15). hba_raid.set_offline/set_missing/start_rebuild/add_vd/del_vd (hba_raid.py:374/380/386/416/444) all pass args[0]=_tool(), which is config.tool()'s shutil.which() result (config.py:80-89) — an absolute path, and even the bare name 'perccli64' is not in the set — so is_write=False and the command executes for real. dry_run IS threaded to these calls with no other guard: cli.py:608 sets watch._DRY_RUN on --dry-run, raid_actions._dry() reads it, and raid_actions.replace_disk (lines 104-106) calls set_offline then set_missing with dry_run=dr before its own dry-run early-return at line 119 — i.e. the healthy member is really offlined+marked missing during the 'preview', exactly as the failure scenario claims, and safety.end_op then prints 'dry-run preview — nothing changed'. Direct CLAUDE.md §9 violation on the RAID-mode box.

</details>

## F-005 — Read path (b2ctl status) automatically fires udevadm trigger/settle for every ghost candidate, violating the section-9 'read path is side-effect-free' rule

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/core.py:27`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

results = list(executor.map(bk.udev_rescue_ghost, [g.serial for g in potential_ghosts])) — scan() is called by cli._status (cli.py:40); udev_rescue_ghost runs `udevadm trigger --action=add` + `udevadm settle` (hba.py:268-269) with no [y/N], plus one `smartctl -i` per /dev/sg* while hunting the serial.

**Failure scenario**

Operator runs plain `b2ctl status` on an IT box where sas2ircu reports one drive the OS rejected (or a SAS drive with no lsblk SERIAL — see the core.py:47 comment). Every status invocation mutates udev state: re-runs udev rules for the device (can re-create nodes, retrigger zfs/systemd udev hooks mid-resilver) — a mutation without confirmation on the read path CLAUDE.md §9 declares side-effect-free.

**Suggested fix**

Add scan(rescue=False) and only pass rescue=True from watch after an explicit [y/N] prompt ('udev rescue ghost <serial>? [y/N]'); status/scan_one keep the default and merely tag the ghost row with 'run [u]dev rescue in watch'.

*Verifier note:* Suggested fix is right and stdlib-only: scan(rescue=False) default, watch passes rescue=True only after an explicit [y/N] naming the serial. Gate at core.scan level (covers both backends). When rescue is skipped, keep the ghost row but change reasons from 'udevadm rescue failed' to something like 'run rescue in watch' — the current text would be a lie. find_sg_for_ghost's smartctl -i probes are read-only and may stay.

**How to verify the fix**

tests/test_core.py::test_status_scan_never_calls_udev_rescue — stub backend returning one ghost, assert udev_rescue_ghost is not invoked when scan() is called without rescue=True, and is invoked once with it.

<details><summary>Verification trace</summary>

Traced cli._status (cli.py:40) -> core.scan() -> line 27 executor.map(bk.udev_rescue_ghost, ...) -> hba.udev_rescue_ghost, which runs `udevadm trigger --action=add <scsi device>` (hba.py:268) plus `udevadm settle` with no [y/N]. Both IT and RAID backends dispatch to the same function (backend.py:83, hba_raid.py:347). No prompt/TASKS/docs entry declares auto-rescue an intentional read-path behavior — docs/devops-guide.md documents ghost detection but never udevadm, so this is not documented design. The trigger fires only when a ghost candidate exists, but that is exactly the SAS-no-lsblk-SERIAL case core.py:47 describes (and finding 41 makes it fire on every scan, permanently). Replaying the add uevent re-runs udev rules (zfs/systemd hooks) — a mutation on the path CLAUDE.md section 9 declares side-effect-free, which the rubric maps to P0.

</details>

## F-006 — No resilvering/rebuilding guard anywhere in the LED-locate path (violates CLAUDE.md section 9: never light an LED on a resilvering disk)

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/locate.py:78`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`def blink_disk(disk, seconds: int = DEFAULT_SECONDS)` routes straight to perccli/ledctl/dd with no check of disk.pd_state, vdev_state, or pool resilver state. None of its callers guard either: watch._cmd_locate (watch.py:381-399), cli._locate (cli.py:67-78), and cli._status --locate (cli.py:51-58) which AUTO-blinks every WARNING/CRITICAL disk — a rebuilding PD (pd_state Rbld) and a DEGRADED-vdev member are graded exactly WARNING by common.assess.

**Failure scenario**

tank (raidz1, 3 disks + spare) is mid-resilver onto the hot spare, so members show DEGRADED/WARNING. Operator runs `b2ctl status --locate` (or watch [l] on the resilvering bay): the resilvering disks' LEDs blink, the DC tech pulls a blinking disk, and the already-degraded raidz1 takes a second failure — pool lost. This is the exact prohibited action in section 9 and section 6.

**Suggested fix**

In blink_disk, refuse and return (False, "resilvering") when disk.pd_state.upper() in ("RBLD","REBUILD") or when the disk's pool reports a resilver in progress (zfs.poll_resilver_status(disk.pool) not completed / vdev contains 'replacing'); add an explicit force=True keyword used only by the post-resilver pull-this-bay prompt (watch.py:449-450). Filter resilvering disks out of the status --locate risky list.

*Verifier note:* Do NOT use a pool-wide 'resilver in progress' block (zfs.poll_resilver_status(pool) not completed) as the sole gate — during a spare resilver the FAULTED member is exactly the disk the operator must locate and pull, and a pool-level block would forbid that legitimate use. Guard per-disk instead: refuse when disk.pd_state.upper() in ('RBLD','REBUILD'), or when the disk's own leaf is an active resilver participant (leaf state ONLINE inside a 'replacing-*' or 'spare-*' sub-vdev while poll_resilver_status shows not completed); still allow FAULTED/UNAVAIL/OFFLINE/REMOVED leaves. The proposed force=True escape hatch for watch.py:449-450 is unnecessary — that blink runs after _wait_resilver reports completion — but harmless. Filtering Rbld PDs out of the status --locate risky list is correct. All stdlib.

**How to verify the fix**

tests/test_locate.py: blink_disk on a Disk(pd_state="Rbld") and on a Disk whose pool is mid-resilver (mock zfs.poll_resilver_status) returns refused and spawns no subprocess; force=True still blinks.

<details><summary>Verification trace</summary>

Traced blink_disk (locate.py:78) and every caller: watch._cmd_locate (watch.py:381-400), cli._locate (cli.py:67-78), and cli._status --locate (cli.py:50-58) — none checks pd_state, vdev_state, or pool resilver state before lighting an LED. common.assess grades pd_state Rbld as WARNING (common.py:146) and a DEGRADED leaf as WARNING (common.py:139), so on a RAID box a mid-rebuild PD lands in the status --locate risky list and can be perccli-locate'd by name with no refusal. CLAUDE.md sections 6 and 9 both state 'never light an LED on a resilvering/rebuilding disk', so the absence of any guard is a direct documented-safety-rule violation, which the rubric maps to P0. One evidence correction: zfs.attach_membership stores the LEAF's own state (zfs.py:124), not the vdev's, so during a tank resilver the healthy siblings show ONLINE/NORMAL and are NOT auto-blinked; the ZFS-side status --locate mostly blinks the FAULTED member — which is the legitimate pull target. The unguarded exposure is therefore (a) RAID-mode Rbld PDs and (b) any manual locate/watch-[l] aimed at a disk that is actively resilvering. The finding's core claim (no guard anywhere) is real; only its tank scenario is overstated.

</details>

## F-007 — raid replace never starts the rebuild and reports false success, because 'Not in progress' is treated as done

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/raid_actions.py:125`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: Locate LED stays blinking on the rebuilding bay for the entire rebuild in raid replace
- **Also independently reported as**: raid replace declares "[+] replace complete" when the rebuild never started ("Not in progress" ambiguity, start_rebuild unchecked)

**Evidence**

`if not st["done"] and st["pct"] == 0.0:\n        hba_raid.start_rebuild(d.bay)` — hba_raid.rebuild_progress (hba_raid.py:398) sets done=True when perccli says 'not in progress', which is exactly what a freshly inserted, never-rebuilt drive reports. So start_rebuild is skipped and _wait_rebuild's first poll immediately prints 'rebuild complete'.

**Failure scenario**

R640 RAID box: `b2ctl raid-replace 32:1`, member failed out, operator inserts the new drive, controller has no auto-rebuild policy for it -> perccli reports 'Not in progress' -> b2ctl skips start_rebuild, prints 'rebuild complete on 32:1' within 3 s and audits success, while vd0/raid1 stays Dgrd with the new drive untouched. The next member failure destroys the volume. Same false 'complete' if the user Ctrl-Cs before inserting anything.

**Suggested fix**

Disambiguate 'never started' from 'finished': after insertion, if rebuild_progress reports not-in-progress, always issue start_rebuild and verify it began (poll again / check PD state); declare success only when the PD state returns to Onln (parse `perccli /cC/eE/sS show` state), otherwise report 'rebuild not started — check perccli'.

*Verifier note:* The suggested fix is right but incomplete: fixing only the pre-check at :125 is not enough because _wait_rebuild reuses the same rebuild_progress and will still declare done on the next 'not in progress'. Disambiguate at the source — re-parse the slot's PD state (hba_raid already has _parse_pd_rows over `/cX/eall/sall show all`, stdlib run()) and treat 'not in progress' as complete only when state is Onln; if UGood/Offln/Rbld-failed, call start_rebuild, check its (ok,out) (currently discarded), and audit failure with the perccli error if it did not start. Same defect as finding 111. | Merged duplicate's note: Fix is correct: call hba_raid.locate(d.bay, False, dry_run=dr) immediately after the insertion input() at :115 (including in its except branch), keeping :128 as a redundant safety-off. Consider wrapping :124-128 so the LED is also cleared if an exception escapes the rebuild wait. | Merged duplicate's note: This finding's fix is the better-specified of the pair: after insert, re-parse the slot's PD state via the existing _parse_pd_rows over `/cX/eall/sall show all` and treat 'not in progress' as done only when state is Onln; otherwise start_rebuild, verify its (ok,out), and audit failure with the perccli error if it refuses. Must also be applied inside _wait_rebuild's done condition, not just the :125 pre-check. Deduplicate with 28 when applying.

**How to verify the fix**

tests/test_raid_actions.py (new file per repo layout — module currently has no tests)::test_replace_starts_rebuild_when_controller_idle — mock rebuild_progress to {'pct':0.0,'done':True} and assert start_rebuild is called and no success message until PD state is Onln.

<details><summary>Verification trace</summary>

Traced: hba_raid.rebuild_progress (hba_raid.py:398) sets done = ('not in progress' in out.lower()) or pct >= 100.0, so a rebuild that never began reads as done. raid_actions.replace:124-127 then skips start_rebuild (condition `not st['done'] and st['pct']==0.0` is False) and _wait_rebuild's first 3s poll hits the same 'not in progress' -> prints 'rebuild complete' and end_op(success). Realistic on the R640: PERC autorebuild disabled, foreign config on a used replacement drive (blocks auto-rebuild until cleared), or drive still spinning up at the poll. Note it also false-completes even when start_rebuild IS called and fails (its (ok,out) is discarded at :126). No guard elsewhere: _wait_rebuild has no PD-state check.

</details>

## F-008 — Dry-run executes destructive perccli commands for real: WRITE_CMDS omits perccli and is matched against args[0] verbatim

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/safety.py:15`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

safety.py:15 `WRITE_CMDS = {"zpool", "wipefs", "sgdisk", "dd"}`; common.py run_check gates dry-run with `args[0] in _safety.WRITE_CMDS`; hba_raid.py:374/380/386/417/438/444 call `run_check([_tool(), _pd(...), "set", "offline"], dry_run=dry_run)` etc., where `_tool()` returns a perccli path (e.g. /usr/sbin/perccli) that is never in the set — so dry_run=True falls through and the command runs.

**Failure scenario**

On the R640 RAID box the operator enables dry-run (cli --dry-run or the watch toggle at watch.py:32) and walks the RAID replace/destroy flow: raid_actions passes dry_run=dr into hba_raid.set_offline/set_missing/add_vd/del_vd, but `perccli ... set offline`, `set missing`, and `/c0/vN del force` execute for real — a VD member is dropped or the whole virtual disk is deleted during what the UI reports as a preview (safety.end_op even prints 'dry-run preview — nothing changed').

**Suggested fix**

In common.run_check compare `os.path.basename(str(args[0]))` (strip any config-resolved absolute path) against WRITE_CMDS, and add "perccli", "perccli64" (and "zfs" for future mutating zfs calls) to WRITE_CMDS in safety.py. Keep read-only perccli 'show' calls on run(), which they already use.

*Verifier note:* Fix is correct and stdlib-only: in common.run_check compare os.path.basename(str(args[0])) against WRITE_CMDS, and add 'perccli' and 'perccli64' to the set in safety.py (basename handles both config overrides and shutil.which paths). Optionally distinguish perccli read calls, but they already go through run() not run_check(), so no false suppression occurs. Add a regression test that calls hba_raid.set_offline(dry_run=True) with a mocked absolute _tool() path and asserts subprocess.run is never invoked.

**How to verify the fix**

tests/test_common.py: monkeypatch subprocess.run to fail the test if invoked, assert run_check(["/usr/sbin/perccli", "/c0/e32/s2", "set", "offline"], dry_run=True) returns (True, "") without executing; same for bare "perccli64".

<details><summary>Verification trace</summary>

Traced the full chain: common.run_check (common.py:56-64) suppresses only when args[0] is verbatim in safety.WRITE_CMDS = {zpool, wipefs, sgdisk, dd}. hba_raid._tool() returns config.tool()'s result — a config-override path or shutil.which() absolute path (config.py:85-89), or bare 'perccli64'/'perccli' — none of which match. raid_actions.replace (:104-106), offline (:153-155), create_vd (:186), hotspare (:257) and del_vd (:279) all pass dry_run=dr into hba_raid wrappers that call run_check([_tool(), ...], dry_run=dry_run) with no other guard; the code plainly expects run_check to suppress (raid_actions.py:122 prints '[dry-run] would start rebuild' AFTER set offline/missing already ran, and safety.end_op prints 'dry-run preview — nothing changed'). So on the R640 RAID box a dry-run replace really fails a member out ('set offline' + 'set missing') and a dry-run del-vd really runs '/cN/vN del force' — data loss presented as a preview. No test catches it because sim's fake perccli is also resolved to an absolute path.

</details>

## F-009 — _replace_member ignores _wait_resilver result and auto-detaches old member + lights pull-LED even when resilver ended with errors or is still running

- **Priority**: P0 (Critical)
- **Location**: `codes/b2ctl/watch.py:445`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Line 444-450: `if not _DRY_RUN:\n    _wait_resilver(pool)\nif detach_old:\n    _detach_if_lingers(pool, _pool_dev(d))\n    ...print(f"please pull bay {d.bay...} ... blinking LED"); locate.blink_disk(d, ...)`. _wait_resilver returns False on "completed WITH ERRORS" (line 413) but the return value is discarded; the same ignore exists at lines 176-179 (_assign_free_disk choice 3) and 663-665 (_cmd_swap). ZFS deliberately leaves the replacing vdev intact when the resilver had errors so the old disk's data stays reachable; force-detaching it discards the only copy of unreconstructed blocks.

**Failure scenario**

On tank (raidz1, 3 disks + spare): operator runs [s]wap or offload replace-onto-spare; during the resilver another member throws read errors, so `zpool status` ends with "resilvered ... with N errors". watch prints "resilver completed WITH ERRORS" but immediately runs `zpool detach tank <old>` and blinks the old disk's LED telling the operator to pull it. Detach + pull discards the last good copy of the damaged blocks; combined with the zfs.py:329 misparse (see separate finding) this can even fire while the resilver is still in progress, blinking a resilvering disk (CLAUDE.md section 9: never auto-detach, never light an LED on a resilvering disk).

**Suggested fix**

Capture the result: `ok = True if _DRY_RUN else _wait_resilver(pool)`. If not ok: print `run: zpool status <pool>` guidance, call safety.end_op(op_id, False, ...) and return False WITHOUT detaching or blinking. Apply the same guard at lines 176-179 and 663-665 (route _cmd_swap through _replace_member).

*Verifier note:* Fix is correct. Sharpen: `ok = True if _DRY_RUN else _wait_resilver(pool)`; when not ok, print recovery guidance, call safety.end_op(op_id, False, ...) and return False before the detach_old block. Apply identically at 176-179 and 663-665 — in _cmd_swap the add_spare(old disk) step must also be skipped on error, since the old disk may still be part of the replacing vdev. Consider making _detach_if_lingers require its own confirm so a lingering replacing vdev is never silently torn down.

**How to verify the fix**

tests/test_watch.py::test_replace_member_stops_on_errored_resilver — patch zfs.poll_resilver_status to return {completed: True, has_errors: True}, assert zfs.detach and locate.blink_disk are NOT called and safety.end_op records failure.

<details><summary>Verification trace</summary>

Traced: _wait_resilver (watch.py:404-417) returns False on 'completed WITH ERRORS' (413) and True on clean completion (415), but the caller at line 445 discards it; lines 446-450 then unconditionally run _detach_if_lingers (zpool detach, watch.py:420-425) and print 'please pull bay X' + blink the old disk's LED. The _confirm_op box at line 435 lists only the zpool-replace command, so the detach is never confirmed — a literal CLAUDE.md section 9 violation ('Never auto-resilver/detach without confirmation'). Same discarded-result pattern verified at 176-179 (_assign_free_disk choice 3) and 663-665 (_cmd_swap, which also detaches then re-adds the old disk as spare regardless of resilver outcome). On tank (raidz1, single-disk tolerance) an errored resilver leaves the replacing vdev holding the old disk as the only copy of unreconstructed blocks; force-detaching it plus instructing the operator to pull that bay is a data-loss path. The 'fires while resilver still running' half of the scenario depends on a separate zfs.py poll_resilver_status finding and is not part of this verdict; the ignore-result defect stands on its own.

</details>

# P1 (High) findings

## F-010 — Backend auto-detection trusts non-empty stdout, so sas2ircu's error banner selects IT mode on a RAID box

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/backend.py:151`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: Auto-detect picks ITBackend whenever the sas2ircu binary runs, because run() truthiness tests stdout, and sas2ircu prints its banner + 'MPTLib2 Error 1' to stdout even with no LSI controller

**Evidence**

`if run([sas, "list"]): return ITBackend()` — common.run ignores the exit code. Real sas2ircu with no LSI controller prints its banner + "SAS2IRCU: MPTLib2 Error 1" to stdout and exits non-zero, so run() returns truthy text. hba.py:142 `return bool(run([_cfg.tool("sas2ircu"), "list"]))` has the identical flaw.

**Failure scenario**

On the R640 (PERC H730P, RAID mode) the operator runs `b2ctl install --with-tools`, which installs BOTH tools (§3). Next `b2ctl status` auto-detects: sas2ircu prints its banner+error -> ITBackend chosen -> members behind the VD disappear from the table, the PERC VD /dev/sda shows as a raw "disk", ZFS actions (wipe/assign) are offered against the hardware VD, and perccli actions are refused by _require_raid().

**Suggested fix**

Require evidence of an actual controller, not just output: reuse the `re.findall(r"^\s*(\d+)\s+SAS", out, re.MULTILINE)` parse from _detect_sas2ircu_controllers (non-empty -> IT), and make hba.have_sas2ircu use the same check. Alternatively extend common.run to also return the exit code and require rc==0.

*Verifier note:* Same as finding 39: the controller-table regex fix must also rework the isfile fallback at backend.py:156-164 to distinguish 'executed OK, zero controllers' (fall through to perccli) from 'binary present but cannot execute' (warn + force IT), otherwise a working sas2ircu on a RAID box still lands in IT-mode. Also apply the parse inside hba.have_sas2ircu(). The alternative of extending common.run to return exit codes would touch every caller; the localized run_check-based or regex-based check is the smaller change. | Merged duplicate's note: The suggested parse-based fix is correct but incomplete: after fixing line 151, a RAID box whose sas2ircu executes fine but reports zero controllers would fall into the os.path.isfile fallback (lines 156-164) and still force IT-mode with a spurious 'failed to execute' warning. Distinguish three cases: (1) ran and controller table parsed (regex ^\s*\d+\s+SAS) -> IT; (2) ran cleanly but zero controllers -> fall through to perccli detection; (3) exec failure (run_check exception / empty output with binary present) -> keep the current libc6-i386 warning + force IT. Apply the same parse in hba.have_sas2ircu(), since core.scan() gates bay_map on it. Stdlib-only, consistent with house style.

**How to verify the fix**

tests/test_backend.py::test_detect_ignores_sas2ircu_error_banner — mock run so `sas2ircu list` yields the banner + "MPTLib2 Error 1" (no controller table) while perccli reports Controller Count = 1; assert RaidBackend is selected.

<details><summary>Verification trace</summary>

Duplicate of finding 39; independently verified. run() ignores returncode (common.py:48), so any stdout from sas2ircu selects ITBackend at backend.py:151; even a falsy run() result still selects IT via the isfile fallback at lines 156-164 whenever the binary is installed. install.sh --with-tools installs both tools without setting controller.mode, making the R640 auto-detect scenario reachable. Downstream claims verified: hba.enumerate_disks/lsblk would list the PERC VD /dev/sda as a raw disk, raid_volumes() returns [] on ITBackend (no HW table), and raid_actions._require_raid() (raid_actions.py:30) refuses perccli actions when backend.name != 'raid'. hba.py:142 bool(run(...)) confirmed.

</details>

## F-011 — start_selftest never passes -d <dtype>, so RAID-mode (megaraid passthrough) burn-in cannot start the self-test it later polls

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/burnin.py:25`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Line 25: `return run_check([sc, "-t", kind, dev], dry_run=dry_run)` — no -d argument — while the polling/verdict side does use it (line 67 `selftest_status(d.dev, d.smart_dtype)`, line 127 `_wait_selftest(d.dev, d.smart_dtype)`).

**Failure scenario**

Burning in a UGood spare on the R640 (dev is the shared VD /dev/sda, smart_dtype="megaraid,7" — the documented burn-in use case of vetting a spare): `smartctl -t long /dev/sda` without passthrough fails or addresses the VD, so either the run aborts with 'could not start self-test', or worse the poll with -d megaraid,7 reads the drive's PREVIOUS self-test log and assess() emits a stale PASS/FAIL for a test that never ran.

**Suggested fix**

Add a dtype parameter to start_selftest (insert ["-d", dtype] when non-empty, mirroring selftest_status line 35) and pass d.smart_dtype at the call in run() (line 119).

*Verifier note:* Suggested fix is correct and stdlib-only: add dtype parameter to start_selftest, insert ['-d', dtype] when non-empty (mirroring line 35), pass d.smart_dtype at line 119. Also update tests/test_burnin.py TestStartSelftest to assert the -d argv for a megaraid disk, since the existing test pins the current (broken) argv exactly.

**How to verify the fix**

tests/test_burnin.py: start_selftest("/dev/sda", "long", dtype="megaraid,7") builds a cmd containing ["-d", "megaraid,7"]; run() on a helpers._disk(smart_dtype="megaraid,7") asserts the -t invocation includes it.

<details><summary>Verification trace</summary>

Traced: start_selftest builds [smartctl, -t, kind, dev] with no -d, while selftest_status (line 35) and its callers in run() (lines 67, 127) do pass d.smart_dtype — the asymmetry is real and the module clearly intends RAID support. hba_raid.enumerate_disks() gives hidden UGood PDs dev=ctrl_dev (the shared VD /dev/sdX) plus smart_dtype='megaraid,<DID>', and in_pool is False for them, so the burnin guard passes. Reachability nuance: watch's [b] menu cannot hit this (_avail_for_aux at watch.py:703 filters 'not d.smart_dtype'), but the CLI path `b2ctl burnin <bay|serial>` resolves via core.scan by bay/serial with no such filter, so the R640 spare-vetting scenario is reachable. Most likely outcome is smartctl -t long on the PERC VD exits nonzero and run() aborts at lines 120-122 (broken action path); if smartctl happens to exit 0 the stale-log false-PASS variant applies. Either way RAID-mode burn-in cannot work.

</details>

## F-012 — read_scan hard timeout of 600 s kills badblocks after 10 minutes although a full surface scan takes hours, then misreports it as disk errors

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/burnin.py:56`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: Full read-surface scan is killed by run_check's 600 s timeout — burn-in --scan always aborts and reports errors on real disks

**Evidence**

Line 56: `return run_check([bb, "-sv", "-b", "4096", dev], dry_run=dry_run, timeout=600)` directly contradicts the module's own message at line 129: "read-surface scan (badblocks, read-only) — may take hours...". run_check converts TimeoutExpired into (False, 'timed out') which run() prints at line 132 as "read scan reported errors".

**Failure scenario**

`b2ctl burnin <bay> --scan` on a 1 TB SSD (35-60+ min even at full SATA speed, longer behind the SAS2308): badblocks is killed at 600 s, the operator is told the scan 'reported errors' (false — it was aborted), and the flow still proceeds to print a PASS/WARN verdict from SMART alone — the disk enters a pool with no completed surface scan while the operator believes it either errored or was scanned.

**Suggested fix**

Run badblocks with no deadline (plumb timeout=None through run_check, or use subprocess.Popen streaming the -sv progress line to the terminal), and distinguish TimeoutExpired from a nonzero exit in the printed message; record the scan result in the final verdict/reasons instead of only printing and continuing.

*Verifier note:* Fix as suggested, with one detail: run_check's signature is `timeout: int = 120` — pass timeout=None through to subprocess.run (subprocess accepts None = no deadline), which is a one-word type-hint change and stdlib-only. Additionally the scan outcome (including a genuine badblocks failure) is only printed and never folded into the verdict/reasons or the exit code — a real bad-sector result still ends '[PASS] safe to add to a pool'; fold sok into assess reasons or force at least WARN on scan failure, and distinguish TimeoutExpired from nonzero exit in the message. | Merged duplicate's note: Same fix as 63: plumb timeout=None through run_check to subprocess.run (stdlib supports None natively) rather than picking another arbitrary ceiling; distinguish TimeoutExpired from a nonzero badblocks exit in the printed message; and fold the scan result into the final verdict instead of print-and-continue. Deduplicate with finding 63 — one patch resolves both.

**How to verify the fix**

tests/test_burnin.py: fake badblocks script sleeping >600 s (scaled) still completes without being killed; a simulated timeout result must not print 'reported errors' and must surface as an aborted-scan warning in the verdict.

<details><summary>Verification trace</summary>

Traced: run_check (common.py:53-70) passes timeout to subprocess.run; TimeoutExpired is caught by the blanket 'except Exception' and returned as (False, 'Command ... timed out after 600 seconds'). burnin.run() line 131-132 renders any not-ok as 'read scan reported errors' and continues to a SMART-only verdict, contradicting the module's own 'may take hours' message at line 129. A full read of a 1 TB SSD cannot finish in 600 s even at line rate, so --scan is unconditionally killed and misreported on every real disk in the fleet (870 EVO 1TB). No guard elsewhere prevents this; only the dry-run path skips it.

</details>

## F-013 — b2ctl --dry-run rollback executes the real zpool command — dry-run not propagated

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/cli.py:449`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: rollback ignores --dry-run and executes the display hint verbatim (create-op hint contains a '# WARNING' comment)

**Evidence**

`ok, out = run_check(cmd)` — no dry_run kwarg, so run_check's WRITE_CMDS suppression never engages even though main() set watch._DRY_RUN. begin_op at line 444 is also called without dry_run (writes a real snapshot and a non-dry audit entry). Docs promise `b2ctl --dry-run <cmd>` = "preview what commands would run — no writes" (docs/user-guide-en.md:728), and every watch/raid action passes dry_run through.

**Failure scenario**

Operator previews a rollback with `b2ctl --dry-run rollback 20260704-...-replace` and answers y expecting the documented preview. cli runs the stored `zpool detach tank <dev>` for real — detaching a member of the raidz1 pool the operator did not intend to touch, dropping tank's only redundancy margin.

**Suggested fix**

In _rollback_cmd, import watch._DRY_RUN (or accept a dry_run arg from main) and call run_check(cmd, dry_run=watch._DRY_RUN) and safety.begin_op(..., dry_run=watch._DRY_RUN)/end_op(..., dry_run=watch._DRY_RUN); print the [DRY-RUN] preview line.

*Verifier note:* Fix is correct: pass dry_run=watch._DRY_RUN into run_check, begin_op, and end_op, and print a [DRY-RUN] preview of the hint command before/instead of executing. All stdlib. | Merged duplicate's note: Fix is right: pass dry_run=watch._DRY_RUN at cli.py:449 (one-line, matches every other call site). For the create hint, either store a machine argv separately from the display hint in the audit entry (best), or minimally strip the comment before splitting: hint.split('#')[0].split(). Both stdlib.

**How to verify the fix**

tests/test_cli.py::test_rollback_honors_dry_run — set watch._DRY_RUN=True, seed a log entry with rollback_hint 'zpool detach tank X', monkeypatch input->'y' and subprocess.run recorder; assert no zpool process is spawned and the entry status becomes 'dry_run'.

<details><summary>Verification trace</summary>

Traced: main() (cli.py:608-610) sets watch._DRY_RUN on --dry-run, but _rollback_cmd calls run_check(cmd) at 449 with no dry_run kwarg; common.py run_check only suppresses writes when dry_run=True is passed explicitly (it never reads watch._DRY_RUN). safety.begin_op at 444 is likewise called without dry_run, so a real snapshot is captured (safety.py:72) and a non-dry audit entry written. Docs promise 'b2ctl --dry-run <cmd> | preview what commands would run — no writes' (user-guide-en.md:728). Every other mutating path (watch.py:170/438/474, raid paths) propagates _DRY_RUN, confirming this is an omission, not design. The stored hint (e.g. `zpool detach tank <dev>`) executes for real under the documented preview flag. Mitigation noted: the operator still sees the exact command and must answer y — hence P1, not P0.

</details>

## F-014 — load() crashes every b2ctl command on valid-JSON-but-wrong-shape config, breaking the 'malformed -> defaults' contract

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/config.py:56`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: config.load() promises 'malformed -> defaults' but structurally-wrong JSON (string where object expected) raises AttributeError and kills every command

**Evidence**

Line 56 `for k, v in user.get("tool_paths", {}).items():` (and line 59-63 `ctrl.get(...)`) assume dicts, while line 68 catches only `(json.JSONDecodeError, OSError, KeyError)` — an AttributeError from `{"tool_paths": "/usr/sbin"}`, `{"controller": "raid"}`, or a top-level list is uncaught. The module docstring (line 3) promises 'Missing or malformed -> all defaults apply'.

**Failure scenario**

Operator hand-edits /etc/b2ctl/config.json and sets `"tool_paths": "/usr/sbin"` (a plausible mistake): the next `b2ctl status` — the read-only main path — tracebacks with AttributeError at the first lazy _get() call in tool(), and watch/locate/every subcommand are equally dead until the file is fixed.

**Suggested fix**

Validate shapes before use (isinstance(user, dict), isinstance(user.get("tool_paths"), dict), isinstance(ctrl, dict)) and/or add AttributeError/TypeError to the except tuple; print one stderr warning and fall back to defaults per the documented contract.

*Verifier note:* Suggested fix is correct. Prefer isinstance guards (isinstance(user, dict), isinstance(user.get("tool_paths"), dict), isinstance(ctrl, dict)) over just widening the except tuple: a broad except mid-merge returns a partially-merged cfg, and per-section guards let good sections still apply. Also extend validate() (line 162-168) to flag non-dict top-level/sections, since today it reports 'config ok' for exactly the file that crashes load(). All stdlib. | Merged duplicate's note: Fix is correct as written (isinstance shape guards preferred; widening the except tuple plus a one-line stderr warning is an acceptable fallback but returns a partially-merged cfg if the error fires mid-merge). Extending validate() to flag non-dict sections closes the 'config ok but crashes' inconsistency.

**How to verify the fix**

tests/test_config.py: with CONFIG_PATH pointed at tmp files containing {"tool_paths": "x"}, {"controller": "raid"}, and [1,2], load() returns the defaults without raising.

<details><summary>Verification trace</summary>

Traced: load() line 56 calls .items() on user.get("tool_paths", {}) and lines 59-63 call .get() on user["controller"]; a string/list value raises AttributeError, and the except tuple at line 68 only covers (JSONDecodeError, OSError, KeyError). Every command triggers load() via backend._detect_backend() -> config.controller_mode() (backend.py:143), and no broad except exists upstream in cli/core/backend, so status/watch/locate all traceback. This directly contradicts the line-3 docstring contract 'Missing or malformed -> all defaults apply'. Not P0: no data loss and requires a hand-mangled config file, but a wrong-shape hand-edit is realistic input the module promises to survive.

</details>

## F-015 — Ghost-row cleanup after SMART uses exact serial equality while ghost creation used prefix matching — SAS drives with no lsblk SERIAL and a truncated sas2ircu serial stay as permanent phantom CRITICAL GHOST rows

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/core.py:53`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

disks = [d for d in disks if not (d.health == "GHOST" and d.serial in real_serials)] — but get_ghost_disks (hba.py:207) matches os_serial.startswith(serial) or serial.startswith(os_serial), and udev_rescue_ghost (hba.py:271) also demands row SERIAL == serial exactly. The code itself documents the trigger (core.py:47: 'Enterprise SAS drives often have no SERIAL in lsblk') and the truncation is why the fuzzy fallback exists at all.

**Failure scenario**

Enterprise SAS drive: lsblk SERIAL empty, sas2ircu reports a truncated serial (e.g. 'S5Y1NR0R' vs SMART's 'S5Y1NR0R123456'). get_ghost_disks creates a ghost (runs pre-SMART), udev rescue runs and 'fails' (exact-match check), SMART then fills the real serial, but 'S5Y1NR0R' not in real_serials → the healthy disk is shown twice: its real row plus a CRITICAL 'Ghost / OS Rejected' row, on every status/watch refresh — plus a pointless udev trigger each scan (compounds the P0 above).

**Suggested fix**

Extract one _serial_match(a, b) helper (mutual startswith, both non-empty) shared by attach_bays, get_ghost_disks, udev_rescue_ghost, and this drop filter; drop a ghost when any real serial fuzzy-matches ghost.serial.

*Verifier note:* Shared _serial_match(a, b) (both non-empty, mutual startswith) for the drop filter is correct — require ghost.serial non-empty so an empty-serial ghost is never dropped by accident. Note the helper does NOT fix udev_rescue_ghost's success check for no-SERIAL SAS disks (lsblk serial is empty, fuzzy cannot match empty): either accept that rescue reports False there and rely on the fixed drop filter, or verify rescue by comparing the block-device set before/after the trigger instead of serial equality.

**How to verify the fix**

tests/test_core.py::test_ghost_dropped_when_sas2ircu_serial_is_prefix — fake lsblk row with SERIAL='', bay_map {'S5Y1NR0R':'1:4'}, smart.read stub setting serial='S5Y1NR0R123456'; assert scan() output has no GHOST row.

<details><summary>Verification trace</summary>

Traced the asymmetry: ghost creation uses mutual startswith (hba.py:207), bay attach uses it too (hba.py:189), find_sg uses substring (hba.py:252) — but the drop filter at core.py:53 uses exact set membership and udev_rescue_ghost (hba.py:271) demands exact lsblk SERIAL equality. For an enterprise SAS drive with empty lsblk SERIAL (the case core.py:47's own comment documents): ghost is created, rescue can never report success (lsblk SERIAL is empty, never == serial), SMART then fills the full serial on the real row, and if sas2ircu's serial is a prefix/truncation of SMART's, `ghost.serial in real_serials` is False -> the disk renders twice (healthy row + permanent CRITICAL 'Ghost / OS Rejected' row) and udev triggers fire on every scan. The prefix-mismatch premise is credible because the codebase added fuzzy matching in three places specifically for it; I could not verify actual sas2ircu truncation on hardware, but the exact-vs-fuzzy inconsistency is a defect regardless. Not reachable on the current Samsung-SATA boxes (lsblk has SERIAL), but enterprise SAS is an explicitly supported input.

</details>

## F-016 — attach_bays overwrites Disk.bay with the bay_map.json display label, but every perccli action (_pd: locate/set_offline/set_missing/start_rebuild/set_jbod/add_hotspare) uses Disk.bay as the controller enc:slot selector

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/hba_raid.py:325`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: attach_bays/get_ghost fuzzy serial-prefix matching is duplicated verbatim between hba.py and hba_raid.py (and a third variant in core.py), so fixes will diverge
- **Also independently reported as**: RAID-mode mutating perccli actions address the display-remapped bay label, not the controller enc:slot

**Evidence**

d.bay = baymap.remap_slot(bm[d.serial], panels) — while _pd() (line 350-353) does enc,slot = enc_slot.split(':') → '/cC/eE/sS', and raid_actions.py/locate.py pass d.bay straight in (e.g. hba_raid.set_offline(d.bay), hba_raid.locate(disk.bay, True)). baymap.py's docstring says the front type=sas panel 'covers the PERC backplane', so a non-identity remap on the RAID backend is a supported config.

**Failure scenario**

bay_map.json with reverse_slots (or an enc:slot map override) on the RAID box — e.g. copied from the R620 since both nodes are kept config-identical: a disk physically in slot 2 of 8 displays as '32:5'; the [o]ffline action then runs `perccli /c0/e32/s5 set offline` — offlining the WRONG, healthy member of the RAID1, degrading the array; set missing + operator pull of the intended drive then takes the VD offline (data loss). Also breaks the §9 LED rule: locate can light a resilvering/rebuilding drive's bay instead of the pull target.

**Suggested fix**

Keep the controller-native selector separate from the display label: set d.ctrl_slot = m['bay'] at enumerate time (never remapped), have attach_bays write only the display d.bay, and make _pd()/locate()/raid_actions consume d.ctrl_slot. Validate numeric enc/slot in _pd and fail loudly otherwise.

*Verifier note:* The suggested split is right and stdlib-only. Minimal version: hba_raid.enumerate_disks already sets the controller-native enc:slot on every PERC PD (line 253/282) — store that in a new Disk field (e.g. d.ctrl_slot) there, make hba_raid.attach_bays write only the display d.bay, and switch raid_actions/locate.blink_disk to d.ctrl_slot. Add a numeric-only assertion in _pd() (enc.isdigit() and slot.isdigit()) so a display label can never silently become a selector again. | Merged duplicate's note: Fix is appropriate and matches the house style (baymap.py already exists as the shared home). Extract _serial_match(a, b) plus a shared attach_bays(disks, bm, panels) into b2ctl.baymap and use _serial_match in core.scan's ghost-drop filter too, so the exact/fuzzy inconsistency is resolved in the same change. | Merged duplicate's note: Fix is right but incomplete: rebuild_progress (used by _wait_rebuild and the raid_actions.replace:124 restart guard) also consumes d.bay, so the raw locator must feed it too. Store the raw perccli enc:slot on the Disk (new field in common.py Disk, e.g. pd_slot, set in hba_raid.enumerate_disks lines 253/282 and preserved by attach_bays before remapping bay); raid_actions/_loc use d.pd_slot or d.bay fallback. Keep _pick_member matching on the displayed d.bay (that is what the operator types). Validate with re.fullmatch(r"\d+:\d+", enc_slot) in _pd and have each action wrapper return (False, msg) instead of raising. Stdlib-only, no conflict with CLAUDE.md.

**How to verify the fix**

tests/test_hba_raid.py::test_actions_use_controller_slot_not_display_bay — panels [{'panel':'front','type':'sas','reverse_slots':true}], attach_bays a member at raw '32:2', then assert set_offline/locate build '/c0/e32/s2' (record args via monkeypatched run_check), while the rendered bay shows the remapped label.

<details><summary>Verification trace</summary>

Traced end-to-end: core.scan always calls RaidBackend.attach_bays -> hba_raid.attach_bays, which overwrites d.bay with baymap.remap_slot(bm[serial], panels) — including for HW members that were given the raw controller enc:slot at enumerate time (hba_raid.py:253). raid_actions.replace (:100-128), offline (:149-158), assign_perc (:221,:245,:257) and locate.blink_disk (:88-91) all pass d.bay into _pd()/hba_raid.locate as the perccli /cC/eE/sS selector. baymap.py's docstring explicitly states the front type=sas panel 'covers the PERC backplane', so a map override or reverse_slots on a RAID box is a supported, documented config; with one configured, set offline/missing/rebuild/jbod/hotspare/locate all target the wrong physical slot — offlining a healthy RAID1 member and lighting the wrong bay LED (a §9 violation). Not P0 because the default deployment (no bay_map.json, or identity remap) is unaffected — it requires a non-identity sas panel on the RAID node.

</details>

## F-017 — blink_many bypasses the perccli routing and dd-reads d.dev — in RAID mode it flickers the whole VD instead of one bay

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/locate.py:99`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Lines 99-100: `procs = [subprocess.Popen(["dd", f"if={d}", "of=/dev/null", ...]) for d in devs]` — raw dd on each dev with no is_perc_pd routing and no ledctl. The module's own header (lines 6-8) states dd on a PERC member's shared /dev/sda "would light the whole VD (wrong bay)". cli._status --locate (cli.py:55-58) feeds it `d.dev` for every risky disk.

**Failure scenario**

R640 RAID box, one PD is WARNING: `b2ctl status --locate` dd-reads /dev/sda (the VD device, shared by ALL members) — every bay's activity LED flickers, the operator cannot identify the failing disk and may pull a healthy member. With several risky PDs, N duplicate dd readers hammer the same VD. Also, a missing dd binary raises FileNotFoundError from Popen mid-comprehension and crashes `b2ctl status` while leaking already-started processes.

**Suggested fix**

Change blink_many to take Disk objects and delegate each to blink_disk (perccli for is_perc_pd, ledctl-else-dd otherwise), deduplicate devices, and wrap process creation in try/except so one failure doesn't crash status; resolve dd via _cfg.tool("dd") like ledctl (config.py already declares the knob at line 35).

*Verifier note:* Delegating each disk to blink_disk is directionally right but changes semantics — blink_disk sleeps per disk, so N disks would blink sequentially (N*seconds) instead of 'at once'. Better: partition the Disk list — for PERC PDs run hba_raid.locate(bay, True) for all, one shared sleep, then locate(bay, False) for all; for raw disks keep the parallel dd Popens (or ledctl on/off around one sleep), deduplicating dev paths. Resolve dd via _cfg.tool('dd') (knob exists, config.py:35) and wrap Popen in try/except OSError for robustness. Also honor the finding-60 resilver guard here. Stdlib only.

**How to verify the fix**

tests/test_locate.py: blink_many with a HW-member Disk asserts hba_raid.locate is called and dd is never spawned on the shared /dev/sda; duplicate devs produce one process; Popen raising FileNotFoundError does not propagate.

<details><summary>Verification trace</summary>

Traced: blink_many (locate.py:96-106) Popens raw dd on each dev with no is_perc_pd routing and no ledctl, and cli._status --locate (cli.py:55-58) feeds it d.dev for every WARNING/CRITICAL disk. In RAID mode both VD members and hidden PDs are synthesised with dev=ctrl_dev — the shared VD block device (hba_raid.py:244-252, 281) — so every risky PD triggers a duplicate dd read of /dev/sda: the whole VD's activity flickers and no single bay is identified, exactly what the module's own header (locate.py:6-8) says must be avoided. Wrong behavior on a realistic main-path input (status --locate on the R640) = P1. The secondary FileNotFoundError claim is technically true of Popen but practically unreachable: dd is in coreutils, an Essential package on Debian 13/Proxmox, so I discount that sub-claim; it does not change the verdict.

</details>

## F-018 — SAS failing health status ('FAILURE PREDICTION THRESHOLD EXCEEDED') is stored as 'FAILURE', which assess() never flags — dying SAS drive shows LEVEL NORMAL

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/smart.py:62`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

d.health = "PASSED" if m.group(1).upper() == "OK" else m.group(1).upper()  — regex r"SMART Health Status:\s*(\w+)" captures only the first word, so smartctl's real failing SCSI output 'SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]' yields health="FAILURE". common.py:168 checks only `if d.health == "FAILED":`.

**Failure scenario**

A SAS drive on the R620 starts predicting failure (IEC threshold exceeded) but has no grown defects yet: HEALTH column shows the odd word FAILURE while LEVEL stays NORMAL and render_details prints '[OK] all disks healthy' — the operator gets no WARNING/CRITICAL for a drive smartctl itself declares failing.

**Suggested fix**

Normalize in smart.py: set d.health = "PASSED" only when the captured status is OK, else "FAILED" (optionally keep the raw phrase for display); and/or harden common.assess to bump CRITICAL when d.readable and d.health not in ("PASSED", "UNKNOWN").

*Verifier note:* Suggested fix is correct and stdlib-only. Simplest safe form: d.health = 'PASSED' if status == 'OK' else 'FAILED' (keep the raw phrase in d.reasons or a new field for display). Hardening assess to treat any readable health not in ('PASSED','UNKNOWN') as CRITICAL is a good belt-and-braces addition and also covers future parse variants.

**How to verify the fix**

tests/test_smart.py — add test_sas_failure_prediction_maps_to_failed: feed a SAS dump containing 'SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED' through smart.read (patched run), assert d.health == "FAILED"; companion assess() test in tests/test_common.py asserting level CRITICAL.

<details><summary>Verification trace</summary>

Traced smart.py:60-62: regex r"SMART Health Status:\s*(\w+)" captures only the first word, so real smartctl SCSI output 'SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]' yields d.health='FAILURE'. common.assess (common.py:168) checks only d.health == 'FAILED', so no CRITICAL bump; nothing else in ui/assess reacts to the value. SAS drives are an explicitly supported path (module docstring, _parse_sas), and _parse_sas feeds only realloc (grown defects) into assess, so an IE-prediction failure with zero grown defects really does render LEVEL NORMAL. Current fleet is SATA SSDs (ATA path via 'test result:'), but SAS support is a first-class feature of the tool.

</details>

## F-019 — _handle_new_disk declares any hot-plugged device 'free' and offers the wipe menu even when the disk is an active pool member

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/watch.py:125`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

watch.py:122-125: `if not d.by_id: ... return` then unconditionally `_assign_free_disk(d, tbw)` — no `d.in_pool` / `d.is_spare` check, and _assign_free_disk opens with `print(f"  Disk {d.by_id or d.dev} is free.")` (line 129) plus option `[6] WIPE it blank`. Compare _cmd_assign (line 276) which filters `not d.in_pool`.

**Failure scenario**

Operator reseats an rpool mirror leg (routine cable/backplane check), or the replacement disk inserted during the spare-less [o]ffload flow is picked up by the next hotplug poll after the command returns: ZFS re-onlines/resilvers the disk, yet watch pops 'NEW DISK DETECTED', asserts the disk 'is free', and offers WIPE. Trusting the tool's claim and confirming runs zfs.wipe(): `sgdisk --zap-all` rewrites the whole-disk GPT while ZFS holds only -part3, so the rpool member's partition vanishes on next reboot — mirror leg lost on the boot pool.

**Suggested fix**

In _handle_new_disk, after core.scan_one(), refuse the free-disk menu when the scanned Disk is stale-state-inconsistent with 'free': if d.in_pool or d.is_spare or d.vdev_state, print its pool/vdev membership (e.g. 'already ONLINE in rpool/mirror-0 — no action') and return instead of calling _assign_free_disk. Stdlib only, single guard mirroring the _cmd_assign filter.

*Verifier note:* The suggested guard is right and implementable since scan_one fully populates membership. Simplify the condition to `if d.in_pool or d.is_spare:` — `d.vdev_state` alone is redundant once in_pool is checked (a non-member has no vdev_state). Print pool/vdev/state (e.g. 'already ONLINE in rpool/mirror-0 — no action') and return. Stdlib-only, no gotcha regressions.

**How to verify the fix**

tests/test_watch.py::test_handle_new_disk_refuses_in_pool_member — patch b2ctl.watch.core.scan_one to return helpers._disk(pool='rpool', vdev='mirror-0', by_id='/dev/disk/by-id/ata-X'), patch b2ctl.watch._assign_free_disk, call _handle_new_disk('/dev/sdb', {}), assert _assign_free_disk was NOT called.

<details><summary>Verification trace</summary>

watch.py:116-125: after the by_id guard, _handle_new_disk unconditionally calls _assign_free_disk, which prints 'Disk ... is free.' (line 129) and offers [6] WIPE. core.scan_one() delegates to scan(), which runs zfs.attach_membership (core.py:55), so a re-inserted pool member arrives with in_pool=True — yet no in_pool/is_spare check exists, unlike _cmd_assign's `not d.in_pool` filter (line 276). zfs.wipe runs `sgdisk --zap-all` on the whole disk (zfs.py:373-377), which succeeds even while ZFS holds only -part3 of an rpool member, destroying its GPT. The scenario is reachable: a reseated member reappears in the `new` set of the poll diff, and the replacement disk inserted during _offline_and_replace's blocking prompt is picked up by the next poll after the command returns, post-replace (now in-pool). The two [y/N] confirms exist, but the tool actively asserts the disk is free, defeating that safeguard.

</details>

## F-020 — _cmd_locate blinks any disk with no resilver/vdev-state guard, violating the 'never light an LED on a resilvering disk' rule

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/watch.py:399`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Lines 394-399: only GHOST is refused (`if chosen.dev == "-"`), then `ok, method = locate.blink_disk(chosen)` unconditionally. No check of chosen.vdev_state (INUSE/DEGRADED/replacing) or an in-progress resilver on chosen.pool. CLAUDE.md section 9 / section 6: never light a locate LED on a rebuilding/resilvering disk. The dd fallback in locate.blink() additionally issues a 5 s direct sequential read against the busy disk.

**Failure scenario**

tank's hot spare is INUSE resilvering after a member fault. Operator presses [l] to find the FAULTED disk to pull but enters the wrong bay/serial (the resilvering spare or a source member): watch happily blinks it — the universal 'pull me' signal — and the operator pulls a resilvering raidz1 device, taking the pool to UNAVAIL/data loss. Same gap in cli.py _locate (line 76).

**Suggested fix**

Before blink_disk: if chosen.pool and (chosen.vdev_state not in (None, 'ONLINE', 'OFFLINE', 'FAULTED', 'UNAVAIL', 'REMOVED', 'AVAIL') or zfs.poll_resilver_status(chosen.pool) shows an in-progress resilver on its pool) -> refuse with an explanatory message (allow only leaves whose state marks them as needing a physical pull). Share the guard with cli._locate.

*Verifier note:* Sharpen the proposed guard: a vdev_state whitelist alone is insufficient because resilver SOURCE members show ONLINE while the resilver runs. Correct rule: if chosen.pool and zfs.poll_resilver_status(chosen.pool) reports not completed (resilver in progress), refuse unless chosen.vdev_state is in (FAULTED, UNAVAIL, REMOVED, OFFLINE) — the states that legitimately need a physical pull — and always refuse INUSE/replacing leaves. Factor the check into locate.py or zfs.py and call it from _cmd_locate, cli._locate, and the status --locate path.

**How to verify the fix**

tests/test_watch.py::test_locate_refuses_resilvering_disk — disk with vdev_state='INUSE' (and a pool with resilver in progress), assert locate.blink_disk not called and a refusal message printed.

<details><summary>Verification trace</summary>

Traced: _cmd_locate (381-400) resolves the target and the only refusal is `chosen.dev == "-"` (GHOST, line 394); line 399 calls locate.blink_disk unconditionally — no check of chosen.vdev_state (INUSE spare mid-resilver, DEGRADED, replacing member) and no pool resilver-in-progress check, despite CLAUDE.md sections 6 and 9 stating 'never light a locate LED on a rebuilding/resilvering disk'. cli._locate (cli.py:67-76) has the identical gap, and `status --locate` (cli.py:50-58) even auto-blinks every WARNING/CRITICAL disk unguarded. The failure scenario is reachable on the real tank raidz1+spare box: during a spare resilver, one mistyped bay/serial at the [l] prompt blinks the resilvering disk and the operator pulls it → pool loses a second device → UNAVAIL. Not P0 because the tool only acts on an explicit operator-named target and prints what it is blinking, so it requires a misidentification rather than acting autonomously; but the project's own non-negotiable rule mandates a code-level guard that is absent.

</details>

## F-021 — _cmd_extend treats zfs.list_pools() dicts as pool-name strings — [e]xtend is unusable on the real two-pool servers

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/watch.py:712`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: _cmd_extend compares/passes zfs.list_pools() dicts as pool names — every cache/SLOG extend path is broken
- **Also independently reported as**: watch [e]xtend pool picker operates on list-of-dicts, so the flow is broken on real pools; _pick_pool() was duplicated instead of reused

**Evidence**

Line 712-713: `pool = pools[0] if len(pools) == 1 else _ask(f"  pool {pools}> ")` then `if pool not in pools:`. zfs.list_pools() (zfs.py:24-33) returns list[dict]. With multiple pools the user types a name string which is never `in` a list of dicts -> always "cancelled". With one pool, `pool` is a dict passed to zfs.add_cache/add_log -> subprocess raises TypeError('expected str... not dict'), and line 752's `e["pool"] == pool` never matches. Masked by tests: tests/test_watch.py lines 753/768/782 mock list_pools as ["tank"] (strings), not the real dict shape.

**Failure scenario**

Both production nodes have rpool AND tank. Operator presses [e], gets prompted with a raw dict-list dump, types "tank" -> "cancelled" every time. On a single-pool box, adding L2ARC fails with "expected str, bytes or os.PathLike object, not dict" and the remove path always says "no cache/log devices". The extend feature simply does not work from watch.

**Suggested fix**

Reuse the existing _pick_pool() (watch.py:61) which correctly returns pools[i]["name"]: `pool = _pick_pool()` and bail on None. Delete the ad-hoc selection.

*Verifier note:* Fix is correct: replace lines 712-714 with `pool = _pick_pool()` / `if not pool: return` — _pick_pool already handles 0/1/N pools and returns the name string. Update the three test_watch.py extend tests to mock list_pools with the real dict shape so the regression is caught. | Merged duplicate's note: Fix is correct: names = [p["name"] for p in pools]; pool = names[0] if len(names) == 1 else _ask(f"  pool {names}> "); validate `pool in names`. This also fixes the choice-3 token filter. Additionally update the three TestExtendAndBurnin tests to mock list_pools with real dict shapes ([{"name": "tank", ...}]) so the regression can't hide again; compare _pick_pool (watch.py:61-74) which handles the dicts correctly and could simply be reused here. | Merged duplicate's note: Fix as suggested: `pool = _pick_pool()`; if None print cancelled. Also update the extend tests to stub list_pools with [{"name": "tank", "health": "ONLINE", ...}] so shape drift is caught.

**How to verify the fix**

tests/test_watch.py::test_extend_uses_real_list_pools_shape — mock zfs.list_pools with [{"name":"rpool",...},{"name":"tank",...}] (match zfs.list_pools' real return), drive _cmd_extend choice 1, assert zfs.add_cache called with pool string "tank"; also fix the three existing extend tests' fixtures.

<details><summary>Verification trace</summary>

Traced fully. zfs.list_pools() (zfs.py:24-33) returns list[dict]. watch.py:712: `pool = pools[0] if len(pools) == 1 else _ask(f"  pool {pools}> ")`. Multi-pool (both production nodes have rpool+tank): the prompt dumps raw dicts, the typed name string is never `in` a list of dicts, so line 713-714 prints 'cancelled' every time — [e]xtend is unreachable. Single-pool: `pool` is a dict; it passes the `in pools` check, then zfs.add_cache/add_log build ['zpool','add','-f',<dict>,...] and subprocess raises 'expected str, bytes or os.PathLike object, not dict' (surfaced as ✗ failed via run_check's except). Remove path line 752 compares e['pool'] (str) == pool (dict), never true → always 'no cache/log devices'. Test masking verified: tests/test_watch.py mocks list_pools as ["tank"] (strings) in test_extend_add_cache/add_log_mirror/remove_aux (~lines 753/767/780). Contrast: _pick_pool (watch.py:61-74) and _cmd_destroy (603-609) both correctly index ['name'].

</details>

## F-022 — KeyboardInterrupt is only caught around select(); Ctrl-C at any prompt or during a resilver wait crashes watch with a traceback (and _confirm_op's bare input() also dies on Ctrl-D)

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/watch.py:800`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: EOF on stdin (Ctrl-D at the main prompt) becomes an infinite full-rescan busy loop instead of quitting
- **Also independently reported as**: watch.run() — the main select() loop, key-to-command dispatch table and hotplug new/gone detection — has no test at all

**Evidence**

Lines 799-802 guard only `r, _, _ = select.select([sys.stdin], [], [], POLL)` with `except KeyboardInterrupt`. _ask (line 54-58) catches only EOFError; _confirm_op line 109 uses raw `input("Proceed? [y/N]: ")` with no handler at all; _wait_resilver (404-417) is an unbounded `while True: time.sleep(2)` loop with no interrupt handling. cli.main() (cli.py:605) adds no top-level guard either.

**Failure scenario**

Operator at "  swap which #> " presses Ctrl-C to cancel (normal habit) -> KeyboardInterrupt propagates out of run() -> full traceback, watch exits. Worse: Ctrl-C during the hours-long tank raidz1 _wait_resilver (the only way to regain the terminal) aborts mid-workflow — safety.end_op is never called (ops.jsonl entry stuck 'pending'), _detach_if_lingers/spare messages are skipped, and Ctrl-D at the CONFIRM OPERATION box raises an unhandled EOFError.

**Suggested fix**

Catch KeyboardInterrupt (and EOFError) in _ask returning "", route _confirm_op's input() through _ask so cancel means No, and wrap the command-dispatch block (or _wait_resilver's loop body) in try/except KeyboardInterrupt that prints 'interrupted — resilver continues in ZFS; check: zpool status <pool>', finalizes safety.end_op, and returns to the main prompt.

*Verifier note:* Fix is right and stdlib-only. Concretely: add `except KeyboardInterrupt` to _ask returning "" (empty answer already means cancel/No everywhere); change _confirm_op line 109 to use _ask; wrap the command-dispatch block in run() (lines 806-831) in try/except KeyboardInterrupt printing 'interrupted — check: zpool status'. For pending safety ops, wrap _replace_member's post-begin_op section in try/finally or catch KeyboardInterrupt to call safety.end_op before re-raising to the dispatch handler. | Merged duplicate's note: Fix is correct and covers both cases: `line = sys.stdin.readline()`; `if line == "": print("bye"); return 0`; then `cmd = line.strip().lower()` so an interactive empty Enter still refreshes. | Merged duplicate's note: Fix is sound and stdlib-only (unittest.mock can patch select.select and sys.stdin). Extracting the dispatch to a module-level dict {key: callable} also removes the elif ladder; test run() by feeding one command then 'q' and asserting the right _cmd_* mock fired and 'b2ctl> ' was written once per event, plus a no-event poll tick writing nothing.

**How to verify the fix**

tests/test_watch.py::test_prompt_ctrl_c_cancels — patch builtins.input to raise KeyboardInterrupt inside _cmd_swap and assert it returns without exception; test_confirm_op_eof_means_no for the Ctrl-D case.

<details><summary>Verification trace</summary>

Traced: the only KeyboardInterrupt handler is watch.py:799-802 around select(). _ask (54-58) catches EOFError only; _confirm_op line 109 uses raw input() with no handler (Ctrl-D there raises unhandled EOFError); _wait_resilver (404-417) is `while True: time.sleep(2)` with nothing catching the interrupt; cli.main (cli.py:605-616) is `return args.func(args)` with no top-level guard. So Ctrl-C at any action prompt or during a multi-hour tank resilver wait propagates out of run() and main() as a full traceback. In _replace_member that abort also skips safety.end_op, leaving the ops.jsonl entry pending. Not P0: the resilver itself continues safely inside ZFS (no data damage), and the crash is on a deliberate interrupt rather than normal input — but a traceback + dead session on the operator's standard cancel gesture, mid-workflow, is broken behavior on realistic input.

</details>

## F-023 — can_detach approves detaching a leg of a 2-way mirror (rpool), defeating the Task C 'refuse if no redundancy remains' guard

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/zfs.py:280`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`online_others = [e for e in members if e["token"] != dev_token and e["state"] == "ONLINE"]` — one ONLINE sibling is enough to return True, so detaching either leg of the 2-disk rpool mirror passes; also vdevs that are neither raidz nor mirror (stripe leaves, 'replacing-0', 'spare-0') fall through to `return True` at line 283 with no member check at all.

**Failure scenario**

rpool = 2x 860 PRO mirror (ZFS-on-root). Operator uses [d]emote, picks one leg: can_detach True -> single [y/N] -> zpool detach + zpool add spare. rpool is now a single-disk boot pool with a 'hot spare' that can never resilver from a failed lone vdev; the next boot-disk failure makes the node unbootable and unrecoverable. CLAUDE.md Task C demanded refuse or warn+double-confirm exactly here. The stripe/replacing fall-through also makes _cmd_offload claim 'This disk is in a mirror. Detach instantly?' for non-mirror members.

**Suggested fix**

Return a redundancy signal: for mirrors require len(online_others) >= 2 to allow silent detach, otherwise have callers (watch._cmd_demote/_cmd_offload) print 'this removes the LAST redundancy of <pool>' and require a second explicit confirm (or refuse for demote); return False for vdevs that are neither mirror nor spare/replacing instead of True.

*Verifier note:* Fix is right and stdlib-only. Concretely: make can_detach return a tri-state or (ok, last_redundancy) tuple — safe only when len(online_others) >= 2; when ==1, callers (watch._cmd_demote, _cmd_offload) must print 'this removes the LAST redundancy of <pool>' and double-confirm (refuse outright for demote per Task C's stricter option); return False at line 283 for vdevs that are neither mirror nor spare-*/replacing-*. Update tests/test_zfs.py:88-93 which currently locks in the unsafe behavior, and note rpool detach also has the proxmox-boot-tool implication (CLAUDE.md section 9) worth surfacing in the prompt.

**How to verify the fix**

tests/test_zfs.py::test_can_detach_two_way_mirror_flags_no_redundancy — _MIRROR_STATUS fixture, assert can_detach (or its new redundancy flag) blocks/flags the 2-way case; tests/test_watch.py::test_demote_double_confirms_last_mirror_leg.

<details><summary>Verification trace</summary>

Traced zfs.py:278-283: one ONLINE sibling suffices, so can_detach returns True for either leg of the 2-disk rpool mirror; tests/test_zfs.py:88-93 asserts exactly that on the 2-disk _MIRROR_STATUS fixture, so the suite encodes the defect. _cmd_demote (watch.py:688-693) then does a single generic confirm ('demote X in rpool to a hot spare?') with no redundancy warning — but CLAUDE.md Task C explicitly requires 'refuse (or warn + double-confirm) if detaching would leave a vdev with no redundancy' and names rpool (the ZFS-on-root boot pool) as the target case. Detaching a 2-way-mirror leg leaves a single-disk boot vdev whose spare can never resilver a lone failed vdev. Also verified the line-283 fall-through: a stripe leaf (vdev==pool) or spare-0/replacing-0 leaf returns True with no member check, making _cmd_offload (watch.py:357-358) print 'This disk is in a mirror. Detach instantly?' for non-mirror members (harmless for stripe — zpool detach then errors — but wrong messaging). Not refutable as intentional: the test encodes implementation behavior, while the project spec demands the opposite.

</details>

## F-024 — can_offline ignores members nested in spare-N/replacing-N sub-vdevs, approving a second outage on an already-degraded raidz1

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/zfs.py:303`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`others = [e for e in topo.values() if e["pool"] == pool and e["vdev"] == vdev and e["token"] != dev_token]` — leaves inside a nested spare-0/replacing-0 carry vdev='spare-0' (per _parse's stack), so a FAULTED original plus its resilvering spare are excluded from 'others' and the remaining direct raidz1-0 leaves all read ONLINE. Contradicts the docstring 'an already-degraded vdev (offlining a 2nd could fail it)'.

**Failure scenario**

tank raidz1: disk A faults, hot spare auto-activates (spare-0: A FAULTED + spare). Operator picks [o]ffload on wearing disk B; can_offline returns True and _offline_and_replace proceeds to `zpool offline`. Mid-resilver ZFS bails with a confusing 'no valid replicas' after the scary DEGRADED confirm; once the spare has synced (FAULTED leaf lingering) ZFS permits it, leaving one raidz1 vdev with two non-ONLINE members and zero margin while the flow tells the user to pull bay B.

**Suggested fix**

In can_offline (and can_detach), treat leaves of nested sub-vdevs as members of the enclosing data vdev: while parsing, record the top-level data vdev (e.g. entry['data_vdev']) in _parse, or in can_offline also collect entries whose vdev is a spare-*/replacing-* child of the target vdev and require them ONLINE too; return False if any FAULTED/UNAVAIL/OFFLINE leaf shares the top-level vdev.

*Verifier note:* Fix direction is right and stdlib-only. Cheapest version: in _parse, also record the top-level data vdev per leaf (second element of vdev_stack, e.g. entry['top_vdev']); then can_offline requires every other leaf with the same top_vdev to be ONLINE. Apply the same top-vdev grouping to can_detach's mirror-member collection so a mirror leg under replacing-N is counted too.

**How to verify the fix**

tests/test_zfs.py::test_can_offline_false_during_spare_rebuild — _parse a raidz1 status with spare-0 (FAULTED old + ONLINE spare) nested inside raidz1-0 (add fixture to tests/helpers.py) and assert can_offline('tank', '<other member>') is False.

<details><summary>Verification trace</summary>

Traced _parse's vdev stack (zfs.py:53-80): leaves indented under spare-0/replacing-0 get vdev='spare-0'/'replacing-0', not the enclosing raidz1-0. So in can_offline (zfs.py:303-305) the FAULTED original AND its resilvering spare are both excluded from `others`; the remaining direct raidz1-0 leaves are ONLINE and the guard returns True, directly contradicting its docstring ('an already-degraded vdev (offlining a 2nd could fail it)'). Scenario reachability verified: an INUSE spare has vdev='spares'/state=INUSE so watch.py:367's AVAIL filter yields no spare and _cmd_offload falls into the can_offline/_offline_and_replace path (watch.py:374). Mid-resilver ZFS itself refuses the offline ('no valid replicas') after the scary DEGRADED confirm; once the spare has synced with the FAULTED leaf lingering, ZFS permits it — zero-margin raidz1 with two non-ONLINE members. Broken guard on the documented tank recovery path (hot-spare auto-resilver) = P1.

</details>

## F-025 — poll_resilver_status misreads an in-progress resilver with 'no estimated completion time' as completed-with-errors

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/zfs.py:329`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: poll_resilver_status has no test for scrub-in-progress or stale 'resilvered' scan lines, both of which it misclassifies
- **Also independently reported as**: poll_resilver_status cannot signal a failed `zpool status` — _wait_resilver spins forever at '0.0% done'

**Evidence**

Line 329: `if "resilvered" in out and "to go" not in out:` -> completed=True, has_errors=("with 0 errors" not in out). Real OpenZFS prints `517M resilvered, 24.83% done, no estimated completion time` early in a resilver (and when paused) — "resilvered" present, "to go" absent — so an in-progress resilver is reported as completed WITH errors. All fixtures (tests/helpers.py:83, tests/test_zfs.py:164,187, sim/bin/zpool:69) only emit the 'to go' form, so the suite never exercises this real output variant.

**Failure scenario**

Operator swaps a tank member onto the spare; the first `_wait_resilver` poll lands while ZFS has no ETA yet -> poll_resilver_status returns completed+has_errors -> watch prints "resilver completed WITH ERRORS" seconds after start, then (via the watch.py:445 bug) attempts `zpool detach` mid-resilver and tells the operator to pull the bay of a disk that is actively resilvering on raidz1.

**Suggested fix**

Treat completion positively: completed only when the scan line matches `resilvered .* in .* with \d+ errors` (or 'resilver in progress' is absent AND a completion line exists). E.g. `m = re.search(r"resilvered .* with (\d+) errors", out)` -> completed=True, has_errors=int(m.group(1))>0; keep %done/ETA parsing for the in-progress branch and return eta='unknown' when the 'no estimated completion time' phrase is present.

*Verifier note:* Suggested fix is correct and stdlib-only. Sharpen: branch on the in-progress marker first — if 'resilver in progress' in out, parse %done/ETA and set eta='unknown' when 'no estimated completion time' is present; only then treat re.search(r'resilvered .* in .* with (\d+) errors', out) as completion (has_errors=int(g1)>0). Also make _replace_member (watch.py:444-450) honor _wait_resilver's return and skip detach/LED on failure — the parser fix alone still leaves that path unguarded. | Merged duplicate's note: Fix direction is right. Concretely: gate completed=True on 'resilver in progress' being absent AND the resilver having been observed in progress at least once (or compare the 'on <date>' timestamp against the op start time); gate the % / ETA extraction on the scan line containing 'resilver in progress' so scrub lines return a distinct idle/none state. | Merged duplicate's note: Fix is sound and stdlib-only. Two refinements: distinguish 'command failed/empty' from 'ran but no resilver in scan line' (a subprocess.run with returncode capture, or a sentinel when out.strip() is empty); and wrap _wait_resilver's loop body to catch KeyboardInterrupt, print 'stopped watching — resilver continues in background; check zpool status <pool>', and return False instead of crashing watch.

**How to verify the fix**

tests/test_zfs.py::test_poll_resilver_no_eta_still_in_progress — feed status text containing 'resilvered, 24.83% done, no estimated completion time', assert completed is False, done == 24.83, has_errors is False.

<details><summary>Verification trace</summary>

Traced zfs.py:329-333: 'resilvered' in out and 'to go' not in out => completed=True, has_errors=('with 0 errors' not in out). Real OpenZFS 2.x prints '517M resilvered, N% done, no estimated completion time' early in a resilver and when paused — that output hits the completed branch and, since the in-progress text never contains 'with 0 errors', has_errors=True. Worse, watch.py:445 ignores _wait_resilver's return value, so after the false 'completed WITH ERRORS' message the flow still runs _detach_if_lingers (old token IS in topology mid-resilver, so zpool detach is attempted) and blinks the LED telling the operator to pull the bay while the raidz1 resilver is still running. Verified all fixtures (tests/helpers.py:83, tests/test_zfs.py:164/187, sim/bin/zpool:69) only emit the 'to go' variant, so the suite cannot catch this.

</details>

## F-026 — Destructive wipe paths untested: wipe_sg (raw dd zeroing, uncaught TimeoutExpired) and wipe (ignores labelclear/wipefs failures) have no unit tests

- **Priority**: P1 (High)
- **Location**: `codes/b2ctl/zfs.py:351`
- **Category**: test-gap
- **Verdict**: CONFIRMED
- **Also independently reported as**: wipe_sg lets subprocess.TimeoutExpired escape, crashing the whole watch session mid ghost-wipe
- **Also independently reported as**: wipe_sg's raw subprocess.run is unguarded — dd hang/timeout raises and crashes watch mid ghost-wipe

**Evidence**

zfs.py:351 `r = subprocess.run(["dd", ...], stderr=None, timeout=120)` — TimeoutExpired is not caught (unlike run/run_check) so a slow disk raises out of _wipe_ghost mid-flow; zfs.py:371-375 wipe() discards the ok of labelclear and wipefs and returns only sgdisk's result. Every test (test_watch.py:549,565) mocks zfs.wipe/wipe_sg; no test builds their argv, dry-run gate, or error paths.

**Failure scenario**

A worn SSD takes >120 s to sync 40 MB: dd raises subprocess.TimeoutExpired, _wipe_ghost crashes with a traceback after already half-zeroing the device, leaving the ghost in a worse state; or wipefs fails silently and wipe() reports success because sgdisk succeeded, so create-pool proceeds on a disk still carrying a ZFS label.

**Suggested fix**

Add tests pinning wipe_sg argv/dry-run string and the timeout behavior (then wrap subprocess.run in try/except TimeoutExpired returning (False, msg)); pin that wipe() surfaces a wipefs failure (then aggregate the three run_check results).

*Verifier note:* One caution on the proposed wipe() aggregation test: `zpool labelclear -f` legitimately exits nonzero on a disk with no ZFS label (the common wipe case), so the test should pin that a wipefs/sgdisk failure is surfaced while a labelclear 'no label' failure is tolerated — a blind AND of all three would break normal wipes of non-ZFS disks. | Merged duplicate's note: Fix is right and stdlib-only: wrap in try/except (subprocess.TimeoutExpired, OSError) and return (False, f'dd failed: {exc}') so _wipe_ghost prints the failure like every other action; a larger timeout (e.g. 300 s) is reasonable given fsync on a degraded disk. OSError also covers dd missing from PATH. | Merged duplicate's note: Fix is correct and stdlib-only. Catch subprocess.TimeoutExpired and OSError, return (False, ...) so _wipe_ghost's existing red '✗ failed' path prints; resolve the binary via config.tool("dd") like locate.py does. Keep stderr=None so dd's status=progress still streams.

**How to verify the fix**

tests/test_zfs.py::TestWipe::test_wipe_sg_dry_run_no_subprocess, ::test_wipe_sg_timeout_returns_false (patch subprocess.run to raise TimeoutExpired), ::test_wipe_runs_labelclear_wipefs_sgdisk_and_fails_if_any_fails.

<details><summary>Verification trace</summary>

Both cited behaviors verified in code: wipe_sg (zfs.py:351-357) calls subprocess.run with timeout=120 and no try/except (TimeoutExpired/OSError propagate), and wipe() (zfs.py:371-375) discards the (ok,out) of labelclear and wipefs, returning only sgdisk's result. Coverage gap verified: grep of codes/tests/ shows zero direct tests of zfs.wipe or zfs.wipe_sg — test_watch.py:547,549,564-572 mock them entirely, and test_zfs.py's action-wrapper section (add_spare/replace/create_pool/add_cache/add_log/remove_vdev/destroy_pool) never touches wipe/wipe_sg argv, dry-run strings, or error paths. As a test-gap finding this is P3 per the rubric; the underlying bugs are independently filed as findings 119 and 120.

</details>

# P2 (Medium) findings

## F-027 — Unguarded int() on controller.index config crashes every scan (status/watch) with a traceback

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/backend.py:63`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

ITBackend._all_indices: `return [int(setting)]` where setting comes straight from /etc/b2ctl/config.json via controller_index_setting(); hba_raid._ctrl_indices (hba_raid.py:90) has the identical `[int(setting)]`. config.validate() never checks index.

**Failure scenario**

Operator typos `"index": "0,1"` or `"index": "one"` in config.json (plausible when trying to select two controllers) -> ValueError inside core.scan() -> `b2ctl status`, `watch`, and every action that scans die with a raw traceback instead of a config error message; the box becomes unmonitorable until the file is hand-fixed.

**Suggested fix**

Wrap the conversion: `try: return [int(setting)] except ValueError: print warn 'invalid controller.index=<v> — using all'; return _detect_..._controllers() or [0]` in both backends, and add an index check to config.validate().

*Verifier note:* Suggested fix is sound and stdlib-only. Apply the try/except ValueError in BOTH backend.ITBackend._all_indices (backend.py:63) and hba_raid._ctrl_indices (hba_raid.py:90), warn with the offending value, and fall back to the detected controller list ([0] as last resort). Also add a validate() row for controller.index (ok if 'all' or int()-parseable, error otherwise) so `b2ctl check` catches the typo before a scan does. Kept at P2 rather than P0: the crash needs an invalid hand-edited config value, so it is an error-handling gap, not a crash under normal operation.

**How to verify the fix**

tests/test_backend.py::test_all_indices_invalid_setting_falls_back_to_all — monkeypatch config.controller_index_setting to return '0,1' and assert no exception and detection fallback is used.

<details><summary>Verification trace</summary>

Traced end-to-end: config.controller_index_setting() (config.py:121-123) returns str() of the raw JSON value with no sanitization; backend.py:63 does [int(setting)] unguarded, and hba_raid.py:90 has the identical [int(setting)]. config.validate() (config.py:155-212) checks JSON parseability, tool binaries, ledctl, and data files but never controller.index. Reachability confirmed: core.scan() (core.py:18) calls bk.bay_map() with controller=None -> _all_indices() on every scan; cli.main() (cli.py:605-615) has no exception handler, so index values like "0,1" or "one" raise ValueError as a raw traceback from status, watch, and every scanning action until the file is hand-edited.

</details>

## F-028 — bay_map.json re-read and re-parsed 4 times per scan (no cache, no shared panels)

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/baymap.py:33`
- **Category**: performance
- **Verdict**: CONFIRMED
- **Also independently reported as**: baymap.load() swallows malformed bay_map.json with a bare `except Exception: return []` — bay labels silently revert to scrambled raw slots

**Evidence**

baymap.py:33 `with open(path) as f: data = json.load(f)` — load() has no caching, and one core.scan() calls it from hba.enumerate_disks (hba.py:74), attach_bays x2 (hba.py:180, via core.py:20 and :50) and get_ghost_disks (hba.py:199); config.bay_map_path() stat's the /etc path each time too.

**Failure scenario**

Watch re-reads and re-parses the same JSON file 4x per refresh and 2x per poll-triggered rescan — pure waste (a few ms each, plus 4 stat/open syscalls) that scales with every new call site; a truncated file mid-edit is also parsed at 4 different instants within one scan, so half the pipeline can see panels and the other half [].

**Suggested fix**

Cache the parsed panel list in a module global keyed on (path, os.stat(path).st_mtime_ns), invalidated automatically when the operator edits the file; alternatively thread `panels` through scan the same way bm already is.

*Verifier note:* Both suggested fixes are stdlib-only and fine. The simplest, most in-house-style option is threading `panels` through scan the same way `bm` already is (attach_bays/get_ghost_disks already accept bm=None with a fallback — add panels=None with a load() fallback). An mtime_ns-keyed module cache also works; avoid functools.lru_cache alone since it would never see operator edits. | Merged duplicate's note: Fix is correct and stdlib-only: catch (OSError, json.JSONDecodeError) and print one warning naming path+error, mirroring spec.py:38 (reuse Y/N colours from common).

**How to verify the fix**

tests/test_baymap.py::test_load_cached_until_mtime_changes — write a temp bay_map, call load() twice asserting one open (patch builtins.open counter), touch mtime, assert re-read.

<details><summary>Verification trace</summary>

Traced the call count exactly as claimed: one normal core.scan() executes baymap.load() 4 times — hba.enumerate_disks (hba.py:74), attach_bays via core.py:20 (hba.py:180), get_ghost_disks via core.py:22 (hba.py:199), and attach_bays again via core.py:50; the ghost-rescue branch (core.py:34-37) adds 3 more. load() has no caching, and config.bay_map_path() re-resolves (os.path.exists stat on the /etc path in _resource_path) each call. Impact is honestly small (tiny JSON, milliseconds), and the torn-read (mid-edit file parsed at 4 instants) is a marginal edge case — pure maintainability/minor-perf debt, correctly rated P3.

</details>

## F-029 — Malformed bay_map panel entries crash core.scan: int() outside the try and non-dict list entries

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/baymap.py:58`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: remap_slot reverse-slots rule produces negative bay labels for slots >= slots_per_enclosure instead of falling back to identity

**Evidence**

Line 58 `n = int(p.get("slots_per_enclosure", 8))` sits OUTSIDE the try block that starts at line 59, so a non-numeric value raises ValueError/TypeError; line 45 `_panels` does `p.get("type")` and raises AttributeError if a list entry is a string. load() (lines 26-41) returns the parsed list without validating entries.

**Failure scenario**

Operator edits the operator-editable bay_map.json to `"slots_per_enclosure": "eight"` (or leaves a stray string in the panel list): remap_slot raises during hba.attach_bays inside core.scan, so `b2ctl status` and the watch loop traceback on the read-only path until the file is fixed — exactly the corruption case the panel format was meant to tolerate.

**Suggested fix**

In load(), filter the list to dict entries (warn once about the rest); in remap_slot, move the int() coercion inside the try (or wrap it with a fallback to 8) so any malformed panel degrades to identity remap.

*Verifier note:* Suggested fix is right and stdlib-only. Extend it: remap_nvme (line 75 `d.get(...)`) has the same non-dict hazard for entries inside a back panel's map list, so filtering in load() should validate both panel entries and nvme map entries (dicts only, warn once), and remap_slot should wrap the int() coercion with a fallback to 8. | Merged duplicate's note: Suggested fix is correct and stdlib-only: apply the reversal only when 0 <= int(slot) < n, else fall through to identity (continue to the next panel / final `return enc_slot`) so out-of-range slots display raw.

**How to verify the fix**

tests/test_baymap.py: remap_slot with slots_per_enclosure="eight" returns the input enc:slot unchanged; load()/remap paths with panels=["junk", {valid}] do not raise and still apply the valid panel.

<details><summary>Verification trace</summary>

Traced: line 58 `n = int(p.get("slots_per_enclosure", 8))` sits outside the try that begins at line 59, so with reverse_slots truthy a non-numeric value raises ValueError/TypeError; line 45 `p.get("type")` raises AttributeError on a non-dict panel entry; load() (lines 26-41) only checks isinstance(data, list), not the entries. remap_slot is on the unguarded read path: hba.attach_bays (hba.py:186) is called from core.scan (core.py:20/50), and hba.enumerate_disks calls remap_nvme (hba.py:95), so a malformed /etc/b2ctl/bay_map.json tracebacks `b2ctl status` and the watch loop. No guard elsewhere: the only try in the chain covers split/int(slot), not int(n). P2 not P0: the crash requires an invalid operator edit of the config file, not normal operation — an error-handling/robustness gap.

</details>

## F-030 — selftest_status result regex scans the whole smartctl output, so a stale 'Completed without error' from the drive's HISTORICAL self-test log masks a current aborted test — false burn-in PASS

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/burnin.py:46`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: selftest_status treats empty/unparseable smartctl output as 'finished, 100%', enabling a false PASS verdict

**Evidence**

m = re.search(r"(?:completed without error|self-test routine in progress|completed:?\s*read failure|completed:?\s*[\w ]+)", out, re.I) — `smartctl -a` includes the persistent self-test LOG table ('# 1  Extended offline    Completed without error  00% 18000  -') listing tests run months ago by a previous owner. If the current execution status is 'was aborted by the host' (no 'completed' wording), the search falls through and matches the old log entry.

**Failure scenario**

Second-hand disk vetting (`b2ctl burnin`, runbook STEP 02): the just-started long test aborts (drive reset/power event mid-test) — execution status says 'aborted by the host', selftest_status returns running=False, result='Completed without error' taken from the previous owner's old test, assess() finds no failure, and the burn-in gate prints PASS 'safe to add to a pool' for a disk that was never actually tested.

**Suggested fix**

Parse only the 'Self-test execution status:' block for ATA (slice out up to the next blank line / 'SMART Attributes' header) instead of searching the full output, and add explicit alternatives for 'aborted'/'interrupted'/'fatal error' that return a non-empty failure result so assess() marks FAIL.

*Verifier note:* Fix is correct and stdlib-only. Implementation detail: slice from 'Self-test execution status' to the next blank line for ATA, but keep a separate branch for SAS drives whose smartctl output has no such header (SAS uses 'SMART Self-test log' with an 'Extended background' result table and different wording — the existing SAS in-progress regex at line 42 shows both formats are in scope). Add explicit 'aborted|interrupted|fatal|unknown' alternatives returning a non-empty result so assess() FAILs; this also closes the aborted-test half of finding 65. | Merged duplicate's note: Fix is right and stdlib-only. Two refinements: (1) in _wait_selftest, an 'unknown' state must have a bounded retry budget or a dead disk mid-test loops forever; (2) the aborted/interrupted handling overlaps finding 159 — solving both means parsing only the execution-status block AND returning a non-empty failure result for aborted/interrupted, so assess() FAILs (or at minimum WARNs) instead of silently passing.

**How to verify the fix**

tests/test_burnin.py::test_selftest_status_aborted_ignores_history — fixture with execution status 'The previous self-test routine was aborted by the host' plus an old '# 1 Extended offline Completed without error' log row; assert result reports the abort (and assess() verdict is FAIL), not PASS.

<details><summary>Verification trace</summary>

Traced: re.search at lines 46-47 scans the entire `smartctl -a` output, which for ATA includes the persistent self-test log table; the project's own test fixture _ATA_DONE (tests/test_burnin.py:9-12) is literally a log-table line ('# 1 Extended offline Completed without error 00% 18000') matched as the result, proving log entries satisfy the regex. The 'aborted by the host' execution-status wording and the 'Aborted by host' log status match none of the four alternatives (all require 'completed' or 'in progress'), so on a current abort the search falls through to any older 'Completed without error' entry — near-universal on second-hand disks with prior test history. Combined with running=False from the fallback, assess() line 68 sees a clean result and passes. Order-of-match check done: a genuinely completed current test matches the execution-status line first, so normal runs are unaffected; only abort/interrupt + stale success mis-parses. P2: requires an abort event (host reset/power blip mid-test), but it is exactly the event the gate exists to catch.

</details>

## F-031 — b2ctl locate lacks the ghost-disk guard watch has and reports false success

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/cli.py:76`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`ok, method = locatemod.blink_disk(d, args.seconds)` runs for any resolved disk. watch._cmd_locate explicitly refuses ghosts: `if chosen.dev == "-": print(... cannot locate a GHOST disk ...)` (watch.py:394-396); cli._locate has no such check, and locate.blink() unconditionally returns (True, "dd") because _dd_read swallows dd's failure.

**Failure scenario**

A disk is rejected by the OS (GHOST, dev='-') — exactly the disk an operator wants to find and pull. `b2ctl locate <its-bay>` runs `ledctl locate=-` then `dd if=-`, both fail instantly, yet the command prints "[+] done (via dd)" and exits 0. The operator walks to the rack expecting a blinking bay and finds nothing, or pulls a guessed (wrong) disk.

**Suggested fix**

After resolving d in _locate, mirror the watch guard: if d.dev == "-": print the GHOST refusal message and return 1. (Separately locate.blink's unconditional True return deserves a fix in locate.py.)

*Verifier note:* Fix is correct: mirror the watch guard (if d.dev == "-": print refusal, return 1) after resolution at line 71-73. The deeper fix — making _dd_read report whether dd actually ran (check returncode / catch FileNotFoundError-like failure) so blink can return False — belongs in locate.py and would also fix finding 15's silent-success symptom.

**How to verify the fix**

tests/test_cli.py::test_locate_refuses_ghost_disk — mock core.scan to return one ghost disk (dev='-', bay '1:3'); call cli._locate with target '1:3'; assert return code 1 and locatemod.blink_disk not called.

<details><summary>Verification trace</summary>

Traced: cli._locate (67-78) resolves the target and calls blink_disk with no dev == "-" check, while watch._cmd_locate:394-396 explicitly refuses ghosts. Ghosts are Disk(dev="-") from hba.py:211 and are resolvable by bay/serial in _locate's matcher. For a ghost, is_perc_pd is false on an IT box (no pd_state, array_type != HW), so blink() runs: _ledctl('locate=-') fails, falls to _dd_read('-') whose subprocess.run swallows the instant dd failure (no check=, only TimeoutExpired caught), and blink unconditionally returns (True, "dd") (locate.py:63-64). _locate then prints '[+] done (via dd)' and returns 0 — false success on exactly the disk an operator most wants to physically find.

</details>

## F-032 — _resolve_devs passes unresolved tokens and /dev/sdX fallbacks straight into zpool mutations, despite its 'never /dev/sdX' contract

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/cli.py:119`
- **Category**: warning
- **Verdict**: CONFIRMED
- **Also independently reported as**: _resolve_devs silently passes unresolved tokens through and falls back to unstable /dev names

**Evidence**

out.append((match.by_id or match.dev) if match else t) — the docstring promises 'never /dev/sdX', but an unmatched token t is passed through verbatim and a matched disk without a by-id link falls back to d.dev; the same `d.by_id or d.dev` fallback feeds zpool add/attach at watch.py:146, 199, 205, 667.

**Failure scenario**

Freshly hot-plugged disk whose /dev/disk/by-id link has not appeared yet: `b2ctl cache-add tank sdc` (or watch [a]ssign → spare) issues `zpool add -f tank ... /dev/sdc`; after the next reboot device names shuffle and the pool leaf token no longer matches the physical disk, breaking b2ctl's serial/bay mapping and violating section 9 'always act on by-id'.

**Suggested fix**

In _resolve_devs, error and abort when a token does not resolve or the resolved disk has no by_id (tell the user to wait for udev / re-insert); in watch, refuse mutations when d.by_id is empty instead of silently using d.dev (mirror the existing _handle_new_disk guard).

*Verifier note:* Do NOT hard-fail every unresolved token: cache-rm/log-rm legitimately accept raw pool leaf tokens (parser help: "cache leaf token / bay / serial / dev") that will never match a scanned disk and must pass through to `zpool remove`. Restrict the abort to (a) the add paths (cache-add/log-add) for unresolved tokens, and (b) any matched disk whose by_id is empty — optionally run `udevadm settle` + one rescan first (hba.py already does this pattern), then error with the same message as watch.py:123. Mirror the guard in watch's `d.by_id or d.dev` mutation sites. All stdlib. | Merged duplicate's note: The suggested fix ('only pass through /dev/ or /dev/disk/by-id/ paths') would BREAK cache-rm/log-rm, where passing an arbitrary zpool-status leaf token to `zpool remove` is a documented feature (parser help: 'cache leaf token / bay / serial / dev'). Restrict the abort-on-unresolved behavior to the add paths (_cache_add/_log_add), and keep verbatim pass-through for the rm paths. Refusing empty by_id with a message is fine for adds.

**How to verify the fix**

tests/test_cli.py: _resolve_devs with an unknown token and with a matched disk whose by_id='' — assert both abort with an error and no zpool command is built.

<details><summary>Verification trace</summary>

Traced cli.py:112-120: `out.append((match.by_id or match.dev) if match else t)` passes unmatched tokens verbatim and falls back to /dev/sdX when by_id is empty (Disk.by_id defaults to "" in common.py:79 and stays empty when the by-id symlink has not appeared). Output feeds zpool mutations via zfs.add_cache/add_log/remove_vdev. The claimed watch.py anchors check out: `d.by_id or d.dev` feeds zfs.add_spare (146, 667), zpool replace (166), zfs.attach and zpool add (199, 205). The codebase itself treats empty by_id as a hazard — watch.py:122-124 (_handle_new_disk) refuses exactly this case ("no stable by-id yet — skipping") — so the missing guard on the CLI/menu mutation paths is a real gap, not intentional design. Practical impact is pool leaves recorded under unstable /dev/sdX names (breaking serial/bay mapping after a name shuffle), not data loss, so P2 rather than the rubric's mechanical §9→P0 reading.

</details>

## F-033 — b2ctl --dry-run update / install perform real writes and downloads

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/cli.py:359`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

_update calls `state = _sync_resource(bundled_name, dest, force)` (shutil.copy2 into /etc/b2ctl, .bak overwrite on --force) and rewrites config.json at lines 365-366; _install dispatches to installer.install_tools/install_profile (apt-get, downloads, /usr/sbin copies) at lines 277-289. Neither consults watch._DRY_RUN, and these code paths use shutil/installer directly so the WRITE_CMDS interception in run_check never applies. Root parser help (line 461): "preview write commands without executing them".

**Failure scenario**

Operator runs `b2ctl --dry-run update --force` to preview what would change: their hand-tuned /etc/b2ctl/bay_map.json is overwritten with the bundled copy (only a .bak remains) and config.json is rewritten. `b2ctl --dry-run install --perc` actually downloads perccli and flips controller.mode=raid on an IT box.

**Suggested fix**

At the top of _update's root branch and each mutating _install branch: if watch._DRY_RUN, print the files/commands that would be written ([DRY-RUN] prefix) and return 0 without touching disk; or pass dry_run into _sync_resource/installer functions.

*Verifier note:* Fix is right and stdlib-only. Simplest: at the top of _update's post-euid-check root section and each mutating _install branch, `if watch._DRY_RUN: print('[DRY-RUN] would ...'); return 0`. Threading dry_run into _sync_resource/installer is cleaner long-term but not required.

**How to verify the fix**

tests/test_cli.py::test_update_dry_run_writes_nothing — point _cfg_mod.STD_DIR/CONFIG_PATH at a tmpdir, set watch._DRY_RUN=True and geteuid->0; run _update with force=True; assert no files created/modified in tmpdir.

<details><summary>Verification trace</summary>

Traced: _update (root branch, lines 347-366) calls _sync_resource — real shutil.copy2 into /etc/b2ctl, with --force backing up then overwriting a customized bay_map.json — and rewrites config.json at 365-366; _install (274-291) dispatches straight to installer.install_profile/install_tools (downloads, apt, mode flip). Neither function reads watch._DRY_RUN, and none of these writes go through run_check, so the WRITE_CMDS dry-run interception never applies. Parser help (line 460-461) and user-guide-en.md:728 promise --dry-run means no writes. Both subcommands are reachable with --dry-run set (main sets watch._DRY_RUN before dispatch; update/install are merely need_root-exempt but self-check euid). P2 rather than P1: update --force keeps a .bak (recoverable) and install requires deliberate flags; still a clear contract violation.

</details>

## F-034 — b2ctl config init as non-root crashes with an unhandled PermissionError traceback

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/cli.py:384`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`with open(path, "w") as f:` (and `os.makedirs(os.path.dirname(path), exist_ok=True)` at line 384) write to /etc/b2ctl/config.json with no OSError handling, while main() line 613 exempts "config" from need_root(): `if args.cmd not in ("version", "check", "config", ...)`.

**Failure scenario**

A non-root user runs `b2ctl config init` (allowed by the root gate, and _check's own hint suggests it: "run 'b2ctl config init' to create"). os.makedirs or open raises PermissionError and the CLI dies with a raw Python traceback instead of the house-style red one-liner, exit code 1 only by accident of the exception.

**Suggested fix**

In _config_init (and consistent with _update's pattern), check os.geteuid() != 0 up front and print a clean '[-] run as root to write /etc/b2ctl/config.json' returning 1, or wrap makedirs/open in try/except OSError with the same message.

*Verifier note:* Prefer try/except OSError around makedirs+open printing '{R}[-] cannot write /etc/b2ctl/config.json — run as root{N}' and returning 1, over a geteuid check: an euid gate would also break legitimate non-root use with a writable override and is redundant with the actual failure.

**How to verify the fix**

tests/test_cli.py::test_config_init_non_root_clean_error — monkeypatch os.geteuid->1000 and CONFIG_PATH to an unwritable dir (chmod 0); assert _config_init returns 1 and prints the message without raising.

<details><summary>Verification trace</summary>

Traced: main() line 613 exempts "config" from need_root(), and _check's missing-config hint (line 251) actively tells users to run 'b2ctl config init'. _config_init has no OSError handling: as non-root, os.makedirs('/etc/b2ctl') at 384 raises PermissionError (or, if the dir exists, open(path,'w') at 386 does). __main__.py has no exception wrapper, so the CLI dies with a raw traceback — exit 1 only via the interpreter's unhandled-exception path, and no house-style red error line. Corrected anchor: 384 is the first raising call on a fresh box; 386 raises when /etc/b2ctl already exists.

</details>

## F-035 — tool_paths overrides for zpool/wipefs/sgdisk/dd/udevadm (and hba.py's lsblk) are dead config keys — every runtime call site uses the bare command name

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/config.py:31`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

config.py:31 `"zpool": "",` (plus wipefs/sgdisk/udevadm/dd/lsblk at lines 30-35) are declared as overridable, and `b2ctl config init` prints "Edit tool_paths to override binary locations." (cli.py:389). But all mutating/reading call sites ignore config.tool(): zfs.py:232-424 run_check(["zpool", ...]), zfs.py:373-375 ["wipefs"]/["sgdisk"], zfs.py:352 ["dd"], hba.py:26/268 ["lsblk"]/["udevadm"], locate.py:30/99 ["dd"]. Only smartctl/sas2ircu/perccli/ledctl (and the cron writer's zpool at zfs.py:442) honor the override.

**Failure scenario**

Operator on the R620 sets `tool_paths.zpool` in /etc/b2ctl/config.json (e.g. to a pinned /usr/local ZFS 2.4 userland matching the kernel module, as `config init` invites) — every replace/attach/detach/offline/destroy still executes whatever `zpool` PATH resolves first, while the generated maintenance cron uses the configured path: two different zpool binaries operate on the same pool depending on entry point, and the operator's declared override is silently ignored for all destructive actions.

**Suggested fix**

Route the first token through config.tool() at the call sites (or add a stdlib helper in common.py, e.g. run_tool(name, args) that prepends config.tool(name)); config.tool() falls back to shutil.which/bare name so the sim PATH-interception harness keeps working. Alternatively delete the never-honored keys from _DEFAULTS and the `config init` promise.

*Verifier note:* Fix is right and stdlib-only. Prefer the common.py helper (e.g. run_tool(name, *args) prepending config.tool(name)) so all four modules converge instead of sprinkling _cfg.tool() imports; config.tool()'s shutil.which fallback preserves the sim PATH-interception harness. If routing is deemed too invasive, the minimum honest fix is deleting the never-honored keys from _DEFAULTS and softening the cli.py:389 message — but then also change zfs.py:442, or the cron keeps honoring an override the interactive path does not.

**How to verify the fix**

tests/test_zfs.py::test_zpool_tool_path_override — monkeypatch config._cache with tool_paths {"zpool": "/custom/zpool"}, stub common.run_check to record argv, call zfs.add_spare/replace_member/destroy_pool and assert argv[0] == "/custom/zpool".

<details><summary>Verification trace</summary>

Traced every cited call site: zfs.py uses bare "zpool" at 25/44/167/180/232-424, "dd" at 352, "udevadm" at 367, "wipefs" at 373/382, "sgdisk" at 375; watch.py uses bare "zpool" at 166/205/349/434/470/625; hba.py bare "lsblk" at 26/40 and "udevadm" at 268-269; locate.py bare "dd" at 30/99. Only zfs.py:442 (the cron writer) resolves zpool via _cfg.tool(), and hba_raid.py:129 honors the lsblk override in the RAID backend while hba.py (IT backend) does not. cli.py:389 prints 'Edit tool_paths to override binary locations.' So the declared keys at config.py:30-35 are dead for all destructive interactive actions, and the cron/interactive zpool-binary split the finding describes is real. Nothing in CLAUDE.md marks this as intentional. P2: documented config feature silently ignored, with divergent binaries between entry points — user-visible but requires the operator to actually set an override.

</details>

## F-036 — sas2ircu bay_map() records Serial No from non-disk DISPLAY sections (Enclosure services device), creating a permanent phantom GHOST CRITICAL row

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba.py:159`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

m = re.match(r"Serial No\s*:\s*(\S+)", s) — the loop over `sas2ircu <c> DISPLAY` never checks the section header ('Device is a Hard disk' vs 'Device is a Enclosure services device'). On expander backplanes the SES section also carries 'Enclosure #', 'Slot #' and 'Serial No' (e.g. Dell 'BP13G+EXP ... Serial No : 7A00FG2'), so the backplane serial lands in the serial->bay map.

**Failure scenario**

On an R620 10-bay / R740XD (the runbook target) with an SES-capable expander backplane: the SEP serial never matches any lsblk/SMART disk serial, so get_ghost_disks() emits a CRITICAL '(Ghost / OS Rejected)' row for the backplane on EVERY status/watch refresh, core.scan fires a udevadm rescue for it each scan, and [a]ssign→wipe-ghost then finds the enclosure's own /dev/sgX by that serial and offers to dd-zero the SES device.

**Suggested fix**

Track the current section in bay_map(): set a flag on lines matching r'^Device is a (.+)' (True only for 'Hard disk', reset otherwise) and only record Enclosure/Slot/Serial while inside a hard-disk section. Stdlib re only; same gate for the parser both backends share.

*Verifier note:* Fix is correct and stdlib-only, with one correction: this sas2ircu parser lives only in the IT backend (hba_raid.py has its own perccli parser), so the section gate belongs in hba.py alone — there is no shared parser to gate. Set in_disk = bool(re.match(r'Device is a Hard disk', s)) on 'Device is a' lines and only record Enclosure/Slot/Serial while in_disk; extend the existing test_sas2ircu_display fixture with an SES section and assert its serial is absent.

**How to verify the fix**

tests/test_hba.py::test_bay_map_ignores_enclosure_services_device — extend the existing DISPLAY fixture (test_hba.py:57) with a 'Device is a Enclosure services device' section carrying Enclosure #/Slot #/Serial No, assert that serial is absent from the mapping and no ghost is produced by get_ghost_disks().

<details><summary>Verification trace</summary>

Traced: bay_map() (hba.py:151-164) records mapping[serial]=enc:slot for ANY section carrying Enclosure#/Slot#/Serial No — it never inspects the 'Device is a ...' header. Real sas2ircu DISPLAY output on expander backplanes includes 'Device is a Enclosure services device' sections with all three fields (Dell BP12G+EXP/BP13G+EXP SEPs), so the backplane serial enters the map, never matches an lsblk/SMART serial, and get_ghost_disks (hba.py:204-218) emits a permanent CRITICAL GHOST row; core.scan (core.py:25-28) fires udev_rescue_ghost for it every scan, and [a]ssign->_wipe_ghost (watch.py:225-243) would locate the SES sg node and offer dd-zeroing it. Caveats vs the claim: the wipe is behind an explicit [y/N] naming device+serial (§9 satisfied), the actual dd write() to an SES sg char device would almost certainly fail rather than damage it, and the current 2xR620 boxes may be direct-attach (no SES SAS target) — but the R740XD runbook target and 10-bay R620s are expander-backed. Persistent phantom CRITICAL + per-scan udevadm triggers + misleading destructive offer on documented target hardware = P2.

</details>

## F-037 — attach_bays/get_ghost_disks re-probe sas2ircu on every call — 5 `sas2ircu list` spawns per scan, 4 redundant

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba.py:178`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

hba.py:178 `if not have_sas2ircu(): return` (same at line 197); have_sas2ircu() (line 140) runs `sas2ircu list` uncached every call. One core.scan() issues: bk.have_tool() (1), bay_map()->_detect_sas2ircu_controllers() (backend.py:181, 1 more `list`), attach_bays (1), get_ghost_disks (1), and the second attach_bays at core.py:50 (1) — five `sas2ircu list` invocations even though a populated bm dict was already passed in.

**Failure scenario**

On the R620 the 32-bit sas2ircu takes ~0.5-1 s per invocation to probe the SAS2308. Every `b2ctl status` and every watch menu command (r/a/o/s/d/l/n/x all call core.scan) pays ~2-4 s of pure redundant controller probing before any useful work; with a ghost-rescue re-enumeration (core.py:34-37) it probes yet again.

**Suggested fix**

Cache the probe result in a module-level variable (e.g. `_have_cache: bool | None` reset per process, or memoize in backend.ITBackend), and skip the have_sas2ircu() gate entirely when a non-None bm is passed (a populated bay map proves the tool works). Stdlib only: a plain module global or functools.lru_cache.

*Verifier note:* Fix is right and stdlib-only. Two independent pieces: (1) memoize have_sas2ircu in a module global (reset not needed within one process; sim/tests can monkeypatch it); (2) in attach_bays/get_ghost_disks, skip the have_sas2ircu() gate when bm is not None — core always passes bm (possibly {} when the tool is absent, which loops harmlessly over nothing). Also cache _detect_sas2ircu_controllers, or better, parse indices from the same cached `list` output as the have-probe.

**How to verify the fix**

tests/test_hba.py::test_attach_bays_with_bm_spawns_no_sas2ircu — monkeypatch common.run with a call recorder from tests/helpers.py, call attach_bays(disks, bm={...}) and assert zero `sas2ircu list` invocations; add test_scan_probes_sas2ircu_once in tests/test_core.py counting `list` calls across one scan().

<details><summary>Verification trace</summary>

Traced and count verified: one IT-mode core.scan() with no ghosts spawns `sas2ircu list` 5 times — core.py:18 bk.have_tool() -> hba.have_sas2ircu (hba.py:140-142, uncached run() every call); backend.py:65->62->_detect_sas2ircu_controllers (backend.py:181) because config default controller.index='all' (config.py:39); hba.py:178 gate in attach_bays; hba.py:197 gate in get_ghost_disks; and the unconditional second attach_bays at core.py:50. Four are redundant given the populated bm already passed in. A ghost-rescue pass (core.py:34-37) adds two more (attach_bays + get_ghost_disks gates). Every watch command routes through core.scan, so the cost recurs per keystroke action. The ~0.5-1 s/invocation figure is an estimate I cannot time here, but 32-bit sas2ircu bus probing is genuinely slow and the redundancy is structural — user-visible latency on every status/watch action = P2.

</details>

## F-038 — find_sg_for_ghost / _read_sg_serial — the code that picks WHICH /dev/sgX gets dd-zeroed — is untested, including its loose bidirectional substring serial match and binary VPD-80 parsing

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba.py:252`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

hba.py:252 `if sg_serial and serial and (serial in sg_serial or sg_serial in serial): return sg_dev` returns the FIRST sg device whose serial merely contains (or is contained by) the target; _read_sg_serial (:222-238) hand-parses binary vpd_pg80 length bytes. No test in tests/test_hba.py touches either function; _wipe_ghost tests mock hba.find_sg_for_ghost.

**Failure scenario**

Two drives whose serials share a prefix (common on same-batch Samsung SSDs, e.g. 'S74ZNS0W5822' truncated by the SAT layer vs full serial): the substring match resolves the ghost to the WRONG /dev/sgX and _wipe_ghost zeroes 40 MB of a healthy pool member's metadata. A vpd_pg80 with a bogus length field could also slice garbage and mis-match.

**Suggested fix**

Add tests with faked /sys/class/scsi_generic entries (patch glob.glob + open) covering exact match, truncated-prefix match, and an ambiguous two-candidate case; then tighten the matcher to prefer exact equality and refuse (return None) when more than one sg device matches.

*Verifier note:* Fix is sound and stdlib-only (patch glob.glob + builtins.open / hba.run in tests). When tightening: prefer exact equality first, fall back to startswith (mirroring attach_bays' truncation handling) rather than full bidirectional containment, and return None printing a warning when >1 sg device matches. Also clamp the vpd_pg80 length: length = min(length, len(data)-4) to survive a bogus length field.

**How to verify the fix**

tests/test_hba.py::TestFindSgForGhost::test_exact_serial_match, ::test_truncated_prefix_match, ::test_ambiguous_two_matches_returns_none, ::TestReadSgSerial::test_vpd_pg80_binary_parse_and_bad_length.

<details><summary>Verification trace</summary>

Traced: hba.py:252 returns the FIRST sorted /dev/sgX whose serial substring-matches bidirectionally, no ambiguity refusal; _read_sg_serial (222-238) hand-parses vpd_pg80 length bytes with no test. Grep of tests/ shows no test touches find_sg_for_ghost or _read_sg_serial — test_watch.py:546/563 mock hba.find_sg_for_ghost, test_hba.py covers neither. The result feeds watch._wipe_ghost -> zfs.wipe_sg (zfs.py:351-357), a real dd zeroing 40 MB, so a truncated-prefix mis-match (the same SAT truncation attach_bays already compensates for with startswith at hba.py:189) would zero the wrong device after a confirm the operator cannot see is wrong. Pure test gaps are P3 per rubric, but the untested matcher is itself an error-handling gap (no exact-match preference, no multi-match refusal) guarding a destructive write — P2.

</details>

## F-039 — Malformed controller.index in config crashes scan with an uncaught ValueError, contradicting config.py's 'malformed → defaults apply' contract

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba_raid.py:90`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

return _list_controllers() if setting == "all" else [int(setting)] — controller_index_setting() returns str(user value) unvalidated; backend.py:63 (ITBackend._all_indices) has the same bare int(setting).

**Failure scenario**

/etc/b2ctl/config.json contains "controller": {"index": "0,1"} (operator tries to select two controllers): `b2ctl status` and every watch action die with a ValueError traceback from int('0,1') instead of degrading to defaults.

**Suggested fix**

Wrap in try/except ValueError in both hba_raid._ctrl_indices and ITBackend._all_indices: fall back to 'all' behavior (or [0]) and print one English warning naming the bad config value.

*Verifier note:* Better than patching both consumers: sanitize once in config.controller_index_setting() — return the value only if setting == 'all' or setting.lstrip('-').isdigit() (really: setting.isdigit()), else print one English warning naming the bad value and return 'all'. That fixes hba_raid._ctrl_indices and backend._all_indices in one place and honors the documented defaults contract. Stdlib-only.

**How to verify the fix**

tests/test_hba_raid.py::test_ctrl_indices_malformed_config and tests/test_backend.py::test_it_indices_malformed_config — monkeypatch controller_index_setting to return 'x'; assert no exception and a sane index list.

<details><summary>Verification trace</summary>

Traced: config.load() coerces controller.index with str(ctrl['index']) and never validates it (config.py:62-63); controller_index_setting() returns it raw; hba_raid._ctrl_indices (line 90) and backend.ITBackend._all_indices (backend.py:63) both do a bare int(setting). With "index": "0,1" (or any non-integer), int() raises ValueError which propagates through bay_map() -> core.scan -> cli, killing status/watch with a traceback. This directly contradicts config.py's module docstring contract 'Missing or malformed -> all defaults apply' (line 3). It requires a hand-edited config, so it's an error-handling gap (P2) rather than a default-path crash.

</details>

## F-040 — RAID-mode scan re-runs perccli ~15 times per status: bay_map recomputed, eall/sall fetched twice, vols parsed then discarded

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba_raid.py:247`
- **Category**: performance
- **Verdict**: CONFIRMED
- **Also independently reported as**: megaraid SMART target falls back to raw[0].dev (possibly an NVMe) or a hardcoded /dev/sda when no PERC VD block device exists, making all hidden-drive SMART reads fail

**Evidence**

hba_raid.py:247 `bay_to_sn = {bay: sn for sn, bay in bay_map().items()}` re-runs `perccli /cN/eall/sall show all` inside enumerate_disks even though core.scan already computed bm=bk.bay_map(); line 272 fetches the SAME eall/sall output a second time; line 240 `_vols, members = _vall_data()` parses volumes then discards them, and _status/_cmd_refresh call raid_volumes() (line 301) which runs _vall_data() again; have_tool() (line 65) is uncached and runs `show ctrlcount` per candidate at >=4 call sites (lines 237, 299, 317, plus bk.have_tool in scan); _ctrl_indices() re-runs ctrlcount each call (lines 215, 271).

**Failure scenario**

On the R640 (PERC H730P, perccli ~0.5-1.5 s per invocation) one `b2ctl status` issues ~15 perccli invocations where 3 unique fetches (ctrlcount, eall/sall, vall) suffice — 8-15 s wall time per status/refresh, repeated on every watch command.

**Suggested fix**

Cache _ctrlcount/have_tool results in module globals alongside _tool_cache; make enumerate_disks accept bm (backend already threads it through attach_bays) and reuse it for bay_to_sn; fetch eall/sall once per controller into a local and use it for both the bay map and the non-member PD pass; have enumerate_disks stash the parsed vols in a module-level per-scan snapshot that raid_volumes() returns instead of re-running _vall_data().

*Verifier note:* Suggested fix is sound and stdlib-only. Simplest safe ordering: (1) cache have_tool/_list_controllers results in module globals next to _tool_cache (invalidate together); (2) in enumerate_disks build bay_to_sn from a bm parameter threaded from core.scan (backend already threads bm into attach_bays — add it to enumerate_disks' signature via the Backend interface) and fetch eall/sall once per controller into a local reused by both the bay map and the _parse_pd_rows pass; (3) stash (vols, members) from the enumerate_disks _vall_data call in a module-level snapshot that raid_volumes() reads. Keep a refresh hook for watch's [r]efresh so cached ctrlcount/vols don't go stale across hotplug. | Merged duplicate's note: Fix is right and stdlib-only: when perc_devs is empty use smartctl's /dev/bus/<N> megaraid addressing; note the member dicts already carry their 'controller' index from _vall_data, so use f"/dev/bus/{m['controller']}" per member instead of assuming controller 0. The silent-misattribution variant also argues for dropping the plain-'-a' fallback attempt when a megaraid dtype was requested.

**How to verify the fix**

tests/test_hba_raid.py::test_enumerate_disks_single_eall_sall_fetch — monkeypatch run with a recorder emitting the sample perccli outputs from tests/helpers.py; assert each of ctrlcount/eall-sall/vall is invoked exactly once across enumerate_disks()+raid_volumes().

<details><summary>Verification trace</summary>

Counted the actual subprocess calls for one `b2ctl status` with the shipped default controller.index='all' (config.py:39): have_tool() is uncached and runs `show ctrlcount` at 4 call sites (core.scan:18, enumerate_disks:237, raid_volumes:299, attach_bays:317 twice via core.py:20/50); _ctrl_indices -> _list_controllers re-runs ctrlcount 5 more times (bay_map in scan, _vall_data:215, bay_map()@247, the eall/sall pass @271, raid_volumes' _vall_data); eall/sall is fetched 3 times (scan's bm, line 247's bay_to_sn which ignores the bm core already computed, line 272's non-member pass) and vall twice (enumerate_disks:240 parses vols then discards them, raid_volumes:301 re-runs _vall_data). Total ~15-16 perccli invocations where ctrlcount+eall/sall+vall (3 unique) suffice. At perccli's typical 0.5-1.5 s per invocation that is a multi-second, user-visible stall on every status/watch refresh. Finding's numbers and call sites all check out.

</details>

## F-041 — One RAID-mode scan re-runs identical perccli commands 4-6 times (eall/sall twice, ctrlcount ~5x, vall twice per status), and IT-mode re-runs `sas2ircu list` per have_sas2ircu() call

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba_raid.py:272`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

for pd in _parse_pd_rows(run([t, f"/c{idx}/eall/sall", "show", "all"])): — the same eall/sall output was just fetched by bay_map() at line 247 (and again by core.scan line 18 via bk.bay_map()); have_tool() (line 65) runs ctrlcount for both tool names on every call, _ctrl_indices→_list_controllers adds more; raid_volumes() re-runs _vall_data. In IT mode hba.attach_bays (line 178) and get_ghost_disks (line 199) each re-run `sas2ircu list`, and core.scan invokes them up to 4 times.

**Failure scenario**

perccli takes ~0.5-1 s per invocation on a PERC; `b2ctl status` in RAID mode spends 3-6 s in duplicate subprocess calls, and `watch` (which calls core.scan at 10+ call sites, e.g. watch.py:271,322,334) feels sluggish after every keystroke/hotplug event.

**Suggested fix**

Fetch each perccli output once per scan and pass it down: have bay_map() return (mapping, pd_rows) or cache the raw eall/sall text; cache _ctrl_indices()/have_tool() results in module-level per-scan state (like _tool_cache); in hba.py cache have_sas2ircu() per process and reuse the bm already passed by core.scan.

*Verifier note:* Suggested fix is sound and stdlib-only. Simplest shape: cache have_tool()/_ctrl_indices() results in module state alongside the existing _tool_cache, have enumerate_disks accept/reuse the bm that core.scan already fetched (the backend interface already threads bm for attach_bays/get_ghost_disks — extend the same pattern), and keep the raw eall/sall text from bay_map() for the _parse_pd_rows pass instead of re-running the command.

**How to verify the fix**

tests/test_hba_raid.py::test_enumerate_disks_runs_eall_sall_once — monkeypatch run to count invocations per argv; assert eall/sall and vall each run exactly once per controller during core.scan() in RAID mode.

<details><summary>Verification trace</summary>

Counted the subprocess calls for one RAID-mode core.scan + status: eall/sall runs 3x (core.scan:18 via bk.bay_map, hba_raid.enumerate_disks:247, and again per controller in the loop at :271-272), vall 2x (enumerate_disks._vall_data + raid_volumes._vall_data), and ctrlcount ~8-10x (have_tool() probes both tool candidates and is hit from backend detection, core.scan:18, enumerate_disks:237, attach_bays:317 — called twice per scan at core.py:20 and :50 — plus raid_volumes:299; each _ctrl_indices() call adds a _list_controllers ctrlcount). IT-mode claim also verified: hba.have_sas2ircu() runs 'sas2ircu list' on every attach_bays (:178) and get_ghost_disks (:197) call, and core.scan invokes attach_bays up to 3x and get_ghost_disks up to 2x per scan; watch.py rescans after every command/hotplug. perccli invocations are known-slow on a PERC, so the duplicate work is user-visible in watch.

</details>

## F-042 — rebuild_progress percent regex requires 'NN%', but real perccli 'show rebuild' prints a bare integer under a 'Progress%' header — progress is always parsed as 0.0

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/hba_raid.py:396`
- **Category**: bug
- **Verdict**: PLAUSIBLE

**Evidence**

m = re.search(r"(\d+(?:\.\d+)?)\s*%", out) — real perccli/storcli `show rebuild` output is a table: 'Drive-ID    Progress% Status      Estimated Time Left' / '/c0/e32/s4         28 In progress 0 Minutes'. The row value '28' has no trailing %, and the only '%' in the output ('Progress%') has no digits before it, so the regex never matches. The repo's own test fixture invents a MegaCli-style string ('Rebuild Progress on Drive = 42.5%', tests/test_hba_raid.py:162) that perccli does not emit.

**Failure scenario**

On the real R640 (PERC H730P), `b2ctl raid-replace`: while the controller rebuilds for hours, rebuild_progress() returns pct=0.0 on every poll, so _wait_rebuild renders a bar stuck at 0% the whole time, and the guard in raid_actions.replace (line 125: `if not st['done'] and st['pct'] == 0.0: start_rebuild(...)`) cannot distinguish 'not started' from '28% underway' — it fires `start rebuild` into an already-running rebuild.

**Suggested fix**

In rebuild_progress(), additionally match the table row: re.search(r"^/c\d+/e\d+/s\d+\s+(\d+(?:\.\d+)?)\s", out, re.M) and take that as pct; keep the '%'-suffixed pattern as a fallback for other firmware. Keep done = 'not in progress' OR pct >= 100.

*Verifier note:* Suggested regex is correct for the table row; anchor it re.M and prefer it over the % fallback: m = re.search(r"^/c\d+/e\d+/s\d+\s+(\d+(?:\.\d+)?)\s", out, re.M). Also treat an 'In progress' status with pct parsed as 0 as 'started' in the raid_actions.replace guard (check for 'in progress' without the leading 'not') so start_rebuild only fires when the controller reports no rebuild at all. Update the tests/test_hba_raid.py fixtures to the real table format alongside. Stdlib-only.

**How to verify the fix**

tests/test_hba_raid.py::test_rebuild_progress_perccli_table_format — patch run() with the real table output ('/c0/e32/s4         28 In progress 0 Minutes' under the 'Progress%' header) and assert pct == 28.0 and done is False.

<details><summary>Verification trace</summary>

Code side fully traced: the only pct source is re.search(r"(\d+(?:\.\d+)?)\s*%", out) at line 396; against the documented storcli/perccli `show rebuild` table ('Drive-ID Progress% Status Estimated Time Left' / '/c0/e32/s4 28 In progress 0 Minutes') no digit ever precedes a '%' (the row value is bare, the header's '%' follows letters), so pct=0.0 on every poll while done-detection still works via 'not in progress' — bar stuck at 0% for the whole rebuild, and the raid_actions.replace:125 guard (`not done and pct==0.0`) cannot tell 'not started' from 'mid-rebuild' and fires a redundant `start rebuild`. The repo's own fixture (tests/test_hba_raid.py:162, 'Rebuild Progress on Drive = 42.5%') is MegaCli-format that perccli does not emit, and hba_raid.py:369 admits the parsing was never validated on hardware. PLAUSIBLE rather than CONFIRMED only because the single deciding fact — the exact H730P perccli output — cannot be traced from this repo; Broadcom's documented storcli table format strongly supports the claim, but some perccli builds could differ. Severity P2: misparse of real tool output, but done/completion still fires and the redundant start-rebuild is a no-op error on the controller, so no data risk.

</details>

## F-043 — Downloaded root-run binaries are never integrity-verified (only a 1 KB size check) and the download has no timeout

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/installer.py:58`
- **Category**: warning
- **Verdict**: CONFIRMED
- **Also independently reported as**: download() has no timeout and network errors escape install_tools as a raw traceback

**Evidence**

Lines 58-61: `urllib.request.urlretrieve(url, dest_path)` followed only by `if size < 1024: raise RuntimeError(...)`. The archives come from hardcoded Google Drive file IDs and end up as /usr/sbin/sas2ircu and /usr/sbin/perccli, executed as root on both production nodes.

**Failure scenario**

The Drive file is replaced/tampered (or a quota-exceeded HTML page >1 KB is served): a wrong or malicious archive is accepted and handed to the extractors; a swapped perccli archive installs an attacker-controlled binary that b2ctl runs as root for every RAID action. Separately, urlretrieve with no timeout makes `b2ctl install --with-tools` hang forever on a black-holed connection.

**Suggested fix**

Pin a SHA-256 per archive next to _GDRIVE and verify with hashlib.sha256 before extraction (stdlib only); replace urlretrieve with urllib.request.urlopen(url, timeout=60) streamed to the dest file.

*Verifier note:* Fix is stdlib-valid. Pin sha256 per archive next to _GDRIVE and verify with hashlib before handing to the extractors; replace urlretrieve with urllib.request.urlopen(url, timeout=60) + shutil.copyfileobj. Apply the same pinned hashes in install.sh (sha256sum -c) or the two paths will diverge again. | Merged duplicate's note: Fix is correct and stdlib-only: urllib.request.urlopen(url, timeout=60) + shutil.copyfileobj (shutil already imported), and `except (RuntimeError, OSError)` at line 187. Overlaps findings 68 (timeout half) and 125 (except half) — dedupe when applying.

**How to verify the fix**

tests/test_installer.py: download() (or a new download_verified()) with a payload whose hash mismatches raises RuntimeError and leaves nothing installed; matching hash passes.

<details><summary>Verification trace</summary>

Traced download() lines 54-62: urlretrieve (which accepts no timeout) followed only by a <1 KB size check; the extracted binaries land in /usr/sbin via _install_to_usr_sbin and are executed as root by every backend action. No checksum anywhere. Mitigating nuance: a quota/HTML page >1 KB does NOT install a binary — zipfile/tarfile raise on it and install_* returns ([✗]); the real integrity exposure is a swapped-but-valid archive on the hardcoded Drive IDs. The no-timeout hang on a black-holed route is real (urlretrieve inherits the default socket timeout of None). install.sh shares the same gap, so this is consistent debt, not intentional protection.

</details>

## F-044 — install_tools catches only RuntimeError — a network failure in urlretrieve tracebacks out of `b2ctl install --with-tools`

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/installer.py:187`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`try: download(_GDRIVE[name], archive) except RuntimeError as exc:` — download() (line 58) calls urllib.request.urlretrieve, which raises urllib.error.URLError/HTTPError (OSError subclasses), none of which are RuntimeError; only the 'download too small' size check is handled.

**Failure scenario**

Datacenter server with no outbound internet (the normal case for these Proxmox boxes): `b2ctl install --with-tools` or `--perc` dies with an unhandled URLError traceback mid-install instead of the clean '[✗] sas2ircu: ...' line, and for --perc the subsequent set_mode('raid') step never runs, leaving the box half-configured.

**Suggested fix**

Broaden to `except (RuntimeError, OSError) as exc:` (URLError subclasses OSError) so the per-tool '[✗] name: msg' path handles offline boxes; profile installs then still fall through to set_mode.

*Verifier note:* `except (RuntimeError, OSError) as exc:` is the correct minimal stdlib fix (URLError subclasses OSError). Pair it with the set_mode success-gating from finding 71, otherwise fixing this alone makes --perc-offline fall through to set_mode('raid') and converts a crash into finding 71's persisted-bad-mode bug.

**How to verify the fix**

tests/test_installer.py::test_install_tools_offline_prints_error — monkeypatch urllib.request.urlretrieve to raise URLError and assert install_tools completes without raising and prints the failure line.

<details><summary>Verification trace</summary>

Line 187 is exactly `except RuntimeError as exc:`; download() raises RuntimeError only for the <1 KB size check, while the urlretrieve call itself raises URLError/HTTPError/socket errors (OSError family) on network failure, none caught. Traced the --perc consequence: the exception propagates through install_tools out of install_profile (line 225) before _cfg.set_mode at line 227, so the box gets a traceback and no mode set — 'half-configured' as claimed. Note the finally at line 195-196 does still clean the tmp dir, so the only damage is the crash UX plus skipped set_mode. Duplicates the except-clause half of finding 116.

</details>

## F-045 — install_profile persists controller.mode even when the tool install failed, forcing a backend the box cannot serve

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/installer.py:227`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Lines 225-227: `install_tools(tools)` (returns None — per-tool failures are only printed) followed unconditionally by `_cfg.set_mode(mode)`. There is no success signal from install_tools at all.

**Failure scenario**

`b2ctl install --perc` on a box without network: the perccli download fails ([✗] printed), yet controller.mode=raid is written to /etc/b2ctl/config.json — every subsequent b2ctl run forces the RAID backend with no perccli present, degrading/breaking status and watch until the operator hand-edits the config or reruns install successfully.

**Suggested fix**

Make install_tools return a {tool: bool} result (or re-check tool_ok(t) for each requested tool after install) and only call set_mode when all requested tools pass; otherwise print that the mode was left unchanged and why.

*Verifier note:* Suggested fix is right and stdlib. Simplest robust form: after install_tools(tools), gate set_mode on all(tool_ok(t) for t in tools) — tool_ok already verifies presence AND executability — and print 'controller.mode left unchanged (tool install failed)' otherwise.

**How to verify the fix**

tests/test_installer.py: install_profile("perc") with download monkeypatched to raise asserts config.set_mode is never called; with tool_ok True afterwards it is called once with "raid".

<details><summary>Verification trace</summary>

Traced install_profile lines 220-228: install_tools() returns None (per-tool failures only printed at lines 188/194), then _cfg.set_mode(mode) runs unconditionally. Real reachable triggers: corrupt/HTML archive (<1 KB RuntimeError or extractor exception), alien failure, or binary-won't-execute — all print [✗] and still persist controller.mode. One correction to the quoted scenario: on a fully offline box the download raises URLError, which escapes the `except RuntimeError` at line 187 as a traceback, so set_mode is NOT reached in that specific case (see finding 125); the mode-persisted-despite-failure bug fires on the RuntimeError/extractor/alien failure paths instead. Forcing controller.mode=raid with no working perccli breaks every subsequent status/watch run until the operator hand-edits /etc/b2ctl/config.json.

</details>

## F-046 — blink() reports success ('done via dd') even when dd fails instantly, misleading a physical pull

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/locate.py:64`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`_dd_read(dev, seconds)` followed by unconditional `return True, "dd"`. _dd_read catches only TimeoutExpired (the success case); a dd that exits immediately with an I/O error is ignored, and a missing dd binary raises uncaught FileNotFoundError. The result feeds `✔ done (via dd)` in watch._cmd_locate and cli._locate.

**Failure scenario**

The disk you locate is precisely the FAULTED one you intend to pull; dd `if=<dev> iflag=direct` on a dead/failing disk exits with an I/O error in <1s, no activity LED ever flickers, yet b2ctl prints success. On the DEGRADED raidz1 tank the operator, seeing no blink but a ✔, guesses the bay and pulls a healthy member -> second failure -> pool faulted. Violates the safety intent behind bay identification.

**Suggested fix**

Make _dd_read return True only when the read ran the full duration (TimeoutExpired) or exited 0 after >= seconds elapsed; catch OSError/FileNotFoundError; propagate that bool from blink() so callers print the existing '✗ failed' path. Resolve dd via config.tool("dd") for consistency.

*Verifier note:* Sharpened fix: make _dd_read return bool — True on TimeoutExpired (ran the full window) or returncode==0; False on nonzero returncode or OSError/FileNotFoundError. Propagate from blink(): `return _dd_read(dev, seconds), "dd"` so the existing '✗ failed' branches in cli._locate (cli.py:77) and watch._cmd_locate (watch.py:400) fire. Drop the 'exited 0 after >= seconds elapsed' timing condition — rc==0 before the timeout means the device read fine end-to-end (only possible on tiny devices) and is not a failure. Resolving dd via _cfg.tool('dd') matches config.py:35. Stdlib only.

**How to verify the fix**

tests/test_locate.py::test_blink_reports_failure_when_dd_errors_instantly — monkeypatch subprocess.run to return returncode=1 immediately (no TimeoutExpired) and assert blink() returns (False, 'dd').

<details><summary>Verification trace</summary>

Traced: _dd_read (locate.py:27-33) runs dd without check=True and catches only subprocess.TimeoutExpired (the ran-full-duration success case); a dd that exits in <1s with an I/O error — realistic for the FAULTED disk that locate exists to find, on the dd fallback path used whenever ledctl (ledmon package, not installed by default on Proxmox) is absent — returns silently, and blink() line 64 unconditionally returns (True, 'dd'), so cli._locate prints '[+] done (via dd)' with exit 0 and watch prints '✔ done'. Confirmed defect: the ok boolean is meaningless on the dd path. The FileNotFoundError sub-claim is technically accurate but practically unreachable (dd is coreutils, Essential on Debian 13). I rate P2 rather than the claimed P1: it is an error-handling gap producing a false success message, and the operator has an independent physical signal (no LED activity) — the pool-loss chain requires the operator to pull a guessed bay despite seeing no blink, which the tool's own workflow never instructs.

</details>

## F-047 — perccli locate path leaves the LED latched on if interrupted — no try/finally, unlike the ledctl path

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/locate.py:90`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Lines 88-91: `ok, _ = hba_raid.locate(disk.bay, True)` then bare `time.sleep(seconds)` then the OFF call — a KeyboardInterrupt during the sleep skips `hba_raid.locate(disk.bay, False)`. The ledctl branch protects the same pattern with try/finally at lines 57-60 ('ALWAYS leave it off').

**Failure scenario**

RAID box: operator blinks bay 32:2 from watch or `b2ctl locate 32:2 30` and hits Ctrl-C during the sleep; the locate LED stays on indefinitely. A DC tech later sees the lit bay on a healthy Onln member of the live VD and pulls it — the marked-disk convention is exactly what section 9's LED rules exist to protect.

**Suggested fix**

Mirror the ledctl branch: wrap the sleep in try/finally with hba_raid.locate(disk.bay, False) in the finally block.

*Verifier note:* Suggested fix is correct and complete: wrap the sleep in try/finally with hba_raid.locate(disk.bay, False) in the finally block, preserving the `return ok, "perccli"` shape. Stdlib only.

**How to verify the fix**

tests/test_locate.py: blink_disk on a HW Disk with time.sleep monkeypatched to raise KeyboardInterrupt asserts hba_raid.locate(bay, False) was still called.

<details><summary>Verification trace</summary>

Traced: locate.py:88-91 — hba_raid.locate(disk.bay, True), then a bare time.sleep(seconds), then the OFF call. hba_raid.locate (hba_raid.py:356-364) issues perccli 'start locate' / 'stop locate', a latched LED state, so a KeyboardInterrupt (Ctrl-C in watch's input loop or cli during the sleep) propagates past line 90 and the stop call at line 91 never runs — LED stays on until someone manually stops it. The ledctl branch protects the identical pattern with try/finally at lines 57-60 ('ALWAYS leave it off'), proving the omission is an inconsistency, not a design choice. Reachable on the R640 RAID box via `b2ctl locate 32:2 30` or watch [l]. Error-handling gap with user-visible/physical effect = P2; the pull-the-wrong-disk escalation is plausible but indirect.

</details>

## F-048 — _pick_member matches the shared controller block device, so 'sda' silently selects an arbitrary healthy RAID member

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/raid_actions.py:49`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`if target in (d.bay, d.serial, d.dev, d.dev.replace("/dev/", "")):` — every synthesized HW member carries the same ctrl_dev (hba_raid.enumerate_disks sets dev=/dev/sda for all members), so target 'sda' or '/dev/sda' matches the FIRST member in list order regardless of which physical drive the operator meant.

**Failure scenario**

R640, vd0 raid1 with a failing member in bay 32:1. Operator runs `b2ctl raid-offline sda` thinking sda identifies the bad disk: _pick_member returns the bay 32:0 member (healthy). If the operator skims the confirm, the healthy drive is set offline+missing and its LED lit for pulling — degrading the array from the wrong side.

**Suggested fix**

In _pick_member, only match on d.dev when the disk owns a real block device (d.smart_dtype == '' and d.array_type != 'HW'); for HW members accept bay and serial only, and print 'ambiguous: HW members share /dev/sdX — use bay or serial' when a dev-style target is given.

*Verifier note:* Suggested fix works but is more complex than needed: in _pick_member simply skip the d.dev / basename comparisons when d.array_type == 'HW' (all HW members share ctrl_dev by construction), and print 'HW members share the controller device — identify by bay (E:S) or serial' when the target looks dev-shaped and no bay/serial matched. The proposed smart_dtype check is redundant for this function since it only iterates _hw_members().

**How to verify the fix**

tests/test_raid_actions.py::test_pick_member_rejects_shared_dev — two HW-member Disks sharing dev='/dev/sda'; assert _pick_member(disks, 'sda') returns None while bay/serial targets still resolve.

<details><summary>Verification trace</summary>

Traced: hba_raid.enumerate_disks (:244-252) sets dev=ctrl_dev (the PERC VD block device, typically /dev/sda) on EVERY synthesized HW member, and _pick_member (:49) matches `target in (d.bay, d.serial, d.dev, d.dev.replace('/dev/',''))` — so 'sda' or '/dev/sda' returns the first HW member in list order, an arbitrary healthy drive. Reachable via `b2ctl raid-offline sda` and `b2ctl raid-replace sda` (offline() at :140, replace() at :83). Partial guard exists: _confirm names the picked disk via disk_label (bay/model/serial), so a careful operator catches it — hence P2, not P1. Still a defect: an ambiguous identifier silently resolves to the wrong physical drive in a destructive flow.

</details>

## F-049 — smartctl attempt chain retries a hung disk with 30 s timeout per attempt — one dying disk stalls the whole scan up to 90-150 s

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/smart.py:33`
- **Category**: performance
- **Verdict**: CONFIRMED
- **Also independently reported as**: smartctl retry ladder can stall a scan for 90–150 s per unresponsive disk (3–5 attempts × 30 s run() timeout)
- **Also independently reported as**: Raw-device fallback runs even when a megaraid dtype is forced, so a dead hidden member can inherit the PERC virtual disk's SMART identity/health

**Evidence**

smart.py:33 `o = run(cmd)` uses common.run's default timeout=30 and run() swallows TimeoutExpired returning "", so the loop at line 30 proceeds to the next device type ([None,'sat','scsi'] = 3 attempts IT-mode; [dtype,'sat+'+dtype,None,'sat','scsi'] = 5 in RAID passthrough) against the same unresponsive drive.

**Failure scenario**

A tank raidz1 member starts hanging ATA IDENTIFY/SMART commands (the exact failure b2ctl exists to catch): each attempt burns the full 30 s, so that one disk costs 90 s (IT) or 150 s (megaraid) inside the ThreadPoolExecutor — `b2ctl status` and every watch refresh freeze for minutes precisely during a disk-failure incident, with hotplug polling suspended.

**Suggested fix**

Pass an explicit shorter timeout (e.g. run(cmd, timeout=10)) for SMART probes, and make the timeout distinguishable — e.g. have run() return None on subprocess.TimeoutExpired (keeping '' for nonzero-exit) so _smartctl breaks out of the attempts loop after the first timeout instead of retrying a hung device. Stdlib only.

*Verifier note:* Same correction as 113: run(cmd, timeout=10) is fine, but 'run() returns None on TimeoutExpired' must not be a global contract change — zfs.list_pools (zfs.py:28) and similar callers do out.splitlines() unguarded and would crash. Make the None-on-timeout behavior opt-in via a keyword arg, or catch subprocess.TimeoutExpired locally in _smartctl and break out of the attempts loop on first timeout. Consider deduplicating with 113 — they are the same finding. | Merged duplicate's note: Fix is stdlib-only and correct. Cleanest variant: give run() an optional sentinel (return None on TimeoutExpired, '' on other failure), pass timeout=10 for smartctl attempts, and break the ladder on the first None — a device that times out on one probe will time out on all of them. | Merged duplicate's note: Do NOT make run() return None on TimeoutExpired globally: callers like zfs.list_pools (zfs.py:28) call .splitlines() on the result unguarded and would crash with AttributeError. Instead either (a) add an opt-in parameter to run() (e.g. none_on_timeout=False, default preserves the str contract) used only by _smartctl, or (b) have _smartctl call subprocess.run directly in a tiny local helper that catches subprocess.TimeoutExpired and breaks out of the attempts loop. Also pass a shorter per-probe timeout (~10 s). Stdlib only, consistent with CLAUDE.md section 4. | Merged duplicate's note: Fix is sound and stdlib-only: when dtype is non-empty, restrict attempts to [dtype, f"sat+{dtype}"] and return '' on failure — a hidden member can never be validly read through the raw VD node, so the ladder is pure downside there.

**How to verify the fix**

tests/test_smart.py::test_smartctl_stops_attempts_after_timeout — monkeypatch smart.run (via helpers recorder) to simulate a timeout sentinel on the first attempt and assert no further attempts are made and d.health == 'NOREAD'.

<details><summary>Verification trace</summary>

Duplicate of 113 and equally accurate; every specific claim checked out: run() default timeout=30 (common.py:43), exception-swallowing to "" (common.py:49-50), 3 attempts IT-mode / 5 attempts RAID passthrough at smart.py:26-29, core.scan's blocking ThreadPoolExecutor(max_workers=4) map (core.py:44-45), and watch.py where _cmd_refresh plus most menu commands call core.scan inside the single-threaded select() loop, so hotplug polling is suspended while the scan stalls. The 90 s / 150 s arithmetic matches the attempt counts. The per-attempt full-30 s stall on a hung drive is the standard failing-disk premise rather than something traceable in code, but the retry-without-timeout-discrimination defect is definitively present.

</details>

## F-050 — ATA raw-value parse concatenates all digits — formatted raws like Seagate's '29229h+18m+27.459s' become garbage

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/smart.py:88`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`raw = int(re.sub(r"\D", "", p[9]) or 0)` — strips every non-digit and concatenates the rest, so attribute 9 raw '29229h+18m+27.459s' parses as 292291827459 hours instead of 29229; the same corruption applies to any single-token composite raw for ids 5/197/187/188/198/241.

**Failure scenario**

A Seagate/other ATA drive with h+m+s POH formatting is burned in or scanned: POWER_ON shows absurd values (~33 billion years), and burnin.assess trips the POH_WARN=40000 gate -> a brand-new vetting disk is graded WARN 'power-on hours = 292291827459', misleading the operator's keep/return decision.

**Suggested fix**

Take only the leading integer of the raw token: `m = re.match(r"\d+", p[9]); raw = int(m.group()) if m else 0`.

*Verifier note:* Suggested fix is correct and stdlib-only: m = re.match(r"\d+", p[9]); raw = int(m.group()) if m else 0. This also correctly handles raws like '30 (Min/Max 25/45)' where the extra text is in later split tokens, and leaves plain-integer Samsung raws unchanged. Add a unit case with a Seagate-style h+m+s line in tests/test_smart.py.

**How to verify the fix**

tests/test_smart.py::test_parse_ata_composite_raw_values — feed an attribute table with 9 Power_On_Hours raw '29229h+18m+27.459s' and assert d.poh == 29229.

<details><summary>Verification trace</summary>

Verified at smart.py:88: re.sub(r"\D", "", p[9]) concatenates every digit run, so a drivedb-formatted attr-9 raw like '29229h+18m+27.459s' (single whitespace-free token, so it is p[9] after line.split()) becomes 292291827459; no ValueError fires, so the corruption is silent, and the same code path feeds ids 5/197/187/188/198/241. burnin.py:74 gates 'if d.poh and d.poh > POH_WARN' and would WARN a healthy vetting disk with the absurd value. Reachability caveat: both deployed servers run Samsung SSDs whose raws are plain integers, so today's fleet never triggers it; the realistic path is burnin/scan of a future non-Samsung (esp. Seagate) drive, an explicitly supported use. Effect is display + advisory burnin grading only — no action path or section-9 safety rule is corrupted, so P2 rather than P1.

</details>

## F-051 — Attribute 241 assumed to be 512-byte LBAs regardless of attribute name — TBW math is wrong for vendors reporting 32MiB/GB units

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/smart.py:96`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: Wear attribute chosen by first occurrence in smartctl output (ascending ID), not by WEAR_ATTR_IDS priority — masks real wear on drives with multiple candidate IDs

**Evidence**

d.lba_written = raw  (aid == 241) later converted via LBA_BYTES = 512 (line 17) in _endurance. Intel reports 241 as Host_Writes_32MiB and SanDisk as Total_GB_Written; the ID is trusted while p[1] (the attribute name that reveals the unit) is ignored.

**Failure scenario**

An Intel DC SSD is added as a tank spare and given an ssd_spec.json entry: 241 raw 300000 (≈9.6 TB in 32MiB units) is read as 300000 LBAs ≈ 0.15 GB → WRITTEN column shows ~0.00TB and end_left pins at ~100% forever, so the endurance WARNING/CRITICAL thresholds can never fire even at TBW exhaustion (the mirror-image vendor unit yields false CRITICAL instead).

**Suggested fix**

In _parse_ata, inspect p[1] for aid 241: scale raw by 32*1024**2/512 when the name contains '32MiB', by 1e9/512 for 'GB'/'GiB' names, else keep 512-byte LBAs; keep the constant for the plain Total_LBAs_Written case.

*Verifier note:* Fix is right; scale by name: '32MiB' in p[1] → raw * (32*1024*1024)//512; 'GiB' → raw * (1024**3)//512; 'GB' → raw * 10**9//512; else raw. Requires capturing p[1] alongside raw in the aid==241 branch — trivial, stdlib-only. | Merged duplicate's note: Fix is correct and minimal: accumulate {aid: val} in the loop, then after the loop `d.wear_val = next((vals[a] for a in WEAR_ATTR_IDS if a in vals), None)` to honor list order as priority. | Merged duplicate's note: Suggested fix is right: m = re.match(r"\d+", p[9]); continue if no match; raw = int(m.group()). This also correctly handles '34565 (12 43)' style raws (though split() already isolates '34565' there) and '0/0' style raws which the current code would also concatenate.

**How to verify the fix**

tests/test_smart.py — add test_attr241_unit_by_name: ATA dumps with '241 Host_Writes_32MiB ... 300000' and '241 Total_LBAs_Written ... 123456789'; assert written_tb ≈ 9.83 and ≈ 0.063 respectively.

<details><summary>Verification trace</summary>

Traced smart.py:95-96 + _endurance (153-162): aid 241 raw is stored as lba_written and always converted with LBA_BYTES=512; p[1] (attribute name) is never consulted. Vendor unit variance for ID 241 is well documented — Intel consumer/DC lines report Host_Writes_32MiB, some SanDisk report Total_Writes_GiB — so written_tb and end_left would be off by ~65536x (or the inverse), pinning endurance at 100% (or false CRITICAL). Latent today: the Samsung fleet reports true Total_LBAs_Written, and the wrong math only matters once such a disk also has an ssd_spec.json TBW entry. P3 as filed.

</details>

## F-052 — Menu index parsing accepts 0 and negative numbers, silently selecting the last list item in destructive flows

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:72`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Line 73: `return pools[int(sel) - 1]["name"]` — input "0" yields index -1 (last pool), "-1" yields -2, with no error. The same `list[int(sel) - 1]` pattern is repeated in every picker: _assign_free_disk line 157, _cmd_assign 297, _cmd_offload 344, _cmd_replace 505, _cmd_destroy 607, _cmd_swap 648, _cmd_demote 684, _cmd_extend 728/758, _cmd_burnin 778, _cmd_create 529.

**Failure scenario**

At "  destroy which #> " or "  offload which #> " the operator types "0" meaning 'cancel/none' — instead of cancelling, the LAST pool/disk in the list is selected and the flow proceeds to its confirmation for a target the operator never chose; a rushed 'y' then offloads/destroys the wrong object (destroy is still saved by the type-name gate, offload/swap have only a single y/N).

**Suggested fix**

Add a shared helper `_pick(items, prompt) -> item|None` that does `i = int(sel); if not 1 <= i <= len(items): return None` and use it in all pickers (also removes 11 copies of the same try/except).

*Verifier note:* Fix is right and stdlib-only: one shared `_pick(items, prompt)` doing `i = int(sel); if not 1 <= i <= len(items): return None`, used by all 12 sites (also handle the multi-select in _cmd_create/_cmd_extend line 728 with the same bound check per index).

**How to verify the fix**

tests/test_watch.py::test_menu_selection_rejects_zero_and_negative — patch _ask to return "0" then "-1" in _cmd_offload with 2 disks, assert 'cancelled' and no action call.

<details><summary>Verification trace</summary>

Corrected anchor: the indexing is line 72 (`return pools[int(sel) - 1]["name"]`); cited line 73 is the except clause. Traced: input "0" → index -1 → Python negative indexing silently returns the LAST item; "-1" → -2; the ValueError/IndexError guard never fires for -len(items)..0 ranges. Pattern verified repeated across all pickers: _assign_free_disk 157/195, _cmd_assign 297, _cmd_offload 343, _cmd_replace 505, _cmd_destroy 607, _cmd_swap 648, _cmd_demote 684, _cmd_extend 728/758, _cmd_burnin 778, and _cmd_create 529 (multi-index). Mitigation exists — every destructive flow shows a y/N confirmation naming the target and destroy adds a type-name gate — so this cannot silently mutate on its own, but '0 = cancel' is a common habit and silently substituting the last disk/pool into an offload/swap/destroy confirmation is a real wrong-target footgun. P2 is right.

</details>

## F-053 — _wait_for_block_device does not wait: one `udevadm settle` + a single lsblk check races the asynchronous SCSI rescan started by wipe_sg

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:218`
- **Category**: bug
- **Verdict**: PLAUSIBLE

**Evidence**

watch.py:218-222: `hba.run(["udevadm", "settle", f"--timeout={timeout}"])` then ONE pass over `hba._lsblk_pairs(...)`; no retry loop. wipe_sg's sysfs `rescan` write (zfs.py:363-365) queues an async SCSI target probe, and its `udevadm trigger --subsystem-match=block` only re-triggers EXISTING block devices (a ghost has none) — so the add uevent may not even be queued yet when settle drains an empty queue and returns immediately.

**Failure scenario**

During the guided ghost wipe (_wipe_ghost step 2/3) on a real disk: wipe_sg zeroes 40 MB and triggers the rescan; the kernel is still probing when settle returns, lsblk shows nothing, _wait_for_block_device returns None, and the flow aborts with 'Disk didn't appear within 20 s. Try re-inserting or rebooting.' — leaving the disk half-wiped (metadata zeroed, full wipe skipped, never offered to _assign_free_disk) although /dev/sdX appears 1-2 s later.

**Suggested fix**

Make it a real deadline poll: loop until time.monotonic() exceeds start+timeout, each iteration running `udevadm settle --timeout=1` (via hba.run) followed by the lsblk serial check, sleeping ~1 s between misses; return the device as soon as it appears. Stdlib only.

*Verifier note:* Suggested fix is correct and stdlib-only: deadline loop on time.monotonic() until start+timeout, each iteration `hba.run(["udevadm","settle","--timeout=1"])` + the lsblk serial check, time.sleep(1) between misses, return /dev/<NAME> on first hit. Keep the existing progress-dot print (`print(".", end="")`-style) so the 'Waiting for OS to recognize disk' line at watch.py:249 shows liveness.

**How to verify the fix**

tests/test_watch.py::test_wait_for_block_device_polls_until_device_appears — patch b2ctl.watch.hba so _lsblk_pairs returns [] on the first two calls and the SN row on the third; assert '/dev/sdc' is returned. Also rewrite the existing test at tests/test_watch.py:420 (test_wait_for_block_device_calls_settle_once), which currently locks in the racy single-check behavior.

<details><summary>Verification trace</summary>

Code half fully traced: _wait_for_block_device (watch.py:216-222) does exactly one `udevadm settle` then one _lsblk_pairs pass — the docstring even says 'check lsblk once' — despite the name promising a wait. wipe_sg (zfs.py:361-367) writes sysfs rescan then `udevadm trigger --action=add --subsystem-match=block`, which only re-fires uevents for block devices that already exist, so it cannot conjure the missing sd node. What I could NOT fully trace is the kernel half of the race: whether sd driver attach after a scsi_generic-level rescan is asynchronous on the Proxmox VE 9.2 kernel (sd probing was async via async_schedule in older kernels; newer kernels reworked it), i.e. whether the block device can genuinely appear 1-2 s after settle returns with an empty queue. If attach is synchronous within the sysfs write, the single settle+check would usually suffice. The no-retry structure is a race by construction either way; consequence (ghost-wipe step 2/3 aborts with a misleading 're-insert or reboot' message, full wipe skipped) matches the code paths at watch.py:249-253.

</details>

## F-054 — _wipe_ghost hands the freshly-wiped disk to _assign_free_disk without the by-id guard, allowing pool actions on unstable /dev/sdX

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:266`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Lines 264-266: `time.sleep(1); d2 = core.scan_one(sdx, tbw); _assign_free_disk(d2, tbw)`. _handle_new_disk (lines 122-124) explicitly skips when `not d.by_id` ("no stable by-id yet"), but _wipe_ghost — which just re-triggered udev via sgdisk --zap-all and waits only 1 s — has no such guard; _assign_free_disk then falls back to `d.by_id or d.dev` (lines 146/199/205) i.e. /dev/sdX.

**Failure scenario**

Ghost disk is wiped; by-id symlinks are still being re-created when scan_one runs 1 s later -> d2.by_id == "". Operator picks [2] add as spare -> `zpool add -f tank spare /dev/sdh`. After the next reboot/enumeration change sdh is a different disk and the pool references the wrong device — violating section 9 'always act on by-id, never /dev/sdX'.

**Suggested fix**

Mirror _handle_new_disk: after scan_one, if not d2.by_id run `udevadm settle` and rescan once, and if still empty print the same 'no stable by-id yet — re-run [a]ssign' skip message instead of calling _assign_free_disk.

*Verifier note:* Suggested fix is correct and stdlib-only. Concretely: after zfs.wipe succeeds, call hba.run(["udevadm", "settle", "--timeout=10"]) (same helper _wait_for_block_device already uses), rescan once, and if d2.by_id is still empty print the same skip message as _handle_new_disk:122-124 and return instead of calling _assign_free_disk.

**How to verify the fix**

tests/test_watch.py::test_wipe_ghost_skips_assign_without_by_id — patch core.scan_one to return Disk(dev='/dev/sdh', by_id=''), assert _assign_free_disk is not invoked.

<details><summary>Verification trace</summary>

Traced: _wipe_ghost (watch.py:225-266) runs zfs.wipe (labelclear + wipefs -a + sgdisk --zap-all, zfs.py:371-375), sleeps 1 s, then calls _assign_free_disk(d2, tbw) with no by-id guard, while _handle_new_disk (watch.py:122-124) explicitly skips when d.by_id is empty. All _assign_free_disk mutating branches fall back to `d.by_id or d.dev` (146/199/205/210), so a pool action can be issued on /dev/sdX. The wipe fires udev change events that momentarily remove/recreate by-id links and there is no `udevadm settle` after the wipe (the settle in _wait_for_block_device runs BEFORE the wipe), so the 1 s race window is real, though narrow — the operator does still see and confirm the /dev/sdX path.

</details>

## F-055 — _wait_resilver loops forever with no escape when zpool status output is unavailable or unparsable

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:406`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: _wait_resilver has no KeyboardInterrupt guard, unlike its two sibling wait loops — Ctrl-C crashes watch and skips the finalizing detach

**Evidence**

`while True: time.sleep(2); st = zfs.poll_resilver_status(pool); ...` — if common.run returns "" (zpool timeout on a loaded pool, tool path broken, pool exported mid-op), poll returns {done:0.0, completed:False} on every iteration; there is no error branch, retry cap, or KeyboardInterrupt handling (Ctrl-C tracebacks out of the whole watch session).

**Failure scenario**

During a replace on tank, `zpool status` starts exceeding run()'s 30 s timeout under resilver load: watch prints "resilvering... 0.0% done, ETA " indefinitely; the operator's only exit is Ctrl-C, which crashes out of watch entirely, skipping the detach/finalise steps.

**Suggested fix**

Track consecutive empty/zero-progress polls and bail out with a warning ("run: zpool status <pool>") after N failures; catch KeyboardInterrupt to return False cleanly like burnin._wait_selftest does.

*Verifier note:* Do NOT bail on zero-PROGRESS — 0.0% for many polls is normal early in a raidz1 resilver on tank. Distinguish 'no/unparsable zpool output' from 'in progress': have poll_resilver_status return an explicit error marker when run() output is empty (or when neither a scan line nor 'resilvered' is present), and bail in _wait_resilver after N consecutive error polls with a pointer to `zpool status <pool>`. Add the KeyboardInterrupt guard per finding 140, and make _replace_member honor a False return (see 140's fix_note). | Merged duplicate's note: One correction to the suggested fix: callers do NOT handle a False return — _replace_member (line 445) ignores _wait_resilver's result and proceeds straight to _detach_if_lingers + end_op(success). After adding the KeyboardInterrupt guard, make _replace_member (and _cmd_swap / _assign_free_disk choice 3) check the return: on False, skip the detach/LED, print how to resume, and end_op with an interrupted/incomplete status rather than success. (ZFS would likely refuse the mid-resilver detach of the replacing pair, but the audit record would still be wrong.)

**How to verify the fix**

tests/test_watch.py::test_wait_resilver_bails_on_empty_status — patch zfs.poll_resilver_status to always return the empty-output shape and assert _wait_resilver returns False after a bounded number of polls.

<details><summary>Verification trace</summary>

Traced: common.run() (common.py:43-50) returns "" on ANY exception including TimeoutExpired (timeout=30) and missing binary; zfs.poll_resilver_status (zfs.py:326-340) on "" hits neither the 'resilvered' branch nor the regexes, returning {done:0.0, completed:False} every time. watch._wait_resilver (watch.py:404-417) is a bare `while True` with no error branch, retry cap, or KeyboardInterrupt handling; watch.run()'s try/except only wraps select.select (line 800), and cli.main/__main__.py catch nothing, so Ctrl-C tracebacks out of the whole process. Also unparsable-but-nonempty output (e.g. a scan line format the regex misses) loops the same way.

</details>

## F-056 — _offline_and_replace matches the replacement disk by bay equality, so bay=None (no sas2ircu / unmapped) matches any free disk

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:485`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: Recovery guidance after spare-less offline names '[r]eplace', but 'r' is refresh in watch and `b2ctl replace` requires a spare that this flow by definition lacks

**Evidence**

Lines 484-486: `new = next((x for x in core.scan(tbw) if x.bay == d.bay and not x.in_pool and x.dev != "-" and x.serial != d.serial and not x.smart_dtype), None)` — when d.bay is None (sas2ircu absent, NVMe without mapping, serial mismatch in bay_map) every free disk also has bay None, so `next()` returns the first free disk in scan order, not the one just inserted in the same bay.

**Failure scenario**

Spare-less offload on a box where bays are unmapped: two free disks exist (an old scratch disk plus the just-inserted replacement). The generator picks the old scratch disk and the CONFIRM box proposes `zpool replace` with it; a rushed operator confirms and the wrong disk is wiped into the pool while the real replacement stays idle.

**Suggested fix**

Guard on identity, not only bay: require `d.bay is not None` for the bay-match shortcut; otherwise diff serials against a pre-insert snapshot (record the free-disk serial set before the 'press Enter' prompt and pick the disk whose serial is new), falling back to 'cannot auto-detect, use [a]ssign'.

*Verifier note:* Fix direction is right. Simplest stdlib version: snapshot the free-disk serial set BEFORE the 'press Enter once the new disk is inserted' prompt (line 483), and after Enter pick the free disk whose serial is not in the snapshot; use the bay-equality shortcut only when d.bay is not None; otherwise print 'cannot auto-detect the new disk — use [a]ssign option 3'. | Merged duplicate's note: Suggested message fix is correct; additionally mention that simply leaving watch running will auto-detect the inserted disk and open the assign menu (that is the primary recovery path; [a]ssign option 3 is the manual one).

**How to verify the fix**

tests/test_watch.py::test_offline_replace_no_bay_does_not_pick_arbitrary_disk — d.bay=None and one pre-existing free disk with bay None, assert _replace_member is not called with it.

<details><summary>Verification trace</summary>

Traced: watch.py:484-486 selects the replacement as the first scanned disk where `x.bay == d.bay and not x.in_pool and x.dev != "-" and x.serial != d.serial and not x.smart_dtype`. When d.bay is None (sas2ircu absent/failed, or serial missing from bay_map — hba.attach_bays leaves bay None), every unmapped free disk satisfies the bay clause, so next() returns whichever free disk scan yields first, not the one inserted in the pulled disk's bay. Reachability caveat: on the two described production boxes sas2ircu is installed and the SATA bays map, so this needs degraded tooling plus 2+ free disks — an edge, but real for any box b2ctl supports. Mitigation traced: the wrong candidate then flows into _replace_member whose _confirm_op box (line 435, rendered at 81-110) prints the chosen disk's bay and serial, so an attentive operator can catch it before zpool replace runs; the wipe-wrong-disk outcome requires confirming a box that names the wrong serial. Defect real, consequence gated by one confirmation → P2.

</details>

## F-057 — _cmd_swap duplicates _replace_member's replace+wait+detach flow but skips the safety audit trail and command-preview confirmation

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:658`
- **Category**: maintainability
- **Verdict**: CONFIRMED
- **Also independently reported as**: Audited replace pipeline copy-pasted three times; the swap and demote copies already drifted and skip the safety audit entirely

**Evidence**

Lines 657-671 inline `zfs.swap_to_spare(...)` -> `_wait_resilver` -> `_detach_if_lingers` -> `zfs.add_spare(...)` with a plain one-line _confirm, while the equivalent _replace_member (428-452) uses _confirm_op (exact commands shown) and safety.begin_op/end_op. The trailing `zfs.add_spare` (line 667) is a second mutating command not named in the confirmed operation.

**Failure scenario**

Operator runs [s]wap on rpool/tank: `zpool replace` + `zpool detach` + `zpool add ... spare` all execute with no entry in /var/log/b2ctl/ops.jsonl — `b2ctl log` and `b2ctl rollback` cannot see or reverse the operation, and the pre-op zpool/smartctl snapshot that every other lifecycle action captures is missing when a post-mortem is needed.

**Suggested fix**

Route _cmd_swap through _replace_member(d, spares[0], detach_old=True) (it already wraps confirm_op + begin_op/end_op + error-aware wait once the P0 fix lands), then confirm the re-add-as-spare as its own [y/N] step listing `zpool add -f <pool> spare <dev>`.

*Verifier note:* Do NOT route through _replace_member(d, spares[0], detach_old=True) as suggested: that path uses `zpool replace -f` (swap is deliberately non-forced) and detach_old also blinks the pull-bay LED (watch.py:449-450), which is wrong for swap where the old disk stays installed as the spare. Instead wrap the existing _cmd_swap flow: build cmds = [replace, detach, zpool add -f <pool> spare <dev>], show them via _confirm_op, and bracket with safety.begin_op/end_op like _replace_member does. | Merged duplicate's note: Fix direction is right. Note _cmd_swap cannot literally call _replace_member unchanged (zfs.swap_to_spare omits -f and the post-step re-adds the old disk as spare), so parameterize _replace_member's command and post-steps as suggested rather than force-fitting.

**How to verify the fix**

tests/test_watch.py::test_swap_writes_audit_entry — drive _cmd_swap with mocked zfs/safety, assert safety.begin_op and end_op are called once each and the add_spare step is separately confirmed.

<details><summary>Verification trace</summary>

Traced: _cmd_swap (watch.py:636-671) runs three mutating commands — zfs.swap_to_spare (zpool replace, zfs.py:315-317), _detach_if_lingers (zpool detach), zfs.add_spare (zpool add -f ... spare) — behind a single plain _confirm, with no safety.begin_op/end_op and no _confirm_op command preview. Every comparable flow (_replace_member:428-452, _assign_free_disk choice 3:167-183, _offline_and_replace:471-475, _cmd_destroy:624-633) writes to /var/log/b2ctl/ops.jsonl and captures a pre-op snapshot (safety.py:34-74); cli.py exposes `log` and `rollback` (549-556) which will be blind to swap. Real audit/consistency gap, but the operation itself confirms correctly and works, so this is structure/consistency debt bordering P2.

</details>

## F-058 — watch startup calls prune_orphan_crons() without propagating _DRY_RUN, so the documented preview mode `b2ctl --dry-run watch` deletes real /etc/cron.d files

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:792`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

watch.py:792 `for p in zfs.prune_orphan_crons():` — the function has a `dry_run` keyword (zfs.py:477 `def prune_orphan_crons(*, dry_run: bool = False)`) but watch.run() never passes it, even though cli.main() sets watch._DRY_RUN=True before watch.run() executes. user-guide-en.md:578 explicitly documents `sudo b2ctl --dry-run watch` as a safe preview.

**Failure scenario**

Operator runs the documented preview `sudo b2ctl --dry-run watch` on a node right after manually exporting `tank` for maintenance (pool temporarily absent from `zpool list`): startup immediately and silently deletes /etc/cron.d/b2ctl-tank for real, killing the monthly trim+scrub schedule — in a mode the UI promises changes nothing. (Distinct from the already-reported zpool-list-transient-failure bug in zfs.py:479: here even a correct orphan decision mutates the system under --dry-run.)

**Suggested fix**

Change watch.py:792 to `for p in zfs.prune_orphan_crons(dry_run=_DRY_RUN):` and prefix the printed message with '[dry-run] would remove' when _DRY_RUN is set (prune already returns the would-be-removed paths without deleting when dry_run=True).

*Verifier note:* Fix is exact and complete: `for p in zfs.prune_orphan_crons(dry_run=_DRY_RUN):` — the function already returns would-be-removed paths without deleting when dry_run=True — and gate the message at watch.py:793 to print '[dry-run] would remove ...' when _DRY_RUN. One sharpening: this only covers dry-run; the separately-reported transient-`zpool list`-failure hazard inside prune_orphan_crons (empty `live` set → prune everything) remains and needs its own guard in zfs.py.

**How to verify the fix**

tests/test_watch.py::test_run_startup_prune_respects_dry_run — set watch._DRY_RUN=True, monkeypatch zfs.prune_orphan_crons with a recorder that asserts dry_run=True was passed, drive one loop iteration of watch.run() with a stubbed select/stdin returning 'q'.

<details><summary>Verification trace</summary>

Fully traced end to end: cli.main (cli.py:608-610) sets `watch._DRY_RUN = True` from the global --dry-run flag BEFORE dispatching `args.func(args)` → watch.run(); watch.py:792 then calls `zfs.prune_orphan_crons()` with no argument, and the function (zfs.py:477-490) defaults dry_run=False and does a real `os.remove` on every /etc/cron.d/b2ctl-* whose pool is absent from `zpool list`. user-guide-en.md:577-578 documents `sudo b2ctl --dry-run watch` under 'Preview any operation without changing anything'. So the documented preview mode performs an unconditional filesystem mutation at startup; the exported-pool case (pool temporarily absent) deletes a cron the operator wants kept, and even a correct orphan decision is a write the mode promises not to make. Not a §9 pool-safety violation and no pool data at risk, so P2 (documented-behavior contract broken, user-visible), not P0/P1.

</details>

## F-059 — Hotplug diff keys on device NAME only, so a pull+insert that reuses the same /dev/sdX while watch is blocked in a prompt is completely invisible

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/watch.py:835`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: A single failed/timed-out lsblk makes watch report every disk removed, then re-detected as NEW with the free-disk (wipe) menu

**Evidence**

watch.py:834-835: `current = _block_devs()` / `new, gone = current - baseline, baseline - current` — _block_devs (line 45-51) collects bare names ('sda','sdb'); the kernel reuses the lowest free sd name, and the loop cannot poll while any interactive handler is blocked in input()/_wait_resilver for minutes.

**Failure scenario**

While the operator is held at a long prompt (e.g. _cmd_create's eight property questions or raid_actions' 'press Enter once the new drive is inserted'), a colleague swaps a failed tank disk: old sdc is pulled and the replacement enumerates as sdc again. After the command returns, current == baseline — watch raises neither the 'disk removed' notice nor the NEW DISK flow (Task B step 5), the table silently shows the old disk's data for the bay, and the replacement never gets the _assign_free_disk offer until a manual [r]efresh.

**Suggested fix**

Track identity, not names: build the baseline from _lsblk_pairs('NAME,SERIAL,TYPE') as a set of (name, serial) tuples (serial may be ''), diff on that set, and feed the NAME part of added tuples to _handle_new_disk. Keeps the same one-lsblk-per-poll cost.

*Verifier note:* Fix is sound and matches existing patterns (hba._lsblk_pairs("NAME,SERIAL,TYPE") is already used at watch.py:219). Keep the TYPE==disk and hba._EXCLUDE prefix filtering from _block_devs. Caveat: core.py notes enterprise SAS drives often report empty SERIAL in lsblk, so those tuples degrade to (name, "") — name-only semantics for that subset, which is acceptable. Feed only the NAME of added tuples to _handle_new_disk; a same-name serial change shows up as one gone + one new tuple, triggering both handlers. | Merged duplicate's note: Fix is right and stdlib-only. In _block_devs return None when _lsblk_pairs yields no rows (a machine with zero disks is not a real state here); in run() skip the diff and keep the old baseline for that cycle. Independently add the in_pool guard: in _handle_new_disk (and/or at the top of _assign_free_disk) `if d.in_pool: print membership and return` — this also fixes the ordinary case of re-inserting a pool member being greeted with a wipe menu.

**How to verify the fix**

tests/test_watch.py::test_hotplug_detects_same_name_serial_change — patch hba._lsblk_pairs to return [{'NAME':'sdc','SERIAL':'SN1','TYPE':'disk'}] for the baseline and [{'NAME':'sdc','SERIAL':'SN2','TYPE':'disk'}] for the poll; assert the diff reports sdc as both gone and new.

<details><summary>Verification trace</summary>

Traced: _block_devs (watch.py:45-51) returns bare NAME strings and the diff at 834-835 is pure set subtraction on names. The kernel allocates the lowest free sdX letter, so a pull+insert that both complete while the loop is blocked (any input()/_ask/_wait_resilver — e.g. _cmd_create's multi-question flow at 536-572 or _offline_and_replace's 'press Enter' at 483) yields current == baseline: no removed notice, no _handle_new_disk, table stale until manual [r]. Even without a prompt, a swap completing within one 2 s POLL window is missed the same way. Low severity is right: no data-loss path, operator recovers with [r]efresh, and the window requires a same-name swap during a blocked prompt.

</details>

## F-060 — Mirrored SLOG leaves are classified as data vdevs: pool_level reports 'mixed' and the extend/remove path cannot see or remove them

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/zfs.py:155`
- **Category**: bug
- **Verdict**: CONFIRMED
- **Also independently reported as**: pool_level() rebuilds full topology per pool row — O(P^2) zpool invocations in every status/refresh render
- **Also independently reported as**: topology() is re-executed per helper call — pool_level() inside assemble_storage makes every refresh O(pools^2) zpool subprocesses

**Evidence**

`if vdev == pool or any(a in vdev for a in _AUX):` — _parse assigns each leaf the NEAREST enclosing vdev, so a mirrored log ('logs' -> 'mirror-1' -> leaves) yields vdev='mirror-1', which contains none of ('cache','log','spare',...). pool_level then adds 'mirror' to a raidz1 pool's level set; watch._cmd_extend's removal filter ('log' in e['vdev'], watch.py:752) misses the same leaves.

**Failure scenario**

Operator runs `b2ctl log-add tank <a> <b>` (the mirrored-SLOG feature b2ctl itself ships in v0.8.0): status table now shows tank LEVEL 'mixed' instead of 'raidz1' (core.py:103), and [e]xtend -> [3] answers 'no cache/log devices on tank', so the SLOG can never be removed through b2ctl; `b2ctl log-rm tank <leaf>` also fails because ZFS requires removing 'mirror-1', not a leaf.

**Suggested fix**

Track the top-level class in _parse: when a vdev header is one of the aux keywords (or the stack already contains one), tag entries with e.g. entry['class']='log'/'cache'/'spare'/'data'. Use that class in pool_level's exclusion and in the extend/remove listing, and remove mirrored aux vdevs by their mirror-N name via zpool remove.

*Verifier note:* Fix is right and stdlib-only. In _parse, derive a per-leaf class from the vdev_stack (if any stack element starts with 'log'/'cache'/'spare'/'special'/'dedup', tag entry['class'] accordingly, else 'data'); use it in pool_level instead of the substring test and in _cmd_extend's aux listing. For removal, when the leaf's class is 'log' and its vdev is mirror-N, offer/execute `zpool remove <pool> mirror-N` (dedupe so the mirror appears once) rather than the leaf token. | Merged duplicate's note: Fix is right and minimal: `def pool_level(pool, topo=None): topo = topology() if topo is None else topo` and have assemble_storage compute topology once (or accept the one scan() built as a parameter) — collapses the render to one list + P statuses total, keeps existing test patches (they patch b2ctl.zfs.topology) working. | Merged duplicate's note: Fix is correct and stdlib-only. Simplest form: core.scan already builds one topology at core.py:55 for attach_membership — return/pass that same dict into assemble_storage and give pool_level a `topo: dict | None = None` parameter (mirroring how attach_membership already takes topo), rather than re-plumbing every helper at once.

**How to verify the fix**

tests/test_zfs.py::test_pool_level_ignores_mirrored_log — fixture status with raidz1-0 plus logs/mirror-1; assert pool_level == 'raidz1' and that the parsed log leaves are identifiable as class 'log'.

<details><summary>Verification trace</summary>

Traced _parse (zfs.py:66-80): the 'logs' header matches _VDEV_RE ('log' + [-\w]* absorbs the 's'), then a nested 'mirror-1' pushes onto the stack, so mirrored-SLOG leaves get vdev='mirror-1' — no _AUX keyword. pool_level (zfs.py:149-162) therefore adds 'mirror' to raidz1 tank's level set => 'mixed' in the storage table (core.py:103), and watch._cmd_extend's removal filter (watch.py:751-752, `'cache' in e['vdev'] or 'log' in e['vdev']`) misses the leaves => '[3] remove' reports 'no cache/log devices'. A single-device log gets vdev='logs' and works, so the break is specific to the mirrored SLOG that b2ctl's own add_log (zfs.py:244-247) creates for 2+ devs. The zpool-remove-needs-mirror-1-not-leaf detail matches ZFS semantics (mirrored log removal takes the mirror-N name; leaf removal is refused). Confirmed: a shipped v0.8.0 feature breaks status LEVEL display and makes the SLOG unremovable through b2ctl; no data risk => P2.

</details>

## F-061 — demote_to_spare is a non-atomic detach-then-add with no compensation — an add_spare failure strands the mirror one-legged with no re-attach path

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/zfs.py:312`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

zfs.py:308-312: `ok, out = detach(pool, dev_token, ...)` ... `return add_spare(pool, dev_token, ...)` — once detach succeeds the pool has lost the mirror leg; if the second step fails the function just returns (False, out) and the caller (_cmd_demote, watch.py:693-697) only prints '✗ failed', leaving redundancy silently reduced between the two commands.

**Failure scenario**

`b2ctl demote` on an rpool mirror leg: detach succeeds (rpool now a single-disk vdev, zero redundancy on the boot pool); `zpool add -f rpool spare <dev>` then fails on a transient condition — udev/zed re-probing the just-detached device holds it busy, or the -part3 token is rejected for `zpool add`. The operator sees only 'failed: ...'; rpool stays unmirrored with no rollback hint (and _cmd_demote skips safety.begin_op entirely, so `b2ctl rollback` cannot help either). State changed between check and completion, half-applied.

**Suggested fix**

Before detaching, capture the remaining ONLINE member of the same vdev from topology(); if add_spare fails, retry once after `udevadm settle`, then attempt automatic compensation `zpool attach -f <pool> <remaining-member> <dev_token>` and return a message stating whether redundancy was restored, always including the exact re-attach command. Keep it inside zfs.demote_to_spare so both watch and cli get the guard.

*Verifier note:* The suggested AUTOMATIC `zpool attach -f` compensation violates CLAUDE.md §9 ('Never auto-resilver/detach without confirmation') — attach starts a resilver. Sharpen to: on add_spare failure, retry once after `udevadm settle` (safe, read-only wait); if still failing, print the exact recovery command (`zpool attach -f <pool> <remaining-online-member> <dev_token>`, capturing the remaining member from the topology snapshot taken before detach) and offer it behind an explicit [y/N] prompt instead of running it unprompted. Also wrap _cmd_demote in safety.begin_op/end_op like the other mutating watch flows. All stdlib/subprocess.

**How to verify the fix**

tests/test_zfs.py::test_demote_to_spare_rolls_back_on_add_spare_failure — patch zfs.detach -> (True,''), zfs.add_spare -> (False,'device busy'), zfs.topology -> rpool mirror-0 with the other leg ONLINE, and run_check; call demote_to_spare('rpool', tok) and assert a `zpool attach` re-attach was issued with the remaining member and the returned message names it.

<details><summary>Verification trace</summary>

Traced fully: zfs.demote_to_spare (zfs.py:308-312) is detach-then-add_spare with no compensation — after a successful detach the mirror leg is gone, and an add_spare failure just returns (False, out). The caller watch._cmd_demote (watch.py:692-697) prints only '✗ failed: ...' with no re-attach hint, and — unlike the replace/offline/destroy flows which all call safety.begin_op — _cmd_demote records nothing, so the rollback machinery cannot help; cli.py:107 delegates straight to _cmd_demote so both entry points share the gap. Guard zfs.can_detach (watch.py:688) only protects the pre-detach state; nothing guards the window between the two zpool commands. Reachability of the trigger (zpool add failing right after detach) is plausible rather than proven — transient udev/zed re-probe busy-ness or any zpool add error — but the missing-error-handling defect itself is fully confirmed at the code level, and the affected pool is the boot mirror (rpool), left silently unmirrored. P2 (error-handling gap on a mutating flow) per rubric.

</details>

## F-062 — has_zfs_label fails OPEN (wipefs error == 'no label'), silently bypassing the dirty-disk guard before pool create; wipe() also ignores labelclear/wipefs failures

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/zfs.py:384`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`ok, out = run_check(["wipefs", "-n", dev]); if not ok: return False` — any wipefs failure (missing binary, EBUSY, permission) is reported as 'disk is clean'. Similarly wipe() (lines 373-375) discards the run_check results of `zpool labelclear` and `wipefs -a` and returns only sgdisk's status, so watch prints '✔ wiped blank' on a partial wipe.

**Failure scenario**

wipefs briefly fails (device opened by udev probe / tool path misconfigured) while an operator creates a pool over a disk that still carries a ZFS label from another pool: the 'already contain data/labels — wipe and continue?' confirmation in _cmd_create (watch.py:574-580) silently never appears, and `zpool create -f` clobbers the old labels with no warning — eroding the CLAUDE.md §9 confirm-before-wipe guarantee.

**Suggested fix**

Fail safe: on wipefs error return True (treat as possibly-labelled) or better return a tri-state/raise so _cmd_create warns 'could not verify labels on <dev>' and requires confirmation; make wipe() AND the three run_check results together and return the first failure's output.

*Verifier note:* Fail-safe direction is right; prefer the tri-state (True/False/None='could not verify') over blanket True so a genuinely clean disk with a transient wipefs hiccup gets a 'could not verify labels' confirm rather than a false 'contains data' claim. For wipe(): surface wipefs -a and sgdisk failures, but tolerate labelclear's expected nonzero exit on disks with no ZFS label — a naive AND of all three would fail every wipe of a non-ZFS disk.

**How to verify the fix**

tests/test_zfs.py::test_has_zfs_label_fails_safe_when_wipefs_errors — monkeypatch run_check to return (False, 'not found') and assert the create flow still prompts (has_zfs_label truthy / warning surfaced).

<details><summary>Verification trace</summary>

Traced: has_zfs_label (zfs.py:382-384) returns False — 'clean' — whenever `wipefs -n` itself fails, and _cmd_create (watch.py:574-582) uses that as its only dirty-disk gate before zpool create runs with -f (zfs.py:402), which suppresses ZFS's own in-use/labelled refusal. So a wipefs error does silently skip the 'already contain data/labels — wipe and continue?' confirmation. The wipe() half is also verified (lines 373-375 discard labelclear/wipefs results). Rated P2 not P0: the create flow still requires the explicit 'create pool ... with N disks?' [y/N] naming the operation (so §9's core confirm rule is not wholesale violated — only the supplementary dirty-disk warning is lost), and `wipefs -n` failing for root on Debian (util-linux always present, read-only open, no exclusive lock) is a rare condition.

</details>

## F-063 — prune_orphan_crons deletes ALL b2ctl maintenance crons when `zpool list` transiently fails — runs unguarded at every watch startup

- **Priority**: P2 (Medium)
- **Location**: `codes/b2ctl/zfs.py:479`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

Line 479-489: `live = {_cron_path(p["name"]) for p in list_pools()}` then removes every /etc/cron.d/b2ctl-* not in `live`. list_pools() builds on common.run(), which returns "" on ANY failure (missing binary, 30 s timeout, non-zero exit) — indistinguishable from "no pools". watch.run() calls this unconditionally at startup (watch.py:792).

**Failure scenario**

Operator starts `b2ctl watch` while the zfs module is busy (heavy resilver/scrub makes `zpool list` exceed run()'s 30 s timeout) or with a broken zpool tool path: list_pools() returns [] -> live is empty -> the monthly TRIM+SCRUB crons for rpool and tank are silently deleted; pools stop being trimmed/scrubbed until someone notices, and the deletion is a mutation performed by what the operator thinks is a monitoring startup.

**Suggested fix**

In prune_orphan_crons: `pools = list_pools()`; `if not pools: return []` (a host with b2ctl crons but zero visible pools means the read failed or everything was destroyed — pruning can wait for a run where zpool answers). Optionally have list_pools distinguish command failure from empty output.

*Verifier note:* Fix is right and stdlib-only: in prune_orphan_crons, `pools = list_pools(); if not pools: return []`. A host with b2ctl-* crons but zero visible pools is either a failed read or total destruction; in both cases skipping the prune is safe (an orphan cron just runs a failing `zpool trim <gone-pool>` once a month until the next successful prune).

**How to verify the fix**

tests/test_zfs.py::test_prune_orphan_crons_noop_when_list_pools_empty — patch list_pools to return [] and glob to return a fake cron path, assert os.remove is not called and [] returned.

<details><summary>Verification trace</summary>

Traced: common.run() (common.py:43-50) returns '' on ANY exception including the 30 s default timeout and a missing/broken zpool binary; list_pools() then returns [], indistinguishable from zero pools. prune_orphan_crons (zfs.py:479-489) computes live={} and unlinks every /etc/cron.d/b2ctl-* file. watch.run() (watch.py:792) calls it unconditionally before rendering anything, with no dry_run and no confirmation, so a transient zpool stall (plausible during heavy resilver/scrub) deletes the rpool+tank TRIM/SCRUB crons. Severity P2 rather than P1: the trigger is a transient failure condition (not everyday input), the deletion is announced ('removed stale cron ...'), and the damage is degraded maintenance rather than a broken action path or data risk — a classic error-handling gap with user-visible effect.

</details>

## F-064 — No cleanup trap: any failure inside install_tools aborts under set -e, leaking the mktemp dir and silently skipping the --perc/--flash mode write

- **Priority**: P2 (Medium)
- **Location**: `codes/install.sh:181`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`_DL_TMP=$(mktemp -d)` has no `trap ... EXIT`; install_tools runs unguarded simple commands under `set -euo pipefail` — `apt-get install -y alien unzip libc6-i386 ...` (line 85) and the hardcoded `cp -f /opt/MegaRAID/perccli/perccli64 /usr/sbin/perccli` (line 120). A failure of either exits the script before `rm -rf "${_DL_TMP}"` (line 187) and before the SET_MODE block (lines 191-198).

**Failure scenario**

`./install.sh --perc` on an R640 whose apt mirror is unreachable (common on Proxmox with the enterprise repo 401 and no other source), or where the alien-converted rpm installs perccli64 to a different path: script dies mid-way with tool archives leaked in /tmp and controller.mode=raid never written — b2ctl silently falls back to auto-detect, contradicting the documented --perc contract, with no summary telling the operator what completed.

**Suggested fix**

Add `trap 'rm -rf "${_DL_TMP:-}"' EXIT` right after mktemp; guard apt-get and the cp with explicit `if ! ...; then echo "[✗] ..." >&2; return 1; fi`; apply SET_MODE before (or independently of) tool installation so the profile survives a tool failure.

*Verifier note:* The suggested fix is right but incomplete: `trap 'rm -rf "${_DL_TMP:-}"' EXIT` must also cover (or install_tools must itself clean) the second mktemp at line 78, which leaks on every run including successful ones. Also guard lines 91-97 (the find-on-missing-dir abort) e.g. `_sas=$(find ... | head -1 || true)`. Moving SET_MODE before the tool install matches installer.py's install_profile ordering-independence intent and keeps the documented --perc/--flash contract.

**How to verify the fix**

New tests/test_install_sh.py: run install.sh --perc via bash with stub apt-get/curl on PATH that exit 1 and PREFIX/CONFIG redirected to a tmpdir; assert the mode file is still written, the temp dir is removed, and a non-zero exit prints an explicit failure line.

<details><summary>Verification trace</summary>

Traced: no `trap` exists anywhere in install.sh; `set -euo pipefail` is active (line 4) and install_tools is invoked as a plain command in the then-body (line 183), so errexit applies inside it. Unguarded `apt-get install -y ...` (line 85) and `cp -f /opt/MegaRAID/perccli/perccli64 ...` (line 120, inside a then-body) abort the whole script on failure, skipping `rm -rf "${_DL_TMP}"` (187) and the SET_MODE block (191-198). Reachable on Proxmox with the unsubscribed enterprise repo (apt-get update failure is ||true'd, so the subsequent install of missing packages like alien fails). Additionally verified: a corrupt sas2ircu zip kills the script at line 95 (`_sas=$(find <missing dir> | head -1)` fails under pipefail+errexit — unzip of a non-zip does not create the -d dir; both behaviors tested), another unguarded path with the same leak/skip effect. Bonus: install_tools' own `_tmp` (line 78) is never removed even on success.

</details>

## F-065 — Sim does not model raid10: pools created with `mirror a b mirror c d` render as a flat stripe and go SUSPENDED after one pull

- **Priority**: P2 (Medium)
- **Location**: `codes/sim/bin/zpool:21`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`_vdev_label` = `{"raidz1": ..., "raidz2": ..., "mirror": ..., "stripe": None}` has no 'raid10' key (cmd_create line 248 records type 'raid10'), so status prints members flat with no mirror-0/mirror-1 groups; _simstate.parity_of (codes/sim/_simstate.py:124-132) falls through to `return 0` for raid10. Verified empirically: 4-disk raid10 status shows four top-level leaves, and pulling one member yields health SUSPENDED.

**Failure scenario**

b2ctl `create --raid10` in sim: zfs.pool_level() derives 'stripe' from the flat topology, so can_offline()/can_detach() refuse legitimate raid10 operations and the offload/demote guards behave wrongly; pulling a single disk of four shows SUSPENDED instead of DEGRADED — the raid10 lifecycle CLAUDE.md §8 claims the sim covers is tested against a wrong model.

**Suggested fix**

In cmd_create store mirror groups (e.g. pool["groups"] = [[a,b],[c,d]]); render `mirror-0`/`mirror-1` sub-vdevs in _render_status; teach parity/_pool_health that raid10 tolerates one missing disk per mirror group (SUSPENDED only when both legs of one group are gone).

*Verifier note:* Fix direction is right; sharpen: store pool['groups'] = [[a,b],[c,d]] in cmd_create (pair devs in order per `mirror` keyword), render `mirror-0`/`mirror-1` sub-vdevs in _render_status for type raid10 — that alone fixes zfs.pool_level/can_offline/can_detach on the b2ctl side since they key off vdev names. Health: raid10 is SUSPENDED only when BOTH legs of some group are absent, DEGRADED when exactly one leg of any group is absent — implement in _pool_health with a raid10 branch (or teach parity_of to take group structure), and keep _simstate backward-compatible for old state.json files lacking 'groups' (fall back to pairing members in order). Stdlib only.

**How to verify the fix**

tests/test_sim_smoke.py: create a raid10 pool via the fake zpool, assert 'mirror-0' and 'mirror-1' appear in `zpool status`, zfs.pool_level() returns 'mirror' (not 'stripe'), and health is DEGRADED (not SUSPENDED) after one pull.

<details><summary>Verification trace</summary>

Confirmed statically and empirically. cmd_create line 248 records type 'raid10' (multiple `mirror` keywords), but _vdev_label (line 21-22) has no raid10 key -> vlabel None -> members render flat under the pool with no mirror-0/mirror-1 groups; _simstate.parity_of (lines 124-132) falls through to 0 for raid10. Reproduced in a throwaway state file: `zpool create testp mirror sdb sdc mirror sdd sde` shows four flat top-level leaves, and setting one member absent yields `state: SUSPENDED` (and SUSPENDED in `zpool list -H`). Downstream: zfs._parse assigns flat leaves vdev==pool, so pool_level() returns 'stripe', can_offline() wrongly refuses ('raidz'/'mirror' not in vdev) and can_detach() wrongly permits (skips both the raidz refusal and the mirror last-leg check). CLAUDE.md §8 explicitly claims the sim covers the raid10 lifecycle, so this is actively wrong behavior of a documented sim capability, not merely missing coverage — hence P2 despite being harness-only.

</details>

# P3 (Low) findings

## F-066 — Package __init__ imports cli for __version__, so importing ANY b2ctl submodule loads the entire application and sets a circular-import trap

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/__init__.py:2`
- **Category**: structure
- **Verdict**: CONFIRMED

**Evidence**

`from .cli import __version__` — cli.py's top-level imports pull watch, core, zfs, locate, installer (urllib.request, tarfile, zipfile), config and ui, so even `import b2ctl.common` executes the whole app graph.

**Failure scenario**

Any lower-layer module that ever adds `from . import cli` (e.g. to print the version in a banner) crashes instantly with a circular ImportError because __init__ -> cli is still mid-import; every test/sim import of a single parser module pays full-app import cost; a syntax error anywhere in watch/installer breaks `import b2ctl.baymap`.

**Suggested fix**

Move the version constant to __init__.py (or a tiny b2ctl/_version.py) and have cli import it (`from . import __version__`); update CLAUDE.md §10 'bump the version string in cli.py' to point at the new location.

*Verifier note:* Fix is sound and stdlib-only. Sharpen: prefer a tiny b2ctl/_version.py holding `__version__ = "0.8.8-itmode"`; have __init__.py do `from ._version import __version__` (dropping the cli import) and cli.py do `from ._version import __version__` — this avoids the partial-init subtlety of cli reading `from . import __version__` while __init__ is mid-import. cli.py line 534 (`version` subcommand) needs no change. __main__.py and sim/run import cli directly and are unaffected. Also update CLAUDE.md §10 ('bump the version string in cli.py') to point at the new location, per the project's doc-currency convention (§4).

**How to verify the fix**

tests/test_cli.py::test_version_import_is_light — in a subprocess run `python3 -c "import b2ctl, sys; assert 'b2ctl.watch' not in sys.modules"` and assert it exits 0.

<details><summary>Verification trace</summary>

Traced and empirically reproduced: line 2 of codes/b2ctl/__init__.py is `from .cli import __version__`; cli.py (lines 18-21) top-level imports core, watch, zfs, spec, locate, backend, config, installer, ui, and running `import b2ctl.common` in the repo loads all 15 b2ctl modules plus urllib.request and tarfile (from installer.py). The circular-ImportError scenario is latent, not current — grep shows no in-package module imports cli (only __main__.py and sim/run, which are entrypoints outside the cycle) — but no guard prevents it and CLAUDE.md does not declare the pattern intentional (§10 merely says the version string lives in cli.py). Full-app import cost on any submodule import is real today; the crash is a one-`from . import cli`-away trap. Structure/maintainability debt with no current runtime failure on the deployed box, so P3 per the rubric.

</details>

## F-067 — burnin.run() orchestration is untested: target resolution, the in-pool refusal guard, dry-run early return, and the FAIL exit code

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/burnin.py:114`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

burnin.py:114 `if d.in_pool:` refuses to self-test a pool member; :104-113 resolves a bay/serial/dev string via core.scan; :123-126 dry-run early return; :144 returns 1 only on FAIL. tests/test_burnin.py covers selftest_status/assess/read_scan/start_selftest, and test_watch's _cmd_burnin test patches b2ctl.burnin.run entirely — run() never executes in any test.

**Failure scenario**

A regression dropping the in_pool guard lets `b2ctl burnin <bay>` kick a long SMART self-test on an active tank raidz1 member (performance hit + confounded health data during production I/O), or a target-resolution change makes 'S74Z...' resolve to the wrong disk; CLI exit code drift (FAIL returning 0) breaks any scripted gate.

**Suggested fix**

Test run() with core.scan/start_selftest/_wait_selftest/smart.read patched: pool member target returns 1 without calling start_selftest; string target resolves by serial; dry_run returns 0 before _wait_selftest; assess FAIL propagates exit 1.

*Verifier note:* Suggested test plan is sound and matches house style (mock-driven, stdlib unittest): patch core.scan/start_selftest/_wait_selftest/smart.read/selftest_status in tests/test_burnin.py and assert (a) pool-member target returns 1 with start_selftest not called, (b) serial string resolves the right Disk, (c) dry_run returns 0 before _wait_selftest, (d) assess FAIL yields exit 1. Also patch time.sleep or keep _wait_selftest patched to keep the suite fast.

**How to verify the fix**

tests/test_burnin.py::TestRunFlow::test_refuses_in_pool_member, ::test_resolves_string_target_by_serial, ::test_dry_run_skips_wait, ::test_fail_verdict_exits_1.

<details><summary>Verification trace</summary>

Traced all three test surfaces: tests/test_burnin.py covers only selftest_status/assess/read_scan/start_selftest (no run()); tests/test_watch.py:797 patches b2ctl.burnin.run entirely in test_burnin_dispatch; tests/test_cli.py:177-183 only asserts the 'burnin' subparser parses. So run()'s target resolution (lines 104-113), the in_pool refusal guard (line 114-116), the dry-run early return (lines 123-126), and the FAIL->exit-1 mapping (line 144) execute in zero tests. The in_pool guard is the only thing standing between `b2ctl burnin <bay>` and starting a long self-test on an active tank raidz1 member, so the regression scenario is credible. P3 per rubric (test-coverage gap).

</details>

## F-068 — _resolve_dev is dead code while the same target-resolution logic is hand-copied in three places

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:27`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`def _resolve_dev(target: str, disks=None):` has zero callers anywhere in the codebase (grep over codes/). Meanwhile the identical bay/serial/dev matching tuple is re-implemented in _locate (lines 69-70), _resolve_devs (lines 117-118), and burnin.run (burnin.py:107-108); the redundant local `from . import zfs` in _cache_add/_cache_rm/_log_add/_log_rm shadows the module already imported at line 18.

**Failure scenario**

The next resolver improvement (e.g. matching NVMe PCIe-address bays or refusing ambiguous targets) gets applied to one copy and not the others — `b2ctl locate <bdf-bay>` resolves while `b2ctl burnin <bdf-bay>` says 'no disk matches', an inconsistency the per-module tests won't catch because each copy is tested in isolation.

**Suggested fix**

Delete _resolve_dev or, better, promote one resolver (target -> Disk) into core.py and call it from _locate, _resolve_devs, and burnin.run; drop the redundant local zfs imports.

*Verifier note:* Fix as suggested: delete _resolve_dev or promote a single resolve_target(target, disks) -> Disk into core.py and call it from _locate, _resolve_devs, and burnin.run; drop the redundant local zfs imports. All stdlib.

**How to verify the fix**

tests/test_cli.py::test_locate_and_burnin_share_resolver — resolve the same odd target (e.g. NVMe serial) through cli._locate and burnin.run with mocked scan and assert identical resolution; plus a grep-style assertion is unnecessary once the dead function is removed.

<details><summary>Verification trace</summary>

Traced: grep over codes/ shows _resolve_dev (cli.py:27) is defined and never called. The bay/serial/dev matching tuple is independently re-implemented in _locate (cli.py:69-70), _resolve_devs (cli.py:117-118), and burnin.run (burnin.py:106-108) — and the copies already diverge (_resolve_dev accepts /dev/ pass-through and omits by_id; the other three match by_id), so the drift the finding predicts has in fact already started. The four local `from . import zfs` statements in _cache_add/_cache_rm/_log_add/_log_rm are redundant with the line-18 import (they rebind the same module object — 'shadows' overstates it, no behavioral effect).

</details>

## F-069 — status --json silently ignores --locate/--seconds

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:43`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

`return 0` right after `print(json.dumps(...))` (line 42) — the function exits before the args.locate block at line 50, so `b2ctl status --json --locate` accepts both flags but never blinks nor mentions it.

**Failure scenario**

A monitoring wrapper runs `b2ctl status --json --locate` intending to both harvest the JSON and light at-risk bays for the on-site tech; the JSON arrives, no LED ever blinks, and nothing in the output indicates the flag was dropped — the tech has no blinking bay to act on.

**Suggested fix**

Either make the flags mutually exclusive in the status subparser (argparse add_mutually_exclusive_group) or run the locate block before returning the JSON path and add a 'located' field; the parser-level rejection is the simplest honest behavior.

*Verifier note:* Parser-level rejection via argparse is not directly possible here (--locate/--json are independent store_true flags in the same subparser; add_mutually_exclusive_group works and is the simplest honest fix as suggested). Alternatively run the locate block before the JSON return — but note that couples machine output with a physical side effect; the mutual-exclusion rejection is safer and stdlib-only.

**How to verify the fix**

tests/test_cli.py::test_status_json_locate_rejected — build_parser().parse_args(['status','--json','--locate']) raises SystemExit(2) once the group is added.

<details><summary>Verification trace</summary>

Traced: cli.py:41-43 — `if args.json: print(json.dumps(...)); return 0` executes before the args.locate block at line 50, so `b2ctl status --json --locate [--seconds N]` accepts all flags, emits JSON, and silently drops the locate/seconds intent with no indication in output or exit code. No guard elsewhere; argparse happily accepts the combination. Bumped from P4 to P3: a silently ignored flag is a (minor) behavioral gap, not pure docs/style — the rubric reserves P4 for changes with no behavior effect.

</details>

## F-070 — Six CLI subcommands call watch's underscore-private workflow functions — all ZFS lifecycle logic is trapped inside the 848-line interactive module

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:82`
- **Category**: structure
- **Verdict**: CONFIRMED
- **Also independently reported as**: Lifecycle wrappers always exit 0 — failures invisible to scripts and cron

**Evidence**

`watch._cmd_offload(spec.load())` (and _cmd_replace/_cmd_create/_cmd_destroy/_cmd_swap/_cmd_demote at cli.py 87-109) reach into private names of the interactive loop module. watch.py mixes the select() loop, hotplug detection, confirm UI, ghost wipe, and every ZFS workflow; RAID workflows by contrast live in their own raid_actions.py.

**Failure scenario**

Task A/D refactors of watch internals (prompt rework, _assign_free_disk unification) silently break `b2ctl offload/replace/create/destroy/swap/demote` with runtime AttributeError — nothing marks these privates as a public CLI contract; watch.py keeps growing past 848 lines as every new workflow lands there.

**Suggested fix**

Extract the ZFS workflows into a new module (e.g. b2ctl/zfs_actions.py, public functions offload()/replace()/swap()/demote()/create()/destroy(), mirroring raid_actions.py); watch keeps only the loop/hotplug/prompt shell and cli calls zfs_actions directly. Natural landing spot for Task D's shared _assign_free_disk.

*Verifier note:* Suggested fix is sound and stdlib-only. Note also cli.py's _DRY_RUN coupling (main() sets watch._DRY_RUN, and _cache_add/_log_add/_burnin read watch._DRY_RUN at lines 126/145/161) — the extraction should move the dry-run flag into the new zfs_actions module (or common/config) or the same hidden coupling just relocates. | Merged duplicate's note: Fix is right: have watch._cmd_* return bool at each existing ok/fail print site, map to `return 0 if ... else 1` in the cli wrappers, and give _log_cmd/_rollback_cmd int returns (replacing the lambdas at 552/556 with direct set_defaults(func=...) after adding an args-taking signature).

**How to verify the fix**

tests/test_zfs_actions.py (new, per one-file-per-module rule) — move the existing workflow tests from test_watch.py; test_watch.py keeps only loop/hotplug/prompt tests.

<details><summary>Verification trace</summary>

Traced cli.py:82-108: six subcommand handlers (_offload/_replace/_create/_destroy/_swap/_demote) call watch._cmd_* underscore-privates; grep confirms all six live in watch.py, which is exactly 848 lines and also contains the select() loop, hotplug handling (_handle_new_disk), confirm UI, ghost wipe, and _assign_free_disk. raid_actions.py exists as the public-function counterpart for RAID workflows, so the asymmetry claim is accurate. Not intentional per CLAUDE.md — the module map describes watch.py as the interactive loop, and Tasks A/D plan refactors of exactly these internals, making the silent-AttributeError risk concrete. Pure structure/maintainability debt with no current runtime defect → P3.

</details>

## F-071 — b2ctl check reports the number of unique bay labels as 'Controllers found'

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:241`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`print(f"  {ok_mark} Controllers found: {len(set(bm.values()) or {0})} "` — but bk.bay_map() returns serial -> "enclosure:slot" (hba.py:146 docstring), so set(bm.values()) is the set of distinct bays, not controllers. The `or {0}` fallback also prints '1' when the map is empty.

**Failure scenario**

On the real R620 with one SAS2308 and 6 mapped disks, `b2ctl check` prints 'Controllers found: 6 (6 disks in bay map)'. With sas2ircu returning nothing (0 disks) it prints 'Controllers found: 1 (0 disks in bay map)'. An operator validating a new box gets wrong controller topology info from the very command meant to verify the environment.

**Suggested fix**

Count controllers from the actual source: for IT mode use len(backend._detect_sas2ircu_controllers() or [0]); for RAID mode use the perccli controller count; or drop the controller claim and print 'Bays mapped: {len(bm)} disks'.

*Verifier note:* Simplest correct fix per the codebase: derive enclosures as {v.split(':')[0] for v in bm.values()} if an 'enclosures' count is wanted, or just print 'Bays mapped: {len(bm)} disks' and drop the controller claim — counting actual controllers would need a new backend hook (sas2ircu LIST / perccli show ctrlcount parse), which is more surface than this line warrants.

**How to verify the fix**

tests/test_cli.py::test_check_controller_count — mock get_backend() with bay_map() returning 6 serial->bay entries from one controller; run _check; assert output says 1 controller (or the relabeled bays-mapped line), not 6.

<details><summary>Verification trace</summary>

Traced: hba.py:145-164 bay_map() returns {serial: 'enclosure:slot'} (docstring at 146 and code at 163 both confirm), so set(bm.values()) at cli.py:241 counts distinct bay labels, not controllers — 6 mapped disks on one SAS2308 prints 'Controllers found: 6'. Also verified the fallback: with bm empty, set() is falsy so `or {0}` yields {0} and len is 1 — 'Controllers found: 1 (0 disks in bay map)' despite zero evidence of a controller. RAID backend bay_map has the same serial->enc:slot shape. Wrong-but-harmless diagnostic text in a verification command: P3.

</details>

## F-072 — b2ctl update (as root) crashes with an unhandled FileNotFoundError traceback when a bundled data file is absent — install.sh explicitly treats bay_map.json as optional

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:313`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

cli.py:313 `_shutil.copy2(src, dest)` in _sync_resource is unguarded; src is the __file__-relative bundled copy (`/opt/b2ctl/bay_map.json`). install.sh:147 `[ -f "${SRC_DIR}/bay_map.json" ] && cp ...` deploys it only if present, so installs from a checkout without bay_map.json legitimately lack the file.

**Failure scenario**

A box deployed via install.sh from a tree without bay_map.json (pre-v0.8.x checkout, or the operator deleted the sample) and no /etc/b2ctl/bay_map.json yet: root runs `b2ctl update` to bind resources — after printing the validate table it dies mid-sync with a raw FileNotFoundError traceback, leaving config.json unwritten and ssd_spec possibly unsynced (bay_map is processed first in _MANAGED).

**Suggested fix**

In _sync_resource, check `os.path.exists(src)` first and return a new state like "missing-bundled" (printed as a [!] row, and skip the `cfg[key] = dest` binding for that entry in _update); wrap the copy2 calls in try/except OSError returning an error state.

*Verifier note:* Fix is correct and stdlib-only; extend it to guard src before BOTH branches (line 313 copy2 and line 315 filecmp.cmp both raise when src is absent). In _update, on the "missing-bundled" state skip the `cfg[key] = dest` binding only if dest also does not exist — if an operator-managed /etc copy is already there, binding it is still correct.

**How to verify the fix**

tests/test_cli.py::test_sync_resource_missing_bundled — point _sync_resource at a tmp dir with no bundled file and assert it returns the skip state instead of raising, and that _update completes and still syncs the remaining _MANAGED entry.

<details><summary>Verification trace</summary>

Traced cli.py:304-321 and install.sh:147. `_shutil.copy2(src, dest)` at line 313 is unguarded and src is the __file__-relative bundled copy (/opt/b2ctl/bay_map.json); install.sh:147 (`[ -f "${SRC_DIR}/bay_map.json" ] && cp ...`) explicitly deploys it only if present while ssd_spec.json (line 146) is copied unconditionally — so an install lacking the bundled bay_map is a deployment the installer itself supports. With no /etc/b2ctl/bay_map.json, root `b2ctl update` raises FileNotFoundError at line 313; bay_map is first in _MANAGED (line 299) and config.json is written only after the loop (line 365), so ssd_spec stays unsynced and config unbound, exactly as claimed. Additionally (not in the finding): when dest EXISTS but src is missing, filecmp.cmp at line 315 raises the same FileNotFoundError. Reachability is narrow — the current tree bundles codes/bay_map.json, so it needs an older/stripped checkout — hence P3, a non-main-path crash on a legitimate but uncommon deployment.

</details>

## F-073 — No validation of --seconds/seconds: negative value crashes and leaks dd readers

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/cli.py:467`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`st.add_argument("--seconds", type=int, default=locatemod.DEFAULT_SECONDS, ...)` (and the locate positional at line 478) accept any int. blink_many Popens dd on every risky disk then calls time.sleep(seconds); time.sleep(-1) raises ValueError before the kill loop runs.

**Failure scenario**

`b2ctl status --locate --seconds -1` (typo for a flag, or scripted arithmetic gone negative): dd processes are spawned against every at-risk disk, time.sleep(-1) raises ValueError, the CLI dies with a traceback, and the orphaned dd readers keep sequentially reading entire 1TB pool members at full throughput until EOF — sustained I/O load on production disks with nothing to stop it.

**Suggested fix**

Add a stdlib argparse type: def _pos_int(s): v=int(s); if v <= 0: raise argparse.ArgumentTypeError('seconds must be > 0'); return v — use it for status --seconds and the locate positional. (Belt-and-braces: wrap blink_many's sleep in try/finally so procs are always killed — locate.py.)

*Verifier note:* The suggested _pos_int argparse type is correct and stdlib-only; apply it to both status --seconds and the locate positional. The belt-and-braces part is the more important half: wrap blink_many's sleep in try/finally around the kill/wait loops in locate.py so procs are always reaped regardless of input validation.

**How to verify the fix**

tests/test_cli.py::test_seconds_must_be_positive — build_parser().parse_args(['status','--locate','--seconds','-1']) raises SystemExit(2); same for ['locate','1:4','-0'] style inputs argparse accepts as negative numbers.

<details><summary>Verification trace</summary>

Traced: cli.py:467 (--seconds) and :478 (locate positional) use bare type=int. argparse accepts '-1' as a negative-number value. For status --locate with a non-empty risky list, locate.blink_many (locate.py:96-106) Popens one dd per dev, then calls time.sleep(seconds) with NO try/finally; time.sleep(-1) raises ValueError, the kill/wait loops never run, the CLI dies with a traceback, and the detached dd children keep sequential-reading the disks to EOF. The locate subcommand path is milder: blink_disk->blink either sleeps inside a try/finally (ledctl off is guaranteed) or hits subprocess.run(timeout=-1) whose TimeoutExpired is caught — so the real damage is the blink_many path. Reachability requires at least one WARNING/CRITICAL disk, hence low severity.

</details>

## F-074 — Disk.is_spare substring test ('"spare" in vdev') misclassifies the faulted member sitting under a transient spare-N vdev as a hot spare

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/common.py:117`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

return self.vdev is not None and "spare" in self.vdev  — during hot-spare activation zpool status nests the failed original leaf under 'spare-1' (see tests/helpers.py _SPARE_N_STATUS); zfs._parse gives that disk vdev="spare-1", while the actual spare device ends up vdev="spares" via the later spares-section entry.

**Failure scenario**

tank's hot spare auto-activates after a member faults: the REMOVED original member gets is_spare=True, so ui._status_cell (ui.py:40→45) routes it through the spare branch and prints an uncolored 'REMOVED' instead of red, and watch.py:498/640 ('not d.is_spare') silently drop the very disk that needs replacing from the [r]eplace/[s]wap candidate lists.

**Suggested fix**

Tighten the property to the spares section only: `return self.vdev == "spares"` (watch.py already uses that exact test for spare selection), keeping members under transient spare-N/replacing-N vdevs classified as regular pool members.

*Verifier note:* Fix `return self.vdev == "spares"` is safe and consistent: the INUSE spare's topo entry is overwritten by the spares-section entry (same token key), so it keeps vdev='spares' and core.py:58/65 (spares_replacing INUSE mapping) plus the ui spare branch still work; the removed member then falls into the in_pool branch and renders red. Matches the exact test watch.py already uses for spare selection (lines 367/509/652).

**How to verify the fix**

tests/test_common.py — add test_is_spare_excludes_spare_n_member: _disk(vdev="spare-1", vdev_state="REMOVED").is_spare is False and _disk(vdev="spares", vdev_state="AVAIL").is_spare is True; plus a tests/test_ui.py assertion that the REMOVED member's STATUS cell is red.

<details><summary>Verification trace</summary>

Traced: common.py:117 `"spare" in self.vdev`. zfs._parse pushes 'spare-1' onto vdev_stack (matched by _VDEV_RE, zfs.py:18) so the REMOVED original leaf gets vdev='spare-1' → is_spare=True; the spare device's later spares-section entry overwrites its topo keys with vdev='spares' (verified against tests/helpers.py:93-110 _SPARE_N_STATUS). Consequences verified: ui._status_cell (ui.py:39) takes the spare branch and at line 45 returns the vdev_state uncolored (REMOVED not red), and watch.py:498/640 exclude the removed member from the [r]eplace/[s]wap candidate lists. Impact is slightly overstated though: _cmd_offload (watch.py:335) does not filter is_spare so the disk remains actionable there, and on the real tank (one spare, INUSE during activation) [r]eplace/[s]wap would abort at 'no AVAIL spare' (watch.py:509-511, 652-653) even if the disk were listed — the exclusion only bites with ≥2 spares. Transient-window, mostly cosmetic misclassification → P3 as filed.

</details>

## F-075 — set_mode silently discards all existing config on a malformed file and writes non-atomically

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/config.py:142`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

Lines 137-142: on JSONDecodeError the existing file becomes `data = {}` and line 144-146 rewrites CONFIG_PATH in place (open('w') + json.dump, no tmp+os.replace). Line 143 `data.setdefault("controller", {})["mode"]` also raises TypeError if the file has `"controller": "raid"` (valid JSON, non-dict), crashing install_profile after the tools were installed.

**Failure scenario**

config.json has a trailing-comma typo and the operator runs `b2ctl install --flash`: their tool_paths / bay_map_path / ssd_spec_path overrides are silently erased and replaced by just {"controller": {"mode": "it"}}. Separately, a crash or full /etc during the in-place write leaves truncated JSON (which load() then treats as 'all defaults', masking the loss).

**Suggested fix**

If the existing file exists but fails to parse, refuse to overwrite (print the parse error and ask the operator to fix it) instead of replacing it; guard non-dict "controller" the same way; write to CONFIG_PATH + ".tmp" and os.replace() for atomicity.

*Verifier note:* Fix is sound and stdlib-only. Also guard non-dict top-level data (isinstance(data, dict)) the same way as non-dict "controller" — a top-level JSON list passes json.load and crashes setdefault with AttributeError. For atomicity, write to a tmp file in the same directory (/etc/b2ctl) and os.replace(); a cross-filesystem tmp path would break the rename.

**How to verify the fix**

tests/test_config.py: set_mode("it") with a malformed existing file raises/warns and leaves the file byte-identical; with {"controller": "raid"} it does not raise; a successful write preserves unrelated keys.

<details><summary>Verification trace</summary>

Traced all three claims: (1) lines 141-142 turn a malformed existing file into data = {} and lines 144-146 rewrite CONFIG_PATH, silently erasing tool_paths/bay_map_path/ssd_spec_path overrides despite the docstring 'Preserves any other keys already in the file'; (2) line 143 data.setdefault("controller", {})["mode"] raises TypeError when the file has "controller": "raid" (valid JSON, so the except at 141 does not fire) — and set_mode is called from installer.py:227 at the end of install_profile, after tools are installed; (3) the write is open('w') + json.dump with no tmp+os.replace, so a crash/ENOSPC mid-write leaves truncated JSON that load() silently treats as defaults. P3 fits: config-file-only loss, needs a pre-existing malformed/mis-shaped file plus an install-profile run.

</details>

## F-076 — validate() does not catch subprocess.TimeoutExpired from the tool probe, crashing `b2ctl update` when a probed binary hangs

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/config.py:179`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

subprocess.run([path], capture_output=True, timeout=5) ... except (FileNotFoundError, PermissionError, OSError): — subprocess.TimeoutExpired derives from SubprocessError, not OSError, so a probe that exceeds 5 s propagates out of validate().

**Failure scenario**

perccli or sas2ircu invoked with no args stalls >5 s probing a busy/wedged controller (or the configured tool_path points at a hung NFS mount): `b2ctl update` and any caller of config.validate() dies with an unhandled subprocess.TimeoutExpired traceback instead of reporting a 'found but won't execute' warn row.

**Suggested fix**

Add subprocess.TimeoutExpired to the except tuple (or catch subprocess.SubprocessError alongside OSError) and treat a timeout as can_run=True-with-warn ('responds slowly') so validation always returns rows instead of raising.

*Verifier note:* Add subprocess.TimeoutExpired to the except tuple (stdlib). Do not fold it into can_run=True as the finding's fix suggests — that would print 'ok' for a binary that hung; catch it separately and emit a distinct warn row like 'found but hung during probe (>5s)'. Note subprocess.run already kills the child on timeout, so no cleanup needed. Also FileNotFoundError/PermissionError are OSError subclasses — the tuple can be simplified to (OSError, subprocess.TimeoutExpired).

**How to verify the fix**

tests/test_config.py::test_validate_tool_probe_timeout_is_warn — patch subprocess.run to raise subprocess.TimeoutExpired(cmd, 5); assert validate() returns normally and the tool row has status 'warn'/'ok', not an exception.

<details><summary>Verification trace</summary>

Traced: line 177 subprocess.run([path], capture_output=True, timeout=5); the except at line 179 is (FileNotFoundError, PermissionError, OSError). subprocess.TimeoutExpired subclasses SubprocessError (direct Exception child), not OSError, so it propagates out of validate(). Caller confirmed: cli.py:329 `results = _cfg_mod.validate()` inside `b2ctl update` (and config validate), with no enclosing handler. Reachability is narrow — sas2ircu/perccli with no args normally print usage instantly — but a tool_path on a hung NFS mount or a wedged-controller perccli makes it real. P3: error-handling gap on a diagnostic path, no data risk.

</details>

## F-077 — SMART thread pool hardcoded to max_workers=4 serialises the 8-bay scan into two waves

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/core.py:44`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

core.py:44 `with ThreadPoolExecutor(max_workers=4) as executor:` — the R620 has 8 bays (plus NVMe), so smartctl reads run in two sequential batches; each smartctl is ~0.5-1 s of I/O-bound wait, so the SMART phase takes ~2x the necessary wall time (and worse when one disk in wave 1 is slow, head-of-line blocking wave 2).

**Failure scenario**

Full 8-disk box: SMART phase ~2 s instead of ~1 s on every status/refresh/scan; combined with the redundant probes elsewhere it compounds into the visible multi-second lag on each watch command.

**Suggested fix**

Size the pool to the workload: `max_workers=min(16, max(1, len(smart_targets)))` — smartctl calls are subprocess-wait bound, so one thread per disk is safe and stdlib-only.

*Verifier note:* Fix is fine and stdlib-only: max_workers=min(16, max(1, len(smart_targets))). Keep the max(1, ...) guard — ThreadPoolExecutor raises ValueError on max_workers=0 and smart_targets can be empty (all-ghost or no-disk edge, sim harness).

**How to verify the fix**

tests/test_core.py::test_smart_pool_sized_to_targets — monkeypatch ThreadPoolExecutor via tests/helpers.py to capture max_workers and assert it equals len(smart_targets) for the 8-disk fixture.

<details><summary>Verification trace</summary>

core.py:44 hardcodes max_workers=4 while smart_targets is all non-ghost disks (8+ bays plus NVMe), so the SMART phase takes roughly 2x the per-disk latency. Verified there is no documented throttling rationale — line 26 in the same function already sizes a pool to len(potential_ghosts), so 4 is arbitrary, not a controller-load policy. One inaccuracy in the evidence: ThreadPoolExecutor is a work queue, not two sequential batches, so a slow disk in 'wave 1' delays only its own worker, not a whole second wave — the ~2x wall-time claim stands but the head-of-line framing is wrong. Impact is ~1 s per scan on the real 8-disk box: minor perf, P3 as filed.

</details>

## F-078 — Disk table sort on bay is lexicographic, so double-digit slots order as 0:1, 0:10, 0:11, 0:2

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/core.py:73`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

disks.sort(key=lambda d: (d.bay or "zz", d.dev)) — bay is a string like '0:10'; str comparison puts '0:10' before '0:2'.

**Failure scenario**

Any backplane or relabel with slot >= 10 (10-bay R640 slot 9 is safe, but 12-bay R720xd noted in hba.py's own docstring, PERC enclosures like '32:10', or NVMe labels 'PCIe2:10') renders the status/watch table out of physical order, making 'pull the disk in bay X' error-prone.

**Suggested fix**

Sort with a numeric-aware key: split bay on ':' and use (prefix, int(slot)) when the slot part is numeric, falling back to the raw string otherwise (stdlib only).

*Verifier note:* Suggested fix is fine and stdlib-only. Implement as a key that splits on ':' and returns (prefix, 0, int(slot)) when the last segment is a digit string, else (bay, 1, 0) fallback — keep the fallback because NVMe/PCIe labels and map overrides are free-form strings, and keep the 'zz' sentinel for bay-less disks.

**How to verify the fix**

tests/test_core.py::test_scan_sorts_bays_numerically — disks with bays '32:2', '32:10', '32:1'; assert output order 1, 2, 10.

<details><summary>Verification trace</summary>

core.py:73 sorts on the raw bay string; baymap.remap_slot returns 'enc:slot' strings (f"{enc}:{slot}"), so '0:10' sorts before '0:2' lexicographically. Not triggered on the current 8-bay R620s (slots 0-7), but 12-bay chassis are acknowledged in hba.py's own docstring, PERC enc:slot values like '32:10' exist in RAID mode, and bay_map 'map' overrides are arbitrary strings — so slot >= 10 is a supported input that renders the table out of physical order. Also d.dev tiebreak is lexicographic (sdb > sdaa), same class of nit.

</details>

## F-079 — scan_one() runs the entire fleet scan (all-disk SMART + sas2ircu + zpool) to build one hot-plugged Disk

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/core.py:110`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

core.py:110 `for d in scan(tbw_table):` — scan_one is called from watch._handle_new_disk (watch.py:118) on every hotplug event and from _wipe_ghost (watch.py:265); it re-enumerates all 8 disks, re-reads SMART on each, re-runs the sas2ircu probes and zpool topology, then throws away everything except one Disk.

**Failure scenario**

Operator inserts a replacement disk during watch: the 'NEW DISK DETECTED' panel appears only after a full ~5-15 s scan (plus the 2 s settle sleep), during which the select() loop is blocked — no keystrokes are handled and a second hotplug in the window is queued behind it.

**Suggested fix**

Scan only the target: run `lsblk -dnb -P -o ... /dev/sdX` for the single device (hba._lsblk_pairs already accepts full cols; add an optional dev arg), attach its bay from one bay_map() call, smart.read() that one Disk, and match membership against one topology() snapshot — reuse the existing helpers rather than the whole pipeline.

*Verifier note:* Fix direction is sound and stdlib-only: single-device lsblk (-P pairs — keep the -P gotcha from CLAUDE.md section 6), one bay_map() call for bay attach, smart.read on the one Disk, membership from one zfs.topology() snapshot. Caveat: the full-scan version also benefits from the ghost/udev path when the inserted disk is initially OS-rejected; the targeted version should still fall back to Disk(dev=dev) when lsblk shows nothing, as today.

**How to verify the fix**

tests/test_core.py::test_scan_one_reads_smart_only_for_target — helpers recorder around smart.read/run asserting exactly one smartctl target when scan_one('/dev/sdh', tbw) runs against the 8-disk fixture.

<details><summary>Verification trace</summary>

core.py:108-114: scan_one iterates the full scan() (enumerate all disks, sas2ircu bay map, ghost probing, SMART on every disk in two waves, zpool topology) and discards all but one Disk. Called from watch._handle_new_disk (watch.py:118, after a 2 s settle sleep) and _wipe_ghost (watch.py:265); the watch loop is synchronous (select at watch.py:800, handler invoked inline at watch.py:841), so keystrokes are indeed unhandled during the scan. Real, but I downgrade from P2: it fires only on hotplug / ghost-wipe (rare, operator-driven physical events), realistic duration is single-digit seconds not 5-15 s, and the flow then blocks on interactive input anyway (_assign_free_disk). Minor perf/structure debt, not a user-facing regression on a hot path.

</details>

## F-080 — The lsblk -P KEY="value" parser (_lsblk_pairs) — the section-6 'lsblk must use -P' gotcha — has no regression test; enumerate tests bypass it by mocking _lsblk_pairs

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba.py:26`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

hba.py:26 `out = run(["lsblk", "-dnb", "-P", "-o", cols])` + _PAIR_RE (:21) is the parsing CLAUDE.md section 6 flags as previously broken ('positional parsing breaks because MODEL contains spaces'); tests/test_hba.py::TestEnumerateNvmeByIdBay patches _lsblk_pairs with pre-parsed dicts, and watch._block_devs/_wait_for_block_device tests also mock it — no test ever parses a raw `NAME="sda" MODEL="Samsung SSD 870 EVO 1TB" ...` line.

**Failure scenario**

A change to _PAIR_RE (e.g. \w+ -> [A-Z]+ or mishandling embedded quotes/empty values) or dropping -P from the argv would mis-split 'Samsung SSD 870 EVO 1TB' exactly like the original bug, corrupting model/serial for every disk (and thus TBW lookup and bay matching), with the whole suite still green because the parser is always mocked.

**Suggested fix**

Add a regression test feeding hba.run with two verbatim lsblk -P lines (one SATA-behind-SAS with spaces in MODEL, one NVMe) and asserting the parsed dicts, plus asserting '-P' is in the argv passed to run.

*Verifier note:* Fix is correct and stdlib-only. Include a MODEL with spaces ('Samsung SSD 870 EVO 1TB'), an empty value (SERIAL=""), and assert run() was called with '-P' in argv (mock_run.call_args). Lowercase-key coverage is unnecessary (lsblk -P emits uppercase keys) but harmless.

**How to verify the fix**

tests/test_hba.py::TestLsblkPairs::test_parses_model_with_spaces_and_empty_values, ::test_cmd_uses_dash_P_pairs_format.

<details><summary>Verification trace</summary>

Traced: test_hba.py:146 (TestEnumerateNvmeByIdBay) and test_watch.py:422/434 all patch _lsblk_pairs with pre-parsed dicts; no test feeds it raw lsblk output or asserts '-P' in the argv. The one nuance the finding understates: TestVdUsage (test_hba.py:110-122) DOES run raw KEY="value" lines through _PAIR_RE via vd_usage, including empty values — but its values contain no spaces and its keys are all uppercase, so both hypothesized regressions (\w+ -> [A-Z]+, or breaking spaces-in-values / dropping -P) would still pass the whole suite. The sim harness exercises real parsing but is manual, not part of pytest. This is exactly the CLAUDE.md §6 solved gotcha with no regression net. Test-coverage gap -> P3 per rubric.

</details>

## F-081 — _by_id_index NVMe rank ties: nvme-uuid.* and namespace-suffixed nvme-<model>_<serial>_1 links score equal to the friendly link, so by_id is decided by os.listdir order

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba.py:111`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

rank = {"ata-": 0, ..., "nvme-eui.": 5, "nvme-": 4} — on Debian 13 (systemd/udev 257) /dev/disk/by-id also contains nvme-uuid.<uuid> and the namespace-qualified duplicate nvme-Samsung_SSD_..._S7XX12345_1; all start with 'nvme-' and score 4, and `if real not in best or s < best[real][0]` keeps whichever os.listdir() yields first (arbitrary inode order).

**Failure scenario**

On the real 2xM.2 NVMe card: after a reboot d.by_id can flip to nvme-uuid.86f1e3... — the bay_map.json back-panel 'by-id' key (documented as the friendly nvme-<model>_<serial> link) silently stops matching, the NVMe bay label reverts to the raw PCIe BDF, and the CLAUDE.md §1 'by_id is the friendly key' contract breaks nondeterministically between boots.

**Suggested fix**

Extend rank with "nvme-uuid.": 6 (checked before "nvme-"), and break score-4 ties deterministically by preferring the shorter name (the non-_<nsid>-suffixed link): compare (s, len(name), name) instead of s alone.

*Verifier note:* Fix is correct and stdlib-only, with one emphasis: 'nvme-uuid.': 6 must be INSERTED BEFORE 'nvme-' in the rank dict (score() returns on first startswith match in insertion order — same trick the comment at hba.py:110 documents for nvme-eui.). The (score, len(name), name) tie-break then deterministically prefers the un-suffixed friendly link over its _<nsid> duplicate; both parts are needed since a long model name can exceed len('nvme-uuid.'+36). Add a test with all four link forms pointing at one realpath.

**How to verify the fix**

tests/test_hba.py::test_by_id_index_prefers_friendly_nvme_over_uuid_and_suffixed — mock listdir returning ['nvme-uuid.x', 'nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345_1', 'nvme-Samsung_SSD_990_EVO_Plus_4TB_S7XX12345', 'nvme-eui.0025...'] in several orders; assert the unsuffixed friendly link always wins.

<details><summary>Verification trace</summary>

Traced: rank (hba.py:111-112) gives every 'nvme-'-prefixed link except 'nvme-eui.' a score of 4 (score() iterates dict insertion order, so nvme-eui. is caught first, but nvme-uuid.* and the namespace-suffixed nvme-<model>_<serial>_1 duplicates all fall through to 'nvme-' = 4), and hba.py:130 keeps the first-listed link on a tie (`s < best[real][0]` is strict), i.e. arbitrary os.listdir order. systemd >=256 (Debian 13 ships 257) creates the _<nsid>-suffixed by-id duplicates, so the tie is real on the NVMe boxes; nvme-uuid.* presence I could not verify in this environment but the _1 duplicate alone breaks determinism. Consequence matches the claim: d.by_id can flip between boots, breaking the bay_map.json 'by-id' friendly-key match (bay reverts to raw BDF). The existing test (test_hba.py:131) only covers eui-vs-model. No data risk — any winning link is still a stable by-id name for ZFS actions — so display/contract nondeterminism = P3.

</details>

## F-082 — hba_raid._parse_bay_map (perccli 'Drive .../eE/sS Device attributes' + 'SN =' parser) is untested — every enumeration test patches bay_map() wholesale

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba_raid.py:107`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

hba_raid.py:107-121 pairs `Drive /cN/eE/sS Device` headers with the following `SN = <serial>` line; enumerate_disks uses it for the serial<->bay attribution of every RAID member (bay_to_sn, :247) and for hidden-drive serials (:275). tests/test_hba_raid.py patches `raid.bay_map` directly in both enumerate tests, so no fixture of `perccli /cX/eall/sall show all` detailed output exists.

**Failure scenario**

A perccli formatting variant (extra indentation, 'SN =' appearing in an unrelated section before the next Drive header, or a drive with an empty SN) silently maps the wrong serial to a bay; RAID members then show the wrong SERIAL/bay in the table and raid-replace target picking by serial (_pick_member) selects the wrong physical drive to offline.

**Suggested fix**

Capture a realistic multi-drive `/c0/eall/sall show all` block (mirroring the R640 output style used for _VALL) as a fixture and unit-test _parse_bay_map against it, including an SN-less drive and interleaved attribute noise.

*Verifier note:* Fix is right: add a realistic multi-drive eall/sall 'Device attributes' fixture (same provenance style as _VALL) and test _parse_bay_map directly, including (a) an SN-less drive followed by a section containing a stray 'SN =' (asserts no stale-slot binding — currently this WOULD mismap) and (b) interleaved attribute noise. Consider also resetting current_slot on any non-Device section header as the accompanying code hardening.

**How to verify the fix**

tests/test_hba_raid.py::TestParseBayMap::test_two_drives_serial_to_encslot, ::test_sn_line_only_binds_to_preceding_drive_header, ::test_missing_sn_skipped.

<details><summary>Verification trace</summary>

Verified: grep over codes/tests/ finds zero references to _parse_bay_map; both enumeration tests in tests/test_hba_raid.py patch raid.bay_map wholesale (lines 101 and 129), and no fixture of `perccli /cX/eall/sall show all` detailed output exists (the sim's fake perccli emits only a minimal 3-line Drive/SN block, sim/bin/perccli:27-31). The parser feeds serial<->bay attribution for every RAID member (bay_to_sn, hba_raid.py:247) and hidden drives (:275), and raid_actions._pick_member can select by serial — so a mismap picks the wrong physical drive to offline. One correction to the failure scenario: an 'SN =' line appearing before the next Drive header cannot mismap on its own (current_slot is cleared after the first SN, :121); the real edge is an SN-less drive section, where the stale current_slot survives non-matching header lines until an unrelated later 'SN =' binds to it — worth a dedicated test case exactly as the fix proposes.

</details>

## F-083 — Dead code left in the tree: hba_raid._lsblk_pairs (unused duplicate), zfs.resilver_status, zfs.add_mirror, ui.render_raid_volumes (kept alive only by its own test)

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba_raid.py:124`
- **Category**: maintainability
- **Verdict**: CONFIRMED
- **Also independently reported as**: hba_raid._lsblk_pairs is dead code duplicating hba._lsblk_pairs (never called anywhere in the module)

**Evidence**

`def _lsblk_pairs(cols: str) -> list[dict]:` in hba_raid.py re-implements hba._lsblk_pairs (with its own _PAIR_RE and a config-resolved lsblk path) and is called by nothing; grep shows zfs.resilver_status (zfs.py:320) and zfs.add_mirror (zfs.py:235) have zero callers in b2ctl/, tests/ and sim/, and ui.render_raid_volumes (ui.py:142) is referenced only by tests/test_ui.py:122 — production uses render_storage.

**Failure scenario**

A maintainer 'fixes' the resilver regex in zfs.resilver_status or the mirror-add path in zfs.add_mirror and observes no change on the box, because the live paths are poll_resilver_status/_replace_member; two subtly different _lsblk_pairs implementations invite the exact lsblk -P parsing regression CLAUDE §6 warns about if someone starts calling the wrong one.

**Suggested fix**

Delete hba_raid._lsblk_pairs, zfs.resilver_status, zfs.add_mirror and ui.render_raid_volumes (plus its two tests), or wire render_raid_volumes back into the RAID status view if the separate table is still wanted.

*Verifier note:* Deletion is safe, but also update the stale references: CLAUDE.md §5 module map still lists add_mirror among zfs actions, and docs/ may reference render_raid_volumes — per §4 the two docs must be updated with the code change. Remove the two render_raid_volumes tests in tests/test_ui.py in the same commit. | Merged duplicate's note: Delete it. If RAID mode ever needs lsblk parsing, import hba._lsblk_pairs; if the config-resolved lsblk path is wanted, apply that to the live hba.py copy instead.

**How to verify the fix**

cd codes && python3 -m pytest tests/ -q after removal (delete tests/test_ui.py::test_render_raid_volumes*); grep -rn for the removed names must return nothing.

<details><summary>Verification trace</summary>

All four dead symbols verified by grep across b2ctl/, tests/, and sim/: hba_raid._lsblk_pairs (def at line 124) has zero callers — the RAID backend imports hba.enumerate_disks which uses hba._lsblk_pairs, leaving two near-duplicate lsblk -P parsers in the tree (exactly the parsing area CLAUDE.md §6 flags); zfs.resilver_status (zfs.py:320) has zero callers — the live path is zfs.poll_resilver_status (watch.py:408 and tests); zfs.add_mirror (zfs.py:235) has zero callers — watch's attach flow uses zfs.attach (watch.py:199); ui.render_raid_volumes (ui.py:142) is referenced only by tests/test_ui.py:122-131 while production renders volumes through core.assemble_storage + render_storage (cli.py:46, watch.py:325 feed raid_volumes() into the unified storage table). The maintainer-edits-the-dead-copy failure mode is credible precisely because the dead names are the obvious ones to grep for.

</details>

## F-084 — attach_bays serial-prefix matching is copy-pasted between the two backends — the docstring itself admits the duplication

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba_raid.py:315`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`"""Fill disk.bay from perccli, same algorithm as hba.attach_bays."""` — hba_raid.py:314-330 and hba.py:167-191 contain the identical loop (exact-serial match, then bidirectional startswith prefix fallback, then baymap.remap_slot).

**Failure scenario**

The serial-matching fallback is exactly the area CLAUDE §6 flags as regression-prone; a fix applied to hba.attach_bays only (e.g. guarding empty serials so a blank never prefix-matches everything) leaves RAID boxes on the old behavior — the two identically-configured servers then report different bays for identical disks.

**Suggested fix**

Move the matching loop into the shared baymap module (e.g. baymap.assign_bays(disks, bm, panels)); both backends' attach_bays become 3-line wrappers that fetch bm and delegate.

*Verifier note:* Fix is right and stdlib-only: hoist the loop into baymap (e.g. baymap.assign_bays(disks, bm, panels)); both attach_bays become wrappers that keep their own have-tool guard and bay_map(controller) default. If finding 109's raw-vs-display split is implemented, put that in the shared helper so both backends preserve the raw locator identically.

**How to verify the fix**

tests/test_baymap.py::test_assign_bays_prefix_fallback — shared fixture exercised once; tests/test_hba.py and tests/test_hba_raid.py assert both backends delegate (patch baymap.assign_bays and assert called).

<details><summary>Verification trace</summary>

Verified token-level duplication: hba.py:183-191 and hba_raid.py:322-330 contain the identical loop (exact serial match -> bidirectional startswith prefix fallback -> baymap.remap_slot), and the hba_raid docstring at line 315 states 'same algorithm as hba.attach_bays'. The divergence risk is not hypothetical — it has already happened semantically: hba.attach_bays' docstring (hba.py:173-175) declares the remapped bay 'display-only (LEDs are driven by device, not slot)', which holds in IT mode, but the copied loop in hba_raid feeds bays that RAID-mode actions consume as perccli selectors (finding 109). A future guard added to only one copy (e.g. empty-serial protection — note d.serial='' members from a bay_map miss at hba_raid.py:257 currently rely on the outer `if d.serial:` check) would split behavior between the two identically-named servers.

</details>

## F-085 — RAID mutating actions hardcode controller 0 while enumeration is multi-controller aware — Disk drops the controller index

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/hba_raid.py:350`
- **Category**: scalability
- **Verdict**: CONFIRMED

**Evidence**

`def _pd(enc_slot: str, controller: int = CONTROLLER) -> str:` (CONTROLLER = 0) is used by locate/set_offline/set_missing/start_rebuild/add_hotspare/set_jbod, and raid_actions never passes a controller (e.g. raid_actions.py:104 `hba_raid.set_offline(d.bay, dry_run=dr)`). Yet `_vall_data()` tags every member `m["controller"] = idx` (line 219) and `enumerate_disks()` discards it when building Disk (lines 252-264).

**Failure scenario**

A box with two controllers and config controller.index="all" (explicitly supported): operator runs `b2ctl raid-replace` on a member enumerated from /c1 at e32:s2 — set_offline/set_missing/locate execute against /c0/e32/s2, offlining and LED-blinking a different physical drive on an unrelated VD, breaking its redundancy.

**Suggested fix**

Add a `ctrl: int | None` field to the Disk dataclass (common.py), populate it in hba_raid.enumerate_disks from m["controller"]/pd rows, and thread it through _pd and all action wrappers; raid_actions passes d.ctrl.

*Verifier note:* Fix is correct: add ctrl (int|None) to the Disk dataclass in common.py, set it from m['controller']/pd rows in both loops of hba_raid.enumerate_disks, and have raid_actions/locate pass d.ctrl (defaulting to CONTROLLER when None) into locate/set_offline/set_missing/start_rebuild/rebuild_progress/add_hotspare/set_jbod. Also fix add_vd/del_vd call sites in raid_actions (create_vd:186, delete_vd:279) which likewise assume /c0, and the hardcoded 'perccli .../c0...' audit cmd strings passed to safety.begin_op. Stdlib-only.

**How to verify the fix**

tests/test_hba_raid.py::test_actions_target_member_controller — fixture with two controllers where the target member is on /c1; assert the built command (via fake run_check in tests/helpers.py) starts with '/c1/e32/s2', not '/c0'.

<details><summary>Verification trace</summary>

Code claim fully traced: _vall_data tags every volume and member with m['controller']=idx (hba_raid.py:217-220), enumerate_disks drops it when building Disk (lines 251-264 and 281-290), _pd defaults to CONTROLLER=0 (line 350), and no raid_actions caller ever passes a controller (raid_actions.py:100-106, 149-155, 221, 254-257) nor does locate.blink_disk (locate.py:88). The shipped default controller.index is 'all' (config.py:39), so enumeration genuinely is multi-controller while every mutating action is hardcoded to /c0 — the asymmetry is real. Severity downgraded from the claimed P1: the actual fleet (2x R620 IT-mode, R640 with one H730P) is single-controller, so the wrong-drive scenario needs hardware that does not exist in this deployment; today /c0 is always correct. It is latent scalability debt, not a live broken action path.

</details>

## F-086 — tarfile.extractall without filter — tar path traversal writes outside the temp dir as root on Python 3.13

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/installer.py:106`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

Line 106: `tf.extractall(tmp)` — on Debian 13 / Python 3.13 (Proxmox VE 9) the default extraction filter is still fully_trusted (with a DeprecationWarning); combined with the unverified download above, a crafted member like `../../usr/sbin/zpool` is written as root.

**Failure scenario**

A tampered perccli.tar.gz (no checksum protects it, see previous finding) contains `../..`-prefixed members: install_perccli extracts them as root, overwriting arbitrary system files (e.g. /usr/sbin/zpool) on the production node before the RPM step even runs. The zipfile path (line 80) is safe because ZipFile.extract sanitizes '..'; tarfile does not.

**Suggested fix**

Call `tf.extractall(tmp, filter="data")` (stdlib, available since 3.12); this also future-proofs against the 3.14 default-filter behavior change.

*Verifier note:* `tf.extractall(tmp, filter="data")` is correct and stdlib (3.12+); the perccli tar.gz contains only regular RPM files so the data filter cannot break legitimate archives, and it also silences the 3.13 DeprecationWarning.

**How to verify the fix**

tests/test_installer.py: install_perccli on a generated tar containing a '../evil' member returns (False, ...) and asserts no file was created outside the temp dir.

<details><summary>Verification trace</summary>

Line 106 is exactly `tf.extractall(tmp)` with no filter. Debian 13 (Proxmox VE 9) ships Python 3.13, where the default extraction filter is still fully_trusted (plus a DeprecationWarning printed into the install output); the 'data' default only arrived in 3.14. The zipfile contrast is accurate — ZipFile.extract sanitizes '..' components, tarfile does not. Downgraded to P3 because exploitation requires the tampered/unverified archive from finding 68 as a precondition (the Drive IDs are fixed); on its own this is one-line defense-in-depth hardening, though the consequence (root write outside tmp) is severe if triggered.

</details>

## F-087 — Prerequisite package sets drifted between install.sh and installer.ensure_prereqs, breaking the claimed '1:1 mirror' install contract

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/installer.py:148`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

installer.py:148 `subprocess.run(["apt-get", "install", "-y", "alien", "libc6-i386"], ...)` vs install.sh:85 `apt-get install -y alien unzip libc6-i386 smartmontools zfsutils-linux gdisk util-linux coreutils udev`. cli.py:257 docstring claims `b2ctl install` is a "1:1 mirror of ./install.sh" and maps --with-tools to the same behavior.

**Failure scenario**

Minimal Debian 13 box deployed with plain ./install.sh (which installs no apt packages by design), then `b2ctl install --with-tools`: tools download and install, exit looks clean, but smartmontools/gdisk are never installed — the next `b2ctl status` shows every disk as SMART-unreadable CRITICAL and any ghost-wipe fails on missing sgdisk, whereas the supposedly identical `./install.sh --with-tools` path would have installed them. The two lists will keep diverging because they are hand-maintained in two languages.

**Suggested fix**

Define one runtime-dependency list in installer.py (e.g. PREREQ_PKGS split into tool prereqs and runtime deps), have ensure_prereqs() install it, and reduce install.sh's install_tools() apt line to the same set (or have install.sh delegate to `b2ctl install` after deploying the package) — same approach as needed for the already-reported duplicated Google Drive IDs.

*Verifier note:* Sharpen the shared list into two tiers: tool prereqs (alien, libc6-i386 — what ensure_prereqs owns) vs b2ctl runtime deps (smartmontools, zfsutils-linux, gdisk); `unzip` belongs to neither for the Python path (installer.py uses stdlib zipfile) and should stay shell-only or be dropped from install.sh when it delegates. Alternatively just fix the cli.py:257 docstring to state the prereq scope difference — cheapest way to stop the contract lie.

**How to verify the fix**

tests/test_installer.py::test_prereq_list_matches_install_sh — parse codes/install.sh for the apt-get install line and assert its package set equals installer.PREREQ_PKGS (guards future drift without running apt).

<details><summary>Verification trace</summary>

All three citations verified: installer.py:148 installs only `alien libc6-i386`; install.sh:85 installs `alien unzip libc6-i386 smartmontools zfsutils-linux gdisk util-linux coreutils udev`; cli.py:257 docstring claims 'b2ctl install — 1:1 mirror of ./install.sh'. So the two --with-tools paths are demonstrably not 1:1. Practical impact is softened on the documented target (Proxmox VE 9.2 ships smartmontools/zfsutils/gdisk out of the box), so the 'every disk SMART-unreadable CRITICAL' scenario needs a minimal plain-Debian box — plausible for the sim/laptop case, not the two production R620/R640s. P3 maintainability debt fits: hand-maintained lists in two languages guaranteed to drift, same pattern as the duplicated Drive IDs.

</details>

## F-088 — raid_actions.replace() and offline() — the guided destructive PERC workflows (set offline+missing, LED, rebuild wait) — have zero tests and no tests/test_raid_actions.py exists

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/raid_actions.py:73`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

replace() (raid_actions.py:73-132) chains set_offline -> set_missing -> locate ON -> wait insert -> start_rebuild -> _wait_rebuild -> locate OFF with begin_op/end_op audit; offline() (:135-163) is similar. tests/ has no test_raid_actions.py; test_cli.py::TestRaidCommands only covers delete_vd/create_vd/assign_perc fragments and raid-replace argument parsing (`ns.func` exists), never executes replace()/offline().

**Failure scenario**

Any regression in the offline->missing ordering, the failure short-circuit (`if ok:` at :105), the LED-off-after-rebuild step, or the dry-run early-return (:119-123) ships silently; e.g. a refactor that starts rebuild before the drive swap, or leaves the locate LED on after a failed set_missing, is only discovered live on the R640 while degrading a real array.

**Suggested fix**

Create tests/test_raid_actions.py (one test file per module, per CLAUDE.md section 8) driving replace() and offline() with hba_raid.* and safety.* patched, input() side_effects for the confirm/insert prompts, asserting call order (set_offline, set_missing, locate True, start_rebuild, locate False) and audit begin/end pairing.

*Verifier note:* Fix is correct and matches house style: tests/test_raid_actions.py with hba_raid.* and safety.* patched, input() side_effects for confirm/insert, asserting call order and audit pairing — plus regression tests for the fixed 28/29/35 behaviors (start_rebuild issued when PD not Onln, LED off after insert confirm, Ctrl-C at insert aborts with end_op(False)).

**How to verify the fix**

tests/test_raid_actions.py::TestReplaceFlow::test_offline_missing_rebuild_led_order, ::test_set_missing_failure_ends_op_and_aborts, ::test_dry_run_skips_rebuild; TestOffline::test_led_on_only_after_success.

<details><summary>Verification trace</summary>

Traced: codes/tests/ contains no test_raid_actions.py (listed the directory). grep for raid_actions across tests hits only test_watch.py (patches assign_perc as a mock, never executes it) and test_cli.py TestRaidCommands, which covers delete_vd cancel, create_vd double-confirm/dry-run/IT-mode refusal, assign_perc JBOD+create menu paths, and raid-replace/raid-offline ARGUMENT PARSING only (`assert hasattr(ns, 'func')`). replace() (:73-132) and offline() (:135-163) — the set_offline->set_missing ordering, the `if ok:` short-circuit at :105, LED on/off sequencing, the :119-123 dry-run early return, and begin_op/end_op pairing — are never executed by any test. Violates the CLAUDE.md §8 one-test-file-per-module convention. Findings 28/29/35 shipping unnoticed is direct evidence of the gap. Test-coverage gap -> P3 per rubric despite the destructive subject matter.

</details>

## F-089 — Audit command lists are hand-written duplicates of what hba_raid actually executes — and have already drifted (binary name, hardcoded /c0)

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/raid_actions.py:100`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`cmds = [["perccli", hba_raid._pd(d.bay), "set", "offline"], ...]` is logged, but execution runs `run_check([_tool(), ...])` where _tool() may resolve to /usr/sbin/perccli64 or a config override; create_vd logs `["perccli", "/c0", "add", "vd", ...]` (line 182) regardless of the real controller. There is also no tests/test_raid_actions.py despite the one-test-file-per-module convention, so the drift is invisible.

**Failure scenario**

During an incident review the operator replays the logged command from ops.jsonl: it targets /c0 with a binary name that may not exist on PATH, acting on the wrong controller or failing confusingly — the audit trail does not record what actually ran.

**Suggested fix**

Have each hba_raid action expose its built command (split into `build_cmd_*()` used by both the runner and the caller, or return the cmd in the (ok, out) tuple path), and log that exact list in begin_op; add the missing test file.

*Verifier note:* Fix direction is right; simplest stdlib form: add a build_cmd(*parts) helper in hba_raid that prepends _tool() and returns the exact list, have each action execute that list, and have callers pass the same list to safety.begin_op — removing every hand-written cmds literal in raid_actions. Drop the '/c0' part of the claim from the rationale (audit and execution agree there); if multi-controller support matters, that is a separate fix in hba_raid's CONTROLLER handling.

**How to verify the fix**

tests/test_raid_actions.py (new)::test_audit_cmds_match_executed — patch run_check to capture argv, run replace/offline/create_vd dry paths, assert safety.begin_op received the identical lists.

<details><summary>Verification trace</summary>

Traced with one correction. Binary-name drift is real: the audited cmds at :100-101 (and :149-150, :182, :254, :277) hardcode 'perccli', while execution goes through run_check([_tool(), ...]) where _tool() (hba_raid.py:57-62) resolves via _pick_tool to perccli64/perccli through config._cfg.tool() — possibly a full path or config override — so ops.jsonl records a command that never ran. The '/c0 regardless of the real controller' claim is overstated: add_vd/_pd also default to CONTROLLER=0 (hba_raid.py:20), so the audit MATCHES execution there; both being hardcoded to controller 0 while enumerate_disks iterates _ctrl_indices() is a separate multi-controller execution bug, not audit drift. Missing-test-file point overlaps finding 89. Net: real maintainability drift in the audit trail, no wrong action taken by b2ctl itself -> P3.

</details>

## F-090 — Ctrl-C/EOF at the 'insert the new drive' prompt is swallowed and the replace flow continues

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/raid_actions.py:116`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`except (EOFError, KeyboardInterrupt):\n        print()` — unlike _confirm (which returns False on interrupt), the insertion prompt catches the interrupt and falls through to the rebuild logic with no drive inserted.

**Failure scenario**

Operator aborts with Ctrl-C after the drive was failed out but before inserting a replacement (e.g. wrong drive on hand). The flow continues, polls rebuild on an empty bay, and (via the 'Not in progress'==done conflation) prints 'rebuild complete' and audits success — the operator believes the abort worked AND that the array is healthy.

**Suggested fix**

On EOFError/KeyboardInterrupt at this input(), turn the locate LED off, call safety.end_op(op_id, False, ...), print that the member is already offline+missing and how to resume (`b2ctl raid-replace <bay>` again), and return 1.

*Verifier note:* Fix is right: on EOFError/KeyboardInterrupt turn the LED off (hba_raid.locate(d.bay, False, dry_run=dr)), call safety.end_op(op_id, False, ...), print that the member is already offline+missing and that rerunning `b2ctl raid-replace` after inserting resumes the flow, and return 1.

**How to verify the fix**

tests/test_raid_actions.py::test_replace_aborts_on_interrupt_at_insert_prompt — patch input to raise KeyboardInterrupt and assert start_rebuild/_wait_rebuild are not called and the return code is 1.

<details><summary>Verification trace</summary>

Traced: raid_actions.py:114-117 — `except (EOFError, KeyboardInterrupt): print()` then falls through to the rebuild logic; contrast _confirm (:19-24) which returns False on the same exceptions. After Ctrl-C with no drive inserted, the flow polls an empty bay and, via the finding-28 'Not in progress'==done conflation, prints 'rebuild complete' and audits success with the locate LED handling proceeding as if replaced. The consequence-amplifier is finding 28's bug; with 28 fixed, Ctrl-C here would attempt/wait a rebuild on an empty slot and report failure — recoverable. On its own this is an interrupt-handling gap on an already-destructive-committed step (offline+missing already ran, so 'abort' cannot undo), so P3.

</details>

## F-091 — Rollback hint for 'replace' indexes positionally into the caller's cmd list (cmds[0][4]/[5]) — fragile cross-module coupling on an EXECUTABLE hint

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/safety.py:22`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`f"zpool replace {e['pool']} {e['cmds'][0][5]} {e['cmds'][0][4]}"` assumes the caller built exactly ["zpool","replace","-f",pool,old,new]. watch builds that shape in two separate places today; cli._rollback_cmd (cli.py:449) executes `hint.split()` after a y/N.

**Failure scenario**

A future edit drops '-f' or inserts an option (e.g. '-s' sequential resilver) in one of the replace-cmd builders: indexes 4/5 now point at the wrong tokens, the stored hint becomes `zpool replace tank <old> <pool>`-style garbage, and `b2ctl rollback <op_id>` executes it against the live raidz1 pool after a confirm that displays the wrong-but-plausible command.

**Suggested fix**

Record explicit `old_dev`/`new_dev` fields in the audit entry at begin_op time (extend begin_op's signature or accept a details dict) and build rollback hints from those named fields, never by indexing cmds.

*Verifier note:* Fix is right and stdlib-only: record explicit old_dev/new_dev (or a details dict) in the audit entry at begin_op and build the replace/demote hints from named fields; keep cli.py's placeholder guard as defense in depth. Pair with finding 99's hint-content tests to lock the shapes.

**How to verify the fix**

tests/test_safety.py::test_replace_hint_from_named_fields — build an entry via begin_op with old/new, mutate the cmd shape (add a flag), assert the hint still names the correct devices.

<details><summary>Verification trace</summary>

Verified the coupling: _ROLLBACK['replace'] builds an executable hint from e['cmds'][0][5] and [4] (safety.py:21-25), which assumes the exact 6-token ['zpool','replace','-f',pool,old,new] shape built independently in watch.py:166 and watch.py:434; zfs.swap_to_spare already uses the 5-token -f-less shape (zfs.py:317), showing both shapes exist in the codebase today (swap's caller happens not to begin_op, so no current miscomputation). cli._rollback_cmd executes hint.split() after y/N. One mitigation the finding misses: cli.py refuses to execute any hint containing '<...>' placeholder tokens, so the 5-token degradation yields a non-executable hint, not a wrong command. The remaining risk — a future differently-shaped 6-token cmd (e.g. inserting '-s') producing a plausible wrong-direction executable rollback — is real fragile cross-module coupling on an executable string. Correct maintainability P3.

</details>

## F-092 — end_op silently no-ops when the audit entry cannot be read back — op result, rollback hint, and post-op verification all skipped

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/safety.py:84`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

`entry = _load_entry(op_id); if entry is None: return` — _append_jsonl/_load_entry both swallow OSError (lines 142-143, 156-157), so if /var/log/b2ctl is unwritable (disk full, read-only /var) begin_op 'succeeds' but end_op finds nothing and returns without printing anything or running _post_op_verify.

**Failure scenario**

Root filesystem hits 100% (a realistic companion to a failing pool): a replace/offline still executes against ZFS, but the operator gets no '✓ replace complete', no rollback hint, and the post-op verification that would have caught a failed replace is silently skipped — the audit trail shows nothing at all for a real mutation.

**Suggested fix**

Keep the pending entry in a module-level dict {op_id: entry} at begin_op time; end_op should fall back to that in-memory entry to print the result and run _post_op_verify even when the JSONL round-trip fails, and print one warning that audit logging is unavailable.

*Verifier note:* Fix is stdlib-only and sound: keep pending entries in a module-level dict at begin_op, have end_op fall back to it when _load_entry returns None, and print a single visible warning ('audit log unwritable: <path>') the first time _append_jsonl/_rewrite_entry hits OSError instead of passing silently.

**How to verify the fix**

tests/test_safety.py::test_end_op_prints_result_when_log_unwritable — point LOG_FILE at an unwritable path, run begin_op/end_op, assert the op-result line and post-op verify still execute (capsys).

<details><summary>Verification trace</summary>

Code behaves exactly as claimed: _append_jsonl swallows OSError (safety.py:142-143) so begin_op 'succeeds' even when /var/log/b2ctl is unwritable; _load_entry swallows OSError too (:156-157) and returns None; end_op then returns at :83-84 before _print_op_result, rollback hint, and _post_op_verify. The ZFS mutation still executes (run_check is independent of the log), so under disk-full/read-only /var the operator gets zero completion output and loses the post-op verification safety net, with no audit record. No guard elsewhere. Downgraded reasoning vs a P2: b2ctl always runs as root, so the trigger requires an abnormal system state (full or read-only root fs), and the destructive op itself and its [y/N] confirmation are unaffected — this is a reporting/verification robustness gap, not a wrong action.

</details>

## F-093 — end_op rewrites the entire unbounded ops.jsonl on every operation completion

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/safety.py:164`
- **Category**: scalability
- **Verdict**: CONFIRMED
- **Also independently reported as**: ops.jsonl read/truncate/rewrite has no locking — a concurrent b2ctl process's audit entry appended mid-rewrite is permanently lost

**Evidence**

safety.py:164-167 `with open(LOG_FILE) as f: lines = f.readlines()` then rewrites the whole file — _load_entry (line 148) also linear-scans it first, so each end_op is two full-file reads plus a full rewrite; entries embed full stdout/stderr and nothing ever rotates or truncates the log.

**Failure scenario**

After months of watch usage on both nodes ops.jsonl grows to many MB; every replace/offline/destroy completion re-reads and rewrites the whole file (seconds of I/O inside the interactive flow), and a crash or power loss during the non-atomic rewrite window truncates the entire audit trail — the one record needed after a bad disk operation.

**Suggested fix**

Stop rewriting history: append a second 'end' record ({op_id, status, ended_at, ...}) and have load_log/find_entry merge begin+end pairs (last record wins); if in-place update must stay, write to LOG_FILE+'.tmp' and os.replace() for atomicity, and cap growth by rotating at a size threshold with os.replace to ops.jsonl.1.

*Verifier note:* Prefer the append-only design (end record per op_id, merge in load_log/find_entry) since it also fixes finding 153. If keeping the rewrite, write to LOG_FILE + '.tmp' and os.replace() (atomic on the same filesystem, stdlib) — that alone removes the truncation-loss window; rotation is optional given the low op rate. | Merged duplicate's note: The append-only fix is the right one (also resolves 136): end_op appends a second {op_id, event:'end', ...} record and _load_entry/load_log/find_entry merge by op_id with last-record-wins; POSIX O_APPEND writes of one small line are effectively atomic, no locking needed. If the rewrite must stay, fcntl.flock on a sidecar .lock file around _append_jsonl/_load_entry/_rewrite_entry is stdlib-compliant; combine with the os.replace() atomic rewrite from 136.

**How to verify the fix**

tests/test_safety.py::test_end_op_appends_without_rewriting — seed a large ops.jsonl in tmp_path, run begin_op/end_op, assert prior bytes untouched (file prefix identical) and find_entry returns the merged final status.

<details><summary>Verification trace</summary>

Verified: _load_entry linear-scans the whole file (:146-158), then _rewrite_entry reads all lines and rewrites the entire file via a truncating open(LOG_FILE, 'w') (:161-177); entries embed full stdout/stderr (:87-88) and nothing rotates or truncates. However the failure scenario is overstated for this deployment: disk lifecycle ops (replace/offline/destroy/create) happen at most a few times a month on two nodes, and each entry is a few KB, so ops.jsonl reaching 'many MB / seconds of I/O' would take years. The materially real part is the non-atomic rewrite: a crash or power loss between the truncating open('w') and the final write loses the entire audit history, which is exactly the record wanted after a bad op. Scalability/robustness debt, correctly P3.

</details>

## F-094 — _post_op_verify and the rollback-hint builders are never executed by any test — every safety test patches them out

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/safety.py:199`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

safety.py:199-221 _post_op_verify re-reads zpool status and prints 'Run: b2ctl rollback <op_id>' on mismatch; :18-30 _ROLLBACK['replace'] indexes e['cmds'][0][5]/[4] positionally into the recorded argv. tests/test_safety.py patches _post_op_verify in every end_op test (:41,54,109) and no test asserts a generated hint string; test_cli only tests consuming a hand-written hint.

**Failure scenario**

The replace hint depends on the cmd shape ['zpool','replace','-f',pool,old,new]; if a caller ever logs the -f-less form (swap_to_spare's shape, 5 tokens), the lambda silently emits the '<new-disk>' placeholder (or, with a different 6-token shape, swaps old/new and the printed rollback command resilvers the WRONG direction). op_checks regressions in _post_op_verify (e.g. 'offline' check inverted) would never warn operators again — all invisible to the suite.

**Suggested fix**

Add tests asserting end-to-end hint content for offline/add_spare/replace (both 6-token and short cmd shapes) and _post_op_verify behavior for pass/fail zpool outputs; prefer deriving the replace hint from named entry fields (dev_path + explicit new_dev) rather than argv indices.

*Verifier note:* Fix is right: add tests asserting end_op-produced rollback_hint strings for offline/add_spare/replace (6-token -f shape, 5-token shape, and a malformed shape), and direct _post_op_verify tests with mocked run_check returning matching/non-matching zpool status output, asserting the 'Post-op check FAILED' + 'b2ctl rollback' lines. Deriving the replace hint from named fields overlaps finding 149's fix.

**How to verify the fix**

tests/test_safety.py::TestRollbackHints::test_replace_hint_from_six_token_cmd, ::test_replace_short_cmd_falls_back_to_placeholder, ::TestPostOpVerify::test_offline_mismatch_prints_rollback_advice, ::test_replace_check_passes_on_resilver_output.

<details><summary>Verification trace</summary>

Verified against tests/test_safety.py and test_cli.py. One correction to the claim: the _ROLLBACK hint lambdas ARE executed by tests (end_op at test_safety.py:43/56/111 calls _build_rollback_hint unpatched), but no test ever asserts a generated hint's content, so the cmds[0][5]/[4] indexing and the 5-token placeholder fallback are effectively unverified. _post_op_verify is genuinely never executed: patched out in all three end_op tests (:41, :54, :109) and has no direct test, so an inverted op_check would ship silently. test_cli.py only consumes hand-written hints. Note the '<new-disk>' placeholder sub-scenario is already contained at execution time by cli.py's placeholder guard (refuses to run tokens matching <...>), so the residual executable risk is only a wrong-shaped 6-token cmd. Test gap is real.

</details>

## F-095 — SAS error-counter log's 'total uncorrected errors' column is never parsed, so d.uncorr stays 0 for SAS drives

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/smart.py:122`
- **Category**: improvement
- **Verdict**: CONFIRMED

**Evidence**

mw = re.search(r"^write:\s+(?:\d+\s+){5}(\d+(?:\.\d+)?)", out, re.M)  — captures only column 6 (gigabytes processed) of the read/write/verify counter rows; column 7 (total uncorrected errors), the standard SAS media-failure signal, is discarded and no other SAS field feeds d.uncorr.

**Failure scenario**

A SAS drive on the R620 logs 'read: ... 6585.833  14' (14 uncorrected errors) but zero grown defects: d.uncorr stays 0, assess() never bumps CRITICAL 'uncorrectable errors', and the disk keeps LEVEL NORMAL while actively returning unrecoverable media errors to ZFS.

**Suggested fix**

Add a pass over r"^(?:read|write|verify):\s+(?:\S+\s+){6}(\d+)" (re.M), taking max() into d.uncorr, alongside the existing GB-processed capture.

*Verifier note:* Proposed regex r"^(?:read|write|verify):\s+(?:\S+\s+){6}(\d+)" with re.M and max() into d.uncorr is correct — \S+ (not \d+) is required for column 6 since GB-processed contains a decimal point (e.g. '6585.833').

**How to verify the fix**

tests/test_smart.py — extend _SAS_OUTPUT (tests/helpers.py) with a read: row carrying a nonzero final column; assert smart._parse_sas sets d.uncorr and assess() goes CRITICAL.

<details><summary>Verification trace</summary>

Traced _parse_sas (smart.py:105-127): the only error-counter regex captures group after (?:\d+\s+){5} — column 6, gigabytes processed — for the write row only, feeding lba_written. Column 7 (total uncorrected errors) of read/write/verify rows is never read, and no other SAS field assigns d.uncorr, so assess()'s uncorr>0 → CRITICAL never fires for SAS drives. Real monitoring gap, but graded P3: grown-defect parsing (line 125) covers the strongly-correlated media-failure signal, ZFS surfaces read/cksum errors at the vdev level, and the current fleet parses via the ATA path.

</details>

## F-096 — smart._parse_nvme and the NVMe dispatch branch are completely untested — no NVMe smartctl fixture exists anywhere in tests/

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/smart.py:130`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

smart.py:130-150 parses Percentage Used, Power On Hours ('1,234' comma form), Data Units Written (*1000 LBA conversion at :147) and Media Errors; the dispatch at :66 requires both "NVMe" and "SMART/Health Information" in the output. tests/test_smart.py and helpers.py contain only _ATA_OUTPUT and _SAS_OUTPUT; grep for 'nvme' in test_smart.py: nothing.

**Failure scenario**

The fleet has 2 NVMe drives per box (sim: 6 SATA/SAS + 2 NVMe). If real `smartctl -a /dev/nvme0n1` output doesn't contain the literal 'NVMe' outside the health section header, dispatch falls into _parse_sas and NVMe wear/POH/written all read as None -> endurance column blank and worn NVMe never reaches WARNING/CRITICAL; a units regression in the *1000 conversion would misreport TBW by 512x unnoticed.

**Suggested fix**

Add a realistic full `smartctl -a` NVMe dump (Samsung 990 EVO style) to tests/helpers.py as _NVME_OUTPUT and test both _parse_nvme field extraction and smart.read() dispatching to it (not _parse_sas).

*Verifier note:* Fix is right: add a realistic full smartctl NVMe dump (Samsung 990 EVO style, with comma-formatted 'Power On Hours: 1,234' and 'Data Units Written') as _NVME_OUTPUT in helpers.py; assert _parse_nvme field values (wear_val, poh, lba_written == units*1000, uncorr) and that read() dispatches to _parse_nvme, not _parse_sas.

**How to verify the fix**

tests/test_smart.py::TestNvmeParsing::test_parse_nvme_extracts_wear_poh_written_uncorr, ::test_read_dispatches_nvme_not_sas, ::test_data_units_written_1000x_conversion.

<details><summary>Verification trace</summary>

Verified: tests/test_smart.py and tests/helpers.py contain zero NVMe content (grep empty); only _ATA_OUTPUT/_SAS_OUTPUT exist. Nuance the finding missed: the sim harness's fake smartctl (sim/bin/smartctl:36-52) does emit NVMe-format output and test_sim_smoke drives the real pipeline over 2 NVMe disks — but that fixture is tautological (its comment says it was written to match the dispatch keywords 'NVMe' + 'SMART/Health Information') and the smoke test asserts only 'nvme0n1'/'PCIe2:0' presence, no wear/POH/written values, so a *1000 units regression or dispatch change would pass. The scenario's specific fear is overstated: real `smartctl -a` NVMe output always contains 'SMART/Health Information (NVMe Log 0x02)', satisfying both dispatch keywords. The unit-test gap itself stands.

</details>

## F-097 — Bidirectional substring TBW lookup lets a truncated model match the wrong capacity's rating, with dict insertion order deciding ties

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/spec.py:47`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

if k and (k in m or m in k):  — `m in k` means a capacity-less model string matches every per-capacity key; the loop returns the first table entry, i.e. whichever was inserted first, not the most specific match.

**Failure scenario**

A disk whose model arrives truncated (SCSI INQUIRY is 16 chars — e.g. 'Samsung SSD 870' when the ATA identify path fails and lsblk's string is all we have) matches 'samsung ssd 870 evo 1tb'=600 first. If the physical drive is a 4TB 870 EVO (rated 2400 TBW) with 700 TB written, end_left computes 0% → false CRITICAL 'endurance left 0.0%' and watch steers the operator toward replacing a healthy disk.

**Suggested fix**

Prefer exact-normalized match, then the longest key satisfying `k in m`; only fall back to `m in k` when exactly one key matches, returning None (unknown) on ambiguity instead of the first hit.

*Verifier note:* Suggested fix is sound and stdlib-only; sharpen it: (1) exact match on _norm(model); (2) else longest key with k in m (spec key contained in full model); (3) else collect keys with m in k (truncated model) and return the value only if all candidates share one value (or there is exactly one candidate), otherwise return None — tbw_rating=None already renders as unknown endurance instead of a false level, and assess() skips the end_left check when it is None.

**How to verify the fix**

tests/test_spec.py — add test_truncated_model_ambiguous_returns_none: table {'samsung ssd 870 evo 1tb': 600, 'samsung ssd 870 evo 4tb': 2400}, lookup('Samsung SSD 870 EVO') must not silently return 600.

<details><summary>Verification trace</summary>

Traced end-to-end. spec.py:47 does bidirectional substring match and returns the first dict hit; load() inserts _DEFAULT_TBW first and JSON overwrites preserve position, so 'samsung ssd 870 evo 1tb'=600 iterates before 'samsung ssd 870 evo 2tb'=1200 from ssd_spec.json. The truncated-model path is reachable: hba.py takes d.model from lsblk (16-char SCSI INQUIRY product behind the SAS2308 SATL, e.g. 'Samsung SSD 870'); smart.py's _parse_ata normally overwrites it with the full ATA model, but on SCSI-output fallback _parse_sas only sets model 'if not d.model', so the truncated string reaches _endurance() -> lookup(). 'samsung ssd 870' then matches the 1TB entry via 'm in k' and returns 600 for any-capacity 870 EVO; with written_tb > 600, end_left clamps to 0.0 and common.py:178 bumps CRITICAL 'endurance left 0.0%'. No guard elsewhere; the docstring's 'substring-based' note does not sanction wrong-capacity first-hit-wins. One scenario inaccuracy: ssd_spec.json has no 870 EVO 4TB (2400 is the 990 EVO Plus 4TB); the realistic victim is the 870 EVO 2TB (1200 TBW) which IS in the shipped table — same defect, same false CRITICAL. Latent on the current fleet (all 1TB models resolve correctly by coincidence), and the effect is a wrong displayed LEVEL with all actions still confirm-gated, so P3 stands.

</details>

## F-098 — Global dry-run state lives in the interactive watch module and is written by cli and read by raid_actions/burnin — layering inversion

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:28`
- **Category**: structure
- **Verdict**: CONFIRMED

**Evidence**

`_DRY_RUN: bool = False` in watch.py; cli.main() sets `_watch._DRY_RUN = True` (cli.py:610), raid_actions._dry() does `from . import watch; return watch._DRY_RUN` (raid_actions.py:38-40), and cli handlers pass `dry_run=watch._DRY_RUN` (cli.py:126,133,145,152,161). Mid-layer action modules import the top-level UI module just to read a flag.

**Failure scenario**

A new entrypoint (the deferred `b2ctl top`, a cron-driven check, or a future zfs_actions module) calls an action with the default dry_run=False and silently ignores --dry-run because the author does not know the mode hides in watch; importing raid_actions alone drags in the whole watch/select machinery.

**Suggested fix**

Move the flag to the bottom layer (e.g. common.DRY_RUN with tiny get/set helpers, or a b2ctl/runtime.py); watch's [t]oggle, cli --dry-run, raid_actions and burnin all reference that single owner.

*Verifier note:* Fix is sound and stdlib-only: put the flag in common.py (bottom layer, already imported everywhere) with get/set helpers; keep watch._toggle_dry_run as a thin wrapper so the [t] hotkey UX is unchanged.

**How to verify the fix**

tests/test_common.py::test_dry_run_single_source — set the flag via the new setter and assert raid_actions._dry() and a cli handler both observe it without importing watch.

<details><summary>Verification trace</summary>

All citations verified: watch.py:28 `_DRY_RUN: bool = False`; cli.main sets `_watch._DRY_RUN = True` (cli.py:608-610); raid_actions._dry() does `from . import watch; return watch._DRY_RUN` (raid_actions.py:37-40); cli handlers pass `dry_run=watch._DRY_RUN` at cli.py:126, 133, 145, 152, 161. So the mode flag for low-level mutating actions is owned by the top-level interactive UI module, and mid-layer modules import watch (and its select/hotplug machinery) just to read it. Nothing in CLAUDE.md marks this as intentional; common.py's docstring ('No other b2ctl module depends on anything above this one') shows the intended layering that this inverts. The failure scenario (new entrypoint silently ignoring --dry-run) is speculative but the structural defect is concrete.

</details>

## F-099 — watch's hotplug baseline reaches into hba private internals (_lsblk_pairs/_EXCLUDE), bypassing the Backend abstraction

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:47`
- **Category**: structure
- **Verdict**: CONFIRMED

**Evidence**

`for row in hba._lsblk_pairs("NAME,TYPE"):` and `name.startswith(hba._EXCLUDE)` in _block_devs (and hba._lsblk_pairs again at line 219 in _wait_for_block_device), while every other enumeration goes through backend.get_backend(). core.assemble_storage similarly imports hba directly for vd_usage (core.py:88) — a RAID-VD concern living in the IT-mode module.

**Failure scenario**

Renaming/privatizing hba internals during the backend split breaks watch hotplug only at runtime (no import error until the loop ticks); a third backend (e.g. an NVMe-only JBOF where hotplug should watch /sys/class/nvme) cannot hook the hotplug baseline because watch is hard-wired to hba's lsblk helper.

**Suggested fix**

Promote a public block-device listing to the shared layer (e.g. Backend.list_block_names() with the lsblk implementation in common or a small blockdev.py; move vd_usage there too) and have watch/_wait_for_block_device/core call it.

*Verifier note:* Fix is fine and stdlib-only. Simplest increment: move _lsblk_pairs/_EXCLUDE (and vd_usage) into common.py or a small blockdev.py imported by hba, hba_raid, watch, and core; a Backend.list_block_names() hook can come later if a non-lsblk backend ever materializes.

**How to verify the fix**

tests/test_watch.py::test_block_devs_uses_backend — patch the new Backend.list_block_names and assert _block_devs consumes it; grep-style assertion that watch no longer references hba._ names.

<details><summary>Verification trace</summary>

Verified: watch._block_devs uses hba._lsblk_pairs and hba._EXCLUDE (watch.py:47,49), _wait_for_block_device uses hba._lsblk_pairs again (watch.py:219), and core.assemble_storage does `from . import hba` for vd_usage (core.py:85-93) — a HW-VD usage concern in the IT-mode module, used even in RAID mode. backend.Backend exposes no block-device listing (backend.py:19-41), and hba_raid.py:124 already carries its own duplicate _lsblk_pairs, reinforcing that the helper belongs in a shared layer. Not a runtime bug today (lsblk is mode-agnostic, so RAID boxes work), and hotplug for a hypothetical NVMe-only backend is speculative — pure structure debt: P3.

</details>

## F-100 — _handle_new_disk uses a fixed time.sleep(2) instead of udev settle, so a slow udev queue makes the hotplug flow abort with a misleading 're-insert' message

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:117`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

watch.py:117-124: `time.sleep(2)  # let udev/SMART settle` then `d = core.scan_one(dev, tbw)` ... `if not d.by_id: print(... 'no stable by-id yet — skipping (re-insert if needed)'); return` — by-id symlinks are created asynchronously by udev; 2 s is a guess, and the module already shells to `udevadm settle` elsewhere (_wait_for_block_device).

**Failure scenario**

A drive is inserted while udev is busy (backplane reset after a pull, or several disks inserted together — exactly the replace-then-replenish moment of Task B): 2 s elapse before /dev/disk/by-id/ata-... exists, _by_id_index misses it, and the NEW DISK flow bails out telling the operator to physically re-insert a perfectly good disk instead of retrying; the disk then needs a manual [a]ssign.

**Suggested fix**

Replace the fixed sleep with `run(["udevadm", "settle", "--timeout=10"])` (common.run), and if d.by_id is still empty after the first scan_one, settle+rescan once more before giving up; keep the skip message as the true last resort.

*Verifier note:* Fix is right and stdlib-only. Use `hba.run(["udevadm", "settle", "--timeout=10"])` for consistency with line 218 (hba.run is the established wrapper here; common.run works too). Then if d.by_id is still empty after the first scan_one, settle once more and re-run scan_one before printing the skip message. Note scan_one runs a full core.scan(), so cap the retry at one to keep the hotplug handler snappy.

**How to verify the fix**

tests/test_watch.py::test_handle_new_disk_retries_when_by_id_missing — patch core.scan_one to return a Disk with by_id='' on the first call and by_id='/dev/disk/by-id/ata-X' on the second, patch _assign_free_disk; call _handle_new_disk and assert scan_one was called twice and _assign_free_disk once.

<details><summary>Verification trace</summary>

Traced: watch.py:117 is a fixed `time.sleep(2)` with comment 'let udev/SMART settle', then scan_one; if udev has not yet created the by-id symlink, lines 122-124 print 'no stable by-id yet — skipping (re-insert if needed)' and return — telling the operator to physically re-insert a good disk when a retry would do. The inconsistency claim is accurate: the same module already shells `udevadm settle` in _wait_for_block_device (line 218) via hba.run. Impact is bounded — the disk remains reachable via manual [a]ssign (_cmd_assign rescans), so 'warning'/P3 is the right level, not a broken action path.

</details>

## F-101 — _assign_free_disk mutating menu choices 2/3/4/5/6 (add-spare, replace-degraded, attach, single-disk add, wipe) are untested — only the choice-4 scan-reuse optimization is asserted

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:148`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

watch.py:149-211: choice 3 runs `zpool replace -f` on a degraded leaf plus _detach_if_lingers plus the rpool proxmox-boot-tool warning (:161-164); choice 5 runs `zpool add -f <pool> <dev>` (pool-redundancy-destroying); choice 6 wipes. tests/test_watch.py::TestWatchAssignChoice4 only asserts core.scan is/isn't called for choice "4"; no test selects 2/3/5/6 or asserts the rpool warning surfaces.

**Failure scenario**

The hotplug new-disk flow (_handle_new_disk -> _assign_free_disk) is the primary Task-B replenish path: a regression that drops the rpool boot-tool warning (CLAUDE.md section 9 requires surfacing it), swaps the old/new argument order in the choice-3 replace cmd, or skips _confirm before choice-6 wipe would pass the whole suite and only surface as a wrong zpool command on a live pool.

**Suggested fix**

Parametrized tests per menu choice with core/zfs/safety/locate patched: assert exact argv for choices 3 and 5, that rpool targets print the proxmox-boot-tool lines, and that declining _confirm/_confirm_op never reaches run_check/zfs.wipe.

*Verifier note:* Fix is right. Add parametrized tests per menu choice patching core/zfs/safety/locate/run_check: assert exact argv for choices 3 and 5, that a degraded rpool target prints the proxmox-boot-tool lines, and that declining _confirm/_confirm_op never reaches run_check/zfs.wipe.

**How to verify the fix**

tests/test_watch.py::TestAssignFreeDisk::test_choice3_replace_degraded_cmd_and_rpool_boot_warning, ::test_choice5_single_disk_add_requires_confirm, ::test_choice6_wipe_declined_never_wipes, ::test_choice2_add_spare_uses_by_id.

<details><summary>Verification trace</summary>

Traced: the only direct _assign_free_disk tests are TestWatchAssignChoice4 (tests/test_watch.py:389-410), which assert only whether core.scan is called for choice "4"; every other reference to _assign_free_disk in the suite is a patched-out mock (lines 64, 81, 557, 598, 618). No test drives choices 2 (add_spare), 3 (zpool replace -f + _detach_if_lingers + the rpool proxmox-boot-tool warning at watch.py:161-164), 5 (single-disk zpool add -f), or 6 (wipe). The failure scenarios (dropped rpool warning, swapped replace args, skipped wipe confirm) would indeed pass the suite. Per the rubric, test-coverage gaps are P3, not the P2 the finding claimed.

</details>

## F-102 — Locate and token-resolution paths run a full SMART scan whose data they never use

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:382`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

watch.py:382 `disks = core.scan(tbw)` in _cmd_locate — the command only needs bay/serial/dev matching to blink an LED, but pays the full scan (8 smartctl reads, 5 sas2ircu probes, zpool topology) before even printing the 'locate which' prompt. Same pattern in cli.py:68 (_locate) and cli.py:114 (_resolve_devs used by cache-add/cache-rm/log-add/log-rm).

**Failure scenario**

Operator types `l` in watch (or runs `b2ctl locate 1:4`) to find a bay to pull: 5-15 s of SMART reads happen first; none of wear/TBW/health output is used — the resolved fields (bay, serial, dev, by_id) all come from enumerate_disks+attach_bays alone.

**Suggested fix**

Add a core.scan_light() (enumerate_disks + attach_bays, no SMART/no zpool) and use it for _cmd_locate, cli._locate, cli._resolve_dev/_resolve_devs; keep full scan for health-driven flows.

*Verifier note:* A scan_light() must keep the ghost-disk pass (or at least the dev == '-' guard stays meaningful only if ghosts are still represented) and must still go through backend.get_backend() so RAID-mode disks get smart_dtype/pd_state for is_perc_pd. _resolve_devs also relies on by_id, which enumerate_disks+_by_id_index provide without SMART, so the light scan suffices there too.

**How to verify the fix**

tests/test_watch.py::test_cmd_locate_no_smartctl — recorder asserts zero smartctl invocations while _cmd_locate resolves a bay from the fixture disks.

<details><summary>Verification trace</summary>

Traced: _cmd_locate (watch.py:381-400) uses only bay/serial/dev (matching) plus smart_dtype/pd_state (locate.is_perc_pd) — all populated by backend enumerate_disks/attach_bays, none by smart.read or zfs.attach_membership. core.scan (core.py:16-74) unconditionally runs the ghost-rescue ThreadPool, SMART reads on every disk, and zpool topology+spares_replacing first. Same pattern verified at cli.py:68 (_locate) and cli.py:114 (_resolve_devs feeding cache-add/cache-rm/log-add/log-rm at 126/133/145/152). One overstatement: SMART reads run in a 4-worker ThreadPool, so wall-clock is closer to 2-5 s than 5-15 s — real but smaller than claimed; also the finding runs the scan before the 'locate which' prompt, as claimed.

</details>

## F-103 — The 'poolable free disk' invariant (not in_pool, dev != '-', no smart_dtype) is enforced by three duplicated inline filters instead of the Disk contract

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/watch.py:521`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`if not d.in_pool and d.dev != "-" and not d.smart_dtype]` in _cmd_create; the same triple appears in _cmd_assign (lines 276-277) and _avail_for_aux (lines 702-703), each with a long comment re-explaining why a HIDDEN PERC drive sharing the VD's /dev/sda must never reach ZFS wipe/add.

**Failure scenario**

A new flow (Task B's replenish menu, a future extend variant) copies the filter and forgets `not d.smart_dtype`: a hidden PERC member whose dev is the shared /dev/sda is offered as 'free', and the wipe path runs `sgdisk --zap-all /dev/sda` — destroying the hardware VD that hosts the OS (the precise hazard the comments warn about, currently enforced only by comment discipline).

**Suggested fix**

Add a `Disk.is_poolable` property in common.py (`not self.in_pool and self.dev != "-" and not self.smart_dtype and self.health != "GHOST"`) and use it in all three sites; keep one authoritative comment on the property.

*Verifier note:* The proposed Disk.is_poolable property is right; the `health != "GHOST"` clause is redundant belt-and-braces (ghosts have dev == '-') but harmless. Also apply it inside the _offline_and_replace filter at watch.py:484-486, which the finding missed.

**How to verify the fix**

tests/test_common.py::test_disk_is_poolable — cases: hidden PERC member (smart_dtype set, shared dev) False; GHOST (dev '-') False; JBOD'd raw disk True; pool member False.

<details><summary>Verification trace</summary>

All three sites verified verbatim: _cmd_assign watch.py:276-277 (with the 4-line HIDDEN-PERC comment), _cmd_create watch.py:520-521 (with a 2-line copy of the same comment), _avail_for_aux watch.py:702-703. The invariant (not in_pool, dev != '-', not smart_dtype) exists only as copy-pasted expressions guarded by comment discipline; the hazard is real — zfs.wipe runs `sgdisk --zap-all` on the given dev, and a hidden PERC member's dev is the shared VD block device. There is also a fourth partial copy in _offline_and_replace (watch.py:484-486). The failure scenario is prospective (a future copy forgetting one clause), which is exactly maintainability-debt territory: P3.

</details>

## F-104 — zfs read-side helpers untested: list_pools() tab parser, spares(), has_zfs_label(), and the attach/add_mirror/swap_to_spare command shapes

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/zfs.py:25`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

zfs.py:25 `run(["zpool", "list", "-H", "-o", ...])` split on tabs feeds every pool menu, prune_orphan_crons and _cmd_destroy — no test feeds it real output; spares() (:165-171), has_zfs_label() (:380-386, line-filter heuristic), attach() (:255), add_mirror() (:235) and swap_to_spare() (:315, deliberately WITHOUT -f unlike replace()) have no unit tests — watch tests mock the whole zfs module.

**Failure scenario**

A refactor adding -f to swap_to_spare (to 'match' replace()) would force-replace even when ZFS would refuse for safety, with no failing test; a zpool list column-order change or locale tab handling silently drops pools from list_pools -> prune_orphan_crons then deletes the cron of a live pool (it prunes anything not in list_pools, zfs.py:479-489); has_zfs_label header-filter drift makes the create-pool dirty-disk warning vanish.

**Suggested fix**

Add fixture-driven tests: a real `zpool list -H -o name,size,alloc,free,health,frag,cap` two-pool dump for list_pools; _RAIDZ_STATUS reuse for spares(); wipefs -n sample output for has_zfs_label; assert exact argv for attach/add_mirror/swap_to_spare including the absence of -f in swap_to_spare.

*Verifier note:* Fix is appropriate as-is. Worth adding to the list_pools test: an empty-output case asserting prune_orphan_crons removes nothing when list_pools returns [] — that pins the most dangerous downstream behavior (mass cron deletion on a zpool failure).

**How to verify the fix**

tests/test_zfs.py::TestListPools::test_parses_tab_output, ::TestSpares::test_avail_tokens_from_raidz_status, ::TestHasZfsLabel::test_label_lines_detected, ::TestZfsActions::test_swap_to_spare_has_no_force_flag.

<details><summary>Verification trace</summary>

Coverage gap verified by grep of codes/tests/: test_zfs.py never calls list_pools, spares, has_zfs_label, attach, add_mirror, or swap_to_spare (its action tests cover add_spare/replace/create_pool/add_cache/add_log/remove_vdev/demote_to_spare/destroy_pool only); every watch test mocks the zfs module (test_watch.py:120,157,212,292 etc.). The claimed blast radii check out: prune_orphan_crons (zfs.py:479-489) deletes every /etc/cron.d/b2ctl-* not in list_pools() and runs unconditionally at watch startup (watch.py:792) — an empty/misparsed list_pools() output silently removes live pools' trim+scrub crons; swap_to_spare (line 317) is indeed the one replace-family wrapper without -f while replace() (line 260) has it, and nothing pins that difference; has_zfs_label's header filter (line 385) has no fixture. The proposed fixtures are all stdlib/mock-based, consistent with the repo's existing test style.

</details>

## F-105 — spares() returns duplicated tokens because topology entries are indexed under both token and realpath

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/zfs.py:170`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`return [e["token"] for e in topo.values() if "spare" in e["vdev"] and e["state"] == "AVAIL"]` — _parse stores the same entry dict under the by-id token AND its realpath (lines 83-87), so on real hardware (where by-id resolves to /dev/sdX) each AVAIL spare appears twice. degraded_leaves already dedups with a seen-set; spares does not.

**Failure scenario**

After a replace on tank, watch prints 'spare restored to AVAIL: /dev/disk/by-id/ata-X, /dev/disk/by-id/ata-X' (watch.py:181-182) — the operator reads two spares where there is one. Any future caller that counts spares() would over-count availability.

**Suggested fix**

Dedupe as degraded_leaves does: iterate {id(e): e for e in topo.values()}.values() or build a set of tokens before returning (preserve order with dict.fromkeys).

*Verifier note:* Suggested fix is right; simplest order-preserving stdlib form: `return list(dict.fromkeys(e["token"] for e in topo.values() if "spare" in e["vdev"] and e["state"] == "AVAIL"))`.

**How to verify the fix**

tests/test_zfs.py::test_spares_unique_tokens — build a topo where the spare entry is registered under two keys (token + realpath alias) and assert zfs.spares returns the token exactly once.

<details><summary>Verification trace</summary>

Traced: _parse (zfs.py:81-87) stores the same entry dict under the -P token AND os.path.realpath(token); spares() iterates topo.values() with no dedup, so on real hardware (by-id link resolves to /dev/sdX, a distinct key) each AVAIL spare token is yielded twice. Sole current caller is watch.py:180-182, which joins the list into the 'spare restored to AVAIL' message — the operator sees the spare printed twice. Note the bug is invisible in tests/sim without real /dev/disk/by-id links (realpath of a nonexistent path returns the path itself, collapsing to one key), which is why no test catches it. degraded_leaves() dedups with a seen-set; spares() does not. Impact today is a cosmetic duplicate in one message plus a latent over-count for any future caller, so P3 rather than P1.

</details>

## F-106 — Dead code: add_mirror and resilver_status are defined but never called anywhere

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/zfs.py:235`
- **Category**: maintainability
- **Verdict**: CONFIRMED

**Evidence**

`def add_mirror(pool, dev_a, dev_b, ...)` (line 235) and `def resilver_status(pool)` (line 320) have zero call sites in b2ctl/, tests/, or sim/ (grep confirms); the live paths use zpool add via watch's inline run_check and poll_resilver_status respectively. resilver_status's regex also spans only one line so it would under-match real output if ever wired up.

**Failure scenario**

A future contributor 'fixes' or extends resilver_status/add_mirror believing they are live (the CLAUDE.md module map still lists add_mirror as a zfs action), wasting effort or shipping an untested mutating entry point (add_mirror runs `zpool add -f ... mirror`, a destructive command with no caller-side confirm contract).

**Suggested fix**

Delete both functions (or wire add_mirror into _assign_free_disk choice 4/5 where the inline zpool add currently lives) and update the CLAUDE.md section 5 module map accordingly.

*Verifier note:* Fix is fine: delete both functions and their absence costs nothing (no tests reference them); update CLAUDE.md section 5's zfs.py row to drop add_mirror (and add the actually-live add_cache/add_log/remove_vdev/create_pool/destroy_pool entries while there). If mirror-extension is wanted later, reintroduce it wired through the _assign_free_disk/confirm contract rather than resurrecting this orphan.

**How to verify the fix**

Run `cd codes && python3 -m pytest tests/ -q` after removal (no test references either symbol); add a grep-based check is unnecessary — compile via python3 -m py_compile b2ctl/*.py.

<details><summary>Verification trace</summary>

Grep across b2ctl/, tests/, and sim/ confirms add_mirror (zfs.py:235) and resilver_status (zfs.py:320) have zero call sites — only their definitions; the live paths are watch's inline run_check for zpool add and poll_resilver_status for progress. CLAUDE.md section 5 module map still lists add_mirror among zfs actions, so the drift claim is accurate. Also verified the secondary point: resilver_status's single-line regex (re.search without DOTALL, zfs.py:322) would match only the 'scan:' header line and miss the %-done detail line if ever wired up. Pure maintainability debt with a mild footgun (add_mirror is an unconfirmed-by-contract destructive zpool add -f) => P3.

</details>

## F-107 — _cmd_offload builds full zpool topology 3-4 times back-to-back within one guarded flow

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/zfs.py:268`
- **Category**: performance
- **Verdict**: CONFIRMED

**Evidence**

zfs.py:268 `topo = topology()` in can_detach; can_offline repeats it at line 298; watch._cmd_offload calls scan (topology at core.py:55), then can_detach (watch.py:357), then can_offline (watch.py:374), and _detach_if_lingers re-runs it again (watch.py:421) — each topology() = `zpool list` + one `zpool status -P -v` per pool.

**Failure scenario**

Offloading a tank member on the two-pool box spawns up to 12 zpool processes (4 topologies x 3 calls each) before/around the confirmation prompts, adding several seconds of latency to an interactive safety flow; the data cannot change between the two guard checks issued milliseconds apart.

**Suggested fix**

Give can_detach/can_offline an optional `topo: dict | None = None` parameter; _cmd_offload computes one topology() snapshot per user interaction and passes it to both guards (still take a fresh snapshot immediately before the mutating zpool call, preserving the section-9 safety semantics).

*Verifier note:* Fix is sound and stdlib-only. Sharpen: thread the snapshot into _offline_and_replace's internal re-check (watch.py:464) too — it is the 4th build; and because _confirm prompts block on human input for arbitrary time, take the fresh snapshot AFTER each confirm returns (immediately before the mutating zpool call), not merely 'before the mutating call' in general.

**How to verify the fix**

tests/test_zfs.py::test_can_detach_and_can_offline_accept_shared_topo — build topo once from the helpers rpool/tank fixture, call both with topo= and assert no zpool subprocess is spawned.

<details><summary>Verification trace</summary>

Traced the spare-less offload path: core.scan builds topology (core.py:55), zfs.can_detach rebuilds it (zfs.py:268, called from watch.py:357), zfs.can_offline rebuilds it (zfs.py:298, watch.py:374), and _offline_and_replace re-checks can_offline a 4th time (watch.py:464). On the 2-pool box each build = 1 `zpool list` + 2 `zpool status`, so 4 builds = 12 zpool processes in one guarded flow — the count in the finding is exact. One evidence flaw: _detach_if_lingers (watch.py:421) runs only after the resilver in the spare path, not back-to-back; the 4-build count holds anyway via the duplicated can_offline check at watch.py:464. Also, prompts DO sit between some guard calls (the _confirm at watch.py:358 precedes can_offline), so 'data cannot change between the two guard checks' is overstated for that pair — which actually argues for the fix's own caveat of re-snapshotting before the mutating call. Real latency is likely sub-second-to-~1s (zpool status is fast), so this is redundancy/structure debt, P3 as filed.

</details>

## F-108 — wipe() ignores labelclear/wipefs failures and reports success based on sgdisk alone

- **Priority**: P3 (Low)
- **Location**: `codes/b2ctl/zfs.py:373`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`run_check(["zpool", "labelclear", "-f", dev], ...)` and the wipefs call on line 374 have their (ok, out) results discarded; only `return run_check(["sgdisk", "--zap-all", dev], ...)` (line 375) decides the reported outcome.

**Failure scenario**

During [n]ew-pool, a 'dirty' disk is wiped while udev/blkid still holds it: `wipefs -a` fails EBUSY and `zpool labelclear` fails, but sgdisk succeeds -> watch prints 'wiped blank' / create proceeds. The disk still carries ZFS labels at the 256K offsets (sgdisk only zaps GPT structures), so it keeps advertising membership in a pool named tank/rpool — on these twin-named nodes a later `zpool import` can pick up the stale label and confuse recovery.

**Suggested fix**

Collect each step's (ok, out); stop at the first failure and return (False, '<cmd>: <out>'); return (True, combined) only when all three succeed. Keep the labelclear -> wipefs -> sgdisk order.

*Verifier note:* The suggested fail-on-first-failure fix is WRONG as written: `zpool labelclear -f` legitimately fails on any disk without a ZFS label (e.g. an ext4 or blank disk), so hard-failing on it would break wipe for the common non-ZFS dirty disk. Correct stdlib fix: treat labelclear as best-effort (or run it only when has_zfs_label(dev)), but capture and propagate wipefs and sgdisk failures ((False, f'wipefs: {out}')); optionally verify with `wipefs -n` afterwards and report any residual signatures instead of claiming clean.

**How to verify the fix**

tests/test_zfs.py::test_wipe_fails_when_wipefs_fails — patch run_check to fail only for the wipefs argv and assert zfs.wipe returns ok=False with wipefs output, and that sgdisk is not invoked after the failure.

<details><summary>Verification trace</summary>

Traced zfs.py:371-375: labelclear and wipefs (ok, out) results are discarded; only sgdisk's result is returned, and sgdisk --zap-all clears GPT structures only — it does not touch ZFS labels at the 256K/512K offsets, so a wipefs EBUSY (e.g. udev/mdadm holding a foreign disk) yields a false 'clean' success report while stale pool labels survive. Downgraded to P3: every downstream consumer of a wiped disk in b2ctl (zpool create -f, add -f, replace -f, attach -f) rewrites the labels anyway, so the stale-label-import hazard only materializes in the wipe-then-don't-use case (e.g. _wipe_ghost step 3 followed by declining _assign_free_disk); the practical defect is a misleading success message plus a rare recovery-confusion window on the twin-named rpool/tank nodes.

</details>

## F-109 — Unknown flags are silently ignored and flag combinations are order-dependent (--with-tools --perc installs perccli only; reversed order installs both)

- **Priority**: P3 (Low)
- **Location**: `codes/install.sh:29`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

`*) ;;` swallows anything unrecognised, and each case assignment overwrites TOOLSET wholesale: `--with-tools --perc` ends with TOOLSET="perccli", while `--perc --with-tools` ends with TOOLSET="sas2ircu perccli" and SET_MODE=raid — two different installs from the same intent.

**Failure scenario**

Operator typos `./install.sh --percc` on the R640: the script performs a plain package-only install, prints the normal success lines, and the operator believes the RAID profile (perccli + mode=raid) is applied; b2ctl later auto-detects, and if detection misfires there is no configured mode to fall back on.

**Suggested fix**

Replace `*) ;;` with `*) echo "unknown option: ${_arg}" >&2; echo "usage: ./install.sh [--with-tools|--perc|--flash]" >&2; exit 2 ;;` and make combinations additive (append to TOOLSET, dedupe) or explicitly reject combining profile flags.

*Verifier note:* Suggested fix is correct and shell-only. Given the documented contract treats the three flags as exclusive profiles, rejecting combinations (`exit 2` on a second profile flag) is truer to intent than making them additive; keep `b2ctl install` argparse behavior (which already errors on unknown flags) as the parity reference.

**How to verify the fix**

tests/test_install_sh.py: assert `bash install.sh --percc` exits 2 with a usage line, and that --with-tools --perc vs --perc --with-tools select the same toolset.

<details><summary>Verification trace</summary>

Traced at lines 24-31: `*) ;;` silently discards unknown args, and each case arm assigns TOOLSET/SET_MODE wholesale, so `--with-tools --perc` yields TOOLSET="perccli" while `--perc --with-tools` yields TOOLSET="sas2ircu perccli" with SET_MODE=raid in both — the order-dependence is exactly as claimed. CLAUDE.md/§3 documents the flags as mutually exclusive profiles and never sanctions silent ignore of typos, so this is not intentional design. The typo scenario (--percc → plain install that prints all the normal success lines including '[+] done') is fully reachable; auto-detect fallback exists (backend auto-detection is the default), which keeps this at operator-confusion level rather than broken-action level — P3 as reported.

</details>

## F-110 — Download validation is a 1 KB size check only, and --flash/--perc still set controller.mode even when the tool install visibly failed

- **Priority**: P3 (Low)
- **Location**: `codes/install.sh:60`
- **Category**: warning
- **Verdict**: CONFIRMED

**Evidence**

`if [ "${_size}" -lt 1024 ]` is the only integrity check — Google Drive quota/error HTML pages are several KB and pass it; downstream `unzip -q ... || true` (line 91) masks the corrupt archive, install_tools ends with `echo "Done. Run: b2ctl check"` (exit 0), and the SET_MODE block at line 191 runs unconditionally afterwards.

**Failure scenario**

`./install.sh --flash` when the Drive file is quota-blocked: a multi-KB HTML page passes the size check, unzip fails silently, '[✗] sas2ircu: binary not found in archive' scrolls by, the script exits 0 and still writes controller.mode=it — the box is now force-pinned to IT mode with no sas2ircu, so every `b2ctl status` shows bay '-' and locate-by-bay is dead, with a 'successful' install exit code for any wrapping automation.

**Suggested fix**

Validate magic bytes (`head -c4` == PK\x03\x04 for the zip, \x1f\x8b for the tar.gz) in _gdrive_get; make install_tools return non-zero when a requested tool did not land on disk, and gate SET_MODE (or downgrade to a loud warning) on that result.

*Verifier note:* Magic-byte validation in _gdrive_get is right (pure shell, no new deps). Prefer making install_tools track and return per-tool failure and exiting non-zero at script end over gating SET_MODE — installer.py deliberately sets the mode after failed installs too (the flag declares the hardware profile), so a loud warning plus non-zero exit preserves parity. Any fix must also address the line-95 abort noted above or the --flash corrupt-zip case silently dies before reaching this logic.

**How to verify the fix**

tests/test_install_sh.py: stub curl emits an HTML page >1KB; assert install.sh --flash exits non-zero (or prints an explicit 'mode NOT set' warning) and does not write controller.mode=it.

<details><summary>Verification trace</summary>

Traced: line 60's 1KB size test is the only integrity check; SET_MODE block (191) runs unconditionally — even when download_tools fails outright (the else at 184-186 prints an error, then the script continues and still writes the mode, exit 0). The exact --flash scenario as written is slightly wrong, though: with a quota-HTML 'zip', unzip exits 9 WITHOUT creating ${_tmp}/sas2ircu (tested), so `_sas=$(find <missing dir>|head -1)` at line 95 aborts under set -e/pipefail (tested) — script dies silently with exit 1 and mode NOT written, rather than 'exits 0 + mode=it'. The described exit-0-with-mode-written path IS real for --perc with a corrupt tar (mkdir -p pre-creates perc_src, find succeeds empty, '[✗] RPM not found', exit 0, mode=raid written) and for any download failure. Note: mode-set-regardless-of-install mirrors installer.py's install_profile (lines 214-230, same 1:1-parity commit 89b075b), so gating the mode may fight intentional design; the weak validation and success exit code are the genuine defects.

</details>

## F-111 — install_tools registers the i386 dpkg architecture and installs libc6-i386/alien/unzip regardless of which tool subset was requested

- **Priority**: P3 (Low)
- **Location**: `codes/install.sh:83`
- **Category**: improvement
- **Verdict**: CONFIRMED

**Evidence**

`dpkg --add-architecture i386` + `apt-get install -y alien unzip libc6-i386 ...` (line 85) run unconditionally before the TOOLSET branches, though i386/libc6-i386/unzip serve only the 32-bit sas2ircu and alien serves only the perccli rpm.

**Failure scenario**

`./install.sh --perc` on the RAID-mode R640 permanently registers the i386 architecture on a production Proxmox host (every future `apt update` fetches i386 indexes) and installs libc6-i386/unzip that nothing uses — an unnecessary persistent system mutation that contradicts the v0.8.3 parity note 'apt prereqs install only when a tool is actually added' at per-tool granularity.

**Suggested fix**

Move the prereqs into the per-tool branches: i386 arch + libc6-i386 + unzip only inside the sas2ircu block; alien only inside the perccli block; shared packages (smartmontools, zfsutils-linux, gdisk) can stay common.

*Verifier note:* If refined, change BOTH sides in tandem — install.sh's prereq block and installer.py's ensure_prereqs() (accept a tools list) — or the documented 1:1 parity between ./install.sh and `b2ctl install` breaks. Shared packages (smartmontools, zfsutils-linux, gdisk, util-linux, coreutils, udev) stay common as suggested.

**How to verify the fix**

tests/test_install_sh.py: with stubbed dpkg/apt-get recording their argv, run --perc and assert dpkg --add-architecture and libc6-i386 are never invoked.

<details><summary>Verification trace</summary>

Traced: dpkg --add-architecture i386 (83) and apt-get install of alien+unzip+libc6-i386 (85) run unconditionally at the top of install_tools, before the per-tool TOOLSET branches — so `--perc` does register i386 and install libc6-i386/unzip that only sas2ircu needs. One correction to the framing: this does NOT contradict the v0.8.3 parity note — that note promises prereqs install 'only when a tool is actually added' (coarse gating, which line 180's WITH_TOOLS check honors), and the Python twin installer.py ensure_prereqs() (131-160) is equally coarse (alien+libc6-i386 for any tool), so coarse granularity is the current intentional shared contract, not a regression. The per-tool refinement is still a valid improvement: permanent i386 index fetching on a production RAID-mode Proxmox host is a real, unnecessary persistent mutation.

</details>

## F-112 — `cp -r` into an existing /opt/b2ctl never removes stale modules and ships the dev machine's __pycache__, so upgrades are not idempotent

- **Priority**: P3 (Low)
- **Location**: `codes/install.sh:145`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`cp -r "${SRC_DIR}/b2ctl" "${PREFIX}/"` merges into an existing /opt/b2ctl/b2ctl: files deleted or renamed upstream (e.g. the removed storcli backend module) remain importable on the server forever, and the repo's b2ctl/__pycache__ (cpython-314 from the dev laptop) is copied along.

**Failure scenario**

A box installed at an older version is upgraded by re-running ./install.sh: an orphaned module that current code no longer ships stays in /opt/b2ctl/b2ctl; any name-based import, `python3 -m` discovery, or future module rename silently resolves to the stale file, producing behavior that differs from a fresh install and is impossible to reproduce from the repo.

**Suggested fix**

Before copying: `rm -rf "${PREFIX}/b2ctl"` then `cp -r`; exclude caches, e.g. `find "${PREFIX}/b2ctl" -name __pycache__ -type d -exec rm -rf {} +` after the copy.

*Verifier note:* Fix is correct; order matters — `rm -rf "${PREFIX}/b2ctl"` before the cp is the robust part, and pruning __pycache__ after copy handles the cache. Simpler equivalent for the cache half: copy with `cp -r` then `find "${PREFIX}/b2ctl" -name '__pycache__' -type d -prune -exec rm -rf {} +`. Pure shell, no new dependencies.

**How to verify the fix**

tests/test_install_sh.py: install into a tmp PREFIX, plant PREFIX/b2ctl/stale_module.py and a __pycache__ dir, re-run install.sh, assert both are gone.

<details><summary>Verification trace</summary>

Traced: `cp -r "${SRC_DIR}/b2ctl" "${PREFIX}/"` with an existing /opt/b2ctl/b2ctl copies INTO the existing directory (POSIX cp -r merge semantics) and never deletes anything, so upstream-removed modules persist; the repo working tree really does contain b2ctl/__pycache__ with cpython-314 .pyc files (verified by ls), which cp -r ships. The upgrade path is real — docs direct re-running ./install.sh, and a module removal already happened in this repo's history (storcli dropped, commit 6a64e9e). Impact today is latent (nothing currently imports a removed module by discovery, and cpython-314 pycs are ignored by the box's older python3), hence P3 not P1, matching the reporter.

</details>

## F-113 — install.sh has zero automated checks — not even bash -n — leaving the section-6 "$bin" quoting gotcha and the launcher heredoc unguarded

- **Priority**: P3 (Low)
- **Location**: `codes/install.sh:165`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

install.sh:165-167 `for bin in smartctl zpool lsblk; do command -v "$bin" ...` is exactly the loop CLAUDE.md section 6 says regressed before ('must use "$bin" NOT "\$bin"'), and :150-156 is the launcher heredoc where `\$@` MUST stay escaped. tests/test_installer.py tests only the Python b2ctl.installer module; grep for install.sh across tests/: no hit.

**Failure scenario**

An edit re-escapes `"$bin"` (dep check silently reports every tool missing) or un-escapes the launcher's `\$@` (launcher bakes in empty args at install time, so `b2ctl locate 1:4 on` runs plain `b2ctl`): both ship to /usr/local/sbin on the next deploy with the suite fully green.

**Suggested fix**

Add tests/test_install_sh.py using subprocess (stdlib): run `bash -n install.sh` for syntax, and assert on the file text that the dep loop contains '"$bin"' (not '\$bin') and the heredoc contains '"\$@"'; optionally run the script with PREFIX/LAUNCHER redirected to tmp_path as non-root and assert the root guard fires.

*Verifier note:* Fix is stdlib-only and sound: tests/test_install_sh.py with subprocess running `bash -n install.sh` (skipUnless shutil.which('bash')) plus text assertions that the dep loop contains '"$bin"' and the heredoc contains '"\$@"'. Also assert PYTHONSAFEPATH=1 stays in the launcher (line 155) — it guards the repo-checkout shadowing fix from commit 4cbfa1b. The non-root guard test (expect exit 1, 'run as root' on stderr) is safe since the root check at 137 precedes all mutations.

**How to verify the fix**

tests/test_install_sh.py::test_bash_syntax_ok, ::test_dep_loop_uses_unescaped_bin_var, ::test_launcher_heredoc_keeps_escaped_argv.

<details><summary>Verification trace</summary>

Traced: no test file targets install.sh — tests/ has test_installer.py (Python module only) and the string 'install.sh' appears only in two docstrings (test_installer.py:93, test_cli.py:214), a minor correction to the finding's 'no hit' claim but immaterial since neither executes or lints the script. The dep-check loop at 165-167 is exactly the CLAUDE.md §6 regression ('must use "$bin" NOT "\$bin"') and the launcher heredoc at 150-156 carries the load-bearing `"\$@"` (line 155) — both currently correct, both unguarded. Failure scenarios are accurate: un-escaping \$@ bakes empty args into /usr/local/sbin/b2ctl so every subcommand runs bare `b2ctl`; the suite stays green either way.

</details>

## F-114 — Non-atomic save() plus load() silently falling back to default_state() lets a concurrent read clobber or shadow the whole sim state

- **Priority**: P3 (Low)
- **Location**: `codes/sim/_simstate.py:73`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

save() truncating-writes STATE_PATH in place (`with open(STATE_PATH, "w")`, line 77) and load() does `except (OSError, json.JSONDecodeError): return default_state()`. Verified: with a half-written state file, fake `zpool status tank` printed the pristine default pool (the earlier `simctl pull 1:4` + spare replacement were gone) with no warning; the next fake binary that saves persists those defaults permanently.

**Failure scenario**

README-endorsed usage — `simctl pull 1:5` in one terminal while `sim/run watch` polls every 2s in another. A reader (fake lsblk/zpool) opens state.json mid-write, gets partial JSON, silently operates on the default 8-disk pristine layout, and a subsequent save writes defaults back: the operator's scenario (pulled disk, created pool, dirty flags) is silently reset mid-test, producing bogus watch behavior that looks like a b2ctl bug.

**Suggested fix**

In save(), write to `STATE_PATH + ".tmp"` and `os.replace()` (atomic on POSIX). In load(), keep default_state() only for FileNotFoundError; on JSONDecodeError retry briefly then raise SystemExit with an explicit '[sim] corrupt state file' message instead of silently substituting defaults.

*Verifier note:* The atomic-write fix is right and stdlib-only: in save(), write to STATE_PATH + '.tmp' then os.replace(). But in load(), keep the default_state() fallback for FileNotFoundError specifically (not all OSError) — running simctl show/b2ctl before 'simctl init' relies on it; on json.JSONDecodeError raise SystemExit('[sim] corrupt state file: ...') instead of retrying (once save is atomic, a torn read can no longer occur, so a decode error always means a genuinely corrupt file and retrying is pointless).

**How to verify the fix**

tests/test_sim_smoke.py: write a truncated state.json, assert _simstate.load() raises/exits rather than returning default_state(); assert save() leaves no window where STATE_PATH is unparsable (os.replace path exists).

<details><summary>Verification trace</summary>

Traced: save() (lines 76-78) is a truncating in-place open('w')+json.dump with no lock or temp file; load() (lines 68-73) silently substitutes default_state() on both OSError and JSONDecodeError. The concurrent scenario is documented usage: sim/README.md line 19 says to run simctl in another terminal while watch is open, and the fake zpool status (sim/bin/zpool cmd_status lines 113-127) is itself a load-modify-save path polled by watch (bumps resilver pct then S.save), so a torn read followed by any save persists the pristine defaults, erasing the operator's scenario with no warning. A race-free trigger also exists: a hand-edited state.json with a JSON typo silently resets everything on the next command. No guard elsewhere; not intentional per CLAUDE.md. Severity downgraded from P2 to P3: the blast radius is the laptop simulation harness only (no real pool, no deployment path, no section-9 safety rule reachable) and the torn-write window is milliseconds on a ~4KB file — real but test-infrastructure debt, matching P3.

</details>

## F-115 — Fake perccli never emits VD/PD tables or rebuild %, so the whole RAID-mode lifecycle is unexercised and raid_actions._wait_rebuild hangs forever in sim

- **Priority**: P3 (Low)
- **Location**: `codes/sim/bin/perccli:22`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`if argv and argv[0].startswith("/c") and "show" in argv:` answers EVERY /cX show query (vall show all, eall/sall show all, show rebuild) with the same 'Drive /c0/eX/sY Device attributes / SN =' listing. hba_raid._parse_vall needs `/cN/vN :` headers + `EID:Slt DID State DG ...` token rows and _parse_pd_rows needs the same table — both parse nothing. hba_raid.rebuild_progress greps `(\d+(?:\.\d+)?)\s*%` and 'not in progress' — neither appears, so it returns {pct:0, done:False}. It also prints `Drive /c0/eNone/sNone` rows for the NVMe disks a PERC cannot see.

**Failure scenario**

`simctl mode raid` then `sim/run replace`: enumerate_disks() finds zero HW members ('no hardware RAID members found'), so the HW:vd/level column, volumes table, megaraid SMART and UGood/JBOD surfacing are never tested against the sim; if a rebuild poll is reached, raid_actions._wait_rebuild (raid_actions.py:57-67) loops forever at 0% until Ctrl-C. CLAUDE.md §8's claim that sim 'covers both backends and the whole lifecycle' is false for RAID, and any regression in _parse_vall/_parse_pd_rows is invisible in sim.

**Suggested fix**

Extend sim/bin/perccli (stdlib only): store VDs in state.json (simctl raid scenarios), answer `/cN/vall show all` with a `/c0/v0 :` header + VD summary row + 'PDs for VD 0' EID:Slt table, answer `/cN/eall/sall show all` with the EID:Slt PD table, answer `show rebuild` with 'Not in progress' or 'NN %' from state, and skip tran==nvme disks.

*Verifier note:* Fix is stdlib-fine but incomplete: (a) route 'show rebuild' BEFORE the generic show branch (argv=['/c0/eE/sS','show','rebuild'], match argv[-1]=='rebuild'), else it stays swallowed; (b) the eall/sall reply must KEEP the existing 'Drive /c0/eX/sY Device attributes' + 'SN =' sections — hba_raid._parse_bay_map depends on them — and add the EID:Slt PD table alongside, like real perccli; (c) PD rows need >=12 tokens with Sp as the last token (model is sliced tok[11:-1] in _parse_pd_rows/_parse_vall); (d) skip disks with tran=='nvme' (enc is None) instead of printing eNone/sNone; (e) to exercise the member path end-to-end, sim/bin/smartctl must also handle '-d megaraid,<DID>' (it currently has no megaraid support) and sim lsblk should emit a 'PERC ...' VD block device in raid mode so _is_perc_vd/ctrl_dev logic runs; also fix CLAUDE.md §8 / sim/README.md wording if the RAID lifecycle is intentionally left out.

**How to verify the fix**

Extend tests/test_sim_smoke.py: set mode=raid, run fake `perccli /c0/vall show all` and feed it to hba_raid._parse_vall — assert one volume and its members parse; assert rebuild_progress on fake output returns done=True when state says no rebuild.

<details><summary>Verification trace</summary>

Traced fully. sim/bin/perccli:22 answers every /cX show query (vall show all, eall/sall show all, show rebuild) with the same flat 'Drive /c0/eX/sY Device attributes / SN =' listing. hba_raid._parse_vall and _parse_pd_rows parse zero rows from it, so _vall_data() returns ([],[]) and sim RAID mode never produces HW members, volumes, megaraid SMART targets, or UGood drives; raid_actions.replace() exits at 'no hardware RAID members found' (raid_actions.py:80). rebuild_progress (hba_raid.py:395-399) on the fake output indeed returns {pct:0, done:False}. NVMe rows: _simstate gives NVMe disks enc=None/slot=None, so the fake literally prints 'Drive /c0/eNone/sNone' as claimed. Two corrections: (1) the title's '_wait_rebuild hangs forever' is unreachable in sim — replace() bails before any rebuild poll, and even if reached the KeyboardInterrupt handler (raid_actions.py:68) exits; (2) sim/README.md:26 scopes RAID mode to 'bay via storcli/perccli', so the stub is a deliberate minimum, though CLAUDE.md §8's 'covers both backends and the whole lifecycle' does overstate it. Net: real test-coverage gap in the sim harness; no effect on real hardware where perccli emits real tables — hence P3 (rubric: test-coverage gaps), not P2 (no error-handling gap or user-visible runtime effect exists).

</details>

## F-116 — Fake `zpool status` mutates sim state (resilver +50% per read) — the read path has side effects and progress is consumed by unrelated reads

- **Priority**: P3 (Low)
- **Location**: `codes/sim/bin/zpool:122`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

cmd_status: `p["resilver"]["pct"] = min(100, p["resilver"]["pct"] + 50)` followed by `S.save(state)` — every `zpool status` invocation advances and persists the resilver.

**Failure scenario**

b2ctl's read-only `status` (side-effect-free per CLAUDE.md §9), safety._capture_snapshot and _post_op_verify each call `zpool status`, so a resilver silently completes after ~2 arbitrary reads. watch._wait_resilver's 2s poll (watch.py:406-417) sees at most one intermediate frame regardless of cadence, and ordering bugs (e.g. detach issued before completion) can never manifest because completion races ahead of the workflow being tested.

**Suggested fix**

Store `{"started_at": <epoch>, "duration": N}` in pool["resilver"] and compute pct from wall-clock elapsed time in cmd_status (no save on read). simctl can set duration per scenario (default ~10s).

*Verifier note:* Suggested fix is sound and stdlib: store {'start': time.time(), 'secs': N} in pool['resilver'], compute pct = min(100, int(100*(now-start)/secs)) in cmd_status with no save on read. Default secs to ~8-10 and make it overridable (env var e.g. B2CTL_SIM_RESILVER_SECS, or a simctl subcommand) so pytest-driven sim runs can keep near-instant completion deterministically. Also update simctl cmd_pull, cmd_replace and cmd_attach, which all seed {'pct': 0}.

**How to verify the fix**

tests/test_sim_smoke.py: trigger a resilver via simctl pull, call fake `zpool status` 5 times rapidly, assert state.json content hash is unchanged by reads and pct is time-based, not read-count-based.

<details><summary>Verification trace</summary>

Verified lines 121-126: every `zpool status` (targeted or bare) bumps each in-progress resilver by 50% and persists via S.save — the fake read path mutates state. Traced the callers: zfs.topology() (used by core.scan, _detach_if_lingers, can_offline/can_detach), safety._capture_snapshot, safety._post_op_verify and zfs.poll_resilver_status all run `zpool status`, so any two arbitrary reads complete a resilver regardless of the workflow under test. The +50 step is deliberate per the inline comment ('simulates poll loop'), and CLAUDE.md §9's read-path rule governs b2ctl itself (unchanged), not sim fakes — so this is not a safety-rule violation, but the masking consequence is real: ordering/timing bugs in Task-B flows cannot manifest. One claim correction: _wait_resilver sees TWO intermediate frames (0% and 50%), not 'at most one' — poll_resilver_status renders before the bump. Harness fidelity issue only; P3.

</details>

## F-117 — Fake `zpool replace` finalizes instantly instead of creating a replacing-N vdev, masking Task-B detach/finalize bugs

- **Priority**: P3 (Low)
- **Location**: `codes/sim/bin/zpool:150`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

cmd_replace: `pool["replacements"] = [r for r in ... if r["removed"] != old]` then swaps members old->new and drops the spare in one step. Real ZFS keeps a `replacing-N` (or spare-N) group holding old+new until resilver completes; b2ctl's watch._detach_if_lingers (watch.py:420) must then `zpool detach` the lingering old token. In sim the old token vanishes immediately and cmd_detach of an unknown dev (line 216) silently succeeds.

**Failure scenario**

Run the replace-onto-spare workflow in sim: topology() never contains a replacing-N group, so _detach_if_lingers is a no-op and any detach — of the wrong token, or missing entirely — 'passes'. A regression that detaches the surviving new member instead of the old one (real pool-fault risk per CLAUDE.md §9) is undetectable in the harness.

**Suggested fix**

Model the intermediate state: on replace, append {"removed": old, "spare": new} to pool["replacements"] (reusing the existing spare-group rendering, or a replacing-N render) and only collapse it when resilver pct>=100 AND `zpool detach <old>` arrives; make cmd_detach return 1 with 'no such device in pool' for unknown tokens.

*Verifier note:* The infrastructure for the fix already exists: _render_status lines 74-88 render spare-N groups and cmd_detach lines 209-215 already collapse a replacement on detach of the removed token. So cmd_replace only needs to, when `new` is in pool['spares'], append {'removed': old, 'spare': new} to pool['replacements'] instead of swapping members (leave old in members); for a fresh-disk replace, keep old and render a replacing-N group, collapsing it in cmd_status once resilver pct>=100 (real ZFS auto-detaches old at completion in that case — do NOT require an explicit detach there, only for the spare case). Also make cmd_detach print 'cannot detach /dev/X: no such device in pool' to stderr and return 1 when the token matches neither a replacement's removed token nor a member/spare. All stdlib.

**How to verify the fix**

tests/test_sim_smoke.py: after fake `zpool replace tank /dev/sdb /dev/sde`, assert `zpool status tank` shows both /dev/sdb and /dev/sde under a replacing/spare group while pct<100, and that `zpool detach tank /dev/nonexistent` exits non-zero.

<details><summary>Verification trace</summary>

Traced cmd_replace lines 149-153: it clears any replacement record for old, swaps members old->new and removes new from spares in one step, so during 'resilver in progress' the old token is already gone from status. Grep confirms pool['replacements'] is only ever created by simctl pull (auto-spare), never by zpool replace — so a b2ctl-initiated replace-onto-spare (watch._replace_onto_spare -> _replace_member) never produces the spare-N/replacing-N intermediate that real ZFS keeps until detach. _detach_if_lingers (watch.py:420) then finds no lingering token and is a no-op in sim, and cmd_detach line 216 silently returns 0 for tokens not in the pool, so a wrong-token detach regression would pass the harness. Real ZFS keeps the old device under spare-N (INUSE) until `zpool detach <old>`, which is exactly Task-B step 4. Harness-only defect: b2ctl on real hardware is unaffected, hence P3 (test-coverage/fidelity gap) rather than the claimed P2.

</details>

## F-118 — Fake zpool silently no-ops `offline`/`online`, so the guarded spare-less offload path is never actually exercised in sim

- **Priority**: P3 (Low)
- **Location**: `codes/sim/bin/zpool:300`
- **Category**: test-gap
- **Verdict**: CONFIRMED

**Evidence**

`if sub in ("online", "offline", "clear", "trim", "scrub"): return 0` — no state change, so a member b2ctl just offlined still renders ONLINE. safety._post_op_verify's offline check (`serial not in o or "OFFLINE" in o`) passes vacuously because sim tokens are /dev/sdX and never contain the serial.

**Failure scenario**

The [o]ffload workflow (guarded `zpool offline` -> pool DEGRADED -> replace new disk in same bay) is run in sim: the pool never leaves ONLINE, the OFFLINE leaf never appears in topology, and the post-op verification succeeds by accident — a regression that offlines the wrong member (P0-class on the real raidz1 tank) would sail through the harness.

**Suggested fix**

Track `pool["offline"]: [names]` in state: cmd for `offline` adds the member (render state OFFLINE, pool DEGRADED), `online` removes it; keep clear/trim/scrub as no-ops.

*Verifier note:* Fix is correct; sharpen: track pool['offline'] = [names]; move 'online'/'offline' out of the line-300 short-circuit into the dispatch table (cmd_offline resolves the token via S.disk_by_token, appends, saves; cmd_online removes). In _render_status show state OFFLINE for those members, and in _pool_health count offlined members together with absent ones toward parity (offline member => DEGRADED; beyond parity => refuse/SUSPENDED). Keep clear/trim/scrub as no-ops. Note the serial-vs-token vacuity in safety._post_op_verify remains unfixable from the sim side (sim tokens are /dev/sdX by design); at least the 'OFFLINE' substring branch becomes genuinely exercisable.

**How to verify the fix**

tests/test_sim_smoke.py: fake `zpool offline tank /dev/sdb`, assert status shows `/dev/sdb ... OFFLINE` and pool DEGRADED, then `online` restores ONLINE.

<details><summary>Verification trace</summary>

Verified main() lines 300-301: online/offline/clear/trim/scrub return 0 before the dispatch table, with zero state change — a member b2ctl just offlined still renders ONLINE and the pool never goes DEGRADED. Traced the offload path: watch._offline_and_replace's can_offline guard does work in sim for raidz1/mirror pools (vdev labels exist), but the offline itself is swallowed, so degraded_leaves() never sees the OFFLINE leaf and the pool health check is untested. Verified the vacuous post-op check: safety._post_op_verify's offline lambda is `serial not in o or 'OFFLINE' in o`, and _render_status prints only /dev/<name> tokens (never serials), so `serial not in o` is always true in sim and the verification passes by accident. A wrong-member offline (P0-class on the real 3-disk raidz1 tank) would sail through. Harness-only test gap; the finding's own P3 is the right severity.

</details>

## F-119 — Docs (and CLAUDE.md §3) document `b2ctl locate <bay> on`, but the CLI only accepts an integer seconds argument — the documented latch-on form errors out

- **Priority**: P3 (Low)
- **Location**: `docs/user-guide-en.md:855`
- **Category**: docs
- **Verdict**: CONFIRMED

**Evidence**

docs/user-guide-en.md:855 "`b2ctl locate <bay> on`, and (destructive, double-confirmed) `b2ctl raid-create ...`" and CLAUDE.md:122 "`b2ctl locate <bay> on|off`" — but cli.py:478-480 defines `lo.add_argument("seconds", nargs="?", type=int, default=locatemod.DEFAULT_SECONDS)`, so 'on' is rejected by argparse.

**Failure scenario**

RAID-mode operator on the R640 follows the guide's disk-replacement section and types `b2ctl locate 32:0 on` to latch the bay LED before walking to the rack: argparse exits with "error: argument seconds: invalid int value: 'on'"; there is no way to latch the LED on/off as documented — only a timed blink — so the operator is stranded mid-procedure at the machine-room step.

**Suggested fix**

Either fix the three doc sites to the real syntax (`b2ctl locate <bay|serial|dev> [secs]`), or extend the parser to accept `on|off` verbs mapping to perccli `start/stop locate` for PERC PDs (stdlib argparse: accept str, validate int-or-on/off in _locate). Keep docs and CLI in lockstep whichever way.

*Verifier note:* Docs-only fix is the right one: change user-guide-en.md:855, user-guide-th.md:789, and CLAUDE.md:122 to the real syntax 'b2ctl locate <bay|serial|dev> [secs]' (matching guide line 729). Do NOT add a latched on/off verb without an auto-off safeguard — CLAUDE.md section 9/6 forbids leaving locate LEDs on resilvering/rebuilding disks, and locate.py's timed design ('LED is ALWAYS left off') is intentional; if the verb is ever added, it must be str-parsed in _locate (stdlib argparse) and still guarantee stop.

**How to verify the fix**

tests/test_cli.py::test_locate_arg_forms — build_parser().parse_args(["locate","32:0","on"]) asserts the chosen contract (SystemExit today, or verb accepted after the fix); plus a docs grep check in the same test that user-guide-en.md no longer advertises an unsupported form.

<details><summary>Verification trace</summary>

Traced cli.py:475-481: the locate subparser defines seconds as nargs='?' type=int, so 'b2ctl locate 32:0 on' exits with argparse 'invalid int value: on'. locate.py has no latch mode at all (blink is strictly timed; comment says 'The LED is ALWAYS left off', perccli start locate is always paired with stop locate). All three cited doc sites exist and are wrong: user-guide-en.md:855, user-guide-th.md:789, CLAUDE.md:122. The English guide even contradicts itself — line 729 documents the correct 'b2ctl locate <bay|serial|dev> [secs]' syntax. Failure-scenario impact is slightly overstated (raid-replace/raid-offline already light the bay LED per lines 852-854, so the operator has a working path in the same procedure), but the documented command genuinely hard-errors.

</details>

# P4 (Suggestion) findings

## F-120 — install --tool help claims 'default: all missing' contradicting the actual no-flag behavior; module docstring lists 5 of 24 subcommands

- **Priority**: P4 (Suggestion)
- **Location**: `codes/b2ctl/cli.py:569`
- **Category**: docs
- **Verdict**: CONFIRMED

**Evidence**

`metavar="TOOL", help="install only this tool (default: all missing)")` — but the no-flag path calls _installer_mod.install_base() which is report-only ('no download, no root needed', line 291, per the v0.8.3 install-parity contract). The install parser help (line 560) likewise says 'download and install tool binaries'. The module docstring (lines 3-8) still lists only status/watch/locate/offload/version.

**Failure scenario**

An operator reads `b2ctl install -h`, expects plain `b2ctl install` to fetch all missing tools (as the help states), runs it on a fresh box, and gets only a status report — then files it as a bug or, worse, assumes tools were installed and proceeds to `b2ctl status` failures.

**Suggested fix**

Change --tool help to 'install only this tool' and the install parser help to 'report tool status; with flags, download+install (sas2ircu/perccli)'; refresh the module docstring to the current subcommand list.

*Verifier note:* Suggested fix is right. Note install_base already prints 'add tools: b2ctl install --with-tools | --perc | --flash' at the end (installer.py:211), which mitigates the 'assumes tools were installed' half of the scenario — the operator does get a hint after running it.

**How to verify the fix**

tests/test_cli.py::test_install_help_matches_contract — assert 'default: all missing' no longer appears in build_parser() help text for install.

<details><summary>Verification trace</summary>

Traced: cli.py:569 help says '(default: all missing)' but the no-flag path (cli.py:291) calls installer.install_base(), whose docstring (installer.py:199-205) states it is a 'No-download status report' — matching the v0.8.3 install-parity contract in CLAUDE.md section 3 (no-flag = report only). The install parser help at cli.py:560 ('download and install tool binaries') has the same mismatch. The module docstring (cli.py:3-8) lists 5 subcommands (status/watch/locate/offload/version) against ~24 registered in build_parser. All three claims verified; the code behavior is the intentional contract, so this is purely a docs/help defect.

</details>

## F-121 — Module docstring documents 4 of the 12 watch commands, and `os`/`Disk` imports are dead

- **Priority**: P4 (Suggestion)
- **Location**: `codes/b2ctl/watch.py:7`
- **Category**: docs
- **Verdict**: CONFIRMED

**Evidence**

Lines 7-10 list only `r / s / l / q` while _MENU (line 785) and run()'s dispatch offer r/a/o/s/d/t/n/e/b/x/l/q; line 18 `import os` and the `Disk` name imported on line 25 are never used in the module (grep shows no `os.` usage and Disk only appears inside message strings).

**Failure scenario**

A new operator or maintainer reading the module header (or help derived from it) believes assign/offload/demote/extend/burnin/destroy do not exist in watch; the stray imports mislead about module dependencies.

**Suggested fix**

Update the docstring command list to match _MENU and drop `import os` and `Disk` from the imports (keep run_check and the colour names).

*Verifier note:* Fix is correct as stated. Keep run_check and the colour names from common; sync the docstring command list with _MENU.

**How to verify the fix**

python3 -m py_compile b2ctl/watch.py plus the existing tests/test_watch.py suite (no behavioral test needed).

<details><summary>Verification trace</summary>

Traced: docstring lines 7-10 list only r/s/l/q while _MENU (watch.py:785-786) and run()'s dispatch (806-831) offer r/a/o/s/d/t/n/e/b/x/l/q. `import os` (line 18) has zero `os.` usages in the module; `Disk` (imported line 25) appears only inside f-string message text (lines 129, 226, 252), never as a name. tests/test_watch.py imports Disk from b2ctl.common (line 10), not from watch, so removing both imports is safe.

</details>

## F-122 — Google Drive file IDs and download base URL are duplicated verbatim between install.sh and b2ctl/installer.py

- **Priority**: P4 (Suggestion)
- **Location**: `codes/install.sh:13`
- **Category**: maintainability
- **Verdict**: CONFIRMED
- **Also independently reported as**: TOOLS_DIR is computed and never used (dead code left from the pre-download local-tools flow)

**Evidence**

`_GDRIVE_SAS2IRCU="1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt"` / `_GDRIVE_PERCCLI=...` / `_GDRIVE_BASE=...` (install.sh:13-15) are byte-identical copies of installer.py:18-22, despite the documented 'one contract' between ./install.sh and `b2ctl install`.

**Failure scenario**

A tool archive is re-uploaded to Drive and only installer.py is updated: `b2ctl install --tool perccli` fetches the new binary while ./install.sh --perc silently downloads the stale one (or a dead ID that fails only via the weak 1KB check) — the two documented-equivalent paths install different binaries.

**Suggested fix**

Single source: have install.sh extract the IDs from the packaged installer.py at run time (`python3 -c "import b2ctl.installer as i; print(i._GDRIVE['sas2ircu'])"` with PYTHONPATH=SRC_DIR), or move IDs to a small tools.json read by both.

*Verifier note:* The python3 -c extraction works but couples install.sh to installer.py private names (_GDRIVE); note install.sh must be able to run before python3 deps are confirmed, so keep a fallback. A tools.json read by both (json is stdlib for the Python side; install.sh can grep/python3 -c it) is the cleaner option. Either satisfies stdlib-only. | Merged duplicate's note: Either delete the line, or the offline-install variant is genuinely useful for airgapped boxes: `if [ -f "${TOOLS_DIR}/SAS2IRCU_P20.zip" ] ...` then skip download_tools and pass TOOLS_DIR to install_tools. If adding the offline path, mirror it in installer.py to keep the documented parity.

**How to verify the fix**

Extend tests/test_installer.py: regex the IDs out of install.sh and assert they equal installer._GDRIVE.

<details><summary>Verification trace</summary>

Traced: install.sh:13-15 (_GDRIVE_SAS2IRCU=1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt, _GDRIVE_PERCCLI=1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk, drive.usercontent.google.com base) are byte-identical to installer.py:19-23 (_GDRIVE dict + _BASE). The 'one contract' parity is documented in CLAUDE.md §3 and commit 89b075b, so divergence on re-upload is a real drift risk between two documented-equivalent paths. Pure duplication/maintainability, no current behavior difference — P4.

</details>

## F-123 — disk_by_bay only parses int:int bays, so simctl cannot address the NVMe disks by their displayed bay (PCIe2:0) and pull prints 'bay None:None'

- **Priority**: P4 (Suggestion)
- **Location**: `codes/sim/_simstate.py:105`
- **Category**: bug
- **Verdict**: CONFIRMED

**Evidence**

`enc, slot = (int(x) for x in bay.split(":"))` raises ValueError for 'PCIe2:0' and returns None; simctl._find routes anything containing ':' to disk_by_bay, and cmd_pull prints `f"... (bay {d['enc']}:{d['slot']})"` which renders 'bay None:None' for NVMe.

**Failure scenario**

Operator copies the BAY value 'PCIe2:0' straight from the b2ctl table (the documented workflow) into `simctl pull PCIe2:0` to test NVMe hotplug: '[sim] no such disk: PCIe2:0'. Pulling by name works but its confirmation line reads 'pulled nvme0n1 (bay None:None)'.

**Suggested fix**

In _find/disk_by_bay, on int-parse failure fall back to matching the bay label from sim/bay_map.json (serial->bay) or accept a serial; in cmd_pull/cmd_insert print the mapped label or the device name when enc/slot are None.

*Verifier note:* Suggested fix is stdlib-only and workable; simpler variant: in simctl._find, when disk_by_bay returns None fall back to a serial->bay index built by json-loading sim/bay_map.json (nvme panel entries) and then disk_by_token, or just try disk_by_name/disk_by_token as a second chance for any ':'-containing ident; in cmd_pull/cmd_insert/cmd_show print the mapped bay label or d['name'] when enc is None instead of 'None:None'.

**How to verify the fix**

tests/test_sim_smoke.py: assert `simctl pull PCIe2:0` (subprocess) pulls nvme0n1 and its message contains no 'None'.

<details><summary>Verification trace</summary>

Traced: line 105 int() raises ValueError on 'PCIe2:0' and disk_by_bay returns None; simctl._find (simctl line 19) routes any ident containing ':' to disk_by_bay, so 'simctl pull PCIe2:0' fails with 'no such disk' even though sim/bay_map.json maps the two NVMe serials to exactly that displayed BAY string. NVMe disks have enc=None/slot=None in default_state() (lines 48-53), so pulling by name prints 'pulled nvme0n1 (bay None:None)' (simctl line 53; same in cmd_insert line 63 and cmd_show line 83). Workaround (pull by device name) exists and the docstring says <bay|dev>, but nothing documents name-only NVMe addressing, so it is a real UX defect, not intentional design. Sim-harness cosmetic/usability only — P4 as reported.

</details>

# Appendix A — Fix ordering for the fixing model

Recommended batches. Within a batch, findings are independent and safe to fix together; batches build on each other. After every batch run `cd codes && python3 -m py_compile b2ctl/*.py && python3 -m pytest tests/ -q`, and validate interactive flows against the sim harness (`python3 sim/simctl init && python3 sim/run status`; switch backend with `sim/simctl mode raid|it`).

**Batch 1 — shared safety infrastructure (fix before anything that depends on it):**
- F-004 (`common.py:59`) + F-008 (`safety.py:15`) share one root cause: the dry-run write-gate matches `args[0]` as an exact token against `WRITE_CMDS`, which both omits `perccli` and misses config-resolved absolute tool paths. Fix once: normalize `os.path.basename(args[0])` (and strip a `perccli64`-style suffix) before matching, and add the perccli/dd binaries to `WRITE_CMDS`. Every later `--dry-run` claim in cli/watch/raid_actions relies on this gate — fix and test it first, or dry-run testing of later batches lies to you.
- F-014 (`config.py:56`) config.load robustness — everything imports config at startup.
- F-006 (`locate.py:78`) add the central "refuse to blink a resilvering/rebuilding disk" guard in locate.py itself. Callers F-001/F-002 (`cli.py:51/58`) and F-020 (`watch.py:399`) then only need to pass disk state / handle the refusal; fixing callers before the central guard just duplicates logic.

**Batch 2 — resilver/replace flow (the data-loss cluster):**
- F-025 (`zfs.py:329`) fix `poll_resilver_status` parsing first (positive-match completion, handle "no estimated completion time").
- F-009 (`watch.py:445`) then make `_replace_member` honor `_wait_resilver`'s result (skip detach + LED on failure), and apply the same guard at `watch.py:176-179` and the swap path.
- F-057 (`watch.py:658`) fold `_cmd_swap` into `_replace_member` while you are there — it is the same flow copy-pasted, and fixing F-009 in two places otherwise.
- F-055 (`watch.py:406`) bounded/interruptible `_wait_resilver` loop belongs to the same edit.
- F-063 (`zfs.py:479`) `prune_orphan_crons` no-op on empty `list_pools()` — small, same file, same test fixture family.

**Batch 3 — remaining P0/P1 action paths (mostly independent, safe to parallelize):**
- cli.py: F-003 (confirmations for cache/log ops), F-013 (rollback dry-run), F-002 remainder after Batch 1.
- watch.py: F-019 (hotplugged in-pool disk offered for wipe), F-021 (extend pool picker — reuse `_pick_pool()`), F-022 (KeyboardInterrupt/EOF at prompts).
- zfs.py: F-023 (can_detach 2-way mirror), F-024 (can_offline nested spare/replacing vdevs).
- RAID mode: F-007 (raid replace "Not in progress" false success), F-016 (perccli actions must use controller enc:slot, not the bay_map display label — keep the raw enc:slot on the Disk object when relabelling), F-017 (blink_many routing), F-010 (backend auto-detect must check sas2ircu exit/controller output, not truthy stdout), F-011 (burnin `-d` dtype), F-012 (burnin scan timeout).
- F-005 (`core.py:27`) move the udevadm ghost-rescue out of the read path (opt-in flag or watch-only).
- F-015 (`core.py:53`) ghost-serial prefix/equality mismatch.
- F-018 (`smart.py:62`) SAS FAILURE status must map to CRITICAL in `assess()`.

**Batch 4 — P2 correctness/robustness, grouped by file** (each file group is one sitting: baymap, burnin, cli, config, hba, hba_raid, installer, locate, smart, watch, zfs, install.sh, sim). Notable couplings: F-028+F-037+F-040/F-041 are all "cache one scan's subprocess results" — introduce a per-scan cache once (e.g. pass a scan context or `functools.lru_cache` cleared per scan) rather than three ad-hoc fixes; F-050+F-051 (smart raw-value parsing + LBA size) touch the same parser.

**Batch 5 — P3 test-gaps and structure.** Add the named missing tests alongside whichever earlier batch touches that module (cheaper than a separate pass); pure-structure findings (duplication, dead code, module splits) last, since earlier fixes change the code they would restructure.

**P4 last, or fold into adjacent edits.**

Cross-cutting cautions for the fixer:
- Several existing tests mock the wrong data shape (e.g. `list_pools` mocked as `["tank"]` instead of list-of-dicts — see F-021). When a fix "breaks" such a test, fix the fixture to match the real shape, never the code to match the fixture.
- Do not add pip dependencies; everything here is fixable with stdlib.
- Mutating-path fixes must preserve the CLAUDE.md section 9 contract: explicit `[y/N]` naming device+pool+operation, by-id device paths, no LED on resilvering disks.
- Per CLAUDE.md section 10: bump the version in `cli.py` and update the reader + DevOps docs when behaviour changes.

# Appendix B — Refuted findings (do NOT "fix" these)

Raised during review, then struck down by adversarial verification. Listed so they are not re-reported or "fixed" into regressions.

- **perccli PD-row parser assumes Sp is the last column; perccli/storcli 7.x (the Dell version for H730P) appends a trailing 'Type' column, corrupting the model and breaking TBW lookup** (`codes/b2ctl/hba_raid.py:160`)
  - Refutation: Two independent refutations. (1) The premise is contradicted by the project's own hardware evidence: tests/test_hba_raid.py:8 carries a PD table annotated 'Real perccli /c0/vall show all output from a Dell R640 / PERC H730P Mini' — the exact deployment hardware — and its header ends 'Model Sp' with no trailing Type column, so tok[11:-1] parses correctly on the real box; the claim that 'the Dell version for H730P' appends Type is asserted, not observed. (2) Even on a hypothetical Type-emitting perccli build, the stated failure chain (TBW lookup misses, endurance never raised) mostly breaks: smart._parse_ata (smart.py:76-78) unconditionally overwrites d.model from smartctl's 'Device Model:' line, and the deployment's members are Samsung SATA SSDs read via megaraid passthrough as ATA — so spec.lookup would use the clean SMART model whenever the drive is readable. The corrupted model would only persist for SAS drives (_parse_sas keeps an existing model) or SMART-unreadable drives, which get no endurance computed anyway.
- **ensure_prereqs() mutates the system (dpkg --add-architecture i386 + apt-get install) before the 'all tools already installed' early return** (`codes/b2ctl/installer.py:169`)
  - Refutation: The cited code exists, but the failure scenario is unreachable and the contract reading is wrong. (1) `b2ctl install --with-tools` calls install_tools(["sas2ircu","perccli"]) (cli.py:285) — an explicit list skips the tools-is-None branch entirely, so the 'all tools already installed / reports it did nothing' early return never fires; with an explicit list both tools are unconditionally re-downloaded and reinstalled, so ensure_prereqs runs on a run that genuinely adds tools, satisfying CLAUDE.md's 'apt prereqs install only when a tool is actually added'. No call site in the repo (cli.py 277/281/285/289, install_profile line 225) ever passes tools=None, so the ordering flaw is dead code. (2) Installing both prereqs for single-tool profiles matches install.sh (line 85 installs an even larger set in one apt line) and the contract promises prereqs 'when a tool is added', not per-tool granularity — intentional parity, not a bug.
- **NVMe bay matching uses substring serial equality and first-entry-wins ordering, contradicting the documented by-id > serial > bdf precedence** (`codes/b2ctl/baymap.py:79`)
  - Refutation: The behavior is as described but it is documented intentional design, not a contradiction. The module docstring (lines 71-72) states BOTH rules: 'precedence by-id > serial > bdf. First matching entry wins.' — key precedence applies WITHIN an entry (an entry 'may key on' several fields, and lines 75-83 check by-id, then serial, then bdf in exactly that order), while cross-entry resolution is explicitly first-match. The CLAUDE.md one-liner is a summary of that same within-entry rule. The substring serial tolerance (`tgt in serial`, with the redundant `tgt == serial or` showing it was a conscious choice) matches the codebase's deliberate fuzzy-serial house style for tool-truncated serials — hba.py:189 does mutual prefix matching for sas bays, and CLAUDE.md section 6 records serial-substring membership matching as a solved gotcha to keep. The failure scenario requires the operator to author a truncated serial key that collides with another drive in their own 2-3 entry map; the shipped example (sim/bay_map.json) instructs copying the serial verbatim from the SERIAL column. Same-model NVMe serials are equal length, so full serials cannot substring-collide.
- **_cmd_swap silently re-adds the wearing disk as the pool's hot spare (and auto-detaches) beyond what the single [y/N] approved** (`codes/b2ctl/watch.py:667`)
  - Refutation: The behavior is documented intentional design, not silent scope creep: docs/user-guide-en.md:341-345 defines [s]wap as 'swap trades places (old disk becomes the new spare; spare enters the pool as a member)' and explicitly contrasts it with offload ('removes the disk from the pool entirely') — an operator intending to RETIRE a worn disk is directed to [o]ffload, which exists for exactly that (watch.py:333-378). The re-add-as-spare is the defining semantic the operator selects when choosing swap, and the result is announced ('✔ ... is now a hot spare'), not silent. The detach at 665 is _detach_if_lingers, the same unconfirmed replace-finalization step used by every other confirmed replace flow (:179, :447), so it is consistent, not an anomaly. The residual valid point — the confirm prompt could spell out all three commands — is already the substance of finding 12; on its own this is prompt-wording polish.
