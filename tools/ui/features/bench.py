"""Bench — the STM32 package-authority workspace.

Panels (all list-driven, added to the Workspace):
  Authority     real pin map (painted from pin_map_geometry) + buildability
                verdict (fabric_drc + current_budget) + rollup + pin inspector
  Profiles      the per-package profile ladder (baseline -> least supported)
  Part Resolver resolve_part(mpn) -> per-chip pin map
  Card Outputs  card_bom + current_budget rails + the vault export list

Everything reads the hardened logic layer directly; nothing is hard-coded.
"""
from __future__ import annotations

from typing import List, Optional

from pathlib import Path

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QComboBox, QGridLayout, QPushButton, QFrame, QScrollArea,
                             QFileDialog)

from .. import theme as T
from .. import widgets as W
from .. import feature as F
from ..util import clear_layout, run_populate

import stm32_db as db
import stm32_authority as sauth
import stm32_pins_tab as pins   # pure helpers: pin_map_geometry, _pin_detail_rows

_CAT_FROM_NET = {"analog": "power", "power": "power", "ground": "ground",
                 "core": "core", "service": "service", "lane": "lane"}


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


# ── connection diagram (per-pin flow: socket -> switch/series -> connector -> net) ─




def _node(kind, headline, sub=None, extra=None, headline_color=None) -> QFrame:
    """A uniform box in the build map. The pin / terminal / contact is the headline
    (prioritised); the component refdes/part sits under it, with any extra below.
    Every node is the same size so the whole map stacks evenly."""
    card = QFrame(); card.setObjectName("connode"); card.setFixedSize(172, 122)
    v = QVBoxLayout(card); v.setContentsMargins(10, 8, 10, 8); v.setSpacing(3)
    v.addStretch(1)
    kl = W.eyebrow(kind); kl.setAlignment(Qt.AlignHCenter); v.addWidget(kl)
    hl = QLabel(str(headline)); hl.setFont(T.mono_font(13, semibold=True))
    hl.setAlignment(Qt.AlignHCenter); hl.setWordWrap(True); v.addWidget(hl)
    subl = exl = None
    if sub:
        subl = QLabel(str(sub)); subl.setFont(T.mono_font(9)); subl.setAlignment(Qt.AlignHCenter); subl.setWordWrap(True)
        v.addWidget(subl)
    if extra:
        exl = QLabel(str(extra)); exl.setFont(T.ui_font(8.5)); exl.setAlignment(Qt.AlignHCenter); exl.setWordWrap(True)
        v.addWidget(exl)
    v.addStretch(1)

    def style():
        hl.setStyleSheet(f"color:{headline_color or T.t('txt1')};background:transparent;")
        if subl is not None:
            subl.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
        if exl is not None:
            exl.setStyleSheet(f"color:{T.t('txt3')};background:transparent;")
        card.setStyleSheet(f"QFrame#connode{{background:{T.t('inset')};border:1px solid {T.t('stroke')};border-radius:8px;}}")
    W.register_restyle(style)
    return card


