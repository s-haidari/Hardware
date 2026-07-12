"""Regression tests for tools/nd_board_setup.py.

nd_board_setup edits the (setup ...) block of a .kicad_pcb S-expression — the
place KiCad ACTUALLY stores board-wide solder-mask / solder-paste globals (the
audit flagged these being written to .kicad_pro, where KiCad ignores them).

Covered:
- get/round-trip of a realistic (setup ...) block (tabs, stackup, pcbplotparams).
- in-place update of an existing key preserves all other content & formatting.
- insertion of an absent key into an existing (setup ...) block.
- creation of a minimal (setup ...) block when none exists.
- string-aware paren scanning (a quoted value containing parens must not break).
- alias mapping: solder_paste_margin -> pad_to_paste_clearance (NO dead keys).
- coord keys (grid_origin / aux_axis_origin) and the yes/no bool key.
- number formatting (int, negative, ratio) matches KiCad style.
- get(aliases) -> set round-trips to a SINGLE node (no duplicates).
- BoardSetupManager / load_ / save_ Path helpers + atomic write + .bak.
- optional cross-check against a real KiCad demo board when installed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_board_setup as BS  # noqa: E402


# A realistic (setup ...) block, tab-indented like KiCad, including a stackup,
# a pcbplotparams sub-list, and — importantly — a quoted string that contains
# parentheses, to exercise the escape/string-aware paren scanner.
SAMPLE_PCB = (
    "(kicad_pcb\n"
    "\t(version 20241229)\n"
    '\t(generator "pcbnew")\n'
    '\t(generator_version "9.0")\n'
    "\t(general\n"
    "\t\t(thickness 1.6)\n"
    "\t)\n"
    "\t(paper \"A4\")\n"
    "\t(layers\n"
    '\t\t(0 "F.Cu" signal)\n'
    '\t\t(31 "B.Cu" signal)\n'
    "\t)\n"
    "\t(setup\n"
    "\t\t(stackup\n"
    '\t\t\t(layer "F.Mask"\n'
    '\t\t\t\t(type "Top Solder Mask")\n'
    "\t\t\t\t(thickness 0.01)\n"
    "\t\t\t)\n"
    '\t\t\t(copper_finish "ENIG")\n'
    "\t\t)\n"
    "\t\t(pad_to_mask_clearance 0.05)\n"
    "\t\t(allow_soldermask_bridges_in_footprints no)\n"
    "\t\t(aux_axis_origin 74.93 140.97)\n"
    "\t\t(pcbplotparams\n"
    "\t\t\t(mode 1)\n"
    '\t\t\t(outputdirectory "plots (final)/")\n'
    "\t\t)\n"
    "\t)\n"
    '\t(net 0 "")\n'
    ")\n"
)

# A board with NO (setup ...) block.
NO_SETUP_PCB = (
    "(kicad_pcb\n"
    "\t(version 20241229)\n"
    '\t(generator "pcbnew")\n'
    "\t(general\n"
    "\t\t(thickness 1.6)\n"
    "\t)\n"
    "\t(layers\n"
    '\t\t(0 "F.Cu" signal)\n'
    "\t)\n"
    '\t(net 0 "")\n'
    ")\n"
)


# ─────────────────────────────────────────────────────────────────────
# GET
# ─────────────────────────────────────────────────────────────────────
def test_has_setup_block():
    assert BS.has_setup_block(SAMPLE_PCB) is True
    assert BS.has_setup_block(NO_SETUP_PCB) is False


def test_get_reads_real_keys():
    d = BS.get_board_setup(SAMPLE_PCB)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.05)
    assert d["allow_soldermask_bridges_in_footprints"] is False
    assert d["aux_axis_origin"] == pytest.approx((74.93, 140.97))


def test_get_adds_aliases():
    d = BS.get_board_setup(SAMPLE_PCB, include_aliases=True)
    # pad_to_mask_clearance mirrors to the friendly solder_mask_clearance name
    assert d["solder_mask_clearance"] == pytest.approx(0.05)
    # without aliases the friendly name is absent
    d2 = BS.get_board_setup(SAMPLE_PCB, include_aliases=False)
    assert "solder_mask_clearance" not in d2
    assert "pad_to_mask_clearance" in d2


def test_get_empty_when_no_setup():
    assert BS.get_board_setup(NO_SETUP_PCB) == {}


def test_string_with_parens_does_not_break_scan():
    # The quoted 'plots (final)/' contains parens; if the scanner were not
    # string-aware, paren matching would desync and this would raise/mis-read.
    d = BS.get_board_setup(SAMPLE_PCB)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.05)
    # And the paren-containing string survives an unrelated edit untouched.
    out = BS.set_board_setup(SAMPLE_PCB, {"solder_mask_min_width": 0.1})
    assert '(outputdirectory "plots (final)/")' in out


# ─────────────────────────────────────────────────────────────────────
# SET — update existing
# ─────────────────────────────────────────────────────────────────────
def test_update_existing_key_in_place():
    out = BS.set_board_setup(SAMPLE_PCB, {"pad_to_mask_clearance": 0.2})
    d = BS.get_board_setup(out)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.2)
    assert out.count("(pad_to_mask_clearance") == 1  # replaced, not duplicated
    # Everything else preserved.
    assert "(stackup" in out
    assert '(copper_finish "ENIG")' in out
    assert "(allow_soldermask_bridges_in_footprints no)" in out
    assert out.count("(setup") == 1


def test_update_preserves_unrelated_bytes():
    out = BS.set_board_setup(SAMPLE_PCB, {"pad_to_mask_clearance": 0.2})
    # The only textual difference should be within the pad_to_mask_clearance node.
    before = SAMPLE_PCB.replace("(pad_to_mask_clearance 0.05)",
                                "(pad_to_mask_clearance 0.2)")
    assert out == before


# ─────────────────────────────────────────────────────────────────────
# SET — insert absent
# ─────────────────────────────────────────────────────────────────────
def test_insert_absent_key():
    out = BS.set_board_setup(SAMPLE_PCB, {"solder_mask_min_width": 0.25})
    d = BS.get_board_setup(out)
    assert d["solder_mask_min_width"] == pytest.approx(0.25)
    # inserted inside the setup block, tab-indented at child depth
    assert "\t\t(solder_mask_min_width 0.25)\n" in out
    # existing content still intact
    assert d["pad_to_mask_clearance"] == pytest.approx(0.05)
    assert out.count("(setup") == 1
    # still valid: the setup block still parses and closes
    assert BS.has_setup_block(out)


def test_insert_coord_and_bool():
    out = BS.set_board_setup(SAMPLE_PCB, {
        "grid_origin": (10.0, 20.0),
    })
    d = BS.get_board_setup(out)
    assert d["grid_origin"] == pytest.approx((10.0, 20.0))
    assert "(grid_origin 10 20)" in out


# ─────────────────────────────────────────────────────────────────────
# ALIASES — the audit fix: friendly names must map to REAL keys, no dead keys
# ─────────────────────────────────────────────────────────────────────
def test_alias_writes_real_key_not_dead_key():
    out = BS.set_board_setup(SAMPLE_PCB, {
        "solder_paste_margin": -0.05,
        "solder_paste_margin_ratio": -0.1,
        "solder_mask_clearance": 0.03,
    })
    # Real keys land in the file...
    assert "(pad_to_paste_clearance -0.05)" in out
    assert "(pad_to_paste_clearance_ratio -0.1)" in out
    assert "(pad_to_mask_clearance 0.03)" in out
    # ...and the pad/footprint-level names are NEVER written as setup keys.
    setup_open, setup_end = BS._find_setup(out)
    setup_text = out[setup_open:setup_end]
    assert "(solder_paste_margin " not in setup_text
    assert "(solder_paste_margin_ratio " not in setup_text
    assert "(solder_mask_clearance " not in setup_text


def test_resolve_key():
    assert BS.resolve_key("solder_paste_margin") == "pad_to_paste_clearance"
    assert BS.resolve_key("pad_to_mask_clearance") == "pad_to_mask_clearance"
    assert BS.resolve_key("bogus_key") is None


def test_unsupported_keys_ignored():
    out = BS.set_board_setup(SAMPLE_PCB, {"totally_made_up": 5})
    assert out == SAMPLE_PCB  # nothing to do -> unchanged


# ─────────────────────────────────────────────────────────────────────
# NO SETUP — create minimal block
# ─────────────────────────────────────────────────────────────────────
def test_create_setup_when_absent():
    out = BS.set_board_setup(NO_SETUP_PCB, {
        "pad_to_mask_clearance": 0.0,
        "solder_mask_min_width": 0.25,
    })
    assert BS.has_setup_block(out)
    d = BS.get_board_setup(out)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.0)
    assert d["solder_mask_min_width"] == pytest.approx(0.25)
    assert out.count("(setup") == 1
    # inserted after the layers block (KiCad's natural order), before nets
    assert out.index("(layers") < out.index("(setup")
    assert out.index("(setup") < out.index("(net 0")


def test_created_block_is_reparseable_roundtrip():
    out = BS.set_board_setup(NO_SETUP_PCB, {"pad_to_mask_clearance": 0.1})
    # a second edit on the freshly created block must update in place
    out2 = BS.set_board_setup(out, {"pad_to_mask_clearance": 0.2})
    assert out2.count("(pad_to_mask_clearance") == 1
    assert BS.get_board_setup(out2)["pad_to_mask_clearance"] == pytest.approx(0.2)


# ─────────────────────────────────────────────────────────────────────
# ROUND TRIP — get(aliases) -> set must not duplicate nodes
# ─────────────────────────────────────────────────────────────────────
def test_alias_roundtrip_no_duplicate():
    d = BS.get_board_setup(SAMPLE_PCB, include_aliases=True)
    # d contains both pad_to_mask_clearance AND solder_mask_clearance (same val)
    assert "solder_mask_clearance" in d and "pad_to_mask_clearance" in d
    out = BS.set_board_setup(SAMPLE_PCB, d)
    # feeding both back in must produce exactly one node for the real key
    assert out.count("(pad_to_mask_clearance") == 1
    assert BS.get_board_setup(out)["pad_to_mask_clearance"] == pytest.approx(0.05)


def test_real_key_overrides_alias_on_conflict():
    # If both alias and real key are supplied, the real key wins.
    out = BS.set_board_setup(SAMPLE_PCB, {
        "solder_mask_clearance": 0.99,        # alias
        "pad_to_mask_clearance": 0.2,         # canonical -> should win
    })
    assert "(pad_to_mask_clearance 0.2)" in out
    assert "0.99" not in out


# ─────────────────────────────────────────────────────────────────────
# NUMBER FORMATTING
# ─────────────────────────────────────────────────────────────────────
def test_number_formatting():
    assert BS._fmt_num(0) == "0"
    assert BS._fmt_num(0.0) == "0"
    assert BS._fmt_num(-0.0) == "0"
    assert BS._fmt_num(138) == "138"
    assert BS._fmt_num(83.5) == "83.5"
    assert BS._fmt_num(-0.05) == "-0.05"
    assert BS._fmt_num(0.100000) == "0.1"


# ─────────────────────────────────────────────────────────────────────
# SINGLE-LINE SETUP
# ─────────────────────────────────────────────────────────────────────
def test_single_line_setup_insert():
    text = '(kicad_pcb (version 1) (setup (pad_to_mask_clearance 0)))'
    out = BS.set_board_setup(text, {"solder_mask_min_width": 0.2})
    assert BS.has_setup_block(out)
    d = BS.get_board_setup(out)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.0)
    assert d["solder_mask_min_width"] == pytest.approx(0.2)


# ─────────────────────────────────────────────────────────────────────
# CRLF handling
# ─────────────────────────────────────────────────────────────────────
def test_crlf_newlines_preserved():
    crlf = SAMPLE_PCB.replace("\n", "\r\n")
    out = BS.set_board_setup(crlf, {"solder_mask_min_width": 0.15})
    assert "\r\n" in out
    # the inserted line uses CRLF, not a bare LF
    assert "\t\t(solder_mask_min_width 0.15)\r\n" in out
    assert BS.get_board_setup(out)["solder_mask_min_width"] == pytest.approx(0.15)


# ─────────────────────────────────────────────────────────────────────
# FILE HELPERS
# ─────────────────────────────────────────────────────────────────────
def test_load_and_save_helpers(tmp_path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(SAMPLE_PCB, encoding="utf-8")
    d = BS.load_board_setup(p)
    assert d["pad_to_mask_clearance"] == pytest.approx(0.05)

    BS.save_board_setup(p, {"solder_paste_margin": -0.05}, backup=True)
    reread = BS.load_board_setup(p)
    assert reread["pad_to_paste_clearance"] == pytest.approx(-0.05)
    # backup of the ORIGINAL exists and still has the old (no paste) content
    bak = tmp_path / "board.kicad_pcb.bak"
    assert bak.exists()
    assert "pad_to_paste_clearance" not in bak.read_text(encoding="utf-8")
    # no temp file left behind
    assert not (tmp_path / "board.kicad_pcb.tmp").exists()


def test_board_setup_manager(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text(SAMPLE_PCB, encoding="utf-8")
    mgr = BS.BoardSetupManager(p)
    cur = mgr.load()
    assert cur["pad_to_mask_clearance"] == pytest.approx(0.05)
    mgr.set({"solder_paste_margin": -0.075})
    # in-memory only until save
    assert "pad_to_paste_clearance" not in p.read_text(encoding="utf-8")
    mgr.save(backup=False)
    assert BS.load_board_setup(p)["pad_to_paste_clearance"] == pytest.approx(-0.075)


# ─────────────────────────────────────────────────────────────────────
# OPTIONAL: cross-check against a real installed KiCad demo board
# ─────────────────────────────────────────────────────────────────────
_DEMO = Path(
    r"C:/Program Files/KiCad/10.0/share/kicad/demos/interf_u/interf_u.kicad_pcb"
)


@pytest.mark.skipif(not _DEMO.exists(), reason="KiCad demo board not installed")
def test_real_demo_board_roundtrip(tmp_path):
    original = _DEMO.read_text(encoding="utf-8")
    d = BS.get_board_setup(original)
    # this demo has (pad_to_mask_clearance 0)
    assert "pad_to_mask_clearance" in d
    # editing it must keep the file parseable and change exactly that node
    out = BS.set_board_setup(original, {"pad_to_mask_clearance": 0.05})
    assert BS.get_board_setup(out)["pad_to_mask_clearance"] == pytest.approx(0.05)
    assert out.count("(setup") == 1
    # stackup + pcbplotparams untouched
    assert "(stackup" in out
    assert "(pcbplotparams" in out
