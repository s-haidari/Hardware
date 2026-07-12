"""In-app subpage navigation (the "no new OS windows" framework).

The shell hosts an editor / manager / picker as a pushed, Back-navigable subpage over the
content area instead of a modal OS window. These lock the push / pop / resolve contract:
the outer content stack swaps to the subpage host, a QDialog resolves through finished()
so on_result carries the real accept/reject outcome, nesting Backs one level at a time,
and the kit.open_subpage bus helper drives it end-to-end."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _shell():
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    _app()
    return NetdeckShell(LM.load_config())


def test_push_shows_host_and_back_returns_to_workspaces():
    from PyQt5.QtWidgets import QWidget
    win = _shell()
    try:
        assert win._content_stack.currentWidget() is win._stack   # starts on the workspaces
        seen = []
        body = QWidget()
        win.push_subpage(body, "Manage Something", on_result=lambda r: seen.append(r))
        assert win._content_stack.currentWidget() is win._subpage_host
        assert win._subpage_title.text() == "Manage Something"
        assert len(win._subpages) == 1
        win._pop_subpage()                                        # Back
        assert win._content_stack.currentWidget() is win._stack
        assert win._subpages == []
        assert seen == [None]                                     # plain widget → result None
    finally:
        win.close()


def test_qdialog_accept_resolves_with_result():
    from PyQt5.QtWidgets import QDialog
    win = _shell()
    try:
        seen = []
        dlg = QDialog()
        win.push_subpage(dlg, "Autofill Preview", on_result=lambda r: seen.append(r))
        assert win._content_stack.currentWidget() is win._subpage_host
        dlg.accept()                                              # OK → finished(Accepted)
        assert seen == [QDialog.Accepted]
        assert win._subpages == []
        assert win._content_stack.currentWidget() is win._stack
    finally:
        win.close()


def test_qdialog_back_rejects_and_double_finished_is_noop():
    from PyQt5.QtWidgets import QDialog
    win = _shell()
    try:
        seen = []
        dlg = QDialog()
        win.push_subpage(dlg, "Search", on_result=lambda r: seen.append(r))
        win._pop_subpage()                                        # Back → reject
        assert seen == [QDialog.Rejected]
        dlg.reject()                                              # second finished → entry gone
        assert seen == [QDialog.Rejected]                         # not double-called
    finally:
        win.close()


def test_nesting_backs_one_level_at_a_time():
    from PyQt5.QtWidgets import QWidget
    win = _shell()
    try:
        a, b = QWidget(), QWidget()
        win.push_subpage(a, "A")
        win.push_subpage(b, "B")
        assert len(win._subpages) == 2
        assert win._subpage_title.text() == "B"
        assert win._subpage_body.currentWidget() is b
        win._pop_subpage()                                        # B → back to A
        assert len(win._subpages) == 1
        assert win._subpage_title.text() == "A"
        assert win._subpage_body.currentWidget() is a
        assert win._content_stack.currentWidget() is win._subpage_host
        win._pop_subpage()                                        # A → workspaces
        assert win._subpages == []
        assert win._content_stack.currentWidget() is win._stack
    finally:
        win.close()


def test_escape_pops_the_top_subpage():
    from PyQt5.QtWidgets import QWidget
    from PyQt5.QtGui import QKeyEvent
    from PyQt5.QtCore import QEvent, Qt
    win = _shell()
    try:
        win.push_subpage(QWidget(), "Z")
        win.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        assert win._subpages == []
        assert win._content_stack.currentWidget() is win._stack
    finally:
        win.close()


def test_open_subpage_bus_helper_drives_the_shell_end_to_end():
    from PyQt5.QtWidgets import QDialog
    from ui import kit
    win = _shell()
    try:
        seen = []
        dlg = QDialog()
        ok = kit.open_subpage(win.ctx, dlg, "Fabrication Presets",
                              on_result=lambda r: seen.append(r))
        assert ok is True
        assert win._content_stack.currentWidget() is win._subpage_host
        assert win._subpage_title.text() == "Fabrication Presets"
        dlg.accept()
        assert seen == [QDialog.Accepted]
        assert win._content_stack.currentWidget() is win._stack
    finally:
        win.close()


def test_open_subpage_without_a_bus_is_a_safe_noop():
    from ui import kit
    from PyQt5.QtWidgets import QWidget

    class _NoBus:
        bus = None

    assert kit.open_subpage(_NoBus(), QWidget(), "x") is False
