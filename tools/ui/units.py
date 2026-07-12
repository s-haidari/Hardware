"""App-wide length units (mm ⇄ mils) — one persisted preference.

Canonical storage is always millimetres; this module only decides how a length
is SHOWN. The current mode is process-global (like ui.theme's dark flag): the
shell seeds it from config.json ("Units") at launch, and re-broadcasts every
change on the "units.changed" bus topic so all live panels re-render. The
Settings control and the PCB Setup unit toggle both drive it via the
"units.set_mode" command topic (the shell owns the single write + broadcast).

Bus contract:
  units.set_mode  (command, arg: "mm"|"mils")  emitted by any unit control
  units.changed   (notice,  arg: "mm"|"mils")  emitted by the shell after it
                  updates this module + persists; consumers re-render from it.
"""
from __future__ import annotations

import math

from .util import mm_to_mils

MM = "mm"
MILS = "mils"

_mode = MM


def mode() -> str:
    """The current display unit — MM or MILS."""
    return _mode


def is_mils() -> bool:
    return _mode == MILS


def set_mode(m: str) -> str:
    """Store a normalised mode: MILS only for an explicit 'mils' (any case),
    else MM. Returns the stored value."""
    global _mode
    _mode = MILS if str(m or "").strip().lower() == MILS else MM
    return _mode


def to_display(mm: float) -> float:
    """Canonical mm -> the number shown in the current unit."""
    return mm_to_mils(mm) if _mode == MILS else float(mm)


def suffix() -> str:
    """The unit suffix, leading space included (' mm' / ' mils')."""
    return " mils" if _mode == MILS else " mm"


_MISSING = "—"


def _num(mm: float) -> float | None:
    """Coerce a length to a finite float, or None for missing/garbage input.

    Defends the formatters against None or non-finite values slipping in from a
    caller that passes dict values straight through — they degrade to a
    placeholder instead of raising."""
    try:
        v = float(mm)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _trim(value: float | None, decimals: int) -> str:
    """Round then drop trailing zeros (0.200 -> '0.2', 62.9921 -> '62.99').

    A None value (missing/non-finite length) degrades to the placeholder."""
    if value is None:
        return _MISSING
    return f"{round(value, decimals):g}"


def fmt(mm: float, *, mm_dec: int = 4, mils_dec: int = 2) -> str:
    """A single length in the current unit, e.g. '1.6 mm' / '62.99 mils'.

    Missing/non-finite input degrades to a placeholder rather than raising."""
    v = _num(mm)
    if _mode == MILS:
        return f"{_trim(None if v is None else mm_to_mils(v), mils_dec)} mils"
    return f"{_trim(v, mm_dec)} mm"


def fmt_dims(*mms: float, mm_dec: int = 3, mils_dec: int = 1) -> str:
    """Several lengths sharing one suffix, e.g. '6.5 × 5.2 mm'.

    Missing/non-finite lengths degrade to a placeholder rather than raising."""
    dec = mils_dec if _mode == MILS else mm_dec
    nums = (_num(x) for x in mms)
    conv = (
        (None if v is None else (mm_to_mils(v) if _mode == MILS else v))
        for v in nums
    )
    return " × ".join(_trim(v, dec) for v in conv) + suffix()
