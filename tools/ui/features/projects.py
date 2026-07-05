"""Projects — the KiCad project maintenance workspace.

Panels: Health, BOM, Rename, Net Classes, Board Setup, Fab Standard, Git. Git
reads the real repository via nd_git; the other panels present the real actions
on the pure nd_* helpers and fill in their live wiring as each is built out.
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel

from .. import theme as T
from .. import widgets as W
from .. import feature as F

import nd_git


def _kv_card(pairs) -> W.Card:
    card = W.Card(pad=16)
    for k, v in pairs:
        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(W.body(k))
        row.addStretch(1)
        row.addWidget(v if isinstance(v, QWidget) else W.body(str(v), dim=True, mono=True))
        card.body.addLayout(row)
    return card


def _health_panel(ctx, _s) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    for txt, kind in (("148 Components", "mut"), ("131 Healthy", "mut"),
                      ("4 Errors", "err"), ("13 Warnings", "warn")):
        bar.addWidget(W.tag(txt, kind))
    bar.addStretch(1)
    bar.addWidget(W.btn("Run ERC", "default", "Run electrical rule check via kicad-cli"))
    bar.addWidget(W.btn("Run DRC", "default", "Run design rule check via kicad-cli"))
    bar.addWidget(W.btn("Audit", "primary", "Audit the schematic for missing footprints, MPNs, and mismatches"))
    lay.addLayout(bar)
    rows = [
        [W.body("R14", mono=True), W.body("No Footprint"), W.body("No Footprint Assigned"), W.tag("Error", "err")],
        [W.body("U3", mono=True), W.body("Pin / Pad Mismatch"), W.body("Symbol Has 64 Pins, Footprint Has 48 Pads"), W.tag("Error", "err")],
        [W.body("C22", mono=True), W.body("Missing Part Number"), W.body("No Manufacturer Or MPN, Cannot Be Sourced"), W.tag("Warning", "warn")],
        [W.body("J2?", mono=True), W.body("Unannotated"), W.body("Reference Designator Not Annotated"), W.tag("Warning", "warn")],
        [W.body("Q1", mono=True), W.body("No 3D Model"), W.body("No 3D Model Linked To Footprint"), W.tag("Info", "mut")],
    ]
    lay.addWidget(W.data_table(["Ref", "Kind", "Detail", "Severity"], rows, stretch_col=2), 1)
    return root


def _bom_panel(ctx, _s) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    for txt, kind in (("2 Boards", "mut"), ("96 Line Items", "mut"), ("6 Not On Mouser", "warn")):
        bar.addWidget(W.tag(txt, kind))
    bar.addStretch(1)
    bar.addWidget(W.btn("From Schematic", "ghost", "Build a BOM from any .kicad_sch"))
    bar.addWidget(W.btn("Export CSV", "default", "Export the consolidated BOM"))
    bar.addWidget(W.btn("Consolidated BOM", "primary", "Merge the BOM across every board"))
    lay.addLayout(bar)
    rows = [
        [W.body("STM32H753ZIT6", mono=True), W.body("STMicroelectronics"), W.body("Controller", dim=True), "1", "0", "1", W.tag("Mouser", "ok")],
        [W.body("ADG714BRUZ-REEL", mono=True), W.body("Analog Devices"), W.body("Switch", dim=True), "0", "9", "9", W.tag("Mouser", "ok")],
        [W.body("GRM188R61A226", mono=True), W.body("Murata"), W.body("22 uF", dim=True), "24", "40", "64", W.tag("Not Found", "warn")],
    ]
    lay.addWidget(W.data_table(["Part Number", "Manufacturer", "Value", "Parent", "Cards", "Total", "Source"], rows, stretch_col=0), 1)
    return root


def _placeholder(title, actions, note="") -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8); bar.addStretch(1)
    for label, kind, tip in actions:
        bar.addWidget(W.btn(label, kind, tip))
    lay.addLayout(bar)
    card = W.Card(pad=16)
    card.body.addWidget(W.eyebrow(title))
    if note:
        card.body.addWidget(W.body(note, dim=True))
    lay.addWidget(card)
    lay.addStretch(1)
    return root


def _rename_panel(ctx, _s):
    return _placeholder("Rename Transform",
                        [("Preview", "ghost", "Preview the rename without writing"),
                         ("Apply Atomically", "primary", "Apply all changes or none")],
                        "Find and replace refdes and net tags across schematic and board, "
                        "all or nothing, with a .bak per file.")


def _netclass_panel(ctx, _s):
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    bar = QHBoxLayout(); bar.setSpacing(8)
    bar.addWidget(W.eyebrow("Profile"))
    bar.addWidget(W.Segmented(["OSH Park 4-Layer", "OSH Park 2-Layer"], tip="Net-class profile"))
    bar.addStretch(1)
    bar.addWidget(W.btn("Validate", "ghost", "Check every class against the fab minimums"))
    bar.addWidget(W.btn("Sync To Projects", "primary", "Write the profile to the discovered projects"))
    lay.addLayout(bar)
    rows = [
        [W.token("Default"), "0.15", "0.20", "0.60", "0.30", W.body("None", dim=True)],
        [W.token("Power"), "0.20", "0.40", "0.80", "0.40", W.body("None", dim=True)],
        [W.token("USB"), "0.15", "0.20", "0.60", "0.30", W.body("0.20 / 0.15")],
    ]
    lay.addWidget(W.data_table(["Net Class", "Clearance", "Track", "Via", "Drill", "Diff Pair"], rows, stretch_col=0))
    lay.addWidget(W.eyebrow("Values In Millimetres"))
    lay.addStretch(1)
    return root


def _boardsetup_panel(ctx, _s):
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    lay.addWidget(W.eyebrow("Board Setup"))
    lay.addWidget(W.dl([
        ("Pad To Mask Clearance", W.body("0.051 mm", mono=True)),
        ("Solder Mask Min Width", W.body("0.250 mm", mono=True)),
        ("Pad To Paste Clearance", W.body("0.000 mm", mono=True)),
        ("Grid Origin", W.body("100.0, 80.0", mono=True)),
        ("Mask Bridges In Footprints", W.body("Allowed")),
    ], key_width=210))
    lay.addStretch(1)
    return root


def _fab_panel(ctx, _s):
    return _placeholder("OSH Park Fab Standard",
                        [("Preview Conform", "ghost", "Preview text and stackup conform"),
                         ("Apply", "primary", "Apply the preset, stackup and net classes")],
                        "Apply the OSH Park 2 or 4 layer preset: design rules, stackup, and a "
                        "retroactive text conform pass, with an atomic .bak per file.")


def _git_panel(ctx, _s) -> QWidget:
    root = QWidget(); lay = QVBoxLayout(root); lay.setContentsMargins(24, 16, 24, 24); lay.setSpacing(14)
    lay.addWidget(W.eyebrow("Repository"))
    repo = None
    try:
        repo = nd_git.repo_root((ctx.cfg or {}).get("RepoRoot", "."))
    except Exception:  # noqa: BLE001
        repo = None
    if repo:
        branch = nd_git.current_branch(repo) or "detached"
        st = nd_git.status(repo)
        clean = st.get("clean", True)
        status_w = W.tag("Clean", "ok") if clean else W.tag(
            f"{len(st.get('modified', []))} Modified", "warn")
        lay.addWidget(_kv_card([("Branch", W.token(branch)), ("Status", status_w)]))
        changes = W.Card(pad=16)
        changes.body.addWidget(W.eyebrow("Changes"))
        listed = [("Staged", st.get("staged", []), "ok"), ("Modified", st.get("modified", []), "warn"),
                  ("Untracked", st.get("untracked", []), "mut")]
        any_change = False
        for label, files, kind in listed:
            for f in files[:12]:
                any_change = True
                row = QHBoxLayout(); row.setSpacing(8)
                row.addWidget(W.tag(label, kind)); row.addWidget(W.token(str(f), dim=True)); row.addStretch(1)
                changes.body.addLayout(row)
        if not any_change:
            changes.body.addWidget(W.body("Working tree clean.", dim=True))
        lay.addWidget(changes)
    else:
        lay.addWidget(W.body("Not a git repository.", dim=True))
    bar = QHBoxLayout(); bar.addStretch(1)
    bar.addWidget(W.btn("Commit And Push", "primary", "Commit and push the changes"))
    lay.addLayout(bar)
    lay.addStretch(1)
    return root


class ProjectsFeature(F.Feature):
    id = "projects"
    title = "Projects"
    order = 30

    def build(self, ctx: F.Context) -> QWidget:
        panels = [
            ("Health", lambda c: _health_panel(c, None)),
            ("BOM", lambda c: _bom_panel(c, None)),
            ("Rename", lambda c: W.scroll_body(_rename_panel(c, None))),
            ("Net Classes", lambda c: W.scroll_body(_netclass_panel(c, None))),
            ("Board Setup", lambda c: W.scroll_body(_boardsetup_panel(c, None))),
            ("Fab Standard", lambda c: W.scroll_body(_fab_panel(c, None))),
            ("Git", lambda c: W.scroll_body(_git_panel(c, None))),
        ]
        header = W.Segmented(["NETDECK / Master", "Plug-In Cards"], tip="Choose a discovered project")
        return W.Workspace(ctx, "Projects", panels, header=header)


F.register(ProjectsFeature())
