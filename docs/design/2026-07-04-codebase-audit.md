<!-- Generated 2026-07-04 by the netdeck-codebase-audit workflow (15 agents). Source of truth for correctness + convenience work. -->

# NETDECK Codebase Audit & Convenience Roadmap

## 1. Executive summary

NETDECK is functionally rich — the three tabs, git sync, the switch-fabric authority generator, and an interactive single-model 3D viewer all already exist. The problem is not missing features; it is **silent data corruption and unhandled failure on the paths that matter most**. Three classes of risk dominate:

1. **The shared symbol library and every KiCad-file rewrite path are unsafe.** An S-expression scanner that infinite-loops on escaped quotes freezes the whole app; a malformed empty-library template emits invalid `.kicad_sym`; there is no conflict-marker/paren validation before merge or `git add -A`; and **the currently committed `libs/MySymbols.kicad_sym` already contains git conflict markers** — KiCad cannot load it today, and every auto-pull propagates the corruption. The bulk-rename engine (`nd_wizard`) is non-atomic, clobbers its own backups, and can mangle net names.

2. **The switch-map data the bench exists to produce is electrically wrong for some pins.** `stm32_authority` picks the wrong ADG714 channel as "representative," so CSV/MD tell an operator to close a switch that ties the socket pin to analog ground instead of VTARGET (LQFP100 pins 10/19/20/27/49). Independently, `stm32_db` routes `VREF-`/`VREFSD-` to the *positive* reference rail. Anyone actuating switches from these exports risks connecting a target's ground reference to a supply.

3. **Two "sync" tools silently do nothing while reporting success.** The project-settings manager writes vias/junction/text/solder keys to `.kicad_pro` paths KiCad does not read, then "verifies" by re-reading the same dead keys (deceptive green). The net-class manager wipes unmanaged classes' net assignments despite promising to preserve them. Both promise a `.bak` that is never written.

**Top risks:** (a) app hang/crash on ordinary input (escaped quote, locked file); (b) committed-library corruption spreading via git; (c) wrong switch actuation from exports; (d) settings/net-class syncs that lie about success.

**Biggest opportunities:** a single "Repair/Validate Library" self-heal + a serialized library/git write queue would turn today's fragility into robustness; a schema-driven settings engine (preserve-by-default deep-merge) would fix the data-loss bugs *and* unlock near-total Board/Schematic Setup coverage at once; and a `kicad-cli`-based board render/export gives an in-app full-board 3D view with zero new dependencies.

---

## 2. Bugs & correctness issues (ranked, deduped)

