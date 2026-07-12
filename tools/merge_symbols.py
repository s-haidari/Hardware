#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge_symbols.py — thin CLI over the app's symbol-merge core, so the
PowerShell import flow (import-kicad-parts.ps1) and the GUI share ONE merge
engine: duplicate symbols are skipped instead of appended twice, and the
S-expression handling is exactly the app's.

Usage:
    merge_symbols.py TARGET.kicad_sym SOURCE.kicad_sym [SOURCE2 ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class _StdoutLog:
    """Duck-typed UILog for CLI use."""

    def write(self, msg):
        print(msg, flush=True)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        from LibraryManager import merge_symbols as _merge
    except ImportError as e:
        print(f"ERROR: could not load the app's merge core ({e}).")
        venv_python = (
            ".venv/Scripts/python.exe"
            if sys.platform.startswith("win")
            else ".venv/bin/python"
        )
        print(f"Run with the repo venv: {venv_python} tools/merge_symbols.py ...")
        return 2
    target = Path(argv[0])
    sources = [Path(a) for a in argv[1:]]
    missing = [s for s in sources if not s.exists()]
    if missing:
        for m in missing:
            print(f"ERROR: source not found: {m}")
        return 2
    _merge(target, sources, _StdoutLog())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
