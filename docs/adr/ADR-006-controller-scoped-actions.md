# ADR-006 — Controller state is a first-class disk attribute, and controller-scoped destructive actions confirm at controller scope

- **Status:** Accepted
- **Date:** 2026-07-30
- **Version:** v0.20.0-itmode
- **Relates to:** ADR-005 (which backend owns the disks — this ADR is about what
  the *owning controller* still forbids once that is settled), ADR-001 (two
  co-equal backends), CLAUDE.md §9 (safety rules — this ADR extends the confirm
  rule, it does not weaken it).

## Context

On a PERC RAID box (`vd0` = `MainSSD` raid1, plus one loose 1.92 TB SATA SSD at
`32:7`), an operator ran `watch` → `[a]ssign` → `[2] Use for ZFS / software RAID
(set JBOD)` on a drive b2ctl had graded

```
- bay 32:7 /dev/sda (SAMSUNG MZ7LH1T9HMLT-00003, SN S4F2NY0KA04123) [CONFIG]
    - available (Unconfigured Good) — set JBOD for ZFS, or add to a RAID volume (raid-create)
```

The controller refused:

```
  ✗ failed: Controller = 0
Status = Failure
Description = Set Drive JBOD Failed.

Detailed Status :
===============

------------------------------------------------
Drive      Status  ErrCd ErrMsg
------------------------------------------------
/c0/e32/s7 Failure   255 Operation not allowed.
------------------------------------------------
```

The operator diagnosed it **outside b2ctl**, from a separate tool: the drive
carried a **foreign configuration** — RAID metadata written by a previous
controller or array. MegaRAID/PERC firmware locks a foreign physical drive out of
*every* state transition (`set jbod`, `add hotsparedrive`, `add vd`) until that
config is imported or discarded.

### Root cause: two independent axes collapsed into one

perccli's PD table carries two orthogonal facts:

| column  | question | values |
|---------|----------|--------|
| `State` | is the drive in a virtual disk? | `Onln` / `Rbld` / `UGood` / `JBOD` / `Failed` … |
| `DG`    | which drive group owns it? | a number, `-` (none), **`F` = foreign** |

`hba_raid._parse_pd_rows()` had always captured `dg` (hba_raid.py:288). But every
enumerate path copied only `state` onto the `Disk`:

```python
d.pd_state = pd["state"]      # hba_raid.py:553 / :586 / :613 — dg dropped here
```

`Disk` had no vocabulary for "the controller forbids this drive", so the whole
stack downstream reasoned from `pd_state` alone and reached a confident wrong
answer at every step:

1. `common.assess()` graded it `CONFIG` — *"available (Unconfigured Good) — set
   JBOD for ZFS"*.
2. `watch._cmd_assign` accepted it into `raid_avail` (`pd_state.upper() in
   ("UGOOD","READY","UGUNSP")`).
3. `raid_actions.assign_perc` offered `[2] set JBOD`.
4. The refusal was printed as a raw vendor dump, uninterpreted.

b2ctl advertised as ready a drive the controller considers locked, then reported
the refusal in the controller's words rather than the operator's.

### The second problem: the fix has no per-drive form

MegaRAID exposes foreign configs only through `/cN/fall` — **f**oreign **all**.
There is no `/cN/eE/sS ... foreign` selector. `perccli /c0/fall del` discards
*every* foreign config on controller 0, not the one drive the operator selected.

That collides with CLAUDE.md §9, which requires "explicit `[y/N]` naming device +
pool + operation". Every prior b2ctl action targets one disk or one pool, so §9's
vocabulary simply has no case for an action whose blast radius is *wider than the
thing the operator picked*.

## Decision

**1. Controller state that forbids an action is a `Disk` attribute, parsed on the
scan path.**

`Disk.pd_foreign: bool`, set by `hba_raid._is_foreign(row)`
(`dg.strip().upper() == "F"`) at all three sites that already copy `pd_state`: the
VD-member loop, the PASS 1 OS-exposed tagger, and the PASS 2 synthesiser. One
authority for the test, so no path can forget it. Zero extra subprocesses — `dg`
is in text the scan already fetches.

`assess()` tests `pd_foreign` **before** the hidden-PERC branch, so it covers a
foreign drive whether it is hidden behind the VD's block device or already
exposed. Level stays `CONFIG`, not `CRITICAL`: the drive is healthy; its
*configuration* is what blocks it.

**2. b2ctl refuses what the firmware will refuse, before calling the firmware.**

`raid_actions._refuse_foreign(targets, what)` gates `[2] set JBOD`, `[3] create
volume` and `[4] hot spare` in both `assign_perc` and `assign_perc_batch`. It is
**all-or-nothing** in batch: if any pick is foreign the whole selection is
rejected. A partial batch reporting "2 ok / 1 failed" reproduces exactly the
ambiguity this ADR exists to remove.

**3. When the firmware does refuse, translate it.**

`hba_raid.explain_error(out, d=, controller=)` matches `operation not allowed` /
`errcd 255` and prints the causes in the order they bite, marking the first hit:

```
  why: the PERC refuses this transition. Checked:
    - foreign config on 32:7    -> YES  <-- this
    - controller 0 JBOD policy  -> ON
    - Support JBOD              -> Yes
  fix: assign -> [5] Foreign config, or `perccli /c0/fall show` then `... del`
```

