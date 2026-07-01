"""
fdata.py — STM32F-only raw reads from the analyzed database.

These accessors deliberately bypass the precomputed analysis tables
(``card_pin_role_cell``, ``parent_lane_stability``, ``mcu_pinout_group``,
``coverage_pass``, ``pin_capability``, ``voltage_envelope``) because those are
computed across *all* STM32 families and cannot be filtered to STM32F after the
fact.  Everything here is rebuilt from the raw per-MCU import tables
(``mcu``, ``mcu_package_pin``, ``pin_function``, ``mcu_boot_debug_rule``,
``effective_power_rule``) restricted to ``family LIKE 'STM32F%'``.

Each accessor returns plain dicts/lists in deterministic order so the generator
is reproducible.  Nothing here writes to the database.
"""
from __future__ import annotations

import sqlite3

from .family import F_FAMILY_SQL, f_mcus

# Service tokens derived from the boot/debug rule, keyed by its pin-name column.
_BOOT_DEBUG_COLUMNS = {
    "swdio_pin_name":  "swdio",
    "swclk_pin_name":  "swclk",
    "swo_pin_name":    "swo",
    "nrst_pin_name":   "nrst",
    "boot0_pin_name":  "boot0",
    "uart_boot_tx_pin": "uart_tx",
    "uart_boot_rx_pin": "uart_rx",
    "usb_dplus_pin":   "usb_dp",
    "usb_dminus_pin":  "usb_dm",
}


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


# ── per-MCU pin maps (the grouping signature source) ───────────────────────

def mcu_pin_maps(conn: sqlite3.Connection, package: str) -> dict[int, dict[int, dict]]:
    """``{mcu_id: {physical_pin: {name, eclass, port, idx, side}}}`` for F MCUs.

    This is the exact per-pin pinout of every STM32F variant in the package and
    is the basis for both the exact-group signature and the per-group pin detail.
    """
    out: dict[int, dict[int, dict]] = {}
    for r in _rows(conn, f"""
        SELECT p.mcu_id              AS mcu_id,
               p.physical_pin_number AS pin,
               p.canonical_pin_name  AS name,
               p.electrical_class    AS eclass,
               p.gpio_port           AS port,
               p.gpio_pin_index      AS idx,
               p.lqfp_side           AS side
          FROM mcu_package_pin p
          JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL}
         ORDER BY p.mcu_id, p.physical_pin_number
    """, (package,)):
        out.setdefault(int(r["mcu_id"]), {})[int(r["pin"])] = {
            "name": r["name"] or "", "eclass": r["eclass"] or "nc",
            "port": r["port"], "idx": r["idx"], "side": r["side"] or "",
        }
    return out


def pin_detail_for_mcu(conn: sqlite3.Connection, mcu_id: int) -> dict[int, dict]:
    """Exact per-pin detail + exact functions for one representative MCU id.

    Mirrors the shape the UI explorer needs: pin → {name, eclass, port, idx,
    functions:[...]}.
    """
    detail: dict[int, dict] = {}
    for r in _rows(conn, """
        SELECT physical_pin_number AS pin, canonical_pin_name AS name,
               electrical_class AS eclass, gpio_port AS port, gpio_pin_index AS idx
          FROM mcu_package_pin WHERE mcu_id = ?
    """, (mcu_id,)):
        detail[int(r["pin"])] = {"name": r["name"], "eclass": r["eclass"],
                                 "port": r["port"], "idx": r["idx"], "functions": []}
    for r in _rows(conn, """
        SELECT p.physical_pin_number AS pin, f.function_name AS fn
          FROM pin_function f JOIN mcu_package_pin p ON p.id = f.mcu_package_pin_id
         WHERE p.mcu_id = ?
         ORDER BY f.function_name
    """, (mcu_id,)):
        d = detail.get(int(r["pin"]))
        if d is not None:
            d["functions"].append(r["fn"])
    return detail


# ── aggregated exact functions per physical pin (across all F MCUs) ─────────

