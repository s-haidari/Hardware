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

# ── token ladders (Refined-Neutral monotonic elevation, zero hue shift) ──────
# nav (below) < canvas (window/tab) < raised (panels) ; inset = the ONE lift
# (grouped / hover / selected). Legacy key names are kept (so every call site,
# incl. the routing session's, inherits the lift for free) and re-valued.
DARK: Dict[str, str] = {
    # Neutral-glass, ported from the library-v2 mockup: translucent white lifts over a
    # painted ambient gradient (see qss() `grad`). Panels are rgba-white (a real Qt
    # composite over the surface behind) so they read as the mockup's layered glass
    # minus the browser-only backdrop blur. Zero hue shift; colour stays on data + status.
    # Elevation ladder is SOLID neutral hex (nav<canvas<raised<inset) — the mockup's
    # glass pre-composited to opaque panels, so the app keeps its monotonic-ladder +
    # WCAG contract while reading like the mockup. The ambient gradient (qss `grad`)
    # supplies the depth the translucency used to; panels sit opaque on top of it.
    "nav": "#0e0e0e", "base": "#0b0b0b", "surface": "#141414", "canvas": "#141414",
    "card": "#212121", "raised": "#212121",
    "inset": "#2b2b2b", "card_hover": "#2b2b2b",
    "hairline": "rgba(255,255,255,0.08)", "stroke": "rgba(255,255,255,0.08)", "divider": "rgba(255,255,255,0.08)",
    "hairline_strong": "rgba(255,255,255,0.14)",
    "txt1": "#f4f4f4", "txt2": "rgba(244,244,244,0.66)", "txt3": "rgba(244,244,244,0.44)",
    "ctl": "rgba(255,255,255,0.055)", "ctl_hover": "rgba(255,255,255,0.10)",
    "tok": "rgba(255,255,255,0.08)", "subtle_hover": "rgba(255,255,255,0.055)",
    "field": "rgba(0,0,0,0.22)",
    "accent": "#f3f3f3", "on_accent": "#161616",
    "ok": "#6cc08a", "warn": "#e0b354", "err": "#e8756c", "info": "#7fb2e8",
    "ok_bg": "rgba(108,192,138,0.14)", "warn_bg": "rgba(224,179,84,0.13)", "err_bg": "rgba(232,117,108,0.13)",
    "seg1": "rgba(244,244,244,0.82)", "seg2": "rgba(244,244,244,0.5)", "seg3": "rgba(244,244,244,0.3)",
}
LIGHT: Dict[str, str] = {
    # Neutral-light twin of the glass palette: solid white cards on a soft grey wash
    # (a light ambient gradient in qss()). Same zero-hue-shift discipline.
    "nav": "#e6e6e6", "base": "#e2e2e2", "surface": "#eeeeee", "canvas": "#eeeeee",
    "card": "#ffffff", "raised": "#ffffff",
    "inset": "#e8e8e8", "card_hover": "#e8e8e8",
    "hairline": "rgba(0,0,0,0.08)", "stroke": "rgba(0,0,0,0.08)", "divider": "rgba(0,0,0,0.08)",
    "hairline_strong": "rgba(0,0,0,0.14)",
    "txt1": "#161616", "txt2": "rgba(22,22,22,0.64)", "txt3": "rgba(22,22,22,0.52)",
    "ctl": "#ffffff", "ctl_hover": "#f0f0f2",
    "tok": "rgba(0,0,0,0.05)", "subtle_hover": "rgba(0,0,0,0.04)",
    "field": "rgba(0,0,0,0.04)",
    "accent": "#1a1a1a", "on_accent": "#f4f4f4",
    "ok": "#2f8f52", "warn": "#96690f", "err": "#c0574e", "info": "#2f6fb8",
    "ok_bg": "rgba(47,143,82,0.12)", "warn_bg": "rgba(150,105,15,0.12)", "err_bg": "rgba(192,87,78,0.12)",
    "seg1": "rgba(0,0,0,0.78)", "seg2": "rgba(0,0,0,0.45)", "seg3": "rgba(0,0,0,0.28)",
}

