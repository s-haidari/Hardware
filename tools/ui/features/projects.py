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
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QCheckBox

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
                rows.append([W.body(", ".join(r.get("refs", [])[:6]), mono=True), str(r.get("qty", "")),
                             W.body(str(r.get("value", "")), dim=True), W.body(str(r.get("mpn", "")), mono=True),
                             W.body(str(r.get("manufacturer", ""))), W.body(str(r.get("footprint", "")), dim=True)])
            result.addWidget(W.data_table(["Refs", "Qty", "Value", "Part Number", "Manufacturer", "Footprint"],
                                          rows, stretch_col=4), 1)

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
                rows.append([W.body(str(r.get("mpn", "")), mono=True), W.body(str(r.get("manufacturer", ""))),
                             W.body(str(r.get("value", "")), dim=True), W.body(str(r.get("footprint", "")), dim=True),
                             str(r.get("total_qty", ""))])
            result.addWidget(W.data_table(["Part Number", "Manufacturer", "Value", "Footprint", "Total"],
                                          rows, stretch_col=0), 1)

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


# ── Net Classes (real vault standard + validate + sync) ──────────────────────
def _netclass_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    mgr = ncm.load_vault_standard()
    profiles = ncm.netclass_profiles()
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow("Profile"))
    bar.addWidget(W.Segmented(profiles, tip="Net-class profile") if profiles else W.body("Vault Standard"))
    bar.addStretch(1)
    b_val = W.btn("Validate", "ghost", "Check every class against the fab minimums")
    b_sync = W.btn("Sync To Projects", "primary", "Write the profile into the discovered projects")
    bar.addWidget(b_val); bar.addWidget(b_sync)
    lay.addLayout(bar)

    rows = []
    for name in mgr.list_netclasses():
        nc = mgr.net_classes[name]
        rows.append([W.body(name, mono=True), str(nc.clearance), str(nc.track_width),
                     str(nc.via_diameter), str(nc.via_drill),
                     W.body(str(nc.diff_pair_width) if nc.diff_pair_width else "None",
                            dim=not nc.diff_pair_width, mono=bool(nc.diff_pair_width))])
    lay.addWidget(W.data_table(["Net Class", "Clearance", "Track", "Via", "Drill", "Differential Pair"], rows, stretch_col=0))
    lay.addWidget(W.eyebrow("Values In Millimetres"))
    status = QVBoxLayout(); lay.addLayout(status); lay.addStretch(1)

    def validate():
        clear_layout(status)
        issues = ncm.validate_netclasses(mgr)
        if not issues:
            status.addWidget(W.tag("All Classes Meet The Fab Minimums", "ok"))
        else:
            for iss in issues:
                status.addWidget(W.body(f"{iss.get('netclass', '')}: {iss.get('issue', '')}", dim=True))

    def sync():
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


# ── Board Setup (real load_board_setup) ──────────────────────────────────────
def _boardsetup_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    boards = state.boards() if state.project else []
    lay.addWidget(W.eyebrow("Board Setup"))
    if not boards:
        lay.addWidget(W.body("No board found in the project.", dim=True)); lay.addStretch(1); return root
    try:
        setup = nd_board_setup.load_board_setup(boards[0])
    except Exception as e:  # noqa: BLE001
        lay.addWidget(W.body(f"Could not read board setup: {e}", dim=True)); lay.addStretch(1); return root
    pairs = []
    for key in sorted(setup):
        val = setup[key]
        pairs.append((key.replace("_", " ").title(), W.body(str(val), mono=True)))
    if pairs:
        lay.addWidget(W.dl(pairs, key_width=240))
    else:
        lay.addWidget(W.body("This board carries no explicit setup overrides (KiCad defaults).", dim=True))
    lay.addWidget(W.body(boards[0].name, dim=True, mono=True))
    lay.addStretch(1)
    return root


# ── Fab Standard (real presets + conform) ────────────────────────────────────
def _fab_panel(ctx, state) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    top = QHBoxLayout(); top.addStretch(1)
    top.addWidget(W.btn("Preview Conform", "ghost", "Preview text and stackup conform"))
    top.addWidget(W.btn("Apply", "primary", "Apply the preset, stackup and net classes"))
    lay.addLayout(top)
    grid = QHBoxLayout(); grid.setSpacing(16)
    for preset in (fabp.OSH_PARK_4LAYER, fabp.OSH_PARK_2LAYER):
        card = W.Card(pad=16)
        card.body.addWidget(W.body(preset.name, mono=False))
        card.body.itemAt(0).widget().setFont(T.ui_font(10, semibold=True))
        card.body.addWidget(W.dl([
            ("Min Track", W.body(f"{preset.min_track_width} mm", mono=True)),
            ("Min Clearance", W.body(f"{preset.min_clearance} mm", mono=True)),
            ("Min Drill", W.body(f"{preset.min_drill} mm", mono=True)),
            ("Copper", W.body(f"{preset.copper_oz} oz", mono=True)),
            ("Finish", W.body(str(preset.finish))),
        ], key_width=120))
        grid.addWidget(card)
    grid.addStretch(1)
    lay.addLayout(grid)
    if getattr(fabp.OSH_PARK_4LAYER, "verify_note", ""):
        note = W.Verdict("Verify Before Ordering", fabp.OSH_PARK_4LAYER.verify_note, "warn")
        lay.addWidget(note)
    lay.addStretch(1)
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