def exact_functions(conn: sqlite3.Connection, package: str) -> dict[int, list[dict]]:
    """Per physical pin: the exact CubeMX functions aggregated across F MCUs."""
    out: dict[int, list[dict]] = {}
    for r in _rows(conn, f"""
        SELECT p.physical_pin_number AS pin, f.function_name AS fn,
               f.peripheral AS periph, f.signal AS sig, f.af_number AS af,
               f.peripheral_category AS cat, COUNT(DISTINCT p.mcu_id) AS n
          FROM pin_function f
          JOIN mcu_package_pin p ON p.id = f.mcu_package_pin_id
          JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL}
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


# ── capability flags, recomputed F-only from function categories ───────────

_CATEGORY_FLAGS = {
    "has_adc":   ("adc",),
    "has_dac":   ("dac",),
    "has_timer": ("timer", "tim"),
    "has_uart":  ("uart", "usart", "lpuart"),
    "has_spi":   ("spi",),
    "has_i2c":   ("i2c",),
    "has_can":   ("can",),
    "has_usb":   ("usb", "otg"),
    "has_audio": ("sai", "i2s", "audio"),
    "has_ethernet": ("eth",),
}


def pin_capabilities(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    """``{pin: {has_adc:bool, …, has_analog:bool}}`` recomputed from F functions."""
    funcs = exact_functions(conn, package)
    out: dict[int, dict] = {}
    for pin, flist in funcs.items():
        haystacks = [
            f"{f['category']} {f['peripheral']} {f['signal']}".lower() for f in flist
        ]
        cap = {flag: False for flag in _CATEGORY_FLAGS}
        for flag, toks in _CATEGORY_FLAGS.items():
            cap[flag] = any(any(t in h for t in toks) for h in haystacks)
        cap["has_analog"] = cap["has_adc"] or cap["has_dac"] or any(
            ("comp" in h or "opamp" in h) for h in haystacks)
        out[pin] = cap
    return out


# ── boot / debug service pins (names resolved to physical pins) ─────────────

def service_pins(conn: sqlite3.Connection, package: str) -> dict[int, set[str]]:
    """``{pin: {service tokens}}`` from the boot/debug rule across F MCUs.

    The rule stores pin *names* (e.g. ``swdio_pin_name='PA13'``); we resolve
    each to a physical pin via that MCU's own pinout, then union across MCUs.
    """
    out: dict[int, set[str]] = {}
    for col, svc in _BOOT_DEBUG_COLUMNS.items():
        for r in _rows(conn, f"""
            SELECT DISTINCT p.physical_pin_number AS pin
              FROM mcu_boot_debug_rule b
              JOIN mcu m ON m.id = b.mcu_id
              JOIN mcu_package_pin p
                ON p.mcu_id = m.id AND p.canonical_pin_name = b.{col}
             WHERE m.package_name = ? AND {F_FAMILY_SQL} AND b.{col} IS NOT NULL
        """, (package,)):
            out.setdefault(int(r["pin"]), set()).add(svc)
    return out


# ── voltage envelope, recomputed F-only ────────────────────────────────────

def voltage(conn: sqlite3.Connection, package: str) -> dict:
    """VTARGET min/max across F MCUs + special-rail pin presence counts."""
    mcu_ids = [int(m["id"]) for m in f_mcus(conn, package)]
    fams = {m["family"] for m in f_mcus(conn, package)}
    vmin = vmax = None
    if mcu_ids:
        placeholders = ",".join("?" * len(mcu_ids))
        r = _rows(conn, f"""
            SELECT MIN(effective_min_mv) AS lo, MAX(effective_max_mv) AS hi
              FROM effective_power_rule
             WHERE rail_name = 'VTARGET' AND mcu_id IN ({placeholders})
        """, tuple(mcu_ids))
        if r and r[0]["lo"] is not None:
            vmin, vmax = int(r[0]["lo"]), int(r[0]["hi"])

    # Special-rail presence from name + electrical class across F MCUs.
    pres = _rows(conn, f"""
        SELECT
          SUM(p.electrical_class='power'  AND UPPER(p.canonical_pin_name) LIKE '%VDDA%') AS vdda,
          SUM(p.electrical_class='power'  AND UPPER(p.canonical_pin_name) LIKE '%VREF%') AS vref,
          SUM(p.electrical_class='power'  AND UPPER(p.canonical_pin_name) LIKE '%VBAT%') AS vbat,
          SUM(p.electrical_class='vcap') AS vcap
        FROM mcu_package_pin p
        JOIN mcu m ON m.id = p.mcu_id
       WHERE m.package_name = ? AND {F_FAMILY_SQL}
    """, (package,))
    p = pres[0] if pres else {}
    vdda_pins, vref_pins = int(p.get("vdda") or 0), int(p.get("vref") or 0)
    vbat_pins, vcap_pins = int(p.get("vbat") or 0), int(p.get("vcap") or 0)
    return {
        "vtarget_min_mv": vmin, "vtarget_max_mv": vmax,
        # Volts alias + derived target-branch plan. The app owns its data, so a
        # target branch is "required" (planned) exactly when the package carries
        # pins of that rail — validate.py checks pins-present-implies-branch, and
        # deriving the flag from the same pin presence keeps that invariant true.
        "vtarget_min_v": (vmin / 1000.0) if vmin is not None else None,
        "vtarget_max_v": (vmax / 1000.0) if vmax is not None else None,
        "vdda_target_required": vdda_pins > 0,
        "vref_target_required": vref_pins > 0,
        "vbat_target_required": vbat_pins > 0,
        "vcap_branch_required": vcap_pins > 0,
        "mcu_count": len(mcu_ids), "family_count": len(fams),
        "vdda_pins": vdda_pins, "vref_pins": vref_pins,
        "vbat_pins": vbat_pins, "vcap_pins": vcap_pins,
    }


# ── physical side per pin (majority across F MCUs) ─────────────────────────

def pin_sides(conn: sqlite3.Connection, package: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for r in _rows(conn, f"""
        SELECT p.physical_pin_number AS pin, p.lqfp_side AS side, COUNT(*) AS c
          FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL} AND p.lqfp_side IS NOT NULL
         GROUP BY p.physical_pin_number, p.lqfp_side
         ORDER BY p.physical_pin_number, c DESC
    """, (package,)):
        out.setdefault(int(r["pin"]), r["side"] or "")
    return out


def mcu_meta(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    """``{mcu_id: {part, family, series, line, max_mhz}}`` for F MCUs."""
    out: dict[int, dict] = {}
    for r in _rows(conn, f"""
        SELECT m.id AS id, m.part_number AS part, m.family AS family,
               m.series AS series, m.line AS line, m.max_frequency_mhz AS mhz
          FROM mcu m WHERE m.package_name = ? AND {F_FAMILY_SQL}
    """, (package,)):
        out[int(r["id"])] = {"part": r["part"], "family": r["family"] or "UNKNOWN",
                             "series": r["series"] or "", "line": r["line"] or "",
                             "max_mhz": r["mhz"]}
    return out


def mcu_voltage(conn: sqlite3.Connection, package: str) -> dict[int, tuple]:
    """``{mcu_id: (vmin_mv, vmax_mv)}`` for the VTARGET rail of each F MCU."""
    out: dict[int, tuple] = {}
    for r in _rows(conn, f"""
        SELECT e.mcu_id AS id, e.effective_min_mv AS lo, e.effective_max_mv AS hi
          FROM effective_power_rule e
          JOIN mcu m ON m.id = e.mcu_id
         WHERE e.rail_name = 'VTARGET' AND m.package_name = ? AND {F_FAMILY_SQL}
    """, (package,)):
        out[int(r["id"])] = (r["lo"], r["hi"])
    return out


def mcu_flags(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    """``{mcu_id: {has_vcap, has_usb, has_boot_uart}}`` for F MCUs."""
    out: dict[int, dict] = {}
    for r in _rows(conn, f"""
        SELECT m.id AS id FROM mcu m WHERE m.package_name = ? AND {F_FAMILY_SQL}
    """, (package,)):
        out[int(r["id"])] = {"has_vcap": False, "has_usb": False, "has_boot_uart": False}
    for r in _rows(conn, f"""
        SELECT DISTINCT p.mcu_id AS id FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL} AND p.electrical_class = 'vcap'
    """, (package,)):
        if int(r["id"]) in out: out[int(r["id"])]["has_vcap"] = True
    for r in _rows(conn, f"""
        SELECT b.mcu_id AS id, b.usb_dplus_pin AS usb, b.uart_boot_tx_pin AS uart
          FROM mcu_boot_debug_rule b JOIN mcu m ON m.id = b.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL}
    """, (package,)):
        d = out.get(int(r["id"]))
        if d:
            d["has_usb"] = d["has_usb"] or bool(r["usb"])
            d["has_boot_uart"] = d["has_boot_uart"] or bool(r["uart"])
    return out


def representative_names(conn: sqlite3.Connection, package: str) -> dict[int, dict]:
    """Most-common datasheet pin name + GPIO port/index per pin across F MCUs."""
    out: dict[int, dict] = {}
    for r in _rows(conn, f"""
        SELECT p.physical_pin_number AS pin, p.canonical_pin_name AS name,
               p.gpio_port AS port, p.gpio_pin_index AS idx, COUNT(*) AS c
          FROM mcu_package_pin p JOIN mcu m ON m.id = p.mcu_id
         WHERE m.package_name = ? AND {F_FAMILY_SQL}
         GROUP BY p.physical_pin_number, p.canonical_pin_name, p.gpio_port, p.gpio_pin_index
         ORDER BY p.physical_pin_number, c DESC, p.canonical_pin_name
    """, (package,)):
        out.setdefault(int(r["pin"]), {
            "name": r["name"], "port": r["port"], "idx": r["idx"]})
    return out
