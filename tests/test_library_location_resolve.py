"""P0 fix: a chosen library folder must load its parts even when its layout differs from
the derived `<root>/libs/MySymbols.kicad_sym`, and a genuinely empty/missing library must
report a clear status instead of silently loading zero parts (the v2.11 real-Windows bug:
"set to the correct folder but loaded no parts" — the derived symbol-lib path was absent,
so an empty stub was auto-created and read as zero).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


def _lib_text(*names):
    body = "".join(f'  (symbol "{n}" (property "Value" "{n}" (id 1)) (pin 1))\n' for n in names)
    return "(kicad_symbol_lib\n" + body + ")\n"


# ── the resolver (pure) ───────────────────────────────────────────────────────
def test_resolver_finds_symbol_lib_directly_in_root(tmp_path):
    """The chosen folder IS the library (MySymbols.kicad_sym directly inside)."""
    (tmp_path / "MySymbols.kicad_sym").write_text(_lib_text("P1"), encoding="utf-8")
    cfg = LM.derive_paths(tmp_path)                 # SymbolLib -> <root>/libs/... (absent)
    assert not Path(cfg["SymbolLib"]).is_file()
    LM._resolve_existing_library(cfg, tmp_path, {})
    assert Path(cfg["SymbolLib"]) == tmp_path / "MySymbols.kicad_sym"


def test_resolver_finds_symbol_lib_one_level_down(tmp_path):
    d = tmp_path / "mylib"; d.mkdir()
    (d / "MySymbols.kicad_sym").write_text(_lib_text("P1"), encoding="utf-8")
    cfg = LM.derive_paths(tmp_path)
    LM._resolve_existing_library(cfg, tmp_path, {})
    assert Path(cfg["SymbolLib"]) == d / "MySymbols.kicad_sym"


def test_resolver_leaves_a_present_standard_library_untouched(tmp_path):
    """A present derived library is never overridden — a stray lib elsewhere under root
    must not hijack the standard `<root>/libs/MySymbols.kicad_sym`."""
    libs = tmp_path / "libs"; libs.mkdir()
    std = libs / "MySymbols.kicad_sym"; std.write_text(_lib_text("REAL"), encoding="utf-8")
    (tmp_path / "MySymbols.kicad_sym").write_text(_lib_text("STRAY"), encoding="utf-8")
    cfg = LM.derive_paths(tmp_path)
    LM._resolve_existing_library(cfg, tmp_path, {})
    assert Path(cfg["SymbolLib"]) == std


def test_explicit_symbol_lib_override_is_honored_when_derived_absent(tmp_path):
    custom = tmp_path / "custom" / "sym.kicad_sym"; custom.parent.mkdir()
    custom.write_text(_lib_text("P1"), encoding="utf-8")
    cfg = LM.derive_paths(tmp_path)
    LM._resolve_existing_library(cfg, tmp_path, {"SymbolLib": str(custom)})
    assert Path(cfg["SymbolLib"]) == custom


# ── end-to-end through load_config → scan (the actual bug repro) ───────────────
def test_load_config_resolves_a_nonstandard_layout_so_scan_finds_parts(tmp_path):
    (tmp_path / "MySymbols.kicad_sym").write_text(_lib_text("P1", "P2"), encoding="utf-8")
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"RepoRoot": str(tmp_path)}), encoding="utf-8")
    cfg = LM.load_config(config_path=cfgp)
    assert Path(cfg["SymbolLib"]) == tmp_path / "MySymbols.kicad_sym"
    names = {r["name"] for r in LM.scan_library_grouped(cfg)}
    assert names and "P1" in names                  # loads its parts, NOT zero
    assert LM.library_status(cfg)["reason"] == "ok"


# ── library_status diagnostic ─────────────────────────────────────────────────
def test_library_status_not_found(tmp_path):
    st = LM.library_status(LM.derive_paths(tmp_path))    # <root>/libs/... absent
    assert st["found"] is False
    assert st["reason"] == "not_found"


def test_library_status_empty_stub(tmp_path):
    sym = tmp_path / "s.kicad_sym"; sym.write_text("(kicad_symbol_lib\n)\n", encoding="utf-8")
    st = LM.library_status({"SymbolLib": str(sym), "RepoRoot": str(tmp_path)})
    assert st["found"] is True
    assert st["symbol_count"] == 0
    assert st["reason"] == "empty"


def test_library_status_ok_names_the_path(tmp_path):
    sym = tmp_path / "s.kicad_sym"; sym.write_text(_lib_text("P1"), encoding="utf-8")
    st = LM.library_status({"SymbolLib": str(sym)})
    assert st["reason"] == "ok"
    assert st["symbol_count"] == 1
    assert st["symbol_path"].endswith("s.kicad_sym")
