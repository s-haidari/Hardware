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
                             QLabel, QTableWidgetItem, QHeaderView, QAbstractItemView)

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


# ── readout fascia ──────────────────────────────────────────────────────────
def _readout(specs):
    """A borderless bench-meter row: a big value over a small dim label, cells set
    apart by whitespace (Quiet Instrument — no card, no divider rules). Returns
    (widget, {key: value_label})."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 6)
    lay.setSpacing(0)
    cells = {}
    mono = ui_theme.MONO_FONT_STACK[0]
    for key, label, val in specs:
        cell = QWidget()
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(0, 0, 44, 0)
        cl.setSpacing(2)
        v = QLabel(str(val))
        v.setObjectName("roValue")           # colour comes from the window QSS (theme-aware)
        vf = QFont(mono); vf.setPointSizeF(15); vf.setWeight(QFont.DemiBold)
        v.setFont(vf)
        cap = CaptionLabel(label)
        cap.setTextColor(QColor(ui_theme.LIGHT_COLORS["FG_DIM"]),
                         QColor(ui_theme.DARK_COLORS["FG_DIM"]))
        cf = cap.font(); cf.setPointSizeF(8.5); cap.setFont(cf)
        cl.addWidget(v)
        cl.addWidget(cap)
        lay.addWidget(cell)
        cells[key] = v
    lay.addStretch(1)
    return w, cells


def _section(text: str) -> QWidget:
    """Title-Case overline + hairline rule (the structural device, theme-aware)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 6, 0, 4)
    lay.setSpacing(10)
    cap = CaptionLabel(text)
    cap.setTextColor(QColor(ui_theme.LIGHT_COLORS["FG_DIM"]),
                     QColor(ui_theme.DARK_COLORS["FG_DIM"]))
    f = cap.font(); f.setPointSizeF(10.5); cap.setFont(f)
    rule = QFrame(); rule.setFixedHeight(1); rule.setObjectName("sectionRule")
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
        self.btn_refresh.setToolTip("Rescan the shared library and rebuild the parts table")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        for label, slot, tip in [
                ("Import ZIPs", self._process_zips,
                 "Import component ZIPs (symbol, footprint, and 3D model) into the shared library"),
                ("Clean Leftovers", self._clean,
                 "Delete orphaned files left behind by removed or renamed parts"),
                ("Repair", self._repair,
                 "Fix broken symbol, footprint, and 3D-model links across the library"),
                ("Remove Duplicates", self._dedupe,
                 "Remove duplicate symbols from the symbol library"),
                ("Register Libraries", self._register,
                 "Register the symbol and footprint libraries in KiCad's library tables"),
                ("Render Board", self._render_board,
                 "Render a .kicad_pcb board to an image using kicad-cli")]:
            b = PushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.git_lbl = CaptionLabel("")
        self.git_lbl.setTextColor(QColor(ui_theme.LIGHT_COLORS["FG_DIM"]),
                                  QColor(ui_theme.DARK_COLORS["FG_DIM"]))
        self.btn_commit = PushButton("Commit")
        self.btn_commit.setToolTip("Commit the shared-library changes to git")
        self.btn_commit.clicked.connect(self._commit)
        bar.addWidget(self.git_lbl)
        bar.addWidget(self.btn_commit)
        root.addLayout(bar)

        # view controls: grouped toggle + filter
        vc = QHBoxLayout()
        vc.setSpacing(8)
        _grp = BodyLabel("Group by Component")
        vc.addWidget(_grp)
        self.group_sw = SwitchButton()
        self.group_sw.setChecked(True)
        self.group_sw.setToolTip("Group one part's symbol, footprint, and 3D model into a single row, "
                                 "or list every library file separately")
        self.group_sw.checkedChanged.connect(lambda *_: self.refresh())
        vc.addWidget(self.group_sw)
        vc.addStretch(1)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("Filter Parts…")
        self.search.setToolTip("Filter the table by part name")
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self._apply_filter)
        vc.addWidget(self.search)
        root.addLayout(vc)

        self.table = TableWidget()
        self.table.setBorderVisible(False)
        self.table.setBorderRadius(0)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setToolTip("Click a part to preview its symbol, footprint, and 3D model below")
        self.table.itemSelectionChanged.connect(self._on_row)
        root.addWidget(self.table, 1)

        # component preview: symbol, footprint, and 3D model side by side
        root.addWidget(_section("Preview"))
        prev = QHBoxLayout()
        prev.setSpacing(12)
        self.pv_symbol = LM.PreviewView()
        self.pv_footprint = LM.PreviewView()
        self.pv_model = LM.PreviewView()
        for title, pv, tip in [
                ("Symbol", self.pv_symbol, "Schematic symbol for the selected part"),
                ("Footprint", self.pv_footprint, "PCB footprint: pads, courtyard, and silkscreen"),
                ("3D Model", self.pv_model, "3D model — drag to rotate, scroll to zoom")]:
            pane = QVBoxLayout()
            pane.setSpacing(6)
            cap = CaptionLabel(title)
            cap.setTextColor(QColor(ui_theme.LIGHT_COLORS["FG_DIM"]),
                             QColor(ui_theme.DARK_COLORS["FG_DIM"]))
            cf = cap.font(); cf.setPointSizeF(11); cap.setFont(cf)
            pv.setToolTip(tip)
            pv.setMinimumHeight(200)
            pv.show_text("Select a part")
            pane.addWidget(cap)
            pane.addWidget(pv, 1)
            holder = QWidget(); holder.setLayout(pane)
            prev.addWidget(holder, 1)
        root.addLayout(prev)

        root.addWidget(_section("Log"))
        self.log = PlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(90)
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
        self._sym_blocks = None          # library may have changed; rebuild the block cache lazily
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
        self._rows_view = rows           # table row index -> group dict (for the preview)
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
        # fill the viewport: text columns share the width, Status hugs its content
        hdr = self.table.horizontalHeader()
        for c in range(4):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._ro["items"].setText(str(len(rows)))
        self._ro["symbols"].setText(str(n_sym))
        self._ro["footprints"].setText(str(n_fp))
        self._ro["models"].setText(str(n_mdl))
        self._ro["dupes"].setText("0")

    def _render_flat(self, rows, summary):
        self._rows_view = []             # flat view has no grouped part to preview
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

    # -- component preview: symbol · footprint · 3D model --
    def _sym_block(self, name):
        """The S-expression text for one symbol, from a lazily-built name -> block cache."""
        blocks = getattr(self, "_sym_blocks", None)
        if blocks is None:
            blocks = {}
            try:
                txt = Path(self.cfg["SymbolLib"]).read_text(encoding="utf-8", errors="replace")
                for b in LM.extract_symbol_blocks(txt):
                    blocks[LM.extract_symbol_name(b)] = b
            except Exception:                  # noqa: BLE001
                pass
            self._sym_blocks = blocks
        return blocks.get(name)

    def _on_row(self):
        """Render the selected part's symbol, footprint, and 3D model into the panes."""
        import fp_render
        r = self.table.currentRow()
        rows = getattr(self, "_rows_view", [])
        if r < 0 or r >= len(rows):
            for pv in (self.pv_symbol, self.pv_footprint, self.pv_model):
                pv.show_text("Select a part")
            return
        g = rows[r]
        # symbol (rendered oversize; the pane scales it to fit, so it stays crisp)
        syms = g.get("symbols") or []
        block = self._sym_block(syms[0]) if syms else None
        img = fp_render.render_symbol_image(block, px=520) if block else None
        (self.pv_symbol.show_image(img) if img is not None
         else self.pv_symbol.show_text("No symbol"))
        # footprint
        fp = Path(self.cfg["FootprintLib"]) / f"{g.get('footprint', '')}.kicad_mod"
        img = fp_render.render_footprint_image(fp, px=640) if fp.exists() else None
        (self.pv_footprint.show_image(img) if img is not None
         else self.pv_footprint.show_text("No footprint"))
        # 3D model (loaded off the GUI thread)
        model = g.get("model")
        if model:
            self._load_3d(Path(self.cfg["ModelLib"]) / model)
        else:
            self.pv_model.show_text("No 3D model")

    def _load_3d(self, path):
        import fp_render
        self.pv_model.show_text("Loading 3D…")
        self._model_token = tok = object()     # supersede an in-flight load if the row changes
        holder = {}

        def job():
            holder["mesh"] = fp_render.load_step_mesh(path)

        def done(_ok):
            if getattr(self, "_model_token", None) is not tok:
                return
            v, f = holder.get("mesh", (None, None))
            if v is not None and f is not None:
                self.pv_model.show_mesh(v, f)
            else:
                self.pv_model.show_text("3D not available")
        self.services.run_async(job, done_cb=done)

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


