"""
report_html.py — render the per-package engineering explorer as self-contained
HTML (no external assets, no JS dependency; collapsibles use native <details>).

This is a pin/hardware explorer, not a statistics table. The headline view is the
per-pin universal hardware branch diagram, generated from the electrical roles a
physical socket pin takes across every exact pinout group.
"""
from __future__ import annotations

import html
from pathlib import Path

from . import explore, io, kicad_blocks, normalize
from .paths import docs_dir, ensure_dirs, TARGET_PACKAGES

_TAG_COLOR = {
    "RS": "#d9822b", "PWR": "#c0392b", "USB": "#8e44ad", "SWD": "#2980b9",
    "ANA": "#16a085", "IO": "#6c7a89", "NC": "#34495e",
}
_TAG_NAME = {
    "RS": "role switch", "PWR": "power/ground", "USB": "USB-sensitive",
    "SWD": "debug-sensitive", "ANA": "analog-sensitive", "IO": "safe IO", "NC": "not connected",
}

_CSS = """
:root{--bg:#1b1d21;--panel:#23262b;--line:#34383f;--text:#d7dadf;--mut:#8b929c;--acc:#5aa9e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.wrap{display:flex;align-items:flex-start}
nav{position:sticky;top:0;align-self:flex-start;width:210px;min-width:210px;height:100vh;overflow:auto;
    padding:16px 12px;background:var(--panel);border-right:1px solid var(--line)}
nav h1{font-size:15px;margin:0 0 4px}nav .sub{color:var(--mut);font-size:12px;margin-bottom:12px}
nav a{display:block;padding:5px 8px;border-radius:5px;color:var(--text);font-size:13px}
nav a:hover{background:#2c3038;text-decoration:none}
main{flex:1;min-width:0;padding:22px 26px;max-width:1200px}
h2{font-size:18px;border-bottom:1px solid var(--line);padding-bottom:6px;margin:34px 0 14px;scroll-margin-top:10px}
h3{font-size:14px;color:var(--mut);margin:18px 0 8px;font-weight:600}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:#2c3038;color:var(--mut);font-weight:600}
tr:nth-child(even) td{background:#1f2228}
code,pre{font-family:ui-monospace,Menlo,Consolas,monospace}
pre{background:#15171a;border:1px solid var(--line);border-radius:6px;padding:12px;overflow:auto;font-size:12.5px;line-height:1.45}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;margin:8px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card .n{font-size:22px;font-weight:700}.card .l{color:var(--mut);font-size:12px}
.chip{display:inline-block;padding:2px 6px;border-radius:4px;color:#fff;font-size:11px;font-weight:700;margin:1px}
.pinchip{display:inline-block;width:62px;text-align:center;padding:3px 0;margin:2px;border-radius:4px;color:#fff;font-size:11px;font-weight:600}
.side{margin:6px 0}.side b{display:inline-block;width:62px;color:var(--mut)}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:6px 0;padding:2px 12px}
summary{cursor:pointer;padding:8px 0;font-weight:600}
summary .pin{color:var(--acc)}summary .mut{color:var(--mut);font-weight:400}
.flag{color:#e0b050}.bad{color:#e06c6c}.ok{color:#5ac18e}
.legend span{margin-right:12px;font-size:12px;color:var(--mut)}
"""

# ── helpers ─────────────────────────────────────────────────────────────────

def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _table(headers, rows) -> str:
    h = "".join(f"<th>{_e(c)}</th>" for c in headers)
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>")
    return f"<table><tr>{h}</tr>{''.join(body)}</table>"


def _chip(tag: str) -> str:
    return f'<span class="chip" style="background:{_TAG_COLOR.get(tag,"#555")}">{_e(tag)}</span>'


# ── sections ────────────────────────────────────────────────────────────────

