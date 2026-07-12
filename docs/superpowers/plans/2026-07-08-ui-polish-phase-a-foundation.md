# UI Polish — Phase A (Refined-Neutral Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the shared design system (`theme.py`, `widgets.py`, new `motion.py`, `shell.py`) into one intentional Refined-Neutral foundation — monotonic neutral elevation ladder, 8/6 radii, single hairline budget, fixed type scale, de-letterspaced headers, borderless elevation, and a reduced-motion-aware animation layer — so every tab lifts at once, before any per-tab pass.

**Architecture:** `theme.py` stays the single token source of truth; its existing key names (`base`, `surface`, `card`, `inset`, `stroke`, `divider`, …) are **re-valued** to a monotonic neutral ladder and **augmented** with new semantic aliases (`canvas`, `raised`, `hairline`, `hairline_strong`) + radius constants + a fixed type-scale helper. Re-valuing (not renaming) means every existing call site — including the parallel session's `features/routing.py`, which this plan must not edit — inherits the lifted values for free. `widgets.py` primitives (`eyebrow`, `Card`, `Verdict`) drop decoration. A new `ui/motion.py` holds the only animation code behind a single reduced-motion gate. `shell.py`/`Workspace` consume motion. The render gate (16 surfaces × 2 themes) is the acceptance surface.

**Tech Stack:** Python 3, PyQt5 (Fusion + full QSS), pytest (offscreen Qt), the existing `render_gate.py` harness.

## Global Constraints

- **No brand/azure accent.** Refined Neutral only — elevate through space, hierarchy, type, borderless elevation, subtle motion. The azure "Quiet Instrument" palette is retired. (spec §Direction)
- **Discipline is all-or-nothing** — one stray QFrame border or stadium pill reintroduces the generated texture (design-rules §1, §4).
- **Color is meaning only** — the meaning-only category palette (`CATEGORY_DARK`/`CATEGORY_LIGHT`) and `txt1/txt2/txt3` tiers are already correct; **preserve them unchanged**. Never tint a surface with a category hue.
- **Regular + Semibold only.** Never Bold/Light/Medium. No letterspacing anywhere.
- **Title Case for all UI text**; real casing for refdes/nets/pins; sentence case only for actual sentences (design-rules §2).
- **WCAG AA verified** on every text tier × surface: `txt1`/`txt2` ≥ 4.5:1 (body/data), `txt3` ≥ 3.0:1 (micro-label / large tier). Alpha tokens are composited over the opaque surface before measuring.
- **Qt5 QSS has NO CSS transitions** — all motion is `QPropertyAnimation`/`QVariantAnimation` in code, gated on reduced-motion (instant no-op when set).
- **Draw hairlines/connectors on the device-pixel grid** (integer / 0.5px cosmetic 1px pen) so they read crisp at fractional DPI.
- **Do NOT edit** `tools/ui/features/routing.py`, `tools/ui/features/routing_panels/**`, or `tools/routing_engine/**` (parallel session owns them). Only touch the shared `theme.py`/`widgets.py`/`shell.py` they consume.
- **Git:** scoped `git add <path>` only (never `-A`); the 5 `libs/My3DModels/*.STEP` files are always-dirty CRLF churn — never stage or revert them. `git pull --rebase --autostash` before every push. Plain commit messages, no `Co-Authored-By`/`Claude-Session` trailers.
- **CI gate:** `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q` (scope: `tests/`, NOT `tools/routing_engine/tests`) must stay green (~829 pass / 3 skip). Watch for Windows-only path-separator failures (`.as_posix()`, never `str(Path)`).

---

## File Structure

- `tools/ui/theme.py` — **modify.** Re-value `DARK`/`LIGHT` to the monotonic ladder; add `canvas`/`raised`/`hairline`/`hairline_strong` keys; add `RADIUS_CONTAINER`/`RADIUS_CONTROL` constants + `radius()` helper; add `TYPE_SCALE` + `scale_font()`; enable tabular alignment on mono; refresh `qss()` to consume the new radii + single hairline + borderless surfaces.
- `tools/ui/widgets.py` — **modify.** `eyebrow()` → Title-case Semibold `txt3`, zero tracking, no `.upper()`. `Card`/`Verdict`/`Verdict._chip` drop decorative borders → borderless elevation. (Button hierarchy primitives `btn(kind=...)` already exist; call-site hierarchy is Phase C.)
- `tools/ui/motion.py` — **create.** The only animation code. Reduced-motion gate (`reduced_motion()`/`set_reduced_motion()`), `animate_opacity()`, `SlidingUnderline` widget, `cross_fade()`, `paint_focus_ring()`.
- `tools/ui/shell.py` — **modify.** Nav hover/selection ease, theme cross-fade on toggle, keyboard focus rings; `Workspace` subtab gets the sliding underline (in `widgets.py`).
- `docs/design/design-rules.md` — **modify.** Reconcile §3/§4 to the shipped Refined-Neutral reality; §1/§2/§5 verbatim; add a one-line changelog.
- `tests/test_ui_foundation.py` — **create.** Token monotonicity + inset-distinct + radius constants + WCAG-AA contrast + type-scale locked sizes/weights + `eyebrow()` zero-letterspacing + borderless-surface assertions.
- `tests/test_ui_motion.py` — **create.** Reduced-motion no-ops for `animate_opacity` and `SlidingUnderline`; gate get/set.

Each task ends with an independently testable, independently committable deliverable.

---

### Task 1: Monotonic neutral elevation ladder + radius + hairline tokens

