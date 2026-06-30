# b2ctl (IT-mode) — Reader Guide

> 📖 อยากเห็นแบบ **กดอะไร → เห็นอะไร** ทีละขั้นพร้อม output จริง → ดู
> [`walkthrough.md`](walkthrough.md) (step-by-step walkthrough)
> 🧪 อยากลองทุก flow โดย **ไม่มี hardware** → simulation harness ที่ `codes/sim/` (ดู `codes/sim/README.md`)

---

## Contents

1. [What it does](#1-what-it-does)
2. [Install](#2-install)
3. [Getting started](#3-getting-started)
4. [🔥 Runbooks (Real-world Scenarios)](#4--runbooks-real-world-scenarios)
5. [Reading the table](#5-reading-the-table)
6. [All features in watch mode](#6-all-features-in-watch-mode)
7. [Safety features](#7-safety-features)
8. [Warnings](#8-warnings)
9. [🚀 Quick Reference / Cheat Sheet](#9--quick-reference--cheat-sheet)

---

## 1. What it does

A command-line tool for watching SSD/HDD health and managing ZFS disks on a
Dell R620 whose PERC H710 has been crossflashed to **IT/HBA mode** (presents as
LSI SAS9207-8i / SAS2308). Disks are raw — no RAID controller to query — so
b2ctl reads each drive directly and talks to ZFS for everything else.

**b2ctl can:**

- Show one table row per physical disk: bay, device, model, serial, power-on
  hours, wear, endurance left, total written, bad sectors, SMART health, pool/vdev,
  and an overall **LEVEL** (NORMAL / CONFIG / WARNING / CRITICAL).
- List ZFS pools and their health.
- Spell out exactly which disks need attention and why.
- **Watch for disks you plug in or pull out** — asks what to do with a new disk:
  add spare, replace a failed disk, or wipe it blank.
- Blink a disk's LED so you know which physical drive to pull (works around
  scrambled bay numbers — see §8).
- **Preview operations in dry-run mode** — shows the exact commands that would
  run without changing anything.
- **Record every mutating action** to an audit trail at `/var/log/b2ctl/ops.jsonl`,
  with a pre-op snapshot and rollback hint.
- **Roll back a previous operation** with `b2ctl rollback <op_id>`.

> 📌 Note: This is the IT-mode sibling of the original storcli/RAID-mode b2ctl. Same look
> and feel, but no perccli/storcli or megaraid passthrough needed.

---

## 2. Install

```bash
cd codes
sudo ./install.sh
```

Copies the package to `/opt/b2ctl`, creates the `b2ctl` launcher at
`/usr/local/sbin/b2ctl`, and creates `/var/log/b2ctl/snapshots/` for the audit
system.

**Install tool binaries at the same time (recommended on a fresh server):**

```bash
cd codes
sudo ./install.sh --with-tools
```

The `--with-tools` flag automatically **downloads** `sas2ircu`, `storcli64`, and
`perccli64` archives from Google Drive, extracts the binaries, and places them in
`/usr/local/sbin/`. Downloads are deleted on completion. Each tool installs
independently — if a download or extraction fails it prints `[✗]` and continues.
Requires `curl` or `wget` (both available by default on Proxmox VE).

**Dependencies:**

| binary | purpose | required? |
|--------|---------|-----------|
| `smartctl` (smartmontools) | read disk SMART health | required |
| `zpool` (zfsutils-linux) | ZFS pool management | required |
| `lsblk` | disk enumeration | required |
| `sas2ircu` | bay numbers (enclosure:slot mapping) | optional — bays show `-` without it. sas2ircu is a 32-bit binary; if installed but bays still `-`, run `apt-get install -y libc6-i386` |
| `ledctl` (ledmon) | activity LED locate | optional — falls back to dd |
| `wipefs`, `sgdisk` | disk wipe action | optional — needed for wipe only |

---

## 3. Getting started

Two ways to run b2ctl:

### 3.1 Quick health check (status)

```bash
sudo b2ctl status
```

Shows the disk table, pool summary, and details block — then exits.

**Options:**

| command | what it does |
|---------|-------------|
| `sudo b2ctl status --locate` | same + blink LEDs on WARNING/CRITICAL disks for ~5s |
| `sudo b2ctl status --json` | machine-readable JSON output |
| `sudo b2ctl --dry-run status` | preview mode — read commands still run, writes suppressed |

### 3.2 Interactive watcher (the main event)

```bash
sudo b2ctl watch
```

Shows the table once, then watches continuously. Two things happen automatically:

- **You insert a disk** — b2ctl detects it within ~2 seconds, prints a panel
  about the new drive, and asks what to do.
- **You pull a disk** — b2ctl reports which device disappeared and reprints pool
  health so you can see if a pool went DEGRADED.

After it starts you'll see:

<details>
<summary>📋 View Watch Mode Screen</summary>

<pre>
================================================================================
BAY   DEV  IF   MODEL            SERIAL            POWER_ON      WEAR   END    ...
--------------------------------------------------------------------------------
1:0   sdf  SAS  Samsung SSD 860  S5G8NE0MXXXXXXX   51020h(~5.8y) 1%     99.2% ...
1:1   sda  SAS  Samsung SSD 860  S5G8NE0MXXXXXXX   51021h(~5.8y) 1%     99.1% ...
1:4   sdb  SAS  Samsung SSD 870  S74ZNS0WXXXXXXX   18238h(~2.1y) 1%     98.4% ...
1:5   sdc  SAS  Samsung SSD 870  S74ZNS0WXXXXXXX   18243h(~2.1y) 1%     98.4% ...
1:6   sdd  SAS  Samsung SSD 870  S74ZNS0WXXXXXXX   18246h(~2.1y) 1%     98.4% ...
1:7   sde  SAS  Samsung SSD 870  S74ZNS0WXXXXXXX   18247h(~2.1y) 1%     99.8% ...
================================================================================
Pools:
  rpool   952G   4.83G  free=947G   ONLINE   cap=0%
  tank    2.72T  1.72G  free=2.72T  ONLINE   cap=0%
[OK] all disks healthy and assigned

[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [n]ew-pool  [e]xtend  [b]urnin  [t]oggle-dryrun  [l]ocate  [q]uit
b2ctl&gt;
</pre>
</details>

Type a single letter to act.

---

## 4. 🔥 Runbooks (Real-world Scenarios)

### A disk failed — replace it

1. `b2ctl> l` → enter the failed disk's serial → LED blinks on its bay → pull it
2. Insert the new disk → b2ctl detects it, shows a panel
3. Choose `[3] REPLACE` → pick the FAULTED pool member → confirm the dialog
4. ZFS resilver starts automatically — check progress with `zpool status tank`

```
  action> 3
    [1] tank: /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W... (FAULTED)
  replace #> 1
```

### A disk is wearing out — swap to spare before it fails

1. Watch shows LEVEL = **WARNING**, END(left) low
2. `b2ctl> s` → pick the worn disk → confirm
3. Resilver starts onto the spare — wait for completion
4. Worn disk becomes the new spare; spare enters the pool as a member
5. No physical move needed

### Add a fresh spare

1. Insert the disk → b2ctl detects it
2. Choose `[2] Add to a pool as hot SPARE` → pick the pool → confirm

### Create a new pool

1. `b2ctl> n` → pick disks → name the pool → pick RAID type → confirm
2. Recommended: `raidz1` for 3 disks, `mirror` for 2 disks

### Safely remove a disk from a pool

1. `b2ctl> o` → pick the disk → confirm resilver to spare (raidz) or confirm detach (mirror/spare)
2. Wait for resilver to complete (if needed)
3. `b2ctl> l` → blink the bay → physically pull the disk

### Preview an operation before running it

1. `b2ctl> t` → dry-run enabled
2. Run any operation — commands print without executing
3. `b2ctl> t` → dry-run disabled, back to live

---

## 5. Reading the table

| column | meaning | example |
|--------|---------|---------|
| **BAY** | enclosure:slot number | `1:4` = enclosure 1, slot 4 |
| **DEV** | Linux device name | `sda`, `sdb` |
| **IF** | interface type | `SAS`, `SATA`, `NVMe` |
| **MODEL** | drive model | `Samsung SSD 870` |
| **SERIAL** | unique serial number | `S74ZNS0WXXXXXXX` |
| **POWER_ON** | hours powered on | `18238h (~2.1y)` |
| **WEAR(used)** | SSD life consumed — from SMART counter (lower = better) | `1%` |
| **END(left)** | endurance remaining vs. rated TBW | `98.4%` |
| **WRITTEN** | total written / rated TBW | `9.87TB/600TBW` |
| **BAD** | reallocated sectors (SATA) or grown defects (SAS) | `0` = normal; `>0` = danger |
| **HEALTH** | SMART self-test result | `PASSED`, `FAILED` |
| **POOL** | pool/vdev membership | `tank/raidz1-0`, `rpool/mirror-0` |
| **STATUS** | ZFS vdev state — green ONLINE/AVAIL, yellow DEGRADED/INUSE→bay, red FAULTED/REMOVED | `ONLINE`, `AVAIL`, `INUSE→1:4` |
| **LEVEL** | overall status | see table below |

**LEVEL meanings:**

| level | meaning |
|-------|---------|
| **NORMAL** | healthy, assigned to a pool — no action needed |
| **CONFIG** | healthy but not in any pool — needs assignment (add as spare, or build a pool) |
| **WARNING** | endurance/wear getting low, or vdev DEGRADED — prepare to act soon |
| **CRITICAL** | SMART failed, bad sectors, near-zero endurance, FAULTED/UNAVAIL vdev, or GHOST (OS rejected drive — no `/dev/sdX` node) — act immediately |

---

## 6. All features in watch mode

After `sudo b2ctl watch`, type single-letter commands at `b2ctl>`.

---

### 6.1 `r` — Refresh table

**When to use:** want fresh data without restarting.

```
b2ctl> r
```

Rescans all disks and reprints the table.

---

### 6.2 `a` — Assign a free disk

**When to use:** a disk shows **CONFIG** (free/unassigned) and you want to put it to work.

```
b2ctl> a
    [1] bay 1:7 /dev/sde (Samsung SSD 870, SN S74ZNS0WXXXXXXX)
  assign which #>
```

Pick the disk, then choose an action:

| choice | action | when to use |
|--------|--------|-------------|
| **[1]** Blink LED | LED flickers ~5s | identify the bay before pulling |
| **[2]** Add as hot SPARE | add to pool as spare | pool needs a standby disk |
| **[3]** REPLACE faulted disk | replace a FAULTED/DEGRADED member | pool has a failed disk |
| **[4]** ATTACH as mirror | attach to existing disk as mirror pair | want to add redundancy |
| **[5]** ADD single disk | add as vdev with no redundancy | ⚠ one failure = total loss |
| **[6]** WIPE | clear all labels and data | prepare disk for a new pool |
| **[s]** Skip | do nothing now | decide later |

> ⚠️ Warning: Every destructive action shows a confirmation box with full `/dev/disk/by-id/` paths before executing. Default answer is **N** — pressing Enter without typing cancels safely.

**Example: add as spare**

```
  action> 2
    [1] rpool (ONLINE)
    [2] tank (ONLINE)
  pool #> 2

┌─ CONFIRM OPERATION ─────────────────────────────────────────────────────┐
│ Op:    add_spare                                                          │
│ Disk:  bay 1:7  S74ZNS0WXXXXXXX  AVAILABLE                               │
│ Pool:  tank                                                               │
│                                                                           │
│ Will run:                                                                 │
│   zpool add tank spare                                                    │
│     /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W582280E          │
│                                                                           │
│ Snapshot → /var/log/b2ctl/snapshots/20260617-120011-add_spare.txt        │
└───────────────────────────────────────────────────────────────────────────┘
Proceed? [y/N]> y
✔ added as spare
```

---

### 6.3 `o` — Offload (remove disk from pool)

**When to use:** need to physically pull a disk out of its pool.

```
b2ctl> o
    [1] bay 1:0 /dev/sdf in rpool (vdev mirror-0)
    [2] bay 1:1 /dev/sda in rpool (vdev mirror-0)
    [3] bay 1:4 /dev/sdb in tank (vdev raidz1-0)
    ...
  offload which #>
```

What happens depends on the disk's role:

| disk role | what b2ctl does |
|-----------|----------------|
| **spare** | removes from pool immediately — no resilver needed |
| **mirror member** | detaches the mirror leg immediately (if other legs are ONLINE) |
| **raidz member** | must resilver data to a spare first — takes time |

**Example: offload a raidz disk (needs spare)**

```
  offload which #> 3
  Replace (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) onto spare (1:7)
    Samsung SSD 870 (S74ZNS0W582283V)? [y/N]> y
  ✔ replace started — resilvering onto spare
  resilvering... 45.2% done, ETA 00:03:21
  ✔ resilver completed 100%
  ✔ detached old disk /dev/sdb
  please pull bay 1:4 ... blinking LED
```

---

### 6.4 `s` — Swap worn disk onto spare

**When to use:** a disk is wearing out (WEAR high, END left low) but hasn't failed yet — resilver it onto the hot spare before it dies.

> 💡 Tip: **Difference from offload:** swap trades places (old disk becomes the new spare; spare enters the pool as a member). Offload removes the disk from the pool entirely.

```
b2ctl> s
    [1] (1:0) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
    [2] (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) in tank
    ...
  swap which #> 2
  swap (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) onto spare
    (1:7) Samsung SSD 870 (S74ZNS0W582283V)? [y/N]> y
  ✔ swap started — resilvering onto spare
  ✔ resilver completed 100%
  ✔ detached old disk /dev/sdb
  ✔ (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) is now a hot spare in 'tank'
```

Result: spare enters pool as raidz1 member; worn disk becomes the new spare. Both stay in the chassis — no physical move needed.

---

### 6.5 `d` — Demote mirror member to spare

**When to use:** mirror has more than 2 legs (e.g., 3-way mirror) and you want to pull one leg down to a spare.

```
b2ctl> d
    [1] (1:0) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
    [2] (1:1) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
  demote which #> 2
  demote (1:1) SAMSUNG MZ7LH1T9 (...) in 'rpool' to a hot spare? [y/N]> y
  ✔ demoted to spare
```

> ⚠️ Warning: b2ctl refuses if detaching would leave a vdev with only one member (no redundancy).

---

### 6.6 `n` — Create new pool

**When to use:** have free disks and want a new ZFS pool.

```
b2ctl> n
    [1] /dev/sdb (bay 1:4)
    [2] /dev/sdc (bay 1:5)
    [3] /dev/sdd (bay 1:6)
  pick disks (space-separated #)> 1 2 3
  pool name> backup
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]> raidz1
  create pool 'backup' (raidz1) with 3 disks? [y/N]> y
  ✔ pool created
```

**RAID types:**

| type | minimum disks | tolerates failures | usable space |
|------|--------------|-------------------|--------------|
| **stripe** | 1 | 0 — one failure = total data loss | 100% |
| **mirror** | 2 | 1 | 50% |
| **raid10** | 4 (even) | 1 per mirror pair | 50% (fast resilver, best random IOPS) |
| **raidz1** | 2 (recommend 3+) | 1 | (N-1)/N |
| **raidz2** | 4 | 2 | (N-2)/N |

> **raid10** = stripe of mirrors. Pick an even number of disks; b2ctl pairs them
> (`mirror d1 d2 mirror d3 d4 …`) and shows the pairs before you confirm. From the
> CLI: `b2ctl create --raid10`.

> ⚠️ Warning: If disks have existing labels or data, b2ctl warns and asks to wipe first.

---

### 6.7 `e` — Extend a pool (L2ARC cache / SLOG log)

**When to use:** speed up an existing pool with a read cache (L2ARC) or a
sync-write log (SLOG), as in the storage-box runbook.

```
b2ctl> e
  [1] add L2ARC cache (read cache; loss = harmless)
  [2] add SLOG log   (sync-write accel; mirror + PLP recommended)
  [3] remove a cache/log device
  action> 2
    [1] /dev/sdg (bay 1:8)
    [2] /dev/sdh (bay 1:9)
  pick disk(s) (space-separated #)> 1 2
  [!] ensure the SSD(s) have Power-Loss Protection (PLP).
  add SLOG (mirror) to 'tank'? [y/N]> y
  ✔ SLOG added
```

- **L2ARC cache** — a read cache on a fast SSD/NVMe. Losing it only costs a cache
  miss, so it is never mirrored. Helps only when your working set is larger than RAM.
- **SLOG log** — accelerates **synchronous** writes (e.g. NFS `sync`). Pick **two**
  disks for a mirror (a lone log device can lose in-flight writes), and use SSDs
  with **Power-Loss Protection (PLP)**. b2ctl warns if you choose a single device.
- CLI: `b2ctl cache-add|cache-rm|log-add|log-rm <pool> <dev…>`.

---

### 6.8 `b` — Burn-in a disk (vet before pooling)

**When to use:** before trusting a new or second-hand disk, run a SMART long
self-test (optionally a full read-surface scan) and get a PASS/WARN/FAIL verdict.

```
b2ctl> b
    [1] /dev/sdg (bay 1:8) Samsung SSD 870 EVO 1TB
  burn in which #> 1
  also run a full read-surface scan (slow, read-only)? [y/N]> n
  self-test /dev/sdg: [####################] 100%
  ✔ self-test finished on /dev/sdg
  [PASS] /dev/sdg
  ✔ safe to add to a pool.
```

- **PASS** — clean. **WARN** — usable but aged (power-on hours > 40000, or grown
  defects): use as lower-priority / not paired with another old disk. **FAIL** —
  uncorrected errors or a failed self-test: do not pool it.
- Read-only: the only actions are the self-test trigger and (optionally) a
  read-only `badblocks` scan — your data/disk is never written.
- CLI: `b2ctl burnin <bay|dev> [--scan] [--short]`.

---

### 6.9 `t` — Toggle dry-run mode

**When to use:** want to see exactly what commands would run without making any changes — for learning, rehearsing, or verifying before a real operation.

```
b2ctl> t
[DRY-RUN] enabled — write commands will be printed, not executed
b2ctl> s
  swap (1:4) Samsung SSD 870 (...) onto spare (1:7)? [y/N]> y
  [DRY-RUN] would run: zpool replace tank
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W...
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W582283V
b2ctl> t
[DRY-RUN] disabled — back to live mode
```

While dry-run is active, write commands (`zpool`, `wipefs`, `sgdisk`, `dd`) print
`[DRY-RUN] would run:` instead of executing. Read commands (SMART reads, pool
status) still execute so you see real disk state.

Also available as a startup flag: `sudo b2ctl --dry-run watch`

---

### 6.10 `l` — Locate (blink LED)

**When to use:** need to confirm which physical bay a disk occupies before pulling it.

```
b2ctl> l
  locate which (bay/serial/sdX)> sdc
  blinking /dev/sdc for 5s ...
  ✔ done
```

Accepts three identifier formats:
- **Bay number:** `1:4`
- **Serial:** `S74ZNS0WXXXXXXX`
- **Device name:** `sdc` or `/dev/sdc`

The bay's activity LED blinks for ~5 seconds then stops automatically.

> 💡 Tip: Always use `l` before physically pulling a disk — bay numbers may be cosmetically scrambled (see §8).

---

### 6.11 `q` — Quit

```
b2ctl> q
bye
```

---

### 6.12 Hot-plug (automatic detection)

**Inserting a disk:**

b2ctl detects it within ~2 seconds and shows a panel:

<details>
<summary>📋 View New Disk Detection Panel</summary>

<pre>
╔══ NEW DISK DETECTED: /dev/sdg ══════════════════════════════════
  device : /dev/sdg  (/dev/disk/by-id/ata-Samsung_SSD_870...)
  model  : Samsung SSD 870   SN S74ZNS0WXXXXXXX
  bay    : 1:3   size 1.0T   SAS   SSD
  health : PASSED   wear 0% used   endurance 100.0% left
╚══════════════════════════════════════════════════════════════════

  Disk /dev/disk/by-id/ata-Samsung_SSD_870... is free.
  What do you want to do with it?
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    [3] REPLACE a degraded/faulted disk in a pool
    [4] ATTACH to an existing disk (convert to/expand mirror)
    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)
    [6] WIPE it blank (for a new pool)
    [s] skip / decide later
  action&gt;
</pre>
</details>

**Removing a disk:**

```
■ disk removed: /dev/sdc
  current pool health:
Pools:
  rpool   952G   4.83G  free=947G   ONLINE    cap=0%
  tank    2.72T  1.72G  free=2.72T  DEGRADED  cap=0%    <-- not ONLINE
```

> ⚠️ Warning: If a pool goes DEGRADED after a removal, replace the missing disk promptly.

---

## 7. Safety features

b2ctl records every destructive operation and gives you the tools to verify, preview, and reverse what it does.

### 7.1 Dry-run mode

Preview any operation without changing anything:

```bash
# Startup flag — dry-run for entire session
sudo b2ctl --dry-run watch

# Or toggle inside watch
b2ctl> t
```

Write commands (`zpool`, `wipefs`, `sgdisk`, `dd`) print `[DRY-RUN] would run:`
and do nothing. Read commands still run so you see real disk state.

### 7.2 Enhanced confirmation dialog

Every destructive action shows a box with the **full `/dev/disk/by-id/` path**
before executing — you can verify the exact device before confirming:

```
┌─ CONFIRM OPERATION ─────────────────────────────────────────────────────┐
│ Op:    replace                                                            │
│ From:  bay 1:4  S74ZNS0WXXXXXXX  ONLINE  (tank/raidz1-0)                │
│ To:    bay 1:7  S8ABCXXXXXXXX    AVAILABLE                               │
│ Pool:  tank/raidz1-0                                                      │
│                                                                           │
│ Will run:                                                                 │
│   zpool replace tank                                                      │
│     /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W...             │
│     /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S8ABC123...             │
│                                                                           │
│ Snapshot → /var/log/b2ctl/snapshots/20260617-143022-replace.txt         │
└───────────────────────────────────────────────────────────────────────────┘
Proceed? [y/N]:
```

Default is **N** — pressing Enter without typing `y` cancels safely.

### 7.3 Pre-op snapshots

Before any write operation, b2ctl captures the state of the pool and the
affected disk, saved to `/var/log/b2ctl/snapshots/<op_id>.txt`:

- `zpool status <pool>`
- `zpool list -v`
- `zfs list`
- `smartctl -a <dev>` for the affected disk

The snapshot path is shown in the confirmation dialog and in `b2ctl log`.

### 7.4 Audit trail

Every operation is recorded in `/var/log/b2ctl/ops.jsonl`. View with:

```bash
b2ctl log             # last 20 operations
b2ctl log --last 50   # last 50
```

Output:

```
OP_ID                       OP        BAY  SERIAL            POOL  STATUS  STARTED
20260617-143022-replace     replace   1:4  S74ZNS0WXXXXXXX   tank  ok      2026-06-17 14:30:22
20260617-120011-add_spare   add_spare 1:7  S8ABCXXXXXXXX     tank  ok      2026-06-17 12:00:11
```

### 7.5 Rollback hints

After each operation, b2ctl prints the command to undo it:

```
✔ replace started — resilvering
  Rollback if needed: zpool replace tank /dev/disk/by-id/<new> /dev/disk/by-id/<old>
```

To execute a rollback:

```bash
b2ctl rollback 20260617-143022-replace
```

b2ctl shows a confirmation dialog with the rollback command and executes on `y`.
The rollback itself is written to the audit log.

**Reversibility:**

| operation | reversible? |
|-----------|------------|
| offline | yes — `zpool online <pool> <dev>` |
| add spare | yes — `zpool remove <pool> <dev>` |
| replace (resilver in progress) | yes — swap back |
| replace (resilver complete) | no |
| demote mirror to spare | yes — `zpool attach <pool> <remaining> <demoted>` |
| create pool | yes — `zpool destroy <pool>` ⚠ destroys all data |
| wipe (`wipefs`/`sgdisk`) | **no — permanent** |

### 7.6 Post-op verification

After each operation completes, b2ctl re-scans the pool to confirm the expected
state was reached. If something looks wrong:

```
⚠ Post-op check FAILED: disk wwn-0x... not found in tank/raidz1-0
  Expected state not reached. See snapshot:
  /var/log/b2ctl/snapshots/20260617-143022-replace.txt
  Run: b2ctl rollback 20260617-143022-replace
```

---

## 8. Warnings

### Bay numbers may be scrambled

On this Dell 12G backplane in IT mode, the controller reports scrambled slot
numbers (known issue — the Dell slot-translation map is absent in LSI
firmware). b2ctl corrects them via `bay_map.json`. The numbers are display-only;
every action keys off the disk **serial**, not the bay. Always use `l` (locate)
to blink the bay and confirm before pulling.

### Mixing SAS and SATA drives

Mixing a SAS drive as a hot spare into an all-SATA pool is technically allowed
in IT mode, but reformat enterprise SAS drives to 512-byte sectors first and
test on a spare bay. When in doubt, match the existing drive type.

### resilver takes time

ZFS resilver time depends on how much data is in the pool. Do not power off or
pull disks during a resilver. Wait for `zpool status` to show `resilvered with
0 errors` before touching anything.

### Confirmation default is N

Every destructive prompt defaults to **N**. Pressing Enter without typing cancels
safely. `wipe` prompts twice.

### rpool boot-disk replacement

Replacing a disk in `rpool` (the Proxmox boot pool) — b2ctl resilvered the ZFS
side, but you must also run `proxmox-boot-tool format/init` on the new disk's
ESP partition **manually**. b2ctl does not touch Proxmox boot config.

---

## 9. 🚀 Quick Reference / Cheat Sheet

### One-shot CLI commands

| command | what it does |
|---------|--------------|
| `b2ctl status` | health table + pool summary + details block |
| `b2ctl status --locate` | same + blink LEDs on WARNING/CRITICAL disks |
| `b2ctl status --json` | JSON output |
| `b2ctl --dry-run <cmd>` | preview what commands would run — no writes |
| `b2ctl locate <bay\|serial\|dev> [secs]` | blink one disk's LED (~5s) |
| `b2ctl offload` | guided: safely remove an in-pool disk |
| `b2ctl replace` | guided: replace a disk onto a spare |
| `b2ctl swap` | guided: swap a worn disk onto an existing spare |
| `b2ctl demote` | guided: demote a mirror leg to a spare |
| `b2ctl create` | guided: create a new ZFS pool |
| `b2ctl log [--last N]` | show last N ops from audit trail (default 20) |
| `b2ctl rollback <op_id>` | roll back a previous operation (with confirmation) |
| `b2ctl check` | verify tools, show backend detected, config file status |
| `b2ctl config show` | print current effective config as JSON |
| `b2ctl config init` | write `/etc/b2ctl/config.json` with auto-detected defaults |
| `b2ctl version` | print version string |
| `sudo b2ctl install` | download and install sas2ircu, storcli, perccli from Google Drive (skips already-installed) |
| `sudo b2ctl install --tool sas2ircu` | install only one tool (`sas2ircu`, `storcli`, or `perccli`) |
| `b2ctl update` | validate config, check tool paths, report missing tools |
| `sudo b2ctl update --export-bay-map` | copy bundled bay_map.json to /etc/b2ctl/ and update config |

### Watch mode keys (at `b2ctl>`)

| key | action |
|-----|--------|
| `r` | refresh the health table |
| `a` | assign a free disk to a pool |
| `o` | offload (remove) a disk from a pool |
| `s` | swap a worn in-pool disk onto a spare |
| `d` | demote a mirror member to a spare |
| `n` | create a new pool |
| `t` | toggle dry-run mode on/off |
| `l` | blink one disk's LED (~5s) by bay/serial/device |
| `q` | quit |

### New disk / assign menu choices

| choice | action |
|--------|--------|
| `1` | blink LED (identify bay before pulling) |
| `2` | add to pool as hot spare |
| `3` | replace a FAULTED/DEGRADED disk in a pool |
| `4` | attach to existing disk (expand mirror) |
| `5` | add as single vdev — no redundancy ⚠ |
| `6` | wipe all labels and data |
| `s` | skip — decide later; come back with `a` |

### Audit trail

| command | example |
|---------|---------|
| `b2ctl log` | show last 20 ops |
| `b2ctl log --last N` | `b2ctl log --last 50` |
| `b2ctl rollback <op_id>` | `b2ctl rollback 20260617-143022-replace` |

### Configuration

b2ctl works out of the box with no config file. A file at `/etc/b2ctl/config.json`
lets you override tool paths and force a backend mode.

```bash
sudo b2ctl config init   # write /etc/b2ctl/config.json with auto-detected defaults
sudo b2ctl config show   # print current effective config
sudo b2ctl check         # verify tools, detect backend, show config path
```

`b2ctl check` is the first thing to run when something looks wrong — it shows
which tools were found, which backend was detected, and whether the config file
exists.

---

> 💡 Tip: **Not sure what to do?** Press `s` (skip) — nothing changes. Come back with
> `a` (assign) when you're ready.

---

## RAID-mode boxes (Dell PERC, e.g. R640 / H730P)

b2ctl works on servers where the PERC runs **hardware RAID** (not crossflashed).
Install it for that box and it switches to RAID mode:

```
b2ctl install --perc      # installs perccli, sets controller.mode=raid
b2ctl install --flash     # (the IT/HBA boxes) installs sas2ircu, mode=it
```

`b2ctl status` then shows the **physical drives behind the RAID volume** (read
through the controller), with the `POOL/ARRAY` column marking each disk:

- `HW:vd0/raid1` — member of a **hardware** RAID volume (the PERC owns it)
- `SW:tank/raidz1-0` — member of a **software** RAID (ZFS pool)
- `-` — direct/unassigned (e.g. an NVMe, a JBOD disk)

A separate **RAID volumes (hardware)** table lists each volume (level, state,
size, member count).

### Replacing a failed RAID disk

```
b2ctl raid-replace          # pick the member, or: b2ctl raid-replace 32:0
```

It fails the drive out, **lights its bay LED**, waits for you to pull it and
insert the replacement, then watches the controller **rebuild** with a live
progress bar. Related: `b2ctl raid-offline <bay>` (just fail it out + LED),
`b2ctl locate <bay> on`, and (destructive, double-confirmed) `b2ctl raid-create
--level raid1 --drives 32:0,32:1` / `b2ctl raid-del <vd>`.

> Note: on a 2×M.2 NVMe card, if only one NVMe shows, enable **PCIe bifurcation
> (x4x4)** for that slot in the BIOS — that is a hardware setting, not b2ctl.

---

## Creating a ZFS pool (`[n]ew-pool`)

After you pick disks, name, and raid level, b2ctl asks for each pool property
with an SSD-optimal default — **press Enter to accept**, or type to override
(`ashift`, `compression`, `atime`, `xattr`, `dnodesize`, `acltype`,
`recordsize`). `recordsize` is workload-tunable (128K general, DB 16K, media 1M,
VM 64–128K) and can be changed per-dataset later.

**autotrim** is a choice:
- **off (Monthly)** *(recommended)* — installs a monthly maintenance schedule for
  the pool: `zpool trim` on the 1st Sunday + `zpool scrub` on the 2nd Sunday
  (cron at `/etc/cron.d/b2ctl-<pool>`).
- **on** — continuous trimming (ZFS handles it); no cron.

## Destroying a ZFS pool (`[x]` or `b2ctl destroy <pool>`)

Destroys the pool with `zpool destroy` — **ALL DATA IS LOST**. You must confirm
and then **type the pool name** to proceed. b2ctl also removes that pool's
maintenance cron. (If you destroy a pool yourself with `zpool destroy`, b2ctl
cleans up the leftover cron the next time you open `b2ctl watch`.)

## Replacing a failing disk with NO spare (`[o]ffload`)

raidz1 (and mirrors) keep running with one disk missing. If a disk is failing,
every bay is full, and there is **no hot spare**, `[o]ffload` it:

1. b2ctl checks the pool is **fully redundant right now** (all other members
   healthy). If not, it **refuses** — offlining a second disk could fail the pool.
2. It runs `zpool offline` — the pool goes **DEGRADED but stays online** (no
   redundancy until you finish), and lights the bay LED.
3. **Pull that bay and insert the new disk into the SAME bay**, then press Enter.
4. b2ctl `zpool replace`s the new disk in and shows the resilver progress; when
   it finishes the pool is back to **ONLINE**.

> ⚠️ While DEGRADED / resilvering there is no redundancy — a second disk failure
> in that window loses data. b2ctl won't let you offline a second disk meanwhile.

## Bay labels — `bay_map.json`

`/etc/b2ctl/bay_map.json` is a list of **panels** describing your chassis:

- **front** (`type: sas`) — the backplane behind the PERC (RAID) or the
  PERC-flashed `sas2ircu` HBA. Bays are `enc:slot`. If the controller reports
  scrambled slots, set `reverse_slots`+`slots_per_enclosure`, or an explicit
  `map` (`{"32:0": "32:7"}`). Calibrate with `b2ctl locate <serial>`.
- **back** (`type: nvme`) — a PCIe/M.2 SSD enclosure (one or more). NVMe has no
  enclosure:slot, so its BAY shows the **PCIe address** (e.g. `d8:00.0`) until you
  relabel it. Each map entry can key on any of three identifiers (**precedence
  by-id > serial > bdf**):

```json
{ "panel": "back", "type": "nvme",
  "map": [ {"by-id":  "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7..", "bay": "PCIe2:0"},
           {"serial": "S7XXNS0W123", "bay": "PCIe2:1"},
           {"bdf":    "d8:00.0",     "bay": "PCIe2:2"} ] }
```

- **`serial`** is the easiest — copy it straight from the **SERIAL** column of
  `b2ctl status`.
- **`by-id`** is a substring of the drive's `/dev/disk/by-id/nvme-<model>_<serial>`
  link (run `ls /dev/disk/by-id/ | grep nvme`); it survives the card moving slots.
- **`bdf`** still works — find it in `b2ctl status` (the BAY) or `cat
  /sys/class/nvme/nvme0/address`.

> NVMe drives appear in the table and in `[a]ssign` / `[b]urnin` like any other
> disk — only the BAY column differs (no enclosure:slot). The bay label is
> display-only; getting it wrong is cosmetic, never dangerous.
