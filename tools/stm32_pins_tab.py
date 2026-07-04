"""stm32_pins_tab.py — the 'STM32 Pins' tab: build the CubeMX database, view the
per-socket-position switch decision matrix, and generate the pin data.

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Self-contained widget; the main window mounts it as the
third nav tab.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QFileSystemWatcher, pyqtSignal, QRectF, QPointF
from PyQt5.QtGui import QColor, QBrush, QPainter, QPen, QFont, QFontMetricsF
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication, QSplitter, QStackedWidget,
    QFrame, QScrollArea, QTextBrowser,
)

# Theme-swappable surface colours, derived from the shared design system
# (tools/ui_theme.py) so this tab can never drift from the shell's palette.
# The pin/data colours further down are theme-independent. set_tab_theme()
# reassigns these; the SVG generators and paintEvents read them at call time,
# so a swap + refresh is enough.
import ui_theme

_PANEL = _CARD = _TXT = _MUT = _LINE = _BODY = ""


def set_tab_theme(dark: bool):
    global _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY
    t = ui_theme.DARK_COLORS if dark else ui_theme.LIGHT_COLORS
    _PANEL = t["MAIN_BG"]      # panel background
    _CARD = t["CARD_BG"]       # card surfaces
    _TXT = t["FG"]             # primary text
    _MUT = t["FG_DIM"]         # muted text
    _LINE = t["BORDER"]        # hairlines
    _BODY = t["IN_BG"]         # the QFP package body fill


set_tab_theme(False)   # light is the app default

import stm32_db as sdb
import stm32_authority as sauth

# Icons come from the shared design system (no import back into the shell).
from ui_theme import (lucide_icon, LUCIDE_NEUTRAL, LUCIDE_BLUE,  # noqa: F401
                      LUCIDE_GREEN, LUCIDE_AMBER)


# Scannable columns that fit the viewport without horizontal scrolling. The verbose
# per-pin detail (rationale, ADG714 wiring, tags, bootloader) lives in the focus
# panel beside the table; the CSV/Markdown exports still carry the full column set.
_COLS = ["Pin", "Side", "Pin Name(s)", "Role Set", "Switch",
         "Destination", "Peripherals", "Breakout", "VDD (V)"]

_BREAKOUT_COLOR = "#4c8df0"   # extraction-access / debug-service breakout (blue)

_SWITCH_COLOR = {
    sdb.SWITCH_MUST: "#e5534b",
    sdb.SWITCH_OSC_OPTIONAL: "#e6a030",
    sdb.SWITCH_NONE: "#9aa1a9",
}
_SWITCH_LABEL = {
    sdb.SWITCH_MUST: "Must-Switch",
    sdb.SWITCH_OSC_OPTIONAL: "Oscillator (Optional)",
    sdb.SWITCH_NONE: "Fixed",
}


def _counts(d: dict) -> str:
    return ", ".join(f"{k}×{v}" for k, v in d.items())


def _names(d: dict) -> str:
    """Table-cell value: the distinct names/roles spelled out, most-common first (no
    ×count clutter, no cryptic +N). The full part-by-part counts stay in the detail."""
    return ", ".join(d.keys()) if d else ""


def _numlist(nums, per: int = 6) -> str:
    """Socket numbers chunked into nowrap groups of `per`, so a long run reads as
    scannable blocks instead of one wrapped wall (HTML, for the detail panel). Groups
    are joined by a BREAKABLE space (+ nbsp for the gap) so Qt wraps between groups
    rather than force-breaking a number in half in the narrow panel."""
    if not nums:
        return ""
    groups = [", ".join(str(n) for n in nums[i:i + per]) for i in range(0, len(nums), per)]
    return " &nbsp;&nbsp;".join(f"<span style='white-space:nowrap'>{g}</span>" for g in groups)


def _tag_summary(tags: dict) -> str:
    out = []
    if tags.get("is_debug"):
        out.append("Debug: " + "/".join(tags.get("debug_role", [])))
    if tags.get("is_boot"):
        out.append("Boot")
    if tags.get("is_clock"):
        out.append("Clock")
    if tags.get("is_core_power"):
        out.append("VCAP")
    if tags.get("is_analog_supply"):
        out.append("VDDA/VREF")
    if tags.get("is_trace"):
        out.append("Trace")
    return " · ".join(out)


def _esc(v) -> str:
    return html.escape(str(v))


def _fmt_rng(r, unit="V") -> str:
    return f"{r[0]}–{r[1]} {unit}" if r else ""


def _pin_detail_html(p: dict) -> str:
    """Full detail for one socket position (pure — unit-testable)."""
    fv = p.get("five_v")
    if fv is None:
        fvt = "n/a (non-GPIO)"
    elif fv["tolerant"]:
        fvt = "5V-Tolerant" + (" (except in oscillator mode)" if fv.get("caveat") == "osc-mode" else "")
    elif any(fv["by_family"].values()):
        fam = ", ".join(f"{k.replace('STM32', '')}={'5V' if v else '3V3'}"
                        for k, v in fv["by_family"].items())
        fvt = f"part-dependent ({fam})"
    else:
        fvt = "3.3V-only"
    bk = p.get("breakout", {})
    bnets = ", ".join(bk.get("service_nets", [])) or ""
    adg = p["assignment"].get("adg714")
    adg_t = None
    if adg:
        s_pin, d_pin = sauth.ADG714_SWITCH_PINS[adg["channel"]]
        adg_t = f"cell {adg['cell']} · SW{adg['channel']} ({s_pin}/{d_pin})"
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    el = p.get("electrical", {}) or {}
    why = sauth.switch_rationale(p)
    rows = [
        ("Name(s)", _counts(p["pin_names"])),
        ("Roles", _counts(p["role_set"])),
        ("Switch", _SWITCH_LABEL.get(p['switch_class'], p['switch_class'])),
    ]
    if why:
        rows.append(("Why", why))
    if adg_t:
        rows.append(("ADG714", adg_t))
    rows.append(("Destination", dest))
    rows += [
        ("Breakout", bnets + (" · TRACE" if bk.get("trace") else "")),
        ("Via", bk.get("via", "")),
        ("Tags", _tag_summary(p["tags"]) or ""),
        ("5V", fvt),
        ("Bootloader", ", ".join(p["tags"].get("bootloader_periph", [])) or ""),
        ("Peripherals", ", ".join(p.get("peripherals", [])) or ""),
        ("VDD", _fmt_rng(el.get("vdd_range_v"))),
    ]
    body = "".join(
        f"<tr><td style='color:{_MUT};padding-right:8px;vertical-align:top'>{k}</td>"
        f"<td>{_esc(v)}</td></tr>" for k, v in rows)
    return (f"<h3 style='margin:2px 0'>Pin {p['position']} "
            f"<span style='color:{_MUT}'>({p.get('side', '')})</span></h3>"
            f"<table>{body}</table>")


def _summary_html(a: dict) -> str:
    """Package summary card: rollup + electrical + card materials (pure)."""
    r = a["rollup"]
    ea = a.get("extraction_access", {})
    el = a.get("electrical", {})
    cm = a.get("card_materials", {})
    cats = sauth.category_lists(a)

    def _row(label, color, nums):
        return (f"<tr><td style='color:{color};white-space:nowrap;vertical-align:top;"
                f"padding:2px 12px 2px 0'>{label} ({len(nums)})</td>"
                f"<td style='padding:2px 0'>{_numlist(nums)}</td></tr>")

    lists_html = (
        "<p><b>Pin lists (socket #):</b></p><table cellspacing='0'>"
        + _row("Must-Switch", _SWITCH_COLOR[sdb.SWITCH_MUST], cats["must_switch"])
        + _row("Oscillator (Optional)", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL], cats["osc_optional"])
        + _row("Breakout", _BREAKOUT_COLOR, cats["breakout"])
        + _row("5V all-parts", _MUT, cats["five_v_all_parts"])
        + _row("Never 5V", _MUT, cats["five_v_never"])
        + "</table>")
    items = "".join(
        f"<tr><td style='text-align:right;padding-right:6px'>{i['qty']}×</td>"
        f"<td>{_esc(i['part'])}</td>"
        f"<td style='color:{_MUT};padding-left:8px'>{_esc(i['role'])}</td></tr>"
        for i in cm.get("items", []))
    return (
        f"<h3 style='margin:2px 0'>{a['package']}: {a['manifest']['part_count']} parts</h3>"
        f"<p><b>Switch:</b> {r['must_switch_count']} must-switch; "
        f"{r['osc_optional_count']} oscillator-optional; {r['fixed_count']} fixed</p>"
        f"<p><b>Breakout:</b> {ea.get('service_breakout_count', 0)} service · "
        f"{len(ea.get('debug_positions', []))} debug · {len(ea.get('trace_positions', []))} trace</p>"
        + lists_html +
        f"<p><b>Electrical:</b> I/O ±{el.get('max_io_current_ma', '?')} mA · "
        f"injection ±{el.get('injection_current_ma', '?')} mA<br>"
        f"VDD {_fmt_rng(el.get('vdd_range_v'))} · VDDA {_fmt_rng(el.get('vdda_range_v'))} · "
        f"VBAT {_fmt_rng(el.get('vbat_range_v'))} · VREF+ {_fmt_rng(el.get('vref_range_v'))}<br>"
        f"VCAP required: <b>{el.get('vcap_required')}</b></p>"
        f"<p><b>Card materials (passive BOM):</b></p><table>{items}</table>"
        f"<p style='color:{_MUT}'>{_esc(cm.get('note', ''))}</p>")


def _default_vault_authority_dir():
    """The vault's generated-authority folder, if the Brain vault is present."""
    brain = Path.home() / "Documents" / "Obsidian" / "Brain"
    return (brain / "Wiki" / "Datasets" / "STM32 Pinout Authority") if brain.is_dir() else None