**Files:**
- Modify: `tools/ui/theme.py:21-44` (the `DARK`/`LIGHT` dicts) and add module constants after them.
- Test: `tests/test_ui_foundation.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `theme.DARK` / `theme.LIGHT` dicts gain keys `canvas`, `raised`, `hairline`, `hairline_strong`; existing keys `base`, `surface`, `card`, `card_hover`, `inset`, `stroke`, `divider` are re-valued (same names, new values).
  - `theme.RADIUS_CONTAINER: int = 8`, `theme.RADIUS_CONTROL: int = 6`.
  - `theme.radius(role: str) -> int` — `role in {"container","control"}`, container default.
  - `theme.ELEVATION: tuple = ("nav", "canvas", "raised")` — the ordered base ladder (used by tests).

- [ ] **Step 1: Write the failing test file**

Create `tests/test_ui_foundation.py`:

```python
"""Phase-A Refined-Neutral foundation: monotonic elevation ladder, radius tokens,
WCAG-AA contrast, fixed type scale, de-letterspaced eyebrow, borderless surfaces."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtGui import QColor, QFont  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.theme as T  # noqa: E402
import ui.widgets as W  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


# ── colour helpers (composite alpha over the opaque surface, then WCAG) ────────
def _grey(token: str) -> float:
    """Average 0-255 channel value of an opaque token (ladder colours are neutral)."""
    c = W._qcolor(token)
    return (c.red() + c.green() + c.blue()) / 3.0


def _srgb_lin(v: float) -> float:
    v /= 255.0
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def _lum(r: float, g: float, b: float) -> float:
    return 0.2126 * _srgb_lin(r) + 0.7152 * _srgb_lin(g) + 0.0722 * _srgb_lin(b)


def _composite(fg: QColor, bg: QColor):
    a = fg.alpha() / 255.0
    return tuple(round(f * a + b * (1 - a))
                 for f, b in ((fg.red(), bg.red()), (fg.green(), bg.green()), (fg.blue(), bg.blue())))


def _contrast(fg_token: str, bg_token: str) -> float:
    fg, bg = W._qcolor(fg_token), W._qcolor(bg_token)
    r, g, b = _composite(fg, bg)
    l1 = _lum(r, g, b) + 0.05
    l2 = _lum(bg.red(), bg.green(), bg.blue()) + 0.05
    return max(l1, l2) / min(l1, l2)


# ── ladder monotonicity ───────────────────────────────────────────────────────
def test_base_ladder_strictly_increases_both_themes():
    for dark in (True, False):
        T.set_theme(dark)
        vals = [_grey(T.t(k)) for k in T.ELEVATION]   # nav, canvas, raised
        assert vals == sorted(vals) and len(set(vals)) == len(vals), \
            f"ladder not strictly increasing ({'dark' if dark else 'light'}): {vals}"
    T.set_theme(True)


def test_inset_is_a_distinct_lift_from_raised():
    for dark in (True, False):
        T.set_theme(dark)
        assert abs(_grey(T.t("inset")) - _grey(T.t("raised"))) >= 4, \
            "inset must read as a distinct grouped/active step from raised"
    T.set_theme(True)


def test_ladder_is_zero_hue_neutral():
    # zero hue shift: R, G, B within a tight band (keeps the WinUI-grey character)
    for dark in (True, False):
        T.set_theme(dark)
        for k in ("nav", "canvas", "raised", "inset"):
            c = W._qcolor(T.t(k))
            assert max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) <= 8, \
                f"{k} is not neutral in {'dark' if dark else 'light'}"
    T.set_theme(True)


# ── radius tokens ──────────────────────────────────────────────────────────────
def test_radius_tokens():
    assert T.RADIUS_CONTAINER == 8 and T.RADIUS_CONTROL == 6
    assert T.radius("container") == 8 and T.radius("control") == 6
    assert T.radius("nope") == 8       # container default


# ── WCAG-AA contrast on every text tier × surface ──────────────────────────────
def test_text_tiers_meet_wcag_on_every_surface():
    for dark in (True, False):
        T.set_theme(dark)
        for surf in ("canvas", "raised", "inset"):
            assert _contrast("txt1", surf) >= 4.5, (dark, surf, "txt1")
            assert _contrast("txt2", surf) >= 4.5, (dark, surf, "txt2")
            assert _contrast("txt3", surf) >= 3.0, (dark, surf, "txt3")  # micro-label tier
    T.set_theme(True)
```

- [ ] **Step 2: Run the ladder/radius tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "ladder or radius or inset or neutral or wcag"`
Expected: FAIL — `AttributeError: module 'ui.theme' has no attribute 'ELEVATION'` / `RADIUS_CONTAINER`; and the ladder assert fails because shipped `inset`/`nav` sit *below* `base`.

- [ ] **Step 3: Re-value the token dicts and add the constants**

In `tools/ui/theme.py`, replace the `DARK` and `LIGHT` dicts (lines 21-44) with the monotonic ladder. **Keep every existing key name** (so all call sites keep working) and add the four new semantic keys. Preserve `txt*`, category, status, seg, `ctl*`, `tok`, `accent`, `on_accent` exactly.

```python
DARK: Dict[str, str] = {
    # ── neutral elevation ladder (monotonic, zero hue) ──
    # nav (below) < canvas (window/tab) < raised (panels) ; inset = the one lift.
    "nav": "#191a1c", "base": "#1c1d1f", "surface": "#1c1d1f", "canvas": "#1c1d1f",
    "card": "#232427", "raised": "#232427",
    "inset": "#2a2c30", "card_hover": "#2a2c30",
    # ── single hairline budget ──
    "hairline": "#2e2f33", "stroke": "#2e2f33", "divider": "#2e2f33",
    "hairline_strong": "#3a3b40",
    # ── text tiers (WCAG-tuned — unchanged) ──
    "txt1": "#ffffff", "txt2": "rgba(255,255,255,0.773)", "txt3": "rgba(255,255,255,0.529)",
    # ── control fills ──
    "ctl": "rgba(255,255,255,0.06)", "ctl_hover": "rgba(255,255,255,0.09)",
    "tok": "rgba(255,255,255,0.08)", "subtle_hover": "rgba(255,255,255,0.055)",
    "accent": "#ededed", "on_accent": "#1a1a1a",
    "ok": "#6ccb5f", "warn": "#e8c245", "err": "#ff99a4", "info": "#8ab4e8",
    "ok_bg": "rgba(108,203,95,0.12)", "warn_bg": "rgba(232,194,69,0.12)", "err_bg": "rgba(255,153,164,0.12)",
    "seg1": "rgba(255,255,255,0.82)", "seg2": "rgba(255,255,255,0.5)", "seg3": "rgba(255,255,255,0.3)",
}
LIGHT: Dict[str, str] = {
    "nav": "#eaeaeb", "base": "#f3f3f3", "surface": "#f3f3f3", "canvas": "#f3f3f3",
    "card": "#fbfbfb", "raised": "#fbfbfb",
    "inset": "#eeeeee", "card_hover": "#eeeeee",
    "hairline": "rgba(0,0,0,0.08)", "stroke": "rgba(0,0,0,0.08)", "divider": "rgba(0,0,0,0.08)",
    "hairline_strong": "rgba(0,0,0,0.14)",
    "txt1": "rgba(0,0,0,0.894)", "txt2": "rgba(0,0,0,0.62)", "txt3": "rgba(0,0,0,0.447)",
    "ctl": "rgba(0,0,0,0.03)", "ctl_hover": "rgba(0,0,0,0.05)",
    "tok": "rgba(0,0,0,0.055)", "subtle_hover": "rgba(0,0,0,0.04)",
    "accent": "#1b1b1b", "on_accent": "#ffffff",
    "ok": "#0f7b0f", "warn": "#9d5d00", "err": "#c42b1c", "info": "#005fb8",
    "ok_bg": "rgba(15,123,15,0.10)", "warn_bg": "rgba(157,93,0,0.10)", "err_bg": "rgba(196,43,28,0.09)",
    "seg1": "rgba(0,0,0,0.78)", "seg2": "rgba(0,0,0,0.45)", "seg3": "rgba(0,0,0,0.26)",
}

# ── elevation ladder + radius tokens (Refined-Neutral) ───────────────────────
# Ordered base ladder: each step a small neutral lift, zero hue shift. `inset` is
# the ONE extra lift (grouped / hover / selected) and is not part of this linear
# order (in light it is a downward wash), so it is verified separately.
ELEVATION = ("nav", "canvas", "raised")
RADIUS_CONTAINER = 8    # the one panel per region, menus, dialogs
RADIUS_CONTROL = 6      # buttons, inputs, combos, row hover, focus rings, chips


def radius(role: str = "container") -> int:
    """Two deliberate radii: 8px containers, 6px controls (design-rules §3)."""
    return RADIUS_CONTROL if role == "control" else RADIUS_CONTAINER
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "ladder or radius or inset or neutral or wcag"`
Expected: PASS (5 tests). If a WCAG assert fails, nudge the offending token one step (e.g. lighten `txt3` band or the surface) and re-run — do not lower the threshold.

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/theme.py tests/test_ui_foundation.py
git commit -m "Feature: monotonic neutral elevation ladder + radius/hairline tokens (Phase A foundation)"
```

---

### Task 2: Fixed type scale + tabular mono data

**Files:**
- Modify: `tools/ui/theme.py` (add after the radius helper; touch `mono_font` at `:159-163`).
- Test: `tests/test_ui_foundation.py`

**Interfaces:**
- Consumes: `theme.ui_font`, `theme.mono_font` (existing).
- Produces:
  - `theme.TYPE_SCALE: dict[str, tuple[float, bool, bool]]` — `role -> (point_size, semibold, mono)`.
  - `theme.scale_font(role: str) -> QFont` — a locked font for a named role; unknown role raises `KeyError` (no improvised sizes).
  - `mono_font()` returns a font whose digits align (monospace face + `PreferQuality` strategy).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ui_foundation.py`:

```python
# ── fixed type scale ───────────────────────────────────────────────────────────
def test_type_scale_locks_sizes_and_weights():
    for role in ("hero", "stat", "payload", "group_subhead", "value",
                 "section", "detail_key", "footnote"):
        f = T.scale_font(role)
        size, semibold, mono = T.TYPE_SCALE[role]
        assert abs(f.pointSizeF() - size) < 0.01, role
        # Regular + Semibold only — never Bold/Light/Medium
        assert f.weight() in (QFont.Normal, QFont.DemiBold), role
        assert (f.weight() == QFont.DemiBold) == semibold, role


def test_type_scale_rejects_improvised_roles():
    import pytest
    with pytest.raises(KeyError):
        T.scale_font("jumbo")


def test_mono_font_is_monospace_for_tabular_alignment():
    f = T.mono_font(10)
    assert f.styleStrategy() == QFont.PreferQuality
    # a mono face resolves (bundled Geist/JetBrains guarantee it off-Windows)
    from PyQt5.QtGui import QFontInfo
    assert QFontInfo(f).fixedPitch() or f.family() in \
        ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Geist Mono")
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "type_scale or mono_font"`
Expected: FAIL — `AttributeError: module 'ui.theme' has no attribute 'scale_font'`.

- [ ] **Step 3: Implement the type scale and tabular mono**

In `tools/ui/theme.py`, add after the `radius()` helper:

```python
# ── fixed type scale (design-rules §3, Regular/Semibold only) ────────────────
# role -> (point_size, semibold, mono). Mono is reserved for machine data so
# monospace re-acquires meaning (and gives tabular digit alignment).
TYPE_SCALE: Dict[str, tuple] = {
    "hero":          (15.5, True,  True),   # pin/signal name — the one focal element
    "stat":          (14.0, True,  True),   # stat numbers, tabular
    "payload":       (10.5, True,  True),   # delivered net (category-coloured at call site)
    "group_subhead": (10.5, True,  True),   # group subhead
    "value":         (10.0, False, True),   # value / terminal / side
    "section":       (11.0, True,  False),  # section header (recedes, trailing hairline)
    "detail_key":    (9.0,  False, False),  # detail key / metadata
    "footnote":      (8.5,  False, False),  # column header / role / unit / footnote — quietest
}


def scale_font(role: str) -> QFont:
    """A locked font for a named type role — never improvise a size (design-rules §3).
    Unknown role raises KeyError so a stray size can't slip in."""
    size, semibold, mono = TYPE_SCALE[role]
    return mono_font(size, semibold) if mono else ui_font(size, semibold)
