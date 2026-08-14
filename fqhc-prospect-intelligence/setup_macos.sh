#!/usr/bin/env bash
#
# One-command setup on macOS.
#
#   ./setup_macos.sh
#
# Finds a suitable Python, builds the virtualenv and installs everything. macOS
# ships Python 3.9 with the Xcode Command Line Tools and this project needs
# 3.11+, so the interpreter search is the whole point of this script: it looks
# past `python3` for a newer one rather than failing deep inside SQLAlchemy.

set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MINIMUM="3.11"

is_new_enough() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
    >/dev/null 2>&1
}

find_python() {
  # Newest first, then whatever `python3` happens to be.
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && is_new_enough "$candidate"; then
      command -v "$candidate"
      return 0
    fi
  done

  # Homebrew installs are not always on PATH, particularly in a fresh shell.
  for prefix in /opt/homebrew/bin /usr/local/bin /Library/Frameworks/Python.framework/Versions; do
    for candidate in "$prefix"/python3.1[1-9] "$prefix"/*/bin/python3; do
      if [[ -x "$candidate" ]] && is_new_enough "$candidate"; then
        echo "$candidate"
        return 0
      fi
    done
  done

  return 1
}

echo "==> Looking for Python $MINIMUM or newer"

if ! PYTHON="$(find_python)"; then
  FOUND="$(python3 --version 2>&1 || echo 'none')"
  cat >&2 <<EOF

No Python $MINIMUM or newer was found. The best available is: $FOUND

macOS ships Python 3.9 with the Xcode Command Line Tools, which is too old.
Install a newer one, then run this script again:

  With Homebrew:
      brew install python@3.12

  Without Homebrew:
      Download the macOS installer from https://www.python.org/downloads/macos/
      and run it. Choose the "universal2" build if offered -- it also lets you
      build an app that runs on Intel Macs.

EOF
  exit 1
fi

echo "    Using $PYTHON ($("$PYTHON" --version 2>&1))"

if [[ -d .venv ]]; then
  echo "==> Removing the existing .venv"
  rm -rf .venv
fi

echo "==> Creating the virtualenv"
"$PYTHON" -m venv .venv

echo "==> Installing dependencies (a few minutes)"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

DESKTOP_OK=1
if ! ./.venv/bin/python -m pip install --quiet -r requirements-desktop.txt; then
  DESKTOP_OK=0
fi

# pip can exit 0 after retrying through a flaky download and still leave
# something unimportable, so prove the install actually works before claiming
# success. A setup script that lies is worse than one that fails.
echo "==> Verifying the install"
if ! VERIFY_OUTPUT="$(./.venv/bin/python -c '
import app, fastapi, sqlalchemy, rapidfuzz, openpyxl, yaml, httpx, jinja2, uvicorn
print("ok")
' 2>&1)"; then
  echo >&2
  echo "The dependencies did not install cleanly:" >&2
  echo >&2
  echo "$VERIFY_OUTPUT" >&2
  echo >&2
  echo "Try running this script again -- the usual cause is a dropped download." >&2
  exit 1
fi

echo
echo "Setup complete. Activate the virtualenv with:"
echo
echo "    source .venv/bin/activate"
echo

if [[ "$DESKTOP_OK" == "1" ]]; then
  echo "Then open the app in a native window:"
  echo
  echo "    python -m desktop.main"
  echo
  echo "Or build the .app and .dmg:"
  echo
  echo "    ./desktop/build_macos.sh"
else
  # pywebview pulls in pyobjc, which is the most likely install to fail.
  echo "The desktop extras did not install, so the native window is unavailable."
  echo "Everything else works -- run the app in your browser with:"
  echo
  echo "    python -m desktop.main --no-window"
fi
echo
