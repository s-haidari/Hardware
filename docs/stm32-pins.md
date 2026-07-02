# STM32 Pins — CubeMX database + pinout authority (spec)

Date: 2026-07-01 · lives in the Hardware repo · requirements authority is the vault's
`Brain/Wiki/Specs/Pinout Authority Generator.md` (draft, 2026-06-30). This doc records the
app-integration design; the vault spec defines the data contract.

## Purpose
Build the STM32 pin database **from scratch** from the CubeMX MCU XML, so the plug-in-card
**switch fabric, breakouts, and ERC are DERIVED, never hand-authored** — killing the drift-bug
class (SWCLK pin 79-vs-76, ADG714 6-vs-8, "pin 90 BOOT-that-isn't"). Per target-socket pin the DB
decides: role-stable across the whole STM32F family → hardwire direct; role-variable → route
through an ADG714 switch channel. That yields the ADG714 cell count, per-cell switch map, per-card
BOM, and the extraction-access overlay (debug/JTAG/trace/boot/VCAP taps).

## Scope (locked, per vault 2026-06-30)
STM32F only, **LQFP64 (53 parts)** + **LQFP100 (60 parts)** for Rev A. Core is family-/package-
agnostic (`supported_families` in the manifest; alphanumeric positions; extensible enums) so H7/BGA
is a manifest edit later, not a rewrite. Source XML = 424 STM32F MCUs.

## Decisions (with Sadad, 2026-07-01)
- **Rewrite inline** in new self-contained `tools/` modules (stdlib + PyQt5), **no `app/backend`/hwkit
  dependency**. hwkit is the reference during the rewrite, then deleted.
- **Full decision-matrix viewer** (a new `STM32 Pins` nav tab) — beyond the vault's minimal headless
  generator; reconcile the vault spec after.
- **Ask-each-time** output (folder picker on Generate).
- **Full field set now**, including fields not in CubeMX (see Phase 2 sources).

## Architecture
New nav tab **`STM32 Pins`** (third, after KICAD Manager / KICAD Tools). Modules:
- `tools/stm32_db.py` — CubeMX XML parse → classify → SQLite build + the switch engine (data-only,
  stdlib). Self-contained rewrite of hwkit `cubemx/*` + `pins/switch_engine.py`.
- `tools/stm32_authority.py` — Layer-B authority + full-spec fields + TSV/YAML/JSON writers.
- `tools/stm32_pins_tab.py` — the Qt tab (Build DB, Generate, decision-matrix viewer, rollup).

## Two-layer schema (per vault spec)
**Layer A — raw, one row per (part, package, position)** → SQLite + `pins_<PKG>.tsv`.
Tables: `mcu`, `mcu_package_pin` (physical_pin_number, canonical/raw name, pin_type,
electrical_class ∈ io/power/ground/reset/boot/oscillator/vcap/nc, gpio_port/index, lqfp_side),
`pin_function` (per `<Signal>`), `pin_role` (derived role_name/role_class).

**Layer B — derived, one row per (package, position)** → `pinout_authority_<PKG>.{yaml,json}` (the
authority). Per position: `pin_names {name: part_count}`, `role_set {identity: part_count}`,
`switch_class` (fixed / must_switch / osc_optional), `assignment` (`{direct,net}` or
`{switched, adg714:{cell,channel,destination}}`), extraction `tags`, `electrical`, `bootloader_periph`,
`conflict_nets`, `variant_note`, `is_fixed`. Plus per-package `rollup` (switched/must_switch/incl_osc/
fixed counts, channel_count, `cells_min = ceil(switched/8)`, `cells_as_built = ceil(channels/8)`) and
`manifest` (part_count, supported_parts, supported_families, DB origin/rev).

## The switch rule (verified)
Fold each pin's roles into 10 identities (VDD/VDDA/VREF/VBAT/VSS/VCAP/BOOT/NRST/OSC/IO). A pin needs a
switch when it carries **≥2 distinct identities** (never a dominant-role shortcut). `must_switch` if a
non-IO identity is a rail/ground/VCAP/BOOT/NRST; `osc_optional` if the only non-IO is OSC. Minority
roles counted (strict-safe) but flagged. Deterministic ADG714 assignment: switched positions sorted
ascending, `cell=floor(i/8)+1`, `channel=i%8+1`, `destination = primary target net`.

**Proof obligations (regression tests):** 424 STM32F MCUs; LQFP64=53 / LQFP100=60 parts; LQFP64
must-switch = **`[1,13,17,18,19,30,31,33,47,48,60]` (11 → 2 ADG714 cells)**; LQFP100 must-switch = 43;
pin 100 (VDD) never switched. Extraction tags: pin 60 is_boot, pin 13 is_analog_supply, pin 5 is_clock,
pin 46 is_debug (SWDIO).