def _overview(ed: explore.ExplorerData) -> str:
    c = ed.counts
    cards = [
        ("MCUs in baseline", c["baseline_members"]), ("Exact groups", c["exact_groups"]),
        ("Build passes", len(ed.passes)), ("Direct IO", c["direct_io"]),
        ("IO-isolated", c["io_switch"]), ("Full role-switch", c["full_role_switch"]),
        ("Fixed power", c["power"]), ("Fixed ground", c["ground"]),
        ("VCAP pins", c["vcap"]), ("USB pairs", c["usb"]),
        ("Switched-gnd review", c["switched_ground_review"]),
        ("Unsafe legacy flags", c["unsafe_db_classifications"]),
        ("Role-control bits", c["role_control_bits"]),
        ("Shift-reg banks (8b)", c["shift_register_banks_8bit"]),
    ]
    grid = "".join(f'<div class="card"><div class="n">{_e(v)}</div>'
                   f'<div class="l">{_e(l)}</div></div>' for l, v in cards)
    v = ed.voltage
    rails = ", ".join(r for r, on in [
        ("VTARGET", True), ("VDDA", v.get("vdda_target_required")),
        ("VREF", v.get("vref_target_required")), ("VBAT", v.get("vbat_target_required")),
        ("VCAP", v.get("vcap_branch_required"))] if on)
    return (f'<h2 id="overview">{_e(ed.package)} — package overview</h2>'
            f'<div class="grid">{grid}</div>'
            f'<p><b>Programmable target rail:</b> VTARGET {_e(v.get("vtarget_range","?"))} '
            f'&nbsp; <b>Parent rails required:</b> {_e(rails)}</p>'
            + _package_map(ed))


def _package_map(ed: explore.ExplorerData) -> str:
    by_side = {"left": [], "bottom": [], "right": [], "top": []}
    for p in ed.pins:
        by_side.get(p.side, by_side["left"]).append(p)
    rows = []
    for side in ("top", "left", "right", "bottom"):
        chips = "".join(
            f'<a class="pinchip" style="background:{_TAG_COLOR.get(p.tag,"#555")}" '
            f'href="#pin-{p.pin}">{p.pin} {p.tag}</a>'
            for p in sorted(by_side[side], key=lambda x: x.pin))
        rows.append(f'<div class="side"><b>{side.upper()}</b>{chips or "<i>—</i>"}</div>')
    legend = '<div class="legend">' + "".join(
        f"<span>{_chip(t)} {_e(n)}</span>" for t, n in _TAG_NAME.items()) + "</div>"
    return ("<h3>Package map — every socket pin, tagged by what it requires</h3>"
            + "".join(rows) + legend)


def _groups(ed: explore.ExplorerData) -> str:
    rows = []
    ports_by_group = _ports_by_group(ed)
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        pass_id = next((p.pass_id for p in ed.passes if p.to_group_id == g.gid), "")
        status = "Build first" if g.is_baseline else f"Delta · {g.delta_kind}"
        rows.append([g.code, g.member_count, pass_id,
                     _e(g.delta_notes or "baseline (no deviations)"), status])
    table = _table(["Group", "MCUs", "Build pass", "Summary", "Status"], rows)
    blocks = []
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        members = ", ".join(g.members[:30]) + (f" … (+{len(g.members)-30})" if len(g.members) > 30 else "")
        ports = ports_by_group.get(g.code, [])
        port_rows = [[p.parent_net, p.matched_function or p.service, p.source_pin, p.source_lane,
                      _ok(p.exact_function_validated)] for p in ports]
        pt = _table(["Parent port", "Exact function", "Pin", "Lane", "validated"], port_rows) \
            if port_rows else "<i>no standard ports</i>"
        blocks.append(
            f'<details><summary><span class="pin">{_e(g.code)}</span> '
            f'<span class="mut">— {g.member_count} MCUs · sig {_e(g.signature_hash)}</span></summary>'
            f'<p><b>Members:</b> {_e(members)}</p>'
            f'<h3>Standard parent ports</h3>{pt}</details>')
    return (f'<h2 id="groups">Exact pinout groups</h2>'
            f'<p class="mut">Groups share an <b>exact</b> pinout — never a majority merge. '
            f'Sorted by member count; the largest is the baseline built first.</p>'
            + table + "".join(blocks))


