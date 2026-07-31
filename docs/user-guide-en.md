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

> 📌 Note: b2ctl is one tool with two co-equal, auto-detected backends —
> **IT/HBA mode** (`sas2ircu`, raw disks, ZFS lifecycle) and **RAID mode**
> (`perccli` + `smartctl -d megaraid` passthrough, hardware RAID). It picks the
> right one per box — leave `controller.mode` on `"auto"` and let it, including on
> a **Dell HBA330 / H330**, where `perccli` sees the card but the OS still owns the
> disks (v0.19.0 — see *HBA330 / H330 boxes* near the end of this guide). (Only the
> old `storcli` tool was dropped — it was blind to a PERC and caused false detection.)

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

> **⚠ Tool downloads now REFUSE without a checksum (v0.24.3).** `sas2ircu` and
> `perccli` are fetched from Google Drive and then run **as root** on every
> `b2ctl status`. The checksum table (`installer._SHA256`) shipped **empty**, so
> nothing was ever verified — only "is this file bigger than 1 KB". Both
> `b2ctl install --with-tools` and `./install.sh --perc` now stop with:
>
> ```
> [✗] no pinned SHA-256 for perccli — refusing to download
>     unverified content that will run as root.
> ```
>
> **To fix it properly:** get the archive from a copy you trust, run
> `sha256sum perccli.tar.gz`, and add the digest to `installer._SHA256`. Both
> install paths read that one table, so they cannot drift apart.
>
> **To bootstrap once anyway:** `B2CTL_ALLOW_UNVERIFIED=1 b2ctl install --perc`.
> It says so loudly while it runs. Use it only if you can verify the binary some
> other way afterwards.
>
> Related: b2ctl no longer passes `--scripts` to `alien`, so installing perccli
> no longer executes the vendor RPM's own install scripts as root. Nothing else
> about the install changes — the only artefact b2ctl uses is the `perccli64`
> binary, which `alien -i` extracts on its own.

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

[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [t]oggle-dryrun  [n]ew-pool  [e]xtend  [m]aint  [u]dev-rescue  [x]destroy-pool  [l]ocate  [q]uit   (or hot-plug)
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

### Every drive appears twice, half the rows `NOREAD` (HBA330 / H330 box)

On a Dell box with an **HBA330 Mini / H330** (LSI SAS3008), `b2ctl status` printed
**two rows per physical drive** — 9 drives, 18 rows. The extra rows all shared the
**same DEV** (`/dev/sda`), **SERIAL `N/A`**, **HEALTH `NOREAD`** and **LEVEL
CRITICAL**, each advising "available (Unconfigured Good) — set JBOD for ZFS".

Nothing is wrong with those disks. b2ctl was treating the card as a hardware-RAID
PERC and inventing one phantom row per drive. **Fixed in v0.19.0** — upgrade, then
make sure `controller.mode` is back on `"auto"` (see *HBA330 / H330 boxes* near the
end of this guide). `b2ctl status` then shows exactly one row per drive again.

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
| **HEALTH_CHK** | last completed **long self-test** result from the drive's own log (v0.17.0) | `OK 120hPOH`, `ERR 30hPOH`, `-` |
| **LEVEL** | overall status | see table below |

**HEALTH_CHK is power-on-hours-relative, not a wall-clock date.** The cell shows
`OK` (long self-test passed) or `ERR` (failed) plus how long ago the test ran
measured in **power-on hours** (`hPOH`) — because the drive logs each self-test
against its lifetime hours, not a calendar date. `OK 120hPOH` means "the last long
self-test passed, and the drive has run 120 more hours since". A `-` means no long
self-test is on record. The value reflects the last long test whoever fired it —
the `[m]aint` health-check, or a manual `smartctl -t long` — and it is parsed for
**SATA, SAS and NVMe** alike (v0.18.0 fixed the SAS `Completed` grading). To run
one, see `[m]aint` below.

**The pool summary also gains a `SCRUB` and `TRIM` column (v0.17.0)** showing each
pool's last scrub (read live from `zpool status`) and last trim (from b2ctl's
maintenance history), e.g. `2d ago`. A blank/`-` means none on record — run one
with `[m]aint` or `b2ctl maint scrub|trim <pool>`.

**LEVEL meanings:**

| level | meaning |
|-------|---------|
| **NORMAL** | healthy, assigned to a pool — no action needed |
| **CONFIG** | healthy but not in any pool — needs assignment (add as spare, or build a pool) |
| **WARNING** | endurance/wear getting low, vdev DEGRADED, or an **HDD** with a moderate defect count (`>50` grown defects, or any pending sector) — prepare to act soon |
| **CRITICAL** | SMART failed, near-zero endurance, FAULTED/UNAVAIL vdev, GHOST (OS rejected drive), **any** bad sector on an SSD/NVMe, or an **HDD** with heavy defects (`>200`) or uncorrectable errors — act immediately |

### When b2ctl says "pool membership UNKNOWN" (v0.24.1)

If you ever see this line at the top of `status` or `watch`:

```
⚠ zpool did not answer (...) — pool membership is UNKNOWN.
  Disk assignment and pool creation are disabled until it does.
```

…then `zpool` itself did not respond — it hung, it is missing, or the path in
`/etc/b2ctl/config.json` is wrong. **This is not a disk problem.**

What b2ctl does about it:

- The **disk table still prints** — that table is how you diagnose why ZFS went
  quiet, so you do not lose it.
- The `POOL` column is blank for every disk and each one is graded `CONFIG` with
  *"pool membership UNKNOWN"*. That does **not** mean the disk is free.
