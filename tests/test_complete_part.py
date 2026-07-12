"""Backend tests for the 'Complete This Part' orchestration:

* complete_part_plan(cfg, row, fetched) — everything the action COULD do for one part,
  as data-only op descriptors the UI renders with checkboxes (safe ops pre-checked,
  risky overwrites unchecked). `fetched` (a distributor result) is injectable so tests
  need no network. Pure: reads disk to find name-match candidates, writes nothing.
* apply_complete_part(cfg, row, ops, selected_keys, log) — execute the selected ops
  (create-symbol → link-footprint → link-model → fills), returning {applied, errors}.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _symbol(name, footprint=None, **props):
    lines = [f'  (symbol "{name}"']
    if footprint is not None:
        lines.append(f'    (property "Footprint" "MyFootprints:{footprint}")')
    for k, v in props.items():
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


def _sym_prop(cfg, sym_name, prop):
    """Read a property straight off the symbol block (the real write target) — more
    precise than the scan-derived row, which only surfaces some identity fields."""
    for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"]))):
        if L.extract_symbol_name(b) == sym_name:
            return L.extract_symbol_properties(b).get(prop, "")
    return None


def _by_key(ops):
    return {o["key"]: o for o in ops}


# ---------------------------------------------------------------------------
# complete_part_plan
# ---------------------------------------------------------------------------
def test_plan_fill_blank_from_fetched_is_safe(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"), {"FP_A": "m.step"}, ["m.step"])
    row = _row(cfg, "U1")
    fetched = {"datasheet": "http://d/ds.pdf", "manufacturer": "Acme"}
    ops = _by_key(L.complete_part_plan(cfg, row, fetched))
    assert "fill:datasheet" in ops and ops["fill:datasheet"]["safe"] is True
    assert ops["fill:datasheet"]["value"] == "http://d/ds.pdf"
    assert ops["fill:datasheet"]["kind"] == "fill_field"


def test_plan_overwrite_existing_is_risky(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A", MANUFACTURER="OldCo"),
                    {"FP_A": "m.step"}, ["m.step"])
    row = _row(cfg, "U1")
    fetched = {"manufacturer": "NewCo"}
    ops = _by_key(L.complete_part_plan(cfg, row, fetched))
    assert "fill:manufacturer" in ops
    assert ops["fill:manufacturer"]["safe"] is False        # overwriting a real value
    assert ops["fill:manufacturer"]["value"] == "NewCo"


def test_plan_link_footprint_by_name_match(tmp_path):
    # MYCAP has no footprint but its Value matches a footprint stem on disk -> safe
    # link op. (Distinct symbol/footprint names avoid the same-name orphan collision.)
    cfg = _make_cfg(tmp_path, _symbol("MYCAP", Value="CC0402"), {"CC0402": None}, [])
    row = _row(cfg, "MYCAP")
    ops = _by_key(L.complete_part_plan(cfg, row))
    assert "link_footprint" in ops
    assert ops["link_footprint"]["value"] == "CC0402" and ops["link_footprint"]["safe"] is True


def test_plan_link_model_by_name_match(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="TPS2121"), {"TPS2121": None},
                    ["TPS2121.step"])
    row = _row(cfg, "U1")
    ops = _by_key(L.complete_part_plan(cfg, row))
    assert "link_model" in ops
    assert ops["link_model"]["value"] == "TPS2121.step" and ops["link_model"]["safe"] is True


def test_plan_create_symbol_for_orphan(tmp_path):
    cfg = _make_cfg(tmp_path, "", {"FP_A": None}, [])
    row = _row(cfg, "FP_A")
    ops = _by_key(L.complete_part_plan(cfg, row))
    assert "create_symbol" in ops and ops["create_symbol"]["safe"] is True


def test_plan_no_fetched_no_fill_ops(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"), {"FP_A": "m.step"}, ["m.step"])
    ops = L.complete_part_plan(cfg, _row(cfg, "U1"), fetched=None)
    assert not any(o["kind"] == "fill_field" for o in ops)


# ---------------------------------------------------------------------------
# apply_complete_part
# ---------------------------------------------------------------------------
def test_apply_fill_writes_property(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"), {"FP_A": "m.step"}, ["m.step"])
    row = _row(cfg, "U1")
    ops = L.complete_part_plan(cfg, row, {"datasheet": "http://d/ds.pdf"})
    res = L.apply_complete_part(cfg, row, ops, ["fill:datasheet"], L._NullLog())
    assert res["applied"] and not res["errors"]
    assert _sym_prop(cfg, "U1", "Datasheet") == "http://d/ds.pdf"


def test_apply_link_footprint(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("MYCAP", Value="CC0402"), {"CC0402": None}, [])
    row = _row(cfg, "MYCAP")
    ops = L.complete_part_plan(cfg, row)
    L.apply_complete_part(cfg, row, ops, ["link_footprint"], L._NullLog())
    assert _row(cfg, "MYCAP")["has_footprint"] is True


def test_apply_link_model_persists_model_line(tmp_path):
    # The footprint file starts with NO (model) line; apply must PERSIST one so the
    # model travels with the footprint when the files are handed to someone else.
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="TPS2121"), {"TPS2121": None},
                    ["TPS2121.step"])
    row = _row(cfg, "U1")
    ops = L.complete_part_plan(cfg, row)
    L.apply_complete_part(cfg, row, ops, ["link_model"], L._NullLog())
    fp_text = L.read_text(Path(cfg["FootprintLib"]) / "TPS2121.kicad_mod")
    assert L.footprint_model_ref(fp_text) == "TPS2121.step"   # physically linked now


def test_apply_create_symbol(tmp_path):
    cfg = _make_cfg(tmp_path, "", {"FP_A": None}, [])
    row = _row(cfg, "FP_A")
    ops = L.complete_part_plan(cfg, row)
    res = L.apply_complete_part(cfg, row, ops, ["create_symbol"], L._NullLog())
    assert res["applied"]
    assert _row(cfg, "FP_A")["has_symbol"] is True


def test_apply_only_selected_ops(tmp_path):
    cfg = _make_cfg(tmp_path, _symbol("U1", footprint="FP_A"), {"FP_A": "m.step"}, ["m.step"])
    row = _row(cfg, "U1")
    ops = L.complete_part_plan(cfg, row, {"datasheet": "d", "manufacturer": "Acme"})
    # select only datasheet; manufacturer must stay blank
    L.apply_complete_part(cfg, row, ops, ["fill:datasheet"], L._NullLog())
    assert _sym_prop(cfg, "U1", "Datasheet") == "d"
    assert not _sym_prop(cfg, "U1", "MANUFACTURER")
