#!/usr/bin/env bash
# b2ctl (IT-mode / HBA build) installer
# Installs the package under /opt/b2ctl and a launcher at /usr/local/sbin/b2ctl.
set -euo pipefail

PREFIX="/opt/b2ctl"
LAUNCHER="/usr/local/sbin/b2ctl"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="$(dirname "${SRC_DIR}")/tools"

_GDRIVE_SAS2IRCU="1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt"
_GDRIVE_STORCLI="1u9x1RCsnz2VaAt6NxEE90mJJdCgps_60"
_GDRIVE_PERCCLI="1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk"
_GDRIVE_BASE="https://drive.usercontent.google.com/download?export=download&confirm=t&id="

WITH_TOOLS=0
for _arg in "$@"; do
    case "$_arg" in
    --with-tools) WITH_TOOLS=1 ;;
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

    _gdrive_get "${_GDRIVE_SAS2IRCU}" "${_dest}/SAS2IRCU_P20.zip"          || return 1
    _gdrive_get "${_GDRIVE_STORCLI}"  "${_dest}/007.3703.0000.0000_MR 7.37_Storcli.zip" || return 1
    _gdrive_get "${_GDRIVE_PERCCLI}"  "${_dest}/perccli_7.1-007.0127_linux.tar.gz"       || return 1
}

install_tools() {
    local _tools="$1"
    local _tmp
    _tmp=$(mktemp -d)
    trap "rm -rf '${_tmp}'" EXIT

    echo ""
    echo "=== Installing tool binaries ==="
    apt-get install -y alien unzip smartmontools zfsutils-linux gdisk util-linux coreutils udev

    # ── sas2ircu ──────────────────────────────────────────────────────────────
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
            cp "${_sas}" /usr/local/sbin/sas2ircu
            chmod +x /usr/local/sbin/sas2ircu
            echo "  [✔] sas2ircu -> /usr/local/sbin/sas2ircu"
        else
            echo "  [✗] sas2ircu: binary not found in archive"
        fi
    else
        echo "  [✗] sas2ircu: archive not found at ${_tools}/SAS2IRCU_P20.zip"
    fi

    # ── storcli64 ─────────────────────────────────────────────────────────────
    echo "[*] storcli64..."
    local _stor_zip="${_tools}/007.3703.0000.0000_MR 7.37_Storcli.zip"
    if [ -f "${_stor_zip}" ]; then
        unzip -q "${_stor_zip}" -d "${_tmp}/stor_outer" || true
        unzip -q "${_tmp}/stor_outer/storcli_rel/Unified_storcli_all_os.zip" \
            -d "${_tmp}/stor_inner" 2>/dev/null || true
        local _stor_deb="${_tmp}/stor_inner/Unified_storcli_all_os/Ubuntu/storcli_007.3703.0000.0000_all.deb"
        if [ -f "${_stor_deb}" ]; then
            dpkg-deb -x "${_stor_deb}" "${_tmp}/stor_bin" 2>/dev/null || true
            local _stor
            _stor=$(find "${_tmp}/stor_bin" -name "storcli64" -type f 2>/dev/null | head -1)
            if [ -n "${_stor}" ]; then
                cp "${_stor}" /usr/local/sbin/storcli64
                chmod +x /usr/local/sbin/storcli64
                ln -sf /usr/local/sbin/storcli64 /usr/local/sbin/storcli
                echo "  [✔] storcli64 -> /usr/local/sbin/storcli64"
            else
                echo "  [✗] storcli64: binary not found in DEB"
            fi
        else
            echo "  [✗] storcli64: Ubuntu DEB not found in inner archive"
        fi
    else
        echo "  [✗] storcli64: archive not found"
    fi

    # ── perccli64 ─────────────────────────────────────────────────────────────
    echo "[*] perccli64..."
    if [ -f "${_tools}/perccli_7.1-007.0127_linux.tar.gz" ]; then
        mkdir -p "${_tmp}/perc_src"
        tar -xzf "${_tools}/perccli_7.1-007.0127_linux.tar.gz" -C "${_tmp}/perc_src" 2>/dev/null || true
        local _perc_rpm
        _perc_rpm=$(find "${_tmp}/perc_src" -name "*.rpm" 2>/dev/null | head -1)
        if [ -n "${_perc_rpm}" ]; then
            (cd "${_tmp}/perc_src" && alien --to-deb "${_perc_rpm}" 2>/dev/null) || true
            local _perc_deb
            _perc_deb=$(find "${_tmp}/perc_src" -name "*.deb" 2>/dev/null | head -1)
            if [ -n "${_perc_deb}" ]; then
                dpkg-deb -x "${_perc_deb}" "${_tmp}/perc_bin" 2>/dev/null || true
                local _perc
                _perc=$(find "${_tmp}/perc_bin" \( -name "perccli64" -o -name "perccli" \) -type f 2>/dev/null | head -1)
                if [ -n "${_perc}" ]; then
                    cp "${_perc}" /usr/local/sbin/perccli64
                    chmod +x /usr/local/sbin/perccli64
                    ln -sf /usr/local/sbin/perccli64 /usr/local/sbin/perccli
                    echo "  [✔] perccli64 -> /usr/local/sbin/perccli64"
                else
                    echo "  [✗] perccli64: binary not found in converted DEB"
                fi
            else
                echo "  [✗] perccli64: alien conversion produced no DEB"
            fi
        else
            echo "  [✗] perccli64: RPM not found in archive"
        fi
    else
        echo "  [✗] perccli64: archive not found at ${_tools}/perccli_7.1-007.0127_linux.tar.gz"
    fi

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
exec env PYTHONPATH="${PREFIX}" python3 -P -m b2ctl "\$@"
EOF
chmod +x "${LAUNCHER}"

echo "[*] checking dependencies"
for bin in smartctl zpool lsblk; do
    command -v "$bin" >/dev/null 2>&1 || echo "  [!] missing: $bin"
done
command -v sas2ircu >/dev/null 2>&1 ||
    echo "  [!] sas2ircu missing — bay numbers will be disabled"
command -v ledctl >/dev/null 2>&1 ||
    echo "  [i] optional: apt install ledmon  (nicer locate LEDs; dd fallback works without it)"

echo "[+] done. try:  sudo b2ctl status   |   sudo b2ctl watch"

if [ "${WITH_TOOLS}" = "1" ]; then
    _DL_TMP=$(mktemp -d)
    trap "rm -rf '${_DL_TMP}'" EXIT
    if download_tools "${_DL_TMP}"; then
        install_tools "${_DL_TMP}"
    else
        echo "  [✗] download failed — aborting tool install" >&2
    fi
fi
