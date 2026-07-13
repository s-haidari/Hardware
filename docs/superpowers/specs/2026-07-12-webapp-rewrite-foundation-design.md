# Web-app rewrite — Foundation design (Components + Projects first)

**Date:** 2026-07-12
**Status:** Approved (brainstorm) — pending spec review → implementation plan
**Scope of THIS spec:** the Foundation sub-project (repo scaffold, backend API skeleton,
Electron shell, frontend design system, one end-to-end vertical slice). Components and
Projects each get their own spec + plan after this.

## Goal

Replace the PyQt5 desktop UI with a **local, desktop-packaged web app** — Electron shell +
Python/FastAPI backend + React/Tailwind frontend — reusing the existing Python business logic
unchanged. First pass covers only the **Components** (formerly Library) and **Projects**
features; Bench, Git, Settings are deferred. The updater is removed.

The win: the mockups (`library-v2.html`, `projects.html`) are already web, so React can match
them faithfully — finally closing the "looks nothing like the mockup" gap the PyQt port never did.

## Non-goals (this pass)

- Bench, Git, Settings features (stay in the old PyQt app until ported later).
- Any hosted/multi-user/cloud service — this is a **local** tool with full local FS/git/KiCad
  access, same as today.
- An in-app updater (removed; a web/Electron app is always the served build).
- Rewriting business logic. `LibraryManager.py` and the `nd_*` modules are reused as-is.

## Runtime model (decided)

Local, packaged as a **desktop app via Electron**:

```
Electron main process
  ├─ spawns the Python FastAPI sidecar (uvicorn on 127.0.0.1:<free port>, chosen at launch)
  ├─ creates a BrowserWindow that loads the built React app
  ├─ passes the sidecar URL to the renderer (preload → window.__API_BASE__)
  └─ on quit / window-all-closed → terminates the sidecar
FastAPI sidecar (Python)
  ├─ imports and CALLS the existing tools/*.py logic directly (no rewrite)
  ├─ same cfg / config.json handling as today (local machine, local API keys)
  └─ bound to localhost only; a per-launch token guards the API (defense-in-depth)
React + Tailwind renderer
  └─ talks to the sidecar over HTTP + SSE
```

Dev mode: the Vite dev server + `uvicorn --reload` run separately; Electron loads the Vite URL.
Prod: Vite builds static assets; Electron loads them from disk; the sidecar is a PyInstaller
one-file exe (reuses the existing frozen-build know-how) that Electron spawns.

## Architecture — the reuse story

The existing Python is UI-agnostic: functions take a `cfg` dict (+ args) and return
dicts/lists/images. FastAPI handlers are a thin veneer:

```python
# backend/app/routers/components.py  (illustrative)
@router.get("/parts")
def parts(cfg=Depends(load_cfg)):
    return LM.scan_library_grouped(cfg)          # existing function, returns rows
```

Previews already render to images via `fp_render` → served as `image/png` responses. This is
why the backend is a **veneer, not a rewrite**: the surface area is API plumbing, not logic.

## Repo layout (new; nothing deleted yet)

```
webapp/
  backend/
    app/
      main.py            FastAPI app, localhost CORS, token guard, mounts routers
      deps.py            load_cfg / auth-token dependencies (wraps LM.load_config)
      jobs.py            in-process job runner + SSE progress for long ops
      routers/
        components.py    parts list, part detail, previews, mutations
        projects.py      discovery, readiness/health, prepare/restore, BOM, exports
    tests/               pytest + httpx (API); the existing tools/ unit tests stay put
    pyproject / requirements
  frontend/
    src/
      app/               shell (nav: Components, Projects), router, providers
      lib/               api client (fetch + SSE), TanStack Query hooks, types
      design/            tokens + primitives ported from the mockups (Tailwind config)
      features/
        components/      the Components page (ported from library-v2.html)
        projects/        the Projects page (ported from projects.html)
    vite.config, tailwind.config, tsconfig, package.json
    tests/               Vitest (unit) + Playwright (e2e drive + screenshots)
  electron/
    main.js              spawn sidecar (free port + token), create window, lifecycle
    preload.js           expose API base + token to the renderer safely
    package.json         electron-builder packaging
```

The existing `tools/ui/**` PyQt app stays working (and is the app that ships) until the web app
reaches parity on Components + Projects, then it retires.

