"""Drive-audit: exercise the styled UI the way a USER does — not the way a unit
test does — so crashes, stale selection-dependent content, and dead selectors are
caught automatically instead of shipping.

Why this exists: pytest + "the panel builds" stayed green while `python -m ui` crashed
(changing the Projects project segfaulted) and showed stale data (choosing a Bench
package didn't refresh the pin table). Those are INTERACTION bugs — only driving the
real widgets surfaces them. This harness builds each styled feature (and the NetdeckShell)
on a self-contained fixture (a library with parts in every health state + real projects +
the STM32 packages), then for every panel: changes each selector through ALL its values,
clicks the safe/read-only actions and each ▶ primary headlessly, and asserts (a) no
exception, (b) selection-dependent views actually refresh. (The legacy bare-window drivers
were removed with bare.py at the Phase-3 flip; the styled drivers below are the whole gate.)

A segfault kills this process (nonzero exit) — which is the point: run it as a subprocess
(see tests/test_drive_audit.py) and a crash becomes a red test, not a silent regression.

Run directly:   QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/drive_audit.py
Exit 0 = clean; nonzero = a regression to fix BEFORE claiming any UI work "done".
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# This runs as a SUBPROCESS whose stdout is a pipe (see tests/test_drive_audit.py). On
# Windows that pipe defaults to cp1252, so the progress prints below (which carry → and ▶)
# would raise UnicodeEncodeError and crash the run with exit 1 — a false "drive-audit
# FAILED" that has nothing to do with the UI. Force UTF-8 so the glyphs survive on any OS.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — very old/detached stream: fall back silently
        pass

from PyQt5.QtWidgets import (QApplication, QComboBox, QPushButton,  # noqa: E402
                             QLabel)
from PyQt5.QtCore import QCoreApplication                                     # noqa: E402

import LibraryManager as LM                                                   # noqa: E402

# Keep a live QApplication for the whole process (a stray one gets GC'd → "Must
# construct a QApplication before a QWidget" abort when a feature builds widgets).
_APP = QApplication.instance() or QApplication([])

_FAILURES: list[str] = []


def _fail(where: str, exc: BaseException | None = None):
    msg = f"[FAIL] {where}"
    if exc is not None:
        msg += f": {exc}\n{traceback.format_exc()[-600:]}"
    _FAILURES.append(msg)
    print(msg, flush=True)


def _pump():
    # let deferred work (QTimer.singleShot rebuilds, deleteLater) run
    for _ in range(4):
        QCoreApplication.processEvents()


def _make_fixture() -> dict:
    """A self-contained library (parts in every state) + a repo with real projects."""
    root = Path(tempfile.mkdtemp(prefix="drive_audit_"))
    libs = root / "libs"
    fp = libs / "MyFootprints.pretty"
    md = libs / "My3DModels"
    fp.mkdir(parents=True)
    md.mkdir(parents=True)
    # complete part, missing-model part, dangling-footprint part, footprint-only orphan
    (libs / "MySymbols.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20211014) (generator "t")\n'
        '  (symbol "U1" (property "Value" "U1")(property "Footprint" "MyFootprints:FP_A")'
        '(property "MANUFACTURER" "Acme")(property "Datasheet" "http://d")(pin 1))\n'
        '  (symbol "R1" (property "Value" "R1")(property "Footprint" "MyFootprints:FP_B")(pin 1))\n'
        '  (symbol "U2" (property "Value" "U2")(property "Footprint" "MyFootprints:GONE")(pin 1))\n'
        ')\n', encoding="utf-8", newline="\n")
    # FP_A points at a VALID VRML model — .wrl renders headlessly (pure-Python + numpy),
    # so the parts-picker row THUMBNAIL is exercised for real in the offscreen drive
    # (STEP is skipped natively headless and would only prove the placeholder path).
    (fp / "FP_A.kicad_mod").write_text(
        '(footprint "FP_A" (layer "F.Cu")\n  (model "${MY3DMODELS}/m.wrl"\n'
        '    (offset (xyz 0 0 0))\n  )\n)\n', encoding="utf-8", newline="\n")
    (fp / "FP_B.kicad_mod").write_text('(footprint "FP_B" (layer "F.Cu"))\n', encoding="utf-8")
    (fp / "ORPH.kicad_mod").write_text('(footprint "ORPH" (layer "F.Cu"))\n', encoding="utf-8")
    (md / "m.wrl").write_text(
        "#VRML V2.0 utf8\nShape {\n  geometry IndexedFaceSet {\n"
        "    coord Coordinate { point [ 0 0 0, 1 0 0, 1 1 0, 0 1 0, 0 0 1, 1 0 1, 1 1 1, 0 1 1 ] }\n"
        "    coordIndex [ 0 1 2 3 -1, 4 5 6 7 -1, 0 1 5 4 -1, 2 3 7 6 -1, 1 2 6 5 -1, 0 3 7 4 -1 ]\n"
        "  }\n}\n", encoding="utf-8", newline="\n")
    # two VALID .kicad_pro projects so the Projects selector has something to switch between
    proj_json = '{"board":{"design_settings":{}},"meta":{"version":1},"net_settings":{}}'
    for name in ("Alpha", "Beta"):
        d = root / name
        d.mkdir()
        (d / f"{name}.kicad_pro").write_text(proj_json, encoding="utf-8")
    # a third project WITH a schematic, in mixed health (an unannotated ref → error,
    # a blank-footprint ref → warning) so Audit Project produces real findings the
    # detail card must render — drives the Projects workbench rebuild for real.
    g = root / "Gamma"
    g.mkdir()
    (g / "Gamma.kicad_pro").write_text(proj_json, encoding="utf-8")
    (g / "Gamma.kicad_sch").write_text(
        '(kicad_sch (version 20230121) (generator eeschema)\n'
        '  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1)\n'
        '    (property "Reference" "R1" (at 100 95 0))\n'
        '    (property "Value" "10k" (at 100 105 0))\n'
        '    (property "Footprint" "" (at 100 100 0))\n  )\n'
        '  (symbol (lib_id "Device:C") (at 120 100 0) (unit 1)\n'
        '    (property "Reference" "C?" (at 120 95 0))\n'
        '    (property "Value" "100nF" (at 120 105 0))\n'
        '    (property "Footprint" "" (at 120 100 0))\n  )\n'
        ')\n', encoding="utf-8", newline="\n")
    # a project that INSTANTIATES a shared-library symbol (R1) via a lib_id, so the
    # rename heads-up (projects_referencing_symbol) has a real reference to surface.
    delta = root / "Delta"
    delta.mkdir()
    (delta / "Delta.kicad_pro").write_text(proj_json, encoding="utf-8")
    (delta / "Delta.kicad_sch").write_text(
        '(kicad_sch (version 20230121) (generator eeschema)\n'
        '  (symbol (lib_id "MySymbols:R1") (at 100 100 0) (unit 1)\n'
        '    (property "Reference" "R1" (at 100 95 0)))\n)\n',
        encoding="utf-8", newline="\n")
    return {"Libs": str(libs), "SymbolLib": str(libs / "MySymbols.kicad_sym"),
            "FootprintLib": str(fp), "ModelLib": str(md), "RepoRoot": str(root)}


def audit_git_workbench():
    """Drive the STYLED Git feature (the kit.workbench convergence pilot) end-to-end on a
    real temp git repo: build it, DRIVE the ▶ Commit & Sync primary headlessly via the
    host._run_primary seam (audit → auto-approve safe keys → apply → report — the modals
    are _headless()-guarded so nothing blocks), then click the read-only report secondaries.
    A crash here (or a ▶ that doesn't clean the tree) is a real regression, caught as a
    nonzero exit. Skipped when git is not on PATH."""
    import subprocess
    import nd_git
    if not nd_git.have_git():
        print("  git-workbench: skipped (git not on PATH)", flush=True)
        return
    from types import SimpleNamespace
    from ui.features import git as G
    d = Path(tempfile.mkdtemp(prefix="drive_git_"))
    nd_git.init_repo(d)
    for k, v in (("user.email", "t@e.com"), ("user.name", "T")):
        subprocess.run(["git", "-C", str(d), "config", k, v], check=False,
                       capture_output=True, text=True, encoding="utf-8")
    (d / "seed.txt").write_text("seed\n", encoding="utf-8")
    nd_git.commit(d, "seed", paths="seed.txt")

    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    ctx = SimpleNamespace(cfg={"RepoRoot": str(d)}, services=_Svc(), theme=None,
                          bus=SimpleNamespace(emit=lambda *a, **k: None,
                                              on_owned=lambda *a, **k: None))
    try:
        host = G._git_workbench(ctx)
    except Exception as e:  # noqa: BLE001
        _fail("Git workbench build", e)
        return
    # DRIVE the ▶ primary end-to-end: a change + a message, then the seam.
    (d / "change.txt").write_text("c\n", encoding="utf-8")
    host._msg.setText("drive-audit commit")
    try:
        host._run_primary()
        _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Git workbench ▶ Commit & Sync", e)
        return
    if nd_git.status(d).get("clean") is not True:
        _fail("Git workbench: ▶ Commit & Sync did not clean the working tree")
    if not any("Committed" in m for m in ctx.services.logs):
        _fail("Git workbench: ▶ Commit & Sync produced no commit report")
    # Drive the read-only + safe secondaries (their report dialogs are headless-safe).
    for fn, label in ((host._status_report, "Status Report"),
                      (host._recent_commits, "Recent Commits"),
                      (host._integrity_scan, "Integrity Scan"),
                      (lambda: host._show_file("seed.txt"), "Show File @ HEAD"),
                      (host._stage_all, "Stage All"),
                      (host._pull, "Pull"),
                      (host._sync_remote, "Sync With Remote")):
        try:
            fn(); _pump()
        except Exception as e:  # noqa: BLE001
            _fail(f"Git workbench {label}", e)
    print("  git workbench driven (▶ commit&sync end-to-end + secondaries, no crash)", flush=True)


def _styled_ctx(cfg):
    """A minimal shell-like ctx for driving a styled feature headlessly: a logging
    services stub (synchronous run_async) and a bus with on / on_owned / emit."""
    from types import SimpleNamespace

    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    class _Bus:
        def __init__(self): self.subs = {}
        def emit(self, name, *a):
            for cb in list(self.subs.get(name, [])):
                cb(*a)
        def on(self, name, cb): self.subs.setdefault(name, []).append(cb)
        def on_owned(self, name, cb, owner=None): self.subs.setdefault(name, []).append(cb)

    return SimpleNamespace(cfg=dict(cfg), services=_Svc(), theme=None, bus=_Bus())


def audit_library_workbench():
    """Drive the STYLED Library feature (library-v2 mockup) on a populated fixture:
    the 2-column picker | canvas splitter (select every part → the detail refreshes,
    previews + identity + sourcing all in one canvas; the per-part Manage actions
    engage via their explicit seams), the Sourcing Health workbench (verdict + ▶ Fix
    All From Library end-to-end), and the Maintenance workbench (verdict + a
    headless-safe report + the ▶ empty-state). A crash or a stale detail is a real
    regression, caught as a nonzero exit."""
    from PyQt5.QtWidgets import QSplitter, QLabel as _QLabel, QPushButton as _QPB
    from ui.features import library as LIB
    from ui.features import library_preview as P
    cfg = _make_fixture()
    ctx = _styled_ctx(cfg)

    # ── Parts: the 2-column picker | canvas splitter ─────────────────────────────────
    try:
        panel = LIB._parts_panel(ctx, None)
        _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library styled Parts build", e)
        return
    sp = panel.findChild(QSplitter)
    if sp is None or sp.count() != 2:
        _fail(f"Library Parts: expected a 2-pane splitter, got {sp.count() if sp else 0}")
    # M1: the picker's front drop zone exists, its drag-highlight hook is wired to the
    # panel, and toggling it does not crash (the whole-panel ZIP drop is _PartsRoot).
    df = getattr(panel, "dropfront", None)
    if df is None:
        _fail("Library Parts: picker drop zone missing")
    elif getattr(panel, "_drag_hint", None) != df.set_dragging:
        _fail("Library Parts: drop-zone drag-highlight hook not wired to the panel")
    else:
        try:
            df.set_dragging(True); _pump(); df.set_dragging(False); _pump()
            if df.property("dragging"):
                _fail("Library Parts: drop-zone still flagged dragging after reset")
        except Exception as e:  # noqa: BLE001
            _fail("Library Parts: drop-zone drag toggle", e)
    lst = panel.parts_list._list
    n = lst.count()
    if n == 0:
        _fail("Library Parts: list empty after scan")
    # M2: rows are grouped under sticky headers (once a grouping is on — the smart
    # default is None for a tiny fixture), and any incomplete/dangling part shows a
    # warning triangle. Assert both, and that Group By re-renders the list.
    pl = panel.parts_list
    pl.set_group_by("Category"); _pump()             # smart default is None on a tiny lib
    if not pl._headers:
        _fail("Library Parts: grouped list rendered no group headers")
    if not any(w.findChild(_QLabel, "partRowWarn") for _r, _it, w in pl._items):
        _fail("Library Parts: no incomplete row showed a warning triangle")
    try:
        base = len(pl._items)
        for mode in ("Completion", "Manufacturer", "None", "Category"):
            pl.set_group_by(mode); _pump()
            if len(pl._items) != base:
                _fail(f"Library Parts: Group By {mode} changed the row count ({len(pl._items)} != {base})")
            if mode == "None" and pl._headers:
                _fail("Library Parts: Group By None still rendered headers")
            if mode == "Category" and not pl._headers:
                _fail("Library Parts: Group By Category rendered no headers")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Group By re-render", e)
    # 3D-model row thumbnail: every row carries a fixed-size right-aligned thumbnail slot
    # (layout stability), building the list with a modeled part does NOT hang (the render
    # is cached + off-thread), and the modeled part's row actually paints a pixmap once
    # its thumbnail lands. The fixture's U1 → FP_A → m.wrl is headless-renderable.
    try:
        pl.set_group_by("None"); _pump()             # flat list, no header offset
        slots = [getattr(w, "_thumb", None) for _r, _it, w in pl._items]
        if any(s is None for s in slots):
            _fail("Library Parts: a row is missing its 3D-model thumbnail slot")
        elif any(s.width() != P._THUMB_SLOT for s in slots if s is not None):
            _fail("Library Parts: a row thumbnail slot is not the fixed thumbnail size")
        else:
            pl._queue_thumbnails(); _pump()          # kick the viewport-lazy loader, drain
            modeled = None
            for r, _it, w in pl._items:
                if pl._row_model_path(r):
                    modeled = w; break
            if modeled is None:
                _fail("Library Parts: no row resolved a 3D model to thumbnail")
            else:
                pm = modeled._thumb.pixmap()
                if pm is None or pm.isNull():
                    _fail("Library Parts: the modeled row never painted a 3D thumbnail")
        pl.set_group_by("Category"); _pump()         # restore the grouping the rest expects
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: 3D-model row thumbnail", e)
    # M3: the ALWAYS-VISIBLE inline finder bar (no hidden pop): its Show checkboxes
    # filter the list and its Group By radios re-group it (the real UI wiring).
    try:
        if not pl._filter_bar.isVisibleTo(pl):
            _fail("Library Parts: inline finder bar is not visible")
        vis0 = pl.visible_count()
        pl._show_boxes["Not Orderable"].setChecked(False); _pump()
        if pl.visible_count() >= vis0:
            _fail("Library Parts: unchecking a Show class did not hide any rows")
        pl._show_boxes["Not Orderable"].setChecked(True); _pump()
        if pl.visible_count() != vis0:
            _fail("Library Parts: re-checking the Show class did not restore the rows")
        pl._group_radios["Manufacturer"].setChecked(True); _pump()  # deferred set_group_by
        if pl.group_by() != "Manufacturer":
            _fail("Library Parts: Group By radio did not switch the grouping")
        pl._group_radios["Category"].setChecked(True); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: inline finder bar Show/Group-By wiring", e)
    # M3b: a query narrows the list AND highlights the match in the row, and the footer
    # reflects the visible count.
    try:
        a_name = next((r.get("name") for r in pl._rows if r.get("name")), None)
        if a_name:
            frag = str(a_name)[:2].lower()
            pl.filter(frag); _pump()
            want = sum(1 for r in pl._rows if frag in str(r.get("name") or "").lower()
                       or frag in str(r.get("mpn") or "").lower())
            if pl.visible_count() != want:
                _fail(f"Library Parts: query '{frag}' visible {pl.visible_count()} != {want}")
            if not any("<span" in w._prim.text().lower()
                       for _r, it, w in pl._items if not it.isHidden()):
                _fail("Library Parts: query did not highlight any visible row")
            if str(pl.visible_count()) not in pl._footer.text():
                _fail("Library Parts: footer did not reflect the visible count")
            pl.filter(""); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: search highlight + footer", e)
    # Duplicates-only: seed a footprint dup group + toggle the checkbox; the list must
    # narrow to the duplicated parts, the row badges appear, and un-toggling restores it.
    try:
        vis_all = pl.visible_count()
        fps = [r.get("footprint") for r in pl._rows if r.get("footprint")]
        dup_fps = set(fps[:2]) if len(fps) >= 2 else set(fps)
        if dup_fps:
            pl.set_duplicate_footprints(dup_fps); _pump()
            if not any(not w._dup_badge.isHidden() for _r, _it, w in pl._items):
                _fail("Library Parts: seeded footprint dup did not surface a row badge")
        pl._dupes_box.setChecked(True); _pump()
        if pl.visible_count() > vis_all:
            _fail("Library Parts: Duplicates-only widened the list")
        pl._dupes_box.setChecked(False); _pump()
        if pl.visible_count() != vis_all:
            _fail("Library Parts: un-toggling Duplicates-only did not restore the list")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Duplicates-only filter", e)
    # M3c: multi-select two duplicates → Manage Duplicates builds the modal; drive its
    # bulk-delete CONFIRM path (cancel, so the fixture is untouched), then click a row's
    # Duplicate badge to open the one-part resolve flow (also headless-guarded).
    try:
        dup_items = [(r, it) for r, it, w in pl._items if not w._dup_badge.isHidden()]
        if len(dup_items) >= 2:
            for _r, it in dup_items[:2]:
                it.setSelected(True)
            _pump()
            if len(pl.selected_duplicate_rows()) < 2:
                _fail("Library Parts: two duplicate rows did not enter the selection")
            dlg = panel.manage_duplicates()
            if dlg is None:
                _fail("Library Parts: Manage Duplicates did not open on 2 selected dupes")
            else:
                cancelled = {"n": 0}
                dlg._delete_checked(confirm=lambda t: cancelled.__setitem__("n", len(t)) or False)
                if cancelled["n"] < 1:
                    _fail("Library Parts: Manage Duplicates confirm path saw no targets")
                dlg.reject()
            # Badge click → single-part resolve flow (guarded exec returns the dialog).
            rdlg = pl._resolve_dup(dup_items[0][0])
            if rdlg is None or not rdlg._checks:
                _fail("Library Parts: Duplicate badge resolve flow built no cards")
            rdlg.reject()
            pl._list.clearSelection(); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Manage Duplicates / badge resolve", e)
    # M3d: Export Visible writes the current view to a temp file with the right header.
    try:
        import tempfile as _tf
        out = os.path.join(_tf.mkdtemp(), "visible.csv")
        panel.export_visible(out)
        _pump()
        txt = Path(out).read_text(encoding="utf-8")
        if not txt.startswith("name,mpn,manufacturer,category,completion,model"):
            _fail("Library Parts: Export Visible header wrong")
        if len(txt.strip().splitlines()) < 2:
            _fail("Library Parts: Export Visible wrote no rows")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Export Visible", e)
    for i in range(n):
        try:
            lst.setCurrentRow(i); _pump()
            row = panel.detail._current
            # a part WITH a symbol must render editable identity field rows; an orphan
            # renders its Create/Reuse affordance instead — either way, not a blank pane.
            has_rows = any(l.text().endswith(":") for l in panel.detail.findChildren(_QLabel))
            has_btn = bool(panel.detail.findChildren(_QPB))
            if row is not None and not (has_rows or has_btn):
                _fail(f"Library Parts part[{i}]: detail rendered nothing")
        except Exception as e:  # noqa: BLE001
            _fail(f"Library Parts part[{i}]", e)

    # Drive the per-part actions via their explicit seams (the parity-closing set).
    rows = {r.get("name"): r for r in LM.scan_library_grouped(ctx.cfg)}
    d = panel.detail
    # M4: the header carries a ⋯ kebab (Reveal Files + delete family) and the still-needs
    # line reflects each part's completion (broken-link / Complete / Missing + pills).
    try:
        an_incomplete = next((r for r in rows.values()
                              if not LM.part_completion(r)["is_complete"]), None)
        if an_incomplete is not None:
            d.show(an_incomplete); _pump()
            if d._needs.count() == 0:
                _fail("Library Parts: still-needs line empty on an incomplete part")
            entries = {a.text() for a in d._kebab_menu.actions() if a.text()}
            if "Reveal Files" not in entries:
                _fail("Library Parts: ⋯ kebab missing Reveal Files")
            if not d._title_warn.isVisibleTo(d):
                _fail("Library Parts: incomplete part header missing its warn glyph")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: header kebab / still-needs", e)
    # M5: Files big-3D-left relayout + Expand→lightbox. A part with a linked model
    # mounts an interactive MeshView in the 3D card, marks it Linked, and its Expand
    # opens a lightbox without crashing (the exact use-after-free class the gates catch).
    try:
        with_model = next((r for r in rows.values() if r.get("model")), None) \
            or next((r for r in rows.values() if r.get("footprint")), None)
        if with_model is not None:
            d.show(with_model); _pump()
            mv = d._mdl.findChild(P.MeshView)
            if d._mdl._light[0] == "mesh":
                if mv is None:
                    _fail("Library Parts: 3D card did not mount a MeshView for a modeled part")
                dlg = d._mdl._open_lightbox(); _pump()
                if dlg is None:
                    _fail("Library Parts: Expand did not build a lightbox for the 3D model")
                dlg.deleteLater()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Files 3D card / lightbox", e)
    try:
        if "U1" in rows:
            d.show(rows["U1"]); _pump()
            d._rename_symbol(new_name="U1_DRIVE"); _pump()
            names = [LM.extract_symbol_name(b) for b in
                     LM.extract_symbol_blocks(Path(ctx.cfg["SymbolLib"]).read_text(encoding="utf-8"))]
            if "U1_DRIVE" not in names:
                _fail("Library Parts: Rename Symbol seam did not rename U1")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Rename Symbol", e)
    # Duplicate (kebab ▸ Duplicate…): copy a symbol-bearing part under a new name; the
    # source stays, the duplicate joins the library with its MPN reset.
    try:
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg) if r.get("symbols")), None)
        if editable is not None:
            d.show(editable); _pump()
            if "Duplicate…" not in {a.text() for a in d._kebab_menu.actions() if a.text()}:
                _fail("Library Parts: ⋯ kebab missing Duplicate on a symbol-bearing part")
            src = editable["symbols"][0]
            d._duplicate_part(row=editable, new_name="DRIVE_DUP"); _pump()
            names = [LM.extract_symbol_name(b) for b in
                     LM.extract_symbol_blocks(Path(ctx.cfg["SymbolLib"]).read_text(encoding="utf-8"))]
            if "DRIVE_DUP" not in names or src not in names:
                _fail("Library Parts: Duplicate did not add the copy while keeping the source")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Duplicate Part", e)
    try:
        orphan = next((r for r in LM.scan_library_grouped(ctx.cfg)
                       if r.get("footprint") and not r.get("symbols")), None)
        if orphan is not None:
            d.show(orphan); _pump()
            src = next((n for n in d._all_symbol_names()), None)
            if src:
                d._reuse_symbol_for_orphan(source=src); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Reuse Existing Symbol", e)
    try:
        rows2 = {r.get("name"): r for r in LM.scan_library_grouped(ctx.cfg)}
        victim = next((r for r in rows2.values() if r.get("footprint") and r.get("symbols")), None)
        if victim is not None:
            d.show(victim); _pump()
            d._delete_footprint(confirm=lambda refs: True); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Delete Footprint", e)

    # ── LIB-flash regression: an inline field edit must NOT commit+push per keystroke
    # (that per-field push storm flashed several windows on Windows during a component
    # update). It writes to disk + marks the detail unsaved; ONE explicit Save commits.
    try:
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg) if r.get("symbols")), None)
        if editable is not None:
            d.show(editable); _pump()
            real_push = LM.git_commit_push
            calls = {"n": 0}
            LM.git_commit_push = lambda c, log, msg: (calls.__setitem__("n", calls["n"] + 1) or True)
            try:
                d._commit_field("Manufacturer", "MANUFACTURER", "manufacturer", "DriveAuditCo"); _pump()
                if calls["n"] != 0:
                    _fail(f"Library Parts: inline edit pushed {calls['n']}× per field (the flash bug)")
                if not d._unsaved:
                    _fail("Library Parts: inline edit did not mark the detail unsaved")
                if not d._savebar.isVisibleTo(d):
                    _fail("Library Parts: Save bar hidden after an unsaved edit")
                d._save_changes(); _pump()
                if calls["n"] != 1:
                    _fail(f"Library Parts: Save committed {calls['n']}× (expected exactly 1)")
                if d._unsaved:
                    _fail("Library Parts: Save did not clear the unsaved state")
            finally:
                LM.git_commit_push = real_push
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: inline-edit Save batching", e)

    # M6: Component Fields open read-only; the Edit toggle (deferred rebuild — the
    # in-signal teardown class the gates catch) reveals click-to-edit line editors,
    # and toggling back returns to the read-only view. No crash on the swap.
    try:
        from PyQt5.QtWidgets import QLineEdit as _QLE
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg) if r.get("symbols")), None)
        if editable is not None:
            d.show(editable); _pump()
            if d._edit_mode:
                _fail("Library Parts: Component Fields did not open in the read-only view")
            view_editors = len(d.findChildren(_QLE))
            d._toggle_edit(); _pump()               # deferred QTimer rebuild fires on pump
            if not d._edit_mode:
                _fail("Library Parts: Edit toggle did not enter edit mode")
            if len(d.findChildren(_QLE)) <= view_editors:
                _fail("Library Parts: Edit mode revealed no editable fields")
            d._toggle_edit(); _pump()
            if d._edit_mode:
                _fail("Library Parts: Done toggle did not return to the read-only view")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Component Fields view/edit toggle", e)

    # M7: a part with live sourcing renders the 4 stat cards + the Price Breaks bar
    # graph; a no-MPN part shows the not-orderable empty state (no fabricated data).
    try:
        from PyQt5.QtWidgets import QFrame as _QF, QProgressBar as _QPBar
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg) if r.get("symbols")), None)
        if editable is not None:
            seeded = dict(editable); seeded["mpn"] = "DRIVE-SRC-1"
            d._src_cache["DRIVE-SRC-1"] = {
                "mpn": "DRIVE-SRC-1", "stock": 4200, "lifecycle": "Active",
                "lead_time": "12 Days", "unit_price": 1.23, "source": "Mouser",
                "price_breaks": [{"qty": 1, "price": 1.60}, {"qty": 100, "price": 1.08},
                                 {"qty": 1000, "price": 0.86}],
            }
            d.show(seeded); _pump()
            cards = [f for f in d.findChildren(_QF) if f.objectName() == "statcard"]
            if len(cards) != 4:
                _fail(f"Library Parts: sourcing showed {len(cards)} stat cards, expected 4")
            if not d.findChildren(_QPBar):
                _fail("Library Parts: sourcing showed no price-break bars for a multi-rung ladder")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: sourcing stat cards / price-break graph", e)

    # M7b: the completion tooltip is self-documenting — hovering the header warn glyph
    # (and the still-needs pills) shows the per-dimension ✓/✗ passport, and its text
    # must match the backend 'missing' set exactly (no drift between the two surfaces).
    try:
        an_incomplete = next((r for r in LM.scan_library_grouped(ctx.cfg)
                              if not LM.part_completion(r)["is_complete"]
                              and not LM.part_completion(r)["dangling"]), None)
        if an_incomplete is not None:
            d.show(an_incomplete); _pump()
            comp = LM.part_completion(an_incomplete)
            tip = d._title_warn.toolTip()
            for m in comp["missing"]:                 # every missing dim on a ✗ line
                line = next((l for l in tip.splitlines() if l.endswith(m)), "")
                if not line.startswith(LM.COMPLETION_CROSS):
                    _fail(f"Library Parts: completion tooltip missing ✗ for '{m}'")
            if f"{comp['score']} of {comp['total']}" not in tip:
                _fail("Library Parts: completion tooltip lacks the N-of-8 summary")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: completion passport tooltip", e)

    # M7c: the three honest sourcing branches. Force each and assert the right panel:
    #   (a) no provider  -> "No Sourcing Provider" + an Open Settings CTA
    #   (b) uncached      -> "Not Looked Up Yet" (provider present, nothing cached)
    #   (c) cached/stale  -> a Mouser refresh that is DISABLED under the 4h shared-cap
    try:
        from PyQt5.QtWidgets import QPushButton as _QPB
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg) if r.get("symbols")), None)
        if editable is not None:
            part = dict(editable); part["mpn"] = "SRC-BRANCH-1"
            # (a) no provider: temporarily blank the lookup chain
            real_lookup = d._lookup
            d._lookup = None
            d.show(part); _pump()
            labels_a = {lab.text() for lab in d.findChildren(QLabel)}
            if "No Sourcing Provider" not in labels_a:
                _fail("Library Parts: no-provider sourcing did not show the No Sourcing Provider state")
            if not any(b.text() == "Open Settings" for b in d.findChildren(_QPB)):
                _fail("Library Parts: no-provider sourcing lacked the Open Settings CTA")
            # (b) provider present, nothing cached
            d._lookup = real_lookup
            d.show(part); _pump()
            labels_b = {lab.text() for lab in d.findChildren(QLabel)}
            if "Not Looked Up Yet" not in labels_b:
                _fail("Library Parts: uncached sourcing did not show Not Looked Up Yet")
            # (c) a fresh (<4h) Mouser snapshot -> Refresh gated by the shared-cap policy
            LM.save_sourcing_snapshot(ctx.cfg, "SRC-BRANCH-1",
                                      {"unit_price": 1.0, "stock": 5, "source": "Mouser"})
            d.show(part); _pump()
            menus = [b for b in d.findChildren(_QPB) if getattr(b, "_menu", None)]
            refresh_acts = [a for b in menus for a in b._menu.actions()
                            if a.text() == "Refresh This Part's Data"]
            if not refresh_acts:
                _fail("Library Parts: cached sourcing header missing the Mouser refresh action")
            elif any(a.isEnabled() for a in refresh_acts):
                _fail("Library Parts: Mouser Refresh enabled on a <4h snapshot (shared-cap gate)")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: three sourcing branches / refresh policy", e)

    # M7d: the datasheet Find button (view mode, part with an MPN + provider) fetches a
    # datasheet link and writes it through the field seam. Drive with a stubbed lookup.
    try:
        editable = next((r for r in LM.scan_library_grouped(ctx.cfg)
                         if r.get("symbols")), None)
        if editable is not None:
            part = dict(editable); part["mpn"] = "DS-FIND-1"; part["datasheet"] = ""
            real_lookup = d._lookup
            d._lookup = lambda n: {"mpn": "DS-FIND-1", "datasheet": "http://ds/find.pdf",
                                   "source": "Mouser"}
            captured = {"val": None}
            real_commit = d._commit_field
            d._commit_field = lambda label, prop, rk, v: captured.__setitem__("val", v)
            try:
                d.show(part); _pump()
                d._find_datasheet(part); _pump()
                if captured["val"] != "http://ds/find.pdf":
                    _fail("Library Parts: datasheet Find did not write the fetched URL to the field")
                # Race guard (adversarial-review fix): if the user navigates to a different
                # part while the async lookup is in flight, the fetched URL must NOT be
                # written onto the now-current unrelated part.
                captured["val"] = None
                other = dict(part); other["mpn"] = "DS-OTHER"
                d._lookup = lambda n: (d.__setattr__("_current", other),
                                       {"mpn": "DS-FIND-1", "datasheet": "http://ds/stale.pdf"})[1]
                d._find_datasheet(part); _pump()
                if captured["val"] is not None:
                    _fail("Library Parts: datasheet Find wrote to a part the user navigated away from")
            finally:
                d._commit_field = real_commit
                d._lookup = real_lookup
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: datasheet Find", e)

    # M7e: the rename heads-up surfaces the projects that reference the symbol to the
    # confirm seam (Delta references R1 via a lib_id). Returning False aborts the rename,
    # so the library file is untouched while the project-lookup + confirm path is driven.
    try:
        rrow = next((r for r in LM.scan_library_grouped(ctx.cfg)
                     if (r.get("symbols") or [None])[0] == "R1"), None)
        if rrow is not None:
            d.show(rrow); _pump()
            seen = {"projs": None}
            d._rename_symbol(new_name="R1_HEADSUP",
                             confirm=lambda projs: (seen.__setitem__("projs", list(projs)), False)[1])
            _pump()
            if not seen["projs"] or "Delta" not in seen["projs"]:
                _fail(f"Library Parts: rename heads-up did not surface referencing projects "
                      f"(saw {seen['projs']})")
            names_now = [LM.extract_symbol_name(b) for b in
                         LM.extract_symbol_blocks(Path(ctx.cfg["SymbolLib"]).read_text(encoding="utf-8"))]
            if "R1_HEADSUP" in names_now:
                _fail("Library Parts: rename proceeded despite the confirm seam returning False")
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: rename heads-up (projects referencing symbol)", e)

    # M8: the Library Tools modal (5 curated real ops) and the Dedup review dialog
    # (per-group keep/delete cards + a live counter). Construct headless (no exec) and
    # drive the counter; the picker exposes the Library Tools entry point.
    try:
        from ui.features.library_preview import LibraryToolsDialog, DedupReviewDialog
        if not callable(getattr(panel, "open_library_tools", None)):
            _fail("Library Parts: picker missing the Library Tools entry point")
        lt = LibraryToolsDialog(ctx)
        from PyQt5.QtWidgets import QFrame as _QF2
        oprows = [f for f in lt.findChildren(_QF2) if f.objectName() == "toolrow"]
        if len(oprows) != 5:
            _fail(f"Library Tools: {len(oprows)} op rows, expected 5")
        groups = LM.find_duplicate_footprints(ctx.cfg) or [["FP_A", "FP_B"]]
        dd = DedupReviewDialog(ctx, groups)
        if not dd._checks:
            _fail("Dedup review: no footprint rows rendered")
        before = dd._counter.text()
        cb = next(iter(dd._checks.values())); cb.setChecked(not cb.isChecked()); _pump()
        if dd._counter.text() == before:
            _fail("Dedup review: live counter did not update on a toggle")
        lt.deleteLater(); dd.deleteLater()
    except Exception as e:  # noqa: BLE001
        _fail("Library Parts: Library Tools / Dedup modals", e)

    print("  library styled Parts driven (2-column, inline finder bar + search highlight + "
          "footer, dup badges, Manage Duplicates modal, Export Visible; per-part actions: "
          "rename/reuse/delete; inline-edit batches behind Save — no per-field push)",
          flush=True)

    # ── Sourcing Health workbench: verdict + ▶ Fix All From Library ──────────────────
    try:
        health = LIB._health_workbench(_styled_ctx(cfg))
        _pump()
        if health._verdict.isHidden():
            _fail("Library Health: verdict band hidden with incomplete parts present")
        health._run_primary(); _pump()          # ▶ Fix All From Library, end-to-end
    except Exception as e:  # noqa: BLE001
        _fail("Library Health workbench", e)
    else:
        print("  library health workbench driven (verdict + ▶ Fix All From Library)", flush=True)

    # ── Maintenance workbench: report secondary + ▶ empty-state ──────────────────────
    try:
        maint = LIB._maintenance_workbench(_styled_ctx(cfg))
        _pump()
        maint._scan_corrupt(); _pump()          # headless-safe report
        maint._run_primary(); _pump()           # no waiting ZIPs → distinct empty message
    except Exception as e:  # noqa: BLE001
        _fail("Library Maintenance workbench", e)
    else:
        print("  library maintenance workbench driven (scan report + ▶ empty-state)", flush=True)


def audit_projects_styled():
    """Drive the STYLED Projects feature (the kit.workbench/editor rebuild) — the piece the old
    audit_projects() never touched (it drives BARE). Two crash classes:

    (1) the PROJECT-SWITCH path: build the whole 5-tab ProjectsFeature and cycle the shared
        project combo through every value — each pick fires state.on_change -> ws.rebuild_all,
        which deleteLater's and rebuilds EVERY sub-panel. This is the exact rebuild-in-signal
        class that motivated this harness; a stale-closure or use-after-free surfaces here.
    (2) each tab's ▶ primary: build every panel directly on the schematic-bearing fixture
        project (Gamma) and drive its primary/seams headlessly (all recipe modals are
        _headless()-guarded), asserting no crash and that the cache/verdict seams populate."""
    from ui.features import projects as PROJ
    cfg = _make_fixture()

    # (1) project-switch / rebuild_all — drive state.select_index (what the header combo calls),
    # via the ProjectsFeature.state seam, so we hit the exact rebuild-in-signal path.
    try:
        feature = PROJ.ProjectsFeature()
        feature.build(_styled_ctx(cfg))
        _pump()
        # Cap the sweep: ProjectsState also discovers under RepoRoot.parent, which under a
        # mkdtemp fixture is a shared temp dir polluted with sibling fixtures — a handful of
        # switches exercises rebuild_all's teardown just as well (mirrors the bare "6 switches").
        for i in range(min(6, len(feature.state.projects))):
            feature.state.select_index(i); _pump()
        if feature.state.projects:
            feature.state.select_index(0); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Projects styled project-switch (rebuild_all)", e); return

    # (2) drive each tab's primary on Gamma (the project WITH a schematic)
    state = PROJ.ProjectsState(cfg)
    state.set_project("Gamma")
    if not state.project or state.project.name != "Gamma":
        _fail("Projects styled: fixture project 'Gamma' not discovered"); return

    def _overview(p):
        if not getattr(p, "_verdict", None):
            _fail("Projects/Overview: no verdict band"); return
        p._readiness(p._snapshot())                       # pure rollup; _run_check shells out — skip

    def _health(p):
        # M0: the verdict now reports schematic-scoped completion ("N/M components fully
        # filled") alongside the health findings.
        sub = getattr(getattr(p, "_verdict", None), "_sub", None)
        if sub is not None and "fully filled" not in sub.text():
            _fail(f"Projects/Health: verdict missing the completion line, got {sub.text()!r}")
        # Seed the SHARED ERC cache so the Prepare write below has something to invalidate
        # (Overview's readiness reads this very dict). Gamma has no kicad-cli, so a fix can't
        # be re-run — the stale entry must be dropped.
        state.set_check("erc", {"errors": 2, "warnings": 0})
        p._run_primary(); _pump()                         # ▶ Prepare Components (headless auto-accepts safe)
        if getattr(p, "_prep", None) is None:
            _fail("Projects/Health: ▶ Prepare Components left no _prep holder")
        # (health-overview) The Prepare write must clear the STALE ERC cache — dropped to
        # None when kicad-cli is absent, or replaced by a fresh re-run when it is present.
        # Either way the stale {"errors": 2} we seeded must be gone.
        if state.checks()["erc"] == {"errors": 2, "warnings": 0}:
            _fail("Projects/Health: Prepare left the stale ERC cache in place")
        # (health-overview) A write happened (Gamma's C? annotates), so Restore is armed.
        if p._last_prepare.get("state") == "prepared":
            # Breakdown chips: refresh the detail, then click a bucket and assert the filter
            # takes (and toggles back). Gamma keeps No-Footprint findings after annotate.
            p._region.handle.refresh(); _pump()
            fstate = p._findings_filter
            if fstate and fstate.get("buckets"):
                first = fstate["buckets"][0]["label"]
                p._apply_findings_filter(first); _pump()
                if fstate["bucket"] != first:
                    _fail(f"Projects/Health: breakdown chip {first!r} did not filter the table")
                p._apply_findings_filter(first); _pump()   # re-click clears
                if fstate["bucket"] is not None:
                    _fail("Projects/Health: re-clicking the breakdown chip did not clear the filter")
            # The before/after itemization is available for export after a Prepare.
            md = p._prepare_diff_markdown(p._snapshot())
            if "Prepare Diff" not in md:
                _fail("Projects/Health: Prepare diff markdown not produced after a write")
            # Restore -> Undo Restore symmetry on the REAL Gamma sheet (state must round-trip).
            sch = Path(state.root_schematic())
            p._restore_prepare(); _pump()
            if p._last_prepare.get("state") != "restored" or "C?" not in sch.read_text(encoding="utf-8"):
                _fail("Projects/Health: Restore did not roll the sheet back to pre-Prepare")
            p._undo_restore(); _pump()
            if p._last_prepare.get("state") != "prepared" or "C?" in sch.read_text(encoding="utf-8"):
                _fail("Projects/Health: Undo Restore did not re-apply the prepared sheet")
        # (health-overview) FillPreview triage: a reference-prefix filter scopes Select All.
        try:
            def _fitem(ref):
                return {"ref": ref, "sheet": "s.kicad_sch",
                        "match": {"confidence": "exact", "lib_part": {"name": ref}},
                        "changes": [{"prop": "MPN", "old": "", "new": f"M-{ref}", "kind": "fill",
                                     "source": "library"}]}
            fplan = {"items": [_fitem("C1"), _fitem("C2"), _fitem("J1")], "summary": {}}
            fcomps = [{"ref": r, "value": "x", "footprint": "L:F", "props": {"Reference": r}}
                      for r in ("C1", "C2", "J1")]
            fdlg = PROJ.FillPreviewDialog(fplan, 0, cfg={}, components=fcomps,
                                          sheet_of={c["ref"]: "s.kicad_sch" for c in fcomps})
            fdlg._filter_edit.setText("C"); _pump()
            fdlg.select_all(); _pump()
            if fdlg.selected() != {("C1", "MPN"), ("C2", "MPN")}:
                _fail(f"Projects/Health: FillPreview prefix filter did not scope Select All, got {fdlg.selected()!r}")
            fdlg.deleteLater()
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Health: FillPreview triage filter", e)
        # (health-overview) Before->after ERC line: a deterministic temp project (no cli),
        # seed the cache, apply an annotate write, and assert the report carries the line.
        try:
            hd = Path(tempfile.mkdtemp(prefix="drive_prep_")) / "H"
            hd.mkdir()
            (hd / "H.kicad_pro").write_text('{"meta":{"version":1}}', encoding="utf-8", newline="\n")
            hsch = hd / "H.kicad_sch"
            hsch.write_text(
                '(kicad_sch (version 20230121) (generator eeschema)\n'
                '  (symbol (lib_id "Device:R") (at 10 10 0) (unit 1)\n'
                '    (property "Reference" "R?" (at 10 5 0))\n'
                '    (property "Value" "1k" (at 10 15 0)) )\n)\n',
                encoding="utf-8", newline="\n")
            hcfg = dict(cfg); hcfg["RepoRoot"] = str(hd)
            hstate = PROJ.ProjectsState(hcfg)
            hp = PROJ._health_panel(_styled_ctx(hcfg), hstate)
            hstate.set_check("erc", {"errors": 3, "warnings": 1})
            hsnap = hp._snapshot()
            hp._prepare_audit(hsnap)                       # builds the plan (annotate R?)
            rep = hp._prepare_apply(hsnap, ["\x00annotate"])
            line = " ".join(rep.get("done", []))
            # With cli present the line reads "ERC: 3 errors -> N errors"; without, "ERC was
            # 3 errors ... Re-run". Both name ERC and reflect the pre-Prepare count.
            if "ERC" not in line:
                _fail(f"Projects/Health: Prepare report missing the ERC before/after line, got {line!r}")
            if hstate.checks()["erc"] == {"errors": 3, "warnings": 1}:
                _fail("Projects/Health: temp Prepare left its stale ERC cache in place")
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Health: ERC before/after report line", e)
        # M3/M4 (owner: library-only, no free-text): the preview groups identical passives
        # and offers unmatched components a LIBRARY PICKER — SELECT an existing part or ADD
        # one — and NEVER an editable schematic field. Drive it on a real temp library so the
        # link path (lib_id + footprint + persisted 3D-model line) can be asserted on disk.
        try:
            import shutil
            from PyQt5.QtWidgets import QLineEdit as _QLE2
            libd = Path(tempfile.mkdtemp(prefix="drive_link_"))
            fpdir = libd / "MyFootprints.pretty"; fpdir.mkdir()
            mdir = libd / "My3DModels"; mdir.mkdir()
            symp = libd / "MySymbols.kicad_sym"
            # One real library part (a 10k resistor pointing at R_0402) so SELECT has a target.
            symp.write_text(
                '(kicad_symbol_lib (version 20211014) (generator "t")\n'
                '  (symbol "R_10k"\n'
                '    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))\n'
                '    (property "Value" "10k" (at 0 2 0) (effects (font (size 1.27 1.27))))\n'
                '    (property "Footprint" "MyFootprints:R_0402" (at 0 -2 0) (effects (font (size 1.27 1.27)) hide))\n'
                '  )\n)\n', encoding="utf-8", newline="\n")
            (fpdir / "R_0402.kicad_mod").write_text(
                '(footprint "R_0402" (layer "F.Cu")\n  (pad "1" smd rect (at 0 0))\n)\n',
                encoding="utf-8", newline="\n")
            # A footprint the ADD path can create a NEW symbol for, with a name-matching model.
            (fpdir / "USB_C_Recept.kicad_mod").write_text(
                '(footprint "USB_C_Recept" (layer "F.Cu")\n  (pad "1" smd rect (at 0 0))\n)\n',
                encoding="utf-8", newline="\n")
            (mdir / "USB_C_Recept.step").write_text("solid\n", encoding="utf-8", newline="\n")
            lcfg = {"SymbolLib": str(symp), "FootprintLib": str(fpdir), "ModelLib": str(mdir)}
            sch = libd / "s.kicad_sch"
            sch.write_text(
                '(kicad_sch (version 20230121) (generator eeschema)\n'
                '  (symbol (lib_id "Device:R") (at 10 10 0) (unit 1)\n'
                '    (property "Reference" "R1" (at 10 5 0))\n'
                '    (property "Value" "10k" (at 10 15 0))\n'
                '    (property "Footprint" "" (at 10 10 0)) )\n'
                '  (symbol (lib_id "Device:R") (at 20 10 0) (unit 1)\n'
                '    (property "Reference" "R2" (at 20 5 0))\n'
                '    (property "Value" "10k" (at 20 15 0))\n'
                '    (property "Footprint" "" (at 20 10 0)) )\n'
                '  (symbol (lib_id "Conn:X") (at 30 10 0) (unit 1)\n'
                '    (property "Reference" "J1" (at 30 5 0))\n'
                '    (property "Value" "USB-C" (at 30 15 0))\n'
                '    (property "Footprint" "" (at 30 10 0)) )\n)\n',
                encoding="utf-8", newline="\n")
            comps = [
                {"ref": "R1", "value": "10k", "footprint": "", "lib_id": "Device:R",
                 "props": {"Reference": "R1", "Value": "10k"}},
                {"ref": "R2", "value": "10k", "footprint": "", "lib_id": "Device:R",
                 "props": {"Reference": "R2", "Value": "10k"}},
                {"ref": "J1", "value": "USB-C", "footprint": "", "lib_id": "Conn:X",
                 "props": {"Reference": "J1", "Value": "USB-C"}},
            ]
            sof = {c["ref"]: str(sch) for c in comps}
            dlg = PROJ.FillPreviewDialog({"items": [], "summary": {}}, 0, cfg=lcfg,
                                         components=comps, sheet_of=sof)
            if not dlg._link_cards:
                _fail("Projects/Health: no library picker cards for the group/unmatched sections")
            # (req a) NOT ONE group/manual card may expose an editable QLineEdit for a
            # SCHEMATIC value. The only line-edits allowed are the combo's search box and the
            # Add-to-Library identity form (which fills the NEW library part, not the schematic).
            for refs, card in dlg._link_cards:
                for e in card._select_row.findChildren(_QLE2):
                    # the combo's own search line-edit is the sole permitted one in Select mode
                    if e is not card._combo.lineEdit():
                        _fail(f"Projects/Health: library picker {refs} exposed a raw schematic field")
            # Group card (R1/R2): SELECT the existing R_10k library part → fill-once link.
            grp_refs, grp_card = next(((r, c) for r, c in dlg._link_cards if set(r) >= {"R1", "R2"}),
                                      (None, None))
            if grp_card is None:
                _fail("Projects/Health: passive group picker not found")
            i = grp_card._combo.findData("R_10k")
            if i < 0:
                _fail("Projects/Health: library part R_10k missing from the picker")
            grp_card._combo.setCurrentIndex(i)
            # Manual card (J1): ADD a new library part for the USB_C_Recept footprint.
            j_refs, j_card = next(((r, c) for r, c in dlg._link_cards if r == ("J1",)), (None, None))
            if j_card is None:
                _fail("Projects/Health: unmatched J1 picker not found")
            j_card._pick_mode("add")
            fi = j_card._fp_combo.findData("USB_C_Recept")
            if fi < 0:
                _fail("Projects/Health: USB_C_Recept footprint missing from the Add pick-list")
            j_card._fp_combo.setCurrentIndex(fi)
            j_card._add_edits["MPN"].setText("USB4110-GF-A")
            dlg.apply(); _pump()
            links = dlg.library_links()
            if links.get("R1", {}).get("kind") != "link" or links.get("R2", {}).get("kind") != "link":
                _fail(f"Projects/Health: group SELECT did not fan a link to every ref, got {links!r}")
            if links.get("J1", {}).get("kind") != "add":
                _fail(f"Projects/Health: unmatched ADD not recorded, got {links!r}")
            # Now run the real link backends and assert the SCHEMATIC lib_id was rewritten and
            # the footprint carries a persisted (model …) line (req c/e).
            lr = PROJ.libfill.link_placed_component(lcfg, str(sch), "R1",
                                                    links["R1"]["lib_part"])
            if lr.get("lib_id") != "MySymbols:R_10k":
                _fail(f"Projects/Health: link did not target MySymbols:R_10k, got {lr!r}")
            txt = sch.read_text(encoding="utf-8")
            if '(lib_id "MySymbols:R_10k")' not in txt:
                _fail("Projects/Health: placed R1 lib_id was not rewritten on disk")
            if 'MyFootprints:R_0402' not in txt:
                _fail("Projects/Health: placed R1 footprint was not written on disk")
            add = PROJ.libfill.add_library_part(lcfg, "USB_C_Recept",
                                                identity={"MPN": "USB4110-GF-A"})
            if not add.get("name"):
                _fail(f"Projects/Health: ADD did not create a library symbol, got {add!r}")
            idx2 = PROJ.libfill.library_parts(lcfg)
            newp = next((p for p in idx2 if p.get("name") == add["name"]), None)
            lr2 = PROJ.libfill.link_placed_component(lcfg, str(sch), "J1", newp or add)
            txt2 = sch.read_text(encoding="utf-8")
            if f'(lib_id "MySymbols:{add["name"]}")' not in txt2:
                _fail("Projects/Health: ADD path did not rewrite J1 lib_id on disk")
            fpmod = (fpdir / "USB_C_Recept.kicad_mod").read_text(encoding="utf-8")
            if "model" not in fpmod or "USB_C_Recept.step" not in fpmod:
                _fail(f"Projects/Health: ADD path did not persist the 3D-model line, got {fpmod!r}")
            dlg.deleteLater()
            shutil.rmtree(libd, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Health: library-only picker link/add", e)

    def _bom(p):
        from PyQt5.QtWidgets import QTableWidget
        p._run_primary(); _pump()                         # ▶ Build and Cost (offline, no pricing)
        if not hasattr(p, "_last_bom"):
            _fail("Projects/BOM: ▶ Build set no _last_bom seam")
        p._boards_spin.setValue(10); _pump()              # live re-projection + verdict track

        # Drive the priced-BOM decision surface directly (a real price lookup would hit the
        # network): a Mouser line, an LCSC line short at volume + long lead, and an unsourced
        # unpriced passive. Exercises the stock tint, the Source view-filter, the export
        # line-scope filters, and the consolidated Details modal — the whole subsystem.
        priced = [
            {"refs": ["U1"], "qty": 1, "mpn": "MCU1", "value": "STM32", "footprint": "LQFP",
             "source": "Mouser", "unit_price": 5.0, "extended": 5.0, "stock": 100, "lifecycle": "Active"},
            {"refs": ["U2"], "qty": 1, "mpn": "FPGA1", "value": "FPGA", "footprint": "BGA",
             "source": "LCSC", "unit_price": 20.0, "extended": 20.0, "stock": 3,
             "lifecycle": "Active", "lead_time": "16 Weeks"},
            {"refs": ["R1", "R2"], "qty": 2, "mpn": "", "value": "10k", "footprint": "R_0402"},
        ]
        p._last_bom = {"rows": priced, "cost": {"total_cost": 25.0, "unpriced_lines": 1}}
        p._last_mode = "project"; p._summary_owner = "bom"
        p._boards_spin.setValue(1); _pump()
        p._source_filter.setCurrentIndex(0)               # All Sources
        p._draw_bom_table(p._last_bom, "project"); _pump()

        def _bom_table():
            tbls = p.findChildren(QTableWidget)
            return tbls[-1] if tbls else None

        t0 = _bom_table()
        if t0 is None or t0.rowCount() != 3:
            _fail("Projects/BOM: priced project table did not render 3 lines")
        # Boards bump → the FPGA (stock 3) can't cover a 5-board run → its row tints (painted
        # by the row-tint delegate, installed only when at least one row is at risk).
        p._boards_spin.setValue(5); _pump()
        t1 = _bom_table()
        if getattr(t1, "_row_tint_delegate", None) is None:
            _fail("Projects/BOM: no at-risk row tinted after bumping Boards past stock")
        # And no tint when stock comfortably covers the run (boards back to 1).
        p._boards_spin.setValue(1); _pump()
        if getattr(_bom_table(), "_row_tint_delegate", None) is not None:
            _fail("Projects/BOM: a row stayed tinted when stock covered the 1-board run")
        p._boards_spin.setValue(5); _pump()
        # Source view-filter narrows the visible rows read-only.
        p._source_filter.setCurrentIndex(1); _pump()      # Mouser Only
        if _bom_table().rowCount() != 1:
            _fail("Projects/BOM: Source=Mouser Only did not narrow the table to 1 line")
        p._source_filter.setCurrentIndex(0); _pump()      # back to All
        if _bom_table().rowCount() != 3:
            _fail("Projects/BOM: Source=All did not restore every line")

        # Export line-scope: 'Priced Only' must drop the unpriced passive from the written CSV.
        import tempfile as _tf2
        out = Path(_tf2.mkdtemp(prefix="drive_bom_")) / "bom.csv"
        _orig = PROJ.QFileDialog.getSaveFileName
        PROJ.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(out), ""))
        try:
            p._priced_only_cb.setChecked(True)
            p._export_csv(); _pump()
            text = out.read_text(encoding="utf-8")
            if "MCU1" not in text or "10k" in text:
                _fail("Projects/BOM: 'Priced Only' export did not drop the unpriced line")
            p._priced_only_cb.setChecked(False)
        finally:
            PROJ.QFileDialog.getSaveFileName = _orig

        # Consolidated Details modal: build a per-board breakdown line and open it (headless →
        # built + reaped, never blocking).
        crow = {"mpn": "MCU1", "value": "STM32", "footprint": "LQFP", "total_qty": 5,
                "per_board": {"BoardA": 3, "BoardB": 2}}
        dlg = p._consolidated_details_dialog(crow)
        if getattr(dlg, "_chart_rows", None) != crow["per_board"]:
            _fail("Projects/BOM: consolidated Details modal missing its per-board chart data")
        dlg.deleteLater()
        p._open_consolidated_details(crow); _pump()       # the real click path (headless-safe)

    def _pcb(p):
        p._validate(); _pump()                            # verdict push, no board write
        p._load_vault_template(); _pump()                 # vault-standard swap + row rebuild
        # M1 (Text & Silkscreen): the silk size is now EDITABLE and Conform rewrites the
        # real .kicad_pcb. Drive it on a controlled board with a silk gr_text — set the silk
        # spinner, Preview (arms Apply) → Apply (headless auto-confirms), then assert the file
        # was rewritten to the new size and a .bak backup was kept. Regression lock for M1.
        d = Path(tempfile.mkdtemp(prefix="drive_conform_"))
        (d / "Cf.kicad_pro").write_text(
            '{"board":{"design_settings":{}},"meta":{"version":1},"net_settings":{}}',
            encoding="utf-8", newline="\n")
        board = d / "Cf.kicad_pcb"
        board.write_text(
            '(kicad_pcb (version 20241229) (generator "pcbnew")\n'
            '  (gr_text "L1" (at 5 5) (layer "F.SilkS")\n'
            '    (effects (font (size 0.5 0.5) (thickness 0.1))))\n'
            ')\n', encoding="utf-8", newline="\n")
        cfg2 = dict(cfg); cfg2["RepoRoot"] = str(d)
        cp = PROJ._pcb_setup_panel(_styled_ctx(cfg2), PROJ.ProjectsState(cfg2))
        try:
            szsp, _thsp = cp._text_fields["silk"]
            cp._text_checks["silk"].setChecked(True)      # silk conform on (also the default)
            szsp._mm = 1.5; szsp._render()                # edit the silk size
            cp._run_conform(False); _pump()               # Preview arms Apply
            if not cp._conform_apply_btn.isEnabled():
                _fail("Projects/Editor: Preview Conform did not arm Apply on a silk board")
            cp._run_conform(True); _pump()                # Apply (headless auto-confirms)
            after = board.read_text(encoding="utf-8")
            if "(size 1.5 1.5)" not in after:
                _fail(f"Projects/Editor: silk text not conformed to 1.5, got {after!r}")
            if not list(d.glob("Cf.kicad_pcb*.bak")):
                _fail("Projects/Editor: Conform kept no .bak backup")
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Editor: Text & Silkscreen conform", e)
        # M2 (Stackup & Thickness): the physical board thickness is EDITABLE and rides the ONE
        # ▶ Save fab write (the spinner value is baked into the fab-preset copy, so Save never
        # silently reverts it). Set a custom thickness, Save, assert the .kicad_pcb (general)
        # thickness matches + the stackup was written; a second Save is idempotent (no re-write).
        d2 = Path(tempfile.mkdtemp(prefix="drive_thick_"))
        (d2 / "Th.kicad_pro").write_text(
            '{"board":{"design_settings":{}},"meta":{"version":1},"net_settings":{}}',
            encoding="utf-8", newline="\n")
        board2 = d2 / "Th.kicad_pcb"
        board2.write_text(
            '(kicad_pcb (version 20241229) (generator "pcbnew")\n'
            '  (general (thickness 1.6))\n'
            '  (setup (pad_to_mask_clearance 0.05))\n'
            ')\n', encoding="utf-8", newline="\n")
        cfg3 = dict(cfg); cfg3["RepoRoot"] = str(d2)
        tp = PROJ._pcb_setup_panel(_styled_ctx(cfg3), PROJ.ProjectsState(cfg3))
        try:
            tp._prof_state["fab"] = "OSH Park 4-layer"    # a real PRESETS entry so fab writes
            tp._thick_field._mm = 1.2                     # override the physical thickness
            for _bk, (_bkind, _bw) in tp._bg_fields.items():   # change a bg field so bg ACTUALLY
                if _bkind == "num":                            # rewrites the board (post-bg !=
                    _bw._mm = float(_bw._mm) + 0.03            # pristine), making the .bak clobber
                    break                                      # observable if unfixed
            pristine2 = board2.read_text(encoding="utf-8")  # pre-save copy for the .bak check
            tp._save(); _pump()
            txt2 = board2.read_text(encoding="utf-8")
            if "(thickness 1.2)" not in txt2:
                _fail(f"Projects/Editor: board thickness not written as 1.2, got {txt2!r}")
            if "(stackup" not in txt2:
                _fail("Projects/Editor: fab stackup not written on Save")
            # M6 lock: bg (board geometry, an explicit setup key is always in bvals) AND fab both
            # write the SAME .kicad_pcb in one Save; the surviving .bak must be the PRISTINE
            # pre-save board (fab must NOT clobber bg's backup, mirroring the .kicad_pro scheme).
            baks2 = list(d2.glob("Th.kicad_pcb*.bak"))
            if not baks2:
                _fail("Projects/Editor: board Save kept no .kicad_pcb .bak")
            elif baks2[0].read_text(encoding="utf-8") != pristine2:
                _fail("Projects/Editor: .kicad_pcb .bak is not pristine (fab clobbered bg's backup)")
            before2 = board2.read_text(encoding="utf-8")
            tp._save(); _pump()                           # idempotent: same values -> no re-write
            if board2.read_text(encoding="utf-8") != before2:
                _fail("Projects/Editor: idempotent re-save changed the board")
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Editor: Stackup & Thickness save", e)
        # M3 (Design Rules completion): DRC/ERC severities + predefined size tables + ERC pin
        # map are editable and written by ▶ Save via pm.save_extended. Build a project with an
        # existing extended state; assert an untouched panel reports nothing to write (dirty
        # detection), then change a DRC + ERC severity, edit a predefined track width, toggle a
        # pin cell, Save, and assert the .kicad_pro was rewritten + a .bak kept. Lock for M3.
        import json as _json
        d3 = Path(tempfile.mkdtemp(prefix="drive_dre_"))
        pro3 = d3 / "Dre.kicad_pro"
        # A Default net class with a via (so pm.default_netclass loads as MANAGED) — needed to
        # reproduce the dr/dre Default-via conflict + the pristine-.bak regression below.
        pro3.write_text(_json.dumps({
            "board": {"design_settings": {
                "rules": {"min_clearance": 0.2},
                "rule_severities": {"clearance": "error", "silk_overlap": "warning"},
                "track_widths": [0.0, 0.25]}},
            "erc": {"rule_severities": {"pin_not_connected": "warning"},
                    "pin_map": [[0] * 12 for _ in range(12)]},
            "net_settings": {"classes": [{"name": "Default", "clearance": 0.2,
                                          "track_width": 0.2, "via_diameter": 0.8,
                                          "via_drill": 0.4}]},
            "text_variables": {"OLD_VAR": "gone"},            # M5: exists so Remove can delete it
            "meta": {"version": 1}}), encoding="utf-8", newline="\n")
        pristine = pro3.read_text(encoding="utf-8")           # pre-save copy for the .bak check
        cfg4 = dict(cfg); cfg4["RepoRoot"] = str(d3)
        mp = PROJ._pcb_setup_panel(_styled_ctx(cfg4), PROJ.ProjectsState(cfg4))
        try:
            if mp._capture().get("dre_dirty"):
                _fail("Projects/Editor: untouched extended state reported dirty")
            mp._sev_combos["drc"]["clearance"].setCurrentText("warning")   # error -> warning
            mp._sev_combos["erc"]["pin_not_connected"].setCurrentText("ignore")
            trk = mp._psize_tables["track"]
            if trk.rowCount() >= 1:
                trk.cellWidget(0, 0)._mm = 0.35                # edit predefined track width
            mp._dr_fields["default_via_diameter"]._mm = 1.0    # edit the FLAT via spin (dr's key)
            from PyQt5.QtWidgets import QPushButton as _QPB
            pm_cells = [b for b in mp.findChildren(_QPB) if b.objectName().startswith("pmc_")]
            if pm_cells:
                pm_cells[0].click()                            # cycle a pin cell 0 -> 1 (symmetric)
            # M4 net-class columns: create a class, edit its new columns (microvia / diff-pair-
            # via-gap / wire stroke / line-style) — round-trips through the "nc" writer.
            mp._nc_new()
            _ncrow = mp._nc_rows()[-1]; _newnc = _ncrow["name"]
            _ncrow["spins"]["microvia_diameter"]._mm = 0.29
            _ncrow["spins"]["diff_pair_via_gap"]._mm = 0.30
            _ncrow["spins"]["wire_thickness"]._mm = 0.254      # 10 mils -> wire_width 10
            _ncrow["line_style"].setCurrentIndex(1)            # Dashed -> line_style 1
            # M4 Default Net Class row: edit clearance (rides "dre"). Must round-trip AND
            # coexist with the flat via edit (dr) on the SAME Default class (Option B ownership).
            mp._dnc_fields["clearance"]._mm = 0.35
            # M5 Project Meta: add a new text var + delete the pre-existing OLD_VAR (clear name).
            mp._meta_add()
            for _rr in range(mp._meta_tbl.rowCount()):
                _nc = mp._meta_tbl.cellWidget(_rr, 0)
                if _nc.text().strip() == "OLD_VAR":
                    _nc.setText("")                            # cleared name -> removed on save
                elif not _nc.text().strip():
                    _nc.setText("NEW_VAR"); mp._meta_tbl.cellWidget(_rr, 1).setText("hello")
            if not mp._capture().get("dre_dirty"):
                _fail("Projects/Editor: changed extended state not detected as dirty")
            mp._save(); _pump()
            # M6 lock: after a dre save the extended baseline must be re-set, so a repeat Save
            # with no further edit is NOT reported dirty (no idle save_extended re-write churn).
            if mp._capture().get("dre_dirty"):
                _fail("Projects/Editor: dre still dirty after save (dre_base not re-baselined)")
            rel = _json.loads(pro3.read_text(encoding="utf-8"))
            ds = rel.get("board", {}).get("design_settings", {})
            if ds.get("rule_severities", {}).get("clearance") != "warning":
                _fail(f"Projects/Editor: DRC severity not saved, got {ds.get('rule_severities')!r}")
            if rel.get("erc", {}).get("rule_severities", {}).get("pin_not_connected") != "ignore":
                _fail("Projects/Editor: ERC severity not saved")
            if 0.35 not in ds.get("track_widths", []):
                _fail(f"Projects/Editor: predefined track width not saved, got {ds.get('track_widths')!r}")
            pmap = rel.get("erc", {}).get("pin_map", [])
            if not any(x for row in pmap for x in row):
                _fail("Projects/Editor: ERC pin-map edit not saved")
            # Fix 1 lock: the flat Via edit (dr) must NOT be reverted by save_extended (dre)
            # rewriting the Default class from the stale loaded via.
            _dflt = next((c for c in rel.get("net_settings", {}).get("classes", [])
                          if c.get("name") == "Default"), {})
            if abs(float(_dflt.get("via_diameter", 0.0)) - 1.0) > 0.01:
                _fail(f"Projects/Editor: flat Via edit reverted by dre, got {_dflt.get('via_diameter')!r}")
            # M4 lock: the Default class carries BOTH dr's via (1.0) AND dre's clearance (0.35)
            # — Option B ownership (disjoint keys) coexists, neither writer reverts the other.
            if abs(float(_dflt.get("clearance", 0.0)) - 0.35) > 0.01:
                _fail(f"Projects/Editor: Default-class clearance (dre) not saved, got {_dflt.get('clearance')!r}")
            # M4 lock: the new net class round-trips its microvia / diff-pair-via / wire stroke
            # / line-style columns through save_to_project (the "nc" writer).
            _cls = next((c for c in rel.get("net_settings", {}).get("classes", [])
                         if c.get("name") == _newnc), None)
            if _cls is None:
                _fail(f"Projects/Editor: new net class {_newnc!r} not written")
            else:
                if abs(float(_cls.get("microvia_diameter", 0.0)) - 0.29) > 0.01:
                    _fail(f"Projects/Editor: net-class microvia not saved, got {_cls.get('microvia_diameter')!r}")
                if abs(float(_cls.get("diff_pair_via_gap", 0.0)) - 0.30) > 0.01:
                    _fail(f"Projects/Editor: net-class diff_pair_via_gap not saved, got {_cls.get('diff_pair_via_gap')!r}")
                if _cls.get("wire_width") != 10:
                    _fail(f"Projects/Editor: net-class wire stroke not saved, got {_cls.get('wire_width')!r}")
                if _cls.get("line_style") != 1:
                    _fail(f"Projects/Editor: net-class line_style not saved, got {_cls.get('line_style')!r}")
            # M5 lock: a text var ADD lands and a REMOVE (cleared name) actually deletes the
            # key from the file (not just stops re-writing it).
            _tv = rel.get("text_variables", {})
            if _tv.get("NEW_VAR") != "hello":
                _fail(f"Projects/Editor: text var add not saved, got {_tv!r}")
            if "OLD_VAR" in _tv:
                _fail(f"Projects/Editor: text var removal not applied, OLD_VAR still present: {_tv!r}")
            # Fix 2 lock: the surviving .bak must be the PRISTINE pre-save file (not clobbered
            # by a later writer to the same .kicad_pro).
            baks = list(d3.glob("Dre.kicad_pro*.bak"))
            if not baks:
                _fail("Projects/Editor: save kept no .bak backup")
            elif baks[0].read_text(encoding="utf-8") != pristine:
                _fail("Projects/Editor: .bak is not the pristine pre-save file")
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Editor: Design Rules + Net Classes completion "
                  "(severities/tables/pin map/net-class columns/Default class)", e)

        # ── Editor enhancements: fab-preset manager, net-class Duplicate, validate-on-save
        #    preview, severity schemes + size templates, per-section dirty dots, per-row
        #    delete. Store paths are redirected to a temp dir so the repo is never written.
        _store_dir = Path(tempfile.mkdtemp(prefix="drive_edit_"))
        _o_fab, _o_dp = PROJ.fabp._presets_path, PROJ.dpre._store_path
        PROJ.fabp._presets_path = lambda: _store_dir / "fab.json"
        PROJ.dpre._store_path = lambda: _store_dir / "design.json"
        try:
            d5 = Path(tempfile.mkdtemp(prefix="drive_edit_pro_"))
            (d5 / "Edit.kicad_pro").write_text(_json.dumps({
                "board": {"design_settings": {"rule_severities": {"clearance": "error"},
                                              "track_widths": [0.0, 0.25]}},
                "erc": {"rule_severities": {}, "pin_map": [[0] * 12 for _ in range(12)]},
                "net_settings": {"classes": [{"name": "Default", "clearance": 0.2,
                                              "track_width": 0.2, "via_diameter": 0.8, "via_drill": 0.4}]},
                "meta": {"version": 1}}), encoding="utf-8", newline="\n")
            cfg5 = dict(cfg); cfg5["RepoRoot"] = str(d5)
            ep = PROJ._pcb_setup_panel(_styled_ctx(cfg5), PROJ.ProjectsState(cfg5))

            # 1. per-section dirty dots start clean, then a severity scheme + a size template
            #    make exactly their sections dirty.
            if any(ep._section_dirty().values()):
                _fail("Projects/Editor: sections reported dirty on a freshly-loaded panel")
            ep._apply_severity_scheme("Strict")
            if ep._sev_combos["drc"]["unconnected_items"].currentText() != "error":
                _fail("Projects/Editor: severity scheme did not set every rule")
            if not ep._section_dirty()["DRC & ERC Severities"]:
                _fail("Projects/Editor: severity-scheme apply did not mark its section dirty")
            ep._apply_size_template("Power")
            if ep._psize_tables["track"].rowCount() != 3:
                _fail(f"Projects/Editor: size template did not refill the track table "
                      f"({ep._psize_tables['track'].rowCount()} rows)")
            if not ep._section_dirty()["Predefined Sizes"]:
                _fail("Projects/Editor: size-template apply did not mark its section dirty")

            # 2. per-row predefined delete (removes the remove-then-add friction)
            _before = ep._psize_tables["track"].rowCount()
            ep._ps_delete_row("track", 0)
            if ep._psize_tables["track"].rowCount() != _before - 1:
                _fail("Projects/Editor: per-row predefined delete did not drop a row")

            # 3. net-class Duplicate creates a <name>_2 variant with no patterns
            ep._nc_new()
            _src = ep._nc_rows()[-1]["name"]
            _dup = ep._nc_duplicate(_src)
            if not _dup or ep._ncmgr.get_netclass(_dup) is None:
                _fail(f"Projects/Editor: net-class Duplicate produced no class (src {_src!r})")
            elif ep._ncmgr.get_netclass(_dup).patterns:
                _fail("Projects/Editor: duplicated net class copied patterns (double-claims nets)")

            # 4. validate-on-save preview: a below-floor class surfaces a non-blocking warn op,
            #    and the save still proceeds (the check never blocks a write).
            ep._nc_rows()[-1]["spins"]["track_width"]._mm = 0.005      # far below any fab floor
            ep._commit_netclasses()
            _snap = ep._capture()
            _ops = ep._save_audit(_snap)
            if not any((not o.get("safe")) and str(o.get("key", "")).startswith("ack:") for o in _ops):
                _fail("Projects/Editor: below-floor class did not appear as a preview violation")
            if not ep._save_violations():
                _fail("Projects/Editor: validate-on-save did not record any violation")
            ep._save(); _pump()                                       # non-blocking: still writes

            # 5. create + select a custom fab preset; it persists and reloads on a rebuild.
            _cp = PROJ.fabp.FabPreset(
                name="Drive Custom Fab", layers=2, min_track_width=0.15, min_clearance=0.15,
                min_drill=0.3, min_annular_ring=0.13, min_edge_clearance=0.3,
                default_track_width=0.25, default_via_diameter=0.6, default_via_drill=0.3,
                board_thickness_mm=1.6, copper_oz=1.0, material="FR-4", finish="HASL",
                soldermask="green",
                stackup=(("F.Cu", "copper", 0.035, "copper"), ("core", "core", 1.5, "FR-4"),
                         ("B.Cu", "copper", 0.035, "copper")))
            PROJ.fabp.save_preset(_cp)
            ep._refresh_fab_presets()
            ep._set_fab("Drive Custom Fab")
            if ep._prof_state["fab"] != "Drive Custom Fab":
                _fail("Projects/Editor: custom fab preset was not selected")
            if ep._fab_combo.currentText() != "Drive Custom Fab":
                _fail("Projects/Editor: fab selector did not show the custom preset")
            if PROJ.fabp.get_preset("Drive Custom Fab") is None:
                _fail("Projects/Editor: custom fab preset did not persist")
            ep2 = PROJ._pcb_setup_panel(_styled_ctx(cfg5), PROJ.ProjectsState(cfg5))
            if "Drive Custom Fab" not in [ep2._fab_combo.itemText(i)
                                          for i in range(ep2._fab_combo.count())]:
                _fail("Projects/Editor: custom fab preset not reloaded on a fresh panel")

            # 6. open the Manage modal (headless: constructs the dialog + returns before exec_)
            #    and exercise the _warn_dialog import path (Delete on a locked built-in). Locks
            #    the _open_fab_manager / _warn_dialog relative-import path — a wrong import there
            #    crashes the real button while the backend seams (above) stay green. Avoids
            #    QInputDialog (would block offscreen), so no New/Duplicate prompt path here.
            ep._open_fab_manager()
            _dlg = PROJ.FabPresetManagerDialog(ep, on_change=lambda: None)
            _dlg._select_name("OSH Park 4-layer")                 # a locked built-in
            _dlg._delete()                                        # hits _warn_dialog + returns
            PROJ._warn_dialog(ep, "drive-audit reachability check")
        except Exception as e:  # noqa: BLE001
            _fail("Projects/Editor: enhancements (fab manager / duplicate / validate-preview "
                  "/ schemes / templates / dirty dots / per-row delete)", e)
        finally:
            PROJ.fabp._presets_path = _o_fab
            PROJ.dpre._store_path = _o_dp

    def _refactor(p):
        from PyQt5.QtWidgets import QPushButton as _QPB
        if not p.findChildren(_QPB):
            _fail("Projects/Refactor: no action buttons")

    for builder, label, drive in (
        (PROJ._overview_panel, "Overview", _overview),
        (PROJ._health_panel, "Health", _health),
        (PROJ._bom_panel, "Bill of Materials", _bom),
        (PROJ._pcb_setup_panel, "Editor", _pcb),
        (PROJ._rename_panel, "Refactor", _refactor),
    ):
        try:
            panel = builder(_styled_ctx(cfg), state)
            drive(panel)
            _pump()
        except Exception as e:  # noqa: BLE001
            _fail(f"Projects styled {label}", e)
    print("  projects styled driven (5-tab switch + each ▶ primary, no crash)", flush=True)


def audit_bench_styled():
    """Drive the STYLED Bench feature — the tabbed kit.tabbed_page workspace that the old
    audit_bench() never touched (it drives BARE). Two crash classes:

    (1) the PACKAGE-SWITCH path: build the whole BenchFeature and cycle the real header combo
        through every package. Each pick fires state.set_package -> ws.rebuild_all, which
        deleteLater's and rebuilds EVERY sub-panel (the rebuild-in-signal class this harness
        exists for). A stale bus closure or use-after-free surfaces here.
    (2) each panel built directly on a BenchState — especially the new Analysis tab (category
        lists + card materials + ADG714 cell map + socket connections + the claim-file lint),
        asserting its tables render and the ▶ lint flow runs headlessly (picker _headless-
        guarded) without a crash, plus the Exports tab's Save File menu carries Pin-Map SVG."""
    from ui.features import bench as BENCH
    from PyQt5.QtWidgets import QTableWidget
    cfg = _make_fixture()

    # (1) package-switch / rebuild_all — drive the header combo the way the user does.
    try:
        feature = BENCH.BenchFeature()
        ws = feature.build(_styled_ctx(cfg))
        _pump()
        combos = ws.findChildren(QComboBox)
        pkg = next((c for c in combos if c.count() > 0), None)
        if pkg is None:
            print("  bench styled: no package selector (DB not built?) — skipped", flush=True)
            return
        for i in range(min(6, pkg.count())):
            pkg.setCurrentIndex(i); _pump()
            from PyQt5.QtCore import QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pkg.setCurrentIndex(0); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Bench styled package-switch (rebuild_all)", e); return

    # (2) drive each panel directly on a fresh BenchState (the buildable-package default).
    state = BENCH.BenchState()
    if state.error or state.package is None:
        print("  bench styled: STM32 DB unavailable — panel drive skipped", flush=True)
        print("  bench styled driven (package switch only)", flush=True)
        return

    def _analysis(p):
        # the new Analysis tab must render its four authority tables, then lint headlessly.
        tables = p.findChildren(QTableWidget)
        if len(tables) < 4:
            _fail(f"Bench/Analysis: expected >=4 tables (category/materials/adg714/socket), "
                  f"got {len(tables)}")
        # BENCH v2.11 regression lock: the Analysis tables must fit ALL their rows at
        # natural height (fit_rows) — NOT clip to ~one visible row inside their own inner
        # scrollbar (owner report: "cut to just one single line that you had to scroll
        # through"). Each multi-row table must be sized to more than a single row's height
        # and must NOT show a vertical scrollbar.
        from PyQt5.QtCore import Qt as _Qt
        fitted = 0
        for t in tables:
            if t.rowCount() < 2:
                continue                     # a 0/1-row table is trivially fully shown
            one_row = (t.horizontalHeader().height() + t.rowHeight(0)
                       + 2 * t.frameWidth())
            if t.verticalScrollBarPolicy() != _Qt.ScrollBarAlwaysOff:
                _fail(f"Bench/Analysis: a {t.rowCount()}-row table still has an inner "
                      f"vertical scrollbar (fit_rows not applied)")
            elif t.height() <= one_row:
                _fail(f"Bench/Analysis: a {t.rowCount()}-row table is sized to "
                      f"{t.height()}px (<= one-row {one_row}px) — clipped to one line")
            else:
                fitted += 1
        if fitted == 0:
            _fail("Bench/Analysis: no multi-row table was fit to its content rows")
        lint_btn = next((b for b in p.findChildren(QPushButton)
                         if b.text().startswith("Lint Claim File")), None)
        if lint_btn is None:
            _fail("Bench/Analysis: no 'Lint Claim Files…' action")
        else:
            lint_btn.click(); _pump()          # _headless() short-circuits the picker; no crash

    def _exports(p):
        # The three single-file saves now live under one "Save File ▾" menu button
        # (progressive disclosure). Assert the menu carries the Pin-Map SVG entry, and
        # drive it headlessly (the file picker is _headless-guarded, so no crash).
        save_menu = next((b for b in p.findChildren(QPushButton)
                          if b.text().startswith("Save File")), None)
        if save_menu is None or not hasattr(save_menu, "_menu"):
            _fail("Bench/Exports: 'Save File ▾' menu button missing")
            return
        svg = next((a for a in save_menu._menu.actions() if "Pin-Map SVG" in a.text()), None)
        if svg is None:
            _fail("Bench/Exports: 'Pin-Map SVG' entry missing from the Save File menu")
            return
        svg.trigger()          # _headless() short-circuits the picker; must not crash
        if not any(b.text() == "Write Authority Bundle" for b in p.findChildren(QPushButton)):
            _fail("Bench/Exports: Write Authority Bundle primary action missing")

    for builder, label, drive in (
        (BENCH._authority_panel, "Overview", lambda p: None),
        (BENCH._profiles_panel, "Profiles", lambda p: None),
        (BENCH._allpins_panel, "All Pins", lambda p: None),
        (BENCH._analysis_panel, "Analysis", _analysis),
        (BENCH._resolver_panel, "MCU Pinout Viewer", lambda p: None),
        (BENCH._outputs_panel, "Exports", _exports),
    ):
        try:
            panel = builder(_styled_ctx(cfg), state)
            drive(panel)
            _pump()
        except Exception as e:  # noqa: BLE001
            _fail(f"Bench styled {label}", e)
    print("  bench styled driven (package switch + each tab, Analysis lint + Exports Save-File menu, no crash)",
          flush=True)


