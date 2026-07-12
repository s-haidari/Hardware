# CAPABILITIES — backend → UI coverage map

**Why this file exists.** The recurring fault was *forgetting features that already exist
in the code* — shipping them as "missing", re-inventing them, or leaving built backends
unwired. Memory drifts; this file (plus the tools below) is the authoritative,
regenerated source of "what the app can do, and where you reach it", so it never has to
be remembered.

## How to use / regenerate
- Fast first-pass alarm (deterministic): `.venv/bin/python tools/ui/capability_audit.py`
  — lists every public backend function NOT referenced in `tools/ui/bare.py`. Noisy on
  purpose (internal helpers + wrapper-surfaced capabilities show up): **20 modules, ~151
  functions flagged** as candidates. Each must be *accounted for*, not auto-treated as a gap.
- Judged truth (workflow): the `capability-coverage` workflow inventories every module's
  user-facing capabilities, checks each against `bare.py`, and **adversarially verifies**
  each claimed gap (is it reachable under another label / via a surfaced wrapper?). Its
  confirmed list is what goes in "Genuinely unsurfaced" below.

**Rule (see `CLAUDE.md` › No-fault gates):** before building a feature, check here + run
`capability_audit.py`; never assume a capability is absent without grepping the backend;
never leave a real backend capability unsurfaced. Update this file when capabilities change.

## Genuinely unsurfaced — verified by the coverage workflow (2026-07-09, run wf_0e53551b)
194 capabilities across 20 modules; **20 adversarially-verified genuinely-unsurfaced**.
NOTE: 14 of the coverage agents were server-rate-limited that run, so the not-yet-rebuilt
panels' modules (stm32_authority/stm32_db/nd_project_health/nd_board_setup/nd_wizard/…)
were NOT fully coverage-checked — **re-run coverage for those during the Projects/Bench
rebuilds** (known candidates: `lint_card`/`run_lint`, `authority_diff`, `card_materials`,
`package_report`, `audit_report_markdown`).

### Library — NOW SURFACED (commit closing this — the "forgot existing features" fix)
| Capability | What it does | Surfaced as |
|-----------|--------------|-------------|
| `library_health_report` | structural completeness: dangling / missing footprint / model / mfr | **Library Health Report (Structure)** + **Export Library Health…** |
| `process_existing_zips` | import every ZIP waiting in Downloads | **Import All Waiting ZIPs** |
| `process_folder_dialog` (via `move_files`) | import an already-extracted part folder | **Import Extracted Folder…** |
| `clean_leftovers` | delete leftover zips/folders in Downloads | **Clean Downloads Leftovers** |

### Library — Parts picker dedupe (NOW SURFACED)
| Capability | What it does | Where it's surfaced |
|-----------|--------------|------|
| `remove_part` (+ `find_duplicate_footprints`, `_dup_mpns`) | delete a whole duplicate part (symbols, optionally footprint/model files) | **Parts ▸ Manage Duplicates** — multi-select 2+ duplicate rows (Ctrl/Shift+click) or click a row's **Dup** badge → the `DuplicateManagerDialog` side-by-side keep/delete modal; bulk delete loops `remove_part` (the same proven per-part delete the detail uses) and commits once. The row Dup badge + the always-visible "Duplicates only" filter both draw the signal from `_dup_mpns` (shared real MPN) and `find_duplicate_footprints` (byte-identical geometry). |
| `remove_symbols_by_indices` | bulk-remove symbol blocks by file position (aborts on a name mismatch) | Kept as the lower-level primitive (covered by `test_lib_parts_picker.py`). The Manage Duplicates UI deliberately uses the higher-level `remove_part` instead: MPN duplicates are distinct symbol NAMES (one row each), so name-based deletion is unambiguous, and `remove_part` additionally handles the footprint/model files + `still_referenced` safety. Index-based deletion is only needed for two blocks sharing a name, which `scan_library_grouped` collapses into one row — not a case the picker can reach. |

### Projects — NOW SURFACED (Projects workbench rebuild, commit closing this)
The project-centric `_proj_panel` rebuild (▶ Prepare This Project, ▶ Build & Cost, the
noun-first detail card) surfaced every capability the coverage gate flagged:

| Capability | What it does | Surfaced as |
|-----------|--------------|-------------|
| `audit_report_markdown` | shareable markdown audit report | **Export Health Report…** (Workbench group) |
| `conform_schematic_text` | conform schematic text + net-label sizing | **Editor → Text & Silkscreen** — editable size/thickness per object type (silk/fab/copper + sch text/labels), each opt-in via a Conform checkbox; **Preview/Apply Conform** passes pcb_targets + sch_targets over the boards + .kicad_sch sheets |
| `create_vault_standard_template` | canonical vault net-class taxonomy at a fab floor | **Load Vault-Standard Net Classes** |
| `load_vault_standard` / `save_vault_standard` | persisted vault standard | **Load Saved Vault Standard** / **Save As Vault Standard** |
| `export_template` / `import_template` | net-class JSON round-trip | **Export / Import Net Classes (JSON)…** |
| `sync_to_projects` | write net classes into every project's .kicad_pro | **Sync Net Classes To All Projects…** (checkbox-preview, .bak each) |
| `netclass_profiles` | fab-floor option list | drives the Load Vault-Standard fab choice |
| `bom_cost_summary` | priced/unpriced coverage | detail-card BOM row + **▶ Build & Cost** report |
| `bom_diff` (+ `bom_diff_csv`) | full added/removed/changed + CSV | Compare actions now return structured reports; **Export Diff CSV…** |
| `bom_diff_cost` / `bom_diff_lead` | cost + lead-time impact of a change | folded into the Compare reports' summary |
| `bom_cost_by_source` | cost split by distributor | **▶ Build & Cost** report ("Cost by source") |
| `set_board_stackup` / `set_board_thickness` | stackup + thickness (via Write Fab Floor) | shown in the fab-facts row + detail card |
| `autofixable_kinds` / `autofixable` | which gaps Fix-All can resolve | tagged per gap in the card + **▶ Prepare** report |
| `clear_project_cache` (NCM) + `clear_project_cache_files` (PSM) | clear .kicad_prl/.lck/fp-cache + legacy caches | **Clear Project Cache & Locks** (Editor — Save) |

`mark_deleted` (NCM) — **accounted for, internal primitive:** a file-only net class is
removed via **Pull From KiCad Project → Delete Net Class → Save To Project**
(`remove_netclass` marks `deleted_names`, which `save_to_project` drops authoritatively);
`mark_deleted` is the lower-level form of that path, no separate control needed.

### Editor — EXTENDED PSM/NetClass coverage NOW SURFACED (Master Controller M3–M5)
The Projects **Editor** tab (`_pcb_setup_panel`) surfaces the full project-property coverage the
file-rewriter backends support. These are `ProjectSettingsManager` / `NetClass` **methods** (not
module-level functions, so `capability_audit` — which tracks module functions — does not flag
them; they are judged here instead):

| Capability (method) | What it does | Surfaced as |
|-----------|--------------|-------------|
| `save_design_rules_only` / `save_extended` (PSM) | focused "dr" / "dre" .kicad_pro writers | **Editor → ▶ Save To Project** — one flow writes dr/dre/nc/bg/fab, single pristine .bak, per-section dirty-gated |
| `set_drc_severity` / `set_erc_severity` | curated DRC(43)/ERC(30) rule severities | **Editor → Design Rules → DRC & ERC Severities** (Unmanaged/error/warning/ignore combos + filter) |
| `set_track_widths` / `set_via_dimensions` / `set_diff_pair_dimensions` | predefined size tables | **Editor → Design Rules → Predefined Sizes** (Add/Remove tables) |
| `ensure_erc_pin_map` / `set_erc_pin_map_entry` / `set_erc_exclusions` | 12×12 ERC pin-conflict matrix + exclusions | **Editor → Design Rules → ERC Pin Conflict Map** (colour-cycle grid, symmetric) |
| `set_default_netclass` (PSM) | the design's Default net class clearance/track/microvia | **Editor → Design Rules → Default Net Class** (via stays with the flat Via spins → dr, disjoint keys) |
| `NetClass` microvia / diff_pair_via_gap / wire / bus / line_style | full KiCad net-class fields | **Editor → Net Classes** 16-column table (rides `save_to_project`) |
| `set_text_variable` / `remove_text_variable` (PSM) | project ${VAR} text variables, with a working delete | **Editor → Design Rules → Project Meta** (Variable/Value table, Add/Remove, cleared-name = delete) |

**Deliberate deferrals (logged gaps, not omissions — spec §4):** default text sizes for NEW
objects (M1b set-default), per-layer stackup editing, and user fab-preset editing have no focused
backend rewriter yet; each has a defined "done" in the spec. **Text conform** (retroactive object
rewrite) is intentionally its OWN Preview/Apply, separate from ▶ Save (settings), because it
rewrites existing `.kicad_pcb` / `.kicad_sch` objects rather than project defaults.

### Bench — NOW SURFACED (styled Bench parity, commit closing this)
The styled Bench (`features/bench.py`) gained an **Analysis** tab + a Pin-Map SVG export,
closing every capability the parity gate flagged that bare's `_bench_panel` surfaced:

| Capability | What it does | Surfaced as |
|-----------|--------------|-------------|
| `category_lists` | socket-pin-number lists per category (must-switch / osc-optional / fixed / debug / boot / 5 V …) | **Analysis › Category Pin Lists** table |
| `card_materials` | worst-cased per-package passive BOM (ADG714 cells, VCAP/decoupling caps) | **Analysis › Card Passive Materials** (summary + table) |
| `adg714_cell_map` | the must-switch fabric as octal-switch instances (cell → 8 channels, spare/used) | **Analysis › ADG714 Cell Map** (per-cell card + 8-row table) |
| `socket_connections` | every socket pin's path to the parent (middle component, destination, contact) | **Analysis › Socket Connections** table |
| `run_lint` | claim-file drift gate (Build Card asserted numbers vs the authority) | **Analysis › Lint Claim File(s)…** (picker → No-Drift / Drift verdict + report) |
| `pin_map_svg` (stm32_pins_tab) | render the pin-map geometry to SVG | **Exports › Save Pin-Map SVG** |

**Accounted-for, exempt (see `capability_audit._EXEMPT["bench"]`):** the `to_*` serializers
(`to_csv`/`to_kicad_symbol`/`to_markdown`/`to_switchmap_c`/`to_switchmap_json`/`to_wiring_md`/
`to_yaml`) + `serializable` + `raw_tsv` are ALL emitted in one pass by the surfaced
**Exports › Write Authority Bundle** (`write_authority` — verified to write every one). DB
provisioning (`build_database` + `default_cubemx_source`) and `package_count` (status stat)
are machine-setup concerns owned by **Settings**, not per-package bench analysis.

### Settings — NOW SURFACED (styled Settings parity, commit closing this)
The styled Settings (`features/settings.py`) gained a **Machine Setup** section (a live
verdict grid + two ▶ actions), closing every capability bare's `_settings_panel` surfaced:

