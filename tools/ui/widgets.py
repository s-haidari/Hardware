"""ui.widgets — the shared component kit, matching the approved mockup.

Chrome styling (buttons, inputs, tables, nav, surfaces) comes from the global QSS
in ui.theme, which re-applies instantly on a theme toggle. The pieces that carry
per-widget colour (tags, tokens, net tokens, the verdict bar, category dots) read
the active theme at build time and register a restyle callback, so `restyle_all()`
retints them on a toggle without rebuilding the tree.

Casing convention (from the design review):
  Title Case -> structural labels (eyebrows, section + column headers) -> `eyebrow(...)`
  Title Case -> human text (titles, buttons, values)
  real casing -> machine data (nets, refdes, pins)                     -> `token/net_token`
Separation is by layout, never a middot. No letterspacing anywhere.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger(__name__)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QWidget, QLabel, QFrame, QHBoxLayout, QVBoxLayout,
                             QPushButton, QGridLayout, QStackedWidget, QSizePolicy,
                             QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                             QStyledItemDelegate)

from . import theme as T

def svg_icon(svg: str, size: int = 18, color: str = "#8b8b91"):
    """Render an inline SVG string to a QIcon tinted `color`. Neutral gray by
    default so it reads on both themes without re-tinting.

    `color` may be a plain SVG hex (``#rrggbb``) OR a theme token in ``rgba(...)``
    / ``rgb(...)`` form. QSvgRenderer can't parse the ``rgba()`` form in a `fill`
    attribute (it silently falls back to black — the light-palette tokens use that
    form), so the token is resolved to an opaque hex for the SVG and its alpha is
    re-applied via painter opacity — the tint then holds in BOTH themes."""
    from PyQt5.QtGui import QIcon, QPixmap, QPainter
    try:
        from PyQt5.QtSvg import QSvgRenderer
    except Exception:  # noqa: BLE001
        return QIcon()
    qc = _qcolor(color)
    hex6 = qc.name()                       # #rrggbb (opaque) — SVG-parseable
    alpha = qc.alphaF()                     # re-applied below so rgba() tints survive
    r = QSvgRenderer(bytearray(svg.replace("currentColor", hex6), encoding="utf-8"))
    pm = QPixmap(size, size); pm.fill(Qt.transparent)
    p = QPainter(pm)
    if alpha < 1.0:
        p.setOpacity(alpha)
    r.render(p); p.end()
    return QIcon(pm)


# ── retheme registry (colour-bearing widgets) ────────────────────────────────
_RESTYLERS: List[Callable[[], None]] = []
# id(fn) -> weakref(owner), for OWNED restylers only. A parallel map (not a tuple in
# _RESTYLERS, and not an attribute on fn — a bound method rejects attributes) keeps
# _RESTYLERS a flat list of callables. id(fn) is unique while fn is registered (the list
# holds it alive); every removal path pops the id so no stale/reused-id entry survives.
_RESTYLE_OWNERS: Dict[int, "object"] = {}


def _restyler_dead(fn: Callable[[], None]) -> bool:
    """True if this restyler's owner widget is gone — its Python wrapper GC'd, or its
    C++ half deleted (``sip.isdeleted``). An owner-less restyler (no ``_RESTYLE_OWNERS``
    entry) is a process-lifetime singleton and is never dead."""
    ref = _RESTYLE_OWNERS.get(id(fn))
    if ref is None:
        return False
    o = ref()
    if o is None:
        return True                         # Python wrapper garbage-collected
    try:
        from PyQt5 import sip
        return bool(sip.isdeleted(o))       # C++ deleted (sip.delete / deleteLater)
    except Exception:  # noqa: BLE001
        return False


def register_restyle(fn: Callable[[], None], owner=None) -> None:
    """Register a theme-restyle callback: run once now, and again on every theme
    toggle (via ``restyle_all``).

    Pass ``owner`` — the widget the callback styles — so the restyler auto-drops when
    that widget dies. Without it, a panel that rebuilds (a bench package switch,
    PCB-table CRUD, a preview card re-created on selection) leaves its stale restylers
    in the registry forever: every later theme toggle re-runs more and more dead
    closures and the process slowly grows and the retint gets slower (SHELL-06).
    Owner-less registration is only for true process-lifetime singletons.

    The owner is tracked by a WEAKREF and pruned LAZILY (on the next ``restyle_all`` /
    ``_prune_restylers``), NOT via ``owner.destroyed.connect(...)``. A Python slot
    connected to ``QObject.destroyed`` is invoked by Qt when the widget's C++ object is
    deleted — and when an un-parented widget trapped in a reference cycle is finalized by
    *Python's* garbage collector, that invocation re-enters the interpreter mid-collection:
    a use-after-free that SEGFAULTS (it crashed the suite inside ``gc.collect()``). Weakref
    cleanup runs in pure Python and never delivers a Qt signal during GC, so the crash
    class is gone — the same fix ``EventBus.on_owned`` uses (see ``ui.feature``)."""
    if owner is not None:
        try:
            import weakref
            _RESTYLE_OWNERS[id(fn)] = weakref.ref(owner)   # parallel map — fn stays a plain callable
        except TypeError:                                  # owner not weakly-referenceable (rare)
            _RESTYLE_OWNERS.pop(id(fn), None)              # degrade to a singleton — clear any stale entry
    else:
        # Ownerless = a process-lifetime singleton. id(fn) is REUSABLE: a freed restyler may
        # have left an entry here that this new fn now collides with. Clear it, or the stale
        # dead-owner weakref would make _restyler_dead(fn) falsely True and prune a live singleton.
        _RESTYLE_OWNERS.pop(id(fn), None)
    _RESTYLERS.append(fn)
    fn()


def _drop_restyle(fn: Callable[[], None]) -> None:
    """Remove a restyler from the registry (idempotent — safe if already gone)."""
    try:
        _RESTYLERS.remove(fn)
    except ValueError:
        pass
    _RESTYLE_OWNERS.pop(id(fn), None)


def _prune_restylers() -> None:
    """Drop every restyler whose owner widget has died (GC'd or C++-deleted). Lazy
    cleanup: called at the top of ``restyle_all`` and exposed for tests that assert the
    registry returns to a baseline after a widget is destroyed (the drop is no longer
    synchronous on ``destroyed`` — see ``register_restyle``)."""
    survivors = []
    for fn in _RESTYLERS:
        if _restyler_dead(fn):
            _RESTYLE_OWNERS.pop(id(fn), None)
        else:
            survivors.append(fn)
    _RESTYLERS[:] = survivors


def restyle_all() -> None:
    survivors = []
    for fn in list(_RESTYLERS):
        if _restyler_dead(fn):
            _RESTYLE_OWNERS.pop(id(fn), None)
            continue                        # owner gone → prune (never call a dead closure)
        try:
            fn()
            survivors.append(fn)
        except RuntimeError:
            # The C++ widget was deleted (deleteLater pending) before the weakref
            # noticed — a benign race. Drop it; a genuine styling bug must not hide here.
            _RESTYLE_OWNERS.pop(id(fn), None)
        except Exception:  # noqa: BLE001
            # A real restyler bug (bad token, typo): log it so a broken widget surfaces
            # in dev instead of silently staying unstyled forever. Keep it registered.
            _log.exception("restyle callback failed")
            survivors.append(fn)
    _RESTYLERS[:] = survivors


# ── the one category/status data-marker dot ──────────────────────────────────
# design-rules §1.1 blesses a small coloured DOT as the sanctioned category/status
# marker ("a 6px leading dot"), where the number is the *diameter*. A dot is a true
# circle, so its radius is derived (size // 2) — never a control/container token and
# never a hand-typed 3/4/5px literal scattered per call site. These two helpers are
# the single source: `dot_css` builds the stylesheet string (use it inside an
# existing restyle / shared `_style` so the marker recolours with the theme without
# stacking a second registration — the VerdictSlot pattern), and `category_dot`
# wraps it as a standalone self-restyling QLabel for the common case.
def dot_css(color: str, size: int) -> str:
    """Stylesheet for a filled circular data-marker dot of `size` px, coloured
    `color` (a resolved colour: T.category(...)/T.t(...)/user hex). Radius is
    size // 2 so the marker is always a true circle."""
    return f"background:{color};border-radius:{size // 2}px;"


def category_dot(cat: str, size: int = 7) -> QLabel:
    """A standalone category data-marker dot whose fill tracks ``T.category(cat)``
    across theme toggles via ONE registered restyle. For a status dot or a dot that
    lives inside a shared ``_style`` (a verdict band), build a plain ``QLabel`` and
    set ``dot_css(colour, size)`` from that owner's restyle instead — don't stack a
    second registration."""
    dot = QLabel(); dot.setFixedSize(size, size)
    register_restyle(lambda: dot.setStyleSheet(dot_css(T.category(cat), size)), dot)
    return dot


def _qcolor(token: str):
    """Parse a theme colour token to a QColor. Tokens are either '#rrggbb' (dark
    palette) or 'rgba(r,g,b,a)' / 'rgb(r,g,b)' (light palette). QColor's string
    constructor doesn't understand the rgba() form, so a raw QColor(token) yields
    an invalid (black) colour for the light palette — parse it explicitly so item
    text is themed correctly in BOTH themes (PROJ-07)."""
    from PyQt5.QtGui import QColor
    s = (token or "").strip()
    if s.startswith("rgba(") or s.startswith("rgb("):
        parts = s[s.index("(") + 1:s.index(")")].split(",")
        try:
            r, g, b = (int(float(parts[i])) for i in range(3))
            a = int(round(float(parts[3]) * 255)) if len(parts) > 3 else 255
            return QColor(r, g, b, a)
        except (ValueError, IndexError):
            return QColor(s)
    return QColor(s)


# ── text ─────────────────────────────────────────────────────────────────────
def eyebrow(text: str) -> QLabel:
    """A quiet Title-case section label (Semibold, txt3, zero tracking). Retired the
    letterspaced UPPERCASE micro-label (design-rules §1.4) — this reskins every
    WORKSPACES / DETAIL / CONNECTION DIAGRAM header app-wide from one edit. Text is
    passed through verbatim so refdes / part numbers keep their real casing."""
    lab = QLabel(text)
    lab.setFont(T.ui_font(8.5, semibold=True))
    register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"), lab)
    return lab


def page_title(text: str) -> QLabel:
    lab = QLabel(text)
    f = T.ui_font(15, semibold=True)
    lab.setFont(f)
    register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), lab)
    return lab


