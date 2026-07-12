"""Convergence Phase 0 · the shared static-label vocabulary + Git de-fork.

widgets.static_label / static_status are the NO-RESTYLER twins of body/subhead/tag,
styled centrally by object name (theme.qss `#s*` rules) so a high-frequency rebuild
area (the Git watchdog) never grows the retint registry. Git's private shadow
vocabulary (_key/_mut/_path/_subhead/_stat + `#git*` QSS) is retired onto them.

These tests pin the de-fork so a future edit can't silently regress it:
  * the static builders register NO restyler (the whole point) — building many in a
    loop leaves the registry flat;
  * each role keeps the EXACT font tier + object name Git relied on (a role-table
    edit that reflows the Git panel fails HERE, not by eye);
  * every `#s*` object name has a matching theme.qss rule in BOTH themes;
  * the retired `#git*` vocabulary is gone from theme.qss AND from git.py object names.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.widgets as W  # noqa: E402
import ui.theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])

_TOOLS = Path(__file__).resolve().parents[1] / "tools"


# ── the static builders register NO restyler (the reason they exist) ──────────
def test_static_label_registers_no_restyler():
    base = len(W._RESTYLERS)
    labels = [W.static_label(f"row {i}", "body") for i in range(50)]
    labels += [W.static_status(f"s{i}", "ok") for i in range(50)]
    assert len(W._RESTYLERS) == base, "static builders must not touch the retint registry"
    assert len(labels) == 100


# ── each role keeps the exact object name + font tier Git relied on ───────────
def test_static_label_role_object_names():
    assert W.static_label("x", "body").objectName() == "sBody"
    assert W.static_label("x", "dim").objectName() == "sDim"
    assert W.static_label("x", "key").objectName() == "sKey"
    assert W.static_label("x", "sub").objectName() == "sSub"


def test_static_label_role_fonts_match_git_originals():
    # The 6 retired git helpers used these exact scale_font roles; a role-table edit
    # that changes any of them would silently reflow the Git panel — lock them.
    #   _key=detail_key  _mut/_path/_branch=value  _subhead=section
    # 'dim' (was _mut) is value-sized — dim by COLOUR, never shrink it.
    for role, want in (("body", "value"), ("dim", "value"),
                       ("key", "detail_key"), ("sub", "section")):
        got = W.static_label("x", role).font()
        exp = T.scale_font(want)
        assert got.pointSizeF() == exp.pointSizeF(), f"{role} pt drift"
        assert got.family() == exp.family(), f"{role} face drift"
        assert got.weight() == exp.weight(), f"{role} weight drift"


def test_static_label_font_role_override():
    got = W.static_label("x", "body", font_role="hero").font()
    assert got.pointSizeF() == T.scale_font("hero").pointSizeF()


# ── static_status mirrors git _stat exactly (dot rule, kinds, footnote font) ──
def test_static_status_kinds_and_dot():
    # mut = neutral dim text, NO leading dot; every other kind gets the '● ' dot.
    assert W.static_status("Clean", "mut").text() == "Clean"
    for kind in ("ok", "warn", "err", "info"):
        lab = W.static_status("X", kind)
        assert lab.text() == "● X"
        assert lab.objectName() == f"sStat_{kind}"
    # footnote tier for all kinds (matches the retired git _stat)
    assert W.static_status("X", "ok").font().pointSizeF() == T.scale_font("footnote").pointSizeF()


# ── every #s* object name is themed in BOTH themes (no unstyled fall-through) ──
def test_every_static_name_has_a_qss_rule_in_both_themes():
    names = ["sBody", "sDim", "sKey", "sSub",
             "sStat_ok", "sStat_warn", "sStat_err", "sStat_info", "sStat_mut"]
    for dark in (True, False):
        sheet = T.qss(dark)
        for n in names:
            assert re.search(rf"QLabel#{n}\b", sheet), f"missing QSS rule for #{n} (dark={dark})"


# ── the load-bearing guarantee: a static label retints on a live toggle ───────
def test_static_label_retints_on_theme_toggle():
    """The whole de-fork rests on this: a static label carries NO restyler, so it retints
    purely from the shell re-applying the sheet (setStyleSheet(T.qss())). Prove it
    DETERMINISTICALLY by pinning the two halves of that path — a rendered-pixmap grab of an
    unshown offscreen widget is platform-fragile (identical on some backends), so we assert
    the contract itself: the label wears the #sBody object name the central QSS targets, and
    #sBody's colour DIFFERS between the dark and light sheets, so re-applying the sheet
    necessarily recolours it (the live setStyleSheet repaint is Qt's own guarantee)."""
    lab = W.static_label("PATHTEXT", "body")
    assert lab.objectName() == "sBody"                     # the hook the central QSS targets

    def _sbody_color(dark):
        m = re.search(r"QLabel#sBody\b[^}]*?color:\s*([^;]+);", T.qss(dark))
        assert m, f"the #sBody QSS rule carries no colour (dark={dark})"
        return m.group(1).strip()

    assert _sbody_color(True) != _sbody_color(False), \
        "static label would not retint on a toggle — #sBody has the same colour in both themes"


# ── the retired #git* vocabulary is fully gone ────────────────────────────────
def test_no_git_shadow_vocabulary_left():
    sheet = T.qss(True) + T.qss(False)
    for dead in ("gitkey", "gitmut", "gitpath", "gitsub", "gittok",
                 "gitstat_ok", "gitstat_warn", "gitstat_mut"):
        assert dead not in sheet, f"retired QSS rule #{dead} still in theme.qss()"
    # git.py must not set any retired label object name (gitPanel, the root container,
    # is allowed — it carries no qss rule and is not a label vocabulary name).
    src = (_TOOLS / "ui" / "features" / "git.py").read_text(encoding="utf-8")
    for m in re.finditer(r'setObjectName\(\s*[\'"]([^\'"]+)[\'"]', src):
        name = m.group(1)
        assert not name.startswith("git") or name == "gitPanel", \
            f"git.py still sets a retired label object name: {name}"
    # And no reference to the deleted helper QSS names anywhere in git.py.
    assert not re.search(r'git(key|mut|path|sub|stat_|tok)', src), \
        "git.py still names the retired shadow vocabulary"
