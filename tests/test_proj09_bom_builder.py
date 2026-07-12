"""PROJ-08/09: a real BOM builder — multi-sheet aggregation, Library/Mouser part-
number enrichment, and basic-part detection (standard passives identified by value)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def _sym(ref, value, lib="Device:R", mpn=None, mfr=None, fp=""):
    props = [f'(property "Reference" "{ref}")', f'(property "Value" "{value}")']
    if fp:
        props.append(f'(property "Footprint" "{fp}")')
    if mpn:
        props.append(f'(property "MPN" "{mpn}")')
    if mfr:
        props.append(f'(property "MANUFACTURER" "{mfr}")')
    return f'(symbol (lib_id "{lib}") ' + " ".join(props) + ')'


def _sch(*syms):
    return "(kicad_sch " + " ".join(syms) + ")"


# ── basic-part detection ─────────────────────────────────────────────────────
def test_is_basic_part_true_for_valued_passive():
    assert L.is_basic_part("R5", "10k", None) is True
    assert L.is_basic_part("C12", "100nF", "") is True
    assert L.is_basic_part("L1", "4.7uH", None) is True


def test_is_basic_part_false_when_mpn_present_or_not_passive():
    assert L.is_basic_part("R5", "10k", "RC0402FR-0710KL") is False   # has a real MPN
    assert L.is_basic_part("U3", "STM32", None) is False              # not a passive
    assert L.is_basic_part("R5", "", None) is False                   # no value


def test_bom_rows_carry_basic_flag(tmp_path):
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("R1", "10k"), _sym("U1", "TPS2121", lib="Device:U",
                                              mpn="TPS2121RUXR", mfr="TI")))
    bom = L.bom_from_kicad_schematic(str(f))
    by_ref = {r["refs"][0]: r for r in bom["rows"]}
    assert by_ref["R1"]["basic"] is True
    assert by_ref["U1"]["basic"] is False


# ── multi-sheet aggregation (PROJ-08) ────────────────────────────────────────
def test_bom_from_project_merges_sheets(tmp_path):
    a = tmp_path / "a.kicad_sch"; a.write_text(_sch(_sym("R1", "10k")))
    b = tmp_path / "b.kicad_sch"; b.write_text(_sch(_sym("R2", "10k"), _sym("C1", "1uF")))
    bom = L.bom_from_project([str(a), str(b)])
    # R1 + R2 are the same value/footprint -> one line, qty 2; C1 -> its own line.
    tenk = [r for r in bom["rows"] if r["value"] == "10k"][0]
    assert tenk["qty"] == 2 and set(tenk["refs"]) == {"R1", "R2"}
    assert bom["component_count"] == 3


# ── Library enrichment (PROJ-09) ─────────────────────────────────────────────
def test_bom_does_not_merge_different_manufacturers(tmp_path):
    # CRITICAL: two same-value/footprint resistors from DIFFERENT manufacturers
    # must not collapse into one line (one manufacturer would be silently dropped).
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("R1", "10k", mfr="Yageo"), _sym("R2", "10k", mfr="Vishay")))
    bom = L.bom_from_kicad_schematic(str(f))
    mfrs = {r["manufacturer"] for r in bom["rows"] if r["value"] == "10k"}
    assert mfrs == {"Yageo", "Vishay"}


def test_bom_never_puts_value_in_mpn_for_a_passive(tmp_path):
    # CRITICAL: a passive's value ("10k") must never appear in the MPN column, and
    # the part stays 'basic' even once a manufacturer is filled in.
    f = tmp_path / "s.kicad_sch"; f.write_text(_sch(_sym("R1", "10k", mfr="Yageo")))
    r = L.bom_from_kicad_schematic(str(f))["rows"][0]
    assert r["mpn"] == ""
    assert r["basic"] is True


def test_bom_ic_value_is_mpn_when_manufacturer_present(tmp_path):
    # An IC (non-passive) that carries its MPN in the Value field + a manufacturer
    # should still surface that MPN.
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("U1", "TPS2121RUXR", lib="Device:U", mfr="TI")))
    r = L.bom_from_kicad_schematic(str(f))["rows"][0]
    assert r["mpn"] == "TPS2121RUXR" and r["basic"] is False


def test_bom_rows_carry_honest_identity_contract(tmp_path):
    # LM:2129: every BOM line carries has_real_mpn, and part_display_names reads the
    # SAME contract as the Library — a passive is flagged not orderable, an MPN'd part
    # is orderable with no flag.
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("R1", "10k"),
                      _sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))
    by_ref = {r["refs"][0]: r for r in L.bom_from_kicad_schematic(str(f))["rows"]}

    assert by_ref["R1"]["has_real_mpn"] is False
    assert by_ref["R1"]["mpn"] == ""
    r1 = L.part_display_names(by_ref["R1"])
    assert r1["orderable"] is False and r1["flag"] == L.NO_MPN_FLAG

    assert by_ref["U1"]["has_real_mpn"] is True
    u1 = L.part_display_names(by_ref["U1"])
    assert u1["orderable"] is True and u1["flag"] == ""


def test_consolidated_bom_rows_carry_honest_identity_contract(tmp_path):
    a = tmp_path / "a.kicad_sch"
    a.write_text(_sch(_sym("R1", "10k"),
                      _sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))
    res = L.consolidated_bom({"Board A": [str(a)]})
    by = {(r.get("mpn") or r.get("value")): r for r in res["rows"]}
    assert by["10k"]["has_real_mpn"] is False
    assert L.part_display_names(by["10k"])["flag"] == L.NO_MPN_FLAG
    assert by["TPS2121RUXR"]["has_real_mpn"] is True
    assert L.part_display_names(by["TPS2121RUXR"])["flag"] == ""


def test_bom_enriches_manufacturer_from_lookup(tmp_path):
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("U1", "TPS2121RUXR", lib="Device:U", mpn="TPS2121RUXR")))
    lookup = lambda mpn: {"manufacturer": "Texas Instruments"} if mpn == "TPS2121RUXR" else None
    bom = L.bom_from_kicad_schematic(str(f), lookup=lookup)
    u1 = [r for r in bom["rows"] if r["refs"] == ["U1"]][0]
    assert u1["manufacturer"] == "Texas Instruments"
