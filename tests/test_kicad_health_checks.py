"""Project Health Audit (nd_project_health) and structured ERC/DRC parsing
(nd_kicad_checks) — both general-purpose, working on any KiCad project."""
import os
import sys
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import nd_project_health as ph      # noqa: E402
import nd_kicad_checks as kc        # noqa: E402

_SCH = ('(kicad_sch (version 20230121)\n'
        '  (lib_symbols\n'
        '    (symbol "Device:R" (pin (at 0 0 0)) (pin (at 0 0 0)))\n'
        '    (symbol "MCU:CHIP" (symbol "CHIP_1_1" (pin) (pin) (pin) (pin))))\n'
        '  (symbol (lib_id "Device:R") (property "Reference" "R1" (at 0 0 0)) '
        '(property "Value" "10k" (at 0 0 0)) (property "Footprint" "LIB:R_0402" (at 0 0 0)))\n'
        '  (symbol (lib_id "Device:R") (property "Reference" "R1" (at 0 0 0)) '
        '(property "Value" "10k" (at 0 0 0)) (property "Footprint" "LIB:R_0402" (at 0 0 0)))\n'
        '  (symbol (lib_id "Device:C") (property "Reference" "C?" (at 0 0 0)) '
        '(property "Value" "1uF" (at 0 0 0)) (property "Footprint" "" (at 0 0 0)))\n'
        '  (symbol (lib_id "MCU:CHIP") (property "Reference" "U1" (at 0 0 0)) '
        '(property "Value" "MYCHIP" (at 0 0 0)) (property "MANUFACTURER" "Acme" (at 0 0 0)) '
        '(property "Footprint" "LIB:QFP8" (at 0 0 0)))\n'
        '  (symbol (lib_id "power:GND") (property "Reference" "#PWR01" (at 0 0 0)) '
        '(property "Value" "GND" (at 0 0 0)))\n)\n')


class ProjectHealthTests(unittest.TestCase):
    def _audit(self):
        self._td = tempfile.TemporaryDirectory()
        tp = pathlib.Path(self._td.name)
        (tp / "b.kicad_sch").write_text(_SCH, encoding="utf-8")
        (tp / "QFP8.kicad_mod").write_text(
            '(footprint "QFP8" ' + " ".join(f'(pad "{i}" smd)' for i in range(1, 9)) +
            ' (model "chip.step"))', encoding="utf-8")
        (tp / "R_0402.kicad_mod").write_text(
            '(footprint "R_0402" (pad "1" smd) (pad "2" smd))', encoding="utf-8")
        return ph.audit_schematic(str(tp / "b.kicad_sch"),
                                  footprint_dirs=[str(tp)], model_dirs=[str(tp)])

    def test_finds_the_real_problems(self):
        a = self._audit()
        kinds = {f["kind"] for f in a["findings"]}
        self.assertIn("duplicate_ref", kinds)         # two R1
        self.assertIn("unannotated", kinds)           # C?
        self.assertIn("no_footprint", kinds)          # C?
        self.assertIn("pin_pad_mismatch", kinds)      # U1: 4 pins vs 8 pads
        self.assertIn("no_3d_model", kinds)           # R_0402 has no model line
        self.assertEqual(a["components"], 4)          # power symbol excluded

    def test_findings_are_deduped_and_ranked(self):
        a = self._audit()
        keys = [(f["ref"], f["kind"], f["detail"]) for f in a["findings"]]
        self.assertEqual(len(keys), len(set(keys)))   # no duplicate findings
        sev = [f["severity"] for f in a["findings"]]
        self.assertEqual(sev, sorted(sev, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s]))
        self.assertIn("Project Health", ph.audit_report_markdown(a))

    def test_clean_schematic_has_no_findings(self):
        with tempfile.TemporaryDirectory() as td:
            sch = ('(kicad_sch (version 1) (lib_symbols '
                   '(symbol "Device:R" (pin) (pin))) '
                   '(symbol (lib_id "Device:R") (property "Reference" "R1" (at 0 0 0)) '
                   '(property "Value" "10k" (at 0 0 0)) '
                   '(property "Footprint" "LIB:R_0402" (at 0 0 0)) '
                   '(property "MANUFACTURER" "Yageo" (at 0 0 0)) '
                   '(property "MPN" "RC0402" (at 0 0 0))))')
            p = pathlib.Path(td) / "c.kicad_sch"
            p.write_text(sch, encoding="utf-8")
            a = ph.audit_schematic(str(p))            # no footprint dirs -> skip pad check
            self.assertEqual(a["findings"], [])
            self.assertEqual(a["healthy"], 1)


