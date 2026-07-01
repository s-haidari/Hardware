# Handoff — rebuild as a NATIVE PySide6 desktop app

Date: 2026-06-30
Author: previous session (ran long; Sadad is restarting fresh)

## The decision (read first)

The previous build was a **web app** (Tauri + Rust shell + FastAPI + React) meant
to look like the native PyQt Library Manager. It never did — Sadad's repeated
feedback: "you can tell it's a web UI." That is inherent: a WebView renders a web
page and can only fake native widgets.

**New direction: build it NATIVE in PySide6 (Qt), not as a web app.** Reasons:
- The reference app is already Qt (`git/Hardware/tools/LibraryManager.py`), so we
  extend working native code instead of imitating its look in HTML.
- Real native widgets for free: `QTreeView`, `QSplitter` with drag handles, menu
  bar, `QStatusBar`, `QProgressBar` — no CSS faking them.
- The valuable backend logic is plain Python in `app/backend/hwkit` and ports
  straight into a Qt app (call it in-process; no HTTP/React/Rust layer). The web
  stack that made it "feel like a web page" gets deleted.

So: **the Tauri/React/FastAPI web UI is abandoned.** Keep `hwkit` as the shared
Python logic layer; build a PySide6 GUI on top of it.

## What Sadad wants

1. **Native PySide6 desktop app that looks like the PyQt Library Manager.**
   Reference: `git/Hardware/tools/LibraryManager.py` (its `_THEME_QSS` ~line 2611
   is the exact stylesheet; layout build ~line 1461+). Not a web look.

2. **Library Manager features FIRST, all of them, exactly.** Sadad: "make it look
   like the library manager and have all the features of it over the STMP; STMP
   was ass." Every feature/button/panel/behavior in `LibraryManager.py` must be
   in the new app, working as it does there. STMP pinout features are secondary.

3. **Ground-up data schema (later).** When the pin/switch features come in, design
   a NEW pin-database schema from first principles — do NOT depend on the old
   STM-Helper schema, and delete the vendored `app/backend/stm32switch/` (old
   generator built around that schema). This is lower priority than the Library
   Manager UI/features.

4. **Look up best practices.** Research desktop-UI best practices online (Qt
   patterns, real repos, design systems) and use available Claude skills/MCPs.
   Goal is a professionally-designed native app, not AI-slop.

## Simplest architecture for the rebuild

- One PySide6 app. Reuse / evolve `git/Hardware/tools/LibraryManager.py` — it is
  already a working Qt Library Manager. Fastest path is to extend it, not restart.
- Import `hwkit` directly for logic (library import/audit/dedupe/tree, git ops,
  and later switch_engine / cubemx builder). No FastAPI, no HTTP, no React.
- Keep it Windows-native (PySide6/PyQt on system Python 3.14).

## What to KEEP from the previous build (do not throw away)

`app/backend/hwkit/` is UI-agnostic Python and is worth keeping — 49 tests pass:
- `pins/switch_engine.py` — canonical switch engine, verified LQFP64 = 11.
- `cubemx/builder.py` — builds the STM32 DB from CubeMX XML (424 MCUs, all 7 LQFP
  packages match hand-checked truth). Source XML committed at
  `app/backend/cubemx_db/mcu` (427 files).
- `pins/matrix.py`, `pins/authority.py`, `pins/switch_report.py`.
- `library/` — KiCad lib import (fixes footprint nickname + missing 3D model),
  audit, dedupe, tree scan, footprint render. `git_ops.py` — git panel logic.
- `netdeck/netclasses.py` — netclass read/write/apply.

## What to DROP

- The Tauri shell (`app/frontend/src-tauri`), the React frontend
  (`app/frontend`), and the FastAPI layer (`hwkit/api/app.py`) — the whole web UI.
- The vendored `app/backend/stm32switch/` package (old generator; unused; delete
  when the schema is redesigned).

## Feature contract

`app/PARITY.md` catalogs 214 features across the old apps. For this rebuild the
**LibraryManager section (111 items)** is the priority contract. Verify each
against `tools/LibraryManager.py` — do not approximate. STMP sections come later.

## Mistakes to NOT repeat

- Don't build the UI in HTML/web again and try to make it "look native." Use Qt.
- Don't claim a feature is done without diffing it against `LibraryManager.py`.
- Don't shape the (future) pin schema around the old STM-Helper tables.
- Don't invent UI elements the reference lacks (stat-cards, colored badges,
  banners, a "Database ready" chip) — Sadad calls these AI slop.

## First steps for the new session

1. Read `tools/LibraryManager.py` end to end — it IS the app to match/extend.
   Get a screenshot of it running from Sadad as the visual ground truth.
2. Research (web) native Qt/desktop UI best practices + look at good repos; use
   the frontend/design skills and any MCPs available.
3. Decide: extend `LibraryManager.py` in place, or start a clean PySide6 app that
   imports `hwkit`. Recommend extending the existing working app.
4. Bring the Library Manager to full feature parity (PARITY.md, LibraryManager
   section), native look. Show Sadad before moving to STMP/pin features.

## Key paths

- **Target app / reference: `git/Hardware/tools/LibraryManager.py`** (PyQt5; QSS
  ~L2611, layout ~L1461). Also `tools/fp_render.py`, `tools/kicad_tools.py`,
  `tools/nd_*.py`, `tools/merge_symbols.py` — the real feature implementations.
- Shared logic to reuse: `app/backend/hwkit/` (Python; no UI deps).
- Feature contract: `app/PARITY.md` (LibraryManager section first).
- Old STMP (lowest priority): `git/STMP/`.
- To delete on schema redesign: `app/backend/stm32switch/`.
