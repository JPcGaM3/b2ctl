# ADR-005 — Backend selection keys on controller PERSONALITY (who owns the disks), not on which vendor tool answers

- **Status:** Accepted
- **Date:** 2026-07-29
- **Version:** v0.19.0-itmode
- **Relates to:** ADR-001 (two co-equal backends + the module layering this ADR
  extends — it revises ADR-001's implicit "sas2ircu is silent ⇒ PERC RAID box"
  detection rule), CLAUDE.md §2 (environment) and §5 (module map).

## Context

A Dell PowerEdge with an **HBA330 Mini** (LSI SAS3008, IT firmware, kernel driver
`mpt3sas`) ran `b2ctl status` and got **18 rows for 9 physical drives**: the 9
correct lsblk rows (`sda`…`sdi`), plus 9 phantoms that all claimed `DEV=/dev/sda`,
`SERIAL N/A`, `HEALTH NOREAD`, `LEVEL CRITICAL`, bays `9:0`–`9:6`/`9:22`/`9:23`,
each carrying the advisory

```
available (Unconfigured Good) — set JBOD for ZFS, or add to a RAID volume (raid-create)
```

(`common.assess`, common.py:262-270, plus `SMART unreadable` → CRITICAL). Every
drive appeared twice — once healthy, once as an unreadable duplicate demanding a
RAID action on a box that has no RAID.

Root cause was a four-link chain; only the first link is architectural:

1. **`backend._detect_backend` read "sas2ircu sees no controller" as "therefore a
   PERC RAID box".** sas2ircu speaks **SAS2 only** and is structurally blind to a
   SAS3008, so an HBA330 fell through to `RaidBackend()` even though the OS — not
   the controller — owns the disks.
2. `hba_raid.enumerate_disks` then synthesised one `Disk` per controller physical
   drive with `dev = ctrl_dev`; with no PERC virtual disk present, `ctrl_dev` fell
   back to `raw[0].dev` = `/dev/sda` (hba_raid.py:522-523).
3. Its dedupe guard was `if sn and sn in raw_serials`, and `raw_serials` came from
   lsblk. **Enterprise SAS drives report no lsblk `SERIAL`** until `smart.read()`
   fills it in, and perccli's per-drive detail sections were unreadable too (the
   header regex demanded the literal word `Device`), so `sn` was `""` and the
   guard never fired.
4. Each phantom got `smart_dtype = "megaraid,<DID>"`, but `smartctl -d megaraid`
   goes through the **MegaRAID SAS ioctl**, which needs a `megaraid_sas` host. An
   HBA330 binds `mpt3sas`, so every phantom read `NOREAD` → CRITICAL.

The structural lesson: **"which vendor tool answers" is not the same question as
"who owns the storage".** perccli manages a PERC in RAID mode *and* a Dell
HBA330/H330 that hands raw disks to the OS; sas2ircu answers for a crossflashed
SAS2308 and for nothing else. b2ctl was picking a backend — enumeration, SMART
transport, lifecycle, *and* bay source, all at once — from a tool-identity probe.

## Decision

### 1. Ask for the controller's PERSONALITY before choosing a backend

New `hba_raid.is_hba_personality()` (hba_raid.py:405) answers "does perccli manage
this card while the **OS** owns the disks?". `backend._detect_backend` consults it
before falling through to `RaidBackend()` (backend.py:223-225):

```python
if hba_raid.have_tool():
    if hba_raid.is_hba_personality():
        return ITBackend(bay_source="perccli")
    return RaidBackend()
```

`_probe_hba_personality` (hba_raid.py:424) reads four signals **in this order**:

| # | signal | reads | verdict |
|---|--------|-------|---------|
| 1 | a **virtual disk exists** | `_vall_data()` | RAID — definitive |
| 2 | **personality string** `Current Personality = HBA-Mode` / `RAID-Mode` | `perccli /cN show`, every `_ctrl_indices()` | HBA / **RAID** — both authoritative |
| 3 | **driver** ≠ `megaraid_sas` (`Driver Name`, else `/sys/class/scsi_host/host*/proc_name`) | perccli / sysfs | HBA |
| 4 | fallback: **every** PD resolves to an OS block device (serial → WWN) | `blockdev.lsblk_pairs` vs `_parse_pd_rows` | HBA |