class CheckRegistryTests(unittest.TestCase):
    """The audit core runs a pluggable list of registered checks, not a hardcoded
    body. New checks register via @register_check; the loop is stable."""

    def _one_comp_audit(self, register=None):
        # a single component with a blank footprint -> exactly one no_footprint find
        comps = [{"ref": "R1", "value": "10k", "footprint": "", "lib_id": "Device:R",
                  "props": {"Reference": "R1", "MANUFACTURER": "Yageo"}}]
        added = None
        try:
            if register is not None:
                added = register
                ph.register_check(register)
            return ph._audit_components(comps, {})
        finally:
            if added is not None and added in ph._CHECKS:
                ph._CHECKS.remove(added)

    def test_checks_are_registered_not_inline(self):
        # the registry is non-empty and covers the built-in kinds
        self.assertTrue(ph._CHECKS)
        for fn in (ph._check_unannotated, ph._check_duplicate_ref,
                   ph._check_no_footprint, ph._check_no_mpn,
                   ph._check_footprint_pads_and_models):
            self.assertIn(fn, ph._CHECKS)

    def test_registered_check_contributes_findings(self):
        base = self._one_comp_audit()
        base_kinds = {f["kind"] for f in base["findings"]}
        self.assertIn("no_footprint", base_kinds)          # built-in still runs
        self.assertNotIn("custom_flag", base_kinds)

        def _flag_all(ctx):
            for c in ctx.comps:
                ctx.add(c["ref"], "info", "custom_flag", "custom check fired")

        au = self._one_comp_audit(register=_flag_all)
        kinds = {f["kind"] for f in au["findings"]}
        self.assertIn("custom_flag", kinds)                # new check took effect
        self.assertIn("no_footprint", kinds)               # without touching the core
        # cleanup restored the registry so the extra check no longer runs
        after = {f["kind"] for f in self._one_comp_audit()["findings"]}
        self.assertNotIn("custom_flag", after)

    def test_footprint_counters_survive_the_registry(self):
        # the pin/pad check writes checked/unresolved onto the context; those must
        # still reach the result dict through the registry indirection.
        with tempfile.TemporaryDirectory() as td:
            tp = pathlib.Path(td)
            (tp / "R_0402.kicad_mod").write_text(
                '(footprint "R_0402" (pad "1" smd) (pad "2" smd))', encoding="utf-8")
            comps = [{"ref": "R1", "value": "10k", "footprint": "LIB:R_0402",
                      "lib_id": "Device:R", "props": {"Reference": "R1"}},
                     {"ref": "R2", "value": "10k", "footprint": "LIB:NOPE",
                      "lib_id": "Device:R", "props": {"Reference": "R2"}}]
            au = ph._audit_components(comps, {"Device:R": 2}, footprint_dirs=[str(tp)])
            self.assertEqual(au["checked_footprints"], 1)
            self.assertEqual(au["unresolved_footprints"], 1)


