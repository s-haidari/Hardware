"""
switch_engine.py — the single canonical source of truth for which target
socket pins need a card-side switch cell, and what each switch routes.

WHY THIS EXISTS
---------------
Three earlier classifiers disagreed on every pin:

  * ``stm_helper/analyzers/card_pin_role_cell.py`` collapsed VDD/VDDA/VREF/VBAT
    into one "power" family, dropped a 10%/2-MCU minority threshold on top, and
    only read ``card_power_map`` (which does not even carry the BOOT role). For
    LQFP64 it found 5 switch pins.
  * ``stm32switch/cells.py`` used the full role set with no threshold.
  * the ``*_matrix.csv`` export used a third ("victim_card") vocabulary.

The hand-verified hardware ground truth (Brain Build Card 7B, from the STM32F
LQFP64 Matrix) is **11** role-conflict pins on LQFP64, not 5. This module
reproduces that exactly and is the only place the rule lives. Every surface
(desktop viewer, HTML report, CSV export, DB analyzer) derives from here.

THE RULE
--------
For each physical socket pin, gather the distinct *switch identities* the pin
takes across every MCU in the package family (one identity per distinct
routing destination, NOT per dominant role):

    VDD VDDA VREF VBAT  -> distinct target rails (never collapsed)
    VSS                 -> ground
    VCAP                -> the local VCAP node
    BOOT NRST           -> parent service nets
    OSC                 -> oscillator service / crystal
    IO                  -> the default CARD_LANE (every GPIO/analog/UART/SWD/USB
                           alternate function is the same one IO identity)

A pin needs a switch when it carries >= 2 distinct identities. Minority roles
(present on a handful of MCUs) are still counted — strict-safe — but flagged so
the engineer sees them (the "strict headline + minority shown" policy).

Conflicts split two ways:

  * SWITCH_MUST          — a rail/ground/VCAP/BOOT/NRST conflict. The card must
                           switch it; routing it direct would be unsafe.
  * SWITCH_OSC_OPTIONAL  — the pin is only OSC|IO. Whether it rides an ADG714
                           channel (switched oscillator service, as LQFP100 does)
                           or routes direct as a lane (as LQFP64 Card 7B does) is
                           a per-card routing choice, not a data fact.

This module is data-only: it takes a sqlite3 connection and returns dataclasses.
No Qt, no I/O, no globals.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

# ── switch identities ───────────────────────────────────────────────────────
ID_VDD = "VDD"
ID_VDDA = "VDDA"
ID_VREF = "VREF"
ID_VBAT = "VBAT"
ID_VSS = "VSS"
ID_VCAP = "VCAP"
ID_BOOT = "BOOT"
ID_NRST = "NRST"
ID_OSC = "OSC"
ID_IO = "IO"

# Identities that, when mixed with anything else, force a real switch cell.
_RAIL_OR_RETURN = {ID_VDD, ID_VDDA, ID_VREF, ID_VBAT, ID_VSS, ID_VCAP}
_SERVICE_CRITICAL = {ID_BOOT, ID_NRST}

# Canonical routing destination per identity (matches Build Cards 7B / 7C).
TARGET_NET = {
    ID_VDD:  "VTARGET",
    ID_VDDA: "VDDA_TGT",
    ID_VREF: "VREF_TGT",
    ID_VBAT: "VBAT_TGT",
    ID_VSS:  "GND",
    ID_VCAP: "VCAP_NODE",
    ID_BOOT: "SERVICE_BOOT0",
    ID_NRST: "SERVICE_NRST",
    ID_OSC:  "SERVICE_OSC",
    ID_IO:   "CARD_LANE",
}

# Human label for the required cell, reusing the stm32switch cell vocabulary so
# the two packages share one set of names.
CELL_DIRECT_IO = "CELL_DIRECT_IO"
CELL_FULL_ROLE_SWITCH = "CELL_FULL_ROLE_SWITCH"
CELL_POWER_ONLY = "CELL_POWER_ONLY"
CELL_GROUND_ONLY = "CELL_GROUND_ONLY"
CELL_VCAP_ONLY = "CELL_VCAP_ONLY"
CELL_OSC_LOCAL = "CELL_OSC_LOCAL"
CELL_NC = "CELL_NC"

# switch_class values
SWITCH_MUST = "must_switch"
SWITCH_OSC_OPTIONAL = "osc_optional"
SWITCH_NONE = "fixed"

# A role is "minority" when it appears on fewer than this share of the family
# (and fewer than the absolute floor). It is still counted; it is only flagged.
MINORITY_PCT = 0.10
MINORITY_MIN = 2


def switch_identity(role_name: str, role_class: str) -> str:
    """Map one (role_name, role_class) from ``pin_role`` to a switch identity.

    Every GPIO / analog / peripheral alternate function folds into a single IO
    identity; only true routing destinations get their own identity.
    """
    rn = (role_name or "").lower()
    rc = (role_class or "").lower()
    if rc == "power":
        if "vdda" in rn:
            return ID_VDDA
        if "vref" in rn:
            return ID_VREF
        if "vbat" in rn:
            return ID_VBAT
        return ID_VDD
    if rc == "ground":
        return ID_VSS
    if "vcap" in rn:
        return ID_VCAP
    if rn == "boot" or rn.startswith("boot"):
        return ID_BOOT
    if "rst" in rn or "reset" in rn:
        return ID_NRST
    if "osc" in rn:
        return ID_OSC
    return ID_IO


@dataclass
class SwitchDecision:
    pin: int
    side: str
    identities: dict[str, int]              # identity -> distinct MCU count
    total_mcus: int
    dominant_identity: str
    minority_identities: list[str]
    needs_switch: bool
    switch_class: str                       # SWITCH_MUST / OSC_OPTIONAL / NONE
    cell_required: str
    target_nets: dict[str, str]             # non-IO identity -> destination net
    review_flags: list[str] = field(default_factory=list)

    @property
    def non_io_identities(self) -> list[str]:
        return sorted(i for i in self.identities if i != ID_IO)

    @property
    def role_label(self) -> str:
        """e.g. 'VBAT|VDD' or 'BOOT|IO' — the conflict as the cards write it."""
        return "|".join(sorted(self.identities, key=lambda i: -self.identities[i]))

    @property
    def primary_target_net(self) -> str:
        """The net the switch routes to (the dominant non-IO destination)."""
        non_io = [i for i in self.identities if i != ID_IO]
        if not non_io:
            return TARGET_NET[ID_IO]
        best = max(non_io, key=lambda i: self.identities[i])
        return TARGET_NET[best]


def _cell_for(identities: dict[str, int], needs_switch: bool) -> str:
    if needs_switch:
        return CELL_FULL_ROLE_SWITCH
    only = next(iter(identities))
    if only == ID_IO:
        return CELL_DIRECT_IO
    if only in {ID_VDD, ID_VDDA, ID_VREF, ID_VBAT}:
        return CELL_POWER_ONLY
    if only == ID_VSS:
        return CELL_GROUND_ONLY
    if only == ID_VCAP:
        return CELL_VCAP_ONLY
    if only == ID_OSC:
        return CELL_OSC_LOCAL
    return CELL_DIRECT_IO


def classify(pin: int, side: str, identities: dict[str, int], total_mcus: int) -> SwitchDecision:
    """Turn one pin's identity histogram into a SwitchDecision."""
    if not identities:
        return SwitchDecision(
            pin=pin, side=side, identities={ID_IO: 0}, total_mcus=total_mcus,
            dominant_identity=ID_IO, minority_identities=[], needs_switch=False,
            switch_class=SWITCH_NONE, cell_required=CELL_NC, target_nets={},
        )

    dominant = max(identities, key=lambda i: identities[i])
    floor = max(MINORITY_MIN, int(total_mcus * MINORITY_PCT)) if total_mcus else MINORITY_MIN
    minority = sorted(i for i, n in identities.items() if n < floor)

    needs_switch = len(identities) >= 2
    non_io = {i for i in identities if i != ID_IO}

    if not needs_switch:
        switch_class = SWITCH_NONE
    elif non_io & (_RAIL_OR_RETURN | _SERVICE_CRITICAL):
        switch_class = SWITCH_MUST
    elif non_io == {ID_OSC}:
        # only OSC vs IO -> the card decides direct-lane vs switched oscillator.
        switch_class = SWITCH_OSC_OPTIONAL
    else:
        switch_class = SWITCH_MUST

    target_nets = {i: TARGET_NET[i] for i in identities if i != ID_IO}
    flags: list[str] = []
    if minority and needs_switch:
        flags.append("MINORITY_ROLE_PRESENT")

    return SwitchDecision(
        pin=pin, side=side, identities=dict(identities), total_mcus=total_mcus,
        dominant_identity=dominant, minority_identities=minority,
        needs_switch=needs_switch, switch_class=switch_class,
        cell_required=_cell_for(identities, needs_switch),
        target_nets=target_nets, review_flags=flags,
    )


