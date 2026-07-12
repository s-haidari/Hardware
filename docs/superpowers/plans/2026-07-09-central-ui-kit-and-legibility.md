# Central UI Kit + Legibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a central composition layer (`tools/ui/kit.py`) that every feature calls, enforce its use with a no-drift lint, tune the category palette for visibility (≥3:1), rebuild the legend + MCU-pinout layout, and migrate all tabs onto the kit.

**Architecture:** `kit.py` sits *above* `widgets.py` (the low-level primitives). It composes primitives + tokens/scale/motion/icons into page-level builders (`page`, `section`, `detail`, `action`, `stat_strip`, `table`, `legend`, `state`, `async_region`, `custom`). Features import `kit` and stop styling directly; a lint test enforces this. Bespoke visuals (pin map, connection diagram, previews, routing canvases) enter via `kit.custom(...)`.

**Tech Stack:** Python 3, PyQt5 (Fusion + QSS), pytest (offscreen Qt), the existing `render_gate.py`.

## Global Constraints

- **Refined-Neutral stays** — neutral chrome, no brand/accent color; color = meaning only. (spec §0)
- **Legibility floor:** every category hue ≥ **3:1** contrast against the surface it sits on, both themes (WCAG graphical-object threshold). (spec §3.1)
- **Regular + Semibold only; no letterspacing; Title Case UI text; real casing for refdes/nets/pins.**
- **kit is additive** — do NOT change `widgets.py` public signatures; `kit` composes them.
- **Escape hatch:** bespoke visuals stay hand-written Qt, entered via `kit.custom(...)`; they are allowlisted in the no-drift lint.
- **Concurrency:** other sessions own `LibraryManager.py`, `mouser_search.py`, `settings.py` (sourcing) and `routing.py`/`routing_panels/**`/`routing_engine/**` (routing). Do NOT edit those until confirmed free. Migrate Settings/Routing last. Scoped `git add <path>` only; never `-A`; never stage the 5 `libs/My3DModels/*.STEP` churn files. `git pull --rebase --autostash` before push. Plain commit messages, no `Co-Authored-By`/`Claude-Session` trailers.
- **CI gate:** `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q` stays green (currently 902 pass / 3 skip). Watch Windows path separators (`.as_posix()`, never `str(Path)`).
- **Render-gate loop:** `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --settle 0.9` then Read the PNGs and self-audit vs `docs/design/design-rules.md` §5.

## File Structure

- `tools/ui/kit.py` — **create.** The composition layer. One responsibility: page-level builders.
- `tools/ui/theme.py` — **modify.** Retune `CATEGORY_DARK`/`CATEGORY_LIGHT`; add `category_contrast()` helper.
- `tests/test_category_contrast.py` — **create.** ≥3:1 category × surface, both themes.
- `tests/test_ui_kit.py` — **create.** Each kit builder renders + enforces its invariant.
- `tests/test_ui_no_drift.py` — **create.** Feature files don't style directly (allowlist for bespoke modules).
- `tools/ui/features/bench.py` — **modify.** Pinout spacing (BENCH-04) + migrate to kit.
- `tools/ui/features/{library,projects,git}.py` — **modify.** Migrate to kit.
- `tools/ui/features/{settings,routing}.py` — **modify LAST**, only when uncontested.

---

### Task 1: Category palette legibility (≥3:1) + contrast test

**Files:**
- Modify: `tools/ui/theme.py` (`CATEGORY_DARK`/`CATEGORY_LIGHT` ~L50-59; add `category_contrast()` after `category()`).
- Test: `tests/test_category_contrast.py`

