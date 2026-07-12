# App Gripes — Triage & Prioritized Plan

**Status:** triaged 2026-07-08 · **Branch:** main · **App commit at intake:** `d5de6c8`
**Source:** user gripe brain-dump (`~/Documents/Obsidian/Brain/Agent/Hardware App Gripes.md`)
**Method:** code-grounded triage — 8 per-area agents verified every gripe against the real source
(evidence at `file:line`), plus 2 reference scouts (design contract, build-card/NETDECK netclass
grammar) and a completeness/dependency pass. 68 distinct gripes triaged.

---

## 0. TL;DR

- **68 gripes** across 8 areas, reconciled from the user's two passes (rough dump + structured template).
- **The single most important finding is not in any one gripe:** a large cluster of "bugs" are
  **already fixed in `main`** and only survive because the **updater is broken** (`SET-04` — it writes
  `*.exe.new` into Downloads and never replaces the running exe). The user is stranded on a stale build.
  `SRC-01`'s "Complete tab" *literally does not exist in the code anymore*. **So Wave 0 is: fix the
  updater, ship a fresh build, and re-confirm which gripes actually survive on latest.**
- The rest resolves into **11 shared workstreams** (WS-A…WS-K). Six of them are foundational engines
  (units, theme, Mouser, git-unify, data-table, netclass-profile) that many per-area gripes hang off —
  build those once in **Wave 1** and the **Wave 2** per-area rebuilds get much cheaper.
- **Wave plan:** W0 broken-core (5) → W1 foundations (11) → W2 per-area rebuilds (31) → W3 polish (19),
  plus a **deferred, decision-gated frameless-shell track** (2).
- **10 gripes are "verify on latest build"** (likely already done); **19 need a user decision** before
  building; **13 are quick-wins**.

---

## 1. Root-cause finding: the stale-build trap (Wave 0)

Several high-severity "bugs" were marked **not-reproduced** by the triage agents because the current
`main` source already does what the user asked. The pattern is too strong to be coincidence:

| Gripe | User says | Code on `main` says |
|-------|-----------|---------------------|
| `SRC-01` | Can't add MPNs unless on the **"Complete" tab**; adding crashed the app | There is **no Complete tab** — the Parts/Sourcing/Import triad was collapsed in Phase 3 (`e442125`). The crash path is gone. |
| `BENCH-07` | Package selection only affects Overview; Profile stuck on LQFP64 | `ws.rebuild_all` re-derives every sub-panel from the selected package; Profiles/Resolver are package-scoped. |
| `PCB-01` | Fabrication / netclasses / board setup should be one tab | Already **one** unified PCB Setup tab (Phase 4, `b7d1930`). |
| `PCB-06` / `PCB-08` | Sticky netclass header; add/edit/filter netclasses | Sticky header, filter, per-row add/delete already exist. |
| `PROJ-03` | Git should be its own tab | Git is **already its own top-level feature** (`order=40`). |
| `SET-01` | Settings icon is a **sun** | Code shows Settings = **gear**; the *theme toggle* is a crescent moon. |

**Why:** `SET-04`. The updater does not replace the running exe, so the user keeps launching an old
binary. Every "phantom" gripe above is a memory of code that has since changed.

**Wave 0 is therefore the unblocker for the entire triage:**

1. **Fix `SET-04`** — make the updater replace the running exe in place (the in-place batch-swap path
   exists in `nd_updater.py`; harden the frozen-Windows path and treat a failed swap as an error, not a
   passive "do it yourself" message) **and** add a nav-bar "update available" badge so the user knows.
2. **Ship a fresh build from current `main`** and have the user install it.
3. **Re-walk the 10 `verify-latest` gripes** on that build — most should close as already-done, and the
   ones that survive get re-triaged with a real repro.
4. While in the Settings surface, fix the two genuinely-dead controls that are independent of the build:
   **`SET-02`/`SHELL-04`** (the Appearance **Theme buttons have no `on_change` handler at all** — they
   are visually interactive but functionally dead; the *working* theme switch is the nav-footer button),
   and **`SET-03`** (delete the unwanted Selection-Accent control — a safe one-line removal).

> Wave 0 is small, low-risk, and mostly visible. It stops us from spending Wave 2 effort "fixing" bugs
> that no longer exist.

