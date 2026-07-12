# North-Star UI — the finished app (one page, owner-approved direction)

**Status:** the definitive end-state every rebuild aims at. Consolidates every owner
request to date (ledger: feel + IA are first-class; zero feature omission; everything
ties together; strip-and-rebuild authority). Execution plan/phases live in
`docs/superpowers/specs/2026-07-09-ui-convergence-design.md` — this doc is the
*destination*, kept short so it stays true.

## The one-sentence product
A **KiCad workshop instrument**: one styled window where every object you care about —
a part, a project, a bench package, the repo, the machine — gets the same calm,
object-centric workbench: *pick the object → see its live verdict → act with one
accented button → everything refreshes in place.*

## The frame (already landed)
- ONE design system (`ui.theme` tokens; dark/light retint everywhere; 8/6 radii).
- Styled shell: nav Library → Projects → Bench → Git → Settings; persistent Activity
  console at the bottom; Ctrl+K search.
- The `kit.workbench` recipe (Git = proven pilot): selector → quiet-when-OK verdict
  band → in-place-refreshed detail → 0-or-1 accent ▶ orchestrated flow (preview →
  apply → structured report) → 2-col secondaries → collapsed Manage/Export.

## Per-panel end state
**Library** — the flagship. 3 tabs:
- *Parts*: 3-pane splitter (facets+search+list · part canvas with 3D MeshView ·
  PartDetail with inline edit). Drag-drop a datasheet/model/zip onto the part.
- *Sourcing Health*: workbench. Verdict = library health count; ▶ **Fix All From
  Library** (the proven fill engine); per-part findings in the detail card.
- *Maintenance*: workbench. Scan, dedupe, trash/restore, portable-library, drop-ins.
- Surfaces all 26 parity omissions or justifies each as internal.

**Projects** — object = the project (shared selector in the Workspace header). Tabs:
- *Overview*: the readiness verdict card (audit + ERC/DRC + git tree + next step) — A.
- *Health*: findings table, ▶ **Prepare This Project** (Fix-All + re-audit + before/
  after diff + Restore Last Prepare) — B.
- *BOM & Procurement*: ▶ **Build & Cost**, per-line orderability drill, blockers,
  procurement bundle, price breaks — C.
- *PCB Setup*: master-detail editor (`kit.editor`, the Phase-2 third shape).
- *Net Classes*: vault-standard load/save/sync, editable diff-pairs.

**Bench** — object = package/family (shared header selector → all tabs re-derive).
Tabs: Pins (PinMap SVG), Fabric/Mesh (MeshView), Wiring, Exports (collapsed). Quiet
verdict (BENCH-14: silence when buildable).

**Git** — DONE (the pilot): status verdict, Changes card, Auto-Pull, ▶ Commit and
Sync, full secondary/machinery parity, live watchdog.

**Settings** — one workbench. Verdict = machine setup status; ▶ **Set Up This
Machine** (guided audit→apply); live toggles (theme/units/auto-pull) act instantly
via the bus. KiCad paths, providers, library location, STM32 DB.

_(Routing was removed entirely — the feature, the Rust engine, and its docs — 2026-07-10.)_

## The polish bar ("best-dev-on-GitHub" checklist, gated per panel)
1. Every control has a tooltip; every label Title Case; static vocab in refresh paths.
2. Every surface has real empty/loading/error states — never a blank pane.
3. Nothing blocks the GUI thread; every mutating op busy-gates and reports honestly
   (structured done/missing/errors — never a silent failure).
4. Refresh never leaks (restyler-flat), rebuilds always deferred (segfault guard).
5. Both themes rendered and READ before "done"; drive-audit drives it like a user;
   parity = 0; suite green; **Windows exe verified on the real library** (release gate).
6. Feel: one accent per surface, calm neutral chrome, color only on the smallest
   element, motion subtle (the design-rules contract).

## Order (unchanged from the spec)
Library → Projects (+A/B/C) → Bench → Settings → Phase 3: flip default styled,
delete `bare.py`, Windows verify. Each panel lands fully gated before the next starts.
