"""Bench bespoke visuals — the meaning-carrying, hand-painted Qt for the Bench
workspace, kept OUT of bench.py so the feature chrome can route everything else
through kit/widgets and pass the no-drift lint.

What lives here (and why it is bespoke, not a generic kit builder):
  * PinMap           — a QPainter package map (net colour fill + switch-class
                       border + breakout notch + 5 V dot + zoom + selection ring).
  * legend()         — a THREE-dimension legend (fill=net colour, border=switch
                       class, mark=dot/notch). The generic kit.legend is dots-only
                       and would collapse those three encodings into one.
  * connection_blocks() — the per-pin signal-path blocks (a build-card flow).
  * stat()           — a centred stat cell used in the authority strip.
  * pin_header() / pin_meta() — the inspector's bespoke pin header + category row.
  * profile_badge()  — the numbered profile badge in the Profiles tab.

The pure net→category helpers live here too so PinMap/connection_blocks are
self-contained; bench.py re-exports `_pin_category` for callers/tests.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
                             QFrame)

from .. import theme as T
from .. import widgets as W
from .. import icons

import stm32_authority as sauth

_CAT_FROM_NET = {"analog": "power", "power": "power", "ground": "ground",
                 "core": "core", "service": "service", "lane": "lane"}


# ── pure net → category helpers (the COLOUR dimension) ───────────────────────
def _pin_category(p: dict) -> str:
    """Net category — the COLOUR dimension only. Switch class and 5 V tolerance are
    separate, stacking layers (a border and a badge) so nothing collapses into one
    conflated colour."""
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    return _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(dest, "lane"), "lane")


def _is_5v(p: dict) -> bool:
    fv = p.get("five_v")
    if not fv:
        return False
    return bool(fv.get("tolerant") or any(fv.get("by_family", {}).values()))


def _resolved_cat(pn: dict) -> str:
    """Category colour for a resolved-part pin, from its action / destination."""
    act = pn.get("action")
    if act == "rail_conflict":
        return "must"
    if act == "service":
        return "service"
    if act == "ground":
        return "ground"
    dest = pn.get("dest", "")
    return _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(dest, "lane"), "lane")


def _net_cat(net, fallback="lane"):
    return _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(net, fallback), fallback)


def _netclass(net, cat="lane") -> str:
    """The PCB net class the delivered net belongs to (for the build map). `cat` is
    the row's real net category (the display net name is not a _NET_CATEGORY key)."""
    u = str(net).upper()
    if any(k in u for k in ("SWD", "SWCLK", "SWO", "TDI", "NTRST", "JTMS", "JTCK")):
        return "SWD"
    if any(k in u for k in ("USB", "_DP", "_DN", "DP_", "DN_")):
        return "USB"
    if cat in ("power", "analog", "ground", "core"):
        return "Power"
    if cat == "service":
        return "Signal"
    return "Default"


# ── small bespoke coloured labels ────────────────────────────────────────────
def _dot(cat: str, px: int = 6) -> QLabel:
    """A 6px category dot — the sanctioned way to carry a hue on a flow row (§4)."""
    d = QLabel(); d.setFixedSize(px, px)
    W.register_restyle(lambda: d.setStyleSheet(
        f"background:{T.category(cat)};border-radius:{px // 2}px;"), d)
    return d


def _conn_val(text, mono=True, cat=None) -> QLabel:
    """One value in a connection-block stage list: mono machine text, optionally in a
    net-category colour (for the delivered net)."""
    lab = QLabel(str(text)); lab.setWordWrap(True)
    lab.setFont(T.mono_font(11, semibold=bool(cat)) if mono else T.ui_font(10))
    W.register_restyle(lambda: lab.setStyleSheet(
        f"color:{T.category(cat) if cat else T.t('txt1')};background:transparent;"), lab)
    return lab


