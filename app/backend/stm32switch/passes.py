"""
passes.py — build-pass plan from the analyzer's coverage passes (spec section 7).

The database expresses coverage as incremental exact-group passes (pass 0 is the
baseline exact group; each later pass enables one more exact group and records
the parent hardware it newly requires).  This module turns those into pass-plan
rows, classifies each pass by the concern it touches (power, reset/boot/swd,
uart, usb, adc/gpio), and attaches templated test + acceptance criteria.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import cells, grouping, io, routers

_LANE_RE = re.compile(r"CARD_LANE_(\d{3})")

# spec pass-type buckets
PASS_BASELINE       = "PASS_000_BASELINE_EXACT_GROUP"
PASS_POWER_GND      = "PASS_010_POWER_AND_GROUND_DELTAS"
PASS_VCAP_RAILS     = "PASS_020_VCAP_VBAT_VDDA_VREF_DELTAS"
PASS_RESET_BOOT_SWD = "PASS_030_RESET_BOOT_SWD_DELTAS"
PASS_UART_BOOT      = "PASS_040_UART_BOOT_DELTAS"
PASS_USB            = "PASS_050_USB_DELTAS"
PASS_ADC_GPIO       = "PASS_060_ADC_GPIO_PROBE_DELTAS"
PASS_FULL_SUPPORT   = "PASS_070_FULL_GROUP_SUPPORT"

# service token -> (concern bucket, spec cell, router)
_SERVICE_BUCKET = {
    "swdio": (PASS_RESET_BOOT_SWD, cells.CELL_IO_SWITCH, routers.ROUTER_SWD),
    "swclk": (PASS_RESET_BOOT_SWD, cells.CELL_IO_SWITCH, routers.ROUTER_SWD),
    "swo":   (PASS_RESET_BOOT_SWD, cells.CELL_IO_SWITCH, routers.ROUTER_SWD),
    "nrst":  (PASS_RESET_BOOT_SWD, cells.CELL_NRST_OPEN_DRAIN, routers.ROUTER_BOOT_RESET),
    "boot0": (PASS_RESET_BOOT_SWD, cells.CELL_BOOT_STRAP, routers.ROUTER_BOOT_RESET),
    "uart_tx": (PASS_UART_BOOT, cells.CELL_IO_SWITCH, routers.ROUTER_UART_BOOT),
    "uart_rx": (PASS_UART_BOOT, cells.CELL_IO_SWITCH, routers.ROUTER_UART_BOOT),
    "usb_dp":  (PASS_USB, cells.CELL_USB_PAIR, routers.ROUTER_USB_FS),
    "usb_dm":  (PASS_USB, cells.CELL_USB_PAIR, routers.ROUTER_USB_FS),
}

# bucket ordering for choosing a pass's primary type (lowest wins).
_BUCKET_ORDER = [
    PASS_POWER_GND, PASS_VCAP_RAILS, PASS_RESET_BOOT_SWD,
    PASS_UART_BOOT, PASS_USB, PASS_ADC_GPIO,
]

_TEST = {
    PASS_BASELINE: ("Populate baseline card + parent. Insert a baseline-group MCU. "
                    "Verify VTARGET sequencing, SWD enumeration, and that every "
                    "role-switch cell powers up in its safe_default (all-off).",
                    "Baseline-group MCU enumerates over SWD with no role-switch "
                    "enabled; no lane sources power into the parent."),
    PASS_RESET_BOOT_SWD: ("Insert an MCU from this group. Select its profile; verify "
                          "SWDIO/SWCLK/SWO and NRST route to the standardized parent "
                          "nets via the added switches.",
                          "Debugger attaches; reset asserts open-drain only; no "
                          "push-pull drive of NRST high."),
    PASS_UART_BOOT: ("Select the group profile; verify the boot UART maps "
                     "MCU_TX→PARENT_UART_RX_FROM_TARGET and the reverse.",
                     "Bootloader handshake completes on the standardized UART nets."),
    PASS_USB: ("Verify USB D+/D- route through the USB-rated differential switch "
               "as a 90Ω matched pair to PARENT_USB_DP/DM.",
               "Host enumerates the target as USB-FS; eye is within spec."),
    PASS_ADC_GPIO: ("Verify analog-capable lanes reach PARENT_ADC_PROBE through the "
                    "low-leakage mux and safe IO lanes reach the GPIO/LA headers.",
                    "Probe reads expected analog level; no digital coupling onto the "
                    "analog probe net."),
    PASS_POWER_GND: ("Verify the added supply/return role paths for this group.",
                     "No VDD/VSS path is ever co-enabled with an IO path."),
    PASS_VCAP_RAILS: ("Verify VCAP/VBAT/VDDA/VREF role paths and local caps.",
                      "VCAP cap stays local to the socket; never exposed as a lane."),
    PASS_FULL_SUPPORT: ("Regression-test every supported exact group on one board "
                        "revision using DNI/0Ω population options.",
                        "All target groups pass their per-pass criteria on a single "
                        "PCB revision."),
}


@dataclass
class PassInfo:
    pass_id: str
    package: str
    pass_number: int
    pass_type: str
    from_group_id: int
    to_group_id: int
    is_baseline: bool
    mcus_newly_enabled: int
    cumulative_mcu_count: int
    cumulative_pct: float
    affected_lanes: list[str] = field(default_factory=list)
    affected_socket_pins: list[int] = field(default_factory=list)
    new_required_cells: list[str] = field(default_factory=list)
    changed_required_cells: list[str] = field(default_factory=list)
    new_parent_router_candidates: list[str] = field(default_factory=list)
    kicad_blocks_needed: list[str] = field(default_factory=list)
    power_safety_impact: str = ""
    signal_integrity_impact: str = ""
    test_procedure: str = ""
    acceptance_criteria: str = ""
    additions: list[dict] = field(default_factory=list)


def _baseline_cells() -> list[str]:
    return [
        cells.CELL_FULL_ROLE_SWITCH, cells.CELL_DIRECT_IO, cells.CELL_IO_SWITCH,
        cells.CELL_POWER_ONLY, cells.CELL_GROUND_ONLY, cells.CELL_VCAP_ONLY,
        cells.CELL_OSC_LOCAL,
    ]


def build_passes(conn, package: str,
                 groups: list[grouping.GroupInfo] | None = None) -> list[PassInfo]:
    if groups is None:
        groups = grouping.load_groups(conn, package)
    by_gid = {g.gid: g for g in groups}
    cov = io.coverage_passes(conn, package)
    adds = io.pass_additions(conn, package)
    adds_by_pass: dict[int, list[dict]] = {}
    for a in adds:
        adds_by_pass.setdefault(int(a["pass_number"]), []).append(a)

    last_idx = len(cov) - 1
    out: list[PassInfo] = []
    for i, c in enumerate(cov):
        pnum = int(c["pass_number"])
        is_base = bool(c["is_baseline"]) or pnum == 0
        gid = int(c["mcu_group_id"])
        group = by_gid.get(gid)
        my_adds = adds_by_pass.get(pnum, [])

        # services touched this pass
        services = sorted({str(a["service_name"]) for a in my_adds})
        buckets, new_cells, new_routers = set(), [], []
        lanes = set(group.affected_lanes if group else [])
        for a in my_adds:
            lanes.add(str(a["lane_name"]))
            b = _SERVICE_BUCKET.get(str(a["service_name"]))
            if b:
                buckets.add(b[0]); new_cells.append(b[1]); new_routers.append(b[2])

        if is_base:
            ptype = PASS_BASELINE
            new_cells = _baseline_cells()
            new_routers = list(routers.ALL_ROUTERS)
        elif i == last_idx and float(c["cumulative_pct"]) >= 99.5:
            ptype = PASS_FULL_SUPPORT
        else:
            ptype = next((b for b in _BUCKET_ORDER if b in buckets), PASS_ADC_GPIO)

        lanes_sorted = sorted(lanes)
        pins = sorted({int(m.group(1)) for ln in lanes_sorted
                       for m in [_LANE_RE.search(ln)] if m})
        new_cells = list(dict.fromkeys(new_cells))
        new_routers = list(dict.fromkeys(new_routers))
        blocks = list(dict.fromkeys(new_cells + new_routers))
        test, accept = _TEST.get(ptype, _TEST[PASS_ADC_GPIO])

        out.append(PassInfo(
            pass_id=f"{package}_PASS_{pnum:03d}",
            package=package,
            pass_number=pnum,
            pass_type=ptype,
            from_group_id=-1 if is_base else (out[-1].to_group_id if out else gid),
            to_group_id=gid,
            is_baseline=is_base,
            mcus_newly_enabled=int(c["mcus_newly_enabled"]),
            cumulative_mcu_count=int(c["cumulative_mcu_count"]),
            cumulative_pct=float(c["cumulative_pct"]),
            affected_lanes=lanes_sorted,
            affected_socket_pins=pins,
            new_required_cells=new_cells,
            changed_required_cells=[],
            new_parent_router_candidates=new_routers,
            kicad_blocks_needed=blocks,
            power_safety_impact=(
                "Baseline establishes all-off safe defaults; role switching only "
                "while VTARGET is off." if is_base else
                "No new always-on power path; additions are signal switches."),
            signal_integrity_impact=(
                "Controlled-impedance USB pair." if ptype == PASS_USB else
                "Low-capacitance switch on debug/clock lanes."
                if ptype == PASS_RESET_BOOT_SWD else
                "Standard series-protected signal."),
            test_procedure=test,
            acceptance_criteria=accept,
            additions=my_adds,
        ))
    return out
