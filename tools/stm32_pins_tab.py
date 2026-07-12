"""stm32_pins_tab.py — pure, unit-testable STM32 pin helpers (no widget lives here).

This module holds only stateless helper functions consumed by the bench UI and
the authority layer; it defines no QWidget, mounts no nav tab, and paints no
canvas. The actual 'STM32 Pins' surface (nav-tab mount, build-DB action,
paintEvents) lives in tools/ui/features/bench.py, which imports this file as
`import stm32_pins_tab as pins  # pure helpers`.

Provides: pin-map geometry (pin_map_geometry) and its SVG (pin_map_svg),
per-pin detail rows (_pin_detail_rows), the signal-chain builder (_pin_chain),
net-name expansion (expandNet), and the theme palette (set_tab_theme + the
module colour dicts).

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Consumed by tools/ui/features/bench.py, bench_visuals.py,
and tools/stm32_authority.py.
"""
from __future__ import annotations

import html

# Theme-swappable surface colours, derived from the shared design system
# (ui.theme — the one source of truth) so this tab can never drift from the shell's
# palette. The pin/data colours further down are theme-independent. set_tab_theme()
# reassigns these; the SVG generators and paintEvents read them at call time,
# so a swap + refresh is enough. Colours resolve to OPAQUE #rrggbb via T.opaque()
# because they interpolate straight into SVG fill=/stroke= (a raw rgba token would
# void the attribute).
from ui import theme as _T

_PANEL = _CARD = _TXT = _MUT = _LINE = _BODY = _CHIP = _FAINT = _ACCENT = ""
# Neutral tones for chrome; the switch-class and net-category HUES come from the
# muted ui.theme CATEGORY family and are wired into the colour dicts below. Colour
# lives only on pins and net names; chip and card fills stay neutral graphite.
_T_MUST = _T_OSC = _T_FIXED = _T_SEL = ""


_SWITCH_COLOR: dict = {}
_BREAKOUT_COLOR = ""
_CAT_COLOR: dict = {}


def _refresh_tones():
    # native ui.theme tokens (legacy FG/FG_DIM/DOT_IDLE/ACCENT → txt1/txt2/hairline_strong/accent)
    global _T_MUST, _T_OSC, _T_FIXED, _T_SEL
    _T_MUST = _T.opaque("txt1")
    _T_OSC = _T.opaque("txt2")
    _T_FIXED = _T.opaque("hairline_strong")
    _T_SEL = _T.opaque("accent")


def _refresh_palette():
    """Rebuild the pin/net colour dicts from the muted CATEGORY family (ui.theme),
    so the pin map, diagram and legend carry switch-class and net-category colour.
    Colour lives ONLY on the pins and the net names; every chip and card background
    stays neutral graphite (never tinted). Needs stm32_db for the class constants."""
    global _SWITCH_COLOR, _BREAKOUT_COLOR, _CAT_COLOR
    c = _T.CATEGORY_DARK if _T.is_dark() else _T.CATEGORY_LIGHT   # opaque category hues
    _SWITCH_COLOR = {sdb.SWITCH_MUST: c["must"], sdb.SWITCH_OSC_OPTIONAL: c["osc"],
                     sdb.SWITCH_NONE: c["fixed"]}
    _BREAKOUT_COLOR = c["breakout"]
    _CAT_COLOR = {"power": c["power"], "ground": c["ground"], "core": c["core"],
                  "service": c["service"], "lane": c["lane"], "analog": c["core"]}


def set_tab_theme(dark: bool):
    global _PANEL, _CARD, _TXT, _MUT, _LINE, _BODY, _CHIP, _FAINT, _ACCENT
    _T.set_theme(dark)         # publish the one active theme for the shared kit too
    # legacy chrome keys → ui.theme native tokens, resolved to OPAQUE #rrggbb for SVG:
    _PANEL = _T.opaque("raised")    # bg_raised — the inspector reading surface
    _CARD = _T.opaque("inset")      # bg_inset — the one lift-step (signal path, hover, selection)
    _TXT = _T.opaque("txt1")        # text_1 primary
    _MUT = _T.opaque("txt2")        # text_2 secondary
    _FAINT = _T.opaque("txt3")      # text_3 micro / dormant / units
    _LINE = _T.opaque("hairline")   # hairline (the whole border budget)
    _BODY = _T.opaque("nav")        # bg_base — deepest step
    _CHIP = _T.opaque("tok")        # legacy neutral chip fill (chips are being retired)
    _ACCENT = _T.opaque("accent")   # azure — interaction only
    _refresh_tones()
    try:
        _refresh_palette()     # skipped on the import-time call before stm32_db loads
    except NameError:
        pass


set_tab_theme(False)   # light is the app default

import stm32_db as sdb
import stm32_authority as sauth
_refresh_palette()   # stm32_db now imported — build the grayscale colour dicts


# Scannable columns that fit the viewport without horizontal scrolling. The verbose
# per-pin detail (rationale, ADG714 wiring, tags, bootloader) lives in the focus
# panel beside the table; the CSV/Markdown exports still carry the full column set.
_COLS = ["Pin", "Side", "Pin Names", "Role Set", "Switch",
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


def _fmt_rng(r, unit="V") -> str:
    return f"{r[0]} to {r[1]} {unit}" if r else ""


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


def _fmt_contact(c: str) -> str:
    """'LA-33' -> 'J_CARD1_LA 33' — the full parent-receptacle identity."""
    if c.startswith("LA-"):
        return f"J_CARD1_LA {c[3:]}"
    if c.startswith("RA-"):
        return f"J_CARD1_RA {c[3:]}"
    return c


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
