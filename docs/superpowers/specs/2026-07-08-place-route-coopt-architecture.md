# Place ↔ Route Co-Optimization — Whole-System Architecture & Roadmap

**Date:** 2026-07-08
**Status:** Proposed architecture (synthesis of 4 research findings, grounded in the repo)
**Parents:**
- `docs/superpowers/specs/2026-07-07-placement-assisted-routing-roadmap.md` (P1→P4 placement track)
- `docs/superpowers/specs/2026-07-07-placement-p1-auto-constrained-repair-design.md` (P1 detail)
- `docs/superpowers/specs/2026-07-08-routing-completion-100pct-campaign-design.md` (routing campaign)

## 0. Goal & scope

**User goal, verbatim intent:** import a schematic's initial component placement onto the
PCB, then *incrementally* improve/reposition components ("a bit") so the autorouter reaches
100% completion, **while preserving per-component design-rule best-practices** (decoupling
caps near their IC's power pins, crystal near the MCU, connectors at board edges,
thermal/courtyard clearance).

**The one-sentence architecture:** a **perturbative, congestion-guided, constraint-preserving
place↔route co-optimization loop** built on the engine that already exists — start from the
KiCad-imported placement, classify what must not move, use a cheap routability surrogate to
propose small nudges, and *gate every accepted move on a real re-route that improves
completion*, escalating routing recovery (shove/neckdown/fine-grid) before ever moving a
footprint.

**In scope:** the `.kicad_pcb`-in → routed-`.kicad_pcb`-out pipeline. Constraint
classification, perturbative placement refinement, the router-in-the-loop accept/revert
control, structured feedback between router and placer, and honest infeasibility reporting.

**Explicitly out of scope** (unchanged from the roadmap): schematic editing, BOM/part
changes, layer-count changes, board-outline changes. From-scratch auto-placement is deferred
to the very last phase and is *not* the recommended path — the repo's own removed `place.py`
experiment showed hand placement beating constructive placement by ~500× router effort
(`placement/README.md`). "Don't ruin the design" is a hard gate, not a preference.

**Two corrections to common framing, both verified in-repo:**
1. **Shove already exists.** `shove.py::try_shove_transaction` + `rip_up_reroute.py` implement
   *transactional rip-up-and-reroute* (not geometric hull-walking shove), wired as escalation
   rung 1 in `escalation.py`. It is grid-native and reversible.
2. **Schematic intent is already in the board file.** `kicad_parser.py:1183–1189` extracts
   `pad.pinfunction` / `pad.pintype` (schematic-derived: `power_in`, `GND`, clock, …). The
   design intent the user wants — which pin is a power pin, which cap is a decap — is
   recoverable from the `.kicad_pcb` **without a `.kicad_sch` parser.** "Import placement" is
   done by KiCad's *Update PCB from Schematic*; the repo's job starts at the `.kicad_pcb`.

**Current baseline:** `bench/results/baseline-p3.json` → **mean completion 55.19%** across the
6-board corpus (icebreaker 33% / synthetic_bga 18% … bb_tb6612 80%). The corpus is small and
run-to-run noisy — `ladder-subplan-b.json` even shows 43.5%, *lower* than baseline. **This is
the single most important measurement fact: gains must be measured before→after on the *same*
board, never against a corpus mean.**

## 1. End-to-end architecture (the loop)

