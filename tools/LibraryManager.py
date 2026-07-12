#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad Manager - PyQt UI

Workflow:
0. Pull (rebase + autostash) to ensure local repo is up to date.
1. Drop vendor ZIPs into the Drop Zone (or Open Downloads to place them).
2. Process ZIPs (move footprints/symbols/models, merge symbols).
3. Clean leftovers (delete remaining ZIPs/extracted folders in downloads).
4. Stage, Commit & Push to GitHub.

Features:
- Responsive PyQt UI (grid-based) that scales cleanly
- Left-aligned workflow buttons (Step 0 → Step 4)
- Drag-and-drop ZIPs into a Drop Zone to copy into downloads/
- Scrollable "Library Contents" panel on the right with Search, Filter, Open, Delete
- Dark theme styling
- Downloads file watcher (QFileSystemWatcher — no extra dependency)
- Live log panel with scrollbar

Author: You
"""

import os
import re
import sys
import json
import time
import shutil
import filecmp
import subprocess
import threading
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from typing import Optional, List, Dict, Tuple
import webbrowser

import nd_git  # unified git backend (PAT auth, corruption guard, timeouts)
import nd_commit_msg  # conventional-commit message builders (GIT-01)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QTextEdit, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QListWidget, QListWidgetItem, QAbstractItemView, QTabBar, QStackedWidget,
    QComboBox, QCheckBox, QGroupBox, QFileDialog, QMessageBox, QInputDialog,
    QHeaderView, QFrame, QScrollArea, QSizePolicy, QSplitter,
    QToolButton, QMenu, QProgressBar, QStatusBar, QSlider, QLayout, QDialog
)
from PyQt5.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QSettings,
    QRect, QRectF, QSize, QPoint
)
from PyQt5.QtGui import (
    QPalette, QColor, QBrush, QIcon, QImage, QPixmap,
    QDragEnterEvent, QDropEvent, QPainter, QPen, QFont
)
try:
    from PyQt5.QtSvg import QSvgRenderer
    HAVE_QTSVG = True
except Exception:
    HAVE_QTSVG = False


# -----------------------------
# Subprocess helper (no flashing console windows on Windows)
# -----------------------------
# When the GUI runs under pythonw.exe (no console), each child process would
# otherwise pop its own console window. CREATE_NO_WINDOW suppresses that flash.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(cmd, **kwargs):
    """subprocess.run wrapper that never flashes a console window."""
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | _NO_WINDOW
    return subprocess.run(cmd, **kwargs)


# -----------------------------
# Configuration (edit defaults)
# -----------------------------
import app_secrets  # baked-in Mouser key (SP1); committed + bundled, never gitignored


def resolve_mouser_key(cfg: Dict[str, str] = None) -> str:
    """The Mouser API key to use (SP1 decision #3).

    Resolution mirrors the DigiKey creds: MOUSER_API_KEY env var (silent dev override)
    -> a user key saved in config.json ('MouserApiKey', gitignored) -> the baked default
    in app_secrets.MOUSER_API_KEY_DEFAULT. A user who supplies their own (uncapped) key
    via config.json is honored rather than silently ignored. `cfg` is accepted for
    call-site compatibility; the config value is read from disk so any caller path sees it.
    """
    return (os.environ.get("MOUSER_API_KEY")
            or read_setting("MouserApiKey")
            or app_secrets.MOUSER_API_KEY_DEFAULT)


# ── Mouser daily-cap countdown (SRC-04) ───────────────────────────────────────
# The app ships ONE shared, free Mouser Search key (app_secrets.MOUSER_API_KEY_DEFAULT).
# Free-tier keys are capped at 1000 calls/day and Mouser resets the counter at midnight
# US Central. When we hit the cap we record it so the UI can show a countdown to the
# next reset instead of silently failing — "use one key + show a timer for next usage".
_MOUSER_LIMIT_KEY = "MouserRateLimitedAt"     # config.json: epoch seconds we last saw a cap


def _central_tz():
    """The America/Chicago zone (DST-aware) if tzdata is available, else a fixed UTC-6.

    Mouser resets the free-tier daily cap at 00:00 US Central, which is UTC-5 in summer
    (CDT) and UTC-6 in winter (CST). Honoring real DST keeps the countdown exact; the
    fixed-offset fallback only matters on a stripped runtime with no zoneinfo database."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Chicago")
    except Exception:                                # noqa: BLE001 — no tzdata: conservative -6
        return _dt.timezone(_dt.timedelta(hours=-6))


