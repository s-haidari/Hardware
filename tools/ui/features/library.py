"""Library — the KiCad parts library workspace (Parts, Sourcing, Import).

Parts reads the real library (scan_library_grouped + library_health_report).
Sourcing and Import present the real actions on the pure LibraryManager helpers;
the live Mouser / write paths wire in as those panels are fleshed out.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from .. import theme as T
from .. import widgets as W
from .. import feature as F

import LibraryManager as LM


def _asset_flags(has_sym, has_fp, has_mdl) -> QWidget:
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(5)
    for label, on in (("SYM", has_sym), ("FP", has_fp), ("3D", has_mdl)):
        chip = QLabel(label); chip.setFont(T.mono_font(8, semibold=True)); chip.setAlignment(Qt.AlignCenter)
        chip.setFixedHeight(20); chip.setFixedWidth(34)
        W.register_restyle(lambda chip=chip, on=on: chip.setStyleSheet(
            f"background:{T.t('ctl_hover') if on else 'transparent'};"
            f"color:{T.t('txt1') if on else T.t('txt3')};border-radius:3px;padding:0 5px;"
            + ("" if on else f"border:1px solid {T.t('stroke')};")))
        h.addWidget(chip)
    h.addStretch(1)
    return w


def _parts_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    cfg = ctx.cfg
    try:
        report = LM.library_health_report(cfg)
        counts = report.get("counts", {})
        rows = LM.scan_library_grouped(cfg)
    except Exception as e:  # noqa: BLE001
        lay.addWidget(W.eyebrow("Library Unavailable"))
        lay.addWidget(W.body(str(e), dim=True)); lay.addStretch(1)
        return root

    chips = [("Complete", str(counts.get("complete", 0)), "ok"),
             ("Missing Model", str(counts.get("missing_model", 0)), "warn"),
             ("Dangling", str(counts.get("dangling", 0)), "err")]
    lay.addWidget(W.Verdict("Library Health", f"{counts.get('parts', len(rows))} Parts Scanned",
                            "ok", chips, plain=True))

    trows = []
    for g in rows[:2000]:
        trows.append([W.body(str(g.get("mpn") or g.get("name") or ""), mono=True),
                      W.body(str(g.get("manufacturer") or ""), dim=True),
                      _asset_flags(g.get("has_symbol"), g.get("has_footprint"), g.get("has_model"))])
    lay.addWidget(W.data_table(["Part Number", "Manufacturer", "Assets"], trows, stretch_col=1), 1)
    return root


def _sourcing_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow("Mouser Sourcing Report"))
    bar.addStretch(1)
    bar.addWidget(W.btn("Enrich From Part Number", "ghost",
                        "Fill blank symbol properties from the distributor, dry run first"))
    bar.addWidget(W.btn("Refresh From Mouser", "default", "Re-query stock, pricing and lifecycle"))
    lay.addLayout(bar)
    has_key = bool((ctx.cfg or {}).get("MouserApiKey"))
    lay.addWidget(W.body(
        "Mouser API key configured. Run a refresh to pull live stock, pricing and lifecycle."
        if has_key else
        "No Mouser API key configured. Add one in Settings to enable live sourcing.",
        dim=not has_key))
    lay.addStretch(1)
    return root


def _import_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    drop = W.Card(pad=30)
    dl = QLabel("Drop A Vendor ZIP To Import"); dl.setFont(T.ui_font(10, semibold=True))
    dl.setAlignment(Qt.AlignHCenter)
    W.register_restyle(lambda: dl.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    drop.body.addWidget(dl)
    steps = W.eyebrow("Extract   Move   Merge   Auto-Link   Enrich   Commit")
    steps.setAlignment(Qt.AlignHCenter)
    drop.body.addWidget(steps)
    lay.addWidget(drop)

    grid = QHBoxLayout(); grid.setSpacing(16)
    maint = W.Card(pad=16)
    maint.body.addWidget(W.eyebrow("Maintenance"))
    for label, tip in (("Dedupe Symbol Library", "Remove duplicate symbols"),
                       ("Repair Footprint And Model Links", "Fix broken links across the library"),
                       ("Auto-Assign Library", "Link missing footprints and models by identity"),
                       ("Restore From Trash", "Restore a snapshot from the .trash safety net")):
        b = W.btn(label, "default", tip); b.setLayoutDirection(Qt.LeftToRight)
        maint.body.addWidget(b)
    grid.addWidget(maint)
    grid.addStretch(1)
    lay.addLayout(grid)
    lay.addStretch(1)
    return root


class LibraryFeature(F.Feature):
    id = "library"
    title = "Library"
    order = 20

    def build(self, ctx: F.Context) -> QWidget:
        panels = [
            ("Parts", lambda c: _parts_panel(c, None)),
            ("Sourcing", lambda c: W.scroll_body(_sourcing_panel(c, None))),
            ("Import", lambda c: W.scroll_body(_import_panel(c, None))),
        ]
        return W.Workspace(ctx, "Library", panels)


F.register(LibraryFeature())
