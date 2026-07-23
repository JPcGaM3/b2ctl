"""Unit tests for b2ctl.smart — ATA/SAS parsing + endurance calculation."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from helpers import (_disk, _ATA_OUTPUT, _SAS_OUTPUT, _SAS_UNCORR_OUTPUT,
                     _NVME_OUTPUT)
from b2ctl import smart
from b2ctl.common import Disk


# Real R740xd NETAPP X357 SAS SSD dump the operator reported for v0.18.0 issue #6
# ('Why it fail? I think, it is passed.'). Healthy: clean error-counter log,
# 0 grown defects, self-test 'Completed'. The trap is 'Non-medium error count:
# 1061' sitting right below the counter log — it must NOT feed d.uncorr.
_SAS_NON_MEDIUM_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Vendor:               NETAPP
Product:              X357_S164A3T8ATE
Revision:             NA55
User Capacity:        3,840,755,982,336 bytes [3.84 TB]
Rotation Rate:        Solid State Device
Serial number:        S5JFNA0R500833
Device type:          disk
Transport protocol:   SAS (SPL-4)
SMART support is:     Enabled

=== START OF READ SMART DATA SECTION ===
SMART Health Status: OK

Percentage used endurance indicator: 0%
Current Drive Temperature:     31 C
Accumulated power on time, hours:minutes 41724:19
Elements in grown defect list: 0

Error counter log:
           Errors Corrected by           Total   Correction     Gigabytes    Total
               ECC          rereads/    errors   algorithm      processed    uncorrected
           fast | delayed   rewrites  corrected  invocations   [10^9 bytes]  errors
read:          0        0         0         0          0      70511.679           0
write:         0        0         0         0          0       6361.349           0
verify:        0        0         0         0          0     347664.599           0

Non-medium error count:     1061

SMART Self-test log
Num  Test              Status                 segment  LifeTime  LBA_first_err [SK ASC ASQ]
     Description                              number   (hours)
# 1  Background long   Completed                   -   41724                 - [-   -    -]
# 2  Background long   Completed                   -   41722                 - [-   -    -]

Long (extended) Self-test duration: 3600 seconds [60.0 minutes]
"""


class TestSmartParsing:
    """Tests for ATA and SAS SMART parsing."""

    def test_parse_ata_extracts_all_fields(self):
        d = Disk(dev="/dev/sda")
        smart._parse_ata(d, _ATA_OUTPUT)
        assert d.model == "Samsung SSD 870 EVO 1TB"
        assert d.serial == "S74ZNS0W582303N"
        assert d.poh == 18238
        assert d.wear_val == 99
        assert d.realloc == 0
        assert d.pending == 0
        assert d.lba_written == 19305985024

    def test_parse_sas_extracts_all_fields(self):
        d = Disk(dev="/dev/sda")
        smart._parse_sas(d, _SAS_OUTPUT)
        assert "SAMSUNG" in d.model
        assert d.serial == "S4F2NY0M105699"
        assert d.poh == 50451
        assert d.wear_val == 99  # 100 - 1%
        assert d.realloc == 0

    def test_parse_sas_uncorrected_errors_bump_critical(self):
        # F-095: column 7 (total uncorrected errors) of the SAS error-counter log
        # feeds d.uncorr even when grown defects are zero; assess() then CRITICAL.
        from b2ctl.common import assess
        d = Disk(dev="/dev/sdw")
        smart._parse_sas(d, _SAS_UNCORR_OUTPUT)
        assert d.uncorr == 14      # read: row column 7
        assert d.realloc == 0      # zero grown defects — uncorr is the only signal
        d.readable = True
        assess(d)
        assert d.level == "CRITICAL"
        assert any("uncorrectable errors" in r for r in d.reasons)

    def test_parse_sas_non_medium_error_count_not_uncorrected(self):
        # v0.18.0 regression: the real R740xd X357 dump the operator reported has
        # a HEALTHY error-counter log (Total uncorrected = 0 in every row) but a
        # large 'Non-medium error count: 1061' one line below it. That line is
        # NOT uncorrected media errors — the parser must read column 7 of the
        # read/write/verify rows (0), not grab 1061, or the disk FAILs spuriously.
        d = Disk(dev="/dev/sdm")
        smart._parse_sas(d, _SAS_NON_MEDIUM_OUTPUT)
        assert d.uncorr == 0            # NOT 1061 (non-medium error count)
        assert d.realloc == 0           # zero grown defects

    def test_parse_sas_non_medium_full_pipeline_pass(self):
        # end-to-end: the exact reported input → burnin.assess PASS (issue #6).
        # read() maps 'SMART Health Status: OK' → PASSED and parses the self-test
        # log; the newest SAS row is a bare 'Completed' (success), stripped clean.
        from b2ctl import burnin
        d = Disk(dev="/dev/sdm", smart_dtype="")
        with patch("b2ctl.smart._smartctl", return_value=_SAS_NON_MEDIUM_OUTPUT):
            smart.read(d, {})
        assert d.health == "PASSED"
        assert d.uncorr == 0
        assert d.selftest_last_result == "Completed"
        assert d.selftest_last_poh == 41724
        with patch("b2ctl.burnin.selftest_status",
                   return_value={"running": False, "pct": 100,
                                 "result": d.selftest_last_result, "eta_min": None}):
            verdict, reasons = burnin.assess(d)
        assert verdict == "PASS", reasons
        assert reasons == []

    def test_endurance_calculation(self):
        d = _disk(lba_written=19305985024, is_ssd=True, model="Samsung SSD 870")
        tbw_table = {"samsung ssd 870": 600}
        smart._endurance(d, tbw_table)
        assert d.written_tb is not None
        assert d.written_tb == pytest.approx(19305985024 * 512 / 1e12, abs=0.01)
        assert d.tbw_rating == 600
        assert d.end_left is not None
        assert 0 <= d.end_left <= 100

    def test_endurance_hdd_no_wear(self):
        d = _disk(is_ssd=False, wear_val=50)
        smart._endurance(d, {})
        assert d.wear_val is None  # HDD: wear is meaningless