@dataclass
class Adg714Bank:
    """One physical ADG714 octal SPST cell and the channels assigned to it."""
    index: int
    channels: list[tuple[int, str]]         # (pin, target_net) per closed-able channel

    @property
    def spare(self) -> int:
        return 8 - len(self.channels)


@dataclass
class PackageSwitchReport:
    package: str
    decisions: list[SwitchDecision]

    def by_pin(self, pin: int) -> SwitchDecision | None:
        for d in self.decisions:
            if d.pin == pin:
                return d
        return None

    def _of_class(self, cls: str) -> list[SwitchDecision]:
        return [d for d in self.decisions if d.switch_class == cls]

    @property
    def must_switch(self) -> list[SwitchDecision]:
        return self._of_class(SWITCH_MUST)

    @property
    def osc_optional(self) -> list[SwitchDecision]:
        return self._of_class(SWITCH_OSC_OPTIONAL)

    @property
    def must_switch_count(self) -> int:
        return len(self.must_switch)

    @property
    def osc_optional_count(self) -> int:
        return len(self.osc_optional)

    @property
    def fixed_count(self) -> int:
        return len(self._of_class(SWITCH_NONE))

    def adg714_banks(self, include_osc: bool = False) -> list[Adg714Bank]:
        """Pack one channel per switch pin into octal ADG714 cells.

        Baseline is one channel per switched pin (the dominant non-IO target;
        the IO alternate is the lane itself when the switch is open). Power
        roles that need paralleling for current add channels at the card level;
        this gives the minimum cell count.
        """
        pins = list(self.must_switch)
        if include_osc:
            pins += list(self.osc_optional)
        pins.sort(key=lambda d: d.pin)
        banks: list[Adg714Bank] = []
        for i, d in enumerate(pins):
            if i % 8 == 0:
                banks.append(Adg714Bank(index=len(banks) + 1, channels=[]))
            banks[-1].channels.append((d.pin, d.primary_target_net))
        return banks

    @property
    def adg714_count(self) -> int:
        return math.ceil(self.must_switch_count / 8) if self.must_switch_count else 0


