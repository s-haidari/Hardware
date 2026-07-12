"""ui.kit — the composition layer features call. Sits ABOVE widgets.py: composes the
primitive kit + tokens/scale/motion/icons into page-level builders that own all
styling, so a feature declares content and never styles directly. Bespoke visuals
enter via kit.custom(). See docs/superpowers/specs/2026-07-09-central-ui-kit-and-legibility.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSplitter

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
    v.setSpacing(T.sp("md"))
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
    v.setSpacing(T.sp("path"))
    for b in body:
        if b is not None:
            v.addWidget(b)
    v.addStretch(1)
    return W.Workspace(ctx=None, title=title,
                       panels=[(title, lambda _ctx: W.scroll_body(inner))],
                       header=head)


def tabbed_page(title: str, panels, *, header: Optional[QWidget] = None, ctx=None):
    """A multi-panel page: reuses widgets.Workspace (sliding subtab underline). `panels`
    is a list of (name, builder(ctx)->QWidget). Use when a feature has sub-panels;
    kit.page is the single-panel form. `ctx` is forwarded to each panel builder and is
    optional (defaults to None) so a feature with real services/bus can pass its own.
    Returns the Workspace so callers can use rebuild_all/select_panel."""
    return W.Workspace(ctx=ctx, title=title, panels=list(panels), header=header)


def panes(sections, *, key: Optional[str] = None, sizes: Optional[Sequence[int]] = None,
          collapsible: Optional[Sequence[bool]] = None,
          min_widths: Optional[Sequence[int]] = None) -> QSplitter:
    """The reusable multi-pane scaffold — the list · center · detail layout that
    Library / PCB / Bench opt into (simple surfaces stay on the single-column kit.page).

    Panes drag-resize via thin handles. `collapsible[i]` marks panes that may collapse
    to zero (default: the two ends collapse, the center never does — you never want the
    working surface to vanish). `min_widths[i]` floors a pane. `sizes` seeds the initial
    widths. `key` persists the layout under app settings so a user's pane widths survive
    relaunch. Handle chrome is styled centrally by the `#panes` object-name in theme.qss()
    — nothing here styles directly."""
    sp = QSplitter(Qt.Horizontal)
    sp.setObjectName("panes")
    sp.setHandleWidth(1)
    n = len(sections)
    if collapsible is None:
        collapsible = [n > 1 and i != n // 2 for i in range(n)]
    for i, w in enumerate(sections):
        sp.addWidget(w)
        sp.setCollapsible(i, bool(collapsible[i]) if i < len(collapsible) else True)
        if min_widths and i < len(min_widths) and min_widths[i]:
            w.setMinimumWidth(int(min_widths[i]))
    if sizes:
        sp.setSizes([int(s) for s in sizes])
    if key:
        _persist_panes(sp, str(key))
    return sp


def _persist_panes(sp: QSplitter, key: str) -> None:
    """Restore this splitter's saved widths now and re-save them whenever the user drags
    a handle, keyed in the app settings — so pane layout survives relaunch. Best-effort:
    a missing/blank/stale stored state just leaves the seeded sizes in place."""
    from PyQt5.QtCore import QByteArray
    setting = f"Panes.{key}"
    try:
        import LibraryManager as LM
    except Exception:  # noqa: BLE001
        return
    saved = LM.read_setting(setting, "") or ""
    if saved:
        try:
            sp.restoreState(QByteArray.fromBase64(saved.encode("ascii")))
        except Exception:  # noqa: BLE001
            pass

    def _save(*_a):
        try:
            LM.write_setting(setting, bytes(sp.saveState().toBase64()).decode("ascii"))
        except Exception:  # noqa: BLE001
            pass
    sp.splitterMoved.connect(_save)


from . import icons as _icons


def state(kind: str, line: str, *, glyph: str = "", sub: str = "",
          action: Optional[Action] = None) -> QWidget:
    """One state pattern: empty / loading / error. loading => skeleton rows."""
    if kind == "loading":
        return W.skeleton_rows(rows=6, cols=4)
    act = W.btn(action.text, kind=action.kind, tip=action.tip, on_click=action.on) if action else None
    g = _icons.GLYPHS.get(glyph, "") if glyph else ""
    return W.empty_state(line, glyph=g, sub=sub, action=act)


def page_layout(root=None, *, spacing: Optional[int] = None,
                margin: Optional[int] = None) -> QVBoxLayout:
    """The ONE page frame: a QVBoxLayout with SYMMETRIC page margins + the contract's
    24px inter-section spacing (design-rules §Spacing: "24px between sections"), routed
    through `T.sp("page")` so the app-wide rhythm is tunable in one place. Replaces the
    scattered, asymmetric `(24,16,24,24)` + `setSpacing(14/12/16)` frames every panel
    hand-rolled (the "spacing is cooked" report). Pass `root` to parent it; override
    `margin`/`spacing` (px) only for a deliberate, documented exception."""
    m = T.sp("page") if margin is None else margin
    s = T.sp("page") if spacing is None else spacing
    lay = QVBoxLayout(root) if root is not None else QVBoxLayout()
    lay.setContentsMargins(m, m, m, m)
    lay.setSpacing(s)
    return lay


from PyQt5.QtWidgets import QLabel, QGridLayout, QSizePolicy


# A stat value splits into a leading magnitude (sign, digits, separators, exponent)
# and a trailing unit token: "±25 mA" -> ("±25", "mA"), "3.3 V" -> ("3.3", "V"),
# "1,024" -> ("1,024", ""), "64" -> ("64", ""). The magnitude run is the sign and the
# first contiguous number (with grouping/decimal separators); everything after it,
# stripped of the joining space, is the unit. A bare count therefore keeps an empty
# unit and renders wholly in txt1.
_MAGNITUDE = re.compile(r"^\s*([+\-±~<>≤≥]*\d[\d.,]*(?:[eE][+\-]?\d+)?)(.*)$", re.DOTALL)


def _split_magnitude_unit(value: str) -> Tuple[str, str]:
    m = _MAGNITUDE.match(value)
    if not m:
        return value, ""
    magnitude, rest = m.group(1), m.group(2)
    return magnitude, rest.strip()


def stat_strip(stats: Sequence[Tuple[str, str]]) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(28)
    for number, label in stats:
        col = QWidget(); cv = QVBoxLayout(col); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(1)
        magnitude, unit = _split_magnitude_unit(number)
        row = QWidget(); rh = QHBoxLayout(row); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(4)
        num = QLabel(magnitude); num.setFont(T.scale_font("stat"))
        W.register_restyle(lambda num=num: num.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), num)
        rh.addWidget(num, 0, Qt.AlignBaseline)
        if unit:
            # Units pushed to txt3/footnote so a spec (±25 mA) reads distinctly from a
            # bare count (design-rules §"Stat strip"). Baseline-aligned to the number.
            un = QLabel(unit); un.setFont(T.scale_font("footnote"))
            W.register_restyle(lambda un=un: un.setStyleSheet(f"color:{T.t('txt3')};background:transparent;"), un)
            rh.addWidget(un, 0, Qt.AlignBaseline)
        rh.addStretch(1)
        lab = W.eyebrow(label)
        cv.addWidget(row); cv.addWidget(lab)
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
                dot.setStyleSheet(W.dot_css(col, 10))
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


# ── the orchestrated ▶ primary flow (ported from bare, headless-safe) ─────────
def _report_text(result) -> str:
    """Structured report dict → human text (spec §5): a summary, ✓ done, ⚠ missing
    [{item,why,how_to_fix}], ✗ errors. A plain string passes through. Pure + testable —
    the port of bare._report's body-building, split out so it is unit-testable headlessly."""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    parts = []
    if result.get("summary"):
        parts.append(str(result["summary"]))
    done = result.get("done") or []
    if done:
        parts.append("\n✓ Done:\n  " + "\n  ".join(str(x) for x in done))
    missing = result.get("missing") or []
    if missing:
        parts.append("\n⚠ Still missing / incomplete:")
        for m in missing:
            if isinstance(m, dict):
                parts.append(f"  • {m.get('item', '?')} — {m.get('why', '')}\n"
                             f"      → Fix: {m.get('how_to_fix', '')}")
            else:
                parts.append(f"  • {m}")
    errors = result.get("errors") or []
    if errors:
        parts.append("\n✗ Errors:\n  " + "\n  ".join(str(x) for x in errors))
    return "\n".join(parts).strip() or "Nothing to do."


