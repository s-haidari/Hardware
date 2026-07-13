# Perfected rewrite direction (reconciled)

**Date:** 2026-07-12
**Status:** Design approved (brainstorm); pending owner spec review then implementation plans.
**Supersedes:** `docs/superpowers/specs/2026-07-12-webapp-rewrite-foundation-design.md` (the Electron
web-app Foundation spec). This document reconciles that spec with the Stockroom clean-room effort
(`~/git/stockroom`, summarized in `Stockroom-Knowledge-Transfer.md`) into one authoritative direction.
**Related detailed specs (kept):** the Stockroom design spec and per-milestone plans remain the
detailed engine specs; this document is the top-level direction they slot into.

No em dashes anywhere (standing owner rule for rewrite output).

## 1. Why this document

On 2026-07-12 two rewrite efforts ran in parallel: an Electron web-app Foundation spec in this repo
(reuse the old PyQt Python unchanged, Components plus Projects, coexist with PyQt), and Stockroom, a
clean-room rewrite in its own repo (pywebview/WebView2, byte-preserving KiCad layer, git-synced
library-as-repo, Components-only, already shipping M1 with M2 landing). They conflicted on nearly
every load-bearing axis. The owner directed a best-of-both synthesis, full Components plus Projects
scope with no capability regression, consolidated into one repo. This document is that synthesis.

## 2. The governing invariant (what keeps this from becoming a Frankenstein)

Every decision below hangs off one statable rule:

> **Clean-room owns every WRITE. Reused logic is READ and COMPUTE only.**
> The clean-room engine (byte-preserving KiCad file layer, library data model, git, mutation engine)
> is the only thing that writes KiCad files, part records, or commits. The mature old logic is reused
> only for analysis and field production (enrichment, BOM, project health, readiness). Any reused
> function that today writes a KiCad file is re-routed through the clean-room writer.

This is a boundary, not a tangle. Clean-room owns writing (where byte-preservation and git-cleanliness
must hold); reuse owns reading and computing (where the old logic is battle-tested and rebuilding is
pure waste). The one integration discipline is that a reused Projects mutation (design rules, net
classes, board setup) writes through the clean-room span layer, never the old reformatting writer.

## 3. Per-axis synthesis decisions

| Axis | Decision | Rationale |
|---|---|---|
| KiCad file surgery | **Clean-room** (Stockroom span-preserving s-expression layer) | Byte-preservation is the whole game for a git-synced library; every off-the-shelf lib was disqualifying and the old writer reformats on write. Verified, already shipped as M1. |
| Library model, profiles, git sync, mutation engine | **Clean-room** (Stockroom) | Git-native source of truth; already built and tested in M2. |
| Ingestion (zip fingerprint, legacy upgrade, 3D relink) | **Clean-room** (Stockroom M3) | Content-fingerprint identity is correct; the old app has no equivalent. |
| **Shell** | **WebView2 via pywebview, NOT Electron** | Target is Windows-only and all logic is Python, so Electron's cross-platform and Node-main-process buy nothing while costing a second runtime plus a bundled Chromium. WebView2: Python is the host process, native drag/drop delivers full zip paths to Python, and the same engine is the scraping fallback for bot-protected sites. This is the one axis where the Electron spec is overruled. |
| Frontend | **Vite + React + TypeScript + Tailwind + TanStack Query + cmdk + three.js** (from the Electron spec) | Both specs agree on React/Tailwind; this ports the existing web mockups faithfully and closes the "looks nothing like the mockup" gap the PyQt port never did. |
| Backend API | **FastAPI thin veneer** (both specs agree) | The surface is API plumbing, not logic. |
| **Enrichment** | **Reuse** the old Mouser client, rate-limit/SRC-04 handling, and parser, and fold in Stockroom's generic URL parser, WebView2 rendered-DOM fallback, and datasheet fetcher | Best of both: proven Mouser code plus the scraper that makes completeness achievable without the API cap (see section 6). |
| **BOM and procurement** | **Reuse** the LibraryManager BOM subsystem (~20 functions: `bom_cost_summary`, `bom_from_project`, `consolidated_bom`, `bom_diff`, `bom_procurement_summary`, `bom_xlsx`, and so on) | Mature pure logic; rebuilding is waste. Decouple from the module-level Qt import. |
| **Projects (health, editor, net classes, design rules, board setup, fab/design presets)** | **Reuse** the Qt-free `nd_*` modules, routing their KiCad writes through the clean-room layer | The entire Projects capability already exists and is Qt-free; only its writes need re-plumbing. |
| Previews | **Replace** `fp_render` (33 lines of Qt) with kicad-cli SVG plus three.js/GLB | Keeps the backend Qt-free and always matches the installed KiCad. |
| Update model | **git-pull ff-only self-update, frozen-once launcher, library-as-repo** | Ships code, UI, and library data in one pull; fits the git-native design. (Confirm in section 12.) |