def _vault_authority_dirs():
    """Both authority homes: the registered dataset folder (Wiki/Datasets/) and the
    spec's data/ location. Save-to-Vault writes the same files to each so citations
    against either path resolve."""
    brain = Path.home() / "Documents" / "Obsidian" / "Brain"
    if not brain.is_dir():
        return []
    return [brain / "Wiki" / "Datasets" / "STM32 Pinout Authority", brain / "data"]


# ── QFP pin-map geometry (pure — shared by the Qt widget AND the SVG export, so
#    the live widget and any preview render pixel-for-pixel identically) ──────

def cells_html(a: dict) -> str:
    """The Cells view body: package summary, the SPI control bus with its
    connector contacts, the daisy-chain order, and one table per ADG714 cell
    (channel, Source/Drain terminals, socket pin, rail — spares included).
    Themed rich text (pure — unit-testable)."""
    w = sauth.card_wiring(a)
    cm = sauth.adg714_cell_map(a)
    css_th = f"color:{_MUT};text-align:left;padding:2px 10px 2px 0;font-size:9pt"
    css_td = "padding:2px 10px 2px 0"
    out = [_summary_html(a), "<hr>"]
    out.append("<h3>Control Bus (SPI, shared / daisy-chained)</h3>")
    out.append("<table cellspacing='0'>")
    out.append(f"<tr><th style='{css_th}'>Signal</th><th style='{css_th}'>ADG714 Pin</th>"
               f"<th style='{css_th}'>Connector Contact</th><th style='{css_th}'>Controller</th></tr>")
    for bus in w["bus"]:
        contact = bus["connector_contact"] if bus["connector_contact"] is not None else "(plane)"
        out.append(f"<tr><td style='{css_td}'>{_esc(bus['signal'])}</td>"
                   f"<td style='{css_td}'>{bus['adg714_pin']}</td>"
                   f"<td style='{css_td}'>{_esc(contact)}</td>"
                   f"<td style='{css_td}'>{_esc(bus['controller'])}</td></tr>")
    out.append("</table>")
    out.append(f"<p style='color:{_MUT}'>{_esc(w['daisy_chain']['note'])}</p>")
    if w.get("exclusive_groups"):
        pins = ", ".join(str(g["socket_pin"]) for g in w["exclusive_groups"])
        out.append(f"<p><b>One-hot groups:</b> channels sharing a socket pin are "
                   f"mutually exclusive branches — close at most one per pin. "
                   f"Multi-branch pins: {pins}.</p>")
    for cell in cm:
        out.append(f"<h3>Cell {cell['cell']} <span style='color:{_MUT}'>"
                   f"({_esc(cell['symbol'])} · {_esc(cell['footprint'])})</span></h3>")
        out.append("<table cellspacing='0'>")
        out.append(f"<tr><th style='{css_th}'>Channel</th><th style='{css_th}'>Terminals</th>"
                   f"<th style='{css_th}'>Socket Pin</th><th style='{css_th}'>Rail</th></tr>")
        for sw in cell["switches"]:
            if sw["spare"]:
                out.append(f"<tr><td style='{css_td}'>{sw['channel']}</td>"
                           f"<td style='{css_td}'>{sw['s_pin']}/{sw['d_pin']}</td>"
                           f"<td style='{css_td};color:{_MUT}' colspan='2'>Spare (No Connect)</td></tr>")
            else:
                out.append(f"<tr><td style='{css_td}'>{sw['channel']}</td>"
                           f"<td style='{css_td}'>{sw['s_pin']}/{sw['d_pin']}</td>"
                           f"<td style='{css_td}'>{sw['position']} ({_esc(sw['pin_name'])})</td>"
                           f"<td style='{css_td}'>{_esc(sw['destination'])}</td></tr>")
        out.append("</table>")
    return "".join(out)


