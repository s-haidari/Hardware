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


# ---------------------------------------------------------------------------
# CORRECTNESS: 'Strip All' on a component reference must peel only leading tag
# prefixes -- never scan for a designator inside the surviving name (which
# lowercased + truncated non-canonical refs, e.g. SH-MyResistor10 -> 'r10').
# ---------------------------------------------------------------------------
def test_ref_strip_all_preserves_noncanonical_body():
    assert W.ref_strip_all_tags("SH-MyResistor10") == "MyResistor10"
    assert W.ref_strip_all_tags("SH-R1") == "R1"
    assert W.ref_strip_all_tags("CG-U5") == "U5"
    assert W.ref_strip_all_tags("R1") == "R1"          # no tag -> unchanged
    assert W.ref_strip_all_tags("MyResistor10") == "MyResistor10"
    assert W.ref_strip_all_tags("SH-CG-R1") == "R1"    # stacked tags peeled


def test_strip_all_ref_end_to_end_no_case_mangle():
    """A schematic strip_all on a custom ref keeps the body verbatim."""
    sch = (
        '(kicad_sch\n'
        '  (symbol (lib_id "Device:R")\n'
        '    (property "Reference" "SH-MyResistor10" (at 0 0 0))\n'
        '  )\n'
        ')\n'
    )
    new, _counts, _samples, changes = W._transform_schematic(
        sch, "strip_all", None, touch_refs=True, touch_labels=False)
    assert '"MyResistor10"' in new
    assert '"r10"' not in new
    assert ("symbol_ref", "SH-MyResistor10", "MyResistor10", None) in changes


# ---------------------------------------------------------------------------
# CORRECTNESS: find/replace on a REFERENCE is whole-token -- find 'R1' must not
# rewrite 'R12' to 'X2' (raw substring replace corrupted superstrings).
# ---------------------------------------------------------------------------
def test_ref_find_replace_is_whole_token():
    assert W.ref_find_replace("R12", "R1", "X") == "R12"   # superstring untouched
    assert W.ref_find_replace("R1", "R1", "X") == "X"      # exact match replaced
    assert W.ref_find_replace("AR1", "R1", "X") == "AR1"   # mid-string untouched
    assert W.ref_find_replace("R1", "", "X") == "R1"       # empty find is a no-op


def test_find_replace_ref_end_to_end_spares_superstrings():
    sch = (
        '(kicad_sch\n'
        '  (symbol (lib_id "Device:R")\n'
        '    (property "Reference" "R1" (at 0 0 0))\n'
        '  )\n'
        '  (symbol (lib_id "Device:R")\n'
        '    (property "Reference" "R12" (at 0 0 0))\n'
        '  )\n'
        ')\n'
    )
    new, _counts, _samples, changes = W._transform_schematic(
        sch, "find_replace", "R1", repl="X", touch_refs=True, touch_labels=False)
    assert '"X"' in new          # R1 -> X
    assert '"R12"' in new        # R12 preserved, NOT 'X2'
    assert '"X2"' not in new
    ref_changes = [c for c in changes if c[0] == "symbol_ref"]
    assert ref_changes == [("symbol_ref", "R1", "X", None)]


# ---------------------------------------------------------------------------
# CORRECTNESS: PCB unannotate of a fiducial (no lib_id) must resolve to 'FID?',
# not 'D?' (single-letter 'D' used to match the 'D1' tail of 'FID1').
# ---------------------------------------------------------------------------
def test_unannotate_fiducial_pcb_path_no_libid():
    assert W.unannotate_ref_with_lib_id("FID1", None) == "FID?"
    assert W.unannotate_ref_with_lib_id("FID12", None) == "FID?"
    # the multi-char class must be listed ahead of single-letter 'D'
    assert W.STANDARD_DESIGNATORS.index("FID") < W.STANDARD_DESIGNATORS.index("D")
    # unaffected classes still resolve correctly
    assert W.unannotate_ref_with_lib_id("D1", None) == "D?"
    assert W.unannotate_ref_with_lib_id("CN3", None) == "CN?"


def test_unannotate_fiducial_pcb_end_to_end():
    pcb = (
        '(kicad_pcb\n'
        '  (footprint "Fiducial"\n'
        '    (fp_text reference "FID1" (at 0 0))\n'
        '  )\n'
        ')\n'
    )
    new, count, _samples, changes = W._transform_pcb(pcb, "unannotate", None)
    assert '"FID?"' in new
    assert '"D?"' not in new
    assert count == 1
    assert ("pcb_ref", "FID1", "FID?", None) in changes


# --- Modern KiCad (v7-9) footprint reference: (property "Reference" "U1" ...) ---
# The transform must rename the reference designator stored as a property inside a
# (footprint ...) block -- the form used by every v20260206 board in this repo --
# and must NOT touch a "Reference" property that lives outside a footprint block.

