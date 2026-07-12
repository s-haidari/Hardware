"""Backend logic for the Library Part-detail + sourcing subsystem.

Covers three pure/near-pure helpers the PartDetail canvas leans on:
  * snapshot_refresh_policy  — Mouser's shared-cap 4h gate vs LCSC always-refreshable
  * projects_referencing_symbol — which discovered projects instantiate a lib symbol
  * completion_tooltip — the per-dimension check/cross passport shown on hover
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import LibraryManager as LM  # noqa: E402


# ── snapshot age + per-provider refresh policy ──────────────────────────────
def test_snapshot_age_seconds_roundtrips():
    now = dt.datetime(2026, 7, 12, 12, 0, tzinfo=dt.timezone.utc)
    older = (now - dt.timedelta(hours=5)).isoformat()
    secs = LM.snapshot_age_seconds(older, now=now)
    assert secs is not None and abs(secs - 5 * 3600) < 1
    assert LM.snapshot_age_seconds("", now=now) is None
    assert LM.snapshot_age_seconds("not-a-date", now=now) is None


def test_refresh_policy_mouser_gated_under_4h():
    pol = LM.snapshot_refresh_policy("Mouser", 2 * 3600)
    assert pol["can_refresh"] is False
    assert "shared" in pol["reason"].lower()          # names the shared-cap reason


def test_refresh_policy_mouser_enabled_over_4h():
    assert LM.snapshot_refresh_policy("Mouser", 5 * 3600)["can_refresh"] is True


def test_refresh_policy_mouser_enabled_when_age_unknown():
    # A just-fetched live part (no persisted age) is refreshable.
    assert LM.snapshot_refresh_policy("Mouser", None)["can_refresh"] is True


def test_refresh_policy_lcsc_always_enabled():
    assert LM.snapshot_refresh_policy("LCSC", 1)["can_refresh"] is True
    assert LM.snapshot_refresh_policy("LCSC", 10 * 3600)["can_refresh"] is True


def test_refresh_policy_unknown_provider_enabled():
    assert LM.snapshot_refresh_policy("", 1)["can_refresh"] is True


# ── projects referencing a library symbol (rename heads-up) ─────────────────
def _repo_with_symbol_use(tmp_path, symbol_name):
    """A repo root with two projects: one whose schematic instantiates `symbol_name`
    via a lib_id, one that does not."""
    pro = '{"board":{},"meta":{"version":1}}'
    uses = tmp_path / "Board_A"
    uses.mkdir()
    (uses / "Board_A.kicad_pro").write_text(pro, encoding="utf-8")
    (uses / "Board_A.kicad_sch").write_text(
        '(kicad_sch (version 20230121)\n'
        f'  (symbol (lib_id "MySymbols:{symbol_name}") (at 10 10 0)\n'
        '    (property "Reference" "U1" (at 10 5 0)))\n)\n', encoding="utf-8")
    other = tmp_path / "Board_B"
    other.mkdir()
    (other / "Board_B.kicad_pro").write_text(pro, encoding="utf-8")
    (other / "Board_B.kicad_sch").write_text(
        '(kicad_sch (version 20230121)\n'
        '  (symbol (lib_id "Device:R") (at 10 10 0)\n'
        '    (property "Reference" "R1" (at 10 5 0)))\n)\n', encoding="utf-8")
    return {"RepoRoot": str(tmp_path)}


def test_projects_referencing_symbol_finds_the_user(tmp_path):
    cfg = _repo_with_symbol_use(tmp_path, "STM32F407")
    hits = LM.projects_referencing_symbol(cfg, "STM32F407")
    assert hits == ["Board_A"]


def test_projects_referencing_symbol_none_when_unused(tmp_path):
    cfg = _repo_with_symbol_use(tmp_path, "STM32F407")
    assert LM.projects_referencing_symbol(cfg, "NOSUCHSYM") == []


def test_projects_referencing_symbol_blank_name(tmp_path):
    cfg = _repo_with_symbol_use(tmp_path, "STM32F407")
    assert LM.projects_referencing_symbol(cfg, "") == []


# ── completion tooltip (per-dimension passport) ─────────────────────────────
def test_completion_tooltip_lists_present_and_missing():
    row = {"has_symbol": True, "has_footprint": True, "has_model": True,
           "mpn": "LM358", "has_real_mpn": True, "manufacturer": "TI",
           "datasheet": "http://d", "description": "", "category": ""}
    comp = LM.part_completion(row)
    tip = LM.completion_tooltip(comp)
    # every dimension label appears exactly once, with a present/absent mark
    for label in ("Symbol", "Footprint", "3D Model", "Part Number",
                  "Manufacturer", "Datasheet", "Description", "Category"):
        assert label in tip
    # the two blanks are the missing set → carry the cross mark
    for missing in comp["missing"]:
        line = next(l for l in tip.splitlines() if l.endswith(missing))
        assert line.startswith(LM.COMPLETION_CROSS)
    # a present one carries the check mark
    sym_line = next(l for l in tip.splitlines() if l.endswith("Symbol"))
    assert sym_line.startswith(LM.COMPLETION_CHECK)
