"""Phase-B shared polish patterns: quiet empty state, skeleton loading, popover
shadow, overlay scrollbars — reusable widgets every tab adopts uniformly."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QGraphicsDropShadowEffect  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.widgets as W  # noqa: E402
import ui.theme as T  # noqa: E402
import ui.motion as M  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


# ── quiet empty state ──────────────────────────────────────────────────────────
def test_empty_state_shows_line_and_optional_action():
    called = []
    action = W.btn("Add Symbol", kind="primary", on_click=lambda: called.append(1))
    es = W.empty_state("No Symbol Yet", glyph="", sub="Drop a .kicad_sym or add one", action=action)
    # the line text is present somewhere in the tree
    labels = es.findChildren(type(W.body("x")))
    texts = [l.text() for l in labels]
    assert any("No Symbol Yet" in t for t in texts)
    assert any("Drop a .kicad_sym" in t for t in texts)
    # the action button is embedded and wired
    btns = es.findChildren(type(action))
    assert action in btns
    _destroy(es)


def test_empty_state_without_action_is_valid():
    es = W.empty_state("Nothing Here")
    assert es is not None
    _destroy(es)


# ── skeleton loading ────────────────────────────────────────────────────────────
def test_skeleton_rows_builds_requested_shape():
    sk = W.skeleton_rows(rows=4, cols=3)
    blocks = sk.findChildren(W.Skeleton)
    assert len(blocks) == 4 * 3
    _destroy(sk)


def test_skeleton_is_static_under_reduced_motion():
    M.set_reduced_motion(True)
    s = W.Skeleton(width=80)
    assert s._anim is None            # no shimmer animation created
    _destroy(s)
    M.set_reduced_motion(False)


# ── popover shadow (popovers only, never cards) ──────────────────────────────────
def test_apply_popover_shadow_installs_soft_shadow():
    c = W.Card()
    W.apply_popover_shadow(c)
    eff = c.graphicsEffect()
    assert isinstance(eff, QGraphicsDropShadowEffect)
    assert eff.blurRadius() > 0
    _destroy(c)
