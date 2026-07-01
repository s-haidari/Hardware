"""
parse.py — parse one CubeMX MCU XML into structured data.

CubeMX format:
    <Mcu Family="STM32F4" Line="STM32F401" Package="LQFP64" RefName="STM32F401R(B-C)Tx" ...>
        <Pin Name="VBAT" Position="1" Type="Power"/>
        <Pin Name="PA14" Position="40" Type="I/O">
            <Signal Name="SYS_JTCK-SWCLK"/>
            <Signal IOModes="..." Name="GPIO"/>
        </Pin>
    </Mcu>
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# CubeMX XML uses a default namespace; strip it for simple tag matching.
_NS = re.compile(r"\{[^}]*\}")


@dataclass
class Signal:
    name: str
    io_modes: str = ""


@dataclass
class Pin:
    position: int
    name: str               # raw CubeMX name, e.g. "PC13-ANTI_TAMP", "VBAT"
    type: str               # Power / I/O / MonoIO / Reset / Boot / NC
    signals: list[Signal] = field(default_factory=list)

    @property
    def signal_names(self) -> set[str]:
        return {s.name for s in self.signals}


@dataclass
class McuData:
    ref_name: str
    family: str
    line: str
    package: str
    pins: list[Pin] = field(default_factory=list)


def _tag(el) -> str:
    return _NS.sub("", el.tag)


def expand_ref_names(ref: str) -> list[str]:
    """Expand CubeMX '(B-C)' / '(x-y-z)' notation into concrete part numbers.

    STM32F401R(B-C)Tx -> [STM32F401RBTx, STM32F401RCTx]. The variants differ only
    in flash/ram, never in pinout, so any one is representative for pin work.
    """
    m = re.search(r"\(([^)]+)\)", ref)
    if not m:
        return [ref]
    opts = m.group(1).split("-")
    return [ref[: m.start()] + o + ref[m.end():] for o in opts]


def parse_mcu_xml(path: Path) -> McuData:
    root = ET.parse(path).getroot()
    mcu = McuData(
        ref_name=root.get("RefName", path.stem),
        family=root.get("Family", ""),
        line=root.get("Line", ""),
        package=root.get("Package", ""),
    )
    for el in root:
        if _tag(el) != "Pin":
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