## Extraction-access breakout (Layer B, orthogonal to switching)
Added 2026-07-01 (with Sadad). The parent board breaks out the socketed target's debug/service
functions so the controller and a bench probe can attach for firmware extraction. This is a **second,
independent per-position axis**: *switching* asks "does the routing role collide across the family? →
ADG714 cell"; *breakout* asks "can this socket pin be debug/bootloader/trace on **any** supported part?
→ route it to a frozen parent-board service net." A pin can be switched, broken-out, both, or neither
(e.g. LQFP100 PA14/SWCLK is fixed-role SWCLK on most parts but VCAP2 on F469/F479 → both switched *and*
broken out). Confirmed orthogonal in the vault: `7G` line 64-70, `Connector Contract` Rev B, Card `5E`.

**Derived from raw signals, not the switch identities.** `stm32_authority._breakout_map` reads the
per-position union of CubeMX `<Signal>` names + raw pin names already in the DB. The switch engine
(`stm32_db`) is **untouched**, so the verified 11/43 counts provably cannot move (regression-tested by
`test_switch_counts_unchanged_by_breakout`).

**Signal → function → frozen parent net** (Connector Contract Rev B + Card 5E; debug port is fixed
silicon PA13/PA14/PB3/PA15/PB4):

| Signal (any part) | Function | Parent net | CoreSight-20 pin |
|---|---|---|---|
| SWDIO / JTMS | SWD data | `SWDIO_PARENT` | 2 |
| SWCLK / JTCK | SWD clock | `SWCLK_PARENT` | 4 |
| JTDO / TRACESWO / SWO | SWO / TDO | `SWO_PARENT` | 6 |
| JTDI | JTAG TDI | `TDI_PARENT` (contact 35, PA15) | 8 |
| NJTRST / JTRST | JTAG nTRST | `NTRST_PARENT` (contact 37, PB4) | 14 |
| reset pin | NRST | `SERVICE_NRST` | 10 |
| boot pin | BOOT0 | `SERVICE_BOOT0` | — |
| OSC_IN / OSC_OUT | HSE osc | `SERVICE_OSC_IN` / `_OUT` | — |
| PA9 USART1_TX / PA10 USART1_RX | boot UART (TX↔RX crossover) | `UART_BOOT_RX` / `UART_BOOT_TX` | — |
| PA12 / PA11 USB | USB-DFU | `USB_DP_TGT` / `USB_DN_TGT` | — |
| TRACECLK / TRACED0-3 | parallel trace | reserved (No-Connect per 5E) | 11/12/13/16 NC |

Per position the authority emits a **`breakout`** block: `service_nets`, human `functions`, `via`
(`adg714_source` if switched else `fixed_direct` — tells the 7G header where to tap), `coresight20_pins`,
`trace`. The package `rollup` gains **`extraction_access`**: the full CoreSight-20 header resolved to
target sockets, the boot-UART / USB-DFU positions, and debug/trace/service-breakout counts. The
`STM32 Pins` tab shows a violet **Breakout** column and `breakout N (debug D, trace T)` in the rollup.

**Ground truth (tested):** LQFP64 SWDIO/SWCLK/SWO/TDI/nTRST = sockets **46/49/55/50/56** (Card 7B + 5E);
LQFP100 TDI/nTRST = **77/90** (5E); CoreSight-20 pin 2→46, pin 8→50, pin 14→56, pin 10→NRST 7; boot UART
PA9→`UART_BOOT_RX`, PA10→`UART_BOOT_TX`. `VSSA`→`VSSA_TGT`; parallel trace detected (`SYS_TRACED*`,
including the port-C remap present on LQFP64) and left reserved No-Connect. LQFP100 correctly flags the
F469/F479 pin-shift (debug-capable at both the base and shifted sockets).

## Reference enrichment (from the DB, no fetch)
Each position also carries **`peripherals`** — the sorted distinct peripheral-instance roots available
at that socket across the whole family (e.g. `SPI1`, `TIM4`, `USART3`, `I2S1`, `ADC1`, `OTG`, `FMC`),
derived from every CubeMX `<Signal>`. Extra extraction tags: **`is_wakeup`** (WKUP pins — reset/glitch
entry) and **`is_usb`** (USB DP/DM). The tab's Search filters on peripherals too.

