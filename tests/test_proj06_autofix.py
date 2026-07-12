"""PROJ-06: safe one-click fix. `autofixable` picks the deterministically-fixable
findings; `_annotate_text` assigns the next free designator number per prefix,
scoped to each symbol block so a symbol's property + instance reference get the
SAME number; `annotate_project` applies it across sheets with a .bak backup.

Task 7 (fill-from-library): the Health panel's Fix-All now collects BOTH the
annotate fixer and the new nd_library_fill plan into ONE preview dialog
(`FillPreviewDialog`); applying it writes the Library's fields into the
`.kicad_sch` and re-audits so findings drop. The panel tests build the real
Health panel headless and drive the Fix-All handler end to end."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_project_health as H  # noqa: E402


SCH = (
    '(kicad_sch\n'
    ' (symbol (lib_id "Device:R") (property "Reference" "R1")'
    ' (instances (project "x" (path "/" (reference "R1") (unit 1)))))\n'
    ' (symbol (lib_id "Device:R") (property "Reference" "R?")'
    ' (instances (project "x" (path "/" (reference "R?") (unit 1)))))\n'
    ' (symbol (lib_id "Device:C") (property "Reference" "C?")'
    ' (instances (project "x" (path "/" (reference "C?") (unit 1)))))\n'
    ')'
)


def test_autofixable_selects_only_unannotated():
    findings = [
        {"ref": "R?", "kind": "unannotated"},
        {"ref": "C3", "kind": "no_footprint"},
        {"ref": "U1", "kind": "pin_pad_mismatch"},
    ]
    fx = H.autofixable(findings)
    assert [f["ref"] for f in fx] == ["R?"]


def test_fixer_registry_is_the_source_of_truth_for_autofixable():
    # autofixable() must derive its fixable kinds from the registered fixer map,
    # not from a duplicated literal. The built-in registration covers 'unannotated'
    # with the annotate_project callable.
    assert H.autofixable_kinds() == set(H._FIXERS)
    assert "unannotated" in H.autofixable_kinds()
    assert H._FIXERS["unannotated"] is H.annotate_project


def test_registering_a_new_fixer_makes_that_kind_autofixable():
    # Registering a fixer for a new kind must immediately make findings of that kind
    # fall out of autofixable() — proving there is no second literal to update.
    sentinel = object()
    added = "test_kind_xyz"
    assert added not in H.autofixable_kinds()
    try:
        H.register_fixer(added, sentinel)
        assert added in H.autofixable_kinds()
        picked = H.autofixable([{"ref": "X1", "kind": added},
                                {"ref": "Y2", "kind": "no_footprint"}])
        assert [f["ref"] for f in picked] == ["X1"]
    finally:
        H._FIXERS.pop(added, None)
    assert added not in H.autofixable_kinds()   # cleanup restored the registry


def test_annotate_text_assigns_next_free_number_per_prefix():
    out, n = H._annotate_text(SCH, used={"R1"})
    assert n == 2                        # R? and C? both fixed
    assert '"R?"' not in out and '"C?"' not in out
    assert out.count('"R2"') == 2        # R? -> R2 in BOTH property and instance
    assert out.count('"C1"') == 2        # C? -> C1, R1 preserved
    assert '"R1"' in out


def test_annotate_text_noop_when_nothing_unannotated():
    clean = '(kicad_sch (symbol (property "Reference" "R1")))'
    out, n = H._annotate_text(clean, used=set())
    assert n == 0 and out == clean


def test_annotate_project_writes_and_backs_up(tmp_path):
    f = tmp_path / "sheet.kicad_sch"
    f.write_text(SCH)
    n = H.annotate_project([str(f)], apply=True)
    assert n == 2
    assert '"R?"' not in f.read_text()
    assert (tmp_path / "sheet.kicad_sch.bak").exists()      # backup made


def test_annotate_does_not_duplicate_property_only_refs(tmp_path):
    # CRITICAL: a symbol whose reference exists ONLY as (property "Reference" "R1")
    # with no (instances) block (legacy / hand-edited). A new R? must become R2 —
    # never re-use R1 and create a duplicate designator on the real board.
    sch = ('(kicad_sch\n'
           ' (symbol (lib_id "Device:R") (property "Reference" "R1"))\n'
           ' (symbol (lib_id "Device:R") (property "Reference" "R?"))\n)')
    f = tmp_path / "s.kicad_sch"; f.write_text(sch)
    H.annotate_project([str(f)], apply=True)
    txt = f.read_text()
    assert txt.count('"R1"') == 1        # original untouched, NOT duplicated
    assert '"R2"' in txt                 # new one numbered past the existing R1
    assert '"R?"' not in txt


def test_annotate_handles_paren_inside_a_field_value(tmp_path):
    # CRITICAL: a stray paren inside a quoted field must not desync the symbol-block
    # scan and silently skip annotating that symbol.
    sch = ('(kicad_sch\n'
           ' (symbol (lib_id "Device:R") (property "Value" "f(x") '
           '(property "Reference" "C?"))\n)')
    f = tmp_path / "s.kicad_sch"; f.write_text(sch)
    n = H.annotate_project([str(f)], apply=True)
    assert n == 1                        # annotated, not skipped
    txt = f.read_text()
    assert '"C1"' in txt and '"C?"' not in txt


def test_annotate_project_dry_run_does_not_write(tmp_path):
    f = tmp_path / "sheet.kicad_sch"
    f.write_text(SCH)
    n = H.annotate_project([str(f)], apply=False)
    assert n == 2                        # reports how many WOULD change
    assert '"R?"' in f.read_text()       # but file untouched


# ── Task 7: Fix-All fills from the Library via one preview dialog ─────────────
# These build the REAL Health panel headless and drive the reworked Fix-All so
# the wiring (annotate + nd_library_fill -> FillPreviewDialog -> apply -> re-audit)
# is exercised, not just the pure module. Imports are local so the pure-logic
# tests above stay Qt-free.

from PyQt5.QtWidgets import QApplication  # noqa: E402
from ui.features import projects as P  # noqa: E402

_APP = QApplication.instance() or QApplication([])


# A Library symbol file: the resistor the fixture R1 references by symbol name,
# carrying a Footprint the schematic instance is missing.
_SYMBOL_LIB = '''(kicad_symbol_lib (version 20211014) (generator "test")
  (symbol "R_10k"
    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 0 2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "MyFootprints:R_0402_1005Metric" (at 0 -2 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "R_10k_0_1" (rectangle (start -1 -2.5) (end 1 2.5)))
  )
  (symbol "STM32F103C8T6"
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8T6" (at 0 2 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "MyFootprints:LQFP-48_7x7mm_P0.5mm" (at 0 -2 0) (effects (font (size 1.27 1.27)) hide))
    (property "MPN" "STM32F103C8T6" (at 0 -4 0) (effects (font (size 1.27 1.27)) hide))
    (property "Manufacturer" "STMicroelectronics" (at 0 -6 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "https://st.com/stm32f103.pdf" (at 0 -8 0) (effects (font (size 1.27 1.27)) hide))
    (property "Description" "ARM Cortex-M3 MCU 64KB flash" (at 0 -10 0) (effects (font (size 1.27 1.27)) hide))
    (symbol "STM32F103C8T6_0_1" (rectangle (start -5 -5) (end 5 5)))
  )
)
'''

# A project schematic: R1 (matches R_10k by symbol name) lacks a Footprint; U1
# (matches STM32F103C8T6 by symbol name AND its own MPN) lacks MPN/Manufacturer/
# Footprint. Both are exact matches, so Fix-All has real fills to propose.
_SCHEMATIC = '''(kicad_sch (version 20230121) (generator "eeschema")
  (lib_symbols
    (symbol "MySymbols:R_10k" (pin_numbers hide)
      (property "Reference" "R" (at 2 0 90) (effects (font (size 1.27 1.27))))
      (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
      (symbol "R_10k_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54)))
    )
    (symbol "MySymbols:STM32F103C8T6" (pin_names (offset 1.016))
      (property "Reference" "U" (at -12.7 22.86 0) (effects (font (size 1.27 1.27))))
      (property "Value" "STM32F103C8T6" (at 10.16 22.86 0) (effects (font (size 1.27 1.27))))
      (symbol "STM32_0_1" (rectangle (start -12.7 20.32) (end 12.7 -22.86)))
    )
  )
  (symbol (lib_id "MySymbols:R_10k") (at 100 100 0) (unit 1)
    (property "Reference" "R1" (at 102 98 0) (effects (font (size 1.27 1.27))))
    (property "Value" "10k" (at 102 102 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 100 100 0) (effects (font (size 1.27 1.27)) hide))
    (property "Datasheet" "~" (at 100 100 0) (effects (font (size 1.27 1.27)) hide))
    (instances (project "proj" (path "/" (reference "R1") (unit 1))))
  )
  (symbol (lib_id "MySymbols:STM32F103C8T6") (at 150 100 0) (unit 1)
    (property "Reference" "U1" (at 150 75 0) (effects (font (size 1.27 1.27))))
    (property "Value" "STM32F103C8T6" (at 150 125 0) (effects (font (size 1.27 1.27))))
    (property "Footprint" "" (at 150 100 0) (effects (font (size 1.27 1.27)) hide))
    (instances (project "proj" (path "/" (reference "U1") (unit 1))))
  )
)
'''


def _fixture_project(tmp_path):
    """A discoverable KiCad project (.kicad_pro + one .kicad_sch) plus a Library
    symbol file. Returns (repo_root, sch_path, sym_path)."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "board.kicad_pro").write_text('{"meta": {"version": 1}}', encoding="utf-8")
    sch = proj / "board.kicad_sch"
    sch.write_text(_SCHEMATIC, encoding="utf-8")
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(_SYMBOL_LIB, encoding="utf-8")
    return tmp_path, sch, sym


