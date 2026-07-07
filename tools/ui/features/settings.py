"""Settings — appearance, paths, sourcing status, library location. Pinned to nav.

SP1: the Mouser API-key field is gone (the app ships a baked key); Sourcing shows
a status line, and a Library Location row lets the user point the app at (or seed)
a writable library folder. Paths are read-mostly, derived from that location.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
                             QFileDialog)

from .. import theme as T
from .. import widgets as W
from .. import feature as F


def _setting_row(title, sub, control) -> W.Card:
    card = W.Card(pad=16)
    row = QHBoxLayout(); row.setSpacing(16)
    col = QVBoxLayout(); col.setSpacing(2)
    t = QLabel(title); t.setFont(T.ui_font(10, semibold=True))
    W.register_restyle(lambda: t.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    col.addWidget(t)
    col.addWidget(W.body(sub, dim=True))
    row.addLayout(col); row.addStretch(1); row.addWidget(control)
    card.body.addLayout(row)
    return card


def _current_location() -> Path:
    import LibraryManager as LM
    return LM.library_location() or LM.REPO_ROOT


def _open_folder():
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(_current_location())))


def _change_location(parent=None):
    import LibraryManager as LM
    chosen = LM._prompt_choose_location(parent)
    if chosen is None:
        return
    LM.write_pointer(chosen)
    LM.apply_library_location(chosen)
    QMessageBox.information(parent, "Library Location",
                            f"Library location set to:\n{chosen}\n\nRestart to load it.")


def _reset_snapshot(parent=None):
    import LibraryManager as LM
    loc = _current_location()
    if QMessageBox.warning(
            parent, "Reset to Bundled Snapshot",
            f"This overwrites the library in\n{loc}\nwith the bundled snapshot. Continue?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
        return
    LM.seed_library(loc, force=True)
    QMessageBox.information(parent, "Reset to Bundled Snapshot",
                            "Library reset from the bundled snapshot. Restart to reload it.")


def _settings_panel(ctx, _s) -> QWidget:
    import LibraryManager as LM
    import stm32_db

    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 20, 24, 24); lay.setSpacing(12)
    lay.setAlignment(Qt.AlignTop)

    lay.addWidget(W.eyebrow("Appearance"))
    lay.addWidget(_setting_row("Theme", "Dark, light, or follow Windows",
                               W.Segmented(["Dark", "Light", "System"], tip="Application theme")))
    lay.addWidget(_setting_row("Selection Accent", "Grayscale ink or the Windows system accent",
                               W.Segmented(["Grayscale Ink", "Windows Accent"], tip="Selection accent colour")))

    # --- Library location (writable; the app manages everything under it) ---
    lay.addWidget(W.eyebrow("Library Location"))
    loc = _current_location()
    loc_card = W.Card(pad=16)
    loc_col = QVBoxLayout(); loc_col.setSpacing(8)
    loc_col.addWidget(W.token(str(loc), dim=True))
    actions = QHBoxLayout(); actions.setSpacing(8)
    actions.addWidget(W.btn("Change", tip="Open an existing library or seed a new one",
                            on_click=lambda: _change_location(root)))
    actions.addWidget(W.btn("Open Folder", tip="Reveal the library folder",
                            on_click=_open_folder))
    actions.addWidget(W.btn("Reset to Bundled Snapshot", tip="Overwrite with the bundled snapshot",
                            on_click=lambda: _reset_snapshot(root)))
    actions.addStretch(1)
    loc_col.addLayout(actions)
    loc_card.body.addLayout(loc_col)
    lay.addWidget(loc_card)

    # --- Derived paths (read-mostly) ---
    lay.addWidget(W.eyebrow("Paths"))
    cfg = ctx.cfg or {}
    pairs = []
    for label, key in (("Repo Root", "RepoRoot"), ("Symbol Library", "SymbolLib"),
                       ("Footprint Library", "FootprintLib"), ("3D Models", "ModelLib")):
        pairs.append((label, W.token(str(cfg.get(key, "")) or "Not Set", dim=True)))
    card = W.Card(pad=16); card.body.addWidget(W.dl(pairs, key_width=160)); lay.addWidget(card)

    # --- Data (bundled, read-only STM32 database) ---
    lay.addWidget(W.eyebrow("Data"))
    n = stm32_db.package_count()
    db_text = (f"Database: bundled, {n} packages (read-only)." if n
               else "Database: not built yet (built in CI for release).")
    lay.addWidget(_setting_row("STM32 Database", db_text, W.body("")))

    # --- Sourcing (baked key; no user field) ---
    lay.addWidget(W.eyebrow("Sourcing"))
    live = bool(LM.resolve_mouser_key())
    status = "Sourcing ready — built-in Mouser key." if live else "Sourcing unavailable — no key in this build."
    lay.addWidget(_setting_row("Mouser", status, W.body("")))

    lay.addStretch(1)
    return root


class SettingsFeature(F.Feature):
    id = "settings"
    title = "Settings"
    order = 900

    def build(self, ctx: F.Context) -> QWidget:
        return W.scroll_body(_settings_panel(ctx, None))


F.register(SettingsFeature())
