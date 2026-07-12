"""kit composition builders: render + invariants (one primary per page, etc.)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget, QLabel  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.kit as kit  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])
def _destroy(w): sip.delete(w)

def test_page_builds_with_title_and_body():
    p = kit.page("Demo", body=[kit.detail("Part", [("Part Number", "R1"), ("Value", "10k")])])
    assert isinstance(p, QWidget)
    texts = [l.text() for l in p.findChildren(QLabel)]
    assert "Demo" in texts and "Part Number" in texts and "10k" in texts
    _destroy(p)

def test_page_allows_exactly_one_primary():
    a = kit.action("Save", lambda: None, kind="primary")
    b = kit.action("Cancel", lambda: None, kind="ghost")
    p = kit.page("Demo", actions=[a, b])       # one primary — ok
    assert isinstance(p, QWidget)
    _destroy(p)

def test_page_rejects_two_primaries():
    a = kit.action("Save", lambda: None, kind="primary")
    b = kit.action("Build", lambda: None, kind="primary")
    with pytest.raises(ValueError):
        kit.page("Demo", actions=[a, b])

def test_menu_button_collapses_a_family_of_actions():
    # W.menu_button folds a row of related buttons into ONE button + a menu; each
    # entry carries its own description, and triggering it fires the callback.
    from PyQt5.QtWidgets import QPushButton
    fired = []
    b = W.menu_button("Save File", [
        ("Card BOM (CSV)", lambda: fired.append("bom"), "Write the card BOM as CSV"),
        ("KiCad Netlist", lambda: fired.append("net"), "Write the socket netlist"),
        None,                                        # a separator
        ("Pin-Map SVG", lambda: fired.append("svg"), "Render the pin-map to SVG"),
    ], tip="Save one export file")
    assert isinstance(b, QPushButton)
    assert b.text().startswith("Save File")          # the trailing ▾ marks it a menu
    assert b.toolTip() == "Save one export file"
    acts = [a for a in b._menu.actions() if not a.isSeparator()]
    assert [a.text() for a in acts] == ["Card BOM (CSV)", "KiCad Netlist", "Pin-Map SVG"]
    assert all(a.toolTip() for a in acts)            # every entry describes itself
    assert any(a.isSeparator() for a in b._menu.actions())
    acts[2].trigger()                                # triggering the menu entry fires its callback
    assert fired == ["svg"]
    _destroy(b)


def test_kit_menu_button_rejects_a_primary_action():
    # The single accent lives in the ▶ flow, so a menu of secondaries takes no primary.
    ok = kit.menu_button("More", [kit.action("A", lambda: None), kit.action("B", lambda: None)])
    from PyQt5.QtWidgets import QPushButton
    assert isinstance(ok, QPushButton)
    _destroy(ok)
    with pytest.raises(ValueError):
        kit.menu_button("More", [kit.action("A", lambda: None, kind="primary")])


def test_section_has_title_and_child():
    s = kit.section("Sourcing", QLabel("body"))
    texts = [l.text() for l in s.findChildren(QLabel)]
    assert "Sourcing" in texts and "body" in texts
    _destroy(s)

def test_state_empty_and_loading_and_error():
    e = kit.state("empty", "Nothing Here", glyph="search")
    lo = kit.state("loading", "Loading")
    er = kit.state("error", "It Broke", glyph="alert")
    for w in (e, lo, er):
        assert w is not None
    # loading shows skeleton blocks
    assert lo.findChildren(W.Skeleton)
    _destroy(e); _destroy(lo); _destroy(er)

def test_async_region_renders_synchronously_offscreen():
    # offscreen run_populate is synchronous, so the rendered result is present immediately
    r = kit.async_region(lambda: [1, 2, 3], lambda data: W.body(f"{len(data)} rows"))
    assert any("3 rows" in l.text() for l in r.findChildren(QLabel))
    _destroy(r)

def test_async_region_error_path_with_no_services_does_not_raise():
    # With ctx=None (the default) there is no async bridge; a raising compute must render
    # the error state inline, NOT AttributeError on ctx.services (run_populate touches it
    # on every branch). This is the real-app / error path the happy test above never hits.
    r = kit.async_region(lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                         lambda data: W.body("should not render"))
    texts = [l.text() for l in r.findChildren(QLabel)]
    assert any("Could Not Load" in t for t in texts)
    assert not any("should not render" in t for t in texts)
    _destroy(r)

def test_async_region_bare_ctx_without_services_runs_inline():
    # A ctx object that lacks a usable `services` must also fall back to inline compute.
    bare = type("Ctx", (), {"services": None})()
    r = kit.async_region(lambda: [1], lambda data: W.body(f"{len(data)} inline"),
                         ctx=bare)
    assert any("1 inline" in l.text() for l in r.findChildren(QLabel))
    _destroy(r)

def test_stat_strip_uses_stat_scale():
    s = kit.stat_strip([("64", "Positions"), ("11", "Channels")])
    texts = [l.text() for l in s.findChildren(QLabel)]
    assert "64" in texts and "Positions" in texts
    _destroy(s)

def test_stat_strip_bare_count_stays_txt1():
    # A bare count has no unit token -> single txt1 label, no footnote split.
    s = kit.stat_strip([("64", "Positions")])
    labels = [l for l in s.findChildren(QLabel)]
    magnitude = next(l for l in labels if l.text() == "64")
    # stat scale is semibold+mono; the footnote scale is not -> distinct fonts.
    from ui import theme as _T
    assert magnitude.font().pointSizeF() == _T.scale_font("stat").pointSizeF()
    # No stray label whose text is an empty unit token.
    assert "" not in [l.text() for l in labels]
    _destroy(s)

def test_stat_strip_splits_unit_to_footnote_tier():
    # design-rules §"Stat strip": units pushed to txt3/footnote so a spec reads distinctly.
    from ui import theme as _T
    s = kit.stat_strip([("±25 mA", "Drive"), ("3.3 V", "Rail")])
    texts = [l.text() for l in s.findChildren(QLabel)]
    # Magnitude and unit live in SEPARATE labels.
    assert "±25" in texts and "mA" in texts
    assert "3.3" in texts and "V" in texts
    # No label carries the joined "±25 mA" string any more.
    assert "±25 mA" not in texts
    labels = {l.text(): l for l in s.findChildren(QLabel)}
    # The magnitude is at stat scale; the unit at the quieter footnote scale.
    assert labels["±25"].font().pointSizeF() == _T.scale_font("stat").pointSizeF()
    assert labels["mA"].font().pointSizeF() == _T.scale_font("footnote").pointSizeF()
    assert labels["mA"].font().pointSizeF() < labels["±25"].font().pointSizeF()
    _destroy(s)

def test_split_magnitude_unit_cases():
    f = kit._split_magnitude_unit
    assert f("64") == ("64", "")
    assert f("±25 mA") == ("±25", "mA")
    assert f("3.3 V") == ("3.3", "V")
    assert f("1,024") == ("1,024", "")
    assert f("100 kΩ") == ("100", "kΩ")
    assert f("1.5e3 Hz") == ("1.5e3", "Hz")
    assert f("N/A") == ("N/A", "")   # non-numeric passes through whole
    assert f("<10 µs") == ("<10", "µs")

def test_legend_shows_every_label():
    lg = kit.legend([("Net Colour", [("power", "Power"), ("ground", "Ground")])])
    texts = [l.text() for l in lg.findChildren(QLabel)]
    assert "Power" in texts and "Ground" in texts and "Net Colour" in texts
    _destroy(lg)

def test_custom_passes_widget_through():
    inner = QLabel("bespoke")
    w = kit.custom(inner)
    assert inner in w.findChildren(QLabel) or w is inner
    _destroy(w)

def _pump():
    # deliverer for deleteLater/destroyed callbacks queued on the event loop
    _APP.processEvents()
    _APP.processEvents()

@pytest.mark.parametrize("build", [
    lambda: kit.stat_strip([("±25 mA", "Drive"), ("64", "Positions")]),
    lambda: kit.legend([("Net Colour", [("power", "Power"), ("ground", "Ground")])]),
    lambda: kit.detail("Part", [("Part Number", "R1"), ("Value", "10k")]),
    lambda: kit.state("empty", "Nothing Here", glyph="search"),
    lambda: kit.async_region(lambda: [1, 2, 3], lambda d: W.body(f"{len(d)} rows")),
])
def test_restyler_registry_stable_across_build_rebuild(build):
    # A restyler registered inside a rebuildable builder MUST carry an owner so it
    # auto-unregisters on destroy; otherwise _RESTYLERS grows every rebuild and each
    # later theme toggle re-runs more dead closures (SHELL-06). Build/destroy twice and
    # assert the registry returns to its baseline length both times.
    import gc
    _pump(); gc.collect(); _pump()     # settle PRIOR tests' dying widgets before baselining
    W._prune_restylers()               # drop is lazy now — prune so the baseline excludes prior dead
    baseline = len(W._RESTYLERS)
    for _ in range(2):
        w = build()
        assert len(W._RESTYLERS) > baseline   # it did register colour-bearing widgets
        _destroy(w)
        _pump()
        W._prune_restylers()                   # drop is lazy now (weakref, not destroyed.connect)
        assert len(W._RESTYLERS) == baseline   # ...and released every one on prune

def test_tabbed_page_builds_panels():
    tp = kit.tabbed_page("Demo", [("A", lambda c: W.body("a")), ("B", lambda c: W.body("b"))])
    assert tp is not None
    _destroy(tp)


# ── kit.panes — the reusable list·center·detail splitter ──────────────────────
def test_panes_builds_splitter_with_sections():
    from PyQt5.QtWidgets import QSplitter
    a, b, c = QWidget(), QWidget(), QWidget()
    sp = kit.panes([a, b, c], sizes=[240, 600, 320])
    assert isinstance(sp, QSplitter)
    assert sp.count() == 3
    assert sp.objectName() == "panes"
    _destroy(sp)

def test_panes_ends_collapse_center_holds():
    # default: the two ends may collapse to zero; the center working pane never does
    sp = kit.panes([QWidget(), QWidget(), QWidget()])
    assert sp.isCollapsible(0) and sp.isCollapsible(2)
    assert not sp.isCollapsible(1)
    _destroy(sp)

def test_panes_min_width_floors_a_pane():
    sp = kit.panes([QWidget(), QWidget()], min_widths=[200, 0])
    assert sp.widget(0).minimumWidth() == 200
    _destroy(sp)

def test_panes_explicit_collapsible_override():
    sp = kit.panes([QWidget(), QWidget()], collapsible=[False, False])
    assert not sp.isCollapsible(0) and not sp.isCollapsible(1)
    _destroy(sp)

def test_panes_widths_persist_and_restore(monkeypatch):
    import LibraryManager as LM
    store = {}
    monkeypatch.setattr(LM, "read_setting", lambda k, d=None, **kw: store.get(k, d))
    monkeypatch.setattr(LM, "write_setting",
                        lambda k, v, **kw: (store.__setitem__(k, v), True)[1])
    # build with a key, drag a handle -> the layout saves
    sp = kit.panes([QWidget(), QWidget(), QWidget()], key="demo", sizes=[100, 200, 100])
    sp.splitterMoved.emit(150, 1)                 # simulate a user drag
    assert "Panes.demo" in store and store["Panes.demo"]
    saved = store["Panes.demo"]
    _destroy(sp)
    # a fresh build under the same key restores (reads) the saved state without error
    sp2 = kit.panes([QWidget(), QWidget(), QWidget()], key="demo")
    assert store["Panes.demo"] == saved            # restore path read it, didn't clobber
    _destroy(sp2)
