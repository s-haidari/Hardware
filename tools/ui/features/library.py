"""Library — the KiCad parts library workspace (Parts, Sourcing, Import).

All three panels are wired to the pure LibraryManager helpers. Slow / mutating
operations (Mouser lookups, dedupe, repair, auto-assign, ZIP import) run off the
GUI thread via the shell's async service and log to the status line.
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog

from .. import theme as T
from .. import widgets as W
from ..util import LogSink, run_populate, clear_layout
from .. import feature as F
from .library_preview import PartsList, PartDetail

import LibraryManager as LM


def _asset_flags(has_sym, has_fp, has_mdl) -> QWidget:
    """Which assets a part has, spelled out. Present in full ink, missing dimmed."""
    w = QWidget(); w.setMinimumWidth(210)
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(14)
    for label, on in (("Symbol", has_sym), ("Footprint", has_fp), ("3D Model", has_mdl)):
        lab = QLabel(label); lab.setFont(T.ui_font(9))
        W.register_restyle(lambda lab=lab, on=on: lab.setStyleSheet(
            f"color:{T.t('txt1') if on else T.t('txt3')};background:transparent;"))
        h.addWidget(lab)
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
    lay.addWidget(W.Verdict("Library Health",
                            f"{counts.get('parts', len(rows))} Parts Scanned",
                            "ok", chips, plain=True))

    detail = PartDetail(ctx)
    parts_list = PartsList(rows, on_select=detail.show)

    split = QHBoxLayout(); split.setSpacing(20)
    left = QVBoxLayout(); left.setSpacing(10)
    left.addWidget(parts_list, 1)
    export = W.btn("Export Catalog", "ghost",
                   "Write a Markdown catalog with rendered previews",
                   lambda: _export_catalog(ctx))
    left.addWidget(export)
    left_w = QWidget(); left_w.setFixedWidth(300); left_w.setLayout(left)
    split.addWidget(left_w)
    split.addWidget(W.scroll_body(detail), 1)
    lay.addLayout(split, 1)

    root.parts_list = parts_list        # test/inspection handles
    root.detail = detail
    return root


def _export_catalog(ctx):
    log = LogSink(ctx.services)
    run_populate(ctx, lambda: LM.export_catalog(ctx.cfg, log),
                 lambda p, ok: ctx.services.log(
                     f"Catalog written to {p}" if ok else "Catalog export failed."),
                 busy="Exporting catalog...")


def _sourcing_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    summary = QHBoxLayout(); summary.setSpacing(8)
    bar.addLayout(summary); bar.addStretch(1)
    btn_refresh = W.btn("Refresh From Mouser", "primary", "Query stock, pricing and lifecycle for every part")
    bar.addWidget(W.btn("Enrich From Part Number", "ghost",
                        "Fill blank symbol properties from Mouser (dry run first)"))
    bar.addWidget(btn_refresh)
    lay.addLayout(bar)
    result = QVBoxLayout(); lay.addLayout(result, 1)

    lookup = LM.providers_from_config(ctx.cfg)
    if lookup is None:
        result.addWidget(W.body("No Mouser API key configured. Add one in Settings to enable live sourcing.", dim=True))
        btn_refresh.setEnabled(False)
        return root

    def refresh():
        clear_layout(result)
        result.addWidget(W.body("Querying Mouser...", dim=True))

        def populate(rep, ok):
            clear_layout(result)
            for i in reversed(range(summary.count())):
                w = summary.itemAt(i).widget()
                if w:
                    w.deleteLater()
            if not rep:
                result.addWidget(W.body("Sourcing report unavailable.", dim=True)); return
            c = rep.get("counts", {})
            for txt, kind in ((f"{c.get('found', 0)} Found", "mut"),
                              (f"{c.get('not_on_mouser', 0)} Not on Mouser", "warn"),
                              (f"{c.get('obsolete_nrnd', 0)} Not Recommended or End of Life", "err")):
                summary.addWidget(W.tag(txt, kind))
            trows = []
            for r in rep.get("rows", []):
                on = r.get("on_mouser")
                life = r.get("lifecycle") or "None"
                life_w = W.tag("Not Recommended", "err") if r.get("obsolete") else (W.body(life) if life != "None" else W.body("None", dim=True))
                price = r.get("unit_price")
                trows.append([W.body(str(r.get("mpn", "")), mono=True), W.body(str(r.get("manufacturer") or ""), dim=True),
                              W.tag("Yes", "ok") if on else W.tag("No", "warn"), life_w,
                              str(r.get("stock", "")), f"${price:.2f}" if price else "None",
                              W.body(str(r.get("lead_time") or "None"), dim=True)])
            result.addWidget(W.data_table(
                ["Part Number", "Manufacturer", "On Mouser", "Lifecycle", "Stock", "Unit Price", "Lead Time"],
                trows, stretch_col=0), 1)

        run_populate(ctx, lambda: LM.library_sourcing_report(ctx.cfg, lookup), populate,
                     busy="Refreshing sourcing report from Mouser...")

    btn_refresh.clicked.connect(refresh)
    return root


def _import_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    log = LogSink(ctx.services)

    drop = W.Card(pad=30)
    dl = QLabel("Import A Vendor ZIP"); dl.setFont(T.ui_font(10, semibold=True)); dl.setAlignment(Qt.AlignHCenter)
    W.register_restyle(lambda: dl.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"))
    drop.body.addWidget(dl)
    steps = W.eyebrow("Extract   Move   Merge   Auto-Link   Enrich   Commit"); steps.setAlignment(Qt.AlignHCenter)
    drop.body.addWidget(steps)

    def import_zip():
        fn, _ = QFileDialog.getOpenFileName(root, "Select a vendor ZIP",
                                            str(ctx.cfg.get("Downloads") or ctx.cfg.get("RepoRoot") or "."),
                                            "ZIP Archives (*.zip)")
        if not fn:
            return
        run_populate(ctx, lambda: LM.process_zip(Path(fn), ctx.cfg, log, commit=False, finalize=True),
                     lambda r, ok: ctx.services.log("Import finished." if ok else "Import failed, see status."),
                     busy=f"Importing {Path(fn).name}...")

    pick = W.btn("Choose ZIP...", "primary", "Extract, move, merge, auto-link, enrich, then commit", import_zip)
    row = QHBoxLayout(); row.addStretch(1); row.addWidget(pick); row.addStretch(1)
    drop.body.addLayout(row)
    lay.addWidget(drop)

    def action(fn, busy):
        run_populate(ctx, fn, lambda r, ok: ctx.services.log("Done." if ok else "Failed, see status."), busy=busy)

    maint = W.Card(pad=16)
    maint.body.addWidget(W.eyebrow("Maintenance"))
    for label, tip, fn, busy in (
            ("Dedupe Symbol Library", "Remove duplicate symbols",
             lambda: LM.dedupe_symbol_library(Path(ctx.cfg["SymbolLib"]), log), "Deduping symbols..."),
            ("Repair Footprint And Model Links", "Fix broken links across the library",
             lambda: LM.repair_library(ctx.cfg, log), "Repairing links..."),
            ("Auto-Assign Library", "Link missing footprints and models by identity",
             lambda: LM.auto_assign_library(ctx.cfg, dry_run=False, log=log), "Auto-assigning...")):
        b = W.btn(label, "default", tip, lambda fn=fn, busy=busy: action(fn, busy))
        maint.body.addWidget(b)
    lay.addWidget(maint)
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
