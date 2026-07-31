"""b2ctl.config — load /etc/b2ctl/config.json and resolve tool paths.

Config file is OPTIONAL. Missing or malformed -> all defaults apply.
The only writer is set_mode() (used by the install profiles); everything else
is read-only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile

CONFIG_PATH = "/etc/b2ctl/config.json"

# Standard absolute locations for operator-editable data files. Preferred over
# the __file__-relative bundled copies so resolution is directory-independent
# (see bay_map_path/ssd_spec_path). `b2ctl update` syncs the bundled files here.
STD_DIR      = "/etc/b2ctl"
STD_BAY_MAP  = os.path.join(STD_DIR, "bay_map.json")
STD_SSD_SPEC = os.path.join(STD_DIR, "ssd_spec.json")

_DEFAULTS: dict = {
    "tool_paths": {
        "sas2ircu": "",
        "perccli": "",
        "perccli64": "",
        "smartctl": "",
        "ledctl": "",
        "lsblk": "",
        "zpool": "",
        "wipefs": "",
        "sgdisk": "",
        "udevadm": "",
        "dd": "",
        "systemctl": "",
    },
    "controller": {
        "mode": "auto",    # "auto" | "it" | "raid"
        "index": "all",    # "all" or integer string e.g. "0"
    },
    "smart": {
        "timeout": 10,          # per-probe smartctl timeout (seconds)
        "megaraid_workers": 4,  # concurrent megaraid SMART probes; one PERC
                                # serializes passthrough, so 16-way saturates it
                                # and slow disks time out (NOREAD). Raise timeout /
                                # lower this on a box with slow or dying disks.
    },
    # Health-grading thresholds, split by disk type. A threshold of null (or "N/A"
    # / any non-integer) DISABLES that check. Defect signals (realloc/pending/
    # uncorr) grade with `>`; endurance/wear grade with `<` (lower % = worse).
    "health": {
        "ssd": {                       # SSD + NVMe: any bad sector is a failure
            "realloc_warn": None, "realloc_crit": 0,
            "pending_warn": None, "pending_crit": 0,
            "uncorr_warn": None,  "uncorr_crit": 0,
            # attr 188 Command_Timeout: a link symptom, never fatal on its own
            "cmdto_warn": 0,      "cmdto_crit": None,
            "endurance_warn": 30, "endurance_crit": 20,
            "wear_warn": 30,      "wear_crit": 20,
            "poh_warn": None,              # burn-in POH warning (off by default)
        },
        "hdd": {                       # HDD: tolerate stable, remapped defects
            "realloc_warn": 50,   "realloc_crit": 200,
            "pending_warn": 0,    "pending_crit": None,
            "uncorr_warn": None,  "uncorr_crit": 0,
            "cmdto_warn": 0,      "cmdto_crit": None,
            "endurance_warn": None, "endurance_crit": None,
            "wear_warn": None,    "wear_crit": None,
            "poh_warn": None,
        },
    },
    "bay_map_path": "",
    "ssd_spec_path": "",
    # Per-pool maintenance intent recorded at create time (v0.17.0). Keyed by pool
    # name -> {"autotrim": "on"|"off", "autoscrub": bool}. Written by
    # set_pool_settings(), removed by remove_pool_settings() on destroy.
    "pools": {},
    # Sticky defaults that pre-fill the create prompts. autoscrub default OFF is a
    # DELIBERATE reversal of the v0.16.0 "scrub always-on" stance (see ADR-003) —
    # manual scrub is the primary path; the timer is opt-in.
    "pool_defaults": {"autotrim": "off", "autoscrub": False},
}

_cache: dict | None = None

# Keys that turn into ROOT execution: tool_paths feeds subprocess argv directly
# (config.tool()), bay_map_path/ssd_spec_path point at files b2ctl trusts and
# reads back in. If /etc/b2ctl/config.json itself is writable by anyone but
# root, those keys are attacker-controlled (F-147).
_TRUST_GATED_KEYS = ("tool_paths", "bay_map_path", "ssd_spec_path")


def _untrusted_reason(path: str) -> str | None:
    """Return why `path` must not be trusted for _TRUST_GATED_KEYS, or None.

    Gated on the path being under /etc (the real deployment location) rather
    than on the caller's euid: the risk is the FILE's ownership, not who is
    currently running b2ctl — an unprivileged `--json` read that resolves
    tool_paths from a tampered config is just as wrong as a root one, and
    gating on euid==0 would hide the problem from every read-only caller.
    This also means sim/tests, which redirect CONFIG_PATH under sim/var or a
    tempdir, never hit this check — they are simply never under /etc.
    """
    if not (path == "/etc" or path.startswith("/etc" + os.sep)):
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    if st.st_uid != 0:
        return f"not owned by root (uid={st.st_uid})"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return "group/world-writable"
    return None


def load() -> dict:
    """Read config file and merge with defaults. Returns merged dict."""
    import copy
    cfg: dict = copy.deepcopy(_DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cfg
        # Merge per-section with shape guards, so a hand-edit that gives one
        # section the wrong type (e.g. "tool_paths": "/usr/sbin", or a top-level
        # list) falls back to defaults for THAT section instead of crashing every
        # command — the module's "malformed -> defaults apply" contract.
        if not isinstance(user, dict):
            return cfg
        tp = user.get("tool_paths")
        if isinstance(tp, dict):
            for k, v in tp.items():
                if v:
                    cfg["tool_paths"][k] = v
        ctrl = user.get("controller")
        if isinstance(ctrl, dict):
            if ctrl.get("mode"):
                cfg["controller"]["mode"] = ctrl["mode"]
            if ctrl.get("index") is not None:
                cfg["controller"]["index"] = str(ctrl["index"])
        sm = user.get("smart")
        if isinstance(sm, dict):
            for k in ("timeout", "megaraid_workers"):
                v = sm.get(k)
                # int-guarded: ignore a non-numeric / non-positive hand-edit and
                # keep the default for THAT key (module's malformed->defaults rule).
                if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                    cfg["smart"][k] = v
        hh = user.get("health")
        if isinstance(hh, dict):
            for typ in ("ssd", "hdd"):
                sub = hh.get(typ)
                if isinstance(sub, dict):
                    for k, v in sub.items():
                        if k in cfg["health"][typ]:
                            # null / "N/A" / any non-int -> None (check disabled);
                            # a real int overrides. Omitted keys keep the default.
                            cfg["health"][typ][k] = _norm_threshold(v)
        if isinstance(user.get("bay_map_path"), str) and user["bay_map_path"]:
            cfg["bay_map_path"] = user["bay_map_path"]
        if isinstance(user.get("ssd_spec_path"), str) and user["ssd_spec_path"]:
            cfg["ssd_spec_path"] = user["ssd_spec_path"]
        # Per-pool records — shape-guarded: a non-dict "pools", or a per-pool value
        # that isn't a dict, falls back to {} for that entry (malformed->defaults).
        pl = user.get("pools")
        if isinstance(pl, dict):
            cfg["pools"] = {
                k: {"autotrim": v.get("autotrim"),
                    "autoscrub": bool(v.get("autoscrub", False))}
                for k, v in pl.items() if isinstance(v, dict)
            }
        pd = user.get("pool_defaults")
        if isinstance(pd, dict):
            if pd.get("autotrim") in ("on", "off"):
                cfg["pool_defaults"]["autotrim"] = pd["autotrim"]
            if "autoscrub" in pd:
                cfg["pool_defaults"]["autoscrub"] = bool(pd["autoscrub"])
    reason = _untrusted_reason(CONFIG_PATH)
    if reason:
        from . import common as _common
        _common.warn(f"{CONFIG_PATH} {reason} — ignoring "
                     f"{'/'.join(_TRUST_GATED_KEYS)} from it (F-147)")
        for key in _TRUST_GATED_KEYS:
            cfg[key] = copy.deepcopy(_DEFAULTS[key])
    return cfg


def _get() -> dict:
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def smart_config() -> dict:
    """SMART scan tuning: {'timeout': int seconds, 'megaraid_workers': int}.

    Operator-tunable via /etc/b2ctl/config.json to fit a controller's passthrough
    throughput (see _DEFAULTS['smart']). Falls back to the defaults if the section
    is absent, so a partial cache never KeyErrors the scan."""
    sm = dict(_DEFAULTS["smart"])
    sm.update(_get().get("smart") or {})
    return sm


def _norm_threshold(v):
    """A health threshold is a positive/zero int, or None = 'check disabled'.
    null / "N/A" / bool / any non-int all normalise to None so a hand-edit never
    grades wrong — it just turns that one check off."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def health_config() -> dict:
    """Type-aware health thresholds: {'ssd': {...}, 'hdd': {...}}.

    Each threshold is an int or None (None = that check is not applied). Falls back
    per key to _DEFAULTS['health'] so a partial/absent cache never KeyErrors."""
    cur = _get().get("health") or {}
    out = {}
    for typ in ("ssd", "hdd"):
        merged = dict(_DEFAULTS["health"][typ])
        sub = cur.get(typ)
        if isinstance(sub, dict):
            for k in merged:
                if k in sub:
                    merged[k] = sub[k]
        out[typ] = merged
    return out


