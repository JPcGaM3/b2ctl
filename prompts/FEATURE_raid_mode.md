# FEATURE: RAID-mode support (Dell PERC) + dual-backend — Status: [x] DONE (validate on R640)

Makes b2ctl work on a Dell PERC in **RAID mode** (R640/H730P) as a co-equal
backend alongside IT/HBA, with install profiles and guarded RAID actions.
storcli is removed everywhere (LSI tool, blind to a PERC, caused false detection).

## Affected files

- `b2ctl/hba_raid.py` — perccli tool-pick, VD/PD parser, member enumeration, actions
- `b2ctl/smart.py` — `-d megaraid,<DID>` passthrough
- `b2ctl/common.py` — Disk: `array_type/array_name/smart_dtype/did/pd_state`; assess()
- `b2ctl/backend.py` — `raid_volumes()`; detection drops storcli
- `b2ctl/ui.py` — `HW:`/`SW:` POOL/ARRAY prefix + `render_raid_volumes()`
- `b2ctl/config.py` — `set_mode()`
- `b2ctl/installer.py` — drop storcli, `cp -f /usr/sbin`, `_PROFILE_*`, `install_profile()`
- `b2ctl/raid_actions.py` (new) — guided replace/offline/create/del
- `b2ctl/cli.py` — locate routing, `install --perc/--flash`, `raid-*` subcommands, v0.6.0
- `install.sh` — `--perc/--flash`, storcli removed
- `tests/test_{hba_raid,smart,ui,config,installer,cli}.py`

## Key facts (from real R640 output)

- `smartctl -a -d megaraid,<DID> /dev/sda` returns full ATA SMART (DID 0,1 = the
  two 870 EVO members; 3,7 → INQUIRY failed). No `sat+` needed.
- `perccli /c0/vall show all` = single source: VD row `0/0 RAID1 Optl … 640.0 GB
  MainSSD`; `PDs for VD 0` table `32:0  0 Onln  0 … Samsung SSD 870 EVO 1TB`.
- VD block device = lsblk disk whose MODEL contains `PERC`; any controller block
  device is a valid megaraid SMART target.
- NVMe "1 of 2" = BIOS PCIe bifurcation (hardware), not b2ctl.

## Design

- **Enumeration**: members synthesised from perccli with `dev=<VD blockdev>`,
  `smart_dtype="megaraid,<DID>"`, `array_type="HW"`, `array_name="vd<n>/<level>"`;
  VD block device dropped from disk rows (shown by `raid_volumes()`); JBOD/NVMe
  from lsblk. SMART read via passthrough fills model/serial/wear/POH/TBW.
- **assess()**: HW member = assigned; level from PERC PD state (Rbld→WARNING,
  Failed/Offln→CRITICAL).
- **Actions** (perccli, guarded `[y/N]` + `safety.begin_op/end_op`): locate,
  set_offline/set_missing, start_rebuild + rebuild_progress, add_vd/del_vd;
  `raid_actions.replace()` = guided offline→missing→LED→prompt→rebuild w/ bar.
- **Install profiles**: `--perc`→perccli+mode=raid, `--flash`→sas2ircu+mode=it;
  binaries `cp -f` to `/usr/sbin` (survive deleting /opt/MegaRAID or download dir).

## Test plan (done — 172 pass)

perccli vall parser (real sample), tool-pick non-zero ctrlcount, enumeration
(VD dropped, members + megaraid target), megaraid smartctl first, set_mode,
install profiles, HW/SW column + volumes table, `_pd`/rebuild_progress, confirm
guards on create/del.

## Validate on hardware (cannot test on CI)

1. `./install.sh --perc` → perccli + mode=raid; `b2ctl check` → perccli ≥1 ctrl.
2. `b2ctl status` → 2 members (32:0/32:1, megaraid SMART, `HW:vd0/raid1`), VD in
   volumes table, NVMe direct, no false GHOST.
3. `b2ctl locate 32:0 on` → slot LED.
4. `raid-replace` / `raid-offline` / `raid-create` / `raid-del` (guarded).
5. Capture `perccli /c0/eall/sall show all` with a JBOD disk to finalise the
   JBOD tag path (currently defensive).
