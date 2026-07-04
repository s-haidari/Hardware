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
        self.assertEqual(r["channel_count"], 11)   # one channel per must-switch pin (Card 7B)
        p1 = next(p for p in data["positions"] if p["position"] == 1)
        self.assertIn("VBAT", p1["role_set"])
        self.assertIn("VDD", p1["role_set"])
        self.assertEqual(p1["switch_class"], db.SWITCH_MUST)
        # one channel per pin -> its dominant rail (VBAT); IO alternate stays hardwired
        self.assertEqual([c["destination"] for c in p1["assignment"]["channels"]], ["VBAT_TGT"])
        self.assertEqual(p1["assignment"]["adg714"]["destination"], "VBAT_TGT")
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
        self.assertEqual(len(summ["files"]), 10)  # + switchmap json/h + wiring md + pin-map svg
        j = json.loads((out / "pinout_authority_LQFP64.json").read_text(encoding="utf-8"))
        self.assertEqual(len(j["positions"]), 64)
        y = (out / "pinout_authority_LQFP64.yaml").read_text(encoding="utf-8")
        self.assertIn("must_switch_count: 11", y)
        tsv = (out / "pins_LQFP64.tsv").read_text(encoding="utf-8")
        self.assertGreater(len(tsv.splitlines()), 53)  # header + 53 parts x pins
        self.assertTrue((out / "LQFP64_socket.kicad_sym").exists())
        csv_lines = (out / "pins_LQFP64.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(csv_lines) - 1, 64)       # one self-contained row per pin
        self.assertIn("ADG714BRUZ-REEL", (out / "authority_LQFP64.md").read_text(encoding="utf-8"))

    def test_kicad_symbol(self):
        """Phase D: the generated KiCad socket symbol is well-formed (structural —
        kicad-cli can't load-validate headlessly here, but the format is spec v6)."""
        sym = auth.to_kicad_symbol(auth.build(self.conn, "LQFP64"))
        self.assertTrue(sym.startswith("(kicad_symbol_lib"))
        self.assertEqual(sym.count("("), sym.count(")"))       # balanced S-expr
        self.assertEqual(sym.count("(pin "), 64)               # one pin per socket
        self.assertIn("LQFP-64_10x10mm", sym)                  # stock footprint referenced
        self.assertIn('name "VBAT_TGT"', sym)                  # pin 1 destination net
        self.assertNotIn("00000000", sym)                      # coordinates rounded clean
        self.assertEqual(auth.to_kicad_symbol(auth.build(self.conn, "LQFP100")).count("(pin "), 100)

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
        # total-I/O = ΣI_IO where explicit (F0/F3/F4/F7) else supply (F1/F2)
        tot = e["total_io_current_ma"]
        self.assertEqual(tot["STM32F0"], 80)
        self.assertEqual(tot["STM32F1"], 150)
        self.assertEqual(tot["STM32F4"], 120)   # ΣI_IO (the 240 was the *supply* total)
        self.assertEqual(e["by_family"]["STM32F4"]["metric"], "sigma_io")
        self.assertEqual(e["supply_total_ma"]["STM32F4"], 240)
        self.assertIn("DS", e["by_family"]["STM32F7"]["ds"])   # carries a datasheet cite
        self.assertEqual(auth.FAMILY_ELECTRICAL["STM32F7"]["vdd_v"], [1.7, 3.6])

    def test_power_bootloader_and_f4_sublines(self):
        """Phase A: FAMILY_POWER (VCAP/VBAT/VREF), exhaustive AN2606 bootloader,
        and the verified F4 sub-line supply totals."""
        e = auth.build(self.conn, "LQFP64")["electrical"]
        # VCAP: F2/F4/F7 need it, F0/F1/F3 don't
        self.assertTrue(e["vcap_required"])                      # F2/F4/F7 present
        self.assertTrue(e["power"]["STM32F4"]["vcap"])
        self.assertIn("2.2", e["power"]["STM32F4"]["vcap_value"])
        self.assertFalse(e["power"]["STM32F0"]["vcap"])
        self.assertEqual(e["vbat_range_v"], [1.65, 3.6])
        self.assertIsNotNone(e["vref_range_v"])
        # F4 sub-line supply totals (F401/F411 verified, retires the ~150 guess)
        self.assertEqual(e["f4_subline_supply_ma"]["STM32F401"], 160)
        self.assertEqual(e["f4_subline_supply_ma"]["STM32F469"], 290)
        # exhaustive AN2606 bootloader: F7 has both CAN buses; F3 I2C3; F4 SPI
        self.assertEqual(auth.BOOTLOADER_PINS["STM32F7"]["CAN"], {"PB5", "PB13", "PD0", "PD1"})
        self.assertIn("PB5", auth.BOOTLOADER_PINS["STM32F3"]["I2C"])   # I2C3 PA8/PB5
        self.assertIn("PA15", auth.BOOTLOADER_PINS["STM32F4"]["SPI"])  # SPI3 NSS

    def test_card_materials_and_drift_gate(self):
        """Phase B: the card passive BOM + the card-vs-authority drift-gate linter."""
        a = auth.build(self.conn, "LQFP64")
        cm = a["card_materials"]
        self.assertEqual(cm["adg714_cells"], 2)                # 11 must-switch -> 2 cells
        self.assertEqual(cm["vcap_required_families"], ["STM32F2", "STM32F4", "STM32F7"])
        self.assertTrue(any("SPST" in i["part"] for i in cm["items"]))   # app does not name the part
        self.assertTrue(any("2.2uF" in i["part"] for i in cm["items"]))    # VCAP caps
        # drift gate: a correct card passes; the classic SWCLK/cell drift fails
        good = auth.lint_card(a, {"must_switch_count": 11, "adg714_cells": 2, "swclk_pos": 49})
        self.assertTrue(all(f["ok"] for f in good))
        bad = auth.lint_card(a, {"swclk_pos": 76, "adg714_cells": 8})
        self.assertFalse(any(f["ok"] for f in bad))
        self.assertEqual({f["field"]: f["actual"] for f in bad}, {"swclk_pos": 49, "adg714_cells": 2})

    def test_tab_detail_helpers(self):
        """Phase C: the tab's pure HTML helpers (skip if PyQt5 unavailable)."""
        try:
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"stm32_pins_tab (PyQt5) unavailable: {e}")
        a = auth.build(self.conn, "LQFP64")
        s = tab._summary_html(a)
        self.assertIn("Card materials", s)
        self.assertIn("2.2uF", s)                    # VCAP shown in the summary
        self.assertIn("VCAP required", s)
        p46 = next(p for p in a["positions"] if p["position"] == 46)
        self.assertIn("SWDIO_PARENT", tab._pin_detail_html(p46))
        pa0 = next(p for p in a["positions"] if "PA0" in p["pin_names"])
        self.assertIn("part-dependent", tab._pin_detail_html(pa0))
        # pin-map geometry (shared by the Qt widget AND the SVG export)
        g = tab.pin_map_geometry(a["positions"], 460, 460)
        self.assertEqual(len(g["pins"]), 64)
        self.assertEqual(len({p["side"] for p in g["pins"]}), 4)   # all 4 QFP sides used
        self.assertTrue(all(len(p["rect"]) == 4 for p in g["pins"]))
        self.assertEqual(tab.pin_map_svg(a, 400, 400).count("<rect") >= 64, True)

    def test_five_v_tolerance_per_family(self):
        """Per-pin 5V-tolerance from the datasheet I/O-structure column, incl. the
        part-dependent analog pins (PA0 FT on F2/F4/F7, not on F0/F1/F3)."""
        self.assertEqual(auth.FAMILY_NOT_5V["STM32F2"], {"PA4", "PA5"})
        self.assertIn("PB10", auth.FAMILY_NOT_5V["STM32F3"])   # F3's larger TTa set
        d = auth.build(self.conn, "LQFP64")

        def fv(name):
            p = next(x for x in d["positions"] if name in x["pin_names"])
            return p["five_v"]

        self.assertTrue(fv("PA13")["tolerant"])                # SWDIO, FT on all parts
        self.assertTrue(all(fv("PA13")["by_family"].values()))
        self.assertFalse(fv("PA4")["tolerant"])                # DAC/TTa, never 5V-tol
        self.assertFalse(any(fv("PA4")["by_family"].values()))
        pa0 = fv("PA0")                                        # family-dependent
        self.assertFalse(pa0["tolerant"])                      # conservative
        self.assertFalse(pa0["by_family"]["STM32F1"])
        self.assertTrue(pa0["by_family"]["STM32F4"])
        vbat = next(x for x in d["positions"] if x["position"] == 1)
        self.assertIsNone(vbat["five_v"])                      # non-GPIO
        summ = d["electrical"]["five_v_positions"]
        self.assertEqual(summ["not_tolerant_any_part"], 2)     # PA4, PA5
        self.assertGreater(summ["family_dependent"], 0)

    def test_trace_captured_and_vssa_relabelled(self):
        d = auth.build(self.conn, "LQFP64")
        trace = d["extraction_access"]["trace_positions"]
        self.assertTrue(trace, "LQFP64 should detect parallel-trace positions")
        self.assertTrue(self._pos(d, trace[0])["tags"]["is_trace"])
        # Analog ground routes to its own rail net, not GND (Card 7B: VSSA = 12).
        a12 = self._pos(d, 12)["assignment"]
        self.assertEqual(a12.get("net", a12.get("destination")), "VSSA_TGT")

    # ── self-contained reporting layer (legible without the vault) ─────────
    def test_category_lists_match_rollup(self):
        a = auth.build(self.conn, "LQFP64")
        cats = auth.category_lists(a)
        r = a["rollup"]
        self.assertEqual(len(cats["must_switch"]), r["must_switch_count"])
        self.assertEqual(len(cats["osc_optional"]), r["osc_optional_count"])
        self.assertEqual(len(cats["fixed"]), r["fixed_count"])
        # power/analog/boot rails switch; the SWD sockets break out (not switched)
        self.assertIn(1, cats["must_switch"])          # VBAT/VDD
        self.assertIn(60, cats["must_switch"])         # BOOT0
        self.assertNotIn(46, cats["must_switch"])      # SWDIO -> breakout, not switched
        self.assertIn(46, cats["breakout"])
        self.assertEqual(cats["five_v_never"], [20, 21])   # PA4, PA5

    def test_switch_rationale(self):
        a = auth.build(self.conn, "LQFP64")
        self.assertIn("VBAT_TGT", auth.switch_rationale(self._pos(a, 1)))
        self.assertIn("SERVICE_BOOT0", auth.switch_rationale(self._pos(a, 60)))
        self.assertIn("crystal", auth.switch_rationale(self._pos(a, 5)))   # osc-optional
        fixed = next(p for p in a["positions"] if p["switch_class"] == db.SWITCH_NONE)
        self.assertEqual(auth.switch_rationale(fixed), "")                  # fixed = no why

    def test_adg714_cell_map_symbol_names(self):
        a = auth.build(self.conn, "LQFP64")
        cm = auth.adg714_cell_map(a)
        self.assertEqual(len(cm), a["rollup"]["cells_min"])   # 11 must-switch -> 2 cells
        self.assertEqual((cm[0]["symbol"], cm[0]["footprint"]), ("ADG714BRUZ-REEL", "RU_24_ADI"))
        for cell in cm:                                       # exact Sn/Dn terminal names
            self.assertEqual(len(cell["switches"]), 8)
            for i, sw in enumerate(cell["switches"], start=1):
                self.assertEqual((sw["s_pin"], sw["d_pin"]), (f"S{i}", f"D{i}"))
        sw1 = cm[0]["switches"][0]                            # cell1/ch1 = VBAT (pin 1)
        self.assertEqual((sw1["position"], sw1["destination"]), (1, "VBAT_TGT"))
        spares = [sw for cell in cm for sw in cell["switches"] if sw["spare"]]
        self.assertEqual(len(spares), 2 * 8 - a["rollup"]["channel_count"])   # 16 - 11 = 5
        # LQFP100 as built (Card 7C / spec locked 2026-06-30): one channel per
        # non-IO role of every switched pin, paralleled to 59 channels / 8 cells;
        # cells_min stays ceil(43/8) = 6.
        lq100 = auth.build(self.conn, "LQFP100")
        self.assertEqual(lq100["rollup"]["channel_count"], 59)
        self.assertEqual(lq100["rollup"]["cells_min"], 6)
        self.assertEqual(lq100["rollup"]["cells_as_built"], 8)
        self.assertEqual(len(auth.adg714_cell_map(lq100)), 8)

    def test_as_built_rail_multiset_matches_card_7c(self):
        """The DB-derived LQFP100 channel map reproduces Card 7C's as-built rail
        multiset count-for-count — the 'derived, never hand-authored' goal."""
        from collections import Counter
        a = auth.build(self.conn, "LQFP100")
        rails = Counter()
        for p in a["positions"]:
            for ch in p["assignment"].get("channels", []):
                rails[ch["destination"]] += 1
        self.assertEqual(dict(rails), {
            "VTARGET": 15, "GND": 12, "VREF_TGT": 6, "VCAP_NODE": 6,
            "SERVICE_OSC_IN": 4, "SERVICE_OSC_OUT": 4, "VSSA_TGT": 3,
            "VDDA_TGT": 3, "VBAT_TGT": 2, "SERVICE_NRST": 2, "SERVICE_BOOT0": 2})
        # LQFP64 stays the Card 7B single-branch map, byte-stable: 30/31/47 on the
        # VCAP node (an open channel already serves their VSS variant via the lane).
        a64 = auth.build(self.conn, "LQFP64")
        w64 = auth.card_wiring(a64)
        self.assertEqual([(c["socket_pin"], c["rail"]) for c in w64["channels"]], [
            (1, "VBAT_TGT"), (13, "VDDA_TGT"), (17, "VREF_TGT"), (18, "GND"),
            (19, "VTARGET"), (30, "VCAP_NODE"), (31, "VCAP_NODE"), (33, "VREF_TGT"),
            (47, "VCAP_NODE"), (48, "VTARGET"), (60, "SERVICE_BOOT0")])

    def test_osc_sides_and_pin100_invariant(self):
        """Oscillator destinations follow the pin's HSE side (split service nets),
        and LQFP100 pin 100 is hardwired VTARGET — never a switch channel (LOCKED)."""
        a64 = auth.build(self.conn, "LQFP64")
        self.assertEqual(self._pos(a64, 5)["assignment"]["destination"], "SERVICE_OSC_IN")
        self.assertEqual(self._pos(a64, 6)["assignment"]["destination"], "SERVICE_OSC_OUT")
        self.assertFalse(self._pos(a64, 5)["assignment"].get("channels"))  # 7B: osc unwired
        a100 = auth.build(self.conn, "LQFP100")
        p100 = self._pos(a100, 100)
        self.assertEqual(p100["switch_class"], db.SWITCH_NONE)
        self.assertFalse(p100["assignment"].get("channels"))
        self.assertEqual(p100["assignment"].get("net"), "VTARGET")
        # osc pins carry BOTH service nets as-built (7C: 4x IN + 4x OUT), one-hot
        osc = self._pos(a100, 12)
        dests = {c["destination"] for c in osc["assignment"]["channels"]}
        self.assertEqual(dests, {"SERVICE_OSC_IN", "SERVICE_OSC_OUT"})
        self.assertTrue(all(c["exclusive_group"] == 12 for c in osc["assignment"]["channels"]))

    def test_switchmap_carries_vssa_spares_and_lanes(self):
        """The firmware exports carry the VSSA_TGT rail (Connector Contract contact 24),
        the spare channels, and per-PIN lane numbering (001..043 must, 044..046 osc)."""
        a = auth.build(self.conn, "LQFP100")
        hdr = auth.to_switchmap_c(a)
        self.assertIn("RAIL_VSSA_TGT", hdr)
        self.assertIn("RAIL_SERVICE_OSC_OUT", hdr)
        self.assertIn("#define NETDECK_LQFP100_CELLS 8", hdr)
        self.assertIn("#define NETDECK_LQFP100_CHANNELS_USED 59", hdr)
        self.assertIn("#define NETDECK_LQFP100_CHANNELS_SPARE 5", hdr)
        w = auth.card_wiring(a)
        self.assertEqual(len(w["spare_channels"]), 5)
        self.assertTrue(w["exclusive_groups"])                    # one-hot groups present
        # 7C lane policy: every pin owns its pin-numbered lane on the frozen
        # Connector Contract rows (001..060 -> LA even 2N; 061..120 -> RA odd)
        self.assertEqual(w["lane_policy"], "by_pin")
        osc12 = next(c for c in w["channels"] if c["socket_pin"] == 12)
        self.assertEqual(osc12["card_lane"], "CARD_LANE_012")
        self.assertEqual(osc12["lane_contact"], "LA-24")          # even row, 2 x 12
        self.assertEqual(osc12["cell_refdes"], "U_SW_L100_2")     # ascending-pin packing
        self.assertEqual(w["socket_refdes"], "XU_TGT100_1")
        self.assertEqual(w["edge_refdes"], "J_EDGE_L100_1")
        # a pin's branches share ONE lane
        p19 = {c["card_lane"] for c in w["channels"] if c["socket_pin"] == 19}
        self.assertEqual(p19, {"CARD_LANE_019"})
        # plain GPIO on 7C rides its numbered lane through the 33 R series R
        sc = {c["pin"]: c for c in auth.socket_connections(a)}
        gpio = sc[15]                                             # PC0 — plain IO
        self.assertEqual((gpio["kind"], gpio["dest"], gpio["contact"]),
                         ("resistor", "CARD_LANE_015", "LA-30"))
        # 7B keeps its own card policy: sequential switch-only lanes (pin 13 ->
        # CARD_LANE_002 per the build card) and DIRECT non-switched routes
        a64 = auth.build(self.conn, "LQFP64")
        w64 = auth.card_wiring(a64)
        self.assertEqual(w64["lane_policy"], "sequential")
        c13 = next(c for c in w64["channels"] if c["socket_pin"] == 13)
        self.assertEqual(c13["card_lane"], "CARD_LANE_002")
        self.assertEqual(c13["cell_refdes"], "U_SW_64_1")
        sc64 = {c["pin"]: c for c in auth.socket_connections(a64)}
        self.assertEqual(sc64[2]["kind"], "direct")               # no series R on 7B
        self.assertEqual(sc64[2]["dest"], "CARD_LANE")

    def test_claims_files_and_drift_gate(self):
        """The checked-in claims files pass the drift gate, and a wrong claim FAILS
        it — the enforcement half of the pinout-authority spec."""
        claims_dir = Path(__file__).resolve().parent.parent / "tools" / "claims"
        files = sorted(claims_dir.glob("claims_*.yaml"))
        self.assertEqual(len(files), 2)
        ok, lines = auth.run_lint(self.conn, files)
        self.assertTrue(ok, "\n".join(lines))
        self.assertTrue(any("adg714_cells: claimed 8, actual 8" in ln for ln in lines))
        # a deliberately wrong claim must be caught
        bad = Path(tempfile.mkdtemp()) / "claims_bad.yaml"
        bad.write_text("package: LQFP100\nclaims:\n  adg714_cells: 6\n", encoding="utf-8")
        ok2, lines2 = auth.run_lint(self.conn, [bad])
        self.assertFalse(ok2)
        self.assertTrue(any("DRIFT" in ln for ln in lines2))

    def test_locked_constants_and_provenance(self):
        """Pin the vault-locked wiring constants and the manifest's DB provenance."""
        self.assertEqual(auth.ADG714_TERMINAL_PIN["S1"], 5)
        self.assertEqual(auth.ADG714_TERMINAL_PIN["D1"], 6)
        self.assertEqual(auth.ADG714_TERMINAL_PIN["D5"], 13)      # non-sequential half
        self.assertEqual(auth.ADG714_TERMINAL_PIN["S5"], 14)
        self.assertEqual(auth.ADG714_TERMINAL_PIN["S8"], 20)
        self.assertEqual(auth.RAIL_CONTACT["VBAT_TGT"], ["LA-33"])
        self.assertEqual(auth.RAIL_CONTACT["VDDA_TGT"], ["RA-20"])
        self.assertEqual(auth.RAIL_CONTACT["VREF_TGT"], ["RA-22"])
        self.assertEqual(auth.RAIL_CONTACT["VSSA_TGT"], ["RA-24"])
        self.assertEqual(auth.RAIL_CONTACT["VTARGET"], ["RA-16", "RA-18"])
        self.assertEqual(auth.RAIL_CONTACT["SERVICE_OSC_IN"], ["RA-10"])
        self.assertEqual(auth.RAIL_CONTACT["SERVICE_OSC_OUT"], ["RA-12"])
        bus = {s: (p, c) for s, p, c, _ in auth.ADG714_BUS}
        self.assertEqual(bus["SCLK"], (1, "LA-9"))
        self.assertEqual(bus["DIN"], (3, "LA-11"))
        self.assertEqual(bus["DOUT"], (22, "LA-13"))
        a = auth.build(self.conn, "LQFP64")
        m = a["manifest"]
        self.assertTrue(m["db_imported_at"])                      # DB origin + rev (spec)
        self.assertTrue(m["db_source_path"])
        self.assertEqual(m["channel_policy"], "dominant")
        self.assertEqual(a["schema_version"], 4)
        # files carry electrical once, top-level only (no 100x duplication)
        slim = auth.serializable(a)
        self.assertNotIn("electrical", slim["positions"][0])
        self.assertIn("electrical", slim)

    def test_csv_and_markdown_export(self):
        a = auth.build(self.conn, "LQFP64")
        lines = auth.to_csv(a).splitlines()
        self.assertEqual(lines[0].split(",")[0], "position")
        self.assertIn("why", lines[0])
        self.assertEqual(len(lines) - 1, len(a["positions"]))     # one row per pin
        self.assertTrue(any(",S1,D1," in ln for ln in lines))     # real ADG714 terminals
        md = auth.to_markdown(a)
        self.assertIn("ADG714BRUZ-REEL", md)
        self.assertIn(f"Must-switch ({a['rollup']['must_switch_count']})", md)
        self.assertIn("| SW1 | S1/D1 |", md)                      # cell-map table
        self.assertIn("VBAT_TGT", md)

    def test_card_wiring_switchmap(self):
        """Ultra-specific terminal wiring + firmware switch-map, mapped onto the vault's
        Connector Contract (IC51 ZIF socket, QSH/QTH contacts, ADG714 S/D pins)."""
        a = auth.build(self.conn, "LQFP64")
        w = auth.card_wiring(a)
        self.assertEqual(len(w["channels"]), 11)                 # matches the vault card
        self.assertEqual(w["zif_socket"], "Yamaichi IC51-0644-807")
        c1 = w["channels"][0]                                    # cell 1 ch 1 = VBAT
        self.assertEqual((c1["cell"], c1["channel"], c1["socket_pin"], c1["rail"]),
                         (1, 1, 1, "VBAT_TGT"))
        self.assertEqual((c1["s_pin"], c1["s_pin_num"], c1["d_pin"], c1["d_pin_num"]),
                         ("S1", 5, "D1", 6))
        self.assertEqual(c1["connector_contacts"], ["LA-33"])     # left connector, contact 33
        self.assertEqual(c1["card_lane"], "CARD_LANE_001")
        self.assertEqual((w["daisy_chain"]["head_din_contact"],
                          w["daisy_chain"]["tail_dout_contact"]), ("LA-11", "LA-13"))
        json.loads(auth.to_switchmap_json(a))                    # valid JSON
        self.assertIn("NETDECK_LQFP64_CHANNELS", auth.to_switchmap_c(a))
        self.assertIn("via Samtec QTH", auth.to_wiring_md(a))

    def test_tab_widget_offscreen(self):
        """Headless Qt widget (offscreen platform): 9 columns, numeric Pin sort +
        sort-safe row->pin selection, category filters, peripheral highlight, and the
        switch-fabric map in the BOM view. Guards the widget wiring (esp. numeric
        sort) that the pure-function tests can't reach."""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt
            import stm32_pins_tab as tab
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"PyQt5 unavailable: {e}")
        _app = QApplication.instance() or QApplication([])
        w = tab.Stm32PinsWidget()
        w.db_path = self.dbp                         # use the test's temp database
        w.load("LQFP64")
        self.assertEqual((w.table.columnCount(), w.table.rowCount()), (9, 64))
        w.table.sortItems(0, Qt.DescendingOrder)
        self.assertEqual(w.table.item(0, 0).data(Qt.UserRole), 64)    # numeric, not "9"
        w.table.selectRow(0)
        self.assertEqual(w._sel_pos, 64)                              # selection follows sort
        w.filter_combo.setCurrentText("Must-Switch")
        vis = sum(not w.table.isRowHidden(r) for r in range(w.table.rowCount()))
        self.assertEqual(vis, 11)
        w.filter_combo.setCurrentText("All")
        spi = next((w.periph_combo.itemText(i) for i in range(w.periph_combo.count())
                    if w.periph_combo.itemText(i).startswith("SPI")), None)
        if spi:
            w.periph_combo.setCurrentText(spi)
            self.assertTrue(w.pin_map.highlight)                      # peripheral -> highlight
        # Map view: the pin map beside the full connection fabric (one card per pin,
        # every physical path shown, category-filterable and re-sortable).
        w.rail.select("map")
        self.assertEqual(len(w.conn_list._rows), 64)                 # every socket pin listed
        w._select(1)
        self.assertEqual(w.conn_list._sel, 1)                        # selection follows the map
        self.assertEqual(w.pin_map.selected, 1)
        # the fabric spells out the exact vault wiring for a switched pin,
        # at refdes level (socket refdes, cell refdes, receptacle contact)
        row = w.conn_list._rows[1]
        text = " ".join(lbl.text() for lbl, _r, _c in row._cells)
        self.assertIn("VBAT_TGT", text)                              # delivered rail
        self.assertIn("J_CARD1_LA 33", text)                         # receptacle contact
        self.assertIn("CARD_LANE_001", text)                         # default lane path too
        self.assertIn("J_SOCKET64_1", w.conn_list.chain.text())     # chain header refdes
        self.assertIn("J_EDGE64_1", w.conn_list.chain.text())
        self.assertIn("U_SW_64_1", text)                             # cell refdes
        self.assertIn("Source S1 Pin 5", text)                       # terminal pins
        w.conn_list.filter_combo.setCurrentText("Switched")
        self.assertEqual(len(w.conn_list._rows), 11)                 # filter to switched pins only
        w.conn_list.sort_combo.setCurrentText("Destination")
        self.assertEqual(len(w.conn_list._rows), 11)                 # re-sort keeps the filtered set
        w.conn_list.filter_combo.setCurrentText("All")
        self.assertEqual(len(w.conn_list._rows), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
