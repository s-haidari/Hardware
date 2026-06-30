"""
routers.py — sparse parent-side service routers (spec section 12).

A parent service router only connects the *known candidate lanes* for that
service, never a full crosspoint.  Candidate lanes come from the per-MCU service
assignments (``profile_service_lane``) plus the safe-IO lanes for general GPIO
access.  The parent always exposes standardized nets; it never wires a raw lane
straight to a user-facing function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cells, io

# ── router definitions ─────────────────────────────────────────────────────

ROUTER_SWD          = "ROUTER_SWD"
ROUTER_UART_BOOT    = "ROUTER_UART_BOOT"
ROUTER_USB_FS       = "ROUTER_USB_FS"
ROUTER_ADC_PROBE    = "ROUTER_ADC_PROBE"
ROUTER_GPIO_ACCESS  = "ROUTER_GPIO_ACCESS"
ROUTER_BOOT_RESET   = "ROUTER_BOOT_RESET"

ALL_ROUTERS = [
    ROUTER_SWD, ROUTER_UART_BOOT, ROUTER_USB_FS,
    ROUTER_ADC_PROBE, ROUTER_GPIO_ACCESS, ROUTER_BOOT_RESET,
]

# Service token → router that owns it.
SERVICE_TO_ROUTER: dict[str, str] = {
    "swdio": ROUTER_SWD, "swclk": ROUTER_SWD, "swo": ROUTER_SWD,
    "uart_tx": ROUTER_UART_BOOT, "uart_rx": ROUTER_UART_BOOT,
    "usb_dp": ROUTER_USB_FS, "usb_dm": ROUTER_USB_FS,
    "nrst": ROUTER_BOOT_RESET, "boot0": ROUTER_BOOT_RESET,
}

# Standardized parent output nets per router (spec section 0/12).
ROUTER_OUTPUTS: dict[str, list[str]] = {
    ROUTER_SWD:         ["PARENT_SWDIO", "PARENT_SWCLK", "PARENT_SWO"],
    ROUTER_UART_BOOT:   ["PARENT_UART_RX_FROM_TARGET", "PARENT_UART_TX_TO_TARGET"],
    ROUTER_USB_FS:      ["PARENT_USB_DP", "PARENT_USB_DM"],
    ROUTER_ADC_PROBE:   ["PARENT_ADC_PROBE"],
    ROUTER_GPIO_ACCESS: ["PARENT_GPIO_PROBE", "PARENT_LOGIC_ANALYZER_BUS"],
    ROUTER_BOOT_RESET:  ["PARENT_NRST", "PARENT_BOOT0_CTRL"],
}

# Switch sensitivity requirement per router (spec section 12).
ROUTER_SWITCH_CLASS: dict[str, str] = {
    ROUTER_SWD:         "low_capacitance_bidirectional",
    ROUTER_UART_BOOT:   "vtarget_compatible_signal",
    ROUTER_USB_FS:      "usb_rated_differential",
    ROUTER_ADC_PROBE:   "low_leakage_analog",
    ROUTER_GPIO_ACCESS: "general_bidirectional",
    ROUTER_BOOT_RESET:  "open_drain_and_strap",
}


@dataclass
class RouterCandidate:
    router: str
    lane: str
    pin: int
    service: str
    side: str = ""


@dataclass
class RouterTable:
    router: str
    outputs: list[str]
    switch_class: str
    candidates: list[RouterCandidate] = field(default_factory=list)

    @property
    def lane_count(self) -> int:
        return len({c.lane for c in self.candidates})


def routers_for_services(services: set[str]) -> set[str]:
    out = {SERVICE_TO_ROUTER[s] for s in services if s in SERVICE_TO_ROUTER}
    # analog probe candidacy is capability-driven, handled in build_router_tables.
    return out


def build_router_tables(conn, package: str, pins: list[dict] | None = None) -> dict[str, RouterTable]:
    """Assemble the sparse candidate-lane table for every parent router.

    Routers are kept as narrow as the data allows:
      * SWD / USB / BOOT_RESET come straight from the per-MCU service lanes.
      * UART_BOOT uses only the *bootloader* UART (not every UART usage).
      * ADC_PROBE / GPIO_ACCESS only receive the *leftover* plain safe-IO lanes
        — lanes already owned by a dedicated service router, role-switch lanes,
        and power/ground/osc/vcap/nc lanes are excluded.
    """
    if pins is None:
        pins = io.package_pins(conn, package)
    services = io.pin_services(conn, package)
    boot_uart = io.boot_uart_lanes(conn, package)
    caps = io.pin_capabilities(conn, package)

    tables = {
        r: RouterTable(router=r, outputs=ROUTER_OUTPUTS[r],
                       switch_class=ROUTER_SWITCH_CLASS[r])
        for r in ALL_ROUTERS
    }
    by_pin = {int(p["pin"]): p for p in pins}

    def _lane(pin: int) -> tuple[str, str]:
        p = by_pin.get(pin, {})
        return p.get("lane") or f"CARD_LANE_{pin:03d}", p.get("side") or ""

    # SWD / USB / BOOT_RESET — dedicated services (UART handled via boot rule).
    for pin, svcs in sorted(services.items()):
        lane, side = _lane(pin)
        for svc in sorted(svcs):
            router = SERVICE_TO_ROUTER.get(svc)
            if router and router != ROUTER_UART_BOOT:
                tables[router].candidates.append(
                    RouterCandidate(router, lane, pin, svc, side))

    # UART_BOOT — strictly the bootloader UART candidate lanes.
    for pin, svcs in sorted(boot_uart.items()):
        lane, side = _lane(pin)
        for svc in sorted(svcs):
            tables[ROUTER_UART_BOOT].candidates.append(
                RouterCandidate(ROUTER_UART_BOOT, lane, pin, svc, side))

    # Lanes already owned by a specific service router are not "general access".
    claimed = {c.lane for rid in (ROUTER_SWD, ROUTER_USB_FS, ROUTER_UART_BOOT,
                                  ROUTER_BOOT_RESET) for c in tables[rid].candidates}

    # ADC_PROBE / GPIO_ACCESS — only leftover plain safe-IO lanes.
    for p in pins:
        pin = int(p["pin"])
        lane, side = _lane(pin)
        if lane in claimed:
            continue
        cap = caps.get(pin, {})
        dec = cells.classify_cell(
            _role_set(p), services.get(pin, set()),
            is_analog=bool(cap.get("has_analog")),
            is_high_speed=bool(cap.get("has_usb") or cap.get("has_ethernet")),
        )
        if dec.cell_id not in (cells.CELL_DIRECT_IO, cells.CELL_IO_SWITCH):
            continue  # exclude role-switch / power / ground / osc / vcap / nc
        tables[ROUTER_GPIO_ACCESS].candidates.append(
            RouterCandidate(ROUTER_GPIO_ACCESS, lane, pin, "gpio", side))
        if cap.get("has_analog"):
            tables[ROUTER_ADC_PROBE].candidates.append(
                RouterCandidate(ROUTER_ADC_PROBE, lane, pin, "adc_probe", side))

    for t in tables.values():
        t.candidates.sort(key=lambda c: (c.pin, c.service))
    return tables


def _role_set(pin_row: dict) -> set[str]:
    from . import roles as R
    return R.normalize_role_set(pin_row.get("role_set"))
