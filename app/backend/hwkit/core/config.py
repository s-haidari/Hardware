"""
config.py — per-machine path resolution. No hard-coded user paths (the legacy
tools/config.json baked in another machine's `developer` paths); everything is
discovered or overridable by environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path


def stm_database_path() -> Path:
    """Resolve the STM32 CubeMX profile database.

    Order: ``HWKIT_DB`` env var -> the installed STM-Helper data dir -> a repo
    checkout next to this one. The first existing path wins; otherwise the
    STM-Helper data location is returned (so callers can report it as missing).
    """
    env = os.environ.get("HWKIT_DB")
    if env:
        return Path(env)

    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "STM-Helper" / "stm32_profiles.sqlite")
    candidates.append(Path.home() / "git" / "STMP" / "stm32_profiles.sqlite")

    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def libs_root() -> Path:
    """The KiCad library root (MySymbols / MyFootprints.pretty / My3DModels)."""
    env = os.environ.get("HWKIT_LIBS")
    if env:
        return Path(env)
    # repo layout: app/backend/hwkit/core/config.py -> repo root is parents[4]
    return Path(__file__).resolve().parents[4] / "libs"
