"""
kicad_blocks.py — single source of truth for the reusable hardware cells and
sparse parent service routers.

The spec data here is authored once as Python structures and emitted to the
``hardware/cell_library/*.yml`` + ``hardware/parent_routers/*.yml`` library and
re-used by :mod:`render_docs` to produce matching markdown.  The daughter card
makes each socket pin safe; the parent reuses these standardized service blocks.
"""
from __future__ import annotations

from . import yamlemit
from .paths import cell_library_dir, parent_routers_dir, ensure_dirs

# ── reusable card/parent hardware cells (spec section 9) ───────────────────

CELL_SPECS: dict[str, dict] = {
    "CELL_DIRECT_IO": {
        "cell_id": "CELL_DIRECT_IO",
        "cell_name": "Direct IO series cell",
        "board_side": "card",
        "purpose": "Permanently-safe IO lane with a series resistor only.",
        "used_when": "Role set is exactly {IO} across every supported exact pinout "
                     "group and the lane needs no isolation/mux/measurement.",
        "hierarchical_pins": ["SOCKET_Pxxx", "CARD_LANE_xxx", "GND"],
        "internal_nets": ["SOCKET_Pxxx", "CARD_LANE_xxx"],
        "required_components": [
            {"ref": "R_IO_xxx", "value": "33R", "note": "22R-100R series, default 33R/47R"},
            {"ref": "D_ESD_xxx", "value": "DNI", "note": "optional ESD clamp footprint"},
        ],
        "component_requirements": ["series_resistor", "optional_esd_dni"],
        "default_state": "passive (always connected)",
        "enable_logic": "none",
        "safety_rules": [
            "Never use if the role set contains power, ground, VCAP, VBAT, VREF, OSC or USB.",
        ],
        "signal_integrity_rules": ["Keep stub short; place R near the socket."],
        "kicad_sheet_name": "cell_direct_io",
        "screenshot_path": "docs/images/cells/CELL_DIRECT_IO.png",
        "ascii_schematic": "SOCKET_Pxxx --[ R_IO_xxx 33R ]--+--> CARD_LANE_xxx\n"
                           "                                 |\n"
                           "                          (DNI ESD to GND)",
        "example_role_sets": ["IO"],
        "notes": "Lowest-cost safe lane; upgrade to CELL_IO_SWITCH if isolation is needed.",
    },
    "CELL_IO_SWITCH": {
        "cell_id": "CELL_IO_SWITCH",
        "cell_name": "Isolating IO switch cell",
        "board_side": "card",
        "purpose": "IO-only lane that needs isolation, muxing, measurement or fault containment.",
        "used_when": "Role set is {IO} but the lane carries a sensitive service "
                     "(SWD/analog/UART) or must be isolated when no card is selected.",
        "hierarchical_pins": ["SOCKET_Pxxx", "CARD_LANE_xxx", "EN_IO_xxx", "VSW_IO", "GND"],
        "internal_nets": ["SOCKET_Pxxx", "SW_MID_xxx", "CARD_LANE_xxx"],
        "required_components": [
            {"ref": "U_IO_xxx", "value": "bidir_analog_switch", "note": "low Ron, low C"},
            {"ref": "R_IO_xxx", "value": "33R", "note": "22R-100R series"},
        ],
        "component_requirements": {
            "IO_LOW_SPEED": ["bidirectional", "low_leakage", "powered_off_protection"],
            "IO_SWD_SAFE": ["bidirectional", "low_capacitance", "low_Ron", "fast_edge_safe"],
            "IO_ANALOG_SAFE": ["bidirectional", "very_low_leakage", "low_charge_injection"],
            "IO_UART_SAFE": ["bidirectional", "vtarget_compatible", "moderate_capacitance"],
        },
        "default_state": "OFF (isolated) until EN_IO_xxx asserted",
        "enable_logic": "EN_IO_xxx active-high; pulldown default OFF",
        "safety_rules": ["Switch must tolerate VTARGET-referenced IO; default isolated."],
        "signal_integrity_rules": [
            "Use low-capacitance switch for SWD/clock lanes.",
            "Use low-leakage switch for analog lanes.",
        ],
        "kicad_sheet_name": "cell_io_switch",
        "screenshot_path": "docs/images/cells/CELL_IO_SWITCH.png",
        "ascii_schematic": "SOCKET_Pxxx --[ U_IO_xxx bidir switch ]--[ R_IO_xxx ]--> CARD_LANE_xxx\n"
                           "                     ^EN_IO_xxx (default OFF)",
        "example_role_sets": ["IO (+ swd/analog/uart service)"],
        "notes": "Variant selected from lane sensitivity class.",
    },
    "CELL_FULL_ROLE_SWITCH": {
        "cell_id": "CELL_FULL_ROLE_SWITCH",
        "cell_name": "Universal per-pin role-switch cell",
        "board_side": "card",
        "purpose": "The critical universal safety cell: a socket pin that is IO on one "
                   "MCU and VDD/VSS/VCAP/VBAT/VDDA/VREF on another.",
        "used_when": "Role set contains IO together with any supply/return/cap role.",
        "hierarchical_pins": [
            "SOCKET_Pxxx", "CARD_LANE_xxx", "VTARGET", "VDDA_TARGET", "VBAT_TARGET",
            "VREF_TARGET", "GND", "AGND", "ROLE_SEL_xxx_0", "ROLE_SEL_xxx_1",
            "ROLE_SEL_xxx_2", "ROLE_SEL_xxx_3", "EN_ROLE_CELL_xxx", "ROLE_CLEAR_N",
            "GLOBAL_ROLE_ENABLE", "TARGET_POWER_OFF_OK", "PROFILE_VALID",
            "CARD_PRESENT", "FAULT_N", "TP_SOCKET_xxx", "TP_LANE_xxx", "FAULT_SENSE_xxx",
        ],
        "internal_nets": ["SOCKET_Pxxx_COMMON", "ROLE_DECODE_xxx[0..15]"],
        "required_components": [
            {"ref": "U_IO_xxx", "value": "bidir_io_switch"},
            {"ref": "U_VDD_xxx", "value": "load_switch"},
            {"ref": "U_VDDA_xxx", "value": "low_noise_load_switch"},
            {"ref": "U_GND_xxx", "value": "ground_fet"},
            {"ref": "U_VCAP_xxx", "value": "low_Ron_switch"},
            {"ref": "C_VCAP_xxx", "value": "from_mcu_profile"},
            {"ref": "U_VBAT_xxx", "value": "ideal_diode_or_load_switch"},
            {"ref": "U_VREF_xxx", "value": "low_leakage_analog_switch"},
            {"ref": "U_DEC_xxx", "value": "one_hot_decoder"},
        ],
        "component_requirements": {
            "decoder": "4-bit role code -> one-hot enables; only one role active",
            "io_switch": ["bidirectional", "low_capacitance", "vtarget_compatible"],
            "vdd_switch": ["load_switch", "low_Ron", "reverse_block", "default_off"],
            "gnd_switch": ["very_low_RDSon", "near_socket", "default_off"],
            "vcap_switch": ["low_Ron", "local_cap", "default_off"],
        },
        "default_state": "all_off (role code 0000 = NC)",
        "enable_logic": "cell_enable_allowed = CARD_PRESENT AND PROFILE_VALID AND "
                        "TARGET_POWER_OFF_OK AND GLOBAL_ROLE_ENABLE AND NOT FAULT",
        "role_codes": {
            "0000": "OFF/NC", "0001": "IO", "0010": "VDD", "0011": "VDDA",
            "0100": "GND/VSS", "0101": "VSSA", "0110": "VCAP", "0111": "VBAT",
            "1000": "VREF", "1001": "BOOT_STRAP", "1010": "NRST_OPEN_DRAIN",
            "1011": "OSC_LOCAL", "1111": "RESERVED_INVALID",
        },
        "safety_rules": [
            "No role switching while VTARGET is enabled.",
            "No VDD and GND path active together.",
            "No IO and VDD path active together.",
            "No IO and VCAP path active together.",
            "VCAP capacitor never connected when the pin is IO/UART/SWD/ADC.",
            "No VDD/VSS/VCAP-ambiguous pin may bypass this cell.",
            "Every switch enable defaults OFF with pulldowns.",
        ],
        "signal_integrity_rules": [
            "Keep VCAP cap and ground FET close to the socket.",
            "IO switch low capacitance if the lane can be SWD/SPI/clock.",
        ],
        "kicad_sheet_name": "cell_full_role_switch",
        "screenshot_path": "docs/images/cells/CELL_FULL_ROLE_SWITCH.png",
        "ascii_schematic": (
            "                         +-- U_IO_xxx --[R_IO]--> CARD_LANE_xxx\n"
            "                         +-- U_VDD_xxx --------> VTARGET\n"
            "                         +-- U_VDDA_xxx -------> VDDA_TARGET\n"
            "SOCKET_Pxxx_COMMON ------+-- U_GND_xxx --------> GND\n"
            "                         +-- U_VCAP_xxx -[C]---> GND\n"
            "                         +-- U_VBAT_xxx -------> VBAT_TARGET\n"
            "                         +-- U_VREF_xxx -------> VREF_TARGET\n"
            "                         +-- (0000) all OFF = NC\n"
            "        one-hot decoder <- ROLE_SEL_xxx[3:0], gated by cell_enable_allowed"),
        "example_role_sets": ["IO,VDD", "IO,VSS", "IO,VCAP,VDD,VSS", "IO,VDD,VDDA,VREF"],
        "notes": "Switched ground requires engineering review; prefer direct VSS where fixed.",
    },
    "CELL_POWER_ONLY": {
        "cell_id": "CELL_POWER_ONLY",
        "cell_name": "Fixed supply cell",
        "board_side": "card",
        "purpose": "Pins that are always a supply (VDD/VDDA/VBAT/VREF) across all groups.",
        "used_when": "Role set is a pure supply with no IO.",
        "hierarchical_pins": ["SOCKET_Pxxx", "VTARGET", "GND"],
        "internal_nets": ["SOCKET_Pxxx"],
        "required_components": [
            {"ref": "FB_xxx", "value": "0R/ferrite", "note": "optional"},
            {"ref": "C_dec_xxx", "value": "100nF", "note": "local decoupling"},
        ],
        "component_requirements": ["low_impedance", "local_decoupling"],
        "default_state": "connected (direct) unless per-pin isolation required",
        "enable_logic": "none (or load switch, default OFF, if isolation needed)",
        "safety_rules": ["Direct low-impedance routing unless isolation is specified."],
        "signal_integrity_rules": ["Decouple close to the socket pin."],
        "kicad_sheet_name": "cell_power_only",
        "screenshot_path": "docs/images/cells/CELL_POWER_ONLY.png",
        "ascii_schematic": "VTARGET --[FB/0R]--+--> SOCKET_Pxxx\n                   |\n                 [C_dec]\n                   |\n                  GND",
        "example_role_sets": ["VDD", "VDDA", "VBAT", "VREF"],
        "notes": "VDDA/VREF use the CELL_ANALOG_SUPPLY low-noise variant.",
    },
    "CELL_GROUND_ONLY": {
        "cell_id": "CELL_GROUND_ONLY",
        "cell_name": "Fixed ground cell",
        "board_side": "card",
        "purpose": "Pins that are always VSS/VSSA across all groups.",
        "used_when": "Role set is pure ground with no IO.",
        "hierarchical_pins": ["SOCKET_Pxxx", "GND"],
        "internal_nets": ["SOCKET_Pxxx"],
        "required_components": [],
        "component_requirements": ["direct_low_impedance"],
        "default_state": "connected (direct)",
        "enable_logic": "none",
        "safety_rules": ["Do not switch fixed ground pins."],
        "signal_integrity_rules": ["Stitch to the ground plane with multiple vias."],
        "kicad_sheet_name": "cell_ground_only",
        "screenshot_path": "docs/images/cells/CELL_GROUND_ONLY.png",
        "ascii_schematic": "SOCKET_Pxxx ----+---- GND plane",
        "example_role_sets": ["VSS", "VSSA"],
        "notes": "Switched ground only ever via CELL_FULL_ROLE_SWITCH for ambiguous pins.",
    },
    "CELL_VCAP_ONLY": {
        "cell_id": "CELL_VCAP_ONLY",
        "cell_name": "VCAP local-cap cell",
        "board_side": "card",
        "purpose": "Pins that are always VCAP (internal LDO filter).",
        "used_when": "Role set is exactly {VCAP}.",
        "hierarchical_pins": ["SOCKET_Pxxx", "GND"],
        "internal_nets": ["SOCKET_Pxxx"],
        "required_components": [{"ref": "C_VCAP_xxx", "value": "from_mcu_profile",
                                 "note": "place <1mm from socket"}],
        "component_requirements": ["local_capacitor"],
        "default_state": "connected (direct to local cap)",
        "enable_logic": "none",
        "safety_rules": ["Never route VCAP to the parent as a normal lane."],
        "signal_integrity_rules": ["Cap close to socket; short, wide trace."],
        "kicad_sheet_name": "cell_vcap_only",
        "screenshot_path": "docs/images/cells/CELL_VCAP_ONLY.png",
        "ascii_schematic": "SOCKET_Pxxx --+--[ C_VCAP_xxx ]--+\n              |                  |\n            (pin)               GND",
        "example_role_sets": ["VCAP"],
        "notes": "Value from the MCU profile.",
    },
    "CELL_ANALOG_SUPPLY": {
        "cell_id": "CELL_ANALOG_SUPPLY",
        "cell_name": "Analog supply filter cell",
        "board_side": "card",
        "purpose": "Low-noise VDDA/VREF supply with filtering (specialization of POWER_ONLY).",
        "used_when": "Role set is a pure analog supply (VDDA/VREF) with no IO.",
        "hierarchical_pins": ["SOCKET_Pxxx", "VDDA_TARGET", "VREF_TARGET", "AGND"],
        "internal_nets": ["SOCKET_Pxxx"],
        "required_components": [
            {"ref": "FB_a_xxx", "value": "ferrite"},
            {"ref": "C_a_xxx", "value": "1uF+10nF", "note": "RC/LC filter"},
        ],
        "component_requirements": ["low_noise", "rc_or_lc_filter"],
        "default_state": "connected",
        "enable_logic": "optional low-noise load switch, default OFF",
        "safety_rules": ["Reference to analog ground; isolate from digital return."],
        "signal_integrity_rules": ["Guard with analog ground; keep away from digital."],
        "kicad_sheet_name": "cell_analog_supply",
        "screenshot_path": "docs/images/cells/CELL_ANALOG_SUPPLY.png",
        "ascii_schematic": "VDDA_TARGET --[FB]--+--> SOCKET_Pxxx\n                    [C filter]\n                    AGND",
        "example_role_sets": ["VDDA", "VREF"],
        "notes": "Classifier currently emits CELL_POWER_ONLY (variant VDDA/VREF) for these.",
    },
    "CELL_BOOT_STRAP": {
        "cell_id": "CELL_BOOT_STRAP",
        "cell_name": "BOOT0 strap cell",
        "board_side": "card",
        "purpose": "BOOT0 / boot-mode strap with controlled default and parent override.",
        "used_when": "Pin carries the boot0 service or BOOT role.",
        "hierarchical_pins": ["SOCKET_BOOT_PIN", "VTARGET", "GND", "PARENT_BOOT0_CTRL", "EN_BOOT_xxx"],
        "internal_nets": ["BOOT_NODE_xxx"],
        "required_components": [
            {"ref": "R_pd_xxx", "value": "100k", "note": "weak default pulldown"},
            {"ref": "R_pu_xxx", "value": "10k", "note": "controlled pullup to VTARGET"},
            {"ref": "U_boot_xxx", "value": "io_switch", "note": "optional parent control"},
        ],
        "component_requirements": ["strap_network", "optional_switch"],
        "default_state": "normal boot (BOOT0 low)",
        "enable_logic": "Parent forces bootloader via PARENT_BOOT0_CTRL before reset release.",
        "safety_rules": ["BOOT defaults to safe normal boot; never hard-driven against VTARGET."],
        "signal_integrity_rules": ["Low-speed strap; no special routing."],
        "kicad_sheet_name": "cell_boot_strap",
        "screenshot_path": "docs/images/cells/CELL_BOOT_STRAP.png",
        "ascii_schematic": "VTARGET--[R_pu 10k]--+--SOCKET_BOOT_PIN\n                      |\n          PARENT_BOOT0_CTRL--[U_boot]\n                      |\n                   [R_pd 100k]\n                      |\n                     GND",
        "example_role_sets": ["BOOT", "IO (+ boot0 service)"],
        "notes": "Set boot before releasing reset.",
    },
    "CELL_NRST_OPEN_DRAIN": {
        "cell_id": "CELL_NRST_OPEN_DRAIN",
        "cell_name": "NRST open-drain cell",
        "board_side": "card",
        "purpose": "Reset line with VTARGET-referenced pullup and open-drain parent drive.",
        "used_when": "Pin carries the nrst service or NRST role.",
        "hierarchical_pins": ["SOCKET_NRST", "VTARGET", "GND", "PARENT_NRST", "EN_RST_xxx"],
        "internal_nets": ["NRST_NODE"],
        "required_components": [
            {"ref": "R_pu_rst", "value": "10k", "note": "pullup to VTARGET"},
            {"ref": "C_rst", "value": "100nF", "note": "filter"},
            {"ref": "Q_rst", "value": "open_drain_fet", "note": "parent pulls low only"},
        ],
        "component_requirements": ["open_drain_reset"],
        "default_state": "released (pulled up to VTARGET)",
        "enable_logic": "Parent may pull NRST low; never drives high.",
        "safety_rules": ["Do not push-pull drive reset high.", "Pullup references VTARGET."],
        "signal_integrity_rules": ["Add filter cap; keep reset away from noisy nets."],
        "kicad_sheet_name": "cell_nrst_open_drain",
        "screenshot_path": "docs/images/cells/CELL_NRST_OPEN_DRAIN.png",
        "ascii_schematic": "VTARGET--[R_pu 10k]--+--SOCKET_NRST\n                      +--[C 100nF]--GND\n                      |\n        PARENT_NRST--[Q open-drain]--GND",
        "example_role_sets": ["NRST", "IO (+ nrst service)"],
        "notes": "Open-drain only.",
    },
    "CELL_OSC_LOCAL": {
        "cell_id": "CELL_OSC_LOCAL",
        "cell_name": "Local oscillator cell",
        "board_side": "card",
        "purpose": "HSE/LSE crystal + load caps kept local to the card.",
        "used_when": "Pin is an oscillator input/output (OSC_IN/OSC_OUT).",
        "hierarchical_pins": ["OSC_IN", "OSC_OUT", "GND", "PARENT_CLK_INJECT (optional)"],
        "internal_nets": ["OSC_IN", "OSC_OUT"],
        "required_components": [
            {"ref": "Y_xxx", "value": "crystal", "note": "frequency from profile"},
            {"ref": "C_l1_xxx", "value": "load_cap"},
            {"ref": "C_l2_xxx", "value": "load_cap"},
        ],
        "component_requirements": ["local_crystal_network"],
        "default_state": "local crystal active",
        "enable_logic": "optional parent clock injection via a separate clock buffer",
        "safety_rules": ["Do not route crystal pins through the parent universal mux."],
        "signal_integrity_rules": ["Crystal + load caps close to socket; guard ground."],
        "kicad_sheet_name": "cell_osc_local",
        "screenshot_path": "docs/images/cells/CELL_OSC_LOCAL.png",
        "ascii_schematic": "OSC_IN --+--[Y_xxx]--+-- OSC_OUT\n         |           |\n      [C_l1]       [C_l2]\n         |           |\n        GND         GND",
        "example_role_sets": ["OSC_IN", "OSC_OUT"],
        "notes": "Keep load caps local; optional parent clock buffer to OSC_IN only.",
    },
    "CELL_USB_PAIR": {
        "cell_id": "CELL_USB_PAIR",
        "cell_name": "USB differential pair cell",
        "board_side": "card",
        "purpose": "USB D+/D- differential routing through a USB-rated switch.",
        "used_when": "Pin carries usb_dp/usb_dm service or USB role (and is not power-ambiguous).",
        "hierarchical_pins": ["SOCKET_USB_DP", "SOCKET_USB_DM", "CARD_USB_DP",
                              "CARD_USB_DM", "EN_USB_xxx", "GND"],
        "internal_nets": ["CARD_USB_DP", "CARD_USB_DM"],
        "required_components": [
            {"ref": "U_usb_xxx", "value": "usb_rated_diff_switch"},
            {"ref": "D_esd_usb", "value": "low_C_ESD", "note": "near parent connector"},
        ],
        "component_requirements": ["usb_rated_diff_switch"],
        "default_state": "OFF until EN_USB_xxx asserted",
        "enable_logic": "EN_USB_xxx; default OFF",
        "safety_rules": ["Do not use generic GPIO analog muxes for USB."],
        "signal_integrity_rules": ["Route as 90Ω differential pair; length-match DP/DM; minimal stubs."],
        "kicad_sheet_name": "cell_usb_pair",
        "screenshot_path": "docs/images/cells/CELL_USB_PAIR.png",
        "ascii_schematic": "SOCKET_USB_DP --\\               /-- CARD_USB_DP\n                 U_usb (USB-rated diff sw)\n"
                           "SOCKET_USB_DM --/               \\-- CARD_USB_DM",
        "example_role_sets": ["USB_DP", "USB_DM", "IO (+ usb service)"],
        "notes": "ESD near the external USB connector on the parent.",
    },
    "CELL_NC": {
        "cell_id": "CELL_NC",
        "cell_name": "Not-connected cell",
        "board_side": "card",
        "purpose": "Socket pin with no assigned role for this package.",
        "used_when": "Role set is empty or exactly {NC}.",
        "hierarchical_pins": ["SOCKET_Pxxx"],
        "internal_nets": [],
        "required_components": [{"ref": "TP_xxx", "value": "DNI", "note": "optional test pad"}],
        "component_requirements": ["none"],
        "default_state": "not connected",
        "enable_logic": "none",
        "safety_rules": ["Leave isolated; do not tie to any rail."],
        "signal_integrity_rules": [],
        "kicad_sheet_name": "cell_nc",
        "screenshot_path": "docs/images/cells/CELL_NC.png",
        "ascii_schematic": "SOCKET_Pxxx --x  (no connection)",
        "example_role_sets": ["NC"],
        "notes": "Higher superset lanes on smaller packages are NC.",
    },
}

