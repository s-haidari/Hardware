"""
schema.py — the canonical STM32F long-matrix schema.

One CSV per package, ``stm32f_<PKG>_matrix.csv``, one row per
(exact STM32F group × socket pin × the role/branch that pin has in that group).
The column order below is the contract; :func:`matrix_rows` builds the rows by
merging DB-sourced facts (identity, per-group pin behavior, capabilities) with
the design decisions from :mod:`rules`.
"""
from __future__ import annotations

import csv
import io as _io

from . import roles as R, rules
from .csvio import cell, write_csv
from .paths import lane_bank, lane_index_in_bank, package_dir

SCHEMA_VERSION = "stm32f-1"
GENERATOR_VERSION = "stm32switch/2"

MATRIX_COLUMNS = [
    # package identity
    "package", "package_pin_count", "package_family_filter", "package_priority",
    "package_supported",
    # exact group identity
    "group_id_internal", "group_label", "group_rank", "group_member_count",
    "group_members", "group_signature_hash", "is_baseline_group", "baseline_delta_summary",
    # MCU membership
    "mcu_part_numbers", "mcu_family", "mcu_series_set", "mcu_count_in_group",
    "representative_mcu", "vmin_mv_group", "vmax_mv_group", "max_mhz_group",
    "has_vcap_group", "has_usb_group", "has_bootloader_uart_group",
    # physical pin / lane identity
    "socket_pin", "socket_side", "socket_side_index", "lane_id", "lane_number",
    "lane_bank", "lane_bank_index", "victim_card_zone", "attack_board_zone",
    # group-specific pin identity
    "datasheet_pin_name", "normalized_pin_name", "port_pin", "gpio_port", "gpio_number",
    "electrical_role_in_group", "special_role_in_group", "voltage_domain_in_group",
    "reset_state_in_group", "boot_state_relevance",
    # alternate function / capability
    "exact_functions_raw", "exact_functions_normalized",
    "has_gpio_capability", "has_adc_capability", "has_dac_capability",
    "has_timer_capability", "has_uart_capability", "has_spi_capability",
    "has_i2c_capability", "has_can_capability", "has_usb_capability",
    "has_swd_capability", "has_trace_capability", "has_clock_capability",
    # cross-group pin summary
    "roles_seen_all_groups", "special_roles_seen_all_groups", "functions_seen_all_groups",
    "group_labels_using_io", "group_labels_using_vdd", "group_labels_using_vdda",
    "group_labels_using_vss", "group_labels_using_vssa", "group_labels_using_vbat",
    "group_labels_using_vref", "group_labels_using_vcap", "group_labels_using_boot",
    "group_labels_using_nrst", "group_labels_using_osc", "group_labels_using_usb",
    "group_labels_using_nc",
    # guarantee / switching classification
    "pin_role_stability", "is_guaranteed_same_electrical_role",
    "is_guaranteed_same_special_role", "is_guaranteed_parent_service_pin",
    "needs_victim_card_switching", "needs_attack_board_routing", "needs_helios_control",
    "switching_reason", "guarantee_reason",
    # victim card implementation
    "victim_card_cell_required", "victim_card_cell_variant", "victim_card_cell_display_name",
    "victim_card_branch_id", "victim_card_branch_display_name", "victim_card_branch_destination",
    "victim_card_branch_groups", "victim_card_required_nets", "victim_card_component_class",
    "victim_card_component_bank_hint", "victim_card_shared_hardware_allowed",
    "victim_card_shared_enable_allowed", "victim_card_default_state",
    "victim_card_placement_zone", "victim_card_notes",
    # branch model (per group)
    "active_branch_role_for_group", "active_branch_destination_for_group",
    "active_branch_control_net_for_group", "active_branch_requires_switch",
    "active_branch_component_class",
    # helios control
    "helios_control_role", "helios_connection_allowed", "helios_connection_method",
    "helios_signal_direction", "helios_control_net", "helios_gpio_requirement",
    "helios_level_safety_required", "helios_default_state", "helios_firmware_action",
    "helios_notes",
    # attack board breakout
    "attack_board_access_required", "attack_board_access_class", "attack_board_standard_port",
    "attack_board_standard_port_label", "attack_board_router_required", "attack_board_router_id",
    "attack_board_router_input_role", "attack_board_router_direction",
    "attack_board_breakout_priority", "attack_board_user_visible", "attack_board_notes",
    # reusable block mapping
    "implementation_owner", "reusable_block_id", "reusable_block_display_name",
    "reusable_block_location", "reusable_block_scope", "can_share_physical_ic",
    "share_group_hint", "independent_enable_required",
    # control fabric
    "control_fabric_required", "control_fabric_type", "control_bit_count",
    "control_select_encoding", "control_enable_net", "control_default_off_required",
    "control_power_off_only_change", "control_interlock_required", "control_readback_required",
    # safety / review
    "safety_class", "review_required", "review_reason", "direct_connection_allowed",
    "direct_connection_reason", "do_not_hardwire_reason", "fault_if_wrong",
    # ui display
    "ui_title", "ui_subtitle", "ui_role_badges", "ui_group_badges", "ui_warning_text",
    "ui_plain_english_summary", "ui_primary_action", "ui_secondary_action",
    "ui_visual_diagram_type", "ui_sort_priority",
    # source / confidence
    "source_kind", "source_file", "source_confidence", "source_notes",
    "schema_version", "generator_version", "last_generated_utc",
]

