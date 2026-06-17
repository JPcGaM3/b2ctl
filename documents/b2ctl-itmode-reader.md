# b2ctl (IT-mode) — Reader Guide

A small command-line tool for watching SSD/HDD health and managing ZFS disks
on a Dell R620 whose PERC H710 has been crossflashed to **IT/HBA mode**
(it presents as an LSI SAS9207-8i / SAS2308). In this mode the disks are raw —
there is no RAID controller to ask — so b2ctl reads each drive directly and
talks to ZFS for everything else.

This is the IT-mode sibling of the original storcli/RAID-mode b2ctl. Same look
and feel (a wide health table with a details block underneath), but it no
longer needs perccli/storcli or megaraid passthrough.

---

## What it does

- Shows one table row per physical disk: bay, device, model, serial, power-on
  hours, wear, endurance left, total written, bad sectors, SMART health, the
  pool/vdev it belongs to, and an overall **LEVEL** (NORMAL / CONFIG / WARNING /
  CRITICAL).
- Lists your ZFS pools and their health underneath.
- Spells out exactly which disks need attention and why.
- **Watches for disks you plug in or pull out while it runs**, and asks what to
  do with a new disk — make it a spare, use it to replace a failed disk, or
  wipe it blank.
- Blinks a single disk's LED (by device) so you know which physical drive to
  pull — works around the scrambled bay numbering (see note below).

---

## Install (about a minute)

```bash
cd codes
sudo ./install.sh
```

This copies the package to `/opt/b2ctl` and creates the `b2ctl` command.
You need `smartctl` (smartmontools), `zpool` (zfsutils), and `lsblk` already
present. `sas2ircu` gives bay numbers; `ledmon` (`ledctl`) gives nicer locate
LEDs but is optional — without it, b2ctl blinks the disk's activity LED instead.

---

## The two ways to use it

### 1. A quick health check

```bash
sudo b2ctl status
```

You get the table, the pool summary, and the details block — the same shape as
the old hardware-RAID version. Add `--locate` to blink the LEDs on any at-risk
disks for a few seconds (`--seconds N` to change). Add `--json` for a
machine-readable dump.

### 2. The interactive watcher (the main event)

```bash
sudo b2ctl watch
```

It prints the table once, then sits and watches. Two things can happen:

**You insert a disk.** b2ctl notices within a couple of seconds, prints a panel
about the new drive (model, serial, size, health), and asks:

  What do you want to do with it?
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    [3] REPLACE a degraded/faulted disk in a pool
    [4] ATTACH to an existing disk (convert to/expand mirror)
    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)
    [6] WIPE it blank (for a new pool)
    [s] skip / decide later
