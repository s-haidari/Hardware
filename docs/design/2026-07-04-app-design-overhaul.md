# NETDECK Desktop — Complete Design Overhaul Spec

**Status:** proposed (awaiting approval) · **Target implementer:** Opus · **Date:** 2026-07-04
**App:** PyQt5 desktop, 3 tabs — *KiCad Manager*, *KiCad Tools*, *STM32 Pins*.

This is the single source of truth for the redesign. It replaces the piecemeal
edits with one coherent system so every tab and screen feels like the same
instrument. Every choice below is grounded in a cited best-practice source
(Fluent 2, enterprise data-table UX, Qt architecture guidance) — see **§10**.

---

## 0. Framework — DECIDED: QFluentWidgets **on PyQt5**, themed grayscale

The user first picked "port to PyQt6 + QFluentWidgets," then delegated: *"whatever
design philosophies I chose you can overwrite with what's better researched."*
Research produced a strictly better option than a PyQt6 port, so we take it.

**Key finding:** QFluentWidgets ships a **native PyQt5 build** (`PyQt-Fluent-Widgets`)
— "a fluent design widgets library based on PyQt5." So we get the *literal* Fluent
components the user wanted **without** migrating the runtime.

| | **CHOSEN: QFluentWidgets on PyQt5** | Rejected: port to PyQt6 first | Rejected: in-house only |
|---|---|---|---|
| Fluent look | Literal Fluent widgets ✓ | Literal Fluent widgets ✓ | Approximated by hand |
| Migration risk | ~none — same runtime we ship | High: enum-scoping, `exec_()`, `QAction` moves, long red period | ~none |
| Custom painters | Stay as-is (PyQt5 QWidgets) | Must be re-homed/ported | Stay as-is |
| Grayscale | `setThemeColor(neutral)` + `Theme.DARK` + custom bg | same | native |
| Effort | Medium | Very high | Medium |

**Why this is better-researched:** the user's real want (from picking QFluentWidgets)
is the Fluent component set + modern feel; the PyQt6 part was incidental. A PyQt5→6
port is a big-bang change orthogonal to the redesign — deferring it removes the
single largest risk while delivering the identical visual outcome. The one concern
with QFluentWidgets (its default colorful Mica look) is neutralized by theming it to
a grey accent on a graphite ground (§6). PyQt6 remains a **later, isolated** upgrade
if ever desired — decoupled from this work.

**Dependency:** `pip install "PyQt-Fluent-Widgets[full]"` (package `qfluentwidgets`).
Everything below assumes this.

---

## 1. Design principles (the rules every screen obeys)

1. **One entry point, then levels.** Each screen answers "what am I looking at?"
   in ≤3 seconds via a summary band, then reveals detail on demand. Cures
   information overload at the source. *(GoodData IA; 3-Second Rule.)*
2. **Progressive disclosure over dumping.** Overview → focus → detail. Never render
   every field at once; a selection drives an inspector. *(Enterprise data-table UX.)*
3. **Hierarchy by size, weight, spacing — not color.** Grayscale luminance + the
   type ramp carry rank. Color is reserved for exactly two jobs (see §2).
4. **One control language.** A single button hierarchy, one nav pattern, one input
   style — learn it once, applies everywhere.
5. **Native, self-contained.** No embedded HTML documents. Data is Qt widgets
   (label/value rows, tables, painted instruments) styled by the same tokens.
6. **De-boxed calm.** Flat panels + hairline rules + generous whitespace. Elevation
   (shadow) only for true overlays. *(Fluent elevation.)*
7. **Structure encodes truth.** Section headers, dividers, and numbering mean
   something (a real grouping or sequence), never decoration.
8. **Plain language, always.** No cryptic tokens, no abbreviations, no
   lowercase-only labels. Full details in §12.

---

## 2. Color — grayscale + two reserved jobs

Color is **not** a decoration budget. Two jobs only:

- **Neutral ACCENT** (`#d6d8dc` dark / `#2c302d` light) — active/selected/focus
  chrome states **only**. Never a data hue.