## Backend API surface

Long-running ops (enrich, import-zip, prepare, BOM build) run through `jobs.py`: `POST` starts a
job → `{job_id}`; the client subscribes to `GET /jobs/{id}/events` (SSE) for progress + result.
Quick reads/mutations are plain synchronous REST.

**Components** (wraps `LibraryManager` + `library_preview` logic):
- `GET /components/parts` — the grouped parts list (clean rows: name over part number).
- `GET /components/part/{name}` — detail: completion passport, sourcing state, files, fields.
- `GET /components/part/{name}/{symbol|footprint|model}.png` — preview images (fp_render).
- `POST /components/part/{name}/{complete|enrich|delete|rename|duplicate|link|...}` — mutations.
- `POST /components/import-zip`, `POST /components/dedupe`, `POST /components/make-portable`.
- `GET /components/status`, `GET /components/health`.

**Projects** (wraps `nd_project_health`, `nd_library_fill`, BOM, git):
- `GET /projects` — **one correct discovery endpoint** (fixes "doesn't find all projects" — a
  single, tested `discover_kicad_projects` call over the right roots).
- `GET /projects/{id}/readiness`, `GET /projects/{id}/health`.
- `POST /projects/{id}/prepare`, `POST /projects/{id}/restore`.
- `GET /projects/{id}/bom` (build + price), `GET /projects/{id}/bom/export/{fmt}`.
- Projects **Editor** (design rules / net classes / meta) — included in the Projects sub-project
  but sequenced LAST within it.

## Frontend

Vite + React + TypeScript + Tailwind. **TanStack Query** for all server state (caching,
invalidation on mutations, the natural fit for the app's read-then-act flows). **React Router**
for `/components` and `/projects`. The design system (`frontend/src/design/`) ports the mockups'
tokens (colors, spacing, radii, the DM Sans type scale) into `tailwind.config` + a small set of
primitives (Card, Button, Eyebrow, Badge, etc.) so feature code matches the mockups by
construction. SSE hooks surface long-op progress inline (the in-app "activity" equivalent).

## Testing (replaces drive_audit / render_gate for these features)

- **Backend:** pytest + httpx `TestClient` against the API on a fixture library/project; the
  existing `LibraryManager` / `nd_*` unit tests stay green (logic, not UI).
- **Frontend unit:** Vitest for hooks/components.
- **Frontend e2e:** Playwright drives the real app against a fixture backend (the new
  drive-audit) and takes screenshots (the new render gate) — asserted against the mockups.
- CI: add a web job; the existing Windows pytest gate stays for the backend logic. The fast
  local parallel test loop (`tools/win`) continues to cover the reused Python.

## Coexistence & cutover

- The web app is built entirely under `webapp/`; the PyQt app is untouched and remains the
  shipped app during the port.
- Cut over per-feature only when the web Components/Projects reach parity + pass their gates.
- The **updater is not ported**: no `check_for_updates`, no Settings update UI in the web app.

## Program decomposition (each its own spec → plan → build)

1. **Foundation (this spec):** scaffold `webapp/`, FastAPI skeleton reusing `cfg`, Electron
   shell + sidecar spawn + lifecycle, React/Tailwind design system from the mockup tokens,
   TanStack Query + SSE plumbing, and **one vertical slice end-to-end** — the parts list
   (`GET /components/parts` → rendered list matching the mockup) — to prove the whole stack.
2. **Components feature:** the full Components page from `library-v2.html`, then the refinements
   already requested (wider + transparent symbol/footprint previews, hidden filter behind the
   funnel, clean rows, mockup drop zone, component-field order, icon-only buttons, important
   text areas as cards).
3. **Projects feature:** the full Projects page from `projects.html` (Overview/readiness,
   Health/Prepare, BOM & procurement), the project-discovery fix, then the Editor last.

## Success criteria (Foundation)

- `webapp/` scaffolded; `npm run dev` (frontend) + `uvicorn` (backend) + Electron all launch and
  the window shows the parts list pulled live from the reused `LibraryManager` logic.
- The parts-list slice matches the mockup's row treatment; both themes render.
- Backend API tests + a Playwright smoke pass; existing Python unit tests stay green.
- A production build packages an Electron app that spawns the PyInstaller sidecar and runs
  offline with no updater.
