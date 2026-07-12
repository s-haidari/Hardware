"""LIB-03: humanized + technical part names.

`part_display_names(row)` turns a grouped-library row into the two names the
Library UI shows side by side:
  - 'humanized'  — the plain-words 'what it IS', the Mouser Description verbatim
                   when present, else the least-machine name we have.
  - 'technical'  — the manufacturer part number (or the raw symbol name for a
                   generic passive with no real MPN).

Pure logic: no GUI, no git, no Mouser network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def test_description_becomes_humanized_name_verbatim():
    row = {"name": "1043_KEY", "mpn": "PTS645SM43SMTR92",
           "description": "Tactile Switches 6mm SPST-NO 50mA 12V"}
    names = L.part_display_names(row)
    assert names["humanized"] == "Tactile Switches 6mm SPST-NO 50mA 12V"
    assert names["technical"] == "PTS645SM43SMTR92"


def test_falls_back_to_symbol_name_without_mouser_data():
    row = {"name": "1043_KEY", "mpn": "1043_KEY"}
    names = L.part_display_names(row)
    assert names["humanized"] == "1043_KEY"
    assert names["technical"] == "1043_KEY"


def test_technical_is_mpn_even_when_description_present():
    row = {"name": "U_REG", "mpn": "TPS2121RUXR", "description": "Power Mux 2.7-22V"}
    assert L.part_display_names(row)["technical"] == "TPS2121RUXR"


def test_generic_passive_without_real_mpn_uses_name_as_technical():
    # A passive whose 'mpn' fell back to the symbol name (no real MPN property).
    row = {"name": "R_0603_10k", "mpn": "R_0603_10k", "description": ""}
    names = L.part_display_names(row)
    assert names["humanized"] == "R_0603_10k"
    assert names["technical"] == "R_0603_10k"


def test_blank_description_is_ignored():
    row = {"name": "D1", "mpn": "S3B", "description": "   "}
    assert L.part_display_names(row)["humanized"] == "D1"


def test_empty_row_yields_empty_strings_not_crash():
    names = L.part_display_names({})
    assert names["humanized"] == ""
    assert names["technical"] == ""
    # An empty row has no identity to flag as un-orderable.
    assert names["orderable"] is False
    assert names["has_real_mpn"] is False


def test_humanized_and_technical_differ_flag_is_derivable():
    # When the two names are equal the UI shows only one line; verify the caller
    # can tell them apart cheaply.
    same = L.part_display_names({"name": "X", "mpn": "X"})
    assert same["humanized"] == same["technical"]
    diff = L.part_display_names({"name": "X", "mpn": "MPN1", "description": "A Widget"})
    assert diff["humanized"] != diff["technical"]


# ── one honest identity contract (LM:2006, LM:2129) ────────────────────────────

def test_explicit_flag_wins_over_inference_real_mpn():
    # A grouped/BOM row carries has_real_mpn straight from strict_mpn.
    row = {"name": "R1", "mpn": "CRCW040210K0", "has_real_mpn": True}
    names = L.part_display_names(row)
    assert names["has_real_mpn"] is True
    assert names["orderable"] is True
    assert names["flag"] == ""


def test_explicit_flag_no_mpn_shows_shared_not_orderable_flag():
    # A generic passive: strict_mpn found nothing, so the row is flagged not orderable
    # even though 'mpn' fell back to the symbol name for a human label.
    row = {"name": "R_0603_10k", "mpn": "R_0603_10k", "has_real_mpn": False}
    names = L.part_display_names(row)
    assert names["has_real_mpn"] is False
    assert names["orderable"] is False
    assert names["flag"] == L.NO_MPN_FLAG
    # The technical name is the honest value/symbol name — NOT a fabricated MPN.
    assert names["technical"] == "R_0603_10k"


def test_has_real_mpn_helper_reads_explicit_flag_from_row():
    assert L.has_real_mpn({"mpn": "TPS2121RUXR", "has_real_mpn": True}) is True
    assert L.has_real_mpn({"mpn": "R_0603_10k", "has_real_mpn": False}) is False


def test_has_real_mpn_infers_when_no_explicit_flag():
    # Minimal {name, mpn} row (no explicit flag): a real MPN differs from the name.
    assert L.has_real_mpn({"name": "U1", "mpn": "TPS2121RUXR"}) is True
    # ...and an mpn equal to the symbol-name fallback is the fallback, not a real MPN.
    assert L.has_real_mpn({"name": "R_0603_10k", "mpn": "R_0603_10k"}) is False
    # No mpn at all → not orderable.
    assert L.has_real_mpn({"name": "1043_KEY", "mpn": ""}) is False


def test_bom_line_with_real_mpn_is_orderable_no_flag():
    # A BOM row shape (from _bom_from_components): strict MPN present.
    row = {"mpn": "TPS2121RUXR", "value": "", "has_real_mpn": True, "name": ""}
    names = L.part_display_names(row)
    assert names["orderable"] is True and names["flag"] == ""


def test_bom_line_without_mpn_flagged_not_orderable():
    # A generic passive BOM row: no strict MPN, only a value.
    row = {"mpn": "", "value": "10k", "has_real_mpn": False, "name": ""}
    names = L.part_display_names(row)
    assert names["orderable"] is False
    assert names["flag"] == L.NO_MPN_FLAG
