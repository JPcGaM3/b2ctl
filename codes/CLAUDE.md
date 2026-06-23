# CLAUDE.md — b2ctl (IT-mode) project handover

> Save this file at the repo root (`~/scripts/b2ctl/codes/CLAUDE.md`). Claude
> Code reads it automatically as project context. It can also be pasted as the
> first message in a fresh Claude Code session.

You are continuing development of **b2ctl**, a Python CLI for monitoring and
managing ZFS disks on Dell R620 servers. Phase 1 (health monitoring) and the
IT-mode rewrite (v0.2.0-itmode) are done and running on real hardware. Your job
is the next set of lifecycle features, described in §7.

---

## 1. What b2ctl is

A stdlib-only Python CLI that shows a wide per-disk health table (bay, model,
serial, power-on hours, wear, TBW endurance, bad sectors, SMART health,
pool/vdev, and an overall LEVEL) plus a details block, and performs ZFS disk
lifecycle actions. It is the IT-mode/HBA sibling of an earlier storcli/RAID-mode
build; the old perccli `ssd_health.py` script is the visual reference for the
table + details layout.

## 2. Environment (read carefully — it dictates every command)

- 2× Dell R620, **Proxmox VE 9.2** (Debian 13, ZFS 2.4).
- PERC H710 mini **crossflashed to IT/HBA mode** → presents as LSI SAS9207-8i
  (SAS2308). **Disks are raw.** There is NO storcli/perccli and NO
  `smartctl -d megaraid`. Do not reintroduce them.
- Identify/inspect disks with: `lsblk` (serial/model), `smartctl -a /dev/sdX`
  (direct, auto device type), `sas2ircu <c> DISPLAY` (serial→enclosure:slot),
  `sas2ircu <c> LOCATE <enc:slot> ON|OFF` (LEDs).
- SATA SSDs sit behind the SAS2308, so `lsblk` reports `TRAN=sas` for them.
  That is expected (SATA-over-STP); the SMART parser still reads them as ATA.

**Storage layout (current):**
- `rpool` — RAID1 mirror, 2× Samsung 860 PRO 1TB, on `-part3` of by-id devices
  (ZFS-on-root). This is the Proxmox boot pool.
- `tank` — **raidz1 (RAID5), 3× Samsung 870 EVO 1TB, + 1 hot spare**
  (just reconfigured from RAID10). Pool members are `/dev/disk/by-id/ata-*`
  whole disks. Props: `ashift=12, compression=lz4, atime=off, xattr=sa`.
- Pools on BOTH servers are named `rpool`/`tank` (intentional — eases future
  replication/migration between the two nodes).

> Because `tank` is now **raidz1**, resilver reads all surviving members and is
> slower/more stressful than a mirror resilver, and the pool tolerates only ONE
> failed disk. The hot spare auto-resilver (or manual `zpool replace`) is the
> recovery path. raidz vdevs cannot be removed or converted in place.

## 3. Repo layout & deploy

```
codes/
  b2ctl/  common.py spec.py hba.py smart.py zfs.py core.py ui.py watch.py cli.py
          __init__.py __main__.py
  ssd_spec.json        # SSD model -> rated TBW
  install.sh           # -> /opt/b2ctl + launcher /usr/local/sbin/b2ctl
documents/
  b2ctl-itmode-reader.md     # reader-facing guide
  b2ctl-itmode-devops.md     # DevOps guide (every subprocess)
```
Run on the box as root (no `sudo` on Proxmox): `b2ctl status`, `b2ctl watch`,
`b2ctl locate <bay> on|off`, `b2ctl swap`, `b2ctl version`.

## 4. Conventions (follow these — they are the user's house style)

- **Code & all output: English**, minimal comments. The user converses in
  **Thai**; reply to the user in Thai, keep code/identifiers/log strings English.
- **Python stdlib ONLY.** No pip dependencies. (`curses`, `select`, `re`,
  `subprocess`, `dataclasses` are all fine — they are stdlib.)
- **Modular "our style"**: small single-responsibility modules, a `Disk`
  dataclass, `run()`/`run_check()` for external commands (list-form, no shell).
- **Documentation: two docs per deliverable** using the `engineering:documentation`
  skill — a reader-facing guide (easy) AND a DevOps guide (covers every
  sub-process). Architecture decisions get an ADR (`engineering:architecture`).
- Ask structured clarifying questions when intent is ambiguous; build
  incrementally and test before claiming done.

## 5. Module map

| module | role | external cmds |
|--------|------|---------------|
| common.py | colours, run/run_check, `Disk` (now carries `pool_token`), `assess()` (levels NORMAL/CONFIG/WARNING/CRITICAL; END_WARN=30, END_CRIT=10) | — |
| spec.py | load/lookup TBW from `ssd_spec.json` | — |
| hba.py | `enumerate_disks()` (lsblk **-P** pairs), `_by_id_index()` (ata->scsi-SATA->wwn->scsi), `attach_bays()`/`bay_map()`, `get_ghost_disks()` | lsblk, sas2ircu |
| smart.py | direct SMART parse (ATA + SAS, SSD/HDD), TBW endurance | smartctl |
| zfs.py | `topology()` (zpool status **-P -v**), `attach_membership()`, `degraded_leaves()`, `spares()`, actions (add_spare/add_mirror/attach/replace/swap_to_spare/wipe) | zpool, wipefs, sgdisk |
| core.py | `scan()` pipeline + `scan_one()` | composes above |
| ui.py | `render_table/pools/details/new_disk` (reference style), `disk_label()` | — |
| watch.py | interactive `select()` loop: hotplug detect + prompts, commands r/a/o/s/d/n/l/q | lsblk |
| locate.py | blink LED using dd | dd |
| cli.py | argparse: status/watch/locate/swap/demote/create/version | sas2ircu |

