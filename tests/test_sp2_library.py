import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _cfg(tmp_path):
    """A minimal library cfg pointing at real files under tmp_path."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "R_0402" (property "Footprint" "MyFootprints:R_0402" (id 2)))\n'
        '  (symbol "2N7002" (property "Footprint" "MyFootprints:SOT-23" (id 2)))\n'
        ')\n'
    )
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "R_0402.kicad_mod").write_text('(footprint "R_0402")')
    mdl = tmp_path / "models"; mdl.mkdir()
    (mdl / "SOT-23.step").write_bytes(b"ISO-10303-21;")
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl)}


def test_symbol_block_for_returns_named_block(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    block = P.symbol_block_for(cfg, "R_0402")
    assert block is not None and "R_0402" in block
    assert P.symbol_block_for(cfg, "NoSuchSymbol") is None


def test_footprint_and_model_paths(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    fp = P.footprint_path_for(cfg, {"footprint": "R_0402"})
    assert fp is not None and fp.name == "R_0402.kicad_mod" and fp.exists()
    assert P.footprint_path_for(cfg, {"footprint": None}) is None
    mp = P.model_path_for(cfg, {"model": "SOT-23.step"})
    assert mp is not None and mp.name == "SOT-23.step" and mp.exists()
    assert P.model_path_for(cfg, {"model": None}) is None


def test_resolve_model_render_none_for_missing(tmp_path):
    from ui.features import library_preview as P
    assert P.resolve_model_render(None) == ("none", None)
    assert P.resolve_model_render(tmp_path / "nope.step") == ("none", None)


def test_resolve_model_render_prefers_mesh_then_image(tmp_path, monkeypatch):
    from ui.features import library_preview as P
    import fp_render as R
    p = tmp_path / "m.step"; p.write_bytes(b"x")

    # mesh available -> ("mesh", (verts, faces))
    monkeypatch.setattr(R, "load_step_mesh", lambda _p: ([[0, 0, 0]], [[0, 0, 0]]))
    kind, payload = P.resolve_model_render(p)
    assert kind == "mesh" and payload == ([[0, 0, 0]], [[0, 0, 0]])

    # no mesh, static image available -> ("image", QImage)
    from PyQt5.QtGui import QImage
    img = QImage(4, 4, QImage.Format_ARGB32)
    monkeypatch.setattr(R, "load_step_mesh", lambda _p: (None, None))
    monkeypatch.setattr(R, "render_step_image", lambda _p, px=420: img)
    kind, payload = P.resolve_model_render(p)
    assert kind == "image" and payload is img

    # nothing -> ("none", None)
    monkeypatch.setattr(R, "render_step_image", lambda _p, px=420: None)
    assert P.resolve_model_render(p) == ("none", None)


def test_meshview_constructs_for_each_kind():
    from ui.features.library_preview import MeshView
    from PyQt5.QtGui import QImage

    mv = MeshView("mesh", ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]]))
    assert mv.interactive is True
    mv.grab()  # paints without raising

    img = QImage(8, 8, QImage.Format_ARGB32); img.fill(0)
    sv = MeshView("image", img)
    assert sv.interactive is False
    sv.grab()


def _fake_ctx(cfg):
    """A Context-like object with synchronous run_async (renders inline in tests)."""
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    from types import SimpleNamespace
    return SimpleNamespace(cfg=cfg, services=_Svc())


def test_partdetail_renders_real_previews(tmp_path, monkeypatch):
    """Done-callback happy path: monkeypatched renders return real images so the
    footprint caption is populated, confirming the populate(result, ok) path fires."""
    from ui.features import library_preview as P
    import fp_render
    from PyQt5.QtGui import QImage

    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)

    good_img = QImage(8, 8, QImage.Format_ARGB32)
    good_img.fill(0xFF0000FF)  # opaque blue — definitely not null

    monkeypatch.setattr(fp_render, "render_symbol_image",
                        lambda block: good_img)
    monkeypatch.setattr(fp_render, "render_footprint_image",
                        lambda path: good_img)
    monkeypatch.setattr(fp_render, "footprint_summary",
                        lambda path: {"pads": 2, "width_mm": 1.0, "height_mm": 0.5})

    det = P.PartDetail(ctx)
    row = {
        "name": "R_0402", "mpn": "R_0402",
        "manufacturer": "Yageo", "description": "Chip Resistor",
        "symbols": ["R_0402"], "footprint": "R_0402", "model": None,
    }
    det.show(row)

    # The footprint done-callback happy path should have populated the caption.
    # WS-A/LIB-14: the caption is unit-aware and trims trailing zeros (1.0 -> "1").
    # It now LEADS with the footprint name so the card says which footprint it is.
    cap = det._fp.caption_text()
    assert cap != "", f"Expected a non-empty footprint caption, got: {cap!r}"
    assert cap == "R_0402 · 2 Pads · 1 × 0.5 mm", f"Unexpected caption: {cap!r}"
    det.grab()  # must not raise


def test_partdetail_show_populates_and_clears(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    # a real grouped-style row
    row = {"name": "R_0402", "mpn": "R_0402", "manufacturer": "Yageo",
           "description": "Chip Resistor", "datasheet": None,
           "footprint": "R_0402", "symbols": ["R_0402"], "model": None,
           "has_symbol": True, "has_footprint": True, "has_model": False}
    det.show(row)
    assert det._current == row        # current row tracked for Re-link
    det.grab()                       # renders without raising
    det.show(None)                   # clearing is safe
    assert det._current is None       # cleared so Re-link can't act on a stale row
    det.grab()


def test_partslist_filter_and_select():
    from ui.features.library_preview import PartsList
    rows = [
        {"name": "R_0402", "mpn": "R_0402", "manufacturer": "Yageo",
         "has_footprint": True, "has_model": True, "dangling": False},
        {"name": "2N7002", "mpn": "2N7002", "manufacturer": "onsemi",
         "has_footprint": True, "has_model": False, "dangling": False},
    ]
    picked = []
    lst = PartsList(rows, on_select=picked.append)
    assert picked and picked[-1]["mpn"] == "R_0402"      # first row auto-selected
    assert lst.visible_count() == 2
    lst.filter("2n7")
    assert lst.visible_count() == 1
    assert picked[-1]["mpn"] == "2N7002"
    lst.filter("")
    assert lst.visible_count() == 2
    assert picked[-1]["mpn"] == "R_0402"


def test_parts_panel_builds_master_detail(tmp_path):
    from ui.features import library as L
    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    panel = L._parts_panel(ctx, None)
    assert hasattr(panel, "parts_list") and hasattr(panel, "detail")
    assert panel.parts_list.visible_count() >= 1
    panel.grab()


def test_enrich_dry_run_then_apply(tmp_path, monkeypatch):
    from ui.features import library as L
    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    calls = []
    import LibraryManager as LM
    monkeypatch.setattr(LM, "enrich_library",
                        lambda c, lookup, dry_run=True, **k: (
                            calls.append(dry_run) or
                            {"changes": [{"symbol": "R_0402"}], "written": not dry_run,
                             "symbols": 1, "looked_up": 1}))
    applied = L._enrich_from_mpn(ctx, lookup=lambda m: None, apply=False)
    assert calls == [True] and applied["changes"]
    L._enrich_from_mpn(ctx, lookup=lambda m: None, apply=True)
    assert calls == [True, False]     # apply re-runs with dry_run=False


def test_scan_corrupt_reports(tmp_path, monkeypatch):
    # The corrupt-file scan (now a Maintenance workbench report) surfaces the count
    # AND each offender with its reason - the same behaviours the old helper proved.
    from ui.features import library as L
    cfg = _cfg(tmp_path); cfg["RepoRoot"] = str(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(L.ND, "find_corrupt_kicad_files",
                        lambda root: [(str(tmp_path / "bad.kicad_sym"), "unbalanced parens")])
    host = L._maintenance_workbench(ctx)
    host._scan_corrupt()
    assert any("1 corrupt" in m for m in ctx.services.logs)       # count reported
    assert any("bad.kicad_sym" in m and "unbalanced" in m for m in ctx.services.logs)  # per-file line


def test_scan_corrupt_empty_is_clean(tmp_path, monkeypatch):
    from ui.features import library as L
    cfg = _cfg(tmp_path); cfg["RepoRoot"] = str(tmp_path)
    ctx = _fake_ctx(cfg)
    monkeypatch.setattr(L.ND, "find_corrupt_kicad_files", lambda root: [])
    host = L._maintenance_workbench(ctx)
    host._scan_corrupt()
    assert any("No corrupt" in m for m in ctx.services.logs)


def test_apply_model_override_persists(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path); cfg["Libs"] = str(tmp_path)
    P.apply_model_override(cfg, "R_0402", "SOT-23.step")
    import LibraryManager as LM
    ov = LM.load_group_overrides(cfg)
    assert ov.get("model", {}).get("R_0402") == "SOT-23.step"


def test_netclass_profile_selector_rebuilds(monkeypatch):
    from ui.features import projects as PJ
    from types import SimpleNamespace
    ctx = SimpleNamespace(cfg={}, services=SimpleNamespace(log=lambda *a: None))
    panel = PJ._netclass_panel(ctx, None)
    # the profile picker (now a dropdown of profiles) must be present
    assert getattr(panel, "_profile_seg", None) is not None
    # switching profiles must not raise
    panel._profile_seg.setCurrentIndex(0)


def test_partslist_row_shows_humanized_technical_and_warn_triangle():
    # Each row is a two-line card — humanized name over the technical name — with a
    # red warning triangle on any incomplete/dangling part (mockup .rowwarn), and NO
    # leading dot (the dot was swapped for the silent-complete / warn-on-gap convention).
    from ui.features.library_preview import PartsList
    from PyQt5.QtWidgets import QLabel
    rows = [
        # missing datasheet + category → incomplete → the triangle must show.
        {"name": "1043_KEY", "mpn": "PTS645SM43", "description": "Tactile Switch 6mm SPST-NO",
         "manufacturer": "CUI", "has_symbol": True, "has_footprint": True,
         "has_model": True, "dangling": False},
    ]
    lst = PartsList(rows, on_select=lambda r: None)
    # item 0 may be a group header now; find the DATA row widget via the cache.
    w = lst._items[0][2]
    assert w is not None, "each row renders a custom two-line widget"
    sub = w.findChild(QLabel, "partRowTechnical")
    warn = w.findChild(QLabel, "partRowWarn")
    dot = w.findChild(QLabel, "partRowDot")
    # Names asserted via properties: the labels elide their rendered text.
    assert w.property("humanized") == "Tactile Switch 6mm SPST-NO"
    assert w.property("technical") == "PTS645SM43"
    assert sub.isVisibleTo(w), "technical name shown when it differs from humanized"
    assert dot is None, "the leading asset dot was retired in the mockup rebuild"
    assert warn is not None and not warn.pixmap().isNull(), "incomplete row must show the warning triangle"
    assert w.property("incomplete") is True


def test_partslist_row_hides_technical_when_equal_to_humanized():
    # No Mouser description and mpn == name: nothing to disambiguate, so the
    # second line is suppressed rather than repeating the same string.
    from ui.features.library_preview import PartsList
    from PyQt5.QtWidgets import QLabel
    rows = [{"name": "R_0402", "mpn": "R_0402", "has_symbol": True,
             "has_footprint": True, "has_model": True, "dangling": False}]
    lst = PartsList(rows, on_select=lambda r: None)
    w = lst._items[0][2]                 # the DATA row widget (item 0 may be a group header)
    sub = w.findChild(QLabel, "partRowTechnical")
    assert not sub.isVisibleTo(w), "technical line hidden when identical to humanized"


def test_partdetail_hero_shows_humanized_over_subline(tmp_path):
    # The detail header (mockup .cvhead) leads with the plain-words name and shows a
    # "MPN · Manufacturer" subline beneath it.
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    det.show({"name": "1043_KEY", "mpn": "PTS645SM43", "description": "Tactile Switch 6mm",
              "manufacturer": "CUI", "symbols": ["1043_KEY"], "footprint": None, "model": None})
    assert det._title.text() == "Tactile Switch 6mm"
    assert "PTS645SM43" in det._subline.text()
    assert "CUI" in det._subline.text()


def test_partdetail_subline_falls_back_when_no_mpn(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    det.show({"name": "R_0402", "mpn": "", "symbols": ["R_0402"],
              "footprint": None, "model": None})
    assert det._title.text() == "R_0402"
    assert det._subline.text() == "No Part Number · Unknown Maker"


def test_partdetail_sourcing_surfaces_all_mouser_fields(tmp_path):
    # LIB-04: the sourcing block shows the FULL Mouser field set (ROHS, category,
    # a product-page link, ...), not just lifecycle/stock/price. Also confirms the
    # 'Look Up'/'Refresh Sourcing' results are actually visible (LIB-06/07).
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QLabel
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    row = {"name": "U1", "mpn": "TPS2121RUXR", "symbols": ["U1"],
           "footprint": None, "model": None, "manufacturer": "TI"}
    det._src_cache["TPS2121RUXR"] = {
        "mpn": "TPS2121RUXR", "manufacturer": "Texas Instruments",
        "category": "Power Switch ICs", "lifecycle": "Active", "rohs": "RoHS Compliant",
        "stock": 4200, "lead_time": "12 Days", "unit_price": 1.23,
        "mouser_pn": "595-TPS2121RUXR", "url": "https://mouser.com/p/tps2121",
        "datasheet": "https://ti.com/ds.pdf", "description": "Power Mux",
        "suggested_replacement": None,
    }
    det.show(row)
    texts = " | ".join(lab.text() for lab in det.findChildren(QLabel))
    assert "ROHS" in texts.upper(), "ROHS status must be surfaced"
    assert "RoHS Compliant" in texts
    assert "Power Switch ICs" in texts, "Mouser category must be surfaced"
    assert "12 Days" in texts, "lead time"
    assert "4,200" in texts, "stock formatted with separators"
    assert "mouser.com/p/tps2121" in texts, "Mouser product-page link surfaced"


def test_partdetail_shows_price_break_ladder(tmp_path):
    # The volume price-break ladder is surfaced per part so volume pricing is visible,
    # not just the qty-1 unit price.
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QLabel
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    row = {"name": "C1", "mpn": "GRM155", "symbols": ["C1"], "footprint": None,
           "model": None, "manufacturer": "Murata"}
    det._src_cache["GRM155"] = {
        "mpn": "GRM155", "lifecycle": "Active", "stock": 1000, "unit_price": 0.0069,
        "price_breaks": [{"qty": 1, "price": 0.0069}, {"qty": 10, "price": 0.0055},
                         {"qty": 100, "price": 0.0048}],
    }
    det.show(row)
    texts = " | ".join(lab.text() for lab in det.findChildren(QLabel))
    assert "Price Breaks" in texts, "volume price-break section present"
    assert "10+" in texts and "100+" in texts, "each break quantity is a rung"
    assert "0.0048" in texts, "the deepest volume price is shown"


def test_partdetail_no_ladder_for_single_break(tmp_path):
    # A single-rung ladder is just the unit price — no separate volume block (stay quiet).
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QLabel
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    row = {"name": "C1", "mpn": "X", "symbols": ["C1"], "footprint": None, "model": None}
    det._src_cache["X"] = {"mpn": "X", "unit_price": 1.0,
                           "price_breaks": [{"qty": 1, "price": 1.0}]}
    det.show(row)
    texts = " | ".join(lab.text() for lab in det.findChildren(QLabel))
    assert "Price Breaks" not in texts


def test_partdetail_link_is_provider_aware(tmp_path):
    # When LCSC is the source, the product-page link points at LCSC, not Mouser.
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QLabel
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    row = {"name": "C1", "mpn": "X", "symbols": ["C1"], "footprint": None, "model": None}
    det._src_cache["X"] = {"mpn": "X", "source": "LCSC", "lifecycle": "Active",
                           "url": "https://www.lcsc.com/product-detail/C1.html"}
    det.show(row)
    texts = " | ".join(lab.text() for lab in det.findChildren(QLabel))
    assert "View On LCSC" in texts
    assert "View On Mouser" not in texts


def test_apply_autofill_writes_mapped_properties(tmp_path, monkeypatch):
    # LIB-05: _apply_autofill persists each planned row-key to its symbol property
    # and refreshes the detail to the new values, in a single commit.
    from ui.features import library_preview as P
    import LibraryManager as L
    cfg = _cfg(tmp_path); ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    det._current = {"name": "U1", "mpn": "TPS2121RUXR", "symbols": ["U1"],
                    "footprint": None, "model": None}
    writes = []
    monkeypatch.setattr(L, "set_library_symbol_property",
                        lambda cfg, names, prop, val: (writes.append((prop, val)), True)[1])
    commits = []
    monkeypatch.setattr(L, "git_commit_push", lambda *a, **k: commits.append(a))
    fetched = {"mpn": "TPS2121RUXR", "manufacturer": "Texas Instruments",
               "description": "Power Mux"}
    det._apply_autofill(fetched, {"manufacturer": "Texas Instruments",
                                  "description": "Power Mux"})
    props = dict(writes)
    assert props["MANUFACTURER"] == "Texas Instruments"
    assert props["Description"] == "Power Mux"
    assert len(commits) == 1, "one commit for the whole autofill batch"
    assert det._current["manufacturer"] == "Texas Instruments"


# ── datasheet surfaced in the Component Fields (mockup moved it out of the header) ──

def test_partdetail_datasheet_surfaced_in_fields(tmp_path):
    from ui.features import library_preview as P
    from PyQt5.QtWidgets import QLabel, QLineEdit
    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)

    def field_texts():
        return " ".join([l.text() for l in det.findChildren(QLabel)]
                        + [e.text() for e in det.findChildren(QLineEdit)])

    row_with_ds = {"name": "R_0402", "mpn": "R_0402", "datasheet": "https://example.com/ds.pdf",
                   "manufacturer": None, "description": None,
                   "footprint": None, "symbols": ["R_0402"], "model": None}
    det.show(row_with_ds)
    assert "https://example.com/ds.pdf" in field_texts(), "datasheet URL must be surfaced in the fields"

    row_no_ds = dict(row_with_ds); row_no_ds["datasheet"] = None
    det.show(row_no_ds)
    assert "https://example.com/ds.pdf" not in field_texts()

    det.show(None)   # clear must not raise
    assert det._title.text() == ""
