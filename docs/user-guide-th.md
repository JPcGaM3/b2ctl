# b2ctl — คู่มือผู้ใช้ฉบับสมบูรณ์

> 📖 อยากได้แบบทำตามทีละขั้น (กดอะไร → เห็นอะไร → แปลว่าอะไร) พร้อม output จริง →
> ดู [`walkthrough.md`](walkthrough.md)
> 🧪 อยากฝึก/ทดสอบทุก flow โดยไม่มี hardware จริง → simulation harness ที่ `codes/sim/` (ดู `codes/sim/README.md`)

---

## สารบัญ

1. [b2ctl คืออะไร?](#1-b2ctl-คืออะไร)
2. [การติดตั้ง](#2-การติดตั้ง)
3. [เริ่มต้นใช้งาน](#3-เริ่มต้นใช้งาน)
4. [🔥 Runbooks (วิธีแก้ปัญหาหน้างานจริง)](#4--runbooks-วิธีแก้ปัญหาหน้างานจริง)
5. [การอ่านตาราง](#5-การอ่านตาราง)
6. [ฟีเจอร์ทั้งหมดในโหมด watch](#6-ฟีเจอร์ทั้งหมดในโหมด-watch)
7. [ระบบความปลอดภัย](#7-ระบบความปลอดภัย)
8. [ข้อควรระวัง](#8-ข้อควรระวัง)
9. [🚀 Cheat Sheet (สรุปคำสั่งลัด)](#9--cheat-sheet-สรุปคำสั่งลัด)

---

## 1. b2ctl คืออะไร?

**b2ctl** คือเครื่องมือสำหรับ **ดูแลดิสก์** บนเซิร์ฟเวอร์ Dell R620
ที่ใช้การ์ด RAID (PERC H710) ถูกแปลงเป็นโหมด **IT/HBA** (By-pass
RAID controller) แล้วใช้ **ZFS** เป็นระบบจัดการ pool แทน

### สิ่งที่ b2ctl ทำได้:

- **ดูสุขภาพดิสก์** — แสดงตารางรวมทุกดิสก์: รุ่น, ซีเรียล, ชั่วโมงใช้งาน, ความสึกหรอ, อายุการใช้งานที่เหลือ
- **จัดการ ZFS pool** — เพิ่ม spare, สลับดิสก์, สร้าง pool ใหม่, ถอดดิสก์ออก
- **ค้นหาดิสก์ตัวจริง** — กะพริบไฟ LED เพื่อบอกว่าดิสก์อยู่ช่องไหน
- **ตรวจจับดิสก์อัตโนมัติ** — เสียบดิสก์ใหม่หรือถอดดิสก์ออก ระบบจะแจ้งเตือนทันที

> 💡 **เปรียบเทียบง่ายๆ:** b2ctl เหมือน "หมอประจำตัว" ของดิสก์ในเครื่อง — มันตรวจสุขภาพ,
> แนะนำการรักษา (เปลี่ยนดิสก์, เพิ่ม spare) และช่วยทำให้เสร็จโดยไม่ต้องพิมพ์คำสั่ง
> ZFS ยาวๆ เอง

---

## 2. การติดตั้ง

### ขั้นตอน:

```bash
git clone https://github.com/JPcGaM3/b2ctl-itmode.git b2ctl
cd b2ctl/codes
sudo ./install.sh
```

`./install.sh` เปล่า ๆ = ลง **เฉพาะ b2ctl** (package + launcher) ไม่โหลด tool ไม่
แตะ `apt` ไม่ต้องต่อเน็ต

**รูปแบบติดตั้ง 4 แบบ (เหมือนกันทั้ง `./install.sh` และ `b2ctl install`):**

| คำสั่ง | ลงอะไร |
|--------|--------|
| `./install.sh` · `b2ctl install` | **เฉพาะ b2ctl** ไม่มี tool ไม่โหลด |
| `./install.sh --with-tools` · `b2ctl install --with-tools` | b2ctl **+ tool ทั้งคู่** (sas2ircu + perccli) จาก Google Drive |
| `./install.sh --perc` · `b2ctl install --perc` | b2ctl + **perccli** + `controller.mode=raid` (เครื่อง Dell PERC RAID) |
| `./install.sh --flash` · `b2ctl install --flash` | b2ctl + **sas2ircu** + `controller.mode=it` (เครื่อง crossflashed HBA) |

- `./install.sh` deploy package; `b2ctl install` (ไม่มี flag) แค่รายงานสถานะ tool +
  mode ปัจจุบัน (b2ctl ลงไปแล้ว) — flag อื่น ๆ ทำงานเหมือนกันทั้งสองทาง
- `--with-tools` **ดาวน์โหลด** archive จาก Google Drive แตกลง `/usr/sbin/` + ลง apt
  prerequisite (`libc6-i386` สำหรับ sas2ircu 32-bit, `alien` สำหรับ perccli) ลบไฟล์
  โหลดทิ้งเมื่อเสร็จ; ต้องมี `curl`/`wget` (มีใน Proxmox VE)
- เลือก `--perc` **หรือ** `--flash` ตามฮาร์ดแวร์ — ลงเฉพาะ tool ของ backend นั้น +
  ตั้ง mode ใน `/etc/b2ctl/config.json`

### สิ่งที่ต้องมีในเครื่องก่อน:

| โปรแกรม                    | ทำหน้าที่             | ต้องมีไหม?                                   |
| -------------------------- | --------------------- | -------------------------------------------- |
| `smartctl` (smartmontools) | อ่านข้อมูลสุขภาพดิสก์ | ✅ จำเป็นต้องมี                               |
| `zpool` (zfsutils-linux)   | จัดการ ZFS pool       | ✅ จำเป็นต้องมี                               |
| `sas2ircu`                 | บอกหมายเลขช่อง (bay)  | 💡 ทางเลือก (ไม่มีก็ได้ แต่จะไม่เห็นเลข bay) |

---

## 3. เริ่มต้นใช้งาน

b2ctl มี 2 วิธีใช้หลักๆ:

### 3.1 ดูผลตรวจสุขภาพแบบเร็ว (Status)

```bash
sudo b2ctl status
```

ระบบจะแสดงตารางดิสก์ทั้งหมดครั้งเดียวแล้วจบ เหมาะสำหรับแวะดูเร็วๆ ว่า "ทุกอย่างโอเคไหม?"

**ตัวเลือกเพิ่มเติม:**

| คำสั่ง | ทำอะไร |
|--------|--------|
| `sudo b2ctl status --locate` | กะพริบไฟ LED บนดิสก์ที่มีปัญหา (~5 วินาที) |
| `sudo b2ctl status --json` | แสดงผลเป็น JSON |

### 3.2 โหมดเฝ้าดู (Watch)

```bash
sudo b2ctl watch
```

นี่คือโหมดหลัก — เปิดทิ้งไว้แล้วจะ:
- แสดงตารางสุขภาพดิสก์
- **เฝ้าดูตลอดเวลา** — ถ้าเสียบดิสก์ใหม่หรือถอดดิสก์ออก ระบบจะแจ้งเตือนทันที
- ให้คุณพิมพ์คำสั่งจัดการดิสก์ได้ตลอด

หลังจากเปิดจะเห็นหน้าจอแบบนี้:

<details>
<summary>📋 ดูหน้าจอตัวอย่างโหมด Watch</summary>

<pre>
========================================================================================================
BAY   DEV       IF   MODEL                   SERIAL            POWER_ON      WEAR(used) END(left)  ...
--------------------------------------------------------------------------------------------------------
1:0   sdf       SAS  Samsung SSD 860         S5G8NE0MXXXXXXX   51020h(~5.8y) 1%         99.2%      ...
1:1   sda       SAS  Samsung SSD 860         S5G8NE0MXXXXXXX   51021h(~5.8y) 1%         99.1%      ...
1:4   sdb       SAS  Samsung SSD 870         S74ZNS0WXXXXXXX   18238h(~2.1y) 1%         98.4%      ...
1:5   sdc       SAS  Samsung SSD 870         S74ZNS0WXXXXXXX   18243h(~2.1y) 1%         98.4%      ...
1:6   sdd       SAS  Samsung SSD 870         S74ZNS0WXXXXXXX   18246h(~2.1y) 1%         98.4%      ...
1:7   sde       SAS  Samsung SSD 870         S74ZNS0WXXXXXXX   18247h(~2.1y) 1%         99.8%      ...
========================================================================================================
Storage summary:
  TYPE NAME            LEVEL    STATE     SIZE      USED      FREE
  SW   rpool           mirror   ONLINE    952G      4.83G     947G
  SW   tank            raidz1   ONLINE    2.72T     1.72G     2.72T
[OK] all disks healthy and assigned

[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [t]oggle-dryrun  [n]ew-pool  [e]xtend  [m]aint  [u]dev-rescue  [x]destroy-pool  [l]ocate  [q]uit   (or hot-plug)
b2ctl&gt;
</pre>
</details>

จากนั้นพิมพ์ตัวอักษรเดียวเพื่อทำงาน (ดูรายละเอียดในหัวข้อ 6)

---

## 4. 🔥 Runbooks (วิธีแก้ปัญหาหน้างานจริง)

### 📌 สถานการณ์ 1: ดิสก์เสีย — ต้องเปลี่ยนใหม่

**อาการ:** ตารางแสดง LEVEL = `CRITICAL`, HEALTH = `FAILED`, หรือ BAD > 0

**ขั้นตอน:**

1. **ค้นหาดิสก์ที่เสีย** — กด `l` แล้วพิมพ์ serial ของดิสก์ที่เสีย → ไฟกะพริบ
2. **ดึงดิสก์เสียออก** — ถอดออกจากช่อง (ระบบจะแจ้ง "disk removed")
3. **เสียบดิสก์ใหม่เข้าไป** — ระบบจะแจ้ง "NEW DISK DETECTED"
4. **เลือก [3] REPLACE** — เพื่อแทนที่ดิสก์ที่เสีย

```
  action> 3
    [1] tank: /dev/disk/by-id/wwn-0x500... (FAULTED)
  replace #> 1
  replace /dev/disk/by-id/wwn-0x500... in 'tank' with (1:5) Samsung SSD 870 (S74ZNS0W...)? [y/N]> y
  ✔ replace started — resilvering
```

5. **รอ resilver เสร็จ** — ZFS จะ rebuild ข้อมูลอัตโนมัติ

---

### 📌 สถานการณ์ 2: ดิสก์เริ่มสึกหรอ — สลับไปยัง spare ก่อนเสีย

**อาการ:** LEVEL = `WARNING`, END(left) ต่ำลง, WEAR(used) สูงขึ้น

**ขั้นตอน:**

1. **ตรวจสอบว่ามี spare ในพูล** — ดูคอลัมน์ POOL ว่ามีดิสก์ที่ขึ้น `tank/spares`
2. **กด `s` (swap)** — เลือกดิสก์ที่สึกหรอ
3. **ยืนยัน `y`** — ระบบ resilver ข้อมูลไปยัง spare แล้วสลับที่กัน
4. **เสร็จ** — ดิสก์เก่ากลายเป็น spare, ดิสก์ใหม่ทำงานแทน

> 💡 ไม่ต้องถอดดิสก์ออกจากเครื่อง — ทั้งสองตัวยังอยู่ในช่อง

---

### 📌 สถานการณ์ 3: เพิ่ม spare ใหม่เข้าพูล

**ใช้เมื่อ:** pool ไม่มี spare สำรอง และต้องการเพิ่ม

**ขั้นตอน:**

1. **เสียบดิสก์ใหม่** — ระบบจะแจ้ง "NEW DISK DETECTED"
2. **เลือก [2] Add to a pool as hot SPARE**
3. **เลือก pool** ที่ต้องการเพิ่ม spare
4. **ยืนยัน `y`**

```
  action> 2
    [1] rpool (ONLINE)
    [2] tank (ONLINE)
  pool #> 2
  add (1:7) Samsung SSD 870 (S74ZNS0W582280E) to 'tank' as spare? [y/N]> y
  ✔ added as spare
```

---

### 📌 สถานการณ์ 4: สร้างพูลใหม่จากดิสก์ว่าง

**ขั้นตอน:**

1. **กด `n`** (new pool)
2. **เลือกดิสก์** — พิมพ์หมายเลขคั่นด้วยเว้นวรรค เช่น `1 2 3`
3. **ตั้งชื่อ pool** — เช่น `backup`, `data`
4. **เลือกประเภท RAID** — แนะนำ `raidz1` สำหรับ 3 ดิสก์ หรือ `mirror` สำหรับ 2 ดิสก์
5. **ยืนยัน `y`**

> ⚠️ ถ้าดิสก์มี label/ข้อมูลเก่า ระบบจะเตือนและถามว่าจะ wipe ก่อนหรือไม่

---

### 📌 สถานการณ์ 5: ถอดดิสก์ออกจากพูลอย่างปลอดภัย

**ขั้นตอน:**

1. **กด `o`** (offload)
2. **เลือกดิสก์** ที่ต้องการถอด
3. **ยืนยัน `y`** — b2ctl จะ offline ดิสก์และโอนให้ spare รับหน้าที่ (mirror เท่านั้น: ไม่มี resilver — pool แค่สูญเสีย redundancy; raidz: มี resilver)
4. **กะพริบ LED** — ระบบจะบอกว่า "blinking LED" เพื่อให้คุณรู้ว่าต้องดึงช่องไหน
5. **ดึงดิสก์ออก** — ตอนนี้ปลอดภัยแล้ว

### 📌 สถานการณ์ 6: ดิสก์ SAS ขึ้น `NOREAD` / `status` ช้า (เครื่อง RAID)

บนเครื่อง PERC การอ่าน SMART ของทุกดิสก์วิ่งผ่าน controller (`smartctl -d megaraid`).
ถ้าอ่านหลายลูกพร้อมกัน controller ตันได้ ดิสก์เก่า/ช้าเลยอ่านไม่ทัน timeout → ขึ้น
**`NOREAD` / "SMART unreadable"** และ scan ช้า. ปรับได้ที่ `/etc/b2ctl/config.json`:

```json
{ "smart": { "timeout": 25, "megaraid_workers": 2 } }
```

`timeout` = วินาทีต่อดิสก์ (เพิ่มถ้าดิสก์ช้า); `megaraid_workers` = อ่านพร้อมกันกี่ลูกผ่าน
controller (ลดถ้าตัน). ถ้าปรับแล้วยัง `NOREAD` อยู่ = ดิสก์นั้นน่าจะกำลังจะพังจริง — เช็คช่องนั้น

---

## 5. การอ่านตาราง

แต่ละคอลัมน์ในตารางหมายถึง:

| คอลัมน์ | ความหมาย | ตัวอย่าง |
|---------|----------|---------|
| **BAY** | หมายเลขช่องดิสก์ (enclosure:slot) | `1:4` = ตู้ 1 ช่อง 4 |
| **DEV** | ชื่อ device ใน Linux | `sda`, `sdb`, `sdc` |
| **IF** | ชนิดการเชื่อมต่อ | `SAS`, `SATA`, `NVMe` |
| **MODEL** | รุ่นดิสก์ | `Samsung SSD 870` |
| **SERIAL** | หมายเลขซีเรียล (เฉพาะตัว) | `S74ZNS0WXXXXXXX` |
| **POWER_ON** | จำนวนชั่วโมงที่เปิดใช้งาน | `18238h(~2.1y)` |
| **WEAR(used)** | ดิสก์สึกหรอไปกี่ % (ยิ่งน้อยยิ่งดี) | `1%`|
| **END(left)** | อายุการใช้งานที่เหลือ (คำนวณจาก TBW) | `98.4%` |
| **WRITTEN** | เขียนข้อมูลไปแล้วเท่าไร / ต่อ TBW ที่รับรอง | `9.87TB/600TBW` |
| **BAD** | จำนวน bad sectors / grown defects | `0` = ปกติ; **SSD/NVMe** มากกว่า 0 = CRITICAL; **HDD** มีนิดหน่อยยังใช้ได้ (ดู LEVEL) |
| **HEALTH** | ผลตรวจ SMART | `PASSED`, `FAILED` |
| **POOL** | อยู่ใน pool / vdev ไหน | `tank/raidz1-0`, `rpool/mirror-0` |
| **STATUS** | สถานะ vdev ของ ZFS — สีเขียว ONLINE/AVAIL, สีเหลือง DEGRADED/INUSE→bay, สีแดง FAULTED/REMOVED | `ONLINE`, `AVAIL`, `INUSE→1:4` |
| **HEALTH_CHK** | ผล **long self-test** ล่าสุดจาก log ของดิสก์เอง (v0.17.0) | `OK 120hPOH`, `ERR 30hPOH`, `-` |
| **LEVEL** | ระดับสถานะรวม | ดูตารางด้านล่าง |

**HEALTH_CHK วัดเป็นชั่วโมงเปิดเครื่อง (power-on hours) ไม่ใช่วันที่จริง.** ช่องนี้แสดง `OK`
(long self-test ผ่าน) หรือ `ERR` (ไม่ผ่าน) พร้อมระยะเวลาตั้งแต่ทดสอบล่าสุด วัดเป็น
**power-on hours** (`hPOH`) — เพราะดิสก์บันทึกผล self-test เทียบกับชั่วโมงใช้งานสะสม
ไม่ใช่ปฏิทิน เช่น `OK 120hPOH` = "long self-test ล่าสุดผ่าน และดิสก์เปิดใช้งานมาอีก 120
ชั่วโมงหลังจากนั้น" ส่วน `-` = ยังไม่เคยมี long self-test บันทึกไว้ ค่านี้สะท้อน long test
ครั้งล่าสุดไม่ว่าใครสั่ง — health-check ใน `[m]aint` หรือ `smartctl -t long` ที่รันเอง — และ
อ่านได้ทั้ง **SATA, SAS และ NVMe** (v0.18.0 แก้การตัดเกรดของ SAS `Completed`) วิธีสั่ง
ทดสอบดู `[m]aint` ด้านล่าง

**ตารางสรุป pool ยังเพิ่มคอลัมน์ `SCRUB` และ `TRIM` (v0.17.0)** แสดงเวลา scrub ล่าสุด
(อ่านสดจาก `zpool status`) และ trim ล่าสุด (จากประวัติ maintenance ของ b2ctl) เช่น
`2d ago` ถ้าว่าง/`-` = ยังไม่มีบันทึก — สั่งได้ด้วย `[m]aint` หรือ `b2ctl maint scrub|trim <pool>`

### ความหมายของ LEVEL:

| สี | ระดับ | ความหมาย |
|----|------|----------|
| 🟢 | **NORMAL** | ดิสก์แข็งแรง อยู่ในพูลเรียบร้อย — ไม่ต้องทำอะไร |
| 🔵 | **CONFIG** | ดิสก์แข็งแรงแต่ **ยังไม่ได้อยู่ในพูลไหนเลย** — ต้องตั้งค่า (เพิ่มเป็น spare หรือสร้าง pool) |
| 🟡 | **WARNING** | เริ่มมีปัญหา — อายุ/wear เหลือน้อย, vdev DEGRADED, หรือ **HDD** มี defect ปานกลาง (`>50` grown defects หรือมี pending sector) — เตรียมเปลี่ยน |
| 🔴 | **CRITICAL** | อันตราย! — SMART ไม่ผ่าน, อายุเหลือน้อยมาก, vdev FAULTED, GHOST, **SSD/NVMe** มี bad sector แม้แต่ตัวเดียว, หรือ **HDD** defect หนัก (`>200`) / uncorrectable — ทำทันที |

**เกณฑ์ bad-sector แยกตามชนิดดิสก์ + ปรับได้ (v0.13.0):** SSD/NVMe เข้ม (bad sector >0 =
CRITICAL); HDD ทน grown defect ที่ remap นิ่งแล้วได้ (`>50 → WARNING`, `>200 → CRITICAL`).
ปรับ band ต่อชนิดใน `/etc/b2ctl/config.json` ใต้ `health` (ดู DevOps guide) — ตั้งเป็น
`"N/A"` = ปิดการเช็คนั้น

---

## 6. ฟีเจอร์ทั้งหมดในโหมด watch

หลังจากเข้า `sudo b2ctl watch` คุณสามารถพิมพ์คำสั่งได้ที่ prompt `b2ctl>`:

---

### 6.1 `r` — รีเฟรชตาราง (Refresh)

**ใช้เมื่อ:** ต้องการดูข้อมูลล่าสุด

```
b2ctl> r
```

ระบบจะสแกนดิสก์ทั้งหมดใหม่และแสดงตารางอีกครั้ง กด `r` ได้เรื่อยๆ ตามต้องการ

---

### 6.2 `a` — Assign (จัดสรรดิสก์ว่างเข้าพูล)

**ใช้เมื่อ:** มีดิสก์ที่ยังไม่ได้อยู่ในพูลไหน (แสดงเป็น CONFIG) และต้องการจัดสรรมัน

```
b2ctl> a
    [1] bay 1:7 /dev/sde (Samsung SSD 870, SN S74ZNS0WXXXXXXX)
  assign which #> (space-separated for batch)
```

รายการนี้รวมดิสก์ว่าง **3 แบบ**:

- ดิสก์ว่างปกติ → `[1] bay 1:7 /dev/sde (Samsung SSD 870, SN …)` — เปิดเมนู action ด้านล่าง
- ดิสก์ **`[GHOST]`** (OS ปฏิเสธ ไม่มี `/dev`) → `[1] [GHOST] bay 1:4 (SN …) — needs
  wipe` — ไปทาง wipe/rescue (ดู `[u]dev-rescue` ด้วย)
- ดิสก์ **PERC Unconfigured-Good** (เฉพาะเครื่อง RAID mode) → `[1] bay 32:4 (Samsung
  …, SN …) (PERC Unconfigured-Good)` — ไปเมนู hardware RAID (set JBOD, สร้าง volume,
  หรือเพิ่มเป็น spare)

**เลือกหลายตัวพร้อมกัน / batch (v0.11.0).** เลือกดิสก์ **หลายตัว**พร้อมกันได้ คั่นด้วยเว้นวรรค
เหมือน `[n]ew-pool` / `[m]aint` health-check:

```
  assign which #> (space-separated for batch) 3 4 5
```

- เลือก **1 ตัว** → เปิดเมนู action ต่อดิสก์ (เหมือนเดิม — เพราะ REPLACE / ATTACH ทำได้ทีละตัว)
- เลือก **2 ตัวขึ้นไป** → เปิดเมนู **batch**: เลือก action เดียว แล้วทำกับ**ทุกตัว**ที่เลือก ยืนยันครั้งเดียว
  ต้องเป็นดิสก์ **ชนิดเดียวกัน** — ถ้าปนชนิด (เช่น PERC + NVMe ว่าง) ระบบจะปฏิเสธพร้อมนับจำนวนแต่ละชนิด
  ให้เลือกทีละชนิด
  - **PERC Unconfigured-Good** → `[1]` blink ทุกตัว · `[2]` **set JBOD ทุกตัว** (เคสที่ใช้บ่อย: เตรียม
    หลายดิสก์ให้ ZFS) · `[3]` สร้าง hardware RAID volume **ก้อนเดียว**จากทุกตัว · `[4]` เพิ่มทุกตัวเป็น hot spare
  - **ดิสก์ว่าง (ZFS-poolable)** → `[1]` blink ทุกตัว · `[2]` เพิ่มทุกตัวเข้า pool เป็น hot SPARE · `[3]` WIPE ทุกตัว

เลือกดิสก์ว่างปกติ ระบบจะถามว่าจะทำอะไรกับมัน:

```
  Disk /dev/disk/by-id/wwn-0x5002538... is free.
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

แต่ละตัวเลือกคือ:

| ตัวเลือก | ทำอะไร | ใช้เมื่อ |
|----------|--------|---------|
| **[1]** กะพริบ LED | ไฟดิสก์จะกะพริบ ~5 วินาที | ต้องการรู้ว่าดิสก์อยู่ช่องไหน ก่อนดึงออก |
| **[2]** เพิ่มเป็น SPARE | เพิ่มเข้า pool เป็นดิสก์สำรอง | เพิ่มดิสก์สำรองไว้ใน pool |
| **[3]** REPLACE ดิสก์เสีย | แทนที่ดิสก์ที่เสียในพูล | มีดิสก์ FAULTED/DEGRADED ในพูล และต้องการแทนที่ |
| **[4]** ATTACH เป็น mirror | ต่อเข้ากับดิสก์ที่มีอยู่เป็นคู่ mirror | ต้องการเพิ่ม redundancy |
| **[5]** ADD แบบไม่มี redundancy | เพิ่มเข้า pool เป็น vdev ตัวเดียว | ⚠️ อันตราย — ดิสก์เสีย 1 ตัว = ข้อมูลหายหมด |
| **[6]** WIPE ล้างข้อมูล | ลบทุกอย่างในดิสก์ | ต้องการเริ่มต้นใหม่ / เตรียมสร้าง pool ใหม่ |
| **[s]** ข้าม | ไม่ทำอะไรตอนนี้ | ยังไม่ตัดสินใจ |

> **⚠️ สำคัญ** — ทุกตัวเลือกที่เปลี่ยนแปลงข้อมูลจะถาม `[y/N]` ยืนยันก่อนทำเสมอ

#### ตัวอย่าง: เพิ่มดิสก์เป็น spare

```
  action> 2
    [1] rpool (ONLINE)
    [2] tank (ONLINE)
  pool #> 2
  add (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX) to 'tank' as spare? [y/N]> y
  ✔ added as spare
```

---

### 6.3 `o` — Offload (ถอดดิสก์ออกจากพูล)

**ใช้เมื่อ:** ต้องการเอาดิสก์ออกจาก pool เพื่อถอดออกทางกายภาพ

```
b2ctl> o
    [1] bay 1:0 /dev/sdf in rpool (vdev mirror-0)
    [2] bay 1:1 /dev/sda in rpool (vdev mirror-0)
    [3] bay 1:4 /dev/sdb in tank (vdev raidz1-0)
    [4] bay 1:5 /dev/sdc in tank (vdev raidz1-0)
    [5] bay 1:6 /dev/sdd in tank (vdev raidz1-0)
    [6] bay 1:7 /dev/sde in tank (vdev spares)
  offload which #>
```

ระบบจะทำงานต่างกันขึ้นอยู่กับประเภทดิสก์:

| ดิสก์เป็นอะไร | ระบบทำอะไร |
|--------------|-----------|
| **Spare** (สำรอง) | ถอดออกจาก pool ทันที (ไม่ต้อง resilver) |
| **Mirror member** | ถอดออกจาก mirror ทันที (ถ้ามี leg อื่นที่ ONLINE) |
| **RAIDZ member** | ⚠️ ต้อง resilver ไปยัง spare ก่อน — ใช้เวลา |

#### ตัวอย่าง: ถอด spare ออก

```
  offload which #> 6
  This disk is a hot spare. Remove (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX) from 'tank'? [y/N]> y
  ✔ removed from pool
```

#### ตัวอย่าง: ถอดดิสก์จาก RAIDZ (ต้องใช้ spare)

```
  offload which #> 3
  Replace (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) onto spare (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX)? [y/N]> y
  ✔ replace started — resilvering onto spare
  resilvering... 45.2% done, ETA 00:03:21
  ✔ resilver completed 100%
  ✔ detached old disk /dev/sdb
  please pull bay 1:4 ... blinking LED
```

> ⚠️ **สำคัญ:** ถ้ากด `N` (ปฏิเสธ) ระบบจะกลับเมนูหลักทันที — ดิสก์จะไม่ถูกถอดออก
> ไม่ต้องกังวลเรื่องความปลอดภัย

---

### 6.4 `s` — Swap (สลับดิสก์สึกหรอไปยัง spare)

**ใช้เมื่อ:** ดิสก์เริ่มสึกหรอ (WEAR สูง, END เหลือน้อย) แต่ยังไม่เสีย — ต้องการสลับไปยัง
spare ก่อนที่จะเสีย

> 💡 **ความแตกต่างจาก offload:** swap = สลับที่กัน (ดิสก์เก่ากลายเป็น spare อัตโนมัติ,
> ดิสก์ใหม่เข้าไปทำงานแทน) ส่วน offload = ถอดออกไปเลย

```
b2ctl> s
    [1] (1:0) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
    [2] (1:1) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
    [3] (1:4) Samsung SSD 870 (S74ZNS0WXXXXXXX) in tank
    [4] (1:5) Samsung SSD 870 (S74ZNS0WXXXXXXX) in tank
    [5] (1:6) Samsung SSD 870 (S74ZNS0WXXXXXXX) in tank
    [6] (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX) in tank
  swap which #> 6
  swap (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX) onto spare (1:4) Samsung SSD 870 (S74ZNS0W582283V)? [y/N]> y
  ✔ swap started — resilvering onto spare
  ✔ resilver completed 100%
  ✔ detached old disk /dev/sde
  ✔ (1:7) Samsung SSD 870 (S74ZNS0WXXXXXXX) is now a hot spare in 'tank'
```

**ผลลัพธ์:** ดิสก์ spare (1:4) เข้าไปทำงานแทนใน raidz1, ส่วนดิสก์เก่า (1:7) กลายเป็น
spare อัตโนมัติ — **ไม่ต้องถอดดิสก์ออก ทั้งสองตัวยังอยู่ในเครื่อง**

---

### 6.5 `d` — Demote (ลดดิสก์ mirror ลงเป็น spare)

**ใช้เมื่อ:** มี mirror ที่มีมากกว่า 2 ขา (เช่น 3-way mirror) และต้องการถอด 1 ขาลงไปเป็น spare

```
b2ctl> d
    [1] (1:0) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
    [2] (1:1) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in rpool
  demote which #> 2
  demote (1:1) SAMSUNG MZ7LH1T9 (S4F2NY0XXXXXXX) in 'rpool' to a hot spare? [y/N]> y
  ✔ demoted to spare
```

> ⚠️ ระบบจะ **ปฏิเสธ** ถ้าการถอดจะทำให้ mirror เหลือไม่พอ (เหลือ 1 ขาสุดท้าย = ไม่มี
> redundancy)

---

### 6.6 `n` — New Pool (สร้างพูลใหม่)

**ใช้เมื่อ:** มีดิสก์ว่างหลายตัวและต้องการสร้าง ZFS pool ใหม่

```
b2ctl> n
    [1] /dev/sdb (bay 1:4)
    [2] /dev/sdc (bay 1:5)
    [3] /dev/sdd (bay 1:6)
  pick disks (space-separated #)> 1 2 3
  size to use per disk (over-provision) [full disk]>
  pool name> backup
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]> raidz1
  ...
  autotrim: [1] off — manual TRIM via [m]aint / `b2ctl maint trim` (recommended)
            [2] on  — zpool autotrim=on (ZFS trims inline)
    choose [1]>
  autoscrub: [1] off — manual scrub via [m]aint / `b2ctl maint scrub` (recommended)
             [2] on  — monthly zfs-scrub timer (self-heals silent bitrot)
    choose [1]>
  ...
  create pool 'backup' (raidz1) with 3 disks (full disk)? [y/N]> y
  ✔ pool created
  [!] autoscrub OFF — no monthly self-heal scheduled for 'backup'; run `b2ctl maint scrub backup` (or [m]aint) periodically
  [!] autotrim OFF — TRIM manually via `b2ctl maint trim backup` (or [m]aint)
```

**`size to use per disk` (over-provision):** กด Enter = ใช้ **ดิสก์ทั้งลูก** (ค่า
มาตรฐาน layout ของ ZFS) หรือใส่ขนาด (เช่น `32G`, `512G`) b2ctl จะสร้างพาร์ทิชันขนาดนั้น
บนแต่ละดิสก์แล้วส่งพาร์ทิชันให้ ZFS เหลือที่ว่างไว้เป็น spare area ของ SSD วิธีนี้ **ไม่ได้**
ทำให้เร็วขึ้น (พาร์ทิชันที่ align ถูกกับ `ashift=12` เร็วเท่าดิสก์ทั้งลูกบน Linux ZFS) แต่ที่ว่าง
สำรองช่วยเรื่อง **อายุการใช้งาน SSD และความสม่ำเสมอของการเขียน** โดยแลกกับเนื้อที่ที่ใช้ได้
คุ้มค่าเฉพาะ SSD ที่ทำ SLOG / L2ARC โดยเฉพาะ — ดู `[e]xtend`

> ⚠️ **การ over-provision จะ WIPE ดิสก์ที่เลือกก่อน (v0.18.0)** — `sgdisk` วางพาร์ทิชัน 1
> ที่ sector ว่างตัวแรกที่ align ดังนั้นตารางพาร์ทิชันเก่าบนดิสก์มือสองจะดันพาร์ทิชันใหม่เลย
> ของเดิม (หรือเลยท้ายดิสก์ลูกเล็ก) = บั๊ก `partition failed` ของ v0.17.0 ตอนนี้ b2ctl เคลียร์
> GPT ของแต่ละดิสก์ก่อนแบ่งพาร์ทิชัน มีคำเตือน WIPE บอกชื่อทุกดิสก์และถามยืนยัน **ครั้งเดียว
> ก่อนแตะดิสก์ (ตอบ N = ไม่ wipe อะไรเลย)** แล้วจึง wipe → partition → ส่ง `-part1` ให้ ZFS ·
> ถ้าเว้นขนาดว่างจะข้ามขั้นตอนนี้ทั้งหมด (ใช้ดิสก์ทั้งลูก) · `b2ctl create --size` และ
> `cache-add`/`log-add --size` บน CLI ก็เหมือนกัน

**`autotrim` / `autoscrub` (ค่าเริ่มต้น = OFF ทั้งคู่):** ทั้งสองคำถามตอนนี้เป็น `[1] off` (ค่า
เริ่มต้น) `/ [2] on` และ **OFF = ทำเองอย่างเดียว ไม่ติดตั้ง timer** (v0.18.0):

- **`autotrim off`** (ค่าเริ่มต้น): TRIM ด้วยมือผ่าน `[m]aint` / `b2ctl maint trim <pool>` ·
  **`autotrim on`** ตั้ง `zpool autotrim=on` ให้ ZFS trim ต่อเนื่องเอง *(กลับด้านจากเดิมที่
  `autotrim off` เคยตั้ง timer `zfs-trim` รายเดือน — ตอนนี้ไม่มี trim timer แล้ว)*
- **`autoscrub off`** (ค่าเริ่มต้น): scrub อ่านทุกบล็อก ตรวจ checksum และซ่อม pool ที่มี
  redundancy เอง เมื่อปิด **การ scrub ด้วยมือคือแนวทางหลัก** (`[m]aint` / `b2ctl maint
  scrub <pool>`) — pool ใหม่จะ *ไม่มี* scrub ตามกำหนดจนกว่าจะเลือก `[2] on` (timer
  `zfs-scrub-monthly@<pool>.timer` รายเดือน) b2ctl จะเตือนและคอลัมน์ `SCRUB` โชว์ว่า scrub
  ล่าสุดเก่าแค่ไหน — ควรสั่งเป็นระยะ

**ประเภท RAID ที่เลือกได้:**

| ประเภท | ดิสก์ขั้นต่ำ | ความปลอดภัย | เนื้อที่ได้ |
|--------|------------|------------|-----------|
| **stripe** | 1 | ไม่มี — เสีย 1 ตัว = ข้อมูลหายหมด | เต็ม 100% |
| **mirror** | 2 | ทนเสียได้ 1 ตัว | 50% |
| **raidz1** | 2 (แนะนำ 3+) | ทนเสียได้ 1 ตัว | (N-1)/N |
| **raidz2** | 4 | ทนเสียได้ 2 ตัว | (N-2)/N |

> ⚠️ ถ้าดิสก์ที่เลือกมีข้อมูลเก่าอยู่ ระบบจะเตือนและถามว่าจะ wipe ก่อนหรือไม่

---

### 6.7 `e` — Extend (เพิ่ม/ถอด cache · log)

**ใช้เมื่อ:** เร่งความเร็ว pool เดิมด้วย L2ARC (read cache) หรือ SLOG (sync-write log)
รายละเอียดเต็มดูหัวข้อ "เพิ่ม cache / log ให้ pool" ท้ายคู่มือ

```
b2ctl> e
  pool #> 2
  [1] add L2ARC cache (read cache; loss = harmless)
  [2] add SLOG log   (sync-write accel; mirror + PLP recommended)
  [3] remove a cache/log device
  [4] replace/repair a degraded cache/log device
  action> 1
    [1] /dev/sde (bay 1:7)
  pick disk(s) (space-separated #)> 1
  add 1 L2ARC cache device(s) to 'tank'? [y/N]> y
  ✔ cache added
```

- **[1] cache** = L2ARC (พังแล้วแค่ cache miss **mirror ไม่ได้** จึงไม่มี prompt topology) ·
  **[2] log** = SLOG · **[3] remove** = ถอด cache/log ออก
- **SLOG topology (v0.17.0):** เมื่อเลือก **≥2 ลูก** b2ctl ถาม topology — `mirror` (มี
  redundancy, แนะนำ), `raid10` (stripe of mirrors, จำนวนคู่ ≥4), หรือ `single/striped`
  (ไม่มี redundancy) log vdev **เป็น raidz ไม่ได้** (กฎ ZFS) จึงไม่มีให้เลือก · ลูกเดียว b2ctl
  เตือน (log หายอาจเสีย sync write ที่ค้างอยู่) · ใช้ SSD ที่มี **PLP** เสมอ
- **`size to use per device` (over-provision):** กด Enter = ใช้ทั้งลูก หรือใส่ขนาด
  (เช่น `32G`) เพื่อแบ่งพาร์ทิชันแล้วส่งพาร์ทิชันให้ ZFS เหลือ spare area — ช่วยอายุ/ความ
  สม่ำเสมอของ SSD SLOG/L2ARC (ไม่เปลี่ยน throughput ถ้า align ถูก) ดู `[n]ew-pool` ·
  **การใส่ขนาดจะ WIPE แต่ละดิสก์ก่อน (v0.18.0)** — เคลียร์ GPT ก่อนแบ่งพาร์ทิชัน (กันตาราง
  พาร์ทิชันเก่าชนกัน) มีคำเตือน WIPE + ยืนยันครั้งเดียว
- CLI: บังคับ topology ด้วย `b2ctl log-add <pool> <dev…> --mirror|--raid10`;
  over-provision ด้วย `--size 32G` บน `cache-add`/`log-add`

- **[4] replace/repair (v0.14.0)** — cache พัง หรือ SLOG mirror ขาไปข้างนึงพัง: ถอดลูกเสีย
  เสียบลูกใหม่ แล้ว `[e] → [4]` เลือกตัวที่ degraded + ลูกใหม่ b2ctl ซ่อมตามชนิด:
  - **cache** → `remove` เก่า + `add` ใหม่ (L2ARC `replace` ไม่ได้; พังแล้วไม่เสียหาย)
  - **SLOG mirror-leg** → `zpool replace` (resilver สั้น ๆ มี progress bar) — **ปลอดภัยกว่า**
    detach+attach เพราะ `replace` ไม่ต้องให้เราเลือกลูกที่จะ *ทำลาย* เอง (พิมพ์ผิดไม่ทำขาดีพัง)
  - **SLOG ลูกเดียว** ที่หายไปเลย (REMOVED/UNAVAIL) → `remove` + `add`
  - CLI: `b2ctl cache-replace <pool> <old> <new>` · `b2ctl log-replace <pool> <old> <new>`

---

### 6.8 `m` — Maintenance ด้วยมือ (scrub · trim · health-check)

**ใช้เมื่อ:** สั่ง scrub หรือ TRIM บน pool ด้วยมือ (แนวทางหลัก เพราะ scrub ตามกำหนดเป็น
opt-in และไม่มี trim timer แล้ว) หรือ health-check ดิสก์ (long self-test + เลือก surface scan)
ก่อนเข้า pool

> **v0.18.0:** ปุ่ม `[b]urnin` เดิม **หายไปแล้ว** — การ vet ดิสก์ย้ายมาเป็น `[m]aint → [3]
> health-check` เหลือ maintenance surface เดียว แทนที่จะมี 2 คำสั่งที่รัน `smartctl -t long`
> เหมือนกัน

```
b2ctl> m
  [1] scrub  (verify checksums + self-heal)
  [2] trim   (release unused SSD blocks)
  [3] health-check (smartctl -t long + optional badblocks + verdict)
  action> 1
    [1] tank (ONLINE)
    [2] rpool (ONLINE)
  pool #> 1
  start scrub on 'tank'? [y/N]> y
  ✔ scrub started
  watch live progress (Ctrl-C detaches; kernel keeps running)? [y/N]> y
  scrubbing... 42.0% done, ETA 00:03:11
  ✔ scrub completed
```

- **scrub** — อ่านทุกบล็อกที่ใช้งาน ตรวจ checksum และซ่อม pool ที่มี redundancy เอง คือ
  แนวป้องกัน bitrot ตัวจริง ถ้าปิด autoscrub ไว้ให้สั่งเองสม่ำเสมอ หน้าจอสดโชว์ `% done` + ETA
- **trim** — บอก SSD ว่าบล็อกไหนว่าง **ไม่มี trim timer แล้ว (v0.18.0)** ดังนั้นถ้าไม่ได้ตั้ง
  `autotrim=on` (trim ต่อเนื่อง) นี่คือวิธีคืนบล็อก ความคืบหน้าหยาบ (ZFS เปิดข้อมูล trim จำกัด)
- **health-check** — เอนจิน vet ดิสก์ (คือ burn-in เดิม) ดูด้านล่าง
- **Ctrl-C = detach ไม่ใช่ยกเลิก** ระหว่างดู scrub/trim สด Ctrl-C พากลับ prompt แต่ **kernel
  ยังรันต่อ** — ตรวจซ้ำด้วย `zpool status <pool>` หรือคอลัมน์ SCRUB ของ pool
- ทุก scrub / trim / health-check ถูกบันทึกลง **ประวัติ maintenance** (`maint.jsonl`) ดูด้วย
  `b2ctl maint --log [--last N]`
- CLI: `b2ctl maint scrub [<pool>]` · `b2ctl maint trim [<pool>]` (ไม่ใส่ pool = ถาม) ·
  `b2ctl maint --log` · คำสั่งบนสุด `b2ctl scrub <pool>` / `b2ctl trim <pool>` ยังใช้ได้เป็น alias

---

### 6.9 `m` → health-check — vet ดิสก์ก่อนเข้าใช้ (คือ burn-in เดิม)

**ใช้เมื่อ:** vet ดิสก์ใหม่/มือสองก่อนเข้า pool — เลือกได้**หลายลูก** (คั่นเว้นวรรค) รัน long
self-test (+เลือก badblocks) แบบ**ไม่บล็อก** มีหน้าจอสดโชว์แถบ + เวลาที่เหลือ ออกทิ้งให้รันต่อ
ได้ (Ctrl-C) เข้าที่ `[m]aint → [3] health-check` (ไม่มีปุ่ม `[b]` แยกแล้วใน v0.18.0)

```
b2ctl> m
  action> 3
    [1] /dev/sdb (bay 32:4) Samsung SSD 870 EVO 1TB
    [2] /dev/sda (bay 32:5) Samsung SSD 870 EVO 1TB
  health-check which #> (space-separated) 1 2
  health-check 2 disk(s) (long self-test)? [y/N]> y
  also run a full read-surface scan (badblocks, read-only, hours)? [y/N]> y

 BAY     DISK      SELF-TEST                     SURFACE SCAN (badblocks)
 32:4    sdb       [########------]  62%  ~1h10m  [###-----------]  18%  ~4h30m
```

- **เลือกดิสก์ไหน — เฉพาะดิสก์ว่าง/spare ทั้งสองทาง:** health-check vet เฉพาะดิสก์ **ว่างหรือ
  spare** · ในโหมด watch ลิสต์เฉพาะพวกนั้น และ `b2ctl maint health <dev…>` จะ **ปฏิเสธ**
  ลูกที่อยู่ใน pool (`… is in pool '<pool>' — self-test it with \`smartctl -t long\` directly`) ·
  จะ self-test **ลูกที่ใช้งานอยู่ใน pool** ให้รัน `smartctl -t long <by-id>` เองใน shell (b2ctl ไม่
  สั่งให้) แต่คอลัมน์ HEALTH_CHK ของลูกนั้นยังอัปเดตแบบ passive จาก `smartctl -a` ตอน refresh
- กลับเข้าดูด้วย `b2ctl maint health --status` หรือ `[m]` → `[3]` อีกครั้งเพื่อเปิดเมนู
  (**[v]** ดู · **[c]** ยกเลิกลูกเดียว · **[a]** ยกเลิกทั้งหมด · **[n]** เริ่มใหม่)
- ยกเลิกกลางคัน: เมนู `[c]`/`[a]` หรือ `b2ctl maint health --cancel <bay|dev …>` /
  `b2ctl maint health --cancel-all` (abort self-test + หยุด scan ไม่มีการเขียนดิสก์)
- ระหว่างเทส `b2ctl status` โชว์ `TEST xx%` ในคอลัมน์ STATUS และคอลัมน์ **HEALTH_CHK** โชว์
  ผล long test ล่าสุด `OK`/`ERR` + อายุเป็น power-on hours (เช่น `OK 120hPOH`) — ใช้ได้กับ
  **SATA, SAS และ NVMe** (v0.18.0 แก้บั๊กที่ดิสก์ **SAS** ปกติ — ซึ่งรายงานสำเร็จเป็นคำว่า
  `Completed` เฉย ๆ ไม่ใช่ `Completed without error` แบบ ATA — ถูกตัดเป็น `ERR`/`FAIL`)
- CLI: `b2ctl maint health <bay|dev> [<bay|dev> …] [--scan] [--short]`; กลับเข้าดูด้วย
  `b2ctl maint health --status`

---

### 6.10 `u` — Udev-rescue (กู้ดิสก์ที่ OS ปฏิเสธ)

**ใช้เมื่อ:** ดิสก์เสียบอยู่จริงแต่ OS ไม่รับ (ขึ้นเป็น **GHOST** ไม่มี `/dev`) — `u` สั่ง
`udevadm trigger`/`settle` ให้ kernel มองเห็น เป็น**อ่านอย่างเดียว**/diagnostic ไม่แตะข้อมูล

```
b2ctl> u
    ghost bay 1:4 serial S74ZNS0WXXXXXXX
  run udevadm trigger/settle to rescue 1 ghost disk(s)? [y/N]> y
  ✔ rescued 1 disk(s)
```

ถ้ากู้ไม่ได้: `no disks recovered — reseat physically or wipe via [a]ssign` · ถ้าไม่มี ghost
เลย: `no ghost (OS-rejected) disks to rescue` (alias: `u` หรือ `rescue`)

---

### 6.11 `x` — Destroy pool (ลบ pool)

**ใช้เมื่อ:** ลบ ZFS pool ถาวร — **ข้อมูลหายหมด** กันด้วยยืนยันสองชั้น + พิมพ์ชื่อ pool
รายละเอียดเต็มดูหัวข้อ "ลบ ZFS pool" ท้ายคู่มือ

```
b2ctl> x
    [1] rpool (952G, ONLINE)
    [2] tank (2.72T, ONLINE)
  destroy which #> 2
  members:
    - (1:4) Samsung SSD 870 EVO 1TB (S74ZNS0WXXXXXXX)
    ...
  [!] destroying 'tank' ERASES ALL DATA on it. This cannot be undone.
  destroy pool 'tank'? [y/N]> y
  type the pool name 'tank' to confirm> tank
  ✔ pool 'tank' destroyed; timers disabled
```

> ⚠️ 2 ด่าน: กด `[y/N]` **และ** พิมพ์ชื่อ pool ให้ตรง b2ctl จะปิด maintenance timer (systemd) ของ pool นั้นให้ด้วย
> (ปุ่ม `x` เดี่ยว ไม่มี alias)

---

### 6.12 `l` — Locate (ค้นหาดิสก์ทางกายภาพ)

**ใช้เมื่อ:** ต้องการรู้ว่าดิสก์ตัวไหนอยู่ช่องไหนในเครื่อง

```
b2ctl> l
  locate which (bay/serial/sdX)> sdc
  blinking /dev/sdc for 5s ...
  ✔ done (via dd)
```

สามารถระบุได้ 3 แบบ:
- **ชื่อ bay:** `1:4`
- **ซีเรียล:** `S74ZNS0WXXXXXXX`
- **ชื่อ device:** `sdc` หรือ `/dev/sdc`

ไฟ LED ของช่องนั้นจะกะพริบ ~5 วินาที แล้วหยุดเอง

> 💡 **เคล็ดลับ:** ใช้คำสั่งนี้ก่อนดึงดิสก์ออกทุกครั้ง เพื่อให้มั่นใจว่าดึงถูกตัว!

---

### 6.13 `t` — สลับโหมด Dry-run (ทดลองโดยไม่เปลี่ยนแปลงจริง)

**ใช้เมื่อ:** ต้องการดูว่าระบบจะรันคำสั่งอะไร **โดยไม่แตะดิสก์จริงๆ** — เหมาะสำหรับ
ฝึกซ้อม ตรวจสอบ หรือเรียนรู้การทำงานของ b2ctl

```
b2ctl> t
[DRY-RUN] enabled — write commands will be printed, not executed
b2ctl> s
  swap (1:4) Samsung SSD 870 (...) onto spare (1:7)? [y/N]> y
  [DRY-RUN] would run: zpool replace tank
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W...
    /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W582283V
b2ctl> t
[DRY-RUN] disabled — back to live mode
```

ขณะ dry-run ทำงาน:
- คำสั่ง **เขียน** (`zpool`, `wipefs`, `sgdisk`, `dd`) → แสดงเท่านั้น ไม่รันจริง
- คำสั่ง **อ่าน** (`smartctl`, `zpool status`) → ยังรันตามปกติ แสดงข้อมูลจริง

สามารถเปิด dry-run ตั้งแต่ต้นได้ด้วย: `sudo b2ctl --dry-run watch`

> 💡 **เคล็ดลับ:** ใช้ dry-run ก่อนทุกครั้งที่ทำงานกับดิสก์ที่ไม่คุ้นเคย เพื่อตรวจสอบ
> ว่าคำสั่งถูกต้องก่อนยืนยัน

---

### 6.14 `q` — Quit (ออก)

```
b2ctl> q
bye
```

---

### 6.15 การเสียบ/ถอดดิสก์ขณะ watch ทำงาน (Hot-plug)

**เสียบดิสก์ใหม่:**

ระบบจะแจ้งอัตโนมัติภายใน 2-3 วินาที:

```
╔══ NEW DISK DETECTED: /dev/sdg ═══════════════════════
  device : /dev/sdg  (/dev/disk/by-id/wwn-0x500...)
  model  : Samsung SSD 870   SN S74ZNS0WXXXXXXX
  bay    : 1:3   size 1.0T   SAS   SSD
  health : PASSED   wear 0% used   endurance 100.0% left
╚════════════════════════════════════════════════════

  Disk /dev/disk/by-id/wwn-0x500... is free.
  What do you want to do with it?
    [1] Prepare for physical removal (Blink LED)
    [2] Add to a pool as hot SPARE
    ...
```

**ถอดดิสก์ออก:**

```
■ disk removed: /dev/sdc
  current pool health:
Pools:
  rpool     952G    4.83G   free=947G    ONLINE    cap=0%
  tank      2.72T   1.72G   free=2.72T   DEGRADED  cap=0%    <-- not ONLINE
```

> ⚠️ ถ้า pool กลายเป็น **DEGRADED** หลังถอดดิสก์ แปลว่าต้องเปลี่ยนดิสก์ใหม่เข้าไปโดยเร็ว

---

## 7. ระบบความปลอดภัย

b2ctl บันทึกทุก operation ที่เปลี่ยนแปลงดิสก์ และให้เครื่องมือตรวจสอบ ย้อนกลับ และ
ทดลองล่วงหน้าก่อนลงมือจริง

---

### 7.1 กล่อง Confirm รายละเอียดครบ

ก่อนทุกคำสั่งที่เปลี่ยนแปลงดิสก์ ระบบจะแสดงกล่องยืนยัน พร้อม **path เต็มของดิสก์**:

```
┌─ CONFIRM OPERATION ──────────────────────────────────────────┐
│ Op:    replace                                                 │
│ From:  bay 1:4  S74ZNS0WXXXXXXX  ONLINE  (tank/raidz1-0)     │
│ To:    bay 1:7  S8ABCXXXXXXXX    AVAILABLE                    │
│ Pool:  tank/raidz1-0                                           │
│                                                                │
│ Will run:                                                      │
│   zpool replace tank                                           │
│     /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S74ZNS0W...  │
│     /dev/disk/by-id/ata-Samsung_SSD_870_EVO_1TB_S8ABC123...  │
│                                                                │
│ Snapshot → /var/log/b2ctl/snapshots/20260617-143022-...txt   │
└────────────────────────────────────────────────────────────────┘
Proceed? [y/N]:
```

ค่าเริ่มต้นคือ **N** — กด Enter เปล่าๆ = ยกเลิกทันที ไม่มีอะไรเกิดขึ้น

---

### 7.2 Audit Trail (บันทึก operation ทุกครั้ง)

ทุกครั้งที่รัน operation ระบบจะบันทึกลง `/var/log/b2ctl/ops.jsonl` โดยอัตโนมัติ

ดูรายการที่ผ่านมาด้วย:

```bash
b2ctl log              # 20 รายการล่าสุด
b2ctl log --last 50    # 50 รายการ
```

ผลลัพธ์:

```
OP_ID                       OP        BAY  SERIAL            POOL  STATUS  STARTED
20260617-143022-replace     replace   1:4  S74ZNS0WXXXXXXX   tank  ok      2026-06-17 14:30:22
20260617-120011-add_spare   add_spare 1:7  S8ABCXXXXXXXX     tank  ok      2026-06-17 12:00:11
```

---

### 7.3 Snapshot ก่อน operation

ก่อนทุกคำสั่งที่เขียนดิสก์ ระบบจะ snapshot สถานะ pool และดิสก์ที่เกี่ยวข้องไว้ที่
`/var/log/b2ctl/snapshots/<op_id>.txt` ประกอบด้วย:
- `zpool status <pool>`
- `zpool list -v`
- `smartctl -a <dev>` ของดิสก์นั้น

ดู path ของ snapshot ได้ในกล่อง Confirm และใน `b2ctl log`

---

### 7.4 Rollback — ย้อนกลับ operation ที่ผ่านมา

หลังทุก operation ระบบจะแสดงคำสั่ง rollback:

```
✔ replace started — resilvering
  Rollback if needed: zpool replace tank /dev/disk/by-id/<new> /dev/disk/by-id/<old>
```

รัน rollback ได้ทันทีด้วย:

```bash
b2ctl rollback 20260617-143022-replace
```

ระบบจะแสดงกล่อง Confirm พร้อมคำสั่งที่จะรัน และบันทึก rollback ลง audit trail ด้วย

**Operations ที่ย้อนได้:**

| operation | ย้อนได้? |
|-----------|---------|
| offline (ปิด disk) | ✅ ใช่ |
| add spare (เพิ่ม spare) | ✅ ใช่ |
| replace (สลับดิสก์) | ✅ ได้ ถ้า resilver ยังไม่เสร็จ |
| demote (ลด mirror ลง spare) | ✅ ใช่ |
| create pool (สร้าง pool) | ⚠️ ได้ แต่ `zpool destroy` จะลบข้อมูลทั้งหมด |
| wipe (ล้างดิสก์) | ❌ ไม่ได้ — ถาวร |

---

### 7.5 Post-op Verification (ตรวจสอบหลัง operation)

หลัง operation เสร็จ ระบบจะ scan pool อีกครั้งเพื่อยืนยันว่าผลลัพธ์ถูกต้อง ถ้ามีปัญหา:

```
⚠ Post-op check FAILED: disk wwn-0x... not found in tank/raidz1-0
  Expected state not reached. See snapshot:
  /var/log/b2ctl/snapshots/20260617-143022-replace.txt
  Run: b2ctl rollback 20260617-143022-replace
```

---

## 8. ข้อควรระวัง

### ⚠️ อย่าผสม SAS กับ SATA โดยไม่ทดสอบก่อน

การเอาดิสก์ SAS มาเป็น spare ในพูลที่เป็น SATA ล้วน อาจมีปัญหาได้ ถ้าไม่แน่ใจ ให้ใช้
ดิสก์ชนิดเดียวกับที่มีอยู่ในพูล

### ⚠️ หมายเลข Bay เป็นเพียงตัวแสดง

บน Dell R620 ที่แฟลชเป็น IT mode หมายเลข slot จะสลับกัน b2ctl
แก้ไขผ่าน `bay_map.json` แต่ทุกการทำงานใช้ **serial** ของดิสก์ ไม่ใช่หมายเลข bay ดังนั้น
ถ้า bay ผิด ให้ใช้ `l` (locate) กะพริบไฟเพื่อยืนยันก่อนเสมอ

### ⚠️ ทุกคำสั่งที่เปลี่ยนแปลงข้อมูลจะถามยืนยัน

ไม่ต้องกังวลว่าจะกดผิดแล้วข้อมูลหาย — ระบบจะแสดงกล่อง Confirm พร้อม path
เต็มของดิสก์ก่อนทุกครั้ง ค่าเริ่มต้นคือ **N (No)** ดังนั้นถ้ากด Enter เปล่าๆ จะไม่มีอะไร
เกิดขึ้น

### ⚠️ ZFS resilver ใช้เวลา

การ resilver (rebuild ข้อมูล) ต้องใช้เวลาขึ้นอยู่กับปริมาณข้อมูล ห้ามปิดเครื่องหรือถอด
ดิสก์ระหว่าง resilver

---

## 9. 🚀 Cheat Sheet (สรุปคำสั่งลัด)

### คำสั่ง CLI (ใช้จาก terminal โดยตรง)

| คำสั่ง | ทำอะไร |
|--------|--------|
| `sudo b2ctl status` | ดูตารางสุขภาพดิสก์ครั้งเดียว |
| `sudo b2ctl status --locate` | ดูตาราง + กะพริบไฟดิสก์ที่มีปัญหา |
| `sudo b2ctl status --json` | แสดงผลเป็น JSON |
| `sudo b2ctl watch` | ⭐ เข้าโหมดเฝ้าดู (แนะนำ) |
| `sudo b2ctl --dry-run watch` | เข้าโหมดเฝ้าดูแบบ dry-run (ไม่เปลี่ยนแปลงจริง) |
| `sudo b2ctl locate <bay/serial/sdX>` | กะพริบไฟดิสก์ตัวนั้น |

**ใช้ไฟดวงไหน?** locate เลือกไฟที่เจาะจงสุด: disk หลัง PERC → ไฟ slot ของ
controller (`perccli`); SATA/SAS raw → ไฟ locate เฉพาะของ backplane ผ่าน `ledctl`
ถ้าลง package `ledmon` แล้ว (`apt install ledmon`), ไม่งั้น fallback เป็นไฟ
activity ผ่าน `dd`. `b2ctl locate` จะบอกว่าใช้ทางไหน (`via ledctl` / `via dd` /
`via perccli`). หมายเหตุ: ไฟ locate เป็น *กะพริบ* (SES identify) ไม่ใช่ติดนิ่ง — ไม่มี
tool ไหนทำให้ไฟ drive ที่ healthy ติดนิ่งหรือดับสนิทได้
| `sudo b2ctl maint scrub [<pool>]` | สั่ง scrub ด้วยมือ (ตรวจ checksum + self-heal); ไม่ใส่ pool = ถาม |
| `sudo b2ctl maint trim [<pool>]` | สั่ง TRIM ด้วยมือ (คืนบล็อกว่างให้ SSD) |
| `sudo b2ctl maint health <dev…> [--scan] [--short] [--status] [--cancel …\|--cancel-all]` | vet ดิสก์: long self-test (+เลือก badblocks) + PASS/WARN/FAIL (เดิมคือ `b2ctl burnin`) |
| `sudo b2ctl maint --log [--last N]` | ดูประวัติ maintenance (scrub/trim/health, ค่าเริ่มต้น 30 ล่าสุด) |
| `sudo b2ctl scrub [<pool>]` / `sudo b2ctl trim [<pool>]` | alias เดิมของ `b2ctl maint scrub` / `maint trim` |
| `sudo b2ctl log-add <pool> <dev…> [--mirror\|--raid10] [--size 32G]` | เพิ่ม SLOG; บังคับ topology + over-provision |
| `sudo b2ctl cache-add <pool> <dev…> [--size 512G]` | เพิ่ม L2ARC cache; over-provision ด้วย `--size` |
| `sudo b2ctl log` | ดู 20 operation ล่าสุดจาก audit trail |
| `sudo b2ctl log --last N` | ดู N operation ล่าสุด |
| `sudo b2ctl rollback <op_id>` | ย้อนกลับ operation ก่อนหน้า (พร้อม confirm) |
| `sudo b2ctl version` | แสดงเวอร์ชัน |
| `b2ctl install` | รายงานสถานะ tool + mode (ไม่โหลดอะไร = เหมือน `./install.sh`) |
| `sudo b2ctl install --with-tools` | ดาวน์โหลด + ติดตั้ง sas2ircu **และ** perccli จาก Google Drive |
| `sudo b2ctl install --perc` / `--flash` | ลง tool ของ backend นั้น + ตั้ง mode (raid/it) |
| `sudo b2ctl install --tool sas2ircu` | ติดตั้งเฉพาะ tool ที่ระบุ (`sas2ircu` หรือ `perccli`) |
| `b2ctl update` | ตรวจ config; **ถ้าเป็น root** จะ sync `bay_map.json` + `ssd_spec.json` ไปที่ `/etc/b2ctl/` และผูกใน config (ไฟล์ที่แก้เองไม่ถูกทับ) |
| `sudo b2ctl update --force` | เขียนทับไฟล์ `/etc/b2ctl/` ที่ผู้ใช้แก้ (สำรอง `.bak` ให้ก่อน) |
| `sudo b2ctl update --export-bay-map` | (เลิกใช้) alias ของ `--force` — ตอนนี้ `update` เฉยๆ sync ทั้งสองไฟล์แล้ว |

### คำสั่งในโหมด watch (พิมพ์ที่ `b2ctl>`)

| ปุ่ม | คำสั่ง | ทำอะไร |
|-----|--------|--------|
| `r` | refresh | รีเฟรชตาราง |
| `a` | assign | จัดสรรดิสก์ว่างเข้า pool (รวม GHOST + PERC-UG) |
| `o` | offload | ถอดดิสก์ออกจาก pool (เพื่อดึงออกทางกายภาพ) |
| `s` | swap | สลับดิสก์สึกหรอไปยัง spare (ดิสก์เก่ากลายเป็น spare) |
| `d` | demote | ลดดิสก์ mirror ลงเป็น spare |
| `t` | toggle dry-run | สลับโหมด dry-run เปิด/ปิด |
| `n` | new-pool | สร้าง pool ใหม่ (ถาม over-provision size + autotrim/autoscrub) |
| `e` | extend | เพิ่ม/ถอด/**ซ่อม** L2ARC cache หรือ SLOG log (ถาม SLOG topology + size) |
| `m` | maint | maintenance ด้วยมือ — `[1]` scrub / `[2]` trim (ต่อ pool) / `[3]` health-check = vet ดิสก์หลายลูก (long self-test + เลือก badblocks + verdict; เดิมคือ `[b]urnin`) |
| `u` | udev-rescue | กู้ดิสก์ที่ OS ปฏิเสธ (GHOST) |
| `x` | destroy-pool | ลบ pool (ยืนยันสองชั้น + พิมพ์ชื่อ pool) |
| `l` | locate | กะพริบไฟ LED หาดิสก์ (~5 วินาที) |
| `q` | quit | ออก |

### ตัวเลือกเมื่อมีดิสก์ว่าง (assign / new disk detected)

| ปุ่ม | ทำอะไร |
|-----|--------|
| `1` | กะพริบ LED (เตรียมดึงออก) |
| `2` | เพิ่มเป็น spare ใน pool |
| `3` | แทนที่ดิสก์ที่เสีย (REPLACE) |
| `4` | ต่อเป็น mirror กับดิสก์ที่มี (ATTACH) |
| `5` | เพิ่มเข้า pool แบบเดี่ยว (ไม่มี redundancy) |
| `6` | ล้างข้อมูลทั้งหมด (WIPE) |
| `s` | ข้ามไว้ก่อน |

---

> 💡 Tip: **มีปัญหา?** ถ้าไม่แน่ใจว่าจะทำอะไร ให้กด `s` (skip) ไว้ก่อนเสมอ — ดิสก์จะไม่ถูก
> เปลี่ยนแปลง แล้วค่อยกลับมาจัดการทีหลังด้วย `a` (assign)

---

## เครื่องที่ใช้ RAID จริง (Dell PERC เช่น R640 / H730P)

b2ctl รองรับเครื่องที่ PERC ทำ **hardware RAID** (ไม่ได้ crossflash) ด้วย ติดตั้งแบบ
RAID แล้วมันจะสลับเป็นโหมด RAID เอง:

```
b2ctl install --perc      # ลง perccli + ตั้ง controller.mode=raid
b2ctl install --flash     # เครื่อง IT/HBA: ลง sas2ircu + mode=it
```

`b2ctl status` จะโชว์ **ดิสก์จริงที่อยู่หลัง RAID volume** (อ่านผ่าน controller) คอลัมน์
`POOL/ARRAY` บอกชนิด:

- `HW:vd0/raid1` — เป็นสมาชิกของ **hardware** RAID (PERC คุม)
- `SW:tank/raidz1-0` — เป็นสมาชิก **software** RAID (ZFS)
- `-` — ดิสก์เดี่ยว/ยังไม่ได้ assign (เช่น NVMe, JBOD)

เครื่องที่มี **ทั้งสองแบบ** ตาราง disk จะแบ่งกลุ่ม — บล็อก
`--- Hardware (PERC RAID) ---` อยู่บน, `--- Software (ZFS) ---` อยู่ล่าง — และ
summary รวมเป็นตาราง **Storage summary** เดียว (hardware บน / software ล่าง):

```
Storage summary:
  TYPE NAME            LEVEL    STATE     SIZE      USED      FREE
  HW   MainSSD         raid1    Optl      640.0 GB  12.0G     628.0G
  SW   tank            mirror   ONLINE    928G      598M      927G
```

- **NAME** — ชื่อ volume ของ hardware (เช่น `MainSSD`) / ชื่อ pool ของ software
- **USED/FREE** — software เอาจาก pool; hardware อ่านจาก **filesystem ที่ mount**
  ของ volume ผ่าน `lsblk` ถ้า volume เป็น raw/ไม่ได้ mount จะขึ้น `-` (ไม่มี FS ให้วัด)

### เปลี่ยนดิสก์ RAID ที่เสีย

```
b2ctl raid-replace          # เลือกตัว หรือระบุ: b2ctl raid-replace 32:0
```

มันจะ fail ดิสก์ออก, **เปิดไฟ LED ช่องนั้น**, รอให้ถอดของเก่าใส่ของใหม่, แล้วเฝ้าดู
controller **rebuild** พร้อมแถบความคืบหน้า คำสั่งอื่น: `raid-offline <bay>`,
`locate <bay|serial|dev> [secs]` (ไฟกะพริบตามเวลาที่กำหนดแล้วดับเสมอ — ไม่มีโหมด
ค้างไฟ `on`/`off` โดยตั้งใจ), และ (อันตราย ยืนยันสองครั้ง) `raid-create` / `raid-del`

> หมายเหตุ: การ์ด NVMe 2×M.2 ถ้าโชว์แค่ตัวเดียว ต้องเปิด **PCIe bifurcation (x4x4)**
> ใน BIOS — เป็นเรื่องฮาร์ดแวร์ ไม่ใช่ b2ctl

---

## สร้าง ZFS pool (`[n]ew-pool`)

หลังเลือกดิสก์ b2ctl จะถามขนาด over-provision (**size**) ก่อน (Enter = ทั้งลูก, ใส่ขนาด
เช่น `32G` จะแบ่งพาร์ทิชันแต่ละดิสก์เหลือ spare area ของ SSD — ดู `[n]` ใน §6) จากนั้น
หลังตั้งชื่อ/เลือก raid level จะถามค่า property ทีละตัว โดยมีค่า default ที่เหมาะกับ SSD
อยู่แล้ว — **กด Enter เพื่อใช้ค่า default** หรือพิมพ์เพื่อเปลี่ยน (`ashift`, `compression`,
`atime`, `xattr`, `dnodesize`, `acltype`, `recordsize`) `recordsize` ปรับตาม workload
ได้ (ทั่วไป 128K, DB 16K, media 1M, VM 64–128K) และเปลี่ยนภายหลังราย dataset ได้

**autotrim** และ **autoscrub** เป็นสองตัวเลือกแยกกัน ทั้งคู่ตอนนี้เป็น `[1] off` (ค่าเริ่มต้น) `/
[2] on` และ **OFF = ทำเองอย่างเดียว ไม่ติดตั้ง timer** (v0.18.0):
- **autoscrub** — *ค่าเริ่มต้น **off***. `[2] on` เปิด `zfs-scrub-monthly@<pool>.timer` ราย
  เดือน (systemd timer ของ distro มากับ `zfsutils-linux`). เมื่อ **off** pool จะ **ไม่มี scrub
  ตามกำหนด** — การ scrub ด้วยมือคือแนวทางหลัก (`[m]aint` / `b2ctl maint scrub`) b2ctl จะ
  เตือนและคอลัมน์ SCRUB จะโชว์ว่า scrub ล่าสุดเก่าแค่ไหน *(นี่กลับทิศจากเดิมที่ scrub รันเสมอ —
  ดู ADR-003/004 pool ที่ไม่เคย scrub อาจสะสม bitrot เงียบ ๆ ควร scrub สม่ำเสมอหรือเปิด
  autoscrub)*
- **autotrim** — *ค่าเริ่มต้น off* = **TRIM ด้วยมือเท่านั้น ไม่มี trim timer** (`b2ctl maint
  trim` / `[m]aint`); เลือก `on` = ตั้ง `zpool autotrim=on` ให้ ZFS trim ต่อเนื่องเอง
  *(v0.18.0 กลับทิศจากเดิมที่ `autotrim off` เคยตั้ง `zfs-trim-monthly@<pool>.timer` — ตอนนี้ไม่มี
  trim timer แล้ว, ดู ADR-004)*

เช็คได้: `systemctl list-timers | grep zfs`. ถ้า distro ไม่มี timer unit b2ctl จะเตือน
"scrub timer NOT scheduled" แล้วไม่ติดตั้งอะไร (ลง `zfsutils-linux` หรือเปิด timer เอง)
คำตอบ autotrim/autoscrub ล่าสุดจะถูกจำไว้เป็นค่าเริ่มต้นของ `create` ครั้งถัดไป

Debian/Proxmox มี cron ในตัวที่ scrub *ทุก* pool รายเดือนอยู่แล้ว (property
`org.debian:periodic-scrub`). กัน scrub ซ้ำ: ตอน b2ctl เปิด scrub timer ของ pool (autoscrub
on) มันจะ set `org.debian:periodic-scrub=disable` ให้ pool นั้น → per-pool timer เป็น
schedule เดียว ไม่ซ้ำกับ cron ของ distro

**raid10** = stripe ของ mirror (เร็ว/ resilver ไว / random IOPS ดีสุด): เลือกดิสก์
**จำนวนคู่ (even, ≥4)** b2ctl จะจับคู่ให้เอง (`mirror d1 d2 mirror d3 d4 …`) และโชว์คู่
ก่อนยืนยัน — จาก CLI ใช้ `b2ctl create --raid10`

## เพิ่ม cache / log ให้ pool (`[e]xtend`)

เร่งความเร็ว pool เดิมตาม runbook ของเครื่อง storage:

- **L2ARC cache** — read-cache บน SSD/NVMe เร็ว ๆ พังแล้วแค่ cache miss (**mirror ไม่ได้**
  จึงไม่มี prompt topology) ช่วยเฉพาะตอน working set ใหญ่กว่า RAM. CLI:
  `b2ctl cache-add|cache-rm <pool> <dev…>`
- **SLOG log** — เร่ง write แบบ **sync** (เช่น NFS `sync`). เมื่อเลือก **≥2 ลูก** b2ctl ถาม
  **topology (v0.17.0)**: `mirror` (redundant, แนะนำ), `raid10` (stripe of mirrors, คู่ ≥4),
  หรือ `single/striped` (ไม่มี redundancy). log vdev **เป็น raidz ไม่ได้** (กฎ ZFS). log ลูก
  เดียวเสีย = เสีย write ที่ค้าง; ต้องเป็น SSD ที่มี **PLP**. CLI: `b2ctl log-add|log-rm
  <pool> <dev…>` (`--mirror`/`--raid10` บังคับ topology)
- **Over-provision:** ทั้ง cache/log ถาม `size to use per device` (Enter = ทั้งลูก,
  ใส่ขนาดเช่น `32G` = แบ่งพาร์ทิชันแล้วส่งพาร์ทิชันให้ ZFS เหลือ spare area ช่วยอายุ SSD).
  **การใส่ขนาดจะ WIPE แต่ละดิสก์ก่อน (v0.18.0)** — เคลียร์ GPT ก่อนแบ่งพาร์ทิชัน มีคำเตือน
  WIPE + ยืนยัน **ครั้งเดียวก่อนแตะดิสก์ (ตอบ N = ไม่ wipe อะไร)**. CLI: `--size 32G` บน
  `cache-add`/`log-add` (คอนเฟิร์มเหมือนกัน)
- เลือก `[3]` เพื่อถอด cache/log ออก (`zpool remove`)
- เลือก `[4]` เพื่อ **ซ่อม cache/log ที่ degraded** (v0.14.0): cache → `remove`+`add`;
  SLOG mirror-leg → `zpool replace` (resilver สั้น มี progress); SLOG ลูกเดียวหายไป →
  `remove`+`add`. CLI: `b2ctl cache-replace|log-replace <pool> <old> <new>`
  (ลูกใหม่ต้อง resolve เป็น by-id ตาม §9; `old` รับ raw leaf token ได้)

## Health-check ดิสก์ก่อนเข้าใช้ (`[m]aint → [3]`, คือ burn-in เดิม)

> **v0.18.0:** ปุ่ม `[b]urnin` และคำสั่ง `b2ctl burnin` **ถูกรวมเข้า** `[m]aint → [3]
> health-check` / `b2ctl maint health` แล้ว — maintenance surface เดียว

ก่อนเชื่อดิสก์ใหม่/มือสอง รัน SMART long self-test (เลือกสแกนผิวอ่านทั้งลูกได้) แล้วได้ผล
**PASS / WARN / FAIL** — ทำ**หลายลูกพร้อมกัน**ได้

**เลือกหลายลูก + รันเบื้องหลัง:** เลือกดิสก์แบบเดียวกับ `[n]ew-pool` (คั่นด้วยเว้นวรรค)
ยืนยัน แล้วเลือกว่าจะสแกนผิว (`badblocks`) ด้วยไหม self-test รันบนเฟิร์มแวร์ของดิสก์เอง
ส่วน scan รันเป็น process เบื้องหลัง จึงมี**หน้าจอสด**โชว์แถบความคืบหน้า + เวลาที่เหลือของ
แต่ละลูก และ**ออกทิ้งให้รันต่อได้** (กด Ctrl-C)

```
b2ctl> m
  action> 3
    [1] /dev/sdb (bay 32:4) Samsung SSD 870 EVO 1TB
    [2] /dev/sda (bay 32:5) Samsung SSD 870 EVO 1TB
  health-check which #> (space-separated) 1 2
  health-check 2 disk(s) (long self-test)? [y/N]> y
  also run a full read-surface scan (badblocks, read-only, hours)? [y/N]> y

 BAY     DISK      SELF-TEST                     SURFACE SCAN (badblocks)
 32:4    sdb       [########------]  62%  ~1h10m  [###-----------]  18%  ~4h30m
 32:5    sda       [##########----]  74%  ~40m    [####----------]  22%  ~4h05m
```

- **เลือกดิสก์ไหน — เฉพาะดิสก์ว่าง/spare ทั้งสองทาง:** health-check vet เฉพาะดิสก์ **ว่างหรือ
  spare** · ในโหมด watch ลิสต์เฉพาะพวกนั้น และ `b2ctl maint health <dev…>` จะ **ปฏิเสธ**
  ลูกที่อยู่ใน pool (`… is in pool '<pool>' — self-test it with \`smartctl -t long\` directly`) ·
  จะ self-test **ลูกที่ใช้งานอยู่ใน pool** ให้รัน `smartctl -t long <by-id>` เองใน shell (b2ctl ไม่
  สั่งให้) แต่คอลัมน์ HEALTH_CHK ของลูกนั้นยังอัปเดตแบบ passive จาก `smartctl -a` ตอน refresh
- **ออก & กลับเข้ามาดูใหม่:** กด **Ctrl-C** กลับไปที่ prompt — test/scan ยังรันต่อ กด `[m]`
  → `[3]` อีกครั้งจะมีเมนู — **[v]** ดูหน้าจอสด · **[c]** ยกเลิกทีละลูก · **[a]** ยกเลิกทั้งหมด · **[n]**
  เริ่มใหม่ (หรือ `b2ctl maint health --status`) พอลูกไหนเสร็จจะเห็นผลตรงนั้น
- **ยกเลิก:** หยุดกลางคัน (เช่นดิสก์กำลังตายค้างทั้ง batch) — เมนู `[c]`/`[a]` หรือ
  `b2ctl maint health --cancel <bay|dev …>` / `b2ctl maint health --cancel-all`. มัน abort
  self-test + หยุด scan (read-only ไม่เขียนอะไร) — ทำใหม่ทีหลังได้
- ระหว่าง self-test `b2ctl status` จะโชว์ `TEST xx%` ในคอลัมน์ STATUS ของดิสก์ลูกนั้น และ
  คอลัมน์ **HEALTH_CHK** โชว์ `OK`/`ERR` + อายุ `hPOH` ได้ทั้ง **SATA, SAS, NVMe** (v0.18.0
  แก้บั๊ก SAS `Completed`)
- **PASS** สะอาด · **WARN** ใช้ได้แต่เก่า (POH > 40000 ชม., grown defect, หรือ scan เจอ bad
  block) → จัด priority ต่ำ · **FAIL** มี uncorrected error หรือ self-test ไม่ผ่าน → อย่าเข้า pool
- **อ่านอย่างเดียว**: ทำแค่สั่ง self-test และ (ถ้าเลือก) `badblocks` แบบ read-only — ไม่
  เขียนทับดิสก์. CLI: `b2ctl maint health <bay|dev> [<bay|dev> …] [--scan] [--short]`,
  กลับเข้าดูด้วย `b2ctl maint health --status`

## ลบ ZFS pool (`[x]` หรือ `b2ctl destroy <pool>`)

ลบ pool ด้วย `zpool destroy` — **ข้อมูลหายทั้งหมด** ต้องยืนยันและ**พิมพ์ชื่อ pool**
เพื่อดำเนินการ b2ctl จะปิด maintenance timer ของ pool นั้นให้ด้วย (ถ้าลบ pool เองด้วย
`zpool destroy` b2ctl จะปิด timer ที่ค้างให้ตอนเปิด `b2ctl watch` ครั้งถัดไป)

## เปลี่ยนดิสก์ที่กำลังจะเสีย ตอนไม่มี spare (`[o]ffload`)

raidz1 (และ mirror) ยังทำงานได้แม้ดิสก์หายไป 1 ลูก ถ้าดิสก์กำลังจะเสีย บายเต็มหมด และ
**ไม่มี hot spare** ให้ `[o]ffload` ตัวนั้น:

1. b2ctl เช็คก่อนว่า pool **redundant เต็มอยู่ตอนนี้** (ลูกอื่นปกติหมด) ถ้าไม่ → **ปฏิเสธ**
   เพราะ offline ลูกที่สองอาจทำ pool ล่ม
2. รัน `zpool offline` — pool จะเป็น **DEGRADED แต่ยังออนไลน์** (ไม่มี redundancy จนกว่าจะ
   เสร็จ) และเปิดไฟ LED ช่องนั้น
3. **ถอดดิสก์ช่องนั้น แล้วใส่ดิสก์ใหม่ในช่องเดิม** จากนั้นกด Enter
4. b2ctl จะ `zpool replace` ดิสก์ใหม่เข้าไป + โชว์ความคืบหน้า resilver พอเสร็จ pool กลับมา
   **ONLINE**

> ⚠️ ระหว่าง DEGRADED / resilver ไม่มี redundancy — ถ้ามีดิสก์ลูกที่สองเสียในช่วงนี้ข้อมูลหาย
> b2ctl จะไม่ยอมให้ offline ลูกที่สองระหว่างนี้

## ป้ายชื่อ bay — `bay_map.json`

`/etc/b2ctl/bay_map.json` เป็น list ของ **panel** ที่อธิบาย chassis:

- **front** (`type: sas`) — backplane หลัง PERC (RAID) หรือ PERC ที่ flash เป็น
  `sas2ircu` บายเป็น `enc:slot` ถ้า controller รายงาน slot สลับ ให้ตั้ง
  `reverse_slots`+`slots_per_enclosure` หรือ `map` ตรง ๆ (`{"32:0": "32:7"}`)
  เทียบตำแหน่งด้วย `b2ctl locate <serial>`
- **back** (`type: nvme`) — กล่อง PCIe/M.2 SSD (มีได้หลายอัน) NVMe ไม่มี enc:slot
  เลยโชว์ **PCIe address** (เช่น `d8:00.0`) จนกว่าจะ relabel แต่ละ entry ใน `map`
  match ได้ด้วย key 3 แบบ (**ลำดับความสำคัญ by-id > serial > bdf**):

```json
{ "panel": "back", "type": "nvme",
  "map": [ {"by-id":  "nvme-Samsung_SSD_990_EVO_Plus_4TB_S7..", "bay": "PCIe2:0"},
           {"serial": "S7XXNS0W123", "bay": "PCIe2:1"},
           {"bdf":    "d8:00.0",     "bay": "PCIe2:2"} ] }
```

- **`serial`** ง่ายสุด — copy จากคอลัมน์ **SERIAL** ใน `b2ctl status` ได้เลย
- **`by-id`** เป็น substring ของลิงก์ `/dev/disk/by-id/nvme-<model>_<serial>`
  (`ls /dev/disk/by-id/ | grep nvme`) ไม่เปลี่ยนแม้ย้าย slot การ์ด
- **`bdf`** ยังใช้ได้ — หาได้จาก `b2ctl status` (คอลัมน์ BAY) หรือ
  `cat /sys/class/nvme/nvme0/address`

> NVMe ขึ้นในตารางและใช้ `[a]ssign` / `[m]aint` health-check ได้เหมือน disk อื่นทุกอย่าง —
> ต่างแค่คอลัมน์ BAY (ไม่มี enc:slot) ป้าย bay เป็นแค่ชื่อแสดงผล ตั้งผิดก็ไม่อันตราย

### ให้ป้าย bay ใช้ได้จากทุก directory

แก้ bay_map ที่ **copy ใน `/etc/b2ctl/`** — ไม่ใช่ตัวใน source checkout สร้าง/รีเฟรช
ด้วย **`b2ctl update`** (เป็น root):

```bash
sudo b2ctl update          # สร้าง /etc/b2ctl/bay_map.json + ssd_spec.json + ผูกใน config
sudo nano /etc/b2ctl/bay_map.json   # ใส่ entry serial NVMe -> bay
b2ctl watch                # map ถูกต้องจากทุก directory แล้ว
```

`b2ctl update` คัดลอก `bay_map.json` และ `ssd_spec.json` (ตาราง TBW ของ SSD) ที่
bundle มา ไปที่ `/etc/b2ctl/` และบันทึก path ลง config ดังนั้น b2ctl โหลดไฟล์เดียวกัน
เสมอไม่ว่ารันจาก directory ไหน และ **จะไม่ทับไฟล์ที่คุณแก้เอง** — ไฟล์ที่แก้แล้วจะขึ้น
`customized-kept` (ถ้าอยากทับใช้ `sudo b2ctl update --force` ซึ่งสำรอง `.bak` ให้ก่อน)

> **ทำไมสำคัญ:** ก่อน v0.8.5 การรัน `b2ctl` จากใน source checkout อาจโหลด
> `bay_map.json` ของ copy นั้นแทนตัวที่ติดตั้งไว้ ทำให้ mapping เหมือนเปลี่ยนตาม
> directory ปัจจุบัน ตอนนี้ launcher รัน copy ที่ติดตั้งเสมอ (`PYTHONSAFEPATH`) และ
> `b2ctl update` วางไฟล์ที่แก้ได้ไว้ที่เดียว (`/etc/b2ctl/`)
