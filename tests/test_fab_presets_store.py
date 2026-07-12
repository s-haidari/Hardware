"""User fab-preset persistence (nd_fab_presets): built-ins overridable but not
deletable, pure user presets fully editable, and get_preset resolving both — the
store behind the Editor's Manage Fabrication Presets modal."""
import os
import sys
import pathlib
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_fab_presets as fabp          # noqa: E402
import nd_pcb_profiles as pcbprof      # noqa: E402


def _custom(name="JLC 4-layer"):
    return fabp.FabPreset(
        name=name, layers=4,
        min_track_width=0.09, min_clearance=0.09, min_drill=0.15,
        min_annular_ring=0.1, min_edge_clearance=0.3,
        default_track_width=0.2, default_via_diameter=0.45, default_via_drill=0.2,
        board_thickness_mm=1.6, copper_oz=1.0, material="FR-4", finish="HASL",
        soldermask="green",
        stackup=(("F.Cu", "copper", 0.035, "copper"),
                 ("core", "core", 1.5, "FR-4"),
                 ("B.Cu", "copper", 0.035, "copper")))


class FabPresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="fabp_")) / "fab_presets.json"

    def test_roundtrip_to_from_dict(self):
        p = _custom()
        r = fabp.FabPreset.from_dict(p.to_dict())
        self.assertEqual(r, p)                       # frozen dataclass equality
        self.assertIsInstance(r.stackup, tuple)
        self.assertIsInstance(r.stackup[0], tuple)   # tuples restored, not lists

    def test_from_dict_ignores_unknown_and_missing(self):
        d = _custom().to_dict()
        d["some_future_key"] = 123                   # newer file, older reader
        del d["verify_note"]                         # optional key absent
        r = fabp.FabPreset.from_dict(d)
        self.assertEqual(r.name, "JLC 4-layer")
        self.assertEqual(r.verify_note, "")          # default kicks in

    def test_save_and_get(self):
        p = _custom()
        fabp.save_preset(p, path=self.tmp)
        self.assertEqual(fabp.get_preset("JLC 4-layer", path=self.tmp), p)
        self.assertIn("JLC 4-layer", fabp.load_presets(path=self.tmp))
        # built-ins still present alongside the user preset
        self.assertIn("OSH Park 4-layer", fabp.load_presets(path=self.tmp))

    def test_builtin_override_then_revert(self):
        self.assertTrue(fabp.is_builtin("OSH Park 4-layer"))
        override = fabp.FabPreset.from_dict(
            {**fabp.PRESETS["OSH Park 4-layer"].to_dict(), "finish": "HASL"})
        fabp.save_preset(override, path=self.tmp)
        self.assertEqual(fabp.get_preset("OSH Park 4-layer", path=self.tmp).finish, "HASL")
        self.assertTrue(fabp.has_user_preset("OSH Park 4-layer", path=self.tmp))
        # delete reverts to the built-in default (not gone)
        self.assertTrue(fabp.delete_preset("OSH Park 4-layer", path=self.tmp))
        self.assertEqual(fabp.get_preset("OSH Park 4-layer", path=self.tmp).finish, "ENIG")

    def test_delete_user_preset(self):
        fabp.save_preset(_custom(), path=self.tmp)
        self.assertTrue(fabp.delete_preset("JLC 4-layer", path=self.tmp))
        self.assertIsNone(fabp.get_preset("JLC 4-layer", path=self.tmp))
        self.assertFalse(fabp.delete_preset("JLC 4-layer", path=self.tmp))   # nothing to delete

    def test_get_missing_is_none(self):
        self.assertIsNone(fabp.get_preset("nope", path=self.tmp))
        self.assertIsNone(fabp.get_preset("", path=self.tmp))

    def test_validate_netclasses_uses_custom_fab_floor(self):
        import nd_netclass_manager as ncm
        preset = _custom()                                # min_track 0.09, min_clearance 0.09
        floor = ncm.floor_from_fab_preset(preset)
        self.assertAlmostEqual(floor["min_track"], 0.09)
        self.assertAlmostEqual(floor["min_clearance"], 0.09)
        m = ncm.NetClassManager()
        # a class legal on this loose custom fab but ILLEGAL on the default OSH-4L floor
        m.add_netclass(ncm.NetClass(name="Sig", clearance=0.1, track_width=0.1,
                                    via_diameter=0.6, via_drill=0.3, patterns=["*SIG*"]))
        # against the custom floor: clean
        self.assertEqual(ncm.validate_netclasses(m, floor=floor), [])
        # against the strict default floor: min_track 0.127 → the 0.1 track is flagged
        issues = ncm.validate_netclasses(m, "OSH Park 4-layer")
        self.assertTrue(any("track" in i["issue"] for i in issues))

    def test_profile_can_reference_a_custom_fab(self):
        # The frozen-PRESETS gap: a profile pointing at a user fab now validates.
        fabp.save_preset(_custom(), path=self.tmp)
        # monkeypatch the default path so validate_profile sees the temp store
        orig = fabp._presets_path
        fabp._presets_path = lambda: self.tmp
        try:
            prof = pcbprof.Profile("MyBoard", "JLC 4-layer", [])
            self.assertEqual(pcbprof.validate_profile(prof), [])
            self.assertIsNotNone(prof.fab_preset)
            self.assertEqual(prof.fab_preset.finish, "HASL")
        finally:
            fabp._presets_path = orig


if __name__ == "__main__":
    unittest.main()
