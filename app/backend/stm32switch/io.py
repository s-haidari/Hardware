"""
io.py — read-only adapter over the analyzed ``stm32_profiles.sqlite``.

Every accessor returns plain dicts/lists in deterministic order so the
downstream generators are reproducible.  Nothing here writes to the database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .paths import default_db_path

_SEVERITY_RANK = {"critical": 3, "moderate": 2, "minor": 1}


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    if not path.exists():
        raise FileNotFoundError(f"database not found: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


# ── package discovery ──────────────────────────────────────────────────────

def available_packages(conn: sqlite3.Connection) -> list[str]:
    return [r["package_name"] for r in _rows(
        conn,
        "SELECT DISTINCT package_name FROM parent_lane_stability "
        "WHERE package_name IS NOT NULL ORDER BY package_name",
    )]


def package_mcu_count(conn: sqlite3.Connection, package: str) -> int:
    r = _rows(conn, "SELECT COUNT(*) n FROM mcu WHERE package_name=?", (package,))
    return int(r[0]["n"]) if r else 0


# ── per-pin spine: card role cell + parent lane stability ──────────────────

def package_pins(conn: sqlite3.Connection, package: str) -> list[dict]:
    """One row per physical socket pin, joining the card role-cell plane with
    the parent lane-stability plane.  This is the spine of the package matrix."""
    return _rows(conn, """
        SELECT c.physical_pin_number          AS pin,
               c.lane_name                     AS lane,
               c.lqfp_side                     AS side,
               c.cell_kind                     AS db_cell_kind,
               c.role_set                      AS role_set,
               c.distinct_role_count           AS distinct_role_count,
               c.needs_role_switch             AS needs_role_switch,
               c.dominant_electrical_role       AS dominant_role,
               c.safe_default                   AS safe_default,
               c.mcu_count                      AS mcu_count,
               s.stability_class                AS stability_class,
               s.dominant_role                  AS stability_dominant_role,
               s.dominant_role_pct              AS dominant_role_pct,
               s.switch_required                AS switch_required,
               s.switch_type_required           AS switch_type_required,
               s.hardwire_safe                  AS hardwire_safe
          FROM card_pin_role_cell c
     LEFT JOIN parent_lane_stability s
            ON s.package_name = c.package_name
           AND s.physical_pin_number = c.physical_pin_number
         WHERE c.package_name = ?
         ORDER BY c.physical_pin_number
    """, (package,))


def pin_names(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    """Representative (most common) datasheet pin name + GPIO port/index per pin."""
    out: dict[int, dict] = {}
    rows = _rows(conn, """
        SELECT p.physical_pin_number AS pin, p.canonical_pin_name AS name,
               p.gpio_port AS port, p.gpio_pin_index AS idx, COUNT(*) AS c
          FROM mcu_package_pin p
          JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ?
         GROUP BY p.physical_pin_number, p.canonical_pin_name, p.gpio_port, p.gpio_pin_index
         ORDER BY p.physical_pin_number, c DESC, p.canonical_pin_name
    """, (package,))
    for r in rows:
        out.setdefault(int(r["pin"]), {
            "name": r["name"], "port": r["port"], "idx": r["idx"],
        })
    return out


def pin_services(conn: sqlite3.Connection, package: str) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for r in _rows(conn, """
        SELECT psl.physical_pin_number AS pin, psl.service_name AS svc
          FROM profile_service_lane psl
          JOIN mcu m ON m.id = psl.mcu_id
         WHERE m.package_name = ?
    """, (package,)):
        out.setdefault(int(r["pin"]), set()).add(str(r["svc"]))
    return out


def boot_uart_lanes(conn: sqlite3.Connection, package: str) -> dict[int, set[str]]:
    """Physical pins that carry the *bootloader* UART (not every UART usage).

    Sourced from ``mcu_boot_debug_rule`` so the UART router stays sparse —
    only the system-boot UART TX/RX candidate lanes, mapped by canonical pin
    name to physical pins across the package's MCU variants.
    """
    out: dict[int, set[str]] = {}
    for col, svc in (("uart_boot_tx_pin", "uart_tx"), ("uart_boot_rx_pin", "uart_rx")):
        for r in _rows(conn, f"""
            SELECT DISTINCT p.physical_pin_number AS pin
              FROM mcu_boot_debug_rule b
              JOIN mcu m ON m.id = b.mcu_id
              JOIN mcu_package_pin p
                ON p.mcu_id = m.id AND p.canonical_pin_name = b.{col}
             WHERE m.package_name = ? AND b.{col} IS NOT NULL
        """, (package,)):
            out.setdefault(int(r["pin"]), set()).add(svc)
    return out


def pin_capabilities(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for r in _rows(conn,
                   "SELECT * FROM pin_capability WHERE package_name=?", (package,)):
        out[int(r["physical_pin_number"])] = r
    return out


def pin_exact_functions(conn: sqlite3.Connection, package: str) -> dict[int, list[dict]]:
    """Per physical pin: the *exact* CubeMX functions (USART1_TX, SYS_JTMS-SWDIO,
    TIM3_CH2, ...) aggregated across the package's MCUs — never broadened."""
    out: dict[int, list[dict]] = {}
    for r in _rows(conn, """
        SELECT p.physical_pin_number AS pin, f.function_name AS fn,
               f.peripheral AS periph, f.signal AS sig, f.af_number AS af,
               f.peripheral_category AS cat, COUNT(DISTINCT p.mcu_id) AS n
          FROM pin_function f
          JOIN mcu_package_pin p ON p.id = f.mcu_package_pin_id
          JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ?
         GROUP BY p.physical_pin_number, f.function_name, f.peripheral,
                  f.signal, f.af_number, f.peripheral_category
         ORDER BY p.physical_pin_number, f.function_name
    """, (package,)):
        out.setdefault(int(r["pin"]), []).append({
            "function_name": r["fn"], "peripheral": r["periph"] or "",
            "signal": r["sig"] or "", "af_number": r["af"] or "",
            "category": r["cat"] or "", "mcu_count": int(r["n"]),
        })
    return out


