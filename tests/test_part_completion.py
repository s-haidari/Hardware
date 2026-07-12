"""Backend tests for the 8-item completion passport (v2.11 Library redesign):

* part_completion(row) — the honest 8-item scorecard for one scan_library_grouped
  row: Symbol, Footprint, 3D Model, Part Number (real MPN), Manufacturer, Datasheet,
  Description, Category. Pure (row-only, no disk). Returns items + score + missing
  labels + dangling + is_complete. is_complete == all 8 present AND not dangling.

Rows are built from a REAL tmp library (no mocks), mirroring test_complete_part.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _symbol(name, footprint=None, props=None):
    lines = [f'  (symbol "{name}"']
    if footprint is not None:
        lines.append(f'    (property "Footprint" "MyFootprints:{footprint}")')
    for k, v in (props or {}).items():
        lines.append(f'    (property "{k}" "{v}")')
    lines.append("    (pin 1)")
    lines.append("  )")
    return "\n".join(lines) + "\n"


def _footprint(name, model_basename=None):
    inner = ""
    if model_basename is not None:
        inner = f'  (model "${{MY3DMODELS}}/{model_basename}"\n    (offset (xyz 0 0 0))\n  )\n'
    return f'(footprint "{name}" (layer "F.Cu")\n{inner})\n'


def _make_cfg(tmp_path, symbols_text, footprints, model_files):
    libs = tmp_path / "libs"
    fp_dir = libs / "MyFootprints.pretty"
    mdl_dir = libs / "My3DModels"
    fp_dir.mkdir(parents=True)
    mdl_dir.mkdir(parents=True)
    (libs / "MySymbols.kicad_sym").write_text(SYM_HEADER + symbols_text + ")\n", encoding="utf-8")
    for stem, model in footprints.items():
        (fp_dir / f"{stem}.kicad_mod").write_text(_footprint(stem, model), encoding="utf-8")
    for m in model_files:
        (mdl_dir / m).write_text("solid\n", encoding="utf-8")
    return {"Libs": str(libs), "SymbolLib": str(libs / "MySymbols.kicad_sym"),
            "FootprintLib": str(fp_dir), "ModelLib": str(mdl_dir)}


def _row(cfg, name):
    return {r["name"]: r for r in L.scan_library_grouped(cfg)}[name]


# Everything a part needs to be Complete (8/8).
_FULL = {"Manufacturer Part Number": "ADG714BRUZ", "MANUFACTURER": "Analog Devices",
         "Datasheet": "http://x/ds.pdf", "Description": "Quad SPST analog switch",
         "Category": "Analog Switch"}


def test_fully_complete_part_scores_8_of_8(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("ADG714", footprint="FP_A", props=_FULL),
                    {"FP_A": "m.step"}, ["m.step"])
    c = L.part_completion(_row(cfg, "ADG714"))
    assert c["total"] == 8
    assert c["score"] == 8
    assert c["missing"] == []
    assert c["is_complete"] is True


def test_missing_model_and_datasheet_scores_6_of_8(tmp_path):
    props = dict(_FULL)
    del props["Datasheet"]
    cfg = _make_cfg(tmp_path, _symbol("ADG714", footprint="FP_A", props=props),
                    {"FP_A": None}, [])          # footprint on disk, but no 3D model
    c = L.part_completion(_row(cfg, "ADG714"))
    assert c["score"] == 6
    assert set(c["missing"]) == {"3D Model", "Datasheet"}
    assert c["is_complete"] is False


def test_explicit_category_counts_as_the_eighth_item(tmp_path):
    props = dict(_FULL)
    del props["Category"]
    cfg = _make_cfg(tmp_path, _symbol("ADG714", footprint="FP_A", props=props),
                    {"FP_A": "m.step"}, ["m.step"])
    c = L.part_completion(_row(cfg, "ADG714"))
    assert c["score"] == 7
    assert c["missing"] == ["Category"]
    assert c["is_complete"] is False


def test_passive_without_a_real_mpn_is_missing_part_number(tmp_path):
    # A generic passive whose only identity is its Value carries no real MPN, so the
    # honest 'Part Number' item is absent (has_real_mpn is False).
    props = {"Value": "CC0402", "Description": "0402 100nF capacitor"}
    cfg = _make_cfg(tmp_path, _symbol("CC0402", footprint="FP_A", props=props),
                    {"FP_A": "m.step"}, ["m.step"])
    c = L.part_completion(_row(cfg, "CC0402"))
    assert "Part Number" in c["missing"]
    assert c["is_complete"] is False


def test_dangling_reference_is_flagged_and_blocks_complete(tmp_path):
    # The symbol references a footprint with no .kicad_mod on disk -> dangling.
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="GHOST", props=_FULL), {}, [])
    c = L.part_completion(_row(cfg, "U1"))
    assert c["dangling"] is True
    assert c["is_complete"] is False


def test_completion_badge_is_n_of_8_or_fix(tmp_path):
    cfg = _make_cfg(tmp_path / "a", _symbol("ADG714", footprint="FP_A", props=_FULL),
                    {"FP_A": "m.step"}, ["m.step"])
    assert L.completion_badge(_row(cfg, "ADG714")) == "8/8"
    props = dict(_FULL)
    del props["Datasheet"]
    cfg2 = _make_cfg(tmp_path / "b", _symbol("ADG714", footprint="FP_A", props=props),
                     {"FP_A": None}, [])
    assert L.completion_badge(_row(cfg2, "ADG714")) == "6/8"
    cfg3 = _make_cfg(tmp_path / "c", _symbol("U1", footprint="GHOST", props=_FULL), {}, [])
    assert L.completion_badge(_row(cfg3, "U1")) == "Fix"


def test_items_are_the_eight_in_files_then_identity_order(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("ADG714", footprint="FP_A", props=_FULL),
                    {"FP_A": "m.step"}, ["m.step"])
    c = L.part_completion(_row(cfg, "ADG714"))
    assert [it["label"] for it in c["items"]] == [
        "Symbol", "Footprint", "3D Model", "Part Number",
        "Manufacturer", "Datasheet", "Description", "Category"]
    assert all(it["present"] for it in c["items"])
