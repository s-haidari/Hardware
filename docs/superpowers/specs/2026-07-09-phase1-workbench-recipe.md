# Phase 1 — the `kit.workbench` recipe (v2.1 contract)

**Status:** v2.1 — sub-surface-scoped; two red-team passes done. v1 (whole-panel replacer)
= needs-changes → v2 (sub-tab scope). v2 re-red-teamed (workflow `wf_53f30135`, 5 lenses +
judge) = **needs-changes, 4 blockers** → this v2.1 resolves them. **This doc is the
implementation contract.** Provenance (v1 + both red-teams) is the appendix.

Grounded firsthand against the real code (2026-07-09, HEAD `cf210f9`): `ui/kit.py`,
`ui/widgets.py` (`Verdict`/`Workspace`/`static_label`/`register_restyle`), `ui/feature.py`
(`Feature`/`Context`/`EventBus`), `ui/console.py`, `ui/util.py` (`run_populate`/
`clear_layout`/`_headless`), `ui/shell.py` (`Services`), the bare cadence in `ui/bare.py`
(`_git_panel`, `_report`, `_checkbox_preview`, `_export_action`), and the real panels
`ui/features/{git,library,library_preview,bench,projects,settings}.py`.

**What v2.1 changed vs v2** (all grounded, see appendix "red-team 2"): (B1) the ported
modals MUST `_headless()`-short-circuit — `run_populate` alone does NOT make the flow
CI-safe. (B2) `detail()` returns `(chrome, fill_fn)`; `refresh()` runs `fill_fn` only —
never re-invokes `detail()` (else it re-registers every restyler → SHELL-06 leak). (B3) the
parity harness = transitive module closure + per-scope alias resolution + bus→shell
allowlist + action-reachability tagging + explicit pairing dict (a single-module name-match
gives false all-clears AND false omissions). (B4) the accent primary is **optional (0-or-1)**,
the verdict band is **quiet-when-OK (may return None → hidden)**, and object selection has a
**Workspace-scoped shared tier** (not only per-tab) — the mandatory-everything v2 contradicted
three landed decisions (Library Parts has no primary; BENCH-14 suppresses the OK band; Bench's
selector drives `ws.rebuild_all` across 5 tabs).

---

## 0. One-line direction
**The recipe is a SUB-TAB builder, not a panel replacer.** `kit.workbench(...)` fills *one
sub-surface* (a `W.Workspace` panel). A feature is a `W.Workspace` whose panels are a mix of
`kit.workbench(...)` sub-tabs and `kit.custom(...)` bespoke editors. Feature shapes:
- **Git** = one-tab Workspace = one workbench (the pilot).
- **Settings** = one-tab Workspace = one workbench (`selector=None`, §8).
- **Library** = a **3-tab** Workspace (Parts / Sourcing Health / Maintenance). Parts is a
  primary-less interactive canvas (Phase-2 `kit.custom` panes editor); the other two are
  workbenches. (NOT one-tab — `library.py:598-603`.)
- **Bench / Projects** = multi-tab Workspaces; each heavy sub-editor is a workbench or a
  `kit.custom` editor, with a **Workspace-scoped shared selector** in the header (§2.C).

## 1. The proven cadence (from bare, top → bottom in the body)
selector → **colour verdict band (quiet-when-OK)** → **noun-first detail/canvas refreshed
IN PLACE** → **0-or-1 accent ▶ primary flow** (audit off-thread → `_checkbox_preview` [safe
pre-checked / risky amber-unchecked, "Apply Checked"] → apply [.bak] → RE-audit →
`_report`{summary,done,missing[{item,why,how_to_fix}],errors}) → 2-col secondary grid →
collapsible machinery (demoted, never dropped) → collapsible exports.

Discipline: selection snapshotted to a **plain dict on the GUI thread** (never read a widget
from a worker); refresh = **in-place fill** (never re-invoke the chrome builder); object-switch
rebuild is **deferred via `QTimer.singleShot(0)`** (segfault guard, per `bare._rebuild`).

---

