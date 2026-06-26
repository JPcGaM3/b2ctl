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

_DEFAULTS: dict = {
    "tool_paths": {
        "sas2ircu": "",
        "perccli": "",
        "perccli64": "",
        "smartctl": "",
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
            for k, v in user.get("tool_paths", {}).items():
                if v:
                    cfg["tool_paths"][k] = v
            ctrl = user.get("controller", {})
            if ctrl.get("mode"):
                cfg["controller"]["mode"] = ctrl["mode"]
            if ctrl.get("index") is not None:
                cfg["controller"]["index"] = str(ctrl["index"])
            if user.get("bay_map_path"):
                cfg["bay_map_path"] = user["bay_map_path"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
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


def bay_map_path() -> str:
    """Return path to bay_map.json (config override or bundled next to code)."""
    p = _get()["bay_map_path"]
    if p:
        return p
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "bay_map.json")
    )


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
        except (json.JSONDecodeError, OSError):
            data = {}
    data.setdefault("controller", {})["mode"] = mode
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
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
                json.load(f)
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

    # bay_map
    bmp = bay_map_path()
    if os.path.exists(bmp):
        if _get()["bay_map_path"]:
            results.append(("bay_map", "ok", bmp))
        else:
            results.append(("bay_map", "warn",
                            f"bundled ({bmp})  →  b2ctl update --export-bay-map to customize"))
    else:
        results.append(("bay_map", "error", f"{bmp} not found"))

    return results
