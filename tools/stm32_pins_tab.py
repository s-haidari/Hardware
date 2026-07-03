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

from PyQt5.QtCore import Qt, QFileSystemWatcher, pyqtSignal, QRectF, QByteArray
from PyQt5.QtGui import QColor, QBrush, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication, QSplitter, QTextEdit, QStackedWidget,
    QFrame, QScrollArea,
)
from PyQt5.QtSvg import QSvgWidget

# Theme-swappable surface colours. The pin/data colours further down are
# theme-independent. set_tab_theme() reassigns these so the custom visuals (pin
# map, stat cards, SVG panels) follow the app theme; the SVG generators and
# paintEvents read these globals at call time, so a swap + refresh is enough.
_PANEL = _CARD = _TXT = _MUT = _LINE = _BODY = ""


def set_tab_theme(dark: bool):
    global _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY
    if dark:
        _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY = \
            "#212124", "#26262b", "#ededf0", "#90909a", "#33333a", "#1c1c1f"
    else:
        _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY = \
            "#f5f5f4", "#ffffff", "#2a2a30", "#70707a", "#e5e5e2", "#eeeeec"


set_tab_theme(False)   # light is the app default

import stm32_db as sdb
import stm32_authority as sauth

try:
    from LibraryManager import (lucide_icon, LUCIDE_NEUTRAL, LUCIDE_BLUE,
                                LUCIDE_GREEN, LUCIDE_AMBER)
    _HAVE_LUCIDE = True
except Exception:  # pragma: no cover
    _HAVE_LUCIDE = False
    LUCIDE_NEUTRAL = LUCIDE_BLUE = LUCIDE_GREEN = LUCIDE_AMBER = ""

    def lucide_icon(*_a, **_k):
        from PyQt5.QtGui import QIcon
        return QIcon()


_COLS = ["Pin", "Side", "Pin Name(s)", "Role Set", "Switch", "Explanation", "Switch cell",
         "Destination", "Peripherals", "Breakout", "Tags", "Bootloader", "VDD (V)"]

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


def _5v_suffix(five_v) -> str:
    """Compact 5V-tolerance token for the Tags cell."""
    if not five_v:
        return ""
    if five_v["tolerant"]:
        return " · 5V-Tolerant" + (" (not as oscillator)" if five_v.get("caveat") == "osc-mode" else "")
    if any(five_v["by_family"].values()):
        return " · 5V (part-dependent)"
    return " · 3.3V only"


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
        fvt = "5V-Tolerant" + (" (except in osc mode)" if fv.get("caveat") == "osc-mode" else "")
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
        + _row("Must-switch", _SWITCH_COLOR[sdb.SWITCH_MUST], cats["must_switch"])
        + _row("Oscillator (optional)", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL], cats["osc_optional"])
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


# ── QFP pin-map geometry (pure — shared by the Qt widget AND the SVG export, so
#    the live widget and any preview render pixel-for-pixel identically) ──────
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
    body = span * 0.62
    plen = span * 0.10
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
         f'<rect x="{bl}" y="{bt}" width="{bw}" height="{bh}" rx="8" fill="#1c1c1f" '
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
                     f'fill="none" stroke="#ffffff" stroke-width="2"/>')
    s.append("</svg>")
    return "".join(s)


_SVG_FONT = "Geist,Inter,'Segoe UI',system-ui,Arial"
_SVG_MONO = "'JetBrains Mono',Consolas,monospace"
_RAIL_COLOR = {
    "VTARGET": "#e5534b", "VBAT_TGT": "#e5534b",                       # power rails, red
    "GND": "#9aa1a9", "VSSA_TGT": "#9aa1a9",                           # ground, grey
    "VDDA_TGT": "#e6a030", "VREF_TGT": "#e6a030",                      # analog supply, amber
    "VCAP_NODE": "#8b6fe8",                                            # core cap, violet
    "SERVICE_BOOT0": "#24b196", "SERVICE_NRST": "#24b196", "SERVICE_OSC_IN": "#24b196",
}


def _rail_color(net):
    return _RAIL_COLOR.get(net, "#8a8f96")


def _contact_str(rail):
    cs = sauth.RAIL_CONTACT.get(rail, [])
    if cs:
        return cs[0] + (f" +{len(cs)-1}" if len(cs) > 1 else "")
    return "GND plane" if rail == "GND" else "local cap"


