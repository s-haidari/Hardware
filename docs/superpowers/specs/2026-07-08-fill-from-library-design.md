# Fill Project Component Fields From the Library — Design Spec

**Date:** 2026-07-08 · **Status:** approved (brainstorm) · **Owner priority:** #1
**Plan reference:** perfection-audit finding #7 (flow-sourcing rebuild — *"owner-requested
'fill component fields from the Library' into Health Fix-All is unbuilt"*).

## 1. Goal & success criteria

Make **Health → Fix-All** actually fix the project — including filling each component's
sourcing/footprint fields from the Library — instead of only annotating reference designators
(today it fixes 0 of 919 findings on the Master project).

Success = all of:
- Running Fix-All on a real project **writes** MPN, manufacturer, datasheet, description, and the
  Library's **footprint** into the `.kicad_sch`, and the 3D model then resolves from that footprint.
- **Nothing wrong is ever written**: exact matches are pre-selected, uncertain (fuzzy) matches are
  shown flagged and unchecked, and **nothing is written until the user approves a preview**.
- The whole thing **feels good** — a calm, grouped, scannable preview with clear confidence and
  old→new deltas, one primary Apply, and an immediate re-audit so progress is visible.
- Fully test-proven: the real Fix-All fills a real fixture project end-to-end.

## 2. Non-goals

