"""Fabrication presets — a house-standard baseline for the settings sync.

A FabPreset captures a board house's design rules + stackup so a project can be made
to conform to what that house can actually build. OSH Park's 2-layer and 4-layer
services are provided as presets.

Values are OSH Park's published capabilities (oshpark.com/guidelines / .../services).
The design rules (trace / space / drill / annular / edge) and the board summary
(thickness, copper weight, finish) are the confident, load-bearing numbers. The
per-dielectric 4-layer stackup thicknesses are marked VERIFY — confirm them against
OSH Park's current published 4-layer stackup before a production order, since fab
stackups change and are not worth asserting from memory.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Dict, List, Optional

MIL = 0.0254  # mm per mil
FABP_VERSION = 1


@dataclass(frozen=True)
class FabPreset:
    name: str
    layers: int
    # ── design-rule minimums (mm) ──
    min_track_width: float
    min_clearance: float
    min_drill: float
    min_annular_ring: float
    min_edge_clearance: float
    # ── sensible defaults for newly-placed tracks/vias (mm), >= the minimums ──
    default_track_width: float
    default_via_diameter: float
    default_via_drill: float
    # ── board summary ──
    board_thickness_mm: float
    copper_oz: float
    material: str
    finish: str
    soldermask: str
    # ── hole clearances + mask (mm); OSH Park uses 5 mil / 5 mil / 2 mil both tiers ──
    min_hole_to_hole: float = 5 * MIL
    min_hole_clearance: float = 5 * MIL         # plated drill/copper-to-hole
    mask_expansion: float = 2 * MIL
    inner_copper_oz: float = 0.0                # 0 = no inner layers
    # ── silk/fab text defaults (mm), compatible with the house silk minimum ──
    silk_text_height: float = 1.0
    silk_text_thickness: float = 0.15
    fab_text_height: float = 1.0
    fab_text_thickness: float = 0.15
    # ── (layer, kind, thickness_mm, material) stack, outer -> outer ──
    stackup: tuple = ()
    verify_note: str = ""

    @property
    def min_via_diameter(self) -> float:
        """Smallest annular via: drill + 2 x minimum annular ring."""
        return round(self.min_drill + 2 * self.min_annular_ring, 4)

    def to_dict(self) -> dict:
        """JSON-safe dict for the user-preset store (stackup tuples become lists)."""
        d = dataclasses.asdict(self)
        d["stackup"] = [list(layer) for layer in self.stackup]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FabPreset":
        """Rebuild a FabPreset from a stored dict, tolerating missing optional keys
        (a preset written by an older version) and restoring the stackup tuples.
        Unknown keys are ignored so a newer file never crashes an older reader."""
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        kw["stackup"] = tuple(tuple(layer) for layer in kw.get("stackup", ()))
        return cls(**kw)


# ── OSH Park 2-layer (1.6 mm, 1 oz, ENIG) ────────────────────────────────────
OSH_PARK_2LAYER = FabPreset(
    name="OSH Park 2-layer", layers=2,
    min_track_width=6 * MIL, min_clearance=6 * MIL, min_drill=10 * MIL,
    min_annular_ring=5 * MIL, min_edge_clearance=15 * MIL,
    default_track_width=10 * MIL, default_via_diameter=24 * MIL, default_via_drill=12 * MIL,
    board_thickness_mm=1.6, copper_oz=1.0, material="FR-4", finish="ENIG", soldermask="purple",
    stackup=(
        ("F.Cu", "copper", 0.0432, "copper"),          # 1.7 mil, 1 oz clad+plated
        ("dielectric 1", "core", 1.51, "FR-4"),        # to ~1.6 mm total
        ("B.Cu", "copper", 0.0432, "copper"),
    ),
    verify_note="OSH Park 2-layer (docs.oshpark.com/design-tools/kicad, 2026-07-05): "
                "6 mil trace/space, 10 mil drill, 5 mil annular (-> 20 mil via), 5 mil "
                "hole clearances, 2 mil mask, 15 mil edge, 1.6 mm FR-4, 1 oz Cu, ENIG.",
)

# ── OSH Park 4-layer (1.6 mm, 1 oz outer / 0.5 oz inner, FR408HR, ENIG) ───────
# Exact spec from docs.oshpark.com/services/four-layer/ + .../kicad (2026-07-05).
OSH_PARK_4LAYER = FabPreset(
    name="OSH Park 4-layer", layers=4,
    min_track_width=5 * MIL, min_clearance=5 * MIL, min_drill=10 * MIL,
    min_annular_ring=4 * MIL, min_edge_clearance=15 * MIL,   # 4 mil annular -> 18 mil via
    default_track_width=8 * MIL, default_via_diameter=18 * MIL, default_via_drill=10 * MIL,
    board_thickness_mm=1.6, copper_oz=1.0, inner_copper_oz=0.5,
    material="FR408HR (190Tg)", finish="ENIG", soldermask="purple",
    stackup=(
        ("F.Cu", "copper", 0.0432, "copper"),          # 1.7 mil, 1 oz clad+plated
        ("prepreg 1", "prepreg", 0.1999, "FR408HR 2113"),   # 7.87 mil, dk 3.61 @1GHz
        ("In1.Cu", "copper", 0.0175, "copper"),        # 0.68 mil, 0.5 oz
        ("core", "core", 0.9906, "FR408HR"),           # 39 mil
        ("In2.Cu", "copper", 0.0175, "copper"),        # 0.68 mil, 0.5 oz
        ("prepreg 2", "prepreg", 0.1999, "FR408HR 2113"),
        ("B.Cu", "copper", 0.0432, "copper"),
    ),
    verify_note="OSH Park 4-layer (docs.oshpark.com/services/four-layer, 2026-07-05): "
                "5 mil trace/space, 10 mil drill, 4 mil annular (-> 18 mil via), 5 mil "
                "hole clearances, 2 mil mask, 15 mil edge, FR408HR 190Tg, 1 oz outer / "
                "0.5 oz inner, ENIG.",
)

PRESETS = {p.name: p for p in (OSH_PARK_2LAYER, OSH_PARK_4LAYER)}
_BUILTIN_FAB_NAMES = tuple(PRESETS)


# ── user-preset persistence (mirrors nd_pcb_profiles: built-ins overridable, not
#    deletable; pure user presets fully editable) ──────────────────────────────
def _presets_path() -> Path:
    """Where user fab presets are read/written. Under a frozen --onefile exe __file__
    points into the throwaway PyInstaller bundle, so it must write to the user's
    library location; dev keeps it next to the module (matches pcb_profiles.json)."""
    import sys
    if getattr(sys, "frozen", False):
        try:
            import LibraryManager as _LM
            loc = _LM.library_location()
            if loc:
                return Path(loc) / "fab_presets.json"
        except Exception:  # noqa: BLE001
            pass
        return Path(sys.executable).resolve().parent / "fab_presets.json"
    return Path(__file__).resolve().parent / "fab_presets.json"


def _load_user_presets(path: Path) -> List[FabPreset]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    out: List[FabPreset] = []
    for d in data.get("presets", []):
        try:
            out.append(FabPreset.from_dict(d))
        except Exception:  # noqa: BLE001
            pass
    return out


def _write_user_presets(presets: List[FabPreset], path: Path) -> None:
    payload = {"version": FABP_VERSION,
               "presets": [p.to_dict() for p in presets]}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)                                   # atomic


def builtin_names() -> tuple:
    """The code-defined seed preset names (overridable but never deletable outright)."""
    return _BUILTIN_FAB_NAMES


def is_builtin(name: str) -> bool:
    return name in _BUILTIN_FAB_NAMES


def load_presets(path: Optional[Path] = None) -> Dict[str, FabPreset]:
    """Every fab preset by name: built-ins first (canonical order), each replaced by a
    user override of the same name, then any user-only presets appended. Mirrors
    ``nd_pcb_profiles.load_profiles`` so the two stores behave identically."""
    path = path or _presets_path()
    user = {p.name: p for p in _load_user_presets(path)}
    out: Dict[str, FabPreset] = {}
    for name in _BUILTIN_FAB_NAMES:
        out[name] = user.get(name, PRESETS[name])
    for name, p in user.items():
        if name not in _BUILTIN_FAB_NAMES:
            out[name] = p
    return out


def get_preset(name: str, path: Optional[Path] = None) -> Optional[FabPreset]:
    """Merged lookup: a user override/preset wins over the built-in of the same name.
    The single call site the whole app should use instead of ``PRESETS.get`` so custom
    (non-OSH-Park) fabs resolve everywhere."""
    if not name:
        return None
    return load_presets(path).get(name)


def has_user_preset(name: str, path: Optional[Path] = None) -> bool:
    """True when something user-saved exists under ``name`` (a pure user preset or a
    user override of a built-in) — i.e. delete_preset(name) would change something."""
    path = path or _presets_path()
    return any(p.name == name for p in _load_user_presets(path))


def save_preset(preset: FabPreset, path: Optional[Path] = None) -> None:
    """Upsert a preset into the user file. Reusing a built-in's name stores an override
    (the built-in stays the fallback, KiCad-profile style)."""
    path = path or _presets_path()
    user = [p for p in _load_user_presets(path) if p.name != preset.name]
    user.append(preset)
    _write_user_presets(user, path)


def delete_preset(name: str, path: Optional[Path] = None) -> bool:
    """Delete a user preset, or revert a user override of a built-in. Returns False when
    there is nothing user-saved under ``name`` (a pure built-in is already at its default)."""
    path = path or _presets_path()
    user = _load_user_presets(path)
    if not any(p.name == name for p in user):
        return False
    _write_user_presets([p for p in user if p.name != name], path)
    return True


def _fmt(v) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def stackup_block(preset: FabPreset, indent: str = "    ") -> str:
    """The KiCad (stackup …) s-expression for a preset's physical layer stack —
    the piece the setup sync did not cover. Wraps the preset's copper/dielectric
    stack with the standard silk/paste/mask layers and the copper finish. The
    4-layer dielectric thicknesses inherit the preset's VERIFY caveat."""
    mask = f'(color "{preset.soldermask.title()}") '
    L = ["(stackup",
         '  (layer "F.SilkS" (type "Top Silk Screen"))',
         '  (layer "F.Paste" (type "Top Solder Paste"))',
         f'  (layer "F.Mask" (type "Top Solder Mask") {mask}(thickness 0.01))']
    for name, kind, thick, mat in preset.stackup:
        if kind == "copper":
            L.append(f'  (layer "{name}" (type "copper") (thickness {_fmt(thick)}))')
        else:
            L.append(f'  (layer "{name}" (type "{kind}") (thickness {_fmt(thick)}) '
                     f'(material "{mat}") (epsilon_r 4.5) (loss_tangent 0.02))')
    L += [f'  (layer "B.Mask" (type "Bottom Solder Mask") {mask}(thickness 0.01))',
          '  (layer "B.Paste" (type "Bottom Solder Paste"))',
          '  (layer "B.SilkS" (type "Bottom Silk Screen"))',
          f'  (copper_finish "{preset.finish}")',
          "  (dielectric_constraints no)",
          ")"]
    return ("\n" + indent).join(L)


