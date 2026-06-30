"""
sheets.py — KiCad hierarchical sheet tree + physical placement zones.

The build instruction system tells engineers exactly which KiCad sheet each cell
belongs in and which physical board zone it goes in. Sheet/zone assignment is
mechanical (derived from the pin's bank and required cell), so the schematic
builder, placement guide, and per-pin recipe all share these helpers.
"""
from __future__ import annotations

from . import cells
from .paths import BANK_SIZE, BANKS, lane_bank

# Physical board zones (spec section 9 of the vision).
ZONE_SOCKET_CORE   = "Socket Core"
ZONE_PIN_ROLE_RING = "Pin Role Ring"
ZONE_CONTROL_RING  = "Control Ring"
ZONE_BACKPLANE     = "Backplane Edge"

# cell id -> physical zone on the plug-in card
_CELL_ZONE = {
    cells.CELL_VCAP_ONLY:       ZONE_SOCKET_CORE,
    cells.CELL_OSC_LOCAL:       ZONE_SOCKET_CORE,
    cells.CELL_POWER_ONLY:      ZONE_SOCKET_CORE,
    cells.CELL_GROUND_ONLY:     ZONE_SOCKET_CORE,
    cells.CELL_FULL_ROLE_SWITCH: ZONE_PIN_ROLE_RING,
    cells.CELL_IO_SWITCH:       ZONE_PIN_ROLE_RING,
    cells.CELL_DIRECT_IO:       ZONE_PIN_ROLE_RING,
    cells.CELL_USB_PAIR:        ZONE_PIN_ROLE_RING,
    cells.CELL_BOOT_STRAP:      ZONE_PIN_ROLE_RING,
    cells.CELL_NRST_OPEN_DRAIN: ZONE_PIN_ROLE_RING,
    cells.CELL_NC:              "—",
}


def placement_zone(cell_id: str) -> str:
    return _CELL_ZONE.get(cell_id, ZONE_PIN_ROLE_RING)


def _bank_index(pin: int) -> int:
    return min((pin - 1) // BANK_SIZE, len(BANKS) - 1)


def card_role_sheet(pin: int, pin_count: int) -> str:
    """The card role-switch sheet a pin's cell is placed in, e.g.
    ``CARD_ROLE_SWITCH_BANK_C_P065_P096``."""
    idx = _bank_index(pin)
    lo = idx * BANK_SIZE + 1
    hi = min((idx + 1) * BANK_SIZE, pin_count)
    return f"CARD_ROLE_SWITCH_BANK_{BANKS[idx]}_P{lo:03d}_P{hi:03d}"


def card_sheet_for_pin(pin: int, pin_count: int, cell_id: str) -> str:
    """Sheet for a pin given its cell — local cells live on their own sheets."""
    if cell_id == cells.CELL_VCAP_ONLY:
        return "CARD_LOCAL_VCAP_AND_DECOUPLING"
    if cell_id == cells.CELL_OSC_LOCAL:
        return "CARD_SOCKET"
    if cell_id in (cells.CELL_POWER_ONLY, cells.CELL_GROUND_ONLY, cells.CELL_NC):
        return "CARD_SOCKET"
    return card_role_sheet(pin, pin_count)


def card_sheets(package: str, pin_count: int) -> list[str]:
    """The plug-in card hierarchical sheet tree for a package."""
    banks = sorted({_bank_index(p) for p in range(1, pin_count + 1)})
    role_sheets = [card_role_sheet(b * BANK_SIZE + 1, pin_count) for b in banks]
    return [
        f"CARD_ROOT_{package}",
        f"CARD_SOCKET_{package}",
        *role_sheets,
        "CARD_LOCAL_VCAP_AND_DECOUPLING",
        "CARD_ROLE_CONTROL_LATCHES",
        "CARD_ID_EEPROM",
        "CARD_BACKPLANE_CONNECTOR",
    ]


def parent_sheets() -> list[str]:
    """The (package-independent) parent board hierarchical sheet tree."""
    return [
        "PARENT_ROOT",
        "PARENT_POWER",
        "PARENT_CONTROLLER_H7",
        "PARENT_BACKPLANE_176",
        "PARENT_SWD_ROUTER",
        "PARENT_UART_BOOT_ROUTER",
        "PARENT_USB_ROUTER",
        "PARENT_ADC_PROBE_ROUTER",
        "PARENT_GPIO_ACCESS_MATRIX",
        "PARENT_BOOT_RESET_CONTROL",
        "PARENT_STANDARD_HEADERS",
    ]


def lane_bank_name(pin: int) -> str:
    return lane_bank(pin)
