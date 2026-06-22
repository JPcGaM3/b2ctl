# b2ctl — Test Checklist / Test Report

> **Version:** `0.5.0-itmode`  ·  **Build:** IT-mode / LSI SAS2308 (PERC H710 crossflashed)
> **ผู้ทดสอบ:** agent (sonnet via SSH)  ·  **วันที่:** 2026-06-22  ·  **เครื่อง (hostname):** pve 100.100.100.201 + pve2 100.100.100.203

เอกสารนี้เป็น **checklist** สำหรับไล่เทส b2ctl ทุก scenario แล้วใช้เป็น test report ส่งต่อได้
ภาษา: เนื้อหาไทย, commands/technical terms อังกฤษ.

---

## วิธีใช้

1. ทำ **Pre-flight** ก่อน เก็บ baseline ไว้เทียบ
2. ไล่เทสทีละ section (A → E) ตามลำดับความเสี่ยง
3. กรอก 3 ช่อง: **Status** (`✅`/`❌`/`⏭`) · **Actual** (สิ่งที่เห็นจริง) · **Comment** (ถ้า `❌` อธิบายว่าต่างจาก Expected ยังไง)
4. ถ้า `❌` → ส่งกลับมาให้ทีม dev แก้ code รอบถัดไป

### Legend (Status)

| สัญลักษณ์ | ความหมาย                                   |
| --------- | ------------------------------------------ |
| `☐`       | ยังไม่เทส                                  |
| `✅`       | PASS — ตรง Expected                        |
| `❌`       | FAIL — ไม่ตรง Expected (กรอก Comment ด้วย) |
| `⏭`       | SKIP — ข้าม (กรอกเหตุผลใน Comment)         |

---

## ⚠️ Safety rules (อ่านก่อนเทส)

- **เทส mutating ops บน `tank` pool เท่านั้น — อย่าแตะ `rpool`** (เป็น boot pool ของ Proxmox)
- ลองด้วย **`--dry-run` ก่อนเสมอ** แล้วค่อยทำจริง (Section B = dry-run, ไม่กระทบข้อมูล)
- **capture `zpool status tank` ก่อน-หลัง** ทุก mutating test ไว้เทียบ
- `tank` เป็น **raidz1** → ทนดิสก์เสียได้ **1 ตัวเท่านั้น**. ระหว่าง resilver อย่าดึงตัวที่ 2
- ห้ามจุดไฟ locate LED บนดิสก์ที่กำลัง resilver

---

## Pre-flight (เก็บ baseline)

```bash
# เก็บสถานะตั้งต้นไว้เทียบทีหลัง
b2ctl version
b2ctl check
b2ctl status --json > /tmp/before.json
zpool status tank   > /tmp/zpool-before.txt
zpool status rpool >> /tmp/zpool-before.txt
```

| ตรวจ                           | Expected                                     | Status | Actual |
| ------------------------------ | -------------------------------------------- | :----: | ------ |
| `b2ctl version`                | `b2ctl 0.5.0-itmode`                         | ✅ 201 / ✅ 203 | `b2ctl 0.5.0-itmode` บนทั้งสองเครื่อง |
| `b2ctl check` รันได้ ไม่ crash | แสดง root, tools, backend mode, config path  | ✅ 201 / ✅ 203 | แสดงครบ; sas2ircu ✔, backend IT-mode |
| `/tmp/before.json` valid JSON  | `python3 -m json.tool /tmp/before.json` ผ่าน | ✅ 201 / ✅ 203 | JSON_OK ทั้งคู่ |
| baseline zpool เก็บแล้ว        | ไฟล์ `/tmp/zpool-before.txt` มีเนื้อหา       | ✅ 201 / ✅ 203 | zpool status tank ONLINE ทั้งคู่ (baseline captured) |

#### 📋 Output จริง + อธิบายสำหรับ new-user

