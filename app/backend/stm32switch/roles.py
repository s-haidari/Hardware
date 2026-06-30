"""
roles.py — role normalization and the dangerous-mixed-role safety predicate.

This is the foundation of the whole safety model.  Electrical safety must be
decided from the *complete* set of roles a socket pin takes across every MCU in
a package, never from a single dominant role.  A pin that is GPIO on one MCU and
VDD/VSS/VCAP on another is electrically dangerous and must route through a
card-local role-switch cell so the parent only ever sees a safe lane.
"""
from __future__ import annotations

# Canonical role enum (spec section 8).
IO       = "IO"
VDD      = "VDD"
VDDA     = "VDDA"
VSS      = "VSS"
VSSA     = "VSSA"
VCAP     = "VCAP"
VBAT     = "VBAT"
VREF     = "VREF"
BOOT     = "BOOT"
NRST     = "NRST"
OSC_IN   = "OSC_IN"
OSC_OUT  = "OSC_OUT"
USB_DP   = "USB_DP"
USB_DM   = "USB_DM"
NC       = "NC"
RESERVED = "RESERVED"
UNKNOWN  = "UNKNOWN"

ROLE_ENUM = frozenset({
    IO, VDD, VDDA, VSS, VSSA, VCAP, VBAT, VREF, BOOT, NRST,
    OSC_IN, OSC_OUT, USB_DP, USB_DM, NC, RESERVED, UNKNOWN,
})

# Roles that are electrically *dangerous* if a pin can also be driven as IO:
# connecting an MCU output to a supply/return/cap node would be destructive.
DANGEROUS_ROLES = frozenset({VDD, VDDA, VSS, VSSA, VCAP, VBAT, VREF})
POWER_ROLES     = frozenset({VDD, VDDA, VBAT, VREF})
GROUND_ROLES    = frozenset({VSS, VSSA})

# Map raw DB / datasheet tokens onto the canonical enum.
_ALIASES: dict[str, str] = {
    "io": IO, "gpio": IO, "pin": IO,
    "vdd": VDD, "power_vdd": VDD, "power_vddusb": VDD, "vddusb": VDD,
    "vdda": VDDA, "power_vdda": VDDA,
    "vss": VSS, "ground": VSS, "gnd": VSS,
    "vssa": VSSA, "ground_analog": VSSA, "agnd": VSSA,
    "vcap": VCAP,
    "vbat": VBAT, "power_vbat": VBAT,
    "vref": VREF, "power_vref": VREF, "vref+": VREF,
    "boot": BOOT, "boot0": BOOT, "boot1": BOOT,
    "nrst": NRST, "reset_nrst": NRST, "reset": NRST,
    "osc_in": OSC_IN, "oscin": OSC_IN, "pf0": OSC_IN,
    "osc_out": OSC_OUT, "oscout": OSC_OUT, "pf1": OSC_OUT,
    "oscillator_hse": OSC_IN, "oscillator_lse": OSC_IN, "oscillator": OSC_IN,
    "usb_dp": USB_DP, "usb_dplus": USB_DP, "dp": USB_DP,
    "usb_dm": USB_DM, "usb_dminus": USB_DM, "dm": USB_DM,
    "nc": NC, "none": NC, "": NC,
    "reserved": RESERVED,
}


def normalize_role(token: str | None) -> str:
    """Map a single raw role token onto the canonical enum (UNKNOWN if unmapped)."""
    if token is None:
        return UNKNOWN
    t = str(token).strip()
    if not t:
        return NC
    low = t.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    up = t.upper()
    if up in ROLE_ENUM:
        return up
    return UNKNOWN


def normalize_role_set(raw: str | list[str] | set[str] | None) -> set[str]:
    """Normalize a comma-separated string or iterable of tokens into a role set."""
    if raw is None:
        return set()
    if isinstance(raw, str):
        tokens = [p for p in raw.replace(";", ",").split(",")]
    else:
        tokens = list(raw)
    out = {normalize_role(tok) for tok in tokens if str(tok).strip() != ""}
    out.discard("")  # safety
    return out


def role_of(name: str | None, eclass: str | None) -> str:
    """Map a pin's ``electrical_class`` + datasheet name onto the canonical role.

    ``electrical_class`` (io/power/ground/reset/boot/oscillator/vcap/nc) is the
    authoritative coarse class; the name disambiguates the power/ground/osc
    sub-rails (VDDA vs VDD, VSSA vs VSS, OSC_OUT vs OSC_IN).
    """
    ec = (eclass or "").lower()
    up = (name or "").upper()
    if ec == "io":         return IO
    if ec == "vcap":       return VCAP
    if ec == "reset":      return NRST
    if ec == "boot":       return BOOT
    if ec == "oscillator": return OSC_OUT if "OUT" in up else OSC_IN
    if ec == "nc":         return NC
    if ec == "ground":
        return VSSA if "VSSA" in up else VSS
    if ec == "power":
        if "VDDA" in up: return VDDA
        if "VBAT" in up: return VBAT
        if "VREF" in up: return VREF
        if "USB" in up:  return VDD
        return VDD
    return normalize_role(name)


def is_dangerous_mixed(roles: set[str]) -> bool:
    """True when a pin is usable as IO yet also takes a supply/return/cap role.

    These pins MUST NOT be hardwired as IO — they require a full role-switch cell.
    """
    return IO in roles and bool(roles & DANGEROUS_ROLES)


def is_power_only(roles: set[str]) -> bool:
    r = roles - {NC}
    return bool(r) and r <= POWER_ROLES and IO not in r


def is_ground_only(roles: set[str]) -> bool:
    r = roles - {NC}
    return bool(r) and r <= GROUND_ROLES and IO not in r


def is_io_only(roles: set[str]) -> bool:
    r = roles - {NC}
    return r == {IO}


def dangerous_members(roles: set[str]) -> list[str]:
    """Sorted list of the dangerous roles present (for messages/flags)."""
    return sorted(roles & DANGEROUS_ROLES)


def role_set_str(roles: set[str]) -> str:
    """Deterministic display string for a role set."""
    return ",".join(sorted(roles)) if roles else NC
