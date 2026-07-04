"""Regression tests for tools/nd_git.py — the thin git backend.

Covers the new pure/logic-first git wrapper (audit Feature A: change repo, show
status, stage/commit from the app):
  * repo identity: is_git_repo / repo_root / current_branch
  * status transitions: untracked -> staged -> committed(clean) -> modified
  * stage / unstage
  * commit returns (ok, sha) and refuses corrupt KiCad content
  * guard_no_corrupt_kicad + local conflict-marker / paren scanners
  * set_repo / init_repo validation

All git-backed tests skip cleanly when 'git' is not on PATH.

Run:  python -m pytest tests/test_backend_git.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_git  # noqa: E402

pytestmark = pytest.mark.skipif(not nd_git.have_git(), reason="git not on PATH")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """Give git a committer identity via env (no raw subprocess, no dependence on
    the machine's global git config) so commit() succeeds under test."""
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Tester")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "tester@example.com")


# ── helpers ───────────────────────────────────────────────────────────────
def _new_repo(tmp_path):
    """Create a fresh git repo via the public API, return its path."""
    repo = tmp_path / "hw"
    res = nd_git.init_repo(repo)
    assert res.ok, res.err
    return repo


def _write(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


_GOOD_SYM = '(kicad_symbol_lib (version 20211014) (generator test))\n'
_CONFLICT_SYM = (
    "(kicad_symbol_lib\n"
    "<<<<<<< HEAD\n"
    "  (symbol \"A\")\n"
    "=======\n"
    "  (symbol \"B\")\n"
    ">>>>>>> other\n"
    ")\n"
)


# ── pure scanners (no git needed, but harmless under skip guard) ───────────
def test_conflict_marker_scanner():
    assert nd_git.has_conflict_markers(_CONFLICT_SYM) is True
    assert nd_git.has_conflict_markers(_GOOD_SYM) is False


def test_paren_balance_scanner():
    assert nd_git.is_paren_balanced(_GOOD_SYM) is True
    assert nd_git.is_paren_balanced("(a (b)") is False
    assert nd_git.is_paren_balanced("(a))") is False
    # parens inside quoted strings must not count
    assert nd_git.is_paren_balanced('(name "a (b) c")') is True


# ── repo identity ──────────────────────────────────────────────────────────
def test_init_and_is_git_repo(tmp_path):
    repo = _new_repo(tmp_path)
    assert nd_git.is_git_repo(repo) is True
    assert nd_git.is_git_repo(tmp_path / "not_a_repo") is False


def test_repo_root_resolves(tmp_path):
    repo = _new_repo(tmp_path)
    sub = repo / "libs"
    sub.mkdir()
    root = nd_git.repo_root(sub)
    assert root is not None
    # git may report a differently-cased/short path on Windows; compare by inode.
    import os
    assert os.path.samefile(root, repo)
    assert nd_git.repo_root(tmp_path / "nope") is None


def test_current_branch_on_unborn_repo(tmp_path):
    repo = _new_repo(tmp_path)
    branch = nd_git.current_branch(repo)
    # Fresh repo before first commit still reports its (unborn) branch name.
    assert isinstance(branch, str) and branch
    assert branch in ("main", "master") or len(branch) > 0


# ── status transitions ─────────────────────────────────────────────────────
def test_status_untracked_to_staged_to_clean_to_modified(tmp_path):
    repo = _new_repo(tmp_path)

    # 1) fresh file -> untracked
    _write(repo, "notes.txt", "hello\n")
    st = nd_git.status(repo)
    assert st["clean"] is False
    assert "notes.txt" in st["untracked"]
    assert st["staged"] == [] and st["modified"] == []

    # 2) stage it -> staged
    assert nd_git.stage(repo, "notes.txt").ok
    st = nd_git.status(repo)
    assert "notes.txt" in st["staged"]
    assert "notes.txt" not in st["untracked"]

    # 3) commit -> clean
    ok, sha = nd_git.commit(repo, "add notes")
    assert ok, sha
    assert len(sha) >= 7
    st = nd_git.status(repo)
    assert st["clean"] is True
    assert st["staged"] == [] and st["modified"] == [] and st["untracked"] == []
    assert nd_git.current_branch(repo)  # branch is born after first commit

    # 4) edit tracked file -> modified
    _write(repo, "notes.txt", "hello world\n")
    st = nd_git.status(repo)
    assert st["clean"] is False
    assert "notes.txt" in st["modified"]


def test_unstage_moves_back_to_untracked(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "a.txt", "x\n")
    assert nd_git.stage(repo, "a.txt").ok
    assert "a.txt" in nd_git.status(repo)["staged"]
    assert nd_git.unstage(repo, "a.txt").ok
    st = nd_git.status(repo)
    assert "a.txt" not in st["staged"]
    assert "a.txt" in st["untracked"]


def test_stage_empty_paths_is_error(tmp_path):
    repo = _new_repo(tmp_path)
    res = nd_git.stage(repo, [])
    assert not res.ok and res.err


# ── commit behavior ────────────────────────────────────────────────────────
def test_commit_with_paths_arg_stages_and_commits(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "sym/lib.kicad_sym", _GOOD_SYM)
    ok, sha = nd_git.commit(repo, "add clean lib", paths="sym/lib.kicad_sym")
    assert ok, sha
    assert nd_git.status(repo)["clean"] is True


def test_commit_nothing_staged_returns_false(tmp_path):
    repo = _new_repo(tmp_path)
    ok, msg = nd_git.commit(repo, "nothing here")
    assert ok is False
    assert "nothing to commit" in msg.lower()


def test_commit_empty_message_returns_false(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "a.txt", "x\n")
    nd_git.stage(repo, "a.txt")
    ok, msg = nd_git.commit(repo, "   ")
    assert ok is False and "message" in msg.lower()


# ── the headline guard: refuse to commit a corrupt .kicad_sym ──────────────
def test_commit_refuses_conflict_markered_kicad_sym(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "libs/MySymbols.kicad_sym", _CONFLICT_SYM)
    assert nd_git.stage(repo, "libs/MySymbols.kicad_sym").ok

    # guard sees the staged corruption
    bad = nd_git.guard_no_corrupt_kicad(repo)
    assert any("MySymbols.kicad_sym" in rel for rel, _ in bad)

    # commit must refuse, nothing gets committed
    ok, msg = nd_git.commit(repo, "should be blocked")
    assert ok is False
    assert "corrupt" in msg.lower() and "conflict" in msg.lower()
    # nothing committed: the corrupt file is still sitting staged, tree not clean
    st = nd_git.status(repo)
    assert any("MySymbols.kicad_sym" in s for s in st["staged"])
    assert st["clean"] is False


def test_commit_refuses_unbalanced_paren_kicad_sch(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "board.kicad_sch", "(kicad_sch (unclosed \n")
    nd_git.stage(repo, "board.kicad_sch")
    ok, msg = nd_git.commit(repo, "blocked")
    assert ok is False
    assert "unbalanced" in msg.lower()


def test_guard_ignores_non_kicad_and_clean_files(tmp_path):
    repo = _new_repo(tmp_path)
    _write(repo, "readme.md", _CONFLICT_SYM)  # markers but not a kicad file
    _write(repo, "ok.kicad_sym", _GOOD_SYM)
    nd_git.stage(repo, ["readme.md", "ok.kicad_sym"])
    assert nd_git.guard_no_corrupt_kicad(repo) == []
    ok, sha = nd_git.commit(repo, "clean commit")
    assert ok, sha


# ── set_repo / init validation ─────────────────────────────────────────────
def test_set_repo_existing_repo(tmp_path):
    repo = _new_repo(tmp_path)
    v = nd_git.set_repo(repo)
    assert v.ok and v.is_repo and v.can_init is False
    assert v.root is not None


def test_set_repo_plain_dir_can_init(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    v = nd_git.set_repo(d)
    assert v.ok and v.is_repo is False and v.can_init is True


def test_set_repo_missing_path(tmp_path):
    v = nd_git.set_repo(tmp_path / "ghost")
    assert v.ok is False and not v.exists


def test_set_repo_file_not_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    v = nd_git.set_repo(f)
    assert v.ok is False and v.exists and v.is_dir is False
