#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the audit fixes in tools/kicad_tools.py.

Covers the pure, GUI-independent logic that the fixes factored out:
  * pick_root_schematic  — non-interactive root-sheet auto-pick for ERC
    (replaces the input()-driven nd_wizard.pick_top_schematic that hung the
    worker thread).
  * sort_netclass_snapshots / _nc_priority_sort_key — loss-free in-place row
    reorder for the Net Class table (blanks stay blank, dup/empty names survive).
  * the junction_size int-coercion contract that _ps_sync relies on.

The Qt-only wiring (dispatch through _run_heavy, per-project sync logging,
exactly-one-project guards, the QTableWidget reorder itself) is verified only by
py_compile + a headless import; those paths can't be exercised without a running
QApplication and event loop.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import kicad_tools as KT  # noqa: E402


# --------------------------------------------------------------------------
# imports — the module is pure-stdlib now (no dead PyQt5 / nd_* / ui_theme)
# --------------------------------------------------------------------------
def test_module_carries_no_dead_gui_or_nd_imports():
    # The old KiCad Tools dialog is gone; the surviving helpers are pure
    # path/sort/kicad-cli code. None of the deleted dialog's heavy imports
    # (PyQt5 widgets, ui_theme/lucide, the nd_* backend modules) or its dead
    # module-level constants may leak back into the namespace, or importing this
    # worker-thread-safe module would drag in Qt again.
    dead = (
        # PyQt5 widget/gui/core classes the dialog used
        "QWidget", "QFrame", "QVBoxLayout", "QHBoxLayout", "QLabel",
        "QPushButton", "QLineEdit", "QComboBox", "QCheckBox", "QListWidget",
        "QTableWidget", "QTableWidgetItem", "QFileDialog", "QMessageBox",
        "QApplication", "QColorDialog", "QDialog", "QDialogButtonBox",
        "QColor", "QIcon", "QPixmap", "QPainter", "QSvgRenderer",
        "Qt", "pyqtSignal",
        # ui_theme / lucide design-system pulls
        "ui_theme", "_lucide", "_LU_NEUTRAL", "_LU_BLUE", "_LU_GREEN",
        "_LU_RED", "_LU_AMBER",
        # nd_* backend modules + their re-exported symbols
        "wiz", "kchecks", "phealth", "fabp", "conform", "ncm",
        "NetClass", "NetClassManager", "create_vault_standard_template",
        "load_vault_standard", "save_vault_standard",
        "ProjectSettings", "ProjectSettingsManager", "mils_to_mm",
        "SEVERITY_LEVELS", "DRC_RULE_IDS", "ERC_RULE_IDS", "ERC_PIN_TYPES",
        "board_setup",
        # dead module-level constants
        "_NO_WINDOW", "_HAVE_QTSVG",
        # stdlib pulls only the deleted code needed
        "sys", "subprocess",
    )
    leaked = [name for name in dead if hasattr(KT, name)]
    assert not leaked, f"dead symbols still bound in kicad_tools: {leaked}"


def test_module_imports_without_qt(monkeypatch):
    # A worker thread must be able to import this module even if PyQt5 is
    # unavailable — proves the dead Qt imports are truly gone, not just unused.
    import importlib
    monkeypatch.setitem(sys.modules, "PyQt5", None)          # force ImportError
    monkeypatch.setitem(sys.modules, "PyQt5.QtWidgets", None)
    monkeypatch.setitem(sys.modules, "PyQt5.QtGui", None)
    monkeypatch.setitem(sys.modules, "PyQt5.QtCore", None)
    monkeypatch.setitem(sys.modules, "ui_theme", None)
    monkeypatch.delitem(sys.modules, "kicad_tools", raising=False)
    mod = importlib.import_module("kicad_tools")
    # the real helpers are still present and callable after a Qt-less import
    assert callable(mod.discover_kicad_projects)
    assert callable(mod.pick_root_schematic)
    # restore the normally-imported module for the rest of the suite
    monkeypatch.undo()
    importlib.reload(importlib.import_module("kicad_tools"))


