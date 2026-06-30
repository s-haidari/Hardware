"""
switch_report.py — render the canonical switch-cell report (switch_engine) as
CSV, Markdown, and a self-contained HTML web front end.

Everything here derives from ``switch_engine.package_report``; there is no second
classification rule. This is the deliverable the engineer "pulls" to know which
socket pins need an ADG714 channel and what each one routes to.
"""
from __future__ import annotations

import csv
import html
import io
import sqlite3
from pathlib import Path

from . import switch_engine as se

CSV_COLUMNS = [
    "package", "pin", "side", "needs_switch", "switch_class", "conflict_roles",
    "dominant_role", "minority_roles", "required_cell", "primary_target_net",
    "all_target_nets", "mcu_counts", "total_mcus",
]


def _row_for(pkg: str, d: se.SwitchDecision) -> dict[str, str]:
    counts = ";".join(f"{k}={v}" for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1]))
    targets = ";".join(f"{k}->{v}" for k, v in sorted(d.target_nets.items()))
    return {
        "package": pkg,
        "pin": str(d.pin),
        "side": d.side,
        "needs_switch": "yes" if d.needs_switch else "no",
        "switch_class": d.switch_class,
        "conflict_roles": d.role_label,
        "dominant_role": d.dominant_identity,
        "minority_roles": ",".join(d.minority_identities),
        "required_cell": d.cell_required,
        "primary_target_net": d.primary_target_net if d.needs_switch else "",
        "all_target_nets": targets,
        "mcu_counts": counts,
        "total_mcus": str(d.total_mcus),
    }


def to_csv_rows(rep: se.PackageSwitchReport, switching_only: bool = True) -> list[dict[str, str]]:
    decisions = rep.decisions
    if switching_only:
        decisions = [d for d in decisions if d.needs_switch]
    return [_row_for(rep.package, d) for d in sorted(decisions, key=lambda d: d.pin)]


def write_csv(reps: list[se.PackageSwitchReport], path: Path, switching_only: bool = True) -> int:
    rows: list[dict[str, str]] = []
    for rep in reps:
        rows.extend(to_csv_rows(rep, switching_only=switching_only))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# ── Markdown ────────────────────────────────────────────────────────────────

def to_markdown(rep: se.PackageSwitchReport) -> str:
    out: list[str] = []
    out.append(f"# {rep.package} switch-cell report")
    out.append("")
    out.append(
        f"- **Must-switch pins:** {rep.must_switch_count}  "
        f"- **Osc-optional pins:** {rep.osc_optional_count}  "
        f"- **Fixed (no switch):** {rep.fixed_count}  "
        f"- **ADG714 cells (min):** {rep.adg714_count}"
    )
    out.append("")
    out.append("## Pins that must switch")
    out.append("")
    out.append("| Pin | Side | Conflict | Routes to | Required cell | Minority roles | MCU counts |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for d in sorted(rep.must_switch, key=lambda d: d.pin):
        counts = ", ".join(f"{k}={v}" for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1]))
        out.append(
            f"| {d.pin} | {d.side} | {d.role_label} | {d.primary_target_net} | "
            f"{d.cell_required} | {', '.join(d.minority_identities) or '—'} | {counts} |"
        )
    if rep.osc_optional:
        out.append("")
        out.append("## Oscillator pins (route direct OR switch — card's choice)")
        out.append("")
        out.append("| Pin | Side | Conflict | MCU counts |")
        out.append("| --- | --- | --- | --- |")
        for d in sorted(rep.osc_optional, key=lambda d: d.pin):
            counts = ", ".join(f"{k}={v}" for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1]))
            out.append(f"| {d.pin} | {d.side} | {d.role_label} | {counts} |")
    out.append("")
    out.append("## ADG714 bank allocation (one channel per must-switch pin)")
    out.append("")
    for b in rep.adg714_banks():
        chans = ", ".join(f"ch{i+1}: pin {p}→{net}" for i, (p, net) in enumerate(b.channels))
        out.append(f"- **Cell {b.index}** ({len(b.channels)}/8, {b.spare} spare): {chans}")
    out.append("")
    return "\n".join(out)


# ── HTML (self-contained, no JS deps) ───────────────────────────────────────

_CSS = """
:root{--bg:#1b1d21;--panel:#23262b;--line:#34383f;--text:#d7dadf;--mut:#8b929c;--acc:#5aa9e6;
--must:#e0805a;--osc:#c9a13b;--ok:#5ac18e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
nav{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 22px}
nav b{font-size:15px}nav a{margin-left:14px;font-size:13px}
main{padding:20px 26px;max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:18px 0 4px}h2{font-size:16px;border-bottom:1px solid var(--line);padding-bottom:6px;margin:30px 0 12px}
.sub{color:var(--mut);font-size:13px;margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:10px 0 4px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card .n{font-size:24px;font-weight:700}.card .l{color:var(--mut);font-size:12px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:5px 9px;text-align:left;vertical-align:top}
th{background:#2c3038;color:var(--mut);font-weight:600;position:sticky;top:0}
tr:nth-child(even) td{background:#1f2228}
code{font-family:ui-monospace,Consolas,monospace;font-size:12.5px}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:11px;font-weight:700;color:#11141a}
.t-must{background:var(--must)}.t-osc{background:var(--osc)}
.net{color:var(--acc);font-weight:600}.mino{color:var(--osc)}
.bank{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 0}
.note{color:var(--mut);font-size:12.5px;margin:6px 0 0}
"""


