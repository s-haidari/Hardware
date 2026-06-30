# Hardware app — unified backend (`hwkit`)

Full rewrite that folds `git/STMP` + the existing `git/Hardware` library manager
into one local desktop app. Decisions (brainstormed 2026-06-30):

- **Goal:** consolidation — one app to build, run, maintain.
- **Shape:** Tauri (Rust shell) + Python/FastAPI sidecar + React (Vite) webview.
  The shell is thin (window, sidecar lifecycle, native pickers, "open in KiCad",
  updater); all logic stays Python so both codebases are *ported*, not rewritten
  in a new language.
- **Home:** this repo (`git/Hardware`). Old `tools/*.py` stay for reference
  during the port.

## Backend modules (`hwkit/`)

| Module | Responsibility | Ported from |
| --- | --- | --- |
| `core/` | per-machine config (auto-detect repo/KiCad/`${MY3DMODELS}`), jobs, logging | new + LibraryManager |
| `kicad/` | KiCad-file primitives: `.kicad_sym` merge, `.kicad_mod` `(model …)`, SVG preview, lib-table nicknames | LibraryManager, fp_render, kicad_tools, merge_symbols |
| `library/` | part ingest (ZIP/easyeda) → **importer that fixes Footprint + model** → catalog | LibraryManager |
| `netdeck/` | netclass / project-settings / wizard services | nd_* tools |
| `pins/` | STMP folded in: switch_engine, pinout authority, matrices, card-lint | stm_helper + stm32switch + web_api |
| `api/` | FastAPI routers + schemas + WebSocket log/progress | new |

## Requirement #1 — importer correctness (fixes the live bug)

Today the manager merges parts but leaves symbols pointing at the wrong footprint
library nickname (51/113 bare, the rest per-part) and drops the footprint
`(model …)` line (92/93 have none). So placed symbols resolve no footprint and no
3D model. `import_part` MUST guarantee, every part:

1. symbol `Footprint` == `MyFootprints:<footprintName>` (never bare/per-part);
2. footprint has one valid `(model "${MY3DMODELS}/<file>.step" …)` line;
3. `MySymbols`/`MyFootprints` registered in the lib-table, `${MY3DMODELS}` defined.

The `kicad/` primitives below are the tested core of that guarantee.

## Build order

1. **`kicad/symbols.py` + `kicad/footprints.py`** — correctness primitives (done, tested).
2. `library/importer.py` — ZIP/folder ingest using the primitives, with a real-fixture test.
3. `kicad/render.py`, `kicad/libtable.py` — previews + lib-table registration.
4. `pins/` — move STMP wholesale (already tested).
5. `netdeck/` — port nd_* tools to services.
6. `api/` — FastAPI wiring + WebSocket.
7. React frontend (reuse STMP `web/`), then Tauri shell (needs Rust toolchain).

## Running tests

```
cd app/backend && python -m unittest discover -s tests
```

Pure stdlib; no FastAPI/Rust needed for the core modules.
