#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration tests for the EXPANDED Project Settings wiring in
tools/kicad_tools.py (KiCadToolsWidget).

These verify the *integration* the orchestrator asked for: the already-built
backends (nd_project_settings_manager's extended API + nd_board_setup) are now
reachable from the widget's Project Settings pane and actually flow through on
Load / Sync.

Covered:
  * SMOKE — the whole widget constructs offscreen with a tmp projects dir without
    raising, the three existing operations (rename / net / settings) still build,
    and the new extended sub-widgets exist.
  * The extended backend is imported and reachable (board_setup module + the
    SEVERITY_LEVELS / DRC_RULE_IDS / ERC_RULE_IDS / ERC_PIN_TYPES constants).
  * _ps_populate_extended -> the manager's ADDITIVE mutators (set_drc_severity /
    set_erc_severity / set_text_variable / set_track_widths / set_via_dimensions /
    set_diff_pair_dimensions / set_default_netclass), including preserve-by-default
    (an empty section writes nothing).
  * _ps_load_extended_into_widgets round-trips load_extended() state back into the
    widgets (severity combos, text-var / size tables, editable Default class).
  * _ps_write_board_setup writes solder-mask/paste to the board's .kicad_pcb
    (setup ...) block (mils -> mm), the place KiCad actually reads them.
  * _ps_load and _ps_sync end-to-end through the real project list (dialogs
    stubbed): a full Sync flushes+verifies the extended state AND writes the board
    setup; a Load populates every extended widget.

All GUI tests run under QT_QPA_PLATFORM=offscreen and skip if PyQt5 /
QFluentWidgets are unavailable.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_project_settings_manager as PSM   # noqa: E402
import nd_board_setup as BS                 # noqa: E402


def _make_project(root: Path, name: str = "board", *, extended: bool = False,
                  with_board: bool = True) -> Path:
    """Create a minimal but realistic KiCad project under root/name and return the
    .kicad_pro path. When extended=True the .kicad_pro carries DRC/ERC severities,
    text vars and the predefined size tables so a load has something to read."""
    proj = root / name
    proj.mkdir(parents=True, exist_ok=True)
    data = {
        "schematic": {"drawing": {
            "default_text_size": 50.0, "default_line_thickness": 6.0,
            "pin_symbol_size": 25.0, "junction_size_choice": 3,
        }},
        "board": {"design_settings": {
            "defaults": {}, "rules": {"min_clearance": 0.2, "min_track_width": 0.254},
        }},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.2,
                                      "track_width": 0.25, "via_diameter": 0.8,
                                      "via_drill": 0.4}]},
    }
    if extended:
        ds = data["board"]["design_settings"]
        ds["rule_severities"] = {"clearance": "warning", "hole_to_hole": "error"}
        ds["track_widths"] = [0.0, 0.3, 0.4]
        ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0},
                                {"diameter": 0.9, "drill": 0.45}]
        ds["diff_pair_dimensions"] = [{"width": 0.2, "gap": 0.15, "via_gap": 0.25}]
        data["erc"] = {"rule_severities": {"pin_not_driven": "ignore"}}
        data["text_variables"] = {"REV": "A1", "AUTHOR": "netdeck"}
    pro = proj / f"{name}.kicad_pro"
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if with_board:
        pcb = proj / f"{name}.kicad_pcb"
        pcb.write_text(
            "(kicad_pcb\n\t(version 20241229)\n"
            "\t(layers\n\t\t(0 \"F.Cu\" signal)\n\t)\n"
            "\t(setup\n\t\t(pad_to_mask_clearance 0.05)\n\t)\n"
            "\t(net 0 \"\")\n)\n", encoding="utf-8")
    return pro


class _FakeMsg:
    """Stand-in for QMessageBox so modal dialogs never block the offscreen tests.
    question() auto-confirms (Yes); information/warning are recorded no-ops."""
    Yes = 0x4000
    No = 0x10000
    events: list = []

    @staticmethod
    def question(*a, **k):
        _FakeMsg.events.append(("question", a[1] if len(a) > 1 else ""))
        return _FakeMsg.Yes

    @staticmethod
    def information(*a, **k):
        _FakeMsg.events.append(("information", a[1] if len(a) > 1 else ""))
        return None

    @staticmethod
    def warning(*a, **k):
        _FakeMsg.events.append(("warning", a[1] if len(a) > 1 else ""))
        return None


class SettingsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PyQt5.QtWidgets import QApplication
            import fluent_theme
            import kicad_tools as KT
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"PyQt5 / QFluentWidgets unavailable: {e}")
        cls._app = QApplication.instance() or QApplication([])
        try:
            fluent_theme.apply_grayscale_fluent(dark=True)   # match the app path
        except Exception:
            pass
        cls.KT = KT

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        # neutralise modal dialogs for the whole test
        self._real_qmb = self.KT.QMessageBox
        self.KT.QMessageBox = _FakeMsg
        _FakeMsg.events = []

    def tearDown(self):
        self.KT.QMessageBox = self._real_qmb

    def _widget(self, projects_dir: Path):
        return self.KT.KiCadToolsWidget(None, str(projects_dir))

    # ── SMOKE: constructs offscreen, existing + new sections present ──────────
    def test_widget_constructs_offscreen(self):
        w = self._widget(self._tmp)               # empty projects dir, no raise
        self.assertIsNotNone(w)
        # existing operations still wired (additive contract)
        self.assertEqual(set(w._op_index), {"rename", "net", "settings"})
        self.assertEqual(w.stack.count(), 3)
        self.assertTrue(w.ps_spins)               # existing mils spins kept
        for attr in ("op_combo", "nc_table"):
            self.assertTrue(hasattr(w, attr), f"existing attr {attr} missing")

    def test_new_extended_sections_exist(self):
        w = self._widget(self._tmp)
        # severity combos keyed by the curated rule-id tuples
        self.assertEqual(set(w.drc_combos), set(PSM.DRC_RULE_IDS))
        self.assertEqual(set(w.erc_combos), set(PSM.ERC_RULE_IDS))
        self.assertEqual(w.tv_table.columnCount(), 2)
        self.assertEqual(w.tw_table.columnCount(), 1)
        self.assertEqual(w.via_table.columnCount(), 2)
        self.assertEqual(w.dp_table.columnCount(), 3)
        self.assertEqual(set(w.dnc_spins), set(w.dnc_checks))
        self.assertIn("clearance", w.dnc_spins)

    def test_backend_modules_reachable(self):
        # the board-setup backend is imported into the widget module
        self.assertTrue(hasattr(self.KT, "board_setup"))
        self.assertTrue(hasattr(self.KT.board_setup, "set_board_setup"))
        self.assertTrue(hasattr(self.KT.board_setup, "get_board_setup"))
        # the extended constants used to build the UI came through the import
        self.assertEqual(self.KT.SEVERITY_LEVELS, PSM.SEVERITY_LEVELS)
        self.assertEqual(self.KT.DRC_RULE_IDS, PSM.DRC_RULE_IDS)
        self.assertEqual(self.KT.ERC_RULE_IDS, PSM.ERC_RULE_IDS)
        self.assertEqual(self.KT.ERC_PIN_TYPES, PSM.ERC_PIN_TYPES)

    # ── _ps_populate_extended -> the manager's additive mutators ─────────────
    def test_populate_extended_reaches_mutators(self):
        w = self._widget(self._tmp)
        w.drc_combos["clearance"].setCurrentText("error")
        w.erc_combos["pin_not_driven"].setCurrentText("warning")
        w._tv_add(); w.tv_table.item(0, 0).setText("REV"); w.tv_table.item(0, 1).setText("B2")
        w._tw_add(); w.tw_table.item(0, 0).setText("0.35")
        w._via_add(); w.via_table.item(0, 0).setText("0.9"); w.via_table.item(0, 1).setText("0.45")
        w._dp_add()
        w.dnc_checks["via_diameter"].setChecked(True)
        w.dnc_spins["via_diameter"].setValue(0.85)

        m = PSM.ProjectSettingsManager()
        w._ps_populate_extended(m)
        self.assertEqual(m.drc_severities["clearance"], "error")
        self.assertEqual(m.erc_severities["pin_not_driven"], "warning")
        self.assertEqual(m.text_variables["REV"], "B2")
        self.assertIn(0.35, m.track_widths)
        self.assertTrue(any(abs(v.diameter - 0.9) < 1e-9 for v in m.via_dimensions))
        self.assertTrue(m.diff_pair_dimensions)
        self.assertAlmostEqual(m.default_netclass.via_diameter, 0.85)

    def test_populate_extended_preserve_by_default(self):
        # Nothing touched -> nothing managed (no manufactured defaults).
        w = self._widget(self._tmp)
        m = PSM.ProjectSettingsManager()
        w._ps_populate_extended(m)
        self.assertEqual(m.drc_severities, {})
        self.assertEqual(m.erc_severities, {})
        self.assertEqual(m.text_variables, {})
        self.assertEqual(m.track_widths, [])
        self.assertEqual(m.via_dimensions, [])
        self.assertEqual(m.diff_pair_dimensions, [])
        self.assertFalse(m.default_netclass.is_managed())

    # ── _ps_load_extended_into_widgets round-trips load_extended() ───────────
    def test_load_extended_into_widgets(self):
        pro = _make_project(self._tmp, extended=True)
        w = self._widget(self._tmp)
        m = PSM.ProjectSettingsManager()
        self.assertTrue(m.load_extended(pro))
        w._ps_load_extended_into_widgets(m)
        self.assertEqual(w.drc_combos["clearance"].currentText(), "warning")
        self.assertEqual(w.drc_combos["hole_to_hole"].currentText(), "error")
        self.assertEqual(w.erc_combos["pin_not_driven"].currentText(), "ignore")
        # an unmanaged rule stays on the inherit sentinel
        self.assertEqual(w.drc_combos["silk_overlap"].currentText(), w.PS_UNMANAGED)
        # text vars + tables populated (leading 0.0 'use net class' rows skipped)
        names = {w.tv_table.item(r, 0).text() for r in range(w.tv_table.rowCount())}
        self.assertEqual(names, {"REV", "AUTHOR"})
        self.assertEqual(w.tw_table.rowCount(), 2)          # 0.3, 0.4 (0.0 skipped)
        self.assertEqual(w.via_table.rowCount(), 1)         # (0.9,0.45); zero row skipped
        self.assertEqual(w.dp_table.rowCount(), 1)

    # ── _ps_write_board_setup -> the board .kicad_pcb (setup) block ──────────
    def test_write_board_setup_mils_to_mm(self):
        pro = _make_project(self._tmp, name="brd", with_board=True)
        w = self._widget(self._tmp)
        w.ps_spins["solder_mask_clearance"].setValue(4.0)   # mils -> 0.1016 mm
        w.ps_spins["solder_paste_margin"].setValue(-3.0)    # mils -> -0.0762 mm
        results = w._ps_write_board_setup([pro])
        self.assertTrue(results and all(results.values()))
        setup = BS.load_board_setup(pro.parent / "brd.kicad_pcb")
        self.assertAlmostEqual(setup["pad_to_mask_clearance"], 0.1016, places=4)
        self.assertAlmostEqual(setup["pad_to_paste_clearance"], -0.0762, places=4)
        # existing setup key preserved, .bak written
        self.assertTrue((pro.parent / "brd.kicad_pcb.bak").exists())

    def test_write_board_setup_no_boards_is_safe(self):
        pro = _make_project(self._tmp, name="noboard", with_board=False)
        w = self._widget(self._tmp)
        self.assertEqual(w._ps_write_board_setup([pro]), {})   # no boards -> {}

    # ── _ps_load end-to-end through the real project list (single project) ───
    def test_ps_load_button_path(self):
        _make_project(self._tmp, name="loadme", extended=True)
        w = self._widget(self._tmp)                # rescan() checks the one project
        self.assertEqual(len(w.selected_pro_files()), 1)
        w._ps_load()                               # no dialog on the single-project path
        self.assertEqual(w.drc_combos["clearance"].currentText(), "warning")
        self.assertEqual(w.erc_combos["pin_not_driven"].currentText(), "ignore")
        self.assertTrue(w.tv_table.rowCount() >= 2)

    # ── _ps_sync end-to-end: flat + extended + board setup in one shot ───────
    def test_ps_sync_button_path_writes_everything(self):
        pro = _make_project(self._tmp, name="syncme", with_board=True)
        w = self._widget(self._tmp)
        self.assertEqual(len(w.selected_pro_files()), 1)
        # set some extended state + board mask/paste, then Sync
        w.drc_combos["clearance"].setCurrentText("error")
        w.erc_combos["unannotated"].setCurrentText("warning")
        w._tv_add(); w.tv_table.item(0, 0).setText("BUILD"); w.tv_table.item(0, 1).setText("42")
        w.dnc_checks["track_width"].setChecked(True)
        w.dnc_spins["track_width"].setValue(0.3)
        w.ps_spins["solder_mask_clearance"].setValue(4.0)
        w._ps_sync()                               # _FakeMsg auto-confirms; runs inline

        raw = json.loads(pro.read_text(encoding="utf-8"))
        self.assertEqual(raw["board"]["design_settings"]["rule_severities"]["clearance"], "error")
        self.assertEqual(raw["erc"]["rule_severities"]["unannotated"], "warning")
        self.assertEqual(raw["text_variables"]["BUILD"], "42")
        default_nc = next(c for c in raw["net_settings"]["classes"] if c["name"] == "Default")
        self.assertAlmostEqual(default_nc["track_width"], 0.3, places=4)
        # board (setup ...) got the solder-mask value KiCad actually reads
        setup = BS.load_board_setup(pro.parent / "syncme.kicad_pcb")
        self.assertAlmostEqual(setup["pad_to_mask_clearance"], 0.1016, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
