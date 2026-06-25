"""Smoke test for the simulation harness — drives real b2ctl via sim/run."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODES = os.path.dirname(HERE)
SIM = os.path.join(CODES, "sim")


def _sim(script, *args, state):
    env = dict(os.environ, B2CTL_STATE=state)
    return subprocess.run(
        [sys.executable, os.path.join(SIM, script), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_sim_status_healthy(tmp_path):
    state = str(tmp_path / "state.json")
    _sim("simctl", "init", state=state)
    r = _sim("run", "status", state=state)
    assert r.returncode == 0, r.stderr
    # 6 disks, both pools, healthy summary
    assert "tank/raidz1-0" in r.stdout
    assert "rpool/mirror-0" in r.stdout
    assert "all disks healthy" in r.stdout


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