def body(text: str, dim: bool = False, mono: bool = False, wrap: bool = False) -> QLabel:
    """Body text (txt1, or txt3 when `dim`). In a rebuild loop use static_label()
    (role 'body'/'dim') — the no-restyler twin — so the retint registry never grows."""
    lab = QLabel(text)
    lab.setFont(T.mono_font(9.5) if mono else T.ui_font(10))
    if wrap:
        lab.setWordWrap(True)
    register_restyle(lambda: lab.setStyleSheet(
        f"color:{T.t('txt3') if dim else T.t('txt1')};background:transparent;"), lab)
    return lab


# ── small pills: tag (status), token (code name), net_token (code + category) ─
def tag(text: str, kind: str = "mut") -> QLabel:
    """A status marker: color on a small leading dot + text, no filled pill — the
    Quiet-Instrument idiom (color lives on the smallest element, never as a surface
    fill). kind in {ok, warn, err, info, mut}. `mut` is neutral dim text, no dot.
    In a high-frequency rebuild loop use static_status() (the no-restyler twin)."""
    disp = text if kind == "mut" else f"● {text}"   # ● + thin space
    lab = QLabel(disp)
    lab.setFont(T.ui_font(9, semibold=True))
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    def style():
        fg = T.t("txt3") if kind == "mut" else T.t(kind)
        lab.setStyleSheet(f"color:{fg};background:transparent;")
    register_restyle(style, lab)
    style()
    return lab


def token(text: str, dim: bool = False) -> QLabel:
    """A machine identifier (refdes, terminal, path) in a subtle chip. For INLINE
    use (inspector, callouts); table identifier columns use plain mono text."""
    lab = QLabel(text)
    lab.setFont(T.mono_font(9))
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    register_restyle(lambda: lab.setStyleSheet(
        f"background:{T.t('tok')};color:{T.t('txt3') if dim else T.t('txt1')};"
        f"border-radius:{T.RADIUS_CONTROL}px;padding:2px 7px;"), lab)  # 2px vertical so descenders (y, g, p) never clip
    return lab


def net_label(text: str, cat: str) -> QWidget:
    """A net name as a 6px category dot + mono text with NO surface fill (design-rules
    §4). Use in a table/ledger cell where net_token's chip background would wash the
    whole column. The dot carries the category colour; the name stays neutral so a
    column of rails reads calm, not tinted."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(7)
    dot = QLabel()
    dot.setFixedSize(7, 7)
    name = QLabel(text)
    name.setFont(T.mono_font(10))
    lay.addWidget(dot)
    lay.addWidget(name)
    lay.addStretch(1)

    def style():
        dot.setStyleSheet(dot_css(T.category(cat), 7))
        name.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
    register_restyle(style, w)
    return w


def net_token(text: str, cat: str) -> QWidget:
    """A net name: category dot + category-coloured mono, in a subtle chip."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(8, 2, 9, 2)
    lay.setSpacing(6)
    dot = QLabel()
    dot.setFixedSize(7, 7)
    name = QLabel(text)
    name.setFont(T.mono_font(9, semibold=True))
    lay.addWidget(dot)
    lay.addWidget(name)

    def style():
        col = T.category(cat)
        dot.setStyleSheet(dot_css(col, 7))
        name.setStyleSheet(f"color:{col};background:transparent;")
        w.setStyleSheet(f"background:{T.t('tok')};border-radius:{T.RADIUS_CONTROL}px;")
    register_restyle(style, w)
    w.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return w


# ── static label vocabulary — the no-restyler twins of body/subhead/tag ───────
# High-frequency rebuild areas (the Git watchdog rebuilds its status/changes bodies
# on every file event; a live table re-fills on refresh) must NOT build the
# register_restyle helpers above (eyebrow/body/tag/dl/subhead) inside the rebuild
# loop — each appends a closure to the retint registry every tick (SHELL-06 / FIX 7).
# These two carry NO restyle closure: they are styled centrally by object name in
# theme.qss() (the `#s*` rules), so a theme toggle retints them via the shell's
# setStyleSheet(qss()) re-apply — the SAME central-QSS path #navItem / #facet use.
# Use these ONLY inside a rebuild loop; everywhere else prefer the owner-tracked
# helpers, which also retint AND drop themselves when their widget is destroyed.
_STATIC_ROLE = {
    # role  -> (object-name, default TYPE_SCALE font role)
    "body": ("sBody", "value"),        # txt1 value      (e.g. a path / branch)
    "dim":  ("sDim",  "value"),        # txt3 value      (dim by COLOUR, NOT size)
    "key":  ("sKey",  "detail_key"),   # txt2 detail key
    "sub":  ("sSub",  "section"),      # txt2 semibold region label
}