def audit_settings_styled():
    """Drive the STYLED Settings feature — the machine-setup section the old audit_plain(4)
    never touched (it drives BARE). Builds SettingsFeature, asserts the Machine Setup verdict
    card + its three actions render, and clicks the read-only Refresh Status (rebuilds the live
    verdict grid — a stale-closure/use-after-free surfaces there). Does NOT click Set Up This
    Machine (it writes real KiCad sym/fp-lib-tables) or Rebuild STM32 Database (a multi-minute
    build if a CubeMX source happens to be present) — both are asserted present, not fired."""
    from ui.features import settings as SETTINGS
    cfg = _make_fixture()
    try:
        feature = SETTINGS.SettingsFeature()
        page = feature.build(_styled_ctx(cfg))
        _pump()
        btns = {b.text() for b in page.findChildren(QPushButton)}
        for need in ("Set Up This Machine", "Rebuild STM32 Database", "Refresh Status"):
            if need not in btns:
                _fail(f"Settings styled: Machine Setup action '{need}' missing")
        refresh = next((b for b in page.findChildren(QPushButton)
                        if b.text() == "Refresh Status"), None)
        if refresh is not None:
            refresh.click(); _pump()             # read-only: rebuilds the live verdict grid
    except Exception as e:  # noqa: BLE001
        _fail("Settings styled (machine setup)", e); return
    print("  settings styled driven (machine-setup verdict + actions, Refresh rebuild, no crash)",
          flush=True)


