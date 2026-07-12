"""BOM cost / procurement roll-up: attach unit price + extended (unit x qty) to each
priced line and total the BOM, so the builder is a purchasing tool, not just a parts
list. Pricing is a separate lookup from identity enrichment (the identity lookup is
library-first and never prices a library part), so it's opt-in via price_lookup.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def _sym(ref, value, lib="Device:R", mpn=None, mfr=None, fp=""):
    props = [f'(property "Reference" "{ref}")', f'(property "Value" "{value}")']
    if fp:
        props.append(f'(property "Footprint" "{fp}")')
    if mpn:
        props.append(f'(property "MPN" "{mpn}")')
    if mfr:
        props.append(f'(property "MANUFACTURER" "{mfr}")')
    return f'(symbol (lib_id "{lib}") ' + " ".join(props) + ')'


def _sch(*syms):
    return "(kicad_sch " + " ".join(syms) + ")"


_PRICES = {
    "TPS2121RUXR": {"unit_price": "$1.25", "stock": 5000, "lifecycle": "Active"},
    "STM32F407VGT6": {"unit_price": 8.0, "stock": 0, "lifecycle": "NRND"},
}


def _price_lookup(mpn):
    return _PRICES.get(mpn)


# ── pure helpers ──────────────────────────────────────────────────────────────
def test_coerce_price_handles_strings_numbers_and_junk():
    assert L._coerce_price("$0.10") == 0.10
    assert L._coerce_price("1,250.00") == 1250.0
    assert L._coerce_price(8) == 8.0
    assert L._coerce_price(None) is None
    assert L._coerce_price("call for pricing") is None


def test_line_extended_multiplies_or_none():
    assert L.line_extended("$1.25", 4) == 5.0
    assert L.line_extended(2.0, 3) == 6.0
    assert L.line_extended(None, 3) is None          # no price
    assert L.line_extended(2.0, 0) is None           # no qty
    assert L.line_extended(2.0, "3") == 6.0          # qty as an int-ish string
    assert L.line_extended(2.0, "3.0") == 6.0        # qty as a decimal string (not dropped)


def test_bom_cost_summary_totals_priced_and_counts_unpriced():
    rows = [
        {"qty": 4, "unit_price": "$1.25", "extended": 5.0},
        {"qty": 2, "unit_price": 8.0, "extended": 16.0},
        {"qty": 10, "unit_price": None},             # unpriced passive
    ]
    s = L.bom_cost_summary(rows)
    assert s["total_cost"] == 21.0
    assert s["priced_lines"] == 2 and s["unpriced_lines"] == 1
    assert s["line_count"] == 3 and s["currency"] == "USD"


def test_summary_recomputes_extended_from_unit_and_total_qty():
    # consolidated rows carry total_qty, not qty, and may lack a precomputed extended.
    rows = [{"total_qty": 3, "unit_price": 2.0}]
    assert L.bom_cost_summary(rows)["total_cost"] == 6.0


# ── sourcing risk (lifecycle + stock) ─────────────────────────────────────────
def test_bom_sourcing_risks_flags_lifecycle_and_stock():
    rows = [
        {"mpn": "OK", "qty": 5, "lifecycle": "Active", "stock": 5000},        # healthy
        {"mpn": "OLD", "qty": 2, "lifecycle": "NRND", "stock": 900},          # lifecycle risk
        {"mpn": "GONE", "qty": 1, "lifecycle": "Active", "stock": 0},         # no stock
        {"mpn": "TIGHT", "qty": 100, "lifecycle": "Active", "stock": 40},     # can't cover the build
        {"mpn": "BARE", "qty": 3},                                            # unpriced -> unknown, not a risk
    ]
    r = L.bom_sourcing_risks(rows)
    assert r["not_active"] == 1                              # NRND
    assert r["no_stock"] == 1                                # stock 0
    assert r["insufficient_stock"] == 1                      # 40 < 100
    assert set(r["risky_mpns"]) == {"OLD", "GONE", "TIGHT"}  # BARE (unknown) never flagged
    assert r["any"] is True


def test_bom_sourcing_risks_none_when_all_healthy():
    rows = [{"mpn": "A", "qty": 1, "lifecycle": "Active", "stock": 100},
            {"mpn": "B", "qty": 2}]                          # unknown stock/lifecycle -> not a risk
    r = L.bom_sourcing_risks(rows)
    assert r == {"not_active": 0, "no_stock": 0, "insufficient_stock": 0,
                 "risky_mpns": [], "any": False}


def test_bom_sourcing_risks_uses_total_qty_for_consolidated_rows():
    rows = [{"mpn": "C", "total_qty": 50, "lifecycle": "Active", "stock": 10}]
    r = L.bom_sourcing_risks(rows)
    assert r["insufficient_stock"] == 1 and r["risky_mpns"] == ["C"]


def test_bom_sourcing_risks_scales_stock_coverage_to_the_build_quantity():
    # 2/board with 40 in stock is plenty for a prototype, but a run of 50 needs 100 —
    # the shortfall only exists at the build quantity, so the risk must scale with it.
    rows = [{"mpn": "TIGHT", "qty": 2, "lifecycle": "Active", "stock": 40}]
    assert L.bom_sourcing_risks(rows)["insufficient_stock"] == 0            # 1 board: 2 <= 40
    assert L.bom_sourcing_risks(rows, boards=50)["insufficient_stock"] == 1  # 50 boards: 100 > 40
    assert L.bom_sourcing_risks(rows, boards=50)["risky_mpns"] == ["TIGHT"]
    # A hard zero is out of stock at any run size; a bad board count folds to 1.
    assert L.bom_sourcing_risks([{"mpn": "GONE", "qty": 1, "stock": 0}], boards=99)["no_stock"] == 1
    assert L.bom_sourcing_risks(rows, boards=0)["insufficient_stock"] == 0   # 0 -> 1 board


# ── price-break ladder (volume pricing) ───────────────────────────────────────
def test_parse_mouser_part_captures_full_price_break_ladder():
    part = {
        "ManufacturerPartNumber": "R-10K",
        "PriceBreaks": [
            {"Quantity": 1, "Price": "$0.10", "Currency": "USD"},
            {"Quantity": 10, "Price": "$0.08"},
            {"Quantity": 100, "Price": "$0.05"},
        ],
    }
    norm = L._parse_mouser_part(part)
    assert norm["unit_price"] == "$0.10"                  # qty-1 price unchanged (back-compat)
    assert norm["price_breaks"] == [
        {"qty": 1, "price": 0.10},
        {"qty": 10, "price": 0.08},
        {"qty": 100, "price": 0.05},
    ]


def test_parse_mouser_part_no_breaks_is_empty_ladder():
    norm = L._parse_mouser_part({"ManufacturerPartNumber": "X"})
    assert norm["price_breaks"] == []
    assert norm["unit_price"] is None


def test_parse_mouser_part_skips_unparseable_breaks():
    part = {"PriceBreaks": [{"Quantity": 1, "Price": "Call"},   # unpriceable -> dropped
                            {"Quantity": 25, "Price": "$0.20"}]}
    assert L._parse_mouser_part(part)["price_breaks"] == [{"qty": 25, "price": 0.20}]


def test_price_at_qty_picks_applicable_break():
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 10, "price": 0.08},
              {"qty": 100, "price": 0.05}]
    assert L.price_at_qty(ladder, 1) == 0.10
    assert L.price_at_qty(ladder, 5) == 0.10             # below the 10-qty break
    assert L.price_at_qty(ladder, 10) == 0.08            # exactly on a break
    assert L.price_at_qty(ladder, 50) == 0.08
    assert L.price_at_qty(ladder, 250) == 0.05           # above the top break
    assert L.price_at_qty(ladder, "100") == 0.05         # qty as an int-ish string
    assert L.price_at_qty([], 5) is None                 # no ladder -> unknown
    assert L.price_at_qty(ladder, 0) == 0.10             # below first break floors to it


def test_price_at_qty_tolerates_unsorted_ladder():
    ladder = [{"qty": 100, "price": 0.05}, {"qty": 1, "price": 0.10},
              {"qty": 10, "price": 0.08}]
    assert L.price_at_qty(ladder, 50) == 0.08


def test_price_rows_uses_volume_price_for_line_qty():
    rows = [{"mpn": "R-10K", "qty": 100}]

    def lookup(mpn):
        return {"price_breaks": [{"qty": 1, "price": 0.10},
                                 {"qty": 10, "price": 0.08},
                                 {"qty": 100, "price": 0.05}],
                "unit_price": "$0.10", "stock": 9000, "lifecycle": "Active"}

    L._price_rows(rows, lookup, "qty")
    r = rows[0]
    assert r["unit_price"] == 0.05                        # 100 units -> the 100-qty break
    assert r["extended"] == 5.0                           # 0.05 * 100 (not 0.10 * 100)
    assert r["price_breaks"]                              # ladder retained for display/export


def test_price_rows_falls_back_to_unit_price_without_ladder():
    rows = [{"mpn": "U1", "qty": 3}]
    L._price_rows(rows, lambda m: {"unit_price": 2.0}, "qty")   # no price_breaks
    assert rows[0]["unit_price"] == 2.0 and rows[0]["extended"] == 6.0


def test_price_rows_threads_source_and_distributor_part_numbers():
    # The provider chain tags each hit with the distributor that carried it and its
    # distributor part number(s); pricing must thread those onto the row so the BOM can
    # show WHICH distributor sources each line and export a JLCPCB/Mouser order.
    rows = [{"mpn": "U1", "qty": 1}]
    L._price_rows(rows, lambda m: {"unit_price": 2.0, "source": "LCSC",
                                   "lcsc_pn": "C12345", "mouser_pn": "595-U1"}, "qty")
    assert rows[0]["source"] == "LCSC"
    assert rows[0]["lcsc_pn"] == "C12345"
    assert rows[0]["mouser_pn"] == "595-U1"


def test_price_rows_does_not_clobber_a_source_already_set():
    # The consolidated BOM sets `source` from its identity lookup BEFORE pricing;
    # pricing must not overwrite it (both come from the same chain, but a NOT FOUND /
    # blank must stay the sourced path's call, not be silently changed).
    rows = [{"mpn": "U1", "qty": 1, "source": "Mouser"}]
    L._price_rows(rows, lambda m: {"unit_price": 1.0, "source": "LCSC"}, "qty")
    assert rows[0]["source"] == "Mouser"


# ── cost split by distributor (multi-source order) ────────────────────────────
def test_bom_cost_by_source_splits_priced_lines_and_sums_to_total():
    rows = [
        {"source": "Mouser", "qty": 4, "unit_price": "$1.25"},   # 5.00
        {"source": "Mouser", "qty": 2, "unit_price": 8.0},       # 16.00
        {"source": "LCSC", "qty": 10, "unit_price": 0.10},       # 1.00
        {"source": "", "qty": 3},                                # unpriced -> skipped entirely
    ]
    split = L.bom_cost_by_source(rows)["sources"]
    assert split["Mouser"] == {"total_cost": 21.0, "lines": 2}
    assert split["LCSC"] == {"total_cost": 1.0, "lines": 1}
    assert "Unsourced" not in split                              # the unpriced line never counts
    # the per-source totals sum to the whole-BOM total
    total = sum(s["total_cost"] for s in split.values())
    assert total == L.bom_cost_summary(rows)["total_cost"]


def test_bom_cost_by_source_scales_to_boards_and_labels_blank_source():
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    rows = [{"source": "", "qty": 2, "unit_price": 0.10, "price_breaks": ladder}]  # priced, no source
    split = L.bom_cost_by_source(rows, boards=50)["sources"]
    assert split == {"Unsourced": {"total_cost": 5.0, "lines": 1}}   # 100 @ 0.05, blank -> Unsourced


# ── volume / build-quantity projection ────────────────────────────────────────
def test_bom_cost_at_qty_scales_flat_price_by_boards():
    # No ladder: each line just scales linearly with the board count.
    rows = [{"qty": 4, "unit_price": "$1.25"}, {"qty": 2, "unit_price": 8.0}]
    r = L.bom_cost_at_qty(rows, 10)
    assert r["boards"] == 10
    assert r["total_cost"] == 210.0                      # (4*1.25 + 2*8) * 10
    assert r["priced_lines"] == 2 and r["unpriced_lines"] == 0
    assert r["currency"] == "USD"


def test_bom_cost_at_qty_uses_the_volume_break_at_the_scaled_order_qty():
    # 2/board at 50 boards = 100 ordered -> drops onto the 100-qty break (0.05),
    # not the qty-1 price (0.10). This is the whole point of the projection.
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 10, "price": 0.08},
              {"qty": 100, "price": 0.05}]
    rows = [{"qty": 2, "unit_price": 0.10, "price_breaks": ladder}]
    assert L.bom_cost_at_qty(rows, 1)["total_cost"] == 0.20    # 2 @ 0.10
    assert L.bom_cost_at_qty(rows, 50)["total_cost"] == 5.0    # 100 @ 0.05


def test_bom_cost_at_qty_boards_one_matches_the_base_summary():
    rows = [{"qty": 4, "unit_price": "$1.25"}, {"qty": 2, "unit_price": 8.0}]
    assert L.bom_cost_at_qty(rows, 1)["total_cost"] == L.bom_cost_summary(rows)["total_cost"]


def test_bom_cost_at_qty_counts_unpriced_and_supports_consolidated_rows():
    rows = [{"total_qty": 3, "unit_price": 2.0}, {"total_qty": 5, "unit_price": None}]
    r = L.bom_cost_at_qty(rows, 4)
    assert r["total_cost"] == 24.0                        # 3*2*4; the unpriced line adds nothing
    assert r["priced_lines"] == 1 and r["unpriced_lines"] == 1


def test_bom_cost_at_qty_treats_bad_board_counts_as_one():
    rows = [{"qty": 2, "unit_price": 1.0}]
    for bad in (0, -3, None, "x"):
        r = L.bom_cost_at_qty(rows, bad)
        assert r["boards"] == 1 and r["total_cost"] == 2.0


def test_bom_cost_at_qty_does_not_mutate_rows():
    rows = [{"qty": 2, "unit_price": 1.0}]
    before = dict(rows[0])
    L.bom_cost_at_qty(rows, 100)
    assert rows[0] == before


# ── shared per-row costing (bom_cost_at_qty ↔ priced_bom_csv_at_qty must not drift) ──
def test_row_cost_at_qty_scales_and_reprices_at_the_volume_break():
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    r = {"qty": 2, "unit_price": 0.10, "price_breaks": ladder}
    assert L._row_cost_at_qty(r, 1) == (2, 0.10, 0.20)       # 2 ordered @ qty-1 price
    assert L._row_cost_at_qty(r, 50) == (100, 0.05, 5.0)     # 100 ordered -> the 0.05 break


def test_row_cost_at_qty_flat_price_and_unpriced_and_consolidated():
    assert L._row_cost_at_qty({"qty": 4, "unit_price": "$1.25"}, 10) == (40, 1.25, 50.0)
    assert L._row_cost_at_qty({"qty": 3}, 5) == (15, None, None)        # no price -> unpriced
    assert L._row_cost_at_qty({"total_qty": 3, "unit_price": 2.0}, 4) == (12, 2.0, 24.0)


def test_board_count_folds_bad_values_to_one():
    for bad in (0, -3, None, "x"):
        assert L._board_count(bad) == 1
    assert L._board_count("25") == 25 and L._board_count(7) == 7


# ── priced BOM CSV projected to N boards ──────────────────────────────────────
def test_priced_bom_csv_at_qty_scales_qty_and_reprices_each_line():
    ladder = [{"qty": 1, "price": 0.10}, {"qty": 100, "price": 0.05}]
    rows = [{"mpn": "R-10K", "manufacturer": "Yageo", "value": "10k", "footprint": "0402",
             "qty": 2, "unit_price": 0.10, "price_breaks": ladder, "source": "Mouser",
             "mouser_pn": "603-R10K", "stock": 9000, "lifecycle": "Active"}]
    out = L.priced_bom_csv_at_qty(rows, 50)
    assert out["boards"] == 50
    assert out["total_cost"] == 5.0                          # 100 @ 0.05
    assert out["priced_lines"] == 1 and out["unpriced_lines"] == 0
    lines = out["csv"].splitlines()
    head = lines[0].split(",")
    assert head[:6] == ["MPN", "Manufacturer", "Value", "Footprint", "Per-Board Qty", "Order Qty"]
    assert "Order Qty" in head and "Ext Price" in head and "Dist P/N" in head
    cells = lines[1].split(",")
    assert cells[0] == "R-10K" and cells[4] == "2" and cells[5] == "100"   # per-board 2, order 100
    assert "603-R10K" in lines[1]                            # dist P/N, matched to Source
    assert "0.0500" in lines[1] and "5.0000" in lines[1]     # volume unit + ext at N


def test_priced_bom_csv_at_qty_carries_normalized_lead_time():
    # The purchasing sheet flags each line's lead in whole weeks so the critical path is
    # visible per line; providers' shapes ("16 Weeks" / numeric) normalize, absent -> blank.
    rows = [{"mpn": "SLOW", "qty": 1, "unit_price": 8.0, "lead_time": "16 Weeks"},
            {"mpn": "FAST", "qty": 1, "unit_price": 1.0, "lead_time": 4},
            {"mpn": "NOLEAD", "qty": 1, "unit_price": 0.5}]
    lines = L.priced_bom_csv_at_qty(rows, 1)["csv"].splitlines()
    head = lines[0].split(",")
    assert "Lead (wks)" in head
    lc = head.index("Lead (wks)")
    by_mpn = {ln.split(",")[0]: ln.split(",") for ln in lines[1:]}
    assert by_mpn["SLOW"][lc] == "16"
    assert by_mpn["FAST"][lc] == "4"
    assert by_mpn["NOLEAD"][lc] == ""                        # unknown -> blank, not 0


def test_priced_bom_csv_at_qty_ranks_cost_drivers_first():
    # A purchasing sheet: the biggest spend leads so cost drivers are obvious, regardless
    # of BOM order. Unpriced lines sort last (no cost to rank).
    rows = [
        {"mpn": "CHEAP", "qty": 10, "unit_price": 0.10},     # ext 1.00
        {"mpn": "DRIVER", "qty": 2, "unit_price": 8.0},      # ext 16.00 -> should lead
        {"mpn": "BARE", "qty": 5},                           # unpriced -> last
        {"mpn": "MID", "qty": 3, "unit_price": 1.0},         # ext 3.00
    ]
    body = L.priced_bom_csv_at_qty(rows, 1)["csv"].splitlines()[1:]
    order = [line.split(",")[0] for line in body]
    assert order == ["DRIVER", "MID", "CHEAP", "BARE"]       # by ext desc, unpriced last


def test_priced_bom_csv_at_qty_boards_one_matches_the_base_total():
    rows = [{"mpn": "A", "qty": 4, "unit_price": "$1.25"},
            {"mpn": "B", "qty": 2, "unit_price": 8.0}]
    out = L.priced_bom_csv_at_qty(rows, 1)
    assert out["total_cost"] == L.bom_cost_summary(rows)["total_cost"]   # 5 + 16 = 21
    assert out["line_count"] == 2


def test_priced_bom_csv_at_qty_counts_unpriced_and_uses_total_qty():
    # A consolidated row (total_qty) that is priced, plus a bare passive that is not.
    rows = [{"mpn": "C", "total_qty": 3, "unit_price": 2.0}, {"value": "10k", "total_qty": 5}]
    out = L.priced_bom_csv_at_qty(rows, 4)
    assert out["total_cost"] == 24.0 and out["priced_lines"] == 1 and out["unpriced_lines"] == 1
    passive = out["csv"].splitlines()[2].split(",")
    assert passive[5] == "20"                                 # order qty still scales (5 * 4)
    assert passive[9] == ""                                   # Ext Price blank when unpriced


def test_priced_bom_csv_at_qty_bad_board_count_is_one_and_never_mutates():
    rows = [{"mpn": "A", "qty": 2, "unit_price": 1.0}]
    before = dict(rows[0])
    out = L.priced_bom_csv_at_qty(rows, 0)
    assert out["boards"] == 1 and out["total_cost"] == 2.0
    assert rows[0] == before                                  # pure: the projection never writes back


def test_dist_pn_matches_the_rows_source():
    # The exported BOM carries ONE distributor part number per line — the P/N for the
    # distributor that actually sourced it, so purchasing orders "from Source by Dist P/N".
    assert L._dist_pn({"source": "LCSC", "lcsc_pn": "C12345", "mouser_pn": "595-U1"}) == "C12345"
    assert L._dist_pn({"source": "Mouser", "lcsc_pn": "C12345", "mouser_pn": "595-U1"}) == "595-U1"
    # Source unknown / no matching P/N -> fall back to whichever P/N is present.
    assert L._dist_pn({"source": "", "mouser_pn": "595-U1"}) == "595-U1"
    assert L._dist_pn({"source": "LCSC", "mouser_pn": "595-U1"}) == "595-U1"  # no lcsc_pn -> fall back
    assert L._dist_pn({"mpn": "U1"}) == ""                      # nothing threaded -> blank


# ── builder integration ───────────────────────────────────────────────────────
def test_project_bom_prices_lines_with_mpn_only(tmp_path):
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("R1", "10k"), _sym("R2", "10k"),
                      _sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))
    bom = L.bom_from_kicad_schematic(str(f), price_lookup=_price_lookup)
    by_ref = {r["refs"][0]: r for r in bom["rows"]}
    u1 = by_ref["U1"]
    assert u1["unit_price"] == "$1.25" and u1["extended"] == 1.25   # qty 1
    assert u1["stock"] == 5000 and u1["lifecycle"] == "Active"
    # the bare passive has no MPN -> stays unpriced (never invents a price)
    assert by_ref["R1"].get("unit_price") in (None, "")
    assert bom["cost"]["total_cost"] == 1.25 and bom["cost"]["priced_lines"] == 1


def test_project_bom_priced_csv_carries_source_column(tmp_path):
    # When priced, the project BOM CSV surfaces which distributor sources each line —
    # the consolidated BOM already has a Source column; the project one now matches.
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))

    def priced(mpn):
        r = dict(_PRICES.get(mpn) or {})
        if r:
            r["source"] = "Mouser"
        return r or None

    bom = L.bom_from_kicad_schematic(str(f), price_lookup=priced)
    header = bom["csv"].splitlines()[0]
    assert "Source" in header.split(",")
    u1 = bom["rows"][0]
    assert u1["source"] == "Mouser"
    # the Source cell for U1 is populated in the CSV body
    assert "Mouser" in bom["csv"].splitlines()[1]


def test_project_bom_priced_csv_carries_dist_pn(tmp_path):
    # The priced project CSV surfaces the distributor part number to order by, matched
    # to the line's Source — purchasing can order without re-looking-up every MPN.
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))

    def priced(mpn):
        r = dict(_PRICES.get(mpn) or {})
        if r:
            r.update(source="LCSC", lcsc_pn="C112225")
        return r or None

    bom = L.bom_from_kicad_schematic(str(f), price_lookup=priced)
    header = bom["csv"].splitlines()[0].split(",")
    assert "Dist P/N" in header
    assert "C112225" in bom["csv"].splitlines()[1]


def test_consolidated_bom_priced_csv_carries_dist_pn(tmp_path):
    parent = tmp_path / "p.kicad_sch"
    parent.write_text(_sch(_sym("U1", "STM32", lib="Device:U", mpn="STM32F407VGT6", mfr="ST")))

    def sourced(mpn):
        return {"source": "Mouser"}

    def priced(mpn):
        r = dict(_PRICES.get(mpn) or {})
        if r:
            r.update(source="Mouser", mouser_pn="511-STM32F407VGT6")
        return r or None

    con = L.consolidated_bom({"Parent": [str(parent)]}, lookup=sourced, price_lookup=priced)
    header = con["csv"].splitlines()[0].split(",")
    assert "Dist P/N" in header
    assert "511-STM32F407VGT6" in con["csv"].splitlines()[1]


def test_project_bom_without_price_lookup_is_unchanged(tmp_path):
    f = tmp_path / "s.kicad_sch"
    f.write_text(_sch(_sym("U1", "TPS2121", lib="Device:U", mpn="TPS2121RUXR", mfr="TI")))
    bom = L.bom_from_kicad_schematic(str(f))
    assert "cost" not in bom                           # no pricing requested
    assert bom["rows"][0].get("extended") in (None, "")


def test_consolidated_bom_prices_by_total_qty(tmp_path):
    parent = tmp_path / "p.kicad_sch"
    parent.write_text(_sch(_sym("U1", "STM32", lib="Device:U", mpn="STM32F407VGT6", mfr="ST")))
    card = tmp_path / "c.kicad_sch"
    card.write_text(_sch(_sym("U1", "STM32", lib="Device:U", mpn="STM32F407VGT6", mfr="ST")))
    con = L.consolidated_bom({"Parent": [str(parent)], "Card": [str(card)]},
                             price_lookup=_price_lookup)
    r = con["rows"][0]
    assert r["total_qty"] == 2
    assert r["unit_price"] == 8.0 and r["extended"] == 16.0        # 8.0 * 2
    assert con["cost"]["total_cost"] == 16.0


# ── lead-time surfacing (critical-path part) ──────────────────────────────────
def test_lead_weeks_parses_mouser_strings_digikey_numbers_and_junk():
    assert L._lead_weeks("16 Weeks") == 16          # Mouser string
    assert L._lead_weeks("12 weeks") == 12          # case-insensitive
    assert L._lead_weeks("3 Wks") == 3              # abbreviated unit
    assert L._lead_weeks(16) == 16                  # DigiKey numeric weeks
    assert L._lead_weeks(8.0) == 8                  # float weeks
    assert L._lead_weeks("14 Days") == 2            # days -> weeks, rounded up
    assert L._lead_weeks("10 Days") == 2            # 10/7 -> ceil 2
    assert L._lead_weeks(None) is None              # LCSC / absent
    assert L._lead_weeks("") is None
    assert L._lead_weeks("In Stock") is None        # unparseable text -> unknown
    assert L._lead_weeks(-4) is None                # garbage negative
    assert L._lead_weeks(0) == 0                    # in-stock, not a lead risk


def test_bom_lead_time_finds_the_longest_lead_part():
    rows = [
        {"mpn": "FAST", "lead_time": "2 Weeks"},
        {"mpn": "SLOW", "lead_time": 20},           # DigiKey numeric — the critical path
        {"mpn": "MID", "lead_time": "8 Weeks"},
        {"mpn": "BARE"},                            # no lead data — ignored
    ]
    r = L.bom_lead_time(rows)
    assert r["max_weeks"] == 20
    assert r["critical_mpn"] == "SLOW"
    assert r["with_lead"] == 3                       # BARE excluded
    assert r["any"] is True


def test_bom_lead_time_none_when_no_lead_data():
    rows = [{"mpn": "A"}, {"mpn": "B", "lead_time": "In Stock"}]
    r = L.bom_lead_time(rows)
    assert r == {"max_weeks": None, "critical_mpn": None, "with_lead": 0, "any": False}


def test_bom_lead_time_ties_keep_first_seen():
    rows = [{"mpn": "FIRST", "lead_time": "12 Weeks"},
            {"mpn": "SECOND", "lead_time": "12 Weeks"}]
    assert L.bom_lead_time(rows)["critical_mpn"] == "FIRST"


def test_bom_lead_time_falls_back_to_value_when_no_mpn():
    rows = [{"value": "10uF X7R", "lead_time": 30}]
    assert L.bom_lead_time(rows)["critical_mpn"] == "10uF X7R"


def test_price_rows_threads_lead_time_onto_priced_rows():
    rows = [{"mpn": "R-10K", "qty": 5}]

    def lookup(mpn):
        return {"unit_price": 0.10, "lead_time": "16 Weeks"}

    L._price_rows(rows, lookup, "qty")
    assert rows[0]["lead_time"] == "16 Weeks"         # threaded through for later surfacing


def test_procurement_summary_covers_cost_lead_and_counts():
    rows = [
        {"mpn": "SLOW", "qty": 1, "unit_price": 20.0, "lead_time": "16 Weeks"},
        {"mpn": "R", "qty": 4, "unit_price": 0.10},
        {"mpn": "C", "qty": 3, "unit_price": 0.05},
    ]                                                 # 3 lines, 8 parts, $20.55, 16 wk (SLOW)
    s = L.bom_procurement_summary(rows)
    assert "3 lines" in s and "8 parts" in s
    assert "$20.55/board" in s
    assert "critical path 16 wk (SLOW)" in s
    assert "unpriced" not in s                        # every line priced


def test_procurement_summary_projects_a_run_when_boards_gt_one():
    rows = [{"mpn": "A", "qty": 2, "unit_price": 1.50}]  # per-board $3.00; ×10 = $30.00
    s = L.bom_procurement_summary(rows, boards=10)
    assert "×10" in s
    assert "$30.00" in s and "$3.00 each" in s
    assert "parts/board" in s                         # labelled per-board when building a run


def test_procurement_summary_flags_unpriced_and_omits_cost_when_nothing_priced():
    rows = [{"mpn": "A", "qty": 1}, {"mpn": "B", "qty": 2}]  # no prices, no lead
    s = L.bom_procurement_summary(rows)
    assert "2 lines" in s and "3 parts" in s
    assert "/board" not in s                          # nothing priced -> no cost figure invented
    assert "2 unpriced" in s
    assert "critical path" not in s                   # no lead data -> no lead claim


def test_procurement_summary_empty_bom():
    assert "0 lines" in L.bom_procurement_summary([])
