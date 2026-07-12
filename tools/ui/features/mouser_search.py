"""Live Mouser search — a command-palette dialog that shows catalog matches as you
type. Debounced (~350 ms), queries off the GUI thread, caches per query, cancels
stale in-flight queries, and distinguishes a genuine no-match from a transport
failure (rate limit / network) so the user is never told "nothing found" when the
call actually failed.

Pick a result → returns the normalized part dict (``dlg.picked``); the caller feeds
it through the existing autofill path to write the identity onto a library symbol.
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QScrollArea, QWidget)

import LibraryManager as LM
from .. import theme as T
from .. import widgets as W
from ..util import run_populate, clear_layout, fmt_countdown

_MIN_CHARS = 3
_DEBOUNCE_MS = 350
_LIMIT = 12
_COUNTDOWN_MS = 30_000          # re-tick the shared-key reset countdown twice a minute


def _fmt_price(v) -> str:
    """Mouser price-break prices come through as a number or a '$0.10' string."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        return f"${v:.2f}"
    s = str(v).strip()
    # Only prepend a '$' when the string is actually a number — a malformed upstream
    # value ('abc') must NOT render as a nonsense price ('$abc'); show it verbatim.
    try:
        float(s.lstrip("$").replace(",", ""))
    except ValueError:
        return s
    return s if s.startswith("$") else f"${s}"


def _lifecycle_marker(lifecycle: str):
    """A quiet status marker for a part's lifecycle, or None when it's healthy
    (Active parts get no marker — only a risk earns ink)."""
    lc = (lifecycle or "").lower()
    if any(w in lc for w in ("obsolete", "end of life", "eol", "discontinued")):
        return W.tag("Obsolete", "err")
    if "nrnd" in lc or "not recommended" in lc:
        return W.tag("NRND", "warn")
    if lc and lc != "active":
        return W.body(lifecycle, dim=True)
    return None


