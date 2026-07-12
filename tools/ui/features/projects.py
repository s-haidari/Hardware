"""Projects — the KiCad project maintenance workspace, fully wired.

Projects are discovered under the repo root; a picker selects one. Panels call
the real nd_* / LibraryManager helpers: audit_schematic, run_erc/run_drc,
bom_from_kicad_schematic / consolidated_bom, the rename wizard, the net-class
manager, board setup, and the OSH Park fab presets. Slow / mutating work runs
off the GUI thread and logs to the status line. (Git is its own top-level
feature — see ui/features/git.py.)
"""
from __future__ import annotations

import re
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
                             QCheckBox, QDoubleSpinBox, QSpinBox, QGridLayout, QFrame,
                             QStackedWidget, QTableWidget, QHeaderView, QAbstractItemView,
                             QFileDialog, QMenu, QApplication, QDialog, QFormLayout,
                             QDialogButtonBox, QPushButton)

from .. import theme as T
from .. import widgets as W
from .. import kit
from .. import icons
from ..util import (LogSink, run_populate, clear_layout, sentence,
                    mm_to_mils, mils_to_mm, confirm)
from .. import feature as F
from .. import units as U
from ..prose import plural
from . import projects_visuals as pv

import nd_wizard
import kicad_tools
import nd_project_health as phealth
import nd_library_fill as libfill
import nd_kicad_checks as kchecks
import nd_netclass_manager as ncm
import nd_pcb_profiles as pcbprof
import nd_board_setup
import nd_fab_presets as fabp
import nd_project_settings_manager as psm
import nd_design_presets as dpre
import nd_object_conform as conform
import LibraryManager as LM
import fp_render
import nd_git
import kicad_paths

_SEV = {"error": "err", "warning": "warn", "exclusion": "mut", "info": "mut"}
# Check acronyms shown verbatim (they are names, not shouted text) — a lookup keeps
# the display string out of a .upper() call the no-drift lint can't tell from styling.
_CHECK_NAME = {"erc": "ERC", "drc": "DRC"}
_KIND_LABEL = {
    "no_footprint": "No Footprint", "duplicate_ref": "Duplicate Reference",
    "pin_pad_mismatch": "Pin / Pad Mismatch", "no_mpn": "No Manufacturer Part Number",
    "unannotated": "Unannotated", "no_3d_model": "No 3D Model", "missing_3d_model": "No 3D Model",
}
# Short labels + colour tint for the Health verdict breakdown chips (one per finding
# kind present, each clickable to filter the findings table). Both 3D-model kinds fold
# into a single "Missing Model" bucket so the user sees one gap, not two.
_KIND_SHORT = {
    "no_footprint": "No Footprint", "no_mpn": "No MPN", "unannotated": "Unannotated",
    "duplicate_ref": "Duplicate Ref", "pin_pad_mismatch": "Pin/Pad",
    "no_3d_model": "Missing Model", "missing_3d_model": "Missing Model",
}
_KIND_CHIP_TINT = {
    "no_footprint": "warn", "no_mpn": "mut", "unannotated": "err",
    "duplicate_ref": "err", "pin_pad_mismatch": "err",
    "no_3d_model": "mut", "missing_3d_model": "mut",
}


def _kind_breakdown(findings):
    """Group findings for the breakdown chips: one bucket per short label (the two
    3D-model kinds collapse into "Missing Model"), most-common first. Each bucket is
    ``{"label", "tint", "count", "kinds": {raw_kind, ...}}`` — the ``kinds`` set is
    what the findings-table filter matches on so the collapsed bucket still filters
    both underlying kinds."""
    buckets = {}
    for f in findings or []:
        raw = str(f.get("kind", ""))
        short = _KIND_SHORT.get(raw, raw.replace("_", " ").title())
        b = buckets.setdefault(short, {"label": short, "tint": _KIND_CHIP_TINT.get(raw, "mut"),
                                       "count": 0, "kinds": set()})
        b["count"] += 1
        b["kinds"].add(raw)
    return sorted(buckets.values(), key=lambda b: (-b["count"], b["label"]))


def _refs_by_kind(res) -> dict:
    """{raw_kind: {ref, ...}} from an audit result — the per-kind reference sets the
    before/after itemization diffs to name exactly which refs a Prepare fixed."""
    out: dict = {}
    for f in (res or {}).get("findings", []):
        out.setdefault(str(f.get("kind", "")), set()).add(str(f.get("ref", "")))
    return out


def _ref_sort_key(ref: str):
    """Natural order for a reference designator (R2 before R10): split the letter
    prefix from the trailing number so a column of refs reads the way KiCad numbers."""
    s = str(ref)
    m = re.match(r"^([A-Za-z_]*)(\d*)", s)
    prefix = m.group(1) if m else s
    num = int(m.group(2)) if (m and m.group(2)) else -1
    return (prefix, num, s)


def _audit_diff(before, after) -> dict:
    """The before/after Prepare itemization: per finding-kind counts before + after +
    Δ, plus the set of refs FIXED (present before, gone after) for that kind. Kinds
    that appear in either audit are included; a kind only in `after` (a newly-surfaced
    finding) shows a positive Δ. Pure — takes two audit result dicts."""
    b_kind = (before or {}).get("counts", {}).get("by_kind", {})
    a_kind = (after or {}).get("counts", {}).get("by_kind", {})
    b_refs, a_refs = _refs_by_kind(before), _refs_by_kind(after)
    rows = []
    for raw in sorted(set(b_kind) | set(a_kind)):
        bn, an = int(b_kind.get(raw, 0)), int(a_kind.get(raw, 0))
        fixed = sorted(b_refs.get(raw, set()) - a_refs.get(raw, set()), key=_ref_sort_key)
        rows.append({"kind": raw, "label": _KIND_LABEL.get(raw, raw.replace("_", " ").title()),
                     "before": bn, "after": an, "delta": an - bn, "fixed": fixed})
    b_tot = sum(int(v) for v in b_kind.values())
    a_tot = sum(int(v) for v in a_kind.values())
    return {"rows": rows, "before_total": b_tot, "after_total": a_tot}


# ── shared project state ─────────────────────────────────────────────────────
class ProjectsState:
    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.projects = []
        seen = set()
        rr = self.cfg.get("RepoRoot")
        roots = [Path(rr), Path(rr).parent] if rr else []
        for r in roots:
            try:
                for p in kicad_tools.discover_kicad_projects(r):
                    if str(p) not in seen:
                        seen.add(str(p)); self.projects.append(Path(p))
            except Exception:  # noqa: BLE001
                pass
        # Prefer a project named like the main board (Master) over archived/sub sheets
        self.project = next((p for p in self.projects if p.name.lower() == "master"),
                            self.projects[0] if self.projects else None)
        self._refreshers = []
        # SHARED ERC/DRC check cache, keyed per project so a project switch never shows
        # another project's result. It lives HERE (not on a panel) because switching the
        # project rebuilds every panel wholesale — a panel-local dict would be discarded,
        # and Overview + Health need to see the SAME cache so a Prepare in Health can
        # invalidate the ERC/DRC that Overview's readiness verdict reads.
        self._checks = {}

    def _proj_key(self):
        return str(self.project) if self.project else ""

    def checks(self) -> dict:
        """The live per-project ERC/DRC cache dict (``{"erc": summary|None, "drc":
        summary|None}``). Both Overview and Health read/write THIS object, so an
        invalidation in one is seen by the other. A summary is the raw
        ``{"errors", "warnings", ...}`` (or ``{"error": msg}``); ``None`` = not run."""
        return self._checks.setdefault(self._proj_key(), {"erc": None, "drc": None})

    def set_check(self, kind, summary):
        self.checks()[kind] = summary

    def invalidate_checks(self):
        """Drop the current project's cached ERC/DRC — IN PLACE so any panel holding a
        reference to this dict sees the reset. Called on every Prepare/Restore/Undo
        write, since those change the schematic/board and make a prior check stale."""
        c = self._checks.get(self._proj_key())
        if c is not None:
            c["erc"] = None
            c["drc"] = None

    def names(self):
        return [p.name for p in self.projects]

    def labels(self):
        """PROJ-01: display strings that keep identically-named projects apart —
        just the name when it's unique, else 'Name — parent', else the full path."""
        names = [p.name for p in self.projects]
        out = []
        for p in self.projects:
            if names.count(p.name) > 1 and p.parent.name:
                out.append(f"{p.name} — {p.parent.name}")
            else:
                out.append(p.name)
        clash = {lab for lab in out if out.count(lab) > 1}   # snapshot before mutating
        # Full-path fallback uses posix separators so the disambiguation label is
        # deterministic across platforms (str(WindowsPath) would emit backslashes).
        return [self.projects[i].as_posix() if lab in clash else lab
                for i, lab in enumerate(out)]

    def _fire(self):
        for fn in list(self._refreshers):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def select_index(self, i: int):
        """Select by list position — the unambiguous handle the combo uses (a name
        alone can match the wrong duplicate project)."""
        if 0 <= i < len(self.projects):
            self.project = self.projects[i]
            self._fire()

    def set_project(self, name):
        for p in self.projects:
            if p.name == name:
                self.project = p
                break
        self._fire()

    def on_change(self, fn):
        self._refreshers.append(fn)

    def schematics(self):
        try:
            return nd_wizard.list_schematics(self.project) if self.project else []
        except Exception:  # noqa: BLE001
            return []

    def boards(self):
        try:
            return nd_wizard.list_boards(self.project) if self.project else []
        except Exception:  # noqa: BLE001
            return []

    def root_schematic(self):
        schs = self.schematics()
        if not schs:
            return None
        try:
            return kicad_tools.pick_root_schematic(schs, kicad_tools.project_pro_file(self.project))
        except Exception:  # noqa: BLE001
            return schs[0]


def _no_project(msg="No KiCad projects discovered under the repo root.") -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(T.sp("page"), T.sp("card"), T.sp("page"), T.sp("page"))
    v.addWidget(W.body(msg, dim=True)); v.addStretch(1)
    return w


def _kicad_cli():
    try:
        return fp_render.find_board_render_cli()
    except Exception:  # noqa: BLE001
        return None


# ── Fix-All preview dialog (the honest "fix everything") ─────────────────────
def _elide(text: str, n: int = 44) -> str:
    """Trim a long value for the old→new delta so a row never forces sideways scroll."""
    s = str(text or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _lib_part_label(part: dict) -> str:
    """One library part as 'name · MPN · footprint' for the picker list (blanks dropped)."""
    bits = [str(part.get("name") or "").strip()]
    mpn = str(part.get("mpn") or "").strip()
    if mpn:
        bits.append(mpn)
    fp = str(part.get("footprint") or "").strip()
    if fp:
        bits.append(fp)
    return "  ·  ".join(b for b in bits if b)


class LibraryPickerCard(W.Card):
    """The owner's library-only fix surface for a group / an unmatched component: NO
    raw-text entry into the schematic — the user either SELECTS an existing library part
    (req c) or ADDS a new one to the Library (req b/d), and the chosen part is auto-linked
    (lib_id + footprint + identity + 3D-model) when the plan applies (req e).

    Two mutually-exclusive modes chosen by a radio-like pair:
      • "Select from Library" — a searchable read-only combo of every library part.
      • "Add to Library"      — pick the footprint the new part links to (+ optional
        identity), which creates a real MySymbols symbol carrying the footprint link.

    `request()` returns the resolved directive for the ref(s), or None when nothing was
    chosen: {"kind": "link", "lib_part": {…}} or {"kind": "add", "footprint": stem,
    "name": …, "identity": {prop: val}}."""

    # Identity fields for the "Add to Library" form (written onto the NEW library symbol,
    # not the schematic — the library is the single source of truth).
    _ADD_FIELDS = (("MPN", "Part Number"), ("Manufacturer", "Manufacturer"),
                   ("Datasheet", "Datasheet"), ("Description", "Description"))

    def __init__(self, title: str, subtitle: str, library_index, footprint_stems,
                 suggested_stem: str = "", on_change=None, parent=None):
        super().__init__(pad=14, parent=parent)
        self._library_index = list(library_index or [])
        self._footprint_stems = list(footprint_stems or [])
        self._on_change = on_change
        self._mode = "select"

        head = QHBoxLayout(); head.setSpacing(T.sp("sm"))
        head.addWidget(W.body(title, mono=True))
        if subtitle:
            head.addWidget(W.body(subtitle, dim=True))
        head.addWidget(W.tag("No Match", "mut"))
        head.addStretch(1)
        hw = QWidget(); hw.setLayout(head); self.body.addWidget(hw)

        # Mode selector: Select (default) vs Add — mutually exclusive, no free-text path.
        modes = QHBoxLayout(); modes.setSpacing(T.sp("sm"))
        self._cb_select = QCheckBox("Select from Library")
        self._cb_add = QCheckBox("Add to Library")
        self._cb_select.setToolTip("Link this component to an existing library part.")
        self._cb_add.setToolTip("No library part fits: create one (footprint + identity) "
                                 "and auto-link this component to it.")
        self._cb_select.setChecked(True)
        self._cb_select.stateChanged.connect(lambda _=0: self._pick_mode("select"))
        self._cb_add.stateChanged.connect(lambda _=0: self._pick_mode("add"))
        modes.addWidget(self._cb_select); modes.addWidget(self._cb_add); modes.addStretch(1)
        mw = QWidget(); mw.setLayout(modes); self.body.addWidget(mw)

        # SELECT: a searchable, read-only-ish combo. Editable ONLY to type a search query;
        # the value applied is always the highlighted library row (never arbitrary text) —
        # apply() resolves the combo's current index back to a library-part record.
        self._select_row = QWidget()
        sr = QHBoxLayout(self._select_row); sr.setContentsMargins(0, 0, 0, 0); sr.setSpacing(T.sp("sm"))
        lab = W.body("Library part"); lab.setFixedWidth(104); sr.addWidget(lab, 0)
        self._combo = QComboBox(); self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)     # typing NEVER adds an item
        self._combo.setMinimumHeight(30)
        self._combo.addItem("Choose a library part…", None)
        for part in self._library_index:
            self._combo.addItem(_lib_part_label(part), part.get("name"))
        try:
            self._combo.setCurrentIndex(0)
            self._combo.lineEdit().setPlaceholderText("Search the library by name / MPN / footprint")
            self._combo.lineEdit().setText("")
        except Exception:  # noqa: BLE001
            pass
        # A completer over the item texts makes it a real search box.
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.currentIndexChanged.connect(lambda _=0: self._changed())
        sr.addWidget(self._combo, 1)
        self.body.addWidget(self._select_row)

        # ADD: footprint pick-list (which footprint the new part links to) + identity form.
        # Hidden until the Add mode is chosen. Its edits fill the NEW LIBRARY symbol.
        self._add_box = QWidget()
        ab = QVBoxLayout(self._add_box); ab.setContentsMargins(0, 0, 0, 0); ab.setSpacing(T.sp("sm"))
        fp_row = QWidget(); fpr = QHBoxLayout(fp_row); fpr.setContentsMargins(0, 0, 0, 0); fpr.setSpacing(T.sp("sm"))
        fl = W.body("Footprint"); fl.setFixedWidth(104); fpr.addWidget(fl, 0)
        self._fp_combo = QComboBox(); self._fp_combo.setEditable(False); self._fp_combo.setMinimumHeight(30)
        self._fp_combo.addItem("Choose a library footprint…", None)
        for stem in self._footprint_stems:
            self._fp_combo.addItem(stem, stem)
        if suggested_stem:
            i = self._fp_combo.findData(suggested_stem)
            if i >= 0:
                self._fp_combo.setCurrentIndex(i)
        self._fp_combo.currentIndexChanged.connect(lambda _=0: self._changed())
        fpr.addWidget(self._fp_combo, 1)
        ab.addWidget(fp_row)
        ab.addWidget(W.body("Creates a MySymbols part linked to that footprint; the fields "
                            "below describe the new library part.", dim=True))
        self._add_edits = {}
        for prop, label in self._ADD_FIELDS:
            row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(T.sp("sm"))
            pl = W.body(label); pl.setFixedWidth(104); rl.addWidget(pl, 0)
            e = QLineEdit(); e.setPlaceholderText(f"Add {label.lower()} (optional)")
            e.setMinimumHeight(30)
            e.textChanged.connect(lambda _=0: self._changed())
            rl.addWidget(e, 1)
            ab.addWidget(row)
            self._add_edits[prop] = e
        self._add_box.setVisible(False)
        self.body.addWidget(self._add_box)

    # ── mode + change plumbing ─────────────────────────────────────────────────
    def _pick_mode(self, mode):
        # Enforce mutual exclusivity without recursing into stateChanged storms.
        self._mode = mode
        self._cb_select.blockSignals(True); self._cb_add.blockSignals(True)
        self._cb_select.setChecked(mode == "select")
        self._cb_add.setChecked(mode == "add")
        self._cb_select.blockSignals(False); self._cb_add.blockSignals(False)
        self._select_row.setVisible(mode == "select")
        self._add_box.setVisible(mode == "add")
        self._changed()

    def _changed(self):
        if callable(self._on_change):
            self._on_change()

    # ── the resolved directive ─────────────────────────────────────────────────
    def selected_lib_part(self):
        """The library-part record currently chosen in Select mode, or None."""
        name = self._combo.currentData()
        if not name:
            return None
        for part in self._library_index:
            if part.get("name") == name:
                return part
        return None

    def request(self):
        """Resolve this card to a link/add directive, or None if nothing usable is chosen.
        Never returns free-text bound for the schematic: Select yields a library-part record;
        Add yields a footprint stem (+ optional identity for the NEW library part)."""
        if self._mode == "select":
            part = self.selected_lib_part()
            if part is None:
                return None
            return {"kind": "link", "lib_part": part}
        # Add mode: require a footprint stem (the symbol->footprint link must be real).
        stem = self._fp_combo.currentData()
        if not stem:
            return None
        identity = {prop: e.text().strip() for prop, e in self._add_edits.items()
                    if e.text().strip()}
        return {"kind": "add", "footprint": stem, "name": None, "identity": identity}


