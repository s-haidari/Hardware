"""
schematic.py — repair the ``Footprint`` field on symbols already placed in a
``.kicad_sch``.

Fixing the symbol library does not touch symbols already in a schematic — KiCad
copies the ``Footprint`` field into each placed instance. This repairs those
instances, but only for parts whose footprint actually lives in the shared
``MyFootprints.pretty`` (so standard-library parts like ``Resistor_SMD:R_0402``
are never disturbed).
"""
from __future__ import annotations

import re
from pathlib import Path

from .symbols import DEFAULT_FP_NICKNAME, footprint_name

_FP_PROP = re.compile(r'(\(property\s+"Footprint"\s+")([^"]*)(")')


def repair_schematic_footprints(sch_text: str, known: set[str],
                                nickname: str = DEFAULT_FP_NICKNAME) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite Footprint fields whose footprint is in ``known`` to
    ``<nickname>:<name>``. Returns (new_text, [(old, new), …])."""
    changes: list[tuple[str, str]] = []

    def repl(m: re.Match) -> str:
        value = m.group(2)
        name = footprint_name(value)
        target = f"{nickname}:{name}"
        if name in known and value != target:
            changes.append((value, target))
            return m.group(1) + target + m.group(3)
        return m.group(0)

    return _FP_PROP.sub(repl, sch_text), changes


def repair_schematic_file(path: Path, known: set[str],
                          nickname: str = DEFAULT_FP_NICKNAME, *,
                          dry_run: bool = True, backup: bool = True) -> list[tuple[str, str]]:
    """Repair one ``.kicad_sch`` file. Dry-run by default (reports, writes
    nothing). When writing, leaves a ``.bak`` beside the original."""
    text = path.read_text(encoding="utf-8", errors="replace")
    new_text, changes = repair_schematic_footprints(text, known, nickname)
    if changes and not dry_run:
        if backup:
            path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8", newline="\n")
        path.write_text(new_text, encoding="utf-8", newline="\n")
    return changes