def _report(host, title: str, result, *, log: Optional[Callable] = None) -> None:
    """Show a human results dialog from a str | {summary,done,missing,errors} dict — the
    port of bare._report. HEADLESS (offscreen render_gate / drive_audit / CI): NO exec_()
    (a modal loop no user dismisses would hang the run) — log the one-line summary and
    return. GUI: parent to host.window() (the TOP-LEVEL window, never the rebuildable body
    widget — a concurrent handle.rebuild()/Workspace._select must not delete a live modal's
    parent), show, then mirror a one-line summary to the log."""
    from .util import _headless
    body = _report_text(result)
    line = f"{title}: " + body.replace("\n", " ")[:200]
    if _headless():
        if callable(log):
            log(line)
        return
    from PyQt5.QtWidgets import QMessageBox
    box = QMessageBox(host.window() if host is not None else None)
    box.setWindowTitle(title)
    head = body if len(body) < 900 else body[:900] + "\n… (full detail below)"
    box.setText(head)
    if len(body) >= 900:
        box.setDetailedText(body)
    box.exec_()
    if callable(log):
        log(line)


def _checkbox_preview(host, title: str, intro: str, ops: Sequence[dict]):
    """Preview `ops` (each {key,label,detail,safe}) as checkboxes — safe pre-checked, risky
    amber+unchecked — and return the selected keys, or None if cancelled. The port of
    bare._checkbox_preview. HEADLESS: NO exec_() — return the SAFE/pre-checked keys directly
    (the deterministic 'apply what it safely can' default), so a headless drive runs the
    whole audit→apply path without blocking. GUI: parent to host.window() (see _report)."""
    from .util import _headless
    safe = [op.get("key") for op in ops if op.get("safe")]
    if _headless():
        return safe
    from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox)
    dlg = QDialog(host.window() if host is not None else None)
    dlg.setWindowTitle(title)
    v = QVBoxLayout(dlg)
    if intro:
        lab = QLabel(intro); lab.setWordWrap(True); v.addWidget(lab)
    boxes = []
    for op in ops:
        cb = QCheckBox(op.get("label", op.get("key", "?")))
        cb.setChecked(bool(op.get("safe")))
        if op.get("detail"):
            cb.setToolTip(op["detail"])
        if not op.get("safe"):
            cb.setStyleSheet(f"color:{T.t('warn')};")   # tint risky (overwrite/delete) ops
        v.addWidget(cb)
        boxes.append((op.get("key"), cb))
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText("Apply Checked")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    if dlg.exec_() != QDialog.Accepted:
        return None
    return [k for k, cb in boxes if cb.isChecked()]