class FillPreviewDialog(QDialog):
    """One reviewable preview covering every proposed change before anything is
    written. Grouped by sheet -> component; each component shows a confidence chip
    (exact calm / verify amber), its matched Library part, and each field change as
    a checkbox row "field: old -> new". Fills on an exact match are pre-checked;
    overwrites and fuzzy (verify) matches are unchecked and flagged. Primary Apply is
    disabled until at least one field is selected.

    `plan` is the nd_library_fill FillPlan. `annotate_n` is how many references the
    annotate fixer would fix (shown + applied alongside the fills). `on_apply` is the
    caller's writer: `on_apply(selected_pairs: set, do_annotate: bool)` — OPTIONAL: when the
    dialog is driven by the kit ▶ Prepare flow (via the recipe's ``preview`` hook), the flow
    reads ``selected()`` / ``annotate_selected()`` AFTER ``exec_`` and owns the write, so
    ``on_apply`` is ``None`` and ``apply()`` just records the choice and accepts.
    """

    def __init__(self, plan, annotate_n, on_apply=None, cfg=None, parent=None,
                 components=None, sheet_of=None):
        super().__init__(parent)
        self.plan = plan
        self.annotate_n = int(annotate_n or 0)
        self._on_apply = on_apply
        self._cfg = cfg or {}
        self._components = list(components or [])
        self._sheet_of = dict(sheet_of or {})
        self._boxes = []                    # (checkbox, ref, prop) for the fill fields
        self._annotate_box = None
        # Triage filter (M-triage): [(card_widget, {refs}, is_passive)] so a prefix / passives
        # filter can hide non-matching cards on a big board, and Select-All + Only-Exact act
        # only on the VISIBLE rows. _filter_prefix / _passives_only hold the live filter.
        self._filter_cards = []
        self._filter_prefix = ""
        self._passives_only = False
        # Library-only linking (owner: NO free-text on the schematic; select-or-add from the
        # Library). _link_cards = [(ref-or-refs tuple, LibraryPickerCard)] for the group /
        # "still needs input" sections. _library_index is the searchable pick-list; each card
        # produces a resolved link/add request read on apply().
        self._library_index = libfill.library_parts(self._cfg)
        self._link_cards = []               # [(refs_tuple, LibraryPickerCard)]
        self._extra_selected = set()        # (ref, prop) pairs recorded on apply()
        self._link_requests = {}            # ref -> link/add request dict, recorded on apply()
        # Passive groups (fill-once) + the components that need manual entry (no library
        # match / distributor data and not a passive) — computed from the components.
        self._groups = libfill.passive_groups(self._components)
        grouped = {r for g in self._groups for r in g["refs"]}
        planned = {it["ref"] for it in (plan or {}).get("items", []) if it.get("changes")}
        self._manual_components = []
        for comp in self._components:
            ref = comp.get("ref", "")
            if ref in planned or ref in grouped:
                continue
            passport = libfill.component_completion(comp)
            if not passport["is_complete"]:
                self._manual_components.append((comp, passport))
        self.applied = False
        self.setWindowTitle("Prepare Components Preview")
        self.setModal(True)
        self.setMinimumSize(560, 460)
        self.resize(700, 640)
        self._build()
        self._sync_apply_enabled()

    # ── construction ─────────────────────────────────────────────────────────
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.sp("xl"), 18, T.sp("xl"), 18)
        outer.setSpacing(T.sp("path"))

        self._header = W.subhead("")            # Semibold region label (kit)
        outer.addWidget(self._header)

        # Bulk affordances: a big board must not be per-field toil.
        bulk = QHBoxLayout(); bulk.setSpacing(T.sp("sm"))
        b_all = W.btn("Select All", "ghost", "Check every proposed field")
        b_exact = W.btn("Only Exact", "ghost", "Check only the confident, blank-fill changes")
        b_clear = W.btn("Clear", "ghost", "Uncheck everything")
        b_all.clicked.connect(lambda: self._bulk("all"))
        b_exact.clicked.connect(lambda: self._bulk("exact"))
        b_clear.clicked.connect(lambda: self._bulk("clear"))
        for b in (b_all, b_exact, b_clear):
            bulk.addWidget(b)
        bulk.addStretch(1)
        outer.addLayout(bulk)

        # Triage filter row: a big board must not force scrolling past irrelevant refs.
        # A reference-prefix box ("C" or "C12") + a Passives-only toggle hide non-matching
        # cards; Select All / Only Exact then act on the VISIBLE rows only.
        self._filter_sections = []          # (header_widget, {refs in this section})
        trow = QHBoxLayout(); trow.setSpacing(T.sp("sm"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by reference (e.g. C or C12)")
        self._filter_edit.setMinimumHeight(30)
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        self._passives_box = QCheckBox("Passives only")
        self._passives_box.setToolTip("Show only R / C / L / FB references (the fill-once groups).")
        self._passives_box.stateChanged.connect(self._on_filter_changed)
        self._filter_count = W.body("", dim=True)
        trow.addWidget(W.body("Filter", dim=True))
        trow.addWidget(self._filter_edit, 1)
        trow.addWidget(self._passives_box)
        trow.addWidget(self._filter_count)
        outer.addLayout(trow)

        # Scrollable body so a long plan never overflows the modal. `W.scroll_body`
        # owns the transparent-viewport chrome (no direct styling here).
        body = QWidget()
        bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(T.sp("md"))

        if self.annotate_n:
            bl.addWidget(self._annotate_card())

        # Group items by sheet, preserving first-seen order.
        by_sheet = {}
        for item in self.plan.get("items", []):
            if not item.get("changes"):
                continue
            by_sheet.setdefault(item.get("sheet") or "", []).append(item)
        for sheet, items in by_sheet.items():
            hdr = W.section_header(_sheet_label(sheet))
            bl.addWidget(hdr)
            self._filter_sections.append((hdr, {it["ref"] for it in items}))
            for item in items:
                card = self._component_card(item)
                bl.addWidget(card)
                self._filter_cards.append((card, {item["ref"]}, _is_passive_ref(item["ref"])))

        # Passive groups (mockup ask: "group them so we use the data of one"): one card
        # per value+footprint, filled once and applied to every ref.
        if self._groups:
            hdr = W.section_header("Passive Groups")
            bl.addWidget(hdr)
            bl.addWidget(W.body("Pick one library part per group (or add one); it links every "
                                "component in that group.", dim=True))
            self._filter_sections.append((hdr, {r for g in self._groups for r in g["refs"]}))
            for group in self._groups:
                card = self._group_card(group)
                bl.addWidget(card)
                self._filter_cards.append((card, set(group["refs"]), True))   # groups are passives

        # Still needs your input: components with no library match / distributor data —
        # type the fields directly so every component ends fully filled.
        if self._manual_components:
            hdr = W.section_header("Still Needs Your Input")
            bl.addWidget(hdr)
            bl.addWidget(W.body("These components have no library match. Select a library "
                                "part or add one; it is auto-linked to the schematic.", dim=True))
            self._filter_sections.append((hdr, {c.get("ref", "") for c, _ in self._manual_components}))
            for comp, passport in self._manual_components:
                card = self._manual_card(comp, passport)
                bl.addWidget(card)
                ref = comp.get("ref", "")
                self._filter_cards.append((card, {ref}, _is_passive_ref(ref)))

        if not self._boxes and not self.annotate_n and not self._groups \
                and not self._manual_components:
            bl.addWidget(W.body("Every component is already complete.", dim=True))

        outer.addWidget(W.scroll_body(body), 1)
        self._apply_card_filter()               # seed the "N of M" count

        # Footer: primary Apply + secondary Cancel (one primary only).
        footer = QHBoxLayout(); footer.setSpacing(T.sp("sm")); footer.addStretch(1)
        b_cancel = W.btn("Cancel", "ghost", "Close without writing anything")
        b_cancel.clicked.connect(self.reject)
        self._apply_btn = W.btn("Apply", "primary", "Write the checked changes, then re-audit")
        self._apply_btn.clicked.connect(self.apply)
        footer.addWidget(b_cancel); footer.addWidget(self._apply_btn)
        outer.addLayout(footer)

    def _annotate_card(self):
        card = W.Card(pad=14)
        cb = QCheckBox(f"Annotate {plural(self.annotate_n, 'unannotated reference')}")
        cb.setChecked(True)                 # deterministic + safe: pre-checked
        cb.setToolTip("Assign the next free designator to each unannotated symbol "
                      "(each edited file is backed up first).")
        cb.stateChanged.connect(lambda _=0: self._sync_apply_enabled())
        self._annotate_box = cb
        card.body.addWidget(cb)
        return card

    def _component_card(self, item):
        match = item.get("match", {})
        conf = match.get("confidence", "none")
        lib_part = match.get("lib_part") or {}
        card = W.Card(pad=14)

        head = QHBoxLayout(); head.setSpacing(T.sp("sm"))
        head.addWidget(W.body(str(item["ref"]), mono=True))
        # Confidence chip: exact is calm (ok), verify is amber (warn).
        chip_kind = "ok" if conf == "exact" else ("warn" if conf == "verify" else "mut")
        chip_txt = "Exact" if conf == "exact" else ("Verify" if conf == "verify" else "No Match")
        head.addWidget(W.tag(chip_txt, chip_kind))
        alts = match.get("alternatives", 0)
        if conf == "verify" and alts:
            head.addWidget(W.body(f"+{alts} other", dim=True))
        name = lib_part.get("name") or ""
        if name:
            head.addWidget(W.body(f"← {name}", dim=True))
        head.addStretch(1)
        head_w = QWidget(); head_w.setLayout(head)
        card.body.addWidget(head_w)

        for ch in item.get("changes", []):
            card.body.addWidget(self._change_row(item, ch, conf))
        return card

    def _change_row(self, item, ch, conf):
        old = _elide(ch.get("old", "")) or "∅"          # empty-set glyph for a blank
        new = _elide(ch.get("new", ""))
        label = f"{ch['prop']}:  {old}  →  {new}"
        cb = QCheckBox(label)
        # Pre-check ONLY blank-fills on an exact match; overwrites + fuzzy stay off.
        pre = (conf == "exact" and ch.get("kind") == "fill")
        cb.setChecked(pre)
        if ch.get("kind") == "overwrite":
            cb.setToolTip("Overwrites existing data. Opt in per field.")
        cb.stateChanged.connect(lambda _=0: self._sync_apply_enabled())
        self._boxes.append((cb, item["ref"], ch["prop"], ch.get("kind"), conf))
        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(T.sp("sm"))
        rl.addWidget(cb)
        if ch.get("kind") == "overwrite":
            rl.addWidget(W.tag("Overwrite", "warn"))
        rl.addStretch(1)
        return row

    def _group_card(self, group):
        """A fill-once library picker for one passive group: choose ONE library part (or
        add one) and it links every ref in the group. No free-text on the schematic."""
        stems = libfill.library_footprint_stems(self._cfg)
        refs = ", ".join(group["refs"][:12]) + (" …" if len(group["refs"]) > 12 else "")
        card = LibraryPickerCard(
            group["label"], refs, self._library_index, stems,
            suggested_stem=group.get("footprint", ""),
            on_change=self._sync_apply_enabled)
        self._link_cards.append((tuple(group["refs"]), card))
        return card

    def _manual_card(self, comp, passport):
        """A library picker for one unmatched component: select an existing library part or
        add a new one; the chosen part is auto-linked on apply. No free-text on the schematic."""
        ref = comp.get("ref", "")
        stems = libfill.library_footprint_stems(self._cfg)
        val = str(comp.get("value") or "").strip()
        suggested = LM.footprint_name(comp.get("footprint")
                                      or (comp.get("props") or {}).get("Footprint") or "")
        card = LibraryPickerCard(
            str(ref), val, self._library_index, stems,
            suggested_stem=suggested, on_change=self._sync_apply_enabled)
        self._link_cards.append(((ref,), card))
        return card

    def _collect_links(self):
        """Resolve every VISIBLE picker card to a per-ref link/add request:
        {ref: {"kind": "link"|"add", …}}. A group's single request fans to every ref in the
        group (fill-once). Scoped to visible cards so the triage filter stays honest."""
        out = {}
        for refs, card in self._link_cards:
            if not any(self._ref_visible(r) for r in refs):
                continue
            try:
                req = card.request()
            except RuntimeError:
                continue
            if not req:
                continue
            for r in refs:
                out[r] = req
        return out

    # ── triage filter ─────────────────────────────────────────────────────────
    def _on_filter_changed(self, *_):
        self._filter_prefix = self._filter_edit.text().strip()
        self._passives_only = self._passives_box.isChecked()
        self._apply_card_filter()
        self._sync_apply_enabled()          # header + Apply reflect the now-visible selection

    def _ref_visible(self, ref: str) -> bool:
        """Does `ref` pass the live filter (prefix match + passives toggle)?"""
        if self._passives_only and not _is_passive_ref(ref):
            return False
        pref = self._filter_prefix
        if pref and not str(ref).upper().startswith(pref.upper()):
            return False
        return True

    def _apply_card_filter(self):
        """Show/hide each card by the live filter, hide a section header whose cards are
        all hidden, and update the 'N of M shown' count. Select All / Only Exact honour
        the same predicate so a bulk action on a filtered board touches only what's shown."""
        shown = 0
        for card, refs, is_passive in self._filter_cards:
            vis = any(self._ref_visible(r) for r in refs) if refs else True
            if self._passives_only and not is_passive:
                vis = False
            try:
                card.setVisible(vis)
            except RuntimeError:
                continue
            if vis:
                shown += 1
        for hdr, refs in self._filter_sections:
            try:
                hdr.setVisible(any(self._ref_visible(r) for r in refs))
            except RuntimeError:
                pass
        total = len(self._filter_cards)
        if hasattr(self, "_filter_count"):
            self._filter_count.setText(f"{shown} of {total} shown"
                                       if (self._filter_prefix or self._passives_only) else "")

    # ── selection ────────────────────────────────────────────────────────────
    def _bulk(self, mode):
        for cb, ref, _prop, kind, conf in self._boxes:
            if not self._ref_visible(ref):
                continue                       # a filtered-out row is never bulk-toggled
            if mode == "all":
                cb.setChecked(True)
            elif mode == "clear":
                cb.setChecked(False)
            elif mode == "exact":
                cb.setChecked(conf == "exact" and kind == "fill")
        self._sync_apply_enabled()

    def select_all(self):
        """Check every proposed field (test/programmatic seam for `_bulk('all')`)."""
        self._bulk("all")

    def selected(self):
        """The set of (ref, prop) field pairs to write: the checked library/distributor
        boxes plus the user-typed group/manual fills recorded on apply(). Scoped to the
        VISIBLE rows so the triage filter is honest — a row hidden by the filter (even an
        auto-pre-checked exact fill) does NOT apply; un-filter to bring it back."""
        base = {(ref, prop) for cb, ref, prop, _k, _c in self._boxes
                if cb.isChecked() and self._ref_visible(ref)}
        return base | self._extra_selected

    def annotate_selected(self):
        return bool(self._annotate_box and self._annotate_box.isChecked())

    def library_links(self):
        """The per-ref link/add directives chosen in the picker cards, recorded on apply().
        {ref: {"kind": "link", "lib_part": {…}}} or {ref: {"kind": "add", …}}. The Prepare
        flow reads this after exec_ and runs the KiCad-correct link (lib_id + footprint +
        3D-model) via nd_library_fill."""
        return dict(self._link_requests)

    def _has_selection(self):
        return (bool(self.selected()) or self.annotate_selected()
                or bool(self._collect_links()))

    def _sync_apply_enabled(self):
        self._apply_btn.setEnabled(self._has_selection())
        sel = self.selected()
        links = self._collect_links()
        comps = len({r for r, _p in sel} | set(links))
        need = sum(1 for cb, _r, _p, _k, conf in self._boxes
                   if conf == "verify" and cb.isChecked())
        parts = [f"{plural(comps, 'component')}",
                 f"{plural(len(sel), 'field')}"]
        if links:
            n_link = sum(1 for r in links.values() if r["kind"] == "link")
            n_add = sum(1 for r in links.values() if r["kind"] == "add")
            if n_link:
                parts.append(f"{plural(n_link, 'library link')}")
            if n_add:
                parts.append(f"{n_add} new library {'part' if n_add == 1 else 'parts'}")
        if self.annotate_n and self.annotate_selected():
            parts.append(f"{plural(self.annotate_n, 'annotation')}")
        if need:
            parts.append(f"{need} need your check")
        self._header.setText("  ·  ".join(parts))

    # ── apply ────────────────────────────────────────────────────────────────
    def apply(self):
        """Record the selection and close accepted. When a writer was supplied
        (``on_apply``) call it; when driven by the ▶ Prepare flow (``on_apply is None``)
        the flow reads ``selected()`` / ``annotate_selected()`` after ``exec_`` and writes."""
        if not self._has_selection():
            return
        # Record the per-ref library link/add directives so the Prepare flow can run the
        # KiCad-correct link (lib_id + footprint + 3D-model) after exec_. No free-text ever
        # reaches the schematic — every directive resolves to a library part.
        self._link_requests = self._collect_links()
        self.applied = True
        if callable(self._on_apply):
            self._on_apply(self.selected(), self.annotate_selected())
        self.accept()


_PASSIVE_PREFIXES = ("R", "C", "L", "FB")


def _is_passive_ref(ref: str) -> bool:
    """A passive reference (R/C/L/FB…) by its letter prefix — the 'Passives only' filter
    and the fill-once groups treat these as the bulk-passive class. FB is checked before
    the single letters so 'FB3' isn't misread as an 'F' part."""
    s = str(ref or "").strip().upper()
    if s.startswith("FB"):
        return True
    return bool(s) and s[0] in ("R", "C", "L")


def _sheet_label(sheet: str) -> str:
    """A sheet path as a short, posix, human label for a group header."""
    if not sheet:
        return "Schematic"
    try:
        return Path(sheet).name
    except Exception:  # noqa: BLE001
        return str(sheet)


# ── Overview — project readiness verdict + Next Step, on kit.workbench ────────
def _overview_panel(ctx, state) -> QWidget:
    """Project readiness Overview, on the ``kit.workbench`` recipe — a browse/verdict tab (no
    ▶ primary; a verdict-only surface is legal). It rolls project readiness up across four axes
    and names the single most useful **Next Step**:

    - **Audit** (reuses ``phealth.audit_project``, memoised by sheet mtime like Health),
    - **ERC / DRC** (cached on-demand: the subprocess checks are far too costly to run on every
      verdict refresh, so they are secondaries whose last result folds into the verdict),
    - **Working tree** (``nd_git.status`` + ``nd_git.ahead_behind`` — uncommitted / ahead / behind),
    - **KiCad CLI** detection (``kicad_paths.find_kicad_cli`` — PARITY; the "KiCad CLI: <path|not
      found>" line makes tool detection visible).

    Verdict is quiet-green "Ready" when everything checks out; err/warn chips otherwise. Machinery
    (Manage): Open in KiCad · Reveal Folder · Clear KiCad Cache (the same two backend calls PCB
    Setup uses — deduped). Returns the ``host`` body (the caller wraps it in ``W.scroll_body``)."""
    if not state.project:
        return _no_project()

    log = getattr(getattr(ctx, "services", None), "log", None)

    def _log(line):
        if callable(log):
            log(str(line))

    host = None                        # bound after kit.workbench(); None-safe reads before then
    memo: dict = {}                    # audit memo, keyed by sheet mtime (mirror Health)
    # The ERC/DRC cache is SHARED on state (per project), so a Prepare in Health
    # invalidates the very dict this readiness verdict reads. Bound once per build;
    # a project switch rebuilds this panel, re-binding to the new project's dict.
    checks: dict = state.checks()
    busy = kit.BusyDict()

    def snapshot() -> dict:
        root = state.root_schematic()
        return {"schs": [str(s) for s in state.schematics()],
                "root_sch": str(root) if root else None,
                "boards": [str(b) for b in state.boards()],
                "repo": str(ctx.cfg["RepoRoot"]) if ctx.cfg.get("RepoRoot") else None,
                "fp_dirs": [ctx.cfg["FootprintLib"]] if ctx.cfg.get("FootprintLib") else None,
                "mdl_dirs": [ctx.cfg["ModelLib"]] if ctx.cfg.get("ModelLib") else None,
                "name": state.project.name if state.project else "project"}

    def _audit(snap):
        schs = snap["schs"]
        try:
            sig = (tuple((s, Path(s).stat().st_mtime_ns) for s in schs),
                   tuple(snap["fp_dirs"] or ()), tuple(snap["mdl_dirs"] or ()),
                   libfill.library_index_signature(ctx.cfg))   # swap/edit the library -> re-audit
        except OSError:
            sig = None
        if sig is not None and memo.get("sig") == sig:
            return memo["res"]
        res = phealth.audit_project(schs, snap["fp_dirs"], snap["mdl_dirs"])
        if sig is not None:
            memo["sig"] = sig
            memo["res"] = res
        return res

    def _next_step(r, snap):
        """The single most useful next action → (title, subtitle, kind). Priority: blocked env
        → audit errors → ERC/DRC errors → un-run checks → git reconcile → warnings → ready."""
        if not snap["schs"]:
            return ("Add a schematic sheet", "This project has no schematic to audit or build.", "warn")
        if r["cli"] is None:
            return ("Install the KiCad CLI", "ERC/DRC and board rendering need kicad-cli on PATH.", "err")
        a = r["audit"] or {}
        if a.get("errs"):
            n = a["errs"]
            return (f"Fix {plural(n, 'audit error')}",
                    "Open the Health tab → ▶ Prepare Components.", "err")
        erc, drc = r["erc"], r["drc"]
        if erc and not erc.get("error") and erc.get("errors"):
            n = erc["errors"]
            return (f"Fix {plural(n, 'ERC violation')}", "Re-run ERC after fixing.", "err")
        if drc and not drc.get("error") and drc.get("errors"):
            n = drc["errors"]
            return (f"Fix {plural(n, 'DRC violation')}", "Re-run DRC after fixing.", "err")
        pending = []
        if erc is None:
            pending.append("ERC")
        if drc is None and snap["boards"]:
            pending.append("DRC")
        if pending:
            return (f"Run {' / '.join(pending)}", "Verify the schematic and board before fabrication.", "warn")
        g = r["git"]
        if g and not g.get("error"):
            if g["changed"]:
                n = g["changed"]
                return (f"Commit {plural(n, 'change')}", "Open the Git tab to review and commit.", "warn")
            if g["ahead"]:
                n = g["ahead"]
                return (f"Push {plural(n, 'commit')}", "Your branch is ahead of the remote.", "warn")
            if g["behind"]:
                n = g["behind"]
                return (f"Pull {plural(n, 'commit')}", "Your branch is behind the remote.", "warn")
        if a.get("warns"):
            n = a["warns"]
            return (f"Review {plural(n, 'audit warning')}", "Open the Health tab for detail.", "warn")
        return ("Ready to fabricate", "Audit clean, checks pass, working tree in sync.", "ok")

    def _readiness(snap):
        """Roll every readiness axis into one dict (audit memoised; git + cli cheap live reads;
        ERC/DRC from the on-demand cache). Off-thread-safe — touches no widgets."""
        out = {"audit": None, "erc": checks["erc"], "drc": checks["drc"], "git": None, "cli": None}
        out["cli"] = kicad_paths.find_kicad_cli()          # PARITY: surfaces find_kicad_cli
        if snap["schs"]:
            res = _audit(snap)
            bs = res.get("counts", {}).get("by_severity", {})
            out["audit"] = {"errs": bs.get("error", 0), "warns": bs.get("warning", 0),
                            "notes": bs.get("info", 0), "healthy": res.get("healthy", 0),
                            "comps": res.get("components", 0)}
        repo = snap["repo"]
        if repo:
            st = nd_git.status(repo)
            ab = nd_git.ahead_behind(repo)
            out["git"] = {"clean": st.get("clean", True), "error": st.get("error"),
                          "changed": len(st.get("staged", [])) + len(st.get("modified", []))
                          + len(st.get("untracked", [])),
                          "ahead": (ab[0] if ab else 0), "behind": (ab[1] if ab else 0),
                          "tracking": ab is not None}
        out["next"] = _next_step(out, snap)
        return out

    # ── verdict: the readiness rollup (quiet-green when ready) ───────────────────────────
    def verdict(snap):
        if not snap["schs"]:
            return W.VerdictState(kind="mut", title="No Schematic",
                                  subtitle="This project has no schematic sheet.")
        r = _readiness(snap)
        a = r["audit"] or {}
        erc, drc, g = r["erc"], r["drc"], r["git"]
        chips = []
        if a.get("errs"):
            chips.append(("Audit", str(a["errs"]), "err"))
        if erc and not erc.get("error") and erc.get("errors"):
            chips.append(("ERC", str(erc["errors"]), "err"))
        if drc and not drc.get("error") and drc.get("errors"):
            chips.append(("DRC", str(drc["errors"]), "err"))
        if g and not g.get("error"):
            if g["ahead"]:
                chips.append(("Ahead", str(g["ahead"]), "warn"))
            if g["behind"]:
                chips.append(("Behind", str(g["behind"]), "warn"))
        title, sub, kind = r["next"]
        if kind == "ok":
            return W.VerdictState(kind="ok", title="Ready", subtitle=sub, chips=chips)
        return W.VerdictState(kind=kind, title=title, subtitle=sub, chips=chips)

    # ── detail: the readiness rows + Next Step + KiCad CLI, chrome once / fill repopulates ──
    def _check_row(label, summary, hint):
        if summary is None:
            return (label, "Not run", "mut", hint)
        if summary.get("error"):
            return (label, "Could not run", "warn", str(summary.get("error"))[:80])
        if summary.get("errors"):
            return (label, plural(summary["errors"], "error"), "err",
                    plural(summary.get("warnings", 0), "warning"))
        if summary.get("warnings"):
            return (label, plural(summary["warnings"], "warning"), "warn", "")
        return (label, "Clean", "ok", "")

    def _readiness_rows(r, snap):
        rows = []
        a = r["audit"]
        if a is None:
            rows.append(("Audit", "No schematic", "mut", ""))
        elif a["errs"]:
            rows.append(("Audit", plural(a["errs"], "error"), "err", f"{a['healthy']}/{a['comps']} components healthy"))
        elif a["warns"]:
            rows.append(("Audit", plural(a["warns"], "warning"), "warn", f"{a['healthy']}/{a['comps']} components healthy"))
        elif a["notes"]:
            rows.append(("Audit", plural(a["notes"], "note"), "mut", f"{a['healthy']}/{a['comps']} components healthy"))
        else:
            rows.append(("Audit", "Clean", "ok", f"{a['comps']} components healthy"))
        rows.append(_check_row("ERC", r["erc"], "Run ERC to check the schematic."))
        if snap["boards"]:
            rows.append(_check_row("DRC", r["drc"], "Run DRC to check the board."))
        else:
            rows.append(("DRC", "No board", "mut", "This project has no PCB to check."))
        g = r["git"]
        if g is None:
            rows.append(("Working Tree", "No repo", "mut", "Not under a git repository."))
        elif g.get("error"):
            rows.append(("Working Tree", "Unavailable", "mut", "git status could not be read."))
        else:
            parts = []
            if g["changed"]:
                parts.append(f"{g['changed']} uncommitted")
            if g["ahead"]:
                parts.append(f"{g['ahead']} ahead")
            if g["behind"]:
                parts.append(f"{g['behind']} behind")
            if not parts:
                rows.append(("Working Tree", "In sync", "ok",
                             "Clean, level with the remote." if g["tracking"] else "Clean (no upstream tracked)."))
            else:
                rows.append(("Working Tree", " · ".join(parts), "warn", "Open the Git tab to reconcile."))
        cli = r["cli"]
        rows.append(("KiCad CLI", "Found" if cli else "Not found", "ok" if cli else "warn",
                     cli or "Install KiCad or add kicad-cli to PATH."))
        return rows

    def _row_widget(label, status, kind, det):
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(T.sp("row"))
        key = W.body(label); key.setFixedWidth(116)
        h.addWidget(key)
        h.addWidget(W.tag(status, kind))
        if det:
            h.addWidget(W.body(det, dim=True), 1)
        else:
            h.addStretch(1)
        return w

    def detail(snap, handle):
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.sp("path"))
        col.addWidget(W.eyebrow("Readiness"))
        rows_host = QWidget(); rows_box = QVBoxLayout(rows_host)
        rows_box.setContentsMargins(0, 0, 0, 0); rows_box.setSpacing(T.sp("sm"))
        col.addWidget(rows_host)
        col.addWidget(W.eyebrow("Next Step"))
        next_host = QWidget(); next_box = QVBoxLayout(next_host)
        next_box.setContentsMargins(0, 0, 0, 0); next_box.setSpacing(T.sp("xs"))
        col.addWidget(next_host)

        def fill(s):
            r = _readiness(s)
            clear_layout(rows_box)
            for (label, status, kind, det) in _readiness_rows(r, s):
                rows_box.addWidget(_row_widget(label, status, kind, det))
            clear_layout(next_box)
            title, sub, kind = r["next"]
            line = QWidget(); lh = QHBoxLayout(line)
            lh.setContentsMargins(0, 0, 0, 0); lh.setSpacing(T.sp("sm"))
            lh.addWidget(W.tag(title, kind)); lh.addStretch(1)
            next_box.addWidget(line)
            if sub:
                next_box.addWidget(W.body(sub, dim=True))

        return body, fill

    # ── secondary op runners (busy-gated, off-thread) ────────────────────────────────────
    def _run_check(kind):
        if busy["on"]:
            return
        snap = snapshot()
        cli = kicad_paths.find_kicad_cli()
        if not cli:
            _log("kicad-cli not found on PATH. Install KiCad or add it to PATH.")
            return
        name = _CHECK_NAME.get(kind, kind)
        target = snap["root_sch"] if kind == "erc" else (snap["boards"][0] if snap["boards"] else None)
        if not target:
            _log(f"No {'schematic' if kind == 'erc' else 'board'} to check.")
            return
        busy["on"] = True

        def work():
            fn = kchecks.run_erc if kind == "erc" else kchecks.run_drc
            res = fn(target, cli)
            if not res or res.get("error") or res.get("returncode"):
                msg = str((res or {}).get("error") or f"kicad-cli exited {(res or {}).get('returncode')}")
                return {"error": sentence(msg)}
            return res.get("summary", {})

        def done(summary, ok):
            busy["on"] = False
            checks[kind] = summary if ok else {"error": "operation failed"}
            s = summary or {}
            if ok and not s.get("error"):
                _log(f"{name}: {s.get('errors', 0)} errors / {s.get('warnings', 0)} warnings.")
            elif ok:
                _log(f"{name}: {s.get('error')}")
            host._region.handle.refresh()

        run_populate(ctx, work, done, busy=f"{name}...")

    def _pro_file():
        try:
            return kicad_tools.project_pro_file(state.project)
        except Exception:  # noqa: BLE001
            return None

    def _open_target(path, what):
        from ..util import _headless
        if not path or not Path(path).exists():
            _log(f"No {what} to open for this project.")
            return
        if _headless():                                    # offscreen drive / CI: no desktop handoff
            _log(f"Would open {Path(path).name}.")
            return
        from PyQt5.QtGui import QDesktopServices
        from PyQt5.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        _log(f"Opened {Path(path).name}.")

    def _open_in_kicad():
        _open_target(_pro_file(), "KiCad project")

    def _reveal():
        pro = _pro_file()
        folder = Path(pro).parent if pro else (state.project if state.project else None)
        _open_target(folder, "project folder")

    def _clear_cache():
        if busy["on"]:
            return
        cfg = getattr(ctx, "cfg", None) or {}
        root_dir = cfg.get("RepoRoot") or (str(Path(state.project).parent) if state.project else None)
        if not root_dir:
            _log("No repo root to clear KiCad caches under.")
            return
        if not confirm(host, "Clear KiCad Cache",
                       "Delete KiCad cache files (.kicad_prl, lock files, fp-info-cache, "
                       "rescue/legacy caches) under the repository? KiCad regenerates them."):
            return
        busy["on"] = True

        def work():
            rp = Path(root_dir)
            counts = psm.clear_project_cache_files(rp, verbose=False) or {}
            try:
                ncm.clear_project_cache(rp)
            except Exception:  # noqa: BLE001
                pass
            n = sum(v for v in counts.values() if isinstance(v, int))
            return f"Cleared {plural(n, 'KiCad cache file')} under {rp.name}."

        def done(line, ok):
            busy["on"] = False
            _log(line if ok else "Clearing the KiCad cache failed.")

        run_populate(ctx, work, done, busy="Clearing KiCad cache...")

    secondary = [
        kit.action("Run Electrical Rules Check", lambda: _run_check("erc"),
                   tip="Run KiCad's ERC on the root schematic (via kicad-cli); the result folds into readiness"),
        kit.action("Run Design Rules Check", lambda: _run_check("drc"),
                   tip="Run KiCad's DRC on the board (via kicad-cli); the result folds into readiness"),
        kit.action("Refresh", lambda: host._refresh(),
                   tip="Re-read the audit and git status for this project"),
    ]
    machinery = [
        kit.action("Open in KiCad", _open_in_kicad, tip="Open this project's .kicad_pro in KiCad"),
        kit.action("Reveal Folder", _reveal, tip="Open the project folder in your file manager"),
        kit.action("Clear KiCad Cache", _clear_cache,
                   tip="Delete KiCad cache/lock files under the repo so settings reload cleanly"),
    ]

    host = kit.workbench(ctx, title="Overview", snapshot=snapshot, verdict=verdict, detail=detail,
                         secondary=secondary, machinery=machinery, busy=busy, chip_slots=5)

    # Disable every action while a mutating/subprocess op runs (mirror Health).
    from PyQt5.QtWidgets import QPushButton
    _buttons = [b for b in host.findChildren(QPushButton) if not b.text().startswith(("▸", "▾"))]

    def _apply_enablement():
        on = not busy["on"]
        for b in _buttons:
            try:
                b.setEnabled(on)
            except RuntimeError:                           # a button deleted by a rebuild
                pass

    busy.on_change = _apply_enablement

    # Test / drive seams.
    host._snapshot = snapshot
    host._readiness = _readiness
    host._audit = _audit
    host._next_step = _next_step
    host._run_check = _run_check
    host._checks = checks
    host._clear_cache = _clear_cache
    host._open_in_kicad = _open_in_kicad
    host._reveal = _reveal
    return host


# ── Health — audit findings + ▶ Prepare This Project, on kit.workbench ────────
def _health_panel(ctx, state) -> QWidget:
    """Project health, rebuilt onto the ``kit.workbench`` recipe: a findings-count verdict
    band (quiet when healthy) → the audit findings table refreshed IN PLACE → the single
    accent ▶ **Prepare This Project** (Fix-All: annotate + fill-from-Library, previewed
    through the rich ``FillPreviewDialog`` via the recipe's ``preview`` hook, then a re-audit
    with a before→after report) → Run ERC / Run DRC / Restore-Last-Prepare secondaries → a
    Markdown audit-report export. Returns the ``host`` body (the caller wraps it in
    ``W.scroll_body``).

    Parity: surfaces ``audit_report_markdown`` (the export), ``autofixable`` (the "N
    Auto-Fixable" chip in the findings summary) and ``autofixable_kinds`` (the annotate op's
    pre-check). Two audits per refresh (verdict + detail) are memoised by sheet mtime so a
    project switch reads each sheet once."""
    log = getattr(getattr(ctx, "services", None), "log", None)

    def _log(line):
        if callable(log):
            log(str(line))

    # Holders: the plan + annotate_n stashed between the ▶ audit and its preview/apply, the
    # backups from the last Prepare (for Restore), and an mtime-keyed audit memo so the
    # verdict and the detail don't each re-read every sheet on the same refresh.
    prep: dict = {}
    # Restore/Undo holder. `originals` = each sheet's PRE-Prepare text; `prepared` = its
    # POST-Prepare text; `state` toggles "prepared" <-> "restored" so Restore and Undo
    # Restore are exact inverses (write originals / write prepared) without re-running
    # Prepare. `diff` carries the before/after audit itemization for the Last Prepare view.
    last_prepare: dict = {"originals": {}, "prepared": {}, "state": None, "diff": None}
    memo: dict = {}
    busy = kit.BusyDict()

    _ANNOTATE_KEY = "\x00annotate"

    def _op_key(ref, prop):
        return f"{ref}\x1f{prop}"

    def snapshot() -> dict:
        """The GUI-thread selection dict every worker reads (workers never touch a widget):
        the current project's sheets + boards + the Library dirs, re-derived from state so a
        project switch takes effect."""
        root = state.root_schematic()
        return {"schs": [str(s) for s in state.schematics()],
                "boards": [str(b) for b in state.boards()],
                "root_sch": str(root) if root else None,
                "fp_dirs": [ctx.cfg["FootprintLib"]] if ctx.cfg.get("FootprintLib") else None,
                "mdl_dirs": [ctx.cfg["ModelLib"]] if ctx.cfg.get("ModelLib") else None,
                "name": state.project.name if state.project else "project"}

    def _audit(snap):
        """Audit every sheet, memoised on (sheet, mtime) so the verdict + detail passes of a
        single refresh read each file once; a Prepare/Restore write bumps the mtime (and
        clears the memo) so the next audit reflects the change."""
        schs = snap["schs"]
        try:
            sig = (tuple((s, Path(s).stat().st_mtime_ns) for s in schs),
                   tuple(snap["fp_dirs"] or ()), tuple(snap["mdl_dirs"] or ()),
                   libfill.library_index_signature(ctx.cfg))   # swap/edit the library -> re-audit
        except OSError:
            sig = None
        if sig is not None and memo.get("sig") == sig:
            return memo["res"]
        res = phealth.audit_project(schs, snap["fp_dirs"], snap["mdl_dirs"])
        if sig is not None:
            memo["sig"] = sig
            memo["res"] = res
        return res

    cmemo: dict = {}

    def _completion(snap):
        """Roll up the schematic-scoped completion passport (nd_library_fill.
        project_completion) over every placed component, memoised on the SAME
        signature as _audit (sheet mtimes PLUS the Library index signature) so a
        library swap/edit re-derives the passport instead of reusing a stale fill."""
        schs = snap["schs"]
        try:
            sig = (tuple((s, Path(s).stat().st_mtime_ns) for s in schs),
                   libfill.library_index_signature(ctx.cfg))
        except OSError:
            sig = None
        if sig is not None and cmemo.get("sig") == sig:
            return cmemo["res"]
        comps = []
        for sch in schs:
            try:
                comps.extend(phealth.schematic_components(sch))
            except Exception:  # noqa: BLE001 — a missing/unreadable sheet just isn't counted
                pass
        res = libfill.project_completion(comps, ctx.cfg)   # cfg -> include the 3D-model dimension
        if sig is not None:
            cmemo["sig"] = sig
            cmemo["res"] = res
        return res

    # ── verdict: findings health + how many components are fully filled ─────────────────
    def verdict(snap):
        if not snap["schs"]:
            return W.VerdictState(kind="mut", title="No Schematic",
                                  subtitle="This project has no schematic sheet to audit.")
        res = _audit(snap)
        bs = res.get("counts", {}).get("by_severity", {})
        errs, warns, notes = bs.get("error", 0), bs.get("warning", 0), bs.get("info", 0)
        total = errs + warns + notes
        comp = _completion(snap)
        n, m = comp["complete"], comp["total"]
        incomplete = m - n
        chips = []
        if incomplete:
            chips.append(("Incomplete", str(incomplete), "warn"))
        if errs:
            chips.append(("Errors", str(errs), "err"))
        if warns:
            chips.append(("Warnings", str(warns), "warn"))
        if notes:
            chips.append(("Notes", str(notes), "mut"))
        sub = f"{n}/{m} components fully filled"
        # Green only when every component is fully filled AND there is no error/warning —
        # the owner's "every component fully filled" bar. Notes stay benign.
        if incomplete == 0 and errs + warns == 0:
            title = "Ready To Build" if m else "No Components"
            return W.VerdictState(kind="ok", title=title, subtitle=sub, chips=chips)
        if errs + warns == 0:
            # Only completeness gaps (no ERC/footprint errors): amber, not red.
            return W.VerdictState(kind="warn", title=f"{incomplete} To Complete",
                                  subtitle=sub, chips=chips)
        kind = "err" if errs else "warn"
        return W.VerdictState(kind=kind, title=f"{plural(total, 'Finding')}",
                              subtitle=sub, chips=chips)

    # ── detail: the findings table, built once, repopulated on refresh ──────────────────
    # The active breakdown filter (a bucket label from _kind_breakdown, or None = show all)
    # persists across fills so a re-audit doesn't drop the user's chosen focus. Panel-scoped
    # so it survives a fill(); a project switch rebuilds the whole panel and resets it.
    fstate = {"bucket": None, "buckets": [], "findings": []}
    _filter_api = {}                                       # detail() drops _on_chip here for the host seam

    def detail(snap, handle):
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.sp("path"))
        if not snap["schs"]:
            col.addWidget(W.eyebrow("Findings"))
            col.addWidget(W.static_label("No schematic sheet in this project to audit.", "dim"))
            return body, (lambda s: None)
        col.addWidget(W.eyebrow("Findings"))
        summary = QHBoxLayout()
        summary.setSpacing(T.sp("sm"))
        summary_w = QWidget()
        summary_w.setLayout(summary)
        col.addWidget(summary_w)
        # Breakdown chips (one per finding kind present) — clickable to filter the table.
        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 0, 0, 0)
        chips_row.setSpacing(6)
        chips_host = QWidget()
        chips_host.setLayout(chips_row)
        col.addWidget(chips_host)
        table_box = QVBoxLayout()
        table_box.setContentsMargins(0, 0, 0, 0)
        table_box.setSpacing(0)
        table_host = QWidget()
        table_host.setLayout(table_box)
        col.addWidget(table_host, 1)
        # The "Last Prepare" before/after itemization — hidden until a Prepare has run.
        prep_box = QVBoxLayout()
        prep_box.setContentsMargins(0, 0, 0, 0)
        prep_box.setSpacing(T.sp("sm"))
        prep_host = QWidget()
        prep_host.setLayout(prep_box)
        col.addWidget(prep_host)

        def _render_table():
            """Repaint the findings table from the cached audit, honouring the active
            breakdown filter. Called by fill() and by a chip click (no re-audit)."""
            clear_layout(table_box)
            findings = fstate["findings"]
            bucket = fstate["bucket"]
            match_kinds = None
            if bucket is not None:
                match_kinds = next((b["kinds"] for b in fstate["buckets"] if b["label"] == bucket), set())
            rows = []
            for f in findings:
                raw = str(f.get("kind", ""))
                if match_kinds is not None and raw not in match_kinds:
                    continue
                kind = _KIND_LABEL.get(raw, raw.replace("_", " ").title())
                sev = f.get("severity", "info")
                rows.append([W.body(str(f.get("ref", "")), mono=True), W.body(kind),
                             W.body(sentence(f.get("detail", ""))),
                             W.tag(sev.title(), _SEV.get(sev, "mut"))])
            if not findings:
                table_box.addWidget(W.empty_state("No Issues Found", glyph=icons.GLYPHS["check"],
                                                  sub="Every component is healthy."))
            elif not rows:
                table_box.addWidget(W.empty_state("No Matches", glyph=icons.GLYPHS.get("search", ""),
                                                  sub=f"No findings under {bucket}. Click the chip again to clear."))
            else:
                table_box.addWidget(W.data_table(["Reference", "Kind", "Detail", "Severity"], rows,
                                                 stretch_col=2, wrap=True), 1)

        def _on_chip(bucket_label):
            fstate["bucket"] = None if fstate["bucket"] == bucket_label else bucket_label
            _render_chips()
            _render_table()

        def _render_chips():
            clear_layout(chips_row)
            buckets = fstate["buckets"]
            if not buckets:
                chips_host.setVisible(False)
                return
            chips_host.setVisible(True)
            for b in buckets:
                active = fstate["bucket"] == b["label"]
                chips_row.addWidget(W.toggle_chip(
                    f"{b['label']}: {b['count']}", b["tint"], active=active,
                    on_click=(lambda lbl=b["label"]: _on_chip(lbl)),
                    tip=f"Show only the {b['label']} findings ({b['count']}). Click again to clear."))
            chips_row.addStretch(1)

        def _render_prepare_diff():
            """Show the before/after itemization of the last Prepare (per-kind counts +
            click a row to reveal the exact refs fixed). Hidden until a Prepare runs."""
            clear_layout(prep_box)
            diff = last_prepare.get("diff")
            if not diff or not diff.get("rows"):
                prep_host.setVisible(False)
                return
            prep_host.setVisible(True)
            head = QHBoxLayout(); head.setSpacing(T.sp("sm"))
            head.addWidget(W.eyebrow("Last Prepare"))
            delta = diff["after_total"] - diff["before_total"]
            head.addWidget(W.tag(f"{diff['before_total']} → {diff['after_total']} ({delta:+d})",
                                 "ok" if delta < 0 else "mut"))
            head.addStretch(1)
            hw = QWidget(); hw.setLayout(head); prep_box.addWidget(hw)
            for r in diff["rows"]:
                prep_box.addWidget(_prepare_diff_row(r))

        def fill(s):
            res = _audit(s)
            bs = res.get("counts", {}).get("by_severity", {})
            findings = res.get("findings", [])
            auto = phealth.autofixable(findings)          # parity: surfaces autofixable()
            clear_layout(summary)
            tags = []
            sheets = res.get("sheets", 1)
            if sheets > 1:
                tags.append((f"{sheets} Sheets", "mut"))
            tags += [(f"{res.get('components', 0)} Components", "mut"),
                     (f"{res.get('healthy', 0)} Healthy", "mut"),
                     (f"{bs.get('error', 0)} Errors", "err"),
                     (f"{bs.get('warning', 0)} Warnings", "warn"),
                     (f"{bs.get('info', 0)} Notes", "mut")]
            if auto:
                tags.append((f"{len(auto)} Auto-Fixable", "ok"))
            for txt, kind in tags:
                summary.addWidget(W.tag(txt, kind))
            summary.addStretch(1)
            # Recompute the breakdown; drop a stale active filter whose bucket vanished.
            fstate["findings"] = findings
            fstate["buckets"] = _kind_breakdown(findings)
            if fstate["bucket"] not in {b["label"] for b in fstate["buckets"]}:
                fstate["bucket"] = None
            _render_chips()
            _render_table()
            _render_prepare_diff()

        _filter_api["apply"] = _on_chip                   # drive/test seam: click a chip by label
        return body, fill

    def _prepare_diff_row(r):
        """One before/after row (kind → before/after/Δ) that toggles a dim sub-line naming
        the exact refs fixed. A no-op click when nothing was fixed for that kind."""
        card = QWidget()
        cv = QVBoxLayout(card); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(2)
        line = QHBoxLayout(); line.setSpacing(T.sp("sm"))
        fixed = r["fixed"]
        n_fixed = len(fixed)
        head = W.toggle_chip(r["label"], "ok" if r["delta"] < 0 else "mut",
                             on_click=None,
                             tip=(f"{plural(n_fixed, 'reference')} fixed. Click to list them."
                                  if n_fixed else "No references fixed for this kind."))
        line.addWidget(head)
        line.addWidget(W.body(f"{r['before']} → {r['after']}  ({r['delta']:+d})", dim=True))
        line.addStretch(1)
        lw = QWidget(); lw.setLayout(line); cv.addWidget(lw)
        refs_lab = W.body("Fixed: " + ", ".join(fixed), dim=True, mono=True) if fixed else None
        if refs_lab is not None:
            refs_lab.setVisible(False)
            refs_lab.setWordWrap(True)
            cv.addWidget(refs_lab)

            def _toggle(_=False):
                refs_lab.setVisible(not refs_lab.isVisible())
            head.clicked.connect(_toggle)
        return card

    # ── the ▶ Prepare This Project flow (audit → rich preview → apply → re-audit) ────────
    def _build_fill_plan(schs):
        """Index the Library and build a fill plan over every sheet's real components.
        `sheet_of` maps each ref to the sheet it was found on so the writer knows which file
        to touch. Off-GUI-safe. Returns an nd_library_fill FillPlan."""
        idx = libfill.library_parts(ctx.cfg)
        components, sheet_of = [], {}
        for sch in schs:
            for comp in phealth.schematic_components(sch):
                components.append(comp)
                sheet_of[comp["ref"]] = str(sch)          # last sheet wins on a dup ref
        prep["components"] = components                    # for the preview's group/manual sections
        prep["sheet_of"] = sheet_of
        plan = libfill.build_fill_plan(components, idx, sheet_of)
        # Enrich identity fields from the distributor (Mouser/LCSC) for any component that
        # carries a real MPN but is still missing manufacturer/datasheet/description. This
        # does network lookups, but we are already OFF the GUI thread here (the flow's audit
        # phase); it degrades to library-only with no key and never fails the plan. Skipped
        # under offscreen (drive/CI) so the harness never touches the network.
        from ..util import _headless
        if not _headless():
            try:
                libfill.enrich_plan(plan, components, ctx.cfg, sheet_of)
            except Exception:  # noqa: BLE001
                pass
        return plan

    def _prepare_audit(snap):
        """OFF-thread: build the Library fill plan + the annotate dry-run, stash them, and
        return the ops (the annotate op + one op per proposed field). Pre-check: annotate is
        gated by ``autofixable_kinds`` (parity); a field is safe only on an exact blank-fill."""
        schs = snap["schs"]
        plan = _build_fill_plan(schs)
        annotate_n = phealth.annotate_project(schs, apply=False)
        prep["plan"] = plan
        prep["annotate_n"] = annotate_n
        auto_annotate = "unannotated" in phealth.autofixable_kinds()
        ops = []
        if annotate_n:
            ops.append({"key": _ANNOTATE_KEY,
                        "label": f"Annotate {plural(annotate_n, 'unannotated reference')}",
                        "detail": "Assign the next free designator to each unannotated symbol.",
                        "safe": bool(auto_annotate)})
        for item in plan.get("items", []):
            ref = item["ref"]
            conf = (item.get("match") or {}).get("confidence", "none")
            for ch in item.get("changes", []):
                old = ch.get("old", "") or "∅"
                over = ch.get("kind") == "overwrite"
                src = ch.get("source", "library")
                tag = "  (from Mouser)" if src == "mouser" else ("  (overwrite)" if over else "")
                # Auto-select an exact-library fill or a distributor fill of a blank field;
                # a fuzzy "verify" match or an overwrite always needs the user's tick.
                safe = ch.get("kind") == "fill" and (conf == "exact" or src == "mouser")
                ops.append({"key": _op_key(ref, ch["prop"]),
                            "label": f"{ref} · {ch['prop']}",
                            "detail": f"{old} → {ch.get('new', '')}" + tag,
                            "safe": bool(safe)})
        return ops

    def _prepare_intro(snap, ops):
        n = len(ops)
        return (f"{plural(n, 'proposed change')} for {snap['name']}. "
                "Exact blank fills are pre-checked; overwrites and fuzzy matches need your check.")

    def _prepare_preview_async(host, label, intro, ops, cont):
        """The recipe's non-blocking ``preview_async`` hook: the rich ``FillPreviewDialog``
        (confidence chips, per-field old→new deltas, library pickers, Only-Exact bulk) opens
        as an in-app SUBPAGE, never a modal OS window. On accept it maps the checked pairs back
        to keys and continues the flow; Back / cancel cancels. HEADLESS: no subpage — continue
        immediately with the safe/pre-checked keys so an offscreen drive runs the whole flow
        (byte-identical to the old sync hook, and the dialog is never built → _fill_dialog None)."""
        from ..util import _headless
        from ..kit import open_subpage
        prep["links"] = {}                          # reset the per-run link/add directives
        if _headless():
            cont([op["key"] for op in ops if op.get("safe")]); return
        dlg = FillPreviewDialog(prep.get("plan") or {"items": []}, prep.get("annotate_n", 0),
                                cfg=ctx.cfg, parent=host.window(),
                                components=prep.get("components"), sheet_of=prep.get("sheet_of"))
        host._fill_dialog = dlg

        def _done(result):
            if result != QDialog.Accepted or not dlg.applied:
                cont(None); return
            prep["links"] = dlg.library_links()     # {ref: link/add directive} for _prepare_apply
            keys = [_op_key(r, p) for (r, p) in dlg.selected()]
            if dlg.annotate_selected():
                keys.append(_ANNOTATE_KEY)
            cont(keys)
        open_subpage(ctx, dlg, label.replace("▶", "").strip(), on_result=_done)

    def _prepare_apply(snap, keys):
        """OFF-thread: write the selected subset (fills + optional annotate) with .bak
        backups, then re-audit and report before→after. Records the backups for Restore."""
        plan = prep.get("plan") or {"items": []}
        selected = set()
        do_annotate = False
        for k in keys:
            if k == _ANNOTATE_KEY:
                do_annotate = True
            else:
                ref, _, prop = k.partition("\x1f")
                selected.add((ref, prop))
        # ERC/DRC before-state: the cached result the user last ran (if any). We prove the
        # fix by re-running below only when there WAS a prior check + kicad-cli is present,
        # so Prepare never surprise-runs a subprocess the user never asked for.
        erc_before = (state.checks() or {}).get("erc")
        drc_before = (state.checks() or {}).get("drc")
        before = _audit(snap)
        b0 = before.get("counts", {}).get("by_severity", {})
        # Snapshot each sheet's ORIGINAL text BEFORE any writer runs. Fill and annotate each write
        # <sheet>.bak right before replacing the file, so on a sheet that gets BOTH a fill and an
        # annotate the second writer's .bak captures the FIRST writer's intermediate — not the
        # pre-Prepare original. Restore must roll back to THIS captured original (else it would
        # keep the fills and only undo the annotation, breaking the "undoes this" promise).
        originals = {}
        for sch in snap["schs"]:
            try:
                originals[sch] = Path(sch).read_text(encoding="utf-8")
            except OSError:
                pass
        res = libfill.apply_fill_plan(plan, selected, ctx.cfg, log)
        # Library-only links (owner requirement): each unmatched / grouped component was
        # pointed at an existing library part (SELECT) or a newly-added one (ADD). Run the
        # KiCad-correct link now — lib_id + footprint + identity + persisted 3D-model line —
        # so placing the symbol pulls the right footprint onto the PCB and the right 3D model
        # in the viewer, not just metadata fields.
        links = prep.get("links") or {}
        link_summary = {"linked": 0, "added": 0, "models": 0}
        sheet_of = prep.get("sheet_of") or {}
        # An ADD directive is shared by every ref of a passive group; create each distinct
        # (footprint, identity, name) library part ONCE, then link every ref to it.
        add_cache = {}
        for ref, req in links.items():
            sheet = sheet_of.get(ref)
            if not sheet:
                res.setdefault("errors", []).append(f"{ref}: no sheet for library link")
                continue
            try:
                if req.get("kind") == "add":
                    ck = (req.get("footprint"), req.get("name"),
                          tuple(sorted((req.get("identity") or {}).items())))
                    part = add_cache.get(ck)
                    if part is None:
                        added = libfill.add_library_part(
                            ctx.cfg, req["footprint"], name=req.get("name"),
                            identity=req.get("identity"), log=log)
                        if added.get("errors"):
                            res.setdefault("errors", []).extend(added["errors"])
                        if not added.get("name"):
                            continue
                        # Refetch the just-created part's record so link fills its identity.
                        idx = libfill.library_parts(ctx.cfg)
                        part = next((p for p in idx if p.get("name") == added["name"]), None)
                        if part is None:
                            part = {"name": added["name"], "footprint": added["footprint"]}
                        add_cache[ck] = part
                        link_summary["added"] += 1
                    lr = libfill.link_placed_component(ctx.cfg, sheet, ref, part, log)
                elif req.get("kind") == "link":
                    lr = libfill.link_placed_component(ctx.cfg, sheet, ref,
                                                       req.get("lib_part") or {}, log)
                else:
                    continue
            except Exception as e:  # noqa: BLE001
                res.setdefault("errors", []).append(f"{ref} link: {e}")
                continue
            if lr.get("errors"):
                res.setdefault("errors", []).extend(lr["errors"])
            if lr.get("written"):
                link_summary["linked"] += 1
                if sheet not in res.setdefault("written_files", []):
                    res["written_files"].append(sheet)
                for b in lr.get("backups", []):
                    if b not in res.setdefault("backups", []):
                        res["backups"].append(b)
                if lr.get("model"):
                    link_summary["models"] += 1
        annotated = 0
        if do_annotate:
            annotated = phealth.annotate_project(snap["schs"], apply=True)
        # Count the sheets a writer actually backed up (for the report line only).
        baks = list(res.get("backups", []))
        for sch in snap["schs"]:
            bak = sch + ".bak"
            if do_annotate and bak not in baks and Path(bak).exists():
                baks.append(bak)
        memo.pop("sig", None)                              # files changed → fresh audit
        after = _audit(snap)
        b1 = after.get("counts", {}).get("by_severity", {})
        # Capture each sheet's POST-Prepare ("prepared") text so Restore is reversible:
        # Undo Restore re-writes THIS without re-running Prepare. Read after every writer.
        prepared = {}
        for sch in snap["schs"]:
            try:
                prepared[sch] = Path(sch).read_text(encoding="utf-8")
            except OSError:
                pass
        wrote = bool(res.get("fields_written", 0) or annotated
                     or link_summary["linked"] or link_summary["added"])
        # Update the reversibility holder as ONE unit, only on a real write. A later Prepare
        # that writes nothing must NOT overwrite originals/prepared with the current on-disk
        # state (that would be the already-prepared text, collapsing the round-trip so Restore
        # becomes a no-op and the true pre-Prepare original is lost).
        if wrote:
            last_prepare["originals"] = originals
            last_prepare["prepared"] = prepared
            last_prepare["state"] = "prepared"
            last_prepare["diff"] = _audit_diff(before, after)
        # Only a real write makes the cached ERC/DRC stale — if nothing was written the
        # schematic is unchanged, so a prior check still stands (don't drop it needlessly).
        check_lines = []
        if wrote:
            # Invalidate the SHARED cache (Overview's readiness reads it); then, only if the
            # user had already run a check and kicad-cli is present, RE-RUN it here (we are
            # off-thread) for real before→after proof and re-cache the fresh result.
            state.invalidate_checks()
            cli = _kicad_cli()
            for kind, prior in (("erc", erc_before), ("drc", drc_before)):
                if not prior or prior.get("error") or not cli:
                    if prior and not prior.get("error"):
                        nm = _CHECK_NAME.get(kind, kind)
                        check_lines.append(f"{nm} was {plural(prior.get('errors', 0), 'error')} / "
                                           f"{plural(prior.get('warnings', 0), 'warning')} before Prepare. "
                                           f"Re-run {nm} to confirm the fix.")
                    continue
                target = snap["root_sch"] if kind == "erc" else (snap["boards"][0] if snap["boards"] else None)
                if not target:
                    continue
                fn = kchecks.run_erc if kind == "erc" else kchecks.run_drc
                r = fn(target, cli)
                nm = _CHECK_NAME.get(kind, kind)
                if r and not r.get("error") and not r.get("returncode"):
                    s = r.get("summary", {})
                    state.set_check(kind, s)                # fresh result -> readiness reflects the fix
                    check_lines.append(f"{nm}: {plural(prior.get('errors', 0), 'error')} → "
                                       f"{plural(s.get('errors', 0), 'error')} "
                                       f"({plural(s.get('warnings', 0), 'warning')} now).")

        filled = res.get("fields_written", 0)
        comps = res.get("components_changed", 0)
        done, errors = [], list(res.get("errors", []))
        if filled:
            done.append(f"Filled {plural(filled, 'field')} across {plural(comps, 'component')}.")
        if link_summary["added"]:
            done.append(f"Added {plural(link_summary['added'], 'new part')} to the Library.")
        if link_summary["linked"]:
            extra = (f" ({plural(link_summary['models'], '3D model')} attached)"
                     if link_summary["models"] else "")
            done.append(f"Linked {plural(link_summary['linked'], 'component')} to a library "
                        f"symbol: lib_id + footprint written{extra}.")
            # Honest scope note (owner: never fake a sync). The link is written into the
            # SCHEMATIC + the library files, which is what makes KiCad resolve the right
            # footprint + 3D model when the symbol is placed. Propagating the new footprint
            # onto an EXISTING .kicad_pcb needs KiCad's "Update PCB from Schematic" (an IPC /
            # GUI action, not offered by kicad-cli); run that in KiCad once to sync the board.
            if snap.get("boards"):
                done.append("Board sync: open the PCB and run Tools ▸ Update PCB from "
                            "Schematic to pull the new footprints onto the existing board "
                            "(kicad-cli has no offline equivalent).")
        if annotated:
            done.append(f"Annotated {plural(annotated, 'reference')}.")
        if wrote and last_prepare["diff"] and last_prepare["diff"]["rows"]:
            d = last_prepare["diff"]
            done.append(f"Findings: {d['before_total']} → {d['after_total']} "
                        f"(see the Last Prepare breakdown below for the per-kind before/after).")
        done.extend(check_lines)
        if baks:
            done.append(f"Backups written for {plural(len(baks), 'sheet')}. Restore Last Prepare "
                        "rolls every changed sheet back to its pre-Prepare state; Undo Restore "
                        "re-applies it.")

        def _fmt(b):
            return f"{b.get('error', 0)} errors / {b.get('warning', 0)} warnings / {b.get('info', 0)} notes"

        summary = (f"Prepared {snap['name']}: {_fmt(b0)} → {_fmt(b1)}."
                   if wrote else "Nothing was written.")
        return {"summary": summary, "done": done, "errors": errors}

    prepare_flow = kit.PrimaryFlow(
        label="▶ Prepare Components", audit=_prepare_audit, intro=_prepare_intro,
        apply=_prepare_apply, preview_async=_prepare_preview_async,
        tip="Prepare every component: annotate references + link footprints/models + fill "
            "each component's fields (part number / manufacturer / datasheet) from the "
            "Library and Mouser, previewed field-by-field, then re-audit",
        empty="Nothing to prepare. Every component is annotated and there is nothing the "
              "Library or Mouser can fill.")

    # ── secondary + machinery op runners (busy-gated, off-thread, then refresh) ──────────
    def _run_op(label, work):
        if busy["on"]:
            return
        busy["on"] = True

        def done(line, ok):
            busy["on"] = False
            if line:
                _log(line)
            host._region.handle.refresh()

        run_populate(ctx, work, done, busy=f"{label}...")

    def _report_op(label, work):
        def done(result, ok):
            kit._report(host, label, result if ok else {"errors": ["operation failed"]}, log=log)

        run_populate(ctx, work, done, busy=f"{label}...")

    def _run_check(kind):
        snap = snapshot()
        cli = _kicad_cli()
        if not cli:
            _log("kicad-cli not found on PATH. Install KiCad or add it to PATH.")
            return
        name = _CHECK_NAME.get(kind, kind)
        target = snap["root_sch"] if kind == "erc" else (snap["boards"][0] if snap["boards"] else None)
        if not target:
            _log(f"No {'schematic' if kind == 'erc' else 'board'} to check.")
            return

        def work():
            fn = kchecks.run_erc if kind == "erc" else kchecks.run_drc
            res = fn(target, cli)
            if not res:
                return {"errors": [f"{name} failed."]}
            if res.get("error") or res.get("returncode"):
                msg = str(res.get("error") or f"kicad-cli exited {res.get('returncode')}")
                return {"errors": [f"{name} could not run: {sentence(msg)}"]}
            s = res.get("summary", {})
            state.set_check(kind, s)                        # SHARED cache: folds into Overview
            # readiness AND gives the next Prepare a real before->after baseline to diff against.
            findings = res.get("findings", [])
            missing = [{"item": str(f.get("rule", "")), "why": sentence(f.get("message", "")),
                        "how_to_fix": str(f.get("where", "")) or "See this location in the design."}
                       for f in findings[:200]]
            return {"summary": f"{name}: {s.get('errors', 0)} errors / {s.get('warnings', 0)} "
                               f"warnings / {s.get('exclusion', 0)} exclusions.",
                    "missing": missing}

        _report_op(name, work)

    def _rewrite_sheets(mapping, label):
        """Write each sheet in `mapping` (path -> desired text) only where it differs from
        disk (idempotent, and a no-op sheet isn't backed up needlessly). Returns (restored
        names, errors). Shared by Restore and Undo Restore so they are exact inverses."""
        restored, errors = [], []
        for sch, text in mapping.items():
            p = Path(sch)
            try:
                if p.read_text(encoding="utf-8") == text:
                    continue                               # already at the target — nothing to write
                p.write_text(text, encoding="utf-8")
                restored.append(p.name)
            except OSError as e:  # noqa: BLE001
                errors.append(f"{p.name}: {e}")
        return restored, errors

    def _restore_prepare():
        """Roll every changed sheet back to its PRE-Prepare text. Reversible: it does NOT
        discard the prepared text, it flips the holder to "restored" so Undo Restore can
        re-apply the prepared state without re-running Prepare."""
        if last_prepare.get("state") != "prepared":
            _log("No prepare to restore. Run Prepare Components first."
                 if last_prepare.get("state") is None else "Already restored. Use Undo Restore to re-apply.")
            return
        originals = dict(last_prepare.get("originals") or {})
        if not originals:
            _log("No prepare to restore. Run Prepare Components first.")
            return

        def work():
            restored, errors = _rewrite_sheets(originals, "restore")
            memo.pop("sig", None)
            state.invalidate_checks()                      # sheets changed → ERC/DRC stale
            # Flip to "restored" ONLY on a clean pass. A partial multi-sheet failure leaves the
            # holder in "prepared" so the state never claims a rollback the disk didn't fully
            # get — the user sees the error and can retry (Undo stays disabled).
            if not errors:
                last_prepare["state"] = "restored"         # reversible: prepared text is kept
            msg = (f"Restored {plural(len(restored), 'sheet')} to the pre-Prepare state. "
                   "Undo Restore re-applies the Prepare."
                   if restored else "Nothing to restore. The sheets already match the pre-Prepare state.")
            if errors:
                msg += " Some sheets could not be restored: " + "; ".join(errors)
            return msg

        _run_op("Restore Last Prepare", work)

    def _undo_restore():
        """Re-apply the last Prepare after a Restore — write the captured POST-Prepare text
        back, without re-running the whole flow. The inverse of Restore; flips back to
        "prepared" so the pair can toggle."""
        if last_prepare.get("state") != "restored":
            _log("Nothing to undo. Run Restore Last Prepare first." if last_prepare.get("state") != "prepared"
                 else "Already at the prepared state. Use Restore Last Prepare to roll back.")
            return
        prepared = dict(last_prepare.get("prepared") or {})
        if not prepared:
            _log("No prepared state captured to re-apply.")
            return

        def work():
            restored, errors = _rewrite_sheets(prepared, "undo")
            memo.pop("sig", None)
            state.invalidate_checks()
            # Flip back to "prepared" ONLY on a clean pass (mirror Restore); a partial failure
            # keeps the holder in "restored" so state never over-claims the re-apply.
            if not errors:
                last_prepare["state"] = "prepared"
            msg = (f"Re-applied the Prepare to {plural(len(restored), 'sheet')}. "
                   "Restore Last Prepare rolls it back again."
                   if restored else "Nothing to re-apply. The sheets already match the prepared state.")
            if errors:
                msg += " Some sheets could not be re-applied: " + "; ".join(errors)
            return msg

        _run_op("Undo Restore", work)

    def _prepare_diff_markdown(snap):
        """A shareable before/after Markdown of the last Prepare: a per-kind count table
        (before / after / Δ) plus the exact references fixed under each kind. Honest when
        no Prepare has run yet."""
        diff = last_prepare.get("diff")
        name = (snap or {}).get("name", "project")
        if not diff or not diff.get("rows"):
            return (f"# Prepare Diff: {name}\n\n"
                    "No Prepare has been run in this session yet, so there is no before/after to "
                    "report. Run the Prepare Components flow, then export this again.\n")
        lines = [f"# Prepare Diff: {name}", "",
                 f"Findings: **{diff['before_total']} → {diff['after_total']}** "
                 f"({diff['after_total'] - diff['before_total']:+d}).", "",
                 "| Finding | Before | After | Δ |", "| --- | ---: | ---: | ---: |"]
        for r in diff["rows"]:
            lines.append(f"| {r['label']} | {r['before']} | {r['after']} | {r['delta']:+d} |")
        fixed_any = [r for r in diff["rows"] if r["fixed"]]
        if fixed_any:
            lines += ["", "## References fixed", ""]
            for r in fixed_any:
                lines.append(f"- **{r['label']}** ({len(r['fixed'])}): {', '.join(r['fixed'])}")
        return "\n".join(lines) + "\n"

    exports = [
        kit.export_action("Audit Report (Markdown)...",
                          lambda snap: phealth.audit_report_markdown(_audit(snap)),
                          lambda snap: f"{snap['name']}-health.md",
                          filt="Markdown (*.md)",
                          tip="A shareable Markdown report of the current audit findings"),
    ]
    exports.append(
        kit.export_action("Prepare Diff (Markdown)...",
                          lambda snap: _prepare_diff_markdown(snap),
                          lambda snap: f"{snap['name']}-prepare-diff.md",
                          filt="Markdown (*.md)",
                          tip="A before/after Markdown of the last Prepare: per-kind counts and "
                              "the exact references it fixed"))
    secondary = [
        kit.action("Run Electrical Rules Check", lambda: _run_check("erc"),
                   tip="Run KiCad's ERC on the root schematic (via kicad-cli); the result folds into readiness"),
        kit.action("Run Design Rules Check", lambda: _run_check("drc"),
                   tip="Run KiCad's DRC on the board (via kicad-cli); the result folds into readiness"),
        kit.action("Restore Last Prepare", _restore_prepare,
                   tip="Roll every changed sheet back to its pre-Prepare state (reversible)"),
        kit.action("Undo Restore", _undo_restore,
                   tip="Re-apply the last Prepare after a Restore, without re-running the flow"),
    ]

    host = kit.workbench(ctx, title="Health", snapshot=snapshot, verdict=verdict, detail=detail,
                         primary=prepare_flow, secondary=secondary, exports=exports, busy=busy)

    # Disable every action while a mutating op runs (no overlapping writes); the ▸/▾
    # collapsible chevrons are skipped (the busy gate never disables them).
    from PyQt5.QtWidgets import QPushButton
    _buttons = [b for b in host.findChildren(QPushButton) if not b.text().startswith(("▸", "▾"))]
    _btn_restore = next((b for b in _buttons if b.text() == "Restore Last Prepare"), None)
    _btn_undo = next((b for b in _buttons if b.text() == "Undo Restore"), None)

    def _sync_restore_buttons():
        """Restore is live only in the "prepared" state; Undo Restore only in "restored".
        Both are additionally gated off while any op runs (the busy sweep below)."""
        on = not busy["on"]
        st = last_prepare.get("state")
        if _btn_restore is not None:
            try:
                _btn_restore.setEnabled(on and st == "prepared")
            except RuntimeError:
                pass
        if _btn_undo is not None:
            try:
                _btn_undo.setEnabled(on and st == "restored")
            except RuntimeError:
                pass

    def _apply_enablement():
        on = not busy["on"]
        for b in _buttons:
            try:
                b.setEnabled(on)
            except RuntimeError:                           # a button deleted by a rebuild
                pass
        _sync_restore_buttons()                            # Restore/Undo also honour the holder state

    busy.on_change = _apply_enablement
    _sync_restore_buttons()                                # initial: neither is live until a Prepare

    # A guarded ▶ so a re-drive / a click during a secondary op can't start a second flow.
    _raw_run = host._run_primary

    def _guarded_run():
        if busy["on"]:
            return
        _raw_run()

    host._run_primary = _guarded_run

    # Test / drive seams.
    host._snapshot = snapshot
    host._audit = _audit
    host._prepare_audit = _prepare_audit
    host._prepare_apply = _prepare_apply
    host._prepare_flow = prepare_flow
    host._prep = prep                                      # {plan, annotate_n} after _prepare_audit
    host._run_check = _run_check
    host._restore_prepare = _restore_prepare
    host._undo_restore = _undo_restore
    host._prepare_diff_markdown = _prepare_diff_markdown
    host._sync_restore_buttons = _sync_restore_buttons
    host._btn_restore = _btn_restore
    host._btn_undo = _btn_undo
    host._fill_dialog = None
    host._fix_all = _guarded_run                           # legacy alias (drives ▶ Prepare)
    host._verdict_of = verdict                             # test seam (readiness classification)
    host._last_prepare = last_prepare                      # test seam (captured originals)
    host._apply_findings_filter = lambda bucket: (_filter_api.get("apply") or (lambda _b: None))(bucket)
    host._findings_filter = fstate                         # {"bucket": str|None, "buckets": [...], ...}
    return host