class TestSmartRead(unittest.TestCase):
    """Full smart.read() path against a realistic ATA smartctl dump."""

    @patch('b2ctl.smart.run')
    def test_smartctl_ata(self, mock_run):
        mock_run.return_value = """smartctl 7.2 2020-12-30 r5155 [x86_64-linux-5.15.0-76-generic] (local build)
Copyright (C) 2002-20, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Model Family:     Samsung based SSDs
Device Model:     Samsung SSD 870 EVO 1TB
Serial Number:    S74ZNS0W582280E
LU WWN Device Id: 5 002538 e404b9d0b
Firmware Version: SVT02B6Q
User Capacity:    1,000,204,886,016 bytes [1.00 TB]
Sector Size:      512 bytes logical/physical
Rotation Rate:    Solid State Device
Form Factor:      2.5 inches
TRIM Command:     Available, deterministic, zeroed
Device is:        In smartctl database [for details use: -P show]
ATA Version is:   ACS-4 T13/3232-D revision 5
SATA Version is:  SATA 3.3, 6.0 Gb/s (current: 6.0 Gb/s)
Local Time is:    Wed Jun 10 12:00:00 2026 UTC
SMART support is: Available - device has SMART capability.
SMART support is: Enabled

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 1
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       1234
 12 Power_Cycle_Count       0x0032   099   099   000    Old_age   Always       -       56
177 Wear_Leveling_Count     0x0013   095   095   000    Pre-fail  Always       -       45
179 Used_Rsvd_Blk_Cnt_Tot   0x0013   100   100   010    Pre-fail  Always       -       0
181 Program_Fail_Cnt_Total  0x0032   100   100   010    Old_age   Always       -       0
182 Erase_Fail_Count_Total  0x0032   100   100   010    Old_age   Always       -       0
183 Runtime_Bad_Block       0x0013   100   100   010    Pre-fail  Always       -       0
187 Uncorrectable_Error_Cnt 0x0032   100   100   000    Old_age   Always       -       0
190 Airflow_Temperature_Cel 0x0032   065   054   000    Old_age   Always       -       35
195 ECC_Error_Rate          0x001a   200   200   000    Old_age   Always       -       0
199 CRC_Error_Count         0x003e   100   100   000    Old_age   Always       -       0
235 POR_Recovery_Count      0x0012   099   099   000    Old_age   Always       -       32
241 Total_LBAs_Written      0x0032   099   099   000    Old_age   Always       -       123456789
"""
        d = Disk(dev="/dev/sda")
        smart.read(d, {})
        self.assertTrue(d.readable)
        self.assertTrue(d.is_ssd)
        self.assertEqual(d.health, "PASSED")
        self.assertEqual(d.model, "Samsung SSD 870 EVO 1TB")
        self.assertEqual(d.serial, "S74ZNS0W582280E")
        self.assertEqual(d.wear_val, 95)
        self.assertEqual(d.poh, 1234)
        self.assertEqual(d.lba_written, 123456789)
        self.assertEqual(d.realloc, 0)
        self.assertEqual(d.uncorr, 0)
        self.assertFalse(d.selftest_running)     # no test in progress in this dump

    @patch('b2ctl.smart.run')
    def test_smartctl_populates_running_selftest(self, mock_run):
        # A self-test in progress + the two-line recommended polling time -> the
        # status table's TEST% / ETA fields, from the SAME -a output (no 2nd call).
        mock_run.return_value = """=== START OF INFORMATION SECTION ===
Device Model:     Samsung SSD 870 EVO 1TB
Serial Number:    S74ZNS0W582280E
Rotation Rate:    Solid State Device

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

Extended self-test routine
recommended polling time: 	(  90) minutes.

Self-test execution status:      ( 249) Self-test routine in progress...
                                        40% of test remaining.

Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       1234
241 Total_LBAs_Written      0x0032   099   099   000    Old_age   Always       -       123456789
"""
        d = Disk(dev="/dev/sda")
        smart.read(d, {})
        self.assertTrue(d.selftest_running)
        self.assertEqual(d.selftest_pct, 60)     # 100 - 40 remaining
        self.assertEqual(d.selftest_eta, "~36m") # 90 * 40/100 = 36


