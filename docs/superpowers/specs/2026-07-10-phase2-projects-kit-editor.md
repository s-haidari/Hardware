# Phase-2 · Projects rebuild + the `kit.editor` third shape

**Status:** design (owner: rebuild Projects onto the recipe at parity; ultracode).
Consolidates the north-star Projects section, spec §10 forward-notes, and the two
finished references (`features/git.py`, `features/library.py`). This doc DESIGNS the new
`kit.editor` shape and the 5-tab Projects information architecture, then the build is TDD.

---

## 1. Why a third shape

The recipe has two shapes today:
- **`kit.workbench`** — selector → verdict → *refresh-in-place detail* → ▶ flow → secondary
  grid → machinery → exports. The detail region is a `RefreshRegion`: every refresh re-runs
  `fill()`. Correct for a **read-verdict-act** surface (Git, Library Health/Maintenance).
- **`kit.panes` / `kit.custom`** — a full-bleed bespoke body (Library Parts splitter).

**PCB Setup** and **Net Classes** are *editors*: their body holds **unsaved user edits** in
live widgets (spin values, a net-class grid, patterns). A `RefreshRegion` is WRONG for them —
any verdict/watchdog refresh would re-run `fill()` and **destroy the in-progress edit**. They
need a body built **once**, never auto-refreshed, whose only rebuilds are **explicit user
actions** (switch profile, add/delete a row). That is the `kit.editor` shape.

## 2. `kit.editor` contract (new, in `ui/kit.py`)

```
editor(ctx, *, title, build_body, verdict=None, primary=None,
       secondary=(), machinery=(), exports=(), busy=None) -> host
```

Body top-to-bottom (same visual grammar as `workbench`, so both read identically):

1. **verdict band** (`W.VerdictSlot`) — the editor's validation state. UNLIKE workbench it is
   NOT auto-recomputed on a timer; it is set by `host._set_verdict(state)` which the feature
   calls after Validate / Save / a load. Quiet (`.set(None)`) when valid or untouched.
2. **the editable body** — `build_body(ctx, host) -> (widget, controller)`. Built ONCE.
   `controller` is an opaque object the feature owns (exposes `snapshot()`, `rebuild()`,
   commit helpers, test seams). The editor mounts `widget` directly (NO `RefreshRegion`,
   NO `scroll_body` wrap around a QSplitter — the feature wraps if it wants scrolling).
3. **▶ primary** — a `PrimaryFlow` (audit → preview → apply → report). For an editor the
   audit lists *what will be written*, apply writes it. Optional (an editor may have none).
4. **2-col secondary grid** — Validate, Pull From KiCad, profile New/Save/Delete, etc.
5. **collapsible Manage** — cache-clear, destructive/rare ops.
6. **collapsible Export** — template exports.

Differences from `workbench`, precisely:
- No `RefreshRegion`; the body is a plain build-once widget + a feature-owned controller.
- The verdict is **push** (`host._set_verdict`), not **pull** (recomputed each refresh).
- `snapshot` for the primary flow reads the **live controller** (GUI-thread widget values),
  so `PrimaryFlow.audit/apply` still run off-thread on a plain dict the controller produced.
- Re-entrancy: the shared `busy` gate still disables the ▶ + secondaries during a Save.

`host` seams: `_verdict`, `_set_verdict(state)`, `_controller`, `_busy`, `_run_primary`
(when a primary is present). Feature adds its own (`_ncmgr`, `_profile_seg`, …) onto `host`.

## 3. Projects IA — 5 tabs on a shared project selector

`ProjectsFeature.build` keeps `ProjectsState` (discovery, selection, `on_change`), but the
selector moves into the **Workspace header** (`W.Workspace(..., header=selector)`), and
`state.on_change(ws.rebuild_all)` re-derives EVERY tab on a project switch (spec §2.C, the
top tier). Panels never self-register on `state.on_change` (leak guard, unchanged).

| Tab | Shape | Verdict | ▶ primary | Notes |
| --- | --- | --- | --- | --- |
| **Overview** | workbench | project readiness (audit + ERC/DRC + git ahead/behind + "next step"), quiet when ready | *(none — a browse/verdict tab is legal)* | Reuses the audit; a "Next Step" line + Open-in-KiCad / reveal machinery. |
| **Health** | workbench | findings count (quiet when 0), top-severity chips | **▶ Prepare This Project** = Fix-All (annotate + fill-from-Library) → re-audit → before/after → Restore Last Prepare | ERC/DRC as secondaries; `audit_report_markdown` export; `autofixable`/`autofixable_kinds` drive the preview pre-check. |
| **BOM & Procurement** | workbench | build status / cost rollup (`bom_cost_summary`), blockers as chips | **▶ Build and Cost** (build → price → cost/lead report) | Export menu + Compare menu as secondaries; Boards/Spares live spins in the detail; Consolidated multi-select. |
| **PCB Setup** | **editor** | Validate status (quiet when in spec) | **▶ Save To Project** (write design rules + board geo + fab floor) | Design-rules grid + board-geometry grid + fab facts + Conform; Profile picker; units toggle. |
| **Net Classes** | **editor** | Validate vs fab floor (quiet when valid) | **▶ Save To Project** (write net classes) | The master-detail net-class grid (inline rename, colour, diff-pair-0=off), vault-standard load/save/sync, profile CRUD, Pull From KiCad. |

