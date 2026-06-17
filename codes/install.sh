#!/usr/bin/env bash
# b2ctl (IT-mode / HBA build) installer
# Installs the package under /opt/b2ctl and a launcher at /usr/local/sbin/b2ctl.
set -euo pipefail

PREFIX="/opt/b2ctl"
LAUNCHER="/usr/local/sbin/b2ctl"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo ./install.sh" >&2
  exit 1
fi

echo "[*] installing b2ctl package -> ${PREFIX}"
mkdir -p "${PREFIX}"
cp -r "${SRC_DIR}/b2ctl" "${PREFIX}/"
cp "${SRC_DIR}/ssd_spec.json" "${PREFIX}/"
[ -f "${SRC_DIR}/bay_map.json" ] && cp "${SRC_DIR}/bay_map.json" "${PREFIX}/"

echo "[*] writing launcher -> ${LAUNCHER}"
cat > "${LAUNCHER}" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="${PREFIX}" python3 -P -m b2ctl "\$@"
EOF
chmod +x "${LAUNCHER}"

echo "[*] checking dependencies"
for bin in smartctl zpool lsblk; do
  command -v "$bin" >/dev/null 2>&1 || echo "  [!] missing: $bin"
done
command -v sas2ircu >/dev/null 2>&1 || \
  echo "  [!] sas2ircu missing — bay numbers will be disabled"
command -v ledctl >/dev/null 2>&1 || \
  echo "  [i] optional: apt install ledmon  (nicer locate LEDs; dd fallback works without it)"

echo "[+] done. try:  sudo b2ctl status   |   sudo b2ctl watch"