def static_label(text: str, role: str = "body", *, font_role: Optional[str] = None) -> QLabel:
    """A themed QLabel styled centrally by object name (theme.qss `#s*` rules) with
    NO per-widget restyle closure — safe to build hundreds of times inside a rebuild
    loop. `role` in {body, dim, key, sub}; its font is the matching TYPE_SCALE role
    (note `dim` is `value`-sized — dim by COLOUR only, never shrink it). Pass
    `font_role` to override the font tier. OUTSIDE a rebuild loop prefer body() /
    subhead() / dl(), which also retint AND track the widget's lifetime."""
    name, default_font = _STATIC_ROLE[role]
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setFont(T.scale_font(font_role or default_font))
    return lab


def static_status(text: str, kind: str = "mut") -> QLabel:
    """The no-restyler twin of tag(): a status marker (leading colour dot + text)
    styled by object name (`#sStat_{kind}`) so it registers NO restyler — for a
    high-frequency rebuild body. kind in {ok, warn, err, info, mut}; `mut` is neutral
    dim text with no dot. Use tag() (owner-tracked) for build-once widgets."""
    disp = text if kind == "mut" else f"● {text}"
    lab = QLabel(disp)
    lab.setObjectName(f"sStat_{kind}")
    lab.setFont(T.scale_font("footnote"))
    lab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return lab


# ── buttons ──────────────────────────────────────────────────────────────────
def btn(text: str, kind: str = "default", tip: str = "", on_click: Optional[Callable] = None) -> QPushButton:
    """kind in {default, primary, ghost}. Every interactive control gets a tooltip."""
    b = QPushButton(text)
    if kind != "default":
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(32)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda: on_click())
    return b


def toggle_chip(text: str, kind: str = "mut", active: bool = False,
                on_click: Optional[Callable] = None, tip: str = "") -> QPushButton:
    """A small clickable FILTER pill: a leading category dot (skipped for ``mut``) +
    label, with a selected (``active``) state. Quiet-Instrument idiom — colour rides
    the dot + text, the surface only lifts to a faint token fill when active, never a
    loud pill. Reusable chrome, so it lives here (not in a feature file, which may not
    call setStyleSheet). ``kind`` in {ok, warn, err, info, mut}. Clicking calls
    ``on_click()``. Radius is the shared 6px control radius (design-rules compliant)."""
    disp = text if kind == "mut" else f"● {text}"
    b = QPushButton(disp)
    b.setFont(T.ui_font(9, semibold=True))
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(True)
    b.setChecked(bool(active))
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    if tip:
        b.setToolTip(tip)

    def style():
        fg = T.t("txt2") if kind == "mut" else T.t(kind)
        if b.isChecked():
            b.setStyleSheet(
                f"QPushButton{{background:{T.t('tok')};color:{fg};border:1px solid {T.t('ln1')};"
                f"border-radius:{T.RADIUS_CONTROL}px;padding:3px 9px;}}")
        else:
            b.setStyleSheet(
                f"QPushButton{{background:transparent;color:{fg};border:1px solid transparent;"
                f"border-radius:{T.RADIUS_CONTROL}px;padding:3px 9px;}}"
                f"QPushButton:hover{{background:{T.t('tok')};}}")
    register_restyle(style, b)
    style()
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    return b


def menu_button(label: str, items, *, tip: str = "", kind: str = "default") -> QPushButton:
    """One button that opens a menu of related actions — progressive disclosure for a
    family of secondary actions (design-rules §2: when in doubt, remove; one focal
    point). Collapses a row of near-duplicate buttons into a single labelled control
    with a trailing ▾; each menu entry names WHAT it does via its own description
    (shown as the item tooltip). `items` is a sequence of (label, on_click, tip)
    tuples; a falsy entry (None) becomes a separator. Headless-safe: the QMenu is
    built now but only pops on click, so tests trigger `._menu` actions directly.

    Note this is a SECONDARY control — it carries no primary accent (the one accent
    lives in the ▶ flow), so `kind` should stay default/ghost."""
    from PyQt5.QtWidgets import QMenu
    b = QPushButton(f"{label}  ▾")            # trailing ▾ marks it a menu
    if kind != "default":
        b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumHeight(32)
    b.setToolTip(tip or f"More {label} actions")
    menu = QMenu(b)
    menu.setToolTipsVisible(True)                  # show each entry's description in the menu
    for it in items:
        if not it:
            menu.addSeparator(); continue
        ilabel, ion, itip = it
        act = menu.addAction(ilabel)
        if itip:
            act.setToolTip(itip); act.setStatusTip(itip)
        act.triggered.connect(lambda _checked=False, on=ion: on() if on else None)
    b._menu = menu                                 # keep a ref + a test/drive seam
    b.clicked.connect(lambda: menu.exec_(b.mapToGlobal(b.rect().bottomLeft())))
    return b


def token_button(text: str, on_click: Callable, tip: str = "") -> QPushButton:
    """A clickable machine identifier (e.g. an MCU part number) styled like a token."""
    b = QPushButton(text)
    b.setObjectName("tokbtn")
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    b.clicked.connect(lambda: on_click(text))
    return b


def token_link(text: str, on_click: Callable, tip: str = "", cat: Optional[str] = None,
               sub: str = "") -> QPushButton:
    """A clickable machine identifier rendered as PLAIN mono text — NO filled chip.
    Transparent by default, a subtle hover wash only. A grid of these reads as an
    aligned list of values, not a wall of boxes (design-rules §1.1: a part number is
    text, not a pill). `cat` colours the name by category (for a switching pin); `sub`
    adds a dim trailing token (e.g. the pin number) that disambiguates repeats."""
    b = QPushButton()
    b.setObjectName("toklink")
    b.setCursor(Qt.PointingHandCursor)
    b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    if tip:
        b.setToolTip(tip)
    h = QHBoxLayout(b)
    h.setContentsMargins(8, 4, 8, 4)
    h.setSpacing(8)
    name = QLabel(text)
    name.setFont(T.mono_font(11, semibold=bool(cat)))
    name.setAttribute(Qt.WA_TransparentForMouseEvents)   # clicks fall through to the button
    h.addWidget(name)
    sublab = None
    if sub:
        sublab = QLabel(sub)
        sublab.setFont(T.mono_font(10))
        sublab.setAttribute(Qt.WA_TransparentForMouseEvents)
        h.addWidget(sublab)
    h.addStretch(1)

    def style():
        col = T.category(cat) if cat else T.t("txt1")
        name.setStyleSheet(f"color:{col};background:transparent;")
        if sublab is not None:
            sublab.setStyleSheet(f"color:{T.t('txt3')};background:transparent;")
        b.setStyleSheet(
            f"QPushButton#toklink{{background:transparent;border:none;border-radius:{T.RADIUS_CONTROL}px;}}"
            f"QPushButton#toklink:hover{{background:{T.t('ctl_hover')};}}")
    register_restyle(style, b)
    b.clicked.connect(lambda: on_click(text))
    return b


def subhead(text: str) -> QLabel:
    """A quiet Title-case region label (Segoe UI 11/Semibold, text_2), no hairline.
    Use inside a card or a control bar where a card edge or the row itself already
    separates the region, so section_header's trailing rule would be redundant.
    In a rebuild loop use static_label(role='sub') — the no-restyler twin."""
    lab = QLabel(text)
    lab.setFont(T.ui_font(11, semibold=True))
    register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;"), lab)
    return lab


