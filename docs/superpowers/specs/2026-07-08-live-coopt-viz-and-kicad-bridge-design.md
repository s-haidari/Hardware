# Live Co-Opt Visualization + KiCad Bridge — Combined Design

**Date:** 2026-07-08
**Status:** Proposed design (brainstorming-converged; pre writing-plans)
**Parents:**
- `docs/superpowers/specs/2026-07-08-place-route-coopt-architecture.md` (the co-opt loop this reshapes)
- `docs/superpowers/research/2026-07-08-router-competitive-and-integration/` (WF1/WF2/WF3 + master report)

**Companion report:** `.../router-strategy-master-report.html` — the honest competitive picture and the 8-upgrade outperform plan. This spec builds the *product surface* that plan implies.

---

## 0. Goal & scope

Two user-requested features, unified under one architecture:

1. **Live routing visualization** — *watch the router work.* A board-preview canvas in the Routing workspace showing the A* wavefront and tracks landing, plus the net-level place↔route co-opt loop as it runs. Theme-aware, NETDECK-native.
2. **Stop-at-first-failure, fix one-by-one** — reshape the co-opt loop from *batch-route-then-diagnose* into *route → stop at the first blocker → attribute WHY → fix (recovery ladder, then reposition) → resume.*

**The one-sentence architecture:** one `QPainter` board-preview **canvas** in the Routing workspace, fed through a **single in-process event protocol (EventBus)** by two interchangeable sources — the existing single-ended stepped visual path and the negotiated production loop — and, in P3, **mirrored to a live KiCad board** through a thin resident plugin. The canvas and the live-KiCad sink are **two consumers of the same event stream**, so the protocol is built once.

**Why this framing (from WF2, verify-corrected):** our genuine competitive edge is **explained failure + place↔route co-opt**, *not* visualization per se — KiCad's PNS is best-in-class live and Freerouting animates its passes. So the viz is not "prettier animation"; it is the **window into the co-opt loop and the blocker attribution** that no competitor ships. The fail-fast reshape is what makes that window show something legible: one blocker at a time, with a reason.

**In scope:** the Routing-workspace canvas + its event protocol; the fail-fast reshape of `place_route_loop.py`; the honest-metric + infeasibility reporting discipline; the KiCad Bridge (P3) as a second sink.

**Explicitly out of scope:** the 8 engine-internal upgrades (multiplicative PathFinder, pattern routing, RUDY, BGA escape — those are the *outperform* track in the report, tracked separately); schematic editing; the analog/EMC placement problem; any second *writer* to the canvas (the canvas is strictly read-only — see §3).

**Honest baseline this rides on (WF2, `baseline.json`):** mean completion **40.8%** (per-board 20.0 / 29.8 / 57.8 / 60.0 / 76.9%, synthetic BGA **0%**), **56 DRC violations**, `connectivity_ok` **FALSE on 5/6 boards**. The viz must be honest about this: it visualizes a router that does not yet finish boards. It shows *why*, which is the point — not a victory lap.

---

## 1. The shared spine — one event protocol, two sources, two sinks

The engine **already emits** everything the canvas needs. Nothing new is required in the router core for P1/P2; the work is subscribing to what's there and defining a stable vocabulary so P3's live-KiCad sink reuses it.

### 1.1 The two event surfaces that already exist

**(a) Per-net lifecycle + negotiation progress (the production view).**
- `batch_route(..., vis_callback=None, progress_callback=None, progress_cb=None, cancel_check=None, ...)` — `route.py:140`.
- `progress_callback(current: int, total: int, net_name: str)` — coarse checkpoints (obstacle build, routing start, complete), `route.py:217/262`.
- `progress_cb(NegotiationProgress)` — per negotiation iteration, `route.py:225/271`.
- `NegotiationProgress(iteration, nets_total, nets_legal, contested_cells, phase='negotiate')` — `negotiated_loop.py:34-39`, emitted `negotiated_loop.py:229`.
- `vis_callback.on_net_start(...)` / `on_net_complete(net_name, success, path, iterations, direction)` — the per-net hooks, `single_ended_loop.py:338/355`.

