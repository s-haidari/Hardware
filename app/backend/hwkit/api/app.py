"""
app.py — the unified Hardware app backend (FastAPI).

Run:  python -m uvicorn hwkit.api.app:app --port 8799   (from app/backend, venv active)

Mounts the folded-in domains. ``pins`` (STM32 switch fabric) is live; the
``library`` / ``netdeck`` routers attach here as those modules are ported.
"""
from __future__ import annotations

import csv
import dataclasses
import io
import shutil
import sqlite3
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from ..core import config
from ..kicad import libtable as klibtable
from ..kicad import render as krender
from ..kicad import schematic as kschematic
from ..library import catalog
from ..library.importer import LibPaths, import_part
from ..netdeck import netclasses as nc
from ..pins import switch_engine as se
from ..pins import switch_report as sr

app = FastAPI(title="Hardware App", version="0.1.0")

# Local desktop app: the packaged webview (tauri://) and the dev server
# (localhost:5173) both call the backend on 127.0.0.1, so allow any local origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _libpaths() -> LibPaths:
    root = config.libs_root()
    return LibPaths(
        symbols=root / "MySymbols.kicad_sym",
        footprints=root / "MyFootprints.pretty",
        models=root / "My3DModels",
    )


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


@app.get("/api/library/audit")
def library_audit() -> dict:
    lp = _libpaths()
    a = catalog.audit(lp.symbols, lp.footprints, lp.models)
    return {
        "libs_root": str(config.libs_root()),
        "symbols": a.symbols,
        "footprints": a.footprints,
        "models": a.models,
        "healthy": a.healthy,
        "symbols_bad_nickname": a.symbols_bad_nickname,
        "footprints_missing_model": a.footprints_missing_model,
        "summary": {
            "symbols_bad_nickname": len(a.symbols_bad_nickname),
            "footprints_missing_model": len(a.footprints_missing_model),
        },
    }


@app.get("/api/library/catalog")
def library_catalog() -> list[dict]:
    lp = _libpaths()
    return [dataclasses.asdict(p) for p in catalog.list_symbols(lp.symbols)]


@app.get("/api/library/footprint/{name}/svg", response_class=Response)
def library_footprint_svg(name: str) -> Response:
    fp = _libpaths().footprints / f"{name}.kicad_mod"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"footprint not found: {name}")
    svg = krender.footprint_svg(fp.read_text(encoding="utf-8", errors="replace"))
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/library/import")
async def library_import(file: UploadFile = File(...)) -> dict:
    """Import a part (.zip from easyeda2kicad / JLCPCB) into the shared library,
    guaranteeing the footprint nickname + 3D-model link are correct."""
    suffix = Path(file.filename or "upload.zip").suffix or ".zip"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        with tmp:
            shutil.copyfileobj(file.file, tmp)
        result = import_part(Path(tmp.name), _libpaths())
        return dataclasses.asdict(result)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.post("/api/library/register")
def library_register(dry_run: bool = True) -> dict:
    """Register MySymbols/MyFootprints in KiCad and define ${MY3DMODELS} so the
    importer's output resolves in KiCad with no manual setup. Dry-run by default."""
    cfg = config.kicad_config_dir()
    if cfg is None:
        raise HTTPException(status_code=404, detail="KiCad config dir not found")
    res = klibtable.register_libraries(
        cfg, config.libs_root(), config.MODEL_DIR_VAR, dry_run=dry_run)
    return {
        "kicad_config_dir": str(cfg),
        "dry_run": res.dry_run,
        "changed": res.changed,
        "sym_lib_added": res.sym_lib_added,
        "fp_lib_added": res.fp_lib_added,
        "env_var_set": res.env_var_set,
    }


class RepairRequest(BaseModel):
    path: str
    dry_run: bool = True


@app.post("/api/library/repair-schematic")
def library_repair_schematic(req: RepairRequest) -> dict:
    """Fix Footprint fields on symbols already placed in a .kicad_sch — only for
    parts in the shared library. Dry-run by default; writes a .bak when applied."""
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"schematic not found: {p}")
    known = catalog.known_footprint_names(_libpaths().footprints)
    changes = kschematic.repair_schematic_file(p, known, dry_run=req.dry_run)
    return {
        "path": str(p),
        "dry_run": req.dry_run,
        "count": len(changes),
        "changes": [{"from": a, "to": b} for a, b in changes],
    }


@app.get("/api/netclasses")
def netclasses_get() -> dict:
    p = config.netclass_standard_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"netclass standard not found: {p}")
    data = nc.load(p)
    return {"path": str(p), "meta": dict(data.get("meta", {})), "classes": nc.to_classes(data)}


class NetclassUpdate(BaseModel):
    classes: list[dict]


@app.put("/api/netclasses")
def netclasses_put(body: NetclassUpdate) -> dict:
    """Write the netclass standard back (preserving header/meta), after a .bak."""
    p = config.netclass_standard_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"netclass standard not found: {p}")
    p.with_suffix(p.suffix + ".bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    data = nc.load(p)
    nc.replace_classes(data, body.classes)
    nc.save(p, data)
    return {"path": str(p), "classes": len(body.classes), "saved": True}


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
