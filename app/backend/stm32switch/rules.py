"""
rules.py — the STM32F Helios / Attack Board / Victim Card design-rules engine.

This is the deterministic policy layer.  It takes the *facts* about a socket pin
(its electrical role in every exact STM32F group, the services it carries, its
capabilities) and derives the *design decisions* the canonical schema needs:
guarantee/switching classification, the Victim Card cell + branch model, what
Helios can control and how, which standardized Attack Board port the lane feeds,
reusable-block ownership, the firmware control fabric, the safety class, and the
plain-English UI fields.

It encodes engineering policy as fixed rules (with the worked examples from the
spec), so the columns get real values; ``UNKNOWN`` is reserved for genuinely
missing MCU facts, not for design decisions.

Terminology:
  * Helios       — the parent STM32H7 controller on the Attack Board.
  * Attack Board — the parent board (standardized ports + service routers).
  * Victim Card  — the plug-in socket card (breaks out every socket pin).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cells, roles as R
from .paths import lane_id

# ── role groupings ─────────────────────────────────────────────────────────
POWER = R.POWER_ROLES                       # VDD, VDDA, VBAT, VREF
GROUND = R.GROUND_ROLES                     # VSS, VSSA
OSC = frozenset({R.OSC_IN, R.OSC_OUT})
USB = frozenset({R.USB_DP, R.USB_DM})
SPECIAL = frozenset({R.NRST, R.BOOT, R.VCAP}) | OSC | USB

# ── pin-role stability enum ────────────────────────────────────────────────
GUARANTEED_FIXED_ROLE             = "GUARANTEED_FIXED_ROLE"
GUARANTEED_IO_DIFFERENT_FUNCTIONS = "GUARANTEED_IO_DIFFERENT_FUNCTIONS"
MIXED_IO_AND_POWER                = "MIXED_IO_AND_POWER"
MIXED_IO_AND_GROUND               = "MIXED_IO_AND_GROUND"
MIXED_IO_AND_SPECIAL              = "MIXED_IO_AND_SPECIAL"
MIXED_POWER_AND_GROUND            = "MIXED_POWER_AND_GROUND"
MIXED_VCAP_OR_ANALOG_SPECIAL      = "MIXED_VCAP_OR_ANALOG_SPECIAL"
UNKNOWN_REVIEW                    = "UNKNOWN_REVIEW"

_MIXED = {MIXED_IO_AND_POWER, MIXED_IO_AND_GROUND, MIXED_IO_AND_SPECIAL,
          MIXED_POWER_AND_GROUND, MIXED_VCAP_OR_ANALOG_SPECIAL, UNKNOWN_REVIEW}

# ── per-pin facts handed to the engine ─────────────────────────────────────

@dataclass
class PinContext:
    pin: int
    lane: str
    side: str
    union_roles: set[str]                       # every role across all F groups
    roles_by_group: dict[str, str]              # group_code -> role at this pin
    labels_by_group: dict[str, str]             # group_code -> "Group A"
    services: set[str]                          # swdio/swclk/swo/nrst/boot0/uart_*/usb_*
    caps: dict                                  # has_adc/.../has_analog
    exact_functions: list[dict] = field(default_factory=list)

    @property
    def is_analog(self) -> bool:
        return bool(self.caps.get("has_analog"))

    @property
    def is_high_speed(self) -> bool:
        return bool(self.caps.get("has_usb") or self.caps.get("has_ethernet"))

    @property
    def roles_nn(self) -> set[str]:
        return self.union_roles - {R.NC}


# ── 1. stability / guarantee classification ────────────────────────────────

def stability(ctx: PinContext) -> str:
    nn = ctx.roles_nn
    if not nn:
        return GUARANTEED_FIXED_ROLE                # all-NC pin
    if nn == {R.IO}:
        return GUARANTEED_IO_DIFFERENT_FUNCTIONS
    if len(nn) == 1:
        return GUARANTEED_FIXED_ROLE
    has_io = R.IO in nn
    if has_io and R.VCAP in nn:
        return MIXED_VCAP_OR_ANALOG_SPECIAL
    if has_io and (nn & POWER):
        return MIXED_IO_AND_POWER
    if has_io and (nn & GROUND):
        return MIXED_IO_AND_GROUND
    if has_io and (nn & SPECIAL):
        return MIXED_IO_AND_SPECIAL
    if (nn & POWER) and (nn & GROUND):
        return MIXED_POWER_AND_GROUND
    return UNKNOWN_REVIEW


def _yn(b: bool) -> str:
    return "yes" if b else "no"


# ── role → branch destination / nets ───────────────────────────────────────

_DEST = {
    R.IO: None,  # filled with the lane
    R.VDD: "VTARGET", R.VDDA: "VDDA_TARGET", R.VBAT: "VBAT_TARGET", R.VREF: "VREF_TARGET",
    R.VSS: "GND", R.VSSA: "AGND", R.VCAP: "VCAP_LOCAL",
    R.NRST: "PARENT_NRST", R.BOOT: "PARENT_BOOT0_CTRL",
    R.OSC_IN: "LOCAL_CRYSTAL", R.OSC_OUT: "LOCAL_CRYSTAL",
    R.USB_DP: "PARENT_USB_DP", R.USB_DM: "PARENT_USB_DM",
    R.NC: "NC",
}


def _dest_for(role: str, lane: str) -> str:
    if role == R.IO:
        return lane
    return _DEST.get(role, "REVIEW")


def _enable_net(pin: int, role: str) -> str:
    return f"EN_P{pin:03d}_{role}"


# ── 2. Victim Card cell ─────────────────────────────────────────────────────

# spec cell id -> component class (classes only; never part numbers)
_VC_COMPONENT_CLASS = {
    "DIRECT_IO":          "DIRECT_IO_SERIES_RESISTOR",
    "PROTECTED_IO":       "LOW_CAP_IO_SWITCH",
    "IO_TO_POWER_SWITCH": "VICTIM_CARD_POWER_BRANCH_SWITCH",
    "IO_TO_GROUND_SWITCH": "VICTIM_CARD_GROUND_BRANCH_REVIEW",
    "IO_TO_VCAP_SWITCH":  "LOW_LEAKAGE_ANALOG_SWITCH",
    "IO_TO_BOOT_SWITCH":  "CONTROLLED_BOOT_STRAP_CELL",
    "IO_TO_NRST_SWITCH":  "OPEN_DRAIN_RESET_CELL",
    "IO_TO_OSC_SWITCH":   "LOW_CAP_IO_SWITCH",
    "FIXED_VDD":          "DIRECT_POWER_BRANCH",
    "FIXED_VDDA":         "DIRECT_POWER_BRANCH",
    "FIXED_VBAT":         "DIRECT_POWER_BRANCH",
    "FIXED_VREF":         "DIRECT_POWER_BRANCH",
    "FIXED_VSS":          "DIRECT_GROUND",
    "FIXED_VSSA":         "DIRECT_GROUND",
    "FIXED_VCAP":         "LOCAL_VCAP_CELL",
    "FIXED_NRST":         "OPEN_DRAIN_RESET_CELL",
    "FIXED_BOOT":         "CONTROLLED_BOOT_STRAP_CELL",
    "FIXED_OSC":          "LOCAL_OSC_CELL",
    "USB_PAIR_CELL":      "USB_RATED_PAIR_ROUTER",
    "NC":                 "NONE",
    "REVIEW_REQUIRED":    "REVIEW",
}

_VC_DISPLAY = {
    "DIRECT_IO": "Direct IO", "PROTECTED_IO": "Protected IO",
    "IO_TO_POWER_SWITCH": "IO/Power Switch", "IO_TO_GROUND_SWITCH": "IO/Ground Switch",
    "IO_TO_VCAP_SWITCH": "IO/VCAP Switch", "IO_TO_BOOT_SWITCH": "IO/BOOT Switch",
    "IO_TO_NRST_SWITCH": "IO/NRST Switch", "IO_TO_OSC_SWITCH": "IO/OSC Switch",
    "FIXED_VDD": "Fixed VDD", "FIXED_VDDA": "Fixed VDDA", "FIXED_VBAT": "Fixed VBAT",
    "FIXED_VREF": "Fixed VREF", "FIXED_VSS": "Fixed VSS", "FIXED_VSSA": "Fixed VSSA",
    "FIXED_VCAP": "Local VCAP", "FIXED_NRST": "Open-Drain NRST", "FIXED_BOOT": "BOOT Strap",
    "FIXED_OSC": "Local Oscillator", "USB_PAIR_CELL": "USB Pair", "NC": "Not Connected",
    "REVIEW_REQUIRED": "Review Required",
}

_SWITCH_CELLS = {"PROTECTED_IO", "IO_TO_POWER_SWITCH", "IO_TO_GROUND_SWITCH",
                 "IO_TO_VCAP_SWITCH", "IO_TO_BOOT_SWITCH", "IO_TO_NRST_SWITCH",
                 "IO_TO_OSC_SWITCH"}


def victim_card_cell(ctx: PinContext) -> tuple[str, str]:
    """Return (cell_id, variant) in the new Victim Card taxonomy."""
    nn = ctx.roles_nn
    if not nn:
        return "NC", "NC"
    dec = cells.classify_cell(ctx.union_roles, ctx.services,
                              is_analog=ctx.is_analog, is_high_speed=ctx.is_high_speed)
    cid = dec.cell_id
    if cid == cells.CELL_FULL_ROLE_SWITCH:
        dangerous = nn & R.DANGEROUS_ROLES
        if R.IO in nn and nn & POWER:
            return "IO_TO_POWER_SWITCH", dec.variant
        if R.IO in nn and (nn & GROUND):
            return "IO_TO_GROUND_SWITCH", dec.variant
        if R.IO in nn and R.VCAP in nn:
            return "IO_TO_VCAP_SWITCH", dec.variant
        if R.IO in nn and R.NRST in nn:
            return "IO_TO_NRST_SWITCH", dec.variant
        if R.IO in nn and R.BOOT in nn:
            return "IO_TO_BOOT_SWITCH", dec.variant
        if R.IO in nn and (nn & OSC):
            return "IO_TO_OSC_SWITCH", dec.variant
        if dangerous:
            return "IO_TO_POWER_SWITCH", dec.variant
        return "REVIEW_REQUIRED", dec.variant
    if cid == cells.CELL_IO_SWITCH:
        return "PROTECTED_IO", dec.variant
    if cid == cells.CELL_DIRECT_IO:
        return "DIRECT_IO", dec.variant
    if cid == cells.CELL_POWER_ONLY:
        if R.VDDA in nn: return "FIXED_VDDA", dec.variant
        if R.VBAT in nn: return "FIXED_VBAT", dec.variant
        if R.VREF in nn: return "FIXED_VREF", dec.variant
        return "FIXED_VDD", dec.variant
    if cid == cells.CELL_GROUND_ONLY:
        return ("FIXED_VSSA" if R.VSSA in nn else "FIXED_VSS"), dec.variant
    if cid == cells.CELL_VCAP_ONLY:
        return "FIXED_VCAP", dec.variant
    if cid == cells.CELL_NRST_OPEN_DRAIN:
        return "FIXED_NRST", dec.variant
    if cid == cells.CELL_BOOT_STRAP:
        return "FIXED_BOOT", dec.variant
    if cid == cells.CELL_OSC_LOCAL:
        return "FIXED_OSC", dec.variant
    if cid == cells.CELL_USB_PAIR:
        return "USB_PAIR_CELL", dec.variant
    return "REVIEW_REQUIRED", dec.variant


# ── 3. Helios control ───────────────────────────────────────────────────────

def helios(ctx: PinContext) -> dict:
    """Helios control role + connection method for this pin."""
    nn = ctx.roles_nn
    svc = ctx.services
    role = "NONE"; method = "NONE"; direction = "NONE"; net = ""; default = "high_z"; note = ""
    stab = stability(ctx)
    # A pin that takes a supply/return/cap role in some F variants is owned by the
    # role-switch enable — even if a *minority* variant uses it as a service pin.
    # Only clean (non-power/ground) pins are classified as a Helios service.
    power_ground_mix = stab in (MIXED_IO_AND_POWER, MIXED_IO_AND_GROUND,
                                MIXED_POWER_AND_GROUND, MIXED_VCAP_OR_ANALOG_SPECIAL,
                                UNKNOWN_REVIEW)
    if power_ground_mix:
        role, method = "ROLE_SWITCH_ENABLE", ("POWER_SWITCH_ENABLE"
                                              if nn & (POWER | GROUND) else "ANALOG_SWITCH_ENABLE")
        direction, net, default = "HELIOS_TO_TARGET", _enable_net(ctx.pin, "ROLE"), "all_off"
        note = "Enable the correct one-hot branch for the selected card profile."
    elif R.NRST in nn or "nrst" in svc:
        role, method, direction = "TARGET_RESET_CONTROL", "OPEN_DRAIN_PULL_LOW", "HELIOS_TO_TARGET"
        net, default = "HELIOS_NRST", "released_high"
        note = "Drive low to reset; release (high-Z) for run. Never drive high."
    elif R.BOOT in nn or "boot0" in svc:
        role, method, direction = "BOOT_MODE_CONTROL", "CONTROLLED_PULLUP_TO_VTARGET", "HELIOS_TO_TARGET"
        net, default = "HELIOS_BOOT0_CTRL", "normal_boot_low"
        note = "Strap BOOT0 only via controlled pull referenced to VTARGET."
    elif "uart_tx" in svc:
        role, method, direction = "UART_BOOT_RX", "LEVEL_SAFE_UART_ROUTER", "TARGET_TO_HELIOS"
        net, default = "HELIOS_UART_BOOT_RX", "idle_high"
        note = "Target TX → Helios RX through the level-safe UART router."
    elif "uart_rx" in svc:
        role, method, direction = "UART_BOOT_TX", "LEVEL_SAFE_UART_ROUTER", "HELIOS_TO_TARGET"
        net, default = "HELIOS_UART_BOOT_TX", "idle_high"
        note = "Helios TX → target RX through the level-safe UART router."
    elif stab == MIXED_IO_AND_SPECIAL:
        role, method = "ROLE_SWITCH_ENABLE", "ANALOG_SWITCH_ENABLE"
        direction, net, default = "HELIOS_TO_TARGET", _enable_net(ctx.pin, "ROLE"), "all_off"
        note = "Enable the correct one-hot branch for the selected card profile."
    elif R.IO in nn:
        role, method, default = "NONE", "DO_NOT_CONNECT_DIRECTLY", "high_z"
        note = "Generic IO; not a Helios control pin."
    return {
        "helios_control_role": role,
        "helios_connection_allowed": _yn(role != "NONE"),
        "helios_connection_method": method,
        "helios_signal_direction": direction,
        "helios_control_net": net,
        "helios_gpio_requirement": "1_gpio" if role != "NONE" and method != "LEVEL_SAFE_UART_ROUTER" else (
            "uart_pair" if method == "LEVEL_SAFE_UART_ROUTER" else "none"),
        "helios_level_safety_required": _yn(method in ("LEVEL_SAFE_UART_ROUTER",
                                                       "CONTROLLED_PULLUP_TO_VTARGET")),
        "helios_default_state": default,
        "helios_firmware_action": note,
        "helios_notes": note,
    }


# ── 4. Attack Board standardized breakout ──────────────────────────────────

def attack_board(ctx: PinContext) -> dict:
    """Which standardized Attack Board port (if any) this lane feeds."""
    nn = ctx.roles_nn
    svc = ctx.services
    cls = "NONE"; port = ""; label = ""; router = ""; rdir = "NONE"
    visible = "no"; prio = 90
    if stability(ctx) in (MIXED_IO_AND_POWER, MIXED_IO_AND_GROUND,
                          MIXED_POWER_AND_GROUND, MIXED_VCAP_OR_ANALOG_SPECIAL, UNKNOWN_REVIEW):
        # Switched pin: it only reaches a header through its IO branch, so it is
        # not a clean standardized service port even if a minority variant is one.
        cls, port, label, prio = ("RAW_BREAKOUT_ONLY", "VICTIM_CARD_BREAKOUT",
                                  "Victim Card breakout (switched)", 60)
    elif "swdio" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_DEBUG", "PARENT_SWDIO", "SWDIO", "ROUTER_SWD", "BIDIRECTIONAL", 10
    elif "swclk" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_DEBUG", "PARENT_SWCLK", "SWCLK", "ROUTER_SWD", "ATTACK_TO_TARGET", 10
    elif "swo" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_DEBUG", "PARENT_SWO", "SWO", "ROUTER_SWD", "TARGET_TO_ATTACK", 12
    elif R.NRST in nn or "nrst" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_RESET_BOOT", "PARENT_NRST", "NRST", "ROUTER_RESET", "ATTACK_TO_TARGET", 11
    elif R.BOOT in nn or "boot0" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_RESET_BOOT", "PARENT_BOOT0_CTRL", "BOOT0", "ROUTER_BOOT", "ATTACK_TO_TARGET", 11
    elif "uart_tx" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_UART_BOOT", "PARENT_UART_RX_FROM_TARGET", "UART RX (from target)", "ROUTER_UART_BOOT", "TARGET_TO_ATTACK", 15
    elif "uart_rx" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_UART_BOOT", "PARENT_UART_TX_TO_TARGET", "UART TX (to target)", "ROUTER_UART_BOOT", "ATTACK_TO_TARGET", 15
    elif R.USB_DP in nn or "usb_dp" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_USB", "PARENT_USB_DP", "USB D+", "ROUTER_USB", "BIDIRECTIONAL", 18
    elif R.USB_DM in nn or "usb_dm" in svc:
        cls, port, label, router, rdir, prio = "STANDARD_USB", "PARENT_USB_DM", "USB D-", "ROUTER_USB", "BIDIRECTIONAL", 18
    elif ctx.is_analog and (R.IO in nn) and stability(ctx) not in _MIXED:
        cls, port, label, rdir, prio, visible = "STANDARD_ANALOG_PROBE", "PARENT_ADC_PROBE", "ADC probe", "TARGET_TO_ATTACK", 40, "yes"
    elif R.IO in nn and stability(ctx) not in _MIXED:
        cls, port, label, prio, visible = "STANDARD_GPIO_HEADER", "PARENT_GPIO_HEADER", "GPIO header", 55, "yes"
    elif R.IO in nn:  # mixed/switched IO still reaches a header after switching
        cls, port, label, prio = "RAW_BREAKOUT_ONLY", "VICTIM_CARD_BREAKOUT", "Victim Card breakout", 60
    elif nn & (POWER | GROUND | {R.VCAP}):
        cls, port, label, prio = "STANDARD_POWER_MONITOR", "PARENT_VTARGET_MONITOR", "Power monitor", 70
    else:
        cls, port, label, prio = "VICTIM_CARD_ONLY", "VICTIM_CARD_BREAKOUT", "Victim Card only", 80
    return {
        "attack_board_access_required": _yn(cls not in ("NONE", "VICTIM_CARD_ONLY")),
        "attack_board_access_class": cls,
        "attack_board_standard_port": port,
        "attack_board_standard_port_label": label,
        "attack_board_router_required": _yn(bool(router)),
        "attack_board_router_id": router,
        "attack_board_router_input_role": (sorted(svc)[0] if svc else (sorted(nn)[0] if nn else "")),
        "attack_board_router_direction": rdir,
        "attack_board_breakout_priority": prio,
        "attack_board_user_visible": visible,
        "attack_board_notes": label,
    }


# ── 5. control fabric ───────────────────────────────────────────────────────

def control_fabric(ctx: PinContext, cell_id: str, hel: dict) -> dict:
    method = hel["helios_connection_method"]
    nn = ctx.roles_nn
    ctype = "NONE"; bits = 0; enc = "none"; ennet = ""
    default_off = "no"; poweroff_only = "no"; interlock = "no"; readback = "no"
    if method == "OPEN_DRAIN_PULL_LOW":
        ctype, bits, ennet = "HELIOS_OPEN_DRAIN_GPIO", 1, hel["helios_control_net"]
    elif method == "CONTROLLED_PULLUP_TO_VTARGET":
        ctype, bits, ennet = "HELIOS_CONTROLLED_STRAP", 1, hel["helios_control_net"]
    elif method == "LEVEL_SAFE_UART_ROUTER":
        ctype, bits = "HELIOS_DIRECT_GPIO", 1
    elif cell_id in _SWITCH_CELLS or hel["helios_control_role"] == "ROLE_SWITCH_ENABLE":
        branches = len([r for r in nn if r != R.NC])
        ctype = "SPI_LATCH_BANK"
        bits = max(branches, 1)
        enc = "one_hot"
        ennet = _enable_net(ctx.pin, "ROLE")
        default_off = "yes"
        interlock = "yes"
        poweroff_only = _yn(bool(nn & (POWER | GROUND | {R.VCAP})))
    elif cell_id == "USB_PAIR_CELL":
        ctype, bits = "ANALOG_MUX_SELECT", 1
    return {
        "control_fabric_required": _yn(ctype != "NONE"),
        "control_fabric_type": ctype,
        "control_bit_count": bits,
        "control_select_encoding": enc,
        "control_enable_net": ennet,
        "control_default_off_required": default_off,
        "control_power_off_only_change": poweroff_only,
        "control_interlock_required": interlock,
        "control_readback_required": readback,
    }


# ── 6. safety class ─────────────────────────────────────────────────────────

def safety(ctx: PinContext, stab: str) -> dict:
    nn = ctx.roles_nn
    svc = ctx.services
    cls = "UNKNOWN"; review = "no"; reason = ""; direct = "no"; direct_reason = ""
    dnh = ""; fault = ""
    if stab in _MIXED:
        cls, review = "UNSAFE_DIRECT", "yes"
        offenders = sorted((nn & R.DANGEROUS_ROLES) | (nn & SPECIAL))
        dnh = (f"Pin is IO in some groups and {', '.join(offenders)} in others; "
               f"hardwiring to the lane would connect an MCU output to a "
               f"supply/return/special node.")
        reason = dnh
        fault = "Permanent damage or contention if hardwired."
    elif R.NRST in nn or "nrst" in svc:
        cls, direct = "SAFE_WITH_OPEN_DRAIN", "no"
        direct_reason = "Reset must be open-drain, pulled to VTARGET."
    elif R.BOOT in nn or "boot0" in svc:
        cls = "SAFE_WITH_CONTROLLED_STRAP"
        direct_reason = "BOOT0 must be a controlled strap referenced to VTARGET."
    elif "uart_tx" in svc or "uart_rx" in svc:
        cls = "SAFE_WITH_LEVEL_SAFE_ROUTER"
    elif nn & OSC:
        cls, review = "REQUIRES_LOCAL_ANALOG_HANDLING", "no"
        direct_reason = "Keep crystal + load caps local to the Victim Card."
    elif R.USB_DP in nn or R.USB_DM in nn or (svc & {"usb_dp", "usb_dm"}):
        cls = "SAFE_WITH_SERIES_RESISTOR"
        direct_reason = "Route as a 90Ω matched pair through the USB router."
    elif R.VCAP in nn:
        cls = "REQUIRES_LOCAL_ANALOG_HANDLING"
        direct_reason = "VCAP needs a local cap close to the socket; never a lane."
    elif nn <= (POWER | GROUND) and nn:
        cls, direct, direct_reason = "SAFE_DIRECT", "yes", "Fixed supply/return; low-impedance direct connection."
    elif ctx.is_analog and R.IO in nn:
        cls = "REQUIRES_LOCAL_ANALOG_HANDLING"
        direct_reason = "Analog-capable IO; isolate with a low-leakage switch + series R."
    elif R.IO in nn:
        cls, direct, direct_reason = "SAFE_WITH_SERIES_RESISTOR", "yes", "Guaranteed IO; series resistor + DNI ESD."
    elif not nn:
        cls, direct = "SAFE_DIRECT", "yes"
        direct_reason = "Not connected."
    else:
        cls, review, reason = "REQUIRES_REVIEW", "yes", "Unclassified role set."
    return {
        "safety_class": cls, "review_required": review, "review_reason": reason,
        "direct_connection_allowed": direct, "direct_connection_reason": direct_reason,
        "do_not_hardwire_reason": dnh, "fault_if_wrong": fault,
    }


# ── 7. UI plain-English fields ──────────────────────────────────────────────

def _labels_using(ctx: PinContext, role: str) -> list[str]:
    return sorted({ctx.labels_by_group[g] for g, r in ctx.roles_by_group.items()
                   if r == role and g in ctx.labels_by_group},
                  key=lambda s: (len(s), s))


def ui_fields(ctx: PinContext, stab: str, cell_id: str, saf: dict) -> dict:
    nn = ctx.roles_nn
    roles_txt = ", ".join(sorted(nn)) or "NC"
    switched = stab in _MIXED
    if switched:
        offenders = sorted((nn & R.DANGEROUS_ROLES) | (nn & SPECIAL))
        off_labels = []
        for role in offenders:
            ls = _labels_using(ctx, role)
            if ls:
                off_labels.append(f"{role} in {', '.join(ls)}")
        subtitle = "IO in most groups; " + "; ".join(off_labels) if off_labels else f"Mixed roles: {roles_txt}"
        warning = saf["do_not_hardwire_reason"]
        primary = f"Build Victim Card {_VC_DISPLAY.get(cell_id, cell_id)} cell."
        diagram = "switch_cell"; prio = 10
    elif nn == {R.IO}:
        subtitle = "Guaranteed IO across all STM32F groups (functions vary)."
        warning = ""; primary = "Break out to the GPIO header with a series resistor."
        diagram = "direct"; prio = 55
    elif nn & (POWER | GROUND | {R.VCAP}):
        subtitle = f"Fixed {roles_txt} across all STM32F groups."
        warning = ""; primary = f"Direct {roles_txt} branch on the Victim Card."
        diagram = "power"; prio = 70
    elif nn & {R.NRST, R.BOOT} or (ctx.services & {"nrst", "boot0"}):
        subtitle = f"Dedicated service pin ({roles_txt})."
        warning = ""; primary = "Build the Helios-controlled service cell."
        diagram = "service"; prio = 20
    else:
        subtitle = f"Roles: {roles_txt}"
        warning = ""; primary = "Break out on the Victim Card."
        diagram = "direct"; prio = 60
    return {
        "ui_title": f"Pin {ctx.pin} · {ctx.lane}",
        "ui_subtitle": subtitle,
        "ui_role_badges": "|".join(sorted(nn)) or "NC",
        "ui_group_badges": "|".join(sorted(set(ctx.labels_by_group.values()),
                                           key=lambda s: (len(s), s))),
        "ui_warning_text": warning,
        "ui_plain_english_summary": f"Pin {ctx.pin} ({ctx.lane}): {subtitle}",
        "ui_primary_action": primary,
        "ui_secondary_action": "Open the Implementation Recipe." if switched else "",
        "ui_visual_diagram_type": diagram,
        "ui_sort_priority": prio,
    }


# ── top-level: all pin-level design fields ─────────────────────────────────

def pin_design_fields(ctx: PinContext) -> dict:
    stab = stability(ctx)
    nn = ctx.roles_nn
    cell_id, variant = victim_card_cell(ctx)
    hel = helios(ctx)
    atk = attack_board(ctx)
    fab = control_fabric(ctx, cell_id, hel)
    saf = safety(ctx, stab)
    ui = ui_fields(ctx, stab, cell_id, saf)

    needs_switch = stab in _MIXED
    is_switch_cell = cell_id in _SWITCH_CELLS
    guaranteed_elec = stab in (GUARANTEED_FIXED_ROLE, GUARANTEED_IO_DIFFERENT_FUNCTIONS)
    is_service = bool(ctx.services & {"swdio", "swclk", "swo", "nrst", "boot0"}) or \
        bool(nn & {R.NRST, R.BOOT})
    owner = ("VICTIM_CARD" if needs_switch else
             "ATTACK_BOARD" if atk["attack_board_access_class"] in (
                 "STANDARD_DEBUG", "STANDARD_RESET_BOOT", "STANDARD_UART_BOOT", "STANDARD_USB")
             else "VICTIM_CARD" if cell_id != "NC" else "NONE")
    block_loc = ("ATTACK_BOARD_HELIOS_CONTROL" if hel["helios_control_role"] != "NONE"
                 and not needs_switch else
                 "ATTACK_BOARD_SERVICE_ROUTER" if atk["attack_board_router_required"] == "yes"
                 and not needs_switch else
                 "VICTIM_CARD_PIN_ROLE_RING" if needs_switch else
                 "VICTIM_CARD_SOCKET_CORE")

    fields = {
        # guarantee / switching
        "pin_role_stability": stab,
        "is_guaranteed_same_electrical_role": _yn(guaranteed_elec),
        "is_guaranteed_same_special_role": _yn(guaranteed_elec and bool(ctx.services)),
        "is_guaranteed_parent_service_pin": _yn(guaranteed_elec and is_service),
        "needs_victim_card_switching": _yn(needs_switch),
        "needs_attack_board_routing": atk["attack_board_access_required"],
        "needs_helios_control": _yn(hel["helios_control_role"] != "NONE"),
        "switching_reason": saf["do_not_hardwire_reason"] if needs_switch else "",
        "guarantee_reason": ("Same role in every STM32F group." if guaranteed_elec
                             else "Role varies across STM32F groups."),
        # victim card cell
        "victim_card_cell_required": cell_id,
        "victim_card_cell_variant": variant,
        "victim_card_cell_display_name": _VC_DISPLAY.get(cell_id, cell_id),
        "victim_card_component_class": _VC_COMPONENT_CLASS.get(cell_id, "REVIEW"),
        "victim_card_component_bank_hint": (f"{ctx.lane}_BANK" if is_switch_cell else ""),
        "victim_card_shared_hardware_allowed": _yn(is_switch_cell or cell_id in ("DIRECT_IO", "PROTECTED_IO")),
        "victim_card_shared_enable_allowed": "no",
        "victim_card_default_state": ("all_off" if is_switch_cell else
                                      "normal_boot_low" if cell_id == "FIXED_BOOT" else
                                      "released_high" if cell_id == "FIXED_NRST" else "static"),
        "victim_card_placement_zone": ("ROLE_RING" if needs_switch else
                                       "SOCKET_CORE" if nn & (POWER | GROUND | {R.VCAP} | OSC) else
                                       "BREAKOUT_EDGE"),
        "victim_card_notes": _VC_DISPLAY.get(cell_id, cell_id),
        # reusable block / sharing
        "implementation_owner": owner,
        "reusable_block_id": (f"VICTIM_{_bank(ctx.pin)}_IO_SWITCH_BANK" if is_switch_cell else
                              f"ATTACK_{atk['attack_board_router_id']}" if atk["attack_board_router_id"] else
                              "VICTIM_DIRECT_BREAKOUT"),
        "reusable_block_display_name": (f"Bank {_bank_letter(ctx.pin)} IO Switch Bank" if is_switch_cell else
                                        atk["attack_board_standard_port_label"] or "Direct Breakout"),
        "reusable_block_location": block_loc,
        "reusable_block_scope": ("package" if needs_switch else "global"),
        "can_share_physical_ic": _yn(is_switch_cell or cell_id in ("DIRECT_IO", "PROTECTED_IO")),
        "share_group_hint": (f"{_bank(ctx.pin)}_SWITCH" if is_switch_cell else ""),
        "independent_enable_required": _yn(is_switch_cell),
        # source/confidence
        "source_kind": "db_derived",
        "source_file": "stm32_profiles.sqlite",
        "source_confidence": "HIGH" if guaranteed_elec or needs_switch else "MEDIUM",
        "source_notes": "",
    }
    fields.update(hel); fields.update(atk); fields.update(fab); fields.update(saf); fields.update(ui)
    # cross-group role-presence columns
    for role, col in _ROLE_PRESENCE_COLS.items():
        fields[col] = "|".join(_labels_using(ctx, role))
    fields["roles_seen_all_groups"] = "|".join(sorted(nn)) or "NC"
    fields["special_roles_seen_all_groups"] = "|".join(sorted(ctx.services)) or "none"
    fields["functions_seen_all_groups"] = "|".join(
        sorted({f["function_name"] for f in ctx.exact_functions})) or "none"
    return fields


_ROLE_PRESENCE_COLS = {
    R.IO: "group_labels_using_io", R.VDD: "group_labels_using_vdd",
    R.VDDA: "group_labels_using_vdda", R.VSS: "group_labels_using_vss",
    R.VSSA: "group_labels_using_vssa", R.VBAT: "group_labels_using_vbat",
    R.VREF: "group_labels_using_vref", R.VCAP: "group_labels_using_vcap",
    R.BOOT: "group_labels_using_boot", R.NRST: "group_labels_using_nrst",
    R.OSC_IN: "group_labels_using_osc", R.USB_DP: "group_labels_using_usb",
    R.NC: "group_labels_using_nc",
}


def _bank(pin: int) -> str:
    return f"BANK_{_bank_letter(pin)}"


def _bank_letter(pin: int) -> str:
    return "ABCDEF"[min((pin - 1) // 32, 5)]


# ── per-(group, pin) branch fields ─────────────────────────────────────────

def branch_fields(ctx: PinContext, role_in_group: str) -> dict:
    """The active branch this pin uses *in one specific exact group*, plus the
    Victim Card branch identity columns for that branch."""
    needs_switch = stability(ctx) in _MIXED
    requires_switch = needs_switch and role_in_group != R.NC
    dest = _dest_for(role_in_group, ctx.lane)
    ctrl = _enable_net(ctx.pin, role_in_group) if requires_switch else "NONE"
    nets = [dest] + ([ctrl] if requires_switch else [])
    return {
        "active_branch_role_for_group": role_in_group,
        "active_branch_destination_for_group": dest,
        "active_branch_control_net_for_group": ctrl,
        "active_branch_requires_switch": _yn(requires_switch),
        "active_branch_component_class": _VC_COMPONENT_CLASS.get(
            victim_card_cell(ctx)[0], "REVIEW") if requires_switch else "DIRECT",
        # Victim Card branch identity for this row's branch
        "victim_card_branch_id": f"P{ctx.pin:03d}_{role_in_group}",
        "victim_card_branch_display_name": f"{role_in_group} branch",
        "victim_card_branch_destination": dest,
        "victim_card_branch_groups": "|".join(_labels_using(ctx, role_in_group)),
        "victim_card_required_nets": "|".join(nets),
    }
