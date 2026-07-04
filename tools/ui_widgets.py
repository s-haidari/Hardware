"""ui_widgets.py — the shared instrument kit every tab is built from.

The design language: no titled card frames. Regions separate by a SectionHeader
(a small uppercase letterspaced caption + a hairline rule that fills the width)
on the flat panel background. Sub-views switch through a left Rail. Package/op
stats read across a ReadoutBand — the bench-meter fascia. Actions live on a
Toolbar. Empty regions show an EmptyState. Everything is theme-aware through
restyle(); the owning tab calls restyle_all() on theme change.

Cards survive ONLY for genuinely liftable list content (a library row, a
net-class entry) — never as a frame around a whole region."""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFrame, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy,
)

import ui_theme

# One type scale for the whole app (points).
FS_VALUE = 12.0     # readout digits, primary numbers
FS_BODY = 9.0       # body text
FS_LABEL = 6.5      # uppercase section captions / readout labels
FS_TITLE = 11.0     # a screen or region's largest label

_UI = ui_theme.UI_FONT_STACK[0]
_MONO = ui_theme.MONO_FONT_STACK[0]


def _font(family, pt, *, bold=False, demibold=False, spacing=0.0):
    f = QFont(family)
    f.setPointSizeF(pt)
    if demibold:
        f.setWeight(QFont.DemiBold)
    elif bold:
        f.setBold(True)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


