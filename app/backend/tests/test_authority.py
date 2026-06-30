"""
Pinout Authority Generator — reproduces ground truth and emits YAML+JSON+TSV.
Needs the real DB; skipped otherwise.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.core import config
from hwkit.pins import authority

DB = config.stm_database_path()
_HAS_DB = DB.exists()


@unittest.skipUnless(_HAS_DB, "stm32_profiles.sqlite not present")
class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect(DB)
        cls.conn.row_factory = sqlite3.Row
        cls.data = authority.build(cls.conn, "LQFP64")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def _pos(self, n):
        return next(p for p in self.data["positions"] if p["position"] == n)

    def test_rollup_matches_ground_truth(self):
        self.assertEqual(self.data["rollup"]["must_switch_count"], 11)
        self.assertEqual(self.data["rollup"]["cells_min"], 2)
        self.assertEqual(self.data["manifest"]["part_count"], 53)

    def test_extraction_tags(self):
        self.assertTrue(self._pos(60)["tags"]["is_boot"])          # BOOT pin
        self.assertTrue(self._pos(13)["tags"]["is_analog_supply"]) # VDDA/VREF
        self.assertTrue(self._pos(5)["tags"]["is_clock"])          # oscillator
        # debug pins
        self.assertTrue(self._pos(46)["tags"]["is_debug"])         # SWDIO

    def test_pin1_role_set_and_assignment(self):
        p1 = self._pos(1)
        self.assertIn("VBAT", p1["role_set"])
        self.assertIn("VDD", p1["role_set"])
        self.assertFalse(p1["is_fixed"])
        self.assertEqual(p1["assignment"]["kind"], "switched")

    def test_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            res = authority.write_authority(self.conn, "LQFP64", Path(d))
            for f in res["files"]:
                self.assertTrue((Path(d) / f).exists(), f)
            data = json.loads((Path(d) / "pinout_authority_LQFP64.json").read_text(encoding="utf-8"))
            self.assertEqual(data["rollup"]["must_switch_count"], 11)


if __name__ == "__main__":
    unittest.main()