def _ctx(cfg):
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg=cfg, services=_Svc(), bus=None)


def _build_health_panel(tmp_path):
    root, sch, sym = _fixture_project(tmp_path)
    cfg = {"RepoRoot": str(root), "SymbolLib": str(sym)}
    ctx = _ctx(cfg)
    state = P.ProjectsState(cfg)
    assert state.project is not None, "fixture project was not discovered"
    panel = P._health_panel(ctx, state)
    return panel, ctx, state, sch


def test_prepare_audit_lists_expected_items(tmp_path):
    # The ▶ Prepare flow (kit.workbench) rebuilt onto the recipe: its off-thread audit
    # lists one op per proposed field; R1 + U1's exact-match fills are surfaced.
    panel, ctx, state, sch = _build_health_panel(tmp_path)
    ops = panel._prepare_audit(panel._snapshot())
    assert ops, "Prepare should have work (there ARE fillable components)"
    # Op keys are 'ref\x1fprop'; the annotate op (if any) is the sentinel '\x00annotate'.
    refs = {k.split("\x1f", 1)[0] for k in (op["key"] for op in ops) if "\x1f" in k}
    assert {"R1", "U1"} <= refs                          # both exact matches surfaced

    # _prepare_audit stashed the real fill plan; the rich preview dialog the ▶ flow's
    # `preview` hook drives still builds + renders clean headless (no paint crash).
    plan = panel._prep["plan"]
    assert {"R1", "U1"} <= {item["ref"] for item in plan["items"]}
    dlg = P.FillPreviewDialog(plan, panel._prep.get("annotate_n", 0))
    dlg.grab()