def profile_service_lanes(conn: sqlite3.Connection, package: str) -> list[dict]:
    """Per-MCU service→pin assignments (part, service, pin) for the package."""
    return _rows(conn, """
        SELECT m.part_number AS part, psl.service_name AS svc,
               psl.physical_pin_number AS pin
          FROM profile_service_lane psl
          JOIN mcu m ON m.id = psl.mcu_id
         WHERE m.package_name = ?
    """, (package,))


def package_voltage(conn: sqlite3.Connection, package: str) -> dict:
    """VTARGET range + which special-rail branches the package needs.

    Per-rail min/typ/max voltages are not in the DB (effective_power_rule only
    carries VTARGET), so VDDA/VREF/VBAT/VCAP are reported by pin *presence*.
    """
    env = voltage_envelope(conn, package) or {}
    pres = _rows(conn, """
        SELECT SUM(role_set LIKE '%VDDA%') AS vdda,
               SUM(role_set LIKE '%VREF%') AS vref,
               SUM(role_set LIKE '%VBAT%') AS vbat,
               SUM(role_set LIKE '%VCAP%') AS vcap
          FROM card_pin_role_cell WHERE package_name = ?
    """, (package,))
    p = pres[0] if pres else {}
    return {
        "vtarget_min_mv": env.get("vtarget_min_mv"),
        "vtarget_max_mv": env.get("vtarget_max_mv"),
        "family_count": env.get("family_count"),
        "mcu_count": env.get("mcu_count"),
        "vdda_pins": int(p.get("vdda") or 0),
        "vref_pins": int(p.get("vref") or 0),
        "vbat_pins": int(p.get("vbat") or 0),
        "vcap_pins": int(p.get("vcap") or 0),
    }


def pin_conflicts(conn: sqlite3.Connection, package: str) -> dict[int, str]:
    """Worst lane-conflict severity per pin (critical/moderate/minor)."""
    out: dict[int, str] = {}
    for r in _rows(conn,
                   "SELECT physical_pin_number AS pin, conflict_severity AS sev "
                   "FROM lane_conflict_analysis WHERE package_name=?", (package,)):
        pin = int(r["pin"]); sev = str(r["sev"])
        if _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(out.get(pin, ""), 0):
            out[pin] = sev
    return out


