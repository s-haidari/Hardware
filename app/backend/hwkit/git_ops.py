"""
git_ops.py — git operations for the library repo (the LibraryManager git panel):
branch/ahead-behind status, commit log, pull (ff-only), push, stage+commit, diff,
checkout. Thin wrappers over the git CLI, run in the repo working tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _run(repo: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "git not found on PATH"


def is_repo(repo: Path) -> bool:
    return (repo / ".git").exists()


def status(repo: Path) -> dict:
    if not is_repo(repo):
        return {"repo": False}
    _, branch = _run(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _, counts = _run(repo, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
    behind = ahead = 0
    parts = counts.split()
    if len(parts) == 2:
        behind, ahead = int(parts[0]), int(parts[1])
    _, dirty = _run(repo, "status", "--porcelain")
    return {"repo": True, "branch": branch.strip(), "ahead": ahead, "behind": behind,
            "dirty": bool(dirty.strip()), "changed_files": len([l for l in dirty.splitlines() if l.strip()])}


def commits(repo: Path, n: int = 40) -> list[dict]:
    if not is_repo(repo):
        return []
    fmt = "%H%x1f%h%x1f%s%x1f%an%x1f%ar"
    _, out = _run(repo, "log", f"-{n}", f"--pretty=format:{fmt}")
    rows = []
    for line in out.splitlines():
        f = line.split("\x1f")
        if len(f) == 5:
            rows.append({"hash": f[0], "short": f[1], "subject": f[2], "author": f[3], "when": f[4]})
    return rows


def diff(repo: Path, ref: str) -> str:
    _, out = _run(repo, "show", "--stat", "--patch", ref)
    return out


def pull(repo: Path) -> dict:
    code, out = _run(repo, "pull", "--ff-only")
    return {"ok": code == 0, "output": out.strip()}


def push(repo: Path) -> dict:
    code, out = _run(repo, "push")
    return {"ok": code == 0, "output": out.strip()}


def stage_commit(repo: Path, message: str) -> dict:
    _run(repo, "add", "-A")
    code, out = _run(repo, "commit", "-m", message)
    return {"ok": code == 0, "output": out.strip()}


def checkout(repo: Path, ref: str) -> dict:
    code, out = _run(repo, "checkout", ref)
    return {"ok": code == 0, "output": out.strip()}
