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


class EnrichTests(unittest.TestCase):
    """Enrich-from-MPN: the property writer, fill-blanks-only orchestration, and the
    snapshot-guarded dry-run library flow."""

    def test_set_symbol_property_replace_and_insert(self):
        blk = ('(symbol "P" (property "Reference" "U" (at 0 0 0)) '
               '(property "Value" "P" (at 0 0 0)) (property "Datasheet" "" (at 0 0 0)))')
        nb = LM.set_symbol_property(blk, "Datasheet", "http://ds/p.pdf")   # replace blank
        nb = LM.set_symbol_property(nb, "MANUFACTURER", "Acme")            # insert absent
        p = LM.extract_symbol_properties(nb)
        self.assertEqual(p["Datasheet"], "http://ds/p.pdf")
        self.assertEqual(p["MANUFACTURER"], "Acme")

    def test_enrich_symbol_fills_blanks_only(self):
        blk = ('(symbol "X" (property "Value" "MPN1" (at 0 0 0)) '
               '(property "MANUFACTURER" "Keep" (at 0 0 0)) (property "Datasheet" "" (at 0 0 0)))')
        out, changed = LM.enrich_symbol(blk, {"manufacturer": "WRONG",
                                              "datasheet": "http://ds", "description": "d"})
        p = LM.extract_symbol_properties(out)
        self.assertEqual(p["MANUFACTURER"], "Keep")           # never overwrite a value
        self.assertEqual(p["Datasheet"], "http://ds")         # blank filled
        self.assertEqual(dict(changed).keys(), {"datasheet", "description"})

    def test_enrich_writes_mouser_part_number(self):
        blk = '(symbol "X" (property "Value" "MPN1" (at 0 0 0)) (property "Datasheet" "" (at 0 0 0)))'
        out, changed = LM.enrich_symbol(
            blk, {"manufacturer": "ST", "datasheet": "http://ds", "mouser_pn": "584-MPN1"})
        self.assertEqual(LM.extract_symbol_properties(out)["Mouser Part Number"], "584-MPN1")
        self.assertIn("mouser_pn", dict(changed))


