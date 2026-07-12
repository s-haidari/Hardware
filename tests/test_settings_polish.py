"""Settings polish/ux/codequality fixes.

Drives the real behaviour behind the audit findings on tools/ui/features/settings.py:

- em-dash-free user-visible copy (design-rules §2 forbids em dashes in rendered strings);
- Paths / Library-Location values render as plain mono text, not chipped tokens
  (design-rules §4 Detail forbids chip-in-card);
- status-only rows carry a scannable status tag instead of an empty placeholder label,
  and _setting_row(control=None) adds no trailing widget;
- changing the library location / resetting the snapshot offers an actionable
  "Relaunch Now" that re-execs, instead of a passive "please restart" notice.
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


def _panel(monkeypatch):
    _app()
    import LibraryManager as LM
    from ui import feature as F
    from ui import theme as T
    from ui.features import settings as S

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    # Deterministic Mouser row: not rate-limited so its copy doesn't shift on the
    # real config.json cap marker.
    monkeypatch.setattr(LM, "mouser_reset_seconds_remaining", lambda *a, **k: None)
    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=F.EventBus())
    return S._settings_panel(ctx, None)


# ── §2 — no em dashes in any rendered label ───────────────────────────────────
def test_no_em_dash_in_any_visible_label(monkeypatch):
    from PyQt5.QtWidgets import QLabel
    panel = _panel(monkeypatch)
    for lbl in panel.findChildren(QLabel):
        assert "—" not in lbl.text(), f"em dash in visible copy: {lbl.text()!r}"


# ── §4 Detail — Paths / Library-Location values are plain mono text, not chips ─
def test_path_and_location_values_are_not_chipped_tokens(monkeypatch):
    """W.token paints a 'tok' background chip; §4 Detail locks the definition list
    to plain mono text. The location line and the four derived-path values must be
    W.body(mono=True), which paints a transparent background."""
    from PyQt5.QtWidgets import QLabel
    panel = _panel(monkeypatch)
    for lbl in panel.findChildren(QLabel):
        ss = lbl.styleSheet()
        # a chip token sets a non-transparent background:tok; body() sets transparent.
        if "background:" in ss:
            assert "background:transparent" in ss.replace(" ", ""), (
                f"chipped value label leaked into Settings: {lbl.text()!r} / {ss!r}")


# ── codequality — _setting_row(control=None) adds no trailing widget ──────────
def test_setting_row_control_none_adds_no_trailing_widget():
    _app()
    from PyQt5.QtWidgets import QLabel
    from ui.features import settings as S

    card = S._setting_row("Title", "sub", None)
    # Only the title + subtitle labels live in the card; no empty placeholder control.
    labels = card.findChildren(QLabel)
    assert [l.text() for l in labels] == ["Title", "sub"]

    tag_widget = QLabel("X")
    card2 = S._setting_row("Title", "sub", tag_widget)
    assert tag_widget in card2.findChildren(QLabel), "a real control is still added"


# ── codequality — status-only rows carry a scannable status tag ───────────────
def test_status_rows_have_scannable_tags(monkeypatch):
    """Mouser / DigiKey / STM32 rows surface state as a tag, not only in prose."""
    from PyQt5.QtWidgets import QLabel
    import LibraryManager as LM
    monkeypatch.setattr(LM, "resolve_digikey_creds", lambda cfg=None: (None, None))
    panel = _panel(monkeypatch)
    texts = [lbl.text() for lbl in panel.findChildren(QLabel)]
    # W.tag with a non-"mut" kind renders a leading status dot ("● Ready"); a "mut"
    # tag is plain neutral text. DigiKey has no creds -> "Not Configured" (mut);
    # Mouser is live+uncapped -> "Ready" (ok).
    assert any(t == "Not Configured" for t in texts), "DigiKey inactive tag present"
    assert any(t.endswith("Ready") for t in texts), "a Ready status tag present (STM32 or Mouser)"


# ── ux — Change / Reset offer an actionable "Relaunch Now" that re-execs ───────
def test_offer_relaunch_reexecs_on_relaunch_and_not_on_later(monkeypatch):
    _app()
    from PyQt5.QtWidgets import QMessageBox
    from ui.features import settings as S

    called = {"n": 0}
    monkeypatch.setattr(S, "_relaunch", lambda: called.__setitem__("n", called["n"] + 1))
    # _offer_relaunch no-ops under headless; exercise the GUI path (parent=None → the native
    # QMessageBox fallback the mock below drives). The inline `from ..util import _headless`
    # in _offer_relaunch picks up this patch.
    from ui import util
    monkeypatch.setattr(util, "_headless", lambda: False)

    # Simulate the user clicking "Relaunch Now": clickedButton() returns the
    # AcceptRole button (the first one added).
    class _AcceptBox:
        AcceptRole = 0
        RejectRole = 1

        def __init__(self, *a, **k):
            self._buttons = []
        def setWindowTitle(self, *a): pass
        def setText(self, *a): pass
        def addButton(self, text, role):
            b = object()
            self._buttons.append(b)
            return b
        def exec_(self): pass
        def clickedButton(self):
            return self._buttons[0]      # the Relaunch Now button

    monkeypatch.setattr(S, "QMessageBox", _AcceptBox)
    S._offer_relaunch(None, "T", "body")
    assert called["n"] == 1, "clicking Relaunch Now must re-exec the app"

    # Now simulate "Later" (the second button) -> no re-exec.
    class _LaterBox(_AcceptBox):
        def clickedButton(self):
            return self._buttons[1]      # the Later button

    monkeypatch.setattr(S, "QMessageBox", _LaterBox)
    S._offer_relaunch(None, "T", "body")
    assert called["n"] == 1, "clicking Later must NOT re-exec"


def test_change_location_offers_relaunch(monkeypatch):
    """_change_location writes the pointer, applies it, then offers a relaunch
    (no passive 'please restart' text path)."""
    _app()
    import LibraryManager as LM
    from ui.features import settings as S

    chosen = Path("/tmp/some-library")
    monkeypatch.setattr(LM, "_prompt_choose_location", lambda parent=None: chosen)
    wrote = {}
    monkeypatch.setattr(LM, "write_pointer", lambda p: wrote.__setitem__("pointer", p))
    monkeypatch.setattr(LM, "apply_library_location", lambda p: wrote.__setitem__("applied", p))
    offered = {}
    monkeypatch.setattr(S, "_offer_relaunch",
                        lambda parent, title, body: offered.__setitem__("title", title))

    S._change_location(None)
    assert wrote["pointer"] == chosen and wrote["applied"] == chosen
    assert offered.get("title") == "Library Location", "must offer a relaunch, not a passive notice"


def test_change_location_cancel_does_nothing(monkeypatch):
    _app()
    import LibraryManager as LM
    from ui.features import settings as S
    monkeypatch.setattr(LM, "_prompt_choose_location", lambda parent=None: None)
    calls = []
    monkeypatch.setattr(LM, "write_pointer", lambda p: calls.append(p))
    monkeypatch.setattr(S, "_offer_relaunch", lambda *a, **k: calls.append("relaunch"))
    S._change_location(None)
    assert calls == [], "cancelling the chooser must not write a pointer or offer relaunch"
