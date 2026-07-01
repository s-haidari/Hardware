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

2. **Rebuild the UI to look like the native PyQt Library Manager — not a web
   app.** Sadad: "I want it to look like the library manager... this UI you can
   tell it's a web UI." Research online (design systems, real GitHub repos,
   Tauri/Electron apps that look native) how to make a web/Tauri UI feel like a
   desktop application and avoid the AI-slop web look, BEFORE building. The tell
   right now is that it reads as a web page; kill that (see the section below).

3. **Full feature parity — Library Manager FIRST.** Start with the Library
   Manager and give it EVERY feature from `git/Hardware/tools/LibraryManager.py`,
   exactly as it works there. STMP features are secondary ("STMP was ass"). The
   `app/PARITY.md` LibraryManager section (111 items) is the contract for this
   tab; verify each against the actual source, do not approximate.

## The reference UI and the real problem

The target look is the **PyQt5 KiCad Library Manager**:
`git/Hardware/tools/LibraryManager.py` (its `_THEME_QSS` around line 2611 is the
exact stylesheet — palette, 8pt type, flat widgets, 3-column splitter, status
bar). Ignore the earlier note in git history that pointed at the STMP web app —
Sadad's words: "I want it to look like the library manager and have all the
features of it over the STMP; STMP was ass to begin with." STMP is deprioritized.

**The real problem:** "this UI you can tell it's a web UI." The previous session
matched the QSS *tokens* (colors, fonts, sizes — verified via computed styles)
but the result still reads as a web app, not a native desktop tool. Matching
hex values is not enough. The rebuild has to kill the web-app tells and make it
feel native:
- Density and spacing of a desktop app, not roomy web margins.
- Native-feeling widgets: a real tree/table with header sections and row
  striping, a QSplitter-style 3-column layout with drag handles, a real menu bar
  / toolbar, a proper bottom status bar with a progress area — not floating
  rounded "cards" with web hover effects.
- No web-isms: no big rounded card shadows, no bouncy transitions, no oversized
  touch-target buttons, no emoji/badge decoration, no gradients.

**First action: open `LibraryManager.py`, run the real app if possible (or read
its layout code + QSS), and rebuild the web UI to look like that native window.**
Get a screenshot of the running PyQt app from Sadad if you can — it's the ground
truth for "does this look native or like a web page."

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

- The reference is `tools/LibraryManager.py` (PyQt5). Matching its QSS tokens is
  NOT enough — the last build did that and still "looks like a web UI." Match the
  native feel (density, widgets, no web-isms), not just the colors.
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

1. **Rebuild the Library Manager tab first**, to look like the native PyQt app
   in `git/Hardware/tools/LibraryManager.py` and to have ALL of its features.
   Get a screenshot of the running PyQt app from Sadad as ground truth.
2. **Research** (web) how to make a Tauri/web UI look like a native desktop app
   (density, native widgets, no web-isms) — real repos/design systems, not
   generic advice. Use the frontend-design skill and the preview MCP if present.
3. Only after the Library Manager is right: the other tabs, then the ground-up
   schema redesign (document it; port `builder` + `switch_engine`; delete
   `stm32switch`). STMP features are lowest priority.

## Key paths

- `app/PARITY.md` — the 214-feature contract (the source of truth for "exactly
  how it was").
- `app/backend/hwkit/` — backend (`api/app.py`, `pins/switch_engine.py`,
  `cubemx/builder.py`, `library/`, `netdeck/`).
- `app/backend/stm32switch/` — OLD generator, now unused → **delete in rebuild**.
- `app/frontend/src/App.tsx` + `App.css` — current UI (rebuild).
- **Visual + feature reference (the target): `git/Hardware/tools/LibraryManager.py`**
  (PyQt5). QSS is `_THEME_QSS` ~line 2611; layout build is ~line 1461+. Make the
  web UI look like this native app and carry all its features.
- STMP (lowest priority): `git/STMP/` (`stm_helper`, `stm32switch`, `web/`) for
  the pinout features only — deprioritized.
- Recent commits on `git/Hardware` `main` document the app-owned-DB fix and the
  UI passes; `git log` there for detail.
