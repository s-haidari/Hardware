"""Tests for the Layer-B pinout authority (tools/stm32_authority.py)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db  # noqa: E402
import stm32_authority as auth  # noqa: E402


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls.dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, cls.dbp)
        cls.conn = db.connect(cls.dbp)

    def test_lqfp64_rollup_and_assignment(self):
        data = auth.build(self.conn, "LQFP64")
        r = data["rollup"]
        self.assertEqual(r["must_switch_count"], 11)
        self.assertEqual(r["cells_min"], 2)
        self.assertEqual(data["manifest"]["part_count"], 53)
        self.assertEqual(data["manifest"]["supported_families"],
                         ["STM32F0", "STM32F1", "STM32F2", "STM32F3", "STM32F4", "STM32F7"])
        p1 = next(p for p in data["positions"] if p["position"] == 1)
        self.assertIn("VBAT", p1["role_set"])
        self.assertIn("VDD", p1["role_set"])
        self.assertEqual(p1["switch_class"], db.SWITCH_MUST)
        self.assertEqual(p1["assignment"]["adg714"], {"cell": 1, "channel": 1, "destination": "VBAT_TGT"})
        p60 = next(p for p in data["positions"] if p["position"] == 60)
        self.assertTrue(p60["tags"]["is_boot"])

    def test_emit_yaml_json_tsv(self):
        out = Path(tempfile.mkdtemp())
        summ = auth.write_authority(self.conn, "LQFP64", out)
        self.assertEqual(len(summ["files"]), 3)
        j = json.loads((out / "pinout_authority_LQFP64.json").read_text(encoding="utf-8"))
        self.assertEqual(len(j["positions"]), 64)
        y = (out / "pinout_authority_LQFP64.yaml").read_text(encoding="utf-8")
        self.assertIn("must_switch_count: 11", y)
        tsv = (out / "pins_LQFP64.tsv").read_text(encoding="utf-8")
        self.assertGreater(len(tsv.splitlines()), 53)  # header + 53 parts x pins


if __name__ == "__main__":
    unittest.main(verbosity=2)
