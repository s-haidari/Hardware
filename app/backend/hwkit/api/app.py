"""
app.py — the unified Hardware app backend (FastAPI).

Run:  python -m uvicorn hwkit.api.app:app --port 8799   (from app/backend, venv active)

Mounts the folded-in domains. ``pins`` (STM32 switch fabric) is live; the
``library`` / ``netdeck`` routers attach here as those modules are ported.
"""
from __future__ import annotations

import csv
import io
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from ..core import config
from ..pins import switch_engine as se
from ..pins import switch_report as sr

app = FastAPI(title="Hardware App", version="0.1.0")


def _conn() -> sqlite3.Connection:
    db = config.stm_database_path()
    if not db.exists():
        raise HTTPException(status_code=503, detail=f"STM database not found: {db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/health")
def health() -> dict:
    db = config.stm_database_path()
    return {
        "status": "ok",
        "database": str(db),
        "database_present": db.exists(),
        "libs_root": str(config.libs_root()),
    }


@app.get("/api/pins/packages")
def pins_packages() -> list[dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT package_name AS p, COUNT(*) AS n FROM mcu "
            "WHERE package_name LIKE 'LQFP%' GROUP BY p ORDER BY n DESC"
        ).fetchall()
        return [{"package": r["p"], "mcus": r["n"]} for r in rows]
    finally:
        conn.close()


@app.get("/api/pins/{package}/switch-report")
def pins_switch_report(package: str) -> dict:
    conn = _conn()
    try:
        rep = se.package_report(conn, package)
        return {
            "package": package,
            "must_switch": rep.must_switch_count,
            "osc_optional": rep.osc_optional_count,
            "fixed": rep.fixed_count,
            "adg714_cells": rep.adg714_count,
            "pins": [
                {
                    "pin": d.pin,
                    "side": d.side,
                    "switch_class": d.switch_class,
                    "conflict_roles": d.role_label,
                    "routes_to": d.primary_target_net,
                    "required_cell": d.cell_required,
                    "minority_roles": d.minority_identities,
                }
                for d in sorted(rep.decisions, key=lambda d: d.pin)
                if d.needs_switch
            ],
        }
    finally:
        conn.close()


@app.get("/api/pins/{package}/switch-cells.csv", response_class=PlainTextResponse)
def pins_switch_csv(package: str) -> str:
    conn = _conn()
    try:
        rep = se.package_report(conn, package)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=sr.CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(sr.to_csv_rows(rep))
        return buf.getvalue()
    finally:
        conn.close()
