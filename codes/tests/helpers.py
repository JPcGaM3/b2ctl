"""Shared test helpers: a Disk factory and sample command outputs.

Imported by the per-module test files via `from helpers import ...` (tests/ is
put on sys.path by conftest.py).
"""
from __future__ import annotations

from b2ctl.common import Disk


def _disk(**kw) -> Disk:
    """Shorthand to build a Disk with sensible defaults."""
    defaults = dict(dev="/dev/sda", by_id="/dev/disk/by-id/wwn-0x123",
                    model="Samsung SSD 870", serial="S74ZNS0W000001",
                    iface="SAS", readable=True, health="PASSED",
                    pool="tank", vdev="raidz1-0", vdev_state="ONLINE",
                    poh=18000, wear_val=99, realloc=0, end_left=98.0,
                    written_tb=10.0, tbw_rating=600.0)
    defaults.update(kw)
    return Disk(**defaults)


# --------------------------------------------------------------------------- #
# Sample `zpool status` outputs (for zfs topology / resilver parsing)
# --------------------------------------------------------------------------- #

_MIRROR_STATUS = """\
  pool: rpool
 state: ONLINE
config:

\tNAME                                      STATE     READ WRITE CKSUM
\trpool                                     ONLINE       0     0     0
\t  mirror-0                                ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xAAA-part3       ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xBBB-part3       ONLINE       0     0     0

errors: No known data errors
"""

_RAIDZ_STATUS = """\
  pool: tank
 state: ONLINE
config:

\tNAME                                    STATE     READ WRITE CKSUM
\ttank                                    ONLINE       0     0     0
\t  raidz1-0                              ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xCCC           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xDDD           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xEEE           ONLINE       0     0     0
\tspares
\t    /dev/disk/by-id/wwn-0xFFF           AVAIL

errors: No known data errors
"""

_DEGRADED_STATUS = """\
  pool: tank
 state: DEGRADED
config:

\tNAME                                    STATE     READ WRITE CKSUM
\ttank                                    DEGRADED     0     0     0
\t  raidz1-0                              DEGRADED     0     0     0
\t    /dev/disk/by-id/wwn-0xCCC           ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xDDD           FAULTED      0     0     0
\t    /dev/disk/by-id/wwn-0xEEE           ONLINE       0     0     0

errors: No known data errors
"""

_RESILVER_DONE = """\
  pool: tank
 state: ONLINE
  scan: resilvered 500M in 00:00:05 with 0 errors on Mon Jun 16 10:00:00 2025
"""

_RESILVER_PROGRESS = """\
  pool: tank
 state: ONLINE
  scan: resilver in progress since Mon Jun 16 10:00:00 2025
    123M resilvered, 45.2% done, 00:03:21 to go
"""


# --------------------------------------------------------------------------- #
# Sample `smartctl -a` outputs (for smart parsing)
# --------------------------------------------------------------------------- #

_ATA_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Device Model:     Samsung SSD 870 EVO 1TB
Serial Number:    S74ZNS0W582303N
Firmware Version: SVT02B6Q
User Capacity:    1,000,204,886,016 bytes

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 1
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       18238
177 Wear_Leveling_Count     0x0013   099   099   005    Pre-fail  Always       -       1
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0
241 Total_LBAs_Written      0x0032   099   099   000    Old_age   Always       -       19305985024
"""

_SAS_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Vendor:               SAMSUNG
Product:              MZ7LH1T9HMLT
Serial number:        S4F2NY0M105699
Device type:          disk

Percentage used endurance indicator: 1%
Accumulated power on time, hours:minutes 50451:14

write:  Total                          Secs   GBytes    MBytes  IOs  MBytes/s
  Total: 114131199 231456.8  118551.6   1024.0        5                  0.51

Elements in grown defect list: 0

SMART Health Status: OK
"""
