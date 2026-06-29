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
NO BACKUP FILES - Direct modification only.
Automatically clears cache (.prl, .lck, fp-info-cache).
Completely ignores .history directories.

Supports KiCad v6+ .kicad_pro JSON format.
"""
import json
import os
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
    junction_size: int = 36               # mils - wire junction dots

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
# PROJECT SETTINGS MANAGER
# ═══════════════════════════════════════════════════════════════════
class ProjectSettingsManager:
    """Manages project settings across KiCad projects"""

    def __init__(self):
        self.settings = ProjectSettings()

    def check_project_locked(self, project_file: Path) -> bool:
        """Check if the project is currently open in KiCad.

        KiCad creates a `.lck` next to whichever file is open — usually the board
        and/or schematic (`Master.kicad_pcb.lck`, `Master.kicad_sch.lck`), not only
        the project. Match ANY sibling `.lck` belonging to this project so the
        open-guard actually fires (a write into an open project gets clobbered)."""
        p = Path(project_file)
        try:
            for lck in p.parent.glob("*.lck"):
                if p.stem in lck.name:
                    return True
        except Exception:
            pass
        return p.with_suffix('.lck').exists()

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

            # Junction size (raw mils, e.g. 36 mils)
            self.settings.junction_size = int(sch_drawing.get("default_junction_size", 36))

            # ═══ PCB SETTINGS ═══
            pcb_defaults = data.get("board", {}).get("design_settings", {}).get("defaults", {})

            # PCB text boxes (user-placed text)
            pcb_text_h = pcb_defaults.get("text_size_h", 1.016)
            pcb_text_thick = pcb_defaults.get("text_thickness", 0.1524)
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

            # Via settings
            pcb_settings = data.get("board", {}).get("design_settings", {})
            self.settings.default_via_diameter = round(mm_to_mils(pcb_settings.get("via_diameter", 0.8)), 1)
            self.settings.default_via_drill = round(mm_to_mils(pcb_settings.get("via_drill", 0.4)), 1)

            # Solder mask/paste
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
        NO BACKUP FILES - Direct modification only.
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

            # Junction size (raw mils — no conversion)
            sch_drawing["default_junction_size"] = self.settings.junction_size

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

            # PCB text boxes (user-placed text)
            design["defaults"]["text_size_h"] = pcb_text_mm
            design["defaults"]["text_size_v"] = pcb_text_mm
            design["defaults"]["text_thickness"] = pcb_text_thick_mm

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

            design["rules"]["min_clearance"] = round(mils_to_mm(self.settings.default_clearance), 4)
            design["rules"]["min_track_width"] = round(mils_to_mm(self.settings.default_track_width), 4)

            # Via settings (convert mils to mm)
            design["via_diameter"] = round(mils_to_mm(self.settings.default_via_diameter), 4)
            design["via_drill"] = round(mils_to_mm(self.settings.default_via_drill), 4)

            # Solder mask/paste (convert mils to mm)
            design["solder_mask_clearance"] = round(mils_to_mm(self.settings.solder_mask_clearance), 4)
            design["solder_paste_margin"] = round(mils_to_mm(self.settings.solder_paste_margin), 4)

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
        """Clear cache files for a specific project (automatic, no prompt)"""
        # Remove .prl (project local settings)
        prl_file = project_file.with_suffix('.kicad_prl')
        if prl_file.exists():
            try:
                prl_file.unlink()
            except Exception as e:
                pass

        # Remove .lck (lock file)
        lck_file = project_file.with_suffix('.lck')
        if lck_file.exists():
            try:
                lck_file.unlink()
            except Exception as e:
                pass

        # Remove fp-info-cache in same directory
        fp_cache = project_file.parent / "fp-info-cache"
        if fp_cache.exists():
            try:
                fp_cache.unlink()
            except Exception as e:
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
        if not near(design.get("via_diameter"), mils_to_mm(self.settings.default_via_diameter), 0.001):
            mismatches.append(f"via_diameter={design.get('via_diameter')} "
                              f"(wanted {round(mils_to_mm(self.settings.default_via_diameter), 4)})")
        return (len(mismatches) == 0), mismatches

    def _clear_local_cache(self, project_file: Path) -> List[str]:
        """Delete ONLY this project's sibling cache/lock files (.kicad_prl, .lck).
        Bounded: no repo-wide or drive-wide recursive scan."""
        cleared = []
        p = Path(project_file)
        siblings = [
            p.with_suffix(".kicad_prl"),
            p.with_suffix(".lck"),
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