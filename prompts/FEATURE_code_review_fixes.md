# Status: [x] DONE

## Affected Files

- `codes/b2ctl/spec.py` — fix 4
- `codes/b2ctl/config.py` — fix 9
- `codes/b2ctl/cli.py` — fix 6
- `codes/b2ctl/zfs.py` — fixes 2, 8
- `codes/b2ctl/watch.py` — fixes 1, 3, 5, 7, 10
- `codes/tests/test_spec.py` — fix 4 test
- `codes/tests/test_zfs.py` — fixes 2, 8 tests
- `codes/tests/test_cli.py` — fix 6 test
- `codes/tests/test_watch.py` — fixes 1, 3, 7 tests

---

## Context

Code review (8 angles) on `fix/agy_review` branch. 9 CONFIRMED + 1 PLAUSIBLE structural.
No new logic — each fix is a targeted correction of an existing bug.

---

## Fix 1 — watch.py:270 — wipe() missing dry_run [CRITICAL]

`_wipe_ghost()` at line 270: `zfs.wipe(sdx)` — missing `dry_run=_DRY_RUN`.
All other wipe callsites (lines 223, 256, 493) pass it. Fix: add kwarg.

**Fix:**
```python
ok2, out2 = zfs.wipe(sdx, dry_run=_DRY_RUN)
```

---

## Fix 2 — zfs.py:264-278 + watch.py — resilver errors treated as success [HIGH]

`poll_resilver_status()` sets `has_errors` only in completion branch → KeyError if accessed
in-progress. Also: watch.py loops never read `has_errors` → errors print ✔ green.

**zfs.py fix:** add `has_errors: False` to initial dict:
```python
res = {"done": 0.0, "eta": "", "completed": False, "has_errors": False}
```
(completion branch already sets it correctly — just needs a default)

**watch.py fix** (inside _wait_resilver helper from fix 10):
```python
if st.get("has_errors"):
    sys.stdout.write(f"\r  ✗ resilver completed WITH ERRORS — run: zpool status {pool}\n")
    return False
```

---

## Fix 3 — watch.py:168,173,399,404 — end_op cancel/fail missing dry_run [HIGH]

4 callsites missing `, dry_run=_DRY_RUN`. Success paths (lines 196, 430) already have it.
Affects: `_assign_free_disk` cancel (168) + fail (173), `_replace_onto_spare` cancel (399) + fail (404).

**Fix:** append `, dry_run=_DRY_RUN` to each of the 4 calls.

---

## Fix 4 — spec.py:49 — reverse match removed [HIGH]

`if k and k in m:` removed `m in k` branch. SATA SSDs behind SAS HBA produce truncated
model strings — lsblk model may be shorter than the spec key.

**Fix:**
```python
if k and (k in m or m in k):
```

**Test:** `lookup("Samsung 870 EVO", {"samsung ssd 870 evo 1tb": 600})` → 600

---

## Fix 5 — watch.py:396 — _replace_onto_spare missing -f [MED]

`_replace_onto_spare` builds `zpool replace` without `-f`. `_assign_free_disk` choice-3
(line 165) already uses `-f`. Inconsistency: recycled spare with stale labels fails.

**Fix:**
```python
cmds = [["zpool", "replace", "-f", pool, _pool_dev(d), spare.pool_token or spare.by_id]]
```

---

## Fix 6 — cli.py:289 — rollback hint placeholders executed [MED]

Hints for create/demote/replace ops contain `<new-disk>`, `<remaining-member>` etc.
`cmd = hint.split()` → `run_check(cmd)` passes these as literal argv → zpool rejects.

**Fix:** add guard before `rb_op_id = safety.begin_op(...)`:
```python
cmd = hint.split()
if any(t.startswith("<") and t.endswith(">") for t in cmd):
    print("  Rollback hint contains unresolved placeholders — resolve manually:")
    print(f"     {hint}")
    return
```

**Test:** hint="zpool replace tank <new-disk> /dev/..." → warning printed, no subprocess.

---

## Fix 7 — watch.py:166,397 — begin_op before _confirm_op [MED]