```

Then update `mono_font` (`:159-163`) to request tabular-friendly rendering:

```python
def mono_font(size: float = 9.5, semibold: bool = False) -> QFont:
    f = QFont(_family(_MONO_FAMILIES))
    f.setPointSizeF(size)
    f.setWeight(QFont.DemiBold if semibold else QFont.Normal)
    # Monospace faces are inherently tabular (all glyphs one advance); PreferQuality
    # keeps hinting so stacked digit columns stay aligned at fractional DPI.
    f.setStyleStrategy(QFont.PreferQuality)
    return f
```

- [ ] **Step 4: Run to verify pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "type_scale or mono_font"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/theme.py tests/test_ui_foundation.py
git commit -m "Feature: fixed type-scale helper + tabular mono alignment (Phase A foundation)"
```

---

### Task 3: Refresh `qss()` to consume 8/6 radii, single hairline, borderless surfaces

**Files:**
- Modify: `tools/ui/theme.py:190-275` (the `qss()` return string).
- Test: `tests/test_ui_foundation.py`

**Interfaces:**
- Consumes: `RADIUS_CONTAINER`, `RADIUS_CONTROL`, `hairline` (via re-valued `stroke`/`divider`), the re-valued ladder.
- Produces: a QSS string using `{RADIUS_CONTAINER}px` on containers (tables, menus, tooltips, cards-via-widgets) and `{RADIUS_CONTROL}px` on controls (buttons, inputs, combos, checkboxes, nav items). No behavioural contract change — same object-name hooks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_foundation.py`:

```python
# ── qss consumes the radius + hairline tokens ──────────────────────────────────
def test_qss_uses_two_deliberate_radii():
    css = T.qss(True)
    assert f"border-radius: {T.RADIUS_CONTROL}px" in css   # controls
    assert f"border-radius: {T.RADIUS_CONTAINER}px" in css # containers
    # 4px flat radius is retired everywhere
    assert "border-radius: 4px" not in css
    assert "border-radius: 3px" not in css
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k qss_uses`
Expected: FAIL — the shipped QSS hardcodes `border-radius: 4px` / `3px`.

- [ ] **Step 3: Rewrite `qss()` to use radius tokens**

In `tools/ui/theme.py`, in `qss()`, introduce locals and replace every hardcoded radius. Add at the top of the f-string scope (right after `c = _active`):

```python
    rc = RADIUS_CONTROL       # 6px — controls
    rk = RADIUS_CONTAINER     # 8px — containers
