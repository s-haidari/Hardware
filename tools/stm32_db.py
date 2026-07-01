"""stm32_db.py — build the STM32 pin database (sqlite) from the CubeMX XML set, and
the canonical switch engine that decides which target-socket pins need a card-side
ADG714 switch cell.

Self-contained (stdlib only): a from-scratch rewrite of the proven hwkit logic
(cubemx/parse+classify+builder, pins/switch_engine), kept inline so the app needs
no app/backend dependency. Deterministic and verifiable: the switch engine on the
built DB reproduces the hand-checked ground truth — LQFP64 = 11 switch pins
[1,13,17,18,19,30,31,33,47,48,60].

Requirements authority: Brain/Wiki/Specs/Pinout Authority Generator.md. See
docs/stm32-pins.md.
"""
from __future__ import annotations

import math
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────
_TOOLS = Path(__file__).resolve().parent


def default_cubemx_source() -> Path | None:
    """Locate the bundled CubeMX MCU XML directory (or None)."""
    env = os.environ.get("STM32_CUBEMX") or os.environ.get("HWKIT_CUBEMX")
    if env and Path(env).is_dir():
        return Path(env)
    candidates = [
        _TOOLS / "cubemx_db" / "mcu",
        _TOOLS.parent / "app" / "backend" / "cubemx_db" / "mcu",
        Path.home() / "git" / "STMP" / "src" / "cubemx_db" / "mcu",
    ]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.xml")):
            return c
    return None


def default_db_path() -> Path:
    """Where the built sqlite DB lives (override with STM32_DB)."""
    env = os.environ.get("STM32_DB")
    if env:
        return Path(env)
    return _TOOLS / "data" / "stm32.sqlite"


# ─────────────────────────────────────────────────────────────────────────────
# Parse — one CubeMX MCU XML → structured data
# ─────────────────────────────────────────────────────────────────────────────
_NS = re.compile(r"\{[^}]*\}")   # CubeMX uses a default namespace; strip it.


@dataclass
class Signal:
    name: str
    io_modes: str = ""


@dataclass
class Pin:
    position: int
    name: str               # raw CubeMX name, e.g. "PC13-ANTI_TAMP", "VBAT"
    type: str               # Power / I/O / MonoIO / Reset / Boot / NC
    signals: list = field(default_factory=list)

    @property
    def signal_names(self) -> set:
        return {s.name for s in self.signals}


@dataclass
class McuData:
    ref_name: str
    family: str
    line: str
    package: str
    vdd_min: str = ""
    vdd_max: str = ""
    pins: list = field(default_factory=list)


def _tag(el) -> str:
    return _NS.sub("", el.tag)


def expand_ref_names(ref: str) -> list:
    """STM32F401R(B-C)Tx -> [STM32F401RBTx, STM32F401RCTx] (variants share pinout)."""
    m = re.search(r"\(([^)]+)\)", ref)
    if not m:
        return [ref]
    return [ref[: m.start()] + o + ref[m.end():] for o in m.group(1).split("-")]


def parse_mcu_xml(path: Path) -> McuData:
    root = ET.parse(path).getroot()
    mcu = McuData(
        ref_name=root.get("RefName", path.stem),
        family=root.get("Family", ""),
        line=root.get("Line", ""),
        package=root.get("Package", ""),
    )
    for el in root:
        tag = _tag(el)
        if tag == "Voltage":
            mcu.vdd_min = el.get("Min", "") or mcu.vdd_min
            mcu.vdd_max = el.get("Max", "") or mcu.vdd_max
            continue
        if tag != "Pin":
            continue
        try:
            pos = int(el.get("Position", "0"))
        except ValueError:
            continue  # BGA balls use alphanumeric positions; skip for LQFP/QFN
        pin = Pin(position=pos, name=el.get("Name", "").strip(), type=el.get("Type", ""))
        for child in el:
            if _tag(child) == "Signal":
                pin.signals.append(Signal(child.get("Name", ""), child.get("IOModes", "")))
        mcu.pins.append(pin)
    return mcu


# ─────────────────────────────────────────────────────────────────────────────
# Classify — a pin's electrical class, canonical name, and roles
# ─────────────────────────────────────────────────────────────────────────────
_PORT = re.compile(r"^P([A-Z])(\d{1,2})")


