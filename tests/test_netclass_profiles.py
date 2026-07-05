"""Net-class profiles (nd_netclass_manager): the vault standard generated against a
fab profile, valid on either OSH Park service and covering every vault net."""
import os
import sys
import pathlib
import fnmatch
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_netclass_manager as ncm      # noqa: E402


def _classify(mgr, net):
    for name in mgr.list_netclasses():
        for pat in (mgr.get_netclass(name).patterns or []):
            if fnmatch.fnmatch(net, pat):
                return name
    return "Default"


class NetClassProfileTests(unittest.TestCase):
    def test_two_profiles(self):
        self.assertEqual(set(ncm.netclass_profiles()),
                         {"OSH Park 2-layer", "OSH Park 4-layer"})

    def test_profiles_apply_the_right_floor(self):
        m4 = ncm.create_vault_standard_template("OSH Park 4-layer")
        m2 = ncm.create_vault_standard_template("OSH Park 2-layer")
        self.assertEqual(len(m4.list_netclasses()), len(m2.list_netclasses()))
        lane4, lane2 = m4.get_netclass("LANE"), m2.get_netclass("LANE")
        # 4-layer: 5 mil floor (0.127 clr, 0.4572 via); 2-layer: 6 mil (0.1524, 0.508)
        self.assertAlmostEqual(lane4.clearance, 0.127)
        self.assertAlmostEqual(lane4.via_diameter, 0.4572)
        self.assertAlmostEqual(lane2.clearance, 0.1524)
        self.assertAlmostEqual(lane2.track_width, 0.1524)
        self.assertAlmostEqual(lane2.via_diameter, 0.508)

    def test_every_class_respects_the_profile_min_clearance(self):
        for prof, floor in (("OSH Park 4-layer", 0.127), ("OSH Park 2-layer", 0.1524)):
            m = ncm.create_vault_standard_template(prof)
            for name in m.list_netclasses():
                self.assertGreaterEqual(m.get_netclass(name).clearance, floor - 1e-9,
                                        f"{prof}/{name} clearance below fab floor")

    def test_covers_every_stm32_authority_net(self):
        m = ncm.create_vault_standard_template()
        nets = ["GND", "VSSA_TGT", "VTARGET", "VDDA_TGT", "VREF_TGT", "VBAT_TGT",
                "VCAP_NODE", "VCAP_DSI_NODE", "SERVICE_OSC_IN", "SERVICE_OSC_OUT",
                "SERVICE_NRST", "SERVICE_BOOT0", "UART_BOOT_TX", "UART_BOOT_RX",
                "USB_DP_TGT", "USB_DN_TGT", "SWDIO_PARENT", "SWCLK_PARENT",
                "SWO_PARENT", "TDI_PARENT", "NTRST_PARENT", "CARD_LANE_001"]
        for net in nets:
            self.assertNotEqual(_classify(m, net), "Default",
                                f"{net} falls through to Default (uncovered)")
        # the specific gaps that were missing before
        self.assertEqual(_classify(m, "VCAP_NODE"), "TGT_CORE")
        self.assertEqual(_classify(m, "VCAP_DSI_NODE"), "TGT_CORE")
        self.assertEqual(_classify(m, "TDI_PARENT"), "SWD")
        self.assertEqual(_classify(m, "NTRST_PARENT"), "SWD")

    def test_all_netclasses_have_proper_values(self):
        for prof in ncm.netclass_profiles():
            m = ncm.create_vault_standard_template(prof)
            self.assertEqual(ncm.validate_netclasses(m, prof), [],
                             f"{prof} has improper net-class values")

    def test_validator_catches_bad_values(self):
        m = ncm.create_vault_standard_template("OSH Park 2-layer")
        m.get_netclass("LANE").clearance = 0.05          # below 2-layer 6 mil floor
        m.get_netclass("SWD").via_drill = 0.9            # drill >= via
        issues = ncm.validate_netclasses(m, "OSH Park 2-layer")
        names = {i["netclass"] for i in issues}
        self.assertIn("LANE", names)
        self.assertIn("SWD", names)

    def test_profile_round_trips_through_save_load(self):
        m = ncm.create_vault_standard_template("OSH Park 2-layer")
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "profile.json"
            m.export_template(f)
            m2 = ncm.NetClassManager()
            m2.import_template(f)
            self.assertEqual(set(m2.list_netclasses()), set(m.list_netclasses()))
            self.assertAlmostEqual(m2.get_netclass("LANE").clearance, 0.1524)


if __name__ == "__main__":
    unittest.main(verbosity=2)
