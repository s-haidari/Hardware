# Local real-Windows loop (from WSL)

The **windows-latest GitHub CI is the authoritative release gate** (`.github/workflows/ci.yml`
runs the full suite; `build-exe.yml` builds `KiCad Manager.exe`). These scripts give a **fast
local pre-check on the real Windows Python** so Windows regressions are caught in minutes — not
a 12-minute CI push — before committing.

They exercise genuine Windows behaviour the Linux offscreen suite can't: cp1252 file I/O,
backslash/drive-letter paths, file locking, and the native PyQt5 `windows` platform.

## One-time setup
```bash
tools/win/setup.sh
```
Syncs a Windows-native working copy to `%USERPROFILE%\win-hardware`, creates a Windows `uv`
venv (`.wvenv`, Python 3.11) and installs the exact deps `ci.yml` uses (PyQt5, watchdog,
pytest, numpy, scipy, shapely, tzdata).

## Run tests on real Windows
```bash
tools/win/wintest.sh                        # full suite
tools/win/wintest.sh tests/test_x.py -q     # a subset
WIN_ENCODING_STRICT=1 tools/win/wintest.sh tests/test_backend_project_settings.py
#   ^ also makes any no-encoding file open a HARD ERROR (cp1252 simulation)
```

## See the app on real Windows
```bash
tools/win/winapp.sh                 # render the Projects surface (both themes) -> build/render-win/
tools/win/winapp.sh bench dark      # <surface> <theme>
tools/win/winapp.sh --run           # launch the interactive app on your Windows desktop
```

## How it stays honest
- `tests/test_windows_hygiene.py` fails on EVERY platform if any file I/O or subprocess is
  missing `encoding=` (the cp1252 hazard), plus non-ASCII round-trip locks through the real
  writers. So a Windows-hostile pattern can't reach CI in the first place.
- The scripts derive the Windows home from `%USERPROFILE%` (not hardcoded), and never sync
  over the Windows venv / `.git` / caches.

Requirements (already present here): WSL interop (`powershell.exe`, `/mnt/c`), a Windows `uv`,
a Windows Python 3.11 (uv fetches it if missing), and — for `kicad-cli` integration — KiCad on
the Windows side (`C:\Program Files\KiCad\10.0`).
