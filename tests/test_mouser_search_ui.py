"""Live typeahead Mouser search dialog: gates on min length, renders results,
tells a genuine no-match from a transport failure, caches per query, and returns
the picked part. run_populate runs synchronously under offscreen Qt, so these drive
the real dialog logic without a live event loop.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication, QPushButton
from PyQt5 import sip

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402
import ui.features.mouser_search as MS  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _ctx():
    return SimpleNamespace(cfg={}, services=SimpleNamespace(log=lambda *a, **k: None))


def _rows(dlg):
    return [b for b in dlg.findChildren(QPushButton) if b.objectName() == "mouserrow"]


def _part(mpn, **kw):
    base = {"mpn": mpn, "manufacturer": "STMicroelectronics", "description": "MCU",
            "stock": 100, "unit_price": 8.12, "lifecycle": "Active"}
    base.update(kw)
    return base


def _result(results, error_code="", error=""):
    return {"query": "q", "results": results, "error": error, "error_code": error_code}


def test_short_query_prompts_not_searches(monkeypatch):
    calls = []
    monkeypatch.setattr(LM, "search_parts", lambda *a, **k: calls.append(1) or _result([]))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="ab")
    assert "at least" in dlg._status.text().lower()
    assert calls == []                                  # never hit the network for <3 chars
    sip.delete(dlg)


def test_results_render_as_rows(monkeypatch):
    monkeypatch.setattr(LM, "search_parts",
                        lambda *a, **k: _result([_part("STM32F407VGT6"), _part("STM32F405RGT6")]))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32F40")
    assert len(_rows(dlg)) == 2
    assert "2 results" in dlg._status.text()
    sip.delete(dlg)


def test_no_match_reads_as_empty_not_error(monkeypatch):
    monkeypatch.setattr(LM, "search_parts", lambda *a, **k: _result([]))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="zzzznotapart")
    assert _rows(dlg) == []
    assert "no mouser parts match" in dlg._status.text().lower()
    sip.delete(dlg)


def test_transport_failure_is_shown_as_failure(monkeypatch):
    monkeypatch.setattr(LM, "search_parts",
                        lambda *a, **k: _result([], error_code="rate_limited",
                                                error="Mouser rate limit reached"))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32")
    assert _rows(dlg) == []
    assert "rate limit" in dlg._status.text().lower()   # NOT "no parts match"
    assert "no mouser parts match" not in dlg._status.text().lower()
    sip.delete(dlg)


def test_failures_are_not_cached_successes_are(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        # first call fails (network), later calls succeed
        if calls["n"] == 1:
            return _result([], error_code="network", error="Could not reach Mouser")
        return _result([_part("STM32F407VGT6")])

    monkeypatch.setattr(LM, "search_parts", flaky)
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="stm32f4")
    assert calls["n"] == 1 and _rows(dlg) == []         # failed, nothing cached
    dlg._run_query("stm32f4")                            # same query retries the network
    assert calls["n"] == 2 and len(_rows(dlg)) == 1     # now succeeds
    dlg._run_query("stm32f4")                            # served from cache, no new call
    assert calls["n"] == 2
    sip.delete(dlg)


def test_picking_a_row_sets_picked_and_accepts(monkeypatch):
    monkeypatch.setattr(LM, "search_parts", lambda *a, **k: _result([_part("STM32F407VGT6")]))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32F40")
    rows = _rows(dlg)
    assert len(rows) == 1
    rows[0].click()
    assert dlg.picked and dlg.picked["mpn"] == "STM32F407VGT6"
    assert dlg.result() == dlg.Accepted
    sip.delete(dlg)


def test_closed_dialog_drops_a_late_result(monkeypatch):
    # A worker callback that lands after the dialog is closed must not render into
    # dead widgets — the _closed guard makes done() early-return, so the result is
    # dropped rather than painted.
    monkeypatch.setattr(LM, "search_parts", lambda *a, **k: _result([_part("A"), _part("B")]))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32F40")
    dlg.done(dlg.Accepted)                              # exec_() returns; dialog closed
    assert dlg._closed is True
    dlg._run_query("a-new-query")                       # its sync callback must drop
    assert _rows(dlg) == []                             # nothing rendered (guarded)
    sip.delete(dlg)


def test_price_and_lifecycle_helpers():
    assert MS._fmt_price(8.1) == "$8.10"
    assert MS._fmt_price("$0.10") == "$0.10"
    assert MS._fmt_price("0.10") == "$0.10"
    assert MS._fmt_price("1,234.5") == "$1,234.5"       # thousands separator still numeric
    assert MS._fmt_price(None) == ""
    assert MS._lifecycle_marker("Active") is None
    assert MS._lifecycle_marker("Obsolete") is not None
    assert MS._lifecycle_marker("NRND") is not None


def test_fmt_price_never_dollar_prefixes_junk():
    # A malformed upstream value must show verbatim, NOT as a nonsense price ("$abc").
    assert MS._fmt_price("abc") == "abc"
    assert MS._fmt_price("N/A") == "N/A"
    assert MS._fmt_price("Call") == "Call"
    assert MS._fmt_price("$abc") == "$abc"               # non-numeric passes through unchanged


def test_rate_limit_message_points_to_a_real_lcsc_path(monkeypatch):
    # The keyword-search box has no LCSC path, so the capped message must NOT promise
    # "LCSC still works" here — it must send the user somewhere LCSC actually sources
    # (manual MPN entry / BOM sourcing), not imply this box can search LCSC.
    monkeypatch.setattr(LM, "search_parts",
                        lambda *a, **k: _result([], error_code="rate_limited",
                                                error="Mouser rate limit reached"))
    # No reset clock -> "resets at midnight" branch.
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: None)
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32")
    txt = dlg._status.text().lower()
    assert "lcsc still works" not in txt                # the unfulfillable promise is gone
    assert "bom sourcing" in txt                        # points at the real LCSC surface
    # And the with-clock branch too.
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: 3 * 3600)
    dlg._run_query("STM32F4")
    txt2 = dlg._status.text().lower()
    assert "lcsc still works" not in txt2
    assert "bom sourcing" in txt2 and "~3h" in txt2
    sip.delete(dlg)


def test_rate_limit_countdown_ticks_down_live(monkeypatch):
    # The countdown must re-read the reset clock, not freeze on its first snapshot: as
    # real time elapses toward reset, a later tick shows a smaller remaining figure.
    clock = {"secs": 3 * 3600 + 12 * 60}                # 3h 12m
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: clock["secs"])
    monkeypatch.setattr(LM, "search_parts",
                        lambda *a, **k: _result([], error_code="rate_limited",
                                                error="Mouser rate limit reached"))
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32")
    assert dlg._countdown.isActive()                    # ticking while capped
    assert "~3h 12m" in dlg._status.text()
    clock["secs"] = 12 * 60                              # time passed toward the reset
    dlg._tick_countdown()                               # what the QTimer fires
    assert "~12m" in dlg._status.text()                 # re-read, not frozen at 3h 12m
    sip.delete(dlg)


def test_countdown_stops_when_cap_clears_and_on_close(monkeypatch):
    seq = {"n": 0}

    def flip(*a, **k):
        seq["n"] += 1
        if seq["n"] == 1:
            return _result([], error_code="rate_limited", error="capped")
        return _result([_part("STM32F407VGT6")])        # cap cleared -> real results

    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: 3600)
    monkeypatch.setattr(LM, "search_parts", flip)
    dlg = MS.MouserSearchDialog(_ctx(), seed_query="STM32")
    assert dlg._countdown.isActive()                    # capped -> running
    dlg._run_query("STM32F40")                           # now succeeds
    assert not dlg._countdown.isActive()                # cleared -> stopped
    assert dlg._rl_base is None
    # And it never keeps ticking on a closed dialog.
    seq["n"] = 0
    dlg._run_query("re-cap")                             # capped again -> running
    assert dlg._countdown.isActive()
    dlg.done(dlg.Rejected)
    assert not dlg._countdown.isActive()
    sip.delete(dlg)
