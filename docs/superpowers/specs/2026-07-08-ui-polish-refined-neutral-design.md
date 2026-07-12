# UI Polish — Refined Neutral, Foundation-First

**Status:** approved design · **Date:** 2026-07-08 · **Owner directive:** "ensure the design
and ui are all amazing, using Qt to its fullest to look impressive while still following our
design rules" + "go all out, super polished."

**Direction chosen (by the owner):** **Refined Neutral** — keep the neutral WinUI-grey identity,
**no brand accent color**. Elevate purely through craft: space, hierarchy, typography, borderless
elevation, refined interaction, and subtle motion. The never-shipped azure "Quiet Instrument"
palette in `design-rules.md` §3 is **retired**, and the doc is reconciled to the shipped reality.

**Sequencing (by the owner):** **Foundation first** — rebuild the shared design system so every
screen lifts at once, then polish tab by tab. The render gate is re-run after each phase and
self-audited against `design-rules.md` §5 before hand-back.

**Non-negotiable guardrails (from `design-rules.md`, all STABLE sections):** §1 anti-patterns,
§2 principles, §5 checklist. This work must move the app *toward* full compliance, never away.
Contrast is verified (WCAG AA) on every text tier × surface; discipline is all-or-nothing (one
stray border or stadium pill reintroduces the generated texture).

---

## 0. Current state (grounded in rendered screens, 2026-07-08)

The app is already competent — dense, clean, Linear/Vercel-adjacent. This is a **refinement**
job, not a rescue. But it currently **violates its own stable rules** in visible ways:

- **Letterspaced UPPERCASE micro-labels everywhere** (`WORKSPACES`, `DETAIL`, `CONNECTION
  DIAGRAM`, `NET COLOUR`, `STM32F PACKAGE`, stat labels). §1.4 calls this the #1 "AI tell."
  All flow from one function: `widgets.eyebrow()` (the app's only `setLetterSpacing`).
- **Decorative card borders** where §1.2/§3 want borderless elevation: connection-diagram
  rows, the detail panel, the symbol/footprint/3D preview boxes.
- **Ad-hoc elevation ladder** — `theme.py` tokens are not monotonic (`nav` below `base`,
  `surface` above, `card` above that, `inset` back down); elevation reads muddy.
- **Flat radius** — 4px everywhere; §3's considered 8/6 two-tier never adopted.
- **Same-weight button clusters** (`Refresh Sourcing` / `Enrich Blanks` / `Import ZIP` all
  identical) — no clear primary/secondary hierarchy, flattening the focal point (§7).
- **No motion** — instant state flips; no eased hover/selection/tab/theme transitions.

What is already right and must be preserved: category hues used as *meaning only*; Segoe UI +
Cascadia/Geist mono split; Regular/Semibold discipline; the borderless data-table (`W.data_table`);
the render-gate regression harness.

---

## 1. Foundation (Phase A) — the shared design system

Everything here lifts all 6 tabs + shell simultaneously. Files: `theme.py`, `widgets.py`,
`shell.py`, plus a new `ui/motion.py`.

### 1.1 Token ladder — one intentional, monotonic *neutral* elevation story
Replace the ad-hoc ladder with three deliberate steps, each a ~+4–6% neutral lightness lift,
**zero hue shift** (keeps the WinUI-grey character the owner asked to keep). Starting targets
(final values contrast-verified in implementation):

| Role | Dark | Light | Use |
|---|---|---|---|
| `canvas` | `#1C1D1F` | `#F3F3F3` | window / tab base |
| `nav` | `#191A1C` | `#EAEAEB` | nav rail (one step *below* canvas, anchors the frame) |
| `raised` | `#232427` | `#FBFBFB` | panels / reading surfaces (the +1 step) |
| `inset` | `#2A2C30` | `#EEEEEE` | the ONE lift: hover / selected / grouped (+1 more) |
| `hairline` | `#2E2F33` | `rgba(0,0,0,.08)` | the whole border budget: table-header rule, row dividers, eyebrow trailing rule |
| `hairline_strong` | `#3A3B40` | `rgba(0,0,0,.14)` | rare structural divide only |

"One step up = grouped or active." No third box; elevation, never a stroke, separates regions.
Keep `txt1/txt2/txt3` tiers (already WCAG-tuned) and the meaning-only category palette as-is.

### 1.2 Radius — two deliberate values
`8px` containers (the one panel per region, menus, dialogs) · `6px` controls (buttons, inputs,
combos, row hover, focus rings, chips). Retire flat 4px. Mixed-but-deliberate per §1.9/§3.

### 1.3 Type scale — fixed and formalized
Lock the §3 scale as named helpers so no size is ever improvised: hero / stat / group-subhead /
value / section-header / detail-key / column-header-footnote. **Regular + Semibold only.**
**Tabular figures** enabled on every mono data column (`QFont.setStyleStrategy` / feature) so
digits align — the borderless tables depend on this.

### 1.4 Hairline crispness
All 1px separators and QPainter connectors drawn on the device-pixel grid (integer / 0.5px,
cosmetic 1px pen) so they read sharp at fractional DPI, never fuzzy.

## 2. Anti-pattern removal (Phase A, same pass) — biggest visible uplift

- **De-letterspace everything.** Rewrite `eyebrow()` → Title-case Semibold `txt3`, zero
  tracking (optionally a trailing hairline rule for structure per §4). Re-skins every section
  header app-wide from one edit. Audit for any other `setLetterSpacing`/uppercase call sites.
- **Remove decorative borders** → borderless elevation: connection-diagram rows become one
  `inset` container (no per-row box), detail panels drop their frame, preview boxes (symbol/
  footprint/3D) separate by space + a single background step, not outlines.
- **Establish button hierarchy** — exactly one primary per view (the focal action); the rest
  become `ghost`/subtle. Kill same-weight clusters.

## 3. Interaction craft — "Qt to its fullest" *within* restraint (Phase A)

New `ui/motion.py` — a tiny, reduced-motion-aware animation layer (Qt5 QSS has no transitions,
so this is `QPropertyAnimation`/`QVariantAnimation` driven, honoring
`QGuiApplication` reduced-motion / a config flag):

- **Eased hover & selection** — nav item, table row, subtab: 120–160ms ease-out on the
  background/opacity, not an instant snap.
- **Sliding subtab underline** — the active-tab rule animates to its new position instead of
  jumping (a single painted rule that tweens x/width).
- **Theme cross-fade** — dark/light toggle fades through a grabbed pixmap overlay rather than a
  hard flip (one short fade; falls back to instant under reduced-motion).
- **Custom-painted focus rings** — crisp 6px-radius neutral ring on keyboard focus (visible
  keyboard path), painted, not a QSS box, so it's consistent across widget types.
- **Press feedback** — subtle scale/level change on primary-button press.

All motion is minimal, purposeful, and instantly disabled under reduced-motion. No decorative
animation, no bounce, no parallax.

## 4. Polish layer — "go all out" (Phase B, shared patterns)

Shared, reusable treatments that then get applied per-tab. Each is a small widget/helper in
`widgets.py` so tabs adopt it uniformly:

- **Empty states** — one quiet pattern (muted glyph + one Title-case line + optional single
  action), replacing raw gray strings ("Not looked up yet.", "3D Preview Unavailable",
  "No Symbol"). Centered *only* here (§10 allows it for genuine empty states).
- **Loading / skeleton states** — replace bare "Loading…" / "Auditing every sheet…" with a
  refined determinate/indeterminate indicator and skeleton rows for tables (shimmer honors
  reduced-motion → static). Off-GUI-thread loads already exist (`run_populate`); this is the
  visible half.
- **Refined scrollbars** — thin overlay style that fades in on hover/scroll and out when idle.
- **Elevation shadows for popovers only** — a single soft shadow token for menus / dialogs /
  tooltips (never on cards — surfaces still separate by background step, not shadow).
- **Consistent iconography** — nav + inline icons unified to one line weight, pixel-snapped,
  aligned on a shared baseline; sized from the type scale, tinted from text tiers.
- **Tooltips** — consistent, informative, delayed; styled from tokens (already partly themed).
- **Selection & keyboard nav** — every interactive surface reachable and legibly focus-ringed;
  selected-row wash = the one `inset` step, reused everywhere.

## 5. Per-tab passes (Phase C) — one commit per tab, render-gated each

Each tab gets a focused pass applying the foundation + polish patterns, with a screen-specific
"what "amazing" means here" checklist. Order by impact:

1. **Bench** (the original "ugly" origin) — de-card the connection diagram into the §4 "signal
   path" recipe; de-letterspace the stat strip + legend; give the view one clear focal point
   (the pinout graphic); tighten the detail definition-list. Fold in open BENCH gripes where
   cheap (BENCH-03 palette, BENCH-08 detail pane, BENCH-13 pill copy).
2. **Library** (the daily driver) — de-frame symbol/footprint/3D boxes; empty/loading states;
   button hierarchy (one primary); part-list row rhythm + selected wash; humanized-name
   hierarchy already present, make it the focal tier.
3. **Projects / BOM** — the borderless data-table is good; apply skeleton loading, primary-action
   hierarchy on export menus, consistent section headers; procurement/BOM export polish continuity.
4. **PCB Setup** — netclass table crispness (sticky header already), profile dropdown as a real
   control, value-editability affordance (PCB-11) via typography not boxes.
5. **Git** — status/diff readability, quiet state chips (genuine status → allowed), commit affordance.
6. **Settings** — the quietest screen; definition-list layout, theme toggle with the new
   cross-fade, remove any residual chrome.
7. **Routing** — (new tab from the parallel session) — bring it onto the shared foundation so it
   matches; coordinate to avoid file collisions (that session owns `tools/routing_engine/` and
   `features/routing.py`; touch only shared theme/widgets it consumes, or hand it the tokens).

## 6. Doc reconciliation (Phase A tail)

Rewrite `design-rules.md` §3 ("Locked tokens — Quiet Instrument / azure") and §4 recipes to
describe the **shipped Refined-Neutral reality**: the real neutral ladder (§1.1), 8/6 radii,
neutral interaction accent (near-white/near-black), the meaning-only category palette, and the
motion policy. §1, §2, §5 stay **verbatim** (stable, never change). Add a one-line changelog
noting azure was retired 2026-07-08 by owner decision.

---

## Architecture / isolation

- `theme.py` — token source of truth; retheme = `set_theme()` + re-apply `qss()`. Unchanged
  contract, refined values + new hairline/radius tokens + type-scale helpers.
- `widgets.py` — the primitive kit; every visual pattern (eyebrow, section header, stat strip,
  empty state, skeleton, definition list, button roles) lives here so tabs stay thin and
  consistent. Each primitive: clear purpose, token-driven, independently render-testable.
- `ui/motion.py` (new) — the only place animation lives; single reduced-motion gate; primitives
  (`ease_bg`, `slide_rule`, `cross_fade`, `focus_ring`) consumed by widgets/shell. Isolated so
  motion can be globally disabled and unit-reasoned.
- `shell.py` — nav, page host, theme application; consumes motion + tokens.
- `render_gate.py` — unchanged harness; the acceptance surface (16 surfaces × 2 themes).

## Testing & acceptance

- **Render gate** re-run after every phase; PNGs self-audited against the §5 checklist
  (borders/pills deletable? one focal point? no letterspacing? color = meaning? one elevation
  per region? spacing on-grid + tabular? "shipped, not mockup"?).
- **Unit tests** for pure logic: token monotonicity (each step strictly lighter/darker in the
  right direction), type-scale helpers return locked sizes/weights, motion respects the
  reduced-motion flag (animations become no-ops), `eyebrow()` emits zero letterspacing.
- **Contrast tests** — every text tier on every surface computed ≥ WCAG AA (extend the existing
  contrast check that drove the txt3→txt2 fix).
- **No-regression** — the full app suite (`tests/`, offscreen) stays green; existing widget/leak
  tests updated only where a primitive's structure legitimately changes.

## Scope guard (YAGNI)

Out of scope: frameless custom window chrome (SHELL-01, decision-gated/deferred); any azure/brand
palette; new features beyond visual polish (BOM/procurement features continue separately). If a
tab pass surfaces a deep functional gripe, it is logged to the tracker, not silently expanded here.
