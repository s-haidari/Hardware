"""Regression tests for the EXTENDED Board/Schematic-Setup coverage added to
nd_project_settings_manager.py (audit §3 completeness gaps + feature spec §5).

New capability under test (each read + write to the REAL KiCad key + verify):
- board.design_settings.rule_severities  (curated DRC severities)
- erc.rule_severities  (+ erc.pin_map matrix, erc.erc_exclusions)
- text_variables  (project-level {VAR} map at .kicad_pro top level)
- design_settings.track_widths / via_dimensions / diff_pair_dimensions
- editable Default net class (net_settings.classes[name=="Default"])
- masked-missing-key tracking (present vs. absent) and native-mm no-drift

These only exercise the assigned public API; they never touch config.json, the
GUI, or any other source file. Everything is tmp .kicad_pro round-trips.
"""
import json
import sys
import pathlib
from pathlib import Path

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_project_settings_manager as PSM  # noqa: E402


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────
# DRC rule severities  (board.design_settings.rule_severities)
# ─────────────────────────────────────────────────────────────────────────────
def test_drc_severity_roundtrip_and_real_key(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})

    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("clearance", "warning")
    m.set_drc_severity("silk_overlap", "ignore")
    m.set_drc_severity("courtyards_overlap", "error")
    assert m.save_extended(pro)

    rs = _read(pro)["board"]["design_settings"]["rule_severities"]
    assert rs["clearance"] == "warning"
    assert rs["silk_overlap"] == "ignore"
    assert rs["courtyards_overlap"] == "error"

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    assert m2.drc_severities["clearance"] == "warning"
    assert m2.drc_severities["silk_overlap"] == "ignore"


def test_drc_severity_preserve_by_default(tmp_path):
    # A non-managed severity already in the file must survive an update to a
    # different rule (deep-merge, not overwrite).
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"board": {"design_settings": {"rule_severities": {
        "solder_mask_bridge": "error", "clearance": "error"}}}})

    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("clearance", "ignore")
    assert m.save_extended(pro)

    rs = _read(pro)["board"]["design_settings"]["rule_severities"]
    assert rs["clearance"] == "ignore"          # managed key updated
    assert rs["solder_mask_bridge"] == "error"  # untouched key preserved


def test_drc_severity_validation():
    m = PSM.ProjectSettingsManager()
    with pytest.raises(ValueError):
        m.set_drc_severity("clearance", "fatal")       # bad level
    with pytest.raises(ValueError):
        m.set_drc_severity("not_a_real_rule", "error")  # bad rule id


