"""Regression tests for the audit fixes in nd_project_settings_manager.py.

Covers:
- Default-netclass via round-trip (via lives in net_settings.classes["Default"],
  NOT the dead board.design_settings.via_diameter/via_drill).
- _verify_saved reads the key KiCad actually consumes (honest, not deceptive).
- backup=True writes a <file>.kicad_pro.bak of the ORIGINAL before replace.
- junction -> junction_size_choice (enum 0-4, clamped); no default_junction_size.
- generic PCB text -> other_text_size_h/v / other_text_thickness (not text_size_h).
- check_project_locked exact-stem match (no false 'Main' vs 'Main_v2' lock).
- _clear_project_cache removes the REAL sibling locks (.kicad_pcb/.sch/.pro.lck).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_project_settings_manager as PSM  # noqa: E402

MM_PER_MIL = 0.0254


def _write_pro(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_pro(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── HIGH: Default-netclass via round-trip ────────────────────────────────────
def test_via_written_to_default_netclass_not_design_settings(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {"net_settings": {"classes": [{"name": "Default"}]}})

    m = PSM.ProjectSettingsManager()
    m.settings.default_via_diameter = 40.0   # mils -> 1.016 mm
    m.settings.default_via_drill = 20.0      # mils -> 0.508 mm
    assert m.save_to_project(pro, backup=False)

    data = _read_pro(pro)
    # Value landed in the Default net class (the key KiCad reads).
    default = next(c for c in data["net_settings"]["classes"] if c["name"] == "Default")
    assert abs(default["via_diameter"] - 1.016) < 1e-6
    assert abs(default["via_drill"] - 0.508) < 1e-6
    # And NOT in the dead design_settings keys.
    ds = data.get("board", {}).get("design_settings", {})
    assert "via_diameter" not in ds
    assert "via_drill" not in ds


def test_via_roundtrip_via_default_netclass(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {})  # no net_settings at all -> Default must be created

    m = PSM.ProjectSettingsManager()
    m.settings.default_via_diameter = 32.0
    m.settings.default_via_drill = 16.0
    assert m.save_to_project(pro, backup=False)

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_from_project(pro)
    assert m2.settings.default_via_diameter == 32.0
    assert m2.settings.default_via_drill == 16.0


# ── HIGH: honest verify (reads the consumed key, not the dead one) ───────────
def test_verify_reads_default_netclass_ignores_dead_key(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {"net_settings": {"classes": [{"name": "Default"}]}})

    m = PSM.ProjectSettingsManager()
    m.settings.default_via_diameter = 40.0
    m.settings.default_via_drill = 20.0
    assert m.save_to_project(pro, backup=False)

    ok, mismatches = m._verify_saved(pro)
    assert ok, mismatches

    # Injecting a bogus DEAD key must NOT make verify pass on a false value:
    # verify ignores design_settings.via_diameter entirely.
    data = _read_pro(pro)
    data.setdefault("board", {}).setdefault("design_settings", {})["via_diameter"] = 99.9
    _write_pro(pro, data)
    ok2, _ = m._verify_saved(pro)
    assert ok2  # still verified from the (correct) Default net class

    # Corrupting the REAL key (Default net class) must fail verification.
    data = _read_pro(pro)
    default = next(c for c in data["net_settings"]["classes"] if c["name"] == "Default")
    default["via_diameter"] = 0.5
    _write_pro(pro, data)
    ok3, mism3 = m._verify_saved(pro)
    assert not ok3
    assert any("Default.via_diameter" in s for s in mism3)


# ── HIGH: backup=True writes a .bak of the ORIGINAL ──────────────────────────
def test_backup_created_with_original_content(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    original = {"net_settings": {"classes": [{"name": "Default"}]}, "marker": "ORIGINAL"}
    _write_pro(pro, original)
    original_text = pro.read_text(encoding="utf-8")

    m = PSM.ProjectSettingsManager()
    m.settings.default_via_diameter = 50.0
    assert m.save_to_project(pro, backup=True)

    bak = tmp_path / "B.kicad_pro.bak"
    assert bak.exists(), "backup=True must create <file>.kicad_pro.bak"
    # The backup holds the pre-write original, not the modified file.
    assert bak.read_text(encoding="utf-8") == original_text
    # The live file was actually modified (marker preserved, via updated).
    data = _read_pro(pro)
    assert data["marker"] == "ORIGINAL"
    default = next(c for c in data["net_settings"]["classes"] if c["name"] == "Default")
    assert abs(default["via_diameter"] - 50.0 * MM_PER_MIL) < 1e-6


def test_no_backup_when_false(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    assert m.save_to_project(pro, backup=False)
    assert not (tmp_path / "B.kicad_pro.bak").exists()


def test_sync_honors_backup(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    results = m.sync_to_projects([pro], backup=True)
    assert results[pro] is True
    assert (tmp_path / "B.kicad_pro.bak").exists()


# ── MEDIUM: junction_size_choice enum key ────────────────────────────────────
def test_junction_written_as_choice_enum(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {})
    m = PSM.ProjectSettingsManager()
    m.settings.junction_size = 2
    assert m.save_to_project(pro, backup=False)

    drawing = _read_pro(pro)["schematic"]["drawing"]
    # The REAL key KiCad consumes is written and drives load/round-trip.
    assert drawing["junction_size_choice"] == 2

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_from_project(pro)
    # Load reads junction_size_choice (the consumed key), not the legacy field.
    assert m2.settings.junction_size == 2


def test_junction_choice_clamped_to_valid_range(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {})
    m = PSM.ProjectSettingsManager()
    m.settings.junction_size = 36     # legacy mils value -> must clamp to enum max 4
    assert m.save_to_project(pro, backup=False)
    assert _read_pro(pro)["schematic"]["drawing"]["junction_size_choice"] == 4

    m.settings.junction_size = -5
    assert m.save_to_project(pro, backup=False)
    assert _read_pro(pro)["schematic"]["drawing"]["junction_size_choice"] == 0


# ── MEDIUM: generic PCB text uses other_text_* keys ──────────────────────────
def test_pcb_text_uses_other_text_keys(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    _write_pro(pro, {})
    m = PSM.ProjectSettingsManager()
    m.settings.pcb_text_size = 40.0
    m.settings.pcb_text_thickness = 6.0
    assert m.save_to_project(pro, backup=False)

    defaults = _read_pro(pro)["board"]["design_settings"]["defaults"]
    assert "other_text_size_h" in defaults
    assert "other_text_size_v" in defaults
    assert "other_text_thickness" in defaults
    # Bare (dead) keys must not be written.
    assert "text_size_h" not in defaults
    assert "text_size_v" not in defaults
    assert "text_thickness" not in defaults

    m2 = PSM.ProjectSettingsManager()
    assert m2.load_from_project(pro)
    assert m2.settings.pcb_text_size == 40.0
    assert m2.settings.pcb_text_thickness == 6.0


# ── LOW: check_project_locked exact-stem match ───────────────────────────────
def test_check_project_locked_exact_stem(tmp_path):
    pro = tmp_path / "Main.kicad_pro"
    _write_pro(pro, {})
    m = PSM.ProjectSettingsManager()

    # A DIFFERENT project's lock must not register (substring bug: 'Main' in 'Main_v2').
    (tmp_path / "Main_v2.kicad_pcb.lck").write_text("", encoding="utf-8")
    assert m.check_project_locked(pro) is False

    # This project's real editor lock registers.
    (tmp_path / "Main.kicad_pcb.lck").write_text("", encoding="utf-8")
    assert m.check_project_locked(pro) is True


def test_check_project_locked_all_real_variants(tmp_path):
    for name in ("A.kicad_pcb.lck", "A.kicad_sch.lck", "A.kicad_pro.lck", "A.lck"):
        d = tmp_path / name.replace(".", "_")
        d.mkdir()
        pro = d / "A.kicad_pro"
        _write_pro(pro, {})
        (d / name).write_text("", encoding="utf-8")
        m = PSM.ProjectSettingsManager()
        assert m.check_project_locked(pro) is True, name


# ── LOW: _clear_project_cache removes the real sibling locks ──────────────────
def test_save_clears_real_lock_siblings(tmp_path):
    pro = tmp_path / "Master.kicad_pro"
    _write_pro(pro, {"net_settings": {"classes": [{"name": "Default"}]}})
    locks = [
        tmp_path / "Master.kicad_pcb.lck",
        tmp_path / "Master.kicad_sch.lck",
        tmp_path / "Master.kicad_pro.lck",
        tmp_path / "Master.kicad_prl",
    ]
    for f in locks:
        f.write_text("", encoding="utf-8")

    m = PSM.ProjectSettingsManager()
    assert m.save_to_project(pro, backup=False)
    for f in locks:
        assert not f.exists(), f"{f.name} should have been cleared"
