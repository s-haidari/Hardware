#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad Tools dialog — folds the NETDECK project helpers into KiCAD Manager:

  * Bulk Rename Wizard   (add/remove owner tags, strip tags, unannotate, find/replace)
  * Net Class Manager    (edit net classes, sync into every project's .kicad_pro)
  * Project Settings     (sync schematic/PCB drawing defaults + design rules)

The "smarter" part: instead of the NETDECK-specific hardcoded project locations,
these operate on whatever **KiCad projects folder** you point them at — projects
are discovered generically (any folder containing a .kicad_pro, ignoring
.history). The reusable cores are vendored as nd_*.py (pure stdlib).
"""
import sys
import subprocess
from pathlib import Path
from typing import List, Optional

from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QCheckBox, QListWidget, QListWidgetItem, QPlainTextEdit,
    QStackedWidget, QTableWidget, QTableWidgetItem, QFormLayout, QDoubleSpinBox,
    QFileDialog, QMessageBox, QAbstractItemView, QHeaderView, QSizePolicy,
    QApplication, QColorDialog, QScrollArea, QToolButton, QMenu, QWidgetAction,
    QGridLayout, QDialog, QDialogButtonBox
)
import ui_theme
from PyQt5.QtGui import QColor, QIcon, QPixmap, QPainter
from PyQt5.QtCore import Qt, pyqtSignal
try:
    from PyQt5.QtSvg import QSvgRenderer
    _HAVE_QTSVG = True
except Exception:
    _HAVE_QTSVG = False


# Lucide icons (MIT), tinted — matches the main window. SVGs bundled in tools/lucide/.
# Icons come from the shared design system (tools/ui_theme.py); the _LU_*
# aliases keep the existing call sites readable.
from ui_theme import (lucide_icon as _lucide,  # noqa: F401
                      LUCIDE_NEUTRAL as _LU_NEUTRAL, LUCIDE_BLUE as _LU_BLUE,
                      LUCIDE_GREEN as _LU_GREEN, LUCIDE_RED as _LU_RED,
                      LUCIDE_AMBER as _LU_AMBER)


import nd_wizard as wiz
import nd_kicad_checks as kchecks
import nd_project_health as phealth
import nd_fab_presets as fabp
import nd_object_conform as conform
import nd_netclass_manager as ncm
from nd_netclass_manager import (
    NetClass, NetClassManager, create_vault_standard_template,
    load_vault_standard, save_vault_standard,
)
from nd_project_settings_manager import (
    ProjectSettings, ProjectSettingsManager, mils_to_mm,
    SEVERITY_LEVELS, DRC_RULE_IDS, ERC_RULE_IDS, ERC_PIN_TYPES,
)
import nd_board_setup as board_setup

# (QFluentWidgets was removed with the old UI; the kept helpers below are pure
# path / sorting / kicad-cli functions with no widget dependency.)


# Hidden-window flag so any kicad-cli call doesn't flash a console
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
