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

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                             QVBoxLayout, QPushButton, QStackedWidget, QFrame, QLabel)

from . import theme as T
from . import widgets as W
from . import feature as F

WINDOW_TITLE = "NETDECK Firmware Extraction Bench"

_ICON = {
    "ham": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M2 4h12M2 8h12M2 12h12"/></svg>',
    "bench": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="3.5" y="3.5" width="9" height="9" rx="1"/><path d="M6 1.5v2M10 1.5v2M6 12.5v2M10 12.5v2M1.5 6h2M1.5 10h2M12.5 6h2M12.5 10h2"/></svg>',
    "library": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="2.5" y="4" width="2.4" height="9" rx="0.5"/><rect x="5.6" y="2.8" width="2.4" height="10.2" rx="0.5"/><rect x="8.7" y="5" width="2.4" height="8" rx="0.5"/><path d="M1.6 13.2h12.8"/></svg>',
    "projects": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M2 4.5H6.5L8 6.5H14V12.5H2Z"/></svg>',
    "settings": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M13 3l-1.4 1.4M4.4 11.6 3 13"/></svg>',
    "theme": '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M13.5 9A5.5 5.5 0 0 1 7 2.5 5.5 5.5 0 1 0 13.5 9Z"/></svg>',
}


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
    def __init__(self, text: str, icon, on_click):
        super().__init__(text)
        self._label = text
        self.setObjectName("navItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setCheckable(False)
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(18, 18))
        self.clicked.connect(on_click)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self); self.style().polish(self)

    def collapse(self, collapsed: bool):
        self.setText("" if collapsed else self._label)
        self.setToolTip(self._label if collapsed else "")


# ── update signals (marshal background results back to the GUI thread) ────────
class _UpdateSignals(QObject):
    found = pyqtSignal(object)     # an update descriptor dict
    none = pyqtSignal(str)         # a "you're up to date" message (manual checks only)


class _DownloadSignals(QObject):
    progress = pyqtSignal(int, int)   # (bytes_done, bytes_total)
    done = pyqtSignal(bool)           # success


