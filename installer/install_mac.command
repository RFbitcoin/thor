#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# THOR Bitcoin Intelligence Dashboard — macOS Installer
# Double-click this file in Finder to install.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DIST_URL="https://thor.rfbitcoin.com/dist/thor-latest.zip"
INSTALL_DIR="$HOME/THOR"
PLIST_LABEL="com.rfbitcoin.thor"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
PORT=5000

# Keep Terminal window open on error
trap 'echo ""; echo "Installation failed. See error above."; read -p "Press Enter to close..."' ERR

clear
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ⚡ THOR — Bitcoin Intelligence Dashboard"
echo "  macOS Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

step() { echo "▶ $1"; }
ok()   { echo "  ✓ $1"; }

# ── Xcode Command Line Tools (provides git, curl) ────────────────────────────
if ! xcode-select -p &>/dev/null; then
  step "Installing Xcode Command Line Tools..."
  xcode-select --install
  echo "  Please complete the Xcode install dialog, then re-run this installer."
  read -p "Press Enter when done..."
fi

# ── Python 3.9+ — try system first, then Homebrew, then python.org ────────────
PYTHON=""
for cmd in python3.11 python3.10 python3.9 python3; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null)
    if [[ "$ver" == "True" ]]; then PYTHON="$cmd"; break; fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  # Try Homebrew
  if ! command -v brew &>/dev/null; then
    step "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || true
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null)" || eval "$(/usr/local/bin/brew shellenv 2>/dev/null)" || true
  fi

  if command -v brew &>/dev/null; then
    step "Installing Python via Homebrew..."
    brew install python@3.11 2>/dev/null || brew install python@3.10 2>/dev/null || brew install python3 || true
    for cmd in python3.11 python3.10 python3; do
      if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(sys.version_info >= (3,9))" 2>/dev/null)
        if [[ "$ver" == "True" ]]; then PYTHON="$cmd"; break; fi
      fi
    done
  fi
fi

if [[ -z "$PYTHON" ]]; then
  echo ""
  echo "  Python 3.9+ not found and could not be installed automatically."
  echo "  Please download and install Python from:"
  echo "  https://www.python.org/downloads/macos/"
  echo "  Then re-run this installer."
  read -p "Press Enter to exit..."
  exit 1
fi
ok "Python: $($PYTHON --version)"

# ── Download THOR ─────────────────────────────────────────────────────────────
step "Downloading THOR..."
TMP=$(mktemp -d)
curl -fsSL --progress-bar "$DIST_URL" -o "$TMP/thor.zip"
ok "Downloaded"

# ── Install ───────────────────────────────────────────────────────────────────
step "Installing to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
unzip -q -o "$TMP/thor.zip" -d "$INSTALL_DIR"
rm -rf "$TMP"

# ── Create .env ───────────────────────────────────────────────────────────────
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  cat > "$INSTALL_DIR/.env" << 'ENVEOF'
KRAKEN_API_KEY=
KRAKEN_API_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
THOR_PASSWORD_HASH=
THOR_PORT=5000
ENVEOF
fi
ok "Config created"

# ── Python virtual environment ────────────────────────────────────────────────
step "Installing Python dependencies..."
$PYTHON -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Dependencies installed"

# ── LaunchAgent (auto-start on login) ────────────────────────────────────────
step "Registering THOR to start automatically..."
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${INSTALL_DIR}/venv/bin/python</string>
        <string>${INSTALL_DIR}/dashboard/server.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}/dashboard</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/logs/thor.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/logs/thor-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
PLISTEOF

mkdir -p "$INSTALL_DIR/logs"
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
ok "THOR will start automatically on login"

# ── Application shortcut ─────────────────────────────────────────────────────
step "Creating THOR app shortcut..."
APP_DIR="$HOME/Applications"
mkdir -p "$APP_DIR"

SHORTCUT="$APP_DIR/THOR.command"
cat > "$SHORTCUT" << SHORTEOF
#!/bin/bash
open "http://localhost:$PORT"
SHORTEOF
chmod +x "$SHORTCUT"

# Also add to Dock if possible
if command -v defaults &>/dev/null; then
  defaults write com.apple.dock persistent-others -array-add \
    "<dict><key>tile-data</key><dict><key>file-data</key><dict><key>_CFURLString</key><string>$SHORTCUT</string><key>_CFURLStringType</key><integer>0</integer></dict></dict></dict>" 2>/dev/null || true
  killall Dock 2>/dev/null || true
fi
ok "Shortcut created in ~/Applications/THOR.command"

# ── Open browser ─────────────────────────────────────────────────────────────
sleep 3
open "http://localhost:$PORT"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ THOR installed successfully!"
echo ""
echo "  Dashboard:  http://localhost:$PORT"
echo "  Config:     $INSTALL_DIR/.env"
echo "  Logs:       $INSTALL_DIR/logs/"
echo ""
echo "  THOR starts automatically when you log in."
echo "  First load: create your password in the browser."
echo "  Add Kraken API keys to .env for live trading."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
read -p "Press Enter to close this window..."
