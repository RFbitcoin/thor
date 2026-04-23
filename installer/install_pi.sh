#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# THOR Bitcoin Intelligence Dashboard — Raspberry Pi / Linux Installer
# Run with: curl -fsSL https://thor.rfbitcoin.com/dist/install_pi.sh | bash
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

THOR_VERSION="latest"
DIST_URL="https://thor.rfbitcoin.com/dist/thor-latest.zip"
INSTALL_DIR="$HOME/thor"
SERVICE_NAME="thor"
PORT=5000

GOLD='\033[0;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
  echo -e "${GOLD}"
  echo "  ██████████╗ ██╗  ██╗ ██████╗ ██████╗ "
  echo "      ██╔══╝ ██║  ██║██╔═══██╗██╔══██╗"
  echo "      ██║    ███████║██║   ██║██████╔╝"
  echo "      ██║    ██╔══██║██║   ██║██╔══██╗"
  echo "      ██║    ██║  ██║╚██████╔╝██║  ██║"
  echo "      ╚═╝    ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝"
  echo -e "${NC}"
  echo -e "${CYAN}  Bitcoin Intelligence Dashboard — Pi/Linux Installer${NC}"
  echo ""
}

step() { echo -e "${GOLD}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}  ✓ $1${NC}"; }
err()  { echo -e "${RED}  ✗ $1${NC}"; exit 1; }

banner

# ── Check OS ─────────────────────────────────────────────────────────────────
if [[ "$(uname)" != "Linux" ]]; then
  err "This installer is for Linux/Raspberry Pi only."
fi

# ── Check Python 3.11+ ───────────────────────────────────────────────────────
step "Checking Python..."
PYTHON=""
for cmd in python3.11 python3.12 python3.13 python3; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c "import sys; print(sys.version_info >= (3,11))")
    if [[ "$ver" == "True" ]]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  step "Installing Python 3.11..."
  sudo apt-get update -qq
  sudo apt-get install -y python3.11 python3.11-venv python3.11-dev || \
    err "Could not install Python 3.11. Install it manually and re-run."
  PYTHON="python3.11"
fi
ok "Python: $($PYTHON --version)"

# ── Install system deps ───────────────────────────────────────────────────────
step "Installing system dependencies..."
sudo apt-get install -y -qq unzip curl git 2>/dev/null || true
ok "System deps ready"

# ── Download THOR ─────────────────────────────────────────────────────────────
step "Downloading THOR..."
TMP=$(mktemp -d)
curl -fsSL --progress-bar "$DIST_URL" -o "$TMP/thor.zip" || \
  err "Download failed. Check your internet connection."
ok "Downloaded"

# ── Install ───────────────────────────────────────────────────────────────────
step "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
unzip -q -o "$TMP/thor.zip" -d "$INSTALL_DIR"
rm -rf "$TMP"

# ── Create .env if missing ────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cp "$INSTALL_DIR/thor_env_template" "$INSTALL_DIR/.env" 2>/dev/null || \
  cat > "$INSTALL_DIR/.env" << 'ENVEOF'
KRAKEN_API_KEY=
KRAKEN_API_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
THOR_PASSWORD_HASH=
THOR_PORT=5000
ENVEOF
fi
ok "Config created at $INSTALL_DIR/.env"

# ── Python virtual environment ────────────────────────────────────────────────
step "Setting up Python environment..."
$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Dependencies installed"

# ── Systemd service ───────────────────────────────────────────────────────────
step "Registering THOR as a system service..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" > /dev/null << SVCEOF
[Unit]
Description=THOR Bitcoin Intelligence Dashboard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR/dashboard
ExecStart=$INSTALL_DIR/venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
ok "Service started"

# ── Desktop shortcut ──────────────────────────────────────────────────────────
DESKTOP_DIR="$HOME/Desktop"
if [[ -d "$DESKTOP_DIR" ]]; then
  cat > "$DESKTOP_DIR/THOR.desktop" << DESKEOF
[Desktop Entry]
Name=THOR Dashboard
Comment=Bitcoin Intelligence Dashboard
Exec=xdg-open http://localhost:$PORT
Icon=$INSTALL_DIR/dashboard/icon.png
Terminal=false
Type=Application
Categories=Finance;
DESKEOF
  chmod +x "$DESKTOP_DIR/THOR.desktop"
  ok "Desktop shortcut created"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  THOR installed successfully!${NC}"
echo ""
echo -e "  Dashboard:   ${CYAN}http://localhost:$PORT${NC}"
echo -e "  Config:      ${CYAN}$INSTALL_DIR/.env${NC}"
echo -e "  Service:     ${CYAN}sudo systemctl status $SERVICE_NAME${NC}"
echo ""
echo -e "  On first load, THOR will ask you to create a password."
echo -e "  Add your Kraken API keys to .env to enable live trading."
echo -e "${GOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Open browser if display available
if command -v xdg-open &>/dev/null && [[ -n "${DISPLAY:-}" ]]; then
  sleep 2 && xdg-open "http://localhost:$PORT" &
fi