```

Then in the returned QSS replace radii as follows (leave colours/paddings as-is unless listed):
- `#navItem` → `border-radius: {rc}px;`
- `QPushButton` → `border-radius: {rc}px;`
- `QPushButton#seg` → `border-radius: {rc}px;`
- `QPushButton#tokbtn` → `border-radius: {rc}px;`
- `QLineEdit, QPlainTextEdit, QTextEdit` → `border-radius: {rc}px;`
- `QComboBox` → `border-radius: {rc}px;`
- `QSpinBox, QDoubleSpinBox` → `border-radius: {rc}px;`
- `QCheckBox::indicator` → `border-radius: {rc}px;` (was 3px)
- `QScrollBar::handle:*` → `border-radius: {rc}px;`
- `QToolTip` → `border-radius: {rc}px;`
- `QTableWidget, QTableView` → `border-radius: {rk}px;` (container; keep)

The subtab underline (`QPushButton#subtab[selected="true"] border-bottom: 2px solid {c['accent']}`) is **removed** here — it becomes the painted sliding underline in Task 7. For now, replace the border-bottom rule with a color-only selection so the QSS stays valid:

```python
QPushButton#subtab[selected="true"] {{ color: {c['txt1']}; font-weight: 600; }}
```

- [ ] **Step 4: Run to verify pass + no regression**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py tests/test_ui_shell.py tests/test_wave0_settings_theme.py -q`
Expected: PASS. (`qss()` must still be a valid string every widget test applies.)

- [ ] **Step 5: Render-gate visual confirmation (both themes)**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --settle 0.9
```
Read a few PNGs (`build/render/*.png`) with the Read tool: radii should read 8/6 (softer containers, crisp controls), nav one step below canvas, no muddy elevation. No unit assertion — this is a human/agent visual gate.

- [ ] **Step 6: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/theme.py tests/test_ui_foundation.py
git commit -m "Feature: qss consumes 8/6 radius tokens, retires flat 4px (Phase A foundation)"
```

---

### Task 4: De-letterspace `eyebrow()` (re-skins every section header app-wide)

**Files:**
- Modify: `tools/ui/widgets.py:99-108` (`eyebrow`).
- Test: `tests/test_ui_foundation.py`

**Interfaces:**
- Consumes: `theme.ui_font`, `theme.t`.
- Produces: `eyebrow(text)` returns a `QLabel` whose text is **unchanged** (no `.upper()`), Semibold `txt3`, **zero letterspacing** (`PercentageSpacing == 100`). Signature unchanged — every existing call site (bench, git, settings, projects, shell, routing) inherits the reskin.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_foundation.py`:

```python
# ── de-letterspaced eyebrow (the #1 AI tell, design-rules §1.4) ─────────────────
def test_eyebrow_has_zero_letterspacing_and_preserves_case():
    lab = W.eyebrow("Connection Diagram")
    f = lab.font()
    assert f.letterSpacingType() == QFont.PercentageSpacing
    assert abs(f.letterSpacing() - 100.0) < 0.01     # 100% == no tracking
    assert lab.text() == "Connection Diagram"        # NOT upper-cased
    assert f.weight() == QFont.DemiBold
    _destroy(lab)


def test_no_setletterspacing_call_sites_remain():
    # eyebrow was the app's ONLY setLetterSpacing; assert none linger in the kit.
    src = (Path(__file__).resolve().parents[1] / "tools" / "ui" / "widgets.py").read_text()
    assert "setLetterSpacing" not in src
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k eyebrow`
Expected: FAIL — text is upper-cased and letterSpacing is 106%.

- [ ] **Step 3: Rewrite `eyebrow()`**

Replace `tools/ui/widgets.py:99-108`:

```python
def eyebrow(text: str) -> QLabel:
    """A quiet Title-case section label (Semibold, txt3, zero tracking). Retired the
    letterspaced UPPERCASE micro-label (design-rules §1.4) — this reskins every
    WORKSPACES / DETAIL / CONNECTION DIAGRAM header app-wide from one edit. Text is
    passed through verbatim so refdes/part numbers keep their real casing."""
    lab = QLabel(text)
    lab.setFont(T.ui_font(8.5, semibold=True))
    register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"), lab)
    return lab
```

Also update the module docstring casing convention (`:9-13`) so it no longer claims eyebrows are ALL CAPS:

```python
Casing convention (from the design review):
  Title Case -> structural labels (eyebrows, section + column headers) -> `eyebrow(...)`
  Title Case -> human text (titles, buttons, values)
  real casing -> machine data (nets, refdes, pins)                     -> `token/net_token`
Separation is by layout, never a middot. No letterspacing anywhere.
```