class TestSasHealthFailure(unittest.TestCase):
    """F-018: a SAS drive predicting failure must map to health=FAILED."""

    @patch('b2ctl.smart.run')
    def test_sas_failure_prediction_maps_to_failed(self, mock_run):
        mock_run.return_value = """=== START OF INFORMATION SECTION ===
Vendor:               SEAGATE
Product:              ST1000
Serial number:        SAS12345
Device type:          disk

Elements in grown defect list: 0

SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]
"""
        d = Disk(dev="/dev/sdz")
        smart.read(d, {})
        self.assertEqual(d.health, "FAILED")
        from b2ctl.common import assess
        assess(d)
        self.assertEqual(d.level, "CRITICAL")

    @patch('b2ctl.smart.run')
    def test_sas_ok_maps_to_passed(self, mock_run):
        mock_run.return_value = _SAS_OUTPUT
        d = Disk(dev="/dev/sdy")
        smart.read(d, {})
        self.assertEqual(d.health, "PASSED")


class TestMegaraidDtype(unittest.TestCase):
    """RAID-mode: smartctl must try -d megaraid,<DID> first when smart_dtype set."""

    def test_smartctl_tries_megaraid_first(self):
        from unittest.mock import patch
        import b2ctl.smart as smart
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            # Return valid SMART only for the megaraid attempt.
            if "megaraid,7" in cmd:
                return "ATTRIBUTE_NAME\nSMART overall-health ... PASSED"
            return ""

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"):
            out = smart._smartctl("/dev/sda", "megaraid,7")
        assert "ATTRIBUTE_NAME" in out
        # first attempt used the forced megaraid type
        assert seen[0] == ["smartctl", "-a", "-d", "megaraid,7", "/dev/sda"]

    def test_megaraid_dtype_never_falls_back_to_raw(self):
        # F-049: a forced megaraid dtype must not attempt the raw VD node — that
        # would read the shared /dev/sda and misattribute the VD SMART.
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return ""   # every attempt fails

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"):
            out = smart._smartctl("/dev/sda", "megaraid,7")
        assert out == ""
        # only the two passthrough forms, never a bare '-a dev' (raw auto-detect)
        assert all("-d" in c for c in seen)
        assert len(seen) == 2


