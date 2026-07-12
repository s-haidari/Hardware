"""SP1: STM32 DB is prebuilt, bundled, and opened read-only when frozen.

default_db_path() reads from the bundle (_MEIPASS) when frozen instead of
writing next to the exe; connect() opens read-only when frozen; a --build CLI
entry lets CI build the DB before packaging.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import stm32_db as DB  # noqa: E402


# --- default_db_path -------------------------------------------------------
def test_default_db_path_dev_unchanged(monkeypatch):
    monkeypatch.delenv("STM32_DB", raising=False)
    monkeypatch.setattr(DB.sys, "frozen", False, raising=False)
    assert DB.default_db_path() == DB._TOOLS / "data" / "stm32.sqlite"


def test_default_db_path_frozen_uses_bundle(monkeypatch, tmp_path):
    monkeypatch.delenv("STM32_DB", raising=False)
    monkeypatch.setattr(DB.sys, "frozen", True, raising=False)
    monkeypatch.setattr(DB.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert DB.default_db_path() == tmp_path / "data" / "stm32.sqlite"


def test_default_db_path_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("STM32_DB", str(tmp_path / "x.sqlite"))
    assert DB.default_db_path() == tmp_path / "x.sqlite"


# --- connect read-only when frozen -----------------------------------------
def _make_db(p: Path):
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE t(x);")
    c.close()


def test_connect_writable_in_dev(monkeypatch, tmp_path):
    monkeypatch.setattr(DB.sys, "frozen", False, raising=False)
    p = tmp_path / "d.sqlite"
    _make_db(p)
    conn = DB.connect(p)
    conn.execute("INSERT INTO t VALUES (1)")  # must not raise in dev
    conn.close()


def test_connect_readonly_when_frozen(monkeypatch, tmp_path):
    p = tmp_path / "d.sqlite"
    _make_db(p)
    monkeypatch.setattr(DB.sys, "frozen", True, raising=False)
    conn = DB.connect(p)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO t VALUES (1)")
    conn.close()


# --- --build CLI -----------------------------------------------------------
def test_build_cli_invokes_build(monkeypatch, tmp_path):
    called = {}

    def fake_build(source_dir, db_path, **kw):
        called["src"] = Path(source_dir)
        called["out"] = Path(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(db_path).write_text("db")
        return DB.BuildResult(1, 1, 1, {"LQFP48": 1})

    monkeypatch.setattr(DB, "build_database", fake_build)
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out" / "stm32.sqlite"
    rc = DB._cli(["--build", "--source", str(src), "--out", str(out)])
    assert rc == 0
    assert called["src"] == src and called["out"] == out
    assert out.exists()