---

## 2. Shared workstreams (the backbone)

Most gripes are not independent — they share an engine. Build the engine once; the per-area items become
thin UI. This is the spine of the wave ordering.

| WS | Name | Feeds (gripe ids) | First wave |
|----|------|-------------------|-----------|
| WS-A | Units (mm/mils) | `LIB-14`, `PCB-02`, `SHELL-03` | W1 |
| WS-B | Mouser enrichment | `LIB-03`, `LIB-04`, `LIB-05`, `LIB-06`, `LIB-07`, `PROJ-09` | W2 (verify W1) |
| WS-C | Theme plumbing | `LIB-09`, `SET-02`, `SHELL-04`, `SHELL-06` | W0 |
| WS-D | Frameless shell | `SRC-02`, `SHELL-01` | Deferred |
| WS-E | Git unify + sync | `LIB-13`, `SRC-03`, `GIT-01`, `GIT-02`, `GIT-03` | W1 |
| WS-F | Data-table widget | `PROJ-07`, `PROJ-08`, `PCB-06`, `PCB-07` | W1 |
| WS-G | Netclass / profile engine | `PCB-03`, `PCB-04`, `PCB-05`, `PCB-09`, `PCB-10`, `PCB-12`, `PCB-13` | W1 |
| WS-H | Compact filters | `LIB-01`, `LIB-02`, `PCB-08` | W2 |
| WS-I | Bench legend / palette | `BENCH-01`, `BENCH-02`, `BENCH-03`, `BENCH-04` | W3 |
| WS-J | Bench connection-diagram | `BENCH-10`, `BENCH-11`, `BENCH-12` | W2 |
| WS-K | Health / Refactor | `PROJ-04`, `PROJ-05`, `PROJ-06` | W2 |

**The three cross-cutting prerequisites** that unlock the most:

- **A persisted app-config writer + an app-state object on `F.Context` + a theme/units bus topic.**
  Right now theme starts DARK every launch (never persisted), the mm/mils toggle is a panel-local dict,
  and Settings controls have no route into `apply_theme`. WS-A and WS-C both need this plumbing — do it
  first inside W1 and both fall out cheaply.
- **A single git backend.** There are **two** parallel git layers: `nd_git.py` (clean, PAT-aware,
  ff-only, used by the Git tab) and an older one inside `LibraryManager.py` (used by every auto-commit).
  Unifying onto `nd_git` (`GIT-03`) gives one auth story, one corruption guard, and one place to generate
  semantic commit messages (`GIT-01`).
- **A wrapping / uncut data-table widget.** `PROJ-05/07/08` (BOM + Health cut off, no wrap, black text in
  dark mode) and `PCB-06/07` (netclass table) are all the same `widgets.data_table` limitations. Fix the
  widget once.

---

## 3. The waves

```
- Wave 0  — 5 items   (S:2 M:2 L:1)   broken core + get onto latest
- Wave 1  — 11 items  (M:6 L:4 XL:1)  foundational engines (mostly invisible, unblock the visible wins)
- Wave 2  — 31 items  (S:4 M:11 L:11 XL:5)  per-area rebuilds that consume the foundations
- Wave 3  — 19 items  (S:14 M:5)      polish / cosmetic / quick-wins (parallelizable, cheap tier)
- Deferred — 2 items  (L:1 XL:1)      frameless shell (decision-gated, high-risk)
```

### Wave 0 — Broken core / get onto latest
`SET-04` (updater + nav badge) · `SET-02` + `SHELL-04` (dead theme buttons + persist theme) ·
`SET-03` (remove accent) · `SRC-01` (verify crash is gone on fresh build).
**Goal:** the user is on a current binary with a working theme control, and the phantom-bug list is
re-confirmed. Small, low-risk, visible.

### Wave 1 — Foundations (unblockers)
The engines. Mostly backend/plumbing; a couple are visible fixes.
- **WS-A units:** `PCB-02`, `LIB-14`, `SHELL-03` — one persisted "Length Units" setting in Settings +
  bus topic; Projects/Bench/Library read it.