Ordering is deliberate. **Signal 1 is checked first** because the sysfs probe reads
`False` wherever `/sys` is absent (a dev laptop, the sim harness) and a real RAID
controller must never be misclassified there. Signal 3 is the decisive and free one
for the field case: no `megaraid_sas` host ⇒ `smartctl -d megaraid,<DID>` **cannot**
work, so RAID-mode enumeration is impossible by construction, not by preference. A
Dell HBA330 prints **no** personality line — it has no switch, it is IT firmware
permanently — so an *empty* signal 2 is a normal answer, not an error; a *populated*
one is believed either way (hba_raid.py:438-442). Signal 4 is reached only for a
`megaraid_sas` card with zero VDs and no personality string, and it is a per-PD test,
not a count: one drive the controller hides is enough to answer RAID.

The verdict is **memoized** (`_hba_personality_cache`, cleared by `_reset_caches`)
because the probe costs up to three perccli round-trips and perccli is slow (F-040).

### 2. `ITBackend.bay_source` — perccli is now a legitimate BAY source for an IT box

`ITBackend` gains `bay_source ∈ {'sas2ircu', 'perccli'}` (backend.py:65). This
splits three questions b2ctl used to answer with one backend flag:

| question | `bay_source='sas2ircu'` | `bay_source='perccli'` |
|----------|-------------------------|------------------------|
| who enumerates? | `hba.enumerate_disks` (lsblk) | **same** |
| how is SMART read? | direct `smartctl -a /dev/sdX` | **same** |
| who knows the bay? | `sas2ircu <c> DISPLAY` | `hba_raid.bay_map()` |

**Only the bay map moves.** The disks are still raw `/dev/sdX`, so enumeration and
SMART stay IT-style and the ZFS lifecycle is untouched. `bay_map()` delegates to
`hba_raid.bay_map` (backend.py:96-99); `attach_bays`/`get_ghost_disks` pre-fetch it
(backend.py:113, 126) so the `hba` module never probes sas2ircu behind our back.

`ITBackend.have_tool()` also **flips a default ITBackend to `'perccli'`** when
sas2ircu is blind but perccli manages the card (backend.py:70-81). `controller.mode
= 'it'` returns `ITBackend()` without any detection (backend.py:192-193), so without
this an operator who forced IT-mode on an HBA330 would lose bays entirely.

**LEDs need no special-casing.** Every drive has its own block device, so it
carries no `pd_state`/`array_type`, `locate.is_perc_pd` is False, and `blink_disk`
takes the normal ledctl → dd path.

### 3. WWN is the serial-independent join key

`common.Disk` gains `wwn` and `hba.enumerate_disks` asks lsblk for it
(`NAME,SIZE,SERIAL,MODEL,TRAN,ROTA,TYPE,WWN`, hba.py:54). `_norm_wwn` lowercases and
strips `0x` and any separator — lsblk prints `0x5000c500a1b2c3d4`, perccli prints
`5000C500A1B2C3D4`. `_match_os_disk` (hba_raid.py:485) resolves a PD to the block
device it **already** is: exact serial → the project's fuzzy `baymap.serial_match` →
WWN. The parser had to be fixed to supply the WWN at all: `_DRIVE_HDR` now accepts
**any** `Drive /cN/eE/sS …` section header, not only the literal `Device attributes`
that some perccli builds omit, and the shared `_parse_detail` backs both
`_parse_bay_map` (`SN`) and `_parse_wwn_map` (`WWN`).

### 4. Invariant — never synthesise a row an OS disk could be

RAID-mode enumeration synthesises a `Disk` for a controller PD **only** when that
PD cannot be a block device the OS already exposes. The join runs in **two passes**
(hba_raid.py:557-612) so that no drive's existence depends on `enc:slot` iteration
order:

- the VD's own block device is excluded from every join table, so it can never
  absorb a PD;
- **pass 1** (hba_raid.py:574) joins *every* non-member PD by serial, then WWN;
  a hit **tags** the real disk (`bay`, `pd_state`, `ctrl_slot`, `ctrl`), records
  the claim and never duplicates it;
- **pass 2** (hba_raid.py:592) synthesises whatever stayed unmatched — against
  the now-complete claim set. It refuses **only** a PD perccli could not identify
  at all (no `SN` *and* no `WWN`) that an unclaimed OS disk matches on both model
  (`_model_match`, a prefix compare in either direction — perccli truncates its
  Model column) and size (`_size_match`). A PD that *has* an identity which simply
  matches nothing is definitively hidden and keeps its row.

The counterpart on the ghost side: `hba.get_ghost_disks` suppresses only when no
bay-map serial matched *any* OS disk (`_matched_any`, hba.py:259) **and** the
serial-less block devices already present can account for every would-be ghost
(`len(ghosts) <= unidentified`, hba.py:253-254) — the two serial domains simply
have not lined up yet, which is a scan-ordering artefact, not a backplane that
rejected 100% of its drives. Any surplus beyond that count is reported.

