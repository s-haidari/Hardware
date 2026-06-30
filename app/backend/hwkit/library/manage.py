"""
manage.py — library maintenance ops ported from tools/LibraryManager.py:
dedupe symbols, remove a part, and batch-process a downloads folder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..kicad import symbols as S
from .importer import LibPaths, import_part


def dedupe(libs: LibPaths, *, dry_run: bool = False) -> int:
    """Drop duplicate symbol blocks (keep first). Returns count removed."""
    if not libs.symbols.exists():
        return 0
    text = libs.symbols.read_text(encoding="utf-8", errors="replace")
    new_text, removed = S.dedupe_library(text)
    if removed and not dry_run:
        libs.symbols.write_text(new_text, encoding="utf-8", newline="\n")
    return removed


def remove_part(libs: LibPaths, name: str, *, remove_footprint: bool = False,
                dry_run: bool = False) -> dict:
    """Remove a symbol by name; optionally its footprint + 3D model too."""
    from . import catalog
    result = {"symbol_removed": 0, "footprint_removed": False, "model_removed": False}

    # Resolve the symbol's footprint name (it differs from the symbol name) before removal.
    fp_name = name
    if libs.symbols.exists():
        for entry in catalog.list_symbols(libs.symbols):
            if entry.symbol == name and entry.footprint:
                fp_name = entry.footprint.split(":")[-1]
                break
        text = libs.symbols.read_text(encoding="utf-8", errors="replace")
        new_text, removed = S.remove_symbol(text, name)
        result["symbol_removed"] = removed
        if removed and not dry_run:
            libs.symbols.write_text(new_text, encoding="utf-8", newline="\n")
    if remove_footprint:
        fp = libs.footprints / f"{fp_name}.kicad_mod"
        if fp.exists():
            result["footprint_removed"] = True
            if not dry_run:
                fp.unlink()
        for suffix in (".step", ".stp", ".wrl", ".STEP", ".STP"):
            mp = libs.models / f"{fp_name}{suffix}"
            if mp.exists():
                result["model_removed"] = True
                if not dry_run:
                    mp.unlink()
    return result


@dataclass
class ProcessResult:
    imported: list[str] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def process_downloads(libs: LibPaths, downloads_dir: Path, *,
                      clear: bool = True, dry_run: bool = False) -> ProcessResult:
    """Import every .zip in ``downloads_dir``, then optionally delete them."""
    res = ProcessResult()
    if not downloads_dir.exists():
        return res
    for zip_path in sorted(downloads_dir.glob("*.zip")):
        if dry_run:
            res.imported.append(zip_path.name)
            continue
        out = import_part(zip_path, libs)
        res.imported.extend(out.symbols)
        res.warnings.extend(out.warnings)
        if clear:
            zip_path.unlink()
            res.cleared.append(zip_path.name)
    return res
