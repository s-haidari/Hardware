# Central UI Composition Kit + Legibility Rework

**Status:** approved design · **Date:** 2026-07-09 · **Direction:** keep Refined-Neutral
(neutral chrome, color = meaning only); centralize composition; fix color *legibility* and
awkward layout. Owner calls captured below.

## Owner decisions (from brainstorming)

| Decision | Choice |
| --- | --- |
| **Goal** | All three: one place to restyle, drift is *enforced*, and new/rewritten tabs are declarative-ish (less Qt boilerplate). |
| **Architecture** | A **composition kit** (`tools/ui/kit.py`) of high-level builders that features call. Not a pure data-schema engine, not just loose composites. |
| **Rollout** | **Big-bang target** — every tab ends up on the kit — but *sequenced* around the concurrent sourcing/routing sessions so we never fight over files. |
| **Color** | Neutral is right; the problem is **legibility** — category colors are hard to see (esp. the legend), not the hue choice. |
| **Layout** | Things are "laid out weirdly" (legend, MCU pinout, general). The kit's single page scaffold + a real legend builder fix most of it. |

---

## 0. Current state (grounded)

The app already has a central layer — `theme.py` (tokens), `widgets.py` (primitive kit),
`motion.py`, `icons.py`, `feature.py` (plug-in contract). The recent overhaul proved
centralization works (de-letterspacing one function reskinned every header). But:

- **The primitives are opt-in**, so features drift: hardcoded `mono_font(15)` instead of
  `scale_font("stat")`, uppercased table headers, per-tab empty-state strings, ad-hoc button
  hierarchy, inline `setStyleSheet`. Phase C had to touch every feature file to correct this.
- **No enforcement** — nothing fails when a feature styles directly.
- **Legibility** — category hues (esp. the over-muted ground/lane greys) are hard to see; the
  Bench legend and MCU pinout are cramped/awkward (open gripes BENCH-03, BENCH-04).

This is why we add a composition layer *above* the primitives and enforce its use, then tune
the palette for visibility and rebuild the legend/pinout layout centrally.

---

## 1. `tools/ui/kit.py` — the composition layer

Sits **above** `widgets.py`. `widgets.py` stays the low-level primitives; `kit.py` composes
them + tokens/scale/motion/icons into **page-level builders** that own all styling. Features
import `kit` and stop importing `theme`/`widgets` for styling. Start with exactly what today's
tabs need — no speculative framework (YAGNI).

### 1.1 API surface

| Builder | Renders | Enforces |
| --- | --- | --- |
| `kit.page(title, *, header=None, actions=(), body=())` | page title + action bar + scrolled body | one page scaffold, standard padding/rhythm |
| `kit.tabbed_page(title, panels, *, header=None)` | sub-tab `Workspace` (reuses `widgets.Workspace`) | one tabbed scaffold + the sliding underline |
| `kit.action(text, on, *, kind="default", tip="")` → `Action` | (spec consumed by page/action bar) | at most **one primary per page** (raises in tests if >1) |
| `kit.section(title, *body, hairline=True)` | `section_header` + content | Title-case section breaks |
| `kit.detail(title, pairs, *, key_width=136)` | section + `W.dl` definition list | aligned key/value, txt2 keys |
| `kit.stat_strip(stats)` | stat numbers via `scale_font("stat")` | tabular, on-scale |
| `kit.table(columns, rows, **opts)` | wraps `W.data_table` | Title-case headers, themed cells |
| `kit.legend(groups)` | the new legend widget (see §3.2) | one legend look app-wide |
| `kit.state(kind, line, *, glyph="", sub="", action=None)` | empty / loading(skeleton) / error via `W.empty_state` / `W.skeleton_rows` | one state pattern; `kind ∈ {empty, loading, error}` |
| `kit.async_region(compute, render, *, rows=6, cols=4)` | `run_populate` with an **automatic skeleton** placeholder | every async panel gets a loading state for free |
| `kit.custom(widget_or_builder)` | drops a bespoke widget in untouched | the escape hatch |

### 1.2 Escape hatch & Qt reality
The pin map, connection diagram, symbol/footprint/3D previews, and routing canvases stay
hand-written Qt; they enter the layout via `kit.custom(...)`. `kit` composes the *existing*
`register_restyle` / `run_populate` / `motion` machinery — it is a composition layer, not a new
runtime — so signals, live updates, async populate, theme toggle, and reduced-motion all keep
working unchanged.

