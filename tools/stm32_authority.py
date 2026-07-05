"""stm32_authority.py — the Pinout Authority Generator (Layer B).

Derives one canonical per-package authority (YAML + JSON) plus the raw per-(part,pin)
TSV from the CubeMX database built by stm32_db. Self-contained (stdlib only:
includes a tiny block-style YAML emitter so there is no ruamel/PyYAML dependency).

Per socket position: pin_names (per-part, exposes F469/F479 shifts), role_set,
switch decision + deterministic ADG714 {cell, channel, destination}, extraction
tags, electrical hints, and bootloader_periph. Plus a rollup (cells_min /
cells_as_built) and a manifest.

Requirements authority: Brain/Wiki/Specs/Pinout Authority Generator.md.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import stm32_db as db

# ─────────────────────────────────────────────────────────────────────────────
# External data tables (cited). Filled from AN2606 + the vault + ST datasheets.
# ─────────────────────────────────────────────────────────────────────────────

# routing-identity -> canonical destination net (confirm against the vault
# Connector Contract / Net Naming Contract). Defaults to the switch-engine map.
NET_DICT: dict = dict(db.TARGET_NET)

# family -> {bootloader_periph: {canonical_pin_name, ...}}  (from ST AN2606).
# A socket position is tagged with a periph when one of its per-part pin names
# matches, for that part's family. Source: ST AN2606 Rev 62 (Mar 2024) system-
# memory-boot-mode per-device tables, EXHAUSTIVELY transcribed 2026-07-02 (225
# device/peripheral/pin-option rows across F0-F7); PDF saved in the vault at
# Sources/Datasheets/. Per family = the UNION of ROM-bootloader pins across its
# sub-lines and pin-options. USART1=PA9/PA10 and USB-DFU FS=PA11/PA12 are
# universal. Notable: F1 CAN2 is PB5/PB6 + PA9 VBUS-sense; F3 adds I2C3 (PA8/PB5);
# F4 adds SPI1-4 + I2C4; F7 has BOTH CAN1 (PD0/PD1) and CAN2 (PB5/PB13). Higher-
# density pins (PIx/PEx) never match on LQFP64/LQFP100. See docs/stm32-pins.md.
BOOTLOADER_PINS: dict = {
    "STM32F0": {
        "USART": {"PA2", "PA3", "PA9", "PA10", "PA14", "PA15"},
        "I2C": {"PB6", "PB7"},
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F1": {
        "USART": {"PA9", "PA10", "PD5", "PD6"},
        "CAN": {"PB5", "PB6"},                       # F105/107 CAN2 RX=PB5, TX=PB6
        "USB-DFU": {"PA9", "PA11", "PA12"},          # PA9 = VBUS sense on F105/107
    },
    "STM32F2": {
        "USART": {"PA9", "PA10", "PB10", "PB11", "PC10", "PC11"},
        "CAN": {"PB5", "PB13"},                      # CAN2 RX=PB5, TX=PB13
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F3": {
        "USART": {"PA2", "PA3", "PA9", "PA10", "PD5", "PD6"},
        "I2C": {"PA8", "PB5", "PB6", "PB7"},         # I2C1 PB6/7 + I2C3 PA8/PB5
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F4": {
        "USART": {"PA2", "PA3", "PA9", "PA10", "PB10", "PB11", "PC10", "PC11", "PD5", "PD6"},
        "CAN": {"PB5", "PB13"},
        "I2C": {"PA8", "PB3", "PB4", "PB6", "PB7", "PB9", "PB10", "PB11", "PB14", "PB15", "PC9", "PF0", "PF1"},
        "SPI": {"PA4", "PA5", "PA6", "PA7", "PA15", "PB4", "PB5", "PB12", "PB13", "PB14", "PB15",
                "PC2", "PC3", "PC7", "PC10", "PC11", "PC12", "PE11", "PE12", "PE13", "PE14",
                "PI0", "PI1", "PI2", "PI3"},
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F7": {
        "USART": {"PA9", "PA10", "PB10", "PB11", "PC10", "PC11"},
        "CAN": {"PB5", "PB13", "PD0", "PD1"},        # CAN1 PD0/PD1 + CAN2 PB5/PB13
        "I2C": {"PA8", "PB6", "PB9", "PC9", "PF0", "PF1"},
        "SPI": {"PA4", "PA5", "PA6", "PA7", "PE11", "PE12", "PE13", "PE14", "PI0", "PI1", "PI2", "PI3"},
        "USB-DFU": {"PA11", "PA12"},
    },
}

# Per-family I/O electrical limits, from the official ST datasheets fetched
# 2026-07-01 and saved to the vault Sources/Datasheets/ (Hard Rule 10; PDFs
# verified %PDF + rev). Values are datasheet absolute-max / operating limits.
#   io_ma       = per-pin I_IO abs-max (source and sink), mA
#   total_io_ma = ΣI_IO (or the device I_VDD/I_VSS ceiling that bounds it), mA
#   inj_ma      = per-pin I_INJ, mA (±; FT/5V-tolerant pins take -inj/+0 only)
#   vdd_v/vdda_v = operating range V; temp_c = 6-suffix ambient (7-suffix → +105)
#   ft_5v       = family has 5V-tolerant (FT) I/O pins
# Per-pin I_IO = ±25 mA and ΣI_INJ = ±25 mA are uniform across STM32F0–F7.
# Full per-field citations: see docs/stm32-pins.md "I/O electrical (fetched)".
FAMILY_ELECTRICAL: dict = {
    "STM32F0": {"io_ma": 25, "total_io_ma": 80,  "metric": "sigma_io",     "sup_ma": None, "inj_ma": 5, "vdd_v": [2.0, 3.6], "vdda_v": [2.4, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DS9826 Rev 6, Table 22 §6.2 p.52"},
    "STM32F1": {"io_ma": 25, "total_io_ma": 150, "metric": "supply_total", "sup_ma": 150,  "inj_ma": 5, "vdd_v": [2.0, 3.6], "vdda_v": [2.0, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DS5319 Rev 20, Table 7 §5.2 p.37"},
    "STM32F2": {"io_ma": 25, "total_io_ma": 120, "metric": "supply_total", "sup_ma": 120,  "inj_ma": 5, "vdd_v": [1.8, 3.6], "vdda_v": [1.8, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DS6329 Rev 18, Table 12 §6.2 p.70"},
    "STM32F3": {"io_ma": 25, "total_io_ma": 80,  "metric": "sigma_io",     "sup_ma": 160,  "inj_ma": 5, "vdd_v": [2.0, 3.6], "vdda_v": [2.0, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DocID026415 Rev 5, Table 17 §6.2 p.71"},
    "STM32F4": {"io_ma": 25, "total_io_ma": 120, "metric": "sigma_io",     "sup_ma": 240,  "inj_ma": 5, "vdd_v": [1.8, 3.6], "vdda_v": [1.8, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DS8626 (DocID022152) Rev 5, Table 12 §5.2 p.78"},
    "STM32F7": {"io_ma": 25, "total_io_ma": 120, "metric": "sigma_io",     "sup_ma": None, "inj_ma": 5, "vdd_v": [1.7, 3.6], "vdda_v": [1.7, 3.6], "temp_c": [-40, 85], "ft_5v": True, "ds": "DS10916 Rev 5, Table 16 §6.2 p.121"},
}
# total_io_ma = the datasheet's binding all-I/O current ceiling: the explicit
# ΣI_IO ("sum of all I/O + control pins") row where the DS states it
# (metric=sigma_io), else the device I_VDD/I_VSS supply total (metric=supply_total).
# sup_ma = the device I_VDD/I_VSS supply ceiling where separately stated. Verified
# 2026-07-02: every F4 sub-line has ΣI_IO = 120 mA (the old 240 was the F405/407
# *supply* total, mislabelled); the supply total varies by sub-line (below). The
# earlier "F401/F411 ~150 UNVERIFIED" guess is retired: 120 ΣI_IO / 160 supply.
F4_SUBLINE_SUPPLY_MA: dict = {   # device I_VDD/I_VSS total (mA); ΣI_IO = 120 for all
    "STM32F401": 160, "STM32F411": 160, "STM32F405/407": 240,
    "STM32F429": 270, "STM32F446": 240, "STM32F469": 290,
}
# Sources: DS10086 R5 (F401), DS10314 R8 (F411), DS9405 R13 (F429),
# DS10693/DocID027107 R6 (F446), DS11189 R8 (F469), DocID022152 R5 (F405/407).

# Per-family POWER / decoupling design data, from the official ST datasheets
# (fetched 2026-07-02, saved to the vault Sources/Datasheets/, cited). Drives the
# NETDECK plug-in-card passive BOM (card_materials in the authority rollup).
#   vcap        = family needs external cap(s) on the internal 1.2V regulator output
#   vcap_value  = the required capacitor(s)
#   vbat_v / vref_v = VBAT and VREF+ operating ranges (vref None = internally VDDA)
#   decoupling  = the datasheet's recommended decoupling recipe
#   n_vdd/n_vss = digital VDD/VSS pin count on LQFP100 (None = not verifiable)
FAMILY_POWER: dict = {
    "STM32F0": {"vcap": False, "vcap_value": None, "vbat_v": [1.65, 3.6], "vref_v": None,
                "decoupling": "3x100nF (per VDD/VSS pair) + 4.7uF bulk; VDDA 10nF+1uF; VDDIO2 100nF+4.7uF",
                "n_vdd": 3, "n_vss": 4, "ds": "DS9826 Rev 6, Fig 13 p.49 / Table 24 p.53"},
    "STM32F1": {"vcap": False, "vcap_value": None, "vbat_v": [1.8, 3.6], "vref_v": [2.4, 3.6],
                "decoupling": "5x100nF (per VDD/VSS pair) + 4.7uF bulk (on VDD3); VDDA 10nF+1uF; VREF+ 10nF+1uF",
                "n_vdd": 5, "n_vss": 5, "ds": "DS5319 Rev 20, Fig 14 p.36 / Table 9 p.38"},
    "STM32F2": {"vcap": True, "vcap_value": "2x2.2uF (VCAP_1/2, ESR<2ohm)", "vbat_v": [1.65, 3.6], "vref_v": [1.8, 3.6],
                "decoupling": "100nF per VDD/VSS pair + 4.7uF bulk; VDDA 100nF+1uF; VREF+ 100nF+1uF",
                "n_vdd": 6, "n_vss": 3, "ds": "DS6329 Rev 18, sec 3.16.2 p.26 / Fig 19 p.68 / Table 16 p.73"},
    "STM32F3": {"vcap": False, "vcap_value": None, "vbat_v": [1.65, 3.6], "vref_v": [2.0, 3.6],
                "decoupling": "100nF per VDD/VSS pair + 4.7uF bulk; VDDA 10nF+1uF; VREF+ 10nF+1uF",
                "n_vdd": 4, "n_vss": 4, "ds": "DocID026415 Rev 5, Fig 12 p.69 / Table 19 p.72"},
    "STM32F4": {"vcap": True, "vcap_value": "2x2.2uF (VCAP_1/2, ESR<2ohm)", "vbat_v": [1.65, 3.6], "vref_v": [1.8, 3.6],
                "decoupling": "100nF per VDD/VSS pair + 4.7uF bulk; VDDA 10nF+1uF; VREF+ 10nF+1uF",
                "n_vdd": None, "n_vss": None, "ds": "DS8626 (DocID022152) Rev 5, sec 2.2.16 p.26 / Fig 21 p.76 / Table 16 p.81"},
    "STM32F7": {"vcap": True, "vcap_value": "2x2.2uF (VCAP_1/2, ESR<2ohm)", "vbat_v": [1.65, 3.6], "vref_v": [1.7, 3.6],
                "decoupling": "100nF per VDD/VSS pair + 4.7uF bulk; VDDA 100nF+1uF; VREF+ 100nF+1uF; VDDUSB 100nF+1uF",
                "n_vdd": 5, "n_vss": 5, "ds": "DS10916 Rev 5, sec 3.18.1 p.28 / Fig 22 p.119 / Table 20 p.125"},
}

# Per-family set of GPIOs that are NOT 5V-tolerant (I/O structure = TTa/TC, i.e.
# 3.3V-only). Every other GPIO is structurally FT (5V-tolerant in digital mode).
# From the datasheet "Pin definitions" I/O-structure column, exhaustively
# classified 2026-07-01 (each family 100% covered; cross-checked vs the cover-page
# 5V-tolerant count where given). PC14/PC15/PH0/PH1 are FT-except-in-osc-mode where
# they are FT; any FT pin loses 5V tolerance while in analog (ADC) mode.
# Sources: DS9826 R6 T14, DS5319 R20 T5, DS6329 R18 T8, DocID026415 R5 T13,
# DS8626/DocID022152 R5 T7, DS10916 R5 T10. See docs/stm32-pins.md.
FAMILY_NOT_5V: dict = {
    "STM32F0": {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1",
                "PC0", "PC1", "PC2", "PC3", "PC4", "PC5", "PC13", "PC14", "PC15"},
    "STM32F1": {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1", "PB5",
                "PC0", "PC1", "PC2", "PC3", "PC4", "PC5", "PC13", "PC14", "PC15"},
    "STM32F2": {"PA4", "PA5"},
    "STM32F3": {"PA0", "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1", "PB2",
                "PB10", "PB11", "PB12", "PB13", "PB14", "PB15", "PC0", "PC1", "PC2", "PC3",
                "PC4", "PC5", "PC13", "PC14", "PC15", "PD8", "PD9", "PD10", "PD11", "PD12",
                "PD13", "PD14", "PD15", "PE7", "PE8", "PE9", "PE10", "PE11", "PE12", "PE13",
                "PE14", "PE15", "PF2", "PF4"},
    "STM32F4": {"PA4", "PA5"},
    "STM32F7": {"PA4", "PA5"},
}
_OSC_CAVEAT_PINS = {"PC14", "PC15", "PH0", "PH1"}   # FT except in oscillator mode
_GPIO_NAME = re.compile(r"^P[A-Z]\d+$")

_DEBUG_ROLE = {"swclk": "SWCLK", "swdio": "SWDIO", "swo": "SWO", "jtag_extra": "JTAG"}
_ANALOG_SUPPLY = {"power_vdda", "power_vref"}

# ─────────────────────────────────────────────────────────────────────────────
# Extraction-access breakout (parent-board service nets) — Layer B, orthogonal
# to the ADG714 switch fabric. A socket position is broken out to a frozen
# parent-board service net when it CAN carry a debug / bootloader / service
# function on ANY supported part (union across the family), independent of
# whether the pin also needs a switch cell. This is derived here in the
# authority from the raw CubeMX signals + pin names already in the DB, so the
# switch engine (stm32_db) is untouched and the switch counts cannot move.
#
# Nets + header pinout are frozen in the vault: Connector Contract Rev B (Left
# odd 1/3/5/7 SWD+NRST, 35/37 JTAG TDI/nTRST; Right even 2/4 USB, 6/8 boot UART,
# 10/12 OSC, 14 BOOT0, 24 VSSA_TGT) and Build Card 5E (CoreSight-20). Trace
# (TRACECLK / TRACED0-3) is reserved No-Connect per 5E. Debug port is fixed
# silicon: PA13=SWDIO, PA14=SWCLK, PB3=SWO/TDO, PA15=JTDI, PB4=NJTRST.
# Sources: Brain/Wiki/Reference/Topics/Connector Contract.md (Rev B),
# Brain/Wiki/Build Notes/Functional Blocks/5 - Service Debug/5E - Debug Breakout
# Headers.md, ST AN2606 (USART1=PA9/PA10, USB-DFU=PA11/PA12 universal boot).
# ─────────────────────────────────────────────────────────────────────────────
SERVICE_NETS = {
    "SWDIO_PARENT", "SWCLK_PARENT", "SWO_PARENT", "TDI_PARENT", "NTRST_PARENT",
    "SERVICE_NRST", "SERVICE_BOOT0", "UART_BOOT_TX", "UART_BOOT_RX",
    "SERVICE_OSC_IN", "SERVICE_OSC_OUT", "USB_DP_TGT", "USB_DN_TGT",
}

# CoreSight-20 (J_TGT_DBG_1) fixed ARM pinout -> target service net (Card 5E
# Required Connections). GND / KEY / trace-NC pins carry no target net.
CORESIGHT20 = [
    (1, "VTREF", "VTARGET"), (2, "SWDIO/TMS", "SWDIO_PARENT"), (3, "GND", "GND"),
    (4, "SWCLK/TCK", "SWCLK_PARENT"), (5, "GND", "GND"), (6, "SWO/TDO", "SWO_PARENT"),
    (7, "KEY", None), (8, "TDI", "TDI_PARENT"), (9, "GND", "GND"),
    (10, "nRESET", "SERVICE_NRST"), (11, "NC", None), (12, "NC", None),
    (13, "NC", None), (14, "nTRST", "NTRST_PARENT"), (15, "GND", "GND"),
    (16, "NC", None), (17, "GND", "GND"), (18, "GND", "GND"), (19, "GND", "GND"),
    (20, "GND", "GND"),
]
_CS20_PIN = {net: pin for pin, _sig, net in CORESIGHT20 if net and net != "GND"}


# ─────────────────────────────────────────────────────────────────────────────
# Per-position derivations
# ─────────────────────────────────────────────────────────────────────────────
def _families_at(conn: sqlite3.Connection, package: str) -> dict:
    """position -> {(family, canonical_pin_name)} across all supported parts."""
    out: dict = defaultdict(set)
    for pin, fam, nm in conn.execute(
        "SELECT p.physical_pin_number, m.family, p.canonical_pin_name "
        "FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id WHERE m.package_name = ?",
        (package,),
    ):
        out[int(pin)].add((fam, nm))
    return out


def _bootloader_periph(fam_names: set) -> list:
    """The bootloader buses a position serves, from AN2606 pin maps (deduped)."""
    found: set = set()
    for fam, nm in fam_names:
        table = BOOTLOADER_PINS.get(fam) or {}
        for periph, pins in table.items():
            if nm in pins:
                found.add(periph)
    return sorted(found)


def _electrical(conn: sqlite3.Connection, package: str) -> dict:
    """Package-wide electrical block: VDD range from CubeMX <Voltage> (per-part),
    plus the datasheet I/O limits aggregated over the families in the package
    (FAMILY_ELECTRICAL). Per-pin I_IO / I_INJ are uniform (±25 / ±5); VDD/VDDA
    ranges and the total-I/O ceiling are the widest / per-family values."""
    vmins, vmaxs, fams = [], [], set()
    for vmin, vmax, fam in conn.execute(
        "SELECT vdd_min, vdd_max, family FROM mcu WHERE package_name = ?", (package,)):
        fams.add(fam)
        try:
            if vmin:
                vmins.append(float(vmin))
            if vmax:
                vmaxs.append(float(vmax))
        except ValueError:
            pass
    known = [f for f in sorted(fams) if f in FAMILY_ELECTRICAL]
    specs = [FAMILY_ELECTRICAL[f] for f in known]

    def widest(key):
        los = [s[key][0] for s in specs]
        his = [s[key][1] for s in specs]
        return [min(los), max(his)] if los and his else None

    pfams = [f for f in known if f in FAMILY_POWER]

    def widest_p(key):
        vals = [FAMILY_POWER[f][key] for f in pfams if FAMILY_POWER[f].get(key)]
        return [min(v[0] for v in vals), max(v[1] for v in vals)] if vals else None

    return {
        "vdd_range_v": [min(vmins), max(vmaxs)] if vmins and vmaxs else None,  # CubeMX per-part
        "vdda_range_v": widest("vdda_v"),
        "vbat_range_v": widest_p("vbat_v"),
        "vref_range_v": widest_p("vref_v"),
        "temp_range_c": widest("temp_c"),
        "max_io_current_ma": max((s["io_ma"] for s in specs), default=None),   # per-pin abs-max
        "injection_current_ma": max((s["inj_ma"] for s in specs), default=None),
        "total_io_current_ma": {f: FAMILY_ELECTRICAL[f]["total_io_ma"] for f in known},
        "supply_total_ma": {f: FAMILY_ELECTRICAL[f]["sup_ma"] for f in known},
        "vcap_required": any(FAMILY_POWER[f]["vcap"] for f in pfams) if pfams else None,
        "ft_5v_tolerant": all(s["ft_5v"] for s in specs) if specs else None,
        "f4_subline_supply_ma": dict(F4_SUBLINE_SUPPLY_MA) if "STM32F4" in known else {},
        "by_family": {f: {k: FAMILY_ELECTRICAL[f][k]
                          for k in ("io_ma", "total_io_ma", "metric", "sup_ma", "inj_ma",
                                    "vdd_v", "vdda_v", "ft_5v", "ds")}
                      for f in known},
        "power": {f: dict(FAMILY_POWER[f]) for f in pfams},
    }


def _position_tags(role_names: set, fam_names: set) -> dict:
    debug = sorted({_DEBUG_ROLE[n] for n in role_names if n in _DEBUG_ROLE})
    return {
        "is_debug": bool(debug),
        "debug_role": debug,
        "is_trace": False,   # set from the breakout signal blob in build()
        "is_boot": "boot" in role_names,
        "is_clock": "oscillator_hse" in role_names,
        "is_core_power": "vcap" in role_names,
        "is_analog_supply": bool(role_names & _ANALOG_SUPPLY),
        "bootloader_periph": _bootloader_periph(fam_names),
    }


# Per-package channel policy, encoding each build card's as-built fabric
# (spec: "channel_count adds paralleled power-rail channels per the card policy").
#   dominant  — one channel per must-switch pin, routed to its dominant rail
#               (Card 7B as built: LQFP64 = 11 channels / 2 cells).
#   per_role  — one channel per non-IO role of every switched pin (must + osc),
#               mutually-exclusive branches sharing the pin's socket trace
#               (Card 7C as built: LQFP100 = 59 channels / 8 cells).
CHANNEL_POLICY = {"LQFP64": "dominant", "LQFP100": "per_role"}


def _adg714_channels(rep, policy: str = "dominant", vssa_pins: frozenset = frozenset()) -> tuple:
    """The as-built ADG714 channel map per the package's card policy. Deterministic
    packing (spec, locked 2026-06-30): switched positions ascending; under per_role each
    pin contributes one channel per non-IO identity (dominant identity first, then by
    descending part count / name), flattened, cell=i//8+1, channel=i%8+1. VSS branches
    of analog-ground pins land on VSSA_TGT (Connector Contract contact 24), not GND.
    Branches of one pin form a mutually-exclusive group (firmware one-hot).
    Returns (pin -> [ {cell, channel, s_pin, d_pin, destination, exclusive_group} ], total)."""
    if policy == "per_role":
        pins = sorted(list(rep.must_switch) + list(rep.osc_optional), key=lambda d: d.pin)
    else:
        pins = sorted(rep.must_switch, key=lambda d: d.pin)
    flat = []                                   # (decision, destination)
    branch_count: dict = {}
    for d in pins:
        if policy == "per_role":
            idents = sorted((i for i in d.identities if i != db.ID_IO),
                            key=lambda i: (-d.identities[i], i))
            dests = []
            for i in idents:
                if i == db.ID_OSC:
                    # The HSE side is part-dependent, so the card wires BOTH
                    # service nets to every osc-capable pin (Card 7C: 4x IN + 4x OUT).
                    dests += ["SERVICE_OSC_IN", "SERVICE_OSC_OUT"]
                else:
                    dests.append(d.target_nets.get(i, db.TARGET_NET[i]))
        else:
            dests = [d.primary_target_net]
        dests = ["VSSA_TGT" if (net == "GND" and d.pin in vssa_pins) else net
                 for net in dests]
        branch_count[d.pin] = len(dests)
        flat.extend((d, net) for net in dests)
    out: dict = defaultdict(list)
    for i, (d, net) in enumerate(flat):
        ch = i % 8 + 1
        out[d.pin].append({"cell": i // 8 + 1, "channel": ch,
                           "s_pin": f"S{ch}", "d_pin": f"D{ch}",
                           "destination": net,
                           "exclusive_group": d.pin if branch_count[d.pin] > 1 else None})
    return dict(out), len(flat)


def _variant_note(pin_names: dict) -> str:
    """Human note when a position takes different names across parts (F469/F479…)."""
    if len(pin_names) <= 1:
        return ""
    parts = ", ".join(f"{n} ({c})" for n, c in pin_names.items())
    return f"part-dependent: {parts}"


def _blob_at(conn: sqlite3.Connection, package: str) -> tuple:
    """position -> (uppercased signal+raw-name token blob, electrical-class set).

    The union of every CubeMX signal name and raw pin name a socket position
    takes across all supported parts. This is the 'could be X on any part'
    evidence the breakout map keys on."""
    blob: dict = defaultdict(set)
    ecs: dict = defaultdict(set)
    for pin, raw, ec, sig in conn.execute(
        "SELECT p.physical_pin_number, p.raw_pin_name, p.electrical_class, f.signal "
        "FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id "
        "LEFT JOIN pin_function f ON f.mcu_package_pin_id = p.id "
        "WHERE m.package_name = ?", (package,)):
        pos = int(pin)
        if raw:
            blob[pos].add(str(raw).upper())
        if sig:
            blob[pos].add(str(sig).upper())
        if ec:
            ecs[pos].add(str(ec))
    return blob, ecs


_PERIPH_SKIP = {"GPIO", ""}


def _peripherals_at(conn: sqlite3.Connection, package: str) -> dict:
    """position -> sorted distinct peripheral-instance roots available across the
    whole family (e.g. I2C1, SPI1, TIM2, USART2, ADC1, OTG, FMC, SDIO). Reference
    data for the extraction platform: what every socket pin can be wired to."""
    out: dict = defaultdict(set)
    for pin, sig in conn.execute(
        "SELECT DISTINCT p.physical_pin_number, f.signal FROM mcu_package_pin p "
        "JOIN mcu m ON m.id = p.mcu_id JOIN pin_function f ON f.mcu_package_pin_id = p.id "
        "WHERE m.package_name = ? AND f.signal IS NOT NULL AND f.signal <> ''", (package,)):
        root = re.split(r"[_-]", str(sig))[0].upper()
        if root not in _PERIPH_SKIP:
            out[int(pin)].add(root)
    return {k: sorted(v) for k, v in out.items()}


def _five_v(fam_gpios: set, peripherals) -> dict | None:
    """Per-position 5V-tolerance across the supported parts. A GPIO is structurally
    FT (5V-tolerant, digital mode) unless it is in its family's non-5V set — which
    differs by family, so a socket position can be 5V-safe under one part and not
    another. `tolerant` is the conservative answer (safe on ALL parts present)."""
    by_fam: dict = {}
    gpios: set = set()
    for fam, nm in fam_gpios:
        if fam not in FAMILY_NOT_5V or not _GPIO_NAME.match(str(nm)):
            continue
        gpios.add(nm)
        ft = nm not in FAMILY_NOT_5V[fam]
        by_fam[fam] = by_fam.get(fam, True) and ft   # AND when >1 GPIO/family here
    if not by_fam:
        return None   # non-GPIO position (power/ground/reset/boot)
    tolerant = all(by_fam.values())
    caveat = ""
    if gpios & _OSC_CAVEAT_PINS:
        caveat = "osc-mode"
    elif tolerant and any(str(p).startswith("ADC") for p in peripherals):
        caveat = "analog-mode"
    return {"tolerant": tolerant, "by_family": dict(sorted(by_fam.items())), "caveat": caveat}


def _breakout_map(tokens: set, canon_names, roles: set, ecs: set, switch_class: str) -> dict:
    """The frozen parent-board service net(s) a position must reach (breakout),
    orthogonal to the switch decision. Sources: Connector Contract Rev B + 5E."""
    text = " ".join(tokens)
    canon = set(canon_names)
    nets: list = []
    funcs: list = []

    def add(net: str, fn: str):
        if net not in nets:
            nets.append(net)
        funcs.append(fn)

    # Debug port — fixed silicon (PA13/PA14/PB3/PA15/PB4).
    if "SWDIO" in text or "JTMS" in text:
        add("SWDIO_PARENT", "SWD_DATA")
    if "SWCLK" in text or "JTCK" in text:
        add("SWCLK_PARENT", "SWD_CLK")
    if "TRACESWO" in text or "JTDO" in text or "-SWO" in text or "_SWO" in text:
        add("SWO_PARENT", "SWO_TDO")
    if "JTDI" in text:
        add("TDI_PARENT", "JTAG_TDI")
    if "JTRST" in text:                      # NJTRST contains JTRST
        add("NTRST_PARENT", "JTAG_NTRST")
    # Reset / boot — dedicated pins.
    if "reset" in ecs or "reset_nrst" in roles:
        add("SERVICE_NRST", "NRST")
    if "boot" in ecs or "boot" in roles:
        add("SERVICE_BOOT0", "BOOT0")
    # HSE oscillator, split IN/OUT (LSE OSC32_IN/OUT does not match).
    if "OSC_IN" in text:
        add("SERVICE_OSC_IN", "OSC_IN")
    if "OSC_OUT" in text:
        add("SERVICE_OSC_OUT", "OSC_OUT")
    # Boot UART — AN2606 universal USART1 (PA9 TX / PA10 RX); TX<->RX crossover
    # to the parent (parent UART_BOOT_TX drives the target RX).
    if "PA9" in canon and "USART1_TX" in text:
        add("UART_BOOT_RX", "UART_BOOT (target TX)")
    if "PA10" in canon and "USART1_RX" in text:
        add("UART_BOOT_TX", "UART_BOOT (target RX)")
    # USB-DFU — AN2606 universal PA12=DP / PA11=DM.
    if "PA12" in canon and "USB" in text:
        add("USB_DP_TGT", "USB_DFU_DP")
    if "PA11" in canon and "USB" in text:
        add("USB_DN_TGT", "USB_DFU_DM")

    # Parallel trace: TRACECLK / TRACECK (CubeMX variant) / TRACED0-3.
    # (TRACESWO is single-wire SWO, already handled as SWO_PARENT above.)
    trace = "TRACECLK" in text or "TRACECK" in text or "TRACED" in text
    cs20 = sorted({_CS20_PIN[n] for n in nets if n in _CS20_PIN})
    return {
        "service_nets": nets,
        "functions": funcs,
        "via": "adg714_source" if switch_class == db.SWITCH_MUST else "fixed_direct",
        "coresight20_pins": cs20,
        "trace": trace,
    }


def _extraction_access(positions: list) -> dict:
    """Package rollup of the breakout layer: the CoreSight-20 header resolved to
    target socket positions, the boot-UART / USB-DFU positions, and counts."""
    by_net: dict = defaultdict(list)
    for p in positions:
        for n in p["breakout"]["service_nets"]:
            by_net[n].append(p["position"])

    def first(net):
        return by_net[net][0] if by_net.get(net) else None

    cs20 = [{"hdr_pin": pin, "signal": sig, "net": net or "NC",
             "target_pos": (first(net) if net and net != "GND" else None)}
            for pin, sig, net in CORESIGHT20]
    return {
        "coresight20": cs20,
        "bootloader_uart": {"tx_pos": first("UART_BOOT_TX"), "rx_pos": first("UART_BOOT_RX")},
        "usb_dfu": {"dp_pos": first("USB_DP_TGT"), "dn_pos": first("USB_DN_TGT")},
        "service_breakout_count": sum(1 for p in positions if p["breakout"]["service_nets"]),
        "debug_positions": sorted(p["position"] for p in positions if p["tags"]["is_debug"]),
        "trace_positions": sorted(p["position"] for p in positions if p["breakout"]["trace"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Build the authority
# ─────────────────────────────────────────────────────────────────────────────
def card_materials(authority: dict) -> dict:
    """Per-package passive BOM for the NETDECK plug-in card, derived from the switch
    rollup + FAMILY_POWER. Worst-cased across the families the package covers (the
    card sockets one target at a time, but must physically carry the caps the
    neediest family wants). A materials guide, not a placed BOM."""
    r = authority["rollup"]
    power = authority["electrical"].get("power", {})
    vcap_fams = sorted(f for f, p in power.items() if p.get("vcap"))
    n_vdd = max((p.get("n_vdd") or 0) for p in power.values()) if power else 0
    items = [{
        "ref": "U_SW_*", "part": "Octal SPST switch (8 channels)", "qty": r["cells_as_built"],
        "role": "Switch fabric",
        "note": f"{r['channel_count']} one-hot channels from {r['must_switch_count']} "
                f"must-switch pins pack into {r['cells_min']} cells. Current-budget "
                f"paralleling may add cells on the physical card; see the vault build card.",
    }]
    if vcap_fams:
        items.append({
            "ref": "C_VCAP_1/2", "part": "2.2uF ceramic X7R (ESR<2ohm)", "qty": 2,
            "role": "Regulator VCAP",
            "note": f"required for {', '.join(vcap_fams)} sockets (VCAP_1/VCAP_2); "
                    f"F0/F1/F3 have no VCAP pin (DNP/harmless)",
        })
    if n_vdd:
        items.append({
            "ref": "C_DEC_*", "part": "100nF ceramic X7R", "qty": n_vdd,
            "role": "VDD decoupling", "note": f"one per VDD/VSS pair (worst-case {n_vdd} on LQFP100)",
        })
    items.append({"ref": "C_BULK", "part": "4.7uF ceramic", "qty": 1, "role": "Bulk",
                  "note": "One bulk cap per package."})
    items.append({"ref": "C_VDDA/VREF", "part": "1uF + 10-100nF ceramic", "qty": 4,
                  "role": "VDDA / VREF+ decoupling",
                  "note": "1uF // 10nF on VDDA; + VREF+ pair where VREF+ is a separate pin"})
    return {
        "package": authority["package"],
        "adg714_cells": r["cells_as_built"],
        "vcap_required_families": vcap_fams,
        "decoupling_100nf_count": n_vdd or None,
        "items": items,
        "note": "Worst-cased across the families this package covers; the card sockets one "
                "target at a time. VCAP caps populate for F2/F4/F7 sockets.",
    }


def lint_card(authority: dict, claims: dict) -> list:
    """Drift-gate: check a Build Card's asserted numbers against the authority.
    `claims` is any subset of: must_switch_count, adg714_cells, adg714_cells_min,
    osc_optional_count, fixed_count, positions_total, and the debug pin positions
    swdio_pos / swclk_pos / swo_pos / tdi_pos / ntrst_pos / nrst_pos. Returns a
    finding per claimed field: {field, claimed, actual, ok, detail}. Catches the
    SWCLK-pin-79-vs-76 / ADG714-6-vs-8 drift this generator exists to kill."""
    r = authority["rollup"]
    ea = authority.get("extraction_access", {})
    actuals = {
        "must_switch_count": (r["must_switch_count"], ""),
        "adg714_cells": (r["cells_as_built"], "as-built incl. osc"),
        "adg714_cells_min": (r["cells_min"], "must-switch only"),
        "osc_optional_count": (r["osc_optional_count"], ""),
        "fixed_count": (r["fixed_count"], ""),
        "positions_total": (r["positions_total"], ""),
    }
    net_key = {"SWDIO_PARENT": "swdio_pos", "SWCLK_PARENT": "swclk_pos", "SWO_PARENT": "swo_pos",
               "TDI_PARENT": "tdi_pos", "NTRST_PARENT": "ntrst_pos", "SERVICE_NRST": "nrst_pos"}
    for c in ea.get("coresight20", []):
        k = net_key.get(c.get("net"))
        if k and c.get("target_pos"):
            actuals[k] = (c["target_pos"], "from CoreSight-20 map")
    # Per-pin routing claims — the aggregate counts above can all match while an
    # individual pin routes to the wrong rail (proven: a stale pre-rev-2 DB passed
    # the count checks with VREF- still on VREF_TGT). Claim forms:
    #   pin_dest_<N>:  the single delivered net of a fixed/service/lane pin
    #   pin_rails_<N>: the sorted '+'-joined channel rail set of a switched pin
    pos_map = {p["position"]: p for p in authority["positions"]}

    def _pin_actual(pos: int, want_rails: bool) -> str:
        p = pos_map.get(pos)
        if p is None:
            return ""
        chans = p["assignment"].get("channels") or []
        if chans:
            rails = "+".join(sorted({c["destination"] for c in chans}))
            if want_rails:
                return rails
            single = p["assignment"].get("destination") or p["assignment"].get("net")
            return single or rails
        return (p["assignment"].get("destination") or p["assignment"].get("net") or "")

    findings = []
    for field, claimed in claims.items():
        m = re.fullmatch(r"pin_(dest|rails)_(\d+)", field)
        if m:
            pos = int(m.group(2))
            if pos not in pos_map:
                findings.append({"field": field, "claimed": claimed, "actual": None,
                                 "ok": False, "detail": "no such position"})
                continue
            actual = _pin_actual(pos, want_rails=(m.group(1) == "rails"))
            findings.append({"field": field, "claimed": str(claimed), "actual": actual,
                             "ok": str(claimed) == actual, "detail": "per-pin routing"})
            continue
        if field not in actuals:
            findings.append({"field": field, "claimed": claimed, "actual": None,
                             "ok": None, "detail": "unknown field (not checked)"})
            continue
        actual, detail = actuals[field]
        findings.append({"field": field, "claimed": claimed, "actual": actual,
                         "ok": claimed == actual, "detail": detail})
    return findings


# ── self-contained reporting (analysis only; the physical ADG714 build is the
#    vault's authority — this layer just makes the tool legible without it) ──────

ADG714_SYMBOL = "ADG714BRUZ-REEL"        # libs/MySymbols.kicad_sym
ADG714_FOOTPRINT = "RU_24_ADI"           # TSSOP-24
# Switch n of an ADG714 uses terminal pair Sn / Dn (verbatim from the symbol:
# S1/D1 pins 5/6, S2/D2 7/8, S3/D3 9/10, S4/D4 11/12, D5/S5 13/14, D6/S6 15/16,
# D7/S7 17/18, D8/S8 19/20). Control/power: SCLK VDD DIN GND VSS DOUT RESET* SYNC*.
ADG714_SWITCH_PINS = {n: (f"S{n}", f"D{n}") for n in range(1, 9)}

# ── Card wiring facts, verbatim from the vault (Connector Contract Rev B; Cards 7A/
#    7B/7C; component pages). The switch analysis is derived from CubeMX; these map it
#    onto the real hardware, terminal by terminal. Source faces the socket, drain the rail.
ZIF_SOCKET = {"LQFP64": "Yamaichi IC51-0644-807", "LQFP100": "Yamaichi IC51-1004-809"}
CONNECTOR = {"parent": "Samtec QSH-060-01-L-D-A", "card": "Samtec QTH-060-03-L-D-A",
             "contacts": 240, "pitch_mm": 0.5, "stack_mm": 11.03, "amp_per_contact": 2}

# Reference designators, verbatim from the build cards (7B / 7C headings).
SOCKET_REFDES = {"LQFP64": "J_SOCKET64_1", "LQFP100": "XU_TGT100_1"}
EDGE_REFDES = {"LQFP64": "J_EDGE64_1", "LQFP100": "J_EDGE_L100_1"}
CELL_REFDES_FMT = {"LQFP64": "U_SW_64_{n}", "LQFP100": "U_SW_L100_{n}"}
SERIES_R_REFDES = {"LQFP100": "R_IO_LANE"}   # 33 R 0402, one per IO-capable lane (7C);
                                             # 7B routes its non-switched pins direct.

# Lane naming policy, per card:
#   7B (LQFP64):  numbered lanes exist ONLY for switched pins, sequential in
#                 ascending socket order (pin 1 -> CARD_LANE_001, pin 13 -> _002 ...).
#   7C (LQFP100): EVERY socket pin has a lane numbered by its socket pin
#                 (CARD_LANE_001..100), riding a 33 R series resistor.
LANE_POLICY = {"LQFP64": "sequential", "LQFP100": "by_pin"}


def lane_contact(lane_num: int) -> str:
    """The parent-receptacle contact a numbered CARD_LANE lands on. The row split
    is verbatim from the frozen Connector Contract Rev B (lanes 001..060 on the
    left connector's even row 2..120; lanes 061..120 on the right connector's odd
    row 1..119); the in-row order is ascending, so lane N maps to LA even 2N or
    RA odd 2(N-60)-1."""
    if 1 <= lane_num <= 60:
        return f"LA-{2 * lane_num}"
    if 61 <= lane_num <= 120:
        return f"RA-{2 * (lane_num - 60) - 1}"
    return ""


def cell_refdes(package: str, cell: int) -> str:
    return CELL_REFDES_FMT.get(package, "U_SW_{n}").format(n=cell)
# ADG714 physical pin number for each S/D terminal (S = source, faces socket; D = drain, faces rail).
ADG714_TERMINAL_PIN = {"S1": 5, "D1": 6, "S2": 7, "D2": 8, "S3": 9, "D3": 10, "S4": 11,
                       "D4": 12, "D5": 13, "S5": 14, "D6": 15, "S6": 16, "D7": 17, "S7": 18,
                       "D8": 19, "S8": 20}
# Rail net -> QSH/QTH connector contact(s), as "<side>-<contact>" (LA = left connector,
# RA = right). [] = not a connector contact (GND solid plane / local VCAP cap).
RAIL_CONTACT = {
    "VBAT_TGT": ["LA-33"], "VDDA_TGT": ["RA-20"], "VREF_TGT": ["RA-22"], "VSSA_TGT": ["RA-24"],
    "VTARGET": ["RA-16", "RA-18"], "SERVICE_BOOT0": ["RA-14"],
    "SERVICE_NRST": ["LA-7"], "SERVICE_OSC_IN": ["RA-10"], "SERVICE_OSC_OUT": ["RA-12"],
    "GND": [], "VCAP_NODE": [],
}
# Shared control/power bus (frozen SPI2 harness): signal -> (ADG714 pin, connector contact, controller pin).
ADG714_BUS = [
    ("SCLK", 1, "LA-9", "PB13"), ("DIN", 3, "LA-11", "PB15"), ("DOUT", 22, "LA-13", "PB14"),
    ("SYNC_N", 24, "LA-15", "PB12"), ("RESET_N", 23, "LA-17", "PB9"),
    ("VDD", 2, "LA-31", "+3V3"), ("GND", 4, None, "plane"), ("VSS", 21, None, "plane"),
]


# Service / debug nets -> connector contact (Connector Contract Rev B).
SERVICE_CONTACT = {
    "SWDIO_PARENT": "LA-1", "SWCLK_PARENT": "LA-3", "SWO_PARENT": "LA-5",
    "SERVICE_NRST": "LA-7", "TDI_PARENT": "LA-35", "NTRST_PARENT": "LA-37",
    "SERVICE_BOOT0": "RA-14", "UART_BOOT_TX": "RA-6", "UART_BOOT_RX": "RA-8",
    "USB_DP_TGT": "RA-2", "USB_DN_TGT": "RA-4",
    "SERVICE_OSC_IN": "RA-10", "SERVICE_OSC_OUT": "RA-12",
}
# net -> category (drives the colour and grouping in the connections view)
_NET_CATEGORY = {
    "VTARGET": "power", "VBAT_TGT": "power",
    "VDDA_TGT": "analog", "VREF_TGT": "analog",
    "GND": "ground", "VSSA_TGT": "ground", "VCAP_NODE": "core",
    "SWDIO_PARENT": "service", "SWCLK_PARENT": "service", "SWO_PARENT": "service",
    "SERVICE_NRST": "service", "TDI_PARENT": "service", "NTRST_PARENT": "service",
    "SERVICE_BOOT0": "service", "UART_BOOT_TX": "service", "UART_BOOT_RX": "service",
    "USB_DP_TGT": "service", "USB_DN_TGT": "service",
    "SERVICE_OSC_IN": "service", "SERVICE_OSC_OUT": "service",
}


def _dest_contact(dest: str) -> str:
    if dest in RAIL_CONTACT:
        cs = RAIL_CONTACT[dest]
        return cs[0] if cs else ("GND Plane" if dest == "GND" else "Local Cap")
    if dest in SERVICE_CONTACT:
        return SERVICE_CONTACT[dest]
    return "Lane Row"


def socket_connections(authority: dict) -> list:
    """Every socket pin's connection to the parent, not just the switched ones. Per pin:
    the middle component (switch for switched pins, a 33-ohm series resistor for GPIO
    lanes, or a direct link for fixed power and debug/service), the destination net and
    its category, and the connector contact."""
    pkg = authority["package"]
    policy = LANE_POLICY.get(pkg, "sequential")
    out = []
    for p in sorted(authority["positions"], key=lambda p: p["position"]):
        pin = p["position"]
        name = list(p["pin_names"])[0] if p["pin_names"] else ""
        service = [n for n in p.get("breakout", {}).get("service_nets", []) if n]
        if p["assignment"].get("channels"):
            dest, kind = p["assignment"]["adg714"]["destination"], "switch"
            contact = _dest_contact(dest)
        elif service:
            dest, kind = service[0], "direct"
            contact = _dest_contact(dest)
        else:
            net = p["assignment"].get("net") or p["assignment"].get("destination") or ""
            if net in _NET_CATEGORY:
                dest, kind = net, "direct"          # fixed power / ground rail
                contact = _dest_contact(dest)
            elif policy == "by_pin":
                # 7C: every IO-capable pin owns its pin-numbered lane, riding a
                # 33 R series resistor to the frozen Connector Contract lane row.
                dest, kind = f"CARD_LANE_{pin:03d}", "resistor"
                contact = lane_contact(pin)
            else:
                # 7B: non-switched pins route DIRECT (no series resistor); the
                # single-role lane assignment is not numbered on this card.
                dest, kind = "CARD_LANE", "direct"
                contact = "Lane Row"
        cat = _NET_CATEGORY.get(dest, "lane")
        out.append({"pin": pin, "name": name, "kind": kind, "dest": dest,
                    "category": cat, "contact": contact,
                    "socket_refdes": SOCKET_REFDES.get(pkg, "J_SOCKET")})
    return out


def card_wiring(authority: dict) -> dict:
    """The switch fabric wired terminal by terminal, mapping the tool's derived channels
    onto the vault's Connector Contract. Per channel: the ADG714 S/D terminal pins, the
    socket pin its Source connects to (via the IC51 ZIF socket), and the rail its Drain
    connects to (via the QSH/QTH connector contact). Daisy chain: DIN into cell 1, each
    cell's DOUT into the next cell's DIN, last cell's DOUT back to the controller."""
    pkg = authority["package"]
    zif = ZIF_SOCKET.get(pkg, "IC51 ZIF socket")
    pinname = {p["position"]: (list(p["pin_names"])[0] if p["pin_names"] else "")
               for p in authority["positions"]}
    # Lanes follow the card's own policy (LANE_POLICY):
    #   sequential (7B): numbered lanes ONLY for switched pins, ascending socket
    #     order (pin 1 -> CARD_LANE_001, pin 13 -> _002, ...); other pins unlaned.
    #   by_pin (7C): every socket pin owns CARD_LANE_{pin:03d} on the frozen
    #     Connector Contract lane rows.
    # A pin's branches share its socket trace, so they share one lane.
    policy = LANE_POLICY.get(pkg, "sequential")
    if policy == "by_pin":
        lane_of = {p["position"]: f"CARD_LANE_{p['position']:03d}"
                   for p in authority["positions"]}
        lane_num_of = {p["position"]: p["position"] for p in authority["positions"]}
    else:
        must = [p["position"] for p in sorted(authority["positions"], key=lambda p: p["position"])
                if p["switch_class"] == db.SWITCH_MUST]
        osc_wired = [p["position"] for p in sorted(authority["positions"], key=lambda p: p["position"])
                     if p["switch_class"] == db.SWITCH_OSC_OPTIONAL
                     and p["assignment"].get("channels")]
        lane_of = {pin: f"CARD_LANE_{i:03d}" for i, pin in enumerate(must + osc_wired, start=1)}
        lane_num_of = {pin: i for i, pin in enumerate(must + osc_wired, start=1)}
    channels, spares, groups = [], [], defaultdict(list)
    cells = adg714_cell_map(authority)
    for cell in cells:
        for sw in cell["switches"]:
            if sw["spare"]:
                spares.append({"cell": cell["cell"], "channel": sw["channel"],
                               "s_pin": sw["s_pin"], "d_pin": sw["d_pin"], "spare": True})
                continue
            rail = sw["destination"]
            contacts = RAIL_CONTACT.get(rail, [])
            name = pinname.get(sw["position"], "")
            if contacts:
                d_to = f"{rail} via {CONNECTOR['card']} contact {'/'.join(map(str, contacts))}"
            elif rail == "GND":
                d_to = "GND (solid ground plane)"
            else:
                d_to = f"{rail} (local 2.2uF cap at the socket)"
            ln = lane_num_of.get(sw["position"])
            channels.append({
                "cell": cell["cell"], "cell_refdes": cell_refdes(pkg, cell["cell"]),
                "channel": sw["channel"],
                "s_pin": sw["s_pin"], "s_pin_num": ADG714_TERMINAL_PIN.get(sw["s_pin"]),
                "d_pin": sw["d_pin"], "d_pin_num": ADG714_TERMINAL_PIN.get(sw["d_pin"]),
                "socket_pin": sw["position"], "socket_name": name,
                "socket_refdes": SOCKET_REFDES.get(pkg, "J_SOCKET"),
                "rail": rail, "connector_contacts": contacts,
                "card_lane": lane_of.get(sw["position"], "CARD_LANE"),
                "lane_contact": lane_contact(ln) if ln else "",
                "s_connects_to": f"socket pin {sw['position']} ({name}) via {zif}",
                "d_connects_to": d_to,
            })
            groups[sw["position"]].append((cell["cell"], sw["channel"]))
    # Mutually-exclusive branch groups: every channel set sharing one socket pin is
    # one-hot — firmware must close at most one (the open state leaves the pin on
    # its lane). Only multi-branch pins are listed.
    exclusive = [{"socket_pin": pin, "channels": [{"cell": c, "channel": ch} for c, ch in chs]}
                 for pin, chs in sorted(groups.items()) if len(chs) > 1]
    daisy = {"head_din_contact": "LA-11", "tail_dout_contact": "LA-13",
             "order": [c["cell"] for c in cells],
             "note": "DIN into cell 1; each cell DOUT into the next cell DIN; last cell DOUT "
                     "back to the controller (LA-13). SCLK/SYNC_N/RESET_N broadcast to all cells."}
    return {"package": pkg, "zif_socket": zif, "connector": CONNECTOR,
            "socket_refdes": SOCKET_REFDES.get(pkg, "J_SOCKET"),
            "edge_refdes": EDGE_REFDES.get(pkg, "J_EDGE"),
            "lane_policy": policy,
            "series_r_refdes": SERIES_R_REFDES.get(pkg, ""),
            "bus": [{"signal": s, "adg714_pin": p, "connector_contact": c, "controller": m}
                    for s, p, c, m in ADG714_BUS],
            "cells": len(cells), "channels": channels, "spare_channels": spares,
            "exclusive_groups": exclusive, "daisy_chain": daisy}


