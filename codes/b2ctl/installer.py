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
# Harmless probe args used to confirm a tool can actually run (any exit code).
_PROBE = {"sas2ircu": ["list"], "storcli": ["show"], "perccli": ["show"]}


def _executes(path: str, probe: list[str]) -> bool:
    """True if the binary can exec at all (any exit code counts as 'runs').

    A 32-bit ELF whose loader (/lib/ld-linux.so.2) is missing fails execve with
    ENOENT, which subprocess surfaces as FileNotFoundError even though the file
    is present — exactly the 'cannot execute: required file not found' case.
    """
    try:
        subprocess.run([path, *probe], capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def tool_ok(name: str) -> bool:
    """Return True if tool binary is present AND executes."""
    path = shutil.which(name)
    return path is not None and _executes(path, _PROBE.get(name, []))


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
        dest = "/usr/local/sbin/sas2ircu"
        shutil.copy2(sas, dest)
        os.chmod(dest, 0o755)
        if not _executes(dest, _PROBE["sas2ircu"]):
            return False, ("installed but won't execute — 32-bit loader missing; "
                           "run: apt-get install -y libc6-i386")
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
        if not _executes(symlink, _PROBE["storcli"]):
            return False, "installed but won't execute (check shared libs)"
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


def ensure_prereqs() -> None:
    """Install + verify apt prerequisites the tool binaries need.

    - alien      : perccli ships only as an RPM; alien converts it to a .deb.
    - libc6-i386 : sas2ircu is a 32-bit ELF and needs the i386 multiarch loader.
                   On amd64 Debian/Proxmox the i386 architecture must be
                   registered (dpkg --add-architecture i386) and the cache
                   refreshed before apt can even see libc6-i386 — do that first.

    Verifies the OUTCOME (does the 32-bit loader exist?) rather than trusting
    apt's exit code, and surfaces the apt error tail when it really failed.
    """
    print("  [*] ensuring prerequisites (alien, libc6-i386)...")
    subprocess.run(["dpkg", "--add-architecture", "i386"],
                   capture_output=True, check=False)
    subprocess.run(["apt-get", "update", "-qq"],
                   capture_output=True, check=False)
    r = subprocess.run(["apt-get", "install", "-y", "alien", "libc6-i386"],
                       capture_output=True, text=True, check=False)

    loader_ok = any(os.path.exists(p) for p in
                    ("/lib/ld-linux.so.2", "/lib32/ld-linux.so.2"))
    if not loader_ok:
        print("  [✗] libc6-i386 not active — sas2ircu (32-bit) will not run.")
        for ln in (r.stderr or r.stdout or "").strip().splitlines()[-3:]:
            print(f"        apt: {ln}")
        print("        fix: dpkg --add-architecture i386 && apt-get update "
              "&& apt-get install -y libc6-i386")
    if shutil.which("alien") is None:
        print("  [✗] alien not installed — perccli install will fail.")


def install_tools(tools: list[str] | None = None) -> None:
    """Download and install tools. tools=None means all missing ones."""
    _install_fn = {
        "sas2ircu": install_sas2ircu,
        "storcli":  install_storcli,
        "perccli":  install_perccli,
    }
    ensure_prereqs()
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
