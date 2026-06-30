"""
Regression guard for the importer-correctness primitives — built from the REAL
broken data in git/Hardware/libs (see app/backend/README.md, requirement #1).

Today's bug:
  * symbols point at the wrong footprint library nickname (per-part or bare),
    so KiCad resolves no footprint when the symbol is placed;
  * footprints have no `(model …)` line, so no 3D model attaches.

These tests pin the fix so it can never silently regress.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.kicad import symbols, footprints


class FootprintNicknameTests(unittest.TestCase):
    def test_strips_per_part_nickname(self):
        # Real case: "STUSB4500QTR:QFN50P400X400X90-25N"
        self.assertEqual(
            symbols.footprint_name("STUSB4500QTR:QFN50P400X400X90-25N"),
            "QFN50P400X400X90-25N",
        )

    def test_bare_name_is_kept(self):
        # Real case: bare "RM_10_ADI" (no nickname at all)
        self.assertEqual(symbols.footprint_name("RM_10_ADI"), "RM_10_ADI")

    def test_qualify_rewrites_per_part_nickname_to_shared_lib(self):
        self.assertEqual(
            symbols.qualify_footprint("STUSB4500QTR:QFN50P400X400X90-25N"),
            "MyFootprints:QFN50P400X400X90-25N",
        )

    def test_qualify_rewrites_bare_name(self):
        self.assertEqual(symbols.qualify_footprint("RM_10_ADI"), "MyFootprints:RM_10_ADI")

    def test_qualify_is_idempotent(self):
        self.assertEqual(
            symbols.qualify_footprint("MyFootprints:QFN1610_STM"),
            "MyFootprints:QFN1610_STM",
        )

    def test_empty_footprint_stays_empty(self):
        self.assertEqual(symbols.qualify_footprint(""), "")

    def test_rewrites_property_line_in_symbol_block(self):
        block = (
            '  (symbol "STUSB4500QTR"\n'
            '    (property "Reference" "U" (id 0) (at 0 0 0))\n'
            '    (property "Footprint" "STUSB4500QTR:QFN50P400X400X90-25N" (id 2) (at 0 0 0)\n'
            '      (effects (hide yes)))\n'
            '  )\n'
        )
        out = symbols.rewrite_symbol_footprint(block)
        self.assertIn('(property "Footprint" "MyFootprints:QFN50P400X400X90-25N"', out)
        self.assertNotIn("STUSB4500QTR:QFN", out)


class FootprintModelTests(unittest.TestCase):
    def test_inserts_model_when_missing(self):
        # 92/93 real footprints have NO model line.
        fp = '(footprint "2N7002"\n  (layer "F.Cu")\n  (pad 1 smd roundrect)\n)\n'
        self.assertFalse(footprints.has_model(fp))
        out = footprints.ensure_model(fp, "2N7002.step")
        self.assertIn('(model "${MY3DMODELS}/2N7002.step"', out)
        self.assertTrue(footprints.has_model(out))
        # still a closed s-expr
        self.assertEqual(out.count("("), out.count(")"))

    def test_repairs_bare_model_path(self):
        # The 1 real model line that exists is bare: "(model TPS61023DRLT.stp"
        fp = (
            '(footprint "TPS61023"\n'
            '  (model TPS61023DRLT.stp\n'
            '    (offset (xyz 0 0 0))\n'
            '    (scale (xyz 1 1 1))\n'
            '    (rotate (xyz 0 0 0))\n'
            '  )\n'
            ')\n'
        )
        out = footprints.ensure_model(fp, "TPS61023DRLT.stp")
        self.assertIn('(model "${MY3DMODELS}/TPS61023DRLT.stp"', out)
        self.assertNotIn("(model TPS61023DRLT.stp", out)

    def test_idempotent_on_correct_path(self):
        fp = (
            '(footprint "x"\n'
            '  (model "${MY3DMODELS}/x.step"\n'
            '    (offset (xyz 0 0 0))\n  )\n)\n'
        )
        out = footprints.ensure_model(fp, "x.step")
        self.assertEqual(out.count('(model "${MY3DMODELS}/x.step"'), 1)


if __name__ == "__main__":
    unittest.main()
