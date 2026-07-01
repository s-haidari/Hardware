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
# memory-boot-mode tables (unchanged in Rev 70); PDF saved in the vault at
# Sources/Datasheets/. Per family = the UNION of ROM-bootloader pins across its
# sub-lines. USART1=PA9/PA10 and USB-DFU=PA11/PA12 are universal. See
# docs/stm32-pins.md. Higher-density pins (PIx/PEx/PFx) simply never match on
# LQFP64/LQFP100. F0/F3 have no CAN/SPI bootloader; F2 has no I2C/SPI bootloader.
BOOTLOADER_PINS: dict = {
    "STM32F0": {
        "USART": {"PA9", "PA10", "PA14", "PA15", "PA2", "PA3"},
        "I2C": {"PB6", "PB7"},
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F1": {
        "USART": {"PA9", "PA10", "PD5", "PD6"},
        "CAN": {"PB5", "PB6"},                       # connectivity line: CAN2 RX=PB5, TX=PB6
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F2": {
        "USART": {"PA9", "PA10", "PB10", "PB11", "PC10", "PC11"},
        "CAN": {"PB5", "PB13"},                      # CAN2 RX=PB5, TX=PB13
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F3": {
        "USART": {"PA9", "PA10", "PD5", "PD6"},
        "I2C": {"PB6", "PB7", "PA8"},
        "USB-DFU": {"PA11", "PA12"},
    },
    "STM32F4": {
        "USART": {"PA9", "PA10", "PB10", "PB11", "PC10", "PC11"},
        "CAN": {"PB5", "PB13"},
        "USB-DFU": {"PA11", "PA12"},
        "I2C": {"PB6", "PB7", "PB9", "PF0", "PF1", "PA8", "PC9"},
        "SPI": {"PA4", "PA5", "PA6", "PA7", "PI0", "PI1", "PI2", "PI3", "PE11", "PE12", "PE13", "PE14"},
    },
    "STM32F7": {
        "USART": {"PA9", "PA10", "PB10", "PB11", "PC10", "PC11"},
        "CAN": {"PD0", "PD1"},                       # F7: CAN1 RX=PD0, TX=PD1
        "USB-DFU": {"PA11", "PA12"},
        "I2C": {"PB6", "PB9", "PF0", "PF1", "PA8", "PC9"},
        "SPI": {"PA4", "PA5", "PA6", "PA7", "PI0", "PI1", "PI2", "PI3", "PE11", "PE12", "PE13", "PE14"},
    },
}

# family -> max I/O source/sink current per pin, mA (from ST datasheets).
# UNVERIFIED — the datasheet fetch did not complete; left null rather than guessed
# (vault Hard Rule 10). Fill with cited per-family constants later.
IO_CURRENT_MA: dict = {}

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
    """Package-wide VDD/VDDA range (from CubeMX <Voltage>) + IO current by family."""
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
    currents = sorted({IO_CURRENT_MA[f] for f in fams if f in IO_CURRENT_MA})
    return {
        "vdd_range_v": [min(vmins), max(vmaxs)] if vmins and vmaxs else None,
        "max_io_current_ma": currents[0] if len(currents) == 1 else (currents or None),
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
def build(conn: sqlite3.Connection, package: str) -> dict:
    rep = db.package_report(conn, package)
    fam_names = _families_at(conn, package)
    adg = _adg714_map(rep)
    blob, ecs = _blob_at(conn, package)

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

        positions.append({
            "position": d.pin,
            "side": d.side,
            "pin_names": pin_names,
            "role_set": {k: v for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1])},
            "pin_type_set": sorted({t for t in [d.role_label] if t}),
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
    for p in positions:
        p["electrical"] = electrical

    parts = [r[0] for r in conn.execute(
        "SELECT part_number FROM mcu WHERE package_name = ? ORDER BY part_number", (package,))]
    families = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT family FROM mcu WHERE package_name = ?", (package,))})

    incl_osc = rep.must_switch_count + rep.osc_optional_count
    return {
        "package": package,
        "schema_version": 2,
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
