# ADR-007 — b2ctl has a machine contract: a versioned JSON envelope, emitted by every read verb

- **Status:** Accepted
- **Date:** 2026-07-31
- **Version:** v0.22.0-itmode (phase 1) · v0.23.0 (phase 2) · v0.25.0 (phase 3)
- **Relates to:** ADR-006 (controller-scoped confirms — the confirm model this
  ADR will have to express for machine callers, phase 2), ADR-001 (module
  layering), CLAUDE.md §9 (safety rules — unchanged by this ADR, which adds no
  mutating surface).

## Context

b2ctl is being driven by an **MCP server and a web UI** in addition to an
operator at a terminal. That makes two demands the tool was never built for:
every command must be callable by a program, and every value must come back as
data.

Where it started:

- **One JSON surface in the whole product**, `status --json`, implemented as
  `json.dumps([vars(d) for d in disks], default=str)` (cli.py:63-64). No version,
  no pools, no volumes, no summary — and `vars()` means every internal field is
  on the wire. `Disk.pool_token` (an internal `zpool status -P` leaf token) and
  the transient `selftest_running`/`selftest_pct`/`selftest_eta` were all
  published by accident, and any rename during a refactor silently reshaped a
  live API.
- **Errors are colour-coded text on stdout** plus an exit code. A client can see
  *that* something failed, never *what*.
- **Every lifecycle verb blocks on a prompt.** `zfs_actions.py` is a thin wrapper
  over `watch._cmd_*`, which has 67 `input()`/`ask()`/`_confirm()` call sites;
  `raid_actions.py` has 25. Only `scrub`/`trim` accept an argument that skips one
  question. Called from MCP, they hang forever.

The third point is a much larger change than the first two, so this release is
deliberately **read-only** — the machine contract lands first and carries zero
risk to storage; non-interactive mutation follows in v0.23.0 (see *Phase 2*).

## Decision

### 1. One envelope, identical on success and failure

```json
{ "schema_version": 1, "ok": true,  "command": "status",
  "data": {...}, "warnings": [], "error": null }

{ "schema_version": 1, "ok": false, "command": "destroy",
  "data": null, "warnings": [],
  "error": { "code": "POOL_NOT_FOUND", "message": "no pool named tonk" } }
```

The keys never vary by outcome, so a client parses once and branches on `ok` and
`error.code`. `message` is human text and is free to be reworded; **no client may
branch on it**. Exit codes stay 0/1 — the envelope carries the detail, the exit
code stays useful for shell scripts.

`b2ctl/jsonout.py` is the single authority: `emit(command, data, warnings=)` and
`fail(command, code, message, data=)`. Nothing else builds an envelope.

Error codes are a closed, stable set: `NO_BACKEND`, `TOOL_MISSING`,
`POOL_NOT_FOUND`, `DISK_NOT_FOUND`, `NEEDS_ROOT`, `INVALID_ARG`, `PARSE_ERROR`,
`UNSUPPORTED`.

`fail()` may still carry `data` — a partial result is often exactly what lets a
client explain the failure.

### 2. `schema_version` bumps only on a break

Adding a key is backward compatible and **keeps** the version. Removing or
renaming a key, or changing what one means, bumps it. A client pins the major
behaviour it understands and can treat unknown keys as additive.

This is the first thing in b2ctl that outside code depends on and that cannot be
changed freely. Saying so explicitly, in a constant, is the point.

### 3. Explicit projections, never `vars()`

`b2ctl/schema.py` holds `disk_json` / `pool_json` / `volume_json` /
`backend_json`, each built from a named field list. Publishing a field becomes a
decision someone made, and adding an internal field to a dataclass can no longer
change the wire format by accident.

Deliberately **not** on the wire: `pool_token` (internal), `selftest_running` /
`selftest_pct` / `selftest_eta` (transient mid-scan progress — long-running
progress gets its own verb in phase 2), `spare_replacing` (derivable, shape not
settled).