class MouserSearchDialog(QDialog):
    """Type-to-search the Mouser catalog; ``exec_()`` then read ``.picked``."""

    def __init__(self, ctx, seed_query: str = "", parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self.picked: Optional[dict] = None
        self._cache: dict = {}          # query.lower() -> search_parts() result (successes only)
        self._seq = 0                   # bumped per query; a late result with a stale seq is dropped
        self._closed = False            # once exec_() returns, drop any late worker callback
        self.setWindowTitle("Search Mouser")
        self.setModal(True)
        self.setMinimumSize(660, 540)
        # Themed dialog surface (a top-level QDialog isn't covered by the window's
        # background rule, so it would default to white in dark mode).
        W.register_restyle(lambda: self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), self)
        self.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addWidget(W.subhead("Search Mouser"))
        sub = W.body("Search the Mouser catalog by manufacturer part number or keyword, "
                     "then pick a result to fill this part's identity from it.", dim=True)
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self._edit = QLineEdit()
        self._edit.setMinimumHeight(34)
        self._edit.setPlaceholderText("e.g. STM32F407VGT6  or  0.1uF 0402 X7R")
        self._edit.setClearButtonEnabled(True)
        lay.addWidget(self._edit)

        self._status = W.body("", dim=True)
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._results = QVBoxLayout()
        self._results.setContentsMargins(0, 0, 0, 0)
        self._results.setSpacing(6)
        holder = QWidget()
        holder.setLayout(self._results)
        holder.setStyleSheet("background:transparent;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(holder)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Transparent viewport so the themed dialog surface shows through (a QScrollArea
        # viewport otherwise defaults to the white palette base).
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        scroll.viewport().setStyleSheet("background:transparent;")
        lay.addWidget(scroll, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._maybe_query)
        self._edit.textChanged.connect(lambda _t: self._timer.start())
        self._edit.returnPressed.connect(self._maybe_query)   # Enter searches immediately

        # A live "frees up in ~3h 12m" countdown while the shared-key cap is showing.
        # Re-reads the reset clock every _COUNTDOWN_MS and rewrites the status label so
        # the number ticks down instead of freezing at its first-render snapshot; runs
        # only while capped and stops the moment the state clears or the dialog closes.
        self._rl_base: Optional[str] = None           # base message the countdown decorates
        self._countdown = QTimer(self)
        self._countdown.setInterval(_COUNTDOWN_MS)
        self._countdown.timeout.connect(self._tick_countdown)

        if seed_query:
            self._edit.setText(seed_query)
            self._maybe_query() if len(seed_query.strip()) >= _MIN_CHARS else self._prompt()
        else:
            self._prompt()

    # ── query lifecycle ───────────────────────────────────────────────────────
    def _prompt(self):
        clear_layout(self._results)
        self._status.setText(f"Type at least {_MIN_CHARS} characters to search.")

    def _maybe_query(self):
        self._timer.stop()
        q = self._edit.text().strip()
        if len(q) < _MIN_CHARS:
            self._prompt()
            return
        self._run_query(q)

    def _run_query(self, q: str):
        key = q.lower()
        self._seq += 1
        seq = self._seq
        if key in self._cache:
            self._render(self._cache[key], q)
            return
        clear_layout(self._results)
        self._status.setText(f"Searching Mouser for “{q}”…")

        def job():
            return LM.search_parts(q, self._ctx.cfg, limit=_LIMIT)

        def done(res, ok):
            if self._closed or seq != self._seq:
                return                                  # dialog closed, or a newer keystroke won
            if not ok or res is None:
                res = {"results": [], "error": "Mouser lookup failed", "error_code": "network"}
            if not res.get("error_code"):               # cache only successes / genuine no-match
                self._cache[key] = res
            self._render(res, q)

        run_populate(self._ctx, job, done)

    def _render(self, res: dict, q: str):
        clear_layout(self._results)
        code = res.get("error_code", "")
        results = res.get("results", [])
        if code == "rate_limited":                      # the shared daily cap — show a live countdown
            self._rl_base = res.get("error") or "Mouser lookup failed. Try again."
            self._status.setText(self._rate_limit_message(self._rl_base))
            if not self._countdown.isActive():
                self._countdown.start()
            return
        self._stop_countdown()                          # any other outcome clears the capped state
        if code:                                        # a real failure — never read as "no match"
            self._status.setText(res.get("error") or "Mouser lookup failed. Try again.")
            return
        if not results:
            self._status.setText(f"No Mouser parts match “{q}”.")
            return
        self._status.setText(f"{len(results)} result{'s' if len(results) != 1 else ''} — "
                             f"pick one to apply it to this part.")
        for part in results:
            self._results.addWidget(self._result_row(part))
        self._results.addStretch(1)

    def _rate_limit_message(self, base: str) -> str:
        """Append a countdown to the shared Mouser key's daily-cap reset. The app ships
        ONE free key (SRC-04); when it is capped, tell the user when it frees up rather
        than leaving a dead-end error. Falls back to the base message if we can't tell."""
        try:
            secs = LM.mouser_reset_seconds_remaining()
        except Exception:                               # noqa: BLE001
            secs = None
        if not secs:
            return (f"{base}. The built-in Mouser key is shared and capped at 1000 "
                    "lookups/day — it resets at midnight (US Central). Enter the MPN "
                    "manually, or use BOM sourcing where LCSC fills in as a fallback.")
        return (f"{base}. The built-in key is shared (1000 lookups/day) — it frees up in "
                f"~{fmt_countdown(secs)}. Enter the MPN manually, or use BOM sourcing "
                "where LCSC fills in as a fallback.")

    def _tick_countdown(self):
        """Re-read the reset clock and rewrite the capped-state label so the countdown
        ticks down live. Stops itself once the state is no longer showing a base message."""
        if self._closed or self._rl_base is None:
            self._stop_countdown()
            return
        self._status.setText(self._rate_limit_message(self._rl_base))

    def _stop_countdown(self):
        self._rl_base = None
        if self._countdown.isActive():
            self._countdown.stop()

    # ── one result row ────────────────────────────────────────────────────────
    def _result_row(self, part: dict) -> QPushButton:
        b = QPushButton()
        b.setObjectName("mouserrow")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(part.get("description") or "")
        col = QVBoxLayout(b)
        col.setContentsMargins(12, 9, 12, 9)
        col.setSpacing(3)

        def _tm(w):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            return w

        line1 = QHBoxLayout(); line1.setSpacing(10)
        mpn = _tm(QLabel(part.get("mpn") or "—")); mpn.setFont(T.mono_font(12, semibold=True))
        line1.addWidget(mpn)
        life = _lifecycle_marker(part.get("lifecycle"))
        if life is not None:
            _tm(life); line1.addWidget(life)
        line1.addStretch(1)
        price = _fmt_price(part.get("unit_price"))
        if price:
            pl = _tm(QLabel(price)); pl.setFont(T.mono_font(12, semibold=True))
            line1.addWidget(pl)
        col.addLayout(line1)

        line2 = QHBoxLayout(); line2.setSpacing(8)
        desc = (part.get("description") or "").strip()
        if len(desc) > 92:
            desc = desc[:91].rstrip() + "…"
        mfr = part.get("manufacturer") or ""
        left = " · ".join(x for x in (mfr, desc) if x) or "—"
        line2.addWidget(_tm(W.body(left, dim=True)))
        line2.addStretch(1)
        stock = part.get("stock") or 0
        line2.addWidget(_tm(W.body(f"{stock:,} in stock" if stock else "No stock", dim=True)))
        col.addLayout(line2)

        def style():
            mpn.setStyleSheet(f"color:{T.t('txt1')};background:transparent;")
            b.setStyleSheet(
                "QPushButton#mouserrow{background:transparent;border:none;border-radius:8px;text-align:left;}"
                f"QPushButton#mouserrow:hover{{background:{T.t('ctl_hover')};}}")
        W.register_restyle(style, b)
        b.clicked.connect(lambda: self._choose(part))
        return b

    def _choose(self, part: dict):
        self.picked = part
        self.accept()

    def done(self, r):
        # accept()/reject()/close all route through QDialog.done — mark closed here so a
        # worker callback that lands after exec_() returns can't touch dead widgets.
        self._closed = True
        self._stop_countdown()                          # never leave the countdown ticking on a dead dialog
        super().done(r)