# Design-layer columns that must never be left UNKNOWN/blank (acceptance gate).
DESIGN_REQUIRED_COLUMNS = [
    "pin_role_stability", "needs_victim_card_switching", "needs_helios_control",
    "victim_card_cell_required", "victim_card_component_class",
    "active_branch_role_for_group", "active_branch_destination_for_group",
    "helios_control_role", "helios_connection_method",
    "attack_board_access_class", "control_fabric_type", "safety_class",
    "implementation_owner", "reusable_block_location",
    "ui_title", "ui_subtitle", "ui_primary_action",
]


# ── group-specific small derivations ───────────────────────────────────────

def _voltage_domain(role: str) -> str:
    return {
        R.VDDA: "VDDA_TARGET", R.VREF: "VREF_TARGET", R.VBAT: "VBAT_TARGET",
        R.VSS: "VSS", R.VSSA: "VSSA", R.VCAP: "VCAP_LOCAL",
    }.get(role, "VTARGET")


def _reset_state(role: str) -> str:
    if role == R.NRST: return "reset_input"
    if role == R.BOOT: return "boot_strap"
    if role in (R.VDD, R.VDDA, R.VBAT, R.VREF, R.VSS, R.VSSA, R.VCAP, R.NC): return "n/a"
    return "input_after_reset"


def _special_role(services: set[str], role: str) -> str:
    m = {"swdio": "SWDIO", "swclk": "SWCLK", "swo": "SWO", "nrst": "NRST",
         "boot0": "BOOT0", "uart_tx": "UART_BOOT_TX", "uart_rx": "UART_BOOT_RX",
         "usb_dp": "USB_DP", "usb_dm": "USB_DM"}
    got = sorted({m[s] for s in services if s in m})
    if got:
        return "|".join(got)
    if role == R.NRST: return "NRST"
    if role == R.BOOT: return "BOOT0"
    if role in (R.OSC_IN, R.OSC_OUT): return "OSC_IN" if role == R.OSC_IN else "OSC_OUT"
    if role in (R.USB_DP, R.USB_DM): return role
    return "NONE"


def _yn(b) -> str:
    return "yes" if b else "no"


# ── row assembly ────────────────────────────────────────────────────────────

def matrix_rows(pd) -> list[dict]:
    """One row per (group, pin).  ``pd`` is a normalize.PackageData."""
    rows: list[dict] = []
    # pin-level (cross-group) design fields are identical across a pin's rows.
    pin_design: dict[int, dict] = {}
    for pin, ctx in pd.contexts.items():
        pin_design[pin] = rules.pin_design_fields(ctx)

    for g in pd.groups:
        meta = pd.group_meta.get(g.code, {})
        for pin in range(1, pd.pin_count + 1):
            ctx = pd.contexts.get(pin)
            if ctx is None:
                continue
            ident = pd.idents[pin]
            role = g.pin_roles.get(pin, R.NC)
            name = g.pin_names.get(pin) or ident.get("name") or ""
            rows.append(_row(pd, g, meta, pin, ctx, ident, role, name, pin_design[pin]))
    return rows


