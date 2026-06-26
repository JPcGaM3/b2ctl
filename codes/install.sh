#!/usr/bin/env bash
# b2ctl (IT-mode / HBA build) installer
# Installs the package under /opt/b2ctl and a launcher at /usr/local/sbin/b2ctl.
set -euo pipefail

PREFIX="/opt/b2ctl"
LAUNCHER="/usr/local/sbin/b2ctl"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(dirname "${SRC_DIR}")/tools"

# storcli dropped: LSI tool, blind to a Dell PERC, only caused false RAID
# detection. RAID = perccli, IT = sas2ircu.
_GDRIVE_SAS2IRCU="1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt"
_GDRIVE_PERCCLI="1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk"
_GDRIVE_BASE="https://drive.usercontent.google.com/download?export=download&confirm=t&id="

# Tool selection + optional controller mode:
#   --with-tools : sas2ircu + perccli (no mode change)
#   --perc       : perccli  + controller.mode=raid
#   --flash      : sas2ircu + controller.mode=it
WITH_TOOLS=0
TOOLSET=""       # space-separated subset: "sas2ircu perccli"
SET_MODE=""      # "raid" | "it" | ""
for _arg in "$@"; do
    case "$_arg" in
    --with-tools) WITH_TOOLS=1; TOOLSET="sas2ircu perccli" ;;
    --perc)       WITH_TOOLS=1; TOOLSET="perccli";  SET_MODE="raid" ;;
    --flash)      WITH_TOOLS=1; TOOLSET="sas2ircu"; SET_MODE="it" ;;
    *) ;;
    esac
done

download_tools() {
    local _dest="$1"
    echo ""
    echo "=== Downloading tool archives from Google Drive ==="

    local _dl=""
    if command -v curl >/dev/null 2>&1; then
        _dl="curl"
    elif command -v wget >/dev/null 2>&1; then
        _dl="wget"
    else
        echo "  [✗] curl or wget not found — cannot download" >&2
        return 1
    fi

    _gdrive_get() {
        local _id="$1" _out="$2"
        local _url="${_GDRIVE_BASE}${_id}"
        echo "[*] $(basename "${_out}") ..."
        if [ "${_dl}" = "curl" ]; then
            curl -L --progress-bar "${_url}" -o "${_out}" || return 1
        else
            wget -q --show-progress "${_url}" -O "${_out}" || return 1
        fi
        # Verify not a Google HTML error page (real archive > 1 KB)
        local _size
        _size=$(wc -c < "${_out}" 2>/dev/null || echo 0)
        if [ "${_size}" -lt 1024 ]; then
            echo "  [✗] $(basename "${_out}"): download too small (${_size} bytes), may have failed" >&2
            return 1
        fi
        echo "  [✔] $(basename "${_out}")"
    }

    case " ${TOOLSET} " in
    *" sas2ircu "*) _gdrive_get "${_GDRIVE_SAS2IRCU}" "${_dest}/SAS2IRCU_P20.zip" || return 1 ;;
    esac
    case " ${TOOLSET} " in
    *" perccli "*)  _gdrive_get "${_GDRIVE_PERCCLI}"  "${_dest}/perccli_7.1-007.0127_linux.tar.gz" || return 1 ;;
    esac
}

