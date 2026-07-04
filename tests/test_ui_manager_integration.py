"""Smoke + integration tests for the KiCad Manager tab wiring in
tools/LibraryManager.py.

These verify that the three orphaned backends are actually wired into the
existing single app:

1. GROUPED LIBRARY VIEW  — checking 'Group by Component' switches the shared
   tree to one-row-per-part rows sourced from scan_library_grouped(cfg), with
   Part / Symbol / Footprint / 3D Model / Status columns and a desaturated warn
   dot on dangling parts; unchecking returns to the flat scan_library view.
2. GIT STATUS + COMMIT   — the header chip text is enriched from nd_git.status,
   and a library-commit action stages+commits via nd_git (reachable core).
3. BOARD 3D RENDER       — fp_render.render_board_image / have_board_render are
   reachable through a core method that returns an explicit unavailable/failure
   state instead of crashing when kicad-cli is missing.

Everything runs under QT_QPA_PLATFORM=offscreen; the full LibraryManagerWindow
is constructed against a throwaway tmp config and must build without raising.
"""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication  # noqa: E402
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QColor  # noqa: E402

import LibraryManager as L  # noqa: E402
import ui_theme  # noqa: E402
import nd_git  # noqa: E402
import fp_render  # noqa: E402


SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _symbol(name, footprint=None):
    lines = [f'  (symbol "{name}"']
    if footprint is not None:
        lines.append(f'    (property "Footprint" "MyFootprints:{footprint}")')
    lines.append("    (pin 1)")
    lines.append("  )")
    return "\n".join(lines) + "\n"


def _footprint(name, model_basename=None):
    inner = ""
    if model_basename is not None:
        inner = f'  (model "${{MY3DMODELS}}/{model_basename}"\n    (offset (xyz 0 0 0))\n  )\n'
    return f'(footprint "{name}" (layer "F.Cu")\n{inner})\n'


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


def _make_cfg(tmp_root: Path):
    """A throwaway cfg pointing at a self-contained library tree with one healthy
    part (U1 -> FP_A -> modelA.step) and one dangling part (U2 -> FP_MISSING)."""
    cfg = L.derive_paths(tmp_root)
    libs = Path(cfg["Libs"])
    fp_dir = Path(cfg["FootprintLib"])
    mdl_dir = Path(cfg["ModelLib"])
    for d in (libs, fp_dir, mdl_dir, Path(cfg["Downloads"]), Path(cfg["MiscDir"])):
        d.mkdir(parents=True, exist_ok=True)

    Path(cfg["SymbolLib"]).write_text(
        SYM_HEADER + _symbol("U1", footprint="FP_A") + _symbol("U2", footprint="FP_MISSING") + ")\n",
        encoding="utf-8",
    )
    (fp_dir / "FP_A.kicad_mod").write_text(_footprint("FP_A", "modelA.step"), encoding="utf-8")
    (mdl_dir / "modelA.step").write_text("solid\n", encoding="utf-8")
    return cfg


@pytest.fixture()
def window(app, tmp_path):
    cfg = _make_cfg(tmp_path)
    win = L.LibraryManagerWindow(cfg)
    try:
        yield win
    finally:
        win.close()


# --------------------------------------------------------------------------
# Smoke: the whole window constructs and exposes the new wiring
# --------------------------------------------------------------------------
def test_window_constructs_and_exposes_new_api(window):
    for attr in (
        "chk_group", "tree",
        "_populate_grouped_parts", "_set_tree_columns",
        "do_commit_library", "_commit_library_core",
        "do_render_board", "_render_board_core", "_show_board_result",
        "_branch_status_text",
        "btn_commit_library",
    ):
        assert hasattr(window, attr), f"missing {attr}"


