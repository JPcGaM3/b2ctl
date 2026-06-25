# b2ctl

**IT-mode ZFS disk manager** for Dell R620 servers whose **PERC H710** is crossflashed
to IT/HBA mode (LSI SAS9207-8i / SAS2308), running on Proxmox VE. Disks are raw — no RAID
controller to query — so b2ctl reads each drive directly (SMART, `sas2ircu` bay mapping)
and drives ZFS for everything else.

It shows a wide per-disk health table (bay, model, serial, power-on hours, wear, TBW
endurance, bad sectors, SMART, pool/vdev, overall LEVEL) and performs the full disk
lifecycle: replace / swap / offload / demote / create-pool, hot-plug detection, and LED
locate — with a `--dry-run` preview mode, an audit trail, and rollback hints.

> 📌 Python **stdlib only** — no pip dependencies. Runs as root on Proxmox VE.

## 🚀 Quick install

```bash
cd codes
sudo ./install.sh                 # installs to /opt/b2ctl + launcher /usr/local/sbin/b2ctl
# or, on a fresh server, also fetch the HBA/RAID tool binaries:
sudo ./install.sh --with-tools    # downloads + installs sas2ircu / storcli / perccli
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
| 🛠️ [**DevOps & Architecture Guide**](docs/devops-guide.md) | Internals: every subprocess, scan pipeline, safety model, backend detection |
| 👣 [**Step-by-step Walkthrough**](docs/walkthrough.md) | "press X → see Y → what it means", with real server output |
| ✅ [**Test Checklist / Report**](docs/test-checklist.md) | Pass/fail test report across all scenarios |

## 🧪 Try it without hardware

A stdlib **simulation harness** lets you run the real b2ctl against a fake 6-disk server
(both IT and RAID backends) on a laptop — no hardware, SSH, or root:

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
