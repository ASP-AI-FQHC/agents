#!/bin/bash
# Double-click this on a Mac to build the ASP Proposal Builder .dmg installer.
# Requires Node.js (https://nodejs.org — LTS installer). One-time.
cd "$(dirname "$0")" || exit 1
echo "== ASP Proposal Builder — Mac build =="
if ! command -v npm >/dev/null 2>&1; then
  echo
  echo "Node.js is not installed. Install the LTS version from https://nodejs.org, then run this again."
  echo
  read -n 1 -s -r -p "Press any key to close."; exit 1
fi
echo "Installing dependencies (first run downloads Electron; a few minutes)…"
npm install || { echo "npm install failed"; read -n 1 -s -r -p "Press any key to close."; exit 1; }
echo "Building the app…"
npm run dist || { echo "build failed"; read -n 1 -s -r -p "Press any key to close."; exit 1; }
echo
echo "Done. Your installer is in the 'dist' folder:"
ls -1 dist/*.dmg 2>/dev/null
open dist 2>/dev/null
read -n 1 -s -r -p "Press any key to close."
