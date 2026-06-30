"""
libtable.py — register the shared libraries in KiCad and define ${MY3DMODELS}.

The importer writes correct files (footprint nickname ``MyFootprints:<name>`` and
model path ``${MY3DMODELS}/<file>``), but those only resolve in KiCad once:
  * ``MySymbols`` is in sym-lib-table and ``MyFootprints`` is in fp-lib-table, and
  * the ``MY3DMODELS`` environment variable is defined in kicad_common.json.

This module makes all three true, idempotently. Dry-run aware so the app can
report what would change before touching the user's KiCad config.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


def _has_lib(text: str, nickname: str) -> bool:
    return re.search(r'\(name\s+"%s"\)' % re.escape(nickname), text) is not None


def ensure_lib_entry(path: Path, root: str, nickname: str, uri: str,
                     *, descr: str = "", dry_run: bool = False) -> bool:
    """Ensure a ``(lib …)`` row for ``nickname`` exists in a KiCad lib-table.
    Returns True when a change was needed (and applied unless dry_run)."""
    header = f"({root}\n\t(version 7)\n)\n"
    text = path.read_text(encoding="utf-8") if path.exists() else header
    if _has_lib(text, nickname):
        return False
    entry = f'\t(lib (name "{nickname}") (type "KiCad") (uri "{uri}") (options "") (descr "{descr}"))\n'
    idx = text.rstrip().rfind(")")
    new_text = text[:idx] + entry + text[idx:]
    if not dry_run:
        path.write_text(new_text, encoding="utf-8", newline="\n")
    return True


def ensure_env_var(common_path: Path, name: str, value: str, *, dry_run: bool = False) -> bool:
    """Ensure kicad_common.json defines environment var ``name = value``."""
    data: dict = {}
    if common_path.exists():
        try:
            data = json.loads(common_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    env = data.get("environment")
    if not isinstance(env, dict):
        env = {}
    vars_ = env.get("vars")
    if not isinstance(vars_, dict):
        vars_ = {}
    if vars_.get(name) == value:
        return False
    vars_[name] = value
    env["vars"] = vars_
    data["environment"] = env
    if not dry_run:
        common_path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")
    return True


@dataclass
class RegisterResult:
    sym_lib_added: bool
    fp_lib_added: bool
    env_var_set: bool
    dry_run: bool

    @property
    def changed(self) -> bool:
        return self.sym_lib_added or self.fp_lib_added or self.env_var_set


def register_libraries(kicad_config_dir: Path, libs_root: Path,
                       model_var: str = "MY3DMODELS", *, dry_run: bool = False) -> RegisterResult:
    """Register MySymbols + MyFootprints and define ${model_var} -> My3DModels."""
    sym = ensure_lib_entry(
        kicad_config_dir / "sym-lib-table", "sym_lib_table",
        "MySymbols", str(libs_root / "MySymbols.kicad_sym").replace("\\", "/"),
        dry_run=dry_run)
    fp = ensure_lib_entry(
        kicad_config_dir / "fp-lib-table", "fp_lib_table",
        "MyFootprints", str(libs_root / "MyFootprints.pretty").replace("\\", "/"),
        dry_run=dry_run)
    envset = ensure_env_var(
        kicad_config_dir / "kicad_common.json", model_var,
        str(libs_root / "My3DModels").replace("\\", "/"), dry_run=dry_run)
    return RegisterResult(sym, fp, envset, dry_run)
