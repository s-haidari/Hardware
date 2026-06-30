"""
grouping.py — STM32F-only exact pinout groups, recomputed from raw pin data.

An *exact* pinout group is a set of STM32F MCUs that share the identical socket
pinout (same datasheet pin name at every physical pin).  Because the precomputed
``mcu_pinout_group`` table mixes all families, we recompute groups here from the
F-only per-MCU pin maps (:mod:`fdata`).

Group A (rank 0) is the baseline — the largest exact group — unless a
``baseline_override.yml`` in the package data dir pins a specific representative
part.  Every other group is described by its *delta* from the baseline: which
socket pins/lanes deviate and what kind of deviation it is.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import fdata, roles as R
from .family import f_mcus
from .group_labels import label_for_rank
from .paths import lane_id, package_dir

# Map a deviating pin's role/name tokens to a coarse delta kind.
_DELTA_KEYWORDS = [
    ("swd_delta",   ("swdio", "swclk", "swo", "jtms", "jtck", "jtdi", "jtdo")),
    ("usb_delta",   ("usb", "otg", "dp", "dm")),
    ("uart_delta",  ("uart", "usart", "tx", "rx")),
    ("reset_delta", ("nrst", "reset")),
    ("boot_delta",  ("boot",)),
    ("osc_delta",   ("osc", "hse", "lse", "rcc")),
    ("power_delta", ("vdd", "vss", "vdda", "vssa", "vbat", "vref", "vcap")),
]


@dataclass
class GroupInfo:
    gid: int
    code: str
    label: str
    rank: int
    member_count: int
    coverage_pct: float
    signature_hash: str
    is_baseline: bool
    delta_kind: str
    delta_notes: str
    rep_mcu_id: int
    rep_part: str
    members: list[str] = field(default_factory=list)
    affected_pins: list[int] = field(default_factory=list)
    affected_lanes: list[str] = field(default_factory=list)
    pin_roles: dict[int, str] = field(default_factory=dict)   # pin -> canonical role
    pin_names: dict[int, str] = field(default_factory=dict)   # pin -> datasheet name


def _signature(pin_map: dict[int, dict]) -> tuple:
    """Exact-pinout signature: the datasheet name at every physical pin."""
    return tuple(pin_map[p]["name"] for p in sorted(pin_map))


def _read_baseline_override(package: str) -> str | None:
    """Optional ``baseline_part: <part_number>`` to force which group is Group A."""
    path = package_dir(package) / "baseline_override.yml"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.lower().startswith("baseline_part"):
            _, _, val = line.partition(":")
            return val.strip() or None
    return None


def _delta_kind(affected_names: list[str], affected_roles: set[str]) -> str:
    blob = " ".join(affected_names).lower() + " " + " ".join(affected_roles).lower()
    kinds = [k for k, words in _DELTA_KEYWORDS if any(w in blob for w in words)]
    return ",".join(kinds) if kinds else "pinout_delta"


def load_groups(conn, package: str) -> list[GroupInfo]:
    mcus = f_mcus(conn, package)
    pin_maps = fdata.mcu_pin_maps(conn, package)
    # Keep only MCUs that actually have a pin map.
    members = [(int(m["id"]), m["part_number"]) for m in mcus
               if int(m["id"]) in pin_maps]
    if not members:
        return []
    total = len(members)

    # Bucket MCUs by exact-pinout signature.
    buckets: dict[tuple, list[tuple[int, str]]] = {}
    for mcu_id, part in members:
        buckets.setdefault(_signature(pin_maps[mcu_id]), []).append((mcu_id, part))

    # Order buckets: by member count desc, then by representative part for stability.
    ordered = sorted(buckets.items(),
                     key=lambda kv: (-len(kv[1]), sorted(p for _, p in kv[1])[0]))

    # Baseline selection: largest group, unless an override names a part.
    override_part = _read_baseline_override(package)
    baseline_idx = 0
    if override_part:
        for i, (_sig, mem) in enumerate(ordered):
            if any(p == override_part for _, p in mem):
                baseline_idx = i
                break
    if baseline_idx != 0:
        ordered.insert(0, ordered.pop(baseline_idx))

    # Baseline pin roles/names for delta comparison.
    base_sig, base_mem = ordered[0]
    base_rep_id = sorted(base_mem, key=lambda x: x[1])[0][0]
    base_map = pin_maps[base_rep_id]
    base_roles = {p: R.role_of(d["name"], d["eclass"]) for p, d in base_map.items()}
    base_names = {p: d["name"] for p, d in base_map.items()}

    out: list[GroupInfo] = []
    for rank, (sig, mem) in enumerate(ordered):
        rep_id, rep_part = sorted(mem, key=lambda x: x[1])[0]
        rep_map = pin_maps[rep_id]
        pin_roles = {p: R.role_of(d["name"], d["eclass"]) for p, d in rep_map.items()}
        pin_names = {p: d["name"] for p, d in rep_map.items()}
        is_baseline = (rank == 0)

        affected_pins: list[int] = []
        affected_names: list[str] = []
        affected_roles: set[str] = set()
        if not is_baseline:
            for p in sorted(rep_map):
                if pin_names.get(p) != base_names.get(p) or pin_roles.get(p) != base_roles.get(p):
                    affected_pins.append(p)
                    affected_names += [pin_names.get(p) or "", base_names.get(p) or ""]
                    affected_roles |= {pin_roles.get(p, ""), base_roles.get(p, "")}

        delta_kind = "baseline" if is_baseline else _delta_kind(affected_names, affected_roles)
        if is_baseline:
            delta_notes = "baseline (no deviations)"
        else:
            lanes = ", ".join(lane_id(p) for p in affected_pins[:6])
            more = f" (+{len(affected_pins) - 6} more)" if len(affected_pins) > 6 else ""
            delta_notes = f"deviates at {len(affected_pins)} lane(s): {lanes}{more}" if affected_pins \
                else "distinct pinout (no role change vs baseline)"

        out.append(GroupInfo(
            gid=rank,
            code=f"{package}_FULL_G{rank:03d}",
            label=label_for_rank(rank),
            rank=rank,
            member_count=len(mem),
            coverage_pct=round(len(mem) / total * 100, 2),
            signature_hash=hashlib.sha1(repr(sig).encode("utf-8")).hexdigest()[:12],
            is_baseline=is_baseline,
            delta_kind=delta_kind,
            delta_notes=delta_notes,
            rep_mcu_id=rep_id,
            rep_part=rep_part,
            members=sorted(p for _, p in mem),
            affected_pins=affected_pins,
            affected_lanes=[lane_id(p) for p in affected_pins],
            pin_roles=pin_roles,
            pin_names=pin_names,
        ))
    return out


def baseline_group(groups: list[GroupInfo]) -> GroupInfo | None:
    for g in groups:
        if g.is_baseline:
            return g
    return groups[0] if groups else None
