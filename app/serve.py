"""
serve.py — run the whole app as one script (no Tauri, no exe).

Builds the React UI if needed, then serves it + the API from one FastAPI server
and opens your browser. Run with the backend venv's Python:

    backend/.venv/Scripts/python serve.py
    (or double-click run-app.bat)
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent          # app/
FRONTEND = HERE / "frontend"
DIST = FRONTEND / "dist"
BACKEND = HERE / "backend"
PORT = 8799
URL = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, str(BACKEND))


def ensure_ui_built() -> None:
    if (DIST / "index.html").exists():
        return
    print("Building the UI (first run)...")
    subprocess.run("npm install", cwd=FRONTEND, shell=True, check=True)
    subprocess.run("npm run build", cwd=FRONTEND, shell=True, check=True)


def main() -> None:
    ensure_ui_built()
    import uvicorn
    print(f"\n  Hardware app -> {URL}\n  (Ctrl+C to stop)\n")
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    uvicorn.run("hwkit.api.app:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
