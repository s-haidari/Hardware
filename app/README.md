# Hardware — unified desktop app

One app folding the STM32 pin/switch tooling (STMP) and the KiCad library
manager into a Tauri desktop shell over a Python/FastAPI backend and a React UI.

```
app/
  backend/    FastAPI backend (hwkit) + venv + tests      — see backend/README.md
  frontend/   React + Vite UI
    src-tauri/  Tauri (Rust) desktop shell
```

## Architecture

- **Tauri shell** (`frontend/src-tauri`, Rust) — native window; spawns the Python
  backend on launch and kills it on exit (no "start a server" step).
- **Backend** (`backend/hwkit`, FastAPI) — `pins` (switch fabric), `library`
  (import / audit / catalog / schematic repair), `netdeck` (netclass standard).
- **Frontend** (`frontend/src`, React) — Library · Pins · Netclasses, talks to
  the backend (`/api` proxied in dev, absolute `127.0.0.1:8799` in the packaged app).

## Run it

One-time setup:

```bash
# backend env (system Python, NOT KiCad's)
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
# frontend deps
cd frontend && npm install
```

Desktop app (spawns the backend automatically):

```bash
cd frontend && npm run app          # dev: native window + hot reload
cd frontend && npm run app:build    # release: NSIS installer in src-tauri/target/release/bundle
```

Or run the pieces directly during development:

```bash
backend/.venv/Scripts/python backend/run_server.py     # http://127.0.0.1:8799
cd frontend && npm run dev                              # http://localhost:5173
```

## Test

```bash
backend/.venv/Scripts/python -m unittest discover -s backend/tests
```

## Toolchain

Python 3.x + FastAPI · Node + Vite · Rust + MSVC (for the Tauri shell).
