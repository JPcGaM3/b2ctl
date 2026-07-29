"""Unit tests for b2ctl.hba_raid — RAID-mode (perccli) parsing + enumeration."""
import unittest
from unittest.mock import patch

import b2ctl.hba_raid as raid
import b2ctl.raid_actions as ra


# Real `perccli /c0/vall show all` output from a Dell R640 / PERC H730P Mini.
_VALL = """Controller = 0
Status = Success
Description = None


/c0/v0 :
======

----------------------------------------------------------------
DG/VD TYPE  State Access Consist Cache Cac sCC     Size Name
----------------------------------------------------------------
0/0   RAID1 Optl  RW     Yes     RWBD  -   OFF 640.0 GB MainSSD
----------------------------------------------------------------


PDs for VD 0 :
============

------------------------------------------------------------------------------
EID:Slt DID State DG     Size Intf Med SED PI SeSz Model                   Sp
------------------------------------------------------------------------------
32:0      0 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U
32:1      1 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U
------------------------------------------------------------------------------
"""


class TestParseVall(unittest.TestCase):

    def test_parses_volume(self):
        vols, _ = raid._parse_vall(_VALL)
        self.assertEqual(len(vols), 1)
        v = vols[0]
        self.assertEqual(v["vd"], "0")
        self.assertEqual(v["raid"], "RAID1")
        self.assertEqual(v["state"], "Optl")
        self.assertEqual(v["size"], "640.0 GB")
        self.assertEqual(v["name"], "MainSSD")

    def test_parses_members(self):
        _, members = raid._parse_vall(_VALL)
        self.assertEqual(len(members), 2)
        m0, m1 = members
        self.assertEqual(m0["bay"], "32:0")
        self.assertEqual(m0["did"], "0")
        self.assertEqual(m0["state"], "Onln")
        self.assertEqual(m0["med"], "SSD")
        self.assertEqual(m0["model"], "Samsung SSD 870 EVO 1TB")
        self.assertEqual(m1["bay"], "32:1")
        self.assertEqual(m1["did"], "1")


class TestIsPercVd(unittest.TestCase):

    def test_perc_model_is_vd(self):
        self.assertTrue(raid._is_perc_vd("PERC H730P Mini"))
        self.assertTrue(raid._is_perc_vd("AVAGO MegaRAID"))

    def test_real_disk_is_not_vd(self):
        self.assertFalse(raid._is_perc_vd("Samsung SSD 870 EVO 1TB"))
        self.assertFalse(raid._is_perc_vd(""))


class TestPickTool(unittest.TestCase):

    def test_prefers_tool_with_nonzero_controllers(self):
        # perccli64 not installed (run empty); perccli reports a controller.
        def _run(cmd):
            if cmd[0] == "perccli":
                return "Controller Count = 1"
            return ""
        with patch.object(raid, "run", side_effect=_run), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = None
            self.assertEqual(raid._pick_tool(), "perccli")
        raid._tool_cache = None

    def test_have_tool_false_when_zero_controllers(self):
        with patch.object(raid, "run", return_value="Controller Count = 0"), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            self.assertFalse(raid.have_tool())


