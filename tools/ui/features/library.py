"""Library — the KiCad parts library workspace (Parts, Sourcing, Import).

All three panels are wired to the pure LibraryManager helpers. Slow / mutating
operations (Mouser lookups, dedupe, repair, auto-assign, ZIP import) run off the
GUI thread via the shell's async service and log to the status line.
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog, QLineEdit

from .. import theme as T
from .. import widgets as W
from ..util import LogSink, run_populate, clear_layout
from .. import feature as F
from .library_preview import PartsList, PartDetail

import LibraryManager as LM


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


def _enrich_from_mpn(ctx, lookup, apply: bool = False) -> dict:
    """Dry-run enrich (fill blank symbol fields from the distributor lookup),
    or apply=True to write. Synchronous core; UI callers wrap it in run_populate."""
    return LM.enrich_library(ctx.cfg, lookup, dry_run=not apply)


def _search_mouser(ctx, query: str, result_layout):
    query = (query or "").strip()
    if not query:
        return
    clear_layout(result_layout)
    result_layout.addWidget(W.body("Searching Mouser...", dim=True))

    def done(rep, ok):
        clear_layout(result_layout)
        hits = (rep or {}).get("results", []) if isinstance(rep, dict) else []
        if not hits:
            result_layout.addWidget(W.body("No matches found.", dim=True)); return
        trows = []
        for r in hits:
            trows.append([W.body(str(r.get("mpn", "")), mono=True),
                          W.body(str(r.get("manufacturer") or ""), dim=True),
                          str(r.get("stock", "")),
                          W.body(str(r.get("description") or ""), dim=True)])
        result_layout.addWidget(W.data_table(
            ["Part Number", "Manufacturer", "Stock", "Description"], trows, stretch_col=(0, 3)), 1)

    run_populate(ctx, lambda: LM.search_parts(query, ctx.cfg), done,
                 busy=f"Searching Mouser for {query}...")


def _sourcing_panel(ctx, _state) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    summary = QHBoxLayout(); summary.setSpacing(8)
    bar.addLayout(summary); bar.addStretch(1)
    btn_refresh = W.btn("Refresh From Mouser", "primary", "Query stock, pricing and lifecycle for every part")
    enrich_btn = W.btn("Enrich From Part Number", "ghost",
                       "Fill blank symbol properties from Mouser (dry run first)")
    bar.addWidget(enrich_btn)
    bar.addWidget(btn_refresh)
    lay.addLayout(bar)

    search_row = QHBoxLayout(); search_row.setSpacing(8)
    q = QLineEdit(); q.setPlaceholderText("Search Mouser by Part Number or Keyword...")
    q.setFont(T.ui_font(10))
    W.register_restyle(lambda: q.setStyleSheet(
        f"QLineEdit{{background:{T.t('inset')};border:none;border-radius:6px;"
        f"padding:6px 10px;color:{T.t('txt1')};}}"))
    result = QVBoxLayout()
    go = W.btn("Search Mouser", "default", "Look up parts online",
               lambda: _search_mouser(ctx, q.text(), result))
    q.returnPressed.connect(lambda: _search_mouser(ctx, q.text(), result))
    search_row.addWidget(q, 1); search_row.addWidget(go)
    lay.addLayout(search_row)
    lay.addLayout(result, 1)

    lookup = LM.providers_from_config(ctx.cfg)
    if lookup is None:
        result.addWidget(W.body("No Mouser API key configured. Add one in Settings to enable live sourcing.", dim=True))
        btn_refresh.setEnabled(False)
        enrich_btn.setEnabled(False)
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

    def do_enrich():
        def dry(res, ok):
            n = len(res.get("changes", [])) if res else 0
            if not n:
                ctx.services.log("Enrich: nothing to fill."); return
            from PyQt5.QtWidgets import QMessageBox
            ans = QMessageBox.question(
                root, "Apply Enrichment",
                f"{n} blank field(s) can be filled from Mouser. Apply?",
                QMessageBox.Yes | QMessageBox.No)
            if ans != QMessageBox.Yes:
                return
            ctx.services.log(f"Enrich: {n} fields fillable. Applying...")
            run_populate(ctx, lambda: _enrich_from_mpn(ctx, lookup, apply=True),
                         lambda r, ok: ctx.services.log(
                             f"Enrich: wrote {len(r.get('changes', []))} fields."
                             if r else "Enrich failed."),
                         busy="Applying enrichment...")
        run_populate(ctx, lambda: _enrich_from_mpn(ctx, lookup, apply=False), dry,
                     busy="Scanning for fillable fields...")

    enrich_btn.clicked.connect(do_enrich)
    return root


def _scan_corrupt(ctx):
    root = ctx.cfg.get("RepoRoot") or ctx.cfg.get("Libs") or "."

    def done(rows, ok):
        if not ok:
            ctx.services.log("Corruption scan failed, see status."); return
        n = len(rows or [])
        if not n:
            ctx.services.log("Corruption scan: no corrupt files found."); return
        ctx.services.log(f"Corruption scan: {n} file(s) flagged.")
        for path, why in rows:
            ctx.services.log(f"  {Path(path).name}: {why}")
    run_populate(ctx, lambda: LM.find_corrupt_kicad_files(root), done,
                 busy="Scanning for corrupt KiCad files...")


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

    pick = W.btn("Choose ZIP", "primary", "Extract, move, merge, auto-link, enrich, then commit", import_zip)
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
    maint.body.addWidget(W.btn("Scan For Corrupt Files", "default",
                               "Check every KiCad file for corruption",
                               lambda: _scan_corrupt(ctx)))
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
