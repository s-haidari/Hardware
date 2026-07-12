#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Robustness regressions for tools/fp_render.py (broken-feature fixes):
  * a pad with a missing (at ...) or (size ...) is COUNTED + kept, not silently dropped;
  * paint_mesh survives NaN/inf verts and out-of-range/empty faces instead of raising
    into a blank 3D pane (the callers swallow the exception into nothing).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import fp_render as F  # noqa: E402


def test_pad_missing_size_is_still_counted():
    # pad 2 has no (size ...) — previously dropped by `except: continue`, undercounting.
    txt = ('(footprint "T" (layer "F.Cu") '
           '(pad "1" smd rect (at 0 0) (size 1 1)) '
           '(pad "2" smd rect (at 1 0)))')
    fp = F._Footprint(F.parse_sexpr(txt))
    assert fp.summary()["pads"] == 2
    assert len(fp.pads) == 2


def test_pad_missing_at_is_still_counted():
    # pad with no (at ...) — position defaults to 0, pad is kept (not dropped).
    txt = '(footprint "T" (layer "F.Cu") (pad "1" smd rect (size 1 1)))'
    fp = F._Footprint(F.parse_sexpr(txt))
    assert fp.summary()["pads"] == 1


def _reset_dark_theme():
    # restore the module default so one test's theme swap can't leak into another
    F.set_render_theme(True)


def test_theme_swap_updates_label_and_fill_globals():
    # copper_label / symlabel / symfill must actually flip with the theme so preview
    # text/tint stays legible in both — they were hardcoded literals before the fix.
    try:
        F.set_render_theme(False)   # light theme
        assert F.COL_COPPER_LABEL.name() == "#f0f0f0"   # light label on light-theme dark pad
        assert F.COL_SYMLABEL.name() == "#1b1b1b"       # dark pin-number on light BG
        assert F.SYMFILL_BASE == (42, 42, 42)           # dark faint tint on light surface
        F.set_render_theme(True)    # dark theme
        assert F.COL_COPPER_LABEL.name() == "#161616"
        assert F.COL_SYMLABEL.name() == "#d9dee5"
        assert F.SYMFILL_BASE == (198, 198, 198)
    finally:
        _reset_dark_theme()


def test_pad_label_color_contrasts_pad_in_light_theme():
    # The pad-number was a fixed near-black (#161616). In light theme the copper pad
    # fill is a dark gray (#454545), so #161616 label on it was ~2:1 contrast (barely
    # visible). After the fix the light-theme label is light (#f0f0f0), so adding the
    # number introduces pixels LIGHTER than the (dark) pad fill.
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    if not _glyphs_rasterize():
        import pytest
        pytest.skip("Qt platform does not rasterize glyphs (headless font rendering)")
    with_num = ('(footprint "T" (layer "F.Cu") '
                '(pad "1" smd rect (at 0 0) (size 6 6) (layer "F.Cu")))')
    no_num = ('(footprint "T" (layer "F.Cu") '
              '(pad "" smd rect (at 0 0) (size 6 6) (layer "F.Cu")))')
    try:
        F.set_render_theme(False)   # light theme: pad fill #454545, label should be light
        light_num = F._Footprint(F.parse_sexpr(with_num)).render(140)
        light_bare = F._Footprint(F.parse_sexpr(no_num)).render(140)
        F.set_render_theme(True)    # dark theme: pad fill #bcbcbc, label should be dark
        dark_num = F._Footprint(F.parse_sexpr(with_num)).render(140)
        dark_bare = F._Footprint(F.parse_sexpr(no_num)).render(140)
    finally:
        _reset_dark_theme()
    # Light theme: the number adds pixels LIGHTER than the darkish pad (the old
    # near-black literal would instead only add darker pixels, failing this).
    assert _lightest_gray(light_num) > _lightest_gray(light_bare)
    # Dark theme: the label is dark, so the number adds DARKER pixels than the
    # light pad fill.
    assert _darkest_gray(dark_num) < _darkest_gray(dark_bare)


def _darkest_gray(img):
    """Lowest per-pixel luminance in the image (0=black..255=white)."""
    lo = 255
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
            lo = min(lo, lum)
    return lo


def _lightest_gray(img):
    hi = 0
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
            hi = max(hi, lum)
    return hi


def _glyphs_rasterize():
    """True if the Qt platform actually rasterizes text glyphs. The headless Windows
    CI runner's offscreen platform often does NOT (no fontconfig), so a drawn label
    adds no pixels — which makes the label-contrast pixel checks below untestable
    there (a drawn '8' leaves the canvas pure white). On Linux/fontconfig it rasterizes,
    so the contrast assertions still run and guard the real behaviour."""
    from PyQt5.QtGui import QImage, QPainter, QFont, QColor
    img = QImage(40, 40, QImage.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setPen(QColor("black"))
    f = QFont()
    f.setPixelSize(28)
    p.setFont(f)
    p.drawText(2, 32, "8")
    p.end()
    return _darkest_gray(img) < 128     # any dark pixel => glyphs rasterized


def test_symbol_pin_label_contrasts_background_in_both_themes():
    # The pin-number text was a fixed near-white (#d9dee5): legible on the dark
    # preview but near-invisible on the light-theme background. After the fix the
    # label tracks the theme. To isolate the LABEL from the pin line (which also
    # tracks the theme), render the same symbol WITH and WITHOUT a pin number and
    # require the number to add a contrasting pixel in each theme.
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841
    if not _glyphs_rasterize():
        import pytest
        pytest.skip("Qt platform does not rasterize glyphs (headless font rendering)")
    with_num = ('(symbol "R" '
                '(pin passive line (at -8 0 0) (length 4) (number "1")))')
    no_num = ('(symbol "R" '
              '(pin passive line (at -8 0 0) (length 4) (number "")))')
    try:
        F.set_render_theme(False)   # light BG ~#eeeeee
        light_num = F._render_symbol_image_uncached(with_num, 200)
        light_bare = F._render_symbol_image_uncached(no_num, 200)
        F.set_render_theme(True)    # dark BG ~#16171a
        dark_num = F._render_symbol_image_uncached(with_num, 200)
        dark_bare = F._render_symbol_image_uncached(no_num, 200)
    finally:
        _reset_dark_theme()
    assert None not in (light_num, light_bare, dark_num, dark_bare)
    # Light theme: adding the number must introduce DARKER pixels than the bare
    # symbol (a near-white #d9dee5 label would not — it'd be lighter than the pin).
    assert _darkest_gray(light_num) < _darkest_gray(light_bare)
    # Dark theme: adding the number introduces LIGHTER pixels (near-white label).
    assert _lightest_gray(dark_num) > _lightest_gray(dark_bare)


def test_paint_mesh_survives_bad_meshes():
    import numpy as np  # noqa: F401
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QImage, QPainter
    app = QApplication.instance() or QApplication([])  # noqa: F841
    img = QImage(64, 64, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    try:
        # NaN vertex + a valid triangle → sanitised, renders, no raise
        F.paint_mesh(p, 64, 64, [[float("nan"), 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
        # out-of-range face index (5 with only 3 verts) → dropped, no IndexError → placeholder
        F.paint_mesh(p, 64, 64, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 5]])
        # empty mesh → quiet placeholder, no raise on .max(0) of an empty array
        F.paint_mesh(p, 64, 64, [], [])
        # too few verts → placeholder
        F.paint_mesh(p, 64, 64, [[0, 0, 0]], [[0, 1, 2]])
    finally:
        p.end()
