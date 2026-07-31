"""b2ctl.installer — download and install sas2ircu (IT) / perccli (RAID).

storcli was dropped: it is the LSI tool, blind to a Dell PERC, and only caused
false RAID detection. RAID mode uses perccli; IT/HBA mode uses sas2ircu.
Binaries are copied (cp -f) to /usr/sbin so they survive deletion of the
download dir or /opt/MegaRAID — matching install.sh.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

_GDRIVE = {
    "sas2ircu": "1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt",
    "perccli":  "1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk",
}
# Pinned SHA-256 per archive. These binaries run as root on both nodes, so a
# pinned archive that does not match is rejected before it is installed (F-043).
#
# THE TABLE IS EMPTY, AND THAT IS AN HONEST DEFAULT — not a claim that verification
# happens. With no pin, download() fetches the archive and says so, printing the
# digest of what it just got in a form you can paste back into this dict; the next
# install then verifies. F-147 made an empty table REFUSE instead, which bought
# nothing (the archive was exactly as unverified either way) and only stopped
# `b2ctl install --with-tools` from working at all (F-149).
#
# To pin: get the archive from a copy you trust, `sha256sum SAS2IRCU_P20.zip`,
# and add it here. install.sh reads this same dict, so the two paths cannot drift.
_SHA256: dict[str, str] = {}
# Set B2CTL_REQUIRE_PINNED=1 to demand a pin — F-147's fail-closed behaviour, kept
# as an opt-IN for an operator who wants it rather than a default nobody asked for.
_REQUIRE_PINNED_ENV = "B2CTL_REQUIRE_PINNED"
_BASE = "https://drive.usercontent.google.com/download?export=download&confirm=t&id="
_ARCHIVE_NAME = {
    "sas2ircu": "SAS2IRCU_P20.zip",
    "perccli":  "perccli.tar.gz",
}
# Harmless probe args used to confirm a tool can actually run (any exit code).
_PROBE = {"sas2ircu": ["list"], "perccli": ["show"]}
# Install profiles: which tools + which controller mode each one sets.
_PROFILE_TOOLS = {"perc": ["perccli"], "flash": ["sas2ircu"]}
_PROFILE_MODE = {"perc": "raid", "flash": "it"}


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


def _sha256_file(path: str) -> str:
    """SHA-256 of a file, streamed in 1 MiB chunks (archives are tens of MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _warn_unpinned(name: str, digest: str) -> None:
    """Say the archive was not verified, and hand over the line that fixes it.

    The point of F-149: pinning should be a copy-paste, not homework. Refusing to
    install (F-147) protected nothing — the archive was unverified either way —
    so the useful thing is to install, be honest about it, and print the digest
    in exactly the form `_SHA256` wants.
    """
    print(f"  [!] UNVERIFIED — no pinned digest for {name}.")
    print(f"      It came from Google Drive and will run as root on this host.")
    print(f"      To pin it for every future install, add to installer._SHA256:")
    print(f'          "{name}": "{digest}",')


def download(file_id: str, dest_path: str, *, sha256: str | None = None,
             name: str = "") -> None:
    """Download a Google Drive file to dest_path.

    Uses urlopen with a 60 s timeout so a black-holed connection can't hang the
    install forever (F-043). Raises RuntimeError on a <1 KB result (HTML error
    page) or, when a hash is pinned, on a SHA-256 mismatch (tampered archive).

    With NO pin the download proceeds and `_warn_unpinned` states that plainly,
    printing the digest so the operator can pin it in one paste (F-149). Set
    B2CTL_REQUIRE_PINNED=1 to demand a pin instead — that refuses before opening
    a connection, which is F-147's behaviour kept as an opt-in.
    """
    name = name or os.path.basename(dest_path)
    if not sha256 and os.environ.get(_REQUIRE_PINNED_ENV) == "1":
        raise RuntimeError(
            f"no pinned SHA-256 for {name} and {_REQUIRE_PINNED_ENV}=1 — "
            f"refusing to download unverified content that will run as root. "
            f"Add the trusted digest (sha256sum {os.path.basename(dest_path)}) "
            f"to installer._SHA256, or unset {_REQUIRE_PINNED_ENV}.")
    url = _BASE + file_id
    print(f"    downloading...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest_path, "wb") as f:
        shutil.copyfileobj(resp, f)
    size = os.path.getsize(dest_path)
    if size < 1024:
        raise RuntimeError(f"download too small ({size} bytes) — may be HTML error page")
    got = _sha256_file(dest_path)          # always computed: it is the thing to print
    if sha256:
        if got != sha256:
            raise RuntimeError(f"sha256 mismatch — expected {sha256}, got {got}; "
                               f"refusing to install a tampered archive")
        print(f" {size // 1024} KB (sha256 verified)")
        return
    print(f" {size // 1024} KB")
    _warn_unpinned(name, got)


def _install_to_usr_sbin(src: str, name: str, probe: list[str]) -> tuple[bool, str]:
    """cp -f a binary to /usr/sbin/<name>, chmod +x, verify it executes."""
    dest = f"/usr/sbin/{name}"
    shutil.copy2(src, dest)
    os.chmod(dest, 0o755)
    if not _executes(dest, probe):
        return False, "installed but won't execute (missing runtime libs)"
    return True, dest


def install_sas2ircu(archive: str) -> tuple[bool, str]:
    """Extract linux_x86_rel/sas2ircu from zip, cp -f to /usr/sbin/sas2ircu."""
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
        ok, msg = _install_to_usr_sbin(sas, "sas2ircu", _PROBE["sas2ircu"])
        if not ok:
            return False, (msg + " — 32-bit loader missing; "
                           "run: apt-get install -y libc6-i386")
        return True, msg
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_perccli(archive: str) -> tuple[bool, str]:
    """Extract *.rpm from tar.gz, alien -i, cp -f perccli64 -> /usr/sbin/perccli."""
    tmp = tempfile.mkdtemp()
    try:
        with tarfile.open(archive) as tf:
            # filter="data" (stdlib 3.12+) rejects '../' path-traversal members —
            # a tampered archive can't write /usr/sbin/zpool as root (F-086).
            tf.extractall(tmp, filter="data")
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
        # alien's maintainer-scriptlet flag is intentionally OMITTED: it would
        # run the vendor RPM's postinst as root, and the only artefact
        # consumed afterwards is perccli64 (a plain data file `alien -i`
        # extracts on its own), so those scriptlets are unneeded attack
        # surface (F-147). install.sh's alien invocation must match.
        r = subprocess.run(["alien", "-i", rpm],
                           cwd=tmp, capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"alien failed: {r.stderr.strip()}"
        binary = "/opt/MegaRAID/perccli/perccli64"
        if not os.path.exists(binary):
            return False, f"alien succeeded but {binary} not found"
        return _install_to_usr_sbin(binary, "perccli", _PROBE["perccli"])
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Prereq package tiers — the single source of truth shared with install.sh
# (F-087/F-111). Per-tool prereqs install ONLY when that tool is requested so
# --perc never registers i386 on a RAID box; runtime deps are always useful.
PREREQ_SAS2IRCU = ["libc6-i386"]        # 32-bit ELF loader (needs dpkg i386 arch)
PREREQ_PERCCLI = ["alien"]              # RPM -> .deb conversion
RUNTIME_PKGS = ["smartmontools", "zfsutils-linux", "gdisk"]


def ensure_prereqs(tools: list[str] | None = None) -> None:
    """Install + verify apt prerequisites for the requested tool subset.

    - alien      : perccli ships only as an RPM; alien converts it to a .deb.
    - libc6-i386 : sas2ircu is a 32-bit ELF and needs the i386 multiarch loader.
                   On amd64 Debian/Proxmox the i386 architecture must be
                   registered (dpkg --add-architecture i386) and the cache
                   refreshed before apt can even see libc6-i386 — do that first.

    Verifies the OUTCOME (does the 32-bit loader exist?) rather than trusting
    apt's exit code, and surfaces the apt error tail when it really failed.
    """
    tools = tools if tools is not None else ["sas2ircu", "perccli"]
    want_sas = "sas2ircu" in tools
    want_perc = "perccli" in tools
    pkgs = list(RUNTIME_PKGS)
    if want_sas:
        pkgs += PREREQ_SAS2IRCU
    if want_perc:
        pkgs += PREREQ_PERCCLI
    print(f"  [*] ensuring prerequisites ({', '.join(pkgs)})...")
    if want_sas:                         # only touch i386 when sas2ircu is wanted
        subprocess.run(["dpkg", "--add-architecture", "i386"],
                       capture_output=True, check=False)
    subprocess.run(["apt-get", "update", "-qq"],
                   capture_output=True, check=False)
    r = subprocess.run(["apt-get", "install", "-y", *pkgs],
                       capture_output=True, text=True, check=False)

    if want_sas:
        loader_ok = any(os.path.exists(p) for p in
                        ("/lib/ld-linux.so.2", "/lib32/ld-linux.so.2"))
        if not loader_ok:
            print("  [✗] libc6-i386 not active — sas2ircu (32-bit) will not run.")
            for ln in (r.stderr or r.stdout or "").strip().splitlines()[-3:]:
                print(f"        apt: {ln}")
            print("        fix: dpkg --add-architecture i386 && apt-get update "
                  "&& apt-get install -y libc6-i386")
    if want_perc and shutil.which("alien") is None:
        print("  [✗] alien not installed — perccli install will fail.")


def install_tools(tools: list[str] | None = None) -> None:
    """Download and install tools. tools=None means all missing ones."""
    _install_fn = {
        "sas2ircu": install_sas2ircu,
        "perccli":  install_perccli,
    }
    if tools is None:
        tools = [t for t in _install_fn if not tool_ok(t)]
        if not tools:
            print("  all tools already installed")
            return
    ensure_prereqs(tools)                # only the prereqs the subset needs (F-111)

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
                download(_GDRIVE[name], archive, sha256=_SHA256.get(name),
                         name=name)
            except (RuntimeError, OSError) as exc:
                # OSError covers urllib URLError/HTTPError/socket errors on an
                # offline box — print the clean line, don't traceback (F-044).
                print(f"  [✗] {name}: {exc}")
                continue
            ok, msg = fn(archive)
            if ok:
                print(f"  [✔] {name} -> {msg}")
            else:
                print(f"  [✗] {name}: {msg}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def install_base() -> None:
    """No-download status report — the CLI mirror of a plain `./install.sh`.

    b2ctl is already installed (we are running from it), so there is nothing to
    deploy and nothing to download. Show which tools are present and the current
    controller mode, and point at the flags that actually add tools.
    """
    from . import config as _cfg
    print("  b2ctl package: installed")
    for t in ("sas2ircu", "perccli"):
        print(f"  {'[ok]' if tool_ok(t) else '[--]'} {t}")
    print(f"  controller.mode = {_cfg.controller_mode()}")
    print("  add tools:  b2ctl install --with-tools | --perc | --flash")


def install_profile(profile: str) -> None:
    """Install the tools for a profile and set the matching controller mode.

    'perc'  -> perccli  + controller.mode=raid
    'flash' -> sas2ircu + controller.mode=it
    """
    from . import config as _cfg
    tools = _PROFILE_TOOLS.get(profile)
    if tools is None:
        print(f"  [✗] unknown profile: {profile}")
        return
    install_tools(tools)
    mode = _PROFILE_MODE[profile]
    # Only commit the mode if every requested tool is actually present AND
    # executable — otherwise the box is forced onto a backend it cannot serve
    # (e.g. mode=raid with no working perccli), breaking every later run (F-045).
    if all(tool_ok(t) for t in tools):
        _cfg.set_mode(mode)
        print(f"  [✔] controller.mode = {mode}  ({_cfg.CONFIG_PATH})")
    else:
        print(f"  [!] controller.mode left unchanged — {profile} tool install "
              f"failed; fix it then re-run, or set controller.mode by hand.")
