#!/usr/bin/env bash
# Run the test suite on the REAL Windows Python, from WSL — no 12-minute CI round-trip.
#
# It rsyncs the current working tree to a Windows-native working copy, then invokes the
# Windows uv venv's pytest. This exercises genuine Windows behaviour (cp1252 file I/O,
# backslash/drive-letter paths, file locking, the PyQt5 'windows' platform) that the Linux
# offscreen suite cannot. The windows-latest GitHub CI is still the authoritative gate;
# this is the fast local pre-check that catches Windows regressions before you push.
#
# Prereq (one-time):   tools/win/setup.sh
# Usage:
#   tools/win/wintest.sh                      # full suite
#   tools/win/wintest.sh tests/test_x.py -q   # a subset / extra pytest args
#   WIN_ENCODING_STRICT=1 tools/win/wintest.sh tests/test_backend_project_settings.py
#       ^ also turns any no-encoding file open into a hard error (cp1252 simulation)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WIN_HOME_WIN="$(powershell.exe -NoProfile -Command '$env:USERPROFILE' | tr -d '\r')"
WIN_HOME_WSL="$(wslpath "$WIN_HOME_WIN")"
WINROOT_WSL="$WIN_HOME_WSL/win-hardware"
WINROOT_WIN="$WIN_HOME_WIN\\win-hardware"

mkdir -p "$WINROOT_WSL"
# Mirror the tree but never touch the Windows venv / git / caches / heavy STEP models.
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '.wvenv' --exclude '__pycache__' \
  --exclude '*.pyc' --exclude 'build/' --exclude '.claude' \
  --exclude 'libs/My3DModels/*.STEP' \
  "$SRC/" "$WINROOT_WSL/"

ARGS="${*:-tests}"
# Default to parallel across all cores (pytest-xdist) unless the caller already passed -n.
# The suite is parallel-safe — tests/conftest.py isolates the per-worker config file and
# restores the LibraryManager globals — so this is the point of the local loop: a full
# real-Windows run in ~1.5 min instead of ~15 (or the ~20-min windows-latest CI round-trip).
case " $ARGS " in
  *" -n "*) : ;;                            # caller set -n (incl. `-n 0` to force serial) → respect it
  *) ARGS="$ARGS -n auto" ;;
esac
PYFLAGS=""
if [ "${WIN_ENCODING_STRICT:-0}" = "1" ]; then
  PYFLAGS="-X warn_default_encoding -W error::EncodingWarning"
fi
# QT_QPA_PLATFORM=offscreen matches the CI (headless); tests/conftest.py also sets it.
powershell.exe -NoProfile -Command "cd '$WINROOT_WIN'; \$env:QT_QPA_PLATFORM='offscreen'; .\\.wvenv\\Scripts\\python.exe $PYFLAGS -m pytest $ARGS"
