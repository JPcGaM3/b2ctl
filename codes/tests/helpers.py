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

_RESILVER_DONE_WITH_ERRORS = """\
  pool: tank
 state: DEGRADED
  scan: resilvered 500M in 00:00:05 with 1 errors on Mon Jun 16 10:00:00 2025
"""

# Early in a real OpenZFS resilver there is no ETA yet — the scan line contains
# both 'resilvered' and 'no estimated completion time' but NOT 'to go'. The old
# parser misread this as completed-with-errors (F-025).
_RESILVER_NO_ETA = """\
  pool: tank
 state: DEGRADED
  scan: resilver in progress since Mon Jun 16 10:00:00 2025
    517M resilvered, 24.83% done, no estimated completion time
"""

# zpool status when a hot spare auto-activates (spare-N vdev, not replacing-N)
_SPARE_N_STATUS = """\
  pool: tank
 state: DEGRADED
config:

\tNAME                                      STATE     READ WRITE CKSUM
\ttank                                      DEGRADED     0     0     0
\t  raidz1-0                               DEGRADED     0     0     0
\t    /dev/disk/by-id/wwn-0xCCC            ONLINE       0     0     0
\t    spare-1                              DEGRADED     0     0     0
\t      /dev/disk/by-id/wwn-0xDDD         REMOVED      0     0     0
\t      /dev/disk/by-id/wwn-0xFFF         ONLINE       0     0     0
\t    /dev/disk/by-id/wwn-0xEEE            ONLINE       0     0     0
\tspares
\t    /dev/disk/by-id/wwn-0xFFF            INUSE currently in use

errors: No known data errors
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

# SAS drive with a nonzero 'total uncorrected errors' column in its error-counter
# log (column 7 of the read/write/verify rows) but ZERO grown defects (F-095).
# Column 6 (gigabytes processed) carries a decimal — the parser must skip it with
# \S+, not \d+ — and the read: row's column 7 is 14 uncorrected errors.
_SAS_UNCORR_OUTPUT = """\
=== START OF INFORMATION SECTION ===
Vendor:               SEAGATE
Product:              ST1200MM0009
Serial number:        S4F2NY0M888888
Device type:          disk

Percentage used endurance indicator: 1%
Accumulated power on time, hours:minutes 33333:14

Error counter log:
           Errors Corrected by           Total   Correction     Gigabytes    Total
               ECC          rereads/    errors   algorithm      processed    uncorrected
           fast | delayed   rewrites  corrected  invocations   [10^9 bytes]  errors
read:   1234567        0         0   1234567          0       6585.833          14
write:        0        0         0         0          0       2345.123           0
verify: 9876543        0         0   9876543          0        123.456           0

Elements in grown defect list: 0

SMART Health Status: OK
"""

# Realistic full `smartctl -a` dump for an NVMe SSD (Samsung 990 EVO Plus style).
# Exercises the NVMe dispatch (requires both 'NVMe' and 'SMART/Health Information'
# in the output) and every _parse_nvme field: comma-formatted 'Power On Hours',
# 'Percentage Used', 'Data Units Written' (*1000 LBA conversion) and 'Media and
# Data Integrity Errors' (F-096).
_NVME_OUTPUT = """\
smartctl 7.3 2022-02-28 r5338 [x86_64-linux-6.8.12-pve] (local build)
Copyright (C) 2002-22, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Model Number:                       Samsung SSD 990 EVO Plus 2TB
Serial Number:                      S7U9NX0Y123456
Firmware Version:                   0B2QKXJ7
PCI Vendor/Subsystem ID:            0x144d
IEEE OUI Identifier:                0x002538
Total NVM Capacity:                 2,000,398,934,016 [2.00 TB]
Unallocated NVM Capacity:           0
Controller ID:                      6
NVMe Version:                       2.0
Number of Namespaces:               1
Namespace 1 Size/Capacity:          2,000,398,934,016 [2.00 TB]
Namespace 1 Formatted LBA Size:     512
Local Time is:                      Mon Jul  6 12:00:00 2026 UTC
Firmware Updates (0x16):            3 Slots, no Reset required

=== START OF SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART/Health Information (NVMe Log 0x02)
Critical Warning:                   0x00
Temperature:                        35 Celsius
Available Spare:                    100%
Available Spare Threshold:          10%
Percentage Used:                    5%
Data Units Read:                    23,456,789 [12.0 TB]
Data Units Written:                 12,345,678 [6.32 TB]
Host Read Commands:                 234,567,890
Host Write Commands:                123,456,789
Controller Busy Time:               1,234
Power Cycles:                       56
Power On Hours:                     1,234
Unsafe Shutdowns:                   12
Media and Data Integrity Errors:    7
Error Information Log Entries:       7
Warning  Comp. Temperature Time:    0
Critical Comp. Temperature Time:    0
Temperature Sensor 1:               35 Celsius
Temperature Sensor 2:               42 Celsius
"""