### 4. `--json` is global, and it implies total stdout silence

`--json` sits beside the existing global `--dry-run`, so it works on every verb
rather than only where someone remembered to add it.

**In JSON mode the envelope is the only thing on stdout.** Any stray `print()`
corrupts the stream. The read path was audited: `baymap.py` (×2, on an unreadable
or legacy `bay_map.json`) and `spec.py` (×1) wrote to stdout; `backend.py`'s hint
already went to stderr. They now route through `common.warn()`, which prints as
before in terminal mode and appends to the envelope's `warnings[]` in JSON mode.

That is why `warnings` is a first-class envelope key rather than an afterthought:
b2ctl genuinely has non-fatal things to say, and a machine caller has to receive
them as data instead of losing them.

`safety.py`'s ten stdout writes are all on the mutation path and are untouched
here; they move behind `common.warn()` in phase 2.

### 5. Read verbs are granular, not just one blob

`status` returns everything (`backend`, `disks`, `pools`, `volumes`, `summary`)
for a single-call client. Alongside it, `disks`, `pools`, `volumes` and `bays`
each return one slice, so an MCP tool polling pool health does not pay for a full
SMART scan. All of them reuse the existing `core.scan` / `core.scan_light` /
`zfs.list_pools` / `backend.raid_volumes` / `core.assemble_storage` /
`maint.load_events` paths — no new probing was added for the machine contract.

## Alternatives rejected

- **Bare data, no envelope** (`status --json` returns a plain array, as today).
  Smaller output, but failures fall back to exit code + stderr text, and there is
  nowhere to put `warnings` or a version. The moment a client needs to tell
  "empty result" from "backend missing", it has to parse prose.
- **Errors on stderr as JSON, data on stdout.** Two streams to correlate, and
  every caller must read both. One envelope on one stream is simpler to consume
  and impossible to half-read.
- **Auto-generate the projection from the dataclass** (`dataclasses.fields`).
  Zero maintenance, but it recreates the exact defect this ADR exists to fix: a
  new internal field would silently appear on the wire.
- **A `--format=json|table` enum instead of `--json`.** More general, no current
  second consumer. `--json` matches `--dry-run`'s existing shape.
- **Ship read and write together.** Rejected on sequencing: mutation needs a
  non-interactive form for ~92 prompt sites plus a confirmation model that
  reinterprets §9 for a caller with no human at the terminal. Read-only is
  independently useful, independently reviewable, and cannot damage an array.

## Consequences

- **`status --json` changes shape — a deliberate break at `schema_version: 1`.**
  It returned a bare array of disk dicts; it now returns the envelope, with the
  disks under `data.disks` and additional fields per disk. There is no compatible
  transition because the old output had no version to negotiate with. Documented
  in both user guides and the DevOps guide.
- New public surface: `b2ctl/jsonout.py`, `b2ctl/schema.py`, the global `--json`
  flag, and the `disks` / `pools` / `volumes` / `bays` verbs.
- `common.warn()` / `take_warnings()` become the way any module reports a
  non-fatal condition on the read path. Direct `print()` on that path is now a
  defect.
- **Known limits, accepted:**
  - The contract covers **read verbs only**. Mutating verbs still prompt, so an
    MCP server must not call them until phase 2. They are not hidden or disabled
    — an operator uses them exactly as before.
  - `backend_json()` probes optionally and must never raise or block: a client
    asking what backend is active on a box with no controller gets `null` fields,
    not an exception.
  - `warnings[]` is best-effort. It captures what b2ctl chooses to route through
    `common.warn()`; a vendor tool writing to our stdout directly would still
    corrupt the stream, which is why every subprocess is captured rather than
    inherited.
- Version bumped to **0.22.0-itmode**.

## Phase 2 (v0.23.0) — mutation, and what `--confirm` means for §9

### The confirm model

§9 requires "an explicit `[y/N]` naming device + pool + operation" for every
mutating action. With no human at the terminal that sentence needs an
interpretation, and this is it:

