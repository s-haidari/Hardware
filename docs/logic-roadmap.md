# NETDECK Logic & Functionality Roadmap

Generated 2026-07-05 from a five-lens code audit (bring-up engineer, correctness
auditor, KiCad workflow, extensibility architect, usability). 34 candidates →
27 distinct changes. Correctness bugs lead; convergence (multiple lenses landing
on the same change independently) is called out.

**Verification status:** items tagged **[VERIFIED]** were re-checked by hand
against the live code and database after the audit. The rest are grounded agent
findings not yet independently confirmed — verify before acting.

---

## (A) Correctness & trust fixes — do these first

**A1. [BUG · VERIFIED · 4-LENS CONVERGENCE] `current_budget()` measures the wrong power path**
Tallies only ADG714-switched channels per rail vs a flat `assumed_target_draw_ma=240`.
Verified on LQFP64: VTARGET is 2 switched channels **+ 2 direct pins**, GND is 1
channel **+ 1 direct pin** — the direct pins (through 2 A connector contacts) are
never counted, so it prints a false brownout on VTARGET/GND (60 mA < 240 mA) while
hiding the real hazard: a switched VDD on one ~30 mA channel. Real per-subline draw
is already in the DB (`supply_total_ma`/`f4_subline_supply_ma`, F469=290, F429=270).
*Anchor:* `stm32_authority.py` `current_budget()` L706-732; direct-pin nets `build()`
L1322-1334; `_electrical()` L275-279; `RAIL_CONTACT`/`CONNECTOR` L787/L825.
**[med / high]**

**A2. [BUG · VERIFIED] Headless CLI writes files *before* gating and never runs `fabric_drc`**
`main()` calls `write_authority` (L1733) for every package/out dir, then `run_lint`
runs and can only exit 1 *after* files are on disk; `fabric_drc` is never called from
the CLI. The CI/pre-commit/vault-sync path can land drifting or structurally-invalid
authorities the GUI would refuse.
*Anchor:* `stm32_authority.py` `main()` L1729-1743. **[small / high]**

**A3. [BUG · VERIFIED] Dominant-policy DRC drops a real minority rail, gate stays green**
Under `CHANNEL_POLICY='dominant'` each must-switch pin gets one channel to the
highest-count identity; minority-rail parts lose their channel. Evidence
(`minority_identities`, `MINORITY_ROLE_PRESENT`, `conflict_nets`) is computed and
consumed by nothing. Verified: LQFP64 pin 1 is VBAT on 50 parts and **VDD on 3**;
it routes to `VBAT_TGT` only — socket one of the 3 and its core supply ties to VBAT.
*Anchor:* `stm32_authority.py:339` + `stm32_db.py:473`; unconsumed fields
`stm32_authority.py:1368-1370`; enforce in `fabric_drc` L628. **[med / high]**

**A4. [BUG · VERIFIED] VCAP_1 / VCAP_2 / VCAP_DSI collapse onto one net**
`switch_identity()` maps every `vcap` role to `ID_VCAP` → one `VCAP_NODE`. Verified
LQFP100: VCAP1(43), VCAP2(73/76) **and** VCAP_DSI(56) all share it. VCAP_DSI is a
separate 1.2 V DSI-PHY regulator on F469/F479 — closing both switches shorts two
regulator outputs through the socket. Split the nodes, or at minimum a DRC finding
that independent regulator outputs must never be tied.
*Anchor:* `stm32_db.py` `switch_identity()` L442 + `TARGET_NET` L402-406. **[med / high]**

**A5. [WRONG ASSUMPTION] BOOT1/PB2 strap is unmodeled — a firmware-extraction blocker**
Only CubeMX `Type=="Boot"` (BOOT0) is recognized; BOOT1 (alt-function of PB2 on
F1/F2/F3/F4, exposed as bare GPIO — needs a silicon table, not the XML) has zero
awareness. To reach the ROM bootloader you must strap BOOT0=1 **and** BOOT1=0; the
bench can land in Flash on the exact families it covers. Add a per-family boot-strap
model + `SERVICE_BOOT1`.
*Anchor:* `stm32_db.py` `roles()` Boot branch ~L168/L207; `_breakout_map()` ~L454. **[med / high]**

**A6. [BUG · latent, 2-lens] Empty footprint for every unlocked package + no symbol/pad cross-check**
`_KICAD_FOOTPRINT.get(pkg,'')` maps only LQFP64/100, so `to_kicad_symbol` emits an
empty Footprint property (unplaceable symbol) for LQFP48/32/144/176/208, TSSOP20,
UFQFPN*, VFQFPN36. Add `validate_socket_symbol()`: re-parse the emitted symbol,
assert its pin-number set equals the referenced footprint's pad set.
*Anchor:* `stm32_authority.py` `to_kicad_symbol` L1465, `_KICAD_FOOTPRINT` L1448;
`fp_render.py` `_Footprint` L154. **[med / high]**

