"""stm32_pins_tab.py — the 'STM32 Pins' tab: build the CubeMX database, view the
per-socket-position switch decision matrix, and generate the pinout authority.

Reads tools/stm32_db.py (DB + switch engine) and tools/stm32_authority.py
(Layer-B authority). Self-contained widget; the main window mounts it as the
third nav tab.
"""
from __future__ import annotations

import html
import os
from pathlib import Path

from PyQt5.QtCore import Qt, QFileSystemWatcher, pyqtSignal, QRectF
from PyQt5.QtGui import QColor, QBrush, QPainter, QPen
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
    QFileDialog, QMessageBox, QApplication, QSplitter, QTextEdit, QStackedWidget,
    QFrame,
)

# palette (mirrors the app's dark theme)
_PANEL, _CARD, _TXT, _MUT, _LINE = "#212124", "#26262b", "#ededf0", "#90909a", "#33333a"

import stm32_db as sdb
import stm32_authority as sauth

try:
    from LibraryManager import (lucide_icon, LUCIDE_NEUTRAL, LUCIDE_BLUE,
                                LUCIDE_GREEN, LUCIDE_AMBER)
    _HAVE_LUCIDE = True
except Exception:  # pragma: no cover
    _HAVE_LUCIDE = False
    LUCIDE_NEUTRAL = LUCIDE_BLUE = LUCIDE_GREEN = LUCIDE_AMBER = ""

    def lucide_icon(*_a, **_k):
        from PyQt5.QtGui import QIcon
        return QIcon()


_COLS = ["Pin", "Side", "Pin Name(s)", "Role Set", "Switch", "Why", "ADG714",
         "Destination", "Peripherals", "Breakout", "Tags", "Bootloader", "V(dd)"]

_BREAKOUT_COLOR = "#8f9fd4"   # extraction-access / debug-service breakout (periwinkle)

_SWITCH_COLOR = {
    sdb.SWITCH_MUST: "#d76b6b",
    sdb.SWITCH_OSC_OPTIONAL: "#cf9f57",
    sdb.SWITCH_NONE: "#5c646b",
}
_SWITCH_LABEL = {
    sdb.SWITCH_MUST: "must switch",
    sdb.SWITCH_OSC_OPTIONAL: "osc optional",
    sdb.SWITCH_NONE: "fixed",
}


def _counts(d: dict) -> str:
    return ", ".join(f"{k}×{v}" for k, v in d.items())


def _primary(d: dict) -> str:
    """Compact table-cell value: the leading (most common) name/role plus a +N badge
    for the remaining variants. The full ×count breakdown stays in the detail panel."""
    keys = list(d.keys())
    if not keys:
        return "—"
    return keys[0] if len(keys) == 1 else f"{keys[0]}  +{len(keys) - 1}"


def _numlist(nums, per: int = 6) -> str:
    """Socket numbers chunked into nowrap groups of `per`, so a long run reads as
    scannable blocks instead of one wrapped wall (HTML, for the detail panel). Groups
    are joined by a BREAKABLE space (+ nbsp for the gap) so Qt wraps between groups
    rather than force-breaking a number in half in the narrow panel."""
    if not nums:
        return "—"
    groups = [", ".join(str(n) for n in nums[i:i + per]) for i in range(0, len(nums), per)]
    return " &nbsp;&nbsp;".join(f"<span style='white-space:nowrap'>{g}</span>" for g in groups)


def _5v_suffix(five_v) -> str:
    """Compact 5V-tolerance token for the Tags cell."""
    if not five_v:
        return ""
    if five_v["tolerant"]:
        return " · 5V" + ("(!osc)" if five_v.get("caveat") == "osc-mode" else "")
    if any(five_v["by_family"].values()):
        return " · 5V*part-dep"
    return " · 3V3-only"


def _tag_summary(tags: dict) -> str:
    out = []
    if tags.get("is_debug"):
        out.append("DEBUG:" + "/".join(tags.get("debug_role", [])))
    if tags.get("is_boot"):
        out.append("BOOT")
    if tags.get("is_clock"):
        out.append("CLK")
    if tags.get("is_core_power"):
        out.append("VCAP")
    if tags.get("is_analog_supply"):
        out.append("VDDA/VREF")
    if tags.get("is_trace"):
        out.append("TRACE")
    return " · ".join(out)


def _esc(v) -> str:
    return html.escape(str(v))


def _fmt_rng(r, unit="V") -> str:
    return f"{r[0]}–{r[1]} {unit}" if r else "—"


