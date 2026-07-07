"""Unit tests for b2ctl.safety — begin_op/end_op JSONL log + snapshot dir."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestBeginOp(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_begin_op_writes_pending_entry(self):
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "/dev/disk/by-id/old", "/dev/disk/by-id/new"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            op_id = safety.begin_op("replace", "ABC123", 3, "/dev/disk/by-id/wwn-x", "tank", "raidz1-0", cmds)
        self.assertTrue(op_id.endswith("-replace"))
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["op"], "replace")
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["disk_serial"], "ABC123")
        self.assertEqual(entry["disk_bay"], 3)
        self.assertEqual(entry["cmds"], cmds)

    def test_end_op_updates_entry_ok(self):
        safety = self._import_safety()
        cmds = [["zpool", "offline", "tank", "/dev/disk/by-id/x"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("offline", "XYZ", 5, "/dev/disk/by-id/x", "tank", "raidz1-0", cmds)
                safety.end_op(op_id, True, "success output", "", 0)
        # append-only log (F-093): end record merges over begin — read via the API
        entry = safety.find_entry(op_id)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["exit_code"], 0)
        self.assertEqual(entry["stdout"], "success output")

    def test_end_op_updates_entry_fail(self):
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "x", "y"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("replace", "ZZZ", 2, "/dev/disk/by-id/z", "tank", "raidz1-0", cmds)
                safety.end_op(op_id, False, "", "error text", 1)
        entry = safety.find_entry(op_id)
        self.assertEqual(entry["status"], "fail")
        self.assertEqual(entry["exit_code"], 1)
        self.assertEqual(entry["stderr"], "error text")

    def test_snapshot_dir_created(self):
        safety = self._import_safety()
        os.makedirs(self.snap_dir, exist_ok=True)
        cmds = [["zpool", "add", "tank", "spare", "/dev/disk/by-id/x"]]
        with patch("b2ctl.safety.run_check", return_value=(True, "pool: tank\nstate: ONLINE")):
            op_id = safety.begin_op("add_spare", "AAA", 1, "/dev/disk/by-id/x", "tank", "spares", cmds)
        snap_path = os.path.join(self.snap_dir, f"{op_id}.txt")
        self.assertTrue(os.path.exists(snap_path))

    def test_dry_run_skips_snapshot(self):
        safety = self._import_safety()
        os.makedirs(self.snap_dir, exist_ok=True)
        cmds = [["zpool", "replace", "tank", "a", "b"]]
        with patch.object(safety, "_capture_snapshot") as mock_snap:
            op_id = safety.begin_op("replace", "S1", 3, "/dev/disk/by-id/x",
                                    "tank", "raidz1-0", cmds, dry_run=True)
        mock_snap.assert_not_called()                       # no capture on dry-run
        self.assertFalse(os.path.exists(os.path.join(self.snap_dir, f"{op_id}.txt")))
        with open(self.log_file) as f:
            entry = json.loads(f.readline())
        self.assertIsNone(entry["snapshot_path"])


class TestEndOpDryRun(unittest.TestCase):
    """dry-run end_op must not render an error icon, suggest a rollback, or
    run the live post-op verification."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_dry_run_clean_output_and_no_verify(self):
        import io
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank", "a", "b"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            op_id = safety.begin_op("replace", "S1", 3, "/dev/disk/by-id/x",
                                    "tank", "raidz1-0", cmds)
        with patch.object(safety, "_post_op_verify") as mock_verify:
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                safety.end_op(op_id, True, "", "", 0, dry_run=True)
        text = out.getvalue()
        mock_verify.assert_not_called()          # no live re-scan on dry-run
        self.assertIn("dry-run", text)
        self.assertNotIn("✗", text)              # no red error icon
        self.assertNotIn("Rollback", text)       # nothing to roll back
        entry = safety.find_entry(op_id)
        self.assertEqual(entry["status"], "dry_run")


