import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pytest  # noqa: E402


def _cfg(tmp_path):
    """A minimal library cfg pointing at real files under tmp_path."""
    sym = tmp_path / "MySymbols.kicad_sym"
    sym.write_text(
        '(kicad_symbol_lib\n'
        '  (symbol "R_0402" (property "Footprint" "MyFootprints:R_0402" (id 2)))\n'
        '  (symbol "2N7002" (property "Footprint" "MyFootprints:SOT-23" (id 2)))\n'
        ')\n'
    )
    fp = tmp_path / "fps"; fp.mkdir()
    (fp / "R_0402.kicad_mod").write_text('(footprint "R_0402")')
    mdl = tmp_path / "models"; mdl.mkdir()
    (mdl / "SOT-23.step").write_bytes(b"ISO-10303-21;")
    return {"SymbolLib": str(sym), "FootprintLib": str(fp), "ModelLib": str(mdl)}


def test_symbol_block_for_returns_named_block(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    block = P.symbol_block_for(cfg, "R_0402")
    assert block is not None and "R_0402" in block
    assert P.symbol_block_for(cfg, "NoSuchSymbol") is None


def test_footprint_and_model_paths(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    fp = P.footprint_path_for(cfg, {"footprint": "R_0402"})
    assert fp is not None and fp.name == "R_0402.kicad_mod" and fp.exists()
    assert P.footprint_path_for(cfg, {"footprint": None}) is None
    mp = P.model_path_for(cfg, {"model": "SOT-23.step"})
    assert mp is not None and mp.name == "SOT-23.step" and mp.exists()
    assert P.model_path_for(cfg, {"model": None}) is None


def test_resolve_model_render_none_for_missing(tmp_path):
    from ui.features import library_preview as P
    assert P.resolve_model_render(None) == ("none", None)
    assert P.resolve_model_render(tmp_path / "nope.step") == ("none", None)


def test_resolve_model_render_prefers_mesh_then_image(tmp_path, monkeypatch):
    from ui.features import library_preview as P
    import fp_render as R
    p = tmp_path / "m.step"; p.write_bytes(b"x")

    # mesh available -> ("mesh", (verts, faces))
    monkeypatch.setattr(R, "load_step_mesh", lambda _p: ([[0, 0, 0]], [[0, 0, 0]]))
    kind, payload = P.resolve_model_render(p)
    assert kind == "mesh" and payload == ([[0, 0, 0]], [[0, 0, 0]])

    # no mesh, static image available -> ("image", QImage)
    from PyQt5.QtGui import QImage
    img = QImage(4, 4, QImage.Format_ARGB32)
    monkeypatch.setattr(R, "load_step_mesh", lambda _p: (None, None))
    monkeypatch.setattr(R, "render_step_image", lambda _p, px=420: img)
    kind, payload = P.resolve_model_render(p)
    assert kind == "image" and payload is img

    # nothing -> ("none", None)
    monkeypatch.setattr(R, "render_step_image", lambda _p, px=420: None)
    assert P.resolve_model_render(p) == ("none", None)


def test_meshview_constructs_for_each_kind():
    from ui.features.library_preview import MeshView
    from PyQt5.QtGui import QImage

    mv = MeshView("mesh", ([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]]))
    assert mv.interactive is True
    mv.grab()  # paints without raising

    img = QImage(8, 8, QImage.Format_ARGB32); img.fill(0)
    sv = MeshView("image", img)
    assert sv.interactive is False
    sv.grab()


def _fake_ctx(cfg):
    """A Context-like object with synchronous run_async (renders inline in tests)."""
    class _Svc:
        def __init__(self): self.logs = []
        def log(self, m): self.logs.append(str(m))
        def run_async(self, fn, ok=None, done_cb=None):
            fn()
            if done_cb:
                done_cb(True)
    from types import SimpleNamespace
    return SimpleNamespace(cfg=cfg, services=_Svc())


def test_partdetail_renders_real_previews(tmp_path, monkeypatch):
    """Done-callback happy path: monkeypatched renders return real images so the
    footprint caption is populated, confirming the populate(result, ok) path fires."""
    from ui.features import library_preview as P
    import fp_render
    from PyQt5.QtGui import QImage

    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)

    good_img = QImage(8, 8, QImage.Format_ARGB32)
    good_img.fill(0xFF0000FF)  # opaque blue — definitely not null

    monkeypatch.setattr(fp_render, "render_symbol_image",
                        lambda block: good_img)
    monkeypatch.setattr(fp_render, "render_footprint_image",
                        lambda path: good_img)
    monkeypatch.setattr(fp_render, "footprint_summary",
                        lambda path: {"pads": 2, "width_mm": 1.0, "height_mm": 0.5})

    det = P.PartDetail(ctx)
    row = {
        "name": "R_0402", "mpn": "R_0402",
        "manufacturer": "Yageo", "description": "Chip Resistor",
        "symbols": ["R_0402"], "footprint": "R_0402", "model": None,
    }
    det.show(row)

    # The footprint done-callback happy path should have populated the caption
    cap = det._fp.caption_text()
    assert cap != "", f"Expected a non-empty footprint caption, got: {cap!r}"
    assert "2" in cap and "1.0" in cap, f"Caption should contain pad/size info: {cap!r}"
    det.grab()  # must not raise


def test_partdetail_show_populates_and_clears(tmp_path):
    from ui.features import library_preview as P
    cfg = _cfg(tmp_path)
    ctx = _fake_ctx(cfg)
    det = P.PartDetail(ctx)
    # a real grouped-style row
    row = {"name": "R_0402", "mpn": "R_0402", "manufacturer": "Yageo",
           "description": "Chip Resistor", "datasheet": None,
           "footprint": "R_0402", "symbols": ["R_0402"], "model": None,
           "has_symbol": True, "has_footprint": True, "has_model": False}
    det.show(row)
    det.grab()                       # renders without raising
    det.show(None)                   # clearing is safe
    det.grab()
