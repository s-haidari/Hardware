"""
importer.py — bring a part (easyeda2kicad / JLCPCB export) into the shared
KiCad libraries so it is *schematic-ready*: the symbol resolves its footprint
and the footprint resolves its 3D model.

This is requirement #1 (see app/backend/README.md). The legacy manager moved the
files but left the symbol pointing at the wrong footprint nickname and dropped
the footprint's model line; ``import_part`` guarantees both are correct.

Input: a directory or .zip containing some of {*.kicad_sym, *.kicad_mod,
*.step/.stp/.wrl}. Output: the parts merged into ``MySymbols.kicad_sym`` /
``MyFootprints.pretty`` / ``My3DModels``, plus an :class:`ImportResult`.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..kicad import footprints, symbols

_MODEL_SUFFIXES = {".step", ".stp", ".wrl"}


@dataclass
class LibPaths:
    symbols: Path        # MySymbols.kicad_sym (file)
    footprints: Path     # MyFootprints.pretty (dir)
    models: Path         # My3DModels (dir)

    def ensure(self) -> None:
        self.symbols.parent.mkdir(parents=True, exist_ok=True)
        if not self.symbols.exists():
            self.symbols.write_text(symbols.SYMBOL_LIB_HEADER, encoding="utf-8", newline="\n")
        self.footprints.mkdir(parents=True, exist_ok=True)
        self.models.mkdir(parents=True, exist_ok=True)


@dataclass
class ImportResult:
    symbols: list[str] = field(default_factory=list)
    footprints: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _existing_model_name(fp_text: str) -> str | None:
    """The basename of a model already referenced by the footprint, if any."""
    import re
    m = re.search(r'\(model\s+"?([^"\s)]+)', fp_text)
    if not m:
        return None
    return Path(m.group(1)).name


def _pick_model(fp_path: Path, fp_text: str, models: list[Path]) -> Path | None:
    """Choose the model file for a footprint: existing reference, stem match,
    or the only model present."""
    ref = _existing_model_name(fp_text)
    if ref:
        for mp in models:
            if mp.name == ref:
                return mp
    for mp in models:
        if mp.stem.lower() == fp_path.stem.lower():
            return mp
    if len(models) == 1:
        return models[0]
    return None


def import_part(source: Path, libs: LibPaths, *,
                model_var: str = footprints.DEFAULT_MODEL_VAR,
                nickname: str = symbols.DEFAULT_FP_NICKNAME) -> ImportResult:
    """Import every part found under ``source`` into the shared libraries."""
    libs.ensure()
    result = ImportResult()

    tmp: tempfile.TemporaryDirectory | None = None
    root = source
    if source.is_file() and source.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(source) as zf:
            zf.extractall(tmp.name)
        root = Path(tmp.name)

    try:
        sym_files = list(root.rglob("*.kicad_sym"))
        fp_files = list(root.rglob("*.kicad_mod"))
        model_files = [p for p in root.rglob("*") if p.suffix.lower() in _MODEL_SUFFIXES]

        # 1. Footprints + their 3D models.
        for fp_path in fp_files:
            fp_text = fp_path.read_text(encoding="utf-8", errors="replace")
            model = _pick_model(fp_path, fp_text, model_files)
            if model is not None:
                dest_model = libs.models / model.name
                shutil.copyfile(model, dest_model)
                fp_text = footprints.ensure_model(fp_text, model.name, model_var)
                result.models.append(model.name)
            else:
                result.warnings.append(f"no 3D model found for footprint {fp_path.name}")
            dest_fp = libs.footprints / fp_path.name
            dest_fp.write_text(fp_text, encoding="utf-8", newline="\n")
            result.footprints.append(fp_path.name)

        # 2. Symbols (footprint nickname rewritten on merge).
        incoming: list[str] = []
        for sp in sym_files:
            incoming.extend(symbols.extract_symbol_blocks(
                sp.read_text(encoding="utf-8", errors="replace")))
        if incoming:
            target = libs.symbols.read_text(encoding="utf-8", errors="replace")
            new_text, added = symbols.merge_into_library(target, incoming, nickname)
            libs.symbols.write_text(new_text, encoding="utf-8", newline="\n")
            result.symbols.extend(added)
    finally:
        if tmp is not None:
            tmp.cleanup()

    return result