## I/O electrical (fetched 2026-07-01)
Per-family I/O limits pulled from the **official ST datasheets** (from st.com, saved to the vault
`Sources/Datasheets/`, PDFs verified `%PDF` + rev; Hard Rule 10). Encoded in
`stm32_authority.FAMILY_ELECTRICAL`; `build()["electrical"]` aggregates them per package (widest VDD/VDDA,
per-family total-I/O, uniform per-pin limits) alongside the CubeMX per-part VDD range. **Closes open question #1.**

| Family | I_IO/pin | ΣI_IO total | I_INJ/pin | VDD (V) | VDDA (V) | 5V-tol | Datasheet (cited) |
|---|---|---|---|---|---|---|---|
| F0 | ±25 mA | ±80 mA | ±5 mA | 2.0–3.6 | 2.4–3.6 | yes | DS9826 R6 §6.2 Table 22 p.52 |
| F1 | ±25 mA | ±150 mA | ±5 mA | 2.0–3.6 | 2.0–3.6 | yes | DS5319 R20 §5.2 Table 7 p.37 |
| F2 | ±25 mA | ±120 mA | ±5 mA | 1.8–3.6 | 1.8–3.6 | yes | DS6329 R18 §6.2 Table 12 p.70 |
| F3 | ±25 mA | ±80 mA | ±5 mA | 2.0–3.6 | 2.0–3.6 | yes | DocID026415 R5 §6.2 Table 17 p.71 |
| F4 | ±25 mA | ±240 mA † | ±5 mA | 1.8–3.6 | 1.8–3.6 | yes | DS8626/DocID022152 R5 §5.2 Table 12 p.78 |
| F7 | ±25 mA | ±120 mA | ±5 mA | 1.7–3.6 ‡ | 1.7–3.6 | yes | DS10916 R5 §6.2 Table 16 p.121 |

Per-pin I_IO (±25 mA) and ΣI_INJ (±25 mA total) are uniform across F0–F7; each value was cross-checked against
a second mention in the same datasheet (no disagreements). Temp = 6-suffix ambient −40..+85 °C (7-suffix →
+105 °C). "5V-tol" = the family has FT pins (V_IN up to 5.5 V; FT pins accept only −5/+0 mA injection, no
positive injection). VDDA lower bound rises to 2.4 V when the ADC/DAC runs at full rate (F0/F2/F3).
† **F4 total is sub-line dependent**: F405/407 I_VDD/I_VSS = 240 mA; F427/429 (DS9405 R5) = 270 mA with an
explicit ΣI_IO = ±120 mA; F401/F411 lower (~150 mA) — **UNVERIFIED, flagged not asserted** (Hard Rule 10).
‡ F7 VDD min 1.7 V requires an external supply supervisor (DS10916 R5 Table 18 note).

**Provenance:** st.com (Akamai) required a full browser header set + `--compressed` for five families. For F4,
st.com was unreachable from the fetch environment, so a **byte-identical** official ST PDF (Author
STMICROELECTRONICS, DocID022152 Rev 5, 200 pp) was pulled from a component-search mirror and verified. Saved,
`%PDF`-validated PDFs (1.9–5.7 MB each): `ST STM32F072 / F103 / F207 / F303 / F407 / F746 datasheet.pdf`.

### Per-pin 5V-tolerance (fetched 2026-07-01)
Every GPIO's 5V-tolerance read from each datasheet's "Pin definitions" I/O-structure column (FT/FTf =
5V-tolerant, TTa/TC = 3.3V-only), exhaustively classified per family (100% coverage; F4 cross-checked
against its cover-page "up to 138 5V-tolerant I/Os"). Encoded as `stm32_authority.FAMILY_NOT_5V` (the
3.3V-only GPIO set per family; every other GPIO is structurally FT). Each position gets a `five_v` block:
`tolerant` (conservative — 5V-safe on **all** supported parts at that socket), `by_family` (the per-family
FT verdict), and `caveat` (`osc-mode` for PC14/15/PH0/1; `analog-mode` for FT pins while ADC-sampling). Tag
`is_5v_tolerant` mirrors `tolerant`; the rollup counts all-parts / part-dependent / never.

Key finding: **5V-tolerance is part-dependent** at many sockets. The analog pins (PA0–7, PB0/1, PC0–5) are
FT on F2/F4/F7 but 3.3V-only on F0/F1/F3, so e.g. the PA0 socket is 5V-safe under an F4 but not an F1 —
`by_family` carries this. Only **PA4/PA5** (DAC/TTa) are never 5V-tolerant on any F-family.