def tool(name: str) -> str:
    """Return resolved binary path for tool.

    Priority: config override -> shutil.which -> bare name (let OS decide).
    """
    override = _get()["tool_paths"].get(name, "")
    if override:
        return override
    found = shutil.which(name)
    return found if found else name


def _bundled_path(name: str) -> str:
    """Absolute path to a data file bundled next to the installed package
    (__file__-relative, so cwd/copy-sensitive — callers prefer /etc first)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", name))


def _resource_path(cfg_key: str, std: str, bundled: str) -> str:
    """Resolve a data file: config override > /etc standard > bundled next to code."""
    p = _get()[cfg_key]
    if p:
        return p
    if os.path.exists(std):
        return std
    return _bundled_path(bundled)


def bay_map_path() -> str:
    """Return path to bay_map.json (override > /etc > bundled)."""
    return _resource_path("bay_map_path", STD_BAY_MAP, "bay_map.json")


def bay_map_write_path() -> str:
    """Return the path an operator edit of bay_map.json should be WRITTEN to.

    Same resolution as bay_map_path() (override > /etc > bundled), except the
    bundled case is redirected to STD_BAY_MAP: the bundled copy lives inside the
    installed package (or this repo checkout), and writing there would edit the
    source tree on a dev box and be lost on redeploy — the operator's /etc copy
    is the only sane write target.
    """
    p = bay_map_path()
    if p == _bundled_path("bay_map.json"):
        return STD_BAY_MAP
    return p


def ssd_spec_path() -> str:
    """Return path to ssd_spec.json (override > /etc > bundled)."""
    return _resource_path("ssd_spec_path", STD_SSD_SPEC, "ssd_spec.json")


def controller_mode() -> str:
    """Return 'auto', 'it', or 'raid'."""
    return _get()["controller"].get("mode", "auto")


def controller_index_setting() -> str:
    """Return raw index setting: 'all' or a numeric string."""
    return str(_get()["controller"].get("index", "all"))


def _load_for_write() -> dict:
    """Read CONFIG_PATH as a mutable dict, PRESERVING every existing key.

    Refuses (raises ValueError) on an unparseable / non-object file rather than
    overwriting it — silently resetting would erase tool_paths/bay_map_path
    (F-075). A missing file returns {}. Shared by every single-setting writer.
    """
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{CONFIG_PATH} is not valid JSON ({exc}); fix it "
                         f"before writing config") from exc
    except OSError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_PATH} top-level is not an object; fix it first")
    return data


def atomic_write_json(path: str, data, *, mode: int = 0o644) -> None:
    """Write `data` as JSON to `path` atomically and crash-safely.

    Public + reusable (not just by this module): `cli._update` and
    `burnin.save_state` write their own JSON files non-atomically with a
    FIXED tmp name today — this is the shared helper they should switch to.

    Two b2ctl processes writing at once (an on-box service + an operator) used to
    race on the SAME fixed '<path>.tmp' name, so one could truncate the
    other's half-written file right before os.replace published the
    interleaved result. `tempfile.mkstemp` gives every writer its own tmp
    file in the same directory (so os.replace stays on one filesystem and is
    atomic), and flush()+fsync() before the rename means a crash/power-loss
    between write and rename can never publish a truncated/empty file
    (F-147, was F-075's docstring promise without the mechanism to back it).

    `mode` is applied via chmod (not open()'s create mode), so it wins over
    whatever the process umask would otherwise leave — the file's permissions are
    a decision, not a side effect of the operator's shell.

    0644, not 0600: `config` and `log` are in cli._ROOT_EXEMPT, so a non-root
    operator is MEANT to read these. 0600 broke that, and silently — config.load()
    swallows PermissionError and returns defaults, so `b2ctl config show` printed
    the wrong answer rather than refusing (F-149). Reading tool_paths was never
    the attack; WRITING them is, and _untrusted_reason() covers that. The tmp
    file is unlinked if anything raises before the rename.
    """
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".b2ctl-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write(data: dict) -> None:
    """Write `data` to CONFIG_PATH atomically (see atomic_write_json) so a
    crash/ENOSPC/concurrent-writer race can't leave a truncated or
    interleaved config that load() reads as all-defaults (F-075/F-147).
    Callers clear _cache afterwards."""
    atomic_write_json(CONFIG_PATH, data)


def set_mode(mode: str) -> None:
    """Persist controller.mode ('it'|'raid'|'auto') to CONFIG_PATH.

    Preserves any other keys already in the file, creates /etc/b2ctl if needed,
    and clears the in-process cache so the new mode takes effect immediately.
    """
    global _cache
    if mode not in ("it", "raid", "auto"):
        raise ValueError(f"invalid mode: {mode}")
    data = _load_for_write()
    ctrl = data.get("controller")
    if not isinstance(ctrl, dict):
        ctrl = {}
        data["controller"] = ctrl
    ctrl["mode"] = mode
    _atomic_write(data)
    _cache = None


def pool_defaults() -> dict:
    """Sticky create-prompt defaults: {'autotrim': 'on'|'off', 'autoscrub': bool}."""
    pd = dict(_DEFAULTS["pool_defaults"])
    pd.update(_get().get("pool_defaults") or {})
    return pd


def set_pool_defaults(*, autotrim: str, autoscrub: bool) -> None:
    """Remember the last-chosen create options so the next create pre-fills them."""
    global _cache
    data = _load_for_write()
    data["pool_defaults"] = {"autotrim": autotrim, "autoscrub": bool(autoscrub)}
    _atomic_write(data)
    _cache = None


def pool_settings(name: str) -> dict:
    """Persisted per-pool maintenance settings {'autotrim','autoscrub'} or {}."""
    return dict(_get().get("pools", {}).get(name, {}))


def set_pool_settings(name: str, *, autotrim: str, autoscrub: bool) -> None:
    """Persist pools[name]; preserves every other key (read-preserve-mutate-atomic)."""
    global _cache
    data = _load_for_write()
    pools = data.get("pools")
    if not isinstance(pools, dict):
        pools = {}
        data["pools"] = pools
    pools[name] = {"autotrim": autotrim, "autoscrub": bool(autoscrub)}
    _atomic_write(data)
    _cache = None


def remove_pool_settings(name: str) -> None:
    """Drop pools[name] on pool destroy. No-op if absent / file missing."""
    global _cache
    if not os.path.exists(CONFIG_PATH):
        return
    data = _load_for_write()
    pools = data.get("pools")
    if isinstance(pools, dict) and pools.pop(name, None) is not None:
        _atomic_write(data)
        _cache = None


_DEFAULT_BAY_MAP: list = [
    {"panel": "front", "type": "sas", "reverse_slots": False, "map": {}}
]

_BAY_KEY_RE = re.compile(r"^\d+:\d+$")


def load_bay_map() -> list:
    """Return the current bay_map.json panel list.

    Falls back to a single default front/sas panel when the file is absent,
    unreadable, or not a list — same 'malformed -> defaults' contract as
    load(); never raises. Parses the file directly rather than importing
    b2ctl.baymap, which imports this module (would be circular).
    """
    path = bay_map_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return list(_DEFAULT_BAY_MAP)
    if isinstance(data, list):
        return data
    return list(_DEFAULT_BAY_MAP)


def write_bay_map(panels: list) -> str:
    """Write the bay_map.json panel list atomically (see atomic_write_json)
    to bay_map_write_path(), not CONFIG_PATH. Raises OSError naturally on
    permission failure (caller reports it). Returns the path actually written.
    """
    path = bay_map_write_path()
    atomic_write_json(path, panels)
    return path


def _sas_panel(panels: list) -> dict:
    """Find the first type=='sas' panel, creating a default one (appended) if
    none exists yet. Mutating the returned dict in place preserves every other
    key on it (e.g. '_comment') and leaves every other panel untouched."""
    for p in panels:
        if isinstance(p, dict) and p.get("type") == "sas":
            return p
    p = {"panel": "front", "type": "sas", "reverse_slots": False, "map": {}}
    panels.append(p)
    return p


def set_front_reverse(on: bool, slots: int | None = None) -> list:
    """Set reverse_slots on the front sas panel; slots=None clears
    slots_per_enclosure so baymap.remap_slot's auto-detection takes over."""
    if slots is not None and (isinstance(slots, bool) or not isinstance(slots, int) or slots <= 0):
        raise ValueError(f"slots must be a positive int, got {slots!r}")
    panels = load_bay_map()
    p = _sas_panel(panels)
    p["reverse_slots"] = bool(on)
    if slots is None:
        p.pop("slots_per_enclosure", None)
    else:
        p["slots_per_enclosure"] = slots
    write_bay_map(panels)
    return panels


def set_bay_label(raw: str, label: str) -> list:
    """Set map[raw] = label on the front sas panel. `raw` must be 'enc:slot'
    (a malformed key would silently never match in baymap.remap_slot)."""
    if not isinstance(raw, str) or not _BAY_KEY_RE.match(raw):
        raise ValueError(f"raw bay key must look like 'enc:slot', got {raw!r}")
    if not label:
        raise ValueError("label must be non-empty")
    panels = load_bay_map()
    p = _sas_panel(panels)
    m = p.get("map")
    if not isinstance(m, dict):
        m = {}
        p["map"] = m
    m[raw] = label
    write_bay_map(panels)
    return panels


def clear_bay_map() -> list:
    """Reset the front sas panel's map to {} and drop reverse_slots /
    slots_per_enclosure — 'undo my bay customisation', not 'delete the file'.
    Other panels (e.g. the nvme back panel) are left untouched."""
    panels = load_bay_map()
    p = _sas_panel(panels)
    p["map"] = {}
    p.pop("reverse_slots", None)
    p.pop("slots_per_enclosure", None)
    write_bay_map(panels)
    return panels


def as_json() -> str:
    """Return current config as formatted JSON string (for `b2ctl config show`)."""
    return json.dumps(_get(), indent=2)


def validate() -> list[tuple[str, str, str]]:
    """Validate current config. Returns list of (field, status, message).
    status: 'ok' | 'warn' | 'error'
    """
    results: list[tuple[str, str, str]] = []

    # config file
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                raw = json.load(f)
            bad = [s for s in ("tool_paths", "controller")
                   if s in raw and not isinstance(raw.get(s), dict)] if isinstance(raw, dict) else ["<root>"]
            if bad:
                results.append(("config", "error",
                                f"{CONFIG_PATH}: wrong shape for {', '.join(bad)} — "
                                f"defaults applied for those"))
            else:
                results.append(("config", "ok", CONFIG_PATH))
        except json.JSONDecodeError as exc:
            results.append(("config", "error", f"{CONFIG_PATH}: JSON parse error: {exc}"))
    else:
        results.append(("config", "warn", f"{CONFIG_PATH} missing — using defaults"))

    # tool paths — test-run each binary (file-existence alone misses 32-bit
    # binaries that exist with +x but can't execute without libc6-i386)
    for name in ("sas2ircu", "perccli", "smartctl", "zpool"):
        path = tool(name)
        try:
            subprocess.run([path], capture_output=True, timeout=5)
            can_run = True
        except subprocess.TimeoutExpired:
            # F-076: a hung probe binary must not crash `b2ctl update`/config check.
            results.append((name, "warn", f"probe timed out at {path}"))
            continue
        except (FileNotFoundError, PermissionError, OSError):
            can_run = False
        if can_run:
            results.append((name, "ok", path))
        elif shutil.which(path) or (os.path.isfile(path) and os.access(path, os.X_OK)):
            hint = ("apt-get install -y libc6-i386" if name == "sas2ircu"
                    else "check binary compatibility")
            results.append((name, "warn", f"found but won't execute  →  {hint}"))
        else:
            hint = (f"run: b2ctl install --tool {name}"
                    if name in ("sas2ircu", "perccli") else "install via apt")
            results.append((name, "warn", f"not found  →  {hint}"))

    # ledctl — optional: enables the dedicated locate LED (else dd fallback)
    if shutil.which(tool("ledctl")):
        results.append(("ledctl", "ok", tool("ledctl")))
    else:
        results.append(("ledctl", "warn",
                        "not found  →  locate uses dd fallback (apt install ledmon)"))

    # data files: config override or /etc standard = ok; bundled fallback = warn
    for label, resolved, override_key in (
        ("bay_map", bay_map_path(), "bay_map_path"),
        ("ssd_spec", ssd_spec_path(), "ssd_spec_path"),
    ):
        if not os.path.exists(resolved):
            results.append((label, "error", f"{resolved} not found"))
        elif _get()[override_key] or resolved == os.path.join(STD_DIR, f"{label}.json"):
            results.append((label, "ok", resolved))
        else:
            results.append((label, "warn",
                            f"bundled ({resolved})  →  run: b2ctl update  (sync to {STD_DIR})"))

    return results
