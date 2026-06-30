"""
csvio.py — deterministic CSV writing helpers.

Generated CSVs are byte-stable: fixed column order, ``\\n`` line endings, no
timestamps, and list cells joined with a stable separator.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

LIST_SEP = " | "
TODO = "TODO_SOURCE_REQUIRED"


def cell(value) -> str:
    """Render one CSV cell deterministically."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple, set)):
        items = sorted(value, key=str) if isinstance(value, set) else list(value)
        return LIST_SEP.join(str(x) for x in items)
    if isinstance(value, float):
        # avoid 1.0 vs 1 drift; trim trailing zeros
        s = f"{value:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(value)


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict]) -> int:
    """Write rows (list of dicts) with the given column order. Returns row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([cell(r.get(c)) for c in columns])
            n += 1
    return n