def _next_mouser_reset(now: float = None) -> float:
    """Epoch seconds of the next Mouser daily-cap reset (00:00 US Central, DST-aware).
    Always in the future relative to `now`."""
    import datetime as _dt
    if now is None:
        now = time.time()
    central = _central_tz()
    dt = _dt.datetime.fromtimestamp(now, central)
    nxt = (dt + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def note_mouser_rate_limited(config_path: Optional[Path] = None) -> None:
    """Record that the shared Mouser key just hit its daily cap, so the UI can count
    down to the next reset. Best-effort; never raises."""
    try:
        write_setting(_MOUSER_LIMIT_KEY, int(time.time()), config_path)
    except Exception:                                # noqa: BLE001
        pass


def mouser_reset_seconds_remaining(config_path: Optional[Path] = None) -> Optional[int]:
    """Seconds until the shared Mouser key's daily cap resets, or None when we have not
    seen a cap this cycle. Returns 0 once the reset has passed (the marker is stale)."""
    try:
        stamp = read_setting(_MOUSER_LIMIT_KEY, None, config_path)
        if not stamp:
            return None
        stamp = float(stamp)
    except (TypeError, ValueError):
        return None
    now = time.time()
    reset_at = _next_mouser_reset(stamp)             # reset that follows when we were capped
    if now >= reset_at:
        return 0                                     # cycle has rolled over; marker is stale
    return int(reset_at - now)


def detect_repo_root() -> Path:
    """Where the library lives.

    Deriving this from the app's own location makes it portable across machines,
    usernames, and clones with no edits.
      * Normal script: repo root = parent of the tools/ folder holding this file.
      * Frozen .exe (PyInstaller): repo root = the folder containing the .exe,
        so dropping the exe into a repo checkout "just works". (sys._MEIPASS is a
        throwaway temp dir, so we must NOT use __file__ when frozen.)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# SP1 — self-contained core: bundle vs. writable-location resolution
#
# Two intents replace the scattered __file__ / sys.executable logic:
#   * bundle_path()      — read-only bundled assets (DB, seed, fonts, key).
#   * library_location() — the writable dir the user chose (config, libs, ...).
# In dev (not frozen) both collapse to the repo tree, so development and the
# test suite are unaffected: none of the pointer/seed machinery runs from source.
# ---------------------------------------------------------------------------

SEED_VERSION = "1"  # bump to force a re-seed of user locations on next launch


def bundle_path(rel: str) -> Path:
    """A read-only bundled asset: sys._MEIPASS when frozen, the repo tree in dev."""
    base = (Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False)
            else detect_repo_root())
    return base / rel


def pointer_path() -> Path:
    """The fixed, tiny pointer file recording the user's chosen library location.

    Frozen default: %APPDATA%/KiCadLibraryManager/workspace.json (POSIX fallback
    ~/.config). Overridable via KICADMGR_POINTER (used by tests and power users).
    """
    override = os.environ.get("KICADMGR_POINTER")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "KiCadLibraryManager" / "workspace.json"


def read_pointer() -> Optional[Path]:
    """The chosen library location, or None if unset or no longer a writable dir."""
    try:
        data = json.loads(pointer_path().read_text(encoding="utf-8"))
        loc = Path(data["library_location"])
    except Exception:
        return None
    return loc if (loc.is_dir() and _can_write_dir(loc)) else None


def write_pointer(location: Path) -> None:
    """Record the chosen library location in the pointer file."""
    p = pointer_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"library_location": str(location)}), encoding="utf-8")


def library_location() -> Optional[Path]:
    """The writable working dir.

    Dev (not frozen): the repo root, exactly as before. Frozen: the pointer's
    location if it exists and is writable, else None — the signal for startup to
    run the first-run 'Choose Library Location' flow (see ensure_library_location).
    """
    if not getattr(sys, "frozen", False):
        return detect_repo_root()
    return read_pointer()


def seed_library(dest: Path, seed_root: Path = None,
                 seed_version: str = SEED_VERSION, force: bool = False) -> bool:
    """Copy the bundled seed library into a fresh, user-chosen location.

    Idempotent and marked: a .seed_version file records the seeded snapshot, so a
    re-seed only happens on force or a SEED_VERSION bump. Returns True if seeding
    ran, False if the location was already at this seed version.
    """
    dest = Path(dest)
    marker = dest / ".seed_version"
    if not force and marker.exists() and marker.read_text(encoding="utf-8").strip() == seed_version:
        return False
    seed_root = Path(seed_root) if seed_root is not None else bundle_path("seed")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("libs", "catalog_assets"):
        src = seed_root / name
        if src.is_dir():
            shutil.copytree(src, dest / name, dirs_exist_ok=True)
    cfg_path = dest / "config.json"
    if force or not cfg_path.exists():
        cfg_path.write_text(json.dumps({"RepoRoot": str(dest)}, indent=2), encoding="utf-8")
    marker.write_text(seed_version, encoding="utf-8")
    return True


def _prompt_choose_location(parent=None) -> Optional[Path]:
    """First-run modal: Open Existing / Create New (seeded). Returns the chosen
    location, or None if the user quit. UI-only; the pure logic is in seed_library
    / write_pointer, which are unit-tested."""
    from PyQt5.QtWidgets import QMessageBox, QFileDialog
    box = QMessageBox(parent)
    box.setWindowTitle("Choose Library Location")
    box.setText("Where should KiCad Library Manager keep your library?")
    box.setInformativeText(
        "Open an existing library folder (e.g. a git clone), or create a new one "
        "seeded from the bundled snapshot. You can change this later in Settings.")
    open_btn = box.addButton("Open Existing", QMessageBox.AcceptRole)
    new_btn = box.addButton("Create New…", QMessageBox.AcceptRole)
    box.addButton("Quit", QMessageBox.RejectRole)
    box.exec_()
    clicked = box.clickedButton()
    if clicked is open_btn:
        d = QFileDialog.getExistingDirectory(parent, "Open existing library folder")
        return Path(d) if d else None
    if clicked is new_btn:
        d = QFileDialog.getExistingDirectory(parent, "Choose a folder for a new library")
        if not d:
            return None
        dest = Path(d)
        seed_library(dest)   # copy bundled seed + write a fresh config.json
        return dest
    return None


def ensure_library_location(parent=None) -> Optional[Path]:
    """The writable library location, prompting on first run when frozen.

    Dev: the repo root (no prompt). Frozen: the pointer's location if valid, else
    the first-run modal — on success the choice is recorded in the pointer. Returns
    None only if the user quit the first-run modal (startup should then exit).
    """
    loc = library_location()
    if loc is not None:
        return loc
    chosen = _prompt_choose_location(parent)
    if chosen is None or not _can_write_dir(chosen):
        return None
    write_pointer(chosen)
    return chosen


def derive_paths(repo_root: Path) -> Dict[str, str]:
    """Build the full config dict from a repo root."""
    libs = repo_root / "libs"
    return {
        "RepoRoot":     str(repo_root),
        "Downloads":    str(repo_root / "downloads"),
        "Libs":         str(libs),
        "SymbolLib":    str(libs / "MySymbols.kicad_sym"),
        "FootprintLib": str(libs / "MyFootprints.pretty"),
        "ModelLib":     str(libs / "My3DModels"),
        "MiscDir":      str(repo_root / "misc"),
        "LogFile":      str(repo_root / "tools" / "ui_python.log"),
        "PythonExe":    sys.executable,
    }


def _can_write_dir(path: Path) -> bool:
    """Probe *real* writability by creating the dir and a temp file.

    os.access() is unreliable on Windows (it ignores ACLs), so we actually try
    to write. This is what lets the app reject another user's protected folder
    instead of failing later with 'Permission denied'.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    probe = path / ".kicadmgr_write_test.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        try:
            if probe.exists():
                probe.unlink()
        except Exception:
            pass
        return False


try:                                    # single source of truth; CI stamps the tag
    from app_build import VERSION as APP_VERSION
except Exception:                       # noqa: BLE001
    APP_VERSION = "dev"

REPO_ROOT = detect_repo_root()
DEFAULTS: Dict[str, str] = derive_paths(REPO_ROOT)
CONFIG_PATH = REPO_ROOT / "tools" / "config.json"


def apply_library_location(loc: Path) -> None:
    """Rebind the module path globals to a resolved library location (frozen).

    Startup calls this once the first-run flow (or the pointer) has produced a
    writable location, so load_config/save and derive_paths all operate on it.
    Frozen config.json lives at the location root; dev keeps tools/config.json.
    In dev this is unnecessary (the globals already point at the repo root).
    """
    global REPO_ROOT, DEFAULTS, CONFIG_PATH
    loc = Path(loc)
    REPO_ROOT = loc
    DEFAULTS = derive_paths(loc)
    CONFIG_PATH = loc / "config.json"


# -----------------------------
# Utilities / logging
# -----------------------------
def load_config(config_path: Optional[Path] = None) -> Dict[str, str]:
    # A persisted RepoRoot (written by save_repo_root/change_path) wins when it
    # is present AND genuinely usable (exists + writable); otherwise we derive
    # every path from this script's/exe's own location, so the app still works
    # regardless of which machine/user/clone it runs from. config.json may also
    # override Downloads/PythonExe, but a Downloads override is honored only if
    # it is genuinely writable (a stale path to another user's folder is
    # ignored, not honored). config_path defaults to the module CONFIG_PATH;
    # it is a seam so tests (and any future multi-root caller) can point the
    # loader at an arbitrary config file. Backward compatible: existing
    # callers keep calling load_config() with no arguments.
    path = Path(config_path) if config_path is not None else CONFIG_PATH

    data: Dict = {}
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
    except Exception as e:
        print(f"WARNING: failed to read config.json: {e}")
        data = {}

    # Honor a persisted RepoRoot only when it resolves to a real, writable
    # directory. A stale path (another machine/user, deleted checkout) is
    # ignored so the app falls back to the portable exe/script derivation
    # instead of pointing the whole app at a folder that does not exist.
    root = REPO_ROOT
    persisted_root = data.get("RepoRoot")
    if persisted_root:
        pr = Path(persisted_root)
        try:
            usable = pr.exists() and pr.is_dir() and _can_write_dir(pr)
        except Exception:
            usable = False
        if usable:
            root = pr

    cfg = derive_paths(root)

    try:
        dl = data.get("Downloads")
        if dl and Path(dl).resolve() != Path(cfg["Downloads"]).resolve() and _can_write_dir(Path(dl)):
            cfg["Downloads"] = str(Path(dl))
        if data.get("PythonExe"):
            cfg["PythonExe"] = data["PythonExe"]
        if data.get("MouserApiKey"):
            cfg["MouserApiKey"] = data["MouserApiKey"]    # secret; gitignored config only
    except Exception as e:
        print(f"WARNING: failed to apply config.json overrides: {e}")

    # Ensure directories exist
    for key in ("RepoRoot", "Downloads", "Libs", "FootprintLib", "ModelLib", "MiscDir"):
        p = Path(cfg[key])
        p.mkdir(parents=True, exist_ok=True)
   
    # Ensure symbol lib exists
    sym_path = Path(cfg["SymbolLib"])
    sym_path.parent.mkdir(parents=True, exist_ok=True)
    if not sym_path.exists():
        sym_path.write_text(
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
            encoding="utf-8", newline="\n"
        )
    return cfg


def _atomic_write_json(path: Path, data) -> None:
    """Write JSON durably: serialize to a sibling .tmp, then os.replace() (atomic on
    POSIX and Windows). A crash or power loss mid-write can never truncate the real
    file — the worst case is a leftover .tmp, not a lost config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_config(cfg: Dict[str, str]):
    try:
        _atomic_write_json(CONFIG_PATH, cfg)
    except Exception as e:
        print(f"WARNING: failed to write config.json: {e}")


def read_setting(key: str, default=None, config_path: Optional[Path] = None):
    """Read a single raw value straight from config.json (the on-disk file, NOT the
    derived runtime cfg). load_config() intentionally re-derives every path and drops
    UI preferences like Theme, so a persisted pref must be read from the raw file.
    Returns `default` when the file/key is absent or unreadable. config_path is a
    test/injection seam (defaults to the module CONFIG_PATH)."""
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and key in data:
                return data[key]
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: failed to read {key} from config.json: {e}")
    return default


def write_setting(key: str, value, config_path: Optional[Path] = None) -> bool:
    """Persist a single value into config.json, preserving every other key (update in
    place; creates the file if needed). Returns True on success. For UI preferences
    (Theme, ...) that must survive a restart but are not path-derived."""
    path = Path(config_path) if config_path is not None else CONFIG_PATH
    data: Dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001
            data = {}
    data[key] = value
    try:
        _atomic_write_json(path, data)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: failed to write {key} to config.json: {e}")
        return False


def save_repo_root(cfg: Dict[str, str], new_root, config_path: Optional[Path] = None) -> bool:
    """Persist a new RepoRoot into config.json so it survives an app restart.

    Audit gap (medium): change_path re-derived every path from the new root and
    called save_config(), but the OLD load_config() ignored the persisted
    RepoRoot and always re-derived it from the exe/script location — so a user's
    root change was silently reverted on the next launch. This writes RepoRoot
    into config.json (creating the file, or updating it in place while
    preserving every other key) after validating that new_root exists and is a
    writable directory. Returns True on success, False if the root is
    invalid/not writable or the write failed.

    Pure persistence + validation only: it also updates cfg["RepoRoot"] in
    memory for immediate consistency, but it does NOT re-derive the other paths,
    restart the watcher, or touch the log. The UI layer that wires this should
    call derive_paths(new_root) to rebuild the rest of cfg (exactly as
    change_path already does) and refresh any live views/watchers.

    config_path defaults to the module CONFIG_PATH; it is a test/injection seam.
    """
    root = Path(new_root)
    # Validate BEFORE writing: never persist a root that would break the app.
    try:
        if not (root.exists() and root.is_dir()):
            return False
        if not _can_write_dir(root):
            return False
    except Exception:
        return False

    path = Path(config_path) if config_path is not None else CONFIG_PATH

    # Update-in-place: preserve any other keys already in config.json.
    data: Dict = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    data["RepoRoot"] = str(root)
    try:
        _atomic_write_json(path, data)
    except Exception as e:
        print(f"WARNING: failed to persist RepoRoot: {e}")
        return False

    if isinstance(cfg, dict):
        cfg["RepoRoot"] = str(root)
    return True


class UILog(QObject):
    """Logger that writes to a file and the GUI log pane.

    Safe to call from ANY thread: the file write is guarded by a lock, and the
    GUI append is marshalled to the main thread through a Qt signal (when the
    signal is emitted from a worker thread, Qt delivers it as a queued call on
    the thread that owns this object — the GUI thread). This is what makes the
    async git workers and the file watcher safe.
    """
    _append = pyqtSignal(str)

    def __init__(self, text_widget: QTextEdit, logfile: Path):
        super().__init__()
        self.text = text_widget
        self.file = logfile
        self._lock = threading.Lock()
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if not self.file.exists():
            self.file.touch()
        self._append.connect(self._do_append)

    def _do_append(self, line: str):
        """Runs on the GUI thread."""
        self.text.append(line)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def write(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        with self._lock:
            try:
                with open(self.file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        self._append.emit(line)


# -----------------------------
# Core: merge symbols
# -----------------------------
def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")

def write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8", newline="\n")

def ensure_target_header(target_path: Path):
    if not target_path.exists():
        write_text(target_path, '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n')

def extract_symbol_blocks(src_text: str) -> List[str]:
    """
    Returns list of full '(symbol ...)' blocks from a .kicad_sym file.
    Simple balanced-paren scanner, tolerates quoted strings.
    """
    blocks: List[str] = []
    s = src_text
    n = len(s)
    i = 0
    while i < n:
        ch = s[i]
        if ch == '"':                       # top-level string: skip it (escape-aware)
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if ch == "(" and s.startswith("(symbol", i):
            start = i
            j = i
            depth = 0
            captured = False
            while j < n:
                cj = s[j]
                if cj == '"':               # string inside the block (KiCad \" escapes)
                    j += 1
                    while j < n and s[j] != '"':
                        j += 2 if s[j] == "\\" else 1
                    j += 1
                    continue
                if cj == "(":
                    depth += 1
                elif cj == ")":
                    depth -= 1
                    if depth == 0:
                        blocks.append(s[start:j + 1])
                        i = j + 1
                        captured = True
                        break
                j += 1
            if not captured:                # unbalanced input: advance, never re-scan forever
                i = start + 1
            continue
        i += 1
    return blocks

def extract_symbol_raw_name(block: str) -> str:
    """The FULL symbol id including any `lib:` prefix — the de-dup identity.

    KiCad symbol ids are unique as written, so two genuinely different source symbols
    can share a suffix after the last colon (VendorA:R_0402 vs VendorB:R_0402). Keying
    de-dup on the RAW id keeps them distinct; only display strips the prefix."""
    head = block.splitlines()[0]
    try:
        if '(symbol "' in head:
            start = head.index('(symbol "') + len('(symbol "')
            end = head.index('"', start)
            return head[start:end]
        if '(name "' in block:
            start = block.index('(name "') + len('(name "')
            end = block.index('"', start)
            return block[start:end]
    except Exception:
        pass
    return head.strip()


def extract_symbol_name(block: str) -> str:
    """The DISPLAY name for a symbol block — the part after any `lib:` prefix. Use
    extract_symbol_raw_name for de-dup/identity, which must not collapse distinct ids."""
    raw = extract_symbol_raw_name(block)
    return raw.split(":")[-1]

def insert_blocks_into_target(target_text: str, blocks: List[str]) -> str:
    """Insert blocks just before the top-level closing paren.

    The paren scan skips quoted strings (honoring KiCad's \\-escapes) so a
    description like "smiley :)" can't drive depth to 0 early and splice the new
    blocks into the middle of a symbol. Mirrors extract_symbol_blocks' scanner.
    """
    s = target_text
    n = len(s)
    depth = 0
    last_close = None
    i = 0
    while i < n:
        ch = s[i]
        if ch == '"':                       # quoted string: skip it (escape-aware)
            i += 1
            while i < n and s[i] != '"':
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                last_close = i
        i += 1
    if last_close is None:
        body = "\n".join(blocks)
        return f'(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n{body}\n)\n'
    return target_text[:last_close] + "\n" + "\n".join(blocks) + "\n" + target_text[last_close:]

# Keep at most this many undo snapshots in libs/.trash/. Every destructive symbol-lib
# rewrite drops a full-library copy there; without a cap the folder grows without bound
# across a working session. 20 covers a deep undo history while staying tiny on disk.
_TRASH_KEEP = 20


def _trash_dir(symbol_lib_path: Path) -> Path:
    """The libs/.trash/ folder that holds undo snapshots for this symbol library."""
    return Path(symbol_lib_path).parent / ".trash"


def list_trash_snapshots(symbol_lib_path: Path) -> List[Path]:
    """Undo snapshot dirs under libs/.trash/, NEWEST first. Names are %Y%m%d_%H%M%S so a
    reverse lexical sort is chronological. Non-timestamp junk is ignored. Never raises."""
    trash = _trash_dir(symbol_lib_path)
    if not trash.exists():
        return []
    try:
        snaps = [d for d in trash.iterdir()
                 if d.is_dir() and re.fullmatch(r"\d{8}_\d{6}", d.name)]
    except OSError:
        return []
    return sorted(snaps, key=lambda d: d.name, reverse=True)


def _prune_trash(symbol_lib_path: Path, keep: Optional[int] = None) -> int:
    """Delete all but the newest `keep` undo snapshots. Returns how many were removed.
    `keep` defaults to the module-level _TRASH_KEEP (read at call time so the cap stays
    configurable). Best-effort: a snapshot that won't delete is skipped, never fatal."""
    if keep is None:
        keep = _TRASH_KEEP
    removed = 0
    for old in list_trash_snapshots(symbol_lib_path)[keep:]:
        try:
            shutil.rmtree(old)
            removed += 1
        except OSError:
            pass
    return removed


def empty_trash(symbol_lib_path: Path, log: Optional[UILog] = None) -> int:
    """Delete EVERY undo snapshot (the 'Empty Undo History' action). Returns the count
    removed. Best-effort per snapshot; never raises."""
    removed = _prune_trash(symbol_lib_path, keep=0)
    if log is not None:
        log.write(f"Emptied undo history: removed {removed} snapshot(s).")
    return removed


def restore_last_trash(symbol_lib_path: Path, log: Optional[UILog] = None) -> bool:
    """Restore the symbol library from its most recent undo snapshot (one copy-back).
    Returns True if a snapshot was found and copied over the live file. The snapshot is
    left in place so an accidental restore is itself undoable. Never raises."""
    for snap in list_trash_snapshots(symbol_lib_path):
        candidate = snap / Path(symbol_lib_path).name
        if candidate.exists():
            try:
                shutil.copy2(candidate, symbol_lib_path)
            except OSError as e:
                if log is not None:
                    log.write(f"Restore failed: {e}")
                return False
            if log is not None:
                log.write(f"Restored {Path(symbol_lib_path).name} from undo snapshot "
                          f"{snap.name}.")
            return True
    if log is not None:
        log.write("Restore: no undo snapshot available.")
    return False


def _snapshot_then_write(symbol_lib_path: Path, new_text: str, log: UILog):
    """Destructive symbol-library rewrite with an undo copy: snapshot the current
    file into libs/.trash/<timestamp>/ first, then write, then prune the trash to the
    newest _TRASH_KEEP snapshots so it can't grow unbounded. A failed snapshot logs and
    continues (the user asked for the operation); restore_last_trash() is the copy-back,
    empty_trash() clears the history."""
    try:
        src = Path(symbol_lib_path)
        if src.exists():
            from datetime import datetime as _dt
            dst_dir = src.parent / ".trash" / _dt.now().strftime("%Y%m%d_%H%M%S")
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / src.name)
            _prune_trash(src)
    except Exception as e:                     # noqa: BLE001
        log.write(f"Trash snapshot failed (continuing): {e}")
    write_text(symbol_lib_path, new_text)


def remove_symbol_by_name(symbol_lib_path: Path, name: str, log: UILog) -> bool:
    """Remove a symbol block by name from the .kicad_sym library"""
    try:
        text = read_text(symbol_lib_path)
        blocks = extract_symbol_blocks(text)
        new_blocks: List[str] = []
        removed = False
        for b in blocks:
            nm = extract_symbol_name(b)
            if nm == name:
                removed = True
            else:
                new_blocks.append(b)
        if not removed:
            return False
        new_text = insert_blocks_into_target(
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
            new_blocks
        )
        _snapshot_then_write(symbol_lib_path, new_text, log)
        log.write(f"Deleted symbol '{name}' from {symbol_lib_path.name}")
        return True
    except Exception as e:
        log.write(f"ERROR deleting symbol '{name}': {e}")
        return False


def dedupe_symbol_library(symbol_lib_path: Path, log: UILog) -> int:
    """Rewrite the symbol library keeping only the FIRST block of each name.

    Returns the number of duplicate blocks removed.
    """
    try:
        with _LIB_LOCK:                      # never interleave with a watcher import
            text = read_text(symbol_lib_path)
            blocks = extract_symbol_blocks(text)
            seen: set = set()
            kept: List[str] = []
            removed = 0
            for b in blocks:
                nm = extract_symbol_name(b)
                if nm in seen:
                    removed += 1
                    continue
                seen.add(nm)
                kept.append(b)
            if removed:
                new_text = insert_blocks_into_target(
                    '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                    kept
                )
                _snapshot_then_write(symbol_lib_path, new_text, log)
                log.write(f"Removed {removed} duplicate symbol(s); kept {len(kept)} unique.")
            else:
                log.write("No duplicate symbols to remove.")
            return removed
    except Exception as e:
        log.write(f"ERROR removing duplicates: {e}")
        return 0


def remove_symbols_by_indices(symbol_lib_path: Path, expected: Dict[int, str],
                              log: UILog) -> int:
    """Remove several symbol blocks at once, identified by file position.

    `expected` maps block-index -> expected name. Removing them in a single pass
    avoids the index-shifting bug you'd hit deleting one at a time. If any index
    no longer matches its expected name (library changed since the scan), the
    whole operation aborts. Returns the number removed.
    """
    try:
        text = read_text(symbol_lib_path)
        blocks = extract_symbol_blocks(text)
        idxset = {i for i in expected if 0 <= i < len(blocks)}
        for i in idxset:
            if extract_symbol_name(blocks[i]) != expected[i]:
                log.write("WARN bulk symbol delete aborted: library changed — "
                          "refresh and retry.")
                return 0
        kept = [b for k, b in enumerate(blocks) if k not in idxset]
        removed = len(blocks) - len(kept)
        if removed:
            new_text = insert_blocks_into_target(
                '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                kept
            )
            _snapshot_then_write(symbol_lib_path, new_text, log)
            log.write(f"Deleted {removed} symbol(s).")
        return removed
    except Exception as e:
        log.write(f"ERROR bulk-deleting symbols: {e}")
        return 0


def find_kicad_dir() -> Optional[Path]:
    """KiCad's bin directory — delegates to the shared locator."""
    from kicad_paths import find_kicad_bin
    return find_kicad_bin()


# ---------------------------------------------------------------------------
# KiCad-sync repair — make placed parts resolve their footprint + 3D model.
#
# On import, parts were copied into the shared library verbatim, so:
#   * symbols kept the vendor's footprint nickname (or a bare name), not
#     "MyFootprints:<name>", so the footprint did not resolve when placed, and
#   * footprints got no "(model ...)" line, so no 3D model attached.
# These helpers rewrite those cross-references to the shared library, and
# register MySymbols / MyFootprints / ${MY3DMODELS} in KiCad's config so the
# references resolve. Self-contained (no external backend dependency).
# ---------------------------------------------------------------------------
FP_NICKNAME = "MyFootprints"
MODEL_VAR = "MY3DMODELS"
MODEL_VAR_REF = "${MY3DMODELS}"

_FP_PROP_RE = re.compile(r'(\(property\s+"Footprint"\s+")([^"]*)(")')
_MODEL_PATH_RE = re.compile(r'(\(model\s+)("[^"]*"|[^"\s)]+)')


def footprint_name(value: str) -> str:
    """Footprint name with any library nickname stripped.
    'STUSB4500QTR:QFN50…' -> 'QFN50…'; bare 'RM_10_ADI' -> itself."""
    value = (value or "").strip()
    return value.split(":")[-1] if value else ""


def qualify_footprint(value: str, nickname: str = FP_NICKNAME) -> str:
    """Return '<nickname>:<footprintName>' for the shared lib (idempotent)."""
    name = footprint_name(value)
    return f"{nickname}:{name}" if name else ""


def rewrite_symbol_footprint(symbol_text: str, nickname: str = FP_NICKNAME) -> str:
    """Rewrite the Footprint property inside a symbol block to the shared lib."""
    def repl(m: "re.Match") -> str:
        return m.group(1) + qualify_footprint(m.group(2), nickname) + m.group(3)
    return _FP_PROP_RE.sub(repl, symbol_text, count=1)


def set_symbol_property(symbol_text: str, key: str, value: str) -> str:
    """Set (or add) one symbol property, regex-precise. Replaces the value in place if
    the property exists, else inserts a new hidden property after the Value line. The
    writer half of enrich-from-MPN; mirrors rewrite_symbol_footprint's precision."""
    val = str(value).replace("\\", "\\\\").replace('"', '\\"')
    pat = re.compile(r'(\(property\s+"' + re.escape(key) + r'"\s+")((?:[^"\\]|\\.)*)(")')
    if pat.search(symbol_text):
        return pat.sub(lambda m: m.group(1) + val + m.group(3), symbol_text, count=1)
    # Insert a new property BEFORE the first existing one — property order is free in
    # KiCad, and this needs no paren-matching (anchoring after a property's end is
    # fragile on compact single-line symbols where the regex over-runs the block).
    anchor = re.search(r'\(property\s+"', symbol_text)
    if not anchor:
        return symbol_text
    ins = (f'(property "{key}" "{val}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))'
           "\n    ")
    return symbol_text[:anchor.start()] + ins + symbol_text[anchor.start():]


# Enrich field -> the symbol property that carries it (write-back). Volatile data
# (stock/price/lifecycle) stays in the sourcing REPORT, not the symbol.
_ENRICH_PROPERTY = {"manufacturer": "MANUFACTURER", "datasheet": "Datasheet",
                    "description": "Description", "mpn": "Value",
                    "mouser_pn": "Mouser Part Number"}


def enrich_symbol(symbol_text: str, lookup_result: dict,
                  fields=("manufacturer", "datasheet", "description", "mouser_pn")) -> tuple:
    """Fill BLANK identity/ordering properties of one symbol from a lookup result.
    Never overwrites a property that already holds a real value (checks the actual
    property, so it is fill-blanks-only for every field). Returns
    (new_text, [(field, value)])."""
    props = extract_symbol_properties(symbol_text)
    changed = []
    for f in fields:
        newv = (lookup_result or {}).get(f)
        prop = _ENRICH_PROPERTY.get(f)
        if not (newv and prop):
            continue
        cur = (props.get(prop) or "").strip()
        if cur and cur.lower() not in _PLACEHOLDERS:
            continue                                 # already has a real value
        symbol_text = set_symbol_property(symbol_text, prop, str(newv))
        changed.append((f, str(newv)))
    return symbol_text, changed


def library_sourcing_report(cfg: Dict[str, str], lookup, throttle: float = 0.0) -> dict:
    """Look up every orderable library part on the distributor and report sourcing
    health — the payoff of a Mouser key for the library. For each part with a real MPN
    it records lifecycle (flagging NRND / obsolete / EOL), stock (flagging out-of-stock),
    unit price, Mouser P/N, lead time, and a suggested replacement for dying parts, then
    a summary + a shareable markdown report. `lookup` is a make_mouser_lookup callable;
    results cache per MPN. Read-only (writes nothing). throttle>0 sleeps between calls if
    the free-tier rate limit bites."""
    import time
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not sym_path.exists():
        return {"rows": [], "counts": {}, "markdown": "No symbol library."}
    parts = []
    for b in extract_symbol_blocks(read_text(sym_path)):
        props = extract_symbol_properties(b)
        ident = part_identity(props, fallback=extract_symbol_name(b))
        mpn = strict_mpn(props) or (ident["mpn"] if ident["manufacturer"] else None)
        if mpn:
            parts.append((extract_symbol_name(b), mpn))

    seen, rows = {}, []
    for name, mpn in parts:
        if mpn not in seen:
            seen[mpn] = lookup(mpn)
            if throttle:
                time.sleep(throttle)
        res = seen[mpn]
        if not res:
            rows.append({"symbol": name, "mpn": mpn, "found": False})
            continue
        life = (res.get("lifecycle") or "").lower()
        obsolete = any(w in life for w in
                       ("obsolete", "eol", "end of life", "nrnd", "not recommended", "discontinued"))
        source = res.get("source", "Mouser")         # chain tags the provider that found it
        rows.append({"symbol": name, "mpn": mpn, "found": True, "source": source,
                     "on_mouser": source == "Mouser",
                     "mouser_pn": res.get("mouser_pn"), "manufacturer": res.get("manufacturer"),
                     "lifecycle": res.get("lifecycle"), "stock": res.get("stock") or 0,
                     "unit_price": res.get("unit_price"), "lead_time": res.get("lead_time"),
                     "obsolete": obsolete, "in_stock": (res.get("stock") or 0) > 0,
                     "suggested_replacement": res.get("suggested_replacement")})

    counts = {"parts": len(rows),
              "found": sum(1 for r in rows if r["found"]),
              "not_found": sum(1 for r in rows if not r["found"]),
              "on_mouser": sum(1 for r in rows if r.get("on_mouser")),
              "not_on_mouser": sum(1 for r in rows if r["found"] and not r.get("on_mouser")),
              "obsolete_nrnd": sum(1 for r in rows if r.get("obsolete")),
              "out_of_stock": sum(1 for r in rows if r["found"] and not r.get("in_stock"))}

    manual = counts["not_found"] + counts["not_on_mouser"]
    L = ["# Library Sourcing", "",
         f"**{counts['on_mouser']} / {counts['parts']} on Mouser**: "
         f"{counts['obsolete_nrnd']} obsolete/NRND, {counts['out_of_stock']} out of stock, "
         f"{manual} to source manually.", ""]
    flags = [r for r in rows if r.get("obsolete") or (r["found"] and not r.get("in_stock"))]
    if flags:
        L += ["## Needs attention", ""]
        for r in flags:
            why = []
            if r.get("obsolete"):
                why.append(f"lifecycle {r.get('lifecycle')}")
            if not r.get("in_stock"):
                why.append("out of stock")
            rep = f" (replace with {r['suggested_replacement']})" if r.get("suggested_replacement") else ""
            L.append(f"- **{r['symbol']}** ({r['mpn']}): {', '.join(why)}{rep}")
        L.append("")
    elsewhere = [r for r in rows if r["found"] and not r.get("on_mouser")]
    if elsewhere:
        L += ["## Not on Mouser (found on a fallback)", ""]
        L += [f"- {r['symbol']} ({r['mpn']}) via {r['source']}" for r in elsewhere] + [""]
    nf = [r for r in rows if not r["found"]]
    if nf:
        L += ["## To source manually (not on Mouser)", ""]
        L += [f"- {r['symbol']} ({r['mpn']})" for r in nf] + [""]
    return {"rows": rows, "counts": counts, "markdown": "\n".join(L) + "\n"}


def enrich_library(cfg: Dict[str, str], lookup, log: UILog = None,
                   fields=("manufacturer", "datasheet", "description", "mouser_pn"),
                   dry_run: bool = True) -> dict:
    """Enrich every symbol's BLANK identity/ordering fields from a distributor lookup.

    `lookup(mpn) -> {...}` (or None). Safe by construction: fills blanks only, matches
    on the symbol's OWN existing MPN, runs under _LIB_LOCK, and snapshots the library to
    .trash before any write. A symbol whose target properties are ALL already filled is
    skipped WITHOUT an API call, so repeated runs (e.g. after each ZIP import) only query
    the genuinely new/incomplete parts. dry_run=True (default) computes the changes
    without writing. Returns {changes, written, symbols, looked_up}."""
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not sym_path.exists():
        return {"error": "no symbol library", "changes": [], "written": False,
                "symbols": 0, "looked_up": 0}
    with _LIB_LOCK:
        text = read_text(sym_path)
        blocks = extract_symbol_blocks(text)
        new_blocks = list(blocks)                     # positional rewrite (no text.replace)
        changes, looked_up, any_edit = [], 0, False
        for idx, b in enumerate(blocks):
            name = extract_symbol_name(b)
            props = extract_symbol_properties(b)
            ident = part_identity(props, fallback=name)
            mpn = ident.get("mpn")
            if not mpn:
                continue
            # only spend an API call if a target property is actually blank
            needs = any(not (props.get(_ENRICH_PROPERTY.get(f, "")) or "").strip()
                        or (props.get(_ENRICH_PROPERTY.get(f, "")) or "").strip().lower()
                        in _PLACEHOLDERS for f in fields)
            if not needs:
                continue
            looked_up += 1
            res = lookup(mpn)
            if not res:
                continue
            nb, filled = enrich_symbol(b, res, fields)
            if filled:
                changes.append({"symbol": name, "mpn": mpn, "filled": filled,
                                "source": res.get("source", "")})
                new_blocks[idx] = nb                  # substitute by index, not by text
                any_edit = True
        written = False
        if not dry_run and any_edit:
            # Rebuild positionally from the substituted blocks — immune to a block whose
            # text is a byte-for-byte substring of another region (text.replace footgun).
            new_text = insert_blocks_into_target(
                '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                new_blocks)
            _snapshot_then_write(sym_path, new_text, log or _NullLog())
            written = True
        return {"changes": changes, "written": written, "symbols": len(blocks),
                "looked_up": looked_up}


class _NullLog:
    def write(self, *_a, **_k):
        pass


# Human detail-field label -> the KiCad symbol property that carries it. The
# inline editor (Library detail pane) writes through this map so the UI never
# hard-codes property names. Mirrors _ENRICH_PROPERTY but keyed by the label the
# user sees.
EDITABLE_SYMBOL_FIELDS = {
    "Value": "Value",
    "Manufacturer": "MANUFACTURER",
    "Description": "Description",
    "Mouser Part Number": "Mouser Part Number",
    "Datasheet": "Datasheet",
}


def set_library_symbol_property(cfg: Dict[str, str], symbol_names, prop_key: str,
                                value: str, log: UILog = None) -> bool:
    """Write ONE property onto the named symbol(s) in the configured library, in
    place. `symbol_names` is a symbol name or an iterable of names (every symbol
    of a grouped part is updated together so its identity stays consistent).
    Snapshot-then-write under _LIB_LOCK. Returns True if the file changed."""
    names = {symbol_names} if isinstance(symbol_names, str) else set(symbol_names or ())
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not names or not prop_key or not sym_path.exists():
        return False
    with _LIB_LOCK:
        text = read_text(sym_path)
        blocks = extract_symbol_blocks(text)
        new_blocks = list(blocks)                     # positional rewrite (no text.replace)
        changed = False
        for idx, b in enumerate(blocks):
            if extract_symbol_name(b) in names:
                nb = set_symbol_property(b, prop_key, value)
                if nb != b:
                    new_blocks[idx] = nb
                    changed = True
        if changed:
            # Rebuild positionally so a block whose text is a substring of another
            # region can never be spliced in the wrong place (text.replace footgun).
            new_text = insert_blocks_into_target(
                '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                new_blocks)
            _snapshot_then_write(sym_path, new_text, log or _NullLog())
        return changed


def set_library_symbol_footprint(cfg: Dict[str, str], symbol_names, fp_stem: str,
                                 log: UILog = None) -> bool:
    """Point the named symbol(s) at a footprint in the shared library by stem
    (writes the qualified `MyFootprints:<stem>` Footprint property)."""
    if not fp_stem:
        return False
    return set_library_symbol_property(
        cfg, symbol_names, "Footprint", qualify_footprint(fp_stem), log)


def install_model_file(cfg: Dict[str, str], src, log: UILog = None) -> Optional[str]:
    """Copy a dropped-in 3D model (.step/.stp/.wrl) into ModelLib without
    clobbering a different file. Returns the installed filename, or None."""
    src = Path(src)
    dst_dir = Path(cfg.get("ModelLib", ""))
    if not src.exists() or src.suffix.lower() not in (".step", ".stp", ".wrl") or not str(dst_dir):
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    res = safe_install(src, dst_dir / src.name, log or _NullLog(), "3D model")
    return src.name if res in ("copied", "identical") else None


def install_footprint_file(cfg: Dict[str, str], src, log: UILog = None) -> Optional[str]:
    """Copy a dropped-in footprint (.kicad_mod) into FootprintLib without
    clobbering a different file. Returns the installed stem, or None."""
    src = Path(src)
    dst_dir = Path(cfg.get("FootprintLib", ""))
    if not src.exists() or src.suffix.lower() != ".kicad_mod" or not str(dst_dir):
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    res = safe_install(src, dst_dir / src.name, log or _NullLog(), "footprint")
    return src.stem if res in ("copied", "identical") else None


def install_symbol_file(cfg: Dict[str, str], src, log: UILog = None) -> bool:
    """Merge a dropped-in symbol library (.kicad_sym) into the shared symbol
    library (duplicate-safe). Returns True when a merge was attempted."""
    src = Path(src)
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not src.exists() or src.suffix.lower() != ".kicad_sym" or not str(sym_path):
        return False
    merge_symbols(sym_path, [src], log or _NullLog())
    return True


def git_commit_push(cfg: Dict[str, str], log: UILog, message: str) -> bool:
    """Non-interactive stage+commit+push for library mutations (drop-in / inline
    edit / import). Corruption-guarded (git_stage_commit refuses to commit corrupt
    files). Returns False when nothing was committed, or when a diverged remote
    blocks the push (LIB-13).

    LIB-13 — ff pull-before-push: after committing, fast-forward any collaborator
    commits in before pushing so a multi-user drop-in fast-forwards instead of
    getting rejected by the remote. If the pull can NOT fast-forward *because the
    remote genuinely advanced* (we're behind), surface it and DON'T push — the
    local commit is kept intact for manual resolution, never clobbered. A pull
    that fails for any other reason (no upstream / offline) is not divergence, so
    we fall through to a best-effort push exactly as before."""
    if not git_stage_commit(cfg, log, message=message):
        return False
    if git_pull(cfg, log):                      # ff-only; True == up-to-date or fast-forwarded
        git_push(cfg, log)
        return True
    # Pull didn't fast-forward. Only a real divergence (behind the remote) should
    # block the push; a missing upstream or an offline remote should not.
    ab = nd_git.ahead_behind(cfg["RepoRoot"])   # (ahead, behind) or None (no upstream)
    if ab and ab[1] > 0:
        log.write("Remote has changes that could not be fast-forwarded in — your "
                  "commit is saved locally but was NOT pushed. Pull/merge the "
                  "remote changes manually, then push.")
        return False
    git_push(cfg, log)                          # no upstream / offline: push as before
    return True


def _parse_mouser_part(p: dict) -> dict:
    """One Mouser API part -> our normalized field dict."""
    breaks = p.get("PriceBreaks") or []
    try:
        stock = int(p.get("AvailabilityInStock") or 0)
    except (TypeError, ValueError):
        stock = 0
    # Full price-break ladder [{qty, price}, ...] ascending, so extended cost can be
    # priced at the real build quantity (volume breaks), not just the qty-1 price.
    ladder = []
    for b in breaks:
        q, pr = b.get("Quantity"), _coerce_price(b.get("Price"))
        try:
            q = int(q)
        except (TypeError, ValueError):
            continue
        if pr is not None:
            ladder.append({"qty": q, "price": pr})
    ladder.sort(key=lambda x: x["qty"])
    return {"mpn": p.get("ManufacturerPartNumber"),
            "manufacturer": p.get("Manufacturer"),
            "datasheet": p.get("DataSheetUrl"),
            "description": p.get("Description"),
            "mouser_pn": p.get("MouserPartNumber"),
            "category": p.get("Category"),
            "lifecycle": p.get("LifecycleStatus") or "Active",   # null = active
            "rohs": p.get("ROHSStatus"),
            "stock": stock,
            "lead_time": p.get("LeadTime"),
            "unit_price": (breaks[0].get("Price") if breaks else None),
            "price_breaks": ladder,
            "url": p.get("ProductDetailUrl"),
            "suggested_replacement": p.get("SuggestedReplacement")}


def _mouser_request(endpoint: str, api_key: str, payload: dict, timeout: int = 8) -> dict:
    """POST to a Mouser Search endpoint and return a STRUCTURED result so callers can
    tell a genuine no-match from a transport failure::

        {"data": <parsed JSON | None>, "status": <int | None>, "error": <code>}

    error code: "" (ok) · "rate_limited" (HTTP 429) · "auth" (401/403, bad/blocked
    key) · "http" (other 4xx/5xx) · "timeout" · "network". Never raises."""
    import json as _json
    import socket
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        f"https://api.mouser.com/api/v1/search/{endpoint}?apiKey={api_key}",
        data=_json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"data": _json.loads(r.read().decode()),
                    "status": getattr(r, "status", 200), "error": ""}
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", None)
        # Mouser signals a hit daily cap as HTTP 403 with an Errors[] body of
        # code "TooManyRequests"/"MaxCallPerDay" — NOT a 429. Read the body so a
        # quota exhaustion is classified as a rate limit (recoverable, worth a
        # countdown) and not as an auth rejection (a dead key). See SRC-04.
        body = ""
        try:
            body = e.read().decode()
        except Exception:                            # noqa: BLE001
            body = ""
        low = body.lower()
        if code == 429 or "toomanyrequests" in low or "maxcallperday" in low \
                or "maximum calls" in low or "exceeded" in low:
            err = "rate_limited"
        elif code in (401, 403):
            err = "auth"
        else:
            err = "http"
        return {"data": None, "status": code, "error": err}
    except (socket.timeout, TimeoutError):
        return {"data": None, "status": None, "error": "timeout"}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        is_to = isinstance(reason, (socket.timeout, TimeoutError))
        return {"data": None, "status": None, "error": "timeout" if is_to else "network"}
    except Exception:                                # noqa: BLE001
        return {"data": None, "status": None, "error": "network"}


def _mouser_post(endpoint: str, api_key: str, payload: dict, timeout: int = 8):
    """POST to a Mouser Search API endpoint; return the parsed JSON or None (never
    raises). Thin backward-compatible shim over _mouser_request for the exact-lookup
    and bulk-report paths that only care about the payload."""
    return _mouser_request(endpoint, api_key, payload, timeout)["data"]


def make_mouser_lookup(api_key: str, timeout: int = 8):
    """A lookup(mpn) -> normalized part dict backed by the Mouser Search API. Requires a
    Mouser API key. Returns None for anything it can't resolve and NEVER raises."""
    def lookup(mpn):
        if not (api_key and mpn):
            return None
        res = _mouser_request("partnumber", api_key,
                              {"SearchByPartRequest": {"mouserPartNumber": mpn,
                                                       "partSearchOptions": "Exact"}}, timeout)
        if res.get("error") == "rate_limited":
            # Record the cap so the UI can count down to the next reset (SRC-04). Bulk
            # enrichment is the path that realistically exhausts 1000 calls/day, so
            # WITHOUT this the countdown never fires on its most common trigger.
            note_mouser_rate_limited()
            return None
        data = res.get("data")
        parts = ((data or {}).get("SearchResults") or {}).get("Parts") or []
        if not parts:
            return None
        up = mpn.strip().upper()
        p = next((x for x in parts
                  if (x.get("ManufacturerPartNumber") or "").upper() == up), parts[0])
        return _parse_mouser_part(p)
    return lookup


# ── LCSC / jlcsearch provider (key-free fallback distributor) ─────────────────
def _lcsc_request(query: str, timeout: int = 8) -> dict:
    """GET the jlcsearch community API for `query` and return a STRUCTURED result,
    mirroring _mouser_request::

        {"data": <parsed JSON | None>, "error": <code>}

    error code: "" (ok) · "rate_limited" (HTTP 429) · "http" (other 4xx/5xx) ·
    "timeout" · "network". No API key required. Never raises."""
    import json as _json
    import socket
    import urllib.parse
    import urllib.request
    import urllib.error
    url = ("https://jlcsearch.tscircuit.com/api/search?full=true&limit=20&q="
           + urllib.parse.quote(query))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {"data": _json.loads(r.read().decode()), "error": ""}
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", None)
        return {"data": None, "error": "rate_limited" if code == 429 else "http"}
    except (socket.timeout, TimeoutError):
        return {"data": None, "error": "timeout"}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        is_to = isinstance(reason, (socket.timeout, TimeoutError))
        return {"data": None, "error": "timeout" if is_to else "network"}
    except Exception:                                # noqa: BLE001
        return {"data": None, "error": "network"}


def _parse_lcsc_part(c: dict) -> dict:
    """One jlcsearch component -> our normalized field dict (same shape make_mouser_lookup
    returns), so LCSC hits feed sourcing AND price-break costing identically. jlcsearch
    reports no lifecycle, so parts default to Active."""
    extra = c.get("extra") or {}
    man = extra.get("manufacturer")
    if isinstance(man, dict):
        man = man.get("name") or ""
    elif not isinstance(man, str):
        man = ""
    ds = extra.get("datasheet")
    if isinstance(ds, dict):
        ds = ds.get("pdf") or ""
    if not ds:
        top = c.get("datasheet")
        ds = top if isinstance(top, str) else ""
    try:
        stock = int(c.get("stock") or extra.get("quantity") or 0)
    except (TypeError, ValueError):
        stock = 0
    # Price-break ladder: prefer the top-level `price` (qFrom rungs); fall back to
    # extra.prices (min_qty rungs). Ascending, unparseable rungs dropped.
    ladder = []
    for b in (c.get("price") or []):
        q, pr = b.get("qFrom"), _coerce_price(b.get("price"))
        try:
            q = int(q)
        except (TypeError, ValueError):
            continue
        if pr is not None:
            ladder.append({"qty": q, "price": pr})
    if not ladder:
        for b in (extra.get("prices") or []):
            q, pr = b.get("min_qty"), _coerce_price(b.get("price"))
            try:
                q = int(q)
            except (TypeError, ValueError):
                continue
            if pr is not None:
                ladder.append({"qty": q, "price": pr})
    ladder.sort(key=lambda x: x["qty"])
    return {"mpn": extra.get("mpn") or c.get("mfr"),
            "manufacturer": man,
            "datasheet": ds,
            "description": extra.get("description") or c.get("description") or "",
            "lcsc_pn": extra.get("number"),
            "category": "",
            "lifecycle": "Active",                   # jlcsearch has no lifecycle field
            "rohs": ("RoHS" if extra.get("rohs") else None),
            "stock": stock,
            "lead_time": None,
            "unit_price": (ladder[0]["price"] if ladder else None),
            "price_breaks": ladder,
            "url": extra.get("url"),
            "basic": c.get("basic"),
            "suggested_replacement": None}


def make_lcsc_lookup(timeout: int = 8):
    """A lookup(mpn) -> normalized part dict backed by the key-free jlcsearch API.
    Picks the component whose MPN matches exactly (case-insensitive), else the first
    result. Returns None for anything it can't resolve and NEVER raises."""
    def lookup(mpn):
        if not mpn:
            return None
        try:
            resp = _lcsc_request(mpn, timeout)
        except Exception:                            # noqa: BLE001 — a dead provider is not fatal
            return None
        comps = ((resp or {}).get("data") or {}).get("components") or []
        if not comps:
            return None
        up = str(mpn).strip().upper()

        def _mpn_of(c):
            e = c.get("extra") or {}
            return str(e.get("mpn") or c.get("mfr") or "").upper()
        c = next((x for x in comps if _mpn_of(x) == up), comps[0])
        return _parse_lcsc_part(c)
    return lookup


def lcsc_enabled(cfg: Dict[str, str] = None) -> bool:
    """Whether the key-free LCSC fallback provider is active. Default ON (zero-config
    sourcing); a user can turn it off in Settings. Honors cfg['LcscSourcing'] first,
    else the persisted setting."""
    val = (cfg or {}).get("LcscSourcing")
    if val is None:
        val = read_setting("LcscSourcing", True)
    return str(val).strip().lower() not in ("0", "false", "no", "off", "")


# ── DigiKey / Product Information v4 provider (last-resort, creds-gated) ───────
# The broadest catalog, but billed OAuth2 — so it is the LAST fallback (after the
# free Mouser key and key-free LCSC) and only registers when creds are present.
# Structurally complete + fully unit-tested; pending live verification (no creds).
def resolve_digikey_creds(cfg: Dict[str, str] = None):
    """The (client_id, client_secret) for the DigiKey Product Information API, or
    (None, None) when unconfigured. Resolution: DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET
    env vars (silent dev override) win, else user creds saved in config.json (gitignored
    — the in-app Settings field writes DigiKeyClientId / DigiKeyClientSecret here), else
    the baked app_secrets defaults (None in git; CI may bake them). Billed creds are NEVER
    committed, so config.json (not app_secrets) is the user path. Absent creds -> DigiKey
    is not registered."""
    cid = (os.environ.get("DIGIKEY_CLIENT_ID")
           or read_setting("DigiKeyClientId")
           or app_secrets.DIGIKEY_CLIENT_ID_DEFAULT)
    sec = (os.environ.get("DIGIKEY_CLIENT_SECRET")
           or read_setting("DigiKeyClientSecret")
           or app_secrets.DIGIKEY_CLIENT_SECRET_DEFAULT)
    return (cid or None, sec or None)


def _digikey_pick_variation(variations) -> dict:
    """DigiKey splits pricing + its own P/N across per-packaging ProductVariations (Cut
    Tape MOQ 1, Tape & Reel MOQ thousands). Pick the variation an engineer prototypes
    from: a priced one with the lowest MinimumOrderQuantity, else the lowest-MOQ
    variation, else {}."""
    if not variations:
        return {}

    def _moq(v):
        try:
            return int(v.get("MinimumOrderQuantity") or 1)
        except (TypeError, ValueError):
            return 1
    priced = [v for v in variations if v.get("StandardPricing")]
    pool = priced or variations
    return min(pool, key=_moq)


def _parse_digikey_part(p: dict) -> dict:
    """One DigiKey v4 keyword-search Product -> our normalized field dict (the SAME
    shape make_mouser_lookup / make_lcsc_lookup return, PLUS `digikey_pn`), so DigiKey
    hits feed sourcing AND price-break costing identically. v4 nests Manufacturer,
    Description, ProductStatus as objects but plain strings are tolerated."""
    man = p.get("Manufacturer")
    if isinstance(man, dict):
        man = man.get("Name") or ""
    elif not isinstance(man, str):
        man = ""
    desc = p.get("Description")
    if isinstance(desc, dict):
        desc = desc.get("ProductDescription") or desc.get("DetailedDescription") or ""
    elif not isinstance(desc, str):
        desc = ""
    status = p.get("ProductStatus")
    if isinstance(status, dict):
        status = status.get("Status")
    cat = p.get("Category")
    if isinstance(cat, dict):
        cat = cat.get("Name") or ""
    elif not isinstance(cat, str):
        cat = ""
    rohs = (p.get("Classifications") or {}).get("RohsStatus") if isinstance(
        p.get("Classifications"), dict) else None
    try:
        stock = int(p.get("QuantityAvailable") or 0)
    except (TypeError, ValueError):
        stock = 0
    var = _digikey_pick_variation(p.get("ProductVariations") or [])
    # Price-break ladder [{qty, price}, ...] ascending; unparseable rungs dropped.
    ladder = []
    for b in (var.get("StandardPricing") or []):
        q, pr = b.get("BreakQuantity"), _coerce_price(b.get("UnitPrice"))
        try:
            q = int(q)
        except (TypeError, ValueError):
            continue
        if pr is not None:
            ladder.append({"qty": q, "price": pr})
    ladder.sort(key=lambda x: x["qty"])
    return {"mpn": p.get("ManufacturerProductNumber"),
            "manufacturer": man,
            "datasheet": p.get("DatasheetUrl"),
            "description": desc,
            "digikey_pn": (var.get("DigiKeyProductNumber") or ""),
            "category": cat,
            "lifecycle": status or "Active",        # empty/null status = active
            "rohs": rohs,
            "stock": stock,
            "lead_time": p.get("ManufacturerLeadWeeks"),
            "unit_price": (ladder[0]["price"] if ladder else None),
            "price_breaks": ladder,
            "url": p.get("ProductUrl"),
            "suggested_replacement": None}


def _digikey_token(client_id: str, client_secret: str, timeout: int = 8):
    """Fetch a DigiKey OAuth2 client-credentials bearer token, or None (never raises).
    A refused token (bad creds, throttle, network) just returns None -> the caller
    sources the part elsewhere. Injectable: tests monkeypatch this whole function."""
    if not (client_id and client_secret):
        return None
    import json as _json
    import socket
    import urllib.parse
    import urllib.request
    import urllib.error
    body = urllib.parse.urlencode({"client_id": client_id,
                                   "client_secret": client_secret,
                                   "grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://api.digikey.com/v1/oauth2/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (_json.loads(r.read().decode()) or {}).get("access_token") or None
    except Exception:                                # noqa: BLE001 — a dead token endpoint is not fatal
        return None


def _digikey_request(endpoint: str, token: str, client_id: str, payload: dict,
                     timeout: int = 8) -> dict:
    """POST to a DigiKey Product Information v4 search endpoint and return a STRUCTURED
    result, mirroring _mouser_request::

        {"data": <parsed JSON | None>, "status": <int | None>, "error": <code>}

    error code: "" (ok) · "rate_limited" (429) · "auth" (401/403) · "http" (other
    4xx/5xx) · "timeout" · "network". Never raises."""
    import json as _json
    import socket
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        f"https://api.digikey.com/products/v4/search/{endpoint}",
        data=_json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}",
                 "X-DIGIKEY-Client-Id": client_id or ""})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"data": _json.loads(r.read().decode()),
                    "status": getattr(r, "status", 200), "error": ""}
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", None)
        err = ("rate_limited" if code == 429
               else "auth" if code in (401, 403)
               else "http")
        return {"data": None, "status": code, "error": err}
    except (socket.timeout, TimeoutError):
        return {"data": None, "status": None, "error": "timeout"}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        is_to = isinstance(reason, (socket.timeout, TimeoutError))
        return {"data": None, "status": None, "error": "timeout" if is_to else "network"}
    except Exception:                                # noqa: BLE001
        return {"data": None, "status": None, "error": "network"}


def make_digikey_lookup(client_id: str, client_secret: str, timeout: int = 8):
    """A lookup(mpn) -> normalized part dict backed by the DigiKey Product Information
    v4 keyword search. Reuses one OAuth2 bearer token across every lookup made through
    this closure — DigiKey tokens are valid ~30 min, and bulk sourcing (enrich_library /
    library_sourcing_report) iterates every part, so a token-per-part would double the
    round-trips and race straight into throttling. The token is cached with a
    conservative TTL and refetched only when missing or expired. Picks the exact-MPN
    match else the first result. Returns None for anything it can't resolve (no creds,
    no token, no hit, transport failure) and NEVER raises."""
    import time as _time
    # Cached bearer token shared by every lookup() call on this closure.
    # DigiKey tokens live ~30 min; 25 min (1500 s) leaves margin against clock skew and
    # in-flight requests. Kept in a mutable cell so the nested lookup can update it.
    _tok = {"token": None, "exp": 0.0}
    _TOKEN_TTL = 1500.0

    def _cached_token():
        now = _time.monotonic()
        if _tok["token"] and now < _tok["exp"]:
            return _tok["token"]
        token = _digikey_token(client_id, client_secret, timeout)
        # Cache successes only; a None (refused/throttled/network) is not memoized so the
        # next lookup can retry rather than stay poisoned for the whole TTL window.
        if token:
            _tok["token"] = token
            _tok["exp"] = now + _TOKEN_TTL
        return token

    def lookup(mpn):
        if not (client_id and client_secret and mpn):
            return None
        try:
            token = _cached_token()
            if not token:
                return None
            resp = _digikey_request("keyword", token, client_id,
                                    {"Keywords": str(mpn).strip(), "Limit": 10, "Offset": 0},
                                    timeout)
            products = ((resp or {}).get("data") or {}).get("Products") or []
        except Exception:                            # noqa: BLE001 — a dead provider is not fatal
            return None
        if not products:
            return None
        up = str(mpn).strip().upper()
        p = next((x for x in products
                  if (x.get("ManufacturerProductNumber") or "").upper() == up), products[0])
        return _parse_digikey_part(p)
    return lookup


# Human-facing messages for each _mouser_request transport failure code.
_MOUSER_ERR_MSG = {
    "rate_limited": "Mouser rate limit reached, pausing before the next lookup",
    "auth": "Mouser rejected the API key",
    "timeout": "Mouser lookup timed out. Check your connection",
    "http": "Mouser lookup failed",
    "network": "Could not reach Mouser. Check your connection",
}


def _classify_mouser_body_error(msg: str) -> str:
    """Mouser often answers HTTP 200 with the real failure in Errors[] rather than a
    status code — a throttle or a bad key both come back this way. Recover the proper
    error_code from the message so the UI reacts the same as it would to a 429/401."""
    low = (msg or "").lower()
    if any(w in low for w in ("too many", "rate limit", "exceed", "quota", "throttl")):
        return "rate_limited"
    if any(w in low for w in ("invalid unique identifier", "api key", "apikey", "unauthor",
                              "not authorized", "invalid key", "access denied", "forbidden")):
        return "auth"
    return "api"


