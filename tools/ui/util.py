"""ui.util — small helpers shared by feature modules."""
from __future__ import annotations

import os


def _headless() -> bool:
    """True under the offscreen Qt platform (render_gate / CI screenshots)."""
    return os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen")


def confirm(parent, title: str, text: str, default_no: bool = True) -> bool:
    """A yes/no confirmation dialog; returns True to proceed. Under headless (offscreen
    tests / render_gate) there is no user to click, and a modal QMessageBox would block
    the run forever — so it auto-proceeds. A test that wants to exercise the guarded
    action therefore sees it happen; render_gate never triggers one."""
    if _headless():
        return True
    from PyQt5.QtWidgets import QMessageBox
    default = QMessageBox.No if default_no else QMessageBox.Yes
    return QMessageBox.question(parent, title, text,
                                QMessageBox.Yes | QMessageBox.No, default) == QMessageBox.Yes


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
    if _headless():
        # Deterministic synchronous render under offscreen Qt (render_gate / CI).
        # A worker thread doing native Qt paint work while the main thread is still
        # building widgets intermittently corrupts the heap and access-violates on
        # Windows CI (the crash surfaces at whatever the main thread runs next).
        # The real app is never headless, so it keeps the responsive threaded path.
        ok = True
        try:
            box["r"] = job()
        except Exception as e:  # noqa: BLE001
            box["r"] = None; ok = False
            ctx.services.log(f"Error: {e}")
        _done(ok)
        return
    ctx.services.run_async(_job, done_cb=_done)


# ── length units (mm ⇄ mils) ─────────────────────────────────────────────────
# The UI stores CANONICAL millimetres everywhere; these convert only for display
# and on edit-commit. 1 mm = 39.3701 mils (1 mil = 0.0254 mm exactly).
MM_PER_MIL = 0.0254


def mm_to_mils(mm):
    """Millimetres -> mils (thousandths of an inch).

    None passes through as None: these are shared display helpers, and a
    missing/unknown length (e.g. a footprint dict with no width_mm) must
    surface as a clean empty display, not an unguarded TypeError from
    float(None). A real numeric value is converted as before."""
    if mm is None:
        return None
    return float(mm) / MM_PER_MIL


def mils_to_mm(mils):
    """Mils -> millimetres. None passes through as None (see mm_to_mils)."""
    if mils is None:
        return None
    return float(mils) * MM_PER_MIL


def fmt_countdown(secs: int) -> str:
    """A friendly "3h 12m" / "12m" / "under a minute" countdown from seconds.

    Shared by Settings, Mouser search, and the library preview so the shared-key
    reset text reads identically everywhere it surfaces."""
    if secs <= 0:
        return "any moment now"
    if secs < 60:
        return "under a minute"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m"


def sentence(text: str) -> str:
    """Format backend detail text as a real sentence: spelled-out words, a leading
    capital, and a trailing period. Applied to audit / ERC / DRC detail strings."""
    s = str(text).strip()
    if not s:
        return s
    s = s.replace(" vs ", " versus ").replace(" w/ ", " with ")
    s = s[0].upper() + s[1:]
    if s[-1] not in ".!?":
        s += "."
    return s


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            # Reparent out of the tree BEFORE the deferred delete so the widget
            # stops painting immediately — otherwise a rebuilt pane (e.g. the
            # library detail on part switch) shows the old content ghosted over
            # the new until deleteLater fires on the next loop return.
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            clear_layout(it.layout())
            it.layout().deleteLater()