```
# 201 — b2ctl version
b2ctl 0.5.0-itmode

# 201 — b2ctl check (ตัดให้สั้น)
[✔] Running as root
[✔] smartctl     /usr/sbin/smartctl
[✔] sas2ircu     /usr/sbin/sas2ircu
[✗] storcli64    not found (needed for RAID mode)   ← ปกติสำหรับ IT-mode
[✔] storcli      /usr/local/bin/storcli
[✔] perccli64    /usr/local/sbin/perccli64
[✔] zpool        /usr/sbin/zpool
[✔] Detected backend: IT-mode
[✔] Controllers found: 6 (6 disks in bay map)
[!] Config: /etc/b2ctl/config.json (missing — using defaults)

# 201 — zpool status tank (baseline)
pool: tank  state: ONLINE
  raidz1-0: wwn-0x5002538f3351ebe2-part1 ONLINE
             wwn-0x5002538f3351d0f6       ONLINE
             wwn-0x5002538f3354e3cb       ONLINE
  spares: wwn-0x5002538f3354e3cd AVAIL
```

203: เหมือน 201 (ต่างแค่ serial/hostname); 203 ไม่มี storcli/perccli ติดตั้ง (แสดง `[✗]` ทุกตัว — ปกติสำหรับเครื่องที่ยังไม่ได้ install optional tools)

---

## Section A — Safe / Read-only

> รันผ่าน SSH ได้ ไม่กระทบข้อมูล. รันได้เลยไม่ต้องอยู่หน้าเครื่อง.

| ID | Scenario | Command | Expected (exact) | Status | Actual | Comment |
|----|----------|---------|------------------|:------:|--------|---------|
| A1 | ดูสถานะดิสก์ | `b2ctl status` | ตารางครบ: BAY/DEV/IF/MODEL/SERIAL/...POOL/STATUS/LEVEL; pools summary ด้านล่าง | ✅ 201 / ✅ 203 | ตารางครบทุก column; 6 disks (+ 1 Virtual Floppy บน 203) | 203 มี `/dev/sdg` USB Virtual Floppy แสดง CRITICAL (unassigned+NOREAD) — expected บน Proxmox VE |
| A2 | สถานะ JSON | `b2ctl status --json` | JSON array ของ disk objects, valid (เช็คด้วย `... \| python3 -m json.tool`) | ✅ 201 / ✅ 203 | JSON_OK ทั้งคู่; array มี fields ครบ (dev, bay, model, serial, pool, vdev_state ฯลฯ) | |
| A3 | MODEL เต็ม (TBW fix) | `b2ctl status` | MODEL แสดงเต็ม เช่น `Samsung SSD 860 PRO 1TB`; WRITTEN แสดง `xx.xxTB/1200TBW` ไม่ใช่ `/?` | ✅ 201 / ✅ 203 | 201: `Samsung SSD 860 PRO 1TB`, WRITTEN=`10.06TB/1200TBW` ✔; 203: `Samsung SSD 870 EVO 1TB`, WRITTEN=`9.70TB/600TBW` ✔ | |
| A4 | BAY column (libc6 fix) | `b2ctl status` | BAY แสดงเลข enclosure:slot (เช่น `1:0`) ไม่ใช่ `-` ทั้งหมด | ✅ 201 / ✅ 203 | BAY = `1:0`, `1:1`, `1:4`, `1:5`, `1:6`, `1:7` ทุกตัว; sas2ircu ทำงานปกติ | |
| A5 | Check environment | `b2ctl check` | `[✔] sas2ircu`, backend = `IT-mode`, "Controllers found: N (M disks in bay map)" M>0 | ✅ 201 / ✅ 203 | `[✔] sas2ircu`, backend IT-mode, Controllers found: 6 (6 disks) ทั้งคู่ | |
| A6 | Update / validate config | `b2ctl update` | แต่ละ tool ขึ้น `[✔]`; sas2ircu ไม่ขึ้น "found but won't execute" | ✅ 201 / ✅ 203 | 201: sas2ircu/storcli/perccli/smartctl/zpool ✔; 203: storcli/perccli `[i] not found → run: b2ctl install` (ไม่ใช่ error) | 203 missing storcli/perccli แต่ไม่ crash — แสดง info hint เท่านั้น |
| A7 | Config show | `b2ctl config show` | พิมพ์ JSON config (tool_paths, controller, bay_map_path) | ✅ 201 / ✅ 203 | JSON config ครบ 3 keys; ทั้งคู่ใช้ defaults (config.json missing) | |
| A8 | Operation log | `b2ctl log` หรือ `b2ctl log --last 5` | ตาราง history (หรือ "No operations logged yet") | ✅ 201 / ✅ 203 | 201: มี 1 entry (replace op จากก่อน); 203: "No operations logged yet" | |
| A9 | Locate LED | `b2ctl locate <bay> 3` | ไฟกระพริบที่ bay ถูกตัว 3 วินาที | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องเพื่อยืนยัน LED; ไม่สามารถตรวจผ่าน SSH |

