# b2ctl — Step-by-step Walkthrough Guide

> **Version:** `0.5.0-itmode` · **Build:** IT-mode / LSI SAS2308 (PERC H710 crossflashed) · Proxmox VE 9.2

เอกสารนี้เป็น **walkthrough** สำหรับคนใช้งานจริง (user เปิดแล้วทำตามได้ทีละขั้น).
ส่วน pass/fail test report อยู่ที่ [`test-checklist.md`](test-checklist.md).

## สารบัญ

| #   | Section                 | คำสั่งหลัก                                                       |
| --- | ----------------------- | ---------------------------------------------------------------- |
| 0   | ตรวจสภาพระบบก่อนใช้     | `b2ctl check`                                                    |
| 1   | ดู/validate config      | `b2ctl update` · `b2ctl config show`                             |
| 2   | อ่านตาราง disk health   | `b2ctl status` · `--json`                                        |
| 3   | dry-run (ลองก่อนทำจริง) | `b2ctl --dry-run swap/replace/demote/create/offload` · watch `t` |
| 4   | hot-plug lifecycle      | `b2ctl watch` (pull → spare → insert → assign)                   |
| 5   | locate LED (หาตัวดิสก์) | watch `l` · `b2ctl locate <bay> <sec>`                           |
| 6   | audit log + rollback    | `b2ctl log` · `b2ctl rollback <op_id>`                           |
| 7   | watch menu (ทุก hotkey) | `b2ctl watch`                                                    |
|     |                         |                                                                  |

---

## Section 0 — ตรวจสภาพระบบก่อนใช้ (`b2ctl check`)

ใช้ครั้งแรกหลังติดตั้ง หรือเวลาสงสัยว่า tool ครบไหม / backend ถูกไหม / bay mapping ทำงานไหม

**Step 1:** รัน environment check

```bash
b2ctl check
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[b2ctl environment check]
  [✔] Running as root
  [✔] smartctl     /usr/sbin/smartctl       (smartctl 7.5 2025-04-30 ...)
  [✔] sas2ircu     /usr/sbin/sas2ircu       (LSI Corporation SAS2 IR Configuration Utility.)
  [✗] storcli64    not found (needed for RAID mode)
  [✔] storcli      /usr/local/bin/storcli   (CLI Version = 007.1705.0000.0000 ...)
  [✔] perccli64    /usr/local/sbin/perccli64 (Status Code = 0)
  [✔] zpool        /usr/sbin/zpool          (zfs-2.4.2-pve1)
  [✔] wipefs       /usr/sbin/wipefs         (wipefs from util-linux 2.41)
  [✔] sgdisk       /usr/sbin/sgdisk         (GPT fdisk (sgdisk) version 1.0.10)
  [✔] udevadm      /usr/bin/udevadm         (257)
  [✔] dd           /usr/bin/dd              (dd (coreutils) 9.7)

  [✔] Detected backend: IT-mode
  [✔] Controllers found: 6 (6 disks in bay map)
  [!] Config: /etc/b2ctl/config.json (missing — using defaults, run 'b2ctl config init' to create)
</pre>
</details>

- **แปลว่า:** tool ครบ, backend = **IT-mode** (HBA — ถูกสำหรับเครื่อง crossflash).
- **user ดู:**
  - `sas2ircu [✔]` สำคัญสุด — ถ้าขึ้น `[✗]` หรือ "found but won't execute" → bay column จะเป็น `-` ทั้งหมด; แก้ด้วย `apt-get install -y libc6-i386` (sas2ircu เป็น binary 32-bit)
  - `storcli64 not found` ตรงนี้ **ปกติ** สำหรับ IT-mode (ใช้ใน RAID-mode เท่านั้น)
  - `Controllers found: 6 (6 disks in bay map)` — เลข disk > 0 = bay mapping ทำงาน
  - `Config ... missing — using defaults` ไม่ใช่ error — b2ctl ใช้ค่า default ได้เลย (จะสร้าง config ก็ต่อเมื่ออยาก override path/mode)

---

## Section 1 — validate config (`b2ctl update`, `b2ctl config show`)

ใช้เช็คว่า tool path + bay_map ที่ b2ctl จะใช้ ถูกต้องครบไหม และดูค่า config ปัจจุบัน

**Step 1:** validate tool + bay_map

```bash
b2ctl update
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[b2ctl update]
  [i] config       /etc/b2ctl/config.json missing — using defaults
  [✔] sas2ircu     /usr/sbin/sas2ircu
  [✔] storcli      /usr/local/bin/storcli
  [✔] perccli      /usr/local/sbin/perccli
  [✔] smartctl     /usr/sbin/smartctl
  [✔] zpool        /usr/sbin/zpool
  [i] bay_map      bundled (/opt/b2ctl/bay_map.json)  →  b2ctl update --export-bay-map to customize
</pre>
</details>