def _theme_qss(dark: bool = True) -> str:
    """The full token stylesheet (buttons, inputs, combos, menus, tables, headers,
    scrollbars) rendered from the shared template. The legacy shell applied this to
    its window; the Fluent shell must too, or every custom-styled control falls back
    to unthemed native rendering (the washed-out 'disabled button' look)."""
    t = ui_theme.DARK_COLORS if dark else ui_theme.LIGHT_COLORS
    qss = LM.LibraryManagerWindow._THEME_QSS
    for k, v in t.items():
        qss = qss.replace("@@" + k + "@@", v)
        qss = qss.replace("@" + k + "@", v)
    check = LM.resource_path("check_dark.png" if dark else "check_light.png")
    qss = qss.replace("@CHECK_IMG@", str(check).replace("\\", "/"))
    qss = qss.replace("@CARET_DOWN@", str(LM.resource_path("caret_down.png")).replace("\\", "/"))
    qss = qss.replace("@CARET_UP@", str(LM.resource_path("caret_up.png")).replace("\\", "/"))
    return qss


class NetdeckWindow(FluentWindow):
    def __init__(self, cfg):
        super().__init__()
        self._dark = True
        fluent_theme.apply_grayscale_fluent(dark=True)
        apply_app_palette(dark=True)
        self.cfg = cfg
        self.setWindowTitle("NETDECK — Firmware Extraction Bench")
        self.resize(1360, 880)

        self.services = ShellServices(self._log)
        ctx = self.services.context()

        self.manager = ManagerView(cfg, self.services)
        self.tools = self._build_tools(ctx)
        self.stm32 = self._build_stm32(ctx)
        self._apply_shell_theme(True)

        self.addSubInterface(self.manager, FluentIcon.LIBRARY, "KiCad Manager")
        self.addSubInterface(self.tools, FluentIcon.DEVELOPER_TOOLS, "KiCad Tools")
        self.addSubInterface(self.stm32, FluentIcon.IOT, "STM32 Pins")
        # Windows 11 Settings shell: an always-expanded nav pane with icon + label,
        # no back arrow, no hamburger collapse; a dark/light toggle at the bottom.
        nav = self.navigationInterface
        try:
            from qfluentwidgets import NavigationItemPosition
            nav.addItem(routeKey="themeToggle", icon=FluentIcon.CONSTRACT,
                        text="Dark / Light", onClick=self._toggle_theme,
                        selectable=False, position=NavigationItemPosition.BOTTOM)
        except Exception:                      # noqa: BLE001
            pass
        nav.setExpandWidth(200)
        for meth, arg in (("setReturnButtonVisible", False),
                          ("setCollapsible", False),
                          ("setMinimumExpandWidth", 900)):
            try:
                getattr(nav, meth)(arg)
            except Exception:                  # noqa: BLE001
                pass
        try:
            nav.expand(useAni=False)
        except TypeError:
            nav.expand()
        except Exception:                      # noqa: BLE001
            pass
        # the docked 200px nav pane sits under the title text's default position:
        # push the window title right of the pane so they never overlap
        try:
            self.titleBar.hBoxLayout.insertSpacing(0, 150)
        except Exception:                      # noqa: BLE001
            pass

    @staticmethod
    def _cb_qss(t) -> str:
        """Grayscale standard checkboxes (Fusion's blue check breaks the palette)."""
        return (f"QCheckBox::indicator{{width:15px;height:15px;border:1.4px solid "
                f"{t['BTN_BORDER']};border-radius:3px;background:transparent;}}"
                f"QCheckBox::indicator:checked{{background:{t['ACCENT']};"
                f"border-color:{t['ACCENT']};}}")

    def _apply_shell_theme(self, dark: bool):
        """Windows 11 dark / light: retheme the Fluent components, the app palette,
        the token QSS (buttons, inputs, tables, headers), and both reused tabs."""
        self._dark = dark
        t = ui_theme.DARK_COLORS if dark else ui_theme.LIGHT_COLORS
        fluent_theme.apply_grayscale_fluent(dark=dark)
        apply_app_palette(dark=dark)
        self.setStyleSheet(
            _theme_qss(dark=dark)
            + f"#managerView{{background:{t['MAIN_BG']};}}"
            f"QLabel#roValue{{color:{t['FG']};background:transparent;}}"
            f"QFrame#sectionRule{{background:{t['BORDER']};border:none;}}"
            + self._cb_qss(t))
        for w in (getattr(self, "tools", None), getattr(self, "stm32", None)):
            if w is not None and hasattr(w, "apply_theme"):
                try:
                    w.apply_theme(dark)
                except Exception:              # noqa: BLE001
                    pass
        apply_app_palette(dark=dark)           # re-assert after tabs republish the theme
        for w in (getattr(self, "tools", None), getattr(self, "stm32", None)):
            if w is not None:
                try:
                    base = (w.styleSheet() or "").split("/*cb*/")[0]
                    w.setStyleSheet(base + "/*cb*/" + self._cb_qss(t))
                except Exception:              # noqa: BLE001
                    pass
        # the Manager's painted preview panes read the active theme at paint time
        for pv in (getattr(self.manager, "pv_symbol", None),
                   getattr(self.manager, "pv_footprint", None),
                   getattr(self.manager, "pv_model", None)):
            if pv is not None:
                pv.update()

    def _toggle_theme(self):
        self._apply_shell_theme(not self._dark)

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
    # Fusion renders from the palette; the default windowsvista style ignores it and
    # paints light-grey native controls inside the dark app.
    app.setStyle("Fusion")
    here = Path(__file__).resolve().parent
    for ttf in glob.glob(str(here / "fonts" / "*.ttf")):
        QFontDatabase.addApplicationFont(ttf)
    for fam in ("Segoe UI Variable Text", "Segoe UI", "Inter", "Geist"):
        if fam in set(QFontDatabase().families()):
            app.setFont(QFont(fam, 9))
            break
    cfg = LM.load_config()
    win = NetdeckWindow(cfg)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
