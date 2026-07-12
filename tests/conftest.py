"""Shared pytest setup — one offscreen QApplication for the whole session.

PyQt with the offscreen platform can segfault at interpreter shutdown when many
widget tests run in one process: Qt C++ objects get torn down after the Python
interpreter has started finalizing (worsened by QFluentWidgets / the frameless
window installing global singletons). Owning a single session-long QApplication
here — kept referenced so it is not garbage-collected mid-teardown — and closing
any leftover top-level widgets + flushing deferred deletes at session end makes
teardown deterministic instead of an occasional exit-139.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402

_APP = None   # module-global reference keeps the QApplication alive to the very end


@pytest.fixture(autouse=True)
def _reset_app_globals():
    """Reset app-wide process globals before each test so state can't leak between
    tests. The units preference (`ui.units._mode`) is a single module global — an
    earlier test that switches to mils otherwise poisons a later test's captions,
    producing a failure that only appears in the full suite (test-ordering
    pollution), never in isolation. Reset to mm (the default) before every test."""
    try:
        from ui import units as U
        U.set_mode("mm")
    except Exception:  # noqa: BLE001
        pass
    yield


@pytest.fixture(autouse=True)
def _isolate_pcb_profile_store(tmp_path, monkeypatch):
    """Redirect the user PCB-profile store to a per-test tmp file. Panel saves
    (projects._save -> nd_pcb_profiles.save_profile) otherwise write to the real
    user store (tools/pcb_profiles.json), polluting a developer's profiles and
    leaking test data across runs. Isolate it so every test starts from the
    built-in profiles and cannot touch the real file."""
    try:
        import nd_pcb_profiles as P
    except Exception:  # noqa: BLE001
        yield
        return
    store = tmp_path / "pcb_profiles.json"
    monkeypatch.setattr(P, "_profiles_path", lambda: store)
    yield


@pytest.fixture(scope="session", autouse=True)
def _qapp():
    global _APP
    try:
        from PyQt5.QtWidgets import QApplication
    except Exception:
        yield None
        return
    _APP = QApplication.instance() or QApplication([])
    yield _APP
    # Deterministic teardown: close/relinquish stray top-level widgets and flush
    # the deferred-delete queue *before* the interpreter finalizes. Do NOT delete
    # the QApplication itself here — that ordering is exactly what crashes.
    try:
        for w in list(_APP.topLevelWidgets()):
            w.close()
            w.deleteLater()
        _APP.processEvents()
    except Exception:
        pass