## 2. The `kit.workbench` contract (v2.1)

```python
def workbench(
    ctx,                          # feature.Context (cfg / services / theme / bus)
    *,
    title: str,                   # sub-surface title (also the Workspace panel name)
    snapshot: Callable[[], dict], # capture current selection→plain dict, ON the GUI thread
    selector: Optional[Selector] = None,   # per-tab object selector (drives THIS tab; §2.C)
    verdict: Optional[Callable[[dict], Optional[VerdictState]]] = None,  # None result ⇒ band hidden
    detail:  Callable[[dict, RefreshHandle], Tuple[QWidget, Callable[[dict], None]]] = ...,
    primary: Optional[PrimaryFlow] = None, # 0-or-1 accent ▶ orchestrated action
    secondary: Sequence[Action] = (),      # 2-col grid; kind=='primary' is FORBIDDEN here
    machinery: Sequence[Action] = (),      # collapsed-by-default section (object mgmt)
    exports: Sequence[ExportAction] = (),  # collapsed-by-default exports
) -> QWidget:                     # the body; caller wraps in W.scroll_body + W.Workspace
```

**Snapshot** = the plain dict `snapshot()` captures on the GUI thread and every worker reads
(workers NEVER touch a widget). Git: `{"repo": Path}`; Bench: `{"package","family"}`.

**Enforcement — one accent primary:** `workbench()` asserts
`not any(a.kind == "primary" for a in secondary)` (raise `ValueError`) so the lone accent is
always the `primary` PrimaryFlow (or none). `PrimaryFlow` is a distinct dataclass, NOT an
`Action`, so `kit._action_bar`'s existing >1-primary guard doesn't see it — this explicit
assert is the enforcement path.

**Assembly** (body order, one `W.scroll_body`):
1. `Selector` row (skipped if `selector is None`).
2. `VerdictSlot` — persistent band, hidden when `verdict` is absent or returns None (§3).
3. `RefreshRegion` hosting `detail(snap, handle)` — the active noun-first slot (§4).
4. Primary flow row — the single accent ▶ (skipped if `primary is None`; browse tabs legal).
5. `secondary` — 2-col grid via **`kit.button_grid`** (new helper, §6) of default-kind buttons.
6. `CollapsibleSection("Manage", machinery)` — collapsed by default (§6).
7. `CollapsibleSection("Export", exports)` — collapsed by default (§6).

### 2.C Object selection — TWO tiers (resolves B4 / the Bench selector)
- **Per-tab `Selector`** (§2 param): change → `snapshot()` on GUI thread → `handle.rebuild()`
  (deferred) rebuilds ONLY this tab. For a tab whose object is local to it.
- **Workspace-scoped shared selector:** a multi-tab feature (Bench/Projects) mounts the OBJECT
  selector in `W.Workspace(header=…)` and wires its change to `ws.rebuild_all()` (mirrors
  `bench.py:781-792`, `projects.py:2554`) so ALL sibling sub-tabs re-derive. Each workbench
  tab reads the shared object through its own `snapshot()` (off a shared `state` object).
  §3's "verdict band is a body row, NOT `header=`" constrains the VERDICT BAND ONLY — a shared
  OBJECT selector may still live in the Workspace header. A tab-local **filter** (Bench family
  combo) that resets sibling state also drives `ws.rebuild_all()`, not `handle.rebuild()`.

---

## 3. `widgets.VerdictSlot` — persistent, quiet-when-OK, in-place (resolves v2-B3 + B4-verdict)

```python
class VerdictSlot(QFrame):
    """A persistent full-width status band, built ONCE. .set(state) mutates text+kind IN
    PLACE; .set(None) HIDES the band (quiet-when-OK, per BENCH-14). Exactly ONE restyler is
    registered (owned by self); it recolours the dot/chip-dots from the stored kind on a
    theme toggle. NEVER rebuilt per refresh — no register_restyle churn (the v1/SHELL-06 leak)."""
    def __init__(self, *, chip_slots: int = 3, parent=None): ...
    def set(self, state: Optional[VerdictState]) -> None:
        # None ⇒ self.setVisible(False); else setVisible(True), mutate title/sub .setText,
        # store kind, show/relabel the fixed chip pool, call self._style() ONCE (no re-register).
```