class TestEnumerate(unittest.TestCase):

    def test_members_synthesised_and_vd_dropped(self):
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730P Mini"
        nvme = Disk(dev="/dev/nvme0n1"); nvme.model = "Samsung SSD 990 EVO"
        vols, members = raid._parse_vall(_VALL)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)), \
             patch.object(raid, "bay_map", return_value={}), \
             patch("b2ctl.hba.enumerate_disks", return_value=[sda, nvme]):
            disks = raid.enumerate_disks()
        devs = [d.dev for d in disks]
        # VD block device (sda) dropped; nvme kept; 2 HW members added.
        self.assertNotIn("/dev/sda", [d.dev for d in disks if d.array_type != "HW"])
        hw = [d for d in disks if d.array_type == "HW"]
        self.assertEqual(len(hw), 2)
        self.assertEqual(hw[0].smart_dtype, "megaraid,0")
        self.assertEqual(hw[0].dev, "/dev/sda")          # megaraid target
        self.assertEqual(hw[0].array_name, "vd0/raid1")
        self.assertIn("/dev/nvme0n1", devs)

    def test_ugood_drives_enumerated_as_available_not_ghost(self):
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730 Mini"
        nvme = Disk(dev="/dev/nvme0n1"); nvme.model = "Samsung 990 EVO"
        vols, members = raid._parse_vall(_VALL)        # 2 members (32:0/32:1)
        eall = (
            "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model               Sp\n"
            "32:0      0 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "32:1      1 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "32:4      4 UGood  - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "32:5      5 UGood  - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n")
        bm = {"S8C5...616E": "32:0", "S8C5...619T": "32:1",
              "S74Z...288W": "32:4", "S74Z...280E": "32:5"}
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)), \
             patch.object(raid, "bay_map", return_value=bm), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=eall), \
             patch("b2ctl.hba.enumerate_disks", return_value=[sda, nvme]):
            disks = raid.enumerate_disks()
        hw = [d for d in disks if d.array_type == "HW"]
        ugood = [d for d in disks if d.pd_state == "UGood"]
        self.assertEqual(len(hw), 2)
        self.assertEqual(len(ugood), 2)
        for d in ugood:
            self.assertEqual(d.array_type, "")          # available, not a member
            self.assertIn(d.smart_dtype, ("megaraid,4", "megaraid,5"))
            self.assertEqual(d.dev, "/dev/sda")          # megaraid target
        # no ghosts in RAID mode
        self.assertEqual(raid.get_ghost_disks(disks), [])

    def test_raid_volumes_member_count(self):
        vols, members = raid._parse_vall(_VALL)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)):
            out = raid.raid_volumes()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["members"], 2)


class TestPerccliCaching(unittest.TestCase):
    """F-040/F-041: perccli probes are not re-run redundantly per scan."""

    def test_have_tool_memoized(self):
        raid._reset_caches()
        with patch.object(raid, "run", return_value="Controller Count = 1") as mock_run, \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            assert raid.have_tool() is True
            assert raid.have_tool() is True
        # one probe per candidate on the first call, none on the second
        assert mock_run.call_count <= len(raid._TOOL_CANDIDATES)
        raid._reset_caches()

    def test_enumerate_fetches_eall_sall_once_per_controller(self):
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730P Mini"
        vols, members = raid._parse_vall(_VALL)
        calls = []

        def _run(cmd, **kw):
            calls.append(cmd)
            return ""

        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", side_effect=_run), \
             patch("b2ctl.hba.enumerate_disks", return_value=[sda]):
            raid.enumerate_disks()
        eall_calls = [c for c in calls if "/c0/eall/sall" in c]
        self.assertEqual(len(eall_calls), 1)   # fetched once, not 2-3x

    def test_attach_bays_with_bm_does_not_probe(self):
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda", serial="SN1")
        with patch.object(raid, "run") as mock_run, \
             patch("b2ctl.baymap.load", return_value=[]):
            raid.attach_bays([d], bm={"SN1": "32:0"})
        mock_run.assert_not_called()
        self.assertEqual(d.bay, "32:0")


