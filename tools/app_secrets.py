"""Baked-in application secrets (SP1, decision #2).

This module is intentionally NOT gitignored and IS bundled into the frozen exe
so a fresh install has a working Mouser key with zero setup. The key is small,
free, rate-limited, and replaceable; the key-in-git tradeoff is accepted for a
private repo (see docs/design/2026-07-07-sp1-self-contained-core-design.md §7).

To activate sourcing in a build, paste the free Mouser Search API key below.
The MOUSER_API_KEY environment variable overrides this silently (dev override).
"""

# Free, rate-limited Mouser Search API key. Empty = no baked key (sourcing then
# relies solely on the MOUSER_API_KEY env var). Fill for self-contained builds.
MOUSER_API_KEY_DEFAULT = "494cc3c4-3e7f-4438-a711-8fc07fa4bc76"
