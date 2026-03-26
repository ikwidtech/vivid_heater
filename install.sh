#!/bin/bash
# ViViD Dryer — auto-installer for Klipper
# Usage: bash install.sh [--instance NAME] [--klipper-dir DIR]
#                        [--config-dir DIR] [--mainsail-dir DIR]
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ------------------------------------------------------------------ #
# Defaults                                                            #
# ------------------------------------------------------------------ #
INSTANCE="Vivid_1"
KLIPPER_DIR="$HOME/klipper"
CONFIG_DIR=""
MAINSAIL_DIR=""

# ------------------------------------------------------------------ #
# Argument parsing                                                    #
# ------------------------------------------------------------------ #
while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance)    INSTANCE="$2";    shift 2 ;;
    --klipper-dir) KLIPPER_DIR="$2"; shift 2 ;;
    --config-dir)  CONFIG_DIR="$2";  shift 2 ;;
    --mainsail-dir) MAINSAIL_DIR="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash install.sh [--instance NAME] [--klipper-dir DIR]"
      echo "                       [--config-dir DIR] [--mainsail-dir DIR]"
      exit 0
      ;;
    *) die "Unknown argument: $1" ;;
  esac
done

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     ViViD Dryer — Klipper Installer      ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ------------------------------------------------------------------ #
# Step 1 — Validate Klipper extras dir                               #
# ------------------------------------------------------------------ #
EXTRAS_DIR="$KLIPPER_DIR/klippy/extras"
if [[ ! -d "$EXTRAS_DIR" ]]; then
  die "Klipper extras dir not found: $EXTRAS_DIR\n  Use --klipper-dir to specify your Klipper installation."
fi
success "Klipper extras dir: $EXTRAS_DIR"

# ------------------------------------------------------------------ #
# Step 2 — Auto-detect config dir                                     #
# ------------------------------------------------------------------ #
if [[ -z "$CONFIG_DIR" ]]; then
  for candidate in \
      "$HOME/printer_data/config" \
      "$HOME/klipper_config" \
      "/etc/klipper"; do
    if [[ -d "$candidate" ]]; then
      CONFIG_DIR="$candidate"
      break
    fi
  done
fi

if [[ -z "$CONFIG_DIR" ]]; then
  die "Could not auto-detect Klipper config directory.\n  Use --config-dir to specify the path."
fi
success "Config dir: $CONFIG_DIR"

# ------------------------------------------------------------------ #
# Step 3 — Auto-detect Mainsail dir (optional)                       #
# ------------------------------------------------------------------ #
if [[ -z "$MAINSAIL_DIR" ]]; then
  for candidate in \
      "$HOME/mainsail" \
      "/var/www/mainsail" \
      "/var/www/html/mainsail"; do
    if [[ -d "$candidate" ]]; then
      MAINSAIL_DIR="$candidate"
      break
    fi
  done
fi

if [[ -n "$MAINSAIL_DIR" ]]; then
  success "Mainsail dir: $MAINSAIL_DIR"
else
  warn "Mainsail dir not found — skipping HTML widget install."
  warn "Use --mainsail-dir to install manually."
fi

echo ""

# ------------------------------------------------------------------ #
# Step 4 — Copy Klipper Python extra                                  #
# ------------------------------------------------------------------ #
SRC_PY="$SCRIPT_DIR/klippy/extras/vivid_dryer.py"
DST_PY="$EXTRAS_DIR/vivid_dryer.py"

if [[ ! -f "$SRC_PY" ]]; then
  die "Source file not found: $SRC_PY"
fi
cp "$SRC_PY" "$DST_PY"
success "Installed: $DST_PY"

# ------------------------------------------------------------------ #
# Step 5 — Copy vivid_dryer.cfg                                       #
# ------------------------------------------------------------------ #
SRC_CFG="$SCRIPT_DIR/config/vivid_dryer.cfg"
DST_CFG="$CONFIG_DIR/vivid_dryer.cfg"

if [[ ! -f "$SRC_CFG" ]]; then
  die "Source file not found: $SRC_CFG"
fi
if [[ -f "$DST_CFG" ]]; then
  warn "Config already exists, skipping: $DST_CFG"
else
  cp "$SRC_CFG" "$DST_CFG"
  success "Installed: $DST_CFG"
