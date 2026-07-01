"""
matrix.py — per-pin matrix + validation, derived from THIS app's CubeMX database
via the canonical switch_engine. No dependency on the old STMP/STM-Helper schema:
every value comes from our own mcu / mcu_package_pin / pin_role tables.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import switch_engine as se

_FIXED_LABEL = {
    se.ID_IO: "Guaranteed IO",
    se.ID_VDD: "Fixed power (VDD)", se.ID_VDDA: "Fixed power (VDDA)",
    se.ID_VREF: "Fixed power (VREF)", se.ID_VBAT: "Fixed power (VBAT)",
    se.ID_VSS: "Fixed ground", se.ID_VCAP: "Fixed VCAP",
    se.ID_BOOT: "Boot strap", se.ID_NRST: "Reset", se.ID_OSC: "Oscillator",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _pin_info(conn: sqlite3.Connection, package: str) -> dict[int, tuple[str, str]]:
    """Representative pin name + GPIO per physical pin (most common across the family)."""
    rows = conn.execute(
        """
        SELECT p.physical_pin_number AS pin, p.canonical_pin_name AS name,
               p.gpio_port AS port, p.gpio_pin_index AS idx, COUNT(*) AS n
        FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id
        WHERE m.package_name = ?
        GROUP BY p.physical_pin_number, p.canonical_pin_name
        ORDER BY p.physical_pin_number, n DESC
        """,
        (package,),
    ).fetchall()
    out: dict[int, tuple[str, str]] = {}
    for r in rows:
        pin = int(r["pin"])
        gpio = f"P{r['port']}{r['idx']}" if r["port"] not in (None, "") and r["idx"] is not None else ""
        if pin not in out:                       # first row = most common name
            out[pin] = (r["name"] or "", gpio)
        elif gpio and not out[pin][1]:           # backfill a GPIO if the top name lacked one
            out[pin] = (out[pin][0], gpio)
    return out


def _stability(d: se.SwitchDecision) -> str:
    if d.switch_class == se.SWITCH_MUST:
        return "Role-variable (needs switch)"
    if d.switch_class == se.SWITCH_OSC_OPTIONAL:
        return "Oscillator (route or switch)"
    return _FIXED_LABEL.get(d.dominant_identity, "Fixed role")


def package_matrix(db_path: Path, package: str) -> dict:
    conn = _connect(db_path)
    try:
        rep = se.package_report(conn, package)
        info = _pin_info(conn, package)
    finally:
        conn.close()

    pins: list[dict] = []
    for d in sorted(rep.decisions, key=lambda d: d.pin):
        name, gpio = info.get(d.pin, ("", ""))
        pins.append({
            "pin": d.pin,
            "side": (d.side or "").title(),
            "pin_name": name,
            "gpio": gpio,
            "roles": ", ".join(sorted(i for i in d.identities)),
            "stability": _stability(d),
            # strictly the must-switch pins, so the map/matrix agree with the
            # canonical headline count (LQFP64 = 11); osc-optional pins carry
            # their own stability label instead of being force-highlighted.
            "needs_switch": d.switch_class == se.SWITCH_MUST,
            "required_cell": d.cell_required,
        })
    return {
        "package": package,
        "pin_count": len(pins),
        "must_switch": rep.must_switch_count,
        "osc_optional": rep.osc_optional_count,
        "fixed": rep.fixed_count,
        "pins": pins,
    }


def package_validation(db_path: Path, package: str) -> dict:
    """Sanity-check the derived switch decisions for this package. Everything is
    computed from our own database, so there is no schema to disagree with; the
    findings flag design-review items (minority roles, osc choice) and any pin
    that came out inconsistent."""
    conn = _connect(db_path)
    try:
        rep = se.package_report(conn, package)
    finally:
        conn.close()

    findings: list[dict] = []
    for d in sorted(rep.decisions, key=lambda d: d.pin):
        where = f"pin {d.pin}"
        if d.needs_switch and not d.primary_target_net:
            findings.append({"severity": "error", "code": "NO_TARGET_NET",
                             "message": f"{where}: switch pin has no routing target net"})
        if d.switch_class == se.SWITCH_OSC_OPTIONAL:
            findings.append({"severity": "warning", "code": "OSC_OPTIONAL",
                             "message": f"{where}: oscillator pin — route direct or switch (per-card choice)"})
        elif d.minority_identities:
            findings.append({"severity": "warning", "code": "MINORITY_ROLE",
                             "message": f"{where}: minority role(s) {', '.join(d.minority_identities)} "
                                        f"seen on few MCUs — verify against the target part"})
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    return {"package": package, "available": True, "errors": errors,
            "warnings": warnings, "findings": findings}
