"""Convergence Phase 0 · ui.theme.opaque() + the stm32_pins_tab re-point off ui_theme.

opaque() resolves any token to an OPAQUE '#rrggbb' (compositing a translucent rgba
token over 'canvas') — the one helper the retired ui_theme shim provided that
stm32_pins_tab's SVG generator needs: a raw rgba token interpolated into an SVG
fill=/stroke= attribute would void it (the plan's top hazard). These tests pin that
invariant and prove the re-point kept every stm32 colour global opaque in both themes.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from ui import theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def test_opaque_returns_rrggbb_for_every_surface_token_both_themes():
    toks = ["raised", "inset", "txt1", "txt2", "txt3", "hairline", "nav", "tok",
            "accent", "hairline_strong", "canvas"]
    for dark in (True, False):
        T.set_theme(dark)
        for tok in toks:
            v = T.opaque(tok)
            assert _HEX.match(v), f"opaque({tok!r}) dark={dark} -> {v!r} not #rrggbb"
    T.set_theme(True)


def test_opaque_composites_translucent_tokens():
    T.set_theme(True)
    # dark 'tok' is rgba(255,255,255,0.08) — must composite over canvas to opaque hex.
    assert T.t("tok").startswith("rgba"), "precondition: tok is translucent in dark"
    assert _HEX.match(T.opaque("tok")), "translucent token did not composite to #rrggbb"


def test_opaque_dark_arg_picks_theme_and_restores_active():
    T.set_theme(True)
    dark_val = T.opaque("txt1", dark=True)
    light_val = T.opaque("txt1", dark=False)
    assert dark_val != light_val, "opaque(dark=...) must resolve per the requested theme"
    assert T.is_dark() is True, "opaque(dark=...) must RESTORE the active theme afterwards"


def test_stm32_colour_globals_are_all_opaque_after_theme_swap():
    # The re-point off ui_theme must keep every value the pin-map SVG interpolates
    # an OPAQUE #rrggbb — a regression to a raw rgba token would silently void fills.
    import stm32_pins_tab as pins
    for dark in (True, False):
        pins.set_tab_theme(dark)
        for name in ("_PANEL", "_CARD", "_TXT", "_MUT", "_LINE", "_BODY", "_CHIP",
                     "_FAINT", "_ACCENT", "_T_MUST", "_T_OSC", "_T_FIXED", "_T_SEL",
                     "_BREAKOUT_COLOR"):
            v = getattr(pins, name)
            assert _HEX.match(v), f"stm32 {name} dark={dark} -> {v!r} not opaque #rrggbb"
        for k, v in pins._SWITCH_COLOR.items():
            assert _HEX.match(v), f"stm32 _SWITCH_COLOR[{k}] dark={dark} -> {v!r} not opaque"
    pins.set_tab_theme(False)                # leave the app default (light) as found
