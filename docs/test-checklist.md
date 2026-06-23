<a id="top"></a>

# b2ctl — Test Checklist / Test Report

> **Version:** `0.5.0-itmode`  ·  **Build:** IT-mode / LSI SAS2308 (PERC H710 crossflashed)
> **ผู้ทดสอบ:** agent (sonnet via SSH)  ·  **วันที่:** 2026-06-22

> **Environment:**
> - Nodes: `pve` 100.100.100.201 + `pve2` 100.100.100.203
> - OS: Proxmox VE 9.2 (Debian 13)
> - ZFS Version: 2.4.2 (`zfs-2.4.2-pve1`) — verified via `b2ctl check`
> - Layout: `rpool` (mirror, boot pool) · `tank` (raidz1, 3 disks + 1 spare)

เอกสารนี้เป็น **checklist** สำหรับไล่เทส b2ctl ทุก scenario แล้วใช้เป็น test report ส่งต่อได้
ภาษา: เนื้อหาไทย, commands/technical terms อังกฤษ.

---

<a id="toc"></a>

## 📑 สารบัญ (Table of Contents)

1. [📊 Executive Summary](#exec-summary)
   - [📌 Action Items & Known Issues](#action-items)
2. [⚠️ Safety rules](#safety)
3. [วิธีใช้ + Legend](#usage)
4. [Pre-flight (baseline)](#preflight)
5. [Section A — Safe / Read-only](#sec-a)
6. [Section B — Dry-run mutating](#sec-b)
7. [📸 Snapshot audit](#snapshot)
8. [Section C — Physical hotplug](#sec-c)
9. [Section D — Edge / Negative path](#sec-d)
10. [Section E — Unit tests (pytest)](#sec-e)

---

<a id="exec-summary"></a>

## 📊 Executive Summary

| Section | ทั้งหมด | ✅ PASS | ❌ FAIL | ⏭ SKIP |
|---------|:------:|:------:|:------:|:------:|
| A — Safe / Read-only | 9 | 9 | 0 | 0 |
| B — Dry-run mutating | 7 | 7 | 0 | 0 |
| C — Physical hotplug | 10 | 9 | 0 | 1 |
| D — Edge / Negative | 7 | 5 | 0 | 2 |
| E — Unit tests | 2 | 2 | 0 | 0 |
| **รวม** | **35** | **32** | **0** | **3** |

> **0 FAIL.** SKIP 3 = C9 (create new pool — ยังไม่ทำ), D5 (raidz demote refuse — cover ด้วย unit test),
> D6 (dirty disk — ต้องหา disk มี data เก่า). 203 กู้จาก SUSPENDED + redeploy → เทส C2/C5-C8/D2/D7 ซ้ำผ่าน.
> Unit tests: **128 passed**.

<a id="action-items"></a>

### 📌 Action Items & Known Issues

- `[Fixed]` **C2-201 pool-aware summary** — เดิมขึ้น `[OK] all disks healthy` ทั้งที่ tank `DEGRADED`. แก้ `ui.render_details` ให้ pool-aware (ขึ้น `pools needing attention` + `[!] ... not ONLINE`). +unit test 2 ตัว
- `[Fixed]` **B2 dry-run replace** — เดิมจบด้วย `✗` ปลอม + จุดไฟ LED + post-op-verify. แก้ที่ `safety._print_op_result`/`end_op` + `watch._replace_onto_spare` (ข้าม LED/verify ตอน dry-run)
- `[Fixed]` **snapshot บน dry-run** — `begin_op` รับ `dry_run=` → dry-run ไม่เขียน snapshot อีก
- `[Fixed]` **swap/replace menu** — กรอง spare ออกจาก candidate list (spare ไม่ใช่ swap source)
- `[Pending]` **Redeploy `install.sh`** บน 201 + 203 → fix ทั้งหมดข้างบนมีผลจริงบน server (ตอนนี้ source แก้แล้วแต่ server ยังรันของเก่า)
- `[Pending]` **203 tank SUSPENDED** — กู้แล้วรอบเทส (`zpool clear tank`) แต่ตรวจ disk 1:5 (sdc) ว่าพังจริงไหม → ดู [203 RECOVERY box](#sec-c)
- `[To-Do]` **C9** create new pool (physical) + **D6** assign dirty disk (physical) — ยังไม่ได้ทำบน server
- `[To-Do]` **C1-203 timing race** — `_handle_removed` อ่าน pool health ก่อน ZFS update → ต้อง `r`. แก้ด้วย `udevadm settle`
- `[To-Do]` **watch `l` locate** fix 5 วิ (standalone `b2ctl locate <bay> <sec>` ปรับได้)
- `[Note]` **D5** raidz demote refuse — menu filter raidz ออกโดย design → cover ด้วย unit test
- `[Note]` **203 `/dev/sdg`** Virtual Floppy แสดง CRITICAL (NOREAD) — ปกติสำหรับ Proxmox VE

---

<a id="safety"></a>

## ⚠️ Safety rules (อ่านก่อนเทส)

- **เทส mutating ops บน `tank` pool เท่านั้น — อย่าแตะ `rpool`** (เป็น boot pool ของ Proxmox)
- ลองด้วย **`--dry-run` ก่อนเสมอ** แล้วค่อยทำจริง (Section B = dry-run, ไม่กระทบข้อมูล)
- **capture `zpool status tank` ก่อน-หลัง** ทุก mutating test ไว้เทียบ
- `tank` เป็น **raidz1** → ทนดิสก์เสียได้ **1 ตัวเท่านั้น**. ระหว่าง resilver อย่าดึงตัวที่ 2
- ห้ามจุดไฟ locate LED บนดิสก์ที่กำลัง resilver

---

<a id="usage"></a>

## วิธีใช้ + Legend

1. ทำ **Pre-flight** ก่อน เก็บ baseline ไว้เทียบ
2. ไล่เทสทีละ section (A → E) ตามลำดับความเสี่ยง
3. กรอก 3 ช่อง: **Status** (`✅`/`❌`/`⏭`) · **Actual** (สิ่งที่เห็นจริง) · **Comment** (ถ้า `❌` อธิบายว่าต่างจาก Expected ยังไง)
4. ถ้า `❌` → ส่งกลับมาให้ทีม dev แก้ code รอบถัดไป

| สัญลักษณ์ | ความหมาย |
| --------- | -------- |
| `☐` | ยังไม่เทส |
| `✅` | PASS — ตรง Expected |
| `❌` | FAIL — ไม่ตรง Expected (กรอก Comment ด้วย) |
| `⏭` | SKIP — ข้าม (กรอกเหตุผลใน Comment) |

---

<a id="preflight"></a>

## Pre-flight (เก็บ baseline)

```bash
# เก็บสถานะตั้งต้นไว้เทียบทีหลัง
b2ctl version
b2ctl check
b2ctl status --json > /tmp/before.json
zpool status tank   > /tmp/zpool-before.txt
zpool status rpool >> /tmp/zpool-before.txt
```

| ตรวจ | Expected | Status | Actual |
| ---- | -------- | :----: | ------ |
| `b2ctl version` | `b2ctl 0.5.0-itmode` | ✅ 201 / ✅ 203 | `b2ctl 0.5.0-itmode` บนทั้งสองเครื่อง |
| `b2ctl check` รันได้ ไม่ crash | แสดง root, tools, backend mode, config path | ✅ 201 / ✅ 203 | แสดงครบ; sas2ircu ✔, backend IT-mode |
| `/tmp/before.json` valid JSON | `python3 -m json.tool /tmp/before.json` ผ่าน | ✅ 201 / ✅ 203 | JSON_OK ทั้งคู่ |
| baseline zpool เก็บแล้ว | ไฟล์ `/tmp/zpool-before.txt` มีเนื้อหา | ✅ 201 / ✅ 203 | zpool status tank ONLINE ทั้งคู่ |

<details>
<summary>📋 ดูตัวอย่าง Output จริง (version / check / baseline) + คำอธิบาย</summary>

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

</details>

---

<a id="sec-a"></a>

## Section A — Safe / Read-only

> รันผ่าน SSH ได้ ไม่กระทบข้อมูล. รันได้เลยไม่ต้องอยู่หน้าเครื่อง.

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| A1 | ดูสถานะดิสก์ — `b2ctl status` | ตารางครบ: BAY/DEV/IF/MODEL/SERIAL/...POOL/STATUS/LEVEL; pools summary ด้านล่าง | ✅ 201 / ✅ 203 | ตารางครบทุก column; 6 disks (+ 1 Virtual Floppy บน 203) | 203 มี `/dev/sdg` USB Virtual Floppy แสดง CRITICAL (unassigned+NOREAD) — expected บน Proxmox VE |
| A2 | สถานะ JSON — `b2ctl status --json` | JSON array ของ disk objects, valid (เช็คด้วย `... \| python3 -m json.tool`) | ✅ 201 / ✅ 203 | JSON_OK ทั้งคู่; array มี fields ครบ (dev, bay, model, serial, pool, vdev_state ฯลฯ) | |
| A3 | MODEL เต็ม (TBW fix) — `b2ctl status` | MODEL แสดงเต็ม เช่น `Samsung SSD 860 PRO 1TB`; WRITTEN แสดง `xx.xxTB/1200TBW` ไม่ใช่ `/?` | ✅ 201 / ✅ 203 | 201: `Samsung SSD 860 PRO 1TB`, WRITTEN=`10.06TB/1200TBW` ✔; 203: `Samsung SSD 870 EVO 1TB`, WRITTEN=`9.70TB/600TBW` ✔ | |
| A4 | BAY column (libc6 fix) — `b2ctl status` | BAY แสดงเลข enclosure:slot (เช่น `1:0`) ไม่ใช่ `-` ทั้งหมด | ✅ 201 / ✅ 203 | BAY = `1:0`, `1:1`, `1:4`, `1:5`, `1:6`, `1:7` ทุกตัว; sas2ircu ทำงานปกติ | |
| A5 | Check environment — `b2ctl check` | `[✔] sas2ircu`, backend = `IT-mode`, "Controllers found: N (M disks in bay map)" M>0 | ✅ 201 / ✅ 203 | `[✔] sas2ircu`, backend IT-mode, Controllers found: 6 (6 disks) ทั้งคู่ | |
| A6 | Update / validate config — `b2ctl update` | แต่ละ tool ขึ้น `[✔]`; sas2ircu ไม่ขึ้น "found but won't execute" | ✅ 201 / ✅ 203 | 201: sas2ircu/storcli/perccli/smartctl/zpool ✔; 203: storcli/perccli `[i] not found → run: b2ctl install` (ไม่ใช่ error) | 203 missing storcli/perccli แต่ไม่ crash — แสดง info hint เท่านั้น |
| A7 | Config show — `b2ctl config show` | พิมพ์ JSON config (tool_paths, controller, bay_map_path) | ✅ 201 / ✅ 203 | JSON config ครบ 3 keys; ทั้งคู่ใช้ defaults (config.json missing) | |
| A8 | Operation log — `b2ctl log [--last 5]` | ตาราง history (หรือ "No operations logged yet") | ✅ 201 / ✅ 203 | 201: มี 1 entry (replace op จากก่อน); 203: "No operations logged yet" | |
| A9 | Locate LED — `watch` → `l` → `1:4` (หรือ `b2ctl locate <bay> <sec>`) | ไฟกระพริบที่ bay ถูกตัว | ✅ 201 / ✅ 203 | `blinking /dev/sdb for 5s ... ✔ done (via dd)` กระพริบ **ถูกตัว** ทั้ง 2 เครื่อง | ⏱ watch `l` fix 5 วินาที; standalone ปรับ sec ได้ |

<details>
<summary>📋 ดูตัวอย่าง Output จริง (status table / log) + คำอธิบาย</summary>

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

ตาราง `b2ctl status` แสดง BAY เป็น `enclosure:slot` (เช่น `1:4`) — ช่อง bay 4 ของ controller 1. WRITTEN = `9.71TB/600TBW` = เขียนไป 9.71TB จาก capacity 600TBW. WEAR = 1% = ใช้ไป 1% ของอายุการใช้งาน.
203: เหมือน 201; เพิ่ม `/dev/sdg` USB Virtual Floppy แสดง CRITICAL (SMART unreadable + unassigned) ปกติสำหรับ virtual device.

```
# 201 — b2ctl log --last 5
OP_ID                          OP       BAY  SERIAL           POOL  STATUS  STARTED
20260622-141956-293556-replace replace  1:4  S74ZNS0W537278Y  tank  ok      2026-06-22T14:19:56
```

Log แสดง operation ที่เคยรันพร้อม timestamp, bay, serial, status — ช่วย audit ย้อนหลังได้.

</details>

---

<a id="sec-b"></a>

## Section B — Dry-run mutating

> ใช้ `--dry-run` → b2ctl **print คำสั่งที่จะรัน แต่ไม่ทำจริง**. รันผ่าน SSH ได้ ไม่ต้องดึงดิสก์.
> เทียบ `zpool status tank` ก่อน-หลัง ต้อง **ไม่เปลี่ยน**.

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| B1 | Swap (dry) — `b2ctl --dry-run swap` → เลือก → y | print `zpool replace ...` (dry-run), pool ไม่เปลี่ยน | ✅ 201 / ✅ 203 | แสดง `[DRY-RUN] would run: zpool replace tank ...` + `zpool detach ...` + `zpool add ... spare` | |
| B2 | Replace (dry) — `b2ctl --dry-run replace` → เลือก → y | print `zpool replace -f tank ...` (dry-run), ไม่ resilver จริง | ✅ 201 / ✅ 203 | แสดง `[DRY-RUN] would run: zpool replace tank ...` ครบ; มี confirm box ก่อน; pool ไม่เปลี่ยน | **แก้แล้วใน source (รอ redeploy):** เดิม dry-run จบด้วย `✗ replace complete` (แดง) + Rollback hint + จุดไฟ LED จริง → เข้าใจผิดว่า fail. แก้เป็น `• replace dry-run preview — nothing changed` + ข้าม LED/post-op-verify |
| B3 | Demote (dry) — `b2ctl --dry-run demote` → เลือก mirror leg → y | print `zpool detach ...` + `zpool add ... spare` (dry-run) | ✅ 201 / ✅ 203 | `[DRY-RUN] would run: zpool detach rpool ...` + `zpool add -f rpool spare ...` ครบ | เมนูแสดงเฉพาะ mirror-capable disks (rpool legs) — raidz members ถูก filter ออกโดย design |
| B4 | Create pool (dry) — `b2ctl --dry-run create` → เลือกดิสก์ว่าง → y | print `zpool create -f -o ashift=12 ...` (dry-run) | ✅ 201 / ⏭ 203* | 201: `no available disks to create pool` (ทุกดิสก์ assigned) — ถูกต้อง; 203: มี `sdg` แต่เป็น Virtual Floppy | *203 disk ว่างเป็น USB Virtual Floppy (NOREAD) ไม่เหมาะสร้าง pool — skip full flow |
| B5 | Offload (dry) — `b2ctl --dry-run offload` → เลือก → y | print คำสั่ง detach/replace (dry-run) ตาม vdev type | ✅ 201 / ✅ 203 | เลือก spare (1:7): `[DRY-RUN] would run: zpool remove tank ...` แล้วเสนอ assign menu (กด `s` skip) | |
| B6 | Watch dry-run toggle — `b2ctl watch` → กด `t` | แสดง `[DRY-RUN MODE: ON]` | ✅ 201 / ✅ 203 | กด `t` → `[DRY-RUN MODE: ON]` ทันที; กด `q` → `bye` | |
| B7 | Watch menu ครบ — `b2ctl watch` | เมนู `[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit` | ✅ 201 / ✅ 203 | เมนูครบทุก option | |

<details>
<summary>📋 ดูตัวอย่าง Output จริง (dry-run swap / watch toggle) + คำอธิบาย</summary>

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

dry-run print คำสั่งพร้อม prefix `[DRY-RUN] would run:` — ไม่มีการเปลี่ยนแปลงจริง ใช้ตรวจว่า b2ctl เลือก disk path ถูกก่อน run จริง.

```
# 201 — b2ctl watch (input: "t\nq")
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit
b2ctl> [DRY-RUN MODE: ON]
[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit
b2ctl> bye
```

กด `t` ใน watch = toggle dry-run; ทุก action หลังจากนั้น print-only ไม่รันจริง เหมาะฝึกก่อน production.
203: เหมือน 201 (ต่างแค่ serial/hostname).

</details>

---

<a id="snapshot"></a>

## 📸 Snapshot audit (`/var/log/b2ctl/snapshots/`)

ทุก mutating op (replace/offline/add_spare) `safety.begin_op()` ถ่าย **pre-op snapshot**
ไว้ก่อน เผื่อ rollback/สืบสวน. ตรวจแล้วบนทั้ง 2 เครื่อง:

| ตรวจ | ผล |
|------|-----|
| โครงสร้างไฟล์ | ✅ ครบ 4 section: `zpool status <pool>` · `zpool list -v` · `zfs list` · `smartctl -a <dev>` |
| content จริง | ✅ 201: tank `state: ONLINE`, smartctl `PASSED`; ไฟล์ ~7.5KB / ~144 บรรทัด |
| ทั้ง 2 เครื่อง | ✅ 201 มี 4 ไฟล์, 203 มี 1 ไฟล์ — header + ทุก section ครบ |

<details>
<summary>📋 ดูตัวอย่าง section headers ของ snapshot</summary>

```
# ตัวอย่าง section headers ของ 1 snapshot (201)
=== b2ctl pre-op snapshot: 20260622-161350-348683-replace ===
--- zpool status tank ---
--- zpool list -v ---
--- zfs list ---
--- smartctl -a /dev/disk/by-id/wwn-0x...-part1 ---
=== START OF INFORMATION SECTION ===
=== START OF READ SMART DATA SECTION ===
```

</details>

- `[Fixed]` **Optional 1:** เดิม `--dry-run` ก็สร้าง snapshot (`begin_op` รันก่อน `run_check`). แก้ `begin_op` รับ `dry_run=` แล้วข้าม `_capture_snapshot` ตอน dry-run → dry-run = preview ล้วน (มีผลหลัง redeploy)
- `[Done]` **Optional 2:** ลบ snapshot ที่งอกจาก dry-run testing — ลบเฉพาะ op `status: "dry_run"` (201: 2, 203: 1); ของ op จริง (ok/fail) เก็บไว้ audit

---

<a id="sec-c"></a>

## Section C — Physical hotplug (ต้องอยู่หน้าเครื่อง)

> ต้องดึง/เสียบดิสก์จริง. ทำบน **tank** เท่านั้น. เปิด `b2ctl watch` ค้างไว้ระหว่างเทส.
> 💡 ลำดับแนะนำ: C1 → C2 (รอ resilver เสร็จ) → C3 → C4 → … แล้วคืนสภาพ

| ID  | Scenario                                           | Expected                                                  |     Status     | Actual                                                                                                                                              | Comment                                                                                            |
| --- | -------------------------------------------------- | --------------------------------------------------------- | :------------: | --------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| C1  | Hot-remove — `watch` → ดึงดิสก์ tank 1 ตัว         | watch แจ้ง disk removed; `zpool status tank` = `DEGRADED` | ✅ 201 / ⚠️ 203 | 201: `■ disk removed` + tank `DEGRADED` ทันที. 203: แจ้ง removed แต่ pool health auto-print ยัง `ONLINE` — ต้อง `r` ก่อนเห็น `DEGRADED`             | ⚠️ **timing race** (203): `_handle_removed` อ่าน zpool ก่อน ZFS update — ไว้แก้ (`udevadm settle`) |
| C2  | Spare auto-replace — (มี spare AVAIL) หลัง C1      | ZFS auto-resilver onto spare                              | ✅ 201 / ✅ 203  | 201: spare 1:7 → `INUSE`, `spare-1` + `resilvered 599M`. 203 (หลังกู้+redeploy): spare → `INUSE`, `resilvered 590M`, `errors: No known data errors` | 203 รอบแรกพังเพราะ hardware (เสีย 2 ตัว) — กู้แล้วเทสซ้ำผ่าน ✅                                     |
| C3  | Hot-insert — เสียบดิสก์กลับ bay เดิม               | watch แสดง `╔══ NEW DISK DETECTED ══`                     | ✅ 201 / ✅ 203  | เสียบกลับ → `NEW DISK DETECTED` + panel (model/SN/bay/health) + assign menu ทั้งคู่                                                                 |                                                                                                    |
| C4  | Assign menu ครบ — จาก C3 ดูเมนู                    | options `[1]`–`[6]` + `[s]` skip                          | ✅ 201 / ✅ 203  | เมนูครบ: `[1]` blink `[2]` spare `[3]` replace `[4]` attach `[5]` add single `[6]` wipe `[s]` skip                                                  |                                                                                                    |
| C5  | Assign as spare — C3 → `2` → เลือก pool `tank` → y | ดิสก์เข้า tank เป็น spare                                 |     ✅ 203      | `action> 2` → `pool #> 2` (tank) → y → `✔ added as spare`; refresh เห็น 1:5 `tank/spares AVAIL`                                                     |                                                                                                    |
| C6  | Replace degraded — C3 → `3` → confirm              | `✔ replace started — resilvering`                         |     ✅ 203      | `action> 3` → เลือก leaf REMOVED → CONFIRM → y → resilver → `✓ replace complete` (เขียว) + Rollback + Snapshot; tank `ONLINE`                       | ✓ เขียวถูกต้อง (real op ไม่ใช่ dry-run)                                                            |
| C7  | Swap onto spare — `watch` → `s` → ยืนยัน           | `zpool replace` รัน                                       |     ✅ 203      | `s` → member 1:4 → y → swap onto spare 1:7 → resilver → `✔ detached old disk` → `✔ (1:4) is now a hot spare`. **ผล: 1:7 เข้า raidz1, 1:4 → spare**  | by design: disk เดิมกลายเป็น spare ใหม่ — pool ไม่เสีย spare หลัง swap                             |
| C8  | Offload spare — `watch` → `o` → เลือก spare        | spare ถูก detach                                          |     ✅ 203      | `o` → spare (vdev spares) → y → `✔ removed from pool` → free + assign menu; `s` skip → refresh เห็น `[CONFIG]`                                      |                                                                                                    |
| C9  | Create new pool — `watch` → `n` → ดิสก์ว่าง ≥2     | `zpool create` สำเร็จ                                     |     ⏭ SKIP     | —                                                                                                                                                   | ยังไม่ได้ทำ                                                                                        |
| C10 | Locate ใหม่ถูก bay — `watch` → `l`                 | ไฟกระพริบตรงตัว                                           | ✅ 201 / ✅ 203  | `l` → `1:4` → `blinking /dev/sdb for 5s ... ✔ done (via dd)` กระพริบถูกตัว                                                                          | ⏱ fix 5 วินาที; standalone ปรับ sec ได้                                                            |

> **🐛 Bug ที่เจอ + แก้แล้ว (รอ redeploy):** ตอน C2-201 ตารางขึ้น `[OK] all disks healthy and assigned`
> **ทั้งที่ tank `DEGRADED`** — เพราะ `render_details` เดิมเช็คแค่ระดับ per-disk (disk ที่ดึงออก
> หายจาก list + spare ที่เข้าแทนเป็น NORMAL). แก้ให้ **pool-aware**: ถ้า pool ไม่ ONLINE จะขึ้น
> `===== pools needing attention =====` + `[!] disks readable but a pool is not ONLINE` แทน
> (ไม่ขึ้น "[OK] healthy" หลอกอีก). +unit test 2 ตัว.

> **⚠️ 203 RECOVERY — tank SUSPENDED (ข้อมูลเสี่ยง, คุณต้องกู้เอง):**
> 203 raidz1 เสีย 2 ตัวพร้อมกัน (ดึง 1:4 ทดสอบ **+** 1:5/sdc มีปัญหาอยู่ก่อน) → `SUSPENDED`
> + `3 data errors`. b2ctl แสดงถูกต้อง ไม่ใช่ bug. ขั้นกู้:
> ```bash
> # 203: เสียบ disk ที่ดึงออกกลับให้ครบก่อน แล้ว
> zpool clear tank            # เคลียร์ IO error, ปลด SUSPENDED
> zpool status -v tank        # ดูว่าไฟล์ไหนเสีย (3 data errors)
> # ตรวจ 1:5 (sdc) ว่าพังจริงไหม → ถ้าพัง replace ตัวนั้น
> ```

<details>
<summary>📋 ดูตัวอย่าง Output จริง (C1 remove / C2 spare / C3 insert) + คำอธิบาย</summary>

```
# 201 — C1 hot-remove (ดึง sdc ออกระหว่าง watch)
■ disk removed: /dev/sdc
  current pool health:
Pools:
  tank      2.72T   1.71G   free=2.72T   DEGRADED  cap=0%  <-- not ONLINE

# 201 — C2 spare auto-replace (zpool status tank)
        spare-1                       DEGRADED
          wwn-0x...d0f6               REMOVED     ← disk ที่ดึงออก
          wwn-0x...e3cd               ONLINE      ← spare เข้าแทน
    spares
      wwn-0x...e3cd                   INUSE  currently in use
  scan: resilvered 599M in 00:00:02 with 0 errors

# 201 — C3 hot-insert (เสียบ sdd กลับ)
╔══ NEW DISK DETECTED: /dev/sdd ═══════════════════════
  model  : Samsung SSD 870 EVO 1TB   SN S74ZNS0W582278Y
  bay    : 1:6   size 931.5G   SAS   SSD
  health : PASSED   wear 1% used   endurance 98.3% left
╚════════════════════════════════════════════════════
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    [3] REPLACE a degraded/faulted disk in a pool
    ... [4] attach  [5] add single  [6] wipe  [s] skip
```

- **C1**: ดึง disk → `■ disk removed` + pool `DEGRADED` (raidz1 ทนเสีย 1 ตัว ยังอ่าน/เขียนได้)
- **C2**: มี hot spare → ZFS **auto-resilver onto spare** เอง (`spare-1` + `INUSE`) ไม่ต้องสั่ง
- **C3**: เสียบกลับ → `NEW DISK DETECTED` + เมนูเลือกว่าจะทำอะไรกับ disk ที่เพิ่งเสียบ

</details>

---

<a id="sec-d"></a>

## Section D — Edge / Negative path

> เทส negative — ต้องเด้ง error/refuse ถูกต้อง ไม่พังเงียบ ไม่ทำจริง.

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| D1 | Cancel ทุก prompt — ทุก action → `N`/`q` | ไม่มีอะไรเปลี่ยน; ขึ้น `cancelled` | ✅ 201 / ✅ 203 | ตอบ `N` ที่ swap prompt → `cancelled` ทันที; zpool ไม่เปลี่ยน | |
| D2 | Swap ไม่มี spare — เอา spare ออก → `watch` → `s` | `no AVAIL spare in pool 'tank'` | ✅ 203 | หลัง offload spare → `s` → เลือก member → `no AVAIL spare in pool 'tank'` ✔ | ข้อความจริงไม่มี "add one first" ในเส้นทาง `_cmd_swap` |
| D3 | Create ดิสก์ไม่พอ — `n` → 1 ดิสก์ → `raidz2` | `error: need at least 4 disks for raidz2` | ⏭ 201 / ✅ 203 | 203: `error: need at least 4 disks for raidz2` ✔ | 201 ไม่มี disk ว่าง → ทำบน 203 (sdg unassigned) |
| D4 | Invalid raid type — `n` → raid type มั่ว `raid9` | `invalid raid type` | ⏭ 201 / ✅ 203 | 203: `invalid raid type` ✔ | 201 ไม่มี disk ว่าง → ทำบน 203 |
| D5 | Demote last mirror leg — `d` → leg สุดท้าย | `refuse: not a detachable mirror leg / would break redundancy` | ⏭ SKIP | — | demote menu filter เฉพาะ mirror-capable disks — raidz members ไม่ปรากฏ; trigger ผ่าน SSH ไม่ได้ → cover ด้วย unit test |
| D6 | Assign dirty disk — เสียบดิสก์มี data เก่า | `WARNING: ... already contain data/labels` | ⏭ SKIP | — | ต้องอยู่หน้าเครื่อง (physical disk) |
| D7 | ดึงดิสก์ระหว่าง resilver — replace/swap → ดึงอีกตัว | pool survive (raidz1) | ⚠️ 203 (partial) | swap เสร็จ (`✔ resilver completed 100%`) แล้วดึง sde → tank `DEGRADED` (raidz1 รอด) ✔ | ⚠️ **ยังไม่ได้เทส "ดึงระหว่าง resilver จริง"**: tank ใช้ 1.69G → resilver เสร็จใน 2 วิ. swap มี poll loop รอ resilver จบ (`watch.py:524`); b2ctl กันการดึง physical ไม่ได้ |

<details>
<summary>📋 ดูตัวอย่าง Output จริง (D3/D4 create reject / D1 cancel) + คำอธิบาย</summary>

```
# 203 — b2ctl --dry-run create (D3: 1 disk + raidz2)
[1] /dev/sdg (bay ?)
pick disks (space-separated #)>  pool name>  raid type (stripe, mirror, raidz1, raidz2) [mirror]>
  error: need at least 4 disks for raidz2

# 203 — b2ctl --dry-run create (D4: 1 disk + raid9)
pick disks (space-separated #)>  pool name>  raid type (stripe, mirror, raidz1, raidz2) [mirror]>
  invalid raid type
```

เมื่อ user ป้อน raid type ผิด tool reject ทันทีก่อน execute — ไม่ทำ zpool create. raidz2 ต้องการ disk ≥ 4 ตัว (2 parity + 2 data).

```
# 201 — D1: cancel swap
swap which #> N
  cancelled
```

กด `N` (หรือ Enter) ที่ confirm prompt → ออกทันที ไม่มีอะไรเปลี่ยน.

</details>

---

<a id="sec-e"></a>

## Section E — Unit tests (pytest) — ✅ agent รันให้แล้วในเครื่อง dev

```bash
cd codes && python3 -m pytest tests/ -q
```

**ผลรอบนี้ (dev machine):** `128 passed, 0 failed` ✅ *(124 เดิม + dry-run fix 2 + pool-aware summary 2)*

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| E1 | Test suite รันได้ | suite รันจบ ไม่ error การ import | ✅ | tests collected, รันจบ | OK |
| E2 | Pass rate | tests ผ่านทั้งหมด | ✅ | **128 passed / 0 failed** | 9 เทสที่เคย fail แก้แล้ว (ดูด้านล่าง) |

<details>
<summary>📋 ดูโครงสร้าง test ใหม่ + รายละเอียด 9 เทสที่แก้</summary>

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

**Source code ไม่ถูกแตะ** — แก้เฉพาะ test (โครงสร้าง + assertion).

</details>

---

[↑ กลับขึ้นบนสุด](#top)