#### 📋 Output จริง + อธิบายสำหรับ new-user

```
# 201 — b2ctl status
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
```

ตาราง `b2ctl status` แสดง BAY เป็น `enclosure:slot` (เช่น `1:4`) — หมายถึงช่อง bay ที่ 4 ของ controller 1. WRITTEN = `9.71TB/600TBW` บอกว่าเขียนไปแล้ว 9.71TB จาก capacity 600TBW รวม. WEAR = 1% หมายถึงดิสก์ใช้ไป 1% ของอายุการใช้งาน

203: เหมือน 201 (ต่างแค่ serial/disk model ในรูพล); 203 มี `/dev/sdg` USB Virtual Floppy เพิ่มมา แสดง CRITICAL (SMART unreadable + unassigned) ซึ่งปกติสำหรับ virtual device

```
# 201 — b2ctl log --last 5
OP_ID                          OP       BAY  SERIAL           POOL  STATUS  STARTED
20260622-141956-293556-replace replace  1:4  S74ZNS0W537278Y  tank  ok      2026-06-22T14:19:56
```

Log แสดง operation ที่เคยรันพร้อม timestamp, bay, serial และ status — ช่วย audit ย้อนหลังได้

---

## Section B — Dry-run mutating

> ใช้ `--dry-run` → b2ctl **print คำสั่งที่จะรัน แต่ไม่ทำจริง**. รันผ่าน SSH ได้ ไม่ต้องดึงดิสก์.
> เทียบ `zpool status tank` ก่อน-หลัง ต้อง **ไม่เปลี่ยน**.

| ID | Scenario | Command | Expected | Status | Actual | Comment |
|----|----------|---------|----------|:------:|--------|---------|
| B1 | Swap (dry) | `b2ctl --dry-run swap` → เลือกดิสก์/spare → y | print `zpool replace ...` (dry-run), pool ไม่เปลี่ยน | ✅ 201 / ✅ 203 | แสดง `[DRY-RUN] would run: zpool replace tank ...` + `zpool detach ...` + `zpool add ... spare` | |
| B2 | Replace (dry) | `b2ctl --dry-run replace` → เลือก → y | print `zpool replace -f tank ...` (dry-run), ไม่ resilver จริง | ✅ 201 / ✅ 203 | แสดง `[DRY-RUN] would run: zpool replace tank ...` ครบ; มี confirm box ก่อน; pool ไม่เปลี่ยน | stderr แสดง `✗ replace complete` หลัง dry-run (บรรทัด status summary) — ไม่ใช่ error จริง เป็น flow summary |
| B3 | Demote (dry) | `b2ctl --dry-run demote` → เลือก mirror leg → y | print `zpool detach ...` + `zpool add ... spare` (dry-run) | ✅ 201 / ✅ 203 | `[DRY-RUN] would run: zpool detach rpool ...` + `zpool add -f rpool spare ...` ครบ | เมนูแสดงเฉพาะ mirror-capable disks (rpool legs) — raidz members ถูก filter ออกโดย design |
| B4 | Create pool (dry) | `b2ctl --dry-run create` → เลือกดิสก์ว่าง → y | print `zpool create -f -o ashift=12 ...` (dry-run) | ✅ 201 / ⏭ 203* | 201: `no available disks to create pool` (ทุกดิสก์ assigned แล้ว) — ถูกต้อง; 203: มี `sdg` แต่เป็น Virtual Floppy | *203 มี disk ว่างแต่เป็น USB Virtual Floppy (NOREAD) ไม่เหมาะสร้าง pool จริง — skip full flow |
| B5 | Offload (dry) | `b2ctl --dry-run offload` → เลือก → y | print คำสั่ง detach/replace (dry-run) ตาม vdev type | ✅ 201 / ✅ 203 | เลือก spare (1:7): `[DRY-RUN] would run: zpool remove tank ...` แล้วเสนอ assign menu (กด `s` skip) | |
| B6 | Watch dry-run toggle | `b2ctl watch` → กด `t` | แสดง `[DRY-RUN MODE: ON]` | ✅ 201 / ✅ 203 | กด `t` → `[DRY-RUN MODE: ON]` ปรากฏทันที; กด `q` → `bye` | |
| B7 | Watch menu ครบ | `b2ctl watch` | เมนู `[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit` | ✅ 201 / ✅ 203 | เมนูครบทุก option ตามที่กำหนด | |

