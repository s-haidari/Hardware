"""Backend tests for reusing an EXISTING library symbol to make a footprint-only
orphan placeable (owner #7: "some can have the same symbol as something else"),
instead of only creating a bare stub.

* rename_symbol_block(block, new_name) — rename a symbol's id AND its nested unit
  sub-symbols (<name>_0_1, <name>_1_1 …) consistently, so KiCad still resolves units.
* duplicate_symbol_for_footprint(cfg, src_name, footprint_stem, log, name) —
  duplicate the chosen symbol, rename it, and repoint its Footprint at the orphan.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _multi_unit_symbol(name: str, footprint: str = "OLD_FP") -> str:
    """A symbol with two nested unit sub-symbols + a Footprint property, the way a
    real KiCad library symbol is shaped."""
    return (
        f'  (symbol "{name}" (in_bom yes) (on_board yes)\n'
        f'    (property "Reference" "U" (at 0 2.54 0))\n'
        f'    (property "Value" "{name}" (at 0 -2.54 0))\n'
        f'    (property "Footprint" "MyFootprints:{footprint}" (at 0 0 0))\n'
        f'    (symbol "{name}_0_1"\n'
        f'      (rectangle (start -5 5) (end 5 -5))\n'
        f'    )\n'
        f'    (symbol "{name}_1_1"\n'
        f'      (pin passive line (at -7.62 0 0) (length 2.54) (name "A") (number "1"))\n'
        f'    )\n'
        f'  )\n'
    )


def _make_cfg(tmp_path, symbols_text):
    libs = tmp_path / "libs"
    (libs / "MyFootprints.pretty").mkdir(parents=True)
    (libs / "My3DModels").mkdir(parents=True)
    sym_path = libs / "MySymbols.kicad_sym"
    sym_path.write_text(SYM_HEADER + symbols_text + ")\n", encoding="utf-8")
    return {"Libs": str(libs), "SymbolLib": str(sym_path),
            "FootprintLib": str(libs / "MyFootprints.pretty"),
            "ModelLib": str(libs / "My3DModels")}


# ---------------------------------------------------------------------------
# rename_symbol_block
# ---------------------------------------------------------------------------
def test_rename_symbol_block_renames_parent_and_units():
    block = _multi_unit_symbol("SRC").strip()
    out = L.rename_symbol_block(block, "NEW")
    assert L.extract_symbol_name(out) == "NEW"
    assert '(symbol "NEW_0_1"' in out
    assert '(symbol "NEW_1_1"' in out
    assert "SRC_0_1" not in out and "SRC_1_1" not in out
    # paren-balanced + still a single extractable block
    assert len(L.extract_symbol_blocks(out)) == 1


def test_rename_symbol_block_new_name_extends_old_prefix():
    # Regression: renaming "U1" -> "U1_RENAMED" (new starts with "<old>_") must NOT
    # double-apply the unit rewrite to the freshly-renamed parent (which produced
    # "U1_RENAMED_RENAMED"). The parent and its units each get exactly one rewrite.
    block = _multi_unit_symbol("U1").strip()
    out = L.rename_symbol_block(block, "U1_RENAMED")
    assert L.extract_symbol_name(out) == "U1_RENAMED"
    assert "U1_RENAMED_RENAMED" not in out
    assert '(symbol "U1_RENAMED_0_1"' in out and '(symbol "U1_RENAMED_1_1"' in out
    assert '(symbol "U1_0_1"' not in out
    assert len(L.extract_symbol_blocks(out)) == 1
    # a name with no units still round-trips cleanly through the prefix-extension case
    single = '  (symbol "U1" (property "Value" "U1" (at 0 0 0)) (pin passive line))'
    out2 = L.rename_symbol_block(single, "U1_RENAMED")
    assert L.extract_symbol_name(out2) == "U1_RENAMED"


def test_rename_symbol_block_handles_spaced_name():
    # A quoted name with a space is valid in .kicad_sym and must round-trip without
    # breaking paren-balance or leaving the old unit prefix behind.
    block = _multi_unit_symbol("SRC").strip()
    out = L.rename_symbol_block(block, "Cap 0402")
    assert L.extract_symbol_name(out) == "Cap 0402"
    assert '(symbol "Cap 0402_0_1"' in out and "SRC_0_1" not in out
    assert len(L.extract_symbol_blocks(out)) == 1


# ---------------------------------------------------------------------------
# duplicate_symbol_for_footprint
# ---------------------------------------------------------------------------
def test_duplicate_symbol_for_footprint_reuses_geometry(tmp_path):
    cfg = _make_cfg(tmp_path, _multi_unit_symbol("CAP_GENERIC", footprint="OLD_FP"))
    new = L.duplicate_symbol_for_footprint(cfg, "CAP_GENERIC", "CC0402", L._NullLog())
    assert new == "CC0402"
    blocks = {L.extract_symbol_name(b): b
              for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"])))}
    # source is untouched, the duplicate exists and carries the source's units
    assert "CAP_GENERIC" in blocks and "CC0402" in blocks
    dup = blocks["CC0402"]
    assert '(symbol "CC0402_0_1"' in dup and '(symbol "CC0402_1_1"' in dup
    # Footprint repointed at the orphan
    assert L.symbol_footprint_ref(dup) == "CC0402"
    # source still points at its own footprint
    assert L.symbol_footprint_ref(blocks["CAP_GENERIC"]) == "OLD_FP"


def test_duplicate_symbol_for_footprint_dedupes_name(tmp_path):
    # A symbol already named after the footprint stem -> new one gets a numeric suffix.
    cfg = _make_cfg(tmp_path,
                    _multi_unit_symbol("SRC") + _multi_unit_symbol("CC0402"))
    new = L.duplicate_symbol_for_footprint(cfg, "SRC", "CC0402", L._NullLog())
    assert new == "CC0402_2"
    names = {L.extract_symbol_name(b)
             for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"])))}
    assert {"SRC", "CC0402", "CC0402_2"} <= names


def test_duplicate_symbol_for_footprint_custom_name(tmp_path):
    cfg = _make_cfg(tmp_path, _multi_unit_symbol("SRC"))
    new = L.duplicate_symbol_for_footprint(cfg, "SRC", "CC0402", L._NullLog(),
                                           name="MyCap")
    assert new == "MyCap"
    blocks = {L.extract_symbol_name(b): b
              for b in L.extract_symbol_blocks(L.read_text(Path(cfg["SymbolLib"])))}
    assert "MyCap" in blocks
    assert L.symbol_footprint_ref(blocks["MyCap"]) == "CC0402"


def test_duplicate_symbol_for_footprint_missing_source(tmp_path):
    cfg = _make_cfg(tmp_path, _multi_unit_symbol("SRC"))
    assert L.duplicate_symbol_for_footprint(cfg, "NOPE", "CC0402", L._NullLog()) is None
    assert L.duplicate_symbol_for_footprint(cfg, "SRC", "", L._NullLog()) is None

# ---------------------------------------------------------------------------
# duplicate_part — copy a part's symbol under a new name to make a variant
# ---------------------------------------------------------------------------
def test_duplicate_part_copies_block_resets_mpn_dedups_name():
    cfg = _make_cfg(tmp := __import__("pathlib").Path(__import__("tempfile").mkdtemp()),
                    _multi_unit_symbol("R_0402", footprint="R_0402"))

    class _Log:
        def write(self, _m): pass

    row = {"name": "R_0402", "symbols": ["R_0402"], "footprint": "R_0402"}
    final = L.duplicate_part(cfg, row, "R_0402_VARIANT", _Log())
    assert final == "R_0402_VARIANT"
    blocks = {L.extract_symbol_name(b): b
              for b in L.extract_symbol_blocks(open(cfg["SymbolLib"]).read())}
    # the source is untouched and the duplicate exists with the copied graphics/link
    assert "R_0402" in blocks and "R_0402_VARIANT" in blocks
    dup = blocks["R_0402_VARIANT"]
    props = L.extract_symbol_properties(dup)
    # Value (this app's MPN field) is reset to the new name so the variant does not
    # inherit the source's part number; the footprint link is preserved verbatim.
    assert props.get("Value") == "R_0402_VARIANT"
    assert props.get("Footprint") == "MyFootprints:R_0402"
    # the nested unit sub-symbols were renamed too (KiCad still resolves units)
    assert '(symbol "R_0402_VARIANT_0_1"' in dup and '(symbol "R_0402_VARIANT_1_1"' in dup
    # a name collision de-duplicates instead of overwriting
    again = L.duplicate_part(cfg, row, "R_0402_VARIANT", _Log())
    assert again == "R_0402_VARIANT_2"


def test_duplicate_part_refuses_without_symbol_or_name():
    cfg = _make_cfg(__import__("pathlib").Path(__import__("tempfile").mkdtemp()),
                    _multi_unit_symbol("U1"))
    assert L.duplicate_part(cfg, {"name": "FP", "symbols": [], "footprint": "FP"}, "X") is None
    assert L.duplicate_part(cfg, {"name": "U1", "symbols": ["U1"]}, "  ") is None
