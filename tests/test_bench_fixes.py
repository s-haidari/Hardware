"""Bench feature audit fixes.

BENCH-05 (correctness): the resolver "5 V" column showed the package-union per-family
value on power/ground/reset/boot/VCAP/NC pins, contradicting "exact silicon". VBAT, VSS,
VDD, NRST, VCAP and BOOT0 must NEVER print "5 V" — 5 V tolerance is a GPIO/analog
attribute. Gated in tools/ui/features/bench.py via _resolved_is_five_v /
_resolved_five_v_label on the pin's electrical_class.

BENCH-06 (coherence): the family filter combo lived on the global header (visible on all
five tabs) yet only affected Profiles. It now lives in the Profiles panel body; the global
header carries only the package.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from PyQt5.QtWidgets import QApplication, QComboBox, QLabel, QTableWidget  # noqa: E402

import stm32_db as db  # noqa: E402
import stm32_authority as sauth  # noqa: E402
import ui.feature as F  # noqa: E402
from ui.features import bench  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _fake_ctx(cfg=None):
    """A ctx with a real EventBus + synchronous services (run_populate runs inline
    offscreen). Mirrors the fixture style of the other bench/panel tests."""
    class _Svc:
        def __init__(self):
            self.logs = []

        def log(self, m):
            self.logs.append(str(m))

        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)

    return SimpleNamespace(cfg=cfg or {}, services=_Svc(), theme=None, bus=F.EventBus())


# ── pure gate: the fix's core, no DB / no Qt ─────────────────────────────────
class ResolvedFiveVGateTests(unittest.TestCase):
    def test_power_ground_reset_boot_vcap_nc_never_five_v(self):
        # Even when resolve_part copied a tolerant union value onto the pin, a
        # non-IO electrical_class can never be 5 V tolerant.
        for ec in ("power", "ground", "reset", "boot", "vcap", "nc"):
            pn = {"electrical_class": ec, "five_v_tolerant": True}
            self.assertFalse(bench._resolved_is_five_v(pn), ec)
            self.assertEqual(bench._resolved_five_v_label(pn), "—", ec)

    def test_io_pin_reflects_its_family_value(self):
        self.assertTrue(bench._resolved_is_five_v({"electrical_class": "io", "five_v_tolerant": True}))
        self.assertEqual(bench._resolved_five_v_label(
            {"electrical_class": "io", "five_v_tolerant": True}), "5 V")
        self.assertFalse(bench._resolved_is_five_v({"electrical_class": "io", "five_v_tolerant": False}))
        self.assertEqual(bench._resolved_five_v_label(
            {"electrical_class": "io", "five_v_tolerant": False}), "—")

    def test_missing_electrical_class_falls_back_to_raw_value(self):
        # Defensive: with no class info, trust the raw tolerant flag (real resolve_part
        # always sets electrical_class, so this only guards malformed input).
        self.assertTrue(bench._resolved_is_five_v({"five_v_tolerant": True}))
        self.assertFalse(bench._resolved_is_five_v({"five_v_tolerant": False}))


# ── end-to-end against a real built DB ───────────────────────────────────────
class _DBTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = db.default_cubemx_source()
        if src is None:
            raise unittest.SkipTest("CubeMX XML source not found")
        cls._dbp = Path(tempfile.mkdtemp()) / "stm32.sqlite"
        db.build_database(src, cls._dbp)
        # BenchState connects via db.default_db_path(); point it at our temp DB.
        cls._prev_env = os.environ.get("STM32_DB")
        os.environ["STM32_DB"] = str(cls._dbp)

    @classmethod
    def tearDownClass(cls):
        if cls._prev_env is None:
            os.environ.pop("STM32_DB", None)
        else:
            os.environ["STM32_DB"] = cls._prev_env


class ResolverPanelFiveVTests(_DBTestBase):
    def _resolve_and_read_five_v(self, mpn):
        """Build the real resolver panel, resolve `mpn`, and return the resolved pins
        plus the rendered "5 V" column text keyed by pin position."""
        state = bench.BenchState()
        self.assertIsNone(state.error, state.error)
        ctx = _fake_ctx()
        panel = bench._resolver_panel(ctx, state)
        # drive the resolve through the panel's own bus handler (same path the UI uses)
        ctx.bus.emit("bench.resolve", mpn)
        res = sauth.resolve_part(state.conn, mpn)
        self.assertTrue(res, f"resolve_part returned nothing for {mpn}")
        tbl = panel.findChild(QTableWidget)
        self.assertIsNotNone(tbl, "resolver table did not render")
        cols = [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]
        self.assertIn("5 V", cols)
        fv_col = cols.index("5 V")
        # the rendered pins map row-by-row to res["pins"][:400]
        shown = {}
        for r in range(tbl.rowCount()):
            w = tbl.cellWidget(r, fv_col)
            lbl = w if isinstance(w, QLabel) else (w.findChild(QLabel) if w else None)
            pin = int(tbl.item(r, 0).text())
            shown[pin] = lbl.text() if lbl else ""
        return res, shown

    def test_no_power_or_ground_pin_prints_five_v(self):
        res, shown = self._resolve_and_read_five_v("STM32F407VGT6")
        offenders = []
        for p in res["pins"]:
            if p["electrical_class"] in ("power", "ground", "reset", "boot", "vcap", "nc"):
                if shown.get(p["pin"]) == "5 V":
                    offenders.append((p["pin"], p["name"], p["electrical_class"]))
        self.assertEqual(offenders, [],
                         f"non-IO pins wrongly labelled 5 V: {offenders}")

    def test_the_column_still_varies_for_io_pins(self):
        # Guard against over-correction: real GPIO 5 V tolerance must still show. The
        # STM32F407 has many 5 V-tolerant IO pins, so at least one row prints "5 V".
        res, shown = self._resolve_and_read_five_v("STM32F407VGT6")
        io_tol = [p["pin"] for p in res["pins"]
                  if p["electrical_class"] == "io" and p.get("five_v_tolerant")]
        self.assertTrue(io_tol, "fixture part has no 5 V-tolerant IO pins")
        self.assertTrue(any(shown.get(pin) == "5 V" for pin in io_tol),
                        "no IO pin prints 5 V — the gate over-corrected")

    def test_specific_rail_pins_show_em_dash(self):
        # The concrete pins the audit named for STM32F407V: VBAT(6), VSS(10), NRST(14),
        # VDD(19), BOOT0(94) all resolve tolerant in the union but must show "—".
        res, shown = self._resolve_and_read_five_v("STM32F407VGT6")
        by_name = {}
        for p in res["pins"]:
            by_name.setdefault(p["name"], p["pin"])
        for nm in ("VBAT", "NRST", "BOOT0"):
            if nm in by_name:
                self.assertEqual(shown.get(by_name[nm]), "—",
                                 f"{nm} (pin {by_name[nm]}) should be em-dash, not 5 V")


class ProfilesFamilyFilterLocationTests(_DBTestBase):
    def test_family_combo_lives_in_profiles_body_not_global_header(self):
        state = bench.BenchState()
        self.assertIsNone(state.error, state.error)
        ctx = _fake_ctx()
        feat = bench.BenchFeature()
        ws = feat.build(ctx)

        # The Profiles panel body carries a family combo whose items are the package's
        # families plus "All Families".
        prof = bench._profiles_panel(ctx, state)
        combos = prof.findChildren(QComboBox)
        fam_combos = [c for c in combos
                      if any(c.itemText(i) == bench._ALL_FAMILIES for i in range(c.count()))]
        self.assertTrue(fam_combos, "Profiles panel has no family combo in its body")
        fam = fam_combos[0]
        self.assertEqual(fam.itemText(0), bench._ALL_FAMILIES)
        self.assertGreater(fam.count(), 1, "family combo lists no families")

        # The global header must NOT carry a family combo: no combo anywhere in the built
        # feature (before any Profiles panel is lazily shown) may list "All Families". The
        # only header control is the package combo, whose items are the buildable packages.
        pkgs = set(state.packages)
        header_combos = [c for c in ws.findChildren(QComboBox)
                         if {c.itemText(i) for i in range(c.count())} <= pkgs]
        self.assertTrue(header_combos, "no package combo on the header")
        for c in ws.findChildren(QComboBox):
            items = {c.itemText(i) for i in range(c.count())}
            self.assertNotIn(bench._ALL_FAMILIES, items,
                             "family filter still lives on the global header / feature chrome")

    def test_selecting_family_narrows_profiles(self):
        # The in-body combo actually filters: selecting a family sets state.family and,
        # on rebuild, narrows the supported-parts count in the meta line.
        state = bench.BenchState()
        ctx = _fake_ctx()
        # capture the full part count first
        a = state.authority()
        all_parts = a["manifest"].get("supported_parts", []) or []
        fams = state.families()
        self.assertTrue(fams, "package supports no families")
        fam = fams[0]
        state.set_family(fam)
        self.assertEqual(state.family, fam)
        narrowed = [p for p in all_parts if fam.upper() in str(p).upper()]
        self.assertLess(len(narrowed), len(all_parts),
                        "chosen family did not narrow the part set")
        # rebuild the panel with the family set and confirm the meta reflects the count
        prof = bench._profiles_panel(ctx, state)
        texts = " ".join(l.text() for l in prof.findChildren(QLabel))
        self.assertIn(f"{len(narrowed)} Supported Parts", texts)
        self.assertIn(f"Family {fam}", texts)


if __name__ == "__main__":
    unittest.main()
