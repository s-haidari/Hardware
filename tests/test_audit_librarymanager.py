"""Regression tests for the audit fixes in tools/LibraryManager.py.

Covers three bugs found by the codebase audit:

1. HIGH  empty-lib template was paren-imbalanced. The header string
   '(kicad_symbol_lib ... (generator "LibraryManager.py"))\\n)\\n' closed the
   lib TWICE (once after generator, once on the next line), so every
   fresh-create / full-rewrite path emitted an invalid .kicad_sym with a stray
   trailing ')'. Fixed so the header leaves the lib OPEN and the final '\\n)'
   closes it exactly once.

2. MEDIUM insert_blocks_into_target counted parens inside quoted strings, so a
   description like "smiley :)" drove depth to 0 early and spliced new blocks
   into the MIDDLE of an existing symbol. Fixed to skip quoted strings
   (escape-aware), mirroring extract_symbol_blocks.

3. HIGH  git commit could publish corruption. Added pure guards
   has_conflict_markers()/is_paren_balanced() (+ find_corrupt_kicad_files) and
   made git_stage_commit refuse to stage/commit when any tracked KiCad file
   still holds merge-conflict markers or is paren-unbalanced.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


class DummyLog:
    """Stand-in for UILog that just records messages."""
    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(msg)


# --- Bug 1: empty-lib template must be paren-balanced ----------------------
def test_ensure_target_header_is_balanced(tmp_path):
    """A freshly-created empty library must have balanced parens (was: stray ')')."""
    sym = tmp_path / "MySymbols.kicad_sym"
    L.ensure_target_header(sym)
    text = sym.read_text(encoding="utf-8")
    assert L.is_paren_balanced(text), text
    # Exactly one top-level open (kicad_symbol_lib and one final close.
    assert text.count("(kicad_symbol_lib") == 1
    # No symbols yet, and the scanner is happy with the file.
    assert L.extract_symbol_blocks(text) == []


def test_insert_fallback_template_is_balanced():
    """The last_close-is-None fallback path must emit a balanced lib."""
    # No top-level closing paren present -> triggers the fallback branch.
    out = L.insert_blocks_into_target("", ['(symbol "A" (pin 1))'])
    assert L.is_paren_balanced(out), out
    names = [L.extract_symbol_name(b) for b in L.extract_symbol_blocks(out)]
    assert names == ["A"]


def test_full_rewrite_round_trips_and_stays_balanced(tmp_path):
    """remove_symbol_by_name rewrites via the template; result must stay valid."""
    sym = tmp_path / "s.kicad_sym"
    header = '(kicad_symbol_lib (version 20211014) (generator "t")\n'
    body = '  (symbol "A" (pin 1))\n  (symbol "B" (pin 1))\n'
    sym.write_text(header + body + ")\n", encoding="utf-8")
    assert L.remove_symbol_by_name(sym, "A", DummyLog()) is True
    text = sym.read_text(encoding="utf-8")
    assert L.is_paren_balanced(text), text
    names = [L.extract_symbol_name(b) for b in L.extract_symbol_blocks(text)]
    assert names == ["B"]


# --- Bug 2: paren scan must ignore parens inside quoted strings ------------
def _lib_with_paren_in_string():
    """A single symbol whose Description property literally contains ':)'."""
    return (
        '(kicad_symbol_lib (version 20211014) (generator "t")\n'
        '  (symbol "U1"\n'
        '    (property "Description" "smiley :)")\n'
        '    (pin 1)\n'
        '  )\n'
        ')\n'
    )


def test_insert_does_not_splice_inside_symbol_with_paren_in_string():
    """The ')' inside "smiley :)" must NOT be treated as a real closing paren.

    Pre-fix, the depth scan hit 0 at U1's own closing paren and spliced the new
    block INSIDE U1, so extract would see only one top-level symbol. Post-fix
    the new block lands after U1, giving two clean top-level symbols.
    """
    target = _lib_with_paren_in_string()
    out = L.insert_blocks_into_target(target, ['  (symbol "U2" (pin 1))'])

    assert L.is_paren_balanced(out), out
    blocks = L.extract_symbol_blocks(out)
    names = [L.extract_symbol_name(b) for b in blocks]
    assert names == ["U1", "U2"], out

    # U1's block is intact (still holds the smiley) and does NOT contain U2.
    u1 = blocks[0]
    assert "smiley :)" in u1
    assert "U2" not in u1
    # The new symbol appears AFTER the smiley string in the file, not before.
    assert out.index('"U2"') > out.index("smiley :)")


def test_merge_preserves_symbol_with_paren_in_string(tmp_path):
    """End-to-end: merging a new symbol into a lib whose existing symbol has a
    ')' in a string keeps both symbols as distinct top-level blocks."""
    target = tmp_path / "MySymbols.kicad_sym"
    target.write_text(_lib_with_paren_in_string(), encoding="utf-8")
    src = tmp_path / "new.kicad_sym"
    src.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "t")\n'
        '  (symbol "U2" (pin 1))\n)\n',
        encoding="utf-8",
    )
    L.merge_symbols(target, [src], DummyLog())
    text = target.read_text(encoding="utf-8")
    assert L.is_paren_balanced(text), text
    names = [L.extract_symbol_name(b) for b in L.extract_symbol_blocks(text)]
    assert sorted(names) == ["U1", "U2"]


# --- Bug 3a: has_conflict_markers -----------------------------------------
def test_has_conflict_markers_detects_each_marker():
    for marker in ("<<<<<<< HEAD", "=======", ">>>>>>> theirs"):
        text = "(kicad_symbol_lib\n" + marker + "\n)\n"
        assert L.has_conflict_markers(text), marker


def test_has_conflict_markers_clean_file():
    assert not L.has_conflict_markers('(kicad_symbol_lib (version 20211014))\n')
    # A '=======' that is NOT at the start of a line is not a conflict marker.
    assert not L.has_conflict_markers('(symbol "x" "a======= b")\n')


# --- Bug 3b: is_paren_balanced --------------------------------------------
def test_is_paren_balanced_basic():
    assert L.is_paren_balanced("()")
    assert L.is_paren_balanced("(a (b) (c (d)))")
    assert not L.is_paren_balanced("(a (b)")        # missing close
    assert not L.is_paren_balanced("(a))")          # extra close
    assert not L.is_paren_balanced(")(")            # goes negative early


def test_is_paren_balanced_ignores_parens_in_strings():
    # Balanced structurally even though the string holds unmatched parens.
    assert L.is_paren_balanced('(symbol "U1" (property "d" "smiley :)"))')
    assert L.is_paren_balanced('(a "((((")')        # only string parens
    # Escaped quote inside a string must not end the string early.
    assert L.is_paren_balanced('(a "he said \\") not done")')


def test_is_paren_balanced_rejects_old_template():
    """The exact pre-fix header (double lib close) must be flagged unbalanced."""
    old = '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n'
    assert not L.is_paren_balanced(old)


# --- Bug 3c: find_corrupt_kicad_files + git_stage_commit guard -------------
def test_find_corrupt_kicad_files(tmp_path):
    good = tmp_path / "good.kicad_sym"
    good.write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    conflicted = tmp_path / "conflicted.kicad_sch"
    conflicted.write_text('(kicad_sch\n<<<<<<< HEAD\n)\n', encoding="utf-8")
    unbalanced = tmp_path / "unbalanced.kicad_pcb"
    unbalanced.write_text('(kicad_pcb (foo)\n', encoding="utf-8")
    # A non-KiCad file with markers must be ignored by this scan.
    (tmp_path / "notes.txt").write_text("<<<<<<< HEAD\n", encoding="utf-8")

    bad = L.find_corrupt_kicad_files(tmp_path)
    bad_names = {p.name for p, _ in bad}
    assert bad_names == {"conflicted.kicad_sch", "unbalanced.kicad_pcb"}
    reasons = {p.name: reason for p, reason in bad}
    assert "conflict" in reasons["conflicted.kicad_sch"]
    assert "paren" in reasons["unbalanced.kicad_pcb"].lower()


def test_git_stage_commit_aborts_on_corruption(tmp_path):
    """git_stage_commit must refuse (return False) and never touch git when a
    tracked KiCad file is corrupt."""
    (tmp_path / "libs").mkdir()
    (tmp_path / "libs" / "MySymbols.kicad_sym").write_text(
        '(kicad_symbol_lib\n=======\n)\n', encoding="utf-8"
    )
    cfg = {"RepoRoot": str(tmp_path)}
    log = DummyLog()
    assert L.git_stage_commit(cfg, log, message="x") is False
    joined = "\n".join(log.lines)
    assert "ABORTED" in joined
    assert "MySymbols.kicad_sym" in joined


def test_git_stage_commit_guard_passes_when_clean(tmp_path, monkeypatch):
    """A clean tree passes the corruption guard and proceeds to `git add`.

    We stub run_git/git_has_staged_changes so the test never shells out to git;
    the point is only that the guard did not abort.
    """
    (tmp_path / "libs").mkdir()
    (tmp_path / "libs" / "MySymbols.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20211014) (generator "t")\n)\n', encoding="utf-8"
    )
    calls = []
    monkeypatch.setattr(L, "run_git", lambda args, cfg, log: calls.append(args))
    monkeypatch.setattr(L, "git_has_staged_changes", lambda cfg: True)
    cfg = {"RepoRoot": str(tmp_path)}
    log = DummyLog()
    assert L.git_stage_commit(cfg, log, message="ok") is True
    # Proceeded past the guard: staged then committed.
    assert ["add", "-A"] in calls
    assert any(a[0] == "commit" for a in calls)
