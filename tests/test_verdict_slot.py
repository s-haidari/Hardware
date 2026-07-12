"""Phase 1 · widgets.VerdictSlot — the persistent, quiet-when-OK verdict band.

The recipe's colour verdict header (spec §3). Unlike widgets.Verdict (which registers a
restyler per chip + per label and is rebuilt-and-swapped on every refresh — the SHELL-06
churn), VerdictSlot is built ONCE and mutated IN PLACE via .set(state):

  * constructing it registers exactly ONE restyler (owned by self);
  * .set(state) N times keeps the retint registry FLAT — no per-refresh churn (the whole
    reason it exists, mirroring the static-vocab discipline);
  * .set(None) HIDES the band (quiet-when-OK, per BENCH-14); .set(state) shows it;
  * .set mutates the SAME title/subtitle labels + kind (no new widgets);
  * chips come from a FIXED pool (set fewer → extras hidden, none created);
  * a theme toggle retints it without growing the registry.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import gc
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _settle():
    """Flush pending widget destroys so a stale unparented widget from a PRIOR test can't
    fire `destroyed` → `_drop_restyle` mid-measurement (register_restyle drops on destroy).
    Makes the absolute-delta assertions immune to cross-test GC timing."""
    _APP.processEvents()
    gc.collect()
    _APP.processEvents()


def _state(kind="ok", title="Buildable", subtitle="", chips=()):
    return W.VerdictState(kind=kind, title=title, subtitle=subtitle, chips=chips)


def test_construct_registers_exactly_one_restyler():
    _settle()
    base = len(W._RESTYLERS)
    slot = W.VerdictSlot()               # keep a ref so it can't GC-drop before the assert
    assert len(W._RESTYLERS) == base + 1, "the band must register exactly ONE restyler"
    assert slot is not None


def test_set_many_times_keeps_registry_flat():
    slot = W.VerdictSlot()
    base = len(W._RESTYLERS)
    for i in range(60):
        slot.set(_state(kind=("ok" if i % 2 else "warn"),
                        title=f"state {i}", subtitle=f"sub {i}",
                        chips=[("Nets", str(i), "ok")]))
    assert len(W._RESTYLERS) == base, ".set() must NOT grow the retint registry (SHELL-06)"


def test_set_none_hides_band_and_state_shows_it():
    slot = W.VerdictSlot()
    slot.set(None)
    assert slot.isHidden(), ".set(None) must hide the band (quiet-when-OK)"
    slot.set(_state())
    assert not slot.isHidden(), ".set(state) must show the band"


def test_set_mutates_same_labels_and_stores_kind():
    slot = W.VerdictSlot()
    slot.set(_state(kind="warn", title="2 Changed", subtitle="unstaged"))
    assert slot._title.text() == "2 Changed"
    assert slot._sub.text() == "unstaged"
    assert slot._kind == "warn"
    # a second set reuses the SAME label objects (no rebuild)
    t_id, s_id = id(slot._title), id(slot._sub)
    slot.set(_state(kind="ok", title="Clean", subtitle=""))
    assert id(slot._title) == t_id and id(slot._sub) == s_id
    assert slot._title.text() == "Clean" and slot._kind == "ok"


def test_chips_use_a_fixed_pool():
    slot = W.VerdictSlot(chip_slots=3)
    # count QLabel descendants once, then set chips repeatedly — no NEW widgets appear
    def _labels():
        return len(slot.findChildren(QLabel))
    slot.set(_state(chips=[("Nets", "12", "ok"), ("Warnings", "1", "warn")]))
    after_two = _labels()
    slot.set(_state(chips=[("Nets", "0", "ok")]))
    slot.set(_state(chips=[("A", "1", "ok"), ("B", "2", "warn"), ("C", "3", "err")]))
    assert _labels() == after_two, "chips must come from a fixed pool — no widgets created per set()"


def test_retints_on_toggle_without_growth():
    slot = W.VerdictSlot()
    slot.set(_state(kind="err", title="Blocked", chips=[("Errors", "3", "err")]))
    W._prune_restylers()                # settle dead-owner restylers so base is stable (drop is lazy)
    base = len(W._RESTYLERS)
    W.restyle_all()                     # simulate a theme toggle
    assert len(W._RESTYLERS) == base, "a toggle must not grow the registry"
