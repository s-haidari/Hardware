"""ui.motion — the ONE place animation lives.

Qt5 QSS has no CSS transitions, so every eased state change is a
QPropertyAnimation / painted tween here. A single reduced-motion gate makes the
whole layer an instant no-op (accessibility + the render gate's determinism):
each primitive applies its final state synchronously and returns None when the
gate is set. No decorative animation — hover / selection / subtab / theme only.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import (Qt, QRect, QPropertyAnimation, QEasingCurve, pyqtProperty,
                          QAbstractAnimation)
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect, QLabel

from . import theme as T

_reduced: bool = False


def _auto_reduced() -> bool:
    """Best-effort OS reduced-motion read; False (motion on) when unknown. Qt5 has
    no direct reduced-motion flag, so this defaults to motion-on and lets a config
    flag / set_reduced_motion() override (the headless render gate sets it)."""
    try:
        from PyQt5.QtGui import QGuiApplication
        _ = QGuiApplication.instance()
        return False
    except Exception:  # noqa: BLE001
        return False


def reduced_motion() -> bool:
    return _reduced


def set_reduced_motion(on: Optional[bool]) -> None:
    """Set the gate. Pass None to re-auto-detect from the OS/config."""
    global _reduced
    _reduced = _auto_reduced() if on is None else bool(on)


# ── opacity tween (theme cross-fade overlay, popovers) ───────────────────────
def animate_opacity(widget: QWidget, start: float, end: float, duration: int = 140,
                    on_done: Optional[Callable] = None) -> Optional[QPropertyAnimation]:
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    if reduced_motion():
        eff.setOpacity(end)
        if on_done:
            on_done()
        return None
    eff.setOpacity(start)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


# ── sliding subtab underline ─────────────────────────────────────────────────
class SlidingUnderline(QWidget):
    """A painted 2px rule that tweens its x/width to the active subtab, instead of
    the QSS border-bottom snapping between tabs. Painted (not a QSS box) so it is
    one consistent element and can animate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(2)
        self._anim: Optional[QPropertyAnimation] = None

    def _get_geom(self) -> QRect:
        return self.geometry()

    def _set_geom(self, r: QRect) -> None:
        self.setGeometry(r)

    geom = pyqtProperty(QRect, fget=_get_geom, fset=_set_geom)

    def move_to(self, x: int, width: int, animate: bool = True) -> None:
        y = self.y()
        target = QRect(int(x), int(y), int(width), self.height())
        # Retire any in-flight animation, then OWN its lifecycle. Never `DeleteWhenStopped`
        # here: it frees the C++ QPropertyAnimation the instant it finishes, leaving
        # self._anim a dangling wrapper whose next .stop() is a use-after-free — the
        # "clicking a second subtab crashes the exe" segfault (v2.10.0). Instead keep the
        # ref, stop the previous one while it is still alive, and delete it deferred; the
        # anim is parented to self, so it never leaks and self._anim is always live-or-None.
        old = self._anim
        self._anim = None
        if old is not None:
            old.stop()
            old.deleteLater()
        if not animate or reduced_motion():
            self.setGeometry(target)
            return
        anim = QPropertyAnimation(self, b"geom", self)
        anim.setDuration(160)
        anim.setStartValue(self.geometry())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._anim = anim

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), T.qcolor("accent"))
        p.end()


# ── theme cross-fade ─────────────────────────────────────────────────────────
def cross_fade(window: QWidget, apply_fn: Callable[[], None], duration: int = 160) -> None:
    """Fade the theme swap through a grabbed-pixmap overlay instead of a hard flip.
    Under reduced motion (or a failed grab) just apply the change instantly."""
    if reduced_motion():
        apply_fn()
        return
    try:
        pixmap = window.grab()
    except Exception:  # noqa: BLE001
        apply_fn()
        return
    overlay = QLabel(window)
    overlay.setPixmap(pixmap)
    overlay.setGeometry(0, 0, window.width(), window.height())
    overlay.show()
    overlay.raise_()
    apply_fn()

    def _cleanup():
        overlay.deleteLater()

    animate_opacity(overlay, 1.0, 0.0, duration=duration, on_done=_cleanup)


# ── painted keyboard focus ring ──────────────────────────────────────────────
def paint_focus_ring(painter: QPainter, rect: QRect, color: QColor,
                     radius: int = T.RADIUS_CONTROL) -> None:
    """A crisp neutral focus ring on the device-pixel grid (0.5px-inset cosmetic
    1px pen) so it never reads fuzzy at fractional DPI."""
    pen = QPen(color)
    pen.setWidth(1)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    r = rect.adjusted(0, 0, -1, -1)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.drawRoundedRect(r, radius, radius)