| Family | 3.3V-only GPIOs (NOT 5V-tolerant) | Source |
|---|---|---|
| F0 | PA0–7, PB0/1, PC0–5, PC13–15 (19) | DS9826 R6 Table 14 §5 pp.36–43 |
| F1 | above + PB5 (20) | DS5319 R20 Table 5 §3 pp.28–33 |
| F2 | PA4, PA5 (2) | DS6329 R18 Table 8 §4 pp.46–56 |
| F3 | 45 (adds PB2, PB10–15, PD8–15, PE7–15, PF2/4) | DocID026415 R5 Table 13 §4 pp.41–52 |
| F4 | PA4, PA5 (2) | DS8626/DocID022152 R5 Table 7 §3 pp.46–58 |
| F7 | PA4, PA5 (2) | DS10916 R5 Table 10 §4 pp.55–74 |

FT pins lose 5V tolerance while in analog (ADC) or oscillator mode (PC14/15/PH0/1) per each datasheet's
I/O-structure footnote. Non-GPIO pins (power/ground/reset/boot) are not classified (`five_v = null`).

### Power / decoupling + reconciled electrical (2026-07-02)
**`FAMILY_POWER`** (per family, cited) drives the card's passive BOM: external **VCAP** need (F2/F4/F7 =
2×2.2 µF on VCAP_1/2, ESR<2 Ω; F0/F1/F3 = none), **VBAT** and **VREF+** ranges, the **decoupling recipe**
(100 nF per VDD/VSS pair + 4.7 µF bulk; VDDA 10–100 nF+1 µF; VREF cap), and the LQFP100 VDD/VSS pin counts.
Surfaced in `build()["electrical"]` as `power`, `vcap_required`, `vbat_range_v`, `vref_range_v`.

**Electrical metric reconciled:** `total_io_ma` is now unambiguously the **ΣI_IO** (sum of all I/O pins)
where the datasheet states it (`metric=sigma_io`), else the device **I_VDD/I_VSS supply** total
(`metric=supply_total`), with `supply_total_ma` carried separately. This fixed F4 (ΣI_IO = **120 mA**; the
old 240 was the F405/407 *supply* total). Verified **F4 sub-line supply totals** (`F4_SUBLINE_SUPPLY_MA`):
F401/F411 = 160, F405/407 = 240, F429 = 270, F446 = 240, F469 = 290 — **retiring the "F401/F411 ~150
UNVERIFIED" flag** (that open question is now closed; each F4 sub-line was fetched + cited).

**Bootloader map** rebuilt from the **exhaustive AN2606 Rev 62** transcription (225 device/peripheral/
pin-option rows): adds F1 CAN2 PB5/PB6 + PA9 VBUS-sense, F3 I2C3 (PA8/PB5), F4 SPI1-4 + I2C4, and F7's
**both** CAN1 (PD0/PD1) and CAN2 (PB5/PB13). Sharpens the per-position `bootloader_periph` tags.

### Card materials + drift-gate (Phase B, 2026-07-02)
`build()` now emits **`card_materials`** — the plug-in card's passive BOM derived from the switch rollup +
FAMILY_POWER: ADG714 count, VCAP caps (2×2.2 µF, populated for F2/F4/F7 sockets), 100 nF decoupling (one per
VDD pair, worst-cased across families), 4.7 µF bulk, VDDA/VREF caps. **`lint_card(authority, claims)`** is
the drift-gate: pass a Build Card's asserted numbers (`must_switch_count`, `adg714_cells`, `swclk_pos`, …)
and it returns ok/mismatch per field against the authority — catching the exact SWCLK-76-vs-49 /
ADG714-8-vs-2 drift this generator exists to kill.

## Full-spec field sources (Phase 2)
- **electrical**: VDD/VDDA range from the CubeMX `<Voltage Max Min>` element (MCU-level, aggregated);
  per-pin `max_io_current_ma` = per-family datasheet constant (small cited table).
- **bootloader_periph** (UART/USB-DFU/SPI/I2C/CAN): NOT in CubeMX → fetch **ST AN2606**, save to the
  vault `Sources/Datasheets/`, build the per-family bootloader-pin map (vault Hard Rule 10).
- **destination-net dictionary + conflict_nets**: consolidate from vault `Connector Contract` /
  `Net Naming Contract` (the `CARD_LANE_xxx` / target-net vocabulary).
- Residual unknowns → the repo spec's Open Questions, not guessed.

## Do NOT (from the vault, honored)
No dominant-role/"role %" collapsing; no 150-column monolithic matrix as the source of truth; no
codenames (VICTIM/HELIOS/DAEMON → target/controller/`U_CTRL`); no KiCad netlist emission or touching
the real NETDECK project (vault-data-only). The viewer is a read-out over the derived authority, not a
new source of truth.