def pin_map_geometry(positions: list, w: float, h: float, margin: float = 46) -> dict:
    """Lay socket pins on a centered QFP body. Returns {body:(x,y,w,h),
    pins:[{pos, side, rect:(x,y,w,h), sw, breakout, name}]}. Pin 1 starts top-left
    and numbers counter-clockwise: left (top→bottom), bottom (L→R), right (bottom
    →top), top (R→L) — the standard LQFP order."""
    by = {p["position"]: p for p in positions}
    nums = sorted(by)
    n = len(nums)
    if not n:
        return {"body": (0, 0, 0, 0), "pins": []}
    per = max(1, n // 4)
    span = min(w, h) - 2 * margin
    body = span * 0.66
    plen = span * 0.095
    cx, cy = w / 2, h / 2
    bl, bt = cx - body / 2, cy - body / 2
    br, bb = cx + body / 2, cy + body / 2
    pitch = body / per
    pw = pitch * 0.60
    pins = []
    for idx, pos in enumerate(nums):
        p = by[pos]
        if idx < per:                                    # left, top→bottom
            y = bt + (idx) * pitch + (pitch - pw) / 2
            rect, side = (bl - plen, y, plen, pw), "L"
        elif idx < 2 * per:                              # bottom, left→right
            x = bl + (idx - per) * pitch + (pitch - pw) / 2
            rect, side = (x, bb, pw, plen), "B"
        elif idx < 3 * per:                              # right, bottom→top
            y = bb - (idx - 2 * per) * pitch - (pitch + pw) / 2
            rect, side = (br, y, plen, pw), "R"
        else:                                            # top, right→left
            x = br - (idx - 3 * per) * pitch - (pitch + pw) / 2
            rect, side = (x, bt - plen, pw, plen), "T"
        bk = p.get("breakout", {})
        pins.append({
            "pos": pos, "side": side, "rect": tuple(round(v, 2) for v in rect),
            "sw": p["switch_class"],
            "breakout": bool(bk.get("service_nets") or bk.get("trace")),
            "name": next(iter(p["pin_names"]), ""),
        })
    return {"body": tuple(round(v, 2) for v in (bl, bt, body, body)), "pins": pins}


def pin_map_svg(authority: dict, w: int = 460, h: int = 460, selected=None) -> str:
    """SVG render of the pin map (same geometry the widget paints) — for preview
    and 'export pin map'."""
    g = pin_map_geometry(authority["positions"], w, h)
    bl, bt, bw, bh = g["body"]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'font-family="Inter,Segoe UI,Arial,sans-serif"><rect width="{w}" height="{h}" fill="{_PANEL}"/>',
         f'<rect x="{bl}" y="{bt}" width="{bw}" height="{bh}" rx="8" fill="{_BODY}" '
         f'stroke="{_LINE}" stroke-width="1.5"/>',
         f'<text x="{bl+bw/2}" y="{bt+bh/2}" fill="{_MUT}" text-anchor="middle" '
         f'font-size="12">{html.escape(authority["package"])}</text>']
    for pin in g["pins"]:
        x, y, pwd, ph = pin["rect"]
        col = _SWITCH_COLOR.get(pin["sw"], "#9aa1a9")
        s.append(f'<rect x="{x}" y="{y}" width="{pwd}" height="{ph}" rx="2" fill="{col}"/>')
        if pin["breakout"]:
            s.append(f'<rect x="{x-1.5}" y="{y-1.5}" width="{pwd+3}" height="{ph+3}" rx="3" '
                     f'fill="none" stroke="{_BREAKOUT_COLOR}" stroke-width="2"/>')
        if pin["pos"] == selected:
            s.append(f'<rect x="{x-3}" y="{y-3}" width="{pwd+6}" height="{ph+6}" rx="4" '
                     f'fill="none" stroke="{_TXT}" stroke-width="2"/>')
    s.append("</svg>")
    return "".join(s)


_SVG_FONT = "Geist,Inter,'Segoe UI',system-ui,Arial"
_SVG_MONO = "'JetBrains Mono',Consolas,monospace"


_CAT_COLOR = {"power": "#e5534b", "analog": "#e6a030", "ground": "#9aa1a9",
              "core": "#8b6fe8", "service": "#24b196", "lane": "#4c8df0"}
_CAT_LABEL = [("All", None), ("Switched", "switch"), ("Power", "power"), ("Analog", "analog"),
              ("Ground", "ground"), ("Core VCAP", "core"), ("Debug & Service", "service"),
              ("GPIO Lanes", "lane")]


def _pin_branches(a: dict, pos: int, cw: dict = None):
    """The physical connection branches of one socket pin — shared by the focus panel,
    the wiring band, and the connections fabric so they never diverge. Returns
    (conn, kind, name, pcol, branches), where branches is a list of
    (caption, [(title, sub, colour-or-None)]); colour None marks a neutral intermediate
    node, a colour marks a delivered destination. A switched pin has two branches (its
    ADG714 channel to a rail, and its default 33 ohm IO lane); others have one. Pass a
    precomputed card_wiring() as cw to avoid rebuilding it per pin."""
    conn = next((c for c in sauth.socket_connections(a) if c["pin"] == pos), None)
    p = next((x for x in a["positions"] if x["position"] == pos), None)
    kind = conn["kind"] if conn else "direct"
    name = next(iter(p["pin_names"]), "") if (p and p["pin_names"]) else ""
    pcol = _CAT_COLOR.get(conn["category"], "#4c8df0") if conn else _MUT
    branches = []
    if kind == "switch":
        cw = cw or sauth.card_wiring(a)
        chans = [x for x in cw["channels"] if x["socket_pin"] == pos]
        many = len(chans) > 1                 # mutually-exclusive branches (one-hot)
        for bi, c in enumerate(chans, start=1):
            rail_sub = ("Contact " + " / ".join(c["connector_contacts"])) if c["connector_contacts"] \
                else ("Ground Plane" if c["rail"] == "GND" else "Local Cap")
            cap = f"SWITCHED ROLE {bi} OF {len(chans)}" if many else "SWITCHED ROLE"
            branches.append((cap, [
                (f"ADG714 Cell {c['cell']} · Channel {c['channel']}",
                 f"Source {c['s_pin']} Pin {c['s_pin_num']} · Drain {c['d_pin']} Pin {c['d_pin_num']}", None),
                (c["rail"], rail_sub, _CAT_COLOR.get(sauth._NET_CATEGORY.get(c["rail"], "lane"), pcol))]))
        if chans:
            branches.append(("DEFAULT IO LANE", [
                ("33 Ω Series Resistor", "", None),
                (chans[0]["card_lane"], "Lane Row", _CAT_COLOR["lane"])]))
    elif kind == "resistor":
        branches.append(("IO LANE", [
            ("33 Ω Series Resistor", "", None),
            (conn["dest"], "Lane Row", _CAT_COLOR["lane"])]))
    else:
        branches.append(("DIRECT", [
            (conn["dest"] if conn else "",
             f"Contact {conn['contact']}" if (conn and conn["contact"]) else "Hardwired", pcol)]))
    return conn, kind, name, pcol, branches


