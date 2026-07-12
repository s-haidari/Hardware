"""Unit tests for the pure logic in LibraryManager.

These cover symbol parsing/merge/dedup, single-copy deletion, footprint/model
overwrite protection, path derivation, and library scanning/filtering — i.e.
everything that can be verified without spinning up the Qt GUI.

Run:  python -m pytest tests -q
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import LibraryManager as L  # noqa: E402


class DummyLog:
    """Stand-in for UILog that just records messages."""
    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(msg)


SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def make_symlib(path, names):
    body = "".join(f'  (symbol "{n}" (pin 1))\n' for n in names)
    path.write_text(SYM_HEADER + body + ")\n", encoding="utf-8")


def names_in(path):
    blocks = L.extract_symbol_blocks(path.read_text(encoding="utf-8"))
    return [L.extract_symbol_name(b) for b in blocks]


# --- symbol parsing -------------------------------------------------------
def test_extract_blocks_and_names(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B", "C"])
    assert names_in(sym) == ["A", "B", "C"]


def test_extract_symbol_name_strips_lib_prefix():
    block = '(symbol "Device:R" (pin 1))'
    assert L.extract_symbol_name(block) == "R"


# --- merge dedup ----------------------------------------------------------
def test_merge_symbols_skips_existing(tmp_path):
    target = tmp_path / "lib.kicad_sym"
    make_symlib(target, ["A"])
    src = tmp_path / "src.kicad_sym"
    make_symlib(src, ["A", "B"])          # A already present, B is new
    log = DummyLog()
    L.merge_symbols(target, [src], log)
    assert sorted(names_in(target)) == ["A", "B"]   # exactly one A


# --- single-copy delete ---------------------------------------------------
def test_remove_symbol_by_index_keeps_twin(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B", "A"])     # duplicate A at index 0 and 2
    log = DummyLog()
    assert L.remove_symbol_by_index(sym, 2, log, expected_name="A") is True
    assert names_in(sym) == ["A", "B"]    # one A survives


def test_remove_symbol_by_index_stale_guard(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B"])
    log = DummyLog()
    # name mismatch -> abort, file unchanged
    assert L.remove_symbol_by_index(sym, 0, log, expected_name="ZZZ") is False
    assert names_in(sym) == ["A", "B"]


# --- multi-select bulk delete --------------------------------------------
def test_remove_symbols_by_indices_bulk(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B", "A", "C", "A"])   # A at indices 0, 2, 4
    log = DummyLog()
    # delete the two later A copies in one pass (no index-shift bug)
    removed = L.remove_symbols_by_indices(sym, {2: "A", 4: "A"}, log)
    assert removed == 2
    assert names_in(sym) == ["A", "B", "C"]


def test_remove_symbols_by_indices_stale_aborts(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B", "C"])
    log = DummyLog()
    removed = L.remove_symbols_by_indices(sym, {1: "WRONG"}, log)
    assert removed == 0
    assert names_in(sym) == ["A", "B", "C"]


# --- KiCad Tools integration (folded-in NETDECK features) ----------------
def test_discover_kicad_projects_generic(tmp_path):
    import kicad_tools as KT
    (tmp_path / "projA").mkdir()
    (tmp_path / "projA" / "a.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / "sub" / "projB").mkdir(parents=True)
    (tmp_path / "sub" / "projB" / "b.kicad_pro").write_text("{}", encoding="utf-8")
    (tmp_path / ".history").mkdir()
    (tmp_path / ".history" / "old.kicad_pro").write_text("{}", encoding="utf-8")
    found = sorted(p.name for p in KT.discover_kicad_projects(tmp_path))
    assert found == ["projA", "projB"]   # nested found, .history ignored


def test_nd_wizard_add_tag_refs(tmp_path):
    import nd_wizard as wiz
    sch = tmp_path / "x.kicad_sch"
    sch.write_text('(kicad_sch\n  (property "Reference" "R1")\n  (property "Reference" "U2")\n)\n',
                   encoding="utf-8")
    counts, _, _ = wiz.schematic_preview_and_apply(
        sch, "add_tag", "SH-", apply=True, touch_refs=True, touch_labels=False)
    assert counts["symbol_ref"] == 2
    txt = sch.read_text(encoding="utf-8")
    assert "SH-R1" in txt and "SH-U2" in txt


# --- extended net-class / project-settings round-trips -------------------
def test_netclass_microvia_roundtrip():
    import nd_netclass_manager as NCM
    nc = NCM.NetClass(name="X", microvia_diameter=0.25, microvia_drill=0.12, diff_pair_via_gap=0.3)
    d = nc.to_kicad_dict()
    assert d["microvia_diameter"] == 0.25 and d["microvia_drill"] == 0.12
    assert d["diff_pair_via_gap"] == 0.3
    back = NCM.NetClass.from_kicad_dict("X", d)
    assert back.microvia_diameter == 0.25 and back.diff_pair_via_gap == 0.3


def test_netclass_patterns_roundtrip(tmp_path):
    import json
    import nd_netclass_manager as NCM
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [{"name": "Default"}]}}), encoding="utf-8")
    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="PWR", patterns=["/VDD*", "GND"]))
    assert m.save_to_project(pro, backup=False)
    m2 = NCM.NetClassManager(); m2.load_from_project(pro)
    assert sorted(m2.get_netclass("PWR").patterns) == ["/VDD*", "GND"]


def test_vault_template_matches_obsidian_spec():
    import nd_netclass_manager as NCM
    m = NCM.create_vault_standard_template()      # default = OSH Park 4-layer profile
    names = m.list_netclasses()
    # 19 classes now (added TGT_CORE for the VCAP 1.2V regulator nodes); patterns
    # extended to fully cover the STM32 authority nets (JTAG TDI/NTRST, VCAP, the
    # ADG714 control bus). Was 18/43 before the 2026-07-05 coverage pass.
    assert len(names) == 19
    assert sum(len(m.get_netclass(n).patterns) for n in names) >= 43
    g = m.get_netclass("GND")
    assert (g.track_width, g.via_diameter, g.via_drill) == (0.25, 0.6, 0.3)
    s = m.get_netclass("SENSE")   # 4-layer signal floor
    assert (s.track_width, s.via_diameter, s.via_drill) == (0.15, 0.4572, 0.254)
    u = m.get_netclass("USB")
    assert (u.diff_pair_width, u.diff_pair_gap) == (0.20, 0.15)
    assert m.get_netclass("FAULT").line_style == "dashed"
    assert m.get_netclass("GND").priority == 0   # GND = highest precedence
    assert "TGT_CORE" in names                    # VCAP regulator-node class


# --- audit tier-1 regressions --------------------------------------------
def test_extract_blocks_escaped_quote(tmp_path):
    # KiCad writes inch marks inside strings as an escaped quote (0.1\").
    # The old scanner mis-paired quotes here and could infinite-loop the GUI.
    text = ('(kicad_symbol_lib (version 20211014) (generator "t")\n'
            '  (symbol "A" (property "Note" "gap 0.1\\" wide") (pin 1))\n'
            '  (symbol "B" (pin 1))\n)\n')
    blocks = L.extract_symbol_blocks(text)
    assert [L.extract_symbol_name(b) for b in blocks] == ["A", "B"]
    assert 'gap 0.1\\" wide' in blocks[0]


def test_extract_blocks_unbalanced_terminates():
    # Missing a closing paren must return (not hang). Reaching this assert = no loop.
    assert L.extract_symbol_blocks('(symbol "X" (pin 1)') == []


def test_strip_all_label_preserves_net_names():
    import nd_wizard as wiz
    # Labels: only a real tag prefix is removed; the net body is never truncated
    # (the old path turned I2C1_SDA -> C1_SDA and USART2_TX -> T2_TX).
    assert wiz.strip_all_label_tags("I2C1_SDA") == "I2C1_SDA"
    assert wiz.strip_all_label_tags("USART2_TX") == "USART2_TX"
    assert wiz.strip_all_label_tags("SH-I2C1_SDA") == "I2C1_SDA"
    assert wiz.strip_all_label_tags("CG-SH-NET1") == "NET1"      # stacked prefixes
    # Component references still use the designator-aware strip.
    assert wiz.strip_all_tags("SH-R1") == "R1"
    assert wiz.strip_all_tags("CG-U5") == "U5"


def test_preview_returns_change_records(tmp_path):
    # kicad_tools' rename audit iterates `for (t, o, n, f) in changes`; guard that
    # contract (the 3rd return is a list of 4-tuples) so the NameError fix stays valid.
    import nd_wizard as wiz
    sch = tmp_path / "z.kicad_sch"
    sch.write_text('(kicad_sch\n  (property "Reference" "R1")\n)\n', encoding="utf-8")
    counts, samples, changes = wiz.schematic_preview_and_apply(
        sch, "add_tag", "SH-", apply=False, touch_refs=True, touch_labels=False)
    assert isinstance(changes, list)
    assert all(len(rec) == 4 for rec in changes)
    assert any(o == "R1" and n == "SH-R1" for (_t, o, n, _f) in changes)


def test_project_settings_min_constraints_roundtrip(tmp_path):
    import json
    import nd_project_settings_manager as PSM
    pro = tmp_path / "B.kicad_pro"
    pro.write_text(json.dumps({"board": {"design_settings": {"rules": {}}}}), encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    m.settings.min_hole_to_hole = 9.0
    m.settings.min_microvia_drill = 3.0
    assert m.save_to_project(pro, backup=False)
    m2 = PSM.ProjectSettingsManager(); m2.load_from_project(pro)
    assert m2.settings.min_hole_to_hole == 9.0 and m2.settings.min_microvia_drill == 3.0


# --- one-click dedup ------------------------------------------------------
def test_dedupe_symbol_library(tmp_path):
    sym = tmp_path / "s.kicad_sym"
    make_symlib(sym, ["A", "B", "A", "C", "B", "A"])
    log = DummyLog()
    removed = L.dedupe_symbol_library(sym, log)
    assert removed == 3
    assert names_in(sym) == ["A", "B", "C"]   # first occurrence order preserved


# --- overwrite protection -------------------------------------------------
def test_safe_install_new_identical_and_conflict(tmp_path):
    log = DummyLog()
    src = tmp_path / "a.kicad_mod"
    src.write_text("footprint v1", encoding="utf-8")
    dst = tmp_path / "dest" / "a.kicad_mod"
    dst.parent.mkdir()

    assert L.safe_install(src, dst, log, "footprint") == "copied"
    assert L.safe_install(src, dst, log, "footprint") == "identical"

    src.write_text("footprint v2 DIFFERENT", encoding="utf-8")
    assert L.safe_install(src, dst, log, "footprint") == "skipped"
    assert dst.read_text(encoding="utf-8") == "footprint v1"   # not overwritten


# --- path derivation ------------------------------------------------------
def test_derive_paths(tmp_path):
    p = L.derive_paths(tmp_path)
    assert p["RepoRoot"] == str(tmp_path)
    assert p["FootprintLib"].endswith("MyFootprints.pretty")
    assert p["SymbolLib"].endswith("MySymbols.kicad_sym")


def test_can_write_dir(tmp_path):
    assert L._can_write_dir(tmp_path / "newsub") is True


# --- scan + filter --------------------------------------------------------
def _make_cfg(tmp_path, sym_names):
    sym = tmp_path / "MySymbols.kicad_sym"
    make_symlib(sym, sym_names)
    fp = tmp_path / "fp"; fp.mkdir()
    (fp / "FP1.kicad_mod").write_text("x", encoding="utf-8")
    md = tmp_path / "md"; md.mkdir()
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(md)}


def test_scan_library_flags_duplicates(tmp_path):
    cfg = _make_cfg(tmp_path, ["A", "B", "A"])
    rows, summary = L.scan_library(cfg)
    syms = [r for r in rows if r["type"] == "Symbol"]
    assert [r["sym_index"] for r in syms] == [0, 1, 2]
    assert summary["duplicates"] == 2          # the two A rows
    assert summary["footprints"] == 1
    assert sum(1 for r in rows if r.get("dup")) == 2


def test_filter_rows_dup_only_and_type(tmp_path):
    cfg = _make_cfg(tmp_path, ["A", "A", "B"])
    rows, _ = L.scan_library(cfg)
    dup_only = L.filter_rows(rows, "", "All", dup_only=True)
    assert all(r["dup"] for r in dup_only)
    assert {r["name"] for r in dup_only} == {"A"}
    sym_only = L.filter_rows(rows, "", {"Symbol"}, dup_only=False)
    assert all(r["type"] == "Symbol" for r in sym_only)


# --- reference-based part grouping (names may differ) ---------------------
def test_symbol_footprint_ref():
    b = '(symbol "U1" (property "Footprint" "MyFootprints:QFN-16") (pin 1))'
    assert L.symbol_footprint_ref(b) == "QFN-16"
    assert L.symbol_footprint_ref('(symbol "U2" (pin 1))') == ""


def test_footprint_model_ref():
    assert L.footprint_model_ref('(footprint "X" (model "${MY3DMODELS}/Foo.step" (scale 1)))') == "Foo.step"
    assert L.footprint_model_ref('(footprint "X" (pad 1))') == ""


def test_associate_parts_groups_by_reference():
    # Symbol->footprint via the Footprint property; footprint->model via its
    # (model …) line — grouped even when footprint and model names are unrelated.
    sym = ('(kicad_symbol_lib (version 20211014) (generator "t")\n'
           '  (symbol "ADG714" (property "Footprint" "MyFootprints:IC51-1004-809") (pin 1))\n'
           '  (symbol "R33" (property "Footprint" "MyFootprints:R_0402") (pin 1))\n'
           '  (symbol "NoFP" (pin 1))\n)\n')
    footprints = {
        "IC51-1004-809": '(footprint "IC51-1004-809" (model "${MY3DMODELS}/Yamaichi_ZIF.step" (scale 1)))',
        "R_0402": '(footprint "R_0402" (pad 1))',   # no (model …) line
    }
    groups = L.associate_parts(sym, footprints, ["Yamaichi_ZIF.step", "R_0402.step"])
    by_fp = {g["footprint"]: g for g in groups}
    # unrelated footprint + model grouped via the explicit reference
    assert by_fp["IC51-1004-809"]["model"] == "Yamaichi_ZIF.step"
    assert by_fp["IC51-1004-809"]["model_source"] == "reference"
    assert by_fp["IC51-1004-809"]["symbols"] == ["ADG714"]
    # footprint without a model line falls back to a name-normalized guess
    assert by_fp["R_0402"]["model"] == "R_0402.step"
    assert by_fp["R_0402"]["model_source"] == "name-match"
    # a symbol with no Footprint property is reported ungrouped
    ung = [g for g in groups if g["footprint"] is None][0]
    assert ung["symbols"] == ["NoFP"]


def test_associate_parts_override_wins():
    sym = '(kicad_symbol_lib\n  (symbol "U1" (property "Footprint" "MyFootprints:FP1") (pin 1))\n)\n'
    footprints = {"FP1": '(footprint "FP1" (model "${V}/auto.step"))'}
    ov = {"model": {"FP1": "manual_pick.step"}}
    g = L.associate_parts(sym, footprints, ["auto.step", "manual_pick.step"], ov)[0]
    assert g["model"] == "manual_pick.step" and g["model_source"] == "override"


def test_group_overrides_roundtrip(tmp_path):
    cfg = {"Libs": str(tmp_path)}
    assert L.load_group_overrides(cfg) == {}
    L.save_group_overrides(cfg, {"model": {"FP1": "x.step"}})
    assert L.load_group_overrides(cfg) == {"model": {"FP1": "x.step"}}
