"""
symbols.py — KiCad ``.kicad_sym`` correctness primitives.

The library manager merges every part's footprint into one shared library
(``MyFootprints.pretty``), but the symbols it imports keep the footprint
library nickname they shipped with — either a per-part nickname
(``STUSB4500QTR:QFN50…``) or none at all (bare ``RM_10_ADI``). KiCad resolves
a footprint by the nickname before the colon, so neither form points at the
shared library, and the placed symbol gets no footprint.

These helpers rewrite the symbol ``Footprint`` field to
``MyFootprints:<footprintName>`` so it resolves against the one registered
library. See app/backend/README.md, requirement #1.
"""
from __future__ import annotations

import re

DEFAULT_FP_NICKNAME = "MyFootprints"

# Matches:  (property "Footprint" "<value>"
_FP_PROP = re.compile(r'(\(property\s+"Footprint"\s+")([^"]*)(")')


def footprint_name(value: str) -> str:
    """The footprint name with any library nickname stripped.

    ``"STUSB4500QTR:QFN50…"`` -> ``"QFN50…"`` ; bare ``"RM_10_ADI"`` -> itself.
    """
    value = (value or "").strip()
    if not value:
        return ""
    return value.split(":")[-1]


def qualify_footprint(value: str, nickname: str = DEFAULT_FP_NICKNAME) -> str:
    """Return ``<nickname>:<footprintName>`` for the shared library.

    Idempotent; empty stays empty.
    """
    name = footprint_name(value)
    return f"{nickname}:{name}" if name else ""


def rewrite_symbol_footprint(symbol_text: str, nickname: str = DEFAULT_FP_NICKNAME) -> str:
    """Rewrite the ``Footprint`` property inside a symbol block to the shared lib."""
    def repl(m: re.Match) -> str:
        return m.group(1) + qualify_footprint(m.group(2), nickname) + m.group(3)

    return _FP_PROP.sub(repl, symbol_text, count=1)