- **Symbol replacement** (swapping a generic `Device:R` for the Library's symbol). Considered and
  deferred — it re-places the component, a heavier/riskier op. May return later as a separate,
  clearly-flagged opt-in. Not in this spec.
- Routing, BOM export, and Library-side edits — out of scope.
- Sourcing *lookups* (Mouser/DigiKey) — this fills from the **local Library**, not the network.

## 3. Architecture

A new focused, pure module plus a preview surface in the existing Health panel. `nd_project_health`
stays the read-only auditor.

```
nd_library_fill.py         (NEW — pure: match + plan + write; no Qt, fully unit-testable)
  ├─ build_fill_plan(components, library_parts, opts) -> FillPlan
  ├─ apply_fill_plan(plan, selection, *, backup=True) -> FillResult   (writes .kicad_sch, .bak)
  └─ (helpers: matching, property/footprint block editing)

tools/ui/features/projects.py  (Health panel)
  ├─ Fix-All: collect fixers' proposed changes (annotate + fill) -> ONE preview dialog
  └─ FillPreviewDialog: grouped review, confidence chips, old→new, Apply -> apply + re-audit

LibraryManager.py  (reused, unchanged): scan_library_grouped, part_identity, strict_mpn,
  qualify_footprint / FP_NICKNAME, register_libraries (footprint resolvability)
nd_project_health.py (reused, unchanged): schematic_components, _symbol_spans, audit_project
```

### Design-for-isolation contract
- **`nd_library_fill`** — *what:* turns (project components + Library parts) into a reviewable plan
  and applies a selected subset to disk. *Depends on:* only stdlib + the pure helpers named above
  (no Qt). *Usable without reading internals:* yes — `build_fill_plan` → show → `apply_fill_plan`.

## 4. Data model

```python
FieldChange   = {field, old, new, source_part}          # e.g. field="MPN", old="", new="RC0402..."
Match         = {ref, lib_part, confidence}              # confidence: "exact" | "verify" | "none"
FillItem      = {ref, sheet, match, changes: [FieldChange], default_selected: bool}
FillPlan      = {items: [FillItem], summary: {components, fields, need_review, no_match}}
FillResult    = {written_files, components_changed, fields_written, backups: [path], errors: []}
```
`default_selected` is True for an exact match with only blank-fills; False when the match is fuzzy
OR any change would overwrite existing non-empty data (see §6).

## 5. Matching rules (`build_fill_plan`)

For each real project component (from `schematic_components`, power/virtual excluded):

1. **Exact — symbol identity.** The component's `lib_id` symbol name matches a Library part's symbol
   → `confidence="exact"`.
2. **Exact — MPN.** Else, if the component already carries a strict MPN (`strict_mpn(props)`) that a
   Library part has → `confidence="exact"`.
3. **Fuzzy — value + footprint.** Else, if a Library part shares the component's normalized `value`
   AND footprint stem → `confidence="verify"`. (Reuses the value-normalization the smart-BOM basic-part
   detection already uses.)
4. **None.** Else `confidence="none"` — surfaced as "not in Library", no changes.

Ambiguity (more than one Library part tie) is treated as **`verify`**, never auto-selected, and the
top candidate is shown with the count of alternatives.

## 6. What gets written & conflict handling

Per matched component, propose a `FieldChange` for each of: **MPN, Manufacturer, Datasheet,
Description, Footprint**, using the Library part's value, but only when it differs from the current
schematic value.

- **Blank → value** (fill): included and **pre-checked** on an exact match.
- **Value → different value** (overwrite): included but **unchecked + flagged "overwrite"** — the
  user opts in per field. We never silently clobber real data.
- **Footprint**: written as the qualified `Nickname:footprint` (`qualify_footprint`); on apply, the
  Library's footprint library is ensured-registered (`register_libraries`) so KiCad + 3D resolve it.
  If the footprint can't be made resolvable, that field is flagged, not written.

## 7. The writer (`apply_fill_plan`)

- Groups selected changes by `.kicad_sch` file. For each file: parse top-level `(symbol …)` blocks
  with the **existing paren-matched, string-aware `_symbol_spans`** (the same routine annotation
  uses — robust against parens inside quoted Values), locate each target symbol by its resolved
  reference, and set/insert the `(property "<Key>" "<val>" …)` inside that block.
- **Safety:** write a `.bak` sibling of every touched file first, then write the new text; per-file
  atomic (temp write + replace). Mirrors `annotate_project`.
- Idempotent: re-running with the same Library state produces no changes.
- Returns `FillResult` (counts + backups + any per-field errors, surfaced — never swallowed).

## 8. Fix-All integration (the honest "fix everything")

Fix-All stops being annotate-only. Flow:
1. Audit the project (existing `audit_project`).
2. Collect proposed changes from every fixer: **annotate** (existing `annotate_project` dry-run) +
   **fill** (`build_fill_plan`). Extensible: new fixers add here.
3. If nothing to do → say so. Else open **one preview** covering all proposed changes.
4. On Apply → run each fixer's apply for the selected subset, then **re-audit** and refresh the
   Health table + the dashboard's verification row.

## 9. Preview UX (feel + information architecture)

A calm, scannable modal (kit components, on the unified design system):
- **Header/summary:** "38 components · 112 fields to fill · 6 need your check" + one primary **Apply**
  (disabled until ≥1 change is selected), plus a secondary Cancel.
- **Grouped by sheet, then component.** Each component row: refdes + human name, a **confidence chip**
  (`exact` calm/green, `verify` amber), the matched Library part, and its field deltas as **old → new**
  with a per-field checkbox. Blanks pre-checked; overwrites + fuzzy unchecked and visually flagged.
- **Bulk affordances:** select-all / only-exact / clear, so a big board isn't per-field toil.
- **No layout overflow, dark+light correct, mobile-sane** — held to `design-rules.md`.
- After Apply: a quiet result toast ("Filled 112 fields across 38 components; backups written") and
  the Health numbers drop live.

## 10. Testing (proof, not just presence)

Unit (pure, no Qt), against a real small `.kicad_sch` fixture with known gaps + a seeded Library:
- Exact-symbol and MPN matches produce the right `FieldChange`s; fuzzy → `verify`, unselected.
- `apply_fill_plan` writes exactly the selected fields into the correct symbol blocks; a `.bak` exists;
  unselected/overwrite fields are untouched; re-run is a no-op.
- Footprint write yields a resolvable qualified name; unresolvable → flagged, not written.
- Panel smoke: Fix-All builds the preview, Apply mutates the fixture, re-audit shows fewer findings.

**Acceptance (dashboard turns green):** on a real project, Fix-All fills the fields, the `.kicad_sch`
changes on disk, and the re-audit reflects it — the observed real result, not a mock.

## 11. Open questions

None blocking. (Symbol-swap deferred by decision; network sourcing explicitly out of scope.)
