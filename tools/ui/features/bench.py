"""Bench — the STM32 package-authority workspace.

Panels (all list-driven, added to the Workspace):
  Authority     real pin map (painted from pin_map_geometry) + buildability
                verdict (fabric_drc + current_budget) + rollup + pin inspector
  Profiles      the per-package profile ladder (baseline -> least supported)
  Part Resolver resolve_part(mpn) -> per-chip pin map
  Card Outputs  card_bom + current_budget rails + the vault export list

Everything reads the hardened logic layer directly; nothing is hard-coded.
"""
from __future__ import annotations

from typing import List, Optional

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QSizePolicy, QComboBox, QGridLayout,
                             QFileDialog, QAbstractItemView)

from .. import widgets as W
from .. import kit
from .. import feature as F
from ..prose import plural
from ..util import clear_layout, run_populate, _headless

from .bench_visuals import (
    PinMap, legend, connection_diagram, stat, pin_header, pin_meta, profile_badge,
    _pin_category, _is_5v, _resolved_cat, _CAT_FROM_NET,
)

import stm32_db as db
import stm32_authority as sauth
import stm32_pins_tab as pins   # pure helpers: pin_map_geometry, _pin_detail_rows


# ── shared package state ─────────────────────────────────────────────────────
_ALL_FAMILIES = "All Families"


class BenchState:
    def __init__(self):
        self.conn = None
        self.packages: List[str] = []
        self.package: Optional[str] = None
        self.error: Optional[str] = None
        self._cache = {}
        try:
            self.conn = db.connect(db.default_db_path())
            # only the packages the bench can actually build (LQFP has pin geometry);
            # the DB's BGA/WLCSP/QFN packages have no map and just noise up the picker.
            self.packages = db.list_buildable_packages(self.conn)
            self.package = "LQFP64" if "LQFP64" in self.packages else (
                self.packages[0] if self.packages else None)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
        self._refreshers = []
        self._profile_tiers = {}        # package -> {sig: [mpn,...]} over ALL parts (memoised)
        self.family = None              # BENCH-06: None == all supported families
        self.goto_resolver = None       # set by BenchFeature: navigate to a resolved chip
        self.goto_authority_pin = None  # set by BenchFeature: jump to a pin on the map

    def authority(self):
        if self.package is None:
            return None
        if self.package not in self._cache:
            self._cache[self.package] = sauth.build(self.conn, self.package)
        return self._cache[self.package]

    def families(self):
        """The STM32F families this package supports, from the authority manifest."""
        au = self.authority()
        fams = ((au or {}).get("manifest") or {}).get("supported_families") or []
        return sorted({str(f) for f in fams})

    def _fire(self):
        for fn in list(self._refreshers):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def set_package(self, pkg: str):
        self.package = pkg
        self.family = None              # a new package resets the family filter
        self._fire()

    def set_family(self, fam):
        self.family = None if fam in (None, "", _ALL_FAMILIES) else fam
        self._fire()

    def on_change(self, fn):
        self._refreshers.append(fn)


# ── Authority panel ──────────────────────────────────────────────────────────
def _authority_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    outer = QVBoxLayout(root)
    outer.setContentsMargins(24, 16, 24, 24)
    outer.setSpacing(14)

    if state.error or state.package is None:
        outer.addWidget(kit.state(
            "error", "Database Unavailable", glyph="alert",
            sub=state.error or "No packages found in the STM32 database."))
        return root

    verdict_holder = QVBoxLayout(); verdict_holder.setContentsMargins(0, 0, 0, 0)
    outer.addLayout(verdict_holder)

    grid = QHBoxLayout(); grid.setSpacing(16)
    # left: map card
    map_card = W.Card(pad=20)
    # smaller default map (scroll-wheel zoom enlarges it) so the inspector gets more width
    pin_map = PinMap(on_select=lambda pos: _show_pin(pos), base=380)
    # Zoom/pan are secondary controls (the map is the focal element, §5): a quiet hint
    # on the left, then a tight −/+ segmented pair plus a ghost Reset on the right. The
    # map itself is the pan/zoom viewport now — wheel zooms toward the cursor and drag
    # pans (owner v2.11: the old scroll-area wrapper could do neither).
    zrow = QHBoxLayout(); zrow.setSpacing(6)
    zrow.addWidget(W.body("Scroll to zoom · drag to pan", dim=True)); zrow.addStretch(1)
    zrow.addWidget(W.eyebrow("Zoom"))
    zseg = W.Segmented(["−", "+"], tip="Zoom the pin map out / in")
    zseg.on_change(lambda t: pin_map.zoom_by(1 / 1.15 if t == "−" else 1.15))
    zrow.addWidget(zseg)
    zrow.addWidget(W.btn("Reset", "ghost", "Reset the zoom and recentre the map",
                         lambda: pin_map.reset_view()))
    map_card.body.addLayout(zrow)
    map_card.body.addWidget(pin_map, 0, Qt.AlignHCenter)
    map_card.body.addWidget(legend())
    strip = QHBoxLayout(); strip.setSpacing(24); strip.setContentsMargins(0, 12, 0, 0)
    strip.addStretch(1)
    stat_cells = {}
    for key, label in (("positions_total", "Positions"), ("must_switch_count", "Must-Switch"),
                       ("cells_as_built", "Switch Cells"), ("channel_count", "Channels"),
                       ("osc_optional_count", "Oscillator Optional")):
        c = stat("0", label); strip.addWidget(c); stat_cells[key] = c
    strip.addStretch(1)
    map_card.body.addLayout(strip)
    map_card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    grid.addWidget(map_card, 0)

    # right: inspector
    insp_card = W.Card(pad=16)
    insp_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    grid.addWidget(insp_card, 1)
    outer.addLayout(grid)

    # Below the map + inspector, the Overview is the long scrollable HOME: the detailed
    # All-Pins table and the Profiles ladder, absorbed from their old standalone tabs
    # (owner v2.11 "Overview should absorb All Pins + Profiles"). Each is a reusable
    # section builder; the Overview owns the section headers. No trailing stretch — a
    # scrollable home lets its sections flow. (The per-pin Connection Diagram moved to
    # the Analysis tab, repainted there as a real diagram.)
    outer.addWidget(_allpins_section(ctx, state))
    outer.addWidget(W.section_header("Profiles"))
    outer.addWidget(_profiles_section(ctx, state))

    def _clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                _clear_layout(it.layout())

    def _show_pin(pos):
        _clear_layout(insp_card.body)
        authority = state.authority()
        p = next((x for x in authority["positions"] if x["position"] == pos), None)
        if p is None:
            insp_card.body.addWidget(kit.state(
                "empty", "Select a Pin on the Map", glyph="bench",
                sub="Click any pad to inspect its role, category and connection path."))
            insp_card.body.addStretch(1)
            return
        name = next(iter(p["pin_names"]), f"Pin {pos}")
        insp_card.body.addWidget(pin_header(
            name, pos, p.get("switch_class") == "must_switch"))
        # category + side
        cat = _pin_category(p)
        side = {"L": "Left", "R": "Right", "T": "Top", "B": "Bottom"}.get(pin_map.side_of(pos), "")
        insp_card.body.addWidget(pin_meta(cat, side))
        # detail rows — a REAL definition list. A multi-part value (joined with " · ")
        # becomes one dl row per part (key shown once), not a faked, space-padded column.
        insp_card.body.addWidget(W.eyebrow("Detail"))
        rows = []
        for label, value in pins._pin_detail_rows(p):
            parts = [s.strip() for s in str(value).split(" · ") if s.strip()] or [""]
            for i, part in enumerate(parts):
                rows.append((label if i == 0 else "", W.body(part, wrap=True)))
        insp_card.body.addWidget(W.dl(rows, key_width=128))
        insp_card.body.addStretch(1)

    def refresh():
        _clear_layout(verdict_holder)
        try:
            authority = state.authority()
        except Exception as e:  # noqa: BLE001 - unsupported package build (e.g. BGA)
            pin_map.set_authority(None)
            verdict_holder.addWidget(W.Verdict(
                f"{state.package} Not Buildable", str(e), "warn", plain=False))
            return
        pin_map.set_authority(authority)
        r = authority["rollup"]
        for key, cell in stat_cells.items():
            lbl = cell.findChild(QLabel)
            if lbl:
                lbl.setText(str(r.get(key, 0)))
        # verdict (verdict_holder was cleared at the top of refresh())
        findings = sauth.fabric_drc(authority)
        ok = sauth.fabric_drc_ok(findings)
        passed = sum(1 for f in findings if f.get("ok"))
        budget = sauth.current_budget(authority)
        budget_ok = not budget.get("findings")
        # BENCH-14: the always-green "Buildable / all rules pass" banner was noise.
        # Surface the verdict ONLY when something needs attention; stay quiet on OK.
        if not (ok and budget_ok):
            chips = [("Fabric DRC", f"{passed} / {len(findings)}", "ok" if ok else "err"),
                     ("Current Budget", "OK" if budget_ok else "Check", "ok" if budget_ok else "warn")]
            verdict_holder.addWidget(W.Verdict(
                "Not Buildable" if not ok else "Check Budget",
                "Structural rules need attention" if not ok else "Review the current budget",
                "err" if not ok else "warn", chips))
        # default selection
        first = authority["positions"][0]["position"] if authority["positions"] else None
        if first is not None:
            pin_map.select(first); _show_pin(first)

    # NB: package-change propagation is handled centrally by ws.rebuild_all (wired in
    # BenchFeature.build), which rebuilds this panel wholesale — so we must NOT also
    # register `refresh` as an on_change here, or stale closures would accumulate.
    refresh()
    # on_owned: this panel is rebuilt wholesale by ws.rebuild_all on every package AND
    # family switch, so a bare on() would leave the old closure (capturing dropped
    # widgets) on the bus forever. Tie the subscription to root's lifetime. (BENCH-leak.)
    ctx.bus.on_owned("bench.jump_pin", lambda pos: (pin_map.select(pos), _show_pin(pos)), owner=root)
    return root