def _pin_detail_html(p: dict) -> str:
    """Full detail for one socket position (pure — unit-testable)."""
    fv = p.get("five_v")
    if fv is None:
        fvt = "n/a (non-GPIO)"
    elif fv["tolerant"]:
        fvt = "5V-tolerant" + (" (except in osc mode)" if fv.get("caveat") == "osc-mode" else "")
    elif any(fv["by_family"].values()):
        fam = ", ".join(f"{k.replace('STM32', '')}={'5V' if v else '3V3'}"
                        for k, v in fv["by_family"].items())
        fvt = f"part-dependent — {fam}"
    else:
        fvt = "3.3V-only"
    bk = p.get("breakout", {})
    bnets = ", ".join(bk.get("service_nets", [])) or "—"
    adg = p["assignment"].get("adg714")
    adg_t = None
    if adg:
        s_pin, d_pin = sauth.ADG714_SWITCH_PINS[adg["channel"]]
        adg_t = f"cell {adg['cell']} · SW{adg['channel']} ({s_pin}/{d_pin})"
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or "—"
    el = p.get("electrical", {}) or {}
    why = sauth.switch_rationale(p)
    rows = [
        ("Name(s)", _counts(p["pin_names"])),
        ("Roles", _counts(p["role_set"])),
        ("Switch", _SWITCH_LABEL.get(p['switch_class'], p['switch_class'])),
    ]
    if why:
        rows.append(("Why", why))
    if adg_t:
        rows.append(("ADG714", adg_t))
    rows.append(("Destination", dest))
    rows += [
        ("Breakout", bnets + (" · TRACE" if bk.get("trace") else "")),
        ("Via", bk.get("via", "—")),
        ("Tags", _tag_summary(p["tags"]) or "—"),
        ("5V", fvt),
        ("Bootloader", ", ".join(p["tags"].get("bootloader_periph", [])) or "—"),
        ("Peripherals", ", ".join(p.get("peripherals", [])) or "—"),
        ("VDD", _fmt_rng(el.get("vdd_range_v"))),
    ]
    body = "".join(
        f"<tr><td style='color:{_MUT};padding-right:8px;vertical-align:top'>{k}</td>"
        f"<td>{_esc(v)}</td></tr>" for k, v in rows)
    return (f"<h3 style='margin:2px 0'>Pin {p['position']} "
            f"<span style='color:{_MUT}'>({p.get('side', '')})</span></h3>"
            f"<table>{body}</table>")


def _summary_html(a: dict) -> str:
    """Package summary card: rollup + electrical + card materials (pure)."""
    r = a["rollup"]
    ea = a.get("extraction_access", {})
    el = a.get("electrical", {})
    cm = a.get("card_materials", {})
    cats = sauth.category_lists(a)

    def _row(label, color, nums):
        return (f"<tr><td style='color:{color};white-space:nowrap;vertical-align:top;"
                f"padding:2px 12px 2px 0'>{label} ({len(nums)})</td>"
                f"<td style='padding:2px 0'>{_numlist(nums)}</td></tr>")

    lists_html = (
        "<p><b>Pin lists (socket #):</b></p><table cellspacing='0'>"
        + _row("Must-switch", _SWITCH_COLOR[sdb.SWITCH_MUST], cats["must_switch"])
        + _row("Osc-optional", _SWITCH_COLOR[sdb.SWITCH_OSC_OPTIONAL], cats["osc_optional"])
        + _row("Breakout", _BREAKOUT_COLOR, cats["breakout"])
        + _row("5V all-parts", _MUT, cats["five_v_all_parts"])
        + _row("Never 5V", _MUT, cats["five_v_never"])
        + "</table>")
    items = "".join(
        f"<tr><td style='text-align:right;padding-right:6px'>{i['qty']}×</td>"
        f"<td>{_esc(i['part'])}</td>"
        f"<td style='color:{_MUT};padding-left:8px'>{_esc(i['role'])}</td></tr>"
        for i in cm.get("items", []))
    return (
        f"<h3 style='margin:2px 0'>{a['package']} — {a['manifest']['part_count']} parts</h3>"
        f"<p><b>Switch:</b> {r['must_switch_count']} must-switch; "
        f"{r['osc_optional_count']} osc-optional; {r['fixed_count']} fixed</p>"
        f"<p><b>Breakout:</b> {ea.get('service_breakout_count', 0)} service · "
        f"{len(ea.get('debug_positions', []))} debug · {len(ea.get('trace_positions', []))} trace</p>"
        + lists_html +
        f"<p><b>Electrical:</b> I/O ±{el.get('max_io_current_ma', '?')} mA · "
        f"inj ±{el.get('injection_current_ma', '?')} mA<br>"
        f"VDD {_fmt_rng(el.get('vdd_range_v'))} · VDDA {_fmt_rng(el.get('vdda_range_v'))} · "
        f"VBAT {_fmt_rng(el.get('vbat_range_v'))} · VREF+ {_fmt_rng(el.get('vref_range_v'))}<br>"
        f"VCAP required: <b>{el.get('vcap_required')}</b></p>"
        f"<p><b>Card materials (passive BOM):</b></p><table>{items}</table>"
        f"<p style='color:{_MUT}'>{_esc(cm.get('note', ''))}</p>")


