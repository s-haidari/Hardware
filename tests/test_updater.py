"""Tests for the in-app exe auto-updater (tools/nd_updater.py).

Covers the pure decision logic (version parse/compare, asset selection, the
newer-release gate) and the Windows swap-script generation. Network + process
side effects are exercised only via injected fetchers / pure builders, so the
suite stays headless and deterministic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_updater as U  # noqa: E402


def test_parse_version_variants():
    assert U.parse_version("v2.1.0") == (2, 1, 0)
    assert U.parse_version("2.1") == (2, 1, 0)
    assert U.parse_version("v10.0.3") == (10, 0, 3)
    assert U.parse_version("2.1.0-rc1") == (2, 1, 0)
    assert U.parse_version("dev") == (0, 0, 0)
    assert U.parse_version("") == (0, 0, 0)


def test_is_newer():
    assert U.is_newer("v2.0.0", "1.2.0")
    assert U.is_newer("v1.2.1", "v1.2.0")
    assert U.is_newer("v2.0.0", "dev")          # a real release beats a dev build
    assert not U.is_newer("v1.2.0", "v1.2.0")
    assert not U.is_newer("v1.1.9", "v1.2.0")


def _release(tag="v2.0.0", name="KiCad Manager.exe"):
    return {
        "tag_name": tag,
        "body": "Notes here",
        "assets": [
            {"name": "other.txt", "browser_download_url": "http://x/other",
             "url": "http://api/other", "size": 1},
            {"name": name, "browser_download_url": "http://x/km.exe",
             "url": "http://api/assets/9", "size": 4242},
        ],
    }


def test_pick_asset():
    dl, api, size = U.pick_asset(_release())
    assert dl == "http://x/km.exe"
    assert api == "http://api/assets/9"
    assert size == 4242


def test_pick_asset_github_dots_for_spaces():
    # GitHub stores 'KiCad Manager.exe' as 'KiCad.Manager.exe' — must still match.
    rel = _release(name="KiCad.Manager.exe")
    dl, api, size = U.pick_asset(rel, name="KiCad Manager.exe")
    assert dl == "http://x/km.exe" and api == "http://api/assets/9"


def test_pick_asset_falls_back_to_sole_exe():
    rel = {"tag_name": "v2", "assets": [
        {"name": "notes.txt", "browser_download_url": "u1", "url": "a1", "size": 1},
        {"name": "Totally-Renamed.exe", "browser_download_url": "u2", "url": "a2", "size": 9},
    ]}
    assert U.pick_asset(rel, name="KiCad Manager.exe") == ("u2", "a2", 9)


def test_pick_asset_none_when_no_exe():
    rel = {"tag_name": "v2", "assets": [
        {"name": "notes.txt", "browser_download_url": "u", "url": "a", "size": 1}]}
    assert U.pick_asset(rel, name="KiCad Manager.exe") == (None, None, 0)


def test_evaluate_release_newer():
    upd = U.evaluate_release(_release("v2.0.0"), "1.2.0")
    assert upd is not None
    assert upd["version"] == "v2.0.0"
    assert upd["url"] == "http://x/km.exe"
    assert upd["size"] == 4242


def test_evaluate_release_not_newer_or_no_asset():
    assert U.evaluate_release(_release("v1.0.0"), "1.2.0") is None      # older
    assert U.evaluate_release(_release("v1.2.0"), "1.2.0") is None      # equal
    no_asset = {"tag_name": "v9.9.9", "assets": []}
    assert U.evaluate_release(no_asset, "1.2.0") is None                # newer, no asset


def test_check_for_update_uses_injected_fetch():
    upd = U.check_for_update("1.2.0", fetch=lambda _url: _release("v2.0.0"))
    assert upd and upd["version"] == "v2.0.0"
    # up to date -> None
    assert U.check_for_update("2.0.0", fetch=lambda _url: _release("v2.0.0")) is None


def test_check_for_update_dev_gate():
    # a dev checkout does not auto-nag ...
    assert U.check_for_update("dev", fetch=lambda _url: _release("v2.0.0")) is None
    # ... unless explicitly allowed (the manual "Check for updates" path)
    upd = U.check_for_update("dev", fetch=lambda _url: _release("v2.0.0"), allow_dev=True)
    assert upd and upd["version"] == "v2.0.0"


def test_check_for_update_swallows_errors():
    def boom(_url):
        raise RuntimeError("offline")
    assert U.check_for_update("1.2.0", fetch=boom) is None


def test_swap_script_shape():
    script = U._swap_script(1234, Path(r"C:\a\KiCad Manager.exe.new"),
                            Path(r"C:\a\KiCad Manager.exe"))
    assert "PID eq 1234" in script
    assert 'move /Y "C:\\a\\KiCad Manager.exe.new" "C:\\a\\KiCad Manager.exe"' in script
    assert 'start "" "C:\\a\\KiCad Manager.exe"' in script
    assert script.endswith('del "%~f0"\r\n')


def test_apply_update_noops_off_windows(tmp_path, monkeypatch):
    # not frozen + not Windows -> returns False, never launches anything
    monkeypatch.setattr(U.os, "name", "posix")
    newexe = tmp_path / "KiCad Manager.exe.new"
    newexe.write_text("x")
    assert U.apply_update_windows(newexe, target=tmp_path / "KiCad Manager.exe") is False


def test_current_version_is_str():
    assert isinstance(U.current_version(), str)