Both guards are biased toward **under-reporting**, because a phantom CRITICAL row
is unreadable, unactionable and repeated per drive, whereas a missing row is still
visible in `perccli`/`lsblk`. Each now suppresses only what it can positively
account for; the first cut of both suppressed more than that — see Consequences.

### 5. The kernel's SAS transport class is the bay source of last resort

Every signal above still ends in a **serial join** for the bay label, and that is
the same weak link that produced the phantoms: lsblk publishes no `SERIAL` for
enterprise SAS drives until `smart.read()` runs, and each tool truncates serials
differently. The kernel already knows the answer without any of that. On the field
HBA330 box (`bkp02`), `/sys/class/sas_device/end_device-*/bay_identifier`:

```
bay=0  enc=0x500056b31234abff dev=sda    …  bay=6  -> sdg
bay=22 enc=0x500056b31234abff dev=sdh
bay=23 enc=0x500056b31234abff dev=sdi
bay=24 enc=0x500056b31234abff dev=NONE       <- SES enclosure processor
```

An exact 1:1 map onto perccli's `9:0`–`9:6` / `9:22` / `9:23`, **with no serial
involved**. (`/sys/class/enclosure/` is empty on that box — the SES driver is not
bound — so the `sas_device` path is the only usable one.)

`blockdev.sas_bay_slots()` (blockdev.py:46, dir constant at blockdev.py:22) returns
`{'/dev/sdX': slot}` by reading each `bay_identifier` and resolving the block device
under the **same** sysfs node (`device/target*/*/block/*`). Two properties matter:

- a node with **no** block device drops out on its own, which is how the SES
  processor at bay 24 excludes itself — the phantom-row hazard F-036 had to
  special-case for sas2ircu is solved *structurally* here, not by a filter;
- it returns `{}` when there is no SAS transport at all (pure SATA/NVMe) or when
  the backplane reports one constant bay for every drive (a useless map).

**Vendor labels always win.** `baymap.assign_sysfs_bays(disks, panels, enc, slots)`
(baymap.py:100) fills only disks that still have **no** bay and runs *after*
`assign_bays` (hba.py:216), so an R620's `sas2ircu` + `reverse_slots` output is
byte-identical to before. The slot still goes through the normal front-panel remap
in `bay_map.json`. `attach_bays` also no longer early-returns when sas2ircu is
missing (hba.py:194-216) — sysfs works with **no vendor tool installed at all**.

**The enclosure prefix is display only.** `hba._enc_hint(bm, override)` (hba.py:180)
prefers the enclosure the vendor map already labels with, then the backend's hint,
then `"0"` — so a disk that switches from a vendor bay to a sysfs bay never changes
the number the operator reads. `hba_raid.enclosure_ids()` (hba_raid.py:168) supplies
that hint from the PD table, and `ITBackend.attach_bays` borrows it when
`bay_source='perccli'` produced an empty bay map (backend.py:107-121), so labels read
`9:0`, not `0:0`. perccli **actions** are unaffected: they still take `Disk.ctrl_slot`,
the raw locator.

### Alternatives rejected

- **Ship `sas3ircu` alongside `sas2ircu`.** It would give the HBA330 an IT-native
  bay source and detection would key on "sas2ircu OR sas3ircu answered". Rejected:
  it adds a third vendor binary to `installer.py` (another pinned SHA-256, another
  Google Drive artefact, another apt prereq tier) and it only moves the boundary —
  a PERC in HBA-Mode still answers perccli and nothing else, so the tool-identity
  conflation would survive intact. Personality is the question; a fourth tool is
  not an answer to it.
- **Tell the operator to set `controller.mode = 'it'`.** This *is* the documented
  escape hatch and it stays. Rejected as the fix because (a) the default path must
  be correct on a stock Dell — the operator saw 9 CRITICAL rows before they had any
  reason to suspect detection — and (b) on its own it is not even sufficient:
  `mode='it'` skips detection entirely (backend.py:192-193), so a plain `ITBackend()`
  would probe a SAS3-blind sas2ircu and report **no bays at all**. That is exactly
  why `have_tool()` flips `bay_source` (§2).
