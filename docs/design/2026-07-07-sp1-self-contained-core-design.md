# SP1 — Self-Contained Core: Design

**Status:** implemented (2026-07-07) — one open item: paste the real Mouser key into
`tools/app_secrets.py` before a release build · **Date:** 2026-07-07
**App:** KiCad Library Manager (PyQt5 desktop, `git/Hardware`)
**Repo (canonical):** `~/git/Hardware` (WSL-native), branch `ui-clean-slate`

---

## 0. Where this sits

The user's full request ("fix the UI and make every feature seamless; build in the API
key and the database; bundle the parts library") is too large for one plan. It is
decomposed into three sub-projects, each with its own spec → plan → build cycle:

- **SP1 — Self-Contained Core (this doc).** Make the frozen `.exe` run with zero setup:
  bundled STM32 database, baked-in Mouser key, a seeded parts library, and a
  user-chosen writable location. Mostly plumbing + packaging; one small Settings UI change.
- **SP2 — Library rebuild + 3D viewer.** Redesign the Library page into a master–detail
  workspace and wire in the *already-existing* `fp_render` symbol/footprint/3D renderers.
- **SP3 — App-wide seamlessness pass.** Apply the design system across Bench, Projects,
  Settings, and surface the large body of stranded backend logic.

Two standing requirements apply to all three (recorded here, executed in SP2/SP3):
1. **Every UI change is validated by offscreen screenshot and critiqued** against
   `docs/design/design-rules.md` — because the user cannot run the app himself
   (per handoff `Brain/Wiki/Archive/Handoffs/2026-07-05-netdeck-ui-clean-slate.md`).
2. **The UI must expose the logic that exists.** A large audit of stranded backend
   features feeds SP2/SP3 (see §11).

This spec covers **SP1 only**.

---

## 1. Problem — the frozen app is not self-contained

The real build is `.github/workflows/build-exe.yml` ("Windows Release EXE"), which builds a
`--onefile --windowed` **"KiCad Manager.exe"** via PyInstaller CLI flags. It ignores the
stale `tools/KiCadLibraryManager.spec`. Current problems:

1. **No baked Mouser key.** Sourcing needs a key from `config.json` or `MOUSER_API_KEY`.
   A fresh install has neither, so Library → Sourcing is dead out of the box.
2. **The database is built on every fresh machine, in the wrong place.** The workflow
   bundles the 19 MB CubeMX XML (`--add-data tools/cubemx_db;cubemx_db`) and the app builds
   `stm32.sqlite` on first launch. `stm32_db.default_db_path()` (`tools/stm32_db.py:46`)
   writes it **next to the exe** when frozen — which fails in a read-only install dir
   (Program Files) and is slow on first run. The prebuilt DB is gitignored (`.gitignore`:
   `*.sqlite`, `tools/data/`) so it never ships.
3. **The parts library is not bundled.** The frozen exe ships with no `libs/`.
   `LibraryManager.detect_repo_root()` (`tools/LibraryManager.py:79`) uses the exe's own
   folder as the repo root **with no marker check**, so running the exe from `Downloads/`
   silently builds an empty library there.
4. **Onefile-hostile `__file__` writes.** Under `--onefile`, `__file__` resolves into the
   throwaway `_MEIPASS` extraction dir. Three code paths break:
   - `tools/ui/theme.py:205` `load_fonts()` globs fonts via `__file__` with **no
     `_MEIPASS` handling** → bundled fonts fail to load; UI falls back to system fonts.
   - `tools/nd_wizard.py:27` `LOG_DIR = Path(__file__).parent / "logs"` → rename
     preview/apply logs write into the ephemeral bundle.
   - `tools/nd_netclass_manager.py:655` `VAULT_STANDARD_PATH = Path(__file__)… /
     "vault_standard.json"` → "Save as Vault Standard" writes into the ephemeral bundle.
5. **LFS.** The 3D models under `libs/My3DModels/` are git-LFS (50 files). Any build that
   bundles them must `git lfs pull` first, or they bundle as pointer stubs.

## 2. Goal / non-goals

**Goal:** one portable `KiCad Manager.exe` that, on a clean Windows machine with no repo
and no prior config, launches, lets the user pick (or create) a library location once, and
immediately has: a working STM32 Bench (bundled DB), working Sourcing (baked key), and a
real parts library (seeded from the bundle), all git-syncable through the existing panel.