- **`[a]ssign`, `[n]ew-pool` and the aux-vdev menus offer nothing.** They refuse
  on purpose: b2ctl will not call a disk "available" on the word of a question
  nobody answered. Before v0.24.1 they listed every live `rpool`/`tank` member as
  a free disk.
- `b2ctl <verb> --json` returns `ok: false` with `error.code: "TOOL_MISSING"`
  rather than an empty pool list, so a script never mistakes it for "no pools".

**What to do:** run `zpool status` by hand. If it hangs, ZFS is stuck (usually a
failing disk or a stalled resilver) — deal with that first. If it says "command
not found", ZFS is not installed or `/sbin` is not mounted. Once `zpool` answers,
press `[r]` in watch and everything comes back.

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
space-separated — the same way as `[n]ew-pool` and `[m]aint` health-check:

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
  size to use per disk (over-provision) [full disk]>
  pool name> backup
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]> raidz1
  ...
  autotrim: [1] off — manual TRIM via [m]aint / `b2ctl maint trim` (recommended)
            [2] on  — zpool autotrim=on (ZFS trims inline)
    choose [1]>
  autoscrub: [1] off — manual scrub via [m]aint / `b2ctl maint scrub` (recommended)
             [2] on  — monthly zfs-scrub timer (self-heals silent bitrot)
    choose [1]>
  ...
  create pool 'backup' (raidz1) with 3 disks (full disk)? [y/N]> y
  ✔ pool created
  ✔ maintenance timers: ...
  [!] autoscrub OFF — no monthly self-heal scheduled for 'backup'; run `b2ctl maint scrub backup` (or [m]aint) periodically
  [!] autotrim OFF — TRIM manually via `b2ctl maint trim backup` (or [m]aint)
```

**`size to use per disk` (over-provision).** Press Enter for the default —
b2ctl hands ZFS the **whole disk** (the idiomatic ZFS layout). Enter a size (e.g.
`32G`, `512G`) and b2ctl instead creates a partition of that size on each disk and
gives ZFS the partition, leaving the rest as SSD spare area. This does **not** make
the pool faster (a correctly-aligned partition performs the same as a whole disk on
Linux ZFS with `ashift=12`); the spare area improves **SSD endurance and
sustained-write consistency**, at the cost of usable capacity. Over-provisioning is
mainly worth it for a dedicated SLOG / L2ARC SSD — see `[e]xtend`.

> ⚠️ **Over-provisioning WIPES each selected disk first (v0.18.0).** `sgdisk`
> places partition 1 at the first free aligned sector, so a stale partition table
> on a used disk would push it past the old partition (or off the end of a small
> drive) — the v0.17.0 `partition failed` bug. b2ctl now clears each disk's GPT
> before it partitions. It prints a WIPE warning naming every disk and asks **one**
> confirm **up front — decline it and nothing is wiped**; then it wipes →
> partitions → hands ZFS the `-part1`. A blank size skips all of this (whole-disk
> path). Same for `b2ctl create --size` and `cache-add`/`log-add --size` from the CLI.

**`autotrim` / `autoscrub` (both default OFF).** Both questions now read
`[1] off` (default) `/ [2] on`, and **OFF means manual-only — no timer is
installed** (v0.18.0):

- **`autotrim off`** (default): TRIM the SSDs by hand with `[m]aint` / `b2ctl maint
  trim <pool>`. **`autotrim on`** sets `zpool autotrim=on` so ZFS trims inline.
  *(This reverses the old behaviour where `autotrim off` scheduled a monthly
  `zfs-trim` timer — there is no trim timer any more.)*
- **`autoscrub off`** (default): a scrub reads every block, verifies checksums, and
  self-heals a redundant pool. With autoscrub off, **manual scrub is the primary
  path** (`[m]aint` / `b2ctl maint scrub <pool>`) — a new pool has *no scheduled
  scrub* unless you pick `[2] on` (a monthly `zfs-scrub-monthly@<pool>.timer`).
  b2ctl prints a reminder and the pool's `SCRUB` column shows how stale the last
  scrub is, so run one periodically.

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
  size to use per device (over-provision) [full disk]> 32G
  SLOG topology:
    [1] mirror  — redundant log (recommended)
    [2] raid10  — stripe of mirrors (even # of disks >= 4)
    [3] single/striped — NO redundancy (log loss can lose sync writes)
    choose [1]> 1
  [!] ensure the SSD(s) have Power-Loss Protection (PLP).
  add SLOG (mirror) to 'tank'? [y/N]> y
  ✔ SLOG added
```

- **L2ARC cache** — a read cache on a fast SSD/NVMe. Losing it only costs a cache
  miss, so it is **never** mirrored and has no topology prompt. Helps only when your
  working set is larger than RAM.
- **SLOG log** — accelerates **synchronous** writes (e.g. NFS `sync`). With **two or
  more** disks b2ctl asks the **topology (v0.17.0)**: `mirror` (redundant —
  recommended), `raid10` (stripe of mirrors, even # ≥ 4), or `single/striped` (no
  redundancy). A log vdev **cannot** be raidz — that is a hard ZFS rule, so it is
  never offered. A lone log device can lose in-flight writes, so b2ctl warns before
  adding one. Always use SSDs with **Power-Loss Protection (PLP)**.