def audit_subtab_animation():
    """Regression lock for the v2.10.0 'clicking a second subtab crashes the exe' segfault.
    SlidingUnderline.move_to started its tween with anim.start(DeleteWhenStopped) while keeping
    a Python ref in self._anim; once the animation finished, DeleteWhenStopped freed the C++
    object and the next click's self._anim.stop() was a use-after-free (hard crash on Windows).
    EVERY existing gate missed it because the animated path only runs with motion ON —
    reduced_motion (which the render gate sets) skips it — AND offscreen subtab buttons have no
    geometry, so Workspace._position_underline early-returns before ever calling move_to. So
    drive a REAL Workspace's underline with motion ON, geometry forced, reaping the animation
    between moves (the exact free-then-reuse trigger), and assert no crash."""
    from ui import motion as _motion
    from ui.features import bench as BENCH
    from PyQt5.QtCore import QEvent
    cfg = _make_fixture()
    prev = _motion.reduced_motion()
    _motion.set_reduced_motion(False)                     # exercise the ANIMATED path (real exe)
    try:
        ws = BENCH.BenchFeature().build(_styled_ctx(cfg))
        u = getattr(ws, "_underline", None)
        if u is None:
            print("  subtab-animation: no underline (single-panel?) — skipped", flush=True)
            return
        u.setGeometry(0, 0, 10, 2)
        for i in range(4):
            u.move_to(40 + i * 30, 60, animate=True)      # start an animated slide
            if getattr(u, "_anim", None) is not None:
                u._anim.stop()                            # reap it (old code: DeleteWhenStopped frees)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            _pump()
        print("  subtab-animation driven (motion ON, anim reaped between moves, no crash)",
              flush=True)
    except Exception as e:  # noqa: BLE001
        _fail("Subtab underline animation (second-click use-after-free)", e)
    finally:
        _motion.set_reduced_motion(prev)


