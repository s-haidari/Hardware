"""ui.theme — the one design system, ported from the approved mockup.

Grayscale Windows 11 chrome using the EXACT WinUI SolidBackgroundFillColor ladder;
colour is reserved for pin/net data (CATEGORY) and pass/fail status. Two themes,
both first-class. Fusion + a full QSS so no native light control ever leaks into
the dark theme.

Typography: Segoe UI Variable for the interface, Cascadia Mono for machine data
(both ship on Windows 11; no bundling). Regular / Semibold only.

Everything reads tokens from the active dict, so a retheme is one set_theme() call
plus re-applying qss() to the shell.
"""
from __future__ import annotations

from typing import Dict

from PyQt5.QtGui import QFont, QColor

# ── token ladders (exact WinUI values, matching the mockup) ──────────────────
DARK: Dict[str, str] = {
    "base": "#202020", "nav": "#1c1c1c", "surface": "#282828", "card": "#2c2c2c",
    "card_hover": "#323232", "inset": "#1c1c1c",
    "txt1": "#ffffff", "txt2": "rgba(255,255,255,0.773)", "txt3": "rgba(255,255,255,0.529)",
    "divider": "rgba(255,255,255,0.09)", "stroke": "rgba(255,255,255,0.06)",
    "ctl": "rgba(255,255,255,0.06)", "ctl_hover": "rgba(255,255,255,0.09)",
    "tok": "rgba(255,255,255,0.08)", "subtle_hover": "rgba(255,255,255,0.055)",
    "accent": "#ededed", "on_accent": "#1a1a1a",
    "ok": "#6ccb5f", "warn": "#e8c245", "err": "#ff99a4", "info": "#8ab4e8",
    "ok_bg": "rgba(108,203,95,0.12)", "warn_bg": "rgba(232,194,69,0.12)", "err_bg": "rgba(255,153,164,0.12)",
    "seg1": "rgba(255,255,255,0.82)", "seg2": "rgba(255,255,255,0.5)", "seg3": "rgba(255,255,255,0.3)",
}
LIGHT: Dict[str, str] = {
    "base": "#f3f3f3", "nav": "#eeeeee", "surface": "#f9f9f9", "card": "#ffffff",
    "card_hover": "#f0f0f0", "inset": "#eeeeee",
    "txt1": "rgba(0,0,0,0.894)", "txt2": "rgba(0,0,0,0.62)", "txt3": "rgba(0,0,0,0.447)",
    "divider": "rgba(0,0,0,0.08)", "stroke": "rgba(0,0,0,0.07)",
    "ctl": "rgba(0,0,0,0.03)", "ctl_hover": "rgba(0,0,0,0.05)",
    "tok": "rgba(0,0,0,0.055)", "subtle_hover": "rgba(0,0,0,0.04)",
    "accent": "#1b1b1b", "on_accent": "#ffffff",
    "ok": "#0f7b0f", "warn": "#9d5d00", "err": "#c42b1c", "info": "#005fb8",
    "ok_bg": "rgba(15,123,15,0.10)", "warn_bg": "rgba(157,93,0,0.10)", "err_bg": "rgba(196,43,28,0.09)",
    "seg1": "rgba(0,0,0,0.78)", "seg2": "rgba(0,0,0,0.45)", "seg3": "rgba(0,0,0,0.26)",
}

# ── category palette — the ONLY hue, on pin/net data (both themes) ───────────
CATEGORY_DARK = {
    "power": "#e0a458", "ground": "#8a97a8", "core": "#b692e6", "service": "#71be93",
    "lane": "#54b3c6", "must": "#ea786e", "osc": "#e88c42", "fixed": "#9aa0aa",
    "breakout": "#54b3c6", "fivev": "#71be93",
}
CATEGORY_LIGHT = {
    "power": "#b26b12", "ground": "#5e6b7d", "core": "#7e52c0", "service": "#2e8b57",
    "lane": "#1d8296", "must": "#c7443a", "osc": "#c56a16", "fixed": "#6b717b",
    "breakout": "#1d8296", "fivev": "#2e8b57",
}

UI_STACK = '"Segoe UI Variable Text","Segoe UI Variable","Segoe UI","Inter",sans-serif'
UI_DISPLAY = '"Segoe UI Variable Display","Segoe UI Variable","Segoe UI",sans-serif'
MONO_STACK = '"Cascadia Mono","Cascadia Code","Consolas",monospace'
_UI_FAMILIES = ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter")
_MONO_FAMILIES = ("Cascadia Mono", "Cascadia Code", "Consolas")

_active = dict(DARK)
_is_dark = True


def set_theme(dark: bool) -> Dict[str, str]:
    global _active, _is_dark
    _active = dict(DARK if dark else LIGHT)
    _is_dark = bool(dark)
    return _active


def is_dark() -> bool:
    return _is_dark


def tokens() -> Dict[str, str]:
    return _active


def t(key: str, fallback: str = "#808080") -> str:
    return _active.get(key, fallback)


def category(name: str, fallback: str = "#8a8f97") -> str:
    pal = CATEGORY_DARK if _is_dark else CATEGORY_LIGHT
    return pal.get(name, fallback)


