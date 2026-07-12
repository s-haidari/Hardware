"""DigiKey (Product Information v4) distributor provider — the third source.

Adds DigiKey to the provider chain AFTER Mouser and LCSC, gated behind OAuth2
client-credentials (client_id + client_secret). Absent creds -> DigiKey is simply
not registered, so the chain is byte-for-byte unchanged (zero regression). The
adapter is injectable/mockable via `_digikey_token` and `_digikey_request` so tests
never hit the network and headless render can't hang.

Built fully unit-testable; ships structurally complete, pending live verification
(no creds available to exercise the real OAuth2 + keyword-search endpoints).
"""
import os
import sys
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


# One realistic DigiKey Product Information v4 keyword-search Product. Pricing and the
# DigiKey part number live per-ProductVariation (packaging): Cut Tape (MOQ 1) carries
# the small-qty ladder; Tape & Reel (MOQ 4000) is the bulk-only variation.
_PRODUCT = {
    "ManufacturerProductNumber": "GRM155R71C104KA88D",
    "Manufacturer": {"Id": 4, "Name": "Murata Electronics"},
    "Description": {"ProductDescription": "CAP CER 0.1UF 16V X7R 0402",
                    "DetailedDescription": "0.1 µF ±10% 16V Ceramic Capacitor X7R 0402"},
    "DatasheetUrl": "https://media.digikey.com/pdf/grm155.pdf",
    "ProductUrl": "https://www.digikey.com/en/products/detail/murata/GRM155R71C104KA88D/1234",
    "QuantityAvailable": 843210,
    "ProductStatus": {"Id": 0, "Status": "Active"},
    "ManufacturerLeadWeeks": "16 Weeks",
    "Classifications": {"RohsStatus": "ROHS3 Compliant", "MoistureSensitivityLevel": "1"},
    "Category": {"Name": "Ceramic Capacitors"},
    "ProductVariations": [
        {"DigiKeyProductNumber": "490-10529-2-ND",
         "PackageType": {"Name": "Tape & Reel (TR)"},
         "MinimumOrderQuantity": 4000,
         "StandardPricing": [{"BreakQuantity": 4000, "UnitPrice": 0.0031, "TotalPrice": 12.4}]},
        {"DigiKeyProductNumber": "490-10529-1-ND",
         "PackageType": {"Name": "Cut Tape (CT)"},
         "MinimumOrderQuantity": 1,
         "StandardPricing": [{"BreakQuantity": 1, "UnitPrice": 0.10, "TotalPrice": 0.10},
                             {"BreakQuantity": 100, "UnitPrice": 0.048, "TotalPrice": 4.80},
                             {"BreakQuantity": 10, "UnitPrice": 0.055, "TotalPrice": 0.55}]},
    ],
}


def _canned_search(products):
    """A fake _digikey_request returning the given Products (no network)."""
    return lambda endpoint, token, client_id, payload, timeout=8: {
        "data": {"Products": products, "ProductsCount": len(products)},
        "status": 200, "error": ""}


# ── _parse_digikey_part ───────────────────────────────────────────────────────
def test_parse_full_product():
    p = LM._parse_digikey_part(_PRODUCT)
    assert p["mpn"] == "GRM155R71C104KA88D"
    assert p["manufacturer"] == "Murata Electronics"
    assert p["datasheet"] == "https://media.digikey.com/pdf/grm155.pdf"
    assert p["description"] == "CAP CER 0.1UF 16V X7R 0402"
    assert p["stock"] == 843210
    assert p["lifecycle"] == "Active"
    assert p["rohs"] == "ROHS3 Compliant"
    assert p["lead_time"] == "16 Weeks"
    assert p["url"].endswith("/1234")
    assert p["category"] == "Ceramic Capacitors"


