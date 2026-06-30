"""Apply the netclass standard to a .kicad_pro — convert + write net_settings."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.netdeck import project

CLASSES = [
    {"netclass": "GND", "color": "#5E8AC7", "clearance": 0.127,
     "track": "0.25 (plane/pour)", "via_dia": 0.6, "via_drill": 0.3, "members": ["*GND"]},
    {"netclass": "USB", "color": "#D26FA0", "clearance": 0.127, "track": "0.20",
     "via_dia": 0.4572, "via_drill": 0.254, "dp_width": 0.20, "dp_gap": 0.15, "members": ["*USB_D*"]},
    {"netclass": "Default", "color": "#8A8F98", "clearance": 0.127, "track": "0.15",
     "members": ["everything else"]},
]


class ProjectApplyTests(unittest.TestCase):
    def test_track_and_color_parse(self):
        self.assertEqual(project.track_mm("0.25 (plane/pour)"), 0.25)
        self.assertEqual(project.track_mm(">= 0.50"), 0.50)
        self.assertEqual(project.hex_to_rgba("#5E8AC7"), "rgba(94, 138, 199, 1.000)")

    def test_apply_writes_classes_and_patterns(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.kicad_pro"
            p.write_text(json.dumps({"meta": {"version": 1}}), encoding="utf-8")
            res = project.apply_netclasses(p, CLASSES)
            self.assertTrue(res.changed)
            self.assertEqual(res.classes, 3)
            data = json.loads(p.read_text(encoding="utf-8"))
            names = [c["name"] for c in data["net_settings"]["classes"]]
            self.assertEqual(names, ["GND", "USB", "Default"])
            gnd = data["net_settings"]["classes"][0]
            self.assertEqual(gnd["track_width"], 0.25)
            self.assertEqual(gnd["pcb_color"], "rgba(94, 138, 199, 1.000)")
            usb = data["net_settings"]["classes"][1]
            self.assertEqual(usb["diff_pair_width"], 0.20)
            # 'everything else' is not a pattern
            pats = data["net_settings"]["netclass_patterns"]
            self.assertEqual(sorted(p["netclass"] for p in pats), ["GND", "USB"])

    def test_idempotent_with_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.kicad_pro"
            p.write_text(json.dumps({}), encoding="utf-8")
            project.apply_netclasses(p, CLASSES)
            res2 = project.apply_netclasses(p, CLASSES)
            self.assertFalse(res2.changed)
            self.assertTrue(p.with_suffix(".kicad_pro.bak").exists())


if __name__ == "__main__":
    unittest.main()