```
                         ┌─────────────────────────────────────────────────┐
   KiCad "Update PCB     │  .kicad_pcb  (schematic-imported placement)      │
   from Schematic"  ───▶ │  pads carry pinfunction/pintype (design intent)  │
                         └───────────────────────┬─────────────────────────┘
                                                 │
                         (A) classify_constraints(pcb)            [placement/constraints.py — NEW]
                             → fixed refs · preserved refs · ignore_net_ids
                                                 │
              ┌──────────────────────────────────▼───────────────────────────────┐
              │  OUTER LOOP  (place_route_loop.py — EXISTS, extend)               │
              │                                                                   │
              │   route(board)  ── negotiated engine ──▶ completion%,             │
              │                     failed nets, structured blocker attribution   │
              │                     (BlockingInfo) + congestion readout           │
              │                                 │                                 │
              │        completion == 100%? ─────┴── yes ─▶ DONE                    │
              │                                 │ no                              │
              │                                 ▼                                 │
              │   ROUTING-FIRST RECOVERY (escalation.py — EXISTS)                 │
              │     rung1 shove(rip+reroute) · rung2 neckdown · rung3 fine-grid   │
              │     — always tried before moving any footprint —                  │
              │                                 │ still failing?                  │
              │                                 ▼                                 │
              │   target = small movable parts owning failed ∪ blocker nets       │
              │            nearest the blocked frontier (near_target/near_source) │
              │                                 │                                 │
              │        ┌────────────────────────▼───────────────────────────┐    │
              │        │ INNER QUENCH (placement/quench.py — EXISTS, extend)  │    │
              │        │  cheap surrogate, ZERO real routes:                  │    │
              │        │   cost = airwire len + pin-pair crossings            │    │
              │        │        + congestion-scaled halo (RUDY overflow)      │    │
              │        │        + decap→power-pin / xtal→MCU attraction       │    │
              │        │  moves: nudge (≤max_disp) · 90° rot · same-fp swap   │    │
              │        │  proposer: force-vector toward failed-net centroid   │    │
              │        └────────────────────────┬───────────────────────────┘    │
              │                                 ▼                                 │
              │   write_placed_output → candidate board (placement/writer.py)     │
              │   route(candidate)                                                │
              │        ACCEPT iff completion improves (tie→DRC/len);              │
              │        else REVERT, widen displacement cap                        │
              │        oscillation guard: hash(failed-set + blocker-bbox)         │
              └───────────────────────────────────┬───────────────────────────────┘
                                                  │
                    assert preservation_invariants (fixed parts unmoved,          [place_route.py — NEW]
                    netlist identical, no courtyard overlap, no net-new DRC)
                                                  │
                    before→after completion report + honest infeasibility         [diagnose-routing-failures skill]
                                                  ▼
                            routed, DRC-clean .kicad_pcb  (or honest "why not 100%")
```

**Two nested loops, one authority.** The **inner quench** is a fast geometry-only surrogate
that *orders* candidate moves; it never decides acceptance. The **outer loop** re-routes for
real and is the sole authority on acceptance. The surrogate may lie (RUDY is optimistic near
BGA/QFP fan-out); the real router is ground truth. This discipline — accept only on a real
completion gain — is already implemented and is the design's correctness backbone.

**Sequencing of levers (cheapest / least-disruptive first):**
routing-recovery (shove → neckdown → fine-grid, no design change) **before** placement nudges
(design change, last resort). Within placement: rotate/flip and same-footprint swap (free,
function-preserving) before positional nudges; small nudges before any wider displacement.

## 2. Subsystem breakdown

Each subsystem: **responsibility · interface · reuse-vs-build with file cites.**

### 2.1 Schematic-placement import
- **Responsibility:** get the initial placement + design intent onto the board.
- **Interface:** KiCad *Update PCB from Schematic* produces the `.kicad_pcb`; `parse_kicad_pcb`
  reads it, including `pad.pinfunction`/`pad.pintype`.
- **Reuse:** `kicad_parser.py` (text path `:1183–1189`, pcbnew path `:1902–1909`). **Build:**
  *nothing.* A `.kicad_sch` parser is **not** needed for P1 — intent is already in pad
  metadata. (`schematic_updater.py` only *writes* pad swaps back; it does not read
  equivalence intent — that is a P2 concern.)

### 2.2 Constraint classifier — "what must not move / what to preserve"
- **Responsibility:** from `PCBData` + `.kicad_pcb` text, derive `(fixed_refs,
  preserved_refs, ignore_net_ids, report)` under the principle **"when uncertain, FIX."**
  - Fixed: existing `(locked yes)`; connectors (`J/P/CN/TB` or fp `Connector|Header|USB|Jack|
    Socket|TestPoint|Terminal`); board-edge parts (courtyard bbox within `edge_margin` of
    `board_bounds`); mounting/fiducials (`H/MH/FID` or large-drill `net_id==0`); crystals/
    oscillators (`Y/X` or fp `Crystal|Oscillator|Resonator`).
  - Preserved (locked in P1): **decoupling caps** — 2-pad `C*` with a power/GND pad,
    associated *through the shared net* to an IC power pin (`pintype ∈ {power_in, power}` /
    `pinfunction ~ VCC|VDD|VDDA|AVDD|3V3|5V|VBAT`) within `decap_radius` (~3 mm).
  - `ignore_net_ids`: power/plane nets by name (`GND|VCC|VDD|VSS|^\+|VBUS|VBAT|VREF`) or
    pad-count > `plane_net_pads` (~12).
