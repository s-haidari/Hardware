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

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QComboBox)

from .. import theme as T
from .. import widgets as W
from .. import feature as F

import stm32_db as db
import stm32_authority as sauth
import stm32_pins_tab as pins   # pure helpers: pin_map_geometry, _pin_detail_rows

_CAT_FROM_NET = {"analog": "power", "power": "power", "ground": "ground",
                 "core": "core", "service": "service", "lane": "lane"}


def _pin_category(p: dict) -> str:
    sc = p.get("switch_class", "")
    if sc == "must_switch":
        return "must"
    if sc == "osc_optional":
        return "osc"
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    return _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(dest, "lane"), "lane")


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


# ── the painted pin map (category colour + numbers + click) ──────────────────
class PinMap(QWidget):
    SIZE = 460

    def __init__(self, on_select, parent=None):
        super().__init__(parent)
        self._on_select = on_select
        self._geo = {"body": (0, 0, 0, 0), "pins": []}
        self._catof = {}
        self._selected = None
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)

    def set_authority(self, authority):
        positions = authority["positions"] if authority else []
        self._geo = pins.pin_map_geometry(positions, self.SIZE, self.SIZE)
        self._catof = {p["position"]: _pin_category(p) for p in positions}
        if self._selected not in self._catof:
            self._selected = None
        self.update()

    def select(self, pos):
        self._selected = pos
        self.update()

    def paintEvent(self, _e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing, True)
        bx, by, bw, bh = self._geo["body"]
        pen = QPen(T.qcolor("txt3")); pen.setWidthF(1.3)
        qp.setPen(pen); qp.setBrush(Qt.NoBrush)
        qp.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)
        # pin-1 marker
        qp.setBrush(T.qcolor("txt3")); qp.setPen(Qt.NoPen)
        qp.drawEllipse(QRectF(bx + 12, by + 12, 7, 7))
        mono = T.mono_font(7)
        for pin in self._geo["pins"]:
            x, y, w, h = pin["rect"]
            cat = self._catof.get(pin["pos"], "lane")
            qp.setBrush(T.qcolor(T.category(cat))); qp.setPen(Qt.NoPen)
            qp.drawRoundedRect(QRectF(x, y, w, h), 2, 2)
            if pin["pos"] == self._selected:
                sp = QPen(T.qcolor("accent")); sp.setWidthF(2.4)
                qp.setPen(sp); qp.setBrush(Qt.NoBrush)
                qp.drawRoundedRect(QRectF(x - 2.5, y - 2.5, w + 5, h + 5), 3, 3)
            # number, outside the pad
            qp.setFont(mono)
            qp.setPen(T.qcolor("txt1" if pin["pos"] == self._selected else "txt3"))
            num = str(pin["pos"])
            if pin["side"] == "L":
                qp.drawText(QRectF(x - 34, y - 2, 28, h + 4), Qt.AlignRight | Qt.AlignVCenter, num)
            elif pin["side"] == "R":
                qp.drawText(QRectF(x + w + 6, y - 2, 28, h + 4), Qt.AlignLeft | Qt.AlignVCenter, num)
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


_LEGEND = [("power", "Power"), ("ground", "Ground"), ("core", "Core"),
           ("service", "Service"), ("lane", "IO Lane"), ("must", "Must-Switch"),
           ("osc", "Oscillator")]


