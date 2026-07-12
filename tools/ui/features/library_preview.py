"""Library preview building blocks — master list, detail pane, and the symbol/
footprint/3D preview cards, all wired to the pure fp_render renderers.

Kept separate from library.py so the feature file stays orchestration-only.
The detail pane surfaces what a part IS (identity + live Mouser sourcing), lets
identity fields be edited in place, and lets a missing asset (symbol / footprint /
3D model) be dropped in by file-picker or drag-and-drop. Inline field edits write
to disk immediately but batch behind an explicit Save (one commit + push, not one
per keystroke — see the save bar); structural mutations (drop-in, rename, delete)
commit + push at once through the pure LibraryManager helpers.
"""
from __future__ import annotations

import os
from html import escape
from pathlib import Path
from typing import Callable, Optional

import LibraryManager as LM
import nd_commit_msg as CM
import fp_render as R
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint, QSize, QRect
from PyQt5.QtGui import QPainter, QFontMetrics
from PyQt5.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QLineEdit, QFileDialog,
                             QInputDialog, QDialog, QCheckBox, QGridLayout,
                             QPlainTextEdit, QPushButton, QRadioButton, QButtonGroup,
                             QLayout, QSizePolicy, QMenu, QProgressBar, QScrollArea,
                             QAbstractItemView, QDialogButtonBox)

from .. import theme as T
from ..prose import plural
from ..util import LogSink, run_populate, clear_layout, fmt_countdown
from .. import widgets as W
from .. import units as U
from .. import icons


def _headless() -> bool:
    """True under the offscreen Qt platform (tests / render_gate). Native drop-target
    registration (OLE RegisterDragDrop) faults there on Windows because the widget has
    no real HWND, corrupting the heap and crashing later — so skip it headlessly. The
    real app runs a native platform and gets full drag-and-drop. Mirrors fp_render."""
    return os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")


