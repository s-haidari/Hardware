"""Audit-fix coverage for tools/LibraryManager.py (patch scope):

  - libs/.trash undo snapshots are pruned to a cap, and are restorable / emptyable
  - DigiKey OAuth2 token is fetched once per lookup-closure, not per part
  - register_libraries surfaces a structured, actionable result when KiCad is absent
  - cfg-path accessors are defensive (.get, no KeyError on a partial cfg)
"""
import os
import sys
import pathlib
import tempfile
import itertools

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


class _CapLog:
    """A UILog-shaped sink that records every message for assertions."""
    def __init__(self):
        self.lines = []

    def write(self, msg="", *a, **k):
        self.lines.append(str(msg))


def _write_lib(path: pathlib.Path, names):
    blocks = "".join(f'  (symbol "{n}" (property "Value" "{n}" (at 0 0 0)))\n'
                     for n in names)
    path.write_text(
        f'(kicad_symbol_lib (version 20211014) (generator "t")\n{blocks})\n',
        encoding="utf-8")


class _StampedDT:
    """A datetime stand-in whose .now().strftime() yields a fresh unique stamp each call,
    so back-to-back snapshots land in distinct libs/.trash/<stamp>/ dirs under test."""
    _counter = itertools.count()

    @classmethod
    def now(cls):
        v = "20260101_0000%02d" % next(cls._counter)

        class _T:
            def strftime(self, _fmt):
                return v
        return _T()


# ── libs/.trash: pruning, restore, empty ─────────────────────────────────────
def test_snapshot_prunes_trash_to_cap(monkeypatch):
    """Every destructive write drops a snapshot; the folder never exceeds _TRASH_KEEP."""
    monkeypatch.setattr(LM, "_TRASH_KEEP", 3)
    import datetime as _real_dt
    monkeypatch.setattr(_real_dt, "datetime", _StampedDT, raising=False)
    with tempfile.TemporaryDirectory() as td:
        sym = pathlib.Path(td) / "Sym.kicad_sym"
        _write_lib(sym, ["A"])
        for i in range(6):
            LM._snapshot_then_write(sym, f'(kicad_symbol_lib (v {i}))\n', _CapLog())
        snaps = LM.list_trash_snapshots(sym)
        assert len(snaps) == 3                       # pruned to the cap
        assert [s.name for s in snaps] == sorted((s.name for s in snaps), reverse=True)


def test_restore_last_trash_copies_back():
    with tempfile.TemporaryDirectory() as td:
        sym = pathlib.Path(td) / "Sym.kicad_sym"
        _write_lib(sym, ["ORIGINAL"])
        log = _CapLog()
        LM._snapshot_then_write(sym, '(kicad_symbol_lib (destroyed))\n', log)
        assert "ORIGINAL" not in sym.read_text(encoding="utf-8")   # write happened
        assert LM.restore_last_trash(sym, log) is True
        assert "ORIGINAL" in sym.read_text(encoding="utf-8")       # copy-back worked
        assert any("Restored" in ln for ln in log.lines)


def test_restore_last_trash_none_available():
    with tempfile.TemporaryDirectory() as td:
        sym = pathlib.Path(td) / "Sym.kicad_sym"
        _write_lib(sym, ["A"])
        log = _CapLog()
        assert LM.restore_last_trash(sym, log) is False
        assert any("no undo snapshot" in ln.lower() for ln in log.lines)


def test_empty_trash_removes_all():
    with tempfile.TemporaryDirectory() as td:
        sym = pathlib.Path(td) / "Sym.kicad_sym"
        _write_lib(sym, ["A"])
        LM._snapshot_then_write(sym, '(kicad_symbol_lib (x))\n', _CapLog())
        assert LM.list_trash_snapshots(sym)                        # at least one
        removed = LM.empty_trash(sym, _CapLog())
        assert removed >= 1
        assert LM.list_trash_snapshots(sym) == []                  # nothing left


def test_list_trash_ignores_non_timestamp_junk():
    with tempfile.TemporaryDirectory() as td:
        sym = pathlib.Path(td) / "Sym.kicad_sym"
        _write_lib(sym, ["A"])
        LM._snapshot_then_write(sym, '(kicad_symbol_lib (x))\n', _CapLog())
        (LM._trash_dir(sym) / "not-a-timestamp").mkdir()
        names = [s.name for s in LM.list_trash_snapshots(sym)]
        assert "not-a-timestamp" not in names
        assert all(len(n) == 15 and n[8] == "_" for n in names)    # %Y%m%d_%H%M%S only


# ── DigiKey token cached once per closure, not per lookup ─────────────────────
def test_digikey_token_fetched_once_per_closure(monkeypatch):
    calls = {"token": 0}

    def _counting_token(cid, sec, timeout=8):
        calls["token"] += 1
        return "TOKEN"

    monkeypatch.setattr(LM, "_digikey_token", _counting_token)
    monkeypatch.setattr(
        LM, "_digikey_request",
        lambda endpoint, token, client_id, payload, timeout=8: {
            "data": {"Products": [{"ManufacturerProductNumber": "PN1"}]},
            "status": 200, "error": ""})

    lookup = LM.make_digikey_lookup("id", "secret")
    for _ in range(5):
        assert lookup("PN1") is not None
    assert calls["token"] == 1        # one token reused across all five part lookups


def test_digikey_token_retries_after_failure(monkeypatch):
    """A None token is not memoized: the next lookup retries the token endpoint."""
    seq = iter([None, "TOKEN"])
    calls = {"token": 0}

    def _token(cid, sec, timeout=8):
        calls["token"] += 1
        return next(seq)

    monkeypatch.setattr(LM, "_digikey_token", _token)
    monkeypatch.setattr(
        LM, "_digikey_request",
        lambda endpoint, token, client_id, payload, timeout=8: {
            "data": {"Products": [{"ManufacturerProductNumber": "PN1"}]},
            "status": 200, "error": ""})

    lookup = LM.make_digikey_lookup("id", "secret")
    assert lookup("PN1") is None      # first token refused
    assert lookup("PN1") is not None  # retried, succeeded
    assert calls["token"] == 2


# ── register_libraries structured result ─────────────────────────────────────
def test_register_libraries_no_config_is_actionable(monkeypatch):
    monkeypatch.setattr(LM, "find_kicad_config_dir", lambda: None)
    log = _CapLog()
    res = LM.register_libraries({"SymbolLib": "s", "FootprintLib": "f", "ModelLib": "m"}, log)
    assert res["ok"] is False
    assert res["reason"] == "no_config"
    assert res["changed"] is False
    assert "KICAD_CONFIG_HOME" in res["message"]                # names the remediation
    assert "register" in res["message"].lower()
    assert any("KICAD_CONFIG_HOME" in ln for ln in log.lines)   # surfaced to the log too


def test_register_libraries_ok_result(monkeypatch, tmp_path):
    monkeypatch.setattr(LM, "find_kicad_config_dir", lambda: tmp_path)
    log = _CapLog()
    res = LM.register_libraries({"SymbolLib": "s", "FootprintLib": "f", "ModelLib": "m"}, log)
    assert res["ok"] is True
    assert res["reason"] == ""
    assert res["changed"] is True                               # tables/env written fresh
    assert (tmp_path / "sym-lib-table").exists()


# ── defensive cfg access (no KeyError on a partial cfg) ──────────────────────
def test_associate_parts_from_cfg_tolerates_missing_keys():
    assert LM.associate_parts_from_cfg({}) == []                # empty cfg, no KeyError


def test_scan_library_grouped_tolerates_missing_keys():
    assert LM.scan_library_grouped({}) == []
