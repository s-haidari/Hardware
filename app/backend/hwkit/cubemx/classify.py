"""
classify.py — derive a pin's electrical class, canonical name, and roles from
its CubeMX name / type / signals. This is the heart of the build: the roles must
reproduce the hand-verified switch ground truth (LQFP64 = 11 switch pins).
"""
from __future__ import annotations

import re

from .parse import Pin

_PORT = re.compile(r"^P([A-Z])(\d{1,2})")


def canonical(pin: Pin) -> tuple[str, str | None, int | None]:
    """(canonical_pin_name, gpio_port, gpio_index)."""
    m = _PORT.match(pin.name)
    if m:
        port, idx = m.group(1), int(m.group(2))
        return f"P{port}{idx}", port, idx
    return pin.name.replace("_", "").replace(" ", "").replace("+", "+"), None, None


def electrical_class(pin: Pin) -> str:
    name = pin.name.upper()
    # Supply-supervisor / reserved pins (NPOR, PDR_ON, RFU) are routed direct as
    # IO, never switched (matches the engineering ground truth).
    if "NPOR" in name or "PDR_ON" in name or name == "RFU":
        return "io"
    if pin.type == "Reset":
        return "reset"
    if pin.type == "Boot":
        return "boot"
    if pin.type == "NC":
        return "nc"
    if pin.type == "Power":
        if name.startswith("VSS"):
            return "ground"
        if name.startswith("VCAP"):
            return "vcap"
        return "power"
    return "io"  # I/O, MonoIO


def _is_analog(pin: Pin) -> bool:
    if any("ADC" in s.name or "DAC" in s.name for s in pin.signals):
        return True
    return any("Analog" in s.io_modes for s in pin.signals)


def roles(pin: Pin) -> list[tuple[str, str]]:
    """List of (role_name, role_class) for this pin on this MCU.

    role_names are chosen to match hwkit.pins.switch_engine.switch_identity:
    power_v* (power) / ground / vcap / boot / reset_nrst / oscillator_hse,
    plus io-family roles (gpio/analog/swclk/swdio/swo/jtag_extra).
    """
    ec = electrical_class(pin)
    name = pin.name.upper()
    sigs = " ".join(s.name for s in pin.signals).upper()
    out: list[tuple[str, str]] = []

    if ec == "power":
        if name.startswith("VBAT"):
            out.append(("power_vbat", "power"))
        elif name.startswith("VDDA"):
            out.append(("power_vdda", "power"))
        elif name.startswith("VREF"):
            out.append(("power_vref", "power"))
        else:
            out.append(("power_vdd", "power"))
    elif ec == "ground":
        out.append(("ground", "ground"))
    elif ec == "vcap":
        out.append(("vcap", "local_card"))
    elif ec == "reset":
        out.append(("reset_nrst", "service"))
    elif ec == "boot":
        out.append(("boot", "service"))
    elif ec == "nc":
        pass
    else:  # io
        # HSE only (PH0/PH1 'OSC_IN'/'OSC_OUT'); NOT the LSE 'OSC32' pins, which
        # are plain GPIO the cards route direct.
        if "OSC_IN" in name or "OSC_OUT" in name or "RCC_OSC_IN" in sigs or "RCC_OSC_OUT" in sigs:
            out.append(("oscillator_hse", "local_card"))
        if "SWDIO" in sigs or "JTMS" in sigs:
            out.append(("swdio", "service"))
        if "SWCLK" in sigs or "JTCK" in sigs:
            out.append(("swclk", "service"))
        if "TRACESWO" in sigs or "JTDO" in sigs or "-SWO" in sigs or "_SWO" in sigs:
            out.append(("swo", "service"))
        if "JTDI" in sigs or "NJTRST" in sigs or "JTRST" in sigs:
            out.append(("jtag_extra", "service"))
        if _is_analog(pin):
            out.append(("analog", "io"))
        if "GPIO" in sigs:
            out.append(("gpio", "io"))
        if not any(rc == "io" for _, rc in out):
            out.append(("gpio", "io"))  # every I/O pin carries an IO identity

    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for rn, rc in out:
        if rn not in seen:
            seen.add(rn)
            uniq.append((rn, rc))
    return uniq
