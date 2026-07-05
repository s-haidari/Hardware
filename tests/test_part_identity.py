"""Canonical part identity from symbol properties (LibraryManager): existing and
future downloads group under their manufacturer / Mouser part number, derived from
the symbol's own embedded properties — no side index to maintain."""
import os
import sys
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402

_BLOCK = '''(symbol "ADG714BRUZ-REEL"
  (property "Reference" "U" (at 0 0 0))
  (property "Value" "ADG714BRUZ-REEL" (at 0 0 0))
  (property "Footprint" "MyFootprints:RU_24_ADI" (at 0 0 0))
  (property "Datasheet" "https://www.analog.com/adg714.pdf" (at 0 0 0))
  (property "MANUFACTURER" "Analog Devices" (at 0 0 0))
  (property "Description" "Octal SPST switch" (at 0 0 0))
)'''

_BARE = '''(symbol "MYSTERY_PART"
  (property "Reference" "U" (at 0 0 0))
  (property "Value" "~" (at 0 0 0))
)'''


class PartIdentityTests(unittest.TestCase):
    def test_extract_symbol_properties(self):
        props = LM.extract_symbol_properties(_BLOCK)
        self.assertEqual(props["Value"], "ADG714BRUZ-REEL")
        self.assertEqual(props["MANUFACTURER"], "Analog Devices")
        self.assertEqual(props["Description"], "Octal SPST switch")

    def test_part_identity_from_properties(self):
        ident = LM.part_identity(LM.extract_symbol_properties(_BLOCK))
        self.assertEqual(ident["mpn"], "ADG714BRUZ-REEL")
        self.assertEqual(ident["manufacturer"], "Analog Devices")
        self.assertIn("analog.com", ident["datasheet"])
        self.assertEqual(ident["description"], "Octal SPST switch")

    def test_placeholders_fall_back(self):
        ident = LM.part_identity(LM.extract_symbol_properties(_BARE), fallback="MYSTERY_PART")
        self.assertEqual(ident["mpn"], "MYSTERY_PART")   # '~' Value is not an identity
        self.assertIsNone(ident["manufacturer"])

    def test_key_normalisation(self):
        ident = LM.part_identity({"Mouser Part Number": "511-STM32F407VGT6",
                                  "Mfr": "STMicroelectronics"})
        self.assertEqual(ident["mpn"], "511-STM32F407VGT6")
        self.assertEqual(ident["manufacturer"], "STMicroelectronics")

    def test_grouped_scan_carries_identity(self):
        """scan_library_grouped rows expose mpn/manufacturer for the real library."""
        cfg = LM.load_config()
        if not pathlib.Path(cfg.get("SymbolLib", "")).exists():
            raise unittest.SkipTest("shared library not present on this machine")
        rows = LM.scan_library_grouped(cfg)
        if not rows:
            raise unittest.SkipTest("library is empty")
        self.assertTrue(all("mpn" in r and "manufacturer" in r for r in rows))
        self.assertTrue(all(r["mpn"] for r in rows))      # every row has a canonical name
        named = [r for r in rows if r["manufacturer"]]
        self.assertTrue(named, "no row carried a manufacturer from its symbol properties")


if __name__ == "__main__":
    unittest.main(verbosity=2)