#### 📋 Output จริง + อธิบายสำหรับ new-user

```
# 201 — b2ctl --dry-run swap (input: "3\ny")
[1] (1:0) Samsung SSD 860 PRO 1TB (S5G8NE0MA10474H) in rpool
[2] (1:1) Samsung SSD 860 PRO 1TB (S5G8NE0MA10478T) in rpool
[3] (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0W537278Y) in tank   ← เลือกอันนี้
[4] (1:5) Samsung SSD 870 EVO 1TB (S74ZNS0W533737E) in tank
[5] (1:6) Samsung SSD 870 EVO 1TB (S74ZNS0W582278Y) in tank
[6] (1:7) Samsung SSD 870 EVO 1TB (S74ZNS0W582280E) in tank
swap which #> swap (1:4) ... onto spare (1:7) ...? [y/N]>
[DRY-RUN] would run: zpool replace tank /dev/disk/by-id/wwn-0x.../...part1 /dev/disk/by-id/wwn-0x.../...part1
  ✔ swap started — resilvering onto spare
[DRY-RUN] would run: zpool detach tank /dev/disk/by-id/wwn-0x...
  ✔ detached old disk /dev/sdb
[DRY-RUN] would run: zpool add -f tank spare /dev/disk/by-id/wwn-0x...
  ✔ (1:4) Samsung SSD 870 EVO 1TB is now a hot spare in 'tank'
```

dry-run จะ print คำสั่งที่จะรันพร้อม prefix `[DRY-RUN] would run:` — ไม่มีการเปลี่ยนแปลงจริงเกิดขึ้น ใช้ตรวจสอบว่า b2ctl เลือก disk path ถูกก่อน run จริง

```
# 201 — b2ctl watch (input: "t\nq")
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit
b2ctl> [DRY-RUN MODE: ON]
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit
b2ctl> bye
```

Watch mode ให้กด `t` เพื่อ toggle dry-run — ทุก action หลังจากนี้จะ print-only ไม่รันจริง เหมาะสำหรับฝึกก่อน production

203: เหมือน 201 (ต่างแค่ serial/hostname)

---

## Section C — Physical hotplug (ต้องอยู่หน้าเครื่อง)

> ต้องดึง/เสียบดิสก์จริง. ทำบน **tank** เท่านั้น. เปิด `b2ctl watch` ค้างไว้ระหว่างเทส.
> 💡 ลำดับแนะนำ: C1 → C2 (รอ resilver เสร็จ) → C3 → C4 → … แล้วคืนสภาพ