def fabric_svg(a: dict) -> str:
    """Signal-path diagram: for each channel, the socket pin (IC51 ZIF) on the left, the
    switch cell (S/D) in the middle, and the header connector contact (QSH/QTH) on the
    right, plus the shared control / daisy-chain bus. Wiring per the vault contract."""
    cells = sauth.adg714_cell_map(a)
    r = a["rollup"]
    pinname = {p["position"]: (list(p["pin_names"])[0] if p["pin_names"] else "")
               for p in a["positions"]}
    zif = sauth.ZIF_SOCKET.get(a["package"], "IC51 ZIF socket")
    rails = [sw["destination"] for cell in cells for sw in cell["switches"]
             if not sw["spare"] and sw["destination"]]
    pill_w = 14 + max((len(x) for x in rails), default=6) * 6.2
    colw, gap, chh, top = 476, 22, 30, 62
    cellh = 8 * chh + top
    cols = 1 if len(cells) <= 1 else 2
    rows = -(-len(cells) // cols)
    W = 28 + cols * colw + (cols - 1) * gap + 28
    cm = a.get("card_materials", {})
    foot = cm.get("items", [])
    bus_h, cy0 = 100, 0
    cy0 = 76 + bus_h + 18
    H = cy0 + rows * (cellh + gap) + (46 + len(foot) * 22 if foot else 0)
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="{_SVG_FONT}">',
         f'<rect width="{W}" height="{H}" fill="{_PANEL}"/>',
         f'<text x="28" y="36" fill="{_TXT}" font-size="16" font-weight="700">{a["package"]} switch fabric</text>',
         f'<text x="28" y="58" fill="{_MUT}" font-size="12">'
         f'{r["must_switch_count"]} must-switch pins, {r["channel_count"]} channels, {r["cells_min"]} cells. '
         f'Socket pin  →  switch cell  →  header connector.</text>']
    # control / power bus card
    s.append(f'<rect x="28" y="76" width="{W-56}" height="{bus_h}" rx="12" fill="{_CARD}"/>')
    s.append(f'<text x="44" y="98" fill="{_TXT}" font-size="12" font-weight="700">Control &amp; power bus (shared)</text>')
    s.append(f'<text x="{W-44}" y="98" fill="{_MUT}" font-size="10" text-anchor="end" '
             f'font-family="{_SVG_MONO}">{_esc(zif)}  ·  Samtec QSH-060-01 / QTH-060-03</text>')
    bus = " · ".join(f"{sig} {ct if ct else 'plane'}" for sig, pin, ct, mcu in sauth.ADG714_BUS)
    s.append(f'<text x="44" y="120" fill="{_TXT}" font-size="10.5" font-family="{_SVG_MONO}">{_esc(bus)}</text>')
    s.append(f'<text x="44" y="139" fill="{_MUT}" font-size="10.5">'
             f'Daisy: DIN into cell 1, each DOUT into the next DIN, tail DOUT to LA-13; '
             f'SCLK / SYNC / RESET broadcast to every cell.</text>')
    s.append(f'<text x="44" y="157" fill="{_MUT}" font-size="10">'
             f'S (source) faces the socket, D (drain) faces the rail. LA = left connector, RA = right.</text>')
    for i, cell in enumerate(cells):
        cx = 28 + (i % cols) * (colw + gap)
        cy = cy0 + (i // cols) * (cellh + gap)
        used = sum(1 for sw in cell["switches"] if not sw["spare"])
        s.append(f'<rect x="{cx}" y="{cy}" width="{colw}" height="{cellh}" rx="12" fill="{_CARD}"/>')
        s.append(f'<text x="{cx+16}" y="{cy+26}" fill="{_TXT}" font-size="13" font-weight="700">Switch cell {cell["cell"]}</text>')
        s.append(f'<text x="{cx+colw-16}" y="{cy+26}" fill="{_MUT}" font-size="10" text-anchor="end" '
                 f'font-family="{_SVG_MONO}">{cell["footprint"]} · {used}/8</text>')
        # zone headers
        for zx, zt in ((cx+18, "SOCKET (ZIF)"), (cx+186, "SWITCH"), (cx+296, "HEADER (QSH/QTH)")):
            s.append(f'<text x="{zx}" y="{cy+46}" fill="{_MUT}" font-size="8.5" font-weight="700" '
                     f'letter-spacing="0.4">{zt}</text>')
        y = cy + top
        for sw in cell["switches"]:
            spare = sw["spare"]
            fg = _MUT if spare else _TXT
            # switch zone (middle): SWk + S/D box
            s.append(f'<text x="{cx+186}" y="{y+18}" fill="{_MUT}" font-size="10" '
                     f'font-family="{_SVG_MONO}">SW{sw["channel"]}</text>')
            s.append(f'<rect x="{cx+214}" y="{y+3}" width="46" height="21" rx="6" fill="{_PANEL}" stroke="{_LINE}"/>')
            s.append(f'<text x="{cx+237}" y="{y+18}" fill="{fg}" font-size="10" text-anchor="middle" '
                     f'font-family="{_SVG_MONO}">{sw["s_pin"]}/{sw["d_pin"]}</text>')
            if spare:
                s.append(f'<text x="{cx+18}" y="{y+18}" fill="{_MUT}" font-size="10.5">spare channel</text>')
                y += chh
                continue
            c = _rail_color(sw["destination"])
            # socket zone (left)
            s.append(f'<circle cx="{cx+22}" cy="{y+14}" r="4" fill="{c}"/>')
            s.append(f'<text x="{cx+32}" y="{y+18}" fill="{_TXT}" font-size="10.5" '
                     f'font-family="{_SVG_MONO}">pin {sw["position"]} {_esc(pinname.get(sw["position"],""))}</text>')
            s.append(f'<text x="{cx+176}" y="{y+18}" fill="{_MUT}" font-size="11">→</text>')
            # header zone (right): rail pill + connector contact
            s.append(f'<text x="{cx+264}" y="{y+18}" fill="{_MUT}" font-size="11">→</text>')
            px = cx + 284
            s.append(f'<rect x="{px}" y="{y+3}" width="{pill_w:.0f}" height="21" rx="10.5" fill="{c}" opacity="0.2"/>')
            s.append(f'<text x="{px+pill_w/2:.0f}" y="{y+18}" fill="{c}" text-anchor="middle" '
                     f'font-size="10" font-family="{_SVG_MONO}" font-weight="600">{_esc(sw["destination"])}</text>')
            s.append(f'<text x="{px+pill_w+8:.0f}" y="{y+18}" fill="{_MUT}" font-size="10" '
                     f'font-family="{_SVG_MONO}">{_esc(_contact_str(sw["destination"]))}</text>')
            y += chh
    if foot:
        fy = cy0 + rows * (cellh + gap) + 10
        s.append(f'<text x="28" y="{fy+14}" fill="{_TXT}" font-size="13" font-weight="700">Passive materials</text>')
        for j, it in enumerate(foot):
            iy = fy + 38 + j * 22
            s.append(f'<text x="28" y="{iy}" fill="{_MUT}" font-size="11" font-family="{_SVG_MONO}">{it["qty"]}x</text>')
            s.append(f'<text x="60" y="{iy}" fill="{_TXT}" font-size="11.5">{_esc(it["part"])}</text>')
            s.append(f'<text x="{W-28}" y="{iy}" fill="{_MUT}" font-size="11" text-anchor="end">{_esc(it["role"])}</text>')
    s.append("</svg>")
    return "".join(s)


_CAT_COLOR = {"power": "#e5534b", "analog": "#e6a030", "ground": "#9aa1a9",
              "core": "#8b6fe8", "service": "#24b196", "lane": "#4c8df0"}
_CAT_LABEL = [("All", None), ("Switched", "switch"), ("Power", "power"), ("Analog", "analog"),
              ("Ground", "ground"), ("Core VCAP", "core"), ("Debug & service", "service"),
              ("GPIO lanes", "lane")]


def _sw_glyph(cx, cy, col):
    lx, rx = cx - 20, cx + 20
    return (f'<rect x="{lx-10}" y="{cy-13}" width="{rx-lx+20}" height="26" rx="8" fill="{_CARD}" stroke="{_LINE}"/>'
            f'<circle cx="{lx}" cy="{cy}" r="3.4" fill="none" stroke="{_TXT}" stroke-width="1.6"/>'
            f'<circle cx="{rx}" cy="{cy}" r="2.8" fill="{_TXT}"/>'
            f'<line x1="{lx}" y1="{cy}" x2="{rx}" y2="{cy}" stroke="{col}" stroke-width="2.8" stroke-linecap="round"/>')


def _res_glyph(cx, cy, col):
    d, up = f"M {cx-24} {cy}", True
    for x in (cx-18, cx-11, cx-4, cx+3, cx+10, cx+17):
        d += f" L {x} {cy + (-6 if up else 6)}"
        up = not up
    d += f" L {cx+24} {cy}"
    return (f'<rect x="{cx-32}" y="{cy-13}" width="64" height="26" rx="8" fill="{_CARD}" stroke="{_LINE}"/>'
            f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.9"/>')


def _dir_glyph(cx, cy, col):
    return f'<circle cx="{cx}" cy="{cy}" r="4.5" fill="{_CARD}" stroke="{col}" stroke-width="2.2"/>'


def connections_svg(a: dict, cat=None) -> str:
    """Every socket pin's connection as a row: target socket -> path component
    (switch / resistor / direct) -> parent header, coloured by destination."""
    conns = sauth.socket_connections(a)
    if cat == "switch":
        conns = [c for c in conns if c["kind"] == "switch"]
    elif cat:
        conns = [c for c in conns if c["category"] == cat]
    W, rowh, top = 908, 46, 96
    scx, scw, hdx, hdw = 24, 184, 660, 224
    H = top + max(1, len(conns)) * rowh + 24
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="{_SVG_FONT}">',
         f'<rect width="{W}" height="{H}" fill="{_PANEL}"/>',
         f'<text x="24" y="40" fill="{_TXT}" font-size="16" font-weight="700">Socket Connections</text>',
         f'<text x="24" y="62" fill="{_MUT}" font-size="12">{len(conns)} pins. Each routes to the '
         f'parent through a switch, a series resistor, or a direct link.</text>']
    for x, t, anch in ((scx, "TARGET SOCKET", "start"), ((scx+scw+hdx)/2, "PATH", "middle"),
                       (hdx, "PARENT HEADER", "start")):
        s.append(f'<text x="{x:.0f}" y="{top-14}" fill="#a2a2a8" font-size="9" font-weight="700" '
                 f'letter-spacing="1" text-anchor="{anch}">{t}</text>')
    for i, c in enumerate(conns):
        y = top + i * rowh + rowh / 2
        col = _CAT_COLOR.get(c["category"], "#4c8df0")
        s.append(f'<line x1="{scx+scw}" y1="{y}" x2="{hdx}" y2="{y}" stroke="{col}" stroke-width="2.2"/>')
        s.append(f'<circle cx="{scx+scw}" cy="{y}" r="3" fill="{col}"/><circle cx="{hdx}" cy="{y}" r="3" fill="{col}"/>')
        s.append(f'<rect x="{scx}" y="{y-18}" width="{scw}" height="36" rx="9" fill="{_CARD}"/>'
                 f'<rect x="{scx}" y="{y-18}" width="3.5" height="36" rx="2" fill="{col}"/>')
        s.append(f'<text x="{scx+16}" y="{y+5}" fill="{_TXT}" font-size="13" font-weight="700">Pin {c["pin"]}'
                 f'<tspan dx="9" fill="{_MUT}" font-weight="400" font-size="12">{_esc(c["name"])}</tspan></text>')
        mx = (scx + scw + hdx) / 2
        s.append({"switch": _sw_glyph, "resistor": _res_glyph, "direct": _dir_glyph}[c["kind"]](mx, y, col))
        clbl = {"switch": "SWITCH", "resistor": "33 &#937;", "direct": "DIRECT"}[c["kind"]]
        s.append(f'<text x="{mx:.0f}" y="{y-16}" fill="#a2a2a8" font-size="8" font-weight="700" '
                 f'letter-spacing="0.6" text-anchor="middle">{clbl}</text>')
        s.append(f'<rect x="{hdx}" y="{y-18}" width="{hdw}" height="36" rx="9" fill="{_CARD}"/>'
                 f'<rect x="{hdx+hdw-3.5}" y="{y-18}" width="3.5" height="36" rx="2" fill="{col}"/>')
        s.append(f'<text x="{hdx+16}" y="{y+5}" fill="{col}" font-size="12.5" font-weight="700">{_esc(c["dest"])}'
                 f'<tspan dx="9" fill="{_MUT}" font-weight="400" font-size="11">{_esc(c["contact"])}</tspan></text>')
    s.append("</svg>")
    return "".join(s)


_ROLE_COLOR = {"VBAT": "#e5534b", "VDD": "#e5534b", "VSS": "#9aa1a9", "VDDA": "#e6a030",
               "VREF": "#e6a030", "VCAP": "#8b6fe8", "BOOT": "#24b196", "OSC": "#24b196",
               "NRST": "#24b196", "IO": "#4c8df0"}


def _role_color(role):
    return _ROLE_COLOR.get(str(role), "#4c8df0")


def _svg_chip(x, y, label, color, filled=False, mono=False, h=22):
    """A rounded chip. Returns (svg, width)."""
    ff = _SVG_MONO if mono else _SVG_FONT
    w = 14 + len(str(label)) * (6.9 if mono else 6.4)
    if filled:
        rect = f'<rect x="{x:.0f}" y="{y}" width="{w:.0f}" height="{h}" rx="{h/2}" fill="{color}"/>'
        tc = "#161618"
    else:
        rect = (f'<rect x="{x:.0f}" y="{y}" width="{w:.0f}" height="{h}" rx="{h/2}" fill="{color}" '
                f'fill-opacity="0.15" stroke="{color}" stroke-opacity="0.45"/>')
        tc = color
    txt = (f'<text x="{x+w/2:.0f}" y="{y+h*0.68:.0f}" fill="{tc}" text-anchor="middle" '
           f'font-size="11" font-family="{ff}" font-weight="600">{_esc(label)}</text>')
    return rect + txt, w


def detail_svg(a: dict, pos=None) -> str:
    """Visual detail panel: a package summary with pin-number chips when no pin is
    selected, or one pin's identities, switch channels, rationale, breakout, 5V, and
    peripherals as chips."""
    W, pad = 372, 18
    body, y = [], 30

    def section(label):
        nonlocal y
        body.append(f'<text x="{pad}" y="{y}" fill="{_MUT}" font-size="10" font-weight="700" '
                    f'letter-spacing="0.5">{label}</text>')
        y += 20

    def chips(items):
        """Uniform-width chips laid out in a grid (every bubble the same length)."""
        nonlocal y
        if not items:
            return
        cw = min(max(16 + len(str(lab)) * 6.4 for lab, _, _ in items), W - 2 * pad)
        per = max(1, int((W - 2 * pad + 7) / (cw + 7)))
        for i, (lab, col, fill) in enumerate(items):
            c = i % per
            if c == 0 and i:
                y += 28
            cx = pad + c * (cw + 7)
            if fill:
                body.append(f'<rect x="{cx:.0f}" y="{y-15}" width="{cw:.0f}" height="22" rx="11" fill="{col}"/>')
                tc = "#161618"
            else:
                body.append(f'<rect x="{cx:.0f}" y="{y-15}" width="{cw:.0f}" height="22" rx="11" '
                            f'fill="{col}" fill-opacity="0.15" stroke="{col}" stroke-opacity="0.45"/>')
                tc = col
            body.append(f'<text x="{cx+cw/2:.0f}" y="{y}" fill="{tc}" text-anchor="middle" '
                        f'font-size="11" font-family="{_SVG_FONT}" font-weight="600">{_esc(lab)}</text>')
        y += 30

    if pos is None:
        r = a["rollup"]
        cats = sauth.category_lists(a)
        body.append(f'<text x="{pad}" y="{y}" fill="{_TXT}" font-size="16" font-weight="700">'
                    f'{a["package"]}</text>')
        y += 21
        body.append(f'<text x="{pad}" y="{y}" fill="{_MUT}" font-size="11.5">'
                    f'{a["manifest"]["part_count"]} parts, {r["positions_total"]} positions. '
                    f'{r["must_switch_count"]} pins switch.</text>')
        y += 26
        for label, col, nums in [("MUST-SWITCH", _SWITCH_COLOR[sdb.SWITCH_MUST], cats["must_switch"]),
                                 ("OSCILLATOR", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL], cats["osc_optional"]),
                                 ("BREAKOUT", _BREAKOUT_COLOR, cats["breakout"]),
                                 ("5V-TOLERANT", "#24b196", cats["five_v_all_parts"]),
                                 ("NEVER 5V", _MUT, cats["five_v_never"])]:
            section(f"{label} ({len(nums)})")
            if not nums:
                body.append(f'<text x="{pad}" y="{y}" fill="{_MUT}" font-size="11">None.</text>')
                y += 24
                continue
            cx = pad
            for n in nums:
                if cx + 30 > W - pad:
                    cx = pad
                    y += 26
                body.append(f'<rect x="{cx}" y="{y-15}" width="26" height="20" rx="6" '
                            f'fill="{col}" fill-opacity="0.16"/>'
                            f'<text x="{cx+13}" y="{y}" fill="{col}" text-anchor="middle" '
                            f'font-size="10.5" font-family="{_SVG_MONO}">{n}</text>')
                cx += 30
            y += 30
    else:
        p = next((x for x in a["positions"] if x["position"] == pos), None)
        if p is None:
            return detail_svg(a, None)
        sc = p["switch_class"]
        tagc = {sdb.SWITCH_MUST: _SWITCH_COLOR[sdb.SWITCH_MUST],
                sdb.SWITCH_OSC_OPTIONAL: _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL],
                sdb.SWITCH_NONE: _MUT}[sc]
        tag = {sdb.SWITCH_MUST: "Must-Switch", sdb.SWITCH_OSC_OPTIONAL: "Oscillator",
               sdb.SWITCH_NONE: "Fixed"}[sc]
        body.append(f'<text x="{pad}" y="{y}" fill="{_TXT}" font-size="19" font-weight="700">Pin {pos}</text>')
        body.append(f'<text x="{pad + 44 + len(str(pos)) * 12}" y="{y}" fill="{_MUT}" '
                    f'font-size="11.5">{p.get("side", "").capitalize()}</text>')
        tw = 14 + len(tag) * 6.4
        ch, _w = _svg_chip(W - pad - tw, y - 16, tag, tagc, filled=True)
        body.append(ch)
        y += 26
        section("IDENTITIES")
        chips([(k, _role_color(k), False) for k in p["role_set"].keys()])
        section("SWITCH")
        chans = p["assignment"].get("channels", [])
        if chans:
            for cdef in chans:
                c = _rail_color(cdef["destination"])
                pill = cdef["destination"]
                pw = 14 + len(pill) * 6.2
                body.append(f'<text x="{pad}" y="{y}" fill="{_MUT}" font-size="10" '
                            f'font-family="{_SVG_MONO}">SW{cdef["channel"]}</text>')
                body.append(f'<circle cx="{pad+42}" cy="{y-4}" r="4" fill="{c}"/>')
                body.append(f'<line x1="{pad+50}" y1="{y-4}" x2="{W-pad-pw-6:.0f}" y2="{y-4}" '
                            f'stroke="{c}" stroke-width="1.3" opacity="0.5"/>')
                body.append(f'<rect x="{W-pad-pw:.0f}" y="{y-15}" width="{pw:.0f}" height="20" rx="10" '
                            f'fill="{c}" fill-opacity="0.2"/>'
                            f'<text x="{W-pad-pw/2:.0f}" y="{y}" fill="{c}" text-anchor="middle" '
                            f'font-size="10" font-family="{_SVG_MONO}" font-weight="600">{_esc(pill)}</text>')
                y += 26
            y += 4
        else:
            dest = p["assignment"].get("net") or p["assignment"].get("destination") or "the application net"
            body.append(f'<text x="{pad}" y="{y}" fill="{_TXT}" font-size="11.5">'
                        f'Direct connection to {_esc(dest)}. No switch.</text>')
            y += 28
        why = sauth.switch_rationale(p)
        if why:
            lines, line = [], ""
            for wd in why.split():
                if len(line) + len(wd) > 44:
                    lines.append(line)
                    line = wd
                else:
                    line = (line + " " + wd).strip()
            lines.append(line)
            bh = 24 + len(lines) * 15
            body.append(f'<rect x="{pad}" y="{y-6}" width="{W-2*pad}" height="{bh}" rx="8" '
                        f'fill="{_CARD}" stroke="{_LINE}"/>')
            body.append(f'<text x="{pad+10}" y="{y+9}" fill="{_MUT}" font-size="9.5" '
                        f'font-weight="700">EXPLANATION</text>')
            for i, ln in enumerate(lines):
                body.append(f'<text x="{pad+10}" y="{y+26+i*15}" fill="{_TXT}" font-size="11">{_esc(ln)}</text>')
            y += bh + 12
        bk = p.get("breakout", {})
        bnets = list(bk.get("service_nets", []))
        if bnets or bk.get("trace"):
            section("BREAKOUT")
            items = [(n, _BREAKOUT_COLOR, False) for n in bnets]
            if bk.get("trace"):
                items.append(("Parallel trace", _BREAKOUT_COLOR, False))
            chips(items)
        fv = p.get("five_v")
        if fv is not None:
            section("5V TOLERANCE")
            if fv["tolerant"]:
                chips([("5V-Tolerant", "#24b196", True)])
            elif any(fv["by_family"].values()):
                chips([("Part-dependent", "#24b196", False)])
            else:
                chips([("3.3V only", _MUT, False)])
        if p.get("peripherals"):
            section("PERIPHERALS")
            chips([(pp, _MUT, False) for pp in p["peripherals"]])
        el = p.get("electrical") or {}
        if el.get("vdd_range_v"):
            section("ELECTRICAL")
            vd = el["vdd_range_v"]
            body.append(f'<text x="{pad}" y="{y}" fill="{_TXT}" font-size="11.5">'
                        f'VDD {vd[0]}–{vd[1]} V. Per-pin I/O up to {el.get("max_io_current_ma", "?")} mA.</text>')
            y += 24
    H = y + 14
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H:.0f}" '
            f'font-family="{_SVG_FONT}"><rect width="{W}" height="{H:.0f}" fill="{_PANEL}"/>')
    return head + "".join(body) + "</svg>"


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
        self.highlight = set()
        self.setMinimumSize(380, 380)

    def set_authority(self, a):
        self.authority = a
        self.selected = None
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

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(_PANEL))
        g = self._geom()
        if not g or not g["pins"]:
            qp.setPen(QColor(_MUT))
            qp.drawText(self.rect(), Qt.AlignCenter, "Build the database to see the pin map")
            return
        bl, bt, bw, bh = g["body"]
        qp.setPen(QPen(QColor(_LINE), 1.5))
        qp.setBrush(QColor(_BODY))
        qp.drawRoundedRect(QRectF(bl, bt, bw, bh), 8, 8)
        qp.setPen(QColor(_MUT))
        qp.drawText(QRectF(bl, bt, bw, bh), Qt.AlignCenter, self.authority["package"])
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(_SWITCH_COLOR.get(pin["sw"], "#9aa1a9")))
            qp.drawRect(QRectF(x, y, pw, ph))
            if pin["breakout"]:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_BREAKOUT_COLOR), 2))
                qp.drawRect(QRectF(x - 1.5, y - 1.5, pw + 3, ph + 3))
            if pin["pos"] in self.highlight:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor("#24b196"), 2.5))
                qp.drawRect(QRectF(x - 3.5, y - 3.5, pw + 7, ph + 7))
            if pin["pos"] == self.selected:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_TXT), 2))
                qp.drawRect(QRectF(x - 3, y - 3, pw + 6, ph + 6))

    def mousePressEvent(self, ev):
        g = self._geom()
        if not g:
            return
        px, py = ev.x(), ev.y()
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            if x - 3 <= px <= x + pw + 3 and y - 3 <= py <= y + ph + 3:
                self.selected = pin["pos"]
                self.update()
                self.pinClicked.emit(pin["pos"])
                return


