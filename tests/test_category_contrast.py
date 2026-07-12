"""Every category hue must clear 3:1 against the surfaces it sits on (legibility)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.theme as T  # noqa: E402
_APP = QApplication.instance() or QApplication([])

CATS = ("power", "ground", "core", "service", "lane", "must", "osc", "fixed", "breakout")

def test_every_category_clears_3to1_on_surfaces_both_themes():
    for dark in (True, False):
        T.set_theme(dark)
        for cat in CATS:
            for surf in ("canvas", "raised", "inset"):
                c = T.category_contrast(cat, surf)
                assert c >= 3.0, f"{'dark' if dark else 'light'} {cat} on {surf}: {c:.2f}"
    T.set_theme(True)
