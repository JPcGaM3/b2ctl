# b2ctl simulation harness

จำลอง server 1 เครื่อง 8 disks (6 SATA/SAS + 2 NVMe) เป็น `state.json` แล้วรัน **b2ctl ตัวจริง**
กับมันได้บน laptop โดยไม่ต้องมี hardware / SSH / root. b2ctl คุย hardware ผ่าน subprocess ทั้งหมด —
harness นี้แค่วาง **fake binaries** (`bin/zpool`, `lsblk`, `sas2ircu`, `perccli`, `smartctl`, ...) บน PATH
ที่อ่าน/เขียน `state.json`. **ไม่แก้ production code b2ctl เลย** (launcher `sim/run` แค่ตั้ง
PATH + ชี้ state + pretend root + ใช้ identity bay map).

## เริ่มใช้

```bash
cd codes
python3 sim/simctl init            # สร้าง state เริ่มต้น (rpool mirror + tank raidz1 + spare)
python3 sim/run status             # b2ctl status กับ sim
python3 sim/run check              # backend detect
python3 sim/run watch              # interactive (swap/replace/offload/create/...)
```

## เปลี่ยน state (อีก terminal ขณะเปิด watch ได้)

```bash
python3 sim/simctl show            # ดู disks + pools + mode
python3 sim/simctl pull 1:5        # ดึง disk (ถ้ามี spare → auto-resilver onto spare)
python3 sim/simctl insert 1:5      # เสียบกลับ → watch เห็น NEW DISK DETECTED
python3 sim/simctl dirty 1:5       # mark ว่ามี data เก่า (ทดสอบ create wipe warning)
python3 sim/simctl mode raid       # สลับเป็น RAID-mode (bay via perccli)
python3 sim/simctl mode it         # กลับ IT-mode (bay via sas2ircu)
python3 sim/simctl reset           # ล้างกลับ default
```

## ครอบคลุม

- status / --json / pool-aware summary (รวมคอลัมน์ HEALTH_CHK + pool SCRUB/TRIM), check / update / config
- watch ทุก hotkey: r a o s d t n e m u x l q
- swap / replace / offload / demote / create pool / **destroy** / **extend (L2ARC cache + SLOG log, raid10)** — **state เปลี่ยนจริง**
- **maint**: scrub / trim / health-check (long self-test + badblocks, verdict) — `[m]` หรือ `b2ctl maint …`
- NVMe (nvme0n1/nvme1n1), hot-plug (pull/insert), spare auto-replace, resilver progress
- dry-run (`--dry-run` + watch `t`), log / rollback / snapshot
- **ทั้ง 2 backend**: IT-mode (sas2ircu) และ RAID-mode (perccli)

## Audit log / snapshot ของ sim แยกจากของจริง

sim เขียน audit trail + pre-op snapshot ลง **`sim/var/`** (`sim/var/ops.jsonl` +
`sim/var/snapshots/`) — **ไม่เคยแตะ `/var/log/b2ctl/` ของ production**. ดังนั้น:
- แยกออกง่าย: sim op อยู่คนละ path กับ real op (ไม่ปนใน `ops.jsonl` เดียวกัน)
- `b2ctl log` / `rollback` / snapshot **ใช้ใน sim ได้** (dir เขียนได้ ไม่ต้อง root)
- snapshot ของ sim สังเกตเพิ่มได้: token เป็น `/dev/sdX` (ไม่ใช่ by-id) + `zpool version` = `zfs-2.4.2-sim`

(`sim/var/` gitignored — generated)

## ข้อจำกัด (มันคือ model ไม่ใช่ ZFS จริง)

- **by-id = ""** — sim ใช้ `/dev/sdX` เป็น token (real ใช้ `ata-`/`wwn-` by-id). flow/logic เทสได้ครบ
- **LED locate** = print message เฉยๆ ไม่มีไฟจริง
- **ไม่เจอ ZFS/hardware quirk จริง**: checksum/scrub, real resilver time, timing race, rpool `-part3` + proxmox-boot-tool (เป็น message)
- bay = identity (state slot == bay ที่แสดง); ไม่จำลอง Dell slot-reversal
- → ใช้เทส **b2ctl logic/flow/parsing** ไม่ใช่เทส ZFS engine

## ไฟล์

| file | หน้าที่ |
|------|--------|
| `state.json` | source of truth (สร้างด้วย `simctl init`) |
| `_simstate.py` | state load/save + helpers + default 8-disk layout (6 SATA/SAS + 2 NVMe) |
| `bin/*` | fake binaries (อ่าน `$B2CTL_STATE`) |
| `simctl` | สั่ง state (init/mode/pull/insert/dirty/show) |
| `run` | launcher: รัน b2ctl ตัวจริงกับ sim (PATH/state/fake-root/identity bay) |