def open_subpage(ctx, widget, title: str = "", *, on_result: Optional[Callable] = None) -> bool:
    """Open `widget` as a pushed in-app subpage (Back-navigable) over the content area
    instead of a modal OS window — the app-wide "no new windows" pattern. Routes through
    the shell via the ``nav.push_subpage`` bus event; returns True once emitted.

    Non-blocking by design: unlike ``dlg.exec_()`` it never runs a nested modal loop, so it
    is headless-safe (an offscreen drive / render gate can push, drive, then Back out — no
    hang). ``on_result(result)`` fires when the subpage closes, carrying a QDialog's
    accept/reject result so the caller's closure can read the dialog's outcome
    (``dlg.applied`` / ``dlg.plan()`` / ``dlg.picked`` …) exactly as a post-exec_() read
    would. Use it everywhere a feature used to ``dlg.exec_()`` a content dialog."""
    bus = getattr(ctx, "bus", None)
    if bus is None:                               # no shell/bus (rare — a stray standalone build)
        return False
    bus.emit("nav.push_subpage", widget, title, on_result)
    return True


class BusyDict(dict):
    """The workbench's shared busy gate, as a dict whose ``on`` flips notify a callback.

    The recipe reads ``busy['on']`` to no-op a refresh while a mutating op runs (the
    re-entrancy guard). This subclass additionally fires ``on_change`` on every flip so a
    feature can disable ALL its action buttons around ANY mutating op — the ▶ flow (which
    flips this via kit's ``busy_gate``) AND the secondary ops (which flip it directly) —
    through one hook, instead of the recipe or each handler having to. Extracted from the
    Git pilot the moment a second workbench (Library) needed it."""

    def __init__(self):
        super().__init__(on=False)
        self.on_change = None

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "on" and callable(self.on_change):
            self.on_change()