# --------------------------------------------------------------------------
# module docstring — describes the ACTUAL module, not the dead dialog
# --------------------------------------------------------------------------
def test_module_docstring_drops_dead_dialog_narrative():
    doc = KT.__doc__ or ""
    # The old dialog and its tabs no longer exist in this module — the docstring
    # must not advertise them.
    for stale in ("KiCad Tools dialog", "Bulk Rename Wizard",
                  "Net Class Manager", "Project Settings"):
        assert stale not in doc, f"stale dialog reference still in docstring: {stale!r}"


def test_module_docstring_names_the_real_helpers():
    doc = KT.__doc__ or ""
    # Every public helper the module actually exposes should be documented, so
    # the docstring stays a faithful map of the module.
    for name in ("discover_kicad_projects", "project_pro_file",
                 "pick_root_schematic", "sort_netclass_snapshots",
                 "wiz_find_kicad_cli"):
        assert callable(getattr(KT, name)), f"missing helper: {name}"
        assert name in doc, f"helper not documented in docstring: {name}"


# --------------------------------------------------------------------------
# pick_root_schematic — non-interactive, never calls input()
# --------------------------------------------------------------------------
def test_pick_root_empty_returns_none():
    assert KT.pick_root_schematic([]) is None
    assert KT.pick_root_schematic([], Path("p/board.kicad_pro")) is None


def test_pick_root_prefers_stem_match_beside_pro():
    pro = Path("proj/board.kicad_pro")
    schs = [
        Path("proj/power.kicad_sch"),
        Path("proj/board.kicad_sch"),      # <- root sheet: stem == pro stem, same dir
        Path("proj/sub/board.kicad_sch"),  # stem matches but wrong dir
    ]
    assert KT.pick_root_schematic(schs, pro) == Path("proj/board.kicad_sch")


def test_pick_root_beside_match_wins_over_subfolder_stem_match():
    # A schematic sitting NEXT TO the .kicad_pro whose stem matches must beat a
    # same-stem schematic buried in a subfolder.
    pro = Path("proj/board.kicad_pro")
    schs = [Path("proj/sub/board.kicad_sch"), Path("proj/board.kicad_sch")]
    assert KT.pick_root_schematic(schs, pro) == Path("proj/board.kicad_sch")


def test_pick_root_stem_match_anywhere_when_not_beside():
    pro = Path("proj/board.kicad_pro")
    schs = [Path("proj/main.kicad_sch"), Path("proj/sub/board.kicad_sch")]
    # no stem match beside pro; stem match anywhere is next best
    assert KT.pick_root_schematic(schs, pro) == Path("proj/sub/board.kicad_sch")


def test_pick_root_beside_pro_when_no_stem_match():
    pro = Path("proj/board.kicad_pro")
    schs = [Path("proj/sub/deep.kicad_sch"), Path("proj/alpha.kicad_sch"),
            Path("proj/beta.kicad_sch")]
    # no stem match anywhere -> prefer a schematic directly beside the pro,
    # alphabetical tie-break
    assert KT.pick_root_schematic(schs, pro) == Path("proj/alpha.kicad_sch")


def test_pick_root_shallowest_fallback_without_pro():
    schs = [Path("proj/sub/deep/x.kicad_sch"), Path("proj/top.kicad_sch"),
            Path("proj/sub/mid.kicad_sch")]
    # no pro hint -> shallowest path wins
    assert KT.pick_root_schematic(schs) == Path("proj/top.kicad_sch")


def test_pick_root_accepts_string_paths():
    # list_schematics yields Paths, but be defensive about str input too.
    got = KT.pick_root_schematic(["proj/board.kicad_sch", "proj/other.kicad_sch"],
                                 "proj/board.kicad_pro")
    assert got == Path("proj/board.kicad_sch")


# --------------------------------------------------------------------------
# sort_netclass_snapshots — loss-free reorder of raw row snapshots
# --------------------------------------------------------------------------
def _snap(name, priority, **extra):
    d = {"name": name, "priority": priority}
    d.update(extra)
    return d