**Refactor** (find/replace · add tag · strip · unannotate across sheets+boards) is a real
capability with no north-star tab. Decision: keep it as a **6th tab** ("Refactor", `kit.custom`
op-form) so nothing is omitted — the over-engineering pass can reconsider folding it. Confirm
against the bare parity target; if bare surfaces it, a tab is the safe parity choice.

**PCB Setup / Net Classes split:** currently ONE tab (net classes = Section C of PCB Setup).
The north-star splits them. Both are editors over the SAME project; they SHARE the profile +
unit state. To avoid two editors fighting over one `.kicad_pro`, the profile/unit state lives
on `ProjectsState` (or a shared holder the two tabs read), and **Save To Project on either tab
writes its own slice** (`save_design_rules_only` + board geo + fab on PCB Setup;
`mgr.save_to_project` + profile-sync on Net Classes) — the existing backend already writes
slices independently, so this is a clean split, not a rebuild of the write path.

## 4. Parity — surface all 13 omissions

- `nd_project_health.audit_report_markdown` → Health **Export** ("Audit Report (Markdown)…").
- `nd_project_health.autofixable` / `autofixable_kinds` → drive the Prepare preview pre-check
  (auto-fixable findings pre-checked; the rest shown but unchecked).
- `LibraryManager.bom_cost_summary` → BOM verdict / summary rollup.
- `nd_netclass_manager.netclass_profiles` → Net Classes profile-tier picker.
- `create_vault_standard_template` / `load_vault_standard` / `save_vault_standard` → Net
  Classes: "Load Vault Standard" (tier picker), "Save As Vault Standard" secondaries.
- `clear_project_cache` (ncm) / `clear_project_cache_files` (psm) → a "Clear KiCad Cache"
  machinery action (both, deduped) on PCB Setup / Overview.
- `mils_to_mm` / `mm_to_mils` → already used internally by the spins; the units toggle IS the
  surfacing. Confirm the parity harness counts the toggle (else add a note like Git's).
- `kicad_paths.find_kicad_cli` → surface a "KiCad CLI: <path|not found>" line on Overview (the
  ERC/DRC readiness already depends on it) — makes detection visible (over-engineering ask).

## 5. Gates (repo CLAUDE.md ## No-fault, ALL required, per tab)
1. **parity projects → 0** (`capability_audit.py --parity`) or each remaining omission
   justified as an internal helper in the ledger.
2. **drive_audit** extended: a `audit_projects_workbench()` that DRIVES the styled 5/6-tab
   Projects — switch project (the segfault path), drive each tab's primary headlessly, assert
   the editor grids survive a verdict set, assert forgotten-cap buttons present.
3. **suite green** — rewrite the coupled tests (`test_sp4_projects.py`, `test_wave1_pcb09_ui`,
   `test_pcb12_pull_profile`, `test_netclass_profiles`, `test_proj05_health_project`,
   `test_backend_project_settings`, `test_audit_netclass`, `test_audit_project_settings`) to
   the new structure; keep every backend-contract test (they test the engine, not the UI).
4. **render Read BOTH themes** (`render_gate.py --surface projects`).
5. **honest completion** — Linux/offscreen ≠ Windows/real-library (the release gate).
6. **adversarial review workflow** before committing the whole rebuild (caught the Library
   dead wire + 5 Git defects).

## 6. Build order (each INDEPENDENTLY gated: unit test + suite + render Read; commit each)
1. `kit.editor` + tests (RED→GREEN, headless-safe, leak-free, busy-gated). ← keystone
2. PCB Setup on `kit.editor` (design rules + board geo + fab + Conform + Save flow).
3. Net Classes on `kit.editor` (net-class grid + vault standard + profile CRUD + Save flow).
4. Health on `kit.workbench` (▶ Prepare This Project + Restore + ERC/DRC + md export).
5. BOM & Procurement on `kit.workbench` (▶ Build and Cost + export/compare + multi-select).
6. Overview on `kit.workbench` (readiness verdict + next step + KiCad-CLI line + cache clear).
7. Refactor tab (kept; kit.custom op-form) — parity-driven.
8. Shared header selector + `rebuild_all` wiring; parity → 0; drive_audit; render Read.
9. Adversarial review; fix confirmed defects; commit + push; ledger + idea tracker.
