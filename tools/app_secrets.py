"""Baked-in application secrets (SP1, decision #2).

This module is intentionally NOT gitignored and IS bundled into the frozen exe
so a fresh install has working sourcing with zero setup.

PUBLIC-REPO POLICY (the repo is public): NOTHING secret is committed here — every
value stays ``None`` in git. Only the free, non-repo-scoped Mouser Search key is
baked into the release exe by CI (from the ``MOUSER_API_KEY`` Actions secret); a
public binary exposing that shared, rate-limited key is acceptable.

- The **Mouser Search key** is baked at build time from the ``MOUSER_API_KEY`` Actions
  secret; ``None`` in git. The MOUSER_API_KEY env var overrides at runtime (dev).
- The **write-scoped tokens** (updater token, git PAT) are NEVER committed AND are no
  longer baked — a repo-access token must not ship in a public binary. Auto-update
  downloads token-less from public releases (nd_updater falls back to the public asset
  URL); an in-app git push uses a user-supplied GIT_PAT env var at runtime.
- The **DigiKey OAuth creds** are NEVER committed and NOT baked — a DigiKey account is
  per-user. They stay ``None`` in the shipped exe; each user supplies their own via the
  in-app Settings (gitignored config.json) or the DIGIKEY_CLIENT_ID/SECRET env vars.
"""

# Free, rate-limited Mouser Search API key. None in git (public repo); CI bakes it into
# the release exe from the MOUSER_API_KEY Actions secret. None/empty = no baked key ->
# sourcing relies on the MOUSER_API_KEY env var, which overrides this value when set.
MOUSER_API_KEY_DEFAULT = None

# Read-only GitHub token for the in-app auto-updater to reach this PRIVATE repo's
# releases. Left None in git; CI writes the `UPDATER_TOKEN` Actions secret here at
# build time so the token is baked into the exe but never committed. A GITHUB_TOKEN /
# GH_TOKEN environment variable overrides this at runtime. See tools/nd_updater.py.
GITHUB_TOKEN_DEFAULT = None

# DigiKey Product Information API v4 OAuth2 client-credentials (client_id + secret).
# DigiKey is the last-resort distributor in the sourcing chain (after Mouser + LCSC).
# These grant billed API access, so — unlike the free Mouser key — they are NEVER
# committed AND (unlike the write-scoped updater/git tokens) they are NOT baked by CI:
# a DigiKey account is per-user, so there is no shared build-time secret to bake. They
# stay None in the shipped exe. The USER supplies their own creds at runtime, resolved
# (highest priority first) as: the DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET env vars
# (silent dev override), else the DigiKeyClientId / DigiKeyClientSecret keys the in-app
# Settings writes into the gitignored config.json. Absent all of these these defaults
# stay None -> DigiKey is simply not registered (zero regression). See
# tools/LibraryManager.py resolve_digikey_creds / make_digikey_lookup.
DIGIKEY_CLIENT_ID_DEFAULT = None
DIGIKEY_CLIENT_SECRET_DEFAULT = None

# GitHub Personal Access Token for the in-app Git feature to push/pull over HTTPS
# (a fresh Windows clone has no SSH key or credential helper). Left None in git; CI
# bakes `secrets.GIT_PAT` at build time. A GIT_PAT / GITHUB_PAT environment variable
# overrides it at runtime. Used only for https:// remotes — ssh remotes ignore it
# and authenticate via the local SSH key. See tools/nd_git.py.
GIT_PAT_DEFAULT = None