**Interfaces:**
- Produces: retuned `CATEGORY_DARK`/`CATEGORY_LIGHT` dicts (same keys); `theme.category_contrast(name: str, surface_key: str) -> float`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_category_contrast.py`:

```python
"""Every category hue must clear 3:1 against the surfaces it sits on (legibility)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from PyQt5.QtWidgets import QApplication  # noqa: E402
import ui.theme as T  # noqa: E402
_APP = QApplication.instance() or QApplication([])

CATS = ("power", "ground", "core", "service", "lane", "must", "osc", "fixed", "breakout")

def test_every_category_clears_3to1_on_surfaces_both_themes():
    for dark in (True, False):
        T.set_theme(dark)
        for cat in CATS:
            for surf in ("canvas", "raised", "inset"):
                c = T.category_contrast(cat, surf)
                assert c >= 3.0, f"{'dark' if dark else 'light'} {cat} on {surf}: {c:.2f}"
    T.set_theme(True)
```

- [ ] **Step 2: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_category_contrast.py -q`
Expected: FAIL — `category_contrast` missing; and the muted ground/lane greys fail 3:1.

- [ ] **Step 3: Add the contrast helper**

In `tools/ui/theme.py`, after `def category(...)`, add (reuse the WCAG math pattern from `tests/test_ui_foundation.py`):

```python
def _rel_lum(hexstr: str) -> float:
    c = QColor(hexstr)
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.red()) + 0.7152 * lin(c.green()) + 0.0722 * lin(c.blue())


def category_contrast(name: str, surface_key: str) -> float:
    """WCAG contrast ratio of a category hue against an (opaque) surface token, in the
    active theme. Category colours are opaque hex; surfaces resolve via t()."""
    fg = _rel_lum(category(name)) + 0.05
    bg = _rel_lum(t(surface_key)) + 0.05
    return max(fg, bg) / min(fg, bg)
```

- [ ] **Step 4: Retune the palettes until the test passes**

Adjust `CATEGORY_DARK`/`CATEGORY_LIGHT` values so all clear 3:1 on canvas/raised/inset. Keep the *meaning* (power warm, ground neutral-cool, core violet, service/fivev green, lane cool-grey, must red, osc orange, fixed grey, breakout cyan) but push lightness so each is clearly visible; the abundant classes (ground, lane, fixed) stay the quietest that still clears 3:1. Starting candidates (verify with the test, nudge as needed):

```python
CATEGORY_DARK = {
    "power": "#e6a94d", "ground": "#9aa6b6", "core": "#c0a0f0", "service": "#7fce9c",
    "lane": "#8a94a4", "must": "#f0847a", "osc": "#f0973f", "fixed": "#a7adb8",
    "breakout": "#5cc6da", "fivev": "#7fce9c",
}
CATEGORY_LIGHT = {
    "power": "#9a5a08", "ground": "#4d5a6e", "core": "#6a3fb0", "service": "#1f7a48",
    "lane": "#5f6775", "must": "#b63428", "osc": "#a85610", "fixed": "#565d69",
    "breakout": "#136f83", "fivev": "#1f7a48",
}
```

- [ ] **Step 5: Run to verify pass + no regression**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_category_contrast.py tests/test_ui_foundation.py tests/test_theme_tokens.py -q`
Expected: PASS.

- [ ] **Step 6: Render-gate the Bench pinout/legend to eyeball the new palette**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --surface bench --settle 0.9` then Read `build/render/bench.overview.{dark,light}.png` — every legend swatch clearly visible.

- [ ] **Step 7: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/theme.py tests/test_category_contrast.py
git commit -m "Feature: retune category palette for legibility (every hue >=3:1 on surfaces)"
```

---

### Task 2: `kit.py` core — page / section / detail / action bar

**Files:**
- Create: `tools/ui/kit.py`
- Test: `tests/test_ui_kit.py`

**Interfaces:**
- Consumes: `widgets` (as W), `theme` (as T).
- Produces:
  - `kit.Action` dataclass: `text:str, on:Callable, kind:str="default", tip:str=""`.
  - `kit.action(text, on, *, kind="default", tip="") -> Action`.
  - `kit.section(title:str, *body:QWidget, hairline=True) -> QWidget`.
  - `kit.detail(title:str, pairs:Sequence[Tuple[str,object]], *, key_width=136) -> QWidget`.
  - `kit.page(title:str, *, header:QWidget=None, actions:Sequence[Action]=(), body:Sequence[QWidget]=()) -> QWidget` — raises `ValueError` if >1 primary action.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_kit.py`:

```python
"""kit composition builders: render + invariants (one primary per page, etc.)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget, QLabel  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.kit as kit  # noqa: E402

_APP = QApplication.instance() or QApplication([])
def _destroy(w): sip.delete(w)

def test_page_builds_with_title_and_body():
    p = kit.page("Demo", body=[kit.detail("Part", [("Part Number", "R1"), ("Value", "10k")])])
    assert isinstance(p, QWidget)
    texts = [l.text() for l in p.findChildren(QLabel)]
    assert "Demo" in texts and "Part Number" in texts and "10k" in texts
    _destroy(p)