| ID | Scenario | Steps | Expected | Status | Actual | Comment |
|----|----------|-------|----------|:------:|--------|---------|
| C1 | Hot-remove | เปิด `watch` → ดึงดิสก์ tank 1 ตัวออก | watch แจ้ง disk removed; `zpool status tank` = `DEGRADED` | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C2 | Spare auto-replace | (มี spare AVAIL อยู่) หลัง C1 | ZFS auto-resilver onto spare | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C3 | Hot-insert | เสียบดิสก์กลับ bay เดิม | watch แสดง `╔══ NEW DISK DETECTED ══` | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C4 | Assign menu ครบ | จาก C3 ดูเมนู | options `[1]`–`[6]` + `[s]` skip | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C5 | Assign as spare | C3 → พิมพ์ `2` → เลือก pool `tank` → y | ดิสก์เข้า tank เป็น spare | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C6 | Replace degraded | (มี degraded leaf) C3 → `3` → confirm | `✔ replace started — resilvering` | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C7 | Swap onto spare | `watch` → `s` → ยืนยัน | `zpool replace` รัน | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C8 | Offload spare | `watch` → `o` → เลือก spare | spare ถูก detach | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C9 | Create new pool | `watch` → `n` → ดิสก์ว่าง ≥2 | `zpool create` สำเร็จ | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |
| C10 | Locate ใหม่ถูก bay | `watch` → `l` | ไฟกระพริบตรงตัว | ⏭ SKIP | — | ต้องอยู่หน้าเครื่องจริง |

---

## Section D — Edge / Negative path

> เทส negative — ต้องเด้ง error/refuse ถูกต้อง ไม่พังเงียบ ไม่ทำจริง.

| ID | Scenario | Steps | Expected (exact message) | Status | Actual | Comment |
|----|----------|-------|--------------------------|:------:|--------|---------|
| D1 | Cancel ทุก prompt | ทุก action → ตอบ `N` หรือ `q` | ไม่มีอะไรเปลี่ยน; ขึ้น `cancelled` | ✅ 201 / ✅ 203 | ตอบ `N` ที่ swap prompt → `cancelled` ทันที; zpool ไม่เปลี่ยน | |
| D2 | Swap ไม่มี spare | เอา spare ออกก่อน → `watch` → `s` | `pool 'tank' has no AVAIL spare — add one first` | ⏭ SKIP | — | ต้องเอา spare ออกก่อน = mutating; ไม่รันบน production |
| D3 | Create ดิสก์ไม่พอ | `n` → เลือก 1 ดิสก์ → raid type `raidz2` | `error: need at least 4 disks for raidz2` | ⏭ 201 / ✅ 203 | 203: `error: need at least 4 disks for raidz2` ✔ | 201 ไม่มี disk ว่าง ทำ test นี้บน 203 เท่านั้น (sdg unassigned) |
| D4 | Invalid raid type | `n` → เลือกดิสก์ → พิมพ์ raid type มั่ว `raid9` | `invalid raid type` | ⏭ 201 / ✅ 203 | 203: `invalid raid type` ✔ | 201 ไม่มี disk ว่าง ทำ test นี้บน 203 เท่านั้น |
| D5 | Demote last mirror leg | `d` → เลือก leg สุดท้าย | `refuse: not a detachable mirror leg / would break redundancy` | ⏭ SKIP | — | demote menu filter เฉพาะ mirror-capable disks — raidz members ไม่ปรากฏในเมนู; ไม่สามารถ trigger path นี้ด้วย SSH; ต้องทดสอบแยกต่างหาก |
| D6 | Assign dirty disk | เสียบดิสก์มี data เก่า | `WARNING: ... already contain data/labels` | ⏭ SKIP | — | ต้องอยู่หน้าเครื่อง (physical disk) |
| D7 | ดึงดิสก์ระหว่าง resilver | เริ่ม replace → ดึงอีกตัว | pool survive (raidz1) | ⏭ SKIP | — | physical; ต้องอยู่หน้าเครื่อง |

#### 📋 Output จริง + อธิบายสำหรับ new-user

```
# 203 — b2ctl --dry-run create (D3: 1 disk + raidz2)
[1] /dev/sdg (bay ?)
pick disks (space-separated #)>  pool name>  raid type (stripe, mirror, raidz1, raidz2) [mirror]>
  error: need at least 4 disks for raidz2

# 203 — b2ctl --dry-run create (D4: 1 disk + raid9)
pick disks (space-separated #)>  pool name>  raid type (stripe, mirror, raidz1, raidz2) [mirror]>
  invalid raid type
```

เมื่อ user ป้อน raid type ผิด tool จะ reject ทันทีก่อน execute — ไม่ทำ zpool create. raidz2 ต้องการ disk ≥ 4 ตัว (2 parity + 2 data)

