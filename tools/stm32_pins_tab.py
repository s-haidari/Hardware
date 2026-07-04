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
from PyQt5.QtGui import (QColor, QBrush, QPainter, QPen, QFont, QFontMetricsF,
                         QPainterPath)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication, QSplitter, QStackedWidget,
    QFrame, QScrollArea, QTextBrowser, QGridLayout,
)

# Theme-swappable surface colours, derived from the shared design system
# (tools/ui_theme.py) so this tab can never drift from the shell's palette.
# The pin/data colours further down are theme-independent. set_tab_theme()
# reassigns these; the SVG generators and paintEvents read them at call time,
# so a swap + refresh is enough.
import ui_theme
import ui_widgets as uw

_PANEL = _CARD = _TXT = _MUT = _LINE = _BODY = ""
# Grayscale luminance ramp — the ONLY encoding of the switch axis (must-switch is
# the whole point of the board, so it carries the most light; fixed pins recede).
# No categorical hues anywhere: net category is shown as an inline TEXT tag. Tones
# follow the active theme, so they invert correctly between graphite and paper.
_T_MUST = _T_OSC = _T_FIXED = _T_SEL = ""


def _refresh_tones():
    t = ui_theme.theme()
    global _T_MUST, _T_OSC, _T_FIXED, _T_SEL
    _T_MUST, _T_OSC, _T_FIXED, _T_SEL = t["FG"], t["FG_DIM"], t["DOT_IDLE"], t["ACCENT"]


def set_tab_theme(dark: bool):
    global _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY
    t = ui_theme.set_theme(dark)   # publish active theme for the shared kit widgets too
    _PANEL = t["MAIN_BG"]      # panel background
    _CARD = t["CARD_BG"]       # card surfaces
    _TXT = t["FG"]             # primary text
    _MUT = t["FG_DIM"]         # muted text
    _LINE = t["BORDER"]        # hairlines
    _BODY = t["IN_BG"]         # the package body fill
    _refresh_tones()


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

# All type colour comes from the one shared categorical palette (ui_theme.CATEGORY).
_BREAKOUT_COLOR = ui_theme.cat("breakout")

