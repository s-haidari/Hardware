"""Regression tests for the audit fixes in tools/stm32_authority.py.

Covers two bugs found by the 2026-07-04 codebase audit:
  * MEDIUM  to_kicad_symbol: fixed IO pins carry the generic net "CARD_LANE"
            (truthy), so the per-pin fallback was dead code and all 53 fixed IO
            LQFP100 pins got the identical name "CARD_LANE" — defeating per-lane
            identity. Fix: relabel to CARD_LANE_{pin:03d} so each pin is distinct.
  * LOW     to_switchmap_c: a zero-channel package emitted `enum {}` and empty
            array initializers `{}` (invalid ISO C). Fix: placeholder guards.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db  # noqa: E402
import stm32_authority as auth  # noqa: E402


class KicadSymbolLaneTests(unittest.TestCase):
    """Bug 1: distinct CARD_LANE_### names per fixed pin (needs the CubeMX DB)."""

    @classmethod
    def setUpClass(cls):
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls.dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, cls.dbp)
        cls.conn = db.connect(cls.dbp)

    def _lane_names(self, package):
        sym = auth.to_kicad_symbol(auth.build(self.conn, package))
        return sym, re.findall(r'name "(CARD_LANE_\d+)"', sym)

    def test_lqfp100_fixed_pins_have_distinct_lane_names(self):
        sym, lanes = self._lane_names("LQFP100")
        # Every fixed IO pin gets its own socket-numbered lane, so no two share a
        # name (the pre-fix bug named all 53 of them the identical "CARD_LANE").
        self.assertEqual(len(lanes), len(set(lanes)),
                         "CARD_LANE names must be unique per fixed pin")
        self.assertEqual(len(lanes), 53)                 # audit: 53/100 fixed IO pins
        # The bare, non-unique net must never appear as a pin name anymore.
        self.assertNotIn('name "CARD_LANE"', sym)
        # Socket-numbered, zero-padded to 3 digits (matches the by_pin lane policy).
        self.assertIn('name "CARD_LANE_015"', sym)       # PC0 — a plain fixed IO pin
        self.assertTrue(all(re.fullmatch(r"CARD_LANE_\d{3}", n) for n in lanes))

    def test_switched_pins_keep_their_destination_net(self):
        # The fix must only relabel the generic CARD_LANE net; real destinations
        # (e.g. pin 1 = VBAT_TGT) are untouched.
        sym, _ = self._lane_names("LQFP100")
        self.assertIn('name "VBAT_TGT"', sym)
        self.assertEqual(sym.count("(pin "), 100)

    def test_lqfp64_symbol_still_wellformed(self):
        # Existing LQFP64 invariants still hold after the relabel.
        sym, lanes = self._lane_names("LQFP64")
        self.assertTrue(sym.startswith("(kicad_symbol_lib"))
        self.assertEqual(sym.count("("), sym.count(")"))     # balanced S-expr
        self.assertEqual(sym.count("(pin "), 64)
        self.assertNotIn('name "CARD_LANE"', sym)            # no bare lane name here either
        self.assertEqual(len(lanes), len(set(lanes)))


class SwitchmapEmptyGuardTests(unittest.TestCase):
    """Bug 2: to_switchmap_c must emit valid ISO C even with zero channels.

    A synthetic authority with no positions exercises the zero-channel path
    without needing the CubeMX DB, so this class runs unconditionally.
    """

    def _empty_header(self):
        fake = {"package": "LQFP0_TEST", "positions": []}
        return auth.to_switchmap_c(fake)

    def test_no_empty_enum(self):
        hdr = self._empty_header()
        # An empty `enum {}` is invalid ISO C; a placeholder member must be present.
        self.assertIn("RAIL_NONE", hdr)
        self.assertNotIn("typedef enum {\n} netdeck_rail_t;", hdr)

    def test_no_empty_array_initializers(self):
        hdr = self._empty_header()
        # Empty aggregate initializers `T x[] = {};` are invalid ISO C.
        self.assertNotIn("CHANNELS[] = {\n};", hdr)
        self.assertNotIn("CELL_ORDER[] = {  };", hdr)
        self.assertIn("{ 0, 0, 0, RAIL_NONE }", hdr)         # placeholder channel row
        self.assertIn("CELL_ORDER[] = { 0 };", hdr)          # placeholder cell order

    def test_defines_report_zero_channels(self):
        hdr = self._empty_header()
        # The count macros stay honest (0 real channels) even with the placeholder
        # element present, so firmware iterating on CHANNELS_USED does nothing.
        self.assertIn("#define NETDECK_LQFP0_TEST_CELLS 0", hdr)
        self.assertIn("#define NETDECK_LQFP0_TEST_CHANNELS_USED 0", hdr)

    def test_nonempty_package_unchanged(self):
        # The guards must be a strict no-op for a real, non-empty package: no
        # placeholder rail/channel/cell-order leaks in.
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, dbp)
        conn = db.connect(dbp)
        hdr = auth.to_switchmap_c(auth.build(conn, "LQFP64"))
        self.assertNotIn("RAIL_NONE", hdr)
        self.assertNotIn("{ 0, 0, 0, RAIL_NONE }", hdr)
        self.assertIn("NETDECK_LQFP64_CHANNELS", hdr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
