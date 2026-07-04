"""Design-token layer in ui_theme (Fluent-grounded): spacing/type/radius/status."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import ui_theme as th  # noqa: E402


def test_spacing_ramp_is_4px_based():
    assert th.SPACING["xs"] == 4 and th.SPACING["s"] == 8 and th.SPACING["xl"] == 24
    assert th.sp("m") == 12
    assert th.sp("nope", 99) == 99


def test_radius_scale():
    assert th.radius("control") == 4 and th.radius("card") == 6
    assert th.radius("pin") == 2


def test_type_ramp_roles():
    for role in ("display", "title", "subtitle", "body", "caption", "overline", "data"):
        size, weight = th.type_role(role)
        assert isinstance(size, (int, float)) and size > 0 and isinstance(weight, str)
    # unknown role falls back to body
    assert th.type_role("nope") == th.TYPE["body"]


def test_status_tracks_active_theme():
    th.set_theme(True)                       # dark
    assert th.status("warn") == th.STATUS["warn"][0]
    assert th.status("ok") == th.STATUS["ok"][0]
    th.set_theme(False)                      # light
    assert th.status("err") == th.STATUS["err"][1]
    th.set_theme(False)                      # leave app default (light) as found
