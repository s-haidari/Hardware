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
    # Whether this class genuinely carries a differential pair. Set from KEY
    # PRESENCE on load (see from_kicad_dict) — NOT from a value heuristic — so a
    # legitimate width=0.2/gap=0.25 pair is preserved instead of being mistaken
    # for the old "baked default" sentinel and silently dropped. None means
    # "derive from the width" (any positive width => has a pair).
    has_diff_pair: Optional[bool] = None

    # Priority (lower = higher precedence; Default uses 2147483647)
    priority: int = 0

    # Net patterns for assignment
    patterns: List[str] = None

    def __post_init__(self):
        if self.patterns is None:
            self.patterns = []
        if self.has_diff_pair is None:
            self.has_diff_pair = (self.diff_pair_width is not None
                                  and self.diff_pair_width > 0)

    def to_kicad_dict(self) -> dict:
        """Convert to KiCad .kicad_pro format"""
        result = {
            "name": self.name,
            "clearance": self.clearance,
            "track_width": self.track_width,
            "via_diameter": self.via_diameter,
            "via_drill": self.via_drill,
        }

        # Add differential pair ONLY when the class actually carries one.
        # Emitting KiCad's 0.2/0.25 defaults for a non-diff class (GND, PWR…)
        # is a phantom: from_kicad_dict then reads 0.2 back as a real width and
        # the UI renders an editable diff-pair spin where the source showed a
        # dim em-dash. KiCad tolerates the keys' absence (falls back to board
        # defaults), so mirror the UI's "width set and > 0" rule and omit them.
        if self.diff_pair_width is not None and self.diff_pair_width > 0:
            result["diff_pair_width"] = self.diff_pair_width
            result["diff_pair_gap"] = self.diff_pair_gap if self.diff_pair_gap else 0.25

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
        # KiCad stores schematic wire/bus widths as integer mils, and a value
        # of 0 is the sentinel for "inherit the default width" — NOT a 0 mm
        # wire. Treat any int as mils (0 -> default) and only trust a genuine
        # float as an already-mm value.
        wire_thickness = NetClass._width_from_kicad(
            data.get("wire_width", 6), default_mm=0.1524)
        bus_thickness = NetClass._width_from_kicad(
            data.get("bus_width", 12), default_mm=0.3048)

        # A differential pair is inferred from the WIDTH key's presence — the key
        # that actually defines a pair and drives serialization (to_kicad_dict
        # emits the pair only for a positive width). Basing has_diff_pair on the
        # width key keeps load and save in lock-step: a class WITH a width (even
        # the legitimate 0.2/0.25 pair the old sentinel hack silently wiped) is
        # preserved and re-emitted; a class WITHOUT a width is a non-diff class.
        # A stray gap-with-no-width is not a usable pair (KiCad needs the width),
        # so it is normalised to "no diff pair" rather than left as an internally
        # contradictory has_diff_pair=True whose gap the writer would then drop.
        dp_width = data.get("diff_pair_width")
        dp_gap = data.get("diff_pair_gap")
        has_dp = isinstance(dp_width, (int, float)) and not isinstance(dp_width, bool) and dp_width > 0
        if not has_dp:
            dp_width = None
            dp_gap = None

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
            diff_pair_width=dp_width,
            diff_pair_gap=dp_gap,
            diff_pair_via_gap=data.get("diff_pair_via_gap", 0.25),
            has_diff_pair=has_dp,
            priority=data.get("priority", 0),
            patterns=[]
        )

    @staticmethod
    def _width_from_kicad(value, default_mm: float) -> float:
        """Interpret a KiCad schematic wire/bus width value.

        KiCad stores these as integer mils; ``0`` means "inherit the default
        width" and must map to ``default_mm`` rather than a 0 mm wire. A float
        value is assumed to already be in mm (legacy/hand-edited files).
        """
        if isinstance(value, bool):  # bool is a subclass of int — guard first
            return default_mm
        if isinstance(value, int):
            if value == 0:
                return default_mm  # KiCad "inherit" sentinel
            return value * 0.0254  # mils -> mm
        try:
            return float(value)
        except (TypeError, ValueError):
            return default_mm

    @staticmethod
    def _hex_to_rgba(hex_color: str) -> str:
        """Convert a hex color to KiCad rgba format, tolerantly.

        Color cells are user-editable, so this never raises: it strips a
        leading ``#``, expands 3-digit shorthand (``#abc`` -> ``#aabbcc``),
        and validates 6 hex digits. Empty strings, named colors (``red``),
        and malformed hex fall back to ``#808080`` so one bad cell cannot
        abort the entire project save with a ValueError.
        """
        s = (hex_color or "").strip().lstrip('#')
        if len(s) == 3:
            s = "".join(c * 2 for c in s)  # #abc -> #aabbcc
        try:
            if len(s) != 6:
                raise ValueError("expected 6 hex digits")
            r = int(s[0:2], 16)
            g = int(s[2:4], 16)
            b = int(s[4:6], 16)
        except ValueError:
            r = g = b = 0x80  # #808080 fallback
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
        # Authoritative-delete set: names the user explicitly removed. On save these
        # are dropped from the file even though they are no longer in the managed set
        # (so the safe-merge must NOT re-preserve them as 'unmanaged'). See
        # mark_deleted / remove_netclass and the save_to_project merge below.
        self.deleted_names: set = set()

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

            # Identify classes in the file that are not managed by us. A name the
            # user explicitly deleted (mark_deleted / remove_netclass) is NOT
            # preserved — otherwise the safe-merge would re-add it as 'unmanaged'
            # and a delete could never actually reach the .kicad_pro.
            managed_names = set(self.net_classes.keys())
            deleted_names = set(self.deleted_names)
            unmanaged_existing = [
                cls for cls in existing_classes
                if cls.get("name") not in managed_names
                and cls.get("name") != "Default"
                and cls.get("name") not in deleted_names
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

            # Update patterns (safe merge — mirrors the class-definition merge
            # above). Two guards protect user net assignments:
            #
            #  * Carry over existing patterns whose netclass is ``Default`` or
            #    one of the preserved-unmanaged classes, THEN append the managed
            #    patterns. The old code rebuilt this list from managed classes
            #    alone, silently unassigning every unmanaged/Default net.
            #  * If the managed set is empty, refuse to touch netclass_patterns
            #    at all: an empty manager must never wipe every net assignment.
            if self.net_classes:
                preserved_names = set(self.last_preserved_unmanaged)
                preserved_names.add("Default")
                existing_patterns = data["net_settings"].get("netclass_patterns", [])

                patterns_list = [
                    p for p in existing_patterns
                    if isinstance(p, dict) and p.get("netclass") in preserved_names
                ]
                for name, netclass in self.net_classes.items():
                    for pattern in netclass.patterns:
                        patterns_list.append({
                            "netclass": name,
                            "pattern": pattern
                        })

                data["net_settings"]["netclass_patterns"] = patterns_list
            elif deleted_names:
                # Empty managed set but the user explicitly deleted classes: drop only
                # the deleted classes' patterns, keep every other assignment intact.
                existing_patterns = data["net_settings"].get("netclass_patterns", [])
                data["net_settings"]["netclass_patterns"] = [
                    p for p in existing_patterns
                    if not (isinstance(p, dict) and p.get("netclass") in deleted_names)
                ]
            # else: empty managed set, no deletions -> leave netclass_patterns intact

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
        # A name that is (re)added is no longer a deletion — clear any stale mark so a
        # New Net Class reusing a just-deleted name is not wiped on the next save.
        self.deleted_names.discard(netclass.name)

    def remove_netclass(self, name: str):
        """Remove a net class.

        Records the name in ``deleted_names`` so the next ``save_to_project`` deletes
        it authoritatively from the .kicad_pro instead of the safe-merge re-preserving
        it as an unmanaged class (which is why a UI delete never used to persist).
        """
        if name in self.net_classes:
            del self.net_classes[name]
        if name in self.patterns:
            del self.patterns[name]
        self.deleted_names.add(name)

    def rename_netclass(self, old_name: str, new_name: str) -> bool:
        """Rename a managed net class old_name -> new_name.

        Returns False (no change) if old_name is absent, new_name is empty/unchanged,
        or new_name already names a DIFFERENT managed class (no silent clobber). On
        success the class keeps its rules/patterns under the new key, the old name is
        marked deleted so the next save removes its stale entry from the .kicad_pro,
        and the new name is cleared from the deleted set.
        """
        new_name = (new_name or "").strip()
        if not new_name or old_name == new_name:
            return False
        if old_name not in self.net_classes:
            return False
        if new_name in self.net_classes:
            return False
        nc = self.net_classes.pop(old_name)
        nc.name = new_name
        self.net_classes[new_name] = nc
        if old_name in self.patterns:
            self.patterns[new_name] = self.patterns.pop(old_name)
        # The old name must be authoritatively deleted from the file; the new name is
        # now live, so it is no longer a deletion.
        self.deleted_names.add(old_name)
        self.deleted_names.discard(new_name)
        return True

    def mark_deleted(self, name: str):
        """Mark a name for authoritative deletion on the next save without requiring
        it to currently be in the managed set (e.g. deleting a class that was only
        ever in the file). ``save_to_project`` will drop it and its patterns."""
        self.net_classes.pop(name, None)
        self.patterns.pop(name, None)
        self.deleted_names.add(name)

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
                "microvia_diameter": netclass.microvia_diameter,
                "microvia_drill": netclass.microvia_drill,
                "diff_pair_width": netclass.diff_pair_width,
                "diff_pair_gap": netclass.diff_pair_gap,
                "diff_pair_via_gap": netclass.diff_pair_via_gap,
                "priority": netclass.priority,
                "patterns": netclass.patterns
            }

        template_file.write_text(json.dumps(template, indent=2), encoding="utf-8")

    def import_template(self, template_file: Path):
        """Import net classes from a JSON template"""
        data = json.loads(template_file.read_text(encoding="utf-8"))

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
                microvia_diameter=nc_data.get("microvia_diameter", 0.3),
                microvia_drill=nc_data.get("microvia_drill", 0.1),
                diff_pair_width=nc_data.get("diff_pair_width"),
                diff_pair_gap=nc_data.get("diff_pair_gap"),
                diff_pair_via_gap=nc_data.get("diff_pair_via_gap", 0.25),
                priority=nc_data.get("priority", 0),
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
# ── Fab profiles: the per-tier floors a net class is generated against ────────
# Each profile sets the SIGNAL-class clearance / track / via / drill and the min
# clearance every class is clamped up to, so the SAME vault taxonomy is emitted at
# whatever floor the chosen OSH Park service allows. Signal vias sit at the fab
# annular floor; power classes keep a heavier 0.6/0.3 via. Values in mm, verified
# against docs.oshpark.com/design-tools/kicad (2026-07-05).
NETCLASS_PROFILES = {
    "OSH Park 4-layer": {"sig_clearance": 0.127, "sig_track": 0.15,
                         "sig_via": 0.4572, "sig_drill": 0.254,
                         "pwr_via": 0.6, "pwr_drill": 0.3,
                         "min_clearance": 0.127, "min_track": 0.127,
                         "min_via": 0.4572, "min_drill": 0.254, "min_annular": 0.1016},
    "OSH Park 2-layer": {"sig_clearance": 0.1524, "sig_track": 0.1524,
                         "sig_via": 0.508, "sig_drill": 0.254,
                         "pwr_via": 0.6, "pwr_drill": 0.3,
                         "min_clearance": 0.1524, "min_track": 0.1524,
                         "min_via": 0.508, "min_drill": 0.254, "min_annular": 0.127},
}
DEFAULT_NETCLASS_PROFILE = "OSH Park 4-layer"

