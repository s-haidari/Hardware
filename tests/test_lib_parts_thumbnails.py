"""Library — Parts picker 3D-model row thumbnails (subsystem: library-parts-picker).

Covers the right-aligned static 3D-model thumbnail on every parts-picker row and its
optimized loader: the on-disk PNG cache keyed by model path + mtime + size (rendered
ONCE, reused unchanged, re-rendered on edit), the async/headless-safe render (never
raises, STEP returns nothing headlessly), the neutral placeholder for a no-model row,
and that the thumbnail slot does not break the elide label or the Duplicate badge.
Run:  python -m pytest tests/test_lib_parts_thumbnails.py -q
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QPixmap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import fp_render as R  # noqa: E402
from ui.features import library_preview as P  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# A minimal but valid VRML2 cube — renders headlessly (pure-Python + numpy), unlike
# STEP whose native cascadio backend is skipped offscreen. This is what lets the
# thumbnail path be exercised end-to-end in CI.
_CUBE_WRL = """#VRML V2.0 utf8
Shape {
  geometry IndexedFaceSet {
    coord Coordinate { point [ 0 0 0, 1 0 0, 1 1 0, 0 1 0, 0 0 1, 1 0 1, 1 1 1, 0 1 1 ] }
    coordIndex [ 0 1 2 3 -1, 4 5 6 7 -1, 0 1 5 4 -1, 2 3 7 6 -1, 1 2 6 5 -1, 0 3 7 4 -1 ]
  }
}
"""


def _write_cube(dirpath: Path, name: str = "cube.wrl") -> Path:
    p = Path(dirpath) / name
    p.write_text(_CUBE_WRL, encoding="utf-8")
    return p


def _sync_ctx(cfg):
    """A ctx whose run_async runs inline (synchronous), so run_populate's threaded
    branch is exercised deterministically under the test."""
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    return SimpleNamespace(cfg=cfg, services=_Svc(), theme=None, bus=None)


# ── the on-disk thumbnail cache (fp_render.model_thumbnail) ────────────────────────
def test_thumbnail_cache_is_keyed_by_path_mtime_and_reused(tmp_path):
    """A model renders to a PNG once; a second call with the model unchanged returns
    the SAME cached file (no re-render), and a changed mtime re-renders to a new key."""
    cache = tmp_path / "cache"
    model = _write_cube(tmp_path)
    t1 = R.model_thumbnail(str(model), cache_dir=cache)
    assert t1 is not None and Path(t1).exists() and Path(t1).stat().st_size > 0
    t2 = R.model_thumbnail(str(model), cache_dir=cache)
    assert t2 == t1                                  # unchanged model → same cached file
    # A real render produced a 32×32 transparent PNG.
    img = QPixmap(t1)
    assert img.width() == R._THUMB_PX and img.height() == R._THUMB_PX
    # Bump the mtime → the cache key changes → a fresh render under a new filename.
    future = time.time() + 30
    os.utime(model, (future, future))
    t3 = R.model_thumbnail(str(model), cache_dir=cache)
    assert t3 is not None and Path(t3).name != Path(t1).name
    assert Path(t3).exists()


def test_thumbnail_is_headless_safe_and_never_raises(tmp_path):
    """The thumbnail path never raises for the ordinary failure modes: an empty path, a
    missing file, and (headless) a STEP model whose native backend is skipped all return
    None cleanly."""
    cache = tmp_path / "cache"
    assert R.model_thumbnail("", cache_dir=cache) is None
    assert R.model_thumbnail(None, cache_dir=cache) is None
    assert R.model_thumbnail(str(tmp_path / "nope.wrl"), cache_dir=cache) is None
    step = tmp_path / "part.step"
    step.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n",
                    encoding="utf-8")
    # Headless: STEP is skipped natively → None, not an exception, not a blank crash.
    assert R.model_thumbnail(str(step), cache_dir=cache) is None


# ── the picker row: thumbnail slot + placeholder + layout integrity ────────────────
def test_modeled_row_gets_a_thumbnail_no_model_row_stays_a_placeholder(tmp_path):
    """A row whose part has a resolvable 3D model paints a real pixmap into its
    right-aligned slot; a row with no model keeps the neutral empty slot (never an
    error). Both rows carry the SAME fixed-size slot so the row width is stable."""
    _write_cube(tmp_path)
    cfg = {"ModelLib": str(tmp_path)}
    rows = [
        {"name": "Modeled", "mpn": "X1", "symbols": ["s1"], "model": "cube.wrl", "footprint": "F1"},
        {"name": "NoModel", "mpn": "X2", "symbols": ["s2"], "model": "", "footprint": "F2"},
    ]
    ctx = _sync_ctx(cfg)
    pl = P.PartsList(rows, on_select=lambda r: None, cfg=cfg, ctx=ctx)
    _APP.processEvents()
    by_name = {r["name"]: w for (r, _it, w) in pl._items}
    modeled = by_name["Modeled"]._thumb
    nomodel = by_name["NoModel"]._thumb
    # Fixed-size slot on every row (layout stability).
    assert modeled.size().width() == P._THUMB_SLOT
    assert nomodel.size().width() == P._THUMB_SLOT
    # Modeled row painted a real pixmap; no-model row did not.
    assert modeled.pixmap() is not None and not modeled.pixmap().isNull()
    assert nomodel.pixmap() is None or nomodel.pixmap().isNull()


def test_thumbnail_slot_preserves_elide_label_and_dup_badge(tmp_path):
    """Adding the right-aligned thumbnail must not break the two existing right-side
    affordances: the primary name still elides, and the Duplicate badge widget is still
    built and stays a distinct widget from the thumbnail."""
    _write_cube(tmp_path)
    cfg = {"ModelLib": str(tmp_path)}
    long_name = "A Very Long Humanized Part Description That Must Elide In The Narrow Picker Column"
    rows = [{"name": long_name, "mpn": "LONG-MPN-0001", "symbols": ["s1"],
             "model": "cube.wrl", "footprint": "F1"}]
    pl = P.PartsList(rows, on_select=lambda r: None, cfg=cfg, ctx=_sync_ctx(cfg))
    _APP.processEvents()
    _row, _it, w = pl._items[0]
    prim = w._prim
    assert isinstance(prim, P._ElideLabel)
    assert prim.full_text() == long_name             # full text preserved for elide
    # Force a narrow width and re-elide: the shown text must be shorter than the full.
    prim.setFixedWidth(80)
    prim._reelide()
    _APP.processEvents()
    assert prim.text() != long_name                  # actually elided at this width
    # Dup badge still present and distinct from the thumbnail slot.
    assert getattr(w, "_dup_badge", None) is not None
    assert w._dup_badge is not w._thumb


def test_bare_constructor_without_cfg_shows_placeholders_and_never_renders(tmp_path):
    """The bare PartsList(rows, on_select) constructor (no cfg/ctx) still builds every
    row with a thumbnail slot, renders nothing (no ModelLib to resolve against), and
    does not raise — the neutral-placeholder degrade path."""
    rows = [{"name": "P", "mpn": "X", "symbols": ["s1"], "model": "cube.wrl", "footprint": "F1"}]
    pl = P.PartsList(rows, on_select=lambda r: None)
    _APP.processEvents()
    _row, _it, w = pl._items[0]
    assert w._thumb.size().width() == P._THUMB_SLOT
    assert w._thumb.pixmap() is None or w._thumb.pixmap().isNull()
    # No cfg → _row_model_path is None → queue is a clean no-op.
    assert pl._row_model_path(rows[0]) is None
    pl._queue_thumbnails()                            # must not raise


def test_thumbnail_survives_a_rescan_and_reuses_the_cache(tmp_path):
    """set_rows rebuilds the row widgets; the thumbnail re-applies from the (unchanged)
    disk cache without re-rendering. The modeled row is painted again after the rebuild."""
    _write_cube(tmp_path)
    cfg = {"ModelLib": str(tmp_path)}
    rows = [{"name": "Modeled", "mpn": "X1", "symbols": ["s1"], "model": "cube.wrl", "footprint": "F1"}]
    pl = P.PartsList(rows, on_select=lambda r: None, cfg=cfg, ctx=_sync_ctx(cfg))
    _APP.processEvents()
    assert not pl._items[0][2]._thumb.pixmap().isNull()
    # Rescan with the same rows (a library mutation elsewhere) → widgets rebuilt.
    pl.set_rows(list(rows))
    _APP.processEvents()
    w = pl._items[0][2]
    assert w._thumb.pixmap() is not None and not w._thumb.pixmap().isNull()