- **Semantic status** — success / warning / error, *desaturated*, used **only** for
  genuine state (validation results, DRC/ERC pass-fail, duplicate warnings). Kept
  visually distinct from ACCENT so "attention" reads at a glance. This is the single
  sanctioned use of hue and it is state, not category. *(Fluent: semantic color is
  separate from accent.)*

Everything else is the graphite/paper neutral ramp already in `ui_theme`:
`WIN_BG → MAIN_BG → CARD_BG → BORDER → FG_DIM → FG`.

**Encoding that used to be color is now:**
- *Switch axis* (must-switch / oscillator / fixed) → **luminance ramp**
  (`_T_MUST=FG`, `_T_OSC=FG_DIM`, `_T_FIXED=DOT_IDLE`). Most important = most light.
- *Net category* (power/ground/service/lane/…) → **inline text tag** (`POWER`,
  `GND`, `IO`, `SVC`), mono uppercase, muted. Legible without a legend lookup.

Status palette (desaturated, both themes) to be added to `ui_theme`:
`ok #6f8f6a / #4a7a44`, `warn #b8964a / #8a6a2a`, `err #b96a63 / #9a4a44`.

---

## 3. Design tokens

### 3.1 Spacing — 4px base ramp (replaces ad-hoc 2/6/7/14)
`XS 4 · S 8 · M 12 · L 16 · XL 24 · XXL 32 · XXXL 48`. Measured from bounding box.
Layouts use `gap`/spacing, not per-element margins. *(Fluent 4px ramp.)*

### 3.2 Type ramp (Fluent roles → our fonts)
UI face **Geist**; data/identifier face **JetBrains Mono**. Sizes in pt for Qt.

| Role | Use | Size/Line | Weight | Face |
|---|---|---|---|---|
| Display | screen title (rare) | 20 / 26 | DemiBold | Geist |
| Title | section/screen name | 15 / 22 | DemiBold | Geist |
| Subtitle | card/group heading | 12 / 18 | SemiBold | Geist |
| Body | default text | 10 / 15 | Regular | Geist |
| Caption | labels, meta | 8.5 / 13 | Regular | Geist |
| Overline | section header eyebrow | 8 / 12, +8% tracking, UPPERCASE | SemiBold | Geist |
| Data | all numbers/identifiers | 9–10 | Medium, tabular figures | JetBrains Mono |

Rule: **every number, net name, refdes, pin id is mono with tabular figures** so
columns align. Prose is Geist. *(Fluent type ramp; tabular-nums for data.)*

### 3.3 Radius & form
`control 4 · card 6 · chip/pill full · pin-lead 2`. Four forms only:
rectangle, pill, circle, ring (selection). *(Fluent four forms.)*

### 3.4 Elevation
Flat (0) everywhere. Hairline `BORDER` separates regions. **One** soft shadow
(blur 16, 12% alpha) reserved for overlays: menus, hover callouts, dialogs,
the pin hover card. *(Fluent elevation ramp.)*

### 3.5 Motion
120–160ms ease for hover/selection/press; 180ms cross-fade on screen switch.
Honor a reduced-motion flag (env or setting) → durations to 0. Subtle only;
over-animation reads as AI-generated.

---

## 4. Navigation model (one pattern, everywhere)

```
┌───────────────────────────────────────────────────────────────┐
│  NETDECK      [ KiCad Manager ] [ KiCad Tools ] [ STM32 Pins ]  │  ← top pivot (primary)
├──────────┬────────────────────────────────────────────────────┤
│  ▎ Rail  │  Toolbar: Title …………………… [secondary] [ PRIMARY ]   │  ← one command row
│  item    │                                                     │
│  item    │   ── overview band (summary numbers) ──             │
│  item ◀  │                                                     │
│          │   content (one idea per region)                     │
├──────────┴────────────────────────────────────────────────────┤
│  status strip: state ● …  · progress · [ Log ⌃ ]               │  ← persistent status + collapsible log
└───────────────────────────────────────────────────────────────┘
```

- **Top pivot** = the 3 tabs. Segmented, filled-selected, one place. *(Fluent pivot.)*
- **Left Rail** = the *only* sub-nav within a tab (Map/Table/Cells;
  Rename/NetClasses/Settings). No competing tab bars or combos-as-nav.
  *(Fluent NavigationView.)*
