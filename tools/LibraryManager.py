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
from typing import Optional, List, Dict
import webbrowser

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

    Resolution: MOUSER_API_KEY env var (silent dev override) -> baked default in
    app_secrets.MOUSER_API_KEY_DEFAULT. The old config.json 'MouserApiKey' is no
    longer consulted; `cfg` is accepted only for call-site compatibility.
    """
    return os.environ.get("MOUSER_API_KEY") or app_secrets.MOUSER_API_KEY_DEFAULT


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


# Theme tokens, fonts, and Lucide icons live in the shared design system
# (tools/ui_theme.py) so every tab draws from one palette and no tab has to
# import another for its icons.
from ui_theme import (  # noqa: F401  (re-exported for the other tabs)
    resource_path, load_bundled_fonts, lucide_icon,
    LUCIDE_NEUTRAL, LUCIDE_BLUE, LUCIDE_GREEN, LUCIDE_RED, LUCIDE_AMBER,
)
import ui_theme


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
            encoding="utf-8"
        )
    return cfg


def save_config(cfg: Dict[str, str]):
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"WARNING: failed to write config.json: {e}")


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
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
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

def extract_symbol_name(block: str) -> str:
    """Extract symbol name from block"""
    head = block.splitlines()[0]
    try:
        if '(symbol "' in head:
            start = head.index('(symbol "') + len('(symbol "')
            end = head.index('"', start)
            raw = head[start:end]
            name = raw.split(':')[-1]
            return name
        if '(name "' in block:
            start = block.index('(name "') + len('(name "')
            end = block.index('"', start)
            return block[start:end]
    except Exception:
        pass
    return head.strip()

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

def _snapshot_then_write(symbol_lib_path: Path, new_text: str, log: UILog):
    """Destructive symbol-library rewrite with an undo copy: snapshot the current
    file into libs/.trash/<timestamp>/ first, then write. A failed snapshot logs
    and continues (the user asked for the operation); a restore is one copy-back."""
    try:
        src = Path(symbol_lib_path)
        if src.exists():
            from datetime import datetime as _dt
            dst_dir = src.parent / ".trash" / _dt.now().strftime("%Y%m%d_%H%M%S")
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / src.name)
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
         f"**{counts['on_mouser']} / {counts['parts']} on Mouser** — "
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
            rep = f" — replace with {r['suggested_replacement']}" if r.get("suggested_replacement") else ""
            L.append(f"- **{r['symbol']}** ({r['mpn']}): {', '.join(why)}{rep}")
        L.append("")
    elsewhere = [r for r in rows if r["found"] and not r.get("on_mouser")]
    if elsewhere:
        L += ["## Not on Mouser (found on a fallback)", ""]
        L += [f"- {r['symbol']} ({r['mpn']}) — via {r['source']}" for r in elsewhere] + [""]
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
        changes, edits, looked_up = [], [], 0
        for b in blocks:
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
                changes.append({"symbol": name, "mpn": mpn, "filled": filled})
                edits.append((b, nb))
        written = False
        if not dry_run and edits:
            new_text = text
            for old, new in edits:
                new_text = new_text.replace(old, new, 1)
            _snapshot_then_write(sym_path, new_text, log or _NullLog())
            written = True
        return {"changes": changes, "written": written, "symbols": len(blocks),
                "looked_up": looked_up}


class _NullLog:
    def write(self, *_a, **_k):
        pass


def _parse_mouser_part(p: dict) -> dict:
    """One Mouser API part -> our normalized field dict."""
    breaks = p.get("PriceBreaks") or []
    try:
        stock = int(p.get("AvailabilityInStock") or 0)
    except (TypeError, ValueError):
        stock = 0
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
            "url": p.get("ProductDetailUrl"),
            "suggested_replacement": p.get("SuggestedReplacement")}


def _mouser_post(endpoint: str, api_key: str, payload: dict, timeout: int = 8):
    """POST to a Mouser Search API endpoint; return the parsed JSON or None (never
    raises)."""
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(
            f"https://api.mouser.com/api/v1/search/{endpoint}?apiKey={api_key}",
            data=_json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode())
    except Exception:                                # noqa: BLE001
        return None


def make_mouser_lookup(api_key: str, timeout: int = 8):
    """A lookup(mpn) -> normalized part dict backed by the Mouser Search API. Requires a
    Mouser API key. Returns None for anything it can't resolve and NEVER raises."""
    def lookup(mpn):
        if not (api_key and mpn):
            return None
        data = _mouser_post("partnumber", api_key,
                            {"SearchByPartRequest": {"mouserPartNumber": mpn,
                                                     "partSearchOptions": "Exact"}}, timeout)
        parts = ((data or {}).get("SearchResults") or {}).get("Parts") or []
        if not parts:
            return None
        up = mpn.strip().upper()
        p = next((x for x in parts
                  if (x.get("ManufacturerPartNumber") or "").upper() == up), parts[0])
        return _parse_mouser_part(p)
    return lookup


