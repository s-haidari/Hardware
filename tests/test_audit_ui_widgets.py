"""Regression tests for the ui_widgets.py audit fixes.

Covers three theme/lifecycle bugs the 2026-07-04 codebase audit flagged:

  1. MEDIUM `_Readout` accent frozen at construction — the "Selected" dot kept the
     old theme's accent on a theme toggle (vanished on light). Accent is now
     re-resolved every restyle() from a token key / callable / literal.
  2. LOW `Rail.restyle` was a no-op and kept no ref to its group SectionHeaders,
     so captions + hairlines kept stale inline colours after a theme toggle.
  3. LOW `Rail.add_item` fired the initial select()->selected(key) during
     construction, before any owner had connected — the emit reached no slots.
     The initial mark is now non-emitting; the owner drives it via select().

This is Qt widget code, so a QApplication is required. We run headless with
QT_QPA_PLATFORM=offscreen (set before QApplication is created).
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402

import ui_theme  # noqa: E402
import ui_widgets as uw  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv[:1])


class ReadoutAccentTests(unittest.TestCase):
    """Bug 1 — the accent dot must follow the active theme, not freeze."""

    def setUp(self):
        ui_theme.set_theme(dark=True)   # deterministic start for every test

    def _dot_css(self, r):
        return r._dot.styleSheet()

    def test_token_key_accent_reresolves_on_theme_toggle(self):
        # This is exactly the kicad_tools "Selected" case, expressed with the new
        # lazy contract: pass the token KEY, not a pre-resolved colour.
        r = uw._Readout("Selected", accent="ACCENT")
        r.restyle()
        self.assertIn(ui_theme.DARK_COLORS["ACCENT"], self._dot_css(r))

        ui_theme.set_theme(dark=False)
        r.restyle()
        css = self._dot_css(r)
        self.assertIn(ui_theme.LIGHT_COLORS["ACCENT"], css)
        # and it is NOT still carrying the dark accent (the original bug)
        self.assertNotIn(ui_theme.DARK_COLORS["ACCENT"], css)

    def test_callable_accent_reresolves_on_theme_toggle(self):
        r = uw._Readout("Selected", accent=lambda: ui_theme.tc("ACCENT"))
        r.restyle()
        self.assertIn(ui_theme.DARK_COLORS["ACCENT"], self._dot_css(r))
        ui_theme.set_theme(dark=False)
        r.restyle()
        self.assertIn(ui_theme.LIGHT_COLORS["ACCENT"], self._dot_css(r))

    def test_none_accent_uses_idle_dot_and_follows_theme(self):
        r = uw._Readout("Fixed", accent=None)
        r.restyle()
        self.assertIn(ui_theme.DARK_COLORS["DOT_IDLE"], self._dot_css(r))
        ui_theme.set_theme(dark=False)
        r.restyle()
        css = self._dot_css(r)
        self.assertIn(ui_theme.LIGHT_COLORS["DOT_IDLE"], css)
        self.assertNotIn(ui_theme.DARK_COLORS["DOT_IDLE"], css)

    def test_literal_category_hex_stays_fixed_across_themes(self):
        # CATEGORY pin/net colours are theme-independent and MUST stay frozen.
        power = ui_theme.cat("power")            # "#e0a94a"
        r = uw._Readout("Power", accent=power)
        r.restyle()
        self.assertIn(power, self._dot_css(r))
        ui_theme.set_theme(dark=False)
        r.restyle()
        self.assertIn(power, self._dot_css(r))   # unchanged on light

    def test_readoutband_selected_dot_visible_on_both_themes(self):
        # End-to-end through the band, mirroring how a tab builds it, but with the
        # token-key accent so the dot is a real theme colour on either theme.
        band = uw.ReadoutBand([
            ("projects", "Projects", None),
            ("selected", "Selected", "ACCENT"),
        ])
        sel = band._stats["selected"]
        for dark in (True, False, True):
            ui_theme.set_theme(dark=dark)
            band.restyle()
            expect = (ui_theme.DARK_COLORS if dark else ui_theme.LIGHT_COLORS)["ACCENT"]
            self.assertIn(expect, sel._dot.styleSheet())


class RailHeaderRestyleTests(unittest.TestCase):
    """Bug 2 — Rail.restyle must re-resolve its group SectionHeaders."""

    def setUp(self):
        ui_theme.set_theme(dark=True)

    def test_group_header_follows_theme_after_restyle(self):
        rail = uw.Rail(150)
        hdr = rail.add_group("View")
        rail.add_item("map", "Map")
        # constructed under dark: caption carries the dark dim colour
        self.assertIn(ui_theme.DARK_COLORS["FG_DIM"], hdr._label.styleSheet())

        ui_theme.set_theme(dark=False)
        rail.restyle()   # no longer a no-op
        label_css = hdr._label.styleSheet()
        rule_css = hdr._rule.styleSheet()
        self.assertIn(ui_theme.LIGHT_COLORS["FG_DIM"], label_css)
        self.assertNotIn(ui_theme.DARK_COLORS["FG_DIM"], label_css)
        self.assertIn(ui_theme.LIGHT_COLORS["BORDER"], rule_css)

    def test_multiple_groups_all_restyle(self):
        rail = uw.Rail(150)
        h1 = rail.add_group("View")
        rail.add_item("a", "A")
        h2 = rail.add_group("More")
        rail.add_item("b", "B")
        ui_theme.set_theme(dark=False)
        rail.restyle()
        for h in (h1, h2):
            self.assertIn(ui_theme.LIGHT_COLORS["FG_DIM"], h._label.styleSheet())


class RailInitialSelectTests(unittest.TestCase):
    """Bug 3 — the first add_item must not emit selected() before the owner
    connects, but explicit select()/clicks must still emit."""

    def setUp(self):
        ui_theme.set_theme(dark=True)

    def test_initial_add_item_does_not_emit(self):
        rail = uw.Rail(150)
        seen = []
        # Connect a spy BEFORE adding items: if add_item still emitted, this fires.
        rail.selected.connect(seen.append)
        rail.add_item("map", "Map")
        rail.add_item("table", "Table")
        self.assertEqual(seen, [])                 # no premature emission
        # ...but state is still committed so the view is correct on first paint
        self.assertEqual(rail.current(), "map")
        self.assertTrue(rail._items["map"].isChecked())

    def test_explicit_select_emits(self):
        rail = uw.Rail(150)
        rail.add_item("map", "Map")
        rail.add_item("table", "Table")
        seen = []
        rail.selected.connect(seen.append)         # owner connects after building
        rail.select("map")
        self.assertEqual(seen, ["map"])
        rail.select("table")
        self.assertEqual(seen, ["map", "table"])
        self.assertEqual(rail.current(), "table")

    def test_button_click_still_emits(self):
        rail = uw.Rail(150)
        rail.add_item("map", "Map")
        btn = rail.add_item("table", "Table")
        seen = []
        rail.selected.connect(seen.append)
        btn.click()                                # user clicks the rail row
        self.assertEqual(seen, ["table"])
        self.assertEqual(rail.current(), "table")

    def test_unknown_key_select_is_noop(self):
        rail = uw.Rail(150)
        rail.add_item("map", "Map")
        seen = []
        rail.selected.connect(seen.append)
        rail.select("does-not-exist")
        self.assertEqual(seen, [])
        self.assertEqual(rail.current(), "map")


if __name__ == "__main__":
    unittest.main()
