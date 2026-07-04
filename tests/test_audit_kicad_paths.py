"""Regression tests for kicad_paths.pick_newest_kicad (audit MEDIUM fix).

Bug: find_kicad_bin picked installs by lexicographic string sort, so '10.0'
sorted BEFORE '9.0' and the OLDER KiCad was selected. Fixed by factoring the
version-picking into a pure helper (pick_newest_kicad) that sorts by the
PARSED version tuple and takes max(), after resolving both x86/x64 globs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from kicad_paths import pick_newest_kicad, _kicad_version_key  # noqa: E402


def test_10_beats_9_not_string_sort():
    """The whole point: 10.0 must win over 9.0 despite '1' < '9' as strings."""
    paths = [
        r"C:\Program Files\KiCad\9.0\bin",
        r"C:\Program Files\KiCad\10.0\bin",
    ]
    assert pick_newest_kicad(paths) == r"C:\Program Files\KiCad\10.0\bin"


def test_order_independent():
    """Selection must not depend on the order candidates are supplied."""
    a = r"C:\Program Files\KiCad\10.0\bin"
    b = r"C:\Program Files\KiCad\9.0\bin"
    assert pick_newest_kicad([a, b]) == a
    assert pick_newest_kicad([b, a]) == a


def test_many_versions_picks_highest():
    paths = [
        r"C:\Program Files\KiCad\7.0\bin",
        r"C:\Program Files\KiCad\8.0\bin",
        r"C:\Program Files\KiCad\10.0\bin",
        r"C:\Program Files\KiCad\9.0\bin",
    ]
    assert pick_newest_kicad(paths) == r"C:\Program Files\KiCad\10.0\bin"


def test_minor_version_compared_numerically():
    """10.2 must beat 10.10? No — 10.10 > 10.2 numerically. Verify tuples."""
    paths = [
        r"C:\Program Files\KiCad\10.2\bin",
        r"C:\Program Files\KiCad\10.10\bin",
    ]
    assert pick_newest_kicad(paths) == r"C:\Program Files\KiCad\10.10\bin"


def test_mixed_x86_x64_same_version_prefers_64bit():
    """Both globs resolved together; on a version tie the 64-bit install wins."""
    x64 = r"C:\Program Files\KiCad\9.0\bin"
    x86 = r"C:\Program Files (x86)\KiCad\9.0\bin"
    assert pick_newest_kicad([x86, x64]) == x64
    assert pick_newest_kicad([x64, x86]) == x64


def test_newer_x86_beats_older_x64():
    """Version dominates the arch tiebreak: a newer x86 beats an older x64."""
    x64_old = r"C:\Program Files\KiCad\9.0\bin"
    x86_new = r"C:\Program Files (x86)\KiCad\10.0\bin"
    assert pick_newest_kicad([x64_old, x86_new]) == x86_new


def test_empty_returns_none():
    assert pick_newest_kicad([]) is None


def test_falsy_entries_ignored():
    assert pick_newest_kicad([None, "", r"C:\Program Files\KiCad\9.0\bin"]) == (
        r"C:\Program Files\KiCad\9.0\bin"
    )
    assert pick_newest_kicad([None, ""]) is None


def test_unparseable_version_sorts_lowest():
    """A version dir with no digits must not be chosen over a real version."""
    real = r"C:\Program Files\KiCad\9.0\bin"
    weird = r"C:\Program Files\KiCad\nightly\bin"
    assert pick_newest_kicad([weird, real]) == real


def test_version_key_parses_tuple():
    assert _kicad_version_key(r"C:\Program Files\KiCad\10.0\bin")[0] == (10, 0)
    assert _kicad_version_key(r"C:\Program Files\KiCad\9.0\bin")[0] == (9, 0)
    # Numeric tuple ordering, the crux of the bug:
    assert _kicad_version_key(r"...\10.0\bin") > _kicad_version_key(r"...\9.0\bin")
