"""
voltage.py — turn the package voltage envelope + special-rail presence into
concrete parent hardware requirements.

Voltage is not just informational: the VTARGET programmable range, and whether
the parent needs VDDA_TARGET / VREF_TARGET / VBAT_TARGET branches and local VCAP
caps, all follow from this. Per-rail min/typ/max voltages are not in the source
DB, so those rails are reported by pin presence (honest, not invented).
"""
from __future__ import annotations


def plan(pv: dict) -> dict:
    vmin = pv.get("vtarget_min_mv")
    vmax = pv.get("vtarget_max_mv")

    def _v(mv):
        return round(mv / 1000, 2) if mv else None

    rng = f"{_v(vmin):.2f}–{_v(vmax):.2f} V" if vmin and vmax else "UNKNOWN"
    return {
        "vtarget_min_v": _v(vmin),
        "vtarget_max_v": _v(vmax),
        "vtarget_range": rng,
        # VTARGET must reach the lowest min across families, so 1.8 V-class IO
        # support is needed when any member goes that low.
        "low_voltage_io_required": bool(vmin and vmin <= 1900),
        "vdda_target_required": pv.get("vdda_pins", 0) > 0,
        "vref_target_required": pv.get("vref_pins", 0) > 0,
        "vbat_target_required": pv.get("vbat_pins", 0) > 0,
        "vcap_branch_required": pv.get("vcap_pins", 0) > 0,
        "vdda_pins": pv.get("vdda_pins", 0),
        "vref_pins": pv.get("vref_pins", 0),
        "vbat_pins": pv.get("vbat_pins", 0),
        "vcap_pins": pv.get("vcap_pins", 0),
    }


def requirement_rows(package: str, pv: dict) -> list[dict]:
    """One row per power rail/branch with its requirement + provenance."""
    p = plan(pv)
    vmin = pv.get("vtarget_min_mv")
    vmax = pv.get("vtarget_max_mv")
    rows = [{
        "package": package, "rail": "VTARGET", "required": True,
        "pin_count": "", "min_v": p["vtarget_min_v"] if vmin else "TODO_SOURCE_REQUIRED",
        "max_v": p["vtarget_max_v"] if vmax else "TODO_SOURCE_REQUIRED",
        "notes": f"programmable target supply; range {p['vtarget_range']}"
                 + ("; must support ~1.8 V-class IO" if p["low_voltage_io_required"] else ""),
    }]
    for rail, req, pins, note in [
        ("VDDA_TARGET", p["vdda_target_required"], p["vdda_pins"],
         "analog supply branch (low-noise filter)"),
        ("VREF_TARGET", p["vref_target_required"], p["vref_pins"],
         "ADC/DAC reference branch"),
        ("VBAT_TARGET", p["vbat_target_required"], p["vbat_pins"],
         "battery/backup-domain branch"),
        ("VCAP_LOCAL", p["vcap_branch_required"], p["vcap_pins"],
         "local LDO cap on the card; never exposed as a lane"),
    ]:
        rows.append({
            "package": package, "rail": rail, "required": req,
            "pin_count": pins,
            "min_v": "TODO_SOURCE_REQUIRED" if req else "",
            "max_v": "TODO_SOURCE_REQUIRED" if req else "",
            "notes": note if req else "no pin in this package needs it",
        })
    return rows