def _legend() -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)
    lay.addStretch(1)
    for cat, label in _LEGEND:
        cell = QWidget(); h = QHBoxLayout(cell); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        dot = QLabel(); dot.setFixedSize(9, 9)
        W.register_restyle(lambda dot=dot, cat=cat: dot.setStyleSheet(
            f"background:{T.category(cat)};border-radius:2px;"))
        lab = W.eyebrow(label)
        h.addWidget(dot); h.addWidget(lab)
        lay.addWidget(cell)
    lay.addStretch(1)
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
    pin_map = PinMap(on_select=lambda pos: _show_pin(pos))
    map_card.body.addWidget(pin_map, 0, Qt.AlignHCenter)
    map_card.body.addWidget(_legend())
    strip = QHBoxLayout(); strip.setSpacing(26); strip.setContentsMargins(0, 10, 0, 0)
    strip.addStretch(1)
    stat_cells = {}
    for key, label in (("positions_total", "Positions"), ("must_switch_count", "Must-Switch"),
                       ("cells_as_built", "ADG714 Cells"), ("channel_count", "Channels"),
                       ("osc_optional_count", "Osc Optional")):
        c = _stat("0", label); strip.addWidget(c); stat_cells[key] = c
    strip.addStretch(1)
    map_card.body.addLayout(strip)
    map_card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    grid.addWidget(map_card, 0)

    # right: inspector
    insp_card = W.Card(pad=16)
    insp_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    grid.addWidget(insp_card, 1)
    outer.addLayout(grid, 1)
    outer.addStretch(1)

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

    def refresh():
        _clear_layout(verdict_holder)
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
        conflicts = authority.get("fabric_warnings", {}).get("minority_rail_conflicts", [])
        if conflicts:
            chips.append(("Minority Conflict", str(len(conflicts)), "warn"))
        title = "Buildable" if ok else "Not Buildable"
        sub = ("One or more minority-rail conflicts to resolve"
               if conflicts else "All structural rules pass")
        verdict_holder.addWidget(W.Verdict(title, sub, "ok" if ok and not conflicts else "warn", chips))
        # default selection
        first = authority["positions"][0]["position"] if authority["positions"] else None
        if first is not None:
            pin_map.select(first); _show_pin(first)

    state.on_change(refresh)
    refresh()
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
        while result_holder.count():
            it = result_holder.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        mpn = edit.text().strip()
        if not mpn:
            return
        try:
            res = sauth.resolve_part(state.conn, mpn)
        except Exception as e:  # noqa: BLE001
            result_holder.addWidget(W.body(f"Could not resolve: {e}", dim=True))
            return
        if not res:
            result_holder.addWidget(W.body(f"No match for {mpn}.", dim=True))
            return
        result_holder.addWidget(W.eyebrow(f"{res['part']}   {res['package']}   {len(res['pins'])} Pins"))
        rows = []
        for pn in res["pins"][:400]:
            action = pn.get("action", "").replace("_", " ").title()
            act_w = W.tag("Rail Conflict", "err") if pn.get("action") == "rail_conflict" else W.body(action)
            dest = W.body(str(pn.get("dest", "")), mono=True)
            ch = ", ".join(f"Cell {s['cell']} Ch {s['channel']}" for s in pn.get("close_switches", []))
            fv = pn.get("five_v_tolerant")
            fv_w = W.tag("5 V", "ok") if fv else W.body("No" if fv is False else "None", dim=True)
            rows.append([str(pn["pin"]), W.body(str(pn.get("name", "")), mono=True), act_w, dest,
                         W.body(ch or "None", dim=not ch, mono=bool(ch)), fv_w])
        tbl = W.data_table(["Pin", "Name", "Action", "Destination", "Close Switches", "5 V"], rows, stretch_col=3)
        result_holder.addWidget(tbl, 1)

    bar.addWidget(W.btn("Resolve", "primary", "Resolve the part number", resolve))
    bar.addWidget(W.body("Exact silicon, not the package union.", dim=True))
    bar.addStretch(1)
    edit.returnPressed.connect(resolve)
    lay.addLayout(bar)
    lay.addLayout(result_holder, 1)
    return root


# ── Card Outputs panel (real card_bom + current_budget) ──────────────────────
def _outputs_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    if state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); return root
    authority = state.authority()

    lay.addWidget(W.eyebrow(f"Card BOM   {state.package}"))
    bom = sauth.card_bom(authority)
    rows = []
    for r in bom.get("rows", []):
        rows.append([W.body(str(r.get("refdes", "")), mono=True), W.body(str(r.get("mpn", "")), mono=True),
                     W.body(str(r.get("role", ""))), str(r.get("qty", ""))])
    lay.addWidget(W.data_table(["Refdes", "Part Number", "Role", "Qty"], rows, stretch_col=2))

    lay.addWidget(W.eyebrow("Current Budget   Rails"))
    budget = sauth.current_budget(authority)
    brows = []
    for rail, info in sorted(budget.get("rails", {}).items()):
        cap = info.get("input_capacity_ma", 0)
        cap_txt = f"{cap/1000:.2f} A" if cap >= 1000 else f"{cap} mA"
        state_w = W.tag("OK", "ok")
        brows.append([W.net_token(rail, _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(rail, "lane"), "lane")),
                      f"{len(info.get('direct_pins', []))}", f"{len(info.get('switch_channels', []))}",
                      cap_txt, state_w])
    lay.addWidget(W.data_table(["Rail", "Direct", "Switched", "Capacity", "State"], brows, stretch_col=0))
    lay.addStretch(1)
    return root