class ErcDrcParseTests(unittest.TestCase):
    def test_parse_erc_json(self):
        erc = ('{"sheets":[{"path":"/s1","violations":['
               '{"type":"pin_not_connected","severity":"error","description":"Pin not connected",'
               '"items":[{"description":"U2 pin 14","pos":{"x":45.0,"y":22.0}}]},'
               '{"type":"unresolved_net","severity":"warning","description":"Unresolved /CLK","items":[]}'
               ']}]}')
        f = kc.parse_erc_json(erc)
        self.assertEqual(len(f), 2)
        self.assertEqual(f[0]["severity"], "error")   # error sorts first
        self.assertEqual(f[0]["rule"], "pin_not_connected")
        self.assertIn("U2 pin 14", f[0]["where"])
        s = kc.summarize(f)
        self.assertEqual((s["errors"], s["warnings"], s["total"]), (1, 1, 2))

    def test_parse_drc_json_merges_sections(self):
        drc = ('{"violations":[{"type":"clearance","severity":"warning","description":"0.18<0.20","items":[]}],'
               '"unconnected_items":[{"type":"unconnected","severity":"error","description":"GND","items":[]}],'
               '"schematic_parity":[{"type":"parity","severity":"error","description":"extra R9","items":[]}]}')
        f = kc.parse_drc_json(drc)
        self.assertEqual(len(f), 3)
        self.assertEqual(kc.summarize(f)["errors"], 2)  # unconnected + parity

    def test_parsers_tolerate_garbage(self):
        self.assertEqual(kc.parse_erc_json("not json"), [])
        self.assertEqual(kc.parse_drc_json(""), [])
        self.assertEqual(kc.parse_erc_json("{}"), [])

    def test_runner_without_cli_is_graceful(self):
        r = kc.run_erc("nope.kicad_sch", "")
        self.assertFalse(r["ok"])
        self.assertIn("kicad-cli", r["error"])
        self.assertEqual(r["findings"], [])


class RunnerFailureSemantics(unittest.TestCase):
    """A kicad-cli DRC/ERC run that fails or writes no/garbage report must report
    ok=False — never a silent clean pass (ok=True, findings=[]). Exercises the real
    subprocess branch of _run_json_check with a faked kicad-cli that controls exactly
    what report (if any) lands on disk."""

    @staticmethod
    def _fake_cli(report=None, returncode=0, stdout=""):
        # stand-in for kicad-cli: writes `report` to the --output path when given,
        # writes nothing when report is None (simulating a run that produced no file).
        def run(cmd, **kw):
            if report is not None:
                out = pathlib.Path(cmd[cmd.index("--output") + 1])
                out.write_text(report, encoding="utf-8")
            p = mock.Mock(); p.returncode = returncode; p.stdout = stdout
            return p
        return run

    def test_no_report_is_a_failure_not_a_clean_pass(self):
        with mock.patch.object(kc.subprocess, "run",
                               self._fake_cli(report=None, returncode=1, stdout="cannot open board")):
            r = kc.run_drc("board.kicad_pcb", "kicad-cli")
        self.assertFalse(r["ok"])              # regression guard: was hardcoded True
        self.assertEqual(r["findings"], [])
        self.assertTrue(r["error"])

    def test_empty_report_is_a_failure(self):
        with mock.patch.object(kc.subprocess, "run", self._fake_cli(report="", returncode=0)):
            r = kc.run_erc("sch.kicad_sch", "kicad-cli")
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])

    def test_corrupt_json_report_is_a_failure(self):
        with mock.patch.object(kc.subprocess, "run",
                               self._fake_cli(report='{"violations": [trunc', returncode=0)):
            r = kc.run_drc("board.kicad_pcb", "kicad-cli")
        self.assertFalse(r["ok"])
        self.assertIn("JSON", r["error"])

    def test_valid_report_with_violations_is_ok_with_findings(self):
        report = ('{"violations": [{"severity": "error", "type": "clearance",'
                  ' "description": "too close", "items": []}]}')
        with mock.patch.object(kc.subprocess, "run", self._fake_cli(report=report, returncode=0)):
            r = kc.run_drc("board.kicad_pcb", "kicad-cli")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["findings"]), 1)
        self.assertEqual(r["findings"][0]["rule"], "clearance")

    def test_valid_empty_report_is_a_genuine_clean_pass(self):
        with mock.patch.object(kc.subprocess, "run",
                               self._fake_cli(report='{"violations": []}', returncode=0)):
            r = kc.run_drc("board.kicad_pcb", "kicad-cli")
        self.assertTrue(r["ok"])               # a real clean board: ok=True, no findings
        self.assertEqual(r["findings"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