def search_parts(query: str, cfg: Dict[str, str] = None, limit: int = 10) -> dict:
    """Built-in part lookup: search Mouser by MPN or keyword and return up to `limit`
    normalized results (manufacturer, datasheet, stock, price, lifecycle, Mouser P/N,
    url). No import needed — for looking a part up before you use it. Returns
    {query, results, error}."""
    key = resolve_mouser_key(cfg)
    if not key:
        return {"query": query, "results": [], "error": "no Mouser API key configured"}
    if not (query or "").strip():
        return {"query": query, "results": [], "error": "empty query"}
    data = _mouser_post("keyword", key,
                        {"SearchByKeywordRequest": {"keyword": query.strip(),
                                                    "records": max(1, min(limit, 50)),
                                                    "startingRecord": 0}})
    parts = ((data or {}).get("SearchResults") or {}).get("Parts") or []
    return {"query": query, "results": [_parse_mouser_part(p) for p in parts[:limit]], "error": ""}


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


def register_libraries(cfg: Dict[str, str], log: UILog) -> bool:
    """Register MySymbols + MyFootprints and define ${MY3DMODELS} in KiCad config."""
    cfgdir = find_kicad_config_dir()
    if cfgdir is None:
        log.write("Register: KiCad config dir not found (is KiCad installed?).")
        return False
    sym = ensure_lib_entry(cfgdir / "sym-lib-table", "sym_lib_table", "MySymbols",
                           str(cfg["SymbolLib"]).replace("\\", "/"))
    fp = ensure_lib_entry(cfgdir / "fp-lib-table", "fp_lib_table", "MyFootprints",
                          str(cfg["FootprintLib"]).replace("\\", "/"))
    envset = ensure_env_var(cfgdir / "kicad_common.json", MODEL_VAR,
                            str(cfg["ModelLib"]).replace("\\", "/"))
    log.write(f"KiCad registration ({cfgdir.name}): MySymbols "
              f"{'added' if sym else 'ok'}, MyFootprints {'added' if fp else 'ok'}, "
              f"${{{MODEL_VAR}}} {'set' if envset else 'ok'}.")
    return sym or fp or envset


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


