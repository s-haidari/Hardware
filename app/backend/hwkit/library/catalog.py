"""
catalog.py — read the current shared library and audit its correctness.

The audit quantifies the live bug across the real ``libs/``: how many symbols
point at the wrong footprint nickname and how many footprints are missing a 3D
model. After a clean import these counts go to zero.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..kicad import footprints as F
from ..kicad import symbols as S

_MODEL_SUFFIXES = {".step", ".stp", ".wrl"}
_FP_FIELD = re.compile(r'\(property\s+"Footprint"\s+"([^"]*)"')


@dataclass
class PartEntry:
    symbol: str
    footprint: str
    footprint_ok: bool          # field is "<nickname>:<name>" form


@dataclass
class LibraryAudit:
    symbols: int = 0
    footprints: int = 0
    models: int = 0
    symbols_bad_nickname: list[str] = field(default_factory=list)
    footprints_missing_model: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.symbols_bad_nickname and not self.footprints_missing_model


def list_symbols(symbols_path: Path, nickname: str = S.DEFAULT_FP_NICKNAME) -> list[PartEntry]:
    if not symbols_path.exists():
        return []
    text = symbols_path.read_text(encoding="utf-8", errors="replace")
    out: list[PartEntry] = []
    for block in S.extract_symbol_blocks(text):
        m = _FP_FIELD.search(block)
        fp = m.group(1) if m else ""
        out.append(PartEntry(
            symbol=S.symbol_name(block),
            footprint=fp,
            footprint_ok=fp.startswith(f"{nickname}:"),
        ))
    return out


def _mtime(p: Path) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""


def tree(symbols_path: Path, footprints_dir: Path, models_dir: Path) -> list[dict]:
    """Full library inventory: one row per symbol / footprint / model, with type,
    name, location, date, and a duplicate flag (name repeated within its type)."""
    from collections import Counter
    items: list[dict] = []
    for e in list_symbols(symbols_path):
        items.append({"type": "symbol", "name": e.symbol, "location": str(symbols_path),
                      "date": _mtime(symbols_path), "footprint": e.footprint, "ok": e.footprint_ok})
    if footprints_dir.exists():
        for fp in sorted(footprints_dir.glob("*.kicad_mod")):
            items.append({"type": "footprint", "name": fp.stem, "location": str(fp), "date": _mtime(fp)})
    if models_dir.exists():
        for m in sorted(models_dir.iterdir()):
            if m.suffix.lower() in _MODEL_SUFFIXES:
                items.append({"type": "model", "name": m.stem, "location": str(m), "date": _mtime(m)})
    counts = Counter((i["type"], i["name"]) for i in items)
    for i in items:
        i["dup"] = counts[(i["type"], i["name"])] > 1
    return items


def known_footprint_names(footprints_dir: Path) -> set[str]:
    """The footprint names that live in the shared library (``*.kicad_mod`` stems)."""
    if not footprints_dir.exists():
        return set()
    return {fp.stem for fp in footprints_dir.glob("*.kicad_mod")}


def audit(symbols_path: Path, footprints_dir: Path, models_dir: Path,
          nickname: str = S.DEFAULT_FP_NICKNAME) -> LibraryAudit:
    a = LibraryAudit()
    parts = list_symbols(symbols_path, nickname)
    a.symbols = len(parts)
    a.symbols_bad_nickname = [p.symbol for p in parts if not p.footprint_ok]

    if footprints_dir.exists():
        for fp in sorted(footprints_dir.glob("*.kicad_mod")):
            a.footprints += 1
            if not F.has_model(fp.read_text(encoding="utf-8", errors="replace")):
                a.footprints_missing_model.append(fp.stem)

    if models_dir.exists():
        a.models = sum(1 for p in models_dir.iterdir()
                       if p.suffix.lower() in _MODEL_SUFFIXES)
    return a
