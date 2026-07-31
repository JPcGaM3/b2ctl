"""Unit tests for b2ctl.installer — execute-verification (no false ✔)."""
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import b2ctl.installer as installer


class TestExecutes(unittest.TestCase):

    def test_executes_true_on_real_binary(self):
        # The running interpreter is guaranteed present + executable.
        self.assertTrue(installer._executes(sys.executable, ["--version"]))

    def test_executes_false_on_missing_path(self):
        self.assertFalse(installer._executes("/no/such/binary", []))

    def test_executes_false_on_non_executable_junk(self):
        # A non-ELF file with +x cannot be exec'd → OSError → False.
        fd, path = tempfile.mkstemp()
        try:
            os.write(fd, b"\x00\x01 not an elf \xff")
            os.close(fd)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            self.assertFalse(installer._executes(path, []))
        finally:
            os.unlink(path)


class TestInstallSas2ircuVerify(unittest.TestCase):
    """fix: install_sas2ircu must NOT report success when the binary won't run."""

    def _make_archive(self, dest_dir: str) -> str:
        # Minimal zip with the path install_sas2ircu walks for: */x86_rel/sas2ircu
        archive = os.path.join(dest_dir, "SAS2IRCU_P20.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("SAS2IRCU/sas2ircu_linux_x86_rel/sas2ircu", b"fake-binary")
        return archive

    def test_install_fails_when_not_executable(self):
        tmp = tempfile.mkdtemp()
        archive = self._make_archive(tmp)
        # copy2/chmod mocked to no-ops (don't touch /usr/local); force the
        # verify probe to report "won't run" (simulates the missing loader).
        with patch("shutil.copy2"), \
             patch("os.chmod"), \
             patch("b2ctl.installer._executes", return_value=False):
            ok, msg = installer.install_sas2ircu(archive)
        self.assertFalse(ok)
        self.assertIn("won't execute", msg)

    def test_install_succeeds_when_executable(self):
        tmp = tempfile.mkdtemp()
        archive = self._make_archive(tmp)
        with patch("shutil.copy2"), \
             patch("os.chmod"), \
             patch("b2ctl.installer._executes", return_value=True):
            ok, msg = installer.install_sas2ircu(archive)
        self.assertTrue(ok)
        self.assertEqual(msg, "/usr/sbin/sas2ircu")


class TestInstallProfile(unittest.TestCase):
    """--perc/--flash install the right tool subset + set the matching mode."""

    def test_perc_profile(self):
        with patch("b2ctl.installer.install_tools") as it, \
             patch("b2ctl.installer.tool_ok", return_value=True), \
             patch("b2ctl.config.set_mode") as sm:
            installer.install_profile("perc")
        it.assert_called_once_with(["perccli"])
        sm.assert_called_once_with("raid")

    def test_flash_profile(self):
        with patch("b2ctl.installer.install_tools") as it, \
             patch("b2ctl.installer.tool_ok", return_value=True), \
             patch("b2ctl.config.set_mode") as sm:
            installer.install_profile("flash")
        it.assert_called_once_with(["sas2ircu"])
        sm.assert_called_once_with("it")

    def test_unknown_profile_noops(self):
        with patch("b2ctl.installer.install_tools") as it, \
             patch("b2ctl.config.set_mode") as sm:
            installer.install_profile("bogus")
        it.assert_not_called()
        sm.assert_not_called()

    def test_mode_not_set_when_tool_install_failed(self):
        # F-045: a failed tool install must NOT persist controller.mode.
        with patch("b2ctl.installer.install_tools"), \
             patch("b2ctl.installer.tool_ok", return_value=False), \
             patch("b2ctl.config.set_mode") as sm:
            installer.install_profile("perc")
        sm.assert_not_called()


class TestDownload(unittest.TestCase):
    """F-043/F-044: integrity + timeout + offline handling."""

    def _fake_urlopen(self, payload):
        import contextlib

        @contextlib.contextmanager
        def _cm(url, timeout=None):
            import io
            yield io.BytesIO(payload)
        return _cm

    def test_hash_mismatch_raises(self):
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "a.bin")
        with patch("urllib.request.urlopen", self._fake_urlopen(b"x" * 4096)):
            with self.assertRaises(RuntimeError):
                installer.download("id", dest, sha256="0" * 64)

    def test_matching_hash_passes(self):
        import hashlib
        payload = b"y" * 4096
        digest = hashlib.sha256(payload).hexdigest()
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "a.bin")
        with patch("urllib.request.urlopen", self._fake_urlopen(payload)):
            installer.download("id", dest, sha256=digest)   # must not raise
        self.assertTrue(os.path.exists(dest))

    def test_install_tools_offline_prints_error(self):
        import urllib.error
        with patch("b2ctl.installer.ensure_prereqs"), \
             patch("b2ctl.installer.tool_ok", return_value=False), \
             patch("b2ctl.installer.download",
                   side_effect=urllib.error.URLError("offline")):
            # must complete without raising (URLError is an OSError)
            installer.install_tools(["sas2ircu"])


