"""SP1: the three __file__ write/read sites become frozen-aware.

Under --onefile, __file__ resolves into the throwaway _MEIPASS extraction dir.
Fonts (read-only bundle) must resolve to the bundle; wizard logs and the
netclass vault_standard.json (writes) must land in the user's library location.
In dev (not frozen) every path is unchanged.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ui"))

import LibraryManager as L  # noqa: E402
import nd_wizard  # noqa: E402
import nd_netclass_manager as NC  # noqa: E402
from ui import theme  # noqa: E402


def _point_at(monkeypatch, tmp_path):
    """Frozen mode with a valid library-location pointer -> tmp_path/lib."""
    loc = tmp_path / "lib"
    loc.mkdir()
    monkeypatch.setenv("KICADMGR_POINTER", str(tmp_path / "workspace.json"))
    L.write_pointer(loc)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    return loc


# --- fonts: read-only bundle -----------------------------------------------
def test_fonts_dir_dev(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert theme._fonts_dir() == Path(theme.__file__).resolve().parent.parent / "fonts"


def test_fonts_dir_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert theme._fonts_dir() == tmp_path / "fonts"


# --- wizard logs: writable location ----------------------------------------
def test_log_dir_dev_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert nd_wizard._log_dir() == Path(nd_wizard.__file__).parent / "logs"


def test_log_dir_frozen_uses_library_location(monkeypatch, tmp_path):
    loc = _point_at(monkeypatch, tmp_path)
    assert nd_wizard._log_dir() == loc / "logs"


# --- netclass vault standard: writable location ----------------------------
def test_vault_standard_dev_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert NC._vault_standard_path() == Path(NC.__file__).resolve().parent / "vault_standard.json"


def test_vault_standard_frozen_uses_library_location(monkeypatch, tmp_path):
    loc = _point_at(monkeypatch, tmp_path)
    assert NC._vault_standard_path() == loc / "vault_standard.json"
