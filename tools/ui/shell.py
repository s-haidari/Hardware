"""ui.shell — the NETDECK window.

A native-titlebar QMainWindow (robust Windows move/resize/snap; the title also
gives the live-validation harness a stable target). Everything below the title
bar is ours: a left nav built ENTIRELY from the feature registry, a content
stack, and a theme toggle. The shell hard-codes no feature.

Retheme is instant: set the tokens, re-apply the QSS, and call restyle_all() to
retint the colour-bearing widgets.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                             QVBoxLayout, QPushButton, QStackedWidget, QFrame, QLabel)

from . import theme as T
from . import widgets as W
from . import feature as F

WINDOW_TITLE = "NETDECK Firmware Extraction Bench"


# ── services (async + logging) available to every feature ────────────────────
class _Bridge(QObject):
    done = pyqtSignal(object, bool)


class Services:
    def __init__(self, log_fn):
        self._log = log_fn
        self._bridge = _Bridge()
        self._bridge.done.connect(lambda cb, ok: cb(ok) if callable(cb) else None)

    def log(self, msg: str):
        self._log(str(msg))

    def run_async(self, fn, ok: str = None, done_cb=None):
        def worker():
            success = True
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                success = False
                self._log(f"Error: {e}")
            else:
                if ok:
                    self._log(ok)
            self._bridge.done.emit(done_cb, success)
        threading.Thread(target=worker, daemon=True).start()


class NavItem(QPushButton):
    def __init__(self, text: str, on_click):
        super().__init__(text)
        self.setObjectName("navItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setCheckable(False)
        self.clicked.connect(on_click)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self); self.style().polish(self)


class NetdeckShell(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self._dark = True
        T.set_theme(True)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1440, 900)

        self.services = Services(self._log)
        self.ctx = F.Context(cfg=cfg, services=self.services, theme=T, bus=F.EventBus())

        # register features (importing the package runs the register() calls)
        from . import features  # noqa: F401
        self._features = F.features()

        root = QWidget(); root.setObjectName("shellRoot")
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._nav = self._build_nav()
        row.addWidget(self._nav)

        self._stack = QStackedWidget()
        self._stack.setObjectName("contentArea")
        row.addWidget(self._stack, 1)
        self.setCentralWidget(root)

        self._nav_items = []
        self._foot_items = []
        self._build_pages()
        self.apply_theme(True)
        self._select(0)

    # -- nav --
    def _build_nav(self) -> QWidget:
        pane = QWidget(); pane.setObjectName("navPane"); pane.setFixedWidth(236)
        self._nav_lay = QVBoxLayout(pane)
        self._nav_lay.setContentsMargins(8, 10, 8, 8)
        self._nav_lay.setSpacing(2)
        return pane

    def _build_pages(self):
        lay = self._nav_lay
        main = [f for f in self._features if f.id != "settings"]
        settings = [f for f in self._features if f.id == "settings"]
        ordered = main + settings

        lay.addWidget(W.eyebrow("Workspaces"))
        self._nav_items = []
        self._page_specs = []          # [feature, built?] — pages build lazily on first nav
        for idx, feat in enumerate(ordered):
            self._stack.addWidget(QWidget())
            self._page_specs.append([feat, False])
            item = NavItem(feat.title, lambda _=False, k=idx: self._select(k))
            if feat.id == "settings":
                self._foot_items.append((idx, item))
            else:
                lay.addWidget(item)
            self._nav_items.append(item)

        lay.addStretch(1)
        rule = QFrame(); rule.setFixedHeight(1)
        W.register_restyle(lambda: rule.setStyleSheet(f"background:{T.t('divider')};border:none;"))
        lay.addWidget(rule)

        self._theme_btn = QPushButton("Dark Theme")
        self._theme_btn.setObjectName("navItem")
        self._theme_btn.setMinimumHeight(38)
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setToolTip("Switch between the dark and light Windows themes")
        self._theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self._theme_btn)
        for idx, item in self._foot_items:
            lay.addWidget(item)

    def _safe_build(self, feat: F.Feature) -> QWidget:
        try:
            return feat.build(self.ctx)
        except Exception as e:  # noqa: BLE001
            w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(24, 24, 24, 24)
            v.addWidget(W.eyebrow(f"{feat.title} Failed To Load"))
            v.addWidget(W.body(str(e), dim=True)); v.addStretch(1)
            return w

    def _select(self, k: int):
        spec = self._page_specs[k]
        if not spec[1]:
            page = self._safe_build(spec[0])
            old = self._stack.widget(k)
            self._stack.removeWidget(old); old.deleteLater()
            self._stack.insertWidget(k, page)
            spec[1] = True
        self._stack.setCurrentIndex(k)
        for i, item in enumerate(self._nav_items):
            item.set_selected(i == k)

    # -- theme --
    def apply_theme(self, dark: bool):
        self._dark = dark
        T.set_theme(dark)
        self._apply_palette()               # so unstyled surfaces (scroll viewports) match
        self.setStyleSheet(T.qss(dark))
        W.restyle_all()
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText("Dark Theme" if dark else "Light Theme")

    @staticmethod
    def _apply_palette():
        """Theme the app QPalette so plain/unstyled Fusion widgets (QScrollArea
        viewports, holders) use the theme surface instead of the light default."""
        app = QApplication.instance()
        if app is None:
            return
        pal = QPalette()
        for role, key in ((QPalette.Window, "surface"), (QPalette.Base, "card"),
                          (QPalette.AlternateBase, "surface"), (QPalette.Text, "txt1"),
                          (QPalette.WindowText, "txt1"), (QPalette.Button, "card"),
                          (QPalette.ButtonText, "txt1"), (QPalette.Highlight, "accent"),
                          (QPalette.HighlightedText, "on_accent"), (QPalette.ToolTipBase, "card"),
                          (QPalette.ToolTipText, "txt1"), (QPalette.PlaceholderText, "txt3")):
            pal.setColor(role, T.qcolor(key))
        app.setPalette(pal)

    def _toggle_theme(self):
        self.apply_theme(not self._dark)

    def _log(self, msg: str):
        self.statusBar().showMessage(str(msg), 6000)


def run():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts(app)
    import LibraryManager as LM
    cfg = LM.load_config()
    win = NetdeckShell(cfg)
    win.show()
    return app.exec_()