class FlowLayout(QLayout):
    """A left-to-right layout that wraps to a new line when it runs out of width —
    the Qt equivalent of CSS flex-wrap (the mockup's .pills). Qt ships no wrapping
    layout, so this is the standard minimal implementation."""

    def __init__(self, parent=None, hspacing=7, vspacing=7):
        super().__init__(parent)
        self._items = []
        self._hs, self._vs = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Horizontal)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self._layout(QRect(0, 0, w, 0), test=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._layout(rect, test=False)

    def sizeHint(self): return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() and line_h > 0:
                x = rect.x(); y += line_h + self._vs; line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._hs
            line_h = max(line_h, h)
        return y + line_h - rect.y()


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


def resolve_model_render(path: Optional[Path]) -> tuple[str, object]:
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


def attach_model_to_footprint(cfg: dict, footprint_stem: str, model_filename: str) -> bool:
    """Write the `(model "${MY3DMODELS}/<file>" …)` line into the footprint file
    on disk — the SAME real, KiCad/BOM/auto_assign-visible tie the ZIP-import and
    Repair/Auto-Assign paths produce (LM.ensure_footprint_model). Idempotent: a
    footprint that already points at this model is left byte-for-byte unchanged.

    Returns True when the footprint's model line is now `<file>` (written or already
    correct), False when the footprint file is missing/unwritable so the caller can
    fall back to the JSON override. Runs under LM._LIB_LOCK so it never interleaves
    with a watcher import / auto-assign writing the same file."""
    fp = Path(cfg.get("FootprintLib", "")) / f"{footprint_stem}.kicad_mod"
    if not fp.exists():
        return False
    try:
        with LM._LIB_LOCK:
            text = LM.read_text(fp)
            new = LM.ensure_footprint_model(text, model_filename)
            if new != text:
                LM.write_text(fp, new)
        return True
    except Exception:  # noqa: BLE001 - fall back to the override rather than crash a drop-in
        return False


def apply_model_override(cfg: dict, footprint_stem: str, model_filename: str) -> None:
    """Fallback tie for a footprint that cannot be edited on disk: persist the
    footprint->model association in the group-override side map (pure JSON; no
    library mutation). resolve_model_render re-reads afterward. Prefer
    attach_model_to_footprint, which writes the real footprint line."""
    ov = LM.load_group_overrides(cfg)
    ov.setdefault("model", {})[footprint_stem] = model_filename
    LM.save_group_overrides(cfg, ov)


class MeshView(QWidget):
    """Interactive 3D model view. kind='mesh' -> orbit/zoom via paint_mesh;
    kind='image' -> a static thumbnail painted to fit. No borders (the parent
    card is the single elevation step)."""

    def __init__(self, kind: str, payload, px: int = 300, parent=None, fill: bool = False):
        super().__init__(parent)
        self._kind = kind
        self._img = payload if kind == "image" else None
        self._verts, self._faces = payload if kind == "mesh" else (None, None)
        self._rx, self._ry, self._zoom = -60.0, -35.0, 1.0
        self._drag = None
        # fill=True (the mockup's files-row): the view expands to fill its card's art
        # region so a big-3D-left / stacked-symbol+footprint-right layout can drive the
        # size. fill=False keeps the legacy fixed square (standalone / test callers).
        if fill:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMinimumHeight(96)
        else:
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
            # SHELL-02: paint_mesh does native mesh math on the GUI thread on every
            # orbit/zoom repaint; a malformed mesh must never take the app down
            # mid-paint. A Python-level fault just skips this frame.
            try:
                R.paint_mesh(qp, w, h, self._verts, self._faces,
                             rot_x=self._rx, rot_y=self._ry, zoom=self._zoom)
            except Exception:  # noqa: BLE001
                pass
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


def _editable_value(value: str, on_commit: Callable[[str], None],
                    mono: bool = False, placeholder: str = "") -> QLineEdit:
    """A value that reads as quiet text but is click-to-edit: a borderless line
    edit that lifts to an inset wash on hover/focus and commits (once) on Enter or
    focus-out. Empty shows a dim placeholder so an unset field invites a value."""
    e = QLineEdit(value or "")
    e.setFont(T.mono_font(9.5) if mono else T.ui_font(10))
    e.setPlaceholderText(placeholder)
    e.setCursorPosition(0)
    e.setToolTip("Click to edit")
    state = {"last": value or ""}

    def commit():
        # BUG-1c: a teardown / focus-loss with no real user edit must NOT commit —
        # editingFinished fires when a still-live field is destroyed during a list
        # rebuild, and an unguarded commit would cascade into the autofill dialog
        # storm. Only a genuinely modified field writes; then the flag is cleared.
        if not e.isModified():
            return
        v = e.text().strip()
        e.setModified(False)
        if v != state["last"]:
            state["last"] = v
            on_commit(v)
    e.editingFinished.connect(commit)
    W.register_restyle(lambda: e.setStyleSheet(
        f"QLineEdit{{background:transparent;border:none;border-radius:6px;"
        f"padding:4px 7px;color:{T.t('txt1')};}}"
        f"QLineEdit:hover{{background:{T.t('ctl')};}}"
        f"QLineEdit:focus{{background:{T.t('inset')};color:{T.t('txt1')};}}"), e)
    return e


class _MultilineEdit(QPlainTextEdit):
    """A borderless multi-line value that wraps at the widget width and grows
    vertically — long content is never clipped and never scrolls sideways
    (design rule). Commits (once) on focus-out, with the same `isModified`-style
    guard as _editable_value so a teardown/focus-loss with no real edit is inert."""

    def __init__(self, value: str, on_commit: Callable[[str], None], placeholder: str = ""):
        super().__init__(value or "")
        self._on_commit = on_commit
        self._last = value or ""
        self.setFont(T.ui_font(10))
        self.setPlaceholderText(placeholder)
        self.setToolTip("Click to edit")
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.textChanged.connect(self._sync_height)
        W.register_restyle(lambda: self.setStyleSheet(
            f"QPlainTextEdit{{background:transparent;border:none;border-radius:6px;"
            f"padding:3px 6px;color:{T.t('txt1')};}}"
            f"QPlainTextEdit:hover{{background:{T.t('ctl')};}}"
            f"QPlainTextEdit:focus{{background:{T.t('inset')};color:{T.t('txt1')};}}"), self)
        self._sync_height()

    def _sync_height(self):
        # Grow to fit the wrapped content so nothing is clipped; never scroll sideways.
        doc = self.document()
        doc.setTextWidth(max(0, self.viewport().width()))
        h = int(doc.size().height()) + 10
        self.setFixedHeight(max(30, h))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_height()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        # Only a genuine edit writes (mirrors _editable_value's isModified guard).
        if not self.document().isModified():
            return
        v = self.toPlainText().strip()
        self.document().setModified(False)
        if v != self._last:
            self._last = v
            self._on_commit(v)


def _editable_multiline(value: str, on_commit: Callable[[str], None],
                        placeholder: str = "") -> QPlainTextEdit:
    """A wrapping, click-to-edit multi-line value (see _MultilineEdit). Use for long
    free text (Description) that a single-line QLineEdit would clip."""
    return _MultilineEdit(value, on_commit, placeholder)


_subhead = W.subhead   # shared quiet Title-case region label (see ui.widgets.subhead)


class PreviewCard(QFrame):
    """A mockup .file card: a raised, hairline-bordered tile whose art region FILLS
    (a centered symbol/footprint render, or the interactive 3D MeshView) with a bottom
    caption bar carrying the asset name, a Linked/empty status, and — once filled — a
    Replace affordance. A hover-revealed Expand button opens the render in a lightbox.
    Empty state is one quiet line plus a drop-in affordance when the missing asset can
    be supplied (file drag-drop or a picker); a structurally blocked drop-in names the
    missing prerequisite instead of offering a no-op picker."""

    def __init__(self, title: str, parent=None, glyph: str = ""):
        super().__init__(parent)
        self._glyph = glyph          # empty-state glyph (symbol/footprint/cube), if any
        self._name = title
        self.setObjectName("filecard")
        W.register_restyle(lambda: self.setStyleSheet(
            f"QFrame#filecard{{background:{T.t('raised')};border:1px solid {T.t('hairline')};"
            f"border-radius:8px;}}"), self)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # Art region (fills): a dark wash with the centered render. Both the Expand
        # overlay and — in the empty state — the drop-in affordance live here.
        self._art = QFrame(); self._art.setObjectName("fileart")
        W.register_restyle(lambda: self._art.setStyleSheet(
            f"QFrame#fileart{{background:{T.t('field')};border:none;"
            f"border-top-left-radius:8px;border-top-right-radius:8px;}}"), self._art)
        self._surface = QVBoxLayout(self._art)
        self._surface.setContentsMargins(12, 12, 12, 12); self._surface.setSpacing(0)
        outer.addWidget(self._art, 1)

        # Caption bar (bottom): the asset name + a Linked/empty status, a Replace ghost
        # once filled (LIB-11), and a dim second line for the render's dimensions.
        cap = QWidget()
        capv = QVBoxLayout(cap); capv.setContentsMargins(11, 8, 11, 9); capv.setSpacing(3)
        namerow = QHBoxLayout(); namerow.setContentsMargins(0, 0, 0, 0); namerow.setSpacing(8)
        self._name_lbl = QLabel(title); self._name_lbl.setFont(T.ui_font(9.5, semibold=True))
        W.register_restyle(lambda: self._name_lbl.setStyleSheet(
            f"color:{T.t('txt1')};background:transparent;"), self._name_lbl)
        namerow.addWidget(self._name_lbl, 0); namerow.addStretch(1)
        self._replace_btn = W.btn("Replace", "ghost", "Replace this asset with a file",
                                  lambda: self._pick())
        self._replace_btn.setVisible(False)
        namerow.addWidget(self._replace_btn, 0)
        # Add affordance for the empty state — the mockup carries it in the caption
        # (an "Add ▾" slot), not the art. Its text is the drop-in prompt so the
        # picker/tests read it straight ("Add Footprint" / "Add 3D Model").
        self._add_btn = W.btn("Add", "ghost", "Add this asset from a file",
                              lambda: self._pick())
        self._add_btn.setVisible(False)
        namerow.addWidget(self._add_btn, 0)
        self._dot = QLabel(); self._dot.setFixedSize(6, 6)
        W.register_restyle(lambda: self._dot.setStyleSheet(
            f"background:{T.t('ok')};border-radius:3px;"), self._dot)
        self._dot.hide()
        self._status = QLabel(""); self._status.setFont(T.ui_font(9))
        W.register_restyle(lambda: self._status.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"), self._status)
        namerow.addWidget(self._dot, 0, Qt.AlignVCenter); namerow.addWidget(self._status, 0)
        capv.addLayout(namerow)
        self._cap = QLabel(""); self._cap.setFont(T.ui_font(9))
        self._cap.setWordWrap(True)          # a long footprint name wraps, never clips
        W.register_restyle(lambda: self._cap.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"), self._cap)
        self._cap.setVisible(False)
        capv.addWidget(self._cap)
        outer.addWidget(cap, 0)

        # Expand → lightbox (mockup .exp): a hover-revealed overlay floated top-right of
        # the art, hidden until the card is filled AND the pointer is over the card.
        self._expand_btn = QPushButton(self._art)
        self._expand_btn.setObjectName("expbtn")
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setCursor(Qt.PointingHandCursor)
        self._expand_btn.setToolTip("Expand this preview")
        self._expand_btn.clicked.connect(self._open_lightbox)
        W.register_restyle(self._restyle_expand, self._expand_btn)
        self._restyle_expand()
        self._expand_btn.hide()

        self._on_file: Optional[Callable[[str], None]] = None
        self._accept: tuple = ()
        self._prompt = ""
        # A drop-in can be structurally impossible for the current part (e.g. a 3D
        # model needs a footprint to attach to; a footprint needs a symbol to link
        # to). When blocked, the picker/drag-drop are inert and the empty state
        # names the missing prerequisite instead of offering a no-op file picker.
        self._can_drop = True
        self._blocked_reason = ""
        # Lightbox payload for the current render, driving Expand:
        # ('image', QImage) | ('mesh', (kind, payload)) | ('none', None).
        self._light: tuple = ("none", None)

    def _restyle_expand(self):
        self._expand_btn.setIcon(W.svg_icon(icons.GLYPHS["expand"], 13, T.t("txt2")))
        self._expand_btn.setStyleSheet(
            "QPushButton#expbtn{background:rgba(0,0,0,0.42);border:none;border-radius:6px;}"
            "QPushButton#expbtn:hover{background:rgba(0,0,0,0.62);}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._expand_btn.move(max(0, self._art.width() - 24 - 8), 8)

    def enterEvent(self, e):
        # Reveal Expand only when there is a render to enlarge (mockup: exp opacity 0→1
        # on hover of a filled card; a missing card has nothing to expand).
        if self._light[0] != "none":
            self._expand_btn.show(); self._expand_btn.raise_()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._expand_btn.hide()
        super().leaveEvent(e)

    def _set_status(self, linked: bool):
        """The caption's Linked dot+word (mockup .st) — shown only when the card holds
        a real asset; a missing card leaves the status blank (its art carries the note)."""
        self._dot.setVisible(linked)
        self._status.setText("Linked" if linked else "")
        self._status.setVisible(linked)

    def _open_lightbox(self):
        """Enlarge the current render in a modal lightbox (mockup .lb). Images paint
        scaled-to-fit; a mesh stays interactive (orbit/zoom) at the larger size."""
        kind, payload = self._light
        if kind == "none":
            return None
        dlg = QDialog(self); dlg.setObjectName("lightbox")
        dlg.setWindowTitle(self._name); dlg.setModal(True)
        v = QVBoxLayout(dlg); v.setContentsMargins(10, 10, 10, 10); v.setSpacing(10)
        head = QHBoxLayout(); head.setContentsMargins(4, 2, 4, 0)
        title = QLabel(self._name); title.setFont(T.ui_font(11, semibold=True))
        W.register_restyle(lambda: title.setStyleSheet(
            f"color:{T.t('txt1')};background:transparent;"), title)
        head.addWidget(title); head.addStretch(1)
        head.addWidget(W.btn("Close", "ghost", "Close this preview", dlg.accept))
        v.addLayout(head)
        art = QFrame(); art.setObjectName("lbart"); art.setFixedSize(560, 400)
        W.register_restyle(lambda: art.setStyleSheet(
            f"QFrame#lbart{{background:{T.t('field')};border:none;border-radius:8px;}}"), art)
        al = QVBoxLayout(art); al.setContentsMargins(16, 16, 16, 16)
        if kind == "image":
            al.addWidget(MeshView("image", payload, fill=True))
        else:
            k, pl = payload
            al.addWidget(MeshView(k, pl, fill=True))
        v.addWidget(art, 0, Qt.AlignCenter)
        W.register_restyle(lambda: dlg.setStyleSheet(
            f"QDialog#lightbox{{background:{T.t('surface')};}}"), dlg)
        dlg.setStyleSheet(f"QDialog#lightbox{{background:{T.t('surface')};}}")
        if not _headless():
            dlg.show()                       # non-blocking (never .exec_ — keeps the loop live)
        return dlg

    def enable_dropin(self, on_file: Callable[[str], None], suffixes, prompt: str):
        """Let this card receive a replacement asset: `on_file(path)` installs it.
        Accepts a matching-suffix file drag-drop and shows a picker in the empty
        state. `suffixes` like ('.step', '.wrl')."""
        self._on_file = on_file
        self._accept = tuple(s.lower() for s in suffixes)
        self._prompt = prompt
        self._add_btn.setText(prompt)        # caption Add slot reads the real prompt
        # File-picker drop-in always works; native drag-drop only on a real platform
        # (see _headless — RegisterDragDrop faults under offscreen Qt on Windows).
        if not _headless():
            self.setAcceptDrops(True)

    def set_droppable(self, can_drop: bool, blocked_reason: str = ""):
        """Gate the drop-in affordance to a structural prerequisite. When can_drop
        is False, the picker + drag-drop + Replace are all suppressed and the empty
        state shows `blocked_reason` (e.g. 'Add A Footprint First') with no no-op
        Add button. Call from the owner's render before it fills or empties the card."""
        self._can_drop = bool(can_drop)
        self._blocked_reason = blocked_reason or ""

    def _droppable(self) -> bool:
        return bool(self._on_file) and self._can_drop

    def _matches(self, path: str) -> bool:
        return bool(self._accept) and Path(path).suffix.lower() in self._accept

    def _pick(self):
        if not self._droppable():
            return
        filt = f"{self._prompt} ({' '.join('*' + s for s in self._accept)})"
        fn, _ = QFileDialog.getOpenFileName(self, self._prompt, "", filt)
        if fn and self._on_file:
            self._on_file(fn)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if self._droppable() and md.hasUrls() and any(self._matches(u.toLocalFile()) for u in md.urls()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if not self._droppable():
            e.ignore()
            return
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if self._matches(p):
                e.acceptProposedAction()
                self._on_file(p)
                return
        e.ignore()

    def _clear_surface(self):
        clear_layout(self._surface)

    def set_image(self, img):
        self._clear_surface()
        if img is None or img.isNull():
            self.set_empty("Not Available")
            return
        # Scale-to-fit the art region (KeepAspectRatio) so the render fills the tall
        # card instead of marooning a fixed-width thumbnail (mockup .file .art).
        self._surface.addWidget(MeshView("image", img, fill=True))
        self._light = ("image", img)
        self._set_status(True)
        self._add_btn.setVisible(False)
        self._replace_btn.setVisible(self._droppable())   # LIB-11: offer a swap

    def set_mesh(self, kind, payload):
        """Mount the interactive 3D model (mockup .file.big .cube-stage): a filling,
        orbit/zoom MeshView, remembered so Expand can re-open it in the lightbox."""
        self._clear_surface()
        self._surface.addWidget(MeshView(kind, payload, fill=True))
        self._light = ("mesh", (kind, payload))
        self._set_status(True)
        self._add_btn.setVisible(False)
        self._replace_btn.setVisible(self._droppable())   # LIB-11: offer a swap

    def set_widget(self, w: QWidget):
        self._clear_surface(); self._surface.addWidget(w)
        self._light = ("none", None)                      # unknown content — no lightbox
        self._set_status(True)
        self._add_btn.setVisible(False)
        self._replace_btn.setVisible(self._droppable())   # LIB-11: offer a swap

    def set_caption(self, text: str):
        self._cap.setText(text or "")
        self._cap.setVisible(bool(text))

    def caption_text(self) -> str:
        """Read-only accessor for the current caption label text."""
        return self._cap.text()

    def _compact_empty(self, sentence: str) -> QWidget:
        """A tight glyph + one quiet line that fits the short stacked file cards
        (the shared W.empty_state's 40px margins clip in a ~110px art region)."""
        box = QWidget(); v = QVBoxLayout(box)
        v.setContentsMargins(6, 6, 6, 6); v.setSpacing(7)
        v.addStretch(1)
        if self._glyph:
            g = QLabel(); g.setAlignment(Qt.AlignHCenter)

            def _tint(_g=g):
                _g.setPixmap(W.svg_icon(self._glyph, 22, T.t("txt3")).pixmap(22, 22))
            W.register_restyle(_tint, g); _tint()
            v.addWidget(g, 0, Qt.AlignHCenter)
        lab = QLabel(sentence); lab.setAlignment(Qt.AlignHCenter); lab.setWordWrap(True)
        lab.setFont(T.ui_font(9))
        W.register_restyle(lambda: lab.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"), lab)
        v.addWidget(lab, 0, Qt.AlignHCenter)
        v.addStretch(1)
        return box

    def set_empty(self, sentence: str):
        self._clear_surface()
        self._light = ("none", None)            # nothing to enlarge
        self._expand_btn.setVisible(False)
        self._set_status(False)
        self._replace_btn.setVisible(False)     # nothing to replace yet — Add lives in caption
        # A structurally blocked drop-in names the missing prerequisite (e.g. 'Add A
        # Footprint First') and offers NO Add — a picker there would be a no-op.
        droppable = self._droppable()
        self._add_btn.setVisible(droppable)
        line = self._blocked_reason if (self._on_file and not self._can_drop
                                        and self._blocked_reason) else sentence
        self._surface.addWidget(self._compact_empty(line))
        self.set_caption("")


class _AutofillDialog(QDialog):
    """LIB-05: preview current-vs-Mouser identity values and choose what to write.

    The two mode buttons set the checkboxes en masse (fill-blanks / overwrite-all);
    individual toggles override. Only fields Mouser has a *different* value for are
    shown. plan() returns {row_key: value} for the checked fields.
    """

    _MONO = {"mpn", "mouser_pn"}

    def __init__(self, row, fetched, parent=None):
        super().__init__(parent)
        self._row = dict(row or {})
        self._fetched = dict(fetched or {})
        self._checks: dict = {}
        self.setWindowTitle("Autofill From Mouser")
        self.setModal(True)
        self.setMinimumWidth(440)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(14)
        lay.addWidget(W.eyebrow("Autofill From Mouser"))
        num = self._fetched.get("mpn") or self._fetched.get("mouser_pn") or "this part"
        sub = W.body(f"Review what Mouser has for {num}, then choose what to write.", dim=True)
        sub.setWordWrap(True); lay.addWidget(sub)

        modes = QHBoxLayout(); modes.setSpacing(8)
        modes.addWidget(W.btn("Fill Blanks Only", "ghost",
                              "Check only the fields that are currently empty",
                              lambda: self.set_mode("blanks")))
        modes.addWidget(W.btn("Overwrite All", "ghost",
                              "Check every field Mouser has a value for",
                              lambda: self.set_mode("overwrite")))
        modes.addStretch(1)
        lay.addLayout(modes)

        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(12)
        grid.setColumnStretch(2, 1)
        r = 0
        for row_key, _prop, label in LM.AUTOFILL_FIELDS:
            new = (self._fetched.get(row_key) or "").strip()
            cur = (self._row.get(row_key) or "").strip()
            if not new or new == cur:
                continue
            cb = QCheckBox(); self._checks[row_key] = cb
            grid.addWidget(cb, r, 0, Qt.AlignTop)
            grid.addWidget(W.body(label), r, 1, Qt.AlignTop)
            col = QVBoxLayout(); col.setSpacing(1)
            nv = W.body(new, mono=row_key in self._MONO); nv.setWordWrap(True)
            col.addWidget(nv)
            if cur:
                ov = W.body(f"was: {cur}", dim=True, mono=row_key in self._MONO)
                ov.setWordWrap(True); col.addWidget(ov)
            grid.addLayout(col, r, 2)
            r += 1
        if not self._checks:
            lay.addWidget(W.body("Everything Mouser has already matches this part.", dim=True))
        lay.addLayout(grid)

        btns = QHBoxLayout(); btns.addStretch(1)
        btns.addWidget(W.btn("Cancel", "ghost",
                             "Close without changing this part", self.reject))
        btns.addWidget(W.btn("Apply", "primary",
                             "Write the checked fields onto this part", self.accept))
        lay.addLayout(btns)

        self.set_mode("blanks")
        W.register_restyle(
            lambda: self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), self)
        self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")

    def set_mode(self, mode: str):
        """Check the fields a mode would write: 'blanks' -> only empty ones,
        'overwrite' -> all candidates."""
        for row_key, cb in self._checks.items():
            cur = (self._row.get(row_key) or "").strip()
            cb.setChecked(mode == "overwrite" or (mode == "blanks" and not cur))

    def plan(self) -> dict:
        allow = {k for k, cb in self._checks.items() if cb.isChecked()}
        return LM.autofill_plan(self._row, self._fetched, "manual", allow=allow)


class DedupReviewDialog(QDialog):
    """Review-then-delete duplicate footprints (mockup §4.2 openDedup). One card per
    group of geometry-identical footprints; the footprint the most symbols reference is
    pre-marked Keep (unchecked), the rest pre-marked Delete (checked). A live
    'N to delete · M kept' counter tracks the choices; Delete Checked removes the
    checked files (undo-safe, real backend) and commits once."""

    def __init__(self, ctx, groups, on_changed=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._on_changed = on_changed
        self._checks: dict = {}          # stem -> QCheckBox (checked = delete)
        self._busy = False
        self.setWindowTitle("Review Duplicate Footprints")
        self.setModal(True); self.setMinimumWidth(560)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(12)
        lay.addWidget(W.eyebrow("Review Duplicate Footprints"))
        sub = W.body("Each group holds footprints with byte-identical geometry. The "
                     "footprint the most symbols reference is kept; check the ones to "
                     "delete. Density variants are never grouped.", dim=True)
        sub.setWordWrap(True); lay.addWidget(sub)

        body = QWidget(); bv = QVBoxLayout(body); bv.setContentsMargins(0, 0, 0, 0); bv.setSpacing(10)
        for group in groups:
            bv.addWidget(self._group_card(group))
        bv.addStretch(1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(body)
        scroll.setFrameShape(QFrame.NoFrame)
        # Let the dialog surface show through the scroll (its default viewport bg would
        # otherwise be a light rectangle in dark theme).
        body.setStyleSheet("background:transparent;")
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        lay.addWidget(scroll, 1)

        self._counter = QLabel(""); self._counter.setFont(T.ui_font(10))
        W.register_restyle(lambda: self._counter.setStyleSheet(
            f"color:{T.t('txt2')};background:transparent;"), self._counter)
        row = QHBoxLayout(); row.addWidget(self._counter); row.addStretch(1)
        row.addWidget(W.btn("Cancel", "ghost", "Close without deleting anything", self.reject))
        self._delete_btn = W.btn("Delete Checked", "primary",
                                 "Delete the checked footprint files and commit", self._delete_checked)
        row.addWidget(self._delete_btn)
        lay.addLayout(row)

        self._update_counter()
        W.register_restyle(lambda: self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), self)
        self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")

    def _pick_keeper(self, group) -> str:
        """Keep the footprint the most symbols reference (the 'used' one); ties break to
        the first stem so the choice is deterministic."""
        best, best_n = group[0], -1
        for stem in group:
            try:
                n = len(LM.symbols_referencing_footprint(self._ctx.cfg, stem))
            except Exception:  # noqa: BLE001
                n = 0
            if n > best_n:
                best, best_n = stem, n
        return best

    def _group_card(self, group) -> QWidget:
        keeper = self._pick_keeper(group)
        card = QFrame(); card.setObjectName("dedupcard")
        W.register_restyle(lambda: card.setStyleSheet(
            f"QFrame#dedupcard{{background:{T.t('raised')};border:1px solid {T.t('hairline')};"
            f"border-radius:8px;}}"), card)
        cv = QVBoxLayout(card); cv.setContentsMargins(14, 12, 14, 12); cv.setSpacing(8)
        cv.addWidget(_subhead(f"{len(group)} Identical Footprints"))
        for stem in group:
            r = QHBoxLayout(); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(10)
            cb = QCheckBox(); cb.setChecked(stem != keeper)
            cb.toggled.connect(lambda _c: self._update_counter())
            self._checks[stem] = cb
            r.addWidget(cb, 0)
            name = W.body(stem, mono=True); r.addWidget(name, 1)
            try:
                refs = len(LM.symbols_referencing_footprint(self._ctx.cfg, stem))
            except Exception:  # noqa: BLE001
                refs = 0
            if stem == keeper:
                tag = W.tag("Keep", "ok")
            else:
                tag = W.body(f"{refs} symbol{'' if refs == 1 else 's'}", dim=True)
            r.addWidget(tag, 0)
            cv.addLayout(r)
        return card

    def _update_counter(self):
        n_del = sum(1 for cb in self._checks.values() if cb.isChecked())
        n_keep = len(self._checks) - n_del
        self._counter.setText(f"{n_del} to delete · {n_keep} kept")
        if getattr(self, "_delete_btn", None) is not None:
            self._delete_btn.setEnabled(n_del > 0 and not self._busy)

    def _delete_checked(self):
        if self._busy:
            return
        stems = [s for s, cb in self._checks.items() if cb.isChecked()]
        if not stems:
            return
        from PyQt5.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Delete Footprints",
            f"Delete {len(stems)} duplicate footprint file"
            f"{'' if len(stems) == 1 else 's'}? This is undo-safe (files go to the "
            "library trash) and commits the result.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self._busy = True
        self._update_counter()

        def job():
            log = LogSink(self._ctx.services)
            removed = 0
            for stem in stems:
                res = LM.remove_footprint(self._ctx.cfg, stem, log)
                if res.get("removed"):
                    removed += 1
            if removed:
                LM.git_commit_push(self._ctx.cfg, log,
                                   f"chore(lib): remove {removed} duplicate footprint"
                                   f"{'' if removed == 1 else 's'}")
            return removed

        def done(removed, ok):
            self._busy = False
            self._ctx.services.log(
                f"Removed {removed} duplicate footprint{'' if removed == 1 else 's'}."
                if removed else "Dedup: nothing removed.")
            if self._on_changed:
                self._on_changed()
            self.accept()
        run_populate(self._ctx, job, done, busy="Removing duplicate footprints…")


class DuplicateManagerDialog(QDialog):
    """Resolve duplicate PARTS side by side (the Parts picker's Manage Duplicates action
    and a row's Duplicate-badge click both open this). One card per part — name, MPN,
    manufacturer, footprint, 3D model, completion — with the most-complete part pre-kept
    (unchecked) and the rest pre-marked delete. 'Delete Checked' removes each checked
    part through the proven remove_part backend (symbols, and optionally the footprint /
    3D-model files), undo-safe, and commits once. Backed by the SAME per-part delete the
    detail's Delete Whole Part uses, so bulk cleanup can never diverge from it."""

    def __init__(self, ctx, rows, on_changed=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._rows = list(rows or [])
        self._on_changed = on_changed
        self._checks: list = []          # (row, QCheckBox) — checked = delete
        self._busy = False
        self.setWindowTitle("Manage Duplicates")
        self.setModal(True); self.setMinimumWidth(560)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(12)
        lay.addWidget(W.eyebrow("Manage Duplicates"))
        sub = W.body("These parts duplicate one another (a shared part number, or a "
                     "byte-identical footprint). The most complete part is kept; check "
                     "the ones to delete. Deletes are undo-safe and commit once.", dim=True)
        sub.setWordWrap(True); lay.addWidget(sub)

        keeper = self._pick_keeper(self._rows)
        cards = QWidget(); flow = FlowLayout(cards, hspacing=10, vspacing=10)
        for row in self._rows:
            flow.addWidget(self._part_card(row, row is keeper))
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(cards)
        scroll.setFrameShape(QFrame.NoFrame)
        cards.setStyleSheet("background:transparent;")
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                             "QScrollArea>QWidget>QWidget{background:transparent;}")
        lay.addWidget(scroll, 1)

        # File-scope options: symbols always go; the footprint / model files are shared-
        # capable, so deleting them is opt-in (off by default, exactly like the detail).
        self._also_fp = QCheckBox("Also delete footprint files")
        self._also_md = QCheckBox("Also delete 3D model files")
        for cb in (self._also_fp, self._also_md):
            cb.setObjectName("finderOpt"); cb.setCursor(Qt.PointingHandCursor)
        opts = QHBoxLayout(); opts.setSpacing(16)
        opts.addWidget(self._also_fp); opts.addWidget(self._also_md); opts.addStretch(1)
        lay.addLayout(opts)

        self._counter = QLabel(""); self._counter.setFont(T.ui_font(10))
        W.register_restyle(lambda: self._counter.setStyleSheet(
            f"color:{T.t('txt2')};background:transparent;"), self._counter)
        row = QHBoxLayout(); row.addWidget(self._counter); row.addStretch(1)
        row.addWidget(W.btn("Cancel", "ghost", "Close without deleting anything", self.reject))
        self._delete_btn = W.btn("Delete Checked", "primary",
                                 "Delete the checked parts and commit", self._delete_checked)
        row.addWidget(self._delete_btn)
        lay.addLayout(row)

        self._update_counter()
        W.register_restyle(lambda: self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), self)
        self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")

    def _pick_keeper(self, rows):
        """Keep the most-complete part (highest passport score); ties break to the first
        row so the choice is deterministic and stable."""
        best, best_n = (rows[0] if rows else None), -1
        for r in rows:
            try:
                n = int(LM.part_completion(r).get("score", 0))
            except Exception:  # noqa: BLE001
                n = 0
            if n > best_n:
                best, best_n = r, n
        return best

    def _field(self, label, value):
        r = QHBoxLayout(); r.setContentsMargins(0, 0, 0, 0); r.setSpacing(6)
        r.addWidget(W.body(label, dim=True), 0)
        v = W.body(value or "—", mono=True); v.setWordWrap(True)
        r.addWidget(v, 1)
        return r

    def _part_card(self, row, is_keeper) -> QWidget:
        card = QFrame(); card.setObjectName("dedupcard")
        card.setMinimumWidth(240)
        W.register_restyle(lambda: card.setStyleSheet(
            f"QFrame#dedupcard{{background:{T.t('raised')};border:1px solid {T.t('hairline')};"
            f"border-radius:8px;}}"), card)
        cv = QVBoxLayout(card); cv.setContentsMargins(14, 12, 14, 12); cv.setSpacing(8)
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(10)
        cb = QCheckBox(); cb.setChecked(not is_keeper)
        cb.toggled.connect(lambda _c: self._update_counter())
        self._checks.append((row, cb))
        head.addWidget(cb, 0)
        nm = W.body(str(row.get("name") or "?")); nm.setFont(T.ui_font(10.5, semibold=True))
        head.addWidget(nm, 1)
        head.addWidget(W.tag("Keep", "ok") if is_keeper else W.body("Delete", dim=True), 0)
        cv.addLayout(head)
        cv.addLayout(self._field("Part Number", row.get("mpn") if row.get("has_real_mpn") else None))
        cv.addLayout(self._field("Manufacturer", row.get("manufacturer")))
        cv.addLayout(self._field("Footprint", row.get("footprint")))
        cv.addLayout(self._field("3D Model", row.get("model")))
        cv.addLayout(self._field("Completion", LM.completion_badge(row)))
        return card

    def _update_counter(self):
        n_del = sum(1 for _r, cb in self._checks if cb.isChecked())
        n_keep = len(self._checks) - n_del
        self._counter.setText(f"{n_del} to delete · {n_keep} kept")
        if getattr(self, "_delete_btn", None) is not None:
            self._delete_btn.setEnabled(n_del > 0 and not self._busy)

    def _delete_checked(self, confirm=None):
        """Delete the checked parts. `confirm` is a test/drive seam: a callable returning
        True to proceed (bypasses the modal QMessageBox); None uses the GUI confirm."""
        if self._busy:
            return
        targets = [r for r, cb in self._checks if cb.isChecked()]
        if not targets:
            return
        del_fp, del_md = self._also_fp.isChecked(), self._also_md.isChecked()
        if callable(confirm):
            if not confirm(targets):
                return
        elif not _headless():
            from PyQt5.QtWidgets import QMessageBox
            extra = ""
            if del_fp or del_md:
                bits = [b for b, on in (("footprint", del_fp), ("3D model", del_md)) if on]
                extra = f" Their {' and '.join(bits)} files are deleted too, when not shared."
            ans = QMessageBox.question(
                self, "Delete Parts",
                f"Delete {plural(len(targets), 'part')}? Their symbols are removed."
                f"{extra} This is undo-safe (snapshot to the library trash) and commits once.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
        else:
            return                       # headless with no explicit confirm never deletes
        self._busy = True
        self._update_counter()

        def job():
            log = LogSink(self._ctx.services)
            removed_parts, removed_syms = [], 0
            for r in targets:
                res = LM.remove_part(self._ctx.cfg, r, log,
                                     delete_footprint=del_fp, delete_model=del_md)
                if res.get("ok"):
                    removed_parts.append(r.get("name", "?"))
                    removed_syms += len(res.get("symbols_removed") or [])
            if removed_parts:
                LM.git_commit_push(self._ctx.cfg, log,
                                   f"chore(lib): remove {plural(len(removed_parts), 'duplicate part')}")
            return {"parts": removed_parts, "symbols": removed_syms}

        def done(res, ok):
            self._busy = False
            n = len((res or {}).get("parts") or [])
            self._ctx.services.log(
                f"Manage Duplicates: removed {plural(n, 'part')} "
                f"({plural((res or {}).get('symbols', 0), 'symbol')})."
                if n else "Manage Duplicates: nothing removed.")
            if self._on_changed:
                self._on_changed()
            self.accept()
        run_populate(self._ctx, job, done, busy="Removing duplicate parts…")


class LibraryToolsDialog(QDialog):
    """Curated maintenance front door (mockup §4.1 openMaint): five high-value ops, each
    wired to the REAL library backend and committing its result. It does not replace the
    Maintenance / Sourcing-Health subtabs — it is the fast path to the ops that matter
    most. Read-only ops (sourcing, integrity) run straight; mutating ops confirm first;
    Deduplicate opens the per-group review dialog."""

    def __init__(self, ctx, on_changed=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._on_changed = on_changed
        self._busy = False
        self.setWindowTitle("Library Tools")
        self.setModal(True); self.setMinimumWidth(560)

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(12)
        lay.addWidget(W.eyebrow("Library Tools"))
        sub = W.body("Curated maintenance for the whole library. Each runs on the real "
                     "library files and commits its result.", dim=True)
        sub.setWordWrap(True); lay.addWidget(sub)

        lookup_ok = LM.providers_from_config(ctx.cfg) is not None
        # (glyph, name, description, handler, enabled) — five real ops.
        self._ops = [
            ("search", "Refresh Sourcing",
             "Query live stock, pricing and lifecycle for every orderable part from Mouser.",
             self._refresh_sourcing, lookup_ok,
             None if lookup_ok else "Add a Mouser API key in Settings to enable sourcing"),
            ("footprint", "Deduplicate Footprints",
             "Find footprints with byte-identical geometry, then review which to delete.",
             self._open_dedup, True, None),
            ("symbol", "Auto-Assign Links",
             "Link unlinked symbols to matching footprints and 3D models by name.",
             self._auto_assign, True, None),
            ("update", "Fix Broken Links",
             "Reconnect symbols to their footprints and 3D models where a reference broke.",
             self._repair, True, None),
            ("check", "Integrity Scan",
             "Check every footprint and model reference is portable and resolvable.",
             self._integrity, True, None),
        ]
        self._status: dict = {}
        for glyph, name, desc, handler, enabled, dis_tip in self._ops:
            lay.addWidget(self._op_row(glyph, name, desc, handler, enabled, dis_tip))

        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(W.btn("Close", "ghost", "Close Library Tools", self.accept))
        lay.addLayout(row)
        W.register_restyle(lambda: self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), self)
        self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")

    def _op_row(self, glyph, name, desc, handler, enabled, dis_tip) -> QWidget:
        card = QFrame(); card.setObjectName("toolrow")
        W.register_restyle(lambda: card.setStyleSheet(
            f"QFrame#toolrow{{background:{T.t('raised')};border:1px solid {T.t('hairline')};"
            f"border-radius:8px;}}"), card)
        h = QHBoxLayout(card); h.setContentsMargins(14, 12, 14, 12); h.setSpacing(12)
        tile = QLabel(); tile.setFixedSize(30, 30); tile.setAlignment(Qt.AlignCenter)
        tile.setObjectName("tooltile")

        def _tint(t=tile, g=glyph):
            t.setPixmap(W.svg_icon(icons.GLYPHS.get(g, icons.GLYPHS["check"]), 16, T.t("txt2")).pixmap(16, 16))
        W.register_restyle(_tint, tile); _tint()
        W.register_restyle(lambda t=tile: t.setStyleSheet(
            f"QLabel#tooltile{{background:{T.t('inset')};border-radius:7px;}}"), tile)
        h.addWidget(tile, 0, Qt.AlignTop)

        col = QVBoxLayout(); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(2)
        nm = QLabel(name); nm.setFont(T.ui_font(10.5, semibold=True))
        W.register_restyle(lambda w=nm: w.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), nm)
        col.addWidget(nm)
        d = W.body(desc, dim=True); d.setWordWrap(True); col.addWidget(d)
        st = QLabel(""); st.setFont(T.ui_font(9)); st.setWordWrap(True); st.setVisible(False)
        W.register_restyle(lambda w=st: w.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"), st)
        self._status[name] = st
        col.addWidget(st)
        h.addLayout(col, 1)

        btn = W.btn("Run", "default", desc, handler)
        btn.setEnabled(enabled)
        if not enabled and dis_tip:
            btn.setToolTip(dis_tip)
        h.addWidget(btn, 0, Qt.AlignVCenter)
        return card

    def _set_status(self, name, text):
        st = self._status.get(name)
        if st is not None:
            st.setText(text); st.setVisible(bool(text))

    def _run(self, name, job, *, confirm=None, busy="Working…"):
        """Shared op runner: optional confirm → off-thread job → status line + refresh."""
        if self._busy:
            return
        if confirm:
            from PyQt5.QtWidgets import QMessageBox
            if QMessageBox.question(self, name, confirm,
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self._busy = True
        self._set_status(name, "Running…")

        def done(line, ok):
            self._busy = False
            self._set_status(name, line if ok else f"{name} failed, see status.")
            self._ctx.services.log(line if ok else f"{name} failed.")
            if self._on_changed:
                self._on_changed()
        run_populate(self._ctx, job, done, busy=busy)

    # ── the five ops (each real backend) ────────────────────────────────────────
    def _refresh_sourcing(self):
        lookup = LM.providers_from_config(self._ctx.cfg)
        if not lookup:
            self._set_status("Refresh Sourcing", "Add a Mouser API key in Settings first."); return

        def job():
            rep = LM.library_sourcing_report(self._ctx.cfg, lookup)
            c = (rep or {}).get("counts", {})
            bus = getattr(self._ctx, "bus", None)
            if bus is not None and rep:
                bus.emit("library.sourcing_report", rep)
            return (f"{c.get('on_mouser', 0)}/{c.get('parts', 0)} on Mouser · "
                    f"{c.get('obsolete_nrnd', 0)} NRND · {c.get('out_of_stock', 0)} out of stock.")
        self._run("Refresh Sourcing", job, busy="Checking sourcing on Mouser…")

    def _open_dedup(self):
        groups = LM.find_duplicate_footprints(self._ctx.cfg)
        if not groups:
            self._set_status("Deduplicate Footprints", "No duplicate footprints found."); return
        dlg = DedupReviewDialog(self._ctx, groups, on_changed=self._on_changed, parent=self)
        n = sum(len(g) for g in groups)
        self._set_status("Deduplicate Footprints",
                         f"{plural(len(groups), 'group')}, {n} footprints. Opening review…")
        if not _headless():
            dlg.exec_()

    def _auto_assign(self):
        def job():
            r = LM.auto_assign_library(self._ctx.cfg, dry_run=False, log=LogSink(self._ctx.services))
            LM.git_commit_push(self._ctx.cfg, LogSink(self._ctx.services),
                               "chore(lib): auto-assign footprints and models")
            fp = (r or {}).get("footprint_count", 0); md = (r or {}).get("model_count", 0)
            return f"Linked {plural(fp, 'footprint')} and {plural(md, 'model')} by name."
        self._run("Auto-Assign Links", job,
                  confirm="Link unlinked symbols to matching footprints and 3D models by "
                          "name, then commit?", busy="Auto-assigning links…")

    def _repair(self):
        def job():
            r = LM.repair_library(self._ctx.cfg, LogSink(self._ctx.services))
            LM.git_commit_push(self._ctx.cfg, LogSink(self._ctx.services),
                               "chore(lib): repair footprint and model links")
            sf = (r or {}).get("symbols_fixed", 0); ff = (r or {}).get("footprints_fixed", 0)
            return f"Repaired {plural(sf, 'symbol link')} and {plural(ff, 'footprint link')}."
        self._run("Fix Broken Links", job,
                  confirm="Rewrite library files to reconnect broken footprint and 3D-model "
                          "links, then commit?", busy="Repairing links…")

    def _integrity(self):
        def job():
            r = LM.verify_handoff_readiness(self._ctx.cfg)
            issues = (r or {}).get("issues") or []
            return ("Integrity OK. Every reference is portable and resolvable."
                    if not issues else f"{plural(len(issues), 'portability issue')} found. See Maintenance.")
        self._run("Integrity Scan", job, busy="Scanning integrity…")


class PartDetail(QWidget):
    """The canvas: it makes a part self-explanatory. An identity header (what the
    part IS), the Files section (interactive 3D + symbol + footprint previews with
    drop-in for any missing asset), an editable identity list, and a live Mouser
    sourcing block. show(row) swaps content; each preview renders off the GUI thread
    via run_populate. This is the whole right-hand column of the two-column Components
    view (library-v2 mockup) — the previews mount inline at the top."""

    def __init__(self, ctx, on_changed: Optional[Callable[[], None]] = None,
                 parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._on_changed = on_changed              # panel rebuild hook after a mutation
        self._lookup = LM.providers_from_config(ctx.cfg)  # distributor chain or None
        self._src_cache: dict = {}                 # mpn -> normalized sourcing dict
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)

        # ── Header (mockup .cvhead): a 22px humanized name with an inline warn glyph
        #    on any incomplete/dangling part, a "MPN · Manufacturer" subline, and a
        #    right-aligned actions row (Reuse/Rename + a ⋯ kebab). The title + subline
        #    are persistent (updated per show); the actions rebuild per show. ──
        head = QWidget()
        hrow = QHBoxLayout(head); hrow.setContentsMargins(0, 0, 0, 0); hrow.setSpacing(12)
        hgrow = QVBoxLayout(); hgrow.setContentsMargins(0, 0, 0, 0); hgrow.setSpacing(3)
        title_row = QHBoxLayout(); title_row.setContentsMargins(0, 0, 0, 0); title_row.setSpacing(8)
        self._title = QLabel(""); self._title.setFont(T.ui_font(16.5, semibold=True))  # ≈22px (mockup .pname)
        self._title.setWordWrap(True)
        W.register_restyle(lambda: self._title.setStyleSheet(
            f"color:{T.t('txt1')};background:transparent;"), self._title)
        self._title_warn = QLabel(); self._title_warn.setFixedSize(17, 17); self._title_warn.hide()
        title_row.addWidget(self._title, 0)
        title_row.addWidget(self._title_warn, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        hgrow.addLayout(title_row)
        # Subline: "MPN · Manufacturer" (mono for the part number), the honest identity.
        self._subline = QLabel(""); self._subline.setFont(T.mono_font(10))
        W.register_restyle(lambda: self._subline.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"), self._subline)
        hgrow.addWidget(self._subline)
        hrow.addLayout(hgrow, 1)
        self._head_actions = QHBoxLayout(); self._head_actions.setContentsMargins(0, 0, 0, 0)
        self._head_actions.setSpacing(7)
        hrow.addLayout(self._head_actions, 0)
        lay.addWidget(head)

        # Still-needs line (mockup .needs), rebuilt per show: a red broken-link notice,
        # a green Complete pill, or a "Missing" label + one amber pill per missing field.
        self._needs = QVBoxLayout(); self._needs.setContentsMargins(0, 0, 0, 0)
        self._needs.setSpacing(7)
        lay.addLayout(self._needs)

        # ── Files (mockup .files-row) — the TOP section of the canvas, ahead of the
        #    Component Fields + Sourcing that show() builds into `_fields`. The
        #    interactive 3D model dominates the left (flex 1.55); the Symbol +
        #    Footprint previews stack on the right (flex 1). A fixed ~344px height
        #    keeps the big model readable, capped at the mockup's 600px, left-aligned.
        self._sym = PreviewCard("Symbol", glyph=icons.GLYPHS["symbol"])
        self._fp = PreviewCard("Footprint", glyph=icons.GLYPHS["footprint"])
        self._mdl = PreviewCard("3D Model", glyph=icons.GLYPHS["cube"])
        self._fp.enable_dropin(self._dropin_footprint, (".kicad_mod",), "Add Footprint")
        self._mdl.enable_dropin(self._dropin_model, (".step", ".stp", ".wrl"), "Add 3D Model")
        self._sym.enable_dropin(self._dropin_symbol, (".kicad_sym",), "Add Symbol")
        lay.addWidget(_subhead("Files"))
        files_wrap = QWidget(); files_wrap.setMaximumWidth(600); files_wrap.setFixedHeight(344)
        frow = QHBoxLayout(files_wrap); frow.setContentsMargins(0, 0, 0, 0); frow.setSpacing(11)
        frow.addWidget(self._mdl, 155)                  # flex 1.55 — the big 3D card
        rcol = QWidget(); rcolv = QVBoxLayout(rcol)
        rcolv.setContentsMargins(0, 0, 0, 0); rcolv.setSpacing(11)
        rcolv.addWidget(self._sym, 1); rcolv.addWidget(self._fp, 1)
        frow.addWidget(rcol, 100)                        # flex 1 — Symbol over Footprint
        lay.addWidget(files_wrap, 0, Qt.AlignLeft)

        # ── Save bar (LIB-flash fix) ────────────────────────────────────────
        # Inline field edits write to disk immediately but are NOT committed +
        # pushed per keystroke — that per-field push storm is what flashed several
        # console/credential windows on Windows during a component update. Instead
        # edits accumulate as "unsaved" and a single explicit Save commits + pushes
        # them ONCE (like every other library mutation). This bar is the persistent
        # Save/Discard affordance; it lives OUTSIDE the rebuilt `_fields` layout so
        # clear_layout() on each show() never wipes it.
        self._unsaved = False
        self._unsaved_edits: list = []      # (label, ident) per pending field edit, for the message
        self._savebar = self._build_savebar()
        lay.addWidget(self._savebar)
        self._refresh_savebar()

        # Rebuilt each show(): the editable identity list + the sourcing block.
        self._fields = QVBoxLayout(); self._fields.setSpacing(14)
        lay.addLayout(self._fields)
        self._current = None
        self._kebab_menu = None             # header ⋯ menu, (re)built per show()
        # Component Fields view/edit toggle (mockup §3.4): read-only by default,
        # reset to view whenever the SELECTED part changes (a same-part re-show after
        # an edit keeps the user in edit mode). `_shown_key` tracks the shown part.
        self._edit_mode = False
        self._shown_key = None
        self._fp_summary = None             # last footprint/model dims, for unit re-caption
        self._mdl_summary = None
        self._preview_theme = T.is_dark()   # the theme the previews were last drawn under
        lay.addStretch(1)
        # Re-render previews only on a REAL theme flip so the baked-in background/ramp
        # tracks the new surface. Guarded against firing on every restyle_all() —
        # render_gate calls restyle_all() once per surface, which would otherwise spawn
        # an async-render thread storm (fd exhaustion on Windows CI).
        W.register_restyle(self._retheme_previews, self)
        # A unit flip only re-labels the cached dims — no async re-render (LIB-14).
        bus = getattr(ctx, "bus", None)
        if bus is not None:
            bus.on("units.changed", self._recaption_previews)

    # ── save bar: batch inline edits behind one explicit Save ───────────────────
    def _build_savebar(self) -> QWidget:
        """The persistent Unsaved-edits bar: a warn-accented strip with Save +
        Discard. Hidden until an inline edit marks the detail dirty."""
        bar = QFrame(); bar.setObjectName("savebar")
        row = QHBoxLayout(bar); row.setContentsMargins(12, 8, 12, 8); row.setSpacing(10)
        self._save_note = QLabel("Unsaved edits")
        self._save_note.setFont(T.ui_font(11, semibold=True))
        row.addWidget(self._save_note); row.addStretch(1)
        self._discard_btn = W.btn("Discard", "ghost",
                                  "Revert the edits you have not saved", self._discard_changes)
        self._save_btn = W.btn("Save Changes", "primary",
                               "Commit and push the edits you have made", self._save_changes)
        row.addWidget(self._discard_btn); row.addWidget(self._save_btn)
        W.register_restyle(lambda: bar.setStyleSheet(
            f"QFrame#savebar{{background:{T.t('inset')};border:1px solid {T.t('warn')};"
            f"border-radius:{T.RADIUS_CONTAINER}px;}}"), bar)
        W.register_restyle(lambda: self._save_note.setStyleSheet(
            f"color:{T.t('warn')};background:transparent;"), self._save_note)
        return bar

    def _refresh_savebar(self):
        """Show the Save bar only while edits are unsaved; keep the count truthful."""
        bar = getattr(self, "_savebar", None)
        if bar is None:
            return
        n = len(self._unsaved_edits)
        self._save_note.setText(
            f"{n} unsaved edit{'' if n == 1 else 's'}" if n else "Unsaved edits")
        bar.setVisible(bool(self._unsaved))

    def _mark_unsaved(self, label: str, ident: str):
        """Record a pending (written-but-uncommitted) field edit; reveal the bar."""
        self._unsaved = True
        self._unsaved_edits.append((label, ident))
        self._refresh_savebar()

    def _clear_unsaved(self):
        """A commit (Save, or any structural mutation that commits) left the work
        tree clean of pending edits — drop the dirty state and hide the bar."""
        self._unsaved = False
        self._unsaved_edits = []
        self._refresh_savebar()

    def _save_changes(self):
        """Commit + push the accumulated inline edits in ONE operation (this is the
        fix for the per-field push storm that flashed windows on Windows)."""
        if not self._unsaved:
            return
        edits = list(self._unsaved_edits)
        if len(edits) == 1:
            msg = CM.field_set(edits[0][0], edits[0][1])
        else:
            msg = f"chore(lib): update {len(edits)} library part fields"

        def job():
            return LM.git_commit_push(self._ctx.cfg, LogSink(self._ctx.services), msg)

        def done(ok, _thread_ok):
            # Clear regardless: a clean-tree "nothing to commit" also means nothing
            # is left unsaved, so a stale Save button self-heals on click.
            self._clear_unsaved()
            self._ctx.services.log("Saved." if ok else "Save: nothing to commit.")
            if self._on_changed:
                self._on_changed()
        run_populate(self._ctx, job, done, busy="Saving library edits…")

    def _discard_changes(self):
        """Revert the uncommitted inline edits, after a confirm."""
        if not self._unsaved:
            return
        from PyQt5.QtWidgets import QMessageBox
        n = len(self._unsaved_edits)
        ans = QMessageBox.question(
            self, "Discard Unsaved Edits",
            f"Discard {n} unsaved edit{'' if n == 1 else 's'}? "
            "The library files revert to the last saved version.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans == QMessageBox.Yes:
            self._apply_discard()

    def _apply_discard(self):
        """Discard apply seam (no modal, so tests/drive-audit can call it): restore
        the work tree, then refresh so the fields re-read the saved values."""
        def job():
            return LM.git_discard_uncommitted(self._ctx.cfg, LogSink(self._ctx.services))

        def done(ok, _thread_ok):
            self._clear_unsaved()
            self._ctx.services.log("Discarded unsaved edits." if ok
                                   else "Discard: could not revert the work tree.")
            if self._on_changed:
                self._on_changed()
            elif self._current:
                self.show(self._current)
        run_populate(self._ctx, job, done, busy="Discarding edits…")

    def _recaption_previews(self, _mode=None):
        """Re-label the footprint/3D-model dimension captions in the current unit,
        reusing the cached summaries (no re-render)."""
        if self._fp_summary:
            self._fp.set_caption(self._fp_caption(self._fp_summary))
        if self._mdl_summary:
            cap = self._mdl_caption(self._mdl_summary)
            if cap:
                self._mdl.set_caption(cap)

    # ── theme ────────────────────────────────────────────────────────────────
    def _retheme_previews(self):
        dark = T.is_dark()
        if dark == getattr(self, "_preview_theme", dark):
            return
        self._preview_theme = dark
        if getattr(self, "_current", None):
            self.show(self._current)

    # ── identity + sourcing ────────────────────────────────────────────────────
    def set_sourcing_report(self, report: Optional[dict]):
        """Seed the per-part sourcing cache from a bulk library_sourcing_report so
        the detail shows live data without re-querying. Refreshes the open part."""
        for r in (report or {}).get("rows", []):
            mpn = r.get("mpn")
            if mpn and r.get("found"):
                self._remember_sourcing(mpn, r)
        if self._current:
            self.show(self._current)

    def show(self, row: Optional[dict]):
        clear_layout(self._fields)
        clear_layout(self._head_actions)
        clear_layout(self._needs)
        if not row:
            self._current = None
            self._kebab_menu = None        # the kebab + its QMenu were just cleared — drop the ref
            self._title.setText(""); self._title_warn.hide(); self._subline.setText("")
            for c in (self._sym, self._fp, self._mdl):
                c.set_empty("Select A Part")
            return
        self._current = row
        # A genuinely NEW part opens in the read-only view; a same-part re-show (after
        # an inline edit / autofill) keeps the user in whichever mode they were in.
        key = row.get("name") or row.get("footprint")
        if key != self._shown_key:
            self._edit_mode = False
            self._shown_key = key
        names = LM.part_display_names(row)
        comp = LM.part_completion(row)
        self._title.setText(names["humanized"])
        # Inline warn glyph (mockup) on any incomplete/dangling part; silent complete.
        if not comp["is_complete"]:
            self._title_warn.setPixmap(
                W.svg_icon(icons.GLYPHS["alert"], 17, T.t("err")).pixmap(17, 17))
            self._title_warn.setToolTip("Has a broken link, needs a fix" if comp["dangling"]
                                        else f"Incomplete, {comp['score']} of {comp['total']}")
            self._title_warn.show()
        else:
            self._title_warn.hide()
        # Subline: "MPN · Manufacturer" — honest 'No Part Number' / 'Unknown Maker'
        # fallbacks (an orphan with no real MPN reads 'No Part Number', never a stub).
        mpn = (row.get("mpn") or "").strip() if names["orderable"] else ""
        mfr = (row.get("manufacturer") or "").strip()
        self._subline.setText(f"{mpn or 'No Part Number'} · {mfr or 'Unknown Maker'}")
        src = self._src_cache.get(row.get("mpn"))

        self._build_needs(row, comp)
        self._build_identity(row)
        self._build_sourcing(row, src)
        self._build_head_actions(row)

        # LIB gating: a drop-in must have somewhere to attach. A footprint links to a
        # symbol; a 3D model attaches to a footprint. Where the prerequisite is
        # absent, block the Add affordance and name the prerequisite in the empty
        # state (rather than offering a file picker that would silently no-op).
        has_symbol = bool(row.get("symbols"))
        has_footprint = bool(row.get("footprint"))
        self._fp.set_droppable(has_symbol, "Add A Symbol First")
        self._mdl.set_droppable(has_footprint, "Add A Footprint First")

        self._render_symbol(row)
        self._render_footprint(row)
        self._render_model(row)

    def _build_identity(self, row):
        """Component Fields (mockup §3.4): a read-only #idview by default, flipped to
        an editable form by the 'Edit Part Values'/'Done' toggle. A footprint-only
        orphan (no symbol) gets the Create-Symbol CTA instead (LM:2117). Package is
        derived from the footprint (read-only). Edits batch behind the Save bar."""
        editable = bool(row.get("symbols"))

        # LM:2117: a footprint-only orphan has no identity to edit — the honest,
        # actionable state is "this footprint has no symbol; make one". Offer a
        # single Create Symbol CTA that builds a stub symbol linked to this
        # footprint, turning the orphan into a placeable part in one click.
        if not editable and row.get("footprint"):
            self._build_orphan_identity(row)
            return

        # Section header (mockup .sec-row): the label + the view<->edit toggle. A part
        # with no symbol has nowhere to write, so it shows the read-only view only.
        hdr = QWidget(); hb = QHBoxLayout(hdr)
        hb.setContentsMargins(0, 0, 0, 0); hb.setSpacing(8)
        hb.addWidget(_subhead("Component Fields")); hb.addStretch(1)
        if editable:
            hb.addWidget(W.btn("Done" if self._edit_mode else "Edit Part Values", "ghost",
                               "Switch between the read-only fields and the edit form",
                               self._toggle_edit))
        self._fields.addWidget(hdr)

        if editable and self._edit_mode:
            self._build_idedit(row, LM.part_completion(row))
        else:
            self._build_idview(row)

    def _build_idview(self, row):
        """The read-only #idview: Part Number, Manufacturer, Category, (Mouser P/N if
        set), the footprint-derived Package, Description, Datasheet. An empty field
        reads 'Missing' in the error tone (mockup .iv.ph); Package reads 'None'."""
        def val(v, mono=False):
            if v:
                return W.body(v, mono=mono)
            lab = QLabel("Missing"); lab.setFont(T.ui_font(10))
            W.register_restyle(lambda: lab.setStyleSheet(
                f"color:{T.t('err')};background:transparent;"), lab)
            return lab
        pkg = W.body((row.get("footprint") or "").strip() or "None")
        pkg.setToolTip("Derived from the footprint, not editable")
        pairs = [
            ("Part Number", val(row.get("mpn"), mono=True)),
            ("Manufacturer", val(row.get("manufacturer"))),
            ("Category", val(row.get("category"))),
        ]
        if (row.get("mouser_pn") or "").strip():
            pairs.append(("Mouser Part Number", val(row.get("mouser_pn"), mono=True)))
        pairs += [
            ("Package", pkg),
            ("Description", val(row.get("description"))),
            ("Datasheet", self._datasheet_view_value(row.get("datasheet"))),
        ]
        self._fields.addWidget(W.dl(pairs, key_width=128))

    def _datasheet_view_value(self, url) -> QWidget:
        """The read-only Datasheet value: the URL (mono, elided) with an Open button
        right beside it so the link is never detached from its field. Empty → Missing."""
        url = (url or "").strip()
        if not url:
            lab = QLabel("Missing"); lab.setFont(T.ui_font(10))
            W.register_restyle(lambda: lab.setStyleSheet(
                f"color:{T.t('err')};background:transparent;"), lab)
            return lab
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        link = W.body(url, mono=True); link.setWordWrap(True); link.setToolTip(url)
        h.addWidget(link, 1)
        h.addWidget(W.btn("Open", "ghost", "Open the datasheet in your browser",
                          lambda u=url: self._open_datasheet(u)), 0)
        return w

    def _open_datasheet(self, url: str):
        if _headless() or not url:
            return
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl
        QDesktopServices.openUrl(QUrl(url))

    def _build_idedit(self, row, comp):
        """The editable #idedit form: an Autofill-From-Mouser button while incomplete,
        then per-field click-to-edit values (Description multiline; Part Number /
        Mouser P/N mono) plus an editable Category and a Datasheet Find▾ when empty.
        Every field writes to disk immediately and batches behind the Save bar."""
        box = QWidget(); v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
        if not comp["is_complete"] and self._lookup:
            af = QHBoxLayout(); af.setContentsMargins(0, 0, 0, 0)
            af.addWidget(W.btn("Autofill From Mouser", "ghost",
                               "Look this part up on Mouser and fill the blank fields",
                               lambda: self._autofill_from_mouser(row)))
            af.addStretch(1)
            v.addLayout(af)

        def ed(label, prop, row_key, value, mono=False, placeholder="Add value"):
            return _editable_value(value or "",
                                   lambda x: self._commit_field(label, prop, row_key, x),
                                   mono=mono, placeholder=placeholder)

        pairs = [
            ("Description", _editable_multiline(
                row.get("description") or "",
                lambda x: self._commit_field("Description", "Description", "description", x),
                placeholder="Add a description")),
            ("Part Number", ed("Part Number", "Value", "mpn", row.get("mpn"),
                               mono=True, placeholder="Add part number")),
            ("Manufacturer", ed("Manufacturer", "MANUFACTURER", "manufacturer",
                                row.get("manufacturer"), placeholder="Add manufacturer")),
            ("Category", ed("Category", "Category", "category", row.get("category"),
                            placeholder="Add category")),
            ("Mouser Part Number", ed("Mouser Part Number", "Mouser Part Number",
                                      "mouser_pn", row.get("mouser_pn"), mono=True,
                                      placeholder="Add Mouser part number")),
            ("Datasheet", self._datasheet_field(row)),
        ]
        v.addWidget(W.dl(pairs, key_width=128))
        self._fields.addWidget(box)

    def _datasheet_field(self, row) -> QWidget:
        """The Datasheet edit row: a click-to-edit URL value, plus — while empty — a
        Find▾ menu (Search Mouser / Paste A URL) mirroring the mockup's Datasheet Find."""
        editor = _editable_value(
            row.get("datasheet") or "",
            lambda x: self._commit_field("Datasheet", "Datasheet", "datasheet", x),
            placeholder="Paste a URL, or use Find")
        ds = (row.get("datasheet") or "").strip()
        if ds:
            # A set datasheet: the editor + an Open button right beside it (not detached).
            w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
            h.addWidget(editor, 1)
            h.addWidget(W.btn("Open", "ghost", "Open the datasheet in your browser",
                              lambda u=ds: self._open_datasheet(u)), 0)
            return w
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        h.addWidget(editor, 1)
        h.addWidget(W.menu_button("Find", [
            ("Search Mouser", lambda: self._find_on_mouser(row),
             "Search Mouser's catalog and enrich this part, datasheet included"),
            ("Paste A URL", lambda: editor.setFocus(),
             "Type or paste the datasheet URL into the field"),
        ], tip="Find a datasheet for this part", kind="ghost"), 0)
        return w

    def _toggle_edit(self):
        """Flip view<->edit. The teardown/rebuild is DEFERRED out of the toggle's own
        click handler (the widget that fired it is inside `_fields`, which the rebuild
        clears) — a synchronous rebuild here would be a use-after-free (CLAUDE.md §4)."""
        self._edit_mode = not self._edit_mode
        QTimer.singleShot(0, self._rebuild_fields)

    def _rebuild_fields(self):
        """Rebuild only the identity + sourcing block (not the previews) — the cheap,
        preview-preserving refresh the edit toggle needs."""
        if not self._current:
            return
        clear_layout(self._fields)
        self._build_identity(self._current)
        self._build_sourcing(self._current, self._src_cache.get(self._current.get("mpn")))

    def _autofill_from_mouser(self, row):
        """Autofill: an exact-MPN Mouser lookup when the part has one, else the live
        catalog search — both funnel through the same preview+apply (_apply_fetched)."""
        mpn = (row.get("mpn") or "").strip()
        if mpn and self._lookup:
            self._offer_autofill(mpn)
        else:
            self._find_on_mouser(row)

    def _build_orphan_identity(self, row):
        """LM:2117: the actionable identity for a footprint-only orphan. A footprint
        with no symbol can't be placed on a schematic (schematics drop symbols); the
        honest state is 'no symbol yet', and the one useful action is to create one
        linked to this footprint. A quiet explanation + a single primary CTA replace
        the old dead read-only form."""
        stem = row.get("footprint") or "this footprint"
        box = QWidget()
        v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        line = W.body(
            f"{stem} is an unlinked footprint. It has no symbol, so it can't be "
            "placed on a schematic or ordered. Create a symbol linked to it to make "
            "it a real, placeable part.", dim=True)
        line.setWordWrap(True)
        v.addWidget(line)
        act = QHBoxLayout(); act.setContentsMargins(0, 0, 0, 0); act.setSpacing(8)
        act.addWidget(W.btn("Create Symbol", "primary",
                            "Create a new symbol linked to this footprint so it can be "
                            "placed and sourced",
                            lambda: self._create_symbol(row)))
        act.addStretch(1)
        v.addLayout(act)
        self._fields.addWidget(box)

    def _create_symbol(self, row):
        """LM:2117: build a stub symbol linked to the orphan's footprint, commit it,
        then refresh so the (now placeable) part re-renders with its editable
        identity + symbol preview."""
        stem = row.get("footprint")
        if not stem:
            self._ctx.services.log("Create Symbol needs a footprint to link to.")
            return

        def job():
            log = LogSink(self._ctx.services)
            name = LM.create_symbol_for_footprint(self._ctx.cfg, stem, log)
            if name:
                LM.git_commit_push(self._ctx.cfg, log, CM.add_symbol(f"{name} (for {stem})"))
            return name

        def done(name, ok):
            if not name:
                self._ctx.services.log("Create Symbol did not complete, see status.")
                return
            self._clear_unsaved()               # the create-symbol commit swept up any pending edits
            self._ctx.services.log(f"Created symbol {name} linked to {stem}.")
            # The part now HAS a symbol — merge it into the row so the detail
            # re-renders as an editable, placeable part. A wired list rescans (and
            # re-shows off the fresh rows); standalone, we show() ourselves.
            new = dict(row)
            new["symbols"] = [name]
            new["has_symbol"] = True
            new["name"] = name
            self._current = new
            if self._on_changed:
                self._on_changed()
            else:
                self.show(new)
        run_populate(self._ctx, job, done, busy=f"Creating symbol for {stem}...")

    # ── manage part: rename / reuse / delete (the parity-closing per-part actions) ─────
    def _build_needs(self, row, comp):
        """The still-needs line (mockup .needs): a red broken-link notice, a green
        Complete pill, or a 'Missing' label + one amber pill per missing field. The
        missing labels come straight from part_completion['missing'] (LM)."""
        if comp["dangling"]:
            lab = QLabel("Has a broken link, needs a fix"); lab.setObjectName("needsBroken")
            lab.setFont(T.ui_font(9)); self._needs.addWidget(lab); return
        if comp["is_complete"]:
            pill = QLabel("Complete"); pill.setObjectName("needsComplete")
            pill.setFont(T.ui_font(9, semibold=True))
            pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
            holder = QWidget(); hl = QHBoxLayout(holder); hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(pill); hl.addStretch(1); self._needs.addWidget(holder); return
        lab = QLabel("Missing"); lab.setObjectName("needsLabel"); lab.setFont(T.ui_font(9))
        self._needs.addWidget(lab)
        pills = QWidget(); flow = FlowLayout(pills)
        for m in comp["missing"]:
            pill = QLabel(m); pill.setObjectName("needsPill"); pill.setFont(T.ui_font(9))
            flow.addWidget(pill)
        self._needs.addWidget(pills)

    def _build_head_actions(self, row):
        """The header actions (mockup .hactions), gated by what the row has: Reuse Symbol
        for a footprint-only orphan, Rename for a part with a symbol, and a ⋯ kebab with
        Reveal Files + the delete family (per-file + whole part, each confirming first)."""
        has_symbol = bool(row.get("symbols"))
        has_footprint = bool(row.get("footprint"))
        has_model = bool(row.get("model"))
        is_orphan = has_footprint and not has_symbol
        if is_orphan:
            self._head_actions.addWidget(W.btn(
                "Reuse Symbol", "ghost",
                "Duplicate an existing symbol and link it to this orphan footprint",
                lambda: self._reuse_symbol_for_orphan()))
        if has_symbol:
            self._head_actions.addWidget(W.btn(
                "Rename", "ghost", "Rename this part's symbol across the library",
                lambda: self._rename_symbol()))
        kebab = QPushButton("⋯"); kebab.setObjectName("kebab"); kebab.setFixedSize(32, 32)
        kebab.setCursor(Qt.PointingHandCursor); kebab.setToolTip("More actions")
        menu = QMenu(kebab)
        if has_symbol:
            menu.addAction("Duplicate…", lambda: self._duplicate_part(row))
        menu.addAction("Reveal Files", lambda: self._reveal_files(row))
        dels = []
        if has_footprint:
            dels.append(("Delete Footprint File", self._delete_footprint))
        if has_model:
            dels.append(("Delete 3D Model File", self._delete_model))
        if has_symbol or has_footprint or has_model:
            dels.append(("Delete Whole Part…", self._delete_part))
        if dels:
            menu.addSeparator()
            for label, fn in dels:
                menu.addAction(label, (lambda f=fn: f()))
        kebab.setMenu(menu)
        self._head_actions.addWidget(kebab)
        self._kebab_menu = menu             # test/inspection handle

    def _reveal_target(self, row) -> Path:
        """The folder to reveal for this part: the footprint library dir if the part has
        a footprint, else the symbol library's dir, else the library root."""
        cfg = self._ctx.cfg
        if row.get("footprint"):
            fp = Path(cfg.get("FootprintLib", ""))
            if str(fp):
                return fp if fp.is_dir() else fp.parent
        sym = Path(cfg.get("SymbolLib", ""))
        if str(sym):
            return sym.parent
        return Path(cfg.get("Libs") or cfg.get("RepoRoot") or ".")

    def _reveal_files(self, row):
        """Open the part's library folder in the OS file manager."""
        target = self._reveal_target(row)
        if _headless():
            return                          # no file manager under offscreen Qt / tests
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _all_symbol_names(self):
        try:
            text = LM.read_text(Path(self._ctx.cfg["SymbolLib"]))
            return sorted(LM.extract_symbol_name(b) for b in LM.extract_symbol_blocks(text))
        except Exception:  # noqa: BLE001
            return []

    def _mutation_refresh(self, patch=None):
        """After a successful per-part mutation: merge `patch` into the current row and
        rescan the list (wired) or re-show ourselves (standalone), so the detail reflects
        the new state exactly once — the same one-render discipline as the inline edits."""
        # A structural mutation commits the whole work tree (stage_all), so any
        # pending inline edits were committed too — the detail is no longer dirty.
        self._clear_unsaved()
        row = self._current
        if patch and row is not None:
            new = dict(row); new.update(patch); self._current = new
        if self._on_changed:
            self._on_changed()
        elif self._current is not None:
            self.show(self._current)

    def _confirm_delete(self, confirm, refs, *, title, what, dangles) -> bool:
        """Resolve a destructive confirmation. `confirm` is either a callable(refs)->bool
        (the explicit test/headless seam) or None (ask via a GUI dialog that NAMES what
        would dangle). A headless run with no explicit seam never deletes (safe default)."""
        if callable(confirm):
            return bool(confirm(refs))
        if _headless():
            return False
        from PyQt5.QtWidgets import QMessageBox
        msg = what
        if refs:
            shown = ", ".join(str(x) for x in list(refs)[:8])
            more = f" (+{len(refs) - 8} more)" if len(refs) > 8 else ""
            msg += (f"\n\n{plural(len(refs), dangles)} reference it and will be left "
                    f"dangling: {shown}{more}")
        msg += "\n\nThis is undo-safe. A snapshot is kept (Maintenance › Undo Last Change)."
        ans = QMessageBox.question(self, title, msg,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ans == QMessageBox.Yes

    def _rename_symbol(self, new_name=None):
        row = self._current
        names = (row or {}).get("symbols") or []
        if not names:
            self._ctx.services.log("This part has no symbol to rename.")
            return
        old = names[0]
        new = new_name
        if new is None:
            if _headless():
                self._ctx.services.log("Rename Symbol needs a name (unavailable headless).")
                return
            text, ok = QInputDialog.getText(self, "Rename Symbol",
                                            f"New name for '{old}':", text=old)
            if not ok or not text.strip():
                return
            new = text.strip()
        if new == old:
            return

        def job():
            log = LogSink(self._ctx.services)
            ok = LM.rename_symbol_in_library(self._ctx.cfg, old, new, log)
            if ok:
                LM.git_commit_push(self._ctx.cfg, log,
                                   f"chore(lib): rename symbol {old} -> {new}")
            return ok

        def done(ok, _ran):
            if not ok:
                self._ctx.services.log(
                    f"Could not rename '{old}' to '{new}' (the name may already be taken).")
                return
            self._ctx.services.log(f"Renamed symbol {old} to {new}.")
            self._mutation_refresh({"symbols": [new if n == old else n for n in names],
                                    "name": new})
        run_populate(self._ctx, job, done, busy=f"Renaming {old}...")

    def _duplicate_part(self, row=None, new_name=None):
        """Duplicate this part's symbol under a new name (kebab ▸ Duplicate…) to make a
        variant — a full copy (footprint / 3D / fields intact) with its MPN reset so it
        needs its own part number. `new_name` skips the prompt for tests/drive."""
        row = row or self._current
        names = (row or {}).get("symbols") or []
        if not names:
            self._ctx.services.log("This part has no symbol to duplicate.")
            return
        src = names[0]
        new = new_name
        if new is None:
            if _headless():
                self._ctx.services.log("Duplicate needs a name (unavailable headless).")
                return
            text, ok = QInputDialog.getText(self, "Duplicate Part",
                                            f"New name for the copy of '{src}':",
                                            text=f"{src}_copy")
            if not ok or not text.strip():
                return
            new = text.strip()

        def job():
            log = LogSink(self._ctx.services)
            final = LM.duplicate_part(self._ctx.cfg, row, new, log)
            if final:
                LM.git_commit_push(self._ctx.cfg, log,
                                   f"feat(lib): duplicate {src} as {final}")
            return final

        def done(final, _ran):
            if not final:
                self._ctx.services.log(f"Could not duplicate '{src}'.")
                return
            self._clear_unsaved()
            self._ctx.services.log(f"Duplicated {src} as {final}. Give it its own part number.")
            # Show the duplicate immediately (a copy of the source with the MPN reset to
            # the new name → reads 'not orderable' until its real MPN is set); the rescan
            # brings it into the list.
            dup = dict(row); dup["name"] = final; dup["symbols"] = [final]
            dup["mpn"] = final; dup["has_real_mpn"] = False
            self._current = dup
            if self._on_changed:
                self._on_changed()
            self.show(dup)
        run_populate(self._ctx, job, done, busy=f"Duplicating {src}...")

    def _reuse_symbol_for_orphan(self, source=None):
        row = self._current
        stem = (row or {}).get("footprint")
        if not stem or (row or {}).get("symbols"):
            self._ctx.services.log("Reuse Symbol applies to a footprint-only orphan.")
            return
        src = source
        if src is None:
            if _headless():
                self._ctx.services.log("Reuse Symbol needs a source symbol (unavailable headless).")
                return
            options = self._all_symbol_names()
            if not options:
                self._ctx.services.log("No existing symbols to reuse.")
                return
            name, ok = QInputDialog.getItem(self, "Reuse Existing Symbol",
                                            f"Duplicate which symbol for '{stem}'?",
                                            options, 0, False)
            if not ok or not name:
                return
            src = name

        def job():
            log = LogSink(self._ctx.services)
            new = LM.duplicate_symbol_for_footprint(self._ctx.cfg, src, stem, log)
            if new:
                LM.git_commit_push(self._ctx.cfg, log, CM.add_symbol(f"{new} (for {stem})"))
            return new

        def done(new, _ran):
            if not new:
                self._ctx.services.log(f"Could not reuse '{src}' for {stem}.")
                return
            self._ctx.services.log(f"Created symbol {new} from {src}, linked to {stem}.")
            self._mutation_refresh({"symbols": [new], "has_symbol": True, "name": new})
        run_populate(self._ctx, job, done, busy=f"Reusing {src} for {stem}...")

    def _delete_footprint(self, confirm=None):
        row = self._current
        stem = (row or {}).get("footprint")
        if not stem:
            self._ctx.services.log("This part has no footprint file to delete.")
            return
        try:
            refs = LM.symbols_referencing_footprint(self._ctx.cfg, stem)
        except Exception:  # noqa: BLE001
            refs = []
        if not self._confirm_delete(confirm, refs, title="Delete Footprint File",
                                    what=f"Delete the footprint '{stem}.kicad_mod'?",
                                    dangles="symbol"):
            return

        def job():
            log = LogSink(self._ctx.services)
            r = LM.remove_footprint(self._ctx.cfg, stem, log)
            if r.get("removed"):
                LM.git_commit_push(self._ctx.cfg, log, f"chore(lib): delete footprint {stem}")
            return r

        def done(r, _ran):
            if not r or not r.get("removed"):
                self._ctx.services.log(f"Footprint {stem} was not deleted.")
                return
            n = len(r.get("referenced_by") or [])
            self._ctx.services.log(f"Deleted footprint {stem}."
                                   + (f" {plural(n, 'symbol')} now dangle." if n else ""))
            self._mutation_refresh({"footprint": None, "has_footprint": False,
                                    "dangling": bool(n)})
        run_populate(self._ctx, job, done, busy=f"Deleting footprint {stem}...")

    def _delete_model(self, confirm=None):
        row = self._current
        name = (row or {}).get("model")
        if not name:
            self._ctx.services.log("This part has no 3D model file to delete.")
            return
        try:
            refs = LM.footprints_referencing_model(self._ctx.cfg, name)
        except Exception:  # noqa: BLE001
            refs = []
        if not self._confirm_delete(confirm, refs, title="Delete 3D Model File",
                                    what=f"Delete the 3D model '{name}'?",
                                    dangles="footprint"):
            return

        def job():
            log = LogSink(self._ctx.services)
            r = LM.remove_model(self._ctx.cfg, name, log)
            if r.get("removed"):
                LM.git_commit_push(self._ctx.cfg, log, f"chore(lib): delete 3D model {name}")
            return r

        def done(r, _ran):
            if not r or not r.get("removed"):
                self._ctx.services.log(f"3D model {name} was not deleted.")
                return
            n = len(r.get("referenced_by") or [])
            self._ctx.services.log(f"Deleted 3D model {name}."
                                   + (f" {plural(n, 'footprint')} now dangle." if n else ""))
            self._mutation_refresh({"model": None, "has_model": False, "dangling": bool(n)})
        run_populate(self._ctx, job, done, busy=f"Deleting 3D model {name}...")

    def _ask_delete_part(self, row):
        """GUI-only: which files to also delete. Headless drives the explicit callable seam."""
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox
        dlg = QDialog(self); dlg.setWindowTitle("Delete Part")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Delete part '{row.get('name', '?')}'? Its symbols are removed."))
        fp_cb = QCheckBox("Also delete the footprint file")
        md_cb = QCheckBox("Also delete the 3D model file")
        fp_cb.setEnabled(bool(row.get("footprint")))
        md_cb.setEnabled(bool(row.get("model")))
        v.addWidget(fp_cb); v.addWidget(md_cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Delete")
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec_() != QDialog.Accepted:
            return None
        return (True, fp_cb.isChecked(), md_cb.isChecked())

    def _delete_part(self, confirm=None):
        row = self._current
        if not row:
            return
        if callable(confirm):
            decision = confirm(row)
        elif _headless():
            decision = None                          # never delete unprompted headless
        else:
            decision = self._ask_delete_part(row)
        if not decision:
            return
        go, del_fp, del_model = decision
        if not go:
            return

        def job():
            log = LogSink(self._ctx.services)
            r = LM.remove_part(self._ctx.cfg, row, log,
                               delete_footprint=del_fp, delete_model=del_model)
            if r.get("ok"):
                LM.git_commit_push(self._ctx.cfg, log,
                                   f"chore(lib): delete part {row.get('name', '?')}")
            return r

        def done(r, _ran):
            if not r or not r.get("ok"):
                self._ctx.services.log(
                    f"Delete Part: {(r or {}).get('reason') or 'nothing removed'}.")
                return
            bits = [f"{plural(len(r.get('symbols_removed') or []), 'symbol')}"]
            if r.get("footprint_removed"):
                bits.append("footprint")
            if r.get("model_removed"):
                bits.append("3D model")
            self._ctx.services.log(
                f"Deleted part {row.get('name', '?')}: removed {', '.join(bits)}.")
            if r.get("still_referenced"):
                self._ctx.services.log(
                    "Some files are still referenced by other parts; those links were kept.")
            self._clear_unsaved()                    # the delete commit swept up any pending edits
            self.show(None)                          # the part is gone — clear the detail
            if self._on_changed:
                self._on_changed()
        run_populate(self._ctx, job, done, busy=f"Deleting {row.get('name', 'part')}...")

    def _build_sourcing(self, row, src):
        """Live Mouser sourcing: lifecycle / stock / price / lead time / suggested
        replacement. Shows cached data if present, else a one-click lookup (when a
        Mouser key is configured), else a quiet hint."""
        mpn = row.get("mpn")
        # Resolve sourcing BEFORE the header so the header can decide whether a
        # "Look Up On Mouser" belongs there at all: fall back to a persisted
        # snapshot so pricing survives relaunch (in-session live data always wins;
        # the snapshot carries an 'as of' age).
        as_of = ""
        if not src and mpn:
            try:
                snap = LM.sourcing_snapshot_for(self._ctx.cfg, mpn)
            except Exception:  # noqa: BLE001
                snap = None
            if snap:
                src = snap
                as_of = LM.snapshot_age_label(snap.get("as_of", ""))

        # One "Mouser ▾" entry point (owner decision) instead of the confusing
        # "Find on Mouser" / "Look Up On Mouser" pair: catalog search (find a part you
        # don't have the exact number for) + exact-MPN refresh (re-price this part),
        # each self-describing. It appears EXACTLY once — in the header when sourcing is
        # already shown, or as the empty-state CTA when nothing is cached — never both.
        def _mouser_menu():
            items = [("Search Catalog…", lambda: self._find_on_mouser(row),
                      "Search the Mouser catalog by keyword or part number, then apply a "
                      "match to this part")]
            if mpn:
                items.append(("Refresh This Part's Data", lambda: self._lookup_one(row),
                              "Re-fetch live stock, pricing and lifecycle for this part's number"))
            return W.menu_button("Mouser", items, kind="ghost",
                                 tip="Search Mouser or refresh this part's sourcing data")

        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(_subhead("Sourcing"))
        head.addStretch(1)
        if self._lookup and src:
            head.addWidget(_mouser_menu())
        self._fields.addLayout(head)

        if src:
            self._fields.addWidget(self._sourcing_body(src, as_of))
        elif not self._lookup:
            self._fields.addWidget(W.body(
                "Add a Mouser API key in Settings to see live stock, pricing and lifecycle.",
                dim=True))
        else:
            # Nothing cached yet: the Mouser menu IS the empty-state CTA (the single
            # entry point when uncached), beside the "Not Looked Up Yet" glyph.
            self._fields.addWidget(W.empty_state(
                "Not Looked Up Yet", glyph=icons.GLYPHS["search"],
                action=_mouser_menu() if self._lookup else None))

    @staticmethod
    def _fmt_price(p) -> str:
        """Money with honest precision: 2 decimals for normal prices, 4 for sub-dime
        parts (a $0.0048 passive would read as $0.00 at 2 places)."""
        if p is None:
            return "—"
        return f"${p:,.4f}" if 0 < p < 0.1 else f"${p:,.2f}"

    def _sourcing_body(self, src: dict, as_of: str = "") -> QWidget:
        """The per-part sourcing block (mockup §3.5): a 4-col stat-card grid (In Stock /
        Unit at 100 / Lifecycle / Lead Time), a horizontal-bar Price Breaks graph
        (bars scale price against the qty-1 max), then the remaining distributor fields
        (Category / RoHS / suggested replacement / product-page link) as a quiet list."""
        box = QWidget(); v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(14)

        if as_of:                                   # freshness note (mockup .srcnote)
            v.addWidget(W.body(f"Last priced {as_of}. Look up again to refresh.", dim=True))

        life = (src.get("lifecycle") or "").strip()
        obsolete = bool(src.get("obsolete")) or any(
            w in life.lower() for w in
            ("obsolete", "eol", "end of life", "nrnd", "not recommended", "discontinued"))
        life_kind = "err" if obsolete else ("warn" if life and life.lower() != "active" else "ok")

        stock = src.get("stock") or 0
        breaks = [b for b in (src.get("price_breaks") or []) if isinstance(b, dict)]
        # Unit at 100 — the applicable ladder price at qty 100 (the mockup's headline
        # volume price), falling back to the qty-1 unit price when there is no ladder.
        unit100 = LM._coerce_price(LM.price_at_qty(breaks, 100)) if breaks \
            else LM._coerce_price(src.get("unit_price"))
        lead = src.get("lead_time")
        v.addWidget(self._stat_cards([
            ("In Stock", f"{stock:,}" if stock else "0", None if stock else "warn"),
            ("Unit at 100", self._fmt_price(unit100), None),
            ("Lifecycle", life or "Active", life_kind),
            ("Lead Time", str(lead) if lead else "—", None),
        ]))

        # Price-break bar graph — only with more than one rung (a single rung is just
        # the unit price already shown in the stat card).
        if len(breaks) > 1:
            v.addWidget(_subhead("Price Breaks"))
            v.addWidget(self._price_break_graph(breaks))

        # LIB-04: the remaining distributor fields, each only when present so the block
        # stays quiet. MPN/datasheet ride the identity list; Category (the distributor's
        # product family), RoHS, a suggested replacement, and the live product page live here.
        pairs = []
        if src.get("category"):
            pairs.append(("Category", W.body(str(src["category"]))))
        if src.get("rohs"):
            pairs.append(("RoHS", W.body(str(src["rohs"]))))
        rep = src.get("suggested_replacement")
        if rep:
            pairs.append(("Suggested Replacement", W.body(str(rep), mono=True)))
        url = src.get("url")
        if url:
            # Provider-aware: the link + key name follow whichever distributor sourced it.
            prov = (src.get("source") or "").strip()
            prov = prov if prov in ("Mouser", "LCSC") else "Mouser"
            safe = escape(str(url), quote=True)   # distributor URLs carry query params (&, =)
            link = QLabel(f'<a href="{safe}" style="color:{T.t("txt2")};'
                          f'text-decoration:none;">View On {prov} →</a>')
            link.setOpenExternalLinks(True)
            link.setTextInteractionFlags(Qt.TextBrowserInteraction)
            pairs.append((f"{prov} Page", link))
        if pairs:
            v.addWidget(W.dl(pairs, key_width=128))
        return box

    def _stat_cards(self, items) -> QWidget:
        """The 4-col sourcing stat grid (mockup .srcstats), capped at the mockup's
        600px. Each item is (label, value, kind) where kind in {ok, warn, err} tints
        the value (used for Lifecycle / an out-of-stock In Stock)."""
        wrap = QWidget(); wrap.setMaximumWidth(600)
        g = QGridLayout(wrap); g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10); g.setVerticalSpacing(10)
        for i, (label, value, kind) in enumerate(items):
            g.addWidget(self._stat_card(label, value, kind), 0, i)
            g.setColumnStretch(i, 1)
        return wrap

    def _stat_card(self, label: str, value: str, kind: Optional[str]) -> QWidget:
        c = QFrame(); c.setObjectName("statcard")
        W.register_restyle(lambda: c.setStyleSheet(
            f"QFrame#statcard{{background:{T.t('raised')};border:1px solid {T.t('hairline')};"
            f"border-radius:8px;}}"), c)
        cv = QVBoxLayout(c); cv.setContentsMargins(13, 11, 13, 11); cv.setSpacing(3)
        sl = QLabel(label); sl.setFont(T.ui_font(8.5))
        W.register_restyle(lambda: sl.setStyleSheet(
            f"color:{T.t('txt3')};background:transparent;"), sl)
        sv = QLabel(value); sv.setFont(T.ui_font(12.5, semibold=True))
        col = kind if kind in ("ok", "warn", "err") else "txt1"
        W.register_restyle(lambda c2=col, w=sv: w.setStyleSheet(
            f"color:{T.t(c2)};background:transparent;"), sv)
        cv.addWidget(sl); cv.addWidget(sv)
        return c

    def _price_break_graph(self, breaks) -> QWidget:
        """Horizontal-bar price ladder (mockup .pbreaks): each rung is 'qty+  ==bar==
        $price', the bar filled to price/max (qty-1 has the highest price → the full
        bar), so the volume discount reads at a glance. Mono on the aligned columns."""
        box = QWidget(); box.setMaximumWidth(480)
        v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        ladder = sorted(breaks, key=lambda b: b.get("qty", 0))
        prices = [LM._coerce_price(b.get("price")) for b in ladder]
        valid = [p for p in prices if p is not None]
        maxp = max(valid) if valid else 0.0
        for b, p in zip(ladder, prices):
            row = QWidget(); h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0); h.setSpacing(12)
            qn = QLabel(f"{b.get('qty')}+"); qn.setFont(T.mono_font(9)); qn.setFixedWidth(48)
            W.register_restyle(lambda w=qn: w.setStyleSheet(
                f"color:{T.t('txt3')};background:transparent;"), qn)
            frac = (p / maxp) if (p is not None and maxp > 0) else 0.0
            bar = QProgressBar(); bar.setObjectName("pbar"); bar.setRange(0, 1000)
            bar.setValue(int(round(frac * 1000))); bar.setTextVisible(False)
            bar.setFixedHeight(9)
            W.register_restyle(lambda w=bar: w.setStyleSheet(
                f"QProgressBar#pbar{{background:{T.t('field')};border:none;border-radius:4px;}}"
                f"QProgressBar#pbar::chunk{{background:{T.t('txt3')};border-radius:4px;}}"), bar)
            pr = QLabel(self._fmt_price(p)); pr.setFont(T.mono_font(9, semibold=True))
            pr.setFixedWidth(64); pr.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            W.register_restyle(lambda w=pr: w.setStyleSheet(
                f"color:{T.t('txt1')};background:transparent;"), pr)
            h.addWidget(qn, 0); h.addWidget(bar, 1); h.addWidget(pr, 0)
            v.addWidget(row)
        return box

    def _remember_sourcing(self, mpn, res):
        """Cache a live sourcing result for this session AND persist a snapshot
        (price / stock / lifecycle / lead time) so it survives relaunch."""
        if not res:
            return
        key = (mpn or res.get("mpn") or "").strip()
        if key:
            self._src_cache[key] = res
        if key and any(res.get(f) is not None
                       for f in ("unit_price", "stock", "lifecycle", "lead_time")):
            try:
                LM.save_sourcing_snapshot(self._ctx.cfg, key, res)
            except Exception:  # noqa: BLE001
                pass

    def _no_match_message(self, ident: str) -> str:
        """SRC-04: a distributor lookup came back empty. Distinguish a genuine no-match
        from the shared Mouser key hitting its daily cap — when a cap is in effect, tell
        the user when it frees up (with LCSC still working) instead of a dead-end 'No
        match'. Mirrors mouser_search._rate_limit_message."""
        try:
            secs = LM.mouser_reset_seconds_remaining()
        except Exception:                            # noqa: BLE001
            secs = None
        if secs:
            return (f"Mouser is rate-limited. The built-in key is shared (1000 "
                    f"lookups/day) and frees up in ~{fmt_countdown(secs)}. "
                    f"LCSC still works in the meantime.")
        return f"No Mouser match for {ident}."

    def _lookup_one(self, row):
        mpn = row.get("mpn")
        if not (self._lookup and mpn):
            return

        def done(res, ok):
            if not res:
                self._ctx.services.log(self._no_match_message(mpn)); return
            self._remember_sourcing(mpn, res)
            if self._current is row or (self._current or {}).get("mpn") == mpn:
                self.show(self._current or row)
        run_populate(self._ctx, lambda: self._lookup(mpn), done,
                     busy=f"Looking up {mpn} on Mouser...")

    # ── inline edit + git ──────────────────────────────────────────────────────
    def _commit_field(self, label: str, prop_key: str, row_key: str, value: str):
        row = self._current
        if not row:
            return
        names = row.get("symbols") or []
        if not names:
            self._ctx.services.log(f"Cannot edit {label}: this part has no symbol."); return
        ident = row.get("mpn") or row.get("name") or "part"

        def job():
            # Write to disk immediately (silent — a file write, no git, no
            # subprocess, no window). The commit + push is DEFERRED to Save so a
            # component update no longer fires a per-field push storm (the flash).
            return LM.set_library_symbol_property(self._ctx.cfg, names, prop_key, value)

        def done(wrote, ok):
            if wrote:
                self._mark_unsaved(label, ident)
                self._ctx.services.log(f"Edited {label} on {ident} (unsaved).")
                new = dict(row); new[row_key] = value
                self._current = new             # keep identity current for the rescan re-show
                # BUG-4: refresh the detail exactly ONCE. When a list is wired
                # (_on_changed = rescan), the rescan's preserve=True selection drives
                # a single detail.show off the fresh rows — so DON'T also show() here
                # (that was the double render). With no list wired (e.g. standalone),
                # show() ourselves so the edit still reflects.
                if self._on_changed:            # keep list dots + facet counts truthful
                    self._on_changed()
                else:
                    self.show(new)
                # LIB-05: entering the part number / Mouser P/N offers to autofill
                # the rest of the identity from Mouser (preview + confirm).
                if row_key in ("mpn", "mouser_pn") and value.strip() and self._lookup:
                    self._offer_autofill(value.strip())
            else:
                self._ctx.services.log(f"{label}: no change written.")
        run_populate(self._ctx, job, done, busy=f"Editing {label}…")

    def _offer_autofill(self, number: str):
        """LIB-05: look `number` up on Mouser and, if it adds anything, preview the
        current-vs-Mouser fields in _AutofillDialog and apply the user's choice."""
        def done(res, ok):
            if not res:
                self._ctx.services.log(self._no_match_message(number)); return
            self._apply_fetched(res)
        run_populate(self._ctx, lambda: self._lookup(number), done,
                     busy=f"Looking up {number} on Mouser...")

    def _apply_fetched(self, res: dict):
        """Given a fetched/chosen Mouser part, preview current-vs-Mouser identity in
        _AutofillDialog and apply the user's choice. Shared by the exact-MPN lookup
        and the live catalog search (both hand us the same normalized part dict)."""
        if not res:
            return
        self._remember_sourcing(res.get("mpn"), res)
        # Nothing Mouser has would change the part → just surface the sourcing.
        if not LM.autofill_plan(self._current or {}, res, "overwrite"):
            if self._current:
                self.show(self._current)
            return
        dlg = _AutofillDialog(self._current or {}, res, self)
        if dlg.exec_() != QDialog.Accepted:
            if self._current:
                self.show(self._current)         # still surface the fetched sourcing
            return
        plan = dlg.plan()
        if plan:
            self._apply_autofill(res, plan)
        elif self._current:
            self.show(self._current)

    def _find_on_mouser(self, row: dict):
        """Open the live catalog search seeded from this part, and apply the picked
        result through the same autofill preview. This is the path for a part whose
        exact MPN you don't yet know — search by keyword, pick, enrich."""
        from .mouser_search import MouserSearchDialog
        seed = (row.get("mpn") or row.get("value") or row.get("name") or "").strip()
        dlg = MouserSearchDialog(self._ctx, seed_query=seed, parent=self)
        dlg.exec_()
        if dlg.picked:
            self._apply_fetched(dlg.picked)

    def _apply_autofill(self, fetched: dict, plan: dict):
        """Write the chosen autofill fields into the symbol(s) in one commit."""
        row = self._current
        names = (row or {}).get("symbols") or []
        if not names:
            return
        ident = row.get("mpn") or row.get("name") or "part"
        prop_of = {rk: prop for rk, prop, _ in LM.AUTOFILL_FIELDS}

        def job():
            wrote = 0
            for row_key, val in plan.items():
                if LM.set_library_symbol_property(self._ctx.cfg, names, prop_of[row_key], val):
                    wrote += 1
            if wrote:
                LM.git_commit_push(self._ctx.cfg, LogSink(self._ctx.services),
                                   CM.field_set("Mouser autofill", ident))
            return wrote

        def done(wrote, ok):
            self._clear_unsaved()               # the autofill commit swept up any pending edits
            self._ctx.services.log(f"Autofilled {plural(wrote, 'field')} on {ident} from Mouser."
                                   if wrote else "Autofill: nothing written.")
            new = dict(row); new.update(plan)
            self._remember_sourcing(new.get("mpn"), fetched)
            self.show(new)
            if self._on_changed:
                self._on_changed()
        run_populate(self._ctx, job, done, busy="Applying Mouser autofill...")

    # ── drop-in assets ─────────────────────────────────────────────────────────
    def _dropin_footprint(self, path: str):
        row = self._current
        names = (row or {}).get("symbols") or []
        if not names:
            self._ctx.services.log("Drop-in needs a part with a symbol to link the footprint to.")
            return

        def job():
            log = LogSink(self._ctx.services)
            stem = LM.install_footprint_file(self._ctx.cfg, path, log)
            if stem and LM.set_library_symbol_footprint(self._ctx.cfg, names, stem, log):
                LM.git_commit_push(self._ctx.cfg, log,
                                   CM.add_footprint(stem, names))
            return stem
        self._after_dropin(job, lambda stem: {"footprint": stem, "has_footprint": True,
                                              "dangling": False}, "footprint")

    def _dropin_model(self, path: str):
        row = self._current
        fp = (row or {}).get("footprint")
        if not fp:
            self._ctx.services.log("Drop-in needs a part with a footprint to attach the model to.")
            return

        def job():
            log = LogSink(self._ctx.services)
            name = LM.install_model_file(self._ctx.cfg, path, log)
            if name:
                # Write the model into the footprint file itself — the real,
                # KiCad/BOM/auto_assign-visible tie the import path also produces —
                # so has_model reflects on-disk state and the commit message matches
                # the diff. Only if that footprint can't be edited do we fall back to
                # the JSON override (and say so in the message, not claim a footprint add).
                if attach_model_to_footprint(self._ctx.cfg, fp, name):
                    LM.git_commit_push(self._ctx.cfg, log, CM.add_model(name, fp))
                else:
                    apply_model_override(self._ctx.cfg, fp, name)
                    LM.git_commit_push(
                        self._ctx.cfg, log,
                        f"chore(lib): associate 3D model {name} with {fp} (override)")
            return name
        self._after_dropin(job, lambda name: {"model": name, "has_model": True,
                                             "dangling": False}, "3D model")

    def _dropin_symbol(self, path: str):
        def job():
            log = LogSink(self._ctx.services)
            ok = LM.install_symbol_file(self._ctx.cfg, path, log)
            if ok:
                LM.git_commit_push(self._ctx.cfg, log,
                                   CM.add_symbol(Path(path).name))
            return ok

        def done(ok, ran):
            # The rescan below (_on_changed = library rescan) IS the link step, so
            # don't tell the user to "re-scan" a step the code already runs. Rescan
            # first, then re-show the current row against the fresh rows so a newly
            # linked symbol renders immediately — matching the footprint/model
            # drop-ins' _after_dropin behavior.
            if not ok:
                self._ctx.services.log("Symbol drop-in failed, see status.")
                return
            self._clear_unsaved()               # the symbol commit swept up any pending edits
            self._ctx.services.log(f"Symbol merged: {Path(path).name}.")
            if self._on_changed:
                self._on_changed()
            if self._current:
                self.show(self._current)
        run_populate(self._ctx, job, done, busy=f"Merging {Path(path).name}...")

    def _after_dropin(self, job, row_patch, kind: str):
        """Run a drop-in job, then refresh the detail + list. `row_patch(result)`
        returns the row fields to merge when the job returned a truthy result."""
        row = self._current

        def done(result, ok):
            if not result:
                self._ctx.services.log(f"{kind.title()} drop-in did not complete, see status.")
                return
            self._clear_unsaved()               # the drop-in commit swept up any pending edits
            self._ctx.services.log(f"Dropped in {kind}: {result}.")
            if row is self._current and row is not None:
                new = dict(row); new.update(row_patch(result))
                self.show(new)
            if self._on_changed:
                self._on_changed()
        run_populate(self._ctx, job, done, busy=f"Adding {kind}...")

    # ── previews ────────────────────────────────────────────────────────────────
    def _render_symbol(self, row):
        block = symbol_block_for(self._ctx.cfg, (row.get("symbols") or [None])[0])
        if not block:
            self._sym.set_empty("No Symbol"); return

        def done(img, ok):
            if self._current is not row:             # a newer part was selected mid-render
                return
            self._sym.set_image(img if ok else None)
        run_populate(self._ctx, lambda: R.render_symbol_image(block), done)

    def _render_footprint(self, row):
        path = footprint_path_for(self._ctx.cfg, row)
        if not path:
            self._fp.set_empty("No Footprint"); return

        def job():
            return R.render_footprint_image(path), R.footprint_summary(path)

        def done(res, ok):
            if self._current is not row:             # a newer part was selected mid-render
                return                               # don't paint A's footprint onto B's card
            img, summ = res if res else (None, None)
            self._fp.set_image(img)
            self._fp_summary = summ                  # kept so a unit flip can re-caption
            if summ:
                self._fp.set_caption(self._fp_caption(summ))
        run_populate(self._ctx, job, done)

    def _fp_caption(self, summ) -> str:
        # dims are canonical mm (fp_render rounds to 3dp); show in the app-wide unit.
        # Lead with the footprint's own name so the card says WHICH footprint this is
        # (the caption word-wraps, so a long name is shown in full, never cut off).
        dims = (f"{summ['pads']} Pads · "
                f"{U.fmt_dims(summ['width_mm'], summ['height_mm'], mm_dec=3, mils_dec=1)}")
        stem = (self._current or {}).get("footprint")
        return f"{stem} · {dims}" if stem else dims

    def _render_model(self, row):
        path = model_path_for(self._ctx.cfg, row)
        if not path:
            self._mdl.set_empty("No 3D Model"); return

        def job():
            return resolve_model_render(path), R.step_summary(path)

        def done(res, ok):
            if self._current is not row:             # a newer part was selected mid-render
                return                               # don't paint A's model onto B's card
            (kind, payload), summ = res if res else (("none", None), None)
            if kind == "none":
                self._mdl.set_empty("3D Preview Unavailable"); return
            self._mdl.set_mesh(kind, payload)
            self._mdl_summary = summ                 # kept so a unit flip can re-caption
            if summ:
                cap = self._mdl_caption(summ)
                if cap:
                    self._mdl.set_caption(cap)
        run_populate(self._ctx, job, done)

    def _mdl_caption(self, summ):
        sz = summ.get("size_mm") or []
        if len(sz) != 3:
            return None
        # size_mm is canonical mm (fp_render rounds to 2dp); show in the app-wide unit.
        return (f"{summ['triangles']} Triangles · "
                f"{U.fmt_dims(sz[0], sz[1], sz[2], mm_dec=2, mils_dec=1)}")


def _highlight_html(text: str, needle: str) -> str:
    """HTML for `text` with every case-insensitive occurrence of `needle` wrapped in a
    neutral highlight span. The emphasis is a grayscale wash (hairline_strong adapts
    per theme), never a brand hue — a scan cue, not a status colour (design-rules §4)."""
    low, out, i, n = text.lower(), [], 0, len(needle)
    while i < len(text):
        j = low.find(needle, i)
        if j < 0:
            out.append(escape(text[i:])); break
        out.append(escape(text[i:j]))
        out.append(f'<span style="background:{T.t("hairline_strong")};">'
                   f'{escape(text[j:j + n])}</span>')
        i = j + n
    return "".join(out)


class _ClickLabel(QLabel):
    """A label that fires `on_click` on a left press (a lightweight clickable chip —
    the row's Duplicate badge). Accepts the event so the list's own click still runs
    (selecting the row) but the badge action takes precedence."""

    def __init__(self, text: str, on_click, parent=None):
        super().__init__(text, parent)
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and callable(self._on_click):
            self._on_click(); e.accept(); return
        super().mousePressEvent(e)


class _ElideLabel(QLabel):
    """A single-line label that elides (…) on the right instead of forcing its
    row wide — keeps long Mouser descriptions from spawning a horizontal scroll
    in the narrow parts list. Reports its FULL text via full_text(). When a search
    query is set (`set_highlight`), the matched substrings in the ELIDED text carry a
    neutral highlight so the query is visible in the row without a widget rebuild."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text or ""
        self._hl = ""                       # lowercased highlight substring (search match)
        from PyQt5.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._reelide()

    def setText(self, text: str):
        self._full = text or ""
        self._reelide()

    def full_text(self) -> str:
        return self._full

    def set_highlight(self, query: str):
        """Set the search substring to emphasise (empty clears it). Cheap no-op when
        unchanged so re-applying the same query across every row does no work."""
        q = (query or "").strip().lower()
        if q == self._hl:
            return
        self._hl = q
        self._reelide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reelide()

    def _reelide(self):
        w = max(0, self.width())
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, w) if w else self._full
        # Highlight the match within what is ACTUALLY shown (post-elide), so an
        # ellipsized name still emphasises the part of the query that survived.
        if self._hl and self._hl in shown.lower():
            self.setTextFormat(Qt.RichText)
            super().setText(_highlight_html(shown, self._hl))
        else:
            self.setTextFormat(Qt.PlainText)
            super().setText(shown)
        # When the text had to be shortened, expose the FULL value on hover so a
        # long footprint name / part description is never truly lost to the ellipsis.
        self.setToolTip(self._full if shown != self._full else "")


# LIB-02 taxonomy: a two-level health filter. The PRIMARY axis is the verdict
# (Complete vs Missing); the MISSING axis breaks a Missing part down by WHAT it
# lacks. A part is Complete only when it has all three assets, a manufacturer,
# and no dangling reference — so Missing == "needs attention for some reason".
def _is_complete(r) -> bool:
    return bool(r.get("has_symbol") and r.get("has_footprint") and r.get("has_model")
                and r.get("manufacturer") and not r.get("dangling"))


def _is_unlinked_footprint(r) -> bool:
    """A footprint-only orphan (LM:2117): a real footprint on disk with no symbol
    to place it. It is NOT an orderable part and is NOT a symbol that merely lacks
    a footprint — it is its own distinct maintenance state, so it gets its own
    facet instead of being lumped into 'Missing Symbol' beside every unlinked
    symbol."""
    return bool(r.get("has_footprint") and not r.get("has_symbol"))


PRIMARY_FACETS = (
    ("All", lambda r: True),
    ("Complete", _is_complete),
    ("Missing", lambda r: not _is_complete(r)),
)
MISSING_FACETS = (
    # 'Missing Symbol' now means a part that HAS a symbol-shaped identity but no
    # symbol asset would be nonsensical — so it stays a symbol-less row that is NOT
    # a footprint-only orphan (those get their own facet below), i.e. an entirely
    # empty group. In practice this bucket is a symbol referencing a missing part;
    # the footprint-only orphan is split out so the two never mix.
    ("Unlinked Footprints", _is_unlinked_footprint),
    ("Missing Footprint", lambda r: r.get("has_symbol") and not r.get("has_footprint")),
    ("Missing 3D Model", lambda r: r.get("has_footprint") and not r.get("has_model")),
    ("Missing Mouser Data", lambda r: r.get("has_symbol") and not r.get("manufacturer")),
    ("Dangling", lambda r: bool(r.get("dangling"))),
)
# Flat tuple for count/predicate lookup; the bar renders the two levels separately.
FACETS = PRIMARY_FACETS + MISSING_FACETS
_FACET_PRED = dict(FACETS)


class PartsList(QWidget):
    """Selectable master list of grouped parts + a client-side search filter and a
    health facet. One 6px asset-state dot per row (the only color); selection uses
    the native row wash. No borders."""

    def __init__(self, rows, on_select: Callable[[dict], None], parent=None, *,
                 group_by: str = "Category",
                 on_group_change: Optional[Callable[[str], None]] = None,
                 on_resolve_dup: Optional[Callable[[dict], None]] = None):
        super().__init__(parent)
        self._rows = list(rows)
        self._on_select = on_select
        # Panel-wired seams: persist the last-used grouping, and open the one-confirm
        # keep/delete flow when a row's Duplicate badge is clicked. Both optional so the
        # bare PartsList(rows, on_select) constructor the tests use keeps working.
        self._on_group_change = on_group_change
        self._on_resolve_dup = on_resolve_dup
        self._facet = "All"
        self._query = ""
        self._group_by = (group_by if group_by in ("Category", "Completion",
                                                   "Manufacturer", "None") else "Category")
        # Multi-select "Show" filter (finder bar): a row is HIDDEN when it belongs to an
        # UNCHECKED class (AND-exclude). Every part is exactly one of Complete /
        # Incomplete / Needs A Fix, plus an orthogonal Not Orderable tag.
        self._show = {"Complete", "Incomplete", "Not Orderable", "Needs A Fix"}
        # "Duplicates only": restrict the list to parts that duplicate another — a shared
        # REAL manufacturer part number (computed from the rows), or a byte-identical
        # footprint file (from find_duplicate_footprints, seeded by the panel via
        # set_duplicate_footprints). Off by default.
        self._dupes_only = False
        self._dup_footprints: set = set()  # footprint stems in a geometry-dup group
        self._dup_mpns: set = set()        # real MPNs shared by 2+ parts
        self._compute_dup_mpns()
        self._selected_key = None          # mpn/name of the selected part, kept across rebuilds
        self._headers = []                 # (group_label, QListWidgetItem, widget) — for hide/show
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)

        self._search = QLineEdit(); self._search.setPlaceholderText("Search Parts...")
        self._search.setFont(T.ui_font(10))
        # BUG-1a: debounce the search. Every keystroke used to tear down and rebuild
        # the whole list (and re-fire the detail), which — combined with the commit
        # cascade — spawned a storm of modal dialogs. Now a keystroke only stores the
        # query and (re)arms a single-shot timer; the filter runs once it settles.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply)
        self._search.textChanged.connect(self._on_search_text)
        W.register_restyle(lambda: self._search.setStyleSheet(
            f"QLineEdit{{background:{T.t('inset')};border:none;border-radius:6px;"
            f"padding:6px 10px;color:{T.t('txt1')};}}"), self._search)
        lay.addWidget(self._search)

        # Finder bar (design-rules §4 — no hidden chrome): the Show / Group By /
        # Duplicates-only controls live in an ALWAYS-VISIBLE wrapping bar under the
        # search, not behind a filter button + pop.
        self._filter_bar = self._build_filter_bar()
        lay.addWidget(self._filter_bar)

        self._list = QListWidget()
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Multi-select (Ctrl/Shift+click) for bulk duplicate management; group headers
        # carry NoItemFlags so they can never enter the selection. The detail still
        # follows the CURRENT row; the selection SET drives the footer + Manage Duplicates.
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.currentRowChanged.connect(self._on_row)
        self._list.itemSelectionChanged.connect(self._update_selection_footer)
        W.register_restyle(self._restyle_list, self._list)
        lay.addWidget(self._list, 1)

        # Sticky group header (mockup .gh position:sticky): a pinned overlay label,
        # parented to the LIST (a viewport() child would scroll with content), kept
        # over the viewport top and re-texted to the topmost visible group on scroll.
        self._overlay = QLabel(self._list)
        self._overlay.setObjectName("partGroupHeaderPinned")
        self._overlay.setFont(T.ui_font(9, semibold=True))
        self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._overlay.hide()
        sb = self._list.verticalScrollBar()
        sb.valueChanged.connect(self._sync_overlay)
        sb.rangeChanged.connect(lambda *_: self._sync_overlay())   # fires once the list has real geometry
        self._list.viewport().installEventFilter(self)

        # Selection footer (mockup): 'N of M selected' while a multi-selection is active,
        # else the quiet visible-count. Sits under the list.
        self._footer = W.static_label("", "dim")
        self._footer.setFont(T.ui_font(9))
        lay.addWidget(self._footer)

        self._items = []                   # (row, QListWidgetItem, widget) DATA rows only
        self._visible = []
        self._group_labels = {}            # casefold(group) -> first-seen display label
        self._rebuilding = False           # True while _rebuild_widgets mutates the list
        self._rebuild_widgets()            # build the row widgets ONCE (PERF lib_preview:1175)
        self._apply()
        self._update_selection_footer()

    def _restyle_list(self):
        self._list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;}}"
            f"QListWidget::item{{border-radius:6px;}}"
            f"QListWidget::item:hover{{background:{T.t('card_hover')};}}"
            f"QListWidget::item:selected{{background:{T.t('inset')};}}")
        # The per-row widgets recolor themselves via their own registered
        # restylers (dot + text tokens), so nothing to iterate here.

    # ── inline finder bar: Show (multi-select) + Group By (single) + Duplicates ────
    def _pop_label(self, text: str) -> QLabel:
        lab = QLabel(text); lab.setObjectName("finderPopLabel")
        lab.setFont(T.ui_font(8, semibold=True))
        return lab

    def _build_filter_bar(self) -> QWidget:
        """The always-visible inline filter/toggle bar (design-rules §4 — no hidden
        chrome), two semantic rows so a section label always leads its controls even as
        they wrap in the narrow picker: a FILTER row ('Show' classes + Duplicates-only)
        and a GROUP row (the single-select grouping)."""
        bar = QWidget(); bar.setObjectName("finderBar")
        col = QVBoxLayout(bar); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(2)

        filt = QWidget(); frow = FlowLayout(filt, hspacing=6, vspacing=4)
        frow.addWidget(self._pop_label("Show"))
        self._show_boxes = {}
        for name in ("Complete", "Incomplete", "Not Orderable", "Needs A Fix"):
            cb = QCheckBox(name); cb.setObjectName("finderOpt"); cb.setChecked(True)
            cb.setCursor(Qt.PointingHandCursor)
            cb.toggled.connect(lambda on, n=name: self._on_show_toggle(n, on))
            self._show_boxes[name] = cb; frow.addWidget(cb)
        # Duplicates-only is a filter (it narrows the set), so it lives on the filter row.
        self._dupes_box = QCheckBox("Duplicates only"); self._dupes_box.setObjectName("finderOpt")
        self._dupes_box.setCursor(Qt.PointingHandCursor)
        self._dupes_box.setToolTip("Show only parts that duplicate another: a shared part "
                                   "number, or a byte-identical footprint")
        self._dupes_box.toggled.connect(self._on_dupes_toggle)
        frow.addWidget(self._dupes_box)
        col.addWidget(filt)

        grp = QWidget(); grow = FlowLayout(grp, hspacing=6, vspacing=4)
        grow.addWidget(self._pop_label("Group"))
        self._group_radios = {}
        self._group_bg = QButtonGroup(bar)
        for name in ("Category", "Completion", "Manufacturer", "None"):
            rb = QRadioButton(name); rb.setObjectName("finderOpt")
            rb.setCursor(Qt.PointingHandCursor)
            rb.setChecked(name == self._group_by)
            self._group_bg.addButton(rb)
            rb.toggled.connect(lambda on, n=name: self._pick_group(n) if on else None)
            self._group_radios[name] = rb; grow.addWidget(rb)
        col.addWidget(grp)
        return bar

    def _on_dupes_toggle(self, on: bool):
        self._dupes_only = bool(on)
        self._apply(preserve=True)         # pure hide/show — no widget rebuild

    def _on_show_toggle(self, name: str, on: bool):
        (self._show.add if on else self._show.discard)(name)
        self._apply(preserve=True)         # a pure hide/show — no widget rebuild

    def _pick_group(self, name: str):
        # Rebuild from OUTSIDE the radio's toggled signal (blueprint footgun #2): defer
        # the list teardown so it never runs mid-signal.
        QTimer.singleShot(0, lambda: self.set_group_by(name))

    def _show_match(self, row) -> bool:
        """AND-exclude: a row is hidden when it belongs to an unchecked Show class."""
        c = LM.part_completion(row)
        state = ("Needs A Fix" if c["dangling"]
                 else "Complete" if c["is_complete"] else "Incomplete")
        if state not in self._show:
            return False
        if not row.get("has_real_mpn") and "Not Orderable" not in self._show:
            return False
        return True

    def _row_widget(self, row) -> QWidget:
        """A two-line row (mockup .row) — the humanized 'what it IS' over the
        technical part number — with a right-side red warning triangle on any
        incomplete/dangling part (silent when complete) and the N/8 passport badge.
        Both text lines elide."""
        names = LM.part_display_names(row)
        comp = LM.part_completion(row)
        w = QWidget()
        w.setProperty("humanized", names["humanized"])
        w.setProperty("technical", names["technical"])
        w.setProperty("incomplete", not comp["is_complete"])

        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 3, 2, 3); lay.setSpacing(9)

        col = QVBoxLayout(); col.setSpacing(1); col.setContentsMargins(0, 0, 0, 0)
        prim = _ElideLabel(names["humanized"]); prim.setObjectName("partRowPrimary")
        prim.setFont(T.ui_font(10))
        sub = _ElideLabel(names["technical"]); sub.setObjectName("partRowTechnical")
        sub.setFont(T.mono_font(8))
        # Suppress the second line when it would just repeat the first.
        show_sub = bool(names["technical"]) and names["technical"] != names["humanized"]
        sub.setVisible(show_sub)
        col.addWidget(prim); col.addWidget(sub)
        # LIB-03 / LM:2006: the same honest flag as the detail + BOM — a no-MPN part
        # carries a quiet 'no MPN · not orderable' line so the list never implies a
        # generic passive is orderable.
        flag = None
        if names["flag"] and names["technical"]:
            flag = _ElideLabel(names["flag"]); flag.setObjectName("partRowNoMpn")
            flag.setFont(T.ui_font(8))
            col.addWidget(flag)
        lay.addLayout(col, 1)
        # Search-highlight seam: _apply_highlight re-tints the matched substring in
        # these two labels on each settled query, WITHOUT a widget rebuild. Store the
        # technical line only when it is actually shown (isVisible() is False on an
        # unshown widget, so gate on the intended-visibility flag, not the live state).
        w._prim = prim
        w._sub = sub if show_sub else None

        # Duplicate badge (right-aligned like the score): shown when the row duplicates
        # another part; clicking it opens the one-confirm keep/delete resolve flow. Built
        # for every row and toggled by _refresh_dup_badges, since the footprint-dup signal
        # arrives AFTER the widgets are built (set_duplicate_footprints).
        dup = _ClickLabel("Duplicate", lambda r=row: self._resolve_dup(r))
        dup.setObjectName("partRowDup")
        dup.setFont(T.ui_font(8, semibold=True))
        dup.setToolTip("Duplicates another part; click to keep or delete")
        dup.setVisible(self._is_duplicate(row))
        lay.addWidget(dup, 0, Qt.AlignVCenter)
        w._dup_badge = dup

        # Warning triangle (mockup .rowwarn): silent-complete / red-triangle-on-any-gap
        # convention — a part that is incomplete OR has a broken link shows it; a
        # complete part shows nothing (the quiet default).
        warn = None
        if not comp["is_complete"]:
            warn = QLabel(); warn.setObjectName("partRowWarn")
            warn.setFixedSize(15, 15)
            warn.setToolTip("Has a broken link, needs a fix" if comp["dangling"]
                            else f"Incomplete, {comp['score']} of {comp['total']}")
            lay.addWidget(warn, 0, Qt.AlignVCenter)

        # completion score (v2.11): the honest N/8 passport badge, right-aligned — the
        # picker's at-a-glance "how done is this part". Small, dim by default, green when
        # Complete; a dangling part shows 'Fix' (it can never be Complete).
        score = QLabel(LM.completion_badge(row)); score.setObjectName("partRowScore")
        score.setFont(T.ui_font(9, semibold=True) if comp["dangling"] else T.mono_font(9))
        lay.addWidget(score, 0, Qt.AlignVCenter)

        def restyle():
            prim.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
            sub.setStyleSheet(f"color:{T.t('txt3')};background:transparent;")
            if flag is not None:
                flag.setStyleSheet(f"color:{T.t('warn')};background:transparent;")
            dup.setStyleSheet(f"background:{T.t('ctl')};color:{T.t('txt2')};"
                              f"border-radius:6px;padding:1px 6px;")
            if comp["dangling"]:
                score.setStyleSheet(f"color:{T.t('err')};background:transparent;")
            elif comp["is_complete"]:
                score.setStyleSheet(f"color:{T.t('ok')};background:transparent;")
            else:
                score.setStyleSheet(f"color:{T.t('txt3')};background:transparent;")
            if warn is not None:                    # err token shifts with theme
                warn.setPixmap(W.svg_icon(icons.GLYPHS["alert"], size=15,
                                          color=T.t("err")).pixmap(15, 15))
            # Keep the highlight wash theme-correct after a flip while a query is live.
            prim._reelide()
            if w._sub is not None:
                w._sub._reelide()
        W.register_restyle(restyle, w)
        restyle()
        return w

    # ── grouping (mockup Group By: Category | Completion | Manufacturer | None) ────
    _COMPLETION_ORDER = ("Needs A Fix", "Incomplete", "Complete")

    def _group_key(self, row) -> str:
        """The group label a row falls under for the current Group By mode. 'None'
        collapses to one flat group ('' → no header)."""
        gb = self._group_by
        if gb == "None":
            return ""
        if gb == "Manufacturer":
            return (row.get("manufacturer") or "").strip() or "Unknown Manufacturer"
        if gb == "Completion":
            c = LM.part_completion(row)
            if c["dangling"]:
                return "Needs A Fix"
            return "Complete" if c["is_complete"] else "Incomplete"
        return (row.get("category") or "").strip() or "Uncategorized"   # Category (default)

    def _grouped_rows(self):
        """[(display_label, [rows])] in display order — problems-first for Completion,
        alphabetical for Category/Manufacturer, a single unlabelled group for None.
        Buckets by casefold so 'Resistor' and 'resistor' consolidate into one group
        (displayed with the first-seen casing)."""
        if self._group_by == "None":
            return [("", list(self._rows))]
        buckets, labels, order = {}, {}, []
        for row in self._rows:
            raw = self._group_key(row)
            k = raw.casefold()
            if k not in buckets:
                buckets[k] = []; labels[k] = raw; order.append(k)
            buckets[k].append(row)
        if self._group_by == "Completion":
            order = [c.casefold() for c in self._COMPLETION_ORDER if c.casefold() in buckets]
        else:
            order = sorted(order)          # keys are casefolded → case-insensitive order
        return [(labels[k], buckets[k]) for k in order]

    def _header_widget(self, label: str) -> QLabel:
        """A quiet group header (mockup .gh) — semibold t3, styled by object name."""
        lab = QLabel(label); lab.setObjectName("partGroupHeader")
        lab.setFont(T.ui_font(9, semibold=True))
        return lab

    def _rebuild_widgets(self):
        # PERF (lib_preview:1175): build one item+widget per row ONCE, here — a
        # facet click / settled search then only toggles setHidden per item
        # (_refilter) instead of tearing down and re-creating every row widget on
        # each filter change. This runs only when the BACKING rows change
        # (init / set_rows / group-by change), where the row content may differ.
        #
        # BUG-1b: a programmatic rebuild must NOT emit currentRowChanged per row —
        # each emission re-ran _on_row -> detail.show, rebuilding the editable
        # detail fields (and, with the old commit path, spawning dialogs). Block the
        # list's signals for the whole teardown+rebuild; the caller refreshes the
        # detail exactly once afterwards.
        # Fix the header row height from font metrics + the qss padding (10px top +
        # 5px bottom) — NOT the header widget's sizeHint, which is unpadded at rebuild
        # time (the app qss is applied AFTER the panel builds in the shell), so a
        # sizeHint-derived height froze at ~18px and clipped the label + pinned overlay.
        self._header_h = QFontMetrics(T.ui_font(9, semibold=True)).height() + 15
        # The scrollbar's rangeChanged fires DURING this rebuild (blockSignals on the
        # list does NOT block a child scrollbar's signals) — guard _sync_overlay so it
        # never runs against the half-built list / stale _visible (would mis-size or
        # stick the pinned overlay). It re-syncs once _apply completes.
        self._rebuilding = True
        grouped = self._grouped_rows()
        self._group_labels = {label.casefold(): label for label, _ in grouped}
        self._list.blockSignals(True)
        try:
            self._list.clear()
            self._items = []               # (row, QListWidgetItem, widget) DATA rows only
            self._headers = []             # (label, QListWidgetItem, widget) group headers
            for label, rows in grouped:
                if label:                  # '' (None grouping) → no header row
                    hit = QListWidgetItem()
                    hit.setFlags(Qt.NoItemFlags)          # non-selectable / non-hover
                    hit.setData(Qt.UserRole, ("__header__", label))
                    hw = self._header_widget(label)
                    hit.setSizeHint(QSize(0, self._header_h))
                    self._list.addItem(hit); self._list.setItemWidget(hit, hw)
                    self._headers.append((label, hit, hw))
                for row in rows:
                    it = QListWidgetItem()
                    it.setData(Qt.UserRole, row)
                    w = self._row_widget(row)
                    it.setSizeHint(w.sizeHint())
                    self._list.addItem(it)
                    self._list.setItemWidget(it, w)
                    self._items.append((row, it, w))
        finally:
            self._list.blockSignals(False)
            self._rebuilding = False

    def _refilter(self):
        """Hide/show the pre-built row items to match the current facet + search,
        WITHOUT rebuilding any widget (PERF lib_preview:1175). Rebuilds `_visible`
        as the ordered list of the rows currently shown."""
        pred = _FACET_PRED.get(self._facet, lambda r: True)
        self._visible = []
        visible_groups = set()
        self._list.blockSignals(True)
        try:
            for row, it, _w in self._items:
                shown = (pred(row) and self._matches(row) and self._show_match(row)
                         and (not self._dupes_only or self._is_duplicate(row)))
                it.setHidden(not shown)
                if shown:
                    self._visible.append(row)
                    visible_groups.add(self._group_key(row))
            # Hide a group header when none of its rows survive the facet/search.
            for label, hit, _hw in self._headers:
                hit.setHidden(label not in visible_groups)
        finally:
            self._list.blockSignals(False)
        self._sync_overlay()

    def _item_row_for(self, row) -> int:
        """The full-list index of the given row's item (headers + hidden rows are
        interleaved), or -1. setCurrentRow takes a full-list index, so map through
        the item cache with self._list.row() (NOT the _items position — headers offset it)."""
        for r, it, _w in self._items:
            if r is row:
                return self._list.row(it)
        return -1

    def _matches(self, r) -> bool:
        q = self._query
        if not q:
            return True
        return (q in str(r.get("mpn") or "").lower()
                or q in str(r.get("name") or "").lower()
                or q in str(r.get("manufacturer") or "").lower()
                or q in str(r.get("description") or "").lower())

    def _key(self, r) -> str:
        # BUG-2: identify a row by its STABLE symbol name, not its mpn — editing the
        # MPN (autofill / enrich) used to change the key out from under
        # preserve=True, so selection snapped back to row 0 and yanked the detail off
        # the just-edited part. The symbol name survives an mpn edit; fall back to
        # mpn/name only for symbol-less rows.
        syms = r.get("symbols") or []
        if syms:
            return f"sym:{syms[0]}"
        return str(r.get("mpn") or r.get("name") or "")

    def _apply(self, preserve: bool = False):
        # A settled query fires this directly (via the debounce timer) as well, so
        # sync the stored query from the search box in that case.
        if self._search_timer.isActive():
            self._search_timer.stop()
        # PERF (lib_preview:1175): a facet/search change is now a pure hide/show over
        # the pre-built row widgets — no teardown/rebuild — so it stays cheap on a
        # large library and never re-runs the restylers.
        self._refilter()
        if self._visible:
            # A fresh facet/search resets to the top; a post-mutation rescan
            # (preserve=True) keeps you on the same part when it is still shown,
            # so an edit / drop-in never yanks the detail back to the first part.
            vis_idx = 0
            if preserve and self._selected_key:
                for i, r in enumerate(self._visible):
                    if self._key(r) == self._selected_key:
                        vis_idx = i
                        break
            # setCurrentRow wants a full-list index; map the chosen visible row back
            # through the item cache (hidden items are interleaved).
            item_idx = self._item_row_for(self._visible[vis_idx])
            # Set the selection with signals blocked so it can't emit
            # currentRowChanged -> _on_row (that would double the detail render on
            # top of the explicit call below). BUG-1b / BUG-4: refresh the detail
            # exactly ONCE per settled rebuild.
            self._list.blockSignals(True)
            try:
                self._list.setCurrentRow(item_idx)
            finally:
                self._list.blockSignals(False)
            self._on_row(item_idx)
        else:
            self._selected_key = None
            self._on_select and self._on_select(None)  # clear the detail when empty
        # Re-tint the query match in the visible rows (cheap no-op when the query is
        # unchanged per label), then re-pin the overlay and refresh the count footer.
        self._apply_highlight()
        # Selecting a row can auto-scroll the list, so re-pin the group overlay after.
        self._sync_overlay()
        self._update_selection_footer()

    def _apply_highlight(self):
        """Push the current query onto every row's name/technical labels so the match
        is emphasised without rebuilding any widget (search stays a pure hide/show)."""
        for _row, _it, w in self._items:
            prim = getattr(w, "_prim", None)
            sub = getattr(w, "_sub", None)
            if prim is not None:
                prim.set_highlight(self._query)
            if sub is not None:
                sub.set_highlight(self._query)

    # ── multi-select footer + seams ───────────────────────────────────────────────
    def selected_rows(self) -> list:
        """The part rows currently in the multi-selection (headers excluded)."""
        out = []
        for it in self._list.selectedItems():
            d = it.data(Qt.UserRole)
            if isinstance(d, dict):
                out.append(d)
        return out

    def selected_duplicate_rows(self) -> list:
        """The selected rows that duplicate another part — the Manage Duplicates set."""
        return [r for r in self.selected_rows() if self._is_duplicate(r)]

    def visible_rows(self) -> list:
        """The rows currently shown (post facet/search/Show/Duplicates filter), in
        display order — the set 'Export Visible Parts' writes out."""
        return list(self._visible)

    def _update_selection_footer(self):
        f = getattr(self, "_footer", None)
        if f is None:
            return
        m = self.visible_count()
        sel = len(self.selected_rows())
        f.setText(f"{sel} of {m} selected" if sel >= 2 else plural(m, "part"))
        # Notify the panel (Manage Duplicates enablement) on EVERY selection refresh —
        # including the _apply path where the list's own signals are blocked, so the
        # action never goes stale after a rescan collapses a multi-selection.
        cb = getattr(self, "_on_selection_changed", None)
        if callable(cb):
            cb()

    def _resolve_dup(self, row):
        """A row's Duplicate badge was clicked: hand off to the panel's keep/delete flow.
        Returns whatever the panel callback returns (the dialog, for the drive/test seam)."""
        if callable(self._on_resolve_dup):
            return self._on_resolve_dup(row)
        return None

    def _refresh_dup_badges(self):
        """Show/hide each row's Duplicate badge against the current dup signals (the
        footprint-dup set arrives after the widgets are built)."""
        for row, _it, w in self._items:
            b = getattr(w, "_dup_badge", None)
            if b is not None:
                b.setVisible(self._is_duplicate(row))

    def _on_search_text(self, query: str):
        """BUG-1a: store the query on each keystroke and (re)arm the debounce timer;
        the actual filter/rebuild runs once, when typing settles."""
        self._query = (query or "").strip().lower()
        self._search_timer.start()

    def filter(self, query: str):
        """Filter immediately by `query` (public API; the interactive search box goes
        through the debounced path instead)."""
        self._query = (query or "").strip().lower()
        self._apply()

    def set_rows(self, rows):
        """Replace the backing rows (after a library mutation) and re-apply the
        current facet + search filter, staying on the selected part. The row
        CONTENT can change here (dot colour, lines, asset flags), so the widget
        cache is rebuilt once — unlike a facet/search change, which only hides and
        shows the existing widgets (PERF lib_preview:1175)."""
        self._rows = list(rows)
        self._compute_dup_mpns()
        self._rebuild_widgets()
        self._apply(preserve=True)

    def _compute_dup_mpns(self):
        """Index the REAL manufacturer part numbers that appear on 2+ parts — the honest
        'same part imported twice' duplicate signal (drawn from the rows, no disk read)."""
        counts: dict = {}
        for r in self._rows:
            if r.get("has_real_mpn"):
                mpn = (r.get("mpn") or "").strip()
                if mpn:
                    counts[mpn] = counts.get(mpn, 0) + 1
        self._dup_mpns = {m for m, n in counts.items() if n > 1}

    def set_duplicate_footprints(self, stems):
        """Seed the byte-identical-geometry footprint stems (from
        find_duplicate_footprints) so the 'Duplicates only' filter also surfaces parts
        whose footprint file duplicates another. The panel computes this off-thread with
        the scan and calls this; a re-filter follows if the filter is active."""
        self._dup_footprints = set(stems or ())
        self._refresh_dup_badges()             # footprint-dup rows can now show the badge
        if self._dupes_only:
            self._apply(preserve=True)

    def _is_duplicate(self, row) -> bool:
        if row.get("has_real_mpn") and (row.get("mpn") or "").strip() in self._dup_mpns:
            return True
        return bool(row.get("footprint")) and row.get("footprint") in self._dup_footprints

    def set_facet(self, facet: str):
        """Narrow the list to a health facet (see FACETS). 'All' clears it.
        Keeps the currently-selected part focused when it survives the new facet
        (preserve=True) — only falls back to row 0 when it is filtered out — so a
        facet toggle never yanks the detail off a still-visible part."""
        self._facet = facet if facet in _FACET_PRED else "All"
        self._apply(preserve=True)

    def set_group_by(self, mode: str):
        """Change the row grouping (Category | Completion | Manufacturer | None) and
        rebuild — the list regroups, staying on the selected part when it survives.
        Fires on_group_change so the panel can persist the last-used grouping."""
        mode = mode if mode in ("Category", "Completion", "Manufacturer", "None") else "Category"
        if mode == self._group_by:
            return
        self._group_by = mode
        # Keep the inline radios in sync when set programmatically (smart default /
        # restore), without re-entering _pick_group.
        rb = getattr(self, "_group_radios", {}).get(mode)
        if rb is not None and not rb.isChecked():
            rb.blockSignals(True); rb.setChecked(True); rb.blockSignals(False)
        self._rebuild_widgets()
        self._apply(preserve=True)
        if callable(self._on_group_change):
            self._on_group_change(mode)

    def group_by(self) -> str:
        return self._group_by

    def facet_counts(self) -> dict:
        """{facet_name: count} over ALL rows (not the current filter) for the bar."""
        return {name: sum(1 for r in self._rows if pred(r)) for name, pred in FACETS}

    def visible_count(self) -> int:
        """The number of rows currently SHOWN (not hidden by the facet/search).
        With the hide/show optimization the list still holds every row's item, so
        count the un-hidden ones — not _list.count()."""
        return len(getattr(self, "_visible", []))

    def _on_row(self, i: int):
        # `i` is a full-list item index (hidden rows are interleaved). Resolve the
        # row off the item's stored data rather than positionally into _visible.
        it = self._list.item(i) if i >= 0 else None
        if it is None or it.isHidden():
            return
        row = it.data(Qt.UserRole)
        if not isinstance(row, dict):       # a group header, not a part — never select it
            return
        self._selected_key = self._key(row)
        if self._on_select:
            self._on_select(row)

    # ── sticky group header overlay (mockup .gh position:sticky) ──────────────────
    def _top_group_label(self):
        """The group label of the topmost visible item (header or row), for the pinned
        overlay. Returns None when nothing is visible. A row's raw group key is mapped
        through the first-seen display label so a mixed-case category shows one casing."""
        it = self._list.itemAt(QPoint(4, 1))
        if it is None:
            return None
        d = it.data(Qt.UserRole)
        if isinstance(d, tuple) and len(d) == 2 and d[0] == "__header__":
            return d[1]
        if isinstance(d, dict):
            raw = self._group_key(d)
            return self._group_labels.get(raw.casefold(), raw)
        return None

    def _sync_overlay(self, *_):
        """Pin the current group's header over the viewport top (the sticky effect).
        Hidden when grouping is off (None) or the list is empty. Never runs mid-rebuild
        (rangeChanged fires there) or before the list has real geometry."""
        ov = getattr(self, "_overlay", None)
        if ov is None or getattr(self, "_rebuilding", False):
            return
        if self._group_by == "None" or not getattr(self, "_visible", None):
            ov.hide(); return
        vp = self._list.viewport()
        if vp.width() <= 0:                # no real geometry yet (unshown) — don't mis-place
            ov.hide(); return
        label = self._top_group_label()
        if not label:
            ov.hide(); return
        ov.setText(label)
        tl = vp.mapTo(self._list, QPoint(0, 0))
        ov.setGeometry(tl.x(), tl.y(), vp.width(), getattr(self, "_header_h", 26))
        ov.show(); ov.raise_()

    def eventFilter(self, obj, ev):
        # Keep the pinned overlay sized to the viewport as the list resizes.
        if obj is self._list.viewport() and ev.type() == QEvent.Resize:
            self._sync_overlay()
        return super().eventFilter(obj, ev)

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_overlay()               # the list now has real geometry — pin the header
