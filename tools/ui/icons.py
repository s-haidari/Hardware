"""ui.icons — the ONE unified icon set (Refined-Neutral iconography).

Every glyph shares one language: a 16x16 grid, `fill="none"` stroke icons at a
single `stroke-width="1.25"` with round caps/joins, `stroke="currentColor"` so
`widgets.svg_icon()` can tint them from a text tier. Fixed shapes (a pin-1 dot,
a gear hub) use `fill="currentColor" stroke="none"` and carry no stroke-width.

Redesign notes (from the 2026-07-08 icon audit):
- unified the 1.2/1.3 stroke-width split to 1.25 + round caps everywhere;
- `sun` is a sun (single center circle + detached radial rays); `settings` is a
  continuous 8-notch cog *outline* + axle hole (no rays) — the two silhouettes
  are unmistakably different even when stacked in the nav footer;
- `bench` reads as an MCU/chip (pinned body + pin-1 dot), `routing` as pads +
  a via joined by rounded traces, `git` as a clean branch/merge.
- added reusable action glyphs (symbol / footprint / cube / search / plus /
  check / alert) for Phase-B empty states and inline affordances.
"""
from __future__ import annotations

_OPEN = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" '
         'stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round">')


def _svg(body: str) -> str:
    return f"{_OPEN}{body}</svg>"


