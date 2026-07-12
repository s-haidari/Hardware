"""ui.shell — the NETDECK window.

A native-titlebar QMainWindow (robust Windows move/resize/snap; the title also
gives the live-validation harness a stable target). Everything below the title
bar is ours: a left nav built ENTIRELY from the feature registry, a content
stack, and a theme toggle. The shell hard-codes no feature.

Retheme is instant: set the tokens, re-apply the QSS, and call restyle_all() to
retint the colour-bearing widgets.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt5.QtCore import Qt, QObject, pyqtSignal, QSize
from PyQt5.QtGui import QPalette, QKeySequence
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout,
                             QVBoxLayout, QPushButton, QStackedWidget, QFrame, QLabel,
                             QLineEdit, QShortcut)

from . import theme as T
from . import widgets as W
from . import feature as F
from . import units as U
from .icons import GLYPHS as _ICON   # the one unified icon set (ui.icons)

WINDOW_TITLE = "NETDECK Firmware Extraction Bench"


# ── services (async + logging) available to every feature ────────────────────
class _Bridge(QObject):
    done = pyqtSignal(object, bool)


class Services:
    def __init__(self, log_fn):
        self._log = log_fn
        self._bridge = _Bridge()
        self._bridge.done.connect(lambda cb, ok: cb(ok) if callable(cb) else None)

    def log(self, msg: str):
        self._log(str(msg))

    def run_async(self, fn, ok: str = None, done_cb=None):
        def worker():
            success = True
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                success = False
                self._log(f"Error: {e}")
            else:
                if ok:
                    self._log(ok)
            self._bridge.done.emit(done_cb, success)
        threading.Thread(target=worker, daemon=True).start()


class NavItem(QPushButton):
    def __init__(self, text: str, icon, on_click, *, enabled: bool = True, tip: str = ""):
        super().__init__(text)
        self._label = text
        self._disabled = not enabled
        self._tip = tip
        self.setObjectName("navItem")
        self.setMinimumHeight(38)
        self.setCheckable(False)
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QSize(18, 18))
        if enabled:
            self.setCursor(Qt.PointingHandCursor)
            self.clicked.connect(on_click)
        else:
            # An honest shelved row: greyed (via #navItem:disabled) and not clickable,
            # with a tip explaining why — never a live row that opens a dead placeholder.
            self.setEnabled(False)
            if tip:
                self.setToolTip(tip)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self); self.style().polish(self)

    def collapse(self, collapsed: bool):
        self.setText("" if collapsed else self._label)
        if self._disabled:
            self.setToolTip(self._tip)               # keep the "why greyed" tip in both states
        else:
            self.setToolTip(self._label if collapsed else "")


# ── update signals (marshal background results back to the GUI thread) ────────
class _UpdateSignals(QObject):
    found = pyqtSignal(object)     # an update descriptor dict
    none = pyqtSignal(str)         # a "you're up to date" message (manual checks only)


class _DownloadSignals(QObject):
    progress = pyqtSignal(int, int)   # (bytes_done, bytes_total)
    done = pyqtSignal(bool)           # success


class _AutoPullSignals(QObject):
    # marshal a background auto-pull GitResult back to the GUI thread so it can log
    result = pyqtSignal(object)       # a nd_git.GitResult


class NetdeckShell(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        import LibraryManager as LM
        # theme persists across launches (config.json "Theme": Dark|Light|System);
        # System follows the Windows apps dark-mode preference.
        self._theme_mode = LM.read_setting("Theme", "Dark")
        self._dark = T.resolve_dark(self._theme_mode, T.os_dark())
        # length units persist app-wide too (config.json "Units": mm|mils)
        U.set_mode(LM.read_setting("Units", U.MM))
        self._nav_collapsed = False
        self._pending_update = None
        T.set_theme(self._dark)
        from . import motion as Mo
        from .util import _headless
        Mo.set_reduced_motion(_headless())   # headless render gate / CI = instant
        # An 'Error:' log line auto-opens the Activity console so it can't scroll away
        # unseen — but OFF under headless so the render gate / CI never auto-expand it
        # (deterministic screenshots). Tests/drive-audit flip this to exercise the path.
        self._auto_surface_errors = not _headless()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1440, 900)

        self.services = Services(self._log)
        self.ctx = F.Context(cfg=cfg, services=self.services, theme=T, bus=F.EventBus())

        # register features (importing the package runs the register() calls)
        from . import features  # noqa: F401
        self._features = F.features()

        root = QWidget(); root.setObjectName("shellRoot")
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._nav = self._build_nav()
        row.addWidget(self._nav)

        # content column: the workspace stack + the Activity console. The console is a
        # durable log surface (the ▶/✓/✗ stream + errors the transient statusBar can't
        # hold), but it is HIDDEN BY DEFAULT — opened on demand from the nav-footer
        # Activity toggle, so it never occupies the bottom unless the user wants it. Its
        # visibility persists across launches; live feedback still flashes in the statusBar.
        content = QWidget()
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(0)
        self._stack = QStackedWidget()
        self._stack.setObjectName("contentArea")
        cv.addWidget(self._stack, 1)
        from .console import ActivityConsole
        self._console = ActivityConsole(on_toggle=self._persist_console)
        self._console.set_expanded(True, notify=False)   # when shown, show the log body
        self._console_open = bool(LM.read_setting("ConsoleVisible", False))
        self._console.setVisible(self._console_open)
        self._unseen_activity = 0
        cv.addWidget(self._console)
        row.addWidget(content, 1)
        self.setCentralWidget(root)
        self._sync_activity_item()          # now the console exists → set the toggle's real state

        self._nav_items = []
        self._foot_items = []
        self._build_pages()
        self.apply_theme(self._dark)
        self._select(0)
        self._restore_search_filter()       # re-apply a persisted Ctrl+K query (SEARCH-persist)

        # updates: a Settings button (or launch) asks; results marshal back to the GUI
        self._upd = _UpdateSignals()
        self._upd.found.connect(self._on_update_available)
        self._upd.none.connect(lambda msg: self._info("Up To Date", msg))
        self.ctx.bus.on("app.check_updates", lambda *_a: self.check_for_updates(manual=True))
        # Settings Theme control routes here (the nav toggle calls _toggle_theme directly)
        self.ctx.bus.on("theme.set_mode", self._set_theme_mode)
        # Any unit control (Settings, PCB Setup) commands here; we own the persist + broadcast
        self.ctx.bus.on("units.set_mode", self._set_units_mode)
        # App-level background auto-pull (GIT-02): the shell owns the timer + the
        # persisted "AutoPull" preference so it runs regardless of the open tab.
        self._autopull = self._build_autopull(cfg)
        self.ctx.bus.on("autopull.set_enabled", self._set_autopull)
        # Cross-feature navigation: a feature can ask the shell (the one owner of tab
        # selection) to open another workspace by id — e.g. the Library sourcing
        # empty-state's "Open Settings" CTA emits nav.open("settings").
        self.ctx.bus.on("nav.open", self._open_feature)

    def _open_feature(self, feature_id: str):
        """Select the workspace whose feature id matches — the bus target for a
        cross-feature 'go here' CTA. No-op on an unknown id (never raises)."""
        for i, spec in enumerate(getattr(self, "_page_specs", [])):
            if spec[0].id == feature_id:
                self._select(i)
                return

    # -- nav --
    def _build_nav(self) -> QWidget:
        pane = QWidget(); pane.setObjectName("navPane"); pane.setFixedWidth(236)
        self._nav_lay = QVBoxLayout(pane)
        self._nav_lay.setContentsMargins(10, 12, 10, 10)
        self._nav_lay.setSpacing(2)

        # brand row — wordmark + a collapse toggle (Ctrl+B)
        brand = QWidget(); brand.setObjectName("navBrandRow")
        br = QHBoxLayout(brand); br.setContentsMargins(6, 0, 0, 2); br.setSpacing(6)
        self._brand = QLabel("NETDECK"); self._brand.setObjectName("navBrand")
        br.addWidget(self._brand); br.addStretch(1)
        ham = QPushButton(); ham.setObjectName("navToggle"); ham.setFixedSize(30, 30)
        ham.setCursor(Qt.PointingHandCursor); ham.setIcon(W.svg_icon(_ICON["ham"]))
        ham.setIconSize(QSize(18, 18))
        ham.setToolTip("Collapse or expand the navigation (Ctrl+B)")
        ham.clicked.connect(self._toggle_nav)
        self._nav_toggle = ham
        br.addWidget(ham)
        self._nav_lay.addWidget(brand)

        # Ctrl+K search — live-filters the workspace list by title AND category, so
        # typing an area name ("firmware", "version") surfaces a workspace whose title
        # differs. The text persists across launches (paired with the pane-width persist).
        self._search = QLineEdit(); self._search.setObjectName("navSearch")
        self._search.setPlaceholderText("Search workspace or category  Ctrl+K")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(W.svg_icon(_ICON["search"]), QLineEdit.LeadingPosition)
        import LibraryManager as LM
        saved = str(LM.read_setting("NavSearch", "") or "").strip()
        if saved:
            self._search.setText(saved)          # applied once the pages exist (_restore_search_filter)
        self._search.textChanged.connect(self._filter_nav)
        self._search.editingFinished.connect(self._persist_search)
        # editingFinished covers a typed query on blur/Enter, but the CLEAR affordances
        # (the line-edit X button, and Ctrl+B collapse which clears the field) empty it
        # WITHOUT an editingFinished — so persist an emptied query immediately, or a stale
        # query would remain in settings and wrongly re-apply on the next launch.
        self._search.textChanged.connect(self._persist_search_if_cleared)
        self._nav_lay.addWidget(self._search)

        # "Did you mean …?" — a quiet link shown only when a query matches nothing;
        # clicking it adopts the closest workspace/category name (difflib).
        self._suggestion = ""
        self._did_you_mean = QPushButton()
        self._did_you_mean.setObjectName("navDidYouMean")
        self._did_you_mean.setCursor(Qt.PointingHandCursor)
        self._did_you_mean.setVisible(False)
        self._did_you_mean.clicked.connect(self._adopt_suggestion)
        self._nav_lay.addWidget(self._did_you_mean)
        self._nav_lay.addSpacing(6)

        # keyboard: Ctrl+K focuses search, Ctrl+B collapses, Ctrl+/ shows the shortcut
        # reference, Esc clears (search-scoped). Each carries a 'shortcutHelp' property so
        # the reference dialog enumerates every bound shortcut app-wide with a real label.
        sc_k = QShortcut(QKeySequence("Ctrl+K"), self,
                         activated=lambda: (self._search.setFocus(), self._search.selectAll()))
        sc_k.setProperty("shortcutHelp", "Search workspaces")
        sc_b = QShortcut(QKeySequence("Ctrl+B"), self, activated=self._toggle_nav)
        sc_b.setProperty("shortcutHelp", "Collapse or expand the navigation")
        sc_h = QShortcut(QKeySequence("Ctrl+/"), self, activated=self._show_shortcuts)
        sc_h.setProperty("shortcutHelp", "Show keyboard shortcuts")
        esc = QShortcut(QKeySequence("Escape"), self._search)
        esc.setContext(Qt.WidgetShortcut)
        esc.setProperty("shortcutHelp", "Clear the search")
        esc.activated.connect(
            lambda: (self._search.clear(), self._search.clearFocus(), self._persist_search()))
        return pane

    def _filter_nav(self, text: str = ""):
        """Live-filter the workspace list by the search text (Ctrl+K), matching on the
        workspace title OR its category, so an area name surfaces a differently-titled
        workspace. While searching, matches are grouped under per-category eyebrows (the
        flat "Workspaces" header hides); an empty query restores the flat list. The footer
        (theme / Settings / update) is never filtered. No-ops before the pages exist."""
        if not hasattr(self, "_page_specs"):
            return
        q = (text or "").strip().lower()
        searching = bool(q)
        cat_visible = {c: False for c in getattr(self, "_cat_eyebrows", {})}
        any_visible = False
        for i, item in enumerate(self._nav_items):
            feat = self._page_specs[i][0]
            if feat.id == "settings":          # footer item — never filtered
                continue
            cat = getattr(feat, "category", "") or "Workspaces"
            match = (not q) or (q in feat.title.lower()) or (q in cat.lower())
            item.setVisible(match)
            if match:
                any_visible = True
                if cat in cat_visible:
                    cat_visible[cat] = True
        # the default "Workspaces" header shows only when NOT searching; each category
        # eyebrow shows only while searching AND when it has at least one visible match.
        if hasattr(self, "_eyebrow"):
            self._eyebrow.setVisible(not searching and not self._nav_collapsed)
        for cat, eb in getattr(self, "_cat_eyebrows", {}).items():
            eb.setVisible(searching and cat_visible.get(cat, False) and not self._nav_collapsed)
        self._update_did_you_mean(q, any_visible)

    def _update_did_you_mean(self, q: str, any_visible: bool):
        """Show a 'Did you mean <name>?' link when a query matched no workspace, using
        difflib against every workspace title + category. Hidden on an empty query, on any
        match, or when the nav is collapsed. Stores the suggestion for _adopt_suggestion."""
        dym = getattr(self, "_did_you_mean", None)
        if dym is None:
            return
        if not q or any_visible:
            self._suggestion = ""
            dym.setVisible(False)
            return
        import difflib
        names = []
        for spec in self._page_specs:
            feat = spec[0]
            if feat.id == "settings":
                continue
            names.append(feat.title)
            if getattr(feat, "category", ""):
                names.append(feat.category)
        lower = [n.lower() for n in names]
        hit = difflib.get_close_matches(q, lower, n=1, cutoff=0.4)
        if hit:
            self._suggestion = names[lower.index(hit[0])]
            dym.setText(f"Did you mean {self._suggestion}?")
            dym.setVisible(not self._nav_collapsed)
        else:
            self._suggestion = ""
            dym.setVisible(False)

    def _adopt_suggestion(self):
        """Adopt the 'Did you mean' suggestion — set it as the query (which now matches)."""
        if getattr(self, "_suggestion", ""):
            self._search.setText(self._suggestion)
            self._search.setFocus()
            self._persist_search()

    def _persist_search(self):
        """Persist the current Ctrl+K query so it survives a relaunch (paired with the
        pane-width persist). The single writer for the "NavSearch" preference."""
        try:
            import LibraryManager as LM
            LM.write_setting("NavSearch", self._search.text().strip())
        except Exception:  # noqa: BLE001
            pass

    def _persist_search_if_cleared(self, text: str = ""):
        """Persist an EMPTIED query at once (the clear-button X / Ctrl+B collapse clear the
        field without an editingFinished). Persisting only on empty keeps per-keystroke
        writes off the hot path while closing the stale-query-re-applies-on-relaunch gap."""
        if not (text or "").strip():
            self._persist_search()

    def _restore_search_filter(self):
        """Apply a search query restored from settings once the pages exist. If the saved
        query now matches no workspace (features changed since it was saved), clear it
        silently so the user never opens to an empty, unexplained nav."""
        q = self._search.text().strip()
        if not q:
            return
        self._filter_nav(q)
        # isHidden(), not isVisible(): at construction the window is not shown yet, so
        # isVisible() is False for every item even when it matched — that would wrongly
        # clear every restored query. isHidden() reflects the explicit filter flag.
        main_visible = any(
            not self._nav_items[i].isHidden()
            for i in range(len(self._nav_items))
            if self._page_specs[i][0].id != "settings")
        if not main_visible:
            self._search.blockSignals(True)
            self._search.clear()
            self._search.blockSignals(False)
            self._filter_nav("")
            self._persist_search()

    # -- keyboard shortcuts reference --
    def _iter_shortcuts(self):
        """Every bound QShortcut under the window, as sorted (keys, description) pairs — the
        app-wide keyboard map. Descriptions come from the 'shortcutHelp' dynamic property set
        where each shortcut is created; a shortcut without one still appears by its key so the
        reference is honest and complete. Deduplicated by key sequence (a described binding
        wins over an undescribed duplicate)."""
        from PyQt5.QtWidgets import QShortcut as _QS
        seen: dict = {}
        for sc in self.findChildren(_QS):
            keys = sc.key().toString(QKeySequence.NativeText).strip()
            if not keys:
                continue
            desc = str(sc.property("shortcutHelp") or "")
            if keys not in seen or (desc and not seen[keys]):
                seen[keys] = desc
        return sorted(seen.items(), key=lambda kv: kv[0].lower())

    def _build_shortcuts_dialog(self):
        """The keyboard-shortcuts reference: a small two-column (keys | action) dialog
        enumerated live from the bound shortcuts. Returned unshown so the render gate and
        tests can inspect it without a blocking modal."""
        from PyQt5.QtWidgets import QDialog, QGridLayout
        dlg = QDialog(self)
        dlg.setObjectName("shortcutsDialog")
        dlg.setWindowTitle("Keyboard Shortcuts")
        dlg.setModal(True)
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(14)
        lay.addWidget(W.eyebrow("Keyboard Shortcuts"))
        rows = self._iter_shortcuts()
        if rows:
            grid = QGridLayout(); grid.setHorizontalSpacing(18); grid.setVerticalSpacing(8)
            grid.setColumnStretch(1, 1)
            for r, (keys, desc) in enumerate(rows):
                grid.addWidget(W.body(keys, mono=True), r, 0, Qt.AlignTop)
                grid.addWidget(W.body(desc or "Shortcut", dim=not desc), r, 1, Qt.AlignTop)
            lay.addLayout(grid)
        else:
            lay.addWidget(W.body("No keyboard shortcuts are bound.", dim=True))
        btns = QHBoxLayout(); btns.addStretch(1)
        btns.addWidget(W.btn("Close", "ghost", "Close this reference", dlg.accept))
        lay.addLayout(btns)
        W.register_restyle(
            lambda: dlg.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}"), dlg)
        dlg.setStyleSheet(f"QDialog{{background:{T.t('surface')};}}")
        return dlg

    def _show_shortcuts(self):
        """Open the keyboard-shortcuts reference (Ctrl+/ or the footer item). Under headless
        a modal would block the run forever, so the dialog is built and returned unshown."""
        from .util import _headless
        dlg = self._build_shortcuts_dialog()
        if _headless():
            return dlg
        dlg.exec_()
        return dlg

    def _build_pages(self):
        lay = self._nav_lay
        main = [f for f in self._features if f.id != "settings"]
        settings = [f for f in self._features if f.id == "settings"]
        ordered = main + settings

        # The flat "Workspaces" header (default view). Per-category eyebrows are inserted
        # before each category's first item, hidden until a Ctrl+K search groups matches.
        self._eyebrow = W.eyebrow("Workspaces")
        lay.addWidget(self._eyebrow)
        self._nav_items = []
        self._page_specs = []          # [feature, built?] — pages build lazily on first nav
        self._cat_eyebrows = {}        # category -> its (hidden) eyebrow, shown while searching
        for idx, feat in enumerate(ordered):
            self._stack.addWidget(QWidget())
            self._page_specs.append([feat, False])
            item = NavItem(feat.title, W.svg_icon(_ICON.get(feat.id, "")),
                           lambda _=False, k=idx: self._select(k),
                           enabled=getattr(feat, "enabled", True),
                           tip=getattr(feat, "disabled_tip", ""))
            if feat.id == "settings":
                self._foot_items.append((idx, item))
            else:
                cat = getattr(feat, "category", "") or "Workspaces"
                if cat not in self._cat_eyebrows:
                    eb = W.eyebrow(cat); eb.setVisible(False)
                    lay.addWidget(eb)
                    self._cat_eyebrows[cat] = eb
                lay.addWidget(item)
            self._nav_items.append(item)

        lay.addStretch(1)
        rule = QFrame(); rule.setFixedHeight(1)
        W.register_restyle(lambda: rule.setStyleSheet(f"background:{T.t('divider')};border:none;"))
        lay.addWidget(rule)

        # Activity toggle — shows/hides the durable log console on demand (hidden by
        # default), so the log never pins to the bottom unless the user opens it.
        self._activity_item = NavItem("Activity", W.svg_icon(_ICON["activity"]),
                                      self._toggle_console)
        self._activity_item.setToolTip("Show or hide the activity log")
        lay.addWidget(self._activity_item)
        self._sync_activity_item()

        # Keyboard-shortcuts reference — opens the app-wide shortcut map (also Ctrl+/).
        self._shortcuts_item = NavItem("Keyboard Shortcuts", W.svg_icon(_ICON["help"]),
                                       self._show_shortcuts)
        self._shortcuts_item.setToolTip("Show the keyboard shortcuts (Ctrl+/)")
        lay.addWidget(self._shortcuts_item)

        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("navItem")
        self._theme_btn.setMinimumHeight(38)
        self._theme_btn.setCursor(Qt.PointingHandCursor)
        self._theme_btn.setIconSize(QSize(18, 18))
        self._theme_btn.setToolTip("Switch between the dark and light Windows themes")
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._sync_theme_btn()
        lay.addWidget(self._theme_btn)

        # a persistent "update available" affordance — hidden until an update is found,
        # so a dismissed dialog (or a silent launch auto-check) still leaves a signal
        self._update_item = NavItem("Update Available", W.svg_icon(_ICON["update"]),
                                    self._open_pending_update)
        self._update_item.setToolTip("A newer version is available. Click to update")
        self._update_item.setVisible(False)
        lay.addWidget(self._update_item)

        for idx, item in self._foot_items:
            lay.addWidget(item)

    def _toggle_nav(self):
        self._nav_collapsed = not self._nav_collapsed
        if self._nav_collapsed and self._search.text():
            self._search.clear()               # don't leave items hidden behind a filter
        self._nav.setFixedWidth(56 if self._nav_collapsed else 236)
        self._brand.setVisible(not self._nav_collapsed)
        self._search.setVisible(not self._nav_collapsed)
        self._did_you_mean.setVisible(False)   # search chrome hides in the rail
        self._eyebrow.setVisible(not self._nav_collapsed)
        for eb in getattr(self, "_cat_eyebrows", {}).values():
            eb.setVisible(False)               # category headers only appear during search
        for it in self._nav_items:
            it.collapse(self._nav_collapsed)
        if hasattr(self, "_update_item"):
            self._update_item.collapse(self._nav_collapsed)
        if hasattr(self, "_shortcuts_item"):
            self._shortcuts_item.collapse(self._nav_collapsed)
        self._sync_activity_item()
        self._sync_theme_btn()

    def _toggle_console(self):
        """Show/hide the durable Activity log on demand (it is hidden by default so it
        never pins to the bottom). The choice persists; opening clears the unseen badge."""
        self._console_open = not self._console_open
        self._console.setVisible(self._console_open)
        if self._console_open:
            self._unseen_activity = 0
        try:
            LM.write_setting("ConsoleVisible", bool(self._console_open))
        except Exception:  # noqa: BLE001
            pass
        self._sync_activity_item()

    def _sync_activity_item(self):
        """Label + selected state for the Activity toggle: highlighted while the console
        is open; a count of unseen lines while it is closed, so activity stays
        discoverable without the panel occupying the bottom (the statusBar still flashes
        each line live)."""
        item = getattr(self, "_activity_item", None)
        console = getattr(self, "_console", None)
        if item is None or console is None:          # nav footer builds before the console
            return
        open_ = getattr(self, "_console_open", False)
        n = getattr(self, "_unseen_activity", 0)
        item._label = "Activity" if (open_ or not n) else f"Activity ({n})"
        item.collapse(self._nav_collapsed)
        item.set_selected(open_)

    def _sync_theme_btn(self):
        """The single source of truth for the theme button's label + glyph. Label is
        empty when the nav is collapsed, else names the ACTIVE theme; the icon is a
        moon in dark and a sun in light (SET-01). Called from _build_pages,
        _toggle_nav, and _apply_theme_now so the three never drift."""
        if not hasattr(self, "_theme_btn"):
            return
        self._theme_btn.setText(
            "" if self._nav_collapsed else ("Dark Theme" if self._dark else "Light Theme"))
        self._theme_btn.setIcon(W.svg_icon(_ICON["theme"] if self._dark else _ICON["sun"]))

    def _safe_build(self, feat: F.Feature) -> QWidget:
        try:
            return feat.build(self.ctx)
        except Exception as e:  # noqa: BLE001
            w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(24, 24, 24, 24)
            v.addWidget(W.eyebrow(f"{feat.title} Failed To Load"))
            v.addWidget(W.body(str(e), dim=True)); v.addStretch(1)
            return w

    def _select(self, k: int):
        spec = self._page_specs[k]
        if not getattr(spec[0], "enabled", True):
            return                              # a disabled/shelved feature is never opened
        if not spec[1]:
            page = self._safe_build(spec[0])
            old = self._stack.widget(k)
            self._stack.removeWidget(old); old.deleteLater()
            self._stack.insertWidget(k, page)
            spec[1] = True
        self._stack.setCurrentIndex(k)
        for i, item in enumerate(self._nav_items):
            item.set_selected(i == k)

    # -- theme --
    def apply_theme(self, dark: bool):
        # Fade the swap through a grabbed-pixmap overlay (ui.motion); instant under
        # reduced motion / headless so the render gate + CI stay deterministic.
        from . import motion as Mo
        Mo.cross_fade(self, lambda: self._apply_theme_now(dark))

    def _apply_theme_now(self, dark: bool):
        self._dark = dark
        T.set_theme(dark)                    # ui.theme is the ONE active theme (the ui_theme
                                             # shim is retired; every consumer reads ui.theme).
        try:                                 # keep component previews on the app surface
            import fp_render as R
            R.set_render_theme(dark, T.t("inset"))
        except Exception:  # noqa: BLE001
            pass
        self._apply_palette()               # so unstyled surfaces (scroll viewports) match
        self.setStyleSheet(T.qss())          # set_theme(dark) already flipped the global; qss() reads it (single source)
        self._set_titlebar_theme(dark)      # tint the native Windows title bar to match
        W.restyle_all()
        self._sync_theme_btn()

    def _set_titlebar_theme(self, dark: bool) -> bool:
        """Tint the native Windows title bar to match the theme (DWM immersive dark
        mode), so a dark app no longer wears a bright OS title bar. Windows-only and
        best-effort: a guarded no-op (returns False) elsewhere or if the DWM call
        fails, so nothing depends on it. Wave 0 decision: dark native title bar rather
        than a full frameless custom chrome."""
        import sys
        if sys.platform != "win32":
            return False
        try:
            import ctypes
            hwnd = int(self.winId())
            val = ctypes.c_int(1 if dark else 0)
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (was 19 on early Win10 20H1 builds)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    return True
            return False
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _apply_palette():
        """Theme the app QPalette so plain/unstyled Fusion widgets (QScrollArea
        viewports, holders) use the theme surface instead of the light default."""
        app = QApplication.instance()
        if app is None:
            return
        pal = QPalette()
        for role, key in ((QPalette.Window, "surface"), (QPalette.Base, "card"),
                          (QPalette.AlternateBase, "surface"), (QPalette.Text, "txt1"),
                          (QPalette.WindowText, "txt1"), (QPalette.Button, "card"),
                          (QPalette.ButtonText, "txt1"), (QPalette.Highlight, "accent"),
                          (QPalette.HighlightedText, "on_accent"), (QPalette.ToolTipBase, "card"),
                          (QPalette.ToolTipText, "txt1"), (QPalette.PlaceholderText, "txt3")):
            pal.setColor(role, T.qcolor(key))
        app.setPalette(pal)

    def _toggle_theme(self):
        # the nav toggle flips to an explicit Dark/Light mode (and persists it)
        self._set_theme_mode("Light" if self._dark else "Dark")

    def _set_theme_mode(self, mode: str):
        """Apply a theme MODE (Dark|Light|System) and persist it so it survives a
        relaunch. The single writer for the "Theme" preference — the Settings Theme
        control and nav toggle only route the mode here; this owns apply + persist +
        broadcast so a live control (Settings' Theme segment) reflects the change."""
        self._theme_mode = mode
        self.apply_theme(T.resolve_dark(mode, T.os_dark()))
        try:
            import LibraryManager as LM
            LM.write_setting("Theme", mode)
        except Exception:  # noqa: BLE001
            pass
        self.ctx.bus.emit("theme.changed", mode)

    def _maybe_follow_os_theme(self) -> bool:
        """When the theme mode is 'System', re-resolve the OS dark/light preference and
        repaint if it changed while the app was open. Returns True iff a repaint fired.
        A no-op in explicit Dark/Light mode (the OS preference is irrelevant then) and
        whenever the OS preference is unknown/unchanged, so it is cheap to call on every
        OS setting-change broadcast."""
        if self._theme_mode != "System":
            return False
        want = T.resolve_dark("System", T.os_dark())
        if want == self._dark:
            return False
        self.apply_theme(want)
        return True

    def nativeEvent(self, event_type, message):
        """Follow a live OS dark/light flip while the app is running. Windows posts a
        WM_SETTINGCHANGE with lParam 'ImmersiveColorSet' when the apps theme changes;
        when the app is in 'System' mode we re-resolve and repaint. Guarded no-op off
        Windows and for every unrelated message, so the normal event path is untouched."""
        try:
            if sys.platform == "win32" and self._theme_mode == "System":
                import ctypes
                import ctypes.wintypes  # noqa: F401 — registers wintypes.MSG
                # WM_SETTINGCHANGE = 0x001A
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x001A and msg.lParam:
                    area = ctypes.wstring_at(msg.lParam)
                    if area == "ImmersiveColorSet":
                        self._maybe_follow_os_theme()
        except Exception:  # noqa: BLE001
            pass
        return super().nativeEvent(event_type, message)

    def _build_autopull(self, cfg: dict):
        """Construct the app-level auto-pull service, seeded from the persisted
        preference. Headless (offscreen render_gate / CI) gets no QTimer — the
        enabled state is still tracked, nothing fires."""
        from .autopull import AutoPullService
        from .util import _headless
        from PyQt5.QtCore import QTimer
        import LibraryManager as LM
        enabled = bool(LM.read_setting("AutoPull", False))
        repo = (cfg or {}).get("RepoRoot")
        timer = None if _headless() else QTimer(self)
        # The pull runs on a daemon thread, so its result must marshal back to the
        # GUI thread before we log it (statusBar is not thread-safe). A background
        # sync used to be completely silent — success, no-op, and a diverged branch
        # all looked identical; now failures (and pulls that actually moved the
        # branch) leave a visible status-bar line.
        self._autopull_sig = _AutoPullSignals()
        self._autopull_sig.result.connect(self._on_autopull_result)
        return AutoPullService(repo, enabled=enabled, timer=timer,
                               on_result=self._autopull_sig.result.emit)

    def _on_autopull_result(self, res):
        """Log the outcome of a background auto-pull (runs on the GUI thread). Quiet
        on the common 'Already up to date.' no-op; a concise line when the pull moved
        the branch forward or when it was blocked (e.g. a diverged local branch)."""
        if res is None:
            return
        if getattr(res, "ok", False):
            out = (getattr(res, "out", "") or "").strip()
            if out and "up to date" not in out.lower():
                self._log("Auto-pull: pulled updates from remote.")
            # a clean "Already up to date." stays silent — no noise on every tick
        else:
            first = (getattr(res, "message", "") or "").splitlines()
            detail = first[0].strip() if first else ""
            self._log(f"Auto-pull blocked: {detail}" if detail
                      else "Auto-pull blocked: local branch could not fast-forward.")

    def _set_autopull(self, on):
        """Enable/disable the background auto-pull, persist it, and broadcast so a
        live control reflects the change. The single writer for the "AutoPull"
        preference — the Git-tab checkbox only emits the command."""
        on = bool(on)
        self._autopull.set_enabled(on)
        try:
            import LibraryManager as LM
            LM.write_setting("AutoPull", on)
        except Exception:  # noqa: BLE001
            pass
        self.ctx.bus.emit("autopull.changed", on)

    def _set_units_mode(self, mode: str):
        """Set the app-wide length unit, persist it, and broadcast so every live
        panel re-renders. The single writer for the "Units" preference — unit
        controls only emit the "units.set_mode" command; this owns the rest."""
        U.set_mode(mode)
        try:
            import LibraryManager as LM
            LM.write_setting("Units", U.mode())
        except Exception:  # noqa: BLE001
            pass
        self.ctx.bus.emit("units.changed", U.mode())

    # -- updates --
    def check_for_updates(self, manual: bool = False):
        """Look for a newer release in the background. Auto-checks only run on a frozen
        build (a source checkout never nags); a manual check runs anywhere so the flow
        is testable. Best-effort — network failures are silent unless `manual`."""
        if not manual and not getattr(sys, "frozen", False):
            return

        def worker():
            upd = None
            try:
                import nd_updater as U
                upd = U.check_for_update(allow_dev=manual)
            except Exception:  # noqa: BLE001
                upd = None
            if upd:
                self._upd.found.emit(upd)
            elif manual:
                try:
                    import nd_updater as U
                    self._upd.none.emit(f"You're on the latest version ({U.current_version()}).")
                except Exception:  # noqa: BLE001
                    self._upd.none.emit("Could not check for updates right now.")
        threading.Thread(target=worker, daemon=True).start()

    def _on_update_available(self, update: dict):
        # reveal the persistent nav badge AND surface the dialog; either the launch
        # auto-check or a manual check lands here.
        self._set_pending_update(update)
        self._show_update_dialog(update)

    def _set_pending_update(self, update: dict):
        self._pending_update = update
        if hasattr(self, "_update_item"):
            self._update_item.setVisible(True)
            # match the current nav width: without this, an update that arrives while
            # the nav is collapsed shows its full "Update Available" label clipped in
            # the 56px rail until the next toggle.
            self._update_item.collapse(self._nav_collapsed)

    def _open_pending_update(self):
        if self._pending_update:
            self._show_update_dialog(self._pending_update)

    def _show_update_dialog(self, update: dict):
        from PyQt5.QtWidgets import QMessageBox
        import nd_updater as U
        box = QMessageBox(self)
        box.setWindowTitle("Update Available")
        box.setIcon(QMessageBox.Information)
        box.setText(f"KiCad Manager {update.get('version')} is available "
                    f"(you have {U.current_version()}).")
        notes = (update.get("notes") or "").strip()
        if notes:
            box.setInformativeText(notes[:500] + ("…" if len(notes) > 500 else ""))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("Update Now")
        box.button(QMessageBox.No).setText("Later")
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() == QMessageBox.Yes:
            self._download_and_apply(update)

    def _download_and_apply(self, update: dict):
        from PyQt5.QtWidgets import QProgressDialog, QMessageBox
        import nd_updater as U
        target = U.exe_path()
        if target is None:                       # dev build: nothing to swap
            self._info("Update", "Updates apply to the installed Windows app only; "
                                 "in a dev build there is no exe to replace.")
            return
        dest = U.staged_path(target)
        dlg = QProgressDialog("Downloading update…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("Updating"); dlg.setWindowModality(Qt.WindowModal)
        dlg.setAutoClose(False); dlg.setAutoReset(False); dlg.setMinimumDuration(0)
        cancelled = {"v": False}
        dlg.canceled.connect(lambda: cancelled.__setitem__("v", True))
        sig = _DownloadSignals()
        sig.progress.connect(lambda d, t: self._on_download_progress(dlg, d, t))

        def finish(ok: bool):
            dlg.close()
            if not ok:
                try:
                    dest.unlink()
                except Exception:  # noqa: BLE001
                    pass
                if not cancelled["v"]:
                    self._warn("Update Failed", "Could not download the update. "
                                                "Please try again later.")
                return
            if U.apply_update_windows(dest, target):
                QApplication.instance().quit()   # the detached helper swaps + relaunches
            else:
                # the automatic swap could not be launched — say so plainly (don't let
                # it read as success) and give the manual fallback.
                self._warn("Automatic Update Didn't Apply",
                           f"The update was downloaded but could not replace the app "
                           f"automatically.\n\nClose the app and replace the exe with this "
                           f"file, then relaunch:\n{dest}")
        sig.done.connect(finish)

        def worker():
            ok = True
            try:
                def prog(d, t):
                    if cancelled["v"]:
                        raise RuntimeError("cancelled")
                    sig.progress.emit(d, t)
                U.download(update, dest, progress=prog)
            except Exception:  # noqa: BLE001
                ok = False
            sig.done.emit(ok and not cancelled["v"])
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _on_download_progress(dlg, done: int, total: int):
        """Update the download dialog. When the release asset reports no total size
        (the asset JSON omitted it), a percentage is impossible, so fall back to an
        indeterminate 'busy' bar and a running byte counter instead of freezing at 0%.
        Restores the determinate percentage bar the moment a real total arrives."""
        if total and total > 0:
            if dlg.maximum() == 0:               # was indeterminate — restore the % bar
                dlg.setRange(0, 100)
            dlg.setValue(int(done * 100 / total))
            dlg.setLabelText(
                f"Downloading update… {NetdeckShell._fmt_bytes(done)} of "
                f"{NetdeckShell._fmt_bytes(total)}")
        else:
            if dlg.maximum() != 0:               # switch to the indeterminate busy bar
                dlg.setRange(0, 0)
            dlg.setLabelText(f"Downloading update… {NetdeckShell._fmt_bytes(done)}")

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        """Human byte count for the download label (e.g. '3.2 MB')."""
        size = float(max(0, int(n)))
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    def _info(self, title: str, msg: str):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self, title, msg)

    def _warn(self, title: str, msg: str):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(self, title, msg)

    def _log(self, msg: str):
        # The Activity console is the DURABLE record (▶/✓/✗ stream + errors); the
        # statusBar keeps the transient 6s flash for a quick glance. Both fed from the
        # one log entry point so nothing a run reports is ever lost off-screen.
        text = str(msg)
        if getattr(self, "_console", None) is not None:
            self._console.append(text)
            # An 'Error:' line (Services logs failures as "Error: {e}") auto-opens the
            # console so a failure can't scroll away unseen — gated by _auto_surface_errors
            # (off under headless). Otherwise a hidden console just bumps the unseen badge.
            if text.strip().lower().startswith("error:") and getattr(self, "_auto_surface_errors", False):
                self._surface_console()
            elif not getattr(self, "_console_open", False):   # count unseen lines for the badge
                self._unseen_activity = getattr(self, "_unseen_activity", 0) + 1
                self._sync_activity_item()
        self.statusBar().showMessage(text, 6000)

    def _surface_console(self):
        """Open the Activity console and expand its body so an error line is visible at
        once (auto-triggered from _log; clears the unseen badge). Only reached when
        _auto_surface_errors is on. Deliberately does NOT persist ConsoleVisible: an error
        forcing the console open is a transient reaction, not the user's choice to keep it
        open — a one-off failure must not leave every future launch showing the console."""
        if not getattr(self, "_console_open", False):
            self._console_open = True
            self._console.setVisible(True)
        self._console.set_expanded(True, notify=False)   # expand the body; don't persist
        self._unseen_activity = 0
        self._sync_activity_item()

    def _persist_console(self, expanded: bool):
        """Persist the console's expanded/collapsed choice so it survives a relaunch."""
        try:
            import LibraryManager as LM
            LM.write_setting("ConsoleExpanded", bool(expanded))
        except Exception:  # noqa: BLE001
            pass


def run():
    # --selftest: a headless launch smoke-test. Construct the whole shell (every page),
    # pump events, and exit 0 WITHOUT entering the GUI loop. The windows-latest release
    # build runs the FROZEN exe with this flag so a build that succeeds but ships a broken
    # exe (a missing PyInstaller hidden import / bundled data file) fails CI instead of the
    # user. Guarded — normal launch is untouched.
    selftest = "--selftest" in sys.argv
    if selftest:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    T.load_fonts(app)
    import LibraryManager as LM
    # SP1: a frozen exe has no repo tree — resolve (and on first run, choose+seed)
    # the writable library location before anything reads config or paths. Skip the
    # interactive first-run chooser under --selftest (headless).
    if getattr(sys, "frozen", False) and not selftest:
        loc = LM.ensure_library_location()
        if loc is None:
            return 0   # user quit the first-run chooser
        LM.apply_library_location(loc)
    cfg = LM.load_config()
    win = NetdeckShell(cfg)
    win.show()
    if selftest:
        for _idx in range(6):            # build every nav page (5 today); extra indices no-op
            try:
                win._select(_idx)
            except Exception:  # noqa: BLE001 - a real build/import issue surfaces as a crash
                pass
        for _ in range(6):
            app.processEvents()
        print("SELFTEST OK: shell constructed", flush=True)
        return 0
    win.check_for_updates()          # frozen builds only; silent if none / offline
    return app.exec_()