- **One command row** per screen: left-aligned title, right-aligned actions,
  exactly one filled **primary** action, rest ghost/default.
- **Status strip** persistent at bottom; **Log** collapses into it (not a full tab
  competing for nav weight).

---

## 5. Component library (`ui_widgets`, token-driven)

Each component reads `ui_theme` tokens and exposes states. Build/verify once,
reuse everywhere.

| Component | Spec |
|---|---|
| `Button(kind)` | `primary` (filled ACCENT), `default` (bordered), `ghost` (borderless), `danger` (semantic err). One height (28px), radius 4, 8/12 padding. States: rest/hover/press/disabled/focus-ring. **All** buttons route through this — no inline `setStyleSheet`. |
| `SegmentedControl` | top pivot + density toggle. Filled selected segment. |
| `Rail` / `RailItem` | left sub-nav; 3px active bar in ACCENT, icon+label, hover/active. |
| `SectionHeader` | Overline eyebrow + hairline rule on flat panel. The structural device. |
| `ReadoutBand` | bench-meter fascia: mono values + Caption labels + neutral dot. Summary-first. |
| `KeyValue` rows | native label/value inspector rows (replaces HTML). Caption key (muted) + Body/Data value; wraps cleanly; optional mono. |
| `Field` | labeled input (QLineEdit/QComboBox) with consistent 28px height, radius 4, focus ring. |
| `DataTable` | wrapper on QTableView: horizontal-only separators, sticky header, density toggle, right-aligned tabular numerics, left text, elide+tooltip, single-sort caret, hover-row. *(Data-table UX.)* |
| `Chip` / `Tag` | pill; used for net-category tags + filters. Grayscale. |
| `EmptyState` | icon + one line + one primary action, centered. |
| `StatusStrip` | state dot (semantic), message, progress, Log toggle. |
| `Instrument` painters | `ChipSilhouette`, `ConnectionDiagram`, pin hover card — read tokens directly, expose `restyle()`. |

---

### 5.1 Our components → QFluentWidgets classes

Most of the kit becomes thin wrappers/config over QFluentWidgets, so we own *policy*
(tokens, states) while the library owns *pixels*. The Fluent type ramp is literally
provided as label classes — a big win over hand-rolling §3.2.

| Our component | QFluentWidgets |
|---|---|
| App shell + nav | `FluentWindow` (frameless title bar, built-in nav) |
| Top tabs / sub-nav | `Pivot` or `SegmentedWidget` (Map/Table/Cells, Rename/NetClasses/Settings) |
| `Button` primary/default/ghost/danger | `PrimaryPushButton` / `PushButton` / `TransparentPushButton` / (danger = PushButton + err qss) |
| `Field` inputs | `LineEdit`, `SearchLineEdit`, `ComboBox`, `SpinBox` |
| Type ramp | `TitleLabel`, `SubtitleLabel`, `StrongBodyLabel`, `BodyLabel`, `CaptionLabel` |
| `DataTable` | `TableWidget`/`TableView` (`setBorderVisible`, `setBorderRadius`, Fluent rows) |
| `Card` / regions | `SimpleCardWidget`, `HeaderCardWidget`, `CardWidget` |
| Status / semantic | `InfoBar` (transient), `InfoBadge` (ok/warn/err counts), `StateToolTip` (progress) |
| `Chip` / filters | `PillPushButton`, `ComboBox` |
| Responsive fills | `FlowLayout`, `SmoothScrollArea` |
| **Instrument painters** | **stay ours** — `ChipSilhouette`, `ConnectionDiagram`, `ReadoutBand` subclass `QWidget`, read tokens + `isDarkTheme()`, dropped into Fluent layouts |

## 6. Theming architecture (QFluentWidgets + our tokens)

- **Drive QFluentWidgets grayscale:** `setTheme(Theme.DARK)` (or AUTO) +
  `setThemeColor(<neutral grey>)` so the Fluent accent is our neutral ACCENT, not
  blue. Graphite ground applied via `setCustomStyleSheet(register=True)` /
  `qconfig` so it re-styles on theme switch. This neutralizes the one QFluentWidgets
  risk (its default colorful Mica). *(QFluentWidgets theme API.)*
