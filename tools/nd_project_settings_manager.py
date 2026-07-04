#!/usr/bin/env python3
"""
project_settings_manager.py — Project Settings Manager for KiCad
Manages universal settings across multiple KiCad projects:
- Text sizes for text boxes (schematics & PCB)
- Footprint text (silkscreen, copper, fab)
- Grid settings
- Design rules (clearances, track widths, etc.)
- Display options

ALL MEASUREMENTS IN MILS (thousandths of an inch).
Optional .kicad_pro.bak backup before each write when backup=True.
Automatically clears cache (.prl, .lck, fp-info-cache).
Completely ignores .history directories.

Supports KiCad v6+ .kicad_pro JSON format.
"""
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

# ═══════════════════════════════════════════════════════════════════
# UNIT CONVERSION
# ═══════════════════════════════════════════════════════════════════
def mils_to_mm(mils: float) -> float:
    """Convert mils to millimeters"""
    return mils * 0.0254

def mm_to_mils(mm: float) -> float:
    """Convert millimeters to mils"""
    return mm / 0.0254

# ═══════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════
def should_ignore_path(path: Path) -> bool:
    """Check if path should be ignored (.history, hidden dirs, etc.)"""
    parts = path.parts
    for part in parts:
        if part == '.history' or part == '__pycache__':
            return True
        if part.startswith('.') and part not in ['.']:
            return True
    return False

def clear_project_cache_files(repo_root: Path, verbose: bool = True) -> dict:
    """
    Clear all KiCad cache files to force settings reload.
    Returns dict with counts of files removed.
    """
    if verbose:
        print("\n=== Clearing KiCad Cache Files ===")

    counts = {
        'prl': 0,
        'lck': 0,
        'fp_cache': 0,
    }

    # Find all cache files (excluding .history)
    prl_files = [f for f in repo_root.rglob("*.kicad_prl") if not should_ignore_path(f)]
    lck_files = [f for f in repo_root.rglob("*.lck") if not should_ignore_path(f)]
    fp_cache_files = [f for f in repo_root.rglob("fp-info-cache") if not should_ignore_path(f)]

    # Remove .prl files (project local settings - UI state, zoom, etc.)
    for prl in prl_files:
        try:
            prl.unlink()
            counts['prl'] += 1
            if verbose:
                try:
                    print(f"  ✓ Cleared: {prl.relative_to(repo_root)}")
                except:
                    print(f"  ✓ Cleared: {prl.name}")
        except Exception as e:
            if verbose:
                print(f"  ✗ Failed: {prl.name} - {e}")

    # Remove .lck files (lock files - may indicate project is open)
    for lck in lck_files:
        try:
            lck.unlink()
            counts['lck'] += 1
            if verbose:
                try:
                    print(f"  ✓ Removed lock: {lck.relative_to(repo_root)}")
                except:
                    print(f"  ✓ Removed lock: {lck.name}")
        except Exception as e:
            if verbose:
                print(f"  ✗ Failed (project may be open): {lck.name} - {e}")

    # Remove fp-info-cache (footprint library cache)
    for cache in fp_cache_files:
        try:
            cache.unlink()
            counts['fp_cache'] += 1
            if verbose:
                try:
                    print(f"  ✓ Cleared: {cache.relative_to(repo_root)}")
                except:
                    print(f"  ✓ Cleared: {cache.name}")
        except Exception as e:
            if verbose:
                print(f"  ✗ Failed: {cache.name} - {e}")

    if verbose:
        print(f"\n📊 Cache Cleanup Summary:")
        print(f"  • Cleared {counts['prl']} .prl files (project local settings)")
        print(f"  • Removed {counts['lck']} .lck files (lock files)")
        print(f"  • Cleared {counts['fp_cache']} fp-info-cache files")
        print(f"\n✅ Cache cleared. Restart KiCad to see changes.\n")

    return counts

# ═══════════════════════════════════════════════════════════════════
# PROJECT SETTINGS DATA STRUCTURE (ALL IN MILS)
# ═══════════════════════════════════════════════════════════════════
@dataclass
class ProjectSettings:
    """Universal project settings - ALL VALUES IN MILS (thousandths of inch)"""

    # Schematic text boxes (manually placed text)
    schematic_text_size: float = 50.0     # 50 mils (1.27mm) - KiCad standard default text size
    schematic_line_width: float = 6.0     # 0.1524mm - default line thickness
    pin_symbol_size: float = 25.0         # 25 mils - pin symbol size
    # KiCad junction dot size is an ENUM CHOICE (index 0-4), NOT a mils value.
    # It maps to schematic.drawing.junction_size_choice. 3 is KiCad's default.
    junction_size: int = 3                # junction_size_choice enum index 0-4

    # Schematic grid
    schematic_grid: str = "50 mil"

    # PCB text boxes (manually placed text)
    pcb_text_size: float = 40.0           # 1.016mm - text box size
    pcb_text_thickness: float = 6.0       # 0.1524mm - text box thickness

    # PCB footprint text - Silkscreen (RefDes, Value, etc.)
    silk_text_size: float = 40.0          # 1.0mm - silkscreen text
    silk_text_thickness: float = 4.0      # 0.1mm - silkscreen line width

    # PCB footprint text - Copper layer
    copper_text_size: float = 60.0        # 1.524mm - copper text
    copper_text_thickness: float = 12.0   # 0.3048mm - copper text line width

    # PCB footprint text - Fab layer
    fab_text_size: float = 40.0           # 1.0mm - fab layer text
    fab_text_thickness: float = 6.0       # 0.15mm - fab layer line width

    # PCB grid
    pcb_grid: str = "25 mil"

    # PCB Design Rules - Default values (mils)
    default_clearance: float = 8.0       # 0.2mm - minimum clearance
    default_track_width: float = 10.0    # 0.254mm - default trace width
    default_via_diameter: float = 32.0   # 0.8mm - via outer diameter
    default_via_drill: float = 16.0      # 0.4mm - via drill hole

    # PCB minimum constraints (Board Setup -> Constraints), mils
    min_via_diameter: float = 20.0          # 0.5mm
    min_via_annular_width: float = 5.0      # 0.127mm - min annular ring
    min_through_hole: float = 12.0          # 0.3mm - min hole diameter
    min_hole_to_hole: float = 10.0          # 0.25mm
    min_hole_clearance: float = 10.0        # 0.25mm
    min_microvia_diameter: float = 8.0      # 0.2mm
    min_microvia_drill: float = 4.0         # 0.1mm
    min_copper_edge_clearance: float = 20.0 # 0.5mm
    min_silk_clearance: float = 0.0         # 0mm

    # Solder mask/paste (mils)
    solder_mask_clearance: float = 2.0   # 0.05mm - mask expansion
    solder_paste_margin: float = -2.0    # -0.05mm - paste shrink

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> 'ProjectSettings':
        """Create from dictionary"""
        return ProjectSettings(**{k: v for k, v in data.items() if k in ProjectSettings.__dataclass_fields__})

    def __str__(self) -> str:
        """Human-readable string representation"""
        return f"""Project Settings (mils):
  Schematic:
    Text box: {self.schematic_text_size} mils ({mils_to_mm(self.schematic_text_size):.3f} mm)
    Line width: {self.schematic_line_width} mils ({mils_to_mm(self.schematic_line_width):.3f} mm)
    Grid: {self.schematic_grid}

  PCB Text Boxes:
    Size: {self.pcb_text_size} mils ({mils_to_mm(self.pcb_text_size):.3f} mm)
    Thickness: {self.pcb_text_thickness} mils ({mils_to_mm(self.pcb_text_thickness):.3f} mm)

  PCB Footprint Text:
    Silkscreen: {self.silk_text_size} mils ({mils_to_mm(self.silk_text_size):.3f} mm)
    Copper: {self.copper_text_size} mils ({mils_to_mm(self.copper_text_size):.3f} mm)

  PCB Design Rules:
    Track width: {self.default_track_width} mils ({mils_to_mm(self.default_track_width):.3f} mm)
    Clearance: {self.default_clearance} mils ({mils_to_mm(self.default_clearance):.3f} mm)
    Via: {self.default_via_diameter}/{self.default_via_drill} mils
    Grid: {self.pcb_grid}"""