## 6. Gotchas already solved (don't regress these)

- **Membership matching**: pools use by-id names and rpool uses `-part3`, so
  path/realpath matching alone fails. `attach_membership()` falls back to
  **matching the disk serial inside the leaf token** — keep that fallback.
- **lsblk must use `-P`** (KEY="value" pairs); positional parsing breaks because
  MODEL contains spaces (`Samsung SSD 870 EVO 1TB`).
- **install.sh**: the dependency-check loop must use `"$bin"` (NOT `"\$bin"`);
  only the launcher heredoc keeps `\$@` escaped.
- ETA from `zpool status` is ZFS's own estimate and **jumps around**; `% done`
  is reliable. On a near-empty pool resilver finishes in seconds — write some
  test data first if you need to see progress.
- Old RAID-mode lesson, still true: never light a locate LED on a
  rebuilding/resilvering disk; only on disks needing physical pull.

## 7. Your tasks (priority order)

### Task A — fix `watch` prompt clutter (small, do first)
`watch.run()` reprints `b2ctl> ` on every `select` timeout, so the prompt piles
up (`b2ctl> b2ctl> b2ctl>`). Print the prompt **once**, poll for hotplug
**silently** on timeout, and only reprint the prompt/table after a command or
event. Don't change the action prompts.

### Task B — replace-onto-spare, with live progress (the big one)
Add a guided workflow that:
1. Lets the user pick an in-pool disk to replace. Confirm with `(bay) model (serial)`.
2. `zpool replace <pool> <member> <spare>` (resilver onto the hot spare directly without offline).
3. **Live resilver UI**: poll `zpool status <pool>` every 2–3 s, parse the
   `scan:` line, render a **progress bar (% done)** + the ZFS **ETA "to go"** as
   a countdown.
4. On completion: `zpool detach <pool> <old faulted disk>` (if lingering) to finalise the
   spare as a permanent member, then tell the user **which bay to pull**
   (light its LED via locate).
5. Detect the **new disk inserted** (reuse watch's hotplug path) and offer a shared `_assign_free_disk` menu.

### Task C — demote an active mirror member to spare (guarded)
`zpool detach` a mirror leg, then `zpool add ... spare`. **Guard hard**: refuse
(or warn + double-confirm) if detaching would leave a vdev with no redundancy.
Applies to rpool (mirror) and any future mirror pools — tank is raidz1 now so
this won't apply to it.

### Task D — assign/offload unification and pool creation guards
- Unify `assign` and `offload` paths into a shared `_assign_free_disk` menu.
- Add `zfs.MIN_DISKS` and label checks (warn and confirm before wipe) to `create` pool.

### Task E — optional `b2ctl top` (curses, read-only monitor)
**Deferred.** Read-only curses auto-refresh monitor; keep all interactive actions in `watch`.

## 8. Testing

No hardware needed for logic. Put fake `lsblk`/`smartctl`/`sas2ircu`/`zpool` on
`PATH` (small Python scripts) emitting realistic output for the current layout
(rpool mirror on `-part3`; tank raidz1 3 disks + 1 spare). Unit-test the
parsers and the membership-by-serial fallback. For Task B, mock a sequence of
`zpool status` outputs that walk 0% → 50% → 100% → "resilvered ... with 0
errors" and assert the bar/ETA/countdown and the detach+replenish steps fire in
order. Run `python3 -m py_compile b2ctl/*.py` before finishing.

Test layout: **one test file per source module** — `tests/test_<module>.py`
(e.g. `test_zfs.py`, `test_watch.py`). Shared `_disk()` factory and sample
command outputs live in `tests/helpers.py` (`tests/conftest.py` puts that on
the path). Run the suite with `cd codes && python3 -m pytest tests/ -q`.

Beyond unit tests there is a **stateful simulation harness at `codes/sim/`** —
the "fake binaries on PATH" idea made stateful: run the *real* b2ctl against a
fake 6-disk server (`state.json`) on a laptop, no hardware/SSH/root.
`python3 sim/simctl init && python3 sim/run status`; change state with
`sim/simctl pull|insert|dirty|mode|show`. Covers both backends (IT/RAID) and the
whole lifecycle; sim audit/snapshots land in `sim/var/` (never `/var/log/b2ctl`).
b2ctl itself is unmodified (sim = fake binaries + a launcher). See `sim/README.md`.

## 9. Safety rules (non-negotiable)

- Read path (`status`, `top`) is side-effect-free.
- Every mutating action: explicit `[y/N]` naming device + pool + operation;
  `wipe` adds a serial-level warning.
- Always act on **by-id**, never the unstable `/dev/sdX`.
- Never auto-resilver/detach without confirmation. Never light an LED on a
  resilvering disk.
- b2ctl does not touch Proxmox boot config. rpool boot-disk replacement still
  requires the operator to run `proxmox-boot-tool format/init` on the new ESP
  manually — surface this in the workflow, don't automate it.

## 10. Definition of done (per deliverable)

Working code (compiles + mock-tested) **and** the two docs updated
(reader + DevOps) per §4, plus an ADR if the change is architectural. Update
ADR-001 to record that the build is now IT-mode/HBA on Proxmox ZFS-on-root and
that `tank` is raidz1+spare (the original "OS on hardware RAID1, no boot pool"
assumption is void). Bump the version string in `cli.py`.
