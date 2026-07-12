"""Design-rule presets: one-click DRC/ERC severity schemes and predefined-size
templates for the Projects -> Editor.

Two kinds of preset, each with a small set of opinionated built-ins plus a user
store (Save As …) that mirrors nd_fab_presets / nd_pcb_profiles: built-ins are
overridable but never deletable; a user preset of the same name shadows the
built-in and a delete reverts to it.

- **Severity schemes** (Strict / Moderate / Relaxed) assign a severity to EVERY
  KiCad DRC and ERC rule id, so applying one fully defines the board's checking
  posture in a click. They are a defensible STARTING POINT the user then reviews
  in the table before Save — not a fab-certified rule set.
- **Size templates** (Fine-Pitch / Power / Mixed / Hobby) pre-fill the predefined
  track-width / via / diff-pair tables with a coherent set of mm dimensions.

Levels are KiCad's own: "error" / "warning" / "ignore" (see
nd_project_settings_manager.SEVERITY_LEVELS). Every rule id below is validated
against the psm rule lists by tests, so a typo can never ship a dead scheme.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import nd_project_settings_manager as psm

DESIGN_PRESETS_VERSION = 1

# ── DRC rule categories (every DRC_RULE_ID lands in exactly one) ──────────────
_DRC_CRITICAL = (
    "clearance", "creepage", "track_width", "annular_width", "connection_width",
    "hole_clearance", "hole_to_hole", "holes_co_located", "copper_edge_clearance",
    "shorting_items", "unconnected_items", "isolated_copper", "invalid_outline",
    "items_not_allowed", "item_on_disabled_layer", "net_conflict",
    "drill_out_of_range", "microvia_drill_out_of_range",
)
_DRC_MANUFACTURING = (
    "starved_thermal", "copper_sliver", "via_dangling", "track_dangling",
    "zones_intersect", "too_many_vias", "length_out_of_range", "skew_out_of_range",
    "diff_pair_uncoupled_length_too_long",
)
_DRC_FOOTPRINT = (
    "courtyards_overlap", "missing_courtyard", "malformed_courtyard",
    "footprint_type_mismatch", "footprint_symbol_mismatch", "lib_footprint_mismatch",
    "lib_footprint_issues", "duplicate_footprints", "extra_footprint", "missing_footprint",
)
_DRC_COSMETIC = ("silk_overlap", "silk_over_copper", "silk_edge_clearance",
                 "text_height", "text_thickness")
_DRC_MISC = ("unresolved_variable",)

# ── ERC rule categories ───────────────────────────────────────────────────────
_ERC_ELECTRICAL = (
    "pin_to_pin", "power_pin_not_driven", "missing_power_pin", "pin_not_driven",
    "bus_to_bus_conflict", "bus_to_net_conflict", "duplicate_reference",
    "multiple_net_names",
)
_ERC_CONNECTIVITY = (
    "pin_not_connected", "no_connect_connected", "no_connect_dangling",
    "label_dangling", "wire_dangling", "hier_label_mismatch", "net_not_bus_member",
    "single_global_label",
)
_ERC_SYMBOL = (
    "lib_symbol_issues", "lib_symbol_mismatch", "different_unit_net",
    "unit_value_mismatch", "extra_units", "missing_unit", "missing_input_pin",
    "missing_bidi_pin", "undefined_netclass", "simulation_model_issue",
)
_ERC_STYLE = ("unannotated", "similar_labels", "endpoint_off_grid")
_ERC_MISC = ("unresolved_variable",)

# ── scheme posture: (category -> level) per scheme, DRC then ERC ──────────────
# The order of the tuples matches the category tuples above.
_DRC_CATS = (_DRC_CRITICAL, _DRC_MANUFACTURING, _DRC_FOOTPRINT, _DRC_COSMETIC, _DRC_MISC)
_ERC_CATS = (_ERC_ELECTRICAL, _ERC_CONNECTIVITY, _ERC_SYMBOL, _ERC_STYLE, _ERC_MISC)

_SCHEME_POSTURE = {
    # scheme -> (drc levels per category, erc levels per category)
    "Strict":   (("error", "error", "error", "warning", "error"),
                 ("error", "error", "error", "warning", "error")),
    "Moderate": (("error", "warning", "warning", "warning", "error"),
                 ("error", "warning", "warning", "warning", "error")),
    "Relaxed":  (("error", "warning", "warning", "ignore", "warning"),
                 ("error", "warning", "ignore", "ignore", "warning")),
}


def _build_scheme(drc_levels, erc_levels) -> dict:
    drc: Dict[str, str] = {}
    for cats, level in zip(_DRC_CATS, drc_levels):
        for rid in cats:
            drc[rid] = level
    erc: Dict[str, str] = {}
    for cats, level in zip(_ERC_CATS, erc_levels):
        for rid in cats:
            erc[rid] = level
    return {"drc": drc, "erc": erc}


BUILTIN_SEVERITY_SCHEMES: Dict[str, dict] = {
    name: _build_scheme(dl, el) for name, (dl, el) in _SCHEME_POSTURE.items()
}
SEVERITY_SCHEME_ORDER = ("Strict", "Moderate", "Relaxed")

# ── size templates (mm): track widths, (via Ø, drill), (dp width, gap, via gap) ─
BUILTIN_SIZE_TEMPLATES: Dict[str, dict] = {
    "Fine-Pitch": {
        "track": [(0.127,), (0.15,), (0.2,)],
        "via": [(0.45, 0.25), (0.6, 0.3)],
        "dp": [(0.127, 0.127, 0.2), (0.1, 0.1, 0.15)],
    },
    "Power": {
        "track": [(0.4,), (0.8,), (1.5,)],
        "via": [(0.8, 0.4), (1.2, 0.6)],
        "dp": [],
    },
    "Mixed": {
        "track": [(0.2,), (0.25,), (0.4,), (0.6,)],
        "via": [(0.6, 0.3), (0.8, 0.4)],
        "dp": [(0.2, 0.15, 0.25)],
    },
    "Hobby": {
        "track": [(0.25,), (0.4,), (0.6,)],
        "via": [(0.8, 0.4), (1.0, 0.5)],
        "dp": [],
    },
}
SIZE_TEMPLATE_ORDER = ("Fine-Pitch", "Power", "Mixed", "Hobby")


# ── user persistence (one JSON file, two sections) ────────────────────────────
def _store_path() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        try:
            import LibraryManager as _LM
            loc = _LM.library_location()
            if loc:
                return Path(loc) / "design_presets.json"
        except Exception:  # noqa: BLE001
            pass
        return Path(sys.executable).resolve().parent / "design_presets.json"
    return Path(__file__).resolve().parent / "design_presets.json"


def _load_store(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_store(data: dict, path: Path) -> None:
    data["version"] = DESIGN_PRESETS_VERSION
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)                                   # atomic


# ── severity schemes ──────────────────────────────────────────────────────────
def is_builtin_scheme(name: str) -> bool:
    return name in BUILTIN_SEVERITY_SCHEMES


def _user_schemes(path: Path) -> dict:
    return _load_store(path).get("severity_schemes", {}) or {}


def load_severity_schemes(path: Optional[Path] = None) -> Dict[str, dict]:
    """Built-ins (canonical order) with user overrides applied, then user-only
    schemes appended. Each scheme is {"drc": {rule: level}, "erc": {rule: level}}."""
    path = path or _store_path()
    user = _user_schemes(path)
    out: Dict[str, dict] = {}
    for name in SEVERITY_SCHEME_ORDER:
        out[name] = user.get(name, BUILTIN_SEVERITY_SCHEMES[name])
    for name, sch in user.items():
        if name not in BUILTIN_SEVERITY_SCHEMES:
            out[name] = sch
    return out


def get_severity_scheme(name: str, path: Optional[Path] = None) -> Optional[dict]:
    return load_severity_schemes(path).get(name)


def save_severity_scheme(name: str, drc: Dict[str, str], erc: Dict[str, str],
                         path: Optional[Path] = None) -> None:
    """Upsert a user severity scheme. Only valid rule ids / levels are stored, so a
    stray combo value can never write a scheme KiCad would reject."""
    path = path or _store_path()
    data = _load_store(path)
    schemes = data.get("severity_schemes", {}) or {}
    schemes[name] = {
        "drc": {r: lv for r, lv in drc.items()
                if r in psm.DRC_RULE_IDS and lv in psm.SEVERITY_LEVELS},
        "erc": {r: lv for r, lv in erc.items()
                if r in psm.ERC_RULE_IDS and lv in psm.SEVERITY_LEVELS},
    }
    data["severity_schemes"] = schemes
    _write_store(data, path)


def delete_severity_scheme(name: str, path: Optional[Path] = None) -> bool:
    path = path or _store_path()
    data = _load_store(path)
    schemes = data.get("severity_schemes", {}) or {}
    if name not in schemes:
        return False
    del schemes[name]
    data["severity_schemes"] = schemes
    _write_store(data, path)
    return True


# ── size templates ────────────────────────────────────────────────────────────
def is_builtin_template(name: str) -> bool:
    return name in BUILTIN_SIZE_TEMPLATES


def _normalise_template(t: dict) -> dict:
    """Coerce a stored template's rows back to tuples of floats (JSON stored lists)."""
    return {
        "track": [tuple(float(x) for x in row) for row in t.get("track", [])],
        "via": [tuple(float(x) for x in row) for row in t.get("via", [])],
        "dp": [tuple(float(x) for x in row) for row in t.get("dp", [])],
    }


