"""Library — the KiCad parts workspace, a single unified view.

One page: a granular health filter bar, a master parts list, and a detail pane
that says what each part IS (identity + live Mouser sourcing), lets identity be
edited in place, and lets a missing symbol / footprint / 3D model be dropped in.
Sourcing refresh, part-number enrichment, ZIP import, and library maintenance
live in the header action row — no separate sub-tabs. Every mutating operation
runs off the GUI thread via the shell's async service and logs to the status line.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMenu,
                             QFileDialog, QFrame, QLabel)

from .. import theme as T
from .. import widgets as W
from .. import kit as K
from .. import icons
from ..util import LogSink, run_populate, _headless, clear_layout
from .. import feature as F
from ..prose import plural
from . import library_preview as P
from .library_preview import PartsList, PartDetail, DuplicateManagerDialog

import LibraryManager as LM
import nd_git as ND
import nd_commit_msg as CM

# Persisted per-user preference: the last-used Parts grouping. A first run has no value,
# so the panel picks a smart default by library size (see _smart_group_by).
_GROUP_BY_SETTING = "LibraryGroupBy"


def _smart_group_by(n: int) -> str:
    """The default Parts grouping for a library of `n` parts: a small library reads fine
    flat (None), a mid library groups by Category, a big one by Manufacturer — so the
    list is never a wall of ungrouped rows nor over-chopped into tiny groups."""
    if n < 20:
        return "None"
    if n <= 100:
        return "Category"
    return "Manufacturer"


def _model_path(cfg, row) -> str:
    """The row's 3D model file path (portable posix form), or '' when it has none."""
    model = row.get("model")
    if not model:
        return ""
    return (Path(cfg.get("ModelLib", "")) / model).as_posix()


def _part_export_records(cfg, rows) -> list:
    """The export rows for the Export Visible Parts action: identity + completion + the
    3D-model path, one dict per part, in the list's current display order."""
    out = []
    for r in rows:
        out.append({
            "name": r.get("name") or "",
            "mpn": (r.get("mpn") or "") if r.get("has_real_mpn") else "",
            "manufacturer": r.get("manufacturer") or "",
            "category": r.get("category") or "",
            "completion": LM.completion_badge(r),
            "model": _model_path(cfg, r),
        })
    return out


_EXPORT_FIELDS = ["name", "mpn", "manufacturer", "category", "completion", "model"]


def _write_parts_export(path, cfg, rows) -> int:
    """Write the visible parts to `path` as JSON (.json) or CSV (any other suffix), UTF-8
    so Windows never chokes on a unicode name. Returns the record count. Pure — the
    button wraps it in a save dialog, the drive/test path calls it directly."""
    recs = _part_export_records(cfg, rows)
    p = Path(path)
    if p.suffix.lower() == ".json":
        p.write_text(json.dumps(recs, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS)
        w.writeheader()
        w.writerows(recs)
        # newline="" so the csv module's own \r\n line terminators are written verbatim:
        # the default newline translation would turn each \r\n into \r\r\n on Windows
        # (a blank line between every record), which strict CSV parsers / Excel choke on.
        p.write_text(buf.getvalue(), encoding="utf-8", newline="")
    return len(recs)


def _action_row(*buttons) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
    h.addStretch(1)
    for b in buttons:
        h.addWidget(b)
    return row


class _PartsRoot(QWidget):
    """The Parts panel root — accepts a dropped vendor ZIP (SRC-03). `_on_zip(path)`
    is set by the builder to run the import."""

    _on_zip = None
    _drag_hint = None       # set by the builder: fn(bool) lifts the drop-zone highlight

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if self._on_zip and md.hasUrls() and any(
                u.toLocalFile().lower().endswith(".zip") for u in md.urls()):
            e.acceptProposedAction()
            if self._drag_hint:
                self._drag_hint(True)
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        if self._drag_hint:
            self._drag_hint(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        if self._drag_hint:
            self._drag_hint(False)
        if not self._on_zip:
            return
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".zip"):
                e.acceptProposedAction(); self._on_zip(p); return
        e.ignore()


class _DropFront(QFrame):
    """The picker's front-of-library drop zone (library-v2 mockup §2.1): a dashed
    panel with an up-tray glyph and 'Drop a vendor ZIP to add parts / or browse
    files'. Clicking anywhere opens the ZIP file picker; the whole-panel ZIP drop is
    handled by `_PartsRoot` (SRC-03) — `set_dragging` only lifts the highlight while a
    file hovers the panel. Styled centrally by object name (QFrame#dropfront)."""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self.setObjectName("dropfront")
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self); v.setContentsMargins(16, 20, 16, 20); v.setSpacing(6)
        icon = QLabel(); icon.setAlignment(Qt.AlignHCenter)
        # Re-tint the glyph from txt3 on every theme flip (svg_icon bakes a fixed hex).
        def _tint(_i=icon):
            _i.setPixmap(W.svg_icon(icons.GLYPHS["upload"], size=28, color=T.t("txt3")).pixmap(28, 28))
        W.register_restyle(_tint, icon)
        v.addWidget(icon, 0, Qt.AlignHCenter)
        # Centrally-themed labels (no per-widget stylesheet): 'sub' = txt2 semibold
        # region label, 'dim' = txt3 — the mockup's .dl / .ds exactly.
        lead = W.static_label("Drop a vendor ZIP to add parts", "sub")
        lead.setAlignment(Qt.AlignHCenter)
        v.addWidget(lead)
        # "or browse files" — 'browse files' carries the mockup's underline link
        # affordance (txt2 detail-key font, underlined) so it reads as clickable even
        # though the whole zone is; 'or ' stays dim. Centered as one line.
        subrow = QHBoxLayout(); subrow.setContentsMargins(0, 0, 0, 0); subrow.setSpacing(4)
        subrow.addStretch(1)
        pre = W.static_label("or", "dim")
        link = W.static_label("browse files", "key")
        lf = link.font(); lf.setUnderline(True); link.setFont(lf)
        subrow.addWidget(pre); subrow.addWidget(link)
        subrow.addStretch(1)
        v.addLayout(subrow)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._on_click:
            self._on_click()
        super().mousePressEvent(e)

    def set_dragging(self, on: bool) -> None:
        self.setProperty("dragging", bool(on))
        self.style().unpolish(self); self.style().polish(self)


def _run_import_zip(ctx, path: Path, rescan):
    """Extract → move → merge → auto-link → enrich → commit a vendor ZIP, then
    refresh the list. Shared by the Import ZIP button and the drag-drop (SRC-03)."""
    log = LogSink(ctx.services)

    def done(r, ok):
        ctx.services.log("Import finished." if ok else "Import failed, see status.")
        rescan()
    run_populate(ctx, lambda: LM.process_zip(path, ctx.cfg, log, commit=True, finalize=True),
                 done, busy=f"Importing {path.name}...")