**A7. [BUG] Unknown/misspelled claim field silently passes the drift gate**
`lint_card` returns `ok=None` for unknown keys; `run_lint` fails only on `ok is False`
— so a typo or a future rollup-key rename turns an assertion into a silent no-op.
Treat unknown non-`pin_*` keys as failures.
*Anchor:* `stm32_authority.py:613-616` + `:1690`. **[small / medium]**

**A8. [TRUST] CubeMX source has no content identity**
Provenance is a directory path + a caller-supplied stamp defaulting to `'1970-01-01'`;
only `classifier_rev` is checked. Two DBs from different CubeMX snapshots produce
identical provenance. Hash the sorted `*.xml` set (or CubeMX version + count + sha256)
into `meta`, expose in the manifest, refuse on source-digest mismatch. Prerequisite
for a trustworthy golden `--check` (C4).
*Anchor:* `stm32_db.py:297` + `:312-313`; manifest `stm32_authority.py:1404-1406`. **[med / high]**

**A9. [BUG · latent] Non-deterministic dominant-rail / role-order tie-break**
`pin_identity_histograms` has no `ORDER BY`, so `max()` ties, `role_set` order, and
`pin_names` sort all break on incidental SQLite row order. No exact ties today (latent),
but the first will silently mis-route. Add `ORDER BY` + a documented secondary key.
*Anchor:* `stm32_db.py:591-601` + `:481-483`. **[small / medium]**

---

## (B) High-value new capabilities

**B1. RDP (readout-protection) model on the extraction paths** — annotate each path
(SWD, boot-UART, USB-DFU) with the RDP level that neutralizes it (L1 blocks SWD flash
read; L2 permanently kills SWD + bootloader RW). Turns a pin list into an extraction
playbook. `_extraction_access()` L486-507. **[small / high]**

**B2. Live buildability verdict at load** — run `fabric_drc` + `current_budget` in
`load()`/`_populate()`, render persistent PASS/FAIL + failing-rule text; click to jump
to the pin/rail. Today DRC runs only at save on the canonical pair; budget is shown
nowhere. Pairs with A1. `stm32_pins_tab.py` L2085/L2123/L1637. **[small / high]**

**B3. Cross-supply power-domain DRC (`power_domain_drc()`)** — encode ST inter-rail
constraints from ranges already in `FAMILY_POWER` (VDDA tracks VDD ~300 mV, VREF+ ≤
VDDA, VBAT=VDD when backup unused, VCAP never externally driven). Emit `{rule,ok,detail}`
like `fabric_drc`. `stm32_authority.py` L628 sibling; ranges L124-143, L267-272. **[med / high]**

**B4. Full card BOM — refdes + MPN + qty** — `card_bom(authority)` unions the passive
guide with components `card_wiring()` already knows: ADG714 cells (`ADG714BRUZ-REEL`,
qty `cells_as_built`), ZIF socket, Samtec edge pair, per-lane 33R resistors. Feeds the
sourcing backlog, which has nothing real to source until this exists. `card_materials`
L513, `socket_connections` L869. **[med / high]**

**B5. Emit a real KiCad netlist for the whole card (`to_kicad_netlist()`)** — standard
`(export (components…)(nets…))` `.net` for socket + every ADG714 cell + edge connector
+ 33R lanes + caps, from data `card_wiring()` already carries. Imports into Pcbnew,
diffs against a hand-drawn schematic, byte-stable for the golden check. `card_wiring`
L909. **[large / high]**

**B6. `resolve_part(conn, mpn)` — single-part view instead of the package union** — emit
the concrete per-MPN pin map: what each pin is, which channels to close, real service
nets, per-pin 5 V tolerance. The union misleads (asserts DFU/boot/5 V-tol for pins that
lack them). DB stores everything per-`mcu_id`; the tool never resolves down. `build()`/
`_breakout_map()` L427-483. **[large / high]**

**B7. Cross-package compare keyed by pin NAME** — `package_compare(a_small, a_big)`:
roles only on the larger part, named pins that move position, extra cells needed,
electrical deltas. `authority_diff` is position-keyed and bails when packages differ, so
it can't answer "LQFP64 or LQFP100 for this firmware?" — the first bring-up decision.
`authority_diff` L744. **[med / high]**

**B8. Library hygiene: symbol pin-numbers vs footprint pad-numbers** — in
`scan_library_grouped`, cross-check the two sets and flag mismatches (LQFP-64 symbol →
44-pad footprint). Both parsers exist in `fp_render`, unused. Catches the classic
wrong-footprint SnapEDA import that passes every current check. `LibraryManager.py`
L860; `fp_render.py` L169/L490. **[med / high]**

