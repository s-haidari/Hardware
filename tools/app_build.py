"""Build-time identity — the single source of truth for the app version.

CI (`.github/workflows/build-exe.yml`) overwrites `VERSION` with the release tag
(e.g. "v2.1.0") right before PyInstaller freezes the exe, so a released build knows
exactly which release it is. In a dev checkout it stays "dev", which the updater
treats as "never newer than a real release" so development never nags to update.

Keep this module import-light and dependency-free: it is imported very early and is
also baked into the frozen bundle (see the spec's hiddenimports).
"""
from __future__ import annotations

# Overwritten by CI at build time. "dev" in any source checkout.
VERSION = "dev"

# owner/name of the GitHub repo whose Releases the updater checks.
REPO = "s-haidari/Hardware"

# The release asset the updater downloads (matches build-exe.yml's uploaded file).
ASSET_NAME = "KiCad Manager.exe"
