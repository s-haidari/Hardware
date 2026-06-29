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
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from PyQt5.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QComboBox, QCheckBox, QListWidget, QListWidgetItem, QPlainTextEdit, QTabWidget,
    QTableWidget, QTableWidgetItem, QFormLayout, QDoubleSpinBox, QFileDialog,
    QMessageBox, QAbstractItemView, QHeaderView, QSizePolicy, QApplication
)
from PyQt5.QtCore import Qt

import nd_wizard as wiz
from nd_netclass_manager import NetClass, NetClassManager, create_vault_standard_template
from nd_project_settings_manager import ProjectSettings, ProjectSettingsManager


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


class KiCadToolsWidget(QWidget):
    # Net-class table columns (label, NetClass attr, mm?)
    NC_COLS = [
        ("Name", "name"), ("Color", "color"), ("Clearance", "clearance"),
        ("Track", "track_width"), ("Via Ø", "via_diameter"),
        ("Via drill", "via_drill"), ("Priority", "priority"),
    ]
    # Project-settings fields shown (attr, label) — all in mils
    PS_FIELDS = [
        ("schematic_text_size", "Schematic text (mil)"),
        ("schematic_line_width", "Schematic line (mil)"),
        ("pcb_text_size", "PCB text size (mil)"),
        ("pcb_text_thickness", "PCB text thickness (mil)"),
        ("silk_text_size", "Silk text size (mil)"),
        ("silk_text_thickness", "Silk text thickness (mil)"),
        ("copper_text_size", "Copper text size (mil)"),
        ("copper_text_thickness", "Copper text thickness (mil)"),
        ("fab_text_size", "Fab text size (mil)"),
        ("fab_text_thickness", "Fab text thickness (mil)"),
        ("default_clearance", "Default clearance (mil)"),
        ("default_track_width", "Default track width (mil)"),
        ("default_via_diameter", "Default via Ø (mil)"),
        ("default_via_drill", "Default via drill (mil)"),
        ("solder_mask_clearance", "Solder mask clearance (mil)"),
        ("solder_paste_margin", "Solder paste margin (mil)"),
    ]

    def __init__(self, parent, projects_dir: str, save_dir_cb=None):
        super().__init__(parent)
        self._save_dir_cb = save_dir_cb

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(10)

        # --- Projects card ---
        pcard, pl = self._card("KiCad Projects")
        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        self.dir_edit = QLineEdit(projects_dir or "")
        top.addWidget(self.dir_edit, 1)
        b_browse = QPushButton("Browse…"); b_browse.clicked.connect(self._browse)
        b_scan = QPushButton("Rescan"); b_scan.clicked.connect(self.rescan)
        top.addWidget(b_browse); top.addWidget(b_scan)
        pl.addLayout(top)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Check the projects to act on:"))
        row2.addStretch()
        b_all = QPushButton("All"); b_all.clicked.connect(lambda: self._check_all(True))
        b_none = QPushButton("None"); b_none.clicked.connect(lambda: self._check_all(False))
        row2.addWidget(b_all); row2.addWidget(b_none)
        pl.addLayout(row2)

        self.proj_list = QListWidget()
        self.proj_list.setMaximumHeight(120)
        pl.addWidget(self.proj_list)
        root.addWidget(pcard)

        # --- Operations card (tabs) ---
        ocard, ol = self._card("Operations")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_rename_tab(), "Bulk Rename")
        self.tabs.addTab(self._build_netclass_tab(), "Net Classes")
        self.tabs.addTab(self._build_settings_tab(), "Project Settings")
        ol.addWidget(self.tabs)
        root.addWidget(ocard, 1)

        # --- Output card ---
        ccard, cl = self._card("Output")
        self.out = QPlainTextEdit(); self.out.setReadOnly(True); self.out.setMaximumHeight(140)
        cl.addWidget(self.out)
        root.addWidget(ccard)

        self.rescan()

    def _card(self, title: str):
        """A titled card matching the library manager's cards (styled app-wide
        via the QFrame#card / QLabel#cardTitle stylesheet rules)."""
        frame = QFrame(); frame.setObjectName("card")
        v = QVBoxLayout(frame); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        lbl = QLabel(title); lbl.setObjectName("cardTitle")
        v.addWidget(lbl)
        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(8, 6, 8, 8); bl.setSpacing(6)
        v.addWidget(body)
        return frame, bl

    # ---------- helpers ----------
    def log(self, msg: str):
        self.out.appendPlainText(msg)
        self.out.verticalScrollBar().setValue(self.out.verticalScrollBar().maximum())
        QApplication.processEvents()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select KiCad projects folder", self.dir_edit.text() or "")
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
        self.log(f"Found {len(projs)} KiCad project(s) under {path or '(unset)'}")

    def _check_all(self, on: bool):
        for i in range(self.proj_list.count()):
            self.proj_list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

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
        w = QWidget(); v = QVBoxLayout(w)
        form = QHBoxLayout()
        form.addWidget(QLabel("Operation:"))
        self.op_combo = QComboBox()
        self.op_combo.addItems([
            "Add tag prefix", "Remove tag prefix", "Strip all tags",
            "Reset to unannotated (lib_id)", "Custom find / replace",
        ])
        self.op_combo.currentIndexChanged.connect(self._op_changed)
        form.addWidget(self.op_combo, 1)
        v.addLayout(form)

        self.tag_edit = QLineEdit(); self.tag_edit.setPlaceholderText("Tag e.g. SH-  or  CG-")
        self.find_edit = QLineEdit(); self.find_edit.setPlaceholderText("Find text")
        self.repl_edit = QLineEdit(); self.repl_edit.setPlaceholderText("Replace with")
        f2 = QFormLayout()
        f2.addRow("Tag:", self.tag_edit)
        f2.addRow("Find:", self.find_edit)
        f2.addRow("Replace:", self.repl_edit)
        v.addLayout(f2)

        scope = QHBoxLayout()
        self.chk_sch_labels = QCheckBox("Schematic labels/nets"); self.chk_sch_labels.setChecked(True)
        self.chk_sch_refs = QCheckBox("Schematic references"); self.chk_sch_refs.setChecked(True)
        self.chk_pcb_refs = QCheckBox("PCB references"); self.chk_pcb_refs.setChecked(True)
        scope.addWidget(self.chk_sch_labels); scope.addWidget(self.chk_sch_refs); scope.addWidget(self.chk_pcb_refs)
        scope.addStretch()
        v.addLayout(scope)

        btns = QHBoxLayout()
        b_prev = QPushButton("Preview"); b_prev.clicked.connect(lambda: self._run_rename(apply=False))
        b_apply = QPushButton("Apply (creates .bak)"); b_apply.clicked.connect(lambda: self._run_rename(apply=True))
        b_erc = QPushButton("Run ERC (kicad-cli)"); b_erc.clicked.connect(self._run_erc)
        btns.addWidget(b_prev); btns.addWidget(b_apply); btns.addStretch(); btns.addWidget(b_erc)
        v.addLayout(btns)
        v.addStretch()
        self._op_changed()
        return w

    def _op_changed(self):
        idx = self.op_combo.currentIndex()
        self.tag_edit.setVisible(idx in (0, 1))
        self.find_edit.setVisible(idx == 4)
        self.repl_edit.setVisible(idx == 4)

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
                self, "Apply changes",
                f"Apply '{self.op_combo.currentText()}' to {len(pros)} project(s)?\n"
                f"A .bak is written next to every modified file.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return

        totals = {"local": 0, "global": 0, "hier": 0, "sheet_pin": 0, "symbol_ref": 0, "pcb_ref": 0}
        samples = []
        self.log(f"\n=== {'APPLY' if apply else 'PREVIEW'}: {self.op_combo.currentText()} ===")
        for pro in pros:
            proj = pro.parent
            if do_labels or do_refs:
                for sch in wiz.list_schematics(proj):
                    counts, smp, _ = wiz.schematic_preview_and_apply(
                        sch, op, tag_or_find, repl=repl, apply=apply,
                        touch_refs=do_refs, touch_labels=do_labels)
                    for k in totals:
                        totals[k] += counts.get(k, 0)
                    samples += smp
            if do_pcb:
                for brd in wiz.list_boards(proj):
                    cnt, smp, _ = wiz.pcb_preview_and_apply(brd, op, tag_or_find, repl=repl, apply=apply)
                    totals["pcb_ref"] += cnt
                    samples += smp
        self.log(f"Schematic labels: {totals['local']+totals['global']+totals['hier']+totals['sheet_pin']}  "
                 f"| Schematic refs: {totals['symbol_ref']}  | PCB refs: {totals['pcb_ref']}")
        for s in samples[:15]:
            self.log("  " + s)
        if not any(totals.values()):
            self.log("  No matching changes.")
        elif apply:
            self.log("Applied. Backups (.bak) created next to modified files.")

    def _run_erc(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "ERC", "No projects selected."); return
        kdir = wiz_find_kicad_cli()
        if not kdir:
            QMessageBox.warning(self, "ERC", "kicad-cli not found (install KiCad)."); return
        self.log("\n=== ERC (kicad-cli) ===")
        for pro in pros:
            schs = wiz.list_schematics(pro.parent)
            if not schs:
                continue
            top = schs[0]
            self.log(f"ERC: {top.name}")
            try:
                proc = subprocess.run([str(kdir), "sch", "erc", str(top)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", creationflags=_NO_WINDOW)
                for line in (proc.stdout or "").splitlines()[-12:]:
                    self.log("  " + line)
            except Exception as e:
                self.log(f"  ERROR: {e}")

    # ============================================================ NET CLASSES
    def _build_netclass_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        bar = QHBoxLayout()
        for label, fn in [
            ("Load vault template", self._nc_load_template),
            ("Load from project", self._nc_load_project),
            ("Add", self._nc_add), ("Remove", self._nc_remove),
            ("Import…", self._nc_import), ("Export…", self._nc_export),
        ]:
            b = QPushButton(label); b.clicked.connect(fn); bar.addWidget(b)
        bar.addStretch()
        b_sync = QPushButton("Sync to selected projects"); b_sync.clicked.connect(self._nc_sync)
        bar.addWidget(b_sync)
        v.addLayout(bar)

        self.nc_table = QTableWidget(0, len(self.NC_COLS))
        self.nc_table.setHorizontalHeaderLabels([c[0] for c in self.NC_COLS])
        self.nc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(self.nc_table, 1)
        v.addWidget(QLabel("Track/clearance/via values are in mm. A .bak is written on sync."))
        return w

    def _nc_set_rows(self, manager: NetClassManager):
        names = manager.list_netclasses()
        self.nc_table.setRowCount(0)
        for name in names:
            nc = manager.get_netclass(name)
            r = self.nc_table.rowCount()
            self.nc_table.insertRow(r)
            vals = [nc.name, nc.color, nc.clearance, nc.track_width,
                    nc.via_diameter, nc.via_drill, nc.priority]
            for col, val in enumerate(vals):
                self.nc_table.setItem(r, col, QTableWidgetItem(str(val)))

    def _nc_load_template(self):
        self._nc_set_rows(create_vault_standard_template())
        self.log("Loaded vault-standard net classes.")

    def _nc_load_project(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Net Classes", "Select a project first."); return
        m = NetClassManager()
        if m.load_from_project(pros[0]):
            self._nc_set_rows(m)
            self.log(f"Loaded net classes from {pros[0].parent.name}.")
        else:
            self.log("Could not load net classes.")

    def _nc_add(self):
        r = self.nc_table.rowCount(); self.nc_table.insertRow(r)
        for col, val in enumerate(["NewClass", "#808080", "0.127", "0.2", "0.8", "0.4", "0"]):
            self.nc_table.setItem(r, col, QTableWidgetItem(val))

    def _nc_remove(self):
        rows = sorted({i.row() for i in self.nc_table.selectedItems()}, reverse=True)
        for r in rows:
            self.nc_table.removeRow(r)

    def _nc_manager_from_table(self) -> NetClassManager:
        m = NetClassManager()
        for r in range(self.nc_table.rowCount()):
            def cell(c, d=""):
                it = self.nc_table.item(r, c)
                return it.text().strip() if it and it.text().strip() else d
            name = cell(0)
            if not name:
                continue
            try:
                nc = NetClass(
                    name=name, color=cell(1, "#808080"),
                    clearance=float(cell(2, "0.127")), track_width=float(cell(3, "0.2")),
                    via_diameter=float(cell(4, "0.8")), via_drill=float(cell(5, "0.4")),
                    priority=int(float(cell(6, "0"))))
                m.add_netclass(nc)
            except ValueError:
                self.log(f"Skipping row {r+1}: invalid number.")
        return m

    def _nc_sync(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Net Classes", "No projects selected."); return
        m = self._nc_manager_from_table()
        if not m.list_netclasses():
            QMessageBox.warning(self, "Net Classes", "No valid net classes to sync."); return
        if QMessageBox.question(
            self, "Sync net classes",
            f"Write {len(m.list_netclasses())} net class(es) into {len(pros)} project(s)?\n"
            f"User-created classes are preserved; a .bak is written.",
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        results = m.sync_to_projects(pros, backup=True)
        ok = sum(1 for v in results.values() if v)
        self.log(f"\nNet-class sync: {ok}/{len(pros)} project(s) updated.")
        if m.last_preserved_unmanaged:
            self.log("Preserved unmanaged classes: " + ", ".join(m.last_preserved_unmanaged))

    def _nc_import(self):
        f, _ = QFileDialog.getOpenFileName(self, "Import net-class template", "", "JSON (*.json)")
        if not f:
            return
        m = NetClassManager(); m.import_template(Path(f)); self._nc_set_rows(m)
        self.log(f"Imported template {Path(f).name}.")

    def _nc_export(self):
        f, _ = QFileDialog.getSaveFileName(self, "Export net-class template", "netclasses.json", "JSON (*.json)")
        if not f:
            return
        self._nc_manager_from_table().export_template(Path(f))
        self.log(f"Exported template to {f}.")

    # ========================================================= PROJECT SETTINGS
    def _build_settings_tab(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        bar = QHBoxLayout()
        b_load = QPushButton("Load from project"); b_load.clicked.connect(self._ps_load)
        bar.addWidget(b_load); bar.addStretch()
        b_sync = QPushButton("Sync to selected projects"); b_sync.clicked.connect(self._ps_sync)
        bar.addWidget(b_sync)
        v.addLayout(bar)

        form = QFormLayout()
        self.ps_spins = {}
        defaults = ProjectSettings()
        for attr, label in self.PS_FIELDS:
            sp = QDoubleSpinBox(); sp.setRange(-1000, 100000); sp.setDecimals(2); sp.setSingleStep(1.0)
            sp.setValue(float(getattr(defaults, attr)))
            self.ps_spins[attr] = sp
            form.addRow(label, sp)
        v.addLayout(form)
        v.addWidget(QLabel("Values are in mils (KiCad's drawing default unit). Grid is per-project (.kicad_prl) and not synced."))
        v.addStretch()
        return w

    def _ps_load(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Project Settings", "Select a project first."); return
        m = ProjectSettingsManager()
        if m.load_from_project(pros[0]):
            for attr, sp in self.ps_spins.items():
                sp.setValue(float(getattr(m.settings, attr)))
            self.log(f"Loaded settings from {pros[0].parent.name}.")
        else:
            self.log("Could not load project settings.")

    def _ps_sync(self):
        pros = self.selected_pro_files()
        if not pros:
            QMessageBox.information(self, "Project Settings", "No projects selected."); return
        if QMessageBox.question(
            self, "Sync settings",
            f"Apply these drawing defaults + design rules to {len(pros)} project(s)?\n"
            f"A .bak is written next to each.",
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        m = ProjectSettingsManager()
        for attr, sp in self.ps_spins.items():
            setattr(m.settings, attr, sp.value())
        results = m.sync_to_projects(pros, backup=True)
        ok = sum(1 for v in results.values() if v)
        self.log(f"\nProject-settings sync: {ok}/{len(pros)} project(s) updated.")


def wiz_find_kicad_cli() -> Optional[str]:
    """Locate kicad-cli.exe next to a KiCad install, or on PATH."""
    import glob as _glob
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe"):
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[-1]
    from shutil import which
    return which("kicad-cli")
