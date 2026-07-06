"""Smoke test for the simulation harness — drives real b2ctl via sim/run."""
import hashlib
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.dirname(HERE)
SIM = os.path.join(CODES, "sim")


def _sim(script, *args, state):
    env = dict(os.environ, B2CTL_STATE=state)
    return subprocess.run(
        [sys.executable, os.path.join(SIM, script), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _bin(binary, *args, state, secs=None):
    """Run a fake tool from sim/bin/ directly against a temp B2CTL_STATE.

    `secs` sets B2CTL_SIM_RESILVER_SECS so resilver timing is deterministic:
    a large value keeps a resilver in-progress, "0" completes it immediately.
    """
    env = dict(os.environ, B2CTL_STATE=state)
    if secs is not None:
        env["B2CTL_SIM_RESILVER_SECS"] = secs
    return subprocess.run(
        [sys.executable, os.path.join(SIM, "bin", binary), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _pct(out):
    m = re.search(r"([\d.]+)% done", out)
    return float(m.group(1)) if m else None


def _import_simstate():
    if SIM not in sys.path:
        sys.path.insert(0, SIM)
    import _simstate
    return _simstate


def test_sim_status_healthy(tmp_path):
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _sim("run", "status", state=state)
    assert r.returncode == 0, r.stderr
    # 8 disks (6 SATA/SAS + 2 NVMe), both pools ONLINE
    assert "tank/raidz1-0" in r.stdout
    assert "rpool/mirror-0" in r.stdout
    assert "DEGRADED" not in r.stdout
    # NVMe enumerated + relabelled by serial via the back panel (PCIe2:N)
    assert "nvme0n1" in r.stdout
    assert "PCIe2:0" in r.stdout


def test_sim_pull_makes_pool_degraded(tmp_path):
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    _sim("simctl", "pull", "1:5", state=state)
    r = _sim("run", "status", state=state)
    assert "DEGRADED" in r.stdout
    # pool-aware summary must NOT claim healthy when a pool is degraded
    assert "all disks healthy" not in r.stdout
    assert "not ONLINE" in r.stdout


def test_sim_raid_mode_backend(tmp_path):
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    _sim("simctl", "mode", "raid", state=state)
    r = _sim("run", "check", state=state)
    assert "RAID-mode" in r.stdout


def _load_fake_zpool():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    path = os.path.join(SIM, "bin", "zpool")
    loader = SourceFileLoader("fakezpool", path)      # extensionless script
    spec = importlib.util.spec_from_loader("fakezpool", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_sim_raid10_topology_and_health():
    """F-065: raid10 renders mirror-0/mirror-1 and is DEGRADED (not SUSPENDED)
    after one pull; b2ctl classifies it as 'mirror', not 'stripe'."""
    z = _load_fake_zpool()
    from b2ctl import zfs
    pool = {"name": "fast", "type": "raid10",
            "members": ["sdb", "sdc", "sdd", "sde"],
            "groups": [["sdb", "sdc"], ["sdd", "sde"]],
            "spares": [], "replacements": [], "cache": [], "log": [], "resilver": None}
    state = {"disks": [{"name": n, "present": True} for n in
                       ("sdb", "sdc", "sdd", "sde")], "pools": [pool]}

    status = z._render_status(state, pool)
    assert "mirror-0" in status and "mirror-1" in status
    assert z._pool_health(state, pool) == "ONLINE"

    # b2ctl side: topology classifies each leaf under a mirror-N top vdev
    topo = {}
    zfs._parse("fast", status, topo)
    tops = {e["top_vdev"] for e in topo.values()}
    assert tops == {"mirror-0", "mirror-1"}
    assert all(zfs.re.sub(r"-\d+$", "", t) == "mirror" for t in tops)  # pool_level -> mirror

    # pull ONE leg of a group -> DEGRADED, not SUSPENDED
    state["disks"][0]["present"] = False
    assert z._pool_health(state, pool) == "DEGRADED"

    # pull the OTHER leg of the same group -> now SUSPENDED
    state["disks"][1]["present"] = False
    assert z._pool_health(state, pool) == "SUSPENDED"


# --------------------------------------------------------------------------- #
# F-114 — atomic save() + load() that fails loud on corruption
# --------------------------------------------------------------------------- #

def test_state_load_raises_on_corrupt_json(tmp_path, monkeypatch):
    """F-114: a genuinely corrupt state file must fail loudly (SystemExit),
    NOT silently reset the operator's scenario to default_state()."""
    ss = _import_simstate()
    p = tmp_path / "state.json"
    p.write_text("{ this is not valid json")
    monkeypatch.setattr(ss, "STATE_PATH", str(p))
    with pytest.raises(SystemExit):
        ss.load()


def test_state_load_missing_returns_default(tmp_path, monkeypatch):
    """F-114: a missing file (pre-`simctl init`) returns the pristine default —
    only FileNotFoundError falls back, not a decode error."""
    ss = _import_simstate()
    p = tmp_path / "nope.json"
    monkeypatch.setattr(ss, "STATE_PATH", str(p))
    assert ss.load() == ss.default_state()


def test_state_save_load_roundtrip(tmp_path, monkeypatch):
    """F-114: save() (atomic tmp + os.replace) then load() round-trips, and
    leaves no unparsable window / stray .tmp behind."""
    ss = _import_simstate()
    p = str(tmp_path / "state.json")
    monkeypatch.setattr(ss, "STATE_PATH", p)
    st = ss.default_state()
    st["disks"][0]["present"] = False          # mutate so it != a fresh default
    st["pools"][1]["offline"] = ["sdc"]
    ss.save(st)
    assert not os.path.exists(p + ".tmp")      # atomic replace cleaned it up
    assert ss.load() == st


# --------------------------------------------------------------------------- #
# F-116 — `zpool status` is side-effect-free (time-derived resilver pct)
# --------------------------------------------------------------------------- #

def test_status_read_is_side_effect_free(tmp_path):
    """F-116: reads never advance/persist the resilver. With a large resilver
    duration, 5 rapid `status` reads leave state.json byte-identical and pct low
    (time-based, not the old +50%-per-read read-count behavior)."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _bin("zpool", "replace", "tank", "/dev/sdb", "/dev/sde",
             state=state, secs="600")
    assert r.returncode == 0, r.stderr
    before = _hash(state)
    pcts = []
    for _ in range(5):
        rs = _bin("zpool", "status", "tank", state=state, secs="600")
        assert rs.returncode == 0, rs.stderr
        assert "resilver in progress" in rs.stdout
        assert "resilvered" not in rs.stdout   # completion never races ahead
        pcts.append(_pct(rs.stdout))
    assert _hash(state) == before              # reads mutated nothing
    assert all(p is not None and p < 50 for p in pcts), pcts


# --------------------------------------------------------------------------- #
# F-117 — replace models the spare-N/replacing intermediate; detach validates
# --------------------------------------------------------------------------- #

def test_replace_creates_spare_group(tmp_path):
    """F-117: `zpool replace` onto a hot spare keeps BOTH the old member and the
    spare under a spare-0 group while the resilver is in progress (pct<100)."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _bin("zpool", "replace", "tank", "/dev/sdb", "/dev/sde",
             state=state, secs="600")
    assert r.returncode == 0, r.stderr
    rs = _bin("zpool", "status", "tank", state=state, secs="600")
    assert "spare-0" in rs.stdout
    assert "/dev/sdb" in rs.stdout and "/dev/sde" in rs.stdout
    assert "resilver in progress" in rs.stdout


def test_detach_nonexistent_errors(tmp_path):
    """F-117: detaching a token that is not in the pool must exit non-zero, so a
    wrong-token detach regression can't silently 'pass' the harness."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _bin("zpool", "detach", "tank", "/dev/nonexistent", state=state)
    assert r.returncode != 0
    assert "no such device in pool" in r.stderr


def test_detach_collapses_spare_group(tmp_path):
    """F-117: detaching the old leg collapses the spare group — the spare
    becomes a plain member and the old device disappears."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    _bin("zpool", "replace", "tank", "/dev/sdb", "/dev/sde",
         state=state, secs="600")
    r = _bin("zpool", "detach", "tank", "/dev/sdb", state=state)
    assert r.returncode == 0, r.stderr
    rs = _bin("zpool", "status", "tank", state=state, secs="600")
    assert "spare-0" not in rs.stdout
    assert "/dev/sde" in rs.stdout             # promoted to plain member
    assert "/dev/sdb" not in rs.stdout         # old leg gone


# --------------------------------------------------------------------------- #
# F-118 — offline/online genuinely change state (DEGRADED <-> ONLINE)
# --------------------------------------------------------------------------- #

def test_offline_online_state_transitions(tmp_path):
    """F-118: `zpool offline` renders the member OFFLINE and the pool DEGRADED
    (in `status` and `list -H`); `zpool online` restores ONLINE."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)

    r = _bin("zpool", "offline", "tank", "/dev/sdc", state=state)
    assert r.returncode == 0, r.stderr
    rs = _bin("zpool", "status", "tank", state=state)
    assert re.search(r"/dev/sdc\s+OFFLINE", rs.stdout), rs.stdout
    assert re.search(r"state:\s+DEGRADED", rs.stdout), rs.stdout

    rl = _bin("zpool", "list", "-H", state=state)
    tank = next(ln for ln in rl.stdout.splitlines() if ln.startswith("tank\t"))
    assert tank.split("\t")[4] == "DEGRADED", tank

    r = _bin("zpool", "online", "tank", "/dev/sdc", state=state)
    assert r.returncode == 0, r.stderr
    rs = _bin("zpool", "status", "tank", state=state)
    assert "OFFLINE" not in rs.stdout
    assert re.search(r"/dev/sdc\s+ONLINE", rs.stdout), rs.stdout


# --------------------------------------------------------------------------- #
# F-123 — simctl addresses NVMe by its relabelled bay, no 'bay None:None'
# --------------------------------------------------------------------------- #

def test_pull_nvme_by_bay_label(tmp_path):
    """F-123: `simctl pull PCIe2:0` resolves the NVMe disk via bay_map.json and
    the confirmation prints the mapped bay, never 'None'."""
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _sim("simctl", "pull", "PCIe2:0", state=state)
    assert r.returncode == 0, r.stderr
    assert "nvme0n1" in r.stdout
    assert "PCIe2:0" in r.stdout
    assert "None" not in r.stdout