- **One token source stays `ui_theme`** — but it now also *feeds* QFluentWidgets:
  our ACCENT → `setThemeColor`; our tones/semantic status → custom QSS snippets via
  `setCustomStyleSheet`. Painters read `ui_theme` directly (and `isDarkTheme()`).
- **Semantic status → `InfoBar`/`InfoBadge`** in the desaturated ok/warn/err colors
  (§2) — the sanctioned hue, wired to real state (duplicates, DRC/ERC, validation).
- **Let the library own restyling** on theme change (its widgets subscribe to
  `qconfig`); we only manually `restyle()` our custom painters. Avoids the QSS
  re-parse cost on large subtrees. *(KDAB / Qt performance.)*
- **Fonts:** register Geist + JetBrains Mono; set as QFluentWidgets font via
  `setFont`/qconfig; tabular figures on the mono face for all data.
- **Phase-0 spike:** confirm a fully grayscale FluentWindow (neutral accent, graphite
  bg, no acrylic tint) before committing the rest — the one unknown to de-risk early.

---

## 7. Per-screen redesign

### 7.1 Shell
Top pivot for the 3 tabs; persistent status strip; global theme toggle in a corner
overflow. Consistent 16px content margins, 8px internal gaps.

### 7.2 KiCad Manager
- **Overview band:** library `ReadoutBand` — Items / Symbols / Footprints / Models /
  Duplicates (duplicates uses semantic **warn** dot when >0).
- **Left rail:** Library · Workflow · Log (Log also mirrored in status strip).
- **Library screen:** command row (Import / Rescan primary), `DataTable` of items
  (name, type, refs, status), selection → `KeyValue` inspector (paths, fields,
  model, footprint) — native, no HTML.
- **Workflow screen:** action rail as `Button` list (consistent, not bespoke
  `#wfAction`), each with title + one-line caption; results into an inline log region.

