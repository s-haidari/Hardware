"""Subsystem: Projects -> Overview + Health (readiness feedback loop).

Locks the enhancement's correctness-critical behaviours:
  * shared per-project ERC/DRC cache — a Prepare in Health invalidates the very
    dict Overview's readiness verdict reads (cross-panel, same state object),
  * Restore <-> Undo Restore symmetry (exact inverses, no re-run),
  * the before/after audit itemization (per-kind counts + refs fixed) + its
    Markdown export,
  * the missing-field breakdown chips filtering the findings table,
  * the ERC/DRC before->after line in the Prepare report.

Pure logic + headless panels (no real kicad-cli). audit_project is monkeypatched
to controlled findings so the assertions are deterministic.
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402
from ui.features import projects as PJ  # noqa: E402


# ── shared helpers ────────────────────────────────────────────────────────────
def _ctx(cfg=None):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg=cfg or {}, services=_Svc())


def _state(tmp_path, with_sheet=True):
    d = tmp_path / "Proj"; d.mkdir()
    (d / "Proj.kicad_pro").write_text('{"meta":{"version":1}}', encoding="utf-8")
    sch = d / "Proj.kicad_sch"
    if with_sheet:
        sch.write_text("(kicad_sch ORIGINAL)\n", encoding="utf-8")

    class _S:
        def __init__(self):
            self.projects = [d]
            self.project = d
            self._checks = {}
        def boards(self):
            return []
        def schematics(self):
            return [sch] if with_sheet else []
        def root_schematic(self):
            return sch if with_sheet else None
        def _proj_key(self):
            return str(self.project)
        def checks(self):
            return self._checks.setdefault(self._proj_key(), {"erc": None, "drc": None})
        def set_check(self, kind, summary):
            self.checks()[kind] = summary
        def invalidate_checks(self):
            c = self._checks.get(self._proj_key())
            if c is not None:
                c["erc"] = None; c["drc"] = None
    return _S(), sch


# ── 1. pure helpers ───────────────────────────────────────────────────────────
def test_kind_breakdown_folds_both_model_kinds_into_one_bucket():
    findings = [
        {"ref": "R1", "kind": "no_footprint", "severity": "warning"},
        {"ref": "R2", "kind": "no_footprint", "severity": "warning"},
        {"ref": "U1", "kind": "no_3d_model", "severity": "info"},
        {"ref": "U2", "kind": "missing_3d_model", "severity": "info"},
        {"ref": "C1", "kind": "no_mpn", "severity": "info"},
    ]
    buckets = PJ._kind_breakdown(findings)
    labels = {b["label"]: b for b in buckets}
    assert "Missing Model" in labels
    # both raw kinds collapse into the one bucket, counted together
    assert labels["Missing Model"]["count"] == 2
    assert labels["Missing Model"]["kinds"] == {"no_3d_model", "missing_3d_model"}
    assert labels["No Footprint"]["count"] == 2
    # most-common first
    assert buckets[0]["count"] >= buckets[-1]["count"]


def test_audit_diff_names_the_refs_that_were_fixed():
    before = {"counts": {"by_kind": {"no_footprint": 2, "no_mpn": 1}},
              "findings": [{"ref": "R2", "kind": "no_footprint"},
                           {"ref": "R10", "kind": "no_footprint"},
                           {"ref": "C1", "kind": "no_mpn"}]}
    after = {"counts": {"by_kind": {"no_footprint": 1}},
             "findings": [{"ref": "R10", "kind": "no_footprint"}]}
    diff = PJ._audit_diff(before, after)
    rows = {r["kind"]: r for r in diff["rows"]}
    assert rows["no_footprint"]["before"] == 2 and rows["no_footprint"]["after"] == 1
    assert rows["no_footprint"]["delta"] == -1
    assert rows["no_footprint"]["fixed"] == ["R2"]           # R2 gone, R10 remains
    assert rows["no_mpn"]["fixed"] == ["C1"] and rows["no_mpn"]["after"] == 0
    assert diff["before_total"] == 3 and diff["after_total"] == 1


def test_ref_sort_key_is_natural_order():
    got = sorted(["R10", "R2", "C1", "R1"], key=PJ._ref_sort_key)
    assert got == ["C1", "R1", "R2", "R10"]


# ── 2. shared ERC/DRC cache: Health invalidates what Overview reads ────────────
def test_prepare_invalidates_the_shared_check_cache_overview_reads(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    overview = PJ._overview_panel(ctx, state)
    health = PJ._health_panel(ctx, state)
    # Overview and Health must read the SAME per-project cache dict.
    assert overview._checks is state.checks()
    state.set_check("erc", {"errors": 3, "warnings": 0})
    assert overview._checks["erc"] == {"errors": 3, "warnings": 0}

    # Drive a real Prepare write via the apply seam (monkeypatch the writers + audit).
    monkeypatch.setattr(PJ, "_kicad_cli", lambda: None)      # no cli -> pure invalidate-to-None branch
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {"counts": {"by_severity": {}, "by_kind": {}}, "findings": []})
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [str(sch) + ".bak"], "fields_written": 1,
                                                      "components_changed": 1, "errors": []})[1])
    health._prep["plan"] = {"items": []}
    snap = health._snapshot()
    health._prepare_apply(snap, ["R1\x1fMPN"])
    # No kicad-cli in the fixture -> cannot re-run, so the stale ERC is dropped (invalidated).
    assert state.checks()["erc"] is None
    assert overview._checks["erc"] is None                   # same dict object -> Overview sees it


def test_prepare_report_shows_erc_before_after_line(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    health = PJ._health_panel(ctx, state)
    state.set_check("erc", {"errors": 3, "warnings": 1})
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {"counts": {"by_severity": {}, "by_kind": {}}, "findings": []})
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [], "fields_written": 2,
                                                      "components_changed": 1, "errors": []})[1])
    monkeypatch.setattr(PJ, "_kicad_cli", lambda: None)      # no cli -> "re-run to confirm" branch
    health._prep["plan"] = {"items": []}
    report = health._prepare_apply(health._snapshot(), ["R1\x1fMPN"])
    joined = " ".join(report["done"])
    assert "ERC" in joined and "before Prepare" in joined and "Re-run" in joined


# ── 3. Restore <-> Undo Restore symmetry ──────────────────────────────────────
def test_restore_and_undo_restore_are_exact_inverses(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    health = PJ._health_panel(ctx, state)
    original = sch.read_text(encoding="utf-8")
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {"counts": {"by_severity": {}, "by_kind": {}}, "findings": []})
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [str(sch) + ".bak"], "fields_written": 1,
                                                      "components_changed": 1, "errors": []})[1])
    health._prep["plan"] = {"items": []}
    health._prepare_apply(health._snapshot(), ["R1\x1fMPN"])
    prepared = sch.read_text(encoding="utf-8")
    assert prepared == "(kicad_sch FILLED)\n"
    assert health._last_prepare["state"] == "prepared"

    # Restore -> back to the pre-Prepare original, state flips to "restored".
    health._restore_prepare()
    assert sch.read_text(encoding="utf-8") == original
    assert health._last_prepare["state"] == "restored"

    # Undo Restore -> re-applies the prepared text WITHOUT re-running Prepare.
    health._undo_restore()
    assert sch.read_text(encoding="utf-8") == prepared
    assert health._last_prepare["state"] == "prepared"

    # And we can toggle back again (Restore is available once more).
    health._restore_prepare()
    assert sch.read_text(encoding="utf-8") == original
    assert health._last_prepare["state"] == "restored"


def test_second_noop_prepare_preserves_the_round_trip(tmp_path, monkeypatch):
    """Adversarial-review lock: a later Prepare that writes NOTHING must not overwrite the
    reversibility holder with the current (already-prepared) on-disk text, or Restore would
    become a no-op and the true pre-Prepare original would be lost."""
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    monkeypatch.setattr(PJ, "_kicad_cli", lambda: None)
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {"counts": {"by_severity": {}, "by_kind": {}}, "findings": []})
    health = PJ._health_panel(ctx, state)
    original = sch.read_text(encoding="utf-8")
    # Prepare #1 writes FILLED.
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [], "fields_written": 1, "components_changed": 1, "errors": []})[1])
    health._prep["plan"] = {"items": []}
    health._prepare_apply(health._snapshot(), ["R1\x1fMPN"])
    # Prepare #2 writes NOTHING (fields_written 0, no annotate).
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: {"backups": [], "fields_written": 0, "components_changed": 0, "errors": []})
    health._prepare_apply(health._snapshot(), [])
    # The holder still points at the TRUE original, so Restore rolls fully back.
    assert health._last_prepare["originals"][str(sch)] == original
    health._restore_prepare()
    assert sch.read_text(encoding="utf-8") == original


def test_restore_does_not_flip_state_on_partial_write_failure(tmp_path, monkeypatch):
    """Adversarial-review lock: if a sheet cannot be written during Restore, the holder must
    NOT claim "restored" (which would arm Undo over a half-rolled-back disk)."""
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    monkeypatch.setattr(PJ, "_kicad_cli", lambda: None)
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {"counts": {"by_severity": {}, "by_kind": {}}, "findings": []})
    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [], "fields_written": 1, "components_changed": 1, "errors": []})[1])
    health = PJ._health_panel(ctx, state)
    health._prep["plan"] = {"items": []}
    health._prepare_apply(health._snapshot(), ["R1\x1fMPN"])
    assert health._last_prepare["state"] == "prepared"
    # Force every restore write to fail (read-only), so _rewrite_sheets returns errors.
    def _wt(self, *a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(Path, "write_text", _wt)
    health._restore_prepare()
    # A failed restore keeps the holder in "prepared" (not falsely "restored").
    assert health._last_prepare["state"] == "prepared"


def test_restore_undo_guard_against_wrong_state(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    health = PJ._health_panel(ctx, state)
    # Fresh panel: nothing prepared -> Restore + Undo both no-op (and never crash / write).
    before = sch.read_text(encoding="utf-8")
    health._restore_prepare()
    health._undo_restore()
    assert sch.read_text(encoding="utf-8") == before
    assert health._last_prepare["state"] is None
    # Button gating: neither is live before a Prepare.
    assert health._btn_restore is not None and not health._btn_restore.isEnabled()
    assert health._btn_undo is not None and not health._btn_undo.isEnabled()


# ── 4. before/after itemization export + breakdown filter ─────────────────────
def test_prepare_diff_markdown_before_and_after(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    # Content-based (deterministic under memoisation): the pre-write sheet reports two
    # No-Footprint findings; once apply_fill_plan writes "FILLED", only R2 remains. Patched
    # BEFORE building the panel so the build-time verdict audit doesn't cache a real result.
    def _audit_by_content(schs, f, m):
        text = Path(schs[0]).read_text(encoding="utf-8") if schs else ""
        if "FILLED" in text:
            return {"counts": {"by_severity": {}, "by_kind": {"no_footprint": 1}},
                    "findings": [{"ref": "R2", "kind": "no_footprint"}]}
        return {"counts": {"by_severity": {}, "by_kind": {"no_footprint": 2}},
                "findings": [{"ref": "R1", "kind": "no_footprint"}, {"ref": "R2", "kind": "no_footprint"}]}
    monkeypatch.setattr(PJ.phealth, "audit_project", _audit_by_content)
    health = PJ._health_panel(ctx, state)
    snap = health._snapshot()
    # No prepare yet -> honest placeholder.
    md0 = health._prepare_diff_markdown(snap)
    assert "No Prepare has been run" in md0

    monkeypatch.setattr(PJ.libfill, "apply_fill_plan",
                        lambda plan, sel, cfg, log: (sch.write_text("(kicad_sch FILLED)\n", encoding="utf-8"),
                                                     {"backups": [], "fields_written": 1,
                                                      "components_changed": 1, "errors": []})[1])
    health._prep["plan"] = {"items": []}
    health._prepare_apply(snap, ["R1\x1fMPN"])
    md = health._prepare_diff_markdown(snap)
    assert "No Footprint | 2 | 1 | -1" in md          # per-kind before/after/delta
    assert "R1" in md                                  # the ref that was fixed is named


def test_findings_breakdown_chip_filters_the_table(tmp_path, monkeypatch):
    state, sch = _state(tmp_path)
    ctx = _ctx({"RepoRoot": str(tmp_path)})
    monkeypatch.setattr(PJ.phealth, "audit_project", lambda s, f, m: {
        "counts": {"by_severity": {"warning": 2, "info": 1}, "by_kind": {"no_footprint": 2, "no_mpn": 1}},
        "components": 3, "healthy": 0, "sheets": 1,
        "findings": [{"ref": "R1", "kind": "no_footprint", "severity": "warning", "detail": "x"},
                     {"ref": "R2", "kind": "no_footprint", "severity": "warning", "detail": "x"},
                     {"ref": "C1", "kind": "no_mpn", "severity": "info", "detail": "y"}]})
    health = PJ._health_panel(ctx, state)
    health._region.handle.refresh()                    # build + fill the detail
    fstate = health._findings_filter
    assert {b["label"] for b in fstate["buckets"]} == {"No Footprint", "No MPN"}
    # Applying the "No MPN" filter narrows the active bucket; re-applying clears it.
    health._apply_findings_filter("No MPN")
    assert fstate["bucket"] == "No MPN"
    health._apply_findings_filter("No MPN")
    assert fstate["bucket"] is None


# ── 5. FillPreviewDialog triage filter ────────────────────────────────────────
def _fill_plan():
    """A synthetic FillPlan with three component cards: two capacitors + one connector,
    each with one exact blank-fill field so Select All has something to check."""
    def item(ref):
        return {"ref": ref, "sheet": "s.kicad_sch",
                "match": {"confidence": "exact", "lib_part": {"name": ref}},
                "changes": [{"prop": "MPN", "old": "", "new": f"MPN-{ref}", "kind": "fill", "source": "library"}]}
    return {"items": [item("C1"), item("C2"), item("J1")], "summary": {}}


def test_fillpreview_prefix_filter_scopes_select_all(tmp_path):
    comps = [{"ref": r, "value": "x", "footprint": "L:F", "props": {"Reference": r}}
             for r in ("C1", "C2", "J1")]
    sof = {c["ref"]: "s.kicad_sch" for c in comps}
    dlg = PJ.FillPreviewDialog(_fill_plan(), 0, cfg={}, components=comps, sheet_of=sof)
    try:
        # No filter: Select All checks all three fields.
        dlg.select_all()
        assert dlg.selected() == {("C1", "MPN"), ("C2", "MPN"), ("J1", "MPN")}
        dlg._bulk("clear")
        # Prefix "C" then Select All -> only the two capacitors fill; J1 stays out.
        dlg._filter_edit.setText("C")
        dlg.select_all()
        assert dlg.selected() == {("C1", "MPN"), ("C2", "MPN")}
        # A more specific prefix narrows further.
        dlg._bulk("clear")
        dlg._filter_edit.setText("C2")
        dlg.select_all()
        assert dlg.selected() == {("C2", "MPN")}
    finally:
        dlg.deleteLater()


def test_fillpreview_passives_only_toggle(tmp_path):
    comps = [{"ref": r, "value": "x", "footprint": "L:F", "props": {"Reference": r}}
             for r in ("C1", "C2", "J1")]
    sof = {c["ref"]: "s.kicad_sch" for c in comps}
    dlg = PJ.FillPreviewDialog(_fill_plan(), 0, cfg={}, components=comps, sheet_of=sof)
    try:
        dlg._passives_box.setChecked(True)     # R/C/L/FB only -> J1 (connector) excluded
        dlg.select_all()
        assert dlg.selected() == {("C1", "MPN"), ("C2", "MPN")}
    finally:
        dlg.deleteLater()


def test_is_passive_ref_classifies_prefixes():
    assert PJ._is_passive_ref("C12") and PJ._is_passive_ref("R1") and PJ._is_passive_ref("FB3")
    assert not PJ._is_passive_ref("J1") and not PJ._is_passive_ref("U7") and not PJ._is_passive_ref("")