- **แปลว่า:** ทุก tool ที่จำเป็นรันได้ (`[✔]`).
- **user ดู:**
  - `[i]` = info เฉยๆ ไม่ใช่ error. `[✔]` = binary นั้น execute ได้จริง (ไม่ใช่แค่มีไฟล์)
  - ถ้า sas2ircu ขึ้น `[i] found but won't execute → apt-get install libc6-i386` ให้ทำตาม
  - `bay_map bundled` = ใช้ไฟล์ bay map ที่มากับ package; ถ้าอยากแก้เอง รัน `b2ctl update --export-bay-map` (copy ไป `/etc/b2ctl/bay_map.json` แก้ได้อิสระ install.sh ไม่ทับ)

**Step 2:** ดูค่า config ปัจจุบัน (อ่านอย่างเดียว)

```bash
b2ctl config show
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
{
  "tool_paths": {
    "sas2ircu": "", "storcli": "", "perccli": "",
    "smartctl": "", "zpool": "", "...": ""
  },
  "controller": { "mode": "auto", "index": "all" },
  "bay_map_path": ""
}
</pre>
</details>

- **แปลว่า:** ค่าว่าง `""` = ใช้ default (b2ctl หา binary จาก PATH เอง). `controller.mode: "auto"` = auto-detect IT vs RAID.
- **user ดู:** ถ้าจะ force ให้เป็น IT-mode (กัน auto-detect พลาด) → `b2ctl config init` แล้วแก้ `"mode": "it"` ในไฟล์ `/etc/b2ctl/config.json`

---
## Section 2 — อ่านตาราง disk health (`b2ctl status`, `b2ctl status --json`)

ใช้ดูสถานะดิสก์ทุกตัวพร้อมกัน — health, อายุการใช้งาน, pool ที่ assign อยู่ และสรุป pool health ด้านล่าง

**Step 1:** ดูตาราง disk health

```bash
b2ctl status
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
BAY   DEV  IF   MODEL                   SERIAL            POWER_ON       WEAR  END    WRITTEN           BAD  HEALTH  POOL           STATUS   LEVEL
1:0   sdf  SAS  Samsung SSD 860 PRO 1TB S5G8NE0MA10474H  51055h(~5.8y)  1%    99.2%  10.06TB/1200TBW   0    PASSED  rpool/mirror-0 ONLINE   NORMAL
1:1   sda  SAS  Samsung SSD 860 PRO 1TB S5G8NE0MA10478T  51056h(~5.8y)  1%    99.1%  10.27TB/1200TBW   0    PASSED  rpool/mirror-0 ONLINE   NORMAL
1:4   sdb  SAS  Samsung SSD 870 EVO 1TB S74ZNS0W537278Y  22926h(~2.6y)  1%    98.4%  9.71TB/600TBW     0    PASSED  tank/raidz1-0  ONLINE   NORMAL
1:5   sdc  SAS  Samsung SSD 870 EVO 1TB S74ZNS0W533737E  18277h(~2.1y)  1%    98.4%  9.88TB/600TBW     0    PASSED  tank/raidz1-0  ONLINE   NORMAL
1:6   sdd  SAS  Samsung SSD 870 EVO 1TB S74ZNS0W582278Y  18281h(~2.1y)  1%    98.3%  9.91TB/600TBW     0    PASSED  tank/raidz1-0  ONLINE   NORMAL
1:7   sde  SAS  Samsung SSD 870 EVO 1TB S74ZNS0W582280E  18281h(~2.1y)  1%    99.8%  1.01TB/600TBW     0    PASSED  tank/spares    AVAIL    NORMAL
Pools:
  rpool  952G   5.96G  free=946G   ONLINE  cap=0%
  tank   2.72T  1.71G  free=2.72T  ONLINE  cap=0%
[OK] all disks healthy and assigned
</pre>
</details>

- **แปลว่า:** ทุกดิสก์ ONLINE, HEALTH = PASSED, pool ทั้งสองเป็น ONLINE.
- **user ดู:**
  - **BAY** = `enclosure:slot` เช่น `1:4` คือช่อง slot 4 ของ controller 1 — ใช้จับคู่ disk กับตำแหน่งจริงในแร็ค
  - **WEAR** = % อายุการใช้งานที่ใช้ไปแล้ว (1% = ยังใหม่มาก)
  - **END** = % endurance ที่เหลืออยู่ (99.8% = ยังเหลืออีกนาน)
  - **WRITTEN** = `xx.xxTB/xxxxTBW` — เขียนจริงไปแล้ว (TB) / ความทนทานตลอดอายุ (TBW รวม); เช่น `10.06TB/1200TBW` = ใช้ไป 10TB จากขีดจำกัด 1200TB
  - **POOL / STATUS / LEVEL** — pool ที่ disk ถูก assign, สถานะ ZFS vdev, บทบาทใน pool (NORMAL, SPARE ฯลฯ)
  - บรรทัด `[OK] all disks healthy and assigned` แสดงเมื่อทุก pool ONLINE; ถ้า pool ใด degraded จะเปลี่ยนเป็น `[!] disks readable but a pool is not ONLINE` พร้อม block `===== pools needing attention =====`

**Step 2:** ดูข้อมูล JSON (สำหรับ scripting หรือ audit)

