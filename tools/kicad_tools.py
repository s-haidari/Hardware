#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad project helpers — pure stdlib utilities shared by Routing and Projects.

No dialog or widgets live here anymore. What remains are GUI-independent,
side-effect-free helpers safe to call from worker threads:

  * discover_kicad_projects  — generic, location-independent project discovery:
    every folder under a root that holds a .kicad_pro (dot-folders and .history
    skipped).
  * project_pro_file         — the .kicad_pro for a given project directory.
  * pick_root_schematic      — non-interactive root/top-level schematic pick for
    ERC (never prompts, unlike nd_wizard.pick_top_schematic which input()s).
  * sort_netclass_snapshots / _nc_priority_sort_key — loss-free reorder of raw
    net-class row snapshots by priority then name (blanks and duplicates survive).
  * wiz_find_kicad_cli       — kicad-cli path via the shared locator.
"""
from pathlib import Path
from typing import List, Optional


def discover_kicad_projects(root: Path) -> List[Path]:
    """Every folder under `root` that contains a .kicad_pro (ignores .history
    and dot-folders). This is the generic, location-independent discovery."""
    root = Path(root)
    if not root.exists():
        return []
    dirs = set()
    for f in root.rglob("*.kicad_pro"):
        if any(p == ".history" or (p.startswith(".") and len(p) > 1) for p in f.parts):
            continue
        dirs.add(f.parent)
    return sorted(dirs, key=lambda p: str(p).lower())


def project_pro_file(project_dir: Path) -> Optional[Path]:
    hits = sorted(Path(project_dir).glob("*.kicad_pro"))
    return hits[0] if hits else None


def pick_root_schematic(schematics: List[Path],
                        pro: Optional[Path] = None) -> Optional[Path]:
    """Pick the root/top-level schematic for ERC **non-interactively**.

    KiCad's convention: the root sheet shares the project's stem
    (``project.kicad_pro`` -> ``project.kicad_sch`` next to it). Prefer that;
    then a stem match anywhere; then any schematic sitting directly beside the
    ``.kicad_pro``; finally the shallowest path (alphabetical tie-break). Never
    prompts, so it is safe to call from a worker thread (unlike the CLI
    ``nd_wizard.pick_top_schematic``, which ``input()``s and would hang/raise)."""
    schs = [Path(s) for s in schematics]
    if not schs:
        return None
    if pro is not None:
        pro = Path(pro)
        stem = pro.stem
        # 1) exact stem match sitting next to the .kicad_pro (the true root sheet)
        for s in schs:
            if s.stem == stem and s.parent == pro.parent:
                return s
        # 2) stem match anywhere in the tree
        for s in schs:
            if s.stem == stem:
                return s
        # 3) any schematic directly beside the project file
        in_dir = sorted((s for s in schs if s.parent == pro.parent),
                        key=lambda p: str(p).lower())
        if in_dir:
            return in_dir[0]
    # 4) fallback: shallowest path (closest to project root), then alphabetical
    return sorted(schs, key=lambda p: (len(p.parts), str(p).lower()))[0]


def _nc_priority_sort_key(snap: dict):
    """Sort key for reordering net-class rows by Priority then Name. A blank or
    non-numeric Priority sorts as 0 (KiCad's implicit default) rather than
    raising or being dropped."""
    p = snap.get("priority")
    try:
        pv = float(p) if p not in (None, "") else 0.0
    except (ValueError, TypeError):
        pv = 0.0
    return (pv, (snap.get("name") or "").lower())


def sort_netclass_snapshots(snaps: List[dict]) -> List[dict]:
    """Stable, loss-free reorder of net-class row snapshots by priority then
    name. Each snapshot is a dict of the row's *raw* cell text, returned intact
    and in full — duplicate names, empty-name rows, and blank cells all survive
    (unlike routing the table through NetClassManager, which is name-keyed and
    back-fills blanks with defaults)."""
    return sorted(snaps, key=_nc_priority_sort_key)




def wiz_find_kicad_cli() -> Optional[str]:
    """kicad-cli path — delegates to the shared locator."""
    from kicad_paths import find_kicad_cli
    return find_kicad_cli()