# ── sparse parent service routers (spec section 12) ────────────────────────

ROUTER_SPECS: dict[str, dict] = {
    "ROUTER_SWD": {
        "router_id": "ROUTER_SWD",
        "purpose": "Attach a debugger to the selected target's SWD lanes.",
        "inputs": ["known SWDIO candidate lanes", "known SWCLK candidate lanes",
                   "known SWO candidate lanes"],
        "outputs": ["PARENT_SWDIO", "PARENT_SWCLK", "PARENT_SWO"],
        "switch_class": "low_capacitance_bidirectional",
        "rules": [
            "SWDIO is bidirectional; SWCLK is edge-sensitive.",
            "Low-capacitance analog/pass switches only.",
            "22R series near the driver path; avoid giant crosspoints and long stubs.",
        ],
        "ascii_schematic": "candidate SWDIO lanes >--[low-C mux]--> PARENT_SWDIO\n"
                           "candidate SWCLK lanes >--[low-C mux]--[22R]--> PARENT_SWCLK\n"
                           "candidate SWO   lanes >--[low-C mux]--> PARENT_SWO",
        "notes": "Sparse: only lanes that ever carry SWD are connected.",
    },
    "ROUTER_UART_BOOT": {
        "router_id": "ROUTER_UART_BOOT",
        "purpose": "Attach the host to the target's boot UART.",
        "inputs": ["known boot UART TX candidate lanes", "known boot UART RX candidate lanes"],
        "outputs": ["PARENT_UART_RX_FROM_TARGET", "PARENT_UART_TX_TO_TARGET"],
        "switch_class": "vtarget_compatible_signal",
        "rules": [
            "MCU_TX -> PARENT_UART_RX_FROM_TARGET.",
            "PARENT_UART_TX_TO_TARGET -> MCU_RX.",
            "VTARGET-compatible signal switches.",
        ],
        "ascii_schematic": "MCU_TX lanes >--[mux]--> PARENT_UART_RX_FROM_TARGET\n"
                           "PARENT_UART_TX_TO_TARGET >--[mux]--> MCU_RX lanes",
        "notes": "Candidate list may be broad; narrow to the boot UART per profile.",
    },
    "ROUTER_USB_FS": {
        "router_id": "ROUTER_USB_FS",
        "purpose": "Attach a USB-FS host to the target's USB pair.",
        "inputs": ["known USB_DP/USB_DM lane pairs"],
        "outputs": ["PARENT_USB_DP", "PARENT_USB_DM"],
        "switch_class": "usb_rated_differential",
        "rules": ["USB-rated differential switches only; no generic GPIO mux.",
                  "90Ω differential pair; length-match; minimal stubs."],
        "ascii_schematic": "USB_DP pairs >==[USB-rated diff sw]==> PARENT_USB_DP\n"
                           "USB_DM pairs >==[USB-rated diff sw]==> PARENT_USB_DM",
        "notes": "ESD near the external connector.",
    },
    "ROUTER_ADC_PROBE": {
        "router_id": "ROUTER_ADC_PROBE",
        "purpose": "Route an analog-capable lane to the measurement front end.",
        "inputs": ["known analog-capable candidate lanes"],
        "outputs": ["PARENT_ADC_PROBE"],
        "switch_class": "low_leakage_analog",
        "rules": ["Low-leakage analog mux.", "Separate analog probe from noisy digital.",
                  "Add optional RC/protection footprint."],
        "ascii_schematic": "analog lanes >--[low-leakage analog mux]--> PARENT_ADC_PROBE",
        "notes": "Guard with analog ground.",
    },
    "ROUTER_GPIO_ACCESS": {
        "router_id": "ROUTER_GPIO_ACCESS",
        "purpose": "Expose safe IO lanes to GPIO headers, a logic-analyzer bus and a probe matrix.",
        "inputs": ["safe IO card lanes (CELL_DIRECT_IO / CELL_IO_SWITCH)"],
        "outputs": ["PARENT_GPIO_PROBE", "PARENT_LOGIC_ANALYZER_BUS"],
        "switch_class": "general_bidirectional",
        "rules": ["Broader than SWD/USB/ADC, but never includes power/ground/VCAP lanes "
                  "unless the card role cell exposes them as IO for the selected profile."],
        "ascii_schematic": "safe IO lanes >--[matrix]--> PARENT_GPIO_PROBE / LA bus",
        "notes": "General access; still excludes unsafe lanes.",
    },
    "ROUTER_BOOT_RESET": {
        "router_id": "ROUTER_BOOT_RESET",
        "purpose": "Drive BOOT0 and NRST as standardized parent controls.",
        "inputs": ["known NRST candidate lanes", "known BOOT0 candidate lanes"],
        "outputs": ["PARENT_NRST", "PARENT_BOOT0_CTRL"],
        "switch_class": "open_drain_and_strap",
        "rules": ["NRST open-drain (pull low only).",
                  "BOOT0 set before releasing reset; never hard-driven against VTARGET."],
        "ascii_schematic": "PARENT_NRST --[open-drain]--> NRST candidate lanes\n"
                           "PARENT_BOOT0_CTRL --[strap drive]--> BOOT0 candidate lanes",
        "notes": "Sequenced with target power-off.",
    },
}


# ── emit YAML library ──────────────────────────────────────────────────────

def write_cell_library() -> int:
    d = cell_library_dir()
    ensure_dirs(d)
    index = {"cells": []}
    for cid, spec in CELL_SPECS.items():
        (d / f"{cid}.yml").write_text(yamlemit.dumps(spec), encoding="utf-8")
        index["cells"].append({
            "cell_id": cid, "cell_name": spec["cell_name"],
            "board_side": spec["board_side"], "file": f"{cid}.yml",
            "screenshot": spec["screenshot_path"],
        })
    (d / "cell_library.yml").write_text(yamlemit.dumps(index), encoding="utf-8")
    return len(CELL_SPECS)


def write_router_library() -> int:
    d = parent_routers_dir()
    ensure_dirs(d)
    for rid, spec in ROUTER_SPECS.items():
        (d / f"{rid}.yml").write_text(yamlemit.dumps(spec), encoding="utf-8")
    return len(ROUTER_SPECS)