def connection_blocks(chain, cfg=None) -> QWidget:
    """The build map as one labelled BLOCK per physical path — read like a build card,
    not a cramped text flow. Each block walks the real chain in order: MCU Pin -> ZIF
    Socket -> the in-line component (switch cell + its Source/Drain terminal pins, or the
    33 Ω series R, or a direct route) -> Destination contact -> the delivered net and the
    PCB net class it lands on. Stage LABELS carry the meaning, so values stay plain and
    human (Pin 12, not a raw KiCad refdes soup)."""
    wrap = QWidget(); col = QVBoxLayout(wrap); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(10)
    pos = chain["pos"]; name = chain.get("name") or ""
    rows = chain.get("rows", [])
    if not rows:
        col.addWidget(W.empty_state(
            "No Connection Path for This Pin", glyph=icons.GLYPHS["routing"])); return wrap
    for r in rows:
        net = r.get("drain_net", ""); dcat = _CAT_FROM_NET.get(r.get("drain_cat", "lane"), "lane")
        if r["kind"] == "switch":
            caption = f"Switched Role · Channel {r.get('channel', '')}".rstrip(" ·")
        elif r["kind"] == "lane":
            caption = "Default IO Lane"
        else:
            caption = "Direct Route"

        # ordered stages — labels say what each thing IS
        stages = [("MCU Pin", _conn_val(f"Pin {pos}" + (f" · {name}" if name else ""))),
                  ("ZIF Socket", _conn_val(f"Pin {pos}"))]
        if r["kind"] == "switch":
            stages.append(("Switch Cell", _conn_val(f"{r.get('cell', '')} · Channel {r.get('channel', '')}")))
            if r.get("s_term") and r.get("d_term"):
                stages.append(("Switch Pins", _conn_val(f"{r['s_term']} → {r['d_term']}")))
        elif r["kind"] == "lane" and r.get("series"):
            stages.append(("Series Resistor", _conn_val(r["series"])))
        else:
            stages.append(("Route", _conn_val("Direct")))
        if r.get("drain_via"):
            stages.append(("Destination", _conn_val(r["drain_via"])))
        stages.append(("Delivered Net", _conn_val(net, cat=dcat)))
        stages.append(("Net Class", W.body(f"{_netclass(net, dcat)} Net Class", dim=True)))

        # One container per branch, de-carded (§4): a single light `raised` step with
        # the container radius — not a stack of heavy nested `inset` boxes. The caption
        # row (dot + role) plus the stage list read as a signal path, separated from the
        # next branch by space, not another filled box.
        block = QFrame(); block.setObjectName("connblock")
        W.register_restyle(lambda bk=block: bk.setStyleSheet(
            f"QFrame#connblock{{background:{T.t('raised')};border:none;"
            f"border-radius:{T.RADIUS_CONTAINER}px;}}"), block)
        bl = QVBoxLayout(block); bl.setContentsMargins(16, 12, 16, 14); bl.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(_dot(dcat), 0, Qt.AlignVCenter)
        caplab = QLabel(caption); caplab.setFont(T.ui_font(10, semibold=True))
        W.register_restyle(lambda l=caplab: l.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), caplab)
        head.addWidget(caplab); head.addStretch(1)
        bl.addLayout(head)
        # a hairline divider under the caption separates the header from the stage list
        rule = QFrame(); rule.setFixedHeight(1); rule.setFrameShape(QFrame.NoFrame)
        W.register_restyle(lambda rl=rule: rl.setStyleSheet(
            f"background:{T.t('hairline')};border:none;"), rule)
        bl.addWidget(rule)
        bl.addWidget(W.dl(stages, key_width=124))
        col.addWidget(block)
    if chain.get("one_hot"):
        col.addWidget(W.body("One-hot: exactly one switched path closes per socketed part.", dim=True))
    return wrap