@dataclass
class PrimaryFlow:
    """The single accent ▶ orchestrated action of a workbench (spec §5). audit + apply run
    OFF the GUI thread; intro builds the preview text. `empty` is reported when audit → [].

    ``preview`` is an optional richer preview: a GUI-thread callable
    ``(host, label, intro_text, ops) -> list[keys] | None`` (None = cancel) that REPLACES
    the default flat ``_checkbox_preview`` for this flow only. A feature supplies it to keep
    a bespoke, higher-fidelity confirmation (e.g. the Fix-All ``FillPreviewDialog``: per-field
    old→new deltas, confidence chips, Only-Exact bulk) while still riding the recipe spine
    (audit→preview→apply→report→after, busy-gated). It MUST stay headless-safe itself (return
    the safe/pre-checked keys under ``_headless()`` so an offscreen drive never blocks), and
    the keys it returns are exactly what ``apply(snapshot, keys)`` consumes."""
    label: str
    audit: Callable                    # (snapshot) -> [ {key,label,detail,safe} ]  (off-thread)
    intro: Callable                    # (snapshot, ops) -> intro text
    apply: Callable                    # (snapshot, keys) -> report dict            (off-thread)
    tip: str = ""
    empty: str = "Nothing to do."
    preview: Optional[Callable] = None  # (host, label, intro_text, ops) -> keys|None; GUI thread


def run_primary_flow(ctx, host, flow: "PrimaryFlow", snapshot: Callable[[], dict],
                     after: Optional[Callable[[], None]] = None,
                     busy_gate: Optional[Callable[[bool], None]] = None) -> None:
    """Drive the orchestrated ▶: audit OFF-thread → preview (safe pre-checked) → apply
    OFF-thread → report → after() (re-audit). ONE marshal rule (spec §7): the workers touch
    no widgets; run_populate's populate callback is the only GUI-thread hop; the modals are
    headless-guarded (see _report/_checkbox_preview). Under offscreen Qt run_populate is
    synchronous, so the whole flow runs inline — drivable headlessly for the pilot's gate."""
    from .util import run_populate
    snap = snapshot()
    log = getattr(getattr(ctx, "services", None), "log", None)
    if callable(busy_gate):
        busy_gate(True)

    def _finish():
        if callable(busy_gate):
            busy_gate(False)
        if callable(after):
            after()

    def on_audit(ops, ok):
        ops = ops or []
        if not ops:
            _report(host, flow.label, {"summary": flow.empty}, log=log)
            _finish(); return
        # A flow may supply a richer preview (kept headless-safe by the feature); default is
        # the shared flat checkbox preview. Both return the selected keys, or None on cancel.
        preview = flow.preview or _checkbox_preview
        keys = preview(host, flow.label, flow.intro(snap, ops), ops)
        if keys is None:                       # cancelled
            if callable(log):
                log(f"{flow.label} cancelled")
            _finish(); return
        if not keys:                           # nothing selected (all risky, none checked)
            _report(host, flow.label, {"summary": "Nothing selected."}, log=log)
            _finish(); return

        def on_done(report, ok2):
            _report(host, flow.label,
                    report if report is not None else {"summary": "Done."}, log=log)
            _finish()
        run_populate(ctx, lambda: flow.apply(snap, keys), on_done)

    run_populate(ctx, lambda: flow.audit(snap), on_audit)


# ── secondary grid · selector · exports ──────────────────────────────────────
def button_grid(actions: Sequence[Action], cols: int = 2) -> QWidget:
    """A 2-col grid of default-kind action buttons — the workbench's secondary atoms
    (spec §2.5/§6). Rejects a primary-kind action: the single accent lives in the ▶ flow,
    so a primary here would be a second accent (the one-primary invariant, enforced)."""
    for a in actions:
        if getattr(a, "kind", "default") == "primary":
            raise ValueError("button_grid holds no primary action: the accent lives in the ▶ flow")
    w = QWidget()
    grid = QGridLayout(w)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(T.sp("md"))
    grid.setVerticalSpacing(T.sp("sm"))
    cols = max(1, int(cols))
    for i, a in enumerate(actions):
        r, c = divmod(i, cols)
        grid.addWidget(W.btn(a.text, kind=a.kind, tip=a.tip, on_click=a.on), r, c)
    for c in range(cols):
        grid.setColumnStretch(c, 1)
    return w


