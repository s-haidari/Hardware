"""Phase-A motion layer: single reduced-motion gate; animations become no-ops."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.motion as M  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


def test_reduced_motion_gate_get_set():
    M.set_reduced_motion(True)
    assert M.reduced_motion() is True
    M.set_reduced_motion(False)
    assert M.reduced_motion() is False


def test_animate_opacity_is_noop_under_reduced_motion():
    M.set_reduced_motion(True)
    done = []
    w = QLabel("x")
    anim = M.animate_opacity(w, 0.0, 1.0, on_done=lambda: done.append(1))
    assert anim is None            # no animation object created
    assert done == [1]             # final state applied synchronously
    _destroy(w)
    M.set_reduced_motion(False)


def test_animate_opacity_returns_animation_when_enabled():
    M.set_reduced_motion(False)
    w = QLabel("x")
    anim = M.animate_opacity(w, 0.0, 1.0, duration=120)
    assert anim is not None
    assert anim.duration() == 120
    anim.stop()
    _destroy(w)


def test_sliding_underline_snaps_under_reduced_motion():
    M.set_reduced_motion(True)
    u = M.SlidingUnderline()
    u.move_to(40, 80, animate=True)     # animate requested but reduced → snap
    assert u.geometry().x() == 40
    assert u.geometry().width() == 80
    _destroy(u)
    M.set_reduced_motion(False)


def test_sliding_underline_reused_after_animation_deleted_does_not_crash():
    # Regression (v2.10.0 "clicking a second subtab crashes the exe"): move_to started the
    # tween with anim.start(DeleteWhenStopped) while keeping a Python ref in self._anim. Once
    # the animation finished, DeleteWhenStopped freed the C++ QPropertyAnimation, leaving
    # self._anim a DANGLING wrapper whose next .stop() (the second subtab click) was a
    # use-after-free — a hard segfault on Windows, a RuntimeError on Linux/sip. Reusing the
    # underline after its previous animation object is gone must be safe.
    from PyQt5.QtCore import QEvent
    M.set_reduced_motion(False)
    u = M.SlidingUnderline()
    u.setGeometry(0, 0, 10, 2)
    u.move_to(100, 50, animate=True)         # first subtab: builds an animation
    first = u._anim
    if first is not None:
        first.stop()                         # finish it → (old code) DeleteWhenStopped frees it
    _APP.sendPostedEvents(None, QEvent.DeferredDelete)
    _APP.processEvents()
    # second subtab click — must NOT touch a freed animation object
    u.move_to(200, 60, animate=True)
    assert u.geometry().x() == 200 or u._anim is not None   # reached here without crashing
    _destroy(u)
    M.set_reduced_motion(False)


def test_cross_fade_applies_immediately_under_reduced_motion():
    M.set_reduced_motion(True)
    applied = []
    win = QWidget()
    win.resize(100, 100)
    M.cross_fade(win, lambda: applied.append(1))
    assert applied == [1]
    _destroy(win)
    M.set_reduced_motion(False)