Both `_assign_free_disk` (line 166) and `_replace_onto_spare` (line 397):
`begin_op` writes audit entry before `_confirm_op` asks user — cancelled ops create
noise entries with status='cancelled'.

**Fix:** swap order at both sites:
```python
# NEW order:
if not _confirm_op(...):
    return                        # no audit entry for cancel
op_id = safety.begin_op(...)
ok, out = run_check(...)
if not ok:
    safety.end_op(op_id, False, "", out, 1, dry_run=_DRY_RUN)
    return
```

---

## Fix 8 — zfs.py:196 — spares_replacing() misses spare-N containers [MED]

Hot-spare auto-activation creates `spare-N` vdev in zpool status, not `replacing-N`.
Function only matches `st.startswith("replacing")` → returns {} for auto-resilvering spares
→ STATUS column never shows →bay indicator for INUSE spares.

**Fix:**
```python
if mv and (st.startswith("replacing") or st.startswith("spare-")):
```

**Test:** fake zpool status with `spare-1` container (REMOVED + ONLINE children)
→ `spares_replacing()` returns `{online_token: removed_token}`.

---

## Fix 9 — config.py:125 — subprocess inline import [LOW/CONVENTION]

`validate()` does `import subprocess as _sp` inline. CLAUDE.md §4 requires run()/run_check().
Fix: move to module-level import. Logic unchanged.

**Fix:** add `import subprocess` at module top if absent. Remove inline import.
Change `_sp.run(` → `subprocess.run(` in validate().

---

## Fix 10 — watch.py — resilver loop + detach extracted [CLEANUP]

Same ~14 lines triplicated in `_assign_free_disk`, `_replace_onto_spare`, `_cmd_swap`.
The dry_run bugs (#1, #3) were missed in 2 of 3 copies — proves the maintenance cost.

**Add 2 helpers** (place before `_replace_onto_spare`):

```python
def _wait_resilver(pool: str) -> bool:
    while True:
        time.sleep(2)
        st = zfs.poll_resilver_status(pool)
        if st["completed"]:
            if st.get("has_errors"):
                sys.stdout.write(
                    f"\r{R}  ✗ resilver completed WITH ERRORS — run: zpool status {pool}{N}\n")
                return False
            sys.stdout.write(f"\r{G}  ✔ resilver completed{N}                    \n")
            return True
        sys.stdout.write(f"\r{Y}  resilvering... {st['done']}% done, ETA {st['eta']}{N}")
        sys.stdout.flush()


def _detach_if_lingers(pool: str, old_token: str) -> None:
    topo = zfs.topology()
    if any(e["pool"] == pool and e["token"] == old_token for e in topo.values()):
        ok_d, out_d = zfs.detach(pool, old_token, dry_run=_DRY_RUN)
        print((G + f"  ✔ detached {old_token}" if ok_d
               else R + f"  ✗ detach failed: {out_d}") + N)
```

Replace 3 loop sites + 2 detach-linger sites with calls. Keep `if not _DRY_RUN:` guard
wrapping `_wait_resilver(pool)` at each callsite.

---

## Test plan

| Fix | Test |
|-----|------|
| 1 | dry_run=True → wipe mock NOT called |
| 2 | poll returns completed+has_errors → returns False from _wait_resilver |
| 3 | dry_run=True + cancel → end_op called with dry_run=True |
| 4 | lookup("Samsung 870 EVO", {"samsung ssd 870 evo 1tb": 600}) → 600 |
| 6 | hint with `<token>` → no subprocess, message printed |
| 7 | cancel → begin_op NOT called (op never starts) |
| 8 | spare-N container in fake zpool status → spares_replacing returns {spare: old} |

## Verification

```bash
cd codes
python3 -m py_compile b2ctl/*.py
python3 -m pytest tests/ -q
grep 'zfs.wipe(' b2ctl/watch.py          # all: dry_run=_DRY_RUN
grep 'end_op(' b2ctl/watch.py            # all: dry_run=_DRY_RUN
grep -n 'begin_op\|_confirm_op' b2ctl/watch.py  # confirm always before begin_op
```
