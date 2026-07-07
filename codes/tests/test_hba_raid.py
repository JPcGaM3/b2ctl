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
