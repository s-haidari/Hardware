# Whole-app UI convergence — design spec

**Date:** 2026-07-09 · **Status:** approved (direction), execution starting · **Owner decision:** converge straight; fold Projects A/B/C into the rebuild.

**Sources:** whole-app UI review `wf_7743c27a` (task `wujgdwju2`, 5 auditors) · Projects design menu `wf_737a694b` (task `wy601vwd6`, A/B/C features) · adversarial Projects review `wf_d269ac52`.

## The problem (unanimous across 5 auditors)

The app is **two complete UIs over one backend**, and it ships the wrong one by default:

- **`tools/ui/bare.py`** (4561 LOC) — the DEFAULT (`python -m ui`). Reliable, capability-**complete**, object-centric **workbench IA** (noun-first verdict card → primary orchestrated action w/ preview → secondary atoms → teach-then-fix `_report`). But **no design system** — raw Fusion + hardcoded status hex.
- **Styled `NetdeckShell`** (`--full`, hidden) — `tools/ui/shell.py` + the real design system (`ui.theme` + `kit.py` + `widgets.py` + `motion.py` + `icons.py`) + styled panels in `features/*.py` (incl. the app's differentiator **visuals**: painted `PinMap`, interactive 3D `MeshView`, live-sourcing `PartDetail`). But **feature-poorer** (styled Git lacks Set/Init Repo, recent commits, scan-corrupt; Settings lacks KiCad detection/updater), older **action-row IA**, and dark code on the default path.

The "two design systems" is **already solved** — `ui_theme.py` is now a derived shim of `ui.theme`. The real split is the **two front-ends**. Every downstream problem (feature drift, forgotten features, monolithic 800-LOC functions, the `_rebuild_all` crash) is caused by maintaining both.

## End-state: ONE app

The **styled `NetdeckShell` frame** (right bones: feature registry, lazy build, `_safe_build` crash isolation, `EventBus` for theme/units/autopull/updater) with:

1. bare's **object-centric Workbench IA** codified as a reusable `ui.kit` recipe, applied to all 5 panels.
2. **Full capability parity** with bare — gated on `capability_audit` = zero styled-side omissions (owner's #1 rule: zero feature omission).
3. The styled **visuals kept verbatim** (`bench_visuals.PinMap/legend/connection_blocks`, `library_preview.MeshView/PreviewCard/PartDetail`, `projects_visuals` container-QSS). Do NOT rebuild them.
4. A **persistent activity/log console** in the shell (bare's one genuinely-good chrome idea — the styled shell only has a transient 6s statusBar).
5. `ui_theme.py` **fully absorbed** into `ui.theme`/`icons` (one icon system — pick `icons.py` inline set vs Lucide); spacing + chrome-type **tokens** added to `ui.theme` (single-source scale).
6. The design-system **frequent-rebuild leak fixed** (`register_restyle` idempotent/keyed) so panels stop forking the vocabulary (Git's shadow `_key/_mut/_dl/...` copies).
7. One **nav order** (Library → Projects → Bench → Routing → Git, Settings footer), one **identity/title**, honest **Routing** disabled state (not a live nav row → dead "Coming Soon" card), global services reachable everywhere.
8. Flip the default to the styled shell; **retire `bare.py`** (frame + parallel panels).

## The Workbench recipe (the central abstraction)

A `ui.kit` builder every panel instantiates so all five teach ONE mental model:

```
workbench(
  selector,          # object picker row: combo + optional (filter + search + counts)
  verdict,           # ALWAYS-present color-coded gap/verdict header ("✓ ready" / "⚠ N …")
  detail_card,       # noun-first W.Card, refreshed IN PLACE via a per-panel bus (never rebuild-whole-panel)
  primaries,         # 1–2 accent ▶ orchestrated actions (preview→confirm→apply→report[→revert])
  secondary,         # 2-col grid of atomic object actions
  machinery,         # collapsible "Advanced" (the button-walls) — zero feature dropped, just demoted
  exports,           # collapsible "Export & Hand Off"
)
```

Emphasis rules (write into `docs/design/design-rules.md`): exactly ONE accent primary per page (the orchestrated action); one boolean-control idiom; a consistent sub-tab rule; results the user must READ → `_report` dialog, the console is for the ▶/✓/✗ progress stream + errors only.

## Phased plan (multi-session; each phase gated + committed)

**Phase 0 — foundation & de-risk** (do first; low-risk, reusable):
- ✅ Fix the `_rebuild_all` crash in bare (done, `7fd30bb`) — bare stays shippable until retired.
- Fix the `register_restyle` rebuild-leak at the root (idempotent/keyed or a `static=` construction mode); delete Git's shadow vocabulary once fixed.
- Absorb `ui_theme.py`: move `load_bundled_fonts`/`resource_path` into `theme.load_fonts`, pick ONE icon system, port `stm32_pins_tab` + `LibraryManager` to import `ui.theme`; delete `ui_theme.py`.
- Add `SPACE` tokens + a `sp(role)` accessor to `ui.theme`; derive `qss()` chrome font-sizes from `TYPE_SCALE` (one type ramp). Route kit/widgets padding through `sp()`.
- Shell: one nav order + grouped tiers (destinations / controls / notifications), honest Routing (disabled item or omit), one identity/title, add the persistent activity console.

**Phase 1 — the Workbench recipe + capability-parity harness**:
- Build `kit.workbench(...)` + a reusable verdict-card + preview→report flow in the styled system.
- Extend `capability_audit` to compare each styled panel against its bare twin (parity report per panel).

**Phase 2 — rebuild each panel on the recipe, at parity, in the styled shell** (one at a time, each gated on capability_audit=0 omissions + a drive_audit case + render screenshot + suite):
1. **Library** (north-star; port `library_preview` visuals + MouserSearchDialog surfaced).
2. **Projects** — rebuild on the recipe AND **fold in the approved A/B/C features** (fused readiness verdict card incl. ERC/DRC + git working-tree + "Next step"; per-part findings table; Restore-Last-Prepare + corruption guard; structured ERC/DRC; per-line BOM orderability drill; Procurement Bundle; live cost/freshness; Fix-All folded into Prepare; Refactor preview gate). See task `wy601vwd6` for the full A/B/C menu.
3. **Bench** (keep `PinMap`/connection-diagram/MeshView; add verdict header + parity).
4. **Git** (port bare's Set/Init Repo, recent commits, show-file@HEAD, scan-corrupt; workbench IA; use real vocabulary after the leak fix).
5. **Settings** (port KiCad CLI/bin detection + updater diagnostics; workbench setup card).

**Phase 3 — flip & retire**: make styled the default in `__main__.py`, delete `bare.py` frame + panels, update `run.sh`/`run.bat`, drive-audit the styled shell, Windows verify.

## Gates (every panel, every phase)
- `capability_audit.py` styled-vs-bare = **zero omissions** before a panel is declared converged.
- `drive_audit.py` (extended to drive the styled shell) exit 0.
- Suite green (baseline 1269/4); render screenshot Read + checked vs `design-rules.md` §5.
- Honest completion: Linux/offscreen ≠ Windows/real-library confirmed.

## Non-negotiables
- Keep the styled **look** (Refined-Neutral WinUI system) — it's good; the job is uniform application + parity, NOT a restyle.
- Keep the **painters/visuals** verbatim.
- **Zero feature omission** — nothing bare does may be lost; parity is proven, not assumed.
- Do NOT naively "pick the styled panels" — that drops shipped Git/Settings capability.
