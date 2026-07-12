"""Phase 1 · the remaining recipe primitives (spec §6): kit.button_grid,
widgets.CollapsibleSection, kit.export_action/ExportAction, kit.Selector.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QPushButton  # noqa: E402
import ui.kit as K  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


# ── button_grid — the 2-col secondary grid ────────────────────────────────────
def test_button_grid_lays_out_all_actions():
    acts = [K.action(f"Do {i}", (lambda: None)) for i in range(5)]
    grid = K.button_grid(acts, cols=2)
    assert len(grid.findChildren(QPushButton)) == 5


def test_button_grid_rejects_a_primary():
    acts = [K.action("A", lambda: None), K.action("B", lambda: None, kind="primary")]
    try:
        K.button_grid(acts)
        assert False, "button_grid must reject a primary-kind action (the accent lives in the flow)"
    except ValueError:
        pass


# ── CollapsibleSection — machinery/exports, collapsed by default ───────────────
def test_collapsible_none_body_hides_the_section():
    sec = W.CollapsibleSection("Manage", None)
    assert sec.isHidden(), "an empty section hides itself entirely"


def test_collapsible_starts_collapsed_and_toggles():
    from PyQt5.QtWidgets import QWidget
    body = QWidget()
    sec = W.CollapsibleSection("Manage", body)
    assert not sec.isHidden()
    assert not body.isVisible() or body.isHidden(), "body is collapsed by default"
    sec.toggle()
    assert sec.is_expanded(), "toggle expands"
    sec.toggle()
    assert not sec.is_expanded(), "toggle collapses again"


# ── export_action / ExportAction — save produce() to a path ───────────────────
def test_export_action_builds_dataclass():
    ea = K.export_action("Export BOM", lambda snap: "csv,data", "bom.csv", tip="t")
    assert ea.label == "Export BOM" and ea.filt and ea.tip == "t"
    assert ea.produce({}) == "csv,data"


def test_export_write_produces_file(tmp_path):
    ea = K.export_action("Export", lambda snap: f"n={snap['n']}", lambda snap: f"out{snap['n']}.txt")
    out = tmp_path / "x.txt"
    K._export_write(ea, {"n": 42}, str(out))
    assert out.read_text(encoding="utf-8") == "n=42"


def test_export_default_name_resolves_callable_and_str():
    ea1 = K.export_action("E", lambda s: "", lambda s: f"file{s['n']}.txt")
    ea2 = K.export_action("E", lambda s: "", "static.txt")
    assert K._export_default_name(ea1, {"n": 3}) == "file3.txt"
    assert K._export_default_name(ea2, {"n": 3}) == "static.txt"


# ── Selector — the per-tab object picker ──────────────────────────────────────
def test_selector_value_and_change():
    picks = []
    sel = K.Selector("Package", ["A", "B", "C"], on_change=picks.append, selected=0)
    assert sel.value() == "A"
    assert picks == [], "building must NOT fire on_change (no spurious initial pick)"
    sel._combo.setCurrentText("B")
    assert sel.value() == "B"
    assert picks == ["B"], "a user pick fires on_change with the selected text"


def test_selector_set_value_is_silent():
    picks = []
    sel = K.Selector("Package", ["A", "B"], on_change=picks.append)
    sel.set_value("B")
    assert sel.value() == "B"
    assert picks == [], "set_value reflects a change made elsewhere WITHOUT firing on_change"
