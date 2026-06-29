#!/usr/bin/env python3
"""
netclass_manager.py — Net Class Manager for KiCad Projects
Manages net classes across multiple KiCad projects:
- Read/write net class definitions from .kicad_pro files
- Synchronize net classes across all projects
- Import/export templates
- Edit colors, widths, clearances, patterns
- Auto-clear KiCad cache files
Supports KiCad v6+ .kicad_pro JSON format.
"""
import json
import logging
import os
import re
from pathlib import Path
import shutil
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from copy import deepcopy

# Version tracking for vault standard
VAULT_STANDARD_VERSION = "1.0.0"

# ═══════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════
def clear_project_cache(repo_root: Path):
    """
    Clear KiCad cache files that prevent settings from updating.

    Clears:
    - All *-cache.lib files (legacy symbol cache)
    - All *-rescue.lib files (rescue cache)
    - All .history/ directories (autosave history)
    - All fp-info-cache files (footprint cache)
    - All sym-lib-table.lock files
    """
    cache_patterns = [
        "*-cache.lib",
        "*-rescue.lib",
        "*-rescue.dcm",
        "fp-info-cache",
        "sym-lib-table.lock",
        "fp-lib-table.lock",
    ]

    cache_dirs = [
        ".history",
    ]

    deleted_count = 0

    # Remove cache files
    for pattern in cache_patterns:
        for cache_file in repo_root.rglob(pattern):
            # Skip files in .git or other hidden directories
            if any(part.startswith('.') and part != '.history' for part in cache_file.parts):
                continue
            try:
                cache_file.unlink()
                deleted_count += 1
                print(f"Deleted: {cache_file.relative_to(repo_root)}")
            except Exception as e:
                print(f"Failed to delete {cache_file}: {e}")

    # Remove cache directories
    for dir_name in cache_dirs:
        for cache_dir in repo_root.rglob(dir_name):
            # Skip .git directories
            if '.git' in cache_dir.parts:
                continue
            try:
                shutil.rmtree(cache_dir)
                deleted_count += 1
                print(f"Deleted: {cache_dir.relative_to(repo_root)}/")
            except Exception as e:
                print(f"Failed to delete {cache_dir}: {e}")

    print(f"\nCleared {deleted_count} cache files/directories")
    return deleted_count