**B9. Enrich-from-MPN write-back** (the requested flow) — build the missing property
*writer* (`set_symbol_property`, mirroring `rewrite_symbol_footprint`'s regex-precise
edit), driven by a distributor lookup on the symbol's existing MPN, gated by: fill
blanks only, identity match, `_LIB_LOCK` + `_snapshot_then_write`, and a per-field
dry-run the user confirms. Every safety primitive exists — it needs composing.
`part_identity` L838, `rewrite_symbol_footprint` L582, `_snapshot_then_write` L434. **[large / high]**

---

## (C) Extensibility seams that unlock future work

**C1. One `PACKAGES` config record replacing 8 scattered per-package dicts** — adding a
package today means editing `CHANNEL_POLICY`, `LANE_POLICY`, `ZIF_SOCKET`,
`SOCKET/EDGE/CELL_REFDES`, `SERIES_R_REFDES`, `_KICAD_FOOTPRINT`. Replace with one data
record + an accessor that **raises** on unknown packages instead of silent
`.get(pkg,default)` (the source of A6's empty footprint + wrong-policy defaults already
shipping). Unblocks backlog #4/#5. `CHANNEL_POLICY` L309, dicts L785-801, L1448. **[med / high]**

**C2. Extract the vault-save pipeline into one logic-layer facade** — the only
correct-and-complete pipeline (gate-before-write ×2, snapshot, write N dirs, diff,
dataset page) lives inside the PyQt method `generate_to_vault()`. Move to
`save_authority_set(conn, packages, out_dirs, claim_files) -> SaveResult`; GUI/CLI/tests
become thin callers (fixes A2 for free). `main()` L1698-1746 vs `generate_to_vault()`
L2302-2419. **[med / high]**

**C3. Self-registering export-format registry** — `write_authority()` hardcodes ~9 inline
`write_text` calls plus a hand-maintained `files:[…]` list that drifts. Registry of
`(id, filename, renderer_fn, requires_pyqt)`; derive the file list from it. Each new
format (B4/B5) becomes one registration. `write_authority()` L1592-1626. **[med / high]**

**C4. Golden `--check` mode built on the registry's renderers** (backlog #2 + substance)
— `--check <dir>` re-renders each registered export in memory and diffs vs disk. Driving
it off C3 guarantees checker and writer share the renderer (no second drift-prone
generator); lets B4/B5 land safely. Trustworthy only after A8. **[med / medium]**

**C5. Lift the Connector Contract / rail map into one validated data artifact** — the
frozen "Rev B" contract is transcribed across `TARGET_NET`, `RAIL_CONTACT`,
`SERVICE_CONTACT`, `CORESIGHT20`, `ADG714_BUS`, `_POWER_NETS`, `_NET_CATEGORY` that must
agree by hand. Move to one JSON/YAML + validator (a net in `RAIL_CONTACT` missing from
`_NET_CATEGORY` = load-time error). Larger lift, lower certainty than C1. **[large / medium]**

**C6. Promote `.trash` into a real history + restore model** — `_snapshot_then_write`
copies before destructive rewrites but there is **no restore code**; "one copy-back" is
a manual instruction. Add `list_snapshots()`/`restore()`/retention; extend to
footprint/model deletes and (via C2) authority overwrites. `LibraryManager.py` L434-447. **[med / medium]**

---

## (D) Nice-to-haves

- **D1. Semantic duplicate detection** beyond exact-name dedupe — group by
  `part_identity` (MPN/mfr). `LibraryManager.py` L477/L838. **[med / medium]**
- **D2. Library search indexes the identity it already scans** (mpn/mfr/datasheet/
  description — "STMicroelectronics" returns nothing today). **[small / medium]**
- **D3. Filtered table export** — `_export` ignores the live filter; pass the visible
  row set. `stm32_pins_tab.py` L2233-2261. **[small / medium]**
- **D4. Persist the semantic diff as an append-only vault CHANGELOG** — route-move lines
  are computed then discarded. `stm32_pins_tab.py` L2373-2400. **[small / medium]**
- **D5. One-click library health report** — aggregate the has_symbol/footprint/model/
  dangling flags into a shareable markdown rollup. **[small / medium]**
- **D6. Distinguish HSE crystal vs bypass + provision the parts** — add an HSE mode flag
  and the missing crystal/load-cap line items. `stm32_db.py` L211-217. **[small / medium]**

---

## If you do only three things

1. **Fix `current_budget()` (A1)** — the only power-integrity check, four lenses
   condemned it, prints a false brownout while hiding the real hazard. [VERIFIED]
2. **Fix the CLI write-then-gate ordering + add `fabric_drc` (A2)** — small change that
   stops CI/vault-sync from committing invalid authorities. [VERIFIED]
3. **Close the two green-gate DRC holes (A3 minority-rail + A4 VCAP)** — both mis-route
   or short supported parts while the gate reports success; A3's evidence is already
   computed and thrown away. [VERIFIED]

*Fourth slot:* the **`PACKAGES` config record (C1)** — turns "add a package" into one
validated record and removes the silent empty-footprint/wrong-policy defaults already
shipping for the unlocked packages.
