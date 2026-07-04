#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the audit fixes in tools/fp_render.py.

Covers the three pure-logic fixes:
  * stroke width read from flat (width X) OR nested (stroke (width X))  [LOW]
  * fp_arc/gr_arc + symbol arc parsing (curved silk, not straight chords) [LOW]
  * WRL/VRML 3D loading + suffix dispatch (blank-preview MEDIUM bug)     [MEDIUM]

Everything here is pure geometry / parsing — no QPainter rendering — so the
tests run headlessly without a QApplication.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import fp_render as F  # noqa: E402


# ---------------------------------------------------------------------------
# LOW: stroke width helper (flat vs nested)
# ---------------------------------------------------------------------------
def test_stroke_width_flat():
    node = F.parse_sexpr('(x (width 0.33))')
    assert F._stroke_width(node) == 0.33


def test_stroke_width_nested():
    # KiCad 7/8/9 nest the width inside (stroke (width X) (type ...))
    node = F.parse_sexpr('(x (stroke (width 0.44) (type default)))')
    assert F._stroke_width(node) == 0.44


def test_stroke_width_default_when_absent():
    node = F.parse_sexpr('(x (layer "F.SilkS"))')
    assert F._stroke_width(node) == 0.1
    assert F._stroke_width(node, default=0.05) == 0.05


def test_stroke_width_malformed_flat_falls_through_to_stroke():
    # A non-numeric flat width must not shadow a valid nested stroke width.
    node = F.parse_sexpr('(x (width foo) (stroke (width 0.2)))')
    assert F._stroke_width(node) == 0.2


def test_footprint_line_width_flat_and_nested():
    txt = (
        '(footprint "T"'
        '  (fp_line (start -1 -1) (end 1 -1) (stroke (width 0.15) (type solid)) (layer "F.SilkS"))'
        '  (fp_line (start 2 2) (end 3 3) (width 0.2) (layer "F.SilkS")))'
    )
    fp = F._Footprint(F.parse_sexpr(txt))
    widths = sorted(round(l[5], 3) for l in fp.lines)
    # Previously the nested-stroke line silently fell back to 0.1.
    assert widths == [0.15, 0.2]


def test_footprint_circle_rect_poly_use_nested_stroke():
    txt = (
        '(footprint "T"'
        '  (fp_circle (center 0 0) (end 1 0) (stroke (width 0.13)) (layer "F.SilkS"))'
        '  (fp_rect (start -1 -1) (end 1 1) (stroke (width 0.17)) (layer "F.SilkS"))'
        '  (fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0.19)) (layer "F.SilkS")))'
    )
    fp = F._Footprint(F.parse_sexpr(txt))
    assert round(fp.circles[0][4], 3) == 0.13
    assert round(fp.rects[0][5], 3) == 0.17
    assert round(fp.polys[0][2], 3) == 0.19


# ---------------------------------------------------------------------------
# LOW: arc geometry + parsing
# ---------------------------------------------------------------------------
def test_arc_polyline_semicircle_on_unit_circle():
    pts = F._arc_polyline((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), segs=24)
    assert len(pts) == 25
    # every sample sits on the unit circle centred at the origin
    for (x, y) in pts:
        assert abs(math.hypot(x, y) - 1.0) < 1e-6
    # the curve actually passes through the mid point (not a straight chord)
    assert min(math.hypot(x - 0.0, y - 1.0) for (x, y) in pts) < 1e-6
    # endpoints are respected
    assert abs(pts[0][0] - 1.0) < 1e-9 and abs(pts[-1][0] + 1.0) < 1e-9


def test_arc_polyline_collinear_fallback():
    # No finite circle through 3 collinear points -> return the chord points.
    assert F._arc_polyline((0, 0), (1, 0), (2, 0)) == [(0, 0), (1, 0), (2, 0)]


def test_arc_polyline_center_quarter_turn():
    pts = F._arc_polyline_center((0.0, 0.0), (1.0, 0.0), 90.0, segs=24)
    assert len(pts) == 25
    assert abs(pts[0][0] - 1.0) < 1e-9 and abs(pts[0][1]) < 1e-9
    assert abs(pts[-1][0]) < 1e-6 and abs(pts[-1][1] - 1.0) < 1e-6


