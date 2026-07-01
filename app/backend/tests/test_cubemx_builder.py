"""
CubeMX DB builder — build the database from scratch and verify it reproduces the
hand-verified ground truth (LQFP64 = 11 switch pins, 424 STM32F MCUs).
Needs the CubeMX XML source; skipped otherwise.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.core import config
from hwkit.cubemx import builder, classify
from hwkit.cubemx.parse import Pin, Signal
from hwkit.pins import switch_engine as se

SRC = config.cubemx_source_dir()
_HAS_SRC = SRC.exists() and any(SRC.glob("*.xml"))


class ClassifyUnitTests(unittest.TestCase):
    def _roles(self, name, typ, sigs=()):
        return dict(classify.roles(Pin(1, name, typ, [Signal(s) for s in sigs])))

    def test_power_pins(self):
        self.assertIn("power_vbat", self._roles("VBAT", "Power"))
        self.assertIn("power_vdda", self._roles("VDDA", "Power"))
        self.assertIn("power_vref", self._roles("VREF+", "Power"))
        self.assertIn("ground", self._roles("VSS", "Power"))
        self.assertIn("vcap", self._roles("VCAP_1", "Power"))

    def test_supervisor_pins_are_io(self):
        self.assertEqual(classify.electrical_class(Pin(1, "NPOR", "Power")), "io")
        self.assertEqual(classify.electrical_class(Pin(1, "RFU", "Power")), "io")

    def test_hse_osc_not_lse(self):
        self.assertIn("oscillator_hse", self._roles("PH0 - OSC_IN", "I/O", ["RCC_OSC_IN"]))
        self.assertNotIn("oscillator_hse", self._roles("PC14-OSC32_IN", "I/O", ["RCC_OSC32_IN", "GPIO"]))

    def test_debug_roles(self):
        self.assertIn("swclk", self._roles("PA14", "I/O", ["SYS_JTCK-SWCLK", "GPIO"]))


@unittest.skipUnless(_HAS_SRC, "CubeMX XML source not present")
class GroundTruthBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / "hwkit_test_built.sqlite"
        cls.result = builder.build_database(SRC, cls.db)
        cls.conn = sqlite3.connect(cls.db)
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.db.unlink(missing_ok=True)

    def test_mcu_count(self):
        self.assertEqual(self.result.mcus, 424)
        self.assertEqual(self.result.packages["LQFP64"], 53)
        self.assertEqual(self.result.packages["LQFP100"], 60)

    def test_lqfp64_reproduces_ground_truth(self):
        rep = se.package_report(self.conn, "LQFP64")
        must = sorted(d.pin for d in rep.decisions if d.switch_class == se.SWITCH_MUST)
        self.assertEqual(must, [1, 13, 17, 18, 19, 30, 31, 33, 47, 48, 60])

    def test_lqfp100_counts(self):
        rep = se.package_report(self.conn, "LQFP100")
        must = [d.pin for d in rep.decisions if d.switch_class == se.SWITCH_MUST]
        self.assertEqual(len(must), 43)
        self.assertFalse(rep.by_pin(100).needs_switch)  # VDD always


if __name__ == "__main__":
    unittest.main()