def menu_button(label: str, actions: Sequence[Action], *, tip: str = "", kind: str = "default") -> QWidget:
    """Collapse a family of related secondary Actions into ONE menu button (progressive
    disclosure) — the Action-model counterpart of W.menu_button. Rejects a primary-kind
    action: the single accent lives in the ▶ flow, so a primary here would be a second
    accent (the one-primary invariant, enforced as in button_grid)."""
    for a in actions:
        if getattr(a, "kind", "default") == "primary":
            raise ValueError("menu_button holds no primary action: the accent lives in the ▶ flow")
    return W.menu_button(label, [(a.text, a.on, a.tip) for a in actions], tip=tip, kind=kind)


class Selector(QWidget):
    """A labeled object picker (spec §2.C): an eyebrow label + a combo of `options`.
    on_change fires with the selected text on a USER pick only — the initial index is set
    BEFORE the signal is connected, so building never fires a spurious pick. set_value
    reflects a change made elsewhere (a shared-state sync) silently."""

    def __init__(self, label: str, options: Sequence[str], on_change: Optional[Callable[[str], None]] = None,
                 selected: int = 0, parent=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QComboBox
        self._on_change = on_change
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(T.sp("md"))
        h.addWidget(W.eyebrow(label))
        self._combo = QComboBox()
        self._combo.addItems([str(o) for o in options])
        if 0 <= selected < len(options):
            self._combo.setCurrentIndex(selected)
        self._combo.currentTextChanged.connect(self._pick)   # connect AFTER the initial set
        h.addWidget(self._combo)
        h.addStretch(1)

    def _pick(self, text: str):
        if callable(self._on_change):
            self._on_change(text)

    def value(self) -> str:
        return self._combo.currentText()

    def set_value(self, text: str):
        self._combo.blockSignals(True)
        idx = self._combo.findText(str(text))
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)


@dataclass
class ExportAction:
    """A collapsible export (spec §6): produce(snapshot) -> the export text; default_name is
    a str or (snapshot)->str so it can reflect the current selection at click time."""
    label: str
    produce: Callable                  # (snapshot) -> str
    default_name: object               # str | (snapshot) -> str
    filt: str = "All Files (*)"
    tip: str = ""


def export_action(label: str, produce: Callable, default_name, *,
                  filt: str = "All Files (*)", tip: str = "") -> ExportAction:
    return ExportAction(label, produce, default_name, filt, tip)


def _export_default_name(ea: ExportAction, snap: dict) -> str:
    return ea.default_name(snap) if callable(ea.default_name) else ea.default_name


def _export_write(ea: ExportAction, snap: dict, path: str) -> str:
    """Write produce(snap) to `path` (the off-thread work of an export). Pure + testable."""
    from pathlib import Path
    Path(path).write_text(ea.produce(snap), encoding="utf-8")
    return path


def _export_slot(ctx, host, ea, snapshot: Callable[[], dict]) -> QWidget:
    """One entry in the Export collapsible. An ``ExportAction`` renders as an export
    button (ask-path → write produce() off-thread); a plain ``Action`` renders as a
    normal button whose handler owns its own dialog/format — the escape hatch for
    exports the flat produce→write model can't express (a binary workbook, a pre-save
    options dialog, a clipboard copy). Both read the SAME collapsible chrome."""
    if isinstance(ea, Action):
        return W.btn(ea.text, kind=ea.kind, tip=ea.tip, on_click=ea.on)
    return export_button(ctx, host, ea, snapshot)


def export_button(ctx, host, ea: ExportAction, snapshot: Callable[[], dict]):
    """A button that asks for a save path (GUI thread), then writes produce() off-thread and
    reports the result — the port of bare._export_action into the recipe."""
    def handler():
        from PyQt5.QtWidgets import QFileDialog
        from .util import run_populate
        snap = snapshot()
        name = _export_default_name(ea, snap)
        path, _ = QFileDialog.getSaveFileName(host, ea.label, name, ea.filt)
        if not path:
            return
        log = getattr(getattr(ctx, "services", None), "log", None)
        run_populate(ctx, lambda: _export_write(ea, snap, path),
                     lambda r, ok: _report(host, ea.label,
                                           {"summary": f"Wrote {path}"} if ok else
                                           {"errors": ["export failed"]}, log=log))
    return W.btn(ea.label, tip=ea.tip, on_click=handler)


