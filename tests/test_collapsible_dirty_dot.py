"""CollapsibleSection.set_dirty: the header unsaved-change dot for the Editor's
per-section Save-preview scope."""
import os
import sys
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QWidget      # noqa: E402
import ui.widgets as W                                  # noqa: E402

_app = QApplication.instance() or QApplication([])


class DirtyDotTests(unittest.TestCase):
    def test_dot_toggles(self):
        sec = W.CollapsibleSection("Predefined Sizes", QWidget())
        self.assertFalse(sec.is_dirty())
        sec.set_dirty(True)
        self.assertTrue(sec.is_dirty())
        self.assertTrue(sec._dot.isVisible())
        sec.set_dirty(False)
        self.assertFalse(sec.is_dirty())
        self.assertFalse(sec._dot.isVisible())

    def test_empty_section_set_dirty_is_noop(self):
        sec = W.CollapsibleSection("Empty", None)     # bodyless → hidden section
        sec.set_dirty(True)                            # must not raise
        self.assertFalse(sec.is_dirty())


if __name__ == "__main__":
    unittest.main()
