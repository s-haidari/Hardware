# Master Controller — Project Property Editor (design plan)

_Synthesized by workflow wf_0e3d9b51 (6 Explore readers + high-effort synth), 2026-07-11. Every cited fn verified to exist. Owner ask: a Projects panel to edit any KiCad-project property (silkscreen text sizes + anything editable via the file-rewriter backends)._

Plan is durable. Returning it as my final output (the calling script parses this verbatim).

---

# Master Controller — Project Property Editor (design plan)

A single panel that edits **any project-wide setting/property the file-rewriter backends support**. Grounded in the six capability maps and verified against source: all cited fns exist; PCB Setup lives at `_pcb_setup_panel` (line 2645 in `tools/ui/features/projects.py`); tabs register in `ProjectsFeature.build` (line ~3597); `kit.PrimaryFlow` (kit.py:385), `kit.section` (kit.py:45), `kit.workbench` (kit.py:581), `FillPreviewDialog` (projects.py:163), `_len_spin` (projects.py:2533) all confirmed.

Full plan file: `/tmp/claude-1000/-home-sadad-git-Hardware/e2cc8d39-2358-4460-8abc-a6df4ef3e568/scratchpad/master-controller-plan.md`

## 1. HONEST SCOPE

"Edit anything you can edit through the tools" = every **project-wide setting/property one of the file-rewriter backends can write** — document-level and default-level properties, not individual placed objects. Three files, three backend groups:
- **`.kicad_pro`** via `nd_project_settings_manager.ProjectSettings` (`save_to_project`/`save_extended`/`save_design_rules_only`) + net classes via `nd_netclass_manager.NetClassManager.save_to_project`.
- **`.kicad_pcb`** via `nd_board_setup.save_board_setup` (setup block), `nd_object_conform.write_fab_to_board` (stackup + physical thickness), `conform_pcb_text` (existing silk/fab/copper text).
- **`.kicad_sch`** via `conform_schematic_text` (text + labels) and `nd_library_fill.write_fields_to_sheet` (symbol-instance property values).

**Boundary (real, unavoidable):** No live KiCad session (map 6: "NO INTERACTIVE KiCAD API"). The app edits **files directly** with S-expr/JSON rewriters. It can edit settings, defaults, and bulk properties of existing text/labels; it **cannot** place/move/rotate footprints, route/edit traces/vias/zones/pads, add/delete schematic symbols/wires, or change board-outline geometry — those need the KiCad GUI (already offered via `_open_in_kicad`, line 782). Every write goes through the backends' `.bak`/`.{timestamp}.bak` backup + atomic tmp+rename path and is re-audited after apply.

**Two behaviors inside the boundary the UI must separate:** (1) **Defaults** (`.kicad_pro` `design_settings.defaults.*`, schematic `drawing.*`) change the *template* — only future objects; (2) **Retroactive conform** (`conform_pcb_text`/`conform_schematic_text`) rewrites *existing* objects. The owner's silkscreen example needs **both**, or the change won't show.

## 2. INVENTORY (Surf? = surfaced in today's PCB Setup panel)

**A. Text & Silkscreen — RETROACTIVE (fonts on real objects)**
| Property | Backend fn | Surf? |
|---|---|---|
| PCB silk size+thickness | `conform_pcb_text` targets['silk'] (nd_object_conform.py:100) | y (preset-derived, read-only) |
| PCB fab size+thickness | `conform_pcb_text` targets['fab'] | y (read-only) |
| PCB copper size+thickness | `conform_pcb_text` targets['copper'] | partial |
| Schematic text size+thickness | `conform_schematic_text` targets['text'] (:115) | n |
| Schematic net labels size+thickness | `conform_schematic_text` targets['labels'] | n |
| Whole-project atomic conform (rollback) | `conform_project` (:212) | y (targets read-only) |

**B. Default text sizes (`.kicad_pro`, template for NEW objects)** — all via `ProjectSettings`→`save_to_project`
PCB other/silk/copper/fab size_h/v + thickness (mm); sch default_text_size, default_line_thickness, pin_symbol_size (mils); junction_size_choice (enum 0-4). **Surf? n.**

**C. Board Stackup & Physical Thickness (`.kicad_pcb`)**
| Stackup block (FabPreset) | `set_board_stackup`/`write_fab_to_board` (:131/179) | n |
| Physical thickness (general.thickness) | `set_board_thickness`/`write_fab_to_board` (:153/179) | n |

