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


def netclass_standard_path() -> Path:
    """The canonical netclass standard YAML (mirror of the vault page
    'Net Class Colors & Styles'). Overridable via ``HWKIT_NETCLASSES``."""
    env = os.environ.get("HWKIT_NETCLASSES")
    if env:
        return Path(env)
    return Path.home() / "git" / "pcb-build-system" / "data" / "net-classes.yaml"


def kicad_config_dir() -> Path | None:
    """KiCad's per-user config dir (holds fp-lib-table, sym-lib-table,
    kicad_common.json). Picks the highest version. Override: ``HWKIT_KICAD_CONFIG``."""
    env = os.environ.get("HWKIT_KICAD_CONFIG")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    base = Path(appdata) / "kicad"
    if not base.exists():
        return None
    versions = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)
    return versions[0] if versions else base


MODEL_DIR_VAR = "MY3DMODELS"


def cubemx_source_dir() -> Path:
    """The CubeMX MCU XML set the database is built from. Override: ``HWKIT_CUBEMX``."""
    env = os.environ.get("HWKIT_CUBEMX")
    if env:
        return Path(env)
    bundled = Path(__file__).resolve().parents[2] / "cubemx_db" / "mcu"  # app/backend/cubemx_db/mcu
    if bundled.exists():
        return bundled
    return Path.home() / "git" / "STMP" / "src" / "cubemx_db" / "mcu"


def authority_dir() -> Path:
    """Where the pinout authority files are written (the vault data/ per spec).
    Override: ``HWKIT_AUTHORITY``."""
    env = os.environ.get("HWKIT_AUTHORITY")
    if env:
        return Path(env)
    return Path.home() / "Documents" / "Obsidian" / "Brain" / "data"


def downloads_dir() -> Path:
    """Folder watched for incoming part ZIPs. Override: ``HWKIT_DOWNLOADS``."""
    env = os.environ.get("HWKIT_DOWNLOADS")
    if env:
        return Path(env)
    return libs_root().parent / "downloads"


def repo_root() -> Path:
    """The git repo that holds the library (for the git panel). Override: ``HWKIT_REPO``."""
    env = os.environ.get("HWKIT_REPO")
    if env:
        return Path(env)
    return libs_root().parent


def libs_root() -> Path:
    """The KiCad library root (MySymbols / MyFootprints.pretty / My3DModels)."""
    env = os.environ.get("HWKIT_LIBS")
    if env:
        return Path(env)
    # repo layout: app/backend/hwkit/core/config.py -> repo root is parents[4]
    return Path(__file__).resolve().parents[4] / "libs"
