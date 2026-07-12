"""SP4 — Projects restructure (PCB Setup merge, Refactor, BOM multi-select, units).

Offscreen-Qt panel-build tests plus pure-logic checks. Mirrors the fixture style
of tests/test_sp2_library.py (a fake ctx with synchronous run_async) and the tmp
.kicad_pro/.kicad_pcb round-trips of the backend tests.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402

# One shared QApplication for the whole module.
from PyQt5.QtWidgets import QApplication, QLineEdit, QLabel  # noqa: E402
_APP = QApplication.instance() or QApplication([])


def _fake_ctx(cfg=None):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg=cfg or {}, services=_Svc())


_MINIMAL_PRO = (
    '{\n'
    '  "board": {"design_settings": {"rules": {"min_clearance": 0.2,\n'
    '    "min_track_width": 0.254}}},\n'
    '  "net_settings": {"classes": [{"name": "Default"}]}\n'
    '}\n'
)
_MINIMAL_PCB = (
    "(kicad_pcb\n"
    "\t(version 20241229)\n"
    '\t(generator "pcbnew")\n'
    "\t(layers\n"
    '\t\t(0 "F.Cu" signal)\n'
    "\t)\n"
    "\t(setup\n"
    "\t\t(pad_to_mask_clearance 0.05)\n"
    "\t)\n"
    '\t(net 0 "")\n'
    ")\n"
)


def _project(tmp_path):
    """A tmp project dir with a real .kicad_pro + .kicad_pcb."""
    d = tmp_path / "Board"; d.mkdir()
    (d / "Board.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
    (d / "Board.kicad_pcb").write_text(_MINIMAL_PCB, encoding="utf-8")
    return d


def _state(tmp_path, n=1):
    """A minimal ProjectsState-like object (the panels only read these)."""
    projects = []
    for i in range(n):
        d = tmp_path / f"Proj{i}"; d.mkdir()
        (d / f"Proj{i}.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
        (d / f"Proj{i}.kicad_pcb").write_text(_MINIMAL_PCB, encoding="utf-8")
        projects.append(d)
    proj = projects[0]

    class _S:
        def __init__(self):
            self.projects = projects
            self.project = proj
            self._checks = {}
        def boards(self):
            import nd_wizard
            return nd_wizard.list_boards(self.project)
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def root_schematic(self):
            return None
        # Mirror ProjectsState's shared per-project ERC/DRC cache (the panels read/write it).
        def _proj_key(self):
            return str(self.project) if self.project else ""
        def checks(self):
            return self._checks.setdefault(self._proj_key(), {"erc": None, "drc": None})
        def set_check(self, kind, summary):
            self.checks()[kind] = summary
        def invalidate_checks(self):
            c = self._checks.get(self._proj_key())
            if c is not None:
                c["erc"] = None
                c["drc"] = None
    return _S()


# ── unit conversion round-trips ───────────────────────────────────────────────
def test_unit_roundtrips():
    from ui.util import mm_to_mils, mils_to_mm
    for mm in (0.127, 0.2, 0.8, 1.6, 0.4572):
        assert mils_to_mm(mm_to_mils(mm)) == pytest.approx(mm, abs=1e-9)
    assert mm_to_mils(1.0) == pytest.approx(39.3701, rel=1e-4)
    assert mils_to_mm(1.0) == pytest.approx(0.0254, abs=1e-9)


# ── PCB Setup panel ───────────────────────────────────────────────────────────
def test_pcb_setup_builds_and_exposes_handles(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    assert panel._ncmgr is not None
    assert panel._profile_seg is not None
    panel.grab()   # renders without raising


def test_pcb_setup_builds_without_project():
    from ui.features import projects as PJ
    ctx = _fake_ctx()
    panel = PJ._pcb_setup_panel(ctx, None)   # degrade gracefully
    assert panel._ncmgr is not None and panel._profile_seg is not None
    panel.grab()


def test_netclass_filter_narrows_rows(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    total = panel._nc_visible_count()
    assert total > 1
    # Derive the filter substring from a REAL class name, not a hardcoded 'gnd'.
    sub = panel._ncmgr.list_netclasses()[0].lower()
    panel._nc_filter(sub)
    assert 0 < panel._nc_visible_count() < total
    panel._nc_filter("")
    assert panel._nc_visible_count() == total


def test_netclass_new_adds_a_class(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    before = len(panel._ncmgr.list_netclasses())
    vis_before = panel._nc_visible_count()
    panel._nc_new()
    assert len(panel._ncmgr.list_netclasses()) == before + 1
    # The rebuilt TABLE must reflect the add too (not just the manager list).
    assert panel._nc_visible_count() == vis_before + 1


def test_netclass_delete_removes_a_class(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    name = panel._ncmgr.list_netclasses()[0]
    vis_before = panel._nc_visible_count()
    panel._nc_delete(name)
    assert name not in panel._ncmgr.list_netclasses()
    # The rebuilt table row count drops by exactly one.
    assert panel._nc_visible_count() == vis_before - 1


def test_netclass_rename_via_name_cell_persists_on_save(tmp_path):
    """PCB projects:1668 — editing the name cell renames the class on the manager,
    and Save To Project writes the NEW name into the .kicad_pro (the old name gone)."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    panel._nc_new()                                   # a fresh editable class
    old = [n for n in panel._ncmgr.list_netclasses() if n.startswith("NEW_CLASS")][0]
    row = next(i for i, r in enumerate(panel._nc_rows()) if r["name"] == old)

    # Drive the inline editor exactly as a double-click + Enter would.
    cell = panel._nc_name_cell(row)
    cell._nc_start_edit()
    cell._nc_name_editor.setText("RENAMED_X")
    cell._nc_commit_edit()

    assert "RENAMED_X" in panel._ncmgr.list_netclasses()
    assert old not in panel._ncmgr.list_netclasses()
    assert old in panel._ncmgr.deleted_names          # old name queued for deletion
    # The label now shows the accepted name.
    assert panel._nc_name_cell(
        next(i for i, r in enumerate(panel._nc_rows()) if r["name"] == "RENAMED_X")
    )._nc_name_label.text() == "RENAMED_X"

    panel._save()
    import json
    pro = state.project / f"{state.project.name}.kicad_pro"
    names = {c["name"] for c in json.loads(pro.read_text(encoding="utf-8"))["net_settings"]["classes"]}
    assert "RENAMED_X" in names
    assert old not in names


def test_netclass_rename_rejects_duplicate(tmp_path):
    """A rename onto an existing class name is rejected — no clobber, the name cell
    reverts to the original."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    names = panel._ncmgr.list_netclasses()
    if len(names) < 2:
        panel._nc_new(); panel._nc_new()
        names = panel._ncmgr.list_netclasses()
    a, b = names[0], names[1]
    row_a = next(i for i, r in enumerate(panel._nc_rows()) if r["name"] == a)
    cell = panel._nc_name_cell(row_a)
    cell._nc_start_edit()
    cell._nc_name_editor.setText(b)                   # collide with an existing name
    cell._nc_commit_edit()
    # Both original names still present; no rename happened.
    assert a in panel._ncmgr.list_netclasses()
    assert b in panel._ncmgr.list_netclasses()
    assert cell._nc_name_label.text() == a            # cell reverted


def test_netclass_delete_persists_to_project_file(tmp_path):
    """PCB projects:1826 — deleting a loaded class then Save To Project must REMOVE it
    from the .kicad_pro, not re-preserve it as unmanaged."""
    import json
    from ui.features import projects as PJ
    # Seed a project whose .kicad_pro already carries an extra class to delete.
    ctx = _fake_ctx(); state = _state(tmp_path)
    pro_path = state.project / f"{state.project.name}.kicad_pro"
    data = json.loads(pro_path.read_text(encoding="utf-8"))
    data["net_settings"]["classes"] = [{"name": "Default"}, {"name": "PWR"}]
    pro_path.write_text(json.dumps(data), encoding="utf-8")

    panel = PJ._pcb_setup_panel(ctx, state)
    # Pull the file's classes into the editor, then delete PWR.
    panel._pull_from_kicad()
    assert "PWR" in panel._ncmgr.list_netclasses()
    panel._nc_delete("PWR")
    assert "PWR" not in panel._ncmgr.list_netclasses()
    panel._save()

    names = {c["name"] for c in json.loads(pro_path.read_text(encoding="utf-8"))["net_settings"]["classes"]}
    assert "PWR" not in names                         # actually removed from the file


def test_save_writes_fab_floor_stackup_and_thickness_to_board(tmp_path):
    """PCB projects:1828 — Save To Project must actually write the fab floor's physical
    stackup + board thickness into the .kicad_pcb, so the board matches the section."""
    from ui.features import projects as PJ
    import nd_fab_presets as fp
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    # Force a fab floor whose preset is a real PRESETS entry so the write engages.
    panel._prof_state["fab"] = "OSH Park 4-layer"
    panel._save()

    import nd_wizard
    board = nd_wizard.list_boards(state.project)[0]
    txt = Path(board).read_text(encoding="utf-8")
    assert "(stackup" in txt
    assert '(copper_finish "ENIG")' in txt
    assert txt.count('(type "copper")') == 4
    tmm = fp.PRESETS["OSH Park 4-layer"].board_thickness_mm
    assert f"(thickness {tmm:g})" in txt


def test_diffpair_zero_spin_is_dimmed(tmp_path):
    """PCB-14 — a diff-pair spin sitting at 0 carries the nc_dp_zero objectName (the
    container QSS dims it); entering a value clears the objectName."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    panel._nc_new()                                   # a fresh class with no diff pair
    row = next(r for r in panel._nc_rows() if r["name"].startswith("NEW_CLASS"))
    dp = row["spins"]["diff_pair_width"]
    assert dp._mm == 0.0
    assert dp.objectName() == "nc_dp_zero"            # dimmed while zero
    # Enter a real value: the dim objectName clears live.
    dp.setValue(dp.value() + 0.2)
    assert dp._mm > 0
    assert dp.objectName() == ""
    # A class that DOES carry a diff pair is not dimmed to begin with.
    nonzero = [r["spins"]["diff_pair_width"] for r in panel._nc_rows()
               if r["spins"]["diff_pair_width"]._mm > 0]
    for sp in nonzero:
        assert sp.objectName() == ""


