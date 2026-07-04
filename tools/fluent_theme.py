"""fluent_theme.py — the QFluentWidgets ↔ NETDECK bridge.

One place that drives QFluentWidgets to our GRAYSCALE-graphite identity so the
redesign is plug-and-play: call `apply_grayscale_fluent(dark)` once at startup,
put `window_qss()` on the top-level window, and use the badge/label helpers.

Design rules this enforces (see docs/design/2026-07-04-app-design-overhaul.md):
  • Accent is our NEUTRAL bright (ui_theme ACCENT), never Fluent blue.
  • Colour does two jobs only: neutral accent (chrome) + desaturated semantic
    status. Counts use a NEUTRAL badge; ok/warn/err use the status badges.
  • Tokens come from ui_theme — the single source of truth — so light/dark and
    the whole app stay in lockstep.

Run it directly to eyeball the grayscale result:
    ./.venv/Scripts/python.exe -m fluent_theme            # opens a window
    QT_QPA_PLATFORM=offscreen ... -m fluent_theme         # saves a PNG instead
"""
from __future__ import annotations

import ui_theme
from qfluentwidgets import setTheme, setThemeColor, Theme, isDarkTheme, InfoBadge

# Desaturated semantic status — the ONLY sanctioned hue, for real state only.
STATUS = {
    "ok":   ("#6f8f6a", "#4a7a44"),   # (dark, light)
    "warn": ("#b8964a", "#8a6a2a"),
    "err":  ("#b96a63", "#9a4a44"),
}


def apply_grayscale_fluent(dark: bool = True, save: bool = False) -> dict:
    """Publish the theme to ui_theme AND drive QFluentWidgets to match, grayscale.
    Returns the active ui_theme token dict."""
    t = ui_theme.set_theme(dark)                 # single source of truth
    setTheme(Theme.DARK if dark else Theme.LIGHT)
    setThemeColor(t["ACCENT"], save=save)        # NEUTRAL accent → grayscale
    return t


def window_qss(t: dict | None = None) -> str:
    """Graphite ground for the top-level window / content panels."""
    t = t or ui_theme.theme()
    return (f"QWidget#ndRoot{{background:{t['WIN_BG']};}}"
            f"QWidget#ndContent{{background:{t['MAIN_BG']};}}")


def status_color(kind: str) -> str:
    """A desaturated semantic colour ('ok'|'warn'|'err') for the active theme."""
    dark = isDarkTheme()
    return STATUS.get(kind, STATUS["ok"])[0 if dark else 1]


def count_badge(text, parent=None):
    """A NEUTRAL badge for plain counts (not a status). Keeps counts grayscale."""
    b = InfoBadge(str(text), parent)
    b.setCustomBackgroundColor(ui_theme.tc("CHIP_BG"), ui_theme.tc("CHIP_BG"))
    return b


def status_badge(kind, text, parent=None):
    """A semantic status badge ('ok'|'warn'|'err') — use ONLY for real state."""
    col = status_color(kind)
    b = InfoBadge(str(text), parent)
    b.setCustomBackgroundColor(col, col)
    return b


def _demo():
    """Standalone grayscale proof — mirrors the Phase-0 spike, sourced from tokens."""
    import os, glob
    from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                                 QTableWidgetItem)
    from PyQt5.QtGui import QFontDatabase
    from qfluentwidgets import (TitleLabel, CaptionLabel, StrongBodyLabel, Pivot,
        PrimaryPushButton, PushButton, TransparentPushButton, LineEdit,
        SimpleCardWidget, TableWidget, setFont)

    app = QApplication.instance() or QApplication([])
    here = os.path.dirname(os.path.abspath(__file__))
    for f in glob.glob(os.path.join(here, "fonts", "*.ttf")):
        QFontDatabase.addApplicationFont(f)

    t = apply_grayscale_fluent(dark=True)
    root = QWidget(); root.setObjectName("ndRoot"); root.setStyleSheet(window_qss(t))
    root.resize(760, 470)
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 20, 24, 20); lay.setSpacing(14)

    lay.addWidget(TitleLabel("STM32 Pins"))
    cap = CaptionLabel("LQFP100 · grayscale bridge demo (tokens from ui_theme)")
    cap.setTextColor(t["FG_DIM"], t["FG_DIM"]); lay.addWidget(cap)

    pivot = Pivot()
    for k, txt in [("map", "Map"), ("table", "Table"), ("cells", "Cells")]:
        pivot.addItem(routeKey=k, text=txt)
    pivot.setCurrentItem("map"); lay.addWidget(pivot)

    row = QHBoxLayout(); row.setSpacing(8)
    row.addWidget(PrimaryPushButton("Import library"))
    row.addWidget(PushButton("Rescan"))
    row.addWidget(TransparentPushButton("Validate"))
    row.addStretch(1)
    le = LineEdit(); le.setPlaceholderText("Filter pins…"); le.setFixedWidth(180)
    row.addWidget(le); lay.addLayout(row)

    card = SimpleCardWidget(); cl = QVBoxLayout(card)
    cl.setContentsMargins(16, 14, 16, 14); cl.setSpacing(10)
    hb = QHBoxLayout(); hb.addWidget(StrongBodyLabel("Must-switch pins")); hb.addStretch(1)
    hb.addWidget(count_badge(26, card))                    # neutral count badge
    hb.addWidget(status_badge("warn", "3 dup", card))      # semantic status badge
    cl.addLayout(hb)
    tbl = TableWidget(); tbl.setColumnCount(4); tbl.setRowCount(3)
    tbl.setHorizontalHeaderLabels(["Pin", "Name", "Destination", "Class"])
    tbl.setBorderVisible(True); tbl.setBorderRadius(6); tbl.verticalHeader().hide()
    for r, rd in enumerate([("72", "PA13·SWDIO", "SWDIO_PARENT", "Must-Switch"),
                            ("1", "VBAT", "VBAT_TGT", "Must-Switch"),
                            ("94", "BOOT0", "SERVICE_BOOT0", "Must-Switch")]):
        for c, v in enumerate(rd):
            tbl.setItem(r, c, QTableWidgetItem(v))
    tbl.setFixedHeight(150); cl.addWidget(tbl); lay.addWidget(card); lay.addStretch(1)

    setFont(root, 14); root.show(); app.processEvents()
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        out = os.path.join(here, "_fluent_demo.png"); root.grab().save(out)
        print("saved", out)
    else:
        app.exec_()


if __name__ == "__main__":
    _demo()