class TestActions(unittest.TestCase):

    def test_pd_selector(self):
        self.assertEqual(raid._pd("32:0"), "/c0/e32/s0")
        self.assertEqual(raid._pd("8:5", controller=1), "/c1/e8/s5")

    # Real PERC H730P `show rebuild` — a table with a BARE integer under
    # 'Progress%', no trailing '%' (F-042).
    _REBUILD_TABLE = """Controller = 0
Status = Success

--------------------------------------------------------
Drive-ID    Progress% Status      Estimated Time Left
--------------------------------------------------------
/c0/e32/s4         28 In progress 0 Minutes
--------------------------------------------------------
"""

    def test_rebuild_progress_perccli_table_format(self):
        with patch.object(raid, "run", return_value=self._REBUILD_TABLE), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            st = raid.rebuild_progress("32:4")
        raid._tool_cache = None
        self.assertAlmostEqual(st["pct"], 28.0)
        self.assertFalse(st["done"])
        self.assertTrue(st["in_progress"])

    def test_rebuild_progress_percent_fallback(self):
        # other firmware may still print an explicit NN% — keep parsing it
        with patch.object(raid, "run", return_value="Rebuild Progress on Drive = 42.5%"), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            st = raid.rebuild_progress("32:0")
        raid._tool_cache = None
        self.assertAlmostEqual(st["pct"], 42.5)
        self.assertFalse(st["done"])

    def test_rebuild_progress_done(self):
        with patch.object(raid, "run", return_value="Status = Not in progress"), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            st = raid.rebuild_progress("32:0")
        raid._tool_cache = None
        self.assertTrue(st["done"])

    def test_locate_command_is_verb_first(self):
        seen = []
        with patch.object(raid, "run_check",
                          side_effect=lambda c, **k: (seen.append(c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            raid.locate("32:0", True)
            raid.locate("32:0", False)
        raid._tool_cache = None
        self.assertEqual(seen[0], ["perccli", "/c0/e32/s0", "start", "locate"])
        self.assertEqual(seen[1], ["perccli", "/c0/e32/s0", "stop", "locate"])

    def test_set_offline_builds_cmd(self):
        seen = {}
        with patch.object(raid, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            raid.set_offline("32:1")
        raid._tool_cache = None
        self.assertEqual(seen["cmd"], ["perccli", "/c0/e32/s1", "set", "offline"])

    def test_raid_token(self):
        self.assertEqual(raid._raid_token("raid1"), "r1")
        self.assertEqual(raid._raid_token("r1"), "r1")
        self.assertEqual(raid._raid_token("1"), "r1")
        self.assertEqual(raid._raid_token("RAID10"), "r10")

    def _capture(self, fn):
        seen = []
        with patch.object(raid, "run_check",
                          side_effect=lambda c, **k: (seen.append(c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            fn()
        raid._tool_cache = None
        return seen[0]

    def test_add_vd_uses_r_level(self):
        cmd = self._capture(lambda: raid.add_vd("raid1", ["32:4", "32:5"]))
        self.assertEqual(cmd, ["perccli", "/c0", "add", "vd", "r1", "drives=32:4,32:5"])

    def test_add_hotspare_with_dg(self):
        cmd = self._capture(lambda: raid.add_hotspare("32:4", dg=0))
        self.assertEqual(cmd, ["perccli", "/c0/e32/s4", "add", "hotsparedrive", "DGs=0"])

    def test_set_jbod(self):
        cmd = self._capture(lambda: raid.set_jbod("32:4"))
        self.assertEqual(cmd, ["perccli", "/c0/e32/s4", "set", "jbod"])


class TestParseBayMap(unittest.TestCase):
    """F-082: the perccli 'Drive /cN/eE/sS Device attributes' -> 'SN =' pairing
    that attributes every RAID member's serial to its enclosure:slot. Realistic
    multi-drive `/cX/eall/sall show all` detailed section."""

    # Two drives, each a 'Device attributes' header followed by an SN line and
    # interleaved attribute noise (WWN/Model), mirroring the R640 output style.
    _EALL = """\
Drive /c0/e32/s0 Device attributes :
====================================
SN = S8C5NX0R123456
Manufacturer Id = ATA
Model Number = Samsung SSD 870 EVO 1TB
WWN = 5002538E40A1B2C3

Drive /c0/e32/s1 Device attributes :
====================================
SN = S8C5NX0R654321
Manufacturer Id = ATA
Model Number = Samsung SSD 870 EVO 1TB
WWN = 5002538E40A1B2D4
"""

    def test_two_drives_serial_to_encslot(self):
        mapping = {}
        raid._parse_bay_map(self._EALL, mapping)
        self.assertEqual(mapping, {"S8C5NX0R123456": "32:0",
                                   "S8C5NX0R654321": "32:1"})

    def test_sn_line_only_binds_to_preceding_drive_header(self):
        # An 'SN =' line with no preceding Drive header is ignored; the SN that
        # follows a header binds to that header's enc:slot.
        text = ("SN = ORPHAN_NO_HEADER\n"
                "Drive /c0/e32/s0 Device attributes :\n"
                "State = Onln\n"
                "SN = S0REAL\n"
                "Model Number = Samsung SSD 870 EVO 1TB\n")
        mapping = {}
        raid._parse_bay_map(text, mapping)
        self.assertEqual(mapping, {"S0REAL": "32:0"})
        self.assertNotIn("ORPHAN_NO_HEADER", mapping)

    def test_missing_sn_skipped(self):
        # A drive whose 'Device attributes' section carries no SN before the
        # next Drive header is not mapped; only the drive that does have an SN
        # ends up in the mapping.
        text = ("Drive /c0/e32/s4 Device attributes :\n"
                "====================================\n"
                "WWN = 5002538E40A1B2C3\n"
                "Model Number = Samsung SSD 870 EVO 1TB\n"
                "Drive /c0/e32/s5 Device attributes :\n"
                "====================================\n"
                "SN = S5ONLY\n"
                "WWN = 5002538E40A1B2D4\n")
        mapping = {}
        raid._parse_bay_map(text, mapping)
        self.assertEqual(mapping, {"S5ONLY": "32:5"})
        self.assertNotIn("32:4", mapping.values())


class TestNormHelpers(unittest.TestCase):
    """F-133: cross-tool join keys (WWN, model) must normalise before compare."""

    def test_norm_wwn_strips_prefix_and_case(self):
        self.assertEqual(raid._norm_wwn("0x5000C500A1B2C3D4"),
                         raid._norm_wwn("5000c500a1b2c3d4"))

    def test_norm_wwn_empty(self):
        self.assertEqual(raid._norm_wwn(""), "")
        self.assertEqual(raid._norm_wwn(None), "")

    def test_model_match_tolerates_perccli_truncation(self):
        # perccli truncates the model column; lsblk reports it in full.
        self.assertTrue(raid._model_match("Samsung SSD 860", "Samsung SSD 860 PRO 1TB"))
        self.assertTrue(raid._model_match("DL2400MM0159", "DL2400MM0159"))

    def test_model_match_rejects_different_models(self):
        self.assertFalse(raid._model_match("DL2400MM0159", "Samsung SSD 860 PRO 1TB"))
        self.assertFalse(raid._model_match("", "Samsung SSD 860 PRO 1TB"))


class TestParseBayMapTolerantHeader(unittest.TestCase):
    """F-133: only SOME perccli builds label the section 'Device attributes'.
    Requiring that literal made every SN unreadable on an HBA330."""

    _EALL_HBA = """\
Drive /c0/e9/s0 - Detailed Information :
========================================

Drive /c0/e9/s0 State :
=======================
Shield Counter = 0

Drive /c0/e9/s0 Device attributes :
===================================
SN = WBM066HP
WWN = 0x5000C500A1B2C3D4
Model Number = DL2400MM0159

Drive /c0/e9/s1 - Detailed Information :
========================================
SN = WBM06F90
WWN = 5000C500A1B2C3D5
"""

    def test_sn_binds_to_any_drive_header(self):
        mapping = {}
        raid._parse_bay_map(self._EALL_HBA, mapping)
        self.assertEqual(mapping, {"WBM066HP": "9:0", "WBM06F90": "9:1"})

    def test_wwn_map_parsed_from_same_text(self):
        mapping = {}
        raid._parse_wwn_map(self._EALL_HBA, mapping)
        self.assertEqual(mapping, {"5000c500a1b2c3d4": "9:0",
                                   "5000c500a1b2c3d5": "9:1"})


class TestEnumerateNoPhantomDuplicates(unittest.TestCase):
    """F-133: on an HBA330 every PD is ALREADY an lsblk disk. Synthesising a
    row per PD produced a duplicate /dev/sda row with no serial and dead
    megaraid SMART for every drive."""

    # No virtual disks; 3 identical SAS HDDs, all exposed to the OS.
    _EALL = (
        "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
        "9:0       0 JBOD   - 2.181 TB SAS  HDD N   N  512B DL2400MM0159     U\n"
        "9:1       1 JBOD   - 2.181 TB SAS  HDD N   N  512B DL2400MM0159     U\n"
        "9:2       2 JBOD   - 2.181 TB SAS  HDD N   N  512B DL2400MM0159     U\n")

    def _raw(self):
        from b2ctl.common import Disk
        out = []
        for n in "abc":
            d = Disk(dev=f"/dev/sd{n}")
            d.model = "DL2400MM0159"       # identical models, no lsblk SERIAL (SAS)
            out.append(d)
        return out

    def _enumerate(self, raw, bay_map_ret, eall):
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "bay_map", return_value=bay_map_ret), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=eall), \
             patch("b2ctl.hba.enumerate_disks", return_value=raw):
            return raid.enumerate_disks()

    def test_no_synthetic_rows_when_serials_unknown(self):
        raw = self._raw()
        disks = self._enumerate(raw, {}, self._EALL)
        self.assertEqual(len(disks), 3)
        self.assertEqual(sorted(d.dev for d in disks),
                         ["/dev/sda", "/dev/sdb", "/dev/sdc"])
        # nothing synthesised => no megaraid passthrough targets
        self.assertEqual([d for d in disks if d.smart_dtype], [])

    def test_wwn_join_tags_the_real_disk(self):
        raw = self._raw()
        raw[0].wwn = "0x5000C500A1B2C3D4"
        eall = self._EALL + (
            "Drive /c0/e9/s0 Device attributes :\n"
            "WWN = 5000c500a1b2c3d4\n")
        disks = self._enumerate(raw, {}, eall)
        self.assertEqual(len(disks), 3)
        self.assertEqual(raw[0].bay, "9:0")
        self.assertEqual(raw[0].ctrl_slot, "9:0")
        self.assertEqual(raw[0].pd_state, "JBOD")

    def test_serial_join_still_tags_the_real_disk(self):
        raw = self._raw()
        raw[1].serial = "WBM06F90"
        eall = self._EALL + ("Drive /c0/e9/s1 Device attributes :\n"
                             "SN = WBM06F90\n")
        disks = self._enumerate(raw, {}, eall)
        self.assertEqual(len(disks), 3)
        self.assertEqual(raw[1].bay, "9:1")

    def test_genuinely_hidden_drive_is_still_synthesised(self):
        # A PD whose model matches NO OS disk really is hidden behind the
        # controller — keep surfacing it (H730P UGood spare).
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730P Mini"
        eall = (
            "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
            "32:4      4 UGood  - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "Drive /c0/e32/s4 Device attributes :\n"
            "SN = S74Z288W\n")
        disks = self._enumerate([sda], {}, eall)
        hidden = [d for d in disks if d.pd_state == "UGood"]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].smart_dtype, "megaraid,4")
        self.assertEqual(hidden[0].serial, "S74Z288W")


class TestHba330FieldRegression(unittest.TestCase):
    """The exact HBA330 Mini report: 9 real drives (7 SAS HDD + 2 SATA SSD),
    every one already an lsblk device, perccli emitting no detail section. The
    old code returned 18 rows — 9 phantom `/dev/sda` entries, serial N/A,
    NOREAD, CRITICAL (F-133)."""

    _EALL = (
        "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
        + "".join(f"9:{i}       {i} JBOD   - 2.181 TB SAS  HDD N   N  512B "
                  f"DL2400MM0159     U\n" for i in range(7))
        + "9:22     22 JBOD   -  931.0 GB SATA SSD N   N  512B "
          "Samsung SSD 860  U\n"
          "9:23     23 JBOD   -  931.0 GB SATA SSD N   N  512B "
          "Samsung SSD 860  U\n")

    def test_nine_drives_stay_nine_rows(self):
        from b2ctl.common import Disk
        raw = []
        for i, name in enumerate("abcdefg"):
            d = Disk(dev=f"/dev/sd{name}")
            d.model = "DL2400MM0159"        # SAS: no lsblk SERIAL, no WWN yet
            raw.append(d)
        for name in "hi":
            d = Disk(dev=f"/dev/sd{name}")
            d.model = "Samsung SSD 860 PRO 1TB"
            raw.append(d)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=self._EALL), \
             patch("b2ctl.hba.enumerate_disks", return_value=raw):
            disks = raid.enumerate_disks()
        self.assertEqual(len(disks), 9)
        self.assertEqual(len({d.dev for d in disks}), 9)   # no shared /dev/sda
        self.assertEqual([d for d in disks if d.smart_dtype], [])


class TestHbaPersonality(unittest.TestCase):
    """F-133: perccli manages an HBA330/H330, but the OS — not the controller —
    owns the disks there. RAID-mode enumeration must not claim such a card."""

    def setUp(self):
        raid._reset_caches()

    def tearDown(self):
        raid._reset_caches()

    def test_false_without_tool(self):
        with patch.object(raid, "have_tool", return_value=False):
            self.assertFalse(raid.is_hba_personality())

    def test_true_when_no_megaraid_sas_driver(self):
        # `smartctl -d megaraid` needs a megaraid_sas host; an HBA330 binds
        # mpt3sas, so RAID-mode SMART is impossible by construction. It reports
        # no personality line at all — that '' is the realistic input here, and
        # an explicit 'RAID-Mode' would (correctly) short-circuit to False.
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_personality", return_value=""), \
             patch.object(raid, "_driver_name", return_value=""), \
             patch.object(raid, "_megaraid_driver_present", return_value=False):
            self.assertTrue(raid.is_hba_personality())

    def test_false_when_virtual_disks_exist(self):
        vols, members = raid._parse_vall(_VALL)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_megaraid_driver_present", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)):
            self.assertFalse(raid.is_hba_personality())

    def test_true_when_controller_reports_hba_personality(self):
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_megaraid_driver_present", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_personality", return_value="HBA-Mode"):
            self.assertTrue(raid.is_hba_personality())

    def test_explicit_raid_personality_wins_over_the_heuristic(self):
        # F-133 review: a freshly-wiped H730P (no VD yet) with an unrelated BOSS
        # mirror + 2 NVMe inflating the lsblk count was classified HBA, which
        # locked the operator out of every raid-* verb in exactly the state that
        # needs them. The controller naming itself RAID-Mode is authoritative.
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_personality", return_value="RAID-MODE"), \
             patch.object(raid, "_driver_name", return_value="megaraid_sas"), \
             patch.object(raid, "_megaraid_driver_present", return_value=True):
            self.assertFalse(raid.is_hba_personality())

    def test_unresolvable_pd_means_the_controller_hides_it(self):
        # megaraid_sas card, no VD, no personality string: HBA only if EVERY PD
        # resolves to an OS block device. Counting unrelated NVMe/BOSS devices
        # instead let 3 strangers outvote 2 genuinely hidden drives.
        eall = (
            "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
            "32:4      4 UGood  - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "32:5      5 UGood  - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "Drive /c0/e32/s4 Device attributes :\nSN = HIDDEN04\n"
            "Drive /c0/e32/s5 Device attributes :\nSN = HIDDEN05\n")
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_personality", return_value=""), \
             patch.object(raid, "_driver_name", return_value="megaraid_sas"), \
             patch.object(raid, "_megaraid_driver_present", return_value=True), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=eall), \
             patch("b2ctl.blockdev.lsblk_pairs", return_value=[
                 {"NAME": "sda", "TYPE": "disk", "SERIAL": "BOSS0001", "WWN": ""},
                 {"NAME": "nvme0n1", "TYPE": "disk", "SERIAL": "NV1", "WWN": ""},
                 {"NAME": "nvme1n1", "TYPE": "disk", "SERIAL": "NV2", "WWN": ""}]):
            self.assertFalse(raid.is_hba_personality())

    def test_every_pd_resolving_to_an_os_disk_means_hba(self):
        eall = (
            "EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
            "32:4      4 JBOD   - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
            "Drive /c0/e32/s4 Device attributes :\nSN = EXPOSED4\n")
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_personality", return_value=""), \
             patch.object(raid, "_driver_name", return_value="megaraid_sas"), \
             patch.object(raid, "_megaraid_driver_present", return_value=True), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=eall), \
             patch("b2ctl.blockdev.lsblk_pairs", return_value=[
                 {"NAME": "sda", "TYPE": "disk", "SERIAL": "EXPOSED4", "WWN": ""},
                 {"NAME": "nvme0n1", "TYPE": "disk", "SERIAL": "NV1", "WWN": ""}]):
            self.assertTrue(raid.is_hba_personality())

    # (The old count-based cases — "PDs outnumber OS disks" / "every PD is an OS
    # disk" — encoded the len(lsblk) >= len(pds) heuristic that the F-133 review
    # showed an unrelated BOSS mirror + NVMe could outvote. They are superseded by
    # test_unresolvable_pd_means_the_controller_hides_it and
    # test_every_pd_resolving_to_an_os_disk_means_hba above, which measure the
    # thing the branch actually asks: does every PD resolve to a block device?)

    def test_result_is_memoized(self):
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_personality", return_value="RAID-Mode"), \
             patch.object(raid, "_megaraid_driver_present", return_value=False), \
             patch.object(raid, "_vall_data", return_value=([], [])) as probe:
            raid.is_hba_personality()
            raid.is_hba_personality()
        self.assertEqual(probe.call_count, 1)


