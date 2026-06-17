# Status: [x] DONE   (PRIORITY 1 — foundation; all future features build on this)

## Affected Files

- `codes/b2ctl/safety.py` ← **NEW**
- `codes/b2ctl/common.py`
- `codes/b2ctl/backend.py`
- `codes/b2ctl/cli.py`
- `codes/b2ctl/watch.py`
- `codes/install.sh`

---

## Context

b2ctl v0.2.1 has no audit trail, no dry-run, no rollback, and confirmation dialogs that
don't show full device paths. A wrong action (offline wrong disk, replace wrong bay) can
degrade or destroy a ZFS pool with no recovery path.

This feature adds a Full Safety Framework covering all four risk areas:
1. No undo/rollback
2. Silent subprocess failures
3. No dry-run/preview
4. Wrong disk selected

---

## Architecture

Single new module `safety.py` owns all safety logic. Every future feature inherits it
for free via `begin_op`/`end_op` in `backend.py`.

### `safety.py` — new module

```python
def begin_op(name: str, disk, cmds: list[list[str]]) -> str:
    """Capture pre-op snapshot, write audit entry (status: pending). Return op_id."""

def end_op(op_id: str, success: bool, stdout: str, stderr: str) -> None:
    """Update audit entry (status: ok/fail), print rollback hint, run post-op verify."""

def dry_run_preview(cmds: list[list[str]]) -> None:
    """Print all write cmds that would run. Return without executing."""

def verify_disk_identity(serial: str, bay, dev_path: str) -> bool:
    """Cross-check serial + bay + /dev/disk/by-id path. Return False if mismatch."""
```

### `common.py` — extend `run_check()`

Add two optional kwargs (no existing callers break):
```python
def run_check(cmd, *, op_id=None, dry_run=False, **kw):
    # dry_run=True → print cmd, return ("", 0) without executing
    # op_id set    → append cmd + result to ops.jsonl entry
```

### `backend.py` — wrap each action

Every destructive action function gains:
```python
op_id = safety.begin_op("replace", disk, cmds)
...run_check(cmd, op_id=op_id, dry_run=dry_run)...
safety.end_op(op_id, success, stdout, stderr)
```
~5-line change per action (`do_replace`, `do_offline`, `do_add_spare`, `do_demote`).

### `cli.py` — new flag + subcommands

- `--dry-run` global flag → passed through to backend
- `b2ctl log [--last N]` → pretty-print `/var/log/b2ctl/ops.jsonl`
- `b2ctl rollback <op_id>` → execute stored `rollback_hint` with confirmation

### `watch.py` — dry-run toggle

- `n` key toggles dry-run mode
- Header prints `[DRY-RUN MODE]` in yellow when active

### `install.sh` — create log dirs

```bash
mkdir -p /var/log/b2ctl/snapshots
```

---

## Implementation Guide

### 1. Audit Trail — `/var/log/b2ctl/ops.jsonl`

JSONL (one JSON object per line, append-only). Each entry:

```json
{
  "op_id": "20260617-143022-replace",
  "op": "replace",
  "disk_serial": "S3EVNX0K123456",
  "disk_bay": 3,
  "dev_path": "/dev/disk/by-id/wwn-0x...",
  "pool": "tank",
  "vdev": "raidz1-0",
  "cmds": [["zpool", "replace", "tank", "/dev/disk/by-id/old", "/dev/disk/by-id/new"]],
  "status": "ok",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "",
  "started_at": "2026-06-17T14:30:22",
  "ended_at": "2026-06-17T14:30:23",
  "rollback_hint": "zpool replace tank /dev/disk/by-id/wwn-new /dev/disk/by-id/wwn-old",
  "snapshot_path": "/var/log/b2ctl/snapshots/20260617-143022-replace.txt"
}
```

`op_id` format: `YYYYMMDD-HHMMSS-<op>` from `datetime.now()`.

### 2. Dry-run Mode

Write cmd classification — check `cmd[0]` against allowlist:
- Write (suppressed): `zpool`, `wipefs`, `sgdisk`, `dd`
- Read (still runs): everything else (`smartctl`, `lsblk`, `zfs`, etc.)

Output when dry-run active:
```
[DRY-RUN] would run: zpool replace tank \
  /dev/disk/by-id/wwn-0x5000c500a1b2c3d4 \
  /dev/disk/by-id/wwn-0x5000c500deadbeef
```

Audit entry written with `status: "dry_run"`.

### 3. Pre-op State Snapshot — `/var/log/b2ctl/snapshots/<op_id>.txt`

