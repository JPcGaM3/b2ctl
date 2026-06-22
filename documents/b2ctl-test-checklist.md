# b2ctl — Test Checklist / Test Report

> **Version:** `0.5.0-itmode`  ·  **Build:** IT-mode / LSI SAS2308 (PERC H710 crossflashed)
> **ผู้ทดสอบ:** ______________  ·  **วันที่:** ____ / ____ / ______  ·  **เครื่อง (hostname):** ______________

เอกสารนี้เป็น **checklist** สำหรับไล่เทส b2ctl ทุก scenario แล้วใช้เป็น test report ส่งต่อได้
ภาษา: เนื้อหาไทย, commands/technical terms อังกฤษ.

---

## วิธีใช้

1. ทำ **Pre-flight** ก่อน เก็บ baseline ไว้เทียบ
2. ไล่เทสทีละ section (A → E) ตามลำดับความเสี่ยง
3. กรอก 3 ช่อง: **Status** (`✅`/`❌`/`⏭`) · **Actual** (สิ่งที่เห็นจริง) · **Comment** (ถ้า `❌` อธิบายว่าต่างจาก Expected ยังไง)
4. ถ้า `❌` → ส่งกลับมาให้ทีม dev แก้ code รอบถัดไป

### Legend (Status)

| สัญลักษณ์ | ความหมาย |
|---------|----------|
| `☐` | ยังไม่เทส |
| `✅` | PASS — ตรง Expected |
| `❌` | FAIL — ไม่ตรง Expected (กรอก Comment ด้วย) |
| `⏭` | SKIP — ข้าม (กรอกเหตุผลใน Comment) |

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

| ตรวจ | Expected | Status | Actual |
|------|----------|:------:|--------|
| `b2ctl version` | `b2ctl 0.5.0-itmode` | ☐ | |
| `b2ctl check` รันได้ ไม่ crash | แสดง root, tools, backend mode, config path | ☐ | |
| `/tmp/before.json` valid JSON | `python3 -m json.tool /tmp/before.json` ผ่าน | ☐ | |
| baseline zpool เก็บแล้ว | ไฟล์ `/tmp/zpool-before.txt` มีเนื้อหา | ☐ | |

---

## Section A — Safe / Read-only

> รันผ่าน SSH ได้ ไม่กระทบข้อมูล. รันได้เลยไม่ต้องอยู่หน้าเครื่อง.

| ID | Scenario | Command | Expected (exact) | Status | Actual | Comment |
|----|----------|---------|------------------|:------:|--------|---------|
| A1 | ดูสถานะดิสก์ | `b2ctl status` | ตารางครบ: BAY/DEV/IF/MODEL/SERIAL/...POOL/STATUS/LEVEL; pools summary ด้านล่าง | ☐ | | |
| A2 | สถานะ JSON | `b2ctl status --json` | JSON array ของ disk objects, valid (เช็คด้วย `... \| python3 -m json.tool`) | ☐ | | |
| A3 | MODEL เต็ม (TBW fix) | `b2ctl status` | MODEL แสดงเต็ม เช่น `Samsung SSD 860 PRO 1TB` (ไม่ตัดเป็น `Samsung SSD 860`); WRITTEN แสดง `xx.xxTB/1200TBW` ไม่ใช่ `/?` | ☐ | | |
| A4 | BAY column (libc6 fix) | `b2ctl status` | BAY แสดงเลข enclosure:slot (เช่น `1:0`) ไม่ใช่ `-` ทั้งหมด (ต้องมี sas2ircu ทำงาน) | ☐ | | |
| A5 | Check environment | `b2ctl check` | `[✔] sas2ircu`, backend = `IT-mode`, "Controllers found: N (M disks in bay map)" M>0 | ☐ | | |
| A6 | Update / validate config | `b2ctl update` | แต่ละ tool ขึ้น `[✔]`; sas2ircu ไม่ขึ้น "found but won't execute" | ☐ | | |
| A7 | Config show | `b2ctl config show` | พิมพ์ JSON config (tool_paths, controller, bay_map_path) | ☐ | | |
| A8 | Operation log | `b2ctl log` หรือ `b2ctl log --last 5` | ตาราง history (หรือ "No operations logged yet" ถ้ายังไม่เคยทำ) | ☐ | | |
| A9 | Locate LED (read-only นับว่าปลอดภัยถ้าดิสก์ไม่ resilver) | `b2ctl locate <bay> 3` เช่น `b2ctl locate 1:0 3` | ไฟกระพริบที่ bay ถูกตัว 3 วินาที | ☐ | | |