**D. Design Rules (`.kicad_pro`)**
| 9 min-rules (clearance/track/via/annular/through-hole/hole-to-hole/microvia-dia/microvia-drill/copper-edge) | `save_design_rules_only` | **y** (11 spins, §B) |
| DRC severities / ERC severities | `set_drc_severity`/`set_erc_severity`+`save_extended` | n |
| ERC pin_map 12×12 / ERC exclusions | `ensure_erc_pin_map`/`set_erc_pin_map_entry`/`set_erc_exclusions` | n |
| Predefined track_widths / via_dimensions / diff_pair_dimensions | `set_track_widths`/`set_via_dimensions`/`set_diff_pair_dimensions` | n |

**E. Net Classes (`.kicad_pro`)** via `NetClassManager`→`save_to_project`
clearance/track/via_dia/via_drill/diff_pair_width/gap/priority/patterns/name/color: **y** (§C table). microvia_diameter, microvia_drill, diff_pair_via_gap, line_style, wire_thickness, bus_thickness: **n**. Default net class (`set_default_netclass`): n. Profile CRUD/vault/validate: y.

**F. Board Setup (`.kicad_pcb` setup)** — `save_board_setup`: pad_to_mask, solder_mask_min_width, pad_to_paste, paste_ratio, grid_origin, aux_axis_origin, allow_soldermask_bridges. **All y** (§D).

**G. Project Meta** — text_variables.* (`set_text_variable`/`remove_text_variable`+`save_extended`): n. Symbol-instance props by Reference (`write_fields_to_sheet`): y (only via Health Prepare, not a general editor).

## 3. UI DESIGN

**Where it lives:** Extend the existing **PCB Setup tab into "Editor"**, not a sibling tab — `_pcb_setup_panel` (line 2645) already owns Design Rules/Net Classes/Board Geometry with working `save_flow = kit.PrimaryFlow` + `_save_job` (line 3343). A second tab would fork profile/units/save state. Rename the label in the `panels` list in `ProjectsFeature.build` (line ~3620); keep `_pcb_setup_panel` as builder and grow it. `state.on_change(ws.rebuild_all)` already rebuilds on project switch — new sections must **not** self-register on_change (stale-closure warning, line 3626).

**Organization:** one scroll body, category sections via `kit.section(title, *body)`. Reuse `_len_spin(unit, mm_value)` for lengths (mm/mils via existing top `unit_seg` Segmented), `_ratio_spin` for dimensionless, `QCheckBox` for bool, `QComboBox` for enums/severities.

