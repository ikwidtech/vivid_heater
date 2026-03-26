#!/usr/bin/env bash
# install.sh — ViViD Dryer Klipper installer
#
# Quick install:
#   curl -fsSL https://raw.githubusercontent.com/ikwidtech/vivid_heater/main/install.sh | bash
#
# Manual:
#   bash install.sh [options]
#
# Options:
#   --instance      Instance name to configure (default: Vivid_1)
#   --klipper-dir   Path to Klipper source checkout (default: ~/klipper)
#   --config-dir    Path to Klipper config directory (auto-detected)
#   --mainsail-dir  Path to Mainsail www directory (auto-detected)
#   --help          Show this help and exit

set -euo pipefail

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }
die()   { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Default options
# ---------------------------------------------------------------------------
INSTANCE="Vivid_1"
KLIPPER_DIR="${HOME}/klipper"
CONFIG_DIR=""
MAINSAIL_DIR=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance)     INSTANCE="$2";     shift 2 ;;
    --klipper-dir)  KLIPPER_DIR="$2";  shift 2 ;;
    --config-dir)   CONFIG_DIR="$2";   shift 2 ;;
    --mainsail-dir) MAINSAIL_DIR="$2"; shift 2 ;;
    --help|-h)
      sed -n '/^# Options:/,/^[^#]/p' "$0" | head -n -1 | sed 's/^# //'
      exit 0 ;;
    *) die "Unknown option: $1.  Run '$0 --help' for usage." ;;
  esac
done

# ---------------------------------------------------------------------------
# Auto-detect config directory
# ---------------------------------------------------------------------------
if [[ -z "$CONFIG_DIR" ]]; then
  for candidate in \
      "${HOME}/printer_data/config" \
      "${HOME}/klipper_config" \
      "${HOME}/config"; do
    if [[ -d "$candidate" ]]; then
      CONFIG_DIR="$candidate"
      break
    fi
  done
fi
if [[ -z "$CONFIG_DIR" ]]; then
  die "Could not auto-detect Klipper config directory. Use --config-dir."
fi

# ---------------------------------------------------------------------------
# Auto-detect Mainsail directory
# ---------------------------------------------------------------------------
if [[ -z "$MAINSAIL_DIR" ]]; then
  for candidate in \
      "${HOME}/mainsail" \
      "/var/www/mainsail" \
      "${HOME}/printer_data/config/mainsail"; do
    if [[ -d "$candidate" ]]; then
      MAINSAIL_DIR="$candidate"
      break
    fi
  done
fi

# ---------------------------------------------------------------------------
# Determine script directory (works when piped from curl too)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo "$(pwd)")"

# ---------------------------------------------------------------------------
# Validate Klipper directory
# ---------------------------------------------------------------------------
if [[ ! -d "${KLIPPER_DIR}/klippy/extras" ]]; then
  die "Klipper extras directory not found at '${KLIPPER_DIR}/klippy/extras'. Use --klipper-dir."
fi

# ---------------------------------------------------------------------------
# Resolve repo root (handles both local run and curl-piped run via tmpdir)
# ---------------------------------------------------------------------------
REPO_ROOT="$SCRIPT_DIR"
if [[ ! -f "${REPO_ROOT}/klippy/extras/vivid_dryer.py" ]]; then
  # Likely running from a temp location (curl | bash). Clone/fetch source.
  TMP_REPO="$(mktemp -d)"
  info "Cloning vivid_heater repository into ${TMP_REPO} ..."
  git clone --depth=1 https://github.com/ikwidtech/vivid_heater.git "$TMP_REPO"
  REPO_ROOT="$TMP_REPO"
fi

# ---------------------------------------------------------------------------
# 1. Copy Klipper extra
# ---------------------------------------------------------------------------
info "Installing vivid_dryer.py → ${KLIPPER_DIR}/klippy/extras/"
cp "${REPO_ROOT}/klippy/extras/vivid_dryer.py" "${KLIPPER_DIR}/klippy/extras/vivid_dryer.py"
info "vivid_dryer.py installed."

# ---------------------------------------------------------------------------
# 2. Copy config files (skip if already present, warn user)
# ---------------------------------------------------------------------------
copy_config() {
  local src="$1" dst="$2" label="$3"
  if [[ -f "$dst" ]]; then
    warn "${label} already exists at '${dst}' — skipping (manual merge may be needed)."
  else
    cp "$src" "$dst"
    info "${label} installed → ${dst}"
  fi
}

mkdir -p "${CONFIG_DIR}/mainsail"

copy_config \
  "${REPO_ROOT}/config/vivid_dryer.cfg" \
  "${CONFIG_DIR}/vivid_dryer.cfg" \
  "vivid_dryer.cfg"

copy_config \
  "${REPO_ROOT}/config/idle_timeout_guard.cfg" \
  "${CONFIG_DIR}/idle_timeout_guard.cfg" \
  "idle_timeout_guard.cfg"

copy_config \
  "${REPO_ROOT}/mainsail/vivid_dryer_panel.cfg" \
  "${CONFIG_DIR}/mainsail/vivid_dryer_panel.cfg" \
  "vivid_dryer_panel.cfg"

# ---------------------------------------------------------------------------
# 3. Copy HTML widget to Mainsail directory (if found)
# ---------------------------------------------------------------------------
if [[ -n "$MAINSAIL_DIR" && -d "$MAINSAIL_DIR" ]]; then
  cp "${REPO_ROOT}/mainsail/vivid_dryer.html" "${MAINSAIL_DIR}/vivid_dryer.html"
  info "Mainsail widget installed → ${MAINSAIL_DIR}/vivid_dryer.html"
else
  warn "Mainsail directory not found — skipping widget install."
  warn "Copy mainsail/vivid_dryer.html manually to your Mainsail www folder."
fi

# ---------------------------------------------------------------------------
# 4. Print next steps
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ViViD Dryer installed successfully!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Verify your printer.cfg defines (DO NOT add to vivid_dryer.cfg):"
echo "       [heater_generic Vivid_1_dryer]"
echo "       [aht10 Vivid_1_dryer_left]"
echo "       [aht10 Vivid_1_dryer_right]"
echo ""
echo "  2. Add to printer.cfg:"
echo "       [include vivid_dryer.cfg]"
echo "       [include mainsail/vivid_dryer_panel.cfg]   # optional"
echo "       [include idle_timeout_guard.cfg]           # optional"
echo ""
echo "  3. If using idle_timeout_guard.cfg, also add:"
echo "       [idle_timeout]"
echo "       gcode:"
echo "         _VIVID_IDLE_TIMEOUT_CHECK"
echo "       timeout: 600"
echo ""
if [[ -n "$MAINSAIL_DIR" && -d "$MAINSAIL_DIR" ]]; then
  echo "  4. Open the Mainsail widget:"
  echo "       http://<your-printer-ip>/vivid_dryer.html"
  echo ""
fi
echo "  5. Restart Klipper to apply changes."
echo ""

# ---------------------------------------------------------------------------
# 5. Optionally restart Klipper
# ---------------------------------------------------------------------------
if [[ -t 0 ]]; then   # only prompt when running interactively
  read -rp "  Restart Klipper now? [y/N] " ans
  if [[ "${ans,,}" == "y" ]]; then
    if systemctl is-active --quiet klipper 2>/dev/null; then
      info "Restarting klipper service ..."
      sudo systemctl restart klipper
      info "Klipper restarted."
    else
      warn "klipper systemd service not found. Restart Klipper manually."
    fi
  else
    info "Skipping Klipper restart. Run 'sudo systemctl restart klipper' when ready."
  fi
fi
