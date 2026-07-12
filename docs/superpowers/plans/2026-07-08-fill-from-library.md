# Fill Project Component Fields From the Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Health Fix-All fill each project component's MPN/manufacturer/datasheet/description + footprint from the local Library, via a preview-and-confirm dialog, writing safely into the `.kicad_sch`.

**Architecture:** A new pure module `tools/nd_library_fill.py` (match → plan → write) composed from existing LibraryManager + nd_project_health helpers, plus a preview dialog wired into the Health panel's Fix-All in `tools/ui/features/projects.py`. Spec: `docs/superpowers/specs/2026-07-08-fill-from-library-design.md`.

**Tech Stack:** Python 3, PyQt5 (UI only), pytest. No new dependencies.

## Global Constraints

- All file reads/writes use `encoding="utf-8"` (Windows CI cp1252 fails otherwise). One line each, verbatim.
- Never `str(Path)` for display → `.as_posix()`.
- Pure logic in `nd_library_fill.py` imports **no Qt**; only stdlib + `LibraryManager` + `nd_project_health`.
- `.bak` backup before every schematic write; per-file atomic (temp write + `os.replace`).
- No compromises: overwrites are never silent; footprint written only when made resolvable.
- Reuse, do not reinvent: `LibraryManager.{extract_symbol_blocks, extract_symbol_properties, extract_symbol_name, set_symbol_property, symbol_footprint_ref, part_identity, strict_mpn, qualify_footprint, FP_NICKNAME, register_libraries}`, `nd_project_health.{schematic_components, _symbol_spans, audit_project}`.

**Project-schematic property keys (NOT the Library `_ENRICH_PROPERTY` map):**
`{"mpn":"MPN", "manufacturer":"Manufacturer", "datasheet":"Datasheet", "description":"Description", "footprint":"Footprint"}`. (MPN → `MPN`, never `Value`.)

---

### Task 1: Library index for matching (`library_parts`)

**Files:**
- Create: `tools/nd_library_fill.py`
- Test: `tests/test_library_fill.py`

**Interfaces:**
- Produces: `library_parts(cfg: dict) -> list[dict]` where each dict is
  `{"name": str, "mpn": str|None, "manufacturer": str|None, "datasheet": str|None,
    "description": str|None, "footprint": str|None, "value": str|None}`.
  Built by iterating symbol blocks of the Library symbol file (`cfg["SymbolLib"]`):
  for each block, `name=extract_symbol_name(b)`, identity via `part_identity(extract_symbol_properties(b), fallback=name)`,
  `footprint=symbol_footprint_ref(b)` (stem), `value=extract_symbol_properties(b).get("Value")`.

- [ ] **Step 1: Write the failing test** — with a tiny fixture symbol lib (a resistor block with Value "10k", Footprint "MyFootprints:R_0402" and a part block with MPN/Manufacturer), assert `library_parts({"SymbolLib": <path>})` returns records whose `name/mpn/footprint/value` match.
- [ ] **Step 2: Run test, verify it fails** (`pytest tests/test_library_fill.py -k library_parts -v`) — ImportError/NameError.
- [ ] **Step 3: Implement `library_parts`** reusing the named helpers; `encoding="utf-8"` on the read (LibraryManager helpers already do).
- [ ] **Step 4: Run test, verify pass.**
- [ ] **Step 5: Commit** (`feat(fill): library index for component matching`).

---

### Task 2: Matching (`match_component`)

**Files:** Modify `tools/nd_library_fill.py`; Test `tests/test_library_fill.py`.

**Interfaces:**
- Consumes: `library_parts` records (Task 1); a project component dict from `nd_project_health.schematic_components` (`{ref, value, footprint, lib_id, props}`).
- Produces: `match_component(comp: dict, lib_index: list[dict]) -> dict` = `{"ref", "lib_part": dict|None, "confidence": "exact"|"verify"|"none", "alternatives": int}`.
  - **exact:** `lib_id` symbol name (`lib_id.split(":")[-1]`) equals a `lib_part["name"]`; OR `strict_mpn(comp["props"])` equals a `lib_part["mpn"]`.
  - **verify:** else a `lib_part` shares normalized value AND footprint stem (normalize value with the same rule the smart-BOM uses — lowercase, strip spaces/units-agnostic; if unsure, exact string match on `.strip().lower()`), `alternatives` = count of other candidates.
  - **none:** otherwise, `lib_part=None`.

