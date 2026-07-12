"""Backend round-trip / data-loss regression tests (subsystem: backend-data-loss-fixes).

Each test proves a value a user saves actually lands on the REAL KiCad key and
survives a re-read — the theme of this subsystem is eliminating silent drops.

Covered (one section per scope item):
  1. solder-mask/paste route to the sibling .kicad_pcb (setup ...) block and
     verify reads them back from there (NOT the dead .kicad_pro keys).
  2. every DRC/ERC rule id present in the file round-trips, incl. a custom id.
  3. a genuine 0.2/0.25 differential-pair class is preserved, not wiped.
  4. a corrupt .kicad_pcb yields a STRUCTURED error, never a crash, and still
     lets schematic-side .kicad_pro settings save.
  5. object-conform run twice is a true no-op: byte-identical output + zero count.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_board_setup as BS  # noqa: E402
import nd_project_settings_manager as PSM  # noqa: E402
import nd_netclass_manager as NCM  # noqa: E402
import nd_object_conform as OC  # noqa: E402


# A realistic, tab-indented .kicad_pcb with a (setup ...) block (no solder keys yet).
_PCB = (
    "(kicad_pcb\n"
    "\t(version 20241229)\n"
    '\t(generator "pcbnew")\n'
    "\t(general\n"
    "\t\t(thickness 1.6)\n"
    "\t)\n"
    "\t(layers\n"
    '\t\t(0 "F.Cu" signal)\n'
    '\t\t(31 "B.Cu" signal)\n'
    "\t)\n"
    "\t(setup\n"
    "\t\t(pad_to_mask_clearance 0)\n"
    "\t)\n"
    "\t(fp_text value \"R1\" (at 0 0) (layer \"F.SilkS\") (effects (font (size 1.5 1.5) (thickness 0.3))))\n"
    '\t(gr_text "TITLE" (at 5 5) (layer "F.SilkS") (effects (font (size 2 2) (thickness 0.4))))\n'
    '\t(net 0 "")\n'
    ")\n"
)


def _pair(tmp_path, pro_data: dict):
    """Write a .kicad_pro + a sibling .kicad_pcb with the same stem. Returns the pro path."""
    pro = tmp_path / "Board.kicad_pro"
    pro.write_text(json.dumps(pro_data, indent=2), encoding="utf-8")
    (tmp_path / "Board.kicad_pcb").write_text(_PCB, encoding="utf-8")
    return pro


# ─────────────────────────────────────────────────────────────────────────────
# 1. Solder-mask / paste round-trip against the sibling .kicad_pcb
# ─────────────────────────────────────────────────────────────────────────────
def test_solder_globals_land_in_kicad_pcb_not_pro(tmp_path):
    pro = _pair(tmp_path, {"net_settings": {"classes": [{"name": "Default"}]}})
    m = PSM.ProjectSettingsManager()
    m.settings.solder_mask_clearance = 4.0    # mils -> 0.1016 mm
    m.settings.solder_paste_margin = -3.0     # mils -> -0.0762 mm
    assert m.save_to_project(pro, backup=False)

    # Landed in the .kicad_pcb (setup ...) on the REAL keys.
    board_text = (tmp_path / "Board.kicad_pcb").read_text(encoding="utf-8")
    vals = BS.get_board_setup(board_text, include_aliases=False)
    assert vals["pad_to_mask_clearance"] == pytest.approx(PSM.mils_to_mm(4.0), abs=1e-4)
    assert vals["pad_to_paste_clearance"] == pytest.approx(PSM.mils_to_mm(-3.0), abs=1e-4)

    # NOT written to the dead .kicad_pro design_settings keys any more.
    design = json.loads(pro.read_text(encoding="utf-8")).get("board", {}).get("design_settings", {})
    assert "solder_mask_clearance" not in design
    assert "solder_paste_margin" not in design

    # save reported the board write, and verify confirms it against the board.
    assert m.last_board_globals["ok"] and m.last_board_globals["wrote"]
    ok, mism = m._verify_saved(pro)
    assert ok, mism


def test_solder_globals_reload_returns_saved_value(tmp_path):
    pro = _pair(tmp_path, {})
    m = PSM.ProjectSettingsManager()
    m.settings.solder_mask_clearance = 5.0
    m.settings.solder_paste_margin = -4.0
    assert m.save_to_project(pro, backup=False)

    # A fresh manager reading the project gets the values back FROM the board.
    m2 = PSM.ProjectSettingsManager()
    assert m2.load_from_project(pro)
    assert m2.settings.solder_mask_clearance == pytest.approx(5.0, abs=0.1)
    assert m2.settings.solder_paste_margin == pytest.approx(-4.0, abs=0.1)


def test_solder_globals_noop_when_no_board(tmp_path):
    # A schematic-only project (no .kicad_pcb) is a benign no-op, and verify passes.
    pro = tmp_path / "SchOnly.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [{"name": "Default"}]}}), encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    m.settings.solder_mask_clearance = 3.0
    assert m.save_to_project(pro, backup=False)
    assert m.last_board_globals == {"ok": True, "wrote": False, "board": None, "error": None}
    ok, mism = m._verify_saved(pro)
    assert ok, mism


def test_solder_globals_untouched_value_does_not_drift(tmp_path):
    # Review regression lock (correctness #1): a board carrying KiCad's own default
    # solder values (0.05 / -0.05 mm, which do NOT sit on the 0.1-mil grid) must be
    # left BYTE-EXACT by an untouched load+save — never rewritten to 0.0508/-0.0508.
    board = tmp_path / "Board.kicad_pcb"
    seeded = BS.set_board_setup(_PCB, {"pad_to_mask_clearance": 0.05,
                                       "pad_to_paste_clearance": -0.05})
    board.write_text(seeded, encoding="utf-8")
    pro = tmp_path / "Board.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [{"name": "Default"}]}}), encoding="utf-8")

    m = PSM.ProjectSettingsManager()
    assert m.load_from_project(pro)          # reads 0.05/-0.05 -> 2.0/-2.0 mils
    assert m.save_to_project(pro, backup=False)   # no user edit
    # No write happened (already matches at mils resolution) and the file is byte-identical.
    assert m.last_board_globals["wrote"] is False
    assert board.read_text(encoding="utf-8") == seeded
    # And it still verifies.
    ok, mism = m._verify_saved(pro)
    assert ok, mism


def test_solder_globals_verify_catches_board_drift(tmp_path):
    pro = _pair(tmp_path, {})
    m = PSM.ProjectSettingsManager()
    m.settings.solder_mask_clearance = 6.0
    assert m.save_to_project(pro, backup=False)
    ok, _ = m._verify_saved(pro)
    assert ok
    # Corrupt the landed value in the board -> verify must fail and name the board key.
    board = tmp_path / "Board.kicad_pcb"
    board.write_text(BS.set_board_setup(board.read_text(encoding="utf-8"),
                                        {"pad_to_mask_clearance": 0.999}), encoding="utf-8")
    ok2, mism2 = m._verify_saved(pro)
    assert not ok2
    assert any("pad_to_mask_clearance" in s for s in mism2)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Every DRC/ERC rule id round-trips, including a custom one
# ─────────────────────────────────────────────────────────────────────────────
def test_custom_drc_severity_roundtrips(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    # A rule id NOT in the curated DRC_RULE_IDS list, with a user-set severity.
    custom = "my_house_rule_xyz"
    assert custom not in PSM.DRC_RULE_IDS
    pro.write_text(json.dumps({"board": {"design_settings": {"rule_severities": {
        custom: "warning", "clearance": "error"}}}}), encoding="utf-8")

    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    # Load captured the custom id into the managed set (not just curated).
    assert m.drc_severities[custom] == "warning"
    assert m.drc_severities["clearance"] == "error"

    # It round-trips through a save + verify against the real key.
    assert m.save_extended(pro)
    rs = json.loads(pro.read_text(encoding="utf-8"))["board"]["design_settings"]["rule_severities"]
    assert rs[custom] == "warning"
    ok, mism = m.verify_extended(pro)
    assert ok, mism


def test_custom_erc_severity_roundtrips(tmp_path):
    pro = tmp_path / "B.kicad_pro"
    custom = "future_erc_check"
    assert custom not in PSM.ERC_RULE_IDS
    pro.write_text(json.dumps({"erc": {"rule_severities": {custom: "ignore"}}}), encoding="utf-8")
    m = PSM.ProjectSettingsManager()
    assert m.load_extended(pro)
    assert m.erc_severities[custom] == "ignore"
    assert m.save_extended(pro)
    ers = json.loads(pro.read_text(encoding="utf-8"))["erc"]["rule_severities"]
    assert ers[custom] == "ignore"


# ─────────────────────────────────────────────────────────────────────────────
# 3. A genuine 0.2/0.25 diff-pair class is preserved (not treated as a sentinel)
# ─────────────────────────────────────────────────────────────────────────────
def test_genuine_diff_pair_survives_project_roundtrip(tmp_path):
    pro = tmp_path / "N.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [
        {"name": "Default"},
        {"name": "USB", "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
         "clearance": 0.15, "track_width": 0.2},
    ]}}), encoding="utf-8")

    m = NCM.NetClassManager()
    assert m.load_from_project(pro)
    usb = m.get_netclass("USB")
    assert usb.has_diff_pair is True
    assert usb.diff_pair_width == pytest.approx(0.2)
    assert usb.diff_pair_gap == pytest.approx(0.25)

    # Save it back out and re-read: the pair must still be exactly 0.2/0.25.
    assert m.save_to_project(pro, backup=False)
    classes = json.loads(pro.read_text(encoding="utf-8"))["net_settings"]["classes"]
    usb_dict = next(c for c in classes if c["name"] == "USB")
    assert usb_dict["diff_pair_width"] == pytest.approx(0.2)
    assert usb_dict["diff_pair_gap"] == pytest.approx(0.25)

    m2 = NCM.NetClassManager()
    assert m2.load_from_project(pro)
    assert m2.get_netclass("USB").diff_pair_width == pytest.approx(0.2)


def test_absent_diff_pair_stays_absent(tmp_path):
    # A class with no diff-pair keys must not resurrect one on round-trip.
    pro = tmp_path / "N.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [
        {"name": "Default"},
        {"name": "GND", "clearance": 0.2, "track_width": 0.3},
    ]}}), encoding="utf-8")
    m = NCM.NetClassManager()
    assert m.load_from_project(pro)
    gnd = m.get_netclass("GND")
    assert gnd.has_diff_pair is False and gnd.diff_pair_width is None
    assert m.save_to_project(pro, backup=False)
    classes = json.loads(pro.read_text(encoding="utf-8"))["net_settings"]["classes"]
    gnd_dict = next(c for c in classes if c["name"] == "GND")
    assert "diff_pair_width" not in gnd_dict
    assert "diff_pair_gap" not in gnd_dict


def test_gap_only_class_is_consistent_no_phantom():
    # Review regression lock (correctness #2): a class with a diff_pair_gap but NO
    # (positive) diff_pair_width is not a usable pair. Load must normalise it to
    # 'no diff pair' (has_diff_pair False, both None) so load and the width-driven
    # write rule AGREE — no has_diff_pair=True object whose gap the writer silently
    # drops on the next save.
    for data in ({"diff_pair_gap": 0.25}, {"diff_pair_width": 0.0, "diff_pair_gap": 0.25}):
        nc = NCM.NetClass.from_kicad_dict("X", data)
        assert nc.has_diff_pair is False
        assert nc.diff_pair_width is None
        assert nc.diff_pair_gap is None
        d = nc.to_kicad_dict()
        assert "diff_pair_width" not in d and "diff_pair_gap" not in d


# ─────────────────────────────────────────────────────────────────────────────
# 4. Corrupt .kicad_pcb -> structured error, no crash; schematic-side still saves
# ─────────────────────────────────────────────────────────────────────────────
_CORRUPT_PCB = "(kicad_pcb\n\t(setup\n\t\t(pad_to_mask_clearance 0.05)\n"  # truncated: never closed


def test_scan_sexpr_diagnostic_offset_and_snippet():
    with pytest.raises(ValueError) as ei:
        BS._scan_sexpr(_CORRUPT_PCB, 0)
    msg = str(ei.value)
    assert "offset 0" in msg
    assert "kicad_pcb" in msg  # snippet includes context


def test_validate_kicad_pcb_rejects_corrupt_and_wrong_head():
    ok, err = BS.validate_kicad_pcb(_CORRUPT_PCB)
    assert not ok and err
    ok2, err2 = BS.validate_kicad_pcb("(kicad_sch (version 1))")
    assert not ok2 and "kicad_pcb" in err2
    ok3, _ = BS.validate_kicad_pcb(_PCB)
    assert ok3


def test_safe_wrappers_return_structured_error_not_raise():
    got = BS.get_board_setup_safe(_CORRUPT_PCB)
    assert got["ok"] is False and got["error"]
    setr = BS.set_board_setup_safe(_CORRUPT_PCB, {"solder_mask_clearance": 0.05})
    assert setr["ok"] is False and setr["error"]
    # And the happy path still returns the value / new text.
    assert BS.get_board_setup_safe(_PCB)["ok"] is True
    assert "pad_to_paste_clearance" in BS.set_board_setup_safe(_PCB, {"solder_paste_margin": -0.05})["text"]


def test_corrupt_board_still_lets_pro_settings_save(tmp_path):
    pro = tmp_path / "Board.kicad_pro"
    pro.write_text(json.dumps({"net_settings": {"classes": [{"name": "Default"}]}}), encoding="utf-8")
    (tmp_path / "Board.kicad_pcb").write_text(_CORRUPT_PCB, encoding="utf-8")

    m = PSM.ProjectSettingsManager()
    m.settings.schematic_text_size = 55.0
    m.settings.solder_mask_clearance = 4.0
    # The .kicad_pro (schematic-side) save must SUCCEED despite the corrupt board.
    assert m.save_to_project(pro, backup=False)
    sch = json.loads(pro.read_text(encoding="utf-8"))["schematic"]["drawing"]
    assert sch["default_text_size"] == 55.0
    # But the board write is honestly reported as failed with a structured error.
    assert m.last_board_globals["ok"] is False
    assert m.last_board_globals["error"]
    # And verify surfaces the board problem (no silent 'verified').
    ok, mism = m._verify_saved(pro)
    assert not ok
    assert any("board globals" in s for s in mism)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Object-conform idempotency: second pass is byte-identical + zero count
# ─────────────────────────────────────────────────────────────────────────────
def test_object_conform_second_pass_is_noop(tmp_path):
    pcb = tmp_path / "C.kicad_pcb"
    pcb.write_text(_PCB, encoding="utf-8")
    ts = "20260712-000000"
    targets = {"silk": (1.0, 0.15)}

    # First pass changes both silk objects (fp_text + gr_text).
    rep1 = OC.conform_project([pcb], targets, {}, ts, dry_run=False)
    assert rep1["written"] is True
    assert rep1["total"] == 2
    after_first = pcb.read_text(encoding="utf-8")

    # Second pass: byte-identical output, ZERO changes reported (true no-op).
    rep2 = OC.conform_project([pcb], targets, {}, ts, dry_run=False)
    assert rep2["total"] == 0
    assert rep2["written"] is False
    assert pcb.read_text(encoding="utf-8") == after_first

    # A dry-run over the already-conformed file also reports zero.
    rep3 = OC.conform_project([pcb], targets, {}, ts, dry_run=True)
    assert rep3["total"] == 0


def test_set_font_reports_unchanged_when_already_at_target():
    block = '(effects (font (size 1 1) (thickness 0.15)))'
    new_block, changed = OC._set_font(block, 1.0, 0.15)
    assert changed is False
    assert new_block == block
    # A genuine change is still reported.
    new_block2, changed2 = OC._set_font(block, 2.0, 0.15)
    assert changed2 is True
    assert "(size 2 2)" in new_block2