### 1.3 Isolation
`kit.py` depends on `theme`, `widgets`, `motion`, `icons`; features depend on `kit` (+ their
domain modules). Each builder has one purpose, is independently render-testable, and returns a
`QWidget`. `widgets.py` is untouched in contract (kit is additive).

---

## 2. Enforcement — no-drift lint

`tests/test_ui_no_drift.py` scans `tools/ui/features/*.py` and **fails** if a *chrome* feature
file does styling directly. Banned in feature files:

- `setStyleSheet(` (styling belongs in kit/widgets/theme)
- `setLetterSpacing` (retired everywhere)
- `.upper()` applied to a header/label string
- `ui_font(<numeric literal>)` / `mono_font(<numeric literal>)` — sizes come from `scale_font`
- constructing a bordered `QFrame` for a container (borderless elevation only)

**Allowlist:** genuinely bespoke visual modules (pin map, connection diagram, preview cards,
routing canvases) are isolated in clearly-named files and exempted — the lint targets chrome,
not custom painting. The allowlist is an explicit list in the test, so adding an exemption is a
visible, reviewed decision.

---

## 3. Visual legibility & layout rework (central)

### 3.1 Category palette legibility (BENCH-03)
Keep meaning-only color and the neutral chrome. Retune `CATEGORY_DARK`/`CATEGORY_LIGHT` so
**every category hue clears a minimum ~3:1 contrast against the surface it sits on** (the WCAG
non-text/graphical-object threshold) — the over-muted ground/lane greys that currently vanish
get enough contrast to be *seen* while still reading quieter than the saturated classes
(power/core/must). A unit test computes each category-vs-surface contrast in both themes and
fails below 3:1. Legend/pinout swatches use a **larger, consistent dot size**.

### 3.2 Legend rework (`kit.legend`)
Replace the cramped ad-hoc legend with one builder: an aligned grid of `swatch + Title-case
label`, consistent spacing, clearly grouped (e.g. Net Colour / Border / Mark), readable at a
glance, theme-aware. Reused anywhere a legend appears.

### 3.3 MCU pinout (BENCH-04)
Give pins enough room that numbers stop overlapping/stacking, and make the selected pin
unmistakable (crisp painted ring via `motion.paint_focus_ring`). Stays a bespoke widget behind
`kit.custom`, but pulls colors/contrast from the retuned tokens. Numbers legible at the default
zoom for the largest package.

### 3.4 General layout
The kit's single page scaffold gives every tab uniform spacing, alignment, and rhythm, which
removes most ad-hoc awkwardness by construction (the "laid out weirdly" complaint).

---

## 4. Migration (big-bang target, sequenced)

Every tab's `build()` and panel builders rewrite onto `kit`; bespoke visuals move behind
`kit.custom` unchanged. Sequenced to avoid collisions with the live sourcing/routing sessions:

1. Build `kit.py` + `test_ui_no_drift.py` + the palette/legend/pinout legibility work first
   (these are in `kit.py`/`theme.py`/`widgets.py` + Bench, which this session owns).
2. Migrate the tabs this session owns: **Bench → Library → Projects → Git**.
3. Migrate **Settings** and the **Routing** tab once the concurrent sessions free those files.

Per tab: rewrite → render-gate (both themes) → tests green → scoped commit. The no-drift lint
flips on per file as each migrates (allowlist the not-yet-migrated ones until their turn).

---

## 5. Testing & acceptance
- **kit builders** — each independently render-tested (returns a valid widget; page enforces one
  primary; state renders empty/loading/error; async_region shows a skeleton then swaps).
- **no-drift lint** — `test_ui_no_drift.py` green across migrated features; red if a banned
  pattern is introduced.
- **palette contrast** — every category × surface ≥ 3:1 in both themes.
- **render gate** — re-run after each tab; §5 checklist self-audit; legend + pinout legible.
- **no regression** — full `pytest tests` stays green; runtime smoke drive (nav, theme toggle,
  package/project switch) stays exception-free.

## 6. Scope guard (YAGNI)
Out of scope: a pure data-schema engine; any brand/accent color (neutral stays); rewriting the
bespoke visual widgets' internals beyond the pinout spacing/legend needed for legibility; the
"fill project components from library" data feature (its own spec). Kit builders are added only
as a real tab needs them — no speculative surface.

## Related, separate thread (not in this spec)
**Fill project component fields from the library** — match each project component to the central
library and populate its fields (MPN/manufacturer/datasheet/footprint) from the library's
enriched data. Data/logic feature touching `LibraryManager.py`/`projects.py` (partly owned by the
sourcing session); gets its own spec + plan after this one, with field-set/trigger confirmed.