- **Interface:** `classify_constraints(pcb_data, pcb_file) -> Constraints`.
- **Reuse:** `analyze_power_paths.py:100 _auto_classify_component` (refdes/pintype →
  role) is the seed — extend, don't replace; `net_queries.py:228 identify_power_nets` +
  `list_nets.py find_power_nets` for power-net patterns; `placement/parser.py`
  `extract_locked_refs` / `extract_courtyard_bboxes` for geometry. **Build:**
  **`placement/constraints.py` — does not exist** (~300 lines). This is the highest-value new
  file; it makes "preserve decap-near-pin / crystal-near-MCU" *automatic* instead of relying
  on `--lock` incantations.

### 2.3 Routability surrogate — cheap "will-it-route" oracle
- **Responsibility:** score a candidate placement without paying a full route, so the inner
  quench can explore many moves cheaply.
- **Interface:** a bin-grid RUDY map — per net, deposit `1/w + 1/h` demand over its bbox bins
  + a pin/pad-density term; supply reduced in courtyard/keepout/edge bins, scaled by layer
  count; overflow = `demand/supply`. Consumed as a quench cost term.
- **Reuse:** `connectivity.py compute_mst_edges` (airwire model, already shared with quench);
  `placement/quench.py::_count_crossings_np` machinery for the crossing term. **Build:**
  `placement/congestion_estimate.py` (~200 lines, new) **and** upgrade the crossing metric to
  Cypress-style **source→sink pin-pair** decomposition (more accurate routing-resource proxy
  than MST-edge or convex-hull overlap).

### 2.4 Placement optimizer (inner quench) — the refiner
- **Responsibility:** greedy zero-temperature quench proposing legal small moves that reduce
  the surrogate cost; locked/preserved parts frozen; deterministic.
- **Interface:** `quench(pcb_data, ..., move_refs, net_weights, lock_refs, ignore_nets) ->
  placements`. Cost today = `length + crossing_penalty·crossings + halo + edge`. Moves =
  nudge (≤`max_displacement`), 90° rotation, same-footprint swap. `candidate_valid` rejects
  courtyard overlap / margin violation.
- **Reuse:** `placement/quench.py` (the whole optimizer + `move_refs`/`net_weights`/legality —
  verified present at `:160–265`). **Build (cost extensions only):** congestion-scaled halo
  (hot bins inflate whitespace = cell-inflation; cool bins relax); **decap→power-pin and
  crystal→MCU attraction springs** (the "preserve best-practices" cost, high weight, keeps
  parts hugging their owner — the current halo/length terms actively *fight* this); optional
  force-vector move proposer (net-force toward connected-pad centroid) to beat the uniform
  `(2n+1)²` grid scan.

### 2.5 Placement writer / legality
- **Responsibility:** write moved/rotated footprints back to `.kicad_pcb`, re-rotating pad
  angles correctly (KiCad "pad angle = footprint + pad rotation" gotcha).
- **Interface:** `write_placed_output(input_file, output_file, placements)`.
- **Reuse:** `placement/writer.py:30` (verified). **Build:** a **round-trip test**
  (write→reparse→assert positions) — the regex `(at …)` rewrite is a latent edge case for
  footprints whose first `(at …)` isn't the origin or non-90° seed rotations.

### 2.6 Router (negotiated-congestion core)
- **Responsibility:** grid A* + PathFinder negotiated congestion — the batch legalizer that
  produces completion. **This is the strongest asset and is *not* the bottleneck.**
- **Interface:** `route.py` CLI → `JSON_SUMMARY` (verified at `route.py:1394`) carrying
  `routed_single`, `failed_single`, structured `failed_multipoint` (with pad refs+coords),
  `multipoint_pads_connected/total`, `total_iterations`, `total_vias`, `wire_length_mm`.
- **Reuse:** `negotiated_loop.py` + `rust_router/src/congestion.rs` (`CongestionMap`: per-cell
  `usage`/`history`, `contested_nets()`, `cost_at()`). **Build:** *nothing to the core.*

