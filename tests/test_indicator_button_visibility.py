"""Owner v2.11 feedback: checkboxes "just turn on and off" with no check mark, and some
buttons are "completely invisible unless you hover" (the collapsible-section chevron next
to Git's Manage, and the faint default buttons). These lock the theme QSS fixes so the
tick/dot and the resting button affordance can't silently regress.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _rule(css: str, selector: str) -> str:
    i = css.index(selector)
    return css[i:i + 200]


def test_checked_checkbox_shows_a_check_mark():
    """The :checked indicator carries a baked check image, not just a flat accent fill."""
    for theme in ("dark", "light"):
        T.set_theme(theme)
        css = T.qss()
        rule = _rule(css, "QCheckBox::indicator:checked")
        assert "image: url(" in rule, f"no check mark on the checked checkbox ({theme})"


def test_checked_radio_shows_a_dot():
    for theme in ("dark", "light"):
        T.set_theme(theme)
        css = T.qss()
        rule = _rule(css, "QRadioButton::indicator:checked")
        assert "image: url(" in rule, f"no dot on the checked radio ({theme})"


def test_indicator_asset_is_actually_baked():
    """The tick PNG is really rendered to disk (not just referenced), so the QSS url
    resolves — the fallback ('' -> flat fill) only kicks in without QtSvg."""
    import ui.icons as icons
    T.set_theme("dark")
    p = T._indicator_png(icons.icon("check"), T._active["on_accent"], 13, "check")
    assert p and Path(p).is_file()


def test_console_chevron_is_not_invisible_at_rest():
    """The collapsible-section chevron (the button next to Git 'Manage') must carry a
    resting box, not a transparent/borderless glyph that only appears on hover."""
    T.set_theme("dark")
    css = T.qss()
    rest = _rule(css, "#consoleChevron {")
    assert "background: transparent" not in rest
    assert "border: none" not in rest


def test_default_button_border_is_visible_at_rest():
    """The base button uses the stronger hairline so a default button reads as a box at
    rest (the 'invisible until hover' complaint), not the faintest stroke."""
    T.set_theme("dark")
    css = T.qss()
    rule = _rule(css, "QPushButton {")
    assert T._active["hairline_strong"] in rule