## 4. Architecture: the layered stack

```
Layer 6  Frontend SPA        React + TS + Tailwind + TanStack Query + cmdk + three.js
Layer 5  Host + API          pywebview WebView2 window  +  FastAPI thin veneer  +  frozen-once launcher
Layer 4  Projects (reuse)    nd_* modules: health, editor, net classes, design rules, board setup, BOM
Layer 3  Enrichment (reuse+) old Mouser client  +  generic scraper  +  WebView2 DOM fallback  +  datasheet fetcher
Layer 2  Ingestion (clean)   content-fingerprint zip adapters, legacy upgrade, 3D relink, staging
Layer 1  Library core (clean) data model + profiles + git sync + atomic mutation engine + derived SQLite index
Layer 0  KiCad file surgery  (clean) byte-preserving s-expression layer + semantic-diff gate + kicad-cli wrapper
```

Writes flow down through Layer 0 (the only KiCad-file writer) and the Layer 1 mutation engine (the only
committer). Reads and computation (Layers 3 and 4) sit on top of the Layer 1 source of truth. The host
(Layer 5) is presentation plus the scraping engine; the frontend (Layer 6) is pure presentation.

## 5. Data layer (source of truth, derived index, settings, portability)

### 5.1 Source of truth stays text
Per-part JSON records plus the KiCad files (per-category `.kicad_sym`, `.pretty` dirs, `models/`,
`datasheets/`) are the git-synced, byte-preserving, per-line-mergeable source of truth. A binary
database as source of truth would destroy per-line history and invite whole-DB merge conflicts on every
sync, so it is rejected for the source of truth.

### 5.2 Derived SQLite index (the "proper database")
A SQLite database is built and refreshed on load and after every git pull, as the fast authoritative
query layer: full-text search, faceted filtering, duplicate detection, BOM joins, and the completion
passport rollup. It is a rebuildable cache derived from the text source and is never committed (it lives
under per-machine state, section 5.4). If it is deleted or corrupted, it rebuilds from the files. This
gives a real database for querying while keeping git clean.

### 5.3 Settings, split by portability
- **Portable shared settings** (fab presets, design-rule and net-class standards, the category taxonomy):
  git-synced as text in the repo and indexed in the SQLite index, so they resolve identically for a
  project on any machine. These are managed by the reused `nd_fab_presets` / `nd_design_presets` /
  `nd_netclass_manager` / `nd_pcb_profiles` logic.
- **Per-machine config** (KiCad install path, API keys, window state, active profile, sync preferences):
  lives outside the repo in per-machine state. Nothing machine-specific or secret ever enters the repo.

### 5.4 Path-portability resolver (what actually delivers the goal)
The requirement is that a project's parts resolve to the identical symbol, footprint, and 3D model on
any machine, independent of filesystem layout or the user's KiCad configuration. The mechanism:

- The library is carried inside the repo, so a clone gets the identical files everywhere.
- KiCad resolves every part through **one environment variable** (`SR_LIB` or the chosen name) plus stable
  `SR-` nicknames. Symbol rows point at `${SR_LIB}/symbols/<category>.kicad_sym`, footprint rows at
  `${SR_LIB}/footprints/<category>.pretty` directories, and 3D links are written as `${SR_LIB}/models/...`.
- Switching profile or machine is a one-variable flip plus a lib-table refresh; every `lib_id` reference in
  a project resolves identically.
- The lib-table and `kicad_common.json` writers are **scoped** (touch only `SR-` rows and the `SR_LIB` var,
  never the user's other libraries or the V10 `(type "Table")` row), **idempotent**, **safe** (timestamped
  backup, parse-validate after write), and **aware** (detect a running KiCad, report a restart is needed).

Note: even KiCad's native database-library feature would not solve footprint and 3D portability by itself
(those still resolve by path), so this resolver is required regardless of how metadata is stored. That is
why the derived-index model wins over native `.kicad_dbl` (which would also add an ODBC-driver setup burden
that cuts against "works regardless of their KiCad setup").

## 6. Completeness contract (the complete-to-add gate)