def _row(pd, g, meta, pin, ctx, ident, role, name, design) -> dict:
    caps = ctx.caps
    funcs = ctx.exact_functions
    row = {
        # package identity
        "package": pd.package, "package_pin_count": pd.pin_count,
        "package_family_filter": "STM32F", "package_priority": pd.priority,
        "package_supported": "yes",
        # group identity
        "group_id_internal": g.code, "group_label": g.label, "group_rank": g.rank,
        "group_member_count": g.member_count, "group_members": "|".join(g.members),
        "group_signature_hash": g.signature_hash, "is_baseline_group": _yn(g.is_baseline),
        "baseline_delta_summary": g.delta_notes,
        # MCU membership
        "mcu_part_numbers": "|".join(g.members), "mcu_family": meta.get("family", "UNKNOWN"),
        "mcu_series_set": "|".join(meta.get("series_set", [])) or "UNKNOWN",
        "mcu_count_in_group": g.member_count, "representative_mcu": g.rep_part,
        "vmin_mv_group": meta.get("vmin_mv", "UNKNOWN"),
        "vmax_mv_group": meta.get("vmax_mv", "UNKNOWN"),
        "max_mhz_group": meta.get("max_mhz", "UNKNOWN"),
        "has_vcap_group": _yn(meta.get("has_vcap")),
        "has_usb_group": _yn(meta.get("has_usb")),
        "has_bootloader_uart_group": _yn(meta.get("has_boot_uart")),
        # physical identity
        "socket_pin": pin, "socket_side": ident.get("side", ""),
        "socket_side_index": ident.get("side_index", ""),
        "lane_id": ctx.lane, "lane_number": pin,
        "lane_bank": lane_bank(pin), "lane_bank_index": lane_index_in_bank(pin),
        "victim_card_zone": design["victim_card_placement_zone"],
        "attack_board_zone": design["reusable_block_location"],
        # group-specific pin identity
        "datasheet_pin_name": name,
        "normalized_pin_name": (name or "").upper(),
        "port_pin": f"{ident.get('port') or ''}{ident.get('idx')}"
                    if ident.get("port") and ident.get("idx") is not None else "",
        "gpio_port": ident.get("port") or "",
        "gpio_number": ident.get("idx") if ident.get("idx") is not None else "",
        "electrical_role_in_group": role,
        "special_role_in_group": _special_role(ctx.services, role),
        "voltage_domain_in_group": _voltage_domain(role),
        "reset_state_in_group": _reset_state(role),
        "boot_state_relevance": "boot0_strap" if (role == R.BOOT or "boot0" in ctx.services) else "none",
        # capability
        "exact_functions_raw": " | ".join(f["function_name"] for f in funcs),
        "exact_functions_normalized": "|".join(sorted({f["category"] for f in funcs if f["category"]})),
        "has_gpio_capability": _yn(R.IO in ctx.union_roles),
        "has_adc_capability": _yn(caps.get("has_adc")),
        "has_dac_capability": _yn(caps.get("has_dac")),
        "has_timer_capability": _yn(caps.get("has_timer")),
        "has_uart_capability": _yn(caps.get("has_uart")),
        "has_spi_capability": _yn(caps.get("has_spi")),
        "has_i2c_capability": _yn(caps.get("has_i2c")),
        "has_can_capability": _yn(caps.get("has_can")),
        "has_usb_capability": _yn(caps.get("has_usb") or (ctx.union_roles & {R.USB_DP, R.USB_DM})),
        "has_swd_capability": _yn(ctx.services & {"swdio", "swclk", "swo"}),
        "has_trace_capability": _yn("swo" in ctx.services),
        "has_clock_capability": _yn((ctx.union_roles & {R.OSC_IN, R.OSC_OUT}) or
                                    any("RCC" in f["function_name"] or "MCO" in f["function_name"]
                                        for f in funcs)),
    }
    # cross-group summary + all design-layer fields (pin-level)
    row.update(design)
    # per-(group,pin) branch fields
    row.update(rules.branch_fields(ctx, role))
    # source/version
    row["schema_version"] = SCHEMA_VERSION
    row["generator_version"] = GENERATOR_VERSION
    row["last_generated_utc"] = ""   # left blank for byte-stable output
    return row