def _group_pin_maps(ed: explore.ExplorerData) -> str:
    ports = {(p.group_code, p.source_pin): p.parent_net for p in ed.standard_ports}
    blocks = []
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        gm = ed.group_pin_maps.get(g.code, {})
        rows = []
        for pin in sorted(gm):
            d = gm[pin]
            role = explore._role_from(d["name"], d["eclass"])
            funcs = "; ".join(d["functions"]) if d["functions"] else "—"
            rows.append([pin, f"CARD_LANE_{pin:03d}", _e(d["name"]), _e(role),
                         _e(funcs), _e(ports.get((g.code, pin), ""))])
        blocks.append(
            f'<details><summary><span class="pin">{_e(g.code)}</span> '
            f'<span class="mut">pin map — exact functions</span></summary>'
            + _table(["Pin", "Lane", "Pin name", "Role", "Exact functions", "Parent port"], rows)
            + "</details>")
    return (f'<h2 id="pinmap">Group pin maps</h2>'
            f'<p class="mut">Every socket pin for each exact group, with the exact functions '
            f'(no broad UART/SPI/TIM).</p>' + "".join(blocks))


def _pin_details(ed: explore.ExplorerData) -> str:
    blocks = []
    for p in ed.pins:
        obs_rows = [[o.group_code, o.member_count, _e(o.pin_name), _e(o.role),
                     _e("; ".join(o.functions) if o.functions else o.role)]
                    for o in p.observations]
        obs = _table(["Group", "MCUs", "Pin name", "Role", "Exact functions"], obs_rows) \
            if obs_rows else "<i>no per-group detail</i>"
        review = ('<p class="flag">⚠ This pin cannot be hardwired as IO — at least one exact '
                  'group uses it as a supply/return/cap; it needs a role switch.</p>'
                  if len(set(p.union_roles) & {"VDD", "VDDA", "VSS", "VSSA", "VCAP", "VBAT", "VREF"})
                  and "IO" in p.union_roles else "")
        blocks.append(
            f'<details id="pin-{p.pin}"><summary>{_chip(p.tag)} '
            f'<span class="pin">Pin {p.pin}</span> '
            f'<span class="mut">· {p.lane} · roles: {_e(", ".join(p.union_roles) or "—")} '
            f'· cell {_e(p.universal_cell)}</span></summary>'
            f'<h3>Observed across exact pinout groups</h3>{obs}'
            f'<h3>Universal hardware cell — physical branches</h3>'
            f'<pre>{_branch_pre(p)}</pre>{review}</details>')
    return (f'<h2 id="pins">Pin detail — what each socket pin does in every group</h2>'
            + "".join(blocks))


def _branch_pre(p: explore.PinExplore) -> str:
    lines = [f"SOCKET_P{p.pin:03d}_COMMON"]
    for i, b in enumerate(p.branches):
        tee = "└──" if i == len(p.branches) - 1 else "├──"
        groups = ", ".join(g.split("_FULL_")[-1] if "_FULL_" in g else g
                           for g in b.used_by_groups)
        supports = "; ".join(b.supports)
        meta = f"   [{groups} | {supports}]" if supports else f"   [{groups}]"
        lines.append(f"    {tee} {b.switch_ref} {b.tail}{meta}")
        if b.note:
            lines.append(f"    {'   ' if i == len(p.branches)-1 else '│  '}      ↳ {b.note}")
    if not p.branches:
        lines.append("    └── (not connected)")
    return _e("\n".join(lines))


