#!/usr/bin/env python3
"""nd_commit_msg.py — pure conventional-commit message builders (Wave 1 · GIT-01).

Every Library mutation the app auto-commits used to get a flat, generic line
("Library: drop in footprint X", "Auto-update: processed foo.zip"). These pure
functions turn a *structured* change into a conventional-commit message that
names the component (scope ``lib``) and the changed fields, so the git history
reads like a changelog. No side effects, no git — just (change) -> str — so they
are trivially unit-testable and the call sites stay thin.

Grammar (locked in the WS-E design):
  * add an asset (footprint / 3D model / symbol)  -> ``feat(lib): add …``
  * set/edit a metadata field                     -> ``chore(lib): set …``
  * import part(s) from a ZIP / folder            -> ``feat(lib): import N part(s) …``
    with a body summarising what auto-linked / enriched (the ``finalize_import``
    change-set that used to be discarded).
"""
from __future__ import annotations

from typing import Iterable, Optional, Union

__all__ = [
    "SCOPE",
    "field_set",
    "add_footprint",
    "add_model",
    "add_symbol",
    "import_parts",
]

SCOPE = "lib"

# How many part names to spell out in an import subject before eliding the rest.
_MAX_NAMES = 6


def _subject(type_: str, summary: str) -> str:
    """A conventional-commit subject line: ``type(lib): summary``."""
    return f"{type_}({SCOPE}): {summary}"


def _join(symbols: Union[str, Iterable[str], None]) -> str:
    """Render one symbol name or an iterable of them as a comma list."""
    if not symbols:
        return ""
    if isinstance(symbols, str):
        return symbols
    return ", ".join(str(s) for s in symbols)


def _plural(n: int, noun: str) -> str:
    """``"1 footprint"`` / ``"2 footprints"`` — naive English pluralization."""
    return f"{n} {noun}" + ("" if n == 1 else "s")


def field_set(field: str, part: str) -> str:
    """A single-field inline edit: ``chore(lib): set <field> on <part>``."""
    return _subject("chore", f"set {field} on {part}")


def add_footprint(stem: str, symbols: Union[str, Iterable[str], None] = None) -> str:
    """A footprint drop-in linked to one or more symbols."""
    tail = f" to {_join(symbols)}" if symbols else ""
    return _subject("feat", f"add footprint {stem}{tail}")


def add_model(name: str, footprint: Optional[str] = None) -> str:
    """A 3D model drop-in attached to a footprint."""
    tail = f" to {footprint}" if footprint else ""
    return _subject("feat", f"add 3D model {name}{tail}")


def add_symbol(filename: str) -> str:
    """A symbol-file drop-in merged into the library."""
    return _subject("feat", f"add symbol {filename}")


def import_parts(
    names: Union[str, Iterable[str], None],
    linked: Optional[dict] = None,
    enriched: Optional[dict] = None,
) -> str:
    """An import of one or more parts, with an optional body describing what the
    post-import ``finalize_import`` pass auto-linked and enriched.

    ``linked``   — the ``finalize_import`` ``{"footprint_count", "model_count"}``.
    ``enriched`` — the ``finalize_import`` ``{"changes": [...], ...}``.
    Both are optional; when absent (e.g. a manual folder import that skips
    finalize) the message is just the subject line."""
    if isinstance(names, str):
        names = [names]
    names = [str(n) for n in (names or [])]
    n = len(names)
    shown = ", ".join(names[:_MAX_NAMES]) + ("…" if n > _MAX_NAMES else "")
    summary = f"import {_plural(n, 'part')}" + (f" ({shown})" if shown else "")
    subject = _subject("feat", summary)

    body: list[str] = []
    if linked:
        bits = []
        fc = int(linked.get("footprint_count") or 0)
        mc = int(linked.get("model_count") or 0)
        if fc:
            bits.append(_plural(fc, "footprint"))
        if mc:
            bits.append(_plural(mc, "3D model"))
        if bits:
            body.append("Auto-linked " + ", ".join(bits))
    if enriched and enriched.get("changes"):
        c = len(enriched["changes"])
        body.append(f"Enriched {_plural(c, 'symbol')} from Mouser")

    if body:
        return subject + "\n\n" + "\n".join(body)
    return subject
