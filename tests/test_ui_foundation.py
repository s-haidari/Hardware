"""Phase-A Refined-Neutral foundation: monotonic elevation ladder, radius tokens,
WCAG-AA contrast, fixed type scale, de-letterspaced eyebrow, borderless surfaces."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtGui import QColor, QFont  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.theme as T  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


# ── colour helpers (composite alpha over the opaque surface, then WCAG) ────────
def _grey(token: str) -> float:
    """Average 0-255 channel value of an opaque token (ladder colours are neutral)."""
    c = W._qcolor(token)
    return (c.red() + c.green() + c.blue()) / 3.0


def _srgb_lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(r: float, g: float, b: float) -> float:
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def _composite(fg: QColor, bg: QColor):
    a = fg.alpha() / 255.0
    return tuple(round(f * a + b * (1 - a))
                 for f, b in ((fg.red(), bg.red()), (fg.green(), bg.green()), (fg.blue(), bg.blue())))


def _contrast(fg_token: str, bg_token: str) -> float:
    fg, bg = W._qcolor(T.t(fg_token)), W._qcolor(T.t(bg_token))
    r, g, b = _composite(fg, bg)
    l1 = _lum(r, g, b) + 0.05
    l2 = _lum(bg.red(), bg.green(), bg.blue()) + 0.05
    return max(l1, l2) / min(l1, l2)


# ── ladder monotonicity ───────────────────────────────────────────────────────
def test_base_ladder_strictly_increases_both_themes():
    for dark in (True, False):
        T.set_theme(dark)
        vals = [_grey(T.t(k)) for k in T.ELEVATION]   # nav, canvas, raised
        assert vals == sorted(vals) and len(set(vals)) == len(vals), \
            f"ladder not strictly increasing ({'dark' if dark else 'light'}): {vals}"
    T.set_theme(True)


def test_inset_is_a_distinct_lift_from_raised():
    for dark in (True, False):
        T.set_theme(dark)
        assert abs(_grey(T.t("inset")) - _grey(T.t("raised"))) >= 4, \
            "inset must read as a distinct grouped/active step from raised"
    T.set_theme(True)


def test_ladder_is_zero_hue_neutral():
    # zero hue shift: R, G, B within a tight band (keeps the WinUI-grey character)
    for dark in (True, False):
        T.set_theme(dark)
        for k in ("nav", "canvas", "raised", "inset"):
            c = W._qcolor(T.t(k))
            assert max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) <= 8, \
                f"{k} is not neutral in {'dark' if dark else 'light'}"
    T.set_theme(True)


# ── radius tokens ──────────────────────────────────────────────────────────────
def test_radius_tokens():
    assert T.RADIUS_CONTAINER == 8 and T.RADIUS_CONTROL == 6
    assert T.radius("container") == 8 and T.radius("control") == 6
    assert T.radius("nope") == 8       # container default


# ── WCAG-AA contrast on every text tier × surface ──────────────────────────────
def test_text_tiers_meet_wcag_on_every_surface():
    for dark in (True, False):
        T.set_theme(dark)
        for surf in ("canvas", "raised", "inset"):
            assert _contrast("txt1", surf) >= 4.5, (dark, surf, "txt1")
            assert _contrast("txt2", surf) >= 4.5, (dark, surf, "txt2")
            assert _contrast("txt3", surf) >= 3.0, (dark, surf, "txt3")  # micro-label tier
    T.set_theme(True)


# ── fixed type scale ───────────────────────────────────────────────────────────
def test_type_scale_locks_sizes_and_weights():
    for role in ("hero", "stat", "payload", "group_subhead", "value",
                 "section", "detail_key", "footnote"):
        f = T.scale_font(role)
        size, semibold, mono = T.TYPE_SCALE[role]
        assert abs(f.pointSizeF() - size) < 0.01, role
        # Regular + Semibold only — never Bold/Light/Medium
        assert f.weight() in (QFont.Normal, QFont.DemiBold), role
        assert (f.weight() == QFont.DemiBold) == semibold, role


def test_type_scale_rejects_improvised_roles():
    import pytest
    with pytest.raises(KeyError):
        T.scale_font("jumbo")


def test_mono_font_is_monospace_for_tabular_alignment():
    f = T.mono_font(10)
    assert f.styleStrategy() == QFont.PreferQuality
    # a mono face resolves (bundled Geist/JetBrains guarantee it off-Windows)
    from PyQt5.QtGui import QFontInfo
    assert QFontInfo(f).fixedPitch() or f.family() in \
        ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Geist Mono")


# ── qss consumes the radius + hairline tokens ──────────────────────────────────
def test_qss_uses_two_deliberate_radii():
    css = T.qss(True)
    assert f"border-radius: {T.RADIUS_CONTROL}px" in css   # controls
    assert f"border-radius: {T.RADIUS_CONTAINER}px" in css # containers
    # 4px flat radius is retired everywhere
    assert "border-radius: 4px" not in css
    assert "border-radius: 3px" not in css


# ── de-letterspaced eyebrow (the #1 AI tell, design-rules §1.4) ─────────────────
def test_eyebrow_has_zero_letterspacing_and_preserves_case():
    lab = W.eyebrow("Connection Diagram")
    f = lab.font()
    # No tracking: an untouched font reports 0.0 (never set); the old eyebrow forced
    # 106%. Anything > 100% is added letterspacing (the retired AI tell).
    assert f.letterSpacing() in (0.0, 100.0), f.letterSpacing()
    assert lab.text() == "Connection Diagram"        # NOT upper-cased
    assert f.weight() == QFont.DemiBold
    _destroy(lab)


def test_no_setletterspacing_call_sites_remain():
    # eyebrow was the app's ONLY setLetterSpacing; assert none linger in the kit.
    src = (Path(__file__).resolve().parents[1] / "tools" / "ui" / "widgets.py").read_text(encoding="utf-8")
    assert "setLetterSpacing" not in src


# ── borderless elevation (design-rules §1.2/§5) ────────────────────────────────
def test_card_is_borderless():
    c = W.Card()
    css = c.styleSheet().replace(" ", "")
    assert "border:none" in css or "border-width:0" in css
    assert f"border-radius:{T.RADIUS_CONTAINER}px" in css
    _destroy(c)


def test_verdict_band_is_borderless():
    v = W.Verdict("Ready", "All checks passed", kind="ok")
    assert "border:none" in v.styleSheet().replace(" ", "")
    _destroy(v)


# ── verdict status dot (color on the smallest element, per design-rules §1.1) ──
def test_verdict_shows_status_dot_colored_by_kind():
    # The band carries its verdict via a small leading dot tinted by `kind`,
    # never by tinting the neutral surface.
    for kind in ("ok", "warn", "err"):
        v = W.Verdict("Status", "detail", kind=kind)
        assert v._dot is not None, f"kind={kind} must render a status dot"
        css = v._dot.styleSheet().replace(" ", "")
        assert f"background:{T.t(kind)}" in css, f"dot must use the {kind} token"
        # dot is a genuine dot: round, not a square block
        assert "border-radius:4px" in css
        # the surface stays neutral (no category hue on the band background)
        assert T.t(kind) not in v.styleSheet().replace(" ", "")
        _destroy(v)


def test_verdict_plain_has_no_status_dot():
    # plain=True keeps the band as neutral chrome: no leading dot at all.
    v = W.Verdict("Package Not Buildable", "reason", kind="warn", plain=True)
    assert v._dot is None
    _destroy(v)


def test_verdict_unknown_kind_falls_back_to_muted_dot():
    # An unrecognised kind still gets a dot, tinted with the muted token — the
    # dot is always present (unless plain), never crashing on an odd kind.
    v = W.Verdict("Status", kind="mystery")
    assert v._dot is not None
    assert f"background:{T.t('txt3')}" in v._dot.styleSheet().replace(" ", "")
    _destroy(v)


# ── motion wired into the shell / Workspace (headless = instant) ───────────────
def test_workspace_subtab_selection_still_works_with_sliding_underline():
    import ui.motion as M
    M.set_reduced_motion(True)                     # headless determinism
    panels = [("First", lambda ctx: W.body("one")),
              ("Second", lambda ctx: W.body("two"))]
    ws = W.Workspace(ctx=None, title="Demo", panels=panels)
    ws.select_panel("Second")
    # the underline exists and tracks selection without raising
    assert hasattr(ws, "_underline")
    _destroy(ws)


def test_single_panel_workspace_has_no_underline():
    # a lone-panel Workspace shows no subtab bar, so no underline is created.
    ws = W.Workspace(ctx=None, title="Solo", panels=[("Only", lambda ctx: W.body("x"))])
    assert ws._underline is None
    _destroy(ws)
