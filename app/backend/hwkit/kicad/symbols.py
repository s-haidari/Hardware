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


# ── library merge (ported from tools/LibraryManager.py, UI stripped) ─────────

SYMBOL_LIB_HEADER = '(kicad_symbol_lib (version 20211014) (generator "hwkit"))\n)\n'


def extract_symbol_blocks(src_text: str) -> list[str]:
    """Return each full ``(symbol …)`` block. Balanced-paren scan, quote-aware."""
    blocks: list[str] = []
    s = src_text
    n = len(s)
    i = 0
    while i < n:
        if s[i] == "(" and s.startswith("(symbol", i):
            start = i
            j = i
            depth = 0
            while j < n:
                ch = s[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        blocks.append(s[start:j + 1])
                        i = j + 1
                        break
                elif ch == '"':
                    j += 1
                    while j < n and s[j] != '"':
                        j += 1
                j += 1
            continue
        elif s[i] == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 1
        i += 1
    return blocks


def symbol_name(block: str) -> str:
    """The symbol's name (library nickname stripped)."""
    head = block.splitlines()[0] if block else ""
    try:
        if '(symbol "' in head:
            start = head.index('(symbol "') + len('(symbol "')
            end = head.index('"', start)
            return head[start:end].split(":")[-1]
    except ValueError:
        pass
    return head.strip()


def insert_blocks(target_text: str, blocks: list[str]) -> str:
    """Insert blocks just before the library's top-level closing paren."""
    depth = 0
    last_close = None
    for idx, ch in enumerate(target_text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                last_close = idx
    if last_close is None:
        return SYMBOL_LIB_HEADER.rstrip()[:-1] + "\n" + "\n".join(blocks) + "\n)\n"
    return target_text[:last_close] + "\n" + "\n".join(blocks) + "\n" + target_text[last_close:]


def merge_into_library(target_text: str, incoming: list[str],
                       nickname: str = DEFAULT_FP_NICKNAME) -> tuple[str, list[str]]:
    """Merge incoming symbol blocks into a library, fixing the footprint nickname
    on each and skipping names already present. Returns (new_text, added_names).
    """
    if not target_text.strip():
        target_text = SYMBOL_LIB_HEADER
    existing = {symbol_name(b) for b in extract_symbol_blocks(target_text)}
    to_add: list[str] = []
    added: list[str] = []
    for block in incoming:
        name = symbol_name(block)
        if name in existing or name in added:
            continue
        to_add.append(rewrite_symbol_footprint(block, nickname))
        added.append(name)
    if not to_add:
        return target_text, []
    return insert_blocks(target_text, to_add), added


def dedupe_library(text: str) -> tuple[str, int]:
    """Keep only the first block of each symbol name. Returns (new_text, removed)."""
    blocks = extract_symbol_blocks(text)
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0
    for b in blocks:
        name = symbol_name(b)
        if name in seen:
            removed += 1
            continue
        seen.add(name)
        kept.append(b)
    if not removed:
        return text, 0
    return insert_blocks(SYMBOL_LIB_HEADER, kept), removed


def remove_symbol(text: str, name: str) -> tuple[str, int]:
    """Remove every block named ``name``. Returns (new_text, removed_count)."""
    blocks = extract_symbol_blocks(text)
    kept = [b for b in blocks if symbol_name(b) != name]
    removed = len(blocks) - len(kept)
    if not removed:
        return text, 0
    return insert_blocks(SYMBOL_LIB_HEADER, kept), removed