def test_parse_picks_lowest_moq_priced_variation():
    """DigiKey splits pricing across packaging variations; the small-qty (lowest-MOQ)
    priced variation is the one an engineer prototypes from, so its P/N + ladder win."""
    p = LM._parse_digikey_part(_PRODUCT)
    assert p["digikey_pn"] == "490-10529-1-ND"                  # Cut Tape (MOQ 1), not the reel
    qtys = [b["qty"] for b in p["price_breaks"]]
    assert qtys == [1, 10, 100]                                 # ascending regardless of input order
    assert p["unit_price"] == 0.10                              # the qty-1 rung
    assert LM.price_at_qty(p["price_breaks"], 250) == 0.048     # feeds the volume-pricing helper


def test_parse_string_manufacturer_and_description():
    """v4 usually nests Manufacturer/Description as objects, but tolerate plain strings."""
    p = LM._parse_digikey_part({"ManufacturerProductNumber": "X",
                                "Manufacturer": "Yageo",
                                "Description": "10k 0402"})
    assert p["manufacturer"] == "Yageo"
    assert p["description"] == "10k 0402"


def test_parse_partial_product_never_crashes():
    p = LM._parse_digikey_part({"ManufacturerProductNumber": "BARE"})  # no variations/status/stock
    assert p["mpn"] == "BARE"
    assert p["manufacturer"] == ""
    assert p["price_breaks"] == []
    assert p["unit_price"] is None
    assert p["stock"] == 0
    assert p["digikey_pn"] == ""
    assert p["lifecycle"] == "Active"                          # empty status defaults to Active


def test_parse_variation_without_pricing_still_yields_pn():
    """A product whose only variation has no StandardPricing still surfaces its P/N."""
    p = LM._parse_digikey_part({
        "ManufacturerProductNumber": "NOPRICE",
        "ProductVariations": [{"DigiKeyProductNumber": "NP-ND", "MinimumOrderQuantity": 1}]})
    assert p["digikey_pn"] == "NP-ND"
    assert p["price_breaks"] == []
    assert p["unit_price"] is None


# ── make_digikey_lookup ───────────────────────────────────────────────────────
def test_lookup_returns_normalized_hit(monkeypatch):
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request", _canned_search([_PRODUCT]))
    r = LM.make_digikey_lookup("id", "secret")("GRM155R71C104KA88D")
    assert r["mpn"] == "GRM155R71C104KA88D"
    assert r["digikey_pn"] == "490-10529-1-ND"


def test_lookup_prefers_exact_mpn_match(monkeypatch):
    other = {"ManufacturerProductNumber": "OTHER"}
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request", _canned_search([other, _PRODUCT]))
    r = LM.make_digikey_lookup("id", "secret")("grm155r71c104ka88d")   # case-insensitive
    assert r["mpn"] == "GRM155R71C104KA88D"                    # exact match wins over first result


def test_lookup_none_on_no_products(monkeypatch):
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request", _canned_search([]))
    assert LM.make_digikey_lookup("id", "secret")("NOPART") is None


def test_lookup_none_on_transport_error(monkeypatch):
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request",
                        lambda *a, **k: {"data": None, "status": None, "error": "timeout"})
    assert LM.make_digikey_lookup("id", "secret")("ANY") is None


def test_lookup_none_when_token_unavailable(monkeypatch):
    """A refused/blocked OAuth token (bad creds, throttle) -> no lookup, never raises."""
    called = []
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: None)
    monkeypatch.setattr(LM, "_digikey_request",
                        lambda *a, **k: called.append(1) or _canned_search([_PRODUCT])(*a, **k))
    assert LM.make_digikey_lookup("id", "secret")("ANY") is None
    assert called == []                                        # never searches without a token


def test_lookup_none_on_empty_mpn(monkeypatch):
    called = []
    monkeypatch.setattr(LM, "_digikey_token",
                        lambda cid, sec, timeout=8: called.append(1) or "TOKEN")
    assert LM.make_digikey_lookup("id", "secret")("") is None
    assert called == []                                        # never even fetches a token


def test_lookup_none_without_creds(monkeypatch):
    assert LM.make_digikey_lookup("", "")("ANY") is None       # no creds -> inert lookup


def test_lookup_never_raises(monkeypatch):
    def boom(cid, sec, timeout=8):
        raise RuntimeError("oauth exploded")
    monkeypatch.setattr(LM, "_digikey_token", boom)
    assert LM.make_digikey_lookup("id", "secret")("ANY") is None   # swallowed, not fatal