**(b) Per-A*-step frontier (the "watch it think" view).**
- `route_net_with_visualization(pcb_data, net_id, config, obstacles, vis_callback) -> dict` — `single_ended_routing.py:1437`.
- `vis_callback.should_pause() -> bool` (`:1546/1580`), `on_route_step(snapshot) -> bool` (return False to quit, `:1549/1557/1582/1588`), `get_iterations_per_step() -> int` (`:1553/1585`).
- `SearchSnapshot` (from the Rust `VisualRouter`): `path`, `found: bool`, `iteration: int`, `open_cells`, `closed_cells` (the A* frontier).

**Honesty note baked into the UI:** surface (b) is the **single-ended, stepped, slower** path — it is *not* the production negotiated engine. The canvas presents (a) as the **primary "production" view** and (b) as an **optional "watch it think" toggle**, explicitly labeled as a slower single-ended demo. Never let the wavefront animation imply it is how the board was actually routed.

### 1.2 The `VisualizationCallback` protocol (the subscription point)

Both sinks implement one duck-typed protocol — the union of the methods above:

```
on_net_start(net_name, net_id, route_index, total) -> None
on_net_complete(net_name, success, path, iterations, direction) -> None
on_route_step(snapshot: SearchSnapshot) -> bool     # False = quit
should_pause() -> bool
get_iterations_per_step() -> int
on_negotiation(progress: NegotiationProgress) -> None   # net-level co-opt tick
```

(Exact current signatures are cited above; confirm each at build time — the two surfaces evolved separately.)

### 1.3 The `BoardDelta` vocabulary (define now, keyed by KIID)

A small, stable, source-agnostic event set the canvas paints and the P3 Bridge applies — **defined in P1 even though the Bridge lands in P3**, so identity is stable from day one:

`MOVE_FP {kiid, x_mm, y_mm, rot_deg}` · `ADD_TRACKS [{start,end,width,layer,net_id}]` · `ADD_VIA {x,y,size,drill,layers,net_id}` · `RIP_NET {net_id | [kiid]}` · `NET_START/NET_DONE {net_id, name, success}` · `BLOCKER {net_id, BlockingInfo}` · `PHASE_DONE {phase, completion}`.

Persist KiCad's KIIDs into `PCBData` on read so identity is stable across the file-based (P1/P2) and live (P3) worlds. This is the single most important forward-compat decision.

---

## 2. P1 — Canvas + wavefront (buildable now, file-based, zero engine surgery)

**Deliverable:** a 6th panel in the Routing workspace that renders the board and animates routing as it runs, driven entirely by the existing callbacks against the existing file-based route.

### 2.1 Components (all new, all NETDECK-native)

- **`BoardScene`** — a paintable data model: footprint courtyards + pads, layers, existing/landing tracks, vias, the active A* frontier (open/closed cells), and the current blocker overlay. Built from `PCBData` (`parse_kicad_pcb` / `build_pcb_data_from_board`, `kicad_parser.py:1605`). Pure data; no Qt.
- **`BoardCanvas(QWidget)`** — the `QPainter` view via the `kit.custom()` escape hatch (`tools/ui/kit.py:168`). Renders `BoardScene`; theme colors via `T.qcolor` / `T.category`. Pan/zoom; layer color mapping. QPainter precedent: `library_preview.py:106-145` (MeshView).
- **`BoardPreviewSignals(QObject)`** — the `pyqtSignal` bridge from the engine worker thread to the widget (idiom: `_DownloadSignals`, `shell.py:88-90`). The callback runs on the route worker thread; it *emits signals only*, never touches Qt directly.
- **`CanvasVizCallback(VisualizationCallback)`** — implements §1.2, translating engine events into `BoardPreviewSignals` emissions. Passed as `batch_route(..., vis_callback=…)`. Honors `should_pause()` / `get_iterations_per_step()` for the step controls (play / pause / step / speed).
- **Off-thread drive:** reuse `run_populate` / `run_async` (`tools/ui/util.py:42-72`, `routing.py:172-194`) so the route runs off the UI thread and the canvas stays live.

### 2.2 Where it lives

- **6th panel** added to the Routing workspace panels list (`tools/ui/features/routing.py`, panels ~285-291).
- **No-drift lint compliance:** bespoke `QPainter` visuals go in an **allowlisted** file — `tools/ui/features/routing_panels/routing_visuals.py` (per `tests/test_ui_no_drift.py`) — not inline in a kit-migrated panel.

### 2.3 The two views (one canvas, one toggle)

