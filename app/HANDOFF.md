# Handoff — Hardware App, GROUND-UP REBUILD

Date: 2026-06-30
Author: previous session (ran long; Sadad is restarting fresh)

## Read this first — the pivot

Sadad wants to **rebuild this app from the ground up** in a fresh session. The
current build has real backend logic worth keeping, but the direction changed.
Three mandates, in his words:

1. **New schema from scratch.** "Stop comparing yourself to the old schema.
   Derive an entire new schema from the ground up." Do NOT reference or depend
   on the old STM-Helper / STMP SQLite schema at all. Design the pin database
   from first principles for THIS app. The vendored `app/backend/stm32switch/`
   package is the old generator built around a 16-table STM-Helper schema — it
   is now unused by the app and should be **deleted**, not ported.

2. **Entire UI rebuild, and make it not look like AI slop.** "The UI looks
   nothing like the PySide6 UI." Also: "look online on how to [build a] Claude
   Code UI without making it look like AI slop." So: research current
   desktop/Tauri UI best practices on the web BEFORE building — real typography,
   density, spacing, component patterns — then rebuild the whole UI.

3. **Exact, full feature parity, every tab.** "Every single tab needs to have
   all the features from the old apps, every single one. Exactly how it was.
   I see a lot of differences in your features." `app/PARITY.md` catalogs 214
   features (union of the four old apps). Treat it as a contract; verify each
   feature against the actual old source code, do not approximate.

## The reference UI — FOUND (this is why the current build looks wrong)

Sadad said "PySide6," but there is **no PySide6 anywhere** in either repo
(grep confirmed). The previous session matched the wrong thing: it styled the
app after `tools/LibraryManager.py`, which is the **PyQt5** KiCad library
manager. That is NOT the look Sadad wants.

The real reference is the **STMP React web app**:
- **`git/STMP/web/src/App.tsx` + `git/STMP/web/src/App.css` + `index.css`** —
  a Vite/React front end. This is the STMP pinout / switch-fabric tool's UI.
- It is served in a desktop window by `git/STMP/src/stm_helper/desktop.py` via
  **pywebview / WebView2** (a web UI in a native shell — that's the "app" Sadad
  is picturing). `stm_helper/ui/` holds the bundled/served assets.

**First action: open `git/STMP/web/src/App.css` and `App.tsx` and match THAT
design** (type, spacing, layout, components). Confirm with Sadad and get a
screenshot of the running STMP web app if possible. The whole reason the last
build "looked nothing like it" is it copied the PyQt manager instead of this
React web UI.

## What the project is

- Merged app = KiCad **LibraryManager** (PyQt5, `git/Hardware/tools/LibraryManager.py`)
  + **STMP** STM32 pinout / switch-fabric tool (`git/STMP`, package `stm_helper`
  + `stm32switch`).
- Lives in **`git/Hardware/app`**. Stack: Tauri (Rust shell) + Python FastAPI
  backend (`app/backend/hwkit`) + React/Vite frontend (`app/frontend`).
- Run as a script: `app/run-app.bat` or `app/serve.py` (builds UI, serves UI+API
  on `127.0.0.1:8799`, opens browser). Dev: vite on `5173` proxies `/api` → 8799.
- Toolchain: system Python 3.14 (venv `app/backend/.venv`), Node 24, Rust/MSVC.
  Constraint: use system Python, NOT KiCad's bundled Python.

## Current state (end of this session)

**Backend (`app/backend/hwkit`) — solid, keep the logic, 49 pytest tests pass:**
- `pins/switch_engine.py` — THE canonical switch-fabric engine. A pin needs an
  ADG714 switch when it takes ≥2 routing identities across the family. Verified:
  LQFP64 = 11 must-switch (the project ground truth). Keep this.
- `cubemx/builder.py` — builds the SQLite DB from CubeMX MCU XML. 424 MCUs, all
  7 LQFP packages match hand-checked truth.
