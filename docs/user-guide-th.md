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

| โปรแกรม | ทำหน้าที่ | ต้องมีไหม? |
|---------|----------|-----------|
| `smartctl` (smartmontools) | อ่านข้อมูลสุขภาพดิสก์ | ✅ จำเป็นต้องมี |
| `zpool` (zfsutils-linux) | จัดการ ZFS pool | ✅ จำเป็นต้องมี |
| `sas2ircu` | บอกหมายเลขช่อง (bay) | 💡 ทางเลือก (ไม่มีก็ได้ แต่จะไม่เห็นเลข bay) |

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

[r]efresh  [a]ssign  [o]ffload  [s]wap  [d]emote  [n]ew-pool  [e]xtend  [b]urnin  [t]oggle-dryrun  [l]ocate  [q]uit   (or hot-plug)
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
| **BAD** | จำนวน bad sectors | `0` = ปกติ, `มากกว่า 0` = อันตราย! |
| **HEALTH** | ผลตรวจ SMART | `PASSED`, `FAILED` |
| **POOL** | อยู่ใน pool / vdev ไหน | `tank/raidz1-0`, `rpool/mirror-0` |
| **STATUS** | สถานะ vdev ของ ZFS — สีเขียว ONLINE/AVAIL, สีเหลือง DEGRADED/INUSE→bay, สีแดง FAULTED/REMOVED | `ONLINE`, `AVAIL`, `INUSE→1:4` |
| **LEVEL** | ระดับสถานะรวม | ดูตารางด้านล่าง |

### ความหมายของ LEVEL:

| สี | ระดับ | ความหมาย |
|----|------|----------|
| 🟢 | **NORMAL** | ดิสก์แข็งแรง อยู่ในพูลเรียบร้อย — ไม่ต้องทำอะไร |
| 🔵 | **CONFIG** | ดิสก์แข็งแรงแต่ **ยังไม่ได้อยู่ในพูลไหนเลย** — ต้องตั้งค่า (เพิ่มเป็น spare หรือสร้าง pool) |
| 🟡 | **WARNING** | เริ่มมีปัญหา — อายุเหลือน้อย หรือ vdev สถานะ DEGRADED — ควรเตรียมเปลี่ยน |
| 🔴 | **CRITICAL** | อันตราย! — SMART ไม่ผ่าน, มี bad sectors, หรืออายุเหลือน้อยมาก — ต้องดำเนินการทันที |

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
  assign which #>
```

พิมพ์หมายเลขของดิสก์ที่ต้องการ ระบบจะถามว่าจะทำอะไรกับมัน:

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
  pool name> backup
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]> raidz1
  create pool 'backup' (raidz1) with 3 disks? [y/N]> y
  ✔ pool created
```

**ประเภท RAID ที่เลือกได้:**

| ประเภท | ดิสก์ขั้นต่ำ | ความปลอดภัย | เนื้อที่ได้ |
|--------|------------|------------|-----------|
| **stripe** | 1 | ไม่มี — เสีย 1 ตัว = ข้อมูลหายหมด | เต็ม 100% |
| **mirror** | 2 | ทนเสียได้ 1 ตัว | 50% |
| **raidz1** | 2 (แนะนำ 3+) | ทนเสียได้ 1 ตัว | (N-1)/N |
| **raidz2** | 4 | ทนเสียได้ 2 ตัว | (N-2)/N |

> ⚠️ ถ้าดิสก์ที่เลือกมีข้อมูลเก่าอยู่ ระบบจะเตือนและถามว่าจะ wipe ก่อนหรือไม่

---

### 6.7 `l` — Locate (ค้นหาดิสก์ทางกายภาพ)

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

### 6.8 `t` — สลับโหมด Dry-run (ทดลองโดยไม่เปลี่ยนแปลงจริง)

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

### 6.9 `q` — Quit (ออก)

```
b2ctl> q
bye
```

---

### 6.10 การเสียบ/ถอดดิสก์ขณะ watch ทำงาน (Hot-plug)

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
| `a` | assign | จัดสรรดิสก์ว่างเข้า pool |
| `o` | offload | ถอดดิสก์ออกจาก pool (เพื่อดึงออกทางกายภาพ) |
| `s` | swap | สลับดิสก์สึกหรอไปยัง spare (ดิสก์เก่ากลายเป็น spare) |
| `d` | demote | ลดดิสก์ mirror ลงเป็น spare |
| `n` | new-pool | สร้าง pool ใหม่ |
| `t` | toggle dry-run | สลับโหมด dry-run เปิด/ปิด |
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
`locate <bay> on`, และ (อันตราย ยืนยันสองครั้ง) `raid-create` / `raid-del`