- [ ] **Step 4: Run to verify pass + widget regression**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py tests/test_wave1_widgets.py -q -k "eyebrow or letterspacing or primitive or self_clean"`
Expected: PASS. (`test_primitive_widgets_self_clean_on_destroy` still passes — one restyler per eyebrow.)

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/widgets.py tests/test_ui_foundation.py
git commit -m "Feature: de-letterspace eyebrow to Title-case Semibold txt3 (reskins all section headers)"
```

---

### Task 5: Borderless elevation on `Card` / `Verdict` / chips

**Files:**
- Modify: `tools/ui/widgets.py` — `Card._style` (`:369-373`), `Verdict._style` (`:435-445`), `Verdict._chip.style` (`:426-432`).
- Test: `tests/test_ui_foundation.py`

**Interfaces:**
- Consumes: re-valued `raised`/`inset` (via `card`), `hairline`.
- Produces: surfaces separate by background step, not a stroke. `Card` background = `card` (=`raised`), **no border**, `RADIUS_CONTAINER`. `Verdict` band = `card`, no border. The `Verdict` chip loses its stadium `border-radius:14px` + border → a quiet `RADIUS_CONTROL` token chip on `tok`. (design-rules §1.1/§1.2/§5)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_foundation.py`:

```python
# ── borderless elevation (design-rules §1.2/§5) ────────────────────────────────
def test_card_is_borderless():
    c = W.Card()
    css = c.styleSheet()
    assert "border:none" in css.replace(" ", "") or "border-width:0" in css.replace(" ", "")
    assert f"border-radius:{T.RADIUS_CONTAINER}px" in css.replace(" ", "")
    _destroy(c)


def test_verdict_band_is_borderless():
    v = W.Verdict("Ready", "All checks passed", kind="ok")
    assert "border:none" in v.styleSheet().replace(" ", "")
    _destroy(v)
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "borderless or verdict_band"`
Expected: FAIL — `Card` paints `border:1px solid stroke`; `Verdict` too.

- [ ] **Step 3: Drop the decorative borders**

`Card._style` (`:369-373`):

```python
    def _style(self):
        # Borderless elevation (design-rules §1.2): a card separates by its raised
        # background step, never a stroke. Scoped to #ndcard so labels inside don't
        # inherit a frame.
        self.setStyleSheet(f"QFrame#ndcard{{background:{T.t('card')};border:none;"
                           f"border-radius:{T.RADIUS_CONTAINER}px;}}")
```

`Verdict._style` (`:435-445`) — change only the surface line:

```python
        self.setStyleSheet(f"QFrame#ndverdict{{background:{bg};border:none;"
                           f"border-radius:{T.RADIUS_CONTAINER}px;}}")
```

`Verdict._chip.style` (`:426-432`) — the chip is a genuine status tag, but the stadium pill + border is the retired idiom; make it a quiet token:

```python
        def style():
            colmap = {"ok": T.t("ok"), "warn": T.t("warn"), "err": T.t("err")}
            dot.setStyleSheet(f"background:{colmap.get(dotkind, T.t('txt3'))};border-radius:3px;")
            lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;")
            w.setStyleSheet(f"QFrame#ndchip{{background:{T.t('tok')};border:none;"
                            f"border-radius:{T.RADIUS_CONTROL}px;}}")
```

Import note: `RADIUS_*` are on `T` (module `theme`), already imported as `from . import theme as T` — reference `T.RADIUS_CONTAINER` / `T.RADIUS_CONTROL`.

- [ ] **Step 4: Run to verify pass + regression**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py tests/test_wave1_widgets.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/widgets.py tests/test_ui_foundation.py
git commit -m "Feature: borderless elevation on Card/Verdict/chips (design-rules §1.2)"
```

---

### Task 6: New `ui/motion.py` — reduced-motion-aware animation layer

**Files:**
- Create: `tools/ui/motion.py`
- Test: `tests/test_ui_motion.py`

**Interfaces:**
- Consumes: PyQt5 animation classes; `theme` (for focus-ring colour).
- Produces:
  - `motion.reduced_motion() -> bool` / `motion.set_reduced_motion(on: bool | None)` — `None` re-auto-detects.
  - `motion.animate_opacity(widget, start, end, duration=140, on_done=None) -> QPropertyAnimation | None` — installs a `QGraphicsOpacityEffect`, tweens `opacity`; under reduced motion sets `end` immediately, calls `on_done`, returns `None`.
  - `motion.SlidingUnderline(QWidget)` — a painted 2px rule; `.move_to(x, width, animate=True)` tweens `_geom` (a `pyqtProperty`); under reduced motion (or `animate=False`) snaps.
  - `motion.cross_fade(window, apply_fn, duration=160)` — grabs a pixmap overlay, runs `apply_fn()`, fades the overlay out; under reduced motion just calls `apply_fn()`.
  - `motion.paint_focus_ring(painter, rect, color, radius=RADIUS_CONTROL)` — a crisp painted ring (pure, device-pixel-snapped).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_motion.py`:

```python
"""Phase-A motion layer: single reduced-motion gate; animations become no-ops."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402
from PyQt5 import sip  # noqa: E402
import ui.motion as M  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _destroy(w):
    sip.delete(w)


def test_reduced_motion_gate_get_set():
    M.set_reduced_motion(True)
    assert M.reduced_motion() is True
    M.set_reduced_motion(False)
    assert M.reduced_motion() is False


def test_animate_opacity_is_noop_under_reduced_motion():
    M.set_reduced_motion(True)
    done = []
    w = QLabel("x")
    anim = M.animate_opacity(w, 0.0, 1.0, on_done=lambda: done.append(1))
    assert anim is None            # no animation object created
    assert done == [1]             # final state applied synchronously
    _destroy(w)
    M.set_reduced_motion(False)


def test_animate_opacity_returns_animation_when_enabled():
    M.set_reduced_motion(False)
    w = QLabel("x")
    anim = M.animate_opacity(w, 0.0, 1.0, duration=120)
    assert anim is not None
    assert anim.duration() == 120
    anim.stop()
    _destroy(w)


def test_sliding_underline_snaps_under_reduced_motion():
    M.set_reduced_motion(True)
    u = M.SlidingUnderline()
    u.move_to(40, 80, animate=True)     # animate requested but reduced → snap
    assert u.geometry().x() == 40
    assert u.geometry().width() == 80
    _destroy(u)
    M.set_reduced_motion(False)


def test_cross_fade_applies_immediately_under_reduced_motion():
    M.set_reduced_motion(True)
    applied = []
    win = QWidget()
    win.resize(100, 100)
    M.cross_fade(win, lambda: applied.append(1))
    assert applied == [1]
    _destroy(win)
    M.set_reduced_motion(False)
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_motion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.motion'`.

- [ ] **Step 3: Create `tools/ui/motion.py`**

```python
"""ui.motion — the ONE place animation lives.