```bash
b2ctl status --json | python3 -m json.tool
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[
  {
    "dev": "/dev/sdf",
    "bay": "1:0",
    "model": "Samsung SSD 860 PRO 1TB",
    "serial": "S5G8NE0MA10474H",
    ...
    "pool": "rpool/mirror-0",
    "vdev_state": "ONLINE"
  },
  ...
]
</pre>
</details>

- **แปลว่า:** `--json` dump array ของ disk objects ทุกตัว — fields ครบ (dev, bay, model, serial, pool, vdev_state ฯลฯ).
- **user ดู:** pipe เข้า `python3 -m json.tool` เพื่อ validate และ pretty-print; ถ้า output ผ่านโดยไม่ error = JSON ถูกต้อง; ใช้ `jq` หรือ script อื่นต่อยอดได้เลย

---

## Section 3 — dry-run (ลองก่อนทำจริง) (`b2ctl --dry-run swap/replace/demote/create/offload` · watch `t`)

ใช้ preview ว่า b2ctl จะรัน `zpool` command อะไร **โดยไม่แตะ disk จริง** — ไม่ resilver, ไม่จุด LED, ไม่เขียน snapshot

**Step 1:** dry-run swap — ดูว่า swap จะรัน command อะไร

```bash
b2ctl --dry-run swap
```

เลือก disk `3` (disk ใน tank) แล้วตอบ `y`

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[1] (1:0) Samsung SSD 860 PRO 1TB (S5G8NE0MA10474H) in rpool
[2] (1:1) Samsung SSD 860 PRO 1TB (S5G8NE0MA10478T) in rpool
[3] (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0W537278Y) in tank
[4] (1:5) Samsung SSD 870 EVO 1TB (S74ZNS0W533737E) in tank
[5] (1:6) Samsung SSD 870 EVO 1TB (S74ZNS0W582278Y) in tank
[6] (1:7) Samsung SSD 870 EVO 1TB (S74ZNS0W582280E) in tank
swap which #&gt; swap (1:4) ... onto spare (1:7) ...? [y/N]&gt;
[DRY-RUN] would run: zpool replace tank /dev/disk/by-id/wwn-0x.../...part1 /dev/disk/by-id/wwn-0x.../...part1
  ✔ swap started — resilvering onto spare
[DRY-RUN] would run: zpool detach tank /dev/disk/by-id/wwn-0x...
  ✔ detached old disk /dev/sdb
[DRY-RUN] would run: zpool add -f tank spare /dev/disk/by-id/wwn-0x...
  ✔ (1:4) Samsung SSD 870 EVO 1TB is now a hot spare in 'tank'
</pre>
</details>

- **แปลว่า:** dry-run print คำสั่ง `zpool replace`, `zpool detach`, `zpool add spare` พร้อม prefix `[DRY-RUN] would run:` — ไม่มีการเปลี่ยนแปลงจริงเกิดขึ้น.
- **user ดู:** copy คำสั่งใน `[DRY-RUN] would run:` ไปตรวจ path ว่าถูกต้องก่อน run จริง

**Step 2:** dry-run replace — ดู confirm box + preview

```bash
b2ctl --dry-run replace
```

เลือก disk degraded แล้วตอบ `y`

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[DRY-RUN] would run: zpool replace tank /dev/disk/by-id/wwn-0x5002538f3351d0f6 /dev/disk/by-id/wwn-0x5002538f3354e3cb-part1
• replace dry-run preview — nothing changed
</pre>
</details>

- **แปลว่า:** replace dry-run แสดง zpool command ที่จะรัน แล้วจบด้วย `• replace dry-run preview — nothing changed` (neutral) — ไม่มี resilver, ไม่จุด LED.
- **user ดู:** บรรทัดจบต้องเป็น `nothing changed` เสมอ ถ้าเห็น `✗ replace complete` (แดง) แสดงว่า version ยังไม่อัพเดต

**Step 3:** dry-run ops อื่น — demote, create, offload มีรูปแบบเดียวกัน

- `b2ctl --dry-run demote` → print `[DRY-RUN] would run: zpool detach rpool ...` + `zpool add -f rpool spare ...`
- `b2ctl --dry-run create` → ถ้าไม่มี disk ว่างจะบอก `no available disks to create pool`; ถ้าเลือก raid type ผิด → `invalid raid type` (ไม่ run zpool create)
- `b2ctl --dry-run offload` → print `[DRY-RUN] would run: zpool remove tank ...`

**Step 4:** toggle dry-run ใน watch mode

```bash
b2ctl watch
```

กด `t`

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [e]xtend [b]urnin [u]dev-rescue [x]destroy-pool [l]ocate [q]uit
b2ctl&gt; [DRY-RUN MODE: ON]
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [e]xtend [b]urnin [u]dev-rescue [x]destroy-pool [l]ocate [q]uit
b2ctl&gt;
</pre>
</details>

- **แปลว่า:** กด `t` → `[DRY-RUN MODE: ON]` ปรากฏ — ทุก action ใน watch หลังจากนี้จะ print-only ไม่รันจริง กด `t` อีกครั้งเพื่อ toggle กลับ.
- **user ดู:** ใช้ `t` เพื่อ "ฝึกซ้อม" ก่อน production โดยไม่ต้องออกจาก watch

---

## Section 4 — hot-plug lifecycle (`b2ctl watch`)

ใช้ monitor disk ที่ pull/insert แบบ real-time — watch จะแจ้งเหตุการณ์ทันที ไม่ต้อง refresh เอง

**Step 1:** เปิด watch mode ค้างไว้ก่อนดึงดิสก์

```bash
b2ctl watch
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
==================================================================================================================================================================================
BAY   DEV       IF   MODEL                   SERIAL            POWER_ON      WEAR(used) END(left)  WRITTEN            BAD   HEALTH   POOL             STATUS    LEVEL
----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
1:0   sdf       SAS  Samsung SSD 860 PRO 1TB S5G8NE0MA10474H   51057h(~5.8y) 1%         99.2%      10.06TB/1200TBW    0     PASSED   rpool/mirror-0   ONLINE    NORMAL
1:1   sda       SAS  Samsung SSD 860 PRO 1TB S5G8NE0MA10478T   51058h(~5.8y) 1%         99.1%      10.27TB/1200TBW    0     PASSED   rpool/mirror-0   ONLINE    NORMAL
1:4   sdb       SAS  Samsung SSD 870 EVO 1TB S74ZNS0W537278Y   22928h(~2.6y) 1%         98.4%      9.71TB/600TBW      0     PASSED   tank/raidz1-0    ONLINE    NORMAL
1:5   sdc       SAS  Samsung SSD 870 EVO 1TB S74ZNS0W533737E   18279h(~2.1y) 1%         98.4%      9.88TB/600TBW      0     PASSED   tank/raidz1-0    ONLINE    NORMAL
1:6   sdd       SAS  Samsung SSD 870 EVO 1TB S74ZNS0W582278Y   18283h(~2.1y) 1%         98.3%      9.91TB/600TBW      0     PASSED   tank/raidz1-0    ONLINE    NORMAL
1:7   sde       SAS  Samsung SSD 870 EVO 1TB S74ZNS0W582280E   18283h(~2.1y) 1%         99.8%      1.01TB/600TBW      0     PASSED   tank/spares      AVAIL     NORMAL
==================================================================================================================================================================================
Pools:
  rpool     952G    5.96G   free=946G    ONLINE    cap=0%
  tank      2.72T   1.71G   free=2.72T   ONLINE    cap=0%