# ═══════════════════════════════════════════════════════════════════
# NET CLASS DATA STRUCTURE
# ═══════════════════════════════════════════════════════════════════
@dataclass
class NetClass:
    """Represents a KiCad net class with all properties"""
    name: str
    # Schematic properties
    color: str = "#808080"  # Hex color
    line_style: str = "solid"  # solid, dashed, dotted, dash_dot
    wire_thickness: float = 0.1524  # mm (6 mil default)
    bus_thickness: float = 0.3048  # mm (12 mil default)

    # PCB properties
    clearance: float = 0.127  # mm
    track_width: float = 0.2  # mm
    via_diameter: float = 0.8  # mm
    via_drill: float = 0.4  # mm

    # Microvia (µVia)
    microvia_diameter: float = 0.3  # mm
    microvia_drill: float = 0.1  # mm

    # Differential pair (optional)
    diff_pair_width: Optional[float] = None
    diff_pair_gap: Optional[float] = None
    diff_pair_via_gap: float = 0.25  # mm

    # Priority (lower = higher precedence; Default uses 2147483647)
    priority: int = 0

    # Net patterns for assignment
    patterns: List[str] = None

    def __post_init__(self):
        if self.patterns is None:
            self.patterns = []

    def to_kicad_dict(self) -> dict:
        """Convert to KiCad .kicad_pro format"""
        result = {
            "name": self.name,
            "clearance": self.clearance,
            "track_width": self.track_width,
            "via_diameter": self.via_diameter,
            "via_drill": self.via_drill,
        }

        # Add differential pair if set
        if self.diff_pair_width is not None:
            result["diff_pair_width"] = self.diff_pair_width
            result["diff_pair_gap"] = self.diff_pair_gap if self.diff_pair_gap else 0.25
        else:
            result["diff_pair_gap"] = 0.25
            result["diff_pair_width"] = 0.2

        # Add diff_pair_via_gap (required in KiCad 10)
        result["diff_pair_via_gap"] = self.diff_pair_via_gap

        # Add microvia settings (required in KiCad 10)
        result["microvia_diameter"] = self.microvia_diameter
        result["microvia_drill"] = self.microvia_drill

        # Add tuning profile (required in KiCad 10)
        result["tuning_profile"] = ""

        # Add priority (lower number = higher priority, Default is max int)
        result["priority"] = self.priority

        # Convert colors
        result["schematic_color"] = self._hex_to_rgba(self.color)
        result["pcb_color"] = self._hex_to_rgba(self.color)

        # CRITICAL FIX: Convert mm to mils (integer)
        # KiCad stores wire/bus widths as integer mils, not float mm
        result["wire_width"] = int(round(self.wire_thickness / 0.0254))  # mm to mils
        result["bus_width"] = int(round(self.bus_thickness / 0.0254))    # mm to mils

        result["line_style"] = self._line_style_to_kicad(self.line_style)

        return result

    @staticmethod
    def from_kicad_dict(name: str, data: dict) -> 'NetClass':
        """Create from KiCad .kicad_pro format"""
        # Convert mils to mm for wire/bus widths
        # Check if values are already in mm (float < 10) or mils (int > 10)
        wire_width_val = data.get("wire_width", 6)
        bus_width_val = data.get("bus_width", 12)

        # If it's a large integer, it's mils; if small float, it's already mm
        if isinstance(wire_width_val, int) and wire_width_val > 2:
            wire_thickness = wire_width_val * 0.0254  # mils to mm
        else:
            wire_thickness = float(wire_width_val)

        if isinstance(bus_width_val, int) and bus_width_val > 2:
            bus_thickness = bus_width_val * 0.0254  # mils to mm
        else:
            bus_thickness = float(bus_width_val)

        return NetClass(
            name=name,
            color=NetClass._rgba_to_hex(data.get("schematic_color", "rgba(128, 128, 128, 1.000)")),
            line_style=NetClass._line_style_from_kicad(data.get("line_style", 0)),
            wire_thickness=wire_thickness,
            bus_thickness=bus_thickness,
            clearance=data.get("clearance", 0.127),
            track_width=data.get("track_width", 0.2),
            via_diameter=data.get("via_diameter", 0.8),
            via_drill=data.get("via_drill", 0.4),
            microvia_diameter=data.get("microvia_diameter", 0.3),
            microvia_drill=data.get("microvia_drill", 0.1),
            diff_pair_width=data.get("diff_pair_width"),
            diff_pair_gap=data.get("diff_pair_gap"),
            diff_pair_via_gap=data.get("diff_pair_via_gap", 0.25),
            priority=data.get("priority", 0),
            patterns=[]
        )

    @staticmethod
    def _hex_to_rgba(hex_color: str) -> str:
        """Convert hex color to KiCad rgba format"""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"rgba({r}, {g}, {b}, 1.000)"

    @staticmethod
    def _rgba_to_hex(rgba: str) -> str:
        """Convert KiCad rgba/rgb to hex color.

        Matches both ``rgba(r, g, b, a)`` (alpha channel) and
        ``rgb(r, g, b)`` (no alpha) which KiCad stores for non-Default classes.
        """
        match = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', rgba)
        if match:
            r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return f"#{r:02X}{g:02X}{b:02X}"
        logging.warning("Unrecognized color format: %r", rgba)
        return "#808080"

    @staticmethod
    def _line_style_to_kicad(style: str) -> int:
        """Convert line style string to KiCad integer"""
        styles = {"solid": 0, "dashed": 1, "dotted": 2, "dash_dot": 3}
        return styles.get(style.lower(), 0)

    @staticmethod
    def _line_style_from_kicad(style_int: int) -> str:
        """Convert KiCad integer to line style string"""
        styles = {0: "solid", 1: "dashed", 2: "dotted", 3: "dash_dot"}
        return styles.get(style_int, "solid")

