"""Regression tests for the audit fixes in tools/stm32_pins_tab.py.

Covers:
  * The module docstring must describe the module's real pure-helper role.
  * Dead-code cleanup — the zero-caller helpers/constants (including the deleted
    HTML-export + pin-search surface: _summary_html / _pin_detail_html /
    _pin_search_haystack) are gone, the live helpers remain, and the module
    drags in no unused PyQt5/json/os imports.

Pure-function tests run without Qt; GUI tests run under the offscreen platform and
skip if PyQt5 is unavailable.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


class NamesHelperTests(unittest.TestCase):
    """The live _names() column renderer, no Qt canvas needed. It backs the visible
    Table 'Pin Name(s)' / 'Role Set' cells (bench.py:_pin_row), so it must spell the
    distinct keys out, most-common first, and never leak the ×count integers/braces."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        self.tab = tab

    def test_renders_distinct_keys_without_count_noise(self):
        # ×count dicts render as comma-joined keys only — no braces, no integers.
        out = self.tab._names({"VBAT": 50, "VDD": 3})
        self.assertEqual(out, "VBAT, VDD")
        self.assertNotIn("{", out)
        self.assertNotIn("}", out)
        self.assertNotIn("50", out)
        self.assertNotIn("3", out)

    def test_empty_dict_renders_empty_string(self):
        self.assertEqual(self.tab._names({}), "")


class ModuleRoleDocstringTests(unittest.TestCase):
    """Coherence fix — the module docstring must describe the module's *real* role.

    stm32_pins_tab.py defines no QWidget, mounts no nav tab, and paints no canvas;
    it is a pure helper module imported by bench.py/bench_visuals.py/stm32_authority.
    The docstring previously claimed it *was* the 'STM32 Pins' tab, a 'Self-contained
    widget; the main window mounts it as the third nav tab' — a false coherence claim.
    """

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        self.tab = tab

    def test_module_defines_no_qwidget_subclass(self):
        # The premise the docstring must not contradict: nothing here is a widget.
        import inspect
        from PyQt5.QtWidgets import QWidget
        widgets = [
            name for name, obj in vars(self.tab).items()
            if inspect.isclass(obj) and issubclass(obj, QWidget)
            and obj.__module__ == self.tab.__name__
        ]
        self.assertEqual(widgets, [], f"module unexpectedly defines widget(s): {widgets}")

    def test_docstring_drops_the_false_widget_and_navtab_claims(self):
        doc = (self.tab.__doc__ or "").lower()
        self.assertTrue(doc, "module lost its docstring")
        self.assertNotIn("self-contained widget", doc)
        self.assertNotIn("third nav tab", doc)

    def test_docstring_states_the_real_pure_helper_role(self):
        doc = (self.tab.__doc__ or "").lower()
        self.assertIn("helper", doc)               # names its actual role
        # points at the module that owns the real tab surface
        self.assertIn("bench.py", doc)


class DeadCodeRemovedTests(unittest.TestCase):
    """Audit cleanup — the zero-caller helpers/constants were removed, and the module
    no longer drags in the unused PyQt5/json/os imports it never touched. The live
    helpers (used by bench.py / stm32_authority.py) must remain intact."""

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        self.tab = tab

    def test_zero_caller_symbols_are_gone(self):
        # Includes the shipped-but-unused HTML-export + pin-search surface: nothing in
        # the Bench UI consumed _summary_html / _pin_detail_html / _pin_search_haystack,
        # so they (and their sole-purpose helpers _numlist / _esc) were deleted.
        for name in ("_pin_branches", "cells_html", "_CAT_LABEL", "_wash",
                     "_SVG_FONT", "_SVG_MONO", "_default_vault_authority_dir",
                     "_vault_authority_dirs",
                     "_summary_html", "_pin_detail_html", "_pin_search_haystack",
                     "_numlist", "_esc"):
            self.assertFalse(hasattr(self.tab, name),
                             f"{name} was deleted as dead code but is still defined")

    def test_live_helpers_survive(self):
        # These have real callers (bench.py / bench_visuals.py / stm32_authority.py);
        # _fmt_contact in particular is a live dependency of _pin_chain and must stay.
        for name in ("pin_map_geometry", "pin_map_svg", "_pin_detail_rows",
                     "_pin_chain", "_fmt_contact", "expandNet", "_names"):
            self.assertTrue(hasattr(self.tab, name),
                            f"{name} is a live helper but went missing")

    def test_no_unused_qt_json_os_imports_survive(self):
        # The module is pure text/HTML/geometry: it imports no PyQt5, json or os.
        import ast
        from pathlib import Path
        src = Path(self.tab.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("PyQt5", imported)
        self.assertNotIn("json", imported)
        self.assertNotIn("os", imported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