- [ ] **Step 1: Write failing tests** — exact-by-symbol, exact-by-MPN, fuzzy value+footprint (confidence "verify"), and no-match (None).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `match_component`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(fill): project-component ↔ Library matching (exact + fuzzy)`).

---

### Task 3: Build the fill plan (`build_fill_plan`)

**Files:** Modify `tools/nd_library_fill.py`; Test `tests/test_library_fill.py`.

**Interfaces:**
- Produces: `build_fill_plan(components: list[dict], lib_index: list[dict], sheet_of: dict) -> dict` =
  `{"items": [FillItem], "summary": {"components", "fields", "need_review", "no_match"}}`.
  `FillItem = {"ref", "sheet", "match": Match, "changes": [FieldChange], "default_selected": bool}`.
  `FieldChange = {"field", "prop", "old", "new", "kind": "fill"|"overwrite"}`.
  For each matched component, propose a FieldChange for each of mpn/manufacturer/datasheet/description/footprint using the project-schematic property keys above, only when `new` differs from the current prop value.
  `kind="fill"` when old is blank/placeholder, else `"overwrite"`. Footprint `new = qualify_footprint(lib_part["footprint"])`.
  `default_selected = (confidence=="exact" and all(c["kind"]=="fill" for c in changes))`.
  `sheet_of` maps `ref -> sheet path` (built by the caller from per-sheet `schematic_components`).

- [ ] **Step 1: Failing tests** — blank field → FieldChange kind "fill", default_selected True on exact; existing value → kind "overwrite", default_selected False; footprint new is `MyFootprints:...`; summary counts correct.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `build_fill_plan`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(fill): build the reviewable fill-plan`).

---

### Task 4: Schematic property writer (`write_fields_to_sheet`)

**Files:** Modify `tools/nd_library_fill.py`; Test `tests/test_library_fill.py`.

