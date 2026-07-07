"""Library preview building blocks — master list, detail pane, and the symbol/
footprint/3D preview cards, all wired to the pure fp_render renderers.

Kept separate from library.py so the feature file stays orchestration-only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import LibraryManager as LM
import fp_render as R
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget

from .. import theme as T


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


class MeshView(QWidget):
    """Interactive 3D model view. kind='mesh' -> orbit/zoom via paint_mesh;
    kind='image' -> a static thumbnail painted to fit. No borders (the parent
    card is the single elevation step)."""

    def __init__(self, kind: str, payload, px: int = 300, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._img = payload if kind == "image" else None
        self._verts, self._faces = payload if kind == "mesh" else (None, None)
        self._rx, self._ry, self._zoom = -60.0, -35.0, 1.0
        self._drag = None
        self.setMinimumHeight(px)
        self.setFixedHeight(px)
        if self.interactive:
            self.setCursor(Qt.OpenHandCursor)

    @property
    def interactive(self) -> bool:
        return self._kind == "mesh"

    def paintEvent(self, _e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        if self._kind == "mesh":
            R.paint_mesh(qp, w, h, self._verts, self._faces,
                         rot_x=self._rx, rot_y=self._ry, zoom=self._zoom)
        elif self._kind == "image" and self._img is not None:
            scaled = self._img.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            qp.drawImage(x, y, scaled)
        qp.end()

    def mousePressEvent(self, e):
        if self.interactive:
            self._drag = e.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self.interactive and self._drag is not None:
            d = e.pos() - self._drag
            self._ry += d.x() * 0.6
            self._rx += d.y() * 0.6
            self._drag = e.pos()
            self.update()

    def mouseReleaseEvent(self, _e):
        if self.interactive:
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)

    def wheelEvent(self, e):
        if self.interactive:
            factor = 1.12 if e.angleDelta().y() > 0 else 1 / 1.12
            self._zoom = max(0.4, min(4.0, self._zoom * factor))
            self.update()
            e.accept()
        else:
            e.ignore()