# ── elevation ladder + radius tokens (Refined-Neutral) ───────────────────────
# Ordered base ladder: each step a small neutral lift, zero hue shift. `inset` is
# the ONE extra lift (grouped / hover / selected) and is not part of this linear
# order (in light it is a downward wash), so it is verified separately.
ELEVATION = ("nav", "canvas", "raised")
RADIUS_CONTAINER = 8    # the one panel per region, menus, dialogs
RADIUS_CONTROL = 6      # buttons, inputs, combos, row hover, focus rings, chips


def radius(role: str = "container") -> int:
    """Two deliberate radii: 8px containers, 6px controls (design-rules §3)."""
    return RADIUS_CONTROL if role == "control" else RADIUS_CONTAINER


# ── spacing scale (design-rules §"Spacing": 4px grid) ────────────────────────
# ONE ramp so padding/gaps stop being scattered magic numbers. The device is a
# ~6:1 contrast between inter-group and intra-group space (24px between sections,
# 2-4px within a group). The numeric steps are the 4px grid (4/8/12/16/20/24/32);
# the semantic roles name the recurring measures the contract fixes (detail-row
# gap 10, signal-path pad 14, data-table row 30) so a call site reads intent.
SPACE: Dict[str, int] = {
    # 4px grid steps
    "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24, "xxxl": 32,
    # semantic roles (design-rules §Spacing / §"Detail card" / §"Signal path")
    "row": 10,          # detail-row gap
    "path": 14,         # signal-path / page-body rhythm
    "card": 16,         # card interior padding
    "page": 24,         # page / scroll-body margin, inter-section gap
    "data_row": 30,     # data-table row height
}


def sp(role: str, fallback: int = 8) -> int:
    """A spacing measure from the one 4px-grid scale (design-rules §Spacing).
    `role` is a grid step (xs/sm/md/lg/xl/xxl/xxxl) or a semantic name
    (row/path/card/page/data_row). Unknown role returns `fallback` (8px)."""
    return SPACE.get(role, fallback)


# ── fixed type scale (design-rules §3, Regular/Semibold only) ────────────────
# role -> (point_size, semibold, mono). Mono is reserved for machine data so
# monospace re-acquires meaning (and gives tabular digit alignment).
# NOTE: QSS chrome font sizes DERIVE from this ramp via px()/chrome_px() (see
# _CHROME_ROLE below) — so the point size of a role in _CHROME_ROLE's values
# (group_subhead/section/value/detail_key/footnote) also sets a chrome size.
# Retune one of those for a CONTENT reason and the mapped chrome size moves too;
# if they should diverge, give chrome its own role rather than editing these.
TYPE_SCALE: Dict[str, tuple] = {
    "hero":          (15.5, True,  True),   # pin/signal name — the one focal element
    "stat":          (14.0, True,  True),   # stat numbers, tabular
    "payload":       (10.5, True,  True),   # delivered net (category-coloured at call site)
    "group_subhead": (10.5, True,  True),   # group subhead
    "value":         (10.0, False, True),   # value / terminal / side
    "section":       (11.0, True,  False),  # section header (recedes, trailing hairline)
    "detail_key":    (9.0,  False, False),  # detail key / metadata
    "footnote":      (8.5,  False, False),  # column header / role / unit / footnote — quietest
}


def scale_font(role: str) -> QFont:
    """A locked font for a named type role — never improvise a size (design-rules §3).
    Unknown role raises KeyError so a stray size can't slip in."""
    size, semibold, mono = TYPE_SCALE[role]
    return mono_font(size, semibold) if mono else ui_font(size, semibold)


def px(role: str) -> int:
    """A TYPE_SCALE role's size as QSS pixels — the px projection (pt × 4/3 at 96dpi)
    of the one point-size type ramp, so QSS chrome and QFont content share ONE scale
    instead of independent magic numbers. Rounded to the nearest px."""
    return round(TYPE_SCALE[role][0] * 4 / 3)


