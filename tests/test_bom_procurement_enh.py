"""BOM & Procurement decision-completeness (subsystem projects-bom-procurement):
per-line stock coverage, the export line-scope predicates, the filtered-CSV re-serializer's
byte-parity with the builders, and the procurement sheet's landed assembly cost math.

The Excel writer is pure-stdlib, so we validate WITHOUT openpyxl: unzip, parse the sheet XML
with ElementTree, and read back the typed cells.
"""
import io
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _cells(data):
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml"))
    out = {}
    for c in root.iter(f"{NS}c"):
        if c.get("t") == "inlineStr":
            node = c.find(f"{NS}is/{NS}t")
            out[c.get("r")] = node.text if node is not None else ""
        else:
            v = c.find(f"{NS}v")
            out[c.get("r")] = v.text if v is not None else None
    return out


def _colmap(cells, n=14):
    return {cells[f"{chr(ord('A') + i)}1"]: chr(ord("A") + i)
            for i in range(n) if f"{chr(ord('A') + i)}1" in cells}


# ── per-line stock coverage ───────────────────────────────────────────────────
def test_stock_risk_flags_short_line_only_at_the_run_quantity():
    r = {"qty": 5, "mpn": "X", "stock": 20}
    assert L.bom_line_stock_risk(r, boards=1)["short"] is False      # need 5, have 20
    assert L.bom_line_stock_risk(r, boards=4)["short"] is False      # need 20, have 20 (exact)
    risk = L.bom_line_stock_risk(r, boards=5)                        # need 25, have 20
    assert risk["short"] is True and risk["kind"] == "warn"
    assert risk["required"] == 25 and risk["available"] == 20


def test_stock_risk_no_stock_is_err_unknown_is_no_risk():
    assert L.bom_line_stock_risk({"qty": 1, "stock": 0}, boards=1)["kind"] == "err"
    assert L.bom_line_stock_risk({"qty": 1, "stock": 0}, boards=1)["short"] is True
    # Unknown stock (never priced) is NOT a risk — absence of data is not a warning.
    unknown = L.bom_line_stock_risk({"qty": 1}, boards=10)
    assert unknown["kind"] is None and unknown["short"] is False and unknown["available"] is None
    # A stray bool is not a stock count.
    assert L.bom_line_stock_risk({"qty": 1, "stock": True}, boards=1)["available"] is None


def test_stock_risk_kind_agrees_with_the_aggregate_on_fractional_stock():
    """Regression lock: a fractional 0 < stock < 1 line must be Low-Stock (warn) in BOTH the
    per-row tint and the aggregate — flooring stock first would split them (int(0.5)=0 -> err)."""
    r = {"qty": 1, "mpn": "X", "stock": 0.5}
    agg = L.bom_sourcing_risks([r], boards=1)
    per = L.bom_line_stock_risk(r, boards=1)
    assert agg["no_stock"] == 0 and agg["insufficient_stock"] == 1  # aggregate: Low-Stock
    assert per["kind"] == "warn"                                    # per-row must match (not err)


def test_stock_risk_matches_the_aggregate_sourcing_counts():
    rows = [{"qty": 1, "mpn": "A", "stock": 0},                      # no stock
            {"qty": 10, "mpn": "B", "stock": 5},                     # low at x1
            {"qty": 1, "mpn": "C", "stock": 100}]                    # fine
    agg = L.bom_sourcing_risks(rows, boards=1)
    per = [L.bom_line_stock_risk(r, boards=1) for r in rows]
    assert sum(1 for p in per if p["kind"] == "err") == agg["no_stock"]
    assert sum(1 for p in per if p["kind"] == "warn") == agg["insufficient_stock"]


# ── export line-scope predicates ──────────────────────────────────────────────
def test_line_populated_and_priced_predicates():
    assert L.bom_line_is_populated({"mpn": "X"}) is True
    assert L.bom_line_is_populated({"value": "10k"}) is True
    assert L.bom_line_is_populated({"mpn": "", "value": ""}) is False
    assert L.bom_line_is_priced({"unit_price": 0.10}) is True
    assert L.bom_line_is_priced({"extended": 1.0}) is True
    assert L.bom_line_is_priced({"unit_price": "Call for pricing"}) is False
    assert L.bom_line_is_priced({"mpn": "X"}) is False