| invocation | behaviour |
|---|---|
| no `--confirm` | **unchanged** — b2ctl prompts exactly as it does today, including the type-the-pool-name second gate |
| `--confirm yes` | confirms auto-approve; the operator stated intent once, for this command |
| `--confirm <target>` | same, **and** the type-the-name gate is satisfied only when `<target>` matches what is about to change |

§9 is preserved rather than weakened: intent is still explicit, still required,
and still refused by default. What moves is *when* it is given — once per
command instead of once per prompt. `--confirm <target>` is strictly stronger
than `yes`, because a caller that names what it is destroying cannot have that
intent silently applied to a different pool by a mis-parsed command.

### One gate, not ninety-two edits

`watch.py` contains **exactly one `input()`**, inside `_ask()`; its 38 `_ask`,
28 `_confirm` and 5 `_confirm_op` sites all funnel through it. `raid_actions.py`
had 10 direct calls. So the whole surface is served by
`common.ask`/`confirm`/`confirm_target`, which the modules now delegate to.

**An unanswered prompt is an error, never a guess.** `common.ask(prompt,
default=, hint=)` returns `default` when the prompt documents one (`[raid1]`,
"blank = global spare"), and otherwise raises `common.NonInteractive`, which
`cli.main` turns into an `INVALID_ARG` envelope naming the prompt and the
argument to supply. Defaulting an unanswered "which disk?" on a destructive path
is how you destroy the wrong one.

### Mutating verbs under `--json`

Mutating commands narrate as they work — confirm boxes, resilver bars, per-step
results — all to stdout, which would shred the envelope. Read verbs build their
own envelope and are marked `emits_json=True`; everything else runs inside
`cli._json_mutation`, which captures stdout and returns it as `data.log`, with
`ok` from the exit code and `OP_FAILED` when the command did not complete. That
keeps the narration available to a web UI without rewriting several hundred
`print()` calls. `safety.py`'s writes moved behind `common.warn()` for the same
reason.

`watch` itself has no machine form and returns `UNSUPPORTED` under `--json`.

### Long-running operations

