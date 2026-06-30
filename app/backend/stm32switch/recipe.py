"""
recipe.py — per-pin Implementation Recipe.

Turns the explorer model into a visual, step-by-step implementation work order
(not a report): the electrical problem, the required cell, a structured hardware
path (rendered as a vector diagram by the UI), which exact groups use each
branch, the implementation sheet/zone, the nets grouped by branch, parent-router
use as route cards, and generated implementation *steps* whose statuses roll up
into the pin's status. Group ids are exposed as friendly letters (Group A, B…).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import roles as R, sheets
from .explore import ExplorerData, PinExplore
from .paths import BANK_SIZE, lane_bank

# implementation-step statuses (stored as these strings by ui.build_state)
NOT_STARTED, IN_PROGRESS, DONE, BLOCKED, NEEDS_REVIEW, NOT_NEEDED = (
    "not_started", "in_progress", "done", "blocked", "needs_review", "not_needed")

# role -> (switch label, passive label, destination template, colour key)
_ROLE_META = {
    R.IO:     ("IO switch",        "33 Ω series R", "CARD_LANE_{pin:03d}", "io"),
    R.VDD:    ("VDD load switch",  "",              "VTARGET",             "rail"),
    R.VDDA:   ("VDDA load switch", "RC filter",     "VDDA_TARGET",         "rail"),
    R.VBAT:   ("VBAT switch",      "",              "VBAT_TARGET",         "rail"),
    R.VREF:   ("VREF switch",      "RC filter",     "VREF_TARGET",         "rail"),
    R.VSS:    ("GND switch",       "",              "GND",                 "gnd"),
    R.VSSA:   ("AGND switch",      "",              "AGND",                "gnd"),
    R.VCAP:   ("VCAP switch",      "2.2 µF cap",    "C_VCAP_{pin:03d}",    "vcap"),
    R.NRST:   ("open-drain FET",   "",              "PARENT_NRST",         "service"),
    R.BOOT:   ("strap network",    "",              "PARENT_BOOT0_CTRL",   "service"),
    R.OSC_IN: ("crystal",          "load caps",     "local crystal",       "service"),
    R.OSC_OUT:("crystal",          "load caps",     "local crystal",       "service"),
}
# fixed vertical order so diagram branches never cross
_ROLE_ORDER = {r: i for i, r in enumerate(
    [R.VDD, R.VDDA, R.VBAT, R.VREF, R.IO, R.BOOT, R.NRST, R.VCAP, R.VSS, R.VSSA,
     R.OSC_IN, R.OSC_OUT])}
_SWITCHED = {R.IO, R.VDD, R.VDDA, R.VSS, R.VSSA, R.VCAP, R.VBAT, R.VREF}

_CELL_HUMAN = {
    "CELL_FULL_ROLE_SWITCH": "Full Role Switch", "CELL_IO_SWITCH": "IO Switch",
    "CELL_DIRECT_IO": "Direct IO", "CELL_POWER_ONLY": "Fixed Power",
    "CELL_GROUND_ONLY": "Fixed Ground", "CELL_VCAP_ONLY": "VCAP Local Cap",
    "CELL_USB_PAIR": "USB Pair", "CELL_BOOT_STRAP": "Boot Strap",
    "CELL_NRST_OPEN_DRAIN": "Reset (open-drain)", "CELL_OSC_LOCAL": "Local Oscillator",
    "CELL_NC": "Not Connected",
}


@dataclass
class BranchView:
    role: str
    label: str
    switch: str
    passive: str
    dest: str
    color_key: str
    group_letters: list[str]
    nets: list[str]


@dataclass
class RouteCard:
    router: str
    groups: list[str]
    lines: list[str]
    action: str


@dataclass
class ImplementationStep:
    step_id: str
    title: str
    why: str
    action: str
    nets: list[str]
    groups: list[str]
    default_status: str = NOT_STARTED
    dependencies: list[str] = field(default_factory=list)


@dataclass
class PinRecipe:
    package: str
    pin: int
    lane: str
    bank: str
    side: str
    zone: str
    sheet: str
    cell_id: str
    cell_variant: str
    component_class: str
    cell_human: str
    variant_human: str
    pass_label: str
    explanation: str
    warnings: list[str]
    branches: list[BranchView]
    nets_by_branch: list[tuple[str, list[str]]]
    parent_cards: list[RouteCard]
    packing: str
    steps: list[ImplementationStep]
    # raw, for export / database mode
    card_nets: list[str]
    ascii_lines: list[str]


# ── group display letters ───────────────────────────────────────────────────

def _excel(n: int) -> str:
    s = ""
    n += 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def group_letter(short_code: str) -> str:
    """``G009`` -> ``Group J``."""
    try:
        return "Group " + _excel(int(short_code.lstrip("G")))
    except ValueError:
        return short_code


def _short(code: str) -> str:
    return code.split("_FULL_")[-1] if "_FULL_" in code else code


def humanize_variant(variant: str) -> str:
    if variant.startswith("ROLES_"):
        return " + ".join(variant[len("ROLES_"):].split("_"))
    return variant.replace("_", " ").title()


# ── build ───────────────────────────────────────────────────────────────────

def build_recipe(ed: ExplorerData, pin: int) -> PinRecipe | None:
    pe: PinExplore | None = next((p for p in ed.pins if p.pin == pin), None)
    if pe is None:
        return None
    dec = pe.decision
    cell_id = dec.cell_id if dec else ("CELL_NC" if not pe.union_roles else "CELL_DIRECT_IO")
    variant = dec.variant if dec else ""
    comp = (dec.component_class if dec else "").replace("_", " ").title()
    common = f"SOCKET_P{pin:03d}_COMMON"

    branches = _branch_views(pe, pin)
    nets_by_branch: list[tuple[str, list[str]]] = [("Common node", [common])]
    card_nets = [common]
    for b in branches:
        nets_by_branch.append((b.label, b.nets))
        card_nets.extend(b.nets)
    if "GND" not in card_nets:
        card_nets.append("GND")

    parent_cards = _route_cards(ed, pe, pin)
    steps = _steps(pe, pin, common, branches, parent_cards)
    pass_label = ("Pass 1 · Switching Architecture"
                  if any(b.role in _SWITCHED for b in branches)
                  else "Pass 2 · Baseline Functionality")

    return PinRecipe(
        package=ed.package, pin=pin, lane=pe.lane, bank=lane_bank(pin), side=pe.side,
        zone=sheets.placement_zone(cell_id),
        sheet=sheets.card_sheet_for_pin(pin, ed.pin_count, cell_id),
        cell_id=cell_id, cell_variant=variant, component_class=comp,
        cell_human=_CELL_HUMAN.get(cell_id, cell_id), variant_human=humanize_variant(variant),
        pass_label=pass_label, explanation=_explanation(pe, branches),
        warnings=_warnings(pe), branches=branches, nets_by_branch=nets_by_branch,
        parent_cards=parent_cards, packing=_packing(pe, cell_id),
        steps=steps, card_nets=list(dict.fromkeys(card_nets)),
        ascii_lines=_ascii_lines(pe, pin),
    )


def _branch_views(pe: PinExplore, pin: int) -> list[BranchView]:
    out: list[BranchView] = []
    for b in sorted(pe.branches, key=lambda x: _ROLE_ORDER.get(x.role, 99)):
        meta = _ROLE_META.get(b.role, ("switch", "", b.role, "service"))
        sw, passive, dest_t, ck = meta
        dest = dest_t.format(pin=pin)
        letters = [group_letter(_short(g)) for g in b.used_by_groups]
        if b.role == R.IO:
            nets = [f"CARD_LANE_{pin:03d}", f"EN_P{pin:03d}_IO", "GND"]
        elif b.role == R.VCAP:
            nets = [f"C_VCAP_{pin:03d}", f"EN_P{pin:03d}_VCAP", "GND"]
        elif b.role in _SWITCHED:
            nets = [dest, f"EN_P{pin:03d}_{b.role}", "GND"]
        else:
            nets = [dest]
        out.append(BranchView(
            role=b.role, label=f"{b.role} branch", switch=sw, passive=passive,
            dest=(f"{dest} → GND" if b.role == R.VCAP else dest),
            color_key=ck, group_letters=letters, nets=nets))
    return out


def _explanation(pe: PinExplore, branches: list[BranchView]) -> str:
    if not branches:
        return "This socket pin has no assigned role for this package. Leave it not connected."
    roles = set(pe.union_roles)
    if not (R.IO in roles and roles & R.DANGEROUS_ROLES):
        b = branches[0]
        return (f"Pin {pe.pin} is {b.role} across the listed groups. "
                f"Build the single {b.label.lower()} to {b.dest}.")
    parts = [f"{', '.join(b.group_letters) or 'all groups'} use it as {b.role}"
             for b in branches]
    return ("; ".join(parts) + ". The socket pin must first enter a role-switch "
            "common node. Each rail or lane connects only when its branch is "
            "enabled, and the role-control logic must never enable two branches at once.")


def _packing(pe: PinExplore, cell_id: str) -> str:
    bank = lane_bank(pe.pin)
    idx = (pe.pin - 1) // BANK_SIZE
    lo, hi = idx * BANK_SIZE + 1, (idx + 1) * BANK_SIZE
    return (f"Logical cell: {_CELL_HUMAN.get(cell_id, cell_id)}. One independent "
            f"role cell for this pin. Physically, pins in {bank} may share "
            f"multi-channel switch ICs (IO switches together, rail switches "
            f"together) as long as every channel keeps its own enable. "
            f"Shared IC is allowed; shared enable is not. "
            f"Suggested grouping: {bank}_P{lo:03d}_P{hi:03d}.")


def _route_cards(ed: ExplorerData, pe: PinExplore, pin: int) -> list[RouteCard]:
    ports = [p for p in ed.standard_ports if p.source_pin == pin]
    by_router: dict[str, list] = {}
    for p in ports:
        by_router.setdefault(p.router_block, []).append(p)
    cards: list[RouteCard] = []
    for router, ps in by_router.items():
        letters = sorted({group_letter(_short(p.group_code)) for p in ps})
        lines = [f"{group_letter(_short(p.group_code))}: "
                 f"{p.matched_function or p.service} → {p.parent_net}" for p in ps]
        cards.append(RouteCard(
            router, letters, lines,
            f"Add {pe.lane} to {router} candidates; only activate it for the "
            f"groups that use it."))
    for r in pe.routers_for_lane:
        if r not in by_router:
            cards.append(RouteCard(r, [], [f"{pe.lane} can feed {r}."],
                                   f"Optional general access via {r}."))
    return cards


def _steps(pe: PinExplore, pin: int, common: str, branches: list[BranchView],
           route_cards: list[RouteCard]) -> list[ImplementationStep]:
    if not branches:
        return [ImplementationStep(
            f"P{pin:03d}-1", "Leave not connected",
            "No exact group uses this socket pin for this package.",
            "Do not place any components. Leave the socket pad floating (no net name).",
            [], [], NOT_NEEDED)]

    multi_branch = len(branches) > 1

    steps: list[ImplementationStep] = [ImplementationStep(
        f"P{pin:03d}-1", "Create the socket common node",
        ("All branches and the socket pad share exactly one net at the pad. "
         "Every other net in this pin's circuit connects to this node through "
         "a switch or passive, never directly to the socket pad."),
        (f"In the schematic, create net {common}. "
         f"Attach the socket pin stub to this net. "
         f"This net must not connect to any rail, lane, or enable directly."),
        [common], [])]

    i = 2
    for b in branches:
        groups_str = ", ".join(b.group_letters) or "all groups"
        en_net = f"EN_P{pin:03d}_{b.role}"
        switch_detail = (
            f" Place the {b.switch} between {common} and {b.dest}."
            + (f" Add the {b.passive} in the path." if b.passive else "")
            + (f" Name the enable net {en_net}. Default state: OFF (enable pulled low)."
               if b.role in _SWITCHED else "")
        )
        steps.append(ImplementationStep(
            f"P{pin:03d}-{i}", f"Build the {b.role} branch",
            (f"{groups_str} use pin {pin} as {b.role} (destination: {b.dest}). "
             + ("A dedicated branch is required because this role must be "
                "independently switchable from the other branches on this pin."
                if multi_branch else
                "This is the only role for this pin so no switching is needed.")),
            f"Connect {common} to {b.dest}.{switch_detail}",
            b.nets, b.group_letters))
        i += 1

    en_nets = [n for b in branches for n in b.nets if n.startswith(f"EN_P{pin:03d}_")]
    if en_nets:
        en_list = ", ".join(en_nets)
        steps.append(ImplementationStep(
            f"P{pin:03d}-{i}", "Wire the role-control enables",
            ("Each branch switch is driven by its own active-high enable net. "
             "Hardware interlock: only one enable may be asserted at a time. "
             "All enables default OFF at power-on. Asserting two enables simultaneously "
             "shorts incompatible rails or creates a bus conflict."),
            (f"Connect each enable net ({en_list}) to the controlling logic. "
             f"All enables must default low (pull-down to GND). "
             f"Add a one-hot interlock in firmware or gate logic before any enable can be "
             f"asserted. Label each net clearly on the schematic."),
            en_nets, []))
        i += 1

    if route_cards:
        router_list = ", ".join(sorted({c.router for c in route_cards}))
        grp_set = sorted({g for c in route_cards for g in c.groups})
        steps.append(ImplementationStep(
            f"P{pin:03d}-{i}", "Register parent-router candidacy",
            (f"This lane ({pe.lane}) provides a standardized parent-board service "
             f"in certain groups. The parent router ({router_list}) must know this "
             f"lane is available and route it only when the matching group is selected."),
            (f"Add {pe.lane} to the candidate list of {router_list}. "
             f"In the router configuration, activate this candidate only for "
             f"{', '.join(grp_set) or 'the applicable groups'}. "
             f"Verify the matched function and parent net assignment in the Ports tab."),
            [], grp_set, NEEDS_REVIEW))
    return steps


def _warnings(pe: PinExplore) -> list[str]:
    roles = set(pe.union_roles)
    out: list[str] = []
    dangerous = roles & R.DANGEROUS_ROLES
    if R.IO in roles and dangerous:
        out.append("This pin cannot be hardwired to its card lane. It is a "
                   "supply or return in some exact groups and must enter the "
                   "role-switch common node first.")
    if R.VSS in roles and R.IO in roles:
        out.append("Switched ground: validate the ground switch is low impedance "
                   "(engineering review required).")
    if R.VCAP in roles and R.IO in roles:
        out.append("The VCAP cap must sit behind the VCAP switch. This pin is IO "
                   "in other groups.")
    return out


def _ascii_lines(pe: PinExplore, pin: int) -> list[str]:
    lines = [f"SOCKET_P{pin:03d}_COMMON"]
    n = len(pe.branches)
    for i, b in enumerate(pe.branches):
        tee = "└──" if i == n - 1 else "├──"
        lines.append(f"    {tee} {b.switch_ref} {b.tail}")
    return lines


# ── markdown export (updated to the new structure; no test checklist) ───────

def recipe_markdown(r: PinRecipe) -> str:
    def _l(items):
        return "\n".join(f"- {i}" for i in items) if items else "_none_"
    overview = _md_table(["FIELD", "VALUE"], [
        ["Package", r.package],
        ["Pin", r.pin],
        ["Lane", r.lane],
        ["Cell", r.cell_human],
        ["Variant", r.variant_human],
        ["Component class", r.component_class],
        ["Build pass", r.pass_label],
    ])
    branches = _md_table(["ROLE", "DESTINATION", "GROUPS", "NETS"], [
        [b.role, b.dest, ", ".join(b.group_letters) or "none", ", ".join(b.nets)]
        for b in r.branches
    ])
    nets = _md_table(["BRANCH", "NETS"], [
        [label, ", ".join(ns)] for label, ns in r.nets_by_branch
    ])
    routes = _md_table(["ROUTER", "ROUTES", "GROUPS", "ACTION"], [
        [c.router, "; ".join(c.lines), ", ".join(c.groups) or "none", c.action]
        for c in r.parent_cards
    ])
    placement = _md_table(["FIELD", "VALUE"], [
        ["Sheet", r.sheet],
        ["Zone", r.zone],
        ["Bank", r.bank],
        ["Side", r.side],
        ["Packing", r.packing],
    ])
    steps_summary = _md_table(["STEP", "TITLE", "DEFAULT", "NETS", "GROUPS"], [
        [i, s.title, s.default_status, ", ".join(s.nets) or "none", ", ".join(s.groups) or "none"]
        for i, s in enumerate(r.steps, 1)
    ])
    step_sections = "\n\n---\n\n".join(
        f"### Step {i}: {s.title}\n\n"
        f"> **Status:** `{s.default_status}`"
        + (f"  |  **Groups:** {', '.join(s.groups)}" if s.groups else "")
        + f"\n\n"
        f"**Why this step exists**\n\n{s.why or 'n/a'}\n\n"
        f"**What to do**\n\n{s.action}\n\n"
        f"**Nets involved:** {', '.join(f'`{n}`' for n in s.nets) if s.nets else '_none_'}"
        for i, s in enumerate(r.steps, 1)
    )
    return f"""# Implementation Recipe: {r.package} Pin {r.pin} ({r.lane})

