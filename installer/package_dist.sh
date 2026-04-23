#!/usr/bin/env bash
# Packages THOR source into thor-latest.zip for distribution.
# Run from the thor/ project root: bash installer/package_dist.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST_DIR="$ROOT/dist"
ZIP_NAME="thor-latest.zip"
ZIP_PATH="$DIST_DIR/$ZIP_NAME"

mkdir -p "$DIST_DIR"

echo "Packaging THOR from $ROOT..."
echo ""

# ── Sanity check: make sure no real secrets are about to leak ─────────────────
for secret_file in "$ROOT/.env" "$ROOT/dashboard/.env"; do
  if [[ -f "$secret_file" ]]; then
    if grep -qE "^\s*(KRAKEN|CMC|TELEGRAM|PASSWORD_HASH).*=.+$" "$secret_file"; then
      echo "⚠  Real credentials detected in $secret_file — will be excluded from zip."
    fi
  fi
done
echo ""

# ── Create a clean .env template to include ───────────────────────────────────
TEMPLATE_ENV="$ROOT/installer/.env.template"
cat > "$TEMPLATE_ENV" << 'ENVEOF'
# ─────────────────────────────────────────────────────────────────────────────
# THOR Configuration — fill in your keys, then restart THOR.
# ─────────────────────────────────────────────────────────────────────────────

# Kraken API keys (required for live trading — leave blank for signal/paper mode)
KRAKEN_API_KEY=
KRAKEN_API_SECRET=

# CoinMarketCap API key (optional — improves token data)
CMC_API_KEY=

# Telegram notifications (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# THOR dashboard password — set automatically on first run via browser
THOR_PASSWORD_HASH=

# Port THOR listens on (default: 5000)
THOR_PORT=5000
ENVEOF

# ── Create Windows launch.bat ─────────────────────────────────────────────────
cat > "$ROOT/installer/launch.bat" << 'BATEOF'
@echo off
start "" "http://localhost:5000"
BATEOF

# ── Build the zip ─────────────────────────────────────────────────────────────
echo "Building $ZIP_PATH ..."
rm -f "$ZIP_PATH"

cd "$ROOT"

zip -r "$ZIP_PATH" . \
  -x ".env" \
  -x ".env.*" \
  -x "*.pyc" \
  -x "*/__pycache__/*" \
  -x "*/.git/*" \
  -x ".git/*" \
  -x ".gitignore" \
  -x "venv/*" \
  -x ".venv/*" \
  -x "*.log" \
  -x "logs/*" \
  -x "dist/*" \
  -x "installer/*" \
  -x "*.db" \
  -x "trading/paper_state.json" \
  -x "trading/gmx_state.json" \
  -x "trading/trades.json" \
  -x "trading/alerts.json" \
  -x "dashboard/price_alerts.json" \
  -x "dashboard/watchlist.json" \
  -x "predictions/state*.json" \
  -x "*.pdf" \
  -x "reports/*" \
  -x "make_guide.py" \
  -x "make_overview.py" \
  -x "fix.py" \
  -x "server.log" \
  -x "config.json" \
  -x "THOR_Overview.md" \
  -x "config/.env" \
  -x "config/*.env" \
  -x "*.bak" \
  -x "*.tmp" \
  -x "*.backup" \
  -x "backtest/TODO*" \
  -x "backtest/reports/*"

# ── Inject clean .env template as .env ───────────────────────────────────────
# Stage it temporarily so it zips with the right path
cp "$TEMPLATE_ENV" "$ROOT/.env.install"
cd "$ROOT"
zip "$ZIP_PATH" ".env.install"
# Rename inside the zip to .env
python3 -c "
import zipfile, shutil, os
zin  = zipfile.ZipFile('$ZIP_PATH', 'r')
zout = zipfile.ZipFile('$ZIP_PATH.tmp', 'w', zipfile.ZIP_DEFLATED)
for item in zin.infolist():
    data = zin.read(item.filename)
    if item.filename == '.env.install':
        item.filename = '.env'
    zout.writestr(item, data)
zin.close(); zout.close()
os.replace('$ZIP_PATH.tmp', '$ZIP_PATH')
"
rm -f "$ROOT/.env.install"

# ── Inject the Windows launch.bat ─────────────────────────────────────────────
cp "$ROOT/installer/launch.bat" "$ROOT/launch.bat"
cd "$ROOT"
zip "$ZIP_PATH" "launch.bat"
rm -f "$ROOT/launch.bat"

# ── Also copy Pi installer script into dist for direct download ───────────────
cp "$SCRIPT_DIR/install_pi.sh" "$DIST_DIR/install_pi.sh"
chmod +x "$DIST_DIR/install_pi.sh"

# ── Report ────────────────────────────────────────────────────────────────────
SIZE=$(du -sh "$ZIP_PATH" | cut -f1)
FILE_COUNT=$(python3 -c "import zipfile; z=zipfile.ZipFile('$ZIP_PATH'); print(len(z.namelist()))")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Package complete"
echo "  File:   $ZIP_PATH"
echo "  Size:   $SIZE"
echo "  Files:  $FILE_COUNT"
echo ""
echo "  Contents check:"
python3 -c "
import zipfile
z = zipfile.ZipFile('$ZIP_PATH')
files = z.namelist()
# Check nothing sensitive leaked
sensitive = [f for f in files if '.env' in f and f != '.env']
has_env   = '.env' in files
secrets   = [f for f in files if f.endswith(('.env','.key','.pem','.secret')) and f != '.env']
print(f'    .env template included: {has_env}')
print(f'    Suspicious files:       {secrets if secrets else \"none\"}')
print(f'    venv included:          {any(\"venv/\" in f for f in files)}')
print(f'    .git included:          {any(\".git/\" in f for f in files)}')
"
echo ""
echo "  Next steps:"
echo "  1. scp $ZIP_PATH user@your-server:/var/www/thor/dist/"
echo "  2. Build THOR-Setup.exe on Windows with Inno Setup"
echo "  3. Build THOR-Installer.pkg on Mac with bash installer/build_mac.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
