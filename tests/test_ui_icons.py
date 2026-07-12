"""Unified icon set (ui.icons): every glyph is valid SVG, renders non-empty, and
shares one stroke weight / cap-join language (Refined-Neutral iconography)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.icons as I  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# the nav + action icons the shell requires (must all exist)
REQUIRED_NAV = {"ham", "bench", "library", "projects", "routing", "git",
                "settings", "theme", "sun", "update"}


def test_all_required_nav_icons_present():
    assert REQUIRED_NAV <= set(I.GLYPHS)


def test_every_glyph_is_valid_svg():
    from PyQt5.QtSvg import QSvgRenderer
    for name, svg in I.GLYPHS.items():
        r = QSvgRenderer(bytearray(svg, encoding="utf-8"))
        assert r.isValid(), f"{name} is not valid SVG"


def test_every_glyph_renders_non_empty_pixmap():
    for name, svg in I.GLYPHS.items():
        icon = W.svg_icon(svg, size=18, color="#000000")
        pm = icon.pixmap(18, 18)
        assert not pm.isNull(), f"{name} rendered a null pixmap"


def test_consistent_stroke_weight():
    # every stroked glyph shares one weight (the 1.2/1.3 split is retired)
    for name, svg in I.GLYPHS.items():
        widths = set(re.findall(r'stroke-width="([0-9.]+)"', svg))
        assert widths <= {"1.25"}, f"{name} has off-weight strokes: {widths}"


def test_uses_16_viewbox_and_round_caps():
    for name, svg in I.GLYPHS.items():
        assert 'viewBox="0 0 16 16"' in svg, f"{name} not on the 16 grid"
        assert 'stroke-linecap="round"' in svg, f"{name} missing round caps"


def _line_count(svg: str) -> int:
    # 'L' line-to commands trace a polygon outline (a cog silhouette);
    # 'l'/'M' short ray strokes trace detached spokes (a sun).
    return len(re.findall(r"[Ll]", svg))


def test_settings_is_a_cog_silhouette_not_a_sun():
    # Regression (2026-07-08 icon audit): the Settings nav glyph sits one row
    # under the Light-theme `sun` toggle. If Settings is a center-circle +
    # detached radial rays (the sun's structure), the two read as two suns.
    # A gear must be a *continuous toothed outline* — a single many-vertex
    # polygon path — never rays.
    settings = I.GLYPHS["settings"]
    sun = I.GLYPHS["sun"]

    # the cog outline is one long L-polygon; the sun is a circle + short rays.
    assert _line_count(settings) >= 24, "settings lost its cog polygon outline"
    # the sun's structure is a circle plus a ray bundle (several M-moves in one
    # path); the cog must NOT carry that ray bundle.
    sun_ray_moves = re.findall(r"M[0-9. ]+[hvl]", sun)
    assert sun_ray_moves, "sun no longer has its radial-ray strokes"
    settings_ray_moves = re.findall(r"M[0-9. ]+[hvl]", settings)
    assert not settings_ray_moves, "settings still uses sun-style radial rays"


def test_settings_and_sun_render_visibly_distinct():
    # Prove it at the pixel level: the two glyphs must not be near-identical.
    from PyQt5.QtCore import Qt

    def _bits(name: str):
        icon = W.svg_icon(I.GLYPHS[name], size=48, color="#000000")
        img = icon.pixmap(48, 48).toImage()
        return [
            1 if img.pixelColor(x, y).alpha() > 32 else 0
            for y in range(48)
            for x in range(48)
        ]

    a, b = _bits("settings"), _bits("sun")
    differing = sum(1 for x, y in zip(a, b) if x != y)
    inked = sum(1 for v in a + b if v)
    # at least a quarter of the inked pixels must disagree — a filled/toothed
    # cog and a spoked sun differ far more than a shared circle + rays would.
    assert differing >= inked * 0.25, (
        f"settings and sun are too visually similar: {differing} differing px"
    )
