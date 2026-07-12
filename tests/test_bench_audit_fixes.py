"""Bench audit fixes — the second wave.

Covers, with tests that drive the REAL panel behaviour (offscreen, real DB):

  BENCH-leak (codequality): the rebuilt Overview + Resolver panels subscribed to the
    bus with a bare on(), so every package/family switch left the stale closure on the
    bus forever. They now use on_owned(..., owner=root) and auto-unsubscribe when the
    panel root is destroyed.

  BENCH-budget (codequality): the Exports "State" column painted an unconditional green
    "OK" that the code could never invalidate. It now derives per-rail hazards from
    current_budget()['findings'] and shows "Check" for a flagged rail.

  BENCH-empty (ux): the Resolver first-open showed a blank pane; it now seeds a proper
    empty state and clears it on the first resolve.

  BENCH-map (ux): the resolved pin map now carries a legend + zoom + a clickable
    map->table-row highlight, matching the Overview map.

  BENCH-perf (perf): Profiles grouped ~53 parts with a full authority build per part,
    re-run on every open AND every family switch. The grouping is now memoised per
    package (one build shared across the resolves) and a family switch reuses it.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5 import sip  # noqa: E402
from PyQt5.QtCore import Qt, QPoint, QPointF  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel, QTableWidget  # noqa: E402

import stm32_db as db  # noqa: E402
import stm32_authority as sauth  # noqa: E402
import ui.feature as F  # noqa: E402
import ui.widgets as W  # noqa: E402
from ui.features import bench  # noqa: E402
from ui.features import bench_visuals  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _fake_ctx(cfg=None):
    class _Svc:
        def __init__(self):
            self.logs = []

        def log(self, m):
            self.logs.append(str(m))

        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    return SimpleNamespace(cfg=cfg or {}, services=_Svc(), theme=None, bus=F.EventBus())


def _all_labels(w):
    return " \n".join(l.text() for l in w.findChildren(QLabel))


# ── pure helper: the budget hazard extractor (no DB / no Qt) ─────────────────
class BudgetHazardRailsTests(unittest.TestCase):
    def test_flags_only_rails_named_in_findings_and_present_in_rails(self):
        budget = {
            "rails": {"VTARGET": {}, "GND": {}, "VDDA": {}},
            "findings": [
                "VTARGET: connector feed 2000 mA is below the ~2400 mA reference draw.",
                "GND: with no direct pin, the socketed part closes only its own ...",
            ],
        }
        self.assertEqual(bench._budget_hazard_rails(budget), {"VTARGET", "GND"})

    def test_no_findings_means_no_hazards(self):
        budget = {"rails": {"VTARGET": {}, "GND": {}}, "findings": []}
        self.assertEqual(bench._budget_hazard_rails(budget), set())

    def test_finding_for_unknown_rail_is_ignored(self):
        # A finding whose prefix is not an actual rail key must never label a row.
        budget = {"rails": {"VTARGET": {}}, "findings": ["MYSTERY: something"]}
        self.assertEqual(bench._budget_hazard_rails(budget), set())


# ── bus-leak: on_owned auto-unsubscribes on panel destroy (no DB, real bus) ──
class BusOwnedUnsubscribeTests(unittest.TestCase):
    def test_overview_and_resolver_do_not_leak_subscribers(self):
        # Build the two rebuilt panels against a stub state (no DB needed: we only need
        # the subscription + destroy path). If a panel can't build far enough to
        # subscribe (e.g. error state), the count simply stays 0, which still passes.
        ctx = _fake_ctx()

        class _Stub(bench.BenchState):
            def __init__(self):
                # bypass the real DB connect; give just enough surface for the panels
                self.conn = None
                self.packages = []
                self.package = None
                self.error = "no db"
                self._cache = {}
                self._refreshers = []
                self._profile_tiers = {}
                self.family = None
                self.goto_resolver = None
                self.goto_authority_pin = None

        # The resolver panel subscribes to bench.resolve regardless of DB (its body build
        # is guarded), so it is the reliable leak probe here.
        st = _Stub()
        before = len(ctx.bus._subs.get("bench.resolve", []))
        p1 = bench._resolver_panel(ctx, st)
        after_one = len(ctx.bus._subs.get("bench.resolve", []))
        self.assertEqual(after_one, before + 1, "resolver did not subscribe")
        p2 = bench._resolver_panel(ctx, st)
        self.assertEqual(len(ctx.bus._subs.get("bench.resolve", [])), before + 2)
        # Destroying a panel root must drop exactly its own subscription (on_owned).
        # sip.delete deletes the C++ object now; on_owned tracks the owner by a WEAKREF and
        # prunes lazily (a `destroyed`-connected Python slot segfaults during GC — see
        # ui.feature.EventBus). The C++ half is `sip.isdeleted` immediately, so _prune()
        # (which every emit also runs) drops the dead subscriber. (A real rebuild drops the
        # old root the same way — the next emit on the bus prunes it.)
        sip.delete(p1)
        ctx.bus._prune("bench.resolve")
        self.assertEqual(len(ctx.bus._subs.get("bench.resolve", [])), before + 1,
                         "destroyed resolver panel leaked its bus subscriber")
        sip.delete(p2)
        ctx.bus._prune("bench.resolve")
        self.assertEqual(len(ctx.bus._subs.get("bench.resolve", [])), before,
                         "second resolver panel leaked its bus subscriber")


# ── end-to-end against a real built DB ───────────────────────────────────────
class _DBTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls._dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, cls._dbp)
        cls._prev_env = os.environ.get("STM32_DB")
        os.environ["STM32_DB"] = str(cls._dbp)

    @classmethod
    def tearDownClass(cls):
        if cls._prev_env is None:
            os.environ.pop("STM32_DB", None)
        else:
            os.environ["STM32_DB"] = cls._prev_env


class ExportsStateColumnTests(_DBTestBase):
    def test_state_column_reflects_budget_findings_not_a_blind_ok(self):
        state = bench.BenchState()
        self.assertIsNone(state.error, state.error)
        ctx = _fake_ctx()
        authority = state.authority()
        budget = sauth.current_budget(authority)
        hazards = bench._budget_hazard_rails(budget)

        panel = bench._outputs_panel(ctx, state)
        # find the rails table: the one whose header row starts with "Rail"
        tables = panel.findChildren(QTableWidget)
        rail_tbl = None
        for t in tables:
            cols = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
            if cols and cols[0] == "Rail" and "State" in cols:
                rail_tbl = t
                break
        self.assertIsNotNone(rail_tbl, "rails table with a State column did not render")
        cols = [rail_tbl.horizontalHeaderItem(c).text() for c in range(rail_tbl.columnCount())]
        rail_col = cols.index("Rail")
        state_col = cols.index("State")

        # Read each row's rail name + State tag text.
        seen = {}
        for r in range(rail_tbl.rowCount()):
            rail_w = rail_tbl.cellWidget(r, rail_col)
            rail_lbls = rail_w.findChildren(QLabel) if rail_w else []
            rail_name = next((l.text() for l in rail_lbls if l.text()), "")
            st_w = rail_tbl.cellWidget(r, state_col)
            st_lbls = st_w.findChildren(QLabel) if st_w else []
            st_text = st_lbls[0].text() if st_lbls else (st_w.text() if isinstance(st_w, QLabel) else "")
            seen[rail_name] = st_text

        # Every hazard rail shows "Check"; every non-hazard rail shows "OK". Crucially the
        # State column is NOT a uniform wall of "OK" unless the budget genuinely has no
        # hazard — and when a hazard exists at least one row must read "Check".
        # tag labels carry a leading dot glyph ("● OK"), so match on the word.
        for rail, st_text in seen.items():
            if rail in hazards:
                self.assertIn("Check", st_text, f"{rail} is a budget hazard but shows {st_text!r}")
                self.assertNotIn("OK", st_text, f"{rail} hazard row shows OK: {st_text!r}")
            else:
                self.assertIn("OK", st_text, f"{rail} is healthy but shows {st_text!r}")
                self.assertNotIn("Check", st_text, f"{rail} healthy row shows Check: {st_text!r}")
        if hazards:
            self.assertTrue(any("Check" in v for v in seen.values()),
                            "budget has a hazard yet no rail row shows Check")


class ResolverEmptyStateTests(_DBTestBase):
    def test_first_open_shows_guidance_then_clears_on_resolve(self):
        state = bench.BenchState()
        ctx = _fake_ctx()
        panel = bench._resolver_panel(ctx, state)
        labels = _all_labels(panel)
        self.assertIn("Resolve a Part to View Its Exact Pinout", labels,
                      "resolver first-open has no empty-state guidance")
        # after a real resolve the guidance is gone and the resolved header appears
        ctx.bus.emit("bench.resolve", "STM32F407VGT6")
        labels2 = _all_labels(panel)
        self.assertNotIn("Resolve a Part to View Its Exact Pinout", labels2,
                         "empty-state guidance was not cleared on resolve")
        tbl = panel.findChild(QTableWidget)
        self.assertIsNotNone(tbl, "resolved pin table did not render")


class ResolverMapAffordancesTests(_DBTestBase):
    def _resolved_panel(self, mpn="STM32F407VGT6"):
        state = bench.BenchState()
        ctx = _fake_ctx()
        panel = bench._resolver_panel(ctx, state)
        ctx.bus.emit("bench.resolve", mpn)
        return panel

    def test_resolved_map_has_legend_and_zoom_controls(self):
        panel = self._resolved_panel()
        # legend(): the resolved map now decodes its colours (a "Net Colour" eyebrow).
        self.assertIn("NET COLOUR", _all_labels(panel).upper(),
                      "resolved map has no legend")
        # zoom: a Reset ghost button + a −/+ segmented pair (both are QPushButtons).
        from PyQt5.QtWidgets import QPushButton
        btn_texts = {b.text() for b in panel.findChildren(QPushButton)}
        self.assertIn("Reset", btn_texts, "resolved map has no zoom Reset")
        self.assertTrue({"−", "+"} <= btn_texts, "resolved map has no −/+ zoom control")
        # the segmented control is actually wired (a real W.Segmented, not decoration)
        self.assertTrue(panel.findChildren(W.Segmented), "resolved map zoom is not a Segmented")
        # the map IS its own pan/zoom viewport now (owner v2.11: the old inert
        # QScrollArea wrapper could neither zoom toward the pointer nor pan). The
        # PinMap carries the camera state itself.
        pm = panel.findChild(bench_visuals.PinMap)
        self.assertIsNotNone(pm, "resolved PinMap did not render")
        self.assertTrue(hasattr(pm, "_pan") and callable(getattr(pm, "reset_view", None)),
                        "resolved map is not the self-panning PinMap")

    def test_map_is_interactive_and_click_highlights_the_table_row(self):
        panel = self._resolved_panel()
        pm = panel.findChild(bench_visuals.PinMap)
        self.assertIsNotNone(pm, "resolved PinMap did not render")
        self.assertIsNotNone(pm._on_select, "resolved map is inert (on_select is None)")
        tbl = panel.findChild(QTableWidget)
        self.assertIsNotNone(tbl)
        self.assertGreater(tbl.rowCount(), 1)
        # pick a real pin from the table's Pin column and select it via the map callback
        target_row = min(3, tbl.rowCount() - 1)
        target_pin = int(tbl.item(target_row, 0).text())
        tbl.clearSelection()
        pm._on_select(target_pin)
        sel = tbl.selectionModel().selectedRows()
        self.assertTrue(sel, "map click selected no table row")
        self.assertEqual(sel[0].row(), target_row,
                         "map click highlighted the wrong table row")


def _synthetic_positions(n=16):
    """A fixture package geometry for the pure-widget camera tests (no DB needed)."""
    return [{"position": i, "switch_class": "fixed", "pin_names": {f"P{i}": 1},
             "breakout": {}} for i in range(1, n + 1)]


class _FakeMouse:
    """A minimal stand-in for QMouseEvent — the handlers only read pos/button/buttons,
    so this avoids the Qt-version-specific QMouseEvent constructor overloads."""
    def __init__(self, pos, button=Qt.LeftButton, buttons=Qt.LeftButton):
        self._pos, self._button, self._buttons = pos, button, buttons

    def pos(self):
        return self._pos

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons


class _FakeWheel:
    """A minimal stand-in for QWheelEvent (reads angleDelta/pos, calls accept)."""
    def __init__(self, pos, dy):
        self._pos, self._dy = pos, dy

    def angleDelta(self):
        return QPoint(0, self._dy)

    def pos(self):
        return self._pos

    def accept(self):
        pass


class PinMapCameraTests(unittest.TestCase):
    """The pan/zoom camera — owner v2.11 bug: "zoom doesn't zoom toward the pointer, and
    you can't drag/pan". The map lays out once at the base size and paints through a
    translate+scale transform: the wheel zooms toward the cursor, a left-drag pans, and a
    click with no drag selects the pad under the pointer."""
    def setUp(self):
        self._picked = []

    def _map(self, base=380):
        pm = bench_visuals.PinMap(on_select=lambda pos: self._picked.append(pos), base=base)
        pm.set_positions(_synthetic_positions(16),
                         {i: {"cat": "lane", "fivev": False} for i in range(1, 17)})
        return pm

    def test_zoom_to_pointer_keeps_the_point_under_the_cursor_fixed(self):
        pm = self._map()
        anchor = QPointF(250.0, 250.0)           # an interior viewport point
        before = pm._to_content(anchor)
        pm.set_zoom(2.0, anchor=anchor)
        self.assertAlmostEqual(pm._zoom, 2.0, places=6)
        after = pm._to_content(anchor)
        # the SAME content point sits under the cursor after the zoom (within rounding)
        self.assertAlmostEqual(before.x(), after.x(), places=3)
        self.assertAlmostEqual(before.y(), after.y(), places=3)

    def test_wheel_up_zooms_in_anchored_to_the_cursor(self):
        pm = self._map()
        anchor = QPoint(120, 300)
        before = pm._to_content(QPointF(anchor))
        pm.wheelEvent(_FakeWheel(anchor, 120))
        self.assertGreater(pm._zoom, 1.0, "wheel up did not zoom in")
        after = pm._to_content(QPointF(anchor))
        self.assertAlmostEqual(before.x(), after.x(), places=2)
        self.assertAlmostEqual(before.y(), after.y(), places=2)

    def test_left_drag_pans_the_map(self):
        pm = self._map()
        pm.set_zoom(2.0)                          # content (760) > viewport (380) => pannable
        pan0 = QPointF(pm._pan)
        pm.mousePressEvent(_FakeMouse(QPoint(190, 190)))
        pm.mouseMoveEvent(_FakeMouse(QPoint(150, 170), button=Qt.NoButton, buttons=Qt.LeftButton))
        self.assertTrue(pm._dragging, "a >slop drag did not enter pan mode")
        self.assertNotEqual((pm._pan.x(), pm._pan.y()), (pan0.x(), pan0.y()),
                            "left-drag did not pan the map")
        pm.mouseReleaseEvent(_FakeMouse(QPoint(150, 170), buttons=Qt.NoButton))
        self.assertFalse(pm._dragging)
        self.assertEqual(self._picked, [], "a drag must not also select a pad")

    def test_click_without_drag_selects_the_pad_under_the_pointer(self):
        pm = self._map()
        pin = pm._geo["pins"][0]                  # zoom 1: viewport == content coords
        x, y, w, h = pin["rect"]
        pt = QPoint(int(x + w / 2), int(y + h / 2))
        pm.mousePressEvent(_FakeMouse(pt))
        pm.mouseReleaseEvent(_FakeMouse(pt, buttons=Qt.NoButton))
        self.assertEqual(self._picked, [pin["pos"]], "a click did not select the pad under it")
        self.assertEqual(pm._selected, pin["pos"])

    def test_reset_view_restores_zoom_and_centres(self):
        pm = self._map()
        pm.set_zoom(3.0, anchor=QPointF(50.0, 50.0))
        pm.reset_view()
        self.assertAlmostEqual(pm._zoom, 1.0, places=6)
        # at zoom 1 the content equals the viewport, so pan centres back to (0, 0)
        self.assertAlmostEqual(pm._pan.x(), 0.0, places=3)
        self.assertAlmostEqual(pm._pan.y(), 0.0, places=3)

    def test_zoom_clamps_to_bounds(self):
        pm = self._map()
        pm.set_zoom(999.0)
        self.assertLessEqual(pm._zoom, pm._MAX_ZOOM)
        pm.set_zoom(0.001)
        self.assertGreaterEqual(pm._zoom, pm._MIN_ZOOM)

    def test_pan_is_clamped_so_the_map_cannot_be_dragged_out_of_view(self):
        pm = self._map()
        pm.set_zoom(2.0)
        # yank far past the edge; the clamp must keep the content spanning the viewport
        pm.mousePressEvent(_FakeMouse(QPoint(190, 190)))
        pm.mouseMoveEvent(_FakeMouse(QPoint(9000, 9000), button=Qt.NoButton, buttons=Qt.LeftButton))
        c = pm._content()
        self.assertLessEqual(pm._pan.x(), 0.0 + 1e-6)
        self.assertGreaterEqual(pm._pan.x(), pm.width() - c - 1e-6)


class ProfilesPerfMemoTests(_DBTestBase):
    def test_grouping_is_memoised_and_family_switch_reuses_it(self):
        state = bench.BenchState()
        ctx = _fake_ctx()
        pkg = state.package

        calls = {"build": 0, "resolve": 0}
        real_build = sauth.build
        real_resolve = sauth.resolve_part

        def counting_build(conn, package):
            calls["build"] += 1
            return real_build(conn, package)

        def counting_resolve(conn, mpn):
            calls["resolve"] += 1
            return real_resolve(conn, mpn)

        sauth.build = counting_build
        sauth.resolve_part = counting_resolve
        try:
            # first open: the grouping runs once. Because build() is memoised across the
            # per-part resolves, build is called far fewer times than resolve.
            bench._profiles_panel(ctx, state)
            first_resolves = calls["resolve"]
            first_builds = calls["build"]
            self.assertGreater(first_resolves, 1, "profiles resolved no parts")
            # the memoised build: one authority build shared across all the resolves
            # (plus at most a couple from the panel's own state.authority()/families()).
            self.assertLess(first_builds, first_resolves,
                            "build() was not shared across the per-part resolves")
            self.assertIn(pkg, state._profile_tiers, "grouping was not cached on state")

            # a family switch fires rebuild_all -> the panel rebuilds. It MUST reuse the
            # cached grouping: no further resolve_part calls for the grouping.
            fams = state.families()
            self.assertTrue(fams, "package supports no families")
            state.set_family(fams[0])
            resolves_before = calls["resolve"]
            bench._profiles_panel(ctx, state)
            self.assertEqual(calls["resolve"], resolves_before,
                             "family switch re-ran the per-part grouping instead of "
                             "reusing the cache")
        finally:
            sauth.build = real_build
            sauth.resolve_part = real_resolve

    def test_family_filtered_tiers_only_contain_family_parts(self):
        state = bench.BenchState()
        ctx = _fake_ctx()
        fams = state.families()
        self.assertTrue(fams)
        fam = fams[0]
        # group everything first (full open)
        bench._profiles_panel(ctx, state)
        # now narrow and rebuild; the rendered chip token_links must all be family parts
        state.set_family(fam)
        panel = bench._profiles_panel(ctx, state)
        # token_link chips carry the part number as a label; gather every mono/part label
        # under the profile cards and confirm each shown chip belongs to the family.
        shown_parts = [l.text() for l in panel.findChildren(QLabel)
                       if l.text().upper().startswith("STM32")]
        self.assertTrue(shown_parts, "no chips rendered after family filter")
        offenders = [p for p in shown_parts if fam.upper() not in p.upper()]
        self.assertEqual(offenders, [],
                         f"family-filtered profiles show non-family chips: {offenders[:5]}")


if __name__ == "__main__":
    unittest.main()
