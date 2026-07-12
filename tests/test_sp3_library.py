"""Phase 3 (Library overhaul) — new backend surface (inline edit + drop-in) and
the merged Parts view (granular filter facets, editable detail fields, drop-in).
Run:  python -m pytest tests/test_sp3_library.py -q
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


# ── backend: inline-edit + drop-in helpers ─────────────────────────────────────
def _libcfg(tmp_path):
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "R_0402" (property "Value" "R_0402" (id 1)) (pin 1))\n'
        '  (symbol "2N7002" (property "Value" "2N7002" (id 1)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "R_0402.kicad_mod").write_text('(footprint "R_0402")', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}


def test_set_library_symbol_property_writes_and_is_idempotent(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    assert LM.set_library_symbol_property(cfg, "R_0402", "MANUFACTURER", "Yageo")
    props = LM.extract_symbol_properties(
        [b for b in LM.extract_symbol_blocks(Path(cfg["SymbolLib"]).read_text())
         if LM.extract_symbol_name(b) == "R_0402"][0])
    assert props.get("MANUFACTURER") == "Yageo"
    # writing the same value again changes nothing
    assert LM.set_library_symbol_property(cfg, "R_0402", "MANUFACTURER", "Yageo") is False
    # an unknown symbol name never writes
    assert LM.set_library_symbol_property(cfg, "NOPE", "MANUFACTURER", "x") is False


def test_set_library_symbol_property_updates_all_named_symbols(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    assert LM.set_library_symbol_property(cfg, ["R_0402", "2N7002"], "Description", "Part")
    text = Path(cfg["SymbolLib"]).read_text()
    for name in ("R_0402", "2N7002"):
        b = [x for x in LM.extract_symbol_blocks(text) if LM.extract_symbol_name(x) == name][0]
        assert LM.extract_symbol_properties(b).get("Description") == "Part"


def test_set_library_symbol_footprint_qualifies(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    assert LM.set_library_symbol_footprint(cfg, "2N7002", "SOT-23")
    b = [x for x in LM.extract_symbol_blocks(Path(cfg["SymbolLib"]).read_text())
         if LM.extract_symbol_name(x) == "2N7002"][0]
    assert LM.extract_symbol_properties(b).get("Footprint") == "MyFootprints:SOT-23"


def test_install_model_file_copies_and_filters_suffix(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    src = tmp_path / "Cap.step"; src.write_bytes(b"ISO-10303-21;")
    assert LM.install_model_file(cfg, src) == "Cap.step"
    assert (Path(cfg["ModelLib"]) / "Cap.step").exists()
    # a re-drop of the identical file still resolves to its name
    assert LM.install_model_file(cfg, src) == "Cap.step"
    bad = tmp_path / "notamodel.txt"; bad.write_text("x")
    assert LM.install_model_file(cfg, bad) is None


def test_install_footprint_file_returns_stem(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    src = tmp_path / "SOT-23.kicad_mod"; src.write_text('(footprint "SOT-23")')
    assert LM.install_footprint_file(cfg, src) == "SOT-23"
    assert (Path(cfg["FootprintLib"]) / "SOT-23.kicad_mod").exists()
    bad = tmp_path / "x.step"; bad.write_bytes(b"y")
    assert LM.install_footprint_file(cfg, bad) is None


def test_install_symbol_file_merges(tmp_path):
    import LibraryManager as LM
    cfg = _libcfg(tmp_path)
    src = tmp_path / "new.kicad_sym"
    src.write_text('(kicad_symbol_lib\n  (symbol "LM317" (pin 1))\n)\n', encoding="utf-8")

    class _Log:
        def write(self, *_a):
            pass
    assert LM.install_symbol_file(cfg, src, _Log()) is True
    names = [LM.extract_symbol_name(b)
             for b in LM.extract_symbol_blocks(Path(cfg["SymbolLib"]).read_text())]
    assert "LM317" in names


# ── UI: filter facets ──────────────────────────────────────────────────────────
def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_partslist_facets_narrow_and_count():
    _app()
    from ui.features.library_preview import PartsList
    rows = [
        {"name": "A", "mpn": "A", "manufacturer": "M", "has_symbol": True,
         "has_footprint": True, "has_model": True, "dangling": False,
         "has_real_mpn": True, "datasheet": "http://d", "description": "d",
         "category": "Misc"},                                                   # Complete (8/8)
        {"name": "B", "mpn": "B", "manufacturer": "M", "has_symbol": True,
         "has_footprint": True, "has_model": False, "dangling": False},         # Missing Model
        {"name": "C", "mpn": "C", "manufacturer": None, "has_symbol": True,
         "has_footprint": False, "has_model": False, "dangling": False},        # Missing Footprint + Data
        {"name": "D", "mpn": "D", "manufacturer": "M", "has_symbol": False,
         "has_footprint": True, "has_model": True, "dangling": True},           # Missing Symbol + Dangling
    ]
    lst = PartsList(rows, on_select=lambda r: None)
    counts = lst.facet_counts()
    # LIB-02 taxonomy: primary Complete-vs-Missing, with a Missing breakdown.
    assert counts["All"] == 4
    assert counts["Complete"] == 1               # only A is 8/8 (assets + full identity)
    assert counts["Missing"] == 3                # everything not complete
    assert counts["Missing 3D Model"] == 1
    assert counts["Missing Footprint"] == 1
    # LM:2117: a footprint-only orphan (D) is its own 'Unlinked Footprints' state,
    # NOT lumped into a 'Missing Symbol' bucket, and it carries no Mouser-data flag
    # (an orphan footprint has no identity to source).
    assert counts["Unlinked Footprints"] == 1
    assert "Missing Symbol" not in counts
    assert counts["Dangling"] == 1
    assert counts["Missing Mouser Data"] == 1        # only C (has a symbol, no manufacturer)
    lst.set_facet("Complete")
    assert lst.visible_count() == 1
    lst.set_facet("Missing")
    assert lst.visible_count() == 3
    lst.set_facet("Missing 3D Model")
    assert lst.visible_count() == 1
    lst.set_facet("Unlinked Footprints")
    assert lst.visible_count() == 1                  # just the footprint-only orphan D
    lst.set_facet("All")
    assert lst.visible_count() == 4


# ── UI: inline edit + drop-in through the detail pane ──────────────────────────
def _fake_ctx(cfg):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    from types import SimpleNamespace
    return SimpleNamespace(cfg=cfg, services=_Svc())




def test_maintenance_summarizers_report_tool_result():
    # The done-line must surface the tool's actual outcome, not a bare "Done.".
    from ui.features import library as L
    # dedupe returns an int (# removed)
    assert L._summarize_dedupe(3) == "Dedupe Symbol Library: removed 3 duplicates."
    assert L._summarize_dedupe(1) == "Dedupe Symbol Library: removed 1 duplicate."
    assert L._summarize_dedupe(0) == "Dedupe Symbol Library: no duplicates."
    # repair returns a counts dict
    assert L._summarize_repair(
        {"symbols_fixed": 2, "footprints_fixed": 1, "footprints_no_model": 0}
    ) == "Repair Footprint And Model Links: fixed 2 symbol links and 1 model line."
    assert L._summarize_repair(
        {"symbols_fixed": 0, "footprints_fixed": 0, "footprints_no_model": 3}
    ) == ("Repair Footprint And Model Links: nothing to fix. "
          "(3 footprints still without a model)")
    # auto-assign returns footprint/model counts
    assert L._summarize_auto_assign(
        {"footprint_count": 4, "model_count": 0}
    ) == "Auto-Assign Library: linked 4 footprints."
    assert L._summarize_auto_assign(
        {"footprint_count": 0, "model_count": 0}
    ) == "Auto-Assign Library: nothing to link."






def test_parts_panel_action_row_has_no_maintenance_button(tmp_path):
    # LIB-08/12: sourcing + import stay on Parts; Maintenance moved to its own tab.
    _app()
    from ui.features import library as L
    from PyQt5.QtWidgets import QPushButton
    panel = L._parts_panel(_fake_ctx(_libcfg(tmp_path)), None)
    labels = {b.text() for b in panel.findChildren(QPushButton)}
    assert "Maintenance" not in labels
    assert "Import ZIP" in labels


def test_preview_card_offers_replace_when_filled(tmp_path):
    # LIB-11: a filled asset card lets you Replace it (not just drop into an empty one).
    _app()
    from ui.features.library_preview import PreviewCard
    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QPushButton
    card = PreviewCard("Footprint")
    card.enable_dropin(lambda p: None, (".kicad_mod",), "Add Footprint")
    img = QImage(12, 12, QImage.Format_RGB32); img.fill(0)
    card.set_image(img)
    labels = {b.text() for b in card.findChildren(QPushButton) if b.isVisibleTo(card)}
    assert "Replace" in labels


def _prop(cfg, sym, key):
    import LibraryManager as LM
    b = [x for x in LM.extract_symbol_blocks(Path(cfg["SymbolLib"]).read_text())
         if LM.extract_symbol_name(x) == sym][0]
    return LM.extract_symbol_properties(b).get(key)


def test_detail_inline_edit_writes_but_defers_commit_until_save(tmp_path, monkeypatch):
    # LIB-flash fix: an inline field edit writes to disk IMMEDIATELY but must NOT
    # commit+push per keystroke (the per-field push storm flashed windows on
    # Windows). The commit+push happens once, on the explicit Save.
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "R_0402", "symbols": ["R_0402"],
              "footprint": "R_0402", "manufacturer": None, "description": None})
    det._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "Yageo")
    # written to disk …
    assert _prop(cfg, "R_0402", "MANUFACTURER") == "Yageo"
    assert det._current.get("manufacturer") == "Yageo"        # detail reflects the edit
    # … but NOT committed yet, and the Save bar is now showing.
    assert commits == []                                      # no per-field push (the fix)
    assert det._unsaved is True
    assert det._savebar.isVisibleTo(det)
    # Explicit Save commits + pushes ONCE.
    det._save_changes()
    assert len(commits) == 1 and "Manufacturer" in commits[0]
    assert det._unsaved is False
    assert not det._savebar.isVisibleTo(det)


def test_detail_multiple_edits_batch_into_one_commit(tmp_path, monkeypatch):
    # Three field edits → still zero pushes while editing → exactly ONE commit on Save.
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "R_0402", "symbols": ["R_0402"],
              "footprint": "R_0402", "manufacturer": None, "description": None})
    det._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "Yageo")
    det._commit_field("Description", "Description", "description", "10k 0402 resistor")
    det._commit_field("Datasheet", "Datasheet", "datasheet", "https://example.com/ds.pdf")
    assert commits == []                                      # nothing pushed while editing
    assert len(det._unsaved_edits) == 3
    det._save_changes()
    assert len(commits) == 1                                  # one batched commit
    assert "3 library part fields" in commits[0]
    # all three landed on disk
    assert _prop(cfg, "R_0402", "MANUFACTURER") == "Yageo"
    assert _prop(cfg, "R_0402", "Datasheet") == "https://example.com/ds.pdf"


def test_detail_discard_reverts_unsaved_edits(tmp_path, monkeypatch):
    # Discard restores the work tree to the last saved version (git checkout --).
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    restored = []
    monkeypatch.setattr(LM, "git_discard_uncommitted",
                        lambda c, log: restored.append(True) or True)
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "R_0402", "symbols": ["R_0402"],
              "footprint": "R_0402", "manufacturer": None})
    det._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "Yageo")
    assert det._unsaved is True
    det._apply_discard()                                      # the no-modal seam
    assert restored == [True]
    assert det._unsaved is False
    assert not det._savebar.isVisibleTo(det)


def test_structural_mutation_clears_pending_edits(tmp_path, monkeypatch):
    # A structural mutation (rename/drop-in/delete) commits the whole work tree, so
    # any pending inline edits ride along — the Save bar must clear, not linger stale.
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "R_0402", "symbols": ["R_0402"],
              "footprint": "R_0402", "manufacturer": None})
    det._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "Yageo")
    assert det._unsaved is True
    det._mutation_refresh({"name": "R_0402"})                 # the shared post-commit refresh
    assert det._unsaved is False
    assert not det._savebar.isVisibleTo(det)


def test_elide_label_exposes_full_text_on_hover(tmp_path):
    # "Footprints can get cut off": an elided label (parts-list rows, incl. a
    # footprint-only orphan's name) must expose its FULL text as a tooltip so the
    # ellipsis never truly hides it. A short label that fits carries no tooltip.
    _app()
    from ui.features.library_preview import _ElideLabel
    long_name = "Capacitor_SMD_C_0402_1005Metric_Pad0.66x0.95mm_HandSolder"
    lab = _ElideLabel(long_name)
    lab.setFixedWidth(40)
    lab._reelide()
    assert lab.text() != long_name                 # it was elided to fit
    assert lab.toolTip() == long_name              # … but the full name is recoverable
    lab.setFixedWidth(4000)
    lab._reelide()
    assert lab.toolTip() == ""                      # fits → no redundant tooltip


def test_footprint_caption_names_the_footprint(tmp_path):
    # The footprint preview card leads its caption with the footprint's own name so
    # the detail says WHICH footprint it is; the caption word-wraps (never clips).
    _app()
    from ui.features import library_preview as P
    ctx = _fake_ctx(_libcfg(tmp_path))
    det = P.PartDetail(ctx)
    det._current = {"name": "R_0402", "footprint": "SOT-23_LongHandSolderVariant"}
    cap = det._fp_caption({"pads": 3, "width_mm": 2.9, "height_mm": 2.8})
    assert cap.startswith("SOT-23_LongHandSolderVariant · ")
    assert "3 Pads" in cap
    assert det._fp._cap.wordWrap() is True         # long names wrap instead of clipping


def test_detail_dropin_model_links_and_commits(tmp_path, monkeypatch):
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    src = tmp_path / "Res.step"; src.write_bytes(b"ISO-10303-21;")
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "R_0402", "symbols": ["R_0402"],
              "footprint": "R_0402", "model": None, "has_model": False})
    det._dropin_model(str(src))
    # the model is installed AND written into the real footprint file (the
    # KiCad/BOM/auto_assign-visible tie), not just a JSON side-map
    assert (Path(cfg["ModelLib"]) / "Res.step").exists()
    fp_text = (Path(cfg["FootprintLib"]) / "R_0402.kicad_mod").read_text(encoding="utf-8")
    assert LM.footprint_has_model(fp_text)
    assert "${MY3DMODELS}/Res.step" in fp_text
    assert det._current.get("model") == "Res.step"


def test_detail_dropin_footprint_links_symbol(tmp_path, monkeypatch):
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    cfg = _libcfg(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    src = tmp_path / "SOT-23.kicad_mod"; src.write_text('(footprint "SOT-23")')
    det = P.PartDetail(ctx)
    det.show({"name": "2N7002", "mpn": "2N7002", "symbols": ["2N7002"],
              "footprint": None, "has_footprint": False})
    det._dropin_footprint(str(src))
    assert (Path(cfg["FootprintLib"]) / "SOT-23.kicad_mod").exists()
    b = [x for x in LM.extract_symbol_blocks(Path(cfg["SymbolLib"]).read_text())
         if LM.extract_symbol_name(x) == "2N7002"][0]
    assert LM.extract_symbol_properties(b).get("Footprint") == "MyFootprints:SOT-23"
    assert det._current.get("footprint") == "SOT-23"


def test_parts_panel_single_view_has_no_subtabs(tmp_path):
    _app()
    import LibraryManager as LM
    from ui import widgets as W
    from ui.features import library as L
    cfg = _libcfg(tmp_path)
    # a real grouped scan so the panel builds against actual rows
    ctx = _fake_ctx(cfg)
    panel = L._parts_panel(ctx, None)
    assert hasattr(panel, "parts_list") and hasattr(panel, "detail")
    # the merged Library is a single-panel Workspace: no sub-tab bar
    ws = W.Workspace(ctx, "Library", [("Parts", lambda c: L._parts_panel(c, None))])
    assert ws._tabs == []


def test_rescan_preserves_selection_not_top(tmp_path):
    _app()
    from ui.features.library_preview import PartsList
    picked = []
    rows = [{"mpn": "A", "name": "A", "has_symbol": True, "has_footprint": True,
             "has_model": True, "dangling": False},
            {"mpn": "B", "name": "B", "has_symbol": True, "has_footprint": True,
             "has_model": False, "dangling": False}]
    lst = PartsList(rows, on_select=lambda r: picked.append(r and r.get("mpn")))
    b = next(r for r in lst._visible if r["mpn"] == "B")   # header-aware (grouped list)
    lst._list.setCurrentRow(lst._item_row_for(b))    # user selects B
    assert picked[-1] == "B"
    # a mutation rescan (B gained a model) must keep B selected, not jump to A
    lst.set_rows([dict(r) for r in rows[:1]] +
                 [{"mpn": "B", "name": "B", "has_symbol": True, "has_footprint": True,
                   "has_model": True, "dangling": False}])
    assert picked[-1] == "B", "rescan must stay on the selected part"


def test_end_to_end_edit_and_dropin_wiring(tmp_path, monkeypatch):
    """Drive the REAL panel: select a part, edit a field, drop in a model — and
    assert each mutation persists, refreshes the list, and stays on the part."""
    _app()
    import LibraryManager as LM
    from ui.features import library as L
    from ui.features.library_preview import PartsList, PartDetail

    # a library with one symbol-bearing part that is missing its manufacturer + model
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "R_0402" (property "Value" "R_0402" (id 1))'
        ' (property "Footprint" "MyFootprints:R_0402" (id 2)) (pin 1))\n)\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "R_0402.kicad_mod").write_text('(footprint "R_0402")')
    mdl = tmp_path / "models"; mdl.mkdir()
    cfg = {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
           "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    ctx = _fake_ctx(cfg)

    panel = L._parts_panel(ctx, None)
    pl = panel.parts_list
    det = panel.detail
    assert pl.visible_count() == 1
    assert det._current and det._current.get("mpn") == "R_0402"     # auto-selected + shown

    # inline edit: add a manufacturer -> persisted, "Missing Mouser Data" count drops to 0
    assert pl.facet_counts()["Missing Mouser Data"] == 1
    det._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "Yageo")
    b = [x for x in LM.extract_symbol_blocks(sym.read_text())
         if LM.extract_symbol_name(x) == "R_0402"][0]
    assert LM.extract_symbol_properties(b).get("MANUFACTURER") == "Yageo"
    assert pl.facet_counts()["Missing Mouser Data"] == 0            # rescan refreshed the list
    assert det._current.get("mpn") == "R_0402"               # stayed on the part

    # drop in a 3D model -> installed, linked, "Missing 3D Model" count drops, still on part
    assert pl.facet_counts()["Missing 3D Model"] == 1
    src = tmp_path / "R.step"; src.write_bytes(b"ISO-10303-21;")
    det._dropin_model(str(src))
    assert (mdl / "R.step").exists()
    # the real footprint file carries the (model …) line — so the rescan sees
    # has_model via the footprint's own reference, not a JSON side-map
    fp_text = (fp / "R_0402.kicad_mod").read_text(encoding="utf-8")
    assert LM.footprint_has_model(fp_text) and "${MY3DMODELS}/R.step" in fp_text
    assert pl.facet_counts()["Missing 3D Model"] == 0
    assert det._current.get("mpn") == "R_0402"


# ── LM:2117 · footprint-only orphans: create + link a symbol ──────────────────
def test_new_symbol_block_links_footprint():
    import LibraryManager as LM
    block = LM.new_symbol_block("MYPART", "SOT23_DIO-L")
    assert LM.extract_symbol_name(block) == "MYPART"
    # a valid, geometry-independent stub that points at the footprint in the shared lib
    assert LM.symbol_footprint_ref(block) == "SOT23_DIO-L"
    props = LM.extract_symbol_properties(block)
    assert props.get("Footprint") == "MyFootprints:SOT23_DIO-L"
    assert props.get("Reference") == "U"


def test_create_symbol_for_footprint_appends_and_dedupes_name(tmp_path):
    import LibraryManager as LM
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text('(kicad_symbol_lib (version 20211014) (generator "x")\n)\n',
                   encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    cfg = {"SymbolLib": str(sym), "FootprintLib": str(fp), "Libs": str(tmp_path)}
    name = LM.create_symbol_for_footprint(cfg, "DRT3-L")
    assert name == "DRT3-L"
    blocks = LM.extract_symbol_blocks(sym.read_text(encoding="utf-8"))
    assert [LM.extract_symbol_name(b) for b in blocks] == ["DRT3-L"]
    assert LM.symbol_footprint_ref(blocks[0]) == "DRT3-L"
    # a name clash never clobbers an existing symbol — it de-duplicates the name
    name2 = LM.create_symbol_for_footprint(cfg, "DRT3-L")
    assert name2 == "DRT3-L_2"
    assert len(LM.extract_symbol_blocks(sym.read_text(encoding="utf-8"))) == 2
    # no footprint stem -> no-op
    assert LM.create_symbol_for_footprint(cfg, "") is None


def test_detail_create_symbol_turns_orphan_into_placeable_part(tmp_path, monkeypatch):
    """The footprint-only orphan detail offers a Create Symbol action (not a dead
    read-only form); clicking it builds a linked symbol, commits, and re-shows the
    row as a now-placeable part."""
    _app()
    import LibraryManager as LM
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QPushButton
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text('(kicad_symbol_lib (version 20211014) (generator "x")\n)\n',
                   encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "ORPH_FP.kicad_mod").write_text('(footprint "ORPH_FP")', encoding="utf-8")
    cfg = {"SymbolLib": str(sym), "FootprintLib": str(fp), "Libs": str(tmp_path),
           "RepoRoot": str(tmp_path)}
    ctx = _fake_ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)

    det = P.PartDetail(ctx)
    orphan = {"name": "ORPH_FP", "mpn": None, "symbols": [], "has_symbol": False,
              "footprint": "ORPH_FP", "has_footprint": True}
    det.show(orphan)
    # the actionable identity: a Create Symbol CTA, NOT an editable/dead identity list
    create = [b for b in det.findChildren(QPushButton) if b.text() == "Create Symbol"]
    assert len(create) == 1

    det._create_symbol(orphan)                        # headless -> runs synchronously
    blocks = LM.extract_symbol_blocks(sym.read_text(encoding="utf-8"))
    assert [LM.extract_symbol_name(b) for b in blocks] == ["ORPH_FP"]
    assert LM.symbol_footprint_ref(blocks[0]) == "ORPH_FP"
    assert commits, "creating a symbol commits the library mutation"
    # the row is now a placeable part with a symbol
    assert det._current.get("has_symbol") is True
    assert det._current.get("symbols") == ["ORPH_FP"]


# ── LM:2117 · Maintenance IA: density variants vs true duplicates ─────────────
def test_footprint_density_variants_collapse_under_base():
    import LibraryManager as LM
    assert LM.footprint_density_variant("SOT23_DIO-L") == ("SOT23_DIO", "L")
    assert LM.footprint_density_variant("QFN1610_STM-M") == ("QFN1610_STM", "M")
    assert LM.footprint_density_variant("DRT3") == ("DRT3", None)
    assert LM.footprint_density_variant("CAP-X") == ("CAP-X", None)   # not a density level
    grouped = LM.group_footprint_variants(
        ["SOT23_DIO-M", "SOT23_DIO-L", "DRT3", "QFN1610_STM-L"])
    assert grouped["SOT23_DIO"] == ["SOT23_DIO-L", "SOT23_DIO-M"]     # collapsed + sorted
    assert grouped["DRT3"] == ["DRT3"]
    assert grouped["QFN1610_STM"] == ["QFN1610_STM-L"]


def test_find_duplicate_footprints_ignores_density_variants(tmp_path):
    """A true duplicate is byte-identical geometry (name aside). Density variants
    have different courtyards, so they must NOT be flagged as duplicates."""
    import LibraryManager as LM
    fp = tmp_path / "fps"; fp.mkdir()
    cfg = {"FootprintLib": str(fp)}
    # identical geometry, different names -> a true duplicate pair
    (fp / "PARTA.kicad_mod").write_text(
        '(footprint "PARTA" (layer F.Cu) (pad 1 smd rect (at 0 0)))', encoding="utf-8")
    (fp / "PARTA_COPY.kicad_mod").write_text(
        '(footprint "PARTA_COPY" (layer F.Cu) (pad 1 smd rect (at 0 0)))', encoding="utf-8")
    # a density-variant pair with DIFFERENT courtyards -> NOT a duplicate
    (fp / "SOT_X-L.kicad_mod").write_text(
        '(footprint "SOT_X-L" (layer F.Cu) (pad 1 smd rect (at 0 0)) (fp_line (start -1 -1)))',
        encoding="utf-8")
    (fp / "SOT_X-M.kicad_mod").write_text(
        '(footprint "SOT_X-M" (layer F.Cu) (pad 1 smd rect (at 0 0)) (fp_line (start -2 -2)))',
        encoding="utf-8")
    dups = LM.find_duplicate_footprints(cfg)
    assert dups == [["PARTA", "PARTA_COPY"]]          # only the true duplicate pair


def test_dedupe_footprint_library_removes_only_true_duplicates(tmp_path):
    import LibraryManager as LM
    from types import SimpleNamespace
    fp = tmp_path / "fps"; fp.mkdir()
    cfg = {"FootprintLib": str(fp)}
    for name in ("KEEP", "DUP1", "DUP2"):
        (fp / f"{name}.kicad_mod").write_text(
            f'(footprint "{name}" (layer F.Cu) (pad 1 smd rect (at 0 0)))', encoding="utf-8")
    # density variants (distinct geometry) must survive untouched
    (fp / "V-L.kicad_mod").write_text(
        '(footprint "V-L" (layer F.Cu) (pad 1 smd rect (at 3 0)))', encoding="utf-8")
    log = SimpleNamespace(write=lambda m: None)
    removed = LM.dedupe_footprint_library(cfg, log)
    assert removed == 2                               # two of the three identical files dropped
    remaining = sorted(p.stem for p in fp.glob("*.kicad_mod"))
    assert "V-L" in remaining                         # variant kept
    # exactly one of the identical trio survives
    survivors = [s for s in remaining if s in ("KEEP", "DUP1", "DUP2")]
    assert len(survivors) == 1



def test_summarize_dedupe_footprints():
    from ui.features import library as L
    assert L._summarize_dedupe_footprints(2) == \
        "Deduplicate Footprints: removed 2 duplicate footprints."
    assert L._summarize_dedupe_footprints(1) == \
        "Deduplicate Footprints: removed 1 duplicate footprint."
    assert "no true duplicates" in L._summarize_dedupe_footprints(0)