def test_page_allows_exactly_one_primary():
    a = kit.action("Save", lambda: None, kind="primary")
    b = kit.action("Cancel", lambda: None, kind="ghost")
    p = kit.page("Demo", actions=[a, b])       # one primary — ok
    assert isinstance(p, QWidget)
    _destroy(p)

def test_page_rejects_two_primaries():
    a = kit.action("Save", lambda: None, kind="primary")
    b = kit.action("Build", lambda: None, kind="primary")
    with pytest.raises(ValueError):
        kit.page("Demo", actions=[a, b])

def test_section_has_title_and_child():
    s = kit.section("Sourcing", QLabel("body"))
    texts = [l.text() for l in s.findChildren(QLabel)]
    assert "Sourcing" in texts and "body" in texts
    _destroy(s)
```

- [ ] **Step 2: Run to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_kit.py -q`
Expected: FAIL — `No module named 'ui.kit'`.

- [ ] **Step 3: Create `tools/ui/kit.py` core**

```python
"""ui.kit — the composition layer features call. Sits ABOVE widgets.py: composes the
primitive kit + tokens/scale/motion/icons into page-level builders that own all
styling, so a feature declares content and never styles directly. Bespoke visuals
enter via kit.custom(). See docs/superpowers/specs/2026-07-09-central-ui-kit-and-legibility.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout

from . import widgets as W
from . import theme as T


@dataclass
class Action:
    text: str
    on: Callable
    kind: str = "default"        # default | primary | ghost
    tip: str = ""


def action(text: str, on: Callable, *, kind: str = "default", tip: str = "") -> Action:
    return Action(text, on, kind, tip)


def _action_bar(actions: Sequence[Action]) -> QWidget:
    primaries = [a for a in actions if a.kind == "primary"]
    if len(primaries) > 1:
        raise ValueError(f"a page has at most one primary action, got {len(primaries)}")
    bar = QWidget()
    h = QHBoxLayout(bar)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    h.addStretch(1)
    for a in actions:
        h.addWidget(W.btn(a.text, kind=a.kind, tip=a.tip, on_click=a.on))
    return bar


def section(title: str, *body: QWidget, hairline: bool = True) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(12)
    v.addWidget(W.section_header(title) if hairline else W.subhead(title))
    for b in body:
        if b is not None:
            v.addWidget(b)
    return w


def detail(title: str, pairs: Sequence[Tuple[str, object]], *, key_width: int = 136) -> QWidget:
    rows = [(k, v if isinstance(v, QWidget) else W.body(str(v))) for k, v in pairs]
    return section(title, W.dl(rows, key_width=key_width))


def page(title: str, *, header: Optional[QWidget] = None,
         actions: Sequence[Action] = (), body: Sequence[QWidget] = ()) -> QWidget:
    """The one page scaffold: title + optional header/action bar + scrolled body."""
    head = header
    if actions:
        bar = _action_bar(actions)     # validates one-primary
        head = bar if header is None else W.hstack(header, bar, stretch_last=False)
    inner = QWidget()
    v = QVBoxLayout(inner)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(14)
    for b in body:
        if b is not None:
            v.addWidget(b)
    v.addStretch(1)
    return W.Workspace(ctx=None, title=title,
                       panels=[(title, lambda _ctx: W.scroll_body(inner))],
                       header=head)
```

- [ ] **Step 4: Run to verify pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_kit.py -q`
Expected: PASS (4 tests). (Note: single-panel `Workspace` shows no sub-tab bar, giving a clean page.)

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/kit.py tests/test_ui_kit.py
git commit -m "Feature: ui.kit core — page/section/detail/action-bar with one-primary enforcement"
```

---

### Task 3: `kit.state` + `kit.async_region`

**Files:** Modify `tools/ui/kit.py`; Test `tests/test_ui_kit.py`.

**Interfaces:**
- Produces: `kit.state(kind:str, line:str, *, glyph="", sub="", action:Action=None) -> QWidget` (`kind ∈ {empty, loading, error}`); `kit.async_region(compute:Callable[[],object], render:Callable[[object],QWidget], *, rows=6, cols=4, ctx=None) -> QWidget`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_ui_kit.py`:

```python
def test_state_empty_and_loading_and_error():
    e = kit.state("empty", "Nothing Here", glyph="search")
    lo = kit.state("loading", "Loading")
    er = kit.state("error", "It Broke", glyph="alert")
    for w in (e, lo, er):
        assert w is not None
    # loading shows skeleton blocks
    assert lo.findChildren(W.Skeleton)
    _destroy(e); _destroy(lo); _destroy(er)