def _parts_panel(ctx, _state) -> QWidget:
    root = _PartsRoot()
    if not _headless():
        root.setAcceptDrops(True)
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    cfg = ctx.cfg
    # SHELL-02: build the panel instantly with an empty list, then fill it from the
    # heavy library scan OFF the GUI thread (below) — opening the Library tab used
    # to freeze for seconds while every symbol/footprint was read synchronously.
    rows = []

    # Two-column Components view (library-v2 mockup): a picker (drop zone + finder +
    # Library Tools + grouped list) on the left, and a single flex canvas on the right.
    # PartDetail owns the three preview cards; with no canvas arg it mounts them inline
    # at the top of its own column (the mockup's Files section), so the whole part —
    # Files, Component Fields, Sourcing, actions — lives in one scrollable canvas.
    detail = PartDetail(ctx)

    # Byte-identical footprint GROUPS from the last scan (stem -> its peer stems), so a
    # Duplicate-badge click can gather the exact geometry group, not just "is a dup".
    dstate = {"stem2grp": {}}

    def _set_fp_groups(groups):
        s2g = {}
        for g in groups or []:
            peers = set(g)
            for stem in g:
                s2g[stem] = peers
        dstate["stem2grp"] = s2g

    def _dup_group_for(row) -> list:
        """Every part that duplicates `row`: parts sharing its real manufacturer part
        number, plus parts whose footprint is byte-identical geometry to its footprint
        (the same find_duplicate_footprints group). De-duplicated by identity, `row`
        first, so the resolve modal shows the whole group."""
        group, seen = [], set()

        def _add(r):
            if id(r) not in seen:
                seen.add(id(r)); group.append(r)
        _add(row)
        mpn = (row.get("mpn") or "").strip() if row.get("has_real_mpn") else ""
        peers = dstate["stem2grp"].get(row.get("footprint"), set())
        for r in parts_list._rows:
            same_mpn = mpn and r.get("has_real_mpn") and (r.get("mpn") or "").strip() == mpn
            same_geom = bool(r.get("footprint")) and r.get("footprint") in peers
            if same_mpn or same_geom:
                _add(r)
        return group

    def _resolve_dup(row):
        """A row's Duplicate badge was clicked: open the keep/delete resolve flow on that
        part's whole duplicate group (the one-confirm flow). Returns the dialog so the
        drive/test path can exercise it; exec is guarded off headlessly."""
        grp = _dup_group_for(row)
        dlg = DuplicateManagerDialog(ctx, grp, on_changed=rescan, parent=root)
        if not _headless():
            dlg.exec_()
        return dlg

    # Last-used grouping, else a smart default by library size once the first scan lands.
    saved_group = str(LM.read_setting(_GROUP_BY_SETTING, "") or "")
    parts_list = PartsList(
        rows, on_select=detail.show,
        group_by=(saved_group or "Category"),
        on_group_change=lambda mode: LM.write_setting(_GROUP_BY_SETTING, mode),
        on_resolve_dup=_resolve_dup)

    def _scan_with_dupes():
        """Scan the library AND compute the byte-identical-geometry footprint groups in
        one off-thread pass, so the 'Duplicates only' filter and the Manage Duplicates
        resolve both have their footprint signal without a second thread hop."""
        rows = LM.scan_library_grouped(cfg)
        try:
            groups = LM.find_duplicate_footprints(cfg)
        except Exception:  # noqa: BLE001 — a filter hint never fails the scan
            groups = []
        dup_stems = {s for g in groups for s in g}
        return rows, dup_stems, groups

    def rescan():
        """Re-read the library after a mutation and refresh the list + counts.
        BUG-3: the scan runs OFF the GUI thread (run_populate) like the initial load
        — running it inline froze the UI on every inline edit / drop-in. Under
        offscreen Qt (tests / render_gate) run_populate is synchronous, so the list
        is refreshed by the time this returns."""
        def done(res, ok):
            if not ok or res is None:
                return
            fresh, dup_stems, groups = res
            _set_fp_groups(groups)
            parts_list.set_rows(fresh)
            parts_list.set_duplicate_footprints(dup_stems)
        run_populate(ctx, _scan_with_dupes, done, busy="Rescanning library...")
    detail._on_changed = rescan

    # ── header actions: sourcing, enrich, maintenance, import ──────────────────
    lookup_available = LM.providers_from_config(cfg) is not None

    def refresh_sourcing():
        lookup = LM.providers_from_config(cfg)
        if not lookup:
            ctx.services.log("Add a Mouser API key in Settings to refresh sourcing."); return

        def done(rep, ok):
            if not rep:
                ctx.services.log("Sourcing report unavailable."); return
            c = rep.get("counts", {})
            ctx.services.log(
                f"Sourcing: {c.get('on_mouser', 0)}/{c.get('parts', 0)} on Mouser · "
                f"{c.get('obsolete_nrnd', 0)} NRND · {c.get('out_of_stock', 0)} out of stock.")
            detail.set_sourcing_report(rep)
        run_populate(ctx, lambda: LM.library_sourcing_report(cfg, lookup), done,
                     busy="Refreshing sourcing from Mouser...")

    def do_enrich():
        lookup = LM.providers_from_config(cfg)
        if not lookup:
            ctx.services.log("Add a Mouser API key in Settings to enrich parts."); return

        def dry(res, ok):
            n = len(res.get("changes", [])) if res else 0
            if not n:
                ctx.services.log("Enrich: nothing to fill."); return
            from PyQt5.QtWidgets import QMessageBox
            ans = QMessageBox.question(
                root, "Apply Enrichment",
                f"{plural(n, 'blank field')} can be filled from Mouser. Apply?",
                QMessageBox.Yes | QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

            def applied(r, ok):
                ctx.services.log(f"Enrich: wrote {len(r.get('changes', []))} fields."
                                 if r else "Enrich failed.")
                rescan()
            run_populate(ctx, lambda: _enrich_from_mpn(ctx, lookup, apply=True), applied,
                         busy="Applying enrichment...")
        run_populate(ctx, lambda: _enrich_from_mpn(ctx, lookup, apply=False), dry,
                     busy="Scanning for fillable fields...")

    def import_zip():
        fn, _ = QFileDialog.getOpenFileName(
            root, "Select A Vendor ZIP",
            str(cfg.get("Downloads") or cfg.get("RepoRoot") or "."), "ZIP Archives (*.zip)")
        if fn:
            _run_import_zip(ctx, Path(fn), rescan)

    src_btn = W.btn("Refresh Sourcing", "default",
                    "Query live stock, pricing and lifecycle for every part from Mouser",
                    refresh_sourcing)
    enrich_btn = W.btn("Enrich Blanks", "default",
                       "Fill blank part fields from Mouser (dry run first)", do_enrich)
    src_btn.setEnabled(lookup_available)
    enrich_btn.setEnabled(lookup_available)
    if not lookup_available:
        src_btn.setToolTip("Add a Mouser API key in Settings to enable live sourcing")
        enrich_btn.setToolTip("Add a Mouser API key in Settings to enable enrichment")

    def manage_duplicates():
        """Open the side-by-side resolve modal on the currently multi-selected duplicate
        parts (Ctrl/Shift+click 2+ duplicate rows first). Returns the dialog (or None
        when fewer than 2 duplicates are selected); exec is guarded off headlessly."""
        sel = parts_list.selected_duplicate_rows()
        if len(sel) < 2:
            ctx.services.log("Manage Duplicates: select 2 or more duplicate parts first "
                             "(Ctrl or Shift click, or click a Dup badge to resolve one).")
            return None
        dlg = DuplicateManagerDialog(ctx, sel, on_changed=rescan, parent=root)
        if not _headless():
            dlg.exec_()
        return dlg

    def export_visible(path=None):
        """Export the currently filtered parts to CSV/JSON. `path` is a test/drive seam;
        None opens a save dialog."""
        vis = parts_list.visible_rows()
        if not vis:
            ctx.services.log("Export Visible: no parts in the current view."); return
        p = path
        if p is None:
            if _headless():
                ctx.services.log("Export Visible is unavailable in a headless run."); return
            default = str(Path(cfg.get("RepoRoot") or ".") / "visible_parts.csv")
            p, _f = QFileDialog.getSaveFileName(
                root, "Export Visible Parts", default,
                "CSV (*.csv);;JSON (*.json)")
            if not p:
                return

        def done(n, ok):
            ctx.services.log(f"Exported {plural(n, 'part')} to {Path(p).as_posix()}."
                             if ok else "Export failed, see status.")
        run_populate(ctx, lambda: _write_parts_export(p, cfg, vis), done,
                     busy="Exporting visible parts...")

    # Parts is a browse view — no forced primary. Import is rare/destructive, so it
    # is the quietest (ghost), not the lone primary it used to be.
    import_btn = W.btn("Import ZIP", "ghost",
                       "Extract, move, merge, auto-link, enrich, then commit a vendor ZIP",
                       import_zip)
    dup_btn = W.btn("Manage Duplicates", "default",
                    "Resolve 2+ selected duplicate parts side by side (bulk delete)",
                    manage_duplicates)
    export_btn = W.btn("Export Visible", "default",
                       "Export the currently filtered parts (name, part number, maker, "
                       "category, completion, model path) as CSV or JSON", export_visible)

    def _sync_dup_btn():
        """Manage Duplicates is live only when 2+ duplicate parts are selected."""
        dup_btn.setEnabled(len(parts_list.selected_duplicate_rows()) >= 2)
    # Fire on every selection refresh (incl. the blocked-signal _apply path after a
    # rescan) via the list's selection hook, so the button never goes stale.
    parts_list._on_selection_changed = _sync_dup_btn
    _sync_dup_btn()

    # LIB-08: sourcing + import live on Parts; Maintenance is its own tab now. The scan
    # affordances — Manage Duplicates + Export Visible — sit alongside them.
    lay.addWidget(_action_row(dup_btn, export_btn, src_btn, enrich_btn, import_btn))
    # Filtering + grouping now live in an always-visible inline bar inside PartsList
    # (design-rules §4 — no hidden chrome), not the old filter-button pop.

    root.manage_duplicates = manage_duplicates       # test / drive seams
    root.export_visible = export_visible

    # A maintenance action on the other tab emits library.changed; refresh here.
    bus = getattr(ctx, "bus", None)
    if bus is not None:
        bus.on("library.changed", lambda *_a: rescan())
        # The Sourcing Health tab broadcasts its Mouser sweep (library.sourcing_report);
        # seed the per-part detail cache from it so a just-swept part shows live stock /
        # pricing / lifecycle without a fresh per-part lookup (the cross-tab wire the
        # adversarial review caught as dead — the Health tab emitted, nothing consumed).
        bus.on("library.sourcing_report", lambda rep: detail.set_sourcing_report(rep))

    # SRC-03: drop a vendor ZIP anywhere on the panel to import it (no button hunt).
    root._on_zip = lambda p: _run_import_zip(ctx, Path(p), rescan)

    # ── picker | canvas (kit.panes: drag-resize · picker collapses · widths persist) ──
    picker_w = QWidget()
    # Picker column inset (mockup .picker padding 6px 6px 0 14px → Qt L,T,R,B); the
    # 6px right gap keeps the list off the splitter handle, 14px left gives the drop
    # zone air. Bottom 0 so the list scroll runs to the panel edge.
    left = QVBoxLayout(picker_w); left.setContentsMargins(14, 6, 6, 0); left.setSpacing(10)
    # Front-of-library drop zone (library-v2 §2.1): click to browse a vendor ZIP, or
    # drop one anywhere on the panel (the whole-panel drop stays _PartsRoot/SRC-03; the
    # zone just makes the affordance obvious and lifts while a file is dragged over).
    dropfront = _DropFront(import_zip)
    left.addWidget(dropfront)
    root._drag_hint = dropfront.set_dragging

    # Library Tools (mockup .pktools): a curated front door to the highest-value
    # whole-library maintenance ops (Refresh Sourcing / Deduplicate / Auto-Assign /
    # Fix Broken Links / Integrity Scan). The Maintenance + Sourcing-Health subtabs stay.
    def open_library_tools():
        from .library_preview import LibraryToolsDialog
        LibraryToolsDialog(ctx, on_changed=rescan, parent=root).exec_()
    tools_btn = W.btn("Library Tools", "default",
                      "Curated whole-library maintenance: sourcing, dedup, links, integrity",
                      open_library_tools)
    left.addWidget(tools_btn)
    root.open_library_tools = open_library_tools     # test/drive handle
    # A calm skeleton stands in for the list while the initial scan runs (instead of
    # a bare "Scanning…" line); it is swapped for the real list once loaded.
    scan_skeleton = W.skeleton_rows(8, 2)
    left.addWidget(scan_skeleton)
    left.addWidget(parts_list, 1)
    parts_list.setVisible(False)
    # Two panes; the picker collapses so a user can give the canvas the whole width,
    # but the canvas (the working surface) never collapses. Widths persist under a new
    # key ("library2" — the pane count/semantics changed from the old 3-pane layout).
    split = K.panes([picker_w, W.scroll_body(detail)],
                    key="library2", sizes=[348, 812],
                    collapsible=[True, False], min_widths=[300, 520])
    lay.addWidget(split, 1)

    # SHELL-02: initial load off the GUI thread. Headless (tests / render_gate) runs
    # run_populate synchronously, so the list is already populated on return; the
    # real app shows the panel immediately and fills it a moment later.
    def _loaded(res, ok):
        scan_skeleton.setVisible(False)
        parts_list.setVisible(True)
        if not ok or res is None:
            ctx.services.log("Library scan failed. See status."); return
        fresh, dup_stems, groups = res
        _set_fp_groups(groups)
        parts_list.set_rows(fresh)
        parts_list.set_duplicate_footprints(dup_stems)
        # First run (no persisted grouping): pick the smart default by library size and
        # persist it so it survives the next launch. Persist explicitly — set_group_by is
        # a no-op (no persist) when the smart default already equals the current mode.
        if not str(LM.read_setting(_GROUP_BY_SETTING, "") or ""):
            sd = _smart_group_by(len(fresh))
            parts_list.set_group_by(sd)
            LM.write_setting(_GROUP_BY_SETTING, sd)
        _sync_dup_btn()
    run_populate(ctx, _scan_with_dupes, _loaded, busy="Scanning library...")

    root.parts_list = parts_list        # test/inspection handles
    root.detail = detail
    root.dropfront = dropfront
    return root


def _summarize_dedupe(removed) -> str:
    """dedupe_symbol_library returns the number of duplicate blocks removed."""
    n = int(removed or 0)
    return (f"Dedupe Symbol Library: removed {plural(n, 'duplicate')}."
            if n else "Dedupe Symbol Library: no duplicates.")


def _summarize_repair(result) -> str:
    """repair_library returns {symbols_fixed, footprints_fixed, footprints_no_model}."""
    r = result or {}
    syms = int(r.get("symbols_fixed", 0))
    fps = int(r.get("footprints_fixed", 0))
    no_model = int(r.get("footprints_no_model", 0))
    if not syms and not fps:
        tail = f" ({plural(no_model, 'footprint')} still without a model)" if no_model else ""
        return f"Repair Footprint And Model Links: nothing to fix.{tail}"
    parts = []
    if syms:
        parts.append(f"{plural(syms, 'symbol link')}")
    if fps:
        parts.append(f"{plural(fps, 'model line')}")
    tail = f"; {plural(no_model, 'footprint')} still without a model" if no_model else ""
    return f"Repair Footprint And Model Links: fixed {' and '.join(parts)}{tail}."


def _summarize_dedupe_footprints(removed) -> str:
    """dedupe_footprint_library returns the number of duplicate footprint files
    removed (density variants are never counted — they aren't duplicates)."""
    n = int(removed or 0)
    return (f"Deduplicate Footprints: removed {plural(n, 'duplicate footprint')}."
            if n else "Deduplicate Footprints: no true duplicates "
                      "(density variants are kept).")


def _summarize_auto_assign(result) -> str:
    """auto_assign_library returns {footprint_count, model_count, ...}."""
    r = result or {}
    fps = int(r.get("footprint_count", 0))
    mdls = int(r.get("model_count", 0))
    if not fps and not mdls:
        return "Auto-Assign Library: nothing to link."
    parts = []
    if fps:
        parts.append(f"{plural(fps, 'footprint')}")
    if mdls:
        parts.append(f"{plural(mdls, 'model')}")
    return f"Auto-Assign Library: linked {' and '.join(parts)}."


def _maintenance_workbench(ctx) -> QWidget:
    """The Maintenance workbench (Phase-2 convergence): the workshop side of the library
    on the ``kit.workbench`` recipe. Verdict = waiting vendor ZIPs (quiet when none);
    detail = the Imports + Undo History cards refreshed in place; ▶ Process Waiting ZIPs
    (preview → batch import → one commit); secondaries = the import / dedupe / repair /
    trash / clean tools (destructive ones confirm-then-commit); machinery = library
    location, KiCad registration, and hand-off portability. Returns the ``host`` body
    (the caller wraps it in ``W.scroll_body`` + ``W.Workspace``)."""
    cfg = ctx.cfg
    busy = K.BusyDict()
    log = getattr(getattr(ctx, "services", None), "log", None)
    lsink = LogSink(ctx.services)       # the UILog the backend tools write their steps to
    ui: dict = {"buttons": []}

    def _log(line):
        if callable(log):
            log(str(line))

    def snapshot() -> dict:
        """GUI-thread selection dict (cheap directory listings only): the waiting vendor
        ZIPs in Downloads and the undo snapshots behind the symbol library."""
        zips, snaps = [], []
        dl = (cfg or {}).get("Downloads")
        if dl:
            try:
                zips = sorted(str(p) for p in Path(dl).glob("*.zip"))
            except OSError:
                pass
        try:
            snaps = [str(p) for p in LM.list_trash_snapshots(Path(cfg["SymbolLib"]))]
        except Exception:  # noqa: BLE001 — no SymbolLib yet is a valid (empty) state
            pass
        return {"cfg": cfg, "zips": zips, "snapshots": snaps}

    def _apply_enablement():
        on = not busy["on"]
        for _t, b in ui.get("buttons", ()):
            try:
                b.setEnabled(on)
            except RuntimeError:        # a button deleted by a region rebuild — skip it
                pass

    busy.on_change = _apply_enablement

    # ── verdict: waiting ZIPs (hidden when the workshop floor is clear) ────────────────
    def verdict(snap):
        n = len(snap.get("zips", ()))
        if not n:
            return None                 # quiet-when-OK: no band at all
        dl = (snap.get("cfg") or {}).get("Downloads") or ""
        return W.VerdictState(kind="warn", title=f"{plural(n, 'ZIP')} Waiting",
                              subtitle=Path(dl).as_posix() if dl else "")

    # ── detail: Imports + Undo History cards (chrome once, fill static) ────────────────
    def detail(snap, handle):
        body = QWidget()
        col = QVBoxLayout(body); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(14)
        col.addWidget(W.eyebrow("Imports"))
        zcard = W.Card(pad=16)
        col.addWidget(zcard)
        col.addWidget(W.eyebrow("Undo History"))
        tcard = W.Card(pad=16)
        col.addWidget(tcard)

        # A maintenance op elsewhere (or the Parts drag-drop) changes the library; keep
        # this tab live. Owner = the chrome, so a region rebuild drops the subscription.
        on_owned = getattr(getattr(ctx, "bus", None), "on_owned", None)
        if callable(on_owned):
            on_owned("library.changed", lambda *_a: host._refresh(), body)

        def _fill_list(card, items, noun, empty_line, cap):
            clear_layout(card.body)
            if not items:
                card.body.addWidget(W.static_label(empty_line, "dim"))
                return
            head = QHBoxLayout(); head.setSpacing(8)
            head.addWidget(W.static_label(noun, "sub"))
            head.addWidget(W.static_label(f"{len(items)}", "dim"))
            head.addStretch(1)
            card.body.addLayout(head)
            for it in items[:cap]:
                card.body.addWidget(W.static_label(Path(it).name, "body"))
            if len(items) > cap:
                card.body.addWidget(W.static_label(f"+{len(items) - cap} more", "dim"))

        def fill(s):
            # An off-thread op flagged a library change; announce it HERE, on the GUI
            # thread, on the post-op refresh (the Parts tab rescans on this signal).
            if ui.pop("pending_changed", False):
                bus = getattr(ctx, "bus", None)
                if bus is not None:
                    bus.emit("library.changed")
            _fill_list(zcard, s.get("zips", []), "Waiting ZIPs",
                       "No ZIPs Waiting. Drop vendor ZIPs in Downloads to import them.", 12)
            _fill_list(tcard, s.get("snapshots", []), "Undo Snapshots",
                       "No Undo Snapshots. Library edits snapshot here automatically.", 8)

        return body, fill

    # ── the ▶ Process Waiting ZIPs primary flow ────────────────────────────────────────
    def _pz_audit(snap):
        zips = snap.get("zips", [])
        if not zips:
            pz_flow.empty = "No vendor ZIPs are waiting in Downloads."
            return []
        return [{"key": z, "label": Path(z).name, "detail": "", "safe": True} for z in zips]

    def _pz_intro(snap, ops):
        return ("Each checked ZIP is extracted, its symbols merged, its footprints and 3D "
                "models installed and auto-linked, blanks enriched, then committed:")

    def _pz_apply(snap, keys):
        zips, c = snap.get("zips", []), snap.get("cfg")
        done, errors = [], []
        if keys and set(keys) == set(zips):
            # Everything selected → the proven one-finalize-one-commit batch path.
            try:
                LM.process_existing_zips(c, lsink)
                done = [f"processed {Path(z).name}" for z in zips]
            except Exception as e:  # noqa: BLE001
                errors.append(f"batch import: {e}")
        else:
            for k in keys:
                try:
                    LM.process_zip(Path(k), c, lsink, commit=True, finalize=True)
                    done.append(f"processed {Path(k).name}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{Path(k).name}: {e}")
        ui["pending_changed"] = True
        return {"summary": (f"Processed {plural(len(done), 'ZIP')}."
                            if done else "Nothing processed."),
                "done": done, "errors": errors}

    pz_flow = K.PrimaryFlow(
        label="▶ Process Waiting ZIPs", audit=_pz_audit, intro=_pz_intro, apply=_pz_apply,
        tip="Import every waiting vendor ZIP: extract, merge, install, auto-link, "
            "enrich, then commit in one pass",
        empty="No vendor ZIPs are waiting in Downloads.")

    # ── op runners (busy-gated, off-thread, then refresh) ──────────────────────────────
    def _confirm(text) -> bool:
        from PyQt5.QtWidgets import QMessageBox
        ans = QMessageBox.question(host, "Confirm Maintenance", text,
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            _log("Cancelled.")
            return False
        return True

    def _run_op(label, work, *, busy_label=None, changes_library=False):
        """Run a mutating op OFF-thread, busy-gated, then log its one-line result and
        refresh (cards + verdict + enablement). ``work()`` returns the human line."""
        if busy["on"]:
            return
        busy["on"] = True

        def done(line, ok):
            busy["on"] = False
            if ok and changes_library:
                ui["pending_changed"] = True
            _log(line if ok else f"{label} failed, see status.")
            host._region.handle.refresh()

        run_populate(ctx, work, done, busy=busy_label or f"{label}…")

    def _report_op(label, work, *, mutating=False, changes_library=False, busy_label=None):
        """Run an op OFF-thread and show its structured result via ``kit._report``
        (headless: logs the summary). ``mutating`` ops take the busy gate + refresh."""
        if mutating and busy["on"]:
            return
        if mutating:
            busy["on"] = True

        def done(result, ok):
            if mutating:
                busy["on"] = False
                if ok and changes_library:
                    ui["pending_changed"] = True
            K._report(host, label, result if ok else {"errors": ["operation failed"]}, log=log)
            if mutating:
                host._region.handle.refresh()

        run_populate(ctx, work, done, busy=busy_label or f"{label}…")

    def _tool(title, work, *, confirm, commit_msg, summarize):
        """A destructive whole-library tool: confirm → run off-thread → commit+push on
        success (off-thread too) → summarize the REAL outcome into the log."""
        if busy["on"]:
            return
        if not _confirm(confirm):
            return

        def job():
            r = work()
            if commit_msg:
                LM.git_commit_push(cfg, lsink, commit_msg)
            return r

        def done(r, ok):
            busy["on"] = False
            if ok:
                ui["pending_changed"] = True
            _log(summarize(r) if ok else f"{title} failed, see status.")
            host._region.handle.refresh()

        busy["on"] = True
        run_populate(ctx, job, done, busy=f"{title}…")

    # ── secondaries ────────────────────────────────────────────────────────────────────
    def _import_zip(path=None):
        p = path
        if p is None:
            if _headless():
                _log("Import Vendor ZIP is unavailable in a headless run."); return
            p, _f = QFileDialog.getOpenFileName(
                host, "Select A Vendor ZIP",
                str(cfg.get("Downloads") or cfg.get("RepoRoot") or "."), "ZIP Archives (*.zip)")
            if not p:
                return

        def work():
            LM.process_zip(Path(p), cfg, lsink, commit=True, finalize=True)
            return f"Imported {Path(p).name}."

        _run_op("Import Vendor ZIP", work, busy_label=f"Importing {Path(p).name}…",
                changes_library=True)

    def _import_folder(path=None):
        p = path
        if p is None:
            if _headless():
                _log("Import Extracted Folder is unavailable in a headless run."); return
            p = QFileDialog.getExistingDirectory(
                host, "Select An Extracted Part Folder",
                str(cfg.get("Downloads") or cfg.get("RepoRoot") or "."))
            if not p:
                return

        def work():
            LM.move_files(Path(p), cfg, lsink)
            LM.git_commit_push(cfg, lsink, CM.import_parts([Path(p).name]))
            return f"Imported folder {Path(p).name}."

        _run_op("Import Extracted Folder", work, busy_label=f"Importing {Path(p).name}…",
                changes_library=True)

    def _merge_symbol_files(target=None, sources=None):
        srcs = sources
        if srcs is None:
            if _headless():
                _log("Merge Symbol Files is unavailable in a headless run."); return
            srcs, _f = QFileDialog.getOpenFileNames(
                host, "Symbol Files To Merge In", str(cfg.get("RepoRoot") or "."),
                "KiCad Symbol Libraries (*.kicad_sym)")
            if not srcs:
                return
        tgt = Path(target) if target else Path(cfg["SymbolLib"])

        def work():
            LM.merge_symbols(tgt, [Path(s) for s in srcs], lsink)
            LM.git_commit_push(cfg, lsink, "chore(lib): merge symbol files")
            return f"Merged {plural(len(srcs), 'symbol file')} into {tgt.name}."

        _run_op("Merge Symbol Files", work, changes_library=True)

    def _dedupe_symbols():
        _tool("Dedupe Symbol Library",
              lambda: LM.dedupe_symbol_library(Path(cfg["SymbolLib"]), lsink),
              confirm="Rewrite the symbol library, keeping only the first definition of "
                      "each duplicated part? This edits the library file and commits the result.",
              commit_msg="chore(lib): dedupe symbol library",
              summarize=_summarize_dedupe)

    def _dedupe_footprints():
        _tool("Deduplicate Footprints",
              lambda: LM.dedupe_footprint_library(cfg, lsink),
              confirm="Delete footprint files whose geometry is byte-identical to another "
                      "(keeping the first of each set)? Density variants are never touched. "
                      "This edits the library and commits the result.",
              commit_msg="chore(lib): deduplicate footprints",
              summarize=_summarize_dedupe_footprints)

    def _repair_links():
        _tool("Repair Footprint And Model Links",
              lambda: LM.repair_library(cfg, lsink),
              confirm="Rewrite library files to reconnect broken footprint and 3D-model "
                      "links? This edits the library and commits the result.",
              commit_msg="chore(lib): repair footprint and model links",
              summarize=_summarize_repair)

    def _auto_assign():
        _tool("Auto-Assign Library",
              lambda: LM.auto_assign_library(cfg, dry_run=False, log=lsink),
              confirm="Link unlinked symbols to matching footprints and models by name? "
                      "This edits the library and commits the result.",
              commit_msg="chore(lib): auto-assign footprints and models",
              summarize=_summarize_auto_assign)

    def _scan_corrupt():
        root = cfg.get("RepoRoot") or cfg.get("Libs") or "."

        def work():
            bad = ND.find_corrupt_kicad_files(root)
            if not bad:
                return {"summary": "No corrupt KiCad files found."}
            return {"summary": f"{plural(len(bad), 'corrupt KiCad file')} found.",
                    "missing": [{"item": Path(p).as_posix(), "why": why,
                                 "how_to_fix": "Fix the merge markers / balance the parens, "
                                               "or discard the file."}
                                for p, why in bad]}

        _report_op("Scan For Corrupt Files", work)

    def _undo_last():
        if busy["on"]:
            return
        if not snapshot()["snapshots"]:
            _log("No undo snapshots to restore."); return
        if not _confirm("Restore the symbol library from the most recent undo snapshot? "
                        "The current library file is replaced (the snapshot itself is kept, "
                        "so this restore is also undoable) and the result is committed."):
            return

        def work():
            if not LM.restore_last_trash(Path(cfg["SymbolLib"]), lsink):
                return None
            LM.git_commit_push(cfg, lsink, "chore(lib): undo last change (restore from .trash)")
            return "Restored the library from the last undo snapshot."

        def done(line, ok):
            busy["on"] = False
            if ok and line:
                ui["pending_changed"] = True
                _log(line)
            else:
                _log("Restore failed. No snapshot applied.")
            host._region.handle.refresh()

        busy["on"] = True
        run_populate(ctx, work, done, busy="Restoring the last snapshot…")

    def _empty_trash():
        if busy["on"]:
            return
        if not _confirm("Delete every undo snapshot? Past library states can no longer "
                        "be restored from the app after this."):
            return

        def work():
            n = LM.empty_trash(Path(cfg["SymbolLib"]), lsink)
            return f"Removed {plural(n, 'undo snapshot')}."

        _run_op("Empty Undo History", work)

    def _clean_downloads():
        if busy["on"]:
            return
        if not _confirm("Delete every ZIP and extracted folder left in Downloads? "
                        "This cannot be undone."):
            return

        def work():
            LM.clean_leftovers(cfg, lsink)
            return "Cleaned Downloads leftovers."

        _run_op("Clean Downloads Leftovers", work)

    def _export_catalog():
        def work():
            p = LM.export_catalog(cfg, lsink)
            return (f"Catalog written to {Path(p).as_posix()}" if p
                    else "Catalog export failed.")

        _run_op("Export Catalog", work, busy_label="Exporting catalog…")

    # ── machinery: location · registration · portability ──────────────────────────────
    def _change_location(path=None):
        p = path
        if p is None:
            if _headless():
                _log("Change Library Location is unavailable in a headless run."); return
            p = QFileDialog.getExistingDirectory(host, "Choose The Library Location",
                                                 str(cfg.get("RepoRoot") or "."))
            if not p:
                return
        root = Path(p)
        if not root.is_dir() or not os.access(str(root), os.W_OK):
            K._report(host, "Change Library Location",
                      {"errors": [f"{root.as_posix()} is not a writable directory."]}, log=log)
            return
        # The proven order (bare): record the pointer, rebind the module globals, then
        # reload config so every derived path re-derives from the new location. The
        # SHARED cfg dict is updated IN PLACE so every panel sees the move.
        LM.write_pointer(root)
        LM.apply_library_location(root)
        fresh = LM.load_config()
        cfg.clear()
        cfg.update(fresh)
        _log(f"Library location set: {root.as_posix()}")
        ui["pending_changed"] = True
        host._region.handle.rebuild()   # deferred: re-derive chrome from the new cfg
        host._refresh()                 # verdict + cards + the pending announce now

    def _setup_kicad():
        def work():
            r = LM.register_libraries(cfg, lsink)
            return {"summary": str(r.get("message")
                                   or ("Libraries registered." if r.get("ok")
                                       else "Registration failed."))}

        _report_op("Set Up KiCad Libraries", work, mutating=True)

    def _check_handoff():
        def work():
            r = LM.verify_handoff_readiness(cfg)
            issues = r.get("issues") or []
            if not issues:
                return {"summary": "Ready to hand off. Every reference is portable."}
            return {"summary": f"{plural(len(issues), 'portability issue')} found.",
                    "missing": [{"item": str(i.get("ref", "?")),
                                 "why": f"{i.get('kind', '')}: {i.get('detail', '')}",
                                 "how_to_fix": str(i.get("how_to_fix", ""))}
                                for i in issues[:100]]}

        _report_op("Check Hand-Off Readiness", work)

    def _make_portable():
        if busy["on"]:
            return
        if not _confirm("Rewrite every footprint and 3D-model reference to its portable "
                        "form (MyFootprints:<stem> / ${MY3DMODELS}/<file>), register the "
                        "libraries in KiCad, and commit? Unresolvable references are "
                        "reported, never guessed."):
            return

        def work():
            before = LM.verify_handoff_readiness(cfg)
            res = LM.make_library_portable(cfg, lsink)
            LM.git_commit_push(cfg, lsink, "chore(lib): make library hand-off portable")
            after = LM.verify_handoff_readiness(cfg)
            n_b = len(before.get("issues") or [])
            n_a = len(after.get("issues") or [])
            return {"summary": f"Portability: fixed {plural(res.get('symbols_fixed', 0), 'symbol reference')} "
                               f"and {plural(res.get('models_fixed', 0), 'model line')}; "
                               f"{plural(n_a, 'issue')} remain (was {n_b}).",
                    "missing": [{"item": str(i.get("ref", "?")),
                                 "why": f"{i.get('kind', '')}: {i.get('detail', '')}",
                                 "how_to_fix": str(i.get("how_to_fix", ""))}
                                for i in (after.get("issues") or [])[:100]]}

        _report_op("Make Portable and Commit", work, mutating=True, changes_library=True)

    # ── assemble the workbench ─────────────────────────────────────────────────────────
    secondary = [
        K.action("Import Vendor ZIP…", lambda: _import_zip(),
                 tip="Extract, merge, install, auto-link, enrich, then commit one vendor ZIP"),
        K.action("Import Extracted Folder…", lambda: _import_folder(),
                 tip="Merge an already-extracted part folder into the library and commit"),
        K.action("Merge Symbol Files…", lambda: _merge_symbol_files(),
                 tip="Merge other .kicad_sym files into the library (duplicates skipped)"),
        K.action("Dedupe Symbol Library", _dedupe_symbols,
                 tip="Merge duplicate symbol definitions so every part appears exactly once"),
        K.action("Deduplicate Footprints", _dedupe_footprints,
                 tip="Remove byte-identical footprint files. IPC density variants are kept"),
        K.action("Repair Footprint And Model Links", _repair_links,
                 tip="Reconnect symbols to their footprints and 3D models where a reference broke"),
        K.action("Auto-Assign Library", _auto_assign,
                 tip="Link unlinked symbols to matching footprints and models by name"),
        K.action("Scan For Corrupt Files…", _scan_corrupt,
                 tip="Check every KiCad file for truncation or parse damage"),
        K.action("Undo Last Change", _undo_last,
                 tip="Restore the symbol library from its most recent automatic snapshot"),
        K.action("Empty Undo History", _empty_trash,
                 tip="Delete every undo snapshot"),
        K.action("Clean Downloads Leftovers", _clean_downloads,
                 tip="Delete leftover ZIPs and extracted folders from Downloads"),
        K.action("Export Catalog", _export_catalog,
                 tip="Write a one-file Markdown catalog of the whole library with previews"),
    ]
    machinery = [
        K.action("Change Library Location…", lambda: _change_location(),
                 tip="Move the app to a different library folder (pointer + config reload)"),
        K.action("Set Up KiCad Libraries", _setup_kicad,
                 tip="Register MySymbols / MyFootprints / ${MY3DMODELS} in KiCad's tables"),
        K.action("Check Hand-Off Readiness", _check_handoff,
                 tip="Audit every reference for portability breaks (read-only)"),
        K.action("Make Portable and Commit", _make_portable,
                 tip="Rewrite references portable, register the libraries, then commit"),
    ]

    host = K.workbench(ctx, title="Maintenance", snapshot=snapshot, verdict=verdict,
                       detail=detail, primary=pz_flow, secondary=secondary,
                       machinery=machinery, busy=busy)

    ui["buttons"] = [(b.text(), b) for b in host.findChildren(QPushButton)
                     if not b.text().startswith(("▸", "▾"))]

    _raw_run = host._run_primary

    def _run_primary():
        if busy["on"]:
            return
        _raw_run()

    host._run_primary = _run_primary

    # Test / drive seams.
    host._snapshot = snapshot
    host._import_zip = _import_zip
    host._import_folder = _import_folder
    host._merge_symbol_files = _merge_symbol_files
    host._dedupe_symbols = _dedupe_symbols
    host._dedupe_footprints = _dedupe_footprints
    host._repair_links = _repair_links
    host._auto_assign = _auto_assign
    host._scan_corrupt = _scan_corrupt
    host._undo_last = _undo_last
    host._empty_trash = _empty_trash
    host._clean_downloads = _clean_downloads
    host._export_catalog = _export_catalog
    host._change_location = _change_location
    host._setup_kicad = _setup_kicad
    host._check_handoff = _check_handoff
    host._make_portable = _make_portable
    host._btn = lambda text: next((b for t, b in ui.get("buttons", ()) if t == text), None)

    _apply_enablement()
    return host


def _enrich_from_mpn(ctx, lookup, apply: bool = False) -> dict:
    """Dry-run enrich (fill blank symbol fields from the distributor lookup),
    or apply=True to write. Synchronous core; UI callers wrap it in run_populate."""
    return LM.enrich_library(ctx.cfg, lookup, dry_run=not apply)


def _mk_health_verdict(rep):
    """`library_health_report` → the tab's VerdictState (None until the first scan)."""
    if not rep:
        return None
    c = rep.get("counts", {})
    parts = int(c.get("parts", 0))
    complete = int(c.get("complete", 0))
    if parts == 0:
        return W.VerdictState(kind="mut", title="No Parts Yet",
                              subtitle="Import parts on the Maintenance tab to begin.")
    if complete == parts:
        return W.VerdictState(kind="ok", title="All Parts Complete",
                              subtitle=f"{plural(parts, 'part')} · symbol, "
                                       "footprint and 3D model all present")
    cand = [("Dangling", int(c.get("dangling", 0)), "err"),
            ("No Footprint", int(c.get("missing_footprint", 0)), "warn"),
            ("No 3D Model", int(c.get("missing_model", 0)), "warn"),
            ("No Manufacturer", int(c.get("no_manufacturer", 0)), "mut")]
    chips = [(lab, str(n), kind) for lab, n, kind in cand if n][:3]
    return W.VerdictState(kind="warn", title=f"{parts - complete} Incomplete",
                          subtitle=f"{complete} of {parts} parts complete", chips=chips)


def _health_workbench(ctx) -> QWidget:
    """Sourcing Health as a workbench (Phase-2 convergence). The verdict band is the
    structural health count (`library_health_report`, computed off-thread on every
    refresh); the detail region carries the per-category findings plus the opt-in Mouser
    sweep; the single accent is **▶ Fix All From Library** — every safe structural
    completion the library itself can satisfy (create stub symbols for orphans, link
    footprints and 3D models by name), previewed per-op, applied per part through the
    proven ``complete_part_plan``/``apply_complete_part`` engine, then committed ONCE.
    Identity fills from the distributor stay in Enrich Blanks (rate-limited, opt-in), so
    the ▶ is fast and offline-safe. The sweep never runs at build time (headless hang)."""
    cfg = ctx.cfg
    busy = K.BusyDict()
    log = getattr(getattr(ctx, "services", None), "log", None)
    lsink = LogSink(ctx.services)
    ui: dict = {"health": None, "sourcing": None, "buttons": [],
                "lookup_ok": LM.providers_from_config(cfg) is not None}

    def _log(line):
        if callable(log):
            log(str(line))

    def snapshot() -> dict:
        return {"cfg": cfg}

    # Base tooltips restored whenever an availability gate re-enables a button.
    _TIPS = {
        "Run Sourcing Check": "Look up every orderable part on Mouser and flag "
                              "obsolete, NRND and out-of-stock parts",
        "Enrich Blanks From Distributor": "Fill blank identity fields from the "
                                          "distributor (dry run and confirm first)",
        "Export Sourcing Report": "Save the latest sourcing sweep as Markdown",
    }

    def _apply_enablement():
        on = not busy["on"]
        for text, b in ui.get("buttons", ()):
            try:
                if text in ("Run Sourcing Check", "Enrich Blanks From Distributor"):
                    ok = on and ui.get("lookup_ok", False)
                    b.setEnabled(ok)
                    b.setToolTip(_TIPS[text] if ui.get("lookup_ok", False) else
                                 "Add a Mouser API key in Settings to enable this.")
                elif text == "Export Sourcing Report":
                    have = ui.get("sourcing") is not None
                    b.setEnabled(on and have)
                    b.setToolTip(_TIPS[text] if have else
                                 "Run Sourcing Check first; the report is exported "
                                 "from its results.")
                else:
                    b.setEnabled(on)
            except RuntimeError:
                pass

    busy.on_change = _apply_enablement

    def _status_tag(r):
        if not r.get("found"):
            return W.tag("Not on Mouser", "err")
        if r.get("obsolete"):
            return W.tag("Obsolete/NRND", "err")
        if not r.get("in_stock"):
            return W.tag("Out of Stock", "warn")
        return W.tag("OK", "ok")

    def _render_sweep(rep):
        """Swap the sweep card's body for the latest results — called ONLY when a sweep
        completes (or on a region rebuild from the stash), never per refresh, so the
        table's restylers are bounded by sweeps, not ticks."""
        card = ui.get("sweep_card")
        if card is None:
            return
        try:
            clear_layout(card.body)
        except RuntimeError:            # the card died with a region rebuild
            return
        flagged = [r for r in rep.get("rows", [])
                   if (not r.get("found")) or r.get("obsolete") or not r.get("in_stock")]
        if not flagged:
            card.body.addWidget(W.static_label(
                "Every sourced part is active and in stock.", "dim"))
            return
        cols = ["Symbol", "Part Number", "Lifecycle", "Stock", "Status",
                "Suggested Replacement"]
        rows = []
        for r in flagged:
            rows.append([str(r.get("symbol", "")), str(r.get("mpn", "")),
                         str(r.get("lifecycle") or "—"),
                         str(r.get("stock", 0)) if r.get("found") else "—",
                         _status_tag(r),
                         str(r.get("suggested_replacement") or "—")])
        card.body.addWidget(W.data_table(cols, rows, stretch_col=(0, 5),
                                         mono_cols={1}, dim_cols={2}, wrap=True))

    # ── detail: Structure findings + the Sourcing sweep (chrome once) ─────────────────
    _FINDING_SECTIONS = (("Dangling Links", "dangling"),
                         ("Missing Footprint", "missing_footprint"),
                         ("Missing 3D Model", "missing_model"),
                         ("No Manufacturer", "no_manufacturer"))

    def detail(snap, handle):
        body = QWidget()
        col = QVBoxLayout(body); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(14)
        col.addWidget(W.eyebrow("Structure"))
        findings = W.Card(pad=16)
        col.addWidget(findings)
        col.addWidget(W.eyebrow("Sourcing"))
        sweep = W.Card(pad=16)
        col.addWidget(sweep)
        ui["sweep_card"] = sweep
        if ui.get("sourcing"):
            _render_sweep(ui["sourcing"])   # a rebuild re-shows the last sweep
        else:
            sweep.body.addWidget(W.static_label(
                "No Sweep Yet. Run Sourcing Check to query Mouser for lifecycle, "
                "stock and suggested replacements.", "dim"))

        on_owned = getattr(getattr(ctx, "bus", None), "on_owned", None)
        if callable(on_owned):
            on_owned("library.changed", lambda *_a: host._refresh(), body)

        def _render_findings(rep):
            clear_layout(findings.body)
            any_gap = False
            for title, key in _FINDING_SECTIONS:
                names = rep.get(key) or []
                if not names:
                    continue
                if any_gap:
                    findings.body.addSpacing(6)
                any_gap = True
                head = QHBoxLayout(); head.setSpacing(8)
                head.addWidget(W.static_label(title, "sub"))
                head.addWidget(W.static_label(f"{len(names)}", "dim"))
                head.addStretch(1)
                findings.body.addLayout(head)
                for nm in names[:8]:
                    findings.body.addWidget(W.static_label(str(nm), "body"))
                if len(names) > 8:
                    findings.body.addWidget(W.static_label(f"+{len(names) - 8} more", "dim"))
            if not any_gap:
                findings.body.addWidget(W.static_label(
                    "Every part has its symbol, footprint and 3D model.", "dim"))

        def fill(s):
            if ui.pop("pending_changed", False):
                bus = getattr(ctx, "bus", None)
                if bus is not None:
                    bus.emit("library.changed")

            def job():
                return LM.library_health_report(cfg)

            def populate(rep, ok):
                if not ok or rep is None:
                    clear_layout(findings.body)
                    findings.body.addWidget(W.static_label(
                        "Health scan failed, see status.", "dim"))
                    return
                ui["health"] = rep
                _render_findings(rep)
                # The band reflects the SAME scan (single compute per refresh); the
                # recipe's own verdict pass reads the stash, so both stay consistent.
                try:
                    host._verdict.set(_mk_health_verdict(rep))
                except NameError:       # first build: host not bound yet — the
                    pass                # workbench's initial verdict pass covers it

            run_populate(ctx, job, populate)

        return body, fill

    # ── the ▶ Fix All From Library primary flow ────────────────────────────────────────
    def _fa_audit(snap):
        """OFF-thread: plan every safe structural completion the library can satisfy.
        Keys are ``<row-index>::<op-key>`` (two rows can share a NAME — an orphan
        footprint and the symbol it matches). A create_symbol op whose footprint is
        also the target of a link_footprint op is dropped: the link supersedes the
        stub, and applying both would leave a junk duplicate symbol."""
        c = snap.get("cfg")
        try:
            rows = LM.scan_library_grouped(c)
        except Exception as e:  # noqa: BLE001
            fix_flow.empty = f"Could not scan the library: {e}"
            return []
        planned, link_targets = [], set()
        for i, row in enumerate(rows):
            try:
                plan = LM.complete_part_plan(c, row)
            except Exception:  # noqa: BLE001 — one broken part must not kill the audit
                plan = []
            if plan:
                planned.append((i, row, plan))
                for op in plan:
                    if op.get("kind") == "link_footprint":
                        link_targets.add(str(op.get("value")))
        plans, ops_out = {}, []
        for i, row, plan in planned:
            kept = [op for op in plan
                    if not (op.get("kind") == "create_symbol"
                            and str(op.get("value")) in link_targets)]
            if not kept:
                continue
            plans[i] = (row, plan)
            for op in kept:
                ops_out.append({"key": f"{i}::{op['key']}",
                                "label": f"{row.get('name', '?')} · {op.get('label', '')}",
                                "detail": op.get("detail", ""),
                                "safe": bool(op.get("safe"))})
        ui["fixall"] = plans
        if not ops_out:
            fix_flow.empty = ("Every part is already structurally complete; the library "
                              "has nothing left to fill. (Identity blanks are filled by "
                              "Enrich Blanks From Distributor.)")
        return ops_out

    def _fa_intro(snap, ops):
        return ("Apply what the library itself can satisfy. Safe ops (name matches, "
                "stub symbols, blank fills) are pre-checked; everything is committed "
                "as one change:")

    def _fa_apply(snap, keys):
        c = snap.get("cfg")
        plans = ui.get("fixall") or {}
        applied, errors, touched = [], [], []
        for i, (row, plan) in plans.items():
            sel = [k.split("::", 1)[1] for k in keys if k.startswith(f"{i}::")]
            if not sel:
                continue
            res = LM.apply_complete_part(c, row, plan, sel, lsink)
            name = row.get("name", "?")
            applied += [f"{name}: {a}" for a in (res.get("applied") or [])]
            errors += [f"{name}: {e}" for e in (res.get("errors") or [])]
            if res.get("applied"):
                touched.append(name)
        if applied:
            LM.git_commit_push(c, lsink,
                               f"feat(lib): fix all from library "
                               f"({plural(len(touched), 'part')})")
            ui["pending_changed"] = True
        # Honest remainder: what each touched part STILL lacks after the fix.
        missing = []
        if touched:
            try:
                byname = {}
                for r in LM.scan_library_grouped(c):
                    byname.setdefault(r.get("name"), r)
                for nm in touched[:20]:
                    r = byname.get(nm)
                    for m in (LM.part_missing(r) if r else []):
                        missing.append({"item": f"{nm}: {m.get('item', '?')}",
                                        "why": m.get("why", ""),
                                        "how_to_fix": m.get("how_to_fix", "")})
            except Exception:  # noqa: BLE001 — the re-scan is advisory, never fatal
                pass
        return {"summary": f"Applied {plural(len(applied), 'fix', 'fixes')} across "
                           f"{plural(len(touched), 'part')}.",
                "done": applied, "errors": errors, "missing": missing[:40]}

    fix_flow = K.PrimaryFlow(
        label="▶ Fix All From Library", audit=_fa_audit, intro=_fa_intro, apply=_fa_apply,
        tip="Create stub symbols and link footprints and 3D models wherever the library "
            "already has the pieces, preview first, then commit once",
        empty="Every part is already structurally complete; nothing to fix.")

    # ── secondaries: the Mouser sweep + enrich ─────────────────────────────────────────
    def _run_sourcing():
        prov = LM.providers_from_config(cfg)
        ui["lookup_ok"] = prov is not None
        _apply_enablement()
        if not prov:
            _log("No Mouser key configured; add one in Settings to run the sourcing check.")
            return
        if busy["on"]:
            return
        busy["on"] = True

        def job():
            return LM.library_sourcing_report(cfg, prov, throttle=0.15)

        def done(rep, ok):
            busy["on"] = False
            if not ok or not rep:
                _log("Sourcing check failed, see status.")
                return
            ui["sourcing"] = rep
            _render_sweep(rep)
            c = rep.get("counts", {})
            _log(f"Sourcing: {c.get('on_mouser', 0)}/{c.get('parts', 0)} on Mouser · "
                 f"{c.get('obsolete_nrnd', 0)} NRND · {c.get('out_of_stock', 0)} out of stock.")
            bus = getattr(ctx, "bus", None)
            if bus is not None:
                bus.emit("library.sourcing_report", rep)   # Parts seeds its detail cache
            _apply_enablement()                            # the export is available now

        run_populate(ctx, job, done, busy="Checking sourcing on Mouser…")

    def _enrich_blanks():
        prov = LM.providers_from_config(cfg)
        ui["lookup_ok"] = prov is not None
        _apply_enablement()
        if not prov:
            _log("No Mouser key configured; add one in Settings to enrich parts.")
            return
        if busy["on"]:
            return
        busy["on"] = True

        def dried(res, ok):
            busy["on"] = False
            if not ok:
                _log("Enrich scan failed, see status.")
                return
            n = len((res or {}).get("changes", []))
            if not n:
                _log("Enrich: nothing to fill.")
                return
            from PyQt5.QtWidgets import QMessageBox
            ans = QMessageBox.question(
                host, "Apply Enrichment",
                f"{plural(n, 'blank field')} can be filled from the distributor. Apply?",
                QMessageBox.Yes | QMessageBox.No)
            if ans != QMessageBox.Yes:
                _log("Cancelled.")
                return
            busy["on"] = True

            def applied(r, ok2):
                busy["on"] = False
                if ok2:
                    ui["pending_changed"] = True
                    _log(f"Enrich: wrote {plural(len((r or {}).get('changes', [])), 'field')}.")
                else:
                    _log("Enrich failed, see status.")
                host._region.handle.refresh()

            run_populate(ctx, lambda: _enrich_from_mpn(ctx, prov, apply=True), applied,
                         busy="Applying enrichment…")

        run_populate(ctx, lambda: _enrich_from_mpn(ctx, prov, apply=False), dried,
                     busy="Scanning for fillable fields…")

    # ── exports ────────────────────────────────────────────────────────────────────────
    def _health_md(_snap):
        rep = ui.get("health") or LM.library_health_report(cfg)
        return str(rep.get("markdown", ""))

    def _sourcing_md(_snap):
        rep = ui.get("sourcing")
        if not rep:
            raise RuntimeError("run the sourcing check first")
        return str(rep.get("markdown", ""))

    exports = [
        K.export_action("Export Health Report", _health_md, "library_health.md",
                        filt="Markdown (*.md)",
                        tip="Save the structural health report as Markdown"),
        K.export_action("Export Sourcing Report", _sourcing_md, "library_sourcing.md",
                        filt="Markdown (*.md)", tip=_TIPS["Export Sourcing Report"]),
    ]

    secondary = [
        K.action("Run Sourcing Check", _run_sourcing, tip=_TIPS["Run Sourcing Check"]),
        K.action("Enrich Blanks From Distributor", _enrich_blanks,
                 tip=_TIPS["Enrich Blanks From Distributor"]),
    ]

    host = K.workbench(ctx, title="Sourcing Health", snapshot=snapshot,
                       verdict=lambda snap: _mk_health_verdict(ui.get("health")),
                       detail=detail, primary=fix_flow, secondary=secondary,
                       exports=exports, busy=busy)

    ui["buttons"] = [(b.text(), b) for b in host.findChildren(QPushButton)
                     if not b.text().startswith(("▸", "▾"))]

    _raw_run = host._run_primary

    def _run_primary():
        if busy["on"]:
            return
        _raw_run()

    host._run_primary = _run_primary

    # Test / drive seams.
    host._snapshot = snapshot
    host._sourcing_report = lambda: ui.get("sourcing")
    host._health_report = lambda: ui.get("health")
    host._fix_all_audit = _fa_audit
    host._run_sourcing = _run_sourcing
    host._enrich_blanks = _enrich_blanks
    host._exports = {ea.label: ea for ea in exports}
    host._btn = lambda text: next((b for t, b in ui.get("buttons", ()) if t == text), None)

    _apply_enablement()
    return host


class LibraryFeature(F.Feature):
    id = "library"
    title = "Library"
    order = 10
    category = "Library"

    def build(self, ctx: F.Context) -> QWidget:
        return W.Workspace(ctx, "Library", [
            ("Parts", lambda c: _parts_panel(c, None)),
            ("Sourcing Health", lambda c: W.scroll_body(_health_workbench(c))),
            ("Maintenance", lambda c: W.scroll_body(_maintenance_workbench(c))),
        ])


F.register(LibraryFeature())
