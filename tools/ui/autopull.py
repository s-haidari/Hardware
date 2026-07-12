"""ui.autopull — app-level background fast-forward auto-pull (Wave 1 · GIT-02).

The Git tab used to own the background auto-pull: a QTimer that fired
``nd_git.pull_ff_only`` every few minutes — but ONLY while the Git tab was open,
and its on/off state didn't persist. GIT-02 lifts that to an app-level service the
shell owns, so the local copy tracks collaborators regardless of which workspace
is showing, and the preference survives a relaunch.

The service is deliberately framework-light and injectable so it unit-tests
without a real event loop:
  * ``timer``  — any object with ``setInterval`` / ``start`` / ``stop`` and a
    ``timeout`` signal (a ``QTimer`` in the app; ``None`` in a headless context,
    where no timer is ever started but the enabled *state* is still tracked);
  * ``pull``   — the blocking fast-forward-only pull (defaults to
    ``nd_git.pull_ff_only``); it NEVER merges or rewrites local work;
  * ``runner`` — how the blocking pull is run off the caller's thread (a daemon
    thread in the app so a slow network never stalls the GUI; a synchronous call
    in tests).
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import nd_git

# Background auto-pull cadence (ms). Modest so a collaborator's push shows up
# within a few minutes without hammering the remote.
AUTO_PULL_MS = 180_000


def _thread_runner(fn: Callable[[], None]) -> None:
    """Run ``fn`` on a daemon thread so a slow ``git pull`` never blocks the GUI."""
    threading.Thread(target=fn, daemon=True).start()


class AutoPullService:
    """Owns the app-level background fast-forward auto-pull loop and its enabled
    state. Enabling starts the timer; each tick runs a fast-forward-only pull off
    the GUI thread. Disabling (or a missing repo / missing timer) stops it. The
    enabled *state* is tracked even when there is no timer (headless), so the
    shell can persist and report it uniformly."""

    def __init__(
        self,
        repo: Optional[str],
        *,
        enabled: bool = False,
        timer=None,
        pull: Optional[Callable] = None,
        runner: Optional[Callable[[Callable[[], None]], None]] = None,
        on_result: Optional[Callable] = None,
        is_repo: Optional[Callable[[str], bool]] = None,
    ):
        self._repo = str(repo) if repo else None
        self._timer = timer
        self._pull = pull or nd_git.pull_ff_only
        self._runner = runner or _thread_runner
        self._on_result = on_result
        self._is_repo = is_repo or nd_git.is_git_repo
        self._enabled = False
        if timer is not None:
            timer.setInterval(AUTO_PULL_MS)
            timer.timeout.connect(self._tick)
        self.set_enabled(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        """Turn the background loop on or off. A no-op on the timer when there is
        no timer (headless) or no repo — but the enabled flag still updates so the
        preference is reported truthfully."""
        self._enabled = bool(on)
        if self._timer is None:
            return
        if self._enabled and self._repo:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self) -> None:
        """One timer fire: run a fast-forward-only pull off the GUI thread. Guarded
        so a fire that races a disable (or a repo-less service) does nothing, and
        so a RepoRoot that isn't a git work tree (e.g. a freshly seeded, non-repo
        library folder) never spawns a doomed ``git pull`` every cadence."""
        if not self._enabled or not self._repo:
            return
        if not self._is_repo(self._repo):
            return
        repo, pull, on_result = self._repo, self._pull, self._on_result

        def job():
            res = pull(repo)
            if on_result:
                on_result(res)

        self._runner(job)
