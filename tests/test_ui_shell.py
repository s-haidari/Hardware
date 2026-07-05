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
    for expected in ("bench", "library", "projects", "settings"):
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
    assert T.is_dark() and T.tokens()["base"] == "#202020"
    T.set_theme(False)
    assert not T.is_dark() and T.tokens()["base"] == "#f3f3f3"


def test_shell_builds_every_panel_and_both_themes():
    _app().setStyle("Fusion")
    import LibraryManager as LM
    from ui.shell import NetdeckShell
    from ui import widgets as W
    win = NetdeckShell(LM.load_config())
    try:
        assert win._stack.count() >= 4
        for ws in win.findChildren(W.Workspace):
            for k in range(len(ws._panels)):
                ws._select(k)                    # forces the panel to build; must not raise
        win.apply_theme(False)
        win.apply_theme(True)
    finally:
        win.close()


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
