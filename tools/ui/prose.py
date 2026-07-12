"""One source for user-facing UI copy conventions.

Two jobs, both about a consistent product *voice* (design-rules.md §2):

* ``plural(n, noun)`` renders a real count phrase — ``"1 error"`` / ``"2 errors"`` —
  so the ``"error(s)"`` tell never ships. This replaces the ad-hoc ``_plural``
  helpers that had drifted into ``library.py`` / ``nd_commit_msg.py`` copies.
* The shared **state headlines** below are the single spelling for empty / error
  states that recur across features, so the same state never reads two ways and a
  future localization has one place to translate. A genuinely one-off headline may
  stay inline, but it still obeys Title Case.

Everything here is Title Case for UI text (design-rules §2); an actual *sentence*
(a status line, a rationale) stays sentence case and does not belong in this file.
"""
from __future__ import annotations


def plural(n: int, noun: str, plural_form: str | None = None) -> str:
    """``"1 error"`` / ``"2 errors"`` — a count phrase with the noun agreeing in
    number, so the lazy ``"error(s)"`` pattern never reaches a rendered label.

    Naive English pluralization (append ``s``); pass ``plural_form`` for an
    irregular noun (``plural(n, "entry", "entries")``). ``n`` is coerced to int so a
    count that arrives as a float / string still agrees correctly.
    """
    n = int(n)
    if n == 1:
        return f"1 {noun}"
    return f"{n} {plural_form if plural_form is not None else noun + 's'}"


def count(n: int, noun: str, plural_form: str | None = None) -> str:
    """Alias reading naturally at a call site that is stating a quantity rather than
    an error/warning tally (``count(len(files), "file")``). Same semantics as
    :func:`plural`."""
    return plural(n, noun, plural_form)


# ── shared state headlines — Title Case, one spelling per recurring state ─────────
# Use these where the SAME empty/error state appears in more than one place so the
# copy can never drift apart; a truly one-off headline may stay inline (Title Case).
NO_PACKAGE = "No Package Loaded"          # a Bench sub-surface with nothing selected
COULD_NOT_LOAD = "Could Not Load"         # a region whose async compute() raised
