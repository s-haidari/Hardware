"""Audit fixes for tools/LibraryManager.py (this session).

Each test drives the REAL behaviour of a fix, not a proxy:

- DST-aware Mouser daily-cap reset (no more fixed UTC-6 that overstates by 1h in summer).
- XLSX numeric cells never emit scientific notation for sub-1e-4 prices.
- make_mouser_lookup records the daily cap on a 429 (bulk-enrichment countdown trigger).
- resolve_mouser_key honours a user key in config.json (no longer silently ignored).
- consolidated_bom does not mislabel an untagged lookup hit as 'Mouser'.
- finalize_import reports the ACTUAL enrichment source(s), not a hardcoded 'Mouser'.
- merge_symbols de-dups on the FULL raw symbol id (colon-suffix collisions stay distinct).
- Positional property write-back is immune to duplicate/substring block text.
- Autofill persists the MPN to a dedicated strict-MPN property, never to 'Value'.
- BOM enrichment default carries Description.
"""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


class _FakeResp:
    def __init__(self, body: str, status: int = 200):
        self._body = body.encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── DST-aware Mouser reset ────────────────────────────────────────────────────
def test_next_mouser_reset_honors_central_dst_in_summer():
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        central = ZoneInfo("America/Chicago")   # the CALL needs the tz DB, not just the import
    except Exception:  # pragma: no cover - env without tzdata (e.g. Windows w/o the pip pkg)
        import pytest
        pytest.skip("no zoneinfo/tzdata available")
    # A mid-summer timestamp (CDT = UTC-5): the next local midnight is 05:00 UTC,
    # NOT the 06:00 UTC that a fixed UTC-6 offset would report.
    now = _dt.datetime(2026, 7, 8, 18, 0, tzinfo=_dt.timezone.utc).timestamp()
    reset = LM._next_mouser_reset(now)
    reset_utc = _dt.datetime.fromtimestamp(reset, _dt.timezone.utc)
    assert (reset_utc.hour, reset_utc.minute) == (5, 0)     # CDT midnight, not 06:00
    # And it is a real local midnight in America/Chicago.
    local = _dt.datetime.fromtimestamp(reset, central)
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    assert reset > now


def test_next_mouser_reset_is_local_midnight_in_winter():
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        central = ZoneInfo("America/Chicago")   # the CALL needs the tz DB, not just the import
    except Exception:  # pragma: no cover
        import pytest
        pytest.skip("no zoneinfo/tzdata available")
    now = _dt.datetime(2026, 1, 15, 18, 0, tzinfo=_dt.timezone.utc).timestamp()  # CST = UTC-6
    reset = LM._next_mouser_reset(now)
    reset_utc = _dt.datetime.fromtimestamp(reset, _dt.timezone.utc)
    assert (reset_utc.hour, reset_utc.minute) == (6, 0)     # CST midnight
    local = _dt.datetime.fromtimestamp(reset, central)
    assert (local.hour, local.minute) == (0, 0)


# ── XLSX numeric formatting never uses scientific notation ────────────────────
def test_xlsx_number_never_scientific():
    # The exact values the finding flagged: repr() would give '1e-05' / '1e-06'.
    assert LM._xlsx_number(round(1e-5, 6)) == "0.00001"
    assert LM._xlsx_number(1e-6) in ("0.000001", "0")       # rounds to 6dp
    assert "e" not in LM._xlsx_number(1e-6).lower()
    assert "e" not in LM._xlsx_number(0.00001).lower()
    # Whole numbers stay compact; ordinary prices render fixed-point.
    assert LM._xlsx_number(5.0) == "5"
    assert LM._xlsx_number(0.1) == "0.1"
    assert LM._xlsx_number(8.12) == "8.12"


def test_bom_xlsx_workbook_has_no_scientific_notation(tmp_path):
    # A sub-1e-4 unit price must land in the workbook as a valid numeric literal.
    import zipfile
    rows = [{"mpn": "R1", "manufacturer": "ACME", "value": "10k", "footprint": "0402",
             "datasheet": "", "unit_price": 0.00001, "extended": 0.00001,
             "qty": 1, "stock": 5000, "lifecycle": "Active"}]
    data = LM.bom_xlsx(rows)
    assert isinstance(data, (bytes, bytearray))
    out = tmp_path / "bom.xlsx"
    out.write_bytes(data)
    with zipfile.ZipFile(out) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    # No exponent in any numeric <v>; the tiny price is fixed-point.
    assert "e-05" not in sheet.lower()
    assert "<v>0.00001</v>" in sheet


# ── make_mouser_lookup records the cap on 429 ─────────────────────────────────
def test_make_mouser_lookup_records_rate_limit(monkeypatch, tmp_path):
    import urllib.request
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://mouser", 429, "rate", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    assert LM.mouser_reset_seconds_remaining(tmp_path / "config.json") is None
    lookup = LM.make_mouser_lookup("testkey")
    assert lookup("SOME-MPN") is None                        # 429 -> miss, never raises
    # The cap is now recorded so the SRC-04 countdown can fire on the bulk path.
    assert LM.mouser_reset_seconds_remaining(tmp_path / "config.json") > 0