def test_footprint_fp_arc_three_point_parsed():
    txt = (
        '(footprint "T"'
        '  (fp_arc (start 1 0) (mid 0.70710678 0.70710678) (end 0 1)'
        '    (stroke (width 0.12)) (layer "F.SilkS")))'
    )
    fp = F._Footprint(F.parse_sexpr(txt))
    assert len(fp.arcs) == 1
    pp, layer, width = fp.arcs[0]
    assert layer == "F.SilkS"
    assert round(width, 3) == 0.12
    assert len(pp) > 3  # sampled curve, not just a chord
    # a mid sample bulges out to radius ~1 (would be ~0.92 for a straight chord)
    mid = pp[len(pp) // 2]
    assert abs(math.hypot(*mid) - 1.0) < 1e-3


def test_footprint_fp_arc_legacy_center_angle_parsed():
    # legacy KiCad: start = centre, end = arc start point, angle = degrees swept
    txt = '(footprint "T" (fp_arc (start 0 0) (end 1 0) (angle 90) (width 0.1) (layer "F.SilkS")))'
    fp = F._Footprint(F.parse_sexpr(txt))
    assert len(fp.arcs) == 1
    pp = fp.arcs[0][0]
    end = pp[-1]
    assert abs(end[0]) < 1e-6 and abs(end[1] - 1.0) < 1e-6


def test_footprint_arc_extends_bbox():
    # A silk arc bulging past the pads must expand the geometry bbox.
    txt = (
        '(footprint "T"'
        '  (pad "1" smd rect (at 0 0) (size 0.2 0.2) (layer "F.Cu"))'
        '  (fp_arc (start 5 0) (mid 0 5) (end -5 0) (stroke (width 0.12)) (layer "F.SilkS")))'
    )
    fp = F._Footprint(F.parse_sexpr(txt))
    x0, y0, x1, y1 = fp.bbox()
    assert y1 >= 4.9  # arc apex near (0, 5)
    assert x1 >= 4.9 and x0 <= -4.9


def test_summary_includes_arc_layer():
    txt = '(footprint "T" (fp_arc (start 1 0) (mid 0.7 0.7) (end 0 1) (stroke (width 0.1)) (layer "F.SilkS")))'
    fp = F._Footprint(F.parse_sexpr(txt))
    assert "F.SilkS" in fp.summary()["layers"]


# ---------------------------------------------------------------------------
# MEDIUM: WRL/VRML support + suffix dispatch
# ---------------------------------------------------------------------------
def test_model_format_classification():
    assert F.model_format("a.step") == "step"
    assert F.model_format("a.STP") == "step"
    assert F.model_format("b.wrl") == "vrml"
    assert F.model_format("b.VRML") == "vrml"
    assert F.model_format("c.3mf") == "unsupported"
    assert F.model_format("d.stl") == "unsupported"


_CUBE_WRL = """#VRML V2.0 utf8
#kicad model
Shape {
  appearance Appearance { material DEF mat Material { diffuseColor 0.8 0.8 0.8 } }
  geometry IndexedFaceSet {
    coordIndex [
      0,1,2,3,-1, 4,5,6,7,-1, 0,1,5,4,-1,
      2,3,7,6,-1, 1,2,6,5,-1, 0,3,7,4,-1
    ]
    coord Coordinate { point [
      -1 -1 -1, 1 -1 -1, 1 1 -1, -1 1 -1,
      -1 -1 1, 1 -1 1, 1 1 1, -1 1 1
    ] }
  }
}
"""


def test_parse_vrml_cube():
    v, f = F.parse_vrml(_CUBE_WRL)
    assert v is not None and f is not None
    assert v.shape == (8, 3)          # 8 unique vertices
    assert f.shape == (12, 3)         # 6 quads -> 12 fan triangles
    assert int(f.max()) == 7          # indices stay within the vertex list


def test_parse_vrml_two_shapes_offset_indices():
    # Two independent triangles; the second Shape's local indices (0,1,2) must be
    # rebased onto the concatenated vertex list.
    text = """#VRML V2.0 utf8
Shape { geometry IndexedFaceSet {
  coordIndex [ 0,1,2,-1 ]
  coord Coordinate { point [ 0 0 0, 1 0 0, 0 1 0 ] } } }
Shape { geometry IndexedFaceSet {
  coordIndex [ 0,1,2,-1 ]
  coord Coordinate { point [ 0 0 1, 1 0 1, 0 1 1 ] } } }
"""
    v, f = F.parse_vrml(text)
    assert v.shape == (6, 3)
    assert f.tolist() == [[0, 1, 2], [3, 4, 5]]


def test_parse_vrml_trailing_face_without_terminator():
    text = """#VRML V2.0 utf8
Shape { geometry IndexedFaceSet {
  coordIndex [ 0 1 2 ]
  coord Coordinate { point [ 0 0 0, 1 0 0, 0 1 0 ] } } }
"""
    v, f = F.parse_vrml(text)
    assert v.shape == (3, 3)
    assert f.tolist() == [[0, 1, 2]]


def test_parse_vrml_empty_returns_none():
    v, f = F.parse_vrml("#VRML V2.0 utf8\n# nothing here\n")
    assert v is None and f is None


def test_load_vrml_mesh_from_file(tmp_path):
    p = tmp_path / "cube.wrl"
    p.write_text(_CUBE_WRL, encoding="utf-8")
    v, f = F.load_vrml_mesh(p)
    assert v.shape == (8, 3) and f.shape == (12, 3)


def test_load_step_mesh_dispatches_wrl_not_cascadio(tmp_path):
    # The core MEDIUM bug: a .wrl used to be fed into cascadio's STEP reader,
    # which raised -> None -> blank preview. It must now load via the VRML path.
    p = tmp_path / "part.wrl"
    p.write_text(_CUBE_WRL, encoding="utf-8")
    v, f = F.load_step_mesh(p)
    assert v is not None and f is not None
    assert v.shape == (8, 3) and f.shape == (12, 3)


def test_load_step_mesh_unsupported_format_returns_none(tmp_path):
    p = tmp_path / "part.3mf"
    p.write_text("not a real model", encoding="utf-8")
    v, f = F.load_step_mesh(p)
    assert v is None and f is None


def test_step_summary_wrl(tmp_path):
    p = tmp_path / "cube.wrl"
    p.write_text(_CUBE_WRL, encoding="utf-8")
    s = F.step_summary(p)
    assert s is not None
    assert s["triangles"] == 12
    # 2x2x2 model units; VRML dims are left as-is (no metre->mm heuristic)
    assert s["size_mm"] == [2.0, 2.0, 2.0]