class LibrarySourcingTests(unittest.TestCase):
    def test_flags_obsolete_and_out_of_stock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sym = pathlib.Path(td) / "S.kicad_sym"
            sym.write_text(
                '(kicad_symbol_lib\n'
                '  (symbol "ADG714" (property "Value" "ADG714BRUZ-REEL" (at 0 0 0)) '
                '(property "MANUFACTURER" "ADI" (at 0 0 0)))\n'
                '  (symbol "OLD" (property "Value" "OLDPART" (at 0 0 0)) '
                '(property "MANUFACTURER" "X" (at 0 0 0)))\n'
                '  (symbol "R" (property "Value" "10k" (at 0 0 0)))\n)\n', encoding="utf-8")

            def stub(mpn):
                if mpn == "ADG714BRUZ-REEL":
                    return {"lifecycle": "Active", "stock": 4374, "unit_price": "$6.18",
                            "mouser_pn": "584-ADG714BRUZ-R"}
                if mpn == "OLDPART":
                    return {"lifecycle": "Obsolete", "stock": 0, "mouser_pn": "X",
                            "suggested_replacement": "NEWPART"}
                return None
            rep = LM.library_sourcing_report(
                {"SymbolLib": str(sym), "FootprintLib": td, "ModelLib": td}, stub)
            c = rep["counts"]
            self.assertEqual(c["parts"], 2)                   # the bare 10k has no MPN, skipped
            self.assertEqual(c["obsolete_nrnd"], 1)
            self.assertEqual(c["out_of_stock"], 1)
            self.assertIn("NEWPART", rep["markdown"])         # replacement surfaced
            self.assertIn("OLD", rep["markdown"])

    def test_enrich_skips_already_complete_parts(self):
        """No API call is spent on a part whose target fields are all filled — so
        enrich is cheap to run after every ZIP import."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sym = pathlib.Path(td) / "S.kicad_sym"
            sym.write_text(
                '(kicad_symbol_lib\n'
                '  (symbol "DONE" (property "Value" "PART_A" (at 0 0 0)) '
                '(property "MANUFACTURER" "ADI" (at 0 0 0)) (property "Datasheet" "http://a" (at 0 0 0)) '
                '(property "Description" "x" (at 0 0 0)) (property "Mouser Part Number" "584-A" (at 0 0 0)))\n'
                '  (symbol "NEW" (property "Value" "PART_B" (at 0 0 0)) '
                '(property "MANUFACTURER" "TI" (at 0 0 0)))\n)\n', encoding="utf-8")
            calls = []

            def lookup(mpn):
                calls.append(mpn)
                return {"datasheet": "http://ds", "description": "d", "mouser_pn": "584-B"}
            r = LM.enrich_library({"SymbolLib": str(sym), "FootprintLib": td, "ModelLib": td},
                                  lookup, dry_run=False)
            self.assertEqual(r["looked_up"], 1)          # only NEW
            self.assertEqual(calls, ["PART_B"])          # DONE skipped, no call
            self.assertIn("http://ds", sym.read_text(encoding="utf-8"))

    def test_finalize_import_links_and_enriches(self):
        """finalize_import (run automatically after a ZIP import) links footprint/3D
        and enriches from the given lookup in one pass."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = pathlib.Path(td)
            (tp / "S.kicad_sym").write_text(
                '(kicad_symbol_lib\n  (symbol "STM32F407" '
                '(property "Value" "STM32F407VGT6" (at 0 0 0)) '
                '(property "MANUFACTURER" "ST" (at 0 0 0)))\n)\n', encoding="utf-8")
            (tp / "STM32F407.kicad_mod").write_text('(footprint "STM32F407" (pad "1" smd))', encoding="utf-8")
            cfg = {"SymbolLib": str(tp / "S.kicad_sym"), "FootprintLib": str(tp), "ModelLib": str(tp)}
            res = LM.finalize_import(cfg, LM._NullLog(),
                                     lookup=lambda m: {"datasheet": "http://ds", "mouser_pn": "511-X"})
            self.assertEqual(res["linked"]["footprint_count"], 1)      # linked STM32F407 footprint
            txt = (tp / "S.kicad_sym").read_text(encoding="utf-8")
            self.assertIn("http://ds", txt)                            # enriched datasheet
            self.assertIn("511-X", txt)                                # Mouser P/N written

    def test_enrich_library_dry_run_then_write(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            sym = pathlib.Path(td) / "Sym.kicad_sym"
            sym.write_text(
                '(kicad_symbol_lib\n'
                '  (symbol "STM32F407VGT6" (property "Value" "STM32F407VGT6" (at 0 0 0)) '
                '(property "MANUFACTURER" "" (at 0 0 0)))\n)\n', encoding="utf-8")
            cfg = {"SymbolLib": str(sym), "FootprintLib": td, "ModelLib": td}

            def lookup(mpn):
                return {"manufacturer": "STMicroelectronics",
                        "datasheet": "http://st/ds.pdf"} if mpn else None

            before = sym.read_text(encoding="utf-8")
            dry = LM.enrich_library(cfg, lookup, dry_run=True)
            self.assertFalse(dry["written"])
            self.assertEqual(sym.read_text(encoding="utf-8"), before)   # dry run wrote nothing
            self.assertEqual(len(dry["changes"]), 1)
            self.assertEqual(dry["changes"][0]["mpn"], "STM32F407VGT6")

            wet = LM.enrich_library(cfg, lookup, dry_run=False)
            self.assertTrue(wet["written"])
            self.assertIn("STMicroelectronics", sym.read_text(encoding="utf-8"))
            self.assertTrue((pathlib.Path(td) / ".trash").exists())     # snapshot taken


class AutoAssignTests(unittest.TestCase):
    """Auto-associate footprints + 3D models across the library (no KiCad), by
    name / identity / token match, dry-run then snapshot-backed apply."""

    def _lib(self, td):
        tp = pathlib.Path(td)
        (tp / "Sym.kicad_sym").write_text(
            '(kicad_symbol_lib\n'
            '  (symbol "ADG714BRUZ-REEL"\n    (property "Reference" "U" (at 0 0 0))\n'
            '    (property "Value" "ADG714BRUZ-REEL" (at 0 0 0))\n'
            '    (property "Footprint" "MyFootprints:GONE" (at 0 0 0)))\n'   # dangling
            '  (symbol "STM32F407"\n    (property "Reference" "U" (at 0 0 0))\n'
            '    (property "Value" "STM32F407VGT6" (at 0 0 0)))\n)\n', encoding="utf-8")  # no fp
        (tp / "RU_24_ADG714.kicad_mod").write_text('(footprint "RU_24_ADG714" (pad "1" smd))', encoding="utf-8")
        (tp / "STM32F407.kicad_mod").write_text('(footprint "STM32F407" (pad "1" smd))', encoding="utf-8")
        (tp / "STM32F407.step").write_text("solid", encoding="utf-8")
        return {"SymbolLib": str(tp / "Sym.kicad_sym"), "FootprintLib": str(tp), "ModelLib": str(tp)}

    def test_dry_run_proposes_without_writing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = self._lib(td)
            before = pathlib.Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
            r = LM.auto_assign_library(cfg, dry_run=True)
            self.assertFalse(r["written"])
            self.assertEqual(pathlib.Path(cfg["SymbolLib"]).read_text(encoding="utf-8"), before)
            fps = {a["symbol"]: a["assign"] for a in r["footprints"]}
            self.assertEqual(fps["ADG714BRUZ-REEL"], "RU_24_ADG714")   # token match
            self.assertEqual(fps["STM32F407"], "STM32F407")            # name match
            self.assertEqual(r["models"][0]["assign"], "STM32F407.step")

    def test_apply_writes_footprint_and_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            cfg = self._lib(td)
            r = LM.auto_assign_library(cfg, dry_run=False)
            self.assertTrue(r["written"])
            txt = pathlib.Path(cfg["SymbolLib"]).read_text(encoding="utf-8")
            blk = {LM.extract_symbol_name(b): b for b in LM.extract_symbol_blocks(txt)}
            self.assertEqual(LM.symbol_footprint_ref(blk["STM32F407"]), "STM32F407")
            self.assertEqual(LM.symbol_footprint_ref(blk["ADG714BRUZ-REEL"]), "RU_24_ADG714")
            self.assertIn("(model", (pathlib.Path(td) / "STM32F407.kicad_mod").read_text(encoding="utf-8"))


class HealthReportTests(unittest.TestCase):
    def test_library_health_report(self):
        cfg = LM.load_config()
        if not pathlib.Path(cfg.get("SymbolLib", "")).exists():
            raise unittest.SkipTest("shared library not present")
        rep = LM.library_health_report(cfg)
        c = rep["counts"]
        self.assertGreaterEqual(c["parts"], c["complete"])
        self.assertLessEqual(c["complete"], c["parts"])
        self.assertIn("Library Health", rep["markdown"])
        self.assertEqual(len(rep["dangling"]), c["dangling"])         # lists match counts
        self.assertEqual(len(rep["no_manufacturer"]), c["no_manufacturer"])


class KicadBomTests(unittest.TestCase):
    """Smart BOM from any KiCad schematic: skip power/virtual symbols, group by real
    identity, IC Value-as-MPN vs bare passives, and optional enrichment."""

    _SCH = ('(kicad_sch (version 20230121)\n'
            '  (symbol (lib_id "Device:R") (property "Reference" "R10" (at 0 0 0)) '
            '(property "Value" "10k" (at 0 0 0)) (property "Footprint" "R_0402" (at 0 0 0)))\n'
            '  (symbol (lib_id "Device:R") (property "Reference" "R2" (at 0 0 0)) '
            '(property "Value" "10k" (at 0 0 0)) (property "Footprint" "R_0402" (at 0 0 0)))\n'
            '  (symbol (lib_id "MCU_ST:STM32F407VGTx") (property "Reference" "U1" (at 0 0 0)) '
            '(property "Value" "STM32F407VGT6" (at 0 0 0)) '
            '(property "MANUFACTURER" "STMicroelectronics" (at 0 0 0)) '
            '(property "Footprint" "LQFP-100" (at 0 0 0)))\n'
            '  (symbol (lib_id "power:GND") (property "Reference" "#PWR01" (at 0 0 0)) '
            '(property "Value" "GND" (at 0 0 0)))\n)\n')

    def _bom(self, lookup=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "t.kicad_sch"
            p.write_text(self._SCH, encoding="utf-8")
            return LM.bom_from_kicad_schematic(str(p), lookup=lookup)

    def test_skips_power_groups_and_natural_sort(self):
        bom = self._bom()
        self.assertEqual(bom["component_count"], 3)          # power symbol skipped
        r = next(x for x in bom["rows"] if x["value"] == "10k")
        self.assertEqual(r["refs"], ["R2", "R10"])           # R2 < R10, grouped
        self.assertEqual(r["qty"], 2)
        self.assertEqual(r["mpn"], "")                       # bare passive: no MPN

    def test_ic_mpn_and_enrich(self):
        def lookup(mpn):
            return {"manufacturer": "ST", "datasheet": "http://ds"} if "STM32" in (mpn or "") else None
        bom = self._bom(lookup=lookup)
        u = next(x for x in bom["rows"] if x["refs"] == ["U1"])
        self.assertEqual(u["mpn"], "STM32F407VGT6")          # IC: Value is the MPN
        self.assertEqual(u["manufacturer"], "STMicroelectronics")
        self.assertEqual(u["datasheet"], "http://ds")        # blank field enriched
        self.assertIn("Refs,Qty,Value,MPN", bom["csv"])

    def test_not_a_schematic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "x.kicad_sym"
            p.write_text("(kicad_symbol_lib)", encoding="utf-8")
            self.assertEqual(LM.bom_from_kicad_schematic(str(p))["rows"], [])


class ConsolidatedBomTests(unittest.TestCase):
    @staticmethod
    def _sch(parts):
        body = "\n".join(
            f'  (symbol (lib_id "L:X") (property "Reference" "{r}" (at 0 0 0)) '
            f'(property "Value" "{v}" (at 0 0 0)) (property "Footprint" "{fp}" (at 0 0 0)))'
            for r, v, fp in parts)
        return f"(kicad_sch (version 1)\n{body}\n)"

    def test_merges_across_boards_with_per_board_qty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = pathlib.Path(td)
            (tp / "parent.kicad_sch").write_text(
                self._sch([("R1", "10k", "R_0402"), ("R2", "10k", "R_0402"),
                           ("U1", "STM32H753", "LQFP144")]), encoding="utf-8")
            (tp / "card.kicad_sch").write_text(
                self._sch([("R1", "10k", "R_0402"), ("R2", "10k", "R_0402"),
                           ("R3", "10k", "R_0402")]), encoding="utf-8")
            b = LM.consolidated_bom({"Parent": [str(tp / "parent.kicad_sch")],
                                     "Card": [str(tp / "card.kicad_sch")]})
            self.assertEqual(b["board_names"], ["Parent", "Card"])
            r10k = next(r for r in b["rows"] if r["value"] == "10k")
            self.assertEqual(r10k["total_qty"], 5)                 # 2 parent + 3 card
            self.assertEqual(r10k["per_board"], {"Parent": 2, "Card": 3})
            self.assertEqual(b["total_parts"], 6)                  # 5 R + 1 U
            # a per-board column exists in the CSV
            self.assertIn("Total,Parent,Card", b["csv"])

    def test_mouser_lookup_from_config_needs_a_key(self):
        self.assertIsNone(LM.mouser_lookup_from_config({}))
        self.assertIsNotNone(LM.mouser_lookup_from_config({"MouserApiKey": "abc"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