- **WS-C finish:** `SHELL-06` (restyle-registry leak makes every theme toggle slower — give restylers a
  lifecycle). (`SET-02`/`SHELL-04` already landed in W0.)
- **WS-E git:** `GIT-03` (unify onto `nd_git`) → `GIT-01` (semantic commit messages) → `GIT-02`
  (app-level background auto-pull service + persisted toggle) → `LIB-13` (fast-forward pull-before-push
  so multi-user drop-ins never reject).
- **WS-F data-table:** the wrapping/uncut variant. Lands `PROJ-07` (black-text-in-dark-mode BOM) here.
- **WS-G anchor:** `PCB-09` — the profile engine: split the fab floor (OSH Park 4/2-layer, **nets-free**)
  from the netclass taxonomy, add profile CRUD (new/save/update/load), and register a **`NETDECK`
  profile** = OSH Park 4-layer + the full 19-class netclass set (seed values in Appendix A).
- **Perf:** `SHELL-02` — move build-time library-scan and git-status loads off the UI thread (they run
  synchronously today); investigate the 3D/STEP-render crash suspect (native OpenCASCADE paint work —
  candidate for a subprocess rather than a thread).

### Wave 2 — Per-area rebuilds
The visible payoff, built on Wave 1.
- **Library:** compact filters + correct taxonomy (`LIB-01/02`, WS-H); Mouser-driven **humanized +
  technical names**, full Mouser field surface, MPN-autofill, kill the dead buttons
  (`LIB-03/04/05/06/07`, WS-B); unified **Add/Replace** asset flow replacing drag-zones (`LIB-11`);
  Maintenance as its own tab (`LIB-12`); preview theme + layout (`LIB-09/10`); fold Import into Parts
  with drag-drop ZIP (`SRC-03`, `LIB-08`).
- **Projects/Health/BOM:** path-disambiguated + multi-select selector (`PROJ-01/02`); Health rebuild —
  multi-sheet component collection, live project-switch refresh, wrapping table, one-click **Fix-All**
  with a pluggable check registry (`PROJ-05/06`, WS-K); Refactor UX overhaul + title-case bug
  (`PROJ-04`); **real BOM builder** — consolidated multi-project, Library/Mouser part-number enrichment,
  basic-part detection (`PROJ-08/09`).
- **PCB Setup (on WS-G):** netclass table redesign — color column, multi-select delete, patterns pulled
  out of the table (`PCB-07`); declutter + profile dropdown (`PCB-05`); board-setup coverage expansion
  (`PCB-03`); geometry profiles (`PCB-04`); pull-profile-from-KiCad (`PCB-12`); original/last-saved undo
  affordance (`PCB-13`); netclass values reconciled to build cards + OSH Park (`PCB-10`).
- **Bench connection-diagram rebuild (WS-J):** `BENCH-10/11/12` — a true build-card-style figure (square
  socket/MCU card, per-passive blocks, long vertical Samtec destination card, one row per pin/pad, edges
  colored by the real netclass). Depends on WS-G for the netclass profile. Grammar in Appendix B.
- **Bench family filter (`BENCH-06`):** a real family selector (STM32F0…F7) threaded into the authority
  query layer.

### Wave 3 — Polish / quick-wins (parallelizable)
- **Bench (WS-I + misc):** legend redesign — compact, off the over-used eyebrow font, monochrome for the
  abundant classes (`BENCH-01/02/03`); pitch-driven pin-number sizing so dense packages stop overlapping
  (`BENCH-04`, the highest-value item here); detail-pane pills (`BENCH-08`); scroll-zoom + pane resize
  (`BENCH-09`); smarter switching-pin pills (`BENCH-13`); remove Buildable header (`BENCH-14`); resolver
  as searchable dropdown (`BENCH-15`); confirm `BENCH-05/07` on latest.
- **Settings/Git:** sun/moon toggle glyph (`SET-01`); Git-as-Projects-tab (`GIT-04`).
- **PCB:** collapsible sections (`PCB-01`); confirm `PCB-06/08`; editable-field affordance + spelled-out
  labels (`PCB-11`).
- **Sourcing:** `SRC-04` (committed free Mouser key) — no action per prior accepted tradeoff.

