"""Seed From Fab Preset seam (projects.py seed_design_rules): a custom (non-builtin)
fab preset must seed the FULL design-rule ruleset onto ProjectSettings, covering every
_DR_FIELDS attr the Editor spinners expose — not just the five NETCLASS floors."""
import os
import sys
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_fab_presets as fabp                             # noqa: E402
import nd_netclass_manager as ncm                         # noqa: E402
from nd_project_settings_manager import ProjectSettings   # noqa: E402
from ui.features.projects import _DR_FIELDS, _DR_SEED     # noqa: E402


def _custom(name="Custom Fab House"):
    """A pure user preset (not OSH Park) with values distinct from the built-ins so a
    correct full seed produces figures no NETCLASS floor would."""
    return fabp.FabPreset(
        name=name, layers=4,
        min_track_width=0.0889, min_clearance=0.1016, min_drill=0.2032,
        min_annular_ring=0.127, min_edge_clearance=0.508,
        default_track_width=0.2032, default_via_diameter=0.508, default_via_drill=0.254,
        board_thickness_mm=1.6, copper_oz=1.0, material="FR-4", finish="HASL",
        soldermask="green",
        stackup=(("F.Cu", "copper", 0.035, "copper"),
                 ("core", "core", 1.5, "FR-4"),
                 ("B.Cu", "copper", 0.035, "copper")))


class FabSeedTests(unittest.TestCase):
    def test_custom_preset_covers_all_dr_fields(self):
        preset = _custom()
        self.assertFalse(fabp.is_builtin(preset.name))     # custom, not OSH Park
        seeded = fabp.apply_to_project_settings(ProjectSettings(), preset)
        # Every spinner attr the Editor exposes must be populated by the seed.
        for _label, attr in _DR_FIELDS:
            self.assertTrue(hasattr(seeded, attr), attr)
            self.assertGreater(getattr(seeded, attr), 0.0, attr)

    def test_seed_differs_from_netclass_default_fallback(self):
        """The whole point of the seam: a custom preset seeds figures the old
        NETCLASS_PROFILES fallback could not have produced for these fields."""
        preset = _custom()
        seeded = fabp.apply_to_project_settings(ProjectSettings(), preset)
        floor = ncm.NETCLASS_PROFILES[ncm.DEFAULT_NETCLASS_PROFILE]
        differ = 0
        for attr, key in _DR_SEED.items():
            if key in floor and abs(getattr(seeded, attr) - float(floor[key])) > 1e-6:
                differ += 1
        self.assertGreater(differ, 0)

    def test_seed_full_ruleset_is_wider_than_netclass_floors(self):
        """_DR_FIELDS (the full spinner set) is strictly larger than the five
        NETCLASS floors the fallback seeded, so the full seed reaches fields the
        fallback never touched (e.g. hole-to-hole, microvia, edge clearance)."""
        dr_attrs = {attr for _label, attr in _DR_FIELDS}
        self.assertTrue(set(_DR_SEED).issubset(dr_attrs))
        self.assertGreater(len(dr_attrs), len(_DR_SEED))
        preset = _custom()
        seeded = fabp.apply_to_project_settings(ProjectSettings(), preset)
        # Fields the fallback never seeded are nonetheless populated by the full seed.
        for attr in dr_attrs - set(_DR_SEED):
            self.assertGreater(getattr(seeded, attr), 0.0, attr)


if __name__ == "__main__":
    unittest.main()
