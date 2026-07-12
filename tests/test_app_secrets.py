"""Key-resolution tests for SP1's baked Mouser key.

SP1 decision #3: the app uses a baked-in Mouser key (tools/app_secrets.py
MOUSER_API_KEY_DEFAULT). The MOUSER_API_KEY environment variable stays as a
silent override. The old config.json 'MouserApiKey' is dropped from the chain.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402


def test_env_var_wins_over_baked_default(monkeypatch):
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.setenv("MOUSER_API_KEY", "FROM_ENV")
    assert L.resolve_mouser_key() == "FROM_ENV"


def test_baked_default_used_when_no_env(monkeypatch):
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert L.resolve_mouser_key() == "BAKED"


def test_config_key_is_ignored(monkeypatch):
    """The config.json key is dropped from the resolution chain (decision #3)."""
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert L.resolve_mouser_key({"MouserApiKey": "STALE_CONFIG"}) == "BAKED"


def test_empty_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert not L.resolve_mouser_key()


def test_providers_live_with_baked_key(monkeypatch):
    """providers_from_config must never return None just because config has no key."""
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "BAKED")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert L.providers_from_config({}) is not None


def test_lcsc_gives_zero_config_sourcing(monkeypatch):
    """No Mouser key still yields a live chain — LCSC (key-free) is the default fallback."""
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(L, "read_setting", lambda key, default=None, **k: default)  # absent setting
    assert L.providers_from_config({}) is not None


def test_providers_dead_when_no_key_and_lcsc_off(monkeypatch):
    """None only when there is genuinely no provider: no Mouser key AND LCSC disabled
    AND no DigiKey creds. Isolate from any real config.json creds (SRC-04 user path)."""
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    monkeypatch.setattr(L, "resolve_digikey_creds", lambda cfg=None: (None, None))
    assert L.providers_from_config({"LcscSourcing": "0"}) is None


# --- DigiKey creds: NOT CI-baked, user-supplied (env var / config.json) --------------
# app_secrets.py documents (module docstring + DIGIKEY_* comment) that these creds are
# never committed AND never baked by CI, unlike the Mouser key and the write tokens.
# These tests pin that contract to the real resolve_digikey_creds resolution order so the
# docstring can't silently drift back to promising a CI bake that build-exe.yml never does.
import app_secrets  # noqa: E402


def _clear_digikey_env(monkeypatch):
    monkeypatch.delenv("DIGIKEY_CLIENT_ID", raising=False)
    monkeypatch.delenv("DIGIKEY_CLIENT_SECRET", raising=False)


def test_digikey_defaults_are_none_in_source():
    """The baked defaults ship as None — billed per-user creds are never committed and
    (unlike updater/git tokens) are never baked by CI, so the source value must stay None."""
    assert app_secrets.DIGIKEY_CLIENT_ID_DEFAULT is None
    assert app_secrets.DIGIKEY_CLIENT_SECRET_DEFAULT is None


def test_digikey_creds_none_when_nothing_configured(monkeypatch):
    """Fresh install (no env var, no config.json, None baked defaults) -> (None, None),
    i.e. DigiKey is simply not registered. This is what a shipped exe actually sees."""
    _clear_digikey_env(monkeypatch)
    monkeypatch.setattr(L, "read_setting", lambda key, default=None, **k: default)
    monkeypatch.setattr(L.app_secrets, "DIGIKEY_CLIENT_ID_DEFAULT", None)
    monkeypatch.setattr(L.app_secrets, "DIGIKEY_CLIENT_SECRET_DEFAULT", None)
    assert L.resolve_digikey_creds() == (None, None)


def test_digikey_creds_from_config_user_path(monkeypatch):
    """The user path: creds saved by in-app Settings into config.json (read_setting),
    resolved when no env override and no baked default exist."""
    _clear_digikey_env(monkeypatch)
    monkeypatch.setattr(L.app_secrets, "DIGIKEY_CLIENT_ID_DEFAULT", None)
    monkeypatch.setattr(L.app_secrets, "DIGIKEY_CLIENT_SECRET_DEFAULT", None)
    saved = {"DigiKeyClientId": "cfg-id", "DigiKeyClientSecret": "cfg-secret"}
    monkeypatch.setattr(L, "read_setting", lambda key, default=None, **k: saved.get(key, default))
    assert L.resolve_digikey_creds() == ("cfg-id", "cfg-secret")


def test_digikey_env_wins_over_config(monkeypatch):
    """The DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET env vars are the silent dev override
    and win over the config.json user path."""
    monkeypatch.setenv("DIGIKEY_CLIENT_ID", "env-id")
    monkeypatch.setenv("DIGIKEY_CLIENT_SECRET", "env-secret")
    saved = {"DigiKeyClientId": "cfg-id", "DigiKeyClientSecret": "cfg-secret"}
    monkeypatch.setattr(L, "read_setting", lambda key, default=None, **k: saved.get(key, default))
    assert L.resolve_digikey_creds() == ("env-id", "env-secret")
