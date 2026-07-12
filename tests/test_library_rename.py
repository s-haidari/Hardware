"""Backend test for rename_symbol_in_library — rename a symbol (and its nested unit
sub-symbols) in place in the shared library. Surfaces the rename capability the UI
was missing (rename_symbol_block existed as a pure helper but nothing wrote it back)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import LibraryManager as L  # noqa: E402

HDR = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _cfg(tmp_path, syms):
    libs = tmp_path / "libs"
    (libs / "MyFootprints.pretty").mkdir(parents=True)
    (libs / "My3DModels").mkdir(parents=True)
    (libs / "MySymbols.kicad_sym").write_text(HDR + syms + ")\n", encoding="utf-8")
    return {"Libs": str(libs), "SymbolLib": str(libs / "MySymbols.kicad_sym"),
            "FootprintLib": str(libs / "MyFootprints.pretty"), "ModelLib": str(libs / "My3DModels")}


def _sym(name):
    return (f'  (symbol "{name}" (in_bom yes)\n'
            f'    (property "Value" "{name}")\n'
            f'    (symbol "{name}_0_1" (rectangle (start -1 1) (end 1 -1)))\n  )\n')


def _names(cfg):
    return {L.extract_symbol_name(b)
            for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"])))}


def test_rename_symbol_in_library(tmp_path):
    cfg = _cfg(tmp_path, _sym("OLD"))
    assert L.rename_symbol_in_library(cfg, "OLD", "NEW", L._NullLog()) is True
    assert _names(cfg) == {"NEW"}
    text = L.read_text(Path(cfg["SymbolLib"]))
    assert '(symbol "NEW_0_1"' in text and "OLD_0_1" not in text     # units renamed too


def test_rename_missing_source_is_false(tmp_path):
    cfg = _cfg(tmp_path, _sym("OLD"))
    assert L.rename_symbol_in_library(cfg, "GHOST", "NEW", L._NullLog()) is False


def test_rename_into_existing_name_refused(tmp_path):
    cfg = _cfg(tmp_path, _sym("A") + _sym("B"))
    assert L.rename_symbol_in_library(cfg, "A", "B", L._NullLog()) is False   # would collide
    assert _names(cfg) == {"A", "B"}
