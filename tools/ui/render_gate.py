"""ui.render_gate — offscreen dark+light screenshots of every app surface.

Drives the REAL shell headless so panels are built against real data, navigates
to each page and Workspace sub-panel, and grabs the window under both themes.
Committed regression gate: re-run after any UI change and self-audit the PNGs
against docs/design/design-rules.md. Images go to a gitignored dir.

    python tools/ui/render_gate.py --out build/render            # all, both themes
    python tools/ui/render_gate.py --surface bench --theme dark
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TOOLS = Path(__file__).resolve().parents[1]        # .../tools
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from PyQt5.QtWidgets import QApplication, QScrollArea  # noqa: E402

from ui import theme as T                            # noqa: E402
from ui import widgets as W                          # noqa: E402


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")


def _content_height(ws):
    """Natural full height of the current panel's content, so the window can grow
    to show below-the-fold content (a scroll_body otherwise clips the audit)."""
    if ws is None:
        return 0
    w = ws._stack.currentWidget()
    inner = w.widget() if isinstance(w, QScrollArea) else w
    if inner is None:
        return 0
    return max(inner.sizeHint().height(), inner.height())


def _surfaces(win):
    """Build every page; return [(feature_id, panel_name|None, page_idx, ws|None, panel_idx|None)]."""
    out = []
    for i in range(win._stack.count()):
        win._select(i)                               # lazily build the page
        feat = win._page_specs[i][0]
        page = win._stack.widget(i)
        # A feature's build() returns the Workspace AS the page root; findChildren
        # returns descendants only, so include the page itself when it is one.
        workspaces = page.findChildren(W.Workspace)
        if isinstance(page, W.Workspace):
            workspaces = [page, *workspaces]
        if not workspaces:
            out.append((feat.id, None, i, None, None))
            continue
        for ws in workspaces:
            for k, (name, _) in enumerate(ws._panels):
                out.append((feat.id, name, i, ws, k))
    return out


def _settle(app, seconds=1.5, step=0.05):
    """Pump the Qt event loop so threaded run_async workers finish and their
    queued done-callbacks repaint the panel before we grab it. Panels that load
    data off the GUI thread otherwise get captured mid-'Loading...'."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        app.sendPostedEvents()
        time.sleep(step)
    app.processEvents()


# Panels that start empty until an interaction; seed them so their rich (auditable)
# state renders. Keyed by surface stem; the callback receives the panel's Workspace.
_SEED = {
    "bench.mcu-pinout-viewer": lambda ws: ws._ctx.bus.emit("bench.resolve", "STM32F407VGT6"),
    "projects.editor": lambda ws: [cs.set_expanded(True)
                                   for cs in ws.findChildren(W.CollapsibleSection)],
}


def render_all(out_dir, themes=("dark", "light"), only=None, settle=1.5):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    T.load_fonts(app)

    import LibraryManager as LM
    from ui.shell import NetdeckShell
    from ui import features  # noqa: F401  importing registers every feature

    win = NetdeckShell(LM.load_config())
    win.resize(1440, 960)
    surfaces = _surfaces(win)

    saved = []
    for theme in themes:
        win.apply_theme(theme == "dark")
        for fid, name, page_idx, ws, k in surfaces:
            if only and fid != only:
                continue
            win._select(page_idx)
            if ws is not None:
                ws._select(k)
            stem = fid if name is None else f"{fid}.{_slug(name)}"
            seed = _SEED.get(stem)
            if seed is not None and ws is not None:
                try:
                    seed(ws)
                except Exception:  # noqa: BLE001 - a seed is best-effort, never fatal
                    pass
            W.restyle_all()
            _settle(app, settle)
            # Grow the window to fit the panel's full content (chrome + below-fold),
            # capped, so a scroll_body doesn't clip the audit; then restore.
            grow = min(_content_height(ws) + 150, 3200)
            win.resize(1440, max(960, grow))
            _settle(app, min(settle, 0.4))
            path = out_dir / f"{stem}.{theme}.png"
            win.grab().save(str(path))
            saved.append(path)
        saved.extend(_render_chrome(win, app, out_dir, theme, settle))
    win.close()
    return saved


def _render_chrome(win, app, out_dir, theme, settle):
    """Capture the shell CHROME states the per-surface loop never shows: the collapsed
    nav rail, a Ctrl+K search grouping matches under category eyebrows, the no-result
    'Did you mean?' suggestion, the keyboard-shortcuts reference, and the Activity console
    auto-expanded by an error. Restores the default chrome state afterwards so the other
    theme's captures start clean."""
    out = []
    win._select(0)
    win.resize(1440, 960)

    # 1. collapsed nav rail
    if not win._nav_collapsed:
        win._toggle_nav()
    _settle(app, settle)
    p = out_dir / f"chrome.nav-collapsed.{theme}.png"; win.grab().save(str(p)); out.append(p)
    win._toggle_nav()                       # expand back
    _settle(app, settle)

    # 2. Ctrl+K search grouping matches under category eyebrows ("r" hits every category)
    win._search.setText("r")
    _settle(app, settle)
    p = out_dir / f"chrome.nav-search-grouped.{theme}.png"; win.grab().save(str(p)); out.append(p)

    # 3. no-result 'Did you mean?' suggestion
    win._search.setText("libary")
    _settle(app, settle)
    p = out_dir / f"chrome.nav-did-you-mean.{theme}.png"; win.grab().save(str(p)); out.append(p)
    win._search.setText("")
    _settle(app, settle)

    # 4. keyboard-shortcuts reference dialog
    dlg = win._build_shortcuts_dialog()
    dlg.resize(360, dlg.sizeHint().height())
    dlg.show()
    _settle(app, settle)
    p = out_dir / f"chrome.shortcuts.{theme}.png"; dlg.grab().save(str(p)); out.append(p)
    dlg.close(); dlg.deleteLater()
    _settle(app, settle)

    # 5. Activity console auto-expanded by an Error line (flip the headless gate on)
    prev = win._auto_surface_errors
    win._auto_surface_errors = True
    win._log("Error: example failure so the console auto-expands for the render gate")
    _settle(app, settle)
    p = out_dir / f"chrome.console-error.{theme}.png"; win.grab().save(str(p)); out.append(p)

    # restore default chrome state for the next theme / a clean finish
    win._auto_surface_errors = prev
    win._console_open = False
    win._console.setVisible(False)
    win._unseen_activity = 0
    win._sync_activity_item()
    _settle(app, min(settle, 0.4))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render every app surface dark+light.")
    ap.add_argument("--out", default="build/render")
    ap.add_argument("--surface", default=None,
                    help="feature id: bench / library / projects / settings")
    ap.add_argument("--theme", default="both", choices=("dark", "light", "both"))
    ap.add_argument("--settle", type=float, default=1.5,
                    help="seconds to pump the event loop per surface so async loads finish")
    args = ap.parse_args(argv)
    themes = ("dark", "light") if args.theme == "both" else (args.theme,)
    saved = render_all(args.out, themes=themes, only=args.surface, settle=args.settle)
    print(f"Wrote {len(saved)} images to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