# ── BOM (real bom_from_kicad_schematic / consolidated_bom) ───────────────────
def _consolidated_boards(projects, selected_names):
    """Build the {project_name: [schematics]} map consolidated_bom expects from
    only the CHOSEN subset of projects (selected_names). Pure + import-light so a
    test can exercise the subset logic without a GUI."""
    chosen = set(selected_names)
    return {p.name: nd_wizard.list_schematics(p) for p in projects if p.name in chosen}


def _bom_panel(ctx, state) -> QWidget:
    """BOM & Procurement, rebuilt onto the ``kit.workbench`` recipe with a CACHED-build
    model. A priced build hits Mouser/LCSC (network) + a Library lookup — far too costly for
    a recipe refresh — so the detail RENDERS a cached ``_last_bom`` holder and never builds on
    a refresh. The pieces:

    - ``snapshot()`` carries the live control values (Boards / Price) + the current sheets.
    - ``verdict()`` reads the cache: cost via ``LibraryManager.bom_cost_summary`` (PARITY —
      this closes that omission), sourcing risk via ``bom_sourcing_risks``, lead via
      ``bom_lead_time``. Quiet ``$X · N lines`` when clean; err/warn chips for NRND / No-Stock
      / Low-Stock / long lead; ``Not Built`` before the first build.
    - ``detail.fill()`` renders the cached BOM table (project vs consolidated modes, the
      dynamic Order + Lead columns) — it never builds. Boards changes re-project LIVE via a
      direct table/summary redraw, guarded off while a diff view is open.
    - **▶ Build and Cost** is the primary flow: builds + prices OFF-thread, writes the holder,
      then ``after=_refresh`` renders it. The initial auto-build stays identity-only (pricing
      off = no network), matching today.
    - **Build Consolidated…** / **Compare To…** are secondaries; the six exports + Copy
      Procurement Summary live in the Export collapsible (each owns its own file dialog /
      binary format, so they ride the recipe's plain-``Action`` export slot).

    Returns the ``host`` body (the caller does NOT wrap it — the recipe's body scrolls itself
    via ``W.scroll_body`` at the feature level). Every legacy ``panel._xxx`` seam the coupled
    tests drive is preserved on ``host``."""
    if not state.project:
        return _no_project()

    log = getattr(getattr(ctx, "services", None), "log", None)

    def _log(line):
        if callable(log):
            log(str(line))

    host = None                        # bound after kit.workbench(); None-safe reads before then
    base = str(ctx.cfg.get("RepoRoot") or ".")
    busy = kit.BusyDict()

    def _money(v):
        p = LM._coerce_price(v)
        return f"${p:,.2f}" if p is not None else "—"

    # ── controls, built ONCE at panel level (so snapshot() can read them regardless of when
    #    detail() mounts them, and a project-switch rebuild reseeds them) ──────────────────
    cb_price = QCheckBox("Price with Mouser")
    cb_price.setToolTip("Look up live Mouser unit price and stock for each part number, "
                        "and total the extended cost. Slower on a big BOM (rate-limited).")
    sp_boards = QSpinBox(); sp_boards.setRange(1, 100000); sp_boards.setValue(1)
    sp_boards.setToolTip("The number of boards you're building. Drives the volume cost "
                         "projection (when priced), the Priced BOM (Volume) export, and the "
                         "Mouser order quantities. The full CSV and JLCPCB exports stay per-board.")
    lab_boards = W.body("Boards", dim=True)
    sp_spares = QSpinBox(); sp_spares.setRange(0, 100); sp_spares.setValue(0)
    sp_spares.setSuffix(" %")
    sp_spares.setToolTip("Extra passives (R/C/L/FB) to add to the Mouser order for SMT "
                         "pick-and-place attrition, rounded up. ICs, connectors and the "
                         "other exports are never padded.")
    lab_spares = W.body("Spares", dim=True)

    # Source view-filter — a read-only lens over the priced project table (never touches the
    # cache or the exports). All / one distributor / Unsourced. Shown only on a priced build.
    _SOURCE_FILTERS = [("All Sources", None), ("Mouser Only", "Mouser"),
                       ("LCSC Only", "LCSC"), ("Unsourced", "")]
    cb_source = QComboBox()
    for lbl, _key in _SOURCE_FILTERS:
        cb_source.addItem(lbl)
    cb_source.setToolTip("Show only the lines carried by one distributor (or the unsourced "
                         "lines). A read-only view filter; the exports are unaffected.")
    lab_source = W.body("Source", dim=True)

    # Export line-scope — narrow what the six exports write, applied through one shared
    # _filter_rows helper so every sheet honours the same predicates. Off = the whole BOM.
    cb_populated = QCheckBox("Populated Only")
    cb_populated.setToolTip("Exports drop lines with no part number and no value "
                            "(blank or placeholder lines a purchasing sheet should never carry).")
    cb_priced_only = QCheckBox("Priced Only")
    cb_priced_only.setToolTip("Exports drop lines with no usable price. Turn on Price with "
                              "Mouser and rebuild to price the lines first.")

    # Project multi-select — only meaningful for the consolidated build. Quiet checkboxes,
    # all checked by default; the consolidated BOM is built from only the ticked projects.
    checks = {}
    picker = QWidget(); pvl = QVBoxLayout(picker); pvl.setContentsMargins(0, 0, 0, 0); pvl.setSpacing(6)
    if len(state.projects) > 1:
        pvl.addWidget(pv.field_label("Include Projects"))
        boxrow = QHBoxLayout(); boxrow.setSpacing(18); boxrow.setContentsMargins(0, 0, 0, 0)
        col = None
        for i, p in enumerate(state.projects):
            if i % 3 == 0:
                col = QVBoxLayout(); col.setSpacing(T.sp("xs")); boxrow.addLayout(col)
            cb = QCheckBox(p.name); cb.setChecked(True); cb.setToolTip(p.as_posix())
            checks[p.name] = cb; col.addWidget(cb)
        boxrow.addStretch(1)
        pvl.addLayout(boxrow)
    picker.setVisible(False)

    # The summary row + the result area (BOM table / diff) — panel-level layouts so every
    # render helper writes into the SAME objects the detail body mounts once.
    summary = QHBoxLayout(); summary.setSpacing(T.sp("sm"))
    summary_w = QWidget(); summary_w.setLayout(summary)
    result = QVBoxLayout()
    result_host = QWidget(); result_host.setLayout(result)

    def snapshot() -> dict:
        """The GUI-thread selection dict every worker reads (workers never touch a widget):
        the current project's sheets, the live Boards count, and whether pricing is on."""
        return {"schs": [str(s) for s in state.schematics()],
                "boards": sp_boards.value(),
                "price": cb_price.isChecked(),
                "name": state.project.name if state.project else "project"}

    # ── verdict: read the cache (cost / risk / lead), quiet-when-clean ───────────────────
    def verdict(snap):
        if not snap["schs"]:
            return W.VerdictState(kind="mut", title="No Schematic",
                                  subtitle="This project has no schematic sheet to build a BOM from.")
        res = getattr(host, "_last_bom", None)
        if not res:
            return W.VerdictState(kind="mut", title="Not Built",
                                  subtitle="Press ▶ Build and Cost to build the bill of materials.")
        rows = res.get("rows", [])
        cost = LM.bom_cost_summary(rows)                  # PARITY: surfaces bom_cost_summary
        risks = LM.bom_sourcing_risks(rows, boards=snap["boards"])
        lead = LM.bom_lead_time(rows)
        chips = []
        if risks["not_active"]:
            chips.append(("NRND/EOL", str(risks["not_active"]), "err"))
        if risks["no_stock"]:
            chips.append(("No Stock", str(risks["no_stock"]), "err"))
        if risks["insufficient_stock"]:
            chips.append(("Low Stock", str(risks["insufficient_stock"]), "warn"))
        if lead["any"] and lead["max_weeks"] >= 12:
            chips.append(("Lead", f"{lead['max_weeks']}wk", "warn"))
        lines = cost["line_count"]
        noun = "line" if lines == 1 else "lines"
        if cost["priced_lines"] > 0:
            title = f"{_money(cost['total_cost'])} · {lines} {noun}"
            sub = f"{cost['priced_lines']} priced, {cost['unpriced_lines']} unpriced"
        else:
            title = f"{lines} {noun} · unpriced"
            sub = "Turn on Price with Mouser and rebuild for costs."
        if risks["not_active"] or risks["no_stock"]:
            kind = "err"
        elif risks["insufficient_stock"] or (lead["any"] and lead["max_weeks"] >= 12):
            kind = "warn"
        else:
            kind = "ok"
        return W.VerdictState(kind=kind, title=title, subtitle=sub, chips=chips)

    # ── detail: controls + summary + result, built ONCE; fill() renders the cache ────────
    def detail(snap, handle):
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.sp("path"))
        ctrls = QHBoxLayout(); ctrls.setSpacing(T.sp("sm"))
        ctrls.addWidget(lab_boards); ctrls.addWidget(sp_boards)
        ctrls.addWidget(lab_spares); ctrls.addWidget(sp_spares)
        ctrls.addWidget(lab_source); ctrls.addWidget(cb_source)
        ctrls.addStretch(1)
        ctrls.addWidget(cb_price)
        ctrls_w = QWidget(); ctrls_w.setLayout(ctrls)
        col.addWidget(ctrls_w)
        col.addWidget(picker)
        col.addWidget(summary_w)
        col.addWidget(result_host, 1)
        return body, (lambda s: _render_cached())

    def _render_cached():
        """Render the cached BOM into the summary + result area (never builds). Leaves an
        open diff untouched (it owns the same area), and shows a quiet empty state before the
        first build. Warms the Compare-To revision cache off-thread on a real render."""
        if getattr(host, "_summary_owner", None) == "diff":
            return                                        # a diff view owns the area — don't clobber
        res = getattr(host, "_last_bom", None)
        if not res:
            set_summary([])
            clear_layout(result)
            if not snapshot()["schs"]:
                result.addWidget(W.empty_state("No Schematic", glyph=icons.GLYPHS["alert"],
                                               sub="This project has no sheet to build a BOM from."))
            else:
                result.addWidget(W.empty_state("Not Built", glyph=icons.GLYPHS.get("box", ""),
                                               sub="Press ▶ Build and Cost to build the bill of materials."))
            return
        _apply_summary(getattr(host, "_last_base_tags", []), res)
        _draw_bom_table(res, getattr(host, "_last_mode", "project"))
        _refresh_recent_refs()

    # ── build core (shared by the ▶ flow, the initial auto-build, and Consolidated) ──────
    def _lookup():
        return LM.chained_lookup(LM.library_lookup(ctx.cfg), LM.providers_from_config(ctx.cfg))

    def _make_price(enabled):
        """The distributor price provider (Mouser preferred, LCSC fallback), throttled to be
        gentle on the rate-limited free key — or None when pricing is off / no provider is
        available. Off-GUI-safe (no widget reads), so a build worker can call it."""
        if not enabled:
            return None
        prov = LM.providers_from_config(ctx.cfg)
        if not prov:
            return None
        import time

        def throttled(mpn):
            r = prov(mpn)
            time.sleep(0.15)                              # one unique MPN at a time
            return r
        return throttled

    def _base_tags(res, mode):
        rows = res.get("rows", [])
        if mode == "consolidated":
            return [(f"{len(res.get('board_names', []))} Boards", "mut"),
                    (f"{res.get('line_count', 0)} Line Items", "mut")]
        basic_n = sum(1 for r in rows if r.get("basic"))
        return [(f"{res.get('component_count', 0)} Components", "mut"),
                (f"{res.get('line_count', 0)} Line Items", "mut"),
                (f"{basic_n} Basic", "mut")]

    def _stash(res, mode):
        """Write the cache holder (called from the build worker; the following GUI-thread
        refresh renders it). Pure attribute writes — no widgets."""
        host._last_bom = res
        host._last_mode = mode
        host._last_base_tags = _base_tags(res, mode)
        host._summary_owner = "bom"

    def _do_build(mode, job, busy_msg):
        """Run a BOM build (job) OFF-thread, write the cache, then refresh (verdict + fill).
        Used by the initial auto-build and Consolidated; the ▶ flow builds through its apply."""
        if busy["on"]:
            return
        busy["on"] = True
        clear_layout(result)
        result.addWidget(W.skeleton_rows(7, 5))

        def populate(res, ok):
            busy["on"] = False
            if not res or res.get("error"):
                host._last_bom = None
                host._summary_owner = None
                set_summary([])
                clear_layout(result)
                result.addWidget(W.empty_state("BOM Failed", glyph=icons.GLYPHS["alert"],
                                               sub=(res or {}).get("error", "")))
                host._verdict.set(W.VerdictState(kind="err", title="BOM Build Failed",
                                                 subtitle=(res or {}).get("error", "")))
                return
            _stash(res, mode)
            host._refresh()                               # verdict + fill render from the cache

        run_populate(ctx, job, populate, busy=busy_msg)

    # ── the ▶ Build and Cost primary flow (build → price → cost/lead report) ─────────────
    def _build_audit(snap):
        schs = snap["schs"]
        if not schs:
            return []
        n = len(schs); priced = snap["price"]
        lbl = f"Build BOM from {plural(n, 'sheet')}"
        if priced:
            lbl += " and price on Mouser"
        det = ("Read every sheet, enrich part numbers from the Library"
               + (", then look up live Mouser price + stock (network)." if priced else " (offline)."))
        return [{"key": "build", "label": lbl, "detail": det, "safe": True}]

    def _build_intro(snap, ops):
        n = len(snap["schs"])
        if snap["price"]:
            return (f"Build the bill of materials from {plural(n, 'sheet')} and look up live Mouser "
                    "price + stock for every part number (network calls, slower on a big BOM).")
        return f"Build the bill of materials from {plural(n, 'sheet')} (offline, no pricing)."

    def _build_report(res, snap):
        rows = res.get("rows", [])
        cost = LM.bom_cost_summary(rows)
        risks = LM.bom_sourcing_risks(rows, boards=snap["boards"])
        lead = LM.bom_lead_time(rows)
        done = [f"{res.get('component_count', 0)} components across {plural(cost['line_count'], 'line')}."]
        if cost["priced_lines"]:
            done.append(f"Priced {plural(cost['priced_lines'], 'line')}: {_money(cost['total_cost'])} total"
                        + (f" · {cost['unpriced_lines']} unpriced." if cost["unpriced_lines"] else "."))
        else:
            done.append("Not priced. Turn on Price with Mouser and rebuild to cost it.")
        missing = []
        if risks["not_active"]:
            missing.append({"item": f"{plural(risks['not_active'], 'NRND/EOL part')}", "why": "lifecycle is not Active",
                            "how_to_fix": "Find a drop-in replacement before ordering."})
        if risks["no_stock"]:
            missing.append({"item": f"{plural(risks['no_stock'], 'out-of-stock line')}", "why": "no distributor stock",
                            "how_to_fix": "Source an alternate or backorder."})
        if risks["insufficient_stock"]:
            missing.append({"item": f"{plural(risks['insufficient_stock'], 'low-stock line')}",
                            "why": f"stock below the {snap['boards']}-board build quantity",
                            "how_to_fix": "Split the order across suppliers or raise the quantity."})
        if lead["any"] and lead["max_weeks"] >= 12:
            who = f": {lead['critical_mpn']}" if lead.get("critical_mpn") else ""
            missing.append({"item": f"{lead['max_weeks']}-week critical-path lead{who}",
                            "why": "a quarter or more gates the whole order",
                            "how_to_fix": "Order the long-lead part first."})
        return {"summary": f"Built {snap['name']} BOM.", "done": done, "missing": missing}

    def _build_apply(snap, keys):
        schs = snap["schs"]
        price = _make_price(snap["price"])
        res = LM.bom_from_project(schs, lookup=_lookup(), price_lookup=price)
        if not res or res.get("error"):
            host._last_bom = None
            return {"errors": [(res or {}).get("error", "BOM build failed.")]}
        _stash(res, "project")
        return _build_report(res, snap)

    build_flow = kit.PrimaryFlow(
        label="▶ Build and Cost", audit=_build_audit, intro=_build_intro, apply=_build_apply,
        tip="Build the BOM from every sheet, enrich from the Library, and (with Price on) cost it on Mouser",
        empty="No schematic sheet in this project to build a BOM from.")

    # ── the cached-BOM render helpers (ported verbatim; root → host) ─────────────────────
    def set_summary(pairs):
        clear_layout(summary)                             # takeAt + unparent, so old tags don't linger
        for txt, kind in pairs:
            summary.addWidget(W.tag(txt, kind))
        summary.addStretch(1)

    def _risk_tags(rows):
        """Procurement-risk summary tags from a priced BOM — the failures worth catching
        before ordering (obsolete parts, empty or short stock). Stock coverage is judged at
        the Boards count. Empty when all healthy."""
        r = LM.bom_sourcing_risks(rows, boards=sp_boards.value())
        out = []
        if r["not_active"]:
            out.append((f"{r['not_active']} NRND/EOL", "err"))
        if r["no_stock"]:
            out.append((f"{r['no_stock']} No Stock", "err"))
        if r["insufficient_stock"]:
            out.append((f"{r['insufficient_stock']} Low Stock", "warn"))
        return out

    def _lead_tag(rows):
        """A single tag naming the critical-path part — the priced line with the longest
        manufacturer lead time. Quiet for a short lead; a long lead (a quarter or more) warns.
        Empty when no line carries parseable lead data."""
        r = LM.bom_lead_time(rows)
        if not r["any"]:
            return []
        wk = r["max_weeks"]
        unit = "wk" if wk == 1 else "wks"
        who = f": {r['critical_mpn']}" if r["critical_mpn"] else ""
        kind = "warn" if wk >= 12 else "info"             # ~a quarter is a real schedule risk
        return [(f"Longest lead {wk} {unit}{who}", kind)]

    def _apply_summary(base_tags, res):
        """Render the summary row: identity tags with the cost, build-quantity projection, and
        sourcing-risk tags folded in. Stored on host so the Boards spinner re-projects live."""
        tags = list(base_tags)
        cost = res.get("cost")
        if cost:
            n = sp_boards.value()
            head = [(f"{_money(cost['total_cost'])} Total", "ok")]
            if n > 1:
                proj = LM.bom_cost_at_qty(res.get("rows", []), n)
                per_board = proj["total_cost"] / n
                head.append((f"Build ×{n}: {_money(proj['total_cost'])} · "
                             f"{_money(per_board)} each", "info"))
            split = LM.bom_cost_by_source(res.get("rows", []), n).get("sources", {})
            if len(split) > 1:
                pieces = " · ".join(f"{name} {_money(v['total_cost'])}" for name, v in
                                    sorted(split.items(), key=lambda kv: -kv[1]["total_cost"]))
                head.append((pieces, "mut"))
            tags = head + tags
            if cost["unpriced_lines"]:
                tags.append((f"{cost['unpriced_lines']} Unpriced", "warn"))
            tags += _risk_tags(res.get("rows", []))
            tags += _lead_tag(res.get("rows", []))
        set_summary(tags)
        host._summary_state = (base_tags, res)
        host._summary_owner = "bom"                       # a real BOM summary — spinner may re-project

    def _resummarize():
        if getattr(host, "_summary_owner", None) != "bom":
            return
        st = getattr(host, "_summary_state", None)
        if st:
            _apply_summary(*st)

    def _current_source_key():
        """The (label, source-value) the Source combo is filtering to, or None for All."""
        i = cb_source.currentIndex()
        return _SOURCE_FILTERS[i][1] if 0 <= i < len(_SOURCE_FILTERS) else None

    def _row_source(r):
        return str(r.get("source", "") or "").strip()

    def _source_badge(r):
        """A quiet per-line source badge from the lookup chain: a named distributor (info
        dot), an unsourced passive (neutral 'Unsourced'), or a looked-up-but-absent part
        ('Not Found', a warn dot). Matches the summary/verdict sourcing language."""
        src = _row_source(r)
        if not src:
            return W.tag("Unsourced", "mut")
        if src.upper() == "NOT FOUND":
            return W.tag("Not Found", "warn")
        return W.tag(src, "info")

    def _consolidated_details_dialog(r):
        """A per-row modal for one consolidated line: its identity, the total, and a
        board-by-board {board: qty} breakdown as a height-scaled bar chart. Built (not
        exec'd) so a headless drive/test can inspect it. No sideways scroll (design-rules §5)."""
        dlg = QDialog(host)
        dlg.setWindowTitle("Per-Board Breakdown")
        v = QVBoxLayout(dlg); v.setContentsMargins(T.sp("xl"), T.sp("card"), T.sp("xl"), 18); v.setSpacing(T.sp("md"))
        names = LM.part_display_names(r)
        title = str(r.get("mpn", "")) if names["orderable"] else names["flag"]
        v.addWidget(W.subhead(title))
        meta_bits = [b for b in (str(r.get("value", "")), str(r.get("footprint", "")),
                                 f"{r.get('total_qty', 0)} total") if b.strip()]
        if meta_bits:
            m = W.body("  ·  ".join(meta_bits), dim=True); m.setWordWrap(True); v.addWidget(m)
        per_board = r.get("per_board") or {}
        if per_board:
            v.addWidget(pv.board_qty_chart(per_board))
        else:
            v.addWidget(W.body("No per-board breakdown for this line.", dim=True))
        close = W.btn("Close", "ghost", "Close this breakdown", on_click=dlg.accept)
        crow = QHBoxLayout(); crow.addStretch(1); crow.addWidget(close)
        v.addLayout(crow)
        dlg._chart_rows = per_board                       # test / drive seam
        return dlg

    def _open_consolidated_details(r):
        from ..util import _headless
        dlg = _consolidated_details_dialog(r)
        host._last_details_dialog = dlg                   # drive/test seam
        if _headless():
            dlg.deleteLater()                             # never block a headless drive
            return
        dlg.exec_()
        dlg.deleteLater()

    def _details_btn(r):
        return W.btn("Details", "ghost", "Show this line's per-board quantity breakdown",
                     on_click=lambda: _open_consolidated_details(r))

    def _draw_bom_table(res, mode):
        """Render the BOM table into the result area for the current Boards count, and arm the
        spinner to redraw it live. `mode` picks the column layout ('project' vs 'consolidated').
        When priced AND Boards>1 an Order column is inserted and Unit/Ext are re-read from each
        line's price-break ladder at that run quantity. On a priced project build a Source badge
        column and the Source view-filter appear, and any line whose known stock can't cover the
        run at the current Boards count is lifted with a subtle inset step + a required-vs-available
        tooltip (sourced from the same logic as the No-Stock / Low-Stock verdict counts)."""
        clear_layout(result)
        all_rows = res.get("rows", [])
        priced = res.get("cost") is not None
        n = sp_boards.value()
        at_qty = priced and n > 1
        # Source view-filter: a read-only lens, only meaningful on a priced project build (the
        # rows carry a `source` only when priced). Show/hide its control to match.
        source_filterable = priced and mode != "consolidated"
        lab_source.setVisible(source_filterable)
        cb_source.setVisible(source_filterable)
        if source_filterable:
            key = _current_source_key()
            data = all_rows if key is None else [r for r in all_rows if _row_source(r) == key]
        else:
            data = all_rows
        has_lead = any(LM._lead_weeks(r.get("lead_time")) is not None for r in data)
        show_source = priced and mode != "consolidated"
        if mode == "consolidated":
            cols = ["Part Number", "Manufacturer", "Value", "Footprint", "Total"]
            if at_qty:
                cols.append("Order")
            if priced:
                cols += ["Unit", "Ext"]
            cols.append("Boards")                         # per-row Details (per-board breakdown)
        else:
            cols = ["Refs", "Qty"]
            if at_qty:
                cols.append("Order")
            cols += ["Value", "Part Number", "Manufacturer", "Type"]
            if show_source:
                cols.append("Source")
            if priced:
                cols += ["Unit", "Ext"]
        if has_lead:
            cols.append("Lead (wks)")
        ix = {name: i for i, name in enumerate(cols)}

        def _pn_cell(r):
            names = LM.part_display_names(r)
            return str(r.get("mpn", "")) if names["orderable"] else names["flag"]

        trows = []
        row_tints = []
        row_tips = {}
        for r in data:
            order_qty, vunit, vext = LM._row_cost_at_qty(r, n) if at_qty else (None, None, None)
            if mode == "consolidated":
                row = [_pn_cell(r), str(r.get("manufacturer", "")),
                       str(r.get("value", "")), str(r.get("footprint", "")),
                       str(r.get("total_qty", ""))]
                if at_qty:
                    row.append(str(order_qty))
                if priced:
                    row += ([_money(vunit), _money(vext)] if at_qty
                            else [_money(r.get("unit_price")), _money(r.get("extended"))])
                row.append(_details_btn(r))
            else:
                refs = r.get("refs", [])
                ref_txt = ", ".join(refs[:4]) + (f"  +{len(refs) - 4}" if len(refs) > 4 else "")
                row = [ref_txt, str(r.get("qty", ""))]
                if at_qty:
                    row.append(str(order_qty))
                row += [str(r.get("value", "")), _pn_cell(r),
                        str(r.get("manufacturer", "")), "Basic" if r.get("basic") else "Extended"]
                if show_source:
                    row.append(_source_badge(r))
                if priced:
                    row += ([_money(vunit), _money(vext)] if at_qty
                            else [_money(r.get("unit_price")), _money(r.get("extended"))])
            if has_lead:
                lw = LM._lead_weeks(r.get("lead_time"))
                row.append("" if lw is None else str(lw))
            # Stock coverage lift: a priced line whose known stock can't cover the run.
            if priced:
                sr = LM.bom_line_stock_risk(r, n)
                if sr["short"]:
                    idx = len(trows)
                    row_tints.append(idx)
                    avail_txt = "0 (no stock)" if sr["kind"] == "err" else str(sr["available"])
                    row_tips[idx] = (f"Short for this build: need {sr['required']} "
                                     f"(x{n} boards), stock is {avail_txt}.")
            trows.append(row)
        if mode == "consolidated":
            stretch = (ix["Manufacturer"], ix["Footprint"])
            mono = {ix["Part Number"]}
            dim = {ix["Value"], ix["Footprint"]}
        else:
            stretch = ix["Manufacturer"]
            mono = {ix["Refs"], ix["Part Number"]}
            dim = {ix["Value"]}
        if priced:
            mono |= {ix["Unit"], ix["Ext"]}
        if at_qty:
            mono |= {ix["Order"]}
        if has_lead:
            mono |= {ix["Lead (wks)"]}; dim |= {ix["Lead (wks)"]}
        result.addWidget(W.data_table(cols, trows, stretch_col=stretch,
                                      mono_cols=mono, dim_cols=dim, wrap=True,
                                      row_tints=row_tints, row_tips=row_tips), 1)
        host._render_table = lambda: _draw_bom_table(res, mode)

    def _on_boards_changed(_v):
        # Re-project the cost tags AND redraw the priced table at the new quantity — but never
        # over an open diff, which shares both the summary row and the result area.
        if getattr(host, "_summary_owner", None) == "diff":
            return
        _resummarize()
        rt = getattr(host, "_render_table", None)
        if rt:
            rt()
        # The sourcing-risk verdict scales with the board count too (stock coverage), so keep the
        # band in step with the live summary/table. verdict() reads only the cached rows (pure, no
        # network), so it's cheap to recompute synchronously per spinner change — no off-thread hop.
        try:
            host._verdict.set(verdict(snapshot()))
        except RuntimeError:                               # band deleted by a concurrent rebuild
            pass
    sp_boards.valueChanged.connect(_on_boards_changed)

    def _on_source_filter_changed(_i):
        # A read-only re-render of the current table at the new source lens — never over an
        # open diff (it owns the result area), and only when a table is actually mounted.
        if getattr(host, "_summary_owner", None) == "diff":
            return
        rt = getattr(host, "_render_table", None)
        if rt:
            rt()
    cb_source.currentIndexChanged.connect(_on_source_filter_changed)

    # ── exports (ported verbatim; root → host) ───────────────────────────────────────────
    def _filter_rows(rows):
        """The Export group's shared line-scope filter — the SAME predicates every one of the
        six exports honours (a single source of truth): 'Populated Only' drops blank/placeholder
        lines, 'Priced Only' drops lines with no usable price. Off = the whole BOM. Read-only."""
        out = list(rows or [])
        if cb_populated.isChecked():
            out = [r for r in out if LM.bom_line_is_populated(r)]
        if cb_priced_only.isChecked():
            out = [r for r in out if LM.bom_line_is_priced(r)]
        return out

    def _save_csv(title, default_name, text):
        fn, _ = QFileDialog.getSaveFileName(host, title, str(Path(base) / default_name),
                                            "CSV Files (*.csv)")
        if not fn:                                       # cancelled (or headless: no dialog)
            return
        try:
            Path(fn).write_text(text, encoding="utf-8", newline="")
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:                           # noqa: BLE001
            ctx.services.log(f"Write failed: {e}")

    def _save_bytes(title, default_name, data, file_filter):
        fn, _ = QFileDialog.getSaveFileName(host, title, str(Path(base) / default_name),
                                            file_filter)
        if not fn:                                       # cancelled (or headless: no dialog)
            return
        try:
            Path(fn).write_bytes(data)
            ctx.services.log(f"Wrote {Path(fn).name}.")
        except Exception as e:                           # noqa: BLE001
            ctx.services.log(f"Write failed: {e}")

    def _export_csv():
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        # Re-serialize from the FILTERED rows so the Export scope applies (with no filter it is
        # byte-identical to the cached csv — locked by test). priced/sourced from the build.
        rows = _filter_rows(res.get("rows", []))
        text = LM.bom_csv(rows, mode=getattr(host, "_last_mode", "project"),
                          board_names=res.get("board_names"),
                          priced=res.get("cost") is not None, sourced="not_on_mouser" in res)
        _save_csv("Export BOM CSV", "bom.csv", text)

    def _export_xlsx():
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        data = LM.bom_xlsx(_filter_rows(res.get("rows", [])))
        _save_bytes("Export BOM (Excel)", "bom.xlsx", data, "Excel Workbook (*.xlsx)")

    def _procurement_opts():
        """Ask for the Procurement-Sheet parameters — PCB pack size, Tax/Tariff rate, order
        Shipping, and the landed-assembly inputs (Labour per Board, Assembly Surcharge % of
        parts) — pre-filled with the last-used values. Returns (pcb_pack, tax_fraction,
        shipping, labour_per_board, surcharge_fraction), or None if cancelled."""
        from ..util import _headless
        if _headless():                                    # offscreen drive/CI: never enter a modal
            return (int(host._proc_pack), float(host._proc_tax_pct) / 100.0, float(host._proc_ship),
                    float(host._proc_labour), float(host._proc_surcharge_pct) / 100.0)
        dlg = QDialog(host); dlg.setWindowTitle("Procurement Sheet Options")
        form = QFormLayout(dlg)
        sp_pack = QSpinBox(); sp_pack.setRange(1, 1000); sp_pack.setValue(int(host._proc_pack))
        sp_pack.setToolTip("Boards ship from the fab in packs of this many, so quantities "
                           "round up to a full pack (a 1-board build buys parts for 3). Set 1 for none.")
        sp_tax = QDoubleSpinBox(); sp_tax.setRange(0, 100); sp_tax.setDecimals(2)
        sp_tax.setValue(float(host._proc_tax_pct)); sp_tax.setSuffix(" %")
        sp_tax.setToolTip("Tax or import tariff applied to each line's Cost @ QTY and summed in the Total row.")
        sp_ship = QDoubleSpinBox(); sp_ship.setRange(0, 100000); sp_ship.setDecimals(2)
        sp_ship.setValue(float(host._proc_ship)); sp_ship.setPrefix("$ ")
        sp_ship.setToolTip("One order-level shipping charge, shown and added in the Total row.")
        sp_labour = QDoubleSpinBox(); sp_labour.setRange(0, 100000); sp_labour.setDecimals(2)
        sp_labour.setValue(float(host._proc_labour)); sp_labour.setPrefix("$ ")
        sp_labour.setToolTip("Flat assembly-labour charge per board built (billed for the Boards "
                             "count, not the pack-rounded parts quantity). Adds an Assembly line.")
        sp_sur = QDoubleSpinBox(); sp_sur.setRange(0, 100); sp_sur.setDecimals(2)
        sp_sur.setValue(float(host._proc_surcharge_pct)); sp_sur.setSuffix(" %")
        sp_sur.setToolTip("Assembly handling/markup as a percent of the parts subtotal, folded "
                          "into the Assembly line and the grand Total.")
        form.addRow("PCB Pack", sp_pack)
        form.addRow("Tax/Tariff", sp_tax)
        form.addRow("Shipping", sp_ship)
        form.addRow("Labour / Board", sp_labour)
        form.addRow("Assembly Surcharge", sp_sur)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec_() != QDialog.Accepted:
            return None
        host._proc_pack = sp_pack.value()                 # remember for next time
        host._proc_tax_pct = sp_tax.value()
        host._proc_ship = sp_ship.value()
        host._proc_labour = sp_labour.value()
        host._proc_surcharge_pct = sp_sur.value()
        return (sp_pack.value(), sp_tax.value() / 100.0, sp_ship.value(),
                sp_labour.value(), sp_sur.value() / 100.0)

    def _export_procurement(opts=None):
        """Export the buy-side procurement sheet (Excel). Quantities honor the PCB pack size and
        the passives spares buffer; Tax/Tariff applies the rate to each line; Labour/Board and
        the Assembly Surcharge add a landed Assembly line. `opts` is a (pcb_pack, tax_fraction,
        shipping[, labour_per_board, surcharge_fraction]) test seam — a 3-tuple still works
        (labour/surcharge default 0); None opens the options dialog. Honors the Export scope."""
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        if opts is None:
            opts = _procurement_opts()
            if opts is None:                              # cancelled
                return
        pack, tax_frac, ship = opts[0], opts[1], opts[2]
        labour = opts[3] if len(opts) > 3 else 0.0
        surcharge_frac = opts[4] if len(opts) > 4 else 0.0
        data = LM.procurement_xlsx(_filter_rows(res.get("rows", [])), boards=sp_boards.value(),
                                   spares_pct=sp_spares.value(), pcb_multiple=pack,
                                   tax_rate=tax_frac, shipping=ship,
                                   labour_per_board=labour, assembly_surcharge_rate=surcharge_frac)
        _save_bytes("Export Procurement Sheet", "procurement.xlsx", data,
                    "Excel Workbook (*.xlsx)")

    def _export_order():
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        boards = sp_boards.value()
        spares = sp_spares.value()
        cart = LM.procurement_cart_csv(_filter_rows(res.get("rows", [])), boards=boards, spares_pct=spares)
        if not cart["line_count"]:
            ctx.services.log("No part numbers to order: every line is a bare passive."); return
        run = f" for {boards} boards" if boards > 1 else ""
        note = f"{cart['line_count']} order lines · {cart['total_qty']} parts{run}"
        if cart["padded_lines"]:
            note += f" · +{spares}% spares on {cart['padded_lines']} passive lines"
        if cart["skipped_no_mpn"]:
            note += f" · {cart['skipped_no_mpn']} passives without a part number skipped"
        ctx.services.log(note)
        default = f"mouser_cart_x{boards}.csv" if boards > 1 else "mouser_cart.csv"
        _save_csv("Export Mouser Cart", default, cart["csv"])

    def _export_priced():
        """The priced BOM as a purchasing sheet for the whole run. Needs a priced BOM."""
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        boards = sp_boards.value()
        sheet = LM.priced_bom_csv_at_qty(_filter_rows(res.get("rows", [])), boards=boards)
        if not sheet["priced_lines"]:
            ctx.services.log("Price the BOM first. Turn on Price with Mouser, then rebuild.")
            return
        run = f" for {boards} boards" if boards > 1 else ""
        note = f"Priced sheet: {_money(sheet['total_cost'])}{run}"
        if sheet["unpriced_lines"]:
            note += f" · {sheet['unpriced_lines']} unpriced"
        ctx.services.log(note)
        default = f"priced_bom_x{boards}.csv" if boards > 1 else "priced_bom.csv"
        _save_csv("Export Priced BOM", default, sheet["csv"])

    def _export_jlcpcb():
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        bom = LM.jlcpcb_bom_csv(_filter_rows(res.get("rows", [])))
        if not bom["line_count"]:
            ctx.services.log("Nothing to place: no BOM line has a value or part number."); return
        note = f"{bom['line_count']} assembly lines · {bom['total_qty']} parts"
        if bom["without_lcsc"]:
            note += f" · {bom['without_lcsc']} without an LCSC Part # (fill before assembly)"
        ctx.services.log(note)
        _save_csv("Export JLCPCB Assembly BOM", "jlcpcb_bom.csv", bom["csv"])

    def _copy_summary():
        """Copy a one-line procurement digest of the built BOM to the clipboard, scaled to the
        current Boards count. No file, no dialog."""
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        text = LM.bom_procurement_summary(res.get("rows", []), sp_boards.value())
        QApplication.clipboard().setText(text)
        ctx.services.log(f"Copied to clipboard: {text}")

    # ── compare / diff (ported verbatim; root → host) ────────────────────────────────────
    def _diff_cost_tags(c):
        if not c or not c.get("priced"):
            return []
        d = c["delta"]
        if d > 0:
            out = [(f"+{_money(d)}/board", "warn")]
        elif d < 0:
            out = [(f"-{_money(abs(d))}/board", "ok")]
        else:
            out = [(f"{_money(0)}/board", "mut")]
        if c.get("removed_unpriced"):
            n = c["removed_unpriced"]
            out.append((f"{n} removed not costed", "mut"))
        return out

    def _diff_lead_tags(l):
        if not l or not l.get("any"):
            return []
        out = []
        aw = l.get("added_max_weeks")
        if aw is not None:
            who = f" ({l['added_critical_mpn']})" if l.get("added_critical_mpn") else ""
            unit = "wk" if aw == 1 else "wks"
            if l.get("on_critical_path"):
                out.append((f"Lead path +{aw} {unit}{who}, critical path", "warn"))
            else:
                out.append((f"+{aw} {unit} lead{who}, off critical path", "mut"))
        if l.get("removed_unassessed"):
            n = l["removed_unassessed"]
            out.append((f"{n} removed, lead not assessed", "mut"))
        return out

    def _render_diff(d):
        """Render a BOM diff: added / removed / qty-changed lines vs a prior export, with the
        per-board cost delta when the current build is priced."""
        clear_layout(result)
        n_add, n_rem, n_chg = len(d["added"]), len(d["removed"]), len(d["changed"])
        set_summary([(f"{n_add} Added", "ok"), (f"{n_rem} Removed", "warn"),
                     (f"{n_chg} Changed", "mut"), (f"{d['unchanged']} Unchanged", "mut")]
                    + _diff_cost_tags(d.get("cost"))
                    + _diff_lead_tags(d.get("lead")))
        host._summary_owner = "diff"                      # the Boards spinner must not clobber this
        if not (n_add or n_rem or n_chg):
            result.addWidget(W.body("No differences. The BOMs match.", dim=True)); return
        exp = W.btn("Export Diff CSV", "ghost", "Save this comparison as a CSV",
                    on_click=lambda: _save_csv("Export BOM Diff", "bom_diff.csv", d.get("csv", "")))
        erow = QHBoxLayout(); erow.addWidget(exp); erow.addStretch(1)
        result.addLayout(erow)
        priced = bool(d.get("cost") and d["cost"].get("priced"))
        bprice = {}
        if priced:
            for row in (getattr(host, "_last_bom", None) or {}).get("rows", []):
                k = LM._bom_line_key(row)
                if k not in bprice:
                    bprice[k] = LM._coerce_price(row.get("unit_price"))

        def _line_cost(entry, qty):
            u = bprice.get(LM._bom_line_key(entry))
            return None if u is None else u * qty

        def _cost_cell(c):
            if c is None:
                return ""
            return f"+{_money(c)}" if c > 0 else (f"-{_money(abs(c))}" if c < 0 else _money(0))

        cols = ["Change", "Part Number", "Value", "From", "To", "Delta"]
        if priced:
            cols.append("Cost Δ")

        def _drow(change, entry, frm, to, dqty, cost):
            row = [change, entry["mpn"], entry["value"], frm, to, dqty]
            if priced:
                row.append(_cost_cell(cost))
            return row
        # Lead-delay lift: the ADDED line(s) whose manufacturer lead now gates the order (from
        # rev B's threaded lead_time) get an amber-tinted row, so the part that pushed the
        # critical path is visible in the diff, not just named in the summary tag.
        lead = d.get("lead") or {}
        amax = lead.get("added_max_weeks")
        on_cp = bool(lead.get("on_critical_path"))
        blead = {}
        if on_cp and amax is not None:
            for row in (getattr(host, "_last_bom", None) or {}).get("rows", []):
                k = LM._bom_line_key(row)
                w = LM._lead_weeks(row.get("lead_time"))
                if w is not None and (blead.get(k) is None or w > blead[k]):
                    blead[k] = w
        tbl = []
        row_tints = []
        row_tips = {}
        for r in d["added"]:
            idx = len(tbl)
            tbl.append(_drow("Added", r, "0", str(r["qty"]), f"+{r['qty']}", _line_cost(r, r["qty"])))
            if on_cp and amax is not None and blead.get(LM._bom_line_key(r)) == amax:
                row_tints.append(idx)
                unit = "wk" if amax == 1 else "wks"
                row_tips[idx] = f"Adds the critical-path lead: +{amax} {unit}."
        for r in d["removed"]:
            tbl.append(_drow("Removed", r, str(r["qty"]), "0", f"-{r['qty']}", None))
        for r in d["changed"]:
            tbl.append(_drow("Changed", r, str(r["from_qty"]), str(r["to_qty"]),
                             f"{r['delta']:+d}", _line_cost(r, r["delta"])))
        mono = {1, 3, 4, 5}
        if priced:
            mono.add(cols.index("Cost Δ"))
        result.addWidget(W.data_table(cols, tbl, stretch_col=1, mono_cols=mono,
                                      dim_cols={2}, wrap=True,
                                      row_tints=row_tints, row_tips=row_tips), 1)

    def _compare_to_csv(path=None):
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(host, "Compare To BOM CSV", base,
                                                  "CSV Files (*.csv)")
        if not path:                                      # cancelled (or headless: no dialog)
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:                            # noqa: BLE001
            ctx.services.log(f"Couldn't read {Path(path).name}: {e}"); return
        old_rows = LM.bom_rows_from_csv(text)
        if not old_rows:
            ctx.services.log("No BOM lines found in that CSV. Is it a bill of materials export?")
            return
        rows_b = res.get("rows", [])
        d = LM.bom_diff(old_rows, rows_b)
        d["cost"] = LM.bom_diff_cost(old_rows, rows_b)    # per-board $ delta from rev B's prices
        d["lead"] = LM.bom_diff_lead(old_rows, rows_b)    # does the change move the critical path?
        d["csv"] = LM.bom_diff_csv(d, rows_b)             # exported diff carries the cost column too
        host._last_diff = d
        ctx.services.log(f"Diff vs {Path(path).name}: {len(d['added'])} added, "
                         f"{len(d['removed'])} removed, {len(d['changed'])} changed.")
        _render_diff(d)

    def _sheet_rels():
        repo = ctx.cfg.get("RepoRoot")
        if not repo:
            return None, []
        rels = []
        root_p = Path(repo).resolve()
        for s in state.schematics():
            try:
                rels.append(Path(s).resolve().relative_to(root_p).as_posix())
            except Exception:                             # noqa: BLE001 — outside the repo
                continue
        return str(repo), rels

    def _recent_refs():
        repo, _ = _sheet_rels()
        return nd_git.recent_commits(repo) if repo else []

    def _refresh_recent_refs():
        """Recompute the recent-commits list for the Compare To… menu OFF the GUI thread and
        cache it on host, so opening the menu never shells out to git inline."""
        def done(refs, ok):
            host._recent_refs_cache = refs or []
        run_populate(ctx, _recent_refs, done)

    def _compare_to_ref(ref):
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        repo, rels = _sheet_rels()
        if not repo or not rels:
            ctx.services.log("No schematics under the repo root to compare."); return

        def show(rel):
            g = nd_git.show(repo, ref, rel)
            return g.out if g.ok else None
        old = LM.bom_rows_at_ref(rels, show)
        if not old["sheets_found"]:
            ctx.services.log(f"No schematic existed at {ref}: nothing to compare."); return
        rows_b = res.get("rows", [])
        d = LM.bom_diff(old["rows"], rows_b)
        d["cost"] = LM.bom_diff_cost(old["rows"], rows_b)
        d["lead"] = LM.bom_diff_lead(old["rows"], rows_b)
        d["csv"] = LM.bom_diff_csv(d, rows_b)
        host._last_diff = d
        miss = f" ({plural(old['sheets_missing'], 'sheet')} absent at {ref})" if old["sheets_missing"] else ""
        ctx.services.log(f"Diff vs {ref}: {len(d['added'])} added, {len(d['removed'])} "
                         f"removed, {len(d['changed'])} changed{miss}.")
        _render_diff(d)

    def _other_projects():
        return [p for p in state.projects if p != state.project]

    def _compare_to_project(name):
        res = getattr(host, "_last_bom", None)
        if not res:
            ctx.services.log("Build a BOM first."); return
        proj = next((p for p in state.projects if p.name == name), None)
        if proj is None:
            ctx.services.log(f"Project not found: {name}"); return
        schs = nd_wizard.list_schematics(proj)
        if not schs:
            ctx.services.log(f"{name} has no schematic to compare."); return
        other = LM.bom_from_project(schs)                 # offline: no lookup, no pricing
        rows_b = res.get("rows", [])
        d = LM.bom_diff(other.get("rows", []), rows_b)
        d["cost"] = LM.bom_diff_cost(other.get("rows", []), rows_b)
        d["lead"] = LM.bom_diff_lead(other.get("rows", []), rows_b)
        d["csv"] = LM.bom_diff_csv(d, rows_b)
        host._last_diff = d
        ctx.services.log(f"Diff vs {name}: {len(d['added'])} added, {len(d['removed'])} "
                         f"removed, {len(d['changed'])} changed.")
        _render_diff(d)

    def _compare_menu():
        """Pick what to compare the built BOM against: a prior CSV export, a git revision, or
        another project. Opens at the cursor (its trigger is the Compare To… secondary)."""
        if not getattr(host, "_last_bom", None):
            ctx.services.log("Build a BOM first."); return
        from PyQt5.QtGui import QCursor
        m = QMenu(host)
        m.addAction("Exported CSV…", _compare_to_csv)
        sub = m.addMenu("Git Revision")
        refs = getattr(host, "_recent_refs_cache", [])
        if not refs:
            a = sub.addAction("No revisions available"); a.setEnabled(False)
        for c in refs:
            label = f"{c['ref']}  {c['subject'][:48]}  · {c['when']}"
            sub.addAction(label, lambda _=False, r=c["ref"]: _compare_to_ref(r))
        others = _other_projects()
        if others:
            psub = m.addMenu("Another Project")
            for p in others:
                psub.addAction(p.name, lambda _=False, n=p.name: _compare_to_project(n))
        m.exec_(QCursor.pos())

    # ── Consolidated build (secondary): reveal the multi-select, then build ──────────────
    def _consolidated():
        picker.setVisible(len(state.projects) > 1)
        selected = [n for n, cb in checks.items() if cb.isChecked()] if checks \
            else [p.name for p in state.projects]
        boards_map = _consolidated_boards(state.projects, selected)
        price = _make_price(cb_price.isChecked())

        def job():
            return LM.consolidated_bom(boards_map, lookup=_lookup(), price_lookup=price)
        _do_build("consolidated", job,
                  "Pricing the consolidated BOM on Mouser..." if price else "Consolidating BOM...")

    # ── assemble the recipe ──────────────────────────────────────────────────────────────
    secondary = [
        kit.action("Build Consolidated…", _consolidated,
                   tip="Merge the bill of materials across the chosen projects (board variants)"),
        kit.action("Compare To…", _compare_menu,
                   tip="Diff the built BOM against a prior CSV, a git revision, or another project"),
    ]
    exports = [
        kit.action("Procurement Sheet (Excel)…", _export_procurement,
                   tip="Buy-side sheet: pack-rounded quantities, tax/tariff, shipping, totals"),
        kit.action("Full BOM CSV…", _export_csv, tip="Every line, per board, as CSV"),
        kit.action("Full BOM (Excel)…", _export_xlsx,
                   tip="Every line as a formatted Excel workbook (numbers sort + sum)"),
        kit.action("Priced BOM (Volume)…", _export_priced,
                   tip="A purchasing sheet projected to the Boards count (needs pricing)"),
        kit.action("Mouser Cart…", _export_order,
                   tip="Cart-upload CSV, scaled to the Boards count with the spares buffer"),
        kit.action("JLCPCB Assembly BOM…", _export_jlcpcb,
                   tip="Assembly BOM for JLCPCB (LCSC part numbers)"),
        kit.action("Copy Procurement Summary", _copy_summary,
                   tip="One-line digest to the clipboard, scaled to the Boards count"),
    ]

    host = kit.workbench(ctx, title="BOM & Procurement", snapshot=snapshot, verdict=verdict,
                         detail=detail, primary=build_flow, secondary=secondary,
                         exports=exports, busy=busy, chip_slots=4)

    # ── holder defaults (set BEFORE any handler can read them) ───────────────────────────
    host._proc_pack = 3                                    # PCBs ship in packs of this many
    host._proc_tax_pct = 0.0                               # tax/tariff percent on each line
    host._proc_ship = 0.0                                  # order-level shipping charge
    host._proc_labour = 0.0                                # assembly labour per board built
    host._proc_surcharge_pct = 0.0                         # assembly surcharge, percent of parts
    host._last_bom = None
    host._last_mode = "project"
    host._last_base_tags = []
    host._summary_owner = None
    host._summary_state = None
    host._render_table = None
    host._last_diff = None
    host._recent_refs_cache = []

    # ── Export scope: 'Populated Only' / 'Priced Only' checkboxes, docked at the top of the
    #    Export collapsible so the two filters read as export controls (they drive _filter_rows,
    #    shared by all six exports). Built here because kit renders the Export body internally. ──
    scope_row = QWidget()
    srl = QHBoxLayout(scope_row); srl.setContentsMargins(0, 0, 0, 6); srl.setSpacing(T.sp("md"))
    srl.addWidget(pv.field_label("Scope"))
    srl.addWidget(cb_populated); srl.addWidget(cb_priced_only); srl.addStretch(1)
    for _sec in host.findChildren(W.CollapsibleSection):
        if getattr(_sec, "_title", "") == "Export" and getattr(_sec, "_body", None) is not None:
            _sec._body.layout().insertWidget(0, scope_row)
            break

    # ── disable every action while a mutating op runs (mirror Health) ────────────────────
    from PyQt5.QtWidgets import QPushButton
    _buttons = [b for b in host.findChildren(QPushButton) if not b.text().startswith(("▸", "▾"))]

    def _apply_enablement():
        on = not busy["on"]
        for b in _buttons:
            try:
                b.setEnabled(on)
            except RuntimeError:                           # a button deleted by a rebuild
                pass

    busy.on_change = _apply_enablement

    # A guarded ▶ so a re-drive / a click during a build can't start a second flow.
    _raw_run = host._run_primary

    def _guarded_run():
        if busy["on"]:
            return
        _raw_run()
    host._run_primary = _guarded_run

    # ── test / cross-panel / drive seams (every legacy panel._xxx preserved) ─────────────
    host._price_cb = cb_price
    host._boards_spin = sp_boards
    host._spares_spin = sp_spares
    host._proj_checks = checks
    host._source_filter = cb_source
    host._populated_cb = cb_populated
    host._priced_only_cb = cb_priced_only
    host._filter_rows = _filter_rows
    host._source_badge = _source_badge
    host._consolidated_details_dialog = _consolidated_details_dialog
    host._open_consolidated_details = _open_consolidated_details
    host._snapshot = snapshot
    host._export_csv = _export_csv
    host._export_xlsx = _export_xlsx
    host._export_procurement = _export_procurement
    host._procurement_opts = _procurement_opts
    host._export_priced = _export_priced
    host._export_order = _export_order
    host._export_jlcpcb = _export_jlcpcb
    host._copy_summary = _copy_summary
    host._compare_to_csv = _compare_to_csv
    host._compare_to_ref = _compare_to_ref
    host._compare_to_project = _compare_to_project
    host._recent_refs = _recent_refs
    host._refresh_recent_refs = _refresh_recent_refs
    host._compare_menu = _compare_menu
    host._render_diff = _render_diff
    host._diff_cost_tags = _diff_cost_tags
    host._diff_lead_tags = _diff_lead_tags
    host._risk_tags = _risk_tags
    host._lead_tag = _lead_tag
    host._apply_summary = _apply_summary
    host._resummarize = _resummarize
    host._draw_bom_table = _draw_bom_table
    host._consolidated = _consolidated
    host._build_flow = build_flow

    # ── initial auto-build: identity-only (pricing off = no network), matching today ─────
    if state.schematics():
        def _initial_job():
            return LM.bom_from_project([str(s) for s in state.schematics()], lookup=_lookup())
        _do_build("project", _initial_job, "Building BOM from every sheet...")
    else:
        host._refresh()                                    # verdict → No Schematic; fill → empty
    return host


