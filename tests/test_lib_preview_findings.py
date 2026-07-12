"""Regression tests for library_preview.py audit findings:

  * HTML escaping of datasheet + distributor URLs interpolated into rich-text QLabels.
  * Async preview render race — a late render callback for part A must NOT paint onto
    part B's card / stomp the cached dimension summaries once B is selected.
  * 3D-model drop-in commit message describes the library override actually written,
    not a footprint-level change that never hit disk.
  * SRC-04 daily-cap countdown at the library-preview lookup entry points — a capped
    key is reported with a countdown, not a dead-end "No match".
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QLabel, QPushButton
from PyQt5 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402
import nd_commit_msg as CM  # noqa: E402
import fp_render as R  # noqa: E402
from ui.features import library_preview as LP  # noqa: E402
from ui.features.library_preview import PartDetail, PartsList  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _ctx(tmp_path):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg={"Libs": str(tmp_path)}, services=_Svc(), bus=None)


def test_distributor_url_is_escaped_in_richtext(tmp_path):
    pd = PartDetail(_ctx(tmp_path))
    src = {"source": "Mouser", "url": "https://mouser.com/p?pn=A&r=B&z=\"q\"",
           "stock": 5, "unit_price": "$1.00"}
    body = pd._sourcing_body(src)
    links = [lab.text() for lab in body.findChildren(QLabel) if "href" in lab.text()]
    assert links, "expected a distributor link label"
    joined = " ".join(links)
    assert "&amp;" in joined
    assert "&quot;" in joined
    assert 'pn=A&r=B' not in joined
    sip.delete(pd)


# ── async render race ────────────────────────────────────────────────────────
def test_late_footprint_render_does_not_paint_wrong_card(tmp_path, monkeypatch):
    """Drive _render_footprint for row A while _current has already moved to row B:
    the stale callback must be a no-op (no caption, no _fp_summary stomp)."""
    pd = PartDetail(_ctx(tmp_path))
    row_a = {"mpn": "A", "name": "A", "footprint": "FP_A"}
    row_b = {"mpn": "B", "name": "B", "footprint": "FP_B"}

    monkeypatch.setattr(LP, "footprint_path_for", lambda cfg, row: Path("/does/not/matter"))
    monkeypatch.setattr(R, "render_footprint_image", lambda p: None)
    monkeypatch.setattr(R, "footprint_summary",
                        lambda p: {"pads": 99, "width_mm": 1.0, "height_mm": 2.0})

    # Simulate: A is the in-flight render, but the user already selected B.
    pd._current = row_b
    pd._fp_summary = None
    pd._render_footprint(row_a)                   # headless -> callback runs synchronously
    assert pd._fp_summary is None                 # A's dims did NOT overwrite the cache
    assert pd._fp.caption_text() == ""            # B's card was not captioned by A

    # Sanity: rendering the CURRENT row does caption + cache.
    pd._render_footprint(row_b)
    assert pd._fp_summary is not None
    assert "99 Pads" in pd._fp.caption_text()
    sip.delete(pd)


def test_late_symbol_render_does_not_paint_wrong_card(tmp_path, monkeypatch):
    pd = PartDetail(_ctx(tmp_path))
    row_a = {"mpn": "A", "name": "A", "symbols": ["SYM_A"]}
    row_b = {"mpn": "B", "name": "B", "symbols": ["SYM_B"]}
    monkeypatch.setattr(LP, "symbol_block_for", lambda cfg, name: "(symbol block)")

    painted = []
    monkeypatch.setattr(pd._sym, "set_image", lambda img: painted.append(img))
    monkeypatch.setattr(R, "render_symbol_image", lambda block: object())

    pd._current = row_b
    pd._render_symbol(row_a)                       # stale render for A
    assert painted == []                           # never painted onto B's symbol card

    pd._render_symbol(row_b)                        # current render paints
    assert len(painted) == 1
    sip.delete(pd)


# ── model drop-in writes the real footprint model line ───────────────────────
def _fp_dir(tmp_path):
    d = tmp_path / "MyFootprints.pretty"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_model_dropin_writes_footprint_line_not_just_override(tmp_path, monkeypatch):
    """The interactive drop-in must write the (model …) line into the footprint
    file — the same on-disk tie the ZIP-import / auto-assign paths produce — so
    has_model reflects real state and downstream (KiCad/BOM/auto_assign) sees it.
    The commit message describes a footprint-level add, matching the diff."""
    fp_dir = _fp_dir(tmp_path)
    fp_file = fp_dir / "FP_X.kicad_mod"
    fp_file.write_text('(footprint "FP_X" (layer "F.Cu")\n  (pad "1" smd rect)\n)\n',
                       encoding="utf-8")
    cfg = {"Libs": str(tmp_path), "FootprintLib": str(fp_dir),
           "ModelLib": str(tmp_path / "models")}
    pd = PartDetail(_ctx(tmp_path))
    pd._ctx.cfg = cfg
    pd._current = {"mpn": "P", "name": "P", "footprint": "FP_X", "symbols": ["S"]}

    monkeypatch.setattr(LM, "install_model_file", lambda cfg, path, log: "widget.step")
    # The override must NOT be the thing that carries the tie on the happy path.
    monkeypatch.setattr(LP, "apply_model_override",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("override must not be used when the footprint is writable")))
    captured = {}
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda cfg, log, msg: captured.setdefault("msg", msg))

    pd._dropin_model("/some/widget.step")          # headless -> job runs synchronously

    written = fp_file.read_text(encoding="utf-8")
    assert LM.footprint_has_model(written)                 # the model line hit disk
    assert '${MY3DMODELS}/widget.step' in written          # correct model path written
    assert captured.get("msg") == CM.add_model("widget.step", "FP_X")
    assert "add 3D model widget.step to FP_X" in captured["msg"]
    sip.delete(pd)


def test_model_dropin_falls_back_to_override_when_footprint_missing(tmp_path, monkeypatch):
    """When the footprint file can't be edited, keep the JSON-override tie AND an
    honest 'override' commit message that never claims a footprint-level add."""
    cfg = {"Libs": str(tmp_path), "FootprintLib": str(_fp_dir(tmp_path)),
           "ModelLib": str(tmp_path / "models")}
    pd = PartDetail(_ctx(tmp_path))
    pd._ctx.cfg = cfg
    pd._current = {"mpn": "P", "name": "P", "footprint": "NO_SUCH_FP", "symbols": ["S"]}

    monkeypatch.setattr(LM, "install_model_file", lambda cfg, path, log: "widget.step")
    override = {}
    monkeypatch.setattr(LP, "apply_model_override",
                        lambda cfg, fp, name: override.update({"fp": fp, "name": name}))
    captured = {}
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda cfg, log, msg: captured.setdefault("msg", msg))

    pd._dropin_model("/some/widget.step")
    assert override == {"fp": "NO_SUCH_FP", "name": "widget.step"}   # override tie applied
    msg = captured.get("msg", "")
    assert msg == "chore(lib): associate 3D model widget.step with NO_SUCH_FP (override)"
    assert "add 3D model" not in msg               # must not claim a footprint-level add
    sip.delete(pd)


def test_attach_model_to_footprint_is_idempotent(tmp_path):
    """Re-attaching the same model leaves the footprint byte-for-byte unchanged
    (no spurious diff/commit churn)."""
    fp_dir = _fp_dir(tmp_path)
    fp_file = fp_dir / "FP_Y.kicad_mod"
    fp_file.write_text('(footprint "FP_Y" (layer "F.Cu")\n)\n', encoding="utf-8")
    cfg = {"FootprintLib": str(fp_dir)}

    assert LP.attach_model_to_footprint(cfg, "FP_Y", "gizmo.step") is True
    first = fp_file.read_text(encoding="utf-8")
    assert LP.attach_model_to_footprint(cfg, "FP_Y", "gizmo.step") is True
    assert fp_file.read_text(encoding="utf-8") == first     # no churn on repeat

    # A footprint that doesn't exist -> False (caller falls back to the override).
    assert LP.attach_model_to_footprint(cfg, "GHOST", "gizmo.step") is False


# ── SRC-04 rate-limit countdown ──────────────────────────────────────────────
def test_lookup_reports_countdown_when_rate_limited(tmp_path, monkeypatch):
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda mpn: None                  # a capped lookup collapses to None
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: 3 * 3600 + 12 * 60)
    pd._current = {"mpn": "STM32", "name": "STM32", "symbols": []}

    pd._lookup_one({"mpn": "STM32", "name": "STM32", "symbols": []})
    logs = " ".join(pd._ctx.services.logs)
    assert "rate-limited" in logs
    assert "3h 12m" in logs
    assert "LCSC still works" in logs
    assert "No Mouser match" not in logs
    sip.delete(pd)


def test_lookup_reports_no_match_when_not_capped(tmp_path, monkeypatch):
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda mpn: None
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: None)
    pd._current = {"mpn": "NOPART", "name": "NOPART", "symbols": []}

    pd._lookup_one({"mpn": "NOPART", "name": "NOPART", "symbols": []})
    logs = " ".join(pd._ctx.services.logs)
    assert "No Mouser match for NOPART" in logs
    assert "rate-limited" not in logs
    sip.delete(pd)


def test_offer_autofill_reports_countdown_when_rate_limited(tmp_path, monkeypatch):
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda number: None
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: 45 * 60)
    pd._current = {"mpn": "X", "name": "X", "symbols": ["S"]}

    pd._offer_autofill("X")
    logs = " ".join(pd._ctx.services.logs)
    assert "rate-limited" in logs
    assert "45m" in logs
    sip.delete(pd)


# ── the Mouser actions live under one "Mouser ▾" menu, never duplicated ───────
def _mouser_menus(pd) -> list:
    return [b for b in pd.findChildren(QPushButton)
            if b.text().startswith("Mouser") and hasattr(b, "_menu")]


def _menu_entry_labels(b) -> set:
    return {a.text() for a in b._menu.actions() if not a.isSeparator()}


def test_mouser_menu_single_when_no_cached_sourcing(tmp_path):
    """A part with an MPN, a Mouser lookup configured, and NO cached sourcing renders
    exactly one 'Mouser ▾' menu — the empty-state CTA — never a second copy in the
    Sourcing header. The menu offers both catalog search and an exact-MPN refresh."""
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda mpn: None                  # a configured (non-None) lookup chain
    pd.show({"mpn": "STM32F103", "name": "STM32F103", "symbols": ["S"]})
    menus = _mouser_menus(pd)
    assert len(menus) == 1, f"expected exactly one Mouser menu, got {len(menus)}"
    assert {"Search Catalog…", "Refresh This Part's Data"} <= _menu_entry_labels(menus[0])
    sip.delete(pd)


def test_mouser_menu_single_when_cached_sourcing(tmp_path):
    """When sourcing already renders (cached), the single 'Mouser ▾' menu lives in the
    header — and there is no empty state to double it."""
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda mpn: None
    pd._src_cache["STM32F103"] = {"stock": 10, "unit_price": "$1.00", "lifecycle": "Active"}
    pd.show({"mpn": "STM32F103", "name": "STM32F103", "symbols": ["S"]})
    assert len(_mouser_menus(pd)) == 1
    # And the empty-state "Not Looked Up Yet" is NOT shown once we have data.
    labels = " ".join(lab.text() for lab in pd.findChildren(QLabel))
    assert "Not Looked Up Yet" not in labels
    sip.delete(pd)


def test_mouser_menu_has_no_refresh_without_mpn(tmp_path):
    """No MPN → nothing to refresh by exact part number, so the Mouser menu offers only
    catalog search (still one menu, no 'Refresh This Part's Data' entry)."""
    pd = PartDetail(_ctx(tmp_path))
    pd._lookup = lambda mpn: None
    pd.show({"mpn": "", "name": "loose", "symbols": ["S"]})
    menus = _mouser_menus(pd)
    assert len(menus) == 1
    entries = _menu_entry_labels(menus[0])
    assert "Search Catalog…" in entries
    assert "Refresh This Part's Data" not in entries
    sip.delete(pd)


# ── key column matches the 128px Detail rule ─────────────────────────────────
def test_identity_and_sourcing_key_width_is_128(tmp_path, monkeypatch):
    """design-rules §Detail fixes the definition-list key column at 128px. Assert
    both the identity list and the sourcing body pass 128 to W.dl."""
    from ui import widgets as W
    widths = []
    real_dl = W.dl
    monkeypatch.setattr(W, "dl", lambda pairs, key_width=136, row_gap=12: widths.append(key_width) or real_dl(pairs, key_width, row_gap))
    pd = PartDetail(_ctx(tmp_path))
    pd._build_identity({"mpn": "P", "name": "P", "symbols": ["S"], "description": "d"})
    body = pd._sourcing_body({"stock": 1, "unit_price": "$1.00"})
    assert widths, "expected W.dl to be called"
    assert all(w == 128 for w in widths), f"key widths were {widths}, expected all 128"
    del body
    sip.delete(pd)


# ── facet change preserves the selected part ─────────────────────────────────
def test_set_facet_preserves_selected_part_when_it_survives(tmp_path):
    """Toggling a health facet must keep the currently-open part focused when it
    survives the new filter — not snap the detail back to row 0."""
    def _complete(mpn):
        # Genuinely 8/8 under the strict passport: the three assets AND full identity
        # (real MPN + manufacturer + datasheet + description + category).
        return {"mpn": mpn, "name": mpn, "symbols": ["s" + mpn], "has_symbol": True,
                "has_footprint": True, "has_model": True, "footprint": "fp" + mpn,
                "has_real_mpn": True, "manufacturer": "ACME", "datasheet": "http://d",
                "description": "d", "category": "Misc"}

    selected = []
    # Two complete parts either side of an incomplete one. Selecting the SECOND
    # complete part means that after the "Complete" filter drops the incomplete
    # part, the survivor is at index 1 — so a preserve=False path (idx=0) would
    # snap to the FIRST complete part and this test would catch the regression.
    rows = [
        _complete("COMP_A"),
        {"mpn": "INCOMPLETE", "name": "INCOMPLETE", "symbols": ["sB"], "has_symbol": True,
         "has_footprint": False, "has_model": False, "manufacturer": "ACME"},
        _complete("COMP_B"),
    ]
    pl = PartsList(rows, lambda r: selected.append(r))
    # select COMP_B via its full-list index (group headers are interleaved now).
    target = next(r for r in pl._visible if r["mpn"] == "COMP_B")
    pl._list.setCurrentRow(pl._item_row_for(target))
    selected.clear()
    # Narrow to Complete: both COMP_A and COMP_B survive; COMP_B is now index 1.
    pl.set_facet("Complete")
    assert selected, "expected a selection callback after the facet change"
    assert selected[-1]["mpn"] == "COMP_B", (
        "facet change yanked the detail off the still-visible selected part")
    sip.delete(pl)


def test_set_facet_falls_back_to_first_when_selection_filtered_out(tmp_path):
    """When the selected part does NOT survive the new facet, fall back to row 0."""
    selected = []
    rows = [
        {"mpn": "COMPLETE", "name": "COMPLETE", "symbols": ["sA"], "has_symbol": True,
         "has_footprint": True, "has_model": True, "footprint": "fpA",
         "has_real_mpn": True, "manufacturer": "ACME", "datasheet": "http://d",
         "description": "d", "category": "Misc"},
        {"mpn": "INCOMPLETE", "name": "INCOMPLETE", "symbols": ["sB"], "has_symbol": True,
         "has_footprint": False, "has_model": False, "manufacturer": "ACME"},
    ]
    pl = PartsList(rows, lambda r: selected.append(r))
    target = next(r for r in pl._visible if r["mpn"] == "INCOMPLETE")
    pl._list.setCurrentRow(pl._item_row_for(target))
    selected.clear()
    pl.set_facet("Complete")                        # INCOMPLETE is filtered out
    assert selected[-1]["mpn"] == "COMPLETE"        # fell back to the sole survivor
    sip.delete(pl)


# ── drop-in gating on structural prerequisites ───────────────────────────────
def test_model_dropin_blocked_without_footprint(tmp_path):
    """A part with no footprint can't accept a 3D model: the 3D card names the
    prerequisite and the picker is inert (no no-op file dialog)."""
    pd = PartDetail(_ctx(tmp_path))
    pd.show({"mpn": "P", "name": "P", "symbols": ["S"]})   # symbol but no footprint
    assert pd._mdl._droppable() is False
    labels = " ".join(lab.text() for lab in pd._mdl.findChildren(QLabel))
    assert "Add A Footprint First" in labels
    # The blocked empty state offers no Add/Replace button.
    add_btns = [b for b in pd._mdl.findChildren(QPushButton)
                if b.text() in ("Add 3D Model", "Replace") and b.isVisible()]
    assert add_btns == []
    # A picker call is a hard no-op even if reached programmatically.
    called = []
    pd._mdl._on_file = lambda p: called.append(p)
    pd._mdl._pick()
    assert called == []
    sip.delete(pd)


def test_footprint_dropin_blocked_without_symbol(tmp_path):
    """A footprint-only part (no symbol) can't accept a footprint drop-in — nothing
    to link it to — so the footprint card names 'Add A Symbol First'."""
    pd = PartDetail(_ctx(tmp_path))
    pd.show({"mpn": "P", "name": "P", "symbols": []})       # no symbol
    assert pd._fp._droppable() is False
    labels = " ".join(lab.text() for lab in pd._fp.findChildren(QLabel))
    assert "Add A Symbol First" in labels
    sip.delete(pd)


def test_model_dropin_enabled_with_footprint(tmp_path):
    """When the prerequisite is present, the drop-in is live and the empty state
    offers the Add action again."""
    pd = PartDetail(_ctx(tmp_path))
    pd.show({"mpn": "P", "name": "P", "symbols": ["S"], "footprint": "fpX"})
    assert pd._mdl._droppable() is True
    add_btns = [b for b in pd._mdl.findChildren(QPushButton)
                if b.text() == "Add 3D Model"]
    assert add_btns, "expected the Add 3D Model picker when a footprint exists"
    sip.delete(pd)


# ── symbol drop-in feedback ──────────────────────────────────────────────────
def test_symbol_dropin_no_misleading_rescan_and_reshows(tmp_path, monkeypatch):
    """The symbol drop-in must NOT tell the user to 're-scan' (the code already
    rescans via _on_changed), and it re-shows the current row afterward so a linked
    symbol renders immediately — matching the footprint/model drop-ins."""
    changed = []
    pd = PartDetail(_ctx(tmp_path), on_changed=lambda: changed.append(True))
    pd._current = {"mpn": "P", "name": "P", "symbols": ["S"]}

    monkeypatch.setattr(LM, "install_symbol_file", lambda cfg, path, log: True)
    monkeypatch.setattr(LM, "git_commit_push", lambda cfg, log, msg: None)
    shows = []
    monkeypatch.setattr(pd, "show", lambda row: shows.append(row))

    pd._dropin_symbol("/some/widget.kicad_sym")     # headless -> runs synchronously
    logs = " ".join(pd._ctx.services.logs)
    assert "re-scan" not in logs.lower()            # no misleading instruction
    assert "Symbol merged" in logs
    assert changed == [True]                         # the rescan ran
    assert shows and shows[-1] is pd._current        # re-shown so it renders now
    sip.delete(pd)


# ── PartsList perf: build row widgets ONCE, hide/show on filter (lib_preview:1175) ──
def _perf_rows():
    def _r(mpn, complete):
        # A "complete" row is genuinely 8/8 (assets + full identity); the incomplete
        # one drops only the 3D model, so a Complete facet hides exactly it.
        base = {"mpn": mpn, "name": mpn, "symbols": ["s" + mpn], "has_symbol": True,
                "has_footprint": True, "footprint": "fp" + mpn, "has_real_mpn": True,
                "manufacturer": "ACME", "datasheet": "http://d", "description": "d",
                "category": "Misc"}
        base["has_model"] = complete
        return base
    # Two complete parts and one missing-model part, so a facet toggle both hides
    # and re-shows rows.
    return [_r("AAA", True), _r("BBB", False), _r("CCC", True)]


def test_facet_toggle_reuses_row_widgets_not_rebuild(tmp_path):
    """A facet change (and a settled search) must HIDE/SHOW the pre-built row
    widgets, never tear them down and rebuild — so the identical widget objects
    survive filtering (the lib_preview:1175 optimization)."""
    pl = PartsList(_perf_rows(), on_select=lambda r: None)
    # Capture the widget object identity for every row after the initial build.
    before = {r["mpn"]: pl._list.itemWidget(it) for r, it, _w in pl._items}
    assert len(before) == 3
    assert pl.visible_count() == 3

    pl.set_facet("Complete")                         # BBB (no model) is filtered out
    assert pl.visible_count() == 2
    assert {r["mpn"] for r in pl._visible} == {"AAA", "CCC"}
    # The list still holds every row's item — filtered rows are HIDDEN, not removed.
    assert len(pl._items) == 3
    hidden = {r["mpn"]: it.isHidden() for r, it, _w in pl._items}
    assert hidden == {"AAA": False, "BBB": True, "CCC": False}

    pl.set_facet("All")                              # BBB comes back
    assert pl.visible_count() == 3
    # SAME widget objects throughout — no rebuild happened on either facet change.
    after = {r["mpn"]: pl._list.itemWidget(it) for r, it, _w in pl._items}
    for mpn, w in before.items():
        assert after[mpn] is w, f"{mpn} row widget was rebuilt on a facet toggle"
    sip.delete(pl)


def test_search_filter_hides_shows_same_widgets(tmp_path):
    """The immediate `filter()` path (and the debounced search behind it) also
    hides/shows the pre-built widgets rather than rebuilding them, and keeps the
    selection landing on the surviving row."""
    picked = []
    pl = PartsList(_perf_rows(), on_select=lambda r: picked.append(r and r.get("mpn")))
    before = {r["mpn"]: pl._list.itemWidget(it) for r, it, _w in pl._items}

    pl.filter("bbb")
    assert pl.visible_count() == 1
    assert pl._visible[0]["mpn"] == "BBB"
    assert picked[-1] == "BBB"                        # selection moved to the survivor
    # BBB's widget is the same object that existed before the search.
    after = {r["mpn"]: pl._list.itemWidget(it) for r, it, _w in pl._items}
    assert after["BBB"] is before["BBB"]

    pl.filter("")                                    # clears -> all three shown again
    assert pl.visible_count() == 3
    assert picked[-1] == "AAA"                        # cleared search resets to row 0
    sip.delete(pl)


def test_set_rows_preserves_selection_across_widget_rebuild(tmp_path):
    """set_rows DOES rebuild the widgets (row content can change), but the selected
    part must stay selected — the perf refactor keeps preserve=True working."""
    picked = []
    rows = _perf_rows()
    pl = PartsList(rows, on_select=lambda r: picked.append(r and r.get("mpn")))
    # select CCC via its full-list index (group headers are interleaved now)
    target = next(r for r in pl._visible if r["mpn"] == "CCC")
    pl._list.setCurrentRow(pl._item_row_for(target))
    assert picked[-1] == "CCC"
    # rescan: CCC now missing its model; identity (symbol key) is unchanged
    fresh = [dict(r) for r in rows]
    fresh[2] = dict(fresh[2]); fresh[2]["has_model"] = False
    pl.set_rows(fresh)
    assert picked[-1] == "CCC", "set_rows must stay on the selected part after rebuild"
    assert pl.visible_count() == 3
    sip.delete(pl)


