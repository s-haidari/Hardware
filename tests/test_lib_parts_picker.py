"""Library — Parts picker scan affordances (subsystem: library-parts-picker).

Covers the enhancements to the parts picker: the always-visible inline filter bar,
search highlighting, multi-select + selection footer, the row Duplicate badge, the
Manage Duplicates side-by-side modal (bulk delete through the proven remove_part
backend), Export Visible Parts (CSV/JSON), and smart-default grouping persisted per
user. Run:  python -m pytest tests/test_lib_parts_picker.py -q
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

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


def _dup_cfg(tmp_path):
    """Two DISTINCT symbols (U_A, U_B) that carry the SAME manufacturer part number —
    the honest 'same part imported twice under two names' duplicate — each keyed on its
    own footprint, plus an unrelated single part R1."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "U_A" (property "Value" "U_A" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_A" (id 2))'
        ' (property "Manufacturer Part Number" "STM32F103" (id 3)) (pin 1))\n'
        '  (symbol "U_B" (property "Value" "U_B" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_B" (id 2))'
        ' (property "Manufacturer Part Number" "STM32F103" (id 3)) (pin 1))\n'
        '  (symbol "R1" (property "Value" "R1" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_R" (id 2))'
        ' (property "Manufacturer Part Number" "RC0402" (id 3)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    for stem in ("FP_A", "FP_B", "FP_R"):
        (fp / f"{stem}.kicad_mod").write_text(f'(footprint "{stem}")', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}


def _rows(cfg):
    return {r["name"]: r for r in LM.scan_library_grouped(cfg)}


# ── backend delete path: the coupled proof the RIGHT symbols go, others untouched ──
def test_remove_part_removes_only_target_symbol(tmp_path):
    """The modal's bulk-delete backend is remove_part (the same per-part delete the
    detail uses). Deleting the U_A duplicate removes ONLY U_A's symbol; U_B and R1 —
    and every footprint file — stay. This is the coupled test the acceptance requires."""
    cfg = _dup_cfg(tmp_path)
    rows = _rows(cfg)
    log = P.LogSink(_fake_ctx(cfg).services)
    res = LM.remove_part(cfg, rows["U_A"], log, delete_footprint=False, delete_model=False)
    assert res["ok"] and res["symbols_removed"] == ["U_A"]
    text = (tmp_path / "MySymbols.kicad_sym").read_text(encoding="utf-8")
    assert '"U_A"' not in text, "U_A symbol block must be gone"
    assert '"U_B"' in text and '"R1"' in text, "the other symbols must be untouched"
    # No footprint deletion was asked for, so every footprint file survives.
    for stem in ("FP_A", "FP_B", "FP_R"):
        assert (tmp_path / "fps" / f"{stem}.kicad_mod").exists()


def test_remove_symbols_by_indices_removes_expected_and_leaves_others(tmp_path):
    """The index-based bulk backend: removing block 0 (U_A) by its expected name removes
    exactly that block and leaves U_B / R1, and a stale expected-name aborts the whole
    operation (no partial delete)."""
    cfg = _dup_cfg(tmp_path)
    sym = Path(cfg["SymbolLib"])
    log = P.LogSink(_fake_ctx(cfg).services)
    # Aborts cleanly when the expected name no longer matches the block at that index.
    assert LM.remove_symbols_by_indices(sym, {0: "WRONG"}, log) == 0
    assert sym.read_text(encoding="utf-8").count("(symbol ") == 3, "aborted delete changes nothing"
    # Remove block 0 (U_A) by its true name → exactly one removed, others intact.
    assert LM.remove_symbols_by_indices(sym, {0: "U_A"}, log) == 1
    text = sym.read_text(encoding="utf-8")
    assert '"U_A"' not in text and '"U_B"' in text and '"R1"' in text


def test_duplicate_manager_dialog_bulk_delete(tmp_path):
    """The Manage Duplicates modal deletes the checked (non-keeper) parts through
    remove_part and leaves the kept one. Driven via the confirm seam (no modal)."""
    cfg = _dup_cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    rows = _rows(cfg)
    dlg = P.DuplicateManagerDialog(ctx, [rows["U_A"], rows["U_B"]], on_changed=lambda: None)
    # The most-complete part is pre-kept (unchecked); the other pre-checked for delete.
    checked = [r.get("name") for r, cb in dlg._checks if cb.isChecked()]
    kept = [r.get("name") for r, cb in dlg._checks if not cb.isChecked()]
    assert len(checked) == 1 and len(kept) == 1
    dlg._delete_checked(confirm=lambda targets: True)
    text = (tmp_path / "MySymbols.kicad_sym").read_text(encoding="utf-8")
    # The kept part survives; the deleted one is gone.
    assert f'"{kept[0]}"' in text
    assert f'"{checked[0]}"' not in text


