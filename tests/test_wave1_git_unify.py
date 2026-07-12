"""Wave 1 · WS-E / GIT-03 — LibraryManager's git layer unified onto nd_git.

The old ``LibraryManager`` git wrappers (``git_push``/``git_pull``/
``git_stage_commit``/…) shelled out through a local ``run_git`` that had **no PAT
auth**, so every Library drop-in auto-push went through an *unauthenticated* path
that fails against the https remote. GIT-03 reimplements those wrappers to
delegate to ``nd_git`` — the PAT-authenticated, corruption-guarded, timeout-
bounded backend the Git tab already uses — behind their unchanged
``(cfg, log, …) -> bool`` signatures so the 9 call sites don't change.

These tests pin the new behavior:
  * the corruption scanners are the *same* objects re-exported from nd_git
    (one scanner, not two);
  * ``git_stage_commit`` still guards corruption and commits a clean change;
  * ``git_push`` / ``git_pull`` delegate to nd_git (so https pushes now carry the
    PAT header) and return nd_git's ok bool;
  * a real push through ``git_commit_push`` reaches a ``file://`` bare remote;
  * the fix itself: a push against an https remote with a PAT configured now
    injects the Authorization header (the old path never could).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import base64
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_git  # noqa: E402
import LibraryManager as LM  # noqa: E402

pytestmark = pytest.mark.skipif(not nd_git.have_git(), reason="git not on PATH")


class _Log:
    """Records UILog lines; ``.text`` joins them for substring assertions."""
    def __init__(self):
        self.lines = []

    def write(self, msg):
        self.lines.append(str(msg))

    @property
    def text(self):
        return "\n".join(self.lines)


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Tester")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "tester@example.com")
    # No stray PAT from the environment unless a test sets one.
    monkeypatch.delenv("GIT_PAT", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)


def _repo(tmp_path, name="hw"):
    """A git repo on branch main with one commit."""
    repo = tmp_path / name
    assert nd_git.init_repo(repo).ok
    _run(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    nd_git.commit(repo, "seed", paths="seed.txt")
    return repo


def _bare_origin_with_clone(tmp_path):
    origin = tmp_path / "origin.git"; origin.mkdir()
    _run(origin, "init", "--bare", "-b", "main")
    clone = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(clone)],
                   capture_output=True, text=True, check=True)
    (clone / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run(clone, "add", "seed.txt"); _run(clone, "commit", "-m", "seed")
    _run(clone, "push", "-u", "origin", "main")
    return origin, clone


# ── one corruption scanner (re-exported from nd_git) ──────────────────────────
def test_scanners_are_nd_git_reexports():
    # LM must not carry its own duplicate scanners any more; the names it exposes
    # are literally nd_git's functions.
    assert LM.find_corrupt_kicad_files is nd_git.find_corrupt_kicad_files
    assert LM.has_conflict_markers is nd_git.has_conflict_markers
    assert LM.is_paren_balanced is nd_git.is_paren_balanced


# ── nd_git.stage_all ──────────────────────────────────────────────────────────
def test_stage_all_stages_new_and_deleted(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new.txt").write_text("n\n", encoding="utf-8")
    (repo / "seed.txt").unlink()               # a deletion must be staged too
    assert nd_git.stage_all(repo).ok
    st = nd_git.status(repo)
    assert "new.txt" in st["staged"]
    assert "seed.txt" in st["staged"]          # add -A captures the removal


# ── nd_git.restore_worktree (backs the Library editor's Discard) ─────────────────
def test_restore_worktree_reverts_uncommitted_edits(tmp_path):
    repo = _repo(tmp_path)
    (repo / "seed.txt").write_text("edited but never committed\n", encoding="utf-8")
    assert (repo / "seed.txt").read_text(encoding="utf-8").startswith("edited")
    r = nd_git.restore_worktree(repo)          # git checkout -- .
    assert r.ok
    # the tracked file is back to its committed content, silently (local op)
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "seed\n"
    assert nd_git.status(repo)["clean"] is True


def test_git_discard_uncommitted_wrapper_restores(tmp_path):
    # The LibraryManager wrapper the UI calls (LM.git_discard_uncommitted) reverts
    # the library work tree via nd_git.restore_worktree.
    repo = _repo(tmp_path)
    (repo / "seed.txt").write_text("dirty\n", encoding="utf-8")
    cfg = {"RepoRoot": str(repo)}

    class _Log:
        def write(self, *_a, **_k): pass
    assert LM.git_discard_uncommitted(cfg, _Log()) is True
    assert (repo / "seed.txt").read_text(encoding="utf-8") == "seed\n"


# ── git_stage_commit (delegates, keeps the corruption guard + logging) ────────
def test_git_stage_commit_commits_clean_change(tmp_path):
    repo = _repo(tmp_path)
    (repo / "part.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    log = _Log()
    assert LM.git_stage_commit({"RepoRoot": str(repo)}, log, message="add a part") is True
    assert nd_git.status(repo)["clean"] is True          # everything committed
    assert _run(repo, "log", "-1", "--pretty=%s").stdout.strip() == "add a part"


def test_git_stage_commit_refuses_corrupt(tmp_path):
    repo = _repo(tmp_path)
    (repo / "libs").mkdir()
    (repo / "libs" / "MySymbols.kicad_sym").write_text(
        '(kicad_symbol_lib\n=======\n)\n', encoding="utf-8")
    log = _Log()
    assert LM.git_stage_commit({"RepoRoot": str(repo)}, log, message="x") is False
    assert "ABORTED" in log.text and "MySymbols.kicad_sym" in log.text
    # Nothing was committed — the corrupt file never entered a commit.
    assert _run(repo, "log", "-1", "--pretty=%s").stdout.strip() == "seed"


def test_git_stage_commit_nothing_to_commit(tmp_path):
    repo = _repo(tmp_path)                                 # clean tree, no changes
    log = _Log()
    assert LM.git_stage_commit({"RepoRoot": str(repo)}, log) is False
    assert "othing to commit" in log.text


# ── git_push / git_pull delegate to nd_git ────────────────────────────────────
def test_git_push_delegates_to_nd_git(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = {}

    def fake_push(r):
        seen["repo"] = r
        return nd_git.GitResult(ok=True, out="ok")

    monkeypatch.setattr(nd_git, "push", fake_push)
    log = _Log()
    assert LM.git_push({"RepoRoot": str(repo)}, log) is True
    assert seen["repo"] == str(repo)                       # routed through nd_git.push


def test_git_pull_delegates_to_nd_git(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    seen = {}

    def fake_pull(r):
        seen["repo"] = r
        return nd_git.GitResult(ok=True, out="Already up to date.")

    monkeypatch.setattr(nd_git, "pull_ff_only", fake_pull)
    log = _Log()
    assert LM.git_pull({"RepoRoot": str(repo)}, log) is True
    assert seen["repo"] == str(repo)


def test_git_push_surfaces_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(nd_git, "push",
                        lambda r: nd_git.GitResult(ok=False, code=1, err="rejected"))
    log = _Log()
    assert LM.git_push({"RepoRoot": str(repo)}, log) is False
    assert "rejected" in log.text


# ── end-to-end through a file:// bare remote (no network) ─────────────────────
def test_git_commit_push_reaches_bare_remote(tmp_path):
    _origin, clone = _bare_origin_with_clone(tmp_path)
    (clone / "asset.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    log = _Log()
    assert LM.git_commit_push({"RepoRoot": str(clone)}, log,
                              "Library: drop in symbol asset.kicad_sym") is True
    # A fresh clone of origin now carries the pushed asset.
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(_origin), str(fresh)],
                   capture_output=True, text=True, check=True)
    assert (fresh / "asset.kicad_sym").exists()


# ── LIB-13: ff pull-before-push so multi-user drop-ins don't reject ───────────
def test_git_commit_push_ff_pulls_then_pushes(tmp_path):
    """Up-to-date remote: commit, ff-pull (no-op), push — the happy path."""
    origin, clone = _bare_origin_with_clone(tmp_path)
    (clone / "a.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    log = _Log()
    assert LM.git_commit_push({"RepoRoot": str(clone)}, log, "feat(lib): add a") is True
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(origin), str(fresh)],
                   capture_output=True, text=True, check=True)
    assert (fresh / "a.kicad_sym").exists()


def test_git_commit_push_surfaces_divergence_without_clobber(tmp_path):
    """A collaborator advanced the remote; our local commit can't fast-forward.
    git_commit_push must surface it, NOT push, and preserve the local commit."""
    origin, b = _bare_origin_with_clone(tmp_path)
    # Another clone 'a' pushes a commit → origin advances beyond what b has.
    a = tmp_path / "worka"
    subprocess.run(["git", "clone", str(origin), str(a)],
                   capture_output=True, text=True, check=True)
    (a / "remote.txt").write_text("r\n", encoding="utf-8")
    _run(a, "add", "remote.txt"); _run(a, "commit", "-m", "remote"); _run(a, "push")
    # b makes its own drop-in and commits+pushes → diverged, can't ff.
    (b / "mine.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    log = _Log()
    assert LM.git_commit_push({"RepoRoot": str(b)}, log, "feat(lib): add mine") is False
    # Local commit preserved…
    assert _run(b, "log", "-1", "--pretty=%s").stdout.strip() == "feat(lib): add mine"
    # …and NOT pushed: a fresh clone of origin lacks our file.
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(origin), str(fresh)],
                   capture_output=True, text=True, check=True)
    assert not (fresh / "mine.kicad_sym").exists()
    assert "NOT pushed" in log.text or "not pushed" in log.text


def test_git_commit_push_no_remote_still_commits(tmp_path):
    """A local repo with no remote still commits (best-effort push just fails);
    the no-upstream pull failure must NOT be mistaken for divergence."""
    repo = _repo(tmp_path)
    (repo / "a.kicad_sym").write_text('(kicad_symbol_lib (version 20211014))\n', encoding="utf-8")
    log = _Log()
    assert LM.git_commit_push({"RepoRoot": str(repo)}, log, "feat(lib): add a") is True
    assert _run(repo, "log", "-1", "--pretty=%s").stdout.strip() == "feat(lib): add a"


# ── the actual fix: an https push now carries the PAT auth header ─────────────
def test_git_push_injects_pat_header_for_https(tmp_path, monkeypatch):
    """The bug WS-E fixes: the old LM.git_push had no auth, so a credential-less
    https clone could never push. Now it delegates to nd_git, which injects the
    PAT Authorization header for https remotes."""
    repo = _repo(tmp_path)
    _run(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    monkeypatch.setenv("GIT_PAT", "ghp_secret")
    real = nd_git._run_git
    recorded = []

    def spy(r, args, timeout=nd_git.DEFAULT_TIMEOUT):
        if "push" in args:                       # intercept only the network op
            recorded.append(args)
            return nd_git.GitResult(ok=True, out="pushed")
        return real(r, args, timeout=timeout)     # let remote/branch resolution run for real

    monkeypatch.setattr(nd_git, "_run_git", spy)
    log = _Log()
    assert LM.git_push({"RepoRoot": str(repo)}, log) is True
    assert recorded, "nd_git.push never issued a push"
    argv = recorded[0]
    assert "-c" in argv
    header = next(a for a in argv if a.startswith("http.extraheader="))
    token = header.split("basic ", 1)[1]
    assert base64.b64decode(token).decode() == "x-access-token:ghp_secret"
