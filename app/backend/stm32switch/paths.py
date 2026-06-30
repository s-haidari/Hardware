"""
paths.py — repository-root discovery and canonical output locations.

All generated artifacts live under deterministic paths so ``build-all`` is
reproducible and ``check_outputs`` can find them without configuration.
"""
from __future__ import annotations

from pathlib import Path

# Standard LQFP package geometry (JEDEC / ST datasheet reference values, mm).
# Reference data — not MCU-specific invented data.
PACKAGE_GEOMETRY: dict[str, dict[str, float | int]] = {
    "LQFP48":  {"pins": 48,  "pitch_mm": 0.5, "body_mm": 7.0},
    "LQFP64":  {"pins": 64,  "pitch_mm": 0.5, "body_mm": 10.0},
    "LQFP100": {"pins": 100, "pitch_mm": 0.5, "body_mm": 14.0},
    "LQFP144": {"pins": 144, "pitch_mm": 0.5, "body_mm": 20.0},
    "LQFP176": {"pins": 176, "pitch_mm": 0.5, "body_mm": 24.0},
}

# Packages the generator targets, in build order.
TARGET_PACKAGES: list[str] = ["LQFP48", "LQFP64", "LQFP100", "LQFP144", "LQFP176"]

# The parent backplane is designed as a 176-lane superset (spec section 13).
SUPERSET_LANES = 176
BANK_SIZE = 32
BANKS = ["A", "B", "C", "D", "E", "F"]


def repo_root() -> Path:
    """Walk up from this file to the repository root (the dir holding .git)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "stm32_profiles.sqlite").exists():
            return parent
    # Fallback: two levels up from src/stm32switch/.
    return here.parents[2]


def default_db_path() -> Path:
    return repo_root() / "stm32_profiles.sqlite"


# ── output directories ─────────────────────────────────────────────────────

def data_dir() -> Path:        return repo_root() / "data"
def packages_dir() -> Path:    return data_dir() / "packages"
def generated_dir() -> Path:   return data_dir() / "generated"
def hardware_dir() -> Path:    return repo_root() / "hardware"
def cell_library_dir() -> Path:   return hardware_dir() / "cell_library"
def parent_routers_dir() -> Path: return hardware_dir() / "parent_routers"
def docs_dir() -> Path:        return repo_root() / "docs"
def schemas_dir() -> Path:     return repo_root() / "schemas"


def package_dir(package: str) -> Path:
    return packages_dir() / package


def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


def lane_id(lane_number: int) -> str:
    """Canonical machine lane id, e.g. 7 -> 'CARD_LANE_007'."""
    return f"CARD_LANE_{lane_number:03d}"


def lane_bank(lane_number: int) -> str:
    """Bank letter for a 1-based lane number (A..F, 32 lanes per bank)."""
    idx = (lane_number - 1) // BANK_SIZE
    idx = min(idx, len(BANKS) - 1)
    return f"BANK_{BANKS[idx]}"


def lane_index_in_bank(lane_number: int) -> int:
    return ((lane_number - 1) % BANK_SIZE) + 1
