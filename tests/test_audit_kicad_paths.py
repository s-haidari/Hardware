"""Regression tests for kicad_paths.pick_newest_kicad (audit MEDIUM fix).

Bug: find_kicad_bin picked installs by lexicographic string sort, so '10.0'
sorted BEFORE '9.0' and the OLDER KiCad was selected. Fixed by factoring the
version-picking into a pure helper (pick_newest_kicad) that sorts by the
PARSED version tuple and takes max(), after resolving both x86/x64 globs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import kicad_paths  # noqa: E402
from kicad_paths import (  # noqa: E402
    pick_newest_kicad,
    _kicad_version_key,
    find_kicad_bin,
    find_kicad_cli,
)


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


# --- find_kicad_bin cross-platform symmetry (coherence fix) -----------------
# find_kicad_bin used to glob ONLY the two Windows Program Files dirs, so on
# POSIX it returned None even when kicad-cli was on PATH — asymmetric with its
# sibling find_kicad_cli, which does a which() fallback. These tests drive the
# real POSIX branch.


def test_find_kicad_bin_posix_uses_path_cli(monkeypatch, tmp_path):
    """On POSIX, find_kicad_bin returns the bin dir of kicad-cli on PATH."""
    bin_dir = tmp_path / "usr" / "local" / "bin"
    bin_dir.mkdir(parents=True)
    cli = bin_dir / "kicad-cli"
    cli.write_text("", encoding="utf-8")

    monkeypatch.delenv("KICAD_BIN", raising=False)
    monkeypatch.setattr(kicad_paths.sys, "platform", "linux")
    monkeypatch.setattr(
        kicad_paths, "which", lambda name: str(cli) if name == "kicad-cli" else None
    )

    assert find_kicad_bin() == bin_dir


def test_find_kicad_bin_posix_none_when_absent(monkeypatch):
    """On POSIX with nothing on PATH and no known prefix, returns None (not a crash)."""
    monkeypatch.delenv("KICAD_BIN", raising=False)
    monkeypatch.setattr(kicad_paths.sys, "platform", "linux")
    monkeypatch.setattr(kicad_paths, "which", lambda name: None)
    # Point every probed prefix at a dir that has no kicad-cli.
    monkeypatch.setattr(kicad_paths.Path, "exists", lambda self: False)

    assert find_kicad_bin() is None


def test_find_kicad_cli_posix_joins_plain_name(monkeypatch, tmp_path):
    """find_kicad_cli must join 'kicad-cli' (no .exe) with the POSIX bin dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "kicad-cli"
    cli.write_text("", encoding="utf-8")

    monkeypatch.delenv("KICAD_BIN", raising=False)
    monkeypatch.setattr(kicad_paths.sys, "platform", "linux")
    monkeypatch.setattr(
        kicad_paths, "which", lambda name: str(cli) if name == "kicad-cli" else None
    )

    assert find_kicad_cli() == str(cli)


def test_find_kicad_bin_env_override_wins(monkeypatch, tmp_path):
    """KICAD_BIN, when it exists, short-circuits before any platform branch."""
    monkeypatch.setenv("KICAD_BIN", str(tmp_path))
    # Even if we claim Windows, the existing env path must win.
    monkeypatch.setattr(kicad_paths.sys, "platform", "win32")
    assert find_kicad_bin() == Path(str(tmp_path))


# --- KICAD_BIN set-but-missing must WARN, not silently no-op (polish fix) ----
# Previously a typo'd/moved KICAD_BIN failed .exists() and fell through to
# auto-detect with zero feedback, so the user believed their override took
# effect. Now it must emit a one-line stderr warning naming the bad path.


def test_find_kicad_bin_missing_env_warns_and_autodetects(
    monkeypatch, tmp_path, capsys
):
    """A set-but-nonexistent KICAD_BIN warns on stderr and still auto-detects."""
    missing = tmp_path / "nope" / "does-not-exist"
    assert not missing.exists()
    monkeypatch.setenv("KICAD_BIN", str(missing))
    # Force the POSIX auto-detect branch to a deterministic hit.
    bin_dir = tmp_path / "real" / "bin"
    bin_dir.mkdir(parents=True)
    cli = bin_dir / "kicad-cli"
    cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(kicad_paths.sys, "platform", "linux")
    monkeypatch.setattr(
        kicad_paths, "which", lambda name: str(cli) if name == "kicad-cli" else None
    )

    # It must NOT return the bad env path — it falls through to auto-detect.
    assert find_kicad_bin() == bin_dir

    err = capsys.readouterr().err
    assert "KICAD_BIN" in err
    assert str(missing) in err


def test_find_kicad_bin_valid_env_does_not_warn(monkeypatch, tmp_path, capsys):
    """A valid KICAD_BIN returns silently — no spurious warning."""
    monkeypatch.setenv("KICAD_BIN", str(tmp_path))
    monkeypatch.setattr(kicad_paths.sys, "platform", "win32")

    assert find_kicad_bin() == Path(str(tmp_path))
    assert capsys.readouterr().err == ""