## Overview

{overview}

## Build Decision

### Reason

{r.explanation}

### Warnings

{_l(r.warnings)}

## Hardware Path

### Branches

{branches}

### Nets by Branch

{nets}

### Parent Routing

{routes}

## Placement

{placement}

## Implementation Steps

### Step Summary

{steps_summary}

---

{step_sections}
"""


def _md_table(headers, rows) -> str:
    def cell(value) -> str:
        text = "" if value is None else str(value)
        return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")

    header_line = "| " + " | ".join(cell(h) for h in headers) + " |"
    rule_line = "| " + " | ".join("---" for _ in headers) + " |"
    if rows:
        body = "\n".join("| " + " | ".join(cell(v) for v in row) + " |" for row in rows)
    else:
        body = "_none_"
    return "\n".join([header_line, rule_line, body])


def pass_packet_markdown(ed: ExplorerData, p) -> str:
    group = next((g for g in ed.groups if g.gid == p.to_group_id), None)
    letter = group_letter(_short(group.code)) if group else f"group {p.to_group_id}"
    members = ", ".join(group.members[:40]) if group else ""
    if group and len(group.members) > 40:
        members += f" … (+{len(group.members) - 40})"
    def _l(items):
        return "\n".join(f"- {i}" for i in items) if items else "_none_"
    return f"""# {p.pass_id}: {p.pass_type}

**Package:** {ed.package} | **Exact group:** {letter} | **MCUs gained:** \
{p.mcus_newly_enabled} | **Cumulative:** {p.cumulative_pct:.0f}%

## MCUs Supported by This Group

{members or '_none_'}

## Implementation Cells to Place

{_l(p.new_required_cells)}

## Parent Routers to Place

{_l(p.new_parent_router_candidates)}

## Affected Lanes and Pins

- Lanes: {', '.join(p.affected_lanes) or 'none'}
- Pins: {', '.join(str(x) for x in p.affected_socket_pins) or 'none'}
"""
