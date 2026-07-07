# SP3-A App-Wide Seamlessness Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every non-Library app surface (Bench, Projects, Settings, shell chrome) render pristine in dark and light against `docs/design/design-rules.md`, backed by a committed reusable render gate.

**Architecture:** A committed offscreen render gate (`tools/ui/render_gate.py`) drives the *real* shell headless, navigates to every page and sub-panel, and grabs the window under both themes to a gitignored dir. Phase B is an audit→fix loop: render all surfaces, self-audit each PNG against design-rules §5 into a severity-ranked ledger, then fix worst-first — conforming each surface to the §4 component recipes — re-rendering until zero critiques.

**Tech Stack:** Python 3, PyQt5, pytest. `QT_QPA_PLATFORM=offscreen` for headless rendering. Existing `ui.theme` / `ui.widgets` / `ui.shell` / `ui.feature` modules.

## Global Constraints

- **`docs/design/design-rules.md` is the immutable standard.** Invent no new tokens, rules, or philosophy. §1–2 (anti-patterns/principles) and §5 (checklist) are stable; §3–4 (locked "Quiet Instrument" tokens + component recipes) are the conformance target. A fix that seems to need a token absent from §3 is a flag to raise with the user, never a silent addition.
- **The bar is pristine, zero critiques** in BOTH dark and light for every surface. Triage orders the work; it does not stop it. P2 nitpicks are fixed last, not deferred.
- **Visual-only scope boundary.** This strand wires NO stranded backend logic (Bench STM32 exporters = SP3-B; extended Projects settings = SP3-C). Fix presentation only; record any stranded-logic dependency in the ledger as deferred. Do not touch behavior-bearing code paths — only their presentation.
- **Copy rule (design-rules §2):** Title Case for all UI labels/headings/buttons/values; sentence case ONLY for actual sentences (status/error/rationale); never all-lowercase; no abbreviations, no em dashes. Signal names/refdes/nets keep real casing (PE3, GND, U_SW_L100_1).
- **Git hygiene:** NEVER `git add -A`. The working tree always shows dirty `libs/My3DModels/*.STEP` + `.gitignore` (LFS smudge) — those are not ours. Stage only named files. Plain commit messages, no `Co-Authored-By` trailers.
- **Rendered PNGs are gitignored** (`build/render/`). The gate and tests are committed; images are not.
- **Out of scope:** the pre-existing 7 `test_audit_kicad_paths` failures (unrelated version-sort bug).
- **This is an audit-driven plan.** Task 1–2 are fully concrete. The fix tasks (3–11) cannot contain exact edits for findings not yet seen: Task 2 produces a ledger where each row carries the precise rule + fix, and each fix task executes its surface's ledger rows against the named §4 recipe, verified objectively by re-render to zero critiques. That deferral is deliberate, not a placeholder.

**Repo commands (run once per shell):**
```bash
cd ~/git/Hardware && source .venv/bin/activate
```

**Render one surface for review:**
```bash
python tools/ui/render_gate.py --surface bench --theme both --out build/render
```

---

### Task 1: Render gate + smoke test + gitignore

**Files:**
- Create: `tools/ui/render_gate.py`
- Create: `tests/test_render_gate.py`
- Modify: `.gitignore` (append `build/` ignore)

**Interfaces:**
- Consumes: `ui.shell.NetdeckShell`, `ui.widgets.Workspace` / `restyle_all`, `ui.theme` (`load_fonts`, `apply` via `shell.apply_theme`), `ui.feature`, `LibraryManager.load_config`. Shell internals used: `win._select(i)`, `win._stack`, `win._page_specs`, `win.apply_theme(dark)`; workspace internals `ws._panels`, `ws._select(k)`.
- Produces: `render_gate.render_all(out_dir, themes=("dark","light"), only=None) -> list[Path]` and `render_gate.main(argv=None) -> int`. Later tasks call `python tools/ui/render_gate.py --surface <fid> --theme both` and read `build/render/<fid>[.<panel-slug>].<theme>.png`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_gate.py`:
```python
"""The committed render gate must produce a dark and light PNG for every
app surface, driving the real shell headless. This is the regression guard
that every surface always builds and grabs under both themes."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_render_gate_writes_dark_and_light_for_every_surface(tmp_path):
    from ui import render_gate
    saved = render_gate.render_all(tmp_path, themes=("dark", "light"))
    assert saved, "no surfaces rendered"
    for p in saved:
        assert p.exists() and p.stat().st_size > 1000, f"empty render: {p}"
    assert any(p.name.endswith(".dark.png") for p in saved)
    assert any(p.name.endswith(".light.png") for p in saved)
    stems = {p.name.split(".")[0] for p in saved}
    for fid in ("bench", "library", "projects", "settings"):
        assert fid in stems, f"missing surface for {fid}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_render_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.render_gate'`.

- [ ] **Step 3: Write the render gate**

Create `tools/ui/render_gate.py`:
```python
"""ui.render_gate — offscreen dark+light screenshots of every app surface.

