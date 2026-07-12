"""Windows-correctness guardrails — enforced on EVERY platform so a Windows-hostile
pattern can never reach the windows-latest release gate (CLAUDE.md: "Windows CI is
the release gate").

The failure modes these lock out:
- A bare ``Path.read_text()`` / ``write_text()`` / text-mode ``open()`` defaults to the
  locale encoding, which is **cp1252 on Windows** — it corrupts or raises
  ``UnicodeDecodeError`` / ``UnicodeEncodeError`` on any byte outside cp1252 (silkscreen
  arrows ``→``, greek ``Ω``/``µ``, ``°``, em dashes …). All file I/O must pass
  ``encoding="utf-8"``.
- ``subprocess`` with ``text=True`` / ``universal_newlines=True`` but no ``encoding=``
  decodes child output with the locale encoding (cp1252 on Windows), mangling non-ASCII
  git / tool output.

These are AST checks (multi-line-aware, unlike a grep) plus real non-ASCII round-trips
through the actual writers. The round-trip through an S-expr writer is a genuine Windows
lock: on Windows a bare writer would raise on ``→`` (U+2192 is not in cp1252) — here it
must survive.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"


def _py_files():
    return sorted(TOOLS.rglob("*.py"))


def _has_kw(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def _const_kw(call: ast.Call, name: str):
    for k in call.keywords:
        if k.arg == name and isinstance(k.value, ast.Constant):
            return k.value.value
    return None


def test_no_bare_file_encoding():
    """Every Path.read_text/write_text and text-mode open() under tools/ passes encoding=."""
    bad = []
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in ("read_text", "write_text"):
                # LM.read_text / LM.write_text are helpers that already set encoding+newline.
                if isinstance(fn.value, ast.Name) and fn.value.id == "LM":
                    continue
                if not _has_kw(node, "encoding"):
                    bad.append(f"{f.relative_to(REPO)}:{node.lineno}  {fn.attr}() missing encoding=")
            elif isinstance(fn, ast.Name) and fn.id == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                mode = _const_kw(node, "mode") if mode is None else mode
                is_binary = isinstance(mode, str) and "b" in mode
                if not is_binary and not _has_kw(node, "encoding"):
                    bad.append(f"{f.relative_to(REPO)}:{node.lineno}  text-mode open() missing encoding=")
    assert not bad, "Windows cp1252 hazard — add encoding='utf-8':\n" + "\n".join(bad)


def test_no_bare_subprocess_text():
    """subprocess run/Popen/check_output with text output must pass encoding= (else the
    child's stdout is decoded with the locale = cp1252 on Windows)."""
    bad = []
    for f in _py_files():
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name not in ("run", "Popen", "check_output"):
                continue
            wants_text = bool(_const_kw(node, "text")) or bool(_const_kw(node, "universal_newlines"))
            if wants_text and not _has_kw(node, "encoding"):
                bad.append(f"{f.relative_to(REPO)}:{node.lineno}  subprocess text= without encoding=")
    assert not bad, "Windows cp1252 hazard in subprocess text decode — add encoding='utf-8':\n" + "\n".join(bad)


# ── real non-ASCII round-trips through the actual writers (Windows content locks) ──────
_NONASCII = "45°C → hot µA Ω — done"   # 45°C → hot µA Ω — done


def test_nonascii_text_variable_roundtrip(tmp_path):
    """A project text variable with non-ASCII survives save_extended + load_extended."""
    import nd_project_settings_manager as PSM
    pro = tmp_path / "B.kicad_pro"
    pro.write_text("{}", encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    m.set_text_variable("NOTE", _NONASCII)
    assert m.save_extended(pro)
    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    assert m2.text_variables["NOTE"] == _NONASCII


def test_nonascii_sexpr_roundtrip(tmp_path):
    """Raw non-ASCII bytes survive the S-expr writer. On Windows a bare writer would raise
    on U+2192 (not in cp1252); this locks the utf-8 encoding on that path."""
    import LibraryManager as LM
    p = tmp_path / "x.kicad_sym"
    content = f'(kicad_symbol_lib (symbol "R" (property "Value" "10µF {_NONASCII}")))\n'
    LM.write_text(p, content)
    assert LM.read_text(p) == content
    # bytes on disk must be the utf-8 encoding, not cp1252 or mojibake
    assert p.read_bytes().decode("utf-8") == content