def canonical(pin: Pin) -> tuple:
    """(canonical_pin_name, gpio_port, gpio_index)."""
    m = _PORT.match(pin.name)
    if m:
        port, idx = m.group(1), int(m.group(2))
        return f"P{port}{idx}", port, idx
    return pin.name.replace("_", "").replace(" ", ""), None, None


def electrical_class(pin: Pin) -> str:
    name = pin.name.upper()
    if "NPOR" in name or "PDR_ON" in name or name == "RFU":
        return "io"
    if pin.type == "Reset":
        return "reset"
    if pin.type == "Boot":
        return "boot"
    if pin.type == "NC":
        return "nc"
    if pin.type == "Power":
        if name.startswith("VSS"):
            return "ground"
        if name.startswith("VCAP"):
            return "vcap"
        return "power"
    return "io"  # I/O, MonoIO


def _is_analog(pin: Pin) -> bool:
    if any("ADC" in s.name or "DAC" in s.name for s in pin.signals):
        return True
    return any("Analog" in s.io_modes for s in pin.signals)


def roles(pin: Pin) -> list:
    """List of (role_name, role_class) for this pin on this MCU."""
    ec = electrical_class(pin)
    name = pin.name.upper()
    sigs = " ".join(s.name for s in pin.signals).upper()
    out: list = []

    if ec == "power":
        if name.startswith("VBAT"):
            out.append(("power_vbat", "power"))
        elif name.startswith("VDDA"):
            out.append(("power_vdda", "power"))
        elif name.startswith("VREF"):
            out.append(("power_vref", "power"))
        else:
            out.append(("power_vdd", "power"))
    elif ec == "ground":
        out.append(("ground", "ground"))
    elif ec == "vcap":
        out.append(("vcap", "local_card"))
    elif ec == "reset":
        out.append(("reset_nrst", "service"))
    elif ec == "boot":
        out.append(("boot", "service"))
    elif ec == "nc":
        pass
    else:  # io — HSE only (OSC_IN/OSC_OUT), NOT LSE OSC32 (plain GPIO)
        if "OSC_IN" in name or "OSC_OUT" in name or "RCC_OSC_IN" in sigs or "RCC_OSC_OUT" in sigs:
            out.append(("oscillator_hse", "local_card"))
        if "SWDIO" in sigs or "JTMS" in sigs:
            out.append(("swdio", "service"))
        if "SWCLK" in sigs or "JTCK" in sigs:
            out.append(("swclk", "service"))
        if "TRACESWO" in sigs or "JTDO" in sigs or "-SWO" in sigs or "_SWO" in sigs:
            out.append(("swo", "service"))
        if "JTDI" in sigs or "NJTRST" in sigs or "JTRST" in sigs:
            out.append(("jtag_extra", "service"))
        if _is_analog(pin):
            out.append(("analog", "io"))
        if "GPIO" in sigs:
            out.append(("gpio", "io"))
        if not any(rc == "io" for _, rc in out):
            out.append(("gpio", "io"))  # every I/O pin carries an IO identity

    seen: set = set()
    uniq: list = []
    for rn, rc in out:
        if rn not in seen:
            seen.add(rn)
            uniq.append((rn, rc))
    return uniq


# ─────────────────────────────────────────────────────────────────────────────
# Build — the sqlite database (Layer A)
# ─────────────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE source_artifact (id INTEGER PRIMARY KEY, path TEXT, imported_at TEXT);
CREATE TABLE mcu (
    id INTEGER PRIMARY KEY, source_artifact_id INTEGER NOT NULL,
    part_number TEXT NOT NULL, family TEXT, line TEXT,
    package_name TEXT, pin_count INTEGER,
    vdd_min TEXT, vdd_max TEXT, imported_at TEXT NOT NULL);
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
    function_name TEXT NOT NULL, signal TEXT, io_modes TEXT);
CREATE TABLE pin_role (
    id INTEGER PRIMARY KEY, mcu_package_pin_id INTEGER NOT NULL,
    role_name TEXT NOT NULL, role_class TEXT NOT NULL,
    UNIQUE(mcu_package_pin_id, role_name));
