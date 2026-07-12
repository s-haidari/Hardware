"""Phase 1 · kit.workbench — the assembled sub-tab recipe (spec §2/§5).

selector → quiet-when-OK verdict band → active detail region → 0-or-1 accent ▶ → 2-col
secondary → collapsible machinery → collapsible exports. The contract that matters:

  * a primary-kind action in `secondary` is rejected (the one-accent invariant);
  * a None-returning verdict hides the band (quiet-when-OK); a state shows it;
  * a 0-primary browse tab is legal (no ▶, no _run_primary seam);
  * with a primary, the _run_primary test seam drives audit→apply→report headlessly AND
    the verdict re-refreshes after (the after() hook);
  * a per-tab selector change triggers a DEFERRED region rebuild.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402
import ui.kit as K  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Rec:
    def __init__(self): self.logs = []
    def log(self, m): self.logs.append(str(m))


class _Ctx:
    def __init__(self): self.services = _Rec()


def _detail_factory(fills):
    def detail(snap, handle):
        card = W.Card()
        body_host = QWidget(); body = QVBoxLayout(body_host)
        card.body.addWidget(body_host)
        def fill(s):
            fills.append(dict(s))
            from ui.util import clear_layout
            clear_layout(body)
            body.addWidget(W.static_label(str(s.get("n", "")), "body"))
        return card, fill
    return detail


def test_rejects_primary_in_secondary():
    try:
        K.workbench(_Ctx(), title="T", snapshot=lambda: {}, detail=_detail_factory([]),
                    secondary=[K.action("X", lambda: None, kind="primary")])
        assert False, "a primary-kind secondary must be rejected"
    except ValueError:
        pass


def test_quiet_verdict_hidden_state_shown():
    wb_none = K.workbench(_Ctx(), title="T", snapshot=lambda: {"n": 1},
                          verdict=lambda snap: None, detail=_detail_factory([]))
    assert wb_none._verdict.isHidden(), "verdict returning None hides the band"
    wb_ok = K.workbench(_Ctx(), title="T", snapshot=lambda: {"n": 1},
                        verdict=lambda snap: W.VerdictState(kind="ok", title="Clean"),
                        detail=_detail_factory([]))
    assert not wb_ok._verdict.isHidden(), "a verdict state shows the band"
    assert wb_ok._verdict._title.text() == "Clean"


def test_zero_primary_browse_tab_is_legal():
    wb = K.workbench(_Ctx(), title="Parts", snapshot=lambda: {}, detail=_detail_factory([]),
                     primary=None)
    assert not hasattr(wb, "_run_primary"), "no primary ⇒ no ▶ seam"


def test_primary_seam_drives_flow_and_refreshes_verdict():
    ctx = _Ctx()
    seen = {"apply": None, "verdict_calls": 0}
    fills = []
    flow = K.PrimaryFlow(
        label="▶ Go",
        audit=lambda snap: [{"key": "a", "label": "A", "safe": True}],
        intro=lambda snap, ops: "intro",
        apply=lambda snap, keys: (seen.__setitem__("apply", list(keys)), {"summary": "done"})[1],
    )
    def verdict(snap):
        seen["verdict_calls"] += 1
        return W.VerdictState(kind="ok", title=f"v{seen['verdict_calls']}")
    wb = K.workbench(ctx, title="T", snapshot=lambda: {"n": 1}, verdict=verdict,
                     detail=_detail_factory(fills), primary=flow)
    assert hasattr(wb, "_run_primary")
    v_before = seen["verdict_calls"]
    wb._run_primary()
    assert seen["apply"] == ["a"], "the seam drives audit→apply headlessly with the safe key"
    assert any("done" in m for m in ctx.services.logs), "the report is logged"
    assert seen["verdict_calls"] > v_before, "after() re-refreshes the verdict"


def test_refresh_noops_during_in_flight_primary():
    ctx = _Ctx()
    fills = []
    marker = {"skipped": None}

    def apply(snap, keys):
        # We are INSIDE the flow → busy is set. A watchdog-style refresh must no-op so a
        # verdict/detail status read can't race this worker (the re-entrancy guard).
        n = len(fills)
        wb._region.handle.refresh()
        marker["skipped"] = (len(fills) == n)
        return {"summary": "done"}

    flow = K.PrimaryFlow(label="▶", audit=lambda s: [{"key": "a", "safe": True}],
                         intro=lambda s, o: "", apply=apply)
    wb = K.workbench(ctx, title="T", snapshot=lambda: {"n": 1},
                     verdict=lambda s: W.VerdictState(kind="ok", title="v"),
                     detail=_detail_factory(fills), primary=flow)
    wb._run_primary()
    assert marker["skipped"] is True, "a refresh during the in-flight flow must no-op (busy gate)"


def test_external_busy_dict_is_shared():
    # a feature can pass its own busy dict so secondary mutating ops share the flow's gate
    fills = []
    gate = {"on": False}
    wb = K.workbench(_Ctx(), title="T", snapshot=lambda: {"n": 1},
                     detail=_detail_factory(fills), busy=gate)
    assert wb._busy is gate, "the workbench uses the caller's busy dict"
    n = len(fills)
    gate["on"] = True                            # a secondary op is running
    wb._region.handle.refresh()
    assert len(fills) == n, "refresh no-ops while the shared gate is set"


def test_selector_change_defers_a_rebuild():
    fills = []
    state = {"n": 0}
    sel = K.Selector("N", ["0", "1", "2"])
    wb = K.workbench(_Ctx(), title="T", snapshot=lambda: dict(state),
                     selector=sel, detail=_detail_factory(fills))
    n_before = len(fills)
    state["n"] = 2
    sel._combo.setCurrentText("2")               # user pick
    assert len(fills) == n_before, "rebuild must be DEFERRED (not synchronous)"
    _APP.processEvents()
    assert len(fills) > n_before, "after the event loop turn the region rebuilds"
