#!/usr/bin/env bash
# Run the KiCad Library Manager UI straight from source — no build, no PyInstaller.
#
#   ./run.sh            # the barebones functionality UI (default)
#   ./run.sh --full     # the redesign shell
#
# Any extra args are forwarded to `python -m ui`. Portable: paths are derived from
# this file's location, so there are no hard-coded usernames. Uses the project-local
# .venv if present, otherwise falls back to system python3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
    echo "[run.sh] .venv not found — using system python3 ($PY)." >&2
    echo "[run.sh] To create the venv:  python3 -m venv .venv && .venv/bin/pip install PyQt5 watchdog" >&2
  else
    echo "[run.sh] ERROR: no .venv and no python3 on PATH." >&2
    exit 1
  fi
fi

# The `ui` package lives under tools/, so put it on the import path (this is the
# bit that otherwise makes `python -m ui` fail from the repo root).
export PYTHONPATH="$ROOT/tools${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" -m ui "$@"
