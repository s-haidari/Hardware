"""LIB-05: MPN autofill planning.

`autofill_plan(row, fetched, mode)` decides which identity fields a Mouser
lookup would write, under three modes:
  - 'blanks'    — fill only fields that are currently empty
  - 'overwrite' — replace every field Mouser has a value for
  - 'manual'    — caller supplies an explicit allow-set of row keys

It returns {row_key: new_value} for exactly the fields that would change, so a
no-op field (same value) is never rewritten. Pure: no GUI, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


FETCHED = {
    "description": "Power Mux 2.7-22V",
    "manufacturer": "Texas Instruments",
    "datasheet": "https://ti.com/ds.pdf",
    "mpn": "TPS2121RUXR",
    "mouser_pn": "595-TPS2121RUXR",
}


def test_blanks_mode_fills_only_empty_fields():
    row = {"mpn": "TPS2121RUXR", "manufacturer": "TI", "description": "",
           "datasheet": None, "mouser_pn": None}
    plan = L.autofill_plan(row, FETCHED, "blanks")
    # description/datasheet/mouser_pn were blank -> filled; mpn/manufacturer kept.
    assert plan == {
        "description": "Power Mux 2.7-22V",
        "datasheet": "https://ti.com/ds.pdf",
        "mouser_pn": "595-TPS2121RUXR",
    }


def test_overwrite_mode_replaces_differing_fields_only():
    row = {"mpn": "TPS2121RUXR", "manufacturer": "TI", "description": "old",
           "datasheet": None, "mouser_pn": None}
    plan = L.autofill_plan(row, FETCHED, "overwrite")
    # mpn is identical -> not rewritten; everything else differs -> included.
    assert "mpn" not in plan
    assert plan["manufacturer"] == "Texas Instruments"
    assert plan["description"] == "Power Mux 2.7-22V"
    assert plan["datasheet"] == "https://ti.com/ds.pdf"
    assert plan["mouser_pn"] == "595-TPS2121RUXR"


def test_manual_mode_honours_explicit_allow_set():
    row = {"mpn": "", "manufacturer": "", "description": "", "datasheet": None,
           "mouser_pn": None}
    plan = L.autofill_plan(row, FETCHED, "manual", allow={"manufacturer", "datasheet"})
    assert plan == {"manufacturer": "Texas Instruments",
                    "datasheet": "https://ti.com/ds.pdf"}


def test_fetched_missing_value_is_never_written():
    row = {"mpn": "", "manufacturer": "", "description": "", "datasheet": None,
           "mouser_pn": None}
    plan = L.autofill_plan(row, {"manufacturer": "TI"}, "blanks")
    assert plan == {"manufacturer": "TI"}


def test_no_changes_yields_empty_plan():
    row = dict(FETCHED)   # already identical to fetched
    assert L.autofill_plan(row, FETCHED, "overwrite") == {}
    assert L.autofill_plan(row, FETCHED, "blanks") == {}


def test_autofill_fields_spec_is_exposed():
    # The UI dialog builds its rows from this spec: (row_key, symbol_prop, label).
    spec = L.AUTOFILL_FIELDS
    keys = {f[0] for f in spec}
    assert {"description", "manufacturer", "datasheet", "mpn", "mouser_pn"} <= keys
    # Each entry maps a row key to the symbol property that persists it.
    by_key = {f[0]: f for f in spec}
    # The MPN persists to a DEDICATED strict-MPN property, NEVER to 'Value' — writing an
    # MPN over a passive's Value ('10k') would corrupt the electrical value and leave the
    # part grouping as a bare passive in the BOM (LIB-05 autofill corruption fix).
    assert by_key["mpn"][1] == "Manufacturer Part Number"
    assert by_key["mpn"][1] != "Value"
    assert by_key["mouser_pn"][1] == "Mouser Part Number"
    # The chosen property must normalize to a strict-MPN key so the written value is
    # recognized as a real manufacturer part number by part_identity / strict_mpn.
    norm = by_key["mpn"][1].lower().replace(" ", "").replace("_", "").replace("-", "")
    assert norm in L._MPN_KEYS_STRICT
