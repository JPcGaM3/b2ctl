"""Offline install fixtures for the sim.

`b2ctl install --with-tools` was the one verb the sim could not exercise at all:
it reaches the real network (`urllib` → Google Drive), the real `apt-get`, and
the real `/usr/sbin`. So the download policy — the thing F-147 broke and F-149
fixed — had no end-to-end coverage on the harness that exists precisely to give
it that.

This builds a real zip/tar.gz on disk, serves them over `file://` by pointing
`installer._BASE` at the fixture dir, and redirects the install target into
`sim/var/usr-sbin/`. `urllib.request.urlopen` handles `file://` natively, so
nothing about `download()` is stubbed — the size check, the magic bytes, the
digest computation and the pin comparison all run for real.

Wired up by `sim/run`; `sim/bin` supplies the fake `alien`/`apt-get`/`dpkg`.
"""
from __future__ import annotations

import os
import tarfile
import zipfile

# Archive layout mirrors what the real downloads contain, because
# install_sas2ircu()/install_perccli() walk for those exact paths:
#   sas2ircu: any dir containing 'x86_rel' with a 'sas2ircu' file in it
#   perccli : any *.rpm, which `alien -i` then "installs" to /opt/MegaRAID/...
_SAS_INNER = "sas2ircu_linux_x86_rel/sas2ircu"
_PERC_RPM = "perccli-007.0127.0000.0000-1.noarch.rpm"


def _fixture_dir(simvar: str) -> str:
    return os.path.join(simvar, "dl")


def _payload(tag: str, blocks: int = 500) -> bytes:
    """Deterministic but INCOMPRESSIBLE filler.

    download() rejects anything under 1 KB as a Google HTML error page, and a
    run of zeros gzips to ~200 bytes — the first fixture tripped its own guard.
    Hash-chained blocks are random-looking (so gzip cannot shrink them) yet
    reproducible, which keeps the archive's SHA-256 stable enough to pin.
    """
    import hashlib
    out = bytearray()
    seed = tag.encode()
    for _ in range(blocks):
        seed = hashlib.sha256(seed).digest()
        out += seed
    return bytes(out)


# Fixed epoch for every archive member. Reproducibility is not cosmetic here:
# a fixture whose SHA-256 changes on every rebuild cannot be used to test the
# PIN path at all, and the first version of this file silently had that problem
# (gzip stores an mtime header, tar stores mtime/uid/gid per member).
_EPOCH = (1980, 1, 1, 0, 0, 0)


def build_archives(simvar: str) -> str:
    """Create the two archives under sim/var/dl and return that directory.

    Byte-reproducible: same content AND same metadata every time, so the digest
    the sim prints is stable and a test can pin it to exercise the verify path,
    not just the warn path.
    """
    d = _fixture_dir(simvar)
    os.makedirs(d, exist_ok=True)

    zpath = os.path.join(d, "SAS2IRCU_P20.zip")
    if not os.path.exists(zpath):
        with zipfile.ZipFile(zpath, "w") as zf:
            info = zipfile.ZipInfo(_SAS_INNER, date_time=_EPOCH)
            info.external_attr = 0o755 << 16
            # >1 KB or download() rejects it as a Google HTML error page.
            zf.writestr(info, b"#!/bin/sh\necho sas2ircu (sim)\n" + _payload("sas2ircu"))

    tpath = os.path.join(d, "perccli.tar.gz")
    if not os.path.exists(tpath):
        import gzip
        import io as _io
        blob = b"fake-rpm" + _payload("perccli")
        raw = _io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tf:
            ti = tarfile.TarInfo(_PERC_RPM)
            ti.size = len(blob)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            tf.addfile(ti, _io.BytesIO(blob))
        with open(tpath, "wb") as f:
            # mtime=0 or gzip stamps "now" into the header and the digest drifts.
            with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0) as gz:
                gz.write(raw.getvalue())
    return d


def install(simvar: str) -> None:
    """Point installer at the local fixtures + a sandboxed /usr/sbin."""
    from b2ctl import installer

    d = os.path.abspath(build_archives(simvar))
    # _BASE + file_id is the URL, and _GDRIVE maps tool -> file_id. Make the
    # "file id" the archive filename so the concatenation lands on a real path.
    # ABSOLUTE: urllib reads the first path segment of file://x/y as a HOSTNAME
    # and rejects anything but localhost, so a relative dir fails with
    # "file:// scheme is supported only on localhost".
    installer._BASE = "file://" + d + "/"
    installer._GDRIVE = {"sas2ircu": "SAS2IRCU_P20.zip", "perccli": "perccli.tar.gz"}

    sbin = os.path.join(simvar, "usr-sbin")
    os.makedirs(sbin, exist_ok=True)

    _real_install_to = installer._install_to_usr_sbin

    def _sandboxed(src: str, name: str, probe: list) -> tuple:
        dest = os.path.join(sbin, name)
        import shutil
        shutil.copy2(src, dest)
        os.chmod(dest, 0o755)
        return True, dest                 # the fixture is not a runnable binary

    installer._install_to_usr_sbin = _sandboxed

    # alien "installs" the rpm to /opt/MegaRAID/perccli/perccli64 — redirect the
    # existence check and the source path into the sandbox instead.
    _real_perccli = installer.install_perccli

    def _perccli(archive: str):
        binary = os.path.join(sbin, "perccli64")
        with open(binary, "wb") as f:
            f.write(b"fake-perccli64\n")
        return installer._install_to_usr_sbin(binary, "perccli",
                                              installer._PROBE["perccli"])

    installer.install_perccli = _perccli
    # ensure_prereqs shells out to apt-get/dpkg; sim/bin fakes those, but skip
    # the noise entirely — prereqs are not what this fixture is testing.
    installer.ensure_prereqs = lambda tools=None: None