def search_parts(query: str, cfg: Dict[str, str] = None, limit: int = 10) -> dict:
    """Built-in part lookup: search Mouser by MPN or keyword and return up to `limit`
    normalized results (manufacturer, datasheet, stock, price, lifecycle, Mouser P/N,
    url). Returns {query, results, error, error_code}. A genuine no-match is
    error=="" with an empty list; a transport failure carries a non-empty error and
    an error_code ("rate_limited"/"auth"/"timeout"/"network"/"http"/"api") so the UI
    can distinguish "nothing found" from "try again"."""
    key = resolve_mouser_key(cfg)
    if not key:
        return {"query": query, "results": [], "error": "no Mouser API key configured",
                "error_code": "no_key"}
    if not (query or "").strip():
        return {"query": query, "results": [], "error": "empty query", "error_code": "empty"}
    resp = _mouser_request("keyword", key,
                           {"SearchByKeywordRequest": {"keyword": query.strip(),
                                                       "records": max(1, min(limit, 50)),
                                                       "startingRecord": 0}})
    if resp["error"]:
        if resp["error"] == "rate_limited":
            note_mouser_rate_limited()               # start/refresh the countdown to reset
        return {"query": query, "results": [],
                "error": _MOUSER_ERR_MSG.get(resp["error"], "Mouser lookup failed"),
                "error_code": resp["error"]}
    data = resp["data"] or {}
    # Mouser can answer HTTP 200 yet report a failure (bad key, quota) in Errors[].
    body_errs = data.get("Errors") or []
    parts = ((data.get("SearchResults") or {}).get("Parts")) or []
    if not parts and body_errs:
        msg = "; ".join(e.get("Message", "") for e in body_errs if e.get("Message"))
        code = _classify_mouser_body_error(msg)
        # For a recognised throttle/auth failure show the curated message (and the code
        # the UI backs off on); for anything else keep Mouser's own informative text.
        human = _MOUSER_ERR_MSG[code] if code in ("rate_limited", "auth") else (
            msg or "Mouser returned an error")
        if code == "rate_limited":
            note_mouser_rate_limited()               # HTTP-200 body-reported cap counts too
        return {"query": query, "results": [], "error": human, "error_code": code}
    return {"query": query, "results": [_parse_mouser_part(p) for p in parts[:limit]],
            "error": "", "error_code": ""}


def footprint_has_model(footprint_text: str) -> bool:
    return "(model" in footprint_text


def _model_block(filename: str) -> str:
    return (
        f'  (model "{MODEL_VAR_REF}/{filename}"\n'
        f"    (offset (xyz 0 0 0))\n"
        f"    (scale (xyz 1 1 1))\n"
        f"    (rotate (xyz 0 0 0))\n"
        f"  )\n"
    )


def set_footprint_model(footprint_text: str, filename: str) -> str:
    """Repair the path of the first existing (model …) line."""
    def repl(m: "re.Match") -> str:
        return f'{m.group(1)}"{MODEL_VAR_REF}/{filename}"'
    return _MODEL_PATH_RE.sub(repl, footprint_text, count=1)


def ensure_footprint_model(footprint_text: str, filename: str) -> str:
    """Guarantee the footprint references ${MY3DMODELS}/<filename> exactly once:
    repair an existing (model …) line, else insert one before the closing paren."""
    if footprint_has_model(footprint_text):
        return set_footprint_model(footprint_text, filename)
    idx = footprint_text.rstrip().rfind(")")
    if idx == -1:
        return footprint_text
    return footprint_text[:idx] + _model_block(filename) + footprint_text[idx:]


def find_kicad_config_dir() -> Optional[Path]:
    """KiCad per-user config dir (highest version) under %APPDATA%/kicad."""
    override = os.environ.get("KICAD_CONFIG_HOME")
    if override and Path(override).exists():
        return Path(override)
    base = Path(os.environ.get("APPDATA", "")) / "kicad"
    if not base.exists():
        return None
    dirs = sorted([d for d in base.iterdir() if d.is_dir()])
    return dirs[-1] if dirs else None


def _lib_table_has(text: str, nickname: str) -> bool:
    return re.search(r'\(name\s+"%s"\)' % re.escape(nickname), text) is not None


def ensure_lib_entry(path: Path, root: str, nickname: str, uri: str, descr: str = "") -> bool:
    """Ensure a (lib …) row for nickname exists in a KiCad lib-table. True if changed."""
    header = f"({root}\n\t(version 7)\n)\n"
    text = read_text(path) if path.exists() else header
    if _lib_table_has(text, nickname):
        return False
    entry = f'\t(lib (name "{nickname}") (type "KiCad") (uri "{uri}") (options "") (descr "{descr}"))\n'
    idx = text.rstrip().rfind(")")
    write_text(path, text[:idx] + entry + text[idx:])
    return True


def ensure_env_var(common_path: Path, name: str, value: str) -> bool:
    """Ensure kicad_common.json defines environment var name = value. True if changed."""
    data: Dict = {}
    if common_path.exists():
        try:
            data = json.loads(read_text(common_path))
        except Exception:
            data = {}
    env = data.get("environment") if isinstance(data.get("environment"), dict) else {}
    vars_ = env.get("vars") if isinstance(env.get("vars"), dict) else {}
    if vars_.get(name) == value:
        return False
    vars_[name] = value
    env["vars"] = vars_
    data["environment"] = env
    write_text(common_path, json.dumps(data, indent=2))
    return True


def register_libraries(cfg: Dict[str, str], log: UILog) -> Dict[str, object]:
    """Register MySymbols + MyFootprints and define ${MY3DMODELS} in KiCad config.

    Returns a structured result so a caller can surface an actionable message:
        {"ok": bool, "reason": <code|"">, "message": <human text>, "changed": bool}
    reason codes: "" (ok) · "no_config" (KiCad config dir not found). When the config
    dir is missing the libraries are NOT registered — placed parts won't resolve their
    footprint/3D model in KiCad — so the message spells out the remediation rather than
    failing silently. `changed` is True when a table/env row was actually written; check
    result["ok"] (not dict truthiness) for success."""
    cfgdir = find_kicad_config_dir()
    if cfgdir is None:
        msg = ("KiCad not detected. Libraries imported but NOT registered, so placed "
               "parts won't resolve their footprint/3D model. Open KiCad once (it "
               "creates its config), or set KICAD_CONFIG_HOME to your kicad config "
               "folder, then run Register again.")
        log.write(f"Register: {msg}")
        return {"ok": False, "reason": "no_config", "message": msg, "changed": False}
    sym = ensure_lib_entry(cfgdir / "sym-lib-table", "sym_lib_table", "MySymbols",
                           str(cfg.get("SymbolLib", "")).replace("\\", "/"))
    fp = ensure_lib_entry(cfgdir / "fp-lib-table", "fp_lib_table", "MyFootprints",
                          str(cfg.get("FootprintLib", "")).replace("\\", "/"))
    envset = ensure_env_var(cfgdir / "kicad_common.json", MODEL_VAR,
                            str(cfg.get("ModelLib", "")).replace("\\", "/"))
    msg = (f"KiCad registration ({cfgdir.name}): MySymbols "
           f"{'added' if sym else 'ok'}, MyFootprints {'added' if fp else 'ok'}, "
           f"${{{MODEL_VAR}}} {'set' if envset else 'ok'}.")
    log.write(msg)
    return {"ok": True, "reason": "", "message": msg, "changed": bool(sym or fp or envset)}


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def match_model_for_footprint(fp_stem: str, model_files: List[Path]) -> Optional[Path]:
    """Best-effort match of a footprint to a 3D model file by normalized name.
    Footprint 'IC_TPS2121RUXR' -> model 'TPS2121RUXR.step'."""
    fpn = _norm_name(fp_stem)
    if not fpn:
        return None
    best = None
    best_len = 0
    for m in model_files:
        mn = _norm_name(m.stem)
        if len(mn) < 4:
            continue
        if mn == fpn or mn in fpn or fpn in mn:
            if len(mn) > best_len:
                best, best_len = m, len(mn)
    return best


# ── Part grouping — associate symbol+footprint+model regardless of name ──────
# Names alone don't group differently-named parts (footprint 'IC51-1004-809' vs
# model 'Yamaichi_ZIF.step'). KiCad already encodes the links explicitly, so we
# group by those first: a symbol's Footprint property, a footprint's (model …)
# line. Name-normalization is only a fallback to *propose* a model for a
# footprint that has none, and a persisted override map covers the rest.
def symbol_footprint_ref(symbol_block: str) -> str:
    """The footprint a symbol points at (library nickname stripped), or ''."""
    m = _FP_PROP_RE.search(symbol_block)
    return footprint_name(m.group(2)) if m else ""


def footprint_model_ref(footprint_text: str) -> str:
    """Basename of the 3D model a footprint's (model …) line references, or ''."""
    m = _MODEL_PATH_RE.search(footprint_text)
    if not m:
        return ""
    raw = m.group(2).strip().strip('"')
    return raw.replace("\\", "/").split("/")[-1]


def associate_parts(symbol_text: str, footprints: Dict[str, str],
                    model_files, overrides: Optional[dict] = None) -> List[dict]:
    """Group symbol/footprint/model into logical parts, naming-independent.

    Precedence for each link: manual override → explicit KiCad reference →
    name-normalized guess. Args: symbol lib text; {footprint_stem: text};
    iterable of model filenames; overrides {"model": {fp: file},
    "symbol": {sym: fp}}. Returns [{footprint, symbols, model, model_source}].
    """
    overrides = overrides or {}
    ov_model = overrides.get("model", {})
    ov_sym = overrides.get("symbol", {})
    model_paths = [Path(x) for x in model_files]

    groups: Dict[str, dict] = {}

    def group_for(fp: str) -> dict:
        if fp not in groups:
            groups[fp] = {"footprint": fp, "symbols": [], "model": None, "model_source": None}
        return groups[fp]

    # footprint -> model: override, then explicit (model …) line, then name guess
    for stem, text in footprints.items():
        g = group_for(stem)
        if stem in ov_model:
            g["model"], g["model_source"] = ov_model[stem], "override"
        else:
            ref = footprint_model_ref(text)
            if ref:
                g["model"], g["model_source"] = ref, "reference"
            else:
                guess = match_model_for_footprint(stem, model_paths)
                if guess:
                    g["model"], g["model_source"] = guess.name, "name-match"

    # symbol -> footprint: override, then the symbol's Footprint property
    ungrouped: List[str] = []
    for b in extract_symbol_blocks(symbol_text):
        nm = extract_symbol_name(b)
        fp = ov_sym.get(nm) or symbol_footprint_ref(b)
        if fp:
            group_for(fp)["symbols"].append(nm)
        else:
            ungrouped.append(nm)

    out = sorted(groups.values(), key=lambda g: g["footprint"].lower())
    if ungrouped:
        out.append({"footprint": None, "symbols": sorted(ungrouped),
                    "model": None, "model_source": None})
    return out


def _group_overrides_path(cfg: Dict[str, str]) -> Path:
    return Path(cfg.get("Libs", ".")) / "part_group_overrides.json"


def load_group_overrides(cfg: Dict[str, str]) -> dict:
    import json
    p = _group_overrides_path(cfg)
    if p.exists():
        try:
            return json.loads(read_text(p))
        except Exception:
            return {}
    return {}


def save_group_overrides(cfg: Dict[str, str], overrides: dict) -> None:
    import json
    write_text(_group_overrides_path(cfg), json.dumps(overrides, indent=2))


# ── sourcing snapshots (persist volatile price/stock/lifecycle across relaunch) ─
# Persisted per-part sourcing snapshot fields. Beyond the volatile price/stock trio we
# now also keep `source` (so the per-provider refresh policy survives relaunch), the
# product `url` + `datasheet` (so the Mouser P/N link and datasheet Find work off a
# cached snapshot), `category`/`rohs`, and the `price_breaks` ladder (so the restored
# view shows the whole volume curve, not just a single unit price).
_SNAPSHOT_FIELDS = ("unit_price", "stock", "lifecycle", "lead_time",
                    "suggested_replacement", "mouser_pn", "source", "url",
                    "datasheet", "category", "rohs", "price_breaks")


def _sourcing_snapshots_path(cfg: Dict[str, str]) -> Path:
    return Path(cfg.get("Libs", ".")) / "sourcing_snapshots.json"


def load_sourcing_snapshots(cfg: Dict[str, str]) -> dict:
    """The whole {MPN_UPPER: {..fields.., as_of}} snapshot store, or {} if none."""
    p = _sourcing_snapshots_path(cfg)
    if p.exists():
        try:
            d = json.loads(read_text(p))
        except Exception:  # noqa: BLE001
            return {}
        # A truncated / hand-edited file can decode to valid-but-non-object JSON
        # (null, a list, a number). Treat anything but a dict as "no store" so a
        # later snap[...] read/write can't crash on None/[].
        return d if isinstance(d, dict) else {}
    return {}


def save_sourcing_snapshot(cfg: Dict[str, str], mpn: str, data: dict, now=None) -> None:
    """Persist a part's live sourcing (price/stock/lifecycle/lead time) with an 'as of'
    timestamp, keyed by upper-cased MPN so it survives relaunch. Only the volatile
    fields are stored (identity already lives on the symbol). Atomic write."""
    mpn = (mpn or "").strip()
    if not mpn:
        return
    import datetime as _dt
    when = now or _dt.datetime.now(_dt.timezone.utc)
    snaps = load_sourcing_snapshots(cfg)
    entry = {k: data.get(k) for k in _SNAPSHOT_FIELDS if data.get(k) is not None}
    entry["as_of"] = when.isoformat()
    snaps[mpn.upper()] = entry
    _atomic_write_json(_sourcing_snapshots_path(cfg), snaps)


def sourcing_snapshot_for(cfg: Dict[str, str], mpn: str) -> Optional[dict]:
    """The last persisted snapshot for `mpn` (case-insensitive), or None."""
    return load_sourcing_snapshots(cfg).get((mpn or "").strip().upper())


def snapshot_age_label(as_of_iso: str, now=None) -> str:
    """A human 'N days ago' for a snapshot's as_of ISO timestamp; '' on bad input.
    `now` is injectable so the label is deterministic under test."""
    secs = snapshot_age_seconds(as_of_iso, now=now)
    if secs is None:
        return ""
    if secs < 60:
        return "just now"
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= n:
            v = int(secs // n)
            return f"{v} {unit}{'s' if v != 1 else ''} ago"
    return "just now"


def snapshot_age_seconds(as_of_iso: str, now=None):
    """Seconds elapsed since a snapshot's as_of ISO timestamp, or None on bad/empty
    input. Tolerates a naive/aware tzinfo mismatch. The numeric backbone shared by the
    'N days ago' label and the per-provider refresh policy."""
    if not as_of_iso:
        return None
    import datetime as _dt
    try:
        t = _dt.datetime.fromisoformat(as_of_iso)
    except (TypeError, ValueError):
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if t.tzinfo is None and now.tzinfo is not None:      # tolerate naive/aware mismatch
        t = t.replace(tzinfo=now.tzinfo)
    elif t.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=t.tzinfo)
    return (now - t).total_seconds()


# The built-in Mouser key is a SHARED 1000-lookups/day cap, so re-pricing a part whose
# snapshot is younger than this is wasteful — the refresh policy disables it. LCSC
# (key-free jlcsearch) has no such cap and is always refreshable.
MOUSER_REFRESH_MIN_AGE_S = 4 * 3600


def snapshot_refresh_policy(source: str, age_seconds) -> Dict[str, object]:
    """Whether a per-part sourcing Refresh should be OFFERED, given the provider that
    sourced the data and the cached snapshot's age (seconds, or None when the data was
    just fetched live this session / has no persisted age).

    Returns {can_refresh, reason}. Mouser is gated: a snapshot younger than
    MOUSER_REFRESH_MIN_AGE_S can't be refreshed (the shared daily cap), and `reason`
    says when it frees up. LCSC and any other/unknown provider are always refreshable.
    Pure — `now` never read here; the caller passes an already-computed age."""
    src = (source or "").strip().lower()
    if src == "mouser" and age_seconds is not None and age_seconds < MOUSER_REFRESH_MIN_AGE_S:
        remaining = int(MOUSER_REFRESH_MIN_AGE_S - age_seconds)
        hrs = MOUSER_REFRESH_MIN_AGE_S // 3600
        rh, rm = remaining // 3600, (remaining % 3600) // 60
        when = (f"{rh}h {rm}m" if rh else f"{rm}m") if remaining > 0 else "shortly"
        return {"can_refresh": False,
                "reason": (f"Priced under {hrs}h ago. Mouser's built-in key is shared "
                           f"(1000 lookups/day), so a re-fetch this soon is skipped. "
                           f"Refresh frees up in ~{when}. LCSC always refreshes.")}
    prov = (source or "").strip() or "the distributor"
    return {"can_refresh": True,
            "reason": f"Re-fetch live stock, pricing and lifecycle from {prov}."}


def projects_referencing_symbol(cfg: Dict[str, str], symbol_name: str) -> List[str]:
    """Names of discovered KiCad projects whose schematics instantiate the library
    symbol `symbol_name` (matched on the symbol-name part of each schematic lib_id).

    Used by the rename heads-up: renaming a shared-library symbol never breaks these
    projects (a schematic embeds its own cached copy of the symbol), so this is
    informational reassurance, not a manual fixup list. Read-only; discovery mirrors
    the Projects workbench (RepoRoot and its parent)."""
    name = (symbol_name or "").strip()
    if not name:
        return []
    import kicad_tools
    rr = cfg.get("RepoRoot")
    roots = [Path(rr), Path(rr).parent] if rr else []
    seen: set = set()
    projects: List[Path] = []
    for r in roots:
        try:
            for p in kicad_tools.discover_kicad_projects(r):
                if str(p) not in seen:
                    seen.add(str(p))
                    projects.append(Path(p))
        except Exception:                        # noqa: BLE001
            pass
    pat = re.compile(r'\(\s*lib_id\s+"[^"]*:' + re.escape(name) + r'"')
    hits: List[str] = []
    for proj in projects:
        try:
            # Non-recursive: a nested folder with its own .kicad_pro is a DISTINCT
            # project (NETDECK cards), counted under its own name — never double-billed.
            if any(pat.search(read_text(sch)) for sch in proj.glob("*.kicad_sch")):
                hits.append(proj.name)
        except Exception:                        # noqa: BLE001
            pass
    return sorted(set(hits))


def _cfg_path(cfg: Dict[str, str], key: str) -> Path:
    """A library path from cfg, read defensively. Missing or blank -> a sentinel that
    is neither a file nor a dir (Path('') resolves to CWD '.', which would spuriously
    'exist'), so callers gate on .is_file()/.is_dir() and see 'not configured' rather
    than reading the current directory or raising KeyError."""
    raw = str(cfg.get(key, "") or "").strip()
    # A blank path would become Path('.') (CWD) and spuriously pass .exists(); point at a
    # name that cannot exist instead so .is_file()/.is_dir() both read False.
    return Path(raw) if raw else Path(__file__).with_name("__cfg_path_unset__")


def associate_parts_from_cfg(cfg: Dict[str, str], overrides: Optional[dict] = None) -> List[dict]:
    """associate_parts() sourced from the configured shared-library paths."""
    sym_path = _cfg_path(cfg, "SymbolLib")
    symbol_text = read_text(sym_path) if sym_path.is_file() else ""
    fp_dir = _cfg_path(cfg, "FootprintLib")
    footprints = {p.stem: read_text(p) for p in sorted(fp_dir.glob("*.kicad_mod"))} \
        if fp_dir.is_dir() else {}
    mdl_dir = _cfg_path(cfg, "ModelLib")
    model_files = [p.name for p in sorted(mdl_dir.glob("*"))
                   if p.suffix.lower() in (".step", ".stp", ".wrl")] if mdl_dir.is_dir() else []
    return associate_parts(symbol_text, footprints, model_files,
                           overrides if overrides is not None else load_group_overrides(cfg))


# ── canonical part identity from the symbol's own properties ──────────────────
# SnapEDA / Component Search Engine / Mouser-style symbols embed the part's real
# identity as symbol properties (in this library: Value holds the manufacturer
# part number, MANUFACTURER the maker, plus Datasheet/Description). Deriving the
# identity from the symbol makes it the single source of truth for EXISTING parts
# and every FUTURE download alike — no side index to maintain.
_PROP_RE = re.compile(r'\(property\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"')

# candidate property names, most-specific first (compared case/sep-insensitively).
# _MPN_KEYS ends with 'value' as a last-resort identity for passives; _MPN_KEYS_STRICT
# drops it, for when only a REAL manufacturer part number will do (BOM MPN column).
_MPN_KEYS_STRICT = ("manufacturerpartnumber", "mpn", "mouserpartnumber", "mouserpartno",
                    "partnumber", "partno")
_MPN_KEYS = _MPN_KEYS_STRICT + ("value",)
_MFR_KEYS = ("manufacturer", "mfr", "mfg", "brand", "vendor")
# Case-folded values treated as "no real identity" and dropped from part_identity /
# strict_mpn. Deliberate tradeoff: "value" is here because KiCad's default Value field
# is literally the string "Value", and a symbol still carrying that default has no MPN.
# The cost is that a genuine part whose Value/MPN case-folds to exactly "value" (or
# "na"/"none") is also rejected — vanishingly rare for a real manufacturer part number,
# and accepted knowingly to keep every default-Value symbol out of the BOM MPN column.
_PLACEHOLDERS = {"", "~", "*", "-", "n/a", "na", "none", "value"}


def extract_symbol_properties(block: str) -> Dict[str, str]:
    """{property name -> value} for one symbol block (quote-unescaped)."""
    out: Dict[str, str] = {}
    for k, v in _PROP_RE.findall(block or ""):
        out[k.replace('\\"', '"')] = v.replace('\\"', '"')
    return out


def strict_mpn(props: Dict[str, str]) -> Optional[str]:
    """A REAL manufacturer part number from a dedicated property (never the Value
    fallback). None for a generic passive that only carries a value."""
    norm = {k.lower().replace(" ", "").replace("_", "").replace("-", ""): (v or "").strip()
            for k, v in (props or {}).items()}
    for k in _MPN_KEYS_STRICT:
        v = norm.get(k, "")
        if v and v.lower() not in _PLACEHOLDERS:
            return v
    return None


def part_identity(props: Dict[str, str], fallback: str = "") -> Dict[str, Optional[str]]:
    """Canonical identity from symbol properties: the manufacturer part number
    (Mouser's canonical name), the manufacturer, and the datasheet/description.
    Falls back to `fallback` (e.g. the footprint stem) when nothing usable exists."""
    norm = {k.lower().replace(" ", "").replace("_", "").replace("-", ""): v.strip()
            for k, v in (props or {}).items()}

    def pick(keys):
        for k in keys:
            v = norm.get(k, "")
            if v and v.lower() not in _PLACEHOLDERS:
                return v
        return None

    return {
        "mpn": pick(_MPN_KEYS) or (fallback or None),
        "manufacturer": pick(_MFR_KEYS),
        "datasheet": pick(("datasheet",)),
        "description": pick(("description", "ki_description")),
        "category": pick(("category",)),
    }


# The single honest label for a part that carries no real manufacturer part number.
# One string, rendered identically in the Library detail, the Library list, and the
# BOM, so a generic passive reads the same everywhere: it is not orderable as-is.
NO_MPN_FLAG = "no MPN · not orderable"


def has_real_mpn(row: Dict[str, str]) -> bool:
    """The ONE honest part-identity signal, read from a grouped-library row OR a BOM
    line. True only when the part carries a REAL manufacturer part number (a
    strict-MPN property), never the Value/symbol-name fallback — so a generic passive
    is False and flagged 'not orderable' in every surface.

    Prefers the row's own explicit flag (`has_real_mpn`, set by scan_library_grouped
    / the BOM builder from strict_mpn). Falls back, for a bare {name, mpn} row, to
    inferring it: a real MPN differs from the humanized/name fallback. This keeps the
    contract honest even for callers that hand us a minimal row."""
    if "has_real_mpn" in row:
        return bool(row.get("has_real_mpn"))
    mpn = (row.get("mpn") or "").strip()
    if not mpn:
        return False
    # No explicit flag: an MPN equal to the symbol-name fallback is the fallback, not a
    # real part number. Anything else is treated as a genuine MPN.
    name = (row.get("name") or "").strip()
    return mpn != name


def part_display_names(row: Dict[str, str]) -> Dict[str, object]:
    """The ONE identity contract for a part — the same dict powers the Library
    detail, the Library list, AND the BOM identity column, so a part reads
    identically everywhere (LIB-03; findings LM:2006, LM:2129).

    - 'humanized' — the plain-words 'what it IS': the Mouser Description verbatim
      when present, else the least-machine name we have (symbol name, then MPN).
      This is what stops meaningless names like '1043_KEY' being all the user sees.
    - 'technical' — the manufacturer part number, falling back to the raw symbol
      name for a generic passive that carries no real MPN. NEVER a fabricated
      MPN-looking string: for a no-MPN part this is honestly the value/symbol name.
    - 'has_real_mpn' / 'orderable' — the honest signal (see has_real_mpn): False for
      a generic passive with no real MPN. Both surfaces DIM the technical name and
      show `flag` when this is False.
    - 'flag' — NO_MPN_FLAG when not orderable, else ''. One string, everywhere.

    Both names are '' for an empty row. When the two are equal the UI shows only one
    line.
    """
    desc = (row.get("description") or "").strip()
    mpn = (row.get("mpn") or "").strip()
    name = (row.get("name") or "").strip()
    real = has_real_mpn(row)
    return {
        "humanized": desc or name or mpn,
        "technical": mpn or name,
        "has_real_mpn": real,
        "orderable": real,
        "flag": "" if real else NO_MPN_FLAG,
    }


def part_missing(row: Dict) -> List[Dict[str, str]]:
    """The honest per-part completeness report: everything the part still needs to
    be fully placeable, renderable, and orderable, each as {item, why, how_to_fix}.

    Composed purely from a scan_library_grouped row's presence + identity flags —
    no disk I/O — so it powers the "Complete This Part" and Fix-All completeness
    dialogs (what's DONE vs what's still MISSING + how to fix each). A fully
    complete part returns []. The `how_to_fix` text names the concrete Library
    action that resolves the gap, so the dialog is actionable, not just a list.
    """
    missing: List[Dict[str, str]] = []
    has_symbol = bool(row.get("has_symbol"))
    has_footprint = bool(row.get("has_footprint"))
    has_model = bool(row.get("has_model"))
    fp = row.get("footprint")
    model = row.get("model")

    # 1. Symbol — a footprint-only orphan can't be placed in a schematic at all.
    if not has_symbol:
        missing.append({
            "item": "Symbol",
            "why": "This footprint has no symbol, so nothing can place it in a schematic.",
            "how_to_fix": "Create a symbol for it (a new stub, or reuse an existing "
                          "library symbol) via Library ▸ Create Symbol for Footprint.",
        })

    # 2. Footprint — none assigned, or the referenced .kicad_mod file is missing.
    if has_symbol and not has_footprint:
        if fp:
            missing.append({
                "item": "Footprint",
                "why": f"The symbol references footprint '{fp}', but no matching "
                       ".kicad_mod file exists in the library (dangling link).",
                "how_to_fix": f"Add the '{fp}.kicad_mod' file, or re-link the symbol "
                              "to an existing footprint via Library ▸ Link to Existing Footprint.",
            })
        else:
            missing.append({
                "item": "Footprint",
                "why": "The symbol has no footprint assigned, so it has no physical "
                       "land pattern.",
                "how_to_fix": "Link an existing footprint or add a new .kicad_mod "
                              "via Library ▸ Link/Add Footprint.",
            })

    # 3. 3D model — only meaningful once a real footprint exists on disk.
    if has_footprint and not has_model:
        if model:
            missing.append({
                "item": "3D Model",
                "why": f"The footprint references 3D model '{model}', but that file "
                       "is missing from the model library (dangling link).",
                "how_to_fix": f"Add the '{model}' file, or re-link to an existing "
                              "model via Library ▸ Link to Existing 3D Model.",
            })
        else:
            missing.append({
                "item": "3D Model",
                "why": "The footprint has no 3D model attached, so the part won't "
                       "render in the 3D board view.",
                "how_to_fix": "Add a .step/.stp/.wrl file or link an existing model "
                              "via Library ▸ Add/Link 3D Model.",
            })

    # 4. Orderable identity + blank identity fields live on symbols, so they only
    #    apply to a symbol-bearing part (a footprint-only orphan is covered by #1).
    if has_symbol:
        if not has_real_mpn(row):
            missing.append({
                "item": "Part Number",
                "why": "No manufacturer part number. The part is not orderable as-is.",
                "how_to_fix": "Set the MPN (and manufacturer) in the identity editor, "
                              "or run Complete This Part to autofill it from a "
                              "distributor lookup.",
            })
        for key, label in (("manufacturer", "Manufacturer"),
                           ("datasheet", "Datasheet"),
                           ("description", "Description")):
            if not (row.get(key) or "").strip():
                missing.append({
                    "item": label,
                    "why": f"The {label} field is blank.",
                    "how_to_fix": f"Fill {label} in the identity editor, or run "
                                  "Complete This Part to autofill it from a "
                                  "distributor lookup.",
                })
    return missing


# ── The 8-item completion passport (v2.11 Library redesign) ──────────────────
# One honest scorecard per part: the eight things it needs to be Complete, in the
# order the Library canvas shows them (Files first, then Identity). Pure — reads only
# a scan_library_grouped row — so the picker score, the canvas ring, and the tightened
# Complete verdict all read the SAME numbers. `present` uses the honest signals shared
# with part_missing / has_real_mpn: Part Number is a REAL MPN (never the Value/name
# fallback), and Category is the explicit property (never a refdes-derived display
# default). Dangling is not one of the eight — it is a disqualifier that blocks Complete.
COMPLETION_ITEMS = (
    ("symbol", "Symbol"),
    ("footprint", "Footprint"),
    ("model", "3D Model"),
    ("part_number", "Part Number"),
    ("manufacturer", "Manufacturer"),
    ("datasheet", "Datasheet"),
    ("description", "Description"),
    ("category", "Category"),
)


def _completion_present(row: Dict) -> Dict[str, bool]:
    """Presence of each of the eight items on a grouped row (honest signals only)."""
    return {
        "symbol": bool(row.get("has_symbol")),
        "footprint": bool(row.get("has_footprint")),
        "model": bool(row.get("has_model")),
        "part_number": has_real_mpn(row),
        "manufacturer": bool((row.get("manufacturer") or "").strip()),
        "datasheet": bool((row.get("datasheet") or "").strip()),
        "description": bool((row.get("description") or "").strip()),
        "category": bool((row.get("category") or "").strip()),
    }


def part_completion(row: Dict) -> Dict:
    """The 8-item completion passport for one scan_library_grouped row (COMPLETION_ITEMS).

    Returns {items:[{key,label,present}×8], score, total(=8), missing:[label...],
    dangling, is_complete}. A part is complete only when all eight are present AND it
    has no dangling reference — the tightened v2.11 rule (previously: three assets +
    manufacturer). Pure; the rich per-gap why/how_to_fix stays in part_missing."""
    present = _completion_present(row)
    items = [{"key": k, "label": label, "present": present[k]}
             for k, label in COMPLETION_ITEMS]
    score = sum(1 for it in items if it["present"])
    dangling = bool(row.get("dangling"))
    return {
        "items": items,
        "score": score,
        "total": len(COMPLETION_ITEMS),
        "missing": [it["label"] for it in items if not it["present"]],
        "dangling": dangling,
        "is_complete": score == len(COMPLETION_ITEMS) and not dangling,
    }


def completion_badge(row: Dict) -> str:
    """The compact per-part completion badge for the picker row / detail header: 'Fix'
    when the part has a dangling reference (it can never be Complete), else 'N/8'."""
    c = part_completion(row)
    return "Fix" if c["dangling"] else f"{c['score']}/{c['total']}"


# The completion passport uses a plain check/cross pair (already the app's vocabulary —
# see kit.workbench_text's ✓/✗ report) so a hover tooltip reads the same everywhere.
COMPLETION_CHECK = "✓"      # ✓
COMPLETION_CROSS = "✗"      # ✗


