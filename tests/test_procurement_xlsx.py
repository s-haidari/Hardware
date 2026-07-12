"""Procurement Excel sheet: the buy-side .xlsx an engineer hands to purchasing, modeled on
a real hand-made order sheet but auto-populated from the Mouser/DigiKey data we already
fetch — Description / P/N (vendor part #) / Electronic Component? / Vendor / QTY / Unit Cost
/ Cost @ QTY / Tax/Tariff / Shipping / Total Cost / Product Link / Notes, plus a TOTAL row.
Pure-stdlib writer, so we validate WITHOUT openpyxl: unzip, parse every XML part with
ElementTree, and read back the typed cells. Quantities honor the PCB pack multiple (boards
round up to it) and the passives-only spares buffer; Tax/Tariff is one rate per line and
Shipping is one order-level charge summed in the TOTAL row.
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
    sheet = zipfile.ZipFile(io.BytesIO(data)).read("xl/worksheets/sheet1.xml")
    root = ET.fromstring(sheet)
    out = {}
    for c in root.iter(f"{NS}c"):
        if c.get("t") == "inlineStr":
            node = c.find(f"{NS}is/{NS}t")
            out[c.get("r")] = node.text if node is not None else ""
        else:
            v = c.find(f"{NS}v")
            out[c.get("r")] = v.text if v is not None else None
    return out


def _colmap(cells):
    return {cells[f"{chr(ord('A') + i)}1"]: chr(ord("A") + i)
            for i in range(15) if f"{chr(ord('A') + i)}1" in cells}


REQUIRED = ["Description", "P/N", "Electronic Component?", "Vendor", "QTY", "Unit Cost",
            "Cost @ QTY", "Tax/Tariff", "Shipping", "Total Cost", "Product Link", "Notes"]


def test_procurement_xlsx_is_valid_and_matches_the_target_columns():
    rows = [{"refs": ["C1"], "qty": 1, "value": "100n", "mpn": "GRM155"}]
    data = L.procurement_xlsx(rows, boards=1)
    assert data[:2] == b"PK"
    zf = zipfile.ZipFile(io.BytesIO(data))
    for part in zf.namelist():
        if part.endswith(".xml") or part.endswith(".rels"):
            ET.fromstring(zf.read(part))                      # well-formed -> Excel opens it
    assert list(_colmap(_cells(data))) == REQUIRED            # exact order, the target layout


def test_procurement_autofills_vendor_pn_link_and_costs_from_mouser_fields():
    rows = [{"refs": ["C1", "C2"], "qty": 2, "value": "100n", "description": "Chip capacitor",
             "mpn": "GRM155R71C104KA88", "mouser_pn": "81-GRM155R71C104KA88", "source": "Mouser",
             "url": "https://www.mouser.com/ProductDetail/81-GRM155R71C104KA88",
             "unit_price": 0.05, "price_breaks": [{"qty": 1, "price": 0.05}, {"qty": 10, "price": 0.02}]}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=3, tax_rate=0.10))
    cm = _colmap(cells)
    assert cells[f"{cm['Description']}2"] == "Chip capacitor"
    assert cells[f"{cm['P/N']}2"] == "81-GRM155R71C104KA88"       # the ORDERABLE vendor part #
    assert cells[f"{cm['Electronic Component?']}2"] == "Yes"
    assert cells[f"{cm['Vendor']}2"] == "mouser.com"             # source -> domain
    assert cells[f"{cm['QTY']}2"] == "6"                         # 2/board * 3 (pack)
    # volume price at qty 6 = the qty-1 break ($0.05, the 10-break not reached); 6*0.05=$0.30
    assert float(cells[f"{cm['Unit Cost']}2"]) == 0.05
    assert float(cells[f"{cm['Cost @ QTY']}2"]) == 0.30
    assert float(cells[f"{cm['Tax/Tariff']}2"]) == 0.03          # 10% of 0.30
    assert float(cells[f"{cm['Total Cost']}2"]) == 0.33
    assert cells[f"{cm['Product Link']}2"] == \
        "https://www.mouser.com/ProductDetail/81-GRM155R71C104KA88"


def test_procurement_description_falls_back_to_refdes_category():
    rows = [{"refs": ["R5"], "qty": 1, "value": "10k", "mpn": "RC10K", "unit_price": 0.01},
            {"refs": ["L2"], "qty": 1, "value": "4.7u", "mpn": "IND", "unit_price": 1.0},
            {"refs": ["FB1"], "qty": 1, "value": "600R", "mpn": "BEAD", "unit_price": 0.1}]
    cells = _cells(L.procurement_xlsx(rows, boards=3, pcb_multiple=3))
    cm = _colmap(cells)
    desc = {cells[f"{cm['P/N']}{r}"]: cells[f"{cm['Description']}{r}"] for r in (2, 3, 4)}
    assert desc["RC10K"] == "Resistor"
    assert desc["IND"] == "Inductor"
    assert desc["BEAD"] == "Ferrite Bead"


def test_procurement_digikey_vendor_and_pn():
    rows = [{"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "EFM32", "source": "DigiKey",
             "digikey_pn": "336-EFM32-ND", "url": "https://www.digikey.com/x", "unit_price": 9.5}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=3))
    cm = _colmap(cells)
    assert cells[f"{cm['Vendor']}2"] == "digikey.com"
    assert cells[f"{cm['P/N']}2"] == "336-EFM32-ND"


def test_procurement_totals_include_order_shipping():
    rows = [{"refs": ["C1"], "qty": 1, "value": "100n", "mpn": "GRM", "unit_price": 0.05},
            {"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM", "unit_price": 5.0}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=3, tax_rate=0.10, shipping=10.0))
    cm = _colmap(cells)
    last = max(int(ref[1:]) for ref in cells if ref[0] == cm["Total Cost"])
    # C1: 3 * .05 = .15 ; U1: 3 * 5 = 15.00 -> subtotal 15.15 ; tax 10% = 1.515 ; ship 10
    assert float(cells[f"{cm['Cost @ QTY']}{last}"]) == 15.15
    assert float(cells[f"{cm['Tax/Tariff']}{last}"]) == 1.515
    assert float(cells[f"{cm['Shipping']}{last}"]) == 10.0
    assert float(cells[f"{cm['Total Cost']}{last}"]) == 26.665      # 15.15 + 1.515 + 10
    assert cells[f"{cm['Description']}{last}"].upper().startswith("TOTAL")


def test_procurement_unpriced_line_leaves_money_blank_but_shows_qty():
    rows = [{"refs": ["J1"], "qty": 1, "value": "USB-C", "mpn": ""}]
    cells = _cells(L.procurement_xlsx(rows, boards=3, pcb_multiple=3))
    cm = _colmap(cells)
    assert cells[f"{cm['QTY']}2"] == "3"
    assert cells.get(f"{cm['Unit Cost']}2", "") in (None, "")
    assert cells.get(f"{cm['Total Cost']}2", "") in (None, "")


def test_procurement_notes_flags_an_unpriced_line():
    rows = [{"refs": ["J1"], "qty": 1, "value": "USB-C", "mpn": ""}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=1))
    cm = _colmap(cells)
    note = (cells.get(f"{cm['Notes']}2") or "").lower()
    assert "price" in note and "quote" in note          # buyer must source it manually


def test_procurement_notes_report_the_spares_padding_on_passives():
    # 10 passives/board * 1 board, +20% attrition -> ceil(10*1.2)=12, i.e. +2 spares.
    rows = [{"refs": [f"C{i}" for i in range(1, 11)], "qty": 10, "value": "100n",
             "mpn": "GRM", "unit_price": 0.05}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=1, spares_pct=20))
    cm = _colmap(cells)
    assert cells[f"{cm['QTY']}2"] == "12"
    note = cells[f"{cm['Notes']}2"]
    assert "+2 spares" in note and "20% attrition" in note


def test_procurement_priced_unpadded_line_has_no_note():
    rows = [{"refs": ["U1"], "qty": 1, "value": "MCU", "mpn": "STM", "unit_price": 5.0}]
    cells = _cells(L.procurement_xlsx(rows, boards=1, pcb_multiple=1))
    cm = _colmap(cells)
    assert (cells.get(f"{cm['Notes']}2") or "") == ""    # nothing to flag


def test_procurement_note_helper_combines_both_exceptions():
    assert L._procurement_note(priced=True, spares_added=0, spares_pct=0) == ""
    assert L._procurement_note(priced=False, spares_added=0, spares_pct=0) != ""
    both = L._procurement_note(priced=False, spares_added=1, spares_pct=15)
    assert "price" in both.lower() and "+1 spare " in both and "15% attrition" in both