- **Keep the dedupe on serial and just make serials arrive earlier.** i.e. run the
  SMART fan-out before enumeration so `raw_serials` is populated. Rejected: it
  inverts the layering ADR-001 settled (`core.scan` composes enumerate → attach →
  SMART), makes the read path pay a full SMART pass before it knows what to read,
  and still fails on any drive whose serial neither side reports. WWN is available
  from lsblk and perccli **for free, before SMART**, which is why it is the join key.
- **Make sysfs the PRIMARY bay source and drop the vendor map to a fallback.** It
  is the more exact of the two — an explicit device→slot edge from the kernel
  versus a fuzzy serial join — so preferring it is tempting. Rejected: the number
  it carries is the *expander's*, and `bay_map.json`'s front-panel remaps
  (`reverse_slots` on the R620s) are calibrated against the *vendor* label. Making
  sysfs primary would silently relabel every already-deployed box the day it
  shipped, for boxes whose labelling was never broken — the operator would have to
  re-learn a chassis they already know, and re-verify every panel entry, to fix a
  problem they do not have. Sysfs also covers only SAS transport, so it cannot be
  the sole source anyway. Filling **only** the gaps the vendor map left is strictly
  additive: no existing box changes, and the HBA330 gains bays it never had.
- **Label sysfs-derived bays by bare slot index, or under a synthetic enclosure
  `0`.** The enclosure number carries no addressing meaning here (perccli actions
  use `Disk.ctrl_slot`), so a plain index is the smaller change. Rejected because
  the label is the operator's *physical* instruction: they read `9:0`–`9:23` from
  `perccli` and from the iDRAC, and printing `0:7` for the drive everything else
  calls `9:7` introduces a second numbering to reconcile while standing at the
  rack. Worse, it would not even be stable within one box — a disk would appear to
  change bay the moment its serial arrived and the vendor map took over. Hence
  `_enc_hint`'s preference order, which keeps the vendor enclosure number on every
  row regardless of which source produced the slot.

## Consequences

- **The detection contract changed.** perccli answering no longer implies RAID. An
  existing PERC RAID box with a virtual disk is unaffected (signal 1 is definitive
  and checked first); an HBA330/H330 or a PERC in HBA-Mode now lands on
  `ITBackend(bay_source='perccli')` and enumerates once, with working direct SMART.
- **What this revises, and what it does not.** ADR-001's *deployment* and *layering*
  decisions stand unchanged; only its detection corollary — the last branch of
  `_detect_backend`, where "sas2ircu reported zero controllers" fell straight through
  to `RaidBackend()` — is revised. ADR-001's F-010 rule (sas2ircu must report an
  ACTUAL controller table, not merely print output) is untouched and still runs
  first. Nothing in ADR-002/003/004 is affected: burn-in, maintenance and the ZFS
  lifecycle never depended on which tool answered, only on the backend's name.
- **`backend.get_backend().name` is still `"it"` for such a box**, so
  `raid_actions._require_raid()` keeps refusing `create-vd` / `assign-perc` /
  `raid-replace` / `offline` / `del-vd` there. Correct for a real HBA330, and
  signal 4 now errs the *other* way (an unresolvable PD reads RAID), so the
  residual risk is a JBOD-only PERC keeping hardware-RAID verbs it does not need
  — not an operator being locked out of the ones they do (below).
- **New `Disk.wwn` field and one extra lsblk column.** No new external tool, no new
  state file, no new subprocess on the scan path — the WWN rides the lsblk call
  `hba.enumerate_disks` already makes, and the WWN map rides the `eall/sall` text
  `enumerate_disks` already fetches once per controller (F-040/F-041 preserved).
- **A box can now have bays with no vendor tool at all.** §5 adds no binary, no
  state file and no subprocess — `sas_bay_slots()` is a sysfs walk — but it does
  widen where bays appear: an HBA330 whose perccli build prints no per-drive detail
  section, and a plain IT box with neither sas2ircu nor perccli installed, both get
  a full bay column where v0.18.0 showed none. Nothing **loses** a bay: sysfs only
  fills disks the vendor map left empty, so `sim` (IT and RAID) and the R620s render
  byte-identically to before.
