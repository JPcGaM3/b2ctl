# FEATURE — multi-disk background burn-in with live progress (v0.10.0-itmode)

Blueprint for the burn-in rewrite. Architecture rationale: `docs/adr/ADR-002`.

## Goal

`watch [b]` / `b2ctl burnin` vet **several disks at once**, non-blocking:
1. select disks space-separated (like `[n]ew-pool`), confirm burn-in `[y/N]`,
   then badblocks `[y/N]`;
2. start `smartctl -t long` on every selected disk (firmware, parallel);
3. if confirmed, start `badblocks -sv` on every selected disk (detached host
   processes);
4. show a **live per-disk view**: self-test bar + surface-scan bar + ETA;
5. **Ctrl-C leaves everything running**; re-attach via `b2ctl burnin --status`.

## Affected files & signatures

- **common.py** — `Disk` gains `selftest_running: bool=False`,
  `selftest_pct: int|None=None`, `selftest_eta: str=""` (additive defaults).
- **burnin.py** (core):
  - `parse_selftest(out) -> {running, pct, eta_min}` — pure; shared with smart.read.
  - `_selftest_eta_min(out, pct_complete)` — ATA recommended-polling-time
    (two-line label, bridged with `\s+`) × remaining%; SAS/NVMe → None.
  - `selftest_status(dev, dtype)` — now returns `eta_min` too; result-string logic
    (F-030 abort) unchanged.
  - `start_scan(dev, serial, *, dry_run)` -> `(pid, logfile)` — `Popen(badblocks
    -sv -b 4096, stderr=logfile, start_new_session=True)`; dry-run → `(None,"")`.
  - `scan_progress(rec) -> {pct, eta_min, running, bad}`; `_parse_badblocks_log`;
    `_pid_alive` (os.kill + `waitpid(WNOHANG)` reap).
  - state: `_state_dir()` (= `safety.LOG_DIR`, read at call time), `_state_path`,
    `load_state`, `save_state` (atomic), `_scan_log_path`.
  - `burnin_snapshot(records)` -> row dicts; `live_view(records, *, sleep=None)`
    (ANSI redraw, Ctrl-C → `save_state` + return); `_finish(records)` (verdict +
    prune); `run_multi(targets, …, do_scan, kind, dry_run)`; `status_view()`;
    `run()` delegates to `run_multi([target])`.
  - **removed:** `_wait_selftest`, `read_scan`.
- **smart.py** — `read()` calls `burnin.parse_selftest(out)` on the already-fetched
  `-a` output; sets `selftest_*` (eta via `ui.fmt_eta`). Zero extra subprocess.
- **ui.py** — `fmt_eta(minutes)`; `_status_cell` shows `TEST xx%` for a self-testing
  free disk; `render_details` adds a self-test line; `_bar`/`_progress_cell`/
  `render_burnin_row`/`render_burnin_view` (header + 1 line per disk).
- **watch.py** — `_cmd_burnin`: re-attach if `load_state()` non-empty; else
  multi-select (`[int(x)-1 for x in sel.split()]`, guard `<0`) + two `_confirm`s +
  `run_multi`.
- **cli.py** — `burnin` `target nargs="*"` + `--status`; `_burnin` → `status_view`
  or `run_multi`.
- **sim** — `sim/bin/badblocks` (incremental `% done`, dirty→bad+exit1, `-w`
  refused); `sim/bin/smartctl` `-t` seeds a self-test, `-a` reports live
  in-progress + polling time; `_simstate.selftest_seed/selftest_pct`.
- **_version.py** — `0.10.0-itmode`.

## Key decisions

- ETA sources: self-test = static recommended-polling-time × remaining% (reliable,
  unlike ZFS resilver ETA §6); badblocks = computed from our own elapsed.
- State file under `safety.LOG_DIR` (call-time) → inherits sim's monkeypatch.
- Re-entrant: never restart a disk already under a self-test.
- Exit code: `burnin` returns 0 once *started*; verdict shown in the live view /
  `--status`, not the exit code (old synchronous FAIL→exit-1 removed).
- No auto-repaint of the wide `status` table (keeps Task-A prompt-clutter fix);
  continuous auto-refresh stays the deferred `b2ctl top` (Task E).

## Test plan (per module; suite stays green, now 495 passed)

- **test_burnin.py**: parse_selftest running/pct/ETA (two-line polling time);
  selftest_status eta key; start_scan argv (no `-w`, `start_new_session`, dry-run
  no Popen); `_parse_badblocks_log`; scan_progress ETA-from-elapsed; `_pid_alive`
  (self alive / reaped child dead); state save/load roundtrip under a temp
  `safety.LOG_DIR`; burnin_snapshot done flag; live_view calls `_finish` on
  all-done and `save_state` on Ctrl-C; run_multi start+view / re-entrant no-restart
  / in-pool refusal / dry-run no view; `_finish` prune + scan-bad→WARN; status_view.
- **test_smart.py**: read() sets `selftest_*` from an in-progress `-a` dump.
- **test_ui.py**: fmt_eta; TEST% cell (free) vs ONLINE (in-pool); details line;
  render_burnin_view two bars; scan n/a; done(N bad).
- **test_watch.py**: `_cmd_burnin` multi-select → run_multi picks; re-attach path.
- **test_cli.py**: `burnin` nargs targets; `--status` → status_view.

## Manual sim verification

`python3 sim/simctl init`; free a SATA disk; `B2CTL_SIM_SELFTEST_SECS=3
sim/run burnin <dev>` → live view → `[PASS]`; with `--scan` on a `dirty` disk →
`[WARN] … bad block(s)`; `sim/run burnin --status`; confirm state in
`sim/var/burnin.json` (never `/var/log`).