def completion_tooltip(comp: Dict) -> str:
    """The per-dimension breakdown shown on hovering a part's completion badge/glyph:
    one line per COMPLETION_ITEMS dimension, '✓ Symbol' when present and '✗ Datasheet'
    when missing, headed by an N/8 (or 'broken link') summary. Built straight off a
    part_completion() result so the lines and the 'missing' set can never disagree."""
    items = comp.get("items", [])
    if comp.get("dangling"):
        head = "Has a broken link, needs a fix"
    elif comp.get("is_complete"):
        head = f"Complete, all {comp.get('total', len(items))} present"
    else:
        head = f"Incomplete, {comp.get('score', 0)} of {comp.get('total', len(items))}"
    lines = [head, ""]
    for it in items:
        mark = COMPLETION_CHECK if it.get("present") else COMPLETION_CROSS
        lines.append(f"{mark} {it.get('label', '')}")
    return "\n".join(lines)


# LIB-05 autofill: the identity fields a Mouser lookup can populate.
# (row_key, symbol_property, human_label) — row_key indexes both the grouped row
# and the normalized Mouser dict; symbol_property is where the value persists.
# NOTE: the manufacturer part number persists to a DEDICATED 'Manufacturer Part Number'
# property (a strict-MPN key), NEVER to 'Value'. A passive's Value ('10k') is its
# electrical value, not a part number — overwriting it with an MPN would both corrupt
# the displayed value and leave the part grouping as a bare passive in the BOM.
AUTOFILL_FIELDS = (
    ("description", "Description", "Description"),
    ("manufacturer", "MANUFACTURER", "Manufacturer"),
    ("datasheet", "Datasheet", "Datasheet"),
    ("mpn", "Manufacturer Part Number", "Part Number"),
    ("mouser_pn", "Mouser Part Number", "Mouser Part Number"),
)


def autofill_plan(row: Dict, fetched: Dict, mode: str,
                  allow: Optional[set] = None) -> Dict[str, str]:
    """Which identity fields a Mouser lookup would write, and their new values.

    mode:
      'blanks'    — fill only fields currently empty on `row`
      'overwrite' — replace every field Mouser has a value for
      'manual'    — restrict to the row keys in `allow`
    A field is included only when Mouser has a non-empty value AND it differs
    from the current one, so an unchanged field is never rewritten.
    """
    plan: Dict[str, str] = {}
    for row_key, _prop, _label in AUTOFILL_FIELDS:
        new = (fetched.get(row_key) or "").strip()
        if not new:
            continue
        cur = (row.get(row_key) or "").strip()
        if new == cur:
            continue
        if mode == "manual":
            if allow is not None and row_key in allow:
                plan[row_key] = new
        elif mode == "overwrite":
            plan[row_key] = new
        elif mode == "blanks" and not cur:
            plan[row_key] = new
    return plan


def scan_library_grouped(cfg: Dict[str, str], overrides: Optional[dict] = None) -> List[dict]:
    """One row per logical part for the future grouped library view.

    Built on associate_parts_from_cfg() (which links symbol -> footprint ->
    model by KiCad's own explicit references, with a name-match fallback), then
    annotated with presence/health flags computed against what is ACTUALLY on
    disk in the configured library paths. Each returned dict:

      name          best human label: the first symbol name if the part has
                    any symbols, else the footprint stem.
      footprint     footprint stem the part is keyed on, or None (ungrouped
                    symbols with no Footprint property).
      symbols       list of symbol names in this part.
      model         basename of the linked 3D model, or None.
      model_source  how the model link was found: 'override' | 'reference'
                    (footprint's own (model …) line) | 'name-match' | None.
      has_symbol    the part has at least one symbol.
      has_footprint the footprint the part references exists as a real
                    .kicad_mod file on disk (a symbol that references a missing
                    footprint is False here, and flagged dangling below).
      has_model     the linked model exists as a real file on disk.
      dangling      True if a symbol references a footprint that is NOT present
                    on disk, OR the footprint references a model file that is
                    NOT present on disk. (Ungrouped symbols with no footprint
                    reference at all are missing-but-not-dangling: has_footprint
                    is False, dangling stays False.)

    Pure-ish: reads the configured SymbolLib/FootprintLib/ModelLib paths, writes
    nothing. Safe to call for a preview.
    """
    groups = associate_parts_from_cfg(cfg, overrides)

    # What actually exists on disk, so we can tell a real link from a dangling
    # reference to a footprint/model that was never (or no longer) installed.
    fp_dir = _cfg_path(cfg, "FootprintLib")
    fp_stems = {p.stem for p in fp_dir.glob("*.kicad_mod")} if fp_dir.is_dir() else set()
    mdl_dir = _cfg_path(cfg, "ModelLib")
    model_names = {p.name for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")} if mdl_dir.is_dir() else set()

    # symbol name -> its property dict, read once (identity source for every row)
    sym_props: Dict[str, Dict[str, str]] = {}
    sym_path = _cfg_path(cfg, "SymbolLib")
    if sym_path.is_file():
        try:
            for b in extract_symbol_blocks(read_text(sym_path)):
                sym_props[extract_symbol_name(b)] = extract_symbol_properties(b)
        except Exception:                      # noqa: BLE001
            pass

    rows: List[dict] = []
    for g in groups:
        fp = g.get("footprint")
        symbols = list(g.get("symbols") or [])
        model = g.get("model")

        has_symbol = bool(symbols)
        has_footprint = fp is not None and fp in fp_stems
        has_model = model is not None and model in model_names

        # A symbol pointing at a footprint that has no .kicad_mod file, or a
        # footprint whose (model …) line points at a missing file, is dangling.
        symbol_refs_missing_fp = has_symbol and fp is not None and fp not in fp_stems
        footprint_refs_missing_model = model is not None and model not in model_names
        dangling = symbol_refs_missing_fp or footprint_refs_missing_model

        name = symbols[0] if symbols else fp

        # canonical identity from the first symbol that carries usable properties
        ident = {"mpn": None, "manufacturer": None, "datasheet": None,
                 "description": None, "category": None}
        for s in symbols:
            cand = part_identity(sym_props.get(s, {}), fallback="")
            if cand["mpn"] or cand["manufacturer"]:
                ident = cand
                break

        # The honest orderability signal: True only if SOME symbol carries a REAL
        # manufacturer part number (strict_mpn — never the Value/symbol-name fallback).
        # A generic passive that only carries a value is False, and flagged
        # 'not orderable' identically in the Library detail/list AND the BOM.
        real_mpn = any(strict_mpn(sym_props.get(s, {})) for s in symbols)

        # The Part Number is a real identity, so it only falls back to the symbol
        # name for a symbol-bearing part (a passive whose canonical label IS its
        # symbol name). A footprint-only orphan has NO part number — do not promote
        # its footprint stem into the MPN field (it would surface a footprint like
        # '1043_KEY' as a fake part number in the identity form). `name` still
        # carries the stem for the human-facing list label; `mpn` stays None so the
        # detail's read-only Part Number field is empty, not a fabricated number.
        mpn = ident["mpn"] or (name if has_symbol else None)

        rows.append({
            "name": name,
            "mpn": mpn,                        # the part's canonical (Mouser) name; None for a footprint-only orphan
            "has_real_mpn": real_mpn,          # honest orderability signal (from strict_mpn); the ONE identity contract
            "manufacturer": ident["manufacturer"],
            "datasheet": ident["datasheet"],
            "description": ident["description"],
            "footprint": fp,
            "symbols": symbols,
            "model": model,
            "model_source": g.get("model_source"),
            "has_symbol": has_symbol,
            "has_footprint": has_footprint,
            "has_model": has_model,
            "dangling": dangling,
            "category": ident.get("category") or "",   # explicit Category property; "" when unset
        })
    return rows


# ── Footprint-only orphans: create + link a symbol (LM:2117 remedy) ──────────
# A footprint with no symbol is an "unlinked footprint" — real geometry the
# library carries but nothing can place, because a schematic drops SYMBOLS, not
# footprints. The dead read-only identity form gave the user nowhere to go; this
# builds a minimal valid symbol keyed on the footprint and points it at that
# footprint, turning the orphan into a placeable part in one action.
_SYMBOL_LIB_HEADER = ('(kicad_symbol_lib (version 20211014) '
                      '(generator "LibraryManager.py")\n)\n')


def new_symbol_block(name: str, footprint_stem: str) -> str:
    """A minimal, KiCad-valid `(symbol …)` block named `name`, carrying the
    Reference/Value/Footprint properties every symbol needs and pointing its
    Footprint property at `footprint_stem` in the shared library.

    Intentionally geometry-free (no pins/graphics) — it is a stub the user
    completes in KiCad's symbol editor; what matters here is that it EXISTS,
    carries the right footprint link, and groups with the orphan footprint so
    the part becomes placeable and orderable. `name` is used verbatim as the
    symbol id (quotes/backslashes escaped)."""
    esc = str(name).replace("\\", "\\\\").replace('"', '\\"')
    fp = qualify_footprint(footprint_stem)
    return (
        f'(symbol "{esc}"\n'
        '    (in_bom yes)\n'
        '    (on_board yes)\n'
        '    (property "Reference" "U"\n'
        '      (at 0 2.54 0)\n'
        '      (effects (font (size 1.27 1.27))))\n'
        f'    (property "Value" "{esc}"\n'
        '      (at 0 -2.54 0)\n'
        '      (effects (font (size 1.27 1.27))))\n'
        f'    (property "Footprint" "{fp}"\n'
        '      (at 0 0 0)\n'
        '      (effects (font (size 1.27 1.27)) hide))\n'
        '    (property "Datasheet" ""\n'
        '      (at 0 0 0)\n'
        '      (effects (font (size 1.27 1.27)) hide))\n'
        ')'
    )


def create_symbol_for_footprint(cfg: Dict[str, str], footprint_stem: str,
                                 log: UILog = None, name: Optional[str] = None) -> Optional[str]:
    """Create a new stub symbol linked to `footprint_stem` and append it to the
    shared symbol library, so a footprint-only orphan becomes a real, placeable
    part. `name` defaults to the footprint stem (the natural label); a clashing
    name is de-duplicated with a numeric suffix so an existing symbol is never
    overwritten. Returns the created symbol name, or None on failure/no-op.

    Snapshot-then-write under _LIB_LOCK, mirroring the other library mutations."""
    stem = (footprint_stem or "").strip()
    if not stem:
        return None
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not str(sym_path):
        return None
    want = (name or stem).strip()
    if not want:
        return None
    with _LIB_LOCK:
        text = read_text(sym_path) if sym_path.exists() else _SYMBOL_LIB_HEADER
        existing = {extract_symbol_name(b) for b in extract_symbol_blocks(text)}
        # Never clobber an existing symbol — a footprint that already has a
        # same-named symbol would not be an orphan, but a manual name can clash.
        final = want
        n = 2
        while final in existing:
            final = f"{want}_{n}"
            n += 1
        block = new_symbol_block(final, stem)
        new_text = insert_blocks_into_target(text, [block])
        _snapshot_then_write(sym_path, new_text, log or _NullLog())
    return final


# ── Reuse an EXISTING symbol for an orphan footprint (owner #7) ──────────────
# create_symbol_for_footprint() makes a bare STUB. But some footprint-only orphans
# should reuse a symbol that already exists (a CC0402 orphan can share a generic
# capacitor symbol). That means DUPLICATING the chosen symbol block — pins, graphics
# and all — under a new name, then repointing its Footprint at the orphan. KiCad
# names a symbol's unit sub-symbols "<parent>_<unit>_<style>", so the rename has to
# rewrite the parent id AND every nested unit id together or the units stop resolving.
def rename_symbol_block(block: str, new_name: str) -> str:
    """Rename a `(symbol …)` block to `new_name`, rewriting the parent id AND its
    nested unit sub-symbols (`<old>_0_1`, `<old>_1_1`, …) so KiCad still resolves the
    units. Operates on a single extracted block (its only `(symbol "<old>…"` ids are
    the parent and its own units), so a literal-prefix rewrite is safe."""
    old = extract_symbol_raw_name(block)
    esc_new = str(new_name).replace("\\", "\\\\").replace('"', '\\"')
    o = re.escape(old)
    # Rewrite the nested unit ids BEFORE the parent. If new_name starts with "<old>_"
    # (e.g. renaming "U1" -> "U1_RENAMED"), renaming the parent first would make the
    # parent id itself match the "(symbol \"OLD_" unit pattern and get a SECOND rewrite
    # ("U1_RENAMED" -> "U1_RENAMED_RENAMED"). Units carry a trailing "_", the parent id
    # does not, so doing units first never touches the still-"<old>" parent.
    # nested unit ids: (symbol "OLD_<unit>_<style>"  -> (symbol "NEW_<unit>_<style>"
    block = re.sub(r'\(symbol\s+"' + o + r'_', f'(symbol "{esc_new}_', block)
    # parent id (first occurrence): (symbol "OLD"  -> (symbol "NEW"
    block = re.sub(r'\(symbol\s+"' + o + r'"', f'(symbol "{esc_new}"', block, count=1)
    return block


def rename_symbol_in_library(cfg: Dict[str, str], old_name: str, new_name: str,
                             log: UILog = None) -> bool:
    """Rename a symbol (and its nested unit sub-symbols) in place in the shared library.
    Refuses if `old_name` is absent or `new_name` already exists (no silent clobber).
    Snapshot-then-write under _LIB_LOCK. Returns True if renamed."""
    old = (old_name or "").strip()
    new = (new_name or "").strip()
    if not old or not new or old == new:
        return False
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not sym_path.exists():
        return False
    with _LIB_LOCK:
        blocks = extract_symbol_blocks(read_text(sym_path))
        names = {extract_symbol_name(b) for b in blocks}
        if old not in names or new in names:
            if log is not None:
                log.write(f"Rename refused: '{old}' missing or '{new}' already exists.")
            return False
        new_blocks = [rename_symbol_block(b, new) if extract_symbol_name(b) == old else b
                      for b in blocks]
        new_text = insert_blocks_into_target(
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
            new_blocks)
        _snapshot_then_write(sym_path, new_text, log or _NullLog())
    if log is not None:
        log.write(f"Renamed symbol '{old}' → '{new}'.")
    return True


def duplicate_symbol_for_footprint(cfg: Dict[str, str], source_symbol_name: str,
                                   footprint_stem: str, log: UILog = None,
                                   name: Optional[str] = None) -> Optional[str]:
    """Make a footprint-only orphan placeable by REUSING an existing library symbol:
    duplicate `source_symbol_name`'s block (pins/graphics and all), rename it (default:
    the footprint stem, de-duplicated so nothing is overwritten), and repoint its
    Footprint at `MyFootprints:<footprint_stem>`. Returns the new symbol name, or None
    if the source is missing / inputs are blank.

    The richer sibling of create_symbol_for_footprint (which emits a geometry-free
    stub): use this when the orphan should share a real, already-drawn symbol.
    Snapshot-then-write under _LIB_LOCK, like the other library mutations."""
    stem = (footprint_stem or "").strip()
    src_name = (source_symbol_name or "").strip()
    if not stem or not src_name:
        return None
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not str(sym_path):
        return None
    with _LIB_LOCK:
        text = read_text(sym_path) if sym_path.exists() else _SYMBOL_LIB_HEADER
        blocks = extract_symbol_blocks(text)
        src_block = next((b for b in blocks if extract_symbol_name(b) == src_name), None)
        if src_block is None:
            if log is not None:
                log.write(f"Reuse symbol: source '{src_name}' not found.")
            return None
        existing = {extract_symbol_name(b) for b in blocks}
        want = (name or stem).strip()
        if not want:
            return None
        final = want
        n = 2
        while final in existing:
            final = f"{want}_{n}"
            n += 1
        dup = rename_symbol_block(src_block, final)
        dup = set_symbol_property(dup, "Footprint", qualify_footprint(stem))
        new_text = insert_blocks_into_target(text, [dup])
        _snapshot_then_write(sym_path, new_text, log or _NullLog())
    if log is not None:
        log.write(f"Reused symbol '{src_name}' as '{final}' for footprint '{stem}'.")
    return final


def duplicate_part(cfg: Dict[str, str], row: Dict, new_name: str,
                   log: UILog = None) -> Optional[str]:
    """Duplicate a part's symbol under `new_name` to make a variant: copy the primary
    symbol's block verbatim (pins, graphics, footprint + 3D-model links, manufacturer /
    datasheet / description fields all intact), rename it, and RESET its Value property
    to the new name so the duplicate does not falsely inherit the source's manufacturer
    part number (a fresh variant needs its own MPN — until set it reads 'not orderable',
    the honest state). The new name is de-duplicated so nothing is overwritten.

    Returns the new symbol name, or None if the part has no symbol / `new_name` is blank.
    Snapshot-then-write under _LIB_LOCK, like every other library mutation."""
    names = list(row.get("symbols") or [])
    src_name = (names[0] if names else "").strip()
    want = (new_name or "").strip()
    if not src_name or not want:
        return None
    sym_path = Path(cfg.get("SymbolLib", ""))
    if not sym_path.exists():
        return None
    with _LIB_LOCK:
        text = read_text(sym_path)
        blocks = extract_symbol_blocks(text)
        src_block = next((b for b in blocks if extract_symbol_name(b) == src_name), None)
        if src_block is None:
            if log is not None:
                log.write(f"Duplicate: source symbol '{src_name}' not found.")
            return None
        existing = {extract_symbol_name(b) for b in blocks}
        final = want
        n = 2
        while final in existing:
            final = f"{want}_{n}"
            n += 1
        dup = rename_symbol_block(src_block, final)
        # Value is this app's MPN field (the Part Number edit writes "Value"); reset it to
        # the new name so the duplicate does not claim the source's exact part number.
        dup = set_symbol_property(dup, "Value", final)
        new_text = insert_blocks_into_target(text, [dup])
        _snapshot_then_write(sym_path, new_text, log or _NullLog())
    if log is not None:
        log.write(f"Duplicated symbol '{src_name}' as '{final}'.")
    return final


# ── Library CRUD: delete footprint / model / whole part ──────────────────────
# The library could ADD symbols/footprints/models (import, drop-in) and remove
# SYMBOLS (remove_symbol_by_name/_by_indices) but had no way to delete a footprint
# FILE, a 3D-model FILE, or a whole part. These close that gap. Every delete
# snapshots the file into libs/.trash/ first (same undo store as the symbol-lib
# rewrites) so it is recoverable, and reports who still references a deleted asset
# so the caller can warn the user about the dangling links it creates.
def _trash_file(cfg: Dict[str, str], path, log: UILog = None):
    """Copy `path` into libs/.trash/<timestamp>/ before a destructive delete so it
    can be recovered, anchored under the symbol-library parent (the same libs root
    the symbol-library undo snapshots use). Best-effort; never raises. Returns the
    snapshot path, or None."""
    try:
        src = Path(path)
        if not src.is_file():
            return None
        sym_parent = _cfg_path(cfg, "SymbolLib").parent
        from datetime import datetime as _dt
        dst_dir = sym_parent / ".trash" / _dt.now().strftime("%Y%m%d_%H%M%S")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.copy2(src, dst)
        _prune_trash(_cfg_path(cfg, "SymbolLib"))
        return dst
    except Exception as e:                     # noqa: BLE001
        if log is not None:
            log.write(f"Trash snapshot failed (continuing): {e}")
        return None


def symbols_referencing_footprint(cfg: Dict[str, str], stem: str) -> List[str]:
    """Names of every library symbol whose Footprint property points at `stem`.
    Used to warn that deleting the footprint would dangle these symbols."""
    sym_path = _cfg_path(cfg, "SymbolLib")
    if not sym_path.is_file():
        return []
    out: List[str] = []
    try:
        for b in extract_symbol_blocks(read_text(sym_path)):
            if symbol_footprint_ref(b) == stem:
                out.append(extract_symbol_name(b))
    except Exception:                          # noqa: BLE001
        pass
    return out


def footprints_referencing_model(cfg: Dict[str, str], name: str) -> List[str]:
    """Stems of every library footprint whose (model …) line references `name`.
    Used to warn that deleting the model would dangle these footprints."""
    fp_dir = _cfg_path(cfg, "FootprintLib")
    if not fp_dir.is_dir():
        return []
    out: List[str] = []
    for p in sorted(fp_dir.glob("*.kicad_mod")):
        try:
            if footprint_model_ref(read_text(p)) == name:
                out.append(p.stem)
        except Exception:                      # noqa: BLE001
            pass
    return out


def remove_footprint(cfg: Dict[str, str], stem: str, log: UILog = None) -> Dict[str, object]:
    """Delete the `<stem>.kicad_mod` file from the footprint library (undo-safe).

    Returns {ok, removed, referenced_by, reason}. `referenced_by` lists the symbols
    that STILL point at this footprint AFTER the call — i.e. links this delete
    dangles — computed before the file is removed so the caller can warn the user.
    Does NOT commit; the caller orchestrates git.
    """
    log = log or _NullLog()
    stem = (stem or "").strip()
    if not stem:
        return {"ok": False, "removed": False, "referenced_by": [], "reason": "no footprint given"}
    target = _cfg_path(cfg, "FootprintLib") / f"{stem}.kicad_mod"
    if not target.is_file():
        return {"ok": False, "removed": False, "referenced_by": [],
                "reason": f"footprint '{stem}' not found"}
    referenced_by = symbols_referencing_footprint(cfg, stem)
    try:
        _trash_file(cfg, target, log)
        target.unlink()
    except OSError as e:
        return {"ok": False, "removed": False, "referenced_by": referenced_by,
                "reason": str(e)}
    log.write(f"Deleted footprint '{stem}'.")
    return {"ok": True, "removed": True, "referenced_by": referenced_by, "reason": ""}


def remove_model(cfg: Dict[str, str], name: str, log: UILog = None) -> Dict[str, object]:
    """Delete a 3D-model file (by exact filename) from the model library (undo-safe).

    Returns {ok, removed, referenced_by, reason}. `referenced_by` lists the footprint
    stems whose (model …) line STILL points at this model AFTER the call — the links
    this delete dangles. Does NOT commit.
    """
    log = log or _NullLog()
    name = (name or "").strip()
    if not name:
        return {"ok": False, "removed": False, "referenced_by": [], "reason": "no model given"}
    target = _cfg_path(cfg, "ModelLib") / name
    if not target.is_file():
        return {"ok": False, "removed": False, "referenced_by": [],
                "reason": f"3D model '{name}' not found"}
    referenced_by = footprints_referencing_model(cfg, name)
    try:
        _trash_file(cfg, target, log)
        target.unlink()
    except OSError as e:
        return {"ok": False, "removed": False, "referenced_by": referenced_by,
                "reason": str(e)}
    log.write(f"Deleted 3D model '{name}'.")
    return {"ok": True, "removed": True, "referenced_by": referenced_by, "reason": ""}


def remove_part(cfg: Dict[str, str], row: Dict, log: UILog = None, *,
                delete_footprint: bool = False,
                delete_model: bool = False) -> Dict[str, object]:
    """Delete a whole part: its symbol(s), and — when asked — the footprint and 3D
    model FILES it keys on. `row` is a scan_library_grouped row.

    Symbols are removed FIRST, so any `still_referenced` warning names only OTHER
    parts that share the deleted footprint/model (this part's own symbols are gone
    and never counted). Returns:

        {ok, symbols_removed:[names], footprint_removed:stem|None,
         model_removed:name|None, still_referenced:{footprint:{stem:[syms]},
         model:{name:[fp_stems]}}, reason}

    Deletes are undo-safe (snapshot to libs/.trash) but NOT auto-committed — the
    caller (e.g. an end-to-end "Delete Part" action) commits the batch.
    """
    log = log or _NullLog()
    result: Dict[str, object] = {
        "ok": False, "symbols_removed": [], "footprint_removed": None,
        "model_removed": None, "still_referenced": {}, "reason": "",
    }
    sym_path = _cfg_path(cfg, "SymbolLib")

    removed_syms: List[str] = []
    for s in list(row.get("symbols") or []):
        if remove_symbol_by_name(sym_path, s, log):
            removed_syms.append(s)
    result["symbols_removed"] = removed_syms

    fp = row.get("footprint")
    if delete_footprint and fp:
        r = remove_footprint(cfg, fp, log)
        if r["removed"]:
            result["footprint_removed"] = fp
        if r["referenced_by"]:                 # symbols outside this part -> now dangling
            result["still_referenced"].setdefault("footprint", {})[fp] = r["referenced_by"]

    model = row.get("model")
    if delete_model and model:
        r = remove_model(cfg, model, log)
        if r["removed"]:
            result["model_removed"] = model
        remaining = [s for s in r["referenced_by"] if s != fp]
        if remaining:                          # footprints outside this part -> now dangling
            result["still_referenced"].setdefault("model", {})[model] = remaining

    result["ok"] = bool(removed_syms or result["footprint_removed"] or result["model_removed"])
    if not result["ok"]:
        result["reason"] = "nothing to delete (part had no removable symbol/footprint/model)"
    return result


# ── Footprint density variants + true duplicates (Maintenance IA, LM:2117) ───
# KiCad IPC footprints ship as density levels: a shared base plus a -L (least),
# -N (nominal), -M (most) courtyard variant (e.g. SOT23_DIO-L / SOT23_DIO-M).
# Those are DELIBERATELY distinct footprints, not duplicates — collapsing them
# under their base keeps the maintenance view legible and stops the dedup tool
# from flagging a legitimate variant pair as a duplicate. A TRUE duplicate is
# two footprint files whose geometry is byte-identical (ignoring the name).
_DENSITY_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<density>[LNM])$")


def footprint_density_variant(stem: str) -> Tuple[str, Optional[str]]:
    """Split a footprint stem into (base, density) where density is 'L'/'N'/'M'
    for an IPC density-level variant, else (stem, None). Only a trailing -L/-N/-M
    on a non-empty base counts, so a bare 'DRT3' or a stem ending in '-X' is left
    whole."""
    s = (stem or "").strip()
    m = _DENSITY_SUFFIX_RE.match(s)
    if m and m.group("base"):
        return m.group("base"), m.group("density")
    return s, None


def group_footprint_variants(stems) -> Dict[str, List[str]]:
    """Collapse footprint stems under their density base: {base: [stems…]} with
    each stem list sorted. A footprint with no density suffix maps to itself
    ('DRT3' -> {'DRT3': ['DRT3']}); a base with variants collapses them
    ('SOT23_DIO' -> ['SOT23_DIO-L', 'SOT23_DIO-M']). The base key is the shared
    root, so the view shows one row per real footprint family."""
    out: Dict[str, List[str]] = {}
    for stem in stems:
        base, _density = footprint_density_variant(stem)
        out.setdefault(base, []).append(stem)
    for base in out:
        out[base] = sorted(out[base])
    return out


def _normalize_footprint_geometry(text: str) -> str:
    """A footprint's identity-independent geometry signature: strip the
    `(footprint "<name>" …)` name token and collapse whitespace so two files
    that differ ONLY in their name/formatting hash identical. A real geometry
    difference (a moved pad, a different courtyard) still differs, so a density
    variant is NOT a false duplicate."""
    t = text or ""
    # Drop the leading footprint NAME token so a rename alone isn't a difference.
    t = re.sub(r'\(footprint\s+"[^"]*"', '(footprint', t, count=1)
    t = re.sub(r'\(footprint\s+[^\s()]+', '(footprint', t, count=1)
    return re.sub(r"\s+", " ", t).strip()


def find_duplicate_footprints(cfg: Dict[str, str]) -> List[List[str]]:
    """Find TRUE duplicate footprints: groups of ≥2 footprint stems whose
    geometry is identical once the name is ignored (see _normalize_footprint_
    geometry). Density variants (-L/-M/-N) have different courtyards, so they are
    never grouped here. Each returned group is the sorted stems; groups are
    ordered by their first stem. Read-only — safe for a preview."""
    fp_dir = _cfg_path(cfg, "FootprintLib")
    if not fp_dir.is_dir():
        return []
    by_sig: Dict[str, List[str]] = {}
    for p in sorted(fp_dir.glob("*.kicad_mod")):
        try:
            sig = _normalize_footprint_geometry(read_text(p))
        except Exception:  # noqa: BLE001 - a preview never crashes on one bad file
            continue
        by_sig.setdefault(sig, []).append(p.stem)
    dups = [sorted(stems) for stems in by_sig.values() if len(stems) > 1]
    dups.sort(key=lambda g: g[0].lower())
    return dups


def dedupe_footprint_library(cfg: Dict[str, str], log: UILog = None) -> int:
    """Delete TRUE duplicate footprint files, keeping the first (alphabetically)
    of each duplicate group. Returns the number of files removed. Density
    variants are NEVER touched (they aren't duplicates). Runs under _LIB_LOCK so
    it can't race a watcher import."""
    fp_dir = _cfg_path(cfg, "FootprintLib")
    if not fp_dir.is_dir():
        return 0
    with _LIB_LOCK:
        groups = find_duplicate_footprints(cfg)
        removed = 0
        for stems in groups:
            for stem in stems[1:]:                      # keep stems[0], drop the rest
                fp = fp_dir / f"{stem}.kicad_mod"
                try:
                    if fp.exists():
                        fp.unlink()
                        removed += 1
                except Exception as e:  # noqa: BLE001
                    (log or _NullLog()).write(f"WARN could not remove duplicate {stem}: {e}")
        if log is not None:
            log.write(f"Removed {removed} duplicate footprint(s)." if removed
                      else "No duplicate footprints to remove.")
        return removed


def _natural_ref(ref: str):
    """Sort key so R2 < R10 (prefix, then numeric index)."""
    m = re.match(r"([A-Za-z_]+)(\d+)", ref or "")
    return (m.group(1), int(m.group(2))) if m else (ref or "", 0)


def mouser_lookup_from_config(cfg: Dict[str, str] = None):
    """A Mouser lookup callable if a key is configured, else None. Reads the key from
    the MOUSER_API_KEY environment variable or the baked-in app default (SP1)."""
    key = resolve_mouser_key(cfg)
    return make_mouser_lookup(key) if key else None


def make_provider_chain(providers):
    """providers: [(name, lookup_fn)] in PREFERENCE order (Mouser first). Returns a
    lookup(mpn) that tries each provider in order and returns the FIRST hit tagged with
    'source'=<name>, else None. Extensible: register any verified distributor adapter as
    a (name, lookup_fn). A part no provider carries comes back None — the signal to
    source it MANUALLY. A throwing/dead provider is skipped, not fatal."""
    def chain(mpn):
        for name, fn in providers:
            try:
                r = fn(mpn)
            except Exception:                        # noqa: BLE001 — a dead provider is just skipped
                r = None
            if r:
                return {**r, "source": name}
        return None
    return chain


def providers_from_config(cfg: Dict[str, str] = None):
    """The distributor lookup chain from configured sources. Mouser is the PREFERRED
    provider (baked key); LCSC (key-free jlcsearch) is the automatic fallback for
    anything Mouser does not carry — so sourcing + volume pricing work with zero
    configuration. Returns a source-tagged lookup(mpn), or None only when NO provider is
    available (no Mouser key AND LCSC disabled). Register more distributors by adding a
    verified adapter to the list."""
    providers = []
    mk = resolve_mouser_key(cfg)
    if mk:
        providers.append(("Mouser", make_mouser_lookup(mk)))
    if lcsc_enabled(cfg):
        providers.append(("LCSC", make_lcsc_lookup()))
    dk_id, dk_secret = resolve_digikey_creds(cfg)
    if dk_id and dk_secret:                          # last-resort, only when creds present
        providers.append(("DigiKey", make_digikey_lookup(dk_id, dk_secret)))
    return make_provider_chain(providers) if providers else None