Qt5 QSS has no CSS transitions, so every eased state change is a
QPropertyAnimation / painted tween here. A single reduced-motion gate makes the
whole layer an instant no-op (accessibility + the render gate's determinism):
each primitive applies its final state synchronously and returns None when the
gate is set. No decorative animation — hover / selection / subtab / theme only.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QRect, QPropertyAnimation, QEasingCurve, pyqtProperty, QAbstractAnimation
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect, QLabel

from . import theme as T

_reduced: bool = False


def _auto_reduced() -> bool:
    """Best-effort OS reduced-motion read; False (motion on) when unknown."""
    try:
        from PyQt5.QtGui import QGuiApplication
        # Qt has no direct reduced-motion flag in Qt5; default to motion-on and let
        # a config flag / set_reduced_motion() override. Headless render gate sets it.
        return False if QGuiApplication.instance() is not None else False
    except Exception:  # noqa: BLE001
        return False


def reduced_motion() -> bool:
    return _reduced


def set_reduced_motion(on: Optional[bool]) -> None:
    """Set the gate. Pass None to re-auto-detect from the OS/config."""
    global _reduced
    _reduced = _auto_reduced() if on is None else bool(on)


# ── opacity tween (theme cross-fade overlay, popovers) ───────────────────────
def animate_opacity(widget: QWidget, start: float, end: float, duration: int = 140,
                    on_done: Optional[Callable] = None) -> Optional[QPropertyAnimation]:
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    if reduced_motion():
        eff.setOpacity(end)
        if on_done:
            on_done()
        return None
    eff.setOpacity(start)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    if on_done:
        anim.finished.connect(on_done)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


# ── sliding subtab underline ─────────────────────────────────────────────────
class SlidingUnderline(QWidget):
    """A painted 2px rule that tweens its x/width to the active subtab, instead of
    the QSS border-bottom snapping between tabs. Painted (not a QSS box) so it is
    one consistent element and can animate."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedHeight(2)
        self._anim: Optional[QPropertyAnimation] = None

    def _get_geom(self) -> QRect:
        return self.geometry()

    def _set_geom(self, r: QRect) -> None:
        self.setGeometry(r)

    geom = pyqtProperty(QRect, fget=_get_geom, fset=_set_geom)

    def move_to(self, x: int, width: int, animate: bool = True) -> None:
        y = self.y()
        target = QRect(int(x), int(y), int(width), self.height())
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not animate or reduced_motion():
            self.setGeometry(target)
            return
        anim = QPropertyAnimation(self, b"geom", self)
        anim.setDuration(160)
        anim.setStartValue(self.geometry())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        self._anim = anim

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), T.qcolor("accent"))
        p.end()


# ── theme cross-fade ─────────────────────────────────────────────────────────
def cross_fade(window: QWidget, apply_fn: Callable[[], None], duration: int = 160) -> None:
    """Fade the theme swap through a grabbed-pixmap overlay instead of a hard flip.
    Under reduced motion (or a failed grab) just apply the change instantly."""
    if reduced_motion():
        apply_fn()
        return
    try:
        pixmap = window.grab()
    except Exception:  # noqa: BLE001
        apply_fn()
        return
    overlay = QLabel(window)
    overlay.setPixmap(pixmap)
    overlay.setGeometry(0, 0, window.width(), window.height())
    overlay.show()
    overlay.raise_()
    apply_fn()

    def _cleanup():
        overlay.deleteLater()

    animate_opacity(overlay, 1.0, 0.0, duration=duration, on_done=_cleanup)


# ── painted keyboard focus ring ──────────────────────────────────────────────
def paint_focus_ring(painter: QPainter, rect: QRect, color: QColor,
                     radius: int = T.RADIUS_CONTROL) -> None:
    """A crisp neutral focus ring on the device-pixel grid (0.5px-inset cosmetic
    1px pen) so it never reads fuzzy at fractional DPI."""
    pen = QPen(color)
    pen.setWidth(1)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    r = rect.adjusted(0, 0, -1, -1)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.drawRoundedRect(r, radius, radius)
```

- [ ] **Step 4: Run to verify pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_motion.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/motion.py tests/test_ui_motion.py
git commit -m "Feature: ui.motion — reduced-motion-aware animation layer (opacity, sliding underline, cross-fade, focus ring)"
```

---

### Task 7: Wire motion into `shell.py` + `Workspace` (nav ease, subtab slide, theme cross-fade)

**Files:**
- Modify: `tools/ui/shell.py` — `apply_theme` (theme cross-fade), headless guard.
- Modify: `tools/ui/widgets.py` — `Workspace` subtab bar uses `SlidingUnderline`.
- Test: `tests/test_ui_foundation.py` (behavioural smoke, headless).

**Interfaces:**
- Consumes: `motion.cross_fade`, `motion.SlidingUnderline`, `motion.set_reduced_motion`.
- Produces: theme toggle fades through an overlay (instant under reduced motion / headless); the active subtab underline slides; behaviour (selection, page build) unchanged.

- [ ] **Step 1: Write the failing/behavioural test**

Append to `tests/test_ui_foundation.py`:

```python
# ── motion wired into the shell / Workspace (headless = instant) ───────────────
def test_workspace_subtab_selection_still_works_with_sliding_underline():
    import ui.motion as M
    M.set_reduced_motion(True)                     # headless determinism
    picked = []
    panels = [("First", lambda ctx: W.body("one")),
              ("Second", lambda ctx: W.body("two"))]
    ws = W.Workspace(ctx=None, title="Demo", panels=panels)
    ws.select_panel("Second")
    # the underline exists and tracks selection without raising
    assert hasattr(ws, "_underline")
    _destroy(ws)


def test_apply_theme_uses_instant_path_when_reduced():
    # Regression guard: the cross-fade must not break the headless theme toggle
    # that render_gate + test_wave0 rely on. Verified indirectly by those suites;
    # here we assert cross_fade with reduced motion calls apply_fn exactly once.
    import ui.motion as M
    M.set_reduced_motion(True)
    calls = []
    from PyQt5.QtWidgets import QWidget
    w = QWidget(); w.resize(50, 50)
    M.cross_fade(w, lambda: calls.append(1))
    assert calls == [1]
    _destroy(w)
```

- [ ] **Step 2: Run to verify failure**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py -q -k "subtab_selection or instant_path"`
Expected: FAIL — `Workspace` has no `_underline`.

- [ ] **Step 3: Add the sliding underline to `Workspace`**

In `tools/ui/widgets.py`, `Workspace.__init__`, replace the static subtab `rule` block (`:625-627`) so the divider stays but an overlaid `SlidingUnderline` tracks the active tab. After building `bar`/`self._tabs` and adding the divider rule, add:

```python
            from .motion import SlidingUnderline
            self._underline = SlidingUnderline(self)
            self._underline.raise_()
```

And at the end of `_select` (`:658-671`), after the loop that toggles `selected`, position the underline under the active tab:

```python
        if getattr(self, "_underline", None) is not None and self._tabs:
            btn = self._tabs[k]
            # map the button's geometry into the Workspace so the painted rule sits
            # exactly under it (device-pixel snapped by integer geometry).
            top_left = btn.mapTo(self, btn.rect().bottomLeft())
            self._underline.move_to(top_left.x(), btn.width(),
                                    animate=self._underline.isVisible())
            self._underline.setGeometry(top_left.x(), top_left.y(),
                                        btn.width(), 2) if not self._underline.isVisible() else None
            self._underline.show()
```

Note: single-panel Workspaces (no tab bar) never create `_tabs`, so guard with `if self._tabs`. Keep `_underline` creation only inside the `if len(self._panels) > 1:` block; initialize `self._underline = None` before that block so the attribute always exists.

- [ ] **Step 4: Add theme cross-fade to `shell.apply_theme`**

In `tools/ui/shell.py`, wrap the visible theme swap in `apply_theme` (`:255-269`) with the cross-fade. Split the method so the actual token/QSS/restyle work is a private `_apply_theme_now`, and `apply_theme` routes through `motion.cross_fade`:

```python
    def apply_theme(self, dark: bool):
        from . import motion as Mo
        Mo.cross_fade(self, lambda: self._apply_theme_now(dark))

    def _apply_theme_now(self, dark: bool):
        self._dark = dark
        T.set_theme(dark)
        try:                                 # keep component previews on the app surface
            import fp_render as R
            R.set_render_theme(dark, T.t("inset"))
        except Exception:  # noqa: BLE001
            pass
        self._apply_palette()
        self.setStyleSheet(T.qss(dark))
        self._set_titlebar_theme(dark)
        W.restyle_all()
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText("" if self._nav_collapsed else ("Dark Theme" if dark else "Light Theme"))
            self._theme_btn.setIcon(W.svg_icon(_ICON["theme"] if dark else _ICON["sun"]))
```

Set the reduced-motion gate once at shell construction from the headless/config state so the render gate and CI stay instant. In `NetdeckShell.__init__` after `T.set_theme(self._dark)` (`:117`), add:

```python
        from . import motion as Mo
        from .util import _headless
        Mo.set_reduced_motion(_headless())   # headless render gate / CI = instant
```

- [ ] **Step 5: Run to verify pass + full shell/theme regression**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_ui_foundation.py tests/test_ui_shell.py tests/test_wave0_settings_theme.py tests/test_routing_shell.py -q`
Expected: PASS. (Routing shell test must stay green — it consumes `Workspace`; the `_underline` guard must not break its single/multi-panel path.)

- [ ] **Step 6: Render-gate confirmation**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --settle 0.9
```
Read the multi-tab surfaces (bench, projects) — the active subtab underline should sit crisply under the active tab in both themes. (Motion itself won't show in a static grab; correctness of placement will.)

- [ ] **Step 7: Commit**

```bash
git pull --rebase --autostash origin main
git add tools/ui/shell.py tools/ui/widgets.py tests/test_ui_foundation.py
git commit -m "Feature: wire motion into shell/Workspace — theme cross-fade + sliding subtab underline"
```

---

### Task 8: Reconcile `design-rules.md` §3/§4 to Refined-Neutral reality

**Files:**
- Modify: `docs/design/design-rules.md` — §3 (lines 91-154) and §4 (155-199). §1, §2, §5 stay **verbatim**.

**Interfaces:** none (documentation). This is the design contract; keeping it truthful is a spec deliverable (spec §6).

- [ ] **Step 1: Rewrite §3 heading + tokens**

Replace the §3 title `## 3. Locked tokens — direction: **Quiet Instrument**` and its body with the shipped Refined-Neutral tokens: the neutral ladder (`nav`/`canvas`/`raised`/`inset` with the Task 1 values, both themes), the single hairline budget (`hairline`/`hairline_strong`), the unchanged `txt1/txt2/txt3` tiers, the **neutral interaction accent** (`accent` = near-white `#ededed` dark / near-black `#1b1b1b` light — no azure), the meaning-only category palette, 8/6 radii, the fixed type scale (Task 2 roles/sizes), and the motion policy (subtle, reduced-motion-aware). Remove every azure `#4FA1E6` reference.

- [ ] **Step 2: Rewrite §4 recipes**

Update the component recipes to reference the neutral tokens (e.g. "selected-row wash = the one `inset` step", "focus ring = neutral `accent`, painted, 6px") instead of azure. Keep the Quiet-Instrument *structure* (borderless ledger, signal-path container, one-hot ghosting by painted opacity) — only the colour/accent language changes.

- [ ] **Step 3: Add the changelog line**

At the top of §3 (right under the heading), add:

```markdown
> **Changelog:** 2026-07-08 — the azure "Quiet Instrument" accent (speced 2026-07-04,
> never shipped) is **retired** by owner decision. Direction is **Refined Neutral**:
> the neutral WinUI-grey identity elevated through space, hierarchy, type, borderless
> elevation, and subtle motion — no brand accent. §1/§2/§5 are unchanged.
```

- [ ] **Step 4: Sanity check — no azure references linger**

Run: `grep -n "4FA1E6\|azure\|Quiet Instrument" docs/design/design-rules.md`
Expected: only the changelog line's historical mention of "Quiet Instrument"/azure remains; no live token uses azure.

- [ ] **Step 5: Commit**

```bash
git pull --rebase --autostash origin main
git add docs/design/design-rules.md
git commit -m "docs(design): reconcile design-rules §3/§4 to shipped Refined-Neutral (azure retired)"
```

---

### Task 9: Phase-A acceptance — full render gate + self-audit + green suite

**Files:** none (verification task). Deliverable: a render-gated, self-audited, green foundation ready for Phase B/C.

- [ ] **Step 1: Full test suite (exactly what CI gates on)**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q`
Expected: PASS — ~829 prior + the new foundation/motion tests, 3 skip. If any prior test asserts an old token value (e.g. a hardcoded `#202020` or `border-radius:4px`), update that test to the new foundation value **only when the new value is correct** — never weaken an assertion to hide a regression.

- [ ] **Step 2: Render gate, both themes**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --settle 0.9
```

- [ ] **Step 3: Self-audit every surface against design-rules §5**

Read the PNGs in `build/render/` with the Read tool. For each surface × theme, confirm the §5 checklist:
- Can any border/box/pill be deleted with no information loss? (should already be gone)
- One clear focal point per view?
- No letterspaced uppercase labels anywhere?
- Every colour carries meaning (category/status only); no tinted surfaces?
- One elevation level per region; nav < canvas < raised; no muddy ladder?
- Spacing on the 4px grid; mono columns tabular-aligned?
- Radii read 8 (containers) / 6 (controls)?
- Final gut check: "shipped app," not "mockup"?

Log any surface that still fails to `~/Documents/Obsidian/Brain/Agent/Hardware App Gripes.md` as a Phase-C item (do not silently expand this plan).

- [ ] **Step 4: Windows-safety scan (CI build gate)**

Run: `grep -rn "str(Path\|os.sep" tools/ui/theme.py tools/ui/widgets.py tools/ui/motion.py tools/ui/shell.py`
Expected: no new `str(Path(...))` label/path construction (the posix-vs-backslash bug already killed one build). None expected — this phase touches no paths.

- [ ] **Step 5: Final commit (if the audit produced doc/gripe updates only)**

```bash
git pull --rebase --autostash origin main
git add docs/superpowers/plans/2026-07-08-ui-polish-phase-a-foundation.md
git commit -m "docs(ui): Phase A foundation render-gated + self-audited (acceptance)"
git push origin main
```

---

## Phases B & C (outline — separate plans after Phase A render-gates)

Per the spec's foundation-first sequencing, Phases B and C are **not** detailed here: they apply the foundation and depend on Phase A's actual rendered output and a per-tab visual audit. Each gets its own `writing-plans` pass once Phase A is green and render-gated.

- **Phase B — shared polish patterns** (`widgets.py` additions, render-gated): quiet empty-state pattern (muted glyph + one Title-case line + optional single action); skeleton/loading states (determinate/indeterminate + skeleton rows, shimmer honors reduced-motion); thin overlay scrollbars (fade on hover/scroll); one soft popover-only shadow token (menus/dialogs/tooltips, never cards); unified pixel-snapped iconography from the type scale; consistent delayed tooltips; keyboard focus path using `motion.paint_focus_ring` everywhere.
- **Phase C — per-tab passes** (one render-gated commit per tab, ordered by impact): Bench → Library → Projects/BOM → PCB Setup → Git → Settings → Routing. Each applies the foundation + Phase-B patterns with a screen-specific "what amazing means here" checklist; fold in cheap open Bench gripes (BENCH-03/08/13) where they fit. **Routing is coordinated, not owned** — touch only the shared `theme.py`/`widgets.py` it consumes, or hand the parallel session the tokens.

---

## Self-Review

**1. Spec coverage** (spec §1–§6):
- §1.1 monotonic neutral ladder → Task 1. §1.2 radii 8/6 → Task 1 + Task 3. §1.3 fixed type scale + tabular → Task 2. §1.4 hairline crispness (device-pixel) → Task 6 (`paint_focus_ring` cosmetic pen) + Task 7 (integer geometry underline); QSS hairlines are single-token via Task 1/3.
- §2 de-letterspace → Task 4; remove decorative borders → Task 5; button hierarchy → primitives already exist (`btn(kind=…)`), call-site hierarchy is explicitly Phase C.
- §3 motion (eased hover/selection, sliding underline, theme cross-fade, focus rings, press feedback) → Task 6 primitives + Task 7 wiring. *Press feedback and per-widget eased hover on nav/table rows are stubbed by the primitives but full call-site wiring lands with Phase C tabs; the shared theme-fade + subtab slide + focus ring ship in Phase A.*
- §4 polish layer → Phase B outline. §5 per-tab → Phase C outline. §6 doc reconciliation → Task 8.
- Testing & acceptance (spec) → Tasks 1–9 tests + Task 9 render gate/contrast/monotonicity/type-scale/motion-gate/eyebrow assertions all present.

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N" — every code step shows complete code. The only deliberate deferrals (Phase B/C, per-widget hover/press wiring) are called out explicitly as out-of-Phase-A scope, matching the spec's foundation-first sequencing, not hidden placeholders.

**3. Type consistency:** `RADIUS_CONTAINER`/`RADIUS_CONTROL`/`radius()` (Task 1) referenced consistently in Tasks 3/5/6. `scale_font`/`TYPE_SCALE` (Task 2) consistent. `ELEVATION` tuple (Task 1) used by Task 1 test. `motion.reduced_motion/set_reduced_motion/animate_opacity/SlidingUnderline/cross_fade/paint_focus_ring` defined in Task 6, consumed with the same names in Task 7. `Workspace._underline` initialized-then-guarded consistently. `eyebrow(text)` signature unchanged (Task 4) so no call site breaks.

**Risk note flagged for the executor:** re-valuing shipped tokens (`inset` flips from a downward `#1c1c1c` to a lift `#2a2c30`; `stroke`/`divider` collapse to one hairline) changes the look of surfaces owned by the parallel routing session. This is intended ("foundation lifts all tabs at once") and requires no edits to their files, but Task 9's render gate must include the Routing surface and any regression there is coordinated with that session, not fixed by editing their files.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-08-ui-polish-phase-a-foundation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
