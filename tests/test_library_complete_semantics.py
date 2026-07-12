"""The "Complete" verdict must mean ONLY fully-complete parts — the tightened v2.11
8-item passport (symbol, footprint, 3D model, part number, manufacturer, datasheet,
description, category, no dangling) — consistently across every surface, so the Sourcing
Health count, the picker "Complete" facet, and the per-part N/8 badge can never disagree.

Regression lock for the batch-9 fix that unified three divergent "Complete" predicates
(library_health_report counted 4 signals; the picker facet counted 5; the badge counted
the strict 8) onto the single source of truth, part_completion(...)["is_complete"].
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


def _assets_only_lib(tmp_path):
    """Two parts that both have the three ASSETS (symbol + footprint + 3D model, nothing
    dangling) yet are NOT fully complete:
      P1 — no identity at all (no MPN, manufacturer, datasheet, description, category).
      P2 — has a manufacturer but still no MPN/datasheet/description/category.
    P2 is the trap: the old 5-signal picker facet (assets + manufacturer) called it
    "Complete" while its badge read 4/8. Under the old 4-signal health count BOTH read
    "complete"; under the strict passport NEITHER does."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "P1" (property "Value" "P1" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_P1" (id 2)) (pin 1))\n'
        '  (symbol "P2" (property "Value" "P2" (id 1))'
        ' (property "Footprint" "MyFootprints:FP_P2" (id 2))'
        ' (property "MANUFACTURER" "ACME" (id 3)) (pin 1))\n'
        ')\n', encoding="utf-8")
    fp = tmp_path / "fps"; fp.mkdir()
    for stem in ("FP_P1", "FP_P2"):
        (fp / f"{stem}.kicad_mod").write_text(
            f'(footprint "{stem}" (model ${{MY3DMODELS}}/{stem}.step))', encoding="utf-8")
    mdl = tmp_path / "models"; mdl.mkdir()
    for stem in ("FP_P1", "FP_P2"):
        (mdl / f"{stem}.step").write_bytes(b"ISO-10303-21;")
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl),
            "Libs": str(tmp_path), "RepoRoot": str(tmp_path)}


def _sym_row(cfg):
    rows = LM.scan_library_grouped(cfg)
    return next(r for r in rows if r.get("has_symbol")), rows


def test_health_complete_count_equals_the_strict_per_part_sum(tmp_path):
    """The invariant that ties the surfaces together: the Sourcing Health "complete"
    count is EXACTLY the number of parts whose N/8 badge reads complete."""
    cfg = _assets_only_lib(tmp_path)
    rep = LM.library_health_report(cfg)
    rows = LM.scan_library_grouped(cfg)
    strict = sum(1 for r in rows if LM.part_completion(r)["is_complete"])
    assert rep["counts"]["complete"] == strict


def test_assets_only_part_is_not_complete(tmp_path):
    """A part with the three assets but no identity is NOT complete anywhere (was 1)."""
    cfg = _assets_only_lib(tmp_path)
    rep = LM.library_health_report(cfg)
    assert rep["counts"]["complete"] == 0
    rows = LM.scan_library_grouped(cfg)
    assert all(not LM.part_completion(r)["is_complete"] for r in rows)


def test_health_report_itemizes_identity_gaps(tmp_path):
    """The report now counts the identity gaps that keep a part incomplete, so the
    verdict/chips can name them instead of silently calling the part complete."""
    cfg = _assets_only_lib(tmp_path)
    c = LM.library_health_report(cfg)["counts"]
    assert c["no_mpn"] == 2               # P1 and P2 both lack a real MPN
    assert c["no_manufacturer"] == 1      # only P1 (P2 has ACME)
    assert c["no_datasheet"] == 2
    assert c["no_description"] == 2
    assert c["no_category"] == 2


def test_picker_complete_facet_matches_the_badge(tmp_path):
    """The picker "Complete" facet predicate is the strict passport, not the old
    5-signal subset — so a part filtered "Complete" can never badge N/8 < 8."""
    from ui.features import library_preview as LP
    cfg = _assets_only_lib(tmp_path)
    rows = LM.scan_library_grouped(cfg)
    for r in rows:
        assert LP._is_complete(r) == LM.part_completion(r)["is_complete"]


def test_part_missing_reports_category(tmp_path):
    """part_missing (the Complete-This-Part / Fix-All gap list) stays in lockstep with
    the passport: a part missing only Category still has Category to fix."""
    cfg = _assets_only_lib(tmp_path)
    row, _ = _sym_row(cfg)
    labels = [m["item"] for m in LM.part_missing(row)]
    assert "Category" in labels
