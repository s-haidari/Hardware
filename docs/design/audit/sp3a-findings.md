# SP3-A Findings Ledger

Source of truth for the fix phase. Bar: **zero critiques, dark AND light**, ordered
worst-first (P0 → P1 → P2). Fixes conform to `docs/design/design-rules.md` §1–4 and,
above all, to **what SP2 shipped as pristine on the Library page** — the calibrated bar.

## Calibration (from Library, the SP2 "pristine" reference)

Library is zero-critique and defines the real bar. It establishes that these are
**acceptable** (do NOT "fix" them):
- **Uppercase section eyebrows/headers**, one per region, quiet text_3 (Library ships
  "SYMBOL", "FOOTPRINT", "3D MODEL", "WORKSPACES"). §1.4 targets *sprinkled* micro-labels
  on every field, not one quiet header per region.
- **A neutral verdict band** (bg_raised, no tint) carrying **status pills** = dot + label +
  count ("Complete 24", "Missing Model 48", "Dangling 1").
- **Soft raised cards** (bg_inset elevation step, no hard outline).

And it establishes that **data is plain text, never boxed in a pill**, and **surfaces are
never tinted with a category hue**.

So the SP3-A job is: make Bench / Projects / Settings look like Library.

---

## Shared / systemic findings (fix once in shared helpers — cascade; verify NO Library regression)

| ID | Sev | Finding | Fix (conform to Library) | Surfaces |
|----|-----|---------|--------------------------|----------|
| **S1** | P1 | Callout/verdict band has a **category-tinted (green/olive) background** | Make band neutral `bg_raised`/`bg_inset`; keep the status pills. Match Library Health band. | bench.overview "Buildable", bench.exports "Pre-Write Checks", projects.fabrication-standard "Verify Before Ordering" |
| **S2** | P1 | **Data boxed in pills** (counts, part/branch names, category values) | Plain text with hierarchy (mono for data); category via 6px dot or colored text, never a filled box | bench.profiles (11 switching-pin boxes, family tags, header count pills), bench.mcu-pinout-viewer ("5 V" pill/row), projects.bom ("300 Components"/"105 Line Items"), projects.health ("300 Components"/"2 Healthy"), projects.git (branch pill) |
| **S3** | P2 | **Filled status pill on every row** (Error/Warning/Modified/Pass/OK/Default) — loud, repeated | Quieter status: 6px colored dot + text (or colored text), keep semantics/hue, drop the box. Match Library dot+label. | projects.health (Error/Warning), projects.git (Modified), projects.board-setup (Default), bench.exports (Pass, budget OK), bench.overview (Fabric DRC/Budget) |
| **S4** | P2 | Category shown as a **filled square swatch** | 6px dot. (Exception: the pin-map legend may keep saturated squares — there color IS the data, §4.) | projects.net-classes (net-class squares), bench legend (verify) |
| **S5** | P2 | **Hard-bordered card/table container** (outline) instead of elevation | Use `bg_inset` elevation step, no hard outline (match Library cards). Editable spin-grids KEEP their input borders (legit affordance). | bench tables + Baseline Switch Fabric + Overview pin-map/detail cards, projects.fabrication preset cards, projects read-only value boxes |
| **S6** | P2 | **Casing (§2):** sentences rendered Title Case / UPPERCASE; table column headers UPPERCASE | Sentence case for actual sentences/notes; sentence-case quiet column headers (§4 ledger recipe). KEEP Title Case labels + uppercase section eyebrows. | projects.board-setup note, projects.net-classes note, all table column headers, bench stat-strip labels |

**Sequencing note:** S1–S6 are largely in shared `widgets.py` helpers (`W.tag`/pill, `W.Card`
border, the callout/band builder, `W.data_table` headers) + `theme.py`. Recommend a **shared-widgets
task FIRST** (new Task 2.5) that fixes S1–S6 at the helper level and re-renders **Library** to prove
zero regression, THEN the per-surface tasks (3–10) mop up panel-specific items. This reorders but does
not change the plan's scope.

---

## Per-surface findings

### Task 3 — bench.overview  (heaviest)
- **P1** CONNECTION DIAGRAM is a row of **bordered node cards** with uppercase micro-labels
  (MCU PIN / ZIF SOCKET / SWITCH CELL / CONNECTOR / DELIVERS) + arrows — the exact retired
  pattern. → §4 **Signal path** recipe: ONE `bg_inset` container, flow rows (not cards),
  1px connector elbows, no socket cards. Rewrite `_node` (~L70) + `_connection_flow` (~L132).
- S1 (Buildable tint), S3 (status pills), S5 (pin-map + detail bordered cards → elevation), S6 (stat labels).
- Keep the Must-Switch chip (the one sanctioned fill, §4 pin header).

### Task 4 — bench.profiles
- S2 (switching-pin boxes, family tags, header count pills → plain text/table + dots). Retire
  `_switch_pill` (~L772) / `_chip_grid` (~L757).
- S5 (Baseline Switch Fabric bordered card → elevation).
- Note: "Chips By Profile" loads via slow async ("Grouping supported chips by profile…") — not a
  design defect; ensure the settled render shows the grouped result.

### Task 5 — bench.all-pins  (already close to §4 ledger)
- S5 (table border → hairline-divider frameless), S6 (uppercase headers → sentence case). Otherwise clean.

### Task 6 — bench.mcu-pinout-viewer  (canonical §4 surface)
- S2 ("5 V" pill/row → plain/dot), S5 (pin-map card + table bordered → elevation), S6 (headers).
  Uses `W.Card`/`W.tag`/`W.data_table` in `_resolver_panel` (~L528).

### Task 7 — bench.exports
- S1 (Pre-Write Checks tint), S3 (Pass/OK pills), S5 (BOM + budget tables + per-rail row boxes → elevation), S6 (headers).
- **Deferred (SP3-B):** "Write Authority Bundle…" is disabled (stranded exporter). Style correctly, do NOT wire.

### Task 8 — projects (7 panels)
- **health:** S2 (count pills), S3 (Error/Warning per-row → dot+text), S5 (table), S6 (headers).
- **bill-of-materials:** S2 (count pills), S5 (table), S6 (headers).
- **net-classes** (SP2-touched, light): S4 (net-class squares → dots), S6 (sentence note + headers),
  S5 (read-only pattern boxes; KEEP editable spin borders).
- **board-setup:** S3 ("Default" pills → dim text), S6 (Title-Cased sentence note → sentence case).
- **fabrication-standard:** S1 (Verify band tint), S5 (two preset cards' hard borders → elevation).
- **git:** S2 (branch-name pill → mono text), S3 (Modified per-row tags → quieter status).
- **rename:** NOT yet audited (simple form) — audit dark+light in the fix task.

### Task 9 — settings  (near pristine)
- S5/S2 (path values sit in subtle boxes → plain mono text). Verify light. Otherwise clean.

### Task 10 — shell chrome  (near pristine)
- Nav neutral, azure selection, quiet "Workspaces" eyebrow — looks clean. Verify collapsed rail +
  sub-tab bar + both themes. Shared-widget fixes must not regress Library (re-check in Task 10 Step 3).

---

## Deferred (stranded logic — NOT this strand)
- bench.exports "Write Authority Bundle…" (disabled) → SP3-B exporters.
- Any control dead because its backend is stranded → SP3-B / SP3-C. Style it; do not wire it.

## Not defects (environmental / by design)
- Bench needed `tools/data/stm32.sqlite` built to render (done). Async panels need the gate's `_settle`.
- Uppercase section eyebrows: KEPT (Library precedent).
- Editable spin-grid cell borders: KEPT (input affordance).
