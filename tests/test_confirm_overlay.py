"""The in-app modal overlay (kit._ModalOverlay / confirm_overlay / info_overlay).

Confirmations and info messages open as a scrim + centered card INSIDE the app window — a
child widget running a local event loop (synchronous like QMessageBox.exec_, so callers keep
`if confirm(...): ...`), never a new OS window. util.confirm routes here when a window is
present and stays headless-safe (auto-True under offscreen)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _win():
    from PyQt5.QtWidgets import QWidget
    w = QWidget()
    w.resize(800, 600)
    return w


def _drive_click(win, label):
    """Schedule a click on the overlay button labelled `label` once the nested loop starts,
    with a hard fallback that force-finishes the overlay so a test can never hang."""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QPushButton
    from ui import kit

    def click():
        for b in win.findChildren(QPushButton):
            if b.text() == label and b.isEnabled() and b.isVisible():
                b.click(); return

    def fallback():
        for o in win.findChildren(kit._ModalOverlay):
            if o.isVisible():
                o.finish(False)

    QTimer.singleShot(0, click)
    QTimer.singleShot(1500, fallback)


def test_modal_overlay_runs_local_loop_and_returns():
    from ui import kit
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QWidget
    _app()
    win = _win()
    ov = kit._ModalOverlay(win, QWidget())
    QTimer.singleShot(0, lambda: ov.finish(True))
    assert ov.run() is True


def test_modal_overlay_escape_cancels():
    from ui import kit
    from PyQt5.QtCore import QTimer, QEvent, Qt
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtWidgets import QWidget
    _app()
    win = _win()
    ov = kit._ModalOverlay(win, QWidget())
    QTimer.singleShot(0, lambda: ov.keyPressEvent(
        QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)))
    assert ov.run() is False


def test_confirm_overlay_confirm_and_cancel():
    from ui import kit
    _app()
    win = _win(); win.show()
    _drive_click(win, "Confirm")
    assert kit.confirm_overlay(win, "Delete Part", "Delete this part?") is True
    _drive_click(win, "Cancel")
    assert kit.confirm_overlay(win, "Delete Part", "Delete this part?") is False


def test_info_overlay_dismisses():
    from ui import kit
    _app()
    win = _win(); win.show()
    _drive_click(win, "OK")
    kit.info_overlay(win, "Done", "The operation finished.")   # returns None, must not hang


def test_util_confirm_headless_auto_proceeds():
    from ui import util
    assert util.confirm(None, "T", "m") is True                # offscreen → no user → proceed


def test_util_confirm_routes_to_overlay_when_windowed(monkeypatch):
    from ui import util
    _app()
    win = _win(); win.show()
    monkeypatch.setattr(util, "_headless", lambda: False)       # simulate a real desktop
    _drive_click(win, "Confirm")
    assert util.confirm(win, "Apply", "Apply the change?") is True
    _drive_click(win, "Cancel")
    assert util.confirm(win, "Apply", "Apply the change?", default_no=True) is False
