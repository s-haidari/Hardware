"""
matrix.py — rich per-pin view + validation from the folded stm32switch generator.

This is STMP's detailed pin matrix (every socket pin's roles, stability, required
cell) and the validation report, served from the app.
"""
from __future__ import annotations

from pathlib import Path

from stm32switch import io as sio
from stm32switch import normalize, schema, validate

_STABILITY_LABEL = {
    "GUARANTEED_IO_DIFFERENT_FUNCTIONS": "Guaranteed IO (functions vary)",
    "GUARANTEED_FIXED_ROLE": "Fixed role",
    "MIXED_IO_AND_POWER": "Mixed IO / power",
    "MIXED_IO_AND_GROUND": "Mixed IO / ground",
    "MIXED_IO_AND_SPECIAL": "Mixed IO / special",
    "MIXED_VCAP_OR_ANALOG_SPECIAL": "Mixed VCAP / analog",
    "MIXED_POWER_AND_GROUND": "Mixed power / ground",
    "UNKNOWN_REVIEW": "Review required",
}


def _clean(s: str) -> str:
    return (s or "").replace("|", ", ").replace("_", " ").strip()


def package_matrix(db_path: Path, package: str) -> dict:
    conn = sio.connect(db_path)
    try:
        pd = normalize.assemble(conn, package)
        rows = schema.matrix_rows(pd)
    finally:
        conn.close()

    base = [r for r in rows if r.get("is_baseline_group") == "yes"] or rows
    seen: set[int] = set()
    pins: list[dict] = []
    for r in sorted(base, key=lambda r: int(r["socket_pin"])):
        pin = int(r["socket_pin"])
        if pin in seen:
            continue
        seen.add(pin)
        stab = r.get("pin_role_stability", "")
        pins.append({
            "pin": pin,
            "side": (r.get("socket_side") or "").title(),
            "pin_name": r.get("datasheet_pin_name", ""),
            "gpio": (f"P{r.get('port_pin')}" if r.get("port_pin") else ""),
            "roles": _clean(r.get("roles_seen_all_groups", "")),
            "stability": _STABILITY_LABEL.get(stab, stab),
            "needs_switch": r.get("needs_victim_card_switching") == "yes",
            "required_cell": r.get("victim_card_cell_display_name") or r.get("victim_card_cell_required", ""),
        })
    try:
        summary = validate.summary_counts(pd)
    except Exception:
        summary = {}
    return {
        "package": package,
        "pin_count": pd.pin_count,
        "groups": len(pd.groups),
        "summary": summary,
        "pins": pins,
    }


def package_validation(db_path: Path, package: str) -> dict:
    conn = sio.connect(db_path)
    try:
        pd = normalize.assemble(conn, package)
        try:
            rep = validate.validate_package(pd)
        except Exception as exc:  # folded generator has an internal mismatch
            return {"package": package, "available": False, "reason": f"{type(exc).__name__}: {exc}",
                    "errors": 0, "warnings": 0, "findings": []}
    finally:
        conn.close()
    return {
        "package": package,
        "available": True,
        "errors": len(rep.errors),
        "warnings": len(rep.warnings),
        "findings": [
            {"severity": f.severity, "code": getattr(f, "code", ""), "message": getattr(f, "message", str(f))}
            for f in rep.findings
        ],
    }
