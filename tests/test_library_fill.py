"""Fill project component fields from the local Library (nd_library_fill).

Pure module: match project components against Library parts, build a reviewable
plan, and write MPN/manufacturer/datasheet/description/footprint safely into a
.kicad_sch (.bak + atomic). No Qt. TDD, task by task.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_library_fill as F  # noqa: E402
import nd_project_health as H  # noqa: E402
import LibraryManager as LM  # noqa: E402


# ── fixtures (real s-expr the actual helpers must parse) ─────────────────────

# A tiny symbol library: a generic resistor (Value 10k, footprint R_0402) and a
# real part carrying MPN/Manufacturer/Datasheet properties.
SYMBOL_LIB = '''(kicad_symbol_lib (version 20211014) (generator "test")
  (symbol "R_10k"
    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 0 2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "MyFootprints:R_0402_1005Metric" (at 0 -2 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "R_10k_0_1" (rectangle (start -1 -2.5) (end 1 2.5)))
  )
  (symbol "STM32F103C8T6"
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8T6" (at 0 2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "MyFootprints:LQFP-48_7x7mm_P0.5mm" (at 0 -2 0) (effects (font (size 1.27 1.27)) hide))
    (property "MPN" "STM32F103C8T6" (at 0 -4 0) (effects (font (size 1.27 1.27)) hide))
    (property "Manufacturer" "STMicroelectronics" (at 0 -6 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "https://st.com/stm32f103.pdf" (at 0 -8 0) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "ARM Cortex-M3 MCU 64KB flash" (at 0 -10 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "STM32F103C8T6_0_1" (rectangle (start -5 -5) (end 5 5)))
  )
)
'''


def _write_symbol_lib(tmp_path) -> str:
    p = tmp_path / "MySymbols.kicad_sym"
    p.write_text(SYMBOL_LIB, encoding="utf-8")
    return str(p)


# A schematic with a lib_symbols cache + two placed instances. R1 lacks MPN and
# Footprint; U1 is an STM32 already carrying its MPN. The cache symbols must NOT
# be treated as placed instances by the writer.
SCHEMATIC = '''(kicad_sch (version 20230121) (generator "eeschema")
  (lib_symbols
    (symbol "Device:R" (pin_numbers hide) (pin_names (offset 0))
      (property "Reference" "R" (at 2 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
      (symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54)))
      (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27)))
    )
    (symbol "MCU_ST_STM32F1:STM32F103C8Tx" (pin_names (offset 1.016))
      (property "Reference" "U" (at -12.7 22.86 0) (effects (font (size 1.27 1.27))))
      (property "Value" "STM32F103C8Tx" (at 10.16 22.86 0) (effects (font (size 1.27 1.27))))
      (symbol "STM32F103C8Tx_0_1" (rectangle (start -12.7 20.32) (end 12.7 -22.86)))
    )
  )
  (symbol (lib_id "MySymbols:R_10k") (at 100 100 0) (unit 1)
    (property "Reference" "R1" (at 102 98 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 102 102 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 100 100 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "~" (at 100 100 0) (effects (font (size 1.27 1.27)) hide))
    (instances (project "proj" (path "/" (reference "R1") (unit 1))))
  )
  (symbol (lib_id "MCU_ST_STM32F1:STM32F103C8Tx") (at 150 100 0) (unit 1)
    (property "Reference" "U1" (at 150 75 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8Tx" (at 150 125 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "MyFootprints:LQFP-48_7x7mm_P0.5mm" (at 150 100 0) (effects (font (size 1.27 1.27)) hide))
    (property "MPN" "STM32F103C8T6" (at 150 100 0) (effects (font (size 1.27 1.27)) hide))
    (instances (project "proj" (path "/" (reference "U1") (unit 1))))
  )
)
'''


def _write_schematic(tmp_path) -> str:
    p = tmp_path / "board.kicad_sch"
    p.write_text(SCHEMATIC, encoding="utf-8")
    return str(p)


# ── Task 1: library_parts ────────────────────────────────────────────────────

def test_library_parts_indexes_resistor_and_part(tmp_path):
    idx = F.library_parts({"SymbolLib": _write_symbol_lib(tmp_path)})
    by_name = {p["name"]: p for p in idx}
    assert set(by_name) == {"R_10k", "STM32F103C8T6"}

    r = by_name["R_10k"]
    assert r["value"] == "10k"
    assert r["footprint"] == "R_0402_1005Metric"      # nickname stripped (stem)
    assert r["mpn"] is None                            # generic passive, no strict MPN

    u = by_name["STM32F103C8T6"]
    assert u["mpn"] == "STM32F103C8T6"
    assert u["manufacturer"] == "STMicroelectronics"
    assert u["datasheet"] == "https://st.com/stm32f103.pdf"
    assert u["description"] == "ARM Cortex-M3 MCU 64KB flash"
    assert u["footprint"] == "LQFP-48_7x7mm_P0.5mm"


def test_library_parts_missing_lib_is_empty(tmp_path):
    assert F.library_parts({"SymbolLib": str(tmp_path / "nope.kicad_sym")}) == []
    assert F.library_parts({}) == []


# ── Task 2: match_component ──────────────────────────────────────────────────

def _lib_index(tmp_path):
    return F.library_parts({"SymbolLib": _write_symbol_lib(tmp_path)})


def test_match_exact_by_symbol_name(tmp_path):
    idx = _lib_index(tmp_path)
    comp = {"ref": "U1", "value": "STM32F103C8Tx",
            "footprint": "MyFootprints:LQFP-48_7x7mm_P0.5mm",
            "lib_id": "MCU_ST_STM32F1:STM32F103C8T6", "props": {"Reference": "U1"}}
    m = F.match_component(comp, idx)
    assert m["ref"] == "U1"
    assert m["confidence"] == "exact"
    assert m["lib_part"]["name"] == "STM32F103C8T6"


def test_match_exact_by_mpn(tmp_path):
    idx = _lib_index(tmp_path)
    # lib_id symbol name does NOT match any library part; the strict MPN does.
    comp = {"ref": "U1", "value": "MCU", "footprint": "",
            "lib_id": "Some:GenericMCU",
            "props": {"Reference": "U1", "MPN": "STM32F103C8T6"}}
    m = F.match_component(comp, idx)
    assert m["confidence"] == "exact"
    assert m["lib_part"]["name"] == "STM32F103C8T6"


def test_match_fuzzy_value_and_footprint(tmp_path):
    idx = _lib_index(tmp_path)
    # No symbol-name / MPN hit, but value "10K" + footprint stem match R_10k.
    comp = {"ref": "R1", "value": "10K",
            "footprint": "SomeLib:R_0402_1005Metric",
            "lib_id": "Device:R", "props": {"Reference": "R1", "Value": "10K"}}
    m = F.match_component(comp, idx)
    assert m["confidence"] == "verify"
    assert m["lib_part"]["name"] == "R_10k"
    assert m["alternatives"] == 0


def test_match_none_when_absent(tmp_path):
    idx = _lib_index(tmp_path)
    comp = {"ref": "C1", "value": "1uF", "footprint": "X:C_0805",
            "lib_id": "Device:C", "props": {"Reference": "C1"}}
    m = F.match_component(comp, idx)
    assert m["confidence"] == "none"
    assert m["lib_part"] is None


# ── Task 3: build_fill_plan ──────────────────────────────────────────────────

def _u1_comp():
    # Exact-by-symbol MCU. Missing manufacturer/datasheet/description; footprint
    # already set to the qualified library value; MPN already present + equal.
    return {"ref": "U1", "value": "STM32F103C8Tx",
            "footprint": "MyFootprints:LQFP-48_7x7mm_P0.5mm",
            "lib_id": "MCU_ST_STM32F1:STM32F103C8T6",
            "props": {"Reference": "U1", "MPN": "STM32F103C8T6",
                      "Footprint": "MyFootprints:LQFP-48_7x7mm_P0.5mm"}}


def _r1_comp():
    # Fuzzy-match resistor: value + footprint stem match R_10k, but the footprint
    # is UNqualified (no nickname) so the plan re-qualifies it to MyFootprints:...
    return {"ref": "R1", "value": "10k", "footprint": "R_0402_1005Metric",
            "lib_id": "Device:R",
            "props": {"Reference": "R1", "Value": "10k",
                      "Footprint": "R_0402_1005Metric", "Datasheet": "~"}}


def test_build_plan_blank_fills_are_default_selected_on_exact(tmp_path):
    idx = _lib_index(tmp_path)
    plan = F.build_fill_plan([_u1_comp()], idx, {"U1": "board.kicad_sch"})
    item = next(i for i in plan["items"] if i["ref"] == "U1")
    assert item["match"]["confidence"] == "exact"
    assert item["sheet"] == "board.kicad_sch"
    changes = {c["field"]: c for c in item["changes"]}
    # MPN + Footprint already correct -> no change proposed for them.
    assert "MPN" not in changes
    assert "Footprint" not in changes
    # Missing manufacturer/datasheet/description are blank-fills.
    assert changes["Manufacturer"]["new"] == "STMicroelectronics"
    assert changes["Manufacturer"]["kind"] == "fill"
    assert changes["Manufacturer"]["old"] == ""
    assert changes["Datasheet"]["kind"] == "fill"
    assert item["default_selected"] is True


def test_build_plan_footprint_qualified_and_fills_blank(tmp_path):
    idx = _lib_index(tmp_path)
    plan = F.build_fill_plan([_r1_comp()], idx, {"R1": "board.kicad_sch"})
    item = next(i for i in plan["items"] if i["ref"] == "R1")
    assert item["match"]["confidence"] == "verify"
    changes = {c["field"]: c for c in item["changes"]}
    assert changes["Footprint"]["new"] == "MyFootprints:R_0402_1005Metric"
    assert changes["Footprint"]["prop"] == "Footprint"
    # Old footprint was present but UNqualified -> re-qualifying is an overwrite.
    assert changes["Footprint"]["kind"] == "overwrite"
    assert changes["Footprint"]["old"] == "R_0402_1005Metric"
    # A "~" placeholder datasheet counts as blank -> fill (R_10k has no datasheet,
    # so no datasheet change is proposed at all here).
    assert "Datasheet" not in changes
    # verify match is never default-selected.
    assert item["default_selected"] is False


def test_build_plan_overwrite_is_flagged_and_not_default(tmp_path):
    idx = _lib_index(tmp_path)
    comp = _u1_comp()
    comp["props"]["Manufacturer"] = "Bogus Corp"           # real existing value
    plan = F.build_fill_plan([comp], idx, {"U1": "board.kicad_sch"})
    item = next(i for i in plan["items"] if i["ref"] == "U1")
    changes = {c["field"]: c for c in item["changes"]}
    assert changes["Manufacturer"]["kind"] == "overwrite"
    assert changes["Manufacturer"]["old"] == "Bogus Corp"
    # Any overwrite present -> exact match is no longer default-selected.
    assert item["default_selected"] is False


def test_build_plan_summary_counts(tmp_path):
    idx = _lib_index(tmp_path)
    comps = [_u1_comp(), _r1_comp(),
             {"ref": "C1", "value": "1u", "footprint": "X:C_0805",
              "lib_id": "Device:C", "props": {"Reference": "C1"}}]
    sheet_of = {"U1": "board.kicad_sch", "R1": "board.kicad_sch",
                "C1": "board.kicad_sch"}
    plan = F.build_fill_plan(comps, idx, sheet_of)
    s = plan["summary"]
    assert s["components"] == 3
    assert s["no_match"] == 1                               # C1
    assert s["need_review"] == 1                            # R1 (verify)
    assert s["fields"] == sum(len(i["changes"]) for i in plan["items"])


# ── Task 4: write_fields_to_sheet (.bak + atomic) ────────────────────────────

def test_write_inserts_and_updates_only_target_ref(tmp_path):
    sch = _write_schematic(tmp_path)
    n = F.write_fields_to_sheet(sch, {
        "R1": {"MPN": "RC0402FR-0710KL",
               "Footprint": "MyFootprints:R_0402_1005Metric",
               "Manufacturer": "Yageo"},
    })
    assert n == 1                                          # one component written

    comps = {c["ref"]: c for c in H.schematic_components(sch)}
    r1 = comps["R1"]
    assert r1["props"]["MPN"] == "RC0402FR-0710KL"         # inserted (was absent)
    assert r1["props"]["Footprint"] == "MyFootprints:R_0402_1005Metric"  # updated
    assert r1["props"]["Manufacturer"] == "Yageo"          # inserted
    # U1 untouched.
    assert "Manufacturer" not in comps["U1"]["props"]
    assert comps["U1"]["props"]["MPN"] == "STM32F103C8T6"


def test_write_never_edits_lib_symbols_cache(tmp_path):
    sch = _write_schematic(tmp_path)
    F.write_fields_to_sheet(sch, {"R1": {"MPN": "RC0402FR-0710KL"}})
    text = Path(sch).read_text(encoding="utf-8")
    cache = text[text.index("(lib_symbols"):text.index("(symbol (lib_id")]
    # The cache's Device:R symbol must NOT have gained an MPN property.
    assert "RC0402FR-0710KL" not in cache
    assert 'property "MPN"' not in cache


def test_write_makes_backup_with_original(tmp_path):
    sch = _write_schematic(tmp_path)
    original = Path(sch).read_text(encoding="utf-8")
    F.write_fields_to_sheet(sch, {"R1": {"MPN": "X"}}, backup=True)
    bak = Path(sch + ".bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original


def test_write_idempotent_and_absent_ref_is_noop(tmp_path):
    sch = _write_schematic(tmp_path)
    F.write_fields_to_sheet(sch, {"R1": {"MPN": "X"}})
    after_first = Path(sch).read_text(encoding="utf-8")
    # Re-run with the SAME change -> nothing new to write, file unchanged.
    n2 = F.write_fields_to_sheet(sch, {"R1": {"MPN": "X"}})
    assert n2 == 0
    assert Path(sch).read_text(encoding="utf-8") == after_first
    # A ref not present in the sheet is a no-op.
    n3 = F.write_fields_to_sheet(sch, {"R99": {"MPN": "Y"}})
    assert n3 == 0
    assert Path(sch).read_text(encoding="utf-8") == after_first


# ── Task 5: apply_fill_plan ──────────────────────────────────────────────────

class _Log:
    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(msg)


def test_apply_writes_only_selected_pairs_and_registers_fp(tmp_path, monkeypatch):
    sch = _write_schematic(tmp_path)
    idx = _lib_index(tmp_path)
    comps = H.schematic_components(sch)
    sheet_of = {c["ref"]: sch for c in comps}
    plan = F.build_fill_plan(comps, idx, sheet_of)

    # R1 (exact-by-symbol R_10k) only gains a Footprint fill; U1 gains sourcing
    # fields. Select R1's Footprint + U1's Manufacturer, across two components.
    selected = {("R1", "Footprint"), ("U1", "Manufacturer")}

    registered = {"called": False}
    monkeypatch.setattr(LM, "register_libraries",
                        lambda cfg, log: registered.__setitem__("called", True) or True)

    log = _Log()
    res = F.apply_fill_plan(plan, selected, {"SymbolLib": "x"}, log)

    assert res["components_changed"] == 2                   # R1 + U1
    assert res["fields_written"] == 2
    assert res["written_files"] == [sch]
    assert res["backups"] and res["backups"][0] == sch + ".bak"
    assert res["errors"] == []
    # Footprint was among the writes -> the footprint library is registered.
    assert registered["called"] is True

    comps_after = {c["ref"]: c for c in H.schematic_components(sch)}
    assert comps_after["R1"]["props"]["Footprint"] == "MyFootprints:R_0402_1005Metric"
    assert comps_after["U1"]["props"]["Manufacturer"] == "STMicroelectronics"
    # U1's Datasheet was NOT selected -> not written.
    assert "Datasheet" not in comps_after["U1"]["props"]


def test_apply_no_footprint_selected_does_not_register(tmp_path, monkeypatch):
    sch = _write_schematic(tmp_path)
    idx = _lib_index(tmp_path)
    comps = H.schematic_components(sch)
    sheet_of = {c["ref"]: sch for c in comps}
    plan = F.build_fill_plan(comps, idx, sheet_of)

    # U1: select only Manufacturer (a non-footprint field).
    selected = {("U1", "Manufacturer")}
    registered = {"called": False}
    monkeypatch.setattr(LM, "register_libraries",
                        lambda cfg, log: registered.__setitem__("called", True) or True)

    res = F.apply_fill_plan(plan, selected, {"SymbolLib": "x"}, _Log())
    assert res["fields_written"] == 1
    assert registered["called"] is False                   # no footprint -> no register

    u1 = {c["ref"]: c for c in H.schematic_components(sch)}["U1"]
    assert u1["props"]["Manufacturer"] == "STMicroelectronics"


def test_apply_empty_selection_is_noop(tmp_path):
    sch = _write_schematic(tmp_path)
    idx = _lib_index(tmp_path)
    comps = H.schematic_components(sch)
    plan = F.build_fill_plan(comps, idx, {c["ref"]: sch for c in comps})
    before = Path(sch).read_text(encoding="utf-8")
    res = F.apply_fill_plan(plan, set(), {"SymbolLib": "x"})
    assert res["fields_written"] == 0 and res["written_files"] == []
    assert Path(sch).read_text(encoding="utf-8") == before


# ── Task 6: end-to-end proof — fill lowers real audit findings ───────────────

# A schematic whose U1 exact-matches the Library STM32 by SYMBOL NAME, and
# starts with NO manufacturer/MPN (a `no_mpn` finding) and NO footprint (a
# `no_footprint` finding). After filling, both should drop.
E2E_SCHEMATIC = '''(kicad_sch (version 20230121) (generator "eeschema")
  (lib_symbols
    (symbol "MCU_ST_STM32F1:STM32F103C8T6" (pin_names (offset 1.016))
      (property "Reference" "U" (at -12.7 22.86 0) (effects (font (size 1.27 1.27))))
      (property "Value" "STM32F103C8T6" (at 10.16 22.86 0) (effects (font (size 1.27 1.27))))
      (symbol "STM32_0_1" (rectangle (start -12.7 20.32) (end 12.7 -22.86)))
    )
  )
  (symbol (lib_id "MCU_ST_STM32F1:STM32F103C8T6") (at 150 100 0) (unit 1)
    (property "Reference" "U1" (at 150 75 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8T6" (at 150 125 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 150 100 0) (effects (font (size 1.27 1.27)) hide))
    (instances (project "proj" (path "/" (reference "U1") (unit 1))))
  )
)
'''


def _no_kind(findings, kind):
    return sum(1 for f in findings if f["kind"] == kind)


def test_end_to_end_fill_lowers_audit_findings(tmp_path):
    sym = _write_symbol_lib(tmp_path)
    sch = tmp_path / "board.kicad_sch"
    sch.write_text(E2E_SCHEMATIC, encoding="utf-8")
    sch = str(sch)

    # Baseline audit: U1 has no MPN/manufacturer and no footprint.
    before = H.audit_project([sch])
    n_no_mpn_before = _no_kind(before["findings"], "no_mpn")
    n_no_fp_before = _no_kind(before["findings"], "no_footprint")
    assert n_no_mpn_before >= 1
    assert n_no_fp_before >= 1

    # Fill: index the Library, build the plan, apply all EXACT-match changes.
    idx = F.library_parts({"SymbolLib": sym})
    comps = H.schematic_components(sch)
    plan = F.build_fill_plan(comps, idx, {c["ref"]: sch for c in comps})
    selected = {(i["ref"], c["prop"]) for i in plan["items"]
                if i["match"]["confidence"] == "exact" for c in i["changes"]}
    assert selected                                       # there IS something to fill
    res = F.apply_fill_plan(plan, selected, {"SymbolLib": sym})
    assert res["fields_written"] >= 2                     # manufacturer + footprint

    # Re-audit the SAME file on disk: both finding counts drop.
    after = H.audit_project([sch])
    assert _no_kind(after["findings"], "no_mpn") < n_no_mpn_before
    assert _no_kind(after["findings"], "no_footprint") < n_no_fp_before

    # The change is real on disk: footprint is now the qualified library value.
    u1 = {c["ref"]: c for c in H.schematic_components(sch)}["U1"]
    assert u1["props"]["Footprint"] == "MyFootprints:LQFP-48_7x7mm_P0.5mm"
    assert u1["props"]["Manufacturer"] == "STMicroelectronics"


# ── completion passport (schematic-instance scoped, M0) ──────────────────────
def test_component_completion_measures_instance_fields():
    # A fully-filled component: footprint + real MPN + mfr + datasheet + description.
    full = {"ref": "U1", "footprint": "MyFootprints:STM32", "props": {
        "Reference": "U1", "Footprint": "MyFootprints:STM32",
        "MPN": "STM32F103C8T6", "Manufacturer": "ST",
        "Datasheet": "https://st.com/d.pdf", "Description": "MCU"}}
    c = F.component_completion(full)
    assert c["is_complete"] is True and c["score"] == 5 and c["missing"] == []
    # A bare resistor: footprint only, no identity yet.
    bare = {"ref": "R1", "footprint": "R_0402", "props": {
        "Reference": "R1", "Value": "10k", "Footprint": "R_0402"}}
    c2 = F.component_completion(bare)
    assert c2["is_complete"] is False and c2["score"] == 1
    assert "Part Number" in c2["missing"] and "Manufacturer" in c2["missing"]
    # A blank/placeholder footprint does not count as linked.
    none_fp = {"ref": "R2", "props": {"Reference": "R2", "Footprint": "~"}}
    assert F.component_completion(none_fp)["items"][0]["present"] is False


def test_project_completion_rolls_up_and_counts_missing():
    comps = [
        {"ref": "U1", "footprint": "fp", "props": {
            "MPN": "REAL-1", "Manufacturer": "ST", "Datasheet": "d", "Description": "x",
            "Footprint": "fp"}},
        {"ref": "R1", "footprint": "R_0402", "props": {"Footprint": "R_0402"}},
        {"ref": "R2", "footprint": "R_0402", "props": {"Footprint": "R_0402"}},
    ]
    roll = F.project_completion(comps)
    assert roll["total"] == 3 and roll["complete"] == 1
    assert set(roll["incomplete_refs"]) == {"R1", "R2"}
    # both resistors miss Part Number / Manufacturer / Datasheet / Description
    assert roll["missing_counts"]["Part Number"] == 2
    assert roll["missing_counts"]["Manufacturer"] == 2


# ── distributor enrichment (M1) ──────────────────────────────────────────────
def test_enrich_plan_fills_blanks_from_distributor():
    # A matched component with a real MPN but blank Manufacturer/Datasheet.
    comp = {"ref": "U1", "footprint": "fp", "props": {
        "Reference": "U1", "MPN": "STM32F103C8T6", "Footprint": "fp"}}
    plan = {"items": [{"ref": "U1", "sheet": "s.kicad_sch",
                       "match": {"confidence": "exact"}, "changes": [], "default_selected": True}],
            "summary": {"fields": 0}}
    calls = []
    def stub(mpn):
        calls.append(mpn)
        return {"mpn": mpn, "manufacturer": "STMicroelectronics",
                "datasheet": "https://st.com/d.pdf", "description": "ARM MCU"}
    out = F.enrich_plan(plan, [comp], cfg={}, sheet_of={"U1": "s.kicad_sch"}, lookup=stub)
    props = {c["prop"]: c for c in out["items"][0]["changes"]}
    assert props["Manufacturer"]["new"] == "STMicroelectronics"
    assert props["Manufacturer"]["source"] == "mouser"
    assert props["Datasheet"]["new"] == "https://st.com/d.pdf"
    assert props["Description"]["new"] == "ARM MCU"
    assert out["summary"]["enriched"] == 3 and calls == ["STM32F103C8T6"]


def test_enrich_plan_creates_item_for_unmatched_mpn_and_caches():
    # An unmatched component (no plan item) carrying an MPN gets a fresh item.
    comps = [
        {"ref": "U1", "props": {"Reference": "U1", "MPN": "PART-X"}},
        {"ref": "U2", "props": {"Reference": "U2", "MPN": "PART-X"}},   # same MPN -> one call
        {"ref": "R1", "props": {"Reference": "R1", "Value": "10k"}},    # no MPN -> skipped
    ]
    calls = []
    def stub(mpn):
        calls.append(mpn)
        return {"manufacturer": "Acme", "datasheet": "http://d", "description": "d"}
    out = F.enrich_plan({"items": [], "summary": {}}, comps, cfg={},
                        sheet_of={"U1": "a", "U2": "a"}, lookup=stub)
    refs = {it["ref"] for it in out["items"]}
    assert refs == {"U1", "U2"}                      # R1 (no MPN) got no item
    assert calls == ["PART-X"]                       # cached: one call for the shared MPN
    assert out["items"][0]["match"]["confidence"] == "mouser"


def test_enrich_plan_no_provider_is_noop(monkeypatch):
    # No distributor configured at all (no Mouser key AND LCSC disabled) -> a clean no-op,
    # never a network call. (An empty cfg would still return the key-free LCSC fallback, so
    # simulate the truly-no-provider case.)
    monkeypatch.setattr(LM, "providers_from_config", lambda cfg=None: None)
    plan = {"items": [], "summary": {}}
    assert F.enrich_plan(plan, [{"ref": "U1", "props": {"MPN": "X"}}], cfg={}) is plan
    assert plan["items"] == []


# ── passive grouping + manual merge (M3/M4 backend) ──────────────────────────
def test_passive_groups_by_value_and_footprint():
    comps = [
        {"ref": "R3", "value": "10k", "footprint": "MyFootprints:R_0402", "props": {"Value": "10k"}},
        {"ref": "R1", "value": "10k", "footprint": "MyFootprints:R_0402", "props": {"Value": "10k"}},
        {"ref": "R2", "value": "10k", "footprint": "R_0603", "props": {"Value": "10k"}},   # diff pkg
        {"ref": "C1", "value": "100n", "footprint": "R_0402", "props": {"Value": "100n"}},
        {"ref": "R9", "value": "1k", "footprint": "R_0402",
         "props": {"Value": "1k", "MPN": "RC0402-1K"}},                    # has MPN -> excluded
        {"ref": "U1", "value": "MCU", "footprint": "QFN", "props": {"Value": "MCU"}},        # not passive
    ]
    groups = F.passive_groups(comps)
    # 10k/R_0402 (R1,R3), 10k/R_0603 (R2), 100n/R_0402 (C1) — the 1k has an MPN, U1 isn't passive
    keys = {g["key"]: g for g in groups}
    tenk = next(g for g in groups if g["value"] == "10k" and g["footprint"] == "R_0402")
    assert tenk["refs"] == ["R1", "R3"] and tenk["label"].startswith("2× · 10k")
    assert "MPN" in tenk["missing"] and "Manufacturer" in tenk["missing"]
    assert all("R9" not in g["refs"] for g in groups)      # MPN'd passive excluded
    assert all("U1" not in g["refs"] for g in groups)      # non-passive excluded
    assert len(groups) == 3


def test_expand_group_fill_fans_out_to_all_refs():
    g = {"refs": ["R1", "R3"]}
    out = F.expand_group_fill(g, {"MPN": "RC0402-10K", "Manufacturer": "Yageo", "Datasheet": ""})
    assert out[("R1", "MPN")] == "RC0402-10K" and out[("R3", "MPN")] == "RC0402-10K"
    assert out[("R1", "Manufacturer")] == "Yageo"
    assert ("R1", "Datasheet") not in out                  # blank value skipped


def test_merge_manual_changes_adds_fieldchanges_and_items():
    comps = [{"ref": "J1", "props": {"Reference": "J1", "Value": "USB"}}]
    plan = {"items": [], "summary": {}}
    F.merge_manual_changes(plan, {("J1", "Datasheet"): "http://ds", ("J1", "MPN"): "USB-C-16"},
                           components=comps, sheet_of={"J1": "s.kicad_sch"}, source="manual")
    it = plan["items"][0]
    assert it["ref"] == "J1" and it["match"]["confidence"] == "manual"
    props = {c["prop"]: c for c in it["changes"]}
    assert props["Datasheet"]["new"] == "http://ds" and props["Datasheet"]["source"] == "manual"
    assert props["MPN"]["new"] == "USB-C-16"
    # merging the group-expand output writes every ref through the same path
    plan2 = {"items": [], "summary": {}}
    fills = F.expand_group_fill({"refs": ["R1", "R2"]}, {"MPN": "RC-10K"})
    F.merge_manual_changes(plan2, fills, components=[], sheet_of={"R1": "a", "R2": "a"},
                           source="group")
    assert {i["ref"] for i in plan2["items"]} == {"R1", "R2"}


# ── 3D-model verification in the passport (M2) ───────────────────────────────
def test_component_model_status_resolves_footprint_and_model(tmp_path):
    fp_dir = tmp_path / "fp.pretty"; fp_dir.mkdir()
    mdl_dir = tmp_path / "models"; mdl_dir.mkdir()
    # a footprint that references a model file
    (fp_dir / "R_0402.kicad_mod").write_text(
        '(footprint "R_0402" (pad "1" smd) (pad "2" smd)\n'
        '  (model "${MY3DMODELS}/R_0402.step" (offset (xyz 0 0 0))))\n', encoding="utf-8")
    cfg = {"FootprintLib": str(fp_dir), "ModelLib": str(mdl_dir)}
    comp = {"ref": "R1", "footprint": "MyFootprints:R_0402", "props": {"Footprint": "MyFootprints:R_0402"}}
    # model file missing -> no_model
    assert F.component_model_status(comp, cfg) == "no_model"
    (mdl_dir / "R_0402.step").write_text("solid", encoding="utf-8")
    assert F.component_model_status(comp, cfg) == "ok"
    # unresolved footprint
    bad = {"ref": "R2", "footprint": "MyFootprints:NOPE", "props": {"Footprint": "MyFootprints:NOPE"}}
    assert F.component_model_status(bad, cfg) == "unresolved_fp"
    # no footprint at all
    assert F.component_model_status({"ref": "R3", "props": {}}, cfg) == "no_footprint"


def test_component_completion_adds_model_dimension_only_with_cfg(tmp_path):
    fp_dir = tmp_path / "fp.pretty"; fp_dir.mkdir()
    (fp_dir / "R_0402.kicad_mod").write_text('(footprint "R_0402" (pad "1" smd))\n', encoding="utf-8")
    cfg = {"FootprintLib": str(fp_dir), "ModelLib": str(tmp_path / "m")}
    comp = {"ref": "U1", "footprint": "MyFootprints:R_0402", "props": {
        "Footprint": "MyFootprints:R_0402", "MPN": "X", "Manufacturer": "A",
        "Datasheet": "d", "Description": "z"}}
    # no cfg -> 5 fields, complete (M0 behaviour preserved)
    assert F.component_completion(comp)["total"] == 5
    assert F.component_completion(comp)["is_complete"] is True
    # with cfg -> 6 fields incl the (missing) 3D model, so NOT complete
    c = F.component_completion(comp, cfg)
    assert c["total"] == 6 and "3D Model" in c["missing"] and c["is_complete"] is False
