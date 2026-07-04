#!/usr/bin/env python3
"""
wizard.py — Interactive terminal wizard for bulk renaming in KiCad projects.

Features:
- Prints all folders that contain KiCad project files (.kicad_pro and legacy .pro) at startup
- Choose project (SH/CG/Master or any custom path)
- Operations: Add tag prefix, Remove tag, Strip all tags, Reset to unannotated, or Custom find/replace
- Scope: schematic nets/labels, schematic symbol references, PCB footprint references, or all
- Preview (counts + examples) -> Apply with per-file .bak backups -> optional ERC via kicad-cli
- Ignores .history folders
- TRUE UNANNOTATION: Uses lib_id to reset any reference to correct designator (e.g., MyResistor10 -> R?)

Requires: Python 3.8+ (standard library only)
"""

import sys
import json
import re
import subprocess
import shutil
import os
from pathlib import Path
from datetime import datetime

# ---------- Configuration ----------
LOG_DIR = Path(__file__).parent / "logs"  # tools/logs/

# Standard KiCad component designators (ordered by priority: multi-char first)
STANDARD_DESIGNATORS = [
    # Two-letter designators (check first)
    'RN', 'SW', 'IC', 'TP', 'FB', 'PS', 'BT', 'CN',
    # Single-letter designators
    'B', 'C', 'D', 'F', 'H', 'J', 'L', 'Q', 'R', 'T', 'U', 'X', 'Y',
    # Less common
    'A', 'E', 'G', 'K', 'M', 'N', 'P', 'S', 'V', 'W', 'Z'
]