def qcolor(key_or_hex: str) -> QColor:
    v = _active.get(key_or_hex, key_or_hex)
    if v.startswith("rgba"):
        nums = v[v.index("(") + 1:v.index(")")].split(",")
        r, g, b = (int(float(x)) for x in nums[:3])
        a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
        return QColor(r, g, b, a)
    return QColor(v)


def _family(families) -> str:
    """First installed family from a stack (fallback: the first name)."""
    try:
        from PyQt5.QtGui import QFontDatabase
        have = set(QFontDatabase().families())
        for fam in families:
            if fam in have:
                return fam
    except Exception:  # noqa: BLE001
        pass
    return families[0]


def ui_font(size: float = 10, semibold: bool = False) -> QFont:
    f = QFont(_family(_UI_FAMILIES))
    f.setPointSizeF(size)
    f.setWeight(QFont.DemiBold if semibold else QFont.Normal)
    return f


def mono_font(size: float = 9.5, semibold: bool = False) -> QFont:
    f = QFont(_family(_MONO_FAMILIES))
    f.setPointSizeF(size)
    f.setWeight(QFont.DemiBold if semibold else QFont.Normal)
    return f


def load_fonts(app) -> None:
    """Register bundled TTFs (if any) and set the base app font to the UI face."""
    try:
        import glob
        from pathlib import Path
        from PyQt5.QtGui import QFontDatabase
        for ttf in glob.glob(str(Path(__file__).resolve().parent.parent / "fonts" / "*.ttf")):
            QFontDatabase.addApplicationFont(ttf)
    except Exception:  # noqa: BLE001
        pass
    f = ui_font(10)
    app.setFont(f)


def qss(dark: bool | None = None) -> str:
    """Full stylesheet for the Fusion base widgets (buttons, inputs, combos,
    tables, headers, scrollbars, tooltips, menus). Object-name hooks let the
    widget kit request specific roles (surface, card, nav, primary, ...)."""
    if dark is not None:
        set_theme(dark)
    c = _active
    return f"""
* {{ outline: none; }}
QWidget {{ color: {c['txt1']}; font-family: {UI_STACK}; font-size: 14px; }}
QMainWindow, #shellRoot {{ background: {c['base']}; }}
#navPane {{ background: {c['nav']}; border-right: 1px solid {c['divider']}; }}
#contentArea, QStackedWidget, #workspace {{ background: {c['surface']}; }}
QLabel {{ background: transparent; }}

/* nav items */
#navItem {{ background: transparent; border: none; border-radius: 4px;
    color: {c['txt2']}; text-align: left; padding: 0 12px; }}
#navItem:hover {{ background: {c['subtle_hover']}; }}
#navItem[selected="true"] {{ background: {c['ctl']}; color: {c['txt1']}; font-weight: 600; }}

/* buttons */
QPushButton {{ background: {c['ctl']}; border: 1px solid {c['stroke']}; border-radius: 4px;
    color: {c['txt1']}; padding: 6px 12px; }}
QPushButton:hover {{ background: {c['ctl_hover']}; }}
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent']}; }}
QPushButton#ghost {{ background: transparent; border: none; }}
QPushButton#ghost:hover {{ background: {c['subtle_hover']}; }}
QPushButton#subtab {{ background: transparent; border: none; border-radius: 0;
    color: {c['txt2']}; padding: 6px 14px; }}
QPushButton#subtab:hover {{ color: {c['txt1']}; }}
QPushButton#subtab[selected="true"] {{ color: {c['txt1']}; font-weight: 600;
    border-bottom: 2px solid {c['accent']}; }}
QPushButton#seg {{ background: transparent; border: none; border-radius: 3px;
    color: {c['txt2']}; padding: 4px 12px; font-family: {MONO_STACK}; font-size: 12px; }}
QPushButton#seg[selected="true"] {{ background: {c['card']}; color: {c['txt1']}; font-weight: 600; }}
QPushButton#tokbtn {{ background: {c['tok']}; border: none; border-radius: 4px;
    color: {c['txt1']}; padding: 3px 9px; font-family: {MONO_STACK}; font-size: 12px; }}
QPushButton#tokbtn:hover {{ background: {c['ctl_hover']}; }}

QLineEdit, QPlainTextEdit, QTextEdit {{ background: {c['ctl']}; border: 1px solid {c['stroke']};
    border-radius: 4px; color: {c['txt1']}; padding: 5px 10px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['txt3']}; }}

QComboBox {{ background: {c['ctl']}; border: 1px solid {c['stroke']}; border-radius: 4px;
    color: {c['txt1']}; padding: 5px 10px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['stroke']};
    selection-background-color: {c['ctl_hover']}; color: {c['txt1']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['ctl_hover']}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['ctl_hover']}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {c['card']}; color: {c['txt1']}; border: 1px solid {c['stroke']};
    border-radius: 4px; padding: 4px 8px; }}

QTableWidget, QTableView {{ background: {c['card']}; alternate-background-color: {c['card']};
    gridline-color: transparent; border: 1px solid {c['stroke']}; border-radius: 8px;
    color: {c['txt2']}; }}
QTableWidget::item {{ padding: 4px 10px; border: none; }}
QTableWidget::item:selected {{ background: {c['ctl']}; color: {c['txt1']}; }}
QHeaderView::section {{ background: {c['card']}; color: {c['txt2']}; border: none;
    border-bottom: 1px solid {c['divider']}; padding: 6px 10px; font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['card']}; border: none; }}
"""
