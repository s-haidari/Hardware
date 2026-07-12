"""Design-rule presets (nd_design_presets): built-in severity schemes cover every
psm rule id, size templates carry coherent dimensions, and the user store
overrides/reverts like the fab-preset store."""
import os
import sys
import pathlib
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_design_presets as dp         # noqa: E402
import nd_project_settings_manager as psm   # noqa: E402


class SeveritySchemeTests(unittest.TestCase):
    def test_builtins_cover_every_rule_with_valid_levels(self):
        for name, sch in dp.BUILTIN_SEVERITY_SCHEMES.items():
            self.assertEqual(set(sch["drc"]), set(psm.DRC_RULE_IDS),
                             f"{name} DRC coverage")
            self.assertEqual(set(sch["erc"]), set(psm.ERC_RULE_IDS),
                             f"{name} ERC coverage")
            for lv in list(sch["drc"].values()) + list(sch["erc"].values()):
                self.assertIn(lv, psm.SEVERITY_LEVELS)

    def test_no_rule_id_typos(self):
        # every category id is a real psm rule id (no dead schemes)
        all_drc = (dp._DRC_CRITICAL + dp._DRC_MANUFACTURING + dp._DRC_FOOTPRINT
                   + dp._DRC_COSMETIC + dp._DRC_MISC)
        all_erc = (dp._ERC_ELECTRICAL + dp._ERC_CONNECTIVITY + dp._ERC_SYMBOL
                   + dp._ERC_STYLE + dp._ERC_MISC)
        self.assertEqual(set(all_drc), set(psm.DRC_RULE_IDS))
        self.assertEqual(set(all_erc), set(psm.ERC_RULE_IDS))
        # categories are disjoint (no rule counted twice)
        self.assertEqual(len(all_drc), len(set(all_drc)))
        self.assertEqual(len(all_erc), len(set(all_erc)))

    def test_strict_is_never_more_lenient_on_critical(self):
        crit = dp.BUILTIN_SEVERITY_SCHEMES["Strict"]["drc"]["clearance"]
        self.assertEqual(crit, "error")
        # relaxed still never silences a real short
        self.assertEqual(dp.BUILTIN_SEVERITY_SCHEMES["Relaxed"]["drc"]["shorting_items"], "error")

    def test_user_scheme_save_override_delete(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="dp_")) / "design_presets.json"
        dp.save_severity_scheme("MyStrict", {"clearance": "error", "bogus_rule": "error"},
                                {"pin_to_pin": "warning"}, path=tmp)
        got = dp.get_severity_scheme("MyStrict", path=tmp)
        self.assertEqual(got["drc"], {"clearance": "error"})   # bogus rule filtered out
        self.assertEqual(got["erc"], {"pin_to_pin": "warning"})
        # override a built-in then revert
        dp.save_severity_scheme("Strict", {"clearance": "ignore"}, {}, path=tmp)
        self.assertEqual(dp.get_severity_scheme("Strict", path=tmp)["drc"], {"clearance": "ignore"})
        self.assertTrue(dp.delete_severity_scheme("Strict", path=tmp))
        self.assertEqual(set(dp.get_severity_scheme("Strict", path=tmp)["drc"]),
                         set(psm.DRC_RULE_IDS))            # reverted to full built-in
        self.assertTrue(dp.is_builtin_scheme("Strict"))
        self.assertFalse(dp.is_builtin_scheme("MyStrict"))


class SizeTemplateTests(unittest.TestCase):
    def test_builtins_shape(self):
        for name, t in dp.BUILTIN_SIZE_TEMPLATES.items():
            self.assertTrue(all(len(r) == 1 for r in t["track"]), name)
            self.assertTrue(all(len(r) == 2 for r in t["via"]), name)
            self.assertTrue(all(len(r) == 3 for r in t["dp"]), name)
        # fine-pitch has diff pairs; power does not
        self.assertTrue(dp.BUILTIN_SIZE_TEMPLATES["Fine-Pitch"]["dp"])
        self.assertFalse(dp.BUILTIN_SIZE_TEMPLATES["Power"]["dp"])

    def test_load_returns_tuples(self):
        t = dp.get_size_template("Mixed")
        self.assertIsInstance(t["track"][0], tuple)
        self.assertIsInstance(t["via"][0], tuple)

    def test_user_template_save_and_drop_zero_rows(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="dp_")) / "design_presets.json"
        dp.save_size_template("MyT", track=[(0.2,), (0.0,)], via=[(0.6, 0.3), (0.0, 0.0)],
                              dp=[(0.2, 0.15, 0.25)], path=tmp)
        got = dp.get_size_template("MyT", path=tmp)
        self.assertEqual(got["track"], [(0.2,)])          # zero row dropped
        self.assertEqual(got["via"], [(0.6, 0.3)])        # absent-sentinel row dropped
        self.assertEqual(got["dp"], [(0.2, 0.15, 0.25)])
        self.assertTrue(dp.delete_size_template("MyT", path=tmp))
        self.assertIsNone(dp.get_size_template("MyT", path=tmp))


if __name__ == "__main__":
    unittest.main()
