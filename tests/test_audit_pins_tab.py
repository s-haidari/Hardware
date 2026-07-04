"""Regression tests for the audit fixes in tools/stm32_pins_tab.py.

Covers:
  * Fix 2 (MEDIUM) — _pin_search_haystack now indexes the visible Destination net
    and the Switch label, so typing 'VTARGET' / 'CARD_LANE_###' / 'Must-Switch'
    finds the pin instead of yielding '0 pins'. Pure function -> unit-tested
    directly, plus an offscreen end-to-end filter check.
  * Fix 3 (MEDIUM) — _select() now drives the Table row selection (map click ->
    matching table row).
  * Fix 4 (LOW)    — a failed load() reverts the package selector to the last good
    package (or clears the views) instead of leaving stale pins under a new label.
  * Fix 5 (LOW)    — _populate_packages closes the sqlite handle via try/finally
    even when the query raises.
  * Fix 1 (HIGH)   — _export() appends the missing extension and traps a failed
    write in a dialog instead of tearing down the app.

Pure-function tests run without Qt; GUI tests run under the offscreen platform and
skip if PyQt5 is unavailable.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db          # noqa: E402
import stm32_authority as auth  # noqa: E402


def _fake_position(**over):
    """A minimal socket-position dict shaped like authority['positions'][i], enough
    for the pure haystack + rationale helpers. Overridable per test."""
    p = {
        "position": 42,
        "pin_names": {"PB7": 1},
        "role_set": {"I2C1_SDA": 1},
        "switch_class": db.SWITCH_MUST,
        "tags": {},
        "assignment": {"destination": "VTARGET"},
        "breakout": {"service_nets": []},
        "peripherals": ["I2C1"],
    }
    p.update(over)
    return p


class HaystackPureTests(unittest.TestCase):
    """Fix 2, no Qt needed: the search haystack is a pure function of one position."""

    def setUp(self):
        # importing the tab pulls PyQt5; skip cleanly if it isn't installed
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        self.tab = tab

    def test_destination_is_indexed(self):
        hs = self.tab._pin_search_haystack(_fake_position(assignment={"destination": "VTARGET"}))
        self.assertIn("vtarget", hs)          # the regression: destination now searchable

    def test_net_fallback_destination_is_indexed(self):
        # plain GPIO pins carry assignment['net'] (e.g. CARD_LANE_042), not 'destination'
        p = _fake_position(switch_class=db.SWITCH_NONE,
                           assignment={"net": "CARD_LANE_042"})
        hs = self.tab._pin_search_haystack(p)
        self.assertIn("card_lane_042", hs)

    def test_switch_label_is_indexed(self):
        must = self.tab._pin_search_haystack(_fake_position(switch_class=db.SWITCH_MUST))
        self.assertIn("must-switch", must)     # the 'Switch' column text, now searchable
        fixed = self.tab._pin_search_haystack(_fake_position(switch_class=db.SWITCH_NONE))
        self.assertIn("fixed", fixed)

    def test_existing_fields_still_indexed(self):
        hs = self.tab._pin_search_haystack(_fake_position())
        self.assertIn("pb7", hs)               # pin name
        self.assertIn("i2c1_sda", hs)          # role
        self.assertIn("i2c1", hs)              # peripheral
        self.assertEqual(hs, hs.lower())       # lowercased for case-insensitive match


class HaystackRealAuthorityTests(unittest.TestCase):
    """Fix 2 against a real built authority (uses the checked-in DB)."""

    @classmethod
    def setUpClass(cls):
        dbp = db.default_db_path()
        if not dbp.exists():
            raise unittest.SkipTest("stm32 database not built")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        cls.tab = tab
        cls.conn = db.connect(dbp)
        cls.a64 = auth.build(cls.conn, "LQFP64")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None) is not None:
            cls.conn.close()

    def _pos(self, n):
        return next(p for p in self.a64["positions"] if p["position"] == n)

    def test_real_destination_and_label_indexed(self):
        p1 = self._pos(1)                                   # VBAT/VDD -> VBAT_TGT, must-switch
        hs = self.tab._pin_search_haystack(p1)
        self.assertIn("vbat_tgt", hs)                       # visible Destination cell
        self.assertIn("must-switch", hs)                    # visible Switch cell

    def test_every_position_haystack_covers_its_destination(self):
        # For every pin, whatever the Table shows in the Destination column must be
        # findable through the haystack (the exact contract the bug violated).
        for p in self.a64["positions"]:
            dest = (p["assignment"].get("destination")
                    or p["assignment"].get("net") or "")
            if dest:
                self.assertIn(dest.lower(), self.tab._pin_search_haystack(p),
                              f"pin {p['position']} destination {dest!r} not searchable")


class _FakeMsg:
    """Stand-in for QMessageBox so modal dialogs never block the offscreen tests;
    records warnings so a test can assert one was shown."""
    warnings: list = []

    @staticmethod
    def warning(parent, title, text, *a, **k):
        _FakeMsg.warnings.append((title, text))
        return None

    @staticmethod
    def information(parent, title, text, *a, **k):
        return None


class PinsTabGuiTests(unittest.TestCase):
    """Offscreen widget tests for the selection-sync, load-revert, package-handle and
    export-guard fixes. Skips if PyQt5 is unavailable."""

    @classmethod
    def setUpClass(cls):
        dbp = db.default_db_path()
        if not dbp.exists():
            raise unittest.SkipTest("stm32 database not built")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"PyQt5 unavailable: {e}")
        cls.tab = tab
        cls.dbp = dbp
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        # neutralise modal dialogs for every GUI test
        self._real_qmb = self.tab.QMessageBox
        self.tab.QMessageBox = _FakeMsg
        _FakeMsg.warnings = []
        w = self.tab.Stm32PinsWidget()
        w.db_path = self.dbp
        w.load("LQFP64")
        self.w = w

    def tearDown(self):
        self.tab.QMessageBox = self._real_qmb
        self.w.deleteLater()

    # ── Fix 3: map/diagram selection drives the table row ──
    def test_select_syncs_table_row(self):
        from PyQt5.QtCore import Qt
        self.w._select(19)
        rows = self.w.table.selectionModel().selectedRows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.w.table.item(rows[0].row(), 0).data(Qt.UserRole), 19)
        self.assertEqual(self.w._sel_pos, 19)
        # re-selecting a different pin moves the table selection with it
        self.w._select(1)
        rows = self.w.table.selectionModel().selectedRows()
        self.assertEqual(self.w.table.item(rows[0].row(), 0).data(Qt.UserRole), 1)

    def test_select_row_correct_after_resort(self):
        from PyQt5.QtCore import Qt
        self.w.table.sortItems(0, Qt.DescendingOrder)   # rows now 64..1
        self.w._select(1)                               # last row after the re-sort
        rows = self.w.table.selectionModel().selectedRows()
        self.assertEqual(self.w.table.item(rows[0].row(), 0).data(Qt.UserRole), 1)

    # ── Fix 2 end-to-end: search by a visible destination / switch label ──
    def test_filter_by_destination_text(self):
        self.w.search.setText("VBAT_TGT")               # pin 1's Destination cell
        visible = [r for r in range(self.w.table.rowCount())
                   if not self.w.table.isRowHidden(r)]
        self.assertTrue(visible, "typing a visible destination must not yield 0 pins")
        from PyQt5.QtCore import Qt
        for r in visible:
            self.assertEqual(self.w.table.item(r, 0).data(Qt.UserRole), 1)

    def test_filter_by_switch_label(self):
        self.w.search.setText("Must-Switch")            # the Switch column label
        visible = sum(not self.w.table.isRowHidden(r)
                      for r in range(self.w.table.rowCount()))
        self.assertEqual(visible, self.a64_must_count())

    def a64_must_count(self):
        return self.w.authority["rollup"]["must_switch_count"]

    # ── Fix 4: failed load reverts the selector, keeps the good authority ──
    def test_failed_load_reverts_package_and_keeps_authority(self):
        self.assertEqual(self.w._loaded_package, "LQFP64")
        good = self.w.authority
        if self.w.pkg_combo.findText("LQFP100") < 0:
            self.w.pkg_combo.addItem("LQFP100")
        real_build = self.tab.sauth.build
        self.tab.sauth.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.w.pkg_combo.setCurrentText("LQFP100")   # fires load -> fails -> revert
        finally:
            self.tab.sauth.build = real_build
        self.assertIs(self.w.authority, good)            # last-good authority preserved
        self.assertEqual(self.w.pkg_combo.currentText(), "LQFP64")  # selector reverted
        self.assertTrue(_FakeMsg.warnings)               # user was told

    def test_first_load_failure_clears_views(self):
        self.w._loaded_package = None                    # simulate 'nothing loaded yet'
        real_build = self.tab.sauth.build
        self.tab.sauth.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.w.load("LQFP100")
        finally:
            self.tab.sauth.build = real_build
        self.assertIsNone(self.w.authority)              # views reset to empty
        self.assertEqual(self.w.table.rowCount(), 0)

    # ── Fix 5: the package query closes its sqlite handle even on error ──
    def test_populate_packages_closes_handle_on_error(self):
        closed = {"v": False}

        class _FakeConn:
            def execute(self, *a, **k):
                raise RuntimeError("boom")

            def close(self):
                closed["v"] = True

        real_connect = self.tab.sdb.connect
        self.tab.sdb.connect = lambda *a, **k: _FakeConn()
        try:
            self.w._packages_populated = False
            self.w._populate_packages()                  # must not raise
        finally:
            self.tab.sdb.connect = real_connect
        self.assertTrue(closed["v"], "sqlite handle must be closed via finally on error")

    # ── Fix 1: export appends the extension and traps write failures ──
    def test_export_appends_missing_extension(self):
        outdir = Path(tempfile.mkdtemp())
        target = str(outdir / "myexport")                # deliberately no .csv

        class _FakeDlg:
            @staticmethod
            def getSaveFileName(*a, **k):
                return (target, "*.csv")

        real_dlg = self.tab.QFileDialog
        self.tab.QFileDialog = _FakeDlg
        try:
            self.w._export_csv()
        finally:
            self.tab.QFileDialog = real_dlg
        self.assertTrue((outdir / "myexport.csv").exists())

    def test_export_write_failure_is_trapped(self):
        # A render/write that raises PermissionError (file open in Excel) must be
        # caught and reported, not propagated (which would kill the app).
        outdir = Path(tempfile.mkdtemp())

        class _FakeDlg:
            @staticmethod
            def getSaveFileName(*a, **k):
                return (str(outdir / "locked.csv"), "*.csv")

        def _boom(_a):
            raise PermissionError("The process cannot access the file")

        real_dlg = self.tab.QFileDialog
        self.tab.QFileDialog = _FakeDlg
        try:
            # must return normally (no exception escapes) and warn the user
            self.w._export("csv", _boom, "pins")
        finally:
            self.tab.QFileDialog = real_dlg
        self.assertTrue(_FakeMsg.warnings, "a failed export must surface a warning dialog")


if __name__ == "__main__":
    unittest.main(verbosity=2)
