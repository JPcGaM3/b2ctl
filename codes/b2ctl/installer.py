"""b2ctl.installer — download and install sas2ircu / storcli / perccli."""
from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

_GDRIVE = {
    "sas2ircu": "1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt",
    "storcli":  "1nMbQFD94vdDl6QNjUzRtp1UHlKwDwmYN",
    "perccli":  "1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk",
}
_BASE = "https://drive.usercontent.google.com/download?export=download&confirm=t&id="
_ARCHIVE_NAME = {
    "sas2ircu": "SAS2IRCU_P20.zip",
    "storcli":  "storcli.zip",
    "perccli":  "perccli.tar.gz",
}


def tool_ok(name: str) -> bool:
    """Return True if tool binary is present and executable."""
    return shutil.which(name) is not None


def download(file_id: str, dest_path: str) -> None:
    """Download a Google Drive file to dest_path. Raises RuntimeError if result < 1 KB."""
    url = _BASE + file_id
    print(f"    downloading...", end="", flush=True)
    urllib.request.urlretrieve(url, dest_path)
    size = os.path.getsize(dest_path)
    if size < 1024:
        raise RuntimeError(f"download too small ({size} bytes) — may be HTML error page")
    print(f" {size // 1024} KB")


def install_sas2ircu(archive: str) -> tuple[bool, str]:
    """Extract linux_x86_rel/sas2ircu from zip, install to /usr/local/bin/sas2ircu."""
    tmp = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        sas = None
        for root, _dirs, files in os.walk(tmp):
            if "x86_rel" in root and "sas2ircu" in files:
                candidate = os.path.join(root, "sas2ircu")
                if not candidate.endswith(".exe"):
                    sas = candidate
                    break
        if not sas:
            return False, "binary not found in archive"
        dest = "/usr/local/bin/sas2ircu"
        shutil.copy2(sas, dest)
        os.chmod(dest, 0o755)
        return True, dest
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_storcli(archive: str) -> tuple[bool, str]:
    """Extract Ubuntu/*.deb from zip, run dpkg -i, symlink storcli64 -> /usr/local/bin/storcli."""
    tmp = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        deb = None
        for root, _dirs, files in os.walk(tmp):
            if os.path.basename(root) == "Ubuntu":
                for f in files:
                    if f.endswith(".deb"):
                        deb = os.path.join(root, f)
                        break
            if deb:
                break
        if not deb:
            return False, "Ubuntu .deb not found in archive"
        r = subprocess.run(["dpkg", "-i", deb], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"dpkg failed: {r.stderr.strip()}"
        binary = "/opt/MegaRAID/storcli/storcli64"
        if not os.path.exists(binary):
            return False, f"dpkg succeeded but {binary} not found"
        symlink = "/usr/local/bin/storcli"
        if os.path.lexists(symlink):
            os.remove(symlink)
        os.symlink(binary, symlink)
        return True, symlink
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_perccli(archive: str) -> tuple[bool, str]:
    """Extract *.rpm from tar.gz, run alien --scripts -i, symlink perccli64 -> /usr/local/bin/perccli."""
    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(tmp)
        rpm = None
        for root, _dirs, files in os.walk(tmp):
            for f in files:
                if f.endswith(".rpm"):
                    rpm = os.path.join(root, f)
                    break
            if rpm:
                break
        if not rpm:
            return False, "RPM not found in archive"
        r = subprocess.run(["alien", "--scripts", "-i", rpm],
                           cwd=tmp, capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"alien failed: {r.stderr.strip()}"
        binary = "/opt/MegaRAID/perccli/perccli64"
        if not os.path.exists(binary):
            return False, f"alien succeeded but {binary} not found"
        symlink = "/usr/local/bin/perccli"
        if os.path.lexists(symlink):
            os.remove(symlink)
        os.symlink(binary, symlink)
        return True, symlink
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_tools(tools: list[str] | None = None) -> None:
    """Download and install tools. tools=None means all missing ones."""
    _install_fn = {
        "sas2ircu": install_sas2ircu,
        "storcli":  install_storcli,
        "perccli":  install_perccli,
    }
    if tools is None:
        tools = [t for t in _install_fn if not tool_ok(t)]
        if not tools:
            print("  all tools already installed")
            return

    tmp = tempfile.mkdtemp()
    try:
        for name in tools:
            fn = _install_fn.get(name)
            if fn is None:
                print(f"  [✗] {name}: unknown tool")
                continue
            print(f"  [*] {name}...")
            archive = os.path.join(tmp, _ARCHIVE_NAME[name])
            try:
                download(_GDRIVE[name], archive)
            except RuntimeError as exc:
                print(f"  [✗] {name}: {exc}")
                continue
            ok, msg = fn(archive)
            if ok:
                print(f"  [✔] {name} -> {msg}")
            else:
                print(f"  [✗] {name}: {msg}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