def audit_shell_chrome():
    """Drive the shell CHROME quick-wins end-to-end on the real NetdeckShell: nav
    collapse/expand, Ctrl+K filter matching title AND category with per-category eyebrow
    grouping, a no-result 'Did you mean?' suggestion + adopt, the keyboard-shortcuts
    reference enumeration, and an 'Error:'-line auto-expanding the Activity console. The
    console assertion flips _auto_surface_errors ON explicitly because it is gated OFF
    under headless — the guard itself is asserted first so both halves are proven."""
    from ui.shell import NetdeckShell
    from ui import features  # noqa: F401 — importing registers every feature
    LM.write_setting("NavSearch", "")            # clean slate for the persist/restore path
    try:
        shell = NetdeckShell(LM.load_config())
        shell.show(); _pump()                     # show so isHidden() reflects the filter flags

        def vis_ids():
            return [shell._page_specs[i][0].id for i, it in enumerate(shell._nav_items)
                    if not it.isHidden() and shell._page_specs[i][0].id != "settings"]

        # nav collapse/expand
        w_open = shell._nav.width()
        shell._toggle_nav(); _pump()
        if not shell._nav_collapsed or shell._nav.width() >= w_open:
            _fail("Shell chrome: nav did not collapse")
        shell._toggle_nav(); _pump()
        if shell._nav_collapsed:
            _fail("Shell chrome: nav did not expand back")

        # Ctrl+K filter by CATEGORY text + per-category eyebrow grouping
        shell._search.setText("firmware"); _pump()
        if vis_ids() != ["bench"]:
            _fail(f"Shell chrome: category search 'firmware' → {vis_ids()} (want ['bench'])")
        if shell._cat_eyebrows["Firmware"].isHidden() or not shell._eyebrow.isHidden():
            _fail("Shell chrome: category grouping eyebrows wrong while searching")

        # no-result 'Did you mean?' + adopt
        shell._search.setText("libary"); _pump()
        if shell._did_you_mean.isHidden() or shell._suggestion != "Library":
            _fail(f"Shell chrome: 'did you mean' missing (suggestion={shell._suggestion!r})")
        shell._adopt_suggestion(); _pump()
        if shell._search.text() != "Library" or shell._did_you_mean.isHidden() is False:
            _fail("Shell chrome: adopting the suggestion did not resolve to a match")
        shell._search.setText(""); _pump()
        if set(vis_ids()) != {"library", "projects", "bench", "git"}:
            _fail(f"Shell chrome: clearing search did not restore the flat list ({vis_ids()})")

        # persist-on-clear: a query cleared by Ctrl+B collapse (bypasses editingFinished)
        # must NOT stay persisted, or it would wrongly re-apply next launch
        shell._search.setText("bench"); shell._persist_search(); _pump()
        shell._toggle_nav(); _pump()              # collapse clears the field
        if LM.read_setting("NavSearch", "") != "":
            _fail("Shell chrome: collapsing left a stale query persisted (would re-apply on relaunch)")
        shell._toggle_nav(); _pump()

        # keyboard-shortcuts reference enumerates the bound shortcuts
        rows = dict(shell._iter_shortcuts())
        for keys in ("Ctrl+K", "Ctrl+B", "Ctrl+/"):
            if not rows.get(keys):
                _fail(f"Shell chrome: shortcut '{keys}' missing from the reference")
        dlg = shell._show_shortcuts()             # headless → built, not exec'd
        if dlg is None:
            _fail("Shell chrome: shortcuts dialog did not build")
        else:
            dlg.deleteLater()

        # Error auto-expand (headless-guarded): OFF by default, ON when enabled
        if shell._auto_surface_errors is not False:
            _fail("Shell chrome: _auto_surface_errors should be OFF under headless")
        shell._console_open = False; shell._console.setVisible(False); shell._unseen_activity = 0
        shell._log("Error: gated off stays hidden"); _pump()
        if shell._console_open is not False:
            _fail("Shell chrome: error surfaced the console while gated OFF")
        shell._auto_surface_errors = True
        shell._log("Error: this must surface"); _pump()
        if not shell._console_open or not shell._console.is_expanded():
            _fail("Shell chrome: an Error line did not auto-expand the console when enabled")

        shell.close(); _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Shell chrome quick-wins", e); return
    finally:
        LM.write_setting("NavSearch", "")
    print("  shell chrome driven (collapse, Ctrl+K category grouping, did-you-mean, "
          "shortcuts, error auto-expand — no crash)", flush=True)


