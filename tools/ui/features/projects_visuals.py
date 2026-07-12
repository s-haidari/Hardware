"""Projects bespoke visuals — the container-scoped QSS composition and data-carrying
widgets for the Projects workspace, kept OUT of projects.py so the feature chrome
can route everything else through kit/widgets and pass the no-drift lint.

What lives here (and why it is bespoke, not a generic kit builder):
  * quiet_fields_qss() / netclass_table_qss() — CONTAINER-scoped stylesheets that
    restyle many child controls at once (borderless spin/line-edit fields that go
    transparent until hover/focus; the sticky-header net-class table). A generic
    per-widget builder can't express "style every QAbstractSpinBox inside this
    grid" — this is legitimate container QSS, applied once and never per row.
  * nc_name_cell()   — the net-class name cell: a DATA-coloured swatch (the class's
    own colour, not a theme token) + a click-to-pick colour dialog. The swatch hue
    is user data, so it can't come from a token role.
  * fabfacts_qss() + fab_key/fab_val/fab_len — the locked fab-fact grid, styled once
    by an object-name container QSS so rebuilding it on every profile switch never
    grows the restyle registry (mirrors the FIX 7 leak guard in bench/git).
  * field_label()    — a quiet secondary (txt2) field label used across the panels.

Fonts come from T.scale_font(role) at the call sites; only the container QSS and the
data-swatch colour are bespoke here.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
                            QStackedWidget, QFrame)

from .. import theme as T
from .. import widgets as W


# ── quiet field label (txt2), the standalone one used outside the table ──────
def field_label(text: str) -> QLabel:
    """A quiet secondary (txt2) field label — not a chip, not an eyebrow. Bespoke
    colour role (txt2) styled per-widget; built once per panel, not per rebuild."""
    lab = QLabel(text)
    lab.setFont(T.scale_font("detail_key"))
    W.register_restyle(lambda: lab.setStyleSheet(f"color:{T.t('txt2')};background:transparent;"), lab)
    return lab


# ── borderless editable fields (container-scoped) ────────────────────────────
def quiet_fields_qss() -> str:
    """Borderless editable fields: transparent until hover/focus. Kept out of the
    per-widget restyle registry by scoping it to the container (table / grid)."""
    txt1, ctl, txt3 = T.t('txt1'), T.t('ctl'), T.t('txt3')
    rc = T.RADIUS_CONTROL
    return (
        f"QAbstractSpinBox{{background:transparent;border:none;color:{txt1};padding:1px 4px;}}"
        f"QAbstractSpinBox:hover{{background:{ctl};border-radius:{rc}px;}}"
        f"QAbstractSpinBox:focus{{background:{ctl};border:1px solid {txt3};border-radius:{rc}px;}}"
        f"QLineEdit{{background:transparent;border:none;color:{txt1};padding:2px 6px;}}"
        f"QLineEdit:hover{{background:{ctl};border-radius:{rc}px;}}"
        f"QLineEdit:focus{{background:{ctl};border:1px solid {txt3};border-radius:{rc}px;}}")


def apply_quiet_fields(container: QWidget) -> None:
    """Style a container's editable child fields borderlessly, and re-apply on theme
    change. One call replaces the setStyleSheet + register_restyle pair."""
    container.setStyleSheet(quiet_fields_qss())
    W.register_restyle(lambda: container.setStyleSheet(quiet_fields_qss()), container)


# ── net-class table (container-scoped, sticky header) ────────────────────────
def netclass_table_qss() -> str:
    """The sticky-header net-class table QSS, container-scoped so rebuilding rows
    never registers a per-row restyle. Includes the quiet-field rules plus the
    name/dash label colour roles (QLabel#nc_name / QLabel#nc_dash)."""
    base, txt2, div = T.t('base'), T.t('txt2'), T.t('divider')
    return (
        f"QTableWidget{{background:transparent;border:none;gridline-color:transparent;color:{T.t('txt1')};}}"
        f"QHeaderView::section{{background:{base};color:{txt2};border:none;"
        f"border-bottom:1px solid {div};padding:6px 8px;font-weight:600;}}"
        f"QTableCornerButton::section{{background:{base};border:none;}}"
        # Net-class name/dash labels retint here (container-scoped) so rebuilding the
        # table never registers a per-row restyle. See nc_name_cell.
        f"QLabel#nc_name{{color:{T.t('txt1')};background:transparent;}}"
        f"QLabel#nc_dash{{color:{T.t('txt3')};background:transparent;}}"
        # PCB-14: a diff-pair spin sitting at 0 means "no diff pair" — dim it (txt3) so
        # the column reads as absent, not as a real 0.0 value. The objectName is set on
        # the spin only while it holds 0 (see projects.py), and this container rule keeps
        # the tint out of the per-row restyle registry. Placed AFTER quiet_fields_qss so
        # it overrides the shared txt1 spin colour.
        + quiet_fields_qss()
        + f"QAbstractSpinBox#nc_dp_zero{{color:{T.t('txt3')};}}")


def apply_netclass_table(tbl: QWidget) -> None:
    """Style the net-class table and re-apply on theme change (registers once)."""
    W.register_restyle(lambda: tbl.setStyleSheet(netclass_table_qss()), tbl)
    tbl.setStyleSheet(netclass_table_qss())


def nc_name_cell(nc, on_pick=None, on_rename=None) -> QWidget:
    """Net-class name: a category-color swatch + the class name in mono. When
    `on_pick` is given the swatch is clickable (PCB-07) — it opens a color picker,
    writes the chosen color onto the class, and calls on_pick(nc).

    When `on_rename` is given the name is editable in place (PCB, New Net Class
    rename): double-clicking the label swaps it for an inline QLineEdit; committing
    (Enter / focus-out) calls on_rename(nc, new_name). The callback owns validation
    and the actual rename — it returns the accepted name (or None to reject), and the
    cell renders whatever it returns. Escape cancels back to the current name.

    The name label is styled through the table's container QSS (QLabel#nc_name in
    netclass_table_qss, registered ONCE) rather than a per-row register_restyle —
    the table is rebuilt on every profile switch / New / Delete, and a per-row
    restyle would grow W._RESTYLERS unbounded and pin every superseded label alive.
    The swatch fill is the class's OWN colour (user data), not a theme token, so it
    is set directly here."""
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(10, 0, 8, 0); h.setSpacing(8)
    dot = QLabel(); dot.setFixedSize(12, 12)

    def paint():
        dot.setStyleSheet(f"background:{nc.color};border-radius:3px;")   # swatch = data
    paint()
    if on_pick:
        dot.setCursor(Qt.PointingHandCursor)
        dot.setToolTip("Click to change this net class's color")

        def click(_e):
            from PyQt5.QtWidgets import QColorDialog
            from PyQt5.QtGui import QColor
            c = QColorDialog.getColor(QColor(nc.color or "#888888"), w, "Net Class Color")
            if c.isValid():
                nc.color = c.name()
                paint(); on_pick(nc)
        dot.mousePressEvent = click

    name = QLabel(nc.name); name.setObjectName("nc_name"); name.setFont(T.scale_font("value"))
    h.addWidget(dot)

    if on_rename is None:
        h.addWidget(name); h.addStretch(1)
        return w

    # ── inline-editable name (PCB New Net Class rename) ──────────────────────
    # A QStackedWidget flips between the label and an editor without changing the
    # cell's size, so the table layout never jumps. The editor inherits the table's
    # container QSS (a plain QLineEdit) — no per-row restyle.
    stack = QStackedWidget()
    editor = QLineEdit(nc.name); editor.setFont(T.scale_font("value"))
    stack.addWidget(name); stack.addWidget(editor)
    stack.setCurrentWidget(name)
    name.setToolTip("Double-click to rename this net class")

    # editing = we are mid-edit (editor shown); guards the whole commit against the
    # editingFinished re-fire that swapping back to the label provokes.
    editing = {"on": False}

    def start_edit(_e=None):
        editor.setText(nc.name)
        editing["on"] = True
        stack.setCurrentWidget(editor)
        editor.setFocus(Qt.MouseFocusReason); editor.selectAll()

    def finish_edit():
        # editingFinished fires on Enter AND on the focus-out that setCurrentWidget(name)
        # itself causes, so it re-enters. Only the first call while actually editing
        # commits; clear the flag FIRST so the swap-induced re-fire is a no-op.
        if not editing["on"]:
            return
        editing["on"] = False
        proposed = editor.text().strip()
        accepted = on_rename(nc, proposed) if proposed and proposed != nc.name else None
        final = accepted if accepted else nc.name
        name.setText(final)
        stack.setCurrentWidget(name)

    def cancel_edit():
        if not editing["on"]:
            return
        editing["on"] = False
        editor.setText(nc.name)
        stack.setCurrentWidget(name)

    name.mouseDoubleClickEvent = start_edit
    editor.editingFinished.connect(finish_edit)

    # Escape cancels; editingFinished already covers Enter + focus-out.
    def key_press(ev):
        if ev.key() == Qt.Key_Escape:
            cancel_edit(); return
        QLineEdit.keyPressEvent(editor, ev)
    editor.keyPressEvent = key_press

    h.addWidget(stack); h.addStretch(1)
    # Expose the pieces so the panel/tests can drive the rename without a real cursor.
    w._nc_name_label = name
    w._nc_name_editor = editor
    w._nc_start_edit = start_edit
    w._nc_commit_edit = finish_edit
    return w


def nc_dash() -> QLabel:
    """The dim em-dash placeholder for a net-class column with no value (design-rules
    §4). Styled by the table container QSS (QLabel#nc_dash), never a per-row restyle."""
    dash = QLabel("—"); dash.setObjectName("nc_dash")
    dash.setFont(T.scale_font("value")); dash.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    dash.setContentsMargins(0, 0, 8, 0)
    return dash


def nc_cell_font(widget: QWidget) -> QWidget:
    """Give a net-class table cell field the compact mono weight so the column reads
    as editable data (design-rules §11 / FIX 8). Colour comes from the container QSS."""
    widget.setFont(T.scale_font("value"))
    return widget


# ── fabrication-fact grid (container object-name QSS) ────────────────────────
def apply_fabfacts_style(fab_holder: QWidget) -> None:
    """Style the fab-fact grid by object name (QLabel#fabkey/#fabval/#fabdim), applied
    once at panel build. rebuild_fabfacts then builds plain labels that inherit this
    styling WITHOUT calling register_restyle — so W._RESTYLERS never grows across
    profile switches (see FIX 7)."""
    def style():
        fab_holder.setStyleSheet(
            f"QLabel#fabkey{{color:{T.t('txt2')};background:transparent;}}"
            f"QLabel#fabval{{color:{T.t('txt1')};background:transparent;}}"
            f"QLabel#fabdim{{color:{T.t('txt3')};background:transparent;}}")
    W.register_restyle(style, fab_holder)
    style()


def fab_key(text: str) -> QLabel:
    lab = QLabel(text); lab.setObjectName("fabkey"); lab.setFont(T.scale_font("detail_key")); return lab


def fab_val(text: str, mono: bool = False) -> QLabel:
    lab = QLabel(text); lab.setObjectName("fabval")
    lab.setFont(T.scale_font("value") if mono else T.scale_font("detail_key")); return lab


# ── ERC pin-conflict matrix (container object-name QSS, built once) ──────────
# A cell's severity is encoded in its objectName so cycling a level (set objectName
# + unpolish/polish) never touches the per-widget restyle registry — the whole grid
# carries ONE registry entry, mirroring the fab-fact / net-class container pattern.
_PMC_NAME = {0: "pmc_ok", 1: "pmc_warn", 2: "pmc_err"}
_PMC_GLYPH = {0: "·", 1: "!", 2: "✕"}   # · ok · ! warning · ✕ error


def pinmap_grid_qss() -> str:
    """Container-scoped QSS for the ERC pin-conflict grid: the axis header labels plus
    the three severity states of a cell button, keyed by objectName."""
    rc = T.RADIUS_CONTROL
    return (
        f"QLabel#pmHdr{{color:{T.t('txt2')};background:transparent;}}"
        f"QPushButton#pmc_ok,QPushButton#pmc_warn,QPushButton#pmc_err{{"
        f"border:1px solid {T.t('stroke')};border-radius:{rc}px;font-weight:700;padding:0;}}"
        f"QPushButton#pmc_ok{{background:{T.t('field')};color:{T.t('txt3')};}}"
        f"QPushButton#pmc_warn{{background:{T.t('warn_bg')};color:{T.t('warn')};}}"
        f"QPushButton#pmc_err{{background:{T.t('err_bg')};color:{T.t('err')};}}")


def apply_pinmap_grid(container: QWidget) -> None:
    """Style the pin-map grid once and re-apply on theme change (a single registry
    entry for the whole matrix, so rebuilding cells never grows W._RESTYLERS)."""
    def style():
        container.setStyleSheet(pinmap_grid_qss())
    W.register_restyle(style, container)
    style()


def pinmap_cell_apply(btn, sev: int) -> None:
    """Set a pin-map cell button's severity look: objectName drives the colour (via the
    container QSS above), the glyph shows the level. Re-polish so the new objectName
    styling lands without rebuilding the container sheet."""
    sev = int(sev) if int(sev) in _PMC_NAME else 0
    btn.setObjectName(_PMC_NAME[sev])
    btn.setText(_PMC_GLYPH[sev])
    btn.style().unpolish(btn); btn.style().polish(btn)


# ── consolidated per-board quantity chart (bespoke: width-scaled data bars) ───
def board_qty_chart(per_board: dict, *, bar_px: int = 180) -> QWidget:
    """A quiet horizontal bar chart of a consolidated line's {board: qty} split — each
    board a row of [name] [bar scaled to the busiest board] [qty]. The bar length is DATA
    (qty / max qty), so the fill colour is a fixed data role (info), not a chrome token;
    the track and labels ride theme tokens and re-tint on theme change. Bespoke painting
    kept here (this module is the Projects visuals allowlist), out of projects.py. Rows
    stack vertically and never scroll sideways (design-rules §10)."""
    w = QWidget()
    col = QVBoxLayout(w); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(6)
    items = list((per_board or {}).items())
    top = max((int(q) for _b, q in items), default=0) or 1
    bars = []                                            # (track, fill) for the restyler
    for board, qty in items:
        q = int(qty)
        row = QHBoxLayout(); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
        name = QLabel(str(board)); name.setFont(T.scale_font("detail_key"))
        name.setMinimumWidth(96); name.setMaximumWidth(160)
        name.setToolTip(str(board))
        # The bar: a fixed-width track holding a fill sized to q/top of it.
        track = QFrame(); track.setFixedSize(bar_px, 10)
        fill = QFrame(track)
        fill_w = max(2, round(bar_px * q / top)) if q > 0 else 0
        fill.setGeometry(0, 0, fill_w, 10)
        bars.append((track, fill))
        val = QLabel(str(q)); val.setFont(T.mono_font(9)); val.setMinimumWidth(40)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(name); row.addWidget(track); row.addWidget(val); row.addStretch(1)
        rw = QWidget(); rw.setLayout(row)
        col.addWidget(rw)

    def paint():
        rc = T.RADIUS_CONTROL
        for track, fill in bars:
            try:
                track.setStyleSheet(f"background:{T.t('tok')};border-radius:{rc}px;")
                fill.setStyleSheet(f"background:{T.t('info')};border-radius:{rc}px;")
            except RuntimeError:                          # bar deleted by a rebuild
                pass
    paint()
    W.register_restyle(paint, w)
    return w
