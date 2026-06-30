"""
cli.py — command-line interface for the STM32F matrix generator.

    python -m stm32switch build               # all STM32F packages
    python -m stm32switch build    --package LQFP100
    python -m stm32switch validate --strict   # acceptance gate (15 questions)

The pipeline is deterministic: same database in, byte-stable CSVs out.
"""
from __future__ import annotations

import argparse
import sys

from . import io, normalize, schema
from .family import F_FAMILY_SQL
from .paths import TARGET_PACKAGES, default_db_path


def _emit(msg: str) -> None:
    print(msg, flush=True)


def _f_packages(conn, requested: str | None) -> list[str]:
    have = [r["package_name"] for r in conn.execute(
        f"SELECT DISTINCT m.package_name AS package_name FROM mcu m "
        f"WHERE m.package_name IS NOT NULL AND {F_FAMILY_SQL} ORDER BY m.package_name"
    ).fetchall()]
    if requested:
        if requested not in have:
            raise SystemExit(f"package {requested!r} has no STM32F MCUs (have: {', '.join(have)})")
        return [requested]
    ordered = [p for p in TARGET_PACKAGES if p in have]
    return ordered or have


# ── build ───────────────────────────────────────────────────────────────────

def cmd_build(conn, package: str | None) -> int:
    _emit("stm32switch build (STM32F-only)")
    _emit(f"  backplane: {normalize.write_superset_backplane()} lanes")
    for pkg in _f_packages(conn, package):
        pd = normalize.assemble(conn, pkg)
        counts = normalize.write_all(pd)
        n_groups = len(pd.groups)
        n_switch = sum(1 for p, c in pd.contexts.items()
                       if rules_needs_switch(c))
        _emit(f"  {pkg:<8} rows={counts['stm32f_matrix']} groups={n_groups} "
              f"pins={pd.pin_count} switched_pins={n_switch}")
    return 0


def rules_needs_switch(ctx) -> bool:
    from . import rules
    return rules.stability(ctx) in rules._MIXED


# ── validate (acceptance gate) ──────────────────────────────────────────────

def cmd_validate(conn, package: str | None, strict: bool) -> int:
    rc = 0
    for pkg in _f_packages(conn, package):
        pd = normalize.assemble(conn, pkg)
        rows = schema.matrix_rows(pd)
        problems: list[str] = []
        for row in rows:
            for col in schema.DESIGN_REQUIRED_COLUMNS:
                val = str(row.get(col, "")).strip()
                if not val or val.upper() == "UNKNOWN":
                    problems.append(f"pin {row['socket_pin']} group {row['group_label']}: "
                                    f"empty/UNKNOWN {col}")
        missing_cols = [c for c in schema.MATRIX_COLUMNS if rows and c not in rows[0]]
        if missing_cols:
            problems.append(f"missing columns: {', '.join(missing_cols)}")
        switched = sum(1 for r in rows if r["needs_victim_card_switching"] == "yes")
        guaranteed = sum(1 for r in rows if r["is_guaranteed_same_electrical_role"] == "yes")
        helios = sum(1 for r in rows if r["needs_helios_control"] == "yes")
        _emit(f"  {pkg:<8} rows={len(rows)} switched={switched} guaranteed={guaranteed} "
              f"helios={helios} problems={len(problems)}")
        for p in problems[:10]:
            _emit(f"      ! {p}")
        if strict and problems:
            rc = 1
    return rc


# ── arg parsing ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stm32switch",
        description="STM32F Helios / Attack Board / Victim Card matrix generator")
    p.add_argument("--db", default=None, help="path to stm32_profiles.sqlite")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("build", "validate"):
        sp = sub.add_parser(name)
        sp.add_argument("--package", default=None, help="single package, e.g. LQFP100")
        if name == "validate":
            sp.add_argument("--strict", action="store_true",
                            help="exit non-zero if any row fails the acceptance gate")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = io.connect(args.db or str(default_db_path()))
    try:
        if args.command == "build":
            return cmd_build(conn, args.package)
        if args.command == "validate":
            return cmd_validate(conn, args.package, getattr(args, "strict", False))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