class _NumItem(QTableWidgetItem):
    """Table item that sorts by its numeric UserRole, so the Pin column orders
    1, 2, ... 10, ... 64 rather than lexicographically (1, 10, 11, ... 2, ...)."""
    def __lt__(self, other):
        try:
            return int(self.data(Qt.UserRole)) < int(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


class PinMapWidget(QWidget):
    """QFP pin-map: paints the socket with pins coloured by switch class (violet
    ring = breakout) via the shared pin_map_geometry; click → pinClicked(pos)."""
    pinClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.authority = None
        self.selected = None
        self.hover = None
        self._hover_xy = None
        self.highlight = set()
        self.setMinimumSize(380, 380)
        self.setMouseTracking(True)

    def set_authority(self, a):
        self.authority = a
        self.selected = None
        self.hover = None
        self.highlight = set()
        self.update()

    def set_selected(self, pos):
        self.selected = pos
        self.update()

    def set_highlight(self, positions):
        self.highlight = set(positions or [])
        self.update()

    def _geom(self):
        if not self.authority:
            return None
        return pin_map_geometry(self.authority["positions"], self.width(), self.height())

    def _pin_at(self, px, py, g=None):
        g = g or self._geom()
        if not g:
            return None
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            if x - 3 <= px <= x + pw + 3 and y - 3 <= py <= y + ph + 3:
                return pin
        return None

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        qp.fillRect(self.rect(), QColor(_PANEL))
        g = self._geom()
        if not g or not g["pins"]:
            qp.setPen(QColor(_MUT))
            qp.drawText(self.rect(), Qt.AlignCenter, "Build the database to see the pin map")
            return
        bl, bt, bw, bh = g["body"]
        # body: a QFP package with an inner bevel and a pin-1 corner dot
        qp.setPen(QPen(QColor(_LINE), 1.5))
        qp.setBrush(QColor(_BODY))
        qp.drawRoundedRect(QRectF(bl, bt, bw, bh), 10, 10)
        inset = 7
        qp.setPen(QPen(QColor(_LINE), 1))
        qp.setBrush(Qt.NoBrush)
        qp.drawRoundedRect(QRectF(bl + inset, bt + inset, bw - 2 * inset, bh - 2 * inset), 7, 7)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor(_MUT))
        qp.drawEllipse(QPointF(bl + inset + 9, bt + inset + 9), 3.2, 3.2)
        # package caption
        pkg = self.authority["package"]
        n = len(g["pins"])
        qp.setPen(QColor(_TXT))
        f = QFont(_SVG_FONT.split(",")[0])
        f.setPointSizeF(11.5)
        f.setWeight(QFont.DemiBold)
        qp.setFont(f)
        qp.drawText(QRectF(bl, bt + bh / 2 - 15, bw, 18), Qt.AlignCenter, pkg)
        f.setPointSizeF(8.5)
        f.setWeight(QFont.Normal)
        qp.setFont(f)
        qp.setPen(QColor(_MUT))
        qp.drawText(QRectF(bl, bt + bh / 2 + 2, bw, 14), Qt.AlignCenter, f"{n} pins")
        # numbers: label every lead when they fit, else a ruler (pin 1 + multiples of 5)
        per = max(1, n // 4)
        pitch = bw / per if per else bw
        dense = pitch < 13
        numf = QFont(_SVG_MONO.split(",")[0].strip("'"))
        numf.setPointSizeF(max(6.5, min(8.5, pitch * 0.5)))
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            pos = pin["pos"]
            emph = pos in (self.selected, self.hover)
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(_SWITCH_COLOR.get(pin["sw"], "#9aa1a9")))
            qp.drawRoundedRect(QRectF(x, y, pw, ph), 1.6, 1.6)
            if pin["breakout"]:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_BREAKOUT_COLOR), 2))
                qp.drawRoundedRect(QRectF(x - 1.5, y - 1.5, pw + 3, ph + 3), 2, 2)
            if pos in self.highlight:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor("#24b196"), 2.5))
                qp.drawRoundedRect(QRectF(x - 3.5, y - 3.5, pw + 7, ph + 7), 3, 3)
            if pos == self.selected:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_TXT), 2))
                qp.drawRoundedRect(QRectF(x - 3, y - 3, pw + 6, ph + 6), 3, 3)
            if dense and not (emph or pos % 5 == 0 or pos == 1):
                continue
            qp.setFont(numf)
            qp.setPen(QColor(_TXT if emph else _MUT))
            s = str(pos)
            side = pin["side"]
            if side == "L":
                r = QRectF(x - 40, y + ph / 2 - 7, 36, 14); al = Qt.AlignRight | Qt.AlignVCenter
            elif side == "R":
                r = QRectF(x + pw + 4, y + ph / 2 - 7, 36, 14); al = Qt.AlignLeft | Qt.AlignVCenter
            elif side == "T":
                r = QRectF(x + pw / 2 - 18, y - 17, 36, 14); al = Qt.AlignHCenter | Qt.AlignBottom
            else:
                r = QRectF(x + pw / 2 - 18, y + ph + 3, 36, 14); al = Qt.AlignHCenter | Qt.AlignTop
            qp.drawText(r, al, s)
        # hover callout: pin number + name + switch role, anchored to the cursor
        if self.hover is not None and self._hover_xy:
            hp = next((p for p in g["pins"] if p["pos"] == self.hover), None)
            if hp:
                self._draw_callout(qp, hp)

    def _draw_callout(self, qp, hp):
        title = f"Pin {hp['pos']}"
        name = hp["name"] or "—"
        role = _SWITCH_LABEL.get(hp["sw"], "")
        tf = QFont(_SVG_FONT.split(",")[0]); tf.setPointSizeF(10.5); tf.setWeight(QFont.DemiBold)
        sf = QFont(_SVG_FONT.split(",")[0]); sf.setPointSizeF(9.0)
        fm_t, fm_s = QFontMetricsF(tf), QFontMetricsF(sf)
        wln = max(fm_t.horizontalAdvance(f"{title}   {name}"), fm_s.horizontalAdvance(role))
        bw2 = wln + 24
        bh2 = 44
        px, py = self._hover_xy
        bx = min(px + 14, self.width() - bw2 - 4)
        by = min(py + 14, self.height() - bh2 - 4)
        bx, by = max(4, bx), max(4, by)
        qp.setPen(QPen(QColor(_LINE), 1))
        qp.setBrush(QColor(_CARD))
        qp.drawRoundedRect(QRectF(bx, by, bw2, bh2), 8, 8)
        qp.setBrush(QColor(_SWITCH_COLOR.get(hp["sw"], "#9aa1a9")))
        qp.setPen(Qt.NoPen)
        qp.drawEllipse(QPointF(bx + 12, by + 16), 4, 4)
        qp.setFont(tf)
        qp.setPen(QColor(_TXT))
        qp.drawText(QRectF(bx + 22, by + 8, bw2 - 26, 16), Qt.AlignLeft | Qt.AlignVCenter,
                    f"{title}   {name}")
        qp.setFont(sf)
        qp.setPen(QColor(_MUT))
        qp.drawText(QRectF(bx + 12, by + 25, bw2 - 16, 14), Qt.AlignLeft | Qt.AlignVCenter, role)

    def mouseMoveEvent(self, ev):
        pin = self._pin_at(ev.x(), ev.y())
        pos = pin["pos"] if pin else None
        self._hover_xy = (ev.x(), ev.y()) if pin else None
        self.setCursor(Qt.PointingHandCursor if pin else Qt.ArrowCursor)
        if pos != self.hover or pin:
            self.hover = pos
            self.update()

    def leaveEvent(self, _ev):
        if self.hover is not None:
            self.hover = None
            self._hover_xy = None
            self.update()

    def mousePressEvent(self, ev):
        pin = self._pin_at(ev.x(), ev.y())
        if pin:
            self.selected = pin["pos"]
            self.update()
            self.pinClicked.emit(pin["pos"])


class _MiniStat(QFrame):
    """A compact top-strip stat: a value over a small label with a coloured left
    accent. Fixed width, so every stat is the same size whatever its value."""
    def __init__(self, label, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("miniStat")
        self._accent = accent
        self.setFixedWidth(106)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 5, 10, 6)
        lay.setSpacing(0)
        self._v = QLabel("–")
        self._l = QLabel(label)
        lay.addWidget(self._v)
        lay.addWidget(self._l)
        self.restyle()

    def restyle(self):
        self.setStyleSheet(f"#miniStat{{background:{_CARD};border-radius:8px;"
                           f"border-left:3px solid {self._accent};}}")
        self._v.setStyleSheet(f"color:{_TXT};font-size:14px;font-weight:700;")
        self._l.setStyleSheet(f"color:{_MUT};font-size:9px;font-weight:600;")

    def set(self, value):
        self._v.setText(str(value))