def consolidated_bom(boards: Dict[str, list], lookup=None, price_lookup=None) -> dict:
    """Merge the BOMs of several boards into one purchasing list.

    `boards`: {board_name: [.kicad_sch sheet paths]} — one entry per board (parent +
    each card), each a list of its schematic sheets. Runs the smart per-sheet BOM,
    groups by MPN (else value+footprint) across ALL boards, sums the quantity, and
    keeps the per-board breakdown + reference designators. If a `lookup` is given it
    fills blank manufacturer/datasheet once per unique part. Returns {rows,
    board_names, csv, line_count, total_parts}. Read-only."""
    board_names = list(boards)
    merged: dict = {}
    for board, sheets in boards.items():
        for sheet in sheets:
            for r in bom_from_kicad_schematic(sheet)["rows"]:
                key = r["mpn"] or ("VF", r["value"], r["footprint"])
                m = merged.setdefault(key, {
                    "mpn": r["mpn"], "manufacturer": r["manufacturer"], "value": r["value"],
                    "has_real_mpn": bool(r["mpn"]),   # ONE identity contract, shared with the Library
                    "footprint": r["footprint"], "datasheet": r["datasheet"],
                    "description": r["description"], "total_qty": 0,
                    "per_board": {}, "refs_by_board": {}})
                m["total_qty"] += r["qty"]
                m["per_board"][board] = m["per_board"].get(board, 0) + r["qty"]
                m["refs_by_board"][board] = sorted(
                    set(m["refs_by_board"].get(board, []) + r["refs"]), key=_natural_ref)
                for f in ("manufacturer", "datasheet", "description"):
                    if not m[f] and r.get(f):
                        m[f] = r[f]

    if lookup:
        for m in merged.values():
            if not m["mpn"]:
                m["source"] = ""                     # generic passive, no distributor lookup
                continue
            res = lookup(m["mpn"])
            if res:
                # The provider chain tags each hit with its TRUE source; default to
                # '' (unknown) rather than 'Mouser' so a raw single-provider lookup
                # that omits the tag never mislabels an LCSC/DigiKey part as Mouser.
                m["source"] = res.get("source", "")
                for f in ("manufacturer", "datasheet"):
                    if not m[f] and res.get(f):
                        m[f] = res[f]
            else:
                m["source"] = "NOT FOUND"

    rows = sorted(merged.values(), key=lambda r: (r["value"].lower(), r["footprint"].lower()))
    sourced = bool(lookup)
    priced = price_lookup is not None
    if priced:
        _price_rows(rows, price_lookup, "total_qty")
    out = {"rows": rows, "board_names": board_names,
           "csv": _bom_consolidated_csv(rows, board_names, sourced, priced),
           "line_count": len(rows), "total_parts": sum(r["total_qty"] for r in rows)}
    if sourced:
        out["not_on_mouser"] = [r["mpn"] or r["value"] for r in rows
                                if r.get("source") not in ("Mouser", "")]
    if priced:
        out["cost"] = bom_cost_summary(rows)
    return out


_BASIC_PREFIXES = {"R", "C", "L", "FB"}   # passives identified by value, not a specific MPN


def is_basic_part(ref, value, mpn) -> bool:
    """A 'basic' part (PROJ-09): a standard passive a fab stocks by value alone —
    a resistor / capacitor / inductor / ferrite bead with a value and no specific
    manufacturer part number. The offline analogue of JLCPCB's basic-vs-extended."""
    if mpn and str(mpn).strip() and str(mpn).strip().lower() not in _PLACEHOLDERS:
        return False
    m = re.match(r"[A-Za-z]+", str(ref or ""))
    prefix = m.group(0).upper() if m else ""
    return prefix in _BASIC_PREFIXES and bool(str(value or "").strip())


def _bom_components(sch_path) -> list:
    """Every real BOM component (ref, props) in one .kicad_sch — skips power /
    virtual / excluded-from-BOM symbols. [] for a non-schematic file."""
    from fp_render import parse_sexpr
    root = parse_sexpr(Path(sch_path).read_text(encoding="utf-8", errors="replace"))
    if not root or root[0] != "kicad_sch":
        return []
    out = []
    for node in root[1:]:
        if not (isinstance(node, list) and node and node[0] == "symbol"):
            continue
        lib_id, props, in_bom = "", {}, True
        for c in node[1:]:
            if not (isinstance(c, list) and c):
                continue
            if c[0] == "lib_id" and len(c) > 1:
                lib_id = c[1]
            elif c[0] == "property" and len(c) > 2:
                props[c[1]] = c[2]
            elif c[0] == "in_bom" and len(c) > 1:
                in_bom = c[1] != "no"
            elif c[0] == "exclude_from_bom":
                in_bom = False
        ref = props.get("Reference", "")
        if not ref or ref.startswith("#") or lib_id.lower().startswith("power:") or not in_bom:
            continue
        out.append((ref, props))
    return out


# ── cost / procurement roll-up ────────────────────────────────────────────────
def _coerce_price(v):
    """A Mouser price ('$0.10', '1,250.00', a number) -> float, or None if it can't
    be parsed (e.g. 'Call for pricing')."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lstrip("$").replace(",", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def price_at_qty(price_breaks, qty):
    """The applicable unit price for ordering `qty` units from a price-break ladder
    [{qty, price}, ...]: the price of the largest break quantity <= qty. Below the
    first break, falls back to that first break's price. Returns None when the ladder
    is empty or qty is unparseable — a caller then keeps the qty-1 price."""
    if not price_breaks:
        return None
    try:
        q = int(float(qty))
    except (TypeError, ValueError):
        return None
    ladder = sorted(price_breaks, key=lambda b: b["qty"])
    applicable = None
    for b in ladder:
        if b["qty"] <= q:
            applicable = b["price"]
        else:
            break
    return applicable if applicable is not None else ladder[0]["price"]


def line_extended(unit_price, qty):
    """Extended line cost = unit_price * qty, or None when either is missing. Rounded
    to 4 dp so fractional-cent unit prices don't accumulate float noise."""
    p = _coerce_price(unit_price)
    try:
        q = int(float(qty))          # tolerate "3" / "3.0" / 3.0, not just ints
    except (TypeError, ValueError):
        q = 0
    return round(p * q, 4) if (p is not None and q) else None


def bom_cost_summary(rows) -> dict:
    """Roll up a BOM's line costs. Sums the extended cost of every PRICED line and
    counts unpriced lines separately, so a partial total is never mistaken for the
    whole. qty comes from 'qty' (project BOM) or 'total_qty' (consolidated). Returns
    {total_cost, priced_lines, unpriced_lines, line_count, currency}."""
    total = 0.0
    priced = unpriced = 0
    for r in rows:
        qty = r.get("qty", r.get("total_qty", 0))
        ext = r.get("extended")
        if ext is None:
            ext = line_extended(r.get("unit_price"), qty)
        if ext is None:
            unpriced += 1
        else:
            total += ext
            priced += 1
    return {"total_cost": round(total, 2), "priced_lines": priced,
            "unpriced_lines": unpriced, "line_count": len(rows), "currency": "USD"}


def _board_count(boards) -> int:
    """A build's board count as a whole number >= 1: anything unparseable or below 1
    (you always build at least one board) folds to 1."""
    try:
        n = int(boards)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def _row_cost_at_qty(r, boards):
    """The order quantity, volume unit price, and extended cost for ONE BOM line at a
    build of `boards` boards (a pre-normalized count >= 1). Order qty = per_board_qty *
    boards; the unit price is re-read from the line's price-break ladder at that scaled
    qty (a bigger run buys down onto a cheaper break), falling back to the stored qty-1
    unit_price when no ladder is present. per-board qty comes from 'qty' (project) or
    'total_qty' (consolidated). Returns (order_qty, unit_price, extended); unit_price and
    extended are None when the line is unpriced. Pure — never mutates `r`. Shared by
    bom_cost_at_qty and priced_bom_csv_at_qty so the projected total and the projected
    per-line sheet can never drift."""
    per_board = r.get("qty", r.get("total_qty", 0)) or 0
    try:
        per_board = int(per_board)
    except (TypeError, ValueError):
        per_board = 0
    order_qty = per_board * boards
    ladder = r.get("price_breaks")
    unit = price_at_qty(ladder, order_qty) if ladder else None
    if unit is None:
        unit = _coerce_price(r.get("unit_price"))
    ext = round(unit * order_qty, 4) if (unit is not None and order_qty) else None
    return order_qty, unit, ext


def bom_cost_at_qty(rows, boards) -> dict:
    """Project a priced BOM's cost for building `boards` copies of the board. Each line's
    order quantity scales to per_board_qty * boards, and its unit price is re-read from
    the price-break ladder at that scaled quantity (so a bigger run buys down onto a
    cheaper break) — falling back to the stored qty-1 unit_price when no ladder is
    present. Mirrors bom_cost_summary's priced/unpriced bookkeeping so the projection's
    counts line up with the base build. Pure: never mutates `rows`, never hits the
    network. per-board qty comes from 'qty' (project) or 'total_qty' (consolidated); a
    board count below 1 (or unparseable) is treated as 1. Returns {boards, total_cost,
    priced_lines, unpriced_lines, currency}."""
    n = _board_count(boards)
    total = 0.0
    priced = unpriced = 0
    for r in rows:
        _order_qty, _unit, ext = _row_cost_at_qty(r, n)
        if ext is None:
            unpriced += 1
        else:
            total += ext
            priced += 1
    return {"boards": n, "total_cost": round(total, 2), "priced_lines": priced,
            "unpriced_lines": unpriced, "currency": "USD"}


def bom_procurement_summary(rows, boards=1) -> str:
    """A one-line, human-readable procurement digest of a BOM — the headline facts an
    engineer pastes into a purchase request or a chat message: line count, total parts,
    per-board cost (plus the run total when building more than one board), the critical-path
    lead time, and how many lines are still unpriced. Builds on the same roll-ups the
    on-screen summary uses (bom_cost_summary / bom_cost_at_qty / bom_lead_time) so the copied
    text can never disagree with what's shown. The cost figure appears only when at least one
    line is priced, the lead only when a line carries lead data, and the unpriced caveat only
    when a line lacks a price — nothing is invented. Pure, offline. Returns the summary
    string (prefixed 'BOM: ')."""
    n = _board_count(boards)
    cost = bom_cost_summary(rows)
    lead = bom_lead_time(rows)
    total_parts = sum(_bom_line_qty(r) for r in rows)
    parts_lbl = "parts/board" if n > 1 else "parts"
    pieces = [f"{cost['line_count']} lines", f"{total_parts} {parts_lbl}"]
    if cost["priced_lines"]:
        pieces.append(f"${cost['total_cost']:,.2f}/board")   # prototype (qty-1) per-board cost
        if n > 1:
            run = bom_cost_at_qty(rows, n)["total_cost"]     # volume-priced at the scaled order
            pieces.append(f"×{n}: ${run:,.2f} (${run / n:,.2f} each)")
    if lead["any"]:
        who = f" ({lead['critical_mpn']})" if lead["critical_mpn"] else ""
        pieces.append(f"critical path {lead['max_weeks']} wk{who}")
    if cost["unpriced_lines"]:
        pieces.append(f"{cost['unpriced_lines']} unpriced")
    return "BOM: " + " · ".join(pieces)


def priced_bom_csv_at_qty(rows, boards=1) -> dict:
    """A priced purchasing sheet for a build of `boards` copies of the board — the
    line-by-line form of bom_cost_at_qty's headline projection. Each line's Order Qty =
    per_board_qty * boards and its Unit/Ext Price are re-read from the price-break ladder
    at that scaled quantity (a bigger run buys down onto a cheaper break), via the same
    _row_cost_at_qty helper the total uses, so the sheet and the total can never disagree.
    Every row is one line (priced or not), mirroring the cost roll-up's counting; the
    Per-Board Qty column keeps the base build visible beside the scaled order. Lines are
    ranked by run spend (biggest Ext Price first, unpriced last) so the cost drivers lead.
    per-board qty comes from 'qty' (project) or 'total_qty' (consolidated); a board count
    below 1 is treated as 1. Read-only. Returns {csv, boards, line_count, priced_lines,
    unpriced_lines, total_cost, currency}."""
    import csv as _csv
    import io as _io
    n = _board_count(boards)
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["MPN", "Manufacturer", "Value", "Footprint", "Per-Board Qty", "Order Qty",
                "Source", "Dist P/N", "Unit Price", "Ext Price", "Stock", "Lifecycle",
                "Lead (wks)"])
    priced = unpriced = 0
    total = 0.0
    costed = []
    for r in rows:
        order_qty, unit, ext = _row_cost_at_qty(r, n)
        if ext is None:
            unpriced += 1
        else:
            total += ext
            priced += 1
        costed.append((r, order_qty, unit, ext))
    # This is a purchasing sheet, so lead with the cost drivers: biggest run spend first,
    # unpriced lines (no cost to rank) last. Stable, so equal-cost lines keep BOM order.
    costed.sort(key=lambda c: c[3] if c[3] is not None else -1.0, reverse=True)
    for r, order_qty, unit, ext in costed:
        per_board = order_qty // n                        # exact: order_qty = per_board * n
        w.writerow([r.get("mpn", ""), r.get("manufacturer", ""), r.get("value", ""),
                    r.get("footprint", ""), per_board, order_qty,
                    r.get("source", ""), _dist_pn(r),
                    f"{unit:.4f}" if unit is not None else "",
                    f"{ext:.4f}" if ext is not None else "",
                    r.get("stock", ""), r.get("lifecycle", ""),
                    _lead_weeks(r.get("lead_time")) if _lead_weeks(r.get("lead_time")) is not None else ""])
    return {"csv": buf.getvalue(), "boards": n, "line_count": len(rows),
            "priced_lines": priced, "unpriced_lines": unpriced,
            "total_cost": round(total, 2), "currency": "USD"}


def bom_cost_by_source(rows, boards=1) -> dict:
    """Split a priced BOM's projected cost by the distributor sourcing each line, so a
    multi-distributor build shows how much to order from each supplier (they sum to the
    whole-run Total). Uses the same per-line volume costing as bom_cost_at_qty (order qty
    = per_board * boards, volume unit price at that qty), so the split stays consistent
    with the headline projection. Only PRICED lines count; a priced line with a blank
    source is grouped as 'Unsourced', while unpriced lines are skipped entirely. per-board
    qty comes from 'qty' (project) or 'total_qty' (consolidated); a board count below 1 is
    treated as 1. Read-only. Returns {sources: {name: {total_cost, lines}}, currency}."""
    n = _board_count(boards)
    by: dict = {}
    for r in rows:
        _order_qty, _unit, ext = _row_cost_at_qty(r, n)
        if ext is None:
            continue
        src = (r.get("source") or "").strip() or "Unsourced"
        s = by.setdefault(src, {"total_cost": 0.0, "lines": 0})
        s["total_cost"] += ext
        s["lines"] += 1
    for s in by.values():
        s["total_cost"] = round(s["total_cost"], 2)
    return {"sources": by, "currency": "USD"}


def bom_sourcing_risks(rows, boards=1) -> dict:
    """Scan priced BOM rows for procurement risk — the failures worth catching BEFORE
    you order. A line is risky when its lifecycle is known and not Active (NRND / EOL /
    obsolete), when its stock is a known 0 (nothing to buy), or when known stock can't
    cover the line's order quantity for the whole run. Lines with unknown lifecycle/stock
    (never priced) are NOT risks — absence of data is not a warning. per-board qty comes
    from 'qty' (project) or 'total_qty' (consolidated), scaled by `boards` so stock
    coverage is judged against the run you're actually ordering (a board count below 1 is
    treated as 1). Returns {not_active, no_stock, insufficient_stock, risky_mpns, any}.
    Read-only."""
    n = _board_count(boards)
    not_active = no_stock = insufficient = 0
    risky: list = []
    for r in rows:
        flagged = False
        lc = (r.get("lifecycle") or "").strip()
        if lc and lc.lower() != "active":
            not_active += 1
            flagged = True
        stock = r.get("stock")
        if isinstance(stock, bool):                      # a stray bool is not a stock count
            stock = None
        if isinstance(stock, (int, float)):
            qty = r.get("qty", r.get("total_qty", 0)) or 0
            try:
                qty = int(qty) * n                       # order qty for the whole run
            except (TypeError, ValueError):
                qty = 0
            if stock <= 0:
                no_stock += 1
                flagged = True
            elif qty and stock < qty:
                insufficient += 1
                flagged = True
        if flagged:
            risky.append((r.get("mpn") or r.get("value") or "").strip())
    # Preserve first-seen order, drop blanks and duplicates.
    seen: dict = {}
    for m in risky:
        if m and m not in seen:
            seen[m] = True
    return {"not_active": not_active, "no_stock": no_stock,
            "insufficient_stock": insufficient, "risky_mpns": list(seen),
            "any": bool(not_active or no_stock or insufficient)}


def bom_line_stock_risk(r, boards=1) -> dict:
    """Stock coverage for ONE BOM line at a build of `boards` boards — the per-row form of
    bom_sourcing_risks' stock test, so a tinted table row can never disagree with the
    headline No-Stock / Low-Stock counts. required = per_board_qty * boards (a board count
    below 1 folds to 1); per-board qty comes from 'qty' (project) or 'total_qty'
    (consolidated). available is the line's known integer stock, or None when stock is
    unknown (a line that was never priced) — unknown is NOT a risk, absence of data is not a
    warning. kind is 'err' when known stock is 0 (nothing to buy), 'warn' when a positive
    known stock is below required (a short line), else None. `short` is True for both risky
    cases. Read-only, pure. Returns {kind, required, available, short}."""
    n = _board_count(boards)
    per_board = r.get("qty", r.get("total_qty", 0)) or 0
    try:
        per_board = int(per_board)
    except (TypeError, ValueError):
        per_board = 0
    required = per_board * n
    stock = r.get("stock")
    if isinstance(stock, bool) or not isinstance(stock, (int, float)):
        return {"kind": None, "required": required, "available": None, "short": False}
    # Branch on the RAW stock (not int(stock)) so the No-Stock vs Low-Stock split matches
    # bom_sourcing_risks EXACTLY — flooring first would call a fractional 0 < stock < 1 line
    # No-Stock while the aggregate counts it Low-Stock, breaking the "can never disagree"
    # invariant. `available` reports the whole count for display (real stock is integral).
    available = int(stock)
    if stock <= 0:
        return {"kind": "err", "required": required, "available": available, "short": True}
    if required and stock < required:
        return {"kind": "warn", "required": required, "available": available, "short": True}
    return {"kind": None, "required": required, "available": available, "short": False}


def bom_line_is_populated(r) -> bool:
    """Whether a BOM line is a real, orderable/identifiable line — it carries a part number
    OR a value. The 'Populated Lines Only' export predicate: it drops blank/placeholder
    lines (no MPN and no value) that a purchasing sheet should never carry. Pure."""
    return bool((r.get("mpn") or "").strip()) or bool((r.get("value") or "").strip())


def bom_line_is_priced(r) -> bool:
    """Whether a BOM line carries a usable price — a stored extended cost, or a unit price
    that parses to a number. The 'Priced Lines Only' export predicate. Pure."""
    if r.get("extended") is not None:
        return True
    return _coerce_price(r.get("unit_price")) is not None


def _lead_weeks(v):
    """Normalize a distributor lead-time value into whole weeks, or None when unknown.
    Providers disagree on shape: Mouser gives strings ("16 Weeks"), DigiKey gives a
    number of weeks (ManufacturerLeadWeeks), LCSC gives nothing. A numeric value is
    taken as weeks; a string is parsed for a leading count plus an optional unit
    (weeks/wks default, days converted up), and anything without a parseable number
    ("In Stock", "", None) returns None — unknown, not a warning. Negative -> None
    (garbage); 0 stays 0 (in stock, not a lead risk). Days round UP to whole weeks so
    a lead time is never understated. Read-only."""
    import math
    if isinstance(v, bool):                              # a stray bool is not a duration
        return None
    if isinstance(v, (int, float)):
        return int(v) if v >= 0 else None
    if not isinstance(v, str):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", v)
    if not m:
        return None
    n = float(m.group(1))
    if n < 0:
        return None
    if re.search(r"\bday", v, re.IGNORECASE):
        return math.ceil(n / 7.0)
    return int(n)


def bom_lead_time(rows) -> dict:
    """Find the critical-path part — the priced line with the longest manufacturer lead
    time — so an order can be planned around the part that gates it. Scans each row's
    threaded `lead_time` through `_lead_weeks`; lines without parseable lead data are
    ignored (absence is not a risk). Ties keep the first-seen line. Returns
    {max_weeks, critical_mpn, with_lead, any}; max_weeks/critical_mpn are None when no
    line carries lead data. Read-only."""
    max_weeks = None
    critical = None
    with_lead = 0
    for r in rows:
        w = _lead_weeks(r.get("lead_time"))
        if w is None:
            continue
        with_lead += 1
        if max_weeks is None or w > max_weeks:
            max_weeks = w
            critical = (r.get("mpn") or r.get("value") or "").strip() or None
    return {"max_weeks": max_weeks, "critical_mpn": critical,
            "with_lead": with_lead, "any": max_weeks is not None}


def _dist_pn(r) -> str:
    """The distributor's own part number for a priced row, matched to its Source so the
    exported BOM says 'order from {Source} by {this P/N}': LCSC -> lcsc_pn, Mouser ->
    mouser_pn, DigiKey -> digikey_pn. Falls back to whichever distributor P/N is present
    when the source is unknown or carried no matching number. Returns '' when nothing
    was threaded."""
    src = (r.get("source") or "").strip().lower()
    lcsc = (r.get("lcsc_pn") or "").strip()
    mouser = (r.get("mouser_pn") or "").strip()
    digikey = (r.get("digikey_pn") or "").strip()
    if src == "lcsc":
        return lcsc or mouser or digikey
    if src == "mouser":
        return mouser or lcsc or digikey
    if src == "digikey":
        return digikey or mouser or lcsc
    return lcsc or mouser or digikey


def _price_rows(rows, price_lookup, qty_key: str):
    """Attach unit_price / stock / lifecycle / extended to each row from a pricing
    lookup (e.g. the Mouser provider), one call per unique MPN. Rows without an MPN
    are left unpriced — a passive's value is not a purchasable part number."""
    cache: dict = {}
    for r in rows:
        mpn = r.get("mpn")
        if not mpn:
            continue
        if mpn not in cache:
            try:
                cache[mpn] = price_lookup(mpn)
            except Exception:  # noqa: BLE001
                cache[mpn] = None
        res = cache[mpn] or {}
        qty = r.get(qty_key, 0)
        # Prefer the price-break ladder so the line is costed at its real quantity
        # (volume price); fall back to the qty-1 unit_price when no ladder is given.
        ladder = res.get("price_breaks")
        vol = price_at_qty(ladder, qty) if ladder else None
        if vol is not None:
            r["unit_price"] = vol
            r["extended"] = line_extended(vol, qty)
            r["price_breaks"] = ladder
        else:
            up = res.get("unit_price")
            if up is not None and up != "":
                r["unit_price"] = up
                r["extended"] = line_extended(up, qty)
                if ladder:
                    r["price_breaks"] = ladder
        if res.get("stock") is not None:
            r["stock"] = res.get("stock")
        if res.get("lifecycle"):
            r["lifecycle"] = res.get("lifecycle")
        if res.get("lead_time") not in (None, ""):
            r["lead_time"] = res.get("lead_time")
        # Thread the distributor that carried the part, its distributor part numbers,
        # the product page URL, and the vendor's category — so the BOM can show WHICH
        # source priced each line, an order export has the part numbers it needs, and the
        # procurement sheet can auto-fill the product link + a human category (the columns
        # a manual buy-sheet fills by hand). Non-destructive: a value already set stands.
        for k in ("source", "lcsc_pn", "mouser_pn", "digikey_pn", "url", "category"):
            v = res.get(k)
            if v and not r.get(k):
                r[k] = v


def _bom_project_csv(rows, priced: bool) -> str:
    """The project BOM export CSV for `rows` (Refs,Qty,Value,MPN,Manufacturer,Footprint,
    Datasheet,Description,Basic, + the priced Source/Dist P/N/Unit/Ext/Stock/Lifecycle
    columns when `priced`). Shared by the builder and the Export group's line-filtered
    re-export, so both emit the identical schema. Pure."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    head = ["Refs", "Qty", "Value", "MPN", "Manufacturer", "Footprint",
            "Datasheet", "Description", "Basic"]
    if priced:
        head += ["Source", "Dist P/N", "Unit Price", "Ext Price", "Stock", "Lifecycle"]
    w.writerow(head)
    for r in rows:
        line = [",".join(r.get("refs", [])), r.get("qty", ""), r.get("value", ""), r.get("mpn", ""),
                r.get("manufacturer", ""), r.get("footprint", ""), r.get("datasheet", ""),
                r.get("description", ""), "yes" if r.get("basic") else ""]
        if priced:
            ext = r.get("extended")
            line += [r.get("source", ""), _dist_pn(r), r.get("unit_price", ""),
                     f"{ext:.4f}" if ext is not None else "",
                     r.get("stock", ""), r.get("lifecycle", "")]
        w.writerow(line)
    return buf.getvalue()


def _bom_consolidated_csv(rows, board_names, sourced: bool, priced: bool) -> str:
    """The consolidated BOM export CSV for `rows` (MPN,Manufacturer,Value,Footprint,Total,
    [Source,] per-board columns, Datasheet, + priced columns) — the per-board breakdown a
    consolidated build carries. Shared by the builder and the line-filtered re-export. Pure."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    head = ["MPN", "Manufacturer", "Value", "Footprint", "Total"] + list(board_names) + ["Datasheet"]
    if sourced:
        head.insert(5, "Source")
    if priced:
        head += ["Dist P/N", "Unit Price", "Ext Price", "Stock", "Lifecycle"]
    w.writerow(head)
    for r in rows:
        row = [r.get("mpn", ""), r.get("manufacturer", ""), r.get("value", ""),
               r.get("footprint", ""), r.get("total_qty", "")]
        if sourced:
            row.append(r.get("source", ""))
        row += [(r.get("per_board") or {}).get(b, 0) for b in board_names] + [r.get("datasheet", "")]
        if priced:
            ext = r.get("extended")
            row += [_dist_pn(r), r.get("unit_price", ""), f"{ext:.4f}" if ext is not None else "",
                    r.get("stock", ""), r.get("lifecycle", "")]
        w.writerow(row)
    return buf.getvalue()


def bom_csv(rows, *, mode="project", board_names=None, priced=False, sourced=False) -> str:
    """Serialize BOM rows to the export CSV for `mode` ('project' | 'consolidated'),
    reproducing the exact columns the builders emit so a FILTERED subset (the Export group's
    'Populated'/'Priced' line filters) re-exports with the same schema. `priced`/`sourced`
    come from the ORIGINAL build, not re-detected from the rows, so filtering out every
    priced line still keeps the header stable. Locked byte-for-byte against the builders'
    own csv by test. Pure."""
    if mode == "consolidated":
        return _bom_consolidated_csv(rows, board_names or [], sourced, priced)
    return _bom_project_csv(rows, priced)


def _bom_from_components(comps, lookup=None,
                        enrich_fields=("manufacturer", "datasheet", "description"), price_lookup=None) -> dict:
    """Group (ref, props) components into BOM lines, enrich blanks via `lookup`,
    and flag basic parts. Shared by the single-sheet and whole-project builders.
    When `price_lookup` is given, each line with an MPN also gets unit_price /
    extended / stock / lifecycle and the result carries a `cost` roll-up."""
    groups: dict = {}
    for ref, props in comps:
        ident = part_identity(props, fallback=props.get("Value", ""))
        value = (props.get("Value") or "").strip()
        smpn = strict_mpn(props)
        # An IC (non-passive) with a manufacturer often carries its real MPN in the
        # Value field (SnapEDA/Mouser). Promote it — but NEVER for a passive, whose
        # Value ("10k") is a value, not a part number.
        if not smpn and ident["manufacturer"] and not is_basic_part(ref, value, None):
            smpn = value if value and value.lower() not in _PLACEHOLDERS else None
        # Fallback grouping keys on value+footprint AND manufacturer, so two parts
        # with the same value but different makers stay distinct lines.
        key = smpn or ("VF", value, props.get("Footprint", ""), ident["manufacturer"] or "")
        g = groups.setdefault(key, {
            "mpn": smpn, "manufacturer": ident["manufacturer"],
            "datasheet": ident["datasheet"], "description": ident["description"],
            "value": props.get("Value", ""), "footprint": props.get("Footprint", ""), "refs": []})
        g["refs"].append(ref)

    if lookup:
        for g in groups.values():
            if g["mpn"] and any(not g.get(f) for f in enrich_fields):
                res = lookup(g["mpn"])
                if res:
                    for f in enrich_fields:
                        if not g.get(f) and res.get(f):
                            g[f] = res[f]

    rows = []
    for g in groups.values():
        refs = sorted(g["refs"], key=_natural_ref)
        rows.append({"refs": refs, "qty": len(refs), "value": g["value"],
                     "mpn": g["mpn"] or "", "manufacturer": g["manufacturer"] or "",
                     "has_real_mpn": bool(g["mpn"]),   # ONE identity contract, shared with the Library
                     "footprint": g["footprint"], "datasheet": g["datasheet"] or "",
                     "description": g["description"] or "",
                     "basic": is_basic_part(refs[0] if refs else "", g["value"], g["mpn"])})
    rows.sort(key=lambda r: (r["value"].lower(), r["footprint"].lower(),
                             _natural_ref(r["refs"][0]) if r["refs"] else ("", 0)))

    priced = price_lookup is not None
    if priced:
        _price_rows(rows, price_lookup, "qty")

    out = {"rows": rows, "component_count": len(comps), "line_count": len(rows),
           "csv": _bom_project_csv(rows, priced)}
    if priced:
        out["cost"] = bom_cost_summary(rows)
    return out


def _bom_line_key(r):
    """Identity of a BOM line for diffing: its MPN (case-folded) when present, else
    value+footprint — the same grouping the consolidated BOM uses, so a value edited on
    an MPN'd part isn't seen as add+remove."""
    mpn = (r.get("mpn") or "").strip()
    if mpn:
        return ("MPN", mpn.upper())
    return ("VF", (r.get("value") or "").strip().lower(),
            (r.get("footprint") or "").strip().lower())


def _bom_line_qty(r) -> int:
    try:
        return int(float(r.get("qty", r.get("total_qty", 0)) or 0))
    except (TypeError, ValueError):
        return 0


def _bom_index(rows) -> dict:
    """Aggregate BOM rows by line key -> {qty, mpn, value, footprint}. Sums duplicate lines
    so a part split across two rows still compares by its true total quantity. Footprint is
    carried (first-seen) so a consumer can re-key an entry to its canonical line identity."""
    idx: dict = {}
    for r in rows or []:
        k = _bom_line_key(r)
        e = idx.setdefault(k, {"qty": 0, "mpn": (r.get("mpn") or "").strip(),
                               "value": (r.get("value") or "").strip(),
                               "footprint": (r.get("footprint") or "").strip()})
        e["qty"] += _bom_line_qty(r)
    return idx


def bom_diff(rows_a, rows_b) -> dict:
    """Compare two BOMs (rev A -> rev B). Lines match by MPN, else value+footprint.
    Returns {added, removed, changed, unchanged, csv}: `added` are lines only in B,
    `removed` only in A, `changed` are lines whose quantity moved (each with from_qty/
    to_qty/delta), `unchanged` the count of identical-quantity lines. Read-only."""
    a, b = _bom_index(rows_a), _bom_index(rows_b)
    added, removed, changed, unchanged = [], [], [], 0
    for k, e in b.items():
        if k not in a:
            added.append({"mpn": e["mpn"], "value": e["value"],
                          "footprint": e["footprint"], "qty": e["qty"]})
    for k, e in a.items():
        if k not in b:
            removed.append({"mpn": e["mpn"], "value": e["value"],
                            "footprint": e["footprint"], "qty": e["qty"]})
    for k, ea in a.items():
        if k not in b:
            continue
        eb = b[k]
        if ea["qty"] != eb["qty"]:
            changed.append({"mpn": eb["mpn"] or ea["mpn"], "value": eb["value"] or ea["value"],
                            "footprint": eb["footprint"] or ea["footprint"],
                            "from_qty": ea["qty"], "to_qty": eb["qty"],
                            "delta": eb["qty"] - ea["qty"]})
        else:
            unchanged += 1

    _lbl = lambda r: (r["mpn"] or r["value"]).lower()  # noqa: E731
    added.sort(key=_lbl); removed.sort(key=_lbl); changed.sort(key=_lbl)

    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["Change", "MPN", "Value", "From Qty", "To Qty", "Delta"])
    for r in added:
        w.writerow(["Added", r["mpn"], r["value"], 0, r["qty"], r["qty"]])
    for r in removed:
        w.writerow(["Removed", r["mpn"], r["value"], r["qty"], 0, -r["qty"]])
    for r in changed:
        w.writerow(["Changed", r["mpn"], r["value"], r["from_qty"], r["to_qty"], r["delta"]])
    return {"added": added, "removed": removed, "changed": changed,
            "unchanged": unchanged, "csv": buf.getvalue()}