- **§1 Text & Silkscreen** *(owner's example, ships first)* — Silk/Fab/Copper + Sch text/Labels, each a size+thickness `_len_spin` pair. Two toggles per group: **"Set default"** (→ `save_to_project`, Cat B) and **"Also conform existing"** (→ `conform_project`/`conform_pcb_text`/`conform_schematic_text`, Cat A; default ON for silk). Inline **live count preview** from `conform_project(dry_run=True)` ("will rewrite N silk texts across M files").
- **§2 Stackup & Thickness** — physical thickness `_len_spin`→`set_board_thickness`/`write_fab_to_board`; stackup as **read-only preset summary** + "Apply stackup from preset" button (no granular backend — don't fake a per-layer editor).
- **§3 Design Rules** *(built — extend)* — keep 11 `dr_fields`; add predefined track/via/diff-pair list editors + a DRC/ERC severity combo table + ERC pin-map grid.
- **§4 Net Classes** *(built — complete)* — keep §C table; add the missing columns microvia_diameter/microvia_drill/diff_pair_via_gap + schematic fields (line_style combo, wire/bus thickness) + a Default-net-class row (`set_default_netclass`).
- **§5 Geometry & Setup** *(built — keep)* — `bg_fields`.
- **§6 Project Meta** — text-variables key→value add/remove table (`set_text_variable`/`remove_text_variable`).

**Preview + Apply (reuse the spine):** ONE Apply = the existing `save_flow = kit.PrimaryFlow` (line 3441), extended so `audit` reports changed sections (dr/nc/bg/fab/**text/stackup/meta**) and `apply` (`_save_job`) dispatches each to its backend — the flow already runs audit→preview→apply→report→after(re-audit) off-thread and is headless-safe. **Rich review** = reuse `FillPreviewDialog` (line 163) as the flow's `preview=` callable (per-field old→new deltas + checkboxes, already headless-guarded); feed it the conform dry-run before/after. Backups: every backend already writes `.bak` + atomic tmp+rename — surface "backups kept" in the report line.

## 4. REUSE MAP + REAL GAPS

**Reuse** (already tabulated above): conform fns → size/thickness spin pairs + dry-run preview + `FillPreviewDialog`; `save_to_project` → default spins; `set_board_thickness`/stackup → thickness spin + preset summary; `save_design_rules_only` → `dr_fields`; predefined-size + severity setters → list/combo tables; `NetClassManager` → §C table (extended); `save_board_setup` → `bg_fields`; text-var setters → key/value table; `kit.PrimaryFlow`+`_save_job` → save; `FillPreviewDialog` → delta review.

**REAL GAPS (no backend — "done" defined, not fabricated):**
1. **Per-layer stackup granularity** — `stackup_block` hard-codes wrappers from a FabPreset (map 1/3). *Done* = a rewriter editing one layer's `(thickness…)`/`(material…)`. Defer; summary only.
2. **Add thickness to a font block lacking one** — `_set_font` only updates if present (map 1, line 66). *Done* = insert `(thickness…)` when absent.
3. **Layer-stack management** (add/delete/reorder copper) — no backend (map 4). *Done* = layer-list rewriter.
4. **Board outline/size geometry** — no backend (map 4). *Done* = Edge.Cuts rewriter (GUI territory).
5. **Per-footprint instance props in `.kicad_pcb`** — only sch symbol props exist (map 4). *Done* = footprint-property rewriter keyed by ref.
6. **Live-object editing** (traces/pads/vias/zones/placement/wires) — architecturally out of scope, file-rewrite only (map 6). GUI job, not a gap to close here.
7. **Fab preset editing** — presets are frozen (map 3). *Done* = user-fab-preset store paralleling profile JSON. Defer; select via profile.

Everything in §2 with `Surf? = n` that HAS a backend is **build work, not a gap**.

## 5. BUILD ORDER (each independently usable + verified: backend check → UI section → drive_audit case → render gate)

- **M0 — Rename + spine.** `"PCB Setup"`→`"Editor"`; confirm `save_flow` reports section keys; regenerate `docs/CAPABILITIES.md`; existing drive_audit stays green.
- **M1 — Text & Silkscreen (owner's slice, usable alone).** §1 with set-default + conform toggles + dry-run count preview via `FillPreviewDialog`. drive_audit: set silk size, apply, assert `.kicad_pcb` silk rewritten + `.bak` exists.
- **M2 — Stackup & Thickness.** thickness spin + "apply stackup from preset". drive_audit: set thickness, assert `general.thickness` changed + idempotent re-save `written=False`.
- **M3 — Design Rules completion.** predefined tables + DRC/ERC severity table + pin map. drive_audit: set a severity, assert `save_extended` wrote it.
- **M4 — Net Classes completion.** microvia/diff_pair_via_gap/schematic columns + Default net class. drive_audit: edit microvia, save→reload round-trip.
- **M5 — Project Meta.** text-variables table. drive_audit: add/remove var round-trip.
- **M6 — Unified review + polish.** ONE Apply drives all sections through extended `_save_job`; `FillPreviewDialog` groups every pending delta by category; re-audit. Full drive_audit + render gate + suite.

Verify each: `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/drive_audit.py` (exit 0) + `render_gate.py --settle 0.9` then **read the PNGs** vs `docs/design/design-rules.md`. Never declare done on pytest alone (No-fault gate 3).

## 6. RISKS / Qt footguns / honest constraints

- **Widget-lifecycle (segfault gate):** never delete/replace a widget synchronously inside its own signal handler; panel rebuilds wholesale via `state.on_change(ws.rebuild_all)` — new sections must NOT self-register on_change (line 3626); defer any in-signal teardown with `QTimer.singleShot(0, …)`.
- **S-expr rewrite safety:** conform fns are regex/string-scan rewriters; `conform_project` is atomic all-or-nothing with rollback from `.{timestamp}.bak`; single-file writes use `.bak` + tmp+rename. Never hand-write files. `_set_font` only changes thickness if already present — the UI must not silently no-op a thickness edit (gap #2).
- **Defaults-vs-retroactive confusion (real UX risk):** user sets "silk default = 1.0mm" and sees no change because existing silk wasn't conformed — the two-toggle design + dry-run count preview mitigates; label it explicitly.
- **Windows CI is the gate:** file reads need `encoding="utf-8"` (cp1252 chokes on → · ×); `.as_posix()` for display, never `str(Path)`. Linux/offscreen is necessary, not sufficient.
- **Offscreen headless:** `PrimaryFlow.preview`/`_report` must return safe pre-checked keys under `_headless()` so drive_audit never blocks; `FillPreviewDialog` is already headless-guarded — keep new previews the same.
- **No live render verify:** `kicad-cli` DRC/render can *check* a board but not confirm a font change *looks* right without opening KiCad. Honest completion claim: "drive-audit + suite green on Linux/offscreen with a fixture; backups written; NOT yet confirmed on Windows / the owner's real library."