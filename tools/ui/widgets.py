"""ui.widgets — the shared component kit, matching the approved mockup.

Chrome styling (buttons, inputs, tables, nav, surfaces) comes from the global QSS
in ui.theme, which re-applies instantly on a theme toggle. The pieces that carry
per-widget colour (tags, tokens, net tokens, the verdict bar, category dots) read
the active theme at build time and register a restyle callback, so `restyle_all()`
retints them on a toggle without rebuilding the tree.

Casing convention (from the design review):
  ALL CAPS  -> structural labels (eyebrows, column headers)   -> `eyebrow(...)`
  Title Case -> human text (titles, buttons, values)
  real casing -> machine data (nets, refdes, pins)            -> `token/net_token`
Separation is by layout, never a middot.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QLabel, QFrame, QHBoxLayout, QVBoxLayout,
                             QPushButton, QGridLayout, QStackedWidget, QSizePolicy,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)

from . import theme as T

def svg_icon(svg: str, size: int = 18, color: str = "#8b8b91"):
    """Render an inline SVG string to a QIcon tinted `color`. Neutral gray by
    default so it reads on both themes without re-tinting."""
    from PyQt5.QtGui import QIcon, QPixmap, QPainter
    try:
        from PyQt5.QtSvg import QSvgRenderer
    except Exception:  # noqa: BLE001
        return QIcon()
    r = QSvgRenderer(bytearray(svg.replace("currentColor", color), encoding="utf-8"))
    pm = QPixmap(size, size); pm.fill(Qt.transparent)
    p = QPainter(pm); r.render(p); p.end()
    return QIcon(pm)


# ── retheme registry (colour-bearing widgets) ────────────────────────────────
_RESTYLERS: List[Callable[[], None]] = []


def register_restyle(fn: Callable[[], None]) -> None:
    _RESTYLERS.append(fn)
    fn()


def restyle_all() -> None:
    for fn in list(_RESTYLERS):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass


# ── text ─────────────────────────────────────────────────────────────────────
def eyebrow(text: str) -> QLabel:
    """The single ALL-CAPS structural label tier."""
    lab = QLabel(text.upper())
    lab.setFont(T.ui_font(8.5, semibold=True))

    def style():
        f = lab.font(); f.setLetterSpacing(f.PercentageSpacing, 106); lab.setFont(f)
        lab.setStyleSheet(f"color:{T.t('txt3')};background:transparent;")
    register_restyle(style)
    return lab


def page_title(text: str) -> QLabel:
    lab = QLabel(text)
    f = T.ui_font(15, semibold=True)
    lab.setFont(f)
    register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    return lab


def body(text: str, dim: bool = False, mono: bool = False, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    lab.setFont(T.mono_font(9.5) if mono else T.ui_font(10))
    if wrap:
        lab.setWordWrap(True)
    register_restyle(lambda: lab.setStyleSheet(
        f"color:{T.t('txt3') if dim else T.t('txt1')};background:transparent;"))
    return lab


# ── small pills: tag (status), token (code name), net_token (code + category) ─
def tag(text: str, kind: str = "mut") -> QLabel:
    """Status pill. kind in {ok, warn, err, mut}."""
    lab = QLabel(text)
    lab.setFont(T.ui_font(8.5, semibold=True))
    lab.setAlignment(Qt.AlignCenter)
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    def style():
        if kind == "mut":
            bg, fg = T.t("tok"), T.t("txt3")
        else:
            bg, fg = T.t(f"{kind}_bg"), T.t(kind)
        lab.setStyleSheet(f"background:{bg};color:{fg};border-radius:9px;padding:2px 9px;")
    register_restyle(style)
    return lab


def token(text: str, dim: bool = False) -> QLabel:
    """A machine identifier (refdes, terminal, path) in a subtle chip. For INLINE
    use (inspector, callouts); table identifier columns use plain mono text."""
    lab = QLabel(text)
    lab.setFont(T.mono_font(9))
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    register_restyle(lambda: lab.setStyleSheet(
        f"background:{T.t('tok')};color:{T.t('txt3') if dim else T.t('txt1')};"
        f"border-radius:4px;padding:2px 7px;"))     # 2px vertical so descenders (y, g, p) never clip
    return lab


def net_token(text: str, cat: str) -> QWidget:
    """A net name: category dot + category-coloured mono, in a subtle chip."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(8, 2, 9, 2)
    lay.setSpacing(6)
    dot = QLabel()
    dot.setFixedSize(7, 7)
    name = QLabel(text)
    name.setFont(T.mono_font(9, semibold=True))
    lay.addWidget(dot)
    lay.addWidget(name)

    def style():
        col = T.category(cat)
        dot.setStyleSheet(f"background:{col};border-radius:3px;")
        name.setStyleSheet(f"color:{col};background:transparent;")
        w.setStyleSheet(f"background:{T.t('tok')};border-radius:4px;")
    register_restyle(style)
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return w