# ---------- Prompt helpers ----------
def prompt_menu(title, options):
    print(f"\n{title}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")
    while True:
        choice = input("Select option #: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        print("Invalid selection, try again.")

def prompt_text(label, default=None, allow_empty=False):
    while True:
        val = input(f"{label}{' ['+default+']' if default else ''}: ").strip()
        if not val and default is not None:
            return default
        if val or allow_empty:
            return val
        print("Enter a non-empty value.")

def prompt_yes_no(label, default="n"):
    d = default.lower()[0] if default else "n"
    val = input(f"{label} (y/n) [{d}]: ").strip().lower()
    if not val:
        val = d
    return val.startswith("y")

# ---------- Project discovery ----------
def should_ignore_path(path: Path) -> bool:
    """Check if path should be ignored (.history, etc.)"""
    parts = path.parts
    return '.history' in parts or any(p.startswith('.') and p != '.' for p in parts)

def discover_projects(repo_root: Path):
    """Discover KiCad projects, ignoring .history folders"""
    candidates = []

    # Check standard locations
    for p in [repo_root/"projects"/"SH", repo_root/"projects"/"CG", repo_root/"projects"/"Master",
              repo_root/"SH", repo_root/"CG", repo_root/"Master"]:
        if p.exists() and not should_ignore_path(p):
            candidates.append(p)

    pr_dir = repo_root/"projects"
    if pr_dir.exists():
        for sub in pr_dir.iterdir():
            if sub.is_dir() and not should_ignore_path(sub):
                if any(sub.rglob("*.kicad_sch")):
                    if sub not in candidates:
                        candidates.append(sub)

    return sorted(set(candidates))

def list_schematics(project_root: Path):
    """Find all schematics, excluding .history"""
    all_schs = project_root.rglob("*.kicad_sch")
    return sorted([s for s in all_schs if not should_ignore_path(s)])

def list_boards(project_root: Path):
    """Find all boards, excluding .history"""
    all_brds = project_root.rglob("*.kicad_pcb")
    return sorted([b for b in all_brds if not should_ignore_path(b)])

def pick_top_schematic(schematics):
    if not schematics:
        return None
    idx = prompt_menu("Choose top-level schematic for ERC (or skip ERC):",
                      [str(s) for s in schematics])
    return schematics[idx]

# ---------- Project file discovery & printing ----------
def find_kicad_projects(root: Path, include_legacy: bool = True, include_prl: bool = False):
    """
    Return {directory_path: [project_file_names]} for all KiCad project files under 'root'.
    Excludes .history folders.
    """
    patterns = ["*.kicad_pro"]
    if include_legacy:
        patterns.append("*.pro")
    if include_prl:
        patterns.append("*.kicad_prl")

    files = []
    for pat in patterns:
        for f in root.rglob(pat):
            if not should_ignore_path(f):
                files.append(f)

    projects = {}
    for f in files:
        d = f.parent.resolve()
        projects.setdefault(d, []).append(f.name)
    return projects

def get_project_display_name(path: Path, root: Path) -> tuple:
    """
    Return (human_name, full_path) for a project.

    Examples:
      Master -> ("Master", "C:/git/development-board/Master")
      work/SH -> ("SH", "C:/git/development-board/work/SH")
    """
    try:
        rel = path.relative_to(root)
        rel_str = str(rel)
    except Exception:
        rel_str = path.name

    # Human name is the last part
    human_name = path.name

    # Full path for display
    full_path = str(path)

    return (human_name, full_path)

def _rel_or_abs(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except Exception:
        return str(path)

def print_kicad_projects(projects, root: Path, title: str = "Detected KiCad projects (.kicad_pro / .pro)"):
    print(f"\n=== {title} ===")
    if not projects:
        print(f"  (none under {root})")
        print("  Tip: KiCad 6+ uses .kicad_pro; older projects used .pro.")
        return
    for d in sorted(projects.keys(), key=lambda p: str(p).lower()):
        human_name, full_path = get_project_display_name(d, root)
        print(f"{human_name}")
        print(f"  Path: {full_path}")
        for name in sorted(projects[d], key=lambda s: s.lower()):
            print(f"  - {name}")

# ---------- Tag & text utilities ----------
def is_bus_label(name: str) -> bool:
    return "[" in name and name.endswith("]")

def add_tag(name: str, tag: str) -> str:
    if name.startswith(tag):
        return name
    if is_bus_label(name):
        base, rest = name.split("[", 1)
        return f"{tag}{base}[{rest}"
    return f"{tag}{name}"

def strip_tag(name: str, tag: str) -> str:
    if name.startswith(tag):
        if is_bus_label(name):
            base, rest = name.split("[", 1)
            base = base[len(tag):]
            return f"{base}[{rest}"
        return name[len(tag):]
    return name

def strip_all_tags(name: str) -> str:
    """Remove all tag prefixes (anything before standard designator)"""
    # Look for a standard designator followed by a number
    for des in STANDARD_DESIGNATORS:
        # Pattern: anything before designator, then designator+number
        pattern = f'^.*?({des}\\d+.*)$'
        match = re.match(pattern, name, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # Fallback: remove non-alphanumeric prefix
    match = re.match(r'^[^A-Za-z0-9]*(.*)$', name)
    if match:
        return match.group(1)
    return name

def strip_all_label_tags(txt: str) -> str:
    """Strip stacked tag prefixes ('^[A-Z]{1,3}-') from a NET / LABEL name ONLY.

    Unlike strip_all_tags — which scans for a component designator and is meant for
    component *references* — this never truncates the body. Net names with no tag
    prefix are returned unchanged (the old code turned 'I2C1_SDA' into 'C1_SDA' and
    'USART2_TX' into 'T2_TX' by matching a designator mid-string)."""
    prev = None
    while prev != txt:
        prev = txt
        m = re.match(r'^[A-Z]{1,3}-', txt)
        if m:
            txt = txt[m.end():]
    return txt

def extract_tag_prefix(ref: str) -> tuple:
    """
    Extract tag prefix from a reference.
    Tag prefixes are typically: XX- (two uppercase letters and hyphen)
    
    Returns: (tag_prefix, ref_without_tag)
    Examples:
        "SH-R1" -> ("SH-", "R1")
        "CG-U5" -> ("CG-", "U5")
        "R1" -> ("", "R1")
        "SH-MyResistor10" -> ("SH-", "MyResistor10")
    """
    # Match pattern: uppercase letters followed by hyphen at the start
    match = re.match(r'^([A-Z]{1,3}-)', ref)
    if match:
        tag = match.group(1)
        return (tag, ref[len(tag):])
    return ("", ref)

# Comprehensive map of ALL standard KiCad libraries to designators
LIBRARY_TO_DESIGNATOR = {
    # ═══════════════════════════════════════════════════════════
    # DEVICE LIBRARY (most common)
    # ═══════════════════════════════════════════════════════════
    "Device:R": "R",
    "Device:R_Small": "R",
    "Device:R_Pack02": "RN",
    "Device:R_Pack03": "RN",
    "Device:R_Pack04": "RN",
    "Device:R_Pack05": "RN",
    "Device:R_Pack08": "RN",
    "Device:R_Network04": "RN",
    "Device:R_Network05": "RN",
    "Device:R_Network06": "RN",
    "Device:R_Network07": "RN",
    "Device:R_Network08": "RN",
    "Device:R_Network09": "RN",
    "Device:R_Network10": "RN",
    "Device:R_Network11": "RN",
    "Device:R_Network13": "RN",
    "Device:R_Potentiometer": "R",
    "Device:R_Potentiometer_Dual": "R",
    "Device:R_Potentiometer_Trim": "R",
    "Device:R_Variable": "R",
    "Device:R_Shunt": "R",
    "Device:R_Photo": "R",
    "Device:Thermistor": "R",
    "Device:Thermistor_NTC": "R",
    "Device:Thermistor_PTC": "R",
    "Device:Varistor": "R",
    
    "Device:C": "C",
    "Device:C_Small": "C",
    "Device:C_Polarized": "C",
    "Device:C_Polarized_Small": "C",
    "Device:CP": "C",
    "Device:CP_Small": "C",
    "Device:C_Trim": "C",
    "Device:C_Variable": "C",
    "Device:C_Network04": "CN",
    "Device:C_Network05": "CN",
    "Device:C_Network06": "CN",
    "Device:C_Network07": "CN",
    "Device:C_Network08": "CN",
    "Device:C_Network09": "CN",
    "Device:C_Network10": "CN",
    
    "Device:L": "L",
    "Device:L_Small": "L",
    "Device:L_Core_Ferrite": "L",
    "Device:L_Core_Iron": "L",
    "Device:L_Coupled": "L",
    
    "Device:D": "D",
    "Device:D_Small": "D",
    "Device:D_ALT": "D",
    "Device:LED": "D",
    "Device:LED_Small": "D",
    "Device:LED_ALT": "D",
    "Device:LED_Dual": "D",
    "Device:LED_RABG": "D",
    "Device:LED_RAGB": "D",
    "Device:LED_RBAG": "D",
    "Device:LED_RBGA": "D",
    "Device:LED_RGBA": "D",
    "Device:LED_RGAB": "D",
    "Device:D_Zener": "D",
    "Device:D_Zener_Small": "D",
    "Device:D_Schottky": "D",
    "Device:D_Schottky_Small": "D",
    "Device:D_Shockley": "D",
    "Device:D_Bridge": "D",
    "Device:D_TVS": "D",
    "Device:D_Avalanche": "D",
    
    "Device:Q_NPN_BEC": "Q",
    "Device:Q_NPN_BCE": "Q",
    "Device:Q_NPN_CBE": "Q",
    "Device:Q_NPN_CEB": "Q",
    "Device:Q_NPN_EBC": "Q",
    "Device:Q_NPN_ECB": "Q",
    "Device:Q_PNP_BEC": "Q",
    "Device:Q_PNP_BCE": "Q",
    "Device:Q_PNP_CBE": "Q",
    "Device:Q_PNP_CEB": "Q",
    "Device:Q_PNP_EBC": "Q",
    "Device:Q_PNP_ECB": "Q",
    "Device:Q_NMOS_GSD": "Q",
    "Device:Q_NMOS_GDS": "Q",
    "Device:Q_NMOS_DGS": "Q",
    "Device:Q_NMOS_DSG": "Q",
    "Device:Q_NMOS_SGD": "Q",
    "Device:Q_NMOS_SDG": "Q",
    "Device:Q_PMOS_GSD": "Q",
    "Device:Q_PMOS_GDS": "Q",
    "Device:Q_PMOS_DGS": "Q",
    "Device:Q_PMOS_DSG": "Q",
    "Device:Q_PMOS_SGD": "Q",
    "Device:Q_PMOS_SDG": "Q",
    "Device:Q_NIGBT_GCE": "Q",
    "Device:Q_PIGBT_GCE": "Q",
    
    "Device:Crystal": "Y",
    "Device:Crystal_Small": "Y",
    "Device:Crystal_GND2": "Y",
    "Device:Crystal_GND23": "Y",
    "Device:Crystal_GND24": "Y",
    "Device:Resonator": "Y",
    "Device:Resonator_Small": "Y",
    
    "Device:Fuse": "F",
    "Device:Fuse_Small": "F",
    "Device:Polyfuse": "F",
    "Device:Polyfuse_Small": "F",
    
    "Device:Buzzer": "B",
    "Device:Speaker": "B",
    
    "Device:Battery": "BT",
    "Device:Battery_Cell": "BT",
    
    "Device:Transformer_1P_1S": "T",
    "Device:Transformer_1P_2S": "T",
    "Device:Transformer_1P_SS": "T",
    
    "Device:RFShield_OnePiece": "J",
    "Device:RFShield_TwoPieces": "J",
    "Device:RFShield_ThreePieces": "J",
    
    # ═══════════════════════════════════════════════════════════
    # CONNECTORS
    # ═══════════════════════════════════════════════════════════
    "Connector:Conn_01x01": "J",
    "Connector:Conn_01x02": "J",
    "Connector:Conn_01x03": "J",
    "Connector:Conn_01x04": "J",
    "Connector:Conn_01x05": "J",
    "Connector:Conn_01x06": "J",
    "Connector:Conn_01x07": "J",
    "Connector:Conn_01x08": "J",
    "Connector:Conn_01x09": "J",
    "Connector:Conn_01x10": "J",
    "Connector:Conn_01x12": "J",
    "Connector:Conn_01x15": "J",
    "Connector:Conn_01x16": "J",
    "Connector:Conn_01x18": "J",
    "Connector:Conn_01x20": "J",
    "Connector:Conn_01x24": "J",
    "Connector:Conn_01x30": "J",
    "Connector:Conn_01x32": "J",
    "Connector:Conn_01x36": "J",
    "Connector:Conn_01x40": "J",
    "Connector:Conn_02x02": "J",
    "Connector:Conn_02x03": "J",
    "Connector:Conn_02x04": "J",
    "Connector:Conn_02x05": "J",
    "Connector:Conn_02x06": "J",
    "Connector:Conn_02x07": "J",
    "Connector:Conn_02x08": "J",
    "Connector:Conn_02x10": "J",
    "Connector:Conn_02x13": "J",
    "Connector:Conn_02x15": "J",
    "Connector:Conn_02x17": "J",
    "Connector:Conn_02x20": "J",
    "Connector:Conn_02x25": "J",
    "Connector:Conn_Coaxial": "J",
    "Connector:TestPoint": "TP",
    "Connector:TestPoint_Alt": "TP",
    "Connector:TestPoint_2Pole": "TP",
    "Connector:USB_A": "J",
    "Connector:USB_B": "J",
    "Connector:USB_B_Micro": "J",
    "Connector:USB_C_Receptacle": "J",
    "Connector:USB_C_Plug": "J",
    "Connector:AudioJack2": "J",
    "Connector:AudioJack3": "J",
    "Connector:AudioJack4": "J",
    "Connector:Barrel_Jack": "J",
    "Connector:Screw_Terminal_01x02": "J",
    
    "Connector_Generic:Conn_01x01": "J",
    "Connector_Generic:Conn_01x02": "J",
    "Connector_Generic:Conn_01x03": "J",
    "Connector_Generic:Conn_01x04": "J",
    "Connector_Generic:Conn_01x05": "J",
    "Connector_Generic:Conn_01x06": "J",
    "Connector_Generic:Conn_01x08": "J",
    "Connector_Generic:Conn_01x10": "J",
    "Connector_Generic:Conn_02x02": "J",
    "Connector_Generic:Conn_02x03": "J",
    "Connector_Generic:Conn_02x04": "J",
    "Connector_Generic:Conn_02x05": "J",
    
    # ═══════════════════════════════════════════════════════════
    # SWITCHES
    # ═══════════════════════════════════════════════════════════
    "Switch:SW_Push": "SW",
    "Switch:SW_SPST": "SW",
    "Switch:SW_SPDT": "SW",
    "Switch:SW_DPST": "SW",
    "Switch:SW_DPDT": "SW",
    "Switch:SW_DIP_x01": "SW",
    "Switch:SW_DIP_x02": "SW",
    "Switch:SW_DIP_x03": "SW",
    "Switch:SW_DIP_x04": "SW",
    "Switch:SW_Slide_SPDT": "SW",
    
    # ═══════════════════════════════════════════════════════════
    # MECHANICAL
    # ═══════════════════════════════════════════════════════════
    "Mechanical:MountingHole": "H",
    "Mechanical:MountingHole_Pad": "H",
    "Mechanical:Fiducial": "FID",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - AMPLIFIERS
    # ═══════════════════════════════════════════════════════════
    "Amplifier_Operational:": "U",
    "Amplifier_Audio:": "U",
    "Amplifier_Buffer:": "U",
    "Amplifier_Current:": "U",
    "Amplifier_Difference:": "U",
    "Amplifier_Instrumentation:": "U",
    "Amplifier_Video:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - ANALOG
    # ═══════════════════════════════════════════════════════════
    "Analog_ADC:": "U",
    "Analog_DAC:": "U",
    "Analog_Switch:": "U",
    "Analog:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - AUDIO
    # ═══════════════════════════════════════════════════════════
    "Audio:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - COMPARATORS
    # ═══════════════════════════════════════════════════════════
    "Comparator:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - DISPLAY DRIVERS
    # ═══════════════════════════════════════════════════════════
    "Display_Character:": "U",
    "Display_Graphic:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - DRIVERS
    # ═══════════════════════════════════════════════════════════
    "Driver_Display:": "U",
    "Driver_FET:": "U",
    "Driver_LED:": "U",
    "Driver_Motor:": "U",
    "Driver_Relay:": "U",
    "Driver_TEC:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - INTERFACE
    # ═══════════════════════════════════════════════════════════
    "Interface_CAN_LIN:": "U",
    "Interface_CurrentLoop:": "U",
    "Interface_Ethernet:": "U",
    "Interface_Expansion:": "U",
    "Interface_HDMI:": "U",
    "Interface_HID:": "U",
    "Interface_LineDriver:": "U",
    "Interface_Optical:": "U",
    "Interface_Telecom:": "U",
    "Interface_UART:": "U",
    "Interface_USB:": "U",
    "Interface:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - ISOLATORS
    # ═══════════════════════════════════════════════════════════
    "Isolator:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - LOGIC
    # ═══════════════════════════════════════════════════════════
    "Logic_LevelTranslator:": "U",
    "Logic_Programmable:": "U",
    "Logic:": "U",
    "74xx:": "U",
    "74xGxx:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - MCUs & MPUs
    # ═══════════════════════════════════════════════════════════
    "MCU_": "U",
    "Microcontroller_": "U",
    "Processor_": "U",
    "CPU:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - MEMORY
    # ═══════════════════════════════════════════════════════════
    "Memory_Controller:": "U",
    "Memory_EEPROM:": "U",
    "Memory_EPROM:": "U",
    "Memory_Flash:": "U",
    "Memory_NVRAM:": "U",
    "Memory_RAM:": "U",
    "Memory_ROM:": "U",
    "Memory_UniqueID:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - POWER MANAGEMENT
    # ═══════════════════════════════════════════════════════════
    "Power_Management:": "U",
    "Power_Supervisor:": "U",
    "Regulator_Linear:": "U",
    "Regulator_Switching:": "U",
    "Regulator_Controller:": "U",
    "Regulator_Current:": "U",
    "Regulator_SwitchedCapacitor:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - RF
    # ═══════════════════════════════════════════════════════════
    "RF_AM_FM:": "U",
    "RF_Amplifier:": "U",
    "RF_Bluetooth:": "U",
    "RF_GPS:": "U",
    "RF_GSM:": "U",
    "RF_Mixer:": "U",
    "RF_Module:": "U",
    "RF_Switch:": "U",
    "RF_WiFi:": "U",
    "RF_ZigBee:": "U",
    "RF:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - SECURITY
    # ═══════════════════════════════════════════════════════════
    "Security:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - SENSORS
    # ═══════════════════════════════════════════════════════════
    "Sensor_Audio:": "U",
    "Sensor_Current:": "U",
    "Sensor_Energy:": "U",
    "Sensor_Gas:": "U",
    "Sensor_Humidity:": "U",
    "Sensor_Magnetic:": "U",
    "Sensor_Motion:": "U",
    "Sensor_Optical:": "U",
    "Sensor_Pressure:": "U",
    "Sensor_Proximity:": "U",
    "Sensor_Temperature:": "U",
    "Sensor_Touch:": "U",
    "Sensor_Voltage:": "U",
    "Sensor:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # ICs - TIMER
    # ═══════════════════════════════════════════════════════════
    "Timer:": "U",
    "Timer_PLL:": "U",
    "Timer_RTC:": "U",

    # ═══════════════════════════════════════════════════════════
    # ICs - VIDEO
    # ═══════════════════════════════════════════════════════════
    "Video:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # RELAYS
    # ═══════════════════════════════════════════════════════════
    "Relay:": "K",
    "Relay_SolidState:": "K",
    
    # ═══════════════════════════════════════════════════════════
    # REFERENCE VOLTAGE
    # ═══════════════════════════════════════════════════════════
    "Reference_Voltage:": "U",
    "Reference_Current:": "U",
    
    # ═══════════════════════════════════════════════════════════
    # GRAPHICS
    # ═══════════════════════════════════════════════════════════
    "Graphic:": None,
    
    # ═══════════════════════════════════════════════════════════
    # SIMULATION
    # ═══════════════════════════════════════════════════════════
    "Simulation_SPICE:": None,
}

def get_designator_from_lib_id(lib_id: str) -> str:
    """
    Extract the proper designator from a lib_id.
    Extremely robust with comprehensive fallback logic.
    
    Examples:
        "Device:R" -> "R"
        "Device:LED" -> "D"
        "Device:C_Network04" -> "CN"
        "Device:RFShield_TwoPieces" -> "J"
        "MCU_ST_STM32F1:STM32F103" -> "U"
        "Connector:Conn_01x04" -> "J"
        "CustomLib:WeirdPart" -> "U" (fallback)
    """
    if not lib_id:
        return "U"
    
    # Check exact matches first
    if lib_id in LIBRARY_TO_DESIGNATOR:
        result = LIBRARY_TO_DESIGNATOR[lib_id]
        return result if result else "U"
    
    # Check prefix matches (libraries ending with : or _)
    for lib_pattern, designator in LIBRARY_TO_DESIGNATOR.items():
        if lib_pattern.endswith("_") or lib_pattern.endswith(":"):
            if lib_id.startswith(lib_pattern):
                return designator if designator else "U"
    
    # Fallback: aggressive pattern matching on symbol name
    parts = lib_id.split(":")
    if len(parts) == 2:
        lib_name, symbol_name = parts
        
        lib_upper = lib_name.upper()
        symbol_upper = symbol_name.upper()
        
        # ═══════════════════════════════════════════════════════════
        # SYMBOL NAME INFERENCE (most specific - check first)
        # ═══════════════════════════════════════════════════════════
        
        # Networks (check before individual components)
        if "C_NETWORK" in symbol_upper or "CNETWORK" in symbol_upper or "C_NET" in symbol_upper:
            return "CN"
        if "R_NETWORK" in symbol_upper or "RNETWORK" in symbol_upper or "R_NET" in symbol_upper or "R_PACK" in symbol_upper:
            return "RN"
        
        # Shields and special connectors
        if "RFSHIELD" in symbol_upper or "RF_SHIELD" in symbol_upper or "SHIELD" in symbol_upper:
            return "J"
        if "TESTPOINT" in symbol_upper or "TEST_POINT" in symbol_upper or "TP_" in symbol_upper:
            return "TP"
        
        # Switches
        if "SWITCH" in symbol_upper or symbol_upper.startswith("SW_"):
            return "SW"
        
        # Passive components
        if any(x in symbol_upper for x in ["_R_", "_RES", "RESISTOR"]) and "NETWORK" not in symbol_upper:
            return "R"
        if any(x in symbol_upper for x in ["_C_", "_CAP", "CAPACITOR"]) and "NETWORK" not in symbol_upper:
            return "C"
        if any(x in symbol_upper for x in ["_L_", "_IND", "INDUCTOR"]):
            return "L"
        
        # Semiconductors
        if any(x in symbol_upper for x in ["DIODE", "LED", "ZENER", "SCHOTTKY", "_D_"]):
            return "D"
        if any(x in symbol_upper for x in ["_Q_", "TRANS", "MOSFET", "FET", "BJT", "IGBT"]):
            return "Q"
        
        # Other passives
        if any(x in symbol_upper for x in ["CRYSTAL", "XTAL", "OSC", "RESON"]):
            return "Y"
        if any(x in symbol_upper for x in ["FUSE", "POLYF"]):
            return "F"
        if any(x in symbol_upper for x in ["BUZZ", "SPEAK"]):
            return "B"
        if any(x in symbol_upper for x in ["BATT"]):
            return "BT"
        if any(x in symbol_upper for x in ["TRANS", "XFMR", "TRANSFORMER"]):
            return "T"
        
        # Connectors
        if any(x in symbol_upper for x in ["CONN_", "CONNECTOR", "_JACK", "USB_", "BARREL"]):
            return "J"
        
        # ═══════════════════════════════════════════════════════════
        # LIBRARY NAME INFERENCE
        # ═══════════════════════════════════════════════════════════
        
        # MCUs and processors
        if any(x in lib_upper for x in ["MCU", "MICRO", "CPU", "PROCESSOR"]):
            return "U"
        
        # Connectors
        if "CONNECTOR" in lib_upper or "CONN" in lib_upper:
            return "J"
        
        # Switches
        if "SWITCH" in lib_upper:
            return "SW"
        
        # Relays
        if "RELAY" in lib_upper:
            return "K"
        
        # ICs and complex parts
        if any(x in lib_upper for x in ["SENSOR", "DRIVER", "INTERFACE", "REGULATOR", 
                                         "POWER", "LOGIC", "MEMORY", "AMPLIF", "OPAMP",
                                         "ANALOG", "TIMER", "VIDEO", "AUDIO", "ISOLATOR",
                                         "COMPARATOR", "DISPLAY", "RF_", "SECURITY"]):
            return "U"
        
        # Logic families
        if lib_upper.startswith("74") or "LOGIC" in lib_upper:
            return "U"
    
    # Final fallback: assume IC/generic part
    return "U"

def parse_schematic_symbols(sch_path: Path) -> dict:
    """
    Parse schematic and return mapping of {reference: lib_id}.
    Robust regex-based approach that works with any schematic format.
    """
    content = sch_path.read_text(encoding='utf-8')
    symbols = {}
    
    # Pattern to match symbol instances (after lib_symbols section)
    # Look for: (symbol followed by (lib_id "...") then later (property "Reference" "...")
    pattern = r'\(symbol\s*\n\s*\(lib_id\s+"([^"]+)"\)(.*?)\(property\s+"Reference"\s+"([^"]+)"'
    
    matches = re.finditer(pattern, content, re.DOTALL)
    
    for match in matches:
        lib_id = match.group(1)
        reference = match.group(3)
        
        # Only store actual component instances, not template defaults
        # Real references have numbers or are longer than 2 chars
        if reference and lib_id:
            # Skip very short single-letter refs (template defaults like "R", "C", "U")
            if len(reference) > 2 or any(c.isdigit() for c in reference):
                symbols[reference] = lib_id
    
    return symbols

def unannotate_ref_with_lib_id(ref: str, lib_id: str = None) -> str:
    """
    Reset reference to unannotated state using lib_id for ground truth.
    
    TRUE unannotation - strips ALL custom naming and resets to the correct
    component designator based on lib_id.
    
    Examples:
        "R1" + "Device:R" -> "R?"
        "MyResistor10" + "Device:R" -> "R?"
        "1xCN333" + "Device:C_Network04" -> "CN?"
        "aaU2133" + "MCU:STM32" -> "U?"
        "SH-R1" + "Device:R" -> "SH-R?"
        "SH-MyResistor10" + "Device:R" -> "SH-R?"
        "sxxxx123" + "Device:RFShield_TwoPieces" -> "J?"
    """
    # Extract tag prefix (e.g., "SH-" from "SH-MyResistor10")
    tag_prefix, ref_without_tag = extract_tag_prefix(ref)
    
    # If we have lib_id, use it exclusively - don't try to parse the reference
    if lib_id:
        designator = get_designator_from_lib_id(lib_id)
        return f"{tag_prefix}{designator}?"
    
    # No lib_id - fallback for PCB mode or if parsing failed
    # Try to find a standard designator in the reference
    for des in STANDARD_DESIGNATORS:
        if len(des) >= 2:
            # Check if reference ends with this designator + number
            pattern = f'{des}\\d+$'
            if re.search(pattern, ref_without_tag, re.IGNORECASE):
                return f"{tag_prefix}{des}?"
    
    for des in STANDARD_DESIGNATORS:
        if len(des) == 1:
            pattern = f'{des}\\d+$'
            if re.search(pattern, ref_without_tag, re.IGNORECASE):
                return f"{tag_prefix}{des}?"
    
    # If we can't determine the type, default to U (IC)
    return f"{tag_prefix}U?"

# Reference transformation functions
def ref_add_tag(ref: str, tag: str) -> str:
    return ref if ref.startswith(tag) else f"{tag}{ref}"

def ref_strip_tag(ref: str, tag: str) -> str:
    return ref[len(tag):] if ref.startswith(tag) else ref

def ref_strip_all_tags(ref: str) -> str:
    """Strip all prefixes, leaving just component type and number (e.g., SH-R1 -> R1)"""
    return strip_all_tags(ref)

def ref_unannotate(ref: str) -> str:
    """Reset to unannotated state - DEPRECATED: use unannotate_ref_with_lib_id instead"""
    return unannotate_ref_with_lib_id(ref, None)

def ref_find_replace(ref: str, find_s: str, repl_s: str) -> str:
    return ref.replace(find_s, repl_s)

# ---------- Schematic operations (lib_id-aware) ----------

def schematic_preview_and_apply(
    sch_path: Path,
    op,
    tag_or_find,
    repl=None,
    apply=False,
    touch_refs=True,
    touch_labels=True
):
    """
    Pure regex-based editing of .kicad_sch files (S-expression format).
    For unannotate operation, parses lib_id to get ground truth about component types.
    
    op: 'add_tag', 'remove_tag', 'strip_all', 'unannotate', 'find_replace'
    returns (counts dict, sample strings list, changes list)
    """
    
    content = sch_path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)
    
    changes = []
    samples = []
    counts = {"local": 0, "global": 0, "hier": 0, "sheet_pin": 0, "symbol_ref": 0}
    
    # For unannotate, parse lib_id mappings first
    ref_to_lib_id = {}
    if op == "unannotate" and touch_refs:
        ref_to_lib_id = parse_schematic_symbols(sch_path)
        
        if not ref_to_lib_id:
            print(f"[WARN] Could not parse any lib_id mappings from {sch_path.name}")
            print(f"[WARN] Unannotation may not work correctly without lib_id information")
        else:
            print(f"[INFO] Found {len(ref_to_lib_id)} components in {sch_path.name}")
    
    # Track which refs we've already processed (to avoid double-counting)
    processed_refs = {}  # {old_ref: new_ref}
    
    def transform_label(txt: str) -> str:
        if op == "add_tag":
            return add_tag(txt, tag_or_find)
        if op == "remove_tag":
            return strip_tag(txt, tag_or_find)
        if op == "strip_all":
            return strip_all_label_tags(txt)   # label-safe: prefix only, never designator scan
        if op == "unannotate":
            return txt  # Don't modify labels for unannotate
        return txt.replace(tag_or_find, repl if repl else "")
    
    def transform_ref(ref: str) -> str:
        # Check if we've already processed this reference
        if ref in processed_refs:
            return processed_refs[ref]
        
        # Transform the reference
        if op == "add_tag":
            new_ref = ref_add_tag(ref, tag_or_find)
        elif op == "remove_tag":
            new_ref = ref_strip_tag(ref, tag_or_find)
        elif op == "strip_all":
            new_ref = ref_strip_all_tags(ref)
        elif op == "unannotate":
            # Use lib_id if available for this reference
            lib_id = ref_to_lib_id.get(ref)
            if not lib_id:
                # No lib_id found - skip transformation with warning
                processed_refs[ref] = ref
                return ref
            new_ref = unannotate_ref_with_lib_id(ref, lib_id)
        else:
            new_ref = ref_find_replace(ref, tag_or_find, repl if repl else "")
        
        # Record this transformation
        if new_ref != ref:
            processed_refs[ref] = new_ref
            
            # Add to counts and samples (only once per unique ref)
            counts["symbol_ref"] += 1
            lib_info = ""
            if ref in ref_to_lib_id:
                lib_info = f" [{ref_to_lib_id[ref]}]"
            changes.append(("symbol_ref", ref, new_ref, sch_path))
            if len(samples) < 20:
                samples.append(f"reference: {ref} -> {new_ref}{lib_info}")
        else:
            processed_refs[ref] = ref  # No change
        
        return new_ref
    
    new_lines = []

    # Track whether we are inside a (lib_symbols ...) block so we never
    # rename template "Reference" values there (only instance refs outside it).
    _in_lib_symbols = False
    _lib_symbols_depth = 0  # paren depth at entry; exit when it returns

    for line in lines:
        new_line = line

        # --- lib_symbols block tracking ---
        if '(lib_symbols' in line:
            _in_lib_symbols = True
            # Count net paren depth so we know when the block closes.
            # Start from the opening paren of (lib_symbols itself.
            _lib_symbols_depth = sum(1 for c in line if c == '(') - sum(1 for c in line if c == ')')
        elif _in_lib_symbols:
            _lib_symbols_depth += sum(1 for c in line if c == '(') - sum(1 for c in line if c == ')')
            if _lib_symbols_depth <= 0:
                _in_lib_symbols = False
                _lib_symbols_depth = 0

        # --- LABELS ---
        if touch_labels:
            # Local labels: (label "TEXT" ...
            if '(label "' in line:
                def replace_local(m):
                    old = m.group(2)
                    new = transform_label(old)
                    if new != old:
                        counts["local"] += 1
                        changes.append(("local_label", old, new, sch_path))
                        if len(samples) < 10:
                            samples.append(f"local: {old} -> {new}")
                    return f'{m.group(1)}{new}{m.group(3)}'
                
                new_line = re.sub(r'(\(label\s+")(.*?)(")', replace_local, new_line)
            
            # Global labels: (global_label "TEXT" ...
            if '(global_label "' in line:
                def replace_global(m):
                    old = m.group(2)
                    new = transform_label(old)
                    if new != old:
                        counts["global"] += 1
                        changes.append(("global_label", old, new, sch_path))
                        if len(samples) < 10:
                            samples.append(f"global: {old} -> {new}")
                    return f'{m.group(1)}{new}{m.group(3)}'
                
                new_line = re.sub(r'(\(global_label\s+")(.*?)(")', replace_global, new_line)
            
            # Hierarchical labels: (hierarchical_label "TEXT" ...
            if '(hierarchical_label "' in line:
                def replace_hier(m):
                    old = m.group(2)
                    new = transform_label(old)
                    if new != old:
                        counts["hier"] += 1
                        changes.append(("hier_label", old, new, sch_path))
                        if len(samples) < 10:
                            samples.append(f"hier: {old} -> {new}")
                    return f'{m.group(1)}{new}{m.group(3)}'
                
                new_line = re.sub(r'(\(hierarchical_label\s+")(.*?)(")', replace_hier, new_line)
            
            # Sheet pins: (pin "TEXT" ...
            if '(pin "' in line:
                def replace_pin(m):
                    old = m.group(2)
                    new = transform_label(old)
                    if new != old:
                        counts["sheet_pin"] += 1
                        changes.append(("sheet_pin", old, new, sch_path))
                        if len(samples) < 10:
                            samples.append(f"pin: {old} -> {new}")
                    return f'{m.group(1)}{new}{m.group(3)}'
                
                new_line = re.sub(r'(\(pin\s+")(.*?)(")', replace_pin, new_line)
        
        # --- SYMBOL REFERENCES ---
        # Skip reference transforms while inside (lib_symbols ...) to avoid
        # corrupting symbol template references (e.g. "R" in Device:R definition).
        if touch_refs and not _in_lib_symbols:
            # Pattern 1: (property "Reference" "U1" ...
            if '(property "Reference"' in line:
                def replace_ref_property(m):
                    old = m.group(2)
                    new = transform_ref(old)
                    return f'{m.group(1)}{new}{m.group(3)}'

                new_line = re.sub(
                    r'(\(property\s+"Reference"\s+")(.*?)(")',
                    replace_ref_property,
                    new_line
                )

            # Pattern 2: (reference "U1") in instances section
            if '(reference "' in line and '(property' not in line:
                def replace_ref_instance(m):
                    old = m.group(2)
                    new = transform_ref(old)
                    return f'{m.group(1)}{new}{m.group(3)}'

                new_line = re.sub(
                    r'(\(reference\s+")(.*?)(")',
                    replace_ref_instance,
                    new_line
                )
        
        new_lines.append(new_line)
    
    # --- SAVE if modified ---
    if apply and changes:
        backup_path = sch_path.with_suffix(sch_path.suffix + ".bak")
        shutil.copy2(sch_path, backup_path)
        sch_path.write_text(''.join(new_lines), encoding='utf-8')
    
    return counts, samples, changes

# ---------- PCB operations ----------
def pcb_preview_and_apply(board_path: Path, op, tag_or_find, repl=None, apply=False):
    """
    Pure regex-based editing of .kicad_pcb files.
    Works without pcbnew dependency.
    """
    
    content = board_path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)
    
    count = 0
    samples = []
    changes = []
    
    def transform_ref(ref: str) -> str:
        if op == "add_tag":
            return ref_add_tag(ref, tag_or_find)
        if op == "remove_tag":
            return ref_strip_tag(ref, tag_or_find)
        if op == "strip_all":
            return ref_strip_all_tags(ref)
        if op == "unannotate":
            # NOTE: PCB unannotation uses the ref string alone (no lib_id available in
            # .kicad_pcb text).  This fallback heuristic is less accurate than the
            # schematic path — the GUI should surface a warning to the user.
            return unannotate_ref_with_lib_id(ref, None)
        return ref_find_replace(ref, tag_or_find, repl if repl else "")
    
    new_lines = []
    
    for line in lines:
        # PCB footprint reference: KiCad 6: (fp_text reference "U1" ...)
        #                          KiCad 7+/10: (fp_text "reference" "U1" ...)
        if '(fp_text' in line and 'reference' in line.lower():
            def replace_ref(m):
                nonlocal count
                old = m.group(2)
                new = transform_ref(old)
                if new != old:
                    count += 1
                    changes.append(("pcb_ref", old, new, board_path))
                    if len(samples) < 10:
                        samples.append(f"pcb-reference: {old} -> {new}")
                return f'{m.group(1)}{new}{m.group(3)}'

            # Match both KiCad 6 (unquoted type) and KiCad 7+/10 (quoted type)
            new_line = re.sub(r'(\(fp_text\s+"?reference"?\s+")(.*?)(")', replace_ref, line)
            new_lines.append(new_line)
        else:
            new_lines.append(line)
    
    if apply and changes:
        backup_path = board_path.with_suffix(board_path.suffix + ".bak")
        shutil.copy2(board_path, backup_path)
        board_path.write_text(''.join(new_lines), encoding='utf-8')
    
    return count, samples, changes

# ---------- ERC via kicad-cli ----------
def run_erc(top_schematic: Path):
    cmd = ["kicad-cli", "sch", "erc", str(top_schematic)]
    print("[INFO] Running ERC:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("[WARN] kicad-cli not found in PATH; ERC skipped.")

# ---------- Main interactive flow ----------
def main():
    repo_root = Path(".").resolve()
    print("\n=== KiCad Bulk Rename Wizard ===")

    # Print all project folders (modern + legacy) before prompts
    projects = find_kicad_projects(repo_root, include_legacy=True, include_prl=False)
    print_kicad_projects(projects, repo_root, title="Detected KiCad projects (.kicad_pro / .pro)")

    # Choose project
    discovered = discover_projects(repo_root)
    
    # Build options with human-readable names
    options = []
    project_map = {}
    for p in discovered:
        human_name, full_path = get_project_display_name(p, repo_root)
        display = f"{human_name} ({full_path})"
        options.append(display)
        project_map[display] = p
    
    options.append("Enter Custom Path...")
    
    p_idx = prompt_menu("Select project folder:", options)
    if p_idx == len(options) - 1:
        project_path = Path(prompt_text("Project path", allow_empty=False)).resolve()
    else:
        project_path = project_map[options[p_idx]]
    
    print(f"[INFO] Project: {project_path}")

    schematics = list_schematics(project_path)
    boards = list_boards(project_path)
    if not schematics and not boards:
        print("[WARN] No KiCad files found under the selected project.")

    # Choose operation
    op_map = [
        "Add Tag Prefix",
        "Remove Tag Prefix",
        "Strip All Tags (remove all prefixes)",
        "Reset to Unannotated (uses lib_id for correct type)",
        "Custom Find/Replace"
    ]
    op_idx = prompt_menu("Choose operation:", op_map)
    
    if op_idx == 0:
        op = "add_tag"
        tag = prompt_text("Tag prefix to ADD (e.g., SH- or CG-)", allow_empty=False)
        find, repl = tag, None
    elif op_idx == 1:
        op = "remove_tag"
        tag = prompt_text("Tag prefix to REMOVE (e.g., SH- or CG-)", allow_empty=False)
        find, repl = tag, None
    elif op_idx == 2:
        op = "strip_all"
        find, repl, tag = None, None, None
        print("[INFO] Will strip all tag prefixes (e.g., SH-R1 -> R1, CG-U5 -> U5)")
    elif op_idx == 3:
        op = "unannotate"
        find, repl, tag = None, None, None
        print("[INFO] Will reset references to unannotated state using lib_id")
        print("[INFO] TRUE UNANNOTATION: strips ALL custom names")
        print("[INFO] Examples:")
        print("[INFO]   R1 -> R? (Device:R)")
        print("[INFO]   MyResistor10 -> R? (Device:R)")
        print("[INFO]   1xCN333 -> CN? (Device:C_Network04)")
        print("[INFO]   sxxxx123 -> J? (Device:RFShield_TwoPieces)")
        print("[INFO]   aaU2133 -> U? (MCU:STM32)")
        print("[INFO]   SH-CustomName123 -> SH-R? (with tag preserved)")
    else:
        op = "find_replace"
        find = prompt_text("Find text", allow_empty=False)
        repl = prompt_text("Replace with", default="", allow_empty=True)
        tag = None

    # Choose scope
    scope_opts = [
        "Schematics: Nets/Labels + Sheet Pins",
        "Schematics: Component References",
        "PCBs: Footprint References",
        "All of the Above"
    ]
    scope_idx = prompt_menu("Select scope:", scope_opts)
    scope_sch_labels = scope_idx in (0, 3)
    scope_sch_refs = scope_idx in (1, 3)
    scope_pcb = scope_idx in (2, 3)

    # Preview
    print("[INFO] Previewing changes (dry run)...")
    total_counts = {"local": 0, "global": 0, "hier": 0, "sheet_pin": 0, "symbol_ref": 0, "pcb_ref": 0}
    preview_samples = []
    all_changes = []

    # Schematics pass
    if schematics and (scope_sch_labels or scope_sch_refs):
        for sch in schematics:
            counts, samples, changes = schematic_preview_and_apply(
                sch,
                op,
                find if tag is None else tag,
                repl=repl,
                apply=False,
                touch_refs=scope_sch_refs,
                touch_labels=scope_sch_labels
            )
            for k in total_counts:
                if k in counts:
                    total_counts[k] += counts[k]
            preview_samples.extend(samples)
            all_changes.extend(changes)

    # PCB pass
    if boards and scope_pcb:
        for brd in boards:
            cnt, samples, changes = pcb_preview_and_apply(
                brd,
                op,
                find if tag is None else tag,
                repl=repl,
                apply=False
            )
            total_counts["pcb_ref"] += cnt
            preview_samples.extend(samples)
            all_changes.extend(changes)

    # Show summary
    print("\n--- Preview Summary ---")
    print(f"Local labels:        {total_counts['local']}")
    print(f"Global labels:       {total_counts['global']}")
    print(f"Hierarchical labels: {total_counts['hier']}")
    print(f"Sheet pins:          {total_counts['sheet_pin']}")
    print(f"Schematic refs:      {total_counts['symbol_ref']}")
    print(f"PCB references:      {total_counts['pcb_ref']}")
    if preview_samples:
        print("\nExamples:")
        for s in preview_samples[:10]:
            print("  " + s)
    else:
        print("  No changes detected.")

    # Save preview logs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    preview_log = LOG_DIR / f"{timestamp}_preview.json"
    preview_log.write_text(json.dumps([
        {"type": k, "count": v} for k, v in total_counts.items()
    ], indent=2))
    
    changes_log = LOG_DIR / f"{timestamp}_changes.json"
    changes_log.write_text(json.dumps([
        {"type": t, "old": o, "new": n, "file": str(p)} for (t, o, n, p) in all_changes
    ], indent=2))
    
    try:
        rel_preview = preview_log.relative_to(repo_root)
        rel_changes = changes_log.relative_to(repo_root)
    except ValueError:
        rel_preview = preview_log
        rel_changes = changes_log
    
    print(f"[INFO] Wrote {rel_preview}")
    print(f"[INFO] Wrote {rel_changes}")

    # Confirm apply
    if not prompt_yes_no("Apply changes now?", default="n"):
        print("[INFO] Aborted. No files modified.")
        return

    # Apply changes
    print("[INFO] Applying changes...")
    applied_changes = []

    if schematics and (scope_sch_labels or scope_sch_refs):
        for sch in schematics:
            counts, samples, changes = schematic_preview_and_apply(
                sch,
                op,
                find if tag is None else tag,
                repl=repl,
                apply=True,
                touch_refs=scope_sch_refs,
                touch_labels=scope_sch_labels
            )
            applied_changes.extend(changes)

    if boards and scope_pcb:
        for brd in boards:
            cnt, samples, changes = pcb_preview_and_apply(
                brd,
                op,
                find if tag is None else tag,
                repl=repl,
                apply=True
            )
            applied_changes.extend(changes)

    applied_log = LOG_DIR / f"{timestamp}_applied.json"
    applied_log.write_text(json.dumps([
        {"type": t, "old": o, "new": n, "file": str(p)} for (t, o, n, p) in applied_changes
    ], indent=2))
    
    try:
        rel_applied = applied_log.relative_to(repo_root)
    except:
        rel_applied = applied_log
    
    print(f"[INFO] Wrote {rel_applied}")

    # ERC
    if schematics and prompt_yes_no("Run ERC via kicad-cli?", default="y"):
        top = pick_top_schematic(schematics)
        if top:
            run_erc(top)
        else:
            print("[INFO] ERC skipped.")

    print("\nDone.")
    print("Backups (.bak) were created next to modified files.")
    print(f"Logs saved to {LOG_DIR.relative_to(repo_root)}/")

if __name__ == "__main__":
    # Check if GUI should be launched
    if len(sys.argv) > 1 and sys.argv[1] == "--gui":
        print("Launching GUI...")
        try:
            from wizard_gui import main as gui_main
            gui_main()
        except ImportError as e:
            print(f"[ERROR] Could not launch GUI: {e}")
            print("Run: python tools/wizard_gui.py")
            sys.exit(1)
    else:
        main()  # Run CLI version