A component may not enter the primary library unless it is **complete**. Completeness is defined by the
**completion passport** (the app already has a strict multi-item passport: identity fields, symbol,
footprint, 3D model, datasheet, purchase link, manufacturer, MPN, category). The exact required-field set
is finalized from that passport during M2 and enforced in one place:

- The atomic add operation in the mutation engine **rejects** an add whose passport is not 100 percent, with
  an honest per-field report of what is missing (never a silent partial add).
- The add flow drives enrichment (section 6.1) to fill the passport before the gate is evaluated, so the
  gate is achievable rather than a wall.
- Editing an existing part toward completeness is always allowed; the gate is on entry to the primary
  library, not on working toward it in staging.

### 6.1 Scraper-first enrichment (reduce dependence on the limited API key)
So that completeness does not bottleneck on the Mouser API cap (1000/day, 30/min), the enrichment cascade
is scraper-first, with the API as one source among several rather than the sole path:

1. **Purchase-link scrape (primary):** given a pasted purchase URL, fetch with a Chrome-impersonating client,
   then cascade schema.org JSON-LD Product markup, OpenGraph/meta, per-site extractors, and embedded JS state
   to extract MPN, manufacturer, description, price breaks, and the datasheet link. No API key consumed.
2. **Datasheet-link scrape (primary):** follow the datasheet URL (real User-Agent, Referer set to the source
   page, HTTP/1.1 retry when HTTP/2 fails), validate by `Content-Type` plus `%PDF-` magic bytes (reject HTML
   viewer wrappers), store the PDF, and extract specs from it.
3. **WebView2 rendered-DOM fallback:** for bot-protected sites (Cloudflare, Akamai) that defeat any HTTP
   client, load the page in the real WebView2 engine the app already hosts and read the rendered DOM. User
   initiated, one page at a time, never a crawler. This is the concrete payoff of the WebView2 shell decision.
4. **Mouser API (supplement):** used when a key is present and helpful, not as the required path. The existing
   rate-limit handling and SRC-04 cap countdown are reused so a capped key degrades honestly to the scraper.

Enrichment never silently overwrites a filled field; changes to existing values are per-field opt-in.

## 7. Library archive and migration of the current library

The current library may contain incomplete or messy parts. Rather than force-complete them all up front or
lose them, the current library is **archived** and the clean library starts complete-only:

- On first run against an existing library, snapshot and import the current parts into an **Archive profile**
  (the profile mechanism already exists: one folder per profile). The Archive profile is preserved intact and
  stays usable in existing projects; it is exempt from the complete-to-add gate (grandfathered).
- The **primary profile** is complete-only from day one: every new add passes the section 6 gate.
- Archived parts can be **completed and promoted** into the primary library later through the same enrichment
  plus gate flow (an explicit, supported path, not an automatic bulk migration).
- The archive is created through the byte-preserving layer and committed as one scoped commit, so nothing is
  reformatted and the import is fully reversible.

## 8. Scope and milestone roadmap (Components plus full Projects, zero regression)

| M | Scope | Status |
|---|---|---|
| M1 | Foundation: byte-preserving KiCad file core, semantic-diff gate, file models, kicad-cli wrapper | Shipped (Stockroom), migrates in |
| M2 | Library data model, profiles, git sync, KiCad wiring, atomic mutation engine, **derived SQLite index**, **complete-to-add gate**, **archive import** | In progress (Stockroom store/vcs green; add the index, gate, archive) |
| M3 | Ingestion: content-fingerprint zip adapters, legacy upgrade, 3D re-linking, staging, LCSC path | Planned |
| M4 | Enrichment: **scraper-first** (purchase and datasheet links), WebView2 DOM fallback, datasheet fetcher, Mouser API as supplement (reuse the old client) | Planned |
| M5 | Backend API + pywebview WebView2 shell + frozen-once launcher + git-pull self-update | Planned (needs Windows) |
| M6 | Frontend: Components UI from the web mockups, Ctrl+K palette, symbol/footprint/3D viewers, ingest, duplicates, settings, doctor | Planned |
| M7 | **Projects (full): audit, health, Editor (design rules / net classes / board setup), BOM, procurement, exports** (reuse the `nd_*` modules and the BOM subsystem, routing KiCad writes through Layer 0) | Planned |
| M8 | Retire the PyQt app per-feature at parity | Planned |

