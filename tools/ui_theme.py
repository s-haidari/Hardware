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
# Quiet Instrument (Vercel/Geist + Linear): three near-black steps, hierarchy from
# weight + opacity. Chrome is PURE GRAYSCALE — the accent is a neutral bright (an
# inverted "ink" for the primary action / selection / focus); colour is reserved
# entirely for pin and net data.
DARK_COLORS = {
    "WIN_BG": "#0B0C0E", "MAIN_BG": "#131519", "FG": "#ECEEF1", "FG_DIM": "#9AA0AA",
    "FG_FAINT": "#656B75",                              # third text tier (micro / dormant)
    "TITLE_FG": "#ffffff", "CARD_BG": "#1A1D22", "BORDER": "#23262C",
    "HDR1": "#131519", "HDR2": "#0B0C0E", "CHIP_BG": "#1A1D22", "IN_BG": "#16181D",
    "BTN_BG": "#22262D", "BTN_HOVER": "#2B303A", "BTN_BORDER": "#3E4550",
    "ACCENT": "#E7E9EC",   # neutral ink — primary action, selection, focus (never a hue)
    "TREE_BG": "#101216", "TREE_ALT": "#131519",
    "SEL_BG": "#2A2F36", "SEL_FG": "#ffffff", "HOVER_BG": "#1A1D22",
    "SEC_BG": "#131519", "SEC_FG": "#9AA0AA", "LOG_BG": "#0B0C0E", "LOG_FG": "#9AA0AA",
    "SCROLL": "#2A2E35", "SCROLL_HOVER": "#3A3F47", "ST_BG": "#0B0C0E", "ST_FG": "#656B75",
    "PROG_BG": "#1A1D22", "PROG1": "#3A3F47", "PROG2": "#E7E9EC",
    "TAB_BG": "#131519", "TAB_SEL_BG": "#1A1D22", "TAB_SEL_FG": "#ffffff",
    "MENU_BG": "#131519", "MENU_SEL": "#1A1D22", "CHK_BG": "#16181D", "CHK_ON": "#E7E9EC",
    "DOT_IDLE": "#3A3F47",
}
# Windows 11 light: a neutral #F3F3F3 ground with white raised surfaces — the same
# grayscale-ink philosophy as dark (colour stays reserved for pin/net data).
LIGHT_COLORS = {
    "WIN_BG": "#F3F3F3", "MAIN_BG": "#FAFAFA", "FG": "#1A1C1F", "FG_DIM": "#5D6269",
    "FG_FAINT": "#8A8F96",
    "TITLE_FG": "#000000", "CARD_BG": "#FFFFFF", "BORDER": "#E1E3E6",
    "HDR1": "#FFFFFF", "HDR2": "#F3F3F3", "CHIP_BG": "#EEF0F2", "IN_BG": "#FFFFFF",
    "BTN_BG": "#FFFFFF", "BTN_HOVER": "#F2F3F5", "BTN_BORDER": "#D5D8DC",
    "ACCENT": "#1F2328",   # neutral ink — primary action, selection, focus (never a hue)
    "TREE_BG": "#FFFFFF", "TREE_ALT": "#F6F7F8",
    "SEL_BG": "#E4E6EA", "SEL_FG": "#000000", "HOVER_BG": "#F0F1F3",
    "SEC_BG": "#F6F7F8", "SEC_FG": "#5D6269", "LOG_BG": "#FFFFFF", "LOG_FG": "#3A3E44",
    "SCROLL": "#C9CDD2", "SCROLL_HOVER": "#AEB3BA", "ST_BG": "#F3F3F3", "ST_FG": "#8A8F96",
    "PROG_BG": "#E9EBEE", "PROG1": "#C9CDD2", "PROG2": "#1F2328",
    "TAB_BG": "#F3F3F3", "TAB_SEL_BG": "#FFFFFF", "TAB_SEL_FG": "#000000",
    "MENU_BG": "#FFFFFF", "MENU_SEL": "#EDEFF2", "CHK_BG": "#FFFFFF", "CHK_ON": "#1F2328",
    "DOT_IDLE": "#C2C6CC",
}

# Categorical pin/net-type palette — one muted family (≈L58 S30) on graphite, so
# the hue reads clearly on pins and nets without ever feeling loud. This is the
# ONLY place colour lives; UI chrome stays neutral graphite (never tinted).
CATEGORY = {
    "power":    "#D6A44C",   # power rails: VTARGET / VDDA / VREF / VBAT (gold)
    "ground":   "#8B94A1",   # returns: GND / VSSA (slate, recedes)
    "core":     "#AC8DD8",   # core cap: VCAP (violet)
    "service":  "#6FB893",   # service nets: OSC / NRST / BOOT0 / debug (green)
    "lane":     "#57AEBE",   # default IO lane: CARD_LANE (teal, off-blue vs accent)
    "must":     "#E8756B",   # must-switch class (coral, the one-hot hero)
    "osc":      "#E67E33",   # oscillator class (orange, pushed off gold)
    "fixed":    "#767C86",   # fixed / neutral (lowest chroma)
    "breakout": "#57AEBE",   # extraction breakout
    "fivev":    "#6FB893",   # 5V-tolerant
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
# Native Windows: Segoe UI is the platform face (per the app's Windows type guidance);
# a monospace stays for tabular data (refdes / nets / terminals) so columns align.
UI_FONT_STACK = ("Segoe UI Variable Text", "Segoe UI", "Inter", "Geist")
MONO_FONT_STACK = ("Cascadia Mono", "Consolas", "Geist Mono", "JetBrains Mono")


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