def _default_vault_authority_dir():
    """The vault's generated-authority folder, if the Brain vault is present."""
    brain = Path.home() / "Documents" / "Obsidian" / "Brain"
    return (brain / "Wiki" / "Datasets" / "STM32 Pinout Authority") if brain.is_dir() else None


# ── QFP pin-map geometry (pure — shared by the Qt widget AND the SVG export, so
#    the live widget and any preview render pixel-for-pixel identically) ──────
def pin_map_geometry(positions: list, w: float, h: float, margin: float = 46) -> dict:
    """Lay socket pins on a centered QFP body. Returns {body:(x,y,w,h),
    pins:[{pos, side, rect:(x,y,w,h), sw, breakout, name}]}. Pin 1 starts top-left
    and numbers counter-clockwise: left (top→bottom), bottom (L→R), right (bottom
    →top), top (R→L) — the standard LQFP order."""
    by = {p["position"]: p for p in positions}
    nums = sorted(by)
    n = len(nums)
    if not n:
        return {"body": (0, 0, 0, 0), "pins": []}
    per = max(1, n // 4)
    span = min(w, h) - 2 * margin
    body = span * 0.62
    plen = span * 0.10
    cx, cy = w / 2, h / 2
    bl, bt = cx - body / 2, cy - body / 2
    br, bb = cx + body / 2, cy + body / 2
    pitch = body / per
    pw = pitch * 0.60
    pins = []
    for idx, pos in enumerate(nums):
        p = by[pos]
        if idx < per:                                    # left, top→bottom
            y = bt + (idx) * pitch + (pitch - pw) / 2
            rect, side = (bl - plen, y, plen, pw), "L"
        elif idx < 2 * per:                              # bottom, left→right
            x = bl + (idx - per) * pitch + (pitch - pw) / 2
            rect, side = (x, bb, pw, plen), "B"
        elif idx < 3 * per:                              # right, bottom→top
            y = bb - (idx - 2 * per) * pitch - (pitch + pw) / 2
            rect, side = (br, y, plen, pw), "R"
        else:                                            # top, right→left
            x = br - (idx - 3 * per) * pitch - (pitch + pw) / 2
            rect, side = (x, bt - plen, pw, plen), "T"
        bk = p.get("breakout", {})
        pins.append({
            "pos": pos, "side": side, "rect": tuple(round(v, 2) for v in rect),
            "sw": p["switch_class"],
            "breakout": bool(bk.get("service_nets") or bk.get("trace")),
            "name": next(iter(p["pin_names"]), ""),
        })
    return {"body": tuple(round(v, 2) for v in (bl, bt, body, body)), "pins": pins}


def pin_map_svg(authority: dict, w: int = 460, h: int = 460, selected=None) -> str:
    """SVG render of the pin map (same geometry the widget paints) — for preview
    and 'export pin map'."""
    g = pin_map_geometry(authority["positions"], w, h)
    bl, bt, bw, bh = g["body"]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
         f'font-family="Inter,Segoe UI,Arial,sans-serif"><rect width="{w}" height="{h}" fill="{_PANEL}"/>',
         f'<rect x="{bl}" y="{bt}" width="{bw}" height="{bh}" rx="8" fill="#1c1c1f" '
         f'stroke="{_LINE}" stroke-width="1.5"/>',
         f'<text x="{bl+bw/2}" y="{bt+bh/2}" fill="{_MUT}" text-anchor="middle" '
         f'font-size="12">{html.escape(authority["package"])}</text>']
    for pin in g["pins"]:
        x, y, pwd, ph = pin["rect"]
        col = _SWITCH_COLOR.get(pin["sw"], "#5c646b")
        s.append(f'<rect x="{x}" y="{y}" width="{pwd}" height="{ph}" rx="2" fill="{col}"/>')
        if pin["breakout"]:
            s.append(f'<rect x="{x-1.5}" y="{y-1.5}" width="{pwd+3}" height="{ph+3}" rx="3" '
                     f'fill="none" stroke="{_BREAKOUT_COLOR}" stroke-width="2"/>')
        if pin["pos"] == selected:
            s.append(f'<rect x="{x-3}" y="{y-3}" width="{pwd+6}" height="{ph+6}" rx="4" '
                     f'fill="none" stroke="#ffffff" stroke-width="2"/>')
    s.append("</svg>")
    return "".join(s)


