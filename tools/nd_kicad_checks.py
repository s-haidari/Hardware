"""Structured ERC + DRC for ANY KiCad 7+/8+ project.

KiCad's electrical-rules (schematic) and design-rules (board) checkers only run
headlessly through kicad-cli, which since KiCad 7 can emit JSON. This module runs
them and — the useful part — parses that JSON into a flat, severity-ranked list of
findings you can rank, count, and click, instead of the raw 12-line tail dump the
tool used to show. It also has no board DRC before.

The JSON PARSERS (parse_erc_json / parse_drc_json) are pure and independently
testable; the runners just shell out and hand their output to the parsers.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


_SEV_ORDER = {"error": 0, "warning": 1, "exclusion": 2, "info": 3}


def _finding(severity, rule, message, where=""):
    sev = (severity or "").lower()
    if sev not in _SEV_ORDER:
        sev = "warning"
    return {"severity": sev, "rule": rule or "", "message": (message or "").strip(),
            "where": where}


def _pos(items) -> str:
    """Human 'x, y' from a KiCad JSON position/items list, best-effort."""
    if isinstance(items, dict) and "pos" in items and isinstance(items["pos"], dict):
        p = items["pos"]
        return f"({p.get('x')}, {p.get('y')})"
    return ""


def parse_erc_json(text: str) -> List[dict]:
    """Flatten a `kicad-cli sch erc --format json` report into findings. Tolerant of
    the schema drift across KiCad 7/8 (sheets[].violations[] with type/severity/
    description/items)."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return []
    out = []
    sheets = doc.get("sheets") or ([doc] if "violations" in doc else [])
    for sheet in sheets:
        sheet_name = sheet.get("path") or sheet.get("uuid") or ""
        for v in sheet.get("violations", []) or []:
            sev = v.get("severity") or (v.get("severity_level"))
            rule = v.get("type") or v.get("rule") or ""
            msg = v.get("description") or v.get("message") or ""
            items = v.get("items") or []
            where = "; ".join(_x for _x in (
                (it.get("description") or "") + (" " + _pos(it) if _pos(it) else "")
                for it in items) if _x.strip()) or (sheet_name or "")
            out.append(_finding(sev, rule, msg, where))
    return sorted(out, key=lambda f: (_SEV_ORDER[f["severity"]], f["rule"]))


def parse_drc_json(text: str) -> List[dict]:
    """Flatten a `kicad-cli pcb drc --format json` report into findings. Merges the
    violations, unconnected_items, and schematic_parity sections KiCad emits."""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return []
    out = []
    for section in ("violations", "unconnected_items", "schematic_parity"):
        for v in doc.get(section, []) or []:
            sev = v.get("severity") or "error"
            rule = v.get("type") or v.get("rule") or section
            msg = v.get("description") or v.get("message") or ""
            items = v.get("items") or []
            where = "; ".join(_x for _x in (
                (it.get("description") or "") + (" " + _pos(it) if _pos(it) else "")
                for it in items) if _x.strip())
            out.append(_finding(sev, rule, msg, where))
    return sorted(out, key=lambda f: (_SEV_ORDER[f["severity"]], f["rule"]))


def summarize(findings: List[dict]) -> dict:
    """{errors, warnings, total} + per-rule counts for a findings list."""
    by_sev, by_rule = {"error": 0, "warning": 0, "exclusion": 0, "info": 0}, {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    return {"total": len(findings), "errors": by_sev["error"],
            "warnings": by_sev["warning"], "by_severity": by_sev, "by_rule": by_rule}


# ── runners (need kicad-cli) ─────────────────────────────────────────────────
_NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0


def _run_json_check(cli: str, args: List[str], src: Path, parser) -> dict:
    """Run a kicad-cli check that writes a JSON report to a temp file, then parse it.
    Returns {ok, findings, summary, error, raw}. Never raises."""
    if not cli:
        return {"ok": False, "error": "kicad-cli not found", "findings": [], "summary": summarize([])}
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "report.json"
        cmd = [cli] + args + ["--format", "json", "--output", str(out), str(src)]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", creationflags=_NO_WINDOW,
                                  timeout=300)
        except Exception as e:                       # noqa: BLE001
            return {"ok": False, "error": str(e), "findings": [], "summary": summarize([])}
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        # A kicad-cli run that produces no report (bad file / crash / nonzero exit with
        # nothing written) must NOT read as a clean board — that silent pass is exactly
        # what the correctness bar forbids. The trustworthy signal is a VALID JSON report:
        # kicad-cli exits 0 even with violations (no --exit-code-violations here), so the
        # return code alone can't tell a clean pass from a failed run.
        if not text.strip():
            err = (proc.stdout or "").strip() or f"kicad-cli produced no report (exit {proc.returncode})"
            return {"ok": False, "error": err, "findings": [],
                    "summary": summarize([]), "returncode": proc.returncode}
        try:
            json.loads(text)                         # a truncated/corrupt report is a failure,
        except (ValueError, TypeError):              # not a clean board with zero findings
            return {"ok": False,
                    "error": f"kicad-cli report was not valid JSON (exit {proc.returncode})",
                    "findings": [], "summary": summarize([]), "returncode": proc.returncode}
        findings = parser(text)
        return {"ok": True, "findings": findings, "summary": summarize(findings),
                "error": "", "returncode": proc.returncode}


def run_erc(sch_path, cli: str) -> dict:
    """Run ERC on a .kicad_sch via kicad-cli and return structured findings."""
    return _run_json_check(cli, ["sch", "erc"], Path(sch_path), parse_erc_json)


def run_drc(pcb_path, cli: str) -> dict:
    """Run DRC on a .kicad_pcb via kicad-cli and return structured findings."""
    return _run_json_check(cli, ["pcb", "drc"], Path(pcb_path), parse_drc_json)