# ── resolve_digikey_creds ─────────────────────────────────────────────────────
def test_resolve_creds_from_env(monkeypatch):
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "env-id")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "env-secret")
    assert LM.resolve_digikey_creds({}) == ("env-id", "env-secret")


def test_resolve_creds_absent_is_none(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(LM.app_secrets, "DIGIKEY_CLIENT_ID_DEFAULT", None)
    monkeypatch.setattr(LM.app_secrets, "DIGIKEY_CLIENT_SECRET_DEFAULT", None)
    # Isolate from any real config.json creds (the user path added for SRC-04):
    # with env + baked + config all empty, DigiKey must resolve to (None, None).
    monkeypatch.setattr(LM, "read_setting", lambda key, default=None, **k: default)
    assert LM.resolve_digikey_creds({}) == (None, None)


# ── providers_from_config: DigiKey registered only with creds, after Mouser/LCSC ──
def test_digikey_absent_without_creds(monkeypatch):
    """No DigiKey creds -> the chain is exactly Mouser+LCSC, zero regression."""
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: (None, None))
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "read_setting", lambda key, default=None, **k: default)
    monkeypatch.setattr(LM, "make_mouser_lookup", lambda key, timeout=8: (lambda m: None))
    monkeypatch.setattr(LM, "_lcsc_request", lambda query, timeout=8: {"data": {"components": []},
                                                                       "error": ""})
    chain = LM.providers_from_config({})
    assert chain("ANYTHING") is None                           # only Mouser+LCSC ran, both miss


def test_digikey_covers_what_mouser_and_lcsc_miss(monkeypatch):
    """DigiKey is the LAST fallback: Mouser wins, then LCSC, then DigiKey."""
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: ("id", "secret"))
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request", _canned_search([_PRODUCT]))
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "read_setting", lambda key, default=None, **k: default)
    monkeypatch.setattr(LM, "make_mouser_lookup",
                        lambda key, timeout=8: (lambda m: {"mpn": m} if m == "ON_MOUSER" else None))
    monkeypatch.setattr(LM, "_lcsc_request",
                        lambda query, timeout=8: {"data": {"components": (
                            [{"mfr": query, "extra": {"mpn": query}}] if query == "ON_LCSC" else [])},
                            "error": ""})
    chain = LM.providers_from_config({})
    assert chain("ON_MOUSER")["source"] == "Mouser"            # preferred provider still wins
    assert chain("ON_LCSC")["source"] == "LCSC"                # second fallback
    assert chain("GRM155R71C104KA88D")["source"] == "DigiKey"  # DigiKey covers the last gap


# ── _dist_pn is source-aware for DigiKey ──────────────────────────────────────
def test_dist_pn_digikey_source():
    r = {"source": "DigiKey", "digikey_pn": "490-10529-1-ND",
         "mouser_pn": "81-GRM155R71C104K", "lcsc_pn": "C71629"}
    assert LM._dist_pn(r) == "490-10529-1-ND"                  # DigiKey source -> DigiKey P/N


def test_dist_pn_falls_back_to_digikey_when_only_one_present():
    r = {"source": "", "digikey_pn": "490-10529-1-ND"}
    assert LM._dist_pn(r) == "490-10529-1-ND"


# ── _price_rows threads digikey_pn through ────────────────────────────────────
def test_price_rows_threads_digikey_pn(monkeypatch):
    monkeypatch.setattr(LM, "_digikey_token", lambda cid, sec, timeout=8: "TOKEN")
    monkeypatch.setattr(LM, "_digikey_request", _canned_search([_PRODUCT]))
    chain = LM.make_provider_chain([("DigiKey", LM.make_digikey_lookup("id", "secret"))])
    rows = [{"mpn": "GRM155R71C104KA88D", "total_qty": 50}]
    LM._price_rows(rows, chain, "total_qty")
    assert rows[0]["digikey_pn"] == "490-10529-1-ND"           # threaded like mouser_pn/lcsc_pn
    assert rows[0]["source"] == "DigiKey"
