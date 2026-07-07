# SP3-A Findings Ledger

Source of truth for the fix phase. Bar: **zero critiques, dark AND light**, ordered
worst-first. Fixes conform to `docs/design/design-rules.md` §1–4 and, above all, to **what
SP2 shipped as pristine on the Library page** — the calibrated bar.

## Calibration (code-confirmed against Library, the SP2 "pristine" reference)

Library is zero-critique and **uses the same shared helpers** as Bench/Projects
(`W.tag`, `W.Card`, `W.data_table`, `W.Verdict`, `W.dl`). So whatever Library ships through
those helpers **is the bar** and must NOT be "fixed":

- **Uppercase table column headers** — `data_table` forces `c.upper()` (widgets.py:332); Library
  ships it. → uppercase headers are ACCEPTABLE. Do NOT change.
- **Table grid lines + the `Card`/chip 1px `stroke` edge** (widgets.py:221, 279, 290) — Library
  ships these. → a subtle 1px edge / table grid is ACCEPTABLE (it reads as a soft elevation edge,
  not a heavy border). Do NOT strip.
- **`W.tag` pills for genuine STATUS** (ok/warn/err) — Library uses `tag("Not Recommended","err")`,
  `tag("Yes","ok")`, `tag("No","warn")`. → status pills are ACCEPTABLE. Keep.
- **Uppercase section eyebrows**, one per region (Library: SYMBOL / FOOTPRINT / 3D MODEL). Keep.

What Library does NOT do, and is therefore a real violation elsewhere:
- **Tint a surface with a category hue** (§1.6).
- **Box plain DATA (counts, names, category values) in a grey `tag("mut")`/custom pill** — Library
  shows data as plain text (mono for machine data); status lives in `Verdict` chips.

So SP3-A job: make Bench / Projects / Settings look like Library. The findings below are only
the places they diverge from it.

---

## Confirmed violations

### V1 — Verdict band is category-tinted (SHARED, P1)
`Verdict._style` (widgets.py:284) uses `bg = {kind}_bg` (green/amber tint) unless `plain=True`.
Library passes `plain=True` (neutral); Bench/Projects don't → tinted surface (§1.6/§5). The status
is already carried by the chip dots, so the tint is pure decoration.
- **Fix (shared, lowest-risk high-leverage):** make `Verdict` always neutral (`bg = card`), i.e.
  retire the `{kind}_bg` branch (or force `plain`). Re-render **Library** to prove no regression
  (Library was already neutral, so it must be pixel-identical).
- Surfaces cleared: bench.overview "Buildable", bench.exports "Pre-Write Checks",
  projects.fabrication-standard "Verify Before Ordering".

### V2 — Plain DATA boxed in grey/custom pills (call sites, P1)
Data (counts, names, families, category values) rendered as `W.tag("…","mut")` (grey pill) or a
custom colored box, where Library uses plain text. NOT the genuine-status tags (those stay).
- **bench.profiles:** the 11 switching-pin colored boxes + family tags + the "LQFP64 / 53 Supported
  Parts / 6 Families" header pills. Retire `_switch_pill` (bench.py:772) / `_chip_grid` (:757);
  render as plain text/table with a 6px category dot. **P1.**
- **projects.bom:** "300 Components" / "105 Line Items" grey pills → plain text. **P2.**
- **projects.health:** "300 Components" / "2 Healthy" grey pills → plain text (the "2 Errors" /
  "159 Warnings" colored ones are genuine status — keep or fold into a Verdict). **P2.**
- **projects.git:** branch-name grey pill → plain mono text. **P2.**

### V3 — Bench Connection Diagram is bordered node cards (call site, P1) — Task 3
bench.overview CONNECTION DIAGRAM = a row of bordered `_node` cards (MCU PIN / ZIF SOCKET / SWITCH
CELL / CONNECTOR / DELIVERS) + arrows — the retired socket-card pattern. → §4 **Signal path**: ONE
`bg_inset` container, flow rows (not cards), 1px connector elbows, no socket cards, one-hot ghosting
by opacity. Rewrite `_node` (bench.py:70) + `_connection_flow` (:132).

### V4 — Category as a filled square swatch → 6px dot (call site, P2)
projects.net-classes net-class color squares → 6px dots (§3). **Exception:** the pin-map legend on
bench.overview keeps saturated squares — there color IS the data (§4). Verify which is which; only
convert the non-pin-map swatches.

### V5 — Actual sentences set in Title Case (call site, P2)
Notes that are real sentences are Title-Cased; §2 wants sentence case for sentences (Title Case is
for labels only).
- projects.board-setup note "Values In Millimetres. Rows Marked Default Are Not Yet Set On The Board."
- projects.net-classes note "VALUES IN MILLIMETRES, ALIGNED TO OSH PARK AND KICAD DESIGN RULES".

### V6 — Repeated grey "Default" pills (call site, P2)
projects.board-setup shows a grey "Default" pill on every row → demote to dim `text_3` "default"
(state via quiet text, not a repeated box). Low priority.

---

## Not violations (Library ships them — do NOT change)
- Uppercase table column headers (`data_table`), table grid lines, `Card`/chip 1px stroke edge.
- `W.tag` status pills with real semantics: projects.health Error/Warning, projects.git Modified,
  bench.exports Pass, bench.* budget OK, mcu-pinout-viewer "5 V" (status, not data).
- Uppercase section eyebrows (one per region).
- Editable spin-grid cell borders (input affordance): projects.net-classes, board-setup.
- bench.all-pins table, projects.bom/health tables — plain `data_table`, already at the Library bar.

## To finish auditing during its fix task
- projects.rename (simple form — not yet rendered-read; audit dark+light in Task 8).
- settings: path values sit in subtle `token` chips — confirm vs Library (likely acceptable; the
  `token` chip is a shipped inline identifier style). Light theme pass.
- shell chrome: nav/tab bar look clean (neutral, azure selection); verify collapsed rail + light in Task 10.

## Deferred (stranded logic — NOT this strand)
- bench.exports "Write Authority Bundle…" (disabled) → SP3-B exporters. Style, do not wire.

---

## Fix task map (re-sequenced)
- **Task 2.5 (new, shared):** V1 Verdict neutral. Re-render Library → prove zero regression. Tiny, high-leverage.
- **Task 3 — bench.overview:** V3 signal-path rewrite (the big one); V1 already cleared by 2.5.
- **Task 4 — bench.profiles:** V2 (switching-pin/family/header pills → text + dots).
- **Task 5 — bench.all-pins:** likely CLEAN after 2.5 — verify only.
- **Task 6 — bench.mcu-pinout-viewer:** verify (5 V is status; table is data_table) — likely clean/minor.
- **Task 7 — bench.exports:** V1 cleared by 2.5; verify Pass/budget are fine (status). Likely clean.
- **Task 8 — projects:** V2 (bom/health/git count+branch pills), V4 (net-class squares→dots), V5 (sentence casing), V6 (Default pills); audit rename.
- **Task 9 — settings:** verify only (near pristine).
- **Task 10 — shell chrome:** verify only; confirm no Library regression from Task 2.5.
