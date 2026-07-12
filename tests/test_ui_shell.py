"""Tests for the clean-slate ui package: the feature registry contract, shell
construction, every panel building, and both themes applying."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_feature_registry_has_the_workspaces():
    from ui import feature as F
    from ui import features  # noqa: F401 - importing registers every feature
    ids = [f.id for f in F.features()]
    for expected in ("bench", "library", "projects", "git", "settings"):
        assert expected in ids
    orders = [f.order for f in F.features()]
    assert orders == sorted(orders)              # nav order is stable


def test_registry_add_and_replace_is_idempotent():
    from ui import feature as F
    from PyQt5.QtWidgets import QWidget

    class Tmp(F.Feature):
        id = "tmp_test"
        title = "Tmp"
        order = 1

        def build(self, ctx):
            return QWidget()

    before = len(F.features())
    F.register(Tmp())
    assert any(f.id == "tmp_test" for f in F.features())
    assert len(F.features()) == before + 1
    F.register(Tmp())                            # same id replaces, not duplicates
    assert sum(1 for f in F.features() if f.id == "tmp_test") == 1
    F._REGISTRY[:] = [f for f in F._REGISTRY if f.id != "tmp_test"]


def test_theme_tokens_and_toggle():
    from ui import theme as T
    for key in ("base", "surface", "card", "txt1", "txt2", "txt3", "accent", "divider"):
        assert key in T.DARK and key in T.LIGHT
    T.set_theme(True)
    assert T.is_dark() and T.tokens()["base"] == "#0b0b0b"   # neutral-glass shell base
    T.set_theme(False)
    assert not T.is_dark() and T.tokens()["base"] == "#e2e2e2"


def test_shell_builds_every_panel_and_both_themes():
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    from ui import widgets as W
    win = NetdeckShell(LM.load_config())
    try:
        assert win._stack.count() >= 4
        for i in range(win._stack.count()):
            win._select(i)                       # lazily build each workspace page
        for ws in win.findChildren(W.Workspace):
            for k in range(len(ws._panels)):
                ws._select(k)                    # forces the panel to build; must not raise
        win.apply_theme(False)
        win.apply_theme(True)
    finally:
        win.close()


def test_activity_console_hidden_by_default_and_toggles_from_the_nav():
    """The durable Activity log must NOT pin to the bottom by default (owner feedback):
    it is hidden until the nav-footer Activity toggle opens it, and while hidden it keeps
    an unseen-line count so activity stays discoverable."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    LM.write_setting("ConsoleVisible", "")           # default state (hidden)
    win = NetdeckShell(LM.load_config())
    try:
        assert win._console_open is False            # hidden by default
        assert win._console.isVisible() is False
        win._unseen_activity = 0                      # ignore any startup line
        win._sync_activity_item()
        win._log("build started")                    # a hidden log bumps the unseen badge
        win._log("wrote file")
        assert win._unseen_activity == 2
        assert win._activity_item._label == "Activity (2)"
        win._toggle_console()                        # the nav toggle opens it
        assert win._console_open is True
        assert win._unseen_activity == 0             # opening clears the badge
        assert win._activity_item._label == "Activity"
        assert win._activity_item.property("selected") is True
        win._toggle_console()                        # and closes it again
        assert win._console_open is False
    finally:
        win.close()


def test_search_matches_category_and_groups_under_eyebrows():
    """Ctrl+K matches a workspace by title OR category, and while searching the matches
    group under per-category eyebrows (the flat "Workspaces" header hides); an empty query
    restores the flat list. Uses isHidden() because the window is not shown in the suite."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    LM.write_setting("NavSearch", "")
    win = NetdeckShell(LM.load_config())
    try:
        def visible_ids():
            return [win._page_specs[i][0].id for i, it in enumerate(win._nav_items)
                    if not it.isHidden() and win._page_specs[i][0].id != "settings"]

        # default: flat list, the "Workspaces" header shows, category eyebrows hidden
        assert set(visible_ids()) == {"library", "projects", "bench", "git"}
        assert not win._eyebrow.isHidden()
        assert all(eb.isHidden() for eb in win._cat_eyebrows.values())

        # match by CATEGORY text ("Firmware") even though the title is "Bench"
        win._search.setText("firmware")
        assert visible_ids() == ["bench"]
        assert not win._cat_eyebrows["Firmware"].isHidden()   # its group header appears
        assert win._eyebrow.isHidden()                        # the flat header hides while searching

        # match by title
        win._search.setText("git")
        assert visible_ids() == ["git"]
        assert not win._cat_eyebrows["Version Control"].isHidden()

        # cleared → flat list restored, category headers hidden again
        win._search.setText("")
        assert set(visible_ids()) == {"library", "projects", "bench", "git"}
        assert not win._eyebrow.isHidden()
        assert all(eb.isHidden() for eb in win._cat_eyebrows.values())
    finally:
        win.close()


def test_search_did_you_mean_on_zero_results():
    """A query that matches nothing surfaces a 'Did you mean <name>?' link (difflib) that,
    when adopted, becomes the query and now matches."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    LM.write_setting("NavSearch", "")
    win = NetdeckShell(LM.load_config())
    try:
        win._search.setText("libary")            # a typo of "Library"
        assert not win._did_you_mean.isHidden()
        assert win._suggestion == "Library"
        assert "Library" in win._did_you_mean.text()
        win._adopt_suggestion()                  # clicking the link adopts the suggestion
        assert win._search.text() == "Library"
        assert win._did_you_mean.isHidden()      # now it matches → the link hides
        # a query that matches leaves the link hidden and no stale suggestion
        win._search.setText("bench")
        assert win._did_you_mean.isHidden()
        assert win._suggestion == ""
    finally:
        win.close()