def test_verify_extended_catches_drc_mismatch(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("track_width", "warning")
    assert m.save_extended(pro)
    ok, mism = m.verify_extended(pro)
    assert ok, mism

    # Corrupt the real key -> verify must fail and name it.
    data = _read(pro)
    data["board"]["design_settings"]["rule_severities"]["track_width"] = "error"
    _write(pro, data)
    ok2, mism2 = m.verify_extended(pro)
    assert not ok2
    assert any("track_width" in s for s in mism2)


# ─────────────────────────────────────────────────────────────────────────────
# ERC severities + pin_map + exclusions  (top-level "erc")
# ─────────────────────────────────────────────────────────────────────────────
def test_erc_severity_roundtrip(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_erc_severity("pin_not_connected", "warning")
    m.set_erc_severity("power_pin_not_driven", "ignore")
    assert m.save_extended(pro)

    ers = _read(pro)["erc"]["rule_severities"]
    assert ers["pin_not_connected"] == "warning"
    assert ers["power_pin_not_driven"] == "ignore"

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    assert m2.erc_severities["pin_not_connected"] == "warning"


def test_erc_severity_validation():
    m = PSM.ProjectSettingsManager()
    with pytest.raises(ValueError):
        m.set_erc_severity("pin_not_connected", "nope")
    with pytest.raises(ValueError):
        m.set_erc_severity("bogus_rule", "error")


def test_erc_pin_map_roundtrip_and_symmetric(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.ensure_erc_pin_map()
    m.set_erc_pin_map_entry(0, 6, 1)   # input vs unspecified -> warning
    m.set_erc_pin_map_entry(7, 8, 2)   # power_in vs power_out -> error
    assert m.save_extended(pro)

    pm = _read(pro)["erc"]["pin_map"]
    assert len(pm) == PSM.ERC_PIN_MAP_SIZE
    assert all(len(row) == PSM.ERC_PIN_MAP_SIZE for row in pm)
    assert pm[0][6] == 1 and pm[6][0] == 1   # symmetric mirror
    assert pm[7][8] == 2 and pm[8][7] == 2

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    assert m2.erc_pin_map[0][6] == 1
    ok, mism = m2.verify_extended(pro)
    assert ok, mism


def test_erc_pin_map_bounds():
    m = PSM.ProjectSettingsManager()
    with pytest.raises(ValueError):
        m.set_erc_pin_map_entry(0, 99, 1)
    with pytest.raises(ValueError):
        m.set_erc_pin_map_entry(0, 0, 9)


def test_erc_exclusions_roundtrip(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"erc": {"erc_exclusions": ["existing_serialised_exclusion"]}})
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    assert m.was_present("erc.erc_exclusions")
    assert m.erc_exclusions == ["existing_serialised_exclusion"]
    # Round-trips verbatim through a save.
    assert m.save_extended(pro)
    assert _read(pro)["erc"]["erc_exclusions"] == ["existing_serialised_exclusion"]


# ─────────────────────────────────────────────────────────────────────────────
# text_variables  (top-level {VAR} map)
# ─────────────────────────────────────────────────────────────────────────────
def test_text_variables_roundtrip_and_merge(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"text_variables": {"KEEP": "old"}})
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    assert m.was_present("text_variables")
    m.set_text_variable("REV", "A2")
    m.set_text_variable("KEEP", "old")   # unchanged managed
    assert m.save_extended(pro)

    tv = _read(pro)["text_variables"]
    assert tv["REV"] == "A2"
    assert tv["KEEP"] == "old"           # merge preserves


def test_text_variables_written_at_top_level(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_text_variable("BOARD_REV", "v3")
    assert m.save_extended(pro)
    data = _read(pro)
    assert data["text_variables"]["BOARD_REV"] == "v3"


# ─────────────────────────────────────────────────────────────────────────────
# Predefined size tables  (track_widths / via_dimensions / diff_pair_dimensions)
# ─────────────────────────────────────────────────────────────────────────────
def test_track_widths_roundtrip_native_mm_no_drift(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_track_widths([0.2, 0.25, 0.5])
    assert m.save_extended(pro)

    tw = _read(pro)["board"]["design_settings"]["track_widths"]
    # KiCad convention: leading 0.0 = "use net-class width".
    assert tw[0] == 0.0
    # Native mm: 0.2 stays EXACTLY 0.2 (no 0.2007 mils-grid drift).
    assert 0.2 in tw and 0.25 in tw and 0.5 in tw
    for v in tw:
        assert v == round(v, 6)

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    ok, mism = m2.verify_extended(pro)
    assert ok, mism


def test_via_dimensions_roundtrip_real_keys(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_via_dimensions([(0.8, 0.4), (0.6, 0.3)])
    assert m.save_extended(pro)

    vd = _read(pro)["board"]["design_settings"]["via_dimensions"]
    # Real KiCad keys are exactly "diameter" and "drill".
    assert all(set(row.keys()) == {"diameter", "drill"} for row in vd)
    assert {"diameter": 0.8, "drill": 0.4} in vd
    assert {"diameter": 0.6, "drill": 0.3} in vd
    # No drift on the mm values.
    assert vd[1]["diameter"] == 0.8 and vd[1]["drill"] == 0.4


def test_diff_pair_dimensions_roundtrip_real_keys(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})
    m = PSM.ProjectSettingsManager()
    m.set_diff_pair_dimensions([(0.2, 0.15, 0.25)])
    assert m.save_extended(pro)

    dp = _read(pro)["board"]["design_settings"]["diff_pair_dimensions"]
    # Real KiCad keys: width / gap / via_gap.
    assert all(set(row.keys()) == {"width", "gap", "via_gap"} for row in dp)
    row = next(r for r in dp if r["width"] == 0.2)
    assert row["gap"] == 0.15 and row["via_gap"] == 0.25


# ─────────────────────────────────────────────────────────────────────────────
# Editable Default net class  (net_settings.classes[name=="Default"])
# ─────────────────────────────────────────────────────────────────────────────
def test_default_netclass_editable_roundtrip(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.set_default_netclass(clearance=0.2, track_width=0.25,
                           via_diameter=0.8, via_drill=0.4)
    assert m.save_extended(pro)

    default = next(c for c in _read(pro)["net_settings"]["classes"]
                   if c["name"] == "Default")
    # mm-native, exact (no drift).
    assert default["clearance"] == 0.2
    assert default["track_width"] == 0.25
    assert default["via_diameter"] == 0.8
    assert default["via_drill"] == 0.4

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_extended(pro)
    assert m2.default_netclass.clearance == 0.2
    assert m2.default_netclass.track_width == 0.25
    ok, mism = m2.verify_extended(pro)
    assert ok, mism


def test_default_netclass_created_when_absent(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})  # no net_settings at all
    m = PSM.ProjectSettingsManager()
    m.set_default_netclass(clearance=0.15)
    assert m.save_extended(pro)
    classes = _read(pro)["net_settings"]["classes"]
    default = next(c for c in classes if c["name"] == "Default")
    assert default["clearance"] == 0.15


def test_default_netclass_unmanaged_fields_preserved(tmp_path):
    # Only clearance is managed; an existing via_diameter must be preserved.
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [
        {"name": "Default", "via_diameter": 0.6, "track_width": 0.3}]}})
    m = PSM.ProjectSettingsManager()
    m.set_default_netclass(clearance=0.2)
    assert m.save_extended(pro)
    default = next(c for c in _read(pro)["net_settings"]["classes"]
                   if c["name"] == "Default")
    assert default["clearance"] == 0.2
    assert default["via_diameter"] == 0.6   # untouched
    assert default["track_width"] == 0.3    # untouched