def bom_diff_cost(rows_a, rows_b) -> dict:
    """Cost the change a revision makes (rev A -> rev B), from the NEWER revision's own
    prices. A parts diff (bom_diff) says WHAT changed; this says what it COSTS per board.
    Rev B is the current, priced build; rev A is the older side, which the compare paths
    reconstruct offline WITHOUT pricing. So every line B ADDS or grows can be costed from
    B's price, but a line B REMOVES exists only in A (no price anywhere) and cannot:

      added  line (only in B):  + qty_b  * unit_b
      changed line (in both):   + (qty_b - qty_a) * unit_b   (a shrink is a saving)
      removed line (only in A):   unknown -> excluded, counted in `removed_unpriced`

    so the delta is never silently one-sided. Uses each line's stored per-board unit_price
    (the qty-1 unit), the stable figure for a per-board delta — volume breaks depend on
    the whole order, not the change. Lines match on the same key as bom_diff (MPN, else
    value+footprint) and duplicate B lines take their first-seen price. A B line whose
    price won't parse contributes nothing (it is not a priced line). Pure, offline.
    Returns {delta, added_cost, changed_cost, removed_unpriced, priced, currency}:
    `delta` is the net per-board $ change from the priced lines; `priced` is False when
    rev B carried no usable price at all (so no cost delta is meaningful)."""
    a, b = _bom_index(rows_a), _bom_index(rows_b)
    bprice: dict = {}                                     # line key -> per-board unit price
    for r in rows_b or []:
        k = _bom_line_key(r)
        if k not in bprice:
            bprice[k] = _coerce_price(r.get("unit_price"))
    added_cost = changed_cost = 0.0
    for k, eb in b.items():
        u = bprice.get(k)
        if u is None:
            continue
        if k not in a:                                    # added: whole line is new
            added_cost += u * eb["qty"]
        elif a[k]["qty"] != eb["qty"]:                    # changed: only the qty delta
            changed_cost += u * (eb["qty"] - a[k]["qty"])
    removed_unpriced = sum(1 for k in a if k not in b)    # only in A -> no price to use
    # `priced` reflects whether rev B was priced at all — so the UI knows a cost delta is
    # meaningful even when the only change is a removed (uncostable) line, delta $0.
    priced = any(u is not None for u in bprice.values())
    return {"delta": round(added_cost + changed_cost, 2),
            "added_cost": round(added_cost, 2), "changed_cost": round(changed_cost, 2),
            "removed_unpriced": removed_unpriced, "priced": priced, "currency": "USD"}


def bom_diff_lead(rows_a, rows_b) -> dict:
    """Assess how a revision (rev A -> rev B) changes the procurement critical path — the
    longest manufacturer lead time gating the order. A cost delta (bom_diff_cost) is
    quantity-sensitive; a LEAD delta is presence-sensitive: a part's lead matters because
    the part is in the BOM at all, not how many of it. So only the lines rev B ADDS
    introduce new lead exposure (assessable from rev B's own threaded `lead_time`); a line
    rev B REMOVES exists only in the older, offline-reconstructed rev A (no lead data
    anywhere) and cannot be assessed — reported as `removed_unassessed`, never silently
    dropped; a qty-only CHANGE keeps a part already present, adding no new lead exposure, so
    it is ignored.

      added_max_weeks:  the longest lead among the ADDED lines (from rev B's `lead_time`)
      build_max_weeks:  the whole current build's critical path (bom_lead_time(rows_b))
      on_critical_path: an added part's lead >= the build critical path -> this revision
                        introduced (or tied) the part that now gates the order

    Lines match on the same key as bom_diff (MPN, else value+footprint); a part split across
    duplicate rev B rows takes its longest parseable lead so it is never understated. Pure,
    offline. Returns {added_max_weeks, added_critical_mpn, build_max_weeks,
    build_critical_mpn, on_critical_path, removed_unassessed, any}; the *_weeks / *_mpn are
    None when no line carries lead data, and `any` is False when rev B carried no parseable
    lead at all (nothing to show)."""
    a, b = _bom_index(rows_a), _bom_index(rows_b)
    lead_by_key: dict = {}                                 # line key -> longest lead (weeks)
    for r in rows_b or []:
        w = _lead_weeks(r.get("lead_time"))
        if w is None:
            continue
        k = _bom_line_key(r)
        if lead_by_key.get(k) is None or w > lead_by_key[k]:
            lead_by_key[k] = w
    added_max = None
    added_mpn = None
    for k, eb in b.items():
        if k in a:                                         # not an added line -> no new lead
            continue
        w = lead_by_key.get(k)
        if w is None:
            continue
        if added_max is None or w > added_max:
            added_max = w
            added_mpn = (eb["mpn"] or eb["value"] or "").strip() or None
    build = bom_lead_time(rows_b)
    on_cp = (added_max is not None and build["max_weeks"] is not None
             and added_max >= build["max_weeks"])
    removed_unassessed = sum(1 for k in a if k not in b)   # only in A -> no lead to read
    return {"added_max_weeks": added_max, "added_critical_mpn": added_mpn,
            "build_max_weeks": build["max_weeks"], "build_critical_mpn": build["critical_mpn"],
            "on_critical_path": on_cp, "removed_unassessed": removed_unassessed,
            "any": build["any"]}


def bom_diff_csv(d, rows_b) -> str:
    """The BOM diff (from bom_diff) as a CSV, extending the parts diff with a per-line
    'Cost Delta' column when rev B (rows_b — the current build) carries prices. Each added
    line costs qty*unit, each changed line delta*unit, read from rev B's own price
    (re-keyed by line identity) so the column sums to bom_diff_cost's headline delta; a
    removed line exists only in the older, unpriced revision, so its cost cell is blank.
    A 'Lead (wks)' column follows when any ADDED line carries a manufacturer lead time (from
    rev B's threaded `lead_time`): a lead delta is presence-sensitive, so only added parts —
    which introduce new procurement — get a lead cell; changed and removed lines leave it
    blank. This lets the shared CSV show that a newly-added part gates the order, the same
    signal the on-screen compare summary tag conveys. When rev B has no usable price AND no
    added line carries lead, the output matches bom_diff's plain form (same header and rows),
    so this is a safe drop-in for d['csv']. Rows are ordered added, then removed, then
    changed — bom_diff's own ordering. Pure, offline."""
    import csv as _csv
    import io as _io
    bprice: dict = {}
    blead: dict = {}                                       # line key -> longest lead (weeks)
    for r in rows_b or []:
        k = _bom_line_key(r)
        if k not in bprice:
            bprice[k] = _coerce_price(r.get("unit_price"))
        w_ = _lead_weeks(r.get("lead_time"))
        if w_ is not None and (blead.get(k) is None or w_ > blead[k]):
            blead[k] = w_
    priced = any(u is not None for u in bprice.values())
    # Only ADDED lines introduce new lead exposure, so the column opens only for those.
    added_keys = {_bom_line_key(e) for e in d["added"]}
    has_lead = any(blead.get(k) is not None for k in added_keys)

    def cost(entry, qty):
        u = bprice.get(_bom_line_key(entry))
        return None if u is None else round(u * qty, 2)

    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    head = ["Change", "MPN", "Value", "From Qty", "To Qty", "Delta"]
    if priced:
        head.append("Cost Delta")
    if has_lead:
        head.append("Lead (wks)")
    w.writerow(head)

    def row(change, e, frm, to, dq, c, lead):
        cells = [change, e["mpn"], e["value"], frm, to, dq]
        if priced:
            cells.append("" if c is None else f"{c:.2f}")
        if has_lead:
            cells.append("" if lead is None else lead)
        return cells
    for e in d["added"]:
        w.writerow(row("Added", e, 0, e["qty"], e["qty"], cost(e, e["qty"]),
                       blead.get(_bom_line_key(e))))
    for e in d["removed"]:
        w.writerow(row("Removed", e, e["qty"], 0, -e["qty"], None, None))
    for e in d["changed"]:
        w.writerow(row("Changed", e, e["from_qty"], e["to_qty"], e["delta"],
                       cost(e, e["delta"]), None))
    return buf.getvalue()


def _xlsx_col(idx: int) -> str:
    """0-based column index -> spreadsheet column letters (0->A, 25->Z, 26->AA)."""
    s = ""
    idx += 1
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _xlsx_escape(s: str) -> str:
    """XML-escape cell text and drop characters XML 1.0 forbids, so a stray control byte
    (or an ampersand / angle bracket in a description) can never make Excel refuse the file."""
    s = "".join(ch for ch in str(s) if ch in "\t\n\r" or ord(ch) >= 0x20)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _xlsx_number(num: float) -> str:
    """A fixed-point OOXML numeric literal for a <v> cell — NEVER scientific notation.

    repr(1e-5) is '1e-05', which Excel/LibreOffice reject as a numeric value, so a
    sub-1e-4 price (a real unit cost on high-volume passives) would corrupt the sheet.
    Whole numbers stay compact ('5'); fractions render fixed-point with trailing zeros
    trimmed ('0.00001')."""
    if num == int(num):
        return str(int(num))
    return format(num, ".6f").rstrip("0").rstrip(".")


# Shared workbook scaffolding for the .xlsx writers below. Style ids referenced by cells:
# s="1" bold (header / totals label); s="2" currency 0.00; s="3" bold currency (totals).
_XLSX_STYLES_BOLD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')
# Adds a currency number format ($#,##0.00) as styles 2 (plain) and 3 (bold, for totals).
_XLSX_STYLES_CURRENCY = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot;#,##0.00"/></numFmts>'
    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')


def _xlsx_package(sheet_xml: str, styles_xml: str, sheet_name: str = "Sheet1") -> bytes:
    """Zip a single-worksheet .xlsx from its worksheet + styles XML. Writes the fixed OPC
    parts (content types, relationships, workbook) around them so each writer only has to
    build the sheet and pick a style table. Pure stdlib — no packaging dependency."""
    import io as _io
    import zipfile as _zip
    name = _xlsx_escape(sheet_name)[:31] or "Sheet1"       # Excel caps sheet names at 31 chars
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>')
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>')
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def bom_xlsx(rows) -> bytes:
    """A clean Excel (.xlsx) workbook of the BOM: one 'BOM' sheet with a bold, frozen header
    row, autofilter dropdowns, auto-sized columns, and — the real win over the CSV export —
    NUMBERS stored as numbers (Qty, Unit/Ext Price, Stock) so Excel can sort and sum them
    instead of treating them as text. Mirrors the Full BOM CSV columns and adds the priced
    Source / Dist P/N / Unit Price / Ext Price / Stock / Lifecycle columns only when the build
    carries pricing. A Mouser string price ("$0.10") is coerced to a real number. Written with
    the standard library alone (zipfile + a little XML) so nothing extra has to be bundled into
    the packaged app. Pure, offline. Returns the .xlsx file as bytes."""
    priced = any(_coerce_price(r.get("unit_price")) is not None or r.get("extended") is not None
                 for r in rows)
    # (header, kind): 't' text, 'n' number, 'i' integer.
    cols = [("Refs", "t"), ("Qty", "i"), ("Value", "t"), ("MPN", "t"), ("Manufacturer", "t"),
            ("Footprint", "t"), ("Datasheet", "t"), ("Description", "t"), ("Basic", "t")]
    if priced:
        cols += [("Source", "t"), ("Dist P/N", "t"), ("Unit Price", "n"), ("Ext Price", "n"),
                 ("Stock", "i"), ("Lifecycle", "t")]

    def values(r):
        refs = r.get("refs", [])
        v = {"Refs": ",".join(refs) if isinstance(refs, list) else str(refs),
             "Qty": _bom_line_qty(r), "Value": r.get("value", ""), "MPN": r.get("mpn", ""),
             "Manufacturer": r.get("manufacturer", ""), "Footprint": r.get("footprint", ""),
             "Datasheet": r.get("datasheet", ""), "Description": r.get("description", ""),
             "Basic": "yes" if r.get("basic") else ""}
        if priced:
            ext = r.get("extended")
            if ext is None:
                ext = line_extended(_coerce_price(r.get("unit_price")), _bom_line_qty(r))
            v.update({"Source": r.get("source", ""), "Dist P/N": _dist_pn(r),
                      "Unit Price": _coerce_price(r.get("unit_price")), "Ext Price": ext,
                      "Stock": r.get("stock", ""), "Lifecycle": r.get("lifecycle", "")})
        return v

    def _num(raw):
        if isinstance(raw, bool) or raw in (None, ""):
            return None
        if isinstance(raw, (int, float)):
            return raw
        return _coerce_price(raw)                          # "$0.10", "5,000" -> float, else None

    def cell(ref, kind, raw, header=False):
        style = ' s="1"' if header else ""
        if kind in ("n", "i") and not header:
            n = _num(raw)
            if n is None:
                return f'<c r="{ref}"{style}/>'            # blank, not a text "0"
            if kind == "i":
                return f'<c r="{ref}"{style}><v>{int(round(n))}</v></c>'
            num = round(float(n), 6)                       # currency precision, never sci-notation
            text = _xlsx_number(num)
            return f'<c r="{ref}"{style}><v>{text}</v></c>'
        s = "" if raw is None else str(raw)
        if s == "":
            return f'<c r="{ref}"{style}/>'
        return (f'<c r="{ref}"{style} t="inlineStr"><is>'
                f'<t xml:space="preserve">{_xlsx_escape(s)}</t></is></c>')

    all_vals = [values(r) for r in rows]
    # Auto-size: widest of the header and any cell in the column, clamped to a sane range.
    widths = []
    for name, _k in cols:
        w = len(name)
        for v in all_vals:
            cv = v[name]
            w = max(w, len("" if cv is None else str(cv)))
        widths.append(min(max(w + 2, 8), 60))
    cols_xml = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                       for i, w in enumerate(widths))

    body = ["".join(cell(f"{_xlsx_col(i)}1", "t", name, header=True)
                    for i, (name, _k) in enumerate(cols))]
    row_xml = [f'<row r="1">{body[0]}</row>']
    for ri, v in enumerate(all_vals, start=2):
        cells = "".join(cell(f"{_xlsx_col(i)}{ri}", k, v[name]) for i, (name, k) in enumerate(cols))
        row_xml.append(f'<row r="{ri}">{cells}</row>')

    last = _xlsx_col(len(cols) - 1)
    nr = len(all_vals) + 1
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last}{nr}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols_xml}</cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<autoFilter ref="A1:{last}{nr}"/>'
        '</worksheet>')

    return _xlsx_package(sheet, _XLSX_STYLES_BOLD, sheet_name="BOM")


_REFDES_CATEGORY = {
    "R": "Resistor", "RN": "Resistor", "C": "Capacitor", "L": "Inductor",
    "FB": "Ferrite Bead", "Y": "Crystal", "X": "Crystal", "XTAL": "Crystal",
    "D": "Diode", "LED": "LED", "Q": "Transistor", "U": "IC", "IC": "IC",
    "J": "Connector", "P": "Connector", "CN": "Connector", "SW": "Switch", "S": "Switch",
    "K": "Relay", "F": "Fuse", "T": "Transformer", "BT": "Battery", "M": "Module",
}


def _refdes_category(ref: str) -> str:
    """A clean human category from a refdes prefix (R -> Resistor, C -> Capacitor, U -> IC)
    for the procurement sheet's Description when the part carries no real description. '' for
    an unknown prefix."""
    m = re.match(r"[A-Za-z]+", (ref or "").strip())
    return _REFDES_CATEGORY.get(m.group(0).upper(), "") if m else ""


def _vendor_domain(source: str) -> str:
    """The distributor's web domain from its name (Mouser -> mouser.com), matching how a
    purchasing sheet names the vendor. Unknown sources pass through unchanged."""
    s = (source or "").strip().lower()
    return {"mouser": "mouser.com", "digikey": "digikey.com", "digi-key": "digikey.com",
            "lcsc": "lcsc.com", "element14": "element14.com", "newark": "newark.com",
            "oshpark": "oshpark.com", "jlcpcb": "jlcpcb.com"}.get(s, source or "")


def _line_description(r) -> str:
    """The procurement Description for a BOM row: the part's real description when it has one
    (from the library / distributor), else a category from the refdes prefix, else the value."""
    desc = (r.get("description") or "").strip()
    if desc:
        return desc
    refs = _row_refs(r)
    cat = _refdes_category(refs[0]) if refs else ""
    return cat or (r.get("value") or "").strip()


def _procurement_note(*, priced: bool, spares_added: int, spares_pct: float) -> str:
    """The auto-generated Notes cell for a procurement line — it flags only the exceptions a
    buyer must act on, so a blank Notes column means "nothing to check". An unpriced line needs
    a manual quote ('no price — request quote'); a passive whose QTY was padded for pick-and-place
    attrition says by how much ('+N spare(s) (P% attrition)') so the inflated count is trusted, not
    questioned. Both can apply to one line (an unpriced padded passive); '' when priced and unpadded."""
    parts = []
    if not priced:
        parts.append("no price — request quote")
    if spares_added > 0:
        parts.append(f"+{spares_added} {'spare' if spares_added == 1 else 'spares'} "
                     f"({spares_pct:g}% attrition)")
    return "; ".join(parts)


def procurement_xlsx(rows, *, boards=1, spares_pct=0, pcb_multiple=3, tax_rate=0.0,
                     shipping=0.0, labour_per_board=0.0, assembly_surcharge_rate=0.0) -> bytes:
    """The buy-side procurement sheet as a clean Excel workbook, modeled on a real hand-made
    order sheet but AUTO-POPULATED from the Mouser/DigiKey data we already fetch — the columns
    a buyer fills by hand: Description, P/N (the orderable distributor part number), Electronic
    Component?, Vendor, QTY, Unit Cost, Cost @ QTY, Tax/Tariff, Shipping, Total Cost, Product
    Link, Notes — with a bold TOTAL row, a frozen header, autofilter, and currency-formatted
    cost cells. The Product Link is the distributor's product page (the manual sheet's
    'Quote ID'); the P/N and volume Unit Cost come straight from the priced lookup.

    Quantities model how the boards are actually built and ordered:
      * PCBs ship in packs of `pcb_multiple` (default 3), so the effective build rounds the
        board count UP to the next multiple — a 1-board build buys parts for 3.
      * QTY = per-board qty * effective boards, plus the `spares_pct` buffer on SMT passives
        only (R/C/L/FB, rounded up for pick-and-place attrition) — ICs/connectors order exact.
    Unit Cost is the volume-break price at that QTY (a bigger order buys down the ladder),
    falling back to the stored unit price. Cost @ QTY = QTY * Unit Cost; Tax/Tariff = Cost @ QTY
    * `tax_rate` (a single fraction, e.g. 0.07 for 7%); per-line Total Cost = Cost @ QTY +
    Tax/Tariff. `shipping` is one order-level charge shown and added in the TOTAL row (the grand
    Total = the cost + tax subtotals + shipping).

    Landed assembly cost (both default 0 = off, so the sheet is unchanged): `labour_per_board`
    is a flat assembly-labour charge per board built, billed for the actual board count `boards`
    (NOT the pack-rounded parts quantity, since you assemble the boards you build);
    `assembly_surcharge_rate` is a fraction of the PARTS subtotal (Cost @ QTY, e.g. 0.05 for 5%)
    covering handling/markup. When either is nonzero a single 'Assembly' line (labour + surcharge,
    its breakdown in Notes) is emitted above the TOTAL row and folded into the grand Total, so the
    sheet foots to parts + labour*boards + surcharge + tax + shipping. Assembly is not taxed
    (labour, not parts). Unpriced lines still list their quantities but leave the money cells blank. The Notes column auto-flags the exceptions a buyer must act on —
    an unpriced line ('no price — request quote') and a spares-padded passive ('+N spares (P%
    attrition)') — and is otherwise blank. Pure, offline, standard-library. Returns the .xlsx bytes."""
    import math
    n = _board_count(boards)
    try:
        mult = int(pcb_multiple)
    except (TypeError, ValueError):
        mult = 1
    mult = mult if mult >= 1 else 1
    eff_boards = math.ceil(n / mult) * mult                # boards rounded up to a full pack
    try:
        pct = max(0.0, float(spares_pct))
    except (TypeError, ValueError):
        pct = 0.0
    try:
        rate = max(0.0, float(tax_rate))
    except (TypeError, ValueError):
        rate = 0.0
    try:
        ship = max(0.0, float(shipping))
    except (TypeError, ValueError):
        ship = 0.0
    try:
        labour = max(0.0, float(labour_per_board))
    except (TypeError, ValueError):
        labour = 0.0
    try:
        surcharge_rate = max(0.0, float(assembly_surcharge_rate))
    except (TypeError, ValueError):
        surcharge_rate = 0.0

    # (header, kind): 't' text, 'i' integer, 'm' money (currency-styled number).
    cols = [("Description", "t"), ("P/N", "t"), ("Electronic Component?", "t"), ("Vendor", "t"),
            ("QTY", "i"), ("Unit Cost", "m"), ("Cost @ QTY", "m"), ("Tax/Tariff", "m"),
            ("Shipping", "m"), ("Total Cost", "m"), ("Product Link", "t"), ("Notes", "t")]

    def line(r):
        per_board = r.get("qty", r.get("total_qty", 0)) or 0
        try:
            per_board = int(per_board)
        except (TypeError, ValueError):
            per_board = 0
        qty = qty_raw = per_board * eff_boards
        spares_added = 0
        if pct and qty and _row_is_passive(r):             # spares pad passives only
            qty = math.ceil(qty * (1 + pct / 100.0))
            spares_added = qty - qty_raw
        ladder = r.get("price_breaks")
        unit = price_at_qty(ladder, qty) if ladder else None
        if unit is None:
            unit = _coerce_price(r.get("unit_price"))
        cost = round(unit * qty, 4) if (unit is not None and qty) else None
        tax = round(cost * rate, 4) if cost is not None else None
        total = round(cost + tax, 4) if cost is not None else None
        return {"Description": _line_description(r), "P/N": _dist_pn(r) or (r.get("mpn") or ""),
                "Electronic Component?": "Yes", "Vendor": _vendor_domain(r.get("source")),
                "QTY": qty, "Unit Cost": unit, "Cost @ QTY": cost, "Tax/Tariff": tax,
                "Shipping": None, "Total Cost": total,
                "Product Link": r.get("url") or "",
                "Notes": _procurement_note(priced=unit is not None,
                                           spares_added=spares_added, spares_pct=pct)}

    data = [line(r) for r in rows]
    cost_sum = round(sum(d["Cost @ QTY"] for d in data if d["Cost @ QTY"] is not None), 4)
    tax_sum = round(sum(d["Tax/Tariff"] for d in data if d["Tax/Tariff"] is not None), 4)

    # Landed assembly: labour billed per board built (actual `n`, not the pack-rounded parts
    # qty) + a surcharge on the parts subtotal. Emitted as one 'Assembly' line and folded into
    # the grand Total; not taxed (labour, not parts). Off (blank) when both inputs are 0.
    labour_total = round(labour * n, 4)
    surcharge = round(cost_sum * surcharge_rate, 4)
    assembly_total = round(labour_total + surcharge, 4)
    assembly_row = None
    if assembly_total > 0:
        bits = []
        if labour_total > 0:
            bits.append(f"labour ${labour:,.2f}/board x {n}")
        if surcharge > 0:
            bits.append(f"{surcharge_rate * 100:g}% surcharge on parts")
        assembly_row = {"Description": "Assembly", "Electronic Component?": "No",
                        "Cost @ QTY": assembly_total, "Total Cost": assembly_total,
                        "Notes": " + ".join(bits)}

    parts_and_assembly = round(cost_sum + assembly_total, 4)
    total_row = {"Description": "TOTAL", "Cost @ QTY": parts_and_assembly, "Tax/Tariff": tax_sum,
                 "Shipping": ship or None,
                 "Total Cost": round(parts_and_assembly + tax_sum + ship, 4)}

    def cell(ref, kind, raw, *, header=False, bold=False):
        if header:
            return (f'<c r="{ref}" s="1" t="inlineStr"><is>'
                    f'<t xml:space="preserve">{_xlsx_escape(raw)}</t></is></c>')
        if kind in ("i", "m"):
            num = raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
            if num is None:
                return f'<c r="{ref}"/>'                    # blank, never a text "0"
            if kind == "i":
                s = ' s="1"' if bold else ""
                return f'<c r="{ref}"{s}><v>{int(round(num))}</v></c>'
            s = ' s="3"' if bold else ' s="2"'              # currency (bold in the totals row)
            num = round(float(num), 6)
            text = _xlsx_number(num)
            return f'<c r="{ref}"{s}><v>{text}</v></c>'
        s = ' s="1"' if bold else ""
        txt = "" if raw is None else str(raw)
        if txt == "":
            return f'<c r="{ref}"{s}/>'
        return (f'<c r="{ref}"{s} t="inlineStr"><is>'
                f'<t xml:space="preserve">{_xlsx_escape(txt)}</t></is></c>')

    # Auto-size columns to the widest of the header, cells, and the totals row.
    widths = []
    for name, _k in cols:
        w = len(name)
        for d in data:
            cv = d[name]
            w = max(w, len("" if cv is None else (f"{cv:.2f}" if isinstance(cv, float) else str(cv))))
        widths.append(min(max(w + 2, 10), 60))
    cols_xml = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                       for i, w in enumerate(widths))

    row_xml = ['<row r="1">' + "".join(
        cell(f"{_xlsx_col(i)}1", "t", name, header=True) for i, (name, _k) in enumerate(cols))
        + '</row>']
    for ri, d in enumerate(data, start=2):
        row_xml.append(f'<row r="{ri}">' + "".join(
            cell(f"{_xlsx_col(i)}{ri}", k, d[name]) for i, (name, k) in enumerate(cols)) + '</row>')
    next_r = len(data) + 2
    # Assembly line (labour + surcharge) sits above the TOTAL, when billed. Money cells only;
    # QTY/Unit are blank (the full landed assembly cost lives in Cost @ QTY), the breakdown in Notes.
    if assembly_row is not None:
        ar = next_r
        row_xml.append(f'<row r="{ar}">' + "".join(
            cell(f"{_xlsx_col(i)}{ar}", "t" if name in ("Description", "Electronic Component?", "Notes")
                 else k, assembly_row.get(name)) for i, (name, k) in enumerate(cols)) + '</row>')
        next_r += 1
    # Bold TOTAL row: "TOTAL" label under Description, sums under the money columns.
    tr = next_r
    total_cells = []
    for i, (name, kind) in enumerate(cols):
        ref = f"{_xlsx_col(i)}{tr}"
        if name in total_row and total_row[name] is not None:
            total_cells.append(cell(ref, "t" if name == "Description" else "m",
                                    total_row[name], bold=True))
        else:
            total_cells.append(f'<c r="{ref}"/>')
    row_xml.append(f'<row r="{tr}">' + "".join(total_cells) + '</row>')

    last = _xlsx_col(len(cols) - 1)
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{last}{tr}"/>'
        '<sheetViews><sheetView tabSelected="1" workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '<selection pane="bottomLeft"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<cols>{cols_xml}</cols>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<autoFilter ref="A1:{last}{len(data) + 1}"/>'
        '</worksheet>')
    return _xlsx_package(sheet, _XLSX_STYLES_CURRENCY, sheet_name="Procurement")


def bom_rows_at_ref(sheet_rels, show) -> dict:
    """Reconstruct a project's BOM as it existed at a git revision, for diffing against
    the current build. `sheet_rels` is the repo-relative paths of the CURRENT build's
    schematic sheets; `show(rel) -> str | None` returns that sheet's content at the
    target revision (None when the sheet did not exist there). Each sheet that existed is
    parsed and the union grouped into BOM lines exactly like the live builder — identity
    only, NO network and NO pricing, because a diff compares parts and quantity, not cost.

    Returns {rows, sheets_found, sheets_missing}. Never raises: a `show` that errors or a
    sheet that won't parse is simply skipped. Note this reconstructs the ref's BOM from
    the ref's version of the *current* sheet set — sheets added or removed wholesale
    between the two revisions are reflected via sheets_missing, not chased through the
    old hierarchy (KiCad hierarchy is defined inside the root schematic)."""
    import os
    import tempfile
    comps: list = []
    found = missing = 0
    with tempfile.TemporaryDirectory() as td:
        for i, rel in enumerate(sheet_rels or []):
            try:
                text = show(rel)
            except Exception:  # noqa: BLE001 — a git failure is just an absent sheet
                text = None
            if not text:
                missing += 1
                continue
            found += 1
            fp = os.path.join(td, f"sheet_{i}.kicad_sch")
            try:
                with open(fp, "w", encoding="utf-8") as fh:
                    fh.write(text)
                comps.extend(_bom_components(fp))
            except Exception:  # noqa: BLE001 — an unparseable sheet drops out
                continue
    res = _bom_from_components(comps)
    return {"rows": res["rows"], "sheets_found": found, "sheets_missing": missing}


# Header aliases so an exported BOM (project OR consolidated) — and reasonable
# hand-made CSVs — parse back into diff-ready rows. Matched case-insensitively.
_CSV_MPN_COLS = ("mpn", "manufacturer part number", "mfr part number",
                 "manufacturer part no", "part number")
_CSV_VALUE_COLS = ("value",)
_CSV_FOOTPRINT_COLS = ("footprint",)
_CSV_QTY_COLS = ("qty", "quantity", "total", "total qty")


def bom_rows_from_csv(text: str) -> list:
    """Parse an exported BOM CSV back into diff-ready rows [{mpn, value, footprint, qty}]
    for bom_diff. Columns are matched by name (case-insensitive) so both the project
    export (Refs,Qty,Value,MPN,...) and the consolidated export (MPN,...,Total,...) load.
    Rows with neither an MPN nor a value are skipped. Never raises — a malformed or
    empty file yields []."""
    if not text:
        return []
    import csv as _csv
    import io as _io
    rows = []
    try:
        reader = _csv.DictReader(_io.StringIO(text))
        if not reader.fieldnames:
            return []
        hdr = {(h or "").strip().lower(): h for h in reader.fieldnames}

        def pick(cands):
            return next((hdr[c] for c in cands if c in hdr), None)
        f_mpn, f_val = pick(_CSV_MPN_COLS), pick(_CSV_VALUE_COLS)
        f_fp, f_qty = pick(_CSV_FOOTPRINT_COLS), pick(_CSV_QTY_COLS)
        for d in reader:
            mpn = (d.get(f_mpn) or "").strip() if f_mpn else ""
            val = (d.get(f_val) or "").strip() if f_val else ""
            if not mpn and not val:
                continue
            qraw = (d.get(f_qty) or "").strip() if f_qty else ""
            try:
                qty = int(float(qraw)) if qraw else 0
            except (TypeError, ValueError):
                qty = 0
            rows.append({"mpn": mpn, "value": val,
                         "footprint": (d.get(f_fp) or "").strip() if f_fp else "",
                         "qty": qty})
    except Exception:                                # noqa: BLE001 — a bad file is just empty
        return []
    return rows


def _row_refs(r) -> list:
    """The reference designators for a BOM row, from a project row (`refs`) or a
    consolidated row (`refs_by_board`), de-duplicated and naturally sorted."""
    if r.get("refs"):
        return sorted(set(r["refs"]), key=_natural_ref)
    out: list = []
    for refs in (r.get("refs_by_board") or {}).values():
        out.extend(refs)
    return sorted(set(out), key=_natural_ref)