# ── PartsList: dup badge · highlight · multi-select footer ──────────────────────────
def test_dup_badge_visible_for_mpn_duplicates(tmp_path):
    cfg = _dup_cfg(tmp_path)
    rows = list(LM.scan_library_grouped(cfg))
    pl = P.PartsList(rows, on_select=lambda r: None)
    by = {}
    for r, _it, w in pl._items:
        by[r.get("name")] = not w._dup_badge.isHidden()
    assert by["U_A"] and by["U_B"], "the shared-MPN parts show the Duplicate badge"
    assert by["R1"] is False, "a unique part shows no badge"


def test_search_highlights_the_match(tmp_path):
    cfg = _dup_cfg(tmp_path)
    rows = list(LM.scan_library_grouped(cfg))
    pl = P.PartsList(rows, on_select=lambda r: None)
    pl.filter("u_a")
    # The visible U_A row's primary label carries a highlight span around the match.
    hit = None
    for r, it, w in pl._items:
        if not it.isHidden() and r.get("name") == "U_A":
            hit = w._prim
    assert hit is not None
    assert "<span" in hit.text().lower(), "matched substring must be wrapped in a highlight span"
    pl.filter("")
    for _r, _it, w in pl._items:
        assert "<span" not in w._prim.text().lower(), "clearing the query removes the highlight"


def test_multiselect_footer_counts(tmp_path):
    cfg = _dup_cfg(tmp_path)
    rows = list(LM.scan_library_grouped(cfg))
    pl = P.PartsList(rows, on_select=lambda r: None)
    # Select the two duplicate items → footer reads 'N of M selected'; both are dup rows.
    for r, it, _w in pl._items:
        if r.get("name") in ("U_A", "U_B"):
            it.setSelected(True)
    pl._update_selection_footer()
    assert "2 of" in pl._footer.text()
    assert {r.get("name") for r in pl.selected_duplicate_rows()} == {"U_A", "U_B"}


# ── smart default grouping + persistence ────────────────────────────────────────────
def test_smart_group_by_thresholds():
    assert LIB._smart_group_by(5) == "None"
    assert LIB._smart_group_by(19) == "None"
    assert LIB._smart_group_by(20) == "Category"
    assert LIB._smart_group_by(100) == "Category"
    assert LIB._smart_group_by(101) == "Manufacturer"


def test_group_by_persists_across_relaunch(tmp_path, monkeypatch):
    """A grouping change writes the LibraryGroupBy setting; a fresh PartsList seeded with
    it restores that grouping — the per-user 'last used' contract."""
    store = {}
    monkeypatch.setattr(LM, "write_setting", lambda k, v, **kw: store.__setitem__(k, v) or True)
    monkeypatch.setattr(LM, "read_setting", lambda k, d=None, **kw: store.get(k, d))
    rows = list(LM.scan_library_grouped(_dup_cfg(tmp_path)))
    pl = P.PartsList(rows, on_select=lambda r: None, group_by="Category",
                     on_group_change=lambda m: LM.write_setting(LIB._GROUP_BY_SETTING, m))
    pl.set_group_by("Manufacturer")
    assert store.get(LIB._GROUP_BY_SETTING) == "Manufacturer"
    # A relaunch reads the setting and seeds the list with it.
    saved = LM.read_setting(LIB._GROUP_BY_SETTING, "")
    pl2 = P.PartsList(rows, on_select=lambda r: None, group_by=saved)
    assert pl2.group_by() == "Manufacturer"


# ── Export Visible Parts (CSV/JSON) ─────────────────────────────────────────────────
def test_export_visible_writes_csv_and_json(tmp_path):
    cfg = _dup_cfg(tmp_path)
    rows = list(LM.scan_library_grouped(cfg))
    csv_p = tmp_path / "out.csv"
    n = LIB._write_parts_export(csv_p, cfg, rows)
    assert n == len(rows)
    head, *body = csv_p.read_text(encoding="utf-8").splitlines()
    assert head == "name,mpn,manufacturer,category,completion,model"
    assert any(line.startswith("U_A,STM32F103,") for line in body)
    json_p = tmp_path / "out.json"
    LIB._write_parts_export(json_p, cfg, rows)
    recs = json.loads(json_p.read_text(encoding="utf-8"))
    assert {r["name"] for r in recs} == {"U_A", "U_B", "R1"}
    assert all(set(r) == {"name", "mpn", "manufacturer", "category", "completion", "model"}
               for r in recs)


def test_export_reflects_the_visible_filter(tmp_path):
    """Export writes the CURRENT view: a search narrows the exported set."""
    cfg = _dup_cfg(tmp_path)
    rows = list(LM.scan_library_grouped(cfg))
    pl = P.PartsList(rows, on_select=lambda r: None)
    pl.filter("r1")
    vis = pl.visible_rows()
    assert {r.get("name") for r in vis} == {"R1"}
    out = tmp_path / "v.json"
    LIB._write_parts_export(out, cfg, vis)
    recs = json.loads(out.read_text(encoding="utf-8"))
    assert [r["name"] for r in recs] == ["R1"]
