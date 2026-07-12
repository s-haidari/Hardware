"""Phase 1 · kit._report / kit._checkbox_preview / kit.run_primary_flow (spec §5).

The orchestrated ▶ primary, ported from bare and made HEADLESS-SAFE (the B1 blocker): the
modal ports MUST short-circuit under offscreen Qt (exec_() would spin a modal loop no user
dismisses and hang drive_audit / render_gate / pytest). So, headless:

  * kit._checkbox_preview returns the SAFE/pre-checked keys WITHOUT exec_();
  * kit._report logs the summary and returns WITHOUT exec_();
  * kit.run_primary_flow therefore runs audit → auto-approve safe → apply → report end to
    end synchronously (run_populate's headless branch is synchronous), with a busy gate
    toggled around it and an after() re-audit hook fired on completion.

kit._report_text is the pure structured-dict → text builder (summary / done / missing / errors).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402
import ui.kit as K  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Rec:
    def __init__(self): self.logs = []
    def log(self, m): self.logs.append(str(m))


class _Ctx:
    def __init__(self): self.services = _Rec()


def _host():
    return QWidget()


# ── the pure text builder ─────────────────────────────────────────────────────
def test_report_text_renders_structured_dict():
    txt = K._report_text({"summary": "Did the thing.",
                          "done": ["staged a", "committed b"],
                          "missing": [{"item": "upstream", "why": "no tracking branch",
                                       "how_to_fix": "push -u once"}],
                          "errors": ["push failed"]})
    assert "Did the thing." in txt
    assert "staged a" in txt and "committed b" in txt
    assert "upstream" in txt and "no tracking branch" in txt and "push -u once" in txt
    assert "push failed" in txt


def test_report_text_plain_string_passthrough():
    assert K._report_text("just a line") == "just a line"


# ── headless modal short-circuits (the B1 guard) ──────────────────────────────
def test_checkbox_preview_headless_returns_safe_keys_without_blocking():
    ops = [{"key": "a", "label": "A", "safe": True},
           {"key": "b", "label": "B", "safe": False},
           {"key": "c", "label": "C", "safe": True}]
    keys = K._checkbox_preview(_host(), "Title", "intro", ops)   # must NOT block headless
    assert keys == ["a", "c"], "headless preview auto-selects the safe/pre-checked ops"


def test_report_headless_logs_summary_without_blocking():
    rec = _Rec()
    K._report(_host(), "Commit & Sync", {"summary": "Committed abc123 + pushed"}, log=rec.log)
    assert any("Committed abc123" in m for m in rec.logs), "headless _report logs the summary"


# ── the full flow, headless end-to-end ────────────────────────────────────────
def test_run_primary_flow_headless_audit_approve_apply_report():
    ctx = _Ctx()
    seen = {"audit": None, "apply": None, "after": 0, "busy": []}
    flow = K.PrimaryFlow(
        label="▶ Do It",
        audit=lambda snap: (seen.__setitem__("audit", dict(snap)),
                            [{"key": "a", "label": "A", "safe": True},
                             {"key": "b", "label": "B", "safe": False}])[1],
        intro=lambda snap, ops: "intro",
        apply=lambda snap, keys: (seen.__setitem__("apply", (dict(snap), list(keys))),
                                  {"summary": "did it", "done": ["a"]})[1],
    )
    K.run_primary_flow(ctx, _host(), flow, snapshot=lambda: {"n": 1},
                       after=lambda: seen.__setitem__("after", seen["after"] + 1),
                       busy_gate=lambda on: seen["busy"].append(on))
    assert seen["audit"] == {"n": 1}, "audit runs with the snapshot"
    assert seen["apply"] == ({"n": 1}, ["a"]), "apply runs with only the SAFE keys (b unchecked)"
    assert any("did it" in m for m in ctx.services.logs), "the report summary is logged headless"
    assert seen["after"] == 1, "after() (re-audit) fires on completion"
    assert seen["busy"] == [True, False], "busy gate is set around the flow and cleared after"


def test_run_primary_flow_empty_audit_reports_empty_skips_apply():
    ctx = _Ctx()
    seen = {"apply": 0}
    flow = K.PrimaryFlow(
        label="▶ Do It",
        audit=lambda snap: [],
        intro=lambda snap, ops: "intro",
        apply=lambda snap, keys: seen.__setitem__("apply", seen["apply"] + 1),
        empty="Nothing to do here.",
    )
    K.run_primary_flow(ctx, _host(), flow, snapshot=lambda: {})
    assert seen["apply"] == 0, "empty audit must NOT call apply"
    assert any("Nothing to do here." in m for m in ctx.services.logs), "empty audit reports the empty message"