def test_async_region_renders_synchronously_offscreen():
    # offscreen run_populate is synchronous, so the rendered result is present immediately
    r = kit.async_region(lambda: [1, 2, 3], lambda data: W.body(f"{len(data)} rows"))
    from PyQt5.QtWidgets import QLabel
    assert any("3 rows" in l.text() for l in r.findChildren(QLabel))
    _destroy(r)
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_ui_kit.py -q -k "state or async"` → FAIL (`kit.state` missing).

- [ ] **Step 3: Implement** — append to `tools/ui/kit.py`:

```python
from . import icons as _icons


def state(kind: str, line: str, *, glyph: str = "", sub: str = "",
          action: Optional[Action] = None) -> QWidget:
    """One state pattern: empty / loading / error. loading => skeleton rows."""
    if kind == "loading":
        return W.skeleton_rows(rows=6, cols=4)
    act = W.btn(action.text, kind=action.kind, tip=action.tip, on_click=action.on) if action else None
    g = _icons.GLYPHS.get(glyph, "") if glyph else ""
    return W.empty_state(line, glyph=g, sub=sub, action=act)


def async_region(compute: Callable[[], object], render: Callable[[object], QWidget], *,
                 rows: int = 6, cols: int = 4, ctx=None) -> QWidget:
    """Run compute() off the GUI thread (via ui.util.run_populate); show a skeleton
    until it lands, then swap in render(result). Offscreen/headless is synchronous."""
    from .util import run_populate
    host = QWidget()
    v = QVBoxLayout(host); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
    placeholder = W.skeleton_rows(rows=rows, cols=cols)
    v.addWidget(placeholder)

    def populate(result):
        while v.count():
            it = v.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.setParent(None); wdg.deleteLater()
        v.addWidget(render(result))
    run_populate(ctx, compute, populate)
    return host
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_ui_kit.py -q` → PASS. (If `run_populate`'s signature differs, read `tools/ui/util.py` and match it — it is used across features as `run_populate(ctx, compute, populate, busy=...)`.)

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/kit.py tests/test_ui_kit.py
git commit -m "Feature: kit.state + kit.async_region (one state pattern, auto skeleton on async)"
```

---

### Task 4: `kit.stat_strip` + `kit.table` + `kit.legend` + `kit.custom`

**Files:** Modify `tools/ui/kit.py`; Test `tests/test_ui_kit.py`.

**Interfaces:**
- Produces:
  - `kit.stat_strip(stats:Sequence[Tuple[str,str]]) -> QWidget` — `(number, label)` pairs, number in `scale_font("stat")`.
  - `kit.table(columns, rows, **opts) -> QWidget` — thin wrapper over `W.data_table` (Title-case headers guaranteed).
  - `kit.legend(groups:Sequence[Tuple[str, Sequence[Tuple[str,str]]]]) -> QWidget` — the reworked legend: `[(group_title, [(category_or_hex, label), ...]), ...]`, aligned swatch+Title-case-label grid, larger consistent swatch.
  - `kit.custom(widget_or_builder) -> QWidget` — escape hatch (accepts a QWidget or a zero-arg builder).

- [ ] **Step 1: Write the failing tests** — append:

```python
def test_stat_strip_uses_stat_scale():
    s = kit.stat_strip([("64", "Positions"), ("11", "Channels")])
    from PyQt5.QtWidgets import QLabel
    texts = [l.text() for l in s.findChildren(QLabel)]
    assert "64" in texts and "Positions" in texts
    _destroy(s)

def test_legend_shows_every_label():
    lg = kit.legend([("Net Colour", [("power", "Power"), ("ground", "Ground")])])
    from PyQt5.QtWidgets import QLabel
    texts = [l.text() for l in lg.findChildren(QLabel)]
    assert "Power" in texts and "Ground" in texts and "Net Colour" in texts
    _destroy(lg)

def test_custom_passes_widget_through():
    from PyQt5.QtWidgets import QLabel
    inner = QLabel("bespoke")
    w = kit.custom(inner)
    assert inner in w.findChildren(QLabel) or w is inner
    _destroy(w)
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_ui_kit.py -q -k "stat_strip or legend or custom"` → FAIL.