- **`size to use per device` (over-provision)** — press Enter for the whole
  device, or enter a size (e.g. `32G`) to partition each device and hand ZFS the
  partition, leaving spare area. Over-provisioning a SLOG/L2ARC SSD improves its
  endurance and write consistency (no throughput change if aligned) — see `[n]ew-pool`.
  **Entering a size WIPES each device first (v0.18.0)** — b2ctl clears the GPT
  before it partitions (so a stale partition table can't collide), behind a WIPE
  warning + one **up-front** confirm (decline it and nothing is wiped). Same on
  `b2ctl cache-add`/`log-add --size`.
- CLI: `b2ctl cache-add|cache-rm|log-add|log-rm <pool> <dev…>`; force a SLOG
  topology with `b2ctl log-add <pool> <dev…> --mirror|--raid10`; over-provision with
  `--size 32G` on `cache-add`/`log-add`.

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

### 6.8 `m` — Manual maintenance (scrub · trim · health-check)

**When to use:** run a scrub or TRIM on a pool by hand (the primary maintenance
path — scheduled scrubs are opt-in and there is no trim timer), or health-check a
disk (long self-test + optional surface scan) before you pool it.

> **v0.18.0:** the old `[b]urnin` key is **gone** — disk vetting is now `[m]aint →
> [3] health-check`, one maintenance surface instead of two verbs that both ran
> `smartctl -t long`.

```
b2ctl> m
  [1] scrub  (verify checksums + self-heal)
  [2] trim   (release unused SSD blocks)
  [3] health-check (smartctl -t long + optional badblocks + verdict)
  action> 1
    [1] tank (ONLINE)
    [2] rpool (ONLINE)
  pool #> 1
  start scrub on 'tank'? [y/N]> y
  ✔ scrub started
  watch live progress (Ctrl-C detaches; kernel keeps running)? [y/N]> y
  scrubbing... 42.0% done, ETA 00:03:11
  ✔ scrub completed
```

- **scrub** — reads every allocated block, verifies checksums, and self-heals a
  redundant pool. This is the real defense against silent bitrot; run it regularly
  if you left autoscrub off. Live progress shows `% done` + the ZFS ETA.
- **trim** — tells the SSDs which blocks are free. There is **no trim timer** any
  more (v0.18.0), so unless the pool has `autotrim=on` (inline), this is how blocks
  get released. Progress is coarse (ZFS exposes limited trim status).
- **health-check** — the disk-vetting engine (the former burn-in). See below.
- **Ctrl-C detaches, it does not cancel.** During a live scrub/trim, Ctrl-C returns
  you to the prompt while the **kernel keeps running** the operation — re-check with
  `zpool status <pool>` or the pool's SCRUB column.
- Every scrub / trim / health-check is written to a **maintenance history**
  (`maint.jsonl`); review it with `b2ctl maint --log [--last N]`.
- CLI: `b2ctl maint scrub [<pool>]` · `b2ctl maint trim [<pool>]` (pool optional —
  prompts if omitted) · `b2ctl maint --log`. The top-level `b2ctl scrub <pool>` /
  `b2ctl trim <pool>` still work as aliases.

---

### 6.9 `m` → health-check — vet disk(s) before pooling (the former burn-in)

**When to use:** before trusting new or second-hand disks, run a SMART long
self-test (optionally a full read-surface scan) on **several disks at once** and
get a PASS/WARN/FAIL verdict per disk. This is `[m]aint → [3] health-check` (there
is no separate `[b]` key in v0.18.0).

**Multi-select + background.** Pick disks the same way as `[n]ew-pool`
(space-separated), confirm, then choose whether to also run a surface scan. The
self-tests run on the drives' own firmware and the scans run as detached
processes, so a **live view** shows a progress bar + estimated time remaining for
each disk — and you can **leave it running** (Ctrl-C) and come back later.

```
b2ctl> m
  action> 3
    [1] /dev/sdb (bay 32:4) Samsung SSD 870 EVO 1TB
    [2] /dev/sda (bay 32:5) Samsung SSD 870 EVO 1TB
    [3] /dev/nvme0n1 (bay PCIe2:0) Samsung 990 EVO Plus
  health-check which #> (space-separated) 1 2 3
  health-check 3 disk(s) (long self-test)? [y/N]> y
  also run a full read-surface scan (badblocks, read-only, hours)? [y/N]> y
  live burn-in — Ctrl-C to leave running in background

 BAY     DISK      SELF-TEST                     SURFACE SCAN (badblocks)
 32:4    sdb       [########------]  62%  ~1h10m  [###-----------]  18%  ~4h30m
 32:5    sda       [##########----]  74%  ~40m    [####----------]  22%  ~4h05m
 PCIe2:0 nvme0     [#############-]  90%  ~8m     n/a
```

- **Which disks — free/spare only, both ways.** health-check vets **free or spare**
  disks; in watch it only lists those, and `b2ctl maint health <dev…>` **refuses**
  an in-pool member (`… is in pool '<pool>' — self-test it with \`smartctl -t long\`
  directly`). To self-test an **active pool member**, run `smartctl -t long <by-id>`
  yourself in a shell — b2ctl doesn't trigger it, but the member's HEALTH_CHK column
  still updates passively from `smartctl -a` on the next refresh.
- **Leaving & re-attaching:** press **Ctrl-C** to return to the prompt — the tests
  and scans keep running. Press `[m]` → `[3]` again for a menu — **[v]** view the
  live view, **[c]** cancel one disk, **[a]** cancel all, **[n]** start a new
  health-check — or run `b2ctl maint health --status`; when a disk finishes you'll
  see its verdict there.
- **Cancelling:** to stop a disk mid-check (e.g. a dying disk holding up the batch),
  use the menu's `[c]`/`[a]`, or `b2ctl maint health --cancel <bay|dev …>` /
  `b2ctl maint health --cancel-all`. It aborts the self-test and stops the read-only
  scan — nothing is written, and the disk can be re-checked later.
- While a self-test runs, `b2ctl status` shows `TEST xx%` in that disk's STATUS
  column, and the **HEALTH_CHK** column shows the last completed long test as
  `OK`/`ERR` + its age in power-on hours (e.g. `OK 120hPOH`) — for **SATA, SAS and
  NVMe** alike. (v0.18.0 fixes a bug where healthy **SAS** disks — whose self-test
  success is the bare word `Completed`, not ATA's `Completed without error` — were
  mis-graded `ERR`/`FAIL`.)
- **PASS** — clean. **WARN** — usable but aged (power-on hours > 40000, grown
  defects, or the surface scan found bad blocks): use as lower-priority. **FAIL** —
  uncorrected errors or a failed self-test: do not pool it.
- **A disk that says nothing now FAILS, not PASSes (v0.24.2).** Previously a drive
  whose SMART could not be read at all had every counter come back empty, so it
  fell through every check and was reported `PASS — safe to add to a pool`. That
  was the worst possible answer: the one drive you learned nothing about is the
  one you must not trust. It now reads
  `FAIL — SMART did not answer (drive unreadable) — cannot vet this disk`.
  Likewise, a self-test that produced **no verdict** (nothing on record and
  nothing running) is now **WARN — no self-test verdict available**, not PASS.
- **The verdict is about the drive you named (v0.25.1).** On a PERC box the DEV
  column shows `-` for every drive behind a controller, because none of them has
  a device node of its own. b2ctl was re-reading SMART by that `-` when it graded
  a finished health-check, which matched the *first* such drive rather than yours
  — so a PASS/WARN/FAIL could come from a neighbouring disk. It now identifies the
  drive by its serial. **IT-mode boxes were never affected** (every disk there has
  its own `/dev/sdX`), and this only became reachable in v0.24.2 when PERC drives
  started being vettable at all.
- **PERC Unconfigured-Good drives can be vetted too (v0.24.2).** A drive sitting
  behind a PERC has no device node of its own — the table shows `-` in DEV — so
  b2ctl vets it through the controller (`smartctl -d megaraid,<DID>`), the same
  route the health table already uses to read it. The firmware self-test runs
  normally; the **surface scan is skipped** and shows `n/a`, because `badblocks`
  needs a real block device and scanning the controller handle would read the
  whole virtual disk instead of the drive.
- Read-only: the only actions are the self-test trigger and (optionally) a
  read-only `badblocks` scan — your data/disk is never written.
- CLI: `b2ctl maint health <bay|dev> [<bay|dev> …] [--scan] [--short]`;
  re-attach with `b2ctl maint health --status`.

---

### 6.10 `u` — Udev-rescue (recover an OS-rejected disk)

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

### 6.11 `x` — Destroy a pool

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
  ✔ pool 'tank' destroyed; timers disabled
```

> ⚠️ Two gates: the `[y/N]` **and** re-typing the exact pool name. b2ctl also
> disables that pool's systemd maintenance timers. (Bare key `x` only — no word alias.)

---

### 6.12 `t` — Toggle dry-run mode

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

### 6.13 `l` — Locate (blink LED)

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

### 6.14 `q` — Quit

```
b2ctl> q
bye
```

---

### 6.15 Hot-plug (automatic detection)

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

## 7.7 Scripting b2ctl — `--json` (v0.22.0)

Every **read** command can return JSON instead of a table, so scripts, a web UI
or an MCP server can use b2ctl directly. `--json` works before or after the verb:

```
b2ctl --json status        # same as
b2ctl status --json
```

You always get one envelope, and it looks the same whether the command worked:

```json
{ "schema_version": 1, "ok": true, "command": "pools",
  "data": { "pools": [ … ] }, "warnings": [], "error": null }
```

```json
{ "schema_version": 1, "ok": false, "command": "disks",
  "data": null, "warnings": [],
  "error": { "code": "NEEDS_ROOT", "message": "run as root (…)" } }
```

- **`ok`** — did it work. **`error.code`** is a fixed word to branch on
  (`NEEDS_ROOT`, `NO_BACKEND`, `TOOL_MISSING`, `POOL_NOT_FOUND`,
  `DISK_NOT_FOUND`, `INVALID_ARG`, `PARSE_ERROR`, `UNSUPPORTED`). Don't match on
  `message` — the wording can change.
- **`warnings`** — non-fatal notices (e.g. an unreadable `bay_map.json`). In
  table mode these print as `[!] …`; with `--json` they move in here so the
  output stays valid JSON.
- **`schema_version`** — only changes if a field is removed or renamed. New
  fields can appear at any time.

Which verbs:

| command | what `data` holds |
|---|---|
| `status --json` | `backend`, `disks`, `pools`, `volumes`, `summary` — everything at once |
| `disks --json` | `disks` (full SMART scan) |
| `pools --json` | `pools` — no SMART, cheap enough to poll often |
| `volumes --json` | `volumes` (hardware RAID; empty list in IT mode) |
| `bays --json` | `panels`, `disks`, `detected_slots`, `path` |
| `check --json` | `root`, `backend`, `tools` |
| `log --json` / `maint --log --json` | `entries` / `events` |
| `raid-foreign --json` | `controller`, `groups`, `bays` |
| `config show --json` | `config`, `paths` |
| `version --json` | `version`, `schema_version` |

Examples:

```bash
b2ctl pools --json | jq -r '.data.pools[] | "\(.name) \(.health) \(.free)"'
b2ctl disks --json | jq '.data.disks[] | select(.level != "NORMAL")'
b2ctl status --json | jq -r '.warnings[]'
```

### Commands that change something — `--confirm` (v0.23.0)

Mutating commands normally stop and ask. A program can't answer, so `--confirm`
answers for it:

```bash
b2ctl destroy tank --json --confirm tank      # strictest: name what you're changing
b2ctl scrub tank   --json --confirm yes
b2ctl offload --disk 32:4 --json --confirm yes
```

- **no `--confirm`** — nothing changes. b2ctl prompts exactly as it always has,
  including "type the pool name to confirm".
- **`--confirm yes`** — approves the confirmations for this one command.
- **`--confirm <target>`** — approves them **and** requires `<target>` to match
  what is actually being changed. Safer: a mistyped or mis-parsed command can't
  have your approval applied to a different pool.

Pick the target with an argument instead of the menu:

| verb | argument |
|---|---|
| `offload` `replace` `swap` `demote` | `--disk <bay\|serial\|dev\|by-id>` |
| `create` | `--disks a,b,c --type mirror --name tank` |
| `destroy` `scrub` `trim` | the pool name, as today |
| `raid-create` `raid-del` `raid-foreign` | as today |

If a command still needs an answer you didn't give, it says so instead of
hanging:

```json
{ "ok": false, "error": { "code": "INVALID_ARG",
  "message": "'offload which disk? #>' — this command still needs that answer; supply --disk <bay|serial|dev>" } }
```

Long jobs return as soon as they start — poll them:

```bash
b2ctl scrub tank --json --confirm yes
b2ctl progress --json | jq '.data.running'
# [{"kind":"scrub","target":"tank","pct":37.2,"eta":"01:04:11","state":"running"}]
```

`progress` reports scrub, TRIM, hardware rebuild and health-check. It only reads,
so it's safe to poll as often as you like.

Under `--json`, a mutating command's normal narration (confirm boxes, resilver
bars) is captured into `data.log` instead of being printed, so the output is
still one clean JSON document.

> `b2ctl watch` has no `--json` form — it's an interactive terminal loop and
> returns `UNSUPPORTED`.

### You always get an envelope back, even when b2ctl dies (v0.25.0)

Three ways a `--json` call used to give a program nothing usable:

| you ran | you used to get | you now get |
|---|---|---|
| any verb on a box with no sas2ircu/perccli | **nothing at all** — zero bytes on stdout | `ok:false`, `error.code: "NO_BACKEND"`, and the reason in `error.message` |
| anything that crashed unexpectedly | a Python traceback | `ok:false`, `error.code: "PARSE_ERROR"` |
| `maint health --status --json` | **it never came back** — it opened the live progress view and redrew forever | the current health-check state, immediately |

Two new error codes are now real, so you can tell "you named something that
doesn't exist" from "the operation failed":

```bash
b2ctl destroy nosuchpool --json --confirm yes   # -> POOL_NOT_FOUND
b2ctl locate 99:99 --json                       # -> DISK_NOT_FOUND
```

If `zpool` itself is silent you get `TOOL_MISSING`, **not** `POOL_NOT_FOUND` —
b2ctl will not tell you a pool is gone when it merely could not look.

### A resilver doesn't hold your program hostage (v0.25.0)

`replace`, `swap` and `offload` start a resilver, which can run for hours.
Interactively b2ctl draws a progress bar and waits. With `--confirm` it now
returns as soon as the resilver has **started**:

```bash
b2ctl replace --disk 32:4 --json --confirm yes
# ok:true — "resilver started on 'tank' — not waiting (hours).
#            Poll it with `b2ctl progress`."
b2ctl progress --json | jq '.data.running'
```

**The old disk stays attached until it finishes.** That is deliberate: until the
resilver completes, the old member may hold the only copy of blocks that haven't
been rebuilt yet, so detaching it early is exactly how you lose data. b2ctl
detaches it for you the next time you're in `watch` and the resilver is done.

Health-checks work the same way — start one under `--json`, then poll
`b2ctl maint health --status --json`.

---

## 8. Warnings

### The table adapts to your terminal (v0.21.0)

On a box with many disks the table used to run off the screen in both directions.
It now fits itself to the window:

- **Too narrow?** The least important columns are dropped, least-useful first
  (`WRITTEN` → `POWER_ON` → `END(left)` → `WEAR(used)` → `HEALTH_CHK` → …), and a
  note says how many were hidden. **BAY, MODEL, SERIAL, HEALTH, POOL/ARRAY and
  LEVEL are never dropped** — you can always tell which disk a row is and whether
  it is healthy.
- **Too tall?** `b2ctl status` opens a pager (`less`), so nothing scrolls away.
  Arrow keys scroll, including sideways for any column that was chopped; `q` quits.

```
[!] 5 column(s) hidden (terminal 120 < 186) — widen the window or use `b2ctl status --full`
```

Nothing changes when you pipe or redirect: `b2ctl status > report.txt` and
`b2ctl status | grep …` always get the full table and never a pager. `--full`
forces every column, `--no-pager` disables paging, and `PAGER=cat` does too.

`watch` fits its columns the same way but **never** pages — it needs the terminal
for hot-plug detection.

### END(left) now matches iDRAC (v0.24.0)

`END(left)` is **remaining rated write endurance** — how much of the drive's rated
lifetime writing is left. It now comes from the drive itself, which is the same
place iDRAC gets **"Remaining Rated Write Endurance"**, so the two agree:

```
BAY     WEAR(used) END(left)
32:12   0%         100.0%       ← iDRAC shows 100%
32:4    1%         99.0%        ← iDRAC shows 99%
```

Before v0.24.0, b2ctl computed this itself as *(rated TBW − TB written) ÷ rated
TBW*, using a small built-in table of drive models. That number did **not** match
iDRAC, and any model missing from the table showed `N/A`.

The old estimate is still calculated and kept for comparison — the two drift
apart over time, because the drive counts what it actually wrote to flash while
the estimate only counts what the host sent it.

With `--json` you can see which source was used:

```bash
b2ctl disks --json | jq -r '.data.disks[] | "\(.bay) \(.end_left) \(.end_source) (spec est \(.end_left_spec))"'
# 32:4 99.0 drive (spec est 98.4)
```

- `end_source: "drive"` — from the disk, matches iDRAC.
- `end_source: "spec"` — the disk doesn't report it, so this is our estimate.
  Those are the models worth adding to `ssd_spec.json`.

Cross-check against the BMC any time:

```bash
racadm storage get pdisks -o | grep -iA2 RemainingRatedWriteEndurance
```

### "uncorrectable errors" vs "command timeouts" — different problems (v0.23.1)

Two reasons look similar in the details block but mean opposite things, and they
send you to different places:

```
- bay 1:5 /dev/sde (SN 67P0A07FTF2E) [CRITICAL]
    - uncorrectable errors=2                      ← the DISK lost data
```

The drive tried its error correction, retried, and gave up. Those sectors are
gone. **Any** count above zero is CRITICAL, on SSDs and HDDs alike — unlike
`reallocated`, where a sector that was successfully moved to spare capacity is
ordinary wear on an old HDD. Don't put this disk in a pool; replace it.

```
- bay 1:5 /dev/sde (SN 67P0A07FTF2E) [WARNING]
    - command timeouts=2 — usually cabling / backplane / power, not the media
```

The drive didn't answer in time. That is a **link** problem: reseat the cable and
the drive, check the backplane and the power. The platter is probably fine.

> Before v0.23.1 the second case was reported as the first — SATA attribute 188
> (`Command_Timeout`) was counted as an uncorrectable error, so a loose cable read
> as `[CRITICAL] uncorrectable errors=2` and people replaced a healthy disk.

Either way, vet the drive before trusting it:

```bash
b2ctl maint health 1:5            # long self-test -> PASS / WARN / FAIL
smartctl -A /dev/sde | grep -E '187|188|198'    # which counter actually moved
```

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
| `b2ctl status --json` | JSON output (see *Scripting b2ctl* below) |
| `b2ctl disks` / `pools` / `volumes` | one slice each — `pools` skips the SMART scan, so it is cheap to poll |
| `b2ctl bays` | show what each drive's bay label resolves to |
| `b2ctl bays --calibrate` | blink each bay, ask which slot lit, write the rule |
| `b2ctl bays --set-reverse on\|off [--slots N]` | mirror-reverse the front panel's numbering |
| `b2ctl bays --set 32:0=32:7` / `--clear` | relabel one bay / drop all bay customisation |
| `b2ctl status --full` | every column at full width, no pager (for copy/paste) |
| `b2ctl status --no-pager` | never page, even when taller than the screen |
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
| `b2ctl create` | guided: create a new ZFS pool (prompts size/over-provision + autotrim/autoscrub, both default off = manual) |
| `b2ctl maint scrub [<pool>]` | start a manual scrub (verify checksums + self-heal); prompts if pool omitted |
| `b2ctl maint trim [<pool>]` | start a manual TRIM (release unused SSD blocks) |
| `b2ctl maint health <dev…> [--scan] [--short] [--status] [--cancel …\|--cancel-all]` | vet disk(s): long self-test (+ optional read-only badblocks) + PASS/WARN/FAIL (was `b2ctl burnin`) |
| `b2ctl maint --log [--last N]` | show the maintenance history (scrub/trim/health, default last 30) |
| `b2ctl scrub [<pool>]` / `b2ctl trim [<pool>]` | back-compat aliases of `b2ctl maint scrub` / `maint trim` |
| `b2ctl log-add <pool> <dev…> [--mirror\|--raid10] [--size 32G]` | add a SLOG; force topology + over-provision |
| `b2ctl cache-add <pool> <dev…> [--size 512G]` | add L2ARC cache; over-provision with `--size` |
| `b2ctl raid-foreign` | show a PERC foreign configuration (read-only, no root) |
| `b2ctl raid-foreign --import\|--clear [-c N]` | import / discard it — **CONTROLLER-WIDE**, double-confirmed |
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
| `n` | create a new pool (prompts over-provision size + autotrim/autoscrub) |
| `e` | extend a pool — add/remove/**repair** L2ARC cache or SLOG log (SLOG topology + size prompts) |
| `m` | manual maintenance — `[1]` scrub / `[2]` trim (per-pool) / `[3]` health-check = multi-select disk vetting (long self-test + optional badblocks + verdict; was `[b]urnin`) |
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

On a **PERC drive** (RAID mode) the same key opens the hardware menu instead:
`1` locate, `2` set JBOD (hand it to ZFS), `3` create a volume, `4` hot spare —
plus `5` **Foreign config** when the drive carries one, which is *required*
before 2/3/4 will run.

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

### Why hardware-RAID rows show `DEV = -` (v0.21.0)

A drive that lives *behind* a PERC virtual disk is invisible to the operating
system — it has no `/dev/sdX` of its own. The DEV column says so:

```
BAY     DEV    IF   MODEL              ... POOL/ARRAY
32:12   -      SAS  X357_S164A3T8ATE   ... HW:vd1/raid10
32:22   -      SATA SSDSC2KG480G8R     ... HW:vd0/raid1
32:0    sda    SAS  DL2400MM0159       ... SW:tank/mirror-0
```

Before v0.21.0 the column printed the *virtual disk's* device (`sdq`) on every
hardware row — the same value for every drive, and the same value even across two
different volumes. Identify these drives by their **BAY**, which is what every
b2ctl action uses anyway.

The side effect worth knowing: with two hardware volumes, the storage summary now
measures each one separately. It used to report identical `USED`/`FREE` for both.

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

### A drive with a FOREIGN config (v0.20.0)

Put a disk in that once belonged to **another controller or another array** and it
arrives carrying that array's metadata. The PERC calls this a **foreign
configuration** and refuses to do *anything* with the drive — JBOD, hot spare,
volume member — until you deal with it. `b2ctl status` flags it:

```
- bay 32:7 /dev/sda (SAMSUNG MZ7LH1T9HMLT-00003, SN S4F2NY0KA04123) [CONFIG]
    - FOREIGN config on this drive — the controller refuses JBOD / hot-spare /
      volume-create until it is imported or cleared (assign -> [5], or perccli /cN/fall)
```

In `watch`, `[a]ssign` that drive and the menu grows a fifth entry. Options 2/3/4
refuse up front and tell you why, instead of letting the controller answer with
`ErrCd 255 Operation not allowed`:

```
  PERC drive (32:7) SAMSUNG MZ7LH1T9HMLT-00003 (S4F2NY0KA04123) [UGood, FOREIGN]
    [1] Locate LED (blink the bay)
    [2] Use for ZFS / software RAID  (set JBOD — exposes it as /dev/sdX)
    [3] CREATE a hardware RAID volume (perccli)
    [4] Add as hardware HOT SPARE
    [5] Foreign config on this controller — import or clear it (REQUIRED before 2/3/4)
```

Choosing `[5]` shows **what the foreign config actually is** before asking:

```
  FOREIGN CONFIG on /c0:
    DG EID:Slot Type    State  Size      VDs
     0 -        RAID10  Frgn   3.491 TB  1
  foreign drive(s) present on this controller: 32:4
  NOTE: this group spans more drives than are present — importing it would give
        a degraded array.
  WARNING: perccli /c0/fall acts on the WHOLE controller — there is no
  per-drive form. Both actions below hit everything listed above.
    [i] import — bring that foreign array back online on this controller
    [c] clear  — DISCARD it; its drives drop to Unconfigured-Good
    [s] skip / decide later
```

**`EID:Slot = -` is normal, not an error.** The controller records a foreign
config per **drive group**, not per drive, so it cannot name one slot when the
group spans several — as above, where the old array was a 2-drive RAID10 and only
one of its disks is in this machine. The line underneath tells you which drives
that are actually plugged in carry the config.

- **import** — you want that old array back (you moved a working set of disks).
- **clear** — you want the disks, not the old array. The array becomes
  unimportable. Double-confirmed: `[y/N]`, then type the controller number.

⚠️ **Read the table before you choose.** There is no per-drive form of this
command — `/c0/fall` means *every* foreign config on controller 0. If the table
lists drives you did not expect, stop and work out where they came from first.

Same thing from the command line:

```
b2ctl raid-foreign                 # show only — read-only, no root needed
b2ctl raid-foreign --import        # import (CONTROLLER-WIDE)
b2ctl raid-foreign --clear         # discard (CONTROLLER-WIDE, DESTRUCTIVE)
b2ctl raid-foreign --clear -c 1    # ... on controller 1
```

After clearing, `[r]efresh` and the drive reads plain `Unconfigured Good` — then
`[2] set JBOD` works.

> If a drive is refused **without** being foreign, b2ctl now prints what it
> checked, so the other cause is visible too:
>
> ```
>   why: the PERC refuses this transition. Checked:
>     - foreign config on 32:7    -> no
>     - controller 0 JBOD policy  -> OFF  <-- this
>     - Support JBOD              -> Yes
>   fix: `perccli /c0 set jbod=on` (controller-wide policy — b2ctl will not flip it for you)
> ```

### HBA330 / H330 boxes — perccli sees the card, the OS owns the disks (v0.19.0)

A Dell **HBA330 Mini / H330** (LSI SAS3008, IT firmware) is *not* a RAID
controller. It hands every drive straight to the OS as a raw `/dev/sdX`, exactly
like a crossflashed H710 — but it is a **SAS3** chip, so the tooling splits:

- **`sas2ircu` cannot see it.** That tool speaks SAS2 only and reports zero
  controllers on this card.
- **`perccli` can.** It manages the card fine, and is the only way to read each
  drive's bay (`enclosure:slot`).

Since **v0.19.0** b2ctl asks *who owns the storage* before picking a mode, so on
such a box it:

- runs the normal **IT/HBA workflow** — one row per real drive from `lsblk`,
  SMART read **directly** (`smartctl -a /dev/sdX`, no megaraid passthrough), and
  the full **ZFS** lifecycle (`[a]ssign`, `[n]ew-pool`, `[o]ffload`, `[s]wap`,
  `[m]aint`, `[l]ocate`);
- takes **only the bay numbers** from perccli.

`b2ctl check` confirms it:

```
  [✔] Detected backend: IT-mode
  [✔] Bays mapped: 9 disks across 1 enclosure(s)
```

**Install perccli, but do not force RAID mode:**

```bash
sudo b2ctl install --tool perccli   # the tool only — leaves controller.mode alone
```

`b2ctl install --perc` is for a real PERC running hardware RAID: it writes
`controller.mode = "raid"`, which on an HBA330 is exactly the setting that produced
the duplicated-`NOREAD` rows in §4.

**Leave `controller.mode` on `"auto"`.** Auto now gets these boxes right by itself,
so you no longer need to force anything:

| `controller.mode` | what happens on an HBA330 / H330 |
|-------------------|----------------------------------|
| `"auto"` (default) | **correct** — IT/ZFS workflow, bays from perccli |
| `"it"` | works (b2ctl falls back to perccli for the bays), but pointless |
| `"raid"` | **wrong** — brings back the duplicated `NOREAD` rows |

If an earlier version left `"raid"` in the config for one of these boxes, put it
back to `"auto"` — edit `/etc/b2ctl/config.json` (there is no `config set` verb):

```json
{ "controller": { "mode": "auto" } }
```

then re-run `b2ctl check` — it should report `Detected backend: IT-mode`.

**Auto never demotes a real PERC.** A controller that names its own personality
(`RAID-Mode`) is believed outright, and where there is no such string b2ctl calls a
card HBA-like only when **every** drive it reports also turns up as an OS block
device. A PERC with no virtual disk yet — a fresh box, or right after `b2ctl
raid-del` — therefore stays in RAID mode with its `raid-*` verbs available; you do
not have to force `"mode": "raid"` just to build the first volume.

**Bays fill in even when perccli reports no serials.** These cards often print a
drive list with no per-drive serial section, and an enterprise SAS drive publishes
no serial to `lsblk` until b2ctl reads SMART — so the serial-keyed bay map matched
nothing and the BAY column stayed blank. b2ctl now also reads each slot straight
from the kernel (`/sys/class/sas_device/*/bay_identifier`), which maps **device →
slot** with no serial involved. Nothing you have memorised changes:

- **The numbers stay the same.** Only the *slot* comes from the kernel; the
  **enclosure** prefix keeps the number the vendor tool already shows, so drives
  still read `9:0 … 9:23`, never `0:0`.
- **A vendor label always wins.** The kernel is consulted only for disks the vendor
  map left without a bay, so an R620 (`sas2ircu` + `reverse_slots`) prints exactly
  the table it printed in v0.18.0.
- **`bay_map.json` still applies** — a kernel slot goes through the same front-panel
  remap (`map` / `reverse_slots`) as any other bay (see *Bay labels* below).

It also works with **no vendor tool installed at all**: on a SAS backplane with
neither `sas2ircu` nor `perccli`, `b2ctl status` now shows bays instead of blanks
(labelled `0:<slot>`, since there is no vendor enclosure number to borrow). A box
with no SAS transport (pure SATA / NVMe) is unaffected — there is nothing to read.
The SES enclosure processor in the backplane has no block device of its own, so it
never becomes an extra row.

**Known limits in v0.19.0 — worth knowing before you trust an empty-looking bay:**

- **On a real PERC RAID box, a hidden drive can still be missing from the table.**
  b2ctl joins every controller drive to an OS disk by **serial, then WWN**, so a
  drive perccli can identify always gets a row — including the mixed layout that
  the `[a]ssign` menu's *set JBOD* creates (one drive exposed to the OS, an
  identical sibling still hidden). Only a drive perccli reports with **neither
  serial nor WWN** can be left out, and then only when a drive of the same model
  *and* size is already visible to the OS — in that case you cannot `set jbod` it
  or add it as a hot spare until it shows up. If a bay is populated but has no row,
  check the controller's own list: `perccli /c0/eall/sall show all`.
- **A GHOST row can be withheld on the pre-SMART pass.** Enterprise SAS drives
  report no serial to `lsblk` until b2ctl reads SMART, which made *every* mapped
  drive look OS-rejected — that was the phantom-row symptom. b2ctl now stays quiet
  only when the drives that have not identified themselves yet can account for all
  of them; any surplus is still reported as a GHOST / CRITICAL row with the
  `[u]dev-rescue` prompt (§6.10). If a populated bay has no row at all, check
  `dmesg | tail` and reseat the drive.

---

## Creating a ZFS pool (`[n]ew-pool`)

After you pick disks, b2ctl asks for an over-provision **size** (Enter = whole
disk; a size such as `32G` partitions each disk and leaves SSD spare area — see
`[n]` in §6). Then, after name and raid level, it asks each pool property with an
SSD-optimal default — **press Enter to accept**, or type to override (`ashift`,
`compression`, `atime`, `xattr`, `dnodesize`, `acltype`, `recordsize`).
`recordsize` is workload-tunable (128K general, DB 16K, media 1M, VM 64–128K) and
can be changed per-dataset later.

**autotrim** and **autoscrub** are two independent choices, **both default OFF**,
and **OFF means manual-only — no timer is installed** (v0.18.0). Both questions
read `[1] off` (default) `/ [2] on`:
- **autoscrub** — *default **off***. `[2] on` enables `zfs-scrub-monthly@<pool>.timer`.
  With **off**, the pool has **no scheduled scrub** — manual scrub is now the primary
  path (`[m]aint` / `b2ctl maint scrub`), and b2ctl prints a reminder + the pool's SCRUB
  column shows how stale the last scrub is. *(This reverses the older always-on scrub;
  see ADR-003 — a pool you never scrub can accumulate undetected bitrot, so scrub it
  regularly or turn autoscrub on.)*
- **autotrim** — *default off* — TRIM by hand with `[m]aint` / `b2ctl maint trim
  <pool>`; `[2] on` sets `zpool autotrim=on` so ZFS trims inline. Either way **no trim
  timer is installed**. *(This reverses the older v0.16/v0.17 behaviour where
  `autotrim off` scheduled a monthly `zfs-trim` timer — there is no trim timer any
  more; see ADR-004.)*

Check what's scheduled: `systemctl list-timers | grep zfs`. If the distro doesn't
ship the timer units, b2ctl warns "scrub timer NOT scheduled" and installs nothing —
install `zfsutils-linux` or enable a timer yourself. Your last autotrim/autoscrub
answers are remembered and pre-fill the next `create`.

Debian/Proxmox also has a built-in cron that scrubs *all* pools monthly (property
`org.debian:periodic-scrub`). To avoid scrubbing twice, when b2ctl enables a pool's
scrub timer it sets that pool's `org.debian:periodic-scrub=disable` — so your
per-pool timer becomes the single schedule. (No trim timer is installed, so
`org.debian:periodic-trim` is left untouched.)

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

> NVMe drives appear in the table and in `[a]ssign` / `[m]aint` health-check like
> any other disk — only the BAY column differs (no enclosure:slot). The bay label is
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
