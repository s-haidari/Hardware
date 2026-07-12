#!/usr/bin/env bash
# Render the app's UI on the REAL Windows desktop (native PyQt5 'windows' platform — real
# fonts, DPI, widget metrics) and copy the PNGs back to build/render-win/ for inspection.
# Unlike the Linux offscreen render gate, this shows how the app ACTUALLY looks on Windows.
#
# Usage:
#   tools/win/winapp.sh                 # projects surface, both themes
#   tools/win/winapp.sh bench dark      # <surface> <theme>
# To launch the interactive app instead of rendering (a window pops on your desktop):
#   tools/win/winapp.sh --run
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WIN_HOME_WIN="$(powershell.exe -NoProfile -Command '$env:USERPROFILE' | tr -d '\r')"
WIN_HOME_WSL="$(wslpath "$WIN_HOME_WIN")"
WINROOT_WSL="$WIN_HOME_WSL/win-hardware"
WINROOT_WIN="$WIN_HOME_WIN\\win-hardware"

rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '.wvenv' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude 'build/' --exclude '.claude' \
  --exclude 'libs/My3DModels/*.STEP' \
  "$SRC/" "$WINROOT_WSL/"

if [ "${1:-}" = "--run" ]; then
  echo "==> launching python -m ui on the Windows desktop (close the window to return)"
  powershell.exe -NoProfile -Command "cd '$WINROOT_WIN'; .\\.wvenv\\Scripts\\python.exe -m ui"
  exit 0
fi

SURFACE="${1:-projects}"; THEME="${2:-both}"
echo "==> rendering '$SURFACE' ($THEME) on the native Windows platform"
# No QT_QPA_PLATFORM -> the real Windows platform renders on this desktop.
powershell.exe -NoProfile -Command "cd '$WINROOT_WIN'; .\\.wvenv\\Scripts\\python.exe tools\\ui\\render_gate.py --out build\\render-win --surface $SURFACE --theme $THEME --settle 1.6"
mkdir -p "$SRC/build/render-win"
cp -f "$WINROOT_WSL/build/render-win/"*.png "$SRC/build/render-win/" 2>/dev/null || true
echo "==> Windows-rendered PNGs -> build/render-win/"
ls "$SRC/build/render-win/" 2>/dev/null | head
