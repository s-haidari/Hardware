"""Live Mouser search: a genuine no-match must be distinguishable from a transport
failure (429 / timeout / network / bad key). Before this, _mouser_post swallowed
every error into None, so search_parts returned an empty result identically whether
Mouser had nothing or the call failed — the typeahead could not tell the user
"no results" from "rate limited, try again".
"""
import sys
import urllib.error
from pathlib import Path

import pytest

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


def _patch_urlopen(monkeypatch, fn):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fn)


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    # Force a resolvable key so search_parts reaches the network layer.
    monkeypatch.setenv("MOUSER_API_KEY", "testkey")
    # Isolate the daily-cap marker write (note_mouser_rate_limited) to a temp config so
    # a rate-limit test never pollutes the real tools/config.json (SRC-04 countdown).
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")
    return None


_ONE_PART = ('{"SearchResults": {"Parts": [{"ManufacturerPartNumber": "STM32F407VGT6",'
             '"Manufacturer": "STMicroelectronics", "AvailabilityInStock": "421",'
             '"PriceBreaks": [{"Price": 8.12}], "LifecycleStatus": "Active"}]}}')


def test_search_returns_normalized_results_on_success(keyed, monkeypatch):
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp(_ONE_PART))
    r = LM.search_parts("STM32F407", limit=5)
    assert r["error"] == "" and r.get("error_code", "") == ""
    assert len(r["results"]) == 1
    assert r["results"][0]["mpn"] == "STM32F407VGT6"
    assert r["results"][0]["stock"] == 421


def test_genuine_no_match_is_not_an_error(keyed, monkeypatch):
    # Empty Parts, no Errors[] -> a real "nothing found", NOT a failure.
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp('{"SearchResults": {"Parts": []}}'))
    r = LM.search_parts("zzzznotapart", limit=5)
    assert r["results"] == []
    assert r["error"] == "" and r.get("error_code", "") == ""


def test_rate_limit_is_flagged(keyed, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("http://mouser", 429, "Too Many Requests", {}, None)
    _patch_urlopen(monkeypatch, boom)
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == []
    assert r["error_code"] == "rate_limited"
    assert r["error"]                                   # a human message, not empty


def test_network_failure_is_flagged(keyed, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no route to host")
    _patch_urlopen(monkeypatch, boom)
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == []
    assert r["error_code"] == "network"
    assert r["error"]


def test_timeout_is_flagged(keyed, monkeypatch):
    import socket

    def boom(*a, **k):
        raise socket.timeout("timed out")
    _patch_urlopen(monkeypatch, boom)
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == []
    assert r["error_code"] == "timeout"


def test_generic_body_error_is_api_with_raw_message(keyed, monkeypatch):
    # HTTP 200 with an unrecognised error in the body -> "api" + Mouser's own text.
    body = '{"Errors": [{"Message": "Something odd happened."}], "SearchResults": null}'
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp(body))
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == [] and r["error_code"] == "api"
    assert "Something odd" in r["error"]


def test_bad_key_in_200_body_is_auth(keyed, monkeypatch):
    # Mouser's bad-key signal is a 200 body 'Invalid unique identifier.', not a 401.
    body = '{"Errors": [{"Message": "Invalid unique identifier."}], "SearchResults": null}'
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp(body))
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == [] and r["error_code"] == "auth"


def test_rate_limit_in_200_body_is_rate_limited(keyed, monkeypatch):
    # Mouser throttling often arrives as a 200 body, not a 429 — must still back off.
    body = '{"Errors": [{"Message": "Too many requests. Rate limit exceeded."}], "SearchResults": null}'
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp(body))
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == [] and r["error_code"] == "rate_limited"


def test_daily_cap_403_body_is_rate_limited_not_auth(keyed, monkeypatch, tmp_path):
    # SRC-04: the free shared key's daily cap comes back as HTTP 403 with an Errors[]
    # body of "TooManyRequests"/"MaxCallPerDay". It MUST classify as rate_limited (a
    # recoverable throttle worth a countdown), NOT auth ("Mouser rejected the key").
    monkeypatch.setattr(LM, "CONFIG_PATH", tmp_path / "config.json")

    class _CapError(urllib.error.HTTPError):
        def __init__(self):
            self.code = 403
        def read(self):
            return (b'{"Errors":[{"Code":"TooManyRequests",'
                    b'"Message":"Maximum calls per day exceeded."}],"SearchResults":null}')

    _patch_urlopen(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(_CapError()))
    r = LM.search_parts("STM32", limit=5)
    assert r["results"] == [] and r["error_code"] == "rate_limited"
    # and the cap is recorded so the UI can count down to the reset
    assert LM.mouser_reset_seconds_remaining(tmp_path / "config.json") > 0


def test_mouser_reset_countdown_absent_and_present(tmp_path):
    cfg = tmp_path / "config.json"
    assert LM.mouser_reset_seconds_remaining(cfg) is None      # never capped -> unknown
    LM.note_mouser_rate_limited(cfg)
    secs = LM.mouser_reset_seconds_remaining(cfg)
    assert isinstance(secs, int) and 0 < secs <= 24 * 3600     # within one day of reset


def test_bad_key_message_still_mentions_mouser(monkeypatch):
    # Contract kept for the existing suite: no key -> a Mouser-mentioning error.
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(LM.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    r = LM.search_parts("anything", {})
    assert r["results"] == [] and "Mouser" in r["error"]


def test_mouser_post_stays_json_or_none(keyed, monkeypatch):
    # Backward-compat: the exact-lookup path still sees parsed JSON or None.
    _patch_urlopen(monkeypatch, lambda *a, **k: _FakeResp(_ONE_PART))
    assert LM._mouser_post("keyword", "k", {}) is not None

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://mouser", 429, "x", {}, None)
    _patch_urlopen(monkeypatch, boom)
    assert LM._mouser_post("keyword", "k", {}) is None