- [ ] **Step 3: Implement** — append to `tools/ui/kit.py`:

```python
from PyQt5.QtWidgets import QLabel, QGridLayout, QSizePolicy


def stat_strip(stats: Sequence[Tuple[str, str]]) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(28)
    for number, label in stats:
        col = QWidget(); cv = QVBoxLayout(col); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(1)
        num = QLabel(number); num.setFont(T.scale_font("stat"))
        lab = W.eyebrow(label)
        W.register_restyle(lambda num=num: num.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), num)
        cv.addWidget(num); cv.addWidget(lab)
        h.addWidget(col)
    h.addStretch(1)
    return w


def table(columns, rows, **opts) -> QWidget:
    return W.data_table(list(columns), rows, **opts)   # data_table already Title-cases headers


def legend(groups: Sequence[Tuple[str, Sequence[Tuple[str, str]]]]) -> QWidget:
    """Aligned swatch+label legend. Each item's first element is a category name
    (resolved via T.category) or a literal hex; the swatch is a 10px rounded dot."""
    w = QWidget()
    grid = QGridLayout(w); grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(16); grid.setVerticalSpacing(8)
    r = 0
    for gtitle, items in groups:
        head = W.eyebrow(gtitle)
        grid.addWidget(head, r, 0, 1, 2, Qt.AlignLeft); r += 1
        for key, label in items:
            dot = QLabel(); dot.setFixedSize(10, 10)
            lab = QLabel(label); lab.setFont(T.ui_font(9))
            def style(dot=dot, key=key, lab=lab):
                col = T.category(key) if not key.startswith("#") else key
                dot.setStyleSheet(f"background:{col};border-radius:5px;")
                lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
            W.register_restyle(style, dot)
            cell = W.hstack(dot, lab, spacing=8)
            cell.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            grid.addWidget(cell, r, 0, 1, 2, Qt.AlignLeft); r += 1
    return w


def custom(widget_or_builder) -> QWidget:
    if callable(widget_or_builder) and not isinstance(widget_or_builder, QWidget):
        return widget_or_builder()
    return widget_or_builder
```

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_ui_kit.py -q` → PASS (all).

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/kit.py tests/test_ui_kit.py
git commit -m "Feature: kit.stat_strip + kit.table + kit.legend (reworked) + kit.custom escape hatch"
```

---

### Task 5: No-drift lint

**Files:** Test `tests/test_ui_no_drift.py`.

**Interfaces:** consumes nothing; scans `tools/ui/features/*.py` source text.

- [ ] **Step 1: Write the test (it defines the enforcement)**

Create `tests/test_ui_no_drift.py`:

```python
"""Feature chrome files must route styling through kit/widgets, not style directly.
Bespoke visual modules are allowlisted (they legitimately paint custom Qt)."""
import re
from pathlib import Path

FEAT = Path(__file__).resolve().parents[1] / "tools" / "ui" / "features"

# bespoke visual modules exempt from the chrome rules (custom painting), AND
# not-yet-migrated files (remove each as it migrates onto kit).
ALLOWLIST = {
    "bench_pinmap.py",          # pin map painting (if split out)
    # not-yet-migrated (remove as migrated):
    "settings.py", "routing.py",
}

BANNED = [
    (re.compile(r"\.setLetterSpacing\("), "setLetterSpacing (retired)"),
    (re.compile(r"\.upper\(\)"), ".upper() on a label (Title Case only)"),
    (re.compile(r"(ui_font|mono_font)\(\s*\d"), "hardcoded font size (use scale_font)"),
    (re.compile(r"\.setStyleSheet\("), "direct setStyleSheet (use kit/widgets)"),
]

def _feature_files():
    for p in sorted(FEAT.glob("*.py")):
        if p.name == "__init__.py" or p.name in ALLOWLIST:
            continue
        # skip routing_panels subpackage dir handled elsewhere
        yield p

def test_migrated_features_do_not_style_directly():
    problems = []
    for p in _feature_files():
        src = p.read_text()
        for rx, why in BANNED:
            for m in rx.finditer(src):
                line = src[:m.start()].count("\n") + 1
                problems.append(f"{p.name}:{line} — {why}")
    assert not problems, "drift found:\n" + "\n".join(problems)
```