def _universal_hardware(ed: explore.ExplorerData) -> str:
    rows = []
    for p in ed.pins:
        branch_names = ", ".join(b.role for b in p.branches) or "—"
        rows.append([f'<a href="#pin-{p.pin}">{p.pin}</a>', p.lane,
                     _e("; ".join(p.union_roles) or "—"), _e(branch_names),
                     _e(p.universal_cell) + (' <span class="flag">REV</span>' if p.review else "")])
    return (f'<h2 id="hardware">Universal pin hardware</h2>'
            f'<p class="mut">One row per physical pin → the parallel hardware branches the card '
            f'must build. Click a pin for its branch diagram.</p>'
            + _table(["Pin", "Lane", "Roles seen", "Required branches", "Universal cell"], rows))


def _standard_ports(ed: explore.ExplorerData) -> str:
    blocks = []
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        ports = [p for p in ed.standard_ports if p.group_code == g.code]
        rows = [[p.parent_net, _e(p.matched_function or p.service), p.source_pin, p.source_lane,
                 _e(p.direction), _ok(p.exact_function_validated)] for p in ports]
        if not rows:
            continue
        blocks.append(
            f'<details><summary><span class="pin">{_e(g.code)}</span> '
            f'<span class="mut">parent routing</span></summary>'
            + _table(["Parent net", "Exact function", "Pin", "Lane", "Direction", "validated"], rows)
            + "</details>")
    return (f'<h2 id="ports">Parent standard port routing</h2>'
            f'<p class="mut">The parent only ever exposes standardized nets; each is validated '
            f'against the exact function on its lane.</p>' + "".join(blocks))


def _passes(ed: explore.ExplorerData) -> str:
    lines = []
    for p in ed.passes:
        lines.append(f"{p.pass_id} — {p.pass_type}")
        lines.append(f"  └── group {p.to_group_id}: +{p.mcus_newly_enabled} MCUs "
                     f"(cumulative {p.cumulative_pct:.0f}%)")
        if p.affected_lanes:
            lines.append(f"        affected lanes: {', '.join(p.affected_lanes[:8])}"
                         + (" …" if len(p.affected_lanes) > 8 else ""))
        if p.new_required_cells:
            lines.append(f"        new cells:   {', '.join(p.new_required_cells)}")
        if p.new_parent_router_candidates:
            lines.append(f"        new routes:  {', '.join(p.new_parent_router_candidates)}")
        lines.append(f"        acceptance:  {p.acceptance_criteria}")
        lines.append("")
    return (f'<h2 id="passes">Build pass plan — group-by-group deltas</h2>'
            f'<pre>{_e(chr(10).join(lines))}</pre>')


def _voltage(ed: explore.ExplorerData) -> str:
    v = ed.voltage
    rails = [
        ["VTARGET", "required", "—", _e(v.get("vtarget_range", "?"))],
        ["VDDA_TARGET", _yn(v.get("vdda_target_required")), v.get("vdda_pins", 0), "analog supply filter"],
        ["VREF_TARGET", _yn(v.get("vref_target_required")), v.get("vref_pins", 0), "ADC/DAC reference"],
        ["VBAT_TARGET", _yn(v.get("vbat_target_required")), v.get("vbat_pins", 0), "backup domain"],
        ["VCAP_LOCAL", _yn(v.get("vcap_branch_required")), v.get("vcap_pins", 0), "local 2.2µF on card"],
    ]
    affected = [[p.pin, p.lane, _e("; ".join(r for r in p.union_roles
                if r in ("VDD", "VDDA", "VSS", "VSSA", "VCAP", "VBAT", "VREF")))]
                for p in ed.pins
                if set(p.union_roles) & {"VDD", "VDDA", "VSS", "VSSA", "VCAP", "VBAT", "VREF"}]
    return (f'<h2 id="voltage">Voltage requirements</h2>'
            + _table(["Rail", "Required", "Pins", "Notes"], rails)
            + "<h3>Pins driving rail/branch hardware</h3>"
            + _table(["Pin", "Lane", "Rail roles"], affected))


