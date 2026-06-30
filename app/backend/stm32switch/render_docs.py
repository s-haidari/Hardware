"""
render_docs.py — generate markdown documentation from the spec data + package
analysis.  Cell/router docs come from the single-source spec in
:mod:`kicad_blocks`; package reports come from the assembled :class:`PackageData`
and the :mod:`validate` findings.
"""
from __future__ import annotations

from pathlib import Path

from . import routers, validate
from .kicad_blocks import CELL_SPECS, ROUTER_SPECS
from .normalize import PackageData
from . import roles as R
from .paths import docs_dir, ensure_dirs


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _list(items) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "_none_"


# ── cell + router library docs (package-independent) ───────────────────────

def write_cell_docs() -> int:
    d = docs_dir() / "cell_library"
    ensure_dirs(d)
    for cid, s in CELL_SPECS.items():
        comp = s.get("component_requirements")
        if isinstance(comp, dict):
            comp_md = _table(["variant / part", "requirements"],
                             [[k, ", ".join(v) if isinstance(v, list) else v]
                              for k, v in comp.items()])
        else:
            comp_md = _list(comp or [])
        rc = s.get("required_components", [])
        rc_md = _table(["ref", "value", "note"],
                       [[c.get("ref", ""), c.get("value", ""), c.get("note", "")]
                        for c in rc]) if rc else "_none_"
        roles_md = ""
        if "role_codes" in s:
            roles_md = "\n### Role codes\n\n" + _table(
                ["code", "role"], [[k, v] for k, v in s["role_codes"].items()])
        body = f"""# {cid} — {s['cell_name']}

**Board side:** {s['board_side']}

## Purpose
{s['purpose']}

## Used when
{s['used_when']}

## Schematic (architecture diagram — NOT a KiCad screenshot)
```
{s['ascii_schematic']}
```
KiCad screenshot (when exported): `{s['screenshot_path']}`
KiCad sheet: `{s['kicad_sheet_name']}`

## Hierarchical pins
{_list(s['hierarchical_pins'])}

## Internal nets
{_list(s.get('internal_nets', []))}

## Required components
{rc_md}

## Component requirements
{comp_md}
{roles_md}

## Default state
{s['default_state']}

## Enable logic
{s['enable_logic']}

## Safety rules
{_list(s['safety_rules'])}

## Signal-integrity rules
{_list(s.get('signal_integrity_rules', []))}

## Example role sets that require this cell
{_list(s.get('example_role_sets', []))}

> Concrete lanes per package: see `data/packages/<pkg>/cell_requirements_<pkg>.csv`.

## Notes
{s.get('notes', '')}
"""
        _w(d / f"{cid}.md", body)
    return len(CELL_SPECS)


def write_router_docs() -> int:
    d = docs_dir() / "parent_routers"
    ensure_dirs(d)
    for rid, s in ROUTER_SPECS.items():
        body = f"""# {rid}

## Purpose
{s['purpose']}

## Inputs (sparse candidate lanes)
{_list(s['inputs'])}

## Standardized parent outputs
{_list(s['outputs'])}

**Switch class:** `{s['switch_class']}`

## Schematic (architecture diagram — NOT a KiCad screenshot)
```
{s['ascii_schematic']}
```
KiCad screenshot (when exported): `docs/images/routers/{rid}.png`

## Rules
{_list(s['rules'])}

## Notes
{s.get('notes', '')}

> Concrete candidate lanes per package: see
> `data/packages/<pkg>/parent_router_candidates_<pkg>.csv`.
"""
        _w(d / f"{rid}.md", body)
    return len(ROUTER_SPECS)


# ── package reports ────────────────────────────────────────────────────────

def write_package_docs(pd: PackageData, report: validate.ValidationReport) -> None:
    d = docs_dir() / "package_reports"
    ensure_dirs(d)
    _write_summary(d, pd, report)
    _write_validation(d, pd, report)
    _write_cell_requirements(d, pd)
    _write_router_candidates(d, pd)
    _write_lane_conflicts(d, pd)
    _write_pass_plan(pd)
    _write_pinout_groups(pd)


def _write_summary(d, pd, report):
    c = validate.summary_counts(pd)
    rows = [[k.replace("_", " "), v] for k, v in c.items()]
    body = f"""# {pd.package} — design summary

Package geometry: {pd.pin_count} pins · {pd.pitch_mm} mm pitch · {pd.body_mm} mm body.
Generated from `stm32_profiles.sqlite` (db-derived). Lanes use the 176-lane
superset backplane; this package occupies CARD_LANE_001..{pd.pin_count:03d}.

## Counts
{_table(["metric", "value"], rows)}

## Safety headline
**{c['unsafe_db_classifications']}** socket pins were classified `hardwired_io`
(or another hardwired kind) by the legacy data but actually require a role
switch given their complete role set. See `{pd.package}_validation.md`.

- Hard errors: **{len(report.errors)}**
- Warnings: **{len(report.warnings)}**

## Voltage requirements
{_voltage_block(pd)}

## Parent standardized ports (exact-function validated)
{_ports_block(pd)}
"""
    _w(d / f"{pd.package}_summary.md", body)


