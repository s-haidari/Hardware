"""
libtable tests — register libraries + define ${MY3DMODELS}, idempotently.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.kicad import libtable

FP_TABLE = (
    "(fp_lib_table\n"
    "\t(version 7)\n"
    '\t(lib (name "KiCad") (type "Table") (uri "x") (options "") (descr ""))\n'
    ")\n"
)


class LibTableTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self._tmp.name)
        self.libs = Path(self._tmp.name) / "libs"
        (self.cfg / "fp-lib-table").write_text(FP_TABLE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_adds_fp_lib_when_missing(self):
        path = self.cfg / "fp-lib-table"
        changed = libtable.ensure_lib_entry(path, "fp_lib_table", "MyFootprints", "C:/libs/MyFootprints.pretty")
        self.assertTrue(changed)
        text = path.read_text(encoding="utf-8")
        self.assertIn('(name "MyFootprints")', text)
        self.assertEqual(text.count('(name "KiCad")'), 1)  # existing entry preserved

    def test_fp_lib_idempotent(self):
        path = self.cfg / "fp-lib-table"
        libtable.ensure_lib_entry(path, "fp_lib_table", "MyFootprints", "C:/libs/MyFootprints.pretty")
        again = libtable.ensure_lib_entry(path, "fp_lib_table", "MyFootprints", "C:/libs/MyFootprints.pretty")
        self.assertFalse(again)

    def test_creates_sym_table_when_absent(self):
        path = self.cfg / "sym-lib-table"
        self.assertFalse(path.exists())
        changed = libtable.ensure_lib_entry(path, "sym_lib_table", "MySymbols", "C:/libs/MySymbols.kicad_sym")
        self.assertTrue(changed)
        self.assertIn('(name "MySymbols")', path.read_text(encoding="utf-8"))

    def test_defines_env_var(self):
        common = self.cfg / "kicad_common.json"
        common.write_text(json.dumps({"environment": {"vars": None}}), encoding="utf-8")
        changed = libtable.ensure_env_var(common, "MY3DMODELS", "C:/libs/My3DModels")
        self.assertTrue(changed)
        data = json.loads(common.read_text(encoding="utf-8"))
        self.assertEqual(data["environment"]["vars"]["MY3DMODELS"], "C:/libs/My3DModels")
        # idempotent
        self.assertFalse(libtable.ensure_env_var(common, "MY3DMODELS", "C:/libs/My3DModels"))

    def test_dry_run_changes_nothing(self):
        path = self.cfg / "fp-lib-table"
        before = path.read_text(encoding="utf-8")
        would = libtable.ensure_lib_entry(path, "fp_lib_table", "MyFootprints", "x", dry_run=True)
        self.assertTrue(would)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_register_libraries_all(self):
        res = libtable.register_libraries(self.cfg, self.libs)
        self.assertTrue(res.fp_lib_added)
        self.assertTrue(res.sym_lib_added)
        self.assertTrue(res.env_var_set)
        self.assertTrue(res.changed)
        # second run is a no-op
        res2 = libtable.register_libraries(self.cfg, self.libs)
        self.assertFalse(res2.changed)


if __name__ == "__main__":
    unittest.main()
