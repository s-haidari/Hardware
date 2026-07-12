"""Wave 1 · PCB-09 (UI) — the profile picker + CRUD wired into PCB Setup.

The PCB Setup panel now picks a profile from a dropdown (built-ins + user
profiles) instead of the old two-option fab segmented control. Selecting a bare
OSH Park profile empties the net-class table; selecting NETDECK loads the full
taxonomy. New / Save / Delete manage user profiles, persisted via nd_pcb_profiles
(here redirected to a tmp file so the repo is never touched).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QComboBox  # noqa: E402
import nd_pcb_profiles as pcbprof  # noqa: E402
from ui.features import projects as PJ  # noqa: E402
from test_sp4_projects import _fake_ctx, _state  # noqa: E402

_APP = QApplication.instance() or QApplication([])


@pytest.fixture()
def tmp_profiles(tmp_path, monkeypatch):
    """Redirect user-profile persistence to a tmp file."""
    path = tmp_path / "pcb_profiles.json"
    monkeypatch.setattr(pcbprof, "_profiles_path", lambda: path)
    return path


def _panel(tmp_path):
    return PJ._pcb_setup_panel(_fake_ctx(), _state(tmp_path))


# ── the picker ────────────────────────────────────────────────────────────────
def test_profile_picker_is_a_dropdown_of_all_profiles(tmp_path, tmp_profiles):
    panel = _panel(tmp_path)
    assert isinstance(panel._profile_seg, QComboBox)
    items = [panel._profile_seg.itemText(i) for i in range(panel._profile_seg.count())]
    assert items == [pcbprof.BARE_OSH_4, pcbprof.BARE_OSH_2, pcbprof.NETDECK]
    assert panel._profile_seg.currentText() == pcbprof.NETDECK      # default = full-nets profile


def test_default_profile_loads_the_full_netclass_set(tmp_path, tmp_profiles):
    panel = _panel(tmp_path)
    assert len(panel._ncmgr.list_netclasses()) >= 15               # NETDECK taxonomy


def test_bare_osh_profile_is_nets_free(tmp_path, tmp_profiles):
    panel = _panel(tmp_path)
    panel._load_profile(pcbprof.BARE_OSH_4)
    assert panel._ncmgr.list_netclasses() == []                    # no nets baked in
    panel._load_profile(pcbprof.NETDECK)
    assert len(panel._ncmgr.list_netclasses()) >= 15               # …and back to full


# ── CRUD ──────────────────────────────────────────────────────────────────────
def test_new_profile_saves_and_appears_in_the_dropdown(tmp_path, tmp_profiles, monkeypatch):
    from PyQt5.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("My Board", True)))
    panel = _panel(tmp_path)
    panel._new_profile()
    items = [panel._profile_seg.itemText(i) for i in range(panel._profile_seg.count())]
    assert "My Board" in items
    saved = pcbprof.get_profile("My Board", path=tmp_profiles)
    assert saved is not None
    # it captured the current (NETDECK) net classes + fab
    assert saved.fab == pcbprof.BARE_OSH_4
    assert len(saved.netclasses) >= 15


def test_new_profile_cancelled_saves_nothing(tmp_path, tmp_profiles, monkeypatch):
    from PyQt5.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    panel = _panel(tmp_path)
    panel._new_profile()
    assert not tmp_profiles.exists()                               # nothing written


def test_save_profile_persists_current_netclasses(tmp_path, tmp_profiles):
    panel = _panel(tmp_path)
    panel._nc_new()                                                # add a class
    n = len(panel._ncmgr.list_netclasses())
    panel._save_profile()                                          # override NETDECK
    reloaded = pcbprof.get_profile(pcbprof.NETDECK, path=tmp_profiles)
    assert len(reloaded.netclasses) == n                          # override captured the edit


def test_delete_user_profile_removes_it(tmp_path, tmp_profiles, monkeypatch):
    from PyQt5.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Scratch", True)))
    panel = _panel(tmp_path)
    panel._new_profile()
    assert "Scratch" in [panel._profile_seg.itemText(i) for i in range(panel._profile_seg.count())]
    panel._load_profile("Scratch")
    panel._delete_profile()
    assert "Scratch" not in [panel._profile_seg.itemText(i) for i in range(panel._profile_seg.count())]


def test_delete_builtin_logs_and_keeps_it(tmp_path, tmp_profiles):
    ctx = _fake_ctx()
    panel = PJ._pcb_setup_panel(ctx, _state(tmp_path))
    panel._load_profile(pcbprof.NETDECK)
    panel._delete_profile()                                        # built-in → can't delete
    items = [panel._profile_seg.itemText(i) for i in range(panel._profile_seg.count())]
    assert pcbprof.NETDECK in items
    assert any("built-in" in m.lower() for m in ctx.services.logs)
