"""ui_shell.py — the plumbing contract between the main window and its tabs.

The window injects a TabContext into every tab so all three share ONE log pane
(and ui_python.log), ONE async runner (work off the GUI thread, status bar +
activity dot driven for free), and ONE progress surface. Tabs must treat every
callable here as thread-safe to invoke from worker threads."""
from __future__ import annotations


class TabContext:
    """Shell services for a tab. All callables are safe from worker threads.

    log(msg)                      -> append to the shared Log pane + logfile
    run_async(fn, busy, ok, done_cb=None) -> run fn off the GUI thread; busy/idle
                                   status handled by the shell; done_cb(ok) runs
                                   on the GUI thread afterwards
    set_progress(done, total, name) -> determinate progress in the status bar
    """

    def __init__(self, log=None, run_async=None, set_progress=None):
        self.log = log or (lambda msg: None)
        self.run_async = run_async
        self.set_progress = set_progress or (lambda done, total, name: None)