GLYPHS = {
    # ── nav + chrome ────────────────────────────────────────────────────────
    "ham": _svg('<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>'),
    # help: a question mark in a ring — the keyboard-shortcuts reference item.
    "help": _svg('<circle cx="8" cy="8" r="5.6"/>'
                 '<path d="M6.4 6.3a1.6 1.6 0 0 1 3.1.5c0 1.1-1.5 1.3-1.5 2.4"/>'
                 '<path d="M8 11.2v.05"/>'),
    # activity: a pulse/waveform line — the Activity-log toggle in the nav footer.
    "activity": _svg('<path d="M2.2 8h2.3l1.5-3.6 2.7 7.2 1.5-3.6h3.6"/>'),
    "bench": _svg(
        '<rect x="4.5" y="4.5" width="7" height="7" rx="1.2"/>'
        '<path d="M6.5 4.5V2.6M9.5 4.5V2.6M6.5 11.5v1.9M9.5 11.5v1.9'
        'M4.5 6.5H2.6M4.5 9.5H2.6M11.5 6.5h1.9M11.5 9.5h1.9"/>'
        '<circle cx="6.4" cy="6.4" r="0.7" fill="currentColor" stroke="none"/>'),
    "library": _svg(
        '<rect x="3" y="4" width="2.5" height="8.4" rx="0.5"/>'
        '<rect x="6.1" y="3.2" width="2.5" height="9.2" rx="0.5"/>'
        '<rect x="9.2" y="4.6" width="2.5" height="7.8" rx="0.5"/>'
        '<path d="M2.3 12.8h11.4"/>'),
    "projects": _svg(
        '<path d="M2.3 5.4a1 1 0 0 1 1-1h3.1l1.5 1.6h4.8a1 1 0 0 1 1 1'
        'v4.2a1 1 0 0 1-1 1H3.3a1 1 0 0 1-1-1z"/>'),
    "routing": _svg(
        '<circle cx="3" cy="3.2" r="1.15"/>'
        '<circle cx="13" cy="12.8" r="1.15"/>'
        '<circle cx="8" cy="8" r="1.15"/>'
        '<path d="M3 4.35V6.6a1 1 0 0 0 1 1h2.85"/>'
        '<path d="M9.15 8H11a1 1 0 0 1 1 1v2.65"/>'),
    "git": _svg(
        '<circle cx="4.8" cy="3.4" r="1.5"/>'
        '<circle cx="4.8" cy="12.6" r="1.5"/>'
        '<circle cx="11.2" cy="6.2" r="1.5"/>'
        '<path d="M4.8 4.9v6.2"/>'
        '<path d="M11.2 7.7v0.3a3.2 3.2 0 0 1-3.2 3.2H4.8"/>'),
    # A single continuous 8-notch cog outline + axle hole — a gear *silhouette*,
    # never a center-disc-plus-detached-rays (which would collide with `sun`).
    "settings": _svg(
        '<path d="M7.30 3.45L6.50 1.88L9.50 1.88L8.70 3.45L10.72 4.29'
        'L11.27 2.61L13.39 4.73L11.71 5.28L12.55 7.30L14.12 6.50L14.12 9.50'
        'L12.55 8.70L11.71 10.72L13.39 11.27L11.27 13.39L10.72 11.71'
        'L8.70 12.55L9.50 14.12L6.50 14.12L7.30 12.55L5.28 11.71L4.73 13.39'
        'L2.61 11.27L4.29 10.72L3.45 8.70L1.88 9.50L1.88 6.50L3.45 7.30'
        'L4.29 5.28L2.61 4.73L4.73 2.61L5.28 4.29Z"/>'
        '<circle cx="8" cy="8" r="2.1"/>'),
    "theme": _svg('<path d="M13.4 9.1A5.5 5.5 0 1 1 6.9 2.6 4.4 4.4 0 0 0 13.4 9.1Z"/>'),
    "sun": _svg(
        '<circle cx="8" cy="8" r="3"/>'
        '<path d="M8 1.4v1.6M8 13v1.6M1.4 8h1.6M13 8h1.6'
        'M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"/>'),
    "update": _svg('<path d="M8 2.4v6.4M5.3 6.1 8 8.8l2.7-2.7M3.4 12.2h9.2"/>'),
    # upload: an up arrow rising out of a tray — the drop-zone / import affordance
    # (the mirror of `update`'s download arrow). Used by the picker's front drop zone.
    "upload": _svg('<path d="M8 9.6V2.9m0 0L5.6 5.3M8 2.9l2.4 2.4"/>'
                   '<path d="M3.5 10.4v1.7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1v-1.7"/>'),
    # ── action / empty-state glyphs ──────────────────────────────────────────
    "symbol": _svg(
        '<rect x="4.5" y="5" width="7" height="6" rx="1"/>'
        '<path d="M4.5 8H2.5M11.5 8h2M8 5V2.8"/>'),
    "footprint": _svg(
        '<rect x="2.6" y="5.5" width="3.2" height="5" rx="0.7"/>'
        '<rect x="10.2" y="5.5" width="3.2" height="5" rx="0.7"/>'
        '<path d="M5.8 8h4.4"/>'),
    "cube": _svg(
        '<path d="M8 2.2l5.2 2.8v6L8 13.8 2.8 11V5z"/>'
        '<path d="M2.9 5.1 8 7.9l5.1-2.8M8 7.9v5.9"/>'),
    "search": _svg('<circle cx="7" cy="7" r="3.8"/><path d="M9.9 9.9l3.4 3.4"/>'),
    # filter: three stacked lines narrowing downward — the finder's Show/Group-By pop.
    "filter": _svg('<path d="M2.5 4h11M4.5 8h7M6.5 12h3"/>'),
    "plus": _svg('<path d="M8 3.4v9.2M3.4 8h9.2"/>'),
    # expand: maximize corners — the file card's hover Expand → lightbox affordance.
    "expand": _svg('<path d="M9 3h4v4"/><path d="M13 3l-4.6 4.6"/>'
                   '<path d="M7 13H3V9"/><path d="M3 13l4.6-4.6"/>'),
    "check": _svg('<path d="M3.5 8.5l3 3 6-6.5"/>'),
    "alert": _svg(
        '<path d="M8 3 14 13H2z"/><path d="M8 6.8v3"/>'
        '<circle cx="8" cy="11.4" r="0.25" fill="currentColor" stroke="none"/>'),
}


def icon(name: str) -> str:
    """The SVG string for a named glyph (empty string if unknown, so a bad key is
    a missing icon, never a crash)."""
    return GLYPHS.get(name, "")
