"""b2ctl.config — load /etc/b2ctl/config.json and resolve tool paths.

Config file is OPTIONAL. Missing or malformed -> all defaults apply.
Never write to /etc/b2ctl/config.json from this module (that is cli.py's job).
"""
from __future__ import annotations

import json
import os
import shutil

CONFIG_PATH = "/etc/b2ctl/config.json"

_DEFAULTS: dict = {
    "tool_paths": {
        "sas2ircu": "",
        "storcli": "",
        "storcli64": "",
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


def as_json() -> str:
    """Return current config as formatted JSON string (for `b2ctl config show`)."""
    return json.dumps(_get(), indent=2)
