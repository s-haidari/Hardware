"""LIB-05: the Mouser autofill preview dialog.

The dialog previews current-vs-Mouser values, lets the user pick a mode
(fill-blanks / overwrite-all) or toggle fields manually, and yields the plan to
apply. Built headless; never exec()'d in tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

ROW = {"mpn": "TPS2121RUXR", "manufacturer": "TI", "description": "",
       "datasheet": None, "mouser_pn": None}
FETCHED = {"description": "Power Mux 2.7-22V", "manufacturer": "Texas Instruments",
           "datasheet": "https://ti.com/ds.pdf", "mpn": "TPS2121RUXR",
           "mouser_pn": "595-TPS2121RUXR"}


def test_dialog_defaults_to_fill_blanks():
    from ui.features.library_preview import _AutofillDialog
    dlg = _AutofillDialog(ROW, FETCHED)
    # blanks default: only the empty fields are planned; manufacturer (has a
    # value) and mpn (identical) are left alone.
    assert dlg.plan() == {"description": "Power Mux 2.7-22V",
                          "datasheet": "https://ti.com/ds.pdf",
                          "mouser_pn": "595-TPS2121RUXR"}


def test_dialog_overwrite_mode_adds_differing_fields():
    from ui.features.library_preview import _AutofillDialog
    dlg = _AutofillDialog(ROW, FETCHED)
    dlg.set_mode("overwrite")
    p = dlg.plan()
    assert p["manufacturer"] == "Texas Instruments"
    assert "mpn" not in p, "identical value is never rewritten"


def test_dialog_identical_field_gets_no_row():
    # mpn is identical between row and fetched -> not an actionable candidate.
    from ui.features.library_preview import _AutofillDialog
    dlg = _AutofillDialog(ROW, FETCHED)
    assert "mpn" not in dlg._checks


def test_dialog_manual_toggle_excludes_field():
    from ui.features.library_preview import _AutofillDialog
    dlg = _AutofillDialog(ROW, FETCHED)
    dlg._checks["description"].setChecked(False)
    assert "description" not in dlg.plan()
    assert "datasheet" in dlg.plan()