### 2.7 Routing-recovery escalation (grid-native "shove")
- **Responsibility:** recover individual failed nets *without changing the placement* — the
  lever that must fire before any footprint moves.
- **Interface:** `escalation.py::run_escalation_ladder` — rung1 shove (rip blockers, reroute
  victims, atomic rollback) → rung2 fab-floor neckdown → rung3 fine-grid retry.
- **Reuse:** `escalation.py`, `shove.py::try_shove_transaction` + `ShoveContext`,
  `rip_up_reroute.py` (`rip_up_net`/`restore_net`, incl. issue-#134 collision-aware restore),
  `fine_grid_retry.py`, `rust_router/src/obstacle_map.rs` (ref-counted `blocked_cells`).
  **Build (Track B, only if needed):** in-loop targeted rip + per-net history spikes inside
  `negotiated_loop.py`; wider victim set (`MAX_VICTIMS_DEFAULT=3` → 5–8) with victim layer
  changes; **shove∧neckdown in one transaction** (a net often needs both space *and* width).
  **Do NOT build** a geometric PNS-style hull-walkaround shove — wrong altitude (moves copper
  µm; the goal needs components moved mm) and wrong substrate (the Rust world is a discrete
  grid with no continuous polyline topology; verified — 3–6k lines, months, low incremental
  gain over Track A+B; corpus already shows shove-as-built recovered **0** nets while neckdown
  recovered **13 of 16**, `escalation.py:182–183`).

### 2.8 Blocker attribution & congestion feedback
- **Responsibility:** tell the placer *where* and *why* routing failed, spatially.
- **Interface:** `analyze_frontier_blocking() -> List[BlockingInfo]` with `blocked_count`,
  `unique_cells`, **`near_target_cells` / `near_source_cells`** (verified
  `blocking_analysis.py:64–72`).
- **Reuse:** `blocking_analysis.py` (the spatial signal). **Build (the highest-ROI reliability
  fix):** **serialize `BlockingInfo` into `JSON_SUMMARY`** (it is *not* there today — verified;
  the summary carries `failed_multipoint` pad coords but not blocker attribution) and **delete
  the `re.findall` blocker scrape** in `place_route_loop.py:63`. Today placement recovers
  blocker nets by regex-parsing log *text* — the single most brittle coupling in the system.
  Also optionally expose `CongestionMap.contested_nets()` as an authoritative heatmap.

### 2.9 Orchestrator (outer loop) + integrated CLI
- **Responsibility:** run the whole loop, accept/revert on real completion, report
  before→after, assert preservation invariants.
- **Interface:** `place_route_loop.py` (loop) + a new thin `place_route.py` (classify → route →
  repair → route → report).
- **Reuse:** `place_route_loop.py` control flow (verified: `run_route`, `nets_to_refs`,
  revert/widen, `--max-target-pins`). **Build:** change `better()` (`:100`) from
  failures→iterations to **completion-first, then failures, then iterations, tie→DRC/len**;
  thread auto-constraints; add oscillation guard (hash of failed-set + blocker-bbox → stop &
  report rather than cycle); **`place_route.py` — does not exist.**

### 2.10 Measurement / bench + diagnosis
- **Responsibility:** prove a change earned its gain; explain residual failures honestly.
- **Interface:** `bench/benchmark.py` (+ `--compare`, exits non-zero on regression); the
  `diagnose-routing-failures` skill for the terminal "why it can't finish" report.
- **Reuse:** `bench/benchmark.py`, `.claude/skills/diagnose-routing-failures`. **Build:** a
  **placement bench mode** — `bench/benchmark.py` has **zero** placement references today
  (verified). Per board: route unmodified → repair loop → route again → record before→after
  completion + preservation assertions.

## 3. Phased roadmap

Each phase is independently valuable, independently testable, and gated on completion
before→after on the same board (never a corpus mean). This aligns with and refines the
existing P1→P4 roadmap.

### Phase 0 — Ground truth & plumbing *(first, cheap, unblocks everything)*
- **Wire placement into `bench/benchmark.py`** (per-board before→after + preservation asserts).
- **Serialize `BlockingInfo` into `JSON_SUMMARY`; delete the log-regex scrape** in
  `place_route_loop.py`.
- **`better()` → completion-first.**
- **Value:** you can finally tell whether placement helps at all; kills the brittlest
  coupling. **Test:** placement bench mode runs on the corpus and emits before→after numbers;
  a fixture confirms structured blocker data round-trips.
- **Effort:** ~1–2 days. Mostly plumbing.

### Phase 1 — Auto-constrained perturbative repair *(the user's core ask)*
- **`placement/constraints.py`** (classifier + preservation invariants).
- **`place_route.py`** (integrated CLI: classify → route → repair → route → report).
- **Decap→power-pin / crystal→MCU attraction terms** + default lock profile for
  connectors/crystals/mounting so best-practice survives *by default*.
- **Value:** "import placement, nudge a bit, preserve best-practices, route" works end-to-end
  and automatically. **Test:** classifier fixtures (connector/edge/mounting/crystal/decap/
  movable-R → correct verdicts, "uncertain→FIX"); quench-legality property test; writer
  round-trip; tiny end-to-end that improves-or-equals completion and moves no fixed part.
- **Effort:** ~1–1.5 weeks. Well-scoped; the hard 80% (quench/loop/writer/legality) exists.

### Phase 2 — Routing recovery composed under the loop
- **Compose the escalation ladder inside the outer loop** so shove/neckdown/fine-grid fire
  *before* placement moves on each round.
- **Track B (only if Phase 1 plateaus):** in-loop targeted rip + history spikes, wider
  victims + victim layer changes, shove∧neckdown-in-one-transaction.
- **Value:** claws back the "shove=0" nets and lets routing absorb congestion the placer
  can't cheaply fix; keeps placement changes a genuine last resort. **Test:** ablation on the
  corpus (e.g. `MAX_VICTIMS=8` + shove∧neckdown) before/after; escalation stays bounded.
- **Effort:** ~3–7 days.

### Phase 3 — Congestion-guided surrogate *(makes the inner loop smart)*
- **`placement/congestion_estimate.py`** (RUDY map) + **congestion-scaled halo** + Cypress
  source→sink crossing metric + optional force-vector proposer.
- **Feed frontier congestion back into targeting** (`near_target_cells` vs `near_source_cells`
  picks *which end* of the net to relieve; move parts *down the congestion gradient*).
- **Value:** the quench proposes better moves faster and reacts to a *gradient* (present
  before anything fails), not just post-mortem hard failures. **Test:** surrogate correlates
  with real completion on the corpus; per-board before→after improves vs Phase 1.
- **Effort:** ~1 week. Tuning-heavy (weights don't transfer perfectly between boards — gate
  every weight change through `bench/benchmark.py --compare`).

### Phase 4 — Wider placement moves (P2/P3, escalate only as numbers demand)
- **Function-preserving pin/gate swap (P2 safe subset first):** symmetric 2-terminal passive
  pad swap (≡180° flip) — Icebreaker failures are dominated by series R-packs, so this alone
  is high-value. Metadata-gated bank/gate swaps only behind an explicit equivalence source.
- **Region re-placement (P3):** bounded auto-re-placement of a cluster (e.g. all decap around
  one IC) inside a defined region.
- **Value:** the last few percent on boards where nudge+recovery plateau. **Test:** function-
  preservation invariant (netlist pad→net map identical modulo proven-equivalent swaps).
- **Effort:** ~1–2 weeks each. **P4 full auto-placement is explicitly *not* recommended** —
  deferred, and the repo's own data says it loses to hand placement.

### GUI parity (spans all phases — a documented, intentional gap)
Per `CLAUDE.md` "keep CLI and GUI in sync": the `kicad_routing_plugin/` builds `PCBData` from
live pcbnew and has **no footprint move/write-back path** (verified — no `write_placed_output`
/ quench references). Placement is **CLI-first**; GUI in-loop placement needs a pcbnew
write-back and is a documented follow-up. **Do not ship a GUI button that silently does
nothing.** Any new engine kwarg (`congestion_weight`, `--auto-constraints`, displacement
ceiling) must be threaded through *both* the argparse layer *and* the GUI call sites + options
panel + `settings_persistence.py`, or it is a silent no-op in the GUI.

### First buildable milestone (concrete)
**Phase 0, step 1 + 2:** extend `route.py`'s `JSON_SUMMARY` to include the `BlockingInfo` list
(net_id, `unique_cells`, `near_target_cells`, `near_source_cells`, frontier bbox), then replace
`place_route_loop.py`'s `re.findall(r'^\s+\d+\.\s+(\S+?):', log)` blocker scrape with a JSON
read. Verifiable in an afternoon: a single `place_route_loop` run on `icebreaker` produces the
same targeting from structured data as from the regex, with no log-format dependency. This is
the smallest change that removes the system's most brittle coupling and unblocks all feedback
work.

## 4. The honest hard parts

**(a) Shove routing — the tempting wrong turn.** The instinct to add geometric push-and-shove
is misguided *for this engine and goal*. Geometric PNS (KiCad `pns_shove.cpp`) needs continuous
`SHAPE_LINE_CHAIN` polylines, hull generation, walkaround, rank-bounded recursion, and a
copy-on-write springback NODE tree — the *opposite* substrate to a discrete grid with
ref-counted blocked cells. It is 3–6k lines of subtle geometry, months of solo work, and a
grid A* can't even represent the sub-cell slither that makes PNS shove tight (quantization is
the real ceiling). The corpus already measured the grid-native shove recovering **0** nets
while neckdown recovered **13 of 16**. The right "batch shove" here is *fixing the existing
rip-and-reroute* — fire it in-loop, wider, combined with neckdown — not a second geometry
engine. **Validate any shove investment with a one-afternoon ablation before committing.**