### Deferred track — Frameless shell (WS-D)
`SHELL-01` + `SRC-02` — remove the native Windows title bar, go frameless with custom chrome. **XL,
high-risk, and decision-gated:** the shell docstring keeps the native title bar deliberately because the
CI screenshot/validation harness keys on it and Windows gives free move/resize/snap. True frameless means
re-implementing snap/resize and re-anchoring the harness. **Decision needed** (see §4): true frameless vs.
a themed/dark native title bar (DWM) that gets 80% of the look for ~5% of the risk. Schedule when churn is
low; not blocking anything else.

---

## 4. Decisions needed before building

> [!note] Decisions locked 2026-07-08
> - **Start:** Wave 0 now.
> - **Title bar (`SHELL-01`/`SRC-02`):** **dark native title bar** via Windows DWM tint — *not* true
>   frameless. This drops both from XL/high-risk to a small, low-risk task and keeps the CI screenshot
>   harness + native window snap intact. Re-scoped out of the Deferred track; folded in with theme work.
> - **Theme (`SET-02`/`SHELL-04`):** persist across launches **and** add a "Follow Windows" System mode.
> - **Git (`GIT-03`/`GIT-01`):** unify the two backends onto `nd_git`, then generate conventional-commit
>   messages naming the component + changed fields.

Grouped by when they block. **Bold = blocks a foundational wave.**

**Blocks Wave 0/1:**
- **Are you on the latest build?** (Confirms the stale-build hypothesis and closes ~10 phantom gripes.)
- **`GIT-03` — merge the two git backends now, or just add a shared message-builder both call?** Merge is
  cleaner; slightly higher risk to the working auto-commit path.
- **`GIT-01` — commit-message style:** conventional-commits (`feat(lib): add TPS2121 …`) vs. a human
  sentence? One line, or a body listing every changed field?
- **Theme persistence / `System` mode:** persist theme across launches (yes, almost certainly) and add a
  "follow Windows" option, or Dark/Light only? Keep both theme controls (nav button + Settings) in sync,
  or drop the nav button?
- **`PCB-09` — profile model:** two independent axes (fab floor + netclass set) or one combined profile?
  (User wants bare OSH Park = nets-free AND a NETDECK = all-nets, which implies two axes.)

**Blocks Wave 2:**
- **`BENCH-06` — which families are "the main ones"** to default to (just F4, or F1/F4/F7)? Hard DB filter
  or live UI filter over the existing F0–F7 DB?
- **`BENCH-03` — which pin classes go monochrome** (lane + ground?) and which stay saturated
  (must-switch/power/core)? Drives a shared palette retune.
- **`PROJ-06` — Fix-All safety:** what may the app mutate automatically (auto-annotate refs, write
  MPNs/footprints into `.kicad_sch`)? `.bak`-backed atomic apply, or per-item confirmation?
- **`PROJ-09` — "basic" part catalog:** JLCPCB basic parts, an internal Library allow-list, or Mouser?
- **`PROJ-02` — multi-select scope:** BOM-only (Health/PCB stay single "primary" project) or whole
  workspace operates on a set?
- **`LIB-05` — MPN autofill:** overwrite existing identity fields, or fill blanks only? Trigger on plain
  Part Number too, or only Mouser P/N?
- **`LIB-02` — "Missing" predicate:** what counts as missing Mouser data (no manufacturer? no datasheet?
  no mouser_pn? failed live lookup)?
- **`PROJ-04` — Refactor placement:** fold the rename ops into Health/Maintenance, or keep a dedicated
  tab with per-op result views?

**Blocks Deferred track:**
- **`SHELL-01`/`SRC-02` — true frameless vs. dark native title bar?** (see Deferred track above).

---

## 5. Full triage index (all 68)

Full per-gripe evidence (`file:line`, root cause, fix approach, dependencies) lives in the tracker doc and
the archived triage JSON (§6). Effort: S <1h · M few hours · L 1–2 days · XL multi-day/redesign.

