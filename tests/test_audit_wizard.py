"""Regression tests for the audit fixes in tools/nd_wizard.py.

Covers the pure-logic fixes that can be verified without the Qt GUI:

  * atomic Apply -- all-or-nothing staging + rollback on a mid-write failure
    (HIGH: a locked/bad file must not leave a half-renamed project);
  * LF preservation on write (MEDIUM: no CRLF flip);
  * timestamped, non-clobbering .bak (MEDIUM: never overwrite a pristine backup);
  * (lib_symbols ...) boundary scan that ignores parens inside quoted strings
    (MEDIUM: a stray paren in a Description must not desync ref renaming);
  * should_ignore_path testing only components relative to the search root
    (LOW: a hidden *ancestor* dir must not hide every file).

Run:  python -m pytest tests/test_audit_wizard.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_wizard as W  # noqa: E402

TS = "20260704_120000"


def write_lf(path: Path, text: str) -> None:
    """Write a source file with real LF endings (no CRLF), like a KiCad file."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# MEDIUM: LF preservation
# ---------------------------------------------------------------------------
def test_write_text_lf_preserves_lf(tmp_path):
    p = tmp_path / "f.kicad_sch"
    W._write_text_lf(p, "a\nb\nc\n")
    data = read_bytes(p)
    assert b"\r" not in data
    assert data == b"a\nb\nc\n"


# ---------------------------------------------------------------------------
# MEDIUM: timestamped, non-clobbering backups
# ---------------------------------------------------------------------------
def test_make_backup_never_clobbers_pristine(tmp_path):
    f = tmp_path / "x.kicad_sch"
    write_lf(f, "ORIG")

    b1 = W._make_backup(f, TS)
    assert b1.name == "x.kicad_sch.20260704_120000.bak"
    assert b1.read_text(encoding="utf-8") == "ORIG"

    # Second run in the same second, after the file was modified, must NOT
    # overwrite the pristine backup.
    write_lf(f, "MODIFIED")
    b2 = W._make_backup(f, TS)
    assert b2 != b1
    assert b1.read_text(encoding="utf-8") == "ORIG"       # pristine preserved
    assert b2.read_text(encoding="utf-8") == "MODIFIED"


# ---------------------------------------------------------------------------
# HIGH: atomic apply -- success, rollback, and stage-failure paths
# ---------------------------------------------------------------------------
def _make_files(tmp_path):
    f1 = tmp_path / "a.kicad_sch"
    f2 = tmp_path / "b.kicad_sch"
    write_lf(f1, "A\n")
    write_lf(f2, "B\n")
    t1 = lambda: ("A2\n", [("x", "A", "A2", f1)])          # noqa: E731
    t2 = lambda: ("B2\n", [("x", "B", "B2", f2)])          # noqa: E731
    return f1, f2, t1, t2


def test_atomic_apply_writes_all_on_success(tmp_path):
    f1, f2, t1, t2 = _make_files(tmp_path)
    applied, backups = W.apply_transforms_atomically([(f1, t1), (f2, t2)], TS)

    assert read_bytes(f1) == b"A2\n"
    assert read_bytes(f2) == b"B2\n"
    assert b"\r" not in read_bytes(f1)                     # LF preserved through commit
    assert len(applied) == 2
    assert len(backups) == 2
    # backups captured the originals
    assert {Path(b).read_text(encoding="utf-8") for b in backups} == {"A\n", "B\n"}


def test_atomic_apply_skips_unchanged_files(tmp_path):
    f1 = tmp_path / "a.kicad_sch"
    write_lf(f1, "A\n")
    nochange = lambda: ("A\n", [])                         # noqa: E731
    applied, backups = W.apply_transforms_atomically([(f1, nochange)], TS)
    assert applied == []
    assert backups == []
    assert list(tmp_path.glob("*.bak")) == []              # nothing backed up
    assert read_bytes(f1) == b"A\n"


def test_atomic_apply_rollback_on_write_failure(tmp_path):
    f1, f2, t1, t2 = _make_files(tmp_path)

    def failing_write(path, content):
        if Path(path).name == "b.kicad_sch":
            raise PermissionError("file is locked by KiCad")
        W._write_text_lf(path, content)

    with pytest.raises(W.ApplyError) as ei:
        W.apply_transforms_atomically([(f1, t1), (f2, t2)], TS, write_fn=failing_write)

    err = ei.value
    assert err.stage == "write"
    assert err.path == Path(f2)
    # f1 was written first, then rolled back to its pristine content; f2 untouched.
    assert read_bytes(f1) == b"A\n"
    assert read_bytes(f2) == b"B\n"


def test_atomic_apply_stage_failure_writes_nothing(tmp_path):
    f1, f2, t1, _ = _make_files(tmp_path)

    def bad_transform():
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "boom")

    with pytest.raises(W.ApplyError) as ei:
        W.apply_transforms_atomically([(f1, t1), (f2, bad_transform)], TS)

    err = ei.value
    assert err.stage == "transform"
    assert err.path == Path(f2)
    # Failure during in-memory staging must leave EVERY file untouched and make
    # no backups at all.
    assert read_bytes(f1) == b"A\n"
    assert read_bytes(f2) == b"B\n"
    assert list(tmp_path.glob("*.bak")) == []