Captured in `begin_op()` before any destructive call. Run and write:
- `zpool status <pool>`
- `zpool list -v`
- `zfs list`
- `smartctl -a <dev>` for affected disk

### 4. Rollback Hints

Stored in `rollback_hint` field. Printed by `end_op()`.

| Op | Rollback cmd | Reversible? |
|---|---|---|
| `offline` | `zpool online <pool> <dev-by-id>` | ✓ |
| `add spare` | `zpool remove <pool> <dev-by-id>` | ✓ |
| `replace` (resilver in-progress) | `zpool replace <pool> <new> <old>` | ✓ |
| `replace` (resilver complete) | — | ✗ warn only |
| `demote` mirror→spare | `zpool attach <pool> <remaining> <demoted>` | ✓ |
| `wipefs` / `sgdisk` | — | ✗ warn only |
| pool `create` | `zpool destroy <pool>` | ✓ red warning |

### 5. `b2ctl rollback <op_id>`

```
Op:       replace  (2026-06-17 14:30:22)
Disk:     bay 3 → bay 7, wwn-0x5000c500a1b2c3d4
Pool:     tank/raidz1-0
Rollback: zpool replace tank /dev/disk/by-id/wwn-new /dev/disk/by-id/wwn-old

Proceed? [y/N]:
```

- Reads `ops.jsonl`, finds entry by `op_id`
- Checks `rollback_hint` — if empty/null: print "Op not reversible. See snapshot: <path>" and exit
- On `y`: execute via `run_check()` with new audit entry (links to original `op_id`)

### 6. Enhanced Confirmation Dialog

Replace all existing bare `[y/N]` prompts for destructive ops:

```
┌─ CONFIRM OPERATION ──────────────────────────────┐
│ Op:     replace                                   │
│ From:   bay 3 │ S3EVNX0K123456 │ ONLINE (tank)   │
│ To:     bay 7 │ S8ABC1K789012  │ AVAILABLE        │
│ Pool:   tank/raidz1-0                             │
│                                                   │
│ Will run:                                         │
│   zpool replace tank                              │
│     /dev/disk/by-id/wwn-0x5000c500a1b2c3d4-part1 │
│     /dev/disk/by-id/wwn-0x5000c500deadbeef        │
│                                                   │
│ Snapshot → /var/log/b2ctl/snapshots/...txt        │
└──────────────────────────────────────────────────┘
Proceed? [y/N]:
```

Box drawn with stdlib (no curses). Shows actual `/dev/disk/by-id/` paths.

### 7. Post-op Verification

Run inside `end_op()` after subprocess exits. Re-scan affected disk + pool. Check:

| Op | Expected state | Failure action |
|---|---|---|
| `replace` | disk appears in target vdev | alert + print snapshot + suggest rollback |
| `replace` | resilver started | alert if no resilver in `zpool status` |
| `add spare` | spare count +1 | alert if unchanged |
| `offline` | disk state = OFFLINE | alert if still ONLINE |
| any | exit_code == 0 | already caught by `run_check()` |

Failure output:
```
⚠ Post-op check FAILED: disk wwn-0x... not found in tank/raidz1-0
  Expected state not reached. See snapshot:
  /var/log/b2ctl/snapshots/20260617-143022-replace.txt
  Run: b2ctl rollback 20260617-143022-replace
```

---

## Acceptance Criteria

- [ ] `b2ctl --dry-run offline <serial>` prints cmd, no execution, audit entry shows `status: dry_run`
- [ ] `b2ctl log` shows table of recent ops with op_id, op, bay, serial, pool, status, time
- [ ] After any write op, `/var/log/b2ctl/snapshots/<op_id>.txt` exists with pool state
- [ ] `b2ctl rollback <op_id>` shows enhanced confirm with real `/dev/disk/by-id/` paths; executes on `y`
- [ ] Irreversible op rollback: prints "not reversible" + snapshot path, exits cleanly
- [ ] Rollback itself written to `ops.jsonl`
- [ ] Enhanced confirmation box shows full device paths for all destructive ops
- [ ] Post-op verify: `offline` that fails to change disk state prints `⚠ Post-op check FAILED`
- [ ] Silent failure: subprocess stderr captured in audit entry, `status: fail` when exit_code != 0
- [ ] `py_compile` clean on all modified files
- [ ] `install.sh` creates `/var/log/b2ctl/snapshots/` on deploy
- [ ] Existing callers of `run_check()` unchanged (no regressions)
