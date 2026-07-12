"""OSH Park fab presets (nd_fab_presets) and the retroactive object-conform engine
(nd_object_conform) — make a project conform to a house standard, existing objects
and all."""
import os
import sys
import pathlib
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_fab_presets as fp            # noqa: E402
import nd_object_conform as oc         # noqa: E402
from nd_project_settings_manager import ProjectSettings   # noqa: E402


class FabPresetTests(unittest.TestCase):
    def test_osh_park_2layer_rules(self):
        p = fp.OSH_PARK_2LAYER
        self.assertAlmostEqual(p.min_track_width, 6 * fp.MIL)
        self.assertAlmostEqual(p.min_clearance, 6 * fp.MIL)
        self.assertAlmostEqual(p.min_drill, 10 * fp.MIL)
        self.assertAlmostEqual(p.min_via_diameter, 20 * fp.MIL)   # drill + 2*annular
        s = fp.apply_to_project_settings(ProjectSettings(), p)
        self.assertEqual(s.default_clearance, 6.0)                # mils
        self.assertEqual(s.default_track_width, 6.0)
        self.assertEqual(s.min_through_hole, 10.0)
        self.assertEqual(s.min_via_annular_width, 5.0)
        self.assertEqual(s.min_via_diameter, 20.0)
        self.assertEqual(s.min_copper_edge_clearance, 15.0)

    def test_osh_park_4layer_is_tighter(self):
        p = fp.OSH_PARK_4LAYER
        self.assertAlmostEqual(p.min_track_width, 5 * fp.MIL)
        self.assertAlmostEqual(p.min_annular_ring, 4 * fp.MIL)     # 4-layer: 4 mil ring
        self.assertAlmostEqual(p.min_via_diameter, 18 * fp.MIL)    # -> 18 mil via (was 20)
        self.assertEqual(p.layers, 4)
        self.assertEqual(len([lyr for lyr in p.stackup if lyr[1] == "copper"]), 4)
        self.assertEqual(p.inner_copper_oz, 0.5)
        self.assertIn("FR408", p.material)                          # exact OSH Park material
        s = fp.apply_to_project_settings(ProjectSettings(), p)
        self.assertEqual(s.default_track_width, 5.0)
        self.assertEqual(s.min_via_annular_width, 4.0)
        self.assertEqual(s.solder_mask_clearance, 2.0)             # 2 mil mask expansion
        self.assertEqual(s.min_hole_clearance, 5.0)                # 5 mil copper-to-hole

    def test_presets_registry(self):
        self.assertEqual(set(fp.PRESETS), {"OSH Park 2-layer", "OSH Park 4-layer"})

    def test_stackup_block_wellformed(self):
        b = fp.stackup_block(fp.OSH_PARK_2LAYER)
        self.assertEqual(b.count("("), b.count(")"))
        self.assertIn('(copper_finish "ENIG")', b)
        self.assertEqual(b.count('(type "copper")'), 2)          # 2-layer -> 2 copper
        self.assertEqual(fp.stackup_block(fp.OSH_PARK_4LAYER).count('(type "copper")'), 4)

    def test_set_board_stackup_insert_and_replace(self):
        # insert where none exists
        pcb = '(kicad_pcb (setup (pad_to_mask_clearance 0.05)) (net 0 ""))'
        new, ch = oc.set_board_stackup(pcb, fp.OSH_PARK_2LAYER)
        self.assertTrue(ch)
        self.assertIn("(stackup", new)
        self.assertEqual(new.count("("), new.count(")"))
        # replace an existing one, dropping the old finish
        pcb2 = '(kicad_pcb (setup (stackup (layer "F.Cu" (type "copper")) (copper_finish "HASL"))))'
        new2, ch2 = oc.set_board_stackup(pcb2, fp.OSH_PARK_4LAYER)
        self.assertTrue(ch2)
        self.assertIn("ENIG", new2)
        self.assertNotIn("HASL", new2)
        self.assertEqual(new2.count('(type "copper")'), 4)


    def test_set_board_thickness_replace_insert_and_create(self):
        # replace an existing (general (thickness ...))
        pcb = '(kicad_pcb (general (thickness 1.6)) (setup))'
        new, ch = oc.set_board_thickness(pcb, 0.8)
        self.assertTrue(ch)
        self.assertIn("(thickness 0.8)", new)
        self.assertNotIn("(thickness 1.6)", new)
        self.assertEqual(new.count("("), new.count(")"))
        # insert into an existing general with no thickness
        pcb2 = '(kicad_pcb (general (drawings 3)) (setup))'
        new2, ch2 = oc.set_board_thickness(pcb2, 1.6)
        self.assertTrue(ch2)
        self.assertIn("(thickness 1.6)", new2)
        self.assertIn("(drawings 3)", new2)
        self.assertEqual(new2.count("("), new2.count(")"))
        # create a general block when none exists
        pcb3 = '(kicad_pcb (setup))'
        new3, ch3 = oc.set_board_thickness(pcb3, 1.6)
        self.assertTrue(ch3)
        self.assertIn("(general", new3)
        self.assertIn("(thickness 1.6)", new3)
        self.assertEqual(new3.count("("), new3.count(")"))

    def test_write_fab_to_board_writes_stackup_and_thickness(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "b.kicad_pcb"
            p.write_text('(kicad_pcb (general (thickness 1.0)) '
                         '(setup (pad_to_mask_clearance 0.05)))', encoding="utf-8")
            rep = oc.write_fab_to_board(p, fp.OSH_PARK_4LAYER, backup=True)
            self.assertTrue(rep["written"])
            self.assertTrue(rep["stackup"] and rep["thickness"])
            txt = p.read_text(encoding="utf-8")
            # Board thickness now matches the preset's physical thickness.
            self.assertIn(f"(thickness {fp.OSH_PARK_4LAYER.board_thickness_mm:g})", txt)
            # The physical stackup + copper finish reached the board.
            self.assertIn("(stackup", txt)
            self.assertIn('(copper_finish "ENIG")', txt)
            self.assertEqual(txt.count('(type "copper")'), 4)
            self.assertEqual(txt.count("("), txt.count(")"))
            # A .bak was kept.
            self.assertTrue(p.with_suffix(p.suffix + ".bak").exists())

    def test_write_fab_to_board_idempotent_second_write_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "b.kicad_pcb"
            p.write_text('(kicad_pcb (general (thickness 1.0)) (setup))', encoding="utf-8")
            oc.write_fab_to_board(p, fp.OSH_PARK_2LAYER, backup=False)
            before = p.read_text(encoding="utf-8")
            rep2 = oc.write_fab_to_board(p, fp.OSH_PARK_2LAYER, backup=False)
            # Re-writing the SAME preset produces no on-disk change.
            self.assertFalse(rep2["written"])
            self.assertFalse(rep2["thickness"])              # thickness already matches
            self.assertEqual(p.read_text(encoding="utf-8"), before)


class ConformTests(unittest.TestCase):
    _PCB = ('(kicad_pcb (footprint "R"\n'
            '  (fp_text reference "R1" (at 0 0) (layer "F.SilkS") (effects (font (size 0.5 0.5) (thickness 0.1))))\n'
            '  (fp_text value "10k" (at 0 1) (layer "F.Fab") (effects (font (size 0.6 0.6) (thickness 0.1)))))\n'
            '  (gr_text "L" (at 5 5) (layer "F.SilkS") (effects (font (size 2 2) (thickness 0.3))))\n'
            '  (fp_text user "%R" (at 0 2) (layer "F.Cu") (effects (font (size 0.4 0.4) (thickness 0.08)))))')
    _SCH = ('(kicad_sch\n'
            '  (text "note" (at 0 0) (effects (font (size 1.27 1.27))))\n'
            '  (label "CLK" (at 1 1) (effects (font (size 1.27 1.27))))\n'
            '  (global_label "VCC" (at 2 2) (effects (font (size 1.5 1.5))))\n'
            '  (text_box "b" (at 3 3) (effects (font (size 9 9)))))')

    def test_pcb_conform_by_layer(self):
        new, counts = oc.conform_pcb_text(self._PCB, {"silk": (1.0, 0.15), "fab": (1.0, 0.15),
                                                      "copper": (1.2, 0.2)})
        self.assertEqual(counts, {"silk": 2, "fab": 1, "copper": 1})  # silk = ref + gr_text
        self.assertIn("(size 1 1) (thickness 0.15)", new)
        self.assertIn("(size 1.2 1.2) (thickness 0.2)", new)
        # only the picked categories change
        only_silk, c = oc.conform_pcb_text(self._PCB, {"silk": (1.0, 0.15)})
        self.assertEqual(c, {"silk": 2})
        self.assertIn("(size 0.6 0.6)", only_silk)                     # fab untouched

    def test_schematic_conform_excludes_text_box(self):
        new, counts = oc.conform_schematic_text(self._SCH, {"text": (2.0, None), "labels": (1.0, None)})
        self.assertEqual(counts, {"text": 1, "labels": 2})
        self.assertIn('(text "note" (at 0 0) (effects (font (size 2 2))', new)
        self.assertIn("(size 9 9)", new)                               # text_box preserved

    def test_conform_project_dry_run_then_apply_with_backup(self):
        with tempfile.TemporaryDirectory() as td:
            tp = pathlib.Path(td)
            pcb = tp / "b.kicad_pcb"
            pcb.write_text(self._PCB, encoding="utf-8")
            sch = tp / "b.kicad_sch"
            sch.write_text(self._SCH, encoding="utf-8")
            before = pcb.read_text(encoding="utf-8")

            dry = oc.conform_project([pcb, sch], {"silk": (1.0, 0.15)}, {"labels": (1.0, None)},
                                     "20260705_120000", dry_run=True)
            self.assertFalse(dry["written"])
            self.assertEqual(pcb.read_text(encoding="utf-8"), before)   # dry run wrote nothing
            self.assertEqual(dry["total"], 2 + 2)                       # 2 silk + 2 labels

            wet = oc.conform_project([pcb, sch], {"silk": (1.0, 0.15)}, {"labels": (1.0, None)},
                                     "20260705_120000", dry_run=False)
            self.assertTrue(wet["written"])
            self.assertIn("(size 1 1)", pcb.read_text(encoding="utf-8"))
            self.assertTrue((tp / "b.kicad_pcb.20260705_120000.bak").exists())   # backup taken
            # backup holds the original
            self.assertEqual((tp / "b.kicad_pcb.20260705_120000.bak").read_text(encoding="utf-8"),
                             before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