# 5 V tolerance is a GPIO/analog attribute. resolve_part attaches the package-union
# per-family `five_v_tolerant` to EVERY pin (stm32_authority.py:1917) with no gate on the
# pin's own electrical_class, so VBAT/VSS/VDD/NRST/VCAP/BOOT0 all inherit True and would
# otherwise print "5 V" — contradicting "exact silicon". Only pins whose electrical_class
# is an actual I/O pin (GPIO/analog) can be 5 V tolerant; power/ground/reset/boot/vcap/nc
# are structurally not, whatever the union says. (BENCH-05.)
_FIVE_V_CLASSES = {"io"}


def _resolved_is_five_v(pn) -> bool:
    """Whether a resolved pin is genuinely 5 V tolerant: it must be an I/O pin
    (electrical_class 'io') AND carry a tolerant family value. Power/ground/reset/boot/
    VCAP/NC pins are structurally not 5 V tolerant, whatever the package-union value the
    resolver copied onto them says. (electrical_class missing -> trust the raw value.)"""
    ec = pn.get("electrical_class")
    if ec is not None and ec not in _FIVE_V_CLASSES:
        return False
    return bool(pn.get("five_v_tolerant"))


def _resolved_five_v_label(pn) -> str:
    """The 5 V column text for one resolved pin: "5 V" when genuinely tolerant, else a
    null em-dash — never "5 V" on a rail, ground, reset, boot, VCAP or NC pin."""
    return "5 V" if _resolved_is_five_v(pn) else "—"