```

Pick one, confirm, and b2ctl runs the right `zpool` command for you.

**You pull a disk.** b2ctl reports which device disappeared and reprints the
pool health so you can see whether a pool went DEGRADED.

While watching, you can also type a single-letter command at the `b2ctl>`
prompt:

| key | action |
|----|--------|
| `r` | refresh the table |
| `a` | assign a free disk |
| `o` | offload an in-pool disk |
| `s` | swap an in-pool disk onto an existing spare |
| `d` | demote a mirror member to a spare |
| `n` | create a new pool (warns if disks contain existing labels) |
| `l` | blink one disk's LED (~5s) by bay/serial/device |
| `q` | quit |

---

## Commands (one-shot)

| command | what it does |
|---------|--------------|
| `b2ctl status [--locate] [--json]` | one-shot health table + pool summary + details block |
| `b2ctl locate <bay\|serial\|dev> [secs]` | blink one disk's LED for ~5 s |
| `b2ctl offload` | guided: safely detach or resilver an in-pool disk |
| `b2ctl replace` | guided: simulate-fail and replace a disk onto a spare |
| `b2ctl swap` | guided: swap a wearing disk onto an existing spare |
| `b2ctl demote` | guided: demote a mirror leg to a spare |
| `b2ctl create` | guided: create a new ZFS pool |
| `b2ctl check` | verify tools, show which backend was detected, config file status |
| `b2ctl config show` | print current effective configuration as JSON |
| `b2ctl config init` | write `/etc/b2ctl/config.json` with auto-detected defaults |
| `b2ctl version` | print version string |

---

## Configuration

b2ctl works out of the box with no config file. A config file at
`/etc/b2ctl/config.json` lets you override tool paths and force a backend mode.

**The file is never auto-created.** Run `b2ctl config init` on the server to
generate it with auto-detected defaults, then edit as needed.

### Key fields

- **`tool_paths.<name>`** — absolute path to a binary, e.g.
  `"/tool_paths/sas2ircu": "/usr/local/bin/sas2ircu"`. An empty string means
  "auto-detect from PATH".
- **`controller.mode`** — `"auto"` (default), `"it"` (HBA/sas2ircu), or
  `"raid"` (storcli/perccli). Use `"it"` or `"raid"` to skip auto-detection
  and guarantee the right backend.
- **`controller.index`** — `"all"` (default, scan all controllers) or a numeric
  string like `"0"` to restrict to one controller.
- **`bay_map_path`** — path to a custom `bay_map.json`. Empty means use the
  bundled file next to the installed package.

### Useful commands

```bash
# On the server — first-time setup
sudo b2ctl config init          # writes /etc/b2ctl/config.json
sudo b2ctl config show          # print current effective config
sudo b2ctl check                # verify tools, detect backend, show config path
```

`b2ctl check` is the first thing to run when something looks wrong. It shows
which tools were found, which backend was detected, and whether the config file
exists.

---

## Reading the table

- **WEAR(used)** — how much SSD life the drive's own SMART counter says is used.
- **END(left)** — endurance left, worked out from total bytes written versus
  the drive's rated TBW (from `ssd_spec.json`). For the Samsung 870 EVO 1TB
  that rating is 600 TBW; for the 860 PRO 1TB it is 1200 TBW.
- **BAD** — reallocated sectors (SATA) or grown defects (SAS). Anything above 0
  is treated as critical.
- **LEVEL**
  - `NORMAL` — healthy and assigned to a pool.
  - `CONFIG` — healthy but **not in any pool** (a spare-in-waiting or a fresh
    disk to add).
  - `WARNING` — endurance/wear getting low, or vdev degraded.
  - `CRITICAL` — failed SMART, bad sectors, almost no endurance left, or a
    faulted/unavailable vdev member. GHOST disks (OS rejected the drive, no `/dev/sdX` node exists) also show as CRITICAL.

---

## Common tasks

**A disk failed — replace it.** Pull the dead one (use
`b2ctl locate <bay|serial> ` first if unsure which it is — it blinks for ~5s), slot in the new one, and let `b2ctl watch` catch
it — choose `[2]` and pick the faulted member. ZFS resilvers automatically.

**A disk is wearing out but still alive.** If you keep a spare in the pool,
press `s` in `watch`, pick the worn disk, and b2ctl resilvers it onto the spare
before it dies. Then physically swap the worn one out at your leisure.

**Adding a fresh spare.** Insert the disk, choose `[1]`, pick the pool.

> On IT mode there is no automatic hardware rebuild. ZFS does the rebuild
> (resilver), and b2ctl turns each of these into one guided step.

---

## A note on bay numbers

On this Dell 12G backplane flashed to LSI IT mode, the controller reports
**scrambled slot numbers** (a known issue — the Dell firmware's slot map is
gone). b2ctl corrects them via `bay_map.json`; this server uses a simple
mirror-reversal across 8 bays. The numbers are cosmetic — every action keys off
the disk **serial**, not the bay. To recalibrate, run `b2ctl locate <serial>`,
watch which bay blinks, and adjust `bay_map.json`.

## A safety note specific to your hardware

Mixing a **SAS** drive as a hot spare into an all-**SATA** pool used to hang the
megaraid driver back when the card was in RAID mode. In IT mode that code path
is gone, so it is technically allowed — but reformat enterprise SAS drives to
512-byte sectors first and test on a spare bay. When in doubt, match the
existing drives.
