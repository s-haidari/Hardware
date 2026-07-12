"""ui.theme low-level helpers: qcolor() hex normalisation, translucent-surface
contrast compositing, _family() memoisation, and #seg using the UI face (not mono)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def test_qcolor_bare_hex_is_normalised_not_black():
    # Bare (no '#') 6/3/8-digit hex must resolve, NOT collapse to invalid opaque black.
    c = T.qcolor("808080")
    assert c.isValid()
    assert (c.red(), c.green(), c.blue()) == (128, 128, 128)
    # Prefixed hex still works and agrees.
    assert T.qcolor("#808080").getRgb() == c.getRgb()
    # 3-digit and 8-digit bare hex too (Qt reads 8-digit '#' hex as #AARRGGBB).
    assert T.qcolor("fff").isValid() and T.qcolor("fff").getRgb()[:3] == (255, 255, 255)
    c8 = T.qcolor("80ff0000")            # AA=80, RR=ff, GG=00, BB=00
    assert c8.isValid() and c8.red() == 255 and c8.alpha() == 128
    assert c8.getRgb() == T.qcolor("#80ff0000").getRgb()


def test_qcolor_named_and_token_still_resolve():
    assert T.qcolor("transparent").getRgb() == (0, 0, 0, 0)  # named colour, alpha 0
    T.set_theme(True)
    # a token key resolves through _active
    tok = T.qcolor("accent")
    assert tok.isValid() and tok.getRgb()[:3] == (0xF3, 0xF3, 0xF3)
    # an rgba(...) token parses alpha
    ct = T.qcolor("txt2")  # rgba(244,244,244,0.66) in dark
    assert ct.isValid() and 0 < ct.alpha() < 255


def test_qcolor_invalid_logs_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        c = T.qcolor("not-a-colour")
    assert not c.isValid()
    assert any("qcolor" in r.message for r in caplog.records)


def test_category_contrast_composites_translucent_surface():
    # LIGHT 'hairline' is rgba(0,0,0,0.08) — a translucent wash over 'canvas', NOT black.
    T.set_theme(False)
    got = T.category_contrast("power", "hairline")
    # Reference: composite hairline over canvas by hand, then WCAG ratio.
    def lum(qc):
        def lin(v):
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * lin(qc.red()) + 0.7152 * lin(qc.green()) + 0.0722 * lin(qc.blue())
    top = T.qcolor(T.t("hairline")); base = T.qcolor(T.t("canvas"))
    a = top.alpha() / 255.0
    from PyQt5.QtGui import QColor
    comp = QColor(round(top.red()*a + base.red()*(1-a)),
                  round(top.green()*a + base.green()*(1-a)),
                  round(top.blue()*a + base.blue()*(1-a)))
    fg = lum(T.qcolor(T.category("power"))) + 0.05
    bg = lum(comp) + 0.05
    expected = max(fg, bg) / min(fg, bg)
    assert abs(got - expected) < 1e-9
    # And it is decisively different from the old fabricated-vs-black number (~3.84).
    assert abs(got - 3.84) > 0.3
    T.set_theme(True)


def test_category_contrast_opaque_surface_unchanged():
    # For an opaque surface the compositing branch is a no-op; still a sane ratio.
    for dark in (True, False):
        T.set_theme(dark)
        c = T.category_contrast("power", "canvas")
        assert c > 1.0
    T.set_theme(True)


def test_family_is_memoised_and_invalidated_on_load_fonts(monkeypatch):
    T._family_cache.clear()
    calls = {"n": 0}
    import PyQt5.QtGui as G
    real = G.QFontDatabase

    class Counting(real):
        def families(self, *a, **k):
            calls["n"] += 1
            return super().families(*a, **k)

    monkeypatch.setattr(G, "QFontDatabase", Counting)
    fam1 = T._family(T._UI_FAMILIES)
    fam2 = T._family(T._UI_FAMILIES)
    assert fam1 == fam2
    assert calls["n"] == 1  # second call served from cache, no re-enumeration

    # A different stack enumerates once more (its own cache slot).
    T._family(T._MONO_FAMILIES)
    assert calls["n"] == 2

    # load_fonts() invalidates the cache (bundled TTFs may change the installed set),
    # then repopulates the UI slot via its own ui_font(10) call -> exactly one more
    # enumeration. Proves the clear happened (a warm cache would have added zero).
    class _App:
        def setFont(self, *_):
            pass
    before = calls["n"]
    T.load_fonts(_App())
    assert calls["n"] == before + 1


def test_seg_labels_use_ui_face_not_mono():
    css = T.qss(True)
    # locate the #seg base rule block and assert it carries the UI stack, not mono.
    marker = "QPushButton#seg {"
    i = css.index(marker)
    block = css[i:css.index("}", i)]
    assert T.UI_STACK in block
    assert T.MONO_STACK not in block


def test_qss_no_arg_reads_active_theme_single_source():
    # The shell flips the global via set_theme(dark) then calls qss() with NO arg,
    # so qss() must render whatever theme is active — no second flip needed.
    T.set_theme(False)                       # light active
    light_css = T.qss()
    assert T.LIGHT["base"] in light_css and T.DARK["base"] not in light_css
    T.set_theme(True)                        # dark active
    dark_css = T.qss()
    assert T.DARK["base"] in dark_css and T.LIGHT["base"] not in dark_css


def test_qss_no_arg_does_not_mutate_active_theme():
    # Inspecting qss() with no arg must NOT flip the process-global active theme
    # (only the explicit qss(dark) back-compat path may, per the docstring note).
    T.set_theme(True)
    before = T.is_dark()
    T.qss()
    assert T.is_dark() == before             # unchanged
    # By contrast, the explicit-arg back-compat path DOES mutate the global.
    T.qss(False)
    assert T.is_dark() is False
    T.set_theme(True)                        # restore dark default