# Chrome (QSS) font sizes DERIVED from the content type ramp: each chrome alias maps
# to the TYPE_SCALE role whose px projection matches, so nav/controls/headers size
# off the SAME point ramp as content (change a TYPE_SCALE size → chrome follows).
# The current projections are exact: group_subhead 10.5→14, section 11→15,
# value 10→13, detail_key 9→12, footnote 8.5→11.
_CHROME_ROLE = {
    "base": "group_subhead",   # default widget/body chrome   → 14px
    "brand": "section",        # nav brand wordmark            → 15px
    "input": "value",          # search / line edits           → 13px
    "control": "detail_key",   # segmented, token btns, facets → 12px
    "header": "footnote",      # table + column headers        → 11px
}


def chrome_px(alias: str) -> int:
    """QSS chrome font size (px) for a chrome alias, derived from TYPE_SCALE via
    ``px()`` — see ``_CHROME_ROLE``. One type ramp for content AND chrome."""
    return px(_CHROME_ROLE[alias])

# ── category palette — the ONLY hue, on pin/net data (both themes) ───────────
# BENCH-03: every hue clears >=3:1 on canvas/raised/inset in BOTH themes (worst
# case ~4.5:1 — real margin, not the bare floor) so pin/net colours are actually
# visible. Meaning is preserved (power warm, ground neutral-cool, core violet,
# service green, lane cool-grey, must red, osc orange, fixed grey, breakout cyan);
# the abundant classes (ground, lane, fixed) stay the quietest that still clears.
# Harmonized family (2026-07-09): ONE tuned set — even lightness/weight, semantic hues,
# and the three greys given distinct temperatures (ground cool, lane neutral, fixed warm)
# so power/osc no longer read as two near-identical ambers. One edit here retunes every
# net + pin-class dot app-wide (single source of truth — see design-rules §3).
CATEGORY_DARK = {
    "power": "#d69f4a", "ground": "#9aa8ba", "core": "#b69bea", "service": "#72c493",
    "lane": "#969aa0", "must": "#eb8078", "osc": "#dc7f3c", "fixed": "#b2a99b",
    "breakout": "#52b6cc", "fivev": "#72c493",
}
CATEGORY_LIGHT = {
    "power": "#9c6c1c", "ground": "#566374", "core": "#6d49b8", "service": "#37945a",
    "lane": "#565b64", "must": "#c74336", "osc": "#a85c14", "fixed": "#665e50",
    "breakout": "#227e91", "fivev": "#37945a",
}

UI_STACK = '"DM Sans","Segoe UI Variable Text","Segoe UI","Inter",sans-serif'
UI_DISPLAY = '"DM Sans","Segoe UI Variable Display","Segoe UI","Inter",sans-serif'
# Native Windows faces lead; the BUNDLED fixed-pitch fonts (JetBrains Mono / Geist
# Mono, shipped in tools/fonts/) follow so mono data columns stay monospaced — and
# therefore column-aligned — off Windows too, where Cascadia/Consolas are absent.
MONO_STACK = '"Cascadia Mono","Cascadia Code","Consolas","JetBrains Mono","Geist Mono",monospace'
_UI_FAMILIES = ("DM Sans", "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter", "Geist")
_MONO_FAMILIES = ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Geist Mono")

_active = dict(DARK)
_is_dark = True


def set_theme(dark: bool) -> Dict[str, str]:
    global _active, _is_dark
    _active = dict(DARK if dark else LIGHT)
    _is_dark = bool(dark)
    return _active


def is_dark() -> bool:
    return _is_dark


def resolve_dark(mode: str, os_is_dark: bool | None = None) -> bool:
    """Resolve a theme MODE ('dark' | 'light' | 'system') to a concrete dark bool.
    'system' follows the OS preference; when that is unknown (off-Windows or a failed
    read) it falls back to dark. An unrecognised mode also falls back to dark."""
    m = (mode or "").strip().lower()
    if m == "light":
        return False
    if m == "system":
        return True if os_is_dark is None else bool(os_is_dark)
    return True                              # 'dark' and any unknown mode


def os_dark() -> bool | None:
    """The Windows apps dark-mode preference (True=dark, False=light), or None when it
    can't be determined (non-Windows, or the registry read fails). Guarded: never
    raises, so callers can feed it straight into resolve_dark() as the 'system' input."""
    import sys
    if sys.platform != "win32":
        return None
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0                      # AppsUseLightTheme: 0 = dark, 1 = light
    except Exception:  # noqa: BLE001
        return None


