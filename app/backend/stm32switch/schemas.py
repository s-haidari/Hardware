"""
schemas.py — JSON Schemas for the generated artifacts.

The CSV schemas are derived from the same column constants the writers use, so
they cannot drift out of sync.  ``write_schemas`` emits them under ``schemas/``.
"""
from __future__ import annotations

import json

from . import normalize
from .paths import schemas_dir, ensure_dirs


def _csv_schema(title: str, columns: list[str], description: str) -> dict:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": title,
        "description": description,
        "type": "array",
        "items": {
            "type": "object",
            "properties": {c: {"type": "string"} for c in columns},
            "required": list(columns),
            "additionalProperties": False,
        },
    }


_CELL_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "cell_library_entry",
    "description": "A reusable hardware cell spec (hardware/cell_library/*.yml).",
    "type": "object",
    "required": ["cell_id", "cell_name", "board_side", "purpose", "used_when",
                 "hierarchical_pins", "default_state", "enable_logic",
                 "safety_rules", "kicad_sheet_name", "screenshot_path"],
    "properties": {
        "cell_id": {"type": "string"},
        "cell_name": {"type": "string"},
        "board_side": {"enum": ["card", "parent"]},
        "purpose": {"type": "string"},
        "used_when": {"type": "string"},
        "hierarchical_pins": {"type": "array", "items": {"type": "string"}},
        "internal_nets": {"type": "array", "items": {"type": "string"}},
        "required_components": {"type": "array"},
        "default_state": {"type": "string"},
        "enable_logic": {"type": "string"},
        "safety_rules": {"type": "array", "items": {"type": "string"}},
        "signal_integrity_rules": {"type": "array", "items": {"type": "string"}},
        "kicad_sheet_name": {"type": "string"},
        "screenshot_path": {"type": "string"},
    },
}

_ROUTER_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "parent_router",
    "description": "A sparse parent service router spec (hardware/parent_routers/*.yml).",
    "type": "object",
    "required": ["router_id", "purpose", "inputs", "outputs", "switch_class", "rules"],
    "properties": {
        "router_id": {"type": "string"},
        "purpose": {"type": "string"},
        "inputs": {"type": "array", "items": {"type": "string"}},
        "outputs": {"type": "array", "items": {"type": "string"}},
        "switch_class": {"type": "string"},
        "rules": {"type": "array", "items": {"type": "string"}},
    },
}


def all_schemas() -> dict[str, dict]:
    return {
        "package_pin_matrix.schema.json": _csv_schema(
            "package_pin_matrix", normalize.CANONICAL_COLUMNS,
            "One row per exact pinout group + socket pin + lane."),
        "pinout_group.schema.json": _csv_schema(
            "pinout_groups", normalize._GROUP_COLS,
            "Exact pinout groups for a package."),
        "lane_summary.schema.json": _csv_schema(
            "lane_summary", normalize._LANE_COLS,
            "Per-lane summary of role set, required cell and routers."),
        "pass_plan.schema.json": _csv_schema(
            "pass_plan", normalize._PASS_COLS,
            "Build pass plan derived from coverage passes."),
        "raw_pin_functions.schema.json": _csv_schema(
            "raw_pin_functions", normalize._FUNC_COLS,
            "Exact CubeMX functions per pin (never broadened)."),
        "parent_standard_ports.schema.json": _csv_schema(
            "parent_standard_ports", normalize._PORT_COLS,
            "Per-group parent standard nets, validated against exact functions."),
        "voltage_requirements.schema.json": _csv_schema(
            "voltage_requirements", normalize._VOLT_COLS,
            "VTARGET range + special-rail branch requirements."),
        "cell_library.schema.json": _CELL_SCHEMA,
        "parent_router.schema.json": _ROUTER_SCHEMA,
    }


def write_schemas() -> int:
    d = schemas_dir()
    ensure_dirs(d)
    schemas = all_schemas()
    for name, schema in schemas.items():
        (d / name).write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(schemas)
