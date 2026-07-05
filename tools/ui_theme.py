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
# Graphite ground. ACCENT is a NEUTRAL bright (chrome active states only, never a
# colour): colour is reserved entirely for pin/net type, via CATEGORY below.
DARK_COLORS = {
    "WIN_BG": "#0f1012", "MAIN_BG": "#17181b", "FG": "#e9eaed", "FG_DIM": "#8b8f97",
    "TITLE_FG": "#ffffff", "CARD_BG": "#1e2024", "BORDER": "#2b2e34",
    "HDR1": "#1e2024", "HDR2": "#17181b", "CHIP_BG": "#23262b", "IN_BG": "#202227",
    "BTN_BG": "#23262b", "BTN_HOVER": "#2b2e34", "BTN_BORDER": "#34383f",
    "ACCENT": "#d6d8dc",   # neutral bright — chrome active only (no colour)
    "TREE_BG": "#1a1b1f", "TREE_ALT": "#1e2024",
    "SEL_BG": "#2b2e34", "SEL_FG": "#ffffff", "HOVER_BG": "#23262b",
    "SEC_BG": "#1e2024", "SEC_FG": "#b6bac1", "LOG_BG": "#131417", "LOG_FG": "#b9bcc3",
    "SCROLL": "#34383f", "SCROLL_HOVER": "#454a52", "ST_BG": "#17181b", "ST_FG": "#8b8f97",
    "PROG_BG": "#202227", "PROG1": "#4a4e56", "PROG2": "#d6d8dc",
    "TAB_BG": "#1e2024", "TAB_SEL_BG": "#26292f", "TAB_SEL_FG": "#ffffff",
    "MENU_BG": "#1e2024", "MENU_SEL": "#26292f", "CHK_BG": "#202227", "CHK_ON": "#d6d8dc",
    "DOT_IDLE": "#4a4e56",
}
LIGHT_COLORS = {
    "WIN_BG": "#f4f6f4", "MAIN_BG": "#eceeec", "FG": "#1b1e1c", "FG_DIM": "#6b7069",
    "TITLE_FG": "#101210", "CARD_BG": "#ffffff", "BORDER": "#dde2df",
    "HDR1": "#ffffff", "HDR2": "#f1f3f1", "CHIP_BG": "#eceeec", "IN_BG": "#ffffff",
    "BTN_BG": "#f4f6f4", "BTN_HOVER": "#eceeec", "BTN_BORDER": "#dbe0dc",
    "ACCENT": "#2c302d",   # neutral bright — chrome active only (no colour)
    "TREE_BG": "#ffffff", "TREE_ALT": "#f6f8f6",
    "SEL_BG": "#e4e8e4", "SEL_FG": "#101210", "HOVER_BG": "#f1f3f1",
    "SEC_BG": "#f4f6f4", "SEC_FG": "#545953", "LOG_BG": "#fafbfa", "LOG_FG": "#333934",
    "SCROLL": "#d2d7d3", "SCROLL_HOVER": "#b6bcb7", "ST_BG": "#eceeec", "ST_FG": "#6b7069",
    "PROG_BG": "#eceeec", "PROG1": "#adb3ae", "PROG2": "#2c302d",
    "TAB_BG": "#eef0ee", "TAB_SEL_BG": "#ffffff", "TAB_SEL_FG": "#101210",
    "MENU_BG": "#ffffff", "MENU_SEL": "#f1f3f1", "CHK_BG": "#ffffff", "CHK_ON": "#2c302d",
    "DOT_IDLE": "#adb3ae",
}

# Categorical pin/net-type palette — one muted family (≈L58 S30) on graphite, so
# the hue reads clearly on pins and nets without ever feeling loud. This is the
# ONLY place colour lives; UI chrome stays neutral graphite (never tinted).
CATEGORY = {
    "power":    "#c6a366",   # power rails: VTARGET / VDDA / VREF / VBAT
    "ground":   "#7f8b9a",   # returns: GND / VSSA
    "core":     "#a98cc0",   # core cap: VCAP
    "service":  "#77a688",   # service nets: OSC / NRST / BOOT0 / debug
    "lane":     "#6f93b5",   # default IO lane: CARD_LANE
    "must":     "#c9736c",   # must-switch class
    "osc":      "#c99f5e",   # oscillator class
    "fixed":    "#8b8f97",   # fixed / neutral
    "breakout": "#6f93b5",   # extraction breakout
    "fivev":    "#5fa393",   # 5V-tolerant
}


def cat(name: str, fallback: str = "#8b8f97") -> str:
    """A pin/net-type colour by category name."""
    return CATEGORY.get(name, fallback)

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


# ── Design tokens (Fluent-grounded) ──────────────────────────────────────────
# The single source the QFluentWidgets bridge + kit widgets read. 4px spacing
# ramp; a semantic type ramp (role → point-size, weight); a small radius scale;
# and desaturated semantic status — the ONLY sanctioned hue besides the neutral
# ACCENT. See docs/design/2026-07-04-app-design-overhaul.md §3.
SPACING = {"xs": 4, "s": 8, "m": 12, "l": 16, "xl": 24, "xxl": 32, "xxxl": 48}

RADIUS = {"control": 4, "card": 6, "pill": 999, "pin": 2}

# role → (point size, weight name). UI prose = Geist; data/identifiers = mono.
TYPE = {
    "display":  (20, "demibold"),
    "title":    (15, "demibold"),
    "subtitle": (12, "semibold"),
    "body":     (10, "normal"),
    "caption":  (8.5, "normal"),
    "overline": (8, "semibold"),
    "data":     (9.5, "medium"),
}

# desaturated semantic status (dark, light) — real state only (ok/warn/err).
STATUS = {
    "ok":   ("#6f8f6a", "#4a7a44"),
    "warn": ("#b8964a", "#8a6a2a"),
    "err":  ("#b96a63", "#9a4a44"),
}

# one soft shadow, for true overlays only (menus, callouts, dialogs).
ELEVATION_OVERLAY = "0 6px 16px rgba(0,0,0,0.28)"


def sp(name: str, fallback: int = 8) -> int:
    """A step from the 4px spacing ramp."""
    return SPACING.get(name, fallback)


def radius(name: str, fallback: int = 4) -> int:
    return RADIUS.get(name, fallback)


def type_role(name: str):
    """(point_size, weight_name) for a type-ramp role."""
    return TYPE.get(name, TYPE["body"])


def status(kind: str) -> str:
    """A desaturated semantic colour ('ok'|'warn'|'err') for the active theme."""
    return STATUS.get(kind, STATUS["ok"])[0 if is_dark() else 1]


# ── Fonts ────────────────────────────────────────────────────────────────────
UI_FONT_STACK = ("Space Grotesk", "Geist", "Inter", "Segoe UI Variable Text", "Segoe UI")
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
