"""Backend regression tests for the NEW LibraryManager capability:

1. scan_library_grouped(cfg) — one-row-per-part grouping for the future grouped
   library view, with has_symbol / has_footprint / has_model presence flags and
   a `dangling` flag (a symbol references a footprint with no .kicad_mod file,
   OR a footprint references a 3D model file that is missing on disk).

2. save_repo_root(cfg, new_root) + load_config() honoring a persisted RepoRoot
   (audit medium: change_path persisted the whole cfg but load_config always
   re-derived RepoRoot from the exe/script location, silently reverting a user's
   root change on the next launch).

These are pure/logic tests: no GUI is constructed, no git is shelled out.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


# --------------------------------------------------------------------------
# Helpers: build a self-contained shared-library tree under tmp_path.
# --------------------------------------------------------------------------
SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _symbol(name: str, footprint: str = None) -> str:
    """A minimal, paren-balanced (symbol …) block. If footprint is given, add a
    Footprint property pointing at MyFootprints:<footprint> (as the real
    importer writes it); otherwise emit no Footprint property at all."""
    lines = [f'  (symbol "{name}"']
    if footprint is not None:
        lines.append(f'    (property "Footprint" "MyFootprints:{footprint}")')
    lines.append("    (pin 1)")
    lines.append("  )")
    return "\n".join(lines) + "\n"


def _footprint(name: str, model_basename: str = None) -> str:
    """A minimal (footprint …) block. If model_basename is given, add a
    (model "${MY3DMODELS}/<file>") line the way ensure_footprint_model does."""
    inner = ""
    if model_basename is not None:
        inner = (
            f'  (model "${{MY3DMODELS}}/{model_basename}"\n'
            "    (offset (xyz 0 0 0))\n"
            "  )\n"
        )
    return f'(footprint "{name}" (layer "F.Cu")\n{inner})\n'


def _make_cfg(tmp_path, symbols_text, footprints, model_files):
    """Write a shared-library tree and return a cfg dict pointing at it.

    footprints: {stem: model_basename-or-None}
    model_files: iterable of model filenames to actually create on disk.
    """
    libs = tmp_path / "libs"
    fp_dir = libs / "MyFootprints.pretty"
    mdl_dir = libs / "My3DModels"
    fp_dir.mkdir(parents=True)
    mdl_dir.mkdir(parents=True)

    sym_path = libs / "MySymbols.kicad_sym"
    sym_path.write_text(SYM_HEADER + symbols_text + ")\n", encoding="utf-8")

    for stem, model_basename in footprints.items():
        (fp_dir / f"{stem}.kicad_mod").write_text(
            _footprint(stem, model_basename), encoding="utf-8"
        )

    for m in model_files:
        (mdl_dir / m).write_text("solid\n", encoding="utf-8")

    return {
        "Libs": str(libs),
        "SymbolLib": str(sym_path),
        "FootprintLib": str(fp_dir),
        "ModelLib": str(mdl_dir),
    }


# --------------------------------------------------------------------------
# scan_library_grouped
# --------------------------------------------------------------------------
def test_scan_library_grouped_flags_and_grouping(tmp_path):
    """One complete part, one symbol->missing-footprint, one footprint->missing
    model, and one footprint-less symbol; verify grouping + every flag."""
    symbols = (
        _symbol("U1", footprint="FP_A")        # complete part
        + _symbol("U2", footprint="FP_MISSING")  # references a footprint w/ no file
        + _symbol("U3")                          # no footprint reference at all
    )
    footprints = {
        "FP_A": "modelA.step",        # model exists -> healthy
        "FP_B": "missing_model.step",  # references a model file that is absent
    }
    model_files = ["modelA.step"]      # note: missing_model.step is NOT created

    cfg = _make_cfg(tmp_path, symbols, footprints, model_files)
    rows = L.scan_library_grouped(cfg)
    by_name = {r["name"]: r for r in rows}

    # -- FP_A: symbol U1 + real footprint + real model = fully healthy --------
    a = by_name["U1"]                  # label is the first symbol name
    assert a["footprint"] == "FP_A"
    assert a["symbols"] == ["U1"]
    assert a["model"] == "modelA.step"
    assert a["model_source"] == "reference"
    assert (a["has_symbol"], a["has_footprint"], a["has_model"]) == (True, True, True)
    assert a["dangling"] is False

    # -- U2 references FP_MISSING which has no .kicad_mod file -> dangling -----
    b = by_name["U2"]
    assert b["footprint"] == "FP_MISSING"
    assert b["has_symbol"] is True
    assert b["has_footprint"] is False   # no footprint file on disk
    assert b["has_model"] is False
    assert b["dangling"] is True

    # -- FP_B has a real file but its (model …) line points at a missing file --
    c = by_name["FP_B"]                 # no symbols -> label falls back to stem
    assert c["symbols"] == []
    assert c["has_symbol"] is False
    assert c["has_footprint"] is True
    assert c["model"] == "missing_model.step"
    assert c["has_model"] is False
    assert c["dangling"] is True

    # -- U3 has no Footprint property: missing, but NOT dangling ---------------
    d = by_name["U3"]
    assert d["footprint"] is None
    assert d["symbols"] == ["U3"]
    assert d["has_symbol"] is True
    assert d["has_footprint"] is False
    assert d["has_model"] is False
    assert d["dangling"] is False


def test_scan_library_grouped_empty_when_no_libs(tmp_path):
    """Nonexistent lib paths must not raise; they yield no rows."""
    cfg = {
        "Libs": str(tmp_path / "libs"),
        "SymbolLib": str(tmp_path / "libs" / "MySymbols.kicad_sym"),
        "FootprintLib": str(tmp_path / "libs" / "MyFootprints.pretty"),
        "ModelLib": str(tmp_path / "libs" / "My3DModels"),
    }
    assert L.scan_library_grouped(cfg) == []


def test_scan_library_grouped_name_match_model_is_not_dangling(tmp_path):
    """A footprint with no (model …) line but a name-matching model file on disk
    gets a 'name-match' model and is healthy (not dangling)."""
    symbols = _symbol("TPS2121", footprint="IC_TPS2121RUXR")
    footprints = {"IC_TPS2121RUXR": None}     # no model line in the footprint
    model_files = ["TPS2121RUXR.step"]        # matches by normalized name

    cfg = _make_cfg(tmp_path, symbols, footprints, model_files)
    rows = L.scan_library_grouped(cfg)
    by_fp = {r["footprint"]: r for r in rows}
    part = by_fp["IC_TPS2121RUXR"]
    assert part["model"] == "TPS2121RUXR.step"
    assert part["model_source"] == "name-match"
    assert part["has_model"] is True
    assert part["dangling"] is False


# --------------------------------------------------------------------------
# save_repo_root + load_config RepoRoot persistence
# --------------------------------------------------------------------------
def test_save_repo_root_roundtrip(tmp_path):
    """save_repo_root writes config.json; load_config then honors it and derives
    every path from the persisted root."""
    cfg_path = tmp_path / "config.json"
    new_root = tmp_path / "myrepo"
    new_root.mkdir()

    cfg = {}
    assert L.save_repo_root(cfg, new_root, config_path=cfg_path) is True
    # in-memory cfg updated immediately
    assert Path(cfg["RepoRoot"]).resolve() == new_root.resolve()

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert Path(data["RepoRoot"]).resolve() == new_root.resolve()

    loaded = L.load_config(config_path=cfg_path)
    assert Path(loaded["RepoRoot"]).resolve() == new_root.resolve()
    # Derived paths hang off the persisted root, not the module REPO_ROOT.
    assert Path(loaded["Libs"]).resolve() == (new_root / "libs").resolve()
    assert Path(loaded["SymbolLib"]).resolve() == (new_root / "libs" / "MySymbols.kicad_sym").resolve()


def test_save_repo_root_preserves_other_keys(tmp_path):
    """Updating RepoRoot in an existing config.json must not drop other keys."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"PythonExe": "C:/custom/py.exe", "Downloads": "D:/dl"}),
        encoding="utf-8",
    )
    new_root = tmp_path / "repo2"
    new_root.mkdir()

    assert L.save_repo_root({}, new_root, config_path=cfg_path) is True
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert Path(data["RepoRoot"]).resolve() == new_root.resolve()
    assert data["PythonExe"] == "C:/custom/py.exe"   # preserved
    assert data["Downloads"] == "D:/dl"               # preserved


