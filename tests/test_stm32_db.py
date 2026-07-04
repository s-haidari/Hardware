"""Ground-truth tests for the inline STM32 CubeMX DB + switch engine (tools/stm32_db.py).

The classify unit tests always run. The full build test runs only when the CubeMX
XML is present, and reproduces the hand-verified Build Card 7B truth: LQFP64 needs
exactly the 11 switch pins [1,13,17,18,19,30,31,33,47,48,60].
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db  # noqa: E402


def _pin(name, typ, sigs=()):
    return db.Pin(position=1, name=name, type=typ,
                  signals=[db.Signal(*(s if isinstance(s, tuple) else (s, ""))) for s in sigs])


class ClassifyUnitTests(unittest.TestCase):
    def test_power_roles(self):
        self.assertEqual(db.roles(_pin("VBAT", "Power")), [("power_vbat", "power")])
        self.assertEqual(db.roles(_pin("VDDA", "Power")), [("power_vdda", "power")])
        self.assertEqual(db.roles(_pin("VREF+", "Power")), [("power_vref", "power")])
        self.assertEqual(db.roles(_pin("VDD", "Power")), [("power_vdd", "power")])
        self.assertEqual(db.roles(_pin("VSS", "Power")), [("ground", "ground")])
        self.assertEqual(db.roles(_pin("VCAP_1", "Power")), [("vcap", "local_card")])

    def test_supervisor_pins_are_io(self):
        self.assertEqual(db.electrical_class(_pin("PDR_ON", "Power")), "io")
        self.assertEqual(db.electrical_class(_pin("RFU", "Power")), "io")

    def test_hse_vs_lse(self):
        # HSE keeps its IN/OUT side (the vault services the pair on split nets:
        # SERVICE_OSC_IN contact RA-10 / SERVICE_OSC_OUT contact RA-12).
        hse_in = db.roles(_pin("PH0-OSC_IN", "I/O", [("RCC_OSC_IN",), ("GPIO",)]))
        self.assertIn(("oscillator_hse_in", "local_card"), hse_in)
        hse_out = db.roles(_pin("PH1-OSC_OUT", "I/O", [("RCC_OSC_OUT",), ("GPIO",)]))
        self.assertIn(("oscillator_hse_out", "local_card"), hse_out)
        lse = db.roles(_pin("PC14-OSC32_IN", "I/O", [("RCC_OSC32_IN",), ("GPIO",)]))
        self.assertFalse(any(rn.startswith("oscillator_hse") for rn, _ in lse))

    def test_swclk_identity(self):
        r = db.roles(_pin("PA14", "I/O", [("SYS_JTCK-SWCLK",), ("GPIO",)]))
        self.assertIn(("swclk", "service"), r)
        self.assertEqual(db.switch_identity("swclk", "service"), db.ID_IO)  # SWD folds into IO


class GroundTruthBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = db.default_cubemx_source()
        if cls.src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls.dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        cls.result = db.build_database(cls.src, cls.dbp)
        cls.conn = db.connect(cls.dbp)

    def test_mcu_and_part_counts(self):
        self.assertEqual(self.result.mcus, 424)
        self.assertEqual(self.result.packages["LQFP64"], 53)
        self.assertEqual(self.result.packages["LQFP100"], 60)

    def test_lqfp64_is_the_eleven(self):
        rep = db.package_report(self.conn, "LQFP64")
        must = sorted(d.pin for d in rep.must_switch)
        self.assertEqual(must, [1, 13, 17, 18, 19, 30, 31, 33, 47, 48, 60])
        self.assertEqual(rep.adg714_count, 2)

    def test_lqfp100_count_and_vdd_fixed(self):
        rep = db.package_report(self.conn, "LQFP100")
        self.assertEqual(rep.must_switch_count, 43)
        self.assertNotIn(100, [d.pin for d in rep.must_switch])  # pin 100 = VDD, never switched


if __name__ == "__main__":
    unittest.main(verbosity=2)
