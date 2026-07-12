"""Lock the dev-tree font packaging: theme.load_fonts must register the bundled
DM Sans face into the installed QFontDatabase, not silently fall back.

This guards the dev tree (tools/fonts/*.ttf reachable + Qt registers the family).
The frozen exe is additionally validated at launch by the shell's --selftest path,
which fails loudly if DM Sans is missing from the _MEIPASS bundle.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtGui import QFontDatabase  # noqa: E402
from ui import theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def test_load_fonts_registers_dm_sans():
    T.load_fonts(_APP)
    assert "DM Sans" in QFontDatabase().families()


def test_load_fonts_returns_registered_families_including_dm_sans():
    registered = T.load_fonts(_APP)
    assert "DM Sans" in registered