---

## Section B — Dry-run mutating

> ใช้ `--dry-run` → b2ctl **print คำสั่งที่จะรัน แต่ไม่ทำจริง**. รันผ่าน SSH ได้ ไม่ต้องดึงดิสก์.
> เทียบ `zpool status tank` ก่อน-หลัง ต้อง **ไม่เปลี่ยน**.

| ID | Scenario | Command | Expected | Status | Actual | Comment |
|----|----------|---------|----------|:------:|--------|---------|
| B1 | Swap (dry) | `b2ctl --dry-run swap` → เลือกดิสก์/spare → y | print `zpool replace ...` (dry-run), pool ไม่เปลี่ยน | ☐ | | |
| B2 | Replace (dry) | `b2ctl --dry-run replace` → เลือก → y | print `zpool replace -f tank ...` (dry-run), ไม่ resilver จริง | ☐ | | |
| B3 | Demote (dry) | `b2ctl --dry-run demote` → เลือก mirror leg → y | print `zpool detach ...` + `zpool add ... spare` (dry-run) | ☐ | | |
| B4 | Create pool (dry) | `b2ctl --dry-run create` → เลือกดิสก์ว่าง → y | print `zpool create -f -o ashift=12 ...` (dry-run), ไม่สร้างจริง | ☐ | | |
| B5 | Offload (dry) | `b2ctl --dry-run offload` → เลือก → y | print คำสั่ง detach/replace (dry-run) ตาม vdev type | ☐ | | |
| B6 | Watch dry-run toggle | `b2ctl watch` → กด `t` | แสดง `[DRY-RUN MODE: ON]`; กด `t` อีกครั้ง → `OFF` | ☐ | | |
| B7 | Watch menu ครบ | `b2ctl watch` | เมนูแสดง `[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [l]ocate [q]uit` | ☐ | | |

---

## Section C — Physical hotplug (ต้องอยู่หน้าเครื่อง)

> ต้องดึง/เสียบดิสก์จริง. ทำบน **tank** เท่านั้น. เปิด `b2ctl watch` ค้างไว้ระหว่างเทส.
> 💡 ลำดับแนะนำ: C1 → C2 (รอ resilver เสร็จ) → C3 → C4 → … แล้วคืนสภาพ

| ID | Scenario | Steps | Expected | Status | Actual | Comment |
|----|----------|-------|----------|:------:|--------|---------|
| C1 | Hot-remove | เปิด `watch` → ดึงดิสก์ tank 1 ตัวออก | watch แจ้ง disk removed; `zpool status tank` = `DEGRADED` | ☐ | | |
| C2 | Spare auto-replace | (มี spare AVAIL อยู่) หลัง C1 | ZFS auto-resilver onto spare; watch/`zpool status` เห็น `replacing` + spare เข้าแทน | ☐ | | |
| C3 | Hot-insert | เสียบดิสก์กลับ bay เดิม | watch แสดง `╔══ NEW DISK DETECTED ══` + เมนู assign | ☐ | | |
| C4 | Assign menu ครบ | จาก C3 ดูเมนู | options `[1]` blink `[2]` spare `[3]` replace `[4]` attach `[5]` add single `[6]` wipe `[s]` skip | ☐ | | |
| C5 | Assign as spare | C3 → พิมพ์ `2` → เลือก pool `tank` → y | ดิสก์เข้า tank เป็น spare (`✔ added as spare`); `zpool status` เห็น spare AVAIL | ☐ | | |
| C6 | Replace degraded | (มี degraded leaf) C3 → `3` → เลือก # → confirm | `✔ replace started — resilvering`; progress bar; จบ `✔ resilver completed` | ☐ | | |
| C7 | Swap onto spare | `watch` → `s` → เลือกดิสก์ → เลือก spare → y | `zpool replace` รัน; ดิสก์เดิมกลายเป็น spare หลัง resilver | ☐ | | |
| C8 | Offload spare | `watch` → `o` → เลือก spare | spare ถูก detach ออกจาก pool | ☐ | | |
| C9 | Create new pool | `watch` → `n` → เลือกดิสก์ว่าง ≥2 → ใส่ชื่อ/raid type → y | `zpool create` สำเร็จ; `zpool status <new>` ขึ้น ONLINE | ☐ | | |
| C10 | Locate ใหม่ถูก bay | `watch` → `l` → ใส่ bay/serial | ไฟกระพริบตรงตัว (ตรวจ bay_map.json ถ้าผิด) | ☐ | | |

---