# ── exact pinout groups ────────────────────────────────────────────────────

def pinout_groups(conn: sqlite3.Connection, package: str) -> list[dict]:
    return _rows(conn, """
        SELECT group_id, mcu_count, coverage_pct, deviation_fingerprint,
               deviation_summary, mcu_list
          FROM mcu_pinout_group
         WHERE package_name = ?
         ORDER BY group_id
    """, (package,))


def mcu_pin_detail(conn: sqlite3.Connection, part_number: str) -> dict[int, dict]:
    """Exact per-pin detail for one representative MCU: datasheet pin name,
    electrical class, GPIO port, and the full exact-function list. Used to show
    what an exact pinout group does at each socket pin."""
    detail: dict[int, dict] = {}
    for r in _rows(conn, """
        SELECT p.physical_pin_number AS pin, p.canonical_pin_name AS name,
               p.electrical_class AS ec, p.gpio_port AS port, p.gpio_pin_index AS idx
          FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id
         WHERE m.part_number = ?
    """, (part_number,)):
        detail[int(r["pin"])] = {"name": r["name"], "eclass": r["ec"],
                                 "port": r["port"], "idx": r["idx"], "functions": []}
    for r in _rows(conn, """
        SELECT p.physical_pin_number AS pin, f.function_name AS fn
          FROM pin_function f JOIN mcu_package_pin p ON p.id = f.mcu_package_pin_id
          JOIN mcu m ON m.id = p.mcu_id
         WHERE m.part_number = ?
         ORDER BY f.function_name
    """, (part_number,)):
        d = detail.get(int(r["pin"]))
        if d is not None:
            d["functions"].append(r["fn"])
    return detail


def mcu_index(conn: sqlite3.Connection, package: str) -> dict[str, dict]:
    """part_number -> {family, series, line} for member resolution."""
    out: dict[str, dict] = {}
    for r in _rows(conn,
                   "SELECT part_number, family, series, line FROM mcu WHERE package_name=?",
                   (package,)):
        out[str(r["part_number"])] = r
    return out


# ── passes ─────────────────────────────────────────────────────────────────

def coverage_passes(conn: sqlite3.Connection, package: str) -> list[dict]:
    return _rows(conn, """
        SELECT pass_number, mcu_group_id, mcus_newly_enabled, cumulative_mcu_count,
               cumulative_pct, is_baseline, parent_additions_count
          FROM coverage_pass
         WHERE package_name = ?
         ORDER BY pass_number
    """, (package,))


def pass_additions(conn: sqlite3.Connection, package: str) -> list[dict]:
    return _rows(conn, """
        SELECT pass_number, lane_name, service_name, component_category,
               switch_type, input_net, output_net, enable_net, quantity, reason
          FROM parent_pass_addition
         WHERE package_name = ?
         ORDER BY pass_number, lane_name, service_name
    """, (package,))


# ── package-level context ──────────────────────────────────────────────────

def voltage_envelope(conn: sqlite3.Connection, package: str) -> dict | None:
    r = _rows(conn, """
        SELECT vtarget_min_mv, vtarget_max_mv, vtarget_span_mv, mcu_count, family_count
          FROM voltage_envelope WHERE package_name=?
    """, (package,))
    return r[0] if r else None


def hardware_cells(conn: sqlite3.Connection) -> list[dict]:
    return _rows(conn, """
        SELECT cell_key, title, board_side, category, description,
               kicad_symbol, default_value, sort_order
          FROM hardware_cell_catalog ORDER BY sort_order, cell_key
    """)


def switch_network(conn: sqlite3.Connection, package: str) -> list[dict]:
    return _rows(conn, """
        SELECT physical_pin_number, lane_name, network_name, network_class,
               source_signal, target_signal, required_cell_name, switch_type,
               control_strategy, safe_default
          FROM switch_network_requirement WHERE package_name=?
         ORDER BY physical_pin_number, network_name
    """, (package,))
