"""SP1 path-resolution core: bundle_path, library_location, pointer, seed.

These exercise the frozen/dev split from the SP1 design spec §4-§6 under
monkeypatched sys.frozen / sys._MEIPASS and a redirected pointer file
(KICADMGR_POINTER), so nothing touches the real %APPDATA%.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


# --- bundle_path -----------------------------------------------------------
def test_bundle_path_dev_uses_repo_tree(monkeypatch):
    monkeypatch.setattr(L.sys, "frozen", False, raising=False)
    assert L.bundle_path("data/stm32.sqlite") == L.detect_repo_root() / "data/stm32.sqlite"


def test_bundle_path_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    monkeypatch.setattr(L.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert L.bundle_path("fonts") == tmp_path / "fonts"


# --- library_location ------------------------------------------------------
def test_library_location_dev_is_repo_root(monkeypatch):
    monkeypatch.setattr(L.sys, "frozen", False, raising=False)
    assert L.library_location() == L.detect_repo_root()


def test_library_location_frozen_uses_pointer(monkeypatch, tmp_path):
    loc = tmp_path / "mylib"
    loc.mkdir()
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    L.write_pointer(loc)
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    assert L.library_location() == loc


def test_library_location_frozen_no_pointer_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    assert L.library_location() is None


# --- pointer read/write ----------------------------------------------------
def test_pointer_roundtrip(monkeypatch, tmp_path):
    loc = tmp_path / "chosen"
    loc.mkdir()
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    L.write_pointer(loc)
    assert L.read_pointer() == loc


def test_read_pointer_missing_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "nope.json"))
    assert L.read_pointer() is None


def test_read_pointer_gone_location_is_none(monkeypatch, tmp_path):
    loc = tmp_path / "chosen"
    loc.mkdir()
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    L.write_pointer(loc)
    loc.rmdir()  # the previously-chosen location vanished
    assert L.read_pointer() is None


# --- seed ------------------------------------------------------------------
def _make_seed(root: Path) -> Path:
    seed = root / "seed"
    (seed / "libs").mkdir(parents=True)
    (seed / "libs" / "MySymbols.kicad_sym").write_text("(kicad_symbol_lib)\n")
    (seed / "catalog_assets").mkdir(parents=True)
    (seed / "catalog_assets" / "index.json").write_text("{}")
    return seed


def test_seed_copies_and_writes_config(tmp_path):
    seed = _make_seed(tmp_path)
    dest = tmp_path / "userlib"
    ran = L.seed_library(dest, seed_root=seed, seed_version="1")
    assert ran is True
    assert (dest / "libs" / "MySymbols.kicad_sym").exists()
    assert (dest / "catalog_assets" / "index.json").exists()
    cfg = json.loads((dest / "config.json").read_text())
    assert cfg["RepoRoot"] == str(dest)
    assert (dest / ".seed_version").read_text().strip() == "1"


def test_seed_is_idempotent(tmp_path):
    seed = _make_seed(tmp_path)
    dest = tmp_path / "userlib"
    assert L.seed_library(dest, seed_root=seed, seed_version="1") is True
    assert L.seed_library(dest, seed_root=seed, seed_version="1") is False


def test_seed_force_reseeds(tmp_path):
    seed = _make_seed(tmp_path)
    dest = tmp_path / "userlib"
    L.seed_library(dest, seed_root=seed, seed_version="1")
    assert L.seed_library(dest, seed_root=seed, seed_version="1", force=True) is True


# --- ensure_library_location (non-modal paths) -----------------------------
def test_ensure_library_location_dev_no_prompt(monkeypatch):
    monkeypatch.setattr(L.sys, "frozen", False, raising=False)
    assert L.ensure_library_location() == L.detect_repo_root()


def test_ensure_library_location_frozen_pointer_no_prompt(monkeypatch, tmp_path):
    loc = tmp_path / "lib"
    loc.mkdir()
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    L.write_pointer(loc)
    monkeypatch.setattr(L.sys, "frozen", True, raising=False)
    # A valid pointer must short-circuit before any modal is constructed.
    monkeypatch.setattr(L, "_prompt_choose_location",
                        lambda parent=None: (_ for _ in ()).throw(AssertionError("prompted")))
    assert L.ensure_library_location() == loc


# --- apply_library_location (rebind seam) ----------------------------------
def test_apply_library_location_rebinds_globals(tmp_path):
    orig_root, orig_cfg = L.REPO_ROOT, L.CONFIG_PATH
    try:
        loc = tmp_path / "userlib"
        loc.mkdir()
        L.apply_library_location(loc)
        assert L.REPO_ROOT == loc
        assert L.CONFIG_PATH == loc / "config.json"
        assert L.DEFAULTS["Libs"] == str(loc / "libs")
    finally:
        L.REPO_ROOT, L.CONFIG_PATH = orig_root, orig_cfg
        L.DEFAULTS = L.derive_paths(orig_root)
