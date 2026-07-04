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
