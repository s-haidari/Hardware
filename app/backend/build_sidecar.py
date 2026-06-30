"""
build_sidecar.py — freeze the backend into a standalone exe and place it as the
Tauri sidecar, so the packaged app needs no Python/venv on the target machine.

Run with the backend venv's Python:
    .venv/Scripts/python build_sidecar.py
Then build the desktop app:
    cd ../frontend && npm run app:build
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                                   # app/backend
SIDECAR_DIR = HERE.parent / "frontend" / "src-tauri" / "binaries"
TARGET_TRIPLE = "x86_64-pc-windows-msvc"

_PYI_ARGS = [
    "--noconfirm", "--onefile", "--name", "hwkit-backend",
    "--collect-submodules", "hwkit",
    "--collect-all", "uvicorn", "--collect-all", "anyio",
    "--collect-all", "ruamel.yaml", "--collect-all", "fastapi",
    "--collect-all", "starlette", "--collect-all", "pydantic",
    "--collect-all", "pydantic_core", "--hidden-import", "hwkit.api.app",
]


def main() -> int:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", *_PYI_ARGS, str(HERE / "run_server.py")],
        cwd=HERE, check=True,
    )
    src = HERE / "dist" / "hwkit-backend.exe"
    if not src.exists():
        print(f"build failed: {src} not produced", file=sys.stderr)
        return 1
    SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    dst = SIDECAR_DIR / f"hwkit-backend-{TARGET_TRIPLE}.exe"
    shutil.copyfile(src, dst)
    print(f"sidecar -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
