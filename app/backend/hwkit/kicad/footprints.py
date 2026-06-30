"""
footprints.py — KiCad ``.kicad_mod`` 3D-model correctness primitives.

When the manager relocates a part's 3D model into the shared ``My3DModels``
folder it fails to write a valid ``(model …)`` line back into the footprint:
in the real library 92 of 93 footprints have no model line at all, and the one
that does uses a bare filename with no path. So no 3D model attaches when the
footprint is placed.

These helpers insert or repair the footprint's ``(model …)`` line so it points
at ``${MY3DMODELS}/<file>`` — the KiCad path variable the app defines for the
shared model folder. See app/backend/README.md, requirement #1.
"""
from __future__ import annotations

import re

DEFAULT_MODEL_VAR = "${MY3DMODELS}"

# Matches the path token right after `(model`, quoted or bare.
_MODEL_PATH = re.compile(r'(\(model\s+)("[^"]*"|[^"\s)]+)')


def has_model(footprint_text: str) -> bool:
    return "(model" in footprint_text


def _model_block(filename: str, var: str) -> str:
    return (
        f'  (model "{var}/{filename}"\n'
        f"    (offset (xyz 0 0 0))\n"
        f"    (scale (xyz 1 1 1))\n"
        f"    (rotate (xyz 0 0 0))\n"
        f"  )\n"
    )


def set_model_path(footprint_text: str, filename: str, var: str = DEFAULT_MODEL_VAR) -> str:
    """Repair the path of the first existing ``(model …)`` line."""
    def repl(m: re.Match) -> str:
        return f'{m.group(1)}"{var}/{filename}"'

    return _MODEL_PATH.sub(repl, footprint_text, count=1)


def ensure_model(footprint_text: str, filename: str, var: str = DEFAULT_MODEL_VAR) -> str:
    """Guarantee the footprint references ``${var}/<filename>`` exactly once.

    Repairs an existing ``(model …)`` line, or inserts a full model block before
    the footprint's closing paren when none exists.
    """
    if has_model(footprint_text):
        return set_model_path(footprint_text, filename, var)
    idx = footprint_text.rstrip().rfind(")")
    if idx == -1:
        return footprint_text
    return footprint_text[:idx] + _model_block(filename, var) + footprint_text[idx:]
