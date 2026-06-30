"""
End-to-end importer test — the exact failing case from the live bug:

import a part whose symbol points at a per-part footprint nickname and whose
footprint has no 3D model, then assert that after import the placed symbol
resolves its footprint (MyFootprints:<name>) AND the footprint resolves its
3D model (${MY3DMODELS}/<file>). If this is green, the bug can't return.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.library.importer import LibPaths, import_part

SYMBOL = (
    '(kicad_symbol_lib (version 20211014) (generator "easyeda2kicad")\n'
    '  (symbol "ACME123"\n'
    '    (property "Reference" "U" (id 0) (at 0 0 0))\n'
    '    (property "Value" "ACME123" (id 1) (at 0 0 0))\n'
    '    (property "Footprint" "ACME123:SOT23-6" (id 2) (at 0 0 0))\n'
    '    (pin bidirectional line (at 0 0 0) (length 2.54))\n'
    '  )\n'
    ')\n'
)
FOOTPRINT = (
    '(footprint "SOT23-6" (version 20211014) (generator "easyeda2kicad")\n'
    '  (layer "F.Cu")\n'
    '  (pad "1" smd roundrect (at 0 0) (size 0.6 0.7) (layers "F.Cu"))\n'
    ')\n'
)


class ImporterEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.source = root / "src"
        self.source.mkdir()
        (self.source / "ACME123.kicad_sym").write_text(SYMBOL, encoding="utf-8")
        (self.source / "SOT23-6.kicad_mod").write_text(FOOTPRINT, encoding="utf-8")
        (self.source / "SOT23-6.step").write_text("SOLID fake step\n", encoding="utf-8")

        libroot = root / "libs"
        self.libs = LibPaths(
            symbols=libroot / "MySymbols.kicad_sym",
            footprints=libroot / "MyFootprints.pretty",
            models=libroot / "My3DModels",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_import_makes_part_schematic_ready(self):
        result = import_part(self.source, self.libs)

        # symbol merged with the shared-library footprint nickname
        self.assertEqual(result.symbols, ["ACME123"])
        sym_text = self.libs.symbols.read_text(encoding="utf-8")
        self.assertIn('(property "Footprint" "MyFootprints:SOT23-6"', sym_text)
        self.assertNotIn("ACME123:SOT23-6", sym_text)

        # footprint copied in and given a valid model line
        fp_text = (self.libs.footprints / "SOT23-6.kicad_mod").read_text(encoding="utf-8")
        self.assertIn('(model "${MY3DMODELS}/SOT23-6.step"', fp_text)

        # model file relocated into the shared model folder
        self.assertTrue((self.libs.models / "SOT23-6.step").exists())
        self.assertEqual(result.warnings, [])

    def test_reimport_is_idempotent(self):
        import_part(self.source, self.libs)
        import_part(self.source, self.libs)  # second time: no duplicate symbol
        sym_text = self.libs.symbols.read_text(encoding="utf-8")
        self.assertEqual(sym_text.count('(symbol "ACME123"'), 1)

    def test_zip_source(self):
        import zipfile
        zpath = Path(self._tmp.name) / "part.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in self.source.iterdir():
                zf.write(f, f.name)
        result = import_part(zpath, self.libs)
        self.assertEqual(result.symbols, ["ACME123"])
        self.assertTrue((self.libs.models / "SOT23-6.step").exists())


if __name__ == "__main__":
    unittest.main()