def to_switchmap_json(authority: dict) -> str:
    """Machine-readable switch map + full terminal wiring, for firmware/tooling."""
    return json.dumps(card_wiring(authority), indent=2)


def to_switchmap_c(authority: dict) -> str:
    """A C header the firmware can include: per-channel {cell, channel, socket_pin,
    rail} plus the daisy-chain cell order. Rails become an enum."""
    w = card_wiring(authority)
    pkg = w["package"]
    # A zero-channel package would emit an empty `enum {}` and empty array
    # initializers `{}` — both invalid ISO C. Guard every list that becomes a C
    # aggregate with a placeholder so the header always compiles.
    rails = sorted({c["rail"] for c in w["channels"]}) or ["NONE"]
    L = [f"/* NETDECK switch map for {pkg}. Generated from the pinout data; wiring per the",
         " * vault Connector Contract (Cards 7A/7B/7C). Do not edit by hand.",
         f" * {_stamp_line(authority)} */",
         f"#ifndef NETDECK_SWITCHMAP_{pkg}_H", f"#define NETDECK_SWITCHMAP_{pkg}_H", "",
         "typedef enum {"]
    L += [f"    RAIL_{r}," for r in rails]
    L += ["} netdeck_rail_t;", "",
          "typedef struct { unsigned char cell, channel, socket_pin; netdeck_rail_t rail; } netdeck_channel_t;",
          "",
          f"static const netdeck_channel_t NETDECK_{pkg}_CHANNELS[] = {{"]
    for c in w["channels"]:
        L.append(f"    {{ {c['cell']}, {c['channel']}, {c['socket_pin']}, RAIL_{c['rail']} }},"
                 f"  /* {c['s_pin']}<-pin{c['socket_pin']} {c['socket_name']}, {c['d_pin']}->{c['rail']} */")
    if not w["channels"]:
        L.append("    { 0, 0, 0, RAIL_NONE },  /* placeholder: package has no switch channels */")
    L.append("};")
    cell_order = w["daisy_chain"]["order"]
    L.append(f"static const unsigned char NETDECK_{pkg}_CELL_ORDER[] = "
             f"{{ {', '.join(str(n) for n in cell_order) if cell_order else '0'} }};")
    L += ["",
          f"#define NETDECK_{pkg}_CELLS {w['cells']}",
          f"#define NETDECK_{pkg}_CHANNELS_USED {len(w['channels'])}",
          f"#define NETDECK_{pkg}_CHANNELS_SPARE {len(w.get('spare_channels', []))}"]
    if w.get("exclusive_groups"):
        L += ["",
              "/* One-hot rule: channels sharing a socket_pin are mutually exclusive",
              " * branches of that pin — close at most ONE per socket_pin; all open",
              " * leaves the pin on its default lane. Multi-branch pins:",
              " *   " + ", ".join(
                  f"pin {g['socket_pin']} ({len(g['channels'])} branches)"
                  for g in w["exclusive_groups"]),
              " */"]
    L += ["", f"#endif /* NETDECK_SWITCHMAP_{pkg}_H */", ""]
    return "\n".join(L)


