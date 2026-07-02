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


def _adg714_map(rep) -> dict:
    """position -> {cell, channel, destination} for the must-switch pins.
    Deterministic: switched positions ascending, cell=floor(i/8)+1, channel=i%8+1."""
    out: dict = {}
    for bank in rep.adg714_banks(include_osc=False):
        for ch, (pin, dest) in enumerate(bank.channels, start=1):
            out[pin] = {"cell": bank.index, "channel": ch, "destination": dest}
    return out


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
        "ref": "U_SW_*", "part": "ADG714 (4x SPST, 8 ch/pkg)", "qty": r["cells_as_built"],
        "role": "switch fabric",
        "note": f"{r['must_switch_count']} must-switch (+{r['osc_optional_count']} osc-optional) "
                f"-> {r['cells_min']} min / {r['cells_as_built']} as-built cells",
    }]
    if vcap_fams:
        items.append({
            "ref": "C_VCAP_1/2", "part": "2.2uF ceramic X7R (ESR<2ohm)", "qty": 2,
            "role": "regulator VCAP",
            "note": f"required for {', '.join(vcap_fams)} sockets (VCAP_1/VCAP_2); "
                    f"F0/F1/F3 have no VCAP pin (DNP/harmless)",
        })
    if n_vdd:
        items.append({
            "ref": "C_DEC_*", "part": "100nF ceramic X7R", "qty": n_vdd,
            "role": "VDD decoupling", "note": f"one per VDD/VSS pair (worst-case {n_vdd} on LQFP100)",
        })
    items.append({"ref": "C_BULK", "part": "4.7uF ceramic", "qty": 1, "role": "bulk",
                  "note": "one bulk cap per package"})
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
    findings = []
    for field, claimed in claims.items():
        if field not in actuals:
            findings.append({"field": field, "claimed": claimed, "actual": None,
                             "ok": None, "detail": "unknown field (not checked)"})
            continue
        actual, detail = actuals[field]
        findings.append({"field": field, "claimed": claimed, "actual": actual,
                         "ok": claimed == actual, "detail": detail})
    return findings


def build(conn: sqlite3.Connection, package: str) -> dict:
    rep = db.package_report(conn, package)
    fam_names = _families_at(conn, package)
    adg = _adg714_map(rep)
    blob, ecs = _blob_at(conn, package)
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
            assignment = {"kind": "switched", "adg714": adg.get(d.pin), "destination": d.primary_target_net}
        elif d.switch_class == db.SWITCH_OSC_OPTIONAL:
            assignment = {"kind": "osc_optional", "destination": d.primary_target_net}
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
    data = {
        "package": package,
        "schema_version": 3,
        "manifest": {
            "part_count": len(parts),
            "supported_parts": parts,
            "supported_families": families,
            "source": "CubeMX MCU XML via tools/stm32_db.py",
        },
        "rollup": {
            "positions_total": len(positions),
            "must_switch_count": rep.must_switch_count,
            "osc_optional_count": rep.osc_optional_count,
            "fixed_count": rep.fixed_count,
            "switched_pin_count": rep.must_switch_count,
            "incl_osc_count": incl_osc,
            "channel_count": incl_osc,
            "cells_min": math.ceil(rep.must_switch_count / 8) if rep.must_switch_count else 0,
            "cells_as_built": math.ceil(incl_osc / 8) if incl_osc else 0,
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


# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────
def write_authority(conn: sqlite3.Connection, package: str, out_dir: Path) -> dict:
    """Write pinout_authority_<pkg>.{yaml,json} + pins_<pkg>.tsv. Returns summary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(conn, package)
    (out_dir / f"pinout_authority_{package}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8", newline="\n")
    (out_dir / f"pinout_authority_{package}.yaml").write_text(
        to_yaml(data), encoding="utf-8", newline="\n")
    (out_dir / f"pins_{package}.tsv").write_text(
        raw_tsv(conn, package), encoding="utf-8", newline="\n")
    return {
        "package": package, "out_dir": str(out_dir),
        "files": [f"pinout_authority_{package}.yaml", f"pinout_authority_{package}.json",
                  f"pins_{package}.tsv"],
        "rollup": data["rollup"],
    }
