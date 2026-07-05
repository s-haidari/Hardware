"""Projects — the KiCad project maintenance workspace, fully wired.

Projects are discovered under the repo root; a picker selects one. Panels call
the real nd_* / LibraryManager helpers: audit_schematic, run_erc/run_drc,
bom_from_kicad_schematic / consolidated_bom, the rename wizard, the net-class
manager, board setup, the OSH Park fab presets, and nd_git. Slow / mutating
work runs off the GUI thread and logs to the status line.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
                             QCheckBox, QDoubleSpinBox, QSpinBox, QGridLayout, QScrollArea, QFrame,
                             QColorDialog, QPushButton)

from .. import theme as T
from .. import widgets as W
from ..util import LogSink, run_populate, clear_layout, sentence
from .. import feature as F

import nd_wizard
import kicad_tools
import nd_project_health as phealth
import nd_kicad_checks as kchecks
import nd_netclass_manager as ncm
import nd_board_setup
import nd_fab_presets as fabp
import nd_object_conform as conform
import LibraryManager as LM
import nd_git
import fp_render

_SEV = {"error": "err", "warning": "warn", "exclusion": "mut", "info": "mut"}
_KIND_LABEL = {
    "no_footprint": "No Footprint", "duplicate_ref": "Duplicate Reference",
    "pin_pad_mismatch": "Pin / Pad Mismatch", "no_mpn": "No Manufacturer Part Number",
    "unannotated": "Unannotated", "no_3d_model": "No 3D Model", "missing_3d_model": "No 3D Model",
}


# ── shared project state ─────────────────────────────────────────────────────
class ProjectsState:
    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.projects = []
        seen = set()
        rr = self.cfg.get("RepoRoot")
        roots = [Path(rr), Path(rr).parent] if rr else []
        for r in roots:
            try:
                for p in kicad_tools.discover_kicad_projects(r):
                    if str(p) not in seen:
                        seen.add(str(p)); self.projects.append(Path(p))
            except Exception:  # noqa: BLE001
                pass
        # Prefer a project named like the main board (Master) over archived/sub sheets
        self.project = next((p for p in self.projects if p.name.lower() == "master"),
                            self.projects[0] if self.projects else None)
        self._refreshers = []

    def names(self):
        return [p.name for p in self.projects]

    def set_project(self, name):
        for p in self.projects:
            if p.name == name:
                self.project = p
                break
        for fn in list(self._refreshers):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def on_change(self, fn):
        self._refreshers.append(fn)

    def schematics(self):
        try:
            return nd_wizard.list_schematics(self.project) if self.project else []
        except Exception:  # noqa: BLE001
            return []

    def boards(self):
        try:
            return nd_wizard.list_boards(self.project) if self.project else []
        except Exception:  # noqa: BLE001
            return []

    def root_schematic(self):
        schs = self.schematics()
        if not schs:
            return None
        try:
            return kicad_tools.pick_root_schematic(schs, kicad_tools.project_pro_file(self.project))
        except Exception:  # noqa: BLE001
            return schs[0]


def _no_project(msg="No KiCad projects discovered under the repo root.") -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(24, 16, 24, 24)
    v.addWidget(W.body(msg, dim=True)); v.addStretch(1)
    return w


def _kicad_cli():
    try:
        return fp_render.find_board_render_cli()
    except Exception:  # noqa: BLE001
        return None


# ── Health (audit + ERC + DRC) ───────────────────────────────────────────────
def _health_panel(ctx, state) -> QWidget:
    if not state.project:
        return _no_project()
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    summary = QHBoxLayout(); summary.setSpacing(8)
    bar.addLayout(summary); bar.addStretch(1)
    b_erc = W.btn("Run Electrical Rules Check", "default", "Run the electrical rules check through the KiCad command line tool")
    b_drc = W.btn("Run Design Rules Check", "default", "Run the design rules check through the KiCad command line tool")
    b_audit = W.btn("Audit", "primary", "Audit the schematic for missing footprints, MPNs and mismatches")
    bar.addWidget(b_erc); bar.addWidget(b_drc); bar.addWidget(b_audit)
    lay.addLayout(bar)
    result = QVBoxLayout(); lay.addLayout(result, 1)
    lay.addStretch(1)

    def set_summary(pairs):
        for i in reversed(range(summary.count())):
            w = summary.itemAt(i).widget()
            if w:
                w.deleteLater()
        for txt, kind in pairs:
            summary.addWidget(W.tag(txt, kind))

    def audit():
        sch = state.root_schematic()
        if not sch:
            ctx.services.log("No schematic found in the project."); return
        clear_layout(result); result.addWidget(W.body("Auditing...", dim=True))
        fp_dirs = [ctx.cfg["FootprintLib"]] if ctx.cfg.get("FootprintLib") else None
        mdl_dirs = [ctx.cfg["ModelLib"]] if ctx.cfg.get("ModelLib") else None

        def populate(res, ok):
            clear_layout(result)
            if not res:
                result.addWidget(W.body("Audit failed.", dim=True)); return
            bs = res.get("counts", {}).get("by_severity", {})
            set_summary([(f"{res.get('components', 0)} Components", "mut"),
                         (f"{res.get('healthy', 0)} Healthy", "mut"),
                         (f"{bs.get('error', 0)} Errors", "err"),
                         (f"{bs.get('warning', 0)} Warnings", "warn")])
            rows = []
            for f in res.get("findings", []):
                raw = str(f.get("kind", ""))
                kind = _KIND_LABEL.get(raw, raw.replace("_", " ").title())
                sev = f.get("severity", "info")
                rows.append([W.body(str(f.get("ref", "")), mono=True), W.body(kind),
                             W.body(sentence(f.get("detail", ""))), W.tag(sev.title(), _SEV.get(sev, "mut"))])
            result.addWidget(W.data_table(["Reference", "Kind", "Detail", "Severity"], rows, stretch_col=2), 1)

        run_populate(ctx, lambda: phealth.audit_schematic(sch, fp_dirs, mdl_dirs), populate, busy="Auditing schematic...")

    def run_check(kind):
        cli = _kicad_cli()
        if not cli:
            ctx.services.log("kicad-cli not found on PATH."); return
        target = state.root_schematic() if kind == "erc" else (state.boards()[0] if state.boards() else None)
        if not target:
            ctx.services.log(f"No {'schematic' if kind == 'erc' else 'board'} to check."); return
        clear_layout(result); result.addWidget(W.body(f"Running {kind.upper()}...", dim=True))

        def populate(res, ok):
            clear_layout(result)
            if not res:
                result.addWidget(W.body(f"{kind.upper()} failed.", dim=True)); return
            s = res.get("summary", {})
            set_summary([(f"{s.get('errors', 0)} Errors", "err"), (f"{s.get('warnings', 0)} Warnings", "warn"),
                         (f"{s.get('exclusion', 0)} Exclusions", "mut")])
            rows = []
            for f in res.get("findings", []):
                sev = f.get("severity", "info")
                rows.append([W.tag(sev.title(), _SEV.get(sev, "mut")), W.body(str(f.get("rule", "")), mono=True),
                             W.body(sentence(f.get("message", ""))), W.body(str(f.get("where", "")), dim=True)])
            if not rows:
                result.addWidget(W.body(f"{kind.upper()} clean.", dim=True))
            else:
                result.addWidget(W.data_table(["Severity", "Rule", "Message", "Where"], rows, stretch_col=2), 1)

        fn = kchecks.run_erc if kind == "erc" else kchecks.run_drc
        run_populate(ctx, lambda: fn(target, cli), populate, busy=f"Running {kind.upper()}...")

    b_audit.clicked.connect(audit)
    b_erc.clicked.connect(lambda: run_check("erc"))
    b_drc.clicked.connect(lambda: run_check("drc"))
    audit()
    return root


# ── BOM (real bom_from_kicad_schematic / consolidated_bom) ───────────────────
def _bom_panel(ctx, state) -> QWidget:
    if not state.project:
        return _no_project()
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    summary = QHBoxLayout(); summary.setSpacing(8); bar.addLayout(summary); bar.addStretch(1)
    b_sch = W.btn("From Schematic", "ghost", "Build a bill of materials from the project root schematic")
    b_con = W.btn("Consolidated Bill of Materials", "primary", "Merge the bill of materials across every discovered project")
    bar.addWidget(b_sch); bar.addWidget(b_con)
    lay.addLayout(bar)
    result = QVBoxLayout(); lay.addLayout(result, 1); lay.addStretch(1)

    def set_summary(pairs):
        for i in reversed(range(summary.count())):
            w = summary.itemAt(i).widget()
            if w:
                w.deleteLater()
        for txt, kind in pairs:
            summary.addWidget(W.tag(txt, kind))

    def from_sch():
        sch = state.root_schematic()
        if not sch:
            ctx.services.log("No schematic found."); return
        clear_layout(result); result.addWidget(W.body("Reading schematic...", dim=True))

        def populate(res, ok):
            clear_layout(result)
            if not res or res.get("error"):
                result.addWidget(W.body((res or {}).get("error", "BOM failed."), dim=True)); return
            set_summary([(f"{res.get('component_count', 0)} Components", "mut"),
                         (f"{res.get('line_count', 0)} Line Items", "mut")])
            rows = []
            for r in res.get("rows", []):
                rows.append([", ".join(r.get("refs", [])[:6]), str(r.get("qty", "")),
                             str(r.get("value", "")), str(r.get("mpn", "")),
                             str(r.get("manufacturer", "")), str(r.get("footprint", ""))])
            result.addWidget(W.data_table(["Refs", "Qty", "Value", "Part Number", "Manufacturer", "Footprint"],
                                          rows, stretch_col=(4, 5), mono_cols={0, 3}, dim_cols={2, 5}), 1)

        run_populate(ctx, lambda: LM.bom_from_kicad_schematic(sch), populate, busy="Building BOM...")

    def consolidated():
        boards = {p.name: nd_wizard.list_schematics(p) for p in state.projects}
        clear_layout(result); result.addWidget(W.body("Consolidating across projects...", dim=True))

        def populate(res, ok):
            clear_layout(result)
            if not res:
                result.addWidget(W.body("Consolidated BOM failed.", dim=True)); return
            set_summary([(f"{len(res.get('board_names', []))} Boards", "mut"),
                         (f"{res.get('line_count', 0)} Line Items", "mut")])
            rows = []
            for r in res.get("rows", []):
                rows.append([str(r.get("mpn", "")), str(r.get("manufacturer", "")),
                             str(r.get("value", "")), str(r.get("footprint", "")),
                             str(r.get("total_qty", ""))])
            result.addWidget(W.data_table(["Part Number", "Manufacturer", "Value", "Footprint", "Total"],
                                          rows, stretch_col=(1, 3), mono_cols={0}, dim_cols={2, 3}), 1)

        run_populate(ctx, lambda: LM.consolidated_bom(boards), populate, busy="Consolidating BOM...")

    b_sch.clicked.connect(from_sch)
    b_con.clicked.connect(consolidated)
    from_sch()
    return root


# ── Rename (real preview + apply) ────────────────────────────────────────────
_OPS = [("Find And Replace", "find_replace"), ("Add Tag", "add_tag"),
        ("Strip All", "strip_all"), ("Unannotate", "unannotate")]


def _rename_panel(ctx, state) -> QWidget:
    if not state.project:
        return _no_project()
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    op_state = {"op": "find_replace"}
    top = QHBoxLayout(); top.setSpacing(8); top.addStretch(1)
    b_prev = W.btn("Preview", "ghost", "Preview the rename without writing")
    b_apply = W.btn("Apply Atomically", "primary", "Apply the transform, .bak per file")
    top.addWidget(b_prev); top.addWidget(b_apply)
    lay.addLayout(top)

    form = W.Card(pad=16)
    seg = W.Segmented([o[0] for o in _OPS], on_change=lambda name: op_state.update(
        op=dict(_OPS).get(name, "find_replace")))
    form.body.addWidget(seg)
    fr = QHBoxLayout(); fr.setSpacing(12)
    find = QLineEdit(); find.setPlaceholderText("SW_L100_"); find.setMinimumHeight(32)
    repl = QLineEdit(); repl.setPlaceholderText("U_SW_64_"); repl.setMinimumHeight(32)
    fcol = QVBoxLayout(); fcol.addWidget(W.eyebrow("Find / Tag")); fcol.addWidget(find)
    rcol = QVBoxLayout(); rcol.addWidget(W.eyebrow("Replace")); rcol.addWidget(repl)
    fr.addLayout(fcol); fr.addLayout(rcol)
    form.body.addLayout(fr)
    cb_refs = QCheckBox("References"); cb_refs.setChecked(True)
    cb_lbls = QCheckBox("Labels"); cb_lbls.setChecked(True)
    cbs = QHBoxLayout(); cbs.setSpacing(18); cbs.addWidget(cb_refs); cbs.addWidget(cb_lbls); cbs.addStretch(1)
    form.body.addLayout(cbs)
    lay.addWidget(form)

    result = QVBoxLayout(); lay.addLayout(result, 1); lay.addStretch(1)

    def _run(apply):
        op = op_state["op"]
        tag_or_find = find.text().strip()
        replacement = repl.text().strip()
        schs = state.schematics(); boards = state.boards()
        clear_layout(result); result.addWidget(W.body("Applying..." if apply else "Previewing...", dim=True))

        def job():
            changes, total = [], 0
            for sch in schs:
                counts, samples, ch = nd_wizard.schematic_preview_and_apply(
                    sch, op, tag_or_find, replacement or None, apply=apply,
                    touch_refs=cb_refs.isChecked(), touch_labels=cb_lbls.isChecked())
                total += sum(counts.values()) if isinstance(counts, dict) else 0
                changes += ch
            for brd in boards:
                cnt, samples, ch = nd_wizard.pcb_preview_and_apply(brd, op, tag_or_find, replacement or None, apply=apply)
                total += cnt
                changes += ch
            return {"total": total, "changes": changes}

        def populate(res, ok):
            clear_layout(result)
            if not res:
                result.addWidget(W.body("Rename failed.", dim=True)); return
            verb = "Applied" if apply else "Preview"
            result.addWidget(W.eyebrow(f"{verb}   {res['total']} Changes"))
            card = W.Card(pad=16)
            for (typ, old, new, path) in res["changes"][:60]:
                row = QHBoxLayout(); row.setSpacing(8)
                row.addWidget(W.tag(str(typ), "mut")); row.addWidget(W.body(str(old), dim=True, mono=True))
                row.addWidget(W.body("->", dim=True)); row.addWidget(W.body(str(new), mono=True)); row.addStretch(1)
                card.body.addLayout(row)
            if not res["changes"]:
                card.body.addWidget(W.body("No matching changes.", dim=True))
            result.addWidget(card)

        run_populate(ctx, job, populate, busy=("Applying rename..." if apply else "Previewing rename..."))

    b_prev.clicked.connect(lambda: _run(False))
    b_apply.clicked.connect(lambda: _run(True))
    return root


# ── Net Classes — comprehensive + editable, OSH Park / KiCad aligned ─────────
def _spin(val, is_int=False, width=84, lo=0.0, hi=20.0, decimals=3):
    if is_int:
        s = QSpinBox(); s.setRange(int(lo), int(hi) if hi > 200 else 200); s.setValue(int(val or 0))
    else:
        s = QDoubleSpinBox(); s.setDecimals(decimals); s.setRange(lo, hi); s.setSingleStep(0.005)
        s.setValue(float(val or 0.0))
    s.setButtonSymbols(s.NoButtons); s.setAlignment(Qt.AlignRight); s.setFixedWidth(width)
    return s


_NC_FIELDS = [
    ("clearance", "Clearance"), ("track_width", "Track Width"),
    ("via_diameter", "Via Diameter"), ("via_drill", "Via Drill"),
    ("microvia_diameter", "Microvia Diameter"), ("microvia_drill", "Microvia Drill"),
    ("diff_pair_width", "Differential Pair Width"), ("diff_pair_gap", "Differential Pair Gap"),
]


def _netclass_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    mgr = ncm.load_vault_standard()
    profiles = ncm.netclass_profiles()
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow("Profile"))
    if profiles:
        bar.addWidget(W.Segmented(profiles, tip="Net-class profile"))
    bar.addStretch(1)
    b_val = W.btn("Validate", "ghost", "Check every class against the OSH Park and KiCad minimums")
    b_sync = W.btn("Sync To Projects", "primary", "Write these net classes into the discovered projects")
    bar.addWidget(b_val); bar.addWidget(b_sync)
    lay.addLayout(bar)

    grid_w = QFrame(); grid_w.setObjectName("ndcard")
    W.register_restyle(lambda: grid_w.setStyleSheet(
        f"QFrame#ndcard{{background:{T.t('card')};border:1px solid {T.t('stroke')};border-radius:8px;}}"))
    grid = QGridLayout(grid_w); grid.setContentsMargins(14, 12, 14, 12)
    grid.setHorizontalSpacing(10); grid.setVerticalSpacing(9)
    grid.addWidget(W.eyebrow("Net Class"), 0, 0)
    for c, (_f, label) in enumerate(_NC_FIELDS, start=1):
        h = W.eyebrow(label); h.setWordWrap(True); h.setAlignment(Qt.AlignHCenter)
        h.setFixedWidth(108 if label.startswith("Differential") else 92)
        grid.addWidget(h, 0, c, Qt.AlignHCenter)
    grid.addWidget(W.eyebrow("Priority"), 0, len(_NC_FIELDS) + 1)

    recolor: dict = {}   # class name -> [callables that re-read nc.color]

    def _swatch(nc):
        btn = QPushButton(); btn.setObjectName("ncswatch"); btn.setFixedSize(24, 18)
        btn.setCursor(Qt.PointingHandCursor); btn.setToolTip("Click to change this class colour")
        def paint():
            btn.setStyleSheet(f"QPushButton#ncswatch{{background:{nc.color};"
                              f"border:1px solid {T.t('stroke')};border-radius:4px;}}")
        def pick():
            col = QColorDialog.getColor(QColor(nc.color), root, f"{nc.name} Colour")
            if col.isValid():
                nc.color = col.name()
                paint()
                for fn in recolor.get(nc.name, []):
                    fn()
        btn.clicked.connect(pick); W.register_restyle(paint); paint()
        return btn

    def _name_cell(nc, name):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        h.addWidget(_swatch(nc)); h.addWidget(W.body(name, mono=True))
        return w

    rows_map = {}
    for r, name in enumerate(mgr.list_netclasses(), start=1):
        nc = mgr.net_classes[name]
        grid.addWidget(_name_cell(nc, name), r, 0)
        spins = {}
        for c, (f, _label) in enumerate(_NC_FIELDS, start=1):
            sp = _spin(getattr(nc, f, 0.0)); grid.addWidget(sp, r, c); spins[f] = sp
        pr = _spin(getattr(nc, "priority", 0), is_int=True, width=64)
        grid.addWidget(pr, r, len(_NC_FIELDS) + 1); spins["priority"] = pr
        rows_map[name] = spins
    area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame)
    area.setWidget(grid_w); area.setFixedHeight(300)
    lay.addWidget(area)
    lay.addWidget(W.eyebrow("Values In Millimetres, Aligned To OSH Park And KiCad Design Rules"))

    # Member nets: which nets fall in each class, colour-coded and editable.
    lay.addWidget(W.eyebrow("Member Nets By Class"))
    mem = W.Card(pad=14)
    net_edits = {}
    for name in mgr.list_netclasses():
        nc = mgr.net_classes[name]
        row = QHBoxLayout(); row.setSpacing(10)
        lab = QLabel(name); lab.setFont(T.mono_font(10)); lab.setFixedWidth(96)
        def _col(lab=lab, nc=nc):
            lab.setStyleSheet(f"color:{nc.color};background:transparent;")
        W.register_restyle(_col); _col()
        recolor.setdefault(nc.name, []).append(_col)
        edit = QLineEdit(", ".join(nc.patterns or []))
        edit.setPlaceholderText("Net names or patterns, comma separated")
        edit.setToolTip("The nets assigned to this class. Edit and Sync to write them into the projects.")
        net_edits[name] = edit
        row.addWidget(lab); row.addWidget(edit, 1)
        mem.body.addLayout(row)
    lay.addWidget(mem)

    status = QVBoxLayout(); lay.addLayout(status); lay.addStretch(1)

    def apply_edits():
        for name, spins in rows_map.items():
            nc = mgr.net_classes[name]
            for f, sp in spins.items():
                setattr(nc, f, int(sp.value()) if f == "priority" else float(sp.value()))
        for name, edit in net_edits.items():
            mgr.net_classes[name].patterns = [s.strip() for s in edit.text().split(",") if s.strip()]

    def validate():
        clear_layout(status); apply_edits()
        issues = ncm.validate_netclasses(mgr)
        if not issues:
            status.addWidget(W.tag("All Classes Meet The Fab Minimums", "ok"))
        else:
            for iss in issues:
                status.addWidget(W.body(f"{iss.get('netclass', '')}: {iss.get('issue', '')}", dim=True))

    def sync():
        apply_edits()
        if not state.projects:
            ctx.services.log("No projects to sync."); return

        def job():
            n = 0
            for p in state.projects:
                pro = kicad_tools.project_pro_file(p)
                if pro and mgr.save_to_project(pro):
                    n += 1
            return n

        run_populate(ctx, job, lambda n, ok: ctx.services.log(f"Synced net classes to {n} project(s)."),
                     busy="Syncing net classes...")

    b_val.clicked.connect(validate)
    b_sync.clicked.connect(sync)
    return root


# ── Board Setup — editable + save ────────────────────────────────────────────
def _boardsetup_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    boards = state.boards() if state.project else []
    bar = QHBoxLayout(); bar.addWidget(W.eyebrow("Board Setup")); bar.addStretch(1)
    b_save = W.btn("Save To Board", "primary", "Write these values into the board file (a .bak is kept)")
    bar.addWidget(b_save); lay.addLayout(bar)
    if not boards:
        lay.addWidget(W.body("No board found in the project.", dim=True)); lay.addStretch(1); return root
    board = boards[0]
    try:
        setup = nd_board_setup.load_board_setup(board, include_aliases=False)
    except Exception as e:  # noqa: BLE001
        lay.addWidget(W.body(f"Could not read board setup: {e}", dim=True)); lay.addStretch(1); return root

    card = W.Card(pad=16)
    fields = {}
    explicit = set(setup)

    def _row(key, label_extra=""):
        row = QHBoxLayout(); row.setSpacing(12)
        lab = W.body(key.replace("_", " ").title())
        row.addWidget(lab)
        if key not in explicit:
            row.addWidget(W.tag("Default", "mut"))
        row.addStretch(1)
        return row

    for key in sorted(nd_board_setup.SETUP_NUMERIC_KEYS):
        row = _row(key); sp = _spin(setup.get(key, 0.0), width=96, lo=-10.0, hi=50.0, decimals=4)
        row.addWidget(sp); fields[key] = ("num", sp); card.body.addLayout(row)
    for key in sorted(nd_board_setup.SETUP_COORD_KEYS):
        row = _row(key); val = setup.get(key, (0.0, 0.0))
        sx = _spin(val[0], width=96, lo=-1000.0, hi=1000.0); sy = _spin(val[1], width=96, lo=-1000.0, hi=1000.0)
        row.addWidget(sx); row.addWidget(sy)
        fields[key] = ("coord", (sx, sy)); card.body.addLayout(row)
    for key in sorted(nd_board_setup.SETUP_BOOL_KEYS):
        row = _row(key); cb = QCheckBox(); cb.setChecked(bool(setup.get(key, False)))
        row.addWidget(cb); fields[key] = ("bool", cb); card.body.addLayout(row)
    lay.addWidget(card)
    lay.addWidget(W.body("Values In Millimetres. Rows Marked Default Are Not Yet Set On The Board.", dim=True))
    lay.addWidget(W.body(board.name, dim=True, mono=True))
    lay.addStretch(1)

    def save():
        values = {}
        for key, (kind, w) in fields.items():
            if kind == "bool":
                values[key] = w.isChecked()
            elif kind == "coord":
                values[key] = (w[0].value(), w[1].value())
            else:
                values[key] = w.value()
        run_populate(ctx, lambda: nd_board_setup.save_board_setup(board, values, backup=True),
                     lambda r, ok: ctx.services.log("Board setup saved." if ok else "Save failed, see status."),
                     busy="Saving board setup...")
    b_save.clicked.connect(save)
    return root


def _ts():
    import time
    return time.strftime("%Y%m%d-%H%M%S")


# ── Fab Standard — presets + wired conform (preview + apply) ──────────────────
def _pcb_targets(p):
    """Text-size targets for nd_object_conform, drawn from the selected preset."""
    t = {}
    for layer, h, w in (("silk", "silk_text_height", "silk_text_thickness"),
                        ("fab", "fab_text_height", "fab_text_thickness")):
        hv = getattr(p, h, None); wv = getattr(p, w, None)
        if hv is not None and wv is not None:
            t[layer] = (hv, wv)
    return t


def _fab_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    presets = {"OSH Park 4-Layer": fabp.OSH_PARK_4LAYER, "OSH Park 2-Layer": fabp.OSH_PARK_2LAYER}
    sel = {"preset": fabp.OSH_PARK_4LAYER}

    top = QHBoxLayout(); top.setSpacing(10)
    top.addWidget(W.eyebrow("Preset"))
    top.addWidget(W.Segmented(list(presets.keys()), on_change=lambda n: sel.update(preset=presets[n]),
                              tip="Choose the fabrication preset to conform against"))
    top.addStretch(1)
    b_prev = W.btn("Preview Conform", "ghost", "Preview the text-size conform without writing any files")
    b_apply = W.btn("Apply", "primary", "Conform board text sizes to the preset (a .bak is kept per file)")
    top.addWidget(b_prev); top.addWidget(b_apply)
    lay.addLayout(top)

    grid = QHBoxLayout(); grid.setSpacing(16)
    for preset in (fabp.OSH_PARK_4LAYER, fabp.OSH_PARK_2LAYER):
        card = W.Card(pad=16)
        title = W.body(preset.name); title.setFont(T.ui_font(10, semibold=True))
        card.body.addWidget(title)
        card.body.addWidget(W.dl([
            ("Min Track", W.body(f"{round(preset.min_track_width, 4):g} mm", mono=True)),
            ("Min Clearance", W.body(f"{round(preset.min_clearance, 4):g} mm", mono=True)),
            ("Min Drill", W.body(f"{round(preset.min_drill, 4):g} mm", mono=True)),
            ("Copper", W.body(f"{round(preset.copper_oz, 2):g} oz", mono=True)),
            ("Finish", W.body(str(preset.finish))),
        ], key_width=120))
        grid.addWidget(card)
    grid.addStretch(1)
    lay.addLayout(grid)

    note_full = getattr(fabp.OSH_PARK_4LAYER, "verify_note", "")
    if note_full:
        v = W.Verdict("Verify Before Ordering",
                      "Confirm the current OSH Park service capabilities before you order. "
                      "These presets track the published four-layer and two-layer rules.", "warn")
        v.setToolTip(note_full)
        lay.addWidget(v)

    result = QVBoxLayout(); result.setSpacing(6); lay.addLayout(result)
    lay.addStretch(1)

    def run_conform(apply: bool):
        p = sel["preset"]
        files = list(state.boards() if state.project else [])
        if not files:
            ctx.services.log("No board found to conform."); return
        pcb_t = _pcb_targets(p)
        ts = _ts()
        clear_layout(result)
        result.addWidget(W.body("Applying..." if apply else "Previewing...", dim=True))

        def job():
            return conform.conform_project(files, pcb_t, {}, ts, dry_run=not apply)

        def populate(rep, ok):
            clear_layout(result)
            if not rep:
                result.addWidget(W.body("Conform unavailable, see status.", dim=True)); return
            total = rep.get("total") or rep.get("changed") or 0
            head = f"{'Applied' if apply else 'Preview'}   {total} Text Objects"
            result.addWidget(W.eyebrow(head))
            for f in rep.get("files", []) or []:
                nm = Path(str(f.get("path", ""))).name or str(f.get("path", ""))
                counts = f.get("counts")
                cnt = sum(counts.values()) if isinstance(counts, dict) else (f.get("changed") or 0)
                result.addWidget(W.body(f"{nm}   {cnt} Changed", dim=True, mono=True))
            ctx.services.log("Fabrication conform applied." if apply else "Fabrication conform preview ready.")

        run_populate(ctx, job, populate, busy=("Applying conform..." if apply else "Previewing conform..."))

    b_prev.clicked.connect(lambda: run_conform(False))
    b_apply.clicked.connect(lambda: run_conform(True))
    return root


# ── Git (real status + commit + push) ────────────────────────────────────────
def _git_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    lay.addWidget(W.eyebrow("Repository"))
    try:
        repo = nd_git.repo_root((ctx.cfg or {}).get("RepoRoot", "."))
    except Exception:  # noqa: BLE001
        repo = None
    if not repo:
        lay.addWidget(W.body("Not a git repository.", dim=True)); lay.addStretch(1); return root
    branch = nd_git.current_branch(repo) or "detached"
    st = nd_git.status(repo)
    clean = st.get("clean", True)
    info = W.Card(pad=16)
    for label, w in (("Branch", W.token(branch)),
                     ("Status", W.tag("Clean", "ok") if clean else W.tag(f"{len(st.get('modified', []))} Modified", "warn"))):
        row = QHBoxLayout(); row.addWidget(W.body(label)); row.addStretch(1); row.addWidget(w)
        info.body.addLayout(row)
    lay.addWidget(info)

    lay.addWidget(W.eyebrow("Changes"))
    changes = W.Card(pad=16)
    any_change = False
    for label, files, kind in (("Staged", st.get("staged", []), "ok"),
                               ("Modified", st.get("modified", []), "warn"),
                               ("Untracked", st.get("untracked", []), "mut")):
        for f in files[:15]:
            any_change = True
            row = QHBoxLayout(); row.setSpacing(8)
            row.addWidget(W.tag(label, kind)); row.addWidget(W.body(str(f), dim=True, mono=True)); row.addStretch(1)
            changes.body.addLayout(row)
    if not any_change:
        changes.body.addWidget(W.body("Working tree clean.", dim=True))
    lay.addWidget(changes)

    msg = QLineEdit(); msg.setPlaceholderText("Commit message"); msg.setMinimumHeight(34)
    lay.addWidget(msg)
    bar = QHBoxLayout(); bar.addStretch(1)

    def commit(push):
        message = msg.text().strip() or "Update"

        def job():
            ok, detail = nd_git.commit(repo, message)
            if ok and push:
                subprocess.run(["git", "-C", str(repo), "push"], capture_output=True, text=True)
            return (ok, detail)

        run_populate(ctx, job, lambda r, ok: ctx.services.log(
            f"Commit: {r[1] if r else 'failed'}" + (" (pushed)" if push and r and r[0] else "")),
            busy="Committing...")

    bar.addWidget(W.btn("Commit", "default", "Commit the staged and tracked changes", lambda: commit(False)))
    bar.addWidget(W.btn("Commit And Push", "primary", "Commit then push to the remote", lambda: commit(True)))
    lay.addLayout(bar)
    lay.addStretch(1)
    return root


class ProjectsFeature(F.Feature):
    id = "projects"
    title = "Projects"
    order = 30

    def build(self, ctx: F.Context) -> QWidget:
        state = ProjectsState(ctx.cfg)
        header = None
        if state.names():
            combo = QComboBox(); combo.addItems(state.names()); combo.setFixedWidth(220)
            combo.setToolTip("Choose a discovered KiCad project")
            if state.project:
                combo.setCurrentText(state.project.name)   # match the preferred default
            combo.currentTextChanged.connect(state.set_project)
            header = W.hstack(W.eyebrow("Project"), combo, spacing=8)
        panels = [
            ("Health", lambda c: _health_panel(c, state)),
            ("Bill of Materials", lambda c: _bom_panel(c, state)),
            ("Rename", lambda c: W.scroll_body(_rename_panel(c, state))),
            ("Net Classes", lambda c: W.scroll_body(_netclass_panel(c, state))),
            ("Board Setup", lambda c: W.scroll_body(_boardsetup_panel(c, state))),
            ("Fabrication Standard", lambda c: W.scroll_body(_fab_panel(c, state))),
            ("Git", lambda c: W.scroll_body(_git_panel(c, state))),
        ]
        return W.Workspace(ctx, "Projects", panels, header=header)


F.register(ProjectsFeature())
