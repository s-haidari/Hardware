"""Settings — appearance, paths, sourcing status, library location. Pinned to nav.

SP1: the Mouser API-key field is gone (the app ships a baked key); Sourcing shows
a status line, and a Library Location row lets the user point the app at (or seed)
a writable library folder. Paths are read-mostly, derived from that location.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
                             QFileDialog)

from .. import theme as T
from .. import widgets as W
from .. import feature as F
from .. import units as U
from .. import kit
from ..util import fmt_countdown, LogSink, run_populate, clear_layout


def _setting_row(title, sub, control=None) -> W.Card:
    """A titled card row. `control` is an optional right-aligned widget (a segment,
    button, or status tag); status-only rows pass None and get no trailing widget."""
    card = W.Card(pad=16)
    row = QHBoxLayout(); row.setSpacing(16)
    col = QVBoxLayout(); col.setSpacing(2)
    t = QLabel(title); t.setFont(T.ui_font(10, semibold=True))
    W.register_restyle(lambda: t.setStyleSheet(f"color:{T.t('txt1')};background:transparent;"), t)
    col.addWidget(t)
    col.addWidget(W.body(sub, dim=True))
    row.addLayout(col); row.addStretch(1)
    if control is not None:
        row.addWidget(control)
    card.body.addLayout(row)
    return card


def _current_location() -> Path:
    import LibraryManager as LM
    return LM.library_location() or LM.REPO_ROOT


def _open_folder():
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(_current_location())))


def _relaunch():
    """Re-exec the running app so a new library location / snapshot loads cleanly.

    Startup is what rebinds the path globals and rebuilds every feature page from
    the new location, so a full re-exec is the reliable way to swap libraries live
    without leaving stale panes behind. Returns True if the re-exec was launched.
    """
    from PyQt5.QtWidgets import QApplication
    QApplication.quit()
    import os
    os.execv(sys.executable, [sys.executable, *sys.argv])
    return True                                        # pragma: no cover (execv replaces us)


def _offer_relaunch(parent, title, body_text) -> None:
    """Confirm a change with an actionable "Relaunch Now" instead of a passive
    "please restart." Choosing Relaunch re-execs; Later keeps working on the old
    library until the next launch."""
    from ..util import _headless
    if _headless():
        return
    win = parent.window() if parent is not None else None
    if win is None:                              # no app window (rare) → native fallback
        box = QMessageBox(parent)
        box.setWindowTitle(title); box.setText(body_text)
        relaunch_btn = box.addButton("Relaunch Now", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is relaunch_btn:
            _relaunch()
        return
    if kit.confirm_overlay(win, title, body_text,
                           confirm_label="Relaunch Now", cancel_label="Later"):
        _relaunch()


def _change_location(parent=None):
    import LibraryManager as LM
    chosen = LM._prompt_choose_location(parent)
    if chosen is None:
        return
    LM.write_pointer(chosen)
    LM.apply_library_location(chosen)
    _offer_relaunch(parent, "Library Location",
                    f"Library location set to:\n{chosen}\n\n"
                    "Relaunch now to load it.")


def _reset_snapshot(parent=None):
    import LibraryManager as LM
    loc = _current_location()
    from ..util import confirm
    if not confirm(parent, "Reset to Bundled Snapshot",
                   f"This overwrites the library in\n{loc}\nwith the bundled snapshot. Continue?"):
        return
    LM.seed_library(loc, force=True)
    _offer_relaunch(parent, "Reset to Bundled Snapshot",
                    "Library reset from the bundled snapshot.\n\n"
                    "Relaunch now to reload it.")


def _machine_setup(ctx, root, lay) -> None:
    """The one machine-readiness object: a live verdict grid (KiCad toolchain, config,
    library location, sourcing, STM32 DB, version) + ▶ Set Up This Machine (register the
    app's libraries into KiCad) + ▶ Rebuild STM32 Database. Ports bare's Machine Setup
    group onto the styled panel; every capability is read LIVE and the card rebuilds after
    any action so it never goes stale."""
    import LibraryManager as LM
    import kicad_paths as KP
    import stm32_db

    lay.addWidget(W.eyebrow("Machine Setup"))
    holder = QVBoxLayout(); holder.setSpacing(10)
    lay.addLayout(holder)

    def build_card():
        clear_layout(holder)
        cfg = LM.load_config()                     # LIVE config (also refreshes after any change)
        kbin = KP.find_kicad_bin()
        kcli = KP.find_kicad_cli()
        kconf = LM.find_kicad_config_dir()
        loc = LM.library_location() or LM.REPO_ROOT
        loc_ok = LM._can_write_dir(loc)
        # Sourcing rollup (surfaces providers_from_config); the per-distributor detail lives
        # in the dedicated Sourcing section below, so this stays a single readiness summary.
        sourcing_ok = LM.providers_from_config(cfg) is not None
        n = stm32_db.package_count()
        rows = [
            ("KiCad Binary", bool(kbin), kbin.as_posix() if kbin else "Not found"),
            ("kicad-cli", bool(kcli), kcli or "Not found (ERC/DRC/exports need it)"),
            ("KiCad Config", kconf is not None,
             kconf.name if kconf else "Not found (Set Up registers libraries here)"),
            ("Library Location", loc_ok, loc.as_posix() + ("" if loc_ok else "  (not writable)")),
            ("Sourcing", sourcing_ok, "Ready" if sourcing_ok else "No providers configured"),
            ("STM32 Database", bool(n), f"{n} packages" if n else "Not built"),
            ("Version", True, str(LM.APP_VERSION)),
        ]
        gaps = [label for label, ok, _ in rows if not ok]
        # Quiet Settings idiom (design-rules §4): a dot+text status tag + a plain summary
        # line — NOT a filled Verdict band; the Settings panel carries no surface-hue chips.
        head = QWidget(); hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(10)
        hl.addWidget(W.tag("Machine Ready", "ok") if not gaps
                     else W.tag(f"{len(gaps)} Setup Item{'s' if len(gaps) != 1 else ''} To Address", "warn"))
        if gaps:
            hl.addWidget(W.body("Address: " + ", ".join(gaps), dim=True))
        hl.addStretch(1)
        holder.addWidget(head)
        card = W.Card(pad=16)
        card.body.addWidget(W.dl(
            [(label, W.hstack(W.tag("OK" if ok else "Check", "ok" if ok else "warn"),
                              W.body(val, dim=True), spacing=8))
             for label, ok, val in rows], key_width=170))
        holder.addWidget(card)

    build_card()

    actions = QHBoxLayout(); actions.setSpacing(8)
    b_setup = W.btn("Set Up This Machine", "primary",
                    "Register the KiCad libraries and report everything this machine still needs")
    b_db = W.btn("Rebuild STM32 Database", "ghost",
                 "Rebuild the MCU database from a CubeMX source (needs STM32_CUBEMX)")
    b_refresh = W.btn("Refresh Status", "ghost", "Re-read the whole setup status now")
    actions.addWidget(b_setup); actions.addWidget(b_db); actions.addWidget(b_refresh)
    actions.addStretch(1)
    lay.addLayout(actions)

    def do_setup():
        def job():
            cfg = LM.load_config()
            reg = LM.register_libraries(cfg, LogSink(ctx.services))
            loc = LM.library_location() or LM.REPO_ROOT
            done, missing = [], []
            if reg.get("ok"):
                done.append(reg.get("message") or "KiCad libraries registered.")
            else:
                missing.append({"item": "KiCad library registration", "why": reg.get("message", ""),
                                "how_to_fix": "Open KiCad once so it creates its config, then re-run."})
            (done if KP.find_kicad_bin() else missing).append(
                f"KiCad binary: {KP.find_kicad_bin().as_posix()}" if KP.find_kicad_bin() else
                {"item": "KiCad toolchain", "why": "KiCad binary not found",
                 "how_to_fix": "Install KiCad or add it to PATH."})
            if LM._can_write_dir(loc):
                done.append(f"Library location writable: {loc.as_posix()}")
            else:
                missing.append({"item": "Library location", "why": f"{loc.as_posix()} not writable",
                                "how_to_fix": "Change Location to a writable folder (below)."})
            if stm32_db.package_count():
                done.append(f"STM32 DB: {stm32_db.package_count()} packages")
            else:
                missing.append({"item": "STM32 database", "why": "not built",
                                "how_to_fix": "Rebuild STM32 Database (above)."})
            return {"summary": f"Setup checked: {len(done)} OK, {len(missing)} to address.",
                    "done": [d for d in done if isinstance(d, str)],
                    "missing": [m for m in missing if isinstance(m, dict)]}

        def after(res, ok):
            kit._report(root, "Set Up This Machine", res, log=ctx.services.log)
            build_card()

        run_populate(ctx, job, after, busy="Setting up this machine...")

    def do_db():
        def job():
            src = stm32_db.default_cubemx_source()
            if not src:
                return {"summary": "Cannot build the STM32 database.",
                        "missing": [{"item": "CubeMX source",
                                     "why": "no CubeMX MCU-XML directory detected",
                                     "how_to_fix": "Point STM32_CUBEMX at your CubeMX install "
                                                   "(db/mcu), then retry."}]}
            res = stm32_db.build_database(src, stm32_db.default_db_path(),
                                          progress=lambda m: ctx.services.log(f"  {m}"))
            return {"summary": f"Built STM32 DB: {getattr(res, 'mcus', '?')} MCUs, "
                               f"{getattr(res, 'packages', '?')} packages.",
                    "done": [f"database at {stm32_db.default_db_path().as_posix()}"]}

        def after(res, ok):
            kit._report(root, "Rebuild STM32 Database", res, log=ctx.services.log)
            build_card()

        run_populate(ctx, job, after, busy="Rebuilding the STM32 database...")

    b_setup.clicked.connect(do_setup)
    b_db.clicked.connect(do_db)
    b_refresh.clicked.connect(build_card)


def _settings_panel(ctx, _s) -> QWidget:
    import LibraryManager as LM
    import stm32_db

    root = QWidget()
    lay = kit.page_layout(root)
    lay.setAlignment(Qt.AlignTop)

    _machine_setup(ctx, root, lay)

    lay.addWidget(W.eyebrow("Appearance"))
    opts = ["Dark", "Light", "System"]
    mode = LM.read_setting("Theme", "Dark")
    sel = opts.index(mode) if mode in opts else 0
    theme_seg = W.Segmented(opts, on_change=lambda m: ctx.bus.emit("theme.set_mode", m),
                            selected=sel, tip="Application theme")
    # Re-sync the segment when the theme is changed elsewhere (e.g. the nav-rail
    # toggle) so it never goes stale — mirrors "units.changed" below (finding settings:94).
    ctx.bus.on("theme.changed", lambda m: theme_seg.select_value(m))
    lay.addWidget(_setting_row("Theme", "Dark, light, or follow Windows", theme_seg))

    # Length units — one app-wide preference (WS-A / PCB-02 / LIB-14 / SHELL-03).
    # Drives the "units.set_mode" command; the shell persists + broadcasts. Kept in
    # sync if the choice is changed elsewhere (e.g. the PCB Setup toggle) while alive.
    uopts = [U.MM, U.MILS]
    umode = LM.read_setting("Units", U.MM)
    usel = uopts.index(umode) if umode in uopts else 0
    units_seg = W.Segmented(uopts, on_change=lambda m: ctx.bus.emit("units.set_mode", m),
                            selected=usel, tip="Length units used across the whole app")
    ctx.bus.on("units.changed", lambda m: units_seg.select_value(m))
    lay.addWidget(_setting_row(
        "Length Units",
        "Millimetres or mils, applied app-wide (net classes, board setup, previews)",
        units_seg))

    # --- Library location (writable; the app manages everything under it) ---
    lay.addWidget(W.eyebrow("Library Location"))
    loc = _current_location()
    loc_card = W.Card(pad=16)
    loc_col = QVBoxLayout(); loc_col.setSpacing(8)
    loc_col.addWidget(W.body(loc.as_posix(), mono=True, dim=True))
    actions = QHBoxLayout(); actions.setSpacing(8)
    actions.addWidget(W.btn("Change", tip="Open an existing library or seed a new one",
                            on_click=lambda: _change_location(root)))
    actions.addWidget(W.btn("Open Folder", tip="Reveal the library folder",
                            on_click=_open_folder))
    actions.addWidget(W.btn("Reset to Bundled Snapshot", tip="Overwrite with the bundled snapshot",
                            on_click=lambda: _reset_snapshot(root)))
    actions.addStretch(1)
    loc_col.addLayout(actions)
    loc_card.body.addLayout(loc_col)
    lay.addWidget(loc_card)

    # --- Derived paths (read-mostly) ---
    lay.addWidget(W.eyebrow("Paths"))
    cfg = ctx.cfg or {}
    pairs = []
    for label, key in (("Repo Root", "RepoRoot"), ("Symbol Library", "SymbolLib"),
                       ("Footprint Library", "FootprintLib"), ("3D Models", "ModelLib")):
        pairs.append((label, W.body(str(cfg.get(key, "")) or "Not Set", mono=True, dim=True)))
    card = W.Card(pad=16); card.body.addWidget(W.dl(pairs, key_width=160)); lay.addWidget(card)

    # --- Data (bundled, read-only STM32 database) ---
    lay.addWidget(W.eyebrow("Data"))
    n = stm32_db.package_count()
    db_text = (f"Database: bundled, {n} packages (read-only)." if n
               else "Database: not built yet (built in CI for release).")
    lay.addWidget(_setting_row(
        "STM32 Database", db_text,
        W.tag("Ready", "ok") if n else W.tag("Not Built", "mut")))

    # --- Sourcing (baked key; no user field) ---
    lay.addWidget(W.eyebrow("Sourcing"))
    live = bool(LM.resolve_mouser_key())
    if live:
        secs = None
        try:
            secs = LM.mouser_reset_seconds_remaining()
        except Exception:                              # noqa: BLE001
            secs = None
        if secs:                                       # shared key currently capped
            status = (f"Daily limit reached on the shared Mouser key. Resets in "
                      f"~{fmt_countdown(secs)}. LCSC/DigiKey still source in the meantime.")
            mouser_tag = W.tag("Rate Limited", "warn")
        else:
            status = "Sourcing ready. Built-in Mouser key (shared, 1000 lookups/day)."
            mouser_tag = W.tag("Ready", "ok")
    else:
        status = "Sourcing unavailable. No key in this build."
        mouser_tag = W.tag("Unavailable", "mut")
    lay.addWidget(_setting_row("Mouser", status, mouser_tag))

    # LCSC — the key-free fallback distributor (jlcsearch). On by default so sourcing and
    # volume pricing work even with no Mouser key; a user can turn it off here.
    lcsc_on = LM.lcsc_enabled(ctx.cfg)
    lcsc_seg = W.Segmented(
        ["On", "Off"], selected=(0 if lcsc_on else 1),
        on_change=lambda m: LM.write_setting("LcscSourcing", "1" if m == "On" else "0"),
        tip="Use LCSC (no API key) to source and price parts Mouser doesn't carry")
    lay.addWidget(_setting_row(
        "LCSC Fallback",
        "Key-free second source. Fills parts Mouser can't, and prices at volume breaks.",
        lcsc_seg))

    # DigiKey — the last-resort third source (billed OAuth2). Active only when creds
    # are configured (DIGIKEY_CLIENT_ID / _SECRET env or a build-baked default); absent
    # creds it never registers, so there is nothing to toggle — just a status line.
    dk_id, dk_secret = LM.resolve_digikey_creds(ctx.cfg)
    dk_ready = bool(dk_id and dk_secret)
    dk_status = ("DigiKey ready. Third-source credentials configured."
                 if dk_ready else
                 "DigiKey not configured. Set DIGIKEY_CLIENT_ID / _SECRET to enable this third source.")
    lay.addWidget(_setting_row(
        "DigiKey", dk_status,
        W.tag("Ready", "ok") if dk_ready else W.tag("Not Configured", "mut")))

    # --- Application version + updates ---
    lay.addWidget(W.eyebrow("Application"))
    lay.addWidget(_setting_row(
        "Version", f"KiCad Manager {LM.APP_VERSION}",
        W.btn("Check for Updates", "default", "Check GitHub for a newer release",
              on_click=lambda: ctx.bus.emit("app.check_updates"))))

    lay.addStretch(1)
    return root


class SettingsFeature(F.Feature):
    id = "settings"
    title = "Settings"
    order = 900
    category = "System"

    def build(self, ctx: F.Context) -> QWidget:
        return W.scroll_body(_settings_panel(ctx, None))


F.register(SettingsFeature())