def _user_templates(path: Path) -> dict:
    return _load_store(path).get("size_templates", {}) or {}


def load_size_templates(path: Optional[Path] = None) -> Dict[str, dict]:
    """Built-ins (canonical order) with user overrides applied, then user-only
    templates appended. Each is {"track": [(w,)], "via": [(d,dr)], "dp": [(w,g,vg)]}."""
    path = path or _store_path()
    user = _user_templates(path)
    out: Dict[str, dict] = {}
    for name in SIZE_TEMPLATE_ORDER:
        out[name] = _normalise_template(user.get(name, BUILTIN_SIZE_TEMPLATES[name]))
    for name, t in user.items():
        if name not in BUILTIN_SIZE_TEMPLATES:
            out[name] = _normalise_template(t)
    return out


def get_size_template(name: str, path: Optional[Path] = None) -> Optional[dict]:
    return load_size_templates(path).get(name)


def save_size_template(name: str, track: List[Tuple], via: List[Tuple],
                       dp: List[Tuple], path: Optional[Path] = None) -> None:
    """Upsert a user size template. Zero/empty rows are dropped so a template never
    carries the absent-sentinel rows the tables tolerate for live editing."""
    path = path or _store_path()
    data = _load_store(path)
    templates = data.get("size_templates", {}) or {}
    templates[name] = {
        "track": [[float(r[0])] for r in track if r and float(r[0]) > 0],
        "via": [[float(r[0]), float(r[1])] for r in via
                if len(r) >= 2 and not (float(r[0]) == 0 and float(r[1]) == 0)],
        "dp": [[float(r[0]), float(r[1]), float(r[2])] for r in dp
               if len(r) >= 3 and not (float(r[0]) == 0 and float(r[1]) == 0 and float(r[2]) == 0)],
    }
    data["size_templates"] = templates
    _write_store(data, path)


def delete_size_template(name: str, path: Optional[Path] = None) -> bool:
    path = path or _store_path()
    data = _load_store(path)
    templates = data.get("size_templates", {}) or {}
    if name not in templates:
        return False
    del templates[name]
    data["size_templates"] = templates
    _write_store(data, path)
    return True
