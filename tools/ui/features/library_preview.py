"""Library preview building blocks — master list, detail pane, and the symbol/
footprint/3D preview cards, all wired to the pure fp_render renderers.

Kept separate from library.py so the feature file stays orchestration-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import LibraryManager as LM
import fp_render as R


def symbol_block_for(cfg: dict, name: str) -> Optional[str]:
    """The raw (symbol …) block text for `name`, or None if absent."""
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not name or not sym_path.exists():
        return None
    try:
        for b in LM.extract_symbol_blocks(LM.read_text(sym_path)):
            if LM.extract_symbol_name(b) == name:
                return b
    except Exception:  # noqa: BLE001 - a preview never crashes the UI
        return None
    return None


def footprint_path_for(cfg: dict, row: dict) -> Optional[Path]:
    """Path to the row's .kicad_mod, or None if the row has no footprint."""
    stem = row.get("footprint")
    if not stem:
        return None
    p = Path(cfg.get("FootprintLib", "")) / f"{stem}.kicad_mod"
    return p if p.exists() else None


def model_path_for(cfg: dict, row: dict) -> Optional[Path]:
    """Path to the row's 3D model file, or None if the row has no model."""
    name = row.get("model")
    if not name:
        return None
    p = Path(cfg.get("ModelLib", "")) / name
    return p if p.exists() else None


def resolve_model_render(path: Optional[Path]):
    """Decide how to show a 3D model, best available first:
      ("mesh", (verts, faces)) — interactive mesh loaded
      ("image", QImage)        — static thumbnail only
      ("none", None)           — nothing renderable
    load_step_mesh dispatches STEP vs WRL by suffix and returns (None, None)
    when the backend is missing, so this covers have_3d() False implicitly.
    """
    if not path or not Path(path).exists():
        return ("none", None)
    try:
        verts, faces = R.load_step_mesh(path)
    except Exception:  # noqa: BLE001
        verts = faces = None
    if verts is not None and faces is not None and len(faces):
        return ("mesh", (verts, faces))
    try:
        img = R.render_step_image(path)
    except Exception:  # noqa: BLE001
        img = None
    if img is not None and not img.isNull():
        return ("image", img)
    return ("none", None)
