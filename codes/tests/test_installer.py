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
        self.assertEqual(msg, "/usr/local/sbin/sas2ircu")


if __name__ == "__main__":
    unittest.main()
