"""ui.util — small helpers shared by feature modules."""
from __future__ import annotations


class LogSink:
    """A UILog-compatible sink (`.write`) that forwards to the shell status line.
    The pure LibraryManager / nd_* helpers accept any object with `.write`."""

    def __init__(self, services):
        self._s = services

    def write(self, msg):
        try:
            self._s.log(str(msg).rstrip())
        except Exception:  # noqa: BLE001
            pass

    def flush(self):  # some callers treat it as a stream
        pass


def run_populate(ctx, job, populate, busy: str = None):
    """Run `job()` off the GUI thread; call `populate(result, ok)` back on the GUI
    thread (marshalled by the shell's async bridge). `job` returns any value."""
    box = {}

    def _job():
        box["r"] = job()

    def _done(ok):
        try:
            populate(box.get("r"), ok)
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Error: {e}")

    if busy:
        ctx.services.log(busy)
    ctx.services.run_async(_job, done_cb=_done)


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        if it.widget():
            it.widget().deleteLater()
        elif it.layout():
            clear_layout(it.layout())
