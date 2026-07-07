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
from PyQt5.QtGui import QPainter, QColor
from PyQt5.QtWidgets import QWidget, QFrame, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QHBoxLayout

from .. import theme as T
from ..util import run_populate, clear_layout
from .. import widgets as W


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


class PreviewCard(QFrame):
    """One elevation step (inset surface, 8px radius, no border): a quiet eyebrow,
    a render surface, and an optional dim caption. Empty state is a dim sentence."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ndinset")
        W.register_restyle(lambda: self.setStyleSheet(
            f"QFrame#ndinset{{background:{T.t('inset')};border:none;border-radius:8px;}}"))
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 14); self._lay.setSpacing(8)
        self._lay.addWidget(W.eyebrow(title))
        self._surface = QVBoxLayout(); self._lay.addLayout(self._surface)
        self._cap = QLabel(""); self._cap.setFont(T.ui_font(9))
        W.register_restyle(lambda: self._cap.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"))
        self._lay.addWidget(self._cap)

    def _clear_surface(self):
        clear_layout(self._surface)

    def set_image(self, img):
        self._clear_surface()
        if img is None or img.isNull():
            self.set_empty("Not Available")
            return
        lab = QLabel(); lab.setAlignment(Qt.AlignCenter)
        from PyQt5.QtGui import QPixmap
        lab.setPixmap(QPixmap.fromImage(img).scaledToWidth(280, Qt.SmoothTransformation))
        self._surface.addWidget(lab)

    def set_widget(self, w: QWidget):
        self._clear_surface(); self._surface.addWidget(w)

    def set_caption(self, text: str):
        self._cap.setText(text or "")
        self._cap.setVisible(bool(text))

    def caption_text(self) -> str:
        """Read-only accessor for the current caption label text."""
        return self._cap.text()

    def set_empty(self, sentence: str):
        self._clear_surface()
        lab = W.body(sentence, dim=True); lab.setAlignment(Qt.AlignCenter)
        self._surface.addWidget(lab)
        self.set_caption("")


class PartDetail(QWidget):
    """The right pane: identity header + three stacked preview cards. show(row)
    swaps content; each preview renders off the GUI thread via run_populate."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)
        self._mpn = QLabel(""); self._mpn.setFont(T.ui_font(13, semibold=True))
        W.register_restyle(lambda: self._mpn.setStyleSheet(
            f"color:{T.t('txt1')};background:transparent;"))
        self._meta = W.body("", dim=True); self._meta.setWordWrap(True)
        lay.addWidget(self._mpn); lay.addWidget(self._meta)
        self._sym = PreviewCard("Symbol")
        self._fp = PreviewCard("Footprint")
        self._mdl = PreviewCard("3D Model")
        for c in (self._sym, self._fp, self._mdl):
            lay.addWidget(c)
        lay.addStretch(1)

    def show(self, row: Optional[dict]):
        if not row:
            self._mpn.setText(""); self._meta.setText("")
            for c in (self._sym, self._fp, self._mdl):
                c.set_empty("Select A Part")
            return
        self._mpn.setText(str(row.get("mpn") or row.get("name") or ""))
        bits = [b for b in (row.get("manufacturer"), row.get("description")) if b]
        self._meta.setText(" · ".join(str(b) for b in bits))
        self._render_symbol(row)
        self._render_footprint(row)
        self._render_model(row)

    def _render_symbol(self, row):
        block = symbol_block_for(self._ctx.cfg, (row.get("symbols") or [None])[0])
        if not block:
            self._sym.set_empty("No Symbol"); return
        run_populate(self._ctx, lambda: R.render_symbol_image(block),
                     lambda img, ok: self._sym.set_image(img if ok else None))

    def _render_footprint(self, row):
        path = footprint_path_for(self._ctx.cfg, row)
        if not path:
            self._fp.set_empty("No Footprint"); return

        def job():
            return R.render_footprint_image(path), R.footprint_summary(path)

        def done(res, ok):
            img, summ = res if res else (None, None)
            self._fp.set_image(img)
            if summ:
                self._fp.set_caption(
                    f"{summ['pads']} Pads · {summ['width_mm']} × "
                    f"{summ['height_mm']} mm")
        run_populate(self._ctx, job, done)

    def _render_model(self, row):
        path = model_path_for(self._ctx.cfg, row)
        if not path:
            self._mdl.set_empty("No 3D Model"); return

        def job():
            return resolve_model_render(path), R.step_summary(path)

        def done(res, ok):
            (kind, payload), summ = res if res else (("none", None), None)
            if kind == "none":
                self._mdl.set_empty("3D Preview Unavailable"); return
            self._mdl.set_widget(MeshView(kind, payload))
            if summ:
                sz = summ.get("size_mm") or []
                if len(sz) == 3:
                    self._mdl.set_caption(
                        f"{summ['triangles']} Triangles · "
                        f"{sz[0]} × {sz[1]} × {sz[2]} mm")
        run_populate(self._ctx, job, done)


def _asset_dot_color(row: dict) -> str:
    """Semantic asset-state color: err=dangling, warn=missing model, else ok."""
    if row.get("dangling"):
        return T.t("err")
    if not row.get("has_model") or not row.get("has_footprint"):
        return T.t("warn")
    return T.t("ok")


class PartsList(QWidget):
    """Selectable master list of grouped parts + a client-side search filter.
    One 6px asset-state dot per row (the only color); selection uses the native
    row wash. No borders."""

    def __init__(self, rows, on_select, parent=None):
        super().__init__(parent)
        self._rows = list(rows)
        self._on_select = on_select
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)

        self._search = QLineEdit(); self._search.setPlaceholderText("Search Parts...")
        self._search.setFont(T.ui_font(10))
        self._search.textChanged.connect(self.filter)
        W.register_restyle(lambda: self._search.setStyleSheet(
            f"QLineEdit{{background:{T.t('inset')};border:none;border-radius:6px;"
            f"padding:6px 10px;color:{T.t('txt1')};}}"))
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.currentRowChanged.connect(self._on_row)
        W.register_restyle(self._restyle_list)
        lay.addWidget(self._list, 1)

        self._build_items(self._rows)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _restyle_list(self):
        self._list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;}}"
            f"QListWidget::item{{color:{T.t('txt1')};padding:7px 8px;border-radius:6px;}}"
            f"QListWidget::item:hover{{background:{T.t('card_hover')};}}"
            f"QListWidget::item:selected{{background:{T.t('inset')};color:{T.t('txt1')};}}")

    def _build_items(self, rows):
        self._list.clear()
        self._visible = []
        for row in rows:
            label = str(row.get("mpn") or row.get("name") or "")
            it = QListWidgetItem("  " + label)
            it.setForeground(QColor(_asset_dot_color(row)))  # dot proxy via a leading swatch
            it.setData(Qt.UserRole, row)
            it.setFont(T.mono_font(10))
            self._list.addItem(it)
            self._visible.append(row)

    def filter(self, query: str):
        q = (query or "").strip().lower()
        rows = [r for r in self._rows
                if q in str(r.get("mpn") or "").lower()
                or q in str(r.get("name") or "").lower()
                or q in str(r.get("manufacturer") or "").lower()] if q else self._rows
        self._build_items(rows)
        if self._list.count():
            self._list.setCurrentRow(0)

    def visible_count(self) -> int:
        return self._list.count()

    def select_index(self, i: int):
        self._list.setCurrentRow(i)

    def _on_row(self, i: int):
        if i < 0 or i >= len(self._visible):
            return
        if self._on_select:
            self._on_select(self._visible[i])
