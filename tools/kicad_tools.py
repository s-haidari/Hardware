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
import ui_widgets as uw
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

# Grayscale QFluentWidgets components for the NEW Project Settings sections
# (DRC/ERC severities, text variables, predefined size tables, editable Default
# net class). Falls back to the plain PyQt5 widgets if QFluentWidgets is somehow
# unavailable so construction never fails; the app themes these through the
# shared apply_grayscale_fluent/_apply_theme path, so they match the design.
try:
    from qfluentwidgets import (
        ComboBox as _FComboBox, DoubleSpinBox as _FDoubleSpinBox,
        LineEdit as _FLineEdit, TableWidget as _FTableWidget,
    )
    _HAVE_FLUENT = True
except Exception:  # pragma: no cover - fluent is a hard requirement; stay safe
    _FComboBox, _FDoubleSpinBox = QComboBox, QDoubleSpinBox
    _FLineEdit, _FTableWidget = QLineEdit, QTableWidget
    _HAVE_FLUENT = False


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


class KiCadToolsWidget(QWidget):
    LINE_STYLES = ["solid", "dashed", "dotted", "dash_dot"]

    # Net-class table columns (label, NetClass attr, kind) — every field KiCad
    # exposes. mm for distances; "color"/"linestyle" get special editors.
    NC_COLS = [
        ("Name", "name", "text"),
        ("Clearance", "clearance", "num"),
        ("Track Width", "track_width", "num"),
        ("Via Size", "via_diameter", "num"),
        ("Via Hole", "via_drill", "num"),
        ("µVia Size", "microvia_diameter", "num"),
        ("µVia Hole", "microvia_drill", "num"),
        ("Diff Pair Width", "diff_pair_width", "num"),
        ("Diff Pair Gap", "diff_pair_gap", "num"),
        ("Diff Pair Via Gap", "diff_pair_via_gap", "num"),
        ("Wire Thickness", "wire_thickness", "num"),
        ("Bus Thickness", "bus_thickness", "num"),
        ("Color", "color", "color"),
        ("Line Style", "line_style", "linestyle"),
        ("Priority", "priority", "num"),
        ("Patterns", "patterns", "patterns"),
    ]
    # Project settings grouped like KiCad's dialog (all values in mils).
    PS_GROUPS = [
        ("Schematic", [
            ("schematic_text_size", "Text Size"),
            ("schematic_line_width", "Line Width"),
            ("pin_symbol_size", "Pin Symbol Size"),
            ("junction_size", "Junction Size"),
        ]),
        ("PCB Text Boxes", [
            ("pcb_text_size", "Text Size"),
            ("pcb_text_thickness", "Text Thickness"),
        ]),
        ("Footprint Text", [
            ("silk_text_size", "Silkscreen Size"),
            ("silk_text_thickness", "Silkscreen Thickness"),
            ("copper_text_size", "Copper Size"),
            ("copper_text_thickness", "Copper Thickness"),
            ("fab_text_size", "Fab Size"),
            ("fab_text_thickness", "Fab Thickness"),
        ]),
        ("Design Rules (Defaults)", [
            ("default_clearance", "Clearance"),
            ("default_track_width", "Track Width"),
            ("default_via_diameter", "Via Diameter"),
            ("default_via_drill", "Via Drill"),
        ]),
        ("Minimum Constraints", [
            ("min_via_diameter", "Min Via Diameter"),
            ("min_via_annular_width", "Min Via Annular Ring"),
            ("min_through_hole", "Min Through-Hole"),
            ("min_hole_to_hole", "Min Hole-to-Hole"),
            ("min_hole_clearance", "Min Hole Clearance"),
            ("min_microvia_diameter", "Min µVia Diameter"),
            ("min_microvia_drill", "Min µVia Drill"),
            ("min_copper_edge_clearance", "Min Copper-to-Edge"),
            ("min_silk_clearance", "Min Silkscreen Clearance"),
        ]),
        ("Solder Mask / Paste", [
            ("solder_mask_clearance", "Mask Clearance"),
            ("solder_paste_margin", "Paste Margin"),
        ]),
    ]

    log_line = pyqtSignal(str)                # queued -> safe from worker threads

    def __init__(self, parent, projects_dir: str, save_dir_cb=None, ctx=None):
        super().__init__(parent)
        self._save_dir_cb = save_dir_cb
        self._ctx = ctx                        # shell services (ui_shell.TabContext)
        self.log_line.connect(self._append_log)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(0)

        # ── left rail: the three operations ──
        self.rail = uw.Rail(150)
        self.rail.add_group("Operations")
        self.rail.add_item("rename", "Bulk Rename")
        self.rail.add_item("net", "Net Classes")
        self.rail.add_item("settings", "Project Settings")
        self.rail.selected.connect(self._on_op)
        self._railwrap = QWidget()
        rwl = QVBoxLayout(self._railwrap)
        rwl.setContentsMargins(0, 0, 16, 0)
        rwl.setSpacing(0)
        rwl.addWidget(self.rail)
        rwl.addStretch(1)
        root.addWidget(self._railwrap)

        # ── main column ──
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)

        # toolbar: folder + browse/rescan on the left, project picker on the right
        bar = uw.toolbar_row()
        fl = QLabel("Folder")
        fl.setStyleSheet("font-weight:600;")
        bar.addWidget(fl)
        self.dir_edit = QLineEdit(projects_dir or "")
        bar.addWidget(self.dir_edit, 1)
        b_browse = uw.button("Browse…", "default", _lucide("folder-open", _LU_NEUTRAL))
        b_browse.setToolTip("Choose the folder that holds your KiCad projects")
        b_browse.clicked.connect(self._browse)
        b_scan = uw.button("Rescan", "default", _lucide("refresh-cw", _LU_BLUE))
        b_scan.setToolTip("Rescan the folder for KiCad projects")
        b_scan.clicked.connect(self.rescan)
        bar.addWidget(b_browse)
        bar.addWidget(b_scan)
        main.addLayout(bar)

        # readout band: project counts
        self.readout = uw.ReadoutBand([
            ("projects", "Projects", None),
            ("selected", "Selected", "ACCENT"),   # token key → re-resolves on theme toggle
        ])
        self.readout.set_identity("KiCad Tools", "schematic + PCB batch operations")
        main.addWidget(self.readout)

        # operation section header (updates with the rail) + stacked pages
        self._op_header = uw.SectionHeader("Bulk Rename")
        main.addWidget(self._op_header)
        self.stack = QStackedWidget()
        self._op_index = {"rename": 0, "net": 1, "settings": 2}
        self._op_title = {"rename": "Bulk Rename", "net": "Net Classes",
                          "settings": "Project Settings"}
        self.stack.addWidget(self._build_rename_tab())
        self.stack.addWidget(self._build_netclass_tab())
        self.stack.addWidget(self._build_settings_tab())
        main.addWidget(self.stack, 1)

        # output
        main.addWidget(uw.SectionHeader("Output"))
        self.out = QPlainTextEdit(); self.out.setReadOnly(True); self.out.setMaximumHeight(96)
        main.addWidget(self.out)

        root.addLayout(main, 1)

        # ── right panel: the projects the tools act on, always visible ──
        proj_panel = QWidget()
        pv = QVBoxLayout(proj_panel)
        pv.setContentsMargins(18, 0, 0, 0)
        pv.setSpacing(8)
        hdr = uw.SectionHeader("Projects")
        pv.addWidget(hdr)
        hb = QHBoxLayout()
        hb.setSpacing(6)
        b_all = uw.button("All", "ghost")
        b_all.setToolTip("Select every project")
        b_all.clicked.connect(lambda: self._check_all(True))
        b_none = uw.button("None", "ghost")
        b_none.setToolTip("Clear the project selection")
        b_none.clicked.connect(lambda: self._check_all(False))
        hb.addWidget(b_all); hb.addWidget(b_none); hb.addStretch(1)
        pv.addLayout(hb)
        self.proj_list = QListWidget()
        self.proj_list.setToolTip("Check the projects the operations should act on")
        self.proj_list.itemChanged.connect(lambda *_: self._update_selection())
        pv.addWidget(self.proj_list, 1)
        proj_panel.setFixedWidth(272)
        root.addWidget(proj_panel)
        self._restyle()
        self.rescan()

    def _on_op(self, key: str):
        self.stack.setCurrentIndex(self._op_index[key])
        self._op_header.set_text(self._op_title[key])

    def _restyle(self):
        self._railwrap.setStyleSheet(
            f"background:transparent;border-right:1px solid {ui_theme.tc('BORDER')};")
        uw.restyle_all(self.rail, self.readout, self._op_header,
                       getattr(self, "_nc_empty", None))

    def apply_theme(self, dark: bool):
        """Follow the app theme (the shell already published the palette)."""
        self._restyle()

    def _card(self, title: str):
        """A titled card — the shared chrome from ui_widgets."""
        from ui_widgets import make_card
        return make_card(title)

    # ---------- helpers ----------
    def log(self, msg: str):
        """Thread-safe: emits into the output pane via a queued signal and
        forwards to the shared shell log when running inside the app."""
        self.log_line.emit(msg)
        if self._ctx is not None:
            try:
                self._ctx.log(msg)
            except Exception:
                pass

    def _append_log(self, msg: str):
        self.out.appendPlainText(msg)
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())

    def _run_heavy(self, busy: str, fn):
        """Run long work off the GUI thread when the shell provides a runner
        (window stays responsive, status bar shows busy); inline standalone."""
        if self._ctx is not None and getattr(self._ctx, "run_async", None):
            self._ctx.run_async(fn, busy, "Done ✓")
        else:
            fn()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select KiCad Projects Folder", self.dir_edit.text() or "")
        if d:
            self.dir_edit.setText(d)
            self.rescan()

    def rescan(self):
        path = self.dir_edit.text().strip()
        if self._save_dir_cb:
            self._save_dir_cb(path)
        self.proj_list.clear()
        projs = discover_kicad_projects(Path(path)) if path else []
        for d in projs:
            pro = project_pro_file(d)
            it = QListWidgetItem(f"{d.name}    ({d})")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, str(pro) if pro else "")
            self.proj_list.addItem(it)
        self._update_selection()
        self.log(f"Found {len(projs)} KiCad project(s) under {path or '(unset)'}")

    def _update_selection(self):
        total = self.proj_list.count()
        sel = sum(1 for i in range(total)
                  if self.proj_list.item(i).checkState() == Qt.Checked)
        self.readout.set("projects", total)
        self.readout.set("selected", sel)

    def _check_all(self, on: bool):
        for i in range(self.proj_list.count()):
            self.proj_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._update_selection()

    def selected_pro_files(self) -> List[Path]:
        out = []
        for i in range(self.proj_list.count()):
            it = self.proj_list.item(i)
            if it.checkState() == Qt.Checked:
                pro = it.data(Qt.UserRole)
                if pro:
                    out.append(Path(pro))
        return out

    def selected_project_dirs(self) -> List[Path]:
        return [p.parent for p in self.selected_pro_files()]

    # ============================================================== RENAME
    def _build_rename_tab(self) -> QWidget:
        # Two columns in a bounded measure so the form doesn't strand a thin
        # column against a void: [operation form] | [scope checkboxes].
        w = QWidget(); outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        cols = QHBoxLayout(); cols.setSpacing(28)

        left = QVBoxLayout(); left.setSpacing(6)
        left.addWidget(uw.SectionHeader("Operation"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(10)
        self.op_combo = QComboBox()
        self.op_combo.setToolTip("Choose the bulk rename or tag operation to run on the selected projects")
        self.op_combo.addItems([
            "Add Tag Prefix", "Remove Tag Prefix", "Strip All Tags",
            "Reset to Unannotated (lib_id)", "Custom Find / Replace",
        ])
        self.op_combo.currentIndexChanged.connect(self._op_changed)
        form.addRow("Operation", self.op_combo)
        self.tag_edit = QLineEdit(); self.tag_edit.setPlaceholderText("E.g. SH- or CG-")
        self.find_edit = QLineEdit(); self.find_edit.setPlaceholderText("Find text")
        self.repl_edit = QLineEdit(); self.repl_edit.setPlaceholderText("Replace with")
        form.addRow("Tag", self.tag_edit)
        form.addRow("Find", self.find_edit)
        form.addRow("Replace", self.repl_edit)
        self._rename_form = form
        left.addLayout(form)
        left.addStretch(1)
        left_w = QWidget(); left_w.setLayout(left); left_w.setMinimumWidth(360)

        right = QVBoxLayout(); right.setSpacing(6)
        right.addWidget(uw.SectionHeader("Scope"))
        self.chk_sch_labels = QCheckBox("Schematic Labels / Nets"); self.chk_sch_labels.setChecked(True)
        self.chk_sch_refs = QCheckBox("Schematic References"); self.chk_sch_refs.setChecked(True)
        self.chk_pcb_refs = QCheckBox("PCB References"); self.chk_pcb_refs.setChecked(True)
        right.addWidget(self.chk_sch_labels)
        right.addWidget(self.chk_sch_refs)
        right.addWidget(self.chk_pcb_refs)
        right.addStretch(1)
        right_w = QWidget(); right_w.setLayout(right); right_w.setMinimumWidth(260)

        cols.addWidget(left_w)
        cols.addWidget(right_w)
        cols.addStretch(1)
        outer.addLayout(cols)

        btns = uw.toolbar_row()
        b_prev = uw.button("Preview", "default", _lucide("list-checks", _LU_NEUTRAL))
        b_prev.setToolTip("Show what the operation would change, without writing any files")
        b_prev.clicked.connect(lambda: self._run_rename(apply=False))
        b_apply = uw.button("Apply (Creates .bak)", "primary", _lucide("pencil", _LU_GREEN))
        b_apply.setToolTip("Apply the operation, saving a .bak backup of every file first")
        b_apply.clicked.connect(lambda: self._run_rename(apply=True))
        b_audit = uw.button("Audit", "default", _lucide("stethoscope", _LU_NEUTRAL))
        b_audit.setToolTip("Health audit of the selected projects — unannotated / duplicate "
                           "refs, missing footprints, pin/pad mismatches, no MPN (no kicad-cli)")
        b_audit.clicked.connect(self._run_audit)
        b_erc = uw.button("Run ERC", "default", _lucide("play", _LU_GREEN))
        b_erc.setToolTip("Run KiCad's Electrical Rules Check (schematic) on the selected projects")
        b_erc.clicked.connect(self._run_erc)
        b_drc = uw.button("Run DRC", "default", _lucide("play", _LU_GREEN))
        b_drc.setToolTip("Run KiCad's Design Rules Check (board) on the selected projects")
        b_drc.clicked.connect(self._run_drc)
        b_fab = uw.button("Fab Standard…", "default", _lucide("layers", _LU_NEUTRAL))
        b_fab.setToolTip("Apply an OSH Park design-rule + stackup standard and conform "
                         "existing objects (text/labels) to it — preview + .bak backup")
        b_fab.clicked.connect(self._open_fab_standard)
        btns.addWidget(b_prev); btns.addWidget(b_apply); btns.addWidget(b_audit)
        btns.addWidget(b_erc); btns.addWidget(b_drc); btns.addWidget(b_fab); btns.addStretch()
        outer.addLayout(btns)
        # actions sit right under the form; the empty room falls to the bottom
        outer.addStretch(1)
        self._op_changed()
        return w

    def _row_visible(self, field, vis):
        field.setVisible(vis)
        lbl = self._rename_form.labelForField(field)
        if lbl is not None:
            lbl.setVisible(vis)

    def _op_changed(self):
        idx = self.op_combo.currentIndex()
        self._row_visible(self.tag_edit, idx in (0, 1))
        self._row_visible(self.find_edit, idx == 4)
        self._row_visible(self.repl_edit, idx == 4)

    def _rename_params(self):
        idx = self.op_combo.currentIndex()
        if idx == 0:
            return "add_tag", self.tag_edit.text().strip(), None
        if idx == 1:
            return "remove_tag", self.tag_edit.text().strip(), None
        if idx == 2:
            return "strip_all", None, None
        if idx == 3:
            return "unannotate", None, None
        return "find_replace", self.find_edit.text(), self.repl_edit.text()

    def _run_rename(self, apply: bool):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Bulk Rename", "No projects selected.")
            return
        op, tag_or_find, repl = self._rename_params()
        if op in ("add_tag", "remove_tag") and not tag_or_find:
            QMessageBox.warning(self, "Bulk Rename", "Enter a tag prefix."); return
        if op == "find_replace" and not tag_or_find:
            QMessageBox.warning(self, "Bulk Rename", "Enter the text to find."); return
        do_labels = self.chk_sch_labels.isChecked()
        do_refs = self.chk_sch_refs.isChecked()
        do_pcb = self.chk_pcb_refs.isChecked()
        if apply:
            if QMessageBox.question(
                self, "Apply Changes",
                f"Apply '{self.op_combo.currentText()}' to {len(pros)} project(s)?\n"
                f"A .bak is written next to every modified file.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

        def work():
            totals = {"local": 0, "global": 0, "hier": 0, "sheet_pin": 0, "symbol_ref": 0, "pcb_ref": 0}
            samples = []
            changes = []                     # audit records; consumed by the JSON audit trail below
            self.log(f"\n=== {'APPLY' if apply else 'PREVIEW'}: {self.op_combo.currentText()} ===")
            # Pass 1 — read-only preview over every file: counts + samples, no writes.
            for pro in pros:
                proj = pro.parent
                if do_labels or do_refs:
                    for sch in wiz.list_schematics(proj):
                        counts, smp, rec = wiz.schematic_preview_and_apply(
                            sch, op, tag_or_find, repl=repl, apply=False,
                            touch_refs=do_refs, touch_labels=do_labels)
                        for k in totals:
                            totals[k] += counts.get(k, 0)
                        samples += smp
                        changes += rec
                if do_pcb:
                    for brd in wiz.list_boards(proj):
                        cnt, smp, rec = wiz.pcb_preview_and_apply(
                            brd, op, tag_or_find, repl=repl, apply=False)
                        totals["pcb_ref"] += cnt
                        samples += smp
                        changes += rec
            self.log(f"Schematic labels: {totals['local']+totals['global']+totals['hier']+totals['sheet_pin']}  "
                     f"| Schematic refs: {totals['symbol_ref']}  | PCB refs: {totals['pcb_ref']}")
            for smp in samples[:15]:
                self.log("  " + smp)
            if not any(totals.values()):
                self.log("  No matching changes.")
            elif apply:
                # Pass 2 — all-or-nothing apply via the wizard's atomic machinery:
                # every transform stages in memory first (a locked or unreadable file
                # aborts before any write), and a failed write rolls every file back
                # from its .bak. No project is ever left half-renamed.
                from datetime import datetime as _dt
                tasks = []
                for pro in pros:
                    proj = pro.parent
                    if do_labels or do_refs:
                        for sch in wiz.list_schematics(proj):
                            tasks.append((sch, wiz._make_sch_task(
                                sch, op, tag_or_find, repl, do_refs, do_labels)))
                    if do_pcb:
                        for brd in wiz.list_boards(proj):
                            tasks.append((brd, wiz._make_pcb_task(brd, op, tag_or_find, repl)))
                stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                try:
                    applied, backups = wiz.apply_transforms_atomically(tasks, stamp)
                except wiz.ApplyError as e:
                    self.log(f"APPLY ABORTED during {e.stage} of {e.path.name}: {e.original}")
                    self.log("All-or-nothing: no files were left modified "
                             "(any partial write was rolled back from its .bak).")
                    changes = []             # nothing landed; write no audit trail
                else:
                    changes = applied        # audit exactly what was written
                    self.log(f"Applied {len(applied)} change(s) across {len(backups)} file(s). "
                             "Backups (.bak) created next to modified files.")
            # JSON audit trail (same records + location the CLI wizard writes)
            if changes:
                try:
                    import json as _json
                    from datetime import datetime as _dt
                    wiz.LOG_DIR.mkdir(parents=True, exist_ok=True)
                    stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                    audit = wiz.LOG_DIR / f"{stamp}_{'applied' if apply else 'preview'}.json"
                    audit.write_text(_json.dumps(
                        [{"type": t, "old": o, "new": n, "file": str(f)}
                         for (t, o, n, f) in changes], indent=2), encoding="utf-8")
                    self.log(f"Audit log: {audit}")
                except Exception as e:
                    self.log(f"Audit log failed: {e}")

        self._run_heavy("Applying rename…" if apply else "Previewing rename…", work)

    def _log_findings(self, findings, limit=60):
        """Severity-ranked findings to the log (shared by ERC / DRC / audit)."""
        icon = {"error": "[E]", "warning": "[W]", "info": "[i]", "exclusion": "[-]"}
        for f in findings[:limit]:
            sev = f.get("severity", "warning")
            rule = f.get("rule") or f.get("kind") or ""
            msg = f.get("message") or f.get("detail") or ""
            where = f.get("where") or f.get("ref") or ""
            self.log(f"  {icon.get(sev, '[?]')} {rule}: {msg}" + (f"  @ {where}" if where else ""))
        if len(findings) > limit:
            self.log(f"  … and {len(findings) - limit} more")

    def _run_erc(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "ERC", "No projects selected."); return
        kdir = wiz_find_kicad_cli()
        if not kdir:
            QMessageBox.warning(self, "ERC", "kicad-cli not found (install KiCad)."); return

        def work():
            self.log("\n=== ERC (structured) ===")
            for pro in pros:
                schs = wiz.list_schematics(pro.parent)
                if not schs:
                    continue
                top = pick_root_schematic(schs, pro)
                res = kchecks.run_erc(top, str(kdir))
                if not res["ok"]:
                    self.log(f"ERC {top.name}: {res['error']}")
                    continue
                s = res["summary"]
                self.log(f"ERC {top.name}: {s['errors']} errors, {s['warnings']} warnings, "
                         f"{s['total']} total")
                self._log_findings(res["findings"])

        self._run_heavy("Running ERC…", work)

    def _run_drc(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "DRC", "No projects selected."); return
        kdir = wiz_find_kicad_cli()
        if not kdir:
            QMessageBox.warning(self, "DRC", "kicad-cli not found (install KiCad)."); return

        def work():
            self.log("\n=== DRC (board, structured) ===")
            for pro in pros:
                boards = wiz.list_boards(pro.parent)
                if not boards:
                    self.log(f"DRC {pro.stem}: no .kicad_pcb found")
                    continue
                board = boards[0]
                res = kchecks.run_drc(board, str(kdir))
                if not res["ok"]:
                    self.log(f"DRC {board.name}: {res['error']}")
                    continue
                s = res["summary"]
                self.log(f"DRC {board.name}: {s['errors']} errors, {s['warnings']} warnings, "
                         f"{s['total']} total")
                self._log_findings(res["findings"])

        self._run_heavy("Running DRC…", work)

    def _run_audit(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Audit", "No projects selected."); return

        def work():
            self.log("\n=== Project Health Audit ===")
            for pro in pros:
                schs = wiz.list_schematics(pro.parent)
                if not schs:
                    self.log(f"Audit {pro.stem}: no schematic found")
                    continue
                top = pick_root_schematic(schs, pro)
                # local .pretty footprint libs + 3D models next to the project enable
                # the pin/pad and model checks; schematic-only checks always run.
                fp_dirs = [str(pro.parent)]
                a = phealth.audit_schematic(str(top), footprint_dirs=fp_dirs, model_dirs=fp_dirs)
                s = a["counts"]["by_severity"]
                self.log(f"Audit {a['project']}: {a['healthy']}/{a['components']} healthy — "
                         f"{s['error']} errors, {s['warning']} warnings, {s['info']} notes")
                self._log_findings(a["findings"])

        self._run_heavy("Auditing…", work)

    # ============================================================ FAB STANDARD
    def _open_fab_standard(self):
        """Self-contained dialog: pick an OSH Park preset + which object types to
        conform, preview (writes nothing), then apply (design rules + stackup to the
        project settings; existing text/labels rewritten, each with a .bak backup)."""
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Fab Standard", "No projects selected."); return

        dlg = QDialog(self)
        dlg.setWindowTitle("Fab Standard")
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"Apply a fab standard to {len(pros)} selected project(s)."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Preset:"))
        preset_combo = QComboBox()
        preset_combo.addItems(list(fabp.PRESETS))
        row.addWidget(preset_combo, 1)
        v.addLayout(row)

        chk = {}
        for key, label in (("settings", "Apply design rules + stackup to project settings"),
                           ("netclasses", "Sync matching net-class profile (OSH Park tier)"),
                           ("silk", "Conform component/board SILK text"),
                           ("fab", "Conform FAB-layer text"),
                           ("copper", "Conform COPPER text"),
                           ("sch_text", "Conform schematic text"),
                           ("labels", "Conform net labels")):
            cb = QCheckBox(label)
            cb.setChecked(key in ("settings", "netclasses", "silk", "fab", "labels"))
            chk[key] = cb
            v.addWidget(cb)

        note = QLabel("Preview shows what would change and writes nothing. Apply makes a "
                      ".bak of every file first.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
        v.addWidget(note)

        bb = QDialogButtonBox()
        b_preview = bb.addButton("Preview", QDialogButtonBox.ActionRole)
        b_apply = bb.addButton("Apply (.bak)", QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        v.addWidget(bb)

        def opts():
            return {k: cb.isChecked() for k, cb in chk.items()}

        b_preview.clicked.connect(lambda: self._run_fab_standard(
            pros, fabp.PRESETS[preset_combo.currentText()], opts(), dry_run=True))
        b_apply.clicked.connect(lambda: (self._run_fab_standard(
            pros, fabp.PRESETS[preset_combo.currentText()], opts(), dry_run=False), dlg.accept()))
        bb.rejected.connect(dlg.reject)
        dlg.exec_()

    def _run_fab_standard(self, pros, preset, o, dry_run):
        import datetime
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        pcb_targets = {}
        if o["silk"]:
            pcb_targets["silk"] = (preset.silk_text_height, preset.silk_text_thickness)
        if o["fab"]:
            pcb_targets["fab"] = (preset.fab_text_height, preset.fab_text_thickness)
        if o["copper"]:
            pcb_targets["copper"] = (1.5, 0.3)
        sch_targets = {}
        if o["sch_text"]:
            sch_targets["text"] = (1.27, None)
        if o["labels"]:
            sch_targets["labels"] = (1.27, None)

        def work():
            self.log(f"\n=== Fab Standard: {preset.name} "
                     f"({'preview' if dry_run else 'apply'}) ===")
            self.log(f"  {preset.verify_note}")
            for pro in pros:
                boards = wiz.list_boards(pro.parent)
                schs = wiz.list_schematics(pro.parent)
                top = pick_root_schematic(schs, pro) if schs else None
                # design rules + stackup -> project settings / board
                if o["settings"] and not dry_run:
                    try:
                        from nd_project_settings_manager import ProjectSettingsManager
                        mgr = ProjectSettingsManager()
                        if mgr.load_from_project(pro):
                            mgr.settings = fabp.apply_to_project_settings(mgr.settings, preset)
                            mgr.save_to_project(pro, backup=True)
                            self.log(f"  {pro.stem}: design rules applied ({preset.name})")
                        for b in boards:
                            txt = b.read_text(encoding="utf-8", errors="replace")
                            new, ch = conform.set_board_stackup(txt, preset)
                            if ch:
                                b.with_suffix(b.suffix + f".{stamp}.bak").write_text(txt, encoding="utf-8")
                                b.write_text(new, encoding="utf-8", newline="\n")
                                self.log(f"  {b.name}: stackup applied ({preset.layers}-layer)")
                    except Exception as e:      # noqa: BLE001
                        self.log(f"  {pro.stem}: settings/stackup ERROR: {e}")
                elif o["settings"] and dry_run:
                    self.log(f"  {pro.stem}: would apply {preset.name} rules + "
                             f"{preset.layers}-layer stackup to {len(boards)} board(s)")
                # net-class profile matching the OSH Park tier
                if o.get("netclasses"):
                    if dry_run:
                        m = ncm.create_vault_standard_template(preset.name)
                        self.log(f"  {pro.stem}: would sync {len(m.list_netclasses())} net "
                                 f"classes ({preset.name} profile)")
                    else:
                        try:
                            m = ncm.create_vault_standard_template(preset.name)
                            m.sync_to_projects([pro], backup=True)
                            self.log(f"  {pro.stem}: synced {len(m.list_netclasses())} net "
                                     f"classes ({preset.name} profile)")
                        except Exception as e:      # noqa: BLE001
                            self.log(f"  {pro.stem}: net-class sync ERROR: {e}")
                # conform existing text/labels
                if pcb_targets or sch_targets:
                    files = ([top] if top else []) + list(boards)
                    res = conform.conform_project(files, pcb_targets, sch_targets, stamp, dry_run=dry_run)
                    verb = "would change" if dry_run else "changed"
                    for f in res["files"]:
                        if f["changed"]:
                            self.log(f"  {Path(f['path']).name}: {verb} {f['changed']} "
                                     f"objects {f['counts']}")
                    if not res["total"]:
                        self.log(f"  {pro.stem}: no matching objects to conform")

        self._run_heavy("Applying fab standard…" if not dry_run else "Previewing…", work)

    # ============================================================ NET CLASSES
    def _build_netclass_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        # Two grouped toolbars instead of one 9-button wall: table editing on the
        # left, source/sync on the right.
        bar = uw.toolbar_row()
        for label, icon_name, icon_color, fn, kind in [
            ("Add", "plus", _LU_GREEN, self._nc_add, "default"),
            ("Remove", "trash-2", _LU_RED, self._nc_remove, "ghost"),
            ("Sort by Priority", "sliders-horizontal", _LU_NEUTRAL, self._nc_sort_by_priority, "ghost"),
        ]:
            b = uw.button(label, kind, _lucide(icon_name, icon_color)); b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addStretch()
        for label, icon_name, icon_color, fn, kind in [
            ("Load Vault Standard", "folder-open", _LU_NEUTRAL, self._nc_load_template, "ghost"),
            ("Save as Vault Standard", "save", _LU_AMBER, self._nc_save_vault_standard, "ghost"),
            ("Load from Project", "folder", _LU_NEUTRAL, self._nc_load_project, "ghost"),
            ("Import…", "file-down", _LU_NEUTRAL, self._nc_import, "ghost"),
            ("Export…", "file-up", _LU_NEUTRAL, self._nc_export, "ghost"),
            ("Sync to Selected Projects", "refresh-cw", _LU_BLUE, self._nc_sync, "primary"),
        ]:
            b = uw.button(label, kind, _lucide(icon_name, icon_color)); b.clicked.connect(fn)
            bar.addWidget(b)
        v.addLayout(bar)

        self.nc_table = QTableWidget(0, len(self.NC_COLS))
        self.nc_table.setHorizontalHeaderLabels([c[0] for c in self.NC_COLS])
        hdr = self.nc_table.horizontalHeader()
        for i in range(len(self.NC_COLS) - 1):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(len(self.NC_COLS) - 1, QHeaderView.Stretch)
        self.nc_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.nc_table.cellDoubleClicked.connect(self._nc_cell_clicked)
        self.nc_table.model().rowsInserted.connect(self._nc_sync_empty)
        self.nc_table.model().rowsRemoved.connect(self._nc_sync_empty)
        # table or empty-state, whichever fits
        self._nc_stack = QStackedWidget()
        self._nc_empty = uw.EmptyState(
            "No net classes loaded",
            "Load the vault standard, load from a project, or Add a class to start.")
        self._nc_stack.addWidget(self._nc_empty)
        self._nc_stack.addWidget(self.nc_table)
        v.addWidget(self._nc_stack, 1)
        note = QLabel("Distances / thicknesses in mm. Double-click a Color cell to "
                      "pick. A .bak is written on sync.")
        note.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
        v.addWidget(note)
        self._nc_sync_empty()
        return w

    def _nc_sync_empty(self, *_):
        self._nc_stack.setCurrentIndex(1 if self.nc_table.rowCount() else 0)

    def _nc_set_color_item(self, r, col, hexv):
        hexv = hexv or "#808080"
        it = QTableWidgetItem(hexv)
        qc = QColor(hexv)
        it.setBackground(qc)
        it.setForeground(QColor("#000000") if qc.lightness() > 128 else QColor("#ffffff"))
        self.nc_table.setItem(r, col, it)

    def _nc_make_row(self, r, values: dict):
        for col, (label, attr, kind) in enumerate(self.NC_COLS):
            val = values.get(attr)
            if kind == "color":
                self._nc_set_color_item(r, col, val or "#808080")
            elif kind == "linestyle":
                combo = QComboBox()
                combo.addItems(self.LINE_STYLES)
                combo.setCurrentText(val if val in self.LINE_STYLES else "solid")
                self.nc_table.setCellWidget(r, col, combo)
            elif kind == "patterns":
                txt = ", ".join(val) if isinstance(val, (list, tuple)) else ("" if val is None else str(val))
                self.nc_table.setItem(r, col, QTableWidgetItem(txt))
            else:
                self.nc_table.setItem(r, col, QTableWidgetItem("" if val is None else str(val)))

    def _nc_snapshot_row(self, r) -> dict:
        """Capture a row's literal cell contents (blanks stay blank, the line-style
        combo selection preserved) keyed by NetClass attr. Used to reorder rows
        without round-tripping through NetClassManager, which would collapse
        duplicate names, drop empty-name rows, and back-fill blank numerics."""
        snap = {}
        for col, (label, attr, kind) in enumerate(self.NC_COLS):
            if kind == "linestyle":
                cw = self.nc_table.cellWidget(r, col)
                snap[attr] = cw.currentText() if cw else "solid"
            else:
                it = self.nc_table.item(r, col)
                snap[attr] = it.text() if it else ""
        return snap

    def _nc_sort_by_priority(self):
        """Reorder the existing table rows by their Priority column, losslessly.

        Snapshots each row's raw cell text and re-lays the rows in sorted order,
        so nothing is lost: duplicate names, empty-name rows, and blank cells all
        survive (the old path rebuilt from a name-keyed NetClassManager, which
        collapsed dups, dropped blank-name rows, and back-filled blank numerics
        with defaults)."""
        snaps = sort_netclass_snapshots(
            [self._nc_snapshot_row(r) for r in range(self.nc_table.rowCount())])
        self.nc_table.setRowCount(0)
        for snap in snaps:
            r = self.nc_table.rowCount()
            self.nc_table.insertRow(r)
            self._nc_make_row(r, snap)

    def _nc_set_rows(self, manager: NetClassManager):
        self.nc_table.setRowCount(0)
        # Show net classes sorted by priority (lower number = higher precedence).
        names = sorted(manager.list_netclasses(),
                       key=lambda n: (manager.get_netclass(n).priority, n.lower()))
        for name in names:
            nc = manager.get_netclass(name)
            r = self.nc_table.rowCount()
            self.nc_table.insertRow(r)
            self._nc_make_row(r, {attr: getattr(nc, attr, None) for _, attr, _ in self.NC_COLS})

    def _nc_cell_clicked(self, r, col):
        if self.NC_COLS[col][2] == "color":
            it = self.nc_table.item(r, col)
            cur = QColor(it.text()) if (it and it.text()) else QColor("#808080")
            chosen = QColorDialog.getColor(cur, self, "Net Class Color")
            if chosen.isValid():
                self._nc_set_color_item(r, col, chosen.name())

    def _nc_load_template(self):
        self._nc_set_rows(load_vault_standard())
        self.log("Loaded the vault standard net classes.")

    def _nc_save_vault_standard(self):
        if self.nc_table.rowCount() == 0:
            QMessageBox.information(self, "Vault Standard", "No net classes to save."); return
        reply = QMessageBox.question(
            self, "Save as Vault Standard",
            "Overwrite the vault standard with the current net classes?\n\n"
            "'Load Vault Standard' will load these from now on.",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            path = save_vault_standard(self._nc_manager_from_table())
            self.log(f"Saved current net classes as the vault standard ({path.name}).")
        except Exception as e:
            QMessageBox.warning(self, "Vault Standard", f"Could not save: {e}")

    def _nc_load_project(self):
        pros = self.selected_pro_files()
        if len(pros) != 1:
            QMessageBox.information(
                self, "Net Classes",
                "Select a project first." if not pros else
                f"Select exactly one project to load from ({len(pros)} selected).")
            return
        m = NetClassManager()
        if m.load_from_project(pros[0]):
            self._nc_set_rows(m)
            self.log(f"Loaded net classes from {pros[0].parent.name}.")
        else:
            self.log("Could not load net classes.")

    def _nc_add(self):
        r = self.nc_table.rowCount(); self.nc_table.insertRow(r)
        self._nc_make_row(r, {
            "name": "NewClass", "clearance": 0.127, "track_width": 0.2,
            "via_diameter": 0.8, "via_drill": 0.4,
            "microvia_diameter": None, "microvia_drill": None,
            "diff_pair_width": None, "diff_pair_gap": None, "diff_pair_via_gap": None,
            "wire_thickness": 0.1524, "bus_thickness": 0.3048,
            "color": "#808080", "line_style": "solid", "priority": 0, "patterns": [],
        })

    def _nc_remove(self):
        rows = sorted({i.row() for i in self.nc_table.selectedItems()}, reverse=True)
        for r in rows:
            self.nc_table.removeRow(r)

    def _nc_manager_from_table(self) -> NetClassManager:
        # NetClassManager is name-keyed, so this reconstruction is inherently
        # lossy: blank-name rows can't be represented and same-name rows collapse
        # (last wins). That's acceptable for a real sync/export (KiCad needs a
        # unique name + concrete numbers), but warn so the loss isn't silent.
        m = NetClassManager()
        seen: set = set()
        dropped_empty = 0
        dups: List[str] = []
        for r in range(self.nc_table.rowCount()):
            row = {}
            for col, (label, attr, kind) in enumerate(self.NC_COLS):
                if kind == "linestyle":
                    cw = self.nc_table.cellWidget(r, col)
                    row[attr] = cw.currentText() if cw else "solid"
                else:
                    it = self.nc_table.item(r, col)
                    row[attr] = it.text().strip() if it else ""
            name = row.get("name")
            if not name:
                dropped_empty += 1
                continue
            if name in seen:
                dups.append(name)
            seen.add(name)

            def fnum(key, d):
                s = row.get(key, "")
                try:
                    return float(s) if s != "" else None
                except ValueError:
                    return d
            pats = [p.strip() for p in str(row.get("patterns", "")).split(",") if p.strip()]
            try:
                nc = NetClass(
                    name=row["name"],
                    color=row.get("color") or "#808080",
                    line_style=row.get("line_style") or "solid",
                    wire_thickness=fnum("wire_thickness", 0.1524) or 0.1524,
                    bus_thickness=fnum("bus_thickness", 0.3048) or 0.3048,
                    clearance=fnum("clearance", 0.127) or 0.127,
                    track_width=fnum("track_width", 0.2) or 0.2,
                    via_diameter=fnum("via_diameter", 0.8) or 0.8,
                    via_drill=fnum("via_drill", 0.4) or 0.4,
                    microvia_diameter=fnum("microvia_diameter", 0.3) or 0.3,
                    microvia_drill=fnum("microvia_drill", 0.1) or 0.1,
                    diff_pair_width=fnum("diff_pair_width", None),
                    diff_pair_gap=fnum("diff_pair_gap", None),
                    diff_pair_via_gap=fnum("diff_pair_via_gap", 0.25) or 0.25,
                    priority=int(fnum("priority", 0) or 0),
                    patterns=pats,
                )
                m.add_netclass(nc)
            except Exception:
                self.log(f"Skipping row {r + 1}: invalid value.")
        if dropped_empty:
            self.log(f"Note: skipped {dropped_empty} row(s) with a blank Name.")
        if dups:
            self.log("Note: duplicate net-class name(s) collapsed (last wins): "
                     + ", ".join(sorted(set(dups))))
        return m

    def _nc_sync(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Net Classes", "No projects selected."); return
        m = self._nc_manager_from_table()
        if not m.list_netclasses():
            QMessageBox.warning(self, "Net Classes", "No valid net classes to sync."); return
        if QMessageBox.question(
            self, "Sync Net Classes",
            f"Write {len(m.list_netclasses())} net class(es) into {len(pros)} project(s)?\n"
            f"User-created classes are preserved; a .bak is written.",
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        def work():
            results = m.sync_to_projects(pros, backup=True)
            ok = sum(1 for v in results.values() if v)
            self.log(f"\nNet-class sync: {ok}/{len(pros)} project(s) updated.")
            for pf, good in results.items():
                self.log(f"  {Path(pf).parent.name}: {'updated' if good else 'FAILED'}")
            if m.last_preserved_unmanaged:
                self.log("Preserved unmanaged classes: " + ", ".join(m.last_preserved_unmanaged))

        self._run_heavy("Syncing net classes…", work)

    def _nc_import(self):
        f, _ = QFileDialog.getOpenFileName(self, "Import Net-Class Template", "", "JSON (*.json)")
        if not f:
            return
        m = NetClassManager(); m.import_template(Path(f)); self._nc_set_rows(m)
        self.log(f"Imported template {Path(f).name}.")

    def _nc_export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export Net-Class Template", "netclasses.json", "JSON (*.json)")
        if not f:
            return
        self._nc_manager_from_table().export_template(Path(f))
        self.log(f"Exported template to {f}.")

    # ========================================================= PROJECT SETTINGS
    def _ps_group(self, section, fields, defaults):
        """A section header over a form of mil spin-boxes with live mm read-outs."""
        box = QVBoxLayout(); box.setSpacing(4)
        box.addWidget(uw.SectionHeader(section))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(10)
        for attr, label in fields:
            sp = QDoubleSpinBox()
            sp.setRange(-1000, 100000); sp.setDecimals(2); sp.setSingleStep(1.0)
            sp.setSuffix(" mil")
            sp.setValue(float(getattr(defaults, attr)))
            self.ps_spins[attr] = sp
            mm = QLabel(); mm.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};"); mm.setMinimumWidth(90)

            def _upd(val, lbl=mm):
                lbl.setText(f"= {val * 0.0254:.3f} mm")
            sp.valueChanged.connect(_upd)
            _upd(sp.value())
            fieldw = QWidget()
            fh = QHBoxLayout(fieldw); fh.setContentsMargins(0, 0, 0, 0); fh.setSpacing(10)
            fh.addWidget(sp); fh.addWidget(mm); fh.addStretch()
            form.addRow(label, fieldw)
        box.addLayout(form)
        return box

    def _build_settings_tab(self) -> QWidget:
        outer = QWidget(); ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0); ov.setSpacing(10)
        self.ps_spins = {}
        defaults = ProjectSettings()
        # two columns so the groups fill the width instead of a thin left column
        # against a void: Schematic | (PCB Text Boxes + Footprint Text).
        cols = QHBoxLayout(); cols.setSpacing(40)
        left = QVBoxLayout(); left.setSpacing(10)
        right = QVBoxLayout(); right.setSpacing(10)
        for i, (section, fields) in enumerate(self.PS_GROUPS):
            (left if i == 0 else right).addLayout(self._ps_group(section, fields, defaults))
        left.addStretch(1); right.addStretch(1)
        lw = QWidget(); lw.setLayout(left); lw.setMinimumWidth(320)
        rw = QWidget(); rw.setLayout(right); rw.setMinimumWidth(320)
        cols.addWidget(lw); cols.addWidget(rw); cols.addStretch(1)

        # The scroll now holds a vertical stack: the existing mils groups on top,
        # then the ADDED extended-coverage sections (DRC/ERC severities, text
        # variables, predefined size tables, editable Default net class).
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        colw = QWidget(); content = QVBoxLayout(colw)
        content.setContentsMargins(0, 0, 0, 0); content.setSpacing(16)
        content.addLayout(cols)
        content.addWidget(self._ps_build_extended())
        content.addStretch(1)
        scroll.setWidget(colw)
        ov.addWidget(scroll, 1)

        note = QLabel("Values in mils with the mm equivalent (extended tables are mm). "
                      "Solder-mask/paste also write to each board's .kicad_pcb (setup). "
                      "Grid is per-project (.kicad_prl) and not synced.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
        ov.addWidget(note)

        bar = uw.toolbar_row()
        b_load = uw.button("Load from Project", "default", _lucide("folder", _LU_NEUTRAL))
        b_load.setToolTip("Load the ERC severity settings from a chosen project")
        b_load.clicked.connect(self._ps_load)
        b_sync = uw.button("Sync to Selected Projects", "primary", _lucide("refresh-cw", _LU_BLUE))
        b_sync.setToolTip("Write these ERC severity settings into every selected project")
        b_sync.clicked.connect(self._ps_sync)
        bar.addWidget(b_load); bar.addWidget(b_sync); bar.addStretch()
        ov.addLayout(bar)
        return outer

    # ── extended-coverage sections (DRC/ERC severities, text vars, size
    #    tables, editable Default net class). Built with grayscale QFluentWidgets
    #    components so they match the redesign; themed via apply_grayscale_fluent.
    PS_UNMANAGED = "(inherit)"       # combo sentinel: leave KiCad's value as-is

    def _build_extended_help(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
        return lbl

    def _ps_severity_grid(self, rule_ids, store: dict, cols: int = 2) -> QWidget:
        """A grid of per-rule severity combos (SEVERITY_LEVELS + an inherit
        sentinel). `store` is filled rule_id -> combo. A combo left on the
        sentinel is preserve-by-default: it is not written on sync."""
        w = QWidget(); grid = QGridLayout(w)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14); grid.setVerticalSpacing(4)
        per_col = (len(rule_ids) + cols - 1) // cols
        for idx, rid in enumerate(rule_ids):
            c = idx // per_col
            r = idx % per_col
            lbl = QLabel(rid)
            lbl.setStyleSheet(f"color:{ui_theme.tc('FG_DIM')};")
            combo = _FComboBox()
            combo.addItems([self.PS_UNMANAGED] + list(SEVERITY_LEVELS))
            combo.setCurrentText(self.PS_UNMANAGED)
            combo.setMinimumWidth(110)
            store[rid] = combo
            grid.addWidget(lbl, r, c * 2)
            grid.addWidget(combo, r, c * 2 + 1)
        return w

    def _mk_table(self, headers) -> QTableWidget:
        t = _FTableWidget()             # QFluentWidgets TableWidget takes no (r,c)
        t.setColumnCount(len(headers))
        t.setRowCount(0)
        t.setHorizontalHeaderLabels(list(headers))
        t.verticalHeader().setVisible(False)
        hdr = t.horizontalHeader()
        for i in range(len(headers)):
            hdr.setSectionResizeMode(i, QHeaderView.Stretch)
        t.setMaximumHeight(180)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        try:                                    # QFluentWidgets TableWidget extras
            t.setBorderVisible(True); t.setBorderRadius(6)
        except Exception:
            pass
        return t

    def _table_toolbar(self, add_cb, remove_cb) -> QHBoxLayout:
        bar = uw.toolbar_row()
        b_add = uw.button("Add Row", "default", _lucide("plus", _LU_GREEN))
        b_add.setToolTip("Add a new row to the table")
        b_add.clicked.connect(add_cb)
        b_rm = uw.button("Remove", "ghost", _lucide("trash-2", _LU_RED))
        b_rm.setToolTip("Remove the selected row from the table")
        b_rm.clicked.connect(remove_cb)
        bar.addWidget(b_add); bar.addWidget(b_rm); bar.addStretch()
        return bar

    def _ps_build_extended(self) -> QWidget:
        wrap = QWidget(); v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(16)

        # DRC severities -----------------------------------------------------
        self.drc_combos: dict = {}
        v.addWidget(uw.SectionHeader("DRC Rule Severities"))
        v.addWidget(self._build_extended_help(
            "Per-rule board DRC severity. Leave a rule on '(inherit)' to keep "
            "KiCad's current value (preserve-by-default)."))
        v.addWidget(self._ps_severity_grid(DRC_RULE_IDS, self.drc_combos))

        # ERC severities -----------------------------------------------------
        self.erc_combos: dict = {}
        v.addWidget(uw.SectionHeader("ERC Rule Severities"))
        v.addWidget(self._build_extended_help(
            "Per-rule schematic ERC severity. '(inherit)' leaves the rule as KiCad wrote it."))
        v.addWidget(self._ps_severity_grid(ERC_RULE_IDS, self.erc_combos))

        # Text variables -----------------------------------------------------
        v.addWidget(uw.SectionHeader("Text Variables"))
        v.addWidget(self._build_extended_help(
            "Project text variables (${NAME} substitutions). Name = Value rows."))
        self.tv_table = self._mk_table(["Name", "Value"])
        v.addLayout(self._table_toolbar(self._tv_add, self._tv_remove))
        v.addWidget(self.tv_table)

        # Predefined track widths -------------------------------------------
        v.addWidget(uw.SectionHeader("Predefined Track Widths (mm)"))
        self.tw_table = self._mk_table(["Track Width (mm)"])
        v.addLayout(self._table_toolbar(self._tw_add, self._tw_remove))
        v.addWidget(self.tw_table)

        # Predefined via sizes ----------------------------------------------
        v.addWidget(uw.SectionHeader("Predefined Via Sizes (mm)"))
        self.via_table = self._mk_table(["Via Diameter (mm)", "Via Drill (mm)"])
        v.addLayout(self._table_toolbar(self._via_add, self._via_remove))
        v.addWidget(self.via_table)

        # Predefined diff-pair sizes ----------------------------------------
        v.addWidget(uw.SectionHeader("Predefined Diff-Pair Sizes (mm)"))
        self.dp_table = self._mk_table(["Width (mm)", "Gap (mm)", "Via Gap (mm)"])
        v.addLayout(self._table_toolbar(self._dp_add, self._dp_remove))
        v.addWidget(self.dp_table)

        # Editable Default net class ----------------------------------------
        v.addWidget(uw.SectionHeader("Default Net Class (mm)"))
        v.addWidget(self._build_extended_help(
            "The 'Default' net-class routing values NetClassManager skips. Tick "
            "'manage' to write a field; unticked fields keep KiCad's value."))
        self.dnc_spins: dict = {}
        self.dnc_checks: dict = {}
        dnc_form = QFormLayout()
        dnc_form.setLabelAlignment(Qt.AlignRight); dnc_form.setHorizontalSpacing(10)
        for key, label in (
            ("clearance", "Clearance"), ("track_width", "Track Width"),
            ("via_diameter", "Via Diameter"), ("via_drill", "Via Drill"),
            ("microvia_diameter", "µVia Diameter"), ("microvia_drill", "µVia Drill"),
        ):
            chk = QCheckBox("manage")
            sp = _FDoubleSpinBox()
            sp.setRange(0.0, 1000.0); sp.setDecimals(4); sp.setSingleStep(0.05)
            sp.setSuffix(" mm"); sp.setEnabled(False)
            chk.toggled.connect(sp.setEnabled)
            self.dnc_checks[key] = chk
            self.dnc_spins[key] = sp
            roww = QWidget(); rh = QHBoxLayout(roww)
            rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(10)
            rh.addWidget(chk); rh.addWidget(sp); rh.addStretch(1)
            dnc_form.addRow(label, roww)
        dnc_wrap = QWidget(); dnc_wrap.setLayout(dnc_form)
        v.addWidget(dnc_wrap)
        return wrap

    # ── table row helpers (add / remove) ────────────────────────────────────
    def _tv_add(self):
        r = self.tv_table.rowCount(); self.tv_table.insertRow(r)
        self.tv_table.setItem(r, 0, QTableWidgetItem("VAR"))
        self.tv_table.setItem(r, 1, QTableWidgetItem(""))

    def _tw_add(self):
        r = self.tw_table.rowCount(); self.tw_table.insertRow(r)
        self.tw_table.setItem(r, 0, QTableWidgetItem("0.25"))

    def _via_add(self):
        r = self.via_table.rowCount(); self.via_table.insertRow(r)
        self.via_table.setItem(r, 0, QTableWidgetItem("0.8"))
        self.via_table.setItem(r, 1, QTableWidgetItem("0.4"))

    def _dp_add(self):
        r = self.dp_table.rowCount(); self.dp_table.insertRow(r)
        self.dp_table.setItem(r, 0, QTableWidgetItem("0.2"))
        self.dp_table.setItem(r, 1, QTableWidgetItem("0.15"))
        self.dp_table.setItem(r, 2, QTableWidgetItem("0.25"))

    @staticmethod
    def _table_remove_selected(table: QTableWidget):
        rows = sorted({i.row() for i in table.selectedItems()}, reverse=True)
        for r in rows:
            table.removeRow(r)

    def _tv_remove(self):
        self._table_remove_selected(self.tv_table)

    def _tw_remove(self):
        self._table_remove_selected(self.tw_table)

    def _via_remove(self):
        self._table_remove_selected(self.via_table)

    def _dp_remove(self):
        self._table_remove_selected(self.dp_table)

    # ── cell readers ─────────────────────────────────────────────────────────
    @staticmethod
    def _cell_text(table: QTableWidget, r: int, c: int) -> str:
        it = table.item(r, c)
        return it.text().strip() if it else ""

    @classmethod
    def _cell_float(cls, table: QTableWidget, r: int, c: int):
        """Return the cell's numeric value, or None if blank / non-numeric."""
        s = cls._cell_text(table, r, c)
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _ps_load(self):
        pros = self.selected_pro_files()
        if len(pros) != 1:
            QMessageBox.information(
                self, "Project Settings",
                "Select a project first." if not pros else
                f"Select exactly one project to load from ({len(pros)} selected).")
            return
        m = ProjectSettingsManager()
        if m.load_from_project(pros[0]):
            for attr, sp in self.ps_spins.items():
                sp.setValue(float(getattr(m.settings, attr)))
            self.log(f"Loaded settings from {pros[0].parent.name}.")
            # Extended coverage (DRC/ERC severities, text vars, size tables, the
            # editable Default net class) — read from the same .kicad_pro.
            try:
                if m.load_extended(pros[0]):
                    self._ps_load_extended_into_widgets(m)
                    self.log("Loaded extended board/schematic setup.")
            except Exception as e:
                self.log(f"Extended settings load failed: {e}")
            # Solder-mask/paste physically live in the board's .kicad_pcb (setup)
            # block, not .kicad_pro — pull the REAL values from the board so the
            # two mils spins reflect what KiCad actually uses.
            self._ps_load_board_setup_into_widgets(pros[0])
        else:
            self.log("Could not load project settings.")

    def _ps_load_extended_into_widgets(self, m: ProjectSettingsManager):
        """Populate the extended-coverage widgets from a manager that has just
        run load_extended(). Preserve-by-default: only keys the file actually held
        move a widget off its inherit/blank default."""
        # DRC / ERC severity combos.
        for rid, combo in self.drc_combos.items():
            combo.setCurrentText(m.drc_severities.get(rid, self.PS_UNMANAGED))
        for rid, combo in self.erc_combos.items():
            combo.setCurrentText(m.erc_severities.get(rid, self.PS_UNMANAGED))

        # Text variables.
        self.tv_table.setRowCount(0)
        for name, value in m.text_variables.items():
            r = self.tv_table.rowCount(); self.tv_table.insertRow(r)
            self.tv_table.setItem(r, 0, QTableWidgetItem(str(name)))
            self.tv_table.setItem(r, 1, QTableWidgetItem("" if value is None else str(value)))

        # Predefined track widths (skip KiCad's leading 0.0 = 'use net class').
        self.tw_table.setRowCount(0)
        for w in m.track_widths:
            if w == 0.0:
                continue
            r = self.tw_table.rowCount(); self.tw_table.insertRow(r)
            self.tw_table.setItem(r, 0, QTableWidgetItem(self._fmt_mm(w)))

        # Predefined via sizes (skip the all-zero 'use net class' row).
        self.via_table.setRowCount(0)
        for v in m.via_dimensions:
            if v.diameter == 0.0 and v.drill == 0.0:
                continue
            r = self.via_table.rowCount(); self.via_table.insertRow(r)
            self.via_table.setItem(r, 0, QTableWidgetItem(self._fmt_mm(v.diameter)))
            self.via_table.setItem(r, 1, QTableWidgetItem(self._fmt_mm(v.drill)))

        # Predefined diff-pair sizes (skip the all-zero row).
        self.dp_table.setRowCount(0)
        for d in m.diff_pair_dimensions:
            if d.width == 0.0 and d.gap == 0.0 and d.via_gap == 0.0:
                continue
            r = self.dp_table.rowCount(); self.dp_table.insertRow(r)
            self.dp_table.setItem(r, 0, QTableWidgetItem(self._fmt_mm(d.width)))
            self.dp_table.setItem(r, 1, QTableWidgetItem(self._fmt_mm(d.gap)))
            self.dp_table.setItem(r, 2, QTableWidgetItem(self._fmt_mm(d.via_gap)))

        # Editable Default net class — None means 'not managed' -> untick.
        for key, chk in self.dnc_checks.items():
            val = getattr(m.default_netclass, key, None)
            if val is None:
                chk.setChecked(False)
                self.dnc_spins[key].setValue(0.0)
            else:
                chk.setChecked(True)
                self.dnc_spins[key].setValue(float(val))

    def _ps_load_board_setup_into_widgets(self, pro: Path):
        """Read solder-mask/paste from the project's board .kicad_pcb (setup)
        block and reflect it in the two mils spins (best-effort)."""
        try:
            boards = wiz.list_boards(Path(pro).parent)
        except Exception:
            boards = []
        for brd in boards:
            try:
                setup = board_setup.load_board_setup(brd)
            except Exception:
                continue
            if "solder_mask_clearance" in setup and "solder_mask_clearance" in self.ps_spins:
                self.ps_spins["solder_mask_clearance"].setValue(
                    round(setup["solder_mask_clearance"] / 0.0254, 2))
            if "solder_paste_margin" in setup and "solder_paste_margin" in self.ps_spins:
                self.ps_spins["solder_paste_margin"].setValue(
                    round(setup["solder_paste_margin"] / 0.0254, 2))
            if setup:
                self.log(f"Loaded solder-mask/paste from board {brd.name}.")
                return

    @staticmethod
    def _fmt_mm(v: float) -> str:
        """Format a mm value compactly (trim trailing zeros) for a table cell."""
        s = ("%.6f" % float(v)).rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"

    def _ps_populate_extended(self, m: ProjectSettingsManager):
        """Push the extended-coverage widget state into `m` via its ADDITIVE
        mutator API (set_drc_severity/set_erc_severity/set_text_variable/
        set_track_widths/set_via_dimensions/set_diff_pair_dimensions/
        set_default_netclass). Preserve-by-default: a section with no user data
        is left untouched so a sync never manufactures defaults into a project."""
        for rid, combo in self.drc_combos.items():
            lvl = combo.currentText()
            if lvl in SEVERITY_LEVELS:
                m.set_drc_severity(rid, lvl)
        for rid, combo in self.erc_combos.items():
            lvl = combo.currentText()
            if lvl in SEVERITY_LEVELS:
                m.set_erc_severity(rid, lvl)

        for r in range(self.tv_table.rowCount()):
            name = self._cell_text(self.tv_table, r, 0)
            if name:
                m.set_text_variable(name, self._cell_text(self.tv_table, r, 1))

        tws = [f for f in (self._cell_float(self.tw_table, r, 0)
                           for r in range(self.tw_table.rowCount())) if f is not None]
        if tws:
            m.set_track_widths(tws)

        vias = []
        for r in range(self.via_table.rowCount()):
            d = self._cell_float(self.via_table, r, 0)
            dr = self._cell_float(self.via_table, r, 1)
            if d is not None and dr is not None:
                vias.append((d, dr))
        if vias:
            m.set_via_dimensions(vias)

        dps = []
        for r in range(self.dp_table.rowCount()):
            w = self._cell_float(self.dp_table, r, 0)
            g = self._cell_float(self.dp_table, r, 1)
            vg = self._cell_float(self.dp_table, r, 2)
            if None not in (w, g, vg):
                dps.append((w, g, vg))
        if dps:
            m.set_diff_pair_dimensions(dps)

        kwargs = {key: self.dnc_spins[key].value()
                  for key, chk in self.dnc_checks.items() if chk.isChecked()}
        if kwargs:
            m.set_default_netclass(**kwargs)

    def _ps_write_board_setup(self, pros: List[Path]) -> dict:
        """Write the solder-mask/paste globals to every selected project's board
        .kicad_pcb (setup) block — the place KiCad actually reads them, unlike
        .kicad_pro. Values convert mils -> mm. Returns {board_path: ok}."""
        results: dict = {}
        mask_mm = round(mils_to_mm(self.ps_spins["solder_mask_clearance"].value()), 6)
        paste_mm = round(mils_to_mm(self.ps_spins["solder_paste_margin"].value()), 6)
        values = {"solder_mask_clearance": mask_mm, "solder_paste_margin": paste_mm}
        for pro in pros:
            try:
                boards = wiz.list_boards(Path(pro).parent)
            except Exception:
                boards = []
            for brd in boards:
                try:
                    board_setup.save_board_setup(brd, values, backup=True)
                    got = board_setup.load_board_setup(brd)
                    ok = (abs(got.get("pad_to_mask_clearance", 1e9) - mask_mm) <= 1e-6
                          and abs(got.get("pad_to_paste_clearance", 1e9) - paste_mm) <= 1e-6)
                    results[brd] = ok
                    self.log(f"  board {brd.name}: solder-mask/paste "
                             f"{'written' if ok else 'NOT verified'}")
                except Exception as e:
                    results[brd] = False
                    self.log(f"  board {brd.name}: FAILED {e}")
        return results

    def _ps_sync(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Project Settings", "No projects selected."); return
        if QMessageBox.question(
            self, "Sync Settings",
            f"Apply these drawing defaults + design rules to {len(pros)} project(s)?\n"
            f"A .bak is written next to each.",
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        m = ProjectSettingsManager()
        for attr, sp in self.ps_spins.items():
            val = sp.value()
            # Int-typed fields (e.g. junction_size -> default_junction_size) must
            # not be written as floats; the spin box always yields a float.
            cur = getattr(m.settings, attr, None)
            if isinstance(cur, int) and not isinstance(cur, bool):
                val = int(round(val))
            setattr(m.settings, attr, val)

        # Populate the manager's EXTENDED state from the new widgets BEFORE the
        # sync. sync_to_projects -> save_to_project now also applies+verifies this
        # extended state, so a single sync flushes both the flat drawing settings
        # and DRC/ERC severities, text vars, size tables and the Default class.
        try:
            self._ps_populate_extended(m)
        except Exception as e:
            self.log(f"Extended settings not applied: {e}")

        # Match the other syncs: run off the GUI thread and report per project.
        def work():
            results = m.sync_to_projects(pros, backup=True)
            ok = sum(1 for v in results.values() if v)
            self.log(f"\nProject-settings sync: {ok}/{len(pros)} project(s) updated.")
            details = getattr(m, "last_sync_details", {}) or {}
            for pf in pros:
                why = details.get(pf) or details.get(Path(pf))
                self.log(f"  {Path(pf).parent.name}: {why if why else ('updated' if results.get(pf) else 'not applied')}")
            # Solder-mask/paste globals belong to the .kicad_pcb (setup) block, not
            # .kicad_pro — write them to each project's board so they take effect.
            self.log("Board solder-mask/paste (.kicad_pcb setup):")
            board_results = self._ps_write_board_setup(pros)
            if not board_results:
                self.log("  (no boards found in selected projects)")

        self._run_heavy("Syncing project settings…", work)


def wiz_find_kicad_cli() -> Optional[str]:
    """kicad-cli path — delegates to the shared locator."""
    from kicad_paths import find_kicad_cli
    return find_kicad_cli()