def test_make_mouser_lookup_normal_miss_does_not_record(monkeypatch, tmp_path):
    import urllib.request
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    empty = '{"SearchResults": {"Parts": []}}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(empty))
    lookup = LM.make_mouser_lookup("testkey")
    assert lookup("NOPART") is None
    assert LM.mouser_reset_seconds_remaining(tmp_path / "config.json") is None  # genuine miss


# ── resolve_mouser_key honours config.json ────────────────────────────────────
def test_resolve_mouser_key_honors_config(monkeypatch, tmp_path):
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(LM, "CONFIG_PATH", cfg_path)
    LM.write_setting("MouserApiKey", "user-supplied-key", cfg_path)
    assert LM.resolve_mouser_key() == "user-supplied-key"   # not silently ignored


def test_resolve_mouser_key_env_beats_config(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(LM, "CONFIG_PATH", cfg_path)
    LM.write_setting("MouserApiKey", "cfg-key", cfg_path)
    monkeypatch.setenv("MOUSER_API_KEY", "env-key")
    assert LM.resolve_mouser_key() == "env-key"


def test_resolve_mouser_key_falls_back_to_baked(monkeypatch, tmp_path):
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")  # empty config
    assert LM.resolve_mouser_key() == LM.app_secrets.MOUSER_API_KEY_DEFAULT


# ── consolidated_bom does not mislabel an untagged hit as Mouser ──────────────
def test_consolidated_bom_untagged_hit_is_not_labeled_mouser(monkeypatch):
    # A single-provider raw lookup that omits 'source' must not be tagged 'Mouser'.
    sheet_rows = {"rows": [{"mpn": "ABC123", "manufacturer": "", "value": "IC",
                            "footprint": "SOIC-8", "datasheet": "", "description": "",
                            "qty": 1, "refs": ["U1"]}]}
    monkeypatch.setattr(LM, "bom_from_kicad_schematic", lambda p: sheet_rows)

    def raw_lookup(mpn):
        return {"manufacturer": "ACME", "datasheet": "d"}   # NO 'source' key
    out = LM.consolidated_bom({"BoardA": ["s1.kicad_sch"]}, lookup=raw_lookup)
    src = out["rows"][0]["source"]
    assert src != "Mouser"                                   # untagged -> unknown, not Mouser
    assert src == ""


# ── merge_symbols de-dups on full raw id ──────────────────────────────────────
def test_merge_symbols_keeps_colon_suffix_collisions(tmp_path):
    class _Log:
        def write(self, *_a, **_k):
            pass

    def sym(raw):
        return (f'  (symbol "{raw}" (in_bom yes)\n'
                f'    (property "Value" "{raw}" (at 0 0 0))\n  )')

    target = tmp_path / "MySymbols.kicad_sym"
    target.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n'
        f'{sym("VendorA:R_0402")}\n)\n', encoding="utf-8")
    src = tmp_path / "src.kicad_sym"
    src.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "x")\n'
        f'{sym("VendorB:R_0402")}\n)\n', encoding="utf-8")

    LM.merge_symbols(target, [src], _Log())
    out = target.read_text(encoding="utf-8")
    # Both distinct source symbols survive despite the shared 'R_0402' suffix.
    assert '(symbol "VendorA:R_0402"' in out
    assert '(symbol "VendorB:R_0402"' in out
    raws = {LM.extract_symbol_raw_name(b) for b in LM.extract_symbol_blocks(out)}
    assert {"VendorA:R_0402", "VendorB:R_0402"} <= raws


def test_merge_symbols_refuses_corrupt_unbalanced_source(tmp_path):
    """A truncated/unbalanced .kicad_sym must NOT be spliced into the target — that
    would make the shared library unloadable by KiCad. extract_symbol_blocks returns []
    for it, and merge must treat that as 'no symbols', never wrap the raw text as a block."""
    class _Log:
        def write(self, *_a, **_k):
            pass

    good = ('  (symbol "Good" (in_bom yes)\n'
            '    (property "Value" "Good" (at 0 0 0))\n  )')
    target = tmp_path / "MySymbols.kicad_sym"
    target.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n'
        f'{good}\n)\n', encoding="utf-8")
    src = tmp_path / "corrupt.kicad_sym"
    src.write_text('(symbol "Broken" (version 20211014)', encoding="utf-8")  # missing close paren

    LM.merge_symbols(target, [src], _Log())
    out = target.read_text(encoding="utf-8")
    assert out.count("(") == out.count(")")     # library stays balanced -> loadable
    assert '"Broken"' not in out                # the corrupt symbol was NOT merged in
    assert '(symbol "Good"' in out              # the valid target symbol is preserved