def to_wiring_md(authority: dict) -> str:
    """Human wiring table: every channel's Source/Drain endpoints and connector contact,
    the way the vault documents it."""
    w = card_wiring(authority)
    L = [f"# {w['package']} switch-cell wiring", "",
         _stamp_line(authority), "",
         f"Socket: {w['zif_socket']}. Connector: {w['connector']['card']} into "
         f"{w['connector']['parent']} ({w['connector']['contacts']} contacts).", "",
         "## Control bus (shared / daisy-chained)", "",
         "| Signal | ADG714 pin | Connector contact | Controller |",
         "|--------|-----------|-------------------|-----------|"]
    for b in w["bus"]:
        L.append(f"| {b['signal']} | {b['adg714_pin']} | "
                 f"{b['connector_contact'] if b['connector_contact'] is not None else '(plane)'} | {b['controller']} |")
    L += ["", f"Daisy chain: {w['daisy_chain']['note']}", "",
          "## Per-channel terminal wiring", "",
          "| Cell | Ch | S pin | Source connects to | D pin | Drain connects to | Lane |",
          "|------|----|-------|--------------------|-------|-------------------|------|"]
    for c in w["channels"]:
        L.append(f"| {c['cell']} | {c['channel']} | {c['s_pin']} (pin {c['s_pin_num']}) | "
                 f"{c['s_connects_to']} | {c['d_pin']} (pin {c['d_pin_num']}) | "
                 f"{c['d_connects_to']} | {c['card_lane']} |")
    return "\n".join(L) + "\n"