_SWITCH_COLOR = {
    sdb.SWITCH_MUST: ui_theme.cat("must"),
    sdb.SWITCH_OSC_OPTIONAL: ui_theme.cat("osc"),
    sdb.SWITCH_NONE: ui_theme.cat("fixed"),
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


def _pin_search_haystack(p: dict) -> str:
    """Lowercase searchable text for one socket position, mirroring the columns the
    Table view actually shows: pin number, pin name(s), role set, the *Switch* label
    and the *Destination* net (both visible but previously un-indexed), plus breakout
    service nets, peripherals, tags, switch rationale and bootloader periph. Pure —
    unit-testable. Mirrors ConnectionsList._haystacks so typing a visible destination
    (VTARGET, CARD_LANE_042) or a switch label finds its pin instead of '0 pins'."""
    sc = p["switch_class"]
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    parts = (
        p["position"], p["pin_names"], p["role_set"],
        _SWITCH_LABEL.get(sc, sc),                      # the visible Switch column
        dest,                                           # the visible Destination column
        p["tags"].get("bootloader_periph", []), _tag_summary(p["tags"]),
        sauth.switch_rationale(p),
        p.get("breakout", {}).get("service_nets", []),
        p.get("peripherals", []),
    )
    return " ".join(str(v) for v in parts).lower()


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
        adg_t = (f"Cell {adg['cell']} · Channel {adg['channel']} "
                 f"({s_pin} Pin {sauth.ADG714_TERMINAL_PIN[s_pin]} → "
                 f"{d_pin} Pin {sauth.ADG714_TERMINAL_PIN[d_pin]})")
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


_CAT_COLOR = {"power": ui_theme.cat("power"), "analog": ui_theme.cat("osc"),
              "ground": ui_theme.cat("ground"), "core": ui_theme.cat("core"),
              "service": ui_theme.cat("service"), "lane": ui_theme.cat("lane")}
_CAT_LABEL = [("All", None), ("Switched", "switch"), ("Power", "power"), ("Analog", "analog"),
              ("Ground", "ground"), ("Core VCAP", "core"), ("Debug & Service", "service"),
              ("GPIO Lanes", "lane")]


def _fmt_contact(c: str) -> str:
    """'LA-33' -> 'J_CARD1_LA 33' — the full parent-receptacle identity."""
    if c.startswith("LA-"):
        return f"J_CARD1_LA {c[3:]}"
    if c.startswith("RA-"):
        return f"J_CARD1_RA {c[3:]}"
    return c


def _pin_branches(a: dict, pos: int, cw: dict = None):
    """Every physical path of one socket pin, at refdes-level specificity (the
    vault's Cards 7B/7C wiring). Returns (conn, kind, name, pcol, branches);
    each branch is a dict of aligned table cells:
      caption   — SWITCHED ROLE / IO LANE / DIRECT
      frm/frm2  — socket endpoint: refdes + pin
      via/via2  — the component in the path: switch cell channel with Source/
                  Drain terminal pins, the 33 R series resistor, or nothing
      to/to2    — the delivered net + its parent-receptacle contact
      color     — the destination's net-category colour
    Pass a precomputed card_wiring() as cw to avoid rebuilding it per pin."""
    conn = next((c for c in sauth.socket_connections(a) if c["pin"] == pos), None)
    p = next((x for x in a["positions"] if x["position"] == pos), None)
    kind = conn["kind"] if conn else "direct"
    name = next(iter(p["pin_names"]), "") if (p and p["pin_names"]) else ""
    pcol = _CAT_COLOR.get(conn["category"], "#4c8df0") if conn else _MUT
    cw = cw or sauth.card_wiring(a)
    branches = []
    if kind == "switch":
        chans = [x for x in cw["channels"] if x["socket_pin"] == pos]
        many = len(chans) > 1                 # mutually-exclusive branches (one-hot)
        for bi, c in enumerate(chans, start=1):
            to2 = (" / ".join(_fmt_contact(x) for x in c["connector_contacts"])
                   if c["connector_contacts"]
                   else ("Ground Plane" if c["rail"] == "GND" else "Local 2.2 µF Cap"))
            branches.append({
                "caption": f"SWITCHED ROLE {bi}/{len(chans)}" if many else "SWITCHED ROLE",
                "via": f"{c['cell_refdes']} · Channel {c['channel']}",
                "via2": f"Source {c['s_pin']} Pin {c['s_pin_num']} → Drain {c['d_pin']} Pin {c['d_pin_num']}",
                "to": c["rail"], "to2": to2,
                "color": _CAT_COLOR.get(sauth._NET_CATEGORY.get(c["rail"], "lane"), pcol),
            })
        if chans:
            c0 = chans[0]
            rname = cw.get("series_r_refdes", "")
            branches.append({
                "caption": "DEFAULT IO LANE",
                "via": f"{rname} · 33 Ω Series" if rname else "Direct Route",
                "via2": "" if rname else "No series resistor on this card",
                "to": c0["card_lane"],
                "to2": _fmt_contact(c0["lane_contact"]) if c0.get("lane_contact") else "Lane Row",
                "color": _CAT_COLOR["lane"],
            })
    elif kind == "resistor":
        rname = cw.get("series_r_refdes", "R_IO_LANE")
        branches.append({
            "caption": "IO LANE",
            "via": f"{rname} · 33 Ω Series", "via2": "",
            "to": conn["dest"], "to2": _fmt_contact(conn["contact"]),
            "color": _CAT_COLOR["lane"],
        })
    else:
        lane_dest = conn and conn["dest"] == "CARD_LANE"
        branches.append({
            "caption": "DIRECT",
            "via": "Direct Route", "via2": "",
            "to": conn["dest"] if conn else "",
            "to2": (_fmt_contact(conn["contact"])
                    if (conn and conn["contact"] and not lane_dest) else
                    ("Lane Row" if lane_dest else "Hardwired")),
            "color": pcol,
        })
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
                qp.setPen(QPen(QColor(ui_theme.cat("fivev")), 2.5))
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



class ConnectionDiagram(QWidget):
    """The selected pin's rails, drawn as signal flow: the ZIF socket on the left,
    then one branch per physical path — through an ADG714 switch cell (with its
    Source/Drain terminal pins) or a 33 Ω series resistor — to the delivered net,
    which is coloured by type and labelled with its connector contact. Replaces the
    dense text fabric: you read a pin's whole story at a glance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._a = None
        self._pos = None
        self._cw = None
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, a, pos, cw):
        self._a, self._pos, self._cw = a, pos, cw
        self.updateGeometry()
        self.update()

    def _branches(self):
        if not self._a or self._pos is None:
            return None
        return _pin_branches(self._a, self._pos, self._cw)

    def sizeHint(self):
        from PyQt5.QtCore import QSize
        b = self._branches()
        n = len(b[4]) if b else 1
        return QSize(680, 24 + n * 56 + (n - 1) * 18 + 22)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(_PANEL))
        info = self._branches()
        if info is None:
            p.setPen(QColor(_MUT))
            f = QFont(_SVG_FONT.split(",")[0]); f.setPointSizeF(10.5)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Select a pin on the map to see its connections")
            return
        conn, kind, name, pcol, branches = info
        socket_ref = (self._cw or {}).get("socket_refdes", "J_SOCKET")

        pad = 6
        NH, GAP = 56, 18
        n = len(branches)
        top = 8
        sockH = n * NH + (n - 1) * GAP
        sockX, sockW = pad, 156
        viaX, viaW = sockX + sockW + 34, 236
        sockCY = top + sockH / 2

        cap_f = QFont(_SVG_MONO.split(",")[0].strip("'")); cap_f.setPointSizeF(6.8)
        main_f = QFont(_SVG_FONT.split(",")[0]); main_f.setPointSizeF(9.5)
        big_f = QFont(_SVG_MONO.split(",")[0].strip("'")); big_f.setPointSizeF(13.0); big_f.setWeight(QFont.DemiBold)
        net_f = QFont(_SVG_MONO.split(",")[0].strip("'")); net_f.setPointSizeF(10.0); net_f.setWeight(QFont.DemiBold)
        sub_f = QFont(_SVG_MONO.split(",")[0].strip("'")); sub_f.setPointSizeF(7.6)

        def node(x, y, w, h, stroke):
            p.setPen(QPen(QColor(stroke), 1))
            p.setBrush(QColor(_CARD))
            p.drawRoundedRect(QRectF(x, y, w, h), 5, 5)

        def label(x, y, w, font, color, text, elide=True):
            p.setFont(font); p.setPen(QColor(color))
            fm = QFontMetricsF(font)
            t = fm.elidedText(text, Qt.ElideRight, w) if elide else text
            p.drawText(QRectF(x, y, w, 16), Qt.AlignLeft | Qt.AlignVCenter, t)

        def wire(path, color):
            p.setPen(QPen(QColor(color), 1.4))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        def dot(x, y, color):
            p.setPen(Qt.NoPen); p.setBrush(QColor(color))
            p.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))

        # socket node (spans all branches); a left accent in the pin's class colour
        # ties it back to the pin map.
        pd = next((x for x in self._a["positions"] if x["position"] == self._pos), None)
        cls_col = _SWITCH_COLOR.get(pd["switch_class"], _MUT) if pd else _MUT
        node(sockX, top, sockW, sockH, _LINE)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(cls_col))
        p.drawRoundedRect(QRectF(sockX, top + 6, 3, sockH - 12), 1.5, 1.5)
        label(sockX + 12, top + 8, sockW - 20, cap_f, _MUT, socket_ref)
        p.setFont(big_f); p.setPen(QColor(_TXT))
        p.drawText(QRectF(sockX + 12, sockCY - 14, sockW - 20, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, f"Pin {self._pos}")
        label(sockX + 12, top + sockH - 22, sockW - 20, sub_f, _MUT, name or "")

        for i, br in enumerate(branches):
            ry = top + i * (NH + GAP)
            rmid = ry + NH / 2
            col = br.get("color") or pcol
            has_via = bool(br.get("via")) and br["via"] != "Direct Route"
            # elbow wire from socket to this branch row
            jx = sockX + sockW + 20
            path = QPainterPath()
            path.moveTo(sockX + sockW, sockCY)
            path.lineTo(sockX + sockW + 11, sockCY)
            path.lineTo(sockX + sockW + 11, rmid)
            path.lineTo(jx, rmid)
            wire(path, col)
            dot(sockX + sockW, sockCY, _MUT)

            if has_via:
                node(viaX, ry, viaW, NH, _LINE)
                label(viaX + 12, ry + 9, viaW - 22, cap_f, _MUT, br["caption"])
                label(viaX + 12, ry + 25, viaW - 22, main_f, _TXT, br["via"])
                if br.get("via2"):
                    label(viaX + 12, ry + 40, viaW - 22, sub_f, _MUT, br["via2"])
                dx = viaX + viaW + 30
                dw = W - pad - dx
                lp = QPainterPath(); lp.moveTo(viaX + viaW, rmid); lp.lineTo(dx, rmid)
                wire(lp, col)
                dot(viaX + viaW, rmid, col)
            else:
                dx = jx + 40
                dw = W - pad - dx
                lp = QPainterPath(); lp.moveTo(jx, rmid); lp.lineTo(dx, rmid)
                wire(lp, col)
                dot(dx, rmid, col)

            # destination node (type-coloured)
            node(dx, ry, dw, NH, col)
            dcap = "Delivered Rail" if br["caption"].startswith("SWITCHED") else \
                   ("Lane Row" if "LANE" in br["caption"] else "Service Net")
            label(dx + 12, ry + 9, dw - 22, cap_f, _MUT, dcap)
            p.setFont(net_f); p.setPen(QColor(col))
            fm = QFontMetricsF(net_f)
            p.drawText(QRectF(dx + 12, ry + 24, dw - 22, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(br["to"], Qt.ElideRight, dw - 22))
            label(dx + 12, ry + 40, dw - 22, sub_f, _MUT, br.get("to2") or "")

        if n > 1 and any(b["caption"].startswith("SWITCHED") for b in branches):
            p.setFont(sub_f); p.setPen(QColor(_MUT))
            p.drawText(QRectF(pad, top + sockH + 4, W - 2 * pad, 16),
                       Qt.AlignLeft,
                       "◇  mutually exclusive — one branch closed at a time (firmware one-hot)")


class ConnectionRow(QFrame):
    """One pin of the wiring table: pin + name in a fixed left column, then one
    aligned row per physical path — FROM (socket refdes · pin), VIA (switch cell
    channel with Source/Drain terminals, or the series resistor), TO (net) and
    the parent-receptacle CONTACT right-aligned. Fixed column widths keep every
    row in the fabric lined up like a datasheet table; the category colour lives
    only in the left bar and the destination net."""
    clicked = pyqtSignal(int)
    W_PIN, W_KIND, W_VIA, W_TO = 64, 104, 330, 150

    def __init__(self, pin, name, category, branches, parent=None):
        super().__init__(parent)
        self.pin = pin
        self._name = name
        self._category = category
        self._selected = False
        self.setObjectName("connRow")
        self.setCursor(Qt.PointingHandCursor)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 6, 12, 7)
        outer.setSpacing(10)
        # left column: pin number (mono) over name
        idbox = QVBoxLayout()
        idbox.setSpacing(0)
        self._pin_lbl = QLabel(str(pin))
        pf = QFont(_SVG_MONO.split(",")[0].strip("'"))
        pf.setPointSizeF(10.5)
        pf.setWeight(QFont.DemiBold)
        self._pin_lbl.setFont(pf)
        self._name_lbl = QLabel(name)
        nf = QFont(_SVG_FONT.split(",")[0])
        nf.setPointSizeF(8.5)
        nf.setBold(True)
        self._name_lbl.setFont(nf)
        idbox.addWidget(self._pin_lbl)
        idbox.addWidget(self._name_lbl)
        idbox.addStretch()
        idw = QWidget()
        idw.setLayout(idbox)
        idw.setFixedWidth(self.W_PIN)
        outer.addWidget(idw)
        # right column: one aligned grid row per path
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(3)
        grid.setColumnMinimumWidth(0, self.W_KIND)
        grid.setColumnMinimumWidth(1, self.W_VIA)
        grid.setColumnMinimumWidth(2, self.W_TO)
        grid.setColumnStretch(3, 1)
        self._cells = []                      # (label, role) for restyle
        for r, br in enumerate(branches):
            self._add_cell(grid, r, 0, br["caption"], "kind")
            via = br["via"] + (f"   ({br['via2']})" if br["via2"] else "")
            self._add_cell(grid, r, 1, via, "via")
            self._add_cell(grid, r, 2, br["to"], "net", color=br["color"])
            self._add_cell(grid, r, 3, br["to2"], "contact")
        gw = QWidget()
        gw.setLayout(grid)
        outer.addWidget(gw, 1)
        self.restyle()

    def _add_cell(self, grid, r, c, text, role, color=None):
        lbl = QLabel(text)
        if role in ("mono", "contact", "net"):
            f = QFont(_SVG_MONO.split(",")[0].strip("'"))
            f.setPointSizeF(8.5)
            if role == "net":
                f.setWeight(QFont.DemiBold)
            lbl.setFont(f)
        elif role == "kind":
            f = QFont(_SVG_FONT.split(",")[0])
            f.setPointSizeF(7.0)
            f.setBold(True)
            f.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
            lbl.setFont(f)
        else:
            f = QFont(_SVG_FONT.split(",")[0])
            f.setPointSizeF(8.5)
            lbl.setFont(f)
        if role == "contact":
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(lbl, r, c)
        self._cells.append((lbl, role, color))
        return lbl

    def restyle(self):
        col = _CAT_COLOR.get(self._category, "#4c8df0")
        bg = _CARD if self._selected else "transparent"
        self.setStyleSheet(
            f"#connRow{{background:{bg};border:none;"
            f"border-left:3px solid {col};border-bottom:1px solid {_LINE};}}"
            f"#connRow:hover{{background:{_CARD};}}")
        self._pin_lbl.setStyleSheet(f"color:{_MUT};")
        self._name_lbl.setStyleSheet(f"color:{_TXT};")
        for lbl, role, ccol in self._cells:
            if role == "kind":
                lbl.setStyleSheet(f"color:{'#a2a2a8' if not self._selected else _TXT};")
            elif role == "net":
                lbl.setStyleSheet(f"color:{ccol or col};")
            elif role in ("contact", "via"):
                lbl.setStyleSheet(f"color:{_MUT};")
            else:
                lbl.setStyleSheet(f"color:{_TXT};")

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
        # the physical chain, stated ONCE for the whole table (refdes level)
        self.chain = QLabel("")
        cf = QFont(_SVG_MONO.split(",")[0].strip("'"))
        cf.setPointSizeF(8.0)
        self.chain.setFont(cf)
        root.addWidget(self.chain)
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
        if a is not None:
            cw = sauth.card_wiring(a)
            lane_note = ("every pin owns CARD_LANE_(pin) via R_IO_LANE 33 Ω"
                         if cw["lane_policy"] == "by_pin"
                         else "numbered lanes are switch-only; other pins route direct")
            self.chain.setText(
                f"{cw['socket_refdes']} ({cw['zif_socket']})  →  "
                f"{cw['edge_refdes']} ({cw['connector']['card']})  →  "
                f"J_CARD1_LA / J_CARD1_RA ({cw['connector']['parent']})   ·   {lane_note}")
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
        if hasattr(self, "chain"):
            self.chain.setStyleSheet(f"color:{_MUT};")
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
        self._loaded_package: str | None = None   # last package that loaded cleanly
        self._building = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ── toolbar: package selector on the left, actions on the right ──
        bar = uw.toolbar_row()
        pkg_lbl = QLabel("Package")
        pkg_lbl.setStyleSheet("font-weight:600;")
        bar.addWidget(pkg_lbl)
        self.pkg_combo = QComboBox()
        self.pkg_combo.addItems(["LQFP64", "LQFP100"])   # vault export set (default)
        self.pkg_combo.currentTextChanged.connect(lambda p: self.load(p))
        self._packages_populated = False
        bar.addWidget(self.pkg_combo)
        bar.addStretch()
        self.btn_build = uw.button("Build Database", "default", lucide_icon("wrench", LUCIDE_AMBER))
        self.btn_build.clicked.connect(self.build_database)
        bar.addWidget(self.btn_build)
        self.btn_gen = uw.button("Export Pin Data", "default", lucide_icon("save", LUCIDE_GREEN))
        self.btn_gen.clicked.connect(self.generate)
        bar.addWidget(self.btn_gen)
        self.btn_vault = uw.button("Save to Vault", "primary", lucide_icon("file-up", LUCIDE_GREEN))
        self.btn_vault.setToolTip("Write the pin data into the Obsidian Brain vault")
        self.btn_vault.clicked.connect(self.generate_to_vault)
        bar.addWidget(self.btn_vault)
        root.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("headerStatus")
        root.addWidget(self.status)

        # ── readout band (shared instrument fascia) ──
        self.readout = uw.ReadoutBand([
            ("must", "Must-Switch", _SWITCH_COLOR[sdb.SWITCH_MUST]),
            ("osc", "Oscillator", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL]),
            ("fixed", "Fixed", None),
            ("breakout", "Breakout", _BREAKOUT_COLOR),
            ("fivev", "5V-Tolerant", ui_theme.cat("fivev")),
            ("io", "Per-Pin I/O", None),
            ("vdda", "VDDA (V)", None),
        ])
        root.addWidget(self.readout)

        # ── body: left rail (views) beside the stacked view ──
        self._sel_pos = None
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_overview_page())
        self.stack.addWidget(self._build_table_page())
        self.stack.addWidget(self._build_cells_page())
        self._view_index = {"map": 0, "table": 1, "cells": 2}

        self.rail = uw.Rail(150)
        self.rail.add_group("View")
        self.rail.add_item("map", "Map")
        self.rail.add_item("table", "Table")
        self.rail.add_item("cells", "Cells")
        self.rail.selected.connect(lambda k: self.stack.setCurrentIndex(self._view_index[k]))
        self._railwrap = QWidget()
        rwl = QVBoxLayout(self._railwrap)
        rwl.setContentsMargins(0, 0, 12, 0)
        rwl.setSpacing(0)
        rwl.addWidget(self.rail)
        rwl.addStretch(1)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._railwrap)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self._restyle_strip()

        # ── live file-watch: reload when the DB is rebuilt on disk ──
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_db_changed)
        self._watcher.directoryChanged.connect(self._on_db_changed)
        self._arm_watch()

        self._load_if_ready()

    # ── page builders ───────────────────────────────────────────────
    def _build_overview_page(self):
        """The Map screen is a pin inspector: the QFP pin map on the left selects a
        pin; the right pane draws that pin's rails as a signal-flow diagram and lists
        its key facts. Clicking a pin (map) drives both."""
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.pin_map = PinMapWidget()
        self.pin_map.pinClicked.connect(self._select)

        # left column: the map over a colour key (fills the column, teaches the code)
        left = QWidget()
        lc = QVBoxLayout(left)
        lc.setContentsMargins(0, 0, 12, 0)
        lc.setSpacing(8)
        lc.addWidget(self.pin_map, 1)
        lc.addWidget(uw.SectionHeader("Legend"))
        lc.addWidget(self._pin_legend())

        # ── right: inspector for the selected pin ──
        insp = QWidget()
        iv = QVBoxLayout(insp)
        iv.setContentsMargins(18, 0, 4, 0)
        iv.setSpacing(8)
        self.insp_header = QLabel("Select a pin")
        self.insp_header.setTextFormat(Qt.RichText)
        iv.addWidget(self.insp_header)
        iv.addWidget(uw.SectionHeader("Connections"))
        self.diagram = ConnectionDiagram()
        iv.addWidget(self.diagram)
        iv.addWidget(uw.SectionHeader("Detail"))
        self.pin_detail = QTextBrowser()
        self.pin_detail.setOpenExternalLinks(False)
        self.pin_detail.setMinimumHeight(150)
        iv.addWidget(self.pin_detail, 1)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(insp)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 4)
        split.setSizes([440, 720])
        lay.addWidget(split, 1)

        # keep a hidden fabric model so search/select helpers still resolve
        self.conn_list = ConnectionsList()
        self.conn_list.hide()
        self.conn_list.pinClicked.connect(self._select)
        return page

    def _pin_legend(self):
        """A compact colour key for the pin/net-type palette."""
        items = [
            ("must", "Must-switch"), ("osc", "Oscillator"), ("fixed", "Fixed"),
            ("breakout", "Breakout"), ("fivev", "5V-tolerant"),
            ("power", "Power rail"), ("ground", "Ground"), ("lane", "IO lane"),
            ("core", "Core cap"), ("service", "Service"),
        ]
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(2, 0, 2, 0)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(5)
        self._legend_dots = []
        for i, (key, label) in enumerate(items):
            row, col = divmod(i, 2)
            cell = QHBoxLayout()
            cell.setSpacing(7)
            dot = QFrame()
            dot.setFixedSize(9, 9)
            dot.setStyleSheet(f"background:{ui_theme.cat(key)};border-radius:4px;")
            self._legend_dots.append(dot)
            lbl = QLabel(label)
            lbl.setFont(QFont(_SVG_FONT.split(",")[0], 8))
            cell.addWidget(dot)
            cell.addWidget(lbl)
            cell.addStretch(1)
            holder = QWidget()
            holder.setLayout(cell)
            g.addWidget(holder, row, col)
        return w

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
        # flat content (de-boxed): no frame, panel ground, explicit UI font so the
        # shell's mono QTextEdit rule doesn't leak in.
        css = (f"QTextBrowser{{background:transparent;color:{_TXT};border:none;"
               f"padding:2px 2px 8px;"
               f"font-family:'Geist','Inter','Segoe UI';font-size:9pt;}}")
        for wdg in (getattr(self, "pin_detail", None), getattr(self, "cells_view", None)):
            if wdg is not None:
                wdg.setStyleSheet(css)
        if getattr(self, "insp_header", None) is not None:
            self.insp_header.setStyleSheet("background:transparent;")

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
        self.table.cellClicked.connect(lambda *_: self.rail.select("map"))
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
            # keep the Table view's row in lock-step with map/diagram clicks. Block
            # the table's signals so selectRow() doesn't recurse back through
            # _on_table_select -> _select. Resolve the row via col-0's UserRole
            # (the pin key) so it's correct even after a numeric re-sort.
            tbl = getattr(self, "table", None)
            if tbl is not None:
                target = next(
                    (r for r in range(tbl.rowCount())
                     if tbl.item(r, 0) is not None
                     and tbl.item(r, 0).data(Qt.UserRole) == pos), None)
                if target is not None:
                    prev = tbl.blockSignals(True)
                    tbl.selectRow(target)
                    tbl.scrollToItem(tbl.item(target, 0),
                                     QAbstractItemView.PositionAtCenter)
                    tbl.blockSignals(prev)
            if getattr(self, "diagram", None) is not None and self.authority:
                self.diagram.set_data(self.authority, pos, self._cw())
            if self.authority:
                p = next((x for x in self.authority["positions"]
                          if x["position"] == pos), None)
                if p is not None:
                    if getattr(self, "insp_header", None) is not None:
                        self.insp_header.setText(self._inspector_header(p))
                    if getattr(self, "pin_detail", None) is not None:
                        self.pin_detail.setHtml(_pin_detail_html(p))

    def _cw(self):
        """Cached card_wiring for the current authority (built once per load)."""
        if getattr(self, "_cw_cache", None) is None and self.authority:
            self._cw_cache = sauth.card_wiring(self.authority)
        return self._cw_cache

    def _inspector_header(self, p):
        name = next(iter(p["pin_names"]), "") if p["pin_names"] else ""
        cls = p["switch_class"]
        col = _SWITCH_COLOR.get(cls, _MUT)
        clabel = _SWITCH_LABEL.get(cls, cls)
        side = p.get("side", "")
        return (f"<span style='font-family:\"JetBrains Mono\";font-size:15pt;"
                f"font-weight:600;color:{_TXT}'>Pin {p['position']}</span>"
                f"<span style='font-size:13pt;color:{_TXT}'>&nbsp;&nbsp;{html.escape(name)}</span>"
                f"<span style='color:{_MUT};font-size:9pt'>&nbsp;&nbsp;{side}</span>"
                f"<span style='color:{col};font-size:9pt'>"
                f"&nbsp;&nbsp;&nbsp;● {clabel}</span>")

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
        if getattr(self, "diagram", None) is not None:
            self.diagram.update()
        # regenerate themed rich text with the new palette
        if self.authority is not None:
            if getattr(self, "cells_view", None) is not None:
                self.cells_view.setHtml(cells_html(self.authority))
            if self._sel_pos is not None:
                self._select(self._sel_pos)

    def _restyle_strip(self):
        self.readout.restyle()
        self._railwrap.setStyleSheet(
            f"background:transparent;border-right:1px solid {_LINE};")
        self.rail.restyle()

    # ── data ───────────────────────────────────────────────────────
    def _populate_packages(self):
        """Offer every package the database actually contains (the LQFP64/LQFP100
        pair stays the vault-export set); keeps the current selection."""
        if self._packages_populated or not self.db_path.exists():
            return
        conn = None
        try:
            conn = sdb.connect(self.db_path)
            pkgs = [r[0] for r in conn.execute(
                "SELECT DISTINCT package_name FROM mcu ORDER BY package_name")]
        except Exception:
            return
        finally:
            if conn is not None:
                conn.close()
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

    def _reset_views(self):
        """Clear every view to its empty state. Used when a package load fails and
        there's no prior good package to fall back to, so stale pins never linger
        under a package label they no longer belong to."""
        self.authority = None
        self._sel_pos = None
        self._cw_cache = None
        if getattr(self, "table", None) is not None:
            self.table.setRowCount(0)
        for m in self._maps():
            m.set_authority(None)
        if getattr(self, "conn_list", None) is not None:
            self.conn_list.set_authority(None)
        if getattr(self, "cells_view", None) is not None:
            self.cells_view.setHtml("")
        if getattr(self, "diagram", None) is not None:
            self.diagram.set_data(None, None, None)
        if getattr(self, "insp_header", None) is not None:
            self.insp_header.setText(
                f"<span style='color:{_MUT};font-size:11pt'>No package loaded</span>")
        if getattr(self, "pin_detail", None) is not None:
            self.pin_detail.setHtml("")

    def _revert_package_or_clear(self):
        """After a failed load, keep the UI self-consistent. Prefer reverting the
        package selector to the last package that loaded cleanly (whose views are
        still on screen); only if nothing ever loaded do we blank the views. Combo
        signals are blocked so the revert doesn't kick off another load."""
        prev = getattr(self, "_loaded_package", None)
        idx = self.pkg_combo.findText(prev) if prev else -1
        if idx >= 0:
            self.pkg_combo.blockSignals(True)
            self.pkg_combo.setCurrentIndex(idx)
            self.pkg_combo.blockSignals(False)
        else:
            self._reset_views()

    def load(self, package: str):
        if not self.db_path.exists():
            return
        conn = sdb.connect(self.db_path)
        try:
            self.authority = sauth.build(conn, package)
        except Exception as e:
            QMessageBox.warning(self, "Load", f"Could not read the database:\n{e}")
            self._revert_package_or_clear()
            return
        finally:
            conn.close()
        self._loaded_package = package
        self._sel_pos = None
        self._cw_cache = None
        self._populate_peripherals()
        self._populate()
        for m in self._maps():
            m.set_authority(self.authority)
        if getattr(self, "conn_list", None) is not None:
            self.conn_list.set_authority(self.authority)
        self._style_browsers()
        if getattr(self, "cells_view", None) is not None:
            self.cells_view.setHtml(cells_html(self.authority))
        if getattr(self, "diagram", None) is not None:
            self.diagram.set_data(None, None, None)
        if getattr(self, "insp_header", None) is not None:
            self.insp_header.setText(
                f"<span style='color:{_MUT};font-size:11pt'>Select a pin on the map</span>")
        if getattr(self, "pin_detail", None) is not None:
            self.pin_detail.setHtml(
                f"<p style='color:{_MUT}'>Select a pin for its full detail.</p>")
        # auto-select the first must-switch pin so the inspector isn't empty
        first = next((p["position"] for p in self.authority["positions"]
                      if p["switch_class"] == sdb.SWITCH_MUST), None)
        if first is not None:
            self._select(first)

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
        self.readout.set_identity(
            a["package"], f"{a['manifest']['part_count']} parts · {r['positions_total']} pins")
        self.readout.set("must", r["must_switch_count"])
        self.readout.set("osc", r["osc_optional_count"])
        self.readout.set("fixed", r["fixed_count"])
        self.readout.set("breakout", ea.get("service_breakout_count", 0))
        self.readout.set("fivev", fv.get("tolerant_all_parts", 0))
        self.readout.set("io", f"±{io} mA" if io else "—")
        self.readout.set("vdda", f"{vdda[0]}–{vdda[1]}" if vdda else "—")

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
            if q and q not in _pin_search_haystack(p):
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
        if not path.lower().endswith(f".{ext.lower()}"):
            path += f".{ext}"
        # Guard the render + write: a locked target (e.g. the file is open in Excel)
        # raises PermissionError; unguarded it would tear down the whole app. Report
        # it in a dialog and leave the tab alive.
        try:
            Path(path).write_text(fn(self.authority), encoding="utf-8", newline="\n")
        except Exception as e:
            QMessageBox.warning(
                self, f"Export {ext.upper()}",
                f"Could not write {Path(path).name}:\n{e}\n\n"
                "If the file is open in another program (for example Excel), "
                "close it and try again.")
            return
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
            written = [sauth.write_authority(conn, pkg, Path(out))
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