def test_search_text_persists_across_relaunch():
    """The Ctrl+K query persists across launches (paired with pane-width persistence):
    a saved query is restored and applied on init; a saved query that now matches nothing
    is cleared silently so the nav is never left empty and unexplained."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    try:
        # a valid saved query is restored AND applied (the nav opens filtered)
        LM.write_setting("NavSearch", "bench")
        win = NetdeckShell(LM.load_config())
        assert win._search.text() == "bench"
        vis = [win._page_specs[i][0].id for i, it in enumerate(win._nav_items)
               if not it.isHidden() and win._page_specs[i][0].id != "settings"]
        assert vis == ["bench"]
        # a fresh edit persists through the single writer
        win._search.setText("git")
        win._persist_search()
        assert LM.read_setting("NavSearch", "") == "git"
        # clearing via a path that bypasses editingFinished (Ctrl+B collapse clears the
        # field; the clear-button X calls clear()) must ALSO persist the emptied query, or a
        # stale query would wrongly re-apply next launch (adversarial-review finding).
        win._toggle_nav()                        # collapse clears the search field
        assert win._search.text() == ""
        assert LM.read_setting("NavSearch", "") == ""
        win._toggle_nav()
        win._search.setText("bench")
        win._persist_search()
        win._search.clear()                      # the clear-button X path
        assert LM.read_setting("NavSearch", "") == ""
        win.close()

        # a stale query that matches nothing is cleared silently on the next launch
        LM.write_setting("NavSearch", "zzznomatch")
        win2 = NetdeckShell(LM.load_config())
        assert win2._search.text() == ""
        vis2 = [win2._page_specs[i][0].id for i, it in enumerate(win2._nav_items)
                if not it.isHidden() and win2._page_specs[i][0].id != "settings"]
        assert set(vis2) == {"library", "projects", "bench", "git"}
        win2.close()
    finally:
        LM.write_setting("NavSearch", "")


def test_error_log_auto_expands_console_but_only_when_enabled():
    """An 'Error:'-prefixed log line auto-opens + expands the Activity console so a failure
    can't scroll away unseen — but ONLY when _auto_surface_errors is on (it is off under
    headless so CI/render-gate stay deterministic)."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    LM.write_setting("ConsoleVisible", "")
    win = NetdeckShell(LM.load_config())
    try:
        assert win._auto_surface_errors is False     # headless default: gated OFF
        win._console_open = False; win._console.setVisible(False); win._unseen_activity = 0
        win._log("Error: something broke")
        assert win._console_open is False            # gated off → stays hidden
        assert win._unseen_activity == 1             # just bumps the unseen badge

        # with the gate on, the same line surfaces the console
        win._auto_surface_errors = True
        win._log("Error: another failure")
        assert win._console_open is True
        assert win._console.is_expanded() is True
        assert win._unseen_activity == 0             # surfacing clears the badge
        # a non-error line does NOT force the console open
        win._console_open = False; win._console.setVisible(False); win._unseen_activity = 0
        win._log("wrote file")
        assert win._console_open is False
        assert win._unseen_activity == 1
    finally:
        win.close()