- [ ] **Step 2: Run** — `pytest tests/test_ui_no_drift.py -q`. It will likely FAIL, listing the current drift in bench/library/projects/git (they still style directly). That is expected — the migration tasks (6-11) drive it to green, removing each file from `ALLOWLIST` as it migrates. **For this task**, add the currently-unmigrated files (`bench.py`, `library.py`, `projects.py`, `git.py`) to `ALLOWLIST` so the test is green now, then remove each in its migration task.

- [ ] **Step 3: Make it green for the current state** — add `bench.py`, `library.py`, `projects.py`, `git.py` to `ALLOWLIST`. Run → PASS.

- [ ] **Step 4: Commit**

```bash
git pull --rebase --autostash origin main
git add tests/test_ui_no_drift.py
git commit -m "Feature: no-drift lint — feature chrome must use kit (allowlist unmigrated + bespoke)"
```

---

### Task 6: MCU pinout legibility (BENCH-04)

**Files:** Modify `tools/ui/features/bench.py` (the `PinMap` widget: geometry/spacing + selected-pin ring).

**Interfaces:** internal to `PinMap`; no external signature change.

- [ ] **Step 1** — Read `PinMap` in `bench.py` (paint + `pin_map_geometry` usage). Identify where pin cells and pin numbers are laid out.
- [ ] **Step 2 (test/verification is visual)** — Render before: `render_gate.py --surface bench`; note the overlapping numbers on the largest package.
- [ ] **Step 3 — Give pins room + a crisp selected ring.** Increase per-pin cell spacing / the map size so adjacent pin numbers don't collide (scale the label font down a touch and/or increase cell pitch); draw the selected pin's ring via `from ..motion import paint_focus_ring` using `T.qcolor("accent")` on the device-pixel grid. Pull pin fill colors from the retuned `T.category(...)`.
- [ ] **Step 4 — Verify** — `render_gate.py --surface bench` both themes; Read the PNGs — pin numbers legible, no overlap, selected pin unmistakable. Run `pytest tests/test_audit_pins_tab.py -q` → PASS.
- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/features/bench.py
git commit -m "Feature: MCU pinout legibility (BENCH-04) — pins spaced so numbers don't overlap, crisp selected ring"
```

---

### Tasks 7–10: Migrate the owned tabs to kit — repeatable recipe

Migrate one tab per task, in order: **7 Bench, 8 Library, 9 Projects, 10 Git.** Each follows the SAME recipe (worked example: Bench). Each ends green + render-gated + committed, and **removes that file from `ALLOWLIST` in `test_ui_no_drift.py`**.

**The recipe (apply per tab):**

- [ ] **Step 1** — Read the tab's `build()` + panel builders. List every place it: builds a page/section header, an action row, a definition list, a stat strip, a table, an empty/loading/error state, a legend, or a bespoke visual.
- [ ] **Step 2** — Rewrite `build()`/panels to construct those via `kit.*`:
  - page shell → `kit.page(...)` or `kit.tabbed_page(...)` (add `tabbed_page` to kit if a tab has sub-panels — see note below);
  - section headers → `kit.section` / `kit.detail`;
  - action rows → `kit.action(...)` list on the page (exactly one `primary`);
  - stat strips → `kit.stat_strip`; tables → `kit.table`; legend → `kit.legend`;
  - empty/loading/error strings → `kit.state(...)`; async panels → `kit.async_region(...)`;
  - pin map / connection diagram / previews / routing canvases → `kit.custom(...)`.
  Delete now-dead local styling helpers. Do NOT change the tab's behavior/signals/state wiring.
- [ ] **Step 3** — Remove the tab's filename from `ALLOWLIST` in `tests/test_ui_no_drift.py`.
- [ ] **Step 4 — Verify:** run the tab's tests (Bench: `test_audit_pins_tab.py test_bench_profiles_depill.py`; Library: `test_library.py test_lib*.py test_sp2_library.py`; Projects: `test_proj*.py test_netclass_profiles.py test_wave1_pcb*.py`; Git: `test_backend_git.py test_wave1_git_unify.py test_sp5_git.py`) + `test_ui_no_drift.py` + `test_ui_kit.py`. All PASS. Then `render_gate.py --surface <tab>` both themes and Read the PNGs (design-rules §5 self-audit).
- [ ] **Step 5 — Commit** (scoped to the tab file + the no-drift test):

```bash
git pull --rebase --autostash origin main
git add tools/ui/features/<tab>.py tests/test_ui_no_drift.py
git commit -m "refactor(ui): migrate <Tab> onto kit (no-drift green; render-gated)"
```

**Note — `kit.tabbed_page`:** if a tab uses sub-panels (Bench, Library, Projects have `Workspace` sub-tabs), add this to `kit.py` in the Bench task (Task 7) and cover it with a `test_ui_kit.py` test:

```python
def tabbed_page(title: str, panels, *, header=None) -> QWidget:
    """A multi-panel page: reuses widgets.Workspace (sliding subtab underline). `panels`
    is a list of (name, builder(ctx)->QWidget)."""
    return W.Workspace(ctx=None, title=title, panels=list(panels), header=header)