# ─────────────────────────────────────────────────────────────────────────────
# SectionHeader — the de-box primitive. Caption + hairline rule, no frame.
# ─────────────────────────────────────────────────────────────────────────────
class SectionHeader(QWidget):
    """A small uppercase caption followed by a hairline rule filling the width.
    Replaces titled card frames — the region below it is flat panel."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 4)
        lay.setSpacing(10)
        self._label = QLabel(text.upper())
        self._label.setFont(_font(_UI, FS_LABEL, bold=True, spacing=1.1))
        self._rule = QFrame()
        self._rule.setFixedHeight(1)
        self._rule.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._right = QWidget()
        self._right_lay = QHBoxLayout(self._right)
        self._right_lay.setContentsMargins(0, 0, 0, 0)
        self._right_lay.setSpacing(6)
        lay.addWidget(self._label)
        lay.addWidget(self._rule, 1)
        lay.addWidget(self._right)
        self.restyle()

    def set_text(self, text: str):
        self._label.setText(text.upper())

    def add_right(self, w: QWidget):
        """Dock a small widget at the right end of the rule (e.g. a count)."""
        self._right_lay.addWidget(w)

    def restyle(self):
        self._label.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};background:transparent;")
        self._rule.setStyleSheet(f"background:{ui_theme.tc('BORDER')};border:none;")


# ─────────────────────────────────────────────────────────────────────────────
# Rail — the single left sub-navigation idiom.
# ─────────────────────────────────────────────────────────────────────────────
class _RailItem(QPushButton):
    def __init__(self, key, label, parent=None):
        super().__init__(label, parent)
        self.key = key
        self.setObjectName("railItem")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(30)
        self.setFont(_font(_UI, FS_BODY, demibold=True))


class Rail(QWidget):
    """A vertical left navigation rail. items = [(key, label), ...]; the active
    item carries a red left-accent bar and a filled row. Grouped optionally via
    add_group(caption). Emits selected(key)."""
    selected = pyqtSignal(str)

    def __init__(self, width: int = 168, parent=None):
        super().__init__(parent)
        self.setFixedWidth(width)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        self._items = {}
        self._current = None
        self._lay.addStretch(1)

    def add_group(self, caption: str):
        hdr = SectionHeader(caption)
        # a little breathing room above each group but the first
        self._lay.insertWidget(self._lay.count() - 1, _spacer(8) if self._items else _spacer(0))
        self._lay.insertWidget(self._lay.count() - 1, hdr)
        return hdr

    def add_item(self, key: str, label: str):
        btn = _RailItem(key, label)
        btn.clicked.connect(lambda _=False, k=key: self.select(k))
        self._items[key] = btn
        self._lay.insertWidget(self._lay.count() - 1, btn)
        if self._current is None:
            self.select(key)
        return btn

    def select(self, key: str):
        if key not in self._items:
            return
        self._current = key
        for k, b in self._items.items():
            b.setChecked(k == key)
        self.selected.emit(key)

    def current(self):
        return self._current

    def restyle(self):
        pass   # styled by app QSS (#railItem rules)


# ─────────────────────────────────────────────────────────────────────────────
# ReadoutBand — the bench-meter fascia. Hairline-ruled stats.
# ─────────────────────────────────────────────────────────────────────────────
class _Readout(QFrame):
    def __init__(self, label, accent=None, parent=None):
        super().__init__(parent)
        self._accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 2, 15, 4)
        lay.setSpacing(4)
        self._v = QLabel("–")
        self._v.setFont(_font(_MONO, FS_VALUE, demibold=True))
        # a small type-coloured DOT before the label (never an underline)
        lrow = QHBoxLayout()
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(6)
        self._dot = QFrame()
        self._dot.setFixedSize(7, 7)
        self._l = QLabel(label.upper())
        self._l.setFont(_font(_UI, FS_LABEL, bold=True, spacing=1.1))
        lrow.addWidget(self._dot)
        lrow.addWidget(self._l)
        lrow.addStretch(1)
        lay.addWidget(self._v)
        lay.addLayout(lrow)
        self.restyle()

    def set_value(self, v):
        self._v.setText(str(v))

    def restyle(self):
        self.setStyleSheet("background:transparent;")
        self._v.setStyleSheet(f"color:{ui_theme.tc('FG')};")
        dot = self._accent or ui_theme.tc("DOT_IDLE")
        self._dot.setStyleSheet(f"background:{dot};border:none;border-radius:3px;")
        self._l.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")


class ReadoutBand(QFrame):
    """A row of instrument readouts: an identity block (name + mono sub-line) then
    hairline-separated stats. specs = [(key, label, accent_or_None), ...]."""

    def __init__(self, specs, parent=None):
        super().__init__(parent)
        self.setObjectName("readoutBand")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(0)
        self.name = QLabel("")
        self.name.setFont(_font(_UI, 15.0, demibold=True))
        self.sub = QLabel("")
        self.sub.setFont(_font(_MONO, 8.0))
        idbox = QVBoxLayout()
        idbox.setContentsMargins(2, 2, 18, 2)
        idbox.setSpacing(0)
        idbox.addWidget(self.name)
        idbox.addWidget(self.sub)
        lay.addLayout(idbox)
        self._stats = {}
        self._seps = []
        for key, label, accent in specs:
            sep = QFrame()
            sep.setFixedWidth(1)
            self._seps.append(sep)
            lay.addWidget(sep)
            r = _Readout(label, accent)
            self._stats[key] = r
            lay.addWidget(r)
        lay.addStretch(1)
        self.restyle()

    def set_identity(self, name: str, sub: str = ""):
        self.name.setText(name)
        self.sub.setText(sub)

    def set(self, key, value):
        if key in self._stats:
            self._stats[key].set_value(value)

    def restyle(self):
        self.setStyleSheet(
            f"#readoutBand{{background:transparent;border:none;"
            f"border-bottom:1px solid {ui_theme.tc('BORDER')};}}")
        self.name.setStyleSheet(f"color:{ui_theme.tc('FG')};")
        self.sub.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
        for sep in self._seps:
            sep.setStyleSheet(f"background:{ui_theme.tc('BORDER')};border:none;")
        for r in self._stats.values():
            r.restyle()


# ─────────────────────────────────────────────────────────────────────────────
# Toolbar — a flat action row (actions dock here; never stranded).
# ─────────────────────────────────────────────────────────────────────────────
def toolbar_row(spacing: int = 6) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    return lay


def button(text: str, kind: str = "default", icon=None) -> QPushButton:
    """A standardized button. kind: 'primary' (accent outline), 'default'
    (hairline), 'ghost' (borderless toolbar)."""
    b = QPushButton(text)
    b.setObjectName({"primary": "btnPrimary", "ghost": "btnGhost"}.get(kind, "btnDefault"))
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(30)
    b.setFont(_font(_UI, FS_BODY, demibold=True))
    if icon is not None:
        b.setIcon(icon)
    return b


# ─────────────────────────────────────────────────────────────────────────────
# EmptyState — centered guidance for empty regions.
# ─────────────────────────────────────────────────────────────────────────────
class EmptyState(QWidget):
    """Centered title + hint for an empty table / preview / list."""

    def __init__(self, title: str, hint: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(4)
        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setFont(_font(_UI, FS_TITLE, demibold=True))
        self._hint = QLabel(hint)
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setFont(_font(_UI, FS_BODY))
        lay.addWidget(self._title)
        if hint:
            lay.addWidget(self._hint)
        self.restyle()

    def set(self, title: str, hint: str = ""):
        self._title.setText(title)
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))

    def restyle(self):
        self._title.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};background:transparent;")
        self._hint.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};background:transparent;")


def _spacer(h: int) -> QWidget:
    w = QWidget()
    w.setFixedHeight(h)
    return w


def restyle_all(*widgets):
    """Call restyle() on every kit widget that has one (theme switch)."""
    for w in widgets:
        if w is not None and hasattr(w, "restyle"):
            w.restyle()


# ── Back-compat: the old CardWidget, kept only for genuinely liftable list
#    items. Regions must use SectionHeader instead. ───────────────────────────
class CardWidget(QFrame):
    """A titled card — retained for list-item content and the log panel. Regions
    use SectionHeader (the de-box primitive)."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("cardTitle")
        self.title_lbl.setFont(_font(_UI, FS_BODY, bold=True))
        # title row: label on the left, an optional widget slot on the right
        title_row = QWidget()
        tl = QHBoxLayout(title_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(6)
        if not title:
            self.title_lbl.setVisible(False)
        tl.addWidget(self.title_lbl)
        tl.addStretch()
        self._title_right = QWidget()
        self._title_right_layout = QHBoxLayout(self._title_right)
        self._title_right_layout.setContentsMargins(0, 0, 0, 0)
        self._title_right_layout.setSpacing(0)
        tl.addWidget(self._title_right)
        outer.addWidget(title_row)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 6, 8, 8)
        self.content_layout.setSpacing(6)
        outer.addWidget(self.content)

    def contentLayout(self):
        return self.content_layout

    def set_title_widget(self, widget: QWidget):
        """Place a widget on the right side of the title row (e.g. a tab bar)."""
        for i in reversed(range(self._title_right_layout.count())):
            item = self._title_right_layout.takeAt(i)
            w = item.widget()
            if w:
                w.setParent(None)
        self._title_right_layout.addWidget(widget)


def make_card(title: str):
    card = CardWidget(title)
    return card, card.contentLayout()