class TestHiddenDriveSurvivesMixedLayout(unittest.TestCase):
    """F-133 review: the anti-duplication refusal must never delete a PD the
    controller genuinely hides. b2ctl's own `assign_perc` [2] set-JBOD workflow
    produces exactly this mix — one drive JBOD-exposed, an identical-model
    sibling still hidden — and the first cut dropped the hidden one whenever it
    happened to be iterated first."""

    def _eall(self, hidden_bay: str, exposed_bay: str) -> str:
        rows = sorted([hidden_bay, exposed_bay], key=lambda b: int(b.split(":")[1]))
        table = ("EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
                 "32:0      0 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
                 "32:1      1 Onln   0 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n")
        for b in rows:
            did = b.split(":")[1]
            state = "UGood" if b == hidden_bay else "JBOD "
            table += (f"{b}      {did} {state}  - 931.0 GB SATA SSD Y   N  512B "
                      f"Samsung SSD 870 EVO 1TB U\n")
        # perccli reports a serial for BOTH: each is identifiable.
        table += (f"Drive /c0/e{hidden_bay.replace(':', '/s')} Device attributes :\n"
                  f"SN = HIDDEN01\n"
                  f"Drive /c0/e{exposed_bay.replace(':', '/s')} Device attributes :\n"
                  f"SN = EXPOSED1\n")
        return table

    def _run(self, hidden_bay: str, exposed_bay: str):
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730P Mini"
        sdb = Disk(dev="/dev/sdb")
        sdb.model, sdb.serial = "Samsung SSD 870 EVO 1TB", "EXPOSED1"
        vols, members = raid._parse_vall(_VALL)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=self._eall(hidden_bay, exposed_bay)), \
             patch("b2ctl.hba.enumerate_disks", return_value=[sda, sdb]):
            disks = raid.enumerate_disks()
        return disks, sdb

    def test_hidden_drive_survives_when_iterated_first(self):
        disks, sdb = self._run("32:2", "32:3")
        hidden = [d for d in disks if d.serial == "HIDDEN01"]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].smart_dtype, "megaraid,2")
        self.assertEqual(hidden[0].pd_state, "UGood")
        self.assertEqual(sdb.bay, "32:3")            # exposed sibling still tagged

    def test_hidden_drive_survives_when_iterated_last(self):
        # Only the two slot numbers swap; the result must not change.
        disks, sdb = self._run("32:3", "32:2")
        hidden = [d for d in disks if d.serial == "HIDDEN01"]
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].smart_dtype, "megaraid,3")
        self.assertEqual(sdb.bay, "32:2")

    def test_failed_hidden_drive_is_never_suppressed(self):
        from b2ctl.common import Disk
        sda = Disk(dev="/dev/sda"); sda.model = "PERC H730P Mini"
        sdb = Disk(dev="/dev/sdb")
        sdb.model, sdb.serial = "Samsung SSD 870 EVO 1TB", "EXPOSED1"
        eall = ("EID:Slt DID State DG     Size Intf Med SED PI SeSz Model            Sp\n"
                "32:2      2 Failed - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
                "32:3      3 JBOD   - 931.0 GB SATA SSD Y   N  512B Samsung SSD 870 EVO 1TB U\n"
                "Drive /c0/e32/s2 Device attributes :\nSN = DEADDISK\n"
                "Drive /c0/e32/s3 Device attributes :\nSN = EXPOSED1\n")
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_ctrl_indices", return_value=[0]), \
             patch.object(raid, "_tool", return_value="perccli"), \
             patch.object(raid, "run", return_value=eall), \
             patch("b2ctl.hba.enumerate_disks", return_value=[sda, sdb]):
            disks = raid.enumerate_disks()
        self.assertEqual([d.pd_state for d in disks if d.serial == "DEADDISK"],
                         ["Failed"])


