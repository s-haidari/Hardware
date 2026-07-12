# Hardware — KiCad Manager (app development instructions)

PyQt5 desktop app: KiCad component-library + PCB-setup + STM32-bench manager.
Entry `python -m ui` (shell `ui.shell.NetdeckShell`). Design contract:
`docs/design/design-rules.md`.

## Session Ledger Protocol — MANDATORY (owner directive, 2026-07-08)

**Every session working on this app maintains the exhaustive ledger at**
`~/Documents/Obsidian/Brain/Agent/Hardware Perfection Log.md` — **literally everything,
every turn, handoff or not**:

- `ASK` every owner message + the intent · `DECISION` every call I make + why ·
  `IDEA` any idea (mine or theirs) · `ACTION` every change/commit/workflow/render/test ·
  `COMPROMISE` every shortcut/deferral with **why + when + what "done" means**.
- Append newest-at-bottom; keep the **Current State** header and **Open Compromises &
  Deferrals** table current. Never leave a half-done thing unrecorded.
- Commit + push **scoped** to the Obsidian repo (only the ledger / `Log.md`; never the
  repo-root `.obsidian/` churn). A `SessionStart` hook in `.claude/settings.local.json`
  reprints the reminder + Current State each session.
- This is the durable memory; transcript backups (raw) are separate (PreCompact hook).

## Idea Tracker — MANDATORY (owner directive, 2026-07-09)

**The moment the owner gives an idea, add it as a checkbox to**
`~/Documents/Obsidian/Brain/Agent/Hardware Ideas.md` — `- [ ]` open, `- [x]` done+verified,
each with a one-line state (commit / status / why-deferred). So no idea is ever lost. Tick
the box (and note the commit) when it ships. Commit + push **scoped** to the Obsidian repo.
This is the IDEA registry; the reasoning still goes in the ledger (`Hardware Perfection Log.md`).

## Current initiative — end-to-end perfection

Owner mandate (2026-07-08): revisit the WHOLE app with intent — end-to-end perfection of
every UI element/feature/logic; **fully working, no compromises, nothing half-assed**;
everything ties together with **no conflicts**; full authority to strip-and-rebuild.
Read the ledger's Current State for live status.

## Git

- **Scoped `git add <path>` only** — never `-A` / `commit -a`. **Never stage
  `libs/My3DModels/*.STEP`** (CRLF churn, not real changes).
- Plain commit messages — **NO `Co-Authored-By` / `Claude-Session` trailers**.
- **Commit messages: one sentence max, no body/description** (owner directive, 2026-07-12).
- Push without asking once committed + ready; never force-push.

## Verify loops

- Tests: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q` (baseline 914/3).
- Render gate (self-audit UI): `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/render_gate.py --out build/render --settle 0.9` — then **Read the PNGs** and check against `docs/design/design-rules.md`.
- Prefer editing serially per file / in isolation so parallel work never conflicts.

## No-fault gates — MANDATORY (owner directive, 2026-07-09)

These exist because the SAME three faults kept recurring: shipping UI regressions,
forgetting features that already exist in the code, and declaring "done" before it was.
Passing `pytest` is NOT enough — it exercises wiring, and stayed green while `python -m ui`
**segfaulted** (changing the Projects project) and showed **stale data** (choosing a Bench
package didn't refresh the table). Do NOT rely on memory or tests alone; run the gates.

### 1. DRIVE the UI — never verify by wiring/tests/panel-builds alone
- Before claiming ANY UI change works, run the drive-audit — it builds the app on a fixture
  and DRIVES it (changes every selector through all values, clicks actions, checks
  selection-dependent views actually refresh, catches crashes):
  `QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui/drive_audit.py` (exit 0 = clean).
  It also runs in the suite as `tests/test_drive_audit.py` (as a subprocess, so a segfault
  becomes a red test, not a dead pytest).
- When fixing a UI bug, ADD a case to `drive_audit.py` that reproduces it (regression lock).
- For a real check, also DRIVE by hand: construct `BareWindow(cfg)`, change the selector,
  CLICK, ASSERT the target/value — never "the panel builds" or "the handler is wired".

### 2. Never forget a feature that exists in the code
- Before building anything, assume the capability may ALREADY exist. Consult
  `docs/CAPABILITIES.md` (the judged map of every backend capability → where it's surfaced)
  and run `.venv/bin/python tools/ui/capability_audit.py` (flags public backend functions
  not referenced in the UI). Account for EACH flagged capability: surface it, or confirm
  it's an internal helper. Regenerate `docs/CAPABILITIES.md` when capabilities change.
- **"Barebones/minimal" = plain look, ZERO feature omission.** Every backend capability the
  app supports must be reachable in `bare.py`. Never ship a subset and call the rest a
  follow-on — that is the owner's #1 recurring complaint.

### 3. Honest completion — no premature or absolute claims
- NEVER say "0 regressions", "fully verified", "flawless", or "done" off tests alone. Say
  exactly what was exercised and WHERE: e.g. "drive-audit + suite green on Linux/offscreen
  with a fixture — NOT yet confirmed on Windows / the owner's real library." The owner runs
  **Windows** on their real library; a Linux/temp-config pass is necessary, not sufficient.

### 4. Qt widget-lifecycle footgun (caused the segfault)
- NEVER delete/replace a widget synchronously from inside its own — or a child's — signal
  handler (combo `currentIndexChanged`, button click). It is a use-after-free that crashes.
  `BareWindow._rebuild` defers the swap via `QTimer.singleShot(0, …)`; keep it deferred, and
  use the same pattern for any in-signal teardown.

## Windows CI is the release gate (unforgiving)

- File-reading tests MUST pass `encoding="utf-8"` (Windows cp1252 chokes on `→ · ×`).
- Never `str(Path)` for display → use `.as_posix()`.
- Release: no source version bump — CI stamps `app_build.VERSION` from a `vX.Y.Z` tag on a
  Windows-green commit; add notes with `gh release edit <tag> --notes-file`.

## Design contract

`docs/design/design-rules.md`. The 8/6-radius invariant is enforced against `theme.qss()`
text — never put a 3/4/5px radius in a `qss()` rule. Reusable chrome → object-name rule in
`theme.qss()`; genuinely bespoke painting → an allowlisted `<tab>_visuals.py`.

## Routing

Shelved behind a "Coming Soon" placeholder (`RoutingFeature.build()`, commit `a439d67`) —
the Rust `grid_router` isn't bundled in the Windows exe. `RoutingState` + helpers are kept,
so restoring the real workspace is a one-revert job.