def associate_parts_from_cfg(cfg: Dict[str, str], overrides: Optional[dict] = None) -> List[dict]:
    """associate_parts() sourced from the configured shared-library paths."""
    sym_path = Path(cfg["SymbolLib"])
    symbol_text = read_text(sym_path) if sym_path.exists() else ""
    fp_dir = Path(cfg["FootprintLib"])
    footprints = {p.stem: read_text(p) for p in sorted(fp_dir.glob("*.kicad_mod"))} \
        if fp_dir.exists() else {}
    mdl_dir = Path(cfg["ModelLib"])
    model_files = [p.name for p in sorted(mdl_dir.glob("*"))
                   if p.suffix.lower() in (".step", ".stp", ".wrl")] if mdl_dir.exists() else []
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
    }


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
    fp_dir = Path(cfg["FootprintLib"])
    fp_stems = {p.stem for p in fp_dir.glob("*.kicad_mod")} if fp_dir.exists() else set()
    mdl_dir = Path(cfg["ModelLib"])
    model_names = {p.name for p in mdl_dir.glob("*")
                   if p.suffix.lower() in (".step", ".stp", ".wrl")} if mdl_dir.exists() else set()

    # symbol name -> its property dict, read once (identity source for every row)
    sym_props: Dict[str, Dict[str, str]] = {}
    sym_path = Path(cfg["SymbolLib"])
    if sym_path.exists():
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
        ident = {"mpn": None, "manufacturer": None, "datasheet": None, "description": None}
        for s in symbols:
            cand = part_identity(sym_props.get(s, {}), fallback="")
            if cand["mpn"] or cand["manufacturer"]:
                ident = cand
                break

        rows.append({
            "name": name,
            "mpn": ident["mpn"] or name,       # the part's canonical (Mouser) name
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
        })
    return rows


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
    """The distributor lookup chain from configured keys. Mouser is the automatic,
    PREFERRED provider; anything Mouser does not carry is left for MANUAL sourcing
    (DigiKey / LCSC / etc.) and is flagged as such in the sourcing report + BOM. Returns
    a source-tagged lookup(mpn), or None if no key is configured. Add more providers by
    registering a verified adapter with make_provider_chain."""
    mk = resolve_mouser_key(cfg)
    return make_provider_chain([("Mouser", make_mouser_lookup(mk))]) if mk else None


def consolidated_bom(boards: Dict[str, list], lookup=None) -> dict:
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
                m["source"] = res.get("source", "Mouser")
                for f in ("manufacturer", "datasheet"):
                    if not m[f] and res.get(f):
                        m[f] = res[f]
            else:
                m["source"] = "NOT FOUND"

    rows = sorted(merged.values(), key=lambda r: (r["value"].lower(), r["footprint"].lower()))
    sourced = bool(lookup)
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    head = ["MPN", "Manufacturer", "Value", "Footprint", "Total"] + board_names + ["Datasheet"]
    if sourced:
        head.insert(5, "Source")                     # which provider carries it (or NOT FOUND)
    w.writerow(head)
    for r in rows:
        row = [r["mpn"], r["manufacturer"], r["value"], r["footprint"], r["total_qty"]]
        if sourced:
            row.append(r.get("source", ""))
        row += [r["per_board"].get(b, 0) for b in board_names] + [r["datasheet"]]
        w.writerow(row)
    out = {"rows": rows, "board_names": board_names, "csv": buf.getvalue(),
           "line_count": len(rows), "total_parts": sum(r["total_qty"] for r in rows)}
    if sourced:
        out["not_on_mouser"] = [r["mpn"] or r["value"] for r in rows
                                if r.get("source") not in ("Mouser", "")]
    return out


