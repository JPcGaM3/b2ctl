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
system. A plain `./install.sh` installs **only b2ctl** — no downloads, no `apt`,
no internet needed.

**The four install forms (same for `./install.sh` and `b2ctl install`):**

| command | what it installs |
|---------|------------------|
| `./install.sh` · `b2ctl install` | **only b2ctl** (package + launcher); no tools, no download |
| `./install.sh --with-tools` · `b2ctl install --with-tools` | b2ctl **+ both** tools (sas2ircu + perccli) from Google Drive |
| `./install.sh --perc` · `b2ctl install --perc` | b2ctl + **perccli** + `controller.mode=raid` (Dell PERC RAID box) |
| `./install.sh --flash` · `b2ctl install --flash` | b2ctl + **sas2ircu** + `controller.mode=it` (crossflashed HBA box) |

- `./install.sh` deploys the package; `b2ctl install` (no flag) just reports tool
  status + the current mode (b2ctl is already installed) — otherwise the flags
  behave identically on both.
- `--with-tools` **downloads** the tool archives from Google Drive, extracts the
  binaries to `/usr/sbin/`, and installs their apt prerequisites
  (`libc6-i386` for the 32-bit sas2ircu, `alien` for perccli). Downloads are
  deleted on completion; each tool installs independently (`[✗]` + continue on
  failure). Requires `curl` or `wget` (both default on Proxmox VE).
- Pick `--perc` **or** `--flash` to match your hardware — it installs just that
  backend's tool and sets the controller mode in `/etc/b2ctl/config.json`.

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
Storage summary:
  TYPE NAME            LEVEL    STATE     SIZE      USED      FREE
  SW   rpool           mirror   ONLINE    952G      4.83G     947G
  SW   tank            raidz1   ONLINE    2.72T     1.72G     2.72T
[OK] all disks healthy and assigned