def pin_identity_histograms(conn: sqlite3.Connection, package: str) -> tuple[dict[int, dict[str, int]], dict[int, str], int]:
    """Return (pin -> {identity: mcu_count}, pin -> side, total_mcus)."""
    total_mcus = int(conn.execute(
        "SELECT COUNT(*) FROM mcu WHERE package_name = ?", (package,)
    ).fetchone()[0])

    rows = conn.execute(
        """
        SELECT p.physical_pin_number AS pin,
               p.lqfp_side          AS side,
               pr.role_name         AS role_name,
               pr.role_class        AS role_class,
               COUNT(DISTINCT p.mcu_id) AS n
        FROM pin_role pr
        JOIN mcu_package_pin p ON p.id = pr.mcu_package_pin_id
        JOIN mcu m             ON m.id = p.mcu_id
        WHERE m.package_name = ?
        GROUP BY p.physical_pin_number, p.lqfp_side, pr.role_name, pr.role_class
        """,
        (package,),
    ).fetchall()

    hist: dict[int, dict[str, int]] = {}
    sides: dict[int, str] = {}
    # An identity's MCU count is the max over the role_names that map to it
    # (a pin can hit the same identity through several role_names; they overlap
    # on the same MCUs, so max is the honest distinct-MCU estimate).
    for r in rows:
        pin = int(r["pin"])
        ident = switch_identity(r["role_name"], r["role_class"])
        bucket = hist.setdefault(pin, {})
        bucket[ident] = max(bucket.get(ident, 0), int(r["n"]))
        sides.setdefault(pin, r["side"] or "")
    return hist, sides, total_mcus


def package_report(conn: sqlite3.Connection, package: str) -> PackageSwitchReport:
    """The canonical per-package switch-cell report."""
    hist, sides, total = pin_identity_histograms(conn, package)
    decisions = [
        classify(pin, sides.get(pin, ""), hist[pin], total)
        for pin in sorted(hist)
    ]
    return PackageSwitchReport(package=package, decisions=decisions)
