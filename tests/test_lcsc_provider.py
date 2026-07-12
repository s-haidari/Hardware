"""LCSC (jlcsearch) distributor provider — the key-free fallback distributor.

Adds a second source to the provider chain so parts Mouser does not carry can still
be sourced AND priced with zero configuration (jlcsearch needs no API key). The
adapter is injectable/mockable via `_lcsc_request` so tests never hit the network and
headless render can't hang.
"""
import os
import sys
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


# One realistic jlcsearch component (full=true), matching the skill's documented shape.
_FULL = {
    "lcsc": 14663,
    "mfr": "GRM155R71C104KA88D",
    "package": "0402",
    "description": "top-level desc",
    "datasheet": "https://www.lcsc.com/datasheet/top.pdf",
    "stock": 2751535,
    "price": [{"qFrom": 1, "qTo": 9, "price": 0.0069},
              {"qFrom": 100, "qTo": 499, "price": 0.0048},
              {"qFrom": 10, "qTo": 99, "price": 0.0055}],
    "basic": 1,
    "extra": {
        "number": "C71629",
        "mpn": "GRM155R71C104KA88D",
        "manufacturer": {"id": 4, "name": "Murata Electronics"},
        "description": "16V 100nF X7R 0402",
        "quantity": 2751535,
        "datasheet": {"pdf": "https://wmsc.lcsc.com/x.pdf"},
        "rohs": True,
        "url": "https://www.lcsc.com/product-detail/C71629.html",
    },
}


def _canned(components):
    """A fake _lcsc_request returning the given components (no network)."""
    return lambda query, timeout=8: {"data": {"components": components}, "error": ""}


# ── _parse_lcsc_part ──────────────────────────────────────────────────────────
def test_parse_full_component():
    p = LM._parse_lcsc_part(_FULL)
    assert p["mpn"] == "GRM155R71C104KA88D"
    assert p["manufacturer"] == "Murata Electronics"
    assert p["datasheet"] == "https://wmsc.lcsc.com/x.pdf"      # prefers extra.datasheet.pdf
    assert p["description"] == "16V 100nF X7R 0402"             # prefers the richer extra desc
    assert p["lcsc_pn"] == "C71629"
    assert p["stock"] == 2751535
    assert p["lifecycle"] == "Active"                          # jlcsearch has no lifecycle field
    assert p["url"].endswith("C71629.html")


def test_parse_price_breaks_ascending_and_unit_price():
    p = LM._parse_lcsc_part(_FULL)
    qtys = [b["qty"] for b in p["price_breaks"]]
    assert qtys == sorted(qtys)                                 # ascending regardless of input order
    assert qtys == [1, 10, 100]
    assert p["unit_price"] == 0.0069                            # the qty-1 rung
    # feeds straight into the existing volume-pricing helper
    assert LM.price_at_qty(p["price_breaks"], 250) == 0.0048


def test_parse_prices_ladder_fallback():
    """When the top-level `price` array is absent, use extra.prices (min_qty rungs)."""
    c = {"mfr": "PART", "extra": {"mpn": "PART", "prices": [
        {"min_qty": 100, "price": 0.01}, {"min_qty": 1, "price": 0.02}]}}
    p = LM._parse_lcsc_part(c)
    assert [b["qty"] for b in p["price_breaks"]] == [1, 100]
    assert p["unit_price"] == 0.02


def test_parse_partial_component_never_crashes():
    p = LM._parse_lcsc_part({"mfr": "BARE"})                    # no extra, no price, no stock
    assert p["mpn"] == "BARE"
    assert p["manufacturer"] == ""
    assert p["price_breaks"] == []
    assert p["unit_price"] is None
    assert p["stock"] == 0


def test_parse_string_manufacturer():
    p = LM._parse_lcsc_part({"mfr": "X", "extra": {"mpn": "X", "manufacturer": "Yageo"}})
    assert p["manufacturer"] == "Yageo"


# ── make_lcsc_lookup ──────────────────────────────────────────────────────────
def test_lookup_returns_normalized_hit(monkeypatch):
    monkeypatch.setattr(LM, "_lcsc_request", _canned([_FULL]))
    r = LM.make_lcsc_lookup()("GRM155R71C104KA88D")
    assert r["mpn"] == "GRM155R71C104KA88D"
    assert r["manufacturer"] == "Murata Electronics"


def test_lookup_prefers_exact_mpn_match(monkeypatch):
    other = {"mfr": "OTHER", "extra": {"mpn": "OTHER"}}
    monkeypatch.setattr(LM, "_lcsc_request", _canned([other, _FULL]))
    r = LM.make_lcsc_lookup()("grm155r71c104ka88d")            # case-insensitive
    assert r["mpn"] == "GRM155R71C104KA88D"                    # exact match wins over first result


def test_lookup_none_on_no_components(monkeypatch):
    monkeypatch.setattr(LM, "_lcsc_request", _canned([]))
    assert LM.make_lcsc_lookup()("NOPART") is None


def test_lookup_none_on_transport_error(monkeypatch):
    monkeypatch.setattr(LM, "_lcsc_request",
                        lambda query, timeout=8: {"data": None, "error": "timeout"})
    assert LM.make_lcsc_lookup()("ANY") is None


def test_lookup_none_on_empty_mpn(monkeypatch):
    called = []
    monkeypatch.setattr(LM, "_lcsc_request",
                        lambda query, timeout=8: called.append(query) or _canned([_FULL])(query))
    assert LM.make_lcsc_lookup()("") is None
    assert called == []                                        # never queries for an empty MPN


def test_lookup_never_raises(monkeypatch):
    def boom(query, timeout=8):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(LM, "_lcsc_request", boom)
    assert LM.make_lcsc_lookup()("ANY") is None                # swallowed, not fatal


# ── providers_from_config: LCSC as fallback + zero-config sourcing ────────────
def test_lcsc_enables_zero_config_sourcing(monkeypatch):
    """With NO Mouser key, the chain still works via LCSC — sourcing needs no config."""
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "read_setting", lambda key, default=None, **k: default)  # absent setting
    monkeypatch.setattr(LM, "_lcsc_request", _canned([_FULL]))
    chain = LM.providers_from_config({})                       # LCSC on by default
    assert chain is not None
    hit = chain("GRM155R71C104KA88D")
    assert hit["source"] == "LCSC"


def test_lcsc_disabled_leaves_no_providers(monkeypatch):
    """Disable LCSC AND no Mouser key AND no DigiKey creds -> no providers -> None.
    Isolate from any real config.json DigiKey creds (SRC-04 user path)."""
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: (None, None))
    assert LM.providers_from_config({"LcscSourcing": "0"}) is None


def test_mouser_preferred_lcsc_fallback(monkeypatch):
    """Mouser wins when it carries the part; LCSC covers what Mouser misses."""
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM, "read_setting", lambda key, default=None, **k: default)  # LCSC on
    monkeypatch.setattr(LM, "make_mouser_lookup",
                        lambda key, timeout=8: (lambda m: {"mpn": m} if m == "ON_MOUSER" else None))
    monkeypatch.setattr(LM, "_lcsc_request",
                        lambda query, timeout=8: {"data": {"components": [
                            {"mfr": query, "extra": {"mpn": query}}]}, "error": ""})
    chain = LM.providers_from_config({})
    assert chain("ON_MOUSER")["source"] == "Mouser"           # preferred provider wins
    assert chain("ONLY_LCSC")["source"] == "LCSC"             # fallback covers the gap