class TestSmartTimeout(unittest.TestCase):
    """F-049: an IT-mode hung disk must not be retried through the attempt ladder.
    A megaraid probe, however, retries ONCE (a timeout there is often just queueing
    behind sibling probes on the shared controller)."""

    def test_ladder_breaks_on_first_timeout(self):
        seen = []

        def _run(cmd, timeout=None, none_on_timeout=False):
            seen.append(cmd)
            return None if none_on_timeout else ""   # simulate TimeoutExpired

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"):
            out = smart._smartctl("/dev/sdx", "")
        assert out == ""
        assert len(seen) == 1   # IT-mode: broke after the first timeout, no retry

    def test_megaraid_retries_once_then_succeeds(self):
        calls = {"n": 0}

        def _run(cmd, timeout=None, none_on_timeout=False):
            calls["n"] += 1
            if calls["n"] == 1:
                return None   # first passthrough probe times out (controller busy)
            return "ATTRIBUTE_NAME\nSMART overall-health ... PASSED"

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"), \
             patch("b2ctl.config.smart_config",
                   return_value={"timeout": 10, "megaraid_workers": 4}):
            out = smart._smartctl("/dev/sda", "megaraid,7")
        assert "ATTRIBUTE_NAME" in out
        assert calls["n"] == 2                 # timed out once -> retried -> read

    def test_megaraid_gives_up_after_retry_still_timing_out(self):
        seen = []

        def _run(cmd, timeout=None, none_on_timeout=False):
            seen.append(cmd)
            return None   # every probe times out

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"), \
             patch("b2ctl.config.smart_config",
                   return_value={"timeout": 10, "megaraid_workers": 4}):
            out = smart._smartctl("/dev/sda", "megaraid,7")
        assert out == ""
        # first form retried once (2 calls), then break — never reached sat+megaraid
        assert len(seen) == 2

    def test_megaraid_timeout_honors_config_value(self):
        seen = []

        def _run(cmd, timeout=None, none_on_timeout=False):
            seen.append(timeout)
            return "ATTRIBUTE_NAME\nSMART overall-health ... PASSED"

        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"), \
             patch("b2ctl.config.smart_config",
                   return_value={"timeout": 25, "megaraid_workers": 2}):
            smart._smartctl("/dev/sda", "megaraid,7")
        assert seen[0] == 25                   # config timeout threaded into run()

    def test_read_marks_noread_on_timeout(self):
        def _run(cmd, timeout=None, none_on_timeout=False):
            return None if none_on_timeout else ""

        d = Disk(dev="/dev/sdx")
        with patch("b2ctl.smart.run", side_effect=_run), \
             patch("b2ctl.config.tool", return_value="smartctl"):
            smart.read(d, {})
        assert d.readable is False and d.health == "NOREAD"


class TestAtaCompositeRaw(unittest.TestCase):
    """F-050/F-051: composite raw parsing + attribute-241 vendor units."""

    _HDR = ("ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      "
            "UPDATED  WHEN_FAILED RAW_VALUE\n")

    def _ata(self, rows):
        return "Device Model:     TestDrive\nSerial Number:    SN1\n" + self._HDR + rows

    def test_composite_poh_takes_leading_integer(self):
        rows = "  9 Power_On_Hours          0x0032   099 099 000 Old_age Always - 29229h+18m+27.459s\n"
        d = Disk(dev="/dev/sdx")
        smart._parse_ata(d, self._ata(rows))
        assert d.poh == 29229

    def test_attr241_32mib_units(self):
        rows = "241 Host_Writes_32MiB       0x0032   099 099 000 Old_age Always - 300000\n"
        d = Disk(dev="/dev/sdx"); d.is_ssd = True
        smart._parse_ata(d, self._ata(rows))
        smart._endurance(d, {})
        # 300000 * 32MiB = 10.07 TB
        assert d.written_tb == pytest.approx(10.066, abs=0.05)

    def test_attr241_plain_lbas_unchanged(self):
        rows = "241 Total_LBAs_Written      0x0032   099 099 000 Old_age Always - 123456789\n"
        d = Disk(dev="/dev/sdx"); d.is_ssd = True
        smart._parse_ata(d, self._ata(rows))
        assert d.lba_written == 123456789

    def test_attr241_gb_units(self):
        rows = "241 Total_GB_Written        0x0032   099 099 000 Old_age Always - 100\n"
        d = Disk(dev="/dev/sdx"); d.is_ssd = True
        smart._parse_ata(d, self._ata(rows))
        smart._endurance(d, {})
        assert d.written_tb == pytest.approx(0.1, abs=0.001)