def test_priority_sort_key_blank_is_zero():
    assert KT._nc_priority_sort_key({"priority": "", "name": "a"})[0] == 0.0
    assert KT._nc_priority_sort_key({"priority": None, "name": "a"})[0] == 0.0
    assert KT._nc_priority_sort_key({"name": "a"})[0] == 0.0            # missing


def test_priority_sort_key_non_numeric_is_zero():
    assert KT._nc_priority_sort_key({"priority": "high", "name": "a"})[0] == 0.0


def test_sort_orders_by_priority_then_name():
    snaps = [_snap("GND", "0"), _snap("HS", "3"), _snap("PWR", "1"),
             _snap("aux", "1")]
    out = KT.sort_netclass_snapshots(snaps)
    # priority ascending; case-insensitive name tie-break ("aux" < "pwr")
    assert [s["name"] for s in out] == ["GND", "aux", "PWR", "HS"]


def test_sort_preserves_duplicate_names():
    # Two rows named "PWR" must BOTH survive the reorder (the old manager path
    # collapsed same-named rows into one).
    snaps = [_snap("PWR", "2", track_width="0.3"),
             _snap("GND", "0"),
             _snap("PWR", "1", track_width="0.5")]
    out = KT.sort_netclass_snapshots(snaps)
    names = [s["name"] for s in out]
    assert names.count("PWR") == 2
    assert names == ["GND", "PWR", "PWR"]
    # the two PWR rows keep their distinct payloads
    pwr_widths = {s["track_width"] for s in out if s["name"] == "PWR"}
    assert pwr_widths == {"0.3", "0.5"}


def test_sort_preserves_empty_name_and_blank_cells():
    # An empty-name row and blank numeric cells must survive verbatim (the old
    # path dropped blank-name rows and back-filled blank numerics with defaults).
    snaps = [_snap("PWR", "1", clearance="0.2"),
             _snap("", "0", clearance="")]           # empty name, blank clearance
    out = KT.sort_netclass_snapshots(snaps)
    assert len(out) == 2
    blank_row = [s for s in out if s["name"] == ""][0]
    assert blank_row["clearance"] == ""              # stayed blank, not defaulted
    assert out[0]["name"] == ""                      # priority 0 sorts first


def test_sort_is_stable_and_returns_snapshots_untouched():
    a = _snap("x", "1", tag="a")
    b = _snap("x", "1", tag="b")
    out = KT.sort_netclass_snapshots([a, b])
    # equal keys -> stable order preserved; objects returned intact
    assert out == [a, b]
    assert out[0]["tag"] == "a" and out[1]["tag"] == "b"


# --------------------------------------------------------------------------
# junction_size int-coercion contract that _ps_sync relies on
# --------------------------------------------------------------------------
def test_junction_size_is_int_typed_field():
    import nd_project_settings_manager as PSM
    s = PSM.ProjectSettings()
    # _ps_sync coerces spin-box floats to int only for int-typed fields; this is
    # the field that must be caught (isinstance int, not bool).
    assert isinstance(s.junction_size, int) and not isinstance(s.junction_size, bool)
    # a representative distance field stays float and must NOT be coerced
    assert isinstance(s.schematic_text_size, float)


def test_junction_size_written_as_int_after_coercion(tmp_path):
    import nd_project_settings_manager as PSM
    pro = tmp_path / "J.kicad_pro"
    pro.write_text(json.dumps({"schematic": {"drawing": {}}}), encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    # emulate _ps_sync: spin box yields a float; the fix rounds int-typed fields.
    m.settings.junction_size = int(round(35.6))
    assert m.save_to_project(pro, backup=False)
    raw = json.loads(pro.read_text(encoding="utf-8"))
    stored = raw["schematic"]["drawing"]["default_junction_size"]
    assert stored == 36
    assert isinstance(stored, int) and not isinstance(stored, bool)