[OK] all disks healthy and assigned
[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [t]oggle-dryrun  [n]ew-pool  [e]xtend  [b]urnin  [u]dev-rescue  [x]destroy-pool  [l]ocate  [q]uit   (or hot-plug)
b2ctl&gt;

</pre>
</details>

- **แปลว่า:** watch polling อยู่ รอ event.
- **user ดู:** ปล่อย terminal นี้ค้างไว้ระหว่างดึง/เสียบดิสก์

**Step 2:** ดึงดิสก์ tank ออก 1 ตัว (เช่น sdc/bay 1:5)

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
■ disk removed: /dev/sdc
  current pool health:
Pools:
  tank      2.72T   1.71G   free=2.72T   DEGRADED  cap=0%  &lt;-- not ONLINE
</pre>
</details>

- **แปลว่า:** watch ตรวจพบ disk หายทันทีและ print `■ disk removed` พร้อมสถานะ pool.
- **user ดู:** `DEGRADED` = raidz1 เสีย 1 ตัว ยังอ่าน/เขียนได้ปกติ — ไม่ต้องตกใจ แต่อย่าดึงตัวที่ 2. ถ้า pool health ยังแสดง `ONLINE` อยู่ให้กด `r` เพื่อ refresh (timing race ระหว่าง b2ctl กับ ZFS update)

**Step 3:** ZFS auto-resilver onto hot spare (อัตโนมัติ ไม่ต้องสั่ง)

```bash
zpool status tank
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
        spare-1                       DEGRADED
          wwn-0x...d0f6               REMOVED     ← disk ที่ดึงออก
          wwn-0x...e3cd               ONLINE      ← spare เข้าแทน
    spares
      wwn-0x...e3cd                   INUSE  currently in use
  scan: resilvered 599M in 00:00:02 with 0 errors
</pre>
</details>

- **แปลว่า:** ZFS เริ่ม resilver ลง spare อัตโนมัติ — spare status เปลี่ยนเป็น `INUSE`, resilvered 599M ใน 2 วินาที.
- **user ดู:** ไม่ต้องสั่ง `zpool replace` เอง — hot spare ทำงานเอง ถ้าไม่มี spare pool จะ degraded ค้างไว้รอ replace จาก admin

**Step 4:** เสียบดิสก์กลับ bay เดิม

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
╔══ NEW DISK DETECTED: /dev/sdd ═══════════════════════
  model  : Samsung SSD 870 EVO 1TB   SN S74ZNS0W582278Y
  bay    : 1:6   size 931.5G   SAS   SSD
  health : PASSED   wear 1% used   endurance 98.3% left
╚════════════════════════════════════════════════════

  Disk /dev/disk/by-id/wwn-0x5002538f3354e3cb is free.
  What do you want to do with it?
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    [3] REPLACE a degraded/faulted disk in a pool
    [4] ATTACH to an existing disk (convert to/expand mirror)
    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)
    [6] WIPE it blank (for a new pool)
    [s] skip / decide later
  action&gt;
</pre>
</details>

- **แปลว่า:** watch ตรวจพบ disk ใหม่ แสดง panel model/SN/bay/health + เมนูให้เลือกทำอะไรกับ disk.
- **user ดู:**
  - `[1]` Blink LED — กระพริบไฟเพื่อยืนยันตำแหน่งก่อนตัดสินใจ
  - `[2]` Add as spare — เพิ่มเป็น hot spare ใน pool
  - `[3]` Replace — แทน disk ที่ degraded/faulted (เริ่ม resilver)
  - `[4]` Attach — ขยาย mirror (เพิ่ม leg ใหม่)
  - `[5]` Add single — สร้าง single-disk pool ใหม่
  - `[6]` Wipe — ล้าง partition/label ออกก่อน (ใช้กับ disk มือสองที่มี data เก่า)
  - `[s]` Skip — ไม่ทำอะไร ออกจาก panel

---

## Section 5 — locate LED (หาตัวดิสก์จริงในแร็ค)

ใช้จุดไฟกระพริบที่ disk ตัวที่ต้องการ เพื่อหาตำแหน่งจริงในแร็คก่อนดึงออก

**Step 1:** locate ผ่าน watch mode (กด `l`)

```bash
b2ctl watch
```

กด `l` แล้วพิมพ์ bay เช่น `1:4`

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
  locate which (bay/serial/sdX)&gt; 1:4
  blinking /dev/sdb for 5s ...
  ✔ done (via dd)
</pre>
</details>

- **แปลว่า:** จุดไฟกระพริบที่ disk ใน bay 1:4 เป็นเวลา 5 วินาที แล้วดับเอง.
- **user ดู:** watch `l` fix 5 วินาที เหมาะสำหรับ spot-check ว่า bay ไหนคือตัวไหน; `via dd` = ใช้ dd ส่ง SCSI locate command ผ่าน SAS HBA

**Step 2:** locate standalone พร้อม custom duration

```bash
b2ctl locate 1:4 30
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
blinking /dev/sdb for 30s ... ✔ done (via dd)
</pre>
</details>

- **แปลว่า:** กระพริบ 30 วินาที — ให้เวลาเดินไปหาในแร็คได้สบาย.
- **user ดู:** `b2ctl locate <bay> <seconds>` รับ custom duration; ใช้เวลาที่นานพอ (10-30 วิ) ถ้าแร็คอยู่ห้องอื่น

> ⚠️ **คำเตือน:** อย่าจุด LED บนดิสก์ที่กำลัง resilver อยู่ — LED signal อาจรบกวน และ disk กำลังรับ load อยู่; รอให้ resilver เสร็จก่อน (ดู `scan: resilvered ... with 0 errors`) แล้วค่อย locate

---

## Section 6 — audit log + rollback (`b2ctl log`, `b2ctl rollback`)

ใช้ดู history ของทุก op ที่เคยรัน และ preview คำสั่ง reverse ก่อน execute rollback

**Step 1:** ดู operation log

```bash
b2ctl log --last 5
```

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
OP_ID                          OP       BAY  SERIAL           POOL  STATUS   STARTED
────────────────────────────────────────────────────────────────────────────────────────────────────
20260622-160135-379472-replace replace  1:4  S74ZNS0W537278Y  tank  fail     2026-06-22T16:01:35
20260622-160139-713790-replace replace  1:4  S74ZNS0W537278Y  tank  dry_run  2026-06-22T16:01:39
20260622-161350-348683-replace replace  1:4  S74ZNS0W537278Y  tank  dry_run  2026-06-22T16:13:50
</pre>
</details>

- **แปลว่า:** ทุก mutating op ถูกบันทึกพร้อม OP_ID, op type, bay, serial, pool, status, timestamp.
- **user ดู:**
  - **OP_ID** = `YYYYMMDD-HHMMSS-<micro>-<op>` — unique ID ใช้กับ `b2ctl rollback`
  - **STATUS** มี 3 ค่า: `ok` (รันสำเร็จ), `fail` (error ระหว่าง op), `dry_run` (dry-run เท่านั้น ไม่ทำจริง) — dry_run จะแสดงสีต่างกัน
  - `b2ctl log` ไม่มี flag = แสดงทั้งหมด; `--last N` = แสดงแค่ N รายการล่าสุด

**Step 2:** preview rollback ก่อน execute

```bash
b2ctl rollback 20260622-160135-379472-replace
```

ตอบ `N` ที่ prompt

**เห็น output:**

<details>
<summary>📋 ดูตัวอย่าง Output จริง</summary>

<pre>
Op:       replace  (2026-06-22T16:01:35)
Disk:     bay 1:4 | S74ZNS0W537278Y
Pool:     tank/raidz1-0
Rollback: zpool replace tank &lt;new-disk&gt; /dev/disk/by-id/wwn-0x5002538f3351ebe2-part1

Execute rollback? [y/N]: Cancelled.
</pre>
</details>

- **แปลว่า:** rollback แสดง Op/Disk/Pool และ **Rollback hint** = คำสั่ง zpool reverse ที่จะรัน แล้วรอ confirm; ตอบ `N` = ยกเลิก ไม่มีอะไรเปลี่ยน.
- **user ดู:**
  - อ่าน `Rollback:` บรรทัดก่อนตอบ `y` เสมอ — บาง op เช่น wipefs ระบุ "not reversible" เพราะ label ถูกลบแล้ว
  - rollback ไม่ได้ auto-undo ทุกอย่าง — มันแค่รัน zpool command reverse; ต้องเข้าใจผลก่อน confirm

---

## Section 7 — watch menu: ทุก hotkey กดแล้วเจออะไร (`b2ctl watch`)

เปิด watch ครั้งเดียวแล้วสั่งงานทุกอย่างจาก hotkey ได้ — นี่คือ reference ว่ากดแต่ละตัวเจออะไร

**Step 1:** เปิด watch — เห็นเมนูล่างตาราง

```
[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [t]oggle-dryrun  [n]ew-pool  [e]xtend  [b]urnin  [u]dev-rescue  [x]destroy-pool  [l]ocate  [q]uit   (or hot-plug)
b2ctl>
```

- **user ดู:** ที่ prompt `b2ctl>` ให้ **พิมพ์ตัวอักษร** (เช่น `s`) **แล้วกด Enter** — watch อ่านทีละบรรทัด (`readline`) ไม่ใช่ single-keypress. ระหว่างรอ คำสั่ง watch จะคอย detect disk pull/insert เองด้วย (ดู Section 4)

| Hotkey | ทำอะไร | mutating? |
| ------ | ------ | :--: |
| `r` | refresh — scan ใหม่ + reprint ตาราง/pool/summary | no |
| `a` | assign — เปิดเมนูจัดการ disk ที่ยัง unassigned (รวม `[GHOST]` + PERC-UG) | yes |
| `o` | offload — ถอด disk/spare ออกจาก pool | yes |
| `s` | swap — ย้าย member ที่ใช้อยู่ไปลง spare (resilver) แล้ว **ตัวเดิมกลายเป็น spare** | yes |
| `d` | demote — ถอด mirror leg ของ rpool มาเป็น spare (guard กัน break redundancy) | yes |
| `t` | toggle-dryrun — เปิด/ปิด preview mode (ดู Section 3) | no |
| `n` | new-pool — สร้าง pool ใหม่จาก disk ว่าง | yes |
| `e` | extend — เพิ่ม/ถอด L2ARC cache หรือ SLOG log | yes |
| `b` | burnin — ตรวจ disk หลายลูก (self-test + badblocks) แบบเบื้องหลัง | no (read-only) |
| `u` | udev-rescue — กู้ disk ที่ OS ปฏิเสธ (GHOST) ด้วย `udevadm` | no |
| `x` | destroy-pool — ลบ pool (ยืนยันสองชั้น + พิมพ์ชื่อ pool) | yes |
| `l` | locate — กระพริบ LED หา disk (ดู Section 5) | no |
| `q` | quit — ออก, print `bye` | no |

### `r` — refresh

พิมพ์ `r` → Enter → reprint ตาราง + Pools + summary ใหม่ (เหมือน `b2ctl status`). ใช้ตอน disk เพิ่งเสียบหรือ pool health ยังไม่อัพเดต

### `s` — swap (ย้าย member ไป spare)

```
b2ctl> s
    [1] (1:0) ... in rpool
    [3] (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0W582283V) in tank
    [6] (1:7) Samsung SSD 870 EVO 1TB (S74ZNS0W582288W) in tank
  swap which #> 3
  swap (1:4) ... onto spare (1:7) ...? [y/N]> y
  ✔ swap started — resilvering onto spare
  ✔ resilver completed 100%
  ✔ detached old disk /dev/sdb
  ✔ (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0W582283V) is now a hot spare in 'tank'
```

- **แปลว่า:** 1:7 (spare) เข้าไปแทน 1:4 ใน raidz1, แล้ว **1:4 กลายเป็น hot spare ตัวใหม่**.
- **user ดู:** ตั้งใจให้ pool ไม่เสีย spare หลัง swap — เมนูจะแสดงเฉพาะ active member (ไม่โชว์ spare เป็นตัวเลือก)

### `o` — offload (ถอด spare/disk ออก)

```
b2ctl> o
    [4] bay 1:5 /dev/sdc in tank (vdev spares)
  offload which #> 4
  This disk is a hot spare. Remove (1:5) ... from 'tank'? [y/N]> y
  ✔ removed from pool

  Disk ... is free.
  What do you want to do with it?
    [1] Blink LED  [2] spare  [3] replace ... [6] wipe  [s] skip
  action> s
  skipped
```

- **แปลว่า:** ถอด disk ออกจาก pool → กลายเป็น free → เด้งเมนู assign ต่อ (เลือก `s` = ปล่อยไว้ → disk ขึ้น `[CONFIG]` unassigned)

### `d` — demote (mirror leg → spare) *[dry-run]*

```
b2ctl> d            # หรือ b2ctl --dry-run demote
    [1] (1:0) SAMSUNG MZ7LH1T9HMLT-00003 (S4F2NY0M105699) in rpool
    [2] (1:1) SAMSUNG MZ7LH1T9HMLT-00003 (S4F2NY0M105559) in rpool
  demote which #> 1
  demote (1:0) ... in 'rpool' to a hot spare? [y/N]> y
  [DRY-RUN] would run: zpool detach rpool /dev/sdf3
  [DRY-RUN] would run: zpool add -f rpool spare /dev/sdf3
  ✔ demoted to spare
```

- **แปลว่า:** demote = ถอด mirror leg ออก (`zpool detach`) แล้วเพิ่มกลับเป็น spare (`zpool add spare`).
- **user ดู:** มี guard — ถ้าถอดแล้วทำให้ vdev เหลือ leg เดียว (เสีย redundancy) จะ **refuse**. ใช้ได้เฉพาะ mirror (rpool); raidz member ทำไม่ได้

### `t` `l` `q`

- `t` toggle-dryrun → `[DRY-RUN MODE: ON]` (ดู Section 3)
- `l` locate → กระพริบ LED (ดู Section 5)
- `q` quit → `bye` แล้วคืน shell

### `a` — assign (จัดการ disk ที่ unassigned)

ใช้กับ disk ที่ขึ้น `[CONFIG]` (อยู่ในเครื่องแต่ยังไม่ได้ assign เข้า pool ไหน)

```
b2ctl> a
    [1] bay 1:7 /dev/sde (Samsung SSD 870 EVO 1TB, SN S74ZNS0W582280E)
  assign which #> 1

  Disk /dev/disk/by-id/wwn-0x5002538f3354e3cd is free.
  What do you want to do with it?
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    [3] REPLACE a degraded/faulted disk in a pool
    [4] ATTACH to an existing disk (convert to/expand mirror)
    [5] ADD single disk to a pool (expand capacity - WARNING: no redundancy)
    [6] WIPE it blank (for a new pool)
    [s] skip / decide later
  action>
```

- **แปลว่า:** `a` ลิสต์เฉพาะ disk ที่ unassigned → เลือกตัว → เด้งเมนูเดียวกับตอน NEW DISK DETECTED (`[1]`–`[6]` + `[s]`).
- **user ดู:** ต่างจากตอนเสียบ disk ใหม่ตรงที่ `a` เรียกเมนูนี้เองได้ทุกเมื่อ ไม่ต้องรอ hot-plug

### `n` — new-pool (สร้าง pool ใหม่จาก disk ว่าง)

```
b2ctl> n
    [1] /dev/sde (bay 1:7)
  pick disks (space-separated #)> 1
  pool name> tutorial
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]> stripe
  WARNING: The following disks already contain data/labels:
    - (1:7) Samsung SSD 870 EVO 1TB (S74ZNS0W582280E)
  these disks already contain data/labels — wipe and continue? [y/N]> y
  create pool 'tutorial' (stripe) with 1 disks? [y/N]> y
  ✔ pool created
```

- **แปลว่า:** `n` ลิสต์เฉพาะ disk ว่าง → เลือกหลายตัวคั่นด้วยเว้นวรรค → ตั้งชื่อ pool → เลือก raid type → ถ้า disk มี data/label เก่าจะ **เตือน + ขอ confirm wipe** ก่อน → confirm สุดท้าย → `zpool create`.
- **user ดู:**
  - raid type ต้องมี disk พอ: `mirror`≥2, `raidz1`≥3, `raidz2`≥4 (ไม่พอ → `error: need at least N disks`); พิมพ์ผิด → `invalid raid type`
  - `stripe` = ไม่มี redundancy (disk เดียวก็ได้ แต่เสีย = ข้อมูลหายหมด)
  - ⚠️ ตัวอย่างนี้สร้าง pool ชื่อ `tutorial` บน disk ว่าง (1:7) — ถ้าทดสอบเสร็จลบด้วย `zpool destroy tutorial`

### `e` extend — เพิ่ม/ถอด cache · log

```
b2ctl> e
  pool #> 2
  [1] add L2ARC cache (read cache; loss = harmless)
  [2] add SLOG log   (sync-write accel; mirror + PLP recommended)
  [3] remove a cache/log device
  action> 1
    [1] /dev/sde (bay 1:7)
  pick disk(s) (space-separated #)> 1
  add 1 L2ARC cache device(s) to 'tank'? [y/N]> y
  ✔ cache added
```

- **แปลว่า:** เลือก pool → `[1]` cache (L2ARC) / `[2]` log (SLOG — เลือก ≥2 ลูก = mirror, ต้องมี PLP) / `[3]` ถอด cache/log ออก → เลือก disk ว่าง → confirm.

### `b` burnin — ตรวจ disk ก่อนใช้ (หลายลูก + เบื้องหลัง)

```
b2ctl> b
    [1] /dev/sdb (bay 32:4) Samsung SSD 870 EVO 1TB
    [2] /dev/sda (bay 32:5) Samsung SSD 870 EVO 1TB
  burn in which #> (space-separated) 1 2
  burn-in 2 disk(s) (long self-test)? [y/N]> y
  also run a full read-surface scan (badblocks, read-only, hours)? [y/N]> y

 BAY     DISK      SELF-TEST                     SURFACE SCAN (badblocks)
 32:4    sdb       [########------]  62%  ~1h10m  [###-----------]  18%  ~4h30m
```

- **แปลว่า:** เลือกได้หลายลูก → long self-test (รันบน firmware) + badblocks (process เบื้องหลัง) → หน้าจอสดโชว์แถบ + เวลาที่เหลือ. Ctrl-C ออกได้ งานยังรันต่อ กลับเข้าดูด้วย `b2ctl burnin --status`.

### `u` udev-rescue — กู้ disk ที่ OS ปฏิเสธ (GHOST)

```
b2ctl> u
    ghost bay 1:4 serial S74ZNS0W537278Y
  run udevadm trigger/settle to rescue 1 ghost disk(s)? [y/N]> y
  ✔ rescued 1 disk(s)
```

- **แปลว่า:** disk เสียบอยู่จริงแต่ OS ไม่รับ (ขึ้นเป็น GHOST ไม่มี `/dev`) → สั่ง `udevadm trigger/settle` ให้ kernel เห็น (อ่านอย่างเดียว). กู้ไม่ได้ → `no disks recovered — reseat physically or wipe via [a]ssign`.

### `x` destroy-pool — ลบ pool (อันตราย)

```
b2ctl> x
    [1] rpool (952G, ONLINE)
    [2] tank (2.72T, ONLINE)
  destroy which #> 2
  members:
    - (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0W537278Y)
    ...
  [!] destroying 'tank' ERASES ALL DATA on it. This cannot be undone.
  destroy pool 'tank'? [y/N]> y
  type the pool name 'tank' to confirm> tank
  ✔ pool 'tank' destroyed; cron removed
```

- **แปลว่า:** ลบ pool ถาวร — 2 ด่าน (`[y/N]` + พิมพ์ชื่อ pool ให้ตรง). b2ctl ลบ cron ของ pool นั้นให้ด้วย.

---

## Section 8 — NVMe: ดูตัวดิสก์ + เปลี่ยนชื่อ bay (`bay_map.json`)

simulator มี NVMe 2 ลูก (Samsung 990 EVO Plus 4TB) อยู่แล้ว ลองดู:

```
python3 sim/simctl init
python3 sim/run status
```

จะเห็น 2 แถวล่างสุด:
```
PCIe2:0 nvme0n1   NVME Samsung SSD 990 EVO Plu S7U9NU0Y401069K   2h(~0.0y)   0%   100.0%  0.00TB/2400TBW   0  PASSED   -   CONFIG
PCIe2:1 nvme1n1   NVME Samsung SSD 990 EVO Plu S7U9NU0Y400872E   10h(~0.0y)  0%   100.0%  0.00TB/2400TBW   0  PASSED   -   CONFIG
```

- **แปลว่า:** NVMe ขึ้นในตารางเหมือน disk อื่นทุกอย่าง (health, wear, endurance) `POOL/ARRAY` เป็น `-` เพราะยังไม่ได้อยู่ pool ไหน → `CONFIG` (unassigned)
- คอลัมน์ **BAY** = `PCIe2:0` / `PCIe2:1` มาจากการ map ใน `bay_map.json` (sim ตั้งไว้ให้แล้ว)

### เปลี่ยนชื่อ bay เอง
NVMe ไม่มี enc:slot เลยตั้งชื่อ bay เองได้ใน `bay_map.json` (panel `back`/`type:nvme`)
match ได้ 3 แบบ — **ลำดับ by-id > serial > bdf**:

```json
{ "panel": "back", "type": "nvme",
  "map": [ {"serial": "S7U9NU0Y401069K", "bay": "PCIe2:0"},
           {"serial": "S7U9NU0Y400872E", "bay": "PCIe2:1"} ] }
```

- **`serial`** ง่ายสุด — copy จากคอลัมน์ **SERIAL** ในตารางได้เลย (ใช้ในตัวอย่างนี้)
- **`by-id`** = substring ของ `/dev/disk/by-id/nvme-<model>_<serial>` (บนเครื่องจริง: `ls /dev/disk/by-id/ | grep nvme`) — ไม่เปลี่ยนแม้ย้าย slot การ์ด
- **`bdf`** = PCIe address (เช่น `d8:00.0`) บนเครื่องจริงดูได้จาก `cat /sys/class/nvme/nvme0/address`

ลองแก้ `bay` ใน `sim/bay_map.json` (เช่นเป็น `NVMe-A` / `NVMe-B`) แล้วรัน `python3 sim/run status` ใหม่ — คอลัมน์ BAY จะเปลี่ยนตาม **ไม่ต้องแตะอย่างอื่น**

> 💡 บนเครื่องจริงไฟล์อยู่ที่ `/etc/b2ctl/bay_map.json` แก้แล้วรัน `b2ctl status` ดูผลได้เลย (เป็นแค่ป้ายชื่อ — ปลอดภัย ไม่กระทบดิสก์)

### burn-in NVMe ก่อนใช้
```
python3 sim/run burnin nvme0n1 --short
```
→ รัน SMART self-test แล้วสรุป **PASS/WARN/FAIL** (อ่านอย่างเดียว ไม่เขียนดิสก์) ใช้คัดดิสก์ใหม่/มือสองก่อนเข้า pool
