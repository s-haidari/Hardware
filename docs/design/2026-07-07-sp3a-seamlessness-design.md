# SP3-A — App-Wide Seamlessness Pass: Design

**Status:** draft — brainstormed 2026-07-07 on `ui-clean-slate` · **Date:** 2026-07-07
**App:** KiCad Library Manager (PyQt5 desktop, `git/Hardware`)
**Repo (canonical):** `~/git/Hardware` (WSL-native), branch `ui-clean-slate`
**Predecessor:** SP2 — Library Rebuild + 3D Viewer (complete, shipped `3a76ff0`)

---

## 0. Where this sits

The overhaul defined three sub-projects. SP1 (self-contained core) and SP2 (Library
rebuild + 3D viewer) are shipped. **SP3** is the app-wide pass over the pages SP2 did
not touch, and it splits into three separable strands:

- **SP3-A — App-wide seamlessness pass (this doc).** A pure *visual* audit-and-fix of
  every non-Library surface (Bench, Projects, Settings, shell chrome) against
  `docs/design/design-rules.md`, until every surface renders pristine in dark and light.
- **SP3-B — Bench STM32 exporters.** Wire the stranded `to_switchmap_c`, `to_kicad_symbol`,
  `authority_diff`, `lint_card`/claims backends into the Bench page. *Not this doc.*
- **SP3-C — Extended Projects settings.** Wire DRC/ERC severities, the 12×12 ERC pin
  matrix, text variables, and track/via/diff-pair tables. *Not this doc.*

**Two standing requirements (from the overhaul §0), carried forward unchanged:**
1. **Every UI change is validated by an offscreen screenshot (dark + light) and critiqued
   against `docs/design/design-rules.md`** — the user cannot run the app. The bar for this
   strand is explicit: **the UI must be pristine with zero critiques.**
2. **`design-rules.md` is the immutable standard.** SP3-A invents no new tokens, rules, or
   philosophy. §1–2 (anti-patterns/principles) and §5 (checklist) are stable; §3–4 (the
   locked "Quiet Instrument" tokens and component recipes) are the target every surface
   must conform to. If a fix appears to need a token absent from §3, that is a flag to
   raise with the user — never a silent addition.

---

## 1. Problem

The non-Library pages predate the design-rules discipline SP2 achieved. In particular the
Bench/STM32 page is the surface whose "ugly / AI-generated" drift *caused* `design-rules.md`
to be written, and its helpers (`_node`, `_swatch`, `_chip_grid`, `_switch_pill`,
`_connection_flow`) still embody the retired card / pill / chip / accent-bar patterns that
§1 forbids. Projects received only the SP2 Net-Class Profile selector; its other six panels
are unaudited. Settings uses `W.Card`/`_setting_row` and is unaudited. No committed way
exists to render these surfaces offscreen for review — SP2 used a throwaway scratchpad script.

**This is a visual pass only.** It does not wire stranded backends (that is SP3-B / SP3-C).
Where a panel looks wrong *because* logic is stranded, SP3-A fixes the presentation and
records the wiring as deferred to the owning strand.

---

## 2. Locked decisions

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | **Scope = all non-Library pages**, ordered **triage-first** | Bench, Projects, Settings, shell chrome. Ordering is worst-first; the audit reveals the distribution of debt rather than fixing it upfront. |
| 2 | **Bar = pristine, zero critiques** | Same bar SP2 shipped. Triage orders the work; it does not stop it. P2 nitpicks are fixed last, not deferred. |
| 3 | **Committed reusable render gate** | `tools/ui/render_gate.py` becomes permanent regression infrastructure — re-runnable on any future UI change, not a throwaway. |
| 4 | **Critique = self-audit against images** | The controller renders each surface, reads the PNGs directly (vision), and critiques against §5 — no separate reviewer subagent. The bar stays in the controller's context. |
| 5 | **Visual-only scope boundary** | No exporter or extended-settings wiring. Presentation fixes only; stranded logic noted and deferred to SP3-B/SP3-C. |
| 6 | **Rendered PNGs are gitignored** | Tool + procedure are committed; images go to `build/render/` (gitignored) to respect the LFS/dirty-tree hygiene rule — no binary noise in git. |

---

## 3. The render gate — `tools/ui/render_gate.py`

A committed, importable + CLI tool. Under `QT_QPA_PLATFORM=offscreen`:

- **Context construction.** Builds a real `feature.Context`: `cfg = LM.load_config()`, a
  stub `Services` (synchronous `run_async(fn, ok=, done_cb=)` that runs `fn` inline and
  invokes `done_cb`; no-op `log`), the live `ui.theme` module, a fresh `EventBus`.