# ─────────────────────────────────────────────────────────────────────────────
# Masked-missing-key fix  (present vs. absent; no manufactured defaults)
# ─────────────────────────────────────────────────────────────────────────────
def test_load_does_not_manufacture_defaults(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {})   # empty project
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    # Nothing was present -> nothing managed, no fabricated defaults.
    assert m.drc_severities == {}
    assert m.erc_severities == {}
    assert m.text_variables == {}
    assert m.track_widths == []
    assert m.via_dimensions == []
    assert m.default_netclass.clearance is None
    assert m.default_netclass.via_diameter is None
    assert not m.was_present("text_variables")
    assert not m.was_present("board.rule_severities")
    assert not m.was_present("default_netclass")


def test_present_vs_absent_distinguished(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"text_variables": {}, "board": {"design_settings": {
        "track_widths": []}}})
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    # A genuinely-empty-but-present structure is distinguished from absent.
    assert m.was_present("text_variables")
    assert m.was_present("track_widths")
    assert not m.was_present("via_dimensions")
    assert m.text_variables == {}
    assert m.track_widths == []


def test_absent_default_via_stays_none_not_zero(tmp_path):
    # A Default class with NO via_diameter key must load as None, never 0.0.
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default", "clearance": 0.0}]}})
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    assert m.default_netclass.clearance == 0.0    # genuine 0.0 preserved
    assert m.default_netclass.via_diameter is None  # absent -> None


# ─────────────────────────────────────────────────────────────────────────────
# Native-unit drift  (skip-rewrite-when-unchanged, exact mm compare)
# ─────────────────────────────────────────────────────────────────────────────
def test_set_mm_if_changed_skips_unchanged():
    m = PSM.ProjectSettingsManager()
    container = {"via_diameter": 0.6}
    # Same value -> no rewrite.
    assert m._set_mm_if_changed(container, "via_diameter", 0.6) is False
    # Different value -> rewrite, exact mm (no drift).
    assert m._set_mm_if_changed(container, "via_diameter", 0.5) is True
    assert container["via_diameter"] == 0.5
    # Absent key -> always writes.
    assert m._set_mm_if_changed(container, "track_width", 0.2) is True
    assert container["track_width"] == 0.2


def test_default_netclass_no_drift_on_noop(tmp_path):
    # Value already at 0.2 mm; managing it to 0.2 must keep it EXACTLY 0.2 (the
    # old mils path produced 0.2007). Byte-identical stored value.
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [
        {"name": "Default", "clearance": 0.2, "via_diameter": 0.8}]}})
    m = PSM.ProjectSettingsManager()
    m.set_default_netclass(clearance=0.2, via_diameter=0.8)
    assert m.save_extended(pro)
    default = next(c for c in _read(pro)["net_settings"]["classes"]
                   if c["name"] == "Default")
    assert default["clearance"] == 0.2
    assert default["via_diameter"] == 0.8


def test_clean_mm_no_mils_grid():
    # Values must NOT be snapped to the 0.1-mil grid.
    assert PSM._clean_mm(0.2) == 0.2
    assert PSM._clean_mm(0.8) == 0.8
    assert PSM._clean_mm(0.127) == 0.127