def category_lists(authority: dict) -> dict:
    """Explicit socket-pin-number lists per category, so the analysis reads without
    cross-referencing the vault. Every list is sorted ascending by position."""
    pos = authority["positions"]

    def nums(pred):
        return sorted(p["position"] for p in pos if pred(p))

    def fv(p):
        return p.get("five_v")

    return {
        "must_switch": nums(lambda p: p["switch_class"] == db.SWITCH_MUST),
        "osc_optional": nums(lambda p: p["switch_class"] == db.SWITCH_OSC_OPTIONAL),
        "fixed": nums(lambda p: p["switch_class"] == db.SWITCH_NONE),
        "breakout": nums(lambda p: p.get("breakout", {}).get("service_nets")),
        "trace": nums(lambda p: p.get("breakout", {}).get("trace")),
        "debug": nums(lambda p: p["tags"].get("is_debug")),
        "boot": nums(lambda p: p["tags"].get("is_boot")),
        "five_v_all_parts": nums(lambda p: fv(p) and fv(p)["tolerant"]),
        "five_v_never": nums(lambda p: fv(p) and not any(fv(p)["by_family"].values())),
    }


def switch_rationale(position: dict) -> str:
    """One-line reason a position must switch ('' for fixed pins). Human-readable,
    derived from the debug/boot tags and the competing nets it would otherwise tie
    together (conflict_nets) — auditable without the vault."""
    sc = position["switch_class"]
    if sc == db.SWITCH_NONE:
        return ""
    if sc == db.SWITCH_OSC_OPTIONAL:
        return ("This position is the HSE oscillator on some parts and a GPIO on others. "
                "Switch it only if an external crystal is fitted.")
    roles = ", ".join(str(k) for k in position.get("role_set", {}).keys())
    conflicts = [c for c in (position.get("conflict_nets") or []) if c]
    if conflicts:
        return (f"This position takes the roles {roles} across the supported parts, so the "
                f"switch routes it to {' or '.join(conflicts)} depending on the part.")
    return f"This position takes the roles {roles}, so it must switch to stay isolated."


