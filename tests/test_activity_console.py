"""Convergence Phase 0 · the shell's persistent Activity console (ui.console).

The styled shell only had a transient 6s statusBar; ui.console.ActivityConsole is the
durable log surface (the ▶/✓/✗ stream + errors), collapsible to a header bar. These
tests pin its behaviour + that the shell wires _log into it and persists the choice.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from ui.console import ActivityConsole  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def test_append_increments_count_and_adds_text():
    c = ActivityConsole()
    c.append("▶ do a thing")
    c.append("✓ done")
    c.append("")                                  # blank is ignored
    assert c._count == 2
    assert "do a thing" in c._log.toPlainText()
    assert "done" in c._log.toPlainText()


def test_clear_resets():
    c = ActivityConsole()
    c.append("x"); c.append("y")
    c.clear()
    assert c._count == 0
    assert c._log.toPlainText() == ""


def test_expand_collapse_toggles_the_log_body():
    # isHidden() reflects the explicit visibility flag regardless of whether the
    # top-level window is shown (isVisible() would be False for an unshown widget).
    c = ActivityConsole()
    assert c.is_expanded() is False               # collapsed by default
    assert c._log.isHidden() is True
    c.set_expanded(True)
    assert c.is_expanded() is True
    assert c._log.isHidden() is False
    assert c._clear.isHidden() is False           # Clear only shows when expanded
    c.toggle()
    assert c.is_expanded() is False
    assert c._log.isHidden() is True


def test_notify_flag_controls_on_toggle_callback():
    fired = []
    c = ActivityConsole(on_toggle=lambda e: fired.append(e))
    c.set_expanded(True, notify=False)            # seeding must NOT fire the callback
    assert fired == []
    c.set_expanded(False)                         # a real toggle fires it
    assert fired == [False]


def test_shell_routes_log_into_the_console_and_persists_toggle(monkeypatch):
    import LibraryManager as LM
    written = {}
    monkeypatch.setattr(LM, "read_setting",
                        lambda key, default=None, config_path=None: default)
    monkeypatch.setattr(LM, "write_setting",
                        lambda key, value, config_path=None: written.__setitem__(key, value) or True)
    from ui.shell import NetdeckShell
    win = NetdeckShell(LM.load_config())
    before = win._console._count
    win._log("▶ a run started")
    assert win._console._count == before + 1, "shell._log must feed the Activity console"
    # toggling the console persists the choice
    win._console.toggle()
    assert written.get("ConsoleExpanded") == win._console.is_expanded()
    win.close()