# ── Part Resolver panel (real resolve_part) ──────────────────────────────────
def _resolver_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    # every ordering part number in this package — the browsable set
    try:
        part_nos = [r[0] for r in state.conn.execute(
            "SELECT DISTINCT part_number FROM mcu WHERE package_name = ? ORDER BY part_number",
            (state.package,))]
    except Exception:  # noqa: BLE001
        part_nos = []

    head = QHBoxLayout(); head.setSpacing(10)
    head.addWidget(W.eyebrow(f"MCU Pinout Viewer   {state.package}" if state.package
                             else "MCU Pinout Viewer"))
    head.addWidget(W.body("Exact silicon, not the package union.", dim=True))
    head.addStretch(1)
    lay.addLayout(head)

    split = QHBoxLayout(); split.setSpacing(16)

    # ── left: a filterable, browsable list of every part in the package ──────────
    # (owner v2.11: "give a searchable LIST to pick from", not an exact-name search).
    left = QVBoxLayout(); left.setSpacing(8)
    filt = QLineEdit(); filt.setMinimumHeight(32); filt.setFixedWidth(300)
    filt.setClearButtonEnabled(True)
    filt.setPlaceholderText(f"Filter {len(part_nos)} parts…" if part_nos
                            else "No parts in this package")
    filt.setToolTip("Type to narrow the list; click a part to paint its exact silicon pinout")
    left.addWidget(filt)
    plist = W.browse_list(fixed_width=300)
    plist.setMinimumHeight(360)
    for pn in part_nos:
        plist.addItem(str(pn))
    left.addWidget(plist, 1)
    count = W.body("", dim=True)
    left.addWidget(count)
    left_w = QWidget(); left_w.setLayout(left)
    split.addWidget(left_w, 0, Qt.AlignTop)

    # ── right: the resolved pinout (map + per-pin table) ─────────────────────────
    result_holder = QVBoxLayout(); result_holder.setSpacing(10)
    right_w = QWidget(); right_w.setLayout(result_holder)
    split.addWidget(right_w, 1)
    lay.addLayout(split, 1)

    def _update_count():
        vis = sum(1 for i in range(plist.count()) if not plist.item(i).isHidden())
        count.setText(f"{vis} of {plural(len(part_nos), 'part')} shown"
                      if part_nos else "No parts in this package")

    def _apply_filter(text):
        q = text.strip().lower()
        for i in range(plist.count()):
            it = plist.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())
        _update_count()
    filt.textChanged.connect(_apply_filter)

    def _seed_empty():
        # First-open guidance beside the list (BENCH-empty) — an intentional empty state,
        # not a blank pane; cleared on the first resolve.
        clear_layout(result_holder)
        result_holder.addWidget(kit.state(
            "empty", "Resolve a Part to View Its Exact Pinout", glyph="bench",
            sub="Pick an ordering part number from the list to paint this chip's real silicon."))
        result_holder.addStretch(1)

    def resolve_mpn(mpn):
        clear_layout(result_holder)
        mpn = (mpn or "").strip()
        if not mpn:
            _seed_empty()
            return
        try:
            res = sauth.resolve_part(state.conn, mpn)
        except Exception as e:  # noqa: BLE001
            result_holder.addWidget(W.body(f"Could not resolve {mpn}: {e}", dim=True))
            return
        if not res:
            result_holder.addWidget(W.body(f"No match for {mpn}.", dim=True))
            return
        result_holder.addWidget(W.eyebrow(f"{res['part']}   {res['package']}   {len(res['pins'])} Pins"))
        rsplit = QHBoxLayout(); rsplit.setSpacing(16)
        # left: the resolved pinout, painted from the datasheet-derived pins. The map is
        # its own pan/zoom viewport (wheel zooms to the cursor, drag pans); a −/+ pair +
        # ghost Reset, a legend under it, and on_select highlights the matching table row.
        map_card = W.Card(pad=16)
        pm = PinMap(on_select=lambda pos: _select_pin_row(pos), base=340)
        geo = [{"position": p["pin"], "switch_class": "fixed",
                "pin_names": {p.get("name", ""): 1}, "breakout": {}} for p in res["pins"]]
        pm.set_positions(geo, {p["pin"]: {"cat": _resolved_cat(p), "fivev": _resolved_is_five_v(p)}
                               for p in res["pins"]})
        zrow = QHBoxLayout(); zrow.setSpacing(6)
        zrow.addWidget(W.body("Scroll to zoom · drag to pan", dim=True)); zrow.addStretch(1)
        zrow.addWidget(W.eyebrow("Zoom"))
        zseg = W.Segmented(["−", "+"], tip="Zoom the pin map out / in")
        zseg.on_change(lambda t, m=pm: m.zoom_by(1 / 1.15 if t == "−" else 1.15))
        zrow.addWidget(zseg)
        zrow.addWidget(W.btn("Reset", "ghost", "Reset the zoom and recentre the map",
                             lambda m=pm: m.reset_view()))
        map_card.body.addLayout(zrow)
        map_card.body.addWidget(pm, 0, Qt.AlignHCenter)
        map_card.body.addWidget(legend())
        map_card.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        rsplit.addWidget(map_card, 0, Qt.AlignTop)
        # right: the clean per-pin table (the datasheet pinout, remade)
        rows = []
        row_of_pin = {}
        for pn in res["pins"][:400]:
            action = pn.get("action", "").replace("_", " ").title()
            act_w = W.tag("Rail Conflict", "err") if pn.get("action") == "rail_conflict" else W.body(action)
            ch = ", ".join(f"Cell {s['cell']} Channel {s['channel']}" for s in pn.get("close_switches", []))
            # 5 V-tolerance is an attribute, not a status: quiet dim text when tolerant,
            # a null em-dash when not — no per-row green dot (design-rules §6, the column
            # was a uniform wall of colour that carried no signal).
            fv_w = W.body(_resolved_five_v_label(pn), dim=True)
            dest = str(pn.get("dest") or "")
            row_of_pin[pn["pin"]] = len(rows)
            rows.append([str(pn["pin"]), W.body(str(pn.get("name", "")), mono=True), act_w,
                         W.body(dest or "—", dim=not dest, mono=bool(dest)),
                         W.body(ch or "—", dim=not ch, mono=bool(ch)), fv_w])
        tbl = W.data_table(["Pin", "Name", "Action", "Destination", "Close Switches", "5 V"], rows, stretch_col=3)

        def _select_pin_row(pos):
            # Map click -> scroll/highlight the matching table row, mirroring the learned
            # Overview map->inspector interaction. (BENCH-map.)
            r = row_of_pin.get(pos)
            if r is None:
                return
            tbl.selectRow(r)
            item = tbl.item(r, 0)
            if item is not None:
                tbl.scrollToItem(item, QAbstractItemView.PositionAtCenter)

        rsplit.addWidget(tbl, 1)
        result_holder.addLayout(rsplit, 1)

    # clicking / arrowing a list row resolves it (the list IS the picker — no separate
    # Resolve button). currentRowChanged fires for both mouse and keyboard selection.
    plist.currentRowChanged.connect(
        lambda i: resolve_mpn(plist.item(i).text()) if i >= 0 and plist.item(i) else None)

    def _select_and_resolve(mpn):
        # bus entry (a Profiles chip -> here): select + reveal the row, then resolve. If
        # setCurrentItem actually changes the row, currentRowChanged already resolves it;
        # otherwise (same row re-emitted, or the part is not in this package) resolve
        # directly so a repeat click still repaints.
        items = plist.findItems(str(mpn), Qt.MatchFixedString)
        if items:
            prev = plist.currentRow()
            plist.setCurrentItem(items[0])
            plist.scrollToItem(items[0], QAbstractItemView.PositionAtCenter)
            if plist.currentRow() != prev:
                return
        resolve_mpn(str(mpn))

    # on_owned: the resolver panel is rebuilt on every package/family switch, so its
    # bus handler must auto-unsubscribe when root is destroyed — a bare on() would
    # accumulate stale closures over deleted widgets. (BENCH-leak.)
    ctx.bus.on_owned("bench.resolve", lambda m: _select_and_resolve(m), owner=root)

    _update_count()
    _seed_empty()   # first-open guidance until a row is picked
    return root


