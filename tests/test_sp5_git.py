"""SP5 — Git as a top-level feature (watchdog + fast-forward auto-pull).

Two halves:
  * pure nd_git remote helpers — ahead_behind / push / pull_ff_only — exercised
    against a local bare "origin" and clones (no network, skips without git);
  * the Git feature panel built under offscreen Qt: it exposes its handles,
    refreshes without raising, never installs a native watcher headless, and a
    watchdog-style refresh does NOT leak restyle callbacks (FIX 7 discipline).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_git  # noqa: E402

pytestmark = pytest.mark.skipif(not nd_git.have_git(), reason="git not on PATH")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Tester")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "tester@example.com")


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True)


def _bare_origin_with_clone(tmp_path):
    """A bare origin plus a clone that has one pushed commit on a tracking branch.
    Returns (origin, clone)."""
    origin = tmp_path / "origin.git"; origin.mkdir()
    _run(origin, "init", "--bare", "-b", "main")
    clone = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(clone)],
                   capture_output=True, text=True, check=True)
    (clone / "seed.txt").write_text("seed\n", encoding="utf-8")
    _run(clone, "add", "seed.txt")
    _run(clone, "commit", "-m", "seed")
    _run(clone, "push", "-u", "origin", "main")
    return origin, clone


# ── nd_git.push ───────────────────────────────────────────────────────────────
def test_push_updates_remote(tmp_path):
    origin, clone = _bare_origin_with_clone(tmp_path)
    (clone / "a.txt").write_text("a\n", encoding="utf-8")
    ok, sha = nd_git.commit(clone, "add a", paths="a.txt")
    assert ok, sha
    res = nd_git.push(clone)
    assert res.ok, res.message
    # A fresh clone of origin now carries the pushed file.
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(origin), str(fresh)],
                   capture_output=True, text=True, check=True)
    assert (fresh / "a.txt").exists()


# ── nd_git.pull_ff_only ───────────────────────────────────────────────────────
def test_pull_ff_only_fast_forwards(tmp_path):
    origin, a = _bare_origin_with_clone(tmp_path)
    b = tmp_path / "workb"
    subprocess.run(["git", "clone", str(origin), str(b)],
                   capture_output=True, text=True, check=True)
    # A pushes a new commit; B fast-forwards to it.
    (a / "new.txt").write_text("new\n", encoding="utf-8")
    _run(a, "add", "new.txt"); _run(a, "commit", "-m", "new"); _run(a, "push")
    res = nd_git.pull_ff_only(b)
    assert res.ok, res.message
    assert (b / "new.txt").exists()


def test_pull_ff_only_refuses_divergence(tmp_path):
    origin, a = _bare_origin_with_clone(tmp_path)
    b = tmp_path / "workb"
    subprocess.run(["git", "clone", str(origin), str(b)],
                   capture_output=True, text=True, check=True)
    # A advances origin one way; B commits a different local change → not a
    # fast-forward. pull_ff_only must fail and leave B's work tree untouched.
    (a / "remote.txt").write_text("r\n", encoding="utf-8")
    _run(a, "add", "remote.txt"); _run(a, "commit", "-m", "remote"); _run(a, "push")
    (b / "local.txt").write_text("l\n", encoding="utf-8")
    _run(b, "add", "local.txt"); _run(b, "commit", "-m", "local")
    res = nd_git.pull_ff_only(b)
    assert not res.ok
    assert (b / "local.txt").exists()            # local work preserved
    assert not (b / "remote.txt").exists()       # nothing merged in


# ── nd_git.ahead_behind ───────────────────────────────────────────────────────
def test_ahead_behind_none_without_upstream(tmp_path):
    repo = tmp_path / "solo"
    assert nd_git.init_repo(repo).ok
    (repo / "x.txt").write_text("x\n", encoding="utf-8")
    nd_git.commit(repo, "x", paths="x.txt")
    assert nd_git.ahead_behind(repo) is None     # no tracking branch


def test_ahead_behind_counts_local_and_remote(tmp_path):
    origin, a = _bare_origin_with_clone(tmp_path)
    assert nd_git.ahead_behind(a) == (0, 0)      # freshly pushed, in sync
    # One un-pushed local commit → ahead 1.
    (a / "ahead.txt").write_text("a\n", encoding="utf-8")
    _run(a, "add", "ahead.txt"); _run(a, "commit", "-m", "ahead")
    assert nd_git.ahead_behind(a) == (1, 0)
    # Another clone pushes a commit; after fetch, `a` is 1 ahead / 1 behind.
    b = tmp_path / "workb"
    subprocess.run(["git", "clone", str(origin), str(b)],
                   capture_output=True, text=True, check=True)
    (b / "behind.txt").write_text("b\n", encoding="utf-8")
    _run(b, "add", "behind.txt"); _run(b, "commit", "-m", "behind"); _run(b, "push")
    _run(a, "fetch")
    assert nd_git.ahead_behind(a) == (1, 1)


# ── https-remote PAT auth (nd_git._auth_config / _pat) ────────────────────────
import base64  # noqa: E402


def test_auth_config_injects_header_only_for_https():
    header = nd_git._auth_config("https://github.com/o/r.git", "ghp_secret")
    assert header[0] == "-c"
    assert header[1].startswith("http.extraheader=AUTHORIZATION: basic ")
    # The header carries base64("x-access-token:<pat>") — GitHub's PAT basic form.
    token = header[1].split("basic ", 1)[1]
    assert base64.b64decode(token).decode() == "x-access-token:ghp_secret"


def test_auth_config_skips_non_https_and_missing_pat():
    # ssh remote → never inject a header (SSH key authenticates instead).
    assert nd_git._auth_config("git@github.com:o/r.git", "ghp_secret") == []
    # local/file remote → no header.
    assert nd_git._auth_config("/tmp/origin.git", "ghp_secret") == []
    # https but no PAT configured → unchanged behavior (empty).
    assert nd_git._auth_config("https://github.com/o/r.git", None) == []
    assert nd_git._auth_config("https://github.com/o/r.git", "") == []
    assert nd_git._auth_config(None, "ghp_secret") == []


def test_pat_prefers_env_over_baked(monkeypatch):
    monkeypatch.setenv("GIT_PAT", "  env_tok  ")
    assert nd_git._pat() == "env_tok"          # trimmed
    monkeypatch.delenv("GIT_PAT", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    # With no env and (in this source checkout) GIT_PAT_DEFAULT = None → None.
    assert nd_git._pat() is None


# ── Git feature: the kit.workbench recipe (offscreen Qt) ──────────────────────
# The Git panel is now a kit.workbench sub-surface (Phase-1 convergence pilot). These
# tests assert the SAME behaviours as the pre-recipe panel — commit stages the tree,
# empty messages are rejected, the busy-gate blocks overlap, Push is ahead-aware, the
# refresh never leaks restylers — but through the new recipe structure: a `host` from
# `_git_workbench(ctx)` exposing `_run_primary` (the ▶ Commit & Sync flow), `_verdict`,
# `_region`, `_busy` (the shared gate), plus the secondary handles (`_commit`, `_push`,
# `_pull`, …) and `_btn(text)` to read a secondary button's enablement.
from PyQt5.QtWidgets import QApplication  # noqa: E402
_APP = QApplication.instance() or QApplication([])


def _fake_ctx(repo):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg={"RepoRoot": str(repo)}, services=_Svc(), theme=None,
                           bus=SimpleNamespace(emit=lambda *a, **k: None,
                                               on_owned=lambda *a, **k: None))


def _repo_with_commit(tmp_path):
    repo = tmp_path / "hw"
    assert nd_git.init_repo(repo).ok
    (repo / "readme.txt").write_text("hi\n", encoding="utf-8")
    nd_git.commit(repo, "init", paths="readme.txt")
    return repo


def _workbench(ctx):
    from ui.features import git as G
    return G._git_workbench(ctx)


def test_git_workbench_builds_and_exposes_handles(tmp_path):
    repo = _repo_with_commit(tmp_path)
    host = _workbench(_fake_ctx(repo))
    assert host._snapshot()["repo"] is not None
    assert callable(host._run_primary)                 # the ▶ Commit & Sync flow seam
    assert host._verdict is not None and host._region is not None
    assert host._msg is not None                       # commit-message chrome present
    assert host._auto_cb is not None                   # Auto-Pull toggle present
    assert not hasattr(host, "_watcher")               # headless: no native watcher
    host.grab()                                          # renders without raising


def test_git_workbench_degrades_without_repo(tmp_path):
    host = _workbench(_fake_ctx(tmp_path / "not_a_repo"))
    assert host._snapshot()["repo"] is None
    assert not host._verdict.isHidden()                # a "No Repository" band shows
    host._region.handle.refresh()                       # no-op, must not raise
    host.grab()


def test_git_workbench_refresh_does_not_leak_restylers(tmp_path):
    import ui.widgets as UW
    repo = _repo_with_commit(tmp_path)
    host = _workbench(_fake_ctx(repo))
    base = len(UW._RESTYLERS)
    # A watchdog fires the combined refresh repeatedly; the Changes card + verdict are
    # repopulated each time (static vocabulary + VerdictSlot.set) but must NOT grow the
    # global restyle registry (the B2/SHELL-06 discipline the recipe is built on).
    for _ in range(6):
        host._region.handle.refresh()
    assert len(UW._RESTYLERS) <= base, (
        f"git refresh leaked {len(UW._RESTYLERS) - base} restyle callbacks")


def test_watch_dirs_recurses_skips_git_and_caps(tmp_path):
    from ui.features import git as G
    repo = _repo_with_commit(tmp_path)
    (repo / "libs" / "My3DModels").mkdir(parents=True)
    (repo / "tools").mkdir()
    dirs = G._watch_dirs(repo)
    assert str(repo) in dirs
    assert str(repo / "libs") in dirs
    assert str(repo / "libs" / "My3DModels") in dirs   # recursion reaches deep dirs
    assert not any("/.git" in d or d.endswith("/.git") for d in dirs)  # .git excluded
    capped = G._watch_dirs(repo, cap=2)
    assert len(capped) == 2 and capped[0] == str(repo)


# ── the ▶ Commit & Sync primary flow (driven headlessly via the _run_primary seam) ──
def test_git_workbench_primary_commit_and_sync_drives_headlessly(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "change.txt").write_text("c\n", encoding="utf-8")
    host._msg.setText("a change")
    host._run_primary()                       # audit → safe keys → apply → report (headless)
    assert nd_git.status(repo).get("clean") is True
    assert any("Committed" in m for m in ctx.services.logs)


def test_git_workbench_primary_stages_the_working_tree(tmp_path):
    # ▶ Commit & Sync previews every change (staged, modified, untracked) and, on the
    # headless auto-approve, commits ALL of them — a brand-new untracked file included.
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "readme.txt").write_text("changed\n", encoding="utf-8")   # modified
    (repo / "brand_new.txt").write_text("new\n", encoding="utf-8")    # untracked
    host._msg.setText("commit everything shown")
    host._run_primary()
    assert nd_git.status(repo).get("clean") is True
    files = _run(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    assert "brand_new.txt" in files


def test_git_workbench_primary_empty_message_does_not_commit(tmp_path):
    # The ▶ flow must not commit with an empty message — the audit short-circuits with a
    # distinct "enter a commit message" empty-report and stages/commits nothing.
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "change.txt").write_text("c\n", encoding="utf-8")
    host._msg.setText("   ")
    before = _run(repo, "rev-parse", "HEAD").stdout.strip()
    host._run_primary()
    assert _run(repo, "rev-parse", "HEAD").stdout.strip() == before   # nothing committed
    assert not nd_git.status(repo).get("clean")
    assert any("message" in m.lower() for m in ctx.services.logs)


# ── verdict band reflects repo state ──────────────────────────────────────────
def test_git_workbench_verdict_reflects_state(tmp_path):
    repo = _repo_with_commit(tmp_path)
    host = _workbench(_fake_ctx(repo))
    assert host._verdict._title.text() == "Clean"          # fresh commit → clean tree
    (repo / "c.txt").write_text("c\n", encoding="utf-8")
    host._region.handle.refresh()
    assert "Changed" in host._verdict._title.text()        # a dirty tree → N Changed


# ── the secondary grid (Commit / Push / Pull / reports / stage-file / repo mgmt) ──
def test_git_workbench_secondary_commit_reports_and_clears_message(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "change.txt").write_text("c\n", encoding="utf-8")
    host._msg.setText("a change")
    host._commit(False)                          # commit, no push (no remote)
    assert nd_git.status(repo).get("clean") is True
    assert any("Committed" in m for m in ctx.services.logs)
    assert host._msg.text() == ""                # cleared on a successful commit


def test_git_workbench_secondary_empty_message_rejected(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "change.txt").write_text("c\n", encoding="utf-8")
    host._msg.setText("   ")
    before = _run(repo, "rev-parse", "HEAD").stdout.strip()
    host._commit(False)
    assert _run(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert not nd_git.status(repo).get("clean")
    assert any("Enter a commit message" in m for m in ctx.services.logs)


def test_git_workbench_busy_blocks_overlapping_jobs(tmp_path):
    # While a job is in flight (busy gate set), a second Commit / Pull / Push must be a
    # no-op so two git workers never race on one work tree. The done-callback is deferred
    # so the workbench stays mid-job.
    repo = _repo_with_commit(tmp_path)
    pending = []

    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            pending.append(done_cb)

    ctx = SimpleNamespace(cfg={"RepoRoot": str(repo)}, services=_Svc(), theme=None,
                          bus=SimpleNamespace(emit=lambda *a, **k: None,
                                              on_owned=lambda *a, **k: None))
    import ui.util as UU
    _orig_headless = UU._headless
    UU._headless = lambda: False                 # force the deferred (threaded) path
    try:
        host = _workbench(ctx)
        pending.clear()                           # drop the build-time deferred verdict populate
        (repo / "change.txt").write_text("c\n", encoding="utf-8")
        host._msg.setText("first")
        host._commit(False)                       # job ran; done-cb deferred → still busy
        assert host._busy["on"] is True
        assert host._btn("Commit").isEnabled() is False   # busy → actions disabled
        assert host._msg.isEnabled() is False
        n_pending = len(pending)
        host._commit(False); host._pull(); host._push()   # all blocked
        assert len(pending) == n_pending, "an overlapping git job was scheduled while busy"
        pending[0](True)                          # complete → un-busy + re-enable
        assert host._busy["on"] is False
        assert host._btn("Commit").isEnabled() is True
    finally:
        UU._headless = _orig_headless


def test_git_workbench_push_button_gated_on_ahead(tmp_path):
    origin, clone = _bare_origin_with_clone(tmp_path)
    ctx = _fake_ctx(clone)
    host = _workbench(ctx)
    assert host._btn("Push").isEnabled() is False             # in sync → nothing to push
    (clone / "ahead.txt").write_text("a\n", encoding="utf-8")
    host._msg.setText("local commit")
    host._commit(False)                                        # commit only, no push
    assert nd_git.ahead_behind(clone) == (1, 0)
    assert host._btn("Push").isEnabled() is True               # ahead>0 → Push enabled


def test_git_workbench_standalone_push_reaches_remote(tmp_path):
    origin, clone = _bare_origin_with_clone(tmp_path)
    ctx = _fake_ctx(clone)
    host = _workbench(ctx)
    (clone / "x.txt").write_text("x\n", encoding="utf-8")
    nd_git.commit(clone, "x", paths="x.txt")                   # committed, un-pushed
    host._region.handle.refresh()
    assert host._btn("Push").isEnabled() is True
    host._push()
    assert any("Pushed" in m for m in ctx.services.logs)
    fresh = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(origin), str(fresh)],
                   capture_output=True, text=True, check=True)
    assert (fresh / "x.txt").exists()


# ── the reports + repo-management machinery (the newly-surfaced parity capabilities) ──
def test_git_workbench_integrity_scan_reports(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    host._integrity_scan()                                     # clean tree → a report line
    assert any("clean" in m.lower() or "corrupt" in m.lower() for m in ctx.services.logs)


def test_git_workbench_recent_commits_reports(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    host._recent_commits()
    assert any("commit" in m.lower() for m in ctx.services.logs)


def test_git_workbench_show_file_at_head_reports(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    host._show_file("readme.txt")                              # explicit path seam (no dialog)
    assert any("readme.txt" in m for m in ctx.services.logs)


def test_git_workbench_stage_and_unstage_file(tmp_path):
    repo = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    (repo / "s.txt").write_text("s\n", encoding="utf-8")
    host._stage_file(str(repo / "s.txt"))                      # explicit path seam
    assert "s.txt" in nd_git.status(repo).get("staged", [])
    host._unstage_file(str(repo / "s.txt"))
    assert "s.txt" not in nd_git.status(repo).get("staged", [])


# ── review-confirmed defect locks (adversarial review wf_0c7cb118) ────────────
def test_git_workbench_push_enabled_without_upstream(tmp_path):
    # No upstream ⇒ ahead_behind() is None. Push must stay ENABLED so the user can
    # invoke it and receive git's actionable "no upstream" error — a permanently
    # greyed button with no explanation was the review-confirmed dead-end (bare
    # keeps Push always-on and surfaces the real message).
    repo = _repo_with_commit(tmp_path)                 # init'd, no remote/upstream
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    assert nd_git.ahead_behind(repo) is None
    assert host._btn("Push").isEnabled() is True
    host._push()
    assert any("Push failed" in m for m in ctx.services.logs)    # actionable, not silent


def test_git_workbench_verdict_reports_missing_git(tmp_path, monkeypatch):
    # With git absent, repo_root() ALSO returns None (it shells out to git) — so on a
    # git-less machine `repo is None` is always true, and the have_git() check must come
    # FIRST or the band misdiagnoses as "No Repository" and misdirects the user to
    # Set Up/Initialize (the review-confirmed unreachable-branch bug). Reproduce the
    # true scenario: repo unresolvable AND have_git() False.
    ctx = _fake_ctx(tmp_path / "not_a_repo")
    host = _workbench(ctx)
    monkeypatch.setattr(nd_git, "have_git", lambda: False)
    host._refresh()
    assert host._verdict._title.text() == "Git Not Installed"


def test_git_workbench_pull_surfaces_gits_real_reason(tmp_path):
    # A failed pull must report git's actual message (e.g. "no tracking information"),
    # not a hardcoded not-a-fast-forward guess — the review-confirmed misdiagnosis.
    repo = _repo_with_commit(tmp_path)                 # no upstream
    ctx = _fake_ctx(repo)
    host = _workbench(ctx)
    host._pull()
    lines = [m for m in ctx.services.logs if m.startswith("Pull skipped:")]
    assert lines, "a failed pull must log a 'Pull skipped:' line"
    assert "tracking" in lines[-1].lower(), \
        f"the real git reason (no tracking information) must surface, got: {lines[-1]!r}"


def test_git_workbench_watchdog_repointed_on_adopt(tmp_path, monkeypatch):
    # The live watchdog must follow the ACTIVE repo: adopting repo B while watching
    # repo A re-points the watcher (review-confirmed: it used to keep watching A
    # forever — the headline live-refresh silently dead for the new repo).
    import LibraryManager as LM
    from ui.features import git as G
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(G, "_headless", lambda: False)   # allow the watcher offscreen
    repo_a = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(repo_a)
    host = _workbench(ctx)
    dirs = {str(Path(d).resolve()) for d in host._watcher.directories()}
    assert str(repo_a.resolve()) in dirs
    repo_b = tmp_path / "b"
    assert nd_git.init_repo(repo_b).ok
    host._set_up_repo(str(repo_b))
    dirs = {str(Path(d).resolve()) for d in host._watcher.directories()}
    assert str(repo_b.resolve()) in dirs                 # now watching the new repo
    assert str(repo_a.resolve()) not in dirs             # …and not the old one


def test_git_workbench_watchdog_installed_after_adopt_from_no_repo(tmp_path, monkeypatch):
    # Starting repo-less installs NO watcher; adopting a repo via Set Up must install
    # one (review-confirmed: it never was, so the live refresh stayed dead all session).
    import LibraryManager as LM
    from ui.features import git as G
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(G, "_headless", lambda: False)
    other = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(tmp_path / "nope")
    host = _workbench(ctx)
    assert getattr(host, "_watcher", None) is None       # no repo → no watcher yet
    host._set_up_repo(str(other))
    assert host._watcher is not None
    dirs = {str(Path(d).resolve()) for d in host._watcher.directories()}
    assert str(other.resolve()) in dirs


def test_git_workbench_seams_rebind_after_adopt(tmp_path, monkeypatch):
    # host._msg / host._auto_cb must track the LIVE chrome across a repo-adoption
    # region rebuild (review-confirmed: they pointed at the destroyed first-build
    # widgets, so an adopt-then-drive would poke a deleted QLineEdit).
    import LibraryManager as LM
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    other = _repo_with_commit(tmp_path)
    ctx = _fake_ctx(tmp_path / "nope")
    host = _workbench(ctx)
    assert host._msg is None                             # no-repo chrome: no field
    host._set_up_repo(str(other))
    for _ in range(4):
        _APP.processEvents()                             # the deferred rebuild lands
    assert host._msg is not None
    host._msg.setText("live")                            # a LIVE widget, not a deleted one
    assert host._snapshot()["msg"] == "live"
    assert host._auto_cb is not None


def test_git_workbench_set_up_repository_adopts(tmp_path, monkeypatch):
    import LibraryManager as LM
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    other = tmp_path / "other"
    assert nd_git.init_repo(other).ok
    (other / "a.txt").write_text("a\n", encoding="utf-8")
    nd_git.commit(other, "a", paths="a.txt")
    ctx = _fake_ctx(tmp_path / "nope")                         # start with no repo
    host = _workbench(ctx)
    host._set_up_repo(str(other))                              # explicit path seam
    assert Path(ctx.cfg["RepoRoot"]).resolve() == other.resolve()


def test_git_workbench_initialize_repository_adopts(tmp_path, monkeypatch):
    import LibraryManager as LM
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    fresh = tmp_path / "fresh"; fresh.mkdir()
    ctx = _fake_ctx(tmp_path / "nope")
    host = _workbench(ctx)
    host._init_repo(str(fresh))                                # explicit path seam
    assert nd_git.is_git_repo(fresh)
    assert Path(ctx.cfg["RepoRoot"]).resolve() == fresh.resolve()
