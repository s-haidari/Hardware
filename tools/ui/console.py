"""ui.console — the shell's persistent Activity console.

The styled shell only had a transient 6-second statusBar line, so a run's ▶/✓/✗
progress stream and any errors scrolled away and were unrecoverable. This is the
one durable log surface (the one genuinely-good chrome idea carried over from the
legacy bare UI): a read-only mono panel pinned to the bottom of the content area,
collapsible to a header bar so it never steals space when idle.

It is styled centrally by object name in theme.qss (#activityConsole / #consoleLog /
#consoleChevron) so it retints on a theme toggle via the shell's setStyleSheet(qss())
re-apply; the header labels use the W.* helpers (built ONCE at shell lifetime, so their
owner-tracked restylers never churn).
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QPlainTextEdit)

from . import theme as T
from . import widgets as W

_LOG_HEIGHT = 156          # expanded log viewport height (px)
_MAX_LINES = 5000          # ring-buffer cap so a long session never grows unbounded


class ActivityConsole(QWidget):
    """A persistent, collapsible activity/log panel. append() adds one line; the
    chevron toggles the log body (the header bar always stays as the affordance)."""

    def __init__(self, on_toggle=None, parent=None):
        super().__init__(parent)
        self.setObjectName("activityConsole")
        self._on_toggle = on_toggle
        self._count = 0
        self._expanded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(T.sp("page"), T.sp("sm"), T.sp("page"), T.sp("sm"))
        root.setSpacing(T.sp("sm"))

        # ── header bar (always visible — the affordance) ──────────────────────
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(T.sp("md"))
        self._eyebrow = W.eyebrow("Activity")
        self._count_lbl = W.body("", dim=True)
        self._chevron = QPushButton("▸")
        self._chevron.setObjectName("consoleChevron")
        self._chevron.setCursor(Qt.PointingHandCursor)
        self._chevron.setFixedSize(24, 24)
        self._chevron.setToolTip("Show the activity log")
        self._chevron.clicked.connect(self.toggle)
        self._clear = W.btn("Clear", "ghost", "Clear the activity log", on_click=self.clear)
        self._clear.setVisible(False)                 # only meaningful when expanded
        head.addWidget(self._chevron)
        head.addWidget(self._eyebrow)
        head.addWidget(self._count_lbl)
        head.addStretch(1)
        head.addWidget(self._clear)
        # let the whole header toggle too (bigger hit target than the 24px chevron)
        head_w = QWidget()
        head_w.setLayout(head)
        root.addWidget(head_w)

        # ── log body (hidden until expanded) ──────────────────────────────────
        self._log = QPlainTextEdit()
        self._log.setObjectName("consoleLog")
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(_MAX_LINES)
        self._log.setFont(T.mono_font(9))
        self._log.setFixedHeight(_LOG_HEIGHT)
        self._log.setPlaceholderText("Progress and errors from actions appear here.")
        self._log.setVisible(False)
        root.addWidget(self._log)

    # ── public API ────────────────────────────────────────────────────────────
    def append(self, msg: str):
        """Add one line to the log (newest at the bottom) and keep the view pinned to
        the tail. Updates the header count so a collapsed console still shows activity."""
        text = str(msg)
        if not text:
            return
        self._log.appendPlainText(text)
        self._count += 1
        self._count_lbl.setText(f"{self._count}")
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self):
        self._log.clear()
        self._count = 0
        self._count_lbl.setText("")

    def set_expanded(self, expanded: bool, *, notify: bool = True):
        """Show/hide the log body. `notify=False` seeds the state (e.g. from the
        persisted preference) WITHOUT firing on_toggle back into a persist write."""
        self._expanded = bool(expanded)
        self._log.setVisible(self._expanded)
        self._clear.setVisible(self._expanded)
        self._chevron.setText("▾" if self._expanded else "▸")
        self._chevron.setToolTip("Hide the activity log" if self._expanded else "Show the activity log")
        if notify and callable(self._on_toggle):
            self._on_toggle(self._expanded)

    def toggle(self):
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded
