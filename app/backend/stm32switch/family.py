"""
family.py — the STM32F-only filter.

The whole new pipeline is STM32F-only: only STM32F-series targets are supported
on the Victim Card.  This module is the single place that decides what "STM32F"
means and resolves the F-only MCU set for a package.  Everything downstream
(grouping, voltage, rules) is recomputed from this subset, because the
precomputed analysis tables in the database mix all STM32 families together and
cannot be filtered after the fact.
"""
from __future__ import annotations

import sqlite3

# An MCU is supported when its family starts with this prefix: STM32F0/F1/F2/
# F3/F4/F7.  Everything else (G0/G4/H7/L0/L4/U5/WB/MP1/…) is out of scope.
SUPPORTED_PREFIX = "STM32F"

# SQL fragment reused by every F-only query (alias the mcu table as ``m``).
F_FAMILY_SQL = "m.family LIKE 'STM32F%'"


def is_supported(family: str | None) -> bool:
    """True when ``family`` is an STM32F-series family."""
    return bool(family) and str(family).upper().startswith(SUPPORTED_PREFIX)


def f_mcus(conn: sqlite3.Connection, package: str) -> list[dict]:
    """All STM32F MCUs in a package: id, part_number, family, series, line.

    Ordered deterministically by part number so grouping / representative
    selection is reproducible.
    """
    rows = conn.execute(
        f"""
        SELECT m.id           AS id,
               m.part_number  AS part_number,
               m.family       AS family,
               m.series       AS series,
               m.line         AS line
          FROM mcu m
         WHERE m.package_name = ? AND {F_FAMILY_SQL}
         ORDER BY m.part_number
        """,
        (package,),
    ).fetchall()
    return [dict(r) for r in rows]


def f_mcu_ids(conn: sqlite3.Connection, package: str) -> list[int]:
    """The F-only MCU row ids for a package (for ``IN (...)`` filtering)."""
    return [int(m["id"]) for m in f_mcus(conn, package)]


def f_mcu_count(conn: sqlite3.Connection, package: str) -> int:
    return len(f_mcu_ids(conn, package))