# ── buttons ──────────────────────────────────────────────────────────────────
def btn(text: str, kind: str = "default", tip: str = "", on_click: Optional[Callable] = None) -> QPushButton:
    """kind in {default, primary, ghost}. Every interactive control gets a tooltip."""
    b = QPushButton(text)
    if kind != "default":
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(32)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda: on_click())
    return b


def token_button(text: str, on_click: Callable, tip: str = "") -> QPushButton:
    """A clickable machine identifier (e.g. an MCU part number) styled like a token."""
    b = QPushButton(text)
    b.setObjectName("tokbtn")
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    b.clicked.connect(lambda: on_click(text))
    return b


class Segmented(QWidget):
    """A mono segmented control (package selector, profile switch, ...)."""

    def __init__(self, options: Sequence[str], on_change: Optional[Callable[[str], None]] = None,
                 selected: int = 0, tip: str = "", parent=None):
        super().__init__(parent)
        self._on_change = on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._buttons: List[QPushButton] = []
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setObjectName("seg")
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("selected", i == selected)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=i: self._pick(k))
            lay.addWidget(b)
            self._buttons.append(b)
        register_restyle(self._style)

    def _style(self):
        self.setStyleSheet(f"QWidget{{background:{T.t('ctl')};border:1px solid {T.t('stroke')};"
                           f"border-radius:4px;}}")

    def _pick(self, k: int):
        for i, b in enumerate(self._buttons):
            b.setProperty("selected", i == k)
            b.style().unpolish(b); b.style().polish(b)
        if self._on_change:
            self._on_change(self._buttons[k].text())


# ── surfaces ─────────────────────────────────────────────────────────────────
class Card(QFrame):
    """One elevation step: a rounded surface, no heavy border. Content via .body."""

    def __init__(self, pad: int = 16, parent=None):
        super().__init__(parent)
        self.setObjectName("ndcard")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(pad, pad, pad, pad)
        self.body.setSpacing(10)
        register_restyle(self._style)

    def _style(self):
        # scope to #ndcard: QLabel subclasses QFrame, so a bare QFrame{} rule would
        # cascade a border onto every label inside the card.
        self.setStyleSheet(f"QFrame#ndcard{{background:{T.t('card')};border:1px solid {T.t('stroke')};"
                           f"border-radius:8px;}}")