# ── Refactor (real preview + apply, per-op controls) ─────────────────────────
# (op label, op key, one-line explanation shown under the controls). Each op gets
# its own control page so the fields match what the op actually consumes.
_OPS = [("Find And Replace", "find_replace"), ("Add Tag", "add_tag"),
        ("Strip All", "strip_all"), ("Unannotate", "unannotate")]
_OP_KEYS = [o[1] for o in _OPS]
_OP_HELP = {
    "find_replace": "Replace a substring across references and labels.",
    "add_tag": "Prefix references and labels with a hierarchy tag.",
    "strip_all": "Remove every tag prefix from references and labels.",
    "unannotate": "Clear reference annotations (R? style).",
}


def _rename_panel(ctx, state) -> QWidget:
    if not state.project:
        return _no_project()
    root = QWidget(); lay = kit.page_layout(root)
    op_state = {"op": "find_replace"}
    top = QHBoxLayout(); top.setSpacing(T.sp("sm"))
    seg = W.Segmented([o[0] for o in _OPS], tip="Choose the refactor operation")
    top.addWidget(seg); top.addStretch(1)
    b_prev = W.btn("Preview", "ghost", "Preview the refactor without writing")
    b_apply = W.btn("Apply To Project", "primary",
                    "Write the change to every sheet and board. Each file is backed up (.bak) first")
    top.addWidget(b_prev); top.addWidget(b_apply)
    lay.addLayout(top)
    intro = W.body("Rename references and net labels across every sheet and board at once. "
                   "Preview first; applying backs up each file (.bak) before writing.", dim=True)
    intro.setWordWrap(True); lay.addWidget(intro)
    root._op_seg = seg
    root._op_state = op_state

    # ── per-op control pages (swapped when the op changes) ──────────────────
    # find_replace: Find + Replace fields · add_tag: one Tag field · strip_all /
    # unannotate: no field, just a one-line explanation. Generic quiet placeholders
    # only (no worked-example strings baked into the UI).
    stack = QStackedWidget()

    # find_replace page
    p_fr = QWidget(); frl = QVBoxLayout(p_fr); frl.setContentsMargins(0, 0, 0, 0); frl.setSpacing(T.sp("row"))
    fr = QHBoxLayout(); fr.setSpacing(T.sp("md"))
    find = QLineEdit(); find.setPlaceholderText("Find text"); find.setMinimumHeight(32)
    repl = QLineEdit(); repl.setPlaceholderText("Replace with"); repl.setMinimumHeight(32)
    fcol = QVBoxLayout(); fcol.setSpacing(6); fcol.addWidget(pv.field_label("Find")); fcol.addWidget(find)
    rcol = QVBoxLayout(); rcol.setSpacing(6); rcol.addWidget(pv.field_label("Replace With")); rcol.addWidget(repl)
    fr.addLayout(fcol); fr.addLayout(rcol)
    frl.addLayout(fr)
    stack.addWidget(p_fr)

    # add_tag page
    p_tag = QWidget(); tgl = QVBoxLayout(p_tag); tgl.setContentsMargins(0, 0, 0, 0); tgl.setSpacing(6)
    tag = QLineEdit(); tag.setPlaceholderText("Tag prefix (e.g. SH-)"); tag.setMinimumHeight(32); tag.setMaximumWidth(320)
    tgl.addWidget(pv.field_label("Tag Prefix")); tgl.addWidget(tag)
    stack.addWidget(p_tag)

    # strip_all page (no field)
    p_strip = QWidget(); spl = QVBoxLayout(p_strip); spl.setContentsMargins(0, 0, 0, 0)
    spl.addWidget(W.body(_OP_HELP["strip_all"], dim=True))
    stack.addWidget(p_strip)

    # unannotate page (no field)
    p_un = QWidget(); unl = QVBoxLayout(p_un); unl.setContentsMargins(0, 0, 0, 0)
    unl.addWidget(W.body(_OP_HELP["unannotate"], dim=True))
    stack.addWidget(p_un)

    lay.addWidget(stack)

    # A one-line explanation of the current op (sentence case, quiet).
    help_lbl = W.body(_OP_HELP["find_replace"], dim=True)
    lay.addWidget(help_lbl)

    cb_refs = QCheckBox("References"); cb_refs.setChecked(True)
    cb_lbls = QCheckBox("Labels"); cb_lbls.setChecked(True)
    cbs = QHBoxLayout(); cbs.setSpacing(18); cbs.addWidget(cb_refs); cbs.addWidget(cb_lbls); cbs.addStretch(1)
    lay.addLayout(cbs)

    def _on_op(name):
        op = dict(_OPS).get(name, "find_replace")
        op_state["op"] = op
        idx = _OP_KEYS.index(op)
        stack.setCurrentIndex(idx)
        # find_replace shows its own inline Find/Replace eyebrows, so only add the
        # help line for the value-less / single-field ops where it clarifies intent.
        help_lbl.setText("" if op == "find_replace" else _OP_HELP[op])
        help_lbl.setVisible(op != "find_replace")
    seg.on_change(_on_op)
    _on_op("Find And Replace")

    result = QVBoxLayout(); lay.addLayout(result, 1); lay.addStretch(1)

    def _placeholder():
        # An intentional empty state for the results region, so the panel doesn't
        # read as a broken void before the first Preview (design-rules §5).
        clear_layout(result)
        ph = W.body("Matches will appear here after you run Preview. Each is shown as "
                    "old → new before anything is written.", dim=True)
        ph.setWordWrap(True)
        result.addWidget(ph)
    _placeholder()

    def _run(apply):
        op = op_state["op"]
        # find_replace reads Find; add_tag reads Tag; the value-less ops send "".
        tag_or_find = (find.text().strip() if op == "find_replace"
                       else tag.text().strip() if op == "add_tag" else "")
        replacement = repl.text().strip() if op == "find_replace" else ""
        # An empty Find/Tag is DESTRUCTIVE, not a no-op: nd_wizard's transforms do
        # str.replace(tag_or_find, repl) / ref.replace('', repl), and an empty needle
        # inserts repl between every character ('GND' -> 'XGXNXDX') on every sheet and
        # board. Refuse it up front — for both Preview and Apply — with a clear reason.
        if op in ("find_replace", "add_tag") and not tag_or_find:
            clear_layout(result)
            need = "text to find" if op == "find_replace" else "a tag prefix"
            msg = (f"Enter {need} first. An empty value would rewrite every "
                   f"reference and label.")
            result.addWidget(W.body(msg, dim=True))
            ctx.services.log(msg)
            return
        schs = state.schematics(); boards = state.boards()
        clear_layout(result); result.addWidget(W.body("Applying..." if apply else "Previewing...", dim=True))

        def job():
            # Apply is all-or-nothing: stage every sheet + board transform in memory,
            # then commit with rollback (nd_wizard.apply_transforms_atomically), so a
            # mid-loop failure (lock/permission/encoding) never leaves the project
            # half-renamed. Preview still runs the per-file dry-run path.
            if apply:
                tasks = [(sch, nd_wizard._make_sch_task(
                            sch, op, tag_or_find, replacement or None,
                            cb_refs.isChecked(), cb_lbls.isChecked())) for sch in schs]
                tasks += [(brd, nd_wizard._make_pcb_task(brd, op, tag_or_find, replacement or None))
                          for brd in boards]
                try:
                    changes, _backups = nd_wizard.apply_transforms_atomically(tasks, _ts())
                except nd_wizard.ApplyError as e:
                    return {"error": True, "stage": e.stage, "path": Path(e.path).name}
                return {"total": len(changes), "changes": changes}
            changes, total = [], 0
            for sch in schs:
                counts, samples, ch = nd_wizard.schematic_preview_and_apply(
                    sch, op, tag_or_find, replacement or None, apply=False,
                    touch_refs=cb_refs.isChecked(), touch_labels=cb_lbls.isChecked())
                total += sum(counts.values()) if isinstance(counts, dict) else 0
                changes += ch
            for brd in boards:
                cnt, samples, ch = nd_wizard.pcb_preview_and_apply(brd, op, tag_or_find, replacement or None, apply=False)
                total += cnt
                changes += ch
            return {"total": total, "changes": changes}

        def populate(res, ok):
            clear_layout(result)
            if not res:
                result.addWidget(W.body("Refactor failed.", dim=True)); return
            if res.get("error"):
                # Atomic apply rolled back — say so, and name the file/phase, so the
                # user knows NO files were left modified (not a silent partial write).
                result.addWidget(W.body(
                    f"Refactor aborted: {res['stage']} failed on {res['path']}. "
                    f"No files were modified.", dim=True))
                ctx.services.log(f"Refactor aborted ({res['stage']} on {res['path']}); "
                                 f"nothing was written."); return
            verb = "Applied" if apply else "Preview"
            changes = res["changes"]
            result.addWidget(W.section_header(f"{verb}   {res['total']} Changes"))
            card = W.Card(pad=16)
            # Show EVERY change — the preview is the safety mechanism for a destructive
            # bulk write, so no row is hidden. A long list is put behind a scroll body
            # (below) rather than truncated, so an unshown corrupting rename can't slip
            # through unseen before Apply.
            for (typ, old, new, path) in changes:
                row = QHBoxLayout(); row.setSpacing(T.sp("sm"))
                row.addWidget(W.tag(str(typ), "mut")); row.addWidget(W.body(str(old), dim=True, mono=True))
                row.addWidget(W.body("→", dim=True)); row.addWidget(W.body(str(new), mono=True)); row.addStretch(1)
                card.body.addLayout(row)
            if not changes:
                card.body.addWidget(W.body("No matching changes.", dim=True))
            # A big list scrolls inside a bounded viewport so the whole page doesn't grow
            # without limit; a short list renders inline as before.
            if len(changes) > 60:
                sb = W.scroll_body(card); sb.setMinimumHeight(360)
                result.addWidget(sb, 1)
            else:
                result.addWidget(card)

        run_populate(ctx, job, populate, busy=("Applying refactor..." if apply else "Previewing refactor..."))

    b_prev.clicked.connect(lambda: _run(False))
    b_apply.clicked.connect(lambda: _run(True))
    # Test / programmatic seams: drive a refactor headless and read the fields.
    root._run = _run
    root._find_edit = find
    root._repl_edit = repl
    root._tag_edit = tag
    root._result_layout = result
    return root