def _arrow() -> QLabel:
    a = QLabel("→"); a.setFont(T.ui_font(15)); a.setAlignment(Qt.AlignCenter); a.setFixedWidth(18)
    W.register_restyle(lambda: a.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"))
    return a


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


def _term_short(t):
    parts = str(t).split("·")
    name = parts[0].strip()
    num = parts[1].replace("Pin", "").strip() if len(parts) > 1 else ""
    return name, num


def _connection_flow(chain, cfg=None) -> QWidget:
    box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(12)
    pos = chain["pos"]; pin_name = chain["name"] or f"Pin {pos}"
    for r in chain.get("rows", []):
        line = QHBoxLayout(); line.setSpacing(6); line.setAlignment(Qt.AlignLeft)
        line.addWidget(_node("MCU Pin", f"Pin {pos}", pin_name)); line.addWidget(_arrow())
        line.addWidget(_node("ZIF Socket", f"Pin {pos}", chain.get("socket", "Socket"))); line.addWidget(_arrow())
        if r["kind"] == "switch":
            sn, snum = _term_short(r["s_term"]); dn, dnum = _term_short(r["d_term"])
            line.addWidget(_node("Switch Cell", f"{sn} → {dn}", r["cell"],
                                 f"Channel {r['channel']} Pins {snum}, {dnum}"))
            line.addWidget(_arrow())
        elif r.get("series"):
            line.addWidget(_node("Series R", "33 Ω", str(r["series"]).split(" ")[0])); line.addWidget(_arrow())
        via = r.get("drain_via", "")
        if "Ground Plane" in via:
            line.addWidget(_node("Ground", "Plane", "Stitching vias"))
        else:
            contact = via.split("·")[-1].strip() if "·" in via else via
            line.addWidget(_node("Connector", contact, chain.get("connector", "Connector")))
        line.addWidget(_arrow())
        net = r.get("drain_net", ""); dcat = r.get("drain_cat", "lane")
        line.addWidget(_node("Delivers", net, f"{_netclass(net, dcat)} Net Class",
                             headline_color=T.category(_CAT_FROM_NET.get(dcat, "lane"))))
        line.addStretch(1)
        wrap = QWidget(); wrap.setLayout(line); col.addWidget(wrap)
    if chain.get("one_hot"):
        col.addWidget(W.eyebrow("One-Hot: exactly one switched path closes per socketed part"))
    return box


# ── shared package state ─────────────────────────────────────────────────────
class BenchState:
    def __init__(self):
        self.conn = None
        self.packages: List[str] = []
        self.package: Optional[str] = None
        self.error: Optional[str] = None
        self._cache = {}
        try:
            self.conn = db.connect(db.default_db_path())
            self.packages = db.list_packages(self.conn)
            self.package = "LQFP64" if "LQFP64" in self.packages else (
                self.packages[0] if self.packages else None)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        self._refreshers = []
        self.goto_resolver = None       # set by BenchFeature: navigate to a resolved chip
        self.goto_authority_pin = None  # set by BenchFeature: jump to a pin on the map

    def authority(self):
        if self.package is None:
            return None
        if self.package not in self._cache:
            self._cache[self.package] = sauth.build(self.conn, self.package)
        return self._cache[self.package]

    def set_package(self, pkg: str):
        self.package = pkg
        for fn in list(self._refreshers):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def on_change(self, fn):
        self._refreshers.append(fn)


# ── the painted pin map: net colour (fill) + switch class (border) + marks + zoom ─
class PinMap(QWidget):
    SIZE = 460  # kept for _side_of's independent geometry

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
        if e.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.12 if e.angleDelta().y() > 0 else 1 / 1.12); e.accept()
        else:
            e.ignore()

    def select(self, pos):
        self._selected = pos; self.update()

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
            # selection ring (outermost)
            if pin["pos"] == self._selected:
                sp = QPen(T.qcolor("accent")); sp.setWidthF(2.4)
                qp.setPen(sp); qp.setBrush(Qt.NoBrush)
                qp.drawRoundedRect(QRectF(x - 2.5, y - 2.5, w + 5, h + 5), 3, 3)
            # number, outside the pad, font scaled with zoom
            qp.setFont(T.mono_font(max(6.5, min(11.0, 7.0 * self._zoom))))
            qp.setPen(T.qcolor("txt1" if pin["pos"] == self._selected else "txt3"))
            num = str(pin["pos"])
            if pin["side"] == "L":
                qp.drawText(QRectF(x - 38, y - 2, 32, h + 4), Qt.AlignRight | Qt.AlignVCenter, num)
            elif pin["side"] == "R":
                qp.drawText(QRectF(x + w + 6, y - 2, 32, h + 4), Qt.AlignLeft | Qt.AlignVCenter, num)
            elif pin["side"] == "T":
                qp.drawText(QRectF(x - 6, y - 20, w + 12, 16), Qt.AlignHCenter | Qt.AlignBottom, num)
            else:
                qp.drawText(QRectF(x - 6, y + h + 4, w + 12, 16), Qt.AlignHCenter | Qt.AlignTop, num)
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


def _swatch(cat, kind="fill") -> QLabel:
    """A legend swatch: fill (net colour), outline/dashed (switch border), dot or
    notch (marks) — one per encoding dimension so they read as separate layers."""
    lab = QLabel(); lab.setFixedSize(11, 11)

    def style():
        if kind == "fill":
            lab.setStyleSheet(f"background:{T.category(cat)};border-radius:2px;")
        elif kind == "dot":
            lab.setStyleSheet(f"background:{T.category(cat)};border-radius:5px;")
        elif kind == "notch":
            lab.setStyleSheet(f"background:transparent;border:1px solid {T.t('txt1')};border-radius:2px;")
        else:
            st = "dashed" if kind == "dashed" else "solid"
            lab.setStyleSheet(f"background:transparent;border:2px {st} {T.category(cat)};border-radius:2px;")
    W.register_restyle(style)
    return lab