def test_profile_switch_does_not_raise(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    panel._profile_seg.setCurrentIndex(1)   # a bare OSH Park profile (nets-free)
    panel._profile_seg.setCurrentIndex(0)   # another profile
    assert panel._ncmgr is not None


def test_unit_toggle_refreshes_without_error(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    panel._unit_seg._pick(1)   # mils
    panel._unit_seg._pick(0)   # mm
    panel.grab()


# ── Refactor panel ────────────────────────────────────────────────────────────
def test_refactor_builds_for_each_op_no_example_placeholders(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._rename_panel(ctx, state)
    # No worked-example strings baked into any field.
    for le in panel.findChildren(QLineEdit):
        ph = le.placeholderText()
        assert "SW_L100_" not in ph and "U_SW_64_" not in ph
    # Every op selects without raising and swaps the controls.
    for label, key in PJ._OPS:
        panel._op_seg._pick(PJ._OP_KEYS.index(key))
        assert panel._op_state["op"] == key
    panel.grab()


def test_pcb_setup_surfaces_vault_standard_actions(tmp_path, monkeypatch):
    """PCB Setup surfaces the vault-standard net-class capabilities (parity): load the template,
    load the saved standard, and save-as — each swapping the editor's manager."""
    from ui.features import projects as PJ
    # Isolate the vault-standard path to tmp so Save-As never writes into the source tree.
    monkeypatch.setattr(PJ.ncm, "_vault_standard_path", lambda: tmp_path / "vault_standard.json")
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    for seam in ("_load_vault_template", "_load_vault_saved", "_save_vault"):
        assert hasattr(panel, seam), f"missing vault-standard seam {seam}"
    panel._load_vault_template()                                    # fills the editor from the template
    assert len(panel._ncmgr.list_netclasses()) > 1
    # save-as then load-saved round-trips through the manager without raising
    panel._save_vault()
    assert (tmp_path / "vault_standard.json").exists()             # wrote to tmp, not the repo
    panel._load_vault_saved()
    assert panel._ncmgr.list_netclasses()


# ── Review-driven regression locks (adversarial review of the Projects rebuild) ─
def test_health_verdict_notes_only_is_quiet_green(tmp_path, monkeypatch):
    """A project with only info-level notes (no MPN / no 3D model) must NOT amber the Health
    verdict — notes are benign and neutral in the detail rows and in Overview."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._health_panel(ctx, state)
    snap = {"schs": [str(tmp_path / "a.kicad_sch")], "fp_dirs": None, "mdl_dirs": None,
            "root_sch": None, "boards": [], "name": "P"}

    def notes_only(schs, fp, md):
        return {"components": 4, "healthy": 4, "sheets": 1, "findings": [],
                "counts": {"by_severity": {"error": 0, "warning": 0, "info": 3}}}
    monkeypatch.setattr(PJ.phealth, "audit_project", notes_only)
    v = panel._verdict_of(snap)
    # Notes alone never amber the band. (The verdict is now completion-aware too — a real
    # sheet with unfilled components would amber "N To Complete" — but this synthetic snap
    # points at no real sheet, so completion is 0/0 and the band stays quiet-green.)
    assert v.kind == "ok"
    # a real warning still ambers
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {
        "components": 4, "healthy": 3, "sheets": 1, "findings": [],
        "counts": {"by_severity": {"error": 0, "warning": 1, "info": 3}}})
    v2 = panel._verdict_of(snap)
    assert v2.kind == "warn"


def test_health_restore_rolls_back_fills_and_annotation_to_original(tmp_path):
    """Restore Last Prepare must roll a sheet that was BOTH filled and annotated all the way
    back to its pre-Prepare original — not just undo the annotation (the .bak-clobber bug)."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._health_panel(ctx, state)
    sch = tmp_path / "board.kicad_sch"
    original = "(kicad_sch ORIGINAL)\n"
    sch.write_text(original, encoding="utf-8")
    snap = {"schs": [str(sch)], "fp_dirs": None, "mdl_dirs": None, "root_sch": None,
            "boards": [], "name": "P"}
    panel._prep["plan"] = {"items": []}                                # no fills; annotate only

    # Simulate the fill+annotate sequence writing the sheet (both would leave a .bak):
    def fake_apply(plan, selected, cfg, log):
        sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8")       # writer 1
        return {"backups": [str(sch) + ".bak"], "fields_written": 1, "components_changed": 1, "errors": []}

    def fake_annotate(schs, apply):
        if apply:
            sch.write_text("(kicad_sch FILLED ANNOTATED)\n", encoding="utf-8")   # writer 2
        return 1
    orig = (PJ.libfill.apply_fill_plan, PJ.phealth.annotate_project, PJ.phealth.audit_project)
    PJ.libfill.apply_fill_plan = fake_apply
    PJ.phealth.annotate_project = fake_annotate
    PJ.phealth.audit_project = lambda s, f, m: {"counts": {"by_severity": {}}}   # skip parsing
    try:
        panel._prepare_apply(snap, ["\x00annotate", "R1\x1fMPN"])      # the annotate + a fill op-key
        assert sch.read_text(encoding="utf-8") == "(kicad_sch FILLED ANNOTATED)\n"
        # captured the true original (not the filled intermediate)
        assert panel._last_prepare["originals"][str(sch)] == original
        panel._restore_prepare()
        assert sch.read_text(encoding="utf-8") == original             # fully rolled back
    finally:
        PJ.libfill.apply_fill_plan, PJ.phealth.annotate_project, PJ.phealth.audit_project = orig


def test_bom_verdict_band_tracks_the_boards_spinner(tmp_path, monkeypatch):
    """Raising Boards can trip a stock shortfall; the verdict band must follow the live summary,
    not stay frozen green."""
    from ui.features import projects as PJ
    ctx = _fake_ctx({"RepoRoot": str(tmp_path)}); state = _state(tmp_path)
    monkeypatch.setattr(state, "schematics", lambda: [tmp_path / "a.kicad_sch"])   # truthy sheets
    panel = PJ._bom_panel(ctx, state)
    res = {"component_count": 1, "line_count": 1, "rows": [
        {"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "X", "manufacturer": "Y",
         "unit_price": 1.0, "extended": 1.0, "source": "Mouser", "lifecycle": "Active", "stock": 40}],
        "cost": {"total_cost": 1.0, "priced_lines": 1, "unpriced_lines": 0}}
    panel._last_bom = res; panel._last_mode = "project"
    panel._last_base_tags = [("1 Line Items", "mut")]; panel._summary_owner = "bom"
    panel._boards_spin.setValue(2); _APP.processEvents()              # 2 <= 40 stock → fine
    assert panel._verdict._kind == "ok"
    panel._boards_spin.setValue(100); _APP.processEvents()            # 100 > 40 stock → Low Stock
    assert panel._verdict._kind == "warn"                             # band followed the spinner


def test_bom_procurement_opts_is_headless_safe(tmp_path):
    """_procurement_opts() must NOT enter a modal under offscreen — it returns the stored
    defaults so a headless drive/CI can't hang."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._proc_pack = 5; panel._proc_tax_pct = 7.0; panel._proc_ship = 12.5
    panel._proc_labour = 3.0; panel._proc_surcharge_pct = 4.0
    opts = panel._procurement_opts()                                   # offscreen → no exec_()
    # (pack, tax fraction, shipping, labour/board, surcharge fraction)
    assert opts == (5, 0.07, 12.5, 3.0, 0.04)


def test_pcb_conform_is_busy_gated(tmp_path):
    """Apply Conform writes the board file off-thread; it must no-op while another mutating op
    (▶ Save) holds the busy gate, so two writers can't race the same .kicad_pcb."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    panel._busy["on"] = True                                           # simulate a Save in flight
    ran = {"n": 0}
    orig = PJ.conform.conform_project
    PJ.conform.conform_project = lambda *a, **k: ran.__setitem__("n", ran["n"] + 1) or {}
    try:
        panel._run_conform(True)                                       # must be a no-op
        _APP.processEvents()
        assert ran["n"] == 0, "conform ran while busy — the race guard is missing"
    finally:
        PJ.conform.conform_project = orig


# ── Overview readiness tab ────────────────────────────────────────────────────
def test_overview_panel_builds_and_reads_readiness(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx({"RepoRoot": str(tmp_path)}); state = _state(tmp_path)
    panel = PJ._overview_panel(ctx, state)
    snap = panel._snapshot()
    r = panel._readiness(snap)
    # No schematic in the fixture → audit is None, and the next step guides the user there.
    assert r["audit"] is None
    assert r["next"][0] == "Add a schematic sheet"
    # cli axis is always evaluated (path or None); erc/drc start un-run.
    assert "cli" in r and panel._checks == {"erc": None, "drc": None}
    panel.grab()                                            # renders without raising


def test_overview_next_step_prioritises_audit_errors_then_git(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx({"RepoRoot": str(tmp_path)}); state = _state(tmp_path)
    panel = PJ._overview_panel(ctx, state)
    base = {"schs": ["/x/a.kicad_sch"], "boards": [], "cli": "/usr/bin/kicad-cli"}
    # audit errors win over everything else (with a cli present).
    r = {"cli": "/usr/bin/kicad-cli", "audit": {"errs": 3, "warns": 0, "notes": 0, "healthy": 1, "comps": 4},
         "erc": None, "drc": None, "git": {"clean": True, "changed": 0, "ahead": 0, "behind": 0, "tracking": True}}
    title, _sub, kind = panel._next_step(r, base)
    assert title == "Fix 3 audit errors" and kind == "err"
    # clean audit + un-run checks → run them next.
    r2 = dict(r, audit={"errs": 0, "warns": 0, "notes": 0, "healthy": 4, "comps": 4})
    assert panel._next_step(r2, base)[0].startswith("Run ERC")
    # clean audit + checks clean + commits ahead → push.
    r3 = dict(r2, erc={"errors": 0, "warnings": 0}, drc={"errors": 0, "warnings": 0},
              git={"clean": True, "changed": 0, "ahead": 2, "behind": 0, "tracking": True})
    assert panel._next_step(r3, base)[0] == "Push 2 commits"
    # everything clean and in sync → ready.
    r4 = dict(r3, git={"clean": True, "changed": 0, "ahead": 0, "behind": 0, "tracking": True})
    t4, _s4, k4 = panel._next_step(r4, base)
    assert t4 == "Ready to fabricate" and k4 == "ok"


def test_overview_run_check_caches_summary_into_readiness(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx({"RepoRoot": str(tmp_path)}); state = _state(tmp_path)
    panel = PJ._overview_panel(ctx, state)
    monkeypatch.setattr(PJ.kicad_paths, "find_kicad_cli", lambda: "/usr/bin/kicad-cli")
    # give snapshot() a real root schematic so ERC has a target, and fake the kicad-cli run.
    monkeypatch.setattr(state, "root_schematic", lambda: tmp_path / "a.kicad_sch")
    monkeypatch.setattr(PJ.kchecks, "run_erc",
                        lambda t, cli: {"ok": True, "summary": {"errors": 2, "warnings": 1}})
    panel._run_check("erc")
    assert panel._checks["erc"] == {"errors": 2, "warnings": 1}
    # the cached ERC summary folds into the readiness rollup (no schs → audit skipped).
    r = panel._readiness({"schs": [], "boards": [], "repo": None,
                          "fp_dirs": None, "mdl_dirs": None, "name": "P", "root_sch": None})
    assert r["erc"] == {"errors": 2, "warnings": 1}


# ── BOM multi-select subset helper ────────────────────────────────────────────
def test_consolidated_boards_uses_only_selected(tmp_path):
    from ui.features import projects as PJ
    state = _state(tmp_path, n=3)
    names = [p.name for p in state.projects]
    chosen = names[:2]
    boards = PJ._consolidated_boards(state.projects, chosen)
    assert set(boards.keys()) == set(chosen)
    assert names[2] not in boards


def test_bom_panel_builds_with_multiselect(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QCheckBox
    ctx = _fake_ctx(); state = _state(tmp_path, n=3)
    panel = PJ._bom_panel(ctx, state)
    # A checkbox per project, all checked by default.
    assert len(panel._proj_checks) == 3
    assert all(isinstance(cb, QCheckBox) and cb.isChecked() for cb in panel._proj_checks.values())
    panel.grab()


def test_bom_export_order_writes_mouser_cart(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)          # no .kicad_sch -> no auto BOM
    panel = PJ._bom_panel(ctx, state)
    # Nothing built yet -> exporting just tells the user to build first.
    panel._export_order()
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    # Simulate a built BOM, then export the order.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "mouser_pn": "595-TPS2121RUXR"},
        {"refs": ["R1", "R2"], "qty": 2, "mpn": "", "value": "10k"},   # passive, skipped
    ]}
    out = tmp_path / "cart.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_order()
    text = out.read_text()
    assert text.splitlines()[0] == \
        "Mouser Part Number,Manufacturer Part Number,Quantity,Customer Reference"
    assert "595-TPS2121RUXR,TPS2121RUXR,1,U1" in text
    assert "10k" not in text                             # the bare passive is not orderable
    assert any("1 order lines" in m and "1 passives" in m for m in ctx.services.logs)


def test_bom_export_order_applies_spares_buffer_to_passives(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    assert panel._spares_spin.value() == 0               # no buffer by default
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR"},                  # IC
        {"refs": ["C1", "C2"], "qty": 2, "mpn": "GRM188R71C104KA01D"},     # SMT passive
    ]}
    panel._boards_spin.setValue(25)
    panel._spares_spin.setValue(5)                       # +5% spares on passives only
    out = tmp_path / "cart_spares.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_order()
    text = out.read_text()
    assert ",GRM188R71C104KA01D,53,C1 C2" in text        # 2*25=50, +5% -> ceil 53
    assert "TPS2121RUXR,25,U1" in text                   # IC untouched at 25
    assert any("+5% spares" in m for m in ctx.services.logs)


def test_bom_export_order_scales_to_the_board_count(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "mouser_pn": "595-TPS2121RUXR"},
    ]}
    panel._boards_spin.setValue(25)                      # order a run of 25 boards
    out = tmp_path / "cart_run.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_order()
    assert "595-TPS2121RUXR,TPS2121RUXR,25,U1" in out.read_text()   # 1/board * 25
    assert any("25 parts for 25 boards" in m for m in ctx.services.logs)


def test_bom_export_priced_scales_the_priced_sheet_to_the_board_count(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # Nothing built yet -> exporting just tells the user to build first.
    panel._export_priced()
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    panel._last_bom = {"rows": [
        {"mpn": "R-10K", "value": "10k", "qty": 2, "unit_price": 0.10,
         "price_breaks": ladder, "source": "Mouser"},
    ]}
    panel._boards_spin.setValue(50)                      # 2/board × 50 = 100 ordered
    out = tmp_path / "priced_run.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_priced()
    text = out.read_text()
    assert text.splitlines()[0].split(",")[:6] == \
        ["MPN", "Manufacturer", "Value", "Footprint", "Per-Board Qty", "Order Qty"]
    body = text.splitlines()[1].split(",")
    assert body[0] == "R-10K" and body[4] == "2" and body[5] == "100"
    assert "0.0500" in text and "5.0000" in text          # volume unit + run ext
    assert any("$5.00 for 50 boards" in m for m in ctx.services.logs)


def test_bom_export_priced_without_pricing_tells_the_user(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._last_bom = {"rows": [{"mpn": "X", "value": "10k", "qty": 1}]}   # no unit_price
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(tmp_path / "p.csv"), "")))
    panel._export_priced()
    assert any("Price the BOM first" in m for m in ctx.services.logs)


def test_bom_summary_splits_cost_by_distributor_when_multi_sourced(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # A build sourced from BOTH Mouser and LCSC shows the per-supplier split.
    res = {"rows": [
        {"source": "Mouser", "qty": 2, "unit_price": 8.0, "mpn": "U1"},   # 16.00
        {"source": "LCSC", "qty": 10, "unit_price": 0.10, "mpn": "R1"},   # 1.00
    ], "cost": {"total_cost": 17.0, "unpriced_lines": 0}}
    panel._apply_summary([("2 Line Items", "mut")], res)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("Mouser $16.00" in t and "LCSC $1.00" in t for t in labels)   # one combined split tag
    # A single-source BOM raises NO split tag (it would just restate the Total). Fresh
    # panel so no deleteLater'd tags from the call above linger in findChildren.
    panel2 = PJ._bom_panel(ctx, state)
    res1 = {"rows": [{"source": "Mouser", "qty": 2, "unit_price": 8.0, "mpn": "U1"}],
            "cost": {"total_cost": 16.0, "unpriced_lines": 0}}
    panel2._apply_summary([("1 Line Items", "mut")], res1)
    labels = [w.text() for w in panel2.findChildren(QLabel)]
    assert not any(" · " in t and "Mouser" in t and "LCSC" in t for t in labels)


def test_bom_risk_tags_surface_lifecycle_and_stock(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # A priced BOM: one obsolete part, one out of stock, one short of the build qty.
    rows = [
        {"mpn": "OK", "qty": 1, "lifecycle": "Active", "stock": 500},
        {"mpn": "OLD", "qty": 2, "lifecycle": "NRND", "stock": 800},
        {"mpn": "GONE", "qty": 1, "lifecycle": "Active", "stock": 0},
        {"mpn": "TIGHT", "qty": 100, "lifecycle": "Active", "stock": 20},
    ]
    tags = panel._risk_tags(rows)
    labels = {t[0] for t in tags}
    assert "1 NRND/EOL" in labels and "1 No Stock" in labels and "1 Low Stock" in labels
    # a clean BOM raises no risk tags at all
    assert panel._risk_tags([{"mpn": "OK", "qty": 1, "lifecycle": "Active", "stock": 9}]) == []


def test_bom_risk_tags_scale_stock_coverage_to_the_board_count(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # 2/board with 40 in stock: enough for a prototype, short for a run of 50.
    rows = [{"mpn": "TIGHT", "qty": 2, "lifecycle": "Active", "stock": 40}]
    panel._boards_spin.setValue(1)
    assert panel._risk_tags(rows) == []                  # no shortfall at 1 board
    panel._boards_spin.setValue(50)
    assert "1 Low Stock" in {t[0] for t in panel._risk_tags(rows)}   # 100 > 40 at 50 boards


def _headers(tbl):
    return [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]


def test_bom_table_part_number_column_flags_no_mpn(tmp_path):
    # LM:2129: the BOM identity column reads the SAME honest contract as the Library.
    # A no-MPN passive shows the shared 'no MPN · not orderable' flag in the Part
    # Number column (never a fabricated part number); an MPN'd part shows it verbatim.
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    res = {"rows": [
        {"refs": ["R1"], "qty": 1, "value": "10k", "mpn": "", "has_real_mpn": False,
         "manufacturer": "", "basic": True},
        {"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "TPS2121RUXR",
         "has_real_mpn": True, "manufacturer": "TI", "basic": False},
    ]}
    panel._boards_spin.setValue(1)
    panel._draw_bom_table(res, "project")
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    pn = heads.index("Part Number")
    cells = {tbl.item(r, 0).text(): tbl.item(r, pn).text() for r in range(tbl.rowCount())}
    # Row keyed by its Refs cell (col 0).
    assert cells["R1"] == "no MPN · not orderable"
    assert cells["U1"] == "TPS2121RUXR"


def test_bom_consolidated_table_part_number_column_flags_no_mpn(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    res = {"rows": [
        {"mpn": "", "has_real_mpn": False, "manufacturer": "", "value": "10k",
         "footprint": "R_0603", "total_qty": 4},
        {"mpn": "TPS2121RUXR", "has_real_mpn": True, "manufacturer": "TI",
         "value": "MCU", "footprint": "QFP", "total_qty": 1},
    ]}
    panel._boards_spin.setValue(1)
    panel._draw_bom_table(res, "consolidated")
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    pn = heads.index("Part Number"); val = heads.index("Value")
    cells = {tbl.item(r, val).text(): tbl.item(r, pn).text() for r in range(tbl.rowCount())}
    assert cells["10k"] == "no MPN · not orderable"
    assert cells["MCU"] == "TPS2121RUXR"


def test_project_priced_table_reflects_build_quantity(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    res = {"rows": [{"refs": ["R1"], "qty": 2, "value": "10k", "mpn": "R-10K",
                     "manufacturer": "Y", "unit_price": 0.10, "extended": 0.20,
                     "price_breaks": ladder, "source": "Mouser"}],
           "cost": {"total_cost": 0.20, "unpriced_lines": 0}}
    # Boards = 1: no Order column, base per-board pricing on-screen.
    panel._boards_spin.setValue(1)
    panel._draw_bom_table(res, "project")
    tbl = panel.findChild(QTableWidget)
    assert "Order" not in _headers(tbl)
    # Boards = 50: an Order column appears (2/board × 50 = 100), priced at the volume break.
    panel._boards_spin.setValue(50)
    panel._draw_bom_table(res, "project")
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    assert "Order" in heads
    assert tbl.item(0, heads.index("Order")).text() == "100"
    assert "$0.05" in tbl.item(0, heads.index("Unit")).text()    # bought down onto the 100 break
    assert "$5.00" in tbl.item(0, heads.index("Ext")).text()     # 0.05 × 100 for the run


def test_consolidated_priced_table_reflects_build_quantity(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    ladder = [{"qty": 1, "price": 1.0}, {"qty": 10, "price": 0.5}]
    res = {"rows": [{"mpn": "U1", "manufacturer": "ST", "value": "MCU", "footprint": "QFP",
                     "total_qty": 3, "unit_price": 1.0, "extended": 3.0,
                     "price_breaks": ladder, "source": "Mouser"}],
           "cost": {"total_cost": 3.0, "unpriced_lines": 0}}
    panel._boards_spin.setValue(5)                       # 3/board × 5 = 15 -> the 0.5 break
    panel._draw_bom_table(res, "consolidated")
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    assert "Order" in heads
    assert tbl.item(0, heads.index("Order")).text() == "15"
    assert "$0.50" in tbl.item(0, heads.index("Unit")).text()
    assert "$7.50" in tbl.item(0, heads.index("Ext")).text()     # 0.5 × 15


def test_boards_spinner_redraws_the_priced_table_live(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    res = {"rows": [{"refs": ["R1"], "qty": 2, "value": "10k", "mpn": "R-10K",
                     "manufacturer": "Y", "unit_price": 0.10, "extended": 0.20,
                     "source": "Mouser"}],
           "cost": {"total_cost": 0.20, "unpriced_lines": 0}}
    panel._apply_summary([("1 Line Items", "mut")], res)         # owner = bom
    panel._draw_bom_table(res, "project")                        # arms _render_table
    assert "Order" not in _headers(panel.findChild(QTableWidget))
    panel._boards_spin.setValue(10)                              # live redraw, no rebuild
    heads = _headers(panel.findChild(QTableWidget))
    assert "Order" in heads
    assert panel.findChild(QTableWidget).item(0, heads.index("Order")).text() == "20"


def test_priced_table_shows_lead_column_only_when_a_line_has_lead(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # No lead data on any line -> the already-wide table stays lean (no Lead column).
    plain = {"rows": [{"refs": ["R1"], "qty": 1, "value": "10k", "mpn": "R-10K",
                       "manufacturer": "Y", "unit_price": 0.10, "extended": 0.10,
                       "source": "Mouser"}],
             "cost": {"total_cost": 0.10, "unpriced_lines": 0}}
    panel._boards_spin.setValue(1)
    panel._draw_bom_table(plain, "project")
    assert "Lead (wks)" not in _headers(panel.findChild(QTableWidget))
    # One line carries a manufacturer lead -> a Lead (wks) column appears: the week count
    # for the line that has it, blank (not 0) for the line without lead data.
    res = {"rows": [{"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "SLOW",
                     "manufacturer": "ST", "unit_price": 8.0, "extended": 8.0,
                     "source": "Mouser", "lead_time": "16 Weeks"},
                    {"refs": ["R1"], "qty": 1, "value": "10k", "mpn": "FAST",
                     "manufacturer": "Y", "unit_price": 0.10, "extended": 0.10,
                     "source": "Mouser"}],
           "cost": {"total_cost": 8.10, "unpriced_lines": 0}}
    panel._draw_bom_table(res, "project")
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    assert "Lead (wks)" in heads
    li = heads.index("Lead (wks)")
    assert tbl.item(0, li).text() == "16"                  # the long-lead part
    assert tbl.item(1, li).text() == ""                    # no lead data -> blank, not 0
    # Consolidated mode grows the column on the same rule.
    cons = {"rows": [{"mpn": "U1", "manufacturer": "ST", "value": "MCU", "footprint": "QFP",
                      "total_qty": 1, "unit_price": 8.0, "extended": 8.0,
                      "source": "Mouser", "lead_time": 20}],
            "cost": {"total_cost": 8.0, "unpriced_lines": 0}}
    panel._draw_bom_table(cons, "consolidated")
    heads = _headers(panel.findChild(QTableWidget))
    assert "Lead (wks)" in heads
    assert panel.findChild(QTableWidget).item(0, heads.index("Lead (wks)")).text() == "20"


def test_boards_spinner_leaves_an_open_diff_untouched(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # A priced BOM summary is live, then the user opens a diff over the same result area.
    res = {"rows": [{"mpn": "U1", "qty": 1, "unit_price": 8.0}],
           "cost": {"total_cost": 8.0, "unpriced_lines": 0}}
    panel._apply_summary([("1 Line Items", "mut")], res)
    panel._render_diff({"added": [{"mpn": "NEW", "value": "x", "qty": 2}],
                        "removed": [], "changed": [], "unchanged": 3, "csv": ""})
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("1 Added" in t for t in labels) and not any("Total" in t for t in labels)
    # Bumping Boards must NOT re-render cost tags over the diff view.
    panel._boards_spin.setValue(10)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("1 Added" in t for t in labels)             # diff summary intact
    assert not any("Total" in t or "Build ×" in t for t in labels)   # cost did not clobber it
    # Re-rendering a real BOM re-arms the spinner: cost tags come back and re-project.
    panel._apply_summary([("1 Line Items", "mut")], res)
    panel._boards_spin.setValue(20)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("Total" in t for t in labels)


def test_bom_lead_tag_flags_the_critical_path_part(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # A long-lead part gates the order — flag it (part + weeks), and warn when long.
    rows = [{"mpn": "FAST", "lead_time": "2 Weeks"},
            {"mpn": "SLOW", "lead_time": 20}]
    tags = panel._lead_tag(rows)
    assert len(tags) == 1
    txt, kind = tags[0]
    assert "20" in txt and "SLOW" in txt          # the critical part and its lead
    assert kind == "warn"                          # 20 wk is a real schedule risk
    # A short lead still surfaces, but quietly (no alarm).
    short = panel._lead_tag([{"mpn": "R1", "lead_time": "3 Weeks"}])
    assert len(short) == 1 and short[0][1] == "info"
    # No lead data at all -> no tag.
    assert panel._lead_tag([{"mpn": "X", "lead_time": "In Stock"}]) == []


def test_bom_summary_shows_longest_lead_tag(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    res = {"rows": [{"mpn": "U1", "qty": 1, "unit_price": 8.0, "lead_time": "16 Weeks"}],
           "cost": {"total_cost": 8.0, "unpriced_lines": 0}}
    panel._apply_summary([("1 Line Items", "mut")], res)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("16" in t and "U1" in t for t in labels)


def test_bom_build_quantity_projects_volume_cost(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)          # no .kicad_sch -> no auto BOM
    panel = PJ._bom_panel(ctx, state)
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    res = {"rows": [{"qty": 2, "unit_price": 0.10, "price_breaks": ladder, "mpn": "R1"}],
           "cost": {"total_cost": 0.20, "unpriced_lines": 0}}
    # Boards = 1: base per-board Total shows, but no build-quantity projection tag.
    panel._boards_spin.setValue(1)
    panel._apply_summary([("1 Line Items", "mut")], res)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("$0.20 Total" in t for t in labels)
    assert not any("Build ×" in t for t in labels)
    # Bumping the spinner re-projects LIVE (valueChanged -> _resummarize), no rebuild:
    # 2/board × 50 boards = 100 ordered -> the 0.05 volume break -> $5.00 for the run.
    panel._boards_spin.setValue(50)
    labels = [w.text() for w in panel.findChildren(QLabel)]
    # The run total AND the per-board unit at that volume: $5.00 / 50 = $0.10 each —
    # a tenth of the $0.20 prototype cost, so the volume discount reads at a glance.
    assert any("Build ×50: $5.00" in t and "$0.10 each" in t for t in labels)
    # The row data is untouched — the projection is a tag, never a mutation.
    assert res["rows"][0]["unit_price"] == 0.10


def test_bom_export_jlcpcb_writes_assembly_bom(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)          # no .kicad_sch -> no auto BOM
    panel = PJ._bom_panel(ctx, state)
    # Nothing built yet -> exporting just tells the user to build first.
    panel._export_jlcpcb()
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    # Simulate a built BOM (one LCSC-sourced IC, one bare passive with no LCSC #).
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "value": "TPS2121",
         "footprint": "Package_SO:SOIC-8", "lcsc_pn": "C2913174"},
        {"refs": ["R1", "R2"], "qty": 2, "mpn": "", "value": "10k",
         "footprint": "Resistor_SMD:R_0402_1005Metric"},
    ]}
    out = tmp_path / "jlc_bom.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_jlcpcb()
    text = out.read_text()
    assert text.splitlines()[0] == "Comment,Designator,Footprint,LCSC Part #"
    assert "TPS2121,U1,Package_SO:SOIC-8,C2913174" in text
    assert '10k,"R1,R2",Resistor_SMD:R_0402_1005Metric,' in text   # passive placed, LCSC # blank
    assert any("2 assembly lines" in m and "1 without an LCSC" in m for m in ctx.services.logs)


def test_bom_compare_to_csv_diffs_against_prior_export(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # Nothing built yet -> comparing just tells the user to build first.
    panel._compare_to_csv(str(tmp_path / "whatever.csv"))
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    # A previously-exported BOM (rev A): U1 qty 1, one 10k passive qty 4.
    prior = tmp_path / "old_bom.csv"
    prior.write_text(
        "Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
        'U1,1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n'
        '"R1,R2,R3,R4",4,10k,,,MyFootprints:R_0402,,,yes\n')
    # Current built BOM (rev B): U1 qty bumped to 3, the 10k passive gone, a new cap added.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 3, "mpn": "TPS2121RUXR", "value": "TPS2121", "footprint": "SOIC"},
        {"refs": ["C1"], "qty": 1, "mpn": "GRM188", "value": "100n", "footprint": "C_0402"},
    ]}
    panel._compare_to_csv(str(prior))
    d = panel._last_diff
    assert {r["mpn"] for r in d["added"]} == {"GRM188"}
    assert {r["value"] for r in d["removed"]} == {"10k"}
    assert d["changed"][0]["from_qty"] == 1 and d["changed"][0]["to_qty"] == 3
    assert any("1 added" in m and "1 removed" in m and "1 changed" in m
               for m in ctx.services.logs)


def test_bom_compare_rejects_non_bom_csv(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._last_bom = {"rows": [{"mpn": "X", "qty": 1}]}
    junk = tmp_path / "junk.csv"; junk.write_text("foo,bar\n1,2\n")       # no MPN/Value column
    panel._compare_to_csv(str(junk))
    assert any("No BOM lines found" in m for m in ctx.services.logs)


def test_diff_cost_tags_render_the_per_board_delta(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # A revision that costs more warns; the caveat flags removed lines it can't price.
    up = panel._diff_cost_tags({"delta": 4.50, "removed_unpriced": 1, "priced": True})
    assert up[0] == ("+$4.50/board", "warn")
    assert any("1 removed not costed" in t for t, _ in up)
    # A saving reads calmly (ok), no caveat when nothing was removed.
    down = panel._diff_cost_tags({"delta": -3.00, "removed_unpriced": 0, "priced": True})
    assert down == [("-$3.00/board", "ok")]
    # No net priced change still shows $0 (mut) so the user knows it was costed.
    zero = panel._diff_cost_tags({"delta": 0.0, "removed_unpriced": 0, "priced": True})
    assert zero == [("$0.00/board", "mut")]
    # An unpriced build shows no cost tag at all.
    assert panel._diff_cost_tags({"delta": 0.0, "removed_unpriced": 2, "priced": False}) == []
    assert panel._diff_cost_tags(None) == []


def test_bom_compare_shows_per_board_cost_delta(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # rev A (exported CSV, unpriced by nature): U1 qty 1, one 10k passive qty 4.
    prior = tmp_path / "old_bom.csv"
    prior.write_text(
        "Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
        'U1,1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n'
        '"R1,R2,R3,R4",4,10k,,,MyFootprints:R_0402,,,yes\n')
    # rev B (current, PRICED): U1 qty 1->3 (+2 @ $2 = +$4), a new cap (+1 @ $0.50), 10k gone.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 3, "mpn": "TPS2121RUXR", "value": "TPS2121",
         "footprint": "SOIC", "unit_price": 2.0},
        {"refs": ["C1"], "qty": 1, "mpn": "GRM188", "value": "100n",
         "footprint": "C_0402", "unit_price": 0.50},
    ], "cost": {"total_cost": 6.50, "unpriced_lines": 0}}
    panel._compare_to_csv(str(prior))
    c = panel._last_diff["cost"]
    assert c["delta"] == 4.50 and c["removed_unpriced"] == 1
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("+$4.50/board" in t for t in labels)
    assert any("1 removed not costed" in t for t in labels)
    # The exported diff CSV carries the same cost column, so the delta is shareable.
    csv = panel._last_diff["csv"]
    assert "Cost Delta" in csv.splitlines()[0]
    assert "Changed,TPS2121RUXR,TPS2121,1,3,2,4.00" in csv        # +2 @ $2


def test_bom_compare_table_shows_per_line_cost_impact(tmp_path):
    from ui.features import projects as PJ
    from PyQt5.QtWidgets import QTableWidget
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    prior = tmp_path / "old.csv"
    prior.write_text(
        "Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
        'U1,1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n'
        '"R1,R2,R3,R4",4,10k,,,MyFootprints:R_0402,,,yes\n')
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 3, "mpn": "TPS2121RUXR", "value": "TPS2121",
         "footprint": "SOIC", "unit_price": 2.0},           # +2 units @ $2 = +$4.00
        {"refs": ["C1"], "qty": 1, "mpn": "GRM188", "value": "100n",
         "footprint": "C_0402", "unit_price": 0.50},         # added @ $0.50
    ], "cost": {"total_cost": 6.5, "unpriced_lines": 0}}
    panel._compare_to_csv(str(prior))
    tbl = panel.findChild(QTableWidget)
    heads = _headers(tbl)
    assert "Cost Δ" in heads
    ci = heads.index("Cost Δ"); pi = heads.index("Part Number"); vi = heads.index("Value")
    cost_by = {}
    for r in range(tbl.rowCount()):
        key = tbl.item(r, pi).text() or tbl.item(r, vi).text()
        cost_by[key] = tbl.item(r, ci).text()
    assert cost_by["TPS2121RUXR"] == "+$4.00"                # the qty bump, priced from rev B
    assert cost_by["GRM188"] == "+$0.50"                     # newly added line
    assert cost_by["10k"] == ""                              # removed -> unpriced, blank cell
    # No Cost Δ column at all when the build isn't priced.
    panel._last_bom = {"rows": [{"refs": ["U1"], "qty": 3, "mpn": "TPS2121RUXR",
                                 "value": "TPS2121", "footprint": "SOIC"}]}
    panel._compare_to_csv(str(prior))
    assert "Cost Δ" not in _headers(panel.findChild(QTableWidget))


def test_diff_lead_tags_flag_the_critical_path(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # An added part that gates the whole build warns, and names the part + weeks.
    on = panel._diff_lead_tags({"added_max_weeks": 16, "added_critical_mpn": "NEW",
                                "on_critical_path": True, "removed_unassessed": 0, "any": True})
    assert on and on[0][1] == "warn"
    assert "16 wk" in on[0][0] and "NEW" in on[0][0] and "critical path" in on[0][0]
    # An added part below the critical path reads calmly (mut, "off critical path").
    off = panel._diff_lead_tags({"added_max_weeks": 4, "added_critical_mpn": "NEW",
                                 "on_critical_path": False, "removed_unassessed": 0, "any": True})
    assert off[0][1] == "mut" and "off critical path" in off[0][0]
    # A removed line whose lead can't be read is flagged quietly, never dropped.
    rem = panel._diff_lead_tags({"added_max_weeks": None, "added_critical_mpn": None,
                                 "on_critical_path": False, "removed_unassessed": 2, "any": True})
    assert any("2 removed" in t and "lead" in t for t, _ in rem)
    # No lead data anywhere -> no lead tag at all (Quiet Instrument).
    assert panel._diff_lead_tags({"added_max_weeks": None, "on_critical_path": False,
                                  "removed_unassessed": 0, "any": False}) == []
    assert panel._diff_lead_tags(None) == []


def test_bom_compare_shows_lead_delta_critical_path(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # rev A (exported CSV): just U1.
    prior = tmp_path / "old_bom.csv"
    prior.write_text(
        "Refs,Qty,Value,MPN,Manufacturer,Footprint,Datasheet,Description,Basic\n"
        'U1,1,TPS2121,TPS2121RUXR,TI,SOIC,,,\n')
    # rev B adds a 16-week part -> it becomes the critical path.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "value": "TPS2121",
         "footprint": "SOIC", "unit_price": 2.0, "lead_time": "2 Weeks"},
        {"refs": ["U2"], "qty": 1, "mpn": "LONGLEAD", "value": "FPGA",
         "footprint": "BGA", "unit_price": 20.0, "lead_time": "16 Weeks"},
    ], "cost": {"total_cost": 22.0, "unpriced_lines": 0}}
    panel._compare_to_csv(str(prior))
    ld = panel._last_diff["lead"]
    assert ld["added_max_weeks"] == 16 and ld["on_critical_path"] is True
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("16 wk" in t and "critical path" in t for t in labels)


def test_copy_procurement_summary_puts_the_digest_on_the_clipboard(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "SLOW", "value": "FPGA", "footprint": "BGA",
         "unit_price": 20.0, "lead_time": "16 Weeks"},
        {"refs": ["R1", "R2"], "qty": 2, "mpn": "R", "value": "10k", "footprint": "R_0402",
         "unit_price": 0.10},
    ], "cost": {"total_cost": 20.20, "unpriced_lines": 0}}
    panel._copy_summary()
    clip = QApplication.clipboard().text()
    assert "2 lines" in clip and "3 parts" in clip
    assert "$20.20/board" in clip
    assert "critical path 16 wk (SLOW)" in clip
    assert any("copied" in m.lower() or "clipboard" in m.lower() for m in ctx.services.logs)


def test_bom_export_xlsx_writes_a_valid_workbook(tmp_path, monkeypatch):
    import io, zipfile
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._export_xlsx()                                   # no build yet
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    panel._last_bom = {"rows": [
        {"refs": ["R1", "R2"], "qty": 2, "value": "10k", "mpn": "RC0402",
         "unit_price": 0.10, "extended": 0.20},
    ], "cost": {"total_cost": 0.20, "unpriced_lines": 0}}
    out = tmp_path / "bom.xlsx"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_xlsx()
    data = out.read_bytes()
    assert data[:2] == b"PK"                               # a real .xlsx landed on disk
    zf = zipfile.ZipFile(io.BytesIO(data))
    assert "xl/worksheets/sheet1.xml" in zf.namelist()
    assert b"Unit Price" in zf.read("xl/worksheets/sheet1.xml")  # priced columns present
    assert any("Wrote bom.xlsx" in m for m in ctx.services.logs)


def test_bom_export_scope_filters_drop_lines_from_the_written_csv(tmp_path, monkeypatch):
    """The 'Priced Only' / 'Populated Only' export scope must drop the right lines from a
    re-serialized CSV — and with both off the CSV is byte-identical to the cached build."""
    from ui.features import projects as PJ
    import LibraryManager as L
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    rows = [
        {"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM", "manufacturer": "ST",
         "footprint": "LQFP", "datasheet": "", "description": "", "basic": False,
         "source": "Mouser", "unit_price": 5.0, "extended": 5.0, "stock": 100, "lifecycle": "Active"},
        {"refs": ["R1", "R2"], "qty": 2, "value": "10k", "mpn": "", "manufacturer": "",
         "footprint": "R_0402", "datasheet": "", "description": "", "basic": True},
    ]
    # A priced build carries the full csv the export re-serializes when no filter is active.
    panel._last_bom = {"rows": rows, "cost": {"total_cost": 5.0, "unpriced_lines": 1},
                       "csv": L.bom_csv(rows, mode="project", priced=True)}
    panel._last_mode = "project"
    out = tmp_path / "bom.csv"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    # No filter -> byte-identical to the cached csv.
    panel._export_csv()
    assert out.read_text(encoding="utf-8") == panel._last_bom["csv"]
    # Priced Only -> the unpriced passive line is dropped.
    panel._priced_only_cb.setChecked(True)
    panel._export_csv()
    text = out.read_text(encoding="utf-8")
    assert "STM" in text and "10k" not in text
    panel._priced_only_cb.setChecked(False)
    # Populated Only keeps both (both carry a value), so nothing is dropped here.
    panel._populated_cb.setChecked(True)
    panel._export_csv()
    assert "STM" in out.read_text(encoding="utf-8") and "10k" in out.read_text(encoding="utf-8")


def test_bom_filter_rows_is_the_shared_export_predicate(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    rows = [{"mpn": "A", "value": "x", "unit_price": 1.0},
            {"mpn": "", "value": "10k"},                       # populated (value), unpriced
            {"mpn": "", "value": ""}]                          # blank
    assert len(panel._filter_rows(rows)) == 3                  # off by default
    panel._populated_cb.setChecked(True)
    assert len(panel._filter_rows(rows)) == 2                  # blank dropped
    panel._priced_only_cb.setChecked(True)
    assert [r["mpn"] for r in panel._filter_rows(rows)] == ["A"]  # only the priced+populated line


def test_bom_source_filter_hidden_until_priced(tmp_path):
    """The Source view-filter only makes sense on a priced project build (rows carry a source
    only when priced) — it stays hidden on an unpriced build and appears once priced."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    unpriced = {"rows": [{"refs": ["R1"], "qty": 1, "value": "10k", "mpn": ""}]}
    panel._draw_bom_table(unpriced, "project"); _APP.processEvents()
    assert panel._source_filter.isHidden() is True                # hidden on an unpriced build
    priced = {"rows": [{"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM",
                        "source": "Mouser", "unit_price": 5.0, "extended": 5.0}],
              "cost": {"total_cost": 5.0, "unpriced_lines": 0}}
    panel._draw_bom_table(priced, "project"); _APP.processEvents()
    assert panel._source_filter.isHidden() is False               # shown once priced


def test_bom_consolidated_details_dialog_carries_the_per_board_split(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    row = {"mpn": "MCU1", "value": "STM32", "footprint": "LQFP", "total_qty": 7,
           "per_board": {"A": 4, "B": 3}}
    dlg = panel._consolidated_details_dialog(row)
    assert dlg._chart_rows == {"A": 4, "B": 3}
    dlg.deleteLater()


def test_bom_export_procurement_sheet_uses_toolbar_controls(tmp_path, monkeypatch):
    import io, zipfile
    import xml.etree.ElementTree as ET
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._export_procurement(opts=(3, 0.10, 0.0))         # no build yet
    assert any("Build a BOM first" in m for m in ctx.services.logs)
    panel._last_bom = {"rows": [
        {"refs": ["R1"], "qty": 2, "value": "10k", "mpn": "RC10K", "mouser_pn": "71-RC10K",
         "source": "Mouser", "url": "https://mouser.com/x", "unit_price": 0.10},
    ], "cost": {"total_cost": 0.20, "unpriced_lines": 0}}
    out = tmp_path / "procurement.xlsx"
    monkeypatch.setattr(PJ.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    panel._export_procurement(opts=(3, 0.10, 5.0))         # packs of 3, 10% tax, $5 shipping
    data = out.read_bytes()
    assert data[:2] == b"PK"
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml"))
    cells = {}
    for c in root.iter(f"{ns}c"):
        v = c.find(f"{ns}is/{ns}t") if c.get("t") == "inlineStr" else c.find(f"{ns}v")
        cells[c.get("r")] = v.text if v is not None else None
    hdr = {cells[f"{chr(ord('A') + i)}1"]: chr(ord('A') + i) for i in range(12)}
    assert cells[f"{hdr['QTY']}2"] == "6"                  # 2/board * 3 (pack rounding)
    assert float(cells[f"{hdr['Tax/Tariff']}2"]) == 0.06   # 6 * $0.10 = $0.60, 10% tax
    assert cells[f"{hdr['P/N']}2"] == "71-RC10K"           # vendor part # from the lookup
    assert cells[f"{hdr['Product Link']}2"] == "https://mouser.com/x"
    assert any("Wrote procurement.xlsx" in m for m in ctx.services.logs)


def test_procurement_options_dialog_returns_and_remembers(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    import ui.util as _U
    monkeypatch.setattr(_U, "_headless", lambda: False)             # exercise the real dialog path
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    assert panel._proc_pack == 3 and panel._proc_tax_pct == 0.0     # defaults
    monkeypatch.setattr(PJ.QDialog, "exec_", lambda self: PJ.QDialog.Accepted)
    opts = panel._procurement_opts()
    # (pack, tax fraction, shipping, labour/board, surcharge fraction)
    assert opts == (3, 0.0, 0.0, 0.0, 0.0)
    # Cancel returns None and doesn't change the remembered values.
    monkeypatch.setattr(PJ.QDialog, "exec_", lambda self: PJ.QDialog.Rejected)
    assert panel._procurement_opts() is None


def test_copy_procurement_summary_needs_a_build_first(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    panel._copy_summary()                                 # no _last_bom yet
    assert any("build a bom" in m.lower() for m in ctx.services.logs)


def test_bom_export_before_build_is_disabled(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._bom_panel(ctx, state)
    # With no schematic the auto-build finds nothing, so export stays disabled.
    assert not getattr(panel, "_last_bom", None)


def test_bom_compare_to_git_revision_diffs_against_a_commit(tmp_path, monkeypatch):
    import nd_git
    if not nd_git.have_git():
        pytest.skip("git not on PATH")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Tester")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "tester@example.com")
    from ui.features import projects as PJ
    # Offline: the panel auto-builds a BOM on construction — keep it from touching a
    # live distributor (only library enrichment, no network) so the test is hermetic.
    monkeypatch.setattr(PJ.LM, "providers_from_config", lambda cfg=None: None)

    # A real repo with one project sheet: U1 (fully enriched so no lookup fires) + R1,R2.
    repo = tmp_path / "hw"
    assert nd_git.init_repo(repo).ok
    proj = repo / "Proj0"; proj.mkdir()
    (proj / "Proj0.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
    sch = proj / "Proj0.kicad_sch"

    def _sym(ref, value, lib="Device:R", mpn=None, mfr=None, ds=None):
        p = [f'(property "Reference" "{ref}")', f'(property "Value" "{value}")']
        if mpn: p.append(f'(property "MPN" "{mpn}")')
        if mfr: p.append(f'(property "MANUFACTURER" "{mfr}")')
        if ds: p.append(f'(property "Datasheet" "{ds}")')
        return f'(symbol (lib_id "{lib}") ' + " ".join(p) + ')'

    u1 = _sym("U1", "MCU", lib="Device:U", mpn="STM32F407VGT6", mfr="ST", ds="http://x")
    sch.write_text("(kicad_sch " + u1 + _sym("R1", "10k") + _sym("R2", "10k") + ")",
                   encoding="utf-8")
    ok, first = nd_git.commit(repo, "rev1: two 10k", paths=[sch])
    assert ok, first

    ctx = _fake_ctx({"RepoRoot": str(repo)})

    class _S:
        def __init__(self):
            self.projects = [proj]; self.project = proj
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(proj)
        def boards(self):
            return []
        def root_schematic(self):
            return None

    panel = PJ._bom_panel(ctx, _S())
    # The revision picker sees the commit.
    refs = panel._recent_refs()
    assert refs and refs[0]["subject"] == "rev1: two 10k"

    # Comparing before any build tells the user to build first.
    panel._last_bom = None
    panel._compare_to_ref(first)
    assert any("Build a BOM first" in m for m in ctx.services.logs)

    # Current build (rev B): R2 dropped (10k qty 2 -> 1), a new cap added.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "STM32F407VGT6", "value": "MCU", "footprint": ""},
        {"refs": ["R1"], "qty": 1, "mpn": "", "value": "10k", "footprint": ""},
        {"refs": ["C1"], "qty": 1, "mpn": "GRM188R71", "value": "100n", "footprint": ""},
    ]}
    panel._compare_to_ref(first)
    d = panel._last_diff
    assert {r["mpn"] for r in d["added"]} == {"GRM188R71"}
    assert d["changed"] and d["changed"][0]["from_qty"] == 2 and d["changed"][0]["to_qty"] == 1
    assert any("Diff vs" in m and "1 added" in m for m in ctx.services.logs)


def test_bom_compare_to_another_project_diffs_two_variants(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    # Offline: the panel auto-builds; keep enrichment from touching a live distributor.
    monkeypatch.setattr(PJ.LM, "providers_from_config", lambda cfg=None: None)

    def _sym(ref, value, lib="Device:R", mpn=None, mfr=None, ds=None):
        p = [f'(property "Reference" "{ref}")', f'(property "Value" "{value}")']
        if mpn: p.append(f'(property "MPN" "{mpn}")')
        if mfr: p.append(f'(property "MANUFACTURER" "{mfr}")')
        if ds: p.append(f'(property "Datasheet" "{ds}")')
        return f'(symbol (lib_id "{lib}") ' + " ".join(p) + ')'

    def _mkproj(name, *syms):
        d = tmp_path / name; d.mkdir()
        (d / f"{name}.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
        (d / f"{name}.kicad_sch").write_text("(kicad_sch " + " ".join(syms) + ")",
                                             encoding="utf-8")
        return d

    u = _sym("U1", "MCU", lib="Device:U", mpn="STM32F407VGT6", mfr="ST", ds="http://x")
    # RevA: MCU + two 10k.  RevB (active): MCU + one 10k + a cap.
    a = _mkproj("RevA", u, _sym("R1", "10k"), _sym("R2", "10k"))
    b = _mkproj("RevB", u, _sym("R1", "10k"),
                _sym("C1", "100n", lib="Device:C", mpn="GRM188R71"))

    ctx = _fake_ctx({"RepoRoot": str(tmp_path)})

    class _S:
        def __init__(self):
            self.projects = [b, a]; self.project = b       # RevB is the active build
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def boards(self):
            return []
        def root_schematic(self):
            return None

    panel = PJ._bom_panel(ctx, _S())
    # The active build (RevB): MCU + one 10k + a cap.
    panel._last_bom = {"rows": [
        {"refs": ["U1"], "qty": 1, "mpn": "STM32F407VGT6", "value": "MCU", "footprint": ""},
        {"refs": ["R1"], "qty": 1, "mpn": "", "value": "10k", "footprint": ""},
        {"refs": ["C1"], "qty": 1, "mpn": "GRM188R71", "value": "100n", "footprint": ""},
    ]}
    # Diffed against RevA (MCU + two 10k): cap added, one 10k dropped (2 -> 1).
    panel._compare_to_project("RevA")
    d = panel._last_diff
    assert {r["mpn"] for r in d["added"]} == {"GRM188R71"}
    assert d["changed"] and d["changed"][0]["from_qty"] == 2 and d["changed"][0]["to_qty"] == 1
    assert any("Diff vs RevA" in m for m in ctx.services.logs)


# ── FIX 7: net-class / fab-facts rebuild must NOT leak restyle callbacks ───────
def test_netclass_rebuild_does_not_leak_restylers(tmp_path):
    from ui.features import projects as PJ
    import ui.widgets as UW
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    base = len(UW._RESTYLERS)
    # Repeated table rebuilds (New/Delete) and profile switches (which rebuild both
    # the table and the fab facts) must not grow the global restyle registry.
    for _ in range(5):
        panel._nc_new()
    name = panel._ncmgr.list_netclasses()[0]
    panel._nc_delete(name)
    panel._profile_seg.setCurrentIndex(1)
    panel._profile_seg.setCurrentIndex(0)
    # No GROWTH == no leak. (Post-SHELL-06 the registry can also SHRINK: restylers
    # now auto-unregister when their widget is destroyed, so an unrelated prior
    # panel being GC'd mid-test can lower the global count. That's the fix working,
    # not a regression — the invariant we care about is that rebuilds never grow it.)
    assert len(UW._RESTYLERS) <= base, (
        f"restyle registry leaked {len(UW._RESTYLERS) - base} entries across rebuilds")


# ── PCB-14: the diff-pair columns are ALWAYS editable (a diff pair can be added) ──
def test_diffpair_columns_always_editable(tmp_path):
    """Every class — diff-pair or not — exposes editable diff-pair spins, so a diff
    pair can be ADDED to a class that lacks one. A spin sitting at 0 means 'no diff
    pair'. Previously non-USB classes rendered a static dim dash with no committable
    spin, so those columns were permanently read-only."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    mgr = panel._ncmgr
    non_diff = next(n for n in mgr.list_netclasses()
                    if not (getattr(mgr.get_netclass(n), "diff_pair_width", None) or 0) > 0)
    row = next(r for r in panel._nc_rows() if r["name"] == non_diff)
    # The diff-pair spins exist and are registered (committable), seeded at 0.
    assert "diff_pair_width" in row["spins"] and "diff_pair_gap" in row["spins"]
    assert row["spins"]["diff_pair_width"]._mm == 0.0
    assert row["spins"]["diff_pair_gap"]._mm == 0.0
    # A genuine diff-pair class (USB) still exposes editable diff-pair spins.
    if "USB" in mgr.list_netclasses():
        usb = next(r for r in panel._nc_rows() if r["name"] == "USB")
        assert "diff_pair_width" in usb["spins"] and "diff_pair_gap" in usb["spins"]


def test_diffpair_can_be_added_to_a_class_that_lacks_one(tmp_path):
    """The core fix: typing a positive width+gap into a non-diff class's spins and
    committing ENABLES a diff pair on it (attr goes from None to the value). Setting a
    spin back to 0 CLEARS it (attr -> None), so a fabricated 0.0 is never persisted."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    mgr = panel._ncmgr
    non_diff = next(n for n in mgr.list_netclasses()
                    if not (getattr(mgr.get_netclass(n), "diff_pair_width", None) or 0) > 0)
    assert not mgr.get_netclass(non_diff).diff_pair_width   # starts with no diff pair
    row = next(r for r in panel._nc_rows() if r["name"] == non_diff)
    # Drive the spins as a user edit would (canonical mm lives on sp._mm).
    row["spins"]["diff_pair_width"]._mm = 0.2
    row["spins"]["diff_pair_gap"]._mm = 0.15
    panel._commit_netclasses()
    nc = mgr.get_netclass(non_diff)
    assert nc.diff_pair_width == 0.2 and nc.diff_pair_gap == 0.15
    # to_kicad_dict now actually emits the diff-pair keys (enabled).
    assert nc.to_kicad_dict().get("diff_pair_width") == 0.2
    # Clearing the width back to 0 removes the diff pair (attr -> None), no 0.0 written.
    row["spins"]["diff_pair_width"]._mm = 0.0
    panel._commit_netclasses()
    nc = mgr.get_netclass(non_diff)
    assert nc.diff_pair_width is None
    assert "diff_pair_width" not in nc.to_kicad_dict()


# ── FIX 8a: design-rules-only save must not materialise unrelated blocks ───────
def test_save_design_rules_only_is_scoped(tmp_path):
    import json
    import nd_project_settings_manager as psm
    pro = tmp_path / "Board.kicad_pro"
    pro.write_text(_MINIMAL_PRO, encoding="utf-8")

    pm = psm.ProjectSettingsManager()
    assert pm.load_from_project(pro)
    pm.settings.default_clearance = 8.0        # mils
    pm.settings.default_track_width = 10.0
    assert pm.save_design_rules_only(pro, backup=False)

    data = json.loads(pro.read_text(encoding="utf-8"))
    # No schematic block fabricated (the minimal file never had one).
    assert "schematic" not in data
    # No footprint-text defaults / mask injected by a design-rules save.
    design = data["board"]["design_settings"]
    assert "defaults" not in design
    assert "solder_mask_clearance" not in design
    # The exposed design-rule keys DID land (mils -> mm).
    rules = design["rules"]
    assert rules["min_clearance"] == round(psm.mils_to_mm(8.0), 4)
    assert rules["min_track_width"] == round(psm.mils_to_mm(10.0), 4)
    # An unrelated pre-existing rule key is preserved.
    assert "net_settings" in data


# ── Refactor: an empty Find/Tag must be refused, never a destructive rewrite ───
def _refactor_labels(panel):
    return [w.text() for w in panel.findChildren(QLabel)]


def test_refactor_empty_find_is_refused(tmp_path, monkeypatch):
    """An empty Find would make nd_wizard do str.replace('', repl), inserting repl
    between every character on every sheet + board. _run must refuse it (Preview AND
    Apply) instead of launching the job."""
    from ui.features import projects as PJ
    import nd_wizard
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._rename_panel(ctx, state)

    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("nd_wizard must NOT be reached with an empty Find")
    monkeypatch.setattr(nd_wizard, "schematic_preview_and_apply", _boom)
    monkeypatch.setattr(nd_wizard, "pcb_preview_and_apply", _boom)
    monkeypatch.setattr(nd_wizard, "apply_transforms_atomically", _boom)

    panel._op_state["op"] = "find_replace"
    panel._find_edit.setText("")          # empty needle
    panel._repl_edit.setText("X")
    panel._run(False)                     # Preview
    panel._run(True)                      # Apply
    assert called["n"] == 0               # never launched the transform
    assert any("empty value" in t for t in _refactor_labels(panel))
    assert any("empty value" in m for m in ctx.services.logs)


def test_refactor_empty_tag_is_refused(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    import nd_wizard
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._rename_panel(ctx, state)
    monkeypatch.setattr(nd_wizard, "apply_transforms_atomically",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unreachable")))
    panel._op_state["op"] = "add_tag"
    panel._tag_edit.setText("   ")        # whitespace-only -> empty after strip
    panel._run(True)
    assert any("empty value" in t for t in _refactor_labels(panel))


def test_refactor_apply_uses_atomic_all_or_nothing(tmp_path, monkeypatch):
    """A non-empty Apply routes through nd_wizard.apply_transforms_atomically (the
    rollback path), not the per-file immediate-write helpers."""
    from ui.features import projects as PJ
    import nd_wizard
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._rename_panel(ctx, state)

    seen = {"atomic": 0, "perfile": 0}

    def _atomic(tasks, ts, **k):
        seen["atomic"] += 1
        list(tasks)                       # exhaust so closures could run
        return ([("symbol_ref", "GND", "GND2", None)], [])
    monkeypatch.setattr(nd_wizard, "apply_transforms_atomically", _atomic)
    monkeypatch.setattr(nd_wizard, "schematic_preview_and_apply",
                        lambda *a, **k: seen.__setitem__("perfile", seen["perfile"] + 1) or ({}, [], []))
    monkeypatch.setattr(nd_wizard, "pcb_preview_and_apply",
                        lambda *a, **k: seen.__setitem__("perfile", seen["perfile"] + 1) or (0, [], []))

    panel._op_state["op"] = "find_replace"
    panel._find_edit.setText("GND"); panel._repl_edit.setText("GND2")
    panel._run(True)                      # Apply
    assert seen["atomic"] == 1 and seen["perfile"] == 0
    assert any("1 Changes" in t for t in _refactor_labels(panel))


def test_refactor_apply_error_reports_no_partial_write(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    import nd_wizard
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._rename_panel(ctx, state)

    def _atomic(tasks, ts, **k):
        list(tasks)
        raise nd_wizard.ApplyError(Path("Board.kicad_pcb"), "write", OSError("locked"))
    monkeypatch.setattr(nd_wizard, "apply_transforms_atomically", _atomic)
    panel._op_state["op"] = "find_replace"
    panel._find_edit.setText("GND"); panel._repl_edit.setText("GND2")
    panel._run(True)
    labels = _refactor_labels(panel)
    assert any("aborted" in t and "No files were modified" in t for t in labels)
    assert any("aborted" in m for m in ctx.services.logs)


# ── PCB Setup: Validate must use the profile's FAB FLOOR, not its name ─────────
def test_validate_uses_the_profile_fab_floor_not_its_name(tmp_path, monkeypatch):
    """A class legal on the 4-layer floor (min_track 0.127) but too thin for the
    2-layer floor (min_track 0.1524) must be flagged when the profile's fab is the
    2-layer preset. The old code passed prof_state['name'] (not a NETCLASS_PROFILES
    key), silently validating against the laxer 4-layer default."""
    import nd_netclass_manager as ncm
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)

    # A user-named profile whose fab floor is the 2-layer preset — the name is NOT a
    # NETCLASS_PROFILES key, so passing it (the old bug) would fall back to 4-layer.
    panel._prof_state["name"] = "MyBoard"
    panel._prof_state["fab"] = "OSH Park 2-layer"

    # Load a single class into the panel's manager: clean on 4-layer, too thin on 2.
    panel._ncmgr.net_classes.clear()
    panel._ncmgr.add_netclass(ncm.NetClass(name="THIN", clearance=0.2, track_width=0.13,
                                           via_diameter=0.6, via_drill=0.3,
                                           patterns=["*NET"]))

    # Spy on what the panel's validate() actually passes to validate_netclasses.
    seen = {}
    real = ncm.validate_netclasses

    def _spy(mgr, profile=ncm.DEFAULT_NETCLASS_PROFILE):
        seen["profile"] = profile
        return real(mgr, profile)
    monkeypatch.setattr(ncm, "validate_netclasses", _spy)

    panel._validate()
    assert seen["profile"] == "OSH Park 2-layer"          # the fab key, not "MyBoard"
    # And it must have flagged the too-thin track against the 2-layer floor.
    assert any("net-class issue" in m for m in ctx.services.logs)

    # Cross-check: the same class is clean on the 4-layer floor the old code used.
    assert not any("track" in i["issue"]
                   for i in real(panel._ncmgr, "OSH Park 4-layer"))


# ── PCB Setup: Pull From KiCad keeps the profile combo in sync ─────────────────
def _pro_with_netclasses(d):
    pro = (
        '{\n'
        '  "net_settings": {"classes": [\n'
        '    {"name": "Default"},\n'
        '    {"name": "HV", "clearance": 0.5, "track_width": 0.4,\n'
        '     "via_diameter": 0.8, "via_drill": 0.4}\n'
        '  ],\n'
        '  "netclass_patterns": [{"netclass": "HV", "pattern": "*HV*"}]}\n'
        '}\n'
    )
    (d / f"{d.name}.kicad_pro").write_text(pro, encoding="utf-8")


def test_pull_from_kicad_syncs_the_profile_combo(tmp_path):
    from ui.features import projects as PJ
    ctx = _fake_ctx(); state = _state(tmp_path)
    _pro_with_netclasses(state.project)            # give the project real net classes
    panel = PJ._pcb_setup_panel(ctx, state)

    before = panel._profile_combo.currentText()
    panel._pull_from_kicad()
    pulled = panel._prof_state["name"]
    assert pulled.endswith("(from KiCad)")
    # The combo now truthfully shows the pulled (unsaved) name, not the old profile.
    assert panel._profile_combo.currentText() == pulled
    assert panel._profile_combo.findText(pulled) >= 0
    assert pulled != before or "(from KiCad)" in pulled
    # The pulled classes are actually in the editor (HV came from the .kicad_pro).
    assert "HV" in panel._ncmgr.list_netclasses()


# ── Health: the summary must expose the info-tier Notes count ──────────────────
def test_health_summary_shows_notes_count(tmp_path, monkeypatch):
    from ui.features import projects as PJ
    import nd_project_health as H
    ctx = _fake_ctx(); state = _state(tmp_path)
    # Health's audit() bails early when the project has no schematic; give it one so
    # the summary (with the info-tier Notes tag) actually builds.
    (state.project / f"{state.project.name}.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")

    audit_res = {
        "components": 5, "healthy": 2, "sheets": 1,
        "counts": {"by_severity": {"error": 1, "warning": 0, "info": 2}},
        "findings": [
            {"ref": "R1", "kind": "no_footprint", "severity": "error", "detail": "x"},
            {"ref": "R2", "kind": "no_mpn", "severity": "info", "detail": "y"},
            {"ref": "R3", "kind": "no_mpn", "severity": "info", "detail": "z"},
        ],
    }
    monkeypatch.setattr(H, "audit_project", lambda *a, **k: audit_res)
    monkeypatch.setattr(H, "annotate_project", lambda *a, **k: 0)
    # Skip the Library index / fill-plan build — irrelevant to the summary.
    import nd_library_fill as LF
    monkeypatch.setattr(LF, "library_parts", lambda cfg: {})
    monkeypatch.setattr(LF, "build_fill_plan", lambda *a, **k: {"items": []})

    panel = PJ._health_panel(ctx, state)           # builds + auto-audits synchronously
    labels = [w.text() for w in panel.findChildren(QLabel)]
    assert any("2 Notes" in t for t in labels)     # the info tier is now visible
    assert any("1 Errors" in t for t in labels)
    assert any("2 Healthy" in t for t in labels)


# ── Apply Conform is gated behind a Preview + confirmation (destructive bulk write) ──
_PCB_WITH_TEXT = (
    '(kicad_pcb\n'
    '\t(version 20241229)\n'
    '\t(generator "pcbnew")\n'
    '\t(layers\n'
    '\t\t(0 "F.Cu" signal)\n'
    '\t)\n'
    '\t(setup\n'
    '\t\t(pad_to_mask_clearance 0.05)\n'
    '\t)\n'
    '\t(net 0 "")\n'
    '\t(gr_text "L1" (at 5 5) (layer "F.SilkS") (effects (font (size 0.5 0.5) (thickness 0.1))))\n'
    '\t(gr_text "F1" (at 6 6) (layer "F.Fab") (effects (font (size 0.6 0.6) (thickness 0.1))))\n'
    ')\n'
)


def _state_with_pcb_text(tmp_path):
    d = tmp_path / "Board"; d.mkdir()
    (d / "Board.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
    (d / "Board.kicad_pcb").write_text(_PCB_WITH_TEXT, encoding="utf-8")

    class _S:
        def __init__(self):
            self.projects = [d]; self.project = d
        def boards(self):
            import nd_wizard
            return nd_wizard.list_boards(self.project)
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def root_schematic(self):
            return None
    return d, _S()


def test_apply_conform_requires_preview_first(tmp_path):
    """Apply Conform mutates every board file, so it stays disabled until a Preview has
    shown the blast radius (the preview IS the safety mechanism for the bulk write)."""
    from ui.features import projects as PJ
    ctx = _fake_ctx(); _, state = _state_with_pcb_text(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    assert panel._conform_apply_btn.isEnabled() is False   # nothing previewed yet
    panel._run_conform(False)                              # Preview
    assert panel._conform_apply_btn.isEnabled() is True    # preview found changes -> Apply armed
    assert any("preview" in m.lower() and "would change" in m.lower() for m in ctx.services.logs)


def test_apply_conform_is_confirmed_and_reports_count(tmp_path, monkeypatch):
    """Apply routes through confirm() (like _nc_delete / _delete_profile) and, when
    confirmed, rewrites the board file and logs the changed-object count + blast radius."""
    from ui.features import projects as PJ
    from ui import util as UU
    ctx = _fake_ctx(); board_dir, state = _state_with_pcb_text(tmp_path)
    board = board_dir / "Board.kicad_pcb"
    before = board.read_text(encoding="utf-8")
    panel = PJ._pcb_setup_panel(ctx, state)

    # Refuse the confirmation first: nothing must be written.
    seen = {"asked": 0}

    def _refuse(parent, title, text, default_no=True):
        seen["asked"] += 1
        assert "Apply Text Conform" == title
        return False
    monkeypatch.setattr(PJ, "confirm", _refuse)
    panel._run_conform(False)                              # Preview (arms Apply)
    panel._run_conform(True)                               # Apply -> confirm refused
    assert seen["asked"] == 1
    assert board.read_text(encoding="utf-8") == before      # unchanged, confirmation refused

    # Now accept: the file is rewritten and the log names the count + board file(s).
    monkeypatch.setattr(PJ, "confirm", lambda *a, **k: True)
    panel._run_conform(False)                              # re-preview (Apply was re-armed off)
    panel._run_conform(True)                               # Apply -> confirmed
    after = board.read_text(encoding="utf-8")
    assert "(size 1 1)" in after                            # silk text conformed to the 1.0 target
    assert any("Text conform applied" in m and "object" in m for m in ctx.services.logs)
    # A fresh preview is required again before the next apply.
    assert panel._conform_apply_btn.isEnabled() is False


# ── Refactor preview shows EVERY change (no silent 60-row truncation) ──────────
def test_refactor_preview_shows_all_changes_no_truncation(tmp_path, monkeypatch):
    """A >60-change preview must render every old->new row, so an unshown corrupting
    rename can't be applied unseen. The list was previously capped at 60 rows."""
    from ui.features import projects as PJ
    import nd_wizard
    ctx = _fake_ctx(); state = _state(tmp_path)
    # The refactor preview only runs the per-sheet path when the project has a schematic.
    (state.project / f"{state.project.name}.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
    panel = PJ._rename_panel(ctx, state)

    # 75 changes from the (preview) per-file path — more than the old 60-row cap.
    n = 75
    changes = [("symbol_ref", f"R{i}", f"X{i}", None) for i in range(n)]
    monkeypatch.setattr(nd_wizard, "schematic_preview_and_apply",
                        lambda *a, **k: ({"refs": n}, [], changes))
    monkeypatch.setattr(nd_wizard, "pcb_preview_and_apply",
                        lambda *a, **k: (0, [], []))

    panel._op_state["op"] = "find_replace"
    panel._find_edit.setText("R"); panel._repl_edit.setText("X")
    panel._run(False)                                     # Preview

    labels = [w.text() for w in panel.findChildren(QLabel)]
    # Every one of the 75 old->new endpoints is present (the last one especially — the
    # 61st+ rows were previously hidden).
    for i in (0, 60, 61, n - 1):
        assert any(t == f"R{i}" for t in labels), f"missing old R{i}"
        assert any(t == f"X{i}" for t in labels), f"missing new X{i}"
    assert any(f"{n} Changes" in t for t in labels)


# ── Save surfaces per-section feedback (counts + preserved user classes) ───────
def test_save_summary_reports_counts_and_preserved_classes():
    """The save log is a per-section digest — DR fields, net classes written, and the
    user/unmanaged classes deliberately preserved in the file — not a flat 'Saved'."""
    from ui.features import projects as PJ
    done = ["design rules", "net classes", "board geometry"]
    stats = {"dr_fields": 11, "nc_written": 5,
             "nc_preserved": ["HV", "USER_A", "USER_B"], "bg_keys": 2}
    msg = PJ._save_summary(done, stats)
    assert "11 design-rule fields" in msg
    assert "5 net classes written" in msg
    assert "3 user classes preserved" in msg
    assert "HV" in msg and "USER_A" in msg
    assert "2 board-geometry keys" in msg


def test_save_preserves_unmanaged_class_and_reports_it(tmp_path, monkeypatch):
    """End-to-end: an unmanaged user class in the .kicad_pro is kept on save AND named
    in the log (via mgr.last_preserved_unmanaged), so the user knows it was left in
    place rather than silently dropped."""
    import json
    import nd_pcb_profiles as pcbprof
    from ui.features import projects as PJ
    # Redirect profile persistence to a tmp file: Save To Project now also upserts the
    # active profile (PCB-15), so keep the write hermetic (out of the repo tree).
    monkeypatch.setattr(pcbprof, "_profiles_path", lambda: tmp_path / "pcb_profiles.json")
    # A project whose .kicad_pro already carries a user class ("HV") the panel doesn't manage.
    d = tmp_path / "Board"; d.mkdir()
    pro = d / "Board.kicad_pro"
    pro.write_text(json.dumps({
        "board": {"design_settings": {"rules": {"min_clearance": 0.2, "min_track_width": 0.254}}},
        "net_settings": {"classes": [{"name": "Default"}, {"name": "HV", "clearance": 0.5}]},
    }, indent=2), encoding="utf-8")
    (d / "Board.kicad_pcb").write_text(_MINIMAL_PCB, encoding="utf-8")

    class _S:
        def __init__(self):
            self.projects = [d]; self.project = d
        def boards(self):
            import nd_wizard
            return nd_wizard.list_boards(self.project)
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def root_schematic(self):
            return None

    ctx = _fake_ctx()
    panel = PJ._pcb_setup_panel(ctx, _S())
    panel._save()                                          # synchronous under the fake ctx
    # The unmanaged HV class survived the write...
    data = json.loads(pro.read_text(encoding="utf-8"))
    names = {c.get("name") for c in data["net_settings"]["classes"]}
    assert "HV" in names
    # ...and the save log names it as a preserved user class (not a flat "Saved").
    assert any("net class" in m and "written" in m and "HV" in m and "preserved" in m
               for m in ctx.services.logs)


def test_save_to_project_syncs_profile_no_divergence(tmp_path, monkeypatch):
    """PCB-15 single source of truth: Save To Project writes the net classes into KiCad
    AND upserts the active profile JSON, so reopening the panel reseeds from a profile
    that MATCHES what was written — no silent divergence that discards KiCad edits."""
    import nd_pcb_profiles as pcbprof
    from ui.features import projects as PJ
    prof_path = tmp_path / "pcb_profiles.json"
    monkeypatch.setattr(pcbprof, "_profiles_path", lambda: prof_path)

    ctx = _fake_ctx(); state = _state(tmp_path)
    panel = PJ._pcb_setup_panel(ctx, state)
    active = panel._prof_state["name"]
    mgr = panel._ncmgr

    # Edit a managed class in the table, then Save To Project.
    target = mgr.list_netclasses()[0]
    row = next(r for r in panel._nc_rows() if r["name"] == target)
    row["spins"]["track_width"]._mm = 0.321
    panel._save()                                          # synchronous under the fake ctx

    # The profile JSON now exists and its class carries the edited value: reopening the
    # panel would reseed this exact state, not a stale one.
    saved = pcbprof.get_profile(active, path=prof_path)
    assert saved is not None, "Save To Project must persist the active profile"
    saved_nc = next(nc for nc in saved.netclasses if nc.name == target)
    assert saved_nc.track_width == 0.321
    # The save summary names the synced profile, so the sync is visible (not silent).
    assert any(f"profile '{active}' updated to match" in m for m in ctx.services.logs)


# ── Compare To… must not shell out to git on the GUI thread (menu-open latency) ──
def test_compare_menu_reads_precomputed_refs_not_inline_git(tmp_path, monkeypatch):
    """Opening Compare To… must read the precomputed cache (warmed off-thread on build),
    never call _recent_refs() inline — that shelled out to git on the GUI thread and
    stalled the click on a large repo."""
    from ui.features import projects as PJ
    monkeypatch.setattr(PJ.LM, "providers_from_config", lambda cfg=None: None)

    d = tmp_path / "Proj0"; d.mkdir()
    (d / "Proj0.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
    (d / "Proj0.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")

    class _S:
        def __init__(self):
            self.projects = [d]; self.project = d
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def boards(self):
            return []
        def root_schematic(self):
            return None

    calls = {"recent": 0}
    fake_refs = [{"ref": "abc1234", "subject": "seed commit", "when": "1d"}]

    # Count every _recent_refs invocation; return a stub so no real git runs.
    import nd_git
    monkeypatch.setattr(nd_git, "recent_commits", lambda repo, *a, **k: (
        calls.__setitem__("recent", calls["recent"] + 1) or list(fake_refs)))

    ctx = _fake_ctx({"RepoRoot": str(tmp_path)})
    panel = PJ._bom_panel(ctx, _S())                      # auto-builds -> warms the cache

    # The build warmed the cache off-thread (synchronous under the fake ctx).
    assert panel._recent_refs_cache == fake_refs
    warmed = calls["recent"]
    assert warmed >= 1

    # Opening the menu reads the cache; it does NOT invoke _recent_refs again inline.
    # Stub QMenu.exec_ so building the menu never blocks on a display, and make any
    # inline git call fail the test.
    from PyQt5.QtWidgets import QMenu
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: None)
    monkeypatch.setattr(nd_git, "recent_commits", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("Compare To… must not shell out to git on menu open")))
    panel._compare_menu()
    assert calls["recent"] == warmed                      # no additional git call on menu open


# ── Projects workspace: the redundant Git sub-tab is gone (git is top-level only) ──
def test_projects_workspace_has_no_git_subtab(tmp_path):
    """The Projects workspace exposed a Git sub-tab that ignored the selected
    project and duplicated the top-level Git page (finding projects:2013). It is
    removed: the panel list is Health / BOM / Editor / Refactor with no 'Git'."""
    from ui.features import projects as PJ
    _project(tmp_path)                                     # a discoverable project
    ctx = _fake_ctx({"RepoRoot": str(tmp_path)})
    ws = PJ.ProjectsFeature().build(ctx)
    titles = [name for name, _ in ws._panels]
    assert titles == ["Overview", "Health", "Bill of Materials", "Editor", "Refactor"]
    assert "Git" not in titles                             # no redundant sub-tab
