"""
validate.py — safety validation of the generated design data (spec section 16).

Hard errors are electrical-safety violations (the headline one being a pin the
legacy DB marked ``hardwired_io`` whose full role set actually requires a role
switch).  Warnings are review items (switched ground, unknown roles, broad
router candidate lists, singleton groups).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import cells, roles as R, routers
from .normalize import PackageData

# Router candidate counts above this fraction of pins are "broad" (warned).
# Dedicated services (SWD/USB/UART-boot/reset) must stay tight; the general
# ADC/GPIO access routers are allowed to be somewhat broader by design.
_ROUTER_BROAD_FRACTION = {
    routers.ROUTER_SWD: 0.20, routers.ROUTER_USB_FS: 0.20,
    routers.ROUTER_BOOT_RESET: 0.15, routers.ROUTER_UART_BOOT: 0.22,
    routers.ROUTER_ADC_PROBE: 0.35, routers.ROUTER_GPIO_ACCESS: 0.50,
}


@dataclass
class Finding:
    severity: str   # "error" | "warning"
    code: str
    where: str      # pin/lane/router context
    message: str


@dataclass
class ValidationReport:
    package: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def pin_error_map(self) -> dict[int, str]:
        out: dict[int, list[str]] = {}
        for f in self.findings:
            if f.severity == "error" and f.where.startswith("pin "):
                try:
                    pin = int(f.where.split()[1])
                except (IndexError, ValueError):
                    continue
                out.setdefault(pin, []).append(f.code)
        return {p: ",".join(v) for p, v in out.items()}


def validate_package(pd: PackageData) -> ValidationReport:
    rep = ValidationReport(package=pd.package)
    npins = max(1, len(pd.facts))

    for f in pd.facts:
        roles = f.role_set
        cid = f.decision.cell_id
        where = f"pin {f.pin} ({f.lane})"

        # ── HARD ERRORS ────────────────────────────────────────────────────
        if f.db_unsafe:
            rep.findings.append(Finding(
                "error", "UNSAFE_DB_CLASSIFICATION", where,
                f"legacy DB cell_kind='{f.db_cell_kind}' but role set "
                f"{{{R.role_set_str(roles)}}} requires {cid}"))
        if R.IO in roles and (R.VDD in roles or R.VSS in roles) and \
                cid == cells.CELL_DIRECT_IO:
            rep.findings.append(Finding(
                "error", "IO_POWER_HARDWIRED", where,
                "role set mixes IO with VDD/VSS but cell is CELL_DIRECT_IO"))
        if R.IO in roles and R.VCAP in roles and cid == cells.CELL_DIRECT_IO:
            rep.findings.append(Finding(
                "error", "IO_VCAP_HARDWIRED", where,
                "role set mixes IO with VCAP but cell is CELL_DIRECT_IO"))
        if R.VCAP in roles and cid not in (cells.CELL_VCAP_ONLY, cells.CELL_FULL_ROLE_SWITCH):
            rep.findings.append(Finding(
                "error", "VCAP_NO_LOCAL_CAP", where,
                f"VCAP role but cell {cid} provides no local capacitor path"))
        if (R.USB_DP in roles or R.USB_DM in roles or (f.services & {"usb_dp", "usb_dm"})) \
                and cid not in (cells.CELL_USB_PAIR, cells.CELL_FULL_ROLE_SWITCH):
            rep.findings.append(Finding(
                "error", "USB_ON_GENERIC_IO", where,
                f"USB lane routed via {cid} instead of a USB-rated pair cell"))

        # ── WARNINGS ───────────────────────────────────────────────────────
        if cells.FLAG_SWITCHED_GROUND in f.review_flags:
            rep.findings.append(Finding(
                "warning", "SWITCHED_GROUND", where,
                "lane requires a switched ground path — engineering review required"))
        if R.UNKNOWN in roles:
            rep.findings.append(Finding(
                "warning", "ROLE_UNKNOWN", where, "lane has an UNKNOWN role token"))
        if cells.FLAG_ANALOG_AND_HS in f.review_flags:
            rep.findings.append(Finding(
                "warning", "ANALOG_AND_HIGH_SPEED", where,
                "lane is both analog-sensitive and high-speed-sensitive"))

    # ── router breadth + structural self-checks ────────────────────────────
    # router_tables is part of the original generator's fuller model; the app's
    # folded pipeline does not build it, so these breadth warnings only run when
    # the tables are present.
    router_tables = getattr(pd, "router_tables", None)
    if router_tables:
        for rid in routers.ALL_ROUTERS:
            t = router_tables[rid]
            limit = int(_ROUTER_BROAD_FRACTION.get(rid, 0.5) * npins) + 1
            if t.lane_count > limit:
                rep.findings.append(Finding(
                    "warning", "ROUTER_CANDIDATES_BROAD", rid,
                    f"{t.lane_count} candidate lanes (> expected ~{limit})"))
    if routers.ROUTER_SWITCH_CLASS[routers.ROUTER_SWD] != "low_capacitance_bidirectional":
        rep.findings.append(Finding("error", "SWD_NOT_BIDIRECTIONAL",
                                    routers.ROUTER_SWD, "SWD router not bidirectional"))
    if "open_drain" not in routers.ROUTER_SWITCH_CLASS[routers.ROUTER_BOOT_RESET]:
        rep.findings.append(Finding("error", "NRST_PUSH_PULL",
                                    routers.ROUTER_BOOT_RESET, "NRST not open-drain"))

    # ── baseline + group sanity ────────────────────────────────────────────
    from . import grouping
    base = grouping.baseline_group(pd.groups)
    if base is None or base.member_count <= 0:
        rep.findings.append(Finding("error", "NO_EXACT_BASELINE", pd.package,
                                    "no exact pinout baseline group found"))
    for g in pd.groups:
        if g.member_count == 1:
            rep.findings.append(Finding("warning", "SINGLETON_GROUP", g.code,
                                        "exact pinout group has a single MCU member"))

    # ── exact-function validation of parent standardized ports ─────────────
    # standard_ports is likewise only present in the fuller generator model.
    for p in getattr(pd, "standard_ports", None) or []:
        if p.exact_function_validated == "no":
            rep.findings.append(Finding(
                "error", "PARENT_PORT_NO_EXACT_FUNCTION",
                f"{p.group_code}/{p.parent_net}",
                f"{p.parent_net} → {p.source_lane} carries no exact {p.service} "
                f"function (has: {p.lane_functions or 'none'})"))

    # ── voltage branch coverage ────────────────────────────────────────────
    v = pd.voltage
    def _has_role(tok: str) -> bool:
        return any(tok in f.role_set for f in pd.facts)
    for tok, key, net in (("VDDA", "vdda_target_required", "VDDA_TARGET"),
                          ("VREF", "vref_target_required", "VREF_TARGET"),
                          ("VBAT", "vbat_target_required", "VBAT_TARGET"),
                          ("VCAP", "vcap_branch_required", "VCAP_LOCAL")):
        if _has_role(tok) and not v.get(key):
            rep.findings.append(Finding(
                "error", "VOLTAGE_BRANCH_MISSING", pd.package,
                f"{tok} pins present but {net} branch not in the voltage plan"))
    if v.get("vtarget_min_v") is None:
        rep.findings.append(Finding(
            "warning", "VTARGET_RANGE_UNKNOWN", pd.package,
            "VTARGET voltage range required for hardware planning is missing"))
    return rep


# ── summary counts (spec section 17) ───────────────────────────────────────

def summary_counts(pd: PackageData) -> dict:
    cell_counts = Counter(f.decision.cell_id for f in pd.facts)
    switched_gnd = sum(1 for f in pd.facts
                       if cells.FLAG_SWITCHED_GROUND in f.review_flags)
    from . import grouping
    base = grouping.baseline_group(pd.groups)
    full = cell_counts.get(cells.CELL_FULL_ROLE_SWITCH, 0)
    direct_io = cell_counts.get(cells.CELL_DIRECT_IO, 0)
    io_switch = cell_counts.get(cells.CELL_IO_SWITCH, 0)
    power = cell_counts.get(cells.CELL_POWER_ONLY, 0)
    ground = cell_counts.get(cells.CELL_GROUND_ONLY, 0)
    role_control_bits = full * 4   # 4-bit one-hot role code per full role cell
    rtables = getattr(pd, "router_tables", None) or {}
    def _cand(rid) -> int:
        t = rtables.get(rid)
        return t.lane_count if t is not None else 0
    return {
        "lanes": len(pd.facts),
        "exact_groups": len(pd.groups),
        "baseline_group": base.code if base else "—",
        "baseline_members": base.member_count if base else 0,
        "full_role_switch": full,
        "direct_io": direct_io,
        "io_switch": io_switch,
        "power": power,
        "ground": ground,
        "vcap": cell_counts.get(cells.CELL_VCAP_ONLY, 0),
        "osc": cell_counts.get(cells.CELL_OSC_LOCAL, 0),
        "usb": cell_counts.get(cells.CELL_USB_PAIR, 0),
        "boot": cell_counts.get(cells.CELL_BOOT_STRAP, 0),
        "nrst": cell_counts.get(cells.CELL_NRST_OPEN_DRAIN, 0),
        "nc": cell_counts.get(cells.CELL_NC, 0),
        # control architecture (spec §11): one-hot 4-bit role code per role cell,
        # chained through 8-bit shift-register/latch banks (config only changes
        # while target power is off).
        "role_control_bits": role_control_bits,
        "shift_register_banks_8bit": (role_control_bits + 7) // 8,
        "simple_io_cells": direct_io + io_switch,
        "fixed_power_ground_cells": power + ground,
        "switched_ground_review": switched_gnd,
        "swd_candidates": _cand(routers.ROUTER_SWD),
        "uart_candidates": _cand(routers.ROUTER_UART_BOOT),
        "usb_candidates": _cand(routers.ROUTER_USB_FS),
        "adc_candidates": _cand(routers.ROUTER_ADC_PROBE),
        "gpio_candidates": _cand(routers.ROUTER_GPIO_ACCESS),
        "unsafe_db_classifications": sum(1 for f in pd.facts if f.db_unsafe),
    }
