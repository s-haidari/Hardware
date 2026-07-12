# Wave 1 · WS-E — Git unify + semantic messages + sync (design)

**Status:** designed 2026-07-08 (WS-A shipped `089e4a1`); WS-E not yet started.
**Scope:** `GIT-03` (unify backends) → `GIT-01` (semantic commit messages) → `GIT-02`
(app-level auto-pull) → `LIB-13` (ff pull-before-push). Decisions locked (plan §4):
**unify onto `nd_git` + conventional-commit messages.**

> Distilled from two code-grounded scout passes over both git layers. This doc is the
> durable design so the next session doesn't re-scout.

---

## The two backends today

### `nd_git.py` — the clean target (keep + extend)
Only consumer: `tools/ui/features/git.py` (Git tab, order=40). Traits:
- **Structured results:** `GitResult(ok, code, out, err)`, `RepoValidation`.
- **PAT auth (the thing LM lacks):** `_pat()` (env `GIT_PAT`/`GITHUB_PAT` > baked
  `app_secrets.GIT_PAT_DEFAULT`), `_remote_url(repo)`, `_auth_config()` — injects a
  base64 `AUTHORIZATION: basic x-access-token:<pat>` header **only** for `https://`
  remotes (ssh/file untouched). Rides in argv; never written to disk.
- **Corruption-guarded commit:** `commit(repo, message, paths=None) -> (bool, sha|err)`
  runs `guard_no_corrupt_kicad` (scans **staged** content via `git show :<path>`),
  refuses empty message / nothing-staged / corrupt KiCad. Never raises.
- `push(repo)` (PAT), `pull_ff_only(repo)` (PAT, never merges/rewrites), `stage`,
  `unstage`, `status()`, `ahead_behind()`, `current_branch()`, `init_repo`, `set_repo`.
- Subprocess helper `_run_git`: `stdin=DEVNULL` (no credential-prompt hang),
  `CREATE_NO_WINDOW`, timeouts (`DEFAULT_TIMEOUT=30`, `NETWORK_TIMEOUT=120`), utf-8 safe.
- **Background sync lives in the Git-tab UI, not nd_git:** `git.py:252-275` — a 3-min
  `QTimer` (`_AUTO_PULL_MS=180_000`) + `QFileSystemWatcher` (500 ms debounce, skips
  `.git`, cap 512 dirs), both **headless-skipped**. Opt-in via a checkbox. So auto-pull
  only runs **while the Git tab is open** — GIT-02 wants it app-level.

### `LibraryManager.py` — the old layer to retire (behind stable signatures)
Functions (all `(cfg, log, ...)`, write each git line to a `UILog`, no auth, no timeout):
- `run_git(args, cfg, log)` — `git -C RepoRoot …` subprocess (no auth/timeout).
- `git_has_staged_changes(cfg)`, `git_stage_commit(cfg, log, message=None)` (does
  `git add -A`, pre-scans work tree via `find_corrupt_kicad_files`, commits),
  `git_push(cfg, log)` **(NO PAT)**, `git_pull(cfg, log)` (`pull --ff-only`, no PAT),
  `git_commit_push(cfg, log, message)`, `commit_and_push` (interactive QInputDialog).