- **Surface registry.** id → builder callable. Covers whole pages (`feature.build(ctx)`)
  *and* individual sub-panels (`_panel(ctx, state)`) so each Bench/Projects panel renders
  in isolation. Sub-panels are built with the same `state` init the feature uses, against
  the real bundled libs/STM32 data (the 95-part data SP2 rendered against) so panels are
  populated, not empty.
- **Render step.** Per surface × {dark, light}: `T.set_theme(dark)`,
  `app.setStyleSheet(T.qss(dark))`, build into a fixed-width host widget, `W.restyle_all()`,
  `host.grab().save(path)`.
- **Output.** `build/render/<surface>.<theme>.png` (gitignored).
- **CLI.** `python -m tools.ui.render_gate [--surface X] [--theme dark|light|both] [--out DIR]`.
  No `--surface` renders all.

### Surface inventory (~13 panels + shell chrome, ×2 themes ≈ 28 renders)

- **Shell chrome:** nav rail (expanded + collapsed), theme toggle, tab bar.
- **Bench (5):** Overview · Profiles · All Pins · MCU Pinout Viewer · Exports.
- **Projects (7):** Health · Bill of Materials · Rename · Net Classes · Board Setup ·
  Fabrication Standard · Git.
- **Settings (1):** the settings panel.

---

## 4. Audit ledger

A living `docs/design/audit/sp3a-findings.md`: one row per finding —
`surface · theme · rule (§1/§4 id) · severity · fix`. Severity:

- **P0** — reads as generated / breaks a §1 hard anti-pattern (border on everything,
  pill on data, accent bar on a card, card-in-card, category-tinted surface).
- **P1** — major hierarchy / spacing / color-meaning violation.
- **P2** — nitpick (a stray radius, an off-scale gap).

The ledger orders the fix work worst-first. The bar is zero-critiques, so P2s are fixed
last, not dropped.

---

## 5. Fix workflow

1. Build the gate (Phase A).
2. Render all surfaces, self-audit each PNG dark+light, populate the ledger (Phase B audit).
3. Fix worst-first: edits land in `bench.py`, `projects.py`, `settings.py`, and shared
   `widgets.py` / `theme.py`. **Bench is the heaviest** — bringing its panels into §4
   conformance likely means rewriting `_node`/`_swatch`/`_chip_grid`/`_switch_pill` to the
   recipes (pin header, signal path, ledger, detail, stat strip, pin map) while preserving
   the existing logic paths.
4. Re-render the touched surface, re-audit, mark the finding closed.
5. Repeat until every surface renders zero critiques in both themes.

**Scope discipline:** presentation only. Stranded exporter / extended-settings logic is
noted in the ledger as deferred to SP3-B / SP3-C and left unwired.

---

## 6. Testing / verification

- **`tests/test_render_gate.py`** (committed regression guard): renders every registered
  surface in both themes headless; asserts no exception and a non-empty pixmap. Guarantees
  every surface always builds under both themes.
- Existing suites stay green: `tests/test_sp2_library.py tests/test_library.py
  tests/test_ui_shell.py` → 51 passed, 1 skipped.
- The **pristine visual bar** is met by re-render + controller self-audit per surface,
  exactly as SP2's gate operated.

---

## 7. Risks / watch

- **Empty-state panels.** Sub-panels need realistic `BenchState` / projects state or they
  render blank. The gate reuses each feature's own state init against the bundled data.
- **Scope creep** into SP3-B/SP3-C — held off by the §2 visual-only boundary.
- **Bench helper rewrites** could regress behavior — the render smoke test plus keeping the
  logic paths intact guard it; behavior-bearing code is not touched, only presentation.
- **Git hygiene.** Never `git add -A` (the working tree always shows dirty
  `libs/My3DModels/*.STEP` + `.gitignore` LFS smudge). Stage only named files. PNGs stay
  gitignored.
- **Out of scope:** the pre-existing 7 `test_audit_kicad_paths` failures (unrelated
  version-sort bug).

---

## 8. Not in this strand (deferred, not lost)

- **SP3-B — Bench STM32 exporters:** `to_switchmap_c`, `to_kicad_symbol`, `authority_diff`,
  `lint_card`/claims wiring.
- **SP3-C — Extended Projects settings:** DRC/ERC severities, 12×12 ERC pin matrix, text
  variables, track/via/diff-pair tables (`nd_project_settings_manager` layer).
