# b2ctl

**IT-mode ZFS disk manager** for Dell PowerEdge servers whose **PERC H710** is crossflashed
to IT/HBA mode (LSI SAS9207-8i / SAS2308), running on Proxmox VE (crossflashed R620 boxes
plus an R740xd NVMe host). Disks are raw — no RAID controller to query — so b2ctl reads
each drive directly (SMART, `sas2ircu` bay mapping) and drives ZFS for everything else.
(A Dell PERC RAID box is also supported via **perccli**; auto-detected or forced.)

It shows a wide per-disk health table (bay, model, serial, power-on hours, wear, TBW
endurance, bad sectors, SMART, HEALTH_CHK last self-test, pool/vdev, overall LEVEL — plus
per-pool SCRUB/TRIM freshness) and performs the full disk lifecycle: replace / swap /
offload / demote / create-pool / **destroy**, **extend** (L2ARC cache + SLOG log, with
optional **over-provisioning**), and **maint** (manual scrub / trim / health-check), with
hot-plug detection and LED locate — all under a `--dry-run` preview mode, an audit trail,
and rollback hints.

> 📌 Python **stdlib only** — no pip dependencies. Runs as root on Proxmox VE.

## 🚀 Quick install

```bash
cd codes
./install.sh                      # installs to /opt/b2ctl + launcher /usr/local/sbin/b2ctl
                                  # (run as root on Proxmox — no sudo). b2ctl only, no downloads.
# or fetch the HBA/RAID tool binaries too:
./install.sh --with-tools         # b2ctl + sas2ircu + perccli (no mode change)
./install.sh --flash              # b2ctl + sas2ircu, sets controller.mode = it  (crossflashed HBA)
./install.sh --perc               # b2ctl + perccli,  sets controller.mode = raid (Dell PERC RAID)
```

Then:

```bash
sudo b2ctl status        # one-shot health table
sudo b2ctl watch         # interactive hotplug-aware loop
sudo b2ctl check         # verify tools + backend
```

## 📚 Documentation

| Doc | สำหรับใคร |
|-----|-----------|
| 📖 [**User Guide (EN)**](docs/user-guide-en.md) | Full manual for operators — runbooks, table reference, watch features, safety |
| 🇹🇭 [**คู่มือการใช้งาน (TH)**](docs/user-guide-th.md) | คู่มือผู้ใช้ฉบับสมบูรณ์ภาษาไทย — runbook หน้างาน + cheat sheet |
| 🖥️ [**User Manual (HTML, TH)**](docs/user-manual.html) | คู่มือ HTML แบบมี sidebar/ค้นหา — เริ่มจากศูนย์ ทีละหัวข้อ |
| 🛠️ [**DevOps & Architecture Guide**](docs/devops-guide.md) | Internals: every subprocess, scan pipeline, safety model, backend detection |
| 👣 [**Step-by-step Walkthrough**](docs/walkthrough.md) | "press X → see Y → what it means", with real server output |
| ✅ [**Test Checklist / Report**](docs/test-checklist.md) | Pass/fail test report across all scenarios |

## 🧪 Try it without hardware

A stdlib **simulation harness** lets you run the real b2ctl against a fake 8-disk server
(6 SATA/SAS + 2 NVMe, both IT and RAID backends) on a laptop — no hardware, SSH, or root:

```bash
cd codes
python3 sim/simctl init && python3 sim/run status
```

See [`codes/sim/README.md`](codes/sim/README.md).

## Repository layout

```
codes/        b2ctl Python package + install.sh + tests/ + sim/
docs/         user/devops guides, walkthrough, test checklist
```
