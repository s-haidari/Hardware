"""Backend tests for the Library CRUD + completeness additions:

* part_missing(row)  — the honest per-part completeness report (item/why/how_to_fix),
  composed from a scan_library_grouped row's presence + identity flags. Feeds the
  "Complete This Part" and Fix-All completeness dialogs.
* remove_footprint(cfg, stem, log) — delete a .kicad_mod, reporting who still refs it.
* remove_model(cfg, name, log)     — delete a 3D model file, reporting who still refs it.
* remove_part(cfg, row, log, *, delete_footprint, delete_model) — delete a whole part
  (its symbol(s) + optionally the footprint/model file), reporting dangling leftovers.

Pure/logic tests: no GUI, no git shelled out. Deletes snapshot to libs/.trash first.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _symbol(name: str, footprint: str = None, **props) -> str:
    """A minimal (symbol …) block. `footprint` adds a Footprint prop pointing at
    MyFootprints:<footprint>; **props adds arbitrary identity properties."""
    lines = [f'  (symbol "{name}"']
    if footprint is not None:
        lines.append(f'    (property "Footprint" "MyFootprints:{footprint}")')
    for k, v in props.items():
        lines.append(f'    (property "{k}" "{v}")')
    lines.append("    (pin 1)")
    lines.append("  )")
    return "\n".join(lines) + "\n"


def _footprint(name: str, model_basename: str = None) -> str:
    inner = ""
    if model_basename is not None:
        inner = (f'  (model "${{MY3DMODELS}}/{model_basename}"\n'
                 "    (offset (xyz 0 0 0))\n  )\n")
    return f'(footprint "{name}" (layer "F.Cu")\n{inner})\n'


def _make_cfg(tmp_path, symbols_text, footprints, model_files):
    libs = tmp_path / "libs"
    fp_dir = libs / "MyFootprints.pretty"
    mdl_dir = libs / "My3DModels"
    fp_dir.mkdir(parents=True)
    mdl_dir.mkdir(parents=True)
    sym_path = libs / "MySymbols.kicad_sym"
    sym_path.write_text(SYM_HEADER + symbols_text + ")\n", encoding="utf-8")
    for stem, model_basename in footprints.items():
        (fp_dir / f"{stem}.kicad_mod").write_text(_footprint(stem, model_basename),
                                                  encoding="utf-8")
    for m in model_files:
        (mdl_dir / m).write_text("solid\n", encoding="utf-8")
    return {"Libs": str(libs), "SymbolLib": str(sym_path),
            "FootprintLib": str(fp_dir), "ModelLib": str(mdl_dir)}


# ---------------------------------------------------------------------------
# part_missing — the completeness contract
# ---------------------------------------------------------------------------
def _items(missing):
    return [m["item"] for m in missing]


def test_part_missing_complete_part_reports_nothing():
    row = {
        "name": "U1", "symbols": ["U1"], "footprint": "FP_A", "model": "m.step",
        "has_symbol": True, "has_footprint": True, "has_model": True,
        "dangling": False, "has_real_mpn": True,
        "mpn": "REAL-1", "manufacturer": "Acme", "datasheet": "http://d", "description": "A part",
    }
    assert L.part_missing(row) == []


def test_part_missing_footprint_only_orphan_wants_a_symbol():
    row = {
        "name": "FP_A", "symbols": [], "footprint": "FP_A", "model": None,
        "has_symbol": False, "has_footprint": True, "has_model": False,
        "dangling": False, "has_real_mpn": False,
    }
    items = _items(L.part_missing(row))
    assert "Symbol" in items
    # identity blanks are NOT reported for a symbol-less orphan (fields live on symbols)
    assert "Manufacturer" not in items and "Part Number" not in items


def test_part_missing_dangling_footprint_reference():
    row = {
        "name": "U2", "symbols": ["U2"], "footprint": "FP_MISSING", "model": None,
        "has_symbol": True, "has_footprint": False, "has_model": False,
        "dangling": True, "has_real_mpn": True, "mpn": "X",
        "manufacturer": "M", "datasheet": "d", "description": "x",
    }
    m = {x["item"]: x for x in L.part_missing(row)}
    assert "Footprint" in m
    assert "FP_MISSING" in m["Footprint"]["why"]          # names the dangling ref


def test_part_missing_no_model_and_no_mpn():
    row = {
        "name": "U3", "symbols": ["U3"], "footprint": "FP_A", "model": None,
        "has_symbol": True, "has_footprint": True, "has_model": False,
        "dangling": False, "has_real_mpn": False,
        "mpn": "U3", "manufacturer": "", "datasheet": "", "description": "",
    }
    items = _items(L.part_missing(row))
    assert "3D Model" in items
    assert "Part Number" in items          # no real MPN -> not orderable
    assert "Manufacturer" in items and "Datasheet" in items and "Description" in items


def test_part_missing_every_item_has_why_and_how():
    row = {
        "name": "FP_X", "symbols": [], "footprint": "FP_X", "model": None,
        "has_symbol": False, "has_footprint": True, "has_model": False,
        "dangling": False, "has_real_mpn": False,
    }
    for entry in L.part_missing(row):
        assert entry["item"] and entry["why"] and entry["how_to_fix"]


def test_part_missing_integrates_with_scan_library_grouped(tmp_path):
    cfg = _make_cfg(
        tmp_path,
        _symbol("U1", footprint="FP_A", **{"Manufacturer Part Number": "REAL-1",
                                           "MANUFACTURER": "Acme", "Datasheet": "http://d",
                                           "Description": "A complete part"}),
        {"FP_A": "m.step"}, ["m.step"])
    row = {r["name"]: r for r in L.scan_library_grouped(cfg)}["U1"]
    assert L.part_missing(row) == []       # fully complete -> nothing missing


# ---------------------------------------------------------------------------
# remove_footprint
# ---------------------------------------------------------------------------
def test_remove_footprint_deletes_file_and_reports_refs(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"),
                    {"FP_A": None, "FP_B": None}, [])
    res = L.remove_footprint(cfg, "FP_A", L._NullLog())
    assert res["ok"] and res["removed"]
    assert not (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()
    assert res["referenced_by"] == ["U1"]         # U1 still points at FP_A (now dangling)


def test_remove_footprint_unreferenced(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"), {"FP_B": None}, [])
    res = L.remove_footprint(cfg, "FP_B", L._NullLog())
    assert res["ok"] and res["referenced_by"] == []


def test_remove_footprint_missing_is_not_ok(tmp_path):
    cfg = _make_cfg(tmp_path, "", {"FP_A": None}, [])
    res = L.remove_footprint(cfg, "NOPE", L._NullLog())
    assert res["ok"] is False and res["removed"] is False and res["reason"]


# ---------------------------------------------------------------------------
# remove_model
# ---------------------------------------------------------------------------
def test_remove_model_deletes_file_and_reports_refs(tmp_path):
    cfg = _make_cfg(tmp_path, "", {"FP_A": "m.step"}, ["m.step"])
    res = L.remove_model(cfg, "m.step", L._NullLog())
    assert res["ok"] and res["removed"]
    assert not (Path(cfg["ModelLib"]) / "m.step").exists()
    assert res["referenced_by"] == ["FP_A"]       # FP_A's (model …) line now dangles


def test_remove_model_missing_is_not_ok(tmp_path):
    cfg = _make_cfg(tmp_path, "", {"FP_A": None}, [])
    res = L.remove_model(cfg, "ghost.step", L._NullLog())
    assert res["ok"] is False and res["reason"]


# ---------------------------------------------------------------------------
# remove_part
# ---------------------------------------------------------------------------
def test_remove_part_full_delete(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"),
                    {"FP_A": "m.step"}, ["m.step"])
    row = {r["name"]: r for r in L.scan_library_grouped(cfg)}["U1"]
    res = L.remove_part(cfg, row, L._NullLog(), delete_footprint=True, delete_model=True)
    assert res["ok"]
    assert res["symbols_removed"] == ["U1"]
    assert res["footprint_removed"] == "FP_A"
    assert res["model_removed"] == "m.step"
    assert not (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()
    assert not (Path(cfg["ModelLib"]) / "m.step").exists()
    # symbol gone from the library
    assert "U1" not in {L.extract_symbol_name(b)
                        for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"])))}


def test_remove_part_symbol_only_keeps_assets(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"),
                    {"FP_A": "m.step"}, ["m.step"])
    row = {r["name"]: r for r in L.scan_library_grouped(cfg)}["U1"]
    res = L.remove_part(cfg, row, L._NullLog(),
                        delete_footprint=False, delete_model=False)
    assert res["symbols_removed"] == ["U1"]
    assert res["footprint_removed"] is None and res["model_removed"] is None
    assert (Path(cfg["FootprintLib"]) / "FP_A.kicad_mod").exists()
    assert (Path(cfg["ModelLib"]) / "m.step").exists()


def test_remove_part_shared_model_reports_still_referenced(tmp_path):
    # Two DIFFERENT footprints (two parts) reference the same m.step. Deleting part
    # U1 (with its model) must warn that FP_B still references m.step (now dangling).
    cfg = _make_cfg(tmp_path,
                    _symbol("U1", footprint="FP_A") + _symbol("U2", footprint="FP_B"),
                    {"FP_A": "m.step", "FP_B": "m.step"}, ["m.step"])
    row = {r["name"]: r for r in L.scan_library_grouped(cfg)}["U1"]
    res = L.remove_part(cfg, row, L._NullLog(), delete_footprint=True, delete_model=True)
    assert res["footprint_removed"] == "FP_A"
    assert res["model_removed"] == "m.step"
    # FP_A is gone; FP_B still points at the deleted model -> reported as dangling.
    assert res["still_referenced"].get("model", {}).get("m.step") == ["FP_B"]


def test_remove_part_still_referenced_footprint_defensive(tmp_path):
    # Defensive contract: if a caller hands a row whose `symbols` omits an external
    # symbol that references the footprint, remove_part must still flag it dangling.
    cfg = _make_cfg(tmp_path,
                    _symbol("U1", footprint="FP_A") + _symbol("KEEP", footprint="FP_A"),
                    {"FP_A": None}, [])
    row = {"name": "U1", "symbols": ["U1"], "footprint": "FP_A", "model": None}
    res = L.remove_part(cfg, row, L._NullLog(), delete_footprint=True)
    assert res["footprint_removed"] == "FP_A"
    assert res["still_referenced"].get("footprint", {}).get("FP_A") == ["KEEP"]
