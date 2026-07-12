"""Library Sourcing Health — Phase-2 convergence: the tab is a `kit.workbench` whose
verdict is the structural health count, whose detail carries the per-category findings
and the opt-in Mouser sweep, and whose ▶ Fix All From Library applies every safe
structural completion (create stub / link footprint / link model) the library itself
can satisfy, previews first, then commits once.

The sweep hits the live catalog, so it must never run at tab-build time (that would
hang headless); it runs only when the handler fires. These tests drive the handlers
with a stubbed report — no network. The Fix All tests run the REAL engine on a
fixture library.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QTableWidget, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402
from ui.features import library as LIB  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _ctx(cfg=None):
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

    return SimpleNamespace(cfg=cfg if cfg is not None else {}, services=_Svc(),
                           theme=None, bus=_Bus())


def _libcfg(tmp_path):
    """A real fixture library: U1 complete; SOT_PART a symbol/footprint pair the
    engine can join by name (link_footprint + link_model, both safe)."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "U1" (property "Value" "U1" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_A" (id 2))'
        ' (property "MANUFACTURER" "ACME" (id 3)) (pin 1))\n'
        '  (symbol "SOT_PART" (property "Value" "SOT_PART" (id 1)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "FP_A.kicad_mod").write_text(
        '(footprint "FP_A" (model ${MY3DMODELS}/FP_A.step))', encoding="utf-8")
    (fp / "SOT_PART.kicad_mod").write_text('(footprint "SOT_PART")', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    (mdl / "FP_A.step").write_bytes(b"ISO-10303-21;")
    (mdl / "SOT_PART.step").write_bytes(b"ISO-10303-21;")
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}


_REPORT = {
    "counts": {"parts": 4, "found": 3, "not_found": 1, "on_mouser": 3,
               "not_on_mouser": 0, "obsolete_nrnd": 1, "out_of_stock": 1},
    "rows": [
        {"symbol": "STM32", "mpn": "STM32F407VGT6", "found": True, "on_mouser": True,
         "lifecycle": "NRND", "stock": 0, "obsolete": True, "in_stock": False,
         "suggested_replacement": "STM32F407VGT7"},
        {"symbol": "TPS", "mpn": "TPS2121RUXR", "found": True, "on_mouser": True,
         "lifecycle": "Active", "stock": 0, "obsolete": False, "in_stock": False,
         "suggested_replacement": None},
        {"symbol": "R10K", "mpn": "RC0402", "found": True, "on_mouser": True,
         "lifecycle": "Active", "stock": 5000, "obsolete": False, "in_stock": True},
        {"symbol": "MYS", "mpn": "MYSTERY-1", "found": False},
    ],
    "markdown": "# Library Sourcing\n\nsome report body\n",
}


# ── build: no network, verdict from the real structure ────────────────────────
def test_workbench_builds_without_touching_the_network(tmp_path, monkeypatch):
    # Building the tab must never sweep the distributor (opt-in only). Poison it.
    def _boom(*_a, **_k):
        raise AssertionError("the sweep ran at build time")
    monkeypatch.setattr(LM, "library_sourcing_report", _boom)
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    assert host._sourcing_report() is None            # nothing swept yet
    host.grab()                                       # renders (cards + buttons)


def test_verdict_counts_incomplete_parts(tmp_path):
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    # Under the strict 8-item passport NONE of the fixture parts is complete — even U1
    # (it has the three assets + a manufacturer but no MPN/datasheet/description/category),
    # which the old loose count wrongly called complete. So all three read incomplete.
    assert not host._verdict.isHidden()
    assert host._verdict._title.text() == "3 Incomplete"


