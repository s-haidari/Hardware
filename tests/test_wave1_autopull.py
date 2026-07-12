"""Wave 1 · GIT-02 — app-level background auto-pull (ui.autopull + shell wiring).

Two halves:
  * the AutoPullService state machine, driven with a fake timer + synchronous
    runner (no event loop, no threads, no git);
  * the shell as the single owner/writer of the persisted "AutoPull" preference:
    it seeds the service at launch and the "autopull.set_enabled" bus command
    flips it, persists it, and broadcasts "autopull.changed".
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402
from ui.autopull import AutoPullService, AUTO_PULL_MS  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self): self._cbs = []
    def connect(self, cb): self._cbs.append(cb)
    def fire(self):
        for cb in list(self._cbs):
            cb()


class _FakeTimer:
    """A QTimer stand-in: records interval + running state; ``timeout.fire()``
    simulates a tick."""
    def __init__(self):
        self.interval = None
        self.running = False
        self.timeout = _Signal()
    def setInterval(self, ms): self.interval = ms
    def start(self): self.running = True
    def stop(self): self.running = False


def _sync_runner(fn): fn()          # run the "background" job inline, deterministically


# ── AutoPullService state machine ─────────────────────────────────────────────
def test_sets_interval_and_stays_stopped_when_disabled():
    t = _FakeTimer()
    svc = AutoPullService("/repo", enabled=False, timer=t, runner=_sync_runner)
    assert t.interval == AUTO_PULL_MS
    assert svc.enabled is False
    assert t.running is False


def test_enabled_starts_the_timer():
    t = _FakeTimer()
    svc = AutoPullService("/repo", enabled=True, timer=t, runner=_sync_runner)
    assert svc.enabled is True
    assert t.running is True


def test_set_enabled_toggles_the_timer():
    t = _FakeTimer()
    svc = AutoPullService("/repo", enabled=False, timer=t, runner=_sync_runner)
    svc.set_enabled(True); assert t.running is True and svc.enabled is True
    svc.set_enabled(False); assert t.running is False and svc.enabled is False


def test_tick_runs_a_fast_forward_pull_with_the_repo():
    t = _FakeTimer()
    pulls = []
    svc = AutoPullService("/repo", enabled=True, timer=t, runner=_sync_runner,
                          pull=lambda repo: pulls.append(repo), is_repo=lambda r: True)
    t.timeout.fire()
    assert pulls == ["/repo"]


def test_tick_is_noop_when_disabled():
    t = _FakeTimer()
    pulls = []
    svc = AutoPullService("/repo", enabled=False, timer=t, runner=_sync_runner,
                          pull=lambda repo: pulls.append(repo))
    t.timeout.fire()                 # a stray fire after a disable must do nothing
    assert pulls == []


def test_no_repo_never_starts():
    t = _FakeTimer()
    svc = AutoPullService(None, enabled=True, timer=t, runner=_sync_runner)
    assert svc.enabled is True       # preference tracked…
    assert t.running is False        # …but nothing to pull, so no timer


def test_headless_timer_none_tracks_state_without_crashing():
    svc = AutoPullService("/repo", enabled=True, timer=None, runner=_sync_runner)
    assert svc.enabled is True
    svc.set_enabled(False)
    assert svc.enabled is False


def test_on_result_receives_pull_outcome():
    t = _FakeTimer()
    seen = []
    svc = AutoPullService("/repo", enabled=True, timer=t, runner=_sync_runner,
                          pull=lambda repo: "RESULT", on_result=seen.append,
                          is_repo=lambda r: True)
    t.timeout.fire()
    assert seen == ["RESULT"]


def test_tick_skips_pull_when_repo_is_not_a_git_work_tree():
    # A RepoRoot that isn't a git work tree (e.g. a freshly seeded, non-repo
    # library folder) must NOT spawn a doomed `git pull` every cadence.
    t = _FakeTimer()
    pulls = []
    svc = AutoPullService("/seeded-lib", enabled=True, timer=t, runner=_sync_runner,
                          pull=lambda repo: pulls.append(repo), is_repo=lambda r: False)
    assert t.running is True          # timer still armed (state is honestly tracked)
    t.timeout.fire()                  # …but the tick short-circuits before spawning git
    assert pulls == []


def test_tick_uses_nd_git_is_git_repo_by_default(tmp_path):
    # Default is_repo is nd_git.is_git_repo: a real temp dir that is NOT a repo
    # must be skipped without the caller having to inject anything.
    import nd_git
    if not nd_git.have_git():
        pytest.skip("git not on PATH")
    t = _FakeTimer()
    pulls = []
    (tmp_path / "lib").mkdir()
    svc = AutoPullService(str(tmp_path / "lib"), enabled=True, timer=t,
                          runner=_sync_runner, pull=lambda repo: pulls.append(repo))
    t.timeout.fire()
    assert pulls == []                # not a git repo → no pull spawned


# ── Shell wiring (offscreen → headless: timer is None, state still tracked) ────
def _shell(monkeypatch, *, autopull=False):
    import LibraryManager as LM
    written = {}
    monkeypatch.setattr(
        LM, "read_setting",
        lambda key, default=None, config_path=None: (
            autopull if key == "AutoPull" else ("Dark" if key == "Theme" else default)))
    monkeypatch.setattr(
        LM, "write_setting",
        lambda key, value, config_path=None: written.__setitem__(key, value) or True)
    from ui.shell import NetdeckShell
    return NetdeckShell(LM.load_config()), written


def test_shell_seeds_autopull_from_persisted_value(monkeypatch):
    win, _ = _shell(monkeypatch, autopull=True)
    assert win._autopull.enabled is True
    win.close()


def test_shell_autopull_defaults_off(monkeypatch):
    win, _ = _shell(monkeypatch, autopull=False)
    assert win._autopull.enabled is False
    win.close()


def test_shell_autopull_result_logs_only_on_change_or_failure(monkeypatch):
    """The background pull is no longer silent: a diverged/blocked pull and a pull
    that actually moved the branch each leave a status-bar line, while the common
    'Already up to date.' no-op stays quiet (no per-tick noise)."""
    import nd_git
    win, _ = _shell(monkeypatch, autopull=False)
    logged = []
    monkeypatch.setattr(win, "_log", logged.append)

    # a clean no-op: silent
    win._on_autopull_result(nd_git.GitResult(ok=True, out="Already up to date.\n"))
    assert logged == []

    # a real fast-forward: one concise line
    win._on_autopull_result(nd_git.GitResult(ok=True, out="Updating 1a2b3c..4d5e6f\n Fast-forward\n"))
    assert len(logged) == 1 and "pulled" in logged[0].lower()

    # a failure (diverged branch): surfaces git's first stderr line
    logged.clear()
    win._on_autopull_result(nd_git.GitResult(
        ok=False, err="fatal: Not possible to fast-forward, aborting.\n"))
    assert len(logged) == 1
    assert "blocked" in logged[0].lower() and "fast-forward" in logged[0].lower()
    win.close()


def test_shell_wires_on_result_into_the_service(monkeypatch):
    """The shell feeds the AutoPullService an on_result callback (so a tick's result
    reaches the GUI thread) — earlier it passed none and dropped every GitResult."""
    win, _ = _shell(monkeypatch, autopull=False)
    assert win._autopull._on_result is not None
    win.close()


def test_shell_autopull_bus_sets_persists_and_broadcasts(monkeypatch):
    win, written = _shell(monkeypatch, autopull=False)
    heard = []
    win.ctx.bus.on("autopull.changed", lambda on: heard.append(on))
    win.ctx.bus.emit("autopull.set_enabled", True)
    assert win._autopull.enabled is True         # service flipped on
    assert written.get("AutoPull") is True        # persisted (single writer)
    assert heard == [True]                         # broadcast for any live control
    win.close()


# ── Git-tab checkbox drives (and seeds from) the app-level preference ──────────
def test_git_panel_autopull_checkbox_seeds_and_commands(tmp_path, monkeypatch):
    # The Auto-Pull toggle now lives in the kit.workbench detail chrome (host._auto_cb);
    # it still seeds from the persisted "AutoPull" pref and commands the shell-owned
    # service on toggle — the same behaviour, through the recipe.
    import nd_git
    if not nd_git.have_git():
        pytest.skip("git not on PATH")
    import LibraryManager as LM
    from types import SimpleNamespace
    from ui.features import git as G

    # Persisted AutoPull = True → the checkbox seeds checked.
    monkeypatch.setattr(LM, "read_setting",
                        lambda key, default=None, config_path=None: True if key == "AutoPull" else default)
    repo = tmp_path / "hw"
    assert nd_git.init_repo(repo).ok
    (repo / "r.txt").write_text("x\n", encoding="utf-8")
    nd_git.commit(repo, "init", paths="r.txt")

    emits = []

    class _Svc:
        def log(self, *a, **k): pass
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    ctx = SimpleNamespace(cfg={"RepoRoot": str(repo)}, services=_Svc(), theme=None,
                          bus=SimpleNamespace(emit=lambda *a, **k: emits.append(a),
                                              on_owned=lambda *a, **k: None))
    host = G._git_workbench(ctx)
    assert host._auto_cb.isChecked() is True            # seeded from the persisted value
    host._auto_cb.setChecked(False)                      # a user un-check…
    assert ("autopull.set_enabled", False) in emits      # …commands the app service