# The vault taxonomy, fab-independent: (name, color, style, wire, bus, base_clearance,
# base_track, kind, patterns, dp_width, dp_gap). kind drives which profile floor
# applies: 'power'/'plane' keep a heavy via + wide track, 'signal' takes the profile's
# signal via/track. Order is priority order (specific patterns above general).
_VAULT_NETCLASSES = [
    ("GND", "#5E8AC7", "solid", 0.2032, 0.3048, 0.127, 0.25, "plane",
     ["*GND", "*VSSA_TGT", "*VSSDSI", "*CHASSIS"], None, None),
    ("PWR_IN", "#B03A2E", "solid", 0.3048, 0.3048, 0.20, 0.60, "power",
     ["*V_SYS", "*USB_VBUS*", "*CELL_IN*"], None, None),
    ("PWR_5V", "#E07B39", "solid", 0.254, 0.3048, 0.127, 0.50, "power", ["*+5V"], None, None),
    ("PWR_3V3", "#C99A2E", "solid", 0.254, 0.3048, 0.127, 0.40, "power",
     ["*+3V3", "*+3V3_STATUS"], None, None),
    ("PWR_1V8", "#A6B84F", "solid", 0.254, 0.3048, 0.127, 0.40, "power", ["*+1V8"], None, None),
    ("TGT_PWR", "#C56FAE", "solid", 0.254, 0.3048, 0.127, 0.50, "power",
     ["*VTARGET*", "*VDDA_TGT", "*VREF_TGT", "*VBAT_TGT"], None, None),
    ("TGT_CORE", "#B060B0", "solid", 0.2032, 0.3048, 0.127, 0.30, "power",
     ["*VCAP_NODE*", "*VCAP_DSI_NODE*", "*VDD12DSI*"], None, None),   # 1.2V regulator nodes
    ("SW_NODE", "#E8B339", "solid", 0.254, 0.3048, 0.20, 0.50, "power",
     ["*SW_5V", "*SW_3V3", "*SW_1V8", "*BST_*"], None, None),
    ("SENSE", "#3FA7B5", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*FB_*", "*_SENSE"], None, None),
    ("CTRL", "#6FA8DC", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*EN_*", "*_SEL", "*_RST"], None, None),
    ("STATUS", "#93C47D", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*PG_*", "*_RDY"], None, None),
    ("FAULT", "#C0392B", "dashed", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*FAULT*", "*KILL*", "*ALERT*", "*OCP*", "*EFUSE_FLT*"], None, None),
    ("USB", "#D26FA0", "solid", 0.2032, 0.3048, 0.127, 0.20, "signal",
     ["*USB_D*"], 0.20, 0.15),
    ("SWD", "#7D6FB2", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*SWDIO*", "*SWCLK*", "*SWO*", "*TDI_PARENT*", "*NTRST_PARENT*", "*JTDI*",
      "*JTMS*", "*JTCK*", "*NJTRST*", "*TRACESWO*"], None, None),   # full SWD + JTAG
    ("SPI_SW", "#2E9E93", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*CARD_SW_*", "*SPI_SCLK*", "*SPI_DIN*", "*SPI_DOUT*", "*SPI_SYNC_N*",
      "*SPI_RESET_N*", "*SPI_CHAIN_*"], None, None),   # ADG714 control bus
    ("I2C_PWR", "#4E9E4E", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*I2C_PWR_*"], None, None),
    ("LANE", "#A96FC2", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*CARD_LANE_*"], None, None),
    ("ID", "#9C7A3C", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*CARD_PRESENT*", "*CARD_ID*", "*PKG_ID*"], None, None),
    ("SERVICE", "#6E8FB0", "solid", 0.1524, 0.3048, 0.127, 0.15, "signal",
     ["*SERVICE_*", "*UART_*", "*MCO*", "*BOOT0*", "*NRST*"], None, None),
]


def netclass_profiles() -> list:
    """The available fab profiles (names) for the vault standard."""
    return list(NETCLASS_PROFILES)


def validate_netclasses(manager, profile: str = DEFAULT_NETCLASS_PROFILE) -> list:
    """Check every net class carries proper, fab-legal values for a profile. Returns
    a list of {netclass, issue} — empty means every class is sound. Checks: clearance
    / track / via / drill at or above the fab floor, drill smaller than via, annular
    ring >= the fab minimum, positive wire/bus stroke, a valid hex color, at least one
    membership pattern, and unique priorities."""
    import re as _re
    prof = NETCLASS_PROFILES.get(profile, NETCLASS_PROFILES[DEFAULT_NETCLASS_PROFILE])
    findings = []
    prios = {}
    for name in manager.list_netclasses():
        nc = manager.get_netclass(name)

        def bad(issue, _n=name):
            findings.append({"netclass": _n, "issue": issue})

        if nc.clearance < prof["min_clearance"] - 1e-9:
            bad(f"clearance {nc.clearance} < fab min {prof['min_clearance']}")
        if nc.track_width < prof["min_track"] - 1e-9:
            bad(f"track {nc.track_width} < fab min {prof['min_track']}")
        if nc.via_diameter < prof["min_via"] - 1e-9:
            bad(f"via {nc.via_diameter} < fab min {prof['min_via']}")
        if nc.via_drill < prof["min_drill"] - 1e-9:
            bad(f"via drill {nc.via_drill} < fab min {prof['min_drill']}")
        if nc.via_drill >= nc.via_diameter:
            bad(f"via drill {nc.via_drill} >= via {nc.via_diameter}")
        elif (nc.via_diameter - nc.via_drill) / 2 < prof["min_annular"] - 1e-9:
            bad(f"annular ring {(nc.via_diameter - nc.via_drill) / 2:.4f} < fab min "
                f"{prof['min_annular']}")
        if nc.wire_thickness <= 0 or nc.bus_thickness <= 0:
            bad("non-positive wire/bus stroke")
        if not _re.fullmatch(r"#[0-9A-Fa-f]{6}", nc.color or ""):
            bad(f"invalid color {nc.color!r}")
        if name != "Default" and not (nc.patterns or []):
            bad("no membership pattern")
        if nc.diff_pair_width and not nc.diff_pair_gap:
            bad("diff-pair width set but no gap")
        prios.setdefault(nc.priority, []).append(name)
    for prio, names in prios.items():
        if len(names) > 1:
            findings.append({"netclass": ", ".join(names), "issue": f"duplicate priority {prio}"})
    return findings


def create_vault_standard_template(profile: str = DEFAULT_NETCLASS_PROFILE) -> NetClassManager:
    """The vault-standard net classes generated against a fab PROFILE (default OSH
    Park 4-layer). The taxonomy + colors + patterns are constant; the clearance /
    track / via floors follow the profile so the same standard is valid on either
    OSH Park service. Every class clearance is clamped up to the profile minimum,
    signal classes take the profile's signal via/track, power/plane classes keep a
    heavy 0.6/0.3 via and their wide track."""
    prof = NETCLASS_PROFILES.get(profile, NETCLASS_PROFILES[DEFAULT_NETCLASS_PROFILE])
    mc, mt = prof["min_clearance"], prof["min_track"]
    manager = NetClassManager()
    for priority, (name, color, style, wire, bus, base_clr, base_trk, kind,
                   patterns, dpw, dpg) in enumerate(_VAULT_NETCLASSES):
        clearance = max(base_clr, mc)
        if kind == "signal":
            track = max(prof["sig_track"], mt)
            via, drill = prof["sig_via"], prof["sig_drill"]
        else:                                        # power / plane
            track = max(base_trk, mt)
            via, drill = prof["pwr_via"], prof["pwr_drill"]
        nc = NetClass(name, color, style, wire, bus, clearance, track, via, drill,
                      diff_pair_width=dpw or 0.0, diff_pair_gap=dpg or 0.0, patterns=patterns)
        nc.priority = priority
        manager.add_netclass(nc)
    return manager


# The editable vault standard is saved here; when absent, the built-in default
# above is used. "Save as Vault Standard" writes this file so the canonical
# standard can be changed without editing code.
def _vault_standard_path() -> Path:
    """Where the editable vault standard is read/written. Under a frozen --onefile
    exe __file__ points into the throwaway PyInstaller bundle, so "Save as Vault
    Standard" must write to the user's library location; dev keeps it next to the
    module (SP1)."""
    import sys
    if getattr(sys, "frozen", False):
        try:
            import LibraryManager as _LM
            loc = _LM.library_location()
            if loc:
                return Path(loc) / "vault_standard.json"
        except Exception:
            pass
        return Path(sys.executable).resolve().parent / "vault_standard.json"
    return Path(__file__).resolve().parent / "vault_standard.json"


def load_vault_standard() -> NetClassManager:
    """The vault-standard net classes: the saved editable standard if present,
    otherwise the built-in default template."""
    path = _vault_standard_path()
    if path.exists():
        try:
            m = NetClassManager()
            m.import_template(path)
            if m.list_netclasses():
                return m
        except Exception:
            pass
    return create_vault_standard_template()


def save_vault_standard(manager: NetClassManager) -> Path:
    """Persist `manager` as the canonical vault standard."""
    path = _vault_standard_path()
    manager.export_template(path)
    return path


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
        # Guard: syncing an empty manager would treat every non-Default class
        # as unmanaged and wipe all net assignments across each project.
        # Refuse rather than silently unassign every net. Populate the manager
        # first via --import-template or --load-from.
        if not manager.net_classes:
            print("ERROR: no net classes loaded — refusing to --sync-to "
                  "(this would unassign every net). Use --import-template or "
                  "--load-from to populate the manager first.")
            raise SystemExit(2)

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