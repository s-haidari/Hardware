#!/usr/bin/env python3
"""
nd_git.py — thin, testable git backend for the NETDECK hardware repo.

A pure-logic wrapper around the git CLI so the app can: change the git repo,
show working-tree status, and stage/commit from within NETDECK — without pulling
in a native git dependency (no GitPython). Every git invocation goes through a
single hidden-window subprocess helper with a timeout and utf-8 decoding, and
NEVER raises on ordinary git failure: callers get a structured result carrying
git's stderr instead of an exception.

Design notes (audit §5.A "Git repository integration"):
  * All git calls use ``git -C <repo>`` + ``creationflags=CREATE_NO_WINDOW`` on
    Windows (so a pythonw.exe host never flashes a console) + a ``timeout=`` so a
    stuck credential/network prompt can't hang a worker forever.
  * Commit is guarded by a LOCAL corrupt-KiCad scanner (conflict markers /
    unbalanced parens) so the app never commits/pushes a broken *.kicad_sym /
    *.kicad_pcb / *.kicad_sch — the exact failure that shared a corrupt library
    last time. This re-implements the idea locally; it does NOT import
    LibraryManager.

Public API (what the UI layer should call):
    have_git() -> bool
    is_git_repo(path) -> bool
    repo_root(path) -> Optional[Path]
    current_branch(repo) -> Optional[str]
    status(repo) -> dict(clean, staged, modified, untracked[, error])
    stage(repo, paths) -> GitResult
    unstage(repo, paths) -> GitResult
    commit(repo, message, paths=None) -> (ok: bool, sha_or_error: str)
    init_repo(path) -> GitResult
    set_repo(path) -> RepoValidation
    guard_no_corrupt_kicad(repo, paths=None) -> list[(relpath, reason)]
    has_conflict_markers(text) -> bool
    is_paren_balanced(text) -> bool
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

__all__ = [
    "GitResult",
    "RepoValidation",
    "have_git",
    "is_git_repo",
    "repo_root",
    "current_branch",
    "status",
    "stage",
    "unstage",
    "commit",
    "init_repo",
    "set_repo",
    "guard_no_corrupt_kicad",
    "has_conflict_markers",
    "is_paren_balanced",
    "find_corrupt_kicad_files",
]

# When the GUI runs under pythonw.exe (no console), each child process would
# otherwise pop its own console window. CREATE_NO_WINDOW suppresses that flash.
# The attribute only exists on Windows; elsewhere it's 0 (no-op).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Default per-call timeout (seconds). A hung https credential prompt or a dead
# network must not block a worker forever, so every git call is bounded.
DEFAULT_TIMEOUT = 30

PathLike = Union[str, Path]


# ═══════════════════════════════════════════════════════════════════
# STRUCTURED RESULTS
# ═══════════════════════════════════════════════════════════════════
@dataclass
class GitResult:
    """Outcome of a single git invocation. Truthy iff the command succeeded, so
    callers can write ``if stage(...):`` yet still inspect ``.err`` on failure."""

    ok: bool
    code: int = 0
    out: str = ""
    err: str = ""

    def __bool__(self) -> bool:
        return self.ok

    @property
    def message(self) -> str:
        """Best human-readable line: stderr if present, else stdout."""
        return (self.err or self.out or "").strip()


@dataclass
class RepoValidation:
    """Result of validating a candidate repo location for ``set_repo``.

    ``ok`` means the path is usable as the repo root — either it is already a git
    work tree (``is_repo``) or it is an existing directory we could ``git init``
    (``can_init``)."""

    ok: bool
    path: Path
    exists: bool = False
    is_dir: bool = False
    is_repo: bool = False
    can_init: bool = False
    root: Optional[Path] = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


# ═══════════════════════════════════════════════════════════════════
# LOCAL CORRUPT-KICAD SCANNER (do NOT import LibraryManager)
# ═══════════════════════════════════════════════════════════════════
# A committed KiCad file that still holds merge-conflict markers or unbalanced
# parens is corrupt and unusable; refuse to commit one rather than push
# corruption to everyone else.
_CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})", re.MULTILINE)
_KICAD_TEXT_SUFFIXES = (".kicad_sym", ".kicad_pcb", ".kicad_sch")


def has_conflict_markers(text: str) -> bool:
    """True if ``text`` contains a git merge-conflict marker at the start of a
    line ('<<<<<<<', '=======', or '>>>>>>>')."""
    return _CONFLICT_MARKER_RE.search(text) is not None


def is_paren_balanced(text: str) -> bool:
    """True if parentheses balance across the whole text, ignoring parens inside
    quoted strings (honoring KiCad's backslash escapes). A file whose depth ever
    goes negative, or ends non-zero, is unbalanced."""
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':  # quoted string: parens inside don't count
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def _scan_kicad_text(text: str) -> Optional[str]:
    """Return a corruption reason for KiCad S-expr ``text``, or None if clean."""
    if has_conflict_markers(text):
        return "merge-conflict markers"
    if not is_paren_balanced(text):
        return "unbalanced parentheses"
    return None


def find_corrupt_kicad_files(root: PathLike) -> List[Tuple[Path, str]]:
    """Scan every *.kicad_sym/.kicad_pcb/.kicad_sch under ``root`` (skipping the
    .git dir) and return [(path, reason), ...] for each corrupt file. Working-tree
    scan; ``guard_no_corrupt_kicad`` is the staged-content commit guard."""
    bad: List[Tuple[Path, str]] = []
    base = Path(root)
    if not base.exists():
        return bad
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _KICAD_TEXT_SUFFIXES:
            continue
        if ".git" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        reason = _scan_kicad_text(text)
        if reason:
            bad.append((p, reason))
    return bad


# ═══════════════════════════════════════════════════════════════════
# LOW-LEVEL SUBPROCESS HELPER
# ═══════════════════════════════════════════════════════════════════
def have_git() -> bool:
    """True if a ``git`` executable is on PATH."""
    return shutil.which("git") is not None


def _as_list(paths: Union[PathLike, Iterable[PathLike], None]) -> List[str]:
    """Normalize a single path or an iterable of paths to a list of str."""
    if paths is None:
        return []
    if isinstance(paths, (str, Path)):
        return [str(paths)]
    return [str(p) for p in paths]


def _run_git(repo: PathLike, args: List[str], timeout: int = DEFAULT_TIMEOUT) -> GitResult:
    """Run ``git -C <repo> <args...>`` with a hidden window and a timeout.

    Never raises on ordinary git failure: a non-zero exit, a missing git binary,
    or a timeout all come back as a ``GitResult`` with ``ok=False`` and stderr
    surfaced in ``.err``."""
    cmd = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,  # never block on a git credential/stdin prompt
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=timeout,
        )
        return GitResult(
            ok=(proc.returncode == 0),
            code=proc.returncode,
            out=proc.stdout or "",
            err=proc.stderr or "",
        )
    except FileNotFoundError:
        return GitResult(ok=False, code=127, err="git executable not found on PATH")
    except subprocess.TimeoutExpired:
        return GitResult(ok=False, code=124, err=f"git timed out after {timeout}s")
    except Exception as e:  # pragma: no cover - defensive
        return GitResult(ok=False, code=1, err=str(e))


# ═══════════════════════════════════════════════════════════════════
# REPO IDENTITY / BRANCH
# ═══════════════════════════════════════════════════════════════════
def is_git_repo(path: PathLike) -> bool:
    """True if ``path`` exists and is inside a git work tree."""
    p = Path(path)
    if not p.exists():
        return False
    res = _run_git(p, ["rev-parse", "--is-inside-work-tree"])
    return res.ok and res.out.strip() == "true"


def repo_root(path: PathLike) -> Optional[Path]:
    """Absolute top-level of the work tree containing ``path``, or None if
    ``path`` isn't inside a git repo."""
    p = Path(path)
    if not p.exists():
        return None
    res = _run_git(p, ["rev-parse", "--show-toplevel"])
    top = res.out.strip()
    if res.ok and top:
        return Path(top)
    return None


def current_branch(repo: PathLike) -> Optional[str]:
    """Current branch name, or None when HEAD is detached (or on error).

    Uses ``symbolic-ref --short HEAD`` so it also works on an *unborn* branch (a
    freshly-``init``'d repo before its first commit), unlike ``rev-parse
    --abbrev-ref``."""
    res = _run_git(repo, ["symbolic-ref", "--short", "HEAD"])
    name = res.out.strip()
    if res.ok and name:
        return name
    return None


# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════
def _unquote_porcelain(path: str) -> str:
    """Strip the surrounding quotes git adds to paths with special chars.

    Status is requested with ``core.quotepath=false`` so non-ascii is not
    C-escaped; this only needs to peel the outer double quotes if present."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return path[1:-1]
    return path


def status(repo: PathLike) -> dict:
    """Working-tree status as a dict:
        {"clean": bool, "staged": [...], "modified": [...], "untracked": [...]}

    A file appears in ``staged`` if its index (X) column is set, in ``modified``
    if its work-tree (Y) column is set — a partially-staged file ("MM") lands in
    both. ``untracked`` holds "??" entries. On error an extra ``"error"`` key
    carries git's stderr and the lists come back empty/clean."""
    result = {"clean": True, "staged": [], "modified": [], "untracked": []}
    res = _run_git(
        repo,
        ["-c", "core.quotepath=false", "status", "--porcelain=v1", "--untracked-files=all"],
    )
    if not res.ok:
        result["error"] = res.message
        return result
    for line in res.out.splitlines():
        if len(line) < 3:
            continue
        x, y, rest = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            result["untracked"].append(_unquote_porcelain(rest))
            continue
        # Rename/copy entries read "R  old -> new"; report the destination path.
        disp = rest.split(" -> ", 1)[1] if " -> " in rest else rest
        disp = _unquote_porcelain(disp)
        if x not in (" ", "?"):
            result["staged"].append(disp)
        if y not in (" ", "?"):
            result["modified"].append(disp)
    result["clean"] = not (result["staged"] or result["modified"] or result["untracked"])
    return result


def _has_staged(repo: PathLike) -> bool:
    """True if there is something staged to commit (works before first commit,
    where ``diff --cached`` compares against the empty tree)."""
    res = _run_git(repo, ["diff", "--cached", "--quiet"])
    # exit 1 => differences staged; exit 0 => nothing staged.
    return res.code == 1


# ═══════════════════════════════════════════════════════════════════
# STAGE / UNSTAGE
# ═══════════════════════════════════════════════════════════════════
def stage(repo: PathLike, paths: Union[PathLike, Iterable[PathLike]]) -> GitResult:
    """Stage the given path(s) with ``git add``. Returns a GitResult (truthy on
    success, ``.err`` populated on failure)."""
    items = _as_list(paths)
    if not items:
        return GitResult(ok=False, code=1, err="no paths given to stage")
    return _run_git(repo, ["add", "--", *items])


def unstage(repo: PathLike, paths: Union[PathLike, Iterable[PathLike]]) -> GitResult:
    """Remove the given path(s) from the index, leaving the work tree untouched.

    Prefers ``git restore --staged`` (git >= 2.23); falls back to ``git reset``
    for an unborn branch where ``restore`` can't resolve HEAD."""
    items = _as_list(paths)
    if not items:
        return GitResult(ok=False, code=1, err="no paths given to unstage")
    res = _run_git(repo, ["restore", "--staged", "--", *items])
    if not res.ok:
        res = _run_git(repo, ["reset", "-q", "--", *items])
    return res


# ═══════════════════════════════════════════════════════════════════
# COMMIT (guarded)
# ═══════════════════════════════════════════════════════════════════
def guard_no_corrupt_kicad(
    repo: PathLike, paths: Union[PathLike, Iterable[PathLike], None] = None
) -> List[Tuple[str, str]]:
    """Scan the *staged* KiCad files that a commit would capture and return
    [(relpath, reason), ...] for any that carry merge-conflict markers or are
    paren-unbalanced. Empty list means safe to commit.

    ``paths`` may restrict the check to specific files; when None, every staged
    *.kicad_sym/.kicad_pcb/.kicad_sch is scanned. Content is read from the index
    (``git show :<path>``) so it reflects exactly what will be committed, not a
    later work-tree edit."""
    repo = Path(repo)
    if paths is None:
        candidates = _staged_kicad_paths(repo)
    else:
        candidates = []
        for raw in _as_list(paths):
            rel = _to_repo_relative(repo, raw)
            if rel and rel.lower().endswith(_KICAD_TEXT_SUFFIXES):
                candidates.append(rel)

    bad: List[Tuple[str, str]] = []
    for rel in candidates:
        show = _run_git(repo, ["show", f":{rel}"])
        if show.ok:
            content = show.out
        else:
            # Not staged (or path resolution mismatch): fall back to work tree.
            try:
                content = (repo / rel).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        reason = _scan_kicad_text(content)
        if reason:
            bad.append((rel, reason))
    return bad


def _staged_kicad_paths(repo: PathLike) -> List[str]:
    """Repo-relative (forward-slash) paths of staged KiCad text files."""
    res = _run_git(repo, ["diff", "--cached", "--name-only", "-z"])
    if not res.ok:
        return []
    parts = [p for p in res.out.split("\0") if p]
    return [p for p in parts if p.lower().endswith(_KICAD_TEXT_SUFFIXES)]


def _to_repo_relative(repo: Path, raw: PathLike) -> Optional[str]:
    """Best-effort convert a path to a repo-relative forward-slash string."""
    p = Path(raw)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(repo.resolve())
        except Exception:
            return None
        return str(rel).replace("\\", "/")
    return str(p).replace("\\", "/")


def commit(
    repo: PathLike,
    message: str,
    paths: Union[PathLike, Iterable[PathLike], None] = None,
) -> Tuple[bool, str]:
    """Commit staged changes (optionally staging ``paths`` first).

    Returns ``(True, <sha>)`` on success or ``(False, <error>)`` otherwise —
    including the guard refusal when a staged KiCad file is corrupt, an empty
    message, "nothing to commit", or git's own stderr (e.g. missing identity).
    Never raises on ordinary git failure."""
    if not (message or "").strip():
        return (False, "empty commit message")

    if paths is not None:
        st = stage(repo, paths)
        if not st.ok:
            return (False, st.message or "git add failed")

    # Refuse to commit corrupt KiCad content (conflict markers / unbalanced
    # parens) — never push corruption downstream.
    corrupt = guard_no_corrupt_kicad(repo)
    if corrupt:
        detail = "; ".join(f"{rel}: {reason}" for rel, reason in corrupt)
        return (False, f"commit refused: corrupt KiCad file(s): {detail}")

    if not _has_staged(repo):
        return (False, "nothing to commit (no staged changes)")

    res = _run_git(repo, ["commit", "-m", message])
    if not res.ok:
        return (False, res.message or "git commit failed")

    sha = _run_git(repo, ["rev-parse", "HEAD"]).out.strip()
    return (True, sha)


# ═══════════════════════════════════════════════════════════════════
# INIT / VALIDATE REPO LOCATION
# ═══════════════════════════════════════════════════════════════════
def init_repo(path: PathLike) -> GitResult:
    """Initialize a git repository at ``path`` (creating the directory if
    needed). Idempotent: re-running on an existing repo is a harmless no-op that
    git reports as reinitialized."""
    p = Path(path)
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return GitResult(ok=False, code=1, err=f"cannot create directory: {e}")
    return _run_git(p, ["init"])


def set_repo(path: PathLike) -> RepoValidation:
    """Validate ``path`` as a repo location for the app to switch to.

    ``ok`` is True when the path exists and is a directory that either already is
    a git work tree (``is_repo``, ``root`` = its top-level) or could be
    initialized (``can_init``). Does NOT mutate anything — call ``init_repo`` if
    ``can_init`` and the user opts in."""
    p = Path(path)
    if not p.exists():
        return RepoValidation(ok=False, path=p, reason="path does not exist")
    if not p.is_dir():
        return RepoValidation(
            ok=False, path=p, exists=True, reason="path is not a directory"
        )
    if is_git_repo(p):
        return RepoValidation(
            ok=True,
            path=p,
            exists=True,
            is_dir=True,
            is_repo=True,
            root=repo_root(p),
            reason="existing git work tree",
        )
    return RepoValidation(
        ok=True,
        path=p,
        exists=True,
        is_dir=True,
        is_repo=False,
        can_init=True,
        root=p,
        reason="directory is not a git repo (can init)",
    )
