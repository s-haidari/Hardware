"""
cells.py — derive the required hardware cell for a socket pin from its role set.

This module implements spec section 8/9 classification.  It NEVER decides safety
from a dominant role; it uses the complete normalized role set plus the parent
services the lane participates in.  It also maps the existing DB ``cell_kind``
onto the spec cell family so :mod:`validate` can flag unsafe legacy
classifications (e.g. a pin marked ``hardwired_io`` whose role set contains VDD).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import roles as R

# ── spec cell ids ──────────────────────────────────────────────────────────
CELL_DIRECT_IO        = "CELL_DIRECT_IO"
CELL_IO_SWITCH        = "CELL_IO_SWITCH"
CELL_FULL_ROLE_SWITCH = "CELL_FULL_ROLE_SWITCH"
CELL_POWER_ONLY       = "CELL_POWER_ONLY"
CELL_GROUND_ONLY      = "CELL_GROUND_ONLY"
CELL_VCAP_ONLY        = "CELL_VCAP_ONLY"
CELL_BOOT_STRAP       = "CELL_BOOT_STRAP"
CELL_NRST_OPEN_DRAIN  = "CELL_NRST_OPEN_DRAIN"
CELL_OSC_LOCAL        = "CELL_OSC_LOCAL"
CELL_USB_PAIR         = "CELL_USB_PAIR"
CELL_NC               = "CELL_NC"

ALL_CELL_IDS = [
    CELL_DIRECT_IO, CELL_IO_SWITCH, CELL_FULL_ROLE_SWITCH, CELL_POWER_ONLY,
    CELL_GROUND_ONLY, CELL_VCAP_ONLY, CELL_BOOT_STRAP, CELL_NRST_OPEN_DRAIN,
    CELL_OSC_LOCAL, CELL_USB_PAIR, CELL_NC,
]

# review flags
FLAG_ROLE_UNKNOWN      = "ROLE_UNKNOWN_REVIEW_REQUIRED"
FLAG_SWITCHED_GROUND   = "SWITCHED_GROUND_REQUIRES_ENGINEERING_REVIEW"
FLAG_UNSAFE_DB_CLASS   = "UNSAFE_DB_CLASSIFICATION"
FLAG_ANALOG_AND_HS     = "ANALOG_AND_HIGH_SPEED_SENSITIVE"

# IO sensitivity variants (spec section 9.2)
IO_LOW_SPEED  = "IO_LOW_SPEED"
IO_SWD_SAFE   = "IO_SWD_SAFE"
IO_ANALOG_SAFE = "IO_ANALOG_SAFE"
IO_UART_SAFE  = "IO_UART_SAFE"

# component requirement classes (coarse, spec sections 9.x)
_COMPONENT_CLASS = {
    CELL_DIRECT_IO:        "series_resistor",
    CELL_IO_SWITCH:        "bidirectional_analog_switch",
    CELL_FULL_ROLE_SWITCH: "one_hot_role_switch_bank",
    CELL_POWER_ONLY:       "load_switch_or_direct",
    CELL_GROUND_ONLY:      "direct_ground",
    CELL_VCAP_ONLY:        "local_capacitor",
    CELL_BOOT_STRAP:       "strap_network",
    CELL_NRST_OPEN_DRAIN:  "open_drain_reset",
    CELL_OSC_LOCAL:        "local_crystal_network",
    CELL_USB_PAIR:         "usb_rated_diff_switch",
    CELL_NC:               "none",
}

# KiCad sheet / symbol / footprint class hints per cell.
_KICAD = {
    CELL_DIRECT_IO:        ("cell_direct_io",        "R_series",        "0402"),
    CELL_IO_SWITCH:        ("cell_io_switch",        "SW_analog_bidir", "SC70-6"),
    CELL_FULL_ROLE_SWITCH: ("cell_full_role_switch", "ROLE_SWITCH_BANK", "QFN_multi"),
    CELL_POWER_ONLY:       ("cell_power_only",       "SW_load",         "SOT23-5"),
    CELL_GROUND_ONLY:      ("cell_ground_only",      "GND",             "n/a"),
    CELL_VCAP_ONLY:        ("cell_vcap_only",        "C",               "0402"),
    CELL_BOOT_STRAP:       ("cell_boot_strap",       "R_strap",         "0402"),
    CELL_NRST_OPEN_DRAIN:  ("cell_nrst_open_drain",  "SW_od",           "SC70"),
    CELL_OSC_LOCAL:        ("cell_osc_local",        "Crystal_GND24",   "3225"),
    CELL_USB_PAIR:         ("cell_usb_pair",         "SW_usb_diff",     "UQFN-10"),
    CELL_NC:               ("cell_nc",               "NC",              "n/a"),
}

# Services that mark a {IO}-only lane as needing an isolating switch.
_SWD_SERVICES    = {"swdio", "swclk", "swo"}
_ANALOG_SERVICES = {"adc", "dac", "analog"}
_UART_SERVICES   = {"uart_tx", "uart_rx"}
_USB_SERVICES    = {"usb_dp", "usb_dm"}


@dataclass
class CellDecision:
    cell_id: str
    variant: str
    component_class: str
    switch_paths: list[str]            # role codes the cell must route
    review_flags: list[str] = field(default_factory=list)
    kicad_sheet: str = ""
    kicad_symbol: str = ""
    kicad_footprint_class: str = ""

    @property
    def screenshot(self) -> str:
        return f"docs/images/cells/{self.cell_id}.png"


def classify_cell(
    role_set: set[str],
    services: set[str] | None = None,
    *,
    is_analog: bool = False,
    is_high_speed: bool = False,
) -> CellDecision:
    """Return the required cell for a pin given its full role set + services."""
    services = {s.lower() for s in (services or set())}
    roles = set(role_set)
    flags: list[str] = []

    if R.UNKNOWN in roles:
        flags.append(FLAG_ROLE_UNKNOWN)

    # 1. The critical universal safety case wins over EVERYTHING else: if a pin
    #    can be IO on one MCU and a supply/return/cap on another, it must route
    #    through the full role-switch cell no matter what service it also offers.
    if R.is_dangerous_mixed(roles):
        if roles & R.GROUND_ROLES:
            flags.append(FLAG_SWITCHED_GROUND)
        if is_analog and is_high_speed:
            flags.append(FLAG_ANALOG_AND_HS)
        variant = "ROLES_" + "_".join(sorted(roles))
        return _mk(CELL_FULL_ROLE_SWITCH, variant, roles, flags)

    # 2. Dedicated high-speed / special services (only reached for non-dangerous
    #    role sets).  A service only reclassifies the cell when the role itself
    #    is present OR the pin is genuinely IO-capable — a pure supply/ground/NC
    #    pin is never turned into a USB/NRST/BOOT cell by a stray service mapping.
    io_capable = R.IO in roles
    if R.USB_DP in roles or R.USB_DM in roles or (io_capable and services & _USB_SERVICES):
        return _mk(CELL_USB_PAIR, "USB_FS_DIFF", roles, flags)
    if R.OSC_IN in roles or R.OSC_OUT in roles:
        return _mk(CELL_OSC_LOCAL, "CRYSTAL_LOCAL", roles, flags)
    if R.NRST in roles or (io_capable and "nrst" in services):
        return _mk(CELL_NRST_OPEN_DRAIN, "OPEN_DRAIN", roles, flags)
    if R.BOOT in roles or (io_capable and "boot0" in services):
        return _mk(CELL_BOOT_STRAP, "STRAP", roles, flags)

    # 3. Pure supply / return / cap pins.
    if roles - {R.NC} == {R.VCAP}:
        return _mk(CELL_VCAP_ONLY, "VCAP_LOCAL", roles, flags)
    if R.is_ground_only(roles):
        return _mk(CELL_GROUND_ONLY, "DIRECT_GND", roles, flags)
    if R.is_power_only(roles):
        rail = "_".join(sorted(roles & (R.POWER_ROLES | {R.VBAT})))
        return _mk(CELL_POWER_ONLY, rail or "VTARGET", roles, flags)

    # 4. IO-only lanes: direct vs. isolating switch by sensitivity / service.
    if R.is_io_only(roles) or roles == set() or roles == {R.IO}:
        if services & _SWD_SERVICES:
            return _mk(CELL_IO_SWITCH, IO_SWD_SAFE, roles, flags)
        if is_analog or (services & _ANALOG_SERVICES):
            return _mk(CELL_IO_SWITCH, IO_ANALOG_SAFE, roles, flags)
        if services & _UART_SERVICES:
            return _mk(CELL_IO_SWITCH, IO_UART_SAFE, roles, flags)
        if is_high_speed:
            return _mk(CELL_IO_SWITCH, IO_LOW_SPEED, roles, flags)
        return _mk(CELL_DIRECT_IO, IO_LOW_SPEED, roles, flags)

    # 5. Not-connected / empty.
    if not (roles - {R.NC}):
        return _mk(CELL_NC, "NC", roles, flags)

    # 6. Anything else is unclassifiable — be conservative, demand the safe cell.
    flags.append(FLAG_ROLE_UNKNOWN)
    return _mk(CELL_FULL_ROLE_SWITCH, "ROLES_" + "_".join(sorted(roles)), roles, flags)


def _mk(cell_id: str, variant: str, roles: set[str], flags: list[str]) -> CellDecision:
    sheet, symbol, fp = _KICAD[cell_id]
    switch_paths = sorted(roles - {R.NC}) if cell_id == CELL_FULL_ROLE_SWITCH else []
    return CellDecision(
        cell_id=cell_id,
        variant=variant,
        component_class=_COMPONENT_CLASS[cell_id],
        switch_paths=switch_paths,
        review_flags=list(dict.fromkeys(flags)),  # dedupe, keep order
        kicad_sheet=sheet,
        kicad_symbol=symbol,
        kicad_footprint_class=fp,
    )


# ── DB cell_kind reconciliation (for the unsafe-classification audit) ───────

# Which spec cell families a stored DB cell_kind is allowed to map to.
DB_CELL_KIND_EXPECTED: dict[str, set[str]] = {
    "hardwired_io":     {CELL_DIRECT_IO, CELL_IO_SWITCH},
    "hardwired_power":  {CELL_POWER_ONLY},
    "hardwired_ground": {CELL_GROUND_ONLY},
    "static_vcap":      {CELL_VCAP_ONLY},
    "role_switch":      {CELL_FULL_ROLE_SWITCH},
    "oscillator_local": {CELL_OSC_LOCAL},
    "nc":               {CELL_NC},
}


def db_classification_is_unsafe(db_cell_kind: str | None, decision: CellDecision) -> bool:
    """True when the legacy DB cell_kind contradicts the role-set-derived cell
    in a way that is electrically unsafe (the spec section 21 audit).

    The canonical failure: DB says ``hardwired_io`` but the role set forces a
    full role switch (IO mixed with VDD/VSS/VCAP/...).
    """
    if not db_cell_kind:
        return False
    expected = DB_CELL_KIND_EXPECTED.get(db_cell_kind)
    if expected is None:
        return False
    if decision.cell_id in expected:
        return False
    # A mismatch is *unsafe* (not merely stricter) when the DB hardwired a pin
    # that actually needs isolation or a full role switch.
    unsafe_targets = {CELL_FULL_ROLE_SWITCH, CELL_VCAP_ONLY, CELL_OSC_LOCAL,
                      CELL_USB_PAIR, CELL_NRST_OPEN_DRAIN, CELL_BOOT_STRAP}
    if db_cell_kind == "hardwired_io" and decision.cell_id in unsafe_targets:
        return True
    if db_cell_kind in ("hardwired_power", "hardwired_ground") and \
            decision.cell_id == CELL_FULL_ROLE_SWITCH:
        return True
    return False