def test_transform_pcb_modern_property_reference_add_tag():
    pcb = (
        '(kicad_pcb\n'
        '  (footprint "Device:C"\n'
        '    (property "Reference" "C1"\n'
        '      (at 1.905 0 180)\n'
        '    )\n'
        '    (property "Value" "100nF"\n'
        '    )\n'
        '  )\n'
        '  (footprint "Device:R"\n'
        '    (property "Reference" "R7"\n'
        '    )\n'
        '  )\n'
        ')\n'
    )
    new, count, _samples, changes = W._transform_pcb(pcb, "add_tag", "SH-")
    assert count == 2
    assert '(property "Reference" "SH-C1"' in new
    assert '(property "Reference" "SH-R7"' in new
    # the Value property must be untouched
    assert '(property "Value" "100nF"' in new
    assert ("pcb_ref", "C1", "SH-C1", None) in changes
    assert ("pcb_ref", "R7", "SH-R7", None) in changes


def test_transform_pcb_property_reference_only_inside_footprint():
    # A (property "Reference" ...) that is NOT inside a footprint block (e.g. a
    # hypothetical top-level property) must never be rewritten.
    pcb = (
        '(kicad_pcb\n'
        '  (property "Reference" "SHOULD_NOT_CHANGE"\n'
        '  )\n'
        '  (footprint "Device:C"\n'
        '    (property "Reference" "C1"\n'
        '    )\n'
        '  )\n'
        ')\n'
    )
    new, count, _samples, _changes = W._transform_pcb(pcb, "add_tag", "SH-")
    assert count == 1
    assert '(property "Reference" "SHOULD_NOT_CHANGE"' in new
    assert '(property "Reference" "SH-C1"' in new


def test_transform_pcb_footprint_boundary_survives_paren_in_string():
    # A stray '(' inside a quoted Value/Description must not desync the footprint
    # block boundary, so a following footprint's reference is still renamed and a
    # following top-level property is still left alone.
    pcb = (
        '(kicad_pcb\n'
        '  (footprint "Device:C"\n'
        '    (property "Value" "Cap (SMD)"\n'
        '    )\n'
        '    (property "Reference" "C1"\n'
        '    )\n'
        '  )\n'
        '  (footprint "Device:R"\n'
        '    (property "Reference" "R2"\n'
        '    )\n'
        '  )\n'
        ')\n'
    )
    new, count, _samples, _changes = W._transform_pcb(pcb, "add_tag", "SH-")
    assert count == 2
    assert '(property "Reference" "SH-C1"' in new
    assert '(property "Reference" "SH-R2"' in new


def test_transform_pcb_real_v20260206_board_renames_every_footprint_ref():
    """Regression: on a real v20260206 board (property-Reference form, no
    fp_text-reference lines) add_tag/strip_all/unannotate must rename EVERY
    footprint reference designator -- the old fp_text-only transform returned 0.
    """
    import re

    board = (
        Path(__file__).resolve().parent / "fixtures" / "rp2040_pico30.kicad_pcb"
    )
    if not board.exists():
        pytest.skip("corpus board not present in this checkout")

    content = board.read_text(encoding="utf-8")
    orig_refs = re.findall(r'\(property "Reference" "([^"]+)"', content)
    assert orig_refs, "fixture board should contain property-Reference designators"
    # sanity: this is the modern form -- no legacy fp_text reference lines
    assert 'fp_text "reference"' not in content
    assert 'fp_text reference' not in content

    def refs(text):
        return re.findall(r'\(property "Reference" "([^"]+)"', text)

    # add_tag: every ref gains the prefix; count == number of footprints
    new, count, _s, _c = W._transform_pcb(content, "add_tag", "SH-", src_path=board)
    assert count == len(orig_refs)
    tagged = refs(new)
    assert len(tagged) == len(orig_refs)
    assert all(r.startswith("SH-") for r in tagged)

    # strip_all: reaches every footprint's reference property (only the tagged
    # ones actually change, but the transform must visit all of them). Assert it
    # renames exactly the refs that carry a tag -- proving the property form is
    # now driven, where the old fp_text-only path visited none.
    would_change = [r for r in orig_refs if W.ref_strip_all_tags(r) != r]
    assert would_change, "fixture should contain at least one tagged reference"
    _new2, count2, _s2, _c2 = W._transform_pcb(content, "strip_all", "SH-", src_path=board)
    assert count2 == len(would_change)

    # unannotate: every ref collapses to <class>?
    newU, countU, _sU, _cU = W._transform_pcb(content, "unannotate", None, src_path=board)
    assert countU == len(orig_refs)
    assert all(r.endswith("?") for r in refs(newU))