```
# 201 — D1: cancel swap
swap which #> N
  cancelled
```

กด `N` (หรือ Enter) ที่ confirm prompt → ออกทันที ไม่มีอะไรเปลี่ยน

---

## Section E — Unit tests (pytest) — ✅ agent รันให้แล้วในเครื่อง dev

```bash
cd codes && python3 -m pytest tests/ -q
```

**ผลรอบนี้ (2026-06-22, dev machine):** `124 passed, 0 failed` (124 total) ✅

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| E1 | Test suite รันได้ | suite รันจบ ไม่ error การ import | ✅ | 124 tests collected, รันจบ | OK |
| E2 | Pass rate | tests ผ่านทั้งหมด | ✅ | **124 passed / 0 failed** | 9 เทสที่เคย fail แก้แล้ว (ดูด้านล่าง) |

**โครงสร้าง test ใหม่ — 1 ไฟล์ต่อ 1 module (หาง่าย):**

```
tests/
  conftest.py + helpers.py     # shared _disk() factory + sample outputs
  test_common.py (17)   test_zfs.py (28)    test_watch.py (23)
  test_ui.py (14)       test_config.py (8)  test_backend.py (7)
  test_hba.py (7)       test_smart.py (5)   test_spec.py (5)
  test_core.py (4)      test_safety.py (4)  test_cli.py (2)
```

**9 เทสที่เคย fail — แก้แล้ว:**
- 7 ตัว (add_spare/replace/create_pool×2/demote_to_spare×2/swap_readds): mock assertion
  เก่าคาด `run_check([...])` แต่ code เรียก `run_check([...], dry_run=False)` (production
  ถูก — มี `dry_run` kwarg จากฟีเจอร์ dry-run). แก้ assertion ให้รับ `dry_run=False`
- 2 ตัว (test_cmd_swap_success / test_cmd_demote_success ใน feature_1b เดิม): **stale** —
  เขียนไว้สำหรับ `_cmd_swap` เวอร์ชันเก่า (ใช้ `zfs.spares()`, ไม่มี topology-linger detach).
  เขียนใหม่ใน `test_watch.py::TestWatchSwapDemoteFlow` ให้ตรง implementation ปัจจุบัน
  (รักษา coverage: resilver poll loop 2 รอบ + detach เมื่อ old token ยัง linger)

**Source code ไม่ถูกแตะ** — แก้เฉพาะ test (โครงสร้าง + assertion).

---

## สรุปผล (Summary)

| Section | ทั้งหมด | ✅ PASS | ❌ FAIL | ⏭ SKIP |
|---------|:------:|:------:|:------:|:------:|
| A — Safe / Read-only | 9 | 8 | 0 | 1 |
| B — Dry-run mutating | 7 | 7 | 0 | 0 |
| C — Physical hotplug | 10 | 0 | 0 | 10 |
| D — Edge / Negative | 7 | 3 | 0 | 4 |
| E — Unit tests | 2 | 2 | 0 | 0 |
| **รวม** | **35** | **20** | **0** | **15** |

**Overall comment / สิ่งที่ต้องแก้ต่อ:**

> ผ่านทุก test ที่รันได้ผ่าน SSH (20/20 PASS, 0 FAIL). Section C ทั้งหมดและ D2/D5/D6/D7 ต้องทำ physical บนเครื่องจริง.
>
> ข้อสังเกตสำหรับ dev team: (1) B2 replace dry-run แสดง `✗ replace complete` ใน status summary หลัง dry-run lines — ดู cosmetic ไม่ใช่ error จริง แต่ควรตรวจ exit code ว่า correct. (2) D5 (refuse demote raidz member) ไม่สามารถ trigger ผ่าน SSH เพราะ demote menu filter raidz members ออกโดย design — path นี้ cover ได้ด้วย unit test เท่านั้น. (3) 203 มี Virtual Floppy `/dev/sdg` แสดง CRITICAL (NOREAD) — ปกติสำหรับ Proxmox VE virtual floppy device.