# ═══════════════════════════════════════════════════════════════════
# NET CLASS MANAGER
# ═══════════════════════════════════════════════════════════════════
class NetClassManager:
    """Manages net classes across KiCad projects"""

    def __init__(self):
        self.net_classes: Dict[str, NetClass] = {}
        self.patterns: Dict[str, List[str]] = {}  # netclass_name -> [patterns]
        # Names of classes that existed in the project file but were not in the
        # managed set; populated after each save_to_project() call.
        self.last_preserved_unmanaged: List[str] = []

    def load_from_project(self, project_file: Path) -> bool:
        """Load net classes from a .kicad_pro file"""
        try:
            data = json.loads(project_file.read_text(encoding='utf-8'))

            # Extract net classes
            net_settings = data.get("net_settings", {})
            classes = net_settings.get("classes", [])  # It's a LIST, not dict!

            # Classes is a list of dicts, each with a "name" key
            for class_data in classes:
                name = class_data.get("name", "")
                if name == "Default":
                    continue  # Skip default class
                self.net_classes[name] = NetClass.from_kicad_dict(name, class_data)

            # Extract patterns
            patterns = net_settings.get("netclass_patterns", [])
            for pattern_entry in patterns:
                netclass = pattern_entry.get("netclass", "")
                pattern = pattern_entry.get("pattern", "")
                if netclass and pattern:
                    if netclass not in self.patterns:
                        self.patterns[netclass] = []
                    self.patterns[netclass].append(pattern)

            # Merge patterns into net classes
            for name, patterns_list in self.patterns.items():
                if name in self.net_classes:
                    self.net_classes[name].patterns = patterns_list

            return True

        except Exception as e:
            print(f"Error loading project {project_file}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def save_to_project(self, project_file: Path, backup: bool = True) -> bool:
        """Save net classes to a .kicad_pro file.

        Safe-merge strategy
        -------------------
        1. ``Default`` class (from the existing file) is always kept first.
        2. All managed classes (``self.net_classes``) replace any same-named
           entries in the file.
        3. Any class already in the file whose name is *not* in the managed
           set (and is not ``Default``) is preserved unchanged at the end of
           the list so that user-created classes are never silently deleted.

        The names of preserved-unmanaged classes are stored in
        ``self.last_preserved_unmanaged`` after the call for the GUI to inspect.

        The write is atomic: JSON is first flushed to a sibling ``.tmp`` file
        in the same directory, then renamed over the target with ``os.replace``
        so a crash mid-write cannot corrupt the project file.
        """
        try:
            # Backup
            if backup:
                backup_path = project_file.with_suffix(project_file.suffix + '.bak')
                shutil.copy2(project_file, backup_path)

            # Load existing project
            data = json.loads(project_file.read_text(encoding='utf-8'))

            # Ensure net_settings exists
            if "net_settings" not in data:
                data["net_settings"] = {}

            if "classes" not in data["net_settings"]:
                data["net_settings"]["classes"] = []

            # Get existing classes list
            existing_classes = data["net_settings"]["classes"]

            # Keep Default class if it exists
            default_class = None
            for cls in existing_classes:
                if cls.get("name") == "Default":
                    default_class = cls
                    break

            # Identify classes in the file that are not managed by us
            managed_names = set(self.net_classes.keys())
            unmanaged_existing = [
                cls for cls in existing_classes
                if cls.get("name") not in managed_names and cls.get("name") != "Default"
            ]
            self.last_preserved_unmanaged = [cls.get("name", "") for cls in unmanaged_existing]

            # Build new classes list (safe merge)
            new_classes = []

            # 1. Default first
            if default_class:
                new_classes.append(default_class)

            # 2. All managed classes
            for name, netclass in self.net_classes.items():
                new_classes.append(netclass.to_kicad_dict())

            # 3. Unmanaged classes from existing file (preserved unchanged)
            new_classes.extend(unmanaged_existing)

            # Replace the classes list
            data["net_settings"]["classes"] = new_classes

            # Update patterns
            patterns_list = []
            for name, netclass in self.net_classes.items():
                for pattern in netclass.patterns:
                    patterns_list.append({
                        "netclass": name,
                        "pattern": pattern
                    })

            data["net_settings"]["netclass_patterns"] = patterns_list

            # Atomic write: temp file in same dir, then os.replace
            tmp_path = project_file.with_suffix(project_file.suffix + '.tmp')
            tmp_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            os.replace(str(tmp_path), str(project_file))
            return True

        except Exception as e:
            print(f"Error saving project {project_file}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def add_netclass(self, netclass: NetClass):
        """Add or update a net class"""
        self.net_classes[netclass.name] = netclass
        if netclass.patterns:
            self.patterns[netclass.name] = netclass.patterns

    def remove_netclass(self, name: str):
        """Remove a net class"""
        if name in self.net_classes:
            del self.net_classes[name]
        if name in self.patterns:
            del self.patterns[name]

    def get_netclass(self, name: str) -> Optional[NetClass]:
        """Get a net class by name"""
        return self.net_classes.get(name)

    def list_netclasses(self) -> List[str]:
        """Get list of all net class names"""
        return sorted(self.net_classes.keys())

    def export_template(self, template_file: Path):
        """Export net classes to a JSON template"""
        template = {
            "version": VAULT_STANDARD_VERSION,
            "netclasses": {}
        }

        for name, netclass in self.net_classes.items():
            template["netclasses"][name] = {
                "color": netclass.color,
                "line_style": netclass.line_style,
                "wire_thickness": netclass.wire_thickness,
                "bus_thickness": netclass.bus_thickness,
                "clearance": netclass.clearance,
                "track_width": netclass.track_width,
                "via_diameter": netclass.via_diameter,
                "via_drill": netclass.via_drill,
                "diff_pair_width": netclass.diff_pair_width,
                "diff_pair_gap": netclass.diff_pair_gap,
                "patterns": netclass.patterns
            }

        template_file.write_text(json.dumps(template, indent=2))

    def import_template(self, template_file: Path):
        """Import net classes from a JSON template"""
        data = json.loads(template_file.read_text())

        for name, nc_data in data.get("netclasses", {}).items():
            netclass = NetClass(
                name=name,
                color=nc_data.get("color", "#808080"),
                line_style=nc_data.get("line_style", "solid"),
                wire_thickness=nc_data.get("wire_thickness", 0.1524),
                bus_thickness=nc_data.get("bus_thickness", 0.3048),
                clearance=nc_data.get("clearance", 0.127),
                track_width=nc_data.get("track_width", 0.2),
                via_diameter=nc_data.get("via_diameter", 0.8),
                via_drill=nc_data.get("via_drill", 0.4),
                diff_pair_width=nc_data.get("diff_pair_width"),
                diff_pair_gap=nc_data.get("diff_pair_gap"),
                patterns=nc_data.get("patterns", [])
            )
            self.add_netclass(netclass)

    def sync_to_projects(self, project_files: List[Path], backup: bool = True) -> Dict[Path, bool]:
        """Sync current net classes to multiple projects"""
        results = {}
        for project_file in project_files:
            success = self.save_to_project(project_file, backup=backup)
            results[project_file] = success
        return results

# ═══════════════════════════════════════════════════════════════════
# PRESET TEMPLATES
# ═══════════════════════════════════════════════════════════════════
def create_vault_standard_template() -> NetClassManager:
    """Create the vault standard net class configuration"""
    manager = NetClassManager()

    # Define all net classes from the specification
    # Define all net classes from the specification
    netclasses = [
        NetClass("GND", "#5E8AC7", "solid", 0.2032, 0.3048, 0.127, 0.4, 0.8, 0.4,
                 patterns=["*GND", "*VSSA_TGT", "*CHASSIS"]),
        NetClass("PWR_IN", "#B03A2E", "solid", 0.3048, 0.3048, 0.20, 0.60, 0.8, 0.4,
                 patterns=["*V_SYS", "*USB_VBUS*", "*CELL_IN*"]),
        NetClass("PWR_5V", "#E07B39", "solid", 0.254, 0.3048, 0.127, 0.50, 0.8, 0.4,
                 patterns=["*+5V"]),
        NetClass("PWR_3V3", "#C99A2E", "solid", 0.254, 0.3048, 0.127, 0.40, 0.8, 0.4,
                 patterns=["*+3V3", "*+3V3_STATUS"]),
        NetClass("PWR_1V8", "#A6B84F", "solid", 0.254, 0.3048, 0.127, 0.40, 0.8, 0.4,
                 patterns=["*+1V8"]),
        NetClass("TGT_PWR", "#C56FAE", "solid", 0.254, 0.3048, 0.127, 0.50, 0.8, 0.4,
                 patterns=["*VTARGET*", "*VDDA_TGT", "*VREF_TGT", "*VBAT_TGT"]),
        NetClass("SW_NODE", "#E8B339", "solid", 0.254, 0.3048, 0.20, 0.50, 0.8, 0.4,
                 patterns=["*SW_5V", "*SW_3V3", "*SW_1V8", "*BST_*"]),
        NetClass("SENSE", "#3FA7B5", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*FB_*", "*_SENSE"]),
        NetClass("CTRL", "#6FA8DC", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*EN_*", "*_SEL", "*_RST"]),
        NetClass("STATUS", "#93C47D", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*PG_*", "*_RDY"]),
        NetClass("FAULT", "#C0392B", "dashed", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*FAULT*", "*KILL*", "*ALERT*", "*OCP*", "*EFUSE_FLT*"]),
        NetClass("USB", "#D26FA0", "solid", 0.2032, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 diff_pair_width=0.20, diff_pair_gap=0.127,
                 patterns=["*USB_D*"]),
        NetClass("SWD", "#7D6FB2", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*SWDIO*", "*SWCLK*", "*SWO*"]),
        NetClass("SPI_SW", "#2E9E93", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*CARD_SW_*"]),
        NetClass("I2C_PWR", "#4E9E4E", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*I2C_PWR_*"]),
        NetClass("LANE", "#A96FC2", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*CARD_LANE_*"]),
        NetClass("ID", "#9C7A3C", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*CARD_PRESENT*", "*CARD_ID*", "*PKG_ID*"]),
        NetClass("SERVICE", "#6E8FB0", "solid", 0.1524, 0.3048, 0.127, 0.20, 0.6, 0.3,
                 patterns=["*SERVICE_*", "*UART_*", "*MCO*"]),
    ]

    for priority, nc in enumerate(netclasses):
        nc.priority = priority
        manager.add_netclass(nc)

    return manager

# ═══════════════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════════════
def main_cli():
    """CLI interface for net class manager"""
    import argparse

    parser = argparse.ArgumentParser(description="KiCad Net Class Manager")
    parser.add_argument("--export-template", help="Export vault standard to template file")
    parser.add_argument("--import-template", help="Import template file")
    parser.add_argument("--sync-to", nargs="+", help="Sync to project files")
    parser.add_argument("--load-from", help="Load from project file")
    parser.add_argument("--clear-cache", action="store_true", help="Clear KiCad cache files")
    parser.add_argument("--repo-root", default=".", help="Repository root path")

    args = parser.parse_args()

    if args.clear_cache:
        clear_project_cache(Path(args.repo_root))
        return

    manager = NetClassManager()

    if args.export_template:
        vault_manager = create_vault_standard_template()
        vault_manager.export_template(Path(args.export_template))
        print(f"Exported vault standard v{VAULT_STANDARD_VERSION} to {args.export_template}")

    elif args.import_template:
        manager.import_template(Path(args.import_template))
        print(f"Imported template from {args.import_template}")
        print(f"Loaded {len(manager.net_classes)} net classes")

    elif args.load_from:
        success = manager.load_from_project(Path(args.load_from))
        if success:
            print(f"Loaded {len(manager.net_classes)} net classes from {args.load_from}")
            for name in manager.list_netclasses():
                nc = manager.get_netclass(name)
                print(f"  {name}: {nc.color} @ {nc.track_width}mm")
        else:
            print("Failed to load project")

    if args.sync_to:
        project_files = [Path(p) for p in args.sync_to]

        # Clear cache first
        print("Clearing cache files...")
        clear_project_cache(Path(args.repo_root))

        print(f"\nSyncing to {len(project_files)} projects...")
        results = manager.sync_to_projects(project_files)

        print(f"\nSynced to {len(results)} projects:")
        for proj, success in results.items():
            status = "✓" if success else "✗"
            print(f"  {status} {proj}")

if __name__ == "__main__":
    main_cli()