**Interfaces:**
- Produces: `write_fields_to_sheet(sheet_path: str|Path, changes_by_ref: dict[str, dict[str,str]], *, backup=True) -> int`.
  `changes_by_ref` = `{ref: {prop: new_value, ...}}`. Returns count of components written.
  Algorithm: read text (`encoding="utf-8"`); for each top-level `(symbol …)` span from `nd_project_health._symbol_spans(text)`, read its Reference (`extract_symbol_properties(block)["Reference"]`); if that ref is in `changes_by_ref`, apply `set_symbol_property(block, prop, val)` for each prop and splice the new block back; write `.bak` then atomic-replace the file if changed. **Only top-level instances**, never the `(lib_symbols)` cache (it's not matched by `_symbol_spans` starting at top-level `(symbol` after the cache — verify the cache's inner symbols aren't top-level; if they are, skip blocks lacking a Reference property).

- [ ] **Step 1: Failing tests** — write MPN+Footprint to R1 in a 2-symbol fixture: R1's props updated, R2 untouched, `.bak` exists with original text, re-run with same changes writes nothing new (idempotent), a ref not present is a no-op.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `write_fields_to_sheet`** (atomic: write temp in same dir, `os.replace`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(fill): safe .kicad_sch property writer (.bak + atomic)`).

---

### Task 5: Apply the plan (`apply_fill_plan`)

**Files:** Modify `tools/nd_library_fill.py`; Test `tests/test_library_fill.py`.

**Interfaces:**
- Produces: `apply_fill_plan(plan: dict, selected: set[tuple[str,str]], cfg: dict, log=None, *, backup=True) -> dict`.
  `selected` = set of `(ref, prop)` the user checked. Groups selected FieldChanges by sheet → `changes_by_ref`, calls `write_fields_to_sheet` per sheet; if any footprint prop was written, call `LibraryManager.register_libraries(cfg, log)` so the footprint resolves. Returns
  `FillResult = {"written_files":[...], "components_changed":int, "fields_written":int, "backups":[...], "errors":[...]}`. Errors captured per file, never raised out.

- [ ] **Step 1: Failing test** — given a plan + a selection, apply mutates the fixture sheet, returns correct counts + a backup path; unselected (ref,prop) pairs are NOT written.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement `apply_fill_plan`.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** (`feat(fill): apply selected fills + register footprint lib`).

---

### Task 6: Re-audit proof (integration test)

**Files:** Test `tests/test_library_fill.py`.

- [ ] **Step 1: Write the end-to-end test** — a fixture project (schematic with an R lacking MPN + Footprint) + a Library that has the matching part; run `library_parts` → `build_fill_plan` (per-sheet components via `nd_project_health.schematic_components`) → `apply_fill_plan(select all exact)`; then `nd_project_health.audit_project([sheet])` reports **fewer** `no_mpn` / `no_footprint` findings than before. Asserts the real result on disk.
- [ ] **Step 2: Run, verify it passes** (implementation already exists from Tasks 1-5).
- [ ] **Step 3: Commit** (`test(fill): end-to-end fill lowers real audit findings`).

---

### Task 7: Fix-All integration + preview dialog (`projects.py`)

**Files:**
- Modify: `tools/ui/features/projects.py` (the Health panel's Fix-All handler)
- Test: `tests/test_proj06_autofix.py` (extend)

**Interfaces:**
- Consumes: `nd_library_fill.{library_parts, build_fill_plan, apply_fill_plan}`, `nd_project_health.{schematic_components, annotate_project, audit_project}`.
- Fix-All flow: build the union of proposed changes — annotate (dry-run count) + fill plan; if empty, log "Nothing to fix."; else open `FillPreviewDialog`.
- `FillPreviewDialog(QDialog)`: grouped by sheet → component; per component a confidence chip (`exact` calm / `verify` amber), the matched Library part, and each FieldChange as a checkbox row `field: old → new` (fill pre-checked, overwrite/verify unchecked). Header summary (`N components · M fields · K need your check`) + bulk (all / only-exact / clear). Primary **Apply** (disabled until ≥1 selected). On Apply → `apply_fill_plan(plan, selected, cfg, log)` + run annotate apply if selected → **re-audit** (`ws.rebuild` / the Health refresh) so numbers drop. Built on the kit; held to `design-rules.md`; no overflow, dark+light, `.grab()` clean headless.

- [ ] **Step 1: Extend the autofix test** — under offscreen, build the Health panel against a fixture project, invoke the Fix-All handler, assert a `FillPreviewDialog` is constructed with the expected item count and that calling its `apply()` mutates the fixture + triggers a re-audit (fewer findings). (Use the existing `_fake_ctx`/panel-build pattern from `test_sp5_git.py` / `test_proj06_autofix.py`.)
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement the dialog + wire Fix-All** (kit components; `run_populate` for the audit/apply so it stays off the GUI thread in the real app, synchronous offscreen).
- [ ] **Step 4: Run test + `render_gate --surface projects` and Read the PNG to self-audit the dialog's layout/feel.**
- [ ] **Step 5: Commit** (`feat(health): Fix-All fills fields from the Library via a preview dialog`).

---

### Task 8: Verification + dashboard

- [ ] **Step 1:** Full suite green (`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q`).
- [ ] **Step 2:** Flip the dashboard's "Fill component fields from the Library" + "Fix-All actually fixes" rows to `verified` with the real evidence (the end-to-end test + commit refs) in `scratchpad/verification.json`; rebuild + redeploy the dashboard.
- [ ] **Step 3:** Log to the ledger; commit/push.

## Self-Review

- **Spec coverage:** matching (T2), fields+footprint+3D-follows (T3/T4/T5), preview UX (T7), safety .bak/atomic (T4), Fix-All-does-everything (T7), re-audit (T6/T7), proof (T6/T8) — all mapped. ✎ Symbol-swap correctly absent (non-goal).
- **Placeholder scan:** none — every task has real signatures + concrete test intent + named helpers.
- **Type consistency:** `Match`/`FillItem`/`FieldChange`/`FillPlan`/`FillResult` names + fields consistent across T2–T7; property-key map defined once in Global Constraints and referenced.
- **Open risk flagged for the implementer:** confirm `_symbol_spans` does not return the `(lib_symbols)` cache's inner symbols as top-level; if it can, skip blocks with no `Reference` property (Task 4 note).
