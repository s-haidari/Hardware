"""stm32_pins_tab.py — the 'STM32 Pins' tab: build the CubeMX database, view the
per-socket-position switch decision matrix, and generate the pin data.

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Self-contained widget; the main window mounts it as the
third nav tab.
"""
from __future__ import annotations

import html
import json
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

_PANEL = _CARD = _TXT = _MUT = _LINE = _BODY = _CHIP = _FAINT = _ACCENT = ""
# Neutral tones for chrome; the switch-class and net-category HUES come from the
# muted ui_theme.CATEGORY family and are wired into the colour dicts below. Colour
# lives only on pins and net names; chip and card fills stay neutral graphite.
_T_MUST = _T_OSC = _T_FIXED = _T_SEL = ""


_SWITCH_COLOR: dict = {}
_BREAKOUT_COLOR = ""
_CAT_COLOR: dict = {}


def _refresh_tones():
    t = ui_theme.theme()
    global _T_MUST, _T_OSC, _T_FIXED, _T_SEL
    _T_MUST, _T_OSC, _T_FIXED, _T_SEL = t["FG"], t["FG_DIM"], t["DOT_IDLE"], t["ACCENT"]


def _refresh_palette():
    """Rebuild the pin/net colour dicts from the muted CATEGORY family (ui_theme),
    so the pin map, diagram and legend carry switch-class and net-category colour.
    Colour lives ONLY on the pins and the net names; every chip and card background
    stays neutral graphite (never tinted). Needs stm32_db for the class constants."""
    global _SWITCH_COLOR, _BREAKOUT_COLOR, _CAT_COLOR
    c = ui_theme.CATEGORY
    _SWITCH_COLOR = {sdb.SWITCH_MUST: c["must"], sdb.SWITCH_OSC_OPTIONAL: c["osc"],
                     sdb.SWITCH_NONE: c["fixed"]}
    _BREAKOUT_COLOR = c["breakout"]
    _CAT_COLOR = {"power": c["power"], "ground": c["ground"], "core": c["core"],
                  "service": c["service"], "lane": c["lane"], "analog": c["core"]}


def set_tab_theme(dark: bool):
    global _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY, _CHIP, _FAINT, _ACCENT
    t = ui_theme.set_theme(dark)   # publish active theme for the shared kit widgets too
    _PANEL = t["MAIN_BG"]      # bg_raised — the inspector reading surface
    _CARD = t["CARD_BG"]       # bg_inset — the one lift-step (signal path, hover, selection)
    _TXT = t["FG"]             # text_1 primary
    _MUT = t["FG_DIM"]         # text_2 secondary
    _FAINT = t["FG_FAINT"]     # text_3 micro / dormant / units
    _LINE = t["BORDER"]        # hairline (the whole border budget)
    _BODY = t["WIN_BG"]        # bg_base — deepest step
    _CHIP = t["CHIP_BG"]       # legacy neutral chip fill (chips are being retired)
    _ACCENT = t["ACCENT"]      # azure — interaction only
    _refresh_tones()
    try:
        _refresh_palette()     # skipped on the import-time call before stm32_db loads
    except NameError:
        pass


set_tab_theme(False)   # light is the app default

import stm32_db as sdb
import stm32_authority as sauth
_refresh_palette()   # stm32_db now imported — build the grayscale colour dicts

# Icons come from the shared design system (no import back into the shell).
from ui_theme import (lucide_icon, LUCIDE_NEUTRAL, LUCIDE_BLUE,  # noqa: F401
                      LUCIDE_GREEN, LUCIDE_AMBER)


# Scannable columns that fit the viewport without horizontal scrolling. The verbose
# per-pin detail (rationale, ADG714 wiring, tags, bootloader) lives in the focus
# panel beside the table; the CSV/Markdown exports still carry the full column set.
_COLS = ["Pin", "Side", "Pin Name(s)", "Role Set", "Switch",
         "Destination", "Peripherals", "Breakout", "VDD (V)"]