def section_header(text: str) -> QWidget:
    """A Title-case section label with a trailing hairline (design-rules §4). The
    quiet, ship-looking section break — use where an eyebrow's letterspaced UPPERCASE
    would just be one more shouted micro-label."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    lab = QLabel(text)
    lab.setFont(T.ui_font(11, semibold=True))
    rule = QFrame()
    rule.setFixedHeight(1)
    rule.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    h.addWidget(lab)
    h.addWidget(rule, 1)

    def style():
        lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
        rule.setStyleSheet(f"background:{T.t('stroke')};border:none;")
    register_restyle(style, w)
    return w


class Segmented(QWidget):
    """A mono segmented control (package selector, profile switch, ...)."""

    def __init__(self, options: Sequence[str], on_change: Optional[Callable[[str], None]] = None,
                 selected: int = 0, tip: str = "", parent=None):
        super().__init__(parent)
        self._on_change = on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self._buttons: List[QPushButton] = []
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setObjectName("seg")
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("selected", i == selected)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=i: self._pick(k))
            lay.addWidget(b)
            self._buttons.append(b)
        register_restyle(self._style, self)

    def _style(self):
        self.setStyleSheet(f"QWidget{{background:{T.t('ctl')};border:1px solid {T.t('stroke')};"
                           f"border-radius:{T.RADIUS_CONTROL}px;}}")

    def on_change(self, fn):
        """Set (or replace) the callback invoked with the selected option's text."""
        self._on_change = fn

    def select(self, k: int):
        """Show segment k as selected WITHOUT firing on_change — for reflecting a
        change that happened elsewhere (e.g. an app-wide setting synced over the bus)."""
        for i, b in enumerate(self._buttons):
            b.setProperty("selected", i == k)
            b.style().unpolish(b); b.style().polish(b)

    def select_value(self, text: str):
        """Silently select the segment whose label is `text` (no-op if absent)."""
        for i, b in enumerate(self._buttons):
            if b.text() == text:
                self.select(i)
                return

    def _pick(self, k: int):
        self.select(k)
        if self._on_change:
            self._on_change(self._buttons[k].text())


# ── surfaces ─────────────────────────────────────────────────────────────────
class Card(QFrame):
    """One elevation step: a rounded surface, no heavy border. Content via .body."""

    def __init__(self, pad: int = None, parent=None):
        super().__init__(parent)
        self.setObjectName("ndcard")
        pad = T.sp("card") if pad is None else pad     # default card interior padding (16)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(pad, pad, pad, pad)
        self.body.setSpacing(T.sp("row"))
        register_restyle(self._style, self)

    def _style(self):
        # Borderless elevation (design-rules §1.2): a card separates by its raised
        # background step, never a stroke. Scoped to #ndcard so labels inside don't
        # inherit a frame (QLabel subclasses QFrame).
        self.setStyleSheet(f"QFrame#ndcard{{background:{T.t('card')};border:none;"
                           f"border-radius:{T.RADIUS_CONTAINER}px;}}")


