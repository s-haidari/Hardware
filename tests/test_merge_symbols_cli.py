# -*- coding: utf-8 -*-
"""Tests for the tools/merge_symbols.py CLI wrapper.

Covers the real end-to-end merge path plus the platform-aware venv hint
printed on ImportError (the hint used to name only the Windows layout).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load_cli():
    """Import tools/merge_symbols.py as an isolated module object."""
    spec = importlib.util.spec_from_file_location(
        "_merge_symbols_cli", _TOOLS / "merge_symbols.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SYM = (
    '(kicad_symbol_lib (version 20211014) (generator test)\n'
    '  (symbol "Vendor:R_0402" (in_bom yes) (on_board yes)\n'
    '    (property "Reference" "R" (id 0) (at 0 0 0))\n'
    '  )\n'
    ')\n'
)


def test_usage_when_too_few_args(capsys):
    cli = _load_cli()
    rc = cli.main([])
    assert rc == 2
    assert "Usage" in capsys.readouterr().out


def test_missing_source_reports_and_exits(tmp_path, capsys):
    cli = _load_cli()
    target = tmp_path / "lib.kicad_sym"
    target.write_text("(kicad_symbol_lib)\n", encoding="utf-8")
    rc = cli.main([str(target), str(tmp_path / "nope.kicad_sym")])
    assert rc == 2
    assert "source not found" in capsys.readouterr().out


def test_end_to_end_merge_writes_symbol(tmp_path, capsys):
    cli = _load_cli()
    target = tmp_path / "lib.kicad_sym"
    target.write_text("(kicad_symbol_lib)\n", encoding="utf-8")
    src = tmp_path / "src.kicad_sym"
    src.write_text(_SYM, encoding="utf-8")

    rc = cli.main([str(target), str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Merged 1 symbol" in out
    merged = target.read_text(encoding="utf-8")
    assert "Vendor:R_0402" in merged


@pytest.mark.parametrize(
    "platform,expected,unexpected",
    [
        ("win32", ".venv/Scripts/python.exe", ".venv/bin/python"),
        ("linux", ".venv/bin/python", ".venv/Scripts/python.exe"),
        ("darwin", ".venv/bin/python", ".venv/Scripts/python.exe"),
    ],
)
def test_import_error_hint_is_platform_aware(
    monkeypatch, capsys, platform, expected, unexpected
):
    """When the merge core can't be imported, the hint names the venv path
    that matches the running platform — not always the Windows layout."""
    cli = _load_cli()
    monkeypatch.setattr(sys, "platform", platform)

    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "LibraryManager":
            raise ImportError("simulated missing core")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)

    rc = cli.main([str(_TOOLS / "a.kicad_sym"), str(_TOOLS / "b.kicad_sym")])
    assert rc == 2
    out = capsys.readouterr().out
    assert expected in out
    assert unexpected not in out