class _StatCard(QFrame):
    """Compact dashboard stat card: title, big value, sub-line, coloured left bar."""
    def __init__(self, title, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self._accent = accent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(1)
        self._t = QLabel(title)
        self._b = QLabel("")
        self._s = QLabel("")
        for w in (self._t, self._b, self._s):
            lay.addWidget(w)
        self.restyle()

    def restyle(self):
        self.setStyleSheet(f"#statCard{{background:{_CARD};border-radius:10px;"
                           f"border-left:4px solid {self._accent};}}")
        self._t.setStyleSheet(f"color:{_MUT};font-size:10px;font-weight:700;")
        self._b.setStyleSheet(f"color:{_TXT};font-size:19px;font-weight:700;")
        self._s.setStyleSheet(f"color:{_MUT};font-size:11px;")

    def set(self, big, sub):
        self._b.setText(str(big))
        self._s.setText(str(sub))


class Stm32PinsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path = sdb.default_db_path()
        self.source = sdb.default_cubemx_source()
        self.authority: dict | None = None
        self._building = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── controls ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Package:"))
        self.pkg_combo = QComboBox()
        self.pkg_combo.addItems(["LQFP64", "LQFP100"])
        self.pkg_combo.currentTextChanged.connect(lambda p: self.load(p))
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
        self.view_combo.addItems(["Pin map", "Table", "Connections"])
        self.view_combo.currentIndexChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        bar.addWidget(self.view_combo)
        root.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("headerStatus")
        root.addWidget(self.status)
        self.rollup = QLabel("")
        self.rollup.setWordWrap(True)
        self.rollup.setTextFormat(Qt.RichText)
        root.addWidget(self.rollup)

        # ── stacked views: Pin map (dashboard) | Table | Card BOM ──
        self._sel_pos = None
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard_page())
        self.stack.addWidget(self._build_table_page())
        self.stack.addWidget(self._build_bom_page())
        root.addWidget(self.stack, 1)

        # ── live file-watch: reload when the DB is rebuilt on disk ──
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_db_changed)
        self._watcher.directoryChanged.connect(self._on_db_changed)
        self._arm_watch()

        self._load_if_ready()

    # ── page builders ───────────────────────────────────────────────
    def _build_dashboard_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        col.setMaximumWidth(240)
        col.setMinimumWidth(206)
        self.sc_switch = _StatCard("SWITCH FABRIC", _SWITCH_COLOR[sdb.SWITCH_MUST])
        self.sc_break = _StatCard("BREAKOUT", _BREAKOUT_COLOR)
        self.sc_5v = _StatCard("5V-TOLERANCE", "#24b196")
        self.sc_elec = _StatCard("ELECTRICAL", "#e6a030")
        for c in (self.sc_switch, self.sc_break, self.sc_5v, self.sc_elec):
            cl.addWidget(c)
        cl.addStretch()
        lay.addWidget(col)
        self.pin_map = PinMapWidget()
        self.pin_map.pinClicked.connect(self._select)
        lay.addWidget(self.pin_map, 2)
        self.map_detail = QSvgWidget()
        self._mda = QScrollArea()
        self._mda.setWidgetResizable(False)
        self._mda.setWidget(self.map_detail)
        self._mda.setMinimumWidth(300)
        self._mda.setMaximumWidth(410)
        self._mda.setStyleSheet("QScrollArea{border:none;background:%s;}" % _PANEL)
        lay.addWidget(self._mda, 1)
        return page

    def _build_table_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Must Switch", "Oscillator", "Fixed",
                                    "Breakout", "5V-Tolerant", "Never 5V"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        frow.addWidget(self.filter_combo)
        frow.addWidget(QLabel("Peripheral:"))
        self.periph_combo = QComboBox()
        self.periph_combo.addItem("Any peripheral")
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
        hdr = self.table.horizontalHeader()
        for i in range(len(_COLS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.itemSelectionChanged.connect(self._on_table_select)
        self.detail = QSvgWidget()
        self.detail_area = QScrollArea()
        self.detail_area.setWidgetResizable(False)
        self.detail_area.setWidget(self.detail)
        self.detail_area.setStyleSheet("QScrollArea{border:none;background:%s;}" % _PANEL)
        # a pin visualizer beside the table: selecting a row lights up its pin here
        self.table_pin_map = PinMapWidget()
        self.table_pin_map.pinClicked.connect(self._select)
        self.table_pin_map.setMinimumHeight(220)
        rightcol = QSplitter(Qt.Vertical)
        rightcol.addWidget(self.table_pin_map)
        rightcol.addWidget(self.detail_area)
        rightcol.setStretchFactor(0, 2)
        rightcol.setStretchFactor(1, 3)
        rightcol.setMinimumWidth(300)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(rightcol)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        return page

    def _build_bom_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Show:"))
        self.conn_combo = QComboBox()
        self.conn_combo.addItems([lbl for lbl, _ in _CAT_LABEL])
        self.conn_combo.currentTextChanged.connect(self._on_conn_filter)
        frow.addWidget(self.conn_combo)
        frow.addStretch()
        lay.addLayout(frow)
        self.bom_svg = QSvgWidget()
        self._bom_area = QScrollArea()
        self._bom_area.setWidgetResizable(False)
        self._bom_area.setWidget(self.bom_svg)
        self._bom_area.setStyleSheet("QScrollArea{border:none;background:%s;}" % _PANEL)
        lay.addWidget(self._bom_area)
        return page

    def _on_conn_filter(self, _t=None):
        if self.authority:
            cat = dict(_CAT_LABEL).get(self.conn_combo.currentText())
            self._load_svg(self.bom_svg, connections_svg(self.authority, cat))

    # ── selection + dashboard ───────────────────────────────────────
    def _maps(self):
        """Both pin visualizers (dashboard + table view) so selection/highlight
        stay in sync across views."""
        ms = [self.pin_map]
        if getattr(self, "table_pin_map", None) is not None:
            ms.append(self.table_pin_map)
        return ms

    def _select(self, pos):
        self._sel_pos = pos
        if pos is not None:
            for m in self._maps():
                m.set_selected(pos)
        self._refresh_details()

    def _on_table_select(self):
        items = self.table.selectedItems()
        if items and self.authority:
            it0 = self.table.item(items[0].row(), 0)
            pos = it0.data(Qt.UserRole) if it0 else None
            if pos is not None:
                self._select(int(pos))

    def _refresh_details(self):
        if not self.authority:
            return
        svg = detail_svg(self.authority, self._sel_pos)
        self._load_svg(self.map_detail, svg)
        self._load_svg(self.detail, svg)

    @staticmethod
    def _load_svg(widget, svg):
        widget.load(QByteArray(svg.encode("utf-8")))
        widget.setFixedSize(widget.renderer().defaultSize())

    def apply_theme(self, dark: bool):
        """Follow the app theme: swap the tab's surface colours and refresh the
        custom visuals (stat cards, scroll areas, pin maps, SVG panels)."""
        set_tab_theme(dark)
        for sc in (self.sc_switch, self.sc_break, self.sc_5v, self.sc_elec):
            sc.restyle()
        for area in (self._mda, self.detail_area, self._bom_area):
            area.setStyleSheet("QScrollArea{border:none;background:%s;}" % _PANEL)
        for m in self._maps():
            m.update()
        if self.authority:
            self._update_dashboard()
            self._refresh_details()

    def _update_dashboard(self):
        a = self.authority
        if not a:
            return
        r, ea, el = a["rollup"], a["extraction_access"], a["electrical"]
        fv = el.get("five_v_positions", {})
        vdda = el.get("vdda_range_v")
        self.sc_switch.set(f"{r['must_switch_count']}",
                           f"Must-switch · {r['osc_optional_count']} oscillator · {r['fixed_count']} fixed")
        self.sc_break.set(f"{ea.get('service_breakout_count', 0)} nets",
                          f"{len(ea.get('debug_positions', []))} debug · "
                          f"{len(ea.get('trace_positions', []))} trace")
        self.sc_5v.set(f"{fv.get('tolerant_all_parts', 0)} 5V-Tolerant",
                       f"{fv.get('family_dependent', 0)} part-dependent · "
                       f"{fv.get('not_tolerant_any_part', 0)} never")
        self.sc_elec.set(f"±{el.get('max_io_current_ma', '?')} mA I/O",
                         f"VDDA {vdda[0]}–{vdda[1]} V · VCAP {el.get('vcap_required')}" if vdda else "")
        cat = dict(_CAT_LABEL).get(self.conn_combo.currentText()) if hasattr(self, "conn_combo") else None
        self._load_svg(self.bom_svg, connections_svg(a, cat))

    # ── data ───────────────────────────────────────────────────────
    def _load_if_ready(self):
        if self.db_path.exists():
            self.load(self.pkg_combo.currentText())
        else:
            src = self.source if self.source else "not found"
            self.status.setText(f"No database yet. CubeMX source: {src}. Click 'Build Database'.")

    def _pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select the CubeMX 'mcu' XML folder",
                                             str(self.source or ""))
        return d or None

    def build_database(self):
        src = self.source or self._pick_source()
        if not src:
            return
        self.status.setText("Building database from CubeMX XML…")
        self._building = True                       # suppress the file-watcher mid-build
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            res = sdb.build_database(src, self.db_path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._building = False
            QMessageBox.warning(self, "Build Database", f"Build failed:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._building = False
        self._arm_watch()
        self.source = src
        lq = ", ".join(f"{k}={v}" for k, v in sorted(res.packages.items()) if k.startswith("LQFP"))
        self.status.setText(f"Built {res.mcus} STM32F MCUs, {res.pins} pins, {res.roles} roles "
                            f"from {src}: {lq}")
        self.load(self.pkg_combo.currentText())

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
        self._update_dashboard()
        self._refresh_details()

    def _populate(self):
        a = self.authority
        if not a:
            return
        r = a["rollup"]
        ea = a.get("extraction_access", {})
        el = a.get("electrical", {})
        io, inj = el.get("max_io_current_ma"), el.get("injection_current_ma")
        vdda = el.get("vdda_range_v") or el.get("vdd_range_v")
        fv = el.get("five_v_positions", {})
        lab = f"color:{_MUT};font-weight:600"
        line1 = (f"<b>{a['package']}</b> · {a['manifest']['part_count']} parts · "
                 f"{r['positions_total']} positions")
        line2 = (f"<span style='{lab}'>Switch</span> "
                 f"must {r['must_switch_count']} · oscillator {r['osc_optional_count']} · "
                 f"fixed {r['fixed_count']}")
        line3 = (f"<span style='{lab}'>Breakout</span> {ea.get('service_breakout_count', 0)} "
                 f"({len(ea.get('debug_positions', []))} debug · "
                 f"{len(ea.get('trace_positions', []))} trace)")
        parts = []
        if io and vdda:
            parts.append(f"I/O ±{io} mA · injection ±{inj} mA · VDDA {vdda[0]}–{vdda[1]} V")
            if fv:
                parts.append(f"5V-Tolerant {fv.get('tolerant_all_parts', 0)} all / "
                             f"{fv.get('family_dependent', 0)} part-dependent / "
                             f"{fv.get('not_tolerant_any_part', 0)} none")
        line4 = (f"<span style='{lab}'>Power</span> " + " · ".join(parts)) if parts else ""
        self.rollup.setText(line1 + "<br>" + line2 + "<br>" + line3
                            + (("<br>" + line4) if line4 else ""))

        rows = a["positions"]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            sc = p["switch_class"]
            adg = p["assignment"].get("adg714")
            if adg:
                s_pin, d_pin = sauth.ADG714_SWITCH_PINS[adg["channel"]]
                adg_txt = f"cell {adg['cell']} · SW{adg['channel']} ({s_pin}/{d_pin})"
            else:
                adg_txt = ""
            dest = (p["assignment"].get("destination") or p["assignment"].get("net") or "")
            bk = p.get("breakout", {})
            bnets = bk.get("service_nets", [])
            btxt = ", ".join(bnets)
            if bk.get("trace"):
                btxt = (btxt + " · TRACE") if btxt else "TRACE"
            cells = [
                str(p["position"]),                                    # 0 Pin
                p.get("side", "").capitalize(),                                     # 1 Side
                _names(p["pin_names"]),                                # 2 Name(s)
                _names(p["role_set"]),                                 # 3 Role Set
                _SWITCH_LABEL.get(sc, sc),                             # 4 Switch
                sauth.switch_rationale(p) or "",                      # 5 Why
                adg_txt,                                               # 6 ADG714
                dest,                                                  # 7 Destination
                ", ".join(p.get("peripherals", [])) or "",           # 8 Peripherals
                btxt or "",                                           # 9 Breakout
                _tag_summary(p["tags"]) + _5v_suffix(p.get("five_v")),  # 10 Tags
                ", ".join(p["tags"].get("bootloader_periph", [])),     # 11 Bootloader
                (lambda e: f"{e['vdd_range_v'][0]}–{e['vdd_range_v'][1]}"
                 if e and e.get("vdd_range_v") else "")(p.get("electrical")),  # 12 V(dd)
            ]
            for c, text in enumerate(cells):
                it = _NumItem(text) if c == 0 else QTableWidgetItem(text)
                if c == 0:
                    it.setData(Qt.UserRole, p["position"])      # numeric sort + row->pin key
                elif c == 4:  # switch class — colour it
                    it.setForeground(QBrush(QColor(_SWITCH_COLOR.get(sc, "#9aa1a9"))))
                elif c == 9 and (bnets or bk.get("trace")):  # breakout — violet
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
        periph = None if periph in ("", "Any peripheral") else periph
        want_class = {
            "Must Switch": sdb.SWITCH_MUST,
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
        hi = set() if name in ("", "Any peripheral") else {
            p["position"] for p in self.authority["positions"] if name in p.get("peripherals", [])}
        for m in self._maps():
            m.set_highlight(hi)
        self._apply_filter()

    def _populate_peripherals(self):
        combo = self.periph_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Any peripheral")
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
        for key, lab in [("must_switch", "Must-switch"), ("osc_optional", "Osc-optional"),
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
        vdir = _default_vault_authority_dir()
        if vdir is None:
            out = QFileDialog.getExistingDirectory(self, "Brain vault not found, choose an output folder")
            if not out:
                return
            vdir = Path(out)
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, vdir) for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate → Vault", f"Failed:\n{e}")
            return
        finally:
            conn.close()
        n = sum(len(w["files"]) for w in written)
        self.status.setText(f"Wrote {n} pin-data files into the vault: {vdir}")
        try:
            os.startfile(str(vdir))  # noqa: S606
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