def _cells_section() -> str:
    blocks = []
    for cid, s in kicad_blocks.CELL_SPECS.items():
        blocks.append(
            f'<details><summary><span class="pin">{_e(cid)}</span> '
            f'<span class="mut">— {_e(s["cell_name"])}</span></summary>'
            f'<p>{_e(s["purpose"])}</p><p class="mut"><b>Used when:</b> {_e(s["used_when"])}</p>'
            f'<pre>{_e(s["ascii_schematic"])}</pre></details>')
    return (f'<h2 id="cells">Cell library — physical branch first</h2>'
            f'<p class="mut">Each reusable cell starts from the physical node, not an abstract label.</p>'
            + "".join(blocks))


def _ports_by_group(ed):
    out = {}
    for p in ed.standard_ports:
        out.setdefault(p.group_code, []).append(p)
    return out


def _ok(status: str) -> str:
    return {"yes": '<span class="ok">✓ exact</span>',
            "dedicated_pin": '<span class="mut">dedicated pin</span>',
            "no": '<span class="bad">✗ no exact fn</span>'}.get(status, _e(status))


def _yn(x) -> str:
    return "required" if x else "no"


def render_package(ed: explore.ExplorerData) -> str:
    nav = "".join(f'<a href="#{i}">{n}</a>' for i, n in [
        ("overview", "Overview"), ("groups", "Exact groups"), ("pinmap", "Group pin maps"),
        ("pins", "Pin detail"), ("hardware", "Universal hardware"), ("ports", "Standard ports"),
        ("passes", "Build passes"), ("voltage", "Voltage"), ("cells", "Cell library")])
    body = (_overview(ed) + _groups(ed) + _group_pin_maps(ed) + _pin_details(ed)
            + _universal_hardware(ed) + _standard_ports(ed) + _passes(ed)
            + _voltage(ed) + _cells_section())
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(ed.package)} — STM32 board explorer</title><style>{_CSS}</style></head>'
            f'<body><div class="wrap"><nav><h1>{_e(ed.package)}</h1>'
            f'<div class="sub"><a href="index.html">‹ all packages</a></div>{nav}</nav>'
            f'<main>{body}</main></div></body></html>')


def _qtable(headers, rows) -> str:
    """Table with explicit border attributes — renders gridlines in QTextBrowser
    (which doesn't honour CSS border-collapse)."""
    h = "".join(f"<th>{_e(c)}</th>" for c in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<table border="1" cellspacing="0" cellpadding="4" '
            f'width="100%">{("<tr>"+h+"</tr>")}{body}</table>')


