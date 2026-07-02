"""stm32_pins_tab.py — the 'STM32 Pins' tab: build the CubeMX database, view the
per-socket-position switch decision matrix, and generate the pinout authority.

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Self-contained widget; the main window mounts it as the
third nav tab.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QFileSystemWatcher
from PyQt5.QtGui import QColor, QBrush
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication, QSplitter, QTextEdit,
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


def _5v_suffix(five_v) -> str:
    """Compact 5V-tolerance token for the Tags cell."""
    if not five_v:
        return ""
    if five_v["tolerant"]:
        return " · 5V" + ("(!osc)" if five_v.get("caveat") == "osc-mode" else "")
    if any(five_v["by_family"].values()):
        return " · 5V*part-dep"
    return " · 3V3-only"


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


def _esc(v) -> str:
    return html.escape(str(v))


def _fmt_rng(r, unit="V") -> str:
    return f"{r[0]}–{r[1]} {unit}" if r else "—"


def _pin_detail_html(p: dict) -> str:
    """Full detail for one socket position (pure — unit-testable)."""
    fv = p.get("five_v")
    if fv is None:
        fvt = "n/a (non-GPIO)"
    elif fv["tolerant"]:
        fvt = "5V-tolerant" + (" (except in osc mode)" if fv.get("caveat") == "osc-mode" else "")
    elif any(fv["by_family"].values()):
        fam = ", ".join(f"{k.replace('STM32', '')}={'5V' if v else '3V3'}"
                        for k, v in fv["by_family"].items())
        fvt = f"part-dependent — {fam}"
    else:
        fvt = "3.3V-only"
    bk = p.get("breakout", {})
    bnets = ", ".join(bk.get("service_nets", [])) or "—"
    adg = p["assignment"].get("adg714")
    adg_t = f"ADG714 cell {adg['cell']} ch {adg['channel']}" if adg else "direct"
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or "—"
    el = p.get("electrical", {}) or {}
    rows = [
        ("Name(s)", _counts(p["pin_names"])),
        ("Roles", _counts(p["role_set"])),
        ("Switch", f"{_SWITCH_LABEL.get(p['switch_class'], p['switch_class'])} · {adg_t} → {dest}"),
        ("Breakout", bnets + (" · TRACE" if bk.get("trace") else "")),
        ("Via", bk.get("via", "—")),
        ("Tags", _tag_summary(p["tags"]) or "—"),
        ("5V", fvt),
        ("Bootloader", ", ".join(p["tags"].get("bootloader_periph", [])) or "—"),
        ("Peripherals", ", ".join(p.get("peripherals", [])) or "—"),
        ("VDD", _fmt_rng(el.get("vdd_range_v"))),
    ]
    body = "".join(
        f"<tr><td style='color:#8a93a3;padding-right:8px;vertical-align:top'>{k}</td>"
        f"<td>{_esc(v)}</td></tr>" for k, v in rows)
    return (f"<h3 style='margin:2px 0'>Pin {p['position']} "
            f"<span style='color:#8a93a3'>({p.get('side', '')})</span></h3>"
            f"<table>{body}</table>")


def _summary_html(a: dict) -> str:
    """Package summary card: rollup + electrical + card materials (pure)."""
    r = a["rollup"]
    ea = a.get("extraction_access", {})
    el = a.get("electrical", {})
    cm = a.get("card_materials", {})
    items = "".join(
        f"<tr><td style='text-align:right;padding-right:6px'>{i['qty']}×</td>"
        f"<td>{_esc(i['part'])}</td>"
        f"<td style='color:#8a93a3;padding-left:8px'>{_esc(i['role'])}</td></tr>"
        for i in cm.get("items", []))
    return (
        f"<h3 style='margin:2px 0'>{a['package']} — {a['manifest']['part_count']} parts</h3>"
        f"<p><b>Switch:</b> {r['must_switch_count']} must-switch → {r['cells_min']} ADG714 cells "
        f"(as-built {r['cells_as_built']}); {r['osc_optional_count']} osc-optional; "
        f"{r['fixed_count']} fixed</p>"
        f"<p><b>Breakout:</b> {ea.get('service_breakout_count', 0)} service · "
        f"{len(ea.get('debug_positions', []))} debug · {len(ea.get('trace_positions', []))} trace</p>"
        f"<p><b>Electrical:</b> I/O ±{el.get('max_io_current_ma', '?')} mA · "
        f"inj ±{el.get('injection_current_ma', '?')} mA<br>"
        f"VDD {_fmt_rng(el.get('vdd_range_v'))} · VDDA {_fmt_rng(el.get('vdda_range_v'))} · "
        f"VBAT {_fmt_rng(el.get('vbat_range_v'))} · VREF+ {_fmt_rng(el.get('vref_range_v'))}<br>"
        f"VCAP required: <b>{el.get('vcap_required')}</b></p>"
        f"<p><b>Card materials (passive BOM):</b></p><table>{items}</table>"
        f"<p style='color:#8a93a3'>{_esc(cm.get('note', ''))}</p>")


def _default_vault_authority_dir():
    """The vault's generated-authority folder, if the Brain vault is present."""
    brain = Path.home() / "Documents" / "Obsidian" / "Brain"
    return (brain / "Wiki" / "Datasets" / "STM32 Pinout Authority") if brain.is_dir() else None


