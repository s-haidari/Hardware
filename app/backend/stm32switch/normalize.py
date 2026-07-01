"""
normalize.py — assemble the STM32F-only package model and emit the canonical CSV.

This is the F-only source of truth that feeds both the ``stm32f_<PKG>_matrix.csv``
export and the live UI.  Everything is recomputed from the STM32F subset of the
database (:mod:`fdata`, :mod:`grouping`); the all-family precomputed analysis
tables are not used.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cells, fdata, grouping, roles as R, rules, schema
from .csvio import write_csv
from .family import f_mcus
from .paths import (PACKAGE_GEOMETRY, TARGET_PACKAGES, generated_dir,
                    lane_bank, lane_id, lane_index_in_bank)


@dataclass
class PinFact:
    """Compatibility view of one socket pin, consumed by the UI explorer and
    cell-library tab.  Derived from the F-only context."""
    pin: int
    lane: str
    side: str
    role_set: set
    services: set
    caps: dict
    decision: object                          # cells.CellDecision
    exact_functions: list = field(default_factory=list)
    routers_for_lane: list = field(default_factory=list)
    # Legacy-DB cross-check fields. The app-owned CubeMX DB derives every cell
    # itself, so there is no external classification to contradict: db_unsafe is
    # always False and there is no legacy cell_kind. Kept so validate.py /
    # render_docs.py (which cross-check a legacy DB's own cell_kind) still run.
    db_unsafe: bool = False
    db_cell_kind: str = ""
    review_flags: list = field(default_factory=list)  # mirrors decision.review_flags
    conflict: str = ""                                # legacy docs field; unused in app path


@dataclass
class PackageData:
    package: str
    pin_count: int
    pitch_mm: float
    body_mm: float
    priority: int
    voltage: dict
    groups: list                              # list[grouping.GroupInfo]
    contexts: dict                            # pin -> rules.PinContext
    idents: dict                              # pin -> identity dict
    group_meta: dict = field(default_factory=dict)   # group code -> MCU meta
    facts: list = field(default_factory=list)        # list[PinFact] (UI compat)


def assemble(conn, package: str) -> PackageData:
    geom = PACKAGE_GEOMETRY.get(package, {})
    groups = grouping.load_groups(conn, package)

    pin_count = int(geom.get("pins") or 0)
    if not pin_count:
        pin_count = max((max(g.pin_roles) for g in groups if g.pin_roles), default=0)

    caps = fdata.pin_capabilities(conn, package)
    services = fdata.service_pins(conn, package)
    funcs = fdata.exact_functions(conn, package)
    sides = fdata.pin_sides(conn, package)
    repnames = fdata.representative_names(conn, package)
    voltage = fdata.voltage(conn, package)

    meta = fdata.mcu_meta(conn, package)
    volt = fdata.mcu_voltage(conn, package)
    flags = fdata.mcu_flags(conn, package)
    id_by_part = {m["part_number"]: int(m["id"]) for m in f_mcus(conn, package)}

    contexts: dict[int, rules.PinContext] = {}
    idents: dict[int, dict] = {}
    side_index: dict[str, int] = {}
    for pin in range(1, pin_count + 1):
        lane = lane_id(pin)
        side = sides.get(pin, "")
        side_index[side] = side_index.get(side, 0) + 1
        roles_by_group = {g.code: g.pin_roles.get(pin, R.NC) for g in groups}
        labels_by_group = {g.code: g.label for g in groups}
        union = set(roles_by_group.values()) or {R.NC}
        contexts[pin] = rules.PinContext(
            pin=pin, lane=lane, side=side, union_roles=union,
            roles_by_group=roles_by_group, labels_by_group=labels_by_group,
            services=services.get(pin, set()), caps=caps.get(pin, {}),
            exact_functions=funcs.get(pin, []),
        )
        rn = repnames.get(pin, {})
        idents[pin] = {"side": side, "side_index": side_index[side],
                       "name": rn.get("name"), "port": rn.get("port"), "idx": rn.get("idx")}

    group_meta: dict[str, dict] = {}
    for g in groups:
        ids = [id_by_part[p] for p in g.members if p in id_by_part]
        vmins = [volt[i][0] for i in ids if i in volt and volt[i][0] is not None]
        vmaxs = [volt[i][1] for i in ids if i in volt and volt[i][1] is not None]
        mhzs = [meta[i]["max_mhz"] for i in ids if i in meta and meta[i]["max_mhz"]]
        fams = sorted({meta[i]["family"] for i in ids if i in meta})
        sers = sorted({meta[i]["series"] for i in ids if i in meta and meta[i]["series"]})
        group_meta[g.code] = {
            "family": fams[0] if len(fams) == 1 else ("MULTIPLE" if fams else "UNKNOWN"),
            "series_set": sers,
            "vmin_mv": min(vmins) if vmins else "UNKNOWN",
            "vmax_mv": max(vmaxs) if vmaxs else "UNKNOWN",
            "max_mhz": max(mhzs) if mhzs else "UNKNOWN",
            "has_vcap": any(flags.get(i, {}).get("has_vcap") for i in ids),
            "has_usb": any(flags.get(i, {}).get("has_usb") for i in ids),
            "has_boot_uart": any(flags.get(i, {}).get("has_boot_uart") for i in ids),
        }

    facts: list[PinFact] = []
    for pin in range(1, pin_count + 1):
        ctx = contexts[pin]
        dec = cells.classify_cell(ctx.union_roles, ctx.services,
                                  is_analog=ctx.is_analog, is_high_speed=ctx.is_high_speed)
        facts.append(PinFact(
            pin=pin, lane=ctx.lane, side=ctx.side, role_set=set(ctx.union_roles),
            services=set(ctx.services), caps=dict(ctx.caps), decision=dec,
            exact_functions=ctx.exact_functions, routers_for_lane=[],
            review_flags=list(getattr(dec, "review_flags", [])),
        ))

    priority = (TARGET_PACKAGES.index(package) + 1) if package in TARGET_PACKAGES else 99
    return PackageData(
        package=package, pin_count=pin_count,
        pitch_mm=float(geom.get("pitch_mm") or 0.5),
        body_mm=float(geom.get("body_mm") or 0.0),
        priority=priority, voltage=voltage, groups=groups,
        contexts=contexts, idents=idents, group_meta=group_meta, facts=facts,
    )


def write_all(pd: PackageData) -> dict[str, int]:
    """Emit the canonical STM32F matrix for one package."""
    return {"stm32f_matrix": schema.write_matrix(pd)}


def write_superset_backplane() -> int:
    """data/generated/parent_backplane_176.csv — the 176-lane superset map."""
    from .paths import SUPERSET_LANES
    cols = ["lane_id", "lane_number", "lane_bank", "lane_index_in_bank",
            "lqfp48", "lqfp64", "lqfp100", "lqfp144", "lqfp176"]
    rows = []
    for n in range(1, SUPERSET_LANES + 1):
        rows.append({
            "lane_id": lane_id(n), "lane_number": n, "lane_bank": lane_bank(n),
            "lane_index_in_bank": lane_index_in_bank(n),
            "lqfp48": "used" if n <= 48 else "NC",
            "lqfp64": "used" if n <= 64 else "NC",
            "lqfp100": "used" if n <= 100 else "NC",
            "lqfp144": "used" if n <= 144 else "NC",
            "lqfp176": "used" if n <= 176 else "NC",
        })
    return write_csv(generated_dir() / "parent_backplane_176.csv", cols, rows)