def adg714_cell_map(authority: dict) -> list:
    """The must-switch fabric as ADG714 instances, using the real symbol pin names.
    One entry per cell (chip), each carrying all 8 switches; unused channels are
    marked spare. One channel per non-IO rail, so a dual-rail pin appears on two
    channels. Which S/D terminal takes the MCU pin versus the destination net is the
    vault's wiring; this reports the switch a socket position lands on."""
    cells: dict = defaultdict(dict)
    for p in authority["positions"]:
        for chd in p["assignment"].get("channels", []):
            cells[chd["cell"]][chd["channel"]] = (p, chd)
    out = []
    for cell in sorted(cells):
        chans = cells[cell]
        switches = []
        for ch in range(1, 9):
            s_pin, d_pin = ADG714_SWITCH_PINS[ch]
            entry = chans.get(ch)
            if entry is None:
                switches.append({"channel": ch, "s_pin": s_pin, "d_pin": d_pin,
                                 "position": None, "pin_name": "", "destination": None,
                                 "spare": True})
                continue
            p, chd = entry
            names = list(p["pin_names"].keys())
            switches.append({
                "channel": ch, "s_pin": s_pin, "d_pin": d_pin,
                "position": p["position"], "pin_name": names[0] if names else "",
                "destination": chd["destination"],
                "spare": False,
            })
        out.append({"cell": cell, "symbol": ADG714_SYMBOL, "footprint": ADG714_FOOTPRINT,
                    "switches": switches})
    return out