# ---------------------------------------------------------------------------
# MEDIUM: (lib_symbols ...) paren scan ignores quoted-string parens
# ---------------------------------------------------------------------------
def test_paren_delta_outside_strings():
    assert W._paren_delta_outside_strings("(a (b) c)") == 0
    assert W._paren_delta_outside_strings("  (lib_symbols") == 1
    # a stray '(' inside a string must not count
    assert W._paren_delta_outside_strings('(property "d" "x (y")') == 0
    # a stray ')' inside a string must not count
    assert W._paren_delta_outside_strings('"a )b )c" (') == 1
    # escaped quote inside a string keeps us in-string until the real close
    assert W._paren_delta_outside_strings('"a\\"b"(') == 1


LIBSYM_STRAY_PAREN = (
    "(kicad_sch\n"
    "  (lib_symbols\n"
    '    (symbol "Device:R"\n'
    '      (property "Reference" "R" (at 0 0 0))\n'
    '      (property "Description" "Resistor (SMD size")\n'   # <-- unbalanced '(' in string
    "    )\n"
    "  )\n"
    "  (symbol\n"
    '    (lib_id "Device:R")\n'
    '    (property "Reference" "R1" (at 10 10 0))\n'
    "  )\n"
    ")\n"
)


def test_lib_symbols_boundary_survives_stray_paren():
    new_content, counts, _samples, changes = W._transform_schematic(
        LIBSYM_STRAY_PAREN, "add_tag", "TP-", touch_refs=True, touch_labels=False
    )
    # The instance reference OUTSIDE lib_symbols must be renamed even though a
    # stray '(' appeared in a Description inside the block (the old paren counter
    # would have stayed "inside" and skipped it).
    assert '"TP-R1"' in new_content
    assert counts["symbol_ref"] == 1
    # The template reference INSIDE lib_symbols must stay untouched.
    assert '"TP-R"' not in new_content
    assert ("symbol_ref", "R1", "TP-R1", None) in changes


# ---------------------------------------------------------------------------
# Guard: label ops still route through the (already fixed) label-safe strip
# ---------------------------------------------------------------------------
STRIP_LABELS = (
    "(kicad_sch\n"
    '  (label "I2C1_SDA" (at 0 0 0))\n'      # no tag prefix -> must be preserved
    '  (label "SH-CLK" (at 1 1 0))\n'        # real tag prefix -> stripped
    ")\n"
)


def test_strip_all_is_label_safe(tmp_path):
    new_content, counts, _s, _c = W._transform_schematic(
        STRIP_LABELS, "strip_all", None, touch_refs=False, touch_labels=True
    )
    assert '"I2C1_SDA"' in new_content          # NOT mangled to "C1_SDA"
    assert '"CLK"' in new_content               # "SH-" prefix removed
    assert '"SH-CLK"' not in new_content
    assert counts["local"] == 1                 # only the tagged label changed


# ---------------------------------------------------------------------------
# LOW: should_ignore_path only inspects components relative to the root
# ---------------------------------------------------------------------------
def test_should_ignore_path_uses_root_relative_components(tmp_path):
    root = tmp_path / ".hiddenparent" / "proj"          # hidden ANCESTOR of root
    child = root / "sub" / "x.kicad_sch"

    # With root given, the hidden ancestor is not considered -> not ignored.
    assert W.should_ignore_path(child, root) is False
    # Hidden / .history components AT OR BELOW the root are still ignored.
    assert W.should_ignore_path(root / ".history" / "x.kicad_sch", root) is True
    assert W.should_ignore_path(root / ".foo" / "x.kicad_sch", root) is True
    # Legacy behavior (no root) demonstrates the bug it fixes: the hidden
    # ancestor makes everything look ignored.
    assert W.should_ignore_path(child) is True


# ---------------------------------------------------------------------------
# End-to-end: the real schematic task builder through the atomic committer
# ---------------------------------------------------------------------------
E2E_SCH = (
    "(kicad_sch\n"
    "  (lib_symbols\n"
    '    (symbol "Device:R"\n'
    '      (property "Reference" "R" (at 0 0 0))\n'
    "    )\n"
    "  )\n"
    "  (symbol\n"
    '    (lib_id "Device:R")\n'
    '    (property "Reference" "R1" (at 10 10 0))\n'
    "  )\n"
    ")\n"
)


def test_end_to_end_sch_task_atomic_apply_preserves_lf(tmp_path):
    sch = tmp_path / "board.kicad_sch"
    write_lf(sch, E2E_SCH)

    task = W._make_sch_task(sch, "add_tag", "TP-", None,
                            touch_refs=True, touch_labels=False)
    applied, backups = W.apply_transforms_atomically([(sch, task)], TS)

    data = read_bytes(sch)
    assert b"\r" not in data                    # never flipped to CRLF
    assert '"TP-R1"' in data.decode("utf-8")    # instance renamed
    assert '"TP-R"' not in data.decode("utf-8")  # template left alone
    assert len(applied) == 1
    assert len(backups) == 1
    # timestamped backup sits next to the file and holds the pristine original
    bak = backups[0]
    assert bak.name == "board.kicad_sch.20260704_120000.bak"
    assert bak.read_text(encoding="utf-8") == E2E_SCH
