"""The Components view (library-v2 mockup) — the Parts tab as the 2-column
picker | canvas splitter, and the per-part actions that close the last of the
styled-vs-bare parity gap.

The Parts tab is a full-bleed `kit.panes` splitter: the picker (drop zone + finder +
grouped list) · the PartDetail canvas (Files previews — rendered symbol / footprint /
interactive 3D MeshView — plus identity + sourcing), carrying a Manage Part action
group. Those actions surface
the remaining bare capabilities: rename a symbol, reuse an existing symbol for a
footprint-only orphan, delete a footprint / 3D model file (with a dangling-reference
warning), and delete a whole part. Every action has an explicit-path seam so it drives
headlessly. Run:  python -m pytest tests/test_lib_parts_actions.py -q
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QSplitter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402
from ui.features import library as LIB  # noqa: E402
from ui.features import library_preview as P  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _fake_ctx(cfg):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    class _Bus:
        def __init__(self): self.subs = {}
        def emit(self, name, *a):
            for cb in self.subs.get(name, []):
                cb(*a)
        def on(self, name, cb): self.subs.setdefault(name, []).append(cb)
        def on_owned(self, name, cb, _owner): self.subs.setdefault(name, []).append(cb)

    return SimpleNamespace(cfg=cfg, services=_Svc(), theme=None, bus=_Bus())


def _libcfg(tmp_path):
    """U1 (symbol+footprint+model, complete) · SOT (footprint-only orphan)."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "U1" (property "Value" "U1" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_A" (id 2))'
        ' (property "MANUFACTURER" "ACME" (id 3)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "FP_A.kicad_mod").write_text(
        '(footprint "FP_A" (model ${MY3DMODELS}/U1.step))', encoding="utf-8")
    (fp / "SOT.kicad_mod").write_text('(footprint "SOT")', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    (mdl / "U1.step").write_bytes(b"ISO-10303-21;")
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}


def _rows(cfg):
    return {r["name"]: r for r in LM.scan_library_grouped(cfg)}


# ── the 2-column picker | canvas splitter (library-v2 mockup) ──────────────────
def test_parts_panel_is_a_two_pane_splitter(tmp_path):
    panel = LIB._parts_panel(_fake_ctx(_libcfg(tmp_path)), None)
    sp = panel.findChild(QSplitter)
    assert sp is not None
    assert sp.count() == 2                      # picker · canvas(detail)
    assert panel.parts_list is not None
    assert panel.detail is not None


def test_detail_mounts_the_three_previews_inline(tmp_path):
    # PartDetail owns the preview cards and mounts them inline at the top of its own
    # column (the mockup's Files section) — there is no separate canvas pane anymore.
    ctx = _fake_ctx(_libcfg(tmp_path))
    detail = P.PartDetail(ctx)
    assert detail._sym is not None and detail._fp is not None and detail._mdl is not None
    detail.show(_rows(ctx.cfg)["U1"])
    detail.grab()


# ── rename symbol ─────────────────────────────────────────────────────────────
def test_rename_symbol_renames_and_commits(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._rename_symbol(new_name="U1_RENAMED")
    text = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    names = [LM.extract_symbol_name(b) for b in LM.extract_symbol_blocks(text)]
    assert "U1_RENAMED" in names and "U1" not in names
    assert commits and any("U1_RENAMED" in m or "rename" in m.lower() for m in commits)


def test_rename_symbol_refuses_a_clash(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    # add a second symbol so the new name collides
    p = Path(cfg["SymbolLib"])
    p.write_text(p.read_text(encoding="utf-8").replace(
        ')\n', '  (symbol "TAKEN" (property "Value" "TAKEN" (id 1)) (pin 1))\n)\n'),
        encoding="utf-8")
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._rename_symbol(new_name="TAKEN")
    assert commits == []                         # nothing committed on a refused rename
    assert any("could not" in m.lower() or "exists" in m.lower() or "taken" in m.lower()
               for m in ctx.services.logs)


# ── reuse existing symbol for a footprint-only orphan ─────────────────────────
def test_reuse_symbol_for_orphan_duplicates_and_links(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["SOT"])               # the orphan footprint
    detail._reuse_symbol_for_orphan(source="U1")
    text = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    names = [LM.extract_symbol_name(b) for b in LM.extract_symbol_blocks(text)]
    assert any(n != "U1" for n in names if "SOT" in n or n.startswith("SOT"))  # a new symbol
    # the new symbol points at the orphan footprint
    assert "MyFootprints:SOT" in text
    assert commits


# ── delete footprint file (with dangling warning) ─────────────────────────────
def test_delete_footprint_warns_then_deletes(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    # U1's footprint is FP_A; U1 references it → the warning must name U1.
    warned = {}
    detail._delete_footprint(confirm=lambda refs: warned.update({"refs": refs}) or True)
    assert "U1" in warned["refs"]                # symbols_referencing_footprint surfaced
    assert not (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()
    assert commits


def test_delete_footprint_cancel_keeps_the_file(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._delete_footprint(confirm=lambda refs: False)
    assert (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()


# ── delete 3D model file (with dangling warning) ──────────────────────────────
def test_delete_model_warns_then_deletes(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    warned = {}
    detail._delete_model(confirm=lambda refs: warned.update({"refs": refs}) or True)
    assert "FP_A" in warned["refs"]              # footprints_referencing_model surfaced
    assert not (Path(cfg["ModelLib"]) / "U1.step").exists()
    assert commits


# ── delete whole part ─────────────────────────────────────────────────────────
def test_delete_part_removes_symbol_and_optionally_files(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._delete_part(confirm=lambda row: (True, True, True))   # (go, del_fp, del_model)
    text = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    assert "U1" not in [LM.extract_symbol_name(b) for b in LM.extract_symbol_blocks(text)]
    assert not (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()
    assert not (Path(cfg["ModelLib"]) / "U1.step").exists()
    assert commits
    assert detail._current is None               # the deleted part's detail is cleared


def test_delete_part_symbol_only_keeps_shared_files(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._delete_part(confirm=lambda row: (True, False, False))
    assert (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()   # files kept
    assert (Path(cfg["ModelLib"]) / "U1.step").exists()
    text = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    assert "U1" not in [LM.extract_symbol_name(b) for b in LM.extract_symbol_blocks(text)]


def test_delete_part_cancel_changes_nothing(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    detail._delete_part(confirm=lambda row: None)   # None = cancelled
    assert (Path(cfg["SymbolLib"]).read_text(encoding="utf-8")).count('symbol "U1"') == 1
    assert commits == []


# ── the actions are wired into the detail header per row (mockup .hactions) ────
def test_manage_actions_present_for_a_real_part(tmp_path):
    from PyQt5.QtWidgets import QPushButton
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["U1"])
    labels = {b.text() for b in detail.findChildren(QPushButton)}
    assert "Rename" in labels
    # Reveal Files + the delete family live in the ⋯ kebab menu.
    entries = {a.text() for a in detail._kebab_menu.actions() if a.text()}
    assert "Reveal Files" in entries
    assert {"Delete Footprint File", "Delete 3D Model File", "Delete Whole Part…"} <= entries


def test_orphan_offers_reuse_symbol(tmp_path):
    from PyQt5.QtWidgets import QPushButton
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    detail = P.PartDetail(ctx)
    detail.show(_rows(cfg)["SOT"])               # footprint-only orphan
    labels = {b.text() for b in detail.findChildren(QPushButton)}
    assert "Reuse Symbol" in labels
