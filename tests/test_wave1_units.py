"""Wave 1 — WS-A: one persisted app-wide Length Units (mm/mils) preference.

Covers PCB-02 / LIB-14 / SHELL-03: the mm/mils choice is no longer a panel-local
dict that resets every launch — it lives in `ui.units` (process-global, like the
theme dark flag), is persisted to config.json ("Units"), and is driven/broadcast
over the bus ("units.set_mode" command, "units.changed" notification). Settings
and the PCB Setup unit toggle both consume it; the Library previews and PCB fab
facts format lengths through it.

Headless: the pure `ui.units` logic needs no Qt; the panel/shell wiring builds
under the offscreen QApplication.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _reset_units():
    """The unit mode is process-global; keep every test isolated (default mm)."""
    from ui import units as U
    U.set_mode(U.MM)
    yield
    U.set_mode(U.MM)


# ── ui.units — pure logic ─────────────────────────────────────────────────────
def test_units_default_is_mm():
    from ui import units as U
    assert U.mode() == U.MM
    assert U.is_mils() is False


def test_units_set_mode_normalises():
    from ui import units as U
    assert U.set_mode("mils") == "mils" and U.is_mils() is True
    assert U.set_mode("MILS") == "mils"          # case-insensitive
    assert U.set_mode("mm") == "mm" and U.is_mils() is False
    assert U.set_mode("nonsense") == "mm"        # anything unknown -> mm
    assert U.set_mode(None) == "mm"


def test_units_set_mode_trims_surrounding_whitespace():
    """A hand-edited config.json 'Units' value with a stray space must still
    resolve — 'MILS ' / ' mils' are the mils mode, not a silent fallback to mm."""
    from ui import units as U
    assert U.set_mode("mils ") == "mils" and U.is_mils() is True
    assert U.set_mode(" MILS ") == "mils"
    assert U.set_mode("\tmils\n") == "mils"
    assert U.set_mode("  mm  ") == "mm"          # trims mm side too


def test_units_to_display_and_suffix():
    from ui import units as U
    assert U.to_display(25.4) == pytest.approx(25.4)
    assert U.suffix() == " mm"
    U.set_mode("mils")
    assert U.to_display(25.4) == pytest.approx(1000.0)   # 25.4 mm = 1000 mils
    assert U.suffix() == " mils"


def test_units_fmt_single_value():
    from ui import units as U
    assert U.fmt(1.6) == "1.6 mm"
    U.set_mode("mils")
    assert U.fmt(1.6) == "62.99 mils"               # 1.6 mm -> 62.9921 mils


def test_units_fmt_dims_shares_one_suffix():
    from ui import units as U
    assert U.fmt_dims(6.5, 5.2) == "6.5 × 5.2 mm"
    U.set_mode("mils")
    assert U.fmt_dims(6.5, 5.2) == "255.9 × 204.7 mils"


def test_units_fmt_degrades_on_missing_or_nonfinite():
    """fmt/fmt_dims must not raise when a caller passes None or a non-finite
    length (e.g. a raw dict value) — they degrade to a placeholder."""
    import math

    from ui import units as U
    assert U.fmt(None) == "— mm"
    assert U.fmt(float("nan")) == "— mm"
    assert U.fmt(math.inf) == "— mm"
    U.set_mode("mils")
    assert U.fmt(None) == "— mils"
    assert U.fmt(float("nan")) == "— mils"
    U.set_mode("mm")
    # dims: the good values still format; only the bad one becomes a placeholder,
    # and the shared suffix survives.
    assert U.fmt_dims(6.5, None) == "6.5 × — mm"
    assert U.fmt_dims(None, None) == "— × — mm"
    U.set_mode("mils")
    assert U.fmt_dims(6.5, None) == "255.9 × — mils"


# ── ui.util length conversions — None-safe display helpers ────────────────────
def test_length_conversions_convert_real_numbers():
    from ui.util import mm_to_mils, mils_to_mm
    assert mm_to_mils(25.4) == pytest.approx(1000.0)   # 25.4 mm = 1000 mils
    assert mils_to_mm(1000.0) == pytest.approx(25.4)
    # int input is coerced, not rejected
    assert mm_to_mils(1) == pytest.approx(1 / 0.0254)
    # round-trip is stable
    assert mils_to_mm(mm_to_mils(1.6)) == pytest.approx(1.6)


def test_length_conversions_pass_none_through():
    """A missing/unknown length (e.g. a footprint dict with no width_mm) must not
    blow up the shared display helpers with float(None) — it passes through as
    None so callers can render a clean empty value instead of crashing."""
    from ui.util import mm_to_mils, mils_to_mm
    assert mm_to_mils(None) is None
    assert mils_to_mm(None) is None


def test_length_conversions_still_reject_garbage():
    """None is the only sanctioned passthrough; genuinely bad edit-commit input
    (a non-numeric string) must still surface loudly, not silently become 0."""
    from ui.util import mm_to_mils, mils_to_mm
    with pytest.raises((TypeError, ValueError)):
        mm_to_mils("not-a-number")
    with pytest.raises((TypeError, ValueError)):
        mils_to_mm("not-a-number")


# ── persistence seam (config.json "Units") ────────────────────────────────────
def test_units_setting_round_trips(tmp_path):
    import LibraryManager as LM
    cfgp = tmp_path / "config.json"
    assert LM.read_setting("Units", "mm", config_path=cfgp) == "mm"     # default absent
    assert LM.write_setting("Units", "mils", config_path=cfgp) is True
    assert LM.read_setting("Units", "mm", config_path=cfgp) == "mils"


# ── Settings panel: a Units control that emits the command topic ──────────────
def test_settings_has_units_segment_that_emits_bus_topic():
    _app()
    import LibraryManager as LM
    from ui import feature as F
    from ui import theme as T
    from ui import widgets as W
    from ui.features import settings as S

    seen = []
    bus = F.EventBus()
    bus.on("units.set_mode", lambda mode: seen.append(mode))

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=bus)
    panel = S._settings_panel(ctx, None)

    segs = panel.findChildren(W.Segmented)
    # Theme + Units + LCSC Fallback segmented controls now live in Settings.
    assert len(segs) == 3
    units_seg = next(s for s in segs
                     if [b.text() for b in s._buttons] == ["mm", "mils"])
    units_seg._pick(1)                              # the "mils" segment
    assert seen == ["mils"], "Units control must emit units.set_mode"


def test_settings_units_segment_reflects_persisted_value(monkeypatch):
    _app()
    import LibraryManager as LM
    from ui import feature as F, theme as T, widgets as W
    from ui.features import settings as S

    monkeypatch.setattr(LM, "read_setting",
                        lambda key, default=None, config_path=None: "mils" if key == "Units" else default)

    class _Svc:
        def run_async(self, *a, **k): pass
        def log(self, *a, **k): pass

    ctx = F.Context(cfg=LM.load_config(), services=_Svc(), theme=T, bus=F.EventBus())
    panel = S._settings_panel(ctx, None)
    units_seg = next(s for s in panel.findChildren(W.Segmented)
                     if [b.text() for b in s._buttons] == ["mm", "mils"])
    assert units_seg._buttons[1].property("selected") is True   # mils pre-selected


# ── Segmented: silent external sync (no on_change re-fire) ─────────────────────
def test_segmented_select_value_is_silent():
    _app()
    from ui import widgets as W
    fired = []
    seg = W.Segmented(["mm", "mils"], on_change=lambda m: fired.append(m))
    seg.select_value("mils")
    assert seg._buttons[1].property("selected") is True
    assert fired == [], "external sync must NOT re-emit on_change"
    seg._pick(0)                                    # a real user click still fires
    assert fired == ["mm"]


# ── Shell wiring: seed at launch, persist + broadcast on change ────────────────
def _shell(monkeypatch, *, units="mm"):
    import LibraryManager as LM
    written = {}
    monkeypatch.setattr(
        LM, "read_setting",
        lambda key, default=None, config_path=None: (
            units if key == "Units" else ("Dark" if key == "Theme" else default)))
    monkeypatch.setattr(
        LM, "write_setting",
        lambda key, value, config_path=None: written.__setitem__(key, value) or True)
    from ui.shell import NetdeckShell
    return NetdeckShell(LM.load_config()), written


def test_shell_seeds_units_from_persisted_value(monkeypatch):
    _app()
    from ui import units as U
    win, _ = _shell(monkeypatch, units="mils")
    assert U.mode() == "mils"
    win.close()


def test_shell_units_bus_sets_persists_and_broadcasts(monkeypatch):
    _app()
    from ui import units as U
    win, written = _shell(monkeypatch, units="mm")
    assert U.mode() == "mm"

    heard = []
    win.ctx.bus.on("units.changed", lambda m: heard.append(m))
    win.ctx.bus.emit("units.set_mode", "mils")

    assert U.mode() == "mils"                    # module state updated
    assert written.get("Units") == "mils"        # persisted (SHELL-03)
    assert heard == ["mils"]                      # live panels notified
    win.close()


# ── PCB Setup consumes the app-wide unit (PCB-02) ─────────────────────────────
def _fake_ctx_with_bus(cfg=None):
    from ui import feature as F

    class _Svc:
        def log(self, *a, **k): pass
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg=cfg or {}, services=_Svc(), bus=F.EventBus())


_MINIMAL_PRO = (
    '{\n'
    '  "board": {"design_settings": {"rules": {"min_clearance": 0.2,\n'
    '    "min_track_width": 0.254}}},\n'
    '  "net_settings": {"classes": [{"name": "Default"}]}\n'
    '}\n'
)
_MINIMAL_PCB = (
    "(kicad_pcb\n\t(version 20241229)\n\t(generator \"pcbnew\")\n"
    "\t(layers\n\t\t(0 \"F.Cu\" signal)\n\t)\n"
    "\t(setup\n\t\t(pad_to_mask_clearance 0.05)\n\t)\n\t(net 0 \"\")\n)\n"
)


def _state(tmp_path):
    d = tmp_path / "Proj"; d.mkdir()
    (d / "Proj.kicad_pro").write_text(_MINIMAL_PRO, encoding="utf-8")
    (d / "Proj.kicad_pcb").write_text(_MINIMAL_PCB, encoding="utf-8")

    class _S:
        def __init__(self):
            self.projects = [d]
            self.project = d
        def boards(self):
            import nd_wizard
            return nd_wizard.list_boards(self.project)
        def schematics(self):
            import nd_wizard
            return nd_wizard.list_schematics(self.project)
        def root_schematic(self):
            return None
    return _S()


def test_pcb_setup_starts_in_persisted_unit(tmp_path):
    _app()
    from ui import units as U
    from ui.features import projects as PJ
    U.set_mode("mils")
    panel = PJ._pcb_setup_panel(_fake_ctx_with_bus(), _state(tmp_path))
    # the inline unit toggle reflects the app-wide mode at build time
    assert panel._unit_seg._buttons[1].property("selected") is True
    panel.grab()


def test_pcb_setup_unit_toggle_emits_command(tmp_path):
    _app()
    from ui.features import projects as PJ
    ctx = _fake_ctx_with_bus()
    seen = []
    ctx.bus.on("units.set_mode", lambda m: seen.append(m))
    panel = PJ._pcb_setup_panel(ctx, _state(tmp_path))
    panel._unit_seg._pick(1)                        # user flips to mils
    assert seen == ["mils"], "the PCB unit toggle must drive the app-wide setting"


def test_pcb_setup_reacts_to_units_changed_broadcast(tmp_path):
    _app()
    from ui.features import projects as PJ
    ctx = _fake_ctx_with_bus()
    panel = PJ._pcb_setup_panel(ctx, _state(tmp_path))
    assert panel._unit["u"] == "mm"
    ctx.bus.emit("units.changed", "mils")           # changed elsewhere (e.g. Settings)
    assert panel._unit["u"] == "mils"               # panel re-rendered in mils
    assert panel._unit_seg._buttons[1].property("selected") is True   # seg synced silently
    panel.grab()


# ── Library previews format dims in the app-wide unit (LIB-14) ────────────────
def test_library_preview_caption_is_unit_aware():
    _app()
    from ui import units as U
    from ui.features.library_preview import PartDetail

    detail = PartDetail(_fake_ctx_with_bus())
    # Simulate a rendered footprint summary (canonical mm, as fp_render returns).
    detail._fp_summary = {"pads": 2, "width_mm": 6.5, "height_mm": 5.2}
    assert detail._fp_caption(detail._fp_summary) == "2 Pads · 6.5 × 5.2 mm"
    U.set_mode("mils")
    assert detail._fp_caption(detail._fp_summary) == "2 Pads · 255.9 × 204.7 mils"


def test_library_preview_recaptions_on_units_changed():
    _app()
    from ui import units as U
    from ui.features.library_preview import PartDetail

    ctx = _fake_ctx_with_bus()
    detail = PartDetail(ctx)
    detail._fp_summary = {"pads": 4, "width_mm": 2.0, "height_mm": 1.0}
    detail._mdl_summary = {"triangles": 12, "size_mm": [2.0, 1.0, 0.5]}
    # Contract: the shell (single writer) updates the module, THEN broadcasts;
    # consumers re-render from the now-current unit. The subscription must fire
    # and re-label the cached dims without an async re-render.
    U.set_mode("mils")
    ctx.bus.emit("units.changed", "mils")
    assert detail._fp.caption_text() == "4 Pads · 78.7 × 39.4 mils"
    assert detail._mdl.caption_text() == "12 Triangles · 78.7 × 39.4 × 19.7 mils"
