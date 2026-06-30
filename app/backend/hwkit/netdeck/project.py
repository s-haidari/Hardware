"""
project.py — apply the vault netclass standard to a KiCad project (.kicad_pro).

Turns the standard's classes (color / track / clearance / via / members) into a
project's ``net_settings.classes`` + ``netclass_patterns`` so the one standard
drives every KiCad project. Idempotent, dry-run aware, writes a .bak.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def track_mm(value) -> float:
    """First numeric value from a track spec ('>= 0.50', '0.15 (plane/pour)')."""
    m = re.search(r"-?\d+(\.\d+)?", str(value))
    return float(m.group()) if m else 0.15


def hex_to_rgba(color: str) -> str:
    c = (color or "").lstrip("#")
    if len(c) != 6:
        return "rgba(138, 143, 152, 1.000)"
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return f"rgba({r}, {g}, {b}, 1.000)"


def _to_kicad_class(c: dict) -> dict:
    track = track_mm(c.get("track"))
    color = hex_to_rgba(str(c.get("color", "")))
    out = {
        "name": c.get("netclass", "Default"),
        "clearance": float(c.get("clearance", 0.2) or 0.2),
        "track_width": track,
        "via_diameter": float(c.get("via_dia", 0.6) or 0.6),
        "via_drill": float(c.get("via_drill", 0.3) or 0.3),
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
        "pcb_color": color,
        "schematic_color": color,
        "wire_width": 6.0,
        "bus_width": 12.0,
        "line_style": 0,
    }
    if "dp_width" in c:
        out["diff_pair_width"] = float(c["dp_width"])
    if "dp_gap" in c:
        out["diff_pair_gap"] = float(c["dp_gap"])
    return out


def _patterns(classes: list[dict]) -> list[dict]:
    pats: list[dict] = []
    for c in classes:
        name = c.get("netclass", "")
        for member in c.get("members", []) or []:
            if not member or "everything" in str(member).lower():
                continue
            pats.append({"netclass": name, "pattern": str(member)})
    return pats


@dataclass
class ApplyResult:
    project: str
    classes: int
    patterns: int
    changed: bool
    dry_run: bool


def apply_netclasses(project_path: Path, classes: list[dict], *,
                     dry_run: bool = False, backup: bool = True) -> ApplyResult:
    data = json.loads(project_path.read_text(encoding="utf-8"))
    net = data.get("net_settings")
    if not isinstance(net, dict):
        net = {}
    new_classes = [_to_kicad_class(c) for c in classes]
    new_patterns = _patterns(classes)

    changed = net.get("classes") != new_classes or net.get("netclass_patterns") != new_patterns
    net["classes"] = new_classes
    net["netclass_patterns"] = new_patterns
    data["net_settings"] = net

    if changed and not dry_run:
        if backup:
            project_path.with_suffix(project_path.suffix + ".bak").write_text(
                project_path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
        project_path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")
    return ApplyResult(str(project_path), len(new_classes), len(new_patterns), changed, dry_run)
