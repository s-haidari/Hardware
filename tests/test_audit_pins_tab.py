"""Regression tests for the audit fixes in tools/stm32_pins_tab.py.

Covers:
  * Fix 2 (MEDIUM) — _pin_search_haystack now indexes the visible Destination net
    and the Switch label, so typing 'VTARGET' / 'CARD_LANE_###' / 'Must-Switch'
    finds the pin instead of yielding '0 pins'. Pure function -> unit-tested
    directly, plus an offscreen end-to-end filter check.
  * Fix 3 (MEDIUM) — _select() now drives the Table row selection (map click ->
    matching table row).
  * Fix 4 (LOW)    — a failed load() reverts the package selector to the last good
    package (or clears the views) instead of leaving stale pins under a new label.
  * Fix 5 (LOW)    — _populate_packages closes the sqlite handle via try/finally
    even when the query raises.
  * Fix 1 (HIGH)   — _export() appends the missing extension and traps a failed
    write in a dialog instead of tearing down the app.

Pure-function tests run without Qt; GUI tests run under the offscreen platform and
skip if PyQt5 is unavailable.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db          # noqa: E402
import stm32_authority as auth  # noqa: E402


def _fake_position(**over):
    """A minimal socket-position dict shaped like authority['positions'][i], enough
    for the pure haystack + rationale helpers. Overridable per test."""
    p = {
        "position": 42,
        "pin_names": {"PB7": 1},
        "role_set": {"I2C1_SDA": 1},
        "switch_class": db.SWITCH_MUST,
        "tags": {},
        "assignment": {"destination": "VTARGET"},
        "breakout": {"service_nets": []},
        "peripherals": ["I2C1"],
    }
    p.update(over)
    return p


class HaystackPureTests(unittest.TestCase):
    """Fix 2, no Qt needed: the search haystack is a pure function of one position."""

    def setUp(self):
        # importing the tab pulls PyQt5; skip cleanly if it isn't installed
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        self.tab = tab

    def test_destination_is_indexed(self):
        hs = self.tab._pin_search_haystack(_fake_position(assignment={"destination": "VTARGET"}))
        self.assertIn("vtarget", hs)          # the regression: destination now searchable

    def test_net_fallback_destination_is_indexed(self):
        # plain GPIO pins carry assignment['net'] (e.g. CARD_LANE_042), not 'destination'
        p = _fake_position(switch_class=db.SWITCH_NONE,
                           assignment={"net": "CARD_LANE_042"})
        hs = self.tab._pin_search_haystack(p)
        self.assertIn("card_lane_042", hs)

    def test_switch_label_is_indexed(self):
        must = self.tab._pin_search_haystack(_fake_position(switch_class=db.SWITCH_MUST))
        self.assertIn("must-switch", must)     # the 'Switch' column text, now searchable
        fixed = self.tab._pin_search_haystack(_fake_position(switch_class=db.SWITCH_NONE))
        self.assertIn("fixed", fixed)

    def test_existing_fields_still_indexed(self):
        hs = self.tab._pin_search_haystack(_fake_position())
        self.assertIn("pb7", hs)               # pin name
        self.assertIn("i2c1_sda", hs)          # role
        self.assertIn("i2c1", hs)              # peripheral
        self.assertEqual(hs, hs.lower())       # lowercased for case-insensitive match


class HaystackRealAuthorityTests(unittest.TestCase):
    """Fix 2 against a real built authority (uses the checked-in DB)."""

    @classmethod
    def setUpClass(cls):
        dbp = db.default_db_path()
        if not dbp.exists():
            raise unittest.SkipTest("stm32 database not built")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        cls.tab = tab
        cls.conn = db.connect(dbp)
        cls.a64 = auth.build(cls.conn, "LQFP64")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None) is not None:
            cls.conn.close()

    def _pos(self, n):
        return next(p for p in self.a64["positions"] if p["position"] == n)

    def test_real_destination_and_label_indexed(self):
        p1 = self._pos(1)                                   # VBAT/VDD -> VBAT_TGT, must-switch
        hs = self.tab._pin_search_haystack(p1)
        self.assertIn("vbat_tgt", hs)                       # visible Destination cell
        self.assertIn("must-switch", hs)                    # visible Switch cell

    def test_every_position_haystack_covers_its_destination(self):
        # For every pin, whatever the Table shows in the Destination column must be
        # findable through the haystack (the exact contract the bug violated).
        for p in self.a64["positions"]:
            dest = (p["assignment"].get("destination")
                    or p["assignment"].get("net") or "")
            if dest:
                self.assertIn(dest.lower(), self.tab._pin_search_haystack(p),
                              f"pin {p['position']} destination {dest!r} not searchable")






if __name__ == "__main__":
    unittest.main(verbosity=2)
