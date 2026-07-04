#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the NEW board-level render in tools/fp_render.py.

``render_board_image`` shells out to the installed KiCad's ``kicad-cli`` to
turn a whole ``.kicad_pcb`` into a QImage. Tests split in two:

  * deterministic tests (no external tool needed) exercise the defensive
    unavailable / bad-input paths — they must return a clear reason and never
    raise;
  * an end-to-end test that generates a tiny board and asserts a non-empty
    image is produced — skipped cleanly when kicad-cli is not installed.

Pure-logic helpers (extents / canvas aspect) are covered directly. The image
paths need only QImage (built-in PNG handler) so they run headlessly; the SVG
fallback additionally needs PyQt5.QtSvg and skips when it is absent.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import fp_render as F  # noqa: E402


# A minimal but valid KiCad 10 board: 50x30 mm outline on Edge.Cuts plus a
# copper track, a via, and a silk line so render/svg have visible content.
_TINY_PCB = """(kicad_pcb
  (version 20240108) (generator "test")
  (general (thickness 1.6)) (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal)
          (36 "B.SilkS" user) (37 "F.SilkS" user) (44 "Edge.Cuts" user))
  (setup) (net 0 "")
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 50 0) (end 50 30) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 50 30) (end 0 30) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.15))
  (gr_line (start 5 5) (end 45 5) (layer "F.SilkS") (width 0.2))
  (segment (start 5 15) (end 45 15) (width 0.6) (layer "F.Cu") (net 0))
  (via (at 25 22) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 0))
)
"""


def _write_tiny(tmp_path) -> Path:
    p = tmp_path / "tiny.kicad_pcb"
    p.write_text(_TINY_PCB, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Pure-logic helpers (no subprocess)
# ---------------------------------------------------------------------------
def test_board_extents_prefers_edge_cuts(tmp_path):
    pcb = _write_tiny(tmp_path)
    ext = F._board_extents(pcb)
    assert ext is not None
    x0, y0, x1, y1 = ext
    # outline is the 50x30 rectangle; the F.Cu/silk/via geometry sits inside it
    assert abs(x0 - 0.0) < 1e-6 and abs(y0 - 0.0) < 1e-6
    assert abs(x1 - 50.0) < 1e-6 and abs(y1 - 30.0) < 1e-6


def test_board_extents_none_for_nonboard(tmp_path):
    p = tmp_path / "not.kicad_pcb"
    p.write_text('(footprint "x")', encoding="utf-8")
    assert F._board_extents(p) is None


def test_board_canvas_preserves_aspect(tmp_path):
    pcb = _write_tiny(tmp_path)
    w, h = F._board_canvas(pcb, 500)
    # 50x30 board -> wider than tall -> width caps at max_px, height scales
    assert w == 500
    assert h == round(500 * 30 / 50)  # 300


def test_board_canvas_square_fallback_when_no_extents(tmp_path):
    p = tmp_path / "empty.kicad_pcb"
    p.write_text("(kicad_pcb (version 20240108))", encoding="utf-8")
    assert F._board_canvas(p, 640) == (640, 640)


# ---------------------------------------------------------------------------
# BoardRenderResult contract
# ---------------------------------------------------------------------------
def test_result_falsy_when_no_image():
    r = F.BoardRenderResult(reason="nope")
    assert not r
    assert r.ok is False
    assert r.image is None
    assert r.reason == "nope"


# ---------------------------------------------------------------------------
# Defensive / unavailable paths — deterministic, never touch the real CLI
# ---------------------------------------------------------------------------
def test_missing_file_returns_reason_without_raising(tmp_path):
    r = F.render_board_image(tmp_path / "does_not_exist.kicad_pcb")
    assert not r.ok
    assert "not found" in r.reason.lower()


def test_wrong_suffix_returns_reason(tmp_path):
    p = tmp_path / "board.txt"
    p.write_text("hello", encoding="utf-8")
    r = F.render_board_image(p)
    assert not r.ok
    assert ".kicad_pcb" in r.reason


def test_cli_absent_returns_clear_state(tmp_path, monkeypatch):
    # Force the "kicad-cli unavailable" branch regardless of what's installed:
    # a real board file, but the locator reports nothing.
    monkeypatch.setattr(F, "find_board_render_cli", lambda: None)
    pcb = _write_tiny(tmp_path)
    r = F.render_board_image(pcb)
    assert not r.ok
    assert r.image is None
    assert "kicad-cli" in r.reason.lower()


def test_have_board_render_matches_locator(monkeypatch):
    monkeypatch.setattr(F, "find_board_render_cli", lambda: None)
    assert F.have_board_render() is False
    monkeypatch.setattr(F, "find_board_render_cli", lambda: "kicad-cli")
    assert F.have_board_render() is True


# ---------------------------------------------------------------------------
# End-to-end — needs the real kicad-cli; skip cleanly when it is absent
# ---------------------------------------------------------------------------
_CLI = F.find_board_render_cli()
_HAVE_QTSVG = True
try:
    from PyQt5.QtSvg import QSvgRenderer  # noqa: F401
except Exception:
    _HAVE_QTSVG = False


@pytest.mark.skipif(_CLI is None, reason="kicad-cli not installed")
def test_render_board_auto_produces_image(tmp_path):
    pcb = _write_tiny(tmp_path)
    r = F.render_board_image(pcb, max_px=400)
    assert r.ok, "expected a board image, got reason: %s" % r.reason
    assert r.image is not None and not r.image.isNull()
    assert r.image.width() > 0 and r.image.height() > 0
    assert r.method in ("render", "svg")
    assert r.png_bytes  # non-empty PNG bytes for callers that want raw data


@pytest.mark.skipif(_CLI is None, reason="kicad-cli not installed")
def test_render_board_method_render(tmp_path):
    pcb = _write_tiny(tmp_path)
    r = F.render_board_image(pcb, max_px=320, method="render")
    assert r.ok, r.reason
    assert r.method == "render"
    # aspect ratio of the 50x30 board is preserved (landscape)
    assert r.image.width() >= r.image.height()


@pytest.mark.skipif(_CLI is None or not _HAVE_QTSVG,
                    reason="kicad-cli and PyQt5.QtSvg required")
def test_render_board_method_svg(tmp_path):
    pcb = _write_tiny(tmp_path)
    r = F.render_board_image(pcb, max_px=320, method="svg")
    assert r.ok, r.reason
    assert r.method == "svg"
    assert not r.image.isNull()
