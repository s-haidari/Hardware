"""The ▶ primary-flow async-preview seam (kit.run_primary_flow / PrimaryFlow.preview_async).

A workbench can show its ▶ confirmation as an in-app SUBPAGE (no modal loop) by supplying a
non-blocking ``preview_async`` that resolves via a continuation instead of a return value.
These lock the seam without a shell: the flow pauses at the preview, ``cont(keys)`` applies and
``cont(None)`` cancels, an empty audit never reaches the preview, and the classic synchronous
``preview`` path is byte-for-byte unchanged."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


class _Svc:
    def __init__(self):
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))


class _Ctx:
    def __init__(self):
        self.services = _Svc()


def _one_op_flow(applied, preview_async=None, preview=None, audit_ops=None):
    from ui import kit
    return kit.PrimaryFlow(
        label="▶ Do Thing",
        audit=lambda snap: (audit_ops if audit_ops is not None
                            else [{"key": "k1", "label": "One", "safe": True}]),
        intro=lambda snap, ops: "intro text",
        apply=lambda snap, keys: (applied.update(keys=list(keys)) or {"summary": "did it"}),
        preview=preview,
        preview_async=preview_async,
    )


def test_async_preview_pauses_then_continue_applies():
    from ui import kit
    applied, captured = {}, {}

    def _preview_async(host, label, intro, ops, cont):
        captured["cont"] = cont
        captured["ops"] = ops

    ctx = _Ctx()
    kit.run_primary_flow(ctx, object(), _one_op_flow(applied, preview_async=_preview_async),
                         snapshot=lambda: {"n": 1})
    # The flow is parked at the subpage — nothing applied yet.
    assert "cont" in captured and applied == {}
    assert captured["ops"] == [{"key": "k1", "label": "One", "safe": True}]
    # Accepting the subpage with keys drives the apply.
    captured["cont"](["k1"])
    assert applied.get("keys") == ["k1"]


def test_async_preview_cancel_does_not_apply():
    from ui import kit
    applied, captured = {}, {}
    ctx = _Ctx()
    kit.run_primary_flow(ctx, object(),
                         _one_op_flow(applied, preview_async=lambda h, l, i, o, c: captured.__setitem__("cont", c)),
                         snapshot=lambda: {})
    captured["cont"](None)                       # Back / cancel
    assert applied == {}
    assert any("cancelled" in m for m in ctx.services.logs)


def test_async_preview_not_called_when_audit_is_empty():
    from ui import kit
    applied, captured = {}, {}
    ctx = _Ctx()
    flow = _one_op_flow(applied, preview_async=lambda *a: captured.__setitem__("cont", a[-1]),
                        audit_ops=[])
    flow.empty = "Nothing to do here."
    kit.run_primary_flow(ctx, object(), flow, snapshot=lambda: {})
    assert "cont" not in captured                # empty audit never opens the preview
    assert any("Nothing to do here." in m for m in ctx.services.logs)


def test_sync_preview_path_is_unchanged():
    from ui import kit
    applied = {}
    ctx = _Ctx()
    flow = _one_op_flow(applied, preview=lambda host, label, intro, ops: ["k1"])
    kit.run_primary_flow(ctx, object(), flow, snapshot=lambda: {})
    assert applied.get("keys") == ["k1"]         # classic synchronous preview still applies


def test_async_preview_takes_precedence_over_sync_preview():
    from ui import kit
    applied, captured = {}, {}
    ctx = _Ctx()
    # Both set: the async seam wins (the sync one must not fire).
    flow = _one_op_flow(applied,
                        preview_async=lambda h, l, i, o, c: captured.__setitem__("cont", c),
                        preview=lambda *a: (_ for _ in ()).throw(AssertionError("sync preview ran")))
    kit.run_primary_flow(ctx, object(), flow, snapshot=lambda: {})
    assert "cont" in captured and applied == {}
    captured["cont"](["k1"])
    assert applied.get("keys") == ["k1"]