def _row_is_passive(r) -> bool:
    """Whether a BOM row is a small SMT passive (resistor / capacitor / inductor /
    ferrite bead) — the parts that suffer pick-and-place attrition on an assembly line.
    Keyed off the refdes prefix against the SAME _BASIC_PREFIXES set is_basic_part uses,
    but MPN-independent (a cart line always has an MPN, yet a specific-MPN 0402 cap is
    still a passive). A row groups one part, so its refdes share a prefix; the first
    ref decides."""
    refs = _row_refs(r)
    if not refs:
        return False
    m = re.match(r"[A-Za-z]+", refs[0])
    return (m.group(0).upper() if m else "") in _BASIC_PREFIXES


def procurement_cart_csv(rows, boards=1, spares_pct=0) -> dict:
    """Build a Mouser cart-upload CSV from priced/enriched BOM rows. One line per part
    that has an MPN (a purchasable part number); bare passives grouped by value alone
    are skipped, since a cart orders by part number. Columns match Mouser's BOM upload
    — the Mouser P/N is filled when a lookup provided it, else left blank so Mouser
    resolves it from the MPN; Customer Reference carries the refdes. Per-board qty comes
    from 'qty' (project BOM) or 'total_qty' (consolidated), scaled by `boards` so a run
    of N boards orders N× each line (a board count below 1 is treated as 1). The refdes
    reference stays per-board — it names the placements on one board.

    `spares_pct` (0 by default) pads the SMT passives (R/C/L/FB) by that percentage,
    ROUNDED UP, to cover pick-and-place attrition — ICs/connectors stay at the exact run
    quantity. A negative/garbage percentage is treated as 0 (compat: no padding). Returns
    {csv, boards, spares_pct, line_count, skipped_no_mpn, padded_lines, total_qty}."""
    import csv as _csv
    import io as _io
    import math
    try:
        n = int(boards)
    except (TypeError, ValueError):
        n = 1
    if n < 1:
        n = 1
    try:
        pct = float(spares_pct)
    except (TypeError, ValueError):
        pct = 0.0
    if pct < 0:
        pct = 0.0
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["Mouser Part Number", "Manufacturer Part Number", "Quantity",
                "Customer Reference"])
    line_count = skipped = total = padded = 0
    for r in rows:
        mpn = (r.get("mpn") or "").strip()
        per_board = r.get("qty", r.get("total_qty", 0)) or 0
        if not mpn:
            skipped += 1
            continue
        try:
            order_qty = int(per_board) * n
        except (TypeError, ValueError):
            order_qty = 0
        if pct and order_qty and _row_is_passive(r):
            buffered = math.ceil(order_qty * (1 + pct / 100.0))
            if buffered > order_qty:
                order_qty = buffered
                padded += 1
        w.writerow([r.get("mouser_pn") or "", mpn, order_qty, " ".join(_row_refs(r))])
        line_count += 1
        total += order_qty
    return {"csv": buf.getvalue(), "boards": n, "spares_pct": (int(pct) if pct == int(pct) else pct),
            "line_count": line_count, "skipped_no_mpn": skipped, "padded_lines": padded,
            "total_qty": total}