def test_prepare_apply_mutates_fixture_and_reaudits(tmp_path):
    panel, ctx, state, sch = _build_health_panel(tmp_path)

    before = H.audit_project([str(sch)])
    n_fp_before = sum(1 for f in before["findings"] if f["kind"] == "no_footprint")
    n_mpn_before = sum(1 for f in before["findings"] if f["kind"] == "no_mpn")
    assert n_fp_before >= 1 and n_mpn_before >= 1

    snap = panel._snapshot()
    ops = panel._prepare_audit(snap)
    keys = [op["key"] for op in ops]                      # take EVERY proposed change
    report = panel._prepare_apply(snap, keys)             # writes + re-audits
    assert "→" in report["summary"]                       # before→after report

    # The schematic changed on disk: R1 + U1 footprints filled from the Library.
    comps = {c["ref"]: c for c in H.schematic_components(str(sch))}
    assert comps["R1"]["props"]["Footprint"] == "MyFootprints:R_0402_1005Metric"
    assert comps["U1"]["props"]["Footprint"] == "MyFootprints:LQFP-48_7x7mm_P0.5mm"
    assert comps["U1"]["props"]["Manufacturer"] == "STMicroelectronics"
    assert (sch.with_suffix(".kicad_sch.bak")).exists()   # safety backup written

    # Re-audit shows fewer missing-footprint / missing-MPN findings.
    after = H.audit_project([str(sch)])
    n_fp_after = sum(1 for f in after["findings"] if f["kind"] == "no_footprint")
    n_mpn_after = sum(1 for f in after["findings"] if f["kind"] == "no_mpn")
    assert n_fp_after < n_fp_before
    assert n_mpn_after < n_mpn_before


