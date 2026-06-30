"""
Schematic-repair tests — fix Footprint fields on already-placed symbols, but
ONLY for parts in the shared library; standard-library parts stay untouched.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.kicad import schematic

# Two placed symbols: one of OUR parts (per-part nickname) and one stdlib part.
SCH = (
    '(kicad_sch (version 20231120)\n'
    '  (symbol (lib_id "ACME:ACME9")\n'
    '    (property "Footprint" "ACME9:SOT23-6" (id 2) (at 0 0 0)))\n'
    '  (symbol (lib_id "Device:R")\n'
    '    (property "Footprint" "Resistor_SMD:R_0402_1005Metric" (id 2) (at 0 0 0)))\n'
    ')\n'
)
KNOWN = {"SOT23-6"}  # only our footprint is in MyFootprints.pretty


class SchematicRepairTests(unittest.TestCase):
    def test_repairs_only_our_parts(self):
        out, changes = schematic.repair_schematic_footprints(SCH, KNOWN)
        self.assertIn('(property "Footprint" "MyFootprints:SOT23-6"', out)
        # standard-library footprint untouched
        self.assertIn('"Resistor_SMD:R_0402_1005Metric"', out)
        self.assertEqual(changes, [("ACME9:SOT23-6", "MyFootprints:SOT23-6")])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.kicad_sch"
            p.write_text(SCH, encoding="utf-8")
            changes = schematic.repair_schematic_file(p, KNOWN, dry_run=True)
            self.assertEqual(len(changes), 1)
            self.assertEqual(p.read_text(encoding="utf-8"), SCH)  # unchanged
            self.assertFalse(p.with_suffix(".kicad_sch.bak").exists())

    def test_apply_writes_with_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.kicad_sch"
            p.write_text(SCH, encoding="utf-8")
            changes = schematic.repair_schematic_file(p, KNOWN, dry_run=False)
            self.assertEqual(len(changes), 1)
            self.assertIn("MyFootprints:SOT23-6", p.read_text(encoding="utf-8"))
            bak = p.with_suffix(".kicad_sch.bak")
            self.assertTrue(bak.exists())
            self.assertEqual(bak.read_text(encoding="utf-8"), SCH)  # original preserved

    def test_idempotent(self):
        out1, _ = schematic.repair_schematic_footprints(SCH, KNOWN)
        out2, changes2 = schematic.repair_schematic_footprints(out1, KNOWN)
        self.assertEqual(out1, out2)
        self.assertEqual(changes2, [])


if __name__ == "__main__":
    unittest.main()