_SWITCH_LABEL = {
    sdb.SWITCH_MUST: "Must-Switch",
    sdb.SWITCH_OSC_OPTIONAL: "Optional Oscillator",
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
    return f"{r[0]} to {r[1]} {unit}" if r else ""


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


def expandNet(s: str) -> str:
    """Un-abbreviate a generated net name for display: VBAT_TGT → VBAT_TARGET,
    SERVICE_OSC_IN → SERVICE_OSCILLATOR_IN."""
    s = (s or "").replace("_TGT", "_TARGET")
    return s.replace("_OSC_IN", "_OSCILLATOR_IN").replace("_OSC_OUT", "_OSCILLATOR_OUT")


_CAT_WORD = {"power": "Power", "analog": "Analog", "ground": "Ground",
             "core": "Core", "service": "Service", "lane": "Card Lane"}


def _pin_detail_rows(p: dict) -> list:
    """(label, value) rows for one pin — Title Case, no redundant rows (delivered net
    + ADG714 wiring live in the signal-path diagram; switch class is in the header),
    un-abbreviated nets. Pure / unit-testable; shared by the native inspector panel
    and the HTML export."""
    fv = p.get("five_v")
    if fv is None:
        fvt = "Not Applicable (non-GPIO)"
    elif fv["tolerant"]:
        fvt = "Yes (Except in Oscillator Mode)" if fv.get("caveat") == "osc-mode" else "Yes"
    elif any(fv["by_family"].values()):
        fvt = "Part-Dependent"
    else:
        fvt = "No"
    bk = p.get("breakout", {})
    bnets = ", ".join(expandNet(n) for n in bk.get("service_nets", [])) or ""
    el = p.get("electrical", {}) or {}
    why = sauth.switch_rationale(p)
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    cat = _CAT_WORD.get(sauth._NET_CATEGORY.get(dest, "lane"), "Card Lane")
    rows = [
        ("Pin Names", _counts(p["pin_names"])),
        ("Roles", _counts(p["role_set"])),
        ("Category", cat),
    ]
    if why:
        rows.append(("Why It Switches", why))
    if p.get("peripherals"):
        rows.append(("Peripherals", ", ".join(p["peripherals"])))
    if bnets or bk.get("trace"):
        _bparts = ([bnets] if bnets else []) + (["Trace"] if bk.get("trace") else [])
        rows.append(("Breakout", " · ".join(_bparts)))
    tagsum = _tag_summary(p["tags"])
    if tagsum:
        rows.append(("Tags", tagsum))
    boot = ", ".join(p["tags"].get("bootloader_periph", []))
    if boot:
        rows.append(("Bootloader", boot))
    rows += [("5 V Tolerant", fvt), ("Supply Voltage", _fmt_rng(el.get("vdd_range_v")))]
    return rows


def _pin_detail_html(p: dict) -> str:
    """HTML rendering of _pin_detail_rows (kept for exports / unit tests)."""
    body = "".join(
        f"<tr><td style='color:{_MUT};padding-right:16px;vertical-align:top;"
        f"white-space:nowrap'>{k}</td><td>{_esc(v)}</td></tr>" for k, v in _pin_detail_rows(p))
    return f"<table cellspacing='0' cellpadding='2'>{body}</table>"




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
        + _row("Optional Oscillator", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL], cats["osc_optional"])
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
        out.append(f"<p><b>One-hot groups:</b> Channels that share a socket pin are "
                   f"mutually exclusive, so firmware closes at most one per pin. "
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


_SVG_FONT = "'Segoe UI Variable Text','Segoe UI',Inter,system-ui,Arial"
_SVG_MONO = "'Cascadia Mono',Consolas,'Geist Mono',monospace"


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
    pcol = _CAT_COLOR.get(conn["category"], _MUT) if conn else _MUT
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


def _pin_chain(a: dict, pos: int, cw: dict = None) -> dict:
    """Structured, refdes-level signal chain for one pin — the source of truth for
    the rebuilt Connections view (schematic chain + Source/Drain ledger). Each row is
    one physical path with the ADG714 Source/Drain terminals, the exact nets on each
    side, and the in-line component (ZIF socket / switch cell / series resistor /
    connector contact). Covers switched AND direct pins."""
    cw = cw or sauth.card_wiring(a)
    conn = next((c for c in sauth.socket_connections(a) if c["pin"] == pos), None)
    p = next((x for x in a["positions"] if x["position"] == pos), None)
    name = next(iter(p["pin_names"]), "") if (p and p["pin_names"]) else ""
    kind = conn["kind"] if conn else "direct"
    socket = cw.get("socket_refdes", "XU_TGT")
    zif = cw.get("zif_socket", "ZIF socket")
    cn = cw.get("connector")
    connector = (cn.get("card") if isinstance(cn, dict) else cn) or "connector"
    series = cw.get("series_r_refdes") or ""      # "" => this card has no lane series R
    series_lbl = f"{series} · 33 Ω" if series else ""
    src_net = f"{socket} Pin {pos} · {name}"

    def _lane_net(v):
        v = v or ""
        return f"CARD_LANE_{pos:03d}" if v == "CARD_LANE" else v

    rows = []
    if kind == "switch":
        chans = [x for x in cw["channels"] if x["socket_pin"] == pos]
        for c in chans:
            contacts = c.get("connector_contacts") or []
            if contacts:
                dvia = f"{connector} · " + " / ".join(_fmt_contact(x) for x in contacts)
            elif c["rail"] == "GND":
                dvia = "Ground Plane · Local Stitching Vias"
            elif series:
                dvia = f"{series} → {_fmt_contact(c.get('lane_contact', ''))}"
            else:
                dvia = f"{connector} · {_fmt_contact(c.get('lane_contact', ''))}"
            rows.append({
                "kind": "switch", "cell": c["cell_refdes"], "channel": c["channel"],
                "s_term": f"{c['s_pin']} · Pin {c['s_pin_num']}",
                "d_term": f"{c['d_pin']} · Pin {c['d_pin_num']}",
                "source_net": src_net, "source_via": zif,
                "drain_net": expandNet(c["rail"]), "drain_via": dvia,
                "drain_cat": sauth._NET_CATEGORY.get(c["rail"], "lane"),
            })
        if chans:
            c0 = chans[0]
            rows.append({
                "kind": "lane", "cell": None, "channel": None, "s_term": None, "d_term": None,
                "source_net": src_net, "source_via": zif, "series": series_lbl or None,
                "drain_net": _lane_net(c0.get("card_lane")),
                "drain_via": f"{connector} · {_fmt_contact(c0.get('lane_contact', ''))}",
                "drain_cat": "lane",
            })
    elif kind == "resistor":
        rows.append({
            "kind": "lane", "cell": None, "s_term": None, "d_term": None,
            "source_net": src_net, "source_via": zif, "series": series_lbl or None,
            "drain_net": _lane_net(conn["dest"]),
            "drain_via": f"{connector} · {_fmt_contact(conn['contact'])}" if conn.get("contact") else connector,
            "drain_cat": "lane",
        })
    else:
        dest = conn["dest"] if conn else ""
        lane = dest == "CARD_LANE"
        rows.append({
            "kind": "direct", "cell": None, "s_term": None, "d_term": None,
            "source_net": src_net, "source_via": zif, "series": None,
            "drain_net": _lane_net(dest),
            "drain_via": (f"{connector} · {_fmt_contact(conn['contact'])}"
                          if (conn and conn.get("contact") and not lane) else connector),
            "drain_cat": conn["category"] if conn else "lane",
        })
    one_hot = sum(1 for r in rows if r["kind"] == "switch") > 1
    return {"pos": pos, "name": name, "kind": kind, "socket": socket, "zif": zif,
            "connector": connector, "series": series, "rows": rows, "one_hot": one_hot}















def _wash(hexcol, amt=0.16):
    """A near-black tint of a category colour, blended over the deepest step — the ONE
    sanctioned filled-chip background (the switch-class chip). Never used elsewhere."""
    base = (_BODY or "#0B0C0E").lstrip("#")
    c = hexcol.lstrip("#")
    b = tuple(int(base[i:i + 2], 16) for i in (0, 2, 4))
    f = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    m = tuple(round(b[i] + (f[i] - b[i]) * amt) for i in range(3))
    return "#%02x%02x%02x" % m