## Phasing
1. **DB core** — `stm32_db.py`: parse+classify+build+switch engine; Build button + status; proof test
   (LQFP64 = the 11).
2. **Authority + full fields** — `stm32_authority.py`: Layer-B + electrical + bootloader (AN2606) +
   net dictionary + TSV/YAML/JSON export.
3. **Viewer tab** — `stm32_pins_tab.py`: the per-position decision matrix + rollup + filters.
4. **Extraction-access breakout** (2026-07-01) — the orthogonal debug/bootloader/trace breakout layer
   in `stm32_authority.py` (per-position `breakout` + `extraction_access` rollup + CoreSight-20/7G),
   the tab's Breakout column, and ground-truth tests. See the section above.

## Repo cleanup (tied to this work)
Done: dropped `app/frontend` (web UI), `app/backend/stm32switch` (old generator), `tools/build`,
`misc/` junk, logs; fixed `build-exe.yml` (Lucide + QtSvg). After the rewrite (done): the CubeMX XML
moved to `tools/cubemx_db/mcu` and **all of `app/` was deleted** (hwkit/tests/api + the abandoned
web/rebuild docs) — the app is now fully self-contained under `tools/`. The built `stm32.sqlite` is
gitignored.

## Data sources gathered
- **bootloader_periph** — ST **AN2606 Rev 62 (Mar 2024)**, system-memory-boot-mode tables; PDF saved
  to the vault `Sources/Datasheets/ST AN2606 STM32 system memory boot mode.pdf`. Encoded per family as
  the union of ROM-bootloader pins (`tools/stm32_authority.py::BOOTLOADER_PINS`). USART1=PA9/PA10 and
  USB-DFU=PA11/PA12 universal; F0/F3 have no CAN/SPI bootloader; F2 no I2C/SPI; F1 CAN2 TX=PB6, F7 uses
  CAN1 on PD0/PD1, F7 I2C1_SDA=PB9.
- **net dictionary** — confirmed from the vault Connector Contract / Naming Conventions: VDD→VTARGET,
  VDDA→VDDA_TGT, VREF→VREF_TGT, VBAT→VBAT_TGT, VSS→GND, VCAP→VCAP_NODE, BOOT→SERVICE_BOOT0,
  NRST→SERVICE_NRST, IO→CARD_LANE_<pin>.

## Open questions / flags
1. **max_io_current_ma → RESOLVED (2026-07-01).** All six ST datasheets fetched, saved, and cited — per-pin
   I_IO = ±25 mA (uniform F0–F7), plus per-family ΣI_IO, injection, VDDA, temp, and 5V-tolerance. See the
   "I/O electrical (fetched)" section. Residual: F4 total-I/O varies by sub-line (F401/F411 ~150 mA UNVERIFIED).
2. **SWD folds into the IO identity for switching — RESOLVED (Sadad, 2026-07-01).** This is correct:
   SWD/SPI/I2C/UART are all one generic card lane for the *switch* decision, so folding them reproduces
   the verified counts (11/43); promoting SWD to a distinct switch identity would wrongly inflate them.
   Debug access is a *breakout* concern, not a switch identity — handled by the orthogonal breakout
   layer above (`SWDIO_PARENT`/`SWCLK_PARENT`/`SWO_PARENT`/`TDI_PARENT`/`NTRST_PARENT`), which leaves the
   switch counts untouched.
3. **VSSA → RESOLVED.** Analog ground now relabels to `VSSA_TGT` (Connector Contract Rev B contact 24)
   in the authority. Destination-label only: the switch identity is unchanged, so counts are unaffected.
4. **OSC IN/OUT → RESOLVED.** The breakout layer splits `SERVICE_OSC_IN` / `SERVICE_OSC_OUT`. The
   switch-engine destination label for OSC pins stays advisory (they are `osc_optional`, per-card).
5. **CARD_LANE numbering = socket-pin number** (`CARD_LANE_NNN = pin NNN`), per the vault lane matrix.
   Card 7B's sequential 001..011 numbering is the hand-authoring drift this generator replaces.
6. **AN2606 UNVERIFIED** (per the fetch): Rev 70 not line-diffed vs Rev 62 (F0–F7 believed unchanged);
   smaller F0 sub-lines, the full F3 device matrix, and per-device F4/F7 SPI/I2C instance pins not
   exhaustively transcribed — the encoded union covers the LQFP64/LQFP100-relevant pins.
- The `bootloader_periph` per position is the **union** across the families/sub-lines present at that
  socket position (a pin can serve different bootloader buses on different parts).
