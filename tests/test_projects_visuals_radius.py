"""Projects bespoke visuals must not emit the retired flat 4px radius.

design-rules.md §radius retires flat 4px and mandates RADIUS_CONTROL (6px) for
editable controls. The quiet-fields container QSS (used by every Design Rules
spin, every Net Class table cell, and every Board Geometry field) is generated
in Python and escaped the .qss lint, so assert on the real emitted string here.
"""
import re

from tools.ui import theme as T
from tools.ui.features import projects_visuals as PV


def _radii(qss: str):
    """Every numeric border-radius value emitted by the QSS."""
    return [int(m) for m in re.findall(r"border-radius:\s*(\d+)px", qss)]


def test_quiet_fields_qss_uses_radius_control_not_flat_4px():
    qss = PV.quiet_fields_qss()
    radii = _radii(qss)
    assert radii, "expected quiet fields to define hover/focus radii"
    # No retired flat 4px (or any 3/4/5) — controls carry RADIUS_CONTROL (6px).
    assert all(r == T.RADIUS_CONTROL for r in radii), (
        f"quiet_fields_qss emitted non-control radii {radii}; "
        f"expected all == RADIUS_CONTROL ({T.RADIUS_CONTROL})"
    )
    assert T.RADIUS_CONTROL == 6


def test_netclass_table_qss_inherits_control_radius():
    # The net-class table folds in the quiet-field rules — same invariant applies.
    qss = PV.netclass_table_qss()
    radii = _radii(qss)
    assert radii, "expected net-class table quiet fields to define radii"
    assert all(r == T.RADIUS_CONTROL for r in radii), (
        f"netclass_table_qss emitted non-control radii {radii}"
    )
