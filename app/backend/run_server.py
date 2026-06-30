"""
run_server.py — backend entry point for the desktop shell.

The Tauri shell spawns this with the backend venv's Python; it serves the
FastAPI app on 127.0.0.1. Port is the first arg or HWKIT_PORT (default 8799).
"""
from __future__ import annotations

import os
import sys

import uvicorn

# Import the app object directly so PyInstaller bundles hwkit when freezing.
from hwkit.api.app import app


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("HWKIT_PORT", "8799"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