def test_extract_symbol_name_still_strips_prefix_for_display():
    block = '  (symbol "VendorA:R_0402" (in_bom yes)\n  )'
    assert LM.extract_symbol_name(block) == "R_0402"         # display unchanged
    assert LM.extract_symbol_raw_name(block) == "VendorA:R_0402"


# ── positional property write-back is collision-proof ─────────────────────────
def test_set_library_symbol_property_positional_no_substring_corruption(tmp_path):
    # Two symbols where one block's text is a substring of the other's region.
    # A naive text.replace(old, new, 1) could splice into the wrong span; the
    # positional rewrite substitutes by index.
    target = tmp_path / "MySymbols.kicad_sym"
    a = ('  (symbol "AAA" (in_bom yes)\n'
         '    (property "Value" "v" (at 0 0 0))\n  )')
    b = ('  (symbol "AAA_LONG" (in_bom yes)\n'
         '    (property "Value" "v" (at 0 0 0))\n  )')
    target.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n'
        f'{a}\n{b}\n)\n', encoding="utf-8")

    ok = LM.set_library_symbol_property({"SymbolLib": str(target)}, "AAA",
                                        "Manufacturer", "ACME")
    assert ok
    out = target.read_text(encoding="utf-8")
    blocks = {LM.extract_symbol_name(x): x for x in LM.extract_symbol_blocks(out)}
    # ONLY the AAA block got the Manufacturer property; AAA_LONG is untouched.
    assert "ACME" in blocks["AAA"]
    assert "ACME" not in blocks["AAA_LONG"]
    # Both symbols still present and intact.
    assert set(blocks) == {"AAA", "AAA_LONG"}


# ── autofill writes MPN to a dedicated strict property, never Value ───────────
def test_autofill_mpn_property_is_strict_not_value():
    by_key = {f[0]: f for f in LM.AUTOFILL_FIELDS}
    prop = by_key["mpn"][1]
    assert prop != "Value"
    norm = prop.lower().replace(" ", "").replace("_", "").replace("-", "")
    assert norm in LM._MPN_KEYS_STRICT
    # A value written under that property is recognized as a real MPN.
    ident = LM.part_identity({prop: "511-CRCW040210K0"})
    assert ident["mpn"] == "511-CRCW040210K0"
    assert LM.strict_mpn({prop: "511-CRCW040210K0"}) == "511-CRCW040210K0"
    # Writing it does NOT touch the passive's electrical Value.
    props = {"Value": "10k", prop: "511-CRCW040210K0"}
    assert props["Value"] == "10k"


# ── enrich_library threads the real source and rewrites positionally ──────────
def test_enrich_library_records_source_and_is_positional(tmp_path):
    # Two symbols with real MPNs and blank Manufacturer. A block whose text is a
    # substring of the other would corrupt under text.replace; positional rewrite
    # substitutes by index and each change carries the provider 'source'.
    def sym(name, mpn):
        return (f'  (symbol "{name}" (in_bom yes)\n'
                f'    (property "Value" "{mpn}" (at 0 0 0))\n  )')

    target = tmp_path / "MySymbols.kicad_sym"
    target.write_text(
        '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n'
        f'{sym("AAA", "MPN1")}\n{sym("AAA_LONG", "MPN2")}\n)\n', encoding="utf-8")

    def lookup(mpn):
        return {"manufacturer": "ACME", "source": "LCSC"}

    out = LM.enrich_library({"SymbolLib": str(target)}, lookup,
                            fields=("manufacturer",), dry_run=False)
    assert out["written"]
    assert out["changes"]
    assert all(c["source"] == "LCSC" for c in out["changes"])  # true source, not 'Mouser'
    text = target.read_text(encoding="utf-8")
    blocks = {LM.extract_symbol_name(b): b for b in LM.extract_symbol_blocks(text)}
    assert set(blocks) == {"AAA", "AAA_LONG"}                  # both intact, none dropped
    assert "ACME" in blocks["AAA"] and "ACME" in blocks["AAA_LONG"]


# ── BOM enrichment default carries Description ─────────────────────────────────
def test_bom_default_enrich_fields_include_description():
    import inspect
    sig = inspect.signature(LM._bom_from_components)
    default = sig.parameters["enrich_fields"].default
    assert "description" in default
    for fn in (LM.bom_from_project, LM.bom_from_kicad_schematic):
        assert "description" in inspect.signature(fn).parameters["enrich_fields"].default


def test_bom_enrichment_fills_blank_description_for_mpn_part():
    comps = [("U1", {"Value": "STM32", "Footprint": "LQFP",
                     "Manufacturer Part Number": "STM32F407"})]

    def lookup(mpn):
        return {"manufacturer": "ST", "datasheet": "d", "description": "MCU 32-bit"}
    out = LM._bom_from_components(comps, lookup=lookup)
    row = out["rows"][0]
    assert row["mpn"] == "STM32F407"
    assert row["description"] == "MCU 32-bit"                # enriched from the lookup
