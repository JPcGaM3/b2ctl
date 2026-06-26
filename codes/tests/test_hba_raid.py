"""Unit tests for b2ctl.hba_raid — RAID-mode (perccli) parsing + enumeration."""
import unittest
from unittest.mock import patch

import b2ctl.hba_raid as raid


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


class TestActions(unittest.TestCase):

    def test_pd_selector(self):
        self.assertEqual(raid._pd("32:0"), "/c0/e32/s0")
        self.assertEqual(raid._pd("8:5", controller=1), "/c1/e8/s5")

    def test_rebuild_progress_in_progress(self):
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
                          side_effect=lambda c: (seen.append(c), (True, ""))[1]), \
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
                          side_effect=lambda c: (seen.setdefault("cmd", c), (True, ""))[1]), \
             patch("b2ctl.config.tool", side_effect=lambda n: n):
            raid._tool_cache = "perccli"
            raid.set_offline("32:1")
        raid._tool_cache = None
        self.assertEqual(seen["cmd"], ["perccli", "/c0/e32/s1", "set", "offline"])


if __name__ == "__main__":
    unittest.main()
