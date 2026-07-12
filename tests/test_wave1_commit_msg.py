"""Wave 1 · GIT-01 — pure conventional-commit message builders (nd_commit_msg).

All builders are pure (change -> str), so these are plain string assertions: no
git, no repo, no Qt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import nd_commit_msg as M  # noqa: E402


def test_field_set():
    assert M.field_set("Manufacturer", "R_0402") == "chore(lib): set Manufacturer on R_0402"


def test_add_footprint_with_symbol():
    assert M.add_footprint("R_0402", "R1") == "feat(lib): add footprint R_0402 to R1"


def test_add_footprint_multiple_symbols():
    assert M.add_footprint("SOT-23", ["Q1", "Q2"]) == "feat(lib): add footprint SOT-23 to Q1, Q2"


def test_add_footprint_no_symbol():
    assert M.add_footprint("R_0402") == "feat(lib): add footprint R_0402"


def test_add_model():
    assert M.add_model("SOT-23.step", "Package_TO_SOT_SMD:SOT-23") == \
        "feat(lib): add 3D model SOT-23.step to Package_TO_SOT_SMD:SOT-23"


def test_add_model_no_footprint():
    assert M.add_model("SOT-23.step") == "feat(lib): add 3D model SOT-23.step"


def test_add_symbol():
    assert M.add_symbol("MyPart.kicad_sym") == "feat(lib): add symbol MyPart.kicad_sym"


def test_import_single_part_no_changeset():
    assert M.import_parts(["R_0402"]) == "feat(lib): import 1 part (R_0402)"


def test_import_accepts_a_bare_string():
    assert M.import_parts("R_0402") == "feat(lib): import 1 part (R_0402)"


def test_import_multiple_parts():
    msg = M.import_parts(["A", "B", "C"])
    assert msg == "feat(lib): import 3 parts (A, B, C)"


def test_import_elides_long_name_lists():
    names = [f"P{i}" for i in range(9)]
    subject = M.import_parts(names)
    assert subject.startswith("feat(lib): import 9 parts (P0, P1, P2, P3, P4, P5…)")


def test_import_body_from_finalize_changeset():
    linked = {"footprint_count": 2, "model_count": 1}
    enriched = {"changes": [{"x": 1}, {"x": 2}, {"x": 3}], "looked_up": 3}
    msg = M.import_parts(["R_0402", "C_0603"], linked=linked, enriched=enriched)
    lines = msg.splitlines()
    assert lines[0] == "feat(lib): import 2 parts (R_0402, C_0603)"
    assert lines[1] == ""                                    # blank line before body
    assert "Auto-linked 2 footprints, 1 3D model" in lines
    assert "Enriched 3 symbols from Mouser" in lines


def test_import_body_omits_empty_sections():
    # Nothing linked, nothing enriched → subject only (no dangling body).
    msg = M.import_parts(["R_0402"],
                         linked={"footprint_count": 0, "model_count": 0},
                         enriched={"changes": []})
    assert msg == "feat(lib): import 1 part (R_0402)"


def test_import_singular_footprint_and_symbol():
    msg = M.import_parts(["R_0402"],
                         linked={"footprint_count": 1, "model_count": 0},
                         enriched={"changes": [{"x": 1}]})
    assert "Auto-linked 1 footprint" in msg          # singular, no trailing 's'
    assert "Enriched 1 symbol from Mouser" in msg