> หมายเหตุ: การ์ด NVMe 2×M.2 ถ้าโชว์แค่ตัวเดียว ต้องเปิด **PCIe bifurcation (x4x4)**
> ใน BIOS — เป็นเรื่องฮาร์ดแวร์ ไม่ใช่ b2ctl

---

## สร้าง ZFS pool (`[n]ew-pool`)

หลังเลือกดิสก์/ตั้งชื่อ/เลือก raid level แล้ว b2ctl จะถามค่า property ทีละตัว โดยมีค่า
default ที่เหมาะกับ SSD อยู่แล้ว — **กด Enter เพื่อใช้ค่า default** หรือพิมพ์เพื่อเปลี่ยน
(`ashift`, `compression`, `atime`, `xattr`, `dnodesize`, `acltype`, `recordsize`)
`recordsize` ปรับตาม workload ได้ (ทั่วไป 128K, DB 16K, media 1M, VM 64–128K) และ
เปลี่ยนภายหลังราย dataset ได้

**autotrim** เป็นตัวเลือก:
- **off (Monthly)** *(แนะนำ)* — ติดตั้ง schedule รายเดือนให้ pool: `zpool trim`
  อาทิตย์แรก + `zpool scrub` อาทิตย์ที่สอง (cron ที่ `/etc/cron.d/b2ctl-<pool>`)
- **on** — trim ต่อเนื่องโดย ZFS เอง; ไม่สร้าง cron

**raid10** = stripe ของ mirror (เร็ว/ resilver ไว / random IOPS ดีสุด): เลือกดิสก์
**จำนวนคู่ (even, ≥4)** b2ctl จะจับคู่ให้เอง (`mirror d1 d2 mirror d3 d4 …`) และโชว์คู่
ก่อนยืนยัน — จาก CLI ใช้ `b2ctl create --raid10`

## เพิ่ม cache / log ให้ pool (`[e]xtend`)

เร่งความเร็ว pool เดิมตาม runbook ของเครื่อง storage:

- **L2ARC cache** — read-cache บน SSD/NVMe เร็ว ๆ พังแล้วแค่ cache miss (ไม่ mirror)
  ช่วยเฉพาะตอน working set ใหญ่กว่า RAM. CLI: `b2ctl cache-add|cache-rm <pool> <dev…>`
- **SLOG log** — เร่ง write แบบ **sync** (เช่น NFS `sync`) เลือก **2 ลูกเพื่อ mirror**
  (log ลูกเดียวเสีย = เสีย write ที่ค้างอยู่) และต้องเป็น SSD ที่มี **PLP**; b2ctl เตือน
  ถ้าเลือกลูกเดียว. CLI: `b2ctl log-add|log-rm <pool> <dev…>`
- เลือก `[3]` เพื่อถอด cache/log ออก (`zpool remove`)

## Burn-in ดิสก์ก่อนเข้าใช้ (`[b]urnin`)

ก่อนเชื่อดิสก์ใหม่/มือสอง รัน SMART long self-test (เลือกสแกนผิวอ่านทั้งลูกได้) แล้วได้ผล
**PASS / WARN / FAIL**:

- **PASS** สะอาด · **WARN** ใช้ได้แต่เก่า (POH > 40000 ชม. หรือมี grown defect) → จัด
  priority ต่ำ อย่าจับคู่ mirror กับดิสก์เก่าด้วยกัน · **FAIL** มี uncorrected error หรือ
  self-test ไม่ผ่าน → อย่าเอาเข้า pool
- **อ่านอย่างเดียว**: ทำแค่สั่ง self-test และ (ถ้าเลือก) `badblocks` แบบ read-only — ไม่
  เขียนทับดิสก์. CLI: `b2ctl burnin <bay|dev> [--scan] [--short]`

## ลบ ZFS pool (`[x]` หรือ `b2ctl destroy <pool>`)

ลบ pool ด้วย `zpool destroy` — **ข้อมูลหายทั้งหมด** ต้องยืนยันและ**พิมพ์ชื่อ pool**
เพื่อดำเนินการ b2ctl จะลบ cron ของ pool นั้นให้ด้วย (ถ้าลบ pool เองด้วย `zpool destroy`
b2ctl จะเก็บกวาด cron ที่ค้างให้ตอนเปิด `b2ctl watch` ครั้งถัดไป)

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

> NVMe ขึ้นในตารางและใช้ `[a]ssign` / `[b]urnin` ได้เหมือน disk อื่นทุกอย่าง —
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