Each milestone produces working, testable software and is adversarially reviewed and merged before the next.
The chain has real dependencies (M3 calls M2's mutation engine, M6 calls M5's API, M7 reuses M4 plus M1),
so it is a pipeline, not a parallel build. Two things always need one pass on the real Windows machine with
KiCad 10: the launcher (M5) and the final KiCad wiring and preview verification.

## 9. Migration and consolidation approach

**Approach: engine-first (chosen over shell-first and dual-track).** Absorb Stockroom's shipped clean-room
core into this repo as the new engine, continue its milestones, and plug reuse in at the natural points:
M4 enrichment reuses the old Mouser and parsing logic, M7 Projects wraps the `nd_*` modules and the BOM
subsystem. This honors "byte-preservation is the whole game" (never build on the old reformatting engine)
and "reuse mature logic where good." Shell-first was rejected because it would build the app over the exact
engine being replaced then require a risky swap; dual-track was rejected because it is the two-conflicting-
directions problem this document ends.

**Consolidation:** the rewrite lives in this (Hardware) repo under one top-level directory with a single
history. Stockroom's shipped engine migrates in (`sexp/`, `verify/`, `kicad/`, `model/`, `store/`, `vcs/`,
`mutation/`), the FastAPI backend and React frontend follow the Electron spec's structure adapted to the
WebView2 host, and the reused `nd_*` and BOM logic is extracted from `tools/` and decoupled from Qt.

**Reuse mechanics (honest about coupling):** the `nd_*` project modules are Qt-free and reuse cleanly. The
BOM and enrichment logic lives in `LibraryManager.py`, whose Qt coupling is thin (a module-level import plus
one lazy import), so the pure functions extract cleanly once that import is removed. `fp_render.py` is
heavily Qt-coupled and is replaced by kicad-cli plus three.js rather than reused.

## 10. Coexistence and cutover

- The PyQt app (`tools/ui/**`) stays working and remains the shipped app until the rewrite reaches parity on a
  feature, then that feature cuts over. The v2.x PyQt line goes bug-fix-only; no new PyQt feature work.
- Cut over per-feature only when the rewrite surface reaches parity and passes its gates (section 11).
- The old in-app updater is not ported; the rewrite's update path is git-pull self-update (section 12).

## 11. Testing

- **Clean-room (Layers 0 to 2):** golden-file byte round-trip tests, the independent semantic-diff gate, and
  property-based tests for the s-expression layer. Real vendor zips as ingestion fixtures.
- **Data layer:** the derived index rebuilds deterministically from fixtures; the complete-to-add gate has
  tests proving an incomplete add is rejected and a complete add succeeds; archive import is round-trip tested.
- **Enrichment:** scraper tests against saved HTML fixtures, datasheet validation tests (magic bytes, HTML
  wrapper rejection), an opt-in live smoke suite. Rate-limit degradation to the scraper is tested.
- **API:** pytest plus httpx against a fixture library and project.
- **Frontend:** Vitest for hooks and components; **Playwright drives the real app (the new drive-audit) and
  screenshots it against the mockups (the new render gate)**.
- **Release gate:** Windows CI stays the gate; the reused `nd_*` and BOM logic keeps its existing unit tests.
  Claims always name the environment they rest on; Linux/offscreen green is necessary, never sufficient.

## 12. Open decisions (confirm at spec review)

1. **Product name:** keep **Stockroom** (already public on GitHub with releases, a strong product identity) or
   fold the rewrite under **KiCad Manager** (the v2.12 release name). Recommendation: keep Stockroom as the
   product name, with this repo as its home.
2. **Update model:** keep **git-pull ff-only self-update** (recommended, fits library-as-repo) or drop the
   updater entirely as the Electron spec did. Recommendation: keep it.
3. **Top-level directory name** for the consolidated rewrite (for example `app/`, `stockroom/`, or `webapp/`).
4. **Exact required-field set** for the complete-to-add gate, finalized from the existing passport during M2.
5. **Archived-part promotion UX:** confirm the complete-and-promote flow is user-initiated per part (recommended)
   rather than an automatic bulk migration.

## 13. Non-goals (this direction)

- No EDA tool other than KiCad, and no KiCad below the V10 target (V9-compatible files are preserved, never
  silently upgraded).
- No hosted, multi-user, or cloud service; this is a local tool with full local filesystem, git, and KiCad
  access. Sync is git; collaborators are trusted committers.
- No routing engine in this direction (the PyQt app's routing stays shelved as its own concern).
- No rewrite of the reused `nd_*` and BOM logic beyond decoupling it from Qt and re-routing its KiCad writes
  through Layer 0.
