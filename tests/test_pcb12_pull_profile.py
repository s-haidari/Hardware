"""PCB-12: pull a PCB-setup profile from an existing KiCad project's net settings."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import nd_pcb_profiles as P  # noqa: E402


def _pro(tmp_path):
    pro = tmp_path / "board.kicad_pro"
    pro.write_text(json.dumps({
        "net_settings": {
            "classes": [
                {"name": "Default", "clearance": 0.2, "track_width": 0.2},
                {"name": "PWR", "clearance": 0.2, "track_width": 0.5,
                 "via_diameter": 0.6, "via_drill": 0.3},
                {"name": "USB", "clearance": 0.127, "track_width": 0.2,
                 "diff_pair_width": 0.2, "diff_pair_gap": 0.15},
            ],
            "netclass_patterns": [{"netclass": "PWR", "pattern": "*V_SYS*"}],
        }
    }))
    return pro


def test_profile_from_project_reads_netclasses(tmp_path):
    prof = P.profile_from_project(_pro(tmp_path), "From board")
    names = {nc.name for nc in prof.netclasses}
    assert "PWR" in names and "USB" in names
    assert "Default" not in names          # Default is managed separately
    assert prof.name == "From board"


def test_profile_from_project_carries_patterns(tmp_path):
    prof = P.profile_from_project(_pro(tmp_path), "From board")
    pwr = next(nc for nc in prof.netclasses if nc.name == "PWR")
    assert "*V_SYS*" in (pwr.patterns or [])


def test_profile_from_project_validates(tmp_path):
    prof = P.profile_from_project(_pro(tmp_path), "From board")
    assert P.validate_profile(prof) == []   # a pulled profile is valid


def test_profile_from_missing_file_is_empty(tmp_path):
    prof = P.profile_from_project(tmp_path / "nope.kicad_pro", "X")
    assert prof.netclasses == [] and prof.name == "X"
