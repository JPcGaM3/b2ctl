"""b2ctl.config — load /etc/b2ctl/config.json and resolve tool paths.

Config file is OPTIONAL. Missing or malformed -> all defaults apply.
The only writer is set_mode() (used by the install profiles); everything else
is read-only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

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
    },
    "controller": {
        "mode": "auto",    # "auto" | "it" | "raid"
        "index": "all",    # "all" or integer string e.g. "0"
    },
    "bay_map_path": "",
    "ssd_spec_path": "",
}

_cache: dict | None = None


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
        if isinstance(user.get("bay_map_path"), str) and user["bay_map_path"]:
            cfg["bay_map_path"] = user["bay_map_path"]
        if isinstance(user.get("ssd_spec_path"), str) and user["ssd_spec_path"]:
            cfg["ssd_spec_path"] = user["ssd_spec_path"]
    return cfg


def _get() -> dict:
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def tool(name: str) -> str:
    """Return resolved binary path for tool.

    Priority: config override -> shutil.which -> bare name (let OS decide).
    """
    override = _get()["tool_paths"].get(name, "")
    if override:
        return override
    found = shutil.which(name)
    return found if found else name


def _resource_path(cfg_key: str, std: str, bundled: str) -> str:
    """Resolve a data file: config override > /etc standard > bundled next to code.

    The bundled fallback is __file__-relative (cwd/copy-sensitive); the /etc
    standard is absolute, so preferring it keeps resolution directory-independent.
    """
    p = _get()[cfg_key]
    if p:
        return p
    if os.path.exists(std):
        return std
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", bundled))


def bay_map_path() -> str:
    """Return path to bay_map.json (override > /etc > bundled)."""
    return _resource_path("bay_map_path", STD_BAY_MAP, "bay_map.json")


def ssd_spec_path() -> str:
    """Return path to ssd_spec.json (override > /etc > bundled)."""
    return _resource_path("ssd_spec_path", STD_SSD_SPEC, "ssd_spec.json")


def controller_mode() -> str:
    """Return 'auto', 'it', or 'raid'."""
    return _get()["controller"].get("mode", "auto")


def controller_index_setting() -> str:
    """Return raw index setting: 'all' or a numeric string."""
    return str(_get()["controller"].get("index", "all"))


def set_mode(mode: str) -> None:
    """Persist controller.mode ('it'|'raid'|'auto') to CONFIG_PATH.

    Preserves any other keys already in the file, creates /etc/b2ctl if needed,
    and clears the in-process cache so the new mode takes effect immediately.
    """
    global _cache
    if mode not in ("it", "raid", "auto"):
        raise ValueError(f"invalid mode: {mode}")
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    data: dict = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            # Refuse to overwrite an unparseable file — silently resetting it to
            # {"controller": {...}} would erase tool_paths/bay_map_path (F-075).
            raise ValueError(f"{CONFIG_PATH} is not valid JSON ({exc}); fix it "
                             f"before setting the mode") from exc
        except OSError:
            data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{CONFIG_PATH} top-level is not an object; fix it first")
    ctrl = data.get("controller")
    if not isinstance(ctrl, dict):
        ctrl = {}
        data["controller"] = ctrl
    ctrl["mode"] = mode
    # Atomic write: tmp in the same dir + os.replace so a crash/ENOSPC can't leave
    # a truncated config that load() would silently read as all-defaults (F-075).
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
    _cache = None


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