- Visually mirrors `widgets.Verdict` (neutral `card` surface, leading kind-dot, title+subtitle,
  right chips) but chips are a **fixed pool** of `chip_slots` built once and shown/relabelled.
- ONE `register_restyle(self._style, self)`; `.set()` mutates text then calls `self._style()`
  (cheap; registers nothing). Title/subtitle plain `QLabel`s coloured inside `_style`.
- **Quiet-when-OK:** `verdict(snap)` returning None hides the band — reconciles BENCH-14
  (`bench.py:223-231`, "the always-green Buildable banner was noise"). Git's verdict is
  always-present (repo is always interesting), so it returns a state every time.
- Mount: the band is the **first full-width row inside the scroll body**, above the card —
  NOT `W.Workspace(header=)`. (An OBJECT selector may still be in the header — §2.C.)

`VerdictState` (dataclass): `kind ∈ {ok,warn,err,info,mut}`, `title`, `subtitle=""`,
`chips: Sequence[Tuple[label,value,dotkind]] = ()`.

---

## 4. `widgets.RefreshRegion` + `RefreshHandle` — chrome-once, fill-in-place (resolves B2)

The detail slot is active (Library canvas facet-filters + drop-ins; Git card ticks on every
watchdog event). Split build-chrome from body-fill exactly as `features/git.py` proves.

```python
# detail(snapshot, handle) -> (chrome_widget, fill_fn)
#   chrome_widget: built ONCE with real W.* helpers (Cards/eyebrows) — restylers OK, build-once
#   fill_fn(snapshot): repopulates the pre-built Card BODIES using ONLY static_label/static_status
#                      (no register_restyle) — safe to call hundreds of times

class RefreshRegion(QWidget):
    """Hosts chrome_widget; drives fill_fn on refresh. refresh() calls fill_fn(snapshot())
    IN PLACE (no clear-and-rebuild of chrome, no new restylers). rebuild() re-invokes detail()
    for a NEW object selection, DEFERRED via QTimer.singleShot(0) (segfault guard). refresh()
    is re-entrancy-guarded: it no-ops while a primary flow is in flight (see §5)."""

@dataclass
class RefreshHandle:
    refresh: Callable[[], None]     # fill_fn(snapshot()) in place — cheap, high-frequency
    rebuild: Callable[[], None]     # deferred detail() re-invoke (object selection changed)
    snapshot: Callable[[], dict]    # re-read the current GUI-thread snapshot
```

- `refresh` NEVER re-invokes `detail()` (that would re-create/re-register every build-once
  `W.eyebrow/body/tag/dl/Card` restyler each tick — `register_restyle` only drops on
  `destroyed`, deferred past `deleteLater`, so a tick burst grows `_RESTYLERS` faster than it
  drains: the exact v1 SHELL-06 leak). It runs `fill_fn`, which touches only pre-built Card
  bodies with the static vocabulary — the proven `git.py:141-214` pattern.
- **Re-entrancy (resolves the threading-minor):** `refresh()` short-circuits while
  `primary`-flow `busy` is set (extend the `git.py` `_busy` gate, today only on click
  handlers, to the refresh path too); wrap the worker-adjacent `nd_git.status` read in
  try/except so a concurrent index read can't raise on the GUI thread.

---

## 5. `kit.PrimaryFlow` + `kit.run_primary_flow` — the orchestrated ▶ (headless-safe, resolves B1)

```python
@dataclass
class PrimaryFlow:
    label: str                     # "▶ Commit & Sync"
    audit: Callable[[dict], list]            # OFF-thread: ops [{key,label,detail,safe}]
    intro: Callable[[dict, list], str]       # preview dialog intro text
    apply: Callable[[dict, list], dict]      # OFF-thread: apply CHECKED keys → report dict
    tip: str = ""
    empty: str = "Nothing to do."            # audit → [] ⇒ report this, skip preview
```

