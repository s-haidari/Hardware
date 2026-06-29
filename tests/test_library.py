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