| ID | Area | Gripe | type/sev | eff | Wave | WS | Flags |
|----|------|-------|----------|-----|------|----|-------|
| `BENCH-01` | Bench | Legend too large / not compact | visual/med | S | W3 | WS-I | quick-win |
| `BENCH-02` | Bench | Legend font over-used, no flavor | visual/low | S | W3 | WS-I | quick-win |
| `BENCH-03` | Bench | Legend colors: abundant classes → monochrome | visual/med | M | W3 | WS-I | decision |
| `BENCH-04` | Bench | MCU pin numbers overlap on dense packages | visual/high | M | W3 | WS-I | **priority** |
| `BENCH-05` | Bench | Package dropdown sort / default LQFP | ux/low | S | W3 | — | verify-latest |
| `BENCH-06` | Bench | Family filter (STM32F…), say it's STM32F | missing/med | L | W2 | — | decision |
| `BENCH-07` | Bench | Package should affect all tabs (Profile stuck) | bug/high | S | W3 | — | verify-latest |
| `BENCH-08` | Bench | Detail pane squished; use pills not text | ux/med | M | W3 | — | |
| `BENCH-09` | Bench | Scroll-wheel zoom; shrink graphic pane | ux/low | S | W3 | — | |
| `BENCH-10` | Bench | Conn-diagram: pins/pads per component like cards | missing/high | XL | W2 | WS-J | depends WS-G |
| `BENCH-11` | Bench | Conn-diagram naming: human names, ordered | ux/med | M | W2 | WS-J | |
| `BENCH-12` | Bench | Conn-diagram should look like a diagram (cards) | missing/high | XL | W2 | WS-J | depends WS-G |
| `BENCH-13` | Bench | Switching pins: smarter uniform pills | ux/med | S | W3 | — | quick-win |
| `BENCH-14` | Bench | Remove pointless "Buildable" header | ux/low | S | W3 | — | quick-win |
| `BENCH-15` | Bench | Pinout viewer: searchable dropdown + relabel | ux/med | M | W3 | — | decision |
| `LIB-01` | Library | Filters ugly (big buttons); make compact | ux/med | M | W2 | WS-H | |
| `LIB-02` | Library | Filter taxonomy: Complete vs Missing-what | ux/med | M | W2 | WS-H | decision |
| `LIB-03` | Library | Humanized + technical part names from Mouser | ux/high | L | W2 | WS-B | |
| `LIB-04` | Library | Show all Mouser info for a part | missing/med | S | W2 | WS-B | quick-win |
| `LIB-05` | Library | Mouser P/N autofills all fields | missing/high | M | W2 | WS-B | decision |
| `LIB-06` | Library | Delete dead "Look up on Mouser" button | ux/med | S | W2 | WS-B | verify-latest |
| `LIB-07` | Library | Refresh-sourcing / Enrich-blanks do nothing | ux/med | M | W2 | WS-B | verify-latest |
| `LIB-08` | Library | Fold Import + Sourcing into Parts tab | ux/low | S | W2 | — | |
| `LIB-09` | Library | Preview bg should follow theme (flip in light) | visual/med | M | W2 | WS-C | runtime |
| `LIB-10` | Library | Preview panes waste horizontal space | visual/low | M | W2 | — | |
| `LIB-11` | Library | Editable fields; Add/Replace not drag-zones | ux/med | L | W2 | — | |
| `LIB-12` | Library | Maintenance as its own tab | ux/low | M | W2 | — | quick-win |
| `LIB-13` | Library | Confirm auto-commit/push + background auto-pull | bug/high | M | W1 | WS-E | decision |
| `LIB-14` | Library | App-wide mm/mils in notes | missing/med | L | W1 | WS-A | |
| `SRC-01` | Sourcing | MPN-add crash + "Complete tab" gating | bug/high | S | W0 | — | verify-latest |
| `SRC-02` | Sourcing | Remove native Windows title bar | ux/med | L | Deferred | WS-D | decision, deferred |
| `SRC-03` | Sourcing | Import ZIP as drag-drop + auto-git fetch | ux/med | M | W2 | WS-E | quick-win |
| `SRC-04` | Sourcing | Committed free Mouser key | idea/low | S | W3 | — | no-action |
| `PROJ-01` | Projects | Selector shows duplicates; show path | ux/med | S | W2 | — | quick-win |
| `PROJ-02` | Projects | Multi-select projects (for BOM) | missing/med | L | W2 | — | decision |
| `PROJ-03` | Projects | Git as its own workspace tab | missing/low | S | W3 | — | verify-latest |
| `PROJ-04` | Projects | Rename tab does too much; ugly; title-case bug | ux/med | L | W2 | WS-K | decision |
| `PROJ-05` | Projects | Health tab non-functional (cut off, no wrap) | bug/high | L | W2 | WS-K | |
| `PROJ-06` | Projects | Health one-click Fix-All + pluggable checks | missing/high | XL | W2 | WS-K | decision |
| `PROJ-07` | Projects | BOM: no wrap, horiz scroll, black text in dark | bug/high | M | W1 | WS-F | runtime |
| `PROJ-08` | Projects | Consolidated BOM broken; multi-project | bug/high | L | W2 | WS-F | |
| `PROJ-09` | Projects | Real BOM builder (Library/Mouser part #s) | missing/high | XL | W2 | WS-B | decision |
| `PCB-01` | PCB Setup | Consolidate fab/netclass/board into one tab | ux/low | S | W3 | — | verify-latest |
| `PCB-02` | PCB Setup | App-wide mils/mm setting | missing/high | L | W1 | WS-A | |
| `PCB-03` | PCB Setup | Board setup missing many KiCad settings | missing/high | XL | W2 | WS-G | |
| `PCB-04` | PCB Setup | Board geometry needs profile treatment | ux/med | L | W2 | WS-G | decision |
| `PCB-05` | PCB Setup | Netclass UI unintuitive; no profiles | ux/high | L | W2 | WS-G | |
| `PCB-06` | PCB Setup | Sticky netclass header | ux/low | S | W3 | WS-F | verify-latest, quick-win |
| `PCB-07` | PCB Setup | Color column; multi-select delete; patterns out | ux/high | L | W2 | WS-F | |
| `PCB-08` | PCB Setup | Add/edit netclasses; filter table | missing/low | S | W3 | WS-H | verify-latest, quick-win |
| `PCB-09` | PCB Setup | Profile CRUD; nets-free OSH + NETDECK profile | missing/high | XL | W1 | WS-G | decision |
| `PCB-10` | PCB Setup | Netclass values from build cards + OSH Park | idea/med | M | W2 | WS-G | decision |
| `PCB-11` | PCB Setup | Values look editable; no abbreviations | visual/med | M | W3 | — | quick-win |
| `PCB-12` | PCB Setup | Pull profile from existing KiCad file | missing/med | M | W2 | WS-G | |
| `PCB-13` | PCB Setup | Show original/last-saved value (undo safety) | missing/med | L | W2 | WS-G | |
| `GIT-01` | Git | Semantic commit messages (what changed) | ux/med | M | W1 | WS-E | decision |
| `GIT-02` | Git | Confirm auto-commit/push + bg auto-pull service | missing/med | M | W1 | WS-E | |
| `GIT-03` | Git | Unify the two git backends onto nd_git | idea/med | L | W1 | WS-E | decision |
| `GIT-04` | Git | Git also as a Projects workspace tab | idea/low | S | W3 | — | quick-win, decision |
| `SET-01` | Settings | Change the (theme-toggle) glyph | visual/low | S | W3 | — | verify-latest, decision |
| `SET-02` | Settings | Theme buttons are dead (no on_change) | bug/high | M | W0 | WS-C | |
| `SET-03` | Settings | Remove Selection-Accent control | ux/low | S | W0 | — | quick-win |
| `SET-04` | Settings | Updater doesn't replace exe; nav badge | bug/med | L | W0 | — | runtime |
| `SHELL-01` | Shell | Frameless custom chrome | missing/med | XL | Deferred | WS-D | decision, deferred |
| `SHELL-02` | Shell | Perf: blocking loads on UI thread; crashes | perf/high | L | W1 | — | runtime |
| `SHELL-03` | Shell | App-wide unit setting in app state | idea/med | M | W1 | WS-A | |
| `SHELL-04` | Shell | Theme correctness app-wide + persistence | bug/high | M | W0 | WS-C | |
| `SHELL-06` | Shell | Restyle-registry leak slows theme toggle | perf/med | M | W1 | WS-C | |

---

## 6. Appendix

### A. `NETDECK` netclass seed (for `PCB-09` / `PCB-10`)
Extracted verbatim from `~/git/NETDECK/Master/Master.kicad_pro` → `net_settings.classes` (19 classes,
units mm), cross-checked against `pcb-build-system/data/net-classes.yaml`. Signal classes sit at the OSH
Park floor (0.15 track / 0.127 clearance / 0.254 via drill); power classes widen the **track**; USB is
the only tightened diff-pair (gap 0.15).

| Netclass | Clearance | Track | Via Ø | Via drill | DiffPair W | DiffPair gap | color rgb |
|---|---|---|---|---|---|---|---|
| Default | 0.20 | 0.20 | 0.60 | 0.30 | 0.20 | 0.25 | — |
| PWR_IN | 0.20 | 0.60 | 0.60 | 0.30 | 0.20 | 0.25 | 176,58,46 |
| PWR_5V | 0.127 | 0.50 | 0.60 | 0.30 | 0.20 | 0.25 | 224,123,57 |
| PWR_3V3 | 0.127 | 0.40 | 0.60 | 0.30 | 0.20 | 0.25 | 201,154,46 |
| PWR_1V8 | 0.127 | 0.40 | 0.60 | 0.30 | 0.20 | 0.25 | 166,184,79 |
| TGT_PWR | 0.127 | 0.50 | 0.60 | 0.30 | 0.20 | 0.25 | 197,111,174 |
| SW_NODE | 0.20 | 0.50 | 0.60 | 0.30 | 0.20 | 0.25 | 232,179,57 |
| GND | 0.127 | 0.25 | 0.60 | 0.30 | 0.20 | 0.25 | 94,138,199 |
| USB | 0.127 | 0.20 | 0.457 | 0.254 | 0.20 | **0.15** | 210,111,160 |
| CTRL / SWD / SPI_SW / LANE / I2C_PWR / ID / SENSE / SERVICE / STATUS / FAULT | 0.127 | 0.15 | 0.457 | 0.254 | 0.20 | 0.25 | (per-class, see archive) |

Net-pattern auto-assignments (43 rules) also captured — e.g. `GND: *GND,*VSSA_TGT,*CHASSIS`;
`PWR_IN: *V_SYS,*USB_VBUS*,*CELL_IN*`; `FAULT: *FAULT*,*KILL*,*ALERT*,*OCP*`. Full list in the archived
reference. `TGT_CORE` appears in app code but not in the `.pro` — decide whether it's legitimate
(1.2 V regulator nodes) before syncing.

### B. Connection-diagram grammar (for `BENCH-10/11/12`)
The build cards already encode the grammar the user wants (`component_implementation[].connections[]`):
- **Atomic unit = one pin row**, five fields: **Pin** (`pin 36 / PA2` — number + role, never refdes-only)
  · **Signal** (plain words) · **Net** (span colored by its netclass) · **Direction** (full word:
  Input/Output/Bidirectional/Passive) · **Destination** (the exact far-end pin/contact, never just a
  label).
- **Three nested levels to draw:** Component block → pin rows (block height = pad count, so a big IC is a
  tall stack, a 2-pad passive is a 2-row block) → net edge colored by netclass, terminating at the named
  far end.
- **Connectors are Passive/Input, never Output** — draw the Samtec dock as a tall pass-through card, one
  row per contact, each pointing at its mating landing. `leaves_card: Yes/No` = does the edge exit the
  board.
- **Naming rules:** role-first human names; `R`/`kR` not Ω; `u` not µ; full words; Title Case; no
  em dashes; group pins by peripheral/function, not pin-number order.

### C. Archived reference material
- Design contract (tokens, component recipes, theme/chrome/nav wiring): distilled from
  `docs/design/design-rules.md` + `tools/ui/{theme,widgets,shell,feature}.py`.
- Full per-gripe triage JSON (evidence + fix approach + deps for all 68) and the two reference scouts are
  in the session workflow output. The live per-item tracker with tags is
  `~/Documents/Obsidian/Brain/Agent/Hardware App Gripes.md`.

---

## 7. Status convention (this doc + tracker)
- `[x]` done & pushed · `#triaged` slotted · `#wave/N` assigned · `#wontfix (reason)`.
- Update both this doc's index table (Wave column) and the tracker as items ship.
