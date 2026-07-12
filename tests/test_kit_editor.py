"""Phase 2 · kit.editor — the third recipe shape (spec docs/superpowers/specs/
2026-07-10-phase2-projects-kit-editor.md §2).

An EDITOR surface (PCB Setup, Net Classes) holds UNSAVED user edits in live widgets, so
unlike kit.workbench it must NOT wrap its body in a RefreshRegion — a verdict/watchdog
refresh would re-run fill() and destroy the in-progress edit. The contract that matters:

  * build_body(ctx, host) is called EXACTLY ONCE; its controller is exposed as host._controller;
  * the verdict is PUSH — host._set_verdict(state) shows the band in place, (None) hides it,
    and a verdict push NEVER rebuilds the body (the editor's defining property);
  * a primary-kind action in `secondary` is rejected (the one-accent invariant);
  * a 0-primary editor is legal (no ▶, no _run_primary seam);
  * with a primary, _run_primary drives audit→apply→report headlessly reading the LIVE
    snapshot, and the after() hook (re-validate) fires;
  * building+destroying the editor leaves the restyle registry flat (leak guard);
  * an external busy dict is shared so secondary mutating ops gate with the ▶ flow.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QVBoxLayout, QWidget, QLabel  # noqa: E402
import ui.kit as K  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Rec:
    def __init__(self): self.logs = []
    def log(self, m): self.logs.append(str(m))


class _Ctx:
    def __init__(self): self.services = _Rec()


def _body_factory(calls):
    """A build_body that records each invocation and returns a trivial (widget, controller)."""
    def build_body(ctx, host):
        calls.append(host)
        w = QWidget(); v = QVBoxLayout(w)
        lab = W.static_label("editor body", "body")
        v.addWidget(lab)
        controller = {"widget": w, "label": lab, "value": 0}
        return w, controller
    return build_body


def test_build_body_called_once_and_controller_exposed():
    calls = []
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory(calls))
    assert len(calls) == 1, "build_body runs exactly once"
    assert ed._controller is not None and ed._controller["value"] == 0
    assert hasattr(ed, "_set_verdict")


def test_verdict_is_push_and_starts_hidden():
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]))
    assert ed._verdict.isHidden(), "an editor's verdict band is quiet until pushed"
    ed._set_verdict(W.VerdictState(kind="ok", title="In Spec"))
    assert not ed._verdict.isHidden() and ed._verdict._title.text() == "In Spec"
    ed._set_verdict(None)
    assert ed._verdict.isHidden(), "None hides the band (quiet-when-OK)"


def test_verdict_push_does_not_rebuild_body():
    """The defining editor property: pushing a verdict must NOT re-run build_body — the
    live edit state in the body survives (a RefreshRegion would clobber it)."""
    calls = []
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory(calls))
    ed._controller["value"] = 42                      # simulate an in-progress edit
    ed._set_verdict(W.VerdictState(kind="warn", title="Check"))
    ed._set_verdict(None)
    ed._set_verdict(W.VerdictState(kind="err", title="Bad"))
    assert len(calls) == 1, "build_body must NOT re-run on a verdict push"
    assert ed._controller["value"] == 42, "the live edit survives a verdict push"


def test_rejects_primary_in_secondary():
    try:
        K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]),
                 secondary=[K.action("X", lambda: None, kind="primary")])
        assert False, "a primary-kind secondary must be rejected"
    except ValueError:
        pass


def test_zero_primary_editor_is_legal():
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]))
    assert not hasattr(ed, "_run_primary"), "no primary ⇒ no ▶ seam"


def test_primary_drives_flow_reading_live_snapshot_and_after():
    ctx = _Ctx()
    seen = {"apply": None, "after": 0}
    live = {"n": 7}                                   # the live editor state snapshot reads
    flow = K.PrimaryFlow(
        label="▶ Save",
        audit=lambda snap: [{"key": f"n={snap['n']}", "label": "row", "safe": True}],
        intro=lambda snap, ops: "intro",
        apply=lambda snap, keys: (seen.__setitem__("apply", list(keys)), {"summary": "saved"})[1],
    )
    ed = K.editor(ctx, title="T", snapshot=lambda: dict(live), build_body=_body_factory([]),
                  primary=flow, after=lambda: seen.__setitem__("after", seen["after"] + 1))
    assert hasattr(ed, "_run_primary")
    ed._run_primary()
    assert seen["apply"] == ["n=7"], "audit→apply reads the LIVE snapshot and applies the safe key"
    assert any("saved" in m for m in ctx.services.logs), "the report is logged"
    assert seen["after"] == 1, "the after() hook (re-validate) fires once"


def test_external_busy_dict_is_shared():
    gate = K.BusyDict()
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]),
                  busy=gate)
    assert ed._busy is gate, "the editor uses the caller's busy dict"


def test_machinery_and_exports_collapse():
    ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]),
                  machinery=[K.action("Clear Cache", lambda: None)],
                  exports=[K.export_action("Template", lambda s: "x", "t.json")])
    cols = [c for c in ed.findChildren(W.CollapsibleSection) if c.isVisible() or c._body is not None]
    # Two visible collapsibles exist (Manage + Export); bodies present but collapsed.
    assert len(cols) >= 2, "machinery + exports each live in a CollapsibleSection"
    assert all(not c.is_expanded() for c in cols), "collapsibles start collapsed"


def _pump():
    _APP.processEvents(); _APP.processEvents()


def test_restyler_registry_stable_across_build_and_destroy():
    import gc
    import sip
    _pump(); gc.collect(); _pump()          # settle prior tests' dying widgets before baselining
    W._prune_restylers()                    # drop is lazy now — prune so the baseline excludes prior dead
    baseline = len(W._RESTYLERS)
    for _ in range(2):
        ed = K.editor(_Ctx(), title="T", snapshot=lambda: {}, build_body=_body_factory([]),
                      secondary=[K.action("A", lambda: None), K.action("B", lambda: None)],
                      machinery=[K.action("M", lambda: None)])
        ed._set_verdict(W.VerdictState(kind="ok", title="v"))
        assert len(W._RESTYLERS) > baseline, "it registered colour-bearing widgets"
        sip.delete(ed)                      # C++ destroy; the restyler drop is lazy (weakref, not destroyed.connect)
        _pump()
        W._prune_restylers()                # reclaim the dead-owner restylers
        assert len(W._RESTYLERS) == baseline, "every restyler released on prune (no leak)"