def render_qtdoc(ed: explore.ExplorerData) -> str:
    """A linear, anchored variant of the explorer for Qt's QTextBrowser (no flex,
    no <details>). Used to embed the explorer inside the desktop app."""
    out: list[str] = []
    a = out.append
    toc = " &nbsp;·&nbsp; ".join(
        f'<a href="#{i}">{n}</a>' for i, n in
        [("overview", "Overview"), ("groups", "Groups"), ("pins", "Pin detail"),
         ("hardware", "Hardware"), ("ports", "Ports"), ("passes", "Passes"),
         ("voltage", "Voltage")])
    a(f'<h2>{_e(ed.package)} — board explorer</h2><p>{toc}</p><hr>')

    c, v = ed.counts, ed.voltage
    a('<a name="overview"></a><h3>Overview</h3>')
    keys = [("baseline_members", "MCUs in baseline"), ("exact_groups", "Exact groups"),
            ("full_role_switch", "Full role-switch pins"), ("direct_io", "Direct IO"),
            ("io_switch", "IO-isolated"), ("power", "Fixed power"), ("ground", "Fixed ground"),
            ("vcap", "VCAP"), ("usb", "USB pairs"),
            ("switched_ground_review", "Switched-gnd review"),
            ("unsafe_db_classifications", "Unsafe legacy flags"),
            ("role_control_bits", "Role-control bits")]
    a(_qtable(["metric", "value"], [[lbl, c[k]] for k, lbl in keys]))
    rails = ", ".join(r for r, on in [("VTARGET", True), ("VDDA", v.get("vdda_target_required")),
                      ("VREF", v.get("vref_target_required")), ("VBAT", v.get("vbat_target_required")),
                      ("VCAP", v.get("vcap_branch_required"))] if on)
    a(f'<p><b>VTARGET</b> {_e(v.get("vtarget_range","?"))} &nbsp; <b>rails:</b> {_e(rails)}</p>')
    a('<p><b>Package map:</b><br>')
    for side in ("top", "left", "right", "bottom"):
        chips = " ".join(
            f'<a href="#pin-{p.pin}"><font color="{_TAG_COLOR.get(p.tag,"#888")}">'
            f'{p.pin}·{p.tag}</font></a>'
            for p in sorted(ed.pins, key=lambda x: x.pin) if p.side == side)
        a(f'<b>{side.upper()}</b> {chips or "—"}<br>')
    a('</p>')

    a('<a name="groups"></a><h3>Exact pinout groups</h3>')
    grows = []
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        pid = next((p.pass_id for p in ed.passes if p.to_group_id == g.gid), "")
        grows.append([g.code, g.member_count, pid,
                      _e(g.delta_notes or "baseline (no deviations)")])
    a(_qtable(["group", "MCUs", "pass", "summary"], grows))

    a('<a name="pins"></a><h3>Pin detail — universal hardware branches</h3>')
    for p in ed.pins:
        a(f'<a name="pin-{p.pin}"></a><p><font color="{_TAG_COLOR.get(p.tag,"#888")}">'
          f'<b>Pin {p.pin}</b></font> · {p.lane} · roles: '
          f'{_e(", ".join(p.union_roles) or "—")} · cell {_e(p.universal_cell)}</p>')
        if p.observations:
            a(_qtable(["group", "MCUs", "pin name", "role", "exact functions"],
                      [[o.group_code.split("_FULL_")[-1], o.member_count, _e(o.pin_name),
                        _e(o.role), _e("; ".join(o.functions) if o.functions else o.role)]
                       for o in p.observations]))
        a(f"<pre>{_branch_pre(p)}</pre>")

    a('<a name="hardware"></a><h3>Universal pin hardware</h3>')
    a(_qtable(["pin", "lane", "roles seen", "branches", "cell"],
              [[f'<a href="#pin-{p.pin}">{p.pin}</a>', p.lane, _e("; ".join(p.union_roles) or "—"),
                _e(", ".join(b.role for b in p.branches) or "—"),
                _e(p.universal_cell) + (" (REV)" if p.review else "")] for p in ed.pins]))

    a('<a name="ports"></a><h3>Parent standard ports</h3>')
    for g in sorted(ed.groups, key=lambda x: -x.member_count):
        ports = [p for p in ed.standard_ports if p.group_code == g.code]
        if not ports:
            continue
        a(f"<p><b>{_e(g.code)}</b></p>")
        a(_qtable(["parent net", "exact function", "pin", "lane", "validated"],
                  [[p.parent_net, _e(p.matched_function or p.service), p.source_pin,
                    p.source_lane, _ok(p.exact_function_validated)] for p in ports]))

    a('<a name="passes"></a><h3>Build passes</h3><pre>')
    for p in ed.passes:
        a(_e(f"{p.pass_id} — {p.pass_type}\n  +{p.mcus_newly_enabled} MCUs "
             f"(cum {p.cumulative_pct:.0f}%)  cells: {', '.join(p.new_required_cells)}\n"))
    a("</pre>")

    a('<a name="voltage"></a><h3>Voltage requirements</h3>')
    a(_qtable(["rail", "required", "pins"], [
        ["VTARGET", _e(v.get("vtarget_range", "?")), "—"],
        ["VDDA_TARGET", _yn(v.get("vdda_target_required")), v.get("vdda_pins", 0)],
        ["VREF_TARGET", _yn(v.get("vref_target_required")), v.get("vref_pins", 0)],
        ["VBAT_TARGET", _yn(v.get("vbat_target_required")), v.get("vbat_pins", 0)],
        ["VCAP_LOCAL", _yn(v.get("vcap_branch_required")), v.get("vcap_pins", 0)]]))
    return "".join(out)