It reads `pd_foreign` **and** falls back to `foreign_bays()`: the DG column is the
free signal, but not every perccli build prints it, and `/cN/fall show` is
authoritative. Both probes run on the error/pre-flight path only — never in
`core.scan()`, where perccli's cost is why its probes are memoised (F-040/F-041).

**4. A controller-scoped destructive action confirms at controller scope.**

This is the general rule ADR-006 establishes, beyond this one feature:

> When the vendor tool offers no per-target form of a destructive operation:
> **(a)** print the complete affected set before asking; **(b)** name the *real*
> scope in the confirm — the controller, not the disk the operator selected;
> **(c)** require the house type-the-name second confirm on the discard form.

Concretely, `raid_actions._run_foreign(kind, controller, rows)`:

```
  FOREIGN CONFIG on /c0:
    DG EID:Slot Type   State  Size
     0 32:7     RAID0  Optl   1.746 TB
  WARNING: perccli /c0/fall acts on the WHOLE controller — there is no
  per-drive form. Both actions below hit all 1 drive(s) listed above.
    [i] import — bring that foreign array back online on this controller
    [c] clear  — DISCARD it; its drives drop to Unconfigured-Good
    [s] skip / decide later
  action> c
  CLEAR ALL foreign config on controller 0 (1 drive(s))? the array becomes unimportable [y/N] y
  type the controller number '0' to confirm> 0
```

`_run_foreign` is the single implementation, shared by watch's `[5]` and the CLI
verb `b2ctl raid-foreign --import|--clear`, so the guards cannot drift apart.
Audited via `safety.begin_op("raid_foreign_clear", …, bay=f"/c{N}/fall", …)` — the
audit trail records the *scope*, not a misleading single-disk target.

**5. b2ctl reports controller policy; it does not change it.**

`jbod_capability()` reads `Support JBOD` / `JBOD` from `perccli /cN show all` so
the *other* cause of `Operation not allowed` is nameable. b2ctl deliberately never
runs `perccli /cN set jbod=on`: it is a controller-wide policy change, it is not
required to complete any b2ctl workflow, and the operator who wants it should make
that decision explicitly. The error text tells them the exact command.

## Alternatives rejected

- **Grade a foreign drive `CRITICAL`.** It is a healthy drive with an
  inconvenient configuration. Escalating it would compete with real failures in
  the table and train the operator to ignore red.
- **Let the action run and interpret the failure afterwards.** Cheaper, but it
  keeps advertising a drive as available and makes every diagnosis reactive. The
  DG column is already in hand at scan time; refusing up front costs nothing.
- **Auto-clear the foreign config as part of `set JBOD`.** Convenient and
  unacceptable: `/cN/fall del` can destroy an importable array the operator
  intended to recover, and it reaches drives they never selected.
- **Skip foreign picks in a batch and proceed with the rest.** Rejected under
  decision 2 — see the all-or-nothing rationale.
- **Offer `set jbod=on` from inside b2ctl.** Rejected under decision 5.
- **Detect foreign only via `/cN/fall show`.** Authoritative but a per-scan
  perccli round-trip on a slow tool, and it would leave the *table* silent
  between scans. The DG column is free; `fall show` stays the fallback and the
  detail view.

## Consequences

- New surface: `Disk.pd_foreign`; `hba_raid.foreign_config / foreign_bays /
  jbod_capability / explain_error / import_foreign / clear_foreign`;
  `raid_actions._refuse_foreign / _print_foreign / _run_foreign / _foreign_menu /
  _fail / foreign`; `watch [a]ssign → [5]`; CLI `b2ctl raid-foreign
  [--import|--clear] [-c N]`.
- `raid-foreign` joins `cli._ROOT_EXEMPT` with the same shape as `maint`: the bare
  form is a read-only `fall show` (§9 read path, no root); `--import`/`--clear`
  require root.
- `perccli`/`perccli64` were already in `safety.WRITE_CMDS`, so `--dry-run` gates
  the two `fall` mutations with no change to the allowlist.
- **Known limits, accepted:**
  - A perccli build that prints no `DG` column leaves `pd_foreign` False, so the
    *table* will not flag the drive. The refusal still fires from `foreign_bays()`
    on the error path, and `b2ctl raid-foreign` shows it — degraded, not wrong.
  - `foreign_config()` keys on the presence of enc:slot **rows**, never the
    `Status` line: several perccli builds answer "no foreign configuration
    present" with `Status = Failure`, and keying on that would report a foreign
    config on every healthy controller.
  - `import` is offered without a preview of what the imported array will *do* to
    the pool layout, because perccli does not provide one before the fact. The
    affected drive list is shown; the operator owns the rest.
  - A batch selection spanning two controllers is refused for `[5]` — a foreign
    config is per controller and there would be two different sets to show.
- Version bumped to **0.20.0-itmode**; unit suite green (**766 passed, 22
  subtests**, up from 727/14); `python3 -m py_compile b2ctl/*.py` clean; the sim
  harness models the whole path end to end (`simctl foreign <bay>` → `DG=F`,
  `/cN/fall show`, the real `ErrCd 255` refusal from `set jbod`, and
  `/cN/fall import|del`) — the fake controller's first modelled failure.
- **Not yet proven on hardware.** The `Operation not allowed` → success transition
  after a clear can only be demonstrated on the real PERC; lab + sim prove the
  flow and the refusals.
