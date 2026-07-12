"""python -m ui  ->  launch NETDECK.

The polished redesign shell (ui.shell.NetdeckShell) — the converged, at-parity,
drive-audited UI. (The legacy barebones UI was removed at the Phase-3 flip; its
capabilities all live in the styled features now — see docs/CAPABILITIES.md.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    from ui.shell import run  # noqa: E402
    sys.exit(run())