def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _stat(n, label) -> str:
    return f'<div class="card"><div class="n">{_e(n)}</div><div class="l">{_e(label)}</div></div>'


def _package_section(rep: se.PackageSwitchReport) -> str:
    p = rep.package
    out = [f'<h1 id="{_e(p)}">{_e(p)}</h1>',
           '<div class="sub">Which target socket pins need a card-side ADG714 switch channel, '
           'derived from the full per-pin role set across the whole STM32F family for this package.</div>']
    out.append('<div class="grid">')
    out.append(_stat(rep.must_switch_count, "Must switch"))
    out.append(_stat(rep.osc_optional_count, "Osc-optional"))
    out.append(_stat(rep.fixed_count, "Fixed (no switch)"))
    out.append(_stat(rep.adg714_count, "ADG714 cells (min)"))
    out.append('</div>')

    out.append('<h2>Pins that must switch</h2>')
    out.append('<table><tr><th>Pin</th><th>Side</th><th>Conflict roles</th><th>Routes to</th>'
               '<th>Required cell</th><th>Minority</th><th>MCU counts</th></tr>')
    for d in sorted(rep.must_switch, key=lambda d: d.pin):
        counts = ", ".join(f"{_e(k)}={v}" for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1]))
        mino = ", ".join(d.minority_identities)
        out.append(
            f'<tr><td><b>{d.pin}</b></td><td>{_e(d.side)}</td>'
            f'<td><span class="tag t-must">{_e(d.role_label)}</span></td>'
            f'<td class="net">{_e(d.primary_target_net)}</td>'
            f'<td><code>{_e(d.cell_required)}</code></td>'
            f'<td class="mino">{_e(mino) or "—"}</td>'
            f'<td>{counts}</td></tr>'
        )
    out.append('</table>')

    if rep.osc_optional:
        out.append('<h2>Oscillator pins — route direct or switch (per-card choice)</h2>')
        out.append('<div class="note">These are role-variable OSC|IO. LQFP64 Card 7B routes them '
                   'direct as lanes; LQFP100 Card 7C switches them. The data alone does not decide it.</div>')
        out.append('<table><tr><th>Pin</th><th>Side</th><th>Conflict</th><th>MCU counts</th></tr>')
        for d in sorted(rep.osc_optional, key=lambda d: d.pin):
            counts = ", ".join(f"{_e(k)}={v}" for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1]))
            out.append(f'<tr><td><b>{d.pin}</b></td><td>{_e(d.side)}</td>'
                       f'<td><span class="tag t-osc">{_e(d.role_label)}</span></td><td>{counts}</td></tr>')
        out.append('</table>')

    out.append('<h2>ADG714 bank allocation</h2>')
    out.append('<div class="note">One channel per must-switch pin (the IO alternate is the open-switch lane). '
               'Power roles that need current paralleling add channels at the card level; this is the minimum cell count.</div>')
    for b in rep.adg714_banks():
        chans = " &nbsp; ".join(
            f'<code>ch{i+1}</code> pin&nbsp;{p}&rarr;<span class="net">{_e(net)}</span>'
            for i, (p, net) in enumerate(b.channels)
        )
        out.append(f'<div class="bank"><b>Cell {b.index}</b> '
                   f'<span class="note">({len(b.channels)}/8, {b.spare} spare)</span><br>{chans}</div>')
    return "\n".join(out)


def to_html(reps: list[se.PackageSwitchReport], title: str = "STM32F Switch-Cell Report") -> str:
    nav_links = " ".join(f'<a href="#{_e(r.package)}">{_e(r.package)}</a>' for r in reps)
    body = "\n".join(_package_section(r) for r in reps)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        f"<nav><b>{_e(title)}</b>{nav_links}</nav><main>"
        "<div class='note'>Single source of truth: stm_helper.switch_engine. "
        "A pin needs a switch when it takes &ge;2 distinct routing identities "
        "(VDD/VDDA/VREF/VBAT/VSS/VCAP/BOOT/NRST/OSC/IO) across the package family. "
        "Minority roles are still counted (strict-safe) and shown.</div>"
        f"{body}</main></body></html>"
    )


def write_html(reps: list[se.PackageSwitchReport], path: Path, title: str = "STM32F Switch-Cell Report") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_html(reps, title=title), encoding="utf-8")


def build_reports(conn: sqlite3.Connection, packages: list[str]) -> list[se.PackageSwitchReport]:
    return [se.package_report(conn, p) for p in packages]
