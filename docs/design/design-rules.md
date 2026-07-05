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
- **Copy is design material.** Full, Title-Case labels; no abbreviations, no em dashes,
  complete sentences for notes. (Already enforced; keep it.)

---

## 3. Locked tokens

> **Pending the chosen art direction** (design workflow in progress). Until locked, the
> current graphite base below is the working reference. Do not treat as final.

**Backgrounds (steps, not borders):**
- Base: `#0f1012` · Panel: `#17181b` · Raised: `#1e2024` _(to be re-tuned per direction)_

**Text tiers:** Primary `#e9eaed` · Secondary `#8b8f97` · Tertiary _tbd_
**Hairline (use rarely):** `#2b2e34`
**Accent (neutral, chrome only):** `#d6d8dc`
**Semantic (muted, meaning only):** power `#c6a366` · ground `#7f8b9a` · core `#a98cc0`
· service `#77a688` · lane `#6f93b5` · must `#c9736c` · osc `#c99f5e` · fixed `#8b8f97`

**Type:** _face + scale to be locked from the chosen direction._ Mono for refdes / nets /
contacts.
**Radius:** _one value, to be locked._
**Spacing scale:** _to be locked (e.g. 4 · 8 · 12 · 16 · 24 · 32)._

---

## 4. Component recipes

> _To be written once the direction is locked — the concrete, borrow-nothing treatment
> for: pin header, signal-path view, source/drain ledger, detail block, pin map, and the
> Manager table. Each recipe states exactly what is shown, its hierarchy, and what is
> deliberately **not** drawn._

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