**(b) The refiner constraint model — the genuinely valuable hard part.** Encoding
"decap-near-pin / crystal-near-MCU / connector-at-edge / thermal clearance" is the part no
autorouter models and where solo effort compounds. The subtle failures:
- **Silent design erosion.** A move can improve routability while dragging a decap 4 mm off its
  IC — DRC-clean, electrically worse, invisible. Mitigations: **"when uncertain, FIX"**
  (lock rather than move); a hard **per-part displacement ceiling** independent of the widening
  schedule; the decap/xtal **attraction spring** (or rigid offset-lock to the owner); a
  **per-round distance-to-owner delta audit**; the preservation invariants that *fail the run*
  on violation.
- **Classifier false negatives** are the highest-consequence error (an unrecognized decap gets
  moved). "When uncertain, FIX" bounds it; an EMC-style spot check on outputs catches the rest.
- **Metadata absence.** `pinfunction`/`pintype` exist only if the schematic populated them.
  Degrade gracefully to refdes+footprint+net-name heuristics (what `_auto_classify_component`
  already does).

**(c) The routability proxy lies.** Airwire length/crossings/RUDY only *correlate* with
routability; RUDY is optimistic near BGA/QFP fan-out and misses via-starvation and layer-
direction bias. **This is why the real route is the sole acceptance authority.** The surrogate
orders moves; it never accepts them. Do not let a better proxy tempt you into skipping the
re-route.