def tokens() -> Dict[str, str]:
    return _active


def t(key: str, fallback: str = "#808080") -> str:
    return _active.get(key, fallback)


def category(name: str, fallback: str = "#8a8f97") -> str:
    pal = CATEGORY_DARK if _is_dark else CATEGORY_LIGHT
    return pal.get(name, fallback)


def _rel_lum_qc(c: QColor) -> float:
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.red()) + 0.7152 * lin(c.green()) + 0.0722 * lin(c.blue())


def _rel_lum(hexstr: str) -> float:
    return _rel_lum_qc(qcolor(hexstr))


def _composite_over(top: QColor, base: QColor) -> QColor:
    """Alpha-composite `top` over an opaque `base` (straight-alpha 'source over').
    Returns an opaque colour so luminance/contrast maths is meaningful for a
    translucent surface token (e.g. LIGHT 'hairline' = rgba over 'canvas')."""
    a = top.alpha() / 255.0
    r = round(top.red() * a + base.red() * (1 - a))
    g = round(top.green() * a + base.green() * (1 - a))
    b = round(top.blue() * a + base.blue() * (1 - a))
    return QColor(r, g, b, 255)


def category_contrast(name: str, surface_key: str) -> float:
    """WCAG contrast ratio of a category hue against a surface token, in the active
    theme. Category colours are opaque hex; surface tokens resolve via t() and may be
    translucent (rgba) — those are composited over the opaque base ('canvas') first
    so the ratio reflects what the eye actually sees, not a fabricated one vs black."""
    fg_c = qcolor(category(name))
    bg_c = qcolor(t(surface_key))
    if bg_c.alpha() < 255:
        bg_c = _composite_over(bg_c, qcolor(t("canvas")))
    fg = _rel_lum_qc(fg_c) + 0.05
    bg = _rel_lum_qc(bg_c) + 0.05
    return max(fg, bg) / min(fg, bg)


_HEXDIGITS = frozenset("0123456789abcdefABCDEF")


def qcolor(key_or_hex: str) -> QColor:
    """Resolve a token key or a literal colour to a QColor. Parses rgba(...) tokens
    and, as a safety net for external/non-token input, prefixes '#' onto bare 3/6/8-digit
    hex (Qt only accepts '#'-prefixed hex, and silently yields an invalid → opaque-black
    QColor otherwise). An invalid result is logged so a bad token surfaces loudly."""
    v = _active.get(key_or_hex, key_or_hex)
    if v.startswith("rgba"):
        nums = v[v.index("(") + 1:v.index(")")].split(",")
        r, g, b = (int(float(x)) for x in nums[:3])
        a = int(float(nums[3]) * 255) if len(nums) > 3 else 255
        return QColor(r, g, b, a)
    if not v.startswith("#") and len(v) in (3, 6, 8) and all(ch in _HEXDIGITS for ch in v):
        v = "#" + v
    c = QColor(v)
    if not c.isValid():
        import logging
        logging.getLogger(__name__).warning("qcolor: unparseable colour %r -> invalid QColor", v)
    return c


def opaque(key_or_hex: str, dark: bool | None = None) -> str:
    """Resolve a token (or literal colour) to an OPAQUE '#rrggbb', compositing any
    translucent rgba token over the theme 'canvas' — exactly what the eye sees.

    For consumers that interpolate a colour into an SVG ``fill=``/``stroke=`` attribute
    or feed it to a QPainter/QColor: those need opaque hex, and a raw ``t()`` token can
    be translucent rgba (dark txt2/txt3, light hairline), which would void the SVG
    attribute. `dark` picks a specific theme (flipped then restored); default resolves
    in the active theme. This is the one helper the retired ``ui_theme`` shim provided
    that ``ui.theme`` lacked (it composited legacy MAIN_BG-style keys); native token
    names are used now."""
    prev = _is_dark
    flip = dark is not None and bool(dark) != prev
    if flip:
        set_theme(bool(dark))
    try:
        qc = qcolor(key_or_hex)
        if qc.alpha() < 255:
            qc = _composite_over(qc, qcolor("canvas"))
        return qc.name()
    finally:
        if flip:
            set_theme(prev)