def test_shortcuts_reference_enumerates_bound_shortcuts():
    """The keyboard-shortcuts reference enumerates every bound QShortcut app-wide with its
    key + description (from the 'shortcutHelp' property), and builds a non-empty dialog."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    win = NetdeckShell(LM.load_config())
    try:
        rows = dict(win._iter_shortcuts())
        assert rows.get("Ctrl+K") == "Search workspaces"
        assert rows.get("Ctrl+B") == "Collapse or expand the navigation"
        assert rows.get("Ctrl+/") == "Show keyboard shortcuts"
        assert "Esc" in rows                          # the search-scoped clear
        dlg = win._show_shortcuts()                   # headless → built but not exec'd
        assert dlg is not None
        from PyQt5.QtWidgets import QLabel
        texts = {lbl.text() for lbl in dlg.findChildren(QLabel)}
        assert "Ctrl+K" in texts and "Search workspaces" in texts
        dlg.deleteLater()
    finally:
        win.close()


def test_theme_button_label_and_icon_stay_in_sync():
    """_sync_theme_btn is the single source of truth for the theme button's label +
    glyph; toggling the theme and collapsing the nav both route through it, so the
    three former copies of the label/icon rule can't drift."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    win = NetdeckShell(LM.load_config())
    try:
        win.apply_theme(True)
        assert win._theme_btn.text() == "Dark Theme"
        win.apply_theme(False)
        assert win._theme_btn.text() == "Light Theme"
        # collapsing clears the label but keeps the button
        win._toggle_nav()
        assert win._nav_collapsed is True
        assert win._theme_btn.text() == ""
        win._toggle_nav()
        assert win._theme_btn.text() == "Light Theme"
        # an icon is always set (moon in dark, sun in light — never blank)
        assert not win._theme_btn.icon().isNull()
    finally:
        win.close()


def test_system_mode_follows_a_live_os_theme_flip(monkeypatch):
    """In 'System' mode the shell repaints when the OS dark/light preference flips
    while the app is open; in an explicit mode it ignores the OS entirely."""
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui import theme as T
    from ui.shell import NetdeckShell
    monkeypatch.setattr(LM, "read_setting",
                        lambda key, default=None, config_path=None:
                        "System" if key == "Theme" else default)
    os_state = {"dark": True}
    monkeypatch.setattr(T, "os_dark", lambda: os_state["dark"])
    win = NetdeckShell(LM.load_config())
    try:
        assert win._theme_mode == "System"
        assert win._dark is True                 # launched dark (OS was dark)
        # OS flips to light while running → the shell follows
        os_state["dark"] = False
        assert win._maybe_follow_os_theme() is True
        assert win._dark is False
        # no change on a repeat call (idempotent, cheap on every broadcast)
        assert win._maybe_follow_os_theme() is False
        # an explicit mode ignores the OS entirely
        win._set_theme_mode("Dark")
        os_state["dark"] = False
        assert win._maybe_follow_os_theme() is False
        assert win._dark is True
    finally:
        win.close()


def test_download_progress_falls_back_to_indeterminate_when_no_total():
    """With no asset size the progress dialog goes indeterminate (busy bar) with a
    running byte counter instead of freezing at 0%; a real total restores the %."""
    _app()
    from PyQt5.QtWidgets import QProgressDialog
    from ui.shell import NetdeckShell
    dlg = QProgressDialog("Downloading update…", "Cancel", 0, 100)
    try:
        # total unknown → indeterminate range (0,0), label shows bytes, not a %
        NetdeckShell._on_download_progress(dlg, 3_355_443, 0)
        assert dlg.maximum() == 0                 # busy/indeterminate
        assert "3.2 MB" in dlg.labelText()
        # a real total arrives → determinate percentage restored
        NetdeckShell._on_download_progress(dlg, 5_000_000, 10_000_000)
        assert dlg.maximum() == 100
        assert dlg.value() == 50
    finally:
        dlg.close()


def test_fmt_bytes_scales_units():
    from ui.shell import NetdeckShell
    assert NetdeckShell._fmt_bytes(512) == "512 B"
    assert NetdeckShell._fmt_bytes(1536) == "1.5 KB"
    assert NetdeckShell._fmt_bytes(3_355_443) == "3.2 MB"


def test_bench_pin_category_from_real_authority():
    import stm32_db as db
    import stm32_authority as sauth
    import stm32_pins_tab as pins
    dbp = db.default_db_path()
    if not dbp.exists():
        pytest.skip("stm32 database not built")
    conn = db.connect(dbp)
    try:
        a = sauth.build(conn, "LQFP64")
        geo = pins.pin_map_geometry(a["positions"], 460, 460)
        assert len(geo["pins"]) == 64
        from ui.features.bench import _pin_category
        cats = {_pin_category(p) for p in a["positions"]}
        assert cats <= {"power", "ground", "core", "service", "lane", "must", "osc"}
    finally:
        conn.close()
