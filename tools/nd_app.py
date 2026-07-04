#!/usr/bin/env python3
"""nd_app.py — NETDECK, rebuilt ground-up on QFluentWidgets.

A grayscale FluentWindow shell over the HARDENED logic layer (stm32_authority/db,
the nd_* managers, LibraryManager's pure library/associate/git helpers, nd_git).
This replaces the old QMainWindow shell. The STM32 Pins and KiCad Tools tabs are
the existing standalone widgets (driven through a Fluent TabContext); the KiCad
Manager tab is rebuilt here on native Fluent components.

Run:  python -m nd_app        (or `python nd_app.py`)
"""
from __future__ import annotations

import glob
import os
import sys
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtGui import QFontDatabase, QFont, QPalette, QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFrame, QApplication,
                             QTableWidgetItem, QHeaderView, QAbstractItemView)

import ui_theme
import fluent_theme
import ui_shell

from qfluentwidgets import (FluentWindow, FluentIcon, TitleLabel, StrongBodyLabel,
    CaptionLabel, BodyLabel, TableWidget, PrimaryPushButton, PushButton,
    TransparentPushButton, SearchLineEdit, SimpleCardWidget, InfoBadge, setFont,
    PlainTextEdit, SwitchButton)

import LibraryManager as LM
import nd_git


# ── shell services (TabContext for the reused tabs) ─────────────────────────
class _AsyncBridge(QObject):
    done = pyqtSignal(object, bool)     # (done_cb, ok) marshalled to the GUI thread


class ShellServices:
    def __init__(self, log_fn):
        self._log = log_fn
        self._bridge = _AsyncBridge()
        self._bridge.done.connect(lambda cb, ok: cb(ok) if callable(cb) else None)

    def context(self) -> "ui_shell.TabContext":
        return ui_shell.TabContext(log=self._log, run_async=self.run_async)

    def run_async(self, fn, busy=None, ok=None, done_cb=None):
        def worker():
            success = True
            try:
                fn()
            except Exception as e:                       # noqa: BLE001
                success = False
                self._log(f"ERROR: {e}")
            else:
                if ok:
                    self._log(ok)
            self._bridge.done.emit(done_cb, success)
        threading.Thread(target=worker, daemon=True).start()


# ── grayscale readout fascia ────────────────────────────────────────────────
def _readout(specs):
    """One bench-meter row of (key, LABEL, value). Returns (card, {key: value_label})."""
    card = SimpleCardWidget()
    lay = QHBoxLayout(card)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    cells = {}
    for i, (key, label, val) in enumerate(specs):
        cell = QWidget()
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(16, 10, 16, 10)
        cl.setSpacing(3)
        cap = CaptionLabel(label)
        cap.setTextColor(ui_theme.tc("FG_DIM"), ui_theme.tc("FG_DIM"))
        v = StrongBodyLabel(str(val))
        v.setFont(QFont("JetBrains Mono", 13))
        cl.addWidget(cap)
        cl.addWidget(v)
        lay.addWidget(cell)
        cells[key] = v
        if i < len(specs) - 1:
            sep = QFrame()
            sep.setFixedWidth(1)
            sep.setStyleSheet(f"background:{ui_theme.tc('BORDER')};")
            lay.addWidget(sep)
    lay.addStretch(1)
    return card, cells