def _budget_hazard_rails(budget: dict) -> set:
    """Rails the current-budget check flagged. Every hazard finding is a string that
    begins with the rail name followed by ':' (see stm32_authority.current_budget),
    so the rail is the token before the first colon. Only rails that are actual keys
    in budget['rails'] count, so a stray finding never mislabels a row."""
    rails = set(budget.get("rails", {}))
    hazards = set()
    for f in budget.get("findings", []) or []:
        rail = str(f).split(":", 1)[0].strip()
        if rail in rails:
            hazards.add(rail)
    return hazards


# ── Card Outputs panel (real card_bom + current_budget) ──────────────────────
def _outputs_panel(ctx, state: BenchState) -> QWidget:
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    if state.package is None:
        lay.addWidget(kit.state("empty", "No Package Loaded", glyph="bench")); return root
    pkg = state.package
    authority = state.authority()
    base = str(Path(ctx.cfg.get("RepoRoot") or "."))

    def save_text(title, default_name, filt, builder):
        if _headless():                      # offscreen drive / CI: no picker, no modal
            ctx.services.log(f"{title}: no file (headless).")
            return
        fn, _ = QFileDialog.getSaveFileName(root, title, str(Path(base) / default_name), filt)
        if not fn:
            return
        try:
            Path(fn).write_text(builder(authority), encoding="utf-8", newline="\n")
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Write failed: {e}")

    positions = authority["positions"]
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.subhead(f"Exports   {pkg}")); bar.addStretch(1)
    # Owner v2.11: "only a far-more-detailed All-Pins export is worth keeping" — so the
    # detailed 13-column pin CSV is the ONE accent primary. The bundle drops to a default
    # (secondary) button and the small single-file saves collapse into a "Save File ▾" menu
    # (progressive disclosure). Same _write_allpins_csv the Overview All-Pins button uses.
    save_menu = W.menu_button("Save File", [
        ("Card Bill Of Materials (CSV)", lambda: save_text(
            "Save Card Bill Of Materials", f"card_bom_{pkg}.csv", "CSV Files (*.csv)", sauth.to_card_bom_csv),
         "Write the card bill of materials as a CSV file"),
        ("KiCad Netlist", lambda: save_text(
            "Save KiCad Netlist", f"{pkg}_socket.net", "KiCad Netlist (*.net)", sauth.to_kicad_netlist),
         "Write the socket netlist in KiCad format"),
        ("Pin-Map SVG", lambda: save_text(
            "Save Pin-Map SVG", f"pinmap_{pkg}.svg", "SVG Files (*.svg)", pins.pin_map_svg),
         "Render the pin-map geometry to an SVG file"),
    ], kind="ghost", tip="Save one export file for this package")
    b_bundle = W.btn("Write Authority Bundle", "default",
                     "Write the full bundle (YAML, JSON, TSV, CSV, socket symbol) to a folder")
    b_allpins = W.btn("Export All Pins (CSV)", "primary",
                      "Write every socket position with full detail (13 columns) to a CSV file")
    bar.addWidget(save_menu); bar.addWidget(b_bundle); bar.addWidget(b_allpins)
    lay.addLayout(bar)
    lay.addWidget(W.body(
        f"The detailed All-Pins CSV is the focal export: all {len(positions)} socket positions "
        "with pin names, roles, peripherals, breakout, tags, supply range, rail conflicts and "
        "5 V tolerance. The bundle and single-file saves remain for KiCad hand-off.",
        dim=True, wrap=True))

    def export_allpins():
        if _headless():                      # offscreen drive / CI: no picker, no modal
            ctx.services.log("Export All Pins: no file (headless).")
            return
        fn, _ = QFileDialog.getSaveFileName(root, "Export All Pins",
                                            str(Path(base) / f"pins_{pkg}.csv"), "CSV Files (*.csv)")
        if not fn:
            return
        try:
            _write_allpins_csv(positions, fn)
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Export failed: {e}")

    b_allpins.clicked.connect(export_allpins)

    findings = sauth.validate_socket_symbol(authority)
    passed = sauth.validate_socket_symbol_ok(findings)
    chips = [(f["rule"].replace("_", " ").title(), "Pass" if f["ok"] else "Fail",
              "ok" if f["ok"] else "err") for f in findings]
    lay.addWidget(W.Verdict(
        "Pre-Write Checks",
        "The emitted socket symbol is placeable and routable in KiCad."
        if passed else "Resolve the failing checks before you write the bundle.",
        "ok" if passed else "err", chips))

    lay.addWidget(W.section_header(f"Card Bill Of Materials   {pkg}"))
    bom = sauth.card_bom(authority)
    rows = []
    for r in bom.get("rows", []):
        rows.append([W.body(str(r.get("refdes", "")), mono=True), W.body(str(r.get("mpn", "")), mono=True),
                     W.body(str(r.get("role", ""))), str(r.get("qty", ""))])
    lay.addWidget(W.data_table(["Reference", "Part Number", "Role", "Quantity"], rows, stretch_col=2))

    lay.addWidget(W.section_header("Current Budget   Rails"))
    budget = sauth.current_budget(authority)
    # Honest per-rail State: current_budget emits hazard findings each prefixed with the
    # rail name before the first ":" (e.g. "VTARGET: connector feed ..."). A rail named in
    # a finding is a real hazard ("Check"); every other rail passed the budget rules
    # ("OK"). This makes the State column carry varying signal instead of a decorative,
    # always-green tag the code could never invalidate (design-rules §6).
    hazard_rails = _budget_hazard_rails(budget)
    brows = []
    for rail, info in sorted(budget.get("rails", {}).items()):
        cap = info.get("input_capacity_ma") or 0
        cap_txt = f"{cap/1000:.2f} A" if cap >= 1000 else f"{cap} mA"
        state_w = W.tag("Check", "warn") if rail in hazard_rails else W.tag("OK", "ok")
        # net_label (dot + mono, no fill) — a filled net_token here washed the whole
        # stretched Rail column with a tint (design-rules §6: no surface hue).
        brows.append([W.net_label(rail, _CAT_FROM_NET.get(sauth._NET_CATEGORY.get(rail, "lane"), "lane")),
                      f"{info.get('direct_pins', 0)}", f"{info.get('switch_channels', 0)}",
                      cap_txt, state_w])
    lay.addWidget(W.data_table(["Rail", "Direct", "Switched", "Capacity", "State"], brows, stretch_col=0))
    lay.addStretch(1)

    def write_bundle():
        if _headless():                      # offscreen drive / CI: no picker, no modal
            ctx.services.log("Write Authority Bundle: no folder (headless).")
            return
        d = QFileDialog.getExistingDirectory(root, "Choose an output folder", base)
        if not d:
            return

        def job():
            c = db.connect(db.default_db_path())
            try:
                return sauth.write_authority(c, pkg, Path(d))
            finally:
                c.close()

        run_populate(ctx, job,
                     lambda r, ok2: ctx.services.log(
                         f"Wrote authority bundle for {pkg}." if ok2 else "Bundle write failed, see status."),
                     busy=f"Writing authority bundle for {pkg}...")

    b_bundle.clicked.connect(write_bundle)
    return root