- Duplicated corruption scanners: `find_corrupt_kicad_files` / `has_conflict_markers`
  / `is_paren_balanced` (identical logic to nd_git's).

**9 call sites (must keep working, ideally unchanged signatures):**
- `LibraryManager.py`: `git_commit_push` internal (~1066); `process_zip` (~2174,
  `"Auto-update: processed <zip>"`); `process_existing_zips` (~2200); `process_folder_dialog`
  (~2214); `commit_and_push` (~2394).
- `ui/features/library_preview.py`: `_edit_property` (~510 `"Library: set <label> on <ident>"`),
  `_dropin_footprint` (~537 `"Library: drop in footprint <stem>"`), `_dropin_model`
  (~555 `"Library: drop in 3D model <name>"`), `_dropin_symbol` (~565 `"Library: drop in symbol <file>"`).

---

## GIT-03 — unify (do first; pure refactor, TDD)

**Approach:** reimplement LM's git wrappers to **delegate to `nd_git`**, keeping their
`(cfg, log, …) -> bool` signatures so no call site changes. The wrappers only translate
`repo = cfg["RepoRoot"]`, call nd_git, and mirror the result into the `UILog` (the one
thing nd_git lacks). This fixes the **PAT-auth bug on every Library drop-in push** and
collapses to one corruption guard.

Sketch:
```python
def git_stage_commit(cfg, log, message=None):
    repo = cfg["RepoRoot"]
    bad = nd_git.find_corrupt_kicad_files(repo)          # one scanner (nd_git's)
    if bad: log(...); return False
    st = nd_git.stage(repo, ".")                          # == add -A semantics
    if not st.ok: log(st.message); return False
    ok, info = nd_git.commit(repo, message or _default_msg())  # guards + "nothing to commit"
    log(info); return ok

def git_push(cfg, log):        # THE fix: PAT auth
    r = nd_git.push(cfg["RepoRoot"]); log(r.message); return r.ok
def git_pull(cfg, log):
    r = nd_git.pull_ff_only(cfg["RepoRoot"]); log(r.message); return r.ok
def git_commit_push(cfg, log, message):
    return git_stage_commit(cfg, log, message) and git_push(cfg, log)
```
Then delete LM's duplicated corruption scanners (re-export from nd_git if anything imports
them). Keep `commit_and_push`'s QInputDialog, delegate its body.

**TDD:** tmp-repo tests — (a) `git_stage_commit` refuses a corrupt `.kicad_sym` (no commit),
(b) commits a clean change and returns True, (c) `git_push`/`git_pull` build the PAT header
for an https remote and skip it for ssh/file (assert on `_auth_config`), (d) each of the 9
call sites still commits with its message (drop-in paths especially). Preserve the UILog
line-logging (existing tests may assert on logged lines).

**Risk:** touches the live auto-commit drop-in path. Verify offscreen + a real tmp-repo
push against a `file://` bare remote (no network) so the auth branch is exercised.

## GIT-01 — semantic commit messages (after GIT-03)

Locked: **conventional commits naming component + changed fields.** Add a small pure
message-builder (new `nd_commit_msg.py` or a fn in nd_git) mapping a structured change to
a message, e.g. `feat(lib): add footprint <stem> to <symbol>`,
`chore(lib): set <field> on <part>`, `feat(lib): import <N> parts (<names>)`. Wire it into
the 4 drop-in sites and the import paths. **Use the `finalize_import` change-set that is
currently discarded:** `finalize_import(...) -> {"linked": {...}, "enriched": {...}}`
returned at `LibraryManager.py:~2147`, discarded at `~2168` (`process_zip`) and `~2194`
(`process_existing_zips`) — thread it into the import commit message (which parts linked /
enriched, which fields written). Pure-function tests on the builder.

## GIT-02 / LIB-13 — sync (after GIT-01)

- **GIT-02:** lift the Git-tab auto-pull to an **app-level** background service (own the
  `QTimer`/watcher in the shell or a service, persisted on/off toggle via
  `LM.read_setting`/`write_setting`, headless-skipped) so it runs regardless of the open
  tab. Reuse `nd_git.pull_ff_only`.
- **LIB-13:** **ff pull-before-push** in the drop-in/import path so a multi-user drop-in
  never rejects: `pull_ff_only` then `push` (guard: if pull can't ff, surface it, don't
  clobber). Fold into `git_commit_push`.

---

## Gotchas
- Repo conventions: commits **direct to `main`**, **plain messages, NO trailers**; standing
  push authorization. **Never** stage the 5 modified `libs/My3DModels/*.STEP` — scoped adds.
- Tests: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` (offscreen Qt, session
  QApplication in `tests/conftest.py`, `tools/` on `sys.path`).
- Existing git tests: `tests/test_backend_git.py` (nd_git), `tests/test_sp5_git.py` (Git
  feature) — keep green; add a `tests/test_wave1_git_unify.py`.
- `app_secrets.GIT_PAT_DEFAULT` is None in a source checkout (baked in CI) — so the PAT
  branch is inert locally; test `_auth_config` directly with a fake PAT + https url.
