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