CREATE INDEX ix_pin_mcu ON mcu_package_pin(mcu_id);
CREATE INDEX ix_role_pin ON pin_role(mcu_package_pin_id);
CREATE INDEX ix_func_pin ON pin_function(mcu_package_pin_id);
"""


def lqfp_side(pos: int, n: int):
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
    packages: dict


def build_database(source_dir: Path, db_path: Path, *,
                   family_prefix: str = "STM32F", stamp: str = "1970-01-01",
                   progress=None) -> BuildResult:
    """Build the database from every CubeMX XML under ``source_dir`` (drops+recreates)."""
    source_dir = Path(source_dir)
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        art = conn.execute("INSERT INTO source_artifact (path, imported_at) VALUES (?,?)",
                           (str(source_dir), stamp)).lastrowid

        n_mcu = n_pin = n_role = 0
        packages: dict = {}
        files = sorted(p for p in source_dir.glob("*.xml") if p.name != "families.xml")
        total = len(files)
        for i, f in enumerate(files):
            if progress and i % 25 == 0:
                progress(i, total, f.stem)
            try:
                mcu = parse_mcu_xml(f)
            except Exception:
                continue
            if family_prefix and not mcu.family.startswith(family_prefix):
                continue
            seen_pos: set = set()
            mcu.pins = [p for p in mcu.pins if not (p.position in seen_pos or seen_pos.add(p.position))]
            pin_count = len(mcu.pins)
            mcu_id = conn.execute(
                "INSERT INTO mcu (source_artifact_id, part_number, family, line, "
                "package_name, pin_count, vdd_min, vdd_max, imported_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (art, mcu.ref_name, mcu.family, mcu.line, mcu.package, pin_count,
                 mcu.vdd_min, mcu.vdd_max, stamp),
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
        if progress:
            progress(total, total, "done")
        return BuildResult(n_mcu, n_pin, n_role, packages)
    finally:
        conn.close()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Switch engine — which target-socket pins need an ADG714 cell (the canonical rule)
# ─────────────────────────────────────────────────────────────────────────────
ID_VDD, ID_VDDA, ID_VREF, ID_VBAT = "VDD", "VDDA", "VREF", "VBAT"
ID_VSS, ID_VCAP, ID_BOOT, ID_NRST, ID_OSC, ID_IO = "VSS", "VCAP", "BOOT", "NRST", "OSC", "IO"

_RAIL_OR_RETURN = {ID_VDD, ID_VDDA, ID_VREF, ID_VBAT, ID_VSS, ID_VCAP}
_SERVICE_CRITICAL = {ID_BOOT, ID_NRST}

TARGET_NET = {
    ID_VDD: "VTARGET", ID_VDDA: "VDDA_TGT", ID_VREF: "VREF_TGT", ID_VBAT: "VBAT_TGT",
    ID_VSS: "GND", ID_VCAP: "VCAP_NODE", ID_BOOT: "SERVICE_BOOT0", ID_NRST: "SERVICE_NRST",
    ID_OSC: "SERVICE_OSC", ID_IO: "CARD_LANE",
}

CELL_DIRECT_IO = "CELL_DIRECT_IO"
CELL_FULL_ROLE_SWITCH = "CELL_FULL_ROLE_SWITCH"
CELL_POWER_ONLY = "CELL_POWER_ONLY"
CELL_GROUND_ONLY = "CELL_GROUND_ONLY"
CELL_VCAP_ONLY = "CELL_VCAP_ONLY"
CELL_OSC_LOCAL = "CELL_OSC_LOCAL"
CELL_NC = "CELL_NC"

SWITCH_MUST = "must_switch"
SWITCH_OSC_OPTIONAL = "osc_optional"
SWITCH_NONE = "fixed"

MINORITY_PCT = 0.10
MINORITY_MIN = 2


def switch_identity(role_name: str, role_class: str) -> str:
    """Map one (role_name, role_class) to a switch identity. Every GPIO/analog/AF
    folds into one IO identity; only true routing destinations get their own."""
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
    identities: dict                        # identity -> distinct MCU count
    total_mcus: int
    dominant_identity: str
    minority_identities: list
    needs_switch: bool
    switch_class: str
    cell_required: str
    target_nets: dict                       # non-IO identity -> destination net
    review_flags: list = field(default_factory=list)

    @property
    def non_io_identities(self) -> list:
        return sorted(i for i in self.identities if i != ID_IO)

    @property
    def role_label(self) -> str:
        return "|".join(sorted(self.identities, key=lambda i: -self.identities[i]))

    @property
    def primary_target_net(self) -> str:
        non_io = [i for i in self.identities if i != ID_IO]
        if not non_io:
            return TARGET_NET[ID_IO]
        best = max(non_io, key=lambda i: self.identities[i])
        return TARGET_NET[best]


def _cell_for(identities: dict, needs_switch: bool) -> str:
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


def classify_pin(pin: int, side: str, identities: dict, total_mcus: int) -> SwitchDecision:
    """Turn one pin's identity histogram into a SwitchDecision."""
    if not identities:
        return SwitchDecision(
            pin=pin, side=side, identities={ID_IO: 0}, total_mcus=total_mcus,
            dominant_identity=ID_IO, minority_identities=[], needs_switch=False,
            switch_class=SWITCH_NONE, cell_required=CELL_NC, target_nets={})

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
        switch_class = SWITCH_OSC_OPTIONAL
    else:
        switch_class = SWITCH_MUST

    target_nets = {i: TARGET_NET[i] for i in identities if i != ID_IO}
    flags: list = []
    if minority and needs_switch:
        flags.append("MINORITY_ROLE_PRESENT")

    return SwitchDecision(
        pin=pin, side=side, identities=dict(identities), total_mcus=total_mcus,
        dominant_identity=dominant, minority_identities=minority,
        needs_switch=needs_switch, switch_class=switch_class,
        cell_required=_cell_for(identities, needs_switch),
        target_nets=target_nets, review_flags=flags)