fi

# ------------------------------------------------------------------ #
# Step 6 — Copy idle_timeout_guard.cfg                                #
# ------------------------------------------------------------------ #
SRC_IDLE="$SCRIPT_DIR/config/idle_timeout_guard.cfg"
DST_IDLE="$CONFIG_DIR/vivid_idle_timeout_guard.cfg"

if [[ -f "$SRC_IDLE" ]]; then
  if [[ -f "$DST_IDLE" ]]; then
    warn "Config already exists, skipping: $DST_IDLE"
  else
    cp "$SRC_IDLE" "$DST_IDLE"
    success "Installed: $DST_IDLE"
  fi
fi

# ------------------------------------------------------------------ #
# Step 7 — Copy vivid_dryer_panel.cfg                                 #
# ------------------------------------------------------------------ #
SRC_PANEL="$SCRIPT_DIR/mainsail/vivid_dryer_panel.cfg"
DST_PANEL="$CONFIG_DIR/vivid_dryer_panel.cfg"

if [[ -f "$SRC_PANEL" ]]; then
  if [[ -f "$DST_PANEL" ]]; then
    warn "Config already exists, skipping: $DST_PANEL"
  else
    cp "$SRC_PANEL" "$DST_PANEL"
    success "Installed: $DST_PANEL"
  fi
fi

# ------------------------------------------------------------------ #
# Step 8 — Copy HTML widget (optional)                                #
# ------------------------------------------------------------------ #
if [[ -n "$MAINSAIL_DIR" ]]; then
  SRC_HTML="$SCRIPT_DIR/mainsail/vivid_dryer.html"
  DST_HTML="$MAINSAIL_DIR/vivid_dryer.html"
  if [[ -f "$SRC_HTML" ]]; then
    cp "$SRC_HTML" "$DST_HTML"
    success "Installed: $DST_HTML"
  fi
fi

# ------------------------------------------------------------------ #
# Step 9 — Next-steps instructions                                    #
# ------------------------------------------------------------------ #
echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo ""
echo "  1. Add this include to your printer.cfg:"
echo ""
echo "       [include vivid_dryer.cfg]"
echo ""
echo "  2. Optionally add Mainsail macro panel and idle guard:"
echo ""
echo "       [include vivid_dryer_panel.cfg]"
echo "       [include vivid_idle_timeout_guard.cfg]"
echo ""
echo "  3. If using the idle guard, add to printer.cfg:"
echo ""
echo "       [idle_timeout]"
echo "       gcode:"
echo "         _VIVID_IDLE_TIMEOUT_CHECK"
echo "       timeout: 600"
echo ""
echo "  4. Edit $CONFIG_DIR/vivid_dryer.cfg"
echo "     to confirm the heater and sensor names match your setup."
echo ""
if [[ -n "$MAINSAIL_DIR" ]]; then
  echo "  5. Open the Mainsail widget in your browser:"
  echo "     http://<your-printer-ip>/vivid_dryer.html"
  echo ""
fi
echo -e "${YELLOW}GCode commands for instance '$INSTANCE':${NC}"
SUFFIX="$(echo "$INSTANCE" | tr '[:lower:]' '[:upper:]')"
echo ""
echo "  VIVID_DRY_START_${SUFFIX} TEMP=55 HOURS=4"
echo "  VIVID_DRY_START_${SUFFIX} HUMIDITY=30 TEMP_MAX=55 TEMP_MIN=35"
echo "  VIVID_DRY_STOP_${SUFFIX}"
echo "  VIVID_DRY_STATUS_${SUFFIX}"
echo ""

# ------------------------------------------------------------------ #
# Step 10 — Prompt to restart Klipper                                 #
# ------------------------------------------------------------------ #
if [[ -t 0 ]]; then
  read -r -p "Restart Klipper now? (y/N): " REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    if command -v systemctl &>/dev/null && systemctl is-active --quiet klipper 2>/dev/null; then
      info "Restarting Klipper via systemctl..."
      sudo systemctl restart klipper
      success "Klipper restarted."
    else
      warn "systemctl not found or Klipper service not active."
      warn "Please restart Klipper manually."
    fi
  else
    warn "Klipper not restarted. Run 'sudo systemctl restart klipper' when ready."
  fi
fi