Drives the REAL shell headless so panels are built against real data, navigates
to each page and Workspace sub-panel, and grabs the window under both themes.
Committed regression gate: re-run after any UI change and self-audit the PNGs
against docs/design/design-rules.md. Images go to a gitignored dir.

    python tools/ui/render_gate.py --out build/render            # all, both themes
    python tools/ui/render_gate.py --surface bench --theme dark
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TOOLS = Path(__file__).resolve().parents[1]        # .../tools
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from PyQt5.QtWidgets import QApplication            # noqa: E402

from ui import theme as T                            # noqa: E402
from ui import widgets as W                          # noqa: E402


def _slug(s: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in s).strip("-")


def _surfaces(win):
    """Build every page; return [(feature_id, panel_name|None, page_idx, ws|None, panel_idx|None)]."""
    out = []
    for i in range(win._stack.count()):
        win._select(i)                               # lazily build the page
        feat = win._page_specs[i][0]
        page = win._stack.widget(i)
        workspaces = page.findChildren(W.Workspace)
        if not workspaces:
            out.append((feat.id, None, i, None, None))
            continue
        for ws in workspaces:
            for k, (name, _) in enumerate(ws._panels):
                out.append((feat.id, name, i, ws, k))
    return out


def render_all(out_dir, themes=("dark", "light"), only=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    T.load_fonts(app)

    import LibraryManager as LM
    from ui.shell import NetdeckShell
    from ui import features  # noqa: F401  importing registers every feature

    win = NetdeckShell(LM.load_config())
    win.resize(1440, 960)
    surfaces = _surfaces(win)

    saved = []
    for theme in themes:
        win.apply_theme(theme == "dark")
        for fid, name, page_idx, ws, k in surfaces:
            if only and fid != only:
                continue
            win._select(page_idx)
            if ws is not None:
                ws._select(k)
            W.restyle_all()
            app.processEvents()
            stem = fid if name is None else f"{fid}.{_slug(name)}"
            path = out_dir / f"{stem}.{theme}.png"
            win.grab().save(str(path))
            saved.append(path)
    win.close()
    return saved


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render every app surface dark+light.")
    ap.add_argument("--out", default="build/render")
    ap.add_argument("--surface", default=None,
                    help="feature id: bench / library / projects / settings")
    ap.add_argument("--theme", default="both", choices=("dark", "light", "both"))
    args = ap.parse_args(argv)
    themes = ("dark", "light") if args.theme == "both" else (args.theme,)
    saved = render_all(args.out, themes=themes, only=args.surface)
    print(f"Wrote {len(saved)} images to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_render_gate.py -v`
Expected: PASS. If it fails on an empty grab (`st_size > 1000`), the offscreen grab needs the window shown — add `win.show(); app.processEvents()` immediately after `win.resize(...)` in `render_all`.

- [ ] **Step 5: Ignore rendered images**

Append to `.gitignore`:
```
# --- SP3-A render-gate output (regenerate with tools/ui/render_gate.py) ---
build/
```

- [ ] **Step 6: Verify existing suites still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py tests/test_sp2_library.py tests/test_library.py -q`
Expected: all pass (prior 51 passed / 1 skipped, plus the new test).

- [ ] **Step 7: Commit**

```bash
git add tools/ui/render_gate.py tests/test_render_gate.py .gitignore
git commit -m "SP3-A Task 1: committed offscreen render gate + smoke test"
```

---

### Task 2: Audit pass → findings ledger

**Files:**
- Create: `docs/design/audit/sp3a-findings.md`

**Interfaces:**
- Consumes: `tools/ui/render_gate.py` (Task 1) and `docs/design/design-rules.md` §1–5.
- Produces: `docs/design/audit/sp3a-findings.md` — the severity-ranked, per-surface fix list every fix task (3–11) executes. Row schema: `| surface | theme | rule (§id) | severity | finding | fix |`.

- [ ] **Step 1: Render every surface, both themes**

Run: `python tools/ui/render_gate.py --out build/render --theme both`
Expected: `Wrote N images to build/render` (N ≈ 2 × surface count). Confirm files exist:
`ls build/render/`

- [ ] **Step 2: Self-audit each PNG against §5**

Read each PNG in `build/render/` (dark and light) with the Read tool. For each, walk the design-rules §5 checklist and §1 hard anti-patterns. Assign severity:
- **P0** — reads as generated / breaks a §1 hard rule (border on everything, pill on data, colored accent bar/rail on a card, card-in-card, category-tinted surface background, letterspaced UPPERCASE micro-labels, emoji section markers, centered content).
- **P1** — major violation of hierarchy / focal point / spacing scale / color-is-meaning / tabular alignment.
- **P2** — nitpick (a stray radius, one off-scale gap, a slightly loud label).

- [ ] **Step 3: Write the ledger**

Create `docs/design/audit/sp3a-findings.md`:
```markdown
# SP3-A Findings Ledger

Source of truth for the fix phase. One row per finding. Bar: zero critiques,
dark AND light. Ordered worst-first (P0 → P1 → P2). Fixes conform to
docs/design/design-rules.md §1–4 — no new tokens.

| # | Surface | Theme | Rule | Sev | Finding | Fix |
|---|---------|-------|------|-----|---------|-----|
| 1 | bench.overview | dark | §1.5 | P0 | Colored accent rail on the authority card | Delete rail; encode class via 6px dot per §4 signal-path |
| ... | ... | ... | ... | ... | ... | ... |

## Deferred (stranded logic — NOT this strand)
- <panel> — <what looks incomplete because logic is stranded> → SP3-B / SP3-C
```
Populate a row for EVERY finding across all surfaces. A surface with no findings gets one row: `| n | <surface> | both | — | clean | none — verify only |`.

- [ ] **Step 4: Commit**

```bash
git add docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 2: full dark+light audit — findings ledger"
```

---

### Task 3: Fix Bench — Overview panel

**Files:**
- Modify: `tools/ui/features/bench.py` — `_authority_panel` and the helpers it uses (`_node` ~L70, `_connection_flow` ~L132, `_arrow` ~L100, `_stat` ~L356).

**Interfaces:**
- Consumes: `docs/design/audit/sp3a-findings.md` rows scoped to `bench.overview`; design-rules §4 recipes **Signal path**, **Stat strip**, **Detail**.
- Produces: an Overview panel with zero critiques. Helper rewrites (`_node`→flow-row, `_connection_flow`→one `bg_inset` container) are reused by later Bench tasks — keep names stable or update all call sites in the same task.

- [ ] **Step 1: Apply the ledger fixes for `bench.overview`**

For each `bench.overview` row, apply the fix. The §4 targets are explicit:
- **Signal path:** ONE `bg_inset` container (8px radius, 14px pad, no border, no socket card, no accent bar). Origin pin at left (mono 15/600) with 1px QPainter connector elbows. Each branch is one flow row, not a card: `[state dot] kind(lowercase dim) · mechanism(mono text_2) · terminals · → · delivered net(category-color mono 14/600) · dest(mono 11 text_3)`. One-hot ghosting by painting opacity, not `QGraphicsOpacityEffect`.
- **Stat strip:** numbers mono 22/600 tabular text_1, units demoted to text_3, separated by whitespace not hairlines.
- Remove every `QFrame` border, pill, and category-tinted background per §1.

- [ ] **Step 2: Re-render Bench**

Run: `python tools/ui/render_gate.py --surface bench --theme both --out build/render`
Expected: `bench.overview.dark.png` / `bench.overview.light.png` refreshed.

- [ ] **Step 3: Self-audit the refreshed PNGs to zero critiques**

Read `build/render/bench.overview.dark.png` and `.light.png`; re-walk §5. If any finding remains, return to Step 1. Mark the ledger rows closed only at zero critiques in BOTH themes.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass (no build regression; behavior paths untouched).

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/bench.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 3: Bench Overview — signal-path/stat-strip conformance"
```

---

### Task 4: Fix Bench — Profiles panel

**Files:**
- Modify: `tools/ui/features/bench.py` — `_profiles_panel` (~L790) and the helpers it uses (`_switch_pill` ~L772, `_chip_grid` ~L757, `_swatch` ~L314, `_legend` ~L337, `_leg_item` ~L333).

**Interfaces:**
- Consumes: ledger rows scoped to `bench.profiles`; §4 **Ledger** / **Detail** recipes and §1.1 (data is not a pill).
- Produces: Profiles panel with zero critiques; `_switch_pill`/`_chip_grid` either retired or reduced to the ONE sanctioned chip (must-switch) per §4 pin header.

- [ ] **Step 1: Apply the ledger fixes for `bench.profiles`**

Per the rows: switch classes/roles are text with hierarchy, not stadium pills (§1.1). A legend swatch is a 6px dot, not a filled chip. The only sanctioned fill is the must-switch chip (coral wash `#221614`, text `#E8756B`, 6px radius, 11/500, no border). Everything else is plain text differentiated by size/weight/color/column on the 4px spacing grid.

- [ ] **Step 2: Re-render Bench**

Run: `python tools/ui/render_gate.py --surface bench --theme both --out build/render`

- [ ] **Step 3: Self-audit to zero critiques**

Read `build/render/bench.profiles.{dark,light}.png`; re-walk §5. Loop until clean in both themes; close the ledger rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/bench.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 4: Bench Profiles — retire pills/chips per design-rules §1"
```

---

### Task 5: Fix Bench — All Pins panel

**Files:**
- Modify: `tools/ui/features/bench.py` — `_allpins_panel` (~L699), `_pin_row` (~L687), `_flow_tokens` (~L739), `_five_v_short` (~L676).

**Interfaces:**
- Consumes: ledger rows scoped to `bench.all-pins`; §4 **Source / drain ledger** recipe (real aligned table of frameless labels, fixed pixel columns, tabular mono, 30px rows, 1px hairline dividers, full-row hover `bg_inset`, nulls = dim `—`).
- Produces: All Pins panel with zero critiques.

- [ ] **Step 1: Apply the ledger fixes for `bench.all-pins`**

Render the pin list as ONE aligned table (QGridLayout of frameless QLabels or a paint delegate — never QFrame chips). Column header once (Sans 11/500 text_3, sentence case, one hairline under). Cells plain text on fixed pixel columns; the net is the only colored cell (category-color mono 13 + 6px leading dot). Mono + tabular figures so columns align. No boxed "None".

- [ ] **Step 2: Re-render Bench**

Run: `python tools/ui/render_gate.py --surface bench --theme both --out build/render`

- [ ] **Step 3: Self-audit to zero critiques**

Read `build/render/bench.all-pins.{dark,light}.png`; loop until clean; close rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py tests/test_ui_shell.py::test_bench_pin_category_from_real_authority -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/bench.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 5: Bench All Pins — borderless aligned ledger table"
```

---

### Task 6: Fix Bench — MCU Pinout Viewer panel

**Files:**
- Modify: `tools/ui/features/bench.py` — `_resolver_panel` (~L528), `_show_pin` (~L429), `_side_of` (~L519), and shared helpers already reworked in Tasks 3/5 (reuse, do not fork).

**Interfaces:**
- Consumes: ledger rows scoped to `bench.mcu-pinout-viewer`; §4 recipes **Pin header**, **Pin map**, **Signal path**, **Source / drain ledger**, **Detail**.
- Produces: the resolver/inspector with zero critiques — the canonical §4 surface (this is the view §4 was written for).

- [ ] **Step 1: Apply the ledger fixes for `bench.mcu-pinout-viewer`**

- **Pin header:** one title block on `bg_raised`, no border/pills. Line 1: `PE3` (mono 24/600 text_1) · middot text_3 · `Pin 2` (mono 13/400 text_2); right-aligned optional must-switch chip. Line 2: 6px category dot + dim metadata (Sans 12/400 text_2, sentence case, middot-separated).
- **Pin map:** keep saturated category colors (color is the data here); selected pin = azure ring. Draw on the device-pixel grid (integer/0.5px cosmetic 1px pen).
- **Signal path / ledger / detail:** reuse the Task 3/5 recipes.

- [ ] **Step 2: Re-render Bench**

Run: `python tools/ui/render_gate.py --surface bench --theme both --out build/render`

- [ ] **Step 3: Self-audit to zero critiques**

Read `build/render/bench.mcu-pinout-viewer.{dark,light}.png`; loop until clean; close rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/bench.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 6: Bench MCU Pinout Viewer — full §4 recipe conformance"
```

---

### Task 7: Fix Bench — Exports panel

**Files:**
- Modify: `tools/ui/features/bench.py` — `_outputs_panel` (~L589).

**Interfaces:**
- Consumes: ledger rows scoped to `bench.exports`; §4 **Detail** recipe and §1 (no card-in-card, no pills). **Scope guard:** presentation only — do NOT wire `to_switchmap_c` / `to_kicad_symbol` / `authority_diff` / `lint_card` (SP3-B). If a control is dead because logic is stranded, style it correctly and note the wiring in the ledger's Deferred section.
- Produces: Exports panel with zero critiques.

- [ ] **Step 1: Apply the ledger fixes for `bench.exports`**

Plain two-column definition list / quiet controls; kill uppercase label chips and any card-in-card. Long content (code, part numbers) scrolls in its own container; the panel never scrolls sideways.

- [ ] **Step 2: Re-render Bench**

Run: `python tools/ui/render_gate.py --surface bench --theme both --out build/render`

- [ ] **Step 3: Self-audit to zero critiques**

Read `build/render/bench.exports.{dark,light}.png`; loop until clean; close rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/bench.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 7: Bench Exports — quiet detail layout (presentation only)"
```

---

### Task 8: Fix Projects — all panels

**Files:**
- Modify: `tools/ui/features/projects.py` — `_health_panel` (~L118), `_bom_panel` (~L206), `_rename_panel` (~L279), `_netclass_panel` (~L372, already SP2-touched — light audit), `_boardsetup_panel` (~L517), `_fab_panel` (~L593), `_git_panel` (~L670).

**Interfaces:**
- Consumes: ledger rows scoped to `projects.*`; §4 recipes as they apply (Detail, Ledger/table, Stat strip). **Scope guard:** do NOT add DRC/ERC severities, the ERC pin matrix, text variables, or track/via/diff-pair tables (SP3-C) — presentation only.
- Produces: all seven Projects panels with zero critiques.

- [ ] **Step 1: Apply the ledger fixes per Projects panel**

Work panel-by-panel through the `projects.*` ledger rows. Common conformance: replace bordered value boxes with plain text hierarchy; tables become borderless aligned grids (fixed pixel columns, tabular mono); one elevation step per region; Title Case labels; category color only on a 6px dot or the datum itself.

- [ ] **Step 2: Re-render Projects**

Run: `python tools/ui/render_gate.py --surface projects --theme both --out build/render`

- [ ] **Step 3: Self-audit every Projects panel to zero critiques**

Read each `build/render/projects.*.{dark,light}.png`; re-walk §5 per panel; loop until all clean in both themes; close rows. (If any single panel proves heavy, split it into its own follow-up commit — but keep the task's deliverable "all Projects panels clean".)

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/projects.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 8: Projects panels — design-rules conformance (presentation only)"
```

---

### Task 9: Fix Settings panel

**Files:**
- Modify: `tools/ui/features/settings.py` — `_settings_panel` (~L68), `_setting_row` (~L22).

**Interfaces:**
- Consumes: ledger rows scoped to `settings`; §4 **Detail** recipe and §1.3 (no card-in-card — `_setting_row` returns `W.Card`; verify it is not a card nested inside a card).
- Produces: Settings panel with zero critiques.

- [ ] **Step 1: Apply the ledger fixes for `settings`**

Each setting row is a quiet two-column row (Title Case key text_2 fixed-width, control right) with whitespace grouping under sentence-case eyebrows — one elevation step, no card-in-card, no bordered pills.

- [ ] **Step 2: Re-render Settings**

Run: `python tools/ui/render_gate.py --surface settings --theme both --out build/render`

- [ ] **Step 3: Self-audit to zero critiques**

Read `build/render/settings.{dark,light}.png`; loop until clean; close rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/features/settings.py docs/design/audit/sp3a-findings.md
git commit -m "SP3-A Task 9: Settings — quiet rows, no card-in-card"
```

---

### Task 10: Fix shell chrome

**Files:**
- Modify: `tools/ui/shell.py` — nav (`_build_nav` ~L125, `_build_pages` ~L137, `NavItem` ~L67), theme toggle button; and `tools/ui/widgets.py` only if a shared chrome primitive (eyebrow, page_title, subtab) needs a shared fix.

**Interfaces:**
- Consumes: ledger rows scoped to `shell` / nav / tab bar; design-rules §1–4 (nav rail is neutral chrome — selection uses the azure accent, not a category; one elevation step; no letterspaced labels).
- Produces: nav rail (expanded + collapsed), sub-tab bar, and theme toggle with zero critiques. Any shared-widget fix must be scoped so it does not regress the already-pristine Library page.

- [ ] **Step 1: Apply the ledger fixes for shell chrome**

Nav items: Title Case, quiet default, azure selected state, 6px radius. Collapsed rail shows icons only, no orphaned labels. Sub-tab bar underline is a single hairline. No decorative color.

- [ ] **Step 2: Re-render ALL surfaces (chrome shows in every shot)**

Run: `python tools/ui/render_gate.py --theme both --out build/render`

- [ ] **Step 3: Self-audit chrome + confirm no Library regression**

Read a representative dark and light shot (chrome is in every window grab) plus `build/render/library.*` to confirm the shared-widget changes did not regress the pristine Library page. Loop until clean; close rows.

- [ ] **Step 4: Verify tests still green**

Run: `python -m pytest tests/test_render_gate.py tests/test_ui_shell.py -q`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add tools/ui/shell.py docs/design/audit/sp3a-findings.md
# add tools/ui/widgets.py ONLY if a shared primitive was touched
git commit -m "SP3-A Task 10: shell chrome — neutral nav, azure selection"
```

---

### Task 11: Final whole-app gate + close-out

**Files:**
- Modify: `docs/design/2026-07-07-sp3a-seamlessness-design.md` (status → complete)
- Modify: `docs/design/audit/sp3a-findings.md` (all rows closed)

**Interfaces:**
- Consumes: every prior task. Produces: the shipped, zero-critique app-wide pass.

- [ ] **Step 1: Render every surface, both themes, fresh**

Run: `python tools/ui/render_gate.py --theme both --out build/render`
Expected: full set regenerated.

- [ ] **Step 2: Whole-app self-audit**

Read every PNG in `build/render/` dark and light. Walk §5 across the whole app. Confirm ZERO open critiques on every surface. Any residual finding reopens the owning task.

- [ ] **Step 3: Full test suite**

Run: `python -m pytest tests/test_render_gate.py tests/test_sp2_library.py tests/test_library.py tests/test_ui_shell.py -q`
Expected: all pass (prior 51 passed / 1 skipped + the render-gate test). Note the pre-existing `test_audit_kicad_paths` 7 failures remain out of scope.

- [ ] **Step 4: Mark complete + commit**

Set the spec status to complete; confirm the ledger shows all rows closed (Deferred section lists only SP3-B/SP3-C items).
```bash
git add docs/design/2026-07-07-sp3a-seamlessness-design.md docs/design/audit/sp3a-findings.md
git commit -m "SP3-A: app-wide seamlessness pass complete — zero critiques dark+light"
```

- [ ] **Step 5: Push**

```bash
git push
```

---

## Self-Review

**Spec coverage:**
- Spec §3 render gate → Task 1 (complete code, drives real shell, gitignored output). ✓
- Spec §3 surface inventory (Bench 5 / Projects 7 / Settings 1 / chrome) → Tasks 3–10 cover every panel + chrome; the gate auto-enumerates panels so none is missed. ✓
- Spec §4 audit ledger (P0/P1/P2, per surface) → Task 2. ✓
- Spec §5 fix workflow (worst-first, Bench helper rewrites, visual-only) → Tasks 3–10, scope guards in Tasks 7/8/9. ✓
- Spec §6 testing (`tests/test_render_gate.py` regression guard; existing suites green; visual signoff) → Task 1 Step 1 + every fix task's Step 3/4 + Task 11. ✓
- Spec §2/§0 immutable design-rules, pristine bar, visual-only boundary → Global Constraints + per-task scope guards. ✓
- Spec §7 risks (empty-state panels, scope creep, helper regression, git hygiene) → gate builds real state, scope guards, smoke test, Global Constraints. ✓

**Placeholder scan:** Task 1 and its test are complete runnable code. Fix tasks (3–11) intentionally source their exact edits from the Task 2 ledger (stated in Global Constraints as the audit-driven design) and name the concrete §4 recipe + objective re-render verification for each surface — this is discovery-driven, not a vague "add error handling" placeholder.

**Type consistency:** `render_all(out_dir, themes, only) -> list[Path]` and `main(argv) -> int` are used identically in the smoke test and every fix task's render command. Shell/workspace internals (`win._select`, `win._stack`, `win._page_specs`, `win.apply_theme`, `ws._panels`, `ws._select`) match the signatures read from `shell.py` and `widgets.py`. Panel/helper line numbers match the current `bench.py` / `projects.py` / `settings.py`. ✓