# ── All Pins panel (every position across all parts in the package) ───────────
def _five_v_short(p) -> str:
    fv = p.get("five_v")
    if fv is None:
        return "Not Applicable"
    if fv.get("tolerant"):
        return "Yes"
    if any((fv.get("by_family") or {}).values()):
        return "Part-Dependent"
    return "No"


def _breakout_text(p) -> str:
    """The pin's breakout signal(s): service nets it fans out to, plus a Trace marker."""
    bk = p.get("breakout") or {}
    parts = [pins.expandNet(n) for n in (bk.get("service_nets") or [])]
    if bk.get("trace"):
        parts.append("Trace")
    return " · ".join(parts)


def _pin_row(p) -> list:
    """One flat row of a position's FULL detail (13 columns of real authority data),
    shared by the table and the CSV export. The on-screen table elides the wide cells to
    their column width; the CSV keeps the whole string. Every value is a plain string so
    csv.writer accepts the row."""
    dest = p["assignment"].get("destination") or p["assignment"].get("net") or ""
    cat = pins._CAT_WORD.get(sauth._NET_CATEGORY.get(dest, "lane"), "Card Lane")
    el = p.get("electrical") or {}
    conflicts = ", ".join(pins.expandNet(c.get("net", "")) for c in (p.get("rail_conflicts") or [])
                          if c.get("net"))
    return [str(p["position"]),
            str(p.get("side", "")).title(),
            pins._names(p["pin_names"]),
            pins._names(p["role_set"]),
            cat,
            pins._SWITCH_LABEL.get(p["switch_class"], p["switch_class"]),
            pins.expandNet(dest),
            ", ".join(str(x) for x in (p.get("peripherals") or [])),
            _breakout_text(p),
            pins._tag_summary(p.get("tags") or {}),
            pins._fmt_rng(el.get("vdd_range_v")),
            conflicts,
            _five_v_short(p)]


_PIN_COLS = ["Pin", "Side", "Pin Names", "Roles", "Category", "Switch Class", "Destination",
             "Peripherals", "Breakout", "Tags", "Supply (V)", "Rail Conflicts", "5 V Tolerant"]


def _write_allpins_csv(positions, path) -> None:
    """Write the detailed all-pins table (full 13 columns, complete peripheral lists) to a
    CSV — the focal Bench export. utf-8 + newline="" so the `·`/`→` glyphs survive Windows
    and rows don't double-space."""
    import csv, io
    buf = io.StringIO(); writer = csv.writer(buf); writer.writerow(_PIN_COLS)
    for p in positions:
        writer.writerow(_pin_row(p))
    Path(path).write_text(buf.getvalue(), encoding="utf-8", newline="")


def _allpins_section(ctx, state: BenchState) -> QWidget:
    """The detailed All-Pins table as a REUSABLE section (the Overview home embeds it and
    the standalone _allpins_panel wraps it): a section header, a description + inline
    Export-CSV primary, then the full 13-column table bounded so it scrolls inside its own
    height rather than making the page kilometres long."""
    w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
    if state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); return w
    pkg = state.package
    positions = state.authority()["positions"]
    lay.addWidget(W.section_header(f"All Pins   {pkg}   {len(positions)} Positions"))
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.body(
        "Every socket position across all supported STM32 parts in this package, with its "
        "functions, peripherals, breakout, tags, supply range and delivered net.", dim=True))
    bar.addStretch(1)
    b_csv = W.btn("Export All Pins (CSV)", "primary",
                  "Write every socket position with full detail to a CSV file")
    bar.addWidget(b_csv, 0, Qt.AlignVCenter)
    lay.addLayout(bar)
    # 13 columns is more than the width holds, so single-line rows + a horizontal
    # scrollbar keep EVERY column reachable (wrap=True would hide the h-scrollbar and clip
    # the right-hand columns). The full peripheral lists live in the CSV export.
    rows = [_pin_row(p) for p in positions]
    tbl = W.data_table(_PIN_COLS, rows, stretch_col=(2, 3, 7), mono_cols={0, 6, 11})
    tbl.setMinimumHeight(360); tbl.setMaximumHeight(560)
    tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    lay.addWidget(tbl)

    def export():
        if _headless():                      # offscreen drive / CI: no picker, no modal
            ctx.services.log("Export All Pins: no file (headless).")
            return
        base = str(Path(ctx.cfg.get("RepoRoot") or "."))
        fn, _ = QFileDialog.getSaveFileName(w, "Export All Pins", str(Path(base) / f"pins_{pkg}.csv"),
                                            "CSV Files (*.csv)")
        if not fn:
            return
        try:
            _write_allpins_csv(positions, fn)
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:  # noqa: BLE001
            ctx.services.log(f"Export failed: {e}")

    b_csv.clicked.connect(export)
    return w


def _allpins_panel(ctx, state: BenchState) -> QWidget:
    """Standalone page wrapper (kept for direct callers); the Overview embeds the section."""
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(12)
    if state.package is None:
        lay.addWidget(kit.state("empty", "No Package Loaded", glyph="bench")); return root
    lay.addWidget(_allpins_section(ctx, state))
    lay.addStretch(1)
    return root