# ─────────────────────────────────────────────────────────────────────────────
# Integration: save_to_project + _verify_saved + sync_to_projects include extended
# ─────────────────────────────────────────────────────────────────────────────
def test_save_to_project_also_applies_extended(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("clearance", "warning")
    m.set_text_variable("REV", "B1")
    # The legacy flat save path must ALSO flush extended state.
    assert m.save_to_project(pro, backup=False)
    data = _read(pro)
    assert data["board"]["design_settings"]["rule_severities"]["clearance"] == "warning"
    assert data["text_variables"]["REV"] == "B1"


def test_verify_saved_includes_extended_mismatch(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("clearance", "warning")
    assert m.save_to_project(pro, backup=False)
    ok, mism = m._verify_saved(pro)
    assert ok, mism

    # Corrupt the extended key -> the combined verify must fail.
    data = _read(pro)
    data["board"]["design_settings"]["rule_severities"]["clearance"] = "error"
    _write(pro, data)
    ok2, mism2 = m._verify_saved(pro)
    assert not ok2
    assert any("clearance" in s for s in mism2)


def test_sync_to_projects_verifies_extended(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.set_erc_severity("pin_not_connected", "ignore")
    m.set_track_widths([0.2, 0.3])
    results = m.sync_to_projects([pro], backup=False)
    assert results[pro] is True
    assert m.last_sync_details[pro] == "verified"


def test_fresh_manager_does_not_inject_extended(tmp_path):
    # Backward-compat: a fresh manager that only sets flat settings must NOT
    # manufacture rule_severities / text_variables / size tables into the file.
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.settings.schematic_text_size = 55.0
    assert m.save_to_project(pro, backup=False)
    ds = _read(pro).get("board", {}).get("design_settings", {})
    assert "rule_severities" not in ds
    assert "track_widths" not in ds
    assert "text_variables" not in _read(pro)


def test_save_extended_honors_backup(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write(pro, {"marker": "ORIG"})
    original = pro.read_text(encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    m.set_drc_severity("clearance", "warning")
    assert m.save_extended(pro, backup=True)
    bak = tmp_path / "B.kicad_pro.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original


# ─────────────────────────────────────────────────────────────────────────────
# Validate our target keys against a REAL KiCad-written file (skip if absent)
# ─────────────────────────────────────────────────────────────────────────────
_REAL_PROS = [
    Path(r"C:/Users/Sadad Haidari/git/NETDECK/Master/Master.kicad_pro"),
    Path(r"C:/Users/Sadad Haidari/kicad-mcp-sandbox/netdeck-parent.kicad_pro"),
]


def _first_real_pro():
    for p in _REAL_PROS:
        if p.exists():
            return p
    return None


def test_target_keys_exist_in_real_kicad_file():
    real = _first_real_pro()
    if real is None:
        pytest.skip("no real .kicad_pro available on this machine")
    data = json.loads(real.read_text(encoding="utf-8"))
    ds = data.get("board", {}).get("design_settings", {})
    erc = data.get("erc", {})
    # Every structure we read/write must be a real key in a KiCad-written file.
    assert isinstance(ds.get("rule_severities"), dict)
    assert isinstance(ds.get("track_widths"), list)
    assert isinstance(ds.get("via_dimensions"), list)
    assert isinstance(ds.get("diff_pair_dimensions"), list)
    assert isinstance(erc.get("rule_severities"), dict)
    assert isinstance(erc.get("pin_map"), list)
    assert isinstance(erc.get("erc_exclusions"), list)
    assert isinstance(data.get("text_variables"), dict)
    # Our curated IDs must be a subset of what KiCad actually emits.
    real_drc = set(ds["rule_severities"].keys())
    real_erc = set(erc["rule_severities"].keys())
    assert set(PSM.DRC_RULE_IDS) <= real_drc, set(PSM.DRC_RULE_IDS) - real_drc
    assert set(PSM.ERC_RULE_IDS) <= real_erc, set(PSM.ERC_RULE_IDS) - real_erc
    # pin_map is the 12x12 matrix we assume.
    assert len(erc["pin_map"]) == PSM.ERC_PIN_MAP_SIZE


def test_load_extended_on_real_file_roundtrips():
    real = _first_real_pro()
    if real is None:
        pytest.skip("no real .kicad_pro available on this machine")
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(real)
    # Re-verifying the freshly-loaded managed state against the same file passes
    # (proves load reads the same keys verify checks).
    ok, mism = m.verify_extended(real)
    assert ok, mism
