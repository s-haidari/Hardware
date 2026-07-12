"""Procurement / order export: turn a priced-or-enriched BOM into a distributor cart
upload CSV. A cart orders by part number, so only lines that carry an MPN are exported
(bare passives grouped by value alone are counted as skipped). Columns match Mouser's
BOM upload so the file drops straight into the cart.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

_HEAD = "Mouser Part Number,Manufacturer Part Number,Quantity,Customer Reference"


def _rows():
    return [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "mouser_pn": "595-TPS2121RUXR"},
        {"refs": ["R1", "R2", "R3"], "qty": 3, "mpn": "", "value": "10k"},  # passive, no MPN
        {"refs": ["C2", "C1"], "qty": 2, "mpn": "GRM188R71C104KA01D"},      # no Mouser P/N
    ]


def test_cart_csv_includes_only_mpn_lines():
    out = L.procurement_cart_csv(_rows())
    assert out["line_count"] == 2
    assert out["skipped_no_mpn"] == 1
    assert out["total_qty"] == 3                             # 1 + 2 (passive excluded)
    lines = out["csv"].strip().splitlines()
    assert lines[0] == _HEAD
    # a Mouser-sourced line carries its Mouser P/N + refdes
    assert "595-TPS2121RUXR,TPS2121RUXR,1,U1" in out["csv"]
    # no Mouser P/N -> blank first column; Mouser resolves it from the MPN. Refs sorted.
    assert ",GRM188R71C104KA01D,2,C1 C2" in out["csv"]
    # the bare passive never appears in an order file
    assert "10k" not in out["csv"]


def test_cart_csv_consolidated_rows_use_total_qty_and_all_refs():
    rows = [{"mpn": "STM32F407VGT6", "total_qty": 2,
             "refs_by_board": {"Parent": ["U1"], "Card": ["U5"]}}]
    out = L.procurement_cart_csv(rows)
    assert out["total_qty"] == 2
    assert ",STM32F407VGT6,2,U1 U5" in out["csv"]


def test_cart_csv_empty_when_no_purchasable_parts():
    out = L.procurement_cart_csv([{"mpn": "", "qty": 5, "value": "1k"}])
    assert out["line_count"] == 0 and out["skipped_no_mpn"] == 1
    assert out["total_qty"] == 0
    assert out["csv"].strip().splitlines() == [_HEAD]


def test_cart_csv_scales_quantities_to_the_board_count():
    # Ordering a run of N boards multiplies every line's quantity by N; the refdes
    # reference stays per-board (it names one board's placements).
    out = L.procurement_cart_csv(_rows(), boards=10)
    assert out["boards"] == 10
    assert out["total_qty"] == 30                            # (1 + 2) * 10
    assert "595-TPS2121RUXR,TPS2121RUXR,10,U1" in out["csv"]  # 1/board * 10
    assert ",GRM188R71C104KA01D,20,C1 C2" in out["csv"]       # 2/board * 10, refs unchanged


def test_cart_csv_board_count_below_one_is_treated_as_one():
    for bad in (0, -4, None, "x"):
        out = L.procurement_cart_csv(_rows(), boards=bad)
        assert out["boards"] == 1 and out["total_qty"] == 3


# ── Assembly attrition / spares buffer ─────────────────────────────────────────
def test_cart_csv_no_spares_by_default():
    """No spares_pct -> quantities are the plain run total, nothing padded (compat)."""
    out = L.procurement_cart_csv(_rows(), boards=10)
    assert out["spares_pct"] == 0
    assert out["padded_lines"] == 0
    assert out["total_qty"] == 30


def test_cart_csv_spares_pad_only_passives_and_round_up():
    """A spares buffer pads the small SMT passives (R/C/L/FB) that suffer pick-and-place
    attrition, rounded UP, and leaves ICs/connectors at the exact run quantity."""
    out = L.procurement_cart_csv(_rows(), boards=25, spares_pct=5)
    assert out["spares_pct"] == 5
    assert out["padded_lines"] == 1                          # only the C1/C2 capacitor line
    # C row: 2/board * 25 = 50, +5% = 52.5 -> ceil 53
    assert ",GRM188R71C104KA01D,53,C1 C2" in out["csv"]
    # U1 is an IC — NOT padded, exactly 1/board * 25 = 25
    assert "595-TPS2121RUXR,TPS2121RUXR,25,U1" in out["csv"]


def test_cart_csv_spares_total_counts_the_extra():
    out = L.procurement_cart_csv(_rows(), boards=25, spares_pct=5)
    assert out["total_qty"] == 25 + 53                       # IC 25 + padded cap 53


def test_cart_csv_spares_ignores_bad_or_negative_pct():
    for bad in (-5, None, "x"):
        out = L.procurement_cart_csv(_rows(), boards=10, spares_pct=bad)
        assert out["spares_pct"] == 0 and out["padded_lines"] == 0
        assert out["total_qty"] == 30


# ── JLCPCB assembly BOM ────────────────────────────────────────────────────────
_JLC_HEAD = "Comment,Designator,Footprint,LCSC Part #"


def test_jlcpcb_bom_csv_matches_assembly_columns():
    rows = [
        {"refs": ["U1"], "qty": 1, "mpn": "TPS2121RUXR", "value": "TPS2121",
         "footprint": "Package_SO:SOIC-8", "lcsc_pn": "C2913174"},
        # a bare passive: JLCPCB assembles it by value even with no MPN, LCSC # blank
        {"refs": ["R3", "R1", "R2"], "qty": 3, "mpn": "", "value": "10k",
         "footprint": "Resistor_SMD:R_0402_1005Metric"},
    ]
    out = L.jlcpcb_bom_csv(rows)
    assert out["line_count"] == 2
    assert out["with_lcsc"] == 1 and out["without_lcsc"] == 1
    assert out["total_qty"] == 4
    lines = out["csv"].strip().splitlines()
    assert lines[0] == _JLC_HEAD
    # Comment is the value; designators comma-joined + naturally sorted; LCSC # threaded.
    assert 'TPS2121,U1,Package_SO:SOIC-8,C2913174' in out["csv"]
    assert '10k,"R1,R2,R3",Resistor_SMD:R_0402_1005Metric,' in out["csv"]


def test_jlcpcb_bom_csv_comment_falls_back_to_mpn_and_skips_empty():
    rows = [
        {"refs": ["U9"], "qty": 1, "mpn": "STM32F407VGT6", "lcsc_pn": "C19399"},  # no value
        {"refs": ["X1"], "qty": 1, "mpn": "", "value": ""},                       # nothing to say
    ]
    out = L.jlcpcb_bom_csv(rows)
    assert out["line_count"] == 1                                   # the empty row is skipped
    assert 'STM32F407VGT6,U9,,C19399' in out["csv"]                 # comment falls back to MPN


def test_jlcpcb_bom_csv_consolidated_rows_use_total_qty_and_all_refs():
    rows = [{"mpn": "STM32F407VGT6", "value": "STM32", "total_qty": 2, "lcsc_pn": "C19399",
             "refs_by_board": {"Parent": ["U1"], "Card": ["U5"]}}]
    out = L.jlcpcb_bom_csv(rows)
    assert out["total_qty"] == 2
    assert 'STM32,"U1,U5",,C19399' in out["csv"]


def test_jlcpcb_bom_csv_empty_when_no_placeable_parts():
    out = L.jlcpcb_bom_csv([{"refs": ["X1"], "mpn": "", "value": ""}])
    assert out["line_count"] == 0 and out["total_qty"] == 0
    assert out["csv"].strip().splitlines() == [_JLC_HEAD]