class ConnectionRow(QFrame):
    """One clickable pin card showing EVERY physical path the socket pin has, from its
    ZIF contact to the parent connector: a switched pin shows both its ADG714 channel
    path (with the real Source/Drain terminal pins) and its default 33 ohm lane; other
    pins show their single path. Clicking emits the pin so the map/band follow along."""
    clicked = pyqtSignal(int)

    def __init__(self, pin, name, category, branches, parent=None):
        super().__init__(parent)
        self.pin = pin
        self._name = name
        self._category = category
        self._branches = branches
        self._selected = False
        self.setObjectName("connRow")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(3)
        self._head = QLabel(); self._head.setTextFormat(Qt.RichText)
        lay.addWidget(self._head)
        self._paths = []
        for _cap, _stops in branches:
            lbl = QLabel(); lbl.setTextFormat(Qt.RichText); lbl.setWordWrap(True)
            lay.addWidget(lbl)
            self._paths.append(lbl)
        self.restyle()

    def _chain_html(self, caption, stops, pcol):
        arrow = f"<span style='color:{_LINE}'> &#8594; </span>"
        parts = [f"<span style='color:{_MUT}'>ZIF Socket</span>"]
        for i, (title, sub, color) in enumerate(stops):
            if i == len(stops) - 1:                       # destination: coloured net + contact
                parts.append(f"{arrow}<span style='color:{color or pcol};font-weight:700'>"
                             f"{html.escape(title)}</span>"
                             f"<span style='color:{_MUT}'> &#183; {html.escape(sub)}</span>")
            else:                                         # component (switch / resistor)
                detail = f"<span style='color:{_MUT}'> ({html.escape(sub)})</span>" if sub else ""
                parts.append(f"{arrow}<span style='color:{_TXT}'>{html.escape(title)}</span>{detail}")
        cap = (f"<span style='color:#a2a2a8;font-weight:700;font-size:8pt;"
               f"letter-spacing:0.6px'>{caption}</span> &nbsp; ")
        return f"<span style='font-size:9pt'>{cap}{''.join(parts)}</span>"

    def restyle(self):
        col = _CAT_COLOR.get(self._category, "#4c8df0")   # bar + destination only
        border = col if self._selected else "transparent"
        self.setStyleSheet(
            f"#connRow{{background:{_CARD};border:1px solid {border};"
            f"border-left:3px solid {col};border-radius:8px;}}"
            f"#connRow:hover{{background:{_PANEL};}}")
        self._head.setText(
            f"<span style='color:{_MUT};font-family:JetBrains Mono;font-weight:700'>{self.pin}</span>"
            f"&nbsp;&nbsp;&nbsp;<span style='color:{_TXT};font-weight:700'>{html.escape(self._name)}</span>")
        for lbl, (cap, stops) in zip(self._paths, self._branches):
            lbl.setText(self._chain_html(cap, stops, col))

    def set_selected(self, sel):
        if sel != self._selected:
            self._selected = sel
            self.restyle()

    def mousePressEvent(self, _ev):
        self.clicked.emit(self.pin)


class ConnectionsList(QWidget):
    """Filterable, sortable list of every socket pin's connection. Rows are clickable
    (ConnectionRow) and drive selection; a category filter and a sort control let you
    reorder any way you like without hiding data."""
    pinClicked = pyqtSignal(int)
    _SORTS = ["Pin Number", "Category", "Destination"]
    _CAT_ORDER = {"power": 0, "analog": 1, "ground": 2, "core": 3, "service": 4, "lane": 5}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._authority = None
        self._rows = {}
        self._sel = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout(); bar.setSpacing(6)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems([lbl for lbl, _ in _CAT_LABEL])
        self.filter_combo.currentTextChanged.connect(self._rebuild)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(self._SORTS)
        self.sort_combo.currentTextChanged.connect(self._rebuild)
        bar.addWidget(QLabel("Show:")); bar.addWidget(self.filter_combo)
        bar.addWidget(QLabel("Sort:")); bar.addWidget(self.sort_combo)
        bar.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Pin, name, net, contact, rail, cell…")
        self.search.setMaximumWidth(230)
        self.search.textChanged.connect(self._rebuild)
        bar.addWidget(self.search)
        bar.addStretch()
        self.count = QLabel(""); self.count.setObjectName("connCount")
        bar.addWidget(self.count)
        root.addLayout(bar)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setObjectName("connScroll")
        self._inner = QWidget(); self._inner.setObjectName("connInner")
        self._vl = QVBoxLayout(self._inner)
        self._vl.setContentsMargins(2, 2, 2, 2)
        self._vl.setSpacing(5)
        self._vl.addStretch()
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)
        self._style_scroll()

    def set_authority(self, a):
        self._authority = a
        self._rebuild()

    def _haystacks(self):
        """pin -> lowercase searchable text: name, destination, contact, plus every
        switch branch's rail / cell / channel / lane — so 'which pin lands on LA-33'
        or 'cell 3' answers itself."""
        a = self._authority
        hs = {}
        cw = sauth.card_wiring(a)
        per_pin = {}
        for c in cw["channels"]:
            per_pin.setdefault(c["socket_pin"], []).append(
                f"{c['rail']} {c['card_lane']} cell {c['cell']} channel {c['channel']} "
                f"{' '.join(c['connector_contacts'])}")
        for rec in sauth.socket_connections(a):
            extra = " ".join(per_pin.get(rec["pin"], []))
            hs[rec["pin"]] = (f"{rec['pin']} {rec['name']} {rec['kind']} {rec['dest']} "
                              f"{rec['contact']} {extra}").lower()
        return hs

    def _records(self):
        if not self._authority:
            return []
        conns = sauth.socket_connections(self._authority)
        q = (self.search.text() or "").strip().lower() if hasattr(self, "search") else ""
        if q:
            hs = self._haystacks()
            conns = [c for c in conns if q in hs.get(c["pin"], "")]
        cat = dict(_CAT_LABEL).get(self.filter_combo.currentText())
        if cat == "switch":
            conns = [c for c in conns if c["kind"] == "switch"]
        elif cat:
            conns = [c for c in conns if c["category"] == cat]
        s = self.sort_combo.currentText()
        if s == "Category":
            conns.sort(key=lambda c: (self._CAT_ORDER.get(c["category"], 9), c["pin"]))
        elif s == "Destination":
            conns.sort(key=lambda c: (c["dest"], c["pin"]))
        else:
            conns.sort(key=lambda c: c["pin"])
        return conns

    def _clear(self):
        while self._vl.count() > 1:                 # keep the trailing stretch
            w = self._vl.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._rows = {}

    def _rebuild(self, *_):
        self._clear()
        conns = self._records()
        self.count.setText(f"{len(conns)} pin" + ("s" if len(conns) != 1 else ""))
        a = self._authority
        cw = sauth.card_wiring(a) if a else None          # built once, reused per pin
        for rec in conns:
            _c, _k, name, _pc, branches = _pin_branches(a, rec["pin"], cw)
            row = ConnectionRow(rec["pin"], rec["name"], rec["category"], branches)
            row.clicked.connect(self.pinClicked)
            row.set_selected(rec["pin"] == self._sel)
            self._vl.insertWidget(self._vl.count() - 1, row)
            self._rows[rec["pin"]] = row

    def set_selected(self, pos):
        self._sel = pos
        for pin, row in self._rows.items():
            row.set_selected(pin == pos)
        r = self._rows.get(pos)
        if r:
            self._scroll.ensureWidgetVisible(r, 0, 40)

    def _style_scroll(self):
        self._scroll.setStyleSheet(f"#connScroll{{border:none;background:{_PANEL};}}")
        self._inner.setStyleSheet(f"#connInner{{background:{_PANEL};}}")
        self.count.setStyleSheet(f"color:{_MUT};font-size:10px;font-weight:700;")

    def restyle(self):
        self._style_scroll()
        self._rebuild()