### 7.3 KiCad Tools
- **Left rail:** Bulk Rename · Net Classes · Project Settings.
- **Each screen:** two-column de-boxed form using `Field`; command row with one
  primary (Apply); a `ReadoutBand` where a summary helps (e.g., #nets, #classes);
  results/preview in a native table, not text dump.

### 7.4 STM32 Pins (the reference screen)
- **Overview band:** package summary — pins, must-switch, oscillator, fixed,
  breakout, cells. Mono values.
- **Left rail:** Map · Table · Cells.
- **Map (primary):** **refined chip silhouette** (§8) left; **native inspector**
  right (header + `ConnectionDiagram` + `KeyValue` detail). Grayscale.
- **Table:** `DataTable` — scannable core columns only, density toggle, filters as
  `Chip`s + search; row click → Map with pin selected. Full field set stays in
  exports.
- **Cells:** **native** (replaces `cells_html`): a `KeyValue` summary + SPI bus
  strip + one compact `DataTable` per ADG714 cell. No HTML.

---

## 8. The chip silhouette (modular, self-populating)

Chosen paradigm: **refined chip silhouette** (physical position preserved, like
CubeMX; leads uniform + identified).

- **Modular renderer** `ChipSilhouette` ingests `authority["positions"]` and computes
  a package outline for any family (LQFP/LGA/BGA/QFN…) from lead count + side
  distribution. Self-populates as data grows — zero hardcoding.
- **Per-pin identity:** every lead uniform size, **separated by a clear gap** (reads
  as discrete units, not a bar); pin number always shown when they fit (ruler when
  dense); name on hover/selected in the hover callout.
- **Grayscale fill** = switch-class luminance (§2). **Selection** = 2px ACCENT ring.
  **Breakout** = subtle hollow notch. **Peripheral highlight** = dashed FG ring.
- Hover callout uses the one elevation shadow.

---

## 9. Killing "data vomit" — concrete rules

- **Summary before detail** on every screen (ReadoutBand / package summary).
- **Tables:** horizontal separators only; density toggle (compact/comfortable);
  right-align + tabular numerics; left-align text; header aligns to content;
  sticky header; elide long cells + tooltip; **core columns visible, rest in
  inspector/export**. *(Data-table UX sources.)*
- **Inspector, not walls of text:** `KeyValue` rows, empty rows omitted, redundant
  rows dropped (e.g., the old `Via: adg714_source`, `Destination` — already shown in
  the diagram).
- **One idea per region**, separated by whitespace + a SectionHeader.
- **Restrained color** so the few semantic cues actually pop.

---

## 10. Sources (best-practice grounding)

- Fluent 2 — [Typography](https://fluent2.microsoft.design/typography),
  [Layout/Spacing](https://fluent2.microsoft.design/layout),
  [Design tokens](https://fluent2.microsoft.design/design-tokens),
  [Elevation](https://fluent2.microsoft.design/elevation).
- [QFluentWidgets](https://qfluentwidgets.com/) (framework option B, considered).
- Qt architecture — [KDAB: Say No to QSS](https://www.kdab.com/say-no-to-qt-style-sheets/),
  [Qt Style Sheets + Custom Painting](https://wiki.qt.io/Qt_Style_Sheets_and_Custom_Painting_Example).
- Data tables — [Pencil & Paper](https://www.pencilandpaper.io/articles/ux-pattern-analysis-enterprise-data-tables),
  [UX Planet](https://uxplanet.org/best-practices-for-usable-and-efficient-data-table-in-applications-4a1d1fb29550).
- Information hierarchy / overload — [GoodData dashboard IA](https://www.gooddata.ai/blog/six-principles-of-dashboard-information-architecture/),
  [3-Second Rule](https://evontech.com/component/easyblog/the-3-second-rule-how-visual-hierarchy-cures-information-overload-in-ui-ux-design.html),
  [IxDF Visual Hierarchy](https://ixdf.org/literature/topics/visual-hierarchy).

---

## 11. Implementation roadmap (for Opus — each phase compiles, 52 tests green, screenshot proof)

0. **Dependency + grayscale spike. ✓ DONE (2026-07-04).** Installed
   `PyQt-Fluent-Widgets[full]` 1.11.2 into the venv; pinned in `requirements.txt`;
   52 tests still green. Proved grayscale: `setTheme(Theme.DARK)` +
   `setThemeColor(ACCENT #d6d8dc)` renders the primary button neutral (not Fluent
   blue), everything else grayscale on graphite. Bridge shipped as
   `tools/fluent_theme.py` (`apply_grayscale_fluent`, `window_qss`, `count_badge`,
   `status_badge`; run `python -m fluent_theme` to eyeball it).
   **Finding:** use a **neutral `count_badge`** for plain counts; reserve the
   semantic `status_badge` (ok/warn/err) for real state — confirmed both render
   correctly side by side.
1. **Tokens.** Extend `ui_theme`: spacing ramp, type ramp helpers, radius, semantic
   status colors, elevation; add the QFluentWidgets bridge (feed ACCENT→`setThemeColor`,
   status→`setCustomStyleSheet`). (Grayscale tone foundation already begun.)
2. **Kit on QFluentWidgets.** Re-home `ui_widgets` onto the §5.1 mapping — one
   `Button` family, one `Field`, one `DataTable`, ramp labels; delete all inline
   `setStyleSheet`/bespoke object-names.
3. **Shell + nav.** Convert the shell to `FluentWindow`; 3 tabs as nav; `Pivot`/
   `SegmentedWidget` sub-nav; persistent status strip + `InfoBar`/`InfoBadge`.
4. **STM32 reference screen.** `ChipSilhouette` + native inspector + native Cells +
   grayscale sweep (folds in tasks #40–#43 here).
5. **KiCad Tools** onto the kit.
6. **KiCad Manager** onto the kit.
7. **Cross-tab polish + live proof:** offscreen renders (light+dark) for every
   screen, live desktop capture, reduced-motion check, tests green, commit + push.

**Verification each phase:** `./.venv/Scripts/python.exe -m py_compile` →
`-m pytest tests -q` (52) → offscreen render → live capture.

> **Note on the in-flight grayscale edit:** the tone foundation added to
> `stm32_pins_tab.py` (`_T_MUST/_T_OSC/_T_FIXED/_T_SEL`, `_refresh_tones`) is
> forward-compatible and stays; the rest of the piecemeal STM32 work (#40–#43) is
> now sequenced under phase 4 rather than done ad-hoc.

---

## 12. Voice & copy standard

Every piece of app-authored text is plain, complete, and properly capitalized.

- **No cryptic tokens.** Never surface internal identifiers, route keys, or enum
  values as UI text (e.g. `adg714_source`, `SWITCH_OSC_OPTIONAL`). Translate to words.
- **No abbreviations.** Spell it out: Peripherals not Periph, References not Refs,
  Footprint not FP, Channel not Ch, Duplicate not Dup, Configuration not Config,
  Power/Ground/Service not PWR/GND/SVC. (Standard units stay as units: V, Ω, mA.)
- **Title Case labels, never lowercase.** Every label, header, button, nav item, tag,
  and key-value key is Title Case ("Pin Names", "Supply Voltage", "Switch Class") or an
  uppercase letter-spaced overline — never sentence-case or lowercase-only. Only full
  descriptive sentences stay sentence case.
- **Net names un-abbreviated.** Expand generated net names on display via a canonical
  map: `_TGT → _TARGET` (VBAT_TGT → VBAT_TARGET), `_OSC_ → _OSCILLATOR_`
  (SERVICE_OSC_IN → SERVICE_OSCILLATOR_IN). Prefer regenerating the authority's net
  names in full form so schematic and UI match. Refdes and STM32 pin names
  (J_EDGE_L100_1, PA13, XU_TGT100_1) stay verbatim — unambiguous part identifiers,
  always shown beside a Title-Case label.
- Applies everywhere: table headers, tags, tooltips, empty states, inspector rows,
  status messages, exports' human-facing columns.

## 13. Connection detail model (Source / Drain, components, PCB)

Every pin — switched OR direct — shows its full signal path split into two sides, so
there is never a "combined" cell to decode:

- **Source side:** ADG714 **source terminal** (Sn · pin) ← target socket pin
  (`XU_TGT100_1` pin N · name) **through** the ZIF socket (`Yamaichi IC51-1004-809`).
- **Drain side:** ADG714 **drain terminal** (Dn · pin) → delivered net (un-abbreviated)
  **through** the card connector (`Samtec QTH-060-03-L-D-A` · contact), the local ground
  pour (GND drains), or the `R_IO_LANE` 33 Ω series resistor for the default lane branch.
- **Direct (non-switched) pins** use the same two-sided model: socket pin → ZIF socket →
  `R_IO_LANE` 33 Ω → connector contact → `CARD_LANE_0NN`.
- The **Switch Cells** view lists every channel as two rows (Source, Drain) with
  *Connected Net* and *Through Component* columns; the **Map** inspector draws the same
  as a left-to-right signal-flow diagram. Both cover switched and direct pins.

**PCB placement is first-class.** For each cell, state the intended physical setup and —
where the board file exists — the measured reality:
- ADG714 placed directly beneath its socket-pin cluster; source traces short
  (target ≤ ~10 mm) to limit stub capacitance on the switched line.
- Series resistors + their connector contacts adjacent; GND drains stitch into the local
  ground pour.
- Connector stack geometry (part-over-part, pitch, board-to-board height, amps/contact)
  shown from the connector definition.
- SPI control bus daisy-chained cell 1→N; clock/sync/reset broadcast; 100 nF at each
  ADG714 supply.
- **Measured distances** read live from the `.kicad_pcb` once placed — the bridge to the
  KiCad-project-settings + 3D-viewer work in the audit roadmap
  (`docs/design/2026-07-04-codebase-audit.md`).

**Status is context-scoped.** The bottom strip reflects the active tab: on STM32 Pins it
shows authority/package state. "Duplicate Items" (same symbol/footprint in more than one
library) belongs to **KiCad Manager only** and must never bleed into the pinout, where it
means nothing.