# ── PCB Setup — merged Net Classes + Design Rules + Board Geometry + Fab ──────
# One scrollable tab. A single mm ⇄ mils unit toggle governs every LENGTH field
# (canonical mm is stored; display and edit-commit convert). One profile selector
# drives the net-class defaults, the design-rule seed floors and the fab facts.

_MM = "mm"
_MILS = "mils"

# Design-rule fields (UI label -> ProjectSettings attr). PSM stores these in MILS;
# the UI keeps canonical mm, so we mils_to_mm on load and mm_to_mils on save.
_DR_FIELDS = [
    ("Min Clearance", "default_clearance"), ("Min Track Width", "default_track_width"),
    ("Via Diameter", "default_via_diameter"), ("Via Drill", "default_via_drill"),
    ("Min Via Diameter", "min_via_diameter"), ("Min Annular Ring", "min_via_annular_width"),
    ("Min Through Hole", "min_through_hole"), ("Min Hole To Hole", "min_hole_to_hole"),
    ("Min Microvia Diameter", "min_microvia_diameter"), ("Min Microvia Drill", "min_microvia_drill"),
    ("Min Copper Edge Clearance", "min_copper_edge_clearance"),
]
# Which design-rule attr each NETCLASS_PROFILES floor seeds (Seed From Profile).
_DR_SEED = {"default_clearance": "min_clearance", "default_track_width": "min_track",
            "default_via_diameter": "min_via", "default_via_drill": "min_drill",
            "min_via_annular_width": "min_annular"}
# Net-class length columns: (NetClass attr, table column index). Mirrors KiCad's own
# Net Classes dialog — routing floors, microvia, diff-pair, then schematic wire/bus stroke.
_NC_LEN = [("clearance", 1), ("track_width", 2), ("via_diameter", 3), ("via_drill", 4),
           ("microvia_diameter", 5), ("microvia_drill", 6),
           ("diff_pair_width", 7), ("diff_pair_gap", 8), ("diff_pair_via_gap", 9),
           ("wire_thickness", 10), ("bus_thickness", 11)]
_NC_COLS = ["Class", "Clearance", "Track Width", "Via Diameter", "Via Drill",
            "Microvia Dia", "Microvia Drill", "Diff Pair W", "Diff Pair G", "Diff Pair Via",
            "Wire Thick", "Bus Thick", "Line Style", "Priority", "Patterns", ""]
# The non-length net-class columns (fixed indices, past the _NC_LEN block).
_NC_COL_STYLE, _NC_COL_PRIORITY, _NC_COL_PATTERNS, _NC_COL_DELETE = 12, 13, 14, 15
# Schematic line style: display label -> NetClass.line_style enum (KiCad int on save).
_NC_LINE_STYLES = [("Solid", "solid"), ("Dashed", "dashed"),
                   ("Dotted", "dotted"), ("Dash-Dot", "dash_dot")]

# ── extended design-rule state (DRC/ERC severities, pin map, predefined tables) ──────
# "Unmanaged" (the 4th severity choice) = leave the project's current value untouched —
# it maps to NOT calling set_*_severity for that rule (preserve-by-default, matching the
# PSM backend). Only error/warning/ignore are written.
_SEV_UNMANAGED = "Unmanaged"
_SEV_CHOICES = (_SEV_UNMANAGED,) + psm.SEVERITY_LEVELS   # ("Unmanaged","error","warning","ignore")
# Short axis labels for the 12x12 ERC pin-conflict grid (psm.ERC_PIN_TYPES order).
_PIN_ABBR = {"input": "In", "output": "Out", "bidirectional": "Bi", "tri_state": "Tri",
             "passive": "Pas", "free": "Free", "unspecified": "Uns", "power_in": "PwI",
             "power_out": "PwO", "open_collector": "OC", "open_emitter": "OE",
             "no_connect": "NC"}


def _humanize_rule(rule_id: str) -> str:
    """'pin_not_connected' -> 'Pin Not Connected' for a readable rule label."""
    return " ".join(w.capitalize() for w in str(rule_id).split("_"))


def _dre_fingerprint(pm) -> tuple:
    """A hashable snapshot of the EXTENDED managed state (severities / pin map / predefined
    size tables). ▶ Save compares this before/after the GUI flush so the extended write is
    surfaced + performed only when the user actually changed something (no idle churn)."""
    if pm is None:
        return ()
    return (
        tuple(sorted(pm.drc_severities.items())),
        tuple(sorted(pm.erc_severities.items())),
        tuple(tuple(int(x) for x in row) for row in pm.erc_pin_map),
        tuple(round(float(w), 6) for w in pm.track_widths),
        tuple((round(v.diameter, 6), round(v.drill, 6)) for v in pm.via_dimensions),
        tuple((round(d.width, 6), round(d.gap, 6), round(d.via_gap, 6))
              for d in pm.diff_pair_dimensions),
        # M4: the editable Default net class (clearance/track/microvia) rides the same "dre"
        # write — include its managed fields so a Default-row edit is detected as dirty.
        tuple((k, round(float(v), 6)) for k, v in pm.default_netclass.managed_items()),
        # M5: project text variables + the set the editor removed (both ride "dre") so an
        # add/edit/remove is surfaced by ▶ Save (a removal shrinks the dict → fingerprint moves).
        tuple(sorted(pm.text_variables.items())),
        tuple(sorted(pm._removed_text_vars)),
    )


def _to_disp(mm, unit):
    """Canonical mm -> the number shown in the current display unit."""
    return mm_to_mils(mm) if unit == _MILS else mm


def _to_mm(disp, unit):
    """The number shown in the current display unit -> canonical mm."""
    return mils_to_mm(disp) if unit == _MILS else disp


def _snap_mm_display(mm):
    """Round a canonical mm value to the cleanest short decimal for DISPLAY only.

    PSM stores design rules 0.1-mil-quantized, so a round mm original (0.2 / 0.6 /
    0.25) re-reads as noise (0.2007 / 0.5994 / 0.2489). The mils→mm round-trip error
    is bounded by one 0.1-mil quantum (~0.00127 mm), so if a value rounds cleanly to
    1/2/3 decimals within that quantum, prefer the rounder figure — recovering
    0.2000 / 0.6000 / 0.2500 while leaving a genuinely fine value (e.g. 0.127 mm =
    5 mils) untouched. Canonical storage (sp._mm) is never changed by this."""
    for decimals in (1, 2, 3):
        snapped = round(mm, decimals)
        if abs(snapped - mm) <= 0.0013:
            return snapped
    return round(mm, 4)


def _len_spin(unit, mm_value, width=104, lo_mm=0.0, hi_mm=25.0, snap=False):
    """A quiet QDoubleSpinBox that holds a LENGTH. Canonical mm lives on sp._mm;
    the box re-renders (value/suffix/decimals/range) in the current unit and syncs
    sp._mm back on every user edit. Style comes from the container (borderless).
    `snap` cleans the mm DISPLAY of 0.1-mil quantization noise (design rules only)."""
    sp = QDoubleSpinBox()
    sp.setButtonSymbols(QDoubleSpinBox.NoButtons)
    sp.setAlignment(Qt.AlignRight)
    sp.setFixedWidth(width)
    sp._mm = float(mm_value or 0.0)
    sp._saved_mm = float(mm_value or 0.0)      # PCB-13: last-saved value (undo safety)
    sp._syncing = False
    sp._snap = snap

    def _mark_saved():
        sp._saved_mm = sp._mm
        sp.setToolTip("")
    sp._mark_saved = _mark_saved

    def render():
        u = unit["u"]
        sp._syncing = True
        if u == _MILS:
            sp.setDecimals(2); sp.setSingleStep(0.5); sp.setSuffix(" mils")
            sp.setRange(_to_disp(lo_mm, u), _to_disp(hi_mm, u))
            sp.setValue(_to_disp(sp._mm, u))
        else:
            sp.setDecimals(4); sp.setSingleStep(0.005); sp.setSuffix(" mm")
            sp.setRange(lo_mm, hi_mm)
            # Snap the shown mm only; sp._mm stays canonical (the _syncing guard
            # stops setValue from writing the snapped figure back into sp._mm).
            sp.setValue(_snap_mm_display(sp._mm) if sp._snap else sp._mm)
        sp._syncing = False

    def changed(_v):
        if not sp._syncing:
            sp._mm = _to_mm(sp.value(), unit["u"])
            # PCB-13: while the value differs from the last-saved one, show the
            # original in the tooltip so an edit is reversible/visible until saved.
            if abs(sp._mm - sp._saved_mm) > 1e-9:
                sp.setToolTip(f"Edited: saved value {_to_disp(sp._saved_mm, unit['u']):.4g} "
                              f"{'mils' if unit['u'] == _MILS else 'mm'}")
            else:
                sp.setToolTip("")

    sp.valueChanged.connect(changed)
    sp._render = render
    render()
    return sp


def _int_spin(val, width=72):
    s = QSpinBox(); s.setButtonSymbols(QSpinBox.NoButtons); s.setAlignment(Qt.AlignRight)
    s.setFixedWidth(width); s.setRange(0, 999999); s.setValue(int(val or 0))
    return s


def _ratio_spin(val, width=104, lo=-1.0, hi=1.0):
    """A dimensionless value (e.g. the paste clearance ratio) — never unit-converted."""
    s = QDoubleSpinBox(); s.setButtonSymbols(QDoubleSpinBox.NoButtons); s.setAlignment(Qt.AlignRight)
    s.setFixedWidth(width); s.setDecimals(4); s.setSingleStep(0.01); s.setRange(lo, hi)
    s.setValue(float(val or 0.0))
    return s


def _ts():
    import time
    return time.strftime("%Y%m%d-%H%M%S")


def _save_summary(done, stats):
    """A per-section digest of what a PCB-Setup save wrote to the file, so the user can
    tell what actually happened (not just a flat 'Saved'): design-rule fields, net
    classes written, the user/unmanaged classes deliberately preserved in the KiCad
    file, and board-geometry keys touched."""
    parts = []
    if "design rules" in done:
        parts.append(f"{plural(stats.get('dr_fields', 0), 'design-rule field')}")
    if "rule tables & severities" in done:
        parts.append(f"{plural(stats.get('dre', 0), 'rule/severity/size-table setting')}")
    if "net classes" in done:
        seg = f"{plural(stats.get('nc_written', 0), 'net class', 'net classes')} written"
        pres = stats.get("nc_preserved") or []
        if pres:
            shown = ", ".join(pres[:4]) + (f" +{len(pres) - 4} more" if len(pres) > 4 else "")
            seg += f" ({plural(len(pres), 'user class', 'user classes')} preserved: {shown})"
        parts.append(seg)
    if "board geometry" in done:
        parts.append(f"{plural(stats.get('bg_keys', 0), 'board-geometry key')}")
    if "fab floor" in done:
        fb = stats.get("fab_board") or {}
        bits = [b for b, on in (("stackup", fb.get("stackup")),
                                ("board thickness", fb.get("thickness"))) if on]
        parts.append("fab floor (" + " + ".join(bits) + ")" if bits else "fab floor")
    tail = "."
    synced = stats.get("profile_synced")
    if synced:
        # PCB-15: name the profile kept in lockstep so the user knows the JSON now
        # matches what was written to KiCad (no silent divergence to reseed from).
        tail = f"; profile '{synced}' updated to match."
    return "Saved to project: " + "; ".join(parts) + tail


def _pcb_targets(p):
    """Text-size targets for nd_object_conform, drawn from the selected preset."""
    t = {}
    for layer, h, w in (("silk", "silk_text_height", "silk_text_thickness"),
                        ("fab", "fab_text_height", "fab_text_thickness")):
        hv = getattr(p, h, None); wv = getattr(p, w, None)
        if hv is not None and wv is not None:
            t[layer] = (hv, wv)
    return t


# Editable fields of a FabPreset the manager modal surfaces, as (attr, label, kind).
# kind: "mm" = length (shown/edited in mm), "num" = plain number, "int" = layer count,
# "text" = free string. Stackup + hole/text sub-fields ride along unchanged from the
# source preset (per-layer stackup editing is a KiCad-GUI job — a logged gap).
_FAB_FIELDS = (
    ("min_track_width", "Min Track Width", "mm"),
    ("min_clearance", "Min Clearance", "mm"),
    ("min_drill", "Min Drill", "mm"),
    ("min_annular_ring", "Min Annular Ring", "mm"),
    ("min_edge_clearance", "Min Edge Clearance", "mm"),
    ("default_track_width", "Default Track Width", "mm"),
    ("default_via_diameter", "Default Via Diameter", "mm"),
    ("default_via_drill", "Default Via Drill", "mm"),
    ("board_thickness_mm", "Board Thickness", "mm"),
    ("copper_oz", "Copper Weight (oz)", "num"),
    ("layers", "Layers", "int"),
    ("material", "Material", "text"),
    ("finish", "Finish", "text"),
    ("soldermask", "Soldermask", "text"),
)