class Stm32PinsWidget(QWidget):
    def __init__(self, parent=None, ctx=None):
        super().__init__(parent)
        self.ctx = ctx                          # shell services (ui_shell.TabContext)
        self.db_path = sdb.default_db_path()
        self.source = sdb.default_cubemx_source() or self._saved_source()
        self.authority: dict | None = None
        self._building = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── controls ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Package:"))
        self.pkg_combo = QComboBox()
        self.pkg_combo.addItems(["LQFP64", "LQFP100"])   # vault export set (default)
        self.pkg_combo.currentTextChanged.connect(lambda p: self.load(p))
        self._packages_populated = False
        bar.addWidget(self.pkg_combo)

        self.btn_build = QPushButton("Build Database")
        self.btn_build.setIcon(lucide_icon("wrench", LUCIDE_AMBER))
        self.btn_build.clicked.connect(self.build_database)
        bar.addWidget(self.btn_build)

        self.btn_gen = QPushButton("Export Pin Data")
        self.btn_gen.setIcon(lucide_icon("save", LUCIDE_GREEN))
        self.btn_gen.clicked.connect(self.generate)
        bar.addWidget(self.btn_gen)

        self.btn_vault = QPushButton("Save to Vault")
        self.btn_vault.setIcon(lucide_icon("file-up", LUCIDE_GREEN))
        self.btn_vault.setToolTip("Write the pin data into the Obsidian Brain vault")
        self.btn_vault.clicked.connect(self.generate_to_vault)
        bar.addWidget(self.btn_vault)
        bar.addStretch()

        bar.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        self.view_combo.addItems(["Map", "Table", "Cells"])
        self.view_combo.currentIndexChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        bar.addWidget(self.view_combo)
        root.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("headerStatus")
        root.addWidget(self.status)

        # ── top strip: package identity + compact stat cards (no wall of text) ──
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.pkg_name = QLabel("")
        self.pkg_sub = QLabel("")
        idbox = QVBoxLayout()
        idbox.setContentsMargins(2, 3, 12, 3)
        idbox.setSpacing(0)
        idbox.addWidget(self.pkg_name)
        idbox.addWidget(self.pkg_sub)
        strip.addLayout(idbox)
        self._stats = {}
        for key, label, accent in [
                ("must", "Must-Switch", _SWITCH_COLOR[sdb.SWITCH_MUST]),
                ("osc", "Oscillator", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL]),
                ("fixed", "Fixed", _MUT),
                ("breakout", "Breakout", _BREAKOUT_COLOR),
                ("fivev", "5V-Tolerant", "#24b196"),
                ("io", "Per-Pin I/O", _MUT),
                ("vdda", "VDDA (V)", _MUT)]:
            b = _MiniStat(label, accent)
            self._stats[key] = b
            strip.addWidget(b)
        strip.addStretch()
        root.addLayout(strip)
        self._restyle_strip()

        # ── stacked views: Overview | Table | Connections ──
        self._sel_pos = None
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_overview_page())
        self.stack.addWidget(self._build_table_page())
        self.stack.addWidget(self._build_cells_page())
        root.addWidget(self.stack, 1)

        # ── live file-watch: reload when the DB is rebuilt on disk ──
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_db_changed)
        self._watcher.directoryChanged.connect(self._on_db_changed)
        self._arm_watch()

        self._load_if_ready()

    # ── page builders ───────────────────────────────────────────────
    def _build_overview_page(self):
        """The Map screen: the pin map beside the full connection fabric. Clicking a pin
        on the map highlights and scrolls to it in the fabric; clicking a fabric row
        selects the pin on the map. Both stay in sync."""
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.pin_map = PinMapWidget()             # the primary pin visualizer
        self.pin_map.pinClicked.connect(self._select)
        self.conn_list = ConnectionsList()        # the full socket -> header fabric
        self.conn_list.pinClicked.connect(self._select)
        # compact per-pin detail (roles, rationale, 5V, bootloader, peripherals)
        self.pin_detail = QTextBrowser()
        self.pin_detail.setOpenExternalLinks(False)
        self.pin_detail.setFixedHeight(230)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)
        lv.addWidget(self.pin_map, 1)
        lv.addWidget(self.pin_detail)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.conn_list)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([460, 700])
        lay.addWidget(split, 1)
        return page

    def _build_cells_page(self):
        """The Cells view: package summary, SPI control bus + daisy chain, and one
        channel table per ADG714 cell (spares included)."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.cells_view = QTextBrowser()
        self.cells_view.setOpenExternalLinks(False)
        lay.addWidget(self.cells_view)
        return page

    def _style_browsers(self):
        css = (f"QTextBrowser{{background:{_CARD};color:{_TXT};border:1px solid {_LINE};"
               f"border-radius:10px;padding:10px;}}")
        for wdg in (getattr(self, "pin_detail", None), getattr(self, "cells_view", None)):
            if wdg is not None:
                wdg.setStyleSheet(css)

    def _build_table_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Must-Switch", "Oscillator", "Fixed",
                                    "Breakout", "5V-Tolerant", "Never 5V"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        frow.addWidget(self.filter_combo)
        frow.addWidget(QLabel("Peripheral:"))
        self.periph_combo = QComboBox()
        self.periph_combo.addItem("Any Peripheral")
        self.periph_combo.currentTextChanged.connect(self._on_peripheral)
        frow.addWidget(self.periph_combo)
        frow.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setMaximumWidth(200)
        self.search.textChanged.connect(self._apply_filter)
        frow.addWidget(self.search)
        frow.addStretch()
        for _label, _slot in [("Export CSV", self._export_csv),
                              ("Export MD", self._export_md),
                              ("Copy Lists", self._copy_lists)]:
            _b = QPushButton(_label)
            _b.clicked.connect(_slot)
            frow.addWidget(_b)
        lay.addLayout(frow)
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        # Columns fill the viewport and never scroll horizontally: the short columns
        # size to content, the text columns share the rest and elide with an ellipsis.
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        hdr = self.table.horizontalHeader()
        for i in range(len(_COLS)):
            hdr.setSectionResizeMode(i, QHeaderView.Stretch)
        for i in (0, 1, 8):                     # Pin, Side, VDD (V) — short
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.itemSelectionChanged.connect(self._on_table_select)
        # clicking a row jumps to the Map screen with that pin selected
        self.table.cellClicked.connect(lambda *_: self.view_combo.setCurrentText("Map"))
        lay.addWidget(self.table, 1)
        return page

    # ── selection ───────────────────────────────────────────────────
    def _maps(self):
        """Every pin visualizer, so selection and peripheral highlight stay in sync."""
        return [self.pin_map]

    def _select(self, pos):
        self._sel_pos = pos
        if pos is not None:
            for m in self._maps():
                m.set_selected(pos)
            if getattr(self, "conn_list", None) is not None:
                self.conn_list.set_selected(pos)
            if getattr(self, "pin_detail", None) is not None and self.authority:
                p = next((x for x in self.authority["positions"]
                          if x["position"] == pos), None)
                if p is not None:
                    self.pin_detail.setHtml(_pin_detail_html(p))

    def _on_table_select(self):
        items = self.table.selectedItems()
        if items and self.authority:
            it0 = self.table.item(items[0].row(), 0)
            pos = it0.data(Qt.UserRole) if it0 else None
            if pos is not None:
                self._select(int(pos))

    def apply_theme(self, dark: bool):
        """Follow the app theme: swap the tab's surface colours and refresh the
        custom visuals (stat strip, pin map, connection fabric)."""
        set_tab_theme(dark)
        self._restyle_strip()
        self._style_browsers()
        for m in self._maps():
            m.update()
        if getattr(self, "conn_list", None) is not None:
            self.conn_list.restyle()
        # regenerate themed rich text with the new palette
        if self.authority is not None:
            if getattr(self, "cells_view", None) is not None:
                self.cells_view.setHtml(cells_html(self.authority))
            self._select(self._sel_pos)

    def _restyle_strip(self):
        self.pkg_name.setStyleSheet(f"color:{_TXT};font-size:16px;font-weight:700;")
        self.pkg_sub.setStyleSheet(f"color:{_MUT};font-size:10px;")
        for b in self._stats.values():
            b.restyle()

    # ── data ───────────────────────────────────────────────────────
    def _populate_packages(self):
        """Offer every package the database actually contains (the LQFP64/LQFP100
        pair stays the vault-export set); keeps the current selection."""
        if self._packages_populated or not self.db_path.exists():
            return
        try:
            conn = sdb.connect(self.db_path)
            pkgs = [r[0] for r in conn.execute(
                "SELECT DISTINCT package_name FROM mcu ORDER BY package_name")]
            conn.close()
        except Exception:
            return
        if pkgs:
            cur = self.pkg_combo.currentText()
            self.pkg_combo.blockSignals(True)
            self.pkg_combo.clear()
            self.pkg_combo.addItems(pkgs)
            if cur in pkgs:
                self.pkg_combo.setCurrentText(cur)
            self.pkg_combo.blockSignals(False)
            self._packages_populated = True

    def _load_if_ready(self):
        if self.db_path.exists():
            self._populate_packages()
            self.load(self.pkg_combo.currentText())
        else:
            src = self.source if self.source else "not found"
            self.status.setText(f"No database yet. CubeMX source: {src}. Click 'Build Database'.")

    def _pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select the CubeMX 'mcu' XML folder",
                                             str(self.source or ""))
        if d:
            # remember the choice so the next launch doesn't re-ask
            from PyQt5.QtCore import QSettings
            QSettings("NETDECK", "KiCadManager").setValue("stm32/cubemx_source", d)
        return d or None

    @staticmethod
    def _saved_source():
        from PyQt5.QtCore import QSettings
        saved = QSettings("NETDECK", "KiCadManager").value("stm32/cubemx_source", "")
        return Path(saved) if saved and Path(saved).exists() else None

    def build_database(self):
        src = self.source or self._pick_source()
        if not src:
            return
        self._building = True                       # suppress the file-watcher mid-build
        box = {}

        def work():
            progress = self.ctx.set_progress if self.ctx else None
            box["res"] = sdb.build_database(src, self.db_path, progress=progress)

        def finish(ok: bool):
            self._building = False
            self._arm_watch()
            if not ok or "res" not in box:
                if self.ctx is None:
                    QMessageBox.warning(self, "Build Database",
                                        "Build failed — see the log for details.")
                return
            res = box["res"]
            self.source = src
            self._packages_populated = False
            self._populate_packages()
            lq = ", ".join(f"{k}={v}" for k, v in sorted(res.packages.items())
                           if k.startswith("LQFP"))
            msg = (f"Built {res.mcus} STM32F MCUs, {res.pins} pins, {res.roles} roles "
                   f"from {src}: {lq}")
            self.status.setText(msg)
            if self.ctx:
                self.ctx.log(msg)
            self.load(self.pkg_combo.currentText())

        if self.ctx and self.ctx.run_async:
            # off the GUI thread: the shell drives the status bar + progress
            self.status.setText("Building database from CubeMX XML…")
            self.ctx.run_async(work, "Building STM32 database…",
                               "Database built ✓", done_cb=finish)
            return
        # standalone fallback (tests / headless): synchronous
        self.status.setText("Building database from CubeMX XML…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            work()
            ok = True
        except Exception as e:
            ok = False
            QMessageBox.warning(self, "Build Database", f"Build failed:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()
        finish(ok)

    def load(self, package: str):
        if not self.db_path.exists():
            return
        conn = sdb.connect(self.db_path)
        try:
            self.authority = sauth.build(conn, package)
        except Exception as e:
            QMessageBox.warning(self, "Load", f"Could not read the database:\n{e}")
            return
        finally:
            conn.close()
        self._sel_pos = None
        self._populate_peripherals()
        self._populate()
        for m in self._maps():
            m.set_authority(self.authority)
        if getattr(self, "conn_list", None) is not None:
            self.conn_list.set_authority(self.authority)
        self._style_browsers()
        if getattr(self, "cells_view", None) is not None:
            self.cells_view.setHtml(cells_html(self.authority))
        if getattr(self, "pin_detail", None) is not None:
            self.pin_detail.setHtml(
                f"<p style='color:{_MUT}'>Select a pin for its full detail.</p>")

    def _populate(self):
        a = self.authority
        if not a:
            return
        r = a["rollup"]
        ea = a.get("extraction_access", {})
        el = a.get("electrical", {})
        io = el.get("max_io_current_ma")
        vdda = el.get("vdda_range_v") or el.get("vdd_range_v")
        fv = el.get("five_v_positions", {})
        # top strip: identity + one number per stat card
        self.pkg_name.setText(a["package"])
        self.pkg_sub.setText(f"{a['manifest']['part_count']} parts · {r['positions_total']} pins")
        self._stats["must"].set(r["must_switch_count"])
        self._stats["osc"].set(r["osc_optional_count"])
        self._stats["fixed"].set(r["fixed_count"])
        self._stats["breakout"].set(ea.get("service_breakout_count", 0))
        self._stats["fivev"].set(fv.get("tolerant_all_parts", 0))
        self._stats["io"].set(f"±{io} mA" if io else "—")
        self._stats["vdda"].set(f"{vdda[0]}–{vdda[1]}" if vdda else "—")

        rows = a["positions"]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            sc = p["switch_class"]
            dest = (p["assignment"].get("destination") or p["assignment"].get("net") or "")
            bk = p.get("breakout", {})
            bnets = bk.get("service_nets", [])
            btxt = ", ".join(bnets)
            if bk.get("trace"):
                btxt = (btxt + " · TRACE") if btxt else "TRACE"
            cells = [
                str(p["position"]),                                    # 0 Pin
                p.get("side", "").capitalize(),                        # 1 Side
                _names(p["pin_names"]),                                # 2 Name(s)
                _names(p["role_set"]),                                 # 3 Role Set
                _SWITCH_LABEL.get(sc, sc),                             # 4 Switch
                dest,                                                  # 5 Destination
                ", ".join(p.get("peripherals", [])) or "",            # 6 Peripherals
                btxt or "",                                            # 7 Breakout
                (lambda e: f"{e['vdd_range_v'][0]}–{e['vdd_range_v'][1]}"
                 if e and e.get("vdd_range_v") else "")(p.get("electrical")),  # 8 VDD
            ]
            for c, text in enumerate(cells):
                it = _NumItem(text) if c == 0 else QTableWidgetItem(text)
                if c == 0:
                    it.setData(Qt.UserRole, p["position"])      # numeric sort + row->pin key
                elif c == 4:  # switch class — colour it
                    it.setForeground(QBrush(QColor(_SWITCH_COLOR.get(sc, "#9aa1a9"))))
                elif c == 7 and (bnets or bk.get("trace")):  # breakout — violet
                    it.setForeground(QBrush(QColor(_BREAKOUT_COLOR)))
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.table.clearSelection()

    def _apply_filter(self):
        if not self.authority:
            return
        want = self.filter_combo.currentText()
        q = self.search.text().strip().lower()
        periph = self.periph_combo.currentText()
        periph = None if periph in ("", "Any Peripheral") else periph
        want_class = {
            "Must-Switch": sdb.SWITCH_MUST,
            "Oscillator": sdb.SWITCH_OSC_OPTIONAL,
            "Fixed": sdb.SWITCH_NONE,
        }.get(want)
        by_pos = {p["position"]: p for p in self.authority["positions"]}
        for row in range(self.table.rowCount()):
            it0 = self.table.item(row, 0)
            p = by_pos.get(it0.data(Qt.UserRole)) if it0 else None
            if p is None:
                continue
            fv = p.get("five_v")
            hide = False
            if want_class is not None and p["switch_class"] != want_class:
                hide = True
            elif want == "Breakout" and not p.get("breakout", {}).get("service_nets"):
                hide = True
            elif want == "5V-Tolerant" and not (fv and fv["tolerant"]):
                hide = True
            elif want == "Never 5V" and not (fv and not any(fv["by_family"].values())):
                hide = True
            if periph and periph not in p.get("peripherals", []):
                hide = True
            if q and q not in " ".join(str(v) for v in (
                    p["position"], p["pin_names"], p["role_set"],
                    p["tags"].get("bootloader_periph", []), _tag_summary(p["tags"]),
                    sauth.switch_rationale(p),
                    p.get("breakout", {}).get("service_nets", []),
                    p.get("peripherals", []))).lower():
                hide = True
            self.table.setRowHidden(row, hide)

    def _on_peripheral(self, _name=None):
        if not self.authority:
            return
        name = self.periph_combo.currentText()
        hi = set() if name in ("", "Any Peripheral") else {
            p["position"] for p in self.authority["positions"] if name in p.get("peripherals", [])}
        for m in self._maps():
            m.set_highlight(hi)
        self._apply_filter()

    def _populate_peripherals(self):
        combo = self.periph_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Any Peripheral")
        combo.addItems(sorted({x for p in self.authority["positions"]
                               for x in p.get("peripherals", [])}))
        combo.blockSignals(False)

    def _export_csv(self):
        self._export("csv", sauth.to_csv, "pins")

    def _export_md(self):
        self._export("md", sauth.to_markdown, "authority")

    def _export(self, ext, fn, stem):
        if not self.authority:
            return
        pkg = self.authority["package"]
        path, _sel = QFileDialog.getSaveFileName(
            self, f"Export {ext.upper()}", f"{stem}_{pkg}.{ext}", f"*.{ext}")
        if not path:
            return
        Path(path).write_text(fn(self.authority), encoding="utf-8", newline="\n")
        self.status.setText(f"Exported {Path(path).name}")

    def _copy_lists(self):
        if not self.authority:
            return
        cats = sauth.category_lists(self.authority)
        lines = [f"{self.authority['package']} pin lists (socket #):"]
        for key, lab in [("must_switch", "Must-Switch"), ("osc_optional", "Oscillator (Optional)"),
                         ("fixed", "Fixed"), ("breakout", "Breakout"),
                         ("five_v_all_parts", "5V all-parts"), ("five_v_never", "Never 5V")]:
            nums = cats[key]
            lines.append(f"{lab} ({len(nums)}): " + (", ".join(map(str, nums)) or ""))
        QApplication.clipboard().setText("\n".join(lines))
        self.status.setText("Copied pin lists to clipboard")

    def generate(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate", "Build the database first.")
            return
        out = QFileDialog.getExistingDirectory(self, "Choose output folder for the pin data")
        if not out:
            return
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, __import__("pathlib").Path(out))
                       for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate", f"Generate failed:\n{e}")
            return
        finally:
            conn.close()
        files = [f for w in written for f in w["files"]]
        self.status.setText(f"Wrote {len(files)} files to {out}: " + ", ".join(files))
        try:
            os.startfile(out)  # noqa: S606
        except Exception:
            pass

    # ── detail panel ────────────────────────────────────────────────

    def generate_to_vault(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate → Vault", "Build the database first.")
            return
        vdirs = _vault_authority_dirs()
        if not vdirs:
            out = QFileDialog.getExistingDirectory(self, "Brain vault not found, choose an output folder")
            if not out:
                return
            vdirs = [Path(out)]
        import hashlib

        def _hashes(vdir, names):
            out = {}
            for nm in names:
                f = Path(vdir) / nm
                if f.exists():
                    out[nm] = hashlib.sha256(f.read_bytes()).hexdigest()
            return out

        conn = sdb.connect(self.db_path)
        try:
            # hash what's there so the save can report changed vs unchanged
            probe = [f"pinout_authority_{p}.json" for p in ("LQFP64", "LQFP100")]
            before = {str(v): _hashes(v, probe) for v in vdirs}
            written = [sauth.write_authority(conn, pkg, vdir)
                       for vdir in vdirs for pkg in ("LQFP64", "LQFP100")]
            # Drift gate at save time: a vault copy that contradicts the build
            # cards' claims must never land silently.
            claims_dir = Path(__file__).resolve().parent / "claims"
            claim_files = sorted(claims_dir.glob("claims_*.yaml"))
            lint_ok, lint_lines = (True, [])
            if claim_files:
                lint_ok, lint_lines = sauth.run_lint(conn, claim_files)
            changed = []
            for v in vdirs:
                after = _hashes(v, probe)
                for nm in probe:
                    if before.get(str(v), {}).get(nm) != after.get(nm):
                        changed.append(nm)
            changed = sorted(set(changed))
            # dataset registration page (generated; overwritten on every save)
            self._write_dataset_page(vdirs, written, lint_ok, lint_lines, changed)
        except Exception as e:
            QMessageBox.warning(self, "Generate → Vault", f"Failed:\n{e}")
            return
        finally:
            conn.close()
        n = sum(len(w["files"]) for w in written)
        dests = " and ".join(str(v) for v in vdirs)
        if not lint_ok:
            drift = "\n".join(ln for ln in lint_lines if "DRIFT" in ln)
            QMessageBox.warning(
                self, "Generate → Vault",
                f"Wrote {n} files, but the drift gate found card/authority "
                f"mismatches:\n\n{drift}\n\nFix the build cards or the claims files.")
        what = "no content changes" if not changed else "changed: " + ", ".join(changed)
        msg = (f"Wrote {n} pin-data files to {dests} ({what}). "
               + ("Drift gate: all claims match." if lint_ok else "DRIFT DETECTED — see warning."))
        self.status.setText(msg)
        if self.ctx:
            self.ctx.log(msg)
        try:
            os.startfile(str(vdirs[0]))  # noqa: S606
        except Exception:
            pass

    def _write_dataset_page(self, vdirs, written, lint_ok, lint_lines, changed):
        """The dataset registration page next to the generated files: provenance,
        rollup numbers, the full inventory, and the latest drift-gate verdict.
        Fully generated — safe to overwrite on every save."""
        by_pkg = {}
        for w in written:
            by_pkg.setdefault(w["package"], w)
        if not by_pkg:
            return
        L = ["---", "type: dataset", "generated: true",
             "tool: git/Hardware tools/stm32_authority.py",
             "schema_version: 4", "---", "",
             "# STM32 Pinout Authority", "",
             "Generated pinout-authority collection — the switch fabric, breakouts, and",
             "cell counts are **derived, never hand-authored** (see the Pinout Authority",
             "Generator spec). Build cards cite these files instead of copying pin numbers.",
             "",
             "Homes: `Wiki/Datasets/STM32 Pinout Authority/` and `Brain/data/` "
             "(identical copies, written together by Save to Vault).", ""]
        for pkg, w in sorted(by_pkg.items()):
            r = w["rollup"]
            L += [f"## {pkg}", "",
                  f"- Must-switch pins: **{r['must_switch_count']}** "
                  f"(+{r['osc_optional_count']} oscillator-optional)",
                  f"- Channels: **{r['channel_count']}** — cells: "
                  f"**{r['cells_min']}** minimum / **{r['cells_as_built']}** as built",
                  "- Files: " + ", ".join(f"`{f}`" for f in w["files"]), ""]
        L += ["## Drift Gate", "",
              ("All build-card claims match the authority."
               if lint_ok else "**DRIFT DETECTED** — see below."), ""]
        L += [f"- {ln}" for ln in lint_lines]
        L += ["", "## Last Save", "",
              ("- No content changes vs the previous save." if not changed
               else "- Changed files: " + ", ".join(f"`{c}`" for c in changed)), ""]
        text = "\n".join(L)
        for v in vdirs:
            try:
                (Path(v) / "STM32 Pinout Authority.md").write_text(
                    text, encoding="utf-8", newline="\n")
            except Exception:
                pass

    # ── live file-watch ─────────────────────────────────────────────
    def _arm_watch(self):
        """(Re)watch the DB file + its dir. QFileSystemWatcher drops a path when
        the file is atomically replaced, so this is called again after a build."""
        for p in (str(self.db_path), str(self.db_path.parent)):
            if p not in self._watcher.files() + self._watcher.directories() and Path(p).exists():
                self._watcher.addPath(p)

    def _on_db_changed(self, _path=None):
        if self._building or not self.db_path.exists():
            return
        self._arm_watch()
        if self.authority:
            self.load(self.pkg_combo.currentText())
