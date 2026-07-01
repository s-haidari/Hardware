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
# matches, for that part's family. Empty until AN2606 is transcribed.
BOOTLOADER_PINS: dict = {}

# family -> max I/O source/sink current per pin, mA (from ST datasheets).
IO_CURRENT_MA: dict = {}

_DEBUG_ROLE = {"swclk": "SWCLK", "swdio": "SWDIO", "swo": "SWO", "jtag_extra": "JTAG"}
_ANALOG_SUPPLY = {"power_vdda", "power_vref"}
_TRACE_ROLE: set = set()   # no trace role in the CubeMX vocabulary yet


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
        "is_trace": bool(role_names & _TRACE_ROLE),
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


# ─────────────────────────────────────────────────────────────────────────────
# Build the authority
# ─────────────────────────────────────────────────────────────────────────────
def build(conn: sqlite3.Connection, package: str) -> dict:
    rep = db.package_report(conn, package)
    fam_names = _families_at(conn, package)
    adg = _adg714_map(rep)

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
        positions.append({
            "position": d.pin,
            "side": d.side,
            "pin_names": pin_names,
            "role_set": {k: v for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1])},
            "pin_type_set": sorted({t for t in [d.role_label] if t}),
            "tags": _position_tags(roles_at.get(d.pin, set()), fam_names.get(d.pin, set())),
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