# ── Profiles panel (real authority: one baseline fabric + minority divergences) ─
def _chip_grid(items, make, cols: int = 5) -> QWidget:
    """Uniform grid of chips: every column equal width so the chips stack evenly."""
    w = QWidget(); g = QGridLayout(w)
    g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(6); g.setVerticalSpacing(6)
    for i, it in enumerate(items):
        b = make(it)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        g.addWidget(b, i // cols, i % cols)
    for c in range(cols):
        g.setColumnStretch(c, 1)
    if not items:
        g.addWidget(W.body("None.", dim=True), 0, 0)
    return w


def _profiles_section(ctx, state: BenchState) -> QWidget:
    """The Profiles content as a REUSABLE section (the Overview home embeds it; the
    standalone _profiles_panel wraps it): the family filter, the baseline switch-fabric
    card, the switching-pin pills, the supported-families line, and the off-thread,
    memoised chips-by-profile grouping."""
    w = QWidget()
    lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)
    if state.error or state.package is None:
        lay.addWidget(W.body("No package loaded.", dim=True)); return w
    try:
        a = state.authority()
    except Exception as e:  # noqa: BLE001
        lay.addWidget(W.body(f"Authority unavailable: {e}", dim=True)); return w
    man = a.get("manifest", {}); roll = a.get("rollup", {})
    all_parts = man.get("supported_parts", []) or []   # every part (grouped once, family-independent)
    parts = list(all_parts)
    fams = man.get("supported_families", []) or []
    pkg = state.package

    # BENCH-06: the family filter is inherently a Profiles-only control (it narrows this
    # tab's chip list and nothing else), so it lives in this panel's body — NOT the global
    # header, where it would be visible on all five tabs yet silently no-op on four. The
    # combo lists the package's supported families (default: All). Selecting one fires
    # state.set_family -> ws.rebuild_all, which rebuilds THIS panel with state.family set,
    # re-reading it below to narrow `parts`.
    fam_row = QHBoxLayout(); fam_row.setSpacing(8)
    fam_row.addWidget(W.eyebrow("Family"))
    fam_combo = QComboBox(); fam_combo.setFixedWidth(160)
    fam_combo.addItem(_ALL_FAMILIES)
    fam_combo.addItems(state.families())
    fam_combo.setCurrentText(state.family if state.family else _ALL_FAMILIES)
    fam_combo.setToolTip("Filter the supported chips below to one STM32F family")
    fam_combo.currentTextChanged.connect(state.set_family)
    fam_row.addWidget(fam_combo); fam_row.addStretch(1)
    lay.addLayout(fam_row)

    # narrow to the chosen family (None = all).
    if state.family:
        parts = [p for p in parts if state.family.upper() in str(p).upper()]
        fams = [f for f in fams if state.family.upper() in str(f).upper()] or [state.family]

    fam_note = f"     ·     Family {state.family}" if state.family else ""
    meta = (f"{state.package}     ·     {len(parts)} Supported Parts"
            f"     ·     {len(fams)} Families{fam_note}")
    lay.addWidget(W.body(meta, dim=True))

    # baseline fabric
    card = W.Card(pad=16)
    card.body.addWidget(W.subhead("Baseline Switch Fabric"))
    card.body.addWidget(W.dl([
        ("Must-Switch Positions", W.body(str(roll.get("must_switch_count", 0)), mono=True)),
        ("Switch Channels", W.body(str(roll.get("channel_count", 0)), mono=True)),
        ("Switch Cells", W.body(str(roll.get("cells_as_built", 0)), mono=True)),
        ("Oscillator Optional", W.body(str(roll.get("osc_optional_count", 0)), mono=True)),
    ], key_width=200))
    lay.addWidget(card)

    # switching pins — smart pills that identify each pin (name + number); the Overview
    # connection diagram already carries the full path, so pills stay compact
    switch_pins = [p for p in a["positions"] if p.get("switch_class") == "must_switch"]
    lay.addWidget(W.section_header(f"Switching Pins ({len(switch_pins)})"))

    def pill_for(p):
        nm = next(iter(p["pin_names"]), f"Pin {p['position']}")
        pos = p["position"]
        return W.token_link(
            nm, lambda _n, k=pos: state.goto_authority_pin(k) if state.goto_authority_pin else None,
            tip=f"Pin {pos}: {nm}. Click to view it on the map.",
            cat=_pin_category(p), sub=f"Pin {pos}")
    lay.addWidget(_chip_grid(switch_pins, pill_for, cols=4))

    lay.addWidget(W.section_header("Supported Families"))
    lay.addWidget(W.body("      ".join(fams) if fams else "None", mono=True, wrap=True))

    # chips grouped by profile — computed off the GUI thread (a resolve per part)
    lay.addWidget(W.section_header("Chips by Profile"))
    prof_box = QVBoxLayout(); prof_box.setSpacing(12); lay.addLayout(prof_box)

    # BENCH-perf: grouping is package-authority-derived and family-INDEPENDENT — the
    # profile a part lands in is the same whether the family filter is set or not. So
    # group ALL parts exactly once per package (memoised on state), then in populate()
    # filter each tier's members down to the currently-shown parts. A family switch
    # (which fires rebuild_all) then reuses the cached grouping instead of re-running
    # ~53 resolve_part calls (each ~64 ms, ~3.7 s total) all over again.
    shown = set(parts)   # the family-narrowed set this rebuild renders

    def _group_all():
        """Group EVERY supported part in this package by its rail-conflict signature.
        resolve_part(conn, mpn) internally calls build(conn, package) — identical for
        every part in the package — so memoise build() for this pass and all resolves
        share one authority (was 53 rebuilds -> 1)."""
        conn = db.connect(db.default_db_path())
        try:
            memo = {}
            real_build = sauth.build

            def _cached_build(c, package):
                # Only memoise this worker's own connection; any other caller (e.g. the
                # GUI thread rebuilding state.authority() concurrently) falls straight
                # through to the real build, so the module patch is race-safe.
                if c is not conn:
                    return real_build(c, package)
                if package not in memo:
                    memo[package] = real_build(c, package)
                return memo[package]

            sauth.build = _cached_build
            try:
                tiers = {}
                for mpn in all_parts:
                    try:
                        r = sauth.resolve_part(conn, mpn)
                    except Exception:  # noqa: BLE001
                        r = None
                    confs = (r.get("rail_conflicts") or []) if r else []
                    sig = tuple(sorted((c.get("needs", ""), c.get("name", "")) for c in confs))
                    tiers.setdefault(sig, []).append(mpn)
                return tiers
            finally:
                sauth.build = real_build
        finally:
            conn.close()

    def compute():
        # Reuse the cached full grouping when it exists (a family-filter rebuild); only
        # the first open of a package pays the resolve cost.
        cached = state._profile_tiers.get(pkg)
        if cached is not None:
            return cached
        tiers = _group_all()
        state._profile_tiers[pkg] = tiers
        return tiers

    def populate(all_tiers, ok):
        clear_layout(prof_box)
        if not all_tiers:
            prof_box.addWidget(W.body("Could not group the chips.", dim=True)); return
        # Narrow each tier to the parts this rebuild shows (the family filter). A tier
        # that has no shown part after filtering is dropped.
        tiers = {}
        for sig, mpns in all_tiers.items():
            keep = [m for m in mpns if m in shown]
            if keep:
                tiers[sig] = keep
        if not tiers:
            prof_box.addWidget(W.body("No chips in the selected family.", dim=True)); return
        total = sum(len(v) for v in tiers.values())
        ordered = sorted(tiers.items(), key=lambda kv: (len(kv[0]), -len(kv[1])))
        for n, (sig, mpns) in enumerate(ordered, 1):
            pcard = W.Card(pad=16)
            head = QHBoxLayout(); head.setSpacing(10)
            head.addWidget(profile_badge(n))
            name = "Baseline" if not sig else "Needs " + ", ".join(f"{nm} ({nd})" for nd, nm in sig)
            head.addWidget(W.subhead(name))
            head.addWidget(W.tag("Fully Supported" if not sig else "Divergent", "ok" if not sig else "warn"))
            head.addStretch(1)
            pct = round(100 * len(mpns) / max(1, total))
            head.addWidget(W.body(f"{len(mpns)} Chips ({pct}%)", dim=True))
            pcard.body.addLayout(head)
            pcard.body.addWidget(_chip_grid(
                sorted(mpns),
                lambda m: W.token_link(str(m), lambda x: state.goto_resolver(x) if state.goto_resolver else None,
                                       tip="View this chip's pinout"),
                cols=5))
            prof_box.addWidget(pcard)

    # Only show the loading skeleton when we will actually compute (first open of a
    # package). A cached rebuild (family switch) populates synchronously, no flash.
    if state._profile_tiers.get(pkg) is None:
        prof_box.addWidget(W.skeleton_rows(3, 4))
    run_populate(ctx, compute, populate, busy="Grouping supported chips by profile...")
    return w