**(d) Non-locality & no guarantee.** Moving a part to fix net A congests net B (whack-a-mole).
The revert-if-worse loop bounds the damage but can stall in a local minimum where no single-
part move helps yet the board isn't 100%. This is genuinely unsolved — there is no cheap method
that *guarantees* 100% by nudging. Mitigations: oscillation guard (hash failed-set +
blocker-bbox; stop & report on a repeat 2-cycle), limited 2-part coordinated moves for the swap
case, and **accepting that some boards need a layer added or a manual move** — report the
binding constraint honestly rather than promising 100%.

**(e) Tiny, noisy corpus.** 6 boards, single-pass, ±10% run-to-run swings. The real risk is
declaring victory or defeat on noise. Before→after on the *same* board + expanding the corpus
is the only defense.

## 5. How to measure success

"100% on routable nets + honest infeasibility reporting + design-rule preservation" decomposes
into four gates, all of which must hold for a phase to PASS:

1. **Completion (headline).** Fully-routed nets / total on the final placed+routed board.
   **Measured before→after on the same board** (route unmodified, then after the phase's
   manipulation) so the manipulation is *proven* to earn the gain. Never a corpus-mean compare
   (the corpus is too noisy).
2. **Honest infeasibility.** When a board can't reach 100%, the loop **stops and reports the
   binding constraint** (`_classify_binding` + the `diagnose-routing-failures` skill) instead
   of churning or over-claiming — e.g. "needs a 3rd layer," "R12/R13 pad-swap would free the
   frontier," "connector J2 boxes in the net." A correct "this is infeasible as placed, here's
   why" is a *success*, not a failure.