def jlcpcb_bom_csv(rows) -> dict:
    """Build a JLCPCB assembly BOM CSV from enriched/priced BOM rows. Columns match
    JLCPCB's assembly upload — Comment, Designator, Footprint, LCSC Part #. Unlike a
    distributor cart, assembly places parts by DESIGNATOR (including bare passives by
    value), so every line with a comment (value, else MPN) and at least one refdes is
    exported; the LCSC Part # is filled when a lookup provided one (`lcsc_pn`) else left
    blank for the user to complete. Qty comes from 'qty' (project BOM) or 'total_qty'
    (consolidated). Returns {csv, line_count, with_lcsc, without_lcsc, total_qty}."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
    line_count = with_lcsc = total = 0
    for r in rows:
        refs = _row_refs(r)
        comment = (r.get("value") or "").strip() or (r.get("mpn") or "").strip()
        if not refs or not comment:                  # nothing to place / nothing to call it
            continue
        lcsc = (r.get("lcsc_pn") or "").strip()
        w.writerow([comment, ",".join(refs), r.get("footprint", ""), lcsc])
        line_count += 1
        if lcsc:
            with_lcsc += 1
        qty = r.get("qty", r.get("total_qty", 0)) or 0
        try:
            total += int(qty)
        except (TypeError, ValueError):
            pass
    return {"csv": buf.getvalue(), "line_count": line_count, "with_lcsc": with_lcsc,
            "without_lcsc": line_count - with_lcsc, "total_qty": total}


def library_lookup(cfg):
    """A lookup(key) -> {manufacturer, datasheet, description, mouser_pn} backed by
    the local Library (PROJ-09) — offline part-number enrichment. Keyed by MPN and,
    as a fallback, the part name. None on a miss."""
    try:
        rows = scan_library_grouped(cfg)
    except Exception:  # noqa: BLE001
        rows = []
    idx: dict = {}
    for r in rows:
        for key in (r.get("mpn"), r.get("name")):
            if key:
                idx.setdefault(str(key).strip().lower(), r)

    def lookup(k):
        r = idx.get(str(k or "").strip().lower())
        if not r:
            return None
        return {"manufacturer": r.get("manufacturer"), "datasheet": r.get("datasheet"),
                "description": r.get("description"), "mouser_pn": r.get("mouser_pn")}
    return lookup


def chained_lookup(*lookups):
    """Combine lookups: first non-None result wins (e.g. Library then Mouser)."""
    fns = [f for f in lookups if f]

    def lookup(k):
        for f in fns:
            try:
                r = f(k)
            except Exception:  # noqa: BLE001
                r = None
            if r:
                return r
        return None
    return lookup


def bom_from_project(sch_paths, lookup=None,
                     enrich_fields=("manufacturer", "datasheet", "description"), price_lookup=None) -> dict:
    """PROJ-08: a single BOM merged across EVERY sheet of a project (not just the
    root), grouping identical parts together with summed quantity. When `price_lookup`
    is given, each line with an MPN is priced and the result carries a `cost` roll-up."""
    comps = []
    for p in (sch_paths or []):
        try:
            comps.extend(_bom_components(p))
        except Exception:  # noqa: BLE001
            continue
    return _bom_from_components(comps, lookup, enrich_fields, price_lookup=price_lookup)


def bom_from_kicad_schematic(sch_path, lookup=None,
                             enrich_fields=("manufacturer", "datasheet", "description"), price_lookup=None) -> dict:
    """Smart BOM from a KiCad 6+/7+ schematic (.kicad_sch), using our identity + enrich
    features on any KiCad file — not just the cards this tool designs.

    Pulls every real component (skips power / virtual / excluded-from-BOM symbols),
    reads its properties, resolves the canonical MPN / manufacturer via part_identity
    (the same logic that groups the library), then groups identical parts — by MPN when
    present, else value + footprint — with their reference designators and quantity.
    If a `lookup(mpn) -> {...}` is given (e.g. make_mouser_lookup), it fills BLANK
    manufacturer / datasheet per group. When `price_lookup` is given, each line with an
    MPN is priced (unit_price / extended / stock / lifecycle) and the result carries a
    `cost` roll-up. Read-only; returns {rows, component_count, line_count, csv}. Each
    row carries a `basic` flag (PROJ-09)."""
    from fp_render import parse_sexpr
    root = parse_sexpr(Path(sch_path).read_text(encoding="utf-8", errors="replace"))
    if not root or root[0] != "kicad_sch":
        return {"error": "not a KiCad schematic (.kicad_sch)", "rows": [],
                "component_count": 0, "line_count": 0, "csv": ""}
    return _bom_from_components(_bom_components(sch_path), lookup, enrich_fields,
                               price_lookup=price_lookup)


def library_health_report(cfg: Dict[str, str], overrides: Optional[dict] = None) -> dict:
    """Roll up scan_library_grouped into a shareable health summary: totals plus the
    lists that need attention — dangling links, symbols missing a footprint or model,
    and parts with no manufacturer identity. Returns counts, the offending lists, and
    a ready-to-share markdown report. Read-only."""
    rows = scan_library_grouped(cfg, overrides)
    total = len(rows)
    dangling = [r for r in rows if r.get("dangling")]
    miss_fp = [r for r in rows if r.get("has_symbol") and not r.get("has_footprint")]
    miss_mdl = [r for r in rows if r.get("has_footprint") and not r.get("has_model")]
    no_mfr = [r for r in rows if not r.get("manufacturer")]
    complete = [r for r in rows if r.get("has_symbol") and r.get("has_footprint")
                and r.get("has_model") and not r.get("dangling")]
    counts = {"parts": total, "complete": len(complete), "dangling": len(dangling),
              "missing_footprint": len(miss_fp), "missing_model": len(miss_mdl),
              "no_manufacturer": len(no_mfr)}

    def _names(rs, limit=40):
        out = [r.get("mpn") or r.get("name") or r.get("footprint") or "?" for r in rs[:limit]]
        if len(rs) > limit:
            out.append(f"… and {len(rs) - limit} more")
        return out

    pct = (100 * len(complete) // total) if total else 0
    L = ["# Library Health", "",
         f"**{len(complete)} / {total} parts complete** ({pct}%) — symbol + footprint + 3D model, no dangling links.",
         "", "## Counts", ""]
    L += [f"- {k.replace('_', ' ').title()}: {v}" for k, v in counts.items()]
    for title, rs in (("Dangling (symbol/footprint points at a missing file)", dangling),
                      ("Missing footprint on disk", miss_fp),
                      ("Missing 3D model on disk", miss_mdl),
                      ("No manufacturer identity", no_mfr)):
        if rs:
            L += ["", f"## {title} ({len(rs)})", ""] + [f"- {n}" for n in _names(rs)]
    return {"counts": counts, "dangling": _names(dangling, 10_000),
            "missing_footprint": _names(miss_fp, 10_000),
            "missing_model": _names(miss_mdl, 10_000),
            "no_manufacturer": _names(no_mfr, 10_000),
            "markdown": "\n".join(L) + "\n"}


def suggest_footprint_for_symbol(sym_name: str, current_fp_basename: str,
                                 props: Dict[str, str], fp_stems) -> tuple:
    """Best footprint stem for a symbol that has none (or a dangling one), by
    name → identity → fuzzy match. Returns (stem, reason) or (None, None)."""
    import difflib
    stems = list(fp_stems)
    low = {s.lower(): s for s in stems}
    for cand in (current_fp_basename, sym_name):     # exact name match
        if cand and cand.lower() in low:
            return low[cand.lower()], "name"
    mpn = (strict_mpn(props) or props.get("Value", "") or "").strip().lower()
    for key in (mpn, sym_name.lower()):              # identity substring, unique
        if key:
            hits = [s for s in stems if key in s.lower() or s.lower() in key]
            if len(hits) == 1:
                return hits[0], "identity"
    # token-substring: a footprint token (>=4 chars, e.g. ADG714, LQFP100) that also
    # appears in the symbol's id — catches ADG714BRUZ-REEL -> RU_24_ADG714.
    ident = f"{sym_name} {mpn}".upper()
    tok_hits = [s for s in stems
                if any(t in ident for t in re.findall(r"[A-Z0-9]{4,}", s.upper()))]
    if len(set(tok_hits)) == 1:
        return tok_hits[0], "token"
    close = difflib.get_close_matches(sym_name.lower(), [s.lower() for s in stems],
                                      n=1, cutoff=0.72)   # fuzzy on the reliable symbol name
    if close:
        return low[close[0]], "fuzzy"
    return None, None


def auto_assign_library(cfg: Dict[str, str], dry_run: bool = True, log: UILog = None) -> dict:
    """Auto-associate footprints AND 3D models across the shared library, no KiCad.

    For every symbol with no resolvable footprint (missing or dangling), pick the
    best-matching footprint by identity then name; for every footprint with no
    resolvable 3D model, pick the best-matching .step/.wrl by name. dry_run=True
    (default) returns the proposed assignments without writing; dry_run=False writes
    them — symbol Footprint -> MyFootprints:<stem>, footprint (model) ->
    ${MY3DMODELS}/<file> — under _LIB_LOCK with a .trash snapshot first. Returns
    {footprints:[{symbol, assign, reason}], models:[{footprint, assign, reason}],
    written}."""
    sym_path = Path(cfg.get("SymbolLib", ""))
    fp_dir = Path(cfg.get("FootprintLib", ""))
    mdl_dir = Path(cfg.get("ModelLib", ""))
    fp_texts = {p.stem: (p, read_text(p)) for p in fp_dir.glob("*.kicad_mod")} if fp_dir.exists() else {}
    fp_stems = set(fp_texts)
    model_paths = [p for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")] if mdl_dir.exists() else []
    model_names = {p.name for p in model_paths}

    fp_assigns, mdl_assigns = [], []
    with _LIB_LOCK:
        # symbols -> footprint
        sym_text = read_text(sym_path) if sym_path.exists() else ""
        sym_blocks = extract_symbol_blocks(sym_text)
        new_sym_blocks = list(sym_blocks)            # positional rewrite (no text.replace footgun)
        any_sym_edit = False
        for idx, b in enumerate(sym_blocks):
            name = extract_symbol_name(b)
            cur = symbol_footprint_ref(b) or ""      # "Nickname:Stem" or ""
            cur_stem = cur.split(":")[-1] if cur else ""
            if cur_stem and cur_stem in fp_stems:
                continue                             # already resolves
            stem, reason = suggest_footprint_for_symbol(
                name, cur_stem, extract_symbol_properties(b), fp_stems)
            if stem:
                fp_assigns.append({"symbol": name, "assign": stem, "reason": reason})
                new_sym_blocks[idx] = set_symbol_property(b, "Footprint", f"{FP_NICKNAME}:{stem}")
                any_sym_edit = True

        # footprints -> 3D model
        fp_writes = []
        for stem, (path, text) in fp_texts.items():
            ref = footprint_model_ref(text)
            if ref and Path(ref).name in model_names:
                continue                             # already has a resolvable model
            guess = match_model_for_footprint(stem, [Path(m.name) for m in model_paths])
            if guess:
                mdl_assigns.append({"footprint": stem, "assign": guess.name, "reason": "name"})
                fp_writes.append((path, ensure_footprint_model(text, guess.name)))

        written = False
        if not dry_run and (any_sym_edit or fp_writes):
            if any_sym_edit:
                # Rebuild positionally from the substituted blocks — immune to a block whose
                # text is a byte-for-byte substring of another region (the text.replace footgun,
                # already fixed the same way in enrich_library / set_library_symbol_property).
                new = insert_blocks_into_target(
                    '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                    new_sym_blocks)
                _snapshot_then_write(sym_path, new, log or _NullLog())
            for path, new_text in fp_writes:
                try:
                    bak = path.with_suffix(path.suffix + ".autobak")
                    shutil.copy2(path, bak)
                    write_text(path, new_text)
                except Exception as e:               # noqa: BLE001
                    (log or _NullLog()).write(f"model assign failed for {path.name}: {e}")
            written = True

    return {"footprints": fp_assigns, "models": mdl_assigns, "written": written,
            "footprint_count": len(fp_assigns), "model_count": len(mdl_assigns)}


# ── "Complete This Part" — one part, planned then applied ────────────────────
# The per-part orchestration behind the workbench's primary action. complete_part_plan
# is PURE (reads disk to find name-match candidates, writes nothing) and returns
# data-only op descriptors so the UI can render a checkbox preview (safe ops
# pre-checked, risky overwrites unchecked) before anything is written — the direct
# answer to "I had no idea what Fix-All changed". apply_complete_part executes only
# the ops the user kept, in dependency order, and does NOT commit (the caller commits
# the batch, then re-scans and reports the remaining part_missing).
def _symbol_props_by_name(cfg: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    sym_path = _cfg_path(cfg, "SymbolLib")
    out: Dict[str, Dict[str, str]] = {}
    if sym_path.is_file():
        try:
            for b in extract_symbol_blocks(read_text(sym_path)):
                out[extract_symbol_name(b)] = extract_symbol_properties(b)
        except Exception:                      # noqa: BLE001
            pass
    return out


def complete_part_plan(cfg: Dict[str, str], row: Dict,
                       fetched: Optional[Dict] = None) -> List[Dict[str, object]]:
    """Everything 'Complete This Part' COULD do for `row`, as data-only op descriptors:
        {key, label, detail, kind, value, safe, ...}
      kind: 'create_symbol' | 'link_footprint' | 'link_model' | 'fill_field'
      safe: True  -> pre-checked (name-match links, stub create, blank fills)
            False -> unchecked (overwriting an existing value)
    `fetched` is an optional distributor result (normalized dict) used to propose
    identity fills; omit it and no fill ops are produced. Pure — no writes."""
    ops: List[Dict[str, object]] = []
    symbols = list(row.get("symbols") or [])
    fp = row.get("footprint")
    has_symbol = bool(row.get("has_symbol"))
    has_footprint = bool(row.get("has_footprint"))
    has_model = bool(row.get("has_model"))

    fp_dir = _cfg_path(cfg, "FootprintLib")
    fp_stems = {p.stem for p in fp_dir.glob("*.kicad_mod")} if fp_dir.is_dir() else set()
    mdl_dir = _cfg_path(cfg, "ModelLib")
    model_paths = [p for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")] if mdl_dir.is_dir() else []

    # 1. Footprint-only orphan -> create a stub symbol (the reuse-an-existing-symbol
    #    variant is a deliberate manual choice, not part of the automatic plan).
    if not has_symbol and fp:
        ops.append({"key": "create_symbol", "kind": "create_symbol", "value": fp,
                    "safe": True, "label": f"Create a stub symbol for footprint '{fp}'",
                    "detail": "Makes the orphan footprint placeable in a schematic."})

    # 2. Symbol with no resolvable footprint -> best name/identity match on disk.
    if has_symbol and not has_footprint:
        name = symbols[0]
        props = _symbol_props_by_name(cfg).get(name, {})
        cur_stem = (fp or "")
        stem, reason = suggest_footprint_for_symbol(name, cur_stem, props, fp_stems)
        if stem:
            ops.append({"key": "link_footprint", "kind": "link_footprint", "value": stem,
                        "reason": reason, "safe": True,
                        "label": f"Link footprint '{stem}' ({reason} match)",
                        "detail": f"Points {name} at MyFootprints:{stem}."})

    # 3. Footprint whose FILE doesn't physically resolve a 3D model -> best name match.
    #    Keyed on the footprint FILE's own (model …) line, NOT scan's has_model: scan
    #    reports has_model from an app-internal name-match, but if the .kicad_mod file
    #    carries no (model) line then KiCad — and ANYONE you hand the files to — sees no
    #    3D model. Persisting the line is what makes the link portable.
    target_fp = fp if has_footprint else next((o["value"] for o in ops
                                               if o["kind"] == "link_footprint"), None)
    if target_fp:
        model_names = {m.name for m in model_paths}
        fp_path = fp_dir / f"{target_fp}.kicad_mod"
        file_model = footprint_model_ref(read_text(fp_path)) if fp_path.is_file() else ""
        if not (file_model and file_model in model_names):     # file link missing/dangling
            guess = match_model_for_footprint(target_fp, [Path(m.name) for m in model_paths])
            if guess:
                ops.append({"key": "link_model", "kind": "link_model", "value": guess.name,
                            "safe": True,
                            "label": f"Attach 3D model '{guess.name}' (name match)",
                            "detail": f"Persists a (model …) line in {target_fp}.kicad_mod "
                                      "so the model travels with the footprint."})

    # 4. Identity fills from a distributor result. Blanks are safe; a value that would
    #    OVERWRITE an existing different one is risky (unchecked by default).
    if fetched and has_symbol:
        blanks = autofill_plan(row, fetched, "blanks")
        overwrites = autofill_plan(row, fetched, "overwrite")
        label_of = {rk: lbl for rk, _prop, lbl in AUTOFILL_FIELDS}
        prop_of = {rk: prop for rk, prop, _lbl in AUTOFILL_FIELDS}
        for row_key, val in overwrites.items():
            safe = row_key in blanks                # a blank fill; else an overwrite
            cur = (row.get(row_key) or "").strip()
            human = label_of.get(row_key, row_key)
            if safe:
                label = f"Fill {human}: '{val}'"
            else:
                label = f"Overwrite {human}: '{cur}' → '{val}'"
            ops.append({"key": f"fill:{row_key}", "kind": "fill_field", "row_key": row_key,
                        "prop": prop_of[row_key], "value": val, "safe": safe, "label": label,
                        "detail": ""})
    return ops


def apply_complete_part(cfg: Dict[str, str], row: Dict, ops: List[Dict],
                        selected_keys, log: UILog = None) -> Dict[str, object]:
    """Execute the ops in `ops` whose `key` is in `selected_keys`, in dependency order
    (create-symbol → link-footprint → link-model → fills). Returns
    {applied:[labels], errors:[str]}. Does NOT commit — the caller commits the batch,
    then re-scans and reports the remaining part_missing."""
    log = log or _NullLog()
    selected = set(selected_keys or ())
    chosen = [o for o in (ops or []) if o.get("key") in selected]
    order = {"create_symbol": 0, "link_footprint": 1, "link_model": 2, "fill_field": 3}
    chosen.sort(key=lambda o: order.get(o.get("kind"), 9))

    names = list(row.get("symbols") or [])
    cur_fp = row.get("footprint")
    applied: List[str] = []
    errors: List[str] = []

    for op in chosen:
        kind = op.get("kind")
        try:
            if kind == "create_symbol":
                new = create_symbol_for_footprint(cfg, op["value"], log)
                if new:
                    names = [new]                  # later fills/links target the new symbol
                    cur_fp = op["value"]
                    applied.append(op["label"])
                else:
                    errors.append(f"create symbol for '{op['value']}' failed")
            elif kind == "link_footprint":
                if set_library_symbol_footprint(cfg, names, op["value"], log):
                    cur_fp = op["value"]
                    applied.append(op["label"])
                else:
                    errors.append(f"link footprint '{op['value']}' wrote nothing")
            elif kind == "link_model":
                fp_path = _cfg_path(cfg, "FootprintLib") / f"{cur_fp}.kicad_mod"
                if not fp_path.is_file():
                    errors.append(f"link model: footprint file missing ({cur_fp})")
                    continue
                text = read_text(fp_path)
                new_text = ensure_footprint_model(text, op["value"])
                if new_text != text:
                    write_text(fp_path, new_text)
                    applied.append(op["label"])
                else:
                    applied.append(op["label"] + " (already attached)")
            elif kind == "fill_field":
                if set_library_symbol_property(cfg, names, op["prop"], op["value"], log):
                    applied.append(op["label"])
                else:
                    errors.append(f"fill {op.get('row_key')} wrote nothing")
        except Exception as e:                     # noqa: BLE001 — never swallow; report it
            errors.append(f"{op.get('label', op.get('key'))}: {e}")
    return {"applied": applied, "errors": errors}


def repair_library(cfg: Dict[str, str], log: UILog) -> Dict[str, int]:
    """Fix the whole shared library so placed parts resolve in KiCad:
    rewrite every symbol's Footprint to MyFootprints:<name>, add/repair each
    footprint's ${MY3DMODELS}/<file> model line (best-name model match), and
    register the libraries + env var. Returns a counts dict."""
    result = {"symbols_fixed": 0, "footprints_fixed": 0, "footprints_no_model": 0}

    with _LIB_LOCK:                          # never interleave with a watcher import
        # 1) symbol -> footprint nickname
        sym_path = Path(cfg["SymbolLib"])
        if sym_path.exists():
            text = read_text(sym_path)
            blocks = extract_symbol_blocks(text)
            new_blocks = []
            for b in blocks:
                nb = rewrite_symbol_footprint(b, FP_NICKNAME)
                if nb != b:
                    result["symbols_fixed"] += 1
                new_blocks.append(nb)
            if result["symbols_fixed"]:
                new_text = insert_blocks_into_target(
                    '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                    new_blocks)
                write_text(sym_path, new_text)

        # 2) footprint -> 3D model line
        fp_dir = Path(cfg["FootprintLib"])
        mdl_dir = Path(cfg["ModelLib"])
        model_files = [p for p in mdl_dir.glob("*")
                       if p.suffix.lower() in (".step", ".stp", ".wrl")] if mdl_dir.exists() else []
        unmatched: List[str] = []
        if fp_dir.exists():
            for fp in sorted(fp_dir.glob("*.kicad_mod")):
                m = match_model_for_footprint(fp.stem, model_files)
                t = read_text(fp)
                if m is None:
                    if not footprint_has_model(t):
                        unmatched.append(fp.stem)
                    continue
                nt = ensure_footprint_model(t, m.name)
                if nt != t:
                    write_text(fp, nt)
                    result["footprints_fixed"] += 1
        result["footprints_no_model"] = len(unmatched)

        # 3) register in KiCad
        register_libraries(cfg, log)

    log.write(f"Repair: {result['symbols_fixed']} symbol footprint link(s) fixed, "
              f"{result['footprints_fixed']} footprint model line(s) fixed.")
    if unmatched:
        preview = ", ".join(unmatched[:10]) + ("…" if len(unmatched) > 10 else "")
        log.write(f"Repair: {len(unmatched)} footprint(s) had no matching 3D model: {preview}")
    return result


# ── Portability: will the same files map identically on someone else's machine? ──
# A reference is portable only if it resolves the SAME on a second machine running
# this app against the same files: symbol→footprint via the `MyFootprints:` nickname
# to a real file, footprint→model via `${MY3DMODELS}/` to a real file. An absolute
# path, a foreign library nickname, a bare (nickname-less) footprint, or a dangling
# ref all resolve on the author's machine but break on the recipient's. This audit is
# READ-ONLY (the fix is repair_library); it's what you run before sharing.
_ABS_PATH_RE = re.compile(r"^(/|[A-Za-z]:[\\/]|\\\\)")   # POSIX, Windows drive, UNC


def footprint_model_paths(footprint_text: str) -> List[str]:
    """Every raw (model …) path in a footprint, verbatim (quotes stripped), in order.
    Portability cares about the WHOLE path (`${MY3DMODELS}/x.step` vs `/abs/x.step`),
    not just the basename that footprint_model_ref returns."""
    out: List[str] = []
    for m in _MODEL_PATH_RE.finditer(footprint_text):
        out.append(m.group(2).strip().strip('"'))
    return out


def verify_handoff_readiness(cfg: Dict[str, str]) -> Dict[str, object]:
    """Audit whether the library will map IDENTICALLY on another machine running this
    app against the same files. Read-only. Returns:

        {ok: bool,                       # True iff no breaking issues
         issues: [{ref, kind, detail, how_to_fix}],
         counts: {symbols, footprints, issues}}

    Issue kinds:
      foreign_footprint_nickname  symbol → `<Vendor>:<stem>` (not MyFootprints)
      unqualified_footprint       symbol → bare `<stem>` (no library nickname)
      footprint_path_ref          symbol → a filesystem path in the Footprint field
      missing_footprint           symbol → MyFootprints:<stem> with no .kicad_mod file
      absolute_model_path         footprint (model …) uses an absolute/drive/UNC path
      foreign_model_path          footprint (model …) not under ${MY3DMODELS}
      missing_model               footprint → ${MY3DMODELS}/<file> with no file on disk
    """
    issues: List[Dict[str, str]] = []
    fp_dir = _cfg_path(cfg, "FootprintLib")
    fp_stems = {p.stem for p in fp_dir.glob("*.kicad_mod")} if fp_dir.is_dir() else set()
    mdl_dir = _cfg_path(cfg, "ModelLib")
    model_names = {p.name for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")} if mdl_dir.is_dir() else set()

    # --- symbols → footprint ------------------------------------------------
    sym_path = _cfg_path(cfg, "SymbolLib")
    sym_blocks = extract_symbol_blocks(read_text(sym_path)) if sym_path.is_file() else []
    for b in sym_blocks:
        name = extract_symbol_name(b)
        m = _FP_PROP_RE.search(b)
        raw = (m.group(2).strip() if m else "")
        if not raw:
            continue                             # no footprint: incomplete, not a portability break
        if _ABS_PATH_RE.match(raw) or "/" in raw or "\\" in raw:
            issues.append({"ref": name, "kind": "footprint_path_ref",
                           "detail": f"Footprint is a filesystem path '{raw}', which won't "
                                     "resolve on another machine.",
                           "how_to_fix": "Run Make Portable / Fix to re-link it as "
                                         "MyFootprints:<stem>."})
            continue
        if ":" in raw:
            nick, stem = raw.split(":", 1)
            if nick != FP_NICKNAME:
                issues.append({"ref": name, "kind": "foreign_footprint_nickname",
                               "detail": f"Footprint uses the '{nick}' library, which the "
                                         "recipient won't have registered; only MyFootprints "
                                         "travels with the files.",
                               "how_to_fix": "Run Make Portable / Fix to requalify it to "
                                             f"MyFootprints:{stem}."})
            elif stem not in fp_stems:
                issues.append({"ref": name, "kind": "missing_footprint",
                               "detail": f"Footprint MyFootprints:{stem} has no .kicad_mod "
                                         "file. The link dangles on every machine.",
                               "how_to_fix": f"Add the '{stem}.kicad_mod' file, or re-link the "
                                             "symbol to an existing footprint."})
        else:
            issues.append({"ref": name, "kind": "unqualified_footprint",
                           "detail": f"Footprint '{raw}' has no library nickname, so KiCad "
                                     "can't resolve it on any machine.",
                           "how_to_fix": "Run Make Portable / Fix to qualify it as "
                                         f"MyFootprints:{raw}."})

    # --- footprints → 3D model ---------------------------------------------
    fp_files = sorted(fp_dir.glob("*.kicad_mod")) if fp_dir.is_dir() else []
    for p in fp_files:
        try:
            paths = footprint_model_paths(read_text(p))
        except Exception:                        # noqa: BLE001
            continue
        for raw in paths:
            norm = raw.replace("\\", "/")
            if _ABS_PATH_RE.match(raw):
                issues.append({"ref": p.stem, "kind": "absolute_model_path",
                               "detail": f"3D model path '{raw}' is absolute, so it points at "
                                         "the author's disk and breaks for everyone else.",
                               "how_to_fix": "Run Make Portable / Fix to rewrite it as "
                                             "${MY3DMODELS}/<file>."})
            elif norm.startswith(MODEL_VAR_REF):
                fname = norm.split("/")[-1]
                if fname not in model_names:
                    issues.append({"ref": p.stem, "kind": "missing_model",
                                   "detail": f"3D model '{fname}' is referenced but not present "
                                             "in the model library. It dangles on every machine.",
                                   "how_to_fix": f"Add the '{fname}' file, or re-link to an "
                                                 "existing model."})
            else:
                issues.append({"ref": p.stem, "kind": "foreign_model_path",
                               "detail": f"3D model path '{raw}' isn't under ${{{MODEL_VAR}}}, "
                                         "so it won't resolve on another machine.",
                               "how_to_fix": "Run Make Portable / Fix to rewrite it as "
                                             "${MY3DMODELS}/<file>."})

    return {"ok": not issues, "issues": issues,
            "counts": {"symbols": len(sym_blocks), "footprints": len(fp_files),
                       "issues": len(issues)}}


def _portablize_footprint_models(text: str) -> str:
    """Rewrite EVERY (model …) path in a footprint to ${MY3DMODELS}/<basename>,
    preserving each basename. Idempotent; unlike set_footprint_model it fixes all
    lines, not just the first."""
    def repl(m: "re.Match") -> str:
        raw = m.group(2).strip().strip('"')
        base = raw.replace("\\", "/").split("/")[-1]
        return f'{m.group(1)}"{MODEL_VAR_REF}/{base}"'
    return _MODEL_PATH_RE.sub(repl, text)


def make_library_portable(cfg: Dict[str, str], log: UILog = None) -> Dict[str, object]:
    """Rewrite every library cross-reference into its portable form so the same files
    map IDENTICALLY on another machine running this app: symbol Footprint →
    MyFootprints:<stem>, footprint (model …) → ${MY3DMODELS}/<basename>. Registers the
    libraries in KiCad afterward. Snapshot-then-write under _LIB_LOCK.

    The precise fixer behind verify_handoff_readiness (repair_library links by
    name-match; this REWRITES existing refs to portable form, e.g. an absolute model
    path whose basename doesn't match the footprint stem). Returns
    {symbols_fixed, models_fixed, unresolved:[str]} — unresolved lists refs whose
    target file is genuinely absent (can't be auto-fixed, still flagged by verify)."""
    log = log or _NullLog()
    result: Dict[str, object] = {"symbols_fixed": 0, "models_fixed": 0, "unresolved": []}
    fp_dir = _cfg_path(cfg, "FootprintLib")
    fp_stems = {p.stem for p in fp_dir.glob("*.kicad_mod")} if fp_dir.is_dir() else set()
    mdl_dir = _cfg_path(cfg, "ModelLib")
    model_names = {p.name for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")} if mdl_dir.is_dir() else set()

    with _LIB_LOCK:
        # symbols → MyFootprints:<stem>
        sym_path = _cfg_path(cfg, "SymbolLib")
        if sym_path.is_file():
            blocks = extract_symbol_blocks(read_text(sym_path))
            new_blocks = list(blocks)
            changed = False
            for i, b in enumerate(blocks):
                m = _FP_PROP_RE.search(b)
                raw = (m.group(2).strip() if m else "")
                if not raw:
                    continue
                stem = re.split(r"[:\\/]", raw)[-1]
                if stem.lower().endswith(".kicad_mod"):
                    stem = stem[: -len(".kicad_mod")]
                portable = f"{FP_NICKNAME}:{stem}"
                if raw != portable:
                    nb = _FP_PROP_RE.sub(lambda mm: mm.group(1) + portable + mm.group(3), b, count=1)
                    if nb != b:
                        new_blocks[i] = nb
                        changed = True
                        result["symbols_fixed"] = int(result["symbols_fixed"]) + 1
                if stem not in fp_stems:
                    result["unresolved"].append(
                        f"{extract_symbol_name(b)} → footprint '{stem}' not found")
            if changed:
                new_text = insert_blocks_into_target(
                    '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
                    new_blocks)
                _snapshot_then_write(sym_path, new_text, log)

        # footprints → ${MY3DMODELS}/<basename>
        if fp_dir.is_dir():
            for p in sorted(fp_dir.glob("*.kicad_mod")):
                text = read_text(p)
                if not footprint_has_model(text):
                    continue
                new_text = _portablize_footprint_models(text)
                if new_text != text:
                    write_text(p, new_text)
                    result["models_fixed"] = int(result["models_fixed"]) + 1
                for raw in footprint_model_paths(new_text):
                    base = raw.replace("\\", "/").split("/")[-1]
                    if base not in model_names:
                        result["unresolved"].append(f"{p.stem} → 3D model '{base}' not found")

        register_libraries(cfg, log)

    log.write(f"Make portable: {result['symbols_fixed']} footprint link(s) + "
              f"{result['models_fixed']} model line(s) rewritten to portable form.")
    return result


def remove_symbol_by_index(symbol_lib_path: Path, index: int, log: UILog,
                           expected_name: Optional[str] = None) -> bool:
    """Remove exactly ONE symbol block, identified by its position in the file.

    This is what lets a single duplicate be deleted without removing its
    identically-named twin (unlike remove_symbol_by_name, which strips all
    matches). If the file changed since it was scanned — so the block at
    `index` no longer matches `expected_name` — the delete is aborted rather
    than risk removing the wrong symbol.
    """
    try:
        text = read_text(symbol_lib_path)
        blocks = extract_symbol_blocks(text)
        if index < 0 or index >= len(blocks):
            log.write(f"ERROR deleting symbol: index {index} out of range "
                      f"(library has {len(blocks)} symbols). Refresh and retry.")
            return False
        found_name = extract_symbol_name(blocks[index])
        if expected_name is not None and found_name != expected_name:
            log.write(f"WARN symbol delete aborted: expected '{expected_name}' at "
                      f"index {index} but found '{found_name}'. Refresh and retry.")
            return False
        del blocks[index]
        new_text = insert_blocks_into_target(
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py")\n)\n',
            blocks
        )
        _snapshot_then_write(symbol_lib_path, new_text, log)
        log.write(f"Deleted one copy of symbol '{found_name}' from {symbol_lib_path.name}")
        return True
    except Exception as e:
        log.write(f"ERROR deleting symbol at index {index}: {e}")
        return False


# -----------------------------
# Core: processing files
# -----------------------------
def wait_file_ready(path: Path, tries: int = 20, delay: float = 0.4) -> bool:
    prev_size = -1
    for _ in range(tries):
        if path.exists():
            try:
                size = path.stat().st_size
                if size == prev_size:
                    return True
                prev_size = size
            except Exception:
                pass
        time.sleep(delay)
    return path.exists()

def expand_zip_to_folder(zip_path: Path, dest_root: Path, log: UILog) -> Optional[Path]:
    base = zip_path.stem
    target = dest_root / base
    target.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(zip_path, "r") as zf:
            zf.extractall(target)
        log.write(f"Expanded {zip_path.name} to {target}")
        return target
    except BadZipFile as e:
        log.write(f"ERROR bad zip {zip_path}: {e}")
    except Exception as e:
        log.write(f"ERROR expand zip {zip_path}: {e}")
    return None

# One lock for every mutation of the shared library (the symbol file, the
# footprint/model dirs, and the follow-up git commit). The watcher spawns one
# thread per new ZIP: without this, two parallel imports read-modify-write
# MySymbols.kicad_sym concurrently and the last writer silently drops the other's
# symbols, while their commits race on git's index.lock. RLock because the batch
# path (process_existing_zips) holds it across its per-zip process_zip calls.
_LIB_LOCK = threading.RLock()


def merge_symbols(target_path: Path, sources: List[Path], log: UILog):
    # Serialize the read-modify-write of the shared symbol lib (LIB-13): merge_symbols is
    # reached from both lock-holding (process_zip) and lock-free (Merge button on a daemon
    # thread, drop-in, folder import) callers, so without the lock two writers can read the
    # same target and the last silently clobbers the other. _LIB_LOCK is an RLock, so the
    # lock-holding callers re-enter on the same thread without deadlock.
    if not sources:
        return
    with _LIB_LOCK:
        _merge_symbols_locked(target_path, sources, log)


def _merge_symbols_locked(target_path: Path, sources: List[Path], log: UILog):
    ensure_target_header(target_path)
    target_text = read_text(target_path)
    # Skip symbols already in the library so re-processing a part doesn't
    # create duplicate entries.
    # De-dup on the FULL raw symbol id (including any lib: prefix) so two distinct
    # source symbols that merely share a suffix (VendorA:R_0402 vs VendorB:R_0402)
    # are not silently dropped as duplicates.
    existing_names = {extract_symbol_raw_name(b) for b in extract_symbol_blocks(target_text)}
    total_blocks: List[str] = []
    skipped = 0
    for src in sources:
        try:
            src_text = read_text(src)
        except Exception as e:
            log.write(f"WARN read symbol {src}: {e}")
            continue
        # extract_symbol_blocks is a balanced-paren scanner that returns [] for
        # unbalanced/corrupt input. Never fall back to wrapping the raw text as a
        # block: splicing a malformed S-expr into the shared library makes it
        # unloadable by KiCad. An empty result is reported as "no symbols" below.
        blocks = extract_symbol_blocks(src_text)
        for b in blocks:
            nm = extract_symbol_raw_name(b)
            if nm in existing_names:
                skipped += 1
                continue
            existing_names.add(nm)
            # Point the symbol at the shared footprint library so it resolves
            # when placed in KiCad (was: kept the vendor's original nickname).
            total_blocks.append(rewrite_symbol_footprint(b, FP_NICKNAME))
    if not total_blocks:
        if skipped:
            log.write(f"No new symbols to merge ({skipped} duplicate(s) skipped).")
        else:
            log.write("No symbols found in source files.")
        return
    new_text = insert_blocks_into_target(target_text, total_blocks)
    try:
        write_text(target_path, new_text)
        suffix = f" ({skipped} duplicate(s) skipped)" if skipped else ""
        log.write(f"Merged {len(total_blocks)} symbol(s) into {target_path}{suffix}")
    except Exception as e:
        log.write(f"ERROR writing merged symbols: {e}")

def safe_install(src: Path, dst: Path, log: UILog, kind: str) -> str:
    """Copy src -> dst WITHOUT clobbering a different existing file.

    Returns one of: 'copied' (new file added), 'identical' (same content already
    present, nothing to do), 'skipped' (a *different* file already exists — left
    untouched), or 'error'. This is the overwrite protection for footprints and
    3D models, mirroring the symbol de-dup behaviour.
    """
    try:
        if dst.exists():
            if filecmp.cmp(str(src), str(dst), shallow=False):
                return "identical"
            log.write(f"SKIP {kind} '{dst.name}': a different file already exists "
                      f"(not overwritten)")
            return "skipped"
        shutil.copy2(src, dst)
        log.write(f"Added {kind}: {dst.name}")
        return "copied"
    except Exception as e:
        log.write(f"ERROR copy {kind} {src}: {e}")
        return "error"


def move_files(part_dir: Path, cfg: Dict[str, str], log: UILog):
    all_files = list(part_dir.rglob("*"))
    files = [p for p in all_files if p.is_file()]

    sym_files = [p for p in files if p.suffix.lower() == ".kicad_sym"]
    mod_files = [p for p in files if p.suffix.lower() == ".kicad_mod"]
    model_files = [p for p in files if p.suffix.lower() in (".step", ".stp", ".wrl")]

    # Merge symbols
    if sym_files:
        merge_symbols(Path(cfg["SymbolLib"]), sym_files, log)

    # Footprints (overwrite-protected)
    skipped = 0
    for m in mod_files:
        if safe_install(m, Path(cfg["FootprintLib"], m.name), log, "footprint") == "skipped":
            skipped += 1

    # 3D models (overwrite-protected)
    for mdl in model_files:
        if safe_install(mdl, Path(cfg["ModelLib"], mdl.name), log, "3D model") == "skipped":
            skipped += 1
    if skipped:
        log.write(f"Overwrite protection: skipped {skipped} existing file(s).")

    # Link each installed footprint to its 3D model so the model attaches when
    # placed. Match by name; if the part shipped exactly one model, use it.
    part_model_names = [Path(mdl.name) for mdl in model_files]
    for m in mod_files:
        fp_dst = Path(cfg["FootprintLib"], m.name)
        if not fp_dst.exists():
            continue
        matched = match_model_for_footprint(fp_dst.stem, part_model_names)
        if matched is None and len(part_model_names) == 1:
            matched = part_model_names[0]
        if matched is None:
            continue
        try:
            t = read_text(fp_dst)
            nt = ensure_footprint_model(t, matched.name)
            if nt != t:
                write_text(fp_dst, nt)
                log.write(f"Linked 3D model {matched.name} -> {m.name}")
        except Exception as e:
            log.write(f"WARN link model for {m.name}: {e}")

    # Unknown / junk -> misc
    allowed = {".kicad_sym", ".kicad_mod", ".step", ".stp", ".wrl", ".zip"}
    junk = [p for p in files if p.suffix.lower() not in allowed]
    for j in junk:
        dst = Path(cfg["MiscDir"], j.name)
        try:
            shutil.move(str(j), str(dst))
            log.write(f"Move misc: {j.name}")
        except Exception as e:
            log.write(f"WARN move misc {j}: {e}")

def remove_part_artifacts(zip_path: Optional[Path], part_dir: Optional[Path], log: UILog):
    if part_dir and part_dir.exists():
        try:
            shutil.rmtree(part_dir)
            log.write(f"Deleted folder {part_dir}")
        except Exception as e:
            log.write(f"WARN del folder {part_dir}: {e}")
    if zip_path and zip_path.exists():
        try:
            zip_path.unlink()
            log.write(f"Deleted zip {zip_path}")
        except Exception as e:
            log.write(f"WARN del zip {zip_path}: {e}")

def finalize_import(cfg: Dict[str, str], log: UILog, lookup=None) -> dict:
    """Post-merge finishing for imported parts, so a ZIP drop yields a READY part with
    no extra clicks: auto-link any missing footprint / 3D model, then (if a Mouser key
    is configured) fill blank manufacturer / datasheet / description / Mouser P/N. Both
    steps are idempotent and fill-blanks-only, so this is safe after every import — only
    the new/incomplete parts are touched, and a network failure just skips enrichment
    (the import still succeeds). Returns {linked, enriched}."""
    linked = auto_assign_library(cfg, dry_run=False, log=log)
    if linked.get("footprint_count") or linked.get("model_count"):
        log.write(f"Auto-linked {linked['footprint_count']} footprint(s), "
                  f"{linked['model_count']} 3D model(s)")
    enriched = {"written": False, "changes": [], "looked_up": 0}
    if lookup is None:
        lookup = providers_from_config(cfg)          # Mouser preferred, fallbacks after
    if lookup:
        enriched = enrich_library(cfg, lookup, log=log, dry_run=False)
        if enriched.get("changes"):
            # The provider chain may resolve via Mouser, LCSC or DigiKey — report the
            # ACTUAL distinct sources rather than hardcoding 'Mouser'.
            srcs = sorted({(c.get("source") or "").strip()
                           for c in enriched["changes"] if (c.get("source") or "").strip()})
            frm = f" from {', '.join(srcs)}" if srcs else " from distributors"
            log.write(f"Enriched {len(enriched['changes'])} symbol(s){frm} "
                      f"({enriched.get('looked_up', 0)} looked up)")
    return {"linked": linked, "enriched": enriched}


def process_zip(zip_path: Path, cfg: Dict[str, str], log: UILog, commit: bool = True,
                finalize: bool = True):
    base = zip_path.stem
    log.write(f"Processing: {base}")
    if not wait_file_ready(zip_path):
        log.write(f"Zip not ready: {zip_path}")
        return
    # Serialize the whole import: the watcher runs one thread per new ZIP, and the
    # library merge + git commit must never interleave between imports.
    with _LIB_LOCK:
        part_dir = expand_zip_to_folder(zip_path, Path(cfg["Downloads"]), log)
        if part_dir is None:
            return
        move_files(part_dir, cfg, log)
        remove_part_artifacts(zip_path, part_dir, log)
        # Finish the part: link footprint/3D + enrich from Mouser (batch runs defer
        # this to one pass at the end, finalize=False).
        changes = finalize_import(cfg, log) if finalize else None
        log.write(f"Done processing {base}")
        # Single-zip path (e.g. the watcher) commits immediately; batch runs skip
        # this and commit once at the end (commit=False). The message names the
        # part and what auto-linked / enriched (GIT-01).
        if commit:
            if changes is not None:
                commit_msg = nd_commit_msg.import_parts(
                    [base], linked=changes["linked"], enriched=changes["enriched"])
            else:
                commit_msg = nd_commit_msg.import_parts([base])
            git_commit_push(cfg, log, commit_msg)

def process_existing_zips(cfg: Dict[str, str], log: UILog, refresh_cb=None, progress_cb=None):
    zips = list(Path(cfg["Downloads"]).glob("*.zip"))
    if not zips:
        log.write("No ZIPs found in downloads")
        if refresh_cb:
            refresh_cb()
        return
    total = len(zips)
    names = []
    with _LIB_LOCK:                              # hold across the batch + its one commit
        for i, z in enumerate(zips, 1):
            if progress_cb:
                progress_cb(i, total, z.stem)
            names.append(z.stem)
            process_zip(z, cfg, log, commit=False, finalize=False)   # defer git + finalize
        # One finalize (link + enrich) + one commit + one push for the whole batch.
        if names:
            changes = finalize_import(cfg, log)
            msg = nd_commit_msg.import_parts(
                names, linked=changes["linked"], enriched=changes["enriched"])
            git_commit_push(cfg, log, msg)
    if refresh_cb:
        refresh_cb()

def process_folder_dialog(cfg: Dict[str, str], log: UILog, refresh_cb=None):
    folder = QFileDialog.getExistingDirectory(None, "Select Extracted Part Folder", cfg["Downloads"])
    if not folder:
        return
    folder_path = Path(folder)
    log.write(f"Manual process folder: {folder_path}")
    move_files(folder_path, cfg, log)
    log.write("Done manual processing")
    # Immediately stage, commit, and push (only if something actually changed).
    # A manual folder import skips finalize, so there's no link/enrich change-set.
    git_commit_push(cfg, log, nd_commit_msg.import_parts([folder_path.name]))
    if refresh_cb:
        refresh_cb()

def clean_leftovers(cfg: Dict[str, str], log: UILog, refresh_cb=None):
    """Delete any remaining *.zip and extracted folders in Downloads"""
    downloads = Path(cfg["Downloads"])
    zips = list(downloads.glob("*.zip"))
    dirs = [p for p in downloads.iterdir() if p.is_dir()]
    if not zips and not dirs:
        log.write("Clean: nothing to remove in downloads")
        if refresh_cb:
            refresh_cb()
        return
   
    msg = (f"This will delete {len(zips)} ZIP file(s) and {len(dirs)} folder(s)\n"
           f"in:\n{downloads}\n\nProceed?")
    reply = QMessageBox.question(None, "Confirm Clean Leftovers", msg,
                                  QMessageBox.Yes | QMessageBox.No)
    if reply != QMessageBox.Yes:
        log.write("Clean: canceled by user")
        return

    # Delete zip files
    for zp in zips:
        try:
            zp.unlink()
            log.write(f"Clean: deleted zip {zp.name}")
        except Exception as e:
            log.write(f"WARN clean zip {zp}: {e}")

    # Delete directories
    for d in dirs:
        try:
            shutil.rmtree(d)
            log.write(f"Clean: deleted folder {d.name}")
        except Exception as e:
            log.write(f"WARN clean folder {d}: {e}")

    log.write("Clean: finished deleting leftovers")
    if refresh_cb:
        refresh_cb()


# -----------------------------
# Git commands  (unified onto nd_git — see docs/design WS-E / GIT-03)
# -----------------------------
# These thin wrappers keep their historical ``(cfg, log, …) -> bool`` signatures
# so the drop-in / inline-edit / import / commit_and_push call sites don't change,
# but every git line now rides ``nd_git``: the ONE PAT-authenticated,
# corruption-guarded, timeout-bounded backend the Git tab already uses. This is
# the fix for the old unauthenticated push — a credential-less https clone (every
# Library auto-push) now injects the PAT header instead of failing — and it
# collapses the two duplicated corruption scanners into one.
#
# The corrupt-KiCad scanners live in nd_git now; re-export the names so existing
# callers (ui.features.library, the audit tests) keep importing them from here.
has_conflict_markers = nd_git.has_conflict_markers
is_paren_balanced = nd_git.is_paren_balanced
find_corrupt_kicad_files = nd_git.find_corrupt_kicad_files


def git_pull(cfg: Dict[str, str], log: UILog) -> bool:
    """Fast-forward-only pull via nd_git (PAT-authenticated for https remotes).
    Never merges or rewrites local work. Returns True on success."""
    log.write("Git pull (fast-forward only)…")
    r = nd_git.pull_ff_only(cfg["RepoRoot"])
    if r.message:
        log.write(r.message)
    if not r.ok:
        log.write(f"ERROR git pull exit {r.code}")
    return r.ok


def git_push(cfg: Dict[str, str], log: UILog) -> bool:
    """Push the current branch via nd_git. nd_git injects the PAT Authorization
    header for https remotes, so a credential-less clone now authenticates
    instead of failing (the fix). Returns True on success."""
    log.write("Git push…")
    r = nd_git.push(cfg["RepoRoot"])
    if r.message:
        log.write(r.message)
    if not r.ok:
        log.write(f"ERROR git push exit {r.code}")
    return r.ok


def git_stage_commit(cfg: Dict[str, str], log: UILog, message: Optional[str] = None) -> bool:
    """Stage everything and commit via nd_git. Returns True only if a commit was
    made.

    A work-tree pre-scan refuses to stage/commit when any KiCad file
    (*.kicad_sym/.kicad_pcb/.kicad_sch) still carries merge-conflict markers or is
    paren-unbalanced; nd_git.commit re-checks the *staged* content as a second
    guard. Either way corruption never gets committed or pushed."""
    repo = cfg["RepoRoot"]
    corrupt = find_corrupt_kicad_files(repo)
    if corrupt:
        log.write("ERROR commit ABORTED: corrupt KiCad file(s) detected — "
                  "refusing to commit corruption:")
        for p, reason in corrupt:
            log.write(f"  {p}: {reason}")
        log.write("Fix the file(s) above (resolve conflicts / balance parens) and retry.")
        return False
    st = nd_git.stage_all(repo)
    if not st.ok:
        log.write(st.message or "ERROR git add failed")
        return False
    if not message:
        message = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    ok, info = nd_git.commit(repo, message)
    if not ok:
        # "nothing to commit" is the benign clean-tree case; anything else is a
        # real git error (missing identity, guard refusal) worth surfacing.
        log.write("Nothing to commit (working tree clean)"
                  if "nothing to commit" in info.lower() else info)
        return False
    log.write(f"Committed {info[:12]}: {message}")
    return True

def git_discard_uncommitted(cfg: Dict[str, str], log: UILog) -> bool:
    """Discard the library repo's uncommitted work-tree edits (restore tracked
    files to HEAD). Backs the Library editor's Discard: inline field edits write
    to disk immediately but only commit on Save, so Discard reverts those pending
    edits. Local + hidden — no network, never flashes. Returns True on success."""
    r = nd_git.restore_worktree(cfg["RepoRoot"])
    if not r.ok:
        log.write(r.message or f"ERROR git restore exit {r.code}")
    return r.ok


def commit_and_push(cfg: Dict[str, str], log: UILog):
    """Combined action: Stage all, prompt for commit message, commit, then push"""
    default = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    msg, ok = QInputDialog.getText(None, "Commit Message", "Enter commit message:", text=default)
    if not ok:
        log.write("Commit: canceled by user")
        return
    if git_stage_commit(cfg, log, message=msg.strip() or default):
        git_push(cfg, log)
    else:
        log.write("Push skipped: nothing was committed")


# -----------------------------
# Watcher (optional)
# -----------------------------


# -----------------------------
# Helpers: safe copy
# -----------------------------
def safe_copy_to_downloads(src_path: Path, downloads: Path) -> Path:
    """Copy src_path to downloads, avoiding overwrite by adding (1), (2), ... suffix"""
    downloads.mkdir(parents=True, exist_ok=True)
    dst = downloads / src_path.name
    if not dst.exists():
        shutil.copy2(src_path, dst)
        return dst

    stem = dst.stem
    suffix = dst.suffix
    i = 1
    while True:
        candidate = downloads / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            shutil.copy2(src_path, candidate)
            return candidate
        i += 1


# -----------------------------
# Library scan + filtering
# -----------------------------
def scan_library(cfg: Dict[str, str]):
    """
    Scan current library contents.
    Returns (rows, summary) where rows is list of dicts:
    {type: 'Symbol'|'Footprint'|'Model', name: str, path: Path}
    """
    rows: List[Dict[str, object]] = []

    def _date(p: Path) -> str:
        try:
            return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))
        except Exception:
            return ""

    # Footprints
    fp_dir = Path(cfg["FootprintLib"])
    if fp_dir.exists():
        for p in sorted(fp_dir.glob("*.kicad_mod")):
            rows.append({"type": "Footprint", "name": p.stem, "path": p, "date": _date(p)})

    # Models
    mdl_dir = Path(cfg["ModelLib"])
    if mdl_dir.exists():
        for ext in ("*.step", "*.stp", "*.wrl"):
            for p in sorted(mdl_dir.glob(ext)):
                rows.append({"type": "Model", "name": p.name, "path": p, "date": _date(p)})

    # Symbols. sym_index is the block's position in the file, so a single
    # duplicate can be removed without disturbing its identically-named twin.
    sym_path = Path(cfg["SymbolLib"])
    if sym_path.exists():
        try:
            sym_date = _date(sym_path)
            text = read_text(sym_path)
            blocks = extract_symbol_blocks(text)
            for i, b in enumerate(blocks):
                nm = extract_symbol_name(b)
                rows.append({"type": "Symbol", "name": nm, "path": sym_path,
                             "sym_index": i, "date": sym_date})
        except Exception:
            pass

    # Flag duplicates: rows that share the same (type, name).
    counts: Dict[tuple, int] = {}
    for r in rows:
        key = (r["type"], r["name"])
        counts[key] = counts.get(key, 0) + 1
    for r in rows:
        r["dup_count"] = counts[(r["type"], r["name"])]
        r["dup"] = r["dup_count"] > 1

    summary = {
        "symbols": sum(1 for r in rows if r["type"] == "Symbol"),
        "footprints": sum(1 for r in rows if r["type"] == "Footprint"),
        "models": sum(1 for r in rows if r["type"] == "Model"),
        "duplicates": sum(1 for r in rows if r["dup"]),
        "total": len(rows),
    }
    return rows, summary


def group_components(rows: List[Dict[str, object]]):
    """Cluster rows (symbol / footprint / 3D model) that belong to the same
    component, even when their names differ slightly (e.g. TPS2121RUXR symbol,
    TPS2121RUX footprint, TPS2121 model). Union by shared normalized-name prefix.
    Returns a list of (label, [rows]) sorted by label."""
    n = len(rows)

    def norm(name):
        stem = re.sub(r"\.(step|stp|wrl|kicad_mod|kicad_sym)$", "", str(name), flags=re.I)
        return re.sub(r"[^a-z0-9]", "", stem.lower())

    norms = [norm(r["name"]) for r in rows]
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        a = norms[i]
        if not a:
            continue
        for j in range(i + 1, n):
            b = norms[j]
            if not b:
                continue
            m = min(len(a), len(b))
            k = 0
            while k < m and a[k] == b[k]:
                k += 1
            # group when they share a long common prefix (>=4 chars and >=70%)
            if m >= 4 and k >= 4 and k >= 0.7 * m:
                union(i, j)

    groups: Dict[int, list] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    out = [(min((str(x["name"]) for x in grp), key=len), grp) for grp in groups.values()]
    out.sort(key=lambda g: g[0].lower())
    return out


def export_catalog(cfg: Dict[str, str], log: UILog, progress_cb=None) -> Optional[Path]:
    """Write one big Markdown catalog (`library_catalog.md`) with a rendered PNG
    + metadata for every footprint, plus tables of 3D models and symbols. The
    single file (with its catalog_assets/ images) is meant to be human- and
    AI-readable as a complete reference of the library."""
    import fp_render
    root = Path(cfg["RepoRoot"])
    assets = root / "catalog_assets"
    assets.mkdir(parents=True, exist_ok=True)
    rows, summary = scan_library(cfg)
    fps = sorted([r for r in rows if r["type"] == "Footprint"], key=lambda r: str(r["name"]).lower())
    models = sorted([r for r in rows if r["type"] == "Model"], key=lambda r: str(r["name"]).lower())
    syms = sorted([r for r in rows if r["type"] == "Symbol"], key=lambda r: str(r["name"]).lower())

    out: List[str] = []
    out.append("# KiCad Manager Catalog\n")
    out.append(f"Generated {time.strftime('%Y-%m-%d %H:%M')} — "
               f"{summary['footprints']} footprints, {summary['models']} 3D models, "
               f"{summary['symbols']} symbols.\n")

    out.append("## Footprints\n")
    rendered = 0
    for i, r in enumerate(fps, 1):
        if progress_cb:
            progress_cb(i, len(fps), str(r["name"]))
        p = Path(r["path"])
        rel = ""
        try:
            img = fp_render.render_footprint_image(p, 360)
            if img is not None:
                fn = assets / (p.stem + ".png")
                img.save(str(fn))
                rel = f"catalog_assets/{fn.name}"
                rendered += 1
        except Exception as e:
            log.write(f"Catalog: render failed for {p.name}: {e}")
        s = fp_render.footprint_summary(p) or {}
        out.append(f"### {r['name']}\n")
        if rel:
            out.append(f"![{r['name']}]({rel})\n")
        out.append(f"- Pads: {s.get('pads', '?')} ({s.get('smd_pads', 0)} SMD, {s.get('tht_pads', 0)} through-hole)")
        out.append(f"- Body: {s.get('width_mm', '?')} × {s.get('height_mm', '?')} mm")
        out.append(f"- Layers: {', '.join(s.get('layers', [])) or '—'}")
        out.append(f"- File: `{p.name}` · added {r.get('date', '')}\n")

    out.append("## 3D Models\n")
    rendered_3d = 0
    for j, r in enumerate(models, 1):
        if progress_cb:
            progress_cb(len(fps) + j, len(fps) + len(models), str(r["name"]))
        p = Path(r["path"])
        rel = ""
        try:
            img = fp_render.render_step_image(p, 360)
            if img is not None:
                fn = assets / (p.stem + "_3d.png")
                img.save(str(fn))
                rel = f"catalog_assets/{fn.name}"
                rendered_3d += 1
        except Exception:
            pass
        s = fp_render.step_summary(p) or {}
        kb = (p.stat().st_size // 1024) if p.exists() else 0
        out.append(f"### {r['name']}\n")
        if rel:
            out.append(f"![{r['name']}]({rel})\n")
        dims = s.get("size_mm")
        if dims:
            out.append(f"- Size: {dims[0]} × {dims[1]} × {dims[2]} mm")
        out.append(f"- Triangles: {s.get('triangles', '?')}")
        out.append(f"- File: `{p.name}` · {kb} KB · added {r.get('date', '')}\n")

    out.append(f"## Symbols ({len(syms)})\n")
    sym_cache: Dict[Path, list] = {}
    rendered_sym = 0
    for r in syms:
        p = Path(r["path"])
        if p not in sym_cache:
            try:
                sym_cache[p] = extract_symbol_blocks(read_text(p))
            except Exception:
                sym_cache[p] = []
        blocks = sym_cache[p]
        idx = r.get("sym_index")
        rel = ""
        try:
            if idx is not None and 0 <= idx < len(blocks):
                img = fp_render.render_symbol_image(blocks[idx], 300)
                if img is not None:
                    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(r["name"]))[:60]
                    fn = assets / f"sym_{idx}_{safe}.png"
                    img.save(str(fn))
                    rel = f"catalog_assets/{fn.name}"
                    rendered_sym += 1
        except Exception:
            pass
        out.append(f"### {r['name']}\n")
        if rel:
            out.append(f"![{r['name']}]({rel})\n")
        out.append(f"- Symbol in `{p.name}`\n")

    md = root / "library_catalog.md"
    write_text(md, "\n".join(out))
    log.write(f"Catalog written: {md.name} "
              f"({rendered}/{len(fps)} footprints, {rendered_3d}/{len(models)} 3D models, "
              f"{rendered_sym}/{len(syms)} symbols rendered)")
    return md

def filter_rows(rows: List[Dict[str, object]], query: str, type_filter: str,
                dup_only: bool = False) -> List[Dict[str, object]]:
    q = (query or "").strip().lower()
    tf = type_filter
    out: List[Dict[str, object]] = []
    for r in rows:
        if dup_only and not r.get("dup"):
            continue
        # Support a list/set of types (multi-select), or a single string
        if isinstance(tf, (list, set)):
            if len(tf) > 0 and "All" not in tf and r["type"] not in tf:
                continue
        else:
            if tf != "All" and r["type"] != tf:
                continue
        name = str(r["name"]).lower()
        if q and q not in name:
            continue
        out.append(r)
    return out


# -----------------------------
# Custom Drop Zone Widget
# -----------------------------


# -----------------------------
# Card-like container for modern UI sections


# CardWidget lives in the shared design system (tools/ui_widgets.py) so every
# tab builds the same card chrome.

# -----------------------------
# Flow layout (wraps its widgets to new rows as width shrinks)
# -----------------------------


# -----------------------------
# Main Window
# -----------------------------


# -----------------------------
# Main
# -----------------------------
def main():
    """Launch NETDECK — the polished redesign shell (the converged, at-parity,
    drive-audited UI). The legacy barebones UI was removed at the Phase-3 flip. This is
    the exe entry point, so it must match ui/__main__.py."""
    from ui.shell import run
    return run()


if __name__ == "__main__":
    main()