class TestNvmeParsing(unittest.TestCase):
    """F-096: cover smart._parse_nvme and the NVMe dispatch branch."""

    def test_parse_nvme_extracts_wear_poh_written_uncorr(self):
        d = Disk(dev="/dev/nvme0n1")
        smart._parse_nvme(d, _NVME_OUTPUT)
        self.assertEqual(d.iface, "NVMe")
        self.assertIn("990", d.model)
        self.assertEqual(d.wear_val, 95)              # 100 - 5%
        self.assertEqual(d.poh, 1234)                 # 'Power On Hours: 1,234'
        self.assertEqual(d.lba_written, 12345678 * 1000)
        self.assertEqual(d.uncorr, 7)                 # Media and Data Integrity Errors

    @patch('b2ctl.smart.run')
    def test_read_dispatches_nvme_not_sas(self, mock_run):
        mock_run.return_value = _NVME_OUTPUT
        d = Disk(dev="/dev/nvme0n1")
        smart.read(d, {})
        self.assertTrue(d.readable)
        self.assertEqual(d.health, "PASSED")
        # Dispatched to _parse_nvme, NOT _parse_sas: the SAS path would set
        # iface='SAS' and leave wear_val/poh unset (it looks for different field
        # names), so these values prove the NVMe branch ran.
        self.assertEqual(d.iface, "NVMe")
        self.assertIn("990", d.model)
        self.assertEqual(d.wear_val, 95)
        self.assertEqual(d.poh, 1234)
        self.assertEqual(d.lba_written, 12345678 * 1000)

    def test_data_units_written_1000x_conversion(self):
        d = Disk(dev="/dev/nvme0n1")
        smart._parse_nvme(d, _NVME_OUTPUT)
        units = 12345678
        self.assertEqual(d.lba_written, units * 1000)
        self.assertNotEqual(d.lba_written, units)     # guard a dropped *1000


# ATA self-test LOG block (newest first) — an Extended test + a Short test.
_SELFTEST_LOG_ATA = """\
SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)  LBA_of_first_error
# 1  Extended offline    Completed without error       00%     18238         -
# 2  Short offline       Completed without error       00%     18200         -
"""

_SELFTEST_LOG_ATA_FAIL = """\
SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)  LBA_of_first_error
# 1  Extended offline    Completed: read failure       90%     12000         0x0badf00d
"""

_SELFTEST_LOG_SAS = """\
SMART Self-test log
Num  Test              Status                 segment  LifeTime  LBA_first_err [SK ASC ASQ]
     Description                              number   (hours)
# 1  Background long   Completed                   -   50451                 - [- - -]
"""

# NVMe uses a distinct 'Self-test Log (NVMe Log 0x06)' block with bare-index
# rows (no '#') and a Power_on_Hours column instead of LifeTime(hours).
_SELFTEST_LOG_NVME = """\
Self-test Log (NVMe Log 0x06)
Self-test status: No self-test in progress
Num  Test_Description  Status                       Power_on_Hours  Failing_LBA  NSID Seg SCT Code
 0   Extended          Completed without error                 736            -     -   -   -    -
 1   Short             Completed without error                 720            -     -   -   -    -
"""


class TestSelftestLog(unittest.TestCase):
    """Passive read of the drive's self-test LOG -> selftest_last_* (HEALTH_CHK)."""

    def test_parse_extended_ok(self):
        res, hrs = smart._parse_selftest_log(_SELFTEST_LOG_ATA)
        assert res == "Completed without error"
        assert hrs == 18238

    def test_parse_extended_failure(self):
        res, hrs = smart._parse_selftest_log(_SELFTEST_LOG_ATA_FAIL)
        assert "read failure" in res
        assert hrs == 12000

    def test_parse_sas_background_long(self):
        res, hrs = smart._parse_selftest_log(_SELFTEST_LOG_SAS)
        assert res == "Completed"
        assert hrs == 50451

    def test_parse_nvme_extended(self):
        # bare-index rows under the NVMe block; newest (index 0) Extended wins,
        # Short (index 1) is skipped, Power_on_Hours is the numeric column.
        res, hrs = smart._parse_selftest_log(_SELFTEST_LOG_NVME)
        assert res == "Completed without error"
        assert hrs == 736

    def test_nvme_block_not_matched_without_header(self):
        # a bare-index row with no 'Self-test Log (NVMe' header must NOT be parsed
        # (guards against matching ATA attribute rows that also start with digits).
        assert smart._parse_selftest_log(
            " 0   Extended   Completed without error   736\n") == ("", None)

    def test_no_log_returns_empty(self):
        assert smart._parse_selftest_log(_ATA_OUTPUT) == ("", None)

    @patch("b2ctl.smart._smartctl")
    def test_read_populates_selftest_last_fields(self, mock_sc):
        mock_sc.return_value = _ATA_OUTPUT + "\n" + _SELFTEST_LOG_ATA
        d = _disk(dev="/dev/sdz", smart_dtype="")
        smart.read(d, {})
        assert d.selftest_last_result == "Completed without error"
        assert d.selftest_last_poh == 18238


if __name__ == "__main__":
    unittest.main()
