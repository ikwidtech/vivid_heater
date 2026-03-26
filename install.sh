#!/bin/bash
# ViViD Dryer — Auto-installer for Klipper + Mainsail
# Usage: bash install.sh [--instance Vivid_1] [--klipper-dir ~/klipper]
#                        [--config-dir ~/printer_data/config]
#                        [--mainsail-dir ~/mainsail]

set -euo pipefail

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
YEL='\033[0;33m'
GRN='\033[0;32m'
RST='\033[0m'

info()  { echo -e "${GRN}[✓]${RST} $*"; }
warn()  { echo -e "${YEL}[!]${RST} $*"; }
error() { echo -e "${RED}[✗]${RST} $*" >&2; }
die()   { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
INSTANCE="Vivid_1"
KLIPPER_DIR="${HOME}/klipper"
CONFIG_DIR=""
MAINSAIL_DIR=""

# ---------------------------------------------------------------------------
# Parse CLI args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance)     INSTANCE="$2";    shift 2 ;;
    --klipper-dir)  KLIPPER_DIR="$2"; shift 2 ;;
    --config-dir)   CONFIG_DIR="$2";  shift 2 ;;
    --mainsail-dir) MAINSAIL_DIR="$2";shift 2 ;;
    *)
      error "Unknown argument: $1"
      echo "Usage: $0 [--instance Vivid_1] [--klipper-dir ~/klipper] [--config-dir ~/printer_data/config] [--mainsail-dir ~/mainsail]"
      exit 1
      ;;
  esac
done

# Resolve ~ in paths
KLIPPER_DIR="${KLIPPER_DIR/#\~/$HOME}"
CONFIG_DIR="${CONFIG_DIR/#\~/$HOME}"
MAINSAIL_DIR="${MAINSAIL_DIR/#\~/$HOME}"

# ---------------------------------------------------------------------------
# Locate script directory (repo root)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Validate Klipper dir
# ---------------------------------------------------------------------------
EXTRAS_DIR="${KLIPPER_DIR}/klippy/extras"
if [[ ! -d "$EXTRAS_DIR" ]]; then
  die "Klipper extras directory not found: ${EXTRAS_DIR}\n    Install Klipper first, or pass --klipper-dir /path/to/klipper"
fi
info "Found Klipper extras: ${EXTRAS_DIR}"

# ---------------------------------------------------------------------------
# Auto-detect config dir
# ---------------------------------------------------------------------------
if [[ -z "$CONFIG_DIR" ]]; then
  for candidate in \
      "${HOME}/printer_data/config" \
      "${HOME}/klipper_config" \
      "/etc/klipper"; do
    if [[ -d "$candidate" ]]; then
      CONFIG_DIR="$candidate"
      break
    fi
  done
fi
if [[ -z "$CONFIG_DIR" || ! -d "$CONFIG_DIR" ]]; then
  die "Could not find Klipper config directory. Pass --config-dir /path/to/config"
fi
info "Using config directory: ${CONFIG_DIR}"

# ---------------------------------------------------------------------------
# Auto-detect Mainsail dir
# ---------------------------------------------------------------------------
if [[ -z "$MAINSAIL_DIR" ]]; then
  for candidate in "${HOME}/mainsail" "/var/www/mainsail"; do
    if [[ -d "$candidate" ]]; then
      MAINSAIL_DIR="$candidate"
      break
    fi
  done
fi
if [[ -n "$MAINSAIL_DIR" && -d "$MAINSAIL_DIR" ]]; then
  info "Found Mainsail directory: ${MAINSAIL_DIR}"
else
  warn "Mainsail directory not found — skipping widget install"
  MAINSAIL_DIR=""
fi

# ---------------------------------------------------------------------------
# Helper: copy a file, skip if destination already exists
# ---------------------------------------------------------------------------
copy_file() {
  local src="$1"
  local dst="$2"
  local label="${3:-$(basename "$dst")}"

  if [[ ! -f "$src" ]]; then
    warn "Source file not found, skipping: ${src}"
    return
  fi

  if [[ -f "$dst" ]]; then
    warn "${label}: already exists, skipping (${dst})"
  else
    cp "$src" "$dst"
    info "Installed ${label} → ${dst}"
  fi
}

# ---------------------------------------------------------------------------
# 1. Install Python extra (always overwrite — safe to re-run)
# ---------------------------------------------------------------------------
cp "${SCRIPT_DIR}/klippy/extras/vivid_dryer.py" \
   "${EXTRAS_DIR}/vivid_dryer.py"
info "Installed vivid_dryer.py → ${EXTRAS_DIR}/vivid_dryer.py"

# ---------------------------------------------------------------------------
# 2. Config files (skip if already present)
# ---------------------------------------------------------------------------
copy_file "${SCRIPT_DIR}/config/vivid_dryer.cfg" \
          "${CONFIG_DIR}/vivid_dryer.cfg" \
          "vivid_dryer.cfg"

copy_file "${SCRIPT_DIR}/config/idle_timeout_guard.cfg" \
          "${CONFIG_DIR}/vivid_idle_timeout_guard.cfg" \
          "vivid_idle_timeout_guard.cfg"

copy_file "${SCRIPT_DIR}/mainsail/vivid_dryer_panel.cfg" \
          "${CONFIG_DIR}/vivid_dryer_panel.cfg" \
          "vivid_dryer_panel.cfg"

# ---------------------------------------------------------------------------
# 3. Mainsail widget
# ---------------------------------------------------------------------------
if [[ -n "$MAINSAIL_DIR" ]]; then
  cp "${SCRIPT_DIR}/mainsail/vivid_dryer.html" \
     "${MAINSAIL_DIR}/vivid_dryer.html"
  info "Installed Mainsail widget → ${MAINSAIL_DIR}/vivid_dryer.html"
fi

# ---------------------------------------------------------------------------
# 4. Next steps
# ---------------------------------------------------------------------------
MAINSAIL_URL="http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'your-printer-ip')/vivid_dryer.html  (or https:// if using SSL)"

echo ""
echo -e "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
echo -e "${GRN}  ViViD Dryer installation complete!${RST}"
echo -e "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RST}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Add to your printer.cfg:"
echo "       [include vivid_dryer.cfg]"
echo "       [include vivid_dryer_panel.cfg]"
echo ""
echo "  2. (Optional) idle_timeout integration:"
echo "       [include vivid_idle_timeout_guard.cfg]"
echo ""
echo "       Then in your [idle_timeout] section:"
echo "       [idle_timeout]"
echo "       gcode: _VIVID_IDLE_TIMEOUT_CHECK"
echo ""
if [[ -n "$MAINSAIL_DIR" ]]; then
echo "  3. Mainsail widget URL:"
echo "       ${MAINSAIL_URL}"
echo ""
fi
echo "  4. Restart Klipper:"
echo "       sudo systemctl restart klipper"
echo ""

# ---------------------------------------------------------------------------
# 5. Offer to restart Klipper
# ---------------------------------------------------------------------------
echo -n "  Restart Klipper now? [y/N] "
read -r REPLY
case "$REPLY" in
  [Yy]*)
    if command -v systemctl &>/dev/null; then
      sudo systemctl restart klipper
      info "Klipper restarted"
    else
      warn "systemctl not available — please restart Klipper manually"
    fi
    ;;
  *)
    warn "Skipping Klipper restart — remember to restart manually"
    ;;
esac
