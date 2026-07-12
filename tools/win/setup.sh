#!/usr/bin/env bash
# One-time setup for the local real-Windows test loop (see tools/win/wintest.sh).
#
# Creates a Windows-native working copy + a Windows uv venv with the exact deps the
# windows-latest CI installs, so `tools/win/wintest.sh` can run the suite on real Windows.
# Requires (already present on this machine): WSL interop, a Windows `uv`, a Windows
# Python 3.11 (uv will fetch it if missing).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WIN_HOME_WIN="$(powershell.exe -NoProfile -Command '$env:USERPROFILE' | tr -d '\r')"
WIN_HOME_WSL="$(wslpath "$WIN_HOME_WIN")"
WINROOT_WSL="$WIN_HOME_WSL/win-hardware"
WINROOT_WIN="$WIN_HOME_WIN\\win-hardware"

echo "==> syncing working tree -> $WINROOT_WSL"
mkdir -p "$WINROOT_WSL"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '.wvenv' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude 'build/' --exclude '.claude' \
  --exclude 'libs/My3DModels/*.STEP' \
  "$SRC/" "$WINROOT_WSL/"

echo "==> creating Windows uv venv (.wvenv, python 3.11) + installing CI deps"
# Deps mirror .github/workflows/ci.yml exactly (PyQt5 + the mesh/tz runtime deps).
powershell.exe -NoProfile -Command "cd '$WINROOT_WIN'; uv venv .wvenv --python 3.11; uv pip install --python .wvenv\\Scripts\\python.exe PyQt5 watchdog pytest numpy scipy shapely tzdata"

echo "==> verifying the Windows venv imports the GUI stack"
powershell.exe -NoProfile -Command "cd '$WINROOT_WIN'; .\\.wvenv\\Scripts\\python.exe -c \"import PyQt5, pytest, numpy, scipy, shapely; from PyQt5 import QtCore; print('Windows venv OK — PyQt5', QtCore.PYQT_VERSION_STR)\""
echo "==> done. Run the suite on real Windows with:  tools/win/wintest.sh"
