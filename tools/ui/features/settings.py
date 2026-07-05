"""Settings — appearance, paths, sourcing key, git. Pinned to the nav footer."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

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


def _settings_panel(ctx, _s) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 20, 24, 24); lay.setSpacing(12)
    lay.setAlignment(Qt.AlignTop)

    lay.addWidget(W.eyebrow("Appearance"))
    lay.addWidget(_setting_row("Theme", "Dark, light, or follow Windows",
                               W.Segmented(["Dark", "Light", "System"], tip="Application theme")))
    lay.addWidget(_setting_row("Selection Accent", "Grayscale ink or the Windows system accent",
                               W.Segmented(["Grayscale Ink", "Windows Accent"], tip="Selection accent colour")))

    lay.addWidget(W.eyebrow("Paths"))
    cfg = ctx.cfg or {}
    pairs = []
    for label, key in (("Repo Root", "RepoRoot"), ("Symbol Library", "SymbolLib"),
                       ("Footprint Library", "FootprintLib"), ("3D Models", "ModelLib")):
        pairs.append((label, W.token(str(cfg.get(key, "")) or "Not Set", dim=True)))
    card = W.Card(pad=16); card.body.addWidget(W.dl(pairs, key_width=160)); lay.addWidget(card)

    lay.addWidget(W.eyebrow("Sourcing"))
    has_key = bool(cfg.get("MouserApiKey"))
    lay.addWidget(_setting_row("Mouser API Key", "Stored in config.json, never committed",
                               W.body("Configured" if has_key else "Not Set", dim=True)))
    lay.addStretch(1)
    return root


class SettingsFeature(F.Feature):
    id = "settings"
    title = "Settings"
    order = 900

    def build(self, ctx: F.Context) -> QWidget:
        return W.scroll_body(_settings_panel(ctx, None))


F.register(SettingsFeature())
