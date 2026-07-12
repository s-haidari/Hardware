"""Profiles-tab de-pill (post-critique polish).

The Profiles tab was the worst "AI mockup" screen: a wall of bordered chips for
part numbers and switching pins. Design-rules §1.1 — a part number is text with
hierarchy, not a pill. These guard the two primitives that replaced the pills:
`token_link` (flat, clickable, no persistent fill) and `section_header`
(Title-case + hairline, replacing the shouted UPPERCASE eyebrow).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


def test_token_link_is_flat_not_a_filled_chip():
    calls = []
    b = W.token_link("STM32F407VGT6", lambda t: calls.append(t), tip="View pinout")
    assert b.objectName() == "toklink"
    # No persistent surface fill — the default state is transparent (the hover wash
    # is a separate :hover rule). A grid of these must not read as a wall of boxes.
    assert "background:transparent" in b.styleSheet().replace(" ", "")
    b.click()
    assert calls == ["STM32F407VGT6"]
    _destroy(b)


def test_token_link_two_tone_switching_pin():
    b = W.token_link("VBAT", lambda t: None, cat="power", sub="Pin 1")
    texts = [lab.text() for lab in b.findChildren(QLabel)]
    assert "VBAT" in texts and "Pin 1" in texts   # name + disambiguating pin number
    _destroy(b)


def test_section_header_keeps_title_case():
    # Unlike eyebrow() (which .upper()s), a section header preserves the given case,
    # so "Chips by Profile" stays Title case rather than shouting.
    w = W.section_header("Chips by Profile")
    texts = [lab.text() for lab in w.findChildren(QLabel)]
    assert "Chips by Profile" in texts
    assert "CHIPS BY PROFILE" not in texts
    _destroy(w)


def test_subhead_keeps_title_case():
    lab = W.subhead("Current Budget")
    assert lab.text() == "Current Budget"          # not uppercased
    _destroy(lab)


def test_net_label_has_no_surface_fill():
    # net_label is the fill-free variant of net_token: a column of them must not
    # wash the row (design-rules §6). The container carries no background.
    w = W.net_label("SERVICE_BOOT0", "service")
    assert "background:" not in w.styleSheet()     # no surface fill on the container
    _destroy(w)