def apply_to_project_settings(settings, preset: FabPreset):
    """Return a copy of a ProjectSettings (mils) populated from a FabPreset (mm).

    Maps the preset's mm-native fab rules onto the mils-native ProjectSettings the
    sync writes into a .kicad_pro (rules.min_clearance / min_track_width, the
    constraint minimums, default via table, and silk/fab text). Board stackup +
    thickness are carried on the preset for the board-side apply (they are not
    ProjectSettings fields)."""
    from nd_project_settings_manager import mm_to_mils as m

    def mil(v, nd=2):
        return round(m(v), nd)

    return dataclasses.replace(
        settings,
        default_clearance=mil(preset.min_clearance),          # -> rules.min_clearance
        default_track_width=mil(preset.min_track_width),      # -> rules.min_track_width
        default_via_diameter=mil(preset.default_via_diameter),
        default_via_drill=mil(preset.default_via_drill),
        min_via_diameter=mil(preset.min_via_diameter),
        min_via_annular_width=mil(preset.min_annular_ring),
        min_through_hole=mil(preset.min_drill),
        min_hole_to_hole=mil(preset.min_hole_to_hole),
        min_hole_clearance=mil(preset.min_hole_clearance),
        min_copper_edge_clearance=mil(preset.min_edge_clearance),
        min_microvia_diameter=mil(preset.min_via_diameter),
        min_microvia_drill=mil(preset.min_drill),
        solder_mask_clearance=mil(preset.mask_expansion),
        silk_text_size=mil(preset.silk_text_height, 1),
        silk_text_thickness=mil(preset.silk_text_thickness, 1),
        fab_text_size=mil(preset.fab_text_height, 1),
        fab_text_thickness=mil(preset.fab_text_thickness, 1),
    )