class TestRollbackHints(unittest.TestCase):
    """F-091 — the 'replace' rollback hint must be built from the named
    old_dev/new_dev fields recorded at begin_op, NOT by positional indexing
    into the caller's cmd list (which a future flag change would shift)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_replace_hint_from_named_fields(self):
        safety = self._import_safety()
        old = "/dev/disk/by-id/ata-OLD_S1"
        new = "/dev/disk/by-id/ata-NEW_S2"
        # A cmd shape DIFFERENT from the canonical 6-token
        # ['zpool','replace','-f',pool,old,new]: an extra '-s' flag shifts the
        # device tokens, so positional cmds[0][5]/[4] would name the WRONG
        # devices. The named old_dev/new_dev fields must win regardless.
        cmds = [["zpool", "replace", "-f", "-s", "tank", old, new]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op(
                    "replace", "SER1", 4, new, "tank", "raidz1-0", cmds,
                    details={"old_dev": old, "new_dev": new})
                safety.end_op(op_id, True, "resilver done", "", 0)
        entry = safety.find_entry(op_id)
        # named fields -> correct executable rollback
        self.assertEqual(entry["rollback_hint"],
                         f"zpool replace tank {new} {old}")
        # positional cmds[0][5]/[4] would have produced this wrong-shape hint
        self.assertNotEqual(entry["rollback_hint"],
                            f"zpool replace tank {cmds[0][5]} {cmds[0][4]}")


class TestEndOpUnwritableLog(unittest.TestCase):
    """F-092 — when the on-disk audit entry cannot be read back (unwritable
    /var), end_op must still fall back to the in-memory pending entry: print
    the op result, run _post_op_verify, and emit one audit-unwritable warning."""

    def setUp(self):
        import b2ctl.safety as safety
        safety._log_warned = False      # let the once-only warning fire again
        safety._PENDING.clear()
        self.tmp = tempfile.mkdtemp()
        self.snap_dir = os.path.join(self.tmp, "snapshots")
        # LOG_FILE lives under a subdir that does NOT exist -> open('a') OSError,
        # while LOG_DIR itself exists so begin_op's makedirs succeeds.
        self.log_file = os.path.join(self.tmp, "missing", "ops.jsonl")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def test_end_op_prints_result_when_log_unwritable(self):
        import io
        safety = self._import_safety()
        cmds = [["zpool", "replace", "tank",
                 "/dev/disk/by-id/old", "/dev/disk/by-id/new"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify") as mock_verify:
                with patch("sys.stdout", new_callable=io.StringIO) as out:
                    op_id = safety.begin_op("replace", "SER9", 6,
                                            "/dev/disk/by-id/new", "tank",
                                            "raidz1-0", cmds)
                    safety.end_op(op_id, True, "ok output", "", 0)
        text = out.getvalue()
        # op result still printed despite the failed log round-trip
        self.assertIn("complete", text)
        # the single audit-unwritable warning fired
        self.assertIn("audit log unwritable", text)
        # post-op verification still ran for the successful non-dry-run op
        mock_verify.assert_called_once()
        # nothing was actually written to disk
        self.assertFalse(os.path.exists(self.log_file))


class TestAppendOnly(unittest.TestCase):
    """F-093 — the log is append-only: end_op appends a second record instead
    of rewriting/truncating the file, and reads merge begin+end by op_id."""

    def setUp(self):
        import b2ctl.safety as safety
        safety._PENDING.clear()
        self.tmp = tempfile.mkdtemp()
        self.log_file = os.path.join(self.tmp, "ops.jsonl")
        self.snap_dir = os.path.join(self.tmp, "snapshots")

    def _import_safety(self):
        import b2ctl.safety as safety
        safety.LOG_DIR = self.tmp
        safety.SNAP_DIR = self.snap_dir
        safety.LOG_FILE = self.log_file
        return safety

    def _seed_prior_lines(self, n=6):
        lines = [json.dumps({"op_id": f"prior-{i}", "op": "offline",
                             "status": "ok", "pool": "tank"}) for i in range(n)]
        blob = "\n".join(lines) + "\n"
        with open(self.log_file, "w") as f:
            f.write(blob)
        return blob

    def test_end_op_appends_without_rewriting(self):
        safety = self._import_safety()
        prior = self._seed_prior_lines()
        cmds = [["zpool", "offline", "tank", "/dev/disk/by-id/x"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("offline", "SER", 2,
                                        "/dev/disk/by-id/x", "tank",
                                        "raidz1-0", cmds)
                safety.end_op(op_id, True, "done", "", 0)
        with open(self.log_file) as f:
            content = f.read()
        # (a) prior bytes untouched — the file was appended to, never rewritten
        self.assertTrue(content.startswith(prior))
        # begin + end records both appended after the seed data
        self.assertEqual(content.count(op_id), 2)
        # (b) merged view yields the final status
        entry = safety.find_entry(op_id)
        self.assertEqual(entry["status"], "ok")

    def test_load_log_merges_begin_and_end_per_op_id(self):
        safety = self._import_safety()
        cmds = [["zpool", "offline", "tank", "/dev/disk/by-id/x"]]
        with patch.object(safety, "_capture_snapshot", return_value=None):
            with patch.object(safety, "_post_op_verify", return_value=None):
                op_id = safety.begin_op("offline", "SER", 2,
                                        "/dev/disk/by-id/x", "tank",
                                        "raidz1-0", cmds)
                safety.end_op(op_id, True, "done", "", 0)
        log = safety.load_log()
        matches = [e for e in log if e.get("op_id") == op_id]
        # two on-disk records collapse to ONE merged entry with the final status
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["status"], "ok")


class TestPostOpVerify(unittest.TestCase):
    """F-094 — _post_op_verify re-scans the pool and, on a mismatch, prints the
    'Post-op check FAILED' + 'b2ctl rollback' advice. Exercised directly."""

    def _entry(self, **over):
        e = {"op_id": "20260706-000000-000000-offline", "op": "offline",
             "pool": "tank", "disk_serial": "ABC123", "snapshot_path": ""}
        e.update(over)
        return e

    def test_offline_mismatch_prints_rollback_advice(self):
        import io
        import b2ctl.safety as safety
        # offline check = (serial not in o) or ("OFFLINE" in o); it FAILS when
        # the serial is still present and the disk is not OFFLINE (op didn't take)
        status = "  pool: tank\n  state: ONLINE\n    ABC123  ONLINE  0 0 0\n"
        entry = self._entry(op="offline", disk_serial="ABC123",
                            snapshot_path="/tmp/snap.txt")
        with patch.object(safety, "run_check", return_value=(True, status)):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                safety._post_op_verify(entry)
        text = out.getvalue()
        self.assertIn("Post-op check FAILED", text)
        self.assertIn("b2ctl rollback", text)

    def test_replace_check_passes_on_resilver_output(self):
        import io
        import b2ctl.safety as safety
        # replace check = ("resilver" in o) or ("resilvered" in o) -> passes
        status = "  scan: resilver in progress\n    ABC123  ONLINE\n"
        entry = self._entry(op="replace", disk_serial="ABC123")
        with patch.object(safety, "run_check", return_value=(True, status)):
            with patch("sys.stdout", new_callable=io.StringIO) as out:
                safety._post_op_verify(entry)
        text = out.getvalue()
        self.assertNotIn("Post-op check FAILED", text)


if __name__ == "__main__":
    unittest.main()