class _NumItem(QTableWidgetItem):
    """Table item that sorts by its numeric UserRole, so the Pin column orders
    1, 2, … 10, … 64 rather than lexicographically (1, 10, 11, … 2, …)."""
    def __lt__(self, other):
        try:
            return int(self.data(Qt.UserRole)) < int(other.data(Qt.UserRole))
        except (TypeError, ValueError):
            return super().__lt__(other)


class PinMapWidget(QWidget):
    """QFP pin-map: paints the socket with pins coloured by switch class (violet
    ring = breakout) via the shared pin_map_geometry; click → pinClicked(pos)."""
    pinClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.authority = None
        self.selected = None
        self.highlight = set()
        self.setMinimumSize(380, 380)

    def set_authority(self, a):
        self.authority = a
        self.selected = None
        self.highlight = set()
        self.update()

    def set_selected(self, pos):
        self.selected = pos
        self.update()

    def set_highlight(self, positions):
        self.highlight = set(positions or [])
        self.update()

    def _geom(self):
        if not self.authority:
            return None
        return pin_map_geometry(self.authority["positions"], self.width(), self.height())

    def paintEvent(self, _ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.fillRect(self.rect(), QColor(_PANEL))
        g = self._geom()
        if not g or not g["pins"]:
            qp.setPen(QColor(_MUT))
            qp.drawText(self.rect(), Qt.AlignCenter, "Build the database to see the pin map")
            return
        bl, bt, bw, bh = g["body"]
        qp.setPen(QPen(QColor(_LINE), 1.5))
        qp.setBrush(QColor("#1c1c1f"))
        qp.drawRoundedRect(QRectF(bl, bt, bw, bh), 8, 8)
        qp.setPen(QColor(_MUT))
        qp.drawText(QRectF(bl, bt, bw, bh), Qt.AlignCenter, self.authority["package"])
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            qp.setPen(Qt.NoPen)
            qp.setBrush(QColor(_SWITCH_COLOR.get(pin["sw"], "#5c646b")))
            qp.drawRect(QRectF(x, y, pw, ph))
            if pin["breakout"]:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(_BREAKOUT_COLOR), 2))
                qp.drawRect(QRectF(x - 1.5, y - 1.5, pw + 3, ph + 3))
            if pin["pos"] in self.highlight:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor("#5fadad"), 2.5))
                qp.drawRect(QRectF(x - 3.5, y - 3.5, pw + 7, ph + 7))
            if pin["pos"] == self.selected:
                qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor("#ffffff"), 2))
                qp.drawRect(QRectF(x - 3, y - 3, pw + 6, ph + 6))

    def mousePressEvent(self, ev):
        g = self._geom()
        if not g:
            return
        px, py = ev.x(), ev.y()
        for pin in g["pins"]:
            x, y, pw, ph = pin["rect"]
            if x - 3 <= px <= x + pw + 3 and y - 3 <= py <= y + ph + 3:
                self.selected = pin["pos"]
                self.update()
                self.pinClicked.emit(pin["pos"])
                return


