"""The UI drive-audit, run as a GATE inside the suite.

Runs tools/ui/drive_audit.py as a SUBPROCESS on purpose: it drives the real widgets
(changing every selector, clicking actions), so a crash there is a genuine app crash —
in-process it would take pytest down with it, so we isolate it and turn a non-zero exit
into a red test. This is the check that would have caught the project-change segfault
that 1268 wiring-tests waved through.
"""
import os
import subprocess
import sys
from pathlib import Path


def test_ui_drive_audit_passes():
    root = Path(__file__).resolve().parents[1]
    audit = root / "tools" / "ui" / "drive_audit.py"
    # Force UTF-8 in the child so its → / ▶ progress prints can't crash on a cp1252 pipe
    # (Windows), and decode its output as UTF-8 to match — the drive-audit's own exit code
    # is what we gate on, not the console codepage it happened to inherit.
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(audit)], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=240)
    assert r.returncode == 0, (
        f"drive-audit FAILED (exit {r.returncode}) — a UI interaction crashed or a "
        f"selection-dependent view went stale. This is a real regression:\n"
        f"--- stdout ---\n{r.stdout[-3000:]}\n--- stderr ---\n{r.stderr[-1500:]}")