# ── the painted pin map: net colour (fill) + switch class (border) + marks + zoom ─
class PinMap(QWidget):
    def __init__(self, on_select, base=380, parent=None):
        super().__init__(parent)
        self._on_select = on_select
        self._base = base
        self._zoom = 1.0
        self._size = base
        self._positions = []
        self._geo = {"body": (0, 0, 0, 0), "pins": []}
        self._attr = {}
        self._selected = None
        self.setFixedSize(base, base)
        self.setCursor(Qt.PointingHandCursor)

    def _relayout(self):
        import stm32_pins_tab as pins
        self._size = max(120, int(self._base * self._zoom))
        self.setFixedSize(self._size, self._size)
        self._geo = pins.pin_map_geometry(self._positions, self._size, self._size)
        self.update()

    def set_positions(self, geo_positions, attrof):
        self._positions = list(geo_positions)
        self._attr = dict(attrof)
        if self._selected not in self._attr:
            self._selected = None
        self._relayout()

    def set_authority(self, authority):
        positions = authority["positions"] if authority else []
        self.set_positions(positions, {p["position"]: {"cat": _pin_category(p), "fivev": _is_5v(p)}
                                       for p in positions})

    def set_zoom(self, z):
        self._zoom = max(0.7, min(2.6, z)); self._relayout()

    def zoom_by(self, f):
        self.set_zoom(self._zoom * f)

    def wheelEvent(self, e):
        # plain wheel zooms the map (the pane is fixed-height, so there is nothing to
        # scroll here anyway); accepting stops it bubbling to the page scroll area
        self.zoom_by(1.12 if e.angleDelta().y() > 0 else 1 / 1.12); e.accept()

    def select(self, pos):
        self._selected = pos; self.update()

    def side_of(self, pos):
        """The side (L/R/T/B) of a pin, read from the already-built geometry — no
        need to recompute the whole package layout per inspector click."""
        for p in self._geo["pins"]:
            if p["pos"] == pos:
                return p["side"]
        return ""

    def paintEvent(self, _e):
        qp = QPainter(self); qp.setRenderHint(QPainter.Antialiasing, True)
        bx, by, bw, bh = self._geo["body"]
        pen = QPen(T.qcolor("txt3")); pen.setWidthF(1.3)
        qp.setPen(pen); qp.setBrush(Qt.NoBrush)
        qp.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)
        qp.setBrush(T.qcolor("txt3")); qp.setPen(Qt.NoPen)
        qp.drawEllipse(QRectF(bx + 12, by + 12, 7, 7))
        for pin in self._geo["pins"]:
            x, y, w, h = pin["rect"]; rect = QRectF(x, y, w, h)
            a = self._attr.get(pin["pos"], {})
            # 1) fill = net category
            qp.setBrush(T.qcolor(T.category(a.get("cat", "lane")))); qp.setPen(Qt.NoPen)
            qp.drawRoundedRect(rect, 2, 2)
            # 2) border = switch class (solid coral = must-switch, dashed orange = oscillator)
            sw = pin.get("sw")
            if sw in ("must_switch", "osc_optional"):
                bp = QPen(T.qcolor(T.category("must" if sw == "must_switch" else "osc"))); bp.setWidthF(2.0)
                if sw == "osc_optional":
                    bp.setStyle(Qt.DashLine)
                qp.setPen(bp); qp.setBrush(Qt.NoBrush); qp.drawRoundedRect(rect, 2, 2)
            # 3) breakout mark = a thin inner notch
            if pin.get("breakout"):
                op = QPen(T.qcolor("txt1")); op.setWidthF(1.0)
                qp.setPen(op); qp.setBrush(Qt.NoBrush)
                qp.drawRoundedRect(QRectF(x + 1.5, y + 1.5, w - 3, h - 3), 1, 1)
            # 4) 5 V-tolerant badge = a small dot at the pad's outer end
            if a.get("fivev"):
                d = 4.0
                cx, cy = {"L": (x + 2.5, y + h / 2), "R": (x + w - 2.5, y + h / 2),
                          "T": (x + w / 2, y + 2.5)}.get(pin["side"], (x + w / 2, y + h - 2.5))
                qp.setBrush(T.qcolor(T.category("service"))); qp.setPen(Qt.NoPen)
                qp.drawEllipse(QRectF(cx - d / 2, cy - d / 2, d, d))
            # selection ring (outermost) — a crisp cosmetic pen so the ring stays
            # 1-device-pixel sharp at any zoom/DPI instead of blurring like a scaled
            # 2.4px stroke did; accent-coloured so the selected pad is unmistakable.
            if pin["pos"] == self._selected:
                sp = QPen(T.qcolor("accent")); sp.setWidthF(2.0); sp.setCosmetic(True)
                qp.setPen(sp); qp.setBrush(Qt.NoBrush)
                qp.drawRoundedRect(QRectF(x - 2.5, y - 2.5, w + 5, h + 5), 3, 3)
            # Pin number, in the margin OUTSIDE the pad. The font is sized to the pad
            # PITCH (the pad's cross-axis extent), the axis along which neighbours
            # crowd, so adjacent numbers never stack. L/R pads are stacked vertically
            # and read the number horizontally into the side margin; T/B pads are
            # stacked horizontally, where a horizontal 2-3 digit number cannot fit the
            # narrow pad pitch (BENCH-04: they overlapped into an unreadable smear), so
            # T/B numbers are ROTATED 90° to read vertically OUT through the top/bottom
            # margin — the same generous outward room the side pins already use. Zoom
            # (scroll wheel) enlarges everything to read tiny multi-digit numbers.
            pitch = h if pin["side"] in ("L", "R") else w
            qp.setFont(T.mono_font(max(6.0, min(11.0, pitch * 0.72))))
            qp.setPen(T.qcolor("txt1" if pin["pos"] == self._selected else "txt3"))
            num = str(pin["pos"])
            if pin["side"] == "L":
                qp.drawText(QRectF(x - 37, y, 33, h), Qt.AlignRight | Qt.AlignVCenter, num)
            elif pin["side"] == "R":
                qp.drawText(QRectF(x + w + 4, y, 33, h), Qt.AlignLeft | Qt.AlignVCenter, num)
            elif pin["side"] == "T":
                # rotate CCW: local +x -> screen up, +y -> screen right; a 34-long,
                # pitch-thick slot centered on the pad, reading up into the top margin.
                qp.save(); qp.translate(x + w / 2, y - 3); qp.rotate(-90)
                qp.drawText(QRectF(0, -w / 2, 34, w), Qt.AlignLeft | Qt.AlignVCenter, num)
                qp.restore()
            else:  # B — same rotation, slot extends DOWN (local -x) into the bottom margin
                qp.save(); qp.translate(x + w / 2, y + h + 3); qp.rotate(-90)
                qp.drawText(QRectF(-34, -w / 2, 34, w), Qt.AlignRight | Qt.AlignVCenter, num)
                qp.restore()
        qp.end()

    def mousePressEvent(self, e):
        pt = e.pos()
        for pin in self._geo["pins"]:
            x, y, w, h = pin["rect"]
            if (x - 4) <= pt.x() <= (x + w + 4) and (y - 4) <= pt.y() <= (y + h + 4):
                self.select(pin["pos"])
                if self._on_select:
                    self._on_select(pin["pos"])
                return