_family_cache: Dict[tuple, str] = {}


def _family(families) -> str:
    """First installed family from a stack (fallback: the first name).

    Memoised per stack: resolving it enumerates the whole installed QFontDatabase,
    which is a full family-set build on every call — and this runs once per themed
    widget (via ui_font/mono_font/scale_font). The installed set is stable within a
    session except when load_fonts() registers the bundled TTFs, which clears the cache."""
    key = tuple(families)
    cached = _family_cache.get(key)
    if cached is not None:
        return cached
    resolved = families[0]
    try:
        from PyQt5.QtGui import QFontDatabase
        have = set(QFontDatabase().families())
        for fam in families:
            if fam in have:
                resolved = fam
                break
    except Exception:  # noqa: BLE001
        pass
    _family_cache[key] = resolved
    return resolved


def ui_font(size: float = 10, semibold: bool = False) -> QFont:
    f = QFont(_family(_UI_FAMILIES))
    f.setPointSizeF(size)
    f.setWeight(QFont.DemiBold if semibold else QFont.Normal)
    return f


def mono_font(size: float = 9.5, semibold: bool = False) -> QFont:
    f = QFont(_family(_MONO_FAMILIES))
    f.setPointSizeF(size)
    f.setWeight(QFont.DemiBold if semibold else QFont.Normal)
    # Monospace faces are inherently tabular (all glyphs one advance); PreferQuality
    # keeps hinting so stacked digit columns stay aligned at fractional DPI.
    f.setStyleStrategy(QFont.PreferQuality)
    return f


def _fonts_dir():
    """The bundled fonts directory. Under a frozen --onefile exe __file__ points
    into the throwaway _MEIPASS bundle, so resolve fonts from _MEIPASS directly;
    in dev it is tools/fonts (parent.parent of this ui/ module)."""
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "fonts"
    return Path(__file__).resolve().parent.parent / "fonts"


def load_fonts(app) -> None:
    """Register bundled TTFs (if any) and set the base app font to the UI face."""
    try:
        import glob
        from PyQt5.QtGui import QFontDatabase
        for ttf in glob.glob(str(_fonts_dir() / "*.ttf")):
            QFontDatabase.addApplicationFont(ttf)
    except Exception:  # noqa: BLE001
        pass
    _family_cache.clear()                # bundled TTFs just changed the installed set
    f = ui_font(10)
    app.setFont(f)


