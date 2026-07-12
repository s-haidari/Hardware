"""EventBus.off / on_owned — unsubscribe + auto-unsubscribe on owner destroy, so a
rebuilt panel (project/package switch) stops leaking dead closures onto the bus."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402
from PyQt5 import sip  # noqa: E402
from ui.feature import EventBus  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


def test_off_unsubscribes():
    bus = EventBus()
    hits = []
    fn = lambda *_a: hits.append(1)
    bus.on("t", fn)
    bus.emit("t")
    bus.off("t", fn)
    bus.emit("t")
    assert hits == [1]                       # only the first emit reached fn


def test_off_is_idempotent():
    bus = EventBus()
    fn = lambda *_a: None
    bus.off("never", fn)                     # no such topic
    bus.on("t", fn)
    bus.off("t", fn)
    bus.off("t", fn)                         # already gone


def test_on_owned_auto_unsubscribes_on_destroy():
    bus = EventBus()
    hits = []
    owner = QWidget()
    bus.on_owned("t", lambda *_a: hits.append(1), owner)
    bus.emit("t")
    assert hits == [1]
    _destroy(owner)                          # destroyed fires -> auto off
    bus.emit("t")
    assert hits == [1]                       # no growth after owner died