| Capability | What it does | Surfaced as |
|-----------|--------------|-------------|
| `register_libraries` (LM) | register MySymbols/MyFootprints/${MY3DMODELS} into KiCad's config | **▶ Set Up This Machine** (register + report done/missing) |
| `find_kicad_bin` / `find_kicad_cli` (kicad_paths) | locate the KiCad toolchain | **Machine Setup** verdict rows (KiCad Binary / kicad-cli) |
| `find_kicad_config_dir` (LM) | locate KiCad's config dir (where libs register) | **Machine Setup** verdict row (KiCad Config) |
| `providers_from_config` (LM) | resolve the sourcing providers from config | drives the **Machine Setup** Mouser-ready row |
| `load_config` (LM) | (re)load the live app config | read fresh on every **Machine Setup** card build + before each action (never a stale capture) |
| `build_database` / `default_cubemx_source` / `default_db_path` (stm32_db) | (re)build the MCU database from a CubeMX source | **▶ Rebuild STM32 Database** (closes the bench exemption — this is the DB rebuild's honest home) |

The setup card also reuses already-surfaced status capabilities (`package_count`,
`resolve_digikey_creds`, `library_location`, `_can_write_dir`). `nd_updater` (Get Latest
Version) stays surfaced via the **Check for Updates** bus command (`app.check_updates`).

### Genuinely N/A (verified — do NOT surface)
| Capability | Why N/A |
|-----------|---------|
| `commit_and_push` (LM) | Redundant wrapper — the Git panel uses `nd_git` G.commit+G.push directly; inline edits use `LM.git_commit_push`. |
| `filter_rows` (LM) | The Library panel reimplements filtering inline (`_FACET_PRED` + search over grouped rows) against a different row shape. |

## Known candidates from the deterministic pass (pre-verification)
These are the higher-signal flags from `capability_audit.py` that earlier manual review
judged likely-real (Bench/Projects mostly — the not-yet-rebuilt panels). The workflow
confirms/rejects each:
- **stm32_authority**: `lint_card` / `run_lint` (card DRC), `authority_diff` (compare two
  authorities), `card_materials` (card BOM), `socket_connections`, `switch_rationale`,
  `adg714_cell_map`.
- **stm32_db**: `package_report`, `pin_identity_histograms`.
- **nd_project_health**: `audit_report_markdown` (exportable audit), `symbol_pin_counts`.
- **nd_netclass_manager / nd_board_setup / nd_object_conform**: several (Projects panel — to
  reconcile during the Projects workbench rebuild).

Everything else the deterministic pass flags is predominantly internal helpers
(`parse_*`, `extract_*`, formatters, getters) or work already done by a surfaced
orchestrator (e.g. `register_libraries` runs inside *Make Portable* / *Set Up KiCad
Libraries*; `enrich_library` behind *Enrich Library*). Do NOT treat a raw flag as a gap
without the concept-level check.

## Library Phase B — Components view rebuilt to the library-v2 mockup (2026-07-11, M5–M9)

The two-column Components view (picker | canvas) was rebuilt to match the approved
`library-v2.html` mockup. This changed **UI surfacing / entry points**, not the backend
capability set — every op below already existed and was reachable (Maintenance /
Sourcing-Health subtabs); Phase B adds faster, mockup-faithful front doors and closes the
`price_at_qty` / component-fields gaps.

### NEW / additional surfacing (commits dbe8616 → 71a8990)
| Capability (LM unless noted) | Now reachable as |
|-----------|--------------|
| `find_duplicate_footprints` + `remove_footprint` + `symbols_referencing_footprint` | **Library Tools ▸ Deduplicate Footprints** → the new **Dedup review dialog** (per-group keep/delete cards, keep-most-referenced, live counter) — a review UI the Maintenance subtab's one-shot dedupe lacked |
| `library_sourcing_report` | **Library Tools ▸ Refresh Sourcing** (in addition to the picker's Refresh Sourcing + the Sourcing-Health subtab) |
| `auto_assign_library` | **Library Tools ▸ Auto-Assign Links** (curated entry; also the Maintenance subtab's Auto-Assign) |
| `repair_library` | **Library Tools ▸ Fix Broken Links** (also Maintenance ▸ Repair Footprint And Model Links) |
| `verify_handoff_readiness` | **Library Tools ▸ Integrity Scan** (also Maintenance ▸ Check Hand-Off Readiness) |
| `price_at_qty` | **per-part Sourcing ▸ "Unit at 100" stat card** + the price-break bar graph (was only in the BOM cost path before) |
| `part_completion` (`missing`) | the canvas **still-needs line** (amber pill per missing field) + the picker **N/8** badge + warn triangle |
| Category symbol property (`set_library_symbol_property` "Category") | **Component Fields ▸ Edit ▸ Category** (now an editable field; previously only read by grouping) |
| `part_identity`-shape fields (mpn/mfr/category/datasheet/description) + footprint-derived Package | **Component Fields read-only #idview** (view/edit toggle) |

### Library Part detail + per-part sourcing (2026-07-12, gen [5])

| Capability (LM unless noted) | Now reachable as |
|-----------|--------------|
| `completion_tooltip` (+ `COMPLETION_CHECK`/`COMPLETION_CROSS`) | the **per-dimension ✓/✗ passport** shown on hovering the canvas warn glyph AND every still-needs state (built off `part_completion` so it never drifts) |
| `snapshot_refresh_policy` (+ `MOUSER_REFRESH_MIN_AGE_S`, `snapshot_age_seconds`) | the **Mouser ▾ ▸ Refresh** gate — disabled while a Mouser snapshot is <4h old (shared-cap), LCSC always; reason on hover |
| `sourcing_snapshot_for` + extended `_SNAPSHOT_FIELDS` (source/url/datasheet/category/rohs/price_breaks) | the **cached Sourcing view** restored after relaunch (freshness headline 'Cached Nh ago', full price-break ladder, provider-aware refresh, hyperlinked Mouser P/N) |
| provider `datasheet` field (via `providers_from_config` lookup) | the identity **Datasheet ▸ Find** button (fetch the datasheet link from the distributor, write it through the field seam) |
| `projects_referencing_symbol` | the **Rename heads-up** — the confirm dialog names the projects that instantiate the symbol (informational; they keep a cached copy and won't break) |
| `nav.open` bus event (shell `_open_feature`) | the no-provider Sourcing empty-state's **Open Settings** CTA (cross-feature navigation) |

### Editor enhancements — fab presets, schemes, templates, duplicate (2026-07-12, gen [6])

| Capability | Now reachable as |
|-----------|--------------|
| `nd_fab_presets.load_presets` / `get_preset` / `save_preset` / `delete_preset` / `is_builtin` / `has_user_preset` / `builtin_names` | the **Fab selector + Manage Fabrication Presets modal** (`FabPresetManagerDialog`): New / Duplicate / Edit / Delete over the user fab-preset store; built-ins locked (Save writes a copy-to-override); a profile can now target a custom (non-OSH-Park) fab end-to-end |
| `nd_design_presets.load_severity_schemes` / `get_severity_scheme` / `save_severity_scheme` / `delete_severity_scheme` | the **DRC & ERC Severities → Scheme** row (Strict / Moderate / Relaxed + Save As / Delete); Apply sets every rule severity in one click (confirm-guarded) |
| `nd_design_presets.load_size_templates` / `get_size_template` / `save_size_template` / `delete_size_template` | the **Predefined Sizes → Template** row (Fine-Pitch / Power / Mixed / Hobby + Save As / Delete); Apply refills the track/via/diff-pair tables |
| `nd_netclass_manager.duplicate_netclass` | the net-class table **right-click → Duplicate** (creates `<name>_2`, same dimensions, no patterns) |
| `nd_netclass_manager.validate_netclasses(floor=)` / `floor_from_fab_preset` | the **validate-on-save preview** (below-fab-floor net classes surface as non-blocking amber "acknowledge" rows) AND the correct fab floor for a custom preset |
| `CollapsibleSection.set_dirty` | the **per-section unsaved-change dots** on Predefined Sizes / DRC & ERC Severities / Default Net Class / Project Meta headers (Save preview scope, visible while scrolling) |

### capability_audit accounting (run 2026-07-11)
`capability_audit.py` flags **60 LibraryManager publics not literally named in the UI**.
Judged: **all 60 are internal helpers or plumbing wrappers**, not unsurfaced user
capabilities —
- symbol/footprint low-level writers (`set_symbol_property`, `rewrite_symbol_footprint`,
  `new_symbol_block`, `rename_symbol_block`, `enrich_symbol`, `set_footprint_model`,
  `remove_symbol_by_*`) drive the surfaced edit / autofill / drop-in / delete flows;
- the import pipeline (`expand_zip_to_folder`, `safe_install`, `finalize_import`,
  `remove_part_artifacts`, `process_folder_dialog`, `safe_copy_to_downloads`,
  `wait_file_ready`) runs inside **Import ZIP / Import Extracted Folder**;
- git plumbing (`git_pull`, `git_push`, `git_stage_commit`, `commit_and_push`) is wrapped
  by the surfaced `git_commit_push` (and `commit_and_push` is already listed N/A above);
- path/repo/env plumbing (`detect_repo_root`, `derive_paths`, `bundle_path`,
  `find_kicad_dir`, `ensure_env_var`, `ensure_lib_entry`) and identity/geometry helpers
  (`strict_mpn`, `part_identity`, `footprint_name`, `qualify_footprint`,
  `group_footprint_variants`, `line_extended`) are called by surfaced orchestrators.

No genuinely-unsurfaced user-facing Library capability. Logged Phase-B gap (NOT faked):
**"Similar Components In Stock"** needs a new `similar_parts(row,cfg)` ranking engine — no
backend exists (`Hardware Ideas.md`); M7 omitted the card rather than fabricate it.

## Projects — Complete-Components flow (2026-07-11, owner pivot)

Projects → Health → **▶ Complete All Components** (was "Prepare This Project") completes
every placed component in a project's schematics: link footprint/model + fill identity
data from the library and the distributor chain, group identical passives to fill once,
and prompt for anything left. New capabilities (all surfaced in `features/projects.py`):

| Capability (nd_library_fill) | Surfaced as |
|-----------|--------------|
| `component_completion` / `project_completion` | the Health **verdict** ("N/M components fully filled" + Incomplete chip; green only when all filled + no ERC/footprint errors) |
| `component_model_status` (helper) | the passport's **3D Model** dimension (footprint + model resolved on disk) — rolls into `component_completion(cfg)` |
| `enrich_plan` | **Mouser/LCSC/DigiKey auto-fill** in the flow's off-thread plan build (Mfr/Datasheet/Description for any component with a real MPN; `_headless`-guarded) |
| `passive_groups` / `expand_group_fill` | the preview's **Passive Groups** section (one card per value+package, "Fill all N" → every ref) |
| `merge_manual_changes` | the preview's **Still Needs Your Input** section + passive-group fills (typed values become FieldChanges the existing `apply_fill_plan` writer persists with `.bak`) |
| `match_component` / `build_fill_plan` / `write_fields_to_sheet` / `apply_fill_plan` (pre-existing) | the flow's match → plan → write pipeline |

`capability_audit` flags 3 `nd_library_fill` publics not named in the UI —
`match_component`, `component_model_status`, `write_fields_to_sheet` — all internal
helpers behind the surfaced `build_fill_plan` / `component_completion` / `apply_fill_plan`.
No unsurfaced user capability. Deferred (logged, not faked): a per-passive-group "Search
Mouser" that fills the shared MPN once (needs off-thread dispatch from the modal).
