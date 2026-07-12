# Library — Completion Passport (v2.11 redesign)

Status: **LOCKED** (owner design decisions 2026-07-10). Supersedes the exploratory
mockups in `.superpowers/mockups/` (`library-v2.html` is the locked reference render).

## Intent

Reframe the Library Parts view around one honest idea: **every part is driven by the
8 things it needs to be Complete.** The picker scores each part `N of 8`; the canvas is
that part's passport — a completion ring, a `▶ Complete This Part` flow, the renders as
the hero, and an editable identity form with one explicit Save. This becomes the grammar
we then roll to Projects / Bench / Git / Settings.

## The 8-item completion model (owner-locked)

A part is **Complete** when all 8 hold AND it has no dangling reference. Every item maps
to a real, writable field/setter — no vaporware:

| # | Item | Field / source | Setter |
|---|------|----------------|--------|
| 1 | Symbol | `row.has_symbol` (symbol block exists) | symbol import / reuse |
| 2 | Footprint | `row.has_footprint` | `set_library_symbol_footprint` |
| 3 | 3D Model | `row.has_model` | `set_footprint_model` / `attach_model_to_footprint` |
| 4 | Part Number | KiCad property `Value` (`row.mpn`) | `set_library_symbol_property(..., "Value", ...)` |
| 5 | Manufacturer | property `MANUFACTURER` (`row.manufacturer`) | `set_library_symbol_property` |
| 6 | Datasheet | property `Datasheet` (`row.datasheet`) | `set_library_symbol_property` |
| 7 | Description | property `Description` (`row.description`) | `set_library_symbol_property` |
| 8 | Category | property `Category` (`row.category`) **explicit only** | `set_library_symbol_property` |

**Category nuance:** the row's `category` today can be an auto **refdes-derived** fallback
(R→Resistor). For #8 to be meaningful, Complete requires the **explicit** `Category`
property, not the fallback — so `part_completion` must be handed (or must read) the
explicit property, not the display fallback. Flag: if the owner finds this too strict,
flip to "any category (incl. fallback) satisfies #8" — one predicate change.

**Dangling reference** is NOT one of the 8. It is a disqualifier: a part with a dangling
ref shows a red **Fix** state in the picker and a fix banner in the canvas, and can never
be Complete regardless of score.

**Behavior change (owner-approved "tighten it"):** today `_is_complete` = 3 assets +
manufacturer + not-dangling. The new rule additionally requires Part Number, Datasheet,
Description, Category. Parts that read green today but lack those drop to e.g. 6/8. This
is intended — more honest.

## Reuse map — what already exists (do NOT rebuild)

- **Sticky Save bar** — `PartDetail._savebar` / `_unsaved` / `_unsaved_edits` /
  `_refresh_savebar` (the LIB-flash fix). Edits accumulate, one explicit Save commits+pushes.
- **Editable identity list** — `PartDetail._fields` (rebuilt each `show`).
- **Preview cards + drop-in** — `PreviewCard` Symbol/Footprint/3D Model with
  `enable_dropin` for missing assets → the "Files" hero.
- **Autofill engine** — `LM.enrich_symbol` / `LM.enrich_library` (Mouser → Value,
  Manufacturer, Datasheet, Description). The `▶` "Autofill From Mouser" path composes this.
- **Facet bar** — `_FacetBar` + `PRIMARY_FACETS` (All / Complete / Missing) + `MISSING_FACETS`.
- **Row builder** — `LM.associate_parts_from_cfg` → `List[dict]` rows.

## What is NEW / changed

1. **`part_completion(row) -> Completion`** (pure, in `LibraryManager.py`): the 8 items,
   each `(key, label, present, counts, hint)`, plus `score`, `total=8`, `missing`,
   `dangling`, `is_complete`. Single source of truth.
2. **`_is_complete` re-expressed** in terms of `part_completion(...).is_complete`.
3. **Picker rows** show `N/8` (green at 8/8, `Fix` when dangling); an **Incomplete** facet;
   score-sorted within group.
4. **Canvas header**: completion **ring** (`N/8`) + a "Still needs …" sentence; a **Fix**
   banner when dangling.
5. **Category** becomes an editable identity field that counts.
6. **`▶ Complete This Part`** primary: opens a 2-choice menu —
   - **Autofill From Mouser** → `enrich_symbol` fills 4–7 (Value/Manufacturer/Datasheet/
     Description), then surfaces any assets it can't fetch (3D Model, footprint, category)
     as the remaining checklist.
   - **Step Through Manually** → walk each missing item one at a time (asset drop-ins +
     field focus), in checklist order.
7. **Per-field affordances** (from `library-v2.html`): `Autofilled` / `Edited` badges and
   inline **diff + Revert** on a changed field. (Save bar already aggregates the Save.)

## Copy / design contract

Title Case for all labels; sentence case only for the "Still needs …" prose and status
lines; **no em dashes**; part numbers/refdes keep real casing. Neutral accent only
(`▶` primary is the sole accent). One focal point = the ring + `▶`.

## Phased build (TDD; drive-audit + render-gate each phase)

- **P1 Foundation:** `part_completion` pure fn + tests; re-express `_is_complete`; keep the
  existing facets green. (No visible UI change yet.)
- **P2 Picker:** `N/8` score per row + `Fix` state + Incomplete facet; regression tests.
- **P3 Canvas:** ring + "Still needs …" + Fix banner + Category field; renders.
- **P4 ▶ flow:** Complete This Part (autofill / manual), composing `enrich_symbol` +
  drop-ins; drive-audit case per branch (headless-guarded, no modal block).
- **P5 Verify:** extend `drive_audit.py` (drive the score, the ▶ menu both branches, the
  Fix state), `render_gate` both themes, **Read the PNGs** vs `library-v2.html`. Update
  `docs/CAPABILITIES.md`.

## Verification gate (per no-fault gates)

`QT_QPA_PLATFORM=offscreen pytest tests -q` green · `drive_audit.py` exit 0 · render-gate
PNGs Read and checked vs the mockup + design-rules · Windows-CI green before tag. Never
claim done off tests alone.
