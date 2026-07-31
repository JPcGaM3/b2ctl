# ADR-002 — Background, re-attachable multi-disk burn-in

- **Status:** Accepted — **command surface superseded-in-part by [ADR-004](ADR-004-unify-maintenance.md)**
- **Date:** 2026-07-06
- **Version:** v0.10.0-itmode
- **Relates to:** ADR-001 (layering), CLAUDE.md §7 Task-B/§8 (sim), runbook STEP 02–03

> **Superseded-in-part by ADR-004 (v0.18.0):** the burn-in *engine* below —
> `run_multi`, the detached `badblocks` scan, the `burnin.json` re-attach state,
> the live view, and the "exits 0 once started" semantics — all still stand. Only
> the *command surface* moved: the top-level `b2ctl burnin` verb and the watch
> `[b]` key are GONE; disk vetting is now `[m]aint → [3] health-check` and
> `b2ctl maint health <dev…>` / `--status`. Read "`b2ctl burnin`" below as
> "`b2ctl maint health`".

## Context

Until v0.9.0, burn-in (`burnin.py`) was **one disk at a time and fully
synchronous**: `run()` started a `smartctl -t long` self-test and then blocked the
whole `watch`/CLI process in a foreground poll loop (`_wait_selftest`), and an
optional `badblocks` surface scan blocked for *hours* with `timeout=None`. On real
hardware this froze the interactive `watch` loop, showed no progress in
`status`/`watch`, and gave no ETA.

The operator asked for: (1) select **multiple** disks like pool-create (`1 2 3`),
(2) run the long self-test on all, (3) optionally `badblocks` on all, and (4) a
**live progress bar with time remaining** for both the self-test and the scan —
and to be able to walk away while it runs.

This is the first time b2ctl needs a **long-running operation that outlives the
foreground command** and must be observed from a *later* process — everything
else in the tool is a synchronous `run()`/`run_check()` subprocess.

## Decision

**1. Two progress sources, reusing what we already fetch.**
The SMART self-test runs on the drive's own firmware; b2ctl only *polls*
`smartctl -a`, which **already** contains the execution status (`% of test
remaining`) and the recommended polling time (the ETA basis). We factored a pure
`parse_selftest(out)` used by BOTH `selftest_status()` and `smart.read()`, so the
status table's `TEST xx%` costs **zero extra subprocess**. `badblocks` is a *host*
process, so it is spawned detached and its stderr logfile is tail-parsed for
`% done`.

**2. Background, not synchronous.**
`run_multi()` starts every selected disk's self-test (`smartctl -t long`, returns
immediately) and, if requested, a **detached** `badblocks`
(`subprocess.Popen(..., stderr=<logfile>, start_new_session=True)`). It then shows
a `live_view()` that redraws per-disk self-test + scan bars + ETA. **Ctrl-C
detaches** (saves state, leaves everything running) — it never aborts. Leaving is
the expected exit; the operator re-attaches later.

**3. A small state file for re-attach.**
`os.path.join(safety.LOG_DIR, "burnin.json")` (records keyed by **serial** — stable
across `/dev/sdX` renames — holding dev/bay/dtype/kind/do_scan/scan_pid/scan_log/
started), plus per-disk `scan-<serial>.log`. The path is resolved **at call time**
from `safety.LOG_DIR`, so the sim harness's existing `safety.LOG_DIR` monkeypatch
(`sim/run`) redirects it to `sim/var/` with no b2ctl change (consistent with the
audit log). `b2ctl burnin --status` (and `[b]` in watch) re-opens the live view
from this file; `_finish()` prunes completed records.

**4. Liveness that survives zombies.**
`_pid_alive(pid)` uses `os.kill(pid,0)` **and** reaps our own finished children
with `waitpid(WNOHANG)` — a bare `os.kill` reports a zombie as still "existing",
which would strand a finished scan as forever-running inside the long-lived
`watch` process.

## Consequences

- **Exit-code semantics change.** `b2ctl burnin <disk>` now exits `0` once the
  tests are *started*; the PASS/WARN/FAIL verdict is surfaced later in the live
  view / `--status`, not in the exit code. The old single-disk synchronous
  FAIL→exit-1 is gone (inherent to backgrounding). A future `--wait` could restore
  a synchronous mode if scripting needs it.
- **Re-entrancy is mandatory.** `run_multi` polls `selftest_status` first and never
  restarts a disk already under a self-test (extends the F-011 "don't address the
  wrong/absent test" caution).
- **No auto-repaint of the `watch` table.** The live progress lives in its own
  view; the wide `status` table only shows `TEST xx%` on refresh, preserving the
  Task-A prompt-clutter fix. A continuously auto-refreshing monitor remains the
  deferred `b2ctl top` (Task E).
- **Retired code:** the blocking `_wait_selftest` and the blocking `read_scan`
  are removed; `start_scan`/`scan_progress`/`live_view` supersede them.
- **Sim:** a fake `sim/bin/badblocks` emits incremental progress; the fake
  `smartctl` seeds a wall-clock self-test (`_simstate.selftest_seed/selftest_pct`)
  so `sim/run status` and the live view exercise the whole path with no hardware.
- **Safety unchanged:** still read-only (self-test trigger + `badblocks -sv`, never
  `-w`), still refuses `in_pool` disks, still acts by-id where it matters.