def write_matrix(pd) -> int:
    path = package_dir(pd.package) / f"stm32f_{pd.package}_matrix.csv"
    return write_csv(path, MATRIX_COLUMNS, matrix_rows(pd))


# ── export helpers (CSV + Obsidian-friendly Markdown), scoped to a group ───

# Curated per-pin columns for the readable whole-package Markdown summary.
_MD_PIN_COLS = [
    ("Pin", "socket_pin"), ("Lane", "lane_id"), ("Roles", "roles_seen_all_groups"),
    ("Stability", "pin_role_stability"), ("Required cell", "victim_card_cell_display_name"),
    ("Helios", "helios_control_role"), ("Attack port", "attack_board_standard_port"),
    ("Safety", "safety_class"), ("Action", "ui_primary_action"),
]


def _scoped_rows(pd, group_code: str | None) -> list[dict]:
    rows = matrix_rows(pd)
    if group_code:
        rows = [r for r in rows if r["group_id_internal"] == group_code]
    return rows


def matrix_csv_text(pd, group_code: str | None = None) -> str:
    """Full canonical matrix (all columns) as CSV text — whole package or one group."""
    buf = _io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(MATRIX_COLUMNS)
    for r in _scoped_rows(pd, group_code):
        w.writerow([cell(r.get(c)) for c in MATRIX_COLUMNS])
    return buf.getvalue()


def _md_cell(value) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def _md_table(headers: list[str], rows: list[list]) -> str:
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(_md_cell(c) for c in r) + " |" for r in rows)
    return f"{head}\n{rule}\n{body}\n" if rows else f"{head}\n{rule}\n"


def matrix_markdown_text(pd, group_code: str | None = None) -> str:
    """Readable Markdown (Obsidian tables) — whole package or one exact group."""
    rows = _scoped_rows(pd, group_code)
    if group_code:
        g = next((g for g in pd.groups if g.code == group_code), None)
        label = g.label if g else group_code
        out = [f"# {pd.package} — {label}", ""]
        if g:
            out += [
                f"- **Internal id:** `{g.code}`",
                f"- **Members:** {g.member_count} MCUs ({g.coverage_pct:.1f}% coverage)",
                f"- **Representative:** {g.rep_part}",
                f"- **Baseline:** {'yes' if g.is_baseline else 'no'}  ·  **Delta:** {g.delta_notes}",
                "",
            ]
        out.append(_md_table(
            ["Pin", "Lane", "Pin name", "Role", "Functions"],
            [[r["socket_pin"], r["lane_id"], r["datasheet_pin_name"],
              r["electrical_role_in_group"], r["exact_functions_raw"]] for r in rows]))
        return "\n".join(out)

    # whole package: per-pin design summary (design fields repeat per group → dedup)
    seen, pin_rows = set(), []
    for r in rows:
        p = r["socket_pin"]
        if p in seen:
            continue
        seen.add(p)
        pin_rows.append([r[col] for _, col in _MD_PIN_COLS])
    switched = sum(1 for x in pin_rows if str(x[3]).startswith("MIXED") or x[3] == "UNKNOWN_REVIEW")
    out = [
        f"# STM32F Implementation Matrix — {pd.package}", "",
        f"- **Exact STM32F groups:** {len(pd.groups)}",
        f"- **Socket pins:** {pd.pin_count}",
        f"- **Pins needing a switch cell:** {switched}",
        "",
        _md_table([h for h, _ in _MD_PIN_COLS], pin_rows),
    ]
    return "\n".join(out)
