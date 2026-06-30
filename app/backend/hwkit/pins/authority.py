"""
authority.py — the Pinout Authority Generator (vault spec 'Pinout Authority
Generator'). Emits one canonical per-package authority (YAML + JSON) plus a raw
per-(part,pin) TSV, derived from the CubeMX database — never hand-authored.

Derived per socket position: pin_names (per-part, exposes F469/F479 shifts),
role_set, extraction tags, switch decision (from switch_engine), and a rollup
(switched_pin_count -> cells_min). Manifest records the parts aggregated over.
"""
from __future__ import annotations

import csv
import io
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import switch_engine as se

_DEBUG_ROLE = {"swclk": "SWCLK", "swdio": "SWDIO", "swo": "SWO", "jtag_extra": "JTAG"}
_ANALOG_SUPPLY = {"power_vdda", "power_vref"}


def _position_tags(role_names: set[str]) -> dict:
    debug = sorted({_DEBUG_ROLE[n] for n in role_names if n in _DEBUG_ROLE})
    return {
        "is_debug": bool(debug),
        "debug_role": debug,
        "is_trace": False,                 # no trace role in this DB vocabulary
        "is_boot": "boot" in role_names,
        "is_clock": "oscillator_hse" in role_names,
        "is_core_power": "vcap" in role_names,
        "is_analog_supply": bool(role_names & _ANALOG_SUPPLY),
        "bootloader_periph": [],           # not derivable from this DB
    }


def build(conn: sqlite3.Connection, package: str) -> dict:
    """The canonical authority dict for one package."""
    rep = se.package_report(conn, package)

    # per-part pin names at each position (exposes part-dependent shifts)
    names: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in conn.execute(
        "SELECT p.physical_pin_number AS pin, p.canonical_pin_name AS nm, "
        "COUNT(DISTINCT p.mcu_id) AS n FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id "
        "WHERE m.package_name = ? GROUP BY pin, nm", (package,),
    ):
        names[int(r["pin"])][str(r["nm"])] = int(r["n"])

    # role-name set at each position (for tags)
    roles_at: dict[int, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT p.physical_pin_number AS pin, pr.role_name AS rn FROM pin_role pr "
        "JOIN mcu_package_pin p ON p.id = pr.mcu_package_pin_id JOIN mcu m ON m.id = p.mcu_id "
        "WHERE m.package_name = ? GROUP BY pin, rn", (package,),
    ):
        roles_at[int(r["pin"])].add(str(r["rn"]))

    positions = []
    for d in sorted(rep.decisions, key=lambda d: d.pin):
        positions.append({
            "position": d.pin,
            "pin_names": dict(sorted(names.get(d.pin, {}).items(), key=lambda kv: -kv[1])),
            "role_set": {k: v for k, v in sorted(d.identities.items(), key=lambda kv: -kv[1])},
            "tags": _position_tags(roles_at.get(d.pin, set())),
            "is_fixed": not d.needs_switch,
            "switch_class": d.switch_class,
            "conflict_nets": sorted(set(d.target_nets.values())) if d.needs_switch else [],
            "assignment": (
                {"kind": "switched", "destination": d.primary_target_net}
                if d.needs_switch else {"kind": "direct"}
            ),
            "required_cell": d.cell_required,
            "minority_roles": d.minority_identities,
        })

    parts = [r[0] for r in conn.execute(
        "SELECT part_number FROM mcu WHERE package_name = ? ORDER BY part_number", (package,))]

    return {
        "package": package,
        "schema_version": 1,
        "manifest": {"part_count": len(parts), "supported_parts": parts,
                     "source": "CubeMX via stm32_profiles.sqlite"},
        "rollup": {
            "positions_total": len(positions),
            "must_switch_count": rep.must_switch_count,
            "osc_optional_count": rep.osc_optional_count,
            "fixed_count": rep.fixed_count,
            "cells_min": math.ceil(rep.must_switch_count / 8) if rep.must_switch_count else 0,
        },
        "positions": positions,
    }


def raw_tsv(conn: sqlite3.Connection, package: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(["part", "package", "position", "pin_name"])
    for r in conn.execute(
        "SELECT m.part_number AS part, p.physical_pin_number AS pin, p.canonical_pin_name AS nm "
        "FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id WHERE m.package_name = ? "
        "ORDER BY part, pin", (package,),
    ):
        w.writerow([r["part"], package, r["pin"], r["nm"]])
    return buf.getvalue()


def write_authority(conn: sqlite3.Connection, package: str, out_dir: Path) -> dict:
    """Write pinout_authority_<pkg>.{yaml,json} + pins_<pkg>.tsv. Returns summary."""
    from ruamel.yaml import YAML
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build(conn, package)

    (out_dir / f"pinout_authority_{package}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8", newline="\n")
    yaml = YAML()
    yaml.default_flow_style = False
    with (out_dir / f"pinout_authority_{package}.yaml").open("w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(data, fh)
    (out_dir / f"pins_{package}.tsv").write_text(raw_tsv(conn, package), encoding="utf-8", newline="\n")

    return {
        "package": package, "out_dir": str(out_dir),
        "files": [f"pinout_authority_{package}.yaml", f"pinout_authority_{package}.json", f"pins_{package}.tsv"],
        "rollup": data["rollup"],
    }