# ── the three-dimension legend (fill / border / mark) ────────────────────────
def _swatch(cat, kind="fill") -> QLabel:
    """A legend swatch: fill (net colour), outline/dashed (switch border), dot or
    notch (marks) — one per encoding dimension so they read as separate layers."""
    lab = QLabel(); lab.setFixedSize(9, 9)

    def style():
        if kind == "fill":
            lab.setStyleSheet(f"background:{T.category(cat)};border-radius:2px;")
        elif kind == "dot":
            lab.setStyleSheet(W.dot_css(T.category(cat), 9))
        elif kind == "notch":
            lab.setStyleSheet(f"background:transparent;border:1px solid {T.t('txt1')};border-radius:2px;")
        else:
            st = "dashed" if kind == "dashed" else "solid"
            lab.setStyleSheet(f"background:transparent;border:2px {st} {T.category(cat)};border-radius:2px;")
    W.register_restyle(style, lab)
    return lab


def _leg_item(row, sw, label, gap=8):
    # BENCH-01/02: compact, and a quiet sentence-case body label instead of the
    # ALL-CAPS eyebrow (which was over-used and shouted). Eyebrow stays for the
    # section headers only, so it keeps its structural meaning.
    # Contrast (owner report, v2.11 "legend hard to see"): the label reads at txt2,
    # NOT txt3. txt3 (W.body dim) composites to ~3.5:1 (dark) / ~4.0:1 (light) on the
    # card — below WCAG AA for this 9px text; txt2 clears AA (~6.8 / ~5.9:1) while
    # staying quieter than the txt1 swatch legend headers.
    row.addSpacing(gap); row.addWidget(sw); row.addSpacing(4)
    lab = QLabel(label); lab.setFont(T.ui_font(9))
    W.register_restyle(lambda l=lab: l.setStyleSheet(
        f"color:{T.t('txt2')};background:transparent;"), lab)
    row.addWidget(lab)