class FabPresetManagerDialog(QDialog):
    """Manage Fabrication Presets — New / Duplicate / Edit / Delete over the user
    fab-preset store (nd_fab_presets). Built-ins are locked: editing one and saving
    writes a same-name USER OVERRIDE (copy-to-override), and Delete on an override
    reverts to the built-in default. A pure user preset is fully editable/deletable.

    The name is set only at New / Duplicate (a prompt) and shown read-only in the form,
    so Save always upserts the selected preset in place — no rename orphans. Stackup and
    the hole/text sub-fields are carried verbatim from the edited preset (per-layer
    stackup editing is a KiCad-GUI job — logged gap), so a saved preset stays complete."""

    def __init__(self, parent=None, *, on_change=None):
        super().__init__(parent)
        from PyQt5.QtWidgets import QListWidget
        self.setWindowTitle("Manage Fabrication Presets")
        self.setMinimumWidth(560)
        self._on_change = on_change
        self._fields = {}
        self._current = None
        outer = QVBoxLayout(self)
        body = QHBoxLayout(); outer.addLayout(body)
        self._list = QListWidget(); self._list.setFixedWidth(220)
        self._list.currentTextChanged.connect(self._on_pick)
        body.addWidget(self._list)
        form_w = QWidget(); self._form = QFormLayout(form_w)
        self._name_lab = QLabel("-")
        self._form.addRow("Preset", self._name_lab)
        for attr, label, kind in _FAB_FIELDS:
            if kind in ("mm", "num"):
                sp = QDoubleSpinBox(); sp.setDecimals(4 if kind == "mm" else 2)
                sp.setRange(0.0, 1000.0); sp.setSingleStep(0.01)
                if kind == "mm":
                    sp.setSuffix(" mm")
                w = sp
            elif kind == "int":
                w = QSpinBox(); w.setRange(1, 64)
            else:
                w = QLineEdit()
            self._fields[attr] = (w, kind)
            self._form.addRow(label, w)
        body.addWidget(form_w, 1)
        # action row
        btns = QHBoxLayout()
        for text, slot in (("New", self._new), ("Duplicate", self._duplicate),
                           ("Delete", self._delete), ("Save", self._save)):
            b = QPushButton(text); b.clicked.connect(slot); btns.addWidget(b)
        btns.addStretch(1)
        close = QPushButton("Close"); close.clicked.connect(self.accept); btns.addWidget(close)
        outer.addLayout(btns)
        self._reload()

    # ── list / form binding ───────────────────────────────────────────────────
    def _reload(self, select=None):
        self._list.blockSignals(True)
        self._list.clear()
        presets = fabp.load_presets()
        for name in presets:
            tag = "built-in" if fabp.is_builtin(name) else "user"
            if fabp.is_builtin(name) and fabp.has_user_preset(name):
                tag = "override"
            self._list.addItem(f"{name}   · {tag}")
        self._list.blockSignals(False)
        target = select or self._current or (next(iter(presets)) if presets else None)
        if target is not None:
            self._select_name(target)

    def _select_name(self, name):
        for i in range(self._list.count()):
            if self._list.item(i).text().split("   · ")[0] == name:
                self._list.setCurrentRow(i)
                return

    def _name_of(self, item_text):
        return (item_text or "").split("   · ")[0]

    def _on_pick(self, item_text):
        name = self._name_of(item_text)
        if not name:
            return
        preset = fabp.get_preset(name)
        if preset is None:
            return
        self._current = name
        self._name_lab.setText(name + ("   (built-in: Save writes a user override)"
                                        if fabp.is_builtin(name) else ""))
        for attr, (w, kind) in self._fields.items():
            val = getattr(preset, attr)
            if kind in ("mm", "num"):
                w.setValue(float(val))
            elif kind == "int":
                w.setValue(int(val))
            else:
                w.setText(str(val))

    def _form_preset(self, name):
        """Build a FabPreset from the form, carrying non-form fields from the current
        source preset so nothing (stackup, hole clearances, text sizes) is dropped."""
        import dataclasses
        src = fabp.get_preset(self._current) or fabp.PRESETS[fabp.builtin_names()[0]]
        kw = {}
        for attr, (w, kind) in self._fields.items():
            kw[attr] = (w.value() if kind in ("mm", "num", "int") else w.text().strip())
        return dataclasses.replace(src, name=name, **kw)

    # ── actions ────────────────────────────────────────────────────────────────
    def _prompt_name(self, title, default):
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, "Preset name:", text=default)
        name = (name or "").strip()
        if not ok or not name:
            return None
        if fabp.is_builtin(name):
            _warn_dialog(self, "That name is a built-in preset. Choose a different name "
                               "(edit the built-in directly to make an override).")
            return None
        return name

    def _new(self):
        name = self._prompt_name("New Fabrication Preset", "New Fab Preset")
        if not name:
            return
        # seed from the current selection so stackup + values are sensible, not blank
        fabp.save_preset(self._form_preset(name))
        self._changed(select=name)

    def _duplicate(self):
        if not self._current:
            return
        name = self._prompt_name("Duplicate Fabrication Preset", f"{self._current} copy")
        if not name:
            return
        import dataclasses
        fabp.save_preset(dataclasses.replace(fabp.get_preset(self._current), name=name))
        self._changed(select=name)

    def _delete(self):
        name = self._current
        if not name:
            return
        if fabp.is_builtin(name) and not fabp.has_user_preset(name):
            _warn_dialog(self, f"'{name}' is a built-in preset and can't be deleted.")
            return
        verb = "Revert your override of" if fabp.is_builtin(name) else "Delete"
        if not confirm(self, "Delete Fabrication Preset", f"{verb} the preset '{name}'?"):
            return
        fabp.delete_preset(name)
        self._current = None
        self._changed()

    def _save(self):
        if not self._current:
            return
        fabp.save_preset(self._form_preset(self._current))
        self._changed(select=self._current)

    def _changed(self, select=None):
        self._reload(select=select)
        if callable(self._on_change):
            self._on_change()


def _warn_dialog(parent, msg):
    from ..util import _headless
    if _headless():
        return
    from PyQt5.QtWidgets import QMessageBox
    QMessageBox.information(parent, "Fabrication Presets", msg)