## Section D — Edge / Negative path

> เทส negative — ต้องเด้ง error/refuse ถูกต้อง ไม่พังเงียบ ไม่ทำจริง.

| ID | Scenario | Steps | Expected (exact message) | Status | Actual | Comment |
|----|----------|-------|--------------------------|:------:|--------|---------|
| D1 | Cancel ทุก prompt | ทุก action → ตอบ `N` หรือ `q` | ไม่มีอะไรเปลี่ยน; ขึ้น `cancelled` / กลับเมนู | ☐ | | |
| D2 | Swap ไม่มี spare | เอา spare ออกก่อน → `watch` → `s` | `pool 'tank' has no AVAIL spare — add one first` | ☐ | | |
| D3 | Create ดิสก์ไม่พอ | `n` → เลือก 1 ดิสก์ → raid type `raidz2` | `error: need at least 4 disks for raidz2` | ☐ | | |
| D4 | Invalid raid type | `n` → เลือกดิสก์ → พิมพ์ raid type มั่ว เช่น `raid9` | `invalid raid type` | ☐ | | |
| D5 | Demote last mirror leg | `d` → เลือก leg สุดท้ายที่เหลือ | `refuse: not a detachable mirror leg / would break redundancy` | ☐ | | |
| D6 | Assign dirty disk | เสียบดิสก์มี data เก่า → `n` (create) เลือกมัน | `WARNING: The following disks already contain data/labels:` + ขอ confirm wipe | ☐ | | |
| D7 | ดึงดิสก์ระหว่าง resilver | เริ่ม replace (C6) → ดึงอีกตัว | pool ยัง survive (raidz1 ทน 1); ไม่ crash | ☐ | | |

---

## Section E — Unit tests (pytest) — ✅ agent รันให้แล้วในเครื่อง dev

```bash
cd codes && python3 -m pytest tests/ -q
```

**ผลรอบนี้ (2026-06-22, dev machine):** `115 passed, 9 failed` (124 total)

| ID | Scenario | Expected | Status | Actual | Comment |
|----|----------|----------|:------:|--------|---------|
| E1 | Test suite รันได้ | suite รันจบ ไม่ error การ import | ✅ | 124 tests collected, รันจบ | OK |
| E2 | Pass rate | tests ผ่านทั้งหมด | ❌ | 115 passed / **9 failed** | 9 failures เป็น **pre-existing** ไม่ใช่ regression — ดูด้านล่าง |

**รายชื่อ 9 เทสที่ fail (pre-existing — mock signature mismatch):**

```
tests/test_b2ctl.py::TestWatchSwap::test_swap_readds_as_spare_on_success
tests/test_b2ctl.py::TestZfsActions::test_add_spare_command
tests/test_b2ctl.py::TestZfsActions::test_replace_command
tests/test_feature_1b.py::TestFeature1b::test_cmd_demote_success
tests/test_feature_1b.py::TestFeature1b::test_cmd_swap_success
tests/test_feature_1b.py::TestFeature1b::test_demote_to_spare
tests/test_feature_1b.py::TestFeature1b::test_demote_to_spare_detach_fails
tests/test_feature_create_pool.py::TestZfsCreatePool::test_create_pool_mirror
tests/test_feature_create_pool.py::TestZfsCreatePool::test_create_pool_stripe
```

**สาเหตุ:** test mock คาด `run_check(['zpool', ...])` แต่ code เรียก
`run_check(['zpool', ...], dry_run=False)` — production code ถูก (มี `dry_run` kwarg
จากฟีเจอร์ dry-run); test assertion ยังเขียนแบบเก่า. **เป็นปัญหาที่ test ไม่ใช่ที่ code.**

**Comment / action:** ควร fix mock assertion ให้รับ `dry_run=False` (แยกรอบ refactor test) —
ไม่บล็อกการใช้งานจริง. รอ user ตัดสินว่าจะแก้รอบนี้หรือรอบหน้า.

---

## สรุปผล (Summary)

| Section | ทั้งหมด | ✅ PASS | ❌ FAIL | ⏭ SKIP |
|---------|:------:|:------:|:------:|:------:|
| A — Safe / Read-only | 9 | | | |
| B — Dry-run mutating | 7 | | | |
| C — Physical hotplug | 10 | | | |
| D — Edge / Negative | 7 | | | |
| E — Unit tests | 2 | 1 | 1 | |
| **รวม** | **35** | | | |

**Overall comment / สิ่งที่ต้องแก้ต่อ:**

> ______________________________________________________________________
>
> ______________________________________________________________________