- **Production view (default):** net-level. `on_net_start` highlights the net; `on_net_complete` lands its track (green success / red fail); `on_negotiation` drives a congestion/contested overlay + a "nets legal / total" readout. This is honest to how the board is really routed.
- **"Watch it think" toggle:** switches the active net to `route_net_with_visualization`, streaming `SearchSnapshot` open/closed frontier per step. Labeled *single-ended demo — slower, not the production route.* Step controls bind to `should_pause` / `get_iterations_per_step`.

**P1 explicitly does NOT** reshape the loop, add recovery, or touch KiCad. It is a pure observer of today's route — shippable on its own, and it makes P2 legible.

---

## 3. P2 — Fail-fast loop + net-level co-opt view

**Deliverable:** reshape `place_route_loop.py` from *batch-route-then-diagnose* into *stop at first blocker → attribute → fix → resume*, and drive the same canvas with net-level co-opt events.

### 3.1 The reshape (from the current shape)

Today (`place_route_loop.py:70-272`): `run_route()` runs the whole route as a subprocess, parses `JSON_SUMMARY`, `blockers_from_summary()` (`:43-67`) extracts blockers, `nets_to_refs()` (`:108-131`) maps to movable refs, `better()` (`:134-156`, already completion-first) accepts/reverts, widening the displacement cap each round. The escalation ladder is **not** called here — grep-confirmed absent; it lives only in `route.py:1088-1146`.

The reshape:

```
route(board)
  └─ first blocker? ── no ─▶ continue / DONE at 100%
                        │ yes
                        ▼
  ATTRIBUTE  — rank blockers by BlockingInfo (unique_cells, then near_target_cells)
               blocking_analysis.py:64-95 · populated route.py:1371-1419
                        │
                        ▼
  FIX, cheapest first:
    1. RECOVERY LADDER  (escalation.py:209-338)   ← compose into the loop (new wiring)
         reorder: neckdown (13/16 recoveries) → fine-grid ½/¼ → shove (0/6, last)
    2. INTENT-PRESERVING REPOSITION  (placement/quench.py)
         quench targeted at the blocker's refs — nudge :419-460 / rotate :435 / swap :462-517
         writer: placement/writer.py:30-120
                        │
                        ▼
  RESUME  — re-route; ACCEPT iff completion improves on the SAME board (tie → DRC/len); else REVERT
```

**Default order (user-confirmed):** recovery-ladder-first, then reposition. Rationale: recovery changes no placement (cheapest, least-disruptive); reposition is a design change and a last resort. This matches the parent co-opt spec's lever sequencing.

### 3.2 The guards (correctness backbone)

- **Oscillation guard:** hash(failed-set + blocker-bbox); on a repeat 2-cycle, **stop and classify** "infeasible as placed" rather than churn. Emits the binding reason ("needs a 3rd layer", "R12/R13 pad-swap would free the frontier", "connector J2 boxes in the net").
- **Preservation invariants (fail the run on violation):** lock connectors / crystals / mounting by refdes + footprint; forbid dragging a decap off its owner's power pin; no fixed part moved (identical `(at x y rot)`); netlist pad→net map identical. (Depends on `placement/constraints.py` — the highest-value unbuilt file, per the parent spec Phase 1.)
- **Structured, not scraped:** consume `BlockingInfo` from `JSON_SUMMARY['blockers']` (already serialized, `route.py:1417`); do not regex-scrape logs. `blockers_from_summary()` already prefers structured — keep it structured-only.

### 3.3 The honest metric (non-negotiable)

- **Headline = completion-delta on the SAME board** (route unmodified → after the fix), never a corpus mean (the corpus swings ±10%, §0).
- **Infeasibility classification is a success, not a failure.** A correct "this board is infeasible as placed, here's the binding constraint" is a first-class output.
- **Never report raw 100%** as if the corpus achieves it. Report per-board before→after + the residual reason.

### 3.4 The co-opt canvas view

The same `BoardCanvas` now shows the loop: current blocker highlighted (frontier bbox from `BlockingInfo.near_target_cells` / `near_source_cells`), the recovery rung being tried, the proposed reposition as a ghosted move, and accept/revert as a color flip. This is the **legible co-opt diff** that is our actual differentiator.

---

## 4. P3 — The KiCad "Bridge" (live board mirror)

**Deliverable:** the EventBus gains a **second sink** — a live-KiCad mirror — so accepted deltas stream into a running KiCad as undo-able frames. The router stays the file-based compute brain; the Bridge is the hands & eyes. **New consumer of the P1 event stream + a ~250-line plugin — not a rewrite.**