def test_save_repo_root_rejects_missing_dir(tmp_path):
    """A nonexistent root is rejected and nothing is written."""
    cfg_path = tmp_path / "config.json"
    missing = tmp_path / "does_not_exist"
    assert L.save_repo_root({}, missing, config_path=cfg_path) is False
    assert not cfg_path.exists()


def test_save_repo_root_rejects_file_target(tmp_path):
    """A path that exists but is a FILE (not a dir) is rejected."""
    cfg_path = tmp_path / "config.json"
    a_file = tmp_path / "not_a_dir.txt"
    a_file.write_text("x", encoding="utf-8")
    assert L.save_repo_root({}, a_file, config_path=cfg_path) is False
    assert not cfg_path.exists()


def test_load_config_falls_back_when_reporoot_absent(tmp_path, monkeypatch):
    """With no RepoRoot in config.json, load_config derives from REPO_ROOT and
    still applies the PythonExe override (backward-compatible behavior)."""
    fake_root = tmp_path / "fakeroot"
    fake_root.mkdir()
    monkeypatch.setattr(L, "REPO_ROOT", fake_root)

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"PythonExe": "C:/custom/py.exe"}), encoding="utf-8")

    loaded = L.load_config(config_path=cfg_path)
    assert Path(loaded["RepoRoot"]).resolve() == fake_root.resolve()
    assert loaded["PythonExe"] == "C:/custom/py.exe"


def test_load_config_ignores_stale_reporoot(tmp_path, monkeypatch):
    """A persisted RepoRoot pointing at a nonexistent folder is ignored, falling
    back to the portable exe/script derivation rather than a dead path."""
    fake_root = tmp_path / "fakeroot"
    fake_root.mkdir()
    monkeypatch.setattr(L, "REPO_ROOT", fake_root)

    cfg_path = tmp_path / "config.json"
    stale = tmp_path / "gone" / "elsewhere"        # never created
    cfg_path.write_text(json.dumps({"RepoRoot": str(stale)}), encoding="utf-8")

    loaded = L.load_config(config_path=cfg_path)
    assert Path(loaded["RepoRoot"]).resolve() == fake_root.resolve()