def _leg_item(row, sw, label, gap=14):
    row.addSpacing(gap); row.addWidget(sw); row.addSpacing(6); row.addWidget(W.eyebrow(label))


def _legend() -> QWidget:
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


def _stat(value: str, label: str) -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
    v.setAlignment(Qt.AlignHCenter)
    num = QLabel(value); num.setFont(T.mono_font(15, semibold=True)); num.setAlignment(Qt.AlignHCenter)
    W.register_restyle(lambda: num.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    lab = W.eyebrow(label); lab.setAlignment(Qt.AlignHCenter)
    v.addWidget(num); v.addWidget(lab)
    return w


# ── Authority panel ──────────────────────────────────────────────────────────
def _authority_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(24, 16, 24, 24)
    outer.setSpacing(14)

    if state.error or state.package is None:
        outer.addWidget(W.eyebrow("Database Unavailable"))
        outer.addWidget(W.body(state.error or "No packages found in the STM32 database.", dim=True))
        outer.addStretch(1)
        return root

    verdict_holder = QVBoxLayout(); verdict_holder.setContentsMargins(0, 0, 0, 0)
    outer.addLayout(verdict_holder)

    grid = QHBoxLayout(); grid.setSpacing(16)
    # left: map card
    map_card = W.Card(pad=20)
    pin_map = PinMap(on_select=lambda pos: _show_pin(pos), base=460)
    zrow = QHBoxLayout(); zrow.setSpacing(6); zrow.addStretch(1)
    zrow.addWidget(W.eyebrow("Zoom"))
    b_zo = W.btn("−", "ghost", "Zoom out", lambda: pin_map.zoom_by(1 / 1.15)); b_zo.setFixedWidth(32)
    b_zi = W.btn("+", "ghost", "Zoom in", lambda: pin_map.zoom_by(1.15)); b_zi.setFixedWidth(32)
    zrow.addWidget(b_zo); zrow.addWidget(b_zi)
    zrow.addWidget(W.btn("Reset", "ghost", "Reset the zoom", lambda: pin_map.set_zoom(1.0)))
    map_card.body.addLayout(zrow)
    _area = QScrollArea(); _area.setWidgetResizable(False); _area.setFrameShape(QFrame.NoFrame)
    _area.setFixedHeight(470); _area.setAlignment(Qt.AlignCenter); _area.setWidget(pin_map)
    map_card.body.addWidget(_area)
    map_card.body.addWidget(_legend())
    strip = QHBoxLayout(); strip.setSpacing(24); strip.setContentsMargins(0, 12, 0, 0)
    strip.addStretch(1)
    stat_cells = {}
    for key, label in (("positions_total", "Positions"), ("must_switch_count", "Must-Switch"),
                       ("cells_as_built", "Switch Cells"), ("channel_count", "Channels"),
                       ("osc_optional_count", "Oscillator Optional")):
        c = _stat("0", label); strip.addWidget(c); stat_cells[key] = c
    strip.addStretch(1)
    map_card.body.addLayout(strip)
    map_card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    grid.addWidget(map_card, 0)

    # right: inspector
    insp_card = W.Card(pad=16)
    insp_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    grid.addWidget(insp_card, 1)
    outer.addLayout(grid)

    outer.addWidget(W.eyebrow("Connection Diagram"))
    conn_holder = QVBoxLayout(); conn_holder.setSpacing(10)
    outer.addLayout(conn_holder)
    outer.addStretch(1)
    cw_holder = {}

    def _clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                _clear_layout(it.layout())

    def _show_pin(pos):
        _clear_layout(insp_card.body)
        authority = state.authority()
        p = next((x for x in authority["positions"] if x["position"] == pos), None)
        if p is None:
            insp_card.body.addWidget(W.body("Select a pin on the map.", dim=True))
            insp_card.body.addStretch(1)
            return
        name = next(iter(p["pin_names"]), f"Pin {pos}")
        header = QHBoxLayout(); header.setSpacing(12)
        pn = QLabel(name); pn.setFont(T.mono_font(22, semibold=True))
        W.register_restyle(lambda: pn.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
        pp = QLabel(f"Pin {pos}"); pp.setFont(T.mono_font(10))
        W.register_restyle(lambda: pp.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"))
        header.addWidget(pn); header.addWidget(pp, 0, Qt.AlignBottom); header.addStretch(1)
        if p.get("switch_class") == "must_switch":
            header.addWidget(W.tag("Must-Switch", "err"))
        insp_card.body.addLayout(header)
        # category + side
        cat = _pin_category(p)
        meta = QHBoxLayout(); meta.setSpacing(16)
        lead = QWidget(); lh = QHBoxLayout(lead); lh.setContentsMargins(0, 0, 0, 0); lh.setSpacing(8)
        dot = QLabel(); dot.setFixedSize(9, 9)
        W.register_restyle(lambda: dot.setStyleSheet(f"background:{T.category(cat)};border-radius:4px;"))
        catlab = QLabel(cat.title()); catlab.setFont(T.ui_font(10, semibold=True))
        W.register_restyle(lambda: catlab.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
        lh.addWidget(dot); lh.addWidget(catlab)
        side = {"L": "Left", "R": "Right", "T": "Top", "B": "Bottom"}.get(_side_of(state, pos), "")
        meta.addWidget(lead)
        if side:
            meta.addWidget(W.body(f"{side} Side", dim=False))
        meta.addStretch(1)
        insp_card.body.addLayout(meta)
        # detail rows (real), middots sanitised out
        insp_card.body.addWidget(W.eyebrow("Detail"))
        rows = []
        for label, value in pins._pin_detail_rows(p):
            rows.append((label, W.body(str(value).replace(" · ", "   "), wrap=True)))
        insp_card.body.addWidget(W.dl(rows, key_width=150))
        insp_card.body.addStretch(1)

        # connection diagram: the per-pin flow from the socket to the delivered net
        _clear_layout(conn_holder)
        try:
            if not cw_holder.get("cw"):
                cw_holder["cw"] = sauth.card_wiring(authority)
            chain = pins._pin_chain(authority, pos, cw_holder["cw"])
            conn_holder.addWidget(_connection_flow(chain, ctx.cfg))
        except Exception as e:  # noqa: BLE001
            conn_holder.addWidget(W.body(f"Connection diagram unavailable: {e}", dim=True))

    def refresh():
        _clear_layout(verdict_holder)
        cw_holder.clear()
        try:
            authority = state.authority()
        except Exception as e:  # noqa: BLE001 - unsupported package build (e.g. BGA)
            pin_map.set_authority(None)
            verdict_holder.addWidget(W.Verdict(
                f"{state.package} Not Buildable", str(e), "warn", plain=False))
            return
        pin_map.set_authority(authority)
        r = authority["rollup"]
        for key, cell in stat_cells.items():
            lbl = cell.findChild(QLabel)
            if lbl:
                lbl.setText(str(r.get(key, 0)))
        # verdict
        _clear_layout(verdict_holder)
        findings = sauth.fabric_drc(authority)
        ok = sauth.fabric_drc_ok(findings)
        passed = sum(1 for f in findings if f.get("ok"))
        budget = sauth.current_budget(authority)
        budget_ok = not budget.get("findings")
        chips = [("Fabric DRC", f"{passed} / {len(findings)}", "ok" if ok else "err"),
                 ("Current Budget", "OK" if budget_ok else "Check", "ok" if budget_ok else "warn")]
        title = "Buildable" if ok else "Not Buildable"
        sub = "All structural rules pass" if ok else "Structural rules need attention"
        verdict_holder.addWidget(W.Verdict(title, sub, "ok" if ok else "warn", chips))
        # default selection
        first = authority["positions"][0]["position"] if authority["positions"] else None
        if first is not None:
            pin_map.select(first); _show_pin(first)

    state.on_change(refresh)
    refresh()
    ctx.bus.on("bench.jump_pin", lambda pos: (pin_map.select(pos), _show_pin(pos)))
    return root


def _side_of(state, pos):
    geo = pins.pin_map_geometry(state.authority()["positions"], PinMap.SIZE, PinMap.SIZE)
    for p in geo["pins"]:
        if p["pos"] == pos:
            return p["side"]
    return ""


# ── Part Resolver panel (real resolve_part) ──────────────────────────────────
def _resolver_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(10)
    edit = QLineEdit(); edit.setPlaceholderText("STM32F407VGT6")
    edit.setFixedWidth(320); edit.setMinimumHeight(34)
    edit.setToolTip("Enter a full ordering part number to resolve its exact silicon pin map")
    bar.addWidget(edit)
    result_holder = QVBoxLayout()

    def resolve():
        clear_layout(result_holder)
        mpn = edit.text().strip()
        if not mpn:
            return
        try:
            res = sauth.resolve_part(state.conn, mpn)
        except Exception as e:  # noqa: BLE001
            result_holder.addWidget(W.body(f"Could not resolve {mpn}: {e}", dim=True))
            return
        if not res:
            result_holder.addWidget(W.body(f"No match for {mpn}.", dim=True))
            return
        result_holder.addWidget(W.eyebrow(f"{res['part']}   {res['package']}   {len(res['pins'])} Pins"))
        split = QHBoxLayout(); split.setSpacing(16)
        # left: the resolved pinout, painted from the datasheet-derived pins
        map_card = W.Card(pad=16)
        pm = PinMap(on_select=None, base=340)
        geo = [{"position": p["pin"], "switch_class": "fixed",
                "pin_names": {p.get("name", ""): 1}, "breakout": {}} for p in res["pins"]]
        pm.set_positions(geo, {p["pin"]: {"cat": _resolved_cat(p), "fivev": bool(p.get("five_v_tolerant"))}
                               for p in res["pins"]})
        map_card.body.addWidget(pm, 0, Qt.AlignHCenter)
        map_card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        split.addWidget(map_card, 0, Qt.AlignTop)
        # right: the clean per-pin table (the datasheet pinout, remade)
        rows = []
        for pn in res["pins"][:400]:
            action = pn.get("action", "").replace("_", " ").title()
            act_w = W.tag("Rail Conflict", "err") if pn.get("action") == "rail_conflict" else W.body(action)
            ch = ", ".join(f"Cell {s['cell']} Channel {s['channel']}" for s in pn.get("close_switches", []))
            fv = pn.get("five_v_tolerant")
            fv_w = W.tag("5 V", "ok") if fv else W.body("No" if fv is False else "None", dim=True)
            rows.append([str(pn["pin"]), W.body(str(pn.get("name", "")), mono=True), act_w,
                         W.body(str(pn.get("dest", "")), mono=True),
                         W.body(ch or "None", dim=not ch, mono=bool(ch)), fv_w])
        tbl = W.data_table(["Pin", "Name", "Action", "Destination", "Close Switches", "5 V"], rows, stretch_col=3)
        split.addWidget(tbl, 1)
        result_holder.addLayout(split, 1)

    bar.addWidget(W.btn("Resolve", "primary", "Resolve the part number", resolve))
    bar.addWidget(W.body("Exact silicon, not the package union.", dim=True))
    bar.addStretch(1)
    edit.returnPressed.connect(resolve)
    ctx.bus.on("bench.resolve", lambda m: (edit.setText(str(m)), resolve()))
    lay.addLayout(bar)
    lay.addLayout(result_holder, 1)
    return root


# ── Card Outputs panel (real card_bom + current_budget) ──────────────────────
def _outputs_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    if state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); return root
    pkg = state.package
    authority = state.authority()

    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow(f"Exports   {pkg}")); bar.addStretch(1)
    b_bom = W.btn("Save Card Bill Of Materials...", "ghost", "Write the card bill of materials as a CSV file")
    b_net = W.btn("Save KiCad Netlist...", "ghost", "Write the socket netlist in KiCad format")
    b_bundle = W.btn("Write Authority Bundle...", "primary",
                     "Write the full bundle (YAML, JSON, TSV, CSV, socket symbol) to a folder")
    bar.addWidget(b_bom); bar.addWidget(b_net); bar.addWidget(b_bundle)
    lay.addLayout(bar)

    findings = sauth.validate_socket_symbol(authority)
    passed = sauth.validate_socket_symbol_ok(findings)
    chips = [(f["rule"].replace("_", " ").title(), "Pass" if f["ok"] else "Fail",
              "ok" if f["ok"] else "err") for f in findings]
    lay.addWidget(W.Verdict(
        "Pre-Write Checks",
        "The emitted socket symbol is placeable and routable in KiCad."
        if passed else "Resolve the failing checks before you write the bundle.",
        "ok" if passed else "err", chips))

    lay.addWidget(W.eyebrow(f"Card Bill Of Materials   {pkg}"))
    bom = sauth.card_bom(authority)
    rows = []
    for r in bom.get("rows", []):
        rows.append([W.body(str(r.get("refdes", "")), mono=True), W.body(str(r.get("mpn", "")), mono=True),
                     W.body(str(r.get("role", ""))), str(r.get("qty", ""))])
    lay.addWidget(W.data_table(["Reference", "Part Number", "Role", "Quantity"], rows, stretch_col=2))

    lay.addWidget(W.eyebrow("Current Budget   Rails"))
    budget = sauth.current_budget(authority)
    brows = []
    for rail, info in sorted(budget.get("rails", {}).items()):
        cap = info.get("input_capacity_ma") or 0
        cap_txt = f"{cap/1000:.2f} A" if cap >= 1000 else f"{cap} mA"
        state_w = W.tag("OK", "ok")
        brows.append([W.net_token(rail, _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(rail, "lane"), "lane")),
                      f"{info.get('direct_pins', 0)}", f"{info.get('switch_channels', 0)}",
                      cap_txt, state_w])
    lay.addWidget(W.data_table(["Rail", "Direct", "Switched", "Capacity", "State"], brows, stretch_col=0))
    lay.addStretch(1)

    base = str(Path(ctx.cfg.get("RepoRoot") or "."))

    def save_text(title, default_name, filt, builder):
        fn, _ = QFileDialog.getSaveFileName(root, title, str(Path(base) / default_name), filt)
        if not fn:
            return
        try:
            Path(fn).write_text(builder(authority), encoding="utf-8", newline="\n")
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Write failed: {e}")

    b_bom.clicked.connect(lambda: save_text(
        "Save Card Bill Of Materials", f"card_bom_{pkg}.csv", "CSV Files (*.csv)", sauth.to_card_bom_csv))
    b_net.clicked.connect(lambda: save_text(
        "Save KiCad Netlist", f"{pkg}_socket.net", "KiCad Netlist (*.net)", sauth.to_kicad_netlist))

    def write_bundle():
        d = QFileDialog.getExistingDirectory(root, "Choose an output folder", base)
        if not d:
            return

        def job():
            c = db.connect(db.default_db_path())
            try:
                return sauth.write_authority(c, pkg, Path(d))
            finally:
                c.close()

        run_populate(ctx, job,
                     lambda r, ok2: ctx.services.log(
                         f"Wrote authority bundle for {pkg}." if ok2 else "Bundle write failed, see status."),
                     busy=f"Writing authority bundle for {pkg}...")

    b_bundle.clicked.connect(write_bundle)
    return root


# ── All Pins panel (every position across all parts in the package) ───────────
def _five_v_short(p) -> str:
    fv = p.get("five_v")
    if fv is None:
        return "Not Applicable"
    if fv.get("tolerant"):
        return "Yes"
    if any((fv.get("by_family") or {}).values()):
        return "Part-Dependent"
    return "No"


def _pin_row(p) -> list:
    """One flat row of a position's data, shared by the table and the CSV export."""
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    cat = pins._CAT_WORD.get(sauth._NET_CATEGORY.get(dest, "lane"), "Card Lane")
    return [str(p["position"]), pins._names(p["pin_names"]), pins._names(p["role_set"]),
            cat, pins._SWITCH_LABEL.get(p["switch_class"], p["switch_class"]),
            pins.expandNet(dest), _five_v_short(p)]


_PIN_COLS = ["Pin", "Pin Names", "Roles", "Category", "Switch Class", "Destination", "5 V Tolerant"]


def _allpins_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(12)
    if state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); return root
    pkg = state.package
    positions = state.authority()["positions"]

    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow(f"All Pins   {pkg}   {len(positions)} Positions")); bar.addStretch(1)
    b_csv = W.btn("Export All Pins...", "primary", "Write every pin and its data to a CSV file")
    bar.addWidget(b_csv); lay.addLayout(bar)
    lay.addWidget(W.body(
        "Every socket position across all supported STM32 parts in this package, with its "
        "functions, category, switch class and delivered net.", dim=True))

    rows = [_pin_row(p) for p in positions]
    lay.addWidget(W.data_table(_PIN_COLS, rows, stretch_col=(1, 2, 5), mono_cols={0, 5}), 1)

    def export():
        base = str(Path(ctx.cfg.get("RepoRoot") or "."))
        fn, _ = QFileDialog.getSaveFileName(root, "Export All Pins", str(Path(base) / f"pins_{pkg}.csv"),
                                            "CSV Files (*.csv)")
        if not fn:
            return
        import csv, io
        buf = io.StringIO(); writer = csv.writer(buf); writer.writerow(_PIN_COLS)
        for p in positions:
            writer.writerow(_pin_row(p))
        try:
            Path(fn).write_text(buf.getvalue(), encoding="utf-8", newline="")
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Export failed: {e}")

    b_csv.clicked.connect(export)
    return root