def audit_library_empty_state():
    """P0 regression lock: a chosen folder that resolves NO parts must show the diagnostic
    banner (which path was checked + Open Settings), never a silent empty list — the fix
    for the v2.11 'correct folder, zero parts' report. Points the Library at an empty
    folder and asserts the list is empty AND the banner surfaces + names the path."""
    from PyQt5.QtWidgets import QLabel as _QLabel
    from ui.features import library as LIB
    import tempfile as _tf
    d = Path(_tf.mkdtemp(prefix="drive_lib_empty_"))
    cfg = {"RepoRoot": str(d), "Libs": str(d / "libs"),
           "SymbolLib": str(d / "libs" / "MySymbols.kicad_sym"),
           "FootprintLib": str(d / "libs" / "MyFootprints.pretty"),
           "ModelLib": str(d / "libs" / "My3DModels")}
    ctx = _styled_ctx(cfg)
    try:
        panel = LIB._parts_panel(ctx, None)
        _pump()
    except Exception as e:  # noqa: BLE001
        _fail("Library empty-state build", e); return
    if panel.parts_list._list.count() != 0:
        _fail("Library empty-state: expected 0 parts on an empty library")
    labels = [lb.text() for lb in panel.findChildren(_QLabel)]
    if not any("No Parts Loaded" in t for t in labels):
        _fail("Library empty-state: diagnostic banner did not show 'No Parts Loaded'")
    if not any("Looking in:" in t for t in labels):
        _fail("Library empty-state: banner did not name the path it checked")
    print("  library empty-state driven (diagnostic banner names the path + Open Settings)")


