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
