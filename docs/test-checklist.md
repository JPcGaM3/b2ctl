<a id="top"></a>

# b2ctl — Test Checklist / Test Report

> **Version:** `0.18.0-itmode`  ·  **Build:** IT-mode / LSI SAS2308 (+ RAID-mode PERC path)
> **ผู้ทดสอบ:** Claude (via `codes/sim` harness)  ·  **วันที่:** 2026-07-22

> ✅ **SIMULATION RUN (v0.18.0-itmode, 2026-07-22).** ผลด้านล่างมาจากการรัน **b2ctl ตัวจริง**
> กับ **simulation harness** (`codes/sim/` — fake 8-disk server, 6 SATA/SAS + 2 NVMe, `state.json`)
> บน laptop **ไม่มี hardware/SSH/root**. มันเทส **CLI / logic / flow / parsing / output ของ v0.18.0
> จริง** แต่ **ไม่ใช่** hardware pass/fail — sim เป็น *model* ไม่ใช่ ZFS/PERC จริง (ดู
> [ข้อจำกัด](#sim-limits)). รอบ hardware ล่าสุด (v0.5.0-itmode, 2026-06-22, nodes 201/203) อยู่ใน
> git history — ต้องรัน hardware ซ้ำเพื่อได้ pass/fail จริงบนเครื่อง.

> **Environment (sim):**
> - Harness: `codes/sim/` (launcher `sim/run` = b2ctl ตัวจริง + fake binaries บน PATH)
> - Fake layout: `rpool` (mirror, 2× 860 PRO) · `tank` (**raidz1**, 3× 870 EVO + 1 spare) · 2× NVMe 990 EVO Plus (unassigned)
> - `zpool version` = `zfs-2.4.2-sim` · audit/maint/snapshot → `sim/var/` (ไม่แตะ `/var/log/b2ctl/`)
> - รันจริงบน b2ctl `0.18.0-itmode` (ไม่แก้ production code — sim = fake binaries + launcher)

เอกสารนี้เป็น **checklist** ไล่เทส b2ctl ทุก scenario แล้วใช้เป็น test report ส่งต่อได้.
ภาษา: เนื้อหาไทย, commands/technical terms อังกฤษ.

---

<a id="toc"></a>

## 📑 สารบัญ (Table of Contents)

1. [📊 Executive Summary](#exec-summary)
   - [🔀 v0.18.0 deltas ที่เทส](#v018-deltas)
2. [⚠️ Safety rules](#safety)
3. [วิธีใช้ + Legend](#usage)
4. [Pre-flight (baseline)](#preflight)
5. [Section A — Safe / Read-only](#sec-a)
6. [Section B — Dry-run mutating](#sec-b)
7. [Section C — Hotplug (sim pull/insert)](#sec-c)
8. [Section D — Edge / Negative path](#sec-d)
9. [Section E — Unit tests (pytest)](#sec-e)
10. [Section F — RAID backend](#sec-f)
11. [ข้อจำกัดของ sim](#sim-limits)

---

<a id="exec-summary"></a>

## 📊 Executive Summary

| Section | ทั้งหมด | ✅ PASS | ❌ FAIL | ⏭ SKIP |
|---------|:------:|:------:|:------:|:------:|
| A — Safe / Read-only | 8 | 8 | 0 | 0 |
| B — Dry-run mutating | 8 | 8 | 0 | 0 |
| C — Hotplug (sim) | 4 | 4 | 0 | 0 |
| D — Edge / Negative | 5 | 5 | 0 | 0 |
| E — Unit tests | 2 | 2 | 0 | 0 |
| F — RAID backend | 3 | 3 | 0 | 0 |
| **รวม** | **30** | **30** | **0** | **0** |

> **0 FAIL** ใน sim. Unit suite: **677 passed, 14 subtests**. ทุก v0.18.0 delta (ดูด้านล่าง)
> ยืนยันด้วย output จริงจาก sim. **ย้ำ:** นี่คือ sim (logic/flow) — hotplug/LED/ZFS-engine
> เป็น model, ต้องรัน hardware แยกเพื่อ pass/fail จริง.

<a id="v018-deltas"></a>

### 🔀 v0.18.0 deltas ที่เทส (ต่างจาก v0.5.0 hardware report)

- **Burn-in ยุบเข้า `maint`** — verb `b2ctl burnin` และปุ่ม watch `[b]` **หายไปแล้ว**; disk vetting =
  `[m]aint → [3] health-check` / `b2ctl maint health <dev…>` (B7, D3, E4).
- **TRIM timer ถูกทิ้ง** — `create` เรียก `install_pool_timers(…, include_trim=False)` เสมอ;
  `autotrim off` = **manual-only** (B4).
- **autotrim/autoscrub default OFF**, prompt เรียงใหม่เป็น `[1] off (default) / [2] on` (B4).
- **destroy → `timers disabled`** (ไม่ใช่ `cron removed` แล้ว) + `systemctl disable` per-pool timer (B8).
- **HEALTH_CHK column** (`OK …hPOH`) + pool **SCRUB/TRIM** columns (A1, C1).
- **SAS self-test classifier** `common.selftest_passed` — `maint health` บน disk คืน PASS ถูกต้อง (D4).
- **watch menu** `[m]aint` แทน `[b]urnin` (B7).

---

<a id="safety"></a>

## ⚠️ Safety rules (อ่านก่อนเทส — ใช้ตอนรัน hardware จริง)

- **เทส mutating ops บน `tank` เท่านั้น — อย่าแตะ `rpool`** (boot pool ของ Proxmox)
- ลองด้วย **`--dry-run` ก่อนเสมอ** (Section B = dry-run, ไม่กระทบข้อมูล)
- **capture `zpool status tank` ก่อน-หลัง** ทุก mutating test ไว้เทียบ
- `tank` = **raidz1** → ทนดิสก์เสียได้ **1 ตัวเท่านั้น**; ระหว่าง resilver อย่าดึงตัวที่ 2
- ห้ามจุดไฟ locate LED บนดิสก์ที่กำลัง resilver
- (sim ไม่มีความเสี่ยงข้อมูลจริง — state.json สร้างใหม่ได้ด้วย `sim/simctl init`)

---

<a id="usage"></a>

## วิธีใช้ + Legend

```bash
cd codes
python3 sim/simctl init            # สร้าง state เริ่มต้น (rpool mirror + tank raidz1 + spare)
python3 sim/run <verb> [args]      # รัน b2ctl ตัวจริงกับ sim
python3 sim/simctl pull|insert|dirty|mode|reset|show   # เปลี่ยน state
```

| สัญลักษณ์ | ความหมาย |
| --------- | -------- |
| `☐` | ยังไม่เทส |
| `✅` | PASS — ตรง Expected |
| `❌` | FAIL — ไม่ตรง Expected (กรอก Comment ด้วย) |
| `⏭` | SKIP — ข้าม (กรอกเหตุผลใน Comment) |

---

<a id="preflight"></a>

## Pre-flight (baseline)

```bash
python3 sim/simctl init
python3 sim/run version
python3 sim/run check
python3 sim/run status --json > /tmp/before.json
```

| ตรวจ | Expected | Status | Actual |
| ---- | -------- | :----: | ------ |
| `b2ctl version` | `b2ctl 0.18.0-itmode` | ✅ | `b2ctl 0.18.0-itmode` |
| `b2ctl check` รันได้ ไม่ crash | root, tools, backend mode, bays mapped, config path | ✅ | IT-mode; sas2ircu/zpool/wipefs/sgdisk/udevadm/dd ✔; `Bays mapped: 6 disks across 1 enclosure(s)` |
| `status --json` valid JSON | `python3 -m json.tool` ผ่าน | ✅ | JSON_OK; array 8 disks, fields ครบ |

<details>
<summary>📋 Output จริง (version / check)</summary>

<pre>
# b2ctl version
b2ctl 0.18.0-itmode

# b2ctl check (sim, ตัดสั้น)
[✔] Running as root
[✔] smartctl     .../sim/bin/smartctl (smartctl 7.5 ...)
[✔] sas2ircu     .../sim/bin/sas2ircu (LSI Corporation SAS2 IR Configuration Utility.)
[✗] perccli      not found (needed for RAID mode)      ← ปกติ: sim IT-mode ไม่ติด perccli
[✔] zpool        .../sim/bin/zpool (zfs-2.4.2-sim)
[✔] wipefs / sgdisk / udevadm / dd   ← tools สำหรับ wipe + over-provision (v0.17/v0.18)
[✔] Detected backend: IT-mode
[✔] Bays mapped: 6 disks across 1 enclosure(s)
[!] Config: .../sim/var/config.json (missing — using defaults, run 'b2ctl config init')
</pre>
</details>

---

<a id="sec-a"></a>

## Section A — Safe / Read-only

> รันได้เลยไม่กระทบ state.

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| A1 | `b2ctl status` | ตารางครบ + **HEALTH_CHK** + POOL/ARRAY `SW:<pool>/<vdev>` + Storage summary (มี **SCRUB/TRIM**) | ✅ | 8 disks (6 SAS + 2 NVMe); HEALTH_CHK `OK 0hPOH`; `SW:rpool/mirror-0`, `SW:tank/raidz1-0`, `SW:tank/spares`; summary rpool/tank |
| A2 | `b2ctl status --json` | JSON array valid | ✅ | JSON_OK; 8 objects; keys `dev,by_id,bay,size_bytes,model,serial,iface,is_ssd,readable,health,poh,wear_val…` |
| A3 | MODEL เต็ม + WRITTEN/TBW | MODEL เต็ม; WRITTEN `xx.xxTB/nnnnTBW` | ✅ | `Samsung SSD 870 EVO 1TB`, `9.71TB/600TBW`; 860 PRO `10.06TB/1200TBW`; NVMe `/2400TBW` |
| A4 | BAY column | enclosure:slot (SAS) + PCIe addr (NVMe) | ✅ | `1:0`…`1:7`; NVMe `PCIe2:0`, `PCIe2:1` |
| A5 | `b2ctl check` | `[✔] sas2ircu`, backend `IT-mode`, bays>0 | ✅ | ✔ sas2ircu, IT-mode, 6 disks / 1 enclosure |
| A6 | `b2ctl config show` | JSON config (tool_paths, controller, `pools`, `pool_defaults`) | ✅ | `pool_defaults: {autotrim: off, autoscrub: false}`, `pools: {}` (v0.17/v0.18 sections) |
| A7 | `b2ctl log` | ตาราง history หรือ "No operations logged yet" | ✅ | `No operations logged yet.` (state สด) |
| A8 | `b2ctl maint --log` | ตาราง maint history หรือ "No maintenance events logged yet" | ✅ | `No maintenance events logged yet.` (ก่อน B/maint) |

<details>
<summary>📋 Output จริง (status table)</summary>

<pre>
BAY     DEV     IF   MODEL                   SERIAL           POWER_ON      WEAR(used) END(left) WRITTEN         BAD HEALTH POOL/ARRAY        STATUS HEALTH_CHK LEVEL
1:0     sdf     SAS  Samsung SSD 860 PRO 1TB S5G8NE0MA10474H  51055h(~5.8y) 1%         99.2%     10.06TB/1200TBW 0   PASSED SW:rpool/mirror-0 ONLINE OK 0hPOH  NORMAL
1:4     sdb     SAS  Samsung SSD 870 EVO 1TB S74ZNS0W537278Y  22926h(~2.6y) 1%         98.4%     9.71TB/600TBW   0   PASSED SW:tank/raidz1-0  ONLINE OK 0hPOH  NORMAL
1:7     sde     SAS  Samsung SSD 870 EVO 1TB S74ZNS0W582280E  18281h(~2.1y) 1%         99.8%     1.01TB/600TBW   0   PASSED SW:tank/spares    AVAIL  OK 0hPOH  NORMAL
PCIe2:0 nvme0n1 NVME Samsung SSD 990 EVO Plu S7U9NU0Y401069K  2h(~0.0y)     0%         100.0%    0.00TB/2400TBW  0   PASSED -                       OK 0hPOH  CONFIG
Storage summary:
  TYPE NAME   LEVEL  STATE   SIZE   USED   FREE   SCRUB   TRIM
  SW   rpool  mirror ONLINE  952G   1.71G  952G   -       -
  SW   tank   raidz1 ONLINE  2.72T  1.71G  2.72T  -       -
===== disks needing config (unassigned) =====
- bay PCIe2:0 /dev/nvme0n1 (Samsung SSD 990 EVO Plus 4TB, SN S7U9NU0Y401069K) [CONFIG]
</pre>

ตาราง v0.18.0 เพิ่ม **HEALTH_CHK** (self-test log แบบ POH-relative, e.g. `OK 120hPOH`) และ
Storage summary เพิ่ม **SCRUB/TRIM** (last scrub live จาก `zpool status`, last trim จาก `maint.jsonl`).
</details>

---

<a id="sec-b"></a>

## Section B — Dry-run mutating

> `--dry-run` → print คำสั่งที่จะรัน **แต่ไม่ทำจริง**. เทียบ state ก่อน-หลัง ต้องไม่เปลี่ยน.

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| B1 | `--dry-run swap` → tank member → y | print `zpool replace/detach/add spare` + preview msg | ✅ | box `Will run:` + `[DRY-RUN] would run: zpool replace tank /dev/sdb /dev/sde` + `• swap dry-run preview — nothing changed` (v0.18: ไม่ขึ้น ✗ ปลอมแล้ว) |
| B2 | `--dry-run swap` → rpool member | ปฏิเสธ (rpool ไม่มี spare) | ✅ | `no AVAIL spare in pool 'rpool'` |
| B3 | `--dry-run demote` → rpool leg | last-redundancy guard: warn + type pool name | ✅ | `⚠ this removes the LAST redundancy of 'rpool'…` + `type the pool name 'rpool' to demote anyway>` → ตอบผิด → `cancelled` |
| B4 | `--dry-run create` (2 NVMe, mirror, default) | `zpool create -f -o ashift=12 -o autotrim=off …`; timers; manual-only warnings | ✅ | ดู output; **autotrim/autoscrub prompt `[1] off (default) / [2] on`**; `✔ maintenance timers: no timers enabled`; `[!] autoscrub OFF …`; `[!] autotrim OFF — TRIM manually via b2ctl maint trim` |
| B5 | `--dry-run offload` → tank member | replace-preview + free-disk menu | ✅ | `• replace dry-run preview — nothing changed` + menu `[1..6]/[s]`; `s` → `skipped` |
| B6 | `watch` → `t` | `[DRY-RUN MODE: ON]` | ✅ | กด `t` → `[DRY-RUN MODE: ON]`; `q` → `bye` |
| B7 | `watch` menu ครบ (v0.18) | `[m]aint` มี, **ไม่มี `[b]urnin`** | ✅ | `[r]efresh [a]ssign [o]ffload [s]wap [d]emote [t]oggle-dryrun [n]ew-pool [e]xtend [m]aint [u]dev-rescue [x]destroy-pool [l]ocate [q]uit` |
| B8 | `--dry-run destroy` tank | `zpool destroy` + `systemctl disable` timers + `timers disabled` | ✅ | `zpool destroy tank` + `systemctl disable --now zfs-scrub-monthly@tank.timer` + `…zfs-trim-monthly@tank.timer` + **`✔ pool 'tank' destroyed; timers disabled`** |

<details>
<summary>📋 Output จริง (B4 create / B8 destroy)</summary>

<pre>
# B4 — --dry-run create (2 NVMe mirror)
    autotrim: [1] off — manual TRIM via [m]aint / `b2ctl maint trim` (recommended)
              [2] on  — zpool autotrim=on (ZFS trims inline)
    autoscrub: [1] off — manual scrub via [m]aint / `b2ctl maint scrub` (recommended)
               [2] on  — monthly zfs-scrub timer (self-heals silent bitrot)
  -> ashift=12 autotrim=off compression=lz4 atime=off xattr=sa dnodesize=auto acltype=posixacl recordsize=128K
  create pool 'testpool' (mirror) with 2 disks (full disk)? [y/N]>
[DRY-RUN] would run: zpool create -f -o ashift=12 -o autotrim=off -O compression=lz4 -O atime=off \
                     -O xattr=sa -O dnodesize=auto -O acltype=posixacl -O recordsize=128K testpool mirror /dev/nvme0n1 /dev/nvme1n1
  ✔ pool created
  ✔ maintenance timers: no timers enabled
  [!] autoscrub OFF — no monthly self-heal scheduled for 'testpool'; run `b2ctl maint scrub testpool` (or [m]aint) periodically
  [!] autotrim OFF — TRIM manually via `b2ctl maint trim testpool` (or [m]aint)

# B8 — --dry-run destroy tank
[DRY-RUN] would run: zpool destroy tank
[DRY-RUN] would run: systemctl disable --now zfs-scrub-monthly@tank.timer
[DRY-RUN] would run: systemctl disable --now zfs-trim-monthly@tank.timer
  ✔ pool 'tank' destroyed; timers disabled
</pre>

v0.18.0: create ไม่ตั้ง trim timer เลย (`include_trim=False`); autoscrub off → `no timers enabled`.
destroy สั่ง `systemctl disable` timer ของ pool (ไม่ใช่ cron) → ข้อความ `timers disabled`.
</details>

---

<a id="sec-c"></a>

## Section C — Hotplug (sim pull/insert) + maint

> `sim/simctl pull|insert` = simulated hotplug (ไม่ใช่ physical). maint = scrub/trim/health.

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| C1 | `simctl pull 1:5` (tank member, มี spare) | spare auto-resilver; `status` = tank **DEGRADED** + spare **INUSE** | ✅ | `pulled sdc (bay 1:5) → spare sde kicking in (resilver)`; status: `SW tank raidz1 DEGRADED`, `SW:tank/spares … INUSE`; `pool tank: DEGRADED` |
| C2 | `maint scrub tank` → y | `✔ scrub started` + log `started` ใน maint.jsonl | ✅ | `✔ scrub started`; ถาม watch live (Ctrl-C detaches); `maint --log` เห็น `scrub tank started` |
| C3 | `maint trim tank` → y | `✔ trim started` + log | ✅ | `✔ trim started`; `maint --log` เห็น `trim tank started` |
| C4 | `maint health nvme0n1 --short` (free disk) | burn-in engine → verdict PASS | ✅ | `Burn-in /dev/nvme0n1 …` → self-test `done` → **`[PASS] … ✔ safe to add to a pool.`** |

<details>
<summary>📋 Output จริง (C1 pull / C4 maint health / maint --log)</summary>

<pre>
# C1 — simctl pull 1:5
[sim] pulled sdc (bay 1:5) → spare sde kicking in (resilver)
# status (หลัง pull)
1:7  sde  ... SW:tank/spares   INUSE   OK 0hPOH  NORMAL
  SW   tank   raidz1   DEGRADED   2.72T  1.71G  2.72T
- pool tank: DEGRADED (not ONLINE — a member may be missing/resilvering)

# C4 — maint health nvme0n1 --short
Burn-in /dev/nvme0n1 (bay PCIe2:0) Samsung SSD 990 EVO Plus 4TB (S7U9NU0Y401069K)
 BAY     DISK     SELF-TEST   SURFACE SCAN (badblocks)
 PCIe2:0 nvme0n1  done        n/a
  [PASS] bay PCIe2:0 /dev/nvme0n1 (S7U9NU0Y401069K)
    ✔ safe to add to a pool.

# maint --log
WHEN                  KIND   TARGET   STATUS   DETAIL
2026-07-22T16:51:54   scrub  tank     started
2026-07-22T16:51:54   trim   tank     started
</pre>

C4 = ตัวเดียวกับ `b2ctl burnin` เดิม (v0.18 ยุบเป็น `maint health`): multi-select long self-test
(+ optional read-only `badblocks --scan`) + PASS/WARN/FAIL, non-blocking, re-attach `--status`.
</details>

---

<a id="sec-d"></a>

## Section D — Edge / Negative path

> ต้องเด้ง error/refuse ถูกต้อง ไม่พังเงียบ ไม่ทำจริง.

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| D1 | Cancel prompt — ตอบ `N`/Enter | `cancelled`, ไม่เปลี่ยน state | ✅ | swap/scrub/trim ตอบ Enter → `cancelled` |
| D2 | Create raidz2 ดิสก์ไม่พอ (1 disk) | `error: need at least 4 disks for raidz2` | ✅ | `error: need at least 4 disks for raidz2` |
| D3 | Invalid raid type (`raid9`) | `invalid raid type` | ✅ | `invalid raid type` |
| D4 | `maint health` บน in-pool member (`sdb`) | ปฏิเสธ — vet เฉพาะ free/spare | ✅ | `[-] maint health vets free/spare disks; /dev/sdb is in pool 'tank' — self-test it with \`smartctl -t long\` directly.` |
| D5 | Demote last mirror leg (rpool) | guard: warn + require type pool name | ✅ | last-redundancy guard (ดู B3) — raidz members ถูก filter ออกจากเมนู demote |

<details>
<summary>📋 Output จริง (D2/D3 create reject / D4 in-pool refuse)</summary>

<pre>
# D2 — create raidz2, 1 disk
  raid type (stripe, mirror, raid10, raidz1, raidz2) [mirror]>  raidz2
  error: need at least 4 disks for raidz2

# D3 — create, raid9
  raid type (...) [mirror]>  raid9
  invalid raid type

# D4 — maint health sdb (in-pool)
[-] maint health vets free/spare disks; /dev/sdb is in pool 'tank' — self-test it with `smartctl -t long` directly.
</pre>

D4 = v0.18.0 guard `burnin._poolable_target`: `maint health` vet เฉพาะ FREE/SPARE disk ทั้ง CLI + watch;
active member ให้รัน `smartctl -t long` เองใน shell (HEALTH_CHK column ยังโชว์ passive).
</details>

---

<a id="sec-e"></a>

## Section E — Unit tests (pytest)

```bash
cd codes && python3 -m pytest tests/ -q
```

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| E1 | Test suite รันได้ | รันจบ ไม่ error import | ✅ | tests collected, รันจบใน ~12s |
| E2 | Pass rate | ผ่านทั้งหมด | ✅ | **677 passed, 14 subtests passed** |

> โครงสร้าง test = 1 ไฟล์ต่อ 1 module (`tests/test_<module>.py`), shared `_disk()` factory ใน
> `tests/helpers.py`. รวม regression จริง: X357 SAS ผ่าน `smart.read`→`assess` = `uncorr=0`/PASS
> ทั้งที่ `Non-medium error count: 1061` (v0.18.0).

---

<a id="sec-f"></a>

## Section F — RAID backend (perccli)

> `simctl mode raid` → b2ctl auto-detect RAID backend (perccli) แทน IT (sas2ircu).

| ID | Scenario | Expected | Status | Actual |
|----|----------|----------|:------:|--------|
| F1 | `simctl mode raid` + `check` | `[✔] perccli`, `Detected backend: RAID-mode` | ✅ | `perccli … (Controller Count = 1)`, `Detected backend: RAID-mode` |
| F2 | `status` (RAID) | sub-headers `--- Hardware (PERC RAID) ---` / `--- Software (ZFS…) ---`; column `HW:vd<n>/<level>` | ✅ | `HW:vd0/raid5` per member; volumes table `HW vd0 raid5 Optl 2.727 TB` |
| F3 | Members via megaraid | physical members อ่านผ่าน `smartctl -d megaraid,<DID>` | ✅ | members อ่าน SMART ได้ (PASSED); free disks เสนอ `set JBOD / raid-create` |

<details>
<summary>📋 Output จริง (RAID-mode status)</summary>

<pre>
--- Hardware (PERC RAID) ---
1:0  sdz  SATA Samsung SSD 860 PRO 1TB ...  HW:vd0/raid5   OK 0hPOH  NORMAL
1:4  sdz  SATA Samsung SSD 870 EVO 1TB ...  HW:vd0/raid5   OK 0hPOH  NORMAL
--- Software (ZFS / unassigned) ---
1:6  sdz  SATA Samsung SSD 870 EVO 1TB ...  -              OK 0hPOH  CONFIG
Storage summary:
  TYPE NAME  LEVEL  STATE  SIZE      USED  FREE  SCRUB  TRIM
  HW   vd0   raid5  Optl   2.727 TB  -     -     -      -
- bay 1:6 ... available (Unconfigured Good) — set JBOD for ZFS, or add to a RAID volume (raid-create)
</pre>

พิสูจน์ b2ctl = **dual-backend** (IT + RAID co-equal, auto-detected). `perccli` + `smartctl -d
megaraid` ใช้ใน RAID backend (มีเฉพาะ `storcli` ที่ถูกถอดออก).
</details>

---

<a id="sim-limits"></a>

## ข้อจำกัดของ sim (มันคือ model ไม่ใช่ ZFS/PERC จริง)

- **by-id = ""** — sim ใช้ `/dev/sdX` เป็น token (real ใช้ `ata-`/`wwn-`/`nvme-` by-id). flow/logic เทสได้ครบ แต่ path จริงต่าง
- **LED locate** = print message เฉยๆ ไม่มีไฟจริง; **hotplug** = `simctl pull/insert` (ไม่ใช่ physical unplug)
- **ไม่เจอ ZFS/hardware quirk จริง**: checksum/real scrub, resilver time จริง, timing race, rpool `-part3` + `proxmox-boot-tool` (เป็น message)
- bay = identity (state slot == bay ที่แสดง); ไม่จำลอง Dell slot-reversal
- NVMe `by-id` relabel ต้องเครื่องจริง (sysfs/by-id แฟกไม่ได้)
- → ใช้เทส **b2ctl logic/flow/parsing/output** ไม่ใช่เทส ZFS engine. **pass/fail บน hardware ต้องรันซ้ำบนเครื่อง.**

---

[↑ กลับขึ้นบนสุด](#top)
