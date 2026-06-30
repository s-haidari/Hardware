"""Library maintenance: dedupe, remove part, process downloads."""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.library import manage
from hwkit.library.importer import LibPaths

LIB = (
    '(kicad_symbol_lib (version 20211014) (generator "x")\n'
    '  (symbol "A" (property "Footprint" "MyFootprints:FA" (id 2) (at 0 0 0)))\n'
    '  (symbol "A" (property "Footprint" "MyFootprints:FA" (id 2) (at 0 0 0)))\n'
    '  (symbol "B" (property "Footprint" "MyFootprints:FB" (id 2) (at 0 0 0)))\n'
    ')\n'
)


class ManageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.libs = LibPaths(root / "MySymbols.kicad_sym", root / "MyFootprints.pretty", root / "My3DModels")
        self.libs.ensure()
        self.libs.symbols.write_text(LIB, encoding="utf-8")
        (self.libs.footprints / "FB.kicad_mod").write_text('(footprint "FB")', encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_dedupe(self):
        removed = manage.dedupe(self.libs)
        self.assertEqual(removed, 1)
        self.assertEqual(self.libs.symbols.read_text(encoding="utf-8").count('(symbol "A"'), 1)

    def test_remove_part_with_footprint(self):
        res = manage.remove_part(self.libs, "B", remove_footprint=True)
        self.assertEqual(res["symbol_removed"], 1)
        self.assertTrue(res["footprint_removed"])
        self.assertNotIn('(symbol "B"', self.libs.symbols.read_text(encoding="utf-8"))
        self.assertFalse((self.libs.footprints / "FB.kicad_mod").exists())

    def test_process_downloads(self):
        dl = Path(self._tmp.name) / "downloads"
        dl.mkdir()
        z = dl / "part.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("C.kicad_sym", '(kicad_symbol_lib (symbol "C" (property "Footprint" "X:FC" (id 2)(at 0 0 0))))')
            zf.writestr("FC.kicad_mod", '(footprint "FC")')
            zf.writestr("FC.step", "x")
        res = manage.process_downloads(self.libs, dl, clear=True)
        self.assertIn("C", res.imported)
        self.assertEqual(res.cleared, ["part.zip"])
        self.assertFalse(z.exists())


if __name__ == "__main__":
    unittest.main()