class _StatCard(QFrame):
    """Compact dashboard stat card: title, big value, sub-line, coloured left bar."""
    def __init__(self, title, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet(f"#statCard{{background:{_CARD};border-radius:10px;"
                           f"border-left:4px solid {accent};}}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(1)
        self._t = QLabel(title)
        self._t.setStyleSheet(f"color:{_MUT};font-size:10px;font-weight:700;")
        self._b = QLabel("—")
        self._b.setStyleSheet(f"color:{_TXT};font-size:19px;font-weight:700;")
        self._s = QLabel("")
        self._s.setStyleSheet(f"color:{_MUT};font-size:11px;")
        for w in (self._t, self._b, self._s):
            lay.addWidget(w)

    def set(self, big, sub):
        self._b.setText(str(big))
        self._s.setText(str(sub))


class Stm32PinsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.db_path = sdb.default_db_path()
        self.source = sdb.default_cubemx_source()
        self.authority: dict | None = None
        self._building = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── controls ───────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("Package:"))
        self.pkg_combo = QComboBox()
        self.pkg_combo.addItems(["LQFP64", "LQFP100"])
        self.pkg_combo.currentTextChanged.connect(lambda p: self.load(p))
        bar.addWidget(self.pkg_combo)

        self.btn_build = QPushButton("Build Database")
        self.btn_build.setIcon(lucide_icon("wrench", LUCIDE_AMBER))
        self.btn_build.clicked.connect(self.build_database)
        bar.addWidget(self.btn_build)

        self.btn_gen = QPushButton("Generate Authority")
        self.btn_gen.setIcon(lucide_icon("save", LUCIDE_GREEN))
        self.btn_gen.clicked.connect(self.generate)
        bar.addWidget(self.btn_gen)

        self.btn_vault = QPushButton("Generate → Vault")
        self.btn_vault.setIcon(lucide_icon("file-up", LUCIDE_GREEN))
        self.btn_vault.setToolTip("Write the pinout authority into the Obsidian Brain vault")
        self.btn_vault.clicked.connect(self.generate_to_vault)
        bar.addWidget(self.btn_vault)
        bar.addStretch()

        bar.addWidget(QLabel("View:"))
        self.view_combo = QComboBox()
        self.view_combo.addItems(["Pin map", "Table", "Card BOM"])
        self.view_combo.currentIndexChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        bar.addWidget(self.view_combo)
        root.addLayout(bar)

        self.status = QLabel("")
        self.status.setObjectName("headerStatus")
        root.addWidget(self.status)
        self.rollup = QLabel("")
        self.rollup.setWordWrap(True)
        self.rollup.setTextFormat(Qt.RichText)
        root.addWidget(self.rollup)

        # ── stacked views: Pin map (dashboard) | Table | Card BOM ──
        self._sel_pos = None
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard_page())
        self.stack.addWidget(self._build_table_page())
        self.stack.addWidget(self._build_bom_page())
        root.addWidget(self.stack, 1)

        # ── live file-watch: reload when the DB is rebuilt on disk ──
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_db_changed)
        self._watcher.directoryChanged.connect(self._on_db_changed)
        self._arm_watch()

        self._load_if_ready()

    # ── page builders ───────────────────────────────────────────────
    def _build_dashboard_page(self):
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        col.setMaximumWidth(240)
        col.setMinimumWidth(206)
        self.sc_switch = _StatCard("SWITCH FABRIC", _SWITCH_COLOR[sdb.SWITCH_MUST])
        self.sc_break = _StatCard("BREAKOUT", _BREAKOUT_COLOR)
        self.sc_5v = _StatCard("5V-TOLERANCE", "#5fadad")
        self.sc_elec = _StatCard("ELECTRICAL", "#cf9f57")
        for c in (self.sc_switch, self.sc_break, self.sc_5v, self.sc_elec):
            cl.addWidget(c)
        cl.addStretch()
        lay.addWidget(col)
        self.pin_map = PinMapWidget()
        self.pin_map.pinClicked.connect(self._select)
        lay.addWidget(self.pin_map, 2)
        self.map_detail = QTextEdit()
        self.map_detail.setReadOnly(True)
        self.map_detail.setMinimumWidth(280)
        self.map_detail.setMaximumWidth(390)
        lay.addWidget(self.map_detail, 1)
        return page

    def _build_table_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Show:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Must switch", "Osc optional", "Fixed",
                                    "Breakout", "5V-tolerant", "Never 5V"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        frow.addWidget(self.filter_combo)
        frow.addWidget(QLabel("Peripheral:"))
        self.periph_combo = QComboBox()
        self.periph_combo.addItem("— any —")
        self.periph_combo.currentTextChanged.connect(self._on_peripheral)
        frow.addWidget(self.periph_combo)
        frow.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setMaximumWidth(200)
        self.search.textChanged.connect(self._apply_filter)
        frow.addWidget(self.search)
        frow.addStretch()
        for _label, _slot in [("Export CSV", self._export_csv),
                              ("Export MD", self._export_md),
                              ("Copy lists", self._copy_lists)]:
            _b = QPushButton(_label)
            _b.clicked.connect(_slot)
            frow.addWidget(_b)
        lay.addLayout(frow)
        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        for i in range(len(_COLS)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.itemSelectionChanged.connect(self._on_table_select)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(300)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        return page

    def _build_bom_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.bom_view = QTextEdit()
        self.bom_view.setReadOnly(True)
        lay.addWidget(self.bom_view)
        return page

    # ── selection + dashboard ───────────────────────────────────────
    def _select(self, pos):
        self._sel_pos = pos
        if pos is not None:
            self.pin_map.set_selected(pos)
        self._refresh_details()

    def _on_table_select(self):
        items = self.table.selectedItems()
        if items and self.authority:
            it0 = self.table.item(items[0].row(), 0)
            pos = it0.data(Qt.UserRole) if it0 else None
            if pos is not None:
                self._select(int(pos))

    def _refresh_details(self):
        if not self.authority:
            return
        if self._sel_pos is not None:
            p = next((x for x in self.authority["positions"]
                      if x["position"] == self._sel_pos), None)
            html_ = _pin_detail_html(p) if p else _summary_html(self.authority)
        else:
            html_ = _summary_html(self.authority)
        self.map_detail.setHtml(html_)
        self.detail.setHtml(html_)

    def _update_dashboard(self):
        a = self.authority
        if not a:
            return
        r, ea, el = a["rollup"], a["extraction_access"], a["electrical"]
        fv = el.get("five_v_positions", {})
        vdda = el.get("vdda_range_v")
        self.sc_switch.set(f"{r['must_switch_count']}",
                           f"must-switch · {r['osc_optional_count']} osc-opt · {r['fixed_count']} fixed")
        self.sc_break.set(f"{ea.get('service_breakout_count', 0)} nets",
                          f"{len(ea.get('debug_positions', []))} debug · "
                          f"{len(ea.get('trace_positions', []))} trace")
        self.sc_5v.set(f"{fv.get('tolerant_all_parts', 0)} 5V-safe",
                       f"{fv.get('family_dependent', 0)} part-dep · "
                       f"{fv.get('not_tolerant_any_part', 0)} never")
        self.sc_elec.set(f"±{el.get('max_io_current_ma', '?')} mA I/O",
                         f"VDDA {vdda[0]}–{vdda[1]} V · VCAP {el.get('vcap_required')}" if vdda else "")
        cm = a.get("card_materials", {})
        rows = "".join(
            f"<tr><td style='text-align:right;padding-right:8px'>{i['qty']}×</td>"
            f"<td>{_esc(i['part'])}</td>"
            f"<td style='color:{_MUT};padding-left:10px'>{_esc(i['role'])}</td></tr>"
            for i in cm.get("items", []))
        map_html = []
        for cell in sauth.adg714_cell_map(a):
            body = "".join(
                f"<tr><td style='color:{_MUT};padding-right:10px'>SW{sw['channel']}</td>"
                f"<td style='padding-right:10px'>{sw['s_pin']}/{sw['d_pin']}</td>"
                f"<td style='padding-right:10px'>"
                f"{'—' if sw['spare'] else 'pin ' + str(sw['position'])}</td>"
                f"<td style='padding-right:10px'>{_esc(sw['pin_name'])}</td>"
                f"<td style='color:{_BREAKOUT_COLOR}'>"
                f"{_esc('(spare)' if sw['spare'] else (sw['destination'] or '—'))}</td></tr>"
                for sw in cell["switches"])
            map_html.append(
                f"<p style='margin:8px 0 2px'><b>Cell {cell['cell']}</b> "
                f"<span style='color:{_MUT}'>{cell['symbol']} · {cell['footprint']}</span></p>"
                f"<table>{body}</table>")
        self.bom_view.setHtml(
            f"<h3>{a['package']} — switch-fabric map (ADG714 cell → socket pin)</h3>"
            + "".join(map_html)
            + f"<h3 style='margin-top:14px'>Plug-in card passive BOM</h3><table>{rows}</table>"
            f"<p style='color:{_MUT}'>{_esc(cm.get('note', ''))}</p>")

    # ── data ───────────────────────────────────────────────────────
    def _load_if_ready(self):
        if self.db_path.exists():
            self.load(self.pkg_combo.currentText())
        else:
            src = self.source if self.source else "not found"
            self.status.setText(f"No database yet. CubeMX source: {src}. Click 'Build Database'.")

    def _pick_source(self):
        d = QFileDialog.getExistingDirectory(self, "Select the CubeMX 'mcu' XML folder",
                                             str(self.source or ""))
        return d or None

    def build_database(self):
        src = self.source or self._pick_source()
        if not src:
            return
        self.status.setText("Building database from CubeMX XML…")
        self._building = True                       # suppress the file-watcher mid-build
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            res = sdb.build_database(src, self.db_path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self._building = False
            QMessageBox.warning(self, "Build Database", f"Build failed:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._building = False
        self._arm_watch()
        self.source = src
        lq = ", ".join(f"{k}={v}" for k, v in sorted(res.packages.items()) if k.startswith("LQFP"))
        self.status.setText(f"Built {res.mcus} STM32F MCUs, {res.pins} pins, {res.roles} roles "
                            f"from {src}  —  {lq}")
        self.load(self.pkg_combo.currentText())

    def load(self, package: str):
        if not self.db_path.exists():
            return
        conn = sdb.connect(self.db_path)
        try:
            self.authority = sauth.build(conn, package)
        except Exception as e:
            QMessageBox.warning(self, "Load", f"Could not read the database:\n{e}")
            return
        finally:
            conn.close()
        self._sel_pos = None
        self._populate_peripherals()
        self._populate()
        self.pin_map.set_authority(self.authority)
        self._update_dashboard()
        self._refresh_details()

    def _populate(self):
        a = self.authority
        if not a:
            return
        r = a["rollup"]
        ea = a.get("extraction_access", {})
        el = a.get("electrical", {})
        io, inj = el.get("max_io_current_ma"), el.get("injection_current_ma")
        vdda = el.get("vdda_range_v") or el.get("vdd_range_v")
        fv = el.get("five_v_positions", {})
        lab = f"color:{_MUT};font-weight:600"
        line1 = (f"<b>{a['package']}</b> · {a['manifest']['part_count']} parts · "
                 f"{r['positions_total']} positions")
        line2 = (f"<span style='{lab}'>Switch</span> "
                 f"must {r['must_switch_count']} · osc {r['osc_optional_count']} · "
                 f"fixed {r['fixed_count']}")
        line3 = (f"<span style='{lab}'>Breakout</span> {ea.get('service_breakout_count', 0)} "
                 f"({len(ea.get('debug_positions', []))} debug · "
                 f"{len(ea.get('trace_positions', []))} trace)")
        parts = []
        if io and vdda:
            parts.append(f"I/O ±{io} mA · inj ±{inj} mA · VDDA {vdda[0]}–{vdda[1]} V")
            if fv:
                parts.append(f"5V-tol {fv.get('tolerant_all_parts', 0)} all / "
                             f"{fv.get('family_dependent', 0)} part-dep / "
                             f"{fv.get('not_tolerant_any_part', 0)} none")
        line4 = (f"<span style='{lab}'>Power</span> " + " · ".join(parts)) if parts else ""
        self.rollup.setText(line1 + "<br>" + line2 + "<br>" + line3
                            + (("<br>" + line4) if line4 else ""))

        rows = a["positions"]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, p in enumerate(rows):
            sc = p["switch_class"]
            adg = p["assignment"].get("adg714")
            if adg:
                s_pin, d_pin = sauth.ADG714_SWITCH_PINS[adg["channel"]]
                adg_txt = f"cell {adg['cell']} · SW{adg['channel']} ({s_pin}/{d_pin})"
            else:
                adg_txt = "—"
            dest = (p["assignment"].get("destination") or p["assignment"].get("net") or "—")
            bk = p.get("breakout", {})
            bnets = bk.get("service_nets", [])
            btxt = ", ".join(bnets)
            if bk.get("trace"):
                btxt = (btxt + " · TRACE") if btxt else "TRACE"
            cells = [
                str(p["position"]),                                    # 0 Pin
                p.get("side", ""),                                     # 1 Side
                _primary(p["pin_names"]),                              # 2 Name(s)
                _primary(p["role_set"]),                               # 3 Role Set
                _SWITCH_LABEL.get(sc, sc),                             # 4 Switch
                sauth.switch_rationale(p) or "—",                      # 5 Why
                adg_txt,                                               # 6 ADG714
                dest,                                                  # 7 Destination
                ", ".join(p.get("peripherals", [])) or "—",           # 8 Peripherals
                btxt or "—",                                           # 9 Breakout
                _tag_summary(p["tags"]) + _5v_suffix(p.get("five_v")),  # 10 Tags
                ", ".join(p["tags"].get("bootloader_periph", [])),     # 11 Bootloader
                (lambda e: f"{e['vdd_range_v'][0]}–{e['vdd_range_v'][1]}"
                 if e and e.get("vdd_range_v") else "")(p.get("electrical")),  # 12 V(dd)
            ]
            for c, text in enumerate(cells):
                it = _NumItem(text) if c == 0 else QTableWidgetItem(text)
                if c == 0:
                    it.setData(Qt.UserRole, p["position"])      # numeric sort + row->pin key
                elif c == 4:  # switch class — colour it
                    it.setForeground(QBrush(QColor(_SWITCH_COLOR.get(sc, "#5c646b"))))
                elif c == 9 and (bnets or bk.get("trace")):  # breakout — violet
                    it.setForeground(QBrush(QColor(_BREAKOUT_COLOR)))
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        self._apply_filter()
        self.table.clearSelection()

    def _apply_filter(self):
        if not self.authority:
            return
        want = self.filter_combo.currentText()
        q = self.search.text().strip().lower()
        periph = self.periph_combo.currentText()
        periph = None if periph in ("", "— any —") else periph
        want_class = {
            "Must switch": sdb.SWITCH_MUST,
            "Osc optional": sdb.SWITCH_OSC_OPTIONAL,
            "Fixed": sdb.SWITCH_NONE,
        }.get(want)
        by_pos = {p["position"]: p for p in self.authority["positions"]}
        for row in range(self.table.rowCount()):
            it0 = self.table.item(row, 0)
            p = by_pos.get(it0.data(Qt.UserRole)) if it0 else None
            if p is None:
                continue
            fv = p.get("five_v")
            hide = False
            if want_class is not None and p["switch_class"] != want_class:
                hide = True
            elif want == "Breakout" and not p.get("breakout", {}).get("service_nets"):
                hide = True
            elif want == "5V-tolerant" and not (fv and fv["tolerant"]):
                hide = True
            elif want == "Never 5V" and not (fv and not any(fv["by_family"].values())):
                hide = True
            if periph and periph not in p.get("peripherals", []):
                hide = True
            if q and q not in " ".join(str(v) for v in (
                    p["position"], p["pin_names"], p["role_set"],
                    p["tags"].get("bootloader_periph", []), _tag_summary(p["tags"]),
                    sauth.switch_rationale(p),
                    p.get("breakout", {}).get("service_nets", []),
                    p.get("peripherals", []))).lower():
                hide = True
            self.table.setRowHidden(row, hide)

    def _on_peripheral(self, _name=None):
        if not self.authority:
            return
        name = self.periph_combo.currentText()
        if name in ("", "— any —"):
            self.pin_map.set_highlight(set())
        else:
            self.pin_map.set_highlight({p["position"] for p in self.authority["positions"]
                                        if name in p.get("peripherals", [])})
        self._apply_filter()

    def _populate_peripherals(self):
        combo = self.periph_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— any —")
        combo.addItems(sorted({x for p in self.authority["positions"]
                               for x in p.get("peripherals", [])}))
        combo.blockSignals(False)

    def _export_csv(self):
        self._export("csv", sauth.to_csv, "pins")

    def _export_md(self):
        self._export("md", sauth.to_markdown, "authority")

    def _export(self, ext, fn, stem):
        if not self.authority:
            return
        pkg = self.authority["package"]
        path, _sel = QFileDialog.getSaveFileName(
            self, f"Export {ext.upper()}", f"{stem}_{pkg}.{ext}", f"*.{ext}")
        if not path:
            return
        Path(path).write_text(fn(self.authority), encoding="utf-8", newline="\n")
        self.status.setText(f"Exported {Path(path).name}")

    def _copy_lists(self):
        if not self.authority:
            return
        cats = sauth.category_lists(self.authority)
        lines = [f"{self.authority['package']} pin lists (socket #):"]
        for key, lab in [("must_switch", "Must-switch"), ("osc_optional", "Osc-optional"),
                         ("fixed", "Fixed"), ("breakout", "Breakout"),
                         ("five_v_all_parts", "5V all-parts"), ("five_v_never", "Never 5V")]:
            nums = cats[key]
            lines.append(f"{lab} ({len(nums)}): " + (", ".join(map(str, nums)) or "—"))
        QApplication.clipboard().setText("\n".join(lines))
        self.status.setText("Copied pin lists to clipboard")

    def generate(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate", "Build the database first.")
            return
        out = QFileDialog.getExistingDirectory(self, "Choose output folder for the pinout authority")
        if not out:
            return
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, __import__("pathlib").Path(out))
                       for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate", f"Generate failed:\n{e}")
            return
        finally:
            conn.close()
        files = [f for w in written for f in w["files"]]
        self.status.setText(f"Wrote {len(files)} files to {out}: " + ", ".join(files))
        try:
            os.startfile(out)  # noqa: S606
        except Exception:
            pass

    # ── detail panel ────────────────────────────────────────────────

    def generate_to_vault(self):
        if not self.db_path.exists():
            QMessageBox.information(self, "Generate → Vault", "Build the database first.")
            return
        vdir = _default_vault_authority_dir()
        if vdir is None:
            out = QFileDialog.getExistingDirectory(self, "Brain vault not found — choose an output folder")
            if not out:
                return
            vdir = Path(out)
        conn = sdb.connect(self.db_path)
        try:
            written = [sauth.write_authority(conn, pkg, vdir) for pkg in ("LQFP64", "LQFP100")]
        except Exception as e:
            QMessageBox.warning(self, "Generate → Vault", f"Failed:\n{e}")
            return
        finally:
            conn.close()
        n = sum(len(w["files"]) for w in written)
        self.status.setText(f"Wrote {n} authority files into the vault: {vdir}")
        try:
            os.startfile(str(vdir))  # noqa: S606
        except Exception:
            pass

    # ── live file-watch ─────────────────────────────────────────────
    def _arm_watch(self):
        """(Re)watch the DB file + its dir. QFileSystemWatcher drops a path when
        the file is atomically replaced, so this is called again after a build."""
        for p in (str(self.db_path), str(self.db_path.parent)):
            if p not in self._watcher.files() + self._watcher.directories() and Path(p).exists():
                self._watcher.addPath(p)

    def _on_db_changed(self, _path=None):
        if self._building or not self.db_path.exists():
            return
        self._arm_watch()
        if self.authority:
            self.load(self.pkg_combo.currentText())