- `pins/matrix.py` — per-pin matrix + validation, rewritten to derive from
  `switch_engine` (no stm32switch dependency).
- `pins/authority.py`, `pins/switch_report.py` — authority files + CSV/MD/HTML.
- `library/` — KiCad lib import (fixes footprint nickname + 3D model), audit,
  dedupe, tree scan, footprint SVG. `git_ops.py` — git panel.
- `netdeck/netclasses.py` — netclass read/write/apply.
- DB is **app-owned**: `app/backend/data/stm32.sqlite` (gitignored, auto-builds
  on startup from the committed XML at `app/backend/cubemx_db/mcu`, 427 files).

**Current schema (PROVISIONAL — Sadad wants it redesigned ground-up):**
`mcu`, `mcu_package_pin`, `pin_function`, `pin_role`. It reproduces the engine's
ground truth but was shaped incrementally. Redesign it deliberately.

**Frontend (`app/frontend/src/App.tsx` + `App.css`) — REBUILD:**
- One big `App.tsx`, 4 nav tabs: Manager (library, 3-col), Pins (pinout map /
  switch cells / matrix + reports + validation), Netclasses, Database.
- Was styled to LibraryManager's PyQt QSS (dark palette, 8pt scale, flat). Sadad
  says it still looks nothing like the reference and reads as AI slop.

**Parity: 77/214 in `app/PARITY.md`.** Many "done" items likely differ from the
originals — re-verify every one against the real old source.

## Mistakes to NOT repeat

- Don't match `tools/LibraryManager.py` blindly — confirm the real reference UI
  first (Sadad says PySide6, and I never found a PySide6 file).
- Don't shape the schema around the old STM-Helper tables. Design fresh, delete
  `app/backend/stm32switch/`.
- Don't invent UI elements the reference doesn't have (stat-cards with big
  numbers + icons, colored type badges, pill tags, banners, a "Database ready"
  chip) — Sadad specifically calls these AI slop.
- Don't claim a feature is done without diffing it against the actual old code.
- Screenshot capture via the preview MCP **times out** in this environment. Use
  `preview_eval` computed-style inspection to verify UI, or ask Sadad to look.
  (Preview loop that worked: vite dev on 5173, `.claude/launch.json` in the
  vault runs `npm --prefix <frontend> run dev`; `vite.config.ts` has
  `server.fs.strict:false` + `/api` proxy to 8799.)

## Suggested first steps for the new session

1. **Study the real reference UI**: `git/STMP/web/src/App.css` + `App.tsx`
   (the STMP React web app). Match its design language. Confirm with Sadad +
   screenshot the running STMP web app.
2. **Research** (web) polished desktop/Tauri UI patterns that avoid the AI-slop
   look — real type scales, spacing, component libraries, density.
3. **Design the new pin-DB schema** from first principles; document it; port
   `builder` + `switch_engine` onto it; delete `stm32switch`.
4. **Rebuild the UI tab by tab**, implementing EVERY `PARITY.md` feature exactly
   as the old apps had it; verify each against source.

## Key paths

- `app/PARITY.md` — the 214-feature contract (the source of truth for "exactly
  how it was").
- `app/backend/hwkit/` — backend (`api/app.py`, `pins/switch_engine.py`,
  `cubemx/builder.py`, `library/`, `netdeck/`).
- `app/backend/stm32switch/` — OLD generator, now unused → **delete in rebuild**.
- `app/frontend/src/App.tsx` + `App.css` — current UI (rebuild).
- **Visual reference (the target look): `git/STMP/web/src/App.css` + `App.tsx`**
  (STMP React web UI, served via `stm_helper/desktop.py` pywebview).
- Feature reference: `git/Hardware/tools/LibraryManager.py` (PyQt5 library
  manager) for the Manager/library features; `git/STMP/` (`stm_helper`,
  `stm32switch`, `web/`) for the STMP pinout features.
- Recent commits on `git/Hardware` `main` document the app-owned-DB fix and the
  UI passes; `git log` there for detail.