def bom_from_kicad_schematic(sch_path, lookup=None,
                             enrich_fields=("manufacturer", "datasheet")) -> dict:
    """Smart BOM from a KiCad 6+/7+ schematic (.kicad_sch), using our identity + enrich
    features on any KiCad file — not just the cards this tool designs.

    Pulls every real component (skips power / virtual / excluded-from-BOM symbols),
    reads its properties, resolves the canonical MPN / manufacturer via part_identity
    (the same logic that groups the library), then groups identical parts — by MPN when
    present, else value + footprint — with their reference designators and quantity.
    If a `lookup(mpn) -> {...}` is given (e.g. make_mouser_lookup), it fills BLANK
    manufacturer / datasheet per group. Read-only; returns {rows, component_count,
    line_count, csv}."""
    import csv as _csv
    import io as _io
    from fp_render import parse_sexpr
    root = parse_sexpr(Path(sch_path).read_text(encoding="utf-8", errors="replace"))
    if not root or root[0] != "kicad_sch":
        return {"error": "not a KiCad schematic (.kicad_sch)", "rows": [],
                "component_count": 0, "line_count": 0, "csv": ""}

    comps = []
    for node in root[1:]:
        if not (isinstance(node, list) and node and node[0] == "symbol"):
            continue                                  # only top-level symbol instances
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
            continue                                  # power rails / virtual parts
        comps.append((ref, props))

    groups: dict = {}
    for ref, props in comps:
        ident = part_identity(props, fallback=props.get("Value", ""))
        # MPN column: a dedicated MPN property wins; else, if the part carries a
        # manufacturer (SnapEDA/Mouser ICs put the MPN in Value), the Value IS the
        # MPN; a bare passive (value only, no manufacturer) gets no MPN.
        smpn = strict_mpn(props)
        if not smpn and ident["manufacturer"]:
            v = (props.get("Value") or "").strip()
            smpn = v if v and v.lower() not in _PLACEHOLDERS else None
        key = smpn or ("VF", props.get("Value", ""), props.get("Footprint", ""))
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
                     "footprint": g["footprint"], "datasheet": g["datasheet"] or "",
                     "description": g["description"] or ""})
    rows.sort(key=lambda r: (r["value"].lower(), r["footprint"].lower(),
                             _natural_ref(r["refs"][0]) if r["refs"] else ("", 0)))

    buf = _io.StringIO()
    w = _csv.writer(buf, lineterminator="\n")
    w.writerow(["Refs", "Qty", "Value", "MPN", "Manufacturer", "Footprint",
                "Datasheet", "Description"])
    for r in rows:
        w.writerow([",".join(r["refs"]), r["qty"], r["value"], r["mpn"],
                    r["manufacturer"], r["footprint"], r["datasheet"], r["description"]])
    return {"rows": rows, "component_count": len(comps), "line_count": len(rows),
            "csv": buf.getvalue()}


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
        sym_edits = []
        for b in extract_symbol_blocks(sym_text):
            name = extract_symbol_name(b)
            cur = symbol_footprint_ref(b) or ""      # "Nickname:Stem" or ""
            cur_stem = cur.split(":")[-1] if cur else ""
            if cur_stem and cur_stem in fp_stems:
                continue                             # already resolves
            stem, reason = suggest_footprint_for_symbol(
                name, cur_stem, extract_symbol_properties(b), fp_stems)
            if stem:
                fp_assigns.append({"symbol": name, "assign": stem, "reason": reason})
                sym_edits.append((b, set_symbol_property(b, "Footprint", f"{FP_NICKNAME}:{stem}")))

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
        if not dry_run and (sym_edits or fp_writes):
            if sym_edits:
                new = sym_text
                for old, nb in sym_edits:
                    new = new.replace(old, nb, 1)
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
    if not sources:
        return
    ensure_target_header(target_path)
    target_text = read_text(target_path)
    # Skip symbols already in the library so re-processing a part doesn't
    # create duplicate entries.
    existing_names = {extract_symbol_name(b) for b in extract_symbol_blocks(target_text)}
    total_blocks: List[str] = []
    skipped = 0
    for src in sources:
        try:
            src_text = read_text(src)
        except Exception as e:
            log.write(f"WARN read symbol {src}: {e}")
            continue
        blocks = extract_symbol_blocks(src_text)
        if not blocks and "(symbol" in src_text:
            blocks = [src_text.strip()]
        for b in blocks:
            nm = extract_symbol_name(b)
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
            log.write(f"Enriched {len(enriched['changes'])} symbol(s) from Mouser "
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
        if finalize:
            finalize_import(cfg, log)
        log.write(f"Done processing {base}")
        # Single-zip path (e.g. the watcher) commits immediately; batch runs skip
        # this and commit once at the end (commit=False).
        if commit:
            commit_msg = f"Auto-update: processed {zip_path.name}"
            if git_stage_commit(cfg, log, message=commit_msg):
                git_push(cfg, log)

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
            finalize_import(cfg, log)
            if len(names) == 1:
                msg = f"Auto-update: processed {names[0]}"
            else:
                shown = ", ".join(names[:6]) + ("…" if len(names) > 6 else "")
                msg = f"Auto-update: processed {len(names)} parts ({shown})"
            if git_stage_commit(cfg, log, message=msg):
                git_push(cfg, log)
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
    # Immediately stage, commit, and push (only if something actually changed)
    if git_stage_commit(cfg, log, message=f"Auto-update: processed folder {folder_path.name}"):
        git_push(cfg, log)
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
# Git commands
# -----------------------------
# Commit-safety guards. A committed KiCad file that still holds merge-conflict
# markers or unbalanced parens is corrupt and unusable, so we refuse to commit
# one rather than push corruption to everyone else (this is exactly how the
# libs/MySymbols.kicad_sym breakage got shared last time).
_CONFLICT_MARKER_RE = re.compile(r'^(<{7}|={7}|>{7})', re.MULTILINE)
_KICAD_TEXT_SUFFIXES = (".kicad_sym", ".kicad_pcb", ".kicad_sch")


def has_conflict_markers(text: str) -> bool:
    """True if text contains a git merge-conflict marker at the start of a line
    ('<<<<<<<', '=======', or '>>>>>>>')."""
    return _CONFLICT_MARKER_RE.search(text) is not None


def is_paren_balanced(text: str) -> bool:
    """True if parentheses balance across the whole text, ignoring parens that
    appear inside quoted strings (honoring KiCad's \\-escapes). A file whose
    depth ever goes negative, or ends non-zero, is unbalanced."""
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':                       # quoted string: parens inside don't count
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def find_corrupt_kicad_files(repo_root) -> List[tuple]:
    """Scan *.kicad_sym/.kicad_pcb/.kicad_sch under repo_root for corruption.
    Returns a list of (path, reason) for every file that carries merge-conflict
    markers or is paren-unbalanced. Skips the .git directory."""
    bad: List[tuple] = []
    root = Path(repo_root)
    if not root.exists():
        return bad
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in _KICAD_TEXT_SUFFIXES:
            continue
        if ".git" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if has_conflict_markers(text):
            bad.append((p, "merge-conflict markers"))
        elif not is_paren_balanced(text):
            bad.append((p, "unbalanced parentheses"))
    return bad


def run_git(args: List[str], cfg: Dict[str, str], log: UILog):
    try:
        proc = run_hidden(
            ["git", "-C", cfg["RepoRoot"], *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8"
        )
        out = proc.stdout or ""
        for line in out.splitlines():
            log.write(line)
        if proc.returncode != 0:
            log.write(f"ERROR git {' '.join(args)} exit {proc.returncode}")
    except FileNotFoundError:
        log.write("ERROR: git not found on PATH. Install Git and retry.")
    except Exception as e:
        log.write(f"ERROR running git {' '.join(args)}: {e}")
def git_pull(cfg: Dict[str, str], log: UILog):
    log.write("Git pull (fast-forward only)…")
    run_git(["pull", "--ff-only"], cfg, log)

def git_push(cfg: Dict[str, str], log: UILog):
    log.write("Git push...")
    run_git(["push"], cfg, log)

def git_has_staged_changes(cfg: Dict[str, str]) -> bool:
    """True if there is something staged to commit. Avoids the noisy
    'nothing to commit' ERROR (git exits non-zero when there's nothing)."""
    try:
        proc = run_hidden(
            ["git", "-C", cfg["RepoRoot"], "diff", "--cached", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return proc.returncode != 0
    except Exception:
        return True


def git_stage_commit(cfg: Dict[str, str], log: UILog, message: Optional[str] = None) -> bool:
    """Stage everything and commit. Returns True only if a commit was made.

    Refuses to stage/commit when any tracked KiCad file (*.kicad_sym/.kicad_pcb/
    .kicad_sch) still carries merge-conflict markers or is paren-unbalanced, so
    corruption never gets committed or pushed."""
    corrupt = find_corrupt_kicad_files(cfg["RepoRoot"])
    if corrupt:
        log.write("ERROR commit ABORTED: corrupt KiCad file(s) detected — "
                  "refusing to commit corruption:")
        for p, reason in corrupt:
            log.write(f"  {p}: {reason}")
        log.write("Fix the file(s) above (resolve conflicts / balance parens) and retry.")
        return False
    run_git(["add", "-A"], cfg, log)
    if not git_has_staged_changes(cfg):
        log.write("Nothing to commit (working tree clean)")
        return False
    if not message:
        message = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    run_git(["commit", "-m", message], cfg, log)
    return True

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


# Custom-painted widgets (DropZone, PreviewView) read the shared active theme.
def _tc(key, fallback):
    return ui_theme.tc(key, fallback)


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
    """Launch NETDECK — the clean-slate ui.shell (the only UI now)."""
    from ui.shell import run
    return run()


if __name__ == "__main__":
    main()