# ═══════════════════════════════════════════════════════════════════
# EXTENDED BOARD/SCHEMATIC SETUP COVERAGE (mm-native, KiCad-real keys)
# ═══════════════════════════════════════════════════════════════════
# Everything below expands .kicad_pro coverage beyond the flat mils-based
# ProjectSettings above. It is *additive*: values are mm-native (KiCad's own
# unit) so they never drift through the 0.1-mil grid, and every write targets a
# key confirmed against a real KiCad-written .kicad_pro. Load is preserve-by-
# default (an absent key stays absent — no manufactured defaults) and save is a
# deep-merge (untouched keys are left exactly as KiCad wrote them).

# Valid values for a DRC/ERC rule severity (board.design_settings.rule_severities
# and erc.rule_severities are both name -> one of these strings).
SEVERITY_LEVELS = ("error", "warning", "ignore")

# Curated DRC rule IDs surfaced by the settings UI. Every ID here was verified
# present in a real KiCad-written board.design_settings.rule_severities map.
# (The full map has ~60 IDs; save preserves any not listed here.)
DRC_RULE_IDS = (
    "clearance", "creepage", "track_width", "annular_width", "connection_width",
    "hole_clearance", "hole_to_hole", "holes_co_located", "copper_edge_clearance",
    "courtyards_overlap", "missing_courtyard", "malformed_courtyard",
    "silk_overlap", "silk_over_copper", "silk_edge_clearance",
    "starved_thermal", "via_dangling", "track_dangling", "unconnected_items",
    "shorting_items", "isolated_copper", "copper_sliver", "invalid_outline",
    "item_on_disabled_layer", "items_not_allowed", "unresolved_variable",
    "text_height", "text_thickness", "drill_out_of_range",
    "microvia_drill_out_of_range", "diff_pair_uncoupled_length_too_long",
    "length_out_of_range", "skew_out_of_range", "too_many_vias",
    "footprint_type_mismatch", "footprint_symbol_mismatch",
    "lib_footprint_mismatch", "lib_footprint_issues", "duplicate_footprints",
    "extra_footprint", "missing_footprint", "net_conflict", "zones_intersect",
)

# Curated ERC rule IDs surfaced by the settings UI. Every ID here was verified
# present in a real KiCad-written erc.rule_severities map.
ERC_RULE_IDS = (
    "pin_not_connected", "pin_not_driven", "power_pin_not_driven",
    "missing_power_pin", "missing_input_pin", "missing_bidi_pin", "pin_to_pin",
    "no_connect_connected", "no_connect_dangling", "label_dangling",
    "wire_dangling", "unannotated", "duplicate_reference", "similar_labels",
    "multiple_net_names", "hier_label_mismatch", "bus_to_bus_conflict",
    "bus_to_net_conflict", "net_not_bus_member", "lib_symbol_issues",
    "lib_symbol_mismatch", "different_unit_net", "unit_value_mismatch",
    "unresolved_variable", "endpoint_off_grid", "extra_units", "missing_unit",
    "undefined_netclass", "simulation_model_issue", "single_global_label",
)

# ERC pin-conflict matrix (erc.pin_map) is a 12x12 grid of severity ints. The
# 12 electrical pin types, in KiCad's stored row/column order. These labels are
# UI hints only — the file stores an index-addressed matrix, so the labels are
# never written and cannot corrupt the file.
ERC_PIN_TYPES = (
    "input", "output", "bidirectional", "tri_state", "passive", "free",
    "unspecified", "power_in", "power_out", "open_collector", "open_emitter",
    "no_connect",
)
ERC_PIN_MAP_SIZE = 12
# erc.pin_map / DRC severity ints: 0 = OK, 1 = warning, 2 = error (KiCad
# PIN_ERROR). 3 is tolerated on read (KiCad's "unconnected" sentinel).
ERC_PIN_MAP_LEVELS = {0: "ok", 1: "warning", 2: "error"}


def _opt_float(value):
    """float(value) for a real number, else None.

    Distinguishes 'key absent / non-numeric' (None) from a genuine ``0.0`` so
    the masked-missing-key problem is avoided: load never turns an absent key
    into a manufactured default. ``bool`` is rejected (it is an ``int``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _clean_mm(value: float) -> float:
    """Normalise a millimetre value to KiCad's nanometre resolution (1e-6 mm)
    without ever routing it through the mils grid. ``0.2`` stays ``0.2`` — no
    ``0.2007`` drift — because we only strip float noise beyond 6 decimals."""
    return round(float(value), 6)


@dataclass
class ViaDimension:
    """One row of the predefined via table (board.design_settings.via_dimensions).
    Both values are millimetres (KiCad-native)."""
    diameter: float = 0.0
    drill: float = 0.0

    def to_kicad_dict(self) -> dict:
        return {"diameter": _clean_mm(self.diameter), "drill": _clean_mm(self.drill)}


@dataclass
class DiffPairDimension:
    """One row of the predefined differential-pair table
    (board.design_settings.diff_pair_dimensions). All values millimetres."""
    width: float = 0.0
    gap: float = 0.0
    via_gap: float = 0.0

    def to_kicad_dict(self) -> dict:
        # KiCad stores the keys as gap/via_gap/width — order is irrelevant in
        # JSON but the key names must match exactly.
        return {
            "gap": _clean_mm(self.gap),
            "via_gap": _clean_mm(self.via_gap),
            "width": _clean_mm(self.width),
        }


@dataclass
class DefaultNetClassSettings:
    """Editable routing values for the *Default* net class
    (net_settings.classes[name=="Default"]) — the entry NetClassManager.load
    skips, so it was previously uneditable. All values are millimetres; ``None``
    means 'not managed', so a save leaves that key exactly as KiCad wrote it."""
    clearance: Optional[float] = None
    track_width: Optional[float] = None
    via_diameter: Optional[float] = None
    via_drill: Optional[float] = None
    microvia_diameter: Optional[float] = None
    microvia_drill: Optional[float] = None

    def managed_items(self):
        """(kicad_key, mm_value) pairs for every field that is not None."""
        for key in ("clearance", "track_width", "via_diameter", "via_drill",
                    "microvia_diameter", "microvia_drill"):
            val = getattr(self, key)
            if val is not None:
                yield key, float(val)

    def is_managed(self) -> bool:
        return any(True for _ in self.managed_items())


# ═══════════════════════════════════════════════════════════════════
# PROJECT SETTINGS MANAGER
# ═══════════════════════════════════════════════════════════════════
class ProjectSettingsManager:
    """Manages project settings across KiCad projects"""

    def __init__(self):
        self.settings = ProjectSettings()

        # ── Extended (mm-native, preserve-by-default) managed state ──────────
        # All start empty / None meaning "not managed": save_to_project and
        # save_extended write a structure ONLY when it holds managed values, so
        # a fresh manager never manufactures defaults into a project file.
        self.drc_severities: Dict[str, str] = {}          # rule_id -> level
        self.erc_severities: Dict[str, str] = {}          # rule_id -> level
        self.text_variables: Dict[str, str] = {}          # {VAR} -> value
        self.track_widths: List[float] = []               # mm (KiCad-native)
        self.via_dimensions: List[ViaDimension] = []      # mm
        self.diff_pair_dimensions: List[DiffPairDimension] = []  # mm
        self.erc_pin_map: List[List[int]] = []            # 12x12 severity ints
        self.erc_exclusions: List[str] = []               # KiCad-serialised strings
        self.default_netclass = DefaultNetClassSettings()  # editable Default class

        # Masked-missing-key fix: record which extended structures were actually
        # present in the loaded file (vs. absent). Lets callers tell a genuine
        # empty list/default from "key was never there", and keeps verify honest.
        self._present: set = set()

    def check_project_locked(self, project_file: Path) -> bool:
        """Check if the project is currently open in KiCad.

        KiCad creates a `.lck` next to whichever file is open — usually the board
        and/or schematic (`Master.kicad_pcb.lck`, `Master.kicad_sch.lck`), not only
        the project. Match the EXACT sibling lock filename for this project: a
        substring test lets stem `Main` match an unrelated `Main_v2.kicad_pcb.lck`
        and wrongly block a legit sync."""
        p = Path(project_file)
        stem = p.stem
        lock_names = {
            f"{stem}.kicad_pro.lck",
            f"{stem}.kicad_pcb.lck",
            f"{stem}.kicad_sch.lck",
            f"{stem}.lck",
        }
        try:
            for lck in p.parent.glob("*.lck"):
                if lck.name in lock_names:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _find_default_netclass(data: dict) -> dict:
        """Return the existing 'Default' entry in net_settings.classes (read-only),
        or {} if absent. KiCad stores the design's default via size/drill here —
        board.design_settings.via_diameter/via_drill are NOT consumed by KiCad."""
        classes = data.get("net_settings", {}).get("classes", [])
        if isinstance(classes, list):
            for cls in classes:
                if isinstance(cls, dict) and cls.get("name") == "Default":
                    return cls
        return {}

    @staticmethod
    def _ensure_default_netclass(data: dict) -> dict:
        """Find or create the 'Default' entry in net_settings.classes and return a
        live reference to it (so callers can mutate it in place). Default is kept
        first in the list, matching KiCad / NetClassManager convention."""
        ns = data.setdefault("net_settings", {})
        classes = ns.get("classes")
        if not isinstance(classes, list):
            classes = []
            ns["classes"] = classes
        for cls in classes:
            if isinstance(cls, dict) and cls.get("name") == "Default":
                return cls
        default_cls = {"name": "Default"}
        classes.insert(0, default_cls)
        return default_cls

    def load_from_project(self, project_file: Path) -> bool:
        """Load settings from a .kicad_pro file (converts mm to mils)"""
        try:
            data = json.loads(project_file.read_text(encoding='utf-8'))

            # ═══ SCHEMATIC SETTINGS ═══
            sch_drawing = data.get("schematic", {}).get("drawing", {})

            # schematic.drawing values are stored as RAW MILS in .kicad_pro — no conversion needed.
            # (board.design_settings values are mm and do need conversion further below.)

            # Text box default size (raw mils, e.g. 50.0 = 1.27 mm = KiCad standard)
            self.settings.schematic_text_size = sch_drawing.get("default_text_size", 50.0)

            # Line thickness (raw mils, e.g. 6.0 mils = 0.1524 mm)
            self.settings.schematic_line_width = sch_drawing.get("default_line_thickness", 6.0)

            # Pin symbol size (raw mils, e.g. 25.0 mils)
            self.settings.pin_symbol_size = sch_drawing.get("pin_symbol_size", 25.0)

            # Junction dot size: KiCad key is junction_size_choice (enum index 0-4),
            # NOT default_junction_size (which KiCad ignores). Default choice is 3.
            self.settings.junction_size = int(sch_drawing.get("junction_size_choice", 3))

            # ═══ PCB SETTINGS ═══
            pcb_defaults = data.get("board", {}).get("design_settings", {}).get("defaults", {})

            # PCB text boxes (generic/"other" user-placed text). KiCad's generic PCB
            # text defaults are other_text_size_h/v / other_text_thickness — the bare
            # text_size_h/text_thickness keys are not read by KiCad.
            pcb_text_h = pcb_defaults.get("other_text_size_h", 1.016)
            pcb_text_thick = pcb_defaults.get("other_text_thickness", 0.1524)
            self.settings.pcb_text_size = round(mm_to_mils(pcb_text_h), 1)
            self.settings.pcb_text_thickness = round(mm_to_mils(pcb_text_thick), 1)

            # Silkscreen (footprint text)
            silk_size_mm = pcb_defaults.get("silk_text_size_h", 1.0)
            silk_thick_mm = pcb_defaults.get("silk_text_thickness", 0.1)
            self.settings.silk_text_size = round(mm_to_mils(silk_size_mm), 1)
            self.settings.silk_text_thickness = round(mm_to_mils(silk_thick_mm), 1)

            # Copper text (footprint copper)
            copper_size_mm = pcb_defaults.get("copper_text_size_h", 1.524)
            copper_thick_mm = pcb_defaults.get("copper_text_thickness", 0.3048)
            self.settings.copper_text_size = round(mm_to_mils(copper_size_mm), 1)
            self.settings.copper_text_thickness = round(mm_to_mils(copper_thick_mm), 1)

            # Fab layer (footprint fab)
            fab_size_mm = pcb_defaults.get("fab_text_size_h", 1.0)
            fab_thick_mm = pcb_defaults.get("fab_text_thickness", 0.15)
            self.settings.fab_text_size = round(mm_to_mils(fab_size_mm), 1)
            self.settings.fab_text_thickness = round(mm_to_mils(fab_thick_mm), 1)

            # Design rules
            rules = data.get("board", {}).get("design_settings", {}).get("rules", {})
            self.settings.default_clearance = round(mm_to_mils(rules.get("min_clearance", 0.2)), 1)
            self.settings.default_track_width = round(mm_to_mils(rules.get("min_track_width", 0.254)), 1)

            # Minimum constraints (Board Setup -> Constraints)
            self.settings.min_via_diameter = round(mm_to_mils(rules.get("min_via_diameter", 0.5)), 1)
            self.settings.min_via_annular_width = round(mm_to_mils(rules.get("min_via_annular_width", 0.127)), 1)
            self.settings.min_through_hole = round(mm_to_mils(rules.get("min_through_hole_diameter", 0.3)), 1)
            self.settings.min_hole_to_hole = round(mm_to_mils(rules.get("min_hole_to_hole", 0.25)), 1)
            self.settings.min_hole_clearance = round(mm_to_mils(rules.get("min_hole_clearance", 0.25)), 1)
            self.settings.min_microvia_diameter = round(mm_to_mils(rules.get("min_microvia_diameter", 0.2)), 1)
            self.settings.min_microvia_drill = round(mm_to_mils(rules.get("min_microvia_drill", 0.1)), 1)
            self.settings.min_copper_edge_clearance = round(mm_to_mils(rules.get("min_copper_edge_clearance", 0.5)), 1)
            self.settings.min_silk_clearance = round(mm_to_mils(rules.get("min_silk_clearance", 0.0)), 1)

            # Via settings: the design's default via size/drill live in the
            # 'Default' net class (net_settings.classes), which is what KiCad reads.
            # design_settings.via_diameter/via_drill are dead keys — do NOT read them.
            default_nc = self._find_default_netclass(data)
            self.settings.default_via_diameter = round(mm_to_mils(default_nc.get("via_diameter", 0.8)), 1)
            self.settings.default_via_drill = round(mm_to_mils(default_nc.get("via_drill", 0.4)), 1)

            # Solder mask/paste — NOTE: these physically live in the .kicad_pcb
            # (setup) block, not .kicad_pro; KiCad ignores them here. Retained only
            # for this tool's own round-trip. See report (skipped: needs .kicad_pcb).
            pcb_settings = data.get("board", {}).get("design_settings", {})
            self.settings.solder_mask_clearance = round(mm_to_mils(pcb_settings.get("solder_mask_clearance", 0.05)), 1)
            self.settings.solder_paste_margin = round(mm_to_mils(pcb_settings.get("solder_paste_margin", -0.05)), 1)

            return True

        except Exception as e:
            print(f"Error loading project {project_file}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def save_to_project(self, project_file: Path, backup: bool = False) -> bool:
        """
        Save settings to a .kicad_pro file (converts mils to mm for KiCad).
        If backup=True, a <file>.kicad_pro.bak copy is written before the atomic
        replace so the change is undoable.
        Automatically clears associated cache files.
        """
        try:
            # Check if locked (warn but continue)
            if self.check_project_locked(project_file):
                print(f"⚠️  {project_file.name} appears to be open (lock file exists)")

            # Load existing project data
            data = json.loads(project_file.read_text(encoding='utf-8'))

            # ═══ UPDATE SCHEMATIC SETTINGS ═══
            if "schematic" not in data:
                data["schematic"] = {}
            if "drawing" not in data["schematic"]:
                data["schematic"]["drawing"] = {}

            sch_drawing = data["schematic"]["drawing"]

            # schematic.drawing values are stored as RAW MILS in .kicad_pro — write directly, no conversion.
            # NOTE: schematic_grid / pcb_grid are NOT written here. KiCad stores grid state in the
            # per-machine .kicad_prl file, not in .kicad_pro. Grid cannot be centrally synced via
            # .kicad_pro; the GUI controls for grid are informational only and do not affect saved files.

            # Text box default size (raw mils — no conversion)
            sch_drawing["default_text_size"] = self.settings.schematic_text_size

            # Line thickness (raw mils — no conversion)
            sch_drawing["default_line_thickness"] = self.settings.schematic_line_width

            # Pin symbol size (raw mils — no conversion)
            sch_drawing["pin_symbol_size"] = self.settings.pin_symbol_size

            # Junction dot size: the key KiCad actually consumes is
            # junction_size_choice, an ENUM index (0-4), not mils — clamp to range.
            sch_drawing["junction_size_choice"] = max(0, min(4, int(round(self.settings.junction_size))))
            # The legacy default_junction_size key is IGNORED by KiCad, but an
            # existing (un-editable) regression test asserts it is written as an int,
            # so we keep emitting it for backward compatibility. It is otherwise dead.
            sch_drawing["default_junction_size"] = int(self.settings.junction_size)

            # ═══ UPDATE PCB SETTINGS ═══
            if "board" not in data:
                data["board"] = {}
            if "design_settings" not in data["board"]:
                data["board"]["design_settings"] = {}

            design = data["board"]["design_settings"]

            if "defaults" not in design:
                design["defaults"] = {}

            # Convert all to mm
            pcb_text_mm = round(mils_to_mm(self.settings.pcb_text_size), 4)
            pcb_text_thick_mm = round(mils_to_mm(self.settings.pcb_text_thickness), 4)
            silk_size_mm = round(mils_to_mm(self.settings.silk_text_size), 4)
            silk_thick_mm = round(mils_to_mm(self.settings.silk_text_thickness), 4)
            copper_size_mm = round(mils_to_mm(self.settings.copper_text_size), 4)
            copper_thick_mm = round(mils_to_mm(self.settings.copper_text_thickness), 4)
            fab_size_mm = round(mils_to_mm(self.settings.fab_text_size), 4)
            fab_thick_mm = round(mils_to_mm(self.settings.fab_text_thickness), 4)

            # PCB text boxes (generic/"other" user-placed text). KiCad's generic PCB
            # text defaults are other_text_size_h/v / other_text_thickness — the bare
            # text_size_h/text_thickness keys are not consumed by KiCad.
            design["defaults"]["other_text_size_h"] = pcb_text_mm
            design["defaults"]["other_text_size_v"] = pcb_text_mm
            design["defaults"]["other_text_thickness"] = pcb_text_thick_mm

            # Silkscreen (footprint text)
            design["defaults"]["silk_text_size_h"] = silk_size_mm
            design["defaults"]["silk_text_size_v"] = silk_size_mm
            design["defaults"]["silk_text_thickness"] = silk_thick_mm

            # Copper (footprint text)
            design["defaults"]["copper_text_size_h"] = copper_size_mm
            design["defaults"]["copper_text_size_v"] = copper_size_mm
            design["defaults"]["copper_text_thickness"] = copper_thick_mm

            # Fab layer (footprint text)
            design["defaults"]["fab_text_size_h"] = fab_size_mm
            design["defaults"]["fab_text_size_v"] = fab_size_mm
            design["defaults"]["fab_text_thickness"] = fab_thick_mm

            # Design rules (convert mils to mm)
            if "rules" not in design:
                design["rules"] = {}

            # NOTE: default_clearance/default_track_width map to the DRC MINIMUMS
            # (rules.min_clearance / rules.min_track_width), not per-net routing
            # defaults. The GUI still labels them "Design Rules (Defaults)"; that
            # label lives in kicad_tools.py and is out of scope for this file.
            design["rules"]["min_clearance"] = round(mils_to_mm(self.settings.default_clearance), 4)
            design["rules"]["min_track_width"] = round(mils_to_mm(self.settings.default_track_width), 4)
            design["rules"]["min_via_diameter"] = round(mils_to_mm(self.settings.min_via_diameter), 4)
            design["rules"]["min_via_annular_width"] = round(mils_to_mm(self.settings.min_via_annular_width), 4)
            design["rules"]["min_through_hole_diameter"] = round(mils_to_mm(self.settings.min_through_hole), 4)
            design["rules"]["min_hole_to_hole"] = round(mils_to_mm(self.settings.min_hole_to_hole), 4)
            design["rules"]["min_hole_clearance"] = round(mils_to_mm(self.settings.min_hole_clearance), 4)
            design["rules"]["min_microvia_diameter"] = round(mils_to_mm(self.settings.min_microvia_diameter), 4)
            design["rules"]["min_microvia_drill"] = round(mils_to_mm(self.settings.min_microvia_drill), 4)
            design["rules"]["min_copper_edge_clearance"] = round(mils_to_mm(self.settings.min_copper_edge_clearance), 4)
            design["rules"]["min_silk_clearance"] = round(mils_to_mm(self.settings.min_silk_clearance), 4)

            # Via settings: write the design's default via size/drill into the
            # 'Default' net class (net_settings.classes) — the ONLY place KiCad
            # reads them from. board.design_settings.via_diameter/via_drill are dead
            # keys, so we no longer write them (that produced deceptive "verified").
            default_nc = self._ensure_default_netclass(data)
            default_nc["via_diameter"] = round(mils_to_mm(self.settings.default_via_diameter), 4)
            default_nc["via_drill"] = round(mils_to_mm(self.settings.default_via_drill), 4)

            # Solder mask/paste: these physically belong to the .kicad_pcb (setup)
            # block, NOT .kicad_pro — KiCad ignores them here. Written only so this
            # tool round-trips its own value; never verified. (See report: skipped.)
            design["solder_mask_clearance"] = round(mils_to_mm(self.settings.solder_mask_clearance), 4)
            design["solder_paste_margin"] = round(mils_to_mm(self.settings.solder_paste_margin), 4)

            # ═══ EXTENDED COVERAGE (DRC/ERC severities, size tables, text vars,
            # editable Default net class) — deep-merged, preserve-by-default. A
            # no-op unless the caller populated the extended managed state, so
            # backward-compatible for callers that only set the flat settings. ═══
            self._apply_extended(data)

            # Optional backup: copy the ORIGINAL project file to <file>.kicad_pro.bak
            # BEFORE the destructive atomic replace, so the sync is undoable. The GUI
            # promises "A .bak is written next to each" and passes backup=True.
            if backup and project_file.exists():
                try:
                    backup_path = project_file.parent / (project_file.name + '.bak')
                    shutil.copy2(str(project_file), str(backup_path))
                except Exception as e:
                    print(f"⚠️  Could not write backup for {project_file.name}: {e}")

            # Atomic write: write to a temp file in the same directory, then os.replace()
            # to swap it in atomically (avoids partial-write data loss on crash/interrupt).
            json_content = json.dumps(data, indent=2)
            tmp_path = project_file.parent / (project_file.stem + '.kicad_pro.tmp')
            try:
                tmp_path.write_text(json_content, encoding='utf-8')
                os.replace(str(tmp_path), str(project_file))
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            # ═══ CLEAR CACHE FILES AUTOMATICALLY ═══
            self._clear_project_cache(project_file)

            return True

        except Exception as e:
            print(f"❌ Error saving project {project_file}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _clear_project_cache(self, project_file: Path):
        """Clear cache/lock files for a specific project (automatic, no prompt).

        Reuses _clear_local_cache's sibling list so the REAL KiCad locks
        (<stem>.kicad_pcb.lck / .kicad_sch.lck / .kicad_pro.lck), not a bogus
        <stem>.lck, are actually removed — plus the fp-info-cache."""
        self._clear_local_cache(project_file)

        # Remove fp-info-cache in same directory
        fp_cache = Path(project_file).parent / "fp-info-cache"
        if fp_cache.exists():
            try:
                fp_cache.unlink()
            except Exception:
                pass

    def export_template(self, template_file: Path):
        """Export settings to a JSON template"""
        template = {
            "version": "1.0.0",
            "units": "mils",
            "description": "KiCad project settings template - all measurements in mils",
            "settings": self.settings.to_dict()
        }
        template_file.write_text(json.dumps(template, indent=2))
        print(f"✅ Exported template to {template_file}")

    def import_template(self, template_file: Path):
        """Import settings from a JSON template"""
        data = json.loads(template_file.read_text())
        settings_data = data.get("settings", {})
        self.settings = ProjectSettings.from_dict(settings_data)
        print(f"✅ Imported template from {template_file}")
        print(f"\n{self.settings}")

    def _verify_saved(self, project_file: Path):
        """Re-read the project file and confirm the intended settings actually landed.
        Returns (ok: bool, mismatches: List[str]). This is what makes sync honest:
        a write is only 'success' if the file re-reads with the values we meant to set."""
        mismatches = []
        try:
            data = json.loads(Path(project_file).read_text(encoding='utf-8'))
        except Exception as e:
            return False, [f"re-read failed: {e}"]

        def near(a, b, tol):
            try:
                return abs(float(a) - float(b)) <= tol
            except Exception:
                return False

        # Schematic drawing values are stored as raw mils
        sch = data.get("schematic", {}).get("drawing", {})
        for key, want in (
            ("default_text_size", self.settings.schematic_text_size),
            ("default_line_thickness", self.settings.schematic_line_width),
            ("pin_symbol_size", self.settings.pin_symbol_size),
        ):
            if not near(sch.get(key), want, 0.01):
                mismatches.append(f"schematic.{key}={sch.get(key)} (wanted {want})")

        # Board values are stored in mm (we set them from mils)
        design = data.get("board", {}).get("design_settings", {})
        rules = design.get("rules", {})
        if not near(rules.get("min_track_width"), mils_to_mm(self.settings.default_track_width), 0.001):
            mismatches.append(f"rules.min_track_width={rules.get('min_track_width')} "
                              f"(wanted {round(mils_to_mm(self.settings.default_track_width), 4)})")
        if not near(rules.get("min_clearance"), mils_to_mm(self.settings.default_clearance), 0.001):
            mismatches.append(f"rules.min_clearance={rules.get('min_clearance')} "
                              f"(wanted {round(mils_to_mm(self.settings.default_clearance), 4)})")

        # Via default lives in the 'Default' net class — the key KiCad reads. Verify
        # THERE, never against the dead design_settings.via_diameter (which would
        # report "verified" for a value KiCad ignores).
        default_nc = self._find_default_netclass(data)
        if not near(default_nc.get("via_diameter"), mils_to_mm(self.settings.default_via_diameter), 0.001):
            mismatches.append(f"Default.via_diameter={default_nc.get('via_diameter')} "
                              f"(wanted {round(mils_to_mm(self.settings.default_via_diameter), 4)})")
        if not near(default_nc.get("via_drill"), mils_to_mm(self.settings.default_via_drill), 0.001):
            mismatches.append(f"Default.via_drill={default_nc.get('via_drill')} "
                              f"(wanted {round(mils_to_mm(self.settings.default_via_drill), 4)})")

        # Full-field verify: also confirm every EXTENDED managed value landed on
        # the key KiCad actually reads. No-op (adds nothing) when the extended
        # state is empty, so the existing flat-settings verify is unchanged.
        mismatches.extend(self._collect_extended_mismatches(data))
        return (len(mismatches) == 0), mismatches

    # ═══════════════════════════════════════════════════════════════════
    # EXTENDED COVERAGE — public API (mm-native, preserve-by-default)
    # ═══════════════════════════════════════════════════════════════════
    def load_extended(self, project_file: Path) -> bool:
        """Read the extended Board/Schematic-Setup coverage from a .kicad_pro:
        DRC + ERC rule severities, ERC pin-conflict matrix + exclusions, project
        text variables, the predefined track-width / via / diff-pair tables, and
        the editable Default net class. Values are kept mm-native (no mils grid).

        Preserve-by-default / masked-missing-key: only keys actually present are
        captured, and ``self._present`` records which top-level structures
        existed so an absent key is never mistaken for a manufactured default."""
        try:
            data = json.loads(Path(project_file).read_text(encoding='utf-8'))
        except Exception as e:
            print(f"Error loading extended settings {project_file}: {e}")
            return False

        self._present = set()
        ds = data.get("board", {}).get("design_settings", {})
        erc = data.get("erc", {})

        # DRC severities — capture only curated IDs that are actually present.
        self.drc_severities = {}
        rs = ds.get("rule_severities")
        if isinstance(rs, dict):
            self._present.add("board.rule_severities")
            for rid in DRC_RULE_IDS:
                val = rs.get(rid)
                if isinstance(val, str):
                    self.drc_severities[rid] = val

        # ERC severities — same treatment.
        self.erc_severities = {}
        ers = erc.get("rule_severities")
        if isinstance(ers, dict):
            self._present.add("erc.rule_severities")
            for rid in ERC_RULE_IDS:
                val = ers.get(rid)
                if isinstance(val, str):
                    self.erc_severities[rid] = val

        # ERC pin-conflict matrix (12x12 severity ints).
        self.erc_pin_map = []
        pm = erc.get("pin_map")
        if isinstance(pm, list) and pm and all(isinstance(r, list) for r in pm):
            self._present.add("erc.pin_map")
            self.erc_pin_map = [[int(x) for x in row] for row in pm]

        # ERC exclusions (opaque KiCad-serialised strings — preserved verbatim).
        self.erc_exclusions = []
        ex = erc.get("erc_exclusions")
        if isinstance(ex, list):
            self._present.add("erc.erc_exclusions")
            self.erc_exclusions = [x for x in ex if isinstance(x, str)]

        # Project text variables ({VAR} map at the .kicad_pro top level).
        self.text_variables = {}
        tv = data.get("text_variables")
        if isinstance(tv, dict):
            self._present.add("text_variables")
            self.text_variables = {str(k): v for k, v in tv.items()}

        # Predefined size tables (all mm-native).
        self.track_widths = []
        tw = ds.get("track_widths")
        if isinstance(tw, list):
            self._present.add("track_widths")
            self.track_widths = [float(x) for x in tw
                                 if isinstance(x, (int, float)) and not isinstance(x, bool)]

        self.via_dimensions = []
        vd = ds.get("via_dimensions")
        if isinstance(vd, list):
            self._present.add("via_dimensions")
            for v in vd:
                if isinstance(v, dict):
                    self.via_dimensions.append(ViaDimension(
                        float(v.get("diameter", 0.0) or 0.0),
                        float(v.get("drill", 0.0) or 0.0)))

        self.diff_pair_dimensions = []
        dp = ds.get("diff_pair_dimensions")
        if isinstance(dp, list):
            self._present.add("diff_pair_dimensions")
            for v in dp:
                if isinstance(v, dict):
                    self.diff_pair_dimensions.append(DiffPairDimension(
                        float(v.get("width", 0.0) or 0.0),
                        float(v.get("gap", 0.0) or 0.0),
                        float(v.get("via_gap", 0.0) or 0.0)))

        # Editable Default net class — Optional/None fields distinguish an absent
        # key from a genuine 0.0 value.
        dnc = self._find_default_netclass(data)
        if dnc:
            self._present.add("default_netclass")
        self.default_netclass = DefaultNetClassSettings(
            clearance=_opt_float(dnc.get("clearance")),
            track_width=_opt_float(dnc.get("track_width")),
            via_diameter=_opt_float(dnc.get("via_diameter")),
            via_drill=_opt_float(dnc.get("via_drill")),
            microvia_diameter=_opt_float(dnc.get("microvia_diameter")),
            microvia_drill=_opt_float(dnc.get("microvia_drill")),
        )
        return True

    def was_present(self, structure: str) -> bool:
        """True if `structure` (e.g. 'text_variables', 'board.rule_severities',
        'via_dimensions', 'default_netclass') existed in the file at load time.
        Lets a UI show 'inherited/absent' distinctly from a managed empty value."""
        return structure in self._present

    # ── mutators the UI calls (all validate against KiCad-real values) ───────
    def set_drc_severity(self, rule: str, level: str):
        """Manage one board DRC rule severity. `level` in error|warning|ignore."""
        if level not in SEVERITY_LEVELS:
            raise ValueError(f"severity must be one of {SEVERITY_LEVELS}, got {level!r}")
        if rule not in DRC_RULE_IDS:
            raise ValueError(f"unknown DRC rule id {rule!r}")
        self.drc_severities[rule] = level

    def set_erc_severity(self, rule: str, level: str):
        """Manage one ERC rule severity. `level` in error|warning|ignore."""
        if level not in SEVERITY_LEVELS:
            raise ValueError(f"severity must be one of {SEVERITY_LEVELS}, got {level!r}")
        if rule not in ERC_RULE_IDS:
            raise ValueError(f"unknown ERC rule id {rule!r}")
        self.erc_severities[rule] = level

    def set_text_variable(self, name: str, value: str):
        """Set/replace one project text variable ({name} -> value)."""
        self.text_variables[str(name)] = str(value)

    def remove_text_variable(self, name: str):
        """Remove a managed text variable (note: save deep-merges, so this only
        drops it from what THIS manager writes; a key already in the file is not
        deleted unless you also clear it in the file)."""
        self.text_variables.pop(str(name), None)

    def set_default_netclass(self, clearance=None, track_width=None,
                             via_diameter=None, via_drill=None,
                             microvia_diameter=None, microvia_drill=None):
        """Edit the Default net class (millimetres). Any argument left None keeps
        that field unmanaged (KiCad's existing value is preserved on save)."""
        d = self.default_netclass
        if clearance is not None:
            d.clearance = float(clearance)
        if track_width is not None:
            d.track_width = float(track_width)
        if via_diameter is not None:
            d.via_diameter = float(via_diameter)
        if via_drill is not None:
            d.via_drill = float(via_drill)
        if microvia_diameter is not None:
            d.microvia_diameter = float(microvia_diameter)
        if microvia_drill is not None:
            d.microvia_drill = float(microvia_drill)

    def set_track_widths(self, widths_mm, ensure_netclass_default: bool = True):
        """Replace the predefined track-width table (millimetres). KiCad keeps a
        leading 0.0 meaning 'use the net-class width'; ensure_netclass_default
        prepends it when missing."""
        vals = [float(w) for w in widths_mm]
        if ensure_netclass_default and (not vals or vals[0] != 0.0):
            vals = [0.0] + [v for v in vals if v != 0.0]
        self.track_widths = vals

    def set_via_dimensions(self, vias, ensure_netclass_default: bool = True):
        """Replace the predefined via table. `vias` items are ViaDimension or
        (diameter_mm, drill_mm) tuples. A leading all-zero row (='use net class')
        is prepended when ensure_netclass_default and absent."""
        out = []
        for v in vias:
            if isinstance(v, ViaDimension):
                out.append(ViaDimension(v.diameter, v.drill))
            else:
                d, dr = v
                out.append(ViaDimension(float(d), float(dr)))
        if ensure_netclass_default and not any(x.diameter == 0.0 and x.drill == 0.0 for x in out):
            out.insert(0, ViaDimension(0.0, 0.0))
        self.via_dimensions = out

    def set_diff_pair_dimensions(self, pairs, ensure_netclass_default: bool = True):
        """Replace the predefined diff-pair table. `pairs` items are
        DiffPairDimension or (width_mm, gap_mm, via_gap_mm) tuples."""
        out = []
        for p in pairs:
            if isinstance(p, DiffPairDimension):
                out.append(DiffPairDimension(p.width, p.gap, p.via_gap))
            else:
                w, g, vg = p
                out.append(DiffPairDimension(float(w), float(g), float(vg)))
        if ensure_netclass_default and not any(
                x.width == 0.0 and x.gap == 0.0 and x.via_gap == 0.0 for x in out):
            out.insert(0, DiffPairDimension(0.0, 0.0, 0.0))
        self.diff_pair_dimensions = out

    def ensure_erc_pin_map(self):
        """Seed a 12x12 all-OK ERC pin matrix if none is managed yet, so a UI can
        edit individual cells. Returns the live matrix."""
        if not self.erc_pin_map:
            self.erc_pin_map = [[0] * ERC_PIN_MAP_SIZE for _ in range(ERC_PIN_MAP_SIZE)]
        return self.erc_pin_map

    def set_erc_pin_map_entry(self, i: int, j: int, severity: int,
                              symmetric: bool = True):
        """Set one ERC pin-conflict cell (0=OK, 1=warning, 2=error). The matrix
        is conceptually symmetric in KiCad, so both [i][j] and [j][i] are set
        unless symmetric=False."""
        if not (0 <= i < ERC_PIN_MAP_SIZE and 0 <= j < ERC_PIN_MAP_SIZE):
            raise ValueError(f"pin index out of range 0..{ERC_PIN_MAP_SIZE - 1}")
        if not (0 <= int(severity) <= 3):
            raise ValueError("severity must be 0=OK, 1=warning, 2=error")
        self.ensure_erc_pin_map()
        self.erc_pin_map[i][j] = int(severity)
        if symmetric:
            self.erc_pin_map[j][i] = int(severity)

    def set_erc_exclusions(self, exclusions):
        """Manage the ERC exclusion list (KiCad-serialised strings, preserved
        verbatim — the tool does not synthesise exclusion strings)."""
        self.erc_exclusions = [str(x) for x in exclusions]

    # ── save / apply / verify ────────────────────────────────────────────────
    def _apply_extended(self, data: dict):
        """Deep-merge the extended managed state into `data` (a parsed .kicad_pro).
        Preserve-by-default: only structures that hold managed values are touched,
        and within a severity map only the managed rule IDs are updated — every
        other key KiCad wrote is left exactly as-is."""
        # Board DRC severities.
        if self.drc_severities:
            ds = data.setdefault("board", {}).setdefault("design_settings", {})
            rs = ds.setdefault("rule_severities", {})
            for rid, level in self.drc_severities.items():
                rs[rid] = level

        # ERC severities.
        if self.erc_severities:
            ers = data.setdefault("erc", {}).setdefault("rule_severities", {})
            for rid, level in self.erc_severities.items():
                ers[rid] = level

        # ERC pin-conflict matrix (whole matrix replaced when managed).
        if self.erc_pin_map:
            data.setdefault("erc", {})["pin_map"] = [
                [int(x) for x in row] for row in self.erc_pin_map]

        # ERC exclusions: only rewrite if we hold values or the key was loaded
        # (so we round-trip an existing list without clobbering an absent one).
        if self.erc_exclusions or ("erc.erc_exclusions" in self._present):
            data.setdefault("erc", {})["erc_exclusions"] = list(self.erc_exclusions)

        # Project text variables.
        if self.text_variables:
            tv = data.setdefault("text_variables", {})
            for k, v in self.text_variables.items():
                tv[k] = v

        # Predefined size tables (mm-native — written through _clean_mm, never
        # the mils grid, so 0.2 stays 0.2).
        ds_for_tables = None
        if self.track_widths or ("track_widths" in self._present):
            ds_for_tables = data.setdefault("board", {}).setdefault("design_settings", {})
            ds_for_tables["track_widths"] = [_clean_mm(w) for w in self.track_widths]
        if self.via_dimensions or ("via_dimensions" in self._present):
            ds_for_tables = ds_for_tables or data.setdefault("board", {}).setdefault("design_settings", {})
            ds_for_tables["via_dimensions"] = [v.to_kicad_dict() for v in self.via_dimensions]
        if self.diff_pair_dimensions or ("diff_pair_dimensions" in self._present):
            ds_for_tables = ds_for_tables or data.setdefault("board", {}).setdefault("design_settings", {})
            ds_for_tables["diff_pair_dimensions"] = [
                d.to_kicad_dict() for d in self.diff_pair_dimensions]

        # Editable Default net class — skip-if-unchanged so a value that already
        # round-trips to the same mm is NOT rewritten (no drift, no churn).
        if self.default_netclass.is_managed():
            default_nc = self._ensure_default_netclass(data)
            for key, want_mm in self.default_netclass.managed_items():
                self._set_mm_if_changed(default_nc, key, want_mm)

    @staticmethod
    def _mm_equal(a, b, tol: float = 1e-9) -> bool:
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return False

    def _set_mm_if_changed(self, container: dict, key: str, want_mm: float,
                           tol: float = 1e-9) -> bool:
        """Write `want_mm` into container[key] only if it differs from what is
        already there (native-mm compare). Returns True if it wrote. Skipping an
        unchanged value avoids the 0.1-mil-grid drift the audit flagged and
        avoids rewriting a byte-identical value on a no-op sync."""
        if key in container and self._mm_equal(container.get(key), want_mm, tol):
            return False
        container[key] = _clean_mm(want_mm)
        return True

    def save_extended(self, project_file: Path, backup: bool = False) -> bool:
        """Write ONLY the extended managed state to a .kicad_pro (deep-merged,
        preserve-by-default, atomic). Use this for a pure DRC/ERC/size-table/
        text-var/Default-class sync without touching the flat drawing settings.
        (save_to_project also applies the extended state, for a combined sync.)"""
        try:
            p = Path(project_file)
            if self.check_project_locked(p):
                print(f"⚠️  {p.name} appears to be open (lock file exists)")

            data = json.loads(p.read_text(encoding='utf-8'))
            self._apply_extended(data)

            if backup and p.exists():
                try:
                    shutil.copy2(str(p), str(p.parent / (p.name + '.bak')))
                except Exception as e:
                    print(f"⚠️  Could not write backup for {p.name}: {e}")

            json_content = json.dumps(data, indent=2)
            tmp_path = p.parent / (p.stem + '.kicad_pro.tmp')
            try:
                tmp_path.write_text(json_content, encoding='utf-8')
                os.replace(str(tmp_path), str(p))
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            self._clear_project_cache(p)
            return True
        except Exception as e:
            print(f"❌ Error saving extended settings {project_file}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _collect_extended_mismatches(self, data: dict) -> List[str]:
        """Return a list of human-readable mismatches for every EXTENDED managed
        value that did NOT land in `data`. Empty when nothing extended is managed
        (so it never perturbs the flat-settings verify)."""
        out: List[str] = []
        ds = data.get("board", {}).get("design_settings", {})
        erc = data.get("erc", {})

        rs = ds.get("rule_severities", {}) if isinstance(ds.get("rule_severities"), dict) else {}
        for rid, level in self.drc_severities.items():
            if rs.get(rid) != level:
                out.append(f"board.rule_severities.{rid}={rs.get(rid)} (wanted {level})")

        ers = erc.get("rule_severities", {}) if isinstance(erc.get("rule_severities"), dict) else {}
        for rid, level in self.erc_severities.items():
            if ers.get(rid) != level:
                out.append(f"erc.rule_severities.{rid}={ers.get(rid)} (wanted {level})")

        if self.erc_pin_map:
            want = [[int(x) for x in row] for row in self.erc_pin_map]
            if erc.get("pin_map") != want:
                out.append("erc.pin_map mismatch")

        if self.erc_exclusions or ("erc.erc_exclusions" in self._present):
            if list(erc.get("erc_exclusions", [])) != list(self.erc_exclusions):
                out.append("erc.erc_exclusions mismatch")

        tv = data.get("text_variables", {})
        tv = tv if isinstance(tv, dict) else {}
        for k, v in self.text_variables.items():
            if tv.get(k) != v:
                out.append(f"text_variables.{k}={tv.get(k)} (wanted {v})")

        if self.track_widths or ("track_widths" in self._present):
            got = ds.get("track_widths", [])
            want = [_clean_mm(w) for w in self.track_widths]
            if not (isinstance(got, list) and len(got) == len(want)
                    and all(self._mm_equal(a, b) for a, b in zip(got, want))):
                out.append(f"track_widths={got} (wanted {want})")

        if self.via_dimensions or ("via_dimensions" in self._present):
            got = ds.get("via_dimensions", [])
            want = [v.to_kicad_dict() for v in self.via_dimensions]
            if not self._via_tables_equal(got, want):
                out.append(f"via_dimensions={got} (wanted {want})")

        if self.diff_pair_dimensions or ("diff_pair_dimensions" in self._present):
            got = ds.get("diff_pair_dimensions", [])
            want = [d.to_kicad_dict() for d in self.diff_pair_dimensions]
            if not self._dp_tables_equal(got, want):
                out.append(f"diff_pair_dimensions={got} (wanted {want})")

        if self.default_netclass.is_managed():
            default_nc = self._find_default_netclass(data)
            for key, want_mm in self.default_netclass.managed_items():
                if not self._mm_equal(default_nc.get(key), want_mm):
                    out.append(f"Default.{key}={default_nc.get(key)} (wanted {_clean_mm(want_mm)})")
        return out

    def _via_tables_equal(self, got, want) -> bool:
        if not isinstance(got, list) or len(got) != len(want):
            return False
        for g, w in zip(got, want):
            if not isinstance(g, dict):
                return False
            if not (self._mm_equal(g.get("diameter"), w["diameter"])
                    and self._mm_equal(g.get("drill"), w["drill"])):
                return False
        return True

    def _dp_tables_equal(self, got, want) -> bool:
        if not isinstance(got, list) or len(got) != len(want):
            return False
        for g, w in zip(got, want):
            if not isinstance(g, dict):
                return False
            if not (self._mm_equal(g.get("width"), w["width"])
                    and self._mm_equal(g.get("gap"), w["gap"])
                    and self._mm_equal(g.get("via_gap"), w["via_gap"])):
                return False
        return True

    def verify_extended(self, project_file: Path):
        """Re-read `project_file` and confirm every extended managed value landed
        on the real KiCad key. Returns (ok, mismatches)."""
        try:
            data = json.loads(Path(project_file).read_text(encoding='utf-8'))
        except Exception as e:
            return False, [f"re-read failed: {e}"]
        mism = self._collect_extended_mismatches(data)
        return (len(mism) == 0), mism

    def _clear_local_cache(self, project_file: Path) -> List[str]:
        """Delete ONLY this project's sibling cache/lock files (.kicad_prl, .lck).
        Bounded: no repo-wide or drive-wide recursive scan."""
        cleared = []
        p = Path(project_file)
        siblings = [
            p.with_suffix(".kicad_prl"),
            p.with_suffix(".lck"),
            p.parent / (p.stem + ".kicad_pro.lck"),
            p.parent / (p.stem + ".kicad_pcb.lck"),
            p.parent / (p.stem + ".kicad_sch.lck"),
        ]
        for sib in siblings:
            try:
                if sib.exists():
                    sib.unlink()
                    cleared.append(sib.name)
            except Exception:
                pass
        return cleared

    def sync_to_projects(self, project_files: List[Path], backup: bool = False,
                         force_open: bool = False) -> Dict[Path, bool]:
        """Sync current settings to multiple projects, VERIFYING each write.

        A project is reported successful ONLY if, after the write, the file
        re-reads with the intended values (no more blind 'success'). Projects that
        are open in KiCad (.lck present) are SKIPPED unless force_open=True, because
        KiCad overwrites .kicad_pro on its next save and the change would silently
        revert. Per-project .kicad_prl is cleared (bounded; no drive scan).

        Per-project explanations are stored in self.last_sync_details so the GUI/CLI
        can show exactly why something did or did not apply."""
        results: Dict[Path, bool] = {}
        self.last_sync_details: Dict[Path, str] = {}

        print(f"\n{'='*60}\n📦 SYNCING SETTINGS TO {len(project_files)} PROJECTS\n{'='*60}\n")
        for i, project_file in enumerate(project_files, 1):
            project_file = Path(project_file)
            print(f"[{i}/{len(project_files)}] {project_file.name}...", end=" ")

            if not project_file.exists():
                results[project_file] = False
                self.last_sync_details[project_file] = "missing file"
                print("❌ missing")
                continue

            if self.check_project_locked(project_file) and not force_open:
                results[project_file] = False
                self.last_sync_details[project_file] = ("SKIPPED: open in KiCad (.lck present). Close it and "
                                                        "re-sync — KiCad would overwrite the change otherwise.")
                print("⏭️  skipped (open)")
                continue

            if not self.save_to_project(project_file, backup=backup):
                results[project_file] = False
                self.last_sync_details[project_file] = "write failed"
                print("❌ write failed")
                continue

            ok, mismatches = self._verify_saved(project_file)
            results[project_file] = ok
            if ok:
                self.last_sync_details[project_file] = "verified"
                self._clear_local_cache(project_file)
                print("✅ verified")
            else:
                self.last_sync_details[project_file] = "NOT applied: " + "; ".join(mismatches)
                print("❌ not verified")

        success_count = sum(1 for r in results.values() if r)
        print(f"\n📊 SYNC SUMMARY: {success_count}/{len(results)} verified")
        print("🔄 Restart KiCad (and close it before syncing) to see changes.\n")
        return results

# ═══════════════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════════════
def main_cli():
    """CLI interface for project settings manager"""
    import argparse

    parser = argparse.ArgumentParser(
        description="KiCad Project Settings Manager - all units in mils"
    )
    parser.add_argument("--export-template", help="Export settings to template file")
    parser.add_argument("--import-template", help="Import settings from template file")
    parser.add_argument("--sync-to", nargs="+", help="Sync to project files")
    parser.add_argument("--load-from", help="Load from project file")
    parser.add_argument("--clear-cache", help="Clear cache for repository root")

    args = parser.parse_args()

    manager = ProjectSettingsManager()

    if args.clear_cache:
        clear_project_cache_files(Path(args.clear_cache))

    elif args.export_template:
        manager.export_template(Path(args.export_template))

    elif args.import_template:
        manager.import_template(Path(args.import_template))

    elif args.load_from:
        success = manager.load_from_project(Path(args.load_from))
        if success:
            print(f"\n✅ Loaded settings from {args.load_from}")
            print(f"\n{manager.settings}")
        else:
            print("❌ Failed to load project")

    if args.sync_to:
        project_files = [Path(p) for p in args.sync_to]
        results = manager.sync_to_projects(project_files, backup=False)

        # Show failed projects if any
        failed = [proj for proj, success in results.items() if not success]
        if failed:
            print("\n❌ Failed projects:")
            for proj in failed:
                print(f"   • {proj}")

if __name__ == "__main__":
    main_cli()