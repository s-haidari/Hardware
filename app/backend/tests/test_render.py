"""Footprint SVG render tests — parse a .kicad_mod and emit valid SVG."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.kicad import render

FP = (
    '(footprint "SOT23-6" (version 20211014)\n'
    '  (pad "1" smd roundrect (at -0.95 -0.95) (size 0.6 0.7) (layers "F.Cu"))\n'
    '  (pad "2" smd roundrect (at -0.95 0.95) (size 0.6 0.7) (layers "F.Cu"))\n'
    '  (pad "6" smd roundrect (at 0.95 -0.95) (size 0.6 0.7) (layers "F.Cu"))\n'
    '  (fp_line (start -0.8 -1.5) (end 0.8 -1.5) (layer "F.SilkS") (width 0.12))\n'
    '  (fp_circle (center -1.4 -0.95) (end -1.3 -0.95) (layer "F.SilkS"))\n'
    ')\n'
)


class RenderTests(unittest.TestCase):
    def test_parses_pads_and_graphics(self):
        fp = render.Footprint(render.parse_sexpr(FP))
        self.assertEqual(fp.pad_count, 3)
        self.assertEqual(len(fp.lines), 1)
        self.assertEqual(len(fp.circles), 1)

    def test_emits_svg(self):
        svg = render.footprint_svg(FP)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("viewBox=", svg)
        self.assertIn("<rect", svg)     # pads
        self.assertIn("<line", svg)     # silk
        self.assertTrue(svg.endswith("</svg>"))

    def test_empty_footprint_is_safe(self):
        svg = render.footprint_svg('(footprint "x")')
        self.assertTrue(svg.startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