Scrubs take hours; a request cannot be held open. Mutating verbs return as soon
as the operation is started, and `b2ctl progress --json` polls: a pure read of
state the kernel and controller already publish (`zfs.poll_scrub_status`,
`poll_trim_status`, `hba_raid.rebuild_progress`, burn-in's state file), so it is
side-effect-free per §9.

`poll_scrub_status` gained an explicit `in_progress` key. It is **not** the
inverse of `completed`: the "scrub repaired …" line persists until the next
scrub, so a pool that has never been scrubbed reports `completed=False` as well —
and `progress` announced a phantom scrub on every such pool until the positive
signal existed.

## Phase 3 (v0.25.0) — the envelope survives the abnormal exits

Phases 1 and 2 made the contract hold whenever a command ran to completion. Phase
3 is about the paths where it does not. **A contract that only holds on the happy
path is not a contract** — an MCP client cannot branch on an answer it never got.

### The envelope is emitted from `main()`, not from each verb

`common.die()` is `sys.exit(1)`, and **`SystemExit` is not an `Exception`
subclass**, so `main()`'s handlers missed it entirely. On a box with neither
sas2ircu nor perccli, `backend.get_backend()` dies inside `core.scan()` and
`b2ctl --json status` wrote **zero bytes** to stdout. `main()` now catches
`SystemExit` and, separately, a final `Exception`; both re-raise untouched without
`--json`, so the human face is byte-identical.

The catch belongs at the single dispatch point, not per verb: every verb reaches
the backend through `core.scan()`/`scan_light()`, and a per-verb guard would be
the same edit forty times with forty chances to forget one.

stderr is redirected **under `--json` only**, so `die()`'s own sentence becomes
`error.message` instead of being lost — passed through `common.strip_ansi`,
because a machine caller must not receive terminal colour codes inside a JSON
string. That helper is now shared with `warn()`, which strips the same way for
`warnings[]`.

SIGINT gets an envelope too. The human path keeps exit 130 (the conventional SIGINT
code operators and scripts rely on); ADR-007's 0/1 rule is for `--json`.

### New rule: no verb under `--json` may block on a live view

`maint health --status --json` — the one verb `_needs_root` advertises as the safe
read-only form — **never returned**. Tagged `emits_json=False`, it routed into
`_json_mutation`, which runs the handler inside a redirected stdout while burn-in's
live view redraws in a `while True` loop: the request hung forever and the capture
buffer grew without bound.

The fix is not to refuse the verb. Burn-in has been **non-blocking by design since
ADR-002** — it exits 0 once the tests are *started*, and the verdict is read later
from `--status`. So the live view is the interactive convenience on top, and
`burnin._unwatched()` simply skips it when nobody is watching. Starting a
health-check under `--json` stays machine-callable.

### Long operations return "started", and `None` is a third state on purpose

This section's existing promise — *"Mutating verbs return as soon as the operation
is started"* — was **false for resilvers** until v0.25.0. `watch._wait_resilver`
polled to completion, so `replace`/`swap`/`offload` held a `--json` request open
for the hours a resilver takes.

It now returns `None` when there is nobody watching. Deliberately not a bool:

- `True` would let the caller run `_detach_if_lingers()` **while the resilver is
  still running**, on a member that may hold the only copy of unreconstructed
  blocks. That is the §9 data-loss path every guard in `watch.py` exists to prevent.
- `False` would report a failure that did not happen, and a machine caller would
  retry a `zpool replace` that is already in flight.

All four call sites record the op as a success (`zpool replace` did succeed), skip
the detach and the pull LED, and point at `b2ctl progress`. The old member staying
attached is the conservative state; `_detach_if_lingers` runs on a later
interactive pass. `raid_actions._wait_rebuild` needs no equivalent — F-144's
non-interactive abort sits in front of its only caller.

### The closed error set is now fully reachable

Four of the nine codes were dead: every domain failure collapsed to `OP_FAILED`, so
a client could not tell *"you named something that does not exist"* from *"the
operation ran and failed"*. `cli._mutation_precheck` resolves the target **before**
`_json_mutation` takes over — a named pool against `zfs.list_pools()`, a named disk
against `core.scan_light()`.

One rule is explicit, and it is F-143's: a **silent** `zpool` reports
`TOOL_MISSING`, never `POOL_NOT_FOUND`. `list_pools()` raises `ZfsUnavailable`
rather than returning `[]`, and that propagates past the precheck untouched.
*"I could not look"* must never be reported as *"it is not there"* — the same
mistake that made b2ctl offer a live boot mirror as a free disk.

### Where this met F-144

An exit code is only worth reading if the verb is honest about what it did.
`_cmd_offload` used to return `True` unconditionally after a spare-less offload,
so `ok:true` was reported on a pool that had just gone DEGRADED and stayed there.
Machine-callable and truthful are the same requirement, reached from two sides.

## Earlier notes (superseded by the sections above)

- A non-interactive form for every prompt in `watch._cmd_*` (67) and
  `raid_actions` (25); the interactive flow stays the default when the flags are
  absent.
- **Confirm model, as decided:** `--confirm yes` proceeds without asking. **Omit
  it and b2ctl prompts exactly as today**, requiring the operator to type the
  `<target>`. Accepting `--confirm <target>` as well costs nothing and is
  stricter for callers that want it. This ADR gains a section stating how that
  satisfies §9 when there is no human at the terminal.
- Long-running operations return `{op_id, state}` immediately instead of
  blocking; a new `progress` verb polls, reusing `zfs.poll_scrub_status` /
  `poll_trim_status`, `hba_raid.rebuild_progress` and burn-in's `--status` state
  file.
- `safety.py`'s stdout writes move behind `common.warn()`.