# ── Profiles panel (real authority: one baseline fabric + minority divergences) ─
def _flow_tokens(items, make=None, per_row: int = 8) -> QWidget:
    """Wrap tokens across rows so a long list never runs off the panel. `make`
    builds each widget (defaults to a plain token)."""
    make = make or (lambda x: W.token(str(x)))
    box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(6)
    row = QHBoxLayout(); row.setSpacing(6); col.addLayout(row)
    per = 0
    for it in items:
        if per >= per_row:
            row = QHBoxLayout(); row.setSpacing(6); col.addLayout(row); per = 0
        row.addWidget(make(it)); per += 1
    for r in range(col.count()):
        lo = col.itemAt(r).layout()
        if lo:
            lo.addStretch(1)
    return box


def _chip_grid(items, make, cols: int = 5) -> QWidget:
    """Uniform grid of chips: every column equal width so the chips stack evenly."""
    w = QWidget(); g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(6); g.setVerticalSpacing(6)
    for i, it in enumerate(items):
        b = make(it)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(b, i // cols, i % cols)
    for c in range(cols):
        g.setColumnStretch(c, 1)
    if not items:
        g.addWidget(W.body("None.", dim=True), 0, 0)
    return w


def _switch_pill(pos, name, cat, roles, on_click) -> QPushButton:
    """A clickable pill for a switching pin: category-coloured name + its roles."""
    b = QPushButton(f"{name}   {roles}" if roles else name)
    b.setObjectName("switchpill")
    b.setCursor(Qt.PointingHandCursor)
    b.setToolTip(f"Pin {pos}: {roles or name}. Click to view it on the map.")

    def style():
        col = T.category(cat)
        b.setStyleSheet(
            f"QPushButton#switchpill{{background:{T.t('tok')};border:none;border-radius:4px;"
            f"color:{col};padding:5px 11px;text-align:left;font-family:{T.MONO_STACK};font-size:12px;}}"
            f"QPushButton#switchpill:hover{{background:{T.t('ctl_hover')};}}")
    W.register_restyle(style)
    b.clicked.connect(lambda: on_click(pos))
    return b


def _profiles_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    if state.error or state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); lay.addStretch(1); return root
    try:
        a = state.authority()
    except Exception as e:  # noqa: BLE001
        lay.addWidget(W.body(f"Could not build the authority: {e}", dim=True)); lay.addStretch(1); return root
    man = a.get("manifest", {}); roll = a.get("rollup", {})
    parts = man.get("supported_parts", []) or []
    fams = man.get("supported_families", []) or []

    top = QHBoxLayout(); top.setSpacing(8)
    for txt in (state.package, f"{man.get('part_count', len(parts))} Supported Parts", f"{len(fams)} Families"):
        top.addWidget(W.tag(txt, "mut"))
    top.addStretch(1)
    lay.addLayout(top)

    # baseline fabric
    card = W.Card(pad=16)
    ttl = QLabel("Baseline Switch Fabric"); ttl.setFont(T.ui_font(10, semibold=True))
    W.register_restyle(lambda: ttl.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    card.body.addWidget(ttl)
    card.body.addWidget(W.dl([
        ("Must-Switch Positions", W.body(str(roll.get("must_switch_count", 0)), mono=True)),
        ("Switch Channels", W.body(str(roll.get("channel_count", 0)), mono=True)),
        ("Switch Cells", W.body(str(roll.get("cells_as_built", 0)), mono=True)),
        ("Oscillator Optional", W.body(str(roll.get("osc_optional_count", 0)), mono=True)),
    ], key_width=200))
    lay.addWidget(card)

    # switching pins (clickable, category-coloured, jump to the map)
    switch_pins = [p for p in a["positions"] if p.get("switch_class") == "must_switch"]
    lay.addWidget(W.eyebrow(f"Switching Pins ({len(switch_pins)})"))

    def pill_for(p):
        name = next(iter(p["pin_names"]), f"Pin {p['position']}")
        roles = "/".join(list(p.get("role_set", {}).keys())[:2])
        return _switch_pill(p["position"], name, _pin_category(p), roles,
                            lambda pos: state.goto_authority_pin(pos) if state.goto_authority_pin else None)
    lay.addWidget(_chip_grid(switch_pins, pill_for, cols=4))

    lay.addWidget(W.eyebrow("Supported Families"))
    lay.addWidget(_flow_tokens(fams))

    # chips grouped by profile — computed off the GUI thread (a resolve per part)
    lay.addWidget(W.eyebrow("Chips by Profile"))
    prof_box = QVBoxLayout(); prof_box.setSpacing(12); lay.addLayout(prof_box)
    prof_box.addWidget(W.body("Grouping supported chips by profile...", dim=True))

    def compute():
        # SQLite connections are thread-bound, so open a fresh one in this worker
        conn = db.connect(db.default_db_path())
        try:
            tiers = {}
            for mpn in parts:
                try:
                    r = sauth.resolve_part(conn, mpn)
                except Exception:  # noqa: BLE001
                    r = None
                confs = (r.get("rail_conflicts") or []) if r else []
                sig = tuple(sorted((c.get("needs", ""), c.get("name", "")) for c in confs))
                tiers.setdefault(sig, []).append(mpn)
            return tiers
        finally:
            conn.close()

    def populate(tiers, ok):
        clear_layout(prof_box)
        if not tiers:
            prof_box.addWidget(W.body("Could not group the chips.", dim=True)); return
        ordered = sorted(tiers.items(), key=lambda kv: (len(kv[0]), -len(kv[1])))
        for n, (sig, mpns) in enumerate(ordered, 1):
            pcard = W.Card(pad=16)
            head = QHBoxLayout(); head.setSpacing(10)
            badge = QLabel(str(n)); badge.setFont(T.mono_font(13, semibold=True))
            badge.setFixedSize(28, 28); badge.setAlignment(Qt.AlignCenter)
            W.register_restyle(lambda b=badge: b.setStyleSheet(
                f"background:{T.t('tok')};color:{T.t('txt1')};border-radius:6px;"))
            head.addWidget(badge)
            name = "Baseline" if not sig else "Needs " + ", ".join(f"{nm} ({nd})" for nd, nm in sig)
            nlab = QLabel(name); nlab.setFont(T.ui_font(10, semibold=True))
            W.register_restyle(lambda nl=nlab: nl.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
            head.addWidget(nlab)
            head.addWidget(W.tag("Fully Supported" if not sig else "Divergent", "ok" if not sig else "warn"))
            head.addStretch(1)
            pct = round(100 * len(mpns) / max(1, len(parts)))
            head.addWidget(W.body(f"{len(mpns)} Chips ({pct}%)", dim=True))
            pcard.body.addLayout(head)
            pcard.body.addWidget(_chip_grid(
                sorted(mpns),
                lambda m: W.token_button(str(m), lambda x: state.goto_resolver(x) if state.goto_resolver else None,
                                         "View this chip's pinout"),
                cols=5))
            prof_box.addWidget(pcard)

    run_populate(ctx, compute, populate, busy="Grouping supported chips by profile...")
    lay.addStretch(1)
    return root


# ── the feature ──────────────────────────────────────────────────────────────
class BenchFeature(F.Feature):
    id = "bench"
    title = "Bench"
    order = 10

    def build(self, ctx: F.Context) -> QWidget:
        state = BenchState()
        header = None
        if state.packages:
            combo = QComboBox()
            combo.addItems(state.packages)
            combo.setCurrentText(state.package or state.packages[0])
            combo.setFixedWidth(170)
            combo.setToolTip("Choose the STM32 package to inspect")
            combo.currentTextChanged.connect(state.set_package)
            header = W.hstack(W.eyebrow("Package"), combo, spacing=8)
        panels = [
            ("Overview", lambda c: W.scroll_body(_authority_panel(c, state))),
            ("Profiles", lambda c: W.scroll_body(_profiles_panel(c, state))),
            ("All Pins", lambda c: _allpins_panel(c, state)),
            ("MCU Pinout Viewer", lambda c: W.scroll_body(_resolver_panel(c, state))),
            ("Exports", lambda c: W.scroll_body(_outputs_panel(c, state))),
        ]
        ws = W.Workspace(ctx, "Bench", panels, header=header)

        def goto_resolver(mpn):
            ws.select_panel("MCU Pinout Viewer")     # builds the viewer + its bus handler
            ctx.bus.emit("bench.resolve", mpn)       # then resolve the clicked chip

        def goto_authority_pin(pos):
            ws.select_panel("Overview")
            ctx.bus.emit("bench.jump_pin", pos)
        state.goto_resolver = goto_resolver
        state.goto_authority_pin = goto_authority_pin
        return ws


F.register(BenchFeature())
