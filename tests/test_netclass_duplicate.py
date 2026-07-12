"""NetClassManager.duplicate_netclass: fast variant creation for the Editor's
right-click Duplicate, without double-claiming member nets."""
import os
import sys
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_netclass_manager as ncm      # noqa: E402


class DuplicateNetClassTests(unittest.TestCase):
    def _mgr(self):
        m = ncm.NetClassManager()
        m.add_netclass(ncm.NetClass(
            name="HS", clearance=0.2, track_width=0.25, via_diameter=0.6,
            via_drill=0.3, line_style="dashed", priority=5,
            diff_pair_width=0.2, diff_pair_gap=0.15, patterns=["USB_*", "HDMI_*"]))
        return m

    def test_duplicate_copies_dimensions_clears_patterns(self):
        m = self._mgr()
        new = m.duplicate_netclass("HS")
        self.assertEqual(new, "HS_2")
        dup = m.get_netclass("HS_2")
        self.assertIsNotNone(dup)
        # dimensions + style + priority copied verbatim
        self.assertAlmostEqual(dup.clearance, 0.2)
        self.assertAlmostEqual(dup.track_width, 0.25)
        self.assertAlmostEqual(dup.via_diameter, 0.6)
        self.assertAlmostEqual(dup.diff_pair_width, 0.2)
        self.assertEqual(dup.line_style, "dashed")
        self.assertEqual(dup.priority, 5)
        # patterns are NOT copied (a variant assigns its own nets)
        self.assertEqual(dup.patterns, [])
        # the source is untouched
        self.assertEqual(m.get_netclass("HS").patterns, ["USB_*", "HDMI_*"])

    def test_duplicate_is_a_deep_copy(self):
        m = self._mgr()
        m.duplicate_netclass("HS")
        m.get_netclass("HS_2").clearance = 9.9
        self.assertAlmostEqual(m.get_netclass("HS").clearance, 0.2)   # source unaffected

    def test_repeated_duplicate_increments(self):
        m = self._mgr()
        self.assertEqual(m.duplicate_netclass("HS"), "HS_2")
        self.assertEqual(m.duplicate_netclass("HS"), "HS_3")
        self.assertEqual(m.duplicate_netclass("HS"), "HS_4")

    def test_duplicate_missing_returns_none(self):
        self.assertIsNone(self._mgr().duplicate_netclass("NOPE"))


if __name__ == "__main__":
    unittest.main()