### 4.1 Architecture (from WF3, verified against local installs)

A **thin resident KiCad plugin using `kipy` internally, driven over a local NNG socket by the standalone PyQt5 app+engine.** Verified: `kipy 0.7.1`, `kicad-cli 10.0.4`; kipy covers **11/13** needs directly.

- **Not pcbnew** — SWIG deprecated, **removed in KiCad 11**; a dead end.
- **Not pure-kipy-from-app-alone** — two API-wide gaps are cleaner solved inside KiCad's process.

**Verified kipy coverage:** read (`get_footprints/get_pads/get_nets/get_tracks/get_vias/get_zones/get_stackup`); **footprint move** (`fp.position=Vector2.from_mm`, `fp.orientation=Angle.from_degrees`, `board.update_items([fp])` — the primitive the old plugin never had, and co-opt needs); track/via/zone write (`board.create_items([...])`, maps 1:1 onto `kicad_writer.py:373 add_tracks_and_vias_to_pcb`); rip/modify (`remove_items`/`update_items`); transactions (`begin_commit`/`push_commit("msg")`/`drop_commit`).

### 4.2 The two decisive gaps and how the Bridge absorbs them

- **#6 No live DRC over IPC** → `board.save()` then `kicad-cli pcb drc --format json`; overlay results, **save-gated / out-of-band**, labeled "as of last save." Never presented as live-accurate.
- **#11 No events** → the API is strict request-reply ("async notifications not possible"), with an `AS_BUSY` single-socket constraint. The Bridge, living inside KiCad's process, **owns a `wxTimer` poll pump** (2–5 Hz `get_items`/`get_selection`, KIID-diff), turning "the app must poll" into "the app gets notified," and **orchestrates the save→DRC→overlay** cycle.

### 4.3 The streaming primitive

**One `push_commit` = one undo entry = one repaint = one frame.** The **KiCadApplier** (write sink in the app) drains the coalescing EventBus queue at 10–20 Hz; each tick: one `begin_commit`, batch `create_items`/`update_items`, `push_commit(label)`. Labels read like a build log (`"Auto-route /USB_DP"`, `"Nudge C14"`). Under load: **widen the interval / drop frame rate — never block the engine.**

### 4.4 Connectivity truth

