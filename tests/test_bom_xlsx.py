"""Clean Excel (.xlsx) export of a BOM. Pure-stdlib writer (zipfile + XML), so nothing
extra has to be bundled into the packaged app. The tests validate the file WITHOUT
openpyxl: a real .xlsx is a zip of well-formed XML parts, so we unzip it, parse every
member with ElementTree (catches malformed XML that would make Excel refuse to open it),
and assert the cell types, header styling, freeze pane, and autofilter are present.
"""
import io
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _sheet(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    return zf, zf.read("xl/worksheets/sheet1.xml")


def _rows(sheet_xml):
    """Parse the worksheet into {cell_ref: (style, type, value)} for row lookups."""
    root = ET.fromstring(sheet_xml)
    cells = {}
    for row in root.iter(f"{NS}row"):
        for c in row.findall(f"{NS}c"):
            ref = c.get("r")
            t = c.get("t")
            if t == "inlineStr":
                node = c.find(f"{NS}is/{NS}t")
                val = node.text if node is not None else ""
            else:
                v = c.find(f"{NS}v")
                val = v.text if v is not None else None
            cells[ref] = (c.get("s"), t, val)
    return cells


def test_bom_xlsx_is_a_valid_zip_of_wellformed_xml():
    rows = [{"refs": ["R1", "R2"], "qty": 2, "value": "10k", "mpn": "RC0402",
             "manufacturer": "Yageo", "footprint": "R_0402", "basic": True}]
    data = L.bom_xlsx(rows)
    assert data[:2] == b"PK"                                  # zip magic -> a real .xlsx
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                 "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"):
        assert part in names, f"missing {part}"
    for part in names:                                        # every XML part must parse
        if part.endswith(".xml") or part.endswith(".rels"):
            ET.fromstring(zf.read(part))                      # raises on malformed XML


def test_bom_xlsx_header_is_bold_and_frozen_with_autofilter():
    rows = [{"refs": ["R1"], "qty": 1, "value": "10k", "mpn": "RC0402"}]
    _, sheet = _sheet(L.bom_xlsx(rows))
    cells = _rows(sheet)
    # Header row 1 is text and carries the bold style (s="1"); the data row is not bold.
    assert cells["A1"][1] == "inlineStr" and cells["A1"][2] == "Refs"
    assert cells["A1"][0] == "1"                              # bold header style
    assert cells["A2"][0] != "1"                              # data row not bold
    txt = sheet.decode("utf-8")
    assert 'state="frozen"' in txt                            # header row frozen
    assert "<autoFilter" in txt                               # filter dropdowns on the header


def test_bom_xlsx_types_numbers_as_numbers_and_text_as_text():
    rows = [{"refs": ["R1", "R2", "R3"], "qty": 3, "value": "10k", "mpn": "RC0402",
             "unit_price": "$0.10", "extended": 0.30, "stock": 5000}]
    _, sheet = _sheet(L.bom_xlsx(rows))
    cells = _rows(sheet)
    head = {cells[f"{col}1"][2]: col for col in
            [chr(ord("A") + i) for i in range(15)] if f"{col}1" in cells}
    # Qty is a numeric cell (no inlineStr), value 3.
    qty = f"{head['Qty']}2"
    assert cells[qty][1] != "inlineStr" and cells[qty][2] == "3"
    # Refs is text.
    refs = f"{head['Refs']}2"
    assert cells[refs][1] == "inlineStr" and cells[refs][2] == "R1,R2,R3"
    # A Mouser string price ("$0.10") is coerced to a real number so Excel can sum it.
    up = f"{head['Unit Price']}2"
    assert cells[up][1] != "inlineStr" and float(cells[up][2]) == 0.10


def test_bom_xlsx_omits_price_columns_when_unpriced():
    rows = [{"refs": ["R1"], "qty": 1, "value": "10k", "mpn": "RC0402"}]
    _, sheet = _sheet(L.bom_xlsx(rows))
    cells = _rows(sheet)
    headers = [cells[f"{col}1"][2] for col in
               [chr(ord("A") + i) for i in range(20)] if f"{col}1" in cells]
    assert "Unit Price" not in headers and "Ext Price" not in headers
    assert "Refs" in headers and "MPN" in headers


def test_bom_xlsx_escapes_xml_special_characters():
    # An ampersand / angle bracket in a value must be escaped or the file won't open.
    rows = [{"refs": ["R1"], "qty": 1, "value": "A & B <x>", "mpn": "M&M"}]
    data = L.bom_xlsx(rows)
    _, sheet = _sheet(data)
    cells = _rows(sheet)                                      # parses -> escaping is valid
    head = {cells[f"{col}1"][2]: col for col in
            [chr(ord("A") + i) for i in range(15)] if f"{col}1" in cells}
    assert cells[f"{head['Value']}2"][2] == "A & B <x>"      # round-trips through the escape
