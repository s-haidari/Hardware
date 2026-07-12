"""Wave 0 — Settings/theme wiring + persistence + updater nav badge.

Covers the fixes for SET-02 / SHELL-04 (the dead Settings theme buttons, theme
persistence across launches, and a Follow-Windows "System" mode), SET-03 (the
removed Selection Accent control), and SET-04 (a persistent nav "update
available" affordance instead of a modal-only notice).

Headless: pure logic (theme mode resolution, raw config read/write) needs no Qt;
the Settings-panel and shell wiring build under the offscreen QApplication.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ── theme mode resolution (pure) ──────────────────────────────────────────────
def test_resolve_dark_explicit_modes():
    from ui import theme as T
    assert T.resolve_dark("dark") is True
    assert T.resolve_dark("light") is False
    # case-insensitive; an unknown mode falls back to dark
    assert T.resolve_dark("Dark") is True
    assert T.resolve_dark("Light") is False
    assert T.resolve_dark("nonsense") is True


def test_resolve_dark_system_follows_os():
    from ui import theme as T
    assert T.resolve_dark("system", os_is_dark=True) is True
    assert T.resolve_dark("system", os_is_dark=False) is False
    # when the OS preference is unknown (e.g. off-Windows), System falls back to dark
    assert T.resolve_dark("system", os_is_dark=None) is True


def test_os_dark_is_guarded_and_never_raises():
    from ui import theme as T
    val = T.os_dark()                       # off-Windows this is None; must not raise
    assert val in (None, True, False)


# ── raw config read/write (theme persistence seam) ────────────────────────────
def test_write_then_read_setting_round_trips(tmp_path):
    import LibraryManager as LM
    cfgp = tmp_path / "config.json"
    assert LM.read_setting("Theme", "Dark", config_path=cfgp) == "Dark"   # default when absent
    assert LM.write_setting("Theme", "Light", config_path=cfgp) is True
    assert LM.read_setting("Theme", "Dark", config_path=cfgp) == "Light"


def test_write_setting_preserves_other_keys(tmp_path):
    import json
    import LibraryManager as LM
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"RepoRoot": "/some/root", "PythonExe": "/usr/bin/python"}))
    LM.write_setting("Theme", "System", config_path=cfgp)
    data = json.loads(cfgp.read_text())
    assert data["Theme"] == "System"
    assert data["RepoRoot"] == "/some/root"     # untouched
    assert data["PythonExe"] == "/usr/bin/python"


# ── Settings panel wiring (SET-02 wired, SET-03 removed) ───────────────────────
def test_settings_theme_segment_emits_bus_topic_and_accent_is_gone():
    _app()
    import LibraryManager as LM
    from ui import feature as F
    from ui import theme as T
    from ui import widgets as W
    from ui.features import settings as S

    seen = []
    bus = F.EventBus()
    bus.on("theme.set_mode", lambda mode: seen.append(mode))

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=bus)
    panel = S._settings_panel(ctx, None)

    segs = panel.findChildren(W.Segmented)
    # Theme + Length Units + LCSC Fallback — the Selection Accent control stays gone (SET-03).
    assert len(segs) == 3, "Theme + Units + LCSC segmented controls belong here (no Accent)"
    labels = [[b.text() for b in s._buttons] for s in segs]
    assert ["Dark", "Light", "System"] in labels, "Theme control present"
    assert ["mm", "mils"] in labels, "Length Units control present (WS-A)"
    assert ["On", "Off"] in labels, "LCSC Fallback control present"

    theme_seg = next(s for s in segs
                     if [b.text() for b in s._buttons] == ["Dark", "Light", "System"])
    theme_seg._pick(1)                          # the "Light" segment
    assert seen == ["Light"], "Theme buttons must emit theme.set_mode (SET-02)"


def test_settings_theme_segment_resyncs_on_theme_changed_broadcast():
    """When the theme is toggled elsewhere (e.g. the nav-rail button), the shell
    broadcasts 'theme.changed'; the Settings Theme segment must re-select silently so
    it never goes stale (finding settings:94), mirroring the Units segment."""
    _app()
    import LibraryManager as LM
    from ui import feature as F
    from ui import theme as T
    from ui import widgets as W
    from ui.features import settings as S

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=F.EventBus())
    panel = S._settings_panel(ctx, None)
    theme_seg = next(s for s in panel.findChildren(W.Segmented)
                     if [b.text() for b in s._buttons] == ["Dark", "Light", "System"])

    ctx.bus.emit("theme.changed", "Light")      # changed via the nav toggle, not the segment
    assert theme_seg._buttons[1].property("selected") is True   # "Light" now selected
    assert theme_seg._buttons[0].property("selected") is False
    ctx.bus.emit("theme.changed", "System")
    assert theme_seg._buttons[2].property("selected") is True


def _sourcing_labels(monkeypatch):
    """Every QLabel text in a freshly built Settings panel (for Sourcing-row asserts)."""
    _app()
    import LibraryManager as LM
    from PyQt5.QtWidgets import QLabel
    from ui import feature as F
    from ui import theme as T
    from ui.features import settings as S

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    # Keep the Mouser row deterministic: not rate-limited, so its text doesn't shift
    # based on the real config.json cap marker (SRC-04 countdown).
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: None)
    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=F.EventBus())
    panel = S._settings_panel(ctx, None)
    return [lbl.text() for lbl in panel.findChildren(QLabel)]


def test_settings_shows_digikey_row_inactive_without_creds(monkeypatch):
    """DigiKey is surfaced in Sourcing like Mouser/LCSC; absent creds it reads inactive."""
    import LibraryManager as LM
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: (None, None))
    texts = _sourcing_labels(monkeypatch)
    assert any(t == "DigiKey" for t in texts), "DigiKey row present in Sourcing"
    dk_status = next(t for t in texts if "DigiKey" in t and t != "DigiKey")
    assert "not configured" in dk_status.lower()


def test_settings_shows_digikey_row_active_with_creds(monkeypatch):
    import LibraryManager as LM
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: ("id", "secret"))
    texts = _sourcing_labels(monkeypatch)
    dk_status = next(t for t in texts if "DigiKey" in t and t != "DigiKey")
    assert "ready" in dk_status.lower() or "active" in dk_status.lower()


# ── Shared fmt_countdown (dedup: one impl in ui.util, imported everywhere) ─────
def test_fmt_countdown_formats_span():
    from ui.util import fmt_countdown
    assert fmt_countdown(0) == "any moment now"
    assert fmt_countdown(-5) == "any moment now"
    assert fmt_countdown(30) == "under a minute"
    assert fmt_countdown(12 * 60) == "12m"
    assert fmt_countdown(3 * 3600 + 12 * 60) == "3h 12m"
    assert fmt_countdown(2 * 3600) == "2h"          # no trailing 0m


def test_fmt_countdown_is_deduped_to_ui_util():
    """The Settings / Mouser-search / library-preview features must all reference the
    single ui.util.fmt_countdown — no local re-definition drifts out of sync."""
    from ui import util as UU
    from ui.features import settings as S
    from ui.features import mouser_search as MS
    from ui.features import library_preview as LP

    assert S.fmt_countdown is UU.fmt_countdown
    assert MS.fmt_countdown is UU.fmt_countdown
    assert LP.fmt_countdown is UU.fmt_countdown
    # no lingering private copies in the feature modules
    for mod in (S, MS, LP):
        assert not hasattr(mod, "_fmt_countdown"), f"{mod.__name__} still has a local copy"


# ── Shell wiring: apply + persist + startup read + update badge ────────────────
def _shell(monkeypatch, *, persisted="Dark"):
    import LibraryManager as LM
    # isolate persistence from the real config.json
    written = {}
    monkeypatch.setattr(LM, "read_setting",
                        lambda key, default=None, config_path=None: persisted if key == "Theme" else default)
    monkeypatch.setattr(LM, "write_setting",
                        lambda key, value, config_path=None: written.__setitem__(key, value) or True)
    from ui.shell import NetdeckShell
    win = NetdeckShell(LM.load_config())
    return win, written


def test_shell_starts_from_persisted_theme(monkeypatch):
    _app()
    win, _ = _shell(monkeypatch, persisted="Light")
    assert win._dark is False and win._theme_mode == "Light"
    win.close()


def test_shell_nav_order(monkeypatch):
    """ONE nav order (Library→Projects→Bench→Git, Settings footer). Routing was removed
    entirely (feature + Rust engine + docs, 2026-07-10), so it is no longer a nav row.
    The general disabled-feature mechanism (Feature.enabled / disabled_tip) stays for any
    future shelved feature, but nothing currently uses it."""
    _app()
    win, _ = _shell(monkeypatch)
    ids = [spec[0].id for spec in win._page_specs]
    assert ids == ["library", "projects", "bench", "git", "settings"]
    assert "routing" not in ids
    # every live nav row is enabled + opens (no shelved rows remain)
    for i, spec in enumerate(win._page_specs):
        assert win._nav_items[i].isEnabled(), f"{spec[0].id} nav row must be enabled"
    win.close()


def test_shell_theme_bus_applies_and_persists(monkeypatch):
    _app()
    win, written = _shell(monkeypatch, persisted="Dark")
    assert win._dark is True
    win.ctx.bus.emit("theme.set_mode", "Light")
    assert win._dark is False and win._theme_mode == "Light"
    assert written.get("Theme") == "Light"      # SHELL-04: choice persists
    win.close()


def test_shell_theme_change_broadcasts_theme_changed(monkeypatch):
    """The single writer (_set_theme_mode) must broadcast 'theme.changed' so a live
    control — e.g. Settings' Theme segment — can re-sync, mirroring 'units.changed'.
    Both the command route (Settings) and the nav toggle flow through it."""
    _app()
    win, _ = _shell(monkeypatch, persisted="Dark")
    heard = []
    win.ctx.bus.on("theme.changed", lambda m: heard.append(m))

    # command route (Settings Theme control emits theme.set_mode)
    win.ctx.bus.emit("theme.set_mode", "Light")
    assert heard == ["Light"], "theme.set_mode must broadcast theme.changed"

    # nav toggle route (flips explicit mode, must broadcast too so Settings restays synced)
    win._toggle_theme()
    assert heard == ["Light", "Dark"], "nav toggle must broadcast theme.changed"
    win.close()


def test_apply_theme_flips_the_active_theme(monkeypatch):
    """apply_theme drives ui.theme (the one active theme) via _apply_theme_now's
    T.set_theme(dark), so the global theme tracks the shell. (Was the legacy-shim
    sync check; ui_theme.py is retired, so this asserts the real target directly.)"""
    _app()
    from ui import theme as T
    win, _ = _shell(monkeypatch, persisted="Dark")
    win.apply_theme(True)
    assert T.is_dark() is True
    win.apply_theme(False)
    assert T.is_dark() is False
    win.apply_theme(True)                    # leave a deterministic active theme
    win.close()


def test_shell_update_badge_hidden_until_pending(monkeypatch):
    _app()
    win, _ = _shell(monkeypatch)
    assert win._update_item.isHidden() is True   # no update -> no badge
    assert win._pending_update is None
    win._set_pending_update({"version": "v9.9.9", "url": "http://x"})
    assert win._pending_update["version"] == "v9.9.9"
    assert win._update_item.isHidden() is False   # SET-04: persistent nav affordance
    win.close()


def test_shell_titlebar_tint_is_guarded(monkeypatch):
    _app()
    win, _ = _shell(monkeypatch)
    # dark native title bar (SHELL-01, rescoped): DWM tint on Windows; a guarded
    # no-op returning False elsewhere. Must never raise.
    assert win._set_titlebar_theme(True) in (True, False)
    assert win._set_titlebar_theme(False) in (True, False)
    win.close()