def _pcb_setup_panel(ctx, state) -> QWidget:
    """PCB Setup — rebuilt onto the ``kit.editor`` recipe (spec 2026-07-10-phase2-projects
    -kit-editor). The four editable sections — Fabrication Profile (+ text-size Conform),
    Design Rules, Net Classes, Board Geometry — are the editor BODY, built ONCE by
    ``build_body`` so a verdict push never clobbers an in-progress edit. ``Save To Project``
    is the single accent ▶ primary flow (audit lists what will be written → preview → apply
    writes it); ``Validate`` pushes the colour verdict band; ``Pull From KiCad`` + profile
    New / Save / Delete are the 2-col secondary grid; ``Clear KiCad Cache`` is Manage
    machinery.

    Net Classes stay MERGED here (Section C) rather than as a standalone tab — the split is a
    logged follow-up (deep profile/unit state shared with this editor). Every legacy test seam
    the panel exposed (``_ncmgr`` / ``_profile_seg`` / ``_load_profile`` / ``_save`` /
    ``_validate`` / ``_nc_*`` / ``_dr_fields`` / ``_run_conform`` / ``_prof_state`` / …) is
    preserved on the returned host, so the coupled tests drive it unchanged."""
    from types import SimpleNamespace
    project = state.project if state else None
    boards = (state.boards() if (state and project) else []) or []
    board = boards[0] if boards else None
    pro = kicad_tools.project_pro_file(project) if project else None

    log = getattr(getattr(ctx, "services", None), "log", None)

    def _log(m):
        if callable(log):
            log(str(m))

    # ── shared mutable state (outer scope; build_body fills the widget-bearing bits) ──────
    unit = {"u": U.mode()}     # seed from the app-wide Length Units preference
    all_fields = []            # design-rule + board-geometry length spins
    nc_fields = []             # net-class table length spins (rebuilt on profile / CRUD)
    psize_fields = []          # predefined track/via/diff-pair length spins (rebuilt on add/remove)
    fab_labels = []            # unit-aware fab-fact labels (rebuilt on profile switch)
    stack_labels = []          # unit-aware stackup-layer thickness labels (rebuilt on switch)
    dr_fields = {}
    bg_fields = {}
    busy = kit.BusyDict()
    S = {}     # scalars build_body fills: pm, dr_writable, setup, explicit, nc_status, last_done

    def _profile_names():
        return [p.name for p in pcbprof.load_profiles()]

    _pnames = _profile_names()
    _default_prof = pcbprof.NETDECK if pcbprof.NETDECK in _pnames else _pnames[0]
    prof_state = {"name": _default_prof,
                  "fab": (pcbprof.get_profile(_default_prof).fab
                          if pcbprof.get_profile(_default_prof) else pcbprof.BARE_OSH_4)}

    def _seed_mgr(profile_name):
        prof = pcbprof.get_profile(profile_name)
        m = ncm.NetClassManager()
        if prof is not None:
            for nc in prof.netclasses:
                m.add_netclass(nc)
        return m

    nc_state = {"mgr": _seed_mgr(_default_prof), "rows": []}

    def refresh_units():
        for sp in all_fields:
            sp._render()
        for sp in nc_fields:
            sp._render()
        for sp in psize_fields:
            sp._render()
        for fn in fab_labels:
            fn()
        for fn in stack_labels:
            fn()

    def _commit_netclasses():
        mgr = nc_state["mgr"]
        for row in nc_state["rows"]:
            nc = mgr.get_netclass(row["name"])
            if not nc:
                continue
            for field, sp in row["spins"].items():
                # PCB-14: a diff-pair dimension of 0 means "no diff pair" — store None
                # (clears the attr) so to_kicad_dict / save_to_project omit it, rather
                # than persisting a fabricated 0.0. Any positive value enables it.
                if field in ("diff_pair_width", "diff_pair_gap"):
                    setattr(nc, field, float(sp._mm) if sp._mm > 0 else None)
                else:
                    setattr(nc, field, float(sp._mm))
            nc.priority = int(row["priority"].value())
            nc.patterns = [s.strip() for s in row["patterns"].text().split(",") if s.strip()]
            nc.line_style = row["line_style"].currentData()

    def _mark_persisted(done):
        # PCB-13: clear the "Edited — saved value X" tooltip on exactly the fields that
        # reached disk, so _saved_mm now tracks the persisted value.
        def mark(widgets):
            for w in widgets:
                fn = getattr(w, "_mark_saved", None)
                if fn:
                    fn()
        if "design rules" in done:
            mark(dr_fields.values())
        if "rule tables & severities" in done:
            mark(psize_fields)
            # Re-baseline the extended dirty snapshot to what we just wrote, so a repeat ▶ Save
            # with no further edit no longer re-offers/re-writes save_extended (idle churn) —
            # dre_base was only ever set at panel build.
            _pm = S.get("pm")
            if _pm is not None:
                S["dre_base"] = _dre_fingerprint(_pm)
            # the four extended sections all rode this write — clear their dirty dots
            rb = S.get("rebaseline_sections"); rd = S.get("refresh_dirty")
            if rb:
                rb()
            if rd:
                rd()
        if "net classes" in done:
            mark(nc_fields)
        if "board geometry" in done:
            bg_spins = []
            for kind, w in bg_fields.values():
                bg_spins += list(w) if kind == "coord" else [w]
            mark(bg_spins)

    # ── the editable body — built ONCE by kit.editor ─────────────────────────────────────
    def build_body(ctx, host):
        root = QWidget()
        lay = QVBoxLayout(root); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(T.sp("row"))

        def add_section(title, first=False):
            if not first:
                lay.addSpacing(12)
            lay.addWidget(W.section_header(title))

        # ── selector row: Profile + Units (the two controls that govern the body) ─────────
        top = QHBoxLayout(); top.setSpacing(T.sp("row"))
        prof_combo = QComboBox()
        prof_combo.addItems(_pnames)
        prof_combo.setCurrentText(_default_prof)      # set BEFORE connecting so it doesn't fire the loader
        prof_combo.setToolTip("Board-setup profile: a fab floor + a net-class set. NETDECK = OSH Park "
                              "4-layer + all net classes; the bare OSH Park profiles carry no nets.")
        # Fab selector: which fabrication preset backs this profile's floor. Independent of
        # the profile (a profile can target any fab), so a custom (non-OSH-Park) preset is
        # reachable end-to-end. Seeded to the profile's fab; a profile switch re-syncs it.
        fab_combo = QComboBox()
        fab_combo.addItems(list(fabp.load_presets()))
        if prof_state["fab"] in [fab_combo.itemText(i) for i in range(fab_combo.count())]:
            fab_combo.setCurrentText(prof_state["fab"])
        fab_combo.setToolTip("Fabrication preset backing this profile's fab floor, stackup and "
                             "board thickness. Manage adds custom fabs.")
        b_fab_manage = W.btn("Manage", "ghost", "Create, edit or delete fabrication presets")
        unit_seg = W.Segmented([_MM, _MILS], selected=(1 if unit["u"] == _MILS else 0),
                               tip="Length units, applied app-wide (also in Settings)")
        top.addWidget(pv.field_label("Profile")); top.addWidget(prof_combo)
        top.addWidget(pv.field_label("Fab")); top.addWidget(fab_combo); top.addWidget(b_fab_manage)
        top.addStretch(1)
        top.addWidget(pv.field_label("Units")); top.addWidget(unit_seg)
        lay.addLayout(top)

        def _apply_unit(u):
            """Adopt an app-wide unit change (from Settings or another panel): re-render the
            length fields and sync the toggle silently. A no-op if already current."""
            if u == unit["u"]:
                return
            unit["u"] = u
            unit_seg.select_value(u)
            refresh_units()

        def _on_unit(u):
            unit["u"] = u
            refresh_units()
            bus = getattr(ctx, "bus", None)
            if bus is not None:
                bus.emit("units.set_mode", u)
        unit_seg.on_change(_on_unit)

        _bus = getattr(ctx, "bus", None)
        if _bus is not None:
            # Owner = the host (the editor top widget): a project switch (ws.rebuild_all ->
            # deleteLater) auto-unsubscribes this closure instead of leaking a dead one.
            _bus.on_owned("units.changed", _apply_unit, host)

        # ── Section A — Fabrication Profile (locked fab facts, quiet read-only text) ───────
        add_section("Fabrication Profile", first=True)
        fab_holder = QWidget(); fhl = QVBoxLayout(fab_holder); fhl.setContentsMargins(0, T.sp("xs"), 0, 0); fhl.setSpacing(0)
        lay.addWidget(fab_holder)
        pv.apply_fabfacts_style(fab_holder)
        _fab_key = pv.fab_key
        _fab_val = pv.fab_val

        def _fab_len(mm):
            lab = pv.fab_val("", mono=True)

            def render():
                lab.setText(f"{mm_to_mils(mm):.2f} mils" if unit["u"] == _MILS else f"{round(mm, 4):g} mm")
            fab_labels.append(render); render()
            return lab

        def rebuild_fabfacts():
            clear_layout(fhl); fab_labels.clear()
            preset = fabp.get_preset(prof_state["fab"])
            if not preset:
                fhl.addWidget(W.empty_state("No Fabrication Preset",
                                            glyph=icons.GLYPHS["alert"],
                                            sub="This profile has no associated fab floor.")); return
            pairs = [
                ("Min Track Width", _fab_len(preset.min_track_width)),
                ("Min Clearance", _fab_len(preset.min_clearance)),
                ("Min Drill", _fab_len(preset.min_drill)),
                ("Copper Weight", _fab_val(f"{round(preset.copper_oz, 2):g} oz", mono=True)),
                ("Finish", _fab_val(str(preset.finish))),
                ("Board Thickness", _fab_len(preset.board_thickness_mm)),
                ("Material", _fab_val(str(preset.material))),
            ]
            grid = QWidget(); g = QGridLayout(grid); g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(T.sp("card")); g.setVerticalSpacing(T.sp("md")); g.setColumnMinimumWidth(0, 170)
            for r, (k, v) in enumerate(pairs):
                g.addWidget(_fab_key(k), r, 0, Qt.AlignTop)
                g.addWidget(v, r, 1, Qt.AlignTop)
            g.setColumnStretch(1, 1)
            fhl.addWidget(grid)
        rebuild_fabfacts()

        # ── Section — Text & Silkscreen (editable sizes + retroactive conform) ────────────
        # The owner's master-controller slice: SET the silk / fab / copper + schematic text
        # sizes here, then Conform rewrites the fonts on EXISTING objects in the real files
        # (silk is checked by default — the owner's "pcb silkscreen sizes"; the rest opt-in).
        add_section("Text & Silkscreen")

        def _text_seed():
            # (key -> (human label, (size_mm, thick_mm) default, conform-on-by-default)).
            # silk / fab defaults track the selected fabrication profile; the rest are the
            # KiCad stock defaults (1.0 mm PCB text, 1.27 mm schematic text).
            preset = fabp.get_preset(prof_state["fab"])
            pt = _pcb_targets(preset) if preset else {}
            return {
                "silk":   ("Silk screen (F/B.Silkscreen)", pt.get("silk", (1.0, 0.15)), True),
                "fab":    ("Fab layer (F/B.Fab)",          pt.get("fab", (1.0, 0.15)),  False),
                "copper": ("Copper text (Cu layers)",      (1.0, 0.15),                 False),
                "text":   ("Schematic text",               (1.27, 0.0),                 False),
                "labels": ("Schematic net labels",         (1.27, 0.0),                 False),
            }

        text_fields = {}      # key -> (size_spin, thick_spin)
        text_checks = {}      # key -> QCheckBox (include this type when conforming)
        tw = QWidget(); tg = QGridLayout(tw); tg.setContentsMargins(0, T.sp("xs"), 0, 0)
        tg.setHorizontalSpacing(T.sp("card")); tg.setVerticalSpacing(T.sp("row"))
        for ci, htxt in ((1, "Object"), (2, "Size"), (3, "Thickness")):
            tg.addWidget(pv.field_label(htxt), 0, ci)
        for r, (key, (label, (sz, th), on)) in enumerate(_text_seed().items(), start=1):
            chk = QCheckBox(); chk.setChecked(on)
            chk.setToolTip("Include this object type when Conform rewrites existing text")
            sp_sz = _len_spin(unit, sz, width=104, hi_mm=25.0)
            sp_th = _len_spin(unit, th, width=104, hi_mm=5.0)
            sp_th.setToolTip("Font stroke thickness. KiCad only stores a thickness on text that "
                             "already carries one, so a thickness edit is skipped on text using "
                             "the default stroke.")
            all_fields.append(sp_sz); all_fields.append(sp_th)
            text_fields[key] = (sp_sz, sp_th); text_checks[key] = chk
            tg.addWidget(chk, r, 0, Qt.AlignCenter)
            tg.addWidget(pv.field_label(label), r, 1)
            tg.addWidget(sp_sz, r, 2)
            tg.addWidget(sp_th, r, 3)
        tg.setColumnStretch(4, 1)
        pv.apply_quiet_fields(tw)
        lay.addWidget(tw)

        text_top = QHBoxLayout(); text_top.setSpacing(T.sp("sm"))
        b_text_seed = W.btn("Seed From Profile", "ghost",
                            "Fill the silk & fab text sizes from the selected fabrication profile")
        text_top.addWidget(b_text_seed); text_top.addStretch(1)
        lay.addLayout(text_top)

        def seed_text_sizes():
            preset = fabp.get_preset(prof_state["fab"])
            pt = _pcb_targets(preset) if preset else {}
            for key in ("silk", "fab"):
                if key in pt and key in text_fields:
                    sz, th = pt[key]; s_sp, t_sp = text_fields[key]
                    s_sp._mm = float(sz); s_sp._render()
                    t_sp._mm = float(th); t_sp._render()
            _log("Text sizes seeded from the fabrication profile (silk + fab).")
        b_text_seed.clicked.connect(seed_text_sizes)
        host._seed_text_sizes = seed_text_sizes

        conf_actions = QHBoxLayout(); conf_actions.setSpacing(T.sp("sm"))
        b_conf_prev = W.btn("Preview Conform", "ghost",
                            "Preview how many existing text objects would be resized")
        b_conf_apply = W.btn("Apply Conform", "default",
                             "Rewrite existing text sizes to the values above (a .bak is kept per "
                             "file). Run Preview first to see how many objects would change.")
        b_conf_apply.setEnabled(False)
        conf_actions.addWidget(b_conf_prev); conf_actions.addWidget(b_conf_apply); conf_actions.addStretch(1)
        lay.addLayout(conf_actions)
        conform_result = QVBoxLayout(); conform_result.setSpacing(T.sp("xs")); lay.addLayout(conform_result)
        conform_state = {"previewed": 0}

        def _text_targets():
            """(pcb_targets, sch_targets) from the CHECKED rows with a positive size. A
            thickness of 0 is passed as None so Conform leaves the font stroke untouched."""
            pcb_t, sch_t = {}, {}
            for key, (s_sp, t_sp) in text_fields.items():
                if not text_checks[key].isChecked() or s_sp._mm <= 0:
                    continue
                pair = (s_sp._mm, t_sp._mm if t_sp._mm > 0 else None)
                (sch_t if key in conform.SCH_TYPES else pcb_t)[key] = pair
            return pcb_t, sch_t

        def run_conform(apply):
            # Conform WRITES the .kicad_pcb / .kicad_sch (apply) off-thread — the SAME files ▶ Save
            # touches. It rides the shared busy gate so it can't overlap a Save (or another Conform)
            # and race two backup-then-write passes on one file (lost update / corruption).
            if busy["on"]:
                return
            pcb_t, sch_t = _text_targets()
            schs = list(state.schematics()) if (state and hasattr(state, "schematics")) else []
            files = [str(b) for b in boards] + [str(s) for s in schs]
            if (not pcb_t and not sch_t) or not files:
                _log("Nothing to conform: check at least one object type with a positive size, "
                     "on a project that has board or schematic files."); return
            if apply:
                n = conform_state["previewed"]
                if not confirm(host, "Apply Text Conform",
                               f"Rewrite text sizes on {plural(n, 'object')} across {plural(len(files), 'file')}? "
                               f"A .bak is kept per file."):
                    return
            ts = _ts()
            clear_layout(conform_result)
            conform_result.addWidget(W.body("Applying..." if apply else "Previewing...", dim=True))
            busy["on"] = True

            def job():
                return conform.conform_project(files, pcb_t, sch_t, ts, dry_run=not apply)

            def populate(rep, ok):
                busy["on"] = False
                clear_layout(conform_result)
                if not rep:
                    conform_result.addWidget(W.body("Conform unavailable, see status.", dim=True)); return
                total = rep.get("total") or 0
                conform_result.addWidget(W.body(f"{'Applied' if apply else 'Preview'}   {total} Text Objects", dim=True))
                if apply:
                    conform_state["previewed"] = 0
                    b_conf_apply.setEnabled(False)
                    _log(f"Text conform applied to {plural(total, 'object')} across {plural(len(files), 'file')}; "
                         f"a .bak was kept per file.")
                else:
                    conform_state["previewed"] = total
                    b_conf_apply.setEnabled(total > 0)
                    _log(f"Text conform preview: {plural(total, 'object')} would change.")

            run_populate(ctx, job, populate, busy=("Applying conform..." if apply else "Previewing conform..."))
        b_conf_prev.clicked.connect(lambda: run_conform(False))
        b_conf_apply.clicked.connect(lambda: run_conform(True))
        host._run_conform = run_conform                    # test seam
        host._conform_apply_btn = b_conf_apply
        host._text_fields = text_fields                    # drive/test seam
        host._text_checks = text_checks

        # ── Section — Stackup & Thickness ─────────────────────────────────────────────────
        # The physical board: an EDITABLE thickness (written into the .kicad_pcb (general)
        # block by the ONE ▶ Save To Project, seeded from the profile so the two never drift)
        # + a read-only view of the profile's physical layer stack. Per-layer stackup editing
        # is a KiCad-GUI job (no file-rewrite backend) — logged gap; summary only here.
        add_section("Stackup & Thickness")

        _preset0 = fabp.get_preset(prof_state["fab"])
        st_top = QWidget(); stg = QGridLayout(st_top); stg.setContentsMargins(0, T.sp("xs"), 0, 0)
        stg.setHorizontalSpacing(T.sp("card")); stg.setVerticalSpacing(T.sp("row"))
        thick_field = _len_spin(unit, (_preset0.board_thickness_mm if _preset0 else 1.6),
                                width=104, hi_mm=10.0)
        thick_field.setToolTip("Physical board thickness written into the .kicad_pcb by "
                               "▶ Save To Project. Seeded from the profile; edit to override.")
        all_fields.append(thick_field)
        S["thick_field"] = thick_field
        stg.addWidget(pv.field_label("Board Thickness"), 0, 0)
        stg.addWidget(thick_field, 0, 1)
        stg.setColumnStretch(2, 1)
        pv.apply_quiet_fields(st_top)
        lay.addWidget(st_top)

        stack_holder = QWidget()
        shl = QVBoxLayout(stack_holder); shl.setContentsMargins(0, T.sp("xs"), 0, 0); shl.setSpacing(0)
        lay.addWidget(stack_holder)
        pv.apply_fabfacts_style(stack_holder)

        def _stack_len(mm):
            lab = pv.fab_val("", mono=True)

            def render():
                lab.setText(f"{mm_to_mils(mm):.2f} mils" if unit["u"] == _MILS else f"{round(mm, 4):g} mm")
            stack_labels.append(render); render()
            return lab

        def rebuild_stackup():
            clear_layout(shl); stack_labels.clear()
            preset = fabp.get_preset(prof_state["fab"])
            if not preset or not preset.stackup:
                shl.addWidget(W.empty_state("No Stackup",
                                            glyph=icons.GLYPHS["alert"],
                                            sub="This profile's fab floor carries no physical layer stack.")); return
            grid = QWidget(); g = QGridLayout(grid); g.setContentsMargins(0, 0, 0, 0)
            g.setHorizontalSpacing(T.sp("card")); g.setVerticalSpacing(T.sp("sm")); g.setColumnMinimumWidth(0, 170)
            for c, h in enumerate(("Layer", "Type", "Thickness", "Material")):
                g.addWidget(pv.fab_key(h), 0, c, Qt.AlignTop)
            for r, (lname, kind, thick, mat) in enumerate(preset.stackup, start=1):
                g.addWidget(pv.fab_val(lname, mono=True), r, 0, Qt.AlignTop)
                g.addWidget(pv.fab_val(kind), r, 1, Qt.AlignTop)
                g.addWidget(_stack_len(thick), r, 2, Qt.AlignTop)
                g.addWidget(pv.fab_val(mat), r, 3, Qt.AlignTop)
            g.setColumnStretch(4, 1)
            shl.addWidget(grid)
        rebuild_stackup()

        def reseed_stackup():
            # profile switched: re-seed the editable thickness to the new fab floor + redraw
            # the read-only stack, so the section always reflects the selected profile.
            p = fabp.get_preset(prof_state["fab"])
            if p is not None:
                thick_field._mm = float(p.board_thickness_mm); thick_field._render()
            rebuild_stackup()

        host._thick_field = thick_field                    # drive/test seam

        # ── Fab selector + Manage wiring (needs rebuild_fabfacts / reseed_stackup above) ────
        def _set_fab(name):
            """Adopt a fab preset for the current profile: redraw the fab facts, re-seed the
            stackup + thickness, and re-seed the silk/fab text defaults. The choice rides
            into New / Save Profile (persisted on the profile via pcbprof)."""
            if not name or fabp.get_preset(name) is None:
                return
            prof_state["fab"] = name
            fab_combo.blockSignals(True); fab_combo.setCurrentText(name); fab_combo.blockSignals(False)
            rebuild_fabfacts()
            reseed_stackup()
            _log(f"Fab preset set to {name}. Save Profile (or New Profile) to keep it on this profile.")

        def _refresh_fab_presets(select=None):
            names = list(fabp.load_presets())
            fab_combo.blockSignals(True)
            fab_combo.clear(); fab_combo.addItems(names)
            want = select or prof_state["fab"]
            if want in names:
                fab_combo.setCurrentText(want)
            fab_combo.blockSignals(False)
            # a delete/revert may have removed the active fab — fall back to a live one
            if prof_state["fab"] not in names and names:
                _set_fab(fab_combo.currentText())
            else:
                rebuild_fabfacts(); reseed_stackup()

        def _open_fab_manager():
            dlg = FabPresetManagerDialog(host, on_change=lambda: _refresh_fab_presets())
            kit.open_subpage(ctx, dlg, "Fabrication Presets",
                             on_result=lambda _r: _refresh_fab_presets())
            return dlg

        fab_combo.currentTextChanged.connect(_set_fab)
        b_fab_manage.clicked.connect(_open_fab_manager)
        host._fab_combo = fab_combo                        # drive/test seams
        host._set_fab = _set_fab
        host._refresh_fab_presets = _refresh_fab_presets
        host._open_fab_manager = _open_fab_manager

        # ── Section B — Design Rules (PSM, editable) ──────────────────────────────────────
        add_section("Design Rules")
        pm = psm.ProjectSettingsManager()
        dr_writable = True
        if pro:
            try:
                dr_writable = bool(pm.load_from_project(pro))
            except Exception:  # noqa: BLE001
                dr_writable = False
            if not dr_writable:
                _log("Could not read the project's design rules; the Design "
                     "Rules section is read-only so it cannot overwrite them.")
        S["pm"] = pm
        S["dr_writable"] = dr_writable
        drw = QWidget(); drg = QGridLayout(drw); drg.setContentsMargins(0, T.sp("xs"), 0, 0)
        drg.setHorizontalSpacing(22); drg.setVerticalSpacing(T.sp("row"))
        _PER_ROW = 3
        for i, (label, attr) in enumerate(_DR_FIELDS):
            r, c = divmod(i, _PER_ROW)
            mmv = mils_to_mm(getattr(pm.settings, attr, 0.0))
            sp = _len_spin(unit, mmv, width=112, hi_mm=25.0, snap=True)
            sp.setEnabled(dr_writable)
            all_fields.append(sp); dr_fields[attr] = sp
            cell = QVBoxLayout(); cell.setSpacing(2)
            cell.addWidget(pv.field_label(label)); cell.addWidget(sp)
            drg.addLayout(cell, r, c)
        drg.setColumnStretch(_PER_ROW, 1)
        pv.apply_quiet_fields(drw)
        lay.addWidget(drw)

        dr_actions = QHBoxLayout(); dr_actions.setSpacing(T.sp("sm"))
        b_seed = W.btn("Seed From Fab Preset", "ghost", "Fill the design-rule floors from the selected fabrication preset")
        b_seed.setEnabled(dr_writable)
        dr_actions.addWidget(b_seed); dr_actions.addStretch(1)
        lay.addLayout(dr_actions)

        def seed_design_rules():
            preset = fabp.get_preset(prof_state["fab"])
            if preset is not None:
                seeded = fabp.apply_to_project_settings(pm.settings, preset)
                for _label, attr in _DR_FIELDS:
                    sp = dr_fields.get(attr)
                    if sp is not None:
                        sp._mm = mils_to_mm(getattr(seeded, attr)); sp._render()
                _log("Design rules seeded from the fabrication preset.")
                return
            floor = ncm.NETCLASS_PROFILES.get(prof_state["fab"], ncm.NETCLASS_PROFILES[ncm.DEFAULT_NETCLASS_PROFILE])
            for attr, key in _DR_SEED.items():
                sp = dr_fields.get(attr)
                if sp is not None and key in floor:
                    sp._mm = float(floor[key]); sp._render()
            _log("Design rules seeded from the fabrication profile.")
        b_seed.clicked.connect(seed_design_rules)
        host._seed_design_rules = seed_design_rules

        # ── Section B.2 — extended design rules: predefined size tables, DRC/ERC severities,
        #    ERC pin-conflict map (PSM extended coverage, written by ▶ Save via save_extended).
        #    Load the extended state separately (load_from_project reads only the flat settings)
        #    so every widget below seeds from the .kicad_pro; all dense controls tuck into
        #    collapsed-by-default sections so the panel reads unchanged at a glance.
        dre_writable = False
        if pro and dr_writable:
            try:
                dre_writable = bool(pm.load_extended(pro))
            except Exception:  # noqa: BLE001
                dre_writable = False
        S["dre_writable"] = dre_writable

        # ---- Predefined Sizes (track widths / vias / diff pairs) --------------------------
        _PS_SPEC = {
            "track": (1, ["Track Width"], (0.25,)),
            "via": (2, ["Via Ø", "Via Drill"], (0.6, 0.3)),
            "dp": (3, ["Pair Width", "Pair Gap", "Via Gap"], (0.2, 0.15, 0.2)),
        }
        pd_state = {
            "track": [(w,) for w in pm.track_widths if w > 0.0],
            "via": [(v.diameter, v.drill) for v in pm.via_dimensions
                    if not (v.diameter == 0.0 and v.drill == 0.0)],
            "dp": [(d.width, d.gap, d.via_gap) for d in pm.diff_pair_dimensions
                   if not (d.width == 0.0 and d.gap == 0.0 and d.via_gap == 0.0)],
        }
        pd_tables = {}

        def _ps_read(key):
            tbl = pd_tables[key]; ncols = _PS_SPEC[key][0]
            return [tuple(float(tbl.cellWidget(r, c)._mm) for c in range(ncols))
                    for r in range(tbl.rowCount())]

        def rebuild_predefined():
            # Full rebuild from pd_state: clears every predefined spin from psize_fields and
            # refills all three tables. Called on add/remove — never deletes a widget from
            # inside its own signal (the +/- buttons live outside the tables), so it is safe
            # synchronously (mirrors rebuild_netclasses).
            psize_fields.clear()
            for key, (ncols, _hdrs, _def) in _PS_SPEC.items():
                tbl = pd_tables[key]
                tbl.clearContents(); tbl.setRowCount(len(pd_state[key]))
                for r, row in enumerate(pd_state[key]):
                    for c in range(ncols):
                        sp = _len_spin(unit, row[c] if c < len(row) else 0.0, width=98, hi_mm=25.0)
                        pv.nc_cell_font(sp); sp.setEnabled(dre_writable)
                        sp.valueChanged.connect(lambda *_a: _bump_dirty())   # per-section dirty dot
                        psize_fields.append(sp); tbl.setCellWidget(r, c, sp)

        def _ps_delete_row(k, r):
            # Per-row delete (right-click) so a middle row can go without the remove-then-add
            # friction of the "Remove" (last-only) button. Captures the live cells first.
            for kk in _PS_SPEC:
                pd_state[kk] = _ps_read(kk)
            if 0 <= r < len(pd_state[k]):
                pd_state[k] = pd_state[k][:r] + pd_state[k][r + 1:]
            rebuild_predefined(); _bump_dirty()
        host._ps_delete_row = _ps_delete_row                # drive/test seam

        ps_body = QWidget(); psl = QVBoxLayout(ps_body)
        psl.setContentsMargins(0, 2, 0, 0); psl.setSpacing(T.sp("row"))
        for key, (ncols, hdrs, default) in _PS_SPEC.items():
            row = QVBoxLayout(); row.setSpacing(T.sp("xs"))
            head = QHBoxLayout(); head.setSpacing(T.sp("sm"))
            head.addWidget(pv.field_label(
                {"track": "Track Widths", "via": "Via Sizes", "dp": "Diff-Pair Sizes"}[key]))
            head.addStretch(1)
            b_add = W.btn("Add", "ghost", f"Add a row to the predefined {key} table")
            b_del = W.btn("Remove", "ghost", f"Remove the last row from the predefined {key} table")
            b_add.setEnabled(dre_writable); b_del.setEnabled(dre_writable)
            head.addWidget(b_add); head.addWidget(b_del)
            row.addLayout(head)
            tbl = QTableWidget(0, ncols); tbl.setHorizontalHeaderLabels(hdrs)
            tbl.verticalHeader().hide(); tbl.setShowGrid(False)
            tbl.setSelectionMode(QAbstractItemView.NoSelection)
            tbl.setFocusPolicy(Qt.NoFocus)
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            tbl.setMaximumHeight(150)
            _h = tbl.horizontalHeader(); _h.setHighlightSections(False)
            for c in range(ncols):
                _h.setSectionResizeMode(c, QHeaderView.Stretch)
            pv.apply_netclass_table(tbl)
            pd_tables[key] = tbl
            row.addWidget(tbl)

            def _mk_ps_menu(k, table):
                def _menu(pos):
                    r = table.rowAt(pos.y())
                    if r < 0:
                        return
                    m = QMenu(host); act = m.addAction("Delete this row")
                    if m.exec_(table.viewport().mapToGlobal(pos)) is act:
                        _ps_delete_row(k, r)
                return _menu
            tbl.setContextMenuPolicy(Qt.CustomContextMenu)
            tbl.customContextMenuRequested.connect(_mk_ps_menu(key, tbl))

            def _mk_add(k):
                def _add():
                    for kk in _PS_SPEC:
                        pd_state[kk] = _ps_read(kk)
                    pd_state[k] = pd_state[k] + [tuple(_PS_SPEC[k][2])]
                    rebuild_predefined()
                return _add

            def _mk_del(k):
                def _del():
                    for kk in _PS_SPEC:
                        pd_state[kk] = _ps_read(kk)
                    if pd_state[k]:
                        pd_state[k] = pd_state[k][:-1]
                    rebuild_predefined()
                return _del

            b_add.clicked.connect(_mk_add(key)); b_del.clicked.connect(_mk_del(key))
            psl.addLayout(row)
        rebuild_predefined()

        # ---- size-template quick-apply (Fine-Pitch / Power / Mixed / Hobby + Save As) --------
        tmpl_row = QHBoxLayout(); tmpl_row.setSpacing(T.sp("sm"))
        tmpl_row.addWidget(pv.field_label("Template"))
        ps_tmpl_combo = QComboBox(); ps_tmpl_combo.setFixedWidth(160)
        ps_tmpl_combo.setToolTip("Pre-fill the track / via / diff-pair tables from a coherent set")
        b_tmpl_apply = W.btn("Apply", "ghost", "Replace the tables with the selected template (confirm)")
        b_tmpl_saveas = W.btn("Save As", "ghost", "Save the current tables as a reusable template")
        b_tmpl_del = W.btn("Delete", "ghost", "Delete the selected custom template (built-ins are locked)")
        for w in (ps_tmpl_combo, b_tmpl_apply, b_tmpl_saveas, b_tmpl_del):
            tmpl_row.addWidget(w)
        tmpl_row.addStretch(1)
        psl.addLayout(tmpl_row)

        def _refresh_size_templates(select=None):
            ps_tmpl_combo.blockSignals(True)
            ps_tmpl_combo.clear(); ps_tmpl_combo.addItems(list(dpre.load_size_templates()))
            if select:
                ps_tmpl_combo.setCurrentText(select)
            ps_tmpl_combo.blockSignals(False)

        def _apply_size_template(name):
            t = dpre.get_size_template(name)
            if not t:
                return
            if not confirm(host, "Apply Size Template",
                           f"Replace the predefined track / via / diff-pair tables with the "
                           f"'{name}' template? Current rows are discarded."):
                return
            for kk in _PS_SPEC:
                pd_state[kk] = [tuple(r) for r in t.get(kk, [])]
            rebuild_predefined(); _bump_dirty()
            _log(f"Applied the '{name}' size template.")

        def _save_size_template_as(name=None):
            from PyQt5.QtWidgets import QInputDialog
            if name is None:
                name, ok = QInputDialog.getText(host, "Save Size Template", "Template name:")
                name = (name or "").strip()
                if not ok or not name:
                    return
            if dpre.is_builtin_template(name):
                _log(f"'{name}' is a built-in template name; choose another."); return
            for kk in _PS_SPEC:
                pd_state[kk] = _ps_read(kk)
            dpre.save_size_template(name, pd_state["track"], pd_state["via"], pd_state["dp"])
            _refresh_size_templates(select=name)
            _log(f"Saved size template '{name}'.")

        def _delete_size_template(name=None):
            name = name or ps_tmpl_combo.currentText()
            if dpre.is_builtin_template(name):
                _log(f"'{name}' is a built-in template and can't be deleted."); return
            if not confirm(host, "Delete Size Template", f"Delete the custom template '{name}'?"):
                return
            if dpre.delete_size_template(name):
                _refresh_size_templates()
                _log(f"Deleted size template '{name}'.")

        _refresh_size_templates()
        b_tmpl_apply.clicked.connect(lambda: _apply_size_template(ps_tmpl_combo.currentText()))
        b_tmpl_saveas.clicked.connect(lambda: _save_size_template_as())
        b_tmpl_del.clicked.connect(lambda: _delete_size_template())
        host._apply_size_template = _apply_size_template    # drive/test seams
        host._save_size_template_as = _save_size_template_as
        host._delete_size_template = _delete_size_template

        ps_section = W.CollapsibleSection("Predefined Sizes", ps_body)
        lay.addWidget(ps_section)

        # ---- DRC & ERC severities -----------------------------------------------------------
        sev_combos = {"drc": {}, "erc": {}}

        def _sev_table(rule_ids, loaded, store):
            tbl = QTableWidget(len(rule_ids), 2)
            tbl.setHorizontalHeaderLabels(["Rule", "Severity"])
            tbl.verticalHeader().hide(); tbl.setShowGrid(False)
            tbl.setSelectionMode(QAbstractItemView.NoSelection)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            tbl.setMaximumHeight(300)
            _h = tbl.horizontalHeader(); _h.setHighlightSections(False)
            _h.setSectionResizeMode(0, QHeaderView.Stretch)
            _h.setSectionResizeMode(1, QHeaderView.Fixed); tbl.setColumnWidth(1, 128)
            from PyQt5.QtWidgets import QTableWidgetItem
            for r, rid in enumerate(rule_ids):
                it = QTableWidgetItem(_humanize_rule(rid))
                it.setToolTip(rid)
                tbl.setItem(r, 0, it)
                cb = QComboBox(); cb.addItems(_SEV_CHOICES)
                cb.setCurrentText(loaded.get(rid, _SEV_UNMANAGED))
                cb.setEnabled(dre_writable)
                cb.setToolTip("Unmanaged = leave the project's current value untouched.")
                cb.currentIndexChanged.connect(lambda *_a: _bump_dirty())   # per-section dirty dot
                store[rid] = cb
                tbl.setCellWidget(r, 1, cb)
            pv.apply_netclass_table(tbl)
            return tbl

        sev_body = QWidget(); svl = QVBoxLayout(sev_body)
        svl.setContentsMargins(0, 2, 0, 0); svl.setSpacing(T.sp("sm"))
        # scheme quick-apply (Strict / Moderate / Relaxed + Save As)
        scheme_row = QHBoxLayout(); scheme_row.setSpacing(T.sp("sm"))
        scheme_row.addWidget(pv.field_label("Scheme"))
        sev_scheme_combo = QComboBox(); sev_scheme_combo.setFixedWidth(160)
        sev_scheme_combo.setToolTip("Pre-fill every DRC/ERC severity to a checking posture")
        b_scheme_apply = W.btn("Apply", "ghost", "Set every rule severity from the scheme (confirm)")
        b_scheme_saveas = W.btn("Save As", "ghost", "Save the current severities as a reusable scheme")
        b_scheme_del = W.btn("Delete", "ghost", "Delete the selected custom scheme (built-ins are locked)")
        for w in (sev_scheme_combo, b_scheme_apply, b_scheme_saveas, b_scheme_del):
            scheme_row.addWidget(w)
        scheme_row.addStretch(1)
        svl.addLayout(scheme_row)
        sev_filter = QLineEdit(); sev_filter.setPlaceholderText("Filter rules")
        sev_filter.setFixedWidth(240)
        sev_filter.setToolTip("Show only rules whose name contains this text")
        svl.addWidget(sev_filter)
        drc_tbl = _sev_table(psm.DRC_RULE_IDS, pm.drc_severities, sev_combos["drc"])
        erc_tbl = _sev_table(psm.ERC_RULE_IDS, pm.erc_severities, sev_combos["erc"])
        svl.addWidget(pv.field_label("DRC rules (board)")); svl.addWidget(drc_tbl)
        svl.addWidget(pv.field_label("ERC rules (schematic)")); svl.addWidget(erc_tbl)

        def _filter_sev(text):
            t = (text or "").strip().lower()
            for ids, tb in ((psm.DRC_RULE_IDS, drc_tbl), (psm.ERC_RULE_IDS, erc_tbl)):
                for r, rid in enumerate(ids):
                    tb.setRowHidden(r, bool(t) and t not in rid.lower()
                                    and t not in _humanize_rule(rid).lower())
        sev_filter.textChanged.connect(_filter_sev)

        def _refresh_severity_schemes(select=None):
            sev_scheme_combo.blockSignals(True)
            sev_scheme_combo.clear(); sev_scheme_combo.addItems(list(dpre.load_severity_schemes()))
            if select:
                sev_scheme_combo.setCurrentText(select)
            sev_scheme_combo.blockSignals(False)

        def _apply_severity_scheme(name):
            sch = dpre.get_severity_scheme(name)
            if not sch:
                return
            if not confirm(host, "Apply Severity Scheme",
                           f"Set the DRC and ERC rule severities from the '{name}' scheme? "
                           f"Rules the scheme does not mention are left unchanged."):
                return
            for kind in ("drc", "erc"):
                for rid, cb in sev_combos[kind].items():
                    lv = sch.get(kind, {}).get(rid)
                    if lv in psm.SEVERITY_LEVELS:
                        cb.setCurrentText(lv)
            _bump_dirty()
            _log(f"Applied the '{name}' severity scheme.")

        def _save_severity_scheme_as(name=None):
            from PyQt5.QtWidgets import QInputDialog
            if name is None:
                name, ok = QInputDialog.getText(host, "Save Severity Scheme", "Scheme name:")
                name = (name or "").strip()
                if not ok or not name:
                    return
            if dpre.is_builtin_scheme(name):
                _log(f"'{name}' is a built-in scheme name; choose another."); return
            drc = {rid: cb.currentText() for rid, cb in sev_combos["drc"].items()
                   if cb.currentText() in psm.SEVERITY_LEVELS}
            erc = {rid: cb.currentText() for rid, cb in sev_combos["erc"].items()
                   if cb.currentText() in psm.SEVERITY_LEVELS}
            dpre.save_severity_scheme(name, drc, erc)
            _refresh_severity_schemes(select=name)
            _log(f"Saved severity scheme '{name}' ({plural(len(drc) + len(erc), 'managed rule')}).")

        def _delete_severity_scheme(name=None):
            name = name or sev_scheme_combo.currentText()
            if dpre.is_builtin_scheme(name):
                _log(f"'{name}' is a built-in scheme and can't be deleted."); return
            if not confirm(host, "Delete Severity Scheme", f"Delete the custom scheme '{name}'?"):
                return
            if dpre.delete_severity_scheme(name):
                _refresh_severity_schemes()
                _log(f"Deleted severity scheme '{name}'.")

        _refresh_severity_schemes()
        b_scheme_apply.clicked.connect(lambda: _apply_severity_scheme(sev_scheme_combo.currentText()))
        b_scheme_saveas.clicked.connect(lambda: _save_severity_scheme_as())
        b_scheme_del.clicked.connect(lambda: _delete_severity_scheme())
        host._apply_severity_scheme = _apply_severity_scheme        # drive/test seams
        host._save_severity_scheme_as = _save_severity_scheme_as
        host._delete_severity_scheme = _delete_severity_scheme

        sev_section = W.CollapsibleSection("DRC & ERC Severities", sev_body)
        lay.addWidget(sev_section)

        # ---- ERC pin-conflict map (12×12, symmetric) ---------------------------------------
        pin_types = list(psm.ERC_PIN_TYPES)
        n_pin = len(pin_types)
        pin_matrix = [list(row) for row in pm.erc_pin_map] if pm.erc_pin_map \
            else [[0] * n_pin for _ in range(n_pin)]
        pin_cells = {}
        pinmap_state = {"touched": False}

        pin_body = QWidget(); pbl = QVBoxLayout(pin_body)
        pbl.setContentsMargins(0, 2, 0, 0); pbl.setSpacing(T.sp("sm"))
        pbl.addWidget(W.body(
            "Pin-to-pin conflict severity used by ERC. Click a cell to cycle "
            "OK → warning → error; the matrix is symmetric so the mirror updates too.",
            dim=True))
        grid_holder = QWidget(); gl = QGridLayout(grid_holder)
        gl.setContentsMargins(0, 0, 0, 0); gl.setHorizontalSpacing(3); gl.setVerticalSpacing(3)
        for j, pt in enumerate(pin_types):
            h = QLabel(_PIN_ABBR.get(pt, pt[:3])); h.setObjectName("pmHdr")
            h.setAlignment(Qt.AlignCenter); h.setToolTip(pt)
            gl.addWidget(h, 0, j + 1)
        for i, pt in enumerate(pin_types):
            rh = QLabel(_PIN_ABBR.get(pt, pt[:3])); rh.setObjectName("pmHdr")
            rh.setToolTip(pt); gl.addWidget(rh, i + 1, 0)
            for j in range(n_pin):
                cell = QPushButton(); cell.setFixedSize(26, 24)
                cell.setCursor(Qt.PointingHandCursor)
                cell.setEnabled(dre_writable)
                cell.setToolTip(f"{pin_types[i]} × {pin_types[j]}")
                pv.pinmap_cell_apply(cell, pin_matrix[i][j])
                pin_cells[(i, j)] = cell

                def _mk_click(ci, cj):
                    def _click():
                        nv = (int(pin_matrix[ci][cj]) + 1) % 3
                        pin_matrix[ci][cj] = nv; pin_matrix[cj][ci] = nv
                        pv.pinmap_cell_apply(pin_cells[(ci, cj)], nv)
                        if (cj, ci) in pin_cells:
                            pv.pinmap_cell_apply(pin_cells[(cj, ci)], nv)
                        pinmap_state["touched"] = True
                    return _click
                cell.clicked.connect(_mk_click(i, j))
                gl.addWidget(cell, i + 1, j + 1)
        pv.apply_pinmap_grid(grid_holder)
        _pin_row = QHBoxLayout(); _pin_row.setContentsMargins(0, 0, 0, 0)
        _pin_row.addWidget(grid_holder); _pin_row.addStretch(1)   # pack to content, don't spread
        pbl.addLayout(_pin_row)
        n_excl = len(pm.erc_exclusions)
        if n_excl:
            pbl.addWidget(W.body(f"{plural(n_excl, 'ERC exclusion')} preserved from the project "
                                 f"(kept verbatim on save).", dim=True))
        lay.addWidget(W.CollapsibleSection("ERC Pin Conflict Map", pin_body))

        # ---- Default Net Class (the design's Default routing class) ------------------------
        #    Rides ▶ Save's "dre" key (save_extended). Manages clearance / track / microvia
        #    ONLY — the Default class's via size/drill are owned by the flat Design-Rules
        #    "Via Diameter/Drill" spins above (save_design_rules_only, "dr"). Leaving via
        #    unmanaged here keeps dr and dre on DISJOINT keys of the Default class, so dre
        #    never reverts a flat via edit (the M3 landmine). Built BEFORE flush_dre so the
        #    closure below can read dnc_fields.
        dnc_fields = {}
        _DNC_SPEC = [("Clearance", "clearance"), ("Track Width", "track_width"),
                     ("Microvia Dia", "microvia_diameter"), ("Microvia Drill", "microvia_drill")]
        # KiCad's stock Default-class values (mm) — the seed fallback only when the file's
        # Default class omitted a key (older KiCad); present keys seed from the file verbatim.
        _DNC_DEFAULTS = {"clearance": 0.2, "track_width": 0.2,
                         "microvia_diameter": 0.3, "microvia_drill": 0.1}
        dnc_body = QWidget(); dncg = QGridLayout(dnc_body)
        dncg.setContentsMargins(0, 2, 0, 0); dncg.setHorizontalSpacing(22); dncg.setVerticalSpacing(T.sp("row"))
        for _i, (_label, _attr) in enumerate(_DNC_SPEC):
            _loaded = getattr(pm.default_netclass, _attr, None)
            _present = _loaded is not None
            _mmv = float(_loaded) if _present else _DNC_DEFAULTS[_attr]
            sp = _len_spin(unit, _mmv, width=112, hi_mm=25.0, snap=True)
            sp.setEnabled(dre_writable)
            sp._seed_present = _present          # preserve-by-default: only manage a field the
            sp._seed_mm = float(_mmv)            # file carried, or one the user changed from seed
            all_fields.append(sp); dnc_fields[_attr] = sp
            sp.valueChanged.connect(lambda *_a: _bump_dirty())   # per-section dirty dot
            cell = QVBoxLayout(); cell.setSpacing(2)
            cell.addWidget(pv.field_label(_label)); cell.addWidget(sp)
            dncg.addLayout(cell, _i // 2, _i % 2)
        dncg.setColumnStretch(2, 1)
        pv.apply_quiet_fields(dnc_body)
        dnc_wrap = QWidget(); dncw = QVBoxLayout(dnc_wrap)
        dncw.setContentsMargins(0, 0, 0, 0); dncw.setSpacing(6)
        dncw.addWidget(dnc_body)
        dncw.addWidget(W.body("Via size and drill for the Default class live in Design Rules -> "
                              "Via Diameter / Via Drill above.", dim=True))
        dnc_section = W.CollapsibleSection("Default Net Class", dnc_wrap)
        lay.addWidget(dnc_section)
        host._dnc_fields = dnc_fields                        # drive/test seam

        # ---- Project Meta (text variables: project-wide ${VAR} substitutions) --------------
        #    Rides ▶ Save's "dre" key (save_extended). Add/Remove live OUTSIDE the table and
        #    rebuild from meta_state (blessed-safe idiom — no widget deleted inside its own
        #    signal). A row with a cleared Variable name is dropped, and the manager records
        #    the removal so a delete actually drops the key from the file (not just stops
        #    re-writing it). Built BEFORE flush_dre so the closure can read the rows.
        meta_state = {"vars": sorted(pm.text_variables.items()), "rows": []}
        meta_body = QWidget(); metal = QVBoxLayout(meta_body)
        metal.setContentsMargins(0, 2, 0, 0); metal.setSpacing(T.sp("sm"))
        meta_top = QHBoxLayout(); meta_top.setSpacing(T.sp("sm"))
        meta_top.addWidget(pv.field_label("Text Variables")); meta_top.addStretch(1)
        b_meta_add = W.btn("Add", "ghost", "Add a project text variable ({VAR} -> value)")
        b_meta_del = W.btn("Remove", "ghost", "Remove the last text variable row")
        b_meta_add.setEnabled(dre_writable); b_meta_del.setEnabled(dre_writable)
        meta_top.addWidget(b_meta_add); meta_top.addWidget(b_meta_del)
        metal.addLayout(meta_top)
        meta_tbl = QTableWidget(0, 2)
        meta_tbl.setHorizontalHeaderLabels(["Variable", "Value"])
        meta_tbl.verticalHeader().hide(); meta_tbl.setShowGrid(False)
        meta_tbl.setSelectionMode(QAbstractItemView.NoSelection)
        meta_tbl.setFocusPolicy(Qt.NoFocus)
        meta_tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        meta_tbl.setMaximumHeight(200)
        _mh = meta_tbl.horizontalHeader(); _mh.setHighlightSections(False)
        _mh.setSectionResizeMode(0, QHeaderView.Stretch); _mh.setSectionResizeMode(1, QHeaderView.Stretch)
        pv.apply_netclass_table(meta_tbl)
        metal.addWidget(meta_tbl)
        metal.addWidget(W.body("Project-wide ${VAR} substitutions. Clear a Variable's name or "
                               "Remove it, then Save, to delete it from the project.", dim=True))

        def _meta_capture():
            meta_state["vars"] = [(r["name"].text().strip(), r["value"].text())
                                  for r in meta_state["rows"]]

        def rebuild_meta():
            meta_state["rows"] = []
            meta_tbl.clearContents(); meta_tbl.setRowCount(len(meta_state["vars"]))
            for r, (nm, vv) in enumerate(meta_state["vars"]):
                meta_tbl.setRowHeight(r, 34)
                ne = pv.nc_cell_font(QLineEdit(str(nm))); ne.setEnabled(dre_writable)
                ne.setPlaceholderText("VARIABLE"); ne.setCursorPosition(0)
                ve = pv.nc_cell_font(QLineEdit(str(vv))); ve.setEnabled(dre_writable)
                ve.setPlaceholderText("value"); ve.setCursorPosition(0)
                meta_tbl.setCellWidget(r, 0, ne); meta_tbl.setCellWidget(r, 1, ve)
                ne.textChanged.connect(lambda *_a: _bump_dirty())   # per-section dirty dot
                ve.textChanged.connect(lambda *_a: _bump_dirty())
                meta_state["rows"].append({"name": ne, "value": ve})

        def _meta_add():
            _meta_capture(); meta_state["vars"] = meta_state["vars"] + [("", "")]
            rebuild_meta(); _bump_dirty()

        def _meta_del():
            _meta_capture()
            if meta_state["vars"]:
                meta_state["vars"] = meta_state["vars"][:-1]
            rebuild_meta(); _bump_dirty()

        b_meta_add.clicked.connect(lambda: _meta_add())
        b_meta_del.clicked.connect(lambda: _meta_del())
        rebuild_meta()
        meta_section = W.CollapsibleSection("Project Meta", meta_body)
        lay.addWidget(meta_section)
        host._meta_tbl = meta_tbl                             # drive/test seam
        host._meta_add = _meta_add

        # ---- flush closure: push every extended edit into pm, for ▶ Save (save_extended) ----
        def flush_dre():
            for key in _PS_SPEC:
                pd_state[key] = _ps_read(key)
            track_vals = [t[0] for t in pd_state["track"] if t[0] > 0]
            if track_vals or pm.was_present("track_widths"):
                pm.set_track_widths(track_vals)
            else:
                pm.track_widths = []
            via_vals = [(t[0], t[1]) for t in pd_state["via"] if not (t[0] == 0 and t[1] == 0)]
            if via_vals or pm.was_present("via_dimensions"):
                pm.set_via_dimensions(via_vals)
            else:
                pm.via_dimensions = []
            dp_vals = [(t[0], t[1], t[2]) for t in pd_state["dp"]
                       if not (t[0] == 0 and t[1] == 0 and t[2] == 0)]
            if dp_vals or pm.was_present("diff_pair_dimensions"):
                pm.set_diff_pair_dimensions(dp_vals)
            else:
                pm.diff_pair_dimensions = []
            pm.drc_severities = {}
            for rid, cb in sev_combos["drc"].items():
                if cb.currentText() in psm.SEVERITY_LEVELS:
                    pm.set_drc_severity(rid, cb.currentText())
            pm.erc_severities = {}
            for rid, cb in sev_combos["erc"].items():
                if cb.currentText() in psm.SEVERITY_LEVELS:
                    pm.set_erc_severity(rid, cb.currentText())
            if pinmap_state["touched"] or pm.was_present("erc.pin_map"):
                pm.ensure_erc_pin_map()
                for a in range(n_pin):
                    for b in range(n_pin):
                        pm.set_erc_pin_map_entry(a, b, pin_matrix[a][b], symmetric=False)
            pm.set_erc_exclusions(list(pm.erc_exclusions))   # round-trip preserve (opaque)
            # M4 Default Net Class: manage clearance/track/microvia from the Default-row spins.
            # via_diameter/via_drill stay owned by the flat Design-Rules "Via Diameter/Drill"
            # spins (save_design_rules_only, "dr") — leaving them None (unmanaged) keeps dre on
            # DISJOINT keys from dr, so dre never reverts a flat via edit (the M3 landmine).
            # Preserve-by-default: only manage a field the file actually carried, or one the
            # user changed from its seed, so an untouched panel never materialises a Default
            # class into a project that lacked one.
            pm.default_netclass = psm.DefaultNetClassSettings()

            def _dnc_val(_sp):
                if _sp._seed_present or abs(_sp._mm - _sp._seed_mm) > 1e-9:
                    return _sp._mm
                return None
            pm.set_default_netclass(
                clearance=_dnc_val(dnc_fields["clearance"]),
                track_width=_dnc_val(dnc_fields["track_width"]),
                microvia_diameter=_dnc_val(dnc_fields["microvia_diameter"]),
                microvia_drill=_dnc_val(dnc_fields["microvia_drill"]),
            )

            # M5 Project Meta: reconcile pm.text_variables to exactly the table (rows with a
            # non-empty name). Any loaded var no longer wanted is REMOVED (recorded so the save
            # deletes it from the file). set/remove keep pm._removed_text_vars consistent.
            want = {}
            for _r in meta_state["rows"]:
                _nm = _r["name"].text().strip()
                if _nm:
                    want[_nm] = _r["value"].text()
            for _nm in list(pm.text_variables.keys()):
                if _nm not in want:
                    pm.remove_text_variable(_nm)
            for _nm, _vv in want.items():
                pm.set_text_variable(_nm, _vv)

        # Baseline AFTER an initial flush so sentinel/normalisation is folded in and an
        # untouched panel reproduces the same fingerprint (→ ▶ Save reports nothing to write).
        flush_dre()
        S["dre_flush"] = flush_dre
        S["dre_base"] = _dre_fingerprint(pm)
        host._dre_flush = flush_dre                          # drive/test seams
        host._sev_combos = sev_combos
        host._pin_matrix = pin_matrix
        host._psize_tables = pd_tables

        # ── per-section dirty dots: each collapsible header shows an accent dot when its
        #    own state diverges from the as-loaded baseline, so the ▶ Save preview scope is
        #    visible while scrolling even with the section collapsed. Signal-driven (edits
        #    call _bump_dirty), re-baselined on save. ──────────────────────────────────────
        def _fp_predefined():
            return tuple(tuple(round(x, 6) for x in r) for k in _PS_SPEC for r in _ps_read(k))

        def _fp_severities():
            d = tuple(sorted((rid, cb.currentText()) for rid, cb in sev_combos["drc"].items()))
            e = tuple(sorted((rid, cb.currentText()) for rid, cb in sev_combos["erc"].items()))
            return (d, e)

        def _fp_default_nc():
            return tuple((a, round(float(sp._mm), 6)) for a, sp in dnc_fields.items())

        def _fp_meta():
            return tuple((r["name"].text().strip(), r["value"].text())
                         for r in meta_state["rows"])

        _sec_defs = [(ps_section, _fp_predefined), (sev_section, _fp_severities),
                     (dnc_section, _fp_default_nc), (meta_section, _fp_meta)]
        _sec_base = {}

        def _capture_section_baselines():
            for sec, fn in _sec_defs:
                _sec_base[id(sec)] = fn()

        def _bump_dirty():
            if not _sec_base:
                return
            for sec, fn in _sec_defs:
                try:
                    sec.set_dirty(fn() != _sec_base.get(id(sec)))
                except RuntimeError:                          # section deleted by a rebuild
                    pass

        _capture_section_baselines()
        S["rebaseline_sections"] = _capture_section_baselines
        S["refresh_dirty"] = _bump_dirty
        host._section_dirty = lambda: {sec._title: sec.is_dirty() for sec, _ in _sec_defs}
        host._bump_dirty = _bump_dirty

        # ── Section C — Net Classes (ncm, editable, sticky-header table) ───────────────────
        add_section("Net Classes")

        nc_top = QHBoxLayout(); nc_top.setSpacing(T.sp("sm"))
        nc_filter = QLineEdit(); nc_filter.setPlaceholderText("Filter classes"); nc_filter.setFixedWidth(240)
        nc_filter.setToolTip("Narrow the visible classes by name or pattern")
        nc_top.addWidget(nc_filter); nc_top.addStretch(1)
        b_newnc = W.btn("New Net Class", "ghost", "Add a new net class defaulted to the profile floors")
        nc_top.addWidget(b_newnc)
        lay.addLayout(nc_top)

        tbl = QTableWidget(0, len(_NC_COLS))
        tbl.setHorizontalHeaderLabels(_NC_COLS)
        tbl.verticalHeader().hide()
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.NoSelection)
        tbl.setShowGrid(False)
        tbl.setWordWrap(False)
        tbl.setFocusPolicy(Qt.NoFocus)
        # 16 columns (KiCad-native breadth) overflow the panel — scroll horizontally rather
        # than crush the cells.
        tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tbl.setMinimumHeight(240); tbl.setMaximumHeight(360)
        hdr = tbl.horizontalHeader(); hdr.setHighlightSections(False)
        hdr.setSectionResizeMode(0, QHeaderView.Interactive); tbl.setColumnWidth(0, 150)
        for _f, ci in _NC_LEN:
            hdr.setSectionResizeMode(ci, QHeaderView.Fixed); tbl.setColumnWidth(ci, 100)
        hdr.setSectionResizeMode(_NC_COL_STYLE, QHeaderView.Fixed); tbl.setColumnWidth(_NC_COL_STYLE, 104)
        hdr.setSectionResizeMode(_NC_COL_PRIORITY, QHeaderView.Fixed); tbl.setColumnWidth(_NC_COL_PRIORITY, 76)
        hdr.setSectionResizeMode(_NC_COL_PATTERNS, QHeaderView.Interactive); tbl.setColumnWidth(_NC_COL_PATTERNS, 200)
        hdr.setSectionResizeMode(_NC_COL_DELETE, QHeaderView.Fixed); tbl.setColumnWidth(_NC_COL_DELETE, 104)

        pv.apply_netclass_table(tbl)
        lay.addWidget(tbl)

        nc_status = QVBoxLayout(); nc_status.setSpacing(T.sp("xs")); lay.addLayout(nc_status)
        S["nc_status"] = nc_status

        def _apply_filter():
            t = nc_filter.text().strip().lower()
            for r, row in enumerate(nc_state["rows"]):
                name = row["name"].lower(); pats = row["patterns"].text().lower()
                vis = True if not t else (t in name or t in pats)
                tbl.setRowHidden(r, not vis)

        def _nc_visible_count():
            return sum(1 for r in range(tbl.rowCount()) if not tbl.isRowHidden(r))

        def rebuild_netclasses():
            nc_fields.clear(); nc_state["rows"] = []
            tbl.clearContents()
            mgr = nc_state["mgr"]
            names = mgr.list_netclasses()
            tbl.setRowCount(len(names))
            for r, name in enumerate(names):
                nc = mgr.get_netclass(name)
                tbl.setRowHeight(r, 34)
                tbl.setCellWidget(r, 0, pv.nc_name_cell(
                    nc,
                    on_pick=lambda _nc: _log(f"{_nc.name} color set to {_nc.color}."),
                    on_rename=_nc_rename))
                spins = {}
                for field, ci in _NC_LEN:
                    mmv = getattr(nc, field, None)
                    is_dp = field in ("diff_pair_width", "diff_pair_gap")
                    sp = _len_spin(unit, mmv if mmv is not None else 0.0, width=104, hi_mm=25.0)
                    pv.nc_cell_font(sp)
                    if is_dp:
                        sp.setToolTip("Diff-pair dimension: 0 means this class has no diff pair. "
                                      "Set both width and gap to enable one.")

                        def _sync_dp_dim(_v=None, _sp=sp):
                            want = "nc_dp_zero" if _sp._mm <= 0 else ""
                            if _sp.objectName() != want:
                                _sp.setObjectName(want)
                                _sp.style().unpolish(_sp); _sp.style().polish(_sp)
                        _sync_dp_dim()
                        sp.valueChanged.connect(_sync_dp_dim)
                    nc_fields.append(sp); spins[field] = sp
                    tbl.setCellWidget(r, ci, sp)
                style_cb = QComboBox()
                for _disp, _val in _NC_LINE_STYLES:
                    style_cb.addItem(_disp, _val)
                _si = style_cb.findData(getattr(nc, "line_style", "solid"))
                style_cb.setCurrentIndex(_si if _si >= 0 else 0)
                style_cb.setToolTip("Schematic wire/bus line style for this net class")
                pv.nc_cell_font(style_cb)
                tbl.setCellWidget(r, _NC_COL_STYLE, style_cb)
                pr = pv.nc_cell_font(_int_spin(getattr(nc, "priority", 0), width=72))
                tbl.setCellWidget(r, _NC_COL_PRIORITY, pr)
                edit = pv.nc_cell_font(QLineEdit(", ".join(nc.patterns or [])))
                edit.setToolTip("Member nets or patterns, comma separated")
                edit.setCursorPosition(0)
                edit.textChanged.connect(lambda _t: _apply_filter())
                tbl.setCellWidget(r, _NC_COL_PATTERNS, edit)
                dbtn = W.btn("Delete", "ghost", f"Remove {name}"); dbtn.setFixedHeight(26)
                dbtn.clicked.connect(lambda _=False, _nc=nc: _nc_delete(_nc.name))
                dwrap = QWidget(); dwl = QHBoxLayout(dwrap); dwl.setContentsMargins(2, 3, 6, 3)
                dwl.addWidget(dbtn); dwl.addStretch(1)
                tbl.setCellWidget(r, _NC_COL_DELETE, dwrap)
                nc_state["rows"].append({"name": name, "spins": spins, "priority": pr,
                                         "patterns": edit, "line_style": style_cb})
            _apply_filter()

        def _nc_new():
            _commit_netclasses()
            mgr = nc_state["mgr"]; base = "NEW_CLASS"; name = base; i = 2
            while mgr.get_netclass(name) is not None:
                name = f"{base}_{i}"; i += 1
            floor = ncm.NETCLASS_PROFILES.get(prof_state["fab"], ncm.NETCLASS_PROFILES[ncm.DEFAULT_NETCLASS_PROFILE])
            mgr.add_netclass(ncm.NetClass(
                name=name, clearance=floor["min_clearance"], track_width=floor["min_track"],
                via_diameter=floor["min_via"], via_drill=floor["min_drill"]))
            rebuild_netclasses()

        def _nc_rename(nc, new_name):
            old = nc.name
            if not nc_state["mgr"].rename_netclass(old, new_name):
                if new_name and new_name != old:
                    _log(f"Could not rename '{old}' to '{new_name}' (name already in use).")
                return None
            for row in nc_state["rows"]:
                if row["name"] == old:
                    row["name"] = new_name
                    break
            _apply_filter()
            _log(f"Net class '{old}' renamed to '{new_name}'. Save To Project to persist it.")
            return new_name

        def _nc_delete(name):
            if not confirm(host, "Delete Net Class",
                           f"Remove the net class '{name}' from this profile?\n\n"
                           f"It is applied when you save the profile."):
                return
            _commit_netclasses()
            nc_state["mgr"].remove_netclass(name)
            rebuild_netclasses()

        def _nc_duplicate(name):
            """Right-click Duplicate: a fast variant (<name>_2) with the same dimensions,
            style and priority but no patterns (so it doesn't double-claim member nets)."""
            _commit_netclasses()
            new = nc_state["mgr"].duplicate_netclass(name)
            if new:
                rebuild_netclasses()
                _log(f"Duplicated net class '{name}' as '{new}'. Save To Project to persist it.")
            return new

        def _nc_context_menu(pos):
            row = tbl.rowAt(pos.y())
            if row < 0 or row >= len(nc_state["rows"]):
                return
            name = nc_state["rows"][row]["name"]
            m = QMenu(host)
            act_dup = m.addAction(f"Duplicate '{name}'")
            act_del = m.addAction(f"Delete '{name}'")
            chosen = m.exec_(tbl.viewport().mapToGlobal(pos))
            if chosen is act_dup:
                _nc_duplicate(name)
            elif chosen is act_del:
                _nc_delete(name)

        tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        tbl.customContextMenuRequested.connect(_nc_context_menu)

        nc_filter.textChanged.connect(lambda _t: _apply_filter())
        b_newnc.clicked.connect(lambda: _nc_new())
        rebuild_netclasses()

        host._nc_new = _nc_new
        host._nc_delete = _nc_delete
        host._nc_duplicate = _nc_duplicate
        host._nc_rename = _nc_rename
        host._nc_name_cell = lambda r: tbl.cellWidget(r, 0)
        host._nc_filter_edit = nc_filter
        host._nc_filter = nc_filter.setText
        host._nc_visible_count = _nc_visible_count
        host._nc_rows = lambda: nc_state["rows"]

        # ── Section D — Board Geometry (nd_board_setup, editable) ──────────────────────────
        add_section("Board Geometry")
        setup = {}
        if board is None:
            lay.addWidget(W.empty_state("No Board Found", glyph=icons.GLYPHS["cube"],
                                        sub="This project has no .kicad_pcb to configure."))
        else:
            try:
                setup = nd_board_setup.load_board_setup(board, include_aliases=False)
            except Exception as e:  # noqa: BLE001
                lay.addWidget(W.body(f"Could not read board setup: {e}", dim=True))
        explicit = set(setup)
        S["setup"] = setup
        S["explicit"] = explicit

        if board is not None:
            bgw = QWidget(); bgg = QGridLayout(bgw); bgg.setContentsMargins(0, T.sp("xs"), 0, 0)
            bgg.setHorizontalSpacing(T.sp("card")); bgg.setVerticalSpacing(T.sp("row"))
            rr = 0

            def _notset(key):
                return W.body("Not Set", dim=True) if key not in explicit else None

            for key in sorted(nd_board_setup.SETUP_NUMERIC_KEYS):
                bgg.addWidget(pv.field_label(key.replace("_", " ").title()), rr, 0)
                if key.endswith("_ratio"):
                    sp = _ratio_spin(setup.get(key, 0.0)); bg_fields[key] = ("ratio", sp)
                else:
                    sp = _len_spin(unit, setup.get(key, 0.0), width=112, lo_mm=-10.0, hi_mm=50.0)
                    all_fields.append(sp); bg_fields[key] = ("num", sp)
                bgg.addWidget(sp, rr, 1)
                ns = _notset(key)
                if ns:
                    bgg.addWidget(ns, rr, 2)
                rr += 1
            for key in sorted(nd_board_setup.SETUP_COORD_KEYS):
                bgg.addWidget(pv.field_label(key.replace("_", " ").title()), rr, 0)
                val = setup.get(key, (0.0, 0.0))
                sx = _len_spin(unit, val[0], width=112, lo_mm=-1000.0, hi_mm=1000.0)
                sy = _len_spin(unit, val[1], width=112, lo_mm=-1000.0, hi_mm=1000.0)
                all_fields.append(sx); all_fields.append(sy)
                cw = QWidget(); ch = QHBoxLayout(cw); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(T.sp("sm"))
                ch.addWidget(sx); ch.addWidget(sy); ch.addStretch(1)
                bgg.addWidget(cw, rr, 1)
                bg_fields[key] = ("coord", (sx, sy))
                ns = _notset(key)
                if ns:
                    bgg.addWidget(ns, rr, 2)
                rr += 1
            for key in sorted(nd_board_setup.SETUP_BOOL_KEYS):
                bgg.addWidget(pv.field_label(key.replace("_", " ").title()), rr, 0)
                cb = QCheckBox(); cb.setChecked(bool(setup.get(key, False)))
                bg_fields[key] = ("bool", cb)
                bgg.addWidget(cb, rr, 1)
                ns = _notset(key)
                if ns:
                    bgg.addWidget(ns, rr, 2)
                rr += 1
            bgg.setColumnStretch(3, 1)
            pv.apply_quiet_fields(bgw)
            lay.addWidget(bgw)
            lay.addWidget(W.body(board.name, dim=True, mono=True))
        host._bg_fields = bg_fields                         # drive/test seam

        # ── profile CRUD + load/pull (need the combo + rebuild_* — live here) ──────────────
        def _mgr_from_netclasses(netclasses):
            m = ncm.NetClassManager()
            for nc in netclasses:
                m.add_netclass(nc)
            return m

        def _load_profile(name):
            prof = pcbprof.get_profile(name)
            if prof is None:
                return
            _commit_netclasses()
            prof_state["name"] = name
            prof_state["fab"] = prof.fab
            fab_combo.blockSignals(True)                   # sync the Fab selector to the profile
            if prof.fab in [fab_combo.itemText(i) for i in range(fab_combo.count())]:
                fab_combo.setCurrentText(prof.fab)
            fab_combo.blockSignals(False)
            nc_state["mgr"] = _mgr_from_netclasses(prof.netclasses)
            host._ncmgr = nc_state["mgr"]
            rebuild_netclasses()
            rebuild_fabfacts()
            reseed_stackup()
            refresh_units()
            clear_layout(nc_status)

        def _pull_from_kicad():
            pro_ = kicad_tools.project_pro_file(state.project) if state.project else None
            if not pro_ or not Path(pro_).exists():
                _log("No .kicad_pro found for this project."); return
            prof = pcbprof.profile_from_project(pro_, f"{Path(pro_).stem} (from KiCad)", prof_state["fab"])
            if not prof.netclasses:
                _log("No net classes found in the KiCad project."); return
            prof_state["name"] = prof.name
            nc_state["mgr"] = _mgr_from_netclasses(prof.netclasses)
            host._ncmgr = nc_state["mgr"]
            prof_combo.blockSignals(True)
            if prof_combo.findText(prof.name) < 0:
                prof_combo.addItem(prof.name)
            prof_combo.setCurrentText(prof.name)
            prof_combo.blockSignals(False)
            rebuild_netclasses(); refresh_units(); clear_layout(nc_status)
            _log(f"Pulled {plural(len(prof.netclasses), 'net class', 'net classes')} from {Path(pro_).name}. "
                 f"Use Save Profile to keep it.")

        def _refresh_profile_list(select=None):
            prof_combo.blockSignals(True)
            prof_combo.clear()
            prof_combo.addItems(_profile_names())
            if select:
                prof_combo.setCurrentText(select)
            prof_combo.blockSignals(False)

        def _profile_from_current():
            _commit_netclasses()
            return pcbprof.Profile(prof_state["name"], prof_state["fab"],
                                   list(nc_state["mgr"].net_classes.values()))

        def _new_profile():
            from PyQt5.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(host, "New Profile", "Profile name:")
            name = (name or "").strip()
            if not ok or not name:
                return
            _commit_netclasses()
            prof = pcbprof.Profile(name, prof_state["fab"],
                                   list(nc_state["mgr"].net_classes.values()))
            errs = pcbprof.validate_profile(prof)
            if errs:
                _log("Cannot save profile: " + "; ".join(errs)); return
            pcbprof.save_profile(prof)
            prof_state["name"] = name
            _refresh_profile_list(select=name)
            _log(f"Saved profile '{name}'.")

        def _save_profile():
            prof = _profile_from_current()
            errs = pcbprof.validate_profile(prof)
            if errs:
                _log("Cannot save profile: " + "; ".join(errs)); return
            pcbprof.save_profile(prof)
            _log(f"Updated profile '{prof.name}'.")

        def _delete_profile():
            name = prof_state["name"]
            builtin = pcbprof.is_builtin(name)
            if builtin and not pcbprof.has_user_profile(name):
                _log(f"'{name}' is a built-in profile and can't be deleted.")
                return
            verb = "Revert your override of" if builtin else "Delete"
            detail = ("This restores the built-in defaults."
                      if builtin else "This permanently removes it from disk and cannot be undone.")
            if not confirm(host, "Delete Profile", f"{verb} the profile '{name}'?\n\n{detail}"):
                return
            if pcbprof.delete_profile(name):
                _log(f"{'Reverted' if builtin else 'Deleted'} profile '{name}'.")
            else:
                _log(f"'{name}' is a built-in profile and can't be deleted.")
            names = _profile_names()
            sel = name if name in names else (
                pcbprof.NETDECK if pcbprof.NETDECK in names else (names[0] if names else None))
            _refresh_profile_list(select=sel)
            if sel:
                _load_profile(sel)

        def _load_vault_template():
            # PARITY: ncm.create_vault_standard_template / ncm.netclass_profiles — generate the
            # vault-standard net classes for the current fab profile into the editor.
            prof = prof_state["fab"] if prof_state["fab"] in ncm.netclass_profiles() \
                else ncm.DEFAULT_NETCLASS_PROFILE
            if not confirm(host, "Load Vault-Standard Template",
                           f"Replace the net classes in the editor with the vault standard for "
                           f"{prof}? Unsaved edits are discarded."):
                return
            nc_state["mgr"] = ncm.create_vault_standard_template(prof)
            host._ncmgr = nc_state["mgr"]
            prof_state["name"] = f"Vault Standard ({prof})"
            rebuild_netclasses(); refresh_units(); clear_layout(nc_status)
            _log(f"Loaded the vault-standard template for {prof}. Use Save Profile to keep it.")

        def _load_vault_saved():
            # PARITY: ncm.load_vault_standard — load the saved editable vault standard (or the
            # built-in default) into the editor.
            if not confirm(host, "Load Saved Vault Standard",
                           "Replace the net classes in the editor with the saved vault standard? "
                           "Unsaved edits are discarded."):
                return
            nc_state["mgr"] = ncm.load_vault_standard()
            host._ncmgr = nc_state["mgr"]
            prof_state["name"] = "Vault Standard"
            rebuild_netclasses(); refresh_units(); clear_layout(nc_status)
            _log(f"Loaded the saved vault standard "
                 f"({plural(len(nc_state['mgr'].list_netclasses()), 'class', 'classes')}).")

        def _save_vault():
            # PARITY: ncm.save_vault_standard — persist the current net classes as the canonical
            # vault standard other projects load from.
            _commit_netclasses()
            path = ncm.save_vault_standard(nc_state["mgr"])
            _log(f"Saved the current net classes as the vault standard: {Path(path).name}.")

        prof_combo.currentTextChanged.connect(_load_profile)
        host._profile_seg = prof_combo
        host._profile_combo = prof_combo
        host._unit_seg = unit_seg
        host._load_profile = _load_profile
        host._pull_from_kicad = _pull_from_kicad
        host._new_profile = _new_profile
        host._save_profile = _save_profile
        host._delete_profile = _delete_profile
        host._load_vault_template = _load_vault_template
        host._load_vault_saved = _load_vault_saved
        host._save_vault = _save_vault

        controller = SimpleNamespace(nc_state=nc_state, prof_state=prof_state,
                                     prof_combo=prof_combo, unit_seg=unit_seg)
        return root, controller

    # ── the write path (▶ Save To Project) — GUI-thread capture + off-thread write ────────
    _ALL = {"dr", "dre", "nc", "bg", "fab"}

    def _capture():
        """GUI thread: flush live edits into the managers, then snapshot everything the
        off-thread write needs (workers never touch a widget)."""
        _commit_netclasses()
        pm = S.get("pm")
        dr_writable = S.get("dr_writable", False)
        if dr_writable and pm is not None:
            for attr, sp in dr_fields.items():
                setattr(pm.settings, attr, mm_to_mils(sp._mm))
        setup = S.get("setup", {}) or {}
        explicit = S.get("explicit", set())

        def _bg_seed(key, kind):
            if kind == "coord":
                return setup.get(key, (0.0, 0.0))
            if kind == "bool":
                return bool(setup.get(key, False))
            return float(setup.get(key, 0.0))

        def _bg_changed(kind, val, seed):
            if kind == "coord":
                return abs(val[0] - seed[0]) > 1e-9 or abs(val[1] - seed[1]) > 1e-9
            if kind == "bool":
                return bool(val) != bool(seed)
            return abs(float(val) - float(seed)) > 1e-9

        bvals = {}
        for key, (kind, w) in bg_fields.items():
            if kind == "bool":
                val = w.isChecked()
            elif kind == "coord":
                val = (w[0]._mm, w[1]._mm)
            elif kind == "ratio":
                val = w.value()
            else:
                val = w._mm
            if key in explicit or _bg_changed(kind, val, _bg_seed(key, kind)):
                bvals[key] = val

        mgr = nc_state["mgr"]
        prof_snapshot = pcbprof.Profile(prof_state["name"], prof_state["fab"],
                                        list(mgr.net_classes.values()))
        # Bake the edited physical thickness into the fab-preset copy so the ONE fab write
        # (▶ Save) uses the spinner value, never silently reverting it to the preset default.
        # The stackup stays the preset's (per-layer editing is a KiCad-GUI job — logged gap).
        fab_preset = fabp.get_preset(prof_state["fab"])
        thick_sp = S.get("thick_field")
        if fab_preset is not None and thick_sp is not None:
            import dataclasses
            tmm = float(getattr(thick_sp, "_mm", 0.0) or 0.0)
            if tmm > 0 and abs(tmm - fab_preset.board_thickness_mm) > 1e-9:
                fab_preset = dataclasses.replace(fab_preset, board_thickness_mm=tmm)
        # Extended design rules (severities / pin map / predefined tables): flush the live
        # widgets into pm, then diff the fingerprint so ▶ Save writes save_extended ONLY when
        # something actually changed (no idle .kicad_pro churn).
        dre_ok = False; dre_dirty = False; n_dre = 0
        dre_flush = S.get("dre_flush")
        if dre_flush is not None and pm is not None and S.get("dre_writable"):
            dre_flush()
            dre_ok = True
            dre_dirty = _dre_fingerprint(pm) != S.get("dre_base")
            n_dre = (len(pm.drc_severities) + len(pm.erc_severities)
                     + sum(1 for row in pm.erc_pin_map for x in row if x)
                     + max(0, len(pm.track_widths) - 1) + max(0, len(pm.via_dimensions) - 1)
                     + max(0, len(pm.diff_pair_dimensions) - 1)
                     + sum(1 for _ in pm.default_netclass.managed_items())
                     + len(pm.text_variables) + len(pm._removed_text_vars))
        return {
            "project": project, "pro": pro, "board": board,
            "dr_ok": dr_writable, "pm": pm, "n_dr": len(dr_fields),
            "dre_ok": dre_ok, "dre_dirty": dre_dirty, "n_dre": n_dre,
            "mgr": mgr, "n_nc": len(mgr.list_netclasses()), "bvals": bvals,
            "prof_snapshot": prof_snapshot, "prof_ok": not pcbprof.validate_profile(prof_snapshot),
            "fab_preset": fab_preset,
        }

    def _save_job(snap, sections):
        """OFF thread: write the selected sections from the snapshot; returns (done, err,
        stats). Mirrors the legacy save() exactly, gated per section by ``sections``."""
        done = []
        err = None
        stats = {"dr_fields": 0, "dre": 0, "nc_written": 0, "nc_preserved": [], "bg_keys": 0,
                 "profile_synced": None, "fab_board": None}
        pro_ = snap["pro"]; board_ = snap["board"]; pm = snap["pm"]; mgr = snap["mgr"]
        if pro_:
            # Three writers touch the SAME .kicad_pro (design rules, extended, net classes) —
            # only the first makes the .bak so a pristine pre-save copy survives (save_extended
            # writes DISJOINT keys from save_design_rules_only, so running both is safe).
            pro_backed_up = False
            if "dr" in sections and snap["dr_ok"] and pm is not None and pm.save_design_rules_only(pro_, backup=True):
                done.append("design rules"); stats["dr_fields"] = snap["n_dr"]; pro_backed_up = True
            if ("dre" in sections and snap.get("dre_ok") and snap.get("dre_dirty")
                    and pm is not None and pm.save_extended(pro_, backup=not pro_backed_up)):
                done.append("rule tables & severities"); stats["dre"] = snap.get("n_dre", 0)
                pro_backed_up = True
            if "nc" in sections and mgr.save_to_project(pro_, backup=not pro_backed_up):
                done.append("net classes"); pro_backed_up = True
                stats["nc_written"] = snap["n_nc"]
                stats["nc_preserved"] = [n for n in mgr.last_preserved_unmanaged if n]
                if snap["prof_ok"]:
                    pcbprof.save_profile(snap["prof_snapshot"])
                    stats["profile_synced"] = snap["prof_snapshot"].name
        # Two writers touch the SAME .kicad_pcb (board geometry, then fab floor) — only the
        # first makes the .bak so the surviving backup is the PRISTINE pre-save board, mirroring
        # the pro_backed_up scheme above (else fab's .bak would overwrite bg's with the already-
        # geometry-edited board, and the board-geometry edit could not be rolled back).
        board_backed_up = False
        if "bg" in sections and board_ is not None and snap["bvals"]:
            try:
                nd_board_setup.save_board_setup(board_, snap["bvals"], backup=not board_backed_up)
                done.append("board geometry"); stats["bg_keys"] = len(snap["bvals"])
                board_backed_up = True
            except Exception as e:  # noqa: BLE001
                err = str(e)
        if "fab" in sections and board_ is not None and snap["fab_preset"] is not None:
            try:
                rep = conform.write_fab_to_board(board_, snap["fab_preset"], backup=not board_backed_up)
                if rep.get("written"):
                    done.append("fab floor")
                    stats["fab_board"] = rep
                    board_backed_up = True
            except Exception as e:  # noqa: BLE001
                err = (err + "; " if err else "") + f"fab floor: {e}"
        S["last_done"] = list(done)
        return done, err, stats

    def save():
        """The legacy ``_save`` seam — capture on the GUI thread, write ALL sections off it,
        then log the digest + mark persisted + refresh the verdict."""
        if not project:
            _log("No project selected to save into."); return
        if busy.get("on"):
            return
        snap = _capture()
        busy["on"] = True

        def populate(res, ok):
            busy["on"] = False
            done, err, stats = res if res else ([], None, {})
            if done:
                _log(_save_summary(done, stats))
                _mark_persisted(done)
            elif not err:
                _log("Nothing saved (no project files found).")
            if err:
                _log(f"Some board writes did not save: {err}")
            _push_verdict()
        run_populate(ctx, lambda: _save_job(snap, _ALL), populate, busy="Saving to project...")

    # ── the ▶ Save To Project primary flow (audit → preview → apply, headless-safe) ───────
    def _save_audit(snap):
        if not snap["project"]:
            save_flow.empty = "No project selected to save into."
            return []
        ops = []
        if snap["pro"]:
            if snap["dr_ok"] and snap["n_dr"]:
                ops.append({"key": "dr", "label": f"Design rules: {plural(snap['n_dr'], 'field')}",
                            "detail": "", "safe": True})
            if snap.get("dre_ok") and snap.get("dre_dirty"):
                ops.append({"key": "dre",
                            "label": "Rule severities, size tables, pin map, Default class & meta",
                            "detail": "", "safe": True})
            if snap["n_nc"]:
                ops.append({"key": "nc", "label": f"Net classes: {plural(snap['n_nc'], 'class', 'classes')}",
                            "detail": "", "safe": True})
        if snap["board"] is not None and snap["bvals"]:
            ops.append({"key": "bg", "label": f"Board geometry: {plural(len(snap['bvals']), 'key')}",
                        "detail": "", "safe": True})
        if snap["board"] is not None and snap["fab_preset"] is not None:
            ops.append({"key": "fab",
                        "label": f"Fab floor: stackup + {snap['fab_preset'].board_thickness_mm:g} mm thickness",
                        "detail": "", "safe": True})
        if not ops:
            save_flow.empty = "Nothing to write: no project files, or nothing changed."
            S["last_violations"] = []
            return ops
        # Validate-on-save: surface net-class-vs-fab-floor violations in the preview as
        # non-blocking, opt-in "acknowledge" rows (amber, unchecked). Checking one is a
        # suppress-and-save-anyway gesture; the net-class write proceeds regardless — the
        # check never blocks the save. Only shown when a write is actually pending.
        violations = []
        if snap["n_nc"]:
            fp = snap.get("fab_preset")
            fab_name = snap["prof_snapshot"].fab if snap.get("prof_snapshot") else ncm.DEFAULT_NETCLASS_PROFILE
            try:
                floor = ncm.floor_from_fab_preset(fp) if fp is not None else None
                violations = ncm.validate_netclasses(snap["mgr"], fab_name, floor=floor)
            except Exception:  # noqa: BLE001
                violations = []
            for i, v in enumerate(violations[:20]):
                ops.append({"key": f"ack:{i}", "safe": False,
                            "label": f"Below fab floor: {v.get('netclass', '')} ({v.get('issue', '')})",
                            "detail": "Under the fabrication minimum. Check to acknowledge and save "
                                      "anyway; the net-class write is not blocked either way."})
        S["last_violations"] = violations
        return ops

    def _save_intro(snap, ops):
        where = Path(snap["pro"]).name if snap["pro"] else "the board"
        return f"Write the checked sections into {where} (a .bak is kept per file):"

    def _save_apply(snap, keys):
        done, err, stats = _save_job(snap, set(keys))
        r = {"summary": _save_summary(done, stats) if done else "Nothing was written.",
             "done": [f"wrote {d}" for d in done]}
        if err:
            r["errors"] = [f"Some board writes did not save: {err}"]
        return r

    def _after_save():
        if S.get("last_done"):
            _mark_persisted(S["last_done"])
        _push_verdict()

    save_flow = kit.PrimaryFlow(
        label="▶ Save To Project", audit=_save_audit, intro=_save_intro, apply=_save_apply,
        tip="Preview what will be written, then write the design rules, net classes, board "
            "geometry and fab floor into the project",
        empty="Nothing to write: no project files, or nothing changed.")

    # ── verdict (push): net classes vs the profile's fab floor ────────────────────────────
    def _compute_verdict():
        if not project:
            return None
        _commit_netclasses()
        try:
            issues = ncm.validate_netclasses(nc_state["mgr"], prof_state["fab"])
        except Exception:  # noqa: BLE001
            return None
        if not issues:
            return W.VerdictState(kind="ok", title="In Spec",
                                  subtitle="Net classes meet the fabrication minimums.")
        names = []
        for i in issues:
            for tok in str(i.get("netclass", "")).split(","):
                tok = tok.strip()
                if tok and tok not in names:
                    names.append(tok)
        shown = ", ".join(names[:4]) + (f" +{len(names) - 4} more" if len(names) > 4 else "")
        sub = (f"Under the profile minimum: {shown}" if shown
               else "One or more classes fall under the fabrication floor.")
        return W.VerdictState(kind="warn", title=f"{len(issues)} Below Fab Floor", subtitle=sub)

    def _push_verdict():
        try:
            host._set_verdict(_compute_verdict())
        except Exception:  # noqa: BLE001
            pass

    def validate():
        if busy.get("on"):
            return
        nc_status = S.get("nc_status")
        if nc_status is not None:
            clear_layout(nc_status)
        _commit_netclasses()
        issues = ncm.validate_netclasses(nc_state["mgr"], prof_state["fab"])
        if not issues:
            if nc_status is not None:
                nc_status.addWidget(W.body("All classes meet the fabrication minimums.", dim=True))
            _log("Net classes valid.")
        else:
            if nc_status is not None:
                for iss in issues[:20]:
                    nc_status.addWidget(W.body(f"{iss.get('netclass', '')}: {iss.get('issue', '')}", dim=True))
            _log(f"{plural(len(issues), 'net-class issue')} found.")
        _push_verdict()

    # ── Manage machinery: Clear KiCad Cache (parity: clear_project_cache[_files]) ──────────
    def _clear_cache():
        if busy.get("on"):
            return
        cfg = getattr(ctx, "cfg", None) or {}
        root_dir = cfg.get("RepoRoot") or cfg.get("SymbolLib")
        if not root_dir and project:
            root_dir = str(Path(project).parent)
        if not root_dir:
            _log("No repo root to clear KiCad caches under."); return
        if not confirm(host, "Clear KiCad Cache",
                       "Delete KiCad cache files (.kicad_prl, lock files, fp-info-cache, "
                       "rescue/legacy caches) under the repository? KiCad regenerates them."):
            return

        def work():
            rp = Path(root_dir)
            counts = psm.clear_project_cache_files(rp, verbose=False) or {}
            try:
                ncm.clear_project_cache(rp)
            except Exception:  # noqa: BLE001
                pass
            n = sum(v for v in counts.values() if isinstance(v, int))
            return f"Cleared {plural(n, 'KiCad cache file')} under {rp.name}."

        def done(line, ok):
            _log(line if ok else "Clearing the KiCad cache failed.")

        run_populate(ctx, work, done, busy="Clearing KiCad cache...")

    # ── assemble the editor ───────────────────────────────────────────────────────────────
    secondary = [
        kit.action("Validate", validate, tip="Check the net classes against the profile's fab minimums"),
        kit.action("Pull From KiCad", lambda: host._pull_from_kicad(),
                   tip="Read the net classes from this project's KiCad file into the editor"),
        kit.action("New Profile", lambda: host._new_profile(),
                   tip="Save the current fab + net classes as a new named profile"),
        kit.action("Save Profile", lambda: host._save_profile(),
                   tip="Update the selected profile with the current net classes"),
        kit.action("Delete Profile", lambda: host._delete_profile(),
                   tip="Delete the selected user profile (a built-in reverts to its default)"),
        kit.action("Load Vault-Standard Template", lambda: host._load_vault_template(),
                   tip="Fill the editor with the vault-standard net classes for the current fab profile"),
        kit.action("Load Saved Vault Standard", lambda: host._load_vault_saved(),
                   tip="Fill the editor with the saved vault-standard net classes"),
        kit.action("Save As Vault Standard", lambda: host._save_vault(),
                   tip="Persist the current net classes as the canonical vault standard"),
    ]
    machinery = [
        kit.action("Clear KiCad Cache", _clear_cache,
                   tip="Delete KiCad cache/lock files under the repo so settings reload cleanly"),
    ]

    host = kit.editor(ctx, title="Editor", snapshot=_capture, build_body=build_body,
                      primary=save_flow, after=_after_save,
                      secondary=secondary, machinery=machinery, busy=busy)

    # Disable every action while a mutating op runs (▶ Save / Validate / Conform / Clear Cache).
    # kit.editor only guards RE-ENTRY of the ▶ flow itself, so — like the workbench panels — wire
    # the shared busy gate to button enablement so an in-flight write can't overlap another op on
    # the same project files. The Conform-Apply button owns its own enabled state (preview count),
    # so it's excluded here and protected purely by run_conform's busy guard.
    _skip = {getattr(host, "_conform_apply_btn", None)}
    _buttons = [b for b in host.findChildren(QPushButton)
                if not b.text().startswith(("▸", "▾")) and b not in _skip]

    def _apply_enablement():
        on = not busy["on"]
        for b in _buttons:
            try:
                b.setEnabled(on)
            except RuntimeError:                           # a button deleted by a rebuild
                pass

    busy.on_change = _apply_enablement

    # ── seams the coupled tests + cross-panel code read ───────────────────────────────────
    host._save = save
    host._validate = validate
    host._save_audit = _save_audit                         # drive seam: inspect preview ops
    host._save_violations = lambda: S.get("last_violations", [])
    host._commit_netclasses = _commit_netclasses
    host._dr_fields = dr_fields
    host._prof_state = prof_state
    host._unit = unit
    host._ncmgr = nc_state["mgr"]
    host._capture = _capture

    _push_verdict()          # initial verdict (hidden when there is no project)
    return host


