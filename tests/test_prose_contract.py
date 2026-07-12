"""App-wide prose + token contract (design-rules.md §2, §3).

The settings tab already had an em-dash gate (test_settings_polish); this extends
the same guardrail across the whole shipped UI package, and adds the plural-tell
and category-contrast contracts so the visual/voice base stays airtight:

* No em dash in any rendered UI string literal (design-rules §2 forbids them). The
  scan is AST-based, so code comments never count (Python discards them); docstrings
  and the sanctioned null glyph (a bare "—") are the only exemptions, and the QSS
  source + dev-only audit tools are out of scope (they render no user prose).
* No lazy "(s)" / "(es)" plural tell in a rendered string — counts flow through
  ``ui.prose.plural`` so "1 error" / "2 errors" read correctly.
* ``plural`` and ``dot_css`` behave as specified.
* Every category hue clears WCAG 3:1 against every elevation it can sit on, in both
  themes, so a category dot / net glyph never sinks into its surface.
"""
import ast
import re
from pathlib import Path

import pytest

from tools.ui import theme as T
from tools.ui import widgets as W
from tools.ui.prose import plural, count

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "tools" / "ui"
EM_DASH = "—"

# Dev-only / non-prose modules: the QSS source string (CSS comments carry em dashes)
# and the drive/capability/render audit tools (developer console banners, never UI).
_NOT_UI_PROSE = {"theme.py", "drive_audit.py", "capability_audit.py", "render_gate.py"}

# UI-rendering modules that live OUTSIDE tools/ui/ but still emit rendered strings
# (the Bench pins tab backs its table headers / detail-row labels from here). They are
# in-scope for the same voice contract, so the gate scans them too.
_EXTRA_UI_MODULES = (ROOT / "tools" / "stm32_pins_tab.py",)


def _ui_py_files():
    for p in sorted(UI.rglob("*.py")):
        if "__pycache__" in p.parts or p.name in _NOT_UI_PROSE:
            continue
        yield p
    for p in _EXTRA_UI_MODULES:
        if p.exists():
            yield p


def _docstring_ids(tree: ast.AST) -> set:
    """id()s of the Constant nodes that are module/class/function docstrings — a
    docstring is prose ABOUT the code, never rendered, so it is exempt."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(getattr(body[0], "value", None), ast.Constant)
                    and isinstance(body[0].value.value, str)):
                ids.add(id(body[0].value))
    return ids


def test_no_em_dash_in_rendered_ui_strings():
    problems = []
    for p in _ui_py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in skip or EM_DASH not in node.value:
                continue
            if node.value.strip() == EM_DASH:          # the sanctioned null glyph
                continue
            problems.append(f"{p.name}:{node.lineno}  {node.value[:60]!r}")
    assert not problems, (
        "em dash in rendered UI copy (design-rules §2 — use a period/colon/semicolon "
        "or restructure; the bare null glyph is the only exemption):\n" + "\n".join(problems))


# The lazy pluralization tell: a rendered "(s)" or "(es)". Docstrings exempt (they may
# say "symbol(s)" describing behaviour); the QSS/dev tools are out of scope as above.
_PLURAL_TELL = re.compile(r"\((?:s|es)\)")


def test_no_lazy_plural_tell_in_rendered_ui_strings():
    problems = []
    for p in _ui_py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in skip:
                continue
            if _PLURAL_TELL.search(node.value):
                problems.append(f"{p.name}:{node.lineno}  {node.value[:60]!r}")
    assert not problems, (
        "lazy '(s)'/'(es)' plural tell in rendered copy — route the count through "
        "ui.prose.plural(n, noun):\n" + "\n".join(problems))


# Interactive labels (buttons / eyebrows / section headers / tags / menu entries) are
# Title Case — they never start lowercase (design-rules §Casing: "Title Case → structural
# labels, titles, buttons"). Body prose stays sentence case; that is not machine-checkable, so
# this gate covers only the label builders, whose first positional arg is the human label.
_LABEL_BUILDERS = {"btn", "eyebrow", "section_header", "menu_button", "toggle_chip", "tag", "action"}


def test_interactive_labels_are_not_lowercase():
    problems = []
    for p in _ui_py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else None)
            if name not in _LABEL_BUILDERS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                s = first.value.strip()
                if s and s[0].isalpha() and s[0].islower():
                    problems.append(f"{p.name}:{node.lineno}  {name}({s[:40]!r})")
    assert not problems, (
        "interactive label starts lowercase — labels / titles / buttons are Title Case "
        "(design-rules §Casing):\n" + "\n".join(problems))


def test_plural_agrees_in_number():
    assert plural(1, "error") == "1 error"
    assert plural(0, "error") == "0 errors"
    assert plural(2, "error") == "2 errors"
    assert plural(3, "entry", "entries") == "3 entries"
    assert plural(1, "entry", "entries") == "1 entry"
    assert plural(2.0, "file") == "2 files"          # a count arriving as float still agrees
    assert count(1, "part") == "1 part"              # the reading-alias is the same helper


def test_dot_css_is_a_true_circle():
    # radius is derived (size // 2) so the marker is always circular, never a stray
    # 3/4/5px literal scattered per call site.
    assert W.dot_css("#abcdef", 7) == "background:#abcdef;border-radius:3px;"
    assert W.dot_css("#abcdef", 9) == "background:#abcdef;border-radius:4px;"
    assert W.dot_css("#abcdef", 10) == "background:#abcdef;border-radius:5px;"


# Every category hue must clear WCAG 3:1 (non-text graphical contrast) against every
# elevation a dot / net glyph can sit on, in BOTH themes — else the marker sinks into
# its surface. 'card'/'raised' is the panel, 'inset' the hover/selected wash, 'canvas'
# the base; a dot appears on all three.
_CATEGORY_SURFACES = ("canvas", "card", "inset")
_MIN_GRAPHICAL_CONTRAST = 3.0


@pytest.mark.parametrize("dark", [True, False], ids=["dark", "light"])
def test_every_category_clears_3to1_on_every_surface(dark):
    T.set_theme(dark=dark)
    try:
        weak = []
        names = list(T.CATEGORY_DARK if dark else T.CATEGORY_LIGHT)
        for name in names:
            for surf in _CATEGORY_SURFACES:
                ratio = T.category_contrast(name, surf)
                if ratio < _MIN_GRAPHICAL_CONTRAST:
                    weak.append(f"{name} on {surf}: {ratio:.2f} (< {_MIN_GRAPHICAL_CONTRAST})")
        assert not weak, (
            f"category hue below 3:1 graphical contrast in {'dark' if dark else 'light'}:\n"
            + "\n".join(weak))
    finally:
        T.set_theme(dark=True)