| Severity | File | Location | Issue | Fix |
|---|---|---|---|---|
| **Critical** | LibraryManager.py | `extract_symbol_blocks` L258-283 | Balanced-paren scanner infinite-loops on any block it can't balance; string skip ignores KiCad `\"` escapes (produced for inch marks like `0.1"`), and on inner-loop exhaustion `i` never advances so the same block re-scans forever. Freezes GUI at startup/refresh (force-kill) and hangs import worker. | Honor `\`-escape in string skip; guarantee forward progress: on `depth!=0` set `i=start+1`/break. Apply same escape handling to top-level skip and `extract_symbol_name`. |
| **Critical** | nd_wizard.py | `strip_all_tags` L195-209 via `transform_label` | Strip-All on labels mangles net *names*: grabs first `<letter><digit>` substring and drops everything before it (`I2C1_SDA`→`C1_SDA`, `USART2_TX`→`T2_TX`), corrupting/merging/splitting nets while still "applying successfully." | Never run designator-scan strip on labels; for labels strip only the recognized `/^[A-Z]{1,3}-/` tag prefix. Restrict designator logic to component refs. |
| **Critical** | kicad_tools.py | `_run_rename.work()` L452,458,470-482 | `changes` is never defined but `if changes:` / iteration run every Preview and Apply → `NameError`. Not caught (try/except is *inside* the block). On Apply files are already mutated; standalone GUI has no ctx so it runs on the GUI thread and the unhandled exception can `qFatal`/abort the app mid-write. | Init `changes=[]`; capture the discarded 3rd return (`counts,smp,rec=…; changes+=rec`); wrap audit block in try/except. |
| **High** | stm32_authority.py | `build()` switched branches ~L1117-1126 | Representative channel = `chans[0]` (ordered by identity part-count), but `destination` = dominant non-VSS role — they can refer to different channels. CSV/MD actuate the wrong ADG714 terminal (socket pin → analog ground instead of VTARGET). Verified LQFP100 pins 10,19,20,27,49. **Electrically dangerous.** | Pick the channel whose `destination == d.primary_target_net`, fall back to `chans[0]`. |
| **High** | stm32_db.py | `electrical_class`/`roles` L147-185; `switch_identity` L400-404 | `VREF-`/`VREFSD-` classified as positive reference → `ID_VREF`/`VREF_TGT`; must tie to analog ground (VSSA). Verified LQFP144 p31, LQFP100 p20/p48. Wiring a part's neg-ref pin to the positive VREF rail = electrical fault; silent (doesn't change must_switch counts). | Detect trailing `-`; map to ground/`ID_VSS`/GND or add a negative-ref role resolving to GND. |
| **High** | LibraryManager.py | Empty-lib template L183,247,317,336,367,402,597,656 | Template is paren-imbalanced (3 opens/4 closes) → every fresh-create/full-rewrite emits `.kicad_sym` with a stray trailing `)`. Invalid S-expr; strict parsers reject the whole library. | Remove one `)` after the generator so the header leaves the lib open and the second line closes it. |
| **High** | LibraryManager.py | `git_stage_commit` L976-985; `.gitattributes` | No conflict-marker / S-expr validation before merge or `git add -A`. `.kicad_sym` is git-mergeable, so 3-way merge injects markers that the app blindly commits/pushes. **Live committed `libs/MySymbols.kicad_sym` already has `<<<<<<< / ======= / >>>>>>>`.** Dedup keeps the FIRST side, silently discarding local edits. | Scan for `^<<<<<<<\|^=======\|^>>>>>>>` and paren-balance before commit; refuse with a log error; mark `*.kicad_sym -merge` (and `-text`) in `.gitattributes`; clean the committed file. |
| **High** | stm32_pins_tab.py | `_export()` write L1636 | Unguarded `write_text`; no global `sys.excepthook`. Re-exporting to a name Excel holds open → `PermissionError` → whole app terminates with no dialog (every other writer here is guarded). | Wrap `fn()`+write in try/except + `QMessageBox.warning`; append extension if missing. |
| **High** | nd_wizard.py | Apply loop L1284-1306; writes L1035-1038,1093-1096 | Apply is non-atomic with zero error handling. On Windows a KiCad-locked file (`PermissionError`) or bad UTF-8 aborts mid-loop → project half-renamed, exactly the inconsistent state the tool should prevent. Dry-run (read-only) shows clean. | Stage all transformed contents in memory; write only if every file succeeds (all-or-nothing), else roll back from `.bak`; per-file error reporting. |
| **High** | kicad_tools.py | `_run_erc.work()` L499 → `pick_top_schematic`/`prompt_menu` | Run ERC calls an interactive CLI helper that `input()`s. In GUI (pythonw/packaged) `input()` raises outside the try; with a console it blocks a worker thread forever on a prompt the user can't see. ERC never actually runs. | Auto-pick root schematic (stem == `.kicad_pro`) non-interactively; drop the interactive helper; fix the misleading comment. |
| **High** | nd_project_settings_manager.py | `save_to_project` L410-411, `load` L297-299, `_verify_saved` L520-522 | `default_via_diameter/drill` read/written to `design_settings.via_diameter/via_drill` — keys KiCad doesn't use (real value lives in `net_settings.classes["Default"]`). Writes are no-ops; verify re-reads the same dead key and reports "✅ verified." Deceptive success. | Write/read the Default netclass (as NetClassManager does) or drop these fields; never mark a field verified unless written to the key KiCad consumes. |
| **High** | nd_project_settings_manager.py | `save_to_project` L313-316; GUI promise kicad_tools L822 | Sync dialog says "A .bak is written next to each" and calls `sync_to_projects(backup=True)`, but `backup` is never used — no `.bak` ever created; write is destructive atomic `os.replace`. No undo. | Honor `backup` (copy to `.kicad_pro.bak` before replace) or remove the param and fix the dialog text. |
| **High** | nd_netclass_manager.py | `save_to_project` L360-369 | "Safe merge" preserves unmanaged class *definitions* but unconditionally rebuilds `netclass_patterns` from managed classes only → every unmanaged class's net assignments are deleted; those nets fall back to Default. Contradicts the documented guarantee. | Carry over existing patterns whose `netclass` is in `last_preserved_unmanaged` or `Default`, then append managed patterns. |
| **High** | nd_netclass_manager.py | `export_template`/`import_template` L411-446 | Template writer+reader omit `priority`, `microvia_diameter/drill`, `diff_pair_via_gap` (all real GUI columns). Save-vault→load-vault is lossy: priorities collapse to 0 → ambiguous netclass precedence. | Add the four fields to both export and import (with NetClass defaults). |
| Medium | LibraryManager.py | Watcher L1046; zip workers; pull timers; `run_git` L937 | No serialization of shared-library writes or git. Watcher spawns a daemon thread *per* zip each committing; concurrent read-modify-write of `MySymbols.kicad_sym` + concurrent git colliding on `index.lock`. Startup pull + 5-min timer + drops overlap. | Serialize all library-mutating and git ops behind one worker queue; batch watcher zips into one pass + one commit. |
| Medium | LibraryManager.py | `change_path` L2070-2098; `load_config` L160-169 | RepoRoot change is never persisted (load_config re-derives from exe/script), and `watcher.cfg`/`log` captured old paths → watcher watches old Downloads, logger writes old file. Half-switched state; reverts on restart. | Persist RepoRoot in config.json with writable/exists fallback; rebuild watcher + re-point log on change. |
| Medium | LibraryManager.py | `insert_blocks_into_target` L304-318 | Insertion-point paren scan counts parens inside quoted strings; a description like `"smiley :)"` drives depth to 0 early and splices new blocks mid-symbol. | Make the depth scan skip quoted strings (with `\`-escape), mirroring the fixed `extract_symbol_blocks`. |
| Medium | stm32_pins_tab.py | `_apply_filter` L1594-1600 | Table Search haystack omits `assignment.destination/net` and the Switch label, both shown on screen. Typing a visible destination (`VTARGET`, `CARD_LANE_042`) hides every row → "0 pins," implying data absent. `ConnectionsList._haystacks` already indexes these. | Add destination + switch label to the joined haystack. |
| Medium | stm32_pins_tab.py | `_select()` L1304-1320 | Selection sync is one-directional: Map/diagram clicks never select the table row; Map→Table disagrees. | Resolve row via col-0 UserRole, block signals, `selectRow`+`scrollToItem`, unblock. |
| Medium | stm32_authority.py | `to_kicad_symbol` L1279-1280 | Fixed-IO `assignment.net` == `"CARD_LANE"` (truthy) so the per-pin fallback is dead code → 53/100 LQFP100 pins all named `CARD_LANE`, defeating per-lane identity in the generated symbol. | `if net in (None,"","CARD_LANE"): net=f"CARD_LANE_{position:03d}"`. |
| Medium | stm32_db.py | `build_database` L317; dead `expand_ref_names` L96 | Multi-variant CubeMX ref names stored verbatim; 245/424 rows collapsed (`STM32F031C(4-6)Tx`). Exact-MPN lookup fails for 58% of parts; true inventory undercounted. | Expand ref names on insert (one row per MPN) or add an alias column; update the count assertions in tests. |
| Medium | nd_wizard.py | `write_text` L1038,1096 | No `newline=` on write; universal-newline read makes LF, write re-translates to CRLF on Windows. One tag rename flips the entire file LF→CRLF → every line shows as changed in git; `.bak` diff is total. | Write with `newline=''` to preserve LF. |
| Medium | nd_wizard.py / kicad_tools.py | `.bak` L1036-1037,1094-1095 | Fixed `.bak` name overwritten every run; a second run copies the already-modified file over the pristine backup, destroying the only safety net. (Same core reached via kicad_tools rename.) | Timestamp the backup (reuse `timestamp`) or refuse to overwrite; better, back up into the timestamped `LOG_DIR`. |
| Medium | nd_wizard.py | lib_symbols depth L932-943 gating L1005 | `(lib_symbols …)` paren counter includes parens inside strings; a stray paren in a Description/URL desyncs it → either template refs inside symbol defs get renamed (corrupts embedded lib) or no refs rename at all while labels still do (inconsistent). Both silent. | Token-scan ignoring quoted-string parens, or parse instance symbols structurally. |
| Medium | kicad_tools.py | `_nc_sort_by_priority`/`_nc_manager_from_table` L590-592,660-703 | "Sort by Priority" round-trips through table reconstruction that mutates data: duplicate names collapse (click Add twice → one row vanishes), empty-name rows dropped, blank numeric cells back-filled with hard defaults. Same lossy path runs on every Sync/Export. | Reorder existing QTableWidget rows in place; if reconstructing, warn on dup/empty names and keep blanks blank. |
| Medium | kicad_tools.py | `_nc_sync` L718-725, `_ps_sync` L815-830 | Sync reports only a bare count; rich per-project reasons (`last_sync_details`, skipped-open `.lck`, unverified) are discarded — user sees "3/5" with no why. `_ps_sync` runs on the GUI thread (unlike the others) → freezes UI. | Log `last_sync_details` + netclass results per project; dispatch `_ps_sync` through `_run_heavy`. |
| Medium | nd_project_settings_manager.py | junction L251,350 | Junction written as `default_junction_size` (raw mils); KiCad uses `junction_size_choice` (enum index 0-4). Key is ignored; spin box is inert. | Map to `junction_size_choice` int enum, or drop the field. |
| Medium | nd_project_settings_manager.py | text L257-260,374-376 | `defaults.text_size_h/v/thickness` — KiCad's generic PCB text defaults are `other_text_size_h/v` / `other_text_thickness`. Writes are no-ops; loads always return fallback. | Rename keys to `other_text_*` (verify against a KiCad-written file). |
| Medium | nd_project_settings_manager.py | L282-283,397-398 | Fields labeled "Design Rules (Defaults)" (`default_clearance`/`default_track_width`) actually map to `rules.min_clearance/min_track_width` (DRC minimums, not routing defaults) and overlap NetClassManager. Misleading. | Relabel as "Min Clearance/Track Width" or write true defaults into the Default netclass. |
| Medium | nd_project_settings_manager.py | L302-303,414-415 | `solder_mask_clearance`/`solder_paste_margin` written to `.kicad_pro board.design_settings`; these live in `.kicad_pcb` setup. Writes ignored. | Confirm location; edit `.kicad_pcb` setup or drop the fields. |
| Medium | nd_netclass_manager.py | `main_cli` L585-593 | `--sync-to` alone with an empty manager treats all non-Default classes as unmanaged and replaces `netclass_patterns` with `[]` → silently unassigns every net (GUI guards this; CLI doesn't). | Error out if `not manager.net_classes`; `save_to_project` should refuse to overwrite patterns when empty. |
| Medium | nd_netclass_manager.py | `_hex_to_rgba` L204-210 | No hex validation; `#abc`/empty/named color raises `ValueError` out of `to_kicad_dict`, aborting the whole project's save with only a lower count shown. Color cells are user-editable. | Strip `#`, expand 3-digit, validate 6 hex, fall back to `#808080` on failure. |
| Medium | fp_render.py | `load_step_mesh`/`render_step_image` L470-587 | 3D path is STEP-only; callers feed `.wrl` (LibraryManager L605,770,1108) straight into cascadio's STEP reader → raises, caught, returns None silently. KiCad's default models are `.wrl`, so most models render blank. | Branch on suffix: load `.wrl/.vrml` via trimesh/VTK; cascadio only for `.step/.stp`; surface an "unsupported format" state. |
| Medium | kicad_paths.py | `find_kicad_bin` L18-22 | Installs chosen by lexicographic string sort: `10.0` sorts before `9.0` → drives the *older* KiCad; also mixes x86/64 globs by path string. Every `kicad-cli` call can target the wrong version. | Sort by parsed version tuple and `max()`; resolve both globs first; consider the registry. |
| Medium | ui_widgets.py | `_Readout` L147-179 | Accent color string frozen at construction; on theme toggle the "Selected" dot keeps the old theme's accent (vanishes: light-gray on light band). | Store accent as a token key/callable and re-resolve in `restyle()`. |
| Medium | fluent_theme.py | `status_color` L47-50 | Derives light/dark from `isDarkTheme()`, a different source of truth than `ui_theme` (which the app toggles directly). Badges render the wrong variant once wired in. | Use `ui_theme.is_dark()`; or route `_apply_theme` through `apply_grayscale_fluent()`. |
| Low | stm32_pins_tab.py | `load()` L1471-1481 | If `sauth.build` raises, previous authority + views stay while `pkg_combo` shows the new package → displays LQFP64 data under an LQFP100 label. | Reset views to empty or revert the combo (signals blocked) on failure. |
| Low | stm32_pins_tab.py | `_populate_packages` L1378-1386 | sqlite conn closed inside try with no finally; on error the handle leaks and `_packages_populated` stays unset → re-runs and leaks a handle each build. | try/finally `conn.close()`. |
| Low | stm32_pins_tab.py | `pin_map_geometry` L295-336 | Assumes 4-sided QFP divisible by 4; combo offers every package_name → non-QFP/odd counts overrun the body (garbled map). `lqfp_side` shares the assumption (mislabels Side column). | Restrict combo to QFP or distribute remainder / show "map unavailable." |
| Low | stm32_authority.py | `_breakout_map` L480 | Osc pins labeled `fixed_direct` though they're switched through ADG714 on per_role cards. Advisory field only. | Base `via` on whether the position received channels. |
| Low | stm32_authority.py | `to_switchmap_c` ~L820-834 | Zero-channel package emits empty `enum {}` / array `{}` (not valid ISO C; GCC tolerates). Unreachable for current parts. | Guard the empty case with a placeholder or skip. |
| Low | stm32_db.py | `pin_identity_histograms` L576 | Folds role counts with `max` instead of distinct-MCU union → understates IO/OSC MCU counts. Affects only the informational dominant-label/minority flag; switch decisions unaffected. | `COUNT(DISTINCT mcu_id)` per identity. |
| Low | stm32_db.py | `build_database` L305-308 | Any unparseable XML is silently skipped; `BuildResult` counts just shrink with no signal. | Collect skipped files/errors on `BuildResult`; surface in UI. |
| Low | nd_wizard.py | summary L1330 | `LOG_DIR.relative_to(repo_root)` unguarded (other prints are wrapped) → `ValueError` traceback *after* files are modified when cwd isn't an ancestor. | Wrap in the same try/except fallback. |
| Low | nd_wizard.py | `should_ignore_path` L67-70 | Tests every component of the absolute path for `.`-prefix; a hidden *ancestor* dir makes all discovery return "No KiCad files found." Env-dependent. | Test only components relative to the search root. |
| Low | kicad_tools.py | `_nc_load_project`/`_ps_load` L633-642,803-813 | "Load from Project" uses `selected_pro_files()[0]` (alphabetically first) silently when several selected. | Prompt for which project or require exactly one selection. |
| Low | kicad_tools.py | `_ps_sync` L826-827 → save L350 | `junction_size` (int field) written from a `QDoubleSpinBox` float → `.kicad_pro` gets `36.0`. Value round-trips but KiCad expects int. | `int(round(...))` for integer settings. |
| Low | nd_project_settings_manager.py | `check_project_locked` L224 | Substring test: stem `Main` matches open `Main_v2.kicad_pcb.lck` → false SKIP blocks a legit sync. | Match `lck.name == stem+'.kicad_*.lck'` precisely. |
| Low | nd_project_settings_manager.py | mm↔mils round-trip L282/397 | Every mm value forced through a 0.1-mil grid: `0.2mm`→`0.2007mm`, `0.8mm`→`0.8001mm`. First sync silently drifts design rules; no-op syncs rewrite drifted numbers. | Store/compare in native mm/nm; skip a key when it round-trips to the same mm. |
| Low | nd_project_settings_manager.py | `_clear_project_cache` L451-452 | Builds `Master.lck`; real locks are `Master.kicad_pcb.lck`/`.kicad_sch.lck` (as `_clear_local_cache` knows). Direct saves don't clear locks. | Reuse `_clear_local_cache`'s sibling list. |
| Low | nd_netclass_manager.py | `from_kicad_dict` L170-182 | `wire_width==0` (KiCad "inherit") read as 0mm; int 1-2 fails `>2` test → treated as mm. | Treat any int wire/bus width as mils; map 0 → default. |
| Low | fp_render.py | `_Footprint._parse` L126,133-152 | Graphic width read from flat `(width X)`; KiCad 7/8/9 nest it in `(stroke (width X))`, so `float()` fails → every line falls back to 0.1mm; circles/rects/polys don't attempt stroke at all. | Helper returning width from flat or nested stroke; use for all shapes. |
| Low | fp_render.py | `_Footprint._parse` L103-152 | `fp_arc`/`gr_arc` never parsed → curved silk/courtyard (pin-1 arcs, rounded corners) dropped from thumbnails. | Parse arcs; approximate as polyline/`arcTo`; include in bbox. |
| Low | fp_render.py | `render_symbol_image` L353-356,415-416 | Symbol arcs captured as (start,end) only and drawn with `drawLine` (straight chord) → inductor humps, diode curves distorted. | Capture mid point; render via `QPainterPath`. |
| Low | fp_render.py | `_suppress_native_stderr` L441-467 | Redirects process-wide FDs 1/2 around cascadio; catalog builds off-thread, so other threads' stdout/stderr is swallowed during conversion. | Serialize model loads under a lock while redirected; scope narrowly. |
| Low | fp_render.py | `load_step_mesh` L477 | `NamedTemporaryFile(delete=False).name` relies on refcount to close the handle before cascadio writes → Windows sharing-violation risk; leaks temp `.glb` if unlink fails. | `fd,glb=mkstemp(...); os.close(fd)`; best-effort unlink. |
| Low | ui_widgets.py | `Rail.restyle` L113-141 | `Rail.restyle` is a no-op and keeps no ref to group `SectionHeader`s → "Operations/View" captions + hairlines keep stale inline colors after theme toggle. (Bare `SectionHeader('Output')` in kicad_tools L236 goes stale too.) | Track created headers and restyle each; give `SectionHeader` automatic theme following. |
| Low | ui_widgets.py | `Rail.add_item` L120-127 | First `add_item` calls `select()`→`selected(key)` before any caller has connected → initial view's side effects (header text, lazy build) never fire; only works because stack defaults to index 0. | Defer initial `select()` to an owner-called `rail.select(key)` after connect, or an `emit=False`/commit path. |
| Low | merge_symbols.py | `main()` L44-45 | Returns 0 unconditionally even on "ERROR writing merged symbols"/"No symbols found"; PS caller ignores exit code → failed merge silent to import pipeline. | try/except, print summary, return nonzero on error/no-op; check `$LASTEXITCODE` in the ps1. |

---

## 3. Completeness gaps by subsystem

**Shared library & git (LibraryManager)**
- No conflict-marker / S-expression validation anywhere (the live committed file is already broken).
- `Process Folder…` runs `rglob` + `shutil.move` on every non-CAD file into `misc/` with no confirmation, dry-run, or undo — a wrong folder relocates unrelated files.
- Deleting footprints/models leaves dangling `MyFootprints:<name>` links + orphaned models in referencing symbols, and deletes aren't committed (only imports are) → drifting git state.
- `register_libraries`/`ensure_lib_entry` only *add* a missing nickname; a stale URI (repo moved) is never corrected → parts silently fail to resolve.
- No handling for un-extractable symbol names; dedup keeps the FIRST by name → genuinely different symbols sharing a name silently dropped.
- `ui_python.log` grows unbounded (no rotation); log pane force-scrolls to bottom even when the user scrolled up.
- `refresh_library`/`scan_library` run synchronously on the GUI thread.

**STM32 pins / authority / db**
- Export and Save-to-Vault are hardcoded to `('LQFP64','LQFP100')` regardless of loaded package/DB; both iterate in one try, so one failing package aborts the whole batch.
- The 5V "analog-mode" caveat is computed but only "osc-mode" is surfaced in `_pin_detail_html`.
- The machine-readable exports the board exists to produce — `to_switchmap_c` (firmware header), `to_switchmap_json`, `to_wiring_md` — are not exposed in the tab.
- Nav Rail has no initial selection (view vs highlighted-view start out of sync).
- NC-only positions are dropped (would under-count `positions_total` and gap the socket symbol pin numbers for any all-NC position).
- Multi-branch (per_role) pins show only one branch in the connections view/CSV/MD; the second destination never surfaces.
- `conflict_nets` omits `SERVICE_OSC_OUT`; no assertion that connector-contact assignments are collision-free.
- No per-pin 5V-tolerance data (FT/FTf lives in the GPIO IP XML, not the MCU XML).
- BGA/WLCSP packages silently build with 0 pins; no signal to the caller.
- `VDDUSB`/`VLCD`/other supply rails collapse into `ID_VDD`/`VTARGET`.

**KiCad Tools (rename / net-class / settings)**
- Unannotate is fragile: needs a newline between `(symbol` and `(lib_id …)`; single-line schematics build no lib_id map and silently do nothing (WARN only to stdout).
- PCB unannotate has no lib_id → heuristic on ref string; the code comment says "GUI should warn" but it never does.
- Rename does no result validation (can produce empty/duplicate refs); no post-Apply re-parse/ERC to confirm the schematic still loads.
- Net-class sync has no `.lck` check, no verify, no cache clear (unlike settings sync) → syncing into an open project is silently reverted by KiCad while the GUI reports success.
- Settings coverage is ~25-30% of Board+Schematic Setup and only what lives in `.kicad_pro`: **missing** DRC severities (`rule_severities`), all of ERC (`erc.rule_severities`, `pin_map`, exclusions), `text_variables`, predefined size tables (`track_widths`/`via_dimensions`/`diff_pair_dimensions`), most `design_settings.defaults` (layer line widths, dimension formatting, zone defaults, apply-to-fp flags), stackup/layers, page/title-block, plot/gerber params. The **Default net class is uneditable** (`load` skips it). Existing board/schematic items are never retro-fitted — only future-item defaults change, contradicting the "standardize drawings" implication.
- Masked-missing-key problem: every `load` uses `.get(key, default)` equal to the field default, so a missing/wrong key is indistinguishable from a genuine default. Verify only re-checks 6 of the written values.

**3D / rendering (fp_render)**
- No VRML/WRL support (KiCad's default format) — root cause of blank previews.
- No board-level render; single footprint/symbol/model only.
- Missing `fp_text`/refdes/pin-1 markers, custom-pad `(primitives …)` (drawn as bounding rect), `roundrect_rratio` (hard-coded 0.25), and `summary()['layers']` omits pad/silk layers.
- No footprint→model resolver (`${MY3DMODELS}` expansion), so the viewer can't go footprint→3D on its own.
- `paint_mesh` is a pure-Python painter's-algorithm rasterizer (re-projects per triangle, no z-buffer, no perspective) — fine for a thumbnail, laggy for large meshes.

**Shared kit**
- `kicad_paths` only globs `C:\Program Files[ (x86)]\KiCad\*\bin` — other drives, portable/MSYS, per-user installs invisible unless `KICAD_BIN`/`PATH` set; no registry lookup.
- `merge_symbols` has no nonzero exit path and the PS caller ignores exit codes.
- `fluent_theme` imports `qfluentwidgets` at top level with no guard (hard crash if absent); no `HAVE_FLUENT` flag.
- `TabContext.run_async` defaults to `None` while its docstring presents it as always-callable → `NoneType is not callable` in standalone/test use.
- `lucide_icon` caches an empty `QIcon()` on any failure keyed by `(name,color,size)` → permanently poisons a legitimate icon once a cold call fails.

---

## 4. Convenience & power-user opportunities

**Library / git**
- One-click **Repair/Validate Library**: strip conflict markers, rebalance parens, remove the stray `)`, report duplicates — converts today's corruption into self-heal.
- Debounce/queue imports so multiple dropped zips + the watcher coalesce into one processing pass + one commit/push with a progress summary.
- Auto-commit deletions (or "Delete + Commit"); when deleting a footprint/model, offer to remove the referencing symbol link (or warn about the dangling link).
- "Open in KiCad" should launch the actual Symbol/Footprint editor, not `os.startfile` (which opens `.step` in a generic viewer).
- Dry-run preview for `Process Folder…` (merge vs move-to-`misc/`) before touching anything.
- Detect/repair stale KiCad library registration (URI mismatch), not just add-if-missing.
- Proper Settings panel exposing RepoRoot/Downloads/Libs, keep watcher+logger in sync, add log rotation.

**STM32 tab**
- Guard `_export()` like `generate()`; append the correct extension.
- Make Table Search cover on-screen Destination + Switch label (mirror `ConnectionsList._haystacks`).
- Bidirectional selection sync across map/diagram/table/inspector.
- Drop or lazy-build the hidden `ConnectionsList` (fully rebuilt every load, never shown; recomputes `card_wiring` three times).
- Derive Export/Vault package set from the DB (current-package vs all), build each package in its own try, and expose the switchmap C/JSON + wiring-MD exporters.

**Rename / settings / net-class**
- Atomic Apply (transform-in-memory, write-all-or-nothing) with timestamped backups; an Undo/Restore-from-`.bak` button.
- Per-file diff/preview for rename (old→new grouped by file) with per-file deselect, instead of 15 flat samples.
- Surface `last_sync_details` (skipped-open / not-verified / verified) and preserved-unmanaged lists as a small results table.
- Auto-detect and default the ERC target to the root schematic; eliminate the interactive prompt.
- Dry-run/diff mode for both managers (per-project, per-key old-mm→new-mm) that also catches the mm-quantization drift.
- Persist last-used projects folder + per-operation form values between sessions.
- Non-interactive/flag-driven `nd_wizard` mode so the overhaul GUI can call the same core without `input()`.

**Rendering**
- WRL/VRML support (single highest-impact change for catalog + viewer).
- Cache thumbnails keyed by path+mtime+size; a `render_any(path)` dispatcher; wrap painters in try/finally.
- Optimize `paint_mesh` (project vertices once, index by faces) or move to a GPU/z-buffer path for the board case.

---

## 5. The three requested features

### A. Git repository integration (change/repoint the hardware repo)

**Current state.** Already extensive in LibraryManager: pull (`--ff-only`), push, staged-guard commit, Commit&Push prompt, auto-commit after processing, a Sync button group, a branch chip with ahead/behind vs `origin/main`, startup + 5-min auto-pull, and an Activity panel (last 50 commits with Diff/Checkout/Open-on-GitHub). Changing the repo exists via Root→Change → `change_path('RepoRoot', …)` (folder picker, writable check, re-derive all paths). The repo root *is* the library/git root.

**Required work.** Fix the persistence bug (RepoRoot re-derived on every launch; `CONFIG_PATH` pinned to the old root); validate the chosen folder is a work tree (`git rev-parse --is-inside-work-tree`), else offer init/clone/set-remote; add a working-tree **status** view (`git status --porcelain=v1 -b`); selective staging instead of blanket `git add -A`; a `.gitignore` for `ui_python.log` + `catalog_assets/`; guard Checkout against detached HEAD (and block auto-commit onto it); add `timeout=` to every git call (an https credential prompt currently hangs a worker forever); replace hardcoded `origin/main` with the real upstream; handle non-fast-forward pulls (offer rebase/merge/stash).

**Recommended approach.** Don't rebuild — the core works. Consolidate the scattered helpers into `tools/git_ops.py` exposing `is_repo/init/clone/status/branch_info/stage(paths)/commit/pull(strategy)/push/remotes`, each a hidden-window `subprocess` with `encoding='utf-8'`, `check=False`, and a timeout, returning `(rc,out)` off the GUI thread via the existing `run_async`/`_spawn`+signals. Extend `change_path` to branch into init/clone/set-remote and persist RepoRoot properly. Add the Status view reusing the QStackedWidget-in-a-card pattern (a third tab with per-row checkboxes → `stage(selected)`+commit). Stay PyQt5+subprocess; no GitPython.

**Effort: medium.**

### B. In-app 3D viewer

**Current state.** A single-model interactive viewer exists (`load_step_mesh` → cascadio/trimesh → numpy verts/faces; `paint_mesh` QPainter rasterizer with rotate/light/back-face-cull/painter-sort; `PreviewView` drag-rotate + scroll-zoom off-thread with a stale-render token; static thumbnails; `have_3d()` graceful degrade). **No full-board viewer.** Limitations: WRL silently broken (fed to the STEP reader), all models render one flat gray (color discarded), painter-sort artifacts, won't scale past a few thousand triangles, capped at 330px with no pop-out/fit/standard-views, and no `.kicad_pcb` path at all (though `kicad-cli` is already located).

**Required work.** Fix the WRL branch + wrap cascadio in try/except; add a full-board path via the already-located `kicad-cli` (`pcb export step/vrml`, headless, cache by mtime); propagate face colors; add a pop-out maximizable window with fit/reset/standard orientations + ortho/perspective; handle board-scale meshes; declare the optional deps (cascadio/trimesh/numpy [+vtk]) in a requirements file.

**Recommended approach.** Two-track, phased. **Track A (fast, no new deps):** keep the numpy+QPainter viewer for single models but fix `.wrl`, plumb face colors, and add a pop-out with fit/reset/standard-view buttons; add a full-board **static** render via `kicad-cli pcb render board.kicad_pcb -o board.png` (KiCad 8+, raytraced, models+colors) shown in a pan/zoom image widget — high-fidelity board image, zero new pip deps. **Track B (interactive board, medium):** embed VTK via `QVTKRenderWindowInteractor` (ships in the `vtk` wheel, works with PyQt5) for trackball camera, real z-buffer, lighting, and picking; feed it the `kicad-cli` VRML export through `vtkVRMLImporter` (keeps per-model colors) or STEP via the cascadio→trimesh→vtkPolyData bridge; reuse the same widget for single models. Rejected: QtDataVisualization (wrong tool), pythonocc-core (~200MB), raw PyOpenGL (more hand-written code).

**Effort: medium.** Track A first (immediate board+model value), Track B when interactive full-board manipulation is wanted.

### C. Full PCB/schematic project settings

**Current state.** A partial editor across `nd_project_settings_manager.py` + `nd_netclass_manager.py` + `kicad_tools` covers ~25-30% of Board+Schematic Setup, only the `.kicad_pro` subset. Working: schematic text/line/pin sizes; footprint silk/copper/fab text; the 9 min-constraints; net-class per-class fields + patterns with safe-merge and vault templates. **Broken/absent** (see §2): via/junction/text/solder keys write to dead paths + deceptive verify; Default net class uneditable; unmanaged patterns dropped on save; false `.bak` promise; partial verify. **Architecturally out of reach today:** stackup/layers, page/title-block, plot params (all in `.kicad_pcb`/`.kicad_sch` s-expressions the tool never parses), plus DRC/ERC severities, ERC pin matrix, predefined dimension tables, and `text_variables`.

**Required work.** Complete `design_settings.defaults` (line widths, dimension formatting, zone defaults, apply-to-fp flags); add the remaining `rules` constraints; a **DRC severities** editor (`rule_severities`, ~40 IDs) and a full **ERC** editor (`rule_severities`, `pin_map` grid, exclusions); predefined-dimension list editors; make the **Default net class** first-class editable; fix pattern preservation + `netclass_assignments` + distinct schematic/pcb color; a `text_variables` editor; correct schematic `junction_size_choice` and the other `.drawing` ratios; s-expression parsers for stackup/layers, page + title block, and `pcbplotparams`; honest full-field verify and a single consistent backup policy; lock/verify/cache parity across both managers; surface `last_sync_details`.

**Recommended approach.** Split into a **JSON-schema layer** and an **s-expression layer**. Replace the flat `ProjectSettings` dataclass with a schema-driven registry mirroring the real `.kicad_pro` tree (each group declares key path, type, unit, KiCad default). **Load = deep-read keeping every untouched key; save = deep-merge back** — this alone fixes the pattern/assignment loss and enables Default-class editing, and makes coverage additive (a new setting = one registry entry + widget). Drive the GUI generically from the registry with a sub-rail (Board | Constraints | DRC | ERC | Net Classes | Text Vars | Schematic | Stackup/Layers | Page & Title | Output). Severity maps and the ERC matrix are QTableWidgets with enum cells. For non-`.kicad_pro` settings, add a small guarded s-expression read/modify/write helper reusing the existing atomic temp+`os.replace`+lock guard. **Harden the engine first** (honest verify, one backup policy, lock/verify/cache parity, surface reasons) before adding fields — cheap, and it removes the active data-loss/false-confidence risks. Keep everything preserve-by-default.

**Effort: large.**

---

## 6. Prioritized roadmap

The design overhaul (`docs/design/2026-07-04-app-design-overhaul.md`) is styling; **none of the below should wait for it**. The correctness work hardens the *cores* (parsers, writers, git, managers) that the new UI will call, so doing it first de-risks the overhaul rather than competing with it.

### Milestone 0 — Stop the bleeding (days, do immediately)
1. **Clean the committed `libs/MySymbols.kicad_sym`** (strip conflict markers) and add `*.kicad_sym -merge` to `.gitattributes`. It's broken *right now*.
2. Fix the three **criticals**: `extract_symbol_blocks` infinite loop, `nd_wizard` strip-all net mangling, `kicad_tools` `changes` NameError.
3. Fix the empty-library **template imbalance** and add conflict-marker/paren validation before any merge/commit (feeds the "Repair Library" button).
4. Guard `stm32_pins_tab._export()` (and add a global `sys.excepthook` so no slot can silently kill the app).

### Milestone 1 — Data integrity you can trust (1-2 weeks)
- **Switch-map correctness:** fix the `stm32_authority` representative-channel selection and the `stm32_db` `VREF-`/`VREFSD-` routing, and add the self-consistency assertion (`adg714.destination == destination`). These directly affect what the bench does to hardware.
- **Rename/settings/net-class safety:** make `nd_wizard` Apply atomic + timestamped backups + LF preservation; fix net-class pattern preservation and template round-trip; make settings verify honest, honor (or remove) the `.bak` promise, and correct the dead `.kicad_pro` keys (via/junction/text/solder). Add the `.lck`/verify/cache parity to net-class sync.
- **Concurrency:** serialize library+git writes behind one queue; add `timeout=` to every git call.

### Milestone 2 — Convenience wins that ride on the fixed cores (1-2 weeks)
- One-click **Repair/Validate Library** (now trivial once validation exists).
- Debounced/coalesced imports (one pass, one commit); auto-commit deletions.
- Git **Status view** + selective staging + persisted RepoRoot + init/clone-on-change (**Feature A** lands here).
- STM32 tab: search-haystack fix, bidirectional selection, DB-driven export set, expose switchmap C/JSON + wiring-MD, surface the analog-mode caveat.
- Rename: per-file diff preview, Undo/Restore, non-interactive core, auto-root ERC.

### Milestone 3 — In-app 3D, Track A (1 week)
- WRL fix + face colors + pop-out viewer; full-board **static** `kicad-cli` render. High visual payoff, no new deps. (**Feature B** Track A.)

### Milestone 4 — Full project settings engine (large, 3-5 weeks)
- Schema-driven, deep-merge, preserve-by-default settings registry; generic GUI sub-rail; DRC/ERC severity + pin-matrix editors; Default net class; text_variables; then the s-expression layer for stackup/page/plot. (**Feature C**.) Best sequenced after Milestone 1's engine hardening so the new coverage sits on trustworthy load/save/verify.

### Milestone 5 — Interactive board 3D + polish (as capacity allows)
- VTK-backed viewer (**Feature B** Track B); `kicad_paths` version-aware/registry discovery; `fp_render` fidelity (fp_arc, stroke width, fp_text, custom pads, thumbnail cache); theme-follow fixes in the shared kit; log rotation; `requirements.txt`.

**Interleaving with the UI overhaul:** Milestones 0-1 are pure logic in the cores and should land *before or in parallel with* the first overhaul PR — the redesigned tabs will call these same functions, so fixing them first means the new UI is built on solid ground. Milestones 2-3 produce new UI surfaces (Status view, pop-out 3D, results tables) that are natural places to *apply* the new design language, so schedule them to co-develop with the overhaul. Milestone 4's generic, registry-driven settings panel is the single biggest new UI surface and should be designed hand-in-hand with the overhaul's component kit (sub-rail, tables, spin/enum/color widgets) rather than retrofitted afterward.