def _section(text: str) -> QWidget:
    """Uppercase letter-spaced overline + hairline rule (the structural device)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 6, 0, 4)
    lay.setSpacing(10)
    cap = CaptionLabel(text.upper())
    cap.setTextColor(ui_theme.tc("FG_DIM"), ui_theme.tc("FG_DIM"))
    f = cap.font(); f.setLetterSpacing(QFont.PercentageSpacing, 108); cap.setFont(f)
    rule = QFrame(); rule.setFixedHeight(1); rule.setStyleSheet(f"background:{ui_theme.tc('BORDER')};")
    lay.addWidget(cap)
    lay.addWidget(rule, 1)
    return w


# ── KiCad Manager tab (rebuilt on Fluent) ───────────────────────────────────
class ManagerView(QWidget):
    """Fresh grayscale library browser: readout, grouped/flat parts table, git
    status + guarded commit, and the maintenance actions — all on the pure
    LibraryManager helpers + nd_git (no old-shell dependency)."""

    _log_signal = pyqtSignal(str)

    def __init__(self, cfg, services: ShellServices, parent=None):
        super().__init__(parent)
        self._log_signal.connect(self._append_log)
        self.setObjectName("managerView")
        self.cfg = cfg
        self.services = services
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addWidget(TitleLabel("KiCad Manager"))
        self.readout_card, self._ro = _readout([
            ("items", "Items", 0), ("symbols", "Symbols", 0),
            ("footprints", "Footprints", 0), ("models", "3D Models", 0),
            ("dupes", "Duplicates", 0)])
        root.addWidget(self.readout_card)

        # command bar (Fluent): one primary + the maintenance/import actions
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_refresh = PrimaryPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        for label, slot in [("Import ZIPs", self._process_zips),
                            ("Clean Leftovers", self._clean),
                            ("Repair", self._repair),
                            ("Remove Duplicates", self._dedupe),
                            ("Register Libraries", self._register),
                            ("Render Board", self._render_board)]:
            b = PushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.git_lbl = CaptionLabel("")
        self.git_lbl.setTextColor(ui_theme.tc("FG_DIM"), ui_theme.tc("FG_DIM"))
        self.btn_commit = PushButton("Commit")
        self.btn_commit.clicked.connect(self._commit)
        bar.addWidget(self.git_lbl)
        bar.addWidget(self.btn_commit)
        root.addLayout(bar)

        # view controls: grouped toggle + filter
        vc = QHBoxLayout()
        vc.setSpacing(8)
        vc.addWidget(BodyLabel("Group by Component"))
        self.group_sw = SwitchButton()
        self.group_sw.setChecked(True)
        self.group_sw.checkedChanged.connect(lambda *_: self.refresh())
        vc.addWidget(self.group_sw)
        vc.addStretch(1)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("Filter parts…")
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self._apply_filter)
        vc.addWidget(self.search)
        root.addLayout(vc)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(6)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.table, 1)

        root.addWidget(_section("Log"))
        self.log = PlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        root.addWidget(self.log)

        self._groups = []
        self.refresh()
        self._refresh_git()

    # -- logic wiring --
    def write(self, msg):                      # UILog sink — thread-safe via signal
        self._log_signal.emit(str(msg).rstrip())

    def _append_log(self, msg):
        self.log.appendPlainText(msg)

    def refresh(self):
        grouped = self.group_sw.isChecked()
        try:
            if grouped:
                self._groups = LM.scan_library_grouped(self.cfg)
                self._render_grouped()
            else:
                rows, summary = LM.scan_library(self.cfg)
                self._render_flat(rows, summary)
        except Exception as e:                 # noqa: BLE001
            self.write(f"Refresh failed: {e}")

    def _render_grouped(self):
        cols = ["Part", "Symbol", "Footprint", "3D Model", "Status"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        rows = [g for g in self._groups if g.get("footprint") is not None]
        self.table.setRowCount(len(rows))
        n_fp = n_mdl = n_sym = 0
        for r, g in enumerate(rows):
            n_fp += 1
            n_mdl += 1 if g.get("model") else 0
            syms = g.get("symbols") or []
            n_sym += len(syms)
            vals = [g["footprint"], ", ".join(syms), g["footprint"],
                    g.get("model") or "—",
                    "Dangling" if g.get("dangling") else "OK"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c == 4 and g.get("dangling"):
                    it.setForeground(_qcolor(ui_theme.status("warn")))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self._ro["items"].setText(str(len(rows)))
        self._ro["symbols"].setText(str(n_sym))
        self._ro["footprints"].setText(str(n_fp))
        self._ro["models"].setText(str(n_mdl))
        self._ro["dupes"].setText("0")

    def _render_flat(self, rows, summary):
        cols = ["Name", "Type", "Status"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(str(row.get("name", ""))))
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get("type", ""))))
            self.table.setItem(r, 2, QTableWidgetItem("Duplicate" if row.get("dup") else "OK"))
        for k, key in (("items", "items"), ("symbols", "symbols"),
                       ("footprints", "footprints"), ("models", "models"),
                       ("dupes", "duplicates")):
            if key in summary:
                self._ro[k].setText(str(summary[key]))

    def _apply_filter(self, text):
        text = (text or "").lower()
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            self.table.setRowHidden(r, bool(text) and (it is None or text not in it.text().lower()))

    def _refresh_git(self):
        try:
            root = nd_git.repo_root(self.cfg.get("RepoRoot", "."))
            if not root:
                self.git_lbl.setText("not a git repo")
                return
            st = nd_git.status(root)
            br = nd_git.current_branch(root) or "—"
            if st["clean"]:
                st_txt = "clean"
            else:
                st_txt = f"{len(st['modified'])} modified, {len(st['untracked'])} untracked"
            self.git_lbl.setText(f"{br} · {st_txt}")
        except Exception:                      # noqa: BLE001
            self.git_lbl.setText("")

    def _commit(self):
        root = nd_git.repo_root(self.cfg.get("RepoRoot", "."))
        if not root:
            self.write("Commit: not a git repository.")
            return
        def job():
            paths = [self.cfg.get(k) for k in ("Libs", "SymbolLib", "FootprintLib", "ModelLib") if self.cfg.get(k)]
            ok, msg = nd_git.commit(root, "NETDECK: update shared library", paths=paths)
            self.write(f"Commit: {msg}")
        self.services.run_async(job, ok="Commit finished.", done_cb=lambda ok: self._refresh_git())

    def _info(self, ok, title, content=""):
        try:
            from qfluentwidgets import InfoBar
            (InfoBar.success if ok else InfoBar.warning)(
                title, content, parent=self, duration=3500)
        except Exception:                      # noqa: BLE001
            pass

    def _run(self, fn, ok_msg):
        def done(ok):
            self.refresh()
            self._refresh_git()
            self._info(ok, ok_msg if ok else f"{ok_msg} — see log")
        self.services.run_async(fn, ok=ok_msg, done_cb=done)

    def _repair(self):
        self._run(lambda: LM.repair_library(self.cfg, self), "Library repaired")

    def _process_zips(self):
        self._run(lambda: LM.process_existing_zips(self.cfg, self), "Processed ZIPs")

    def _clean(self):
        self._run(lambda: LM.clean_leftovers(self.cfg, self), "Cleaned leftovers")

    def _dedupe(self):
        self._run(lambda: LM.dedupe_symbol_library(Path(self.cfg["SymbolLib"]), self),
                  "Removed duplicate symbols")

    def _register(self):
        self._run(lambda: LM.register_libraries(self.cfg, self), "Registered libraries in KiCad")

    def _render_board(self):
        from PyQt5.QtWidgets import QFileDialog
        import fp_render
        if not fp_render.have_board_render():
            self._info(False, "Board render unavailable", "kicad-cli was not found.")
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, "Select a board", str(Path(self.cfg.get("RepoRoot", "."))),
            "KiCad PCB (*.kicad_pcb)")
        if not fn:
            return
        def job():
            res = fp_render.render_board_image(fn)
            self.write(f"Board render: {getattr(res, 'message', 'done')}")
        self.services.run_async(job, ok="Board rendered",
                                done_cb=lambda ok: self._info(ok, "Board rendered", Path(fn).name))


def _qcolor(hexs):
    from PyQt5.QtGui import QColor
    return QColor(hexs)


# ── the FluentWindow shell ───────────────────────────────────────────────────
def apply_app_palette(dark: bool = True):
    """Dark graphite QPalette so plain QWidgets/tables match the Fluent components
    (QFluentWidgets' setTheme themes its own widgets, not the app palette)."""
    t = ui_theme.set_theme(dark)
    p = QPalette()
    for role, key in ((QPalette.Window, "MAIN_BG"), (QPalette.Base, "CARD_BG"),
                      (QPalette.AlternateBase, "MAIN_BG"), (QPalette.Text, "FG"),
                      (QPalette.WindowText, "FG"), (QPalette.Button, "CARD_BG"),
                      (QPalette.ButtonText, "FG"), (QPalette.Highlight, "SEL_BG"),
                      (QPalette.HighlightedText, "FG"), (QPalette.ToolTipBase, "CARD_BG"),
                      (QPalette.ToolTipText, "FG"), (QPalette.PlaceholderText, "FG_DIM")):
        p.setColor(role, QColor(t[key]))
    ap = QApplication.instance()
    if ap is not None:
        ap.setPalette(p)


class NetdeckWindow(FluentWindow):
    def __init__(self, cfg):
        super().__init__()
        fluent_theme.apply_grayscale_fluent(dark=True)
        apply_app_palette(dark=True)
        # graphite ground + GRAYSCALE standard checkboxes (Fusion checks default to a
        # blue that breaks the palette — force the neutral accent instead).
        self.setStyleSheet(
            f"#managerView{{background:{ui_theme.tc('MAIN_BG')};}}"
            f"QCheckBox::indicator{{width:15px;height:15px;border:1.4px solid "
            f"{ui_theme.tc('BTN_BORDER')};border-radius:3px;background:transparent;}}"
            f"QCheckBox::indicator:checked{{background:{ui_theme.tc('ACCENT')};"
            f"border-color:{ui_theme.tc('ACCENT')};}}")
        self.cfg = cfg
        self.setWindowTitle("NETDECK — Firmware Extraction Bench")
        self.resize(1360, 880)

        self.services = ShellServices(self._log)
        ctx = self.services.context()

        self.manager = ManagerView(cfg, self.services)
        self.tools = self._build_tools(ctx)
        self.stm32 = self._build_stm32(ctx)
        for w in (self.tools, self.stm32):   # push the reused tabs' internal theme to dark
            if hasattr(w, "apply_theme"):
                try:
                    w.apply_theme(True)
                except Exception:
                    pass
        apply_app_palette(dark=True)          # re-assert after tabs republish the theme
        # grayscale standard checkboxes on the reused tabs (highest specificity, so it
        # wins over the widget's own stylesheet; Fusion's blue check breaks the palette)
        _cb = (f"QCheckBox::indicator{{width:15px;height:15px;border:1.4px solid "
               f"{ui_theme.tc('BTN_BORDER')};border-radius:3px;background:transparent;}}"
               f"QCheckBox::indicator:checked{{background:{ui_theme.tc('ACCENT')};"
               f"border-color:{ui_theme.tc('ACCENT')};}}")
        for w in (self.tools, self.stm32):
            try:
                w.setStyleSheet((w.styleSheet() or "") + _cb)
            except Exception:
                pass

        self.addSubInterface(self.manager, FluentIcon.LIBRARY, "KiCad Manager")
        self.addSubInterface(self.tools, FluentIcon.DEVELOPER_TOOLS, "KiCad Tools")
        self.addSubInterface(self.stm32, FluentIcon.IOT, "STM32 Pins")
        self.navigationInterface.setExpandWidth(200)

    def _log(self, msg):
        if getattr(self, "manager", None) is not None:
            self.manager.write(msg)

    def _build_tools(self, ctx):
        try:
            from kicad_tools import KiCadToolsWidget
            projects_dir = str(Path(self.cfg.get("RepoRoot", ".")).parent)
            w = KiCadToolsWidget(None, projects_dir, ctx=ctx)
        except Exception as e:                 # noqa: BLE001
            w = _fallback(f"KiCad Tools unavailable:\n{e}")
        w.setObjectName("toolsView")
        return w

    def _build_stm32(self, ctx):
        try:
            from stm32_pins_tab import Stm32PinsWidget
            w = Stm32PinsWidget(None, ctx=ctx)
        except Exception as e:                 # noqa: BLE001
            w = _fallback(f"STM32 Pins unavailable:\n{e}")
        w.setObjectName("stm32View")
        return w


def _fallback(text):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(BodyLabel(text))
    return w


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM", ""))
    app = QApplication.instance() or QApplication(sys.argv)
    here = Path(__file__).resolve().parent
    for ttf in glob.glob(str(here / "fonts" / "*.ttf")):
        QFontDatabase.addApplicationFont(ttf)
    for fam in ("Geist", "Inter", "Segoe UI Variable Text", "Segoe UI"):
        if fam in set(QFontDatabase().families()):
            app.setFont(QFont(fam, 9))
            break
    cfg = LM.load_config()
    win = NetdeckWindow(cfg)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
