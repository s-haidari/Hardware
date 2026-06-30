"""
group_labels.py — human-facing exact-group labels.

Backend keeps machine ids like ``LQFP100_FULL_G000``; the UI and the canonical
CSV present clean labels: Group A, Group B, … Group Z, Group AA, Group AB, …
Baseline (rank 0) is Group A.  This is the single canonical implementation
(the UI previously had its own copy in ``main_window._group_letter``).
"""
from __future__ import annotations


def letter_for_rank(rank: int) -> str:
    """0→A, 1→B, …, 25→Z, 26→AA, 27→AB, …  (spreadsheet-column style)."""
    if rank < 0:
        return "?"
    s = ""
    n = rank + 1
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def label_for_rank(rank: int) -> str:
    """Full display label, e.g. rank 0 → 'Group A'."""
    return f"Group {letter_for_rank(rank)}"