class TestSuppressionGuards(unittest.TestCase):
    """F-133 review: the model refusal must not be a blunt instrument."""

    def test_model_match_rejects_severely_truncated_prefix(self):
        self.assertFalse(raid._model_match("S", "Samsung SSD 870 EVO 1TB"))
        self.assertFalse(raid._model_match("Sam", "Samsung SSD 870 EVO 1TB"))

    def test_model_match_still_accepts_real_perccli_truncation(self):
        self.assertTrue(raid._model_match("Samsung SSD 860", "Samsung SSD 860 PRO 1TB"))

    def test_pd_size_is_parsed_as_binary_units(self):
        # perccli prints binary sizes under decimal labels.
        self.assertTrue(raid._size_match("2.182 TB", 2_400_476_274_688))    # DL2400MM0159
        self.assertTrue(raid._size_match("953.869 GB", 1_024_209_543_168))  # 860 PRO 1TB

    def test_size_mismatch_blocks_suppression(self):
        self.assertFalse(raid._size_match("953.869 GB", 2_400_476_274_688))

    def test_unknown_size_never_widens_the_guard(self):
        self.assertTrue(raid._size_match("", 2_400_476_274_688))
        self.assertTrue(raid._size_match("2.182 TB", None))


class TestHba330RealControllerShow(unittest.TestCase):
    """Real `perccli /c0 show` from the field HBA330 Mini (F-133). Note what it
    does NOT contain: no Personality line and no Virtual Drives line — the card
    has no personality switch, it is IT firmware permanently. What it DOES
    contain is `Driver Name = mpt3sas`, which settles it."""

    _SHOW = """Controller = 0
Status = Success
Description = None

Product Name = Dell HBA330 Mini
Serial Number = 5d094660873aa100
FW Version = 16.00.11.00
Driver Name = mpt3sas
Driver Version = 54.100.00.00
Vendor Id = 0x1000
Board Name = Dell HBA330 Mini
Physical Drives = 9

PD LIST :
=======

-------------------------------------------------------------------------
EID:Slt DID State DG       Size Intf Med SED PI SeSz Model            Sp
-------------------------------------------------------------------------
9:0       0 UGood -    2.182 TB SAS  HDD N   N  512B DL2400MM0159     U
9:22      7 UGood -  953.869 GB SATA SSD N   N  512B Samsung SSD 860  U
-------------------------------------------------------------------------
"""

    _SHOW_PERC = """Controller = 0
Product Name = PERC H730P Mini
Driver Name = megaraid_sas
Current Personality = RAID-Mode
Virtual Drives = 1
Physical Drives = 4
"""

    def setUp(self):
        raid._reset_caches()

    def tearDown(self):
        raid._reset_caches()

    def test_driver_name_parsed(self):
        with patch.object(raid, "run", return_value=self._SHOW), \
             patch.object(raid, "_tool", return_value="perccli"):
            self.assertEqual(raid._driver_name(), "mpt3sas")

    def test_personality_absent_is_empty_not_an_error(self):
        with patch.object(raid, "run", return_value=self._SHOW), \
             patch.object(raid, "_tool", return_value="perccli"):
            self.assertEqual(raid._personality(), "")

    def test_hba330_classified_as_hba_without_sysfs(self):
        # sysfs deliberately reports "no megaraid_sas host" the WRONG way round
        # here: the driver name from perccli must win on its own.
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=([], [])), \
             patch.object(raid, "_megaraid_driver_present", return_value=True), \
             patch.object(raid, "run", return_value=self._SHOW), \
             patch.object(raid, "_tool", return_value="perccli"):
            self.assertTrue(raid.is_hba_personality())

    def test_perc_raid_mode_still_classified_as_raid(self):
        vols, members = raid._parse_vall(_VALL)
        with patch.object(raid, "have_tool", return_value=True), \
             patch.object(raid, "_vall_data", return_value=(vols, members)), \
             patch.object(raid, "run", return_value=self._SHOW_PERC), \
             patch.object(raid, "_tool", return_value="perccli"):
            self.assertFalse(raid.is_hba_personality())

    def test_pd_list_in_controller_show_parses(self):
        pds = raid._parse_pd_rows(self._SHOW)
        self.assertEqual([p["bay"] for p in pds], ["9:0", "9:22"])
        self.assertEqual(pds[0]["state"], "UGood")      # HBA330 says UGood, not JBOD
        self.assertEqual(pds[0]["model"], "DL2400MM0159")
        self.assertEqual(pds[1]["model"], "Samsung SSD 860")


class TestActionController(unittest.TestCase):
    """F-085: a member enumerated on /c1 must have its perccli action target
    /c1, not the hardcoded /c0."""

    def test_actions_target_member_controller(self):
        from b2ctl.common import Disk
        d = Disk(dev="/dev/sda", ctrl=1, ctrl_slot="32:2")
        seen = {}
        with patch.object(raid, "run_check",
                          side_effect=lambda c, **k: (seen.setdefault("cmd", c),
                                                      (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            # ra._ctrl(d) resolves the member's controller (1), threaded into _pd.
            raid.set_offline(d.ctrl_slot, ra._ctrl(d))
        raid._tool_cache = None
        self.assertEqual(seen["cmd"], ["perccli", "/c1/e32/s2", "set", "offline"])


if __name__ == "__main__":
    unittest.main()