def test_prepare_run_headless_applies_safe_keys(tmp_path):
    # Driving the whole ▶ flow headlessly (no dialog) applies the safe/pre-checked keys
    # (exact fills + annotate) and re-audits — the recipe's headless contract.
    panel, ctx, state, sch = _build_health_panel(tmp_path)
    assert panel._fill_dialog is None
    panel._run_primary()                                 # headless: no exec_, applies safe keys
    assert panel._fill_dialog is None                    # no dialog was built headless
    comps = {c["ref"]: c for c in H.schematic_components(str(sch))}
    # R1 + U1 are exact matches → their fills are safe → applied headlessly.
    assert comps["R1"]["props"]["Footprint"] == "MyFootprints:R_0402_1005Metric"


def test_prepare_nothing_to_do_says_so(tmp_path):
    # A project with no Library match and nothing unannotated: Prepare has no ops and the
    # flow reports its `empty` line — no dialog is built.
    proj = tmp_path / "proj"; proj.mkdir()
    (proj / "board.kicad_pro").write_text("{}", encoding="utf-8")
    sch = proj / "board.kicad_sch"
    sch.write_text(
        '(kicad_sch (version 20230121) (generator "eeschema")\n'
        '  (symbol (lib_id "Device:R") (at 0 0 0) (unit 1)\n'
        '    (property "Reference" "R1" (at 0 0 0))\n'
        '    (property "Value" "10k" (at 0 0 0))\n'
        '    (property "Footprint" "MyFootprints:R_0402" (at 0 0 0))\n'
        '    (property "MPN" "RC0402" (at 0 0 0))\n'
        '    (instances (project "proj" (path "/" (reference "R1") (unit 1)))))\n)',
        encoding="utf-8")
    cfg = {"RepoRoot": str(tmp_path), "SymbolLib": str(tmp_path / "none.kicad_sym")}
    ctx = _ctx(cfg)
    state = P.ProjectsState(cfg)
    panel = P._health_panel(ctx, state)
    assert panel._prepare_audit(panel._snapshot()) == []  # nothing to prepare
    panel._run_primary()                                  # reports the flow's empty line
    assert panel._fill_dialog is None
    assert any("Nothing to prepare" in m for m in ctx.services.logs)