def qss(dark: bool | None = None) -> str:
    """Full stylesheet for the Fusion base widgets (buttons, inputs, combos,
    tables, headers, scrollbars, tooltips, menus). Object-name hooks let the
    widget kit request specific roles (surface, card, nav, primary, ...).

    Reads the current active theme. Passing `dark` is a back-compat convenience that
    ALSO mutates the process-global active theme via set_theme() — prefer calling
    set_theme() explicitly then qss() with no arg. To merely inspect a stylesheet
    without flipping the active theme, do NOT pass `dark`."""
    if dark is not None:
        set_theme(dark)
    c = _active
    rc = RADIUS_CONTROL       # 6px — controls
    rk = RADIUS_CONTAINER     # 8px — containers
    # Chrome font sizes, derived from the one TYPE_SCALE point ramp (chrome_px):
    fz_base = chrome_px("base")        # 14px
    fz_brand = chrome_px("brand")      # 15px
    fz_input = chrome_px("input")      # 13px
    fz_ctl = chrome_px("control")      # 12px
    fz_hdr = chrome_px("header")       # 11px
    # Ambient gradient (the mockup's top-left glow, native): a Qt radial painted on the
    # shell + content containers. Panels are rgba-white lifts over it, so the app reads
    # as the mockup's layered glass without the browser-only backdrop blur.
    glow = "#1c1c1c" if _is_dark else "#f7f7f7"
    grad = (f"qradialgradient(cx:0.16, cy:-0.05, radius:1.3, fx:0.16, fy:-0.05, "
            f"stop:0 {glow}, stop:0.5 {c['canvas']}, stop:1 {c['base']})")
    return f"""
/* outline:none folded into QWidget — the universal `*` selector amid a large
   sheet makes Qt's QSS parser emit a spurious "Could not parse stylesheet". */
QWidget {{ color: {c['txt1']}; font-family: {UI_STACK}; font-size: {fz_base}px; outline: none; }}
QMainWindow, #shellRoot {{ background: {grad}; }}
#navPane {{ background: {c['nav']}; border-right: 1px solid {c['divider']}; }}
#contentArea, QStackedWidget, #workspace {{ background: {grad}; }}
QLabel {{ background: transparent; }}

/* nav rail — brand, search, workspace items (calm: inset active step, no accent rail) */
#navBrandRow {{ background: transparent; }}
#navBrand {{ background: transparent; color: {c['txt1']}; font-size: {fz_brand}px;
    font-weight: 700; }}
#navToggle {{ background: transparent; border: none; border-radius: {rc}px; }}
#navToggle:hover {{ background: {c['subtle_hover']}; }}
#navSearch {{ background: {c['field']}; border: 1px solid {c['stroke']}; border-radius: {rc}px;
    color: {c['txt1']}; padding: 6px 8px; font-size: {fz_input}px; }}
#navSearch:focus {{ border-color: {c['accent']}; background: {c['inset']}; }}
#navItem {{ background: transparent; border: none; border-radius: {rc}px;
    color: {c['txt2']}; text-align: left; padding: 0 12px; }}
#navItem:hover {{ background: {c['subtle_hover']}; }}
#navItem[selected="true"] {{ background: {c['inset']}; color: {c['txt1']}; font-weight: 600; }}
/* an honest shelved workspace: greyed + no hover, never a live row to a dead card.
   Qt disabled widgets don't receive hover, but pin :disabled:hover explicitly so the
   greyed state can never flash the hover wash regardless of Qt/style quirks. */
#navItem:disabled, #navItem:disabled:hover {{ background: transparent; color: {c['txt3']}; }}

/* buttons */
QPushButton {{ background: {c['ctl']}; border: 1px solid {c['stroke']}; border-radius: {rc}px;
    color: {c['txt1']}; padding: 6px 12px; }}
QPushButton:hover {{ background: {c['ctl_hover']}; }}
/* border same colour as the fill: without an explicit border a Fusion QSS button can
   skip painting its background box, leaving white on_accent text on a light page. */
QPushButton#primary {{ background: {c['accent']}; color: {c['on_accent']};
    border: 1px solid {c['accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {c['accent']}; border-color: {c['accent']}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {c['stroke']}; }}
QPushButton#ghost:hover {{ background: {c['subtle_hover']}; }}
QPushButton#subtab {{ background: transparent; border: none; border-radius: 0;
    color: {c['txt2']}; padding: 6px 14px; }}
QPushButton#subtab:hover {{ color: {c['txt1']}; }}
/* subtab selection is colour-only; the active underline is the painted, animated
   SlidingUnderline (ui.motion) overlaid by Workspace — not a QSS border-bottom. */
QPushButton#subtab[selected="true"] {{ color: {c['txt1']}; font-weight: 600; }}
/* Segment labels are INTERFACE text (Find And Replace / mm / mils / profile names),
   so they use the UI face — mono is reserved for machine values (design-rules §3). */
QPushButton#seg {{ background: transparent; border: none; border-radius: {rc}px;
    color: {c['txt2']}; padding: 4px 12px; font-family: {UI_STACK}; font-size: {fz_ctl}px; }}
QPushButton#seg[selected="true"] {{ background: {c['card']}; color: {c['txt1']}; font-weight: 600; }}
QPushButton#tokbtn {{ background: {c['tok']}; border: none; border-radius: {rc}px;
    color: {c['txt1']}; padding: 3px 9px; font-family: {MONO_STACK}; font-size: {fz_ctl}px; }}
QPushButton#tokbtn:hover {{ background: {c['ctl_hover']}; }}
/* Library finder (mockup .finder): a filter button beside the search box that opens
   a Show/Group-By pop. Quiet by default, lifts on hover; its badge carries the count
   of active (non-default) filters. The pop is a raised card; its Show checkboxes and
   Group By radios inherit the global QCheckBox/QRadioButton chrome. */
QPushButton#finderFilter {{ background: {c['ctl']}; border: 1px solid {c['stroke']};
    border-radius: {rc}px; }}
QPushButton#finderFilter:hover {{ background: {c['ctl_hover']}; }}
QLabel#finderBadge {{ background: {c['accent']}; color: {c['on_accent']};
    border-radius: {rc}px; font-size: 8px; font-weight: 700; }}
QFrame#finderPop {{ background: {c['card']}; border: 1px solid {c['hairline_strong']};
    border-radius: {rk}px; }}
QLabel#finderPopLabel {{ color: {c['txt3']}; background: transparent; }}
QCheckBox#finderOpt, QRadioButton#finderOpt {{ color: {c['txt1']}; background: transparent;
    padding: 5px 4px; spacing: 9px; }}
QCheckBox#finderOpt:hover, QRadioButton#finderOpt:hover {{ color: {c['txt1']}; }}
/* Detail header still-needs line (mockup .needs) + the ⋯ kebab. The Complete pill is
   green, each missing-field pill is amber, a broken link reads red. Reusable chrome. */
QLabel#needsBroken {{ color: {c['err']}; background: transparent; }}
QLabel#needsLabel {{ color: {c['txt3']}; background: transparent; }}
QLabel#needsComplete {{ color: {c['ok']}; background: {c['ok_bg']};
    border-radius: {rc}px; padding: 4px 11px; }}
QLabel#needsPill {{ color: {c['warn']}; background: {c['warn_bg']};
    border-radius: {rc}px; padding: 4px 11px; }}
QPushButton#kebab {{ background: {c['ctl']}; border: 1px solid {c['stroke']};
    border-radius: {rc}px; color: {c['txt2']}; font-size: 15px; font-weight: 700; }}
QPushButton#kebab:hover {{ background: {c['ctl_hover']}; color: {c['txt1']}; }}
QPushButton#kebab::menu-indicator {{ image: none; width: 0; }}
/* Library picker front drop zone (library-v2 §2.1): a dashed panel that opens the
   vendor-ZIP import on click and lifts on hover / while a ZIP is dragged over the
   panel (the `dragging` property, set by _PartsRoot). Reusable chrome → object name. */
QFrame#dropfront {{ background: {c['ctl']}; border: 1px dashed {c['hairline_strong']};
    border-radius: {rk}px; }}
QFrame#dropfront:hover, QFrame#dropfront[dragging="true"] {{
    background: {c['subtle_hover']}; border-color: {c['txt3']}; }}
/* Library picker grouped-row headers (mockup .gh): a quiet semibold t3 label.
   The inline header is transparent (scrolls with rows); its pinned twin
   (#partGroupHeaderPinned) carries an OPAQUE canvas fill so it covers rows
   scrolling underneath it — the sticky-section effect PyQt has no native form of. */
QLabel#partGroupHeader {{ color: {c['txt3']}; background: transparent;
    padding: 10px 8px 5px 8px; }}
QLabel#partGroupHeaderPinned {{ color: {c['txt3']}; background: {c['canvas']};
    padding: 10px 8px 5px 8px; }}
/* Static label vocabulary (widgets.static_label / static_status) — the no-restyler
   twins of body/subhead/tag, themed by object name so high-frequency rebuild areas
   (the Git watchdog, live tables) retint via the shell's setStyleSheet(qss()) re-apply
   WITHOUT a per-widget restyle closure. Status colour lives on the dot+text, no fill
   (design-rules §1.1). Kept in one contiguous block so the vocabulary stays discoverable. */
QLabel#sBody {{ color: {c['txt1']}; background: transparent; }}
QLabel#sDim  {{ color: {c['txt3']}; background: transparent; }}
QLabel#sKey  {{ color: {c['txt2']}; background: transparent; }}
QLabel#sSub  {{ color: {c['txt2']}; background: transparent; }}
QLabel#sStat_ok   {{ color: {c['ok']};   background: transparent; }}
QLabel#sStat_warn {{ color: {c['warn']}; background: transparent; }}
QLabel#sStat_err  {{ color: {c['err']};  background: transparent; }}
QLabel#sStat_info {{ color: {c['info']}; background: transparent; }}
QLabel#sStat_mut  {{ color: {c['txt3']}; background: transparent; }}
/* A disabled button (incl. primary) must stay legibly muted in BOTH themes — Qt's
   default faded compositing turned disabled #primary into near-invisible text. */
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#ghost:disabled {{
    background: {c['ctl']}; color: {c['txt3']}; border: 1px solid {c['stroke']}; font-weight: 400; }}

QLineEdit, QPlainTextEdit, QTextEdit {{ background: {c['field']}; border: 1px solid {c['stroke']};
    border-radius: {rc}px; color: {c['txt1']}; padding: 5px 10px; selection-background-color: {c['accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['txt3']}; }}

QComboBox {{ background: {c['field']}; border: 1px solid {c['stroke']}; border-radius: {rc}px;
    color: {c['txt1']}; padding: 5px 10px; }}
QComboBox QAbstractItemView {{ background: {c['card']}; border: 1px solid {c['stroke']};
    selection-background-color: {c['ctl_hover']}; color: {c['txt1']}; }}

/* menu-button popups (W.menu_button) — a raised surface, one hairline, the inset wash on hover */
QMenu {{ background: {c['card']}; border: 1px solid {c['stroke']}; border-radius: {rk}px; padding: 4px; }}
QMenu::item {{ padding: 6px 12px; border-radius: {rc}px; color: {c['txt1']}; background: transparent; }}
QMenu::item:selected {{ background: {c['ctl_hover']}; color: {c['txt1']}; }}
QMenu::separator {{ height: 1px; background: {c['divider']}; margin: 4px 8px; }}

QSpinBox, QDoubleSpinBox {{ background: {c['field']}; border: 1px solid {c['stroke']};
    border-radius: {rc}px; color: {c['txt1']}; padding: 4px 6px; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {c['txt3']}; }}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QCheckBox {{ color: {c['txt1']}; spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c['stroke']};
    border-radius: {rc}px; background: {c['ctl']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QRadioButton {{ color: {c['txt1']}; spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {c['stroke']};
    border-radius: 7px; background: {c['ctl']}; }}
QRadioButton::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['ctl_hover']}; border-radius: {rc}px; min-height: 24px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['ctl_hover']}; border-radius: {rc}px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* kit.panes — the reusable list·center·detail splitter; hairline handle, brighter on grab */
#panes {{ background: transparent; }}
#panes::handle {{ background: {c['divider']}; }}
#panes::handle:hover {{ background: {c['txt3']}; }}

QToolTip {{ background: {c['card']}; color: {c['txt1']}; border: 1px solid {c['stroke']};
    border-radius: {rc}px; padding: 4px 8px; }}

QTableWidget, QTableView {{ background: {c['card']}; alternate-background-color: {c['card']};
    gridline-color: transparent; border: 1px solid {c['stroke']}; border-radius: {rk}px;
    color: {c['txt2']}; }}
QTableWidget::item {{ padding: 4px 10px; border: none; }}
QTableWidget::item:selected {{ background: {c['ctl']}; color: {c['txt1']}; }}
QHeaderView::section {{ background: {c['card']}; color: {c['txt2']}; border: none;
    border-bottom: 1px solid {c['divider']}; padding: 6px 10px; font-size: {fz_hdr}px; font-weight: 600; }}
QTableCornerButton::section {{ background: {c['card']}; border: none; }}

/* Activity console (ui.console) — a pinned bottom chrome region: deepest nav tone +
   a top hairline so it reads as a panel, not part of the workspace. The log is a
   borderless transparent mono surface (overrides the QPlainTextEdit input styling). */
#activityConsole {{ background: {c['nav']}; border-top: 1px solid {c['divider']}; }}
#consoleLog {{ background: transparent; border: none; color: {c['txt2']}; padding: 0; }}
#consoleChevron {{ background: transparent; border: none; border-radius: {rc}px;
    color: {c['txt2']}; font-size: {fz_input}px; }}
#consoleChevron:hover {{ background: {c['subtle_hover']}; color: {c['txt1']}; }}
"""
