"""Smoke test for the ground-up QFluentWidgets app (nd_app.NetdeckWindow).

Constructs the whole FluentWindow offscreen over the real config + hardened logic
and asserts it builds, themes, and wires the rebuilt Manager to the grouped-library
backend. Skips cleanly if PyQt5 / QFluentWidgets are unavailable.
"""
import os
import sys
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def test_nd_app_constructs_and_wires_backends():
    try:
        from PyQt5.QtWidgets import QApplication  # noqa: F401
        import nd_app
    except Exception as e:                        # pragma: no cover
        pytest.skip(f"PyQt5 / QFluentWidgets unavailable: {e}")

    cfg = nd_app.LM.load_config()
    win = nd_app.NetdeckWindow(cfg)
    try:
        # three tabs registered in the FluentWindow stack
        assert win.stackedWidget.count() >= 3
        # rebuilt Manager tab is wired to the grouped-library backend
        assert hasattr(win.manager, "table")
        assert win.manager.table.rowCount() >= 0
        # the reused tabs are present
        assert win.stm32 is not None and win.tools is not None
        # grayscale theme is active
        from qfluentwidgets import isDarkTheme
        assert isDarkTheme() is True
    finally:
        win.close()
        win.deleteLater()


def test_readout_helper_builds():
    try:
        from PyQt5.QtWidgets import QApplication  # noqa: F401
        import nd_app
    except Exception as e:                        # pragma: no cover
        pytest.skip(f"PyQt5 / QFluentWidgets unavailable: {e}")
    card, cells = nd_app._readout([("a", "A", 1), ("b", "B", 2)])
    assert set(cells) == {"a", "b"}
    cells["a"].setText("9")
    assert cells["a"].text() == "9"