@dataclass
class Adg714Bank:
    index: int
    channels: list                          # (pin, target_net) per channel

    @property
    def spare(self) -> int:
        return 8 - len(self.channels)


@dataclass
class PackageSwitchReport:
    package: str
    decisions: list

    def by_pin(self, pin: int):
        for d in self.decisions:
            if d.pin == pin:
                return d
        return None

    def _of_class(self, cls: str) -> list:
        return [d for d in self.decisions if d.switch_class == cls]

    @property
    def must_switch(self) -> list:
        return self._of_class(SWITCH_MUST)

    @property
    def osc_optional(self) -> list:
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

    def adg714_banks(self, include_osc: bool = False) -> list:
        pins = list(self.must_switch)
        if include_osc:
            pins += list(self.osc_optional)
        pins.sort(key=lambda d: d.pin)
        banks: list = []
        for i, d in enumerate(pins):
            if i % 8 == 0:
                banks.append(Adg714Bank(index=len(banks) + 1, channels=[]))
            banks[-1].channels.append((d.pin, d.primary_target_net))
        return banks

    @property
    def adg714_count(self) -> int:
        return math.ceil(self.must_switch_count / 8) if self.must_switch_count else 0


def pin_identity_histograms(conn: sqlite3.Connection, package: str) -> tuple:
    """(pin -> {identity: mcu_count}, pin -> side, total_mcus)."""
    total_mcus = int(conn.execute(
        "SELECT COUNT(*) FROM mcu WHERE package_name = ?", (package,)).fetchone()[0])
    rows = conn.execute(
        """
        SELECT p.physical_pin_number, p.lqfp_side, pr.role_name, pr.role_class,
               COUNT(DISTINCT p.mcu_id)
        FROM pin_role pr
        JOIN mcu_package_pin p ON p.id = pr.mcu_package_pin_id
        JOIN mcu m             ON m.id = p.mcu_id
        WHERE m.package_name = ?
        GROUP BY p.physical_pin_number, p.lqfp_side, pr.role_name, pr.role_class
        """,
        (package,),
    ).fetchall()

    hist: dict = {}
    sides: dict = {}
    for pin, side, role_name, role_class, n in rows:
        pin = int(pin)
        ident = switch_identity(role_name, role_class)
        bucket = hist.setdefault(pin, {})
        bucket[ident] = max(bucket.get(ident, 0), int(n))
        sides.setdefault(pin, side or "")
    return hist, sides, total_mcus


def package_report(conn: sqlite3.Connection, package: str) -> PackageSwitchReport:
    """The canonical per-package switch-cell report."""
    hist, sides, total = pin_identity_histograms(conn, package)
    decisions = [classify_pin(pin, sides.get(pin, ""), hist[pin], total) for pin in sorted(hist)]
    return PackageSwitchReport(package=package, decisions=decisions)