def hstack(*widgets, spacing: int = 10, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


# ── verdict / InfoBar ────────────────────────────────────────────────────────
class Verdict(QFrame):
    """The buildability / status bar: icon dot, title + subtitle, chips on the right."""

    def __init__(self, title: str, subtitle: str = "", kind: str = "ok",
                 chips: Optional[Sequence[Tuple[str, str, str]]] = None, plain: bool = False, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._plain = plain
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(1)
        self._title = QLabel(title); self._title.setFont(T.ui_font(10, semibold=True))
        text.addWidget(self._title)
        self._sub = None
        if subtitle:
            self._sub = QLabel(subtitle); self._sub.setFont(T.ui_font(9)); self._sub.setWordWrap(True)
            text.addWidget(self._sub)
        lay.addLayout(text)
        lay.addStretch(1)
        for label, value, dotkind in (chips or ()):
            lay.addWidget(self._chip(label, value, dotkind))
        register_restyle(self._style)

    def _chip(self, label: str, value: str, dotkind: str) -> QWidget:
        w = QFrame(); w.setObjectName("ndchip")
        h = QHBoxLayout(w); h.setContentsMargins(11, 4, 12, 4); h.setSpacing(7)
        dot = QLabel(); dot.setFixedSize(7, 7)
        lab = QLabel(label); lab.setFont(T.ui_font(9))
        h.addWidget(dot); h.addWidget(lab)
        if value:
            val = QLabel(value); val.setFont(T.ui_font(9, semibold=True)); h.addWidget(val)

        def style():
            colmap = {"ok": T.t("ok"), "warn": T.t("warn"), "err": T.t("err")}
            dot.setStyleSheet(f"background:{colmap.get(dotkind, T.t('txt3'))};border-radius:3px;")
            lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
            w.setStyleSheet(f"QFrame#ndchip{{background:{T.t('card')};border:1px solid {T.t('stroke')};"
                            f"border-radius:14px;}}")
        register_restyle(style)
        return w

    def _style(self):
        if self._plain:
            bg = T.t("card")
        else:
            bg = T.t(f"{self._kind}_bg")
        self.setObjectName("ndverdict")
        self.setStyleSheet(f"QFrame#ndverdict{{background:{bg};border:1px solid {T.t('stroke')};border-radius:8px;}}")
        self._title.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
        if self._sub is not None:
            self._sub.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")


# ── definition list ──────────────────────────────────────────────────────────
def dl(pairs: Sequence[Tuple[str, QWidget]], key_width: int = 136) -> QWidget:
    """Two-column key/value list. Value is any widget (label, token, tag)."""
    w = QWidget()
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(12)
    grid.setColumnMinimumWidth(0, key_width)
    for r, (k, v) in enumerate(pairs):
        key = QLabel(k); key.setFont(T.ui_font(10))
        register_restyle(lambda key=key: key.setStyleSheet(f"color:{T.t('txt2')};background:transparent;"))
        grid.addWidget(key, r, 0, Qt.AlignTop)
        if isinstance(v, str):
            v = body(v)
        grid.addWidget(v, r, 1, Qt.AlignTop)
    grid.setColumnStretch(1, 1)
    return w


# ── data table ───────────────────────────────────────────────────────────────
def data_table(columns: Sequence[str], rows: Sequence[Sequence], stretch_col: int = 0) -> QTableWidget:
    """A dense, borderless-feeling table. Cells may be str or QWidget."""
    tbl = QTableWidget(len(rows), len(columns))
    tbl.setHorizontalHeaderLabels([c.upper() for c in columns])
    tbl.verticalHeader().hide()
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SingleSelection)
    tbl.setShowGrid(False)
    tbl.setWordWrap(False)
    tbl.horizontalHeader().setHighlightSections(False)
    tbl.verticalHeader().setDefaultSectionSize(34)
    for r, row in enumerate(rows):
        for cidx, cell in enumerate(row):
            if isinstance(cell, QWidget):
                tbl.setCellWidget(r, cidx, cell)
            else:
                it = QTableWidgetItem(str(cell))
                tbl.setItem(r, cidx, it)
    # Column sizing: Qt's ResizeToContents ignores cell *widgets*, so a column of
    # tokens/tags collapses. Measure both items and cell-widget hints ourselves.
    hdr = tbl.horizontalHeader()
    fm = tbl.fontMetrics()
    def _text_w(s):
        return fm.horizontalAdvance(s) if hasattr(fm, "horizontalAdvance") else fm.width(s)
    for c in range(len(columns)):
        if c == stretch_col:
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
            continue
        hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        wmax = _text_w(columns[c].upper()) + 24
        for r in range(len(rows)):
            cw = tbl.cellWidget(r, c)
            if cw is not None:
                wmax = max(wmax, cw.sizeHint().width())
            else:
                it = tbl.item(r, c)
                if it is not None:
                    wmax = max(wmax, _text_w(it.text()) + 24)
        tbl.setColumnWidth(c, wmax + 28)
    return tbl


# ── the modular sub-feature host ─────────────────────────────────────────────
Panel = Tuple[str, Callable]   # (title, builder(ctx)->QWidget)


class Workspace(QWidget):
    """A feature's root: a page title + optional header controls, a sub-tab bar
    built from a list of Panels, and a lazily-built stacked content area.

    Panels are list-driven: add a (title, builder) tuple to add a sub-feature,
    remove it to drop one. The shell knows nothing about panels."""

    def __init__(self, ctx, title: str, panels: Sequence[Panel],
                 header: Optional[QWidget] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("workspace")
        self._ctx = ctx
        self._panels = list(panels)
        self._built = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(24, 16, 24, 8)
        head.setSpacing(16)
        head.addWidget(page_title(title))
        if header is not None:
            head.addStretch(1)
            head.addWidget(header)
        else:
            head.addStretch(1)
        root.addLayout(head)

        bar = QHBoxLayout()
        bar.setContentsMargins(24, 0, 24, 0)
        bar.setSpacing(2)
        self._tabs: List[QPushButton] = []
        for i, (name, _) in enumerate(self._panels):
            b = QPushButton(name)
            b.setObjectName("subtab")
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("selected", i == 0)
            b.clicked.connect(lambda _=False, k=i: self._select(k))
            bar.addWidget(b)
            self._tabs.append(b)
        bar.addStretch(1)
        root.addLayout(bar)

        rule = QFrame(); rule.setFixedHeight(1)
        register_restyle(lambda: rule.setStyleSheet(f"background:{T.t('divider')};border:none;"))
        root.addWidget(rule)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        for _ in self._panels:
            self._stack.addWidget(QWidget())   # placeholders, built on first show
        self._select(0)

    def select_panel(self, title: str):
        """Switch to a sub-panel by its title (used for cross-panel navigation)."""
        for i, (name, _) in enumerate(self._panels):
            if name == title:
                self._select(i)
                return

    def _select(self, k: int):
        for i, b in enumerate(self._tabs):
            b.setProperty("selected", i == k)
            b.style().unpolish(b); b.style().polish(b)
        if k not in self._built:
            try:
                w = self._panels[k][1](self._ctx)
            except Exception as e:  # noqa: BLE001
                w = _error_panel(self._panels[k][0], e)
            old = self._stack.widget(k)
            self._stack.removeWidget(old); old.deleteLater()
            self._stack.insertWidget(k, w)
            self._built[k] = w
        self._stack.setCurrentIndex(k)


def scroll_body(widget: QWidget) -> QWidget:
    """Wrap a panel body in a vertical scroll area with the standard page padding."""
    from PyQt5.QtWidgets import QScrollArea
    holder = QWidget()
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(24, 16, 24, 24)
    lay.setSpacing(14)
    lay.addWidget(widget)
    lay.addStretch(1)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(holder)
    return area


def _error_panel(name: str, err: Exception) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 24, 24, 24)
    lay.addWidget(eyebrow(f"{name} Unavailable"))
    lay.addWidget(body(str(err), dim=True))
    lay.addStretch(1)
    return w