No ratsnest RPC, but `get_connected_items` exists (KiCad 10.0.1+); derive unconnected-per-net and reconcile with `ratsnest_check.reconcile()` (`ratsnest_check.py:9`, pure/deterministic — **KiCad's answer wins**). Do one authoritative rebuild per *phase*, never per track.

### 4.5 Packaging & targets

User ticks *Preferences → Plugins → Enable IPC API server* (off by default) + keeps the board open; `pip install kicad-python`; the Bridge is one auto-installable `plugin.json` + small entry. **Target KiCad 10.0.1+.** File + `kicad-cli` is the KiCad-8 / headless / disabled-IPC fallback (reuses `write_placed_output` + `add_tracks_and_vias_to_pcb`).

### 4.6 Risks (measure the first one before committing)

- **Latency at scale (HIGH):** research cites ~10–20s for 100 moves over IPC. **Benchmark `push_commit` RTT at N-tracks/commit early**; cap RTT below the frame interval; shed by coalescing, not by dropping correctness.
- **AS_BUSY during interactive drag (MED):** Bridge owns the drag flag + region locks; Applier retries with backoff, never hard-fails a tick.
- **Version dependence (MED):** gate with `check_version()`; target 10.0.1+; fall back to file+cli on 8.
- **DRC lag (MED):** save-before-DRC, labeled "as of last save."

---

## 5. What P1/P2 must do so P3 is not a rewrite

1. **Canonical source = the event stream, not `JSON_SUMMARY`.** Make the per-net/per-step callbacks the source of truth; `JSON_SUMMARY` is the end-of-run reduction. Both sinks subscribe to identical events.
2. **`BoardDelta` vocabulary keyed by KIID from day one** (§1.3). Persist KiCad KIIDs into `PCBData` on read.
3. **Acceptance authority stays in-process and side-effect-free** — the same control loop works over a subprocess (P1/P2) or an IPC stream (P3).
4. **Canvas stays strictly read-only** — one writer only (the engine → sinks). No second writer, ever.
5. **Thread every new engine kwarg through BOTH CLI and GUI** (argparse + GUI call sites + options panel + `settings_persistence.py`) — per `CLAUDE.md` CLI/GUI parity. When P3 adds `--live` / `ipc_sink=…`, it lands in both or it's a silent no-op.

---

## 6. Phasing, testing, measurement

| Phase | Deliverable | Buildable | Gate |
|---|---|---|---|
| **P1** | Canvas + wavefront panel, `CanvasVizCallback`, EventBus, `BoardDelta` vocab | Now — zero engine surgery | Panel renders a real route live; no-drift lint green; off-thread (no UI stall) |
| **P2** | Fail-fast reshape of `place_route_loop.py`, ladder composed in-loop, guards, honest metric, co-opt canvas view | After P1 | Before→after completion on ≥1 board improves; oscillation guard stops & classifies; preservation invariants fail the run on violation |
| **P3** | KiCad Bridge sink + plugin | After P1/P2; needs KiCad 10.0.1+ | `push_commit` RTT measured & under frame budget; deltas mirror to live board as undo-able frames; DRC overlay save-gated |

**Tests:** P1 — `BoardScene`-from-`PCBData` fixture; signal-thread-safety (callback emits, never touches Qt); no-drift lint on `routing_visuals.py`. P2 — blocker-ranking fixture (structured, not scraped); oscillation-guard cycle test; preservation-invariant violation fails the run; before→after harness on the corpus (`QT_QPA_PLATFORM=offscreen`). P3 — KIID round-trip (rotate → reparse → assert linkage); RTT benchmark; version-gate + file-fallback path.

**Measurement discipline (all phases):** completion before→after on the **same** board; infeasibility classification counts as success; never a corpus mean; never raw 100%.

---

## 7. Open questions / risks

1. **Viz honesty vs. appeal.** The "watch it think" wavefront is the single-ended demo, not the production route — the toggle must stay clearly labeled or it misleads. Open: how prominent should the production net-level view be vs. the seductive A* animation?
2. **Fail-fast vs. throughput — DECIDED (2026-07-08):** the loop stops at the **single first blocker**, strictly one-at-a-time (the literal request), chosen for co-opt-viz legibility over throughput. Accepted cost: slower on boards with many independent failures. Top-K-independent batching is explicitly *not* built; revisit only if corpus wall-time proves prohibitive.
3. **`push_commit` RTT is unmeasured.** The whole P3 seamlessness claim rests on it. Must be benchmarked before committing to live streaming; the file-fallback keeps P3 valuable even if live proves too slow.
4. **`placement/constraints.py` is a P2 dependency** (preservation invariants) and is unbuilt — carried from the parent co-opt spec's Phase 1. P2 either builds it or degrades to refdes+footprint+net-name heuristics.
5. **GUI write-back debt.** P2's reposition is CLI-first; a live GUI reposition needs P3's Bridge (kipy footprint move) — do not ship a GUI button that silently does nothing.
6. **The router is still 40.8%.** This spec makes the router *visible and honest*; it does not make it *finish boards*. That is the separate 8-upgrade outperform track (the report). The viz must not imply the completion problem is solved.

---

## 8. Reuse-vs-build summary

**Reuse as-is:** the entire engine event surface (`vis_callback`, `progress_callback`, `NegotiationProgress`, `SearchSnapshot`, `route_net_with_visualization`); `place_route_loop.py` control flow + `better()`; `escalation.py` ladder; `blocking_analysis.py` `BlockingInfo`; `placement/quench.py` moves + `placement/writer.py`; `kicad_parser.py` `build_pcb_data_from_board`; `kicad_writer.py:373`; `ratsnest_check.reconcile()`; `kit.custom()`, `T.qcolor/category`, `run_populate/run_async`, the `_DownloadSignals` idiom, `library_preview.py` QPainter precedent.

**Build new:** `routing_visuals.py` (`BoardScene` / `BoardCanvas` / `BoardPreviewSignals` / `CanvasVizCallback`, allowlisted); the 6th Routing panel; the `BoardDelta` vocabulary + KIID persistence; the fail-fast reshape + oscillation guard + preservation invariants in `place_route_loop.py`; (P3) the `KiCadApplier` sink + the ~250-line Bridge plugin. **Do NOT build:** a second canvas writer; a pcbnew path; per-track connectivity rebuilds; any engine-core change for P1/P2.

---

_Next per brainstorming: spec self-review → **user review gate** → `writing-plans`._
