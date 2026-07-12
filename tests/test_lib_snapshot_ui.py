"""The Library detail pane surfaces a persisted sourcing snapshot (with its 'as of'
age) when there's no live lookup this session, and renders a Mouser '$8.12' string
price without crashing (the old f"${price:.2f}" blew up on string prices).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402
from ui.features.library_preview import PartDetail  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _ctx(tmp_path):
    return SimpleNamespace(cfg={"Libs": str(tmp_path)},
                           services=SimpleNamespace(log=lambda *a, **k: None),
                           bus=None)


def _labels(w):
    return [lab.text() for lab in w.findChildren(QLabel)]


def test_snapshot_is_shown_when_no_live_lookup(tmp_path):
    LM.save_sourcing_snapshot(tmp_path_cfg := {"Libs": str(tmp_path)}, "STM32F407VGT6",
                              {"unit_price": "$8.12", "stock": 421, "lifecycle": "Active"})
    pd = PartDetail(_ctx(tmp_path))
    pd.show({"mpn": "STM32F407VGT6", "name": "STM32F407VGT6", "symbols": []})
    texts = " ".join(_labels(pd))
    assert "Last priced" in texts                    # the as-of line rendered
    assert "$8.12" in texts                          # string price coerced + shown, no crash
    assert "421" in texts                            # stock from the snapshot
    sip.delete(pd)


def test_no_snapshot_shows_not_looked_up(tmp_path):
    pd = PartDetail(_ctx(tmp_path))
    pd.show({"mpn": "NOPART", "name": "NOPART", "symbols": []})
    texts = " ".join(_labels(pd))
    assert "Last priced" not in texts
    sip.delete(pd)
