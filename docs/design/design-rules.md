# NETDECK UI — Design Rules

**Status:** living guardrails · **Read this before touching any UI.**
**One line:** make it look like a shipping product (Linear, Raycast, Vercel, native
macOS / Windows 11), not a generated mockup. When in doubt, *remove*.

This document exists because the STM32 tab drifted into a look the user called "ugly"
and "AI-generated." That did not happen by accident — it happened by adding decoration
(borders, pills, tags, accent bars) instead of designing hierarchy and space. The rules
below are the correction. Sections 1–2 and 5 are **stable and never change**. Sections
3–4 hold the concrete tokens/recipes and get locked from the chosen art direction.

---

## 1. Never break (hard anti-patterns)

Each rule is a specific thing that made the app read as AI-made. The pattern is always
the same: decoration standing in for design.

1. **Do not box every value in a bordered pill.** Pills/chips are for *status and
   short tags*, used sparingly (a switch class, a state). A pin number, a net name, a
   terminal, a part number are **not** pills — they are text with hierarchy.
   *Instead:* plain text, differentiated by size / weight / color / column, separated by
   space.

2. **Do not put a border on everything.** Borders are the loudest, cheapest separator
   and the fastest way to look generated. Default to **zero** borders.
   *Instead:* separate regions with whitespace and a subtle background step; use a single
   hairline only where a real edge helps scanning (e.g. a table header underline).

3. **No cards inside cards inside cards.** One container per region, one elevation level.
   If a thing is already inside a panel, it does not also need its own outlined box.

4. **No letterspaced UPPERCASE micro-labels sprinkled around.** `SIDE · ADG714 TERMINAL
   · SWITCHED ROLE · PIN NAMES` everywhere is an AI tell and adds noise.
   *Instead:* let position and hierarchy carry meaning. If a label is truly needed, use
   quiet sentence-case secondary text, and only where context does not already make it
   obvious. One set of column headers for a table is fine; a label on every field is not.

5. **No colored accent bar / rail on a rounded card.** This specific combination is on
   every "looks AI-made" list. Encode class/category some other way (a small dot, the
   text color, a table column) or not visually at all.

6. **Color is meaning, never decoration.** The interface is neutral by default. Hue
   appears *only* where it encodes something (pin class, net category, status), stays
   muted, and is used on the smallest element that carries it (a dot, the text itself) —
   **never as a background tint on a surface.** If removing a color loses no information,
   remove it.

7. **Every view has one focal point.** Decide the single most important thing and make
   it clearly the biggest / brightest; everything else recedes. Near-equal visual weight
   across the whole panel is the flattest, most generated look there is.

8. **Do not reach for a "safe" typeface as a crutch.** The face must be a deliberate
   choice with rationale, set at deliberate sizes and weights. (Locked face in §3.)
   Getting the *hierarchy and sizing* right matters more than the font name.

9. **Pick one small corner radius and use it everywhere,** or use none. Mixed radii and
   "round everything generously" both read as amateur. (Locked value in §3.)

10. **No emoji as section markers. Nothing centered by default.** Long content
    (tables, code, part numbers) scrolls inside its own container; the panel never
    scrolls sideways.

---

## 2. Principles (what to do instead)

- **Hierarchy is the whole game.** Before adding anything, ask: what is the one thing a
  user looks at first here? Make it dominant. Then the second tier, then the rest. Three
  tiers is usually enough.
- **Whitespace does the work borders were doing.** Grouping comes from proximity and
  space, not outlines. Generous, consistent gaps read as "designed."
- **Elevation via background steps, not outlines.** Distinguish surfaces with a small
  lightness change (base → raised), not a stroke.
- **A fixed type scale.** A short, closed set of sizes and weights; never improvise a new
  size. Data uses tabular figures so columns align.
- **Dense is allowed; noisy is not.** This is a pro engineering tool — density is fine
  when it has rhythm, alignment, and air. Cramped-with-boxes is the failure mode.