```
```python
def test_tabbed_page_builds_panels():
    tp = kit.tabbed_page("Demo", [("A", lambda c: W.body("a")), ("B", lambda c: W.body("b"))])
    assert tp is not None
    _destroy(tp)
```

---

### Task 11: Migrate Settings + Routing (ONLY when uncontested)

**Files:** `tools/ui/features/settings.py`, `tools/ui/features/routing.py` (+ `routing_panels/*` styling only).

- [ ] **Step 1 — Gate:** confirm the sourcing session has released `settings.py` and the routing session has released `routing.py`/`routing_panels/` (`git status --short` shows them clean, or coordinate). If still contested, STOP and report — do not edit.
- [ ] **Step 2** — Apply the Task 7–10 recipe to each; Settings especially becomes `kit.detail`/`kit.section` definition lists (retire the card-per-row). Remove `settings.py`/`routing.py` from `ALLOWLIST`.
- [ ] **Step 3 — Verify** — their tests (`test_wave0_settings_theme.py`, `test_routing_shell.py`, `test_routing_*`) + `test_ui_no_drift.py` PASS; render-gate both surfaces both themes.
- [ ] **Step 4 — Commit** scoped per file.

---

### Task 12: Acceptance

- [ ] **Step 1** — `test_ui_no_drift.py` green with `ALLOWLIST` reduced to only genuinely-bespoke modules (no unmigrated feature files remain).
- [ ] **Step 2** — Full render gate both themes; §5 self-audit every surface; legend + pinout legible; palette visible.
- [ ] **Step 3** — Full suite: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q` green.
- [ ] **Step 4** — Runtime smoke drive (adapt `scratchpad/smoke_drive.py`): nav all pages, theme toggle ×4, package + project switch, subtab switch — zero exceptions.
- [ ] **Step 5** — Coordinated `git pull --rebase --autostash origin main && git push origin main`.

---

## Self-Review

**1. Spec coverage:** kit module + all builders → Tasks 2-4 (+`tabbed_page` note); enforcement lint → Task 5; palette legibility ≥3:1 → Task 1; legend rework → Task 4 (`kit.legend`); pinout BENCH-04 → Task 6; page-scaffold uniformity → Tasks 7-11 (migration); big-bang sequenced migration → Tasks 7-11; testing/acceptance → Task 12. `kit.custom` escape hatch → Task 4. All spec sections mapped.

**2. Placeholder scan:** No TBD/"similar to"/vague-error steps. The migration recipe (Tasks 7-11) is an explicit repeatable procedure, not a placeholder — each tab is the same recipe on different content, with concrete per-tab test lists and the exact kit calls to use. Palette values in Task 1 are concrete starting candidates with a test that forces correctness.

**3. Type consistency:** `Action`/`action` (Task 2) used by `page`/`state` (Tasks 2-3). `kit.page`/`section`/`detail`/`stat_strip`/`table`/`legend`/`state`/`async_region`/`custom`/`tabbed_page` names consistent across tasks and the no-drift/migration references. `category_contrast(name, surface_key)` (Task 1) matches its test. `run_populate(ctx, compute, populate)` — verify exact signature against `tools/ui/util.py` at implementation (flagged in Task 3 Step 4).

**Risk note:** `kit.page` builds on the single-panel `Workspace` (no sub-tab bar) — confirm in Task 2 that a lone-panel Workspace renders without a stray underline (the Phase-A `_underline=None` guard covers this). Migration must preserve each tab's async/state wiring — behavior-preserving refactor, verified by the existing per-tab tests.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-central-ui-kit-and-legibility.md`. This session is handing off to a fresh session (owner requested). Recommended execution: **subagent-driven** for the migration tasks (one subagent per tab, disjoint files), inline for the kit/theme foundation (Tasks 1-6).
