#!/usr/bin/env bash
#
# Build the macOS application bundle.
#
#   ./desktop/build_macos.sh
#
# Run from the project root on a Mac, inside the virtualenv that has the
# desktop extras installed. PyInstaller cannot cross-compile, so this has to
# run on macOS. The result is dist/"FQHC Prospect Intelligence.app".

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script builds the macOS bundle and must run on macOS." >&2
  echo "PyInstaller cannot cross-compile." >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if ! python -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is not installed. Run:" >&2
  echo "    pip install -r requirements-desktop.txt" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Icon: build icon.icns from the checked-in PNG using macOS's own tools.
# ---------------------------------------------------------------------------

if [[ ! -f desktop/icon.icns ]]; then
  echo "==> Building icon.icns"
  ICONSET="$(mktemp -d)/icon.iconset"
  mkdir -p "$ICONSET"
  for size in 16 32 128 256 512; do
    sips -z $size $size desktop/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double desktop/icon.png \
      --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o desktop/icon.icns
fi

# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

echo "==> Running PyInstaller"
rm -rf build dist
pyinstaller desktop/fqhc.spec --noconfirm

APP="dist/FQHC Prospect Intelligence.app"
if [[ ! -d "$APP" ]]; then
  echo "Build finished but $APP was not produced." >&2
  exit 1
fi

echo
echo "Built: $APP"
echo
echo "The bundle is unsigned, so Gatekeeper will refuse it on first open."
echo "Right-click the app and choose Open, then confirm -- once per machine."
echo
echo "To sign it instead (needs an Apple Developer account):"
echo "    codesign --deep --force --options runtime \\"
echo "      --sign \"Developer ID Application: YOUR NAME (TEAMID)\" \\"
echo "      \"$APP\""
echo "    xcrun notarytool submit \"$APP\" --keychain-profile AC_PASSWORD --wait"
echo "    xcrun stapler staple \"$APP\""