- **Motion is minimal and purposeful,** and respects reduced-motion.
- **Copy is design material. Title Case for ALL UI text** — labels, headings, named terms,
  buttons, values ("Signal Path", "Source and Drain", "Connected Net", "Must-Switch",
  "Default Card Lane", "Pin Names", "Save to Vault", "Part-Dependent", "Not Applicable").
  Minor words ("and/or/the/in") stay lowercase. **Sentence case ONLY for an actual sentence**
  — the one-hot note, a status/error message, the switch rationale. Never all-lowercase. No
  abbreviations, no em dashes. Signal names, refdes, and nets keep their real casing
  (PE3, GND, U_SW_L100_1).

---

## 3. Locked tokens — direction: **Refined Neutral**

> **Changelog:** 2026-07-08 — the azure "Quiet Instrument" accent (speced 2026-07-04,
> never shipped) is **retired** by owner decision. Direction is **Refined Neutral**:
> the neutral WinUI-grey identity elevated through space, hierarchy, type, borderless
> elevation, and subtle motion — no brand accent. §1/§2/§5 are unchanged.
> **2026-07-11 — Neutral-Glass retheme (owner decision):** the palette is re-ported from
> the approved library-v2 mockup: **DM Sans** interface face (bundled), a pure-neutral
> grey elevation ladder (nav #0e0e0e < canvas #141414 < raised #212121 < inset #2b2b2b in
> dark), thin translucent white hairlines, and a painted ambient radial gradient behind
> solid panels (PyQt has no backdrop blur, so the mockup's glass is pre-composited to
> opaque panels + the gradient supplies the depth — a "native match", not literal frost).
> Radii stay 8/6 (the mockup's 9/7 is a sub-perceptual delta not worth the invariant churn).
> The zero-hue-neutral, monotonic-ladder, and WCAG contracts below are unchanged and still enforced.

The identity is neutral WinUI grey; there is **no brand/accent hue**. The interaction
"accent" is a near-neutral (near-white in dark, near-black in light) used only on
controls. The whole point is restraint: turn ~85% of the pane down so the ~15% that
matters can read. Tokens live in `tools/ui/theme.py` (`DARK`/`LIGHT`); a retheme is
one `set_theme()` + re-applied `qss()`.

**Backgrounds — one monotonic neutral ladder (zero hue shift), separated by elevation not borders.**
`nav` sits one step *below* `canvas` to anchor the frame; `inset` is the ONE lift
(grouped / hover / selected — "one step up = grouped or active"); there is no third box.

| Role | Dark | Light | Use |
|---|---|---|---|
| `nav` | `#191a1c` | `#eaeaeb` | nav rail (below canvas) |
| `base` / `surface` / `canvas` | `#1c1d1f` | `#f3f3f3` | window / tab / content canvas |
| `card` / `raised` | `#232427` | `#fbfbfb` | panels, reading surfaces (the +1 step) |
| `inset` / `card_hover` | `#2a2c30` | `#eeeeee` | the ONE lift: hover / selected / grouped |

**Hairlines (the whole border budget):** `hairline` (`#2e2f33` dark / `rgba(0,0,0,.08)` light),
1px. Used ONLY for real edges that aid scanning: a table-header rule, ledger row dividers,
an optional section-header trailing rule. `stroke`/`divider` are aliases of `hairline` (one
budget). Never 2px, never colored. `hairline_strong` (`#3a3b40` / `rgba(0,0,0,.14)`) is
reserved for one structural divide if ever truly needed.

**Text tiers (hierarchy comes from these + weight, not size — WCAG-AA verified on every surface):**
- `txt1` (`#ffffff` / `rgba(0,0,0,.894)`) — primary: pin hero, stat numbers, group subheads, primary values, live-branch net
- `txt2` (`rgba(255,255,255,.773)` / `rgba(0,0,0,.62)`) — labels, secondary values, detail keys, dim data columns
- `txt3` (`rgba(255,255,255,.529)` / `rgba(0,0,0,.447)`) — micro-labels, section eyebrows, column headers, units, dormant branch, null em-dashes

**Interaction accent (neutral — controls ONLY, never a value color):** `accent`
(`#ededed` dark / `#1b1b1b` light), `on_accent` its inverse. Appears only on: the primary
button, keyboard focus rings (painted, 6px, `ui.motion.paint_focus_ring`), the selected pin-map
cell ring, and the sliding subtab underline. It is neutral by design — no azure/indigo. The
selected-row / grouped wash is the `inset` step, not a hue.

**Semantic (muted, meaning only — a 6px dot and the delivered-net text, nothing else):** the
`CATEGORY_DARK`/`CATEGORY_LIGHT` palettes (power / ground / core / service / lane / must / osc /
fixed / breakout). In the inspector these appear in exactly TWO places: a 6px leading dot, and the
delivered-net mono glyphs. Never a border, a fill, a left-rule on a card, or repeated on every cell.
The pin-map may run them saturated (there, color *is* the data). Selection reuses the neutral accent,
not a category.

**Type — bundled DM Sans for interface/prose (Segoe UI / Inter fallback) + Cascadia Mono
(bundled JetBrains/Geist Mono off-Windows) for all machine data (refdes, nets, pins, terminals
so columns align).** Weights **Regular and Semibold only** — never Bold/Light/Medium. **No letterspacing
anywhere** (the letterspaced UPPERCASE eyebrow is retired — `widgets.eyebrow()` is now Title-case
Semibold `txt3`, zero tracking). Title Case for UI text (see §2). Left-aligned.

**Fixed type scale — named roles in `theme.TYPE_SCALE`, never improvise a size** (`scale_font(role)`;
unknown role raises so no stray size slips in). Hierarchy by role, not one uniform size — the focal
element (pin hero) is largest; labels / section + column headers / units deliberately RECEDE.
- `hero` (signal name): mono 15.5 / Semibold, `txt1` — the one focal element
- `stat`: mono 14 / Semibold, tabular, `txt1` (label `footnote`, `txt3`)
- `payload` (delivered net): mono 10.5 / Semibold, in its category colour
- `group_subhead`: mono 10.5 / Semibold, `txt1`
- `value` / terminal / side: mono 10, `txt1`/`txt2`
- `section` header: Segoe UI 11 / Semibold, `txt2`, optional trailing hairline (recedes)
- `detail_key` / metadata: Segoe UI 9 / Regular, `txt2`, fixed column
- `footnote` (column header / role / through / unit): 8.5, `txt3` — the quietest tier
Mono is reserved STRICTLY for machine values so monospace re-acquires meaning (and gives tabular
digit alignment). Keep the focal hero and the coloured net loud; keep everything that labels or
structures them quiet.

**Radius:** exactly two — `RADIUS_CONTAINER` **8px** (the one panel per region, menus, dialogs) and
`RADIUS_CONTROL` **6px** (buttons, inputs, combos, row hover, focus rings, chips). Flat 4px is retired.
Stadium pills on data are retired.

**Motion (subtle, purposeful, reduced-motion-aware — all in `ui.motion`).** Qt5 QSS has no
transitions, so motion is `QPropertyAnimation`/painted and gated by a single reduced-motion flag
(instant no-op when set; headless render gate + CI run instant). Shipped: eased theme cross-fade,
the sliding subtab underline, painted focus rings. No decorative animation, no bounce, no parallax.

**Spacing:** 4px grid — 4 / 8 / 12 / 16 / 20 / 24 / 32. The device is a ~6:1 contrast between
inter-group and intra-group space: 24px between sections, 2-4px between rows in a group. Data
row 30px, signal-path padding 14px, detail row gap 10px, card interior padding 16px. One shared
left baseline; ledger columns fixed-width so both branch groups align down the pane. The scale is
the single source in code — `ui.theme.SPACE` + `T.sp(role)` (grid steps `xs/sm/md/lg/xl/xxl/xxxl`
and the semantic roles `row`=10, `path`=14, `card`=16, `page`=24, `data_row`=30); route padding
through `sp()`, never a scattered literal.

---

## 4. Component recipes (Refined Neutral)

> Token names below use the shipped `theme.py` keys: `raised` (panels), `inset` (the one
> lift / hover / selected), `txt1`/`txt2`/`txt3` (text tiers), neutral `accent` (controls only).
> The Quiet-Instrument *structure* (borderless ledger, one signal-path container, one-hot
> ghosting by painted opacity) is retained; only the colour/accent language is neutral now.


**Pin header** — one title block on `raised`, no border, no pills. Line 1 baseline row:
`PE3` (Geist Mono 24/600 txt1) · middot txt3 · `Pin 2` (Geist Mono 13/400 txt2); right-
aligned, the ONE sanctioned fill: a `must-switch` chip (coral wash `#221614`, text `#E8756B`,
6px radius, 11/500, no border). Line 2 dim metadata (Geist Sans 12/400 txt2, sentence case,
middot-separated): a 6px category dot + `Ground` · `left side` · `5 V-tolerant`.

**Signal path** — ONE `inset` container (8px radius, 14px pad, no border, no socket card,
no accent bar). Origin pin stated once at left (mono 15/600) with two 1px QPainter connector
elbows to two rows. Each branch is one flow row (not a card): `[state dot] kind(lowercase dim)
· mechanism(mono txt2) · terminals · → · delivered net(category-color mono 14/600) · dest(mono
11 txt3)`. **One-hot ghosting:** closed branch at 100% + FILLED dot; open branch at ~40% + HOLLOW
dot — board state legible with no badge. Footnote one line, txt3. `→` is the only arrow.

**Source / drain ledger** — the biggest win: delete both card wrappers, every pill, every cell
border. One real aligned table (QGridLayout of frameless QLabels, or a text-only paint delegate —
never QFrame chips). Column header once: `side · terminal · connected net · through` (Sans 11/500
txt3, sentence case, one hairline under). Each branch is a lightweight subhead (6px category dot
+ mono group name + trailing dim lowercase role — no box). Data rows 30px, 1px hairline dividers,
full-row hover `inset`. Cells plain text on the fixed grid: side = `▸ source` / `◂ drain`
(glyph + Sans 12 txt2); terminal = mono 13 txt1; net = the payload, category-color mono 13
with a 6px leading dot (the ONLY colored cell); through = mono 12 txt3. Nulls = dim `—`, never a
boxed "None". Both groups share fixed columns. Reads like a datasheet pinout, not a Bento grid.

**Detail** — kill the uppercase seated label chips; this is the quietest block. Plain two-column
definition list: key Geist Sans 12/400 txt2, Title case, fixed 128px, no chip; value 13 txt1
(mono for data, Sans for prose) that wraps fully or truncates with an explicit ellipsis + tooltip.
Rows separated by 10px whitespace. Zero borders, zero chips.

**Stat strip** — keep it, demote the chrome: numbers Geist Mono 22/600 tabular txt1, units pushed
to txt3 so a spec (`±25 mA`) reads distinctly from a count, stats separated by whitespace not
hairlines.

**Pin map** — keep the saturated category colors (color *is* the data here). Selected pin = the
neutral `accent` ring (painted, 6px). This is the one place category color runs at full strength.

**Watch (from the judge's pitfalls):** the ~4-6% elevation steps are load-bearing — verify real
contrast, never shave the eyebrow hairlines to look cleaner. Fix ledger columns in pixels and
guarantee the mono font loads (tabular) or the borderless table collapses. Draw connectors on the
device-pixel grid (integer / 0.5px, cosmetic 1px pen) or they read fuzzy. Do the ghosting by
painting colors at target opacity, NOT by stacking QGraphicsOpacityEffect on live widgets. Discipline
is all-or-nothing: one stray QFrame border or stadium pill reintroduces the generated texture.

---

## 5. Pre-ship checklist

Run this before considering any UI change done. Any "no" is a fix, not a maybe.

- [ ] Can I delete a border, box, or pill and lose **no** information? → delete it.
- [ ] Is there a clear single focal point, or is everything the same weight?
- [ ] Any letterspaced uppercase label I can cut or make quiet sentence-case?
- [ ] Any pill that is not a genuine status/tag? → make it text.
- [ ] Is every use of color carrying meaning, or is some decorative?
- [ ] Is any surface tinted with a category hue? → make it neutral.
- [ ] One elevation level per region, no card-in-card-in-card?
- [ ] Does spacing follow the scale, and do numbers align (tabular)?
- [ ] Final gut check: does this look like it **shipped in a real app**, or like a
      mockup? If the latter, it is not done.
