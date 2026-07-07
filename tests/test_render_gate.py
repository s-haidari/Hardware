"""The committed render gate must produce a dark and light PNG for every
app surface, driving the real shell headless. This is the regression guard
that every surface always builds and grabs under both themes."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def test_render_gate_writes_dark_and_light_for_every_surface(tmp_path):
    from ui import render_gate
    saved = render_gate.render_all(tmp_path, themes=("dark", "light"))
    assert saved, "no surfaces rendered"
    for p in saved:
        assert p.exists() and p.stat().st_size > 1000, f"empty render: {p}"
    assert any(p.name.endswith(".dark.png") for p in saved)
    assert any(p.name.endswith(".light.png") for p in saved)
    stems = {p.name.split(".")[0] for p in saved}
    for fid in ("bench", "library", "projects", "settings"):
        assert fid in stems, f"missing surface for {fid}"