def legend() -> QWidget:
    """The three-encoding legend: fill = net colour, border = switch class
    (solid/dashed), mark = dot/notch. Bespoke because the generic dots-only
    kit.legend would lose the border and mark dimensions."""
    w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(7)
    r1 = QHBoxLayout(); r1.setSpacing(0); r1.addStretch(1)
    r1.addWidget(W.eyebrow("Net Colour"))
    for cat, label in (("power", "Power"), ("ground", "Ground"), ("core", "Core"),
                       ("service", "Service"), ("lane", "IO Lane")):
        _leg_item(r1, _swatch(cat, "fill"), label)
    r1.addStretch(1); lay.addLayout(r1)
    r2 = QHBoxLayout(); r2.setSpacing(0); r2.addStretch(1)
    r2.addWidget(W.eyebrow("Border"))
    _leg_item(r2, _swatch("must", "outline"), "Must-Switch")
    _leg_item(r2, _swatch("osc", "dashed"), "Oscillator")
    r2.addSpacing(22); r2.addWidget(W.eyebrow("Mark"))
    _leg_item(r2, _swatch("service", "dot"), "5 V-Tolerant")
    _leg_item(r2, _swatch("fixed", "notch"), "Breakout")
    r2.addStretch(1); lay.addLayout(r2)
    return w


def stat(value: str, label: str) -> QWidget:
    """A centred stat cell: big tabular number over a quiet eyebrow label. Bespoke
    (centre-aligned) vs kit.stat_strip's left-aligned strip."""
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
    v.setAlignment(Qt.AlignHCenter)
    num = QLabel(value); num.setFont(T.scale_font("stat")); num.setAlignment(Qt.AlignHCenter)
    W.register_restyle(lambda: num.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), num)
    lab = W.eyebrow(label); lab.setAlignment(Qt.AlignHCenter)
    v.addWidget(num); v.addWidget(lab)
    return w


# ── inspector bespoke sub-builders (pin header + category/side meta row) ──────
def pin_header(name: str, pos, must_switch: bool) -> QWidget:
    """The inspector header: focal pin name (hero) + dim pin number, and an optional
    Must-Switch tag. Bespoke because the hero name + baseline-aligned pin number is a
    one-off focal composition, not a definition list."""
    w = QWidget()
    header = QHBoxLayout(w); header.setContentsMargins(0, 0, 0, 0); header.setSpacing(12)
    pn = QLabel(name); pn.setFont(T.scale_font("hero"))
    W.register_restyle(lambda: pn.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), pn)
    pp = QLabel(f"Pin {pos}"); pp.setFont(T.scale_font("value"))
    W.register_restyle(lambda: pp.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"), pp)
    header.addWidget(pn); header.addWidget(pp, 0, Qt.AlignBottom); header.addStretch(1)
    if must_switch:
        header.addWidget(W.tag("Must-Switch", "err"))
    return w


def pin_meta(cat: str, side: str) -> QWidget:
    """The inspector's category + side row: a category dot + capitalised category
    name, then the pad side. Bespoke coloured composition."""
    w = QWidget()
    meta = QHBoxLayout(w); meta.setContentsMargins(0, 0, 0, 0); meta.setSpacing(16)
    lead = QWidget(); lh = QHBoxLayout(lead); lh.setContentsMargins(0, 0, 0, 0); lh.setSpacing(8)
    dot = QLabel(); dot.setFixedSize(9, 9)
    W.register_restyle(lambda: dot.setStyleSheet(W.dot_css(T.category(cat), 9)), dot)
    catlab = QLabel(cat.title()); catlab.setFont(T.ui_font(10, semibold=True))
    W.register_restyle(lambda: catlab.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), catlab)
    lh.addWidget(dot); lh.addWidget(catlab)
    meta.addWidget(lead)
    if side:
        meta.addWidget(W.body(f"{side} Side", dim=False))
    meta.addStretch(1)
    return w


def profile_badge(n: int) -> QLabel:
    """The numbered rank badge in the Profiles 'Chips by Profile' list — a small
    filled token square with the ordinal. Bespoke filled swatch."""
    badge = QLabel(str(n)); badge.setFont(T.scale_font("group_subhead"))
    badge.setFixedSize(28, 28); badge.setAlignment(Qt.AlignCenter)
    W.register_restyle(lambda b=badge: b.setStyleSheet(
        f"background:{T.t('tok')};color:{T.t('txt1')};border-radius:6px;"), badge)
    return badge