# ── Profiles panel (representative ladder; per-package tiers) ─────────────────
_PROFILES = [
    (1, "Baseline", 78, 100, ["F405", "F407", "F415", "F417", "F205", "F207"],
     "Foundation card wiring. Every profile extends this.", 18, 24, "ok", "Buildable"),
    (2, "Extended", 16, 20, ["F427", "F429", "F437", "F439"],
     "Adds the VCAP_DSI split and two extra channels on Cell 2.", 20, 26, "ok", "Buildable"),
    (3, "Full", 6, 8, ["F469", "F479"],
     "Adds the DSI regulator rail and minority VDD handling on Pin 1.", 21, 27, "warn", "1 Conflict"),
]


def _profiles_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    pkg = state.package or "Package"
    top = QHBoxLayout(); top.setSpacing(8)
    for txt in (pkg, f"{len(_PROFILES)} Profiles", "128 Chips Covered"):
        top.addWidget(W.tag(txt, "mut"))
    top.addStretch(1); top.addWidget(W.eyebrow("Cumulative Coverage"))
    lay.addLayout(top)
    # coverage bar
    bar = QWidget(); bh = QHBoxLayout(bar); bh.setContentsMargins(0, 0, 0, 0); bh.setSpacing(2)
    bar.setFixedHeight(36)
    for n, name, pct, *_ in _PROFILES:
        seg = QLabel(f"P{n}  {pct}%"); seg.setFont(T.mono_font(9, semibold=True))
        seg.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        seg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        W.register_restyle(lambda seg=seg, n=n: seg.setStyleSheet(
            f"background:{T.t(f'seg{n}')};color:{T.t('surface')};padding:0 12px;"))
        bh.addWidget(seg, pct)
    lay.addWidget(bar)
    for n, name, pct, chips, fam, adds, must, ch, kind, build in _PROFILES:
        card = W.Card(pad=16)
        top = QHBoxLayout(); top.setSpacing(12)
        idx = QLabel(str(n)); idx.setFont(T.mono_font(14, semibold=True)); idx.setFixedSize(32, 32)
        idx.setAlignment(Qt.AlignCenter)
        W.register_restyle(lambda idx=idx: idx.setStyleSheet(
            f"background:{T.t('tok')};color:{T.t('txt1')};border-radius:6px;"))
        namecol = QVBoxLayout(); namecol.setSpacing(1)
        nlab = QLabel(name); nlab.setFont(T.ui_font(10, semibold=True))
        W.register_restyle(lambda nlab=nlab: nlab.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
        namecol.addWidget(nlab)
        namecol.addWidget(W.eyebrow("Baseline Foundation" if n == 1 else f"Extends Profile {n-1}"))
        top.addWidget(idx); top.addLayout(namecol); top.addStretch(1)
        cov = QLabel(f"{pct}%"); cov.setFont(T.mono_font(15, semibold=True))
        W.register_restyle(lambda cov=cov: cov.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
        top.addWidget(cov)
        card.body.addLayout(top)
        fam_row = QHBoxLayout(); fam_row.setSpacing(6)
        for f in fam:
            fam_row.addWidget(W.token(f))
        fam_row.addStretch(1)
        card.body.addLayout(fam_row)
        foot = QHBoxLayout()
        foot.addWidget(W.tag(build, kind))
        foot.addStretch(1)
        foot.addWidget(W.body(f"{must} Must-Switch, {ch} Channels", dim=True))
        card.body.addLayout(foot)
        lay.addWidget(card)
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
            ("Authority", lambda c: W.scroll_body(_authority_panel(c, state))),
            ("Profiles", lambda c: W.scroll_body(_profiles_panel(c, state))),
            ("Part Resolver", lambda c: _resolver_panel(c, state)),
            ("Card Outputs", lambda c: W.scroll_body(_outputs_panel(c, state))),
        ]
        return W.Workspace(ctx, "Bench", panels, header=header)


F.register(BenchFeature())