class Stm32PinsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path = sdb.default_db_path()
        self.source = sdb.default_cubemx_source()
        self.authority: dict | None = None
        self._building = False

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

        self.btn_vault = QPushButton("Generate → Vault")
        self.btn_vault.setIcon(lucide_icon("file-up", LUCIDE_GREEN))
        self.btn_vault.setToolTip("Write the pinout authority into the Obsidian Brain vault")
        self.btn_vault.clicked.connect(self.generate_to_vault)
        bar.addWidget(self.btn_vault)
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
        self.table.itemSelectionChanged.connect(self._show_detail)

        # ── detail panel (per-pin, or the package summary when nothing is selected) ──
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(300)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # ── live file-watch: reload when the DB is rebuilt on disk ──
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_db_changed)
        self._watcher.directoryChanged.connect(self._on_db_changed)
        self._arm_watch()

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
        self._building = True                       # suppress the file-watcher mid-build
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            res = sdb.build_database(src, self.db_path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._building = False
            QMessageBox.warning(self, "Build Database", f"Build failed:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._building = False
        self._arm_watch()
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
        el = a.get("electrical", {})
        io, inj = el.get("max_io_current_ma"), el.get("injection_current_ma")
        vdda = el.get("vdda_range_v") or el.get("vdd_range_v")
        fv = el.get("five_v_positions", {})
        elec = ""
        if io and vdda:
            elec = f"   |   I/O ±{io} mA · inj ±{inj} mA · VDDA {vdda[0]}–{vdda[1]} V"
            if fv:
                elec += (f" · 5V-tol: {fv.get('tolerant_all_parts', 0)} all-parts / "
                         f"{fv.get('family_dependent', 0)} part-dep / "
                         f"{fv.get('not_tolerant_any_part', 0)} none")
        self.rollup.setWordWrap(True)
        self.rollup.setText(
            f"{a['package']}  —  {a['manifest']['part_count']} parts · {r['positions_total']} positions · "
            f"must-switch {r['must_switch_count']} ({r['cells_min']} ADG714 cells; "
            f"{r['cells_as_built']} incl. osc) · osc-optional {r['osc_optional_count']} · "
            f"fixed {r['fixed_count']}   |   breakout {ea.get('service_breakout_count', 0)} "
            f"(debug {len(ea.get('debug_positions', []))}, trace {len(ea.get('trace_positions', []))})"
            f"{elec}")

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
                _tag_summary(p["tags"]) + _5v_suffix(p.get("five_v")),
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
        self.table.clearSelection()
        self._show_detail()   # no selection → package summary

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
                    p.get("breakout", {}).get("service_nets", []),
                    p.get("peripherals", []))).lower():
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

    # ── detail panel ────────────────────────────────────────────────
    def _show_detail(self):
        if not self.authority:
            self.detail.clear()
            return
        items = self.table.selectedItems()
        if items:
            self.detail.setHtml(_pin_detail_html(self.authority["positions"][items[0].row()]))
        else:
            self.detail.setHtml(_summary_html(self.authority))

    def generate_to_vault(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate → Vault", "Build the database first.")
            return
        vdir = _default_vault_authority_dir()
        if vdir is None:
            out = QFileDialog.getExistingDirectory(self, "Brain vault not found — choose an output folder")
            if not out:
                return
            vdir = Path(out)
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, vdir) for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate → Vault", f"Failed:\n{e}")
            return
        finally:
            conn.close()
        n = sum(len(w["files"]) for w in written)
        self.status.setText(f"Wrote {n} authority files into the vault: {vdir}")
        try:
            os.startfile(str(vdir))  # noqa: S606
        except Exception:
            pass

    # ── live file-watch ─────────────────────────────────────────────
    def _arm_watch(self):
        """(Re)watch the DB file + its dir. QFileSystemWatcher drops a path when
        the file is atomically replaced, so this is called again after a build."""
        for p in (str(self.db_path), str(self.db_path.parent)):
            if p not in self._watcher.files() + self._watcher.directories() and Path(p).exists():
                self._watcher.addPath(p)

    def _on_db_changed(self, _path=None):
        if self._building or not self.db_path.exists():
            return
        self._arm_watch()
        if self.authority:
            self.load(self.pkg_combo.currentText())
