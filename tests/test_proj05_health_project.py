"""PROJ-05: Health must grab EVERY component across ALL sheets, not just the root
schematic, and detect duplicates project-wide."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_project_health as H  # noqa: E402


def _c(ref, value="1k", fp="", lib="Device:R", props=None):
    return {"ref": ref, "value": value, "footprint": fp, "lib_id": lib,
            "props": props or {}}


def test_audit_project_aggregates_all_sheets(monkeypatch):
    comps_by = {
        "a.kicad_sch": [_c("R1", "10k")],
        "b.kicad_sch": [_c("R2", "1k"), _c("C1", "1u")],
    }
    monkeypatch.setattr(H, "schematic_components", lambda p: comps_by[str(p)])
    monkeypatch.setattr(H, "symbol_pin_counts", lambda p: {})
    au = H.audit_project(["a.kicad_sch", "b.kicad_sch"])
    assert au["components"] == 3           # every sheet's components collected
    assert au["sheets"] == 2
    assert not any(f["kind"] == "duplicate_ref" for f in au["findings"])   # unique refs


def test_audit_project_flags_intra_sheet_duplicates_only(monkeypatch):
    comps_by = {
        # SAME sheet, same ref, different parts -> a genuine duplicate.
        "a.kicad_sch": [_c("R1", "10k"), _c("R1", "1k")],
        # DIFFERENT sheets sharing a base ref -> NOT flagged (hierarchical artifact).
        "b.kicad_sch": [_c("C1", "1u")],
        "c.kicad_sch": [_c("C1", "2u")],
    }
    monkeypatch.setattr(H, "schematic_components", lambda p: comps_by[str(p)])
    monkeypatch.setattr(H, "symbol_pin_counts", lambda p: {})
    au = H.audit_project(["a.kicad_sch", "b.kicad_sch", "c.kicad_sch"])
    dups = {f["ref"] for f in au["findings"] if f["kind"] == "duplicate_ref"}
    assert dups == {"R1"}                  # R1 intra-sheet flagged; C1 cross-sheet ignored


def test_audit_project_empty_is_safe():
    au = H.audit_project([])
    assert au["components"] == 0 and au["sheets"] == 0 and au["findings"] == []


def test_audit_schematic_still_works(monkeypatch):
    monkeypatch.setattr(H, "schematic_components", lambda p: [_c("R?", "10k")])
    monkeypatch.setattr(H, "symbol_pin_counts", lambda p: {})
    au = H.audit_schematic("x.kicad_sch")
    assert any(f["kind"] == "unannotated" for f in au["findings"])