def test_verdict_all_complete_is_ok(tmp_path):
    # A library whose every part is 8/8 under the strict passport (symbol, footprint, 3D
    # model, MPN, manufacturer, datasheet, description, category) reads all-complete —
    # the three assets alone no longer qualify.
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "U1" (property "Value" "U1" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_A" (id 2))'
        ' (property "MANUFACTURER" "ACME" (id 3))'
        ' (property "Manufacturer Part Number" "MPN-1" (id 4))'
        ' (property "Datasheet" "http://x/1.pdf" (id 5))'
        ' (property "Description" "a part" (id 6))'
        ' (property "Category" "Misc" (id 7)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "FP_A.kicad_mod").write_text(
        '(footprint "FP_A" (model ${MY3DMODELS}/FP_A.step))', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    (mdl / "FP_A.step").write_bytes(b"ISO-10303-21;")
    cfg = {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
           "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}
    host = LIB._health_workbench(_ctx(cfg))
    assert not host._verdict.isHidden()
    assert host._verdict._title.text() == "All Parts Complete"


def test_no_em_dash_in_static_copy(tmp_path):
    from PyQt5.QtWidgets import QLabel
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    for lab in host.findChildren(QLabel):
        assert "—" not in lab.text()


# ── the ▶ Fix All From Library primary flow (REAL engine, no mocks) ───────────
def test_fix_all_links_what_the_library_can_satisfy(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _ctx(cfg)
    commits = []
    monkeypatch.setattr(LM, "git_commit_push",
                        lambda c, log, msg: commits.append(msg) or True)
    host = LIB._health_workbench(ctx)
    host._run_primary()                               # headless: safe ops auto-selected
    txt = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    assert "MyFootprints:SOT_PART" in txt             # the engine linked the footprint
    fp = (Path(cfg["FootprintLib"]) / "SOT_PART.kicad_mod").read_text(encoding="utf-8")
    assert "SOT_PART.step" in fp                      # ...and attached the 3D model
    assert len(commits) == 1                          # ONE commit for the whole fix
    assert ("library.changed",) in ctx.bus.emitted    # Parts is told to rescan


def test_fix_all_drops_the_stub_superseded_by_a_link(tmp_path):
    # The orphan-footprint row plans create_symbol for SOT_PART while the symbol row
    # plans link_footprint to the same stem — applying both would leave a junk stub.
    # The audit must keep the link and drop the superseded stub op.
    cfg = _libcfg(tmp_path)
    host = LIB._health_workbench(_ctx(cfg))
    ops = host._fix_all_audit(host._snapshot())
    labels = [o["label"] for o in ops]
    assert any("Link footprint 'SOT_PART'" in l for l in labels)
    assert not any("stub symbol for footprint 'SOT_PART'" in l for l in labels)


def test_fix_all_when_everything_is_complete_reports_empty(tmp_path, monkeypatch):
    cfg = _libcfg(tmp_path)
    ctx = _ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    host = LIB._health_workbench(ctx)
    host._run_primary()                               # first run fixes the structure
    ctx.services.logs.clear()
    host._run_primary()                               # nothing structural left
    assert any("Every part is already structurally complete" in m
               for m in ctx.services.logs)


def test_fix_all_subset_applies_only_the_checked_ops(tmp_path, monkeypatch):
    from ui import kit as K
    cfg = _libcfg(tmp_path)
    ctx = _ctx(cfg)
    monkeypatch.setattr(LM, "git_commit_push", lambda c, log, msg: True)
    host = LIB._health_workbench(ctx)
    ops = host._fix_all_audit(host._snapshot())
    link_fp = next(o["key"] for o in ops if "Link footprint" in o["label"])
    monkeypatch.setattr(K, "_checkbox_preview", lambda *a, **k: [link_fp])
    host._run_primary()
    txt = Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
    assert "MyFootprints:SOT_PART" in txt             # the checked op applied
    fp = (Path(cfg["FootprintLib"]) / "SOT_PART.kicad_mod").read_text(encoding="utf-8")
    assert "SOT_PART.step" not in fp                  # the unchecked one did not


# ── the sourcing sweep (stubbed, opt-in) ──────────────────────────────────────
def test_sweep_button_disabled_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: None)
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    b = host._btn("Run Sourcing Check")
    assert b is not None and b.isEnabled() is False
    assert "key" in b.toolTip().lower()


def test_sweep_button_enabled_with_key(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    assert host._btn("Run Sourcing Check").isEnabled() is True


def test_sweep_without_key_logs_and_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: None)
    ctx = _ctx(_libcfg(tmp_path))
    host = LIB._health_workbench(ctx)
    host._run_sourcing()
    assert any("no mouser key" in m.lower() or "no distributor" in m.lower()
               for m in ctx.services.logs)


def test_sweep_seeds_the_parts_detail_cache_cross_tab(tmp_path, monkeypatch):
    # Adversarial-review regression: the Health sweep broadcasts library.sourcing_report,
    # and the Parts pane MUST consume it (detail.set_sourcing_report) so a just-swept part
    # shows live data without a fresh per-part lookup. Both panels share one ctx/bus.
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    monkeypatch.setattr(LM, "library_sourcing_report",
                        lambda cfg, lookup, throttle=0.0: _REPORT)
    from ui.feature import EventBus              # the REAL dispatching bus (the stub _Bus
    ctx = _ctx(_libcfg(tmp_path))                # in this file only records emissions)
    ctx.bus = EventBus()
    parts = LIB._parts_panel(ctx, None)           # subscribes to library.sourcing_report
    health = LIB._health_workbench(ctx)           # emits it on sweep
    health._run_sourcing()                        # headless run_populate is synchronous
    # every found MPN from the sweep is now cached in the Parts detail (not the not-found one)
    cache = parts.detail._src_cache
    assert "STM32F407VGT6" in cache and "TPS2121RUXR" in cache and "RC0402" in cache
    assert "MYSTERY-1" not in cache


def test_sweep_renders_flagged_parts_only(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    monkeypatch.setattr(LM, "library_sourcing_report",
                        lambda cfg, lookup, throttle=0.0: _REPORT)
    ctx = _ctx(_libcfg(tmp_path))
    host = LIB._health_workbench(ctx)
    host._run_sourcing()                              # headless run_populate is synchronous
    assert host._sourcing_report() is _REPORT
    tbl = host.findChild(QTableWidget)
    assert tbl is not None
    # Three flagged rows (obsolete NRND, out-of-stock active, and the not-found part);
    # the healthy in-stock RC0402 is NOT flagged.
    assert tbl.rowCount() == 3
    syms = {tbl.item(r, 0).text() for r in range(tbl.rowCount())}
    assert syms == {"STM32", "TPS", "MYS"}
    # ...and the sweep is broadcast so the Parts detail can seed its sourcing cache.
    assert any(e[0] == "library.sourcing_report" for e in ctx.bus.emitted)


# ── enrich blanks (dry-run → confirm → apply) ─────────────────────────────────
def test_enrich_dry_runs_confirms_then_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    seen = []

    def _enrich(cfg, lookup, dry_run=True, log=None):
        seen.append(dry_run)
        return {"changes": [{"symbol": "U1", "prop": "Description", "value": "x"}]}
    monkeypatch.setattr(LM, "enrich_library", _enrich)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    ctx = _ctx(_libcfg(tmp_path))
    host = LIB._health_workbench(ctx)
    host._enrich_blanks()
    assert seen == [True, False]                      # dry run first, then the write
    assert any("1" in m and "field" in m.lower() for m in ctx.services.logs)


def test_enrich_cancel_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    seen = []
    monkeypatch.setattr(LM, "enrich_library",
                        lambda cfg, lookup, dry_run=True, log=None:
                        seen.append(dry_run) or {"changes": [{"symbol": "U1"}]})
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    host._enrich_blanks()
    assert seen == [True]                             # never re-ran with dry_run=False


# ── exports ───────────────────────────────────────────────────────────────────
def test_export_health_report_writes_markdown(tmp_path):
    from ui import kit as K
    cfg = _libcfg(tmp_path)
    host = LIB._health_workbench(_ctx(cfg))
    ea = host._exports["Export Health Report"]
    out = tmp_path / "health.md"
    K._export_write(ea, host._snapshot(), str(out))
    body = out.read_text(encoding="utf-8")
    assert "1" in body and len(body) > 20             # the real markdown report


def test_export_sourcing_report_gated_until_a_sweep_ran(tmp_path, monkeypatch):
    from ui import kit as K
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: (lambda mpn: None))
    monkeypatch.setattr(LM, "library_sourcing_report",
                        lambda cfg, lookup, throttle=0.0: _REPORT)
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    b = host._btn("Export Sourcing Report")
    assert b is not None and not b.isEnabled()        # nothing to export yet
    host._run_sourcing()
    assert b.isEnabled()
    ea = host._exports["Export Sourcing Report"]
    out = tmp_path / "sourcing.md"
    K._export_write(ea, host._snapshot(), str(out))
    assert out.read_text(encoding="utf-8") == _REPORT["markdown"]


# ── discipline ────────────────────────────────────────────────────────────────
def test_busy_gate_disables_actions(tmp_path):
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    host._busy["on"] = True
    assert not host._btn("Enrich Blanks From Distributor").isEnabled()
    host._busy["on"] = False
    assert host._btn("Enrich Blanks From Distributor").isEnabled()


def test_refresh_keeps_the_restyle_registry_flat(tmp_path):
    from ui import widgets as W
    host = LIB._health_workbench(_ctx(_libcfg(tmp_path)))
    before = len(W._RESTYLERS)
    for _ in range(8):
        host._refresh()
    assert len(W._RESTYLERS) <= before, (
        f"health refresh leaked {len(W._RESTYLERS) - before} restyle callbacks")
