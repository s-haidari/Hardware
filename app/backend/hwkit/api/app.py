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
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
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

def _ensure_database() -> None:
    """Build the app-owned pin database from the bundled CubeMX XML if it is
    missing, so a fresh checkout works with no external file and no committed
    binary — the database is always produced by our own builder."""
    db = config.stm_database_path()
    src = config.cubemx_source_dir()
    if db.exists() or not src.exists():
        return
    try:
        from ..cubemx import builder
        db.parent.mkdir(parents=True, exist_ok=True)
        builder.build_database(src, db)
    except Exception:  # never block startup; the Database view reports status
        pass


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    _ensure_database()
    yield


app = FastAPI(title="Hardware App", version="0.1.0", lifespan=_lifespan)

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


@app.get("/api/database/status")
def database_status() -> dict:
    src = config.cubemx_source_dir()
    db = config.stm_database_path()
    xml = len(list(src.glob("*.xml"))) if src.exists() else 0
    mcus = 0
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            mcus = int(conn.execute("SELECT COUNT(*) FROM mcu").fetchone()[0])
        except sqlite3.Error:
            mcus = 0
        finally:
            conn.close()
    return {"cubemx_source": str(src), "source_present": src.exists(), "xml_files": xml,
            "database": str(db), "database_present": db.exists(), "mcu_count": mcus}


@app.post("/api/database/build")
def database_build() -> dict:
    """Rebuild the STM pin database from the CubeMX XML (app-owned, from scratch)."""
    from ..cubemx import builder
    src = config.cubemx_source_dir()
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"CubeMX source not found: {src}")
    db = config.stm_database_path()
    if db.exists():
        shutil.copyfile(db, db.with_suffix(db.suffix + ".bak"))
    res = builder.build_database(src, db)
    return {"source": str(src), "database": str(db),
            "mcus": res.mcus, "pins": res.pins, "roles": res.roles, "packages": res.packages}


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


@app.post("/api/library/dedupe")
def library_dedupe(dry_run: bool = False) -> dict:
    from ..library import manage
    removed = manage.dedupe(_libpaths(), dry_run=dry_run)
    return {"removed": removed, "dry_run": dry_run}


class RemoveRequest(BaseModel):
    name: str
    remove_footprint: bool = False
    dry_run: bool = False


@app.post("/api/library/remove")
def library_remove(body: RemoveRequest) -> dict:
    from ..library import manage
    return manage.remove_part(_libpaths(), body.name,
                              remove_footprint=body.remove_footprint, dry_run=body.dry_run)


@app.post("/api/library/process-downloads")
def library_process_downloads(clear: bool = True, dry_run: bool = False) -> dict:
    from ..library import manage
    res = manage.process_downloads(_libpaths(), config.downloads_dir(),
                                   clear=clear, dry_run=dry_run)
    return {
        "downloads_dir": str(config.downloads_dir()),
        "imported": res.imported, "cleared": res.cleared, "warnings": res.warnings,
    }


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


class NetclassApply(BaseModel):
    project_path: str
    dry_run: bool = True


@app.post("/api/netclasses/apply")
def netclasses_apply(body: NetclassApply) -> dict:
    """Apply the vault netclass standard into a KiCad project's net_settings."""
    from ..netdeck import project as nproject
    p = Path(body.project_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"project not found: {p}")
    sp = config.netclass_standard_path()
    if not sp.exists():
        raise HTTPException(status_code=404, detail=f"netclass standard not found: {sp}")
    classes = nc.to_classes(nc.load(sp))
    res = nproject.apply_netclasses(p, classes, dry_run=body.dry_run)
    return {
        "project": res.project, "dry_run": res.dry_run, "changed": res.changed,
        "classes": res.classes, "patterns": res.patterns,
    }


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


@app.post("/api/authority/generate")
def authority_generate(package: str, out_dir: str | None = None) -> dict:
    """Generate the canonical pinout authority (YAML + JSON + raw TSV) for a package."""
    from ..pins import authority
    conn = _conn()
    try:
        target = Path(out_dir) if out_dir else config.authority_dir()
        return authority.write_authority(conn, package, target)
    finally:
        conn.close()


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


@app.get("/api/pins/{package}/matrix")
def pins_matrix(package: str) -> dict:
    from ..pins import matrix
    db = config.stm_database_path()
    if not db.exists():
        raise HTTPException(status_code=503, detail=f"STM database not found: {db}")
    return matrix.package_matrix(db, package)


@app.get("/api/pins/{package}/validate")
def pins_validate(package: str) -> dict:
    from ..pins import matrix
    db = config.stm_database_path()
    if not db.exists():
        raise HTTPException(status_code=503, detail=f"STM database not found: {db}")
    return matrix.package_validation(db, package)


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


@app.get("/api/pins/{package}/report.html", response_class=HTMLResponse)
def pins_report_html(package: str) -> str:
    conn = _conn()
    try:
        rep = se.package_report(conn, package)
        return sr.to_html([rep], title=f"{package} switch-cell report")
    finally:
        conn.close()


@app.get("/api/pins/{package}/report.md", response_class=PlainTextResponse)
def pins_report_md(package: str) -> str:
    conn = _conn()
    try:
        return sr.to_markdown(se.package_report(conn, package))
    finally:
        conn.close()


@app.get("/api/library/tree")
def library_tree() -> list[dict]:
    lp = _libpaths()
    return catalog.tree(lp.symbols, lp.footprints, lp.models)


@app.get("/api/paths")
def paths_get() -> dict:
    return {
        "repo": str(config.repo_root()), "libs": str(config.libs_root()),
        "downloads": str(config.downloads_dir()), "database": str(config.stm_database_path()),
        "cubemx": str(config.cubemx_source_dir()),
    }


class OpenReq(BaseModel):
    path: str


@app.post("/api/open")
def open_path(body: OpenReq) -> dict:
    import os
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {p}")
    try:
        os.startfile(str(p))  # noqa: platform-specific (Windows)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


# ── git panel ──────────────────────────────────────────────────────────────
@app.get("/api/git/status")
def git_status() -> dict:
    from .. import git_ops
    return git_ops.status(config.repo_root())


@app.get("/api/git/commits")
def git_commits(n: int = 40) -> list[dict]:
    from .. import git_ops
    return git_ops.commits(config.repo_root(), n)


@app.get("/api/git/diff/{ref}", response_class=PlainTextResponse)
def git_diff(ref: str) -> str:
    from .. import git_ops
    return git_ops.diff(config.repo_root(), ref)


@app.post("/api/git/pull")
def git_pull() -> dict:
    from .. import git_ops
    return git_ops.pull(config.repo_root())


@app.post("/api/git/push")
def git_push() -> dict:
    from .. import git_ops
    return git_ops.push(config.repo_root())


class CommitReq(BaseModel):
    message: str


@app.post("/api/git/commit")
def git_commit(body: CommitReq) -> dict:
    from .. import git_ops
    return git_ops.stage_commit(config.repo_root(), body.message)


class CheckoutReq(BaseModel):
    ref: str


@app.post("/api/git/checkout")
def git_checkout(body: CheckoutReq) -> dict:
    from .. import git_ops
    return git_ops.checkout(config.repo_root(), body.ref)


# Serve the built React UI (when present) from the same origin, so the app can
# run as one process in a browser without Tauri. Mounted last so /api/* wins.
_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if (_dist / "index.html").exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
