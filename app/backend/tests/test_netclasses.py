"""
Netclass standard editor tests — load, edit a class, save, and confirm the
change persists while the file's header comments and meta survive (round-trip).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hwkit.netdeck import netclasses as nc

SAMPLE = """\
# KiCad V10 net-class palette for NETDECK.
# Source of truth: vault page "Net Class Colors & Styles".

meta:
  fab: "OSH Park 4-layer"
  units: mm

classes:
  - netclass: GND
    color: "#5E8AC7"
    clearance: 0.127
    members: ["*GND"]
  - netclass: SPI_SW
    color: "#2E9E93"
    clearance: 0.127
    members: ["*CARD_SW_*"]
"""


class NetclassRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "net-classes.yaml"
        self.path.write_text(SAMPLE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_classes(self):
        classes = nc.to_classes(nc.load(self.path))
        names = [c["netclass"] for c in classes]
        self.assertEqual(names, ["GND", "SPI_SW"])
        self.assertEqual(classes[1]["color"], "#2E9E93")

    def test_edit_color_persists_and_comments_survive(self):
        data = nc.load(self.path)
        classes = nc.to_classes(data)
        classes[1]["color"] = "#FF0000"          # recolor SPI_SW
        nc.replace_classes(data, classes)
        nc.save(self.path, data)

        text = self.path.read_text(encoding="utf-8")
        self.assertIn('# Source of truth: vault page', text)   # header comment survived
        self.assertIn("fab:", text)                            # meta survived
        reloaded = nc.to_classes(nc.load(self.path))
        self.assertEqual(reloaded[1]["color"], "#FF0000")
        self.assertEqual(reloaded[0]["netclass"], "GND")       # other class intact

    def test_add_a_class(self):
        data = nc.load(self.path)
        classes = nc.to_classes(data)
        classes.append({"netclass": "TRACE", "color": "#123456", "members": ["*TRACE*"]})
        nc.replace_classes(data, classes)
        nc.save(self.path, data)
        names = [c["netclass"] for c in nc.to_classes(nc.load(self.path))]
        self.assertEqual(names, ["GND", "SPI_SW", "TRACE"])


if __name__ == "__main__":
    unittest.main()
