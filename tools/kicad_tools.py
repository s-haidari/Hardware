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
    QMessageBox, QAbstractItemView, QHeaderView, QSizePolicy, QApplication,
    QColorDialog, QScrollArea, QToolButton, QMenu, QWidgetAction
)
from PyQt5.QtGui import QColor
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
        ("DP Width", "diff_pair_width", "num"),
        ("DP Gap", "diff_pair_gap", "num"),
        ("DP Via Gap", "diff_pair_via_gap", "num"),
        ("Wire Thickness", "wire_thickness", "num"),
        ("Bus Thickness", "bus_thickness", "num"),
        ("Color", "color", "color"),
        ("Line Style", "line_style", "linestyle"),
        ("Priority", "priority", "num"),
        ("Patterns (net globs)", "patterns", "patterns"),
    ]
    # Project settings grouped like KiCad's dialog (all values in mils).
    PS_GROUPS = [
        ("Schematic", [
            ("schematic_text_size", "Text size"),
            ("schematic_line_width", "Line width"),
            ("pin_symbol_size", "Pin symbol size"),
            ("junction_size", "Junction size"),
        ]),
        ("PCB Text Boxes", [
            ("pcb_text_size", "Text size"),
            ("pcb_text_thickness", "Text thickness"),
        ]),
        ("Footprint Text", [
            ("silk_text_size", "Silkscreen size"),
            ("silk_text_thickness", "Silkscreen thickness"),
            ("copper_text_size", "Copper size"),
            ("copper_text_thickness", "Copper thickness"),
            ("fab_text_size", "Fab size"),
            ("fab_text_thickness", "Fab thickness"),
        ]),
        ("Design Rules (defaults)", [
            ("default_clearance", "Clearance"),
            ("default_track_width", "Track width"),
            ("default_via_diameter", "Via diameter"),
            ("default_via_drill", "Via drill"),
        ]),
        ("Minimum Constraints", [
            ("min_via_diameter", "Min via diameter"),
            ("min_via_annular_width", "Min via annular ring"),
            ("min_through_hole", "Min through-hole"),
            ("min_hole_to_hole", "Min hole-to-hole"),
            ("min_hole_clearance", "Min hole clearance"),
            ("min_microvia_diameter", "Min µvia diameter"),
            ("min_microvia_drill", "Min µvia drill"),
            ("min_copper_edge_clearance", "Min copper-to-edge"),
            ("min_silk_clearance", "Min silk clearance"),
        ]),
        ("Solder Mask / Paste", [
            ("solder_mask_clearance", "Mask clearance"),
            ("solder_paste_margin", "Paste margin"),
        ]),
    ]

    def __init__(self, parent, projects_dir: str, save_dir_cb=None):
        super().__init__(parent)
        self._save_dir_cb = save_dir_cb

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(10)

        # --- Projects card: folder + a compact multi-select dropdown ---
        pcard, pl = self._card("KICAD Projects")
        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        self.dir_edit = QLineEdit(projects_dir or "")
        top.addWidget(self.dir_edit, 1)
        b_browse = QPushButton("Browse…"); b_browse.clicked.connect(self._browse)
        b_scan = QPushButton("Rescan"); b_scan.clicked.connect(self.rescan)
        top.addWidget(b_browse); top.addWidget(b_scan)
        pl.addLayout(top)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Apply to:"))
        self.proj_btn = QToolButton()
        self.proj_btn.setPopupMode(QToolButton.InstantPopup)
        self.proj_btn.setText("No projects")
        self.proj_btn.setMinimumWidth(220)
        self.proj_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        proj_menu = QMenu(self.proj_btn)
        picker = QWidget()
        pv = QVBoxLayout(picker); pv.setContentsMargins(8, 8, 8, 8); pv.setSpacing(6)
        hb = QHBoxLayout()
        b_all = QPushButton("All"); b_all.clicked.connect(lambda: self._check_all(True))
        b_none = QPushButton("None"); b_none.clicked.connect(lambda: self._check_all(False))
        hb.addWidget(b_all); hb.addWidget(b_none); hb.addStretch()
        pv.addLayout(hb)
        self.proj_list = QListWidget()
        self.proj_list.setMinimumWidth(340)
        self.proj_list.setMinimumHeight(140)
        self.proj_list.setMaximumHeight(280)
        self.proj_list.itemChanged.connect(lambda *_: self._update_selection())
        pv.addWidget(self.proj_list)
        wa = QWidgetAction(proj_menu); wa.setDefaultWidget(picker)
        proj_menu.addAction(wa)
        self.proj_btn.setMenu(proj_menu)
        row2.addWidget(self.proj_btn)
        row2.addStretch()
        pl.addLayout(row2)
        root.addWidget(pcard)

        # --- Operations card (fills the rest) ---
        ocard, ol = self._card("Operations")
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_rename_tab(), "Bulk Rename")
        self.tabs.addTab(self._build_netclass_tab(), "Net Classes")
        self.tabs.addTab(self._build_settings_tab(), "Project Settings")
        ol.addWidget(self.tabs)
        root.addWidget(ocard, 1)

        # --- Output (full width, short) ---
        ccard, cl = self._card("Output")
        self.out = QPlainTextEdit(); self.out.setReadOnly(True); self.out.setMaximumHeight(110)
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
        d = QFileDialog.getExistingDirectory(self, "Select KICAD projects folder", self.dir_edit.text() or "")
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
        self.log(f"Found {len(projs)} KICAD project(s) under {path or '(unset)'}")

    def _update_selection(self):
        total = self.proj_list.count()
        sel = sum(1 for i in range(total)
                  if self.proj_list.item(i).checkState() == Qt.Checked)
        if total == 0:
            self.proj_btn.setText("No Projects")
        elif sel == total:
            self.proj_btn.setText(f"All {total} Projects")
        else:
            self.proj_btn.setText(f"{sel} of {total} Selected")

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
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.op_combo = QComboBox()
        self.op_combo.addItems([
            "Add tag prefix", "Remove tag prefix", "Strip all tags",
            "Reset to unannotated (lib_id)", "Custom find / replace",
        ])
        self.op_combo.currentIndexChanged.connect(self._op_changed)
        form.addRow("Operation:", self.op_combo)

        self.tag_edit = QLineEdit(); self.tag_edit.setPlaceholderText("e.g.  SH-   or   CG-")
        self.find_edit = QLineEdit(); self.find_edit.setPlaceholderText("Find text")
        self.repl_edit = QLineEdit(); self.repl_edit.setPlaceholderText("Replace with")
        form.addRow("Tag:", self.tag_edit)
        form.addRow("Find:", self.find_edit)
        form.addRow("Replace:", self.repl_edit)
        self._rename_form = form
        v.addLayout(form)

        scope_box = QFrame(); scope_box.setObjectName("card")
        sb = QVBoxLayout(scope_box); sb.setContentsMargins(10, 8, 10, 8); sb.setSpacing(4)
        sb.addWidget(QLabel("Scope"))
        self.chk_sch_labels = QCheckBox("Schematic labels / nets"); self.chk_sch_labels.setChecked(True)
        self.chk_sch_refs = QCheckBox("Schematic references"); self.chk_sch_refs.setChecked(True)
        self.chk_pcb_refs = QCheckBox("PCB references"); self.chk_pcb_refs.setChecked(True)
        sb.addWidget(self.chk_sch_labels); sb.addWidget(self.chk_sch_refs); sb.addWidget(self.chk_pcb_refs)
        v.addWidget(scope_box)

        v.addStretch(1)

        btns = QHBoxLayout()
        b_prev = QPushButton("Preview"); b_prev.clicked.connect(lambda: self._run_rename(apply=False))
        b_apply = QPushButton("Apply  (Creates .bak)"); b_apply.clicked.connect(lambda: self._run_rename(apply=True))
        b_erc = QPushButton("Run ERC"); b_erc.clicked.connect(self._run_erc)
        btns.addWidget(b_prev); btns.addWidget(b_apply); btns.addStretch(); btns.addWidget(b_erc)
        v.addLayout(btns)
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
            QMessageBox.warning(self, "ERC", "kicad-cli not found (install KICAD)."); return
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
            ("Load Vault Template", self._nc_load_template),
            ("Load from Project", self._nc_load_project),
            ("Add", self._nc_add), ("Remove", self._nc_remove),
            ("Import…", self._nc_import), ("Export…", self._nc_export),
        ]:
            b = QPushButton(label); b.clicked.connect(fn); bar.addWidget(b)
        bar.addStretch()
        b_sync = QPushButton("Sync to Selected Projects"); b_sync.clicked.connect(self._nc_sync)
        bar.addWidget(b_sync)
        v.addLayout(bar)

        self.nc_table = QTableWidget(0, len(self.NC_COLS))
        self.nc_table.setHorizontalHeaderLabels([c[0] for c in self.NC_COLS])
        # Size every column to its content so text is never truncated; the table
        # scrolls horizontally instead of forcing the window wider.
        self.nc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.nc_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.nc_table.cellDoubleClicked.connect(self._nc_cell_clicked)
        v.addWidget(self.nc_table, 1)
        v.addWidget(QLabel("Distances/thicknesses in mm. Double-click a Color cell to "
                           "pick. A .bak is written on sync."))
        return w

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

    def _nc_set_rows(self, manager: NetClassManager):
        self.nc_table.setRowCount(0)
        for name in manager.list_netclasses():
            nc = manager.get_netclass(name)
            r = self.nc_table.rowCount()
            self.nc_table.insertRow(r)
            self._nc_make_row(r, {attr: getattr(nc, attr, None) for _, attr, _ in self.NC_COLS})

    def _nc_cell_clicked(self, r, col):
        if self.NC_COLS[col][2] == "color":
            it = self.nc_table.item(r, col)
            cur = QColor(it.text()) if (it and it.text()) else QColor("#808080")
            chosen = QColorDialog.getColor(cur, self, "Net class color")
            if chosen.isValid():
                self._nc_set_color_item(r, col, chosen.name())

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
        self._nc_make_row(r, {
            "name": "NewClass", "clearance": 0.127, "track_width": 0.2,
            "via_diameter": 0.8, "via_drill": 0.4, "diff_pair_width": None,
            "diff_pair_gap": None, "wire_thickness": 0.1524, "bus_thickness": 0.3048,
            "color": "#808080", "line_style": "solid", "priority": 0,
        })

    def _nc_remove(self):
        rows = sorted({i.row() for i in self.nc_table.selectedItems()}, reverse=True)
        for r in rows:
            self.nc_table.removeRow(r)

    def _nc_manager_from_table(self) -> NetClassManager:
        m = NetClassManager()
        for r in range(self.nc_table.rowCount()):
            row = {}
            for col, (label, attr, kind) in enumerate(self.NC_COLS):
                if kind == "linestyle":
                    cw = self.nc_table.cellWidget(r, col)
                    row[attr] = cw.currentText() if cw else "solid"
                else:
                    it = self.nc_table.item(r, col)
                    row[attr] = it.text().strip() if it else ""
            if not row.get("name"):
                continue

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
        outer = QWidget(); ov = QVBoxLayout(outer)
        bar = QHBoxLayout()
        b_load = QPushButton("Load from Project"); b_load.clicked.connect(self._ps_load)
        bar.addWidget(b_load); bar.addStretch()
        b_sync = QPushButton("Sync to Selected Projects"); b_sync.clicked.connect(self._ps_sync)
        bar.addWidget(b_sync)
        ov.addLayout(bar)

        # Grouped like KiCad's dialog, in a scroll area so it never squishes.
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); form = QFormLayout(inner)
        form.setLabelAlignment(Qt.AlignRight)
        self.ps_spins = {}
        defaults = ProjectSettings()
        for section, fields in self.PS_GROUPS:
            hdr = QLabel(section)
            hdr.setStyleSheet("font-weight: 800; padding-top: 8px;")
            form.addRow(hdr)
            for attr, label in fields:
                sp = QDoubleSpinBox()
                sp.setRange(-1000, 100000); sp.setDecimals(2); sp.setSingleStep(1.0)
                sp.setSuffix(" mil")
                sp.setValue(float(getattr(defaults, attr)))
                self.ps_spins[attr] = sp
                form.addRow(label, sp)
        scroll.setWidget(inner)
        ov.addWidget(scroll, 1)
        ov.addWidget(QLabel("Values are in mils. Grid is per-project (.kicad_prl) and not synced."))
        return outer

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
    """Locate kicad-cli.exe next to a KICAD install, or on PATH."""
    import glob as _glob
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe"):
        hits = sorted(_glob.glob(pat))
        if hits:
            return hits[-1]
    from shutil import which
    return which("kicad-cli")
