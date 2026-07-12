"""Backend tests for verify_handoff_readiness(cfg) — the portability audit.

Owner requirement: "if someone else has the app and the same KiCad files then all the
components should be mapped properly." A reference is portable only if it resolves the
SAME on another machine: symbol→footprint via the `MyFootprints:` nickname to a real
file, footprint→model via `${MY3DMODELS}/` to a real file. Absolute paths, foreign
nicknames, bare stems, and dangling refs all break on a second machine — this audit
flags each with why + how_to_fix (read-only; the fix is repair_library).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as L  # noqa: E402

SYM_HEADER = '(kicad_symbol_lib (version 20211014) (generator "t")\n'


def _sym_raw(name, footprint_value):
    """A symbol whose Footprint property is set to `footprint_value` VERBATIM (so a
    test can inject a foreign nickname, a bare stem, or an absolute path)."""
    return (f'  (symbol "{name}"\n'
            f'    (property "Footprint" "{footprint_value}")\n'
            f'    (pin 1)\n  )\n')


def _fp_raw(name, model_path=None):
    """A footprint whose (model …) path is `model_path` VERBATIM, or none."""
    inner = f'  (model "{model_path}"\n    (offset (xyz 0 0 0))\n  )\n' if model_path else ""
    return f'(footprint "{name}" (layer "F.Cu")\n{inner})\n'


def _make(tmp_path, symbols_text, footprints, model_files):
    """footprints: {stem: raw_model_path_or_None}. model_files: filenames to create."""
    libs = tmp_path / "libs"
    fp_dir = libs / "MyFootprints.pretty"
    mdl_dir = libs / "My3DModels"
    fp_dir.mkdir(parents=True)
    mdl_dir.mkdir(parents=True)
    (libs / "MySymbols.kicad_sym").write_text(SYM_HEADER + symbols_text + ")\n", encoding="utf-8")
    for stem, model_path in footprints.items():
        (fp_dir / f"{stem}.kicad_mod").write_text(_fp_raw(stem, model_path), encoding="utf-8")
    for m in model_files:
        (mdl_dir / m).write_text("solid\n", encoding="utf-8")
    return {"Libs": str(libs), "SymbolLib": str(libs / "MySymbols.kicad_sym"),
            "FootprintLib": str(fp_dir), "ModelLib": str(mdl_dir)}


def _kinds(res):
    return {i["kind"] for i in res["issues"]}


def test_clean_library_is_handoff_ready(tmp_path):
    cfg = _make(tmp_path,
                _sym_raw("U1", "MyFootprints:FP_A"),
                {"FP_A": "${MY3DMODELS}/m.step"}, ["m.step"])
    res = L.verify_handoff_readiness(cfg)
    assert res["ok"] is True
    assert res["issues"] == []


def test_foreign_footprint_nickname_flagged(tmp_path):
    # Resolves on my machine only if I registered "Vendor"; breaks for everyone else.
    cfg = _make(tmp_path, _sym_raw("U1", "Vendor:FP_A"),
                {"FP_A": "${MY3DMODELS}/m.step"}, ["m.step"])
    res = L.verify_handoff_readiness(cfg)
    assert res["ok"] is False
    assert "foreign_footprint_nickname" in _kinds(res)


def test_unqualified_footprint_flagged(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "FP_A"),
                {"FP_A": "${MY3DMODELS}/m.step"}, ["m.step"])
    assert "unqualified_footprint" in _kinds(L.verify_handoff_readiness(cfg))


def test_missing_footprint_flagged(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "MyFootprints:GONE"),
                {"FP_A": "${MY3DMODELS}/m.step"}, ["m.step"])
    assert "missing_footprint" in _kinds(L.verify_handoff_readiness(cfg))


def test_absolute_model_path_flagged(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "MyFootprints:FP_A"),
                {"FP_A": "/home/sadad/models/m.step"}, ["m.step"])
    res = L.verify_handoff_readiness(cfg)
    assert res["ok"] is False
    assert "absolute_model_path" in _kinds(res)


def test_windows_absolute_model_path_flagged(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "MyFootprints:FP_A"),
                {"FP_A": "C:\\\\Users\\\\me\\\\m.step"}, ["m.step"])
    assert "absolute_model_path" in _kinds(L.verify_handoff_readiness(cfg))


def test_missing_model_flagged(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "MyFootprints:FP_A"),
                {"FP_A": "${MY3DMODELS}/gone.step"}, [])   # file not created
    assert "missing_model" in _kinds(L.verify_handoff_readiness(cfg))


def test_every_issue_has_ref_why_and_fix(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "Vendor:FP_A"),
                {"FP_A": "/abs/m.step"}, [])
    for i in L.verify_handoff_readiness(cfg)["issues"]:
        assert i["ref"] and i["kind"] and i["detail"] and i["how_to_fix"]


def test_counts_reported(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "MyFootprints:FP_A") + _sym_raw("U2", "MyFootprints:FP_A"),
                {"FP_A": "${MY3DMODELS}/m.step"}, ["m.step"])
    res = L.verify_handoff_readiness(cfg)
    assert res["counts"]["symbols"] == 2 and res["counts"]["footprints"] == 1


# ---------------------------------------------------------------------------
# make_library_portable — the fix that makes verify pass
# ---------------------------------------------------------------------------
def test_make_portable_fixes_foreign_nickname_and_absolute_model(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "Vendor:FP_A"),
                {"FP_A": "/home/sadad/models/m.step"}, ["m.step"])
    assert L.verify_handoff_readiness(cfg)["ok"] is False
    res = L.make_library_portable(cfg, L._NullLog())
    assert res["symbols_fixed"] >= 1 and res["models_fixed"] >= 1
    after = L.verify_handoff_readiness(cfg)
    assert after["ok"] is True                       # fully portable now
    # concrete rewrites
    fp_text = L.read_text(Path(cfg["FootprintLib"]) / "FP_A.kicad_mod")
    assert L.footprint_model_paths(fp_text) == ["${MY3DMODELS}/m.step"]


def test_make_portable_fixes_unqualified_footprint(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "FP_A"), {"FP_A": None}, [])
    L.make_library_portable(cfg, L._NullLog())
    assert L.verify_handoff_readiness(cfg)["ok"] is True


def test_make_portable_reports_unresolvable(tmp_path):
    # A symbol pointing at a footprint that simply doesn't exist can't be auto-fixed;
    # it must be reported, and verify still flags it afterward.
    cfg = _make(tmp_path, _sym_raw("U1", "Vendor:GONE"), {"FP_A": None}, [])
    res = L.make_library_portable(cfg, L._NullLog())
    assert any("GONE" in u for u in res["unresolved"])
    assert L.verify_handoff_readiness(cfg)["ok"] is False


def test_make_portable_is_idempotent(tmp_path):
    cfg = _make(tmp_path, _sym_raw("U1", "Vendor:FP_A"),
                {"FP_A": "/abs/m.step"}, ["m.step"])
    L.make_library_portable(cfg, L._NullLog())
    second = L.make_library_portable(cfg, L._NullLog())
    assert second["symbols_fixed"] == 0 and second["models_fixed"] == 0