_CSV_COLUMNS = ["position", "side", "pin_names", "roles", "switch_class", "why",
                "adg714_cell", "adg714_switch", "adg714_s", "adg714_d", "destination",
                "peripherals", "breakout_nets", "trace", "five_v", "bootloader",
                "vdd_min_v", "vdd_max_v"]


def to_csv(authority: dict) -> str:
    """One row per socket pin, every self-contained column. Opens in any spreadsheet;
    no vault required."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(_CSV_COLUMNS)
    vdd = (authority.get("electrical") or {}).get("vdd_range_v") or [None, None]
    for p in sorted(authority["positions"], key=lambda p: p["position"]):
        a = p["assignment"]
        adg = a.get("adg714") or {}
        ch = adg.get("channel")
        s_pin, d_pin = ADG714_SWITCH_PINS.get(ch, ("", "")) if ch else ("", "")
        fv = p.get("five_v")
        w.writerow([
            p["position"], p.get("side", ""),
            " / ".join(p["pin_names"].keys()),
            " ".join(str(k) for k in p["role_set"].keys()),
            p["switch_class"], switch_rationale(p),
            adg.get("cell", ""), ch or "", s_pin, d_pin,
            a.get("destination") or a.get("net") or "",
            " ".join(p.get("peripherals", [])),
            " ".join(p.get("breakout", {}).get("service_nets", [])),
            "yes" if p.get("breakout", {}).get("trace") else "",
            ("yes" if fv["tolerant"] else "no") if fv else "",
            " ".join(p["tags"].get("bootloader_periph", [])),
            vdd[0] if vdd else "", vdd[1] if vdd else "",
        ])
    return buf.getvalue()


def _md_cell(v) -> str:
    return str(v).replace("|", r"\|")


def to_markdown(authority: dict) -> str:
    """A standalone, self-contained Markdown report for someone with neither the
    app nor the vault: summary, explicit pin-number lists, the ADG714 switch-fabric
    map (real symbol pin names), electrical block, and a full per-pin table."""
    a = authority
    r = a["rollup"]
    man = a["manifest"]
    el = a.get("electrical") or {}
    ea = a.get("extraction_access", {})
    cats = category_lists(a)
    fvp = el.get("five_v_positions", {})

    def joln(nums):
        return ", ".join(str(n) for n in nums) if nums else "—"

    L = [f"# STM32 {a['package']} — Pinout Authority", "",
         _stamp_line(a), ""]
    L.append(f"{man['part_count']} parts · families {', '.join(man['supported_families'])} · "
             f"source: {man['source']}")
    L += ["", "## Summary", ""]
    L.append(f"- Positions: **{r['positions_total']}**")
    L.append(f"- Must-switch: **{r['must_switch_count']}** · Osc-optional: "
             f"**{r['osc_optional_count']}** · Fixed: **{r['fixed_count']}**")
    L.append(f"- Breakout (service nets): **{ea.get('service_breakout_count', 0)}** "
             f"(debug {len(ea.get('debug_positions', []))}, trace {len(ea.get('trace_positions', []))})")
    if fvp:
        L.append(f"- 5V-tolerant: {fvp.get('tolerant_all_parts', 0)} all-parts / "
                 f"{fvp.get('family_dependent', 0)} part-dependent / "
                 f"{fvp.get('not_tolerant_any_part', 0)} never")

    L += ["", "## Pin lists (by socket number)", ""]
    L.append(f"- **Must-switch ({len(cats['must_switch'])}):** {joln(cats['must_switch'])}")
    L.append(f"- **Osc-optional ({len(cats['osc_optional'])}):** {joln(cats['osc_optional'])}")
    L.append(f"- **Breakout ({len(cats['breakout'])}):** {joln(cats['breakout'])}")
    L.append(f"- **Debug ({len(cats['debug'])}):** {joln(cats['debug'])}")
    L.append(f"- **5V-tolerant, all parts ({len(cats['five_v_all_parts'])}):** {joln(cats['five_v_all_parts'])}")
    L.append(f"- **Never 5V-tolerant ({len(cats['five_v_never'])}):** {joln(cats['five_v_never'])}")

    L += ["", f"## Switch fabric — ADG714 ({ADG714_SYMBOL} / {ADG714_FOOTPRINT})", ""]
    for cell in adg714_cell_map(a):
        L += [f"### Cell {cell['cell']}", "",
              "| Sw | Terminals | Pin | Name | Destination |",
              "|----|-----------|-----|------|-------------|"]
        for sw in cell["switches"]:
            if sw["spare"]:
                L.append(f"| SW{sw['channel']} | {sw['s_pin']}/{sw['d_pin']} | — | — | *(spare)* |")
            else:
                L.append(f"| SW{sw['channel']} | {sw['s_pin']}/{sw['d_pin']} | {sw['position']} "
                         f"| {_md_cell(sw['pin_name'])} | {_md_cell(sw['destination'] or '—')} |")
        L.append("")

    io_ma = el.get("max_io_current_ma")
    inj = el.get("injection_current_ma")
    vdd = el.get("vdd_range_v")
    vdda = el.get("vdda_range_v")
    L += ["## Electrical", ""]
    if vdd:
        L.append(f"- VDD: {vdd[0]}–{vdd[1]} V" + (f" · VDDA: {vdda[0]}–{vdda[1]} V" if vdda else ""))
    if io_ma:
        L.append(f"- Per-pin I/O: ±{io_ma} mA (injection ±{inj} mA)")
    if el.get("vcap_required") is not None:
        L.append(f"- VCAP external caps required: {'yes' if el['vcap_required'] else 'no'}")

    L += ["", "## Pins", "",
          "| Pin | Side | Name(s) | Roles | Switch | Why | ADG714 | Destination | "
          "Peripherals | Breakout | 5V | Bootloader | VDD |",
          "|-----|------|---------|-------|--------|-----|--------|-------------|"
          "-------------|----------|----|-----------|-----|"]
    for p in sorted(a["positions"], key=lambda p: p["position"]):
        asg = p["assignment"]
        adg = asg.get("adg714")
        adg_t = f"cell {adg['cell']} · SW{adg['channel']} ({ADG714_SWITCH_PINS[adg['channel']][0]}/"\
                f"{ADG714_SWITCH_PINS[adg['channel']][1]})" if adg else "—"
        fv = p.get("five_v")
        five = ("Y" if fv["tolerant"] else "n") if fv else ""
        bk = p.get("breakout", {})
        bnets = ", ".join(bk.get("service_nets", [])) + (" ·TRACE" if bk.get("trace") else "")
        L.append("| " + " | ".join(_md_cell(x) for x in [
            p["position"], p.get("side", ""),
            " / ".join(p["pin_names"].keys()),
            " ".join(str(k) for k in p["role_set"].keys()),
            _SWITCH_MD.get(p["switch_class"], p["switch_class"]),
            switch_rationale(p) or "—", adg_t,
            asg.get("destination") or asg.get("net") or "—",
            " ".join(p.get("peripherals", [])) or "—",
            bnets or "—", five,
            " ".join(p["tags"].get("bootloader_periph", [])) or "—",
            f"{vdd[0]}–{vdd[1]}" if vdd else "—",
        ]) + " |")
    return "\n".join(L) + "\n"


_SWITCH_MD = {db.SWITCH_MUST: "must", db.SWITCH_OSC_OPTIONAL: "osc", db.SWITCH_NONE: "fixed"}


def build(conn: sqlite3.Connection, package: str) -> dict:
    # Stale-database guard: classification/routing rules run at DB build time, so a
    # DB stamped by an older classifier would silently emit outdated routing (the
    # exact drift class the gate exists to kill). Refuse it with a clear remedy.
    rev = db.classifier_rev(conn)
    if rev != db.CLASSIFIER_REV:
        raise ValueError(
            f"This database was built by classifier revision {rev or 'pre-1'}; the "
            f"current rules are revision {db.CLASSIFIER_REV}. Rebuild the database "
            f"(Build Database) so the routing reflects the current classification.")
    rep = db.package_report(conn, package)
    fam_names = _families_at(conn, package)
    blob, ecs = _blob_at(conn, package)
    vssa_pins = frozenset(pin for pin, toks in blob.items() if "VSSA" in " ".join(toks))
    policy = CHANNEL_POLICY.get(package, "dominant")
    adg_ch, channel_count = _adg714_channels(rep, policy, vssa_pins)
    periph = _peripherals_at(conn, package)

    names: dict = defaultdict(lambda: defaultdict(int))
    for pin, nm, n in conn.execute(
        "SELECT p.physical_pin_number, p.canonical_pin_name, COUNT(DISTINCT p.mcu_id) "
        "FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id WHERE m.package_name = ? "
        "GROUP BY p.physical_pin_number, p.canonical_pin_name", (package,)):
        names[int(pin)][str(nm)] = int(n)

    roles_at: dict = defaultdict(set)
    for pin, rn in conn.execute(
        "SELECT p.physical_pin_number, pr.role_name FROM pin_role pr "
        "JOIN mcu_package_pin p ON p.id = pr.mcu_package_pin_id JOIN mcu m ON m.id = p.mcu_id "
        "WHERE m.package_name = ? GROUP BY p.physical_pin_number, pr.role_name", (package,)):
        roles_at[int(pin)].add(str(rn))

    positions = []
    for d in sorted(rep.decisions, key=lambda d: d.pin):
        pin_names = dict(sorted(names.get(d.pin, {}).items(), key=lambda kv: -kv[1]))
        if d.switch_class == db.SWITCH_MUST:
            chans = adg_ch.get(d.pin, [])
            assignment = {"kind": "switched", "channels": chans,
                          "adg714": chans[0] if chans else None,
                          "destination": d.primary_target_net}
        elif d.switch_class == db.SWITCH_OSC_OPTIONAL:
            chans = adg_ch.get(d.pin, [])       # wired on per_role cards (7C), none on 7B
            assignment = {"kind": "osc_optional", "channels": chans,
                          "adg714": chans[0] if chans else None,
                          "destination": d.primary_target_net}
        else:
            only = d.non_io_identities[0] if d.non_io_identities else db.ID_IO
            assignment = {"kind": "direct", "net": NET_DICT.get(only, NET_DICT[db.ID_IO])}

        tokens = blob.get(d.pin, set())
        breakout = _breakout_map(tokens, pin_names.keys(), roles_at.get(d.pin, set()),
                                 ecs.get(d.pin, set()), d.switch_class)
        # Analog ground (VSSA) routes to its own frozen rail contact VSSA_TGT,
        # not GND (Connector Contract Rev B contact 24). Destination-label only:
        # the switch identity is unchanged, so the switch counts cannot move.
        if "VSSA" in " ".join(tokens):
            for key in ("net", "destination"):
                if assignment.get(key) == "GND":
                    assignment[key] = "VSSA_TGT"
        # The representative ADG714 channel MUST be the one that actually delivers the
        # labelled destination. Blindly using chans[0] made CSV/MD tell the operator to
        # close a switch tying the socket pin to the WRONG rail (e.g. analog ground
        # instead of VTARGET) — an electrical hazard. Match on the final destination.
        if assignment.get("kind") in ("switched", "osc_optional"):
            _chans = assignment.get("channels") or []
            _dest = assignment.get("destination")
            assignment["adg714"] = next(
                (c for c in _chans if c.get("destination") == _dest),
                _chans[0] if _chans else None)
        tags = _position_tags(roles_at.get(d.pin, set()), fam_names.get(d.pin, set()))
        tags["is_trace"] = breakout["trace"]
        text = " ".join(tokens)
        tags["is_wakeup"] = "WKUP" in text
        tags["is_usb"] = "USB" in text
        five_v = _five_v(fam_names.get(d.pin, set()), periph.get(d.pin, []))
        tags["is_5v_tolerant"] = five_v["tolerant"] if five_v else None

        positions.append({
            "position": d.pin,
            "side": d.side,
            "pin_names": pin_names,
            "role_set": {k: v for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1])},
            "pin_type_set": sorted({t for t in [d.role_label] if t}),
            "peripherals": periph.get(d.pin, []),
            "five_v": five_v,
            "tags": tags,
            "breakout": breakout,
            "electrical": None,   # filled below (package-wide, referenced per position)
            "is_fixed": not d.needs_switch,
            "switch_class": d.switch_class,
            "required_cell": d.cell_required,
            "conflict_nets": sorted(set(d.target_nets.values())) if d.needs_switch else [],
            "assignment": assignment,
            "minority_roles": d.minority_identities,
            "variant_note": _variant_note(pin_names),
        })

    electrical = _electrical(conn, package)
    fv = [p["five_v"] for p in positions if p["five_v"]]
    electrical["five_v_positions"] = {
        "classified_gpio": len(fv),
        "tolerant_all_parts": sum(1 for f in fv if f["tolerant"]),
        "family_dependent": sum(1 for f in fv if not f["tolerant"] and any(f["by_family"].values())),
        "not_tolerant_any_part": sum(1 for f in fv if not any(f["by_family"].values())),
    }
    for p in positions:
        p["electrical"] = electrical

    parts = [r[0] for r in conn.execute(
        "SELECT part_number FROM mcu WHERE package_name = ? ORDER BY part_number", (package,))]
    families = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT family FROM mcu WHERE package_name = ?", (package,))})

    incl_osc = rep.must_switch_count + rep.osc_optional_count
    src = conn.execute(
        "SELECT path, imported_at FROM source_artifact ORDER BY id DESC LIMIT 1").fetchone()
    data = {
        "package": package,
        "schema_version": 4,
        "manifest": {
            "part_count": len(parts),
            "supported_parts": parts,
            "supported_families": families,
            "source": "CubeMX MCU XML via tools/stm32_db.py",
            # DB origin + rev (spec, Layer B): ties every generated file to the
            # exact CubeMX import it was derived from. Stable across re-runs of
            # the same DB, so outputs stay byte-stable.
            "db_source_path": src[0] if src else None,
            "db_imported_at": src[1] if src else None,
            "channel_policy": policy,
        },
        "rollup": {
            "positions_total": len(positions),
            "must_switch_count": rep.must_switch_count,
            "osc_optional_count": rep.osc_optional_count,
            "fixed_count": rep.fixed_count,
            "switched_pin_count": rep.must_switch_count,
            "incl_osc_count": incl_osc,
            "channel_count": channel_count,
            # Spec (locked 2026-06-30): cells_min = ceil(switched_pin_count / 8),
            # cells_as_built = ceil(channel_count / 8); cards cite the one they mean.
            "cells_min": math.ceil(rep.must_switch_count / 8) if rep.must_switch_count else 0,
            "cells_as_built": math.ceil(channel_count / 8) if channel_count else 0,
        },
        "electrical": electrical,
        "extraction_access": _extraction_access(positions),
        "positions": positions,
    }
    data["card_materials"] = card_materials(data)
    return data


def raw_tsv(conn: sqlite3.Connection, package: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(["part", "package", "position", "canonical_pin_name", "raw_pin_name",
                "pin_type", "electrical_class"])
    for part, pin, canon, raw, typ, ec in conn.execute(
        "SELECT m.part_number, p.physical_pin_number, p.canonical_pin_name, p.raw_pin_name, "
        "p.pin_type, p.electrical_class FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id "
        "WHERE m.package_name = ? ORDER BY m.part_number, p.physical_pin_number", (package,)):
        w.writerow([part, package, pin, canon, raw, typ, ec])
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# KiCad socket symbol (Phase D) — a .kicad_sym generated from the authority so the
# card schematic starts from the derived per-pin nets, never hand-authored. Stock
# LQFP footprint referenced (no land pattern reinvented). KiCad 6+ S-expression.
# ─────────────────────────────────────────────────────────────────────────────
_KICAD_FOOTPRINT = {
    "LQFP64": "Package_QFP:LQFP-64_10x10mm_P0.5mm",
    "LQFP100": "Package_QFP:LQFP-100_14x14mm_P0.5mm",
}
_POWER_NETS = {"VTARGET", "VDDA_TGT", "VREF_TGT", "VBAT_TGT", "VCAP_NODE", "GND", "VSSA_TGT"}


def _sx(s) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _pin_kind(p: dict, net: str) -> str:
    """KiCad electrical type for a socket pin from its net: power nets are
    power_in; everything else (switched or lane) is bidirectional."""
    return "power_in" if net in _POWER_NETS else "bidirectional"


def to_kicad_symbol(authority: dict) -> str:
    """A KiCad 6+ symbol-library string for the socketed target: one pin per socket
    position, named by its authority net (switched destination / fixed net / lane),
    numbered by position, split left/right, with the stock LQFP footprint."""
    pkg = authority["package"]
    positions = sorted(authority["positions"], key=lambda p: p["position"])
    n_left = (len(positions) + 1) // 2
    left, right = positions[:n_left], positions[n_left:]
    pitch, hw, length = 2.54, 15.24, 2.54

    def col_y(count):
        top = (count - 1) / 2 * pitch
        return [round(top - i * pitch, 2) for i in range(count)]

    half_h = round((max(len(left), len(right)) / 2 * pitch) + pitch, 2)

    def pin(p, side, y):
        net = p["assignment"].get("destination") or p["assignment"].get("net")
        # Fixed IO pins carry the generic net "CARD_LANE" (db.TARGET_NET[ID_IO]),
        # which is truthy — so the old `or f"CARD_LANE_{position}"` fallback was
        # dead code and every fixed IO pin got the identical name "CARD_LANE",
        # defeating per-lane identity in the symbol. Give each its socket-numbered
        # lane (matching the by_pin lane policy elsewhere: CARD_LANE_{pin:03d}).
        if net in (None, "", "CARD_LANE"):
            net = f"CARD_LANE_{p['position']:03d}"
        at = f"(at {-(hw + length)} {y} 0)" if side == "L" else f"(at {hw + length} {y} 180)"
        return (f'      (pin {_pin_kind(p, net)} line {at} (length {length})\n'
                f'        (name "{_sx(net)}" (effects (font (size 1.27 1.27))))\n'
                f'        (number "{p["position"]}" (effects (font (size 1.27 1.27)))))')

    pins = ([pin(p, "L", y) for p, y in zip(left, col_y(len(left)))]
            + [pin(p, "R", y) for p, y in zip(right, col_y(len(right)))])
    sym = f"{pkg}_SOCKET"
    fp = _KICAD_FOOTPRINT.get(pkg, "")
    ref_y = round(half_h + 2.54, 2)
    return (
        '(kicad_symbol_lib (version 20211014) (generator stm32_authority)\n'
        f'  (symbol "{sym}" (in_bom yes) (on_board yes)\n'
        f'    (property "Reference" "U" (at 0 {ref_y} 0) (effects (font (size 1.27 1.27))))\n'
        f'    (property "Value" "{sym}" (at 0 {-ref_y} 0) (effects (font (size 1.27 1.27))))\n'
        f'    (property "Footprint" "{_sx(fp)}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
        f'    (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))\n'
        f'    (symbol "{sym}_1_1"\n'
        f'      (rectangle (start {-hw} {half_h}) (end {hw} {-half_h})\n'
        f'        (stroke (width 0.254) (type default)) (fill (type background)))\n'
        + "\n".join(pins) + "\n"
        '    )\n  )\n)\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Minimal block-style YAML emitter (stdlib only)
# ─────────────────────────────────────────────────────────────────────────────
def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if s == "" or re.search(r'[:#\[\]\{\},&*!|>%@`"\']|^\s|\s$|^[-?]', s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _yaml(v, indent=0) -> list:
    pad = "  " * indent
    lines: list = []
    if isinstance(v, dict):
        if not v:
            return [pad + "{}"]
        for k, val in v.items():
            key = _yaml_scalar(k)
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{pad}{key}:")
                lines += _yaml(val, indent + 1)
            else:
                lines.append(f"{pad}{key}: {_yaml_inline(val)}")
    elif isinstance(v, list):
        if not v:
            return [pad + "[]"]
        for item in v:
            if isinstance(item, (dict, list)) and item:
                sub = _yaml(item, indent + 1)
                sub[0] = pad + "- " + sub[0].lstrip()
                lines.append(sub[0])
                lines += sub[1:]
            else:
                lines.append(f"{pad}- {_yaml_inline(item)}")
    else:
        lines.append(pad + _yaml_scalar(v))
    return lines


def _yaml_inline(v) -> str:
    if isinstance(v, dict):
        return "{}" if not v else "{" + ", ".join(f"{_yaml_scalar(k)}: {_yaml_inline(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[]" if not v else "[" + ", ".join(_yaml_inline(x) for x in v) + "]"
    return _yaml_scalar(v)


def to_yaml(data: dict) -> str:
    return "\n".join(_yaml(data)) + "\n"


def serializable(data: dict) -> dict:
    """The authority dict shaped for file output: the package-wide electrical block
    stays top-level only instead of being repeated verbatim inside all 64/100
    positions (which bloats the JSON/YAML ~100x with identical copies). In-memory
    consumers keep the per-position reference; files carry it once."""
    slim = dict(data)
    slim["positions"] = [
        {k: v for k, v in p.items() if k != "electrical"} for p in data["positions"]
    ]
    return slim


# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────
def write_authority(conn: sqlite3.Connection, package: str, out_dir: Path) -> dict:
    """Write pinout_authority_<pkg>.{yaml,json} + pins_<pkg>.tsv + <pkg>_socket.kicad_sym.
    Returns summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(conn, package)
    slim = serializable(data)
    (out_dir / f"pinout_authority_{package}.json").write_text(
        json.dumps(slim, indent=2), encoding="utf-8", newline="\n")
    (out_dir / f"pinout_authority_{package}.yaml").write_text(
        to_yaml(slim), encoding="utf-8", newline="\n")
    (out_dir / f"pins_{package}.tsv").write_text(
        raw_tsv(conn, package), encoding="utf-8", newline="\n")
    (out_dir / f"{package}_socket.kicad_sym").write_text(
        to_kicad_symbol(data), encoding="utf-8", newline="\n")
    (out_dir / f"pins_{package}.csv").write_text(
        to_csv(data), encoding="utf-8", newline="\n")
    (out_dir / f"authority_{package}.md").write_text(
        to_markdown(data), encoding="utf-8", newline="\n")
    (out_dir / f"switchmap_{package}.json").write_text(
        to_switchmap_json(data), encoding="utf-8", newline="\n")
    (out_dir / f"switchmap_{package}.h").write_text(
        to_switchmap_c(data), encoding="utf-8", newline="\n")
    (out_dir / f"wiring_{package}.md").write_text(
        to_wiring_md(data), encoding="utf-8", newline="\n")
    svg_files = []
    try:
        from stm32_pins_tab import pin_map_svg
        (out_dir / f"pinmap_{package}.svg").write_text(
            pin_map_svg(data), encoding="utf-8", newline="\n")
        svg_files = [f"pinmap_{package}.svg"]
    except Exception:
        pass                                  # headless without PyQt5: skip the SVG
    return {
        "package": package, "out_dir": str(out_dir),
        "files": [f"pinout_authority_{package}.yaml", f"pinout_authority_{package}.json",
                  f"pins_{package}.tsv", f"{package}_socket.kicad_sym",
                  f"pins_{package}.csv", f"authority_{package}.md",
                  f"switchmap_{package}.json", f"switchmap_{package}.h",
                  f"wiring_{package}.md"] + svg_files,
        "rollup": data["rollup"],
    }