`run_primary_flow(ctx, host, flow, snapshot, after=None, busy_gate=None)`:
1. set `busy_gate(True)`; `run_populate(ctx, lambda: flow.audit(snap), on_audit)` — OFF-thread.
2. `on_audit(ops, ok)` (GUI thread): empty ⇒ `kit._report(host, flow.label, {"summary": flow.empty})`;
   else `keys = kit._checkbox_preview(host, flow.label, flow.intro(snap, ops), ops)`. `None`⇒cancel.
3. `run_populate(ctx, lambda: flow.apply(snap, keys), on_done)` — apply OFF-thread (writes `.bak`).
4. `on_done(report, ok)` (GUI thread): `kit._report(host, flow.label, report)`; `busy_gate(False)`;
   `after()` if given (re-audit: `handle.refresh()` + verdict recompute in place).

**HEADLESS (resolves B1 — this is the crux for the pilot's own gates):**
`run_populate` marshals only the WORKER; it does NOT make the modals CI-safe. So the ports
MUST `_headless()`-short-circuit (mirroring `util.confirm` at `util.py:17` and
`projects.py:534-536`):
- `kit._checkbox_preview(...)` headless ⇒ return the **safe/pre-checked keys** WITHOUT `exec_()`.
- `kit._report(...)` headless ⇒ `ctx.services.log(summary)` and return WITHOUT `exec_()`.
Without this, `exec_()` spins a modal loop no user dismisses → `drive_audit`/`render_gate`/
`pytest` HANG under offscreen Qt (this is why `drive_audit.py:197-198` refuses to click the
bare primaries today). **Correction to §7:** the flow is NOT "CI-safe by construction" — the
worker is marshalled; the modal ports are made CI-safe by the `_headless()` guard.

- `kit._report` / `kit._checkbox_preview` are ports of `bare._report`/`bare._checkbox_preview`,
  parented to **`host.window()`** (the top-level window), NOT the rebuildable body widget — a
  deferred `handle.rebuild()`/`Workspace._select` (`widgets.py:936` removeWidget+deleteLater)
  must not delete a live modal's parent. Same structured-dict shape
  `{summary,done,missing[{item,why,how_to_fix}],errors}`. Modal ⇒ GUI-thread only.
- **Test seam:** expose the flow's apply step (a `root._run_primary(keys)` handle, like
  projects' `root._fill_dialog`) so `drive_audit` can drive audit→auto-approve→apply→report
  end-to-end headlessly and assert the report — the §10-step-7 Git gate depends on this.

---

## 6. Collapsible + grid + export + selector primitives

```python
def button_grid(actions: Sequence[Action], cols: int = 2) -> QWidget:
    """NEW. A 2-col grid of default-kind action buttons (the secondary atoms). Named here
    because §2.5 depends on it and v2 forgot to. Reuses W.btn; raises if any kind=='primary'."""

class CollapsibleSection(QWidget):
    """Header (chevron ▸/▾ mirroring console.py) + a body of Actions. Collapsed by default.
    Header styled by object name (#collapseHeader) — no per-tick restyler. Empty ⇒ hidden."""

@dataclass
class ExportAction:                # kit.export_action(label, produce, default_name, ...)
    label: str
    produce: Callable[[dict], str]         # OFF-thread text producer (BOM CSV, catalog md…)
    default_name: Callable[[dict], str] | str
    filt: str = "All Files (*)"
    tip: str = ""
    # → QFileDialog.getSaveFileName (GUI thread) then write produce() off-thread via
    #   run_populate; kit._report confirms the path. Port of bare._export_action.

class Selector(QWidget):           # kit.Selector — labeled combo/Segmented (§2.C tiering)
```
`kit.asset_row(...)` (Library asset badges) is Phase-2; not on the Git critical path.

---

## 7. The ONE thread-marshal rule (stated once, used everywhere)

> **Workers never touch widgets.** All heavy work (`verdict`, `audit`, `apply`, `produce`)
> runs inside `run_populate(ctx, job, populate)`. `populate(result, ok)` is the **single**
> point results reach the GUI thread, and it updates the surface ONLY through in-place setters
> — `VerdictSlot.set(...)`, `RefreshHandle.refresh()`. Modal UI (`kit._report`,
> `kit._checkbox_preview`, `QFileDialog`) is GUI-thread-only, invoked from `populate` / a click
> handler, never from `job`, and is **`_headless()`-guarded** (§5) so an offscreen run never
> blocks. `run_populate` marshals the worker (with a synchronous headless branch); it does NOT
> by itself make a modal CI-safe — the `_headless()` guard on the modal does.

---

## 8. Settings shape (`selector=None`)
`kit.workbench(selector=None, verdict=setup-status, primary=Set-Up-This-Machine)`:
- **verdict** = machine setup status (KiCad paths found? library location set? providers
  configured?) → `ok` when set up, `warn`+gap list otherwise. May return None if fully set up
  and quiet is preferred, but Settings likely always shows a state.
- **primary ▶ "Set Up This Machine"** = the guided audit→preview→apply setup flow.
- **live toggles** (theme/units/auto-pull) are **secondary** controls acting via the bus
  IMMEDIATELY (`ctx.bus.emit("theme.set_mode", …)`), NOT the refresh model — they already work
  that way in `features/settings.py`; keep them. (Their backend reach lives in `shell.py` via
  the bus — see §9 bus allowlist so parity doesn't false-flag them.)

---

## 9. Parity harness — precise (resolves B3; extend `tools/ui/capability_audit.py`)
A styled-vs-bare **omission report per feature id**. The v2 "single-module name-match" gave
both false all-clears and false omissions; v2.1 pins the method:

1. **Explicit pairing dict** (fail loud on any unpaired panel/feature):
   `{"_git_panel":"git","_lib_panel":"library","_proj_panel":"projects","_bench_panel":"bench","_settings_panel":"settings"}`.
   `routing` is exempt (shelved, no bare source). Assert every bare `*_panel` and every
   non-shelved styled feature id is covered exactly once.
2. **Resolve import aliases per closure scope** (AST `asname`): bare does `import nd_git as G`
   in `_git_panel` (`bare.py:3871`), `as NG` / `as GIT` elsewhere — map each back to `nd_git`
   before matching attribute access. A literal `"nd_git."` match finds ZERO calls in the Git
   panel (false all-clear). Match resolved-binding attribute access per closure.
3. **Bare side = user capabilities only:** tag each bare backend call site by whether it is
   transitively reachable from a widget-action callback (`_btn`/`_btn_raw`/`_input_action`/
   `_export_action` lambda). Symbols reached ONLY inside a worker/status helper are NOT user
   capabilities — e.g. `have_git` (`bare.py:3990`, a QLabel string), `guard_no_corrupt_kicad`
   (`bare.py:4054`, inside `apply_work`). Exclude them (or a small explicit internal-guard
   allowlist).
4. **Styled side = TRANSITIVE module closure**, unioned: `features/<id>.py` PLUS every sibling
   it imports (`from . import library_preview`, `mouser_search`, `bench_visuals`,
   `projects_visuals`). Styled Library reaches `LM.search_parts`/`extract_symbol_blocks`/
   `ensure_footprint_model` through those siblings (`library_preview.py:48,115`;
   `mouser_search.py:158,199`) — a single-module walk falsely flags them.
5. **Bus→shell allowlist:** a `ctx.bus.emit("<cmd>")` whose `<cmd>` is handled in `shell.py`
   (the Services layer, e.g. `app.check_updates` → `nd_updater.*` at `shell.py:515-568`) counts
   as SURFACED. Maintain an explicit `{bus_cmd → capabilities}` allowlist.
6. **Report** = per feature: {bare user-capability symbols} − {styled closure symbols ∪ bus
   allowlist}. A migration drives this to **0**. Authoritative human map: `docs/CAPABILITIES.md`.

**Scope boundary (important):** the symbol harness measures BACKEND-CALL coverage, NOT UX-flow
parity. The ▶ Commit&Sync preview flow, checkbox-apply, and structured `_report` reuse symbols
(`commit`/`push`/`pull_ff_only`) styled already calls — a set-delta CANNOT flag them. **Flow/UX
parity is gated by the `drive_audit` case (§10 step 7), not by `capability_audit`.**

**Corrected Git symbol delta** (firsthand, `bare _git_panel` vs `features/git.py` + closure):
`{find_corrupt_kicad_files, init_repo, recent_commits, set_repo, show, stage, unstage}` are the
real not-yet-surfaced user capabilities (`stage_all` is ALREADY called at `git.py:287`;
`have_git`/`guard_no_corrupt_kicad` are internal guards, excluded per step 3). The preview flow
+ Sync-with-remote + the 4 structured reports are FLOW gaps for `drive_audit`, not symbol gaps.

---

## 10. Build order (each step INDEPENDENTLY gated: unit test + suite + render Read)
1. `widgets.VerdictSlot` (+ `VerdictState`) — persistent band, `.set()`/`.set(None)`-hides,
   ONE restyler. Test: `.set()` N× keeps `len(widgets._RESTYLERS)` flat; `.set(None)` hides;
   retints on toggle. Render Read both themes.
2. `widgets.RefreshRegion` + `RefreshHandle` — `detail→(chrome,fill_fn)`; refresh runs
   `fill_fn`, rebuild deferred. Test: `refresh()` N× keeps `_RESTYLERS` flat (fails if wired to
   `detail()`); rebuild deferred; refresh no-ops while busy.
3. `kit._report` / `kit._checkbox_preview` / `kit.run_primary_flow` — ports, **`_headless()`-
   guarded**, `host.window()`-parented, with the `root._run_primary` test seam. Test headlessly:
   report dict→text; headless `_checkbox_preview` returns safe keys w/o exec_(); a full
   audit→apply→report drive with no hang.
4. `kit.button_grid` (2-col) + `widgets.CollapsibleSection` + `kit.export_action`/`ExportAction`
   + `kit.Selector`. Test: empty section hides; grid rejects a primary; export writes produce().
5. Assemble `kit.workbench(...)`. Test: body order; the `secondary` no-primary assert; quiet
   verdict hides; per-tab selector→rebuild; 0-primary browse tab is legal.
6. Extend `capability_audit.py` → the §9 per-feature parity report (pairing dict, alias
   resolution, action-reachability, transitive closure, bus allowlist). Test on Git = the
   corrected delta.
7. **Pilot-migrate Git** through the recipe, end-to-end gated: capability parity = 0 omissions
   + a `drive_audit` case that DRIVES the styled Git primary headlessly (audit→auto-approve→
   apply→report via the test seam, asserting the report) + render Read + suite green + commit.

Then Phase 2 (Library → Projects+A/B/C → Bench → Git-done → Settings), Phase 3 (flip default in
`__main__.py`, delete `bare.py`, update run.sh/run.bat, Windows verify).

### Phase-2 forward-notes (deferred, do NOT solve now — flagged so they're not forgotten)
- **PCB Setup** (`projects.py:1707`) is a master-detail editable-grid editor (Save primary +
  Validate verdict + row-CRUD + profile-CRUD + live spins). Needs a THIRD shape — a
  `kit.editor` (or documented `kit.custom`-with-chrome) — designed at Phase 2, NOT now.
- **BOM / Health** (`projects.py`) tabs carry MULTIPLE orchestrated flows (Build+Export-menu+
  Compare; Audit+Fix-All+ERC+DRC). Either raise the recipe to `flows: Sequence[PrimaryFlow]`
  (one accented) or route them as `kit.custom` — decide at Phase 2.
- **Library Parts** is a `kit.panes` 3-way splitter (persisted widths, `_FacetBar`, drag-drop,
  inline PartDetail edit) → a full-bleed `kit.custom` tab, NOT a workbench detail slot (a
  `scroll_body` around a QSplitter defeats drag-resize).

## 11. Gates (repo CLAUDE.md ## No-fault gates)
- Suite `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q` (baseline **1284/4**).
- `tools/ui/drive_audit.py` exit 0 (extend to DRIVE the styled Git primary via the §5 test seam).
- `tools/ui/capability_audit.py` → 0 omissions for the migrated panel.
- `tools/ui/render_gate.py … --settle 0.9` → **Read the PNGs** vs design-rules §5.
- Honest completion: state what was DRIVEN + on which platform (Linux/offscreen ≠ Windows).

---

# Appendix — provenance (do NOT implement v1)

## The proven bare pattern (map source, verbatim)
selector → always-present colour verdict/gap header → noun-first detail card refreshed IN
PLACE via a per-panel `_CardBus(QObject){changed=pyqtSignal}` → 1–2 accent ▶ orchestrated
primaries (audit off-thread → `_checkbox_preview` → apply [.bak] → RE-audit → `_report`) →
2-col secondary atoms → collapsible machinery → collapsible exports.

## v1 contract (SUPERSEDED — whole-panel scope was the flaw)
New pieces: `kit.workbench`, `kit.Selector`, `kit.VerdictState`, `kit.PrimaryFlow`,
`kit.RefreshHandle`, `kit.run_primary_flow`, `kit._checkbox_preview`, `kit._report`,
`kit.export_action`, `kit.asset_row`, `widgets.VerdictSlot`, `widgets.RefreshRegion`,
`widgets.CollapsibleSection`.

## Red-team 1 (v1 = needs-changes) → resolved by v2 sub-tab scope
1. Multi-verdict/multi-editor panels; 2. sub-tabs dissolved; 3. VerdictSlot churns registry;
4. verdict mount vs Workspace header; 5. panel-scoped controls + cross-nav homeless;
6. Settings no shape; 7. Library canvas interactive; 8. thread-marshal described twice;
9. one-primary vs Projects per-tab.

## Red-team 2 (v2 = needs-changes, workflow `wf_53f30135`) → resolved by v2.1 above
- **B1 [blocker] §5/§7** — ported `_report`/`_checkbox_preview` `exec_()` unguarded → offscreen
  HANG; the Git pilot's own gates unsatisfiable. → §5 `_headless()` short-circuit + test seam;
  §7 corrected. (`bare.py:308,361`; `drive_audit.py:197-198`; `projects.py:534-536`.)
- **B2 [blocker] §2/§4** — `detail()->QWidget` with no fill hook forces `refresh()` to re-invoke
  `detail()` wholesale → re-registers every restyler each tick → SHELL-06 leak. → §4
  `detail→(chrome,fill_fn)`, refresh runs `fill_fn` only. (`git.py:141-214`; `widgets.py:70-75`.)
- **B3 [blocker] §9** — single-module name-match harness: false all-clear (alias `nd_git as G`),
  false omissions (sibling-module + bus reach), wrong worked example (`stage_all` already
  called). → §9 transitive closure + per-scope alias resolution + bus allowlist + action-
  reachability + pairing dict + corrected delta. (`git.py:287`; `library_preview.py:48,115`;
  `shell.py:515-568`; `bare.py:3871`.)
- **B4 [blocker] §0/§1/§2/§3** — mandatory primary + always-present band + per-tab selector
  contradict landed decisions. → primary optional (0-or-1); verdict quiet-when-OK (None hides);
  Workspace-scoped shared-selector tier (§2.C); Library is 3-tab. (`library.py:224-228,598-603`;
  `bench.py:223-231,781-792`.)
- **Re-entrancy edit §4/§5** — watchdog refresh + host-parented modal unguarded vs in-flight
  primary. → refresh `_busy`-gated; status read try/except; modals `host.window()`-parented.
- **Deferred to Phase 2** (dropped as not-Phase-1-blocking): PCB Setup third shape; BOM/Health
  multi-flow; Library Parts `kit.custom` splitter — captured in §10 forward-notes.