[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [t]oggle-dryrun  [n]ew-pool  [e]xtend  [b]urnin  [u]dev-rescue  [x]destroy-pool  [l]ocate  [q]uit   (or hot-plug)
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

### SAS disks show `NOREAD` (or `status` is slow) on a RAID box

On a PERC box, SMART for every disk is read through the controller
(`smartctl -d megaraid`). If many disks are read at once the controller can
saturate and slow/old disks miss the read timeout → they show **`NOREAD` /
"SMART unreadable"**, and the scan gets slow. Tune it in `/etc/b2ctl/config.json`:

```json
{ "smart": { "timeout": 25, "megaraid_workers": 2 } }
```

`timeout` = seconds per disk (raise it for slow disks); `megaraid_workers` =
how many disks are read at once through the controller (lower it if it saturates).
A disk that stays `NOREAD` after this is likely genuinely failing — check its bay.

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
| **BAD** | reallocated sectors / grown defects | `0` = normal; on an **SSD/NVMe** any `>0` is CRITICAL; on an **HDD** a few are tolerated (see LEVEL below) |
| **HEALTH** | SMART self-test result | `PASSED`, `FAILED` |
| **POOL** | pool/vdev membership | `tank/raidz1-0`, `rpool/mirror-0` |
| **STATUS** | ZFS vdev state — green ONLINE/AVAIL, yellow DEGRADED/INUSE→bay, red FAULTED/REMOVED | `ONLINE`, `AVAIL`, `INUSE→1:4` |
| **LEVEL** | overall status | see table below |

**LEVEL meanings:**

| level | meaning |
|-------|---------|
| **NORMAL** | healthy, assigned to a pool — no action needed |
| **CONFIG** | healthy but not in any pool — needs assignment (add as spare, or build a pool) |
| **WARNING** | endurance/wear getting low, vdev DEGRADED, or an **HDD** with a moderate defect count (`>50` grown defects, or any pending sector) — prepare to act soon |
| **CRITICAL** | SMART failed, near-zero endurance, FAULTED/UNAVAIL vdev, GHOST (OS rejected drive), **any** bad sector on an SSD/NVMe, or an **HDD** with heavy defects (`>200`) or uncorrectable errors — act immediately |

**Bad-sector grading is type-aware and tunable (v0.13.0).** SSD/NVMe are strict
(any reallocated/pending/uncorrectable sector → CRITICAL); HDDs tolerate stable,
already-remapped grown defects (`>50 → WARNING`, `>200 → CRITICAL`). Adjust the
bands per type in `/etc/b2ctl/config.json` under `health` — see the DevOps guide.
A threshold set to `"N/A"` turns that check off.

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
  assign which #> (space-separated for batch)
```

The list gathers **three** kinds of unassigned disk:

- a normal free disk → `[1] bay 1:7 /dev/sde (Samsung SSD 870, SN …)` — opens the
  action menu below.
- a **`[GHOST]`** disk (OS-rejected, no `/dev` node) → `[1] [GHOST] bay 1:4 (SN …)
  — needs wipe` — routes to a wipe/rescue flow (also see `[u]dev-rescue`).
- a **PERC Unconfigured-Good** disk (RAID-mode boxes only) → `[1] bay 32:4
  (Samsung …, SN …) (PERC Unconfigured-Good)` — routes to the hardware-RAID menu
  (set JBOD for ZFS, create a volume, or add as a hot spare).

**Multi-select / batch (v0.11.0).** Pick **several** disks at once,
space-separated — the same way as `[n]ew-pool` and `[b]urnin`:

```
  assign which #> (space-separated for batch) 3 4 5
```

- **One** pick opens the per-disk action menu below (unchanged — it keeps
  REPLACE / ATTACH, which act on a single disk).
- **Two or more** picks open a **batch** menu: choose one action and it applies
  to **every** selected disk with a single confirm. The selection must be a
  single disk **type** — mixing types (e.g. a PERC drive + a free NVMe) is
  refused with a per-type count, so you pick one type at a time.
  - **PERC Unconfigured-Good** → `[1]` blink all · `[2]` **set JBOD on all** (the
    common "prep N disks for ZFS" case) · `[3]` create **one** hardware RAID
    volume from all · `[4]` add all as hot spares.
  - **Free (ZFS-poolable)** → `[1]` blink all · `[2]` add all to a pool as hot
    SPARE · `[3]` WIPE all blank.

Pick a normal free disk, then choose an action:

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
  [4] replace/repair a degraded cache/log device
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

**`[4]` replace/repair a degraded cache/log device (v0.14.0).** When a cache disk
or one leg of a mirrored SLOG dies, pull it, insert a new disk, then run `[e] → [4]`.
b2ctl lists the degraded cache/log leaves; pick the dead one and the replacement
disk, and it repairs by type:

```
b2ctl> e
  action> 4
    [1] SLOG mirror-leg  /dev/disk/by-id/ata-LOGB  FAULTED
  repair which #> 1
    [1] /dev/sdh (bay 1:9)
  replacement disk #> 1
  repair log on 'tank': replace ata-LOGB -> ata-NEW? [y/N]> y
  ✔ replace started — resilvering
  ✔ resilver completed
```

- **Cache** is repaired by **remove + add** (L2ARC can't be replaced; loss is harmless).
- A **SLOG mirror leg** is repaired with **`zpool replace`** (a brief resilver, live
  progress bar). This is safer than detaching-and-reattaching: `replace` never asks
  you to hand-pick a device to *destroy*, so a mistake can't kill the surviving leg.
- A **single (non-mirrored) SLOG** that is fully gone is repaired by remove + add.
- CLI: `b2ctl cache-replace <pool> <old> <new>` · `b2ctl log-replace <pool> <old> <new>`.

---

### 6.8 `b` — Burn-in disk(s) (vet before pooling)

**When to use:** before trusting new or second-hand disks, run a SMART long
self-test (optionally a full read-surface scan) on **several disks at once** and
get a PASS/WARN/FAIL verdict per disk.

**Multi-select + background (v0.10.0).** Pick disks the same way as `[n]ew-pool`
(space-separated), confirm, then choose whether to also run a surface scan. The
self-tests run on the drives' own firmware and the scans run as detached
processes, so a **live view** shows a progress bar + estimated time remaining for
each disk — and you can **leave it running** (Ctrl-C) and come back later.

```
b2ctl> b
    [1] /dev/sdb (bay 32:4) Samsung SSD 870 EVO 1TB
    [2] /dev/sda (bay 32:5) Samsung SSD 870 EVO 1TB
    [3] /dev/nvme0n1 (bay PCIe2:0) Samsung 990 EVO Plus
  burn in which #> (space-separated) 1 2 3
  burn-in 3 disk(s) (long self-test)? [y/N]> y
  also run a full read-surface scan (badblocks, read-only, hours)? [y/N]> y
  live burn-in — Ctrl-C to leave running in background

 BAY     DISK      SELF-TEST                     SURFACE SCAN (badblocks)
 32:4    sdb       [########------]  62%  ~1h10m  [###-----------]  18%  ~4h30m
 32:5    sda       [##########----]  74%  ~40m    [####----------]  22%  ~4h05m
 PCIe2:0 nvme0     [#############-]  90%  ~8m     n/a
```

- **Leaving & re-attaching:** press **Ctrl-C** to return to the prompt — the tests
  and scans keep running. Press `[b]` again for a menu — **[v]** view the live view,
  **[c]** cancel one disk, **[a]** cancel all, **[n]** start a new burn-in — or run
  `b2ctl burnin --status`; when a disk finishes you'll see its verdict there.
- **Cancelling:** to stop a disk mid-burn-in (e.g. a dying disk holding up the
  batch), use `[b]` → `[c]`/`[a]`, or `b2ctl burnin --cancel <bay|dev …>` /
  `b2ctl burnin --cancel-all`. It aborts the self-test and stops the read-only
  scan — nothing is written, and the disk can be re-burned later.
- While a self-test runs, `b2ctl status` shows `TEST xx%` in that disk's STATUS
  column (and the details block adds a `self-test running: …%` line).
- **PASS** — clean. **WARN** — usable but aged (power-on hours > 40000, grown
  defects, or the surface scan found bad blocks): use as lower-priority. **FAIL** —
  uncorrected errors or a failed self-test: do not pool it.
- Read-only: the only actions are the self-test trigger and (optionally) a
  read-only `badblocks` scan — your data/disk is never written.
- CLI: `b2ctl burnin <bay|dev> [<bay|dev> …] [--scan] [--short]`;
  re-attach with `b2ctl burnin --status`.

---

### 6.9 `u` — Udev-rescue (recover an OS-rejected disk)

**When to use:** a disk is physically present but the OS rejected it — it shows as
a **GHOST** (no `/dev` node). `u` fires `udevadm trigger`/`settle` to try to make
the kernel enumerate it. Read-only/diagnostic — it does not touch disk contents.

```
b2ctl> u
    ghost bay 1:4 serial S74ZNS0WXXXXXXX
  run udevadm trigger/settle to rescue 1 ghost disk(s)? [y/N]> y
  ✔ rescued 1 disk(s)
```

If nothing recovers: `no disks recovered — reseat physically or wipe via
[a]ssign`. When there are no ghosts: `no ghost (OS-rejected) disks to rescue`.
(Aliases: `u` or `rescue`.)

---

### 6.10 `x` — Destroy a pool

**When to use:** permanently delete a ZFS pool. **All data is lost** — guarded by a
double confirm plus typing the pool name. (Deep-dive: the **Destroying a ZFS pool**
section near the end of this guide.)

```
b2ctl> x
    [1] rpool (952G, ONLINE)
    [2] tank (2.72T, ONLINE)
  destroy which #> 2
  members:
    - (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0WXXXXXXX)
    - (1:5) Samsung SSD 870 EVO 1TB (S74ZNS0WXXXXXXX)
    ...
  [!] destroying 'tank' ERASES ALL DATA on it. This cannot be undone.
  destroy pool 'tank'? [y/N]> y
  type the pool name 'tank' to confirm> tank
  ✔ pool 'tank' destroyed; cron removed
```

> ⚠️ Two gates: the `[y/N]` **and** re-typing the exact pool name. b2ctl also
> removes that pool's maintenance cron. (Bare key `x` only — no word alias.)

---

### 6.11 `t` — Toggle dry-run mode

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

### 6.12 `l` — Locate (blink LED)

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

### 6.13 `q` — Quit

```
b2ctl> q
bye
```

---

### 6.14 Hot-plug (automatic detection)

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

**Which LED?** locate picks the most-dedicated indicator: PERC drives → the
controller's slot LED (`perccli`); raw SATA/SAS → the backplane's dedicated
**locate LED via `ledctl`** if the `ledmon` package is installed (`apt install
ledmon`), otherwise the **`dd` activity-LED** fallback. `b2ctl locate` prints
which it used (`via ledctl` / `via dd` / `via perccli`). Note the locate LED is a
*blink* (SES identify), not solid — no tool can make a healthy drive's LED solid
or fully dark.
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
| `b2ctl install` | report tool + mode status (no download — same as `./install.sh`) |
| `sudo b2ctl install --with-tools` | download + install **both** tools (sas2ircu + perccli) |
| `sudo b2ctl install --perc` / `--flash` | install that backend's tool + set the mode (raid/it) |
| `sudo b2ctl install --tool sas2ircu` | install only one tool (`sas2ircu` or `perccli`) |
| `b2ctl update` | validate config; **as root** also sync `bay_map.json` + `ssd_spec.json` into `/etc/b2ctl/` and bind them in config (preserves files you edited) |
| `sudo b2ctl update --force` | overwrite operator-customized `/etc/b2ctl/` files (keeps a `.bak`) |
| `sudo b2ctl update --export-bay-map` | deprecated alias of `--force` (plain `update` now syncs both files) |

### Watch mode keys (at `b2ctl>`)

| key | action |
|-----|--------|
| `r` | refresh the health table |
| `a` | assign a free disk to a pool (also lists GHOST + PERC-UG disks) |
| `o` | offload (remove) a disk from a pool |
| `s` | swap a worn in-pool disk onto a spare |
| `d` | demote a mirror member to a spare |
| `t` | toggle dry-run mode on/off |
| `n` | create a new pool |
| `e` | extend a pool — add/remove/**repair** L2ARC cache or SLOG log |
| `b` | burn-in disk(s) — multi-select self-test (+ optional badblocks) |
| `u` | udev-rescue an OS-rejected (GHOST) disk |
| `x` | destroy a pool (double-confirm + type the pool name) |
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

On a box that has **both** kinds, the disk table groups them — a
`--- Hardware (PERC RAID) ---` block on top, `--- Software (ZFS) ---` below — and
the summary becomes one **Storage summary** table, hardware rows above software:

```
Storage summary:
  TYPE NAME            LEVEL    STATE     SIZE      USED      FREE
  HW   MainSSD         raid1    Optl      640.0 GB  12.0G     628.0G
  SW   tank            mirror   ONLINE    928G      598M      927G
```

- **NAME** — the hardware volume's name (e.g. `MainSSD`) / the ZFS pool name.
- **USED/FREE** — for software, from the pool; for hardware, read from the
  volume's **mounted filesystem** via `lsblk`. If the hardware volume is raw or
  not mounted, USED/FREE show `-` (there's no filesystem to measure).

### Replacing a failed RAID disk

```
b2ctl raid-replace          # pick the member, or: b2ctl raid-replace 32:0
```

It fails the drive out, **lights its bay LED**, waits for you to pull it and
insert the replacement, then watches the controller **rebuild** with a live
progress bar. Related: `b2ctl raid-offline <bay>` (just fail it out + LED),
`b2ctl locate <bay|serial|dev> [secs]` (a timed blink — the LED is always
turned back off; there is no latched `on`/`off` form, by design), and
(destructive, double-confirmed) `b2ctl raid-create
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

**autotrim** is a choice — note that **the monthly SCRUB always runs either way**
(scrub is what checks data integrity and self-heals; it doesn't depend on trim).
b2ctl schedules maintenance with the distro's own **systemd timers** (from
`zfsutils-linux`), one per pool:
- **off** *(recommended)* — enables `zfs-scrub-monthly@<pool>.timer` **and**
  `zfs-trim-monthly@<pool>.timer`.
- **on** — continuous trimming (ZFS handles it); enables the scrub timer only.

Check what's scheduled: `systemctl list-timers | grep zfs`. If the distro doesn't
ship the timer units, b2ctl warns "scrub timer NOT scheduled" and installs nothing —
install `zfsutils-linux` or enable a timer yourself.

Debian/Proxmox also has a built-in cron that scrubs *all* pools monthly (property
`org.debian:periodic-scrub`). To avoid scrubbing twice, when b2ctl enables a pool's
timer it sets that pool's `org.debian:periodic-scrub=disable` (and `…periodic-trim`
for the trim timer) — so your per-pool timer becomes the single schedule.

## Destroying a ZFS pool (`[x]` or `b2ctl destroy <pool>`)

Destroys the pool with `zpool destroy` — **ALL DATA IS LOST**. You must confirm
and then **type the pool name** to proceed. b2ctl also disables that pool's
maintenance timers. (If you destroy a pool yourself with `zpool destroy`, b2ctl
disables the leftover timers the next time you open `b2ctl watch`.)

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

### Make your bay labels apply from every directory

Edit the bay_map in the **`/etc/b2ctl/` copy** — not the one in the source
checkout. To create/refresh it, run **`b2ctl update`** as root:

```bash
sudo b2ctl update          # creates /etc/b2ctl/bay_map.json + ssd_spec.json, binds them in config
sudo nano /etc/b2ctl/bay_map.json   # add your NVMe serial -> bay entries
b2ctl watch                # now maps correctly from ANY directory
```

`b2ctl update` copies the bundled `bay_map.json` and `ssd_spec.json` (the SSD
TBW table) into `/etc/b2ctl/` and records their paths in the config, so b2ctl
always loads the same files no matter which directory you run it from. It **will
not overwrite files you edited** — a customized file shows `customized-kept`
(use `sudo b2ctl update --force` to overwrite, which first saves a `.bak`).

> **Why this matters:** before v0.8.5, running `b2ctl` from inside the source
> checkout could load that copy's `bay_map.json` instead of the installed one, so
> the mapping seemed to change with the current directory. The installer now runs
> the installed copy regardless of directory (`PYTHONSAFEPATH`), and `b2ctl
> update` puts the editable files in one fixed place (`/etc/b2ctl/`).