def _profiles_panel(ctx, state: BenchState) -> QWidget:
    """Standalone page wrapper (kept for direct callers/tests); the Overview embeds the
    section directly."""
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    if state.error or state.package is None:
        lay.addWidget(kit.state("empty", "No Package Loaded", glyph="bench")); return root
    lay.addWidget(_profiles_section(ctx, state))
    lay.addStretch(1)
    return root


# ── Analysis panel (read-only authority views + claim-file lint) ──────────────
def _analysis_panel(ctx, state: BenchState) -> QWidget:
    """Every forgotten authority read: category pin lists, the ADG714 cell map, the
    card passive materials, the full socket-connection table, and the claim-file drift
    gate. All derived read-only from the current authority (lint rebuilds per claim's
    own package)."""
    root = QWidget()
    lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(16)
    if state.error or state.package is None:
        lay.addWidget(kit.state("empty", "No Package Loaded", glyph="bench")); return root
    try:
        authority = state.authority()
    except Exception as e:  # noqa: BLE001 - unsupported package build (e.g. BGA)
        lay.addWidget(kit.state("error", "Authority Unavailable", glyph="alert",
                                sub=f"Could not build the authority: {e}")); return root
    pkg = state.package

    # Connection Diagram — the real painted socket→net signal path for a chosen pin,
    # moved here from the Overview (owner v2.11: "the Connection Diagram should live more
    # on the Analysis page" + "make it a real, clean diagram"). A pin picker drives it,
    # since Analysis has no map to click.
    lay.addWidget(W.section_header("Connection Diagram"))
    order = sorted(authority["positions"], key=lambda p: p["position"])
    cw = sauth.card_wiring(authority)
    cd_bar = QHBoxLayout(); cd_bar.setSpacing(8)
    cd_bar.addWidget(W.eyebrow("Pin"))
    pin_combo = QComboBox(); pin_combo.setFixedWidth(240)
    pin_combo.setToolTip("Choose a socket pin to draw its path from the MCU to the delivered net")
    for p in order:
        nm = next(iter(p["pin_names"]), "")
        pin_combo.addItem(f"Pin {p['position']}" + (f" · {nm}" if nm else ""), p["position"])
    cd_bar.addWidget(pin_combo)
    cd_bar.addWidget(W.body("The socket-to-net path for the selected pin.", dim=True))
    cd_bar.addStretch(1)
    lay.addLayout(cd_bar)
    cd_holder = QVBoxLayout(); cd_holder.setSpacing(6); lay.addLayout(cd_holder)

    def _paint_diagram(pos):
        clear_layout(cd_holder)
        if pos is None:
            return
        try:
            cd_holder.addWidget(connection_diagram(pins._pin_chain(authority, pos, cw)))
        except Exception as e:  # noqa: BLE001
            cd_holder.addWidget(W.body(f"Connection diagram unavailable: {e}", dim=True))
    pin_combo.currentIndexChanged.connect(lambda _i: _paint_diagram(pin_combo.currentData()))
    # default to the first must-switch pin (the most interesting path), else the first pin
    _default = next((p["position"] for p in order if p.get("switch_class") == "must_switch"),
                    order[0]["position"] if order else None)
    if _default is not None:
        pin_combo.blockSignals(True)
        _di = pin_combo.findData(_default)
        if _di >= 0:
            pin_combo.setCurrentIndex(_di)
        pin_combo.blockSignals(False)
    _paint_diagram(pin_combo.currentData())

    # Category pin lists — explicit socket-pin-number lists per category.
    lay.addWidget(W.section_header(f"Category Pin Lists   {pkg}"))
    cats = sauth.category_lists(authority)
    crows = []
    for key, nums in cats.items():
        label = key.replace("_", " ").title()
        txt = ", ".join(str(n) for n in nums) if nums else "—"
        crows.append([W.body(label), str(len(nums)), W.body(txt, dim=not nums, mono=bool(nums), wrap=True)])
    lay.addWidget(W.data_table(["Category", "Count", "Positions"], crows, stretch_col=2, wrap=True, fit_rows=True))

    # Card passive materials — the worst-cased per-package passive BOM.
    lay.addWidget(W.section_header(f"Card Passive Materials   {pkg}"))
    mat = sauth.card_materials(authority)
    vcap = ", ".join(mat.get("vcap_required_families") or []) or "None"
    lay.addWidget(W.dl([
        ("ADG714 Cells", W.body(str(mat.get("adg714_cells", 0)), mono=True)),
        ("VCAP Families", W.body(vcap)),
        ("100nF Decoupling", W.body(str(mat.get("decoupling_100nf_count") or "—"), mono=True)),
    ], key_width=170))
    mrows = []
    for it in mat.get("items", []):
        mrows.append([W.body(str(it.get("ref", "")), mono=True), W.body(str(it.get("part", ""))),
                      str(it.get("qty", "")), W.body(str(it.get("role", ""))),
                      W.body(str(it.get("note", "")), dim=True, wrap=True)])
    lay.addWidget(W.data_table(["Reference", "Part", "Qty", "Role", "Note"], mrows, stretch_col=4, wrap=True, fit_rows=True))

    # ADG714 cell map — the must-switch fabric as octal-switch instances.
    cells = sauth.adg714_cell_map(authority)
    lay.addWidget(W.section_header(f"ADG714 Cell Map   {len(cells)} Cells"))
    if not cells:
        lay.addWidget(W.body("No must-switch fabric for this package.", dim=True))
    for c in cells:
        card = W.Card(pad=16)
        head = QHBoxLayout(); head.setSpacing(10)
        head.addWidget(W.subhead(f"Cell {c['cell']}   {c['symbol']}"))
        head.addWidget(W.body(c.get("footprint", ""), dim=True)); head.addStretch(1)
        card.body.addLayout(head)
        srows = []
        for sw in c["switches"]:
            used = not sw.get("spare")
            pos = sw.get("position")
            srows.append([str(sw["channel"]), W.body(str(sw.get("s_pin", "")), mono=True),
                          W.body(str(sw.get("d_pin", "")), mono=True),
                          W.body(str(pos) if pos is not None else "—", dim=not used, mono=used),
                          W.body(sw.get("pin_name") or "—", dim=not used, mono=used),
                          W.body(str(sw.get("destination") or "—"), dim=not used, mono=used),
                          W.tag("In Use", "ok") if used else W.tag("Spare", "mut")])
        card.body.addWidget(W.data_table(
            ["Channel", "S Pin", "D Pin", "Position", "Pin", "Destination", "State"],
            srows, stretch_col=5, fit_rows=True))
        lay.addWidget(card)

    # Socket connections — every socket pin's path to the parent, not just switched ones.
    conns = sauth.socket_connections(authority)
    lay.addWidget(W.section_header(f"Socket Connections   {len(conns)}"))
    krows = []
    for cn in conns:
        krows.append([str(cn["pin"]), W.body(str(cn.get("name", "")), mono=True),
                      W.body(str(cn.get("kind", "")).title()),
                      W.body(str(cn.get("dest", "")), mono=True),
                      W.body(str(cn.get("category", ""))), W.body(str(cn.get("contact", "")))])
    lay.addWidget(W.data_table(
        ["Pin", "Name", "Middle", "Destination", "Category", "Contact"],
        krows, stretch_col=(3, 5), mono_cols={0}, fit_rows=True))

    # Lint claim files — the drift gate.
    lay.addWidget(W.section_header("Lint Claim Files"))
    lay.addWidget(W.body(
        "Drift gate: check a Build Card's asserted numbers (must-switch count, ADG714 "
        "cells, debug pin positions) against the built authority.", dim=True))
    lint_bar = QHBoxLayout(); lint_bar.setSpacing(8)
    b_lint = W.btn("Lint Claim Files…", "primary",
                   "Pick claim files and check them against the authority")
    lint_bar.addWidget(b_lint); lint_bar.addStretch(1)
    lay.addLayout(lint_bar)
    lint_holder = QVBoxLayout(); lint_holder.setSpacing(8)
    lay.addLayout(lint_holder)

    def run_lint_flow():
        if _headless():                       # offscreen drive / CI: no picker, no modal
            ctx.services.log("Lint Claim Files: no file (headless).")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            root, "Claim Files to Lint", str(Path(ctx.cfg.get("RepoRoot") or ".")),
            "Claims (*.json *.yaml *.yml)")
        if not paths:
            ctx.services.log("Lint Claim Files cancelled.")
            return

        def job():
            c = db.connect(db.default_db_path())
            try:
                return sauth.run_lint(c, [Path(p) for p in paths])
            finally:
                c.close()

        def done(res, ok2):
            clear_layout(lint_holder)
            if not ok2 or not res:
                lint_holder.addWidget(W.Verdict("Lint Failed", "Could not run the drift gate.", "err"))
                return
            all_ok, lines, drifted = res
            lint_holder.addWidget(W.Verdict(
                "No Drift" if all_ok else "Drift Found",
                "Every claimed number matches the authority." if all_ok
                else f"Drifted: {', '.join(sorted(drifted)) or 'see report'}.",
                "ok" if all_ok else "err"))
            for ln in (lines or [])[:200]:
                lint_holder.addWidget(W.body(str(ln), mono=True, wrap=True))
            ctx.services.log(f"Lint: {'no drift' if all_ok else 'DRIFT found'} "
                             f"({plural(len(lines or []), 'line')}).")

        run_populate(ctx, job, done, busy="Linting claim files...")

    b_lint.clicked.connect(run_lint_flow)
    lay.addStretch(1)
    return root


