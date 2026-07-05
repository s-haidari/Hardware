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
- **Copy is design material.** **Sentence case** — capitalize only the first word and any
  proper nouns / acronyms ("Signal path", "Must-switch", "Connected net"), never Title Case
  ("Signal Path") and never all-lowercase ("signal path"). No abbreviations, no em dashes,
  complete sentences for notes. Signal names, refdes, and nets keep their real casing
  (PE3, GND, U_SW_L100_1).

---

## 3. Locked tokens — direction: **Quiet Instrument**

Chosen 2026-07-04 (Vercel/Geist surfaces + Linear's borderless property list; azure
accent carries identity, not a trendy typeface). The whole point is restraint: turn
~85% of the pane down so the ~15% that matters can read.

**Backgrounds (three steps, separated by elevation not borders):**
- `bg_base #0B0C0E` — window / tab canvas
- `bg_raised #131519` — the inspector reading surface (~+4% lift; header, ledger, detail all live here as plain text)
- `bg_inset #1A1D22` — the ONE lift-step: the single signal-path container, and full-row hover / selected-row wash. "One step up = grouped or active." There is no third box.

**Hairlines (the whole border budget):** `#23262C` (1px, ~5-6% white). Used ONLY for: the
3 section-eyebrow trailing rules, the ledger row dividers, and the one table-header rule.
Never 2px, never colored except the azure focus/selected ring. Reserve `#2E323A` for one
structural divide if ever truly needed.

**Text tiers (hierarchy comes from these + weight, not size):**
- `text_1 #ECEEF1` — primary: the pin hero, stat numbers, group subheads, primary values, the live-branch net
- `text_2 #9AA0AA` (~58%) — labels, secondary values, detail keys, the ledger side column
- `text_3 #656B75` (~40%) — micro-labels, column headers, units, through-component, section eyebrows, the dormant branch, null em-dashes

**Accent (azure — interaction ONLY, never a value color):** `#4FA1E6` · hover `#6BB2EE` ·
press `#3E8ED0` · selected-row wash `#142230`. Appears only on: selected pin-map cell ring,
selected ledger-row 2px left-rule, keyboard focus rings, primary button. Azure (instrument),
deliberately not indigo/purple (the AI-startup cliché); it cannot collide with `lane` (teal).

**Semantic (muted ~12%, meaning only — a 6px dot and the delivered-net text, nothing else):**
must `#E8756B` · power `#D6A44C` · osc `#E67E33` · ground `#8B94A1` · core `#AC8DD8` ·
service `#6FB893` · lane `#57AEBE` · fixed `#767C86`. In the inspector these appear in
exactly TWO places: a 6px leading dot, and the delivered-net mono glyphs. Never a border,
a fill, a left-rule on a card, or repeated on every cell. The pin-map may run them more
saturated (there, color *is* the data). Selection reuses the azure accent, not a category.

**Type — native Windows: Segoe UI (Variable) for interface/prose + Consolas / Cascadia Mono
for all machine data (refdes, nets, pins, terminals so columns align).** Weights **Regular
and Semibold only** — never Bold, never Light, never Medium. No letterspacing anywhere.
Sentence case (see §2). Left-aligned. Sizes in pt (Qt); px ≈ pt × 1.333 at 96 dpi. Stay at
or above the floor: **never below 12px Regular / 14px Semibold** (≈ 9pt / 10.5pt). 120-135%
line height, 8px grid.
- Pin hero (signal name): mono ~17pt / Semibold, text_1 — the focal element
- Stat numbers: mono ~15pt / Semibold, tabular, text_1 (label ~9.5pt Regular, in text_3)
- Section header: Segoe UI ~12.5pt / Semibold, text_2, with a trailing hairline
- Group subhead: mono ~10.5pt / Semibold, text_1
- Primary value / net: ~10.5-11pt (mono for data, Semibold for the delivered net), text_1
- Secondary / label / side / column header: Segoe UI ~9.5-10.5pt / Regular, text_2 or text_3
- Detail key: Segoe UI ~10.5pt / Regular, text_2, fixed 140px column
Mono is reserved STRICTLY for machine values so monospace re-acquires meaning. Enable tabular
figures wherever digits stack. Deviation note: the pure-Windows guidance recommends a 16px body;
this dense engineering inspector runs one step tighter (~14px) to keep the pin table scannable,
while holding the 12px / 14px legibility floor.

**Radius:** exactly two. 8px for the one container (signal-path) and menus; 6px for controls,
the row hover, focus rings, and the single must-switch chip. Stadium pills on data are retired.

**Spacing:** 4px grid — 4 / 8 / 12 / 16 / 20 / 24 / 32. The device is a ~6:1 contrast between
inter-group and intra-group space: 24px between sections, 2-4px between rows in a group. Data
row 30px, signal-path padding 14px, detail row gap 10px. One shared left baseline; ledger
columns fixed-width so both branch groups align down the pane.

---

## 4. Component recipes (Quiet Instrument)

**Pin header** — one title block on `bg_raised`, no border, no pills. Line 1 baseline row:
`PE3` (Geist Mono 24/600 text_1) · middot text_3 · `Pin 2` (Geist Mono 13/400 text_2); right-
aligned, the ONE sanctioned fill: a `must-switch` chip (coral wash `#221614`, text `#E8756B`,
6px radius, 11/500, no border). Line 2 dim metadata (Geist Sans 12/400 text_2, sentence case,
middot-separated): a 6px category dot + `Ground` · `left side` · `5 V-tolerant`.

**Signal path** — ONE `bg_inset` container (8px radius, 14px pad, no border, no socket card,
no accent bar). Origin pin stated once at left (mono 15/600) with two 1px QPainter connector
elbows to two rows. Each branch is one flow row (not a card): `[state dot] kind(lowercase dim)
· mechanism(mono text_2) · terminals · → · delivered net(category-color mono 14/600) · dest(mono
11 text_3)`. **One-hot ghosting:** closed branch at 100% + FILLED dot; open branch at ~40% + HOLLOW
dot — board state legible with no badge. Footnote one line, text_3. `→` is the only arrow.

**Source / drain ledger** — the biggest win: delete both card wrappers, every pill, every cell
border. One real aligned table (QGridLayout of frameless QLabels, or a text-only paint delegate —
never QFrame chips). Column header once: `side · terminal · connected net · through` (Sans 11/500
text_3, sentence case, one hairline under). Each branch is a lightweight subhead (6px category dot
+ mono group name + trailing dim lowercase role — no box). Data rows 30px, 1px hairline dividers,
full-row hover `bg_inset`. Cells plain text on the fixed grid: side = `▸ source` / `◂ drain`
(glyph + Sans 12 text_2); terminal = mono 13 text_1; net = the payload, category-color mono 13
with a 6px leading dot (the ONLY colored cell); through = mono 12 text_3. Nulls = dim `—`, never a
boxed "None". Both groups share fixed columns. Reads like a datasheet pinout, not a Bento grid.

**Detail** — kill the uppercase seated label chips; this is the quietest block. Plain two-column
definition list: key Geist Sans 12/400 text_2, Title case, fixed 128px, no chip; value 13 text_1
(mono for data, Sans for prose) that wraps fully or truncates with an explicit ellipsis + tooltip.
Rows separated by 10px whitespace. Zero borders, zero chips.

**Stat strip** — keep it, demote the chrome: numbers Geist Mono 22/600 tabular text_1, units pushed
to text_3 so a spec (`±25 mA`) reads distinctly from a count, stats separated by whitespace not
hairlines.

**Pin map** — keep the saturated category colors (color *is* the data here). Selected pin = azure
ring. This is the one place color runs at full strength.

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
