"""kicad_paths.py — the ONE KiCad install locator (stdlib-only, so the CLI
tools can use it too). Replaces the three independent copies that lived in
LibraryManager, kicad_tools, and the nd_* scripts."""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from shutil import which
from typing import Iterable, Optional


def _kicad_version_key(path: str) -> tuple:
    """Sort key for a KiCad install path, based on its PARSED version.

    The version is the directory that contains ``bin`` (e.g.
    ``C:\\Program Files\\KiCad\\10.0\\bin`` -> ``(10, 0)``). Comparing the
    parsed integer tuple is what makes 10.0 rank ABOVE 9.0 — a plain string
    sort would place "10.0" before "9.0" and drive the older install.

    Returns ``(version_tuple, is_64bit)`` so that, on a version tie, the
    64-bit install (not under "Program Files (x86)") wins. Paths whose
    version segment has no digits sort lowest via an empty version tuple.

    KiCad install paths are Windows paths; parse them separator-agnostically so
    the backslash version segment is found even when this runs on POSIX (where
    ``pathlib`` would treat the whole ``C:\\...\\bin`` string as one component).
    """
    segs = [s for s in re.split(r"[\\/]+", str(path).strip()) if s]
    version_dir = segs[-2] if len(segs) >= 2 else (segs[-1] if segs else "")
    nums = re.findall(r"\d+", version_dir)
    version = tuple(int(n) for n in nums)
    is_64bit = 0 if "(x86)" in str(path) else 1
    return (version, is_64bit)


def pick_newest_kicad(paths: Iterable[str]) -> Optional[str]:
    """Return the newest KiCad ``bin`` path from candidate paths.

    Selection is by PARSED version tuple (so '10.0' > '9.0'), never by
    lexicographic string order. Both the x86 and x64 globs should be
    resolved and passed in together; on a version tie the 64-bit install
    is preferred. Returns ``None`` when there are no candidates.
    """
    candidates = [p for p in paths if p]
    if not candidates:
        return None
    return max(candidates, key=_kicad_version_key)


def find_kicad_bin() -> Optional[Path]:
    """KiCad's bin directory (highest installed version), honouring KICAD_BIN."""
    env = os.environ.get("KICAD_BIN")
    if env and Path(env).exists():
        return Path(env)
    hits: list = []
    for pat in (r"C:\Program Files\KiCad\*\bin", r"C:\Program Files (x86)\KiCad\*\bin"):
        hits += glob.glob(pat)
    newest = pick_newest_kicad(hits)
    return Path(newest) if newest else None


def find_kicad_cli() -> Optional[str]:
    """Full path to kicad-cli.exe (or a PATH lookup as a last resort)."""
    bin_dir = find_kicad_bin()
    if bin_dir:
        cli = bin_dir / "kicad-cli.exe"
        if cli.exists():
            return str(cli)
    return which("kicad-cli")