**Non-goals (SP1):**
- No UI redesign beyond the Settings changes in §8 (that is SP2/SP3).
- No changes to how any logic *works* — only where it reads/writes files when frozen.
- No new distributor integrations (Mouser only; DigiKey/LCSC deferred).
- No change to the git-sync engine (`nd_git.py`) — reused verbatim.

## 3. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Baked distributor key | **Mouser only** |
| 2 | Key delivery | **Committed module** `tools/app_secrets.py` (private repo; key-in-git tradeoff accepted) |
| 3 | Settings key field | **Removed entirely**; app uses the baked key (env var still overrides silently) |
| 4 | STM32 DB | **Prebuilt in CI, read-only from the bundle**; rebuild-from-XML becomes a dev-only script |
| 5 | Parts library | **Bundled snapshot, seeded to a per-user copy** the app manages |
| 6 | Writable location | **User-chosen; set once, changeable anytime in Settings** (not a fixed `%APPDATA%` path) |
| 7 | Git sync | **Reuse `nd_git.py` + the Projects Git panel unchanged**; the chosen location can be a git repo |
| 8 | Frozen-path bugs | **Fix the 3 `__file__` writes** (fonts, wizard logs, vault_standard) — plumbing, not logic |

---

## 4. Data locations — the core model

Three tiers. Only the pointer is at a fixed path; everything the app mutates lives at a
location the user chooses.

| Tier | Location (frozen) | Location (dev / from source) | Holds |
|---|---|---|---|
| **Bundle** (read-only) | `sys._MEIPASS` | the repo tree | `stm32.sqlite`, seed `libs/` + `catalog_assets/`, fonts, icons, the baked key module |
| **Pointer** (fixed, tiny) | `%APPDATA%\KiCadLibraryManager\workspace.json` | (unused in dev) | one field: the chosen library-location path |
| **Library location** (writable, user-chosen) | wherever the user picked | the repo root | `config.json`, `libs/`, `catalog_assets/`, `misc/`, `downloads/` — and optionally a git repo |

**Dev mode is unchanged.** When not frozen, `detect_repo_root()` still returns the repo
root and `config.json` still lives at `tools/config.json`. None of the pointer/seed
machinery runs from source, so development and the 361 tests are unaffected.

---

## 5. Path-resolution refactor

Add two helpers (in `LibraryManager.py`, the existing home of `detect_repo_root`) and route
all frozen-relevant paths through them. This replaces the scattered `__file__` /
`sys.executable` logic with two clear intents.

```
def bundle_path(rel: str) -> Path:
    """Read-only bundled asset. _MEIPASS when frozen, repo tree in dev."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
           else detect_repo_root()
    return base / rel

def library_location() -> Path:
    """Writable working dir. Pointer file when frozen, repo root in dev."""
    if not getattr(sys, "frozen", False):
        return detect_repo_root()
    return _read_pointer() or _prompt_or_default_location()
```

Rewire:
- `CONFIG_PATH` → `library_location() / "config.json"`.
- `derive_paths(root)` called with `root = library_location()` when frozen.
- `stm32_db.default_db_path()` (`tools/stm32_db.py:46`) → `bundle_path("data/stm32.sqlite")`
  when frozen, opened **read-only** (`sqlite3.connect(f"file:{p}?mode=ro", uri=True)` in
  `connect()`, `tools/stm32_db.py:393`; a writable fallback is unnecessary because the DB
  is prebuilt).
- `ui/theme.py:205 load_fonts()` → resolve the fonts dir via `bundle_path("fonts")`
  (mirrors the frozen-aware `resource_path` already in `ui_theme.py:170`).
- `nd_wizard.py:27 LOG_DIR` → `library_location() / "logs"` when frozen.
- `nd_netclass_manager.py:655 VAULT_STANDARD_PATH` → `library_location() /
  "vault_standard.json"` when frozen.

The last two are one-line redirects guarded by `sys.frozen`; they do not change how bulk
rename or net-class management *works*, only where their side files land when frozen.

---

## 6. First-run and location flow

`library_location()` when frozen:

1. **Pointer exists and its path is a writable dir** → return it. Straight into the app.
2. **No pointer (first launch)** → show a one-time modal **Choose Library Location**:
   - **Open Existing** — pick a folder that already holds a library (e.g. a git clone).
   - **Create New** — pick an empty folder; the app copies the bundled seed
     (`bundle_path("seed/libs")`, `bundle_path("seed/catalog_assets")`) into it, writes a
     fresh `config.json`, and offers an optional `git init` (via the existing
     `nd_git.set_repo` / `init_repo`).
   - Write `workspace.json` with the chosen path; launch.
3. **Pointer exists but the path is gone/unwritable** → same modal, pre-explained ("the
   previous location is unavailable").

Seeding is idempotent and marked: a `.seed_version` file in the location records the seeded
snapshot so re-seeding is deliberate. The seed copy is ~27 MB (mostly the LFS 3D models),
one time, fast.

---

## 7. Baked Mouser key

- New committed module `tools/app_secrets.py` exposing `MOUSER_API_KEY_DEFAULT = "…"`.
  It must **not** be gitignored and **must** be bundled (`--hidden-import app_secrets` or
  data). It is a small, free, rate-limited key on a private repo; the key-in-git tradeoff is
  accepted (decision #2).
- Resolution order becomes, at all three current sites
  (`LibraryManager.py:837`, `:1241`, `:1271`):
  `os.environ["MOUSER_API_KEY"]` → `MOUSER_API_KEY_DEFAULT`. The `config.json` key is
  dropped from the resolution chain and from Settings (§8). The env var stays as a silent
  dev override.
- `providers_from_config()` therefore never returns `None` for missing-key reasons, so
  Library → Sourcing and the "Enrich From Part Number" path are always live.

**Risk:** the key is extractable from the exe and lives in git history. Accepted for a
personal, free, replaceable Mouser key. Documented in §12.

---

## 8. Settings UI changes (the only SP1 UI surface)

In `tools/ui/features/settings.py`:

- **Remove** the entire Sourcing "Mouser API Key" card (`settings.py:108-152`) and its
  save/clear handlers. Replace with a single status line: *"Sourcing ready — built-in
  Mouser key."*
- **Add** a **Library Location** row: current path (mono) + **Change…** (Open Existing /
  Create-and-seed elsewhere, reusing the §6 flow) · **Open Folder** · **Reset to Bundled
  Snapshot** (destructive; confirm dialog; re-copies the seed).
- **Add** a small **Data** line: *"Database: bundled, N packages (read-only)."*
- Paths section becomes read-mostly (paths derive from the chosen location).

This surface is validated by an offscreen render (dark + light) and critiqued against
`design-rules.md` before it is called done — in particular checking it does not reintroduce
the `Verdict` tinted-surface or stadium-pill tells noted in the UI audit.

Git sync is untouched and stays in **Projects → Git**, now operating on the chosen location.

---

## 9. Packaging

**Consolidate onto a maintained spec.** Rewrite `tools/KiCadLibraryManager.spec` to be the
single source of truth (currently stale and ignored), and change the workflow's build step
to `pyinstaller tools/KiCadLibraryManager.spec` instead of the inline CLI. The spec keeps
`--onefile --windowed`, name **"KiCad Manager"**, and reproduces the current
hidden-imports + `collect-all` for cascadio/trimesh/numpy (so the 3D stack still bundles).

**Spec `datas` additions:**
- `data/stm32.sqlite → data/` (built in the CI step below; drop `cubemx_db` from the
  bundle — it's only needed to build the DB, which now happens pre-freeze).
- `libs/ → seed/libs/`
- `catalog_assets/ → seed/catalog_assets/`
- `app_secrets.py` bundled (module import).

**Workflow (`.github/workflows/build-exe.yml`) changes:**
- `actions/checkout@v4` with **`lfs: true`** (so 3D models bundle as real files).
- New step **before** PyInstaller: build the DB —
  `python -m stm32_db --build` (a thin dev/CI entry that calls `build_database`) writing
  `tools/data/stm32.sqlite` from `tools/cubemx_db/mcu`.
- Build via the spec; keep the existing tests-gate, artifact upload, and release steps.

## 10. Git sync — reused, unchanged

`nd_git.py` and the Projects Git panel are used verbatim (decision #7). SP1 only ensures the
panel's repo path is the chosen library location. Note for accuracy: `nd_git.py` has **no
push/pull** — the panel's push is a raw `subprocess` call in `projects.py:694`; that is
existing behavior and out of scope here.

---

## 11. Stranded-logic inventory (recorded for SP2/SP3, not built in SP1)

Captured now so it isn't re-discovered later. Not exhaustive; the SP2 audit expands it.

- **Rendering (SP2 core):** `fp_render` `render_symbol_image`, `render_footprint_image`,
  `render_step_image` (STEP + WRL, drag-rotate `paint_mesh`), `render_board_image`,
  `footprint_summary`, `step_summary` — the whole preview stack is bundled but uncalled.
- **Dead controls:** "Enrich From Part Number" button has no handler
  (`library.py:71`); net-class Profile selector has no `on_change` (`projects.py:378`).
- **Library:** `search_parts` (part search), `enrich_library`/`enrich_symbol`,
  `export_catalog`, group-override editor, `find_corrupt_kicad_files`.
- **STM32:** `resolve_part` per-part view is wired in Bench, but exporters
  `to_switchmap_c`, `to_kicad_symbol`, `authority_diff`, `lint_card`/claims have no UI.
- **Project tools:** the extended `nd_project_settings_manager` layer (DRC/ERC severities,
  the 12×12 ERC pin matrix, text variables, track/via/diff-pair tables) is entirely
  unreferenced by the UI.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Baked key extractable / in git history | Accepted; free, rate-limited, replaceable key on a private repo |
| PyInstaller misses cascadio's native OpenCASCADE libs → 3D silently dead | Keep `--collect-all cascadio/trimesh/numpy`; add a post-build smoke test that calls `fp_render.have_3d()` in the frozen exe |
| LFS pointers bundled instead of real models | `lfs: true` in checkout; smoke test opens one seeded `.STEP` and asserts a mesh loads |
| Exe grows (~80 MB → ~110 MB) with the seed library | Acceptable for a self-contained tool; drop the 19 MB XML from the bundle to offset |
| First-run seed copy latency (~27 MB) | One-time, on a location the user chose; shown with a progress message |
| Frozen-path redirects touch "perfect" modules | Redirects are `sys.frozen`-guarded one-liners; the 361 tests run from source (unfrozen) and stay green; add explicit frozen-path unit tests |
| Wrong-folder library (running exe loose) | The §6 explicit location chooser + pointer replaces the silent exe-folder assumption |

## 13. Testing

- **Unit (pytest, from source):** `bundle_path`/`library_location` under monkeypatched
  `sys.frozen` + `sys._MEIPASS`; pointer read/write; first-run seed into a `tmp_path`;
  key resolution (env vs baked default); `default_db_path` returns the bundle path + opens
  read-only when frozen. Keep the existing **361 tests green**.
- **Frozen smoke (CI, post-build):** launch the exe headless in a clean temp dir; assert
  the pointer flow, DB opens (N packages > 0), `have_3d()` is True, one seeded STEP loads,
  and Sourcing reports a live key.

## 14. File touch map

| File | Change |
|---|---|
| `tools/LibraryManager.py` | add `bundle_path`, `library_location`, pointer read/write, seed-on-first-run; reroute `CONFIG_PATH`/`derive_paths`; key resolution → baked default |
| `tools/app_secrets.py` | **new**, committed, bundled — `MOUSER_API_KEY_DEFAULT` |
| `tools/stm32_db.py` | `default_db_path` → `bundle_path` when frozen; `connect` read-only mode; `--build` CLI entry |
| `tools/ui/theme.py` | `load_fonts` via `bundle_path("fonts")` |
| `tools/nd_wizard.py` | `LOG_DIR` → `library_location()/logs` when frozen |
| `tools/nd_netclass_manager.py` | `VAULT_STANDARD_PATH` → `library_location()/vault_standard.json` when frozen |
| `tools/ui/features/settings.py` | remove key card; add Library Location + Data rows |
| `tools/KiCadLibraryManager.spec` | rewrite as source-of-truth; add DB + seed + secrets datas |
| `.github/workflows/build-exe.yml` | `lfs: true`; pre-build DB step; build via spec |
| `tests/` | new frozen-path / seed / key-resolution units |

## 15. Out of scope / deferred

- Library UI redesign + 3D viewer wiring → **SP2**.
- App-wide seamlessness + stranded-logic exposure → **SP3**.
- DigiKey/LCSC in-app integration.
- Bundling a git binary / making git-install-free (git stays a PATH dependency; `nd_git`
  unchanged).
- Cross-platform (the app is Windows-only: `kicad_paths.py` globs `C:\Program Files\KiCad`).