- **Three guards were narrowed by adversarial review before this ADR shipped.** The
  invariant in §4 and the personality contract in §1 are unchanged in intent; what
  changed is how much each is allowed to suppress. All three first cuts refused more
  than their evidence justified, and all three failure modes were *silent*:
  - **The anti-duplication refusal is now scoped to identity-less PDs, and is
    order-independent.** It previously fired for PDs perccli **had** identified
    (a `SN`/`WWN` that matches no OS disk is proof the drive is hidden, not a reason
    to drop it) and decided *mid-loop*, against a `claimed` set holding only the PDs
    iterated so far — so on a RAID box with identical drives, a hidden UGood/Failed
    drive at a **lower** enc:slot than an identical JBOD sibling vanished, and
    reversing the two slot numbers brought it back. That is not hypothetical: it is
    the mixed layout b2ctl's own `assign-perc` set-JBOD flow creates. The two-pass
    join (§4) fixes both — pass 1 completes the claim set, and pass 2 refuses only a
    PD with **no identity at all**, now also gated on size (`_size_match`) as well as
    model. `_model_match` additionally gained a minimum matched length
    (`_MODEL_MIN = 8`, hba_raid.py:214): a bare prefix compare made
    `("S", "Samsung SSD 870 EVO 1TB")` **True**, i.e. one severely truncated Model
    column could suppress arbitrary drives. Sizes are parsed as **powers of 1024**
    (`_pd_size_bytes`, hba_raid.py:239) because perccli prints binary sizes under
    decimal labels — `2.182 TB` for a 2 400 476 274 688-byte drive, `953.869 GB` for
    an 860 PRO 1TB — and an unknown size returns `True`, so the check can only ever
    narrow the suppression, never widen it.
  - **An explicit personality string is authoritative in BOTH directions.** Signal 2
    was consulted only positively (`startswith("HBA")`), so `Current Personality =
    RAID-Mode` fell through to the heuristic. A freshly-wiped H730P — no VD yet, i.e.
    exactly the state *before* `raid-create` — was therefore classified HBA and locked
    the operator out of every `raid-*` verb, with no way back short of editing
    `controller.mode` in `/etc/b2ctl/config.json`. A controller that names its own
    personality is now believed either way (hba_raid.py:438-442). The heuristic behind
    it was replaced as well: `len(os_disks) >= len(pds)` counted *every* non-excluded
    lsblk disk, so an unrelated BOSS-S1 mirror plus 2 NVMe could outvote two hidden
    PERC drives; signal 4 now requires **every** PD to resolve to an OS block device
    by serial or WWN, and any unresolved PD means the controller is hiding it ⇒ RAID.
    Personality and driver are read across all `_ctrl_indices()`, not a hardcoded
    `/c0`.
  - **The ghost guard no longer blanks the ghost list outright.** It returned `[]`
    whenever every bay-map entry ghosted and any OS disk lacked a serial — which on
    serial-less SAS drives is *always* true at the point `core.scan` computes ghosts
    (core.py:42, before the SMART fan-out), permanently disabling `OS_REJECTED`
    detection on the very hardware this ADR targets: a drive the OS genuinely rejected
    (foreign RAID metadata) was swallowed with the phantoms — no GHOST row, no
    CRITICAL, no `[u]dev rescue` prompt. It now suppresses only when no bay-map serial
    matched **any** OS disk *and* the serial-less block devices already present can
    account for every would-be ghost; a surplus is reported rather than hidden
    (hba.py:252-255).
- **Known limitations, accepted and operator-visible:**
  - **Signal 4 answers RAID when a PD cannot be resolved at all.** A `megaraid_sas`
    card with zero VDs, no personality string, and one drive whose serial *and* WWN
    neither tool publishes reads as "the controller hides it" ⇒ RAID. That is the
    safe direction — nothing is hidden and every `raid-*` verb stays available — but
    an all-JBOD PERC in exactly that state enumerates RAID-style; `controller.mode =
    'it'` is the escape hatch, and `have_tool()` will flip its `bay_source` to perccli
    (§2). The verdict is memoized for the process.
  - **A PD with no identity at all is still suppressed** when an unclaimed OS disk
    matches it on both model and size. This is the HBA330 case the refusal exists for
    and there is nothing left to distinguish the two; the drive remains visible in
    `perccli` and `lsblk`.
  - **The sysfs bay source covers SAS transport only, and its enclosure prefix is a
    convention.** Drives on an AHCI port and NVMe expose no `end_device-*` node (NVMe
    keeps its PCIe-address bay until relabelled), and a backplane that reports one
    constant `bay_identifier` for every drive is discarded as useless. If perccli
    reports **more than one** enclosure while the bay map is empty, no hint can be
    chosen and labels fall back to `0:<slot>` (backend.py:114-120) — cosmetic, and
    fixable with a `bay_map.json` front panel.
- Version bumped to **0.19.0-itmode**; unit suite green (**727 passed, 14
  subtests**); `python3 -m py_compile b2ctl/*.py` clean; the sim harness shows both
  backends unchanged (`sim/simctl init && sim/run status`, and `sim/simctl mode raid`).