class NetdeckShell(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self._dark = True
        self._nav_collapsed = False
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

        # updates: a Settings button (or launch) asks; results marshal back to the GUI
        self._upd = _UpdateSignals()
        self._upd.found.connect(self._on_update_available)
        self._upd.none.connect(lambda msg: self._info("Up To Date", msg))
        self.ctx.bus.on("app.check_updates", lambda *_a: self.check_for_updates(manual=True))

    # -- nav --
    def _build_nav(self) -> QWidget:
        pane = QWidget(); pane.setObjectName("navPane"); pane.setFixedWidth(236)
        self._nav_lay = QVBoxLayout(pane)
        self._nav_lay.setContentsMargins(8, 10, 8, 8)
        self._nav_lay.setSpacing(2)
        ham = QPushButton(); ham.setObjectName("navItem"); ham.setMinimumHeight(38)
        ham.setCursor(Qt.PointingHandCursor); ham.setIcon(W.svg_icon(_ICON["ham"])); ham.setIconSize(QSize(18, 18))
        ham.setToolTip("Collapse or expand the navigation")
        ham.clicked.connect(self._toggle_nav)
        self._nav_lay.addWidget(ham)
        return pane

    def _build_pages(self):
        lay = self._nav_lay
        main = [f for f in self._features if f.id != "settings"]
        settings = [f for f in self._features if f.id == "settings"]
        ordered = main + settings

        self._eyebrow = W.eyebrow("Workspaces")
        lay.addWidget(self._eyebrow)
        self._nav_items = []
        self._page_specs = []          # [feature, built?] — pages build lazily on first nav
        for idx, feat in enumerate(ordered):
            self._stack.addWidget(QWidget())
            self._page_specs.append([feat, False])
            item = NavItem(feat.title, W.svg_icon(_ICON.get(feat.id, "")),
                           lambda _=False, k=idx: self._select(k))
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
        self._theme_btn.setIcon(W.svg_icon(_ICON["theme"]))
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.setToolTip("Switch between the dark and light Windows themes")
        self._theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self._theme_btn)
        for idx, item in self._foot_items:
            lay.addWidget(item)

    def _toggle_nav(self):
        self._nav_collapsed = not self._nav_collapsed
        self._nav.setFixedWidth(56 if self._nav_collapsed else 236)
        self._eyebrow.setVisible(not self._nav_collapsed)
        for it in self._nav_items:
            it.collapse(self._nav_collapsed)
        self._theme_btn.setText("" if self._nav_collapsed else ("Dark Theme" if self._dark else "Light Theme"))

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
        try:                                 # keep component previews on the app surface
            import fp_render as R
            R.set_render_theme(dark, T.t("inset"))
        except Exception:  # noqa: BLE001
            pass
        self._apply_palette()               # so unstyled surfaces (scroll viewports) match
        self.setStyleSheet(T.qss(dark))
        W.restyle_all()
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText("" if self._nav_collapsed else ("Dark Theme" if dark else "Light Theme"))

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

    # -- updates --
    def check_for_updates(self, manual: bool = False):
        """Look for a newer release in the background. Auto-checks only run on a frozen
        build (a source checkout never nags); a manual check runs anywhere so the flow
        is testable. Best-effort — network failures are silent unless `manual`."""
        if not manual and not getattr(sys, "frozen", False):
            return

        def worker():
            upd = None
            try:
                import nd_updater as U
                upd = U.check_for_update(allow_dev=manual)
            except Exception:  # noqa: BLE001
                upd = None
            if upd:
                self._upd.found.emit(upd)
            elif manual:
                try:
                    import nd_updater as U
                    self._upd.none.emit(f"You're on the latest version ({U.current_version()}).")
                except Exception:  # noqa: BLE001
                    self._upd.none.emit("Could not check for updates right now.")
        threading.Thread(target=worker, daemon=True).start()

    def _on_update_available(self, update: dict):
        from PyQt5.QtWidgets import QMessageBox
        import nd_updater as U
        box = QMessageBox(self)
        box.setWindowTitle("Update Available")
        box.setIcon(QMessageBox.Information)
        box.setText(f"KiCad Manager {update.get('version')} is available "
                    f"(you have {U.current_version()}).")
        notes = (update.get("notes") or "").strip()
        if notes:
            box.setInformativeText(notes[:500] + ("…" if len(notes) > 500 else ""))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("Update Now")
        box.button(QMessageBox.No).setText("Later")
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() == QMessageBox.Yes:
            self._download_and_apply(update)

    def _download_and_apply(self, update: dict):
        from PyQt5.QtWidgets import QProgressDialog, QMessageBox
        import nd_updater as U
        target = U.exe_path()
        if target is None:                       # dev build: nothing to swap
            self._info("Update", "Updates apply to the installed Windows app only; "
                                 "in a dev build there is no exe to replace.")
            return
        dest = U.staged_path(target)
        dlg = QProgressDialog("Downloading update…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Updating"); dlg.setWindowModality(Qt.WindowModal)
        dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.setMinimumDuration(0)
        cancelled = {"v": False}
        dlg.canceled.connect(lambda: cancelled.__setitem__("v", True))
        sig = _DownloadSignals()
        sig.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / t)) if t else None)

        def finish(ok: bool):
            dlg.close()
            if not ok:
                try:
                    dest.unlink()
                except Exception:  # noqa: BLE001
                    pass
                if not cancelled["v"]:
                    self._warn("Update Failed", "Could not download the update. "
                                                "Please try again later.")
                return
            if U.apply_update_windows(dest, target):
                QApplication.instance().quit()   # the detached helper swaps + relaunches
            else:
                self._info("Update Downloaded",
                           f"Saved to:\n{dest}\n\nClose the app and replace the exe with "
                           f"this file, then relaunch.")
        sig.done.connect(finish)

        def worker():
            ok = True
            try:
                def prog(d, t):
                    if cancelled["v"]:
                        raise RuntimeError("cancelled")
                    sig.progress.emit(d, t)
                U.download(update, dest, progress=prog)
            except Exception:  # noqa: BLE001
                ok = False
            sig.done.emit(ok and not cancelled["v"])
        threading.Thread(target=worker, daemon=True).start()

    def _info(self, title: str, msg: str):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, title, msg)

    def _warn(self, title: str, msg: str):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(self, title, msg)

    def _log(self, msg: str):
        self.statusBar().showMessage(str(msg), 6000)


def run():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts(app)
    import LibraryManager as LM
    # SP1: a frozen exe has no repo tree — resolve (and on first run, choose+seed)
    # the writable library location before anything reads config or paths.
    if getattr(sys, "frozen", False):
        loc = LM.ensure_library_location()
        if loc is None:
            return 0   # user quit the first-run chooser
        LM.apply_library_location(loc)
    cfg = LM.load_config()
    win = NetdeckShell(cfg)
    win.show()
    win.check_for_updates()          # frozen builds only; silent if none / offline
    return app.exec_()