class TestInstallBase(unittest.TestCase):
    """`b2ctl install` (no flag) = base report, no download (mirrors ./install.sh)."""

    def test_base_reports_and_downloads_nothing(self):
        import io
        import contextlib
        with patch("b2ctl.installer.tool_ok", return_value=True), \
             patch("b2ctl.config.controller_mode", return_value="it"), \
             patch("b2ctl.installer.download") as dl, \
             patch("b2ctl.installer.install_tools") as it, \
             patch("b2ctl.installer.ensure_prereqs") as ep:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                installer.install_base()
            out = buf.getvalue()
        self.assertIn("sas2ircu", out)
        self.assertIn("perccli", out)
        self.assertIn("controller.mode = it", out)
        self.assertIn("--with-tools", out)
        dl.assert_not_called()
        it.assert_not_called()
        ep.assert_not_called()


def _install_sh_text() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    codes = os.path.dirname(here)
    with open(os.path.join(codes, "install.sh")) as f:
        return f.read()


class TestGDriveParity(unittest.TestCase):
    """F-122: the Google Drive file IDs are duplicated in install.sh; the
    fallback IDs it hardcodes must stay equal to installer._GDRIVE."""

    def test_gdrive_ids_match_install_sh(self):
        import re
        sh = _install_sh_text()
        m_sas = re.search(r"_GDRIVE_SAS2IRCU:=([A-Za-z0-9_-]+)", sh)
        m_perc = re.search(r"_GDRIVE_PERCCLI:=([A-Za-z0-9_-]+)", sh)
        self.assertIsNotNone(m_sas, "sas2ircu fallback ID not found in install.sh")
        self.assertIsNotNone(m_perc, "perccli fallback ID not found in install.sh")
        self.assertEqual(m_sas.group(1), installer._GDRIVE["sas2ircu"])
        self.assertEqual(m_perc.group(1), installer._GDRIVE["perccli"])


class TestPrereqParity(unittest.TestCase):
    """F-087: drift-guard — every apt package installer.py declares as a prereq
    must also appear in install.sh's apt set (the documented '1:1 mirror')."""

    def _install_sh_pkgs(self):
        import re
        pkgs = set()
        for chunk in re.findall(r'_pkgs="([^"]*)"', _install_sh_text()):
            for tok in chunk.split():
                if "$" in tok or "{" in tok:      # skip the ${_pkgs} back-ref
                    continue
                pkgs.add(tok)
        return pkgs

    def test_installer_prereqs_are_in_install_sh(self):
        sh_pkgs = self._install_sh_pkgs()
        self.assertTrue(sh_pkgs, "no _pkgs= assignments parsed from install.sh")
        declared = set(installer.RUNTIME_PKGS
                       + installer.PREREQ_SAS2IRCU
                       + installer.PREREQ_PERCCLI)
        missing = declared - sh_pkgs
        self.assertEqual(missing, set(),
                         f"installer prereqs missing from install.sh: {missing}")


