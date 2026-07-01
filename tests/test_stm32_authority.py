"""Tests for the Layer-B pinout authority (tools/stm32_authority.py)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import stm32_db as db  # noqa: E402
import stm32_authority as auth  # noqa: E402


class AuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls.dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, cls.dbp)
        cls.conn = db.connect(cls.dbp)

    def test_lqfp64_rollup_and_assignment(self):
        data = auth.build(self.conn, "LQFP64")
        r = data["rollup"]
        self.assertEqual(r["must_switch_count"], 11)
        self.assertEqual(r["cells_min"], 2)
        self.assertEqual(data["manifest"]["part_count"], 53)
        self.assertEqual(data["manifest"]["supported_families"],
                         ["STM32F0", "STM32F1", "STM32F2", "STM32F3", "STM32F4", "STM32F7"])
        p1 = next(p for p in data["positions"] if p["position"] == 1)
        self.assertIn("VBAT", p1["role_set"])
        self.assertIn("VDD", p1["role_set"])
        self.assertEqual(p1["switch_class"], db.SWITCH_MUST)
        self.assertEqual(p1["assignment"]["adg714"], {"cell": 1, "channel": 1, "destination": "VBAT_TGT"})
        p60 = next(p for p in data["positions"] if p["position"] == 60)
        self.assertTrue(p60["tags"]["is_boot"])

    def test_bootloader_tags_from_an2606(self):
        data = auth.build(self.conn, "LQFP64")

        def periph_at(name):
            for p in data["positions"]:
                if name in p["pin_names"]:
                    return p["tags"]["bootloader_periph"]
            return None

        self.assertIn("USART", periph_at("PA9"))      # USART1_TX, universal
        self.assertIn("USB-DFU", periph_at("PA11"))   # USB_DM, universal
        self.assertIn("CAN", periph_at("PB13"))       # CAN2_TX (F2/F4)
        self.assertTrue(any(p["tags"]["bootloader_periph"] for p in data["positions"]))

    def test_emit_yaml_json_tsv(self):
        out = Path(tempfile.mkdtemp())
        summ = auth.write_authority(self.conn, "LQFP64", out)
        self.assertEqual(len(summ["files"]), 3)
        j = json.loads((out / "pinout_authority_LQFP64.json").read_text(encoding="utf-8"))
        self.assertEqual(len(j["positions"]), 64)
        y = (out / "pinout_authority_LQFP64.yaml").read_text(encoding="utf-8")
        self.assertIn("must_switch_count: 11", y)
        tsv = (out / "pins_LQFP64.tsv").read_text(encoding="utf-8")
        self.assertGreater(len(tsv.splitlines()), 53)  # header + 53 parts x pins

    # ── extraction-access breakout (Layer B, orthogonal to switching) ──────
    @staticmethod
    def _pos(data, n):
        return next(p for p in data["positions"] if p["position"] == n)

    def test_switch_counts_unchanged_by_breakout(self):
        """The breakout layer derives from raw signals only; it must NOT move
        the verified switch decision (Build Card 7B/7C ground truth)."""
        self.assertEqual(auth.build(self.conn, "LQFP64")["rollup"]["must_switch_count"], 11)
        self.assertEqual(auth.build(self.conn, "LQFP100")["rollup"]["must_switch_count"], 43)

    def test_lqfp64_debug_breakout_positions(self):
        """SWD/JTAG break out on the vault ground-truth sockets (Card 7B + 5E)."""
        d = auth.build(self.conn, "LQFP64")
        for pos, net in {46: "SWDIO_PARENT", 49: "SWCLK_PARENT", 55: "SWO_PARENT",
                         50: "TDI_PARENT", 56: "NTRST_PARENT"}.items():
            self.assertIn(net, self._pos(d, pos)["breakout"]["service_nets"],
                          f"LQFP64 pin {pos} should break out {net}")

    def test_lqfp100_jtag_positions(self):
        """Card 5E: target PA15/PB4 = LQFP100 socket 77/90."""
        d = auth.build(self.conn, "LQFP100")
        self.assertIn("TDI_PARENT", self._pos(d, 77)["breakout"]["service_nets"])
        self.assertIn("NTRST_PARENT", self._pos(d, 90)["breakout"]["service_nets"])

    def test_coresight20_map_lqfp64(self):
        """CoreSight-20 header pins resolve to the target sockets (Card 5E)."""
        ea = auth.build(self.conn, "LQFP64")["extraction_access"]
        cs = {c["hdr_pin"]: c for c in ea["coresight20"]}
        self.assertEqual((cs[2]["net"], cs[2]["target_pos"]), ("SWDIO_PARENT", 46))
        self.assertEqual((cs[4]["net"], cs[4]["target_pos"]), ("SWCLK_PARENT", 49))
        self.assertEqual((cs[6]["net"], cs[6]["target_pos"]), ("SWO_PARENT", 55))
        self.assertEqual((cs[8]["net"], cs[8]["target_pos"]), ("TDI_PARENT", 50))
        self.assertEqual((cs[10]["net"], cs[10]["target_pos"]), ("SERVICE_NRST", 7))
        self.assertEqual((cs[14]["net"], cs[14]["target_pos"]), ("NTRST_PARENT", 56))
        self.assertIsNone(cs[7]["target_pos"])   # pin 7 = KEY, no net

    def test_boot_uart_and_usb_dfu(self):
        """AN2606 universal boot buses: USART1 (PA9/PA10, TX<->RX crossover) and
        USB-DFU (PA11/PA12)."""
        d = auth.build(self.conn, "LQFP64")
        pa9 = next(p for p in d["positions"] if "PA9" in p["pin_names"])
        pa10 = next(p for p in d["positions"] if "PA10" in p["pin_names"])
        self.assertIn("UART_BOOT_RX", pa9["breakout"]["service_nets"])   # target TX
        self.assertIn("UART_BOOT_TX", pa10["breakout"]["service_nets"])  # target RX
        ea = d["extraction_access"]
        self.assertIsNotNone(ea["bootloader_uart"]["tx_pos"])
        self.assertIsNotNone(ea["usb_dfu"]["dp_pos"])
        self.assertIsNotNone(ea["usb_dfu"]["dn_pos"])

    def test_electrical_from_datasheets(self):
        """Per-family I/O limits from the fetched ST datasheets (open Q#1 closed)."""
        e = auth.build(self.conn, "LQFP64")["electrical"]
        self.assertEqual(e["max_io_current_ma"], 25)        # ±25 mA uniform F0–F7
        self.assertEqual(e["injection_current_ma"], 5)      # ±5 mA per pin
        self.assertTrue(e["ft_5v_tolerant"])
        self.assertIsNotNone(e["vdda_range_v"])
        # per-family total-I/O ceiling differs (F0/F3=80, F1=150, F2=120, F4=240, F7=120)
        tot = e["total_io_current_ma"]
        self.assertEqual(tot["STM32F0"], 80)
        self.assertEqual(tot["STM32F1"], 150)
        self.assertEqual(tot["STM32F4"], 240)
        self.assertIn("DS", e["by_family"]["STM32F7"]["ds"])   # carries a datasheet cite
        self.assertEqual(auth.FAMILY_ELECTRICAL["STM32F7"]["vdd_v"], [1.7, 3.6])

    def test_trace_captured_and_vssa_relabelled(self):
        d = auth.build(self.conn, "LQFP64")
        trace = d["extraction_access"]["trace_positions"]
        self.assertTrue(trace, "LQFP64 should detect parallel-trace positions")
        self.assertTrue(self._pos(d, trace[0])["tags"]["is_trace"])
        # Analog ground routes to its own rail net, not GND (Card 7B: VSSA = 12).
        a12 = self._pos(d, 12)["assignment"]
        self.assertEqual(a12.get("net", a12.get("destination")), "VSSA_TGT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