def _stamp_line(authority: dict) -> str:
    """One provenance line for the text exports: DB origin + rev (spec, Layer B).
    Derived from the DB import record, not the wall clock, so re-running against
    the same database stays byte-stable."""
    m = authority.get("manifest", {})
    return (f"Generated by tools/stm32_authority.py (schema v{authority.get('schema_version', '?')}) "
            f"from {m.get('source', 'the CubeMX DB')}; DB imported {m.get('db_imported_at') or 'unknown'}; "
            f"channel policy {m.get('channel_policy', 'dominant')}.")


# ─────────────────────────────────────────────────────────────────────────────
# Claims + CLI — the drift gate, runnable headless (CI / pre-commit / vault save)
# ─────────────────────────────────────────────────────────────────────────────
def load_claims(path) -> dict:
    """Read a claims file for lint_card: JSON, or a flat YAML subset
    ('package: NAME' plus 'field: value' lines, optionally nested one level
    under 'claims:'). Values are ints where they look like ints."""
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        doc = json.loads(text)
        return {"package": doc.get("package"), "claims": doc.get("claims", doc)}
    package, claims, in_claims = None, {}, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        indented = line[0] in " \t"
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if key == "package" and not indented:
            package = val
            continue
        if key == "claims" and not indented:
            in_claims = True
            continue
        if indented and not in_claims:
            continue
        if val:
            try:
                claims[key] = int(val)
            except ValueError:
                claims[key] = val
    return {"package": package, "claims": claims}


