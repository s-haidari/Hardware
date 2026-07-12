"""Phase 2 (UI convergence) — the Library MAINTENANCE workbench on the kit recipe.

The behaviours the old `_maintenance_panel` proved are re-asserted here through the new
`_maintenance_workbench(ctx)` structure — destructive tools confirm-then-commit, the done
line reports the tool's real outcome, cancel writes nothing, read-only tools never confirm
or commit — plus the NEW recipe surfaces: the waiting-ZIPs verdict band, the ▶ Process
Waiting ZIPs primary flow (batch vs subset), the undo-history card (trash snapshots), the
import/merge seams, and the Manage machinery (library location / KiCad registration /
hand-off portability). Run:  python -m pytest tests/test_library_maintenance.py -q
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _libcfg(tmp_path):
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "R_0402" (property "Value" "R_0402" (id 1)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "R_0402.kicad_mod").write_text('(footprint "R_0402")', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    dl = tmp_path / "downloads"; dl.mkdir()
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path), "Downloads": str(dl)}


def _fake_ctx(cfg):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    class _Bus:
        def __init__(self): self.emitted = []
        def emit(self, name, *a): self.emitted.append((name,) + a)
        def on(self, *_a, **_k): pass
        def on_owned(self, *_a, **_k): pass

    return SimpleNamespace(cfg=cfg, services=_Svc(), theme=None, bus=_Bus())


def _workbench(ctx):
    from ui.features import library as L
    return L._maintenance_workbench(ctx)


def _yes(monkeypatch, counter=None):
    # Confirmations now route through the in-app overlay via ui.util.confirm; patch that
    # (an inline `from ..util import confirm` at each call site picks up the patch).
    from ui import util
    def c(*_a, **_k):
        if counter is not None:
            counter["n"] += 1
        return True
    monkeypatch.setattr(util, "confirm", c)


# ── build + verdict ────────────────────────────────────────────────────────────
def test_maintenance_workbench_builds_and_exposes_handles(tmp_path):
    host = _workbench(_fake_ctx(_libcfg(tmp_path)))
    snap = host._snapshot()
    assert snap["zips"] == [] and snap["snapshots"] == []
    assert callable(host._run_primary)
    for title in ("Dedupe Symbol Library", "Deduplicate Footprints",
                  "Repair Footprint And Model Links", "Auto-Assign Library",
                  "Scan For Corrupt Files…", "Import Vendor ZIP…",
                  "Import Extracted Folder…", "Merge Symbol Files…",
                  "Undo Last Change", "Empty Undo History",
                  "Clean Downloads Leftovers", "Export Catalog"):
        assert host._btn(title) is not None, f"missing action {title!r}"
    for title in ("Change Library Location…", "Set Up KiCad Libraries",
                  "Check Hand-Off Readiness", "Make Portable and Commit"):
        assert host._btn(title) is not None, f"missing machinery {title!r}"
    assert host._verdict.isHidden()                     # no waiting ZIPs → quiet band


def test_verdict_counts_waiting_zips(tmp_path):
    cfg = _libcfg(tmp_path)
    (Path(cfg["Downloads"]) / "a.zip").write_bytes(b"PK")
    (Path(cfg["Downloads"]) / "b.zip").write_bytes(b"PK")
    host = _workbench(_fake_ctx(cfg))
    assert not host._verdict.isHidden()
    assert host._verdict._title.text() == "2 ZIPs Waiting"
    (Path(cfg["Downloads"]) / "a.zip").unlink()
    (Path(cfg["Downloads"]) / "b.zip").unlink()
    host._refresh()
    assert host._verdict.isHidden()                     # cleared → quiet again


# ── the ▶ Process Waiting ZIPs primary flow ───────────────────────────────────
def test_primary_processes_all_waiting_zips_as_one_batch(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    (Path(cfg["Downloads"]) / "a.zip").write_bytes(b"PK")
    (Path(cfg["Downloads"]) / "b.zip").write_bytes(b"PK")
    ctx = _fake_ctx(cfg)
    calls = {"batch": 0, "single": []}
    monkeypatch.setattr(LM, "process_existing_zips",
                        lambda c, log, refresh_cb=None, progress_cb=None:
                        calls.__setitem__("batch", calls["batch"] + 1))
    monkeypatch.setattr(LM, "process_zip",
                        lambda p, c, log, commit=True, finalize=True:
                        calls["single"].append(str(p)))
    host = _workbench(ctx)
    host._run_primary()                                 # headless: safe keys auto-selected
    assert calls["batch"] == 1                          # ALL selected → the one-commit batch path
    assert calls["single"] == []
    # the post-flow refresh announces the library change for the Parts tab to rescan
    assert ("library.changed",) in ctx.bus.emitted


def test_primary_subset_processes_only_the_selected_zips(tmp_path, monkeypatch):
    import LibraryManager as LM
    from ui import kit as K
    cfg = _libcfg(tmp_path)
    za = Path(cfg["Downloads"]) / "a.zip"; za.write_bytes(b"PK")
    (Path(cfg["Downloads"]) / "b.zip").write_bytes(b"PK")
    ctx = _fake_ctx(cfg)
    calls = {"batch": 0, "single": []}
    monkeypatch.setattr(LM, "process_existing_zips",
                        lambda *a, **k: calls.__setitem__("batch", calls["batch"] + 1))
    monkeypatch.setattr(LM, "process_zip",
                        lambda p, c, log, commit=True, finalize=True:
                        calls["single"].append(Path(p).name))
    monkeypatch.setattr(K, "_checkbox_preview", lambda *a, **k: [str(za)])
    host = _workbench(ctx)
    host._run_primary()
    assert calls["batch"] == 0
    assert calls["single"] == ["a.zip"]


def test_primary_with_no_zips_reports_the_distinct_empty_message(tmp_path):
    ctx = _fake_ctx(_libcfg(tmp_path))
    host = _workbench(ctx)
    host._run_primary()
    assert any("No vendor ZIPs" in m for m in ctx.services.logs)


# ── destructive tools: confirm → run → summarize → commit ─────────────────────
def test_destructive_tools_confirm_then_commit(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    asked = {"n": 0}
    _yes(monkeypatch, asked)
    monkeypatch.setattr(LM, "dedupe_footprint_library", lambda c, log=None: 2)
    host = _workbench(ctx)
    for title in ("Dedupe Symbol Library", "Deduplicate Footprints",
                  "Repair Footprint And Model Links", "Auto-Assign Library"):
        host._btn(title).click()
    assert asked["n"] == 4
    assert len(commits) == 4
    assert any("dedupe symbol" in m for m in commits)
    assert any("deduplicate footprints" in m for m in commits)
    assert any("repair" in m for m in commits)
    assert any("auto-assign" in m for m in commits)
    # the footprint dedupe's done line reports the real count
    assert any("removed 2 duplicate footprints" in m for m in ctx.services.logs)


def test_destructive_cancel_writes_nothing(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    commits, ran = [], {"n": 0}
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    monkeypatch.setattr(LM, "dedupe_symbol_library",
                        lambda *a, **k: ran.__setitem__("n", ran["n"] + 1) or 0)
    from ui import util
    monkeypatch.setattr(util, "confirm", lambda *a, **k: False)
    host = _workbench(ctx)
    host._btn("Dedupe Symbol Library").click()
    assert ran["n"] == 0
    assert commits == []
    assert any("Cancelled" in m for m in ctx.services.logs)


def test_done_line_surfaces_real_outcome(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    _yes(monkeypatch)
    monkeypatch.setattr(LM, "dedupe_symbol_library", lambda *a, **k: 2)
    host = _workbench(ctx)
    host._btn("Dedupe Symbol Library").click()
    assert any(m == "Dedupe Symbol Library: removed 2 duplicates." for m in ctx.services.logs)
    assert "Done." not in ctx.services.logs


def test_done_line_reports_failure(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    _yes(monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("nope")
    monkeypatch.setattr(LM, "dedupe_symbol_library", _boom)
    host = _workbench(ctx)
    host._btn("Dedupe Symbol Library").click()
    assert any("failed" in m.lower() for m in ctx.services.logs)
    assert not any("removed" in m for m in ctx.services.logs)


def test_readonly_tools_never_confirm_or_commit(tmp_path, monkeypatch):
    import LibraryManager as LM
    from ui.features import library as L
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits, asked = [], {"n": 0}
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: asked.__setitem__("n", asked["n"] + 1)
                                     or QMessageBox.Yes))
    monkeypatch.setattr(L.ND, "find_corrupt_kicad_files", lambda root: [])
    monkeypatch.setattr(LM, "export_catalog",
                        lambda cfg, log, progress_cb=None: tmp_path / "catalog.md")
    host = _workbench(ctx)
    host._btn("Scan For Corrupt Files…").click()
    host._btn("Export Catalog").click()
    assert asked["n"] == 0
    assert commits == []


def test_corrupt_scan_reports_offenders(tmp_path, monkeypatch):
    from ui.features import library as L
    ctx = _fake_ctx(_libcfg(tmp_path))
    monkeypatch.setattr(L.ND, "find_corrupt_kicad_files",
                        lambda root: [(tmp_path / "bad.kicad_sym", "unbalanced parentheses")])
    host = _workbench(ctx)
    host._scan_corrupt()
    assert any("bad.kicad_sym" in m for m in ctx.services.logs)


# ── undo history (trash lifecycle) ────────────────────────────────────────────
def _seed_trash(cfg, names=("20260101_010101", "20260202_020202")):
    trash = Path(cfg["SymbolLib"]).parent / ".trash"
    for n in names:
        d = trash / n
        d.mkdir(parents=True, exist_ok=True)
        (d / "MySymbols.kicad_sym").write_text("(kicad_symbol_lib)", encoding="utf-8")


def test_snapshot_lists_trash_newest_first(tmp_path):
    cfg = _libcfg(tmp_path)
    _seed_trash(cfg)
    host = _workbench(_fake_ctx(cfg))
    snaps = host._snapshot()["snapshots"]
    assert [Path(s).name for s in snaps] == ["20260202_020202", "20260101_010101"]


def test_undo_last_change_confirms_restores_and_commits(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    _seed_trash(cfg)
    ctx = _fake_ctx(cfg)
    commits, restored = [], {"n": 0}
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    monkeypatch.setattr(LM, "restore_last_trash",
                        lambda sym, log=None: restored.__setitem__("n", restored["n"] + 1) or True)
    asked = {"n": 0}
    _yes(monkeypatch, asked)
    host = _workbench(ctx)
    host._btn("Undo Last Change").click()
    assert asked["n"] == 1 and restored["n"] == 1
    assert any("undo" in m for m in commits)
    assert ("library.changed",) in ctx.bus.emitted     # the restore changes the library


def test_undo_with_no_snapshots_reports_and_never_commits(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    _yes(monkeypatch)
    host = _workbench(ctx)
    host._btn("Undo Last Change").click()
    assert commits == []
    assert any("no undo snapshot" in m.lower() for m in ctx.services.logs)


def test_empty_undo_history_confirms_and_reports_count(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    _seed_trash(cfg)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "empty_trash", lambda sym, log=None: 2)
    asked = {"n": 0}
    _yes(monkeypatch, asked)
    host = _workbench(ctx)
    host._btn("Empty Undo History").click()
    assert asked["n"] == 1
    assert any("2" in m and "snapshot" in m.lower() for m in ctx.services.logs)


# ── imports (ZIP / folder / merge) ────────────────────────────────────────────
def test_import_vendor_zip_seam_processes_and_announces(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    got = []
    monkeypatch.setattr(LM, "process_zip",
                        lambda p, c, log, commit=True, finalize=True: got.append(Path(p).name))
    host = _workbench(ctx)
    z = tmp_path / "vendor.zip"; z.write_bytes(b"PK")
    host._import_zip(path=str(z))
    assert got == ["vendor.zip"]
    assert ("library.changed",) in ctx.bus.emitted


def test_import_extracted_folder_commits_with_import_parts_message(tmp_path, monkeypatch):
    import LibraryManager as LM
    import nd_commit_msg as CM
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    moved, commits = [], []
    monkeypatch.setattr(LM, "move_files", lambda d, c, log: moved.append(Path(d).name))
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    host = _workbench(ctx)
    folder = tmp_path / "LM317_pack"; folder.mkdir()
    host._import_folder(path=str(folder))
    assert moved == ["LM317_pack"]
    assert commits and commits[0] == CM.import_parts(["LM317_pack"])


def test_merge_symbol_files_merges_and_commits(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    merged, commits = [], []
    monkeypatch.setattr(LM, "merge_symbols",
                        lambda target, sources, log: merged.append(
                            (Path(target).name, [Path(s).name for s in sources])))
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    host = _workbench(ctx)
    src = tmp_path / "extra.kicad_sym"; src.write_text("(kicad_symbol_lib)", encoding="utf-8")
    host._merge_symbol_files(sources=[str(src)])
    assert merged == [("MySymbols.kicad_sym", ["extra.kicad_sym"])]
    assert any("merge" in m for m in commits)


# ── machinery: location / registration / portability ─────────────────────────
def test_change_location_flow_pointer_apply_reload_in_order(tmp_path, monkeypatch):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    order = []
    monkeypatch.setattr(LM, "write_pointer", lambda p: order.append("pointer"))
    monkeypatch.setattr(LM, "apply_library_location", lambda p: order.append("apply"))
    monkeypatch.setattr(LM, "load_config",
                        lambda config_path=None: order.append("load") or
                        {"SymbolLib": str(tmp_path / "elsewhere" / "MySymbols.kicad_sym"),
                         "RepoRoot": str(tmp_path / "elsewhere")})
    host = _workbench(ctx)
    new = tmp_path / "elsewhere"; new.mkdir()
    host._change_location(path=str(new))
    assert order == ["pointer", "apply", "load"]
    assert ctx.cfg["RepoRoot"] == str(new)             # cfg updated IN PLACE (shared dict)
    assert ("library.changed",) in ctx.bus.emitted


def test_change_location_rejects_a_missing_dir(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    called = {"n": 0}
    monkeypatch.setattr(LM, "write_pointer", lambda p: called.__setitem__("n", called["n"] + 1))
    host = _workbench(ctx)
    host._change_location(path=str(tmp_path / "nope"))
    assert called["n"] == 0
    assert any("not" in m.lower() for m in ctx.services.logs)


def test_setup_kicad_reports_registration_message(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    monkeypatch.setattr(LM, "register_libraries",
                        lambda c, log: {"ok": True, "reason": "",
                                        "message": "Libraries registered.", "changed": True})
    host = _workbench(ctx)
    host._btn("Set Up KiCad Libraries").click()
    assert any("Libraries registered." in m for m in ctx.services.logs)


def test_check_handoff_reports_issues(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    monkeypatch.setattr(LM, "verify_handoff_readiness",
                        lambda c: {"ok": False,
                                   "issues": [{"ref": "R_0402", "kind": "missing_footprint",
                                               "detail": "no file", "how_to_fix": "add it"}],
                                   "counts": {"symbols": 1, "footprints": 0, "issues": 1}})
    host = _workbench(ctx)
    host._btn("Check Hand-Off Readiness").click()
    assert any("R_0402" in m for m in ctx.services.logs)


def test_make_portable_orchestrates_audit_rewrite_commit_audit(tmp_path, monkeypatch):
    import LibraryManager as LM
    ctx = _fake_ctx(_libcfg(tmp_path))
    order = []
    monkeypatch.setattr(LM, "verify_handoff_readiness",
                        lambda c: order.append("verify") or
                        {"ok": True, "issues": [], "counts": {"issues": 0}})
    monkeypatch.setattr(LM, "make_library_portable",
                        lambda c, log=None: order.append("portable") or
                        {"symbols_fixed": 2, "models_fixed": 1, "unresolved": []})
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: order.append("commit") or True)
    _yes(monkeypatch)
    host = _workbench(ctx)
    host._btn("Make Portable and Commit").click()
    assert order == ["verify", "portable", "commit", "verify"]
    assert any("2" in m for m in ctx.services.logs)    # fixed counts reach the log


# ── busy gate + leak discipline ───────────────────────────────────────────────
def test_busy_gate_disables_every_action(tmp_path):
    host = _workbench(_fake_ctx(_libcfg(tmp_path)))
    b = host._btn("Dedupe Symbol Library")
    assert b.isEnabled()
    host._busy["on"] = True
    assert not b.isEnabled()
    assert not host._btn("Make Portable and Commit").isEnabled()
    host._busy["on"] = False
    assert b.isEnabled()


def test_refresh_keeps_the_restyle_registry_flat(tmp_path):
    from ui import widgets as W
    host = _workbench(_fake_ctx(_libcfg(tmp_path)))
    before = len(W._RESTYLERS)
    # Cross-tab announces re-fire this fill repeatedly; the cards are repopulated with
    # the static vocabulary + VerdictSlot.set — the registry must never GROW (other
    # tests' dying widgets may shrink it, so <= like the git pilot's lock).
    for _ in range(10):
        host._refresh()
    assert len(W._RESTYLERS) <= before, (
        f"maintenance refresh leaked {len(W._RESTYLERS) - before} restyle callbacks")
