"""ui_theme.py — the ONE design system shared by every tab: theme tokens, the
active-theme accessor, bundled fonts, and the Lucide icon loader.

LibraryManager (the shell), kicad_tools, and stm32_pins_tab all import from
here, so the palettes cannot drift apart and no tab needs to import another
tab for its icons (which used to create a circular import)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap, QPainter

try:
    from PyQt5.QtSvg import QSvgRenderer
    HAVE_QTSVG = True
except Exception:  # pragma: no cover
    HAVE_QTSVG = False

# ── Theme tokens ─────────────────────────────────────────────────────────────
DARK_COLORS = {
    "WIN_BG": "#1a1a1c", "MAIN_BG": "#151517", "FG": "#ededf0", "FG_DIM": "#90909a",
    "TITLE_FG": "#ffffff", "CARD_BG": "#212124", "BORDER": "#33333a",
    "HDR1": "#212124", "HDR2": "#1a1a1c", "CHIP_BG": "#26262b", "IN_BG": "#1c1c1f",
    "BTN_BG": "#26262b", "BTN_HOVER": "#2f2f35", "BTN_BORDER": "#37373e",
    "ACCENT": "#d0d0d6", "TREE_BG": "#1c1c1f", "TREE_ALT": "#202024",
    "SEL_BG": "#33333c", "SEL_FG": "#ffffff", "HOVER_BG": "#26262b",
    "SEC_BG": "#212124", "SEC_FG": "#b8b8c0", "LOG_BG": "#151517", "LOG_FG": "#c0c0c8",
    "SCROLL": "#37373e", "SCROLL_HOVER": "#4a4a52", "ST_BG": "#151517", "ST_FG": "#90909a",
    "PROG_BG": "#1c1c1f", "PROG1": "#55555e", "PROG2": "#d0d0d6",
    "TAB_BG": "#212124", "TAB_SEL_BG": "#2a2a30", "TAB_SEL_FG": "#ffffff",
    "MENU_BG": "#212124", "MENU_SEL": "#2a2a30", "CHK_BG": "#1c1c1f", "CHK_ON": "#d0d0d6",
    "DOT_IDLE": "#55555e",
}
LIGHT_COLORS = {
    "WIN_BG": "#f7f7f6", "MAIN_BG": "#efefee", "FG": "#26262b", "FG_DIM": "#85858c",
    "TITLE_FG": "#101014", "CARD_BG": "#ffffff", "BORDER": "#ececea",
    "HDR1": "#ffffff", "HDR2": "#f2f2f0", "CHIP_BG": "#efefed", "IN_BG": "#ffffff",
    "BTN_BG": "#f7f7f6", "BTN_HOVER": "#efefee", "BTN_BORDER": "#e2e2df",
    "ACCENT": "#2a2a30", "TREE_BG": "#ffffff", "TREE_ALT": "#f7f7f6",
    "SEL_BG": "#e6e6e2", "SEL_FG": "#101014", "HOVER_BG": "#f2f2f0",
    "SEC_BG": "#f7f7f6", "SEC_FG": "#56565c", "LOG_BG": "#fafafa", "LOG_FG": "#33333a",
    "SCROLL": "#d4d4d0", "SCROLL_HOVER": "#b8b8b4", "ST_BG": "#efefee", "ST_FG": "#85858c",
    "PROG_BG": "#efefee", "PROG1": "#b0b0ac", "PROG2": "#2a2a30",
    "TAB_BG": "#f0f0ee", "TAB_SEL_BG": "#ffffff", "TAB_SEL_FG": "#101014",
    "MENU_BG": "#ffffff", "MENU_SEL": "#f2f2f0", "CHK_BG": "#ffffff", "CHK_ON": "#2a2a30",
    "DOT_IDLE": "#b0b0ac",
}

# The active theme dict. LIGHT is the app default; the main window's
# _apply_theme calls set_theme() and every custom-painted widget reads tc().
_ACTIVE: Dict[str, str] = dict(LIGHT_COLORS)
_IS_DARK = False


def set_theme(dark: bool) -> Dict[str, str]:
    """Swap the active token set. Returns the active dict."""
    global _ACTIVE, _IS_DARK
    _ACTIVE = dict(DARK_COLORS if dark else LIGHT_COLORS)
    _IS_DARK = bool(dark)
    return _ACTIVE


def is_dark() -> bool:
    return _IS_DARK


def theme() -> Dict[str, str]:
    """The active token dict."""
    return _ACTIVE


def tc(key: str, fallback: str = "#888888") -> str:
    """One themed colour by token name."""
    return _ACTIVE.get(key, fallback)


# ── Fonts ────────────────────────────────────────────────────────────────────
UI_FONT_STACK = ("Geist", "Inter", "Segoe UI Variable Text", "Segoe UI")
MONO_FONT_STACK = ("JetBrains Mono", "Cascadia Mono", "Consolas")


def resource_path(name: str) -> Path:
    """Bundled resource next to the tools, tolerant of frozen (PyInstaller) mode."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent / name


def load_bundled_fonts() -> bool:
    """Register the bundled TTFs (Geist/Inter/JetBrains Mono). Requires a
    QApplication to already exist."""
    from PyQt5.QtGui import QFontDatabase
    loaded = False
    try:
        fdir = resource_path("fonts")
        if fdir.exists():
            for ttf in sorted(fdir.glob("*.ttf")):
                if QFontDatabase.addApplicationFont(str(ttf)) != -1:
                    loaded = True
    except Exception:
        pass
    return loaded


# ── Lucide icons (https://lucide.dev, MIT) ──────────────────────────────────
# Grayscale UI chrome: icons carry no colour; only the pin/data visuals are
# coloured. The semantic names stay so call sites read meaningfully.
LUCIDE_NEUTRAL = "#8b8b91"
LUCIDE_BLUE = "#8b8b91"
LUCIDE_GREEN = "#8b8b91"
LUCIDE_RED = "#8b8b91"
LUCIDE_AMBER = "#8b8b91"

_LUCIDE_CACHE: Dict[tuple, QIcon] = {}


def lucide_icon(name: str, color: str = LUCIDE_NEUTRAL, size: int = 18) -> QIcon:
    """Render a bundled Lucide SVG tinted to `color` as a QIcon."""
    key = (name, color, size)
    if key in _LUCIDE_CACHE:
        return _LUCIDE_CACHE[key]
    icon = QIcon()
    if HAVE_QTSVG:
        try:
            svg = resource_path(f"lucide/{name}.svg").read_text(encoding="utf-8")
            svg = svg.replace("currentColor", color)
            renderer = QSvgRenderer(bytearray(svg, encoding="utf-8"))
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            renderer.render(p)
            p.end()
            icon = QIcon(pm)
        except Exception:
            icon = QIcon()
    _LUCIDE_CACHE[key] = icon
    return icon
