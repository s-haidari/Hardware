"""BOM-at-a-git-revision diff: reconstruct a project's BOM as it existed at an
arbitrary git ref and diff it against the current build, so an engineer can see
exactly what parts changed since a commit / tag — not just against an exported CSV.

Two layers:
  * nd_git.show / nd_git.recent_commits — the git plumbing (real temp repo, skips
    when git is absent).
  * LibraryManager.bom_rows_at_ref — reconstruct BOM rows from each sheet's content
    at the ref via an injected `show` (no git needed).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_git  # noqa: E402
import LibraryManager as L  # noqa: E402


def _sch(*syms):
    return "(kicad_sch " + " ".join(syms) + ")"


def _sym(ref, value, lib="Device:R", mpn=None):
    props = [f'(property "Reference" "{ref}")', f'(property "Value" "{value}")']
    if mpn:
        props.append(f'(property "MPN" "{mpn}")')
    return f'(symbol (lib_id "{lib}") ' + " ".join(props) + ')'


# ── LM.bom_rows_at_ref — reconstruction via injected show (no git) ──────────────
def test_bom_rows_at_ref_reconstructs_from_shown_sheets():
    # Two sheets at the ref; `show` returns each sheet's content, the union is grouped
    # into BOM lines exactly like the live builder (identity only, no pricing).
    sheets = {
        "board/root.kicad_sch": _sch(_sym("U1", "STM32", lib="Device:U", mpn="STM32F407VGT6")),
        "board/power.kicad_sch": _sch(_sym("R1", "10k"), _sym("R2", "10k")),
    }
    out = L.bom_rows_at_ref(list(sheets), lambda rel: sheets.get(rel))
    assert out["sheets_found"] == 2 and out["sheets_missing"] == 0
    by = {(r["mpn"] or r["value"]): r for r in out["rows"]}
    assert by["STM32F407VGT6"]["qty"] == 1
    assert by["10k"]["qty"] == 2                     # R1 + R2 grouped


def test_bom_rows_at_ref_counts_sheets_absent_at_that_ref():
    # A sheet that did not exist at the ref -> show returns None -> counted missing,
    # never crashes; the BOM is built from whatever sheets did exist.
    sheets = {"board/root.kicad_sch": _sch(_sym("R1", "1k"))}

    def show(rel):
        return sheets.get(rel)                       # the 'new' sheet is absent -> None

    out = L.bom_rows_at_ref(["board/root.kicad_sch", "board/added_later.kicad_sch"], show)
    assert out["sheets_found"] == 1 and out["sheets_missing"] == 1
    assert {r["value"] for r in out["rows"]} == {"1k"}


def test_bom_rows_at_ref_all_absent_is_empty_not_error():
    out = L.bom_rows_at_ref(["a.kicad_sch"], lambda rel: None)
    assert out["sheets_found"] == 0 and out["rows"] == []


def test_bom_rows_at_ref_survives_a_show_that_raises():
    def show(rel):
        raise RuntimeError("git blew up")

    out = L.bom_rows_at_ref(["a.kicad_sch"], show)   # never propagates
    assert out["sheets_found"] == 0 and out["rows"] == []


def test_bom_rows_at_ref_feeds_bom_diff_end_to_end():
    # rev A (at ref): U1 qty 1, 10k x2.  Current build (rev B): U1 qty 1, 10k gone, cap added.
    ref_sheets = {"s.kicad_sch": _sch(_sym("U1", "MCU", lib="Device:U", mpn="STM32F407VGT6"),
                                      _sym("R1", "10k"), _sym("R2", "10k"))}
    ref = L.bom_rows_at_ref(list(ref_sheets), lambda rel: ref_sheets.get(rel))
    current = [{"mpn": "STM32F407VGT6", "value": "MCU", "qty": 1},
               {"mpn": "GRM188R71", "value": "100n", "qty": 1}]
    d = L.bom_diff(ref["rows"], current)
    assert {r["mpn"] for r in d["added"]} == {"GRM188R71"}
    assert {r["value"] for r in d["removed"]} == {"10k"}


# ── nd_git plumbing (needs git) ────────────────────────────────────────────────
_needs_git = pytest.mark.skipif(not nd_git.have_git(), reason="git not on PATH")


@pytest.fixture
def _identity(monkeypatch):
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Tester")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "tester@example.com")


def _repo_with_two_revs(tmp_path):
    repo = tmp_path / "hw"
    assert nd_git.init_repo(repo).ok
    sch = repo / "board" / "board.kicad_sch"
    sch.parent.mkdir(parents=True, exist_ok=True)
    sch.write_text(_sch(_sym("R1", "10k"), _sym("R2", "10k")), encoding="utf-8")
    ok, first = nd_git.commit(repo, "first: two 10k", paths=[sch])
    assert ok, first
    sch.write_text(_sch(_sym("R1", "10k")), encoding="utf-8")   # drop R2
    ok, second = nd_git.commit(repo, "second: drop R2", paths=[sch])
    assert ok, second
    return repo, first, second


@_needs_git
def test_show_returns_file_content_at_a_ref(tmp_path, _identity):
    repo, first, _second = _repo_with_two_revs(tmp_path)
    g = nd_git.show(repo, first, "board/board.kicad_sch")
    assert g.ok and "R2" in g.out                    # R2 was present at the first commit
    g2 = nd_git.show(repo, "HEAD", "board/board.kicad_sch")
    assert g2.ok and "R2" not in g2.out              # dropped by HEAD


@_needs_git
def test_show_missing_path_is_not_ok(tmp_path, _identity):
    repo, _first, _second = _repo_with_two_revs(tmp_path)
    g = nd_git.show(repo, "HEAD", "board/does_not_exist.kicad_sch")
    assert not g.ok


@_needs_git
def test_recent_commits_lists_newest_first(tmp_path, _identity):
    repo, _first, second = _repo_with_two_revs(tmp_path)
    commits = nd_git.recent_commits(repo, n=10)
    assert len(commits) == 2
    assert commits[0]["subject"] == "second: drop R2"     # newest first
    assert second.startswith(commits[0]["ref"])           # short sha is a prefix of full


@_needs_git
def test_recent_commits_empty_on_non_repo(tmp_path):
    assert nd_git.recent_commits(tmp_path / "nope") == []


@_needs_git
def test_show_plus_bom_rows_at_ref_diffs_a_real_repo(tmp_path, _identity):
    # End to end over a real repo: reconstruct the BOM at the first commit and diff it
    # against HEAD's build — the dropped R2 shows up as a quantity change.
    repo, first, _second = _repo_with_two_revs(tmp_path)
    rel = "board/board.kicad_sch"
    old = L.bom_rows_at_ref([rel], lambda r: (lambda g: g.out if g.ok else None)(
        nd_git.show(repo, first, r)))
    now = L.bom_from_kicad_schematic(str(repo / rel))["rows"]
    d = L.bom_diff(old["rows"], now)
    assert d["changed"] and d["changed"][0]["from_qty"] == 2 and d["changed"][0]["to_qty"] == 1
