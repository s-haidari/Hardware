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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_APP = None   # module-global reference keeps the QApplication alive to the very end


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