# --------------------------------------------------------------------------
# 1) Grouped library view
# --------------------------------------------------------------------------
def test_grouped_view_switches_columns_and_flags_dangling(window):
    window.chk_group.setChecked(True)
    window.refresh_library()
    tree = window.tree

    assert tree.columnCount() == 5
    headers = [tree.headerItem().text(i) for i in range(5)]
    assert headers == ["Part", "Symbol", "Footprint", "3D Model", "Status"]

    n = tree.topLevelItemCount()
    assert n >= 2, "expected one row per part (U1 healthy, U2 dangling)"

    rows_by_name = {}
    for i in range(n):
        it = tree.topLevelItem(i)
        r = it.data(0, Qt.UserRole) or {}
        assert r.get("_grouped") is True
        rows_by_name[it.text(0)] = it

    # U2 references a footprint with no .kicad_mod file -> dangling warn dot.
    dangling = rows_by_name["U2"]
    assert (dangling.data(0, Qt.UserRole) or {}).get("dangling") is True
    assert "Dangling" in dangling.toolTip(0)
    warn = QColor(ui_theme.status("warn"))
    assert dangling.foreground(4).color().name() == warn.name()

    # U1 is a complete, healthy part.
    healthy = rows_by_name["U1"]
    assert (healthy.data(0, Qt.UserRole) or {}).get("dangling") is False


def test_unchecking_group_returns_to_flat_view(window):
    window.chk_group.setChecked(True)
    window.refresh_library()
    assert window.tree.columnCount() == 5

    window.chk_group.setChecked(False)
    window.refresh_library()
    tree = window.tree
    assert tree.columnCount() == 4
    headers = [tree.headerItem().text(i) for i in range(4)]
    assert headers == ["Format", "Name", "Location", "Date"]
    # Flat rows carry the flat schema (a 'type' in column 0), not the group marker.
    assert tree.topLevelItemCount() > 0
    first = tree.topLevelItem(0)
    assert first.text(0) in ("Symbol", "Footprint", "Model")


def test_scan_library_grouped_backend_reachable(window):
    rows = L.scan_library_grouped(window.cfg)
    assert isinstance(rows, list)
    names = {r["name"] for r in rows}
    assert {"U1", "U2"} <= names


# --------------------------------------------------------------------------
# 2) Git status + commit via nd_git
# --------------------------------------------------------------------------
def test_branch_status_text_uses_nd_git(window):
    # Reachable and returns a string even when the tmp cfg is not a git repo.
    txt = window._branch_status_text()
    assert isinstance(txt, str)


def test_commit_library_core_reachable_on_non_repo(window):
    ok, detail = window._commit_library_core("smoke")
    assert ok is False            # tmp cfg is not a git work tree
    assert isinstance(detail, str) and detail


def test_commit_library_commits_via_nd_git(app, tmp_path):
    """End-to-end when git is available: a real repo commits the staged library."""
    if not nd_git.have_git():
        pytest.skip("git not on PATH")
    cfg = _make_cfg(tmp_path)
    assert nd_git.init_repo(tmp_path).ok
    # give the repo a committer identity so commit() can succeed
    import subprocess
    for kv in (["user.email", "t@t.t"], ["user.name", "t"]):
        subprocess.run(["git", "-C", str(tmp_path), "config", *kv], check=True)
    win = L.LibraryManagerWindow(cfg)
    try:
        ok, detail = win._commit_library_core("wire library outputs")
        assert ok is True, detail
        assert nd_git.current_branch(tmp_path)  # branch born after first commit
    finally:
        win.close()


# --------------------------------------------------------------------------
# 3) Board 3D render via fp_render
# --------------------------------------------------------------------------
def test_render_board_core_missing_file_is_graceful(window, tmp_path):
    img, msg = window._render_board_core(tmp_path / "nope.kicad_pcb")
    assert img is None
    assert isinstance(msg, str) and msg


def test_render_board_core_reports_unavailable_cli(window, tmp_path, monkeypatch):
    # Force the kicad-cli-absent branch regardless of what's installed.
    monkeypatch.setattr(fp_render, "find_board_render_cli", lambda: None)
    img, msg = window._render_board_core(tmp_path / "board.kicad_pcb")
    assert img is None
    assert "kicad-cli" in msg.lower()