# ── the assembled recipe: kit.workbench (spec §2) ────────────────────────────
def workbench(ctx, *, title: str, snapshot: Callable[[], dict],
              detail: Callable,
              selector: Optional["Selector"] = None,
              verdict: Optional[Callable[[dict], object]] = None,
              primary: Optional["PrimaryFlow"] = None,
              secondary: Sequence[Action] = (),
              machinery: Sequence[Action] = (),
              exports: Sequence[ExportAction] = (),
              busy: Optional[dict] = None,
              chip_slots: int = 3) -> QWidget:
    """Assemble one workbench sub-surface (spec §2): selector → quiet-when-OK verdict band →
    active detail region → 0-or-1 accent ▶ primary → 2-col secondary grid → collapsible
    machinery → collapsible exports. Returns the body widget (wrap in W.scroll_body +
    W.Workspace). The single accent is the `primary` flow; a primary-kind action in
    `secondary` is rejected. Exposes `_verdict` / `_region` / `_refresh` and, when a primary
    is present, the `_run_primary` test seam (so drive_audit can drive the flow headlessly)."""
    if any(getattr(a, "kind", "default") == "primary" for a in secondary):
        raise ValueError("a workbench's only accent primary is the ▶ flow: none in `secondary`")

    from .util import run_populate
    # Shared busy flag: the primary flow AND any secondary mutating op (a feature can pass its
    # own dict) set it, so a watchdog-driven refresh no-ops while ANY of them writes the object
    # — the re-entrancy guard (spec §4/§5). Default: an internal flag for the primary alone.
    busy = busy if busy is not None else {"on": False}
    host = QWidget()
    root = QVBoxLayout(host)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(T.sp("path"))

    if selector is not None:
        root.addWidget(selector)

    vslot = W.VerdictSlot(chip_slots=chip_slots)
    root.addWidget(vslot)

    region = W.RefreshRegion(ctx, snapshot, detail, busy=lambda: busy["on"])
    root.addWidget(region)

    def _refresh_verdict():
        if verdict is None:
            vslot.set(None)
            return
        snap = snapshot()
        run_populate(ctx, lambda: verdict(snap), lambda st, ok: vslot.set(st if ok else None))

    # The detail's own high-frequency refresh (a watchdog tick, a re-audit) must update BOTH
    # the card AND the verdict on the same cadence (spec §3). Rebind the handle's refresh to
    # the COMBINED refresh — the detail captured the same handle object, so its later
    # handle.refresh() calls resolve to this. Capture the ORIGINAL first to avoid recursion.
    _region_refresh = region.handle.refresh
    def _refresh():
        # Re-entrancy guard (spec §4/§5): while a primary flow is in flight, a watchdog-
        # driven refresh must skip BOTH the card fill AND the off-thread verdict recompute,
        # so a verdict()/detail status read can't race the flow's worker on the same object.
        # The flow clears busy BEFORE its after=_refresh runs, so the post-flow refresh lands.
        if busy["on"]:
            return
        _region_refresh()
        _refresh_verdict()
    region.handle.refresh = _refresh

    if primary is not None:
        def _run_primary():
            run_primary_flow(ctx, host, primary, snapshot, after=_refresh,
                             busy_gate=lambda on: busy.__setitem__("on", on))
        root.addWidget(_action_bar((action(primary.label, _run_primary,
                                           kind="primary", tip=primary.tip),)))
        host._run_primary = _run_primary            # test / drive_audit seam

    if selector is not None and hasattr(selector, "_on_change"):
        # a per-tab selector change → snapshot already reflects the pick → deferred rebuild
        # of this tab's region + a verdict recompute (spec §2.C, per-tab tier).
        selector._on_change = lambda _t: (region.handle.rebuild(), _refresh_verdict())

    if secondary:
        root.addWidget(button_grid(secondary))

    mach_body = button_grid(machinery) if machinery else None
    root.addWidget(W.CollapsibleSection("Manage", mach_body))

    if exports:
        exp_host = QWidget()
        ev = QVBoxLayout(exp_host); ev.setContentsMargins(0, 0, 0, 0); ev.setSpacing(T.sp("sm"))
        for ea in exports:
            ev.addWidget(_export_slot(ctx, host, ea, snapshot))
        root.addWidget(W.CollapsibleSection("Export", exp_host))
    else:
        root.addWidget(W.CollapsibleSection("Export", None))

    host._verdict = vslot
    host._region = region
    host._refresh = _refresh
    host._busy = busy                               # shared gate (secondary ops set it too)
    _refresh_verdict()                              # initial verdict
    return host