def render_pin_detail(ed: explore.ExplorerData, pe: explore.PinExplore) -> str:
    """Detail for ONE socket pin — used by the in-app Pin Map right panel.
    Shows what every exact group uses the pin for, the universal hardware branch
    diagram, and which parent standard ports it sources."""
    color = _TAG_COLOR.get(pe.tag, "#888")
    obs = _qtable(["group", "MCUs", "pin name", "role", "exact functions"],
                  [[o.group_code.split("_FULL_")[-1], o.member_count, _e(o.pin_name),
                    _e(o.role), _e("; ".join(o.functions) if o.functions else o.role)]
                   for o in pe.observations]) if pe.observations else "<i>no per-group detail</i>"
    dangerous = set(pe.union_roles) & {"VDD", "VDDA", "VSS", "VSSA", "VCAP", "VBAT", "VREF"}
    review = ('<p><font color="#e0b050">⚠ Cannot be hardwired as IO — at least one '
              'exact group uses this pin as a supply/return/cap; it needs a role '
              'switch.</font></p>' if dangerous and "IO" in pe.union_roles else "")
    ports = [p for p in ed.standard_ports if p.source_pin == pe.pin]
    port_tbl = _qtable(["parent net", "exact function", "group", "validated"],
                       [[p.parent_net, _e(p.matched_function or p.service),
                         p.group_code.split("_FULL_")[-1], _ok(p.exact_function_validated)]
                        for p in ports]) if ports else "<i>not a parent service source</i>"
    return (f'<h3><font color="{color}">Pin {pe.pin}</font> · {pe.lane}</h3>'
            f'<p>tag: <b>{pe.tag}</b> · roles seen: {_e(", ".join(pe.union_roles) or "—")} '
            f'· universal cell: <b>{_e(pe.universal_cell)}</b></p>'
            f'<b>What each exact pinout group uses this pin for</b>{obs}{review}'
            f'<b>Universal hardware — physical branches</b><pre>{_branch_pre(pe)}</pre>'
            f'<b>Parent standard-port routing</b>{port_tbl}')


def render_index(packages: list[str]) -> str:
    links = "".join(f'<li><a href="{_e(p)}.html">{_e(p)}</a></li>' for p in packages)
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<title>STM32 universal board explorer</title><style>{_CSS}</style></head>'
            f'<body><main><h2>STM32 universal board database — explorer</h2>'
            f'<p class="mut">Pin + hardware implementation explorer per package.</p>'
            f'<ul>{links}</ul></main></body></html>')


def write_reports(conn, package: str | None = None) -> list[str]:
    d = docs_dir() / "reports"
    ensure_dirs(d)
    avail = io.available_packages(conn)
    pkgs = [package] if package else [p for p in TARGET_PACKAGES if p in avail] or avail
    written = []
    for pkg in pkgs:
        pd = normalize.assemble(conn, pkg)
        ed = build_and_render(conn, pkg, pd)
        (d / f"{pkg}.html").write_text(ed, encoding="utf-8")
        written.append(f"{pkg}.html")
    index_pkgs = [p for p in TARGET_PACKAGES if p in avail] or avail
    (d / "index.html").write_text(render_index(index_pkgs), encoding="utf-8")
    return written


def build_and_render(conn, package: str, pd) -> str:
    ed = explore.build_explorer(conn, package, pd)
    return render_package(ed)
