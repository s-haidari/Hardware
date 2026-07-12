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

    def test_part_number_is_cubemx_ref_stored_as_is(self):
        # part_number is the CubeMX RefName verbatim — one row per PINOUT, not per
        # orderable MPN. CubeMX collapses variant groups + a trailing 'x' suffix
        # wildcard (any temperature/quality grade), so the stored keys legitimately
        # contain '(' groups and end in 'x'. This is intentional: the mcu table is
        # keyed by pinout and real MPNs are matched back at query time
        # (stm32_authority._cubemx_regex / resolve_part). Guard the design so nobody
        # re-adds a half-expander that only strips '(...)' and leaves the 'x'.
        rows = [r[0] for r in self.conn.execute("SELECT part_number FROM mcu")]
        self.assertTrue(rows, "no MCUs built")
        collapsed = [pn for pn in rows if "(" in pn]
        self.assertTrue(collapsed, "expected collapsed CubeMX group refs, e.g. STM32F031C(4-6)Tx")
        # every collapsed ref is stored WHOLE, not split into separate rows
        self.assertIn("STM32F031C(4-6)Tx", rows)
        # the module must NOT ship a ref-name expander that implies a build the
        # build never performs (the checked-in DB proves the refs are collapsed)
        self.assertFalse(hasattr(db, "expand_ref_names"),
                         "expand_ref_names was never wired into build_database; "
                         "part_number is the CubeMX ref stored as-is")

    def test_collapsed_ref_resolves_to_real_mpn(self):
        # The stored collapsed ref must round-trip to a REAL orderable MPN via the
        # authority resolver — proof that storing the pinout key (not an expanded,
        # still-wildcarded pseudo-MPN) is the correct end-to-end behavior.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import stm32_authority as auth  # noqa: E402
        # STM32F031C(4-6)Tx collapses grades 4/6 and a 'x' suffix; F031C6T6 is real.
        res = auth.resolve_part(self.conn, "STM32F031C6T6")
        self.assertIsNotNone(res, "real MPN STM32F031C6T6 should resolve to a stored pinout")
        self.assertEqual(res["package"], "LQFP48")


_SYNTH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Mcu RefName="STM32F999ZZTx" Family="STM32F9" Line="STM32F9x" Package="LQFP4">
  <Voltage Min="1.8" Max="3.6"/>
  <Pin Position="1" Name="PH0-OSC_IN" Type="I/O">
    <Signal Name="RCC_OSC_IN"/>
    <Signal Name="GPIO"/>
  </Pin>
  <Pin Position="2" Name="PH1-OSC_OUT" Type="I/O">
    <Signal Name="RCC_OSC_OUT"/>
    <Signal Name="GPIO"/>
  </Pin>
  <Pin Position="3" Name="VDD" Type="Power"/>
  <Pin Position="4" Name="VSS" Type="Power"/>
</Mcu>
"""


class SchemaClassifierAlignmentTests(unittest.TestCase):
    """The electrical_class CHECK enum must be exactly what electrical_class() can
    emit — no unreachable value (oscillator pins classify as 'io'; their oscillator
    nature lives in pin_role.role_name)."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        src = cls.dir / "mcu"
        src.mkdir()
        (src / "STM32F999.xml").write_text(_SYNTH_XML, encoding="utf-8")
        cls.dbp = cls.dir / "stm32.sqlite"
        # family_prefix="STM32F" matches the synthetic STM32F9 MCU.
        db.build_database(src, cls.dbp)
        cls.conn = db.connect(cls.dbp)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_oscillator_never_an_electrical_class(self):
        # The OSC pins built above are the exact case the enum's 'oscillator' value
        # pretended to cover — verify they land in 'io', not 'oscillator'.
        osc = self.conn.execute(
            "SELECT electrical_class FROM mcu_package_pin WHERE raw_pin_name LIKE '%OSC%'"
        ).fetchall()
        self.assertTrue(osc, "synthetic OSC pins should be present")
        self.assertTrue(all(r[0] == "io" for r in osc))
        n = self.conn.execute(
            "SELECT COUNT(*) FROM mcu_package_pin WHERE electrical_class='oscillator'"
        ).fetchone()[0]
        self.assertEqual(n, 0, "the classifier can never emit 'oscillator'")

    def test_every_stored_class_is_reachable_from_classifier(self):
        # Every electrical_class value the DB actually holds must be one the
        # electrical_class() classifier can produce (the enum has no dead values).
        stored = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT electrical_class FROM mcu_package_pin")}
        reachable = {"io", "power", "ground", "reset", "boot", "vcap", "nc"}
        self.assertTrue(stored, "no pins built")
        self.assertTrue(stored <= reachable,
                        f"unreachable stored classes: {stored - reachable}")
        self.assertNotIn("oscillator", stored)

    def test_check_constraint_rejects_oscillator(self):
        # The tightened CHECK must actively reject 'oscillator' so no code/migration
        # can ever write the dead value the finding flagged.
        import sqlite3
        pid = self.conn.execute("SELECT id FROM mcu_package_pin LIMIT 1").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO mcu_package_pin (mcu_id, physical_pin_number, "
                "canonical_pin_name, electrical_class) VALUES "
                "((SELECT mcu_id FROM mcu_package_pin WHERE id=?), 99, 'PX99', 'oscillator')",
                (pid,))
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main(verbosity=2)