def _netclass_panel(ctx, state) -> QWidget:
    """Backward-compat shim: Net Classes merged into the PCB Setup tab. An existing
    test builds this and drives ._profile_seg, so it still resolves to the merged
    panel (which exposes the same handle)."""
    return _pcb_setup_panel(ctx, state)

class ProjectsFeature(F.Feature):
    id = "projects"
    title = "Projects"
    order = 20
    category = "Design"

    def build(self, ctx: F.Context) -> QWidget:
        state = ProjectsState(ctx.cfg)
        header = None
        if state.names():
            # Each item shows the project name; its full path rides along as the
            # item tooltip so identically-named projects stay distinguishable, and
            # the combo's own tooltip tracks the current project's path.
            combo = QComboBox(); combo.setFixedWidth(260)
            for i, (p, lab) in enumerate(zip(state.projects, state.labels())):
                combo.addItem(lab)                         # name, disambiguated by path
                combo.setItemData(i, p.as_posix(), Qt.ToolTipRole)   # posix: Windows-safe display
            if state.project and state.project in state.projects:
                combo.setCurrentIndex(state.projects.index(state.project))   # preferred default
            combo.setToolTip(state.project.as_posix() if state.project else "Choose a discovered KiCad project")

            def _on_pick(i):
                state.select_index(i)                      # index, not name — dup-safe
                combo.setToolTip(state.project.as_posix() if state.project else "")
            combo.currentIndexChanged.connect(_on_pick)
            header = W.hstack(W.eyebrow("Project"), combo, spacing=8)
        panels = [
            ("Overview", lambda c: W.scroll_body(_overview_panel(c, state))),
            ("Health", lambda c: W.scroll_body(_health_panel(c, state))),
            ("Bill of Materials", lambda c: _bom_panel(c, state)),
            ("Editor", lambda c: W.scroll_body(_pcb_setup_panel(c, state))),
            ("Refactor", lambda c: W.scroll_body(_rename_panel(c, state))),
        ]
        ws = kit.tabbed_page("Projects", panels, header=header, ctx=ctx)
        # Switching the project rebuilds EVERY sub-panel wholesale (mirrors Bench),
        # so PCB Setup / Refactor can't be left showing — or SAVING INTO — the
        # previously-selected project. This is the single refresh mechanism; panels
        # must NOT also self-register on_change (that leaks stale closures per rebuild).
        state.on_change(ws.rebuild_all)
        self.state = state          # drive/test seam: lets drive_audit switch projects
        return ws


F.register(ProjectsFeature())
