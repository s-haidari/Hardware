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

## Repo cleanup (tied to this work)
Done: dropped `app/frontend` (web UI), `app/backend/stm32switch` (old generator), `tools/build`,
`misc/` junk, logs; fixed `build-exe.yml` (Lucide + QtSvg). After the rewrite: delete
`app/backend/hwkit` + `tests` + `api` (unused by the app/CI), keep the CubeMX XML data source
(`app/backend/cubemx_db/mcu`, or relocate under `tools/`).
