"""ui_widgets.py — shared UI building blocks used by every tab, styled by the
app-wide QSS rules (QFrame#card / QLabel#cardTitle). One card implementation
instead of one per tab."""
from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QLabel, QWidget, QVBoxLayout, QHBoxLayout


class CardWidget(QFrame):
    """A titled card section: title row (with an optional right-side widget slot)
    over a content area. contentLayout() is where callers add their widgets."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("cardTitle")
        f = self.title_lbl.font()
        f.setPointSize(9)
        f.setBold(True)
        self.title_lbl.setFont(f)
        # Title area: label on left, optional widget on right (for tab bars etc.)
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        if not title:
            self.title_lbl.setVisible(False)
        title_layout.addWidget(self.title_lbl)
        title_layout.addStretch()
        # container for right-side title widgets
        self._title_right = QWidget()
        self._title_right_layout = QHBoxLayout(self._title_right)
        self._title_right_layout.setContentsMargins(0, 0, 0, 0)
        self._title_right_layout.setSpacing(0)
        title_layout.addWidget(self._title_right)
        outer.addWidget(title_container)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 6, 8, 8)
        self.content_layout.setSpacing(6)
        outer.addWidget(self.content)

    def contentLayout(self):
        return self.content_layout

    def set_title_widget(self, widget: QWidget):
        """Place a widget on the right side of the title area (e.g. tab bar)."""
        for i in reversed(range(self._title_right_layout.count())):
            item = self._title_right_layout.takeAt(i)
            w = item.widget()
            if w:
                w.setParent(None)
        self._title_right_layout.addWidget(widget)


def make_card(title: str):
    """A (card, body_layout) pair — the light-weight form the KiCad Tools tab
    uses. Same chrome as CardWidget, without the title-widget slot."""
    card = CardWidget(title)
    return card, card.contentLayout()
