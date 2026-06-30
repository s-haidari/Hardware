"""
ports.py — parent standardized service ports, validated against exact functions.

For each exact pinout group, map every standardized parent net (PARENT_SWDIO,
PARENT_UART_RX_FROM_TARGET, ...) to the CARD_LANE the group actually uses for
that service, and verify the lane really carries the matching *exact* function
(e.g. PARENT_SWDIO must land on a pin whose functions include SYS_JTMS-SWDIO).
The parent never exposes a raw lane as a user-facing function.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import funcs

# (service, parent_net, direction, router_block)
# UART honours the crossover naming rule: MCU TX -> parent RX, parent TX -> MCU RX.
STANDARD_PORTS = [
    ("swdio",   "PARENT_SWDIO",               "bidirectional", "ROUTER_SWD"),
    ("swclk",   "PARENT_SWCLK",               "input",         "ROUTER_SWD"),
    ("swo",     "PARENT_SWO",                 "output",        "ROUTER_SWD"),
    ("nrst",    "PARENT_NRST",                "open_drain",    "ROUTER_BOOT_RESET"),
    ("boot0",   "PARENT_BOOT0_CTRL",          "output",        "ROUTER_BOOT_RESET"),
    ("uart_tx", "PARENT_UART_RX_FROM_TARGET", "input",         "ROUTER_UART_BOOT"),
    ("uart_rx", "PARENT_UART_TX_TO_TARGET",   "output",        "ROUTER_UART_BOOT"),
    ("usb_dp",  "PARENT_USB_DP",              "bidirectional", "ROUTER_USB_FS"),
    ("usb_dm",  "PARENT_USB_DM",              "bidirectional", "ROUTER_USB_FS"),
]
_UART_SERVICES = {"uart_tx", "uart_rx"}


@dataclass
class StandardPort:
    group_code: str
    parent_net: str
    service: str
    direction: str
    router_block: str
    source_pin: int
    source_lane: str
    exact_function_validated: str   # yes | no | dedicated_pin
    matched_function: str
    lane_functions: str             # all exact functions on the lane


def build_standard_ports(package: str, groups, service_lanes: list[dict],
                         exact_funcs: dict[int, list[dict]],
                         boot_uart_pins: set[int]) -> list[StandardPort]:
    # service -> {part_number: [pins]}
    by_service: dict[str, dict[str, list[int]]] = {}
    for r in service_lanes:
        by_service.setdefault(r["svc"], {}).setdefault(r["part"], []).append(int(r["pin"]))

    out: list[StandardPort] = []
    for g in groups:
        members = set(g.members)
        for service, net, direction, router in STANDARD_PORTS:
            part_pins = by_service.get(service, {})
            pins = [p for part in members for p in part_pins.get(part, [])]
            # UART standard ports are restricted to the bootloader UART pins.
            if service in _UART_SERVICES and boot_uart_pins:
                pins = [p for p in pins if p in boot_uart_pins]
            if not pins:
                continue
            pin = Counter(pins).most_common(1)[0][0]
            lane = f"CARD_LANE_{pin:03d}"
            status, matched = funcs.lane_carries_service(exact_funcs.get(pin, []), service)
            names = funcs.exact_function_names(exact_funcs.get(pin, []))
            out.append(StandardPort(
                group_code=g.code, parent_net=net, service=service,
                direction=direction, router_block=router, source_pin=pin,
                source_lane=lane, exact_function_validated=status,
                matched_function=matched,
                lane_functions=" | ".join(names) if names else "",
            ))
    out.sort(key=lambda s: (s.group_code, s.parent_net))
    return out
