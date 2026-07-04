"""kicad_paths.py — the ONE KiCad install locator (stdlib-only, so the CLI
tools can use it too). Replaces the three independent copies that lived in
LibraryManager, kicad_tools, and the nd_* scripts."""
from __future__ import annotations

import glob
import os
from pathlib import Path
from shutil import which
from typing import Optional


def find_kicad_bin() -> Optional[Path]:
    """KiCad's bin directory (highest installed version), honouring KICAD_BIN."""
    env = os.environ.get("KICAD_BIN")
    if env and Path(env).exists():
        return Path(env)
    hits: list = []
    for pat in (r"C:\Program Files\KiCad\*\bin", r"C:\Program Files (x86)\KiCad\*\bin"):
        hits += glob.glob(pat)
    hits.sort()
    return Path(hits[-1]) if hits else None


def find_kicad_cli() -> Optional[str]:
    """Full path to kicad-cli.exe (or a PATH lookup as a last resort)."""
    bin_dir = find_kicad_bin()
    if bin_dir:
        cli = bin_dir / "kicad-cli.exe"
        if cli.exists():
            return str(cli)
    return which("kicad-cli")