def run_lint(conn: sqlite3.Connection, claim_paths) -> tuple:
    """Run the drift gate over claim files. Returns (all_ok, report_lines)."""
    ok, lines = True, []
    for cp in claim_paths:
        doc = load_claims(cp)
        pkg = doc.get("package")
        if not pkg:
            ok = False
            lines.append(f"{cp}: no 'package:' field — cannot lint")
            continue
        authority = build(conn, pkg)
        findings = lint_card(authority, doc["claims"])
        for f in findings:
            state = "OK " if f["ok"] else ("??" if f["ok"] is None else "DRIFT")
            if f["ok"] is False:
                ok = False
            note = f" ({f['detail']})" if f.get("detail") else ""
            lines.append(f"{pkg} {state} {f['field']}: claimed {f['claimed']}, "
                         f"actual {f['actual']}{note}")
    return ok, lines


def main(argv=None) -> int:
    """Headless entry point: (re)build the DB if needed, regenerate every export,
    and optionally run the card-vs-authority drift gate. Nonzero exit on drift, so
    the vault copies can never silently go stale.

        python stm32_authority.py --out <dir> [--out <dir2>] [--packages LQFP64,LQFP100]
                                  [--db <sqlite>] [--source <cubemx mcu dir>]
                                  [--lint <claims.yaml> ...] [--lint-only]
    """
    import argparse
    ap = argparse.ArgumentParser(description="STM32 pinout-authority generator + drift gate")
    ap.add_argument("--db", default=None, help="SQLite DB path (default: the app's DB)")
    ap.add_argument("--source", default=None, help="CubeMX 'mcu' XML folder (to build the DB)")
    ap.add_argument("--out", action="append", default=[],
                    help="Output directory (repeatable — e.g. vault Datasets + Brain/data)")
    ap.add_argument("--packages", default="LQFP64,LQFP100")
    ap.add_argument("--lint", action="append", default=[], help="Claims file(s) to check")
    ap.add_argument("--lint-only", action="store_true", help="Skip generation; only lint")
    args = ap.parse_args(argv)

    dbp = Path(args.db) if args.db else db.default_db_path()
    if not dbp.exists():
        src = args.source or db.default_cubemx_source()
        if not src:
            print("No database and no CubeMX source found; pass --source.")
            return 2
        print(f"Building database from {src} ...")
        db.build_database(src, dbp)
    conn = db.connect(dbp)
    try:
        packages = [p.strip() for p in args.packages.split(",") if p.strip()]
        if not args.lint_only:
            outs = [Path(o) for o in (args.out or ["."])]
            for out in outs:
                for pkg in packages:
                    summary = write_authority(conn, pkg, out)
                    r = summary["rollup"]
                    print(f"{pkg} -> {out}  ({len(summary['files'])} files; "
                          f"{r['channel_count']} channels, cells {r['cells_min']}/{r['cells_as_built']})")
        if args.lint:
            ok, lines = run_lint(conn, args.lint)
            print("\n".join(lines))
            if not ok:
                print("DRIFT DETECTED — a build card disagrees with the authority.")
                return 1
            print("Drift gate: all claims match the authority.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
