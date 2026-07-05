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
from dataclasses import dataclass

MIL = 0.0254  # mm per mil


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
