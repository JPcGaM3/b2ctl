#!/usr/bin/env bash
# b2ctl (IT-mode / HBA build) installer
# Installs the package under /opt/b2ctl and a launcher at /usr/local/sbin/b2ctl.
set -euo pipefail

PREFIX="/opt/b2ctl"
LAUNCHER="/usr/local/sbin/b2ctl"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# storcli dropped: LSI tool, blind to a Dell PERC, only caused false RAID
# detection. RAID = perccli, IT = sas2ircu.
# Single source: read the Drive IDs/base from the packaged installer.py so the
# two download paths can't drift (F-122); fall back to hardcoded values if the
# python read fails (install.sh must work before Python deps are confirmed).
_gid() { PYTHONPATH="${SRC_DIR}" python3 -c \
    "import b2ctl.installer as i; print(i._GDRIVE['$1'] if '$1' in i._GDRIVE else i._BASE)" \
    2>/dev/null; }
_GDRIVE_SAS2IRCU="$(_gid sas2ircu)"; : "${_GDRIVE_SAS2IRCU:=1rP7f8weCvXEaqWSAj5MDNwMDvK2RXTCt}"
_GDRIVE_PERCCLI="$(_gid perccli)";   : "${_GDRIVE_PERCCLI:=1hJt5Sr2xNW4OHCD-AoefiHhjJCeWVWVk}"
_GDRIVE_BASE="$(_gid _base)"
: "${_GDRIVE_BASE:=https://drive.usercontent.google.com/download?export=download&confirm=t&id=}"

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
    # Reject unknown flags instead of silently ignoring a typo like --percc,
    # which otherwise runs a plain install while the operator thinks --perc
    # applied (F-109). Mirrors `b2ctl install`'s argparse rejection.
    *) echo "unknown option: ${_arg}" >&2
       echo "usage: ./install.sh [--with-tools|--perc|--flash]" >&2
       exit 2 ;;
    esac
done

# Temp dirs are created below; a single EXIT trap removes them on ANY exit path
# (success, error under set -e, or Ctrl-C), so a failed apt/cp can no longer
# leak /tmp dirs or skip the mode write (F-064).
_DL_TMP=""
_TOOLS_TMP=""
cleanup() { rm -rf "${_DL_TMP:-}" "${_TOOLS_TMP:-}"; }
trap cleanup EXIT

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
        # Magic-byte check: a multi-KB Google quota/HTML page passes the size test
        # but is not a real archive (F-110). zip = PK\x03\x04, gzip = \x1f\x8b.
        local _magic
        _magic=$(head -c2 "${_out}" | od -An -tx1 | tr -d ' \n')
        case "${_out}" in
        *.zip)    [ "${_magic}" = "504b" ] || { echo "  [✗] $(basename "${_out}"): not a zip (got magic ${_magic})" >&2; return 1; } ;;
        *.tar.gz) [ "${_magic}" = "1f8b" ] || { echo "  [✗] $(basename "${_out}"): not a gzip (got magic ${_magic})" >&2; return 1; } ;;
        esac
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
    _TOOLS_TMP=$(mktemp -d)          # global so the EXIT trap cleans it (F-064)
    local _tmp="${_TOOLS_TMP}"

    echo ""
    echo "=== Installing tool binaries ==="
    # Per-tool prereqs: i386/libc6-i386/unzip only for the 32-bit sas2ircu, alien
    # only for the perccli rpm — so `--perc` never registers i386 on a RAID box
    # (F-111). Runtime deps (smart/zfs/gdisk) are always useful. Mirrors
    # installer.ensure_prereqs' tiers (F-087).
    local _pkgs="smartmontools zfsutils-linux gdisk util-linux coreutils udev"
    if [[ " ${TOOLSET} " == *" sas2ircu "* ]]; then
        dpkg --add-architecture i386 2>/dev/null || true
        _pkgs="${_pkgs} libc6-i386 unzip"
    fi
    if [[ " ${TOOLSET} " == *" perccli "* ]]; then
        _pkgs="${_pkgs} alien"
    fi
    apt-get update -qq 2>/dev/null || true
    # Guarded: a prereq failure (unreachable mirror on a Proxmox box) must not
    # abort the whole script under set -e (F-064).
    # shellcheck disable=SC2086
    apt-get install -y ${_pkgs} \
        || echo "  [!] apt: some prerequisites failed to install (offline mirror?) — tool install may be incomplete"

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
# Replace the package dir (don't merge): `cp -r` into an existing tree leaves
# upstream-removed modules importable forever and re-runs are non-idempotent
# (F-112). Then drop any dev-machine __pycache__ that tagged along.
rm -rf "${PREFIX}/b2ctl"
cp -r "${SRC_DIR}/b2ctl" "${PREFIX}/"
find "${PREFIX}/b2ctl" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp "${SRC_DIR}/ssd_spec.json" "${PREFIX}/"
[ -f "${SRC_DIR}/bay_map.json" ] && cp "${SRC_DIR}/bay_map.json" "${PREFIX}/"

echo "[*] writing launcher -> ${LAUNCHER}"
cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
# PYTHONSAFEPATH=1 stops python -m prepending the cwd to sys.path, so running
# b2ctl from a directory containing a b2ctl/ package (e.g. the repo checkout)
# cannot shadow this install. Honored on Python >=3.11, ignored on older.
exec env PYTHONPATH="${PREFIX}" PYTHONSAFEPATH=1 python3 -m b2ctl "\$@"
EOF
chmod +x "${LAUNCHER}"

# A plain `./install.sh` installs ONLY the b2ctl package — no apt, no downloads.
# Tool runtime deps (libc6-i386 for the 32-bit sas2ircu, alien for perccli) are
# installed by install_tools() further down, and only when a tool is actually
# being installed (--with-tools / --perc / --flash).

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
    # temp dirs are removed by the EXIT trap
fi

# Profile (--perc/--flash) sets the controller mode — but only if the required
# tool is actually present, mirroring installer.py's F-045 gate: forcing
# mode=raid with no perccli would break every later status/watch run.
if [ -n "${SET_MODE}" ]; then
    _mode_tool="sas2ircu"
    [ "${SET_MODE}" = "raid" ] && _mode_tool="perccli"
    if ! command -v "${_mode_tool}" >/dev/null 2>&1; then
        echo "  [!] controller.mode left unchanged — ${_mode_tool} not installed; "\
"fix the tool install then re-run, or set controller.mode by hand." >&2
    elif PYTHONPATH="${PREFIX}" python3 -c \
        "import b2ctl.config as c; c.set_mode('${SET_MODE}')" 2>/dev/null; then
        echo "[+] controller.mode = ${SET_MODE}  (/etc/b2ctl/config.json)"
    else
        echo "  [!] failed to set controller.mode=${SET_MODE}" >&2
    fi
fi