def _voltage_block(pd: PackageData) -> str:
    v = pd.voltage
    rows = [
        ["VTARGET range", v.get("vtarget_range", "UNKNOWN")],
        ["~1.8 V-class IO needed", "yes" if v.get("low_voltage_io_required") else "no"],
        ["VDDA_TARGET branch", f"{'required' if v.get('vdda_target_required') else 'no'} ({v.get('vdda_pins',0)} pins)"],
        ["VREF_TARGET branch", f"{'required' if v.get('vref_target_required') else 'no'} ({v.get('vref_pins',0)} pins)"],
        ["VBAT_TARGET branch", f"{'required' if v.get('vbat_target_required') else 'no'} ({v.get('vbat_pins',0)} pins)"],
        ["VCAP local cap", f"{'required' if v.get('vcap_branch_required') else 'no'} ({v.get('vcap_pins',0)} pins)"],
    ]
    return _table(["item", "value"], rows)


def _ports_block(pd: PackageData) -> str:
    ok = sum(1 for p in pd.standard_ports if p.exact_function_validated == "yes")
    bad = [p for p in pd.standard_ports if p.exact_function_validated == "no"]
    ded = sum(1 for p in pd.standard_ports if p.exact_function_validated == "dedicated_pin")
    head = (f"{len(pd.standard_ports)} ports across {len(pd.groups)} groups — "
            f"**{ok}** exact-function validated, {ded} dedicated pins, **{len(bad)}** unvalidated.")
    if bad:
        head += "\n\n" + _table(
            ["group", "net", "lane", "functions on lane"],
            [[p.group_code, p.parent_net, p.source_lane, p.lane_functions or "none"]
             for p in bad[:20]])
    return head


def _write_validation(d, pd, report):
    err_rows = [[f.code, f.where, f.message] for f in report.errors]
    warn_rows = [[f.code, f.where, f.message] for f in report.warnings]
    unsafe = [f for f in pd.facts if f.db_unsafe]
    audit = _table(["pin", "lane", "role_set", "legacy_db_cell_kind", "required_cell"],
                   [[f.pin, f.lane, R.role_set_str(f.role_set), f.db_cell_kind,
                     f.decision.cell_id] for f in unsafe]) if unsafe else "_none_"
    body = f"""# {pd.package} — validation report

## Section 21 audit — unsafe legacy classifications
Rows where the legacy DB cell kind is hardwired but the full role set contains a
supply/return/cap (VDD/VSS/VCAP/VDDA/VSSA/VBAT/VREF) or other isolation-requiring
role. These must route through a role-switch (or dedicated) cell.

{audit}

## Hard errors ({len(report.errors)})
{_table(["code", "where", "message"], err_rows) if err_rows else "_none_"}

## Warnings ({len(report.warnings)})
{_table(["code", "where", "message"], warn_rows) if warn_rows else "_none_"}
"""
    _w(d / f"{pd.package}_validation.md", body)


def _write_cell_requirements(d, pd):
    from collections import Counter
    counts = Counter(f.decision.cell_id for f in pd.facts)
    rows = [[cid, n] for cid, n in sorted(counts.items())]
    detail = _table(["pin", "lane", "role_set", "required_cell", "variant", "component_class"],
                    [[f.pin, f.lane, R.role_set_str(f.role_set), f.decision.cell_id,
                      f.decision.variant, f.decision.component_class] for f in pd.facts])
    body = f"""# {pd.package} — cell requirements

## Cell type counts
{_table(["cell", "count"], rows)}

## Per-pin cell requirement
{detail}
"""
    _w(d / f"{pd.package}_cell_requirements.md", body)


def _write_router_candidates(d, pd):
    sections = []
    for rid in routers.ALL_ROUTERS:
        t = pd.router_tables[rid]
        rows = [[c.pin, c.lane, c.service, c.side] for c in t.candidates]
        sections.append(
            f"## {rid}\nOutputs: `{', '.join(t.outputs)}` · switch: `{t.switch_class}` · "
            f"**{t.lane_count}** candidate lanes\n\n"
            + (_table(["pin", "lane", "service", "side"], rows) if rows else "_no candidates_"))
    _w(d / f"{pd.package}_parent_router_candidates.md",
       f"# {pd.package} — parent router candidates (sparse)\n\n" + "\n\n".join(sections))


