"""Phase 1 · widgets.RefreshRegion + RefreshHandle — chrome-once, fill-in-place (spec §4).

The active noun-first slot. detail(snapshot, handle) -> (chrome_widget, fill_fn): the chrome
is built ONCE (real W.* helpers, restylers OK); fill_fn repopulates pre-built bodies with the
static vocabulary. The contract that matters:

  * handle.refresh() runs fill_fn — it NEVER re-invokes detail(), so a high-frequency refresh
    keeps the retint registry FLAT and calls detail exactly once (the B2 SHELL-06 guard);
  * fill_fn is handed the CURRENT snapshot each refresh;
  * handle.rebuild() re-invokes detail() but DEFERRED (QTimer.singleShot(0)) — the in-signal
    use-after-free segfault guard;
  * refresh() no-ops while a busy gate is set (re-entrancy vs an in-flight primary flow);
  * handle.snapshot() reads the current GUI-thread snapshot.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import gc
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _settle():
    """Flush pending destroys so a prior test's stale widget can't drop a restyler
    mid-measurement (makes the absolute registry-delta assertions GC-timing-immune)."""
    _APP.processEvents()
    gc.collect()
    _APP.processEvents()


def _make_detail(calls, fills):
    """A detail() that builds real (restyler-registering) chrome ONCE and returns a fill
    that touches only a dedicated body layout with the static vocabulary (no restylers)."""
    def detail(snap, handle):
        calls.append(dict(snap))
        card = W.Card()                       # real chrome → registers restyler(s), ONCE
        card.body.addWidget(W.eyebrow("Status"))
        body_host = QWidget(); body = QVBoxLayout(body_host)
        card.body.addWidget(body_host)

        def fill(s):
            fills.append(dict(s))
            from ui.util import clear_layout
            clear_layout(body)
            body.addWidget(W.static_label(str(s.get("n", "")), "body"))   # no restyler
        return card, fill
    return detail


def test_refresh_runs_fill_not_detail_and_keeps_registry_flat():
    calls, fills = [], []
    snap = {"n": 0}
    rr = W.RefreshRegion(ctx=None, snapshot=lambda: snap, detail=_make_detail(calls, fills))
    assert len(calls) == 1, "detail() runs once at build"
    _settle()
    base = len(W._RESTYLERS)
    for i in range(50):
        snap["n"] = i
        rr.handle.refresh()
    assert len(calls) == 1, "refresh() must NOT re-invoke detail() (that would re-register chrome)"
    assert len(fills) == 51, "refresh() runs fill_fn each time (1 build + 50 refresh)"
    assert len(W._RESTYLERS) == base, "refresh() must keep the retint registry flat (SHELL-06/B2)"


def test_fill_receives_current_snapshot():
    calls, fills = [], []
    snap = {"n": 7}
    rr = W.RefreshRegion(ctx=None, snapshot=lambda: dict(snap), detail=_make_detail(calls, fills))
    snap["n"] = 99
    rr.handle.refresh()
    assert fills[-1] == {"n": 99}, "fill_fn must see the current snapshot"


def test_rebuild_is_deferred_then_runs():
    calls, fills = [], []
    snap = {"n": 0}
    rr = W.RefreshRegion(ctx=None, snapshot=lambda: snap, detail=_make_detail(calls, fills))
    assert len(calls) == 1
    rr.handle.rebuild()
    assert len(calls) == 1, "rebuild() must be DEFERRED, not synchronous (segfault guard)"
    _APP.processEvents()                       # flush the QTimer.singleShot(0)
    assert len(calls) == 2, "after the event loop turn, detail() re-runs"


def test_refresh_noops_while_busy():
    calls, fills = [], []
    rr = W.RefreshRegion(ctx=None, snapshot=lambda: {"n": 1},
                         detail=_make_detail(calls, fills), busy=lambda: True)
    n = len(fills)
    rr.handle.refresh()
    assert len(fills) == n, "refresh() must no-op while the busy gate is set"


def test_handle_snapshot_reads_current():
    snap = {"n": 3}
    rr = W.RefreshRegion(ctx=None, snapshot=lambda: dict(snap), detail=_make_detail([], []))
    snap["n"] = 5
    assert rr.handle.snapshot() == {"n": 5}
