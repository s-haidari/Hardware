"""Phase 1 · the styled-vs-bare capability PARITY harness (spec §9).

capability_audit.parity() reports, per feature id, the backend symbols the BARE panel uses
that the STYLED feature (its transitive module closure + bus allowlist) does NOT — the
omission list a migration drives to zero. The v2 single-module name-match gave false
all-clears (bare aliases nd_git as G/NG/GIT) and false omissions (sibling-module + bus
reach); this locks the v2.1 corrections against the real code:

  * per-scope alias resolution: bare's `import nd_git as G` is resolved, so G.* counts as
    nd_git.* (a literal 'nd_git.' match would find zero → a false all-clear);
  * the Git nd_git delta is exactly the corrected §9 set (stage_all NOT in it — styled calls
    it; have_git / guard_no_corrupt_kicad exempted as internal guards);
  * transitive closure: Library reaches extract_symbol_blocks / ensure_footprint_model via
    the library_preview SIBLING, so they are NOT false omissions;
  * the bare↔styled pairing is explicit and fails loud on an unpaired panel.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import ui.capability_audit as CA  # noqa: E402


def test_pairing_covers_every_bare_panel():
    # every *_panel builder in bare.py must be paired (fail-loud contract)
    pairs = CA._check_pairing()
    assert pairs == CA._PAIRING
    assert set(CA._PAIRING) == {"_git_panel", "_lib_panel", "_proj_panel",
                                "_bench_panel", "_settings_panel"}


def test_git_is_at_full_parity_after_the_pilot_migration():
    # Phase-1 step 7: the styled Git feature was rebuilt onto kit.workbench and now
    # surfaces EVERY bare capability — the corrected §9 delta (find_corrupt_kicad_files,
    # init_repo, recent_commits, set_repo, show, stage, unstage + LibraryManager
    # .save_repo_root) is closed. Git must be at ZERO omissions.
    rep = CA.parity()
    assert rep["git"]["omissions"] == 0, \
        f"Git must be at parity after the migration; still missing: {rep['git']['missing']}"


def test_git_stage_all_is_not_an_omission():
    # styled git.py:287 calls nd_git.stage_all — a name-keyed harness got this wrong
    rep = CA.parity()
    assert "stage_all" not in set(rep["git"]["missing"].get("nd_git", []))


def test_git_internal_guards_are_exempt():
    rep = CA.parity()
    ndg = set(rep["git"]["missing"].get("nd_git", []))
    assert "have_git" not in ndg and "guard_no_corrupt_kicad" not in ndg, \
        "internal status probe / implicit-guard symbols are not user capabilities"


def test_library_sibling_reach_is_not_a_false_omission():
    rep = CA.parity()
    lib_missing = rep["library"]["missing"]
    flat = {s for syms in lib_missing.values() for s in syms}
    # bare _lib_panel uses these directly; styled reaches them via the library_preview sibling
    assert "extract_symbol_blocks" not in flat
    assert "ensure_footprint_model" not in flat


def test_alias_resolution_finds_bare_git_usage():
    # the whole point: G.status etc. resolve to nd_git.status, so the bare set is non-empty
    used = CA._bare_panel_symbols("_git_panel")
    assert used.get("nd_git"), "alias-resolved bare set must be non-empty (not a false all-clear)"
    assert "status" in used["nd_git"] and "commit" in used["nd_git"]
