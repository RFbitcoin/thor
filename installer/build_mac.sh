#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# THOR — macOS .pkg Builder
# Run on a Mac to produce THOR-Installer.pkg
# Requirements: macOS + Xcode Command Line Tools
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="1.0"
PKG_NAME="THOR-Installer"
BUILD_DIR="$SCRIPT_DIR/mac_build"
OUTPUT_DIR="$SCRIPT_DIR/../dist"

echo "Building THOR macOS installer..."

mkdir -p "$BUILD_DIR/scripts"
mkdir -p "$BUILD_DIR/payload/Applications"
mkdir -p "$OUTPUT_DIR"

# ── Copy installer script as postinstall ─────────────────────────────────────
cp "$SCRIPT_DIR/install_mac.command" "$BUILD_DIR/scripts/postinstall"
chmod +x "$BUILD_DIR/scripts/postinstall"

# ── Create a minimal payload (required by pkgbuild) ──────────────────────────
# The real install happens in postinstall; payload just drops the .command file
cp "$SCRIPT_DIR/install_mac.command" "$BUILD_DIR/payload/Applications/THOR Installer.command"
chmod +x "$BUILD_DIR/payload/Applications/THOR Installer.command"

# ── Build component package ───────────────────────────────────────────────────
pkgbuild \
  --root "$BUILD_DIR/payload" \
  --scripts "$BUILD_DIR/scripts" \
  --identifier "com.rfbitcoin.thor" \
  --version "$VERSION" \
  --install-location "/" \
  "$BUILD_DIR/thor_component.pkg"

# ── Build distribution package ────────────────────────────────────────────────
cat > "$BUILD_DIR/distribution.xml" << DISTEOF
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>THOR Bitcoin Intelligence Dashboard</title>
    <organization>com.rfbitcoin</organization>
    <domains enable_anywhere="false" enable_currentUserHome="true" enable_localSystem="true"/>
    <options customize="never" require-scripts="false" rootVolumeOnly="false"/>
    <os-version min="10.15"/>
    <welcome file="welcome.html" mime-type="text/html"/>
    <readme  file="readme.html"  mime-type="text/html"/>
    <license file="license.html" mime-type="text/html"/>
    <choices-outline>
        <line choice="default">
            <line choice="com.rfbitcoin.thor"/>
        </line>
    </choices-outline>
    <choice id="default"/>
    <choice id="com.rfbitcoin.thor" visible="false">
        <pkg-ref id="com.rfbitcoin.thor"/>
    </choice>
    <pkg-ref id="com.rfbitcoin.thor" version="$VERSION" onConclusion="none">thor_component.pkg</pkg-ref>
</installer-gui-script>
DISTEOF

# Create minimal HTML resources
mkdir -p "$BUILD_DIR/resources"
echo '<html><body><h2>⚡ THOR Bitcoin Intelligence Dashboard</h2><p>Institutional-grade Bitcoin signal intelligence. Self-hosted on your Mac.</p></body></html>' > "$BUILD_DIR/resources/welcome.html"
echo '<html><body><p>THOR will install to ~/THOR and start automatically on login. Requires an internet connection to download dependencies (~50MB).</p></body></html>' > "$BUILD_DIR/resources/readme.html"
echo '<html><body><p>THOR is provided under a commercial license. Not for redistribution. See thor.rfbitcoin.com for terms.</p><p>Nothing on this platform constitutes financial advice. Past performance does not guarantee future results.</p></body></html>' > "$BUILD_DIR/resources/license.html"

productbuild \
  --distribution "$BUILD_DIR/distribution.xml" \
  --resources "$BUILD_DIR/resources" \
  --package-path "$BUILD_DIR" \
  "$OUTPUT_DIR/$PKG_NAME.pkg"

rm -rf "$BUILD_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Built: $OUTPUT_DIR/$PKG_NAME.pkg"
echo ""
echo "  To sign (recommended, requires Apple Developer account):"
echo "  productsign --sign 'Developer ID Installer: Your Name' \\"
echo "    $OUTPUT_DIR/$PKG_NAME.pkg \\"
echo "    $OUTPUT_DIR/$PKG_NAME-signed.pkg"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