# ── the assembled recipe: kit.editor (spec 2026-07-10-phase2-projects-kit-editor §2) ──
def editor(ctx, *, title: str, snapshot: Callable[[], dict],
           build_body: Callable,
           primary: Optional["PrimaryFlow"] = None,
           after: Optional[Callable[[], None]] = None,
           secondary: Sequence[Action] = (),
           machinery: Sequence[Action] = (),
           exports: Sequence[ExportAction] = (),
           busy: Optional[dict] = None,
           chip_slots: int = 3) -> QWidget:
    """Assemble one EDITOR sub-surface — the recipe's THIRD shape (PCB Setup, Net Classes).

    Same visual grammar as ``workbench`` (push verdict band → body → 0-or-1 accent ▶ →
    2-col secondary grid → collapsible Manage → collapsible Export) but the body holds
    UNSAVED user edits in live widgets, so it is built ONCE by ``build_body(ctx, host) ->
    (widget, controller)`` and mounted directly — NOT wrapped in a ``RefreshRegion``. There
    is therefore no auto-refresh to clobber an in-progress edit.

    The verdict is PUSH: the band starts hidden and the feature calls ``host._set_verdict
    (state)`` after a load / Validate / Save (``None`` hides it, quiet-when-OK). Unlike
    ``workbench`` it is never recomputed on a timer.

    The ▶ ``primary`` (optional) is a normal ``PrimaryFlow`` whose ``audit``/``apply`` run
    off-thread on the dict ``snapshot()`` produces from the LIVE controller/widgets; the
    shared ``busy`` gate disables the ▶ + secondaries during a Save; ``after`` (e.g. a
    re-validate) fires once the flow finishes. ``build_body``'s ``controller`` is exposed
    as ``host._controller`` (the feature's own test/drive seam bag).

    Returns the body widget (wrap in ``W.scroll_body`` + ``W.Workspace``). ``host`` seams:
    ``_verdict`` / ``_set_verdict(state)`` / ``_controller`` / ``_busy`` and, with a primary,
    ``_run_primary``."""
    if any(getattr(a, "kind", "default") == "primary" for a in secondary):
        raise ValueError("an editor's only accent primary is the ▶ flow: none in `secondary`")

    busy = busy if busy is not None else {"on": False}
    host = QWidget()
    root = QVBoxLayout(host)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(T.sp("path"))

    # ── verdict: PUSH band (hidden until the feature calls _set_verdict) ────────────────
    vslot = W.VerdictSlot(chip_slots=chip_slots)
    root.addWidget(vslot)
    host._verdict = vslot
    host._set_verdict = vslot.set                   # push seam — never auto-recomputed

    # ── the editable body: built ONCE, mounted directly (no RefreshRegion) ──────────────
    widget, controller = build_body(ctx, host)
    host._controller = controller
    root.addWidget(widget)

    # ── the ▶ primary flow (reads the LIVE snapshot; after() = re-validate) ─────────────
    if primary is not None:
        def _run_primary():
            if busy.get("on"):                      # re-entrancy guard (a Save in flight)
                return
            run_primary_flow(ctx, host, primary, snapshot, after=after,
                             busy_gate=lambda on: busy.__setitem__("on", on))
        root.addWidget(_action_bar((action(primary.label, _run_primary,
                                           kind="primary", tip=primary.tip),)))
        host._run_primary = _run_primary            # test / drive_audit seam

    if secondary:
        root.addWidget(button_grid(secondary))

    mach_body = button_grid(machinery) if machinery else None
    root.addWidget(W.CollapsibleSection("Manage", mach_body))

    if exports:
        exp_host = QWidget()
        ev = QVBoxLayout(exp_host); ev.setContentsMargins(0, 0, 0, 0); ev.setSpacing(T.sp("sm"))
        for ea in exports:
            ev.addWidget(_export_slot(ctx, host, ea, snapshot))
        root.addWidget(W.CollapsibleSection("Export", exp_host))
    else:
        root.addWidget(W.CollapsibleSection("Export", None))

    host._busy = busy
    return host