class TestDownloadFailsClosed(unittest.TestCase):
    """F-147 — download() refuses BEFORE opening a connection when no SHA-256
    pin is available, and the explicit opt-out bypasses that refusal."""

    def _fake_urlopen(self, payload):
        import contextlib
        import io

        @contextlib.contextmanager
        def _cm(url, timeout=None):
            yield io.BytesIO(payload)
        return _cm

    def test_missing_pin_refuses_without_touching_the_network(self):
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "a.bin")
        with patch("urllib.request.urlopen") as uo:
            with self.assertRaises(RuntimeError) as ctx:
                installer.download("id", dest)          # no sha256, no opt-out
        uo.assert_not_called()                            # refused before any request
        self.assertFalse(os.path.exists(dest))             # nothing written
        self.assertIn("sha256sum", str(ctx.exception))     # tells the operator how to fix it
        self.assertIn(installer._ALLOW_UNVERIFIED_ENV, str(ctx.exception))

    def test_allow_unverified_bypasses_the_refusal(self):
        payload = b"z" * 4096
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "a.bin")
        with patch("urllib.request.urlopen", self._fake_urlopen(payload)):
            installer.download("id", dest, allow_unverified=True)   # must not raise
        self.assertTrue(os.path.exists(dest))

    def test_pinned_sha256_still_works_without_the_opt_out(self):
        # A real pin satisfies the gate on its own — allow_unverified stays False.
        import hashlib
        payload = b"y" * 4096
        digest = hashlib.sha256(payload).hexdigest()
        tmp = tempfile.mkdtemp()
        dest = os.path.join(tmp, "a.bin")
        with patch("urllib.request.urlopen", self._fake_urlopen(payload)):
            installer.download("id", dest, sha256=digest)   # must not raise
        self.assertTrue(os.path.exists(dest))


class TestInstallToolsUnverifiedEnv(unittest.TestCase):
    """install_tools() is the ONLY place the env var opt-out is read; download()
    itself never touches the environment."""

    def test_env_unset_leaves_allow_unverified_false(self):
        with patch("b2ctl.installer.ensure_prereqs"), \
             patch("b2ctl.installer.download") as dl, \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop(installer._ALLOW_UNVERIFIED_ENV, None)
            installer.install_tools(["sas2ircu"])
        self.assertFalse(dl.call_args.kwargs.get("allow_unverified"))

    def test_env_set_passes_allow_unverified_true(self):
        with patch("b2ctl.installer.ensure_prereqs"), \
             patch("b2ctl.installer.download") as dl, \
             patch.dict(os.environ, {installer._ALLOW_UNVERIFIED_ENV: "1"}):
            installer.install_tools(["sas2ircu"])
        self.assertTrue(dl.call_args.kwargs.get("allow_unverified"))


class TestAlienNoScripts(unittest.TestCase):
    """F-147 — `alien --scripts` runs the vendor RPM's maintainer scriptlets
    as root; the only artefact consumed afterwards is perccli64 (a plain data
    file alien -i extracts regardless of --scripts), so neither install path
    may pass it. Source-text drift-guard mirrors TestGDriveParity's idiom;
    the behavioural test pins the actual subprocess.run call."""

    def test_installer_py_source_omits_scripts_flag(self):
        import inspect
        src = inspect.getsource(installer.install_perccli)
        self.assertNotIn("--scripts", src)

    def test_install_sh_omits_scripts_flag(self):
        self.assertNotIn("--scripts", _install_sh_text())

    def test_alien_invoked_without_scripts_flag(self):
        import tarfile
        tmp = tempfile.mkdtemp()
        rpm_path = os.path.join(tmp, "perccli.rpm")
        with open(rpm_path, "wb") as f:
            f.write(b"fake-rpm")
        archive = os.path.join(tmp, "perccli.tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(rpm_path, arcname="perccli.rpm")
        fake_proc = unittest.mock.Mock(returncode=0, stdout="", stderr="")
        with patch("b2ctl.installer.subprocess.run", return_value=fake_proc) as sp, \
             patch("b2ctl.installer.os.path.exists", return_value=True), \
             patch("b2ctl.installer._install_to_usr_sbin",
                   return_value=(True, "/usr/sbin/perccli")):
            ok, msg = installer.install_perccli(archive)
        self.assertTrue(ok)
        called_args = sp.call_args[0][0]
        self.assertIn("alien", called_args)
        self.assertNotIn("--scripts", called_args)


if __name__ == "__main__":
    unittest.main()