# ── the feature ──────────────────────────────────────────────────────────────
class BenchFeature(F.Feature):
    id = "bench"
    title = "Bench"
    order = 30
    category = "Firmware"

    def build(self, ctx: F.Context) -> QWidget:
        state = BenchState()
        header = None
        if state.packages:
            combo = QComboBox()
            combo.addItems(state.packages)
            combo.setCurrentText(state.package or state.packages[0])
            combo.setFixedWidth(170)
            combo.setToolTip("Choose the STM32F LQFP package to inspect (the buildable set)")
            # BENCH-06: the family filter is NOT here. It is a Profiles-only control, so it
            # lives in that panel's body (see _profiles_panel) — a global header combo that
            # no-ops on four of five tabs was the incoherence. The header carries only the
            # package, which every tab genuinely reflects. set_package resets state.family to
            # None and fires rebuild_all, which rebuilds the Profiles panel (and its own
            # family combo, defaulting to All) fresh.
            header = W.hstack(W.eyebrow("STM32F Package"), combo, spacing=8)
            combo.currentTextChanged.connect(state.set_package)
        # Overview is now the scrollable home: it absorbs the All Pins table + the Profiles
        # ladder as sections (owner v2.11), so those two standalone tabs are gone. The
        # `_allpins_panel`/`_profiles_panel` wrappers remain as functions for direct
        # callers/tests. Connection Diagram lives on Analysis now.
        panels = [
            ("Overview", lambda c: W.scroll_body(_authority_panel(c, state))),
            ("Analysis", lambda c: W.scroll_body(_analysis_panel(c, state))),
            ("MCU Pinout Viewer", lambda c: W.scroll_body(_resolver_panel(c, state))),
            ("Exports", lambda c: W.scroll_body(_outputs_panel(c, state))),
        ]
        ws = kit.tabbed_page("Bench", panels, header=header, ctx=ctx)
        # changing the package rebuilds every sub-panel so all tabs reflect it (the
        # lazy panel cache otherwise leaves Profiles/Resolver/All-Pins on stale data)
        state.on_change(ws.rebuild_all)

        def goto_resolver(mpn):
            ws.select_panel("MCU Pinout Viewer")     # builds the viewer + its bus handler
            ctx.bus.emit("bench.resolve", mpn)       # then resolve the clicked chip

        def goto_authority_pin(pos):
            ws.select_panel("Overview")
            ctx.bus.emit("bench.jump_pin", pos)
        state.goto_resolver = goto_resolver
        state.goto_authority_pin = goto_authority_pin
        return ws


F.register(BenchFeature())
