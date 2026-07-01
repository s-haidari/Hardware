"""
builder.py — build the STM32 pin database (sqlite) from the CubeMX XML set.

Produces mcu / mcu_package_pin / pin_function / pin_role with the schema the app
consumers (switch_engine, authority, matrix) read. Deterministic and verifiable:
switch_engine on the result reproduces the hand-checked ground truth.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .classify import canonical, electrical_class, roles
from .parse import parse_mcu_xml

_SCHEMA = """
CREATE TABLE source_artifact (id INTEGER PRIMARY KEY, path TEXT, imported_at TEXT);
CREATE TABLE mcu (
    id INTEGER PRIMARY KEY, source_artifact_id INTEGER NOT NULL,
    part_number TEXT NOT NULL, family TEXT, line TEXT,
    package_name TEXT, pin_count INTEGER, imported_at TEXT NOT NULL);
CREATE TABLE mcu_package_pin (
    id INTEGER PRIMARY KEY, mcu_id INTEGER NOT NULL, package_name TEXT,
    physical_pin_number INTEGER NOT NULL, canonical_pin_name TEXT NOT NULL,
    raw_pin_name TEXT, pin_type TEXT,
    electrical_class TEXT NOT NULL
        CHECK(electrical_class IN ('io','power','ground','reset','boot','oscillator','vcap','nc')),
    gpio_port TEXT, gpio_pin_index INTEGER,
    lqfp_side TEXT CHECK(lqfp_side IN ('left','bottom','right','top')),
    source_confidence REAL DEFAULT 0.9,
    UNIQUE(mcu_id, physical_pin_number));
CREATE TABLE pin_function (
    id INTEGER PRIMARY KEY, mcu_package_pin_id INTEGER NOT NULL,
    function_name TEXT NOT NULL, signal TEXT, io_modes TEXT,
    function_class TEXT NOT NULL DEFAULT 'other',
    peripheral_category TEXT NOT NULL DEFAULT 'other');
CREATE TABLE pin_role (
    id INTEGER PRIMARY KEY, mcu_package_pin_id INTEGER NOT NULL,
    role_name TEXT NOT NULL, role_class TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.9, reason_code TEXT NOT NULL DEFAULT 'cubemx',
    source_table TEXT NOT NULL DEFAULT 'mcu_package_pin', source_id INTEGER NOT NULL DEFAULT 0,
    analyzer_name TEXT NOT NULL DEFAULT 'cubemx_builder', export_safe INTEGER DEFAULT 1,
    warning_state TEXT DEFAULT 'ok', UNIQUE(mcu_package_pin_id, role_name));
CREATE INDEX ix_pin_mcu ON mcu_package_pin(mcu_id);
CREATE INDEX ix_role_pin ON pin_role(mcu_package_pin_id);
CREATE INDEX ix_func_pin ON pin_function(mcu_package_pin_id);
"""


def lqfp_side(pos: int, n: int) -> str | None:
    if n <= 0 or pos < 1 or pos > n:
        return None
    q = n // 4
    if pos <= q:
        return "left"
    if pos <= 2 * q:
        return "bottom"
    if pos <= 3 * q:
        return "right"
    return "top"


@dataclass
class BuildResult:
    mcus: int
    pins: int
    roles: int
    packages: dict[str, int]


def build_database(source_dir: Path, db_path: Path, *,
                   family_prefix: str = "STM32F", stamp: str = "1970-01-01") -> BuildResult:
    """Build the database from every CubeMX XML under ``source_dir``."""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        art = conn.execute("INSERT INTO source_artifact (path, imported_at) VALUES (?,?)",
                           (str(source_dir), stamp)).lastrowid

        n_mcu = n_pin = n_role = 0
        packages: dict[str, int] = {}
        files = sorted(p for p in source_dir.glob("*.xml") if p.name != "families.xml")
        for f in files:
            mcu = parse_mcu_xml(f)
            if family_prefix and not mcu.family.startswith(family_prefix):
                continue
            # Some XML list the same position twice (bonding variants); keep first.
            seen_pos: set[int] = set()
            mcu.pins = [p for p in mcu.pins if not (p.position in seen_pos or seen_pos.add(p.position))]
            pin_count = len(mcu.pins)
            mcu_id = conn.execute(
                "INSERT INTO mcu (source_artifact_id, part_number, family, line, "
                "package_name, pin_count, imported_at) VALUES (?,?,?,?,?,?,?)",
                (art, mcu.ref_name, mcu.family, mcu.line, mcu.package, pin_count, stamp),
            ).lastrowid
            n_mcu += 1
            packages[mcu.package] = packages.get(mcu.package, 0) + 1

            for pin in mcu.pins:
                ec = electrical_class(pin)
                canon, port, idx = canonical(pin)
                pin_id = conn.execute(
                    "INSERT INTO mcu_package_pin (mcu_id, package_name, physical_pin_number, "
                    "canonical_pin_name, raw_pin_name, pin_type, electrical_class, gpio_port, "
                    "gpio_pin_index, lqfp_side) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (mcu_id, mcu.package, pin.position, canon, pin.name, pin.type, ec,
                     port, idx, lqfp_side(pin.position, pin_count)),
                ).lastrowid
                n_pin += 1
                for s in pin.signals:
                    conn.execute(
                        "INSERT INTO pin_function (mcu_package_pin_id, function_name, signal, io_modes) "
                        "VALUES (?,?,?,?)", (pin_id, s.name, s.name, s.io_modes))
                for rn, rc in roles(pin):
                    conn.execute(
                        "INSERT OR IGNORE INTO pin_role (mcu_package_pin_id, role_name, role_class) "
                        "VALUES (?,?,?)", (pin_id, rn, rc))
                    n_role += 1
        conn.commit()
        return BuildResult(n_mcu, n_pin, n_role, packages)
    finally:
        conn.close()