install_tools() {
    local _tools="$1"
    local _tmp
    _tmp=$(mktemp -d)

    echo ""
    echo "=== Installing tool binaries ==="
    # i386 multiarch must be registered before libc6-i386 (sas2ircu is 32-bit ELF)
    dpkg --add-architecture i386 2>/dev/null || true
    apt-get update -qq 2>/dev/null || true
    apt-get install -y alien unzip libc6-i386 smartmontools zfsutils-linux gdisk util-linux coreutils udev

    # ── sas2ircu ──────────────────────────────────────────────────────────────
    if [[ " ${TOOLSET} " == *" sas2ircu "* ]]; then
    echo "[*] sas2ircu..."
    if [ -f "${_tools}/SAS2IRCU_P20.zip" ]; then
        unzip -q "${_tools}/SAS2IRCU_P20.zip" -d "${_tmp}/sas2ircu" || true
        local _arch _sas=""
        _arch=$(uname -m)
        if [ "${_arch}" = "x86_64" ]; then
            _sas=$(find "${_tmp}/sas2ircu" -path "*x86-64*" -name "sas2ircu" -type f 2>/dev/null | head -1)
        fi
        [ -z "${_sas}" ] && _sas=$(find "${_tmp}/sas2ircu" -path "*x86*" -name "sas2ircu" -type f 2>/dev/null | grep -v '\.exe' | head -1)
        if [ -n "${_sas}" ]; then
            cp -f "${_sas}" /usr/sbin/sas2ircu
            chmod +x /usr/sbin/sas2ircu
            echo "  [✔] sas2ircu -> /usr/sbin/sas2ircu"
        else
            echo "  [✗] sas2ircu: binary not found in archive"
        fi
    else
        echo "  [✗] sas2ircu: archive not found at ${_tools}/SAS2IRCU_P20.zip"
    fi
    fi  # TOOLSET sas2ircu

    # ── perccli64 ─────────────────────────────────────────────────────────────
    if [[ " ${TOOLSET} " == *" perccli "* ]]; then
    echo "[*] perccli64..."
    if [ -f "${_tools}/perccli_7.1-007.0127_linux.tar.gz" ]; then
        mkdir -p "${_tmp}/perc_src"
        tar -xzf "${_tools}/perccli_7.1-007.0127_linux.tar.gz" -C "${_tmp}/perc_src" 2>/dev/null || true
        local _perc_rpm
        _perc_rpm=$(find "${_tmp}/perc_src" -name "*.rpm" 2>/dev/null | head -1)
        if [ -n "${_perc_rpm}" ]; then
            if (cd "${_tmp}/perc_src" && alien --scripts -i "${_perc_rpm}" 2>&1); then
                cp -f /opt/MegaRAID/perccli/perccli64 /usr/sbin/perccli
                echo "  [✔] perccli64 -> /usr/sbin/perccli"
            else
                echo "  [✗] perccli64: alien install failed"
            fi
        else
            echo "  [✗] perccli64: RPM not found in archive"
        fi
    else
        echo "  [✗] perccli64: archive not found at ${_tools}/perccli_7.1-007.0127_linux.tar.gz"
    fi
    fi  # TOOLSET perccli

    echo ""
    echo "Done. Run: b2ctl check"
}

if [[ $EUID -ne 0 ]]; then
    echo "run as root: sudo ./install.sh" >&2
    exit 1
fi

echo "[*] installing b2ctl package -> ${PREFIX}"
mkdir -p "${PREFIX}"
mkdir -p /var/log/b2ctl/snapshots
cp -r "${SRC_DIR}/b2ctl" "${PREFIX}/"
cp "${SRC_DIR}/ssd_spec.json" "${PREFIX}/"
[ -f "${SRC_DIR}/bay_map.json" ] && cp "${SRC_DIR}/bay_map.json" "${PREFIX}/"

echo "[*] writing launcher -> ${LAUNCHER}"
cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="${PREFIX}" python3 -m b2ctl "\$@"
EOF
chmod +x "${LAUNCHER}"

# sas2ircu (IT/HBA mode) is a 32-bit ELF needing libc6-i386. A RAID-only
# (--perc) install uses perccli for bays/LEDs, so skip the sas2ircu bits there.
if [ "${SET_MODE}" != "raid" ]; then
    echo "[*] installing sas2ircu runtime dependency (32-bit ELF needs libc6-i386)"
    dpkg --add-architecture i386 >/dev/null 2>&1 || true
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y libc6-i386 >/dev/null 2>&1 || \
        echo "  [!] libc6-i386 install failed — sas2ircu will not execute"
fi

echo "[*] checking dependencies"
for bin in smartctl zpool lsblk; do
    command -v "$bin" >/dev/null 2>&1 || echo "  [!] missing: $bin"
done
if [ "${SET_MODE}" = "raid" ]; then
    command -v perccli >/dev/null 2>&1 ||
        echo "  [i] perccli will be installed below (RAID mode)"
else
    command -v sas2ircu >/dev/null 2>&1 ||
        echo "  [!] sas2ircu missing — bay numbers disabled (IT mode); install with: ./install.sh --flash"
fi
command -v ledctl >/dev/null 2>&1 ||
    echo "  [i] optional: apt install ledmon  (nicer locate LEDs; dd fallback works without it)"

echo "[+] done. try:  sudo b2ctl status   |   sudo b2ctl watch"

if [ "${WITH_TOOLS}" = "1" ]; then
    _DL_TMP=$(mktemp -d)
    if download_tools "${_DL_TMP}"; then
        install_tools "${_DL_TMP}"
    else
        echo "  [✗] download failed — aborting tool install" >&2
    fi
    rm -rf "${_DL_TMP}"
fi

# Profile (--perc/--flash) also sets the controller mode in config.json.
if [ -n "${SET_MODE}" ]; then
    if PYTHONPATH="${PREFIX}" python3 -c \
        "import b2ctl.config as c; c.set_mode('${SET_MODE}')" 2>/dev/null; then
        echo "[+] controller.mode = ${SET_MODE}  (/etc/b2ctl/config.json)"
    else
        echo "  [!] failed to set controller.mode=${SET_MODE}" >&2
    fi
fi
