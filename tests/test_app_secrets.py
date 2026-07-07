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


def test_providers_dead_when_truly_no_key(monkeypatch):
    monkeypatch.setattr(L.app_secrets, "MOUSER_API_KEY_DEFAULT", "")
    monkeypatch.delenv("MOUSER_API_KEY", raising=False)
    assert L.providers_from_config({}) is None
