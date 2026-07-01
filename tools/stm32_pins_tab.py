"""stm32_pins_tab.py — the 'STM32 Pins' tab: build the CubeMX database, view the
per-socket-position switch decision matrix, and generate the pinout authority.

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Self-contained widget; the main window mounts it as the
third nav tab.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication,
)

import stm32_db as sdb
import stm32_authority as sauth

try:
    from LibraryManager import (lucide_icon, LUCIDE_NEUTRAL, LUCIDE_BLUE,
                                LUCIDE_GREEN, LUCIDE_AMBER)
    _HAVE_LUCIDE = True
except Exception:  # pragma: no cover
    _HAVE_LUCIDE = False
    LUCIDE_NEUTRAL = LUCIDE_BLUE = LUCIDE_GREEN = LUCIDE_AMBER = ""

    def lucide_icon(*_a, **_k):
        from PyQt5.QtGui import QIcon
        return QIcon()


_COLS = ["Pin", "Side", "Pin Name(s)", "Role Set", "Switch", "ADG714",
         "Destination", "Breakout", "Tags", "Bootloader", "V(dd)"]

_BREAKOUT_COLOR = "#b57edc"   # extraction-access / debug-service breakout (violet)

_SWITCH_COLOR = {
    sdb.SWITCH_MUST: "#cc5b5b",
    sdb.SWITCH_OSC_OPTIONAL: "#c99a2e",
    sdb.SWITCH_NONE: "#8a93a3",
}
_SWITCH_LABEL = {
    sdb.SWITCH_MUST: "must switch",
    sdb.SWITCH_OSC_OPTIONAL: "osc optional",
    sdb.SWITCH_NONE: "fixed",
}


def _counts(d: dict) -> str:
    return ", ".join(f"{k}×{v}" for k, v in d.items())


def _tag_summary(tags: dict) -> str:
    out = []
    if tags.get("is_debug"):
        out.append("DEBUG:" + "/".join(tags.get("debug_role", [])))
    if tags.get("is_boot"):
        out.append("BOOT")
    if tags.get("is_clock"):
        out.append("CLK")
    if tags.get("is_core_power"):
        out.append("VCAP")
    if tags.get("is_analog_supply"):
        out.append("VDDA/VREF")
    if tags.get("is_trace"):
        out.append("TRACE")
    return " · ".join(out)


class Stm32PinsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path = sdb.default_db_path()
        self.source = sdb.default_cubemx_source()
        self.authority: dict | None = None

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── controls ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Package:"))
        self.pkg_combo = QComboBox()
        self.pkg_combo.addItems(["LQFP64", "LQFP100"])
        self.pkg_combo.currentTextChanged.connect(lambda p: self.load(p))
        bar.addWidget(self.pkg_combo)

        self.btn_build = QPushButton("Build Database")
        self.btn_build.setIcon(lucide_icon("wrench", LUCIDE_AMBER))
        self.btn_build.clicked.connect(self.build_database)
        bar.addWidget(self.btn_build)

        self.btn_gen = QPushButton("Generate Authority")
        self.btn_gen.setIcon(lucide_icon("save", LUCIDE_GREEN))
        self.btn_gen.clicked.connect(self.generate)
        bar.addWidget(self.btn_gen)
        bar.addStretch()

        bar.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Must switch", "Osc optional", "Fixed"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        bar.addWidget(self.filter_combo)
        bar.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setMaximumWidth(220)
        self.search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search)
        root.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("headerStatus")
        root.addWidget(self.status)
        self.rollup = QLabel("")
        f = self.rollup.font()
        f.setBold(True)
        self.rollup.setFont(f)
        root.addWidget(self.rollup)

        # ── matrix ─────────────────────────────────────────────────
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        for i in range(len(_COLS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.table, 1)

        self._load_if_ready()

    # ── data ───────────────────────────────────────────────────────
    def _load_if_ready(self):
        if self.db_path.exists():
            self.load(self.pkg_combo.currentText())
        else:
            src = self.source if self.source else "not found"
            self.status.setText(f"No database yet. CubeMX source: {src}. Click 'Build Database'.")

    def _pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select the CubeMX 'mcu' XML folder",
                                             str(self.source or ""))
        return d or None

    def build_database(self):
        src = self.source or self._pick_source()
        if not src:
            return
        self.status.setText("Building database from CubeMX XML…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            res = sdb.build_database(src, self.db_path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Build Database", f"Build failed:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.source = src
        lq = ", ".join(f"{k}={v}" for k, v in sorted(res.packages.items()) if k.startswith("LQFP"))
        self.status.setText(f"Built {res.mcus} STM32F MCUs, {res.pins} pins, {res.roles} roles "
                            f"from {src}  —  {lq}")
        self.load(self.pkg_combo.currentText())

    def load(self, package: str):
        if not self.db_path.exists():
            return
        conn = sdb.connect(self.db_path)
        try:
            self.authority = sauth.build(conn, package)
        except Exception as e:
            QMessageBox.warning(self, "Load", f"Could not read the database:\n{e}")
            return
        finally:
            conn.close()
        self._populate()

    def _populate(self):
        a = self.authority
        if not a:
            return
        r = a["rollup"]
        ea = a.get("extraction_access", {})
        self.rollup.setText(
            f"{a['package']}  —  {a['manifest']['part_count']} parts · {r['positions_total']} positions · "
            f"must-switch {r['must_switch_count']} ({r['cells_min']} ADG714 cells; "
            f"{r['cells_as_built']} incl. osc) · osc-optional {r['osc_optional_count']} · "
            f"fixed {r['fixed_count']}   |   breakout {ea.get('service_breakout_count', 0)} "
            f"(debug {len(ea.get('debug_positions', []))}, trace {len(ea.get('trace_positions', []))})")

        rows = a["positions"]
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            sc = p["switch_class"]
            adg = p["assignment"].get("adg714")
            adg_txt = f"cell {adg['cell']} · ch {adg['channel']}" if adg else "—"
            dest = (p["assignment"].get("destination") or p["assignment"].get("net") or "—")
            bk = p.get("breakout", {})
            bnets = bk.get("service_nets", [])
            btxt = ", ".join(bnets)
            if bk.get("trace"):
                btxt = (btxt + " · TRACE") if btxt else "TRACE"
            cells = [
                str(p["position"]),
                p.get("side", ""),
                _counts(p["pin_names"]),
                _counts(p["role_set"]),
                _SWITCH_LABEL.get(sc, sc),
                adg_txt,
                dest,
                btxt or "—",
                _tag_summary(p["tags"]),
                ", ".join(p["tags"].get("bootloader_periph", [])),
                (lambda e: f"{e['vdd_range_v'][0]}–{e['vdd_range_v'][1]}"
                 if e and e.get("vdd_range_v") else "")(p.get("electrical")),
            ]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c == 4:  # switch class — colour it
                    it.setForeground(QBrush(QColor(_SWITCH_COLOR.get(sc, "#8a93a3"))))
                elif c == 7 and (bnets or bk.get("trace")):  # breakout — violet
                    it.setForeground(QBrush(QColor(_BREAKOUT_COLOR)))
                self.table.setItem(i, c, it)
        self._apply_filter()

    def _apply_filter(self):
        want = self.filter_combo.currentText()
        q = self.search.text().strip().lower()
        want_class = {
            "Must switch": sdb.SWITCH_MUST,
            "Osc optional": sdb.SWITCH_OSC_OPTIONAL,
            "Fixed": sdb.SWITCH_NONE,
        }.get(want)
        rows = self.authority["positions"] if self.authority else []
        for i, p in enumerate(rows):
            hide = False
            if want_class and p["switch_class"] != want_class:
                hide = True
            if q and q not in " ".join(str(v) for v in (
                    p["position"], p["pin_names"], p["role_set"],
                    p["tags"].get("bootloader_periph", []), _tag_summary(p["tags"]),
                    p.get("breakout", {}).get("service_nets", []))).lower():
                hide = True
            self.table.setRowHidden(i, hide)

    def generate(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate", "Build the database first.")
            return
        out = QFileDialog.getExistingDirectory(self, "Choose output folder for the pinout authority")
        if not out:
            return
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, __import__("pathlib").Path(out))
                       for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate", f"Generate failed:\n{e}")
            return
        finally:
            conn.close()
        files = [f for w in written for f in w["files"]]
        self.status.setText(f"Wrote {len(files)} files to {out}: " + ", ".join(files))
        try:
            os.startfile(out)  # noqa: S606
        except Exception:
            pass
