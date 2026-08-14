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

PY_VERSION="$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)'; then
  echo "This project needs Python 3.11 or newer; this virtualenv has $PY_VERSION." >&2
  echo "macOS ships 3.9 with the Xcode Command Line Tools, which is too old." >&2
  echo "    brew install python@3.12   # or download from python.org" >&2
  echo "    rm -rf .venv && python3.12 -m venv .venv && source .venv/bin/activate" >&2
  echo "    pip install -r requirements.txt -r requirements-desktop.txt" >&2
  exit 1
fi

if ! python -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller is not installed. Run:" >&2
  echo "    pip install -r requirements-desktop.txt" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Architecture preflight
#
# PyInstaller builds for the architecture of the Python running it. Say plainly
# which machines the result will run on, rather than letting someone discover it
# when a colleague double-clicks the app and nothing happens.
# ---------------------------------------------------------------------------

PY_PLATFORM="$(python -c 'import sysconfig; print(sysconfig.get_platform())')"
echo "==> Building with Python for: $PY_PLATFORM"

case "$PY_PLATFORM" in
  *universal2*)
    if [[ "${FQHC_TARGET_ARCH:-}" == "universal2" ]]; then
      echo "    Target: universal2 -- runs on both Apple Silicon and Intel."
    else
      echo "    This Python is universal2. Set FQHC_TARGET_ARCH=universal2 to"
      echo "    build an app that runs on both Apple Silicon and Intel Macs."
    fi
    ;;
  *arm64*)
    echo "    Target: Apple Silicon only. Intel Macs will not run this build."
    echo "    For both, install a universal2 Python and re-run with"
    echo "    FQHC_TARGET_ARCH=universal2."
    ;;
  *x86_64*)
    echo "    Target: Intel only. It will run on Apple Silicon under Rosetta 2."
    ;;
esac
echo

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

# ---------------------------------------------------------------------------
# Disk image
#
# A .app is a folder; a .dmg is the single file you actually hand to someone.
# Built with hdiutil, which ships with macOS -- no extra tooling.
# ---------------------------------------------------------------------------

if [[ "${FQHC_SKIP_DMG:-}" != "1" ]]; then
  echo
  echo "==> Building disk image"
  DMG="dist/FQHC Prospect Intelligence.dmg"
  STAGING="$(mktemp -d)/FQHC Prospect Intelligence"
  mkdir -p "$STAGING"
  cp -R "$APP" "$STAGING/"
  # The conventional drag-to-install layout: the app beside an Applications
  # shortcut, so opening the image explains what to do without instructions.
  ln -s /Applications "$STAGING/Applications"

  rm -f "$DMG"
  hdiutil create \
    -volname "FQHC Prospect Intelligence" \
    -srcfolder "$STAGING" \
    -ov -format UDZO \
    "$DMG" >/dev/null

  rm -rf "$(dirname "$STAGING")"
  echo "Built: $DMG  ($(du -h "$DMG" | cut -f1))"
fi

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
