"""
explore.py — assemble the per-pin "engineering explorer" model.

For each physical socket pin this builds:
  * what every exact pinout group uses it for (pin name, electrical role, the
    exact functions — never broadened),
  * the universal hardware cell (the set of parallel branches the card must
    build so the pin is safe for every group),
  * the parent standard ports each group routes through it.

A group shares an *exact* pinout, so a single representative member describes
the whole group's pin map.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cells, fdata, roles as R, rules
from .normalize import PackageData
from .paths import lane_id as _lane_id

# Branch rendering: role -> (switch ref suffix, tail toward its destination net)
BRANCH_TAIL = {
    R.IO:     ("U{n}_IO",   "── R{n}_IO 33R ──▶ CARD_LANE_{lane:03d}"),
    R.VDD:    ("U{n}_VDD",  "──────────────▶ VTARGET"),
    R.VDDA:   ("U{n}_VDDA", "──────────────▶ VDDA_TARGET"),
    R.VSS:    ("U{n}_GND",  "──────────────▶ GND"),
    R.VSSA:   ("U{n}_VSSA", "──────────────▶ AGND"),
    R.VCAP:   ("U{n}_VCAP", "── C{n}_VCAP 2.2uF ──▶ GND"),
    R.VBAT:   ("U{n}_VBAT", "──────────────▶ VBAT_TARGET"),
    R.VREF:   ("U{n}_VREF", "──────────────▶ VREF_TARGET"),
    R.NRST:   ("U{n}_NRST", "── open-drain ──▶ PARENT_NRST"),
    R.BOOT:   ("U{n}_BOOT", "── strap ──▶ PARENT_BOOT0_CTRL"),
    R.OSC_IN: ("Y{n}",      "── local crystal + load caps (kept on card)"),
    R.OSC_OUT:("Y{n}",      "── local crystal + load caps (kept on card)"),
}
# Order branches are drawn in.
BRANCH_ORDER = [R.IO, R.VDD, R.VDDA, R.VSS, R.VSSA, R.VCAP, R.VBAT, R.VREF,
                R.NRST, R.BOOT, R.OSC_IN, R.OSC_OUT]

# Per-pin category tag for the package map.
TAG_RS, TAG_PWR, TAG_USB, TAG_SWD, TAG_ANA, TAG_IO, TAG_NC = (
    "RS", "PWR", "USB", "SWD", "ANA", "IO", "NC")


@dataclass
class GroupObs:
    group_code: str
    member_count: int
    pin_name: str
    role: str
    functions: list[str]


@dataclass
class Branch:
    role: str
    switch_ref: str
    tail: str
    used_by_groups: list[str]
    supports: list[str]
    note: str = ""


@dataclass
class PinExplore:
    pin: int
    lane: str
    side: str
    tag: str
    observations: list[GroupObs]
    union_roles: list[str]
    universal_cell: str
    branches: list[Branch] = field(default_factory=list)
    review: bool = False
    # enrichment for the Build Recipe (sourced from normalize.PinFacts)
    decision: object = None              # cells.CellDecision
    services: list[str] = field(default_factory=list)
    exact_functions: list[str] = field(default_factory=list)
    routers_for_lane: list[str] = field(default_factory=list)
    design: dict = field(default_factory=dict)   # rules.pin_design_fields (single model)


@dataclass
class ExplorerData:
    package: str
    pin_count: int
    counts: dict
    voltage: dict
    groups: list
    passes: list
    standard_ports: list
    pins: list[PinExplore]
    group_pin_maps: dict          # group_code -> {pin: detail dict}
    rep_part: dict                # group_code -> representative part number


def _role_from(name: str | None, eclass: str | None) -> str:
    ec = (eclass or "").lower()
    if ec == "io":         return R.IO
    if ec == "vcap":       return R.VCAP
    if ec == "reset":      return R.NRST
    if ec == "boot":       return R.BOOT
    if ec == "oscillator": return R.OSC_IN
    if ec == "nc":         return R.NC
    up = (name or "").upper()
    if ec == "ground":
        return R.VSSA if "VSSA" in up else R.VSS
    if ec == "power":
        if "VDDA" in up: return R.VDDA
        if "VBAT" in up: return R.VBAT
        if "VREF" in up: return R.VREF
        return R.VDD
    return R.normalize_role(name)


def _universal_cell(union: set[str]) -> str:
    rs = union - {R.NC}
    if not rs:
        return "NC"
    if rs == {R.IO}:
        return "DIRECT_IO"
    dangerous = rs & R.DANGEROUS_ROLES
    if R.IO in rs and len(dangerous) >= 3:
        return "FULL_ROLE_SWITCH"
    return "PIN_" + "_".join(sorted(rs))


def _tag(union: set[str], services: set[str], is_analog: bool, cell_id: str) -> str:
    if cell_id == cells.CELL_FULL_ROLE_SWITCH:
        return TAG_RS
    if union <= {R.NC} or not union:
        return TAG_NC
    if (union & (R.POWER_ROLES | R.GROUND_ROLES | {R.VCAP})) and R.IO not in union:
        return TAG_PWR
    if services & {"usb_dp", "usb_dm"}:
        return TAG_USB
    if services & {"swdio", "swclk", "swo"}:
        return TAG_SWD
    if is_analog:
        return TAG_ANA
    return TAG_IO


def build_explorer(conn, package: str, pd: PackageData) -> ExplorerData:
    """Build the per-pin explorer model from the STM32F-only PackageData.

    Sourced entirely from the recomputed F-only model: per-group pin detail
    comes from each group's representative MCU; passes / standard-ports are not
    part of the F-only model and are returned empty.
    """
    groups = pd.groups
    rep_part = {g.code: g.rep_part for g in groups}
    group_maps = {g.code: fdata.pin_detail_for_mcu(conn, g.rep_mcu_id) for g in groups}

    facts_by_pin = {f.pin: f for f in pd.facts}
    pins: list[PinExplore] = []
    for pin in range(1, pd.pin_count + 1):
        obs: list[GroupObs] = []
        for g in groups:
            d = group_maps.get(g.code, {}).get(pin)
            if d is None:
                continue
            obs.append(GroupObs(g.code, g.member_count, d["name"] or "",
                                _role_from(d["name"], d["eclass"]), d["functions"]))
        union = {o.role for o in obs}
        f = facts_by_pin.get(pin)
        if not union and f:
            union = set(f.role_set)
        branches = _branches(pin, union, obs)
        review = R.VSS in union and R.IO in union
        services = f.services if f else set()
        is_analog = bool(f.caps.get("has_analog")) if f else False
        cell_id = f.decision.cell_id if f else ""
        exact_fns = sorted({fn for o in obs for fn in o.functions})
        ctx = pd.contexts.get(pin)
        design = rules.pin_design_fields(ctx) if ctx is not None else {}
        pins.append(PinExplore(
            pin=pin, lane=_lane_id(pin),
            side=f.side if f else "",
            tag=_tag(union, services, is_analog, cell_id),
            observations=obs, union_roles=sorted(union),
            universal_cell=_universal_cell(union), branches=branches, review=review,
            decision=(f.decision if f else None),
            services=sorted(services), exact_functions=exact_fns,
            routers_for_lane=list(f.routers_for_lane) if f else [],
            design=design,
        ))

    return ExplorerData(
        package=package, pin_count=pd.pin_count,
        counts=_summary_counts(pd), voltage=_enrich_voltage(pd.voltage),
        groups=groups, passes=[], standard_ports=[],
        pins=pins, group_pin_maps=group_maps, rep_part=rep_part,
    )


def _enrich_voltage(v: dict) -> dict:
    """Add the derived keys the UI overview/voltage tabs read."""
    vmin, vmax = v.get("vtarget_min_mv"), v.get("vtarget_max_mv")
    rng = f"{vmin/1000:.2f}–{vmax/1000:.2f} V" if vmin and vmax else "?"
    return {**v, "vtarget_range": rng,
            "vdda_target_required": v.get("vdda_pins", 0) > 0,
            "vref_target_required": v.get("vref_pins", 0) > 0,
            "vbat_target_required": v.get("vbat_pins", 0) > 0,
            "vcap_branch_required": v.get("vcap_pins", 0) > 0}


def _summary_counts(pd: PackageData) -> dict:
    """Package-level tallies the overview cards read (F-only)."""
    facts = pd.facts
    def n(cid):
        return sum(1 for f in facts if f.decision.cell_id == cid)
    baseline = next((g for g in pd.groups if g.is_baseline), None)
    # Rules-based design per pin — the single source of truth shared with the
    # STM32F Matrix tab and the canonical CSV (keeps every view consistent).
    designs = {f.pin: rules.pin_design_fields(pd.contexts[f.pin]) for f in facts}
    victim_cells: dict[str, int] = {}
    for d in designs.values():
        victim_cells[d["victim_card_cell_display_name"]] = \
            victim_cells.get(d["victim_card_cell_display_name"], 0) + 1
    return {
        "baseline_members": baseline.member_count if baseline else 0,
        "exact_groups": len(pd.groups),
        # legacy cell-decision tallies (overview cards)
        "full_role_switch": n(cells.CELL_FULL_ROLE_SWITCH),
        "direct_io": n(cells.CELL_DIRECT_IO),
        "io_switch": n(cells.CELL_IO_SWITCH),
        "power": n(cells.CELL_POWER_ONLY),
        "ground": n(cells.CELL_GROUND_ONLY),
        "vcap": n(cells.CELL_VCAP_ONLY),
        "usb": n(cells.CELL_USB_PAIR),
        "switched_ground_review": sum(
            1 for f in facts if R.IO in f.role_set and (f.role_set & R.GROUND_ROLES)),
        "role_control_bits": sum(
            len(f.role_set - {R.NC}) for f in facts
            if f.decision.cell_id == cells.CELL_FULL_ROLE_SWITCH),
        "unsafe_db_classifications": 0,
        # rules-based, Matrix-consistent tallies
        "switching_needed": sum(1 for d in designs.values()
                                if d["needs_victim_card_switching"] == "yes"),
        "guaranteed_pins": sum(1 for d in designs.values()
                               if d["needs_victim_card_switching"] == "no"),
        "helios_controlled": sum(1 for d in designs.values()
                                 if d["needs_helios_control"] == "yes"),
        "unsafe_pins": sum(1 for d in designs.values()
                           if d["safety_class"] == "UNSAFE_DIRECT"),
        "victim_cells": victim_cells,
    }


def _branches(pin: int, union: set[str], obs: list[GroupObs]) -> list[Branch]:
    out: list[Branch] = []
    for role in BRANCH_ORDER:
        if role not in union:
            continue
        ref_t, tail_t = BRANCH_TAIL[role]
        users = [o.group_code for o in obs if o.role == role]
        if role == R.IO:
            supports = sorted({fn for o in obs if o.role == R.IO for fn in o.functions})
        else:
            supports = [role]
        note = ""
        if role in R.GROUND_ROLES and R.IO in union:
            note = "switched ground — low-R path, engineering review"
        if role == R.VCAP:
            note = "cap close to socket; never exposed as a lane"
        out.append(Branch(
            role=role, switch_ref=ref_t.format(n=f"{pin:03d}"),
            tail=tail_t.format(n=f"{pin:03d}", lane=pin),
            used_by_groups=users or ["(package-aggregate)"],
            supports=supports, note=note,
        ))
    return out
