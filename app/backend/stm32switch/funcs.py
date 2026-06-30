"""
funcs.py — exact-function helpers.

Exact CubeMX functions (USART1_TX, SYS_JTMS-SWDIO, TIM3_CH2, USB_OTG_FS_DP) are
preserved verbatim — never broadened to "uart"/"spi". These helpers expose the
per-pin exact-function list and test whether a lane actually carries the exact
function a parent standardized service requires.
"""
from __future__ import annotations

# service token -> predicate over an UPPERCASED exact function name
SERVICE_FUNC_MATCH = {
    "swdio":   lambda nm: "SWDIO" in nm or "JTMS" in nm,
    "swclk":   lambda nm: "SWCLK" in nm or "JTCK" in nm,
    "swo":     lambda nm: "SWO" in nm,
    # STM32 has UART, LPUART (contains "UART") and USART (does NOT contain "UART").
    "uart_tx": lambda nm: ("UART" in nm or "USART" in nm) and ("_TX" in nm or nm.endswith("TX")),
    "uart_rx": lambda nm: ("UART" in nm or "USART" in nm) and ("_RX" in nm or nm.endswith("RX")),
    "usb_dp":  lambda nm: "USB" in nm and "DP" in nm,
    "usb_dm":  lambda nm: "USB" in nm and "DM" in nm,
}

# Services that are dedicated pins (electrical role), not alternate functions —
# so they are not expected to appear in pin_function.
DEDICATED_SERVICES = {"nrst", "boot0"}


def exact_function_names(funcs_for_pin: list[dict]) -> list[str]:
    return [f["function_name"] for f in funcs_for_pin]


def normalized_families(funcs_for_pin: list[dict]) -> list[str]:
    """Sorted distinct peripheral categories (helper view only — not a replacement
    for the exact functions)."""
    return sorted({(f["category"] or "").lower() for f in funcs_for_pin if f.get("category")})


def lane_carries_service(funcs_for_pin: list[dict], service: str) -> tuple[str, str]:
    """Return (status, matched_function).

    status: "yes" | "no" | "dedicated_pin".  ``dedicated_pin`` is used for
    NRST/BOOT0 which are not alternate functions.
    """
    if service in DEDICATED_SERVICES:
        return ("dedicated_pin", "")
    pred = SERVICE_FUNC_MATCH.get(service)
    if pred is None:
        return ("dedicated_pin", "")
    for f in funcs_for_pin:
        nm = (f["function_name"] or "").upper()
        if pred(nm):
            return ("yes", f["function_name"])
    return ("no", "")