def main() -> int:
    cfg = _make_fixture()
    print("drive-audit fixture:", cfg["RepoRoot"], flush=True)
    audit_git_workbench()
    audit_library_workbench()
    audit_library_empty_state()
    audit_projects_styled()
    audit_bench_styled()
    audit_settings_styled()
    audit_subtab_animation()
    audit_shell_chrome()
    # Whole-app smoke: build the real NetdeckShell and lazily build EVERY page. The styled
    # shell is what `python -m ui` now launches; a construction/selection crash (a feature
    # registration or page-build regression, or the library-location rebuild) must fail here,
    # not in front of the user — the styled successor to the old BareWindow._rebuild_all lock.
    try:
        from ui.shell import NetdeckShell
        from ui import features  # noqa: F401 — importing registers every feature
        shell = NetdeckShell(LM.load_config())
        for i in range(shell._stack.count()):
            shell._select(i); _pump()
        print(f"  whole-app shell driven ({shell._stack.count()} pages built, no crash)",
              flush=True)
    except Exception as e:  # noqa: BLE001
        _fail("NetdeckShell whole-app build/select", e)
    print("=" * 40, flush=True)
    if _FAILURES:
        print(f"DRIVE-AUDIT FAILED — {len(_FAILURES)} issue(s):", flush=True)
        for f in _FAILURES:
            print("  " + f.splitlines()[0], flush=True)
        return 1
    print("DRIVE-AUDIT PASSED — all panels driven, selectors refresh, no crash.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