3. **Design-rule preservation** (fail the run if violated): no fixed part moved (identical
   `(at x y rot)`); netlist pad→net map identical (P1 does no remap; P2 only proven-equivalent
   swaps); no courtyard overlap / margin violation (`candidate_valid` + `check_pads.py`);
   no net-new DRC class vs the unmoved-placement route; per-round decap/xtal distance-to-owner
   delta within tolerance.
4. **Regression gate.** `bench/benchmark.py --compare` green (no board regressed); fast test
   suite + `cargo test` green; corpus fixtures restored after any run.

**A phase PASSES** iff it strictly improves completion on ≥1 corpus board with gates 2–4 all
satisfied and no board regressed.

## 6. Open questions / risks

1. **Router is the ceiling past ~85%.** Placement co-opt raises the ceiling; it cannot
   substitute for a shove-capable *router*. Be honest that above ~85% the router (its lack of
   geometric shove, its grid quantization), not the placer, is the bottleneck — and the
   grid-native answer to "not enough room" is the region-clipped fine grid, not a new geometry
   engine. **Open:** is a bounded continuous-geometry legalizer *just for the last-mm slither*
   ever worth it, or is fine-grid always enough?
2. **Weight tuning doesn't generalize.** A surrogate weight set that fixes board X can regress
   board Y; without the bench gate this becomes overfitting — the most likely quiet-failure
   mode. **Open:** per-net-class weight profiles vs one global set?
3. **Whack-a-mole / local minima.** No guarantee of 100% by nudging; the oscillation guard
   detects cycles but doesn't escape them. **Open:** how far to push coordinated multi-part
   moves (P3 region re-placement) before declaring infeasible?
4. **GUI write-back debt.** Placement is CLI-only; a pcbnew footprint write-back is real work
   deferred across all phases. **Open:** invest in GUI write-back, or keep placement a CLI
   pre-step to GUI routing indefinitely?
5. **Analog/EMC/thermal placement is genuinely unsolved** and should not be claimed
   (return-path, sensitive-node isolation, thermal). Scope stays digital-routability
   refinement of a seed; the attraction/lock terms are a *floor* (don't make it worse), not
   EMC-aware placement.
6. **Corpus size.** 6 boards is too few to trust. Expanding the corpus is a prerequisite for
   confident claims and is itself un-scoped work.

## 7. Reuse-vs-build summary

**Reuse as-is (the expensive parts are done and correct):** `placement/quench.py` (optimizer,
moves, legality, `move_refs`/`net_weights`), `placement/writer.py`, `placement/parser.py`,
`placement/utility.py`, `place_route_loop.py` (loop skeleton), `place_optimize.py` (one-shot
CLI), `negotiated_loop.py` + `rust_router/src/congestion.rs` (PathFinder core),
`escalation.py` + `shove.py` + `rip_up_reroute.py` + `fine_grid_retry.py` (recovery),
`rust_router/src/obstacle_map.rs` (grid world model), `blocking_analysis.py` (`BlockingInfo`),
`connectivity.py compute_mst_edges`, `analyze_power_paths.py` (`_auto_classify_component`),
`net_queries.py`/`list_nets.py` (power-net patterns), `kicad_parser.py` (pin metadata),
`route.py` `JSON_SUMMARY` contract, `bench/benchmark.py`, the `diagnose-routing-failures` skill.

**Build new:** `placement/constraints.py` (classifier + invariants — highest value),
`place_route.py` (integrated CLI), `placement/congestion_estimate.py` (RUDY surrogate), cost
extensions in `quench.py` (congestion halo, Cypress crossings, decap/xtal attraction, force
proposer), `better()` → completion-first + oscillation guard in `place_route_loop.py`,
`BlockingInfo` serialization into `JSON_SUMMARY` + regex-scrape deletion, placement bench mode,
the test suite (classifier fixtures, quench-legality property test, writer round-trip, tiny
end-to-end). **Do NOT build:** a geometric PNS shove, a full analytical/GPU placer, from-scratch
auto-placement, a `.kicad_sch` parser for P1.

**Stale-doc fix:** `place_optimize.py` and `placement/README.md` reference
`docs/placement-optimization.md`, which **does not exist** — repoint to the
`2026-07-07-placement-*` specs and this architecture doc.