# ── filtered-CSV re-serializer parity ─────────────────────────────────────────
def _sch(tmp_path):
    p = tmp_path / "s.kicad_sch"
    p.write_text(
        '(kicad_sch (version 20241229)\n'
        '  (symbol (lib_id "Device:R") (property "Reference" "R1") (property "Value" "10k")'
        ' (property "Footprint" "R_0402"))\n'
        '  (symbol (lib_id "Device:C") (property "Reference" "C1") (property "Value" "100n")'
        ' (property "Footprint" "C_0402"))\n'
        ')\n', encoding="utf-8")
    return p


def test_bom_csv_project_is_byte_identical_to_the_builder(tmp_path):
    build = L.bom_from_project([str(_sch(tmp_path))])
    priced = build.get("cost") is not None
    assert L.bom_csv(build["rows"], mode="project", priced=priced) == build["csv"]


def test_bom_csv_consolidated_is_byte_identical_to_the_builder(tmp_path):
    s = str(_sch(tmp_path))
    build = L.consolidated_bom({"BoardA": [s], "BoardB": [s]})
    sourced = "not_on_mouser" in build
    priced = "cost" in build
    assert L.bom_csv(build["rows"], mode="consolidated", board_names=build["board_names"],
                     sourced=sourced, priced=priced) == build["csv"]


def test_bom_csv_filtered_subset_drops_the_rows(tmp_path):
    build = L.bom_from_project([str(_sch(tmp_path))])
    rows = build["rows"]
    kept = [r for r in rows if r.get("value") == "10k"]
    text = L.bom_csv(kept, mode="project")
    assert "10k" in text and "100n" not in text                     # only the kept line


# ── procurement landed assembly cost ──────────────────────────────────────────
def test_procurement_landed_total_is_parts_plus_labour_surcharge_tax_ship():
    rows = [{"refs": ["C1"], "qty": 2, "value": "100n", "mpn": "GRM", "unit_price": 0.05},
            {"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM", "unit_price": 5.0}]
    data = L.procurement_xlsx(rows, boards=1, pcb_multiple=3, tax_rate=0.10, shipping=10.0,
                              labour_per_board=4.0, assembly_surcharge_rate=0.05)
    cells = _cells(data); cm = _colmap(cells)
    rows_by_desc = {cells[f"{cm['Description']}{i}"]: i
                    for i in range(2, 40) if f"{cm['Description']}{i}" in cells
                    and cells[f"{cm['Description']}{i}"]}
    # Parts subtotal: C1 6*0.05=0.30, U1 3*5=15.00 -> 15.30. Tax 10% = 1.53.
    # Labour 4.0*1 board = 4.0 ; surcharge 5% of 15.30 = 0.765 ; assembly = 4.765.
    assert "Assembly" in rows_by_desc
    ar = rows_by_desc["Assembly"]
    assert float(cells[f"{cm['Cost @ QTY']}{ar}"]) == 4.765
    assert cells.get(f"{cm['Tax/Tariff']}{ar}", "") in (None, "")    # assembly is not taxed
    tr = rows_by_desc["TOTAL"]
    assert float(cells[f"{cm['Cost @ QTY']}{tr}"]) == 20.065         # parts 15.30 + assembly 4.765
    assert float(cells[f"{cm['Tax/Tariff']}{tr}"]) == 1.53
    assert float(cells[f"{cm['Shipping']}{tr}"]) == 10.0
    assert float(cells[f"{cm['Total Cost']}{tr}"]) == 31.595         # 20.065 + 1.53 + 10


def test_procurement_no_assembly_line_when_labour_and_surcharge_are_zero():
    rows = [{"refs": ["C1"], "qty": 1, "value": "100n", "mpn": "GRM", "unit_price": 0.05}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=1))
    cm = _colmap(cells)
    descs = [cells[f"{cm['Description']}{i}"] for i in range(2, 40)
             if f"{cm['Description']}{i}" in cells]
    assert "Assembly" not in descs                                   # off by default


def test_procurement_labour_bills_actual_boards_not_the_pack_rounding():
    # 1 board, pack of 3 -> parts buy for 3, but labour bills the 1 board actually built.
    rows = [{"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM", "unit_price": 5.0}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=3, labour_per_board=7.0))
    cm = _colmap(cells)
    ar = next(i for i in range(2, 40) if f"{cm['Description']}{i}" in cells
              and cells[f"{cm['Description']}{i}"] == "Assembly")
    assert float(cells[f"{cm['Cost @ QTY']}{ar}"]) == 7.0           # 7.0 * 1 board, not * 3
