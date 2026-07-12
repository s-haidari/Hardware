"""Regression tests for the audit fixes in tools/nd_netclass_manager.py.

Covers the pure-logic bugs fixed in the net class manager:
  * unmanaged / Default net-assignment preservation on save (HIGH)
  * export/import template round-trip of priority + microvia + diff_pair_via_gap (HIGH)
  * empty-manager guards on save_to_project and main_cli --sync-to (MEDIUM)
  * tolerant _hex_to_rgba hex-color validation/fallback (MEDIUM)
  * from_kicad_dict wire/bus width: 0 mils -> default, any int -> mils (LOW)

Run:  python -m pytest tests/test_audit_netclass.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_netclass_manager as NCM  # noqa: E402


# ── HIGH: unmanaged + Default net assignments survive save ────────────────
def test_save_preserves_unmanaged_and_default_patterns(tmp_path):
    """save_to_project must keep patterns for Default and unmanaged classes,
    then append managed patterns — not wipe everything not managed."""
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [
                {"name": "Default"},
                {"name": "HS"},          # unmanaged, user-created class
            ],
            "netclass_patterns": [
                {"netclass": "HS", "pattern": "*HDMI*"},
                {"netclass": "Default", "pattern": "*MISC*"},
                {"netclass": "PWR", "pattern": "*STALE*"},  # old managed pattern
            ],
        }
    }), encoding="utf-8")

    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="PWR", patterns=["*VDD*"]))
    assert m.save_to_project(pro, backup=False)

    # Unmanaged class definition recorded for the GUI.
    assert m.last_preserved_unmanaged == ["HS"]

    data = json.loads(pro.read_text(encoding="utf-8"))
    pats = {(p["netclass"], p["pattern"]) for p in data["net_settings"]["netclass_patterns"]}

    # Unmanaged + Default assignments survived.
    assert ("HS", "*HDMI*") in pats
    assert ("Default", "*MISC*") in pats
    # Fresh managed pattern written.
    assert ("PWR", "*VDD*") in pats
    # Stale managed pattern replaced, not duplicated.
    assert ("PWR", "*STALE*") not in pats

    # Unmanaged class definition itself is preserved too.
    names = {c["name"] for c in data["net_settings"]["classes"]}
    assert {"Default", "HS", "PWR"} <= names


# ── HIGH: template round-trips priority + microvia + diff_pair_via_gap ─────
def test_template_roundtrip_of_four_fields(tmp_path):
    """export_template -> import_template must preserve priority,
    microvia_diameter, microvia_drill, diff_pair_via_gap."""
    src = NCM.NetClassManager()
    src.add_netclass(NCM.NetClass(
        name="HS",
        priority=7,
        microvia_diameter=0.22,
        microvia_drill=0.11,
        diff_pair_via_gap=0.33,
    ))

    tpl = tmp_path / "vault.json"
    src.export_template(tpl)

    # The four fields are actually written to disk.
    on_disk = json.loads(tpl.read_text(encoding="utf-8"))["netclasses"]["HS"]
    for key in ("priority", "microvia_diameter", "microvia_drill", "diff_pair_via_gap"):
        assert key in on_disk, f"{key} missing from exported template"

    dst = NCM.NetClassManager()
    dst.import_template(tpl)
    nc = dst.get_netclass("HS")
    assert nc.priority == 7
    assert nc.microvia_diameter == 0.22
    assert nc.microvia_drill == 0.11
    assert nc.diff_pair_via_gap == 0.33


def test_template_import_uses_netclass_defaults_when_absent(tmp_path):
    """A legacy template missing the four fields imports with NetClass defaults,
    not None/crash."""
    tpl = tmp_path / "legacy.json"
    tpl.write_text(json.dumps({
        "version": "1.0.0",
        "netclasses": {"X": {"color": "#123456", "patterns": []}},
    }), encoding="utf-8")

    m = NCM.NetClassManager()
    m.import_template(tpl)
    nc = m.get_netclass("X")
    defaults = NCM.NetClass(name="X")
    assert nc.priority == defaults.priority
    assert nc.microvia_diameter == defaults.microvia_diameter
    assert nc.microvia_drill == defaults.microvia_drill
    assert nc.diff_pair_via_gap == defaults.diff_pair_via_gap


# ── MEDIUM: empty-manager guards ──────────────────────────────────────────
def test_empty_manager_save_does_not_wipe_patterns(tmp_path):
    """save_to_project with no managed classes must leave netclass_patterns
    untouched instead of unassigning every net."""
    pro = tmp_path / "P.kicad_pro"
    original = {
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "HS"}],
            "netclass_patterns": [
                {"netclass": "HS", "pattern": "*HDMI*"},
                {"netclass": "Default", "pattern": "*MISC*"},
            ],
        }
    }
    pro.write_text(json.dumps(original), encoding="utf-8")

    m = NCM.NetClassManager()  # empty
    assert m.save_to_project(pro, backup=False)

    data = json.loads(pro.read_text(encoding="utf-8"))
    pats = {(p["netclass"], p["pattern"]) for p in data["net_settings"]["netclass_patterns"]}
    assert pats == {("HS", "*HDMI*"), ("Default", "*MISC*")}


def test_cli_sync_to_empty_manager_errors_out(monkeypatch, tmp_path):
    """main_cli --sync-to with nothing loaded must refuse (SystemExit), not
    silently unassign every net."""
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [{"name": "Default"}]}}),
                   encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["nd_netclass_manager", "--sync-to", str(pro)])
    with pytest.raises(SystemExit) as excinfo:
        NCM.main_cli()
    assert excinfo.value.code == 2
    # The project file must be untouched by the aborted sync.
    data = json.loads(pro.read_text(encoding="utf-8"))
    assert data["net_settings"]["classes"] == [{"name": "Default"}]


# ── authoritative delete: a removed class must actually leave the file ────────
def test_load_then_delete_then_save_removes_class(tmp_path):
    """PCB projects:1826 — load the file's classes, delete one, save. The deleted
    class (and its patterns) must be GONE, not re-preserved as 'unmanaged'."""
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "HS"}, {"name": "PWR"}],
            "netclass_patterns": [
                {"netclass": "HS", "pattern": "*HDMI*"},
                {"netclass": "PWR", "pattern": "*VDD*"},
                {"netclass": "Default", "pattern": "*MISC*"},
            ],
        }
    }), encoding="utf-8")

    m = NCM.NetClassManager()
    assert m.load_from_project(pro)                 # HS + PWR are now managed
    m.remove_netclass("HS")                         # user deletes HS in the UI
    assert "HS" in m.deleted_names
    assert m.save_to_project(pro, backup=False)

    data = json.loads(pro.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["net_settings"]["classes"]}
    assert "HS" not in names                        # actually removed
    assert {"Default", "PWR"} <= names              # the rest survive
    # HS was NOT re-preserved as unmanaged.
    assert "HS" not in m.last_preserved_unmanaged
    pats = {(p["netclass"], p["pattern"]) for p in data["net_settings"]["netclass_patterns"]}
    assert ("HS", "*HDMI*") not in pats             # its pattern is gone too
    assert ("PWR", "*VDD*") in pats                 # other assignments intact
    assert ("Default", "*MISC*") in pats


def test_delete_does_not_touch_genuine_unmanaged(tmp_path):
    """A class the user never loaded into the manager (genuinely unmanaged) must
    still be preserved — only EXPLICIT deletions are dropped."""
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "HS"}, {"name": "USB"}],
            "netclass_patterns": [],
        }
    }), encoding="utf-8")

    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="HS"))
    m.remove_netclass("HS")                          # delete the one we manage
    assert m.save_to_project(pro, backup=False)

    data = json.loads(pro.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["net_settings"]["classes"]}
    assert "HS" not in names                         # explicit delete honoured
    assert "USB" in names                            # untouched unmanaged survives
    assert m.last_preserved_unmanaged == ["USB"]


def test_delete_all_managed_still_drops_deleted_patterns(tmp_path):
    """Empty managed set + a deletion: only the deleted class's patterns are dropped;
    every other assignment stays (the empty-manager guard must not re-add them)."""
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "HS"}],
            "netclass_patterns": [
                {"netclass": "HS", "pattern": "*HDMI*"},
                {"netclass": "Default", "pattern": "*MISC*"},
            ],
        }
    }), encoding="utf-8")

    m = NCM.NetClassManager()
    assert m.load_from_project(pro)
    m.remove_netclass("HS")                          # managed set now empty
    assert not m.net_classes
    assert m.save_to_project(pro, backup=False)

    data = json.loads(pro.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["net_settings"]["classes"]}
    assert "HS" not in names
    pats = {(p["netclass"], p["pattern"]) for p in data["net_settings"]["netclass_patterns"]}
    assert ("HS", "*HDMI*") not in pats              # deleted class's pattern dropped
    assert ("Default", "*MISC*") in pats             # everything else intact


def test_add_after_delete_clears_deletion_mark(tmp_path):
    """Re-adding a just-deleted name (New Net Class reusing it) must un-mark it so it
    is written, not wiped."""
    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="HS"))
    m.remove_netclass("HS")
    assert "HS" in m.deleted_names
    m.add_netclass(NCM.NetClass(name="HS", clearance=0.3))
    assert "HS" not in m.deleted_names
    assert "HS" in m.net_classes


# ── rename: keep the rules under the new name, drop the old from the file ─────
def test_rename_netclass_moves_rules_and_marks_old_deleted():
    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="OLD", clearance=0.25, patterns=["*SIG*"]))
    assert m.rename_netclass("OLD", "NEW")
    assert "OLD" not in m.net_classes and "NEW" in m.net_classes
    assert m.get_netclass("NEW").clearance == 0.25
    assert m.get_netclass("NEW").name == "NEW"
    assert m.patterns.get("NEW") == ["*SIG*"]
    assert "OLD" in m.deleted_names                  # old name will be deleted on save
    assert "NEW" not in m.deleted_names


def test_rename_rejects_duplicate_and_noop():
    m = NCM.NetClassManager()
    m.add_netclass(NCM.NetClass(name="A"))
    m.add_netclass(NCM.NetClass(name="B"))
    assert not m.rename_netclass("A", "B")           # would clobber B
    assert not m.rename_netclass("A", "A")           # no-op
    assert not m.rename_netclass("A", "  ")          # empty
    assert not m.rename_netclass("MISSING", "C")     # source absent
    assert {"A", "B"} == set(m.net_classes)


def test_rename_then_save_replaces_name_in_file(tmp_path):
    pro = tmp_path / "P.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [{"name": "Default"}, {"name": "OLD"}],
            "netclass_patterns": [{"netclass": "OLD", "pattern": "*SIG*"}],
        }
    }), encoding="utf-8")
    m = NCM.NetClassManager()
    assert m.load_from_project(pro)
    assert m.rename_netclass("OLD", "NEW")
    assert m.save_to_project(pro, backup=False)
    data = json.loads(pro.read_text(encoding="utf-8"))
    names = {c["name"] for c in data["net_settings"]["classes"]}
    assert "OLD" not in names and "NEW" in names
    pats = {(p["netclass"], p["pattern"]) for p in data["net_settings"]["netclass_patterns"]}
    assert ("NEW", "*SIG*") in pats
    assert ("OLD", "*SIG*") not in pats


# ── MEDIUM: tolerant hex-color validation ─────────────────────────────────
def test_hex_to_rgba_three_digit_shorthand():
    assert NCM.NetClass._hex_to_rgba("#abc") == "rgba(170, 187, 204, 1.000)"


def test_hex_to_rgba_without_hash():
    assert NCM.NetClass._hex_to_rgba("ABCDEF") == "rgba(171, 205, 239, 1.000)"


@pytest.mark.parametrize("bad", ["", "#", "red", "#12345", "#GGGGGG", "  ", None])
def test_hex_to_rgba_falls_back_on_bad_input(bad):
    assert NCM.NetClass._hex_to_rgba(bad) == "rgba(128, 128, 128, 1.000)"


def test_bad_color_does_not_abort_to_kicad_dict():
    """A user-entered bad color must not raise out of to_kicad_dict."""
    nc = NCM.NetClass(name="X", color="not-a-color")
    d = nc.to_kicad_dict()  # must not raise
    assert d["schematic_color"] == "rgba(128, 128, 128, 1.000)"
    assert d["pcb_color"] == "rgba(128, 128, 128, 1.000)"


# ── LOW: wire/bus width 0 (inherit) -> default, any int -> mils ───────────
def test_wire_width_zero_maps_to_default():
    nc = NCM.NetClass.from_kicad_dict("X", {"wire_width": 0, "bus_width": 0})
    assert nc.wire_thickness == pytest.approx(0.1524)
    assert nc.bus_thickness == pytest.approx(0.3048)


def test_small_int_width_treated_as_mils_not_mm():
    # Pre-fix, int 1-2 failed the ">2 mils" test and was read as 1-2 mm.
    nc = NCM.NetClass.from_kicad_dict("X", {"wire_width": 1, "bus_width": 2})
    assert nc.wire_thickness == pytest.approx(1 * 0.0254)
    assert nc.bus_thickness == pytest.approx(2 * 0.0254)


def test_normal_mil_width_roundtrips():
    nc = NCM.NetClass.from_kicad_dict("X", {"wire_width": 6, "bus_width": 12})
    assert nc.wire_thickness == pytest.approx(0.1524)
    assert nc.bus_thickness == pytest.approx(0.3048)


def test_float_width_treated_as_mm():
    nc = NCM.NetClass.from_kicad_dict("X", {"wire_width": 0.25, "bus_width": 0.5})
    assert nc.wire_thickness == pytest.approx(0.25)
    assert nc.bus_thickness == pytest.approx(0.5)


# ── CORRECTNESS: no phantom diff-pair on a non-diff class round-trip ──────
def test_non_diff_class_omits_diff_pair_keys_on_write():
    """A class with no diff pair must NOT emit KiCad's 0.2/0.25 defaults —
    that phantom reads back as a real diff-pair width."""
    nc = NCM.NetClass(name="GND")
    assert nc.diff_pair_width is None
    d = nc.to_kicad_dict()
    assert "diff_pair_width" not in d
    assert "diff_pair_gap" not in d


def test_non_diff_class_roundtrips_to_none():
    """GND (no diff pair) must survive to_kicad_dict -> from_kicad_dict as
    diff_pair_width=None, not resurrect as 0.2 mm."""
    nc = NCM.NetClass(name="GND")
    rt = NCM.NetClass.from_kicad_dict("GND", nc.to_kicad_dict())
    assert rt.diff_pair_width is None
    assert rt.diff_pair_gap is None


def test_real_diff_pair_class_roundtrips_intact():
    """A genuine diff-pair class keeps its exact width/gap through a round-trip."""
    nc = NCM.NetClass(name="USB", diff_pair_width=0.2, diff_pair_gap=0.15)
    d = nc.to_kicad_dict()
    assert d["diff_pair_width"] == 0.2
    assert d["diff_pair_gap"] == 0.15
    rt = NCM.NetClass.from_kicad_dict("USB", d)
    assert rt.diff_pair_width == pytest.approx(0.2)
    assert rt.diff_pair_gap == pytest.approx(0.15)


def test_zero_diff_pair_width_omits_keys():
    """A 0.0 width (vault-standard non-diff sentinel) must not be written as
    a diff pair."""
    nc = NCM.NetClass(name="PWR", diff_pair_width=0.0, diff_pair_gap=0.0)
    d = nc.to_kicad_dict()
    assert "diff_pair_width" not in d
    assert "diff_pair_gap" not in d


def test_present_diff_pair_keys_are_preserved_not_wiped():
    """Data-loss fix (backend-data-loss-fixes item 3): a class whose diff_pair
    keys are PRESENT on disk keeps its exact width/gap — even the 0.2/0.25 pair
    an older build baked in. 'No diff pair' is inferred ONLY when both keys are
    truly absent, so a legitimate 0.2/0.25 pair is never mistaken for a sentinel
    and silently dropped. has_diff_pair reflects key presence."""
    nc = NCM.NetClass.from_kicad_dict(
        "OLD", {"diff_pair_width": 0.2, "diff_pair_gap": 0.25})
    assert nc.diff_pair_width == pytest.approx(0.2)
    assert nc.diff_pair_gap == pytest.approx(0.25)
    assert nc.has_diff_pair is True


def test_absent_diff_pair_keys_read_as_no_diff_pair():
    """When BOTH diff_pair keys are absent, the class is a non-diff class:
    width/gap are None and has_diff_pair is False."""
    nc = NCM.NetClass.from_kicad_dict("GND", {"clearance": 0.2, "track_width": 0.25})
    assert nc.diff_pair_width is None
    assert nc.diff_pair_gap is None
    assert nc.has_diff_pair is False