def _write_lane_conflicts(d, pd):
    rows = [[f.pin, f.lane, R.role_set_str(f.role_set), f.conflict,
             f.decision.cell_id]
            for f in pd.facts if f.conflict and f.conflict != "none"]
    body = f"""# {pd.package} — lane conflicts

Pins where different MCU groups drive incompatible services onto the same
physical lane (resolved by the card role-switch cell).

{_table(["pin", "lane", "role_set", "conflict", "required_cell"], rows) if rows else "_no conflicts recorded_"}
"""
    _w(d / f"{pd.package}_lane_conflicts.md", body)


def _write_pass_plan(pd):
    d = docs_dir() / "pass_plans"
    ensure_dirs(d)
    rows = [[p.pass_id, p.pass_type, p.mcus_newly_enabled,
             f"{p.cumulative_pct:.1f}%", ",".join(p.new_required_cells[:3]),
             ",".join(p.new_parent_router_candidates)] for p in pd.passes]
    body = f"""# {pd.package} — build pass plan

Each pass enables one more exact pinout group and records the parent hardware it
newly requires. Footprints follow the union of all groups (DNI/0Ω for early
passes); see `pass_strategy_notes.md`.

{_table(["pass", "type", "+MCUs", "coverage", "new cells", "new routers"], rows)}
"""
    _w(d / f"{pd.package}_pass_plan.md", body)


def _write_pinout_groups(pd):
    d = docs_dir() / "pinout_groups"
    ensure_dirs(d)
    rows = [[g.code, "✔" if g.is_baseline else "", g.member_count,
             f"{g.coverage_pct:.1f}%", g.delta_kind,
             (g.delta_notes[:70] + "…") if len(g.delta_notes) > 70 else g.delta_notes]
            for g in pd.groups]
    body = f"""# {pd.package} — exact pinout groups

Groups are exact shared-pinout sets (never per-pin majority merges). The
baseline is the largest exact group; later groups are deltas from it.

{_table(["group", "baseline", "members", "coverage", "delta kind", "deviation"], rows)}
"""
    _w(d / f"{pd.package}_groups.md", body)


# ── architecture + strategy (package-independent) ──────────────────────────

def write_architecture_docs() -> None:
    d = docs_dir() / "architecture"
    ensure_dirs(d)
    _w(d / "overview.md", _ARCH_OVERVIEW)
    _w(d / "parent_backplane_176.md", _BACKPLANE_DOC)
    _w(docs_dir() / "pass_plans" / "pass_strategy_notes.md", _PASS_STRATEGY)


_ARCH_OVERVIEW = """# Architecture overview

```
MCU socket pin
    -> plug-in-card pin role switch cell      (card makes the pin safe)
    -> safe CARD_LANE_xxx
    -> parent-side service router mux          (parent reuses shared hardware)
    -> standardized parent hardware net
```

The parent board exposes only standardized nets (PARENT_SWDIO, PARENT_NRST,
PARENT_UART_*, PARENT_USB_*, PARENT_ADC_PROBE, VTARGET, ...). It never exposes a
raw lane as a user-facing function. The daughter card is responsible for making
each socket pin electrically safe before it ever reaches the parent.

**Core safety rule:** a pin's required cell is decided from its complete role set
across every MCU in the package, never from a dominant role.
"""

_BACKPLANE_DOC = """# Parent backplane — 176-lane superset

The parent backplane is designed as a 176-lane superset so one parent accepts
LQFP48/64/100/144/176 cards. Smaller cards use the low lanes; higher lanes are NC.

| bank | lanes |
| --- | --- |
| BANK_A | CARD_LANE_001..032 |
| BANK_B | CARD_LANE_033..064 |
| BANK_C | CARD_LANE_065..096 |
| BANK_D | CARD_LANE_097..128 |
| BANK_E | CARD_LANE_129..160 |
| BANK_F | CARD_LANE_161..176 + spare/control |

Full per-lane usage matrix: `data/generated/parent_backplane_176.csv`.
"""

_PASS_STRATEGY = """# Pass strategy notes

Passes iterate from the baseline exact pinout group; each later pass enables one
more exact group and adds only the parent hardware that group needs.

**One PCB revision, many groups.** If a single physical PCB revision is intended
to support all target groups at once, the footprints must support the *union* of
every group's cell requirement from the start. Early passes may leave parts
DNI / 0Ω-populated, but no footprint may block a later group. The per-pass
`new_required_cells` / `kicad_blocks_needed` columns define that union.
"""
