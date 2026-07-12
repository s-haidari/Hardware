"""Wave 1 · SHELL-06 (restyle-registry lifecycle) + PROJ-07 (themed data_table).

SHELL-06: restylers registered with an ``owner`` widget must auto-unregister when
that widget is destroyed, so a rebuilt panel's stale restylers don't accumulate
(the append-only registry made every theme toggle slower over a session).

PROJ-07: data_table must colour EVERY cell from the active theme (not fall back to
Qt's default black, unreadable in dark mode) and offer a wrapping mode so a wide
BOM stays readable without a horizontal scrollbar.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402
from PyQt5.QtGui import QColor  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.widgets as W  # noqa: E402
import ui.theme as T  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    """Delete a QWidget's C++ object NOW so its ``destroyed`` signal fires
    synchronously. ``deleteLater()`` + ``processEvents()`` does NOT deliver the
    DeferredDelete event, so tests would never observe the auto-unregister that a
    real running event loop performs."""
    sip.delete(w)


# ── SHELL-06: restyle-registry lifecycle ──────────────────────────────────────
def test_owner_bound_restyler_drops_on_destroy():
    W._prune_restylers()                         # clean baseline (prior tests' dead restylers drop lazily)
    base = len(W._RESTYLERS)
    w = QWidget()
    W.register_restyle(lambda: None, w)
    assert len(W._RESTYLERS) == base + 1
    _destroy(w)                                  # C++ gone; drop is lazy (weakref, not destroyed.connect)
    W._prune_restylers()                         # reclaim the dead-owner restyler
    assert len(W._RESTYLERS) == base


def test_ownerless_restyler_persists():
    base = len(W._RESTYLERS)
    fn = lambda: None
    W.register_restyle(fn)                        # no owner → process-lifetime
    assert len(W._RESTYLERS) == base + 1
    W._drop_restyle(fn)                           # cleanup so we don't pollute peers
    assert len(W._RESTYLERS) == base


def test_register_restyle_invokes_immediately():
    calls = []
    w = QWidget()
    W.register_restyle(lambda: calls.append(1), w)
    assert calls == [1]
    _destroy(w)


def test_primitive_widgets_self_clean_on_destroy():
    # A rebuilt panel that recreates these primitives must not grow the registry.
    W._prune_restylers()                         # clean baseline (prior tests' dead restylers drop lazily)
    base = len(W._RESTYLERS)
    widgets = [W.body("x"), W.tag("ok"), W.token("R1"), W.eyebrow("HDR"),
               W.page_title("P"), W.net_token("NET", "power")]
    assert len(W._RESTYLERS) == base + len(widgets)
    for wdg in widgets:
        _destroy(wdg)
    W._prune_restylers()                         # drop is lazy now (weakref, not destroyed.connect)
    assert len(W._RESTYLERS) == base


def test_rebuild_loop_does_not_leak():
    W._prune_restylers()                         # clean baseline (prior tests' dead restylers drop lazily)
    base = len(W._RESTYLERS)
    for _ in range(20):
        lab = W.body("row")
        _destroy(lab)
    W._prune_restylers()                         # drop is lazy now (weakref, not destroyed.connect)
    assert len(W._RESTYLERS) == base


def test_register_restyle_owner_is_gc_safe():
    """Regression lock — the restyler GC segfault. A restyler-owner widget trapped in a
    reference cycle and freed by Python's CYCLIC garbage collector must NOT crash the
    interpreter: register_restyle tracks the owner by weakref + lazy prune, never
    ``owner.destroyed.connect(python_slot)`` — that slot, invoked by Qt mid-collection, is a
    use-after-free that SIGSEGVs (the same class fixed for EventBus.on_owned). It also must
    never connect anything to ``destroyed``."""
    import gc
    # Flush any restyler-owners a PRIOR test trapped in a Python cycle FIRST, so `base`
    # is stable and only this test's `w` moves the count — otherwise those cycles are
    # freed by *this* test's gc.collect() below and skew the final assertion (they were
    # weakref-alive, hence counted, at base capture). The GC-safety guarantees under test
    # (no destroyed slot; gc.collect completes; the owner's restyler prunes) are unchanged.
    gc.collect()
    W._prune_restylers()
    base = len(W._RESTYLERS)
    w = QWidget()
    W.register_restyle(lambda: None, w)
    assert len(W._RESTYLERS) == base + 1
    # No Python slot may sit on destroyed (that is the crash mechanism).
    assert w.receivers(w.destroyed) == 0, "register_restyle must not connect to destroyed (GC-unsafe)"
    # Trap w in a pure-Python reference cycle so ONLY cyclic gc can free it, then collect:
    # the OLD code delivered destroyed → _drop_restyle to Python HERE, mid-collection → crash.
    cyc = {}; cyc["w"] = w; w._cycle = cyc
    del w, cyc
    gc.collect()                                 # must complete, no segfault
    W._prune_restylers()
    assert len(W._RESTYLERS) == base, "the cyclically-collected owner's restyler is pruned"


# ── PROJ-07: themed, wrapping data_table ──────────────────────────────────────
def test_data_table_colours_every_cell_from_theme():
    tbl = W.data_table(["A", "B"], [["x", "y"], ["z", "w"]], dim_cols={1})
    for r in range(2):
        # non-dim column → primary text; dim column → secondary (txt2, not txt3):
        # dimmed columns carry data, and txt3 fails WCAG AA on the light card.
        assert tbl.item(r, 0).foreground().color() == W._qcolor(T.t("txt1"))
        assert tbl.item(r, 1).foreground().color() == W._qcolor(T.t("txt2"))
    _destroy(tbl)


def test_data_table_never_leaves_cells_default_black():
    # The bug: unstyled items fell back to QColor(0,0,0). Assert no plain cell is
    # left at the default when the theme's primary text isn't black.
    was_dark = T.is_dark()
    T.set_theme(True)                             # dark: txt1 is the near-white primary
    try:
        tbl = W.data_table(["A"], [["hello"]])
        assert tbl.item(0, 0).foreground().color() == W._qcolor(T.t("txt1"))
        assert tbl.item(0, 0).foreground().color() != QColor(0, 0, 0)
        _destroy(tbl)
    finally:
        T.set_theme(was_dark)


def test_data_table_wrap_mode():
    tbl = W.data_table(["A"], [["a very long cell value that should wrap"]], wrap=True)
    assert tbl.wordWrap() is True
    from PyQt5.QtCore import Qt
    assert tbl.textElideMode() == Qt.ElideNone
    _destroy(tbl)


def test_data_table_default_is_non_wrapping():
    tbl = W.data_table(["A"], [["x"]])
    assert tbl.wordWrap() is False
    _destroy(tbl)


# ── design-contract: retired flat-4px radius; borderless ledger table ─────────
def test_chip_widgets_use_control_radius_not_flat_4px():
    """token / net_token / Segmented are controls → RADIUS_CONTROL (6px). The
    retired flat 4px must not survive on any of them (design-rules radius invariant)."""
    from PyQt5.QtWidgets import QLabel

    control = f"border-radius:{T.RADIUS_CONTROL}px"

    tok = W.token("R1")
    net = W.net_token("VBUS", "power")
    seg = W.Segmented(["A", "B"])
    try:
        assert control in tok.styleSheet()
        # net_token styles its own wrapper QWidget (the chip background lives there).
        assert control in net.styleSheet()
        # Segmented styles itself in _style(); force it and read back.
        seg._style()
        assert control in seg.styleSheet()

        for wdg in (tok, net, seg):
            css = wdg.styleSheet()
            assert "border-radius:4px" not in css
            assert "border-radius: 4px" not in css
    finally:
        _destroy(tok); _destroy(net); _destroy(seg)


def test_data_table_is_borderless_ledger():
    """The shared table is a borderless ledger (design-rules §1.2 / §4): no cell
    grid, only a 1px bottom hairline per row + the header underline. No vertical
    rules, no box around every cell."""
    from PyQt5.QtWidgets import QAbstractItemView

    tbl = W.data_table(["A", "B"], [["x", "y"], ["z", "w"]])
    try:
        # The Qt cell grid (both vertical and horizontal rules) is off.
        assert tbl.showGrid() is False
        css = tbl.styleSheet()
        # Row separation is a per-item BOTTOM hairline only (horizontal divider).
        assert "QTableWidget::item" in css
        assert f"border-bottom:1px solid {T.t('stroke')}" in css
        # No Qt gridline colour is set (that would redraw the full ruled grid).
        assert "gridline-color" not in css
        # Header keeps its underline.
        assert "QHeaderView::section" in css
        # Full-row hover survives as the single inset lift.
        assert tbl.selectionBehavior() == QAbstractItemView.SelectRows
        assert f"background:{T.t('inset')}" in css
    finally:
        _destroy(tbl)


def test_qcolor_parses_rgba_and_hex():
    assert W._qcolor("#ffffff") == QColor("#ffffff")
    c = W._qcolor("rgba(0,0,0,0.894)")
    assert (c.red(), c.green(), c.blue()) == (0, 0, 0)
    assert c.alpha() == round(0.894 * 255)
    assert W._qcolor("rgb(255,128,0)") == QColor(255, 128, 0)


# ── restyle_all: narrow the exception guard (codequality :71) ─────────────────
def test_restyle_all_logs_real_restyler_bug_not_swallowed(caplog):
    """A genuine restyler bug (not a deleted-widget RuntimeError) must be logged,
    not silently swallowed leaving the widget unstyled with no trace."""
    import logging

    def boom():
        raise ValueError("bad token")

    # boom is an OWNERLESS restyler keyed only by id(boom). Restyler-heavy panels earlier in
    # the suite churn closures, so a stale _RESTYLE_OWNERS entry may linger at a freed id that
    # boom now reuses — which would make _restyler_dead(boom) falsely True and prune it before
    # it runs. Clear any such stale entry so this test asserts the log deterministically.
    W._RESTYLE_OWNERS.pop(id(boom), None)
    W._RESTYLERS.append(boom)
    try:
        with caplog.at_level(logging.ERROR, logger="ui.widgets"):
            W.restyle_all()          # must NOT raise, but MUST log the ValueError
        assert any("restyle callback failed" in r.message for r in caplog.records)
    finally:
        W._drop_restyle(boom)


def test_restyle_all_drops_restyler_of_deleted_widget():
    """restyle_all drops a dead restyler two ways, neither of which raises: an OWNED one
    whose widget is gone is pruned by the weakref check (never called); an OWNERLESS one
    that touches a deleted widget raises RuntimeError, which restyle_all swallows + drops."""
    base = len(W._RESTYLERS)
    lab = QWidget()
    W.register_restyle(lambda: lab.setStyleSheet(""), lab)   # owned toucher (weakref-pruned path)
    dead = QWidget()
    fn = lambda: dead.setStyleSheet("")
    W._RESTYLERS.append(fn)                                  # ownerless toucher (RuntimeError path)
    assert len(W._RESTYLERS) == base + 2
    sip.delete(lab); sip.delete(dead)                        # both C++ gone (the drop is lazy now)
    W.restyle_all()                                          # must not raise
    assert fn not in W._RESTYLERS                            # ownerless-dead dropped via RuntimeError
    assert len(W._RESTYLERS) == base                         # owned-dead pruned via the weakref check


# ── data_table h-scroll policy by wrap mode (ux :508) ─────────────────────────
def test_data_table_wrap_hides_hscroll_nonwrap_keeps_it():
    from PyQt5.QtCore import Qt

    wrapped = W.data_table(["A"], [["long value that wraps"]], wrap=True)
    plain = W.data_table(["A", "B"], [["x", "y"]])
    try:
        # wrap=True: no sideways scroll (it reflows), scrollbar forced off.
        assert wrapped.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        # wrap=False: fixed columns that overflow the pane must stay reachable.
        assert plain.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    finally:
        _destroy(wrapped); _destroy(plain)


# ── empty_state glyph re-tints on theme toggle (polish :591) ──────────────────
def test_empty_state_glyph_registers_restyle():
    """The empty-state glyph must re-render from a theme tier on toggle (own
    restyler), not bake a single hard-coded gray pixmap at build time."""
    _GLYPH = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' \
             '<circle cx="12" cy="12" r="8" fill="currentColor"/></svg>'
    base = len(W._RESTYLERS)
    w = W.empty_state("Nothing here", glyph=_GLYPH)
    try:
        # empty_state registers restylers for the caption(s) AND the glyph icon.
        # Assert the glyph added one (more restylers than the same call without a glyph).
        assert len(W._RESTYLERS) > base
        # And the glyph label actually carries a non-null themed pixmap.
        from PyQt5.QtWidgets import QLabel
        labels = w.findChildren(QLabel)
        assert any(not lab.pixmap().isNull() for lab in labels if lab.pixmap() is not None)
    finally:
        _destroy(w)


def test_empty_state_glyph_pixmap_differs_between_themes():
    """txt3 differs between light and dark, so the re-tinted glyph pixmap must
    differ after a theme toggle (proves it re-renders, not baked once)."""
    _GLYPH = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' \
             '<rect x="4" y="4" width="16" height="16" fill="currentColor"/></svg>'
    from PyQt5.QtWidgets import QLabel
    was_dark = T.is_dark()
    try:
        T.set_theme(True)
        w = W.empty_state("x", glyph=_GLYPH)
        icon = next(lab for lab in w.findChildren(QLabel)
                    if lab.pixmap() is not None and not lab.pixmap().isNull())
        dark_bytes = icon.pixmap().toImage()
        T.set_theme(False)
        W.restyle_all()                       # toggle re-tints registered widgets
        light_bytes = icon.pixmap().toImage()
        assert dark_bytes != light_bytes      # glyph re-rendered from the new txt3
        _destroy(w)
    finally:
        T.set_theme(was_dark)


# ── Skeleton pauses its shimmer off-screen (perf :611) ────────────────────────
def test_skeleton_pauses_animation_on_hide_and_resumes_on_show(monkeypatch):
    """The shimmer animation must pause when the skeleton is hidden (off-screen)
    so a swapped-out placeholder stops driving GUI-thread repaints, and resume
    when shown again."""
    from PyQt5.QtCore import QAbstractAnimation
    import ui.motion as M
    # Force the animated path even under offscreen/reduced-motion in CI.
    monkeypatch.setattr(M, "reduced_motion", lambda: False)

    sk = W.Skeleton(width=100)
    try:
        assert sk._anim is not None                              # animation was created
        sk.show()
        # Simulate the swap-out: hide fires hideEvent → pause.
        sk.hide()
        assert sk._anim.state() == QAbstractAnimation.Paused
        # Re-show resumes it.
        sk.show()
        assert sk._anim.state() == QAbstractAnimation.Running
    finally:
        _destroy(sk)