def hstack(*widgets, spacing: int = 10, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


# ── verdict / InfoBar ────────────────────────────────────────────────────────
class Verdict(QFrame):
    """The buildability / status bar: icon dot, title + subtitle, chips on the right."""

    def __init__(self, title: str, subtitle: str = "", kind: str = "ok",
                 chips: Optional[Sequence[Tuple[str, str, str]]] = None, plain: bool = False, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._plain = plain
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(16)
        # Leading status dot: honour `kind` (ok/warn/err) with the small colored dot
        # promised in the docstring, using the tag idiom (color on the smallest
        # element). `plain=True` means no dot — the band reads as neutral chrome.
        self._dot = None
        if not plain:
            self._dot = QLabel(); self._dot.setFixedSize(9, 9)
            lay.addWidget(self._dot, 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        self._title = QLabel(title); self._title.setFont(T.ui_font(10, semibold=True))
        text.addWidget(self._title)
        self._sub = None
        if subtitle:
            self._sub = QLabel(subtitle); self._sub.setFont(T.ui_font(9)); self._sub.setWordWrap(True)
            text.addWidget(self._sub)
        lay.addLayout(text)
        lay.addStretch(1)
        for label, value, dotkind in (chips or ()):
            lay.addWidget(self._chip(label, value, dotkind))
        register_restyle(self._style, self)

    def _chip(self, label: str, value: str, dotkind: str) -> QWidget:
        w = QFrame(); w.setObjectName("ndchip")
        h = QHBoxLayout(w); h.setContentsMargins(11, 4, 12, 4); h.setSpacing(7)
        dot = QLabel(); dot.setFixedSize(7, 7)
        lab = QLabel(label); lab.setFont(T.ui_font(9))
        h.addWidget(dot); h.addWidget(lab)
        if value:
            val = QLabel(value); val.setFont(T.ui_font(9, semibold=True)); h.addWidget(val)

        def style():
            colmap = {"ok": T.t("ok"), "warn": T.t("warn"), "err": T.t("err")}
            dot.setStyleSheet(dot_css(colmap.get(dotkind, T.t('txt3')), 7))
            lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
            # a genuine status tag, but the stadium pill + border is the retired idiom:
            # a quiet 6px token on the tok fill, borderless (design-rules §1.1/§1.2).
            w.setStyleSheet(f"QFrame#ndchip{{background:{T.t('tok')};border:none;"
                            f"border-radius:{T.RADIUS_CONTROL}px;}}")
        register_restyle(style, w)
        return w

    def _style(self):
        # Neutral surface always: a status band never tints its background with a
        # category hue (design-rules §1.6/§5). Status is carried by the leading dot
        # and the chip dots, so the surface stays neutral. `kind` colors the dot,
        # not the background (color on the smallest element). Matches the Library band.
        bg = T.t("card")
        self.setObjectName("ndverdict")
        self.setStyleSheet(f"QFrame#ndverdict{{background:{bg};border:none;"
                           f"border-radius:{T.RADIUS_CONTAINER}px;}}")
        if self._dot is not None:
            colmap = {"ok": T.t("ok"), "warn": T.t("warn"), "err": T.t("err")}
            self._dot.setStyleSheet(dot_css(colmap.get(self._kind, T.t('txt3')), 9))
        self._title.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
        if self._sub is not None:
            self._sub.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")


# ── persistent verdict band (the recipe's colour verdict header) ─────────────
@dataclass
class VerdictState:
    """One computed verdict for a workbench sub-surface (spec §3). `kind` colours the
    leading dot; `chips` is a right-aligned row of (label, value, dotkind) summaries."""
    kind: str = "mut"                 # ok | warn | err | info | mut
    title: str = ""
    subtitle: str = ""
    chips: Sequence[Tuple[str, str, str]] = ()


class VerdictSlot(QFrame):
    """A PERSISTENT, quiet-when-OK verdict band, built ONCE and mutated in place.

    Unlike Verdict (rebuilt-and-swapped, registering a restyler per chip + per label —
    the SHELL-06 churn), VerdictSlot builds a fixed structure once (dot · title ·
    subtitle · a fixed pool of `chip_slots` chips) and registers EXACTLY ONE restyler,
    owned by self. `.set(state)` mutates the existing labels' text + the stored kind and
    calls that one `_style()` — it registers nothing, so a high-frequency refresh never
    grows the retint registry. `.set(None)` hides the band (quiet-when-OK, per BENCH-14).

    Colour discipline: every colour-bearing child (dot, title, subtitle, each chip dot /
    label / value) is a PLAIN QLabel with no restyler of its own; the single `_style`
    recolours them all from the active theme + the stored kinds, so a theme toggle retints
    the whole band through the one registration."""

    def __init__(self, *, chip_slots: int = 3, parent=None):
        super().__init__(parent)
        self.setObjectName("ndverdict")
        self._kind = "mut"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(16)

        self._dot = QLabel(); self._dot.setFixedSize(9, 9)
        lay.addWidget(self._dot, 0, Qt.AlignVCenter)

        text = QVBoxLayout(); text.setSpacing(1)
        self._title = QLabel(""); self._title.setFont(T.ui_font(10, semibold=True))
        self._sub = QLabel(""); self._sub.setFont(T.ui_font(9)); self._sub.setWordWrap(True)
        self._sub.setVisible(False)
        text.addWidget(self._title)
        text.addWidget(self._sub)
        lay.addLayout(text)
        lay.addStretch(1)

        # A FIXED pool of chips built once; set() shows/relabels them, never creates more.
        self._chips = []
        for _ in range(max(0, chip_slots)):
            frame = QFrame(); frame.setObjectName("ndchip")
            h = QHBoxLayout(frame); h.setContentsMargins(11, 4, 12, 4); h.setSpacing(7)
            cdot = QLabel(); cdot.setFixedSize(7, 7)
            clab = QLabel(""); clab.setFont(T.ui_font(9))
            cval = QLabel(""); cval.setFont(T.ui_font(9, semibold=True))
            h.addWidget(cdot); h.addWidget(clab); h.addWidget(cval)
            frame.setVisible(False)
            lay.addWidget(frame)
            self._chips.append({"frame": frame, "dot": cdot, "lab": clab,
                                "val": cval, "dotkind": "mut"})

        self.setVisible(False)          # an unset band is hidden until .set(state)
        register_restyle(self._style, self)   # the ONE registration

    def _style(self):
        # Neutral card surface always (a status band never tints its background with a
        # category hue — design-rules §1.6/§5); status rides the leading dot + chip dots.
        colmap = {"ok": T.t("ok"), "warn": T.t("warn"), "err": T.t("err"), "info": T.t("info")}
        self.setStyleSheet(f"QFrame#ndverdict{{background:{T.t('card')};border:none;"
                           f"border-radius:{T.RADIUS_CONTAINER}px;}}")
        self._dot.setStyleSheet(dot_css(colmap.get(self._kind, T.t('txt3')), 9))
        self._title.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
        self._sub.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
        for c in self._chips:
            c["dot"].setStyleSheet(dot_css(colmap.get(c['dotkind'], T.t('txt3')), 7))
            c["lab"].setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
            c["val"].setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
            c["frame"].setStyleSheet(f"QFrame#ndchip{{background:{T.t('tok')};border:none;"
                                     f"border-radius:{T.RADIUS_CONTROL}px;}}")

    def set(self, state: Optional[VerdictState]) -> None:
        """Show + update the band from `state`, IN PLACE; `None` hides it (quiet-when-OK).
        Registers nothing — mutates the existing labels and re-runs the one `_style()`."""
        if state is None:
            self.setVisible(False)
            return
        self.setVisible(True)
        self._kind = state.kind or "mut"
        self._title.setText(state.title or "")
        self._sub.setText(state.subtitle or "")
        self._sub.setVisible(bool(state.subtitle))
        chips = list(state.chips or ())
        for i, c in enumerate(self._chips):
            if i < len(chips):
                label, value, dotkind = chips[i]
                c["lab"].setText(str(label))
                c["val"].setText(str(value))
                c["val"].setVisible(bool(value))
                c["dotkind"] = dotkind or "mut"
                c["frame"].setVisible(True)
            else:
                c["frame"].setVisible(False)
        self._style()                   # recolour in place — no new registration


# ── active refresh region (chrome-once, fill-in-place) ───────────────────────
@dataclass
class RefreshHandle:
    """The handle a workbench detail builder receives so it can drive its own refresh.
    `refresh` fills the pre-built bodies in place (cheap, high-frequency); `rebuild`
    re-invokes the detail builder for a NEW object selection (deferred, segfault-safe);
    `snapshot` re-reads the current GUI-thread selection dict."""
    refresh: Callable[[], None]
    rebuild: Callable[[], None]
    snapshot: Callable[[], dict]


class RefreshRegion(QWidget):
    """Hosts an ACTIVE noun-first detail slot (spec §4). `detail(snapshot, handle)` returns
    `(chrome_widget, fill_fn)`: the chrome is built ONCE (real W.* helpers — restylers are
    fine build-once); `fill_fn(snapshot)` repopulates the chrome's pre-built bodies using the
    static vocabulary (no restylers).

    `handle.refresh()` runs `fill_fn` — it NEVER re-invokes `detail()`, so a high-frequency
    refresh (a watchdog tick, a re-audit) keeps the retint registry flat instead of
    re-registering every build-once restyler each tick (the B2/SHELL-06 guard). `handle
    .rebuild()` re-invokes `detail()` for a changed OBJECT selection, DEFERRED via
    `QTimer.singleShot(0)` (the in-signal use-after-free segfault guard, per bare._rebuild).
    `refresh()` no-ops while `busy()` is set — the re-entrancy guard against an in-flight
    primary flow (a commit worker writing the tree must not race a watchdog refresh)."""

    def __init__(self, ctx, snapshot: Callable[[], dict],
                 detail: Callable[[dict, "RefreshHandle"], Tuple[QWidget, Callable[[dict], None]]],
                 *, busy: Optional[Callable[[], bool]] = None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._snapshot = snapshot
        self._detail = detail
        self._busy = busy
        self._fill: Optional[Callable[[dict], None]] = None
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self.handle = RefreshHandle(refresh=self._refresh, rebuild=self._rebuild,
                                    snapshot=snapshot)
        self._build()

    def _build(self):
        """Mount fresh chrome + do the initial fill. On the rebuild path this first clears
        the previous chrome (its restylers drop on the deferred destroy)."""
        from .util import clear_layout
        clear_layout(self._root)
        snap = self._snapshot()
        chrome, fill = self._detail(snap, self.handle)
        self._fill = fill
        self._root.addWidget(chrome)
        if callable(fill):
            fill(snap)

    def _refresh(self):
        if self._busy is not None and self._busy():
            return                              # a primary flow is in flight — no race
        if callable(self._fill):
            self._fill(self._snapshot())

    def _rebuild(self):
        QTimer.singleShot(0, self._build)       # deferred: never tear down inside a signal


class CollapsibleSection(QWidget):
    """A demoted, collapsed-by-default section (spec §6): a header (chevron ▸/▾ + title)
    that toggles a body. Machinery / exports live here — present but never in the way. An
    empty section (`body is None`) hides itself entirely, so a workbench with no machinery
    shows no dead header. Build-once (never in a refresh loop), so the title's owner-tracked
    restyler is fine; the chevron reuses the central #consoleChevron QSS."""

    def __init__(self, title: str, body: Optional[QWidget] = None, *, parent=None):
        super().__init__(parent)
        self._body = body
        self._expanded = False
        self._title = title
        self._dot = None
        self._head_w = None
        self._dirty = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(T.sp("sm"))
        if body is None:
            self.setVisible(False)              # no content → no section at all
            self._chevron = None
            return
        self._chevron = QPushButton("▸")
        self._chevron.setObjectName("consoleChevron")
        self._chevron.setCursor(Qt.PointingHandCursor)
        self._chevron.setFixedSize(24, 24)
        self._chevron.setToolTip(f"Show {title}")
        self._chevron.clicked.connect(self.toggle)
        head = QHBoxLayout(); head.setContentsMargins(0, 0, 0, 0); head.setSpacing(T.sp("md"))
        head.addWidget(self._chevron)
        head.addWidget(subhead(title))
        # An unsaved-change dot: hidden until set_dirty(True), so a collapsed section's
        # edits stay visible in the Save preview scope while scrolling (design contract §6).
        self._dot = QLabel()
        self._dot.setFixedSize(6, 6)
        register_restyle(lambda: self._dot.setStyleSheet(dot_css(T.t("accent"), 6)), self._dot)
        self._dot.setVisible(False)
        head.addWidget(self._dot)
        head.addStretch(1)
        head_w = QWidget(); head_w.setLayout(head)
        head_w.setCursor(Qt.PointingHandCursor)
        self._head_w = head_w
        v.addWidget(head_w)
        body.setVisible(False)                  # collapsed by default
        v.addWidget(body)
        self.setVisible(True)                    # the section itself shows (body stays collapsed)

    def set_dirty(self, on: bool):
        """Show/hide the header's unsaved-change dot. No-op on an empty (bodyless)
        section. Safe to call repeatedly (drives the Save preview scope indicator)."""
        if self._dot is None:
            return
        self._dirty = bool(on)
        self._dot.setVisible(self._dirty)
        if self._head_w is not None:
            self._head_w.setToolTip(f"{self._title} has unsaved changes" if self._dirty else "")

    def is_dirty(self) -> bool:
        # An explicit flag, not _dot.isVisible() — a child's isVisible() is False whenever
        # the top-level window is not shown (headless tests / offscreen), which would make
        # the state untestable.
        return self._dirty

    def set_expanded(self, on: bool):
        self._expanded = bool(on)
        if self._body is not None:
            self._body.setVisible(self._expanded)
        if self._chevron is not None:
            self._chevron.setText("▾" if self._expanded else "▸")

    def toggle(self):
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded


# ── definition list ──────────────────────────────────────────────────────────
def dl(pairs: Sequence[Tuple[str, QWidget]], key_width: int = 136,
       row_gap: int = 12) -> QWidget:
    """Two-column key/value list. Value is any widget (label, token, tag). `row_gap`
    is the vertical space between rows (default 12; the identity canvas passes the
    design-contract detail-row gap of 10)."""
    w = QWidget()
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16)
    grid.setVerticalSpacing(row_gap)
    grid.setColumnMinimumWidth(0, key_width)
    for r, (k, v) in enumerate(pairs):
        key = QLabel(k); key.setFont(T.ui_font(10))
        register_restyle(lambda key=key: key.setStyleSheet(f"color:{T.t('txt2')};background:transparent;"), key)
        grid.addWidget(key, r, 0, Qt.AlignTop)
        if isinstance(v, str):
            v = body(v)
        grid.addWidget(v, r, 1, Qt.AlignTop)
    grid.setColumnStretch(1, 1)
    return w


# ── data table ───────────────────────────────────────────────────────────────
def data_table(columns: Sequence[str], rows: Sequence[Sequence], stretch_col=0,
               mono_cols=(), dim_cols=(), max_col: int = 300, wrap: bool = False,
               row_tints=(), row_tips=None) -> QTableWidget:
    """A structured table with light row and column separators.

    Cells may be a plain str (rendered as a native item) or a QWidget (tokens,
    tags, asset flags). Plain-text columns can be styled mono or dim by index via
    `mono_cols` / `dim_cols`. `stretch_col` accepts one index or several; stretched
    columns share the leftover width. Non-stretch columns are content-sized but
    capped at `max_col`.

    `row_tints` is an iterable of row indices to lift with a subtle inset background
    step (design-rules §2: elevation via a background step, never a colour wash) — the
    'this row needs attention' cue, kept theme-aware. `row_tips` is an optional {row:
    tooltip} map: a tinted row's cells (and any widget cell) carry that one explanation
    on hover, so the reason for the lift is one hover away. Both cover widget cells too
    (wrapped in a tinted holder), so a badge column tints uniformly with its row.

    `wrap=True` wraps long cell text onto extra lines and grows the row height to
    fit (rows auto-size), instead of eliding — so a wide BOM stays readable in the
    panel width without a horizontal scrollbar (PROJ-07). Every cell's text colour
    comes from the active theme (PROJ-07: items no longer fall back to Qt's default
    black, which was unreadable in dark mode)."""
    from PyQt5.QtGui import QColor
    stretch = set(stretch_col) if isinstance(stretch_col, (list, tuple, set)) else {stretch_col}
    mono_cols = set(mono_cols); dim_cols = set(dim_cols)
    tint_rows = set(row_tints); row_tips = row_tips or {}
    tinted_holders = []                                   # (holder, ) widget cells to re-tint on theme

    class _RowTintDelegate(QStyledItemDelegate):
        """Paint the subtle inset lift for tinted rows BENEATH the cell text. A delegate is
        used (not item.setBackground) because a QTableWidget carrying a stylesheet suppresses
        item background brushes — the delegate paints directly, so the row reads uniformly."""
        def paint(self, painter, option, index):
            if index.row() in tint_rows:
                painter.fillRect(option.rect, _qcolor(T.t("inset")))
            super().paint(painter, option, index)

    tbl = QTableWidget(len(rows), len(columns))
    # Title-case headers (not letterspaced UPPERCASE): narrower, so a payload header
    # like "Manufacturer" stops clipping in a stretch column, and consistent with the
    # app's Title-case copy. A per-header tooltip is the safety net if one still elides.
    tbl.setHorizontalHeaderLabels(list(columns))
    for c in range(len(columns)):
        hi = tbl.horizontalHeaderItem(c)
        if hi is not None:
            hi.setToolTip(columns[c])
    tbl.verticalHeader().hide()
    tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
    tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
    tbl.setSelectionMode(QAbstractItemView.SingleSelection)
    # Borderless ledger (design-rules §1.2 / §4): no cell grid — vertical rules are
    # noise. Row separation is a single 1px bottom hairline per ::item (below), plus
    # the header underline. Never a box around every cell.
    tbl.setShowGrid(False)
    tbl.setWordWrap(wrap)
    tbl.setTextElideMode(Qt.ElideNone if wrap else Qt.ElideRight)
    # wrap=True (the PROJ-07 wrapping BOM) reflows into extra lines and must never
    # scroll sideways — hide the h-scrollbar. wrap=False content-sizes fixed columns
    # capped at max_col; when the table is wider than its pane those columns would
    # otherwise be clipped with no way to reach them, so keep the scrollbar as-needed
    # (design-rules §10: long content scrolls inside its own container).
    tbl.setHorizontalScrollBarPolicy(
        Qt.ScrollBarAlwaysOff if wrap else Qt.ScrollBarAsNeeded)
    tbl.horizontalHeader().setHighlightSections(False)
    tbl.horizontalHeader().setMinimumSectionSize(52)
    if wrap:
        # Let each row grow tall enough to show its wrapped lines.
        tbl.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    else:
        tbl.verticalHeader().setDefaultSectionSize(34)

    mono = T.mono_font(9)
    for r, row in enumerate(rows):
        tip = row_tips.get(r)
        for cidx, cell in enumerate(row):
            if isinstance(cell, QWidget):
                if r in tint_rows:
                    # Wrap the widget so the tint paints behind it too (the widget itself
                    # stays transparent). The holder carries the row tooltip.
                    holder = QWidget()
                    hl = QHBoxLayout(holder); hl.setContentsMargins(6, 0, 6, 0); hl.setSpacing(0)
                    hl.addWidget(cell); hl.addStretch(1)
                    if tip:
                        holder.setToolTip(tip)
                    tinted_holders.append(holder)
                    tbl.setCellWidget(r, cidx, holder)
                else:
                    if tip:
                        cell.setToolTip(tip)
                    tbl.setCellWidget(r, cidx, cell)
            else:
                it = QTableWidgetItem(str(cell))
                if cidx in mono_cols:
                    it.setFont(mono)
                it.setToolTip(tip or str(cell))
                tbl.setItem(r, cidx, it)

    hdr = tbl.horizontalHeader()
    fm = tbl.fontMetrics()
    def _text_w(s):
        return fm.horizontalAdvance(s) if hasattr(fm, "horizontalAdvance") else fm.width(s)
    for c in range(len(columns)):
        if c in stretch:
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
            continue
        hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        wmax = _text_w(columns[c]) + 24
        for r in range(len(rows)):
            cw = tbl.cellWidget(r, c)
            if cw is not None:
                wmax = max(wmax, cw.sizeHint().width())
            else:
                it = tbl.item(r, c)
                if it is not None:
                    wmax = max(wmax, _text_w(it.text()) + 24)
        wmax = min(wmax, max_col)
        pad = 26 + (18 if c == len(columns) - 1 and c not in stretch else 0)
        tbl.setColumnWidth(c, wmax + pad)

    def _style():
        # Colour EVERY plain item explicitly (PROJ-07): a QTableWidget's
        # stylesheet `color:` doesn't reliably reach item text through the
        # delegate, so unstyled cells fell back to Qt's default black — invisible
        # in dark mode. Dim columns get txt2, the rest txt1, per active theme.
        # txt2 (not txt3): dimmed columns still carry DATA (part values, lead weeks),
        # and txt3 is the micro-label tier — at rgba(0,0,0,.447) it composites to ~3.3:1
        # on the light card, below WCAG AA (4.5:1). txt2 (~6.2:1) stays clearly dimmer
        # than the txt1 keys while remaining legible; txt3 is reserved for real labels.
        base = _qcolor(T.t("txt1"))
        dim = _qcolor(T.t("txt2"))
        for r in range(len(rows)):
            for c in range(len(columns)):
                it = tbl.item(r, c)
                if it is not None:
                    it.setForeground(dim if c in dim_cols else base)
        # Tinted rows are painted by the delegate (item background brushes are suppressed
        # under a table stylesheet); widget cells sit above the delegate, so their holder
        # carries the same inset step directly.
        for holder in tinted_holders:
            try:
                holder.setStyleSheet(f"background:{T.t('inset')};")
            except RuntimeError:                          # holder deleted by a rebuild
                pass
        tbl.viewport().update()
        # Borderless ledger: no gridline-color (grid is off). Each row gets ONE 1px
        # bottom hairline via ::item — horizontal dividers only, no vertical rules,
        # no box. Full-row hover is the single inset lift (SelectRows makes it span
        # the row). Header keeps its underline.
        tbl.setStyleSheet(
            f"QTableWidget{{background:transparent;"
            f"color:{T.t('txt1')};selection-background-color:{T.t('ctl')};selection-color:{T.t('txt1')};}}"
            f"QTableWidget::item{{border:none;border-bottom:1px solid {T.t('stroke')};}}"
            f"QTableWidget::item:hover{{background:{T.t('inset')};}}"
            f"QHeaderView::section{{background:transparent;color:{T.t('txt2')};border:none;"
            f"border-bottom:1px solid {T.t('stroke')};padding:6px 8px;font-weight:600;}}")
    if tint_rows:
        _delegate = _RowTintDelegate(tbl)
        tbl.setItemDelegate(_delegate)
        tbl._row_tint_delegate = _delegate                # keep a ref alive with the table
    register_restyle(_style, tbl)
    return tbl


# ── Phase B: shared polish patterns ──────────────────────────────────────────
def empty_state(line: str, glyph: str = "", sub: str = "",
                action: Optional[QWidget] = None) -> QWidget:
    """The ONE quiet empty-state pattern: an optional muted glyph, one Title-case
    line, an optional sub-line, and at most one action. Centered — design-rules §10
    permits centering for genuine empty states only. Replaces raw gray strings
    ("Not looked up yet.", "No Symbol", "3D Preview Unavailable")."""
    w = QWidget()
    outer = QVBoxLayout(w)
    outer.setContentsMargins(24, 40, 24, 40)
    outer.setSpacing(0)
    outer.addStretch(1)
    if glyph:
        icon = QLabel()
        icon.setAlignment(Qt.AlignHCenter)
        # Re-render the pixmap from a theme tier on every toggle: svg_icon's
        # hard-coded neutral gray would keep the same mid-gray on a light/dark
        # flip while the caption below re-tints, so tint the glyph from txt3 and
        # register_restyle with the QLabel as owner (auto-unregisters on destroy).
        def _tint_glyph(_icon=icon, _glyph=glyph):
            _icon.setPixmap(svg_icon(_glyph, size=30, color=T.t("txt3")).pixmap(30, 30))
        register_restyle(_tint_glyph, icon)
        outer.addWidget(icon, 0, Qt.AlignHCenter)
        outer.addSpacing(12)
    ln = body(line)
    ln.setAlignment(Qt.AlignHCenter)
    register_restyle(lambda: ln.setStyleSheet(f"color:{T.t('txt2')};background:transparent;"), ln)
    outer.addWidget(ln, 0, Qt.AlignHCenter)
    if sub:
        s = body(sub, dim=True)
        s.setAlignment(Qt.AlignHCenter)
        s.setWordWrap(True)
        outer.addSpacing(4)
        outer.addWidget(s, 0, Qt.AlignHCenter)
    if action is not None:
        outer.addSpacing(16)
        outer.addWidget(action, 0, Qt.AlignHCenter)
    outer.addStretch(1)
    return w


class Skeleton(QWidget):
    """A placeholder loading block: a rounded `inset` rect with a soft highlight
    sweeping across it. Honors reduced motion — static (no animation) when set, so
    the render gate and reduced-motion users get a calm block, not a shimmer."""

    def __init__(self, width: Optional[int] = None, height: int = 12, parent=None):
        super().__init__(parent)
        self._pos = 0.0
        self._anim = None
        self.setFixedHeight(height)
        if width is not None:
            self.setFixedWidth(width)
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        from .motion import reduced_motion
        if not reduced_motion():
            from PyQt5.QtCore import QVariantAnimation
            a = QVariantAnimation(self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(1100)
            a.setLoopCount(-1)
            a.valueChanged.connect(self._on_tick)
            a.start()
            self._anim = a

    def _on_tick(self, v):
        self._pos = float(v)
        self.update()

    def hideEvent(self, ev):
        # Stop driving GUI-thread repaints once this skeleton is off-screen. When
        # real content swaps in, skeleton_rows' N placeholders are hidden (or
        # linger pending-delete) — without this every one keeps repainting an
        # invisible shimmer at ~30fps until GC. Pause (not stop) so a re-shown
        # placeholder resumes from where it was.
        from PyQt5.QtCore import QAbstractAnimation
        if self._anim is not None and self._anim.state() == QAbstractAnimation.Running:
            self._anim.pause()
        super().hideEvent(ev)

    def showEvent(self, ev):
        from PyQt5.QtCore import QAbstractAnimation
        if self._anim is not None and self._anim.state() == QAbstractAnimation.Paused:
            self._anim.resume()
        super().showEvent(ev)

    def paintEvent(self, _ev):
        from PyQt5.QtGui import QPainter, QLinearGradient, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        r = self.rect()
        p.setBrush(T.qcolor("inset"))
        p.drawRoundedRect(r, T.RADIUS_CONTROL, T.RADIUS_CONTROL)
        if self._anim is not None:
            hi = T.qcolor("ctl_hover")
            clear = QColor(hi); clear.setAlpha(0)
            x = self._pos
            grad = QLinearGradient(r.left(), 0.0, r.right(), 0.0)
            grad.setColorAt(max(0.0, x - 0.18), clear)
            grad.setColorAt(min(1.0, x), hi)
            grad.setColorAt(min(1.0, x + 0.18), clear)
            p.setBrush(grad)
            p.drawRoundedRect(r, T.RADIUS_CONTROL, T.RADIUS_CONTROL)
        p.end()


def skeleton_rows(rows: int, cols: int, parent=None) -> QWidget:
    """A block of skeleton rows for a table/list that is loading. Column widths taper
    so it reads as data, not a grid of identical bars."""
    w = QWidget(parent)
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(12)
    widths = (120, 80, 200, 100, 90, 140)   # deterministic taper, cycled
    for _r in range(rows):
        rw = QWidget()
        h = QHBoxLayout(rw)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(16)
        for c in range(cols):
            h.addWidget(Skeleton(width=widths[c % len(widths)]))
        h.addStretch(1)
        v.addWidget(rw)
    return w


def apply_popover_shadow(w: QWidget) -> None:
    """One soft drop shadow for popovers / menus / dialogs ONLY (never cards —
    surfaces separate by a background step, not a shadow). design-rules §4."""
    from PyQt5.QtWidgets import QGraphicsDropShadowEffect
    from PyQt5.QtGui import QColor
    eff = QGraphicsDropShadowEffect(w)
    eff.setBlurRadius(28)
    eff.setXOffset(0)
    eff.setYOffset(8)
    eff.setColor(QColor(0, 0, 0, 90))
    w.setGraphicsEffect(eff)


# ── the modular sub-feature host ─────────────────────────────────────────────
Panel = Tuple[str, Callable]   # (title, builder(ctx)->QWidget)


class Workspace(QWidget):
    """A feature's root: a page title + optional header controls, a sub-tab bar
    built from a list of Panels, and a lazily-built stacked content area.

    Panels are list-driven: add a (title, builder) tuple to add a sub-feature,
    remove it to drop one. The shell knows nothing about panels."""

    def __init__(self, ctx, title: str, panels: Sequence[Panel],
                 header: Optional[QWidget] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("workspace")
        self._ctx = ctx
        self._panels = list(panels)
        self._built = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(T.sp("page"), T.sp("lg"), T.sp("page"), T.sp("sm"))
        head.setSpacing(T.sp("lg"))
        head.addWidget(page_title(title))
        if header is not None:
            head.addStretch(1)
            head.addWidget(header)
        else:
            head.addStretch(1)
        root.addLayout(head)

        # A single-panel feature (e.g. the merged Library) shows no sub-tab bar —
        # a lone tab under the page title is redundant chrome. The page title +
        # optional header row carry it. Multi-panel features keep the tab bar.
        self._tabs: List[QPushButton] = []
        self._underline = None
        if len(self._panels) > 1:
            bar = QHBoxLayout()
            bar.setContentsMargins(24, 0, 24, 0)
            bar.setSpacing(2)
            for i, (name, _) in enumerate(self._panels):
                b = QPushButton(name)
                b.setObjectName("subtab")
                b.setCursor(Qt.PointingHandCursor)
                b.setProperty("selected", i == 0)
                b.clicked.connect(lambda _=False, k=i: self._select(k))
                bar.addWidget(b)
                self._tabs.append(b)
            bar.addStretch(1)
            root.addLayout(bar)

            rule = QFrame(); rule.setFixedHeight(1)
            register_restyle(lambda: rule.setStyleSheet(f"background:{T.t('divider')};border:none;"), rule)
            root.addWidget(rule)

            # the active-tab underline is a painted rule that slides between tabs
            # (ui.motion), overlaid on this Workspace — not a QSS border-bottom.
            from .motion import SlidingUnderline
            self._underline = SlidingUnderline(self)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        for _ in self._panels:
            self._stack.addWidget(QWidget())   # placeholders, built on first show
        self._select(0)

    def select_panel(self, title: str):
        """Switch to a sub-panel by its title (used for cross-panel navigation)."""
        for i, (name, _) in enumerate(self._panels):
            if name == title:
                self._select(i)
                return

    def rebuild_all(self):
        """Drop every built sub-panel so each rebuilds fresh the next time it is shown,
        and rebuild the currently-visible one now. Use when shared upstream state (e.g.
        the Bench package selection) changes and the cached panels would otherwise show
        stale data — the lazy _built cache never rebuilds on its own."""
        cur = self._stack.currentIndex()
        self._built.clear()
        for k in range(len(self._panels)):           # reset non-current panels to placeholders
            if k == cur:
                continue
            old = self._stack.widget(k)
            if old is not None:
                self._stack.removeWidget(old); old.deleteLater()
            self._stack.insertWidget(k, QWidget())
        self._select(cur)                            # cur not in _built -> rebuilds now

    def _position_underline(self, animate: bool = True):
        """Place the sliding underline under the active subtab. Called on selection
        and on resize (button geometry is only final after layout)."""
        u = getattr(self, "_underline", None)
        if u is None or not self._tabs:
            return
        k = next((i for i, b in enumerate(self._tabs) if b.property("selected")), 0)
        btn = self._tabs[k]
        if btn.width() <= 0:
            return                                   # not laid out yet
        bl = btn.mapTo(self, btn.rect().bottomLeft())
        u.setGeometry(u.x(), bl.y(), u.width(), u.height())   # lock the y baseline
        u.move_to(bl.x(), btn.width(), animate=animate and u.isVisible())
        u.show(); u.raise_()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._position_underline(animate=False)

    def _select(self, k: int):
        for i, b in enumerate(self._tabs):
            b.setProperty("selected", i == k)
            b.style().unpolish(b); b.style().polish(b)
        self._position_underline()
        if k not in self._built:
            try:
                w = self._panels[k][1](self._ctx)
            except Exception as e:  # noqa: BLE001
                w = _error_panel(self._panels[k][0], e)
            old = self._stack.widget(k)
            self._stack.removeWidget(old); old.deleteLater()
            self._stack.insertWidget(k, w)
            self._built[k] = w
        self._stack.setCurrentIndex(k)


def scroll_body(widget: QWidget) -> QWidget:
    """Wrap a panel body in a vertical scroll area with the standard page padding."""
    from PyQt5.QtWidgets import QScrollArea
    holder = QWidget(); holder.setObjectName("scrollHolder")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(T.sp("page"), T.sp("lg"), T.sp("page"), T.sp("page"))
    lay.setSpacing(T.sp("path"))
    lay.addWidget(widget)
    lay.addStretch(1)
    area = QScrollArea(); area.setObjectName("scrollArea")
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidget(holder)
    # The scroll chrome must not inject the palette base — keep it transparent so the
    # themed page background shows through (content paints its own cards). Without this
    # the viewport renders a light pane in dark mode. Every rule is id-SCOPED: a bare
    # `background: transparent` on the viewport (no selector) cascades to EVERY
    # descendant and wipes the accent fill off child #primary buttons (they then read
    # as disabled dark-on-dark). Naming the viewport and scoping by id keeps it
    # transparent without leaking onto children.
    vp = area.viewport(); vp.setObjectName("scrollViewport")
    area.setStyleSheet("#scrollArea, #scrollHolder, #scrollViewport { background: transparent; }")
    return area


def _error_panel(name: str, err: Exception) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(24, 24, 24, 24)
    lay.addWidget(eyebrow(f"{name} Unavailable"))
    lay.addWidget(body(str(err), dim=True))
    lay.addStretch(1)
    return w
