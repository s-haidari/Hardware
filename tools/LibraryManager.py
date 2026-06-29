#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad Library Manager - PyQt UI

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
- Optional file watcher (requires 'watchdog')
- Live log panel with scrollbar

Author: You
"""

import os
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
    QHeaderView, QFrame, QScrollArea, QSizePolicy, QSplitter, QStyle,
    QToolButton, QMenu, QProgressBar, QStatusBar, QSlider, QLayout
)
from PyQt5.QtCore import (
    Qt, QTimer, pyqtSignal, QThread, QMimeData, QUrl, QObject, QSettings,
    QRect, QSize, QPoint
)
from PyQt5.QtGui import QPalette, QColor, QBrush, QIcon, QDragEnterEvent, QDropEvent, QPainter, QPen, QFont

# -----------------------------
# Optional watcher (robust handling)
# -----------------------------
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAVE_WATCHDOG = True
except Exception:
    HAVE_WATCHDOG = False

    # Runtime stubs
    class Observer:
        def __init__(self, *_, **__): pass
        def schedule(self, *_, **__): raise RuntimeError("watchdog is not installed")
        def start(self): raise RuntimeError("watchdog is not installed")
        def stop(self): pass
        def join(self, timeout: Optional[float] = None): pass

    class FileSystemEventHandler:
        def __init__(self, *_, **__): pass

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


def resource_path(name: str) -> Path:
    """Locate a bundled resource (e.g. the app icon) in both script and frozen
    (PyInstaller) modes. When frozen, data files live under sys._MEIPASS."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent / name


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


APP_VERSION = "1.1.0"

REPO_ROOT = detect_repo_root()
DEFAULTS: Dict[str, str] = derive_paths(REPO_ROOT)
CONFIG_PATH = REPO_ROOT / "tools" / "config.json"


# -----------------------------
# Utilities / logging
# -----------------------------
def load_config() -> Dict[str, str]:
    # Always start from paths derived from this script's own location, so the
    # app works regardless of which machine/user/clone it runs from. config.json
    # may override Downloads/PythonExe, but only if the override is genuinely
    # writable (a stale path to another user's folder is ignored, not honored).
    cfg = derive_paths(REPO_ROOT)
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            dl = data.get("Downloads")
            if dl and Path(dl).resolve() != Path(cfg["Downloads"]).resolve() and _can_write_dir(Path(dl)):
                cfg["Downloads"] = str(Path(dl))
            if data.get("PythonExe"):
                cfg["PythonExe"] = data["PythonExe"]
    except Exception as e:
        print(f"WARNING: failed to read config.json: {e}")
   
    # Ensure directories exist
    for key in ("RepoRoot", "Downloads", "Libs", "FootprintLib", "ModelLib", "MiscDir"):
        p = Path(cfg[key])
        p.mkdir(parents=True, exist_ok=True)
   
    # Ensure symbol lib exists
    sym_path = Path(cfg["SymbolLib"])
    sym_path.parent.mkdir(parents=True, exist_ok=True)
    if not sym_path.exists():
        sym_path.write_text(
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n',
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
        write_text(target_path, '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n')

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
        if s[i] == "(" and s.startswith("(symbol", i):
            start = i
            j = i
            depth = 0
            while j < n:
                ch = s[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        blocks.append(s[start:j+1])
                        i = j + 1
                        break
                elif ch == '"':
                    j += 1
                    while j < n and s[j] != '"':
                        j += 1
                j += 1
            continue
        elif s[i] == '"':
            i += 1
            while i < n and s[i] != '"':
                i += 1
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
    """Insert blocks just before the top-level closing paren"""
    depth = 0
    last_close = None
    for idx, ch in enumerate(target_text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                last_close = idx
    if last_close is None:
        body = "\n".join(blocks)
        return f'(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n{body}\n)\n'
    return target_text[:last_close] + "\n" + "\n".join(blocks) + "\n" + target_text[last_close:]

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
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n',
            new_blocks
        )
        write_text(symbol_lib_path, new_text)
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
                '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n',
                kept
            )
            write_text(symbol_lib_path, new_text)
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
                '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n',
                kept
            )
            write_text(symbol_lib_path, new_text)
            log.write(f"Deleted {removed} symbol(s).")
        return removed
    except Exception as e:
        log.write(f"ERROR bulk-deleting symbols: {e}")
        return 0


def find_kicad_dir() -> Optional[Path]:
    """Locate KiCad's bin directory (highest version), or None if not installed."""
    env = os.environ.get("KICAD_BIN")
    if env and Path(env).exists():
        return Path(env)
    import glob as _glob
    hits: List[str] = []
    for pat in (r"C:\Program Files\KiCad\*\bin", r"C:\Program Files (x86)\KiCad\*\bin"):
        hits += _glob.glob(pat)
    hits.sort()
    return Path(hits[-1]) if hits else None


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
            '(kicad_symbol_lib (version 20211014) (generator "LibraryManager.py"))\n)\n',
            blocks
        )
        write_text(symbol_lib_path, new_text)
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
            total_blocks.append(b)
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

def process_zip(zip_path: Path, cfg: Dict[str, str], log: UILog, commit: bool = True):
    base = zip_path.stem
    log.write(f"Processing: {base}")
    if not wait_file_ready(zip_path):
        log.write(f"Zip not ready: {zip_path}")
        return
    part_dir = expand_zip_to_folder(zip_path, Path(cfg["Downloads"]), log)
    if part_dir is None:
        return
    move_files(part_dir, cfg, log)
    remove_part_artifacts(zip_path, part_dir, log)
    log.write(f"Done processing {base}")
    # Single-zip path (e.g. the watcher) commits immediately; batch runs skip this
    # and commit once at the end (commit=False).
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
    for i, z in enumerate(zips, 1):
        if progress_cb:
            progress_cb(i, total, z.stem)
        names.append(z.stem)
        process_zip(z, cfg, log, commit=False)   # defer git to one batch commit
    # One commit + one push for the whole batch.
    if names:
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
    folder = QFileDialog.getExistingDirectory(None, "Select extracted part folder", cfg["Downloads"])
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
    """Stage everything and commit. Returns True only if a commit was made."""
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
if HAVE_WATCHDOG:
    class ZipHandler(FileSystemEventHandler):
        def __init__(self, cfg: Dict[str, str], log: UILog):
            super().__init__()
            self.cfg = cfg
            self.log = log

        def on_created(self, event):
            if event.is_directory:
                return
            p = Path(event.src_path)
            if p.suffix.lower() == ".zip":
                process_zip(p, self.cfg, self.log)

        def on_modified(self, event):
            if event.is_directory:
                return
            p = Path(event.src_path)
            if p.suffix.lower() == ".zip":
                process_zip(p, self.cfg, self.log)
else:
    class ZipHandler:
        def __init__(self, *_, **__):
            raise RuntimeError("Watcher unavailable: install 'watchdog' (pip install watchdog)")

class WatchController:
    def __init__(self, cfg: Dict[str, str], log: UILog):
        self.cfg = cfg
        self.log = log
        self.observer: Optional[Observer] = None

    def start(self):
        if not HAVE_WATCHDOG:
            self.log.write("Watcher unavailable: install 'watchdog' (pip install watchdog)")
            return
        if self.observer:
            self.stop()
        handler = ZipHandler(self.cfg, self.log)
        self.observer = Observer()
        self.observer.schedule(handler, self.cfg["Downloads"], recursive=False)
        self.observer.start()
        self.log.write("Watcher started")

    def stop(self):
        if self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=3)
            except Exception:
                pass
            self.observer = None
        self.log.write("Watcher stopped")


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

    # Footprints
    fp_dir = Path(cfg["FootprintLib"])
    if fp_dir.exists():
        for p in sorted(fp_dir.glob("*.kicad_mod")):
            rows.append({"type": "Footprint", "name": p.stem, "path": p})

    # Models
    mdl_dir = Path(cfg["ModelLib"])
    if mdl_dir.exists():
        for ext in ("*.step", "*.stp", "*.wrl"):
            for p in sorted(mdl_dir.glob(ext)):
                rows.append({"type": "Model", "name": p.name, "path": p})

    # Symbols. sym_index is the block's position in the file, so a single
    # duplicate can be removed without disturbing its identically-named twin.
    sym_path = Path(cfg["SymbolLib"])
    if sym_path.exists():
        try:
            text = read_text(sym_path)
            blocks = extract_symbol_blocks(text)
            for i, b in enumerate(blocks):
                nm = extract_symbol_name(b)
                rows.append({"type": "Symbol", "name": nm, "path": sym_path, "sym_index": i})
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
class DropZone(QFrame):
    """Custom widget that accepts drag-and-drop of ZIP files"""
    files_dropped = pyqtSignal(list)  # emits list of Path objects
   
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        # Container with a dashed box that contains the label and checkbox
        self.setMinimumHeight(88)
        self.setFrameStyle(QFrame.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Dashed area for dropping files (contains inner layout)
        self.dash_box = QFrame()
        self.dash_box.setObjectName("dashBox")
        self.dash_box.setAcceptDrops(True)
        self.dash_box.setMinimumHeight(64)
        self.dash_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        inner = QVBoxLayout(self.dash_box)
        inner.setContentsMargins(12, 12, 12, 12)
        inner.setSpacing(6)
        inner.setAlignment(Qt.AlignCenter)

        # Label inside dashed box
        self.label = QLabel("Drop ZIP Files Here")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 10pt; color: #606060; background: transparent;")
        inner.addStretch()
        inner.addWidget(self.label)

        # Checkbox inside dashed box for process-on-drop
        self.chk_process = QCheckBox("Process on drop")
        self.chk_process.setChecked(True)
        inner.addWidget(self.chk_process)
        inner.addStretch()

        layout.addWidget(self.dash_box)

        # Styles for dashed box (will be used/changed on hover)
        self.default_style = "QFrame#dashBox { border: 1px dashed #9a9a9a; border-radius: 6px; background: transparent; }"
        self.hover_style = "QFrame#dashBox { border: 1px dashed #808080; border-radius: 6px; background: #f7f7f7; }"
        self.dash_box.setStyleSheet(self.default_style)

        # Hook dash_box event handlers to forward to parent signals
        def _dash_dragEnterEvent(event):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                self.dash_box.setStyleSheet(self.hover_style)

        def _dash_dragLeaveEvent(event):
            self.dash_box.setStyleSheet(self.default_style)

        def _dash_dropEvent(event):
            self.dash_box.setStyleSheet(self.default_style)
            files = []
            for url in event.mimeData().urls():
                path = Path(url.toLocalFile())
                if path.suffix.lower() == ".zip":
                    files.append(path)
            if files:
                self.files_dropped.emit(files)
            event.acceptProposedAction()

        self.dash_box.dragEnterEvent = _dash_dragEnterEvent
        self.dash_box.dragLeaveEvent = _dash_dragLeaveEvent
        self.dash_box.dropEvent = _dash_dropEvent
   
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.dash_box.setStyleSheet(self.hover_style)
   
    def dragLeaveEvent(self, event):
        self.dash_box.setStyleSheet(self.default_style)
   
    def dropEvent(self, event: QDropEvent):
        self.dash_box.setStyleSheet(self.default_style)
        files = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".zip":
                files.append(path)
        if files:
            self.files_dropped.emit(files)
        event.acceptProposedAction()


# -----------------------------
# Card-like container for modern UI sections
class CardWidget(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setObjectName("cardTitle")
        f = self.title_lbl.font()
        f.setPointSize(9)
        f.setBold(True)
        self.title_lbl.setFont(f)
        # Title area: label on left, optional widget on right (for tab bars etc.)
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        if title:
            title_layout.addWidget(self.title_lbl)
        else:
            # hide label area when title empty, keeping layout for right-side widgets
            self.title_lbl.setVisible(False)
            title_layout.addWidget(self.title_lbl)
        title_layout.addStretch()
        # container for right-side title widgets
        self._title_right = QWidget()
        self._title_right_layout = QHBoxLayout(self._title_right)
        self._title_right_layout.setContentsMargins(0, 0, 0, 0)
        self._title_right_layout.setSpacing(0)
        title_layout.addWidget(self._title_right)
        outer.addWidget(title_container)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 6, 8, 8)
        self.content_layout.setSpacing(6)
        outer.addWidget(self.content)

    def contentLayout(self):
        return self.content_layout

    def set_title_widget(self, widget: QWidget):
        """Place a widget on the right side of the title area (e.g. tab bar)."""
        # remove existing widgets
        for i in reversed(range(self._title_right_layout.count())):
            item = self._title_right_layout.takeAt(i)
            w = item.widget()
            if w:
                w.setParent(None)
        self._title_right_layout.addWidget(widget)

# -----------------------------
# Flow layout (wraps its widgets to new rows as width shrinks)
# -----------------------------
class FlowLayout(QLayout):
    """A layout that arranges children left-to-right and wraps to the next row
    when it runs out of width — so a row of buttons never gets clipped."""
    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        spacing = self.spacing()
        for item in self._items:
            w, h = item.sizeHint().width(), item.sizeHint().height()
            next_x = x + w + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + w + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height - rect.y()


# -----------------------------
# Main Window
# -----------------------------
class LibraryManagerWindow(QMainWindow):
    # Signals used for thread-safe logging and refresh
    log_signal = pyqtSignal(str)
    pull_done = pyqtSignal()
    commits_signal = pyqtSignal(list)
    # UI feedback / async plumbing
    progress_signal = pyqtSignal(int, int, str)   # done, total, name (from workers)
    branch_signal = pyqtSignal(str)               # branch + ahead/behind text
    _async_finished = pyqtSignal(object)          # carries a callable to run on GUI thread

    def __init__(self, cfg: Dict[str, str]):
        super().__init__()
        self.cfg = cfg
        self.rows = []
        self.summary = {}
        self.process_on_drop = True
        self._busy = False
        self._branch_text_val = ""
        self._closing = False
        self._workers = []   # tracked background threads (joined on close)

        self.setWindowTitle("KiCAD Manager")
        self.setMinimumSize(1040, 680)
        _icon = resource_path("app_icon.ico")
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))

        # Central widget with main layout. WA_TranslucentBackground lets the
        # root background show the desktop; the opacity slider tunes only that
        # background's alpha (cards/text stay fully opaque).
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        central = QWidget()
        central.setObjectName("rootCentral")
        self.central = central
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)  # consistent spacing between sections
        main_layout.setContentsMargins(12, 12, 12, 12)

        # --- Header bar (title + live branch/activity status) ---
        main_layout.addWidget(self.create_header_bar())

        # --- Drop Zone ---
        drop_group = self.create_drop_zone()
        main_layout.addWidget(drop_group)

        # (Log panel will be created later so we can embed the Activity tab into its title)

        # --- Central splitters for robust resizing ---
        central_splitter = QSplitter(Qt.Horizontal)
        central_splitter.setHandleWidth(6)

        # Left column: Workflow (now contains advanced dropdown)
        workflow = self.create_workflow()
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(workflow)
        left_panel.setMinimumWidth(320)
        central_splitter.addWidget(left_panel)

        # Middle column: Contents
        library = self.create_library_panel()
        library.setMinimumWidth(300)
        central_splitter.addWidget(library)

        # Right column: Log (with embedded Log/Activity tab selector in its title)
        log_card = self.create_log_panel()
        log_card.setMinimumWidth(300)
        central_splitter.addWidget(log_card)

        # Connect cross-thread signals now that the log widget exists
        self.log_signal.connect(self.log.write)
        self.pull_done.connect(self.refresh_library)
        # Also refresh commits + branch status after pull completes
        self.pull_done.connect(self.refresh_commits)
        self.pull_done.connect(self.update_branch_status)

        # Initialize watcher (needs log)
        self.watcher = WatchController(self.cfg, self.log)

        # Prevent collapsing of any of the three columns
        try:
            central_splitter.setCollapsible(0, False)
            central_splitter.setCollapsible(1, False)
            central_splitter.setCollapsible(2, False)
        except Exception:
            pass

        # connect commits update signal to UI updater
        self.commits_signal.connect(self.update_commits_list)

        # Async / feedback wiring
        self.progress_signal.connect(self.set_progress)
        self.branch_signal.connect(self._on_branch_text)
        self._async_finished.connect(lambda fn: fn())

        central_splitter.setStretchFactor(0, 0)
        central_splitter.setStretchFactor(1, 1)
        central_splitter.setStretchFactor(2, 1)
        main_layout.addWidget(central_splitter)

        # --- Status bar (operation text + progress + result chip) ---
        self.build_status_bar()

        # View settings (theme + background opacity + geometry) persisted across runs
        self._settings = QSettings("KiCadLibraryManager", "KiCadLibraryManager")
        theme = self._settings.value("theme", "dark")
        try:
            self._opacity_pct = int(self._settings.value("opacity", 100))
        except (TypeError, ValueError):
            self._opacity_pct = 100
        self._opacity_pct = max(30, min(100, self._opacity_pct))
        self._apply_theme(str(theme).lower() != "light")   # restyles with current alpha
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(self._opacity_pct)
        self.opacity_slider.blockSignals(False)
        self.opacity_value_lbl.setText(f"{self._opacity_pct}%")
        geo = self._settings.value("geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        self.set_idle()
        self.update_branch_status()

        # Start an initial background pull shortly after UI shows
        QTimer.singleShot(250, self.start_initial_pull)

        # Initial library scan (will run after pull completes via refresh)
        self.refresh_library()

        self.log.write("UI started")
        # Auto-pull every 5 minutes
        self.auto_pull_timer = QTimer(self)
        self.auto_pull_timer.setInterval(300000)  # 5 minutes
        self.auto_pull_timer.timeout.connect(self._periodic_pull)
        self.auto_pull_timer.start()
   
    def create_header(self) -> CardWidget:
        """Create header with repo info"""
        card = CardWidget("")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = card.contentLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Header is intentionally minimal; control buttons moved into the Workflow area
        h = QHBoxLayout()
        h.addStretch()
        layout.addLayout(h)

        return card

    # -------------------------------------------------------------------
    # Header bar + status bar + feedback
    # -------------------------------------------------------------------
    def create_header_bar(self) -> QWidget:
        """Top strip: app title on the left, live branch + activity on the right."""
        bar = QFrame()
        bar.setObjectName("headerBar")
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 14, 8)
        h.setSpacing(8)

        title = QLabel("KiCAD Manager")
        title.setObjectName("appTitle")
        h.addWidget(title)
        h.addStretch()

        # Inline view controls (built straight into the top bar)
        self.theme_btn = QToolButton()
        self.theme_btn.setObjectName("iconBtn")
        self.theme_btn.setText("☾")            # set per-theme in _apply_theme
        self.theme_btn.setToolTip("Toggle light / dark")
        self.theme_btn.clicked.connect(self.toggle_theme)
        h.addWidget(self.theme_btn)

        opac_lbl = QLabel("Opacity")
        opac_lbl.setObjectName("headerStatus")
        h.addWidget(opac_lbl)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setFixedWidth(110)
        self.opacity_slider.setToolTip("Window background transparency")
        self.opacity_slider.valueChanged.connect(self.set_bg_opacity)
        h.addWidget(self.opacity_slider)
        self.opacity_value_lbl = QLabel("100%")
        self.opacity_value_lbl.setObjectName("headerStatus")
        self.opacity_value_lbl.setFixedWidth(38)
        h.addWidget(self.opacity_value_lbl)

        self.about_btn = QToolButton()
        self.about_btn.setText("?")
        self.about_btn.setToolTip("About")
        self.about_btn.clicked.connect(self.show_about)
        h.addWidget(self.about_btn)

        # Separator-ish spacing, then live status indicators on the far right
        h.addSpacing(8)
        self.branch_label = QLabel("")
        self.branch_label.setObjectName("branchChip")
        h.addWidget(self.branch_label)
        self.activity_dot = QLabel("●")   # ●
        self.activity_dot.setObjectName("activityDot")
        self.header_status = QLabel("Idle")
        self.header_status.setObjectName("headerStatus")
        h.addWidget(self.activity_dot)
        h.addWidget(self.header_status)
        return bar

    def build_status_bar(self):
        """Bottom status bar: operation text, progress bar, last-result chip."""
        sb = QStatusBar()
        self.setStatusBar(sb)

        self.status_label = QLabel("Idle")
        sb.addWidget(self.status_label, 1)

        self.progress = QProgressBar()
        self.progress.setObjectName("opProgress")
        self.progress.setMaximumWidth(220)
        self.progress.setMaximumHeight(14)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        sb.addPermanentWidget(self.progress)

        self.result_chip = QLabel("")
        self.result_chip.setObjectName("resultChip")
        sb.addPermanentWidget(self.result_chip)

    def set_busy(self, msg: str):
        """Enter a busy state (call on the GUI thread)."""
        self._busy = True
        self.status_label.setText(msg)
        self.header_status.setText(msg)
        self.activity_dot.setStyleSheet("color: %s;" % self._theme["FG"])   # high-contrast = working
        self.result_chip.setText("")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)   # indeterminate until a count arrives

    def set_progress(self, done: int, total: int, name: str):
        """Determinate progress update (GUI-thread slot for progress_signal)."""
        self.progress.setVisible(True)
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)
        self.status_label.setText(f"{name}  ({done}/{total})")

    def set_idle(self, result: Optional[str] = None, ok: bool = True):
        """Return to idle (call on the GUI thread)."""
        self._busy = False
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.status_label.setText("Idle")
        self.activity_dot.setStyleSheet("color: %s;" % self._theme["DOT_IDLE"])   # dim = idle
        self.header_status.setText(self._branch_text_val or "Idle")
        if result:
            self.result_chip.setText(result)
            self.result_chip.setStyleSheet(
                "color: %s;" % (self._theme["FG"] if ok else "#d9534f")
            )

    def _on_branch_text(self, txt: str):
        self._branch_text_val = txt
        self.branch_label.setText(txt)
        if not self._busy:
            self.header_status.setText(txt or "Idle")

    def _emit(self, signal, *args):
        """Emit a window signal from a worker thread, unless we're shutting
        down (avoids touching a destroyed C++ object during teardown)."""
        if self._closing:
            return
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def _spawn(self, target):
        """Start a tracked daemon thread so closeEvent can join it cleanly."""
        self._workers = [t for t in self._workers if t.is_alive()]
        th = threading.Thread(target=target, daemon=True)
        self._workers.append(th)
        th.start()
        return th

    def update_branch_status(self):
        """Fetch branch + ahead/behind in the background and update the header."""
        def _work():
            try:
                b = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "rev-parse", "--abbrev-ref", "HEAD"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
                ).stdout.strip()
                counts = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "rev-list", "--left-right", "--count",
                     "origin/main...HEAD"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
                ).stdout.strip()
                behind = ahead = "0"
                parts = counts.split()
                if len(parts) == 2:
                    behind, ahead = parts
                txt = b or "main"
                extras = []
                if ahead not in ("", "0"):
                    extras.append(f"↑{ahead}")   # ↑ahead
                if behind not in ("", "0"):
                    extras.append(f"↓{behind}")  # ↓behind
                if extras:
                    txt += "   " + " ".join(extras)
                self._emit(self.branch_signal, txt)
            except Exception:
                self._emit(self.branch_signal, "")
        self._spawn(_work)

    def run_async(self, fn, busy_msg: str, success_msg: Optional[str] = None,
                  refresh: bool = False):
        """Run blocking work (git/processing) off the GUI thread so the window
        stays responsive. UI updates happen on the GUI thread via signals."""
        self.set_busy(busy_msg)

        def _work():
            ok = True
            try:
                fn()
            except Exception as e:
                ok = False
                self._emit(self.log_signal, f"ERROR: {e}")

            def _finish():
                self.set_idle(success_msg if ok else "Error - see log", ok)
                self.update_branch_status()
                if refresh:
                    self.refresh_library()
                    self.refresh_commits()
            self._emit(self._async_finished, _finish)

        self._spawn(_work)

    # ----- async action handlers (dialogs run here on the GUI thread) -----
    def do_pull(self):
        self.run_async(lambda: git_pull(self.cfg, self.log),
                       "Pulling…", "Pulled ✓", refresh=True)

    def do_push(self):
        self.run_async(lambda: git_push(self.cfg, self.log),
                       "Pushing…", "Pushed ✓")

    def do_stage_commit(self):
        self.run_async(lambda: git_stage_commit(self.cfg, self.log),
                       "Committing…", "Committed ✓", refresh=True)

    def do_process_zips(self):
        def work():
            process_existing_zips(
                self.cfg, self.log, refresh_cb=None,
                progress_cb=lambda d, t, n: self._emit(self.progress_signal, d, t, n)
            )
        self.run_async(work, "Processing ZIPs…", "Processed ✓", refresh=True)

    def do_commit_push(self):
        default = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
        msg, ok = QInputDialog.getText(self, "Commit Message", "Enter commit message:", text=default)
        if not ok:
            self.log.write("Commit: canceled by user")
            return
        message = msg.strip() or default

        def work():
            if git_stage_commit(self.cfg, self.log, message=message):
                git_push(self.cfg, self.log)
            else:
                self.log.write("Push skipped: nothing was committed")
        self.run_async(work, "Commit & Push…", "Pushed ✓", refresh=True)

    def do_process_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select extracted part folder", self.cfg["Downloads"])
        if not folder:
            return
        folder_path = Path(folder)

        def work():
            self.log.write(f"Manual process folder: {folder_path}")
            move_files(folder_path, self.cfg, self.log)
            self.log.write("Done manual processing")
            if git_stage_commit(self.cfg, self.log,
                                message=f"Auto-update: processed folder {folder_path.name}"):
                git_push(self.cfg, self.log)
        self.run_async(work, "Processing folder…", "Processed ✓", refresh=True)

    def change_path(self, key: str, btn: QPushButton):
        """Allow user to change a configured path (RepoRoot or Downloads)"""
        start = self.cfg.get(key, DEFAULTS.get(key, ""))
        new = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if not new:
            return
        new_path = Path(new)
        if not _can_write_dir(new_path):
            QMessageBox.warning(self, "Change Folder", f"Folder is not writable:\n{new_path}")
            return
        if key == 'RepoRoot':
            # Re-derive every path from the new root so the whole app stays consistent.
            self.cfg = derive_paths(new_path)
        else:
            self.cfg[key] = str(new_path)
        save_config(self.cfg)
        # Ensure directory exists
        Path(self.cfg[key]).mkdir(parents=True, exist_ok=True)
        # Keep button label short; full path shown in the menu and tooltip
        btn.setText("Root" if key == 'RepoRoot' else "Downloads")
        btn.setToolTip(self.cfg[key])
        # Update menu path displays if present
        if hasattr(self, 'repo_path_action'):
            self.repo_path_action.setText(self.cfg['RepoRoot'])
        if hasattr(self, 'dl_path_action'):
            self.dl_path_action.setText(self.cfg['Downloads'])
        # Reflect new paths in the library view
        if key == 'RepoRoot':
            self.refresh_library()

    def start_initial_pull(self):
        """Start a background thread to pull latest from GitHub and refresh library."""
        def _pull():
            try:
                self.log_signal.emit("Auto-pull: fetching latest from GitHub...")

                # Run git commands and forward output via signal (avoid calling UI from this thread)
                
                def _run_git(args):
                    try:
                        proc = run_hidden(
                            ["git", "-C", self.cfg["RepoRoot"], "pull", "--ff-only"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8"
                        )
                        out = proc.stdout or ""
                        for line in out.splitlines():
                            self.log_signal.emit(line)
                        if proc.returncode != 0:
                            self.log_signal.emit(
                                f"Startup pull: non-FF or error (exit {proc.returncode}). "
                                f"Local state kept; no rebase performed."
                            )
                        else:
                            self.log_signal.emit("Auto-pull: finished")
                            self.pull_done.emit()
                    except FileNotFoundError:
                        self.log_signal.emit("ERROR: git not found on PATH. Install Git and retry.")
                    except Exception as e:
                        self.log_signal.emit(f"ERROR running git pull: {e}")
                    

                _run_git(["pull", "--ff-only"])
            except Exception as e:
                self.log_signal.emit(f"Auto-pull failed: {e}")

        self._spawn(_pull)
    def _periodic_pull(self):
        def _pull():
            try:
                self.log_signal.emit("Periodic auto-pull: checking for updates...")
                proc = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "pull", "--ff-only"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8"
                )
                out = proc.stdout or ""
                for line in out.splitlines():
                    self.log_signal.emit(line)
                if proc.returncode != 0:
                    self.log_signal.emit(
                        f"Periodic pull: non-FF or error (exit {proc.returncode}). "
                        f"Local state kept; no rebase performed."
                    )
                else:
                    self.log_signal.emit("Periodic auto-pull: up to date.")
                    self.pull_done.emit()
            except Exception as e:
                self.log_signal.emit(f"Periodic auto-pull failed: {e}")
        self._spawn(_pull)
    def create_drop_zone(self) -> CardWidget:
        """Create drag-and-drop zone"""
        card = CardWidget("Drop Zone")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = card.contentLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # Custom drop zone widget
        self.drop_zone = DropZone()
        self.drop_zone.setMinimumHeight(56)
        self.drop_zone.setMaximumHeight(96)
        self.drop_zone.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.drop_zone.files_dropped.connect(self.handle_dropped_files)
        layout.addWidget(self.drop_zone)

        # Wire the drop zone's internal checkbox to window state
        try:
            self.drop_zone.chk_process.stateChanged.connect(lambda s: setattr(self, 'process_on_drop', bool(s)))
            # initialize state from the widget
            self.process_on_drop = bool(self.drop_zone.chk_process.isChecked())
        except Exception:
            # Fallback: keep existing default
            pass
        return card
   
    def create_workflow(self) -> CardWidget:
        """Create workflow buttons (Step 0-4)"""
        card = CardWidget("Workflow")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # Make workflow taller so step buttons are prominent
        card.setMinimumHeight(360)
        layout = card.contentLayout()
        layout.setSpacing(6)
        layout.setContentsMargins(4, 6, 4, 6)

        # Button style used for workflow and advanced button
        btn_style = """
            QPushButton {
                text-align: left;
                padding: 6px 10px;
                font-size: 9pt;
            }
        """

        st = self.style()

        # Advanced dropdown placed above the step buttons; full-width and sized like the steps
        adv_menu = QMenu()
        adv_btn = QPushButton("Advanced")
        adv_btn.setIcon(st.standardIcon(QStyle.SP_FileDialogDetailedView))
        adv_btn.setMaximumHeight(34)
        adv_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        adv_btn.setStyleSheet(btn_style)
        adv_actions = [
            ("Pull", self.do_pull),
            ("Push", self.do_push),
            ("Stage and Commit", self.do_stage_commit),
            ("Process Folder", self.do_process_folder),
            ("Start Watcher", lambda: self.watcher.start()),
            ("Stop Watcher", lambda: self.watcher.stop()),
            ("Open Libraries", lambda: os.startfile(self.cfg["Libs"])),
            ("Open Log File", lambda: os.startfile(self.cfg["LogFile"])),
        ]
        for label, cb in adv_actions:
            a = adv_menu.addAction(label)
            a.triggered.connect(lambda checked=False, fn=cb: fn())
        # Attach menu to QPushButton and show on click
        adv_btn.setMenu(adv_menu)
        adv_btn.clicked.connect(adv_btn.showMenu)
        layout.addWidget(adv_btn)

        # KiCad project tools (rename / net classes / project settings)
        tools_btn = QPushButton("KiCad Tools")
        tools_btn.setIcon(st.standardIcon(QStyle.SP_FileDialogDetailedView))
        tools_btn.setStyleSheet(btn_style)
        tools_btn.setMaximumHeight(34)
        tools_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tools_btn.clicked.connect(self.open_kicad_tools)
        layout.addWidget(tools_btn)

        # Full step labels with clear descriptions and icons (all uniform style)
        buttons = [
            ("Step 0: Pull (Fast-Forward)", self.do_pull, QStyle.SP_ArrowDown),
            ("Step 1: Open Downloads", lambda: os.startfile(self.cfg["Downloads"]), QStyle.SP_DirOpenIcon),
            ("Step 2: Process ZIPs", self.do_process_zips, QStyle.SP_MediaPlay),
            ("Step 3: Clean Leftovers", lambda: clean_leftovers(self.cfg, self.log, self.refresh_library), QStyle.SP_TrashIcon),
            ("Step 4: Stage, Commit, Push", self.do_commit_push, QStyle.SP_ArrowUp),
        ]

        for text, callback, icon in buttons:
            btn = QPushButton(text)
            btn.setIcon(st.standardIcon(icon))
            btn.setStyleSheet(btn_style)
            btn.setMaximumHeight(36)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(callback)
            layout.addWidget(btn)

        # Root / Downloads buttons placed under the step buttons as requested
        row = QHBoxLayout()
        row.setSpacing(6)
        # Repo root button
        self.repo_btn = QPushButton("Root")
        self.repo_btn.setMaximumHeight(28)
        self.repo_btn.setStyleSheet(btn_style)
        self.repo_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        repo_menu = QMenu(self.repo_btn)
        self.repo_path_action = repo_menu.addAction(self.cfg.get('RepoRoot', ''))
        self.repo_path_action.setEnabled(False)
        repo_menu.addSeparator()
        repo_menu.addAction("Open").triggered.connect(lambda: os.startfile(self.cfg['RepoRoot']))
        repo_menu.addAction("Change").triggered.connect(lambda: self.change_path('RepoRoot', self.repo_btn))
        self.repo_btn.setMenu(repo_menu)
        self.repo_btn.clicked.connect(self.repo_btn.showMenu)
        self.repo_btn.setToolTip(self.cfg['RepoRoot'])
        row.addWidget(self.repo_btn)

        # Downloads button
        self.dl_btn = QPushButton("Downloads")
        self.dl_btn.setMaximumHeight(28)
        self.dl_btn.setStyleSheet(btn_style)
        self.dl_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        dl_menu = QMenu(self.dl_btn)
        self.dl_path_action = dl_menu.addAction(self.cfg.get('Downloads', ''))
        self.dl_path_action.setEnabled(False)
        dl_menu.addSeparator()
        dl_menu.addAction("Open").triggered.connect(lambda: os.startfile(self.cfg['Downloads']))
        dl_menu.addAction("Change").triggered.connect(lambda: self.change_path('Downloads', self.dl_btn))
        self.dl_btn.setMenu(dl_menu)
        self.dl_btn.clicked.connect(self.dl_btn.showMenu)
        self.dl_btn.setToolTip(self.cfg['Downloads'])
        row.addWidget(self.dl_btn)

        layout.addLayout(row)

        layout.addStretch()
        return card
   
    def create_library_panel(self) -> CardWidget:
        """Create library contents panel with filter/search"""
        group = CardWidget("Contents")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = group.contentLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
       
        # Filter block (Format above Search)
        filter_block = QVBoxLayout()

        # Format row
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        # Checkbox dropdown for multi-select formats
        self.format_btn = QToolButton()
        self.format_btn.setText("All")
        self.format_btn.setPopupMode(QToolButton.InstantPopup)
        self.format_menu = QMenu(self.format_btn)
        # 'All' action - non-checkable, toggles selection when clicked
        self.format_all_action = self.format_menu.addAction("All")
        self.format_all_action.triggered.connect(lambda: self.on_format_all_clicked())
        self.format_menu.addSeparator()
        self.format_checks = {}
        for label in ["Symbol", "Footprint", "Model"]:
            act = self.format_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(True)
            act.triggered.connect(lambda checked, lbl=label: self.on_format_toggled(lbl, checked))
            self.format_checks[label] = act
        self.format_btn.setMenu(self.format_menu)
        self.format_btn.setMaximumHeight(24)
        # Make format button text small and non-bold so labels like '2 selected' fit
        self.format_btn.setStyleSheet("font-weight: 400; font-size: 8pt; padding: 2px 6px;")
        self.format_btn.setMinimumWidth(100)
        self.format_btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        fmt_row.addWidget(self.format_btn)
        fmt_row.addStretch()
        filter_block.addLayout(fmt_row)

        # Search row
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self.on_filter_change)
        self.search_edit.setMaximumHeight(24)
        search_row.addWidget(self.search_edit)
        filter_block.addLayout(search_row)

        # Duplicates-only toggle
        self.chk_dupes = QCheckBox("Duplicates only")
        self.chk_dupes.setChecked(False)
        self.chk_dupes.setToolTip("Show only entries that have more than one copy")
        self.chk_dupes.stateChanged.connect(self.on_filter_change)
        filter_block.addWidget(self.chk_dupes)

        # Initialize filters state
        self.format_filters = set(self.format_checks.keys())
        self.update_format_btn_text()

        layout.addLayout(filter_block)
       
        # Summary label
        self.lbl_summary = QLabel("Library: 0 items")
        summary_font = self.lbl_summary.font()
        summary_font.setPointSize(8)
        self.lbl_summary.setFont(summary_font)
        layout.addWidget(self.lbl_summary)
       
        # Action buttons — a flow layout so they wrap (never clip) when narrow
        btn_layout = FlowLayout(spacing=6)
        st = self.style()
        actions = [
            ("Refresh", QStyle.SP_BrowserReload, self.refresh_library),
            ("Open", QStyle.SP_DirOpenIcon, self.on_tree_open),
            ("Open in KiCad", QStyle.SP_FileDialogContentsView, self.open_in_kicad),
            ("Delete", QStyle.SP_TrashIcon, self.on_tree_delete),
            ("Remove Duplicates", QStyle.SP_DialogResetButton, self.on_remove_duplicates),
        ]
        for label, icon, cb in actions:
            b = QPushButton(label)
            b.setIcon(st.standardIcon(icon))
            b.setMaximumHeight(26)
            b.clicked.connect(cb)
            btn_layout.addWidget(b)
        layout.addLayout(btn_layout)

        # Tree widget (multi-select)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Format", "Name", "Location"])
        self.tree.setColumnWidth(0, 130)
        self.tree.setColumnWidth(1, 260)
        self.tree.setColumnWidth(2, 420)
        self.tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setIndentation(10)
        # Prefer sizing Type/Name to contents and let Location stretch
        header = self.tree.header()
        try:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
        except Exception:
            self.tree.header().setStretchLastSection(True)

        self.tree.itemDoubleClicked.connect(self.open_in_kicad)
        layout.addWidget(self.tree)

        return group
   
    
   
    def create_log_panel(self) -> CardWidget:
        """Create log panel"""
        # Use empty title so we can place the Log/Activity tab bar in the title area
        group = CardWidget("")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = group.contentLayout()
        layout.setContentsMargins(8, 6, 8, 6)

        # Stacked area: index 0 = log text; index 1 = commits/activity
        stack = QStackedWidget()

        # Log text widget
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(120)
        self.txt_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        stack.addWidget(self.txt_log)

        # Activity widget (commits) — created as a plain widget
        commits_widget = self.create_git_panel()
        stack.addWidget(commits_widget)

        layout.addWidget(stack)

        # Tab selector placed into the title area of the card
        tabbar = QTabBar()
        tabbar.addTab("Log")
        tabbar.addTab("Activity")
        # Make tabs expand equally so they appear symmetric
        tabbar.setExpanding(True)
        tabbar.setDrawBase(False)
        tabbar.setCurrentIndex(0)
        tabbar.currentChanged.connect(lambda i: stack.setCurrentIndex(i))
        # Name the tabbar so theme styles can target it to match pane headers
        tabbar.setObjectName("cardTabBar")
        # Match the card title font: bold, slightly larger for header-like appearance
        fa = tabbar.font()
        fa.setPointSize(10)
        fa.setBold(True)
        tabbar.setFont(fa)
        tabbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        group.set_title_widget(tabbar)

        # Initialize logger
        self.log = UILog(self.txt_log, Path(self.cfg["LogFile"]))

        return group

    def create_git_panel(self) -> QWidget:
        """Create a QWidget containing commits list and controls."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Commits:"))
        top.addStretch()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setMaximumHeight(28)
        btn_refresh.clicked.connect(lambda: self.refresh_commits())
        top.addWidget(btn_refresh)
        self.btn_open_github = QPushButton("Open on GitHub")
        self.btn_open_github.setMaximumHeight(28)
        self.btn_open_github.clicked.connect(lambda: self.open_selected_commit_on_github())
        top.addWidget(self.btn_open_github)
        self.btn_diff = QPushButton("Diff")
        self.btn_diff.setMaximumHeight(28)
        self.btn_diff.clicked.connect(lambda: self.show_selected_commit_diff())
        top.addWidget(self.btn_diff)
        self.btn_checkout = QPushButton("Checkout")
        self.btn_checkout.setMaximumHeight(28)
        self.btn_checkout.clicked.connect(lambda: self.checkout_selected_commit())
        top.addWidget(self.btn_checkout)
        layout.addLayout(top)

        self.commits_list = QListWidget()
        self.commits_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.commits_list.itemDoubleClicked.connect(self.on_commit_double_click)
        # Initially disable action buttons until a commit is selected
        for b in (getattr(self, 'btn_open_github', None), getattr(self, 'btn_diff', None), getattr(self, 'btn_checkout', None)):
            if b is not None:
                b.setEnabled(False)
        self.commits_list.itemSelectionChanged.connect(self._on_commit_selection_changed)
        layout.addWidget(self.commits_list)

        return widget

    def refresh_commits(self):
        """Fetch recent commits from git in a background thread and emit results."""
        def _run():
            try:
                proc = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "log", "--pretty=format:%h%x09%ad%x09%an%x09%s", "--date=short", "-n", "50"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8"
                )
                out = proc.stdout or ""
                commits = []
                for line in out.splitlines():
                    parts = line.split("\t", 3)
                    if len(parts) >= 4:
                        h, date, author, subject = parts
                    elif len(parts) == 3:
                        h, date, author = parts
                        subject = ""
                    elif len(parts) == 2:
                        h, date = parts
                        author = subject = ""
                    else:
                        h = line
                        date = author = subject = ""
                    commits.append({"hash": h, "date": date, "author": author, "subject": subject})
                self.commits_signal.emit(commits)
            except FileNotFoundError:
                self.log_signal.emit("ERROR: git not found on PATH. Install Git and retry.")
                self.commits_signal.emit([])
            except Exception as e:
                self.log_signal.emit(f"ERROR refreshing commits: {e}")
                self.commits_signal.emit([])
        self._spawn(_run)

    def _on_commit_selection_changed(self):
        has = bool(self.commits_list.currentItem())
        for attr in ('btn_open_github', 'btn_diff', 'btn_checkout'):
            b = getattr(self, attr, None)
            if b is not None:
                b.setEnabled(has)

    def update_commits_list(self, commits: list):
        """Update QListWidget with commit entries (runs in main thread via signal)."""
        self.commits_list.clear()
        for c in commits:
            text = f"{c.get('hash','')}  {c.get('date','')}  {c.get('author','')}  - {c.get('subject','')}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, c)
            self.commits_list.addItem(item)

    def on_commit_double_click(self, item: QListWidgetItem):
        """Show full commit in the log area when a commit is double-clicked."""
        commit = item.data(Qt.UserRole)
        if not commit:
            return
        h = commit.get('hash')

        def _run_show():
            try:
                proc = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "show", "--pretty=format:%H%n%an%n%ad%n%B", h],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8"
                )
                out = proc.stdout or ""
                for line in out.splitlines():
                    self.log_signal.emit(line)
            except Exception as e:
                self.log_signal.emit(f"ERROR showing commit {h}: {e}")

        self._spawn(_run_show)

    def get_selected_commit_hash(self) -> Optional[str]:
        it = self.commits_list.currentItem()
        if not it:
            return None
        data = it.data(Qt.UserRole)
        if isinstance(data, dict):
            return data.get('hash')
        # Fallback: try to parse the text
        text = it.text()
        return text.split()[0] if text else None

    def get_github_repo_url(self) -> Optional[str]:
        try:
            proc = run_hidden(
                ["git", "-C", self.cfg["RepoRoot"], "config", "--get", "remote.origin.url"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8"
            )
            url = (proc.stdout or "").strip()
            if not url:
                return None
            # git@github.com:owner/repo.git -> https://github.com/owner/repo
            if url.startswith("git@github.com:"):
                path = url.split(":", 1)[1]
                if path.endswith('.git'):
                    path = path[:-4]
                return f"https://github.com/{path}"
            # https://github.com/owner/repo.git
            if url.startswith("https://") or url.startswith("http://"):
                if url.endswith('.git'):
                    url = url[:-4]
                return url
            # ssh://git@github.com/owner/repo.git
            if "github.com" in url:
                # attempt to extract owner/repo
                parts = url.split('github.com')[-1].lstrip(':/')
                if parts.endswith('.git'):
                    parts = parts[:-4]
                return f"https://github.com/{parts}"
        except Exception:
            return None

    def open_selected_commit_on_github(self):
        sha = self.get_selected_commit_hash()
        if not sha:
            self.log_signal.emit("No commit selected to open on GitHub")
            return
        base = self.get_github_repo_url()
        if not base:
            self.log_signal.emit("Unable to determine GitHub remote URL")
            return
        url = f"{base}/commit/{sha}"
        webbrowser.open(url)
        self.log_signal.emit(f"Opening {url}")

    def show_selected_commit_diff(self):
        sha = self.get_selected_commit_hash()
        if not sha:
            self.log_signal.emit("No commit selected for diff")
            return
        def _run():
            try:
                proc = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "show", sha],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
                )
                out = proc.stdout or ""
                for line in out.splitlines():
                    self.log_signal.emit(line)
            except Exception as e:
                self.log_signal.emit(f"ERROR showing diff for {sha}: {e}")
        self._spawn(_run)

    def checkout_selected_commit(self):
        sha = self.get_selected_commit_hash()
        if not sha:
            self.log_signal.emit("No commit selected to checkout")
            return
        reply = QMessageBox.question(self, "Checkout commit",
                                     f"Checkout commit {sha}? This will change your working tree.",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        def _run():
            try:
                proc = run_hidden(
                    ["git", "-C", self.cfg["RepoRoot"], "checkout", sha],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
                )
                out = proc.stdout or ""
                for line in out.splitlines():
                    self.log_signal.emit(line)
            except Exception as e:
                self.log_signal.emit(f"ERROR checking out {sha}: {e}")
        self._spawn(_run)
   
    # ------------------------------------------------------------------
    # Theming: one token-based stylesheet drives both dark and light.
    # ------------------------------------------------------------------
    _DARK_COLORS = {
        "WIN_BG": "#1e1e1e", "MAIN_BG": "#1a1a1a", "FG": "#e6e6e6", "FG_DIM": "#b8b8b8",
        "TITLE_FG": "#f4f4f4", "CARD_BG": "#232323", "BORDER": "#2e2e2e",
        "HDR1": "#272727", "HDR2": "#202020", "CHIP_BG": "#2b2b2b", "IN_BG": "#262626",
        "BTN_BG": "#2f2f2f", "BTN_HOVER": "#353535", "BTN_BORDER": "#3f3f3f",
        "ACCENT": "#8a8a8a", "TREE_BG": "#1f1f1f", "TREE_ALT": "#242424",
        "SEL_BG": "#3d3d3d", "SEL_FG": "#f5f5f5", "HOVER_BG": "#2a2a2a",
        "SEC_BG": "#262626", "SEC_FG": "#d8d8d8", "LOG_BG": "#151515", "LOG_FG": "#d6d6d6",
        "SCROLL": "#3a3a3a", "SCROLL_HOVER": "#777777", "ST_BG": "#1a1a1a", "ST_FG": "#b8b8b8",
        "PROG_BG": "#262626", "PROG1": "#8a8a8a", "PROG2": "#b0b0b0",
        "TAB_BG": "#2a2a2a", "TAB_SEL_BG": "#3d3d3d", "TAB_SEL_FG": "#f5f5f5",
        "MENU_BG": "#2a2a2a", "MENU_SEL": "#3a3a3a", "CHK_BG": "#262626", "CHK_ON": "#9a9a9a",
        "DOT_IDLE": "#5a5a5a",
    }
    _LIGHT_COLORS = {
        "WIN_BG": "#f3f3f4", "MAIN_BG": "#e9e9ea", "FG": "#2a2a2a", "FG_DIM": "#5a5a5a",
        "TITLE_FG": "#1a1a1a", "CARD_BG": "#ffffff", "BORDER": "#dcdcdc",
        "HDR1": "#fcfcfc", "HDR2": "#efefef", "CHIP_BG": "#ececec", "IN_BG": "#ffffff",
        "BTN_BG": "#efefef", "BTN_HOVER": "#e6e6e6", "BTN_BORDER": "#cfcfcf",
        "ACCENT": "#888888", "TREE_BG": "#ffffff", "TREE_ALT": "#f5f5f5",
        "SEL_BG": "#d2d2d2", "SEL_FG": "#1a1a1a", "HOVER_BG": "#ececec",
        "SEC_BG": "#efefef", "SEC_FG": "#2a2a2a", "LOG_BG": "#fbfbfb", "LOG_FG": "#303030",
        "SCROLL": "#c4c4c4", "SCROLL_HOVER": "#9a9a9a", "ST_BG": "#e9e9ea", "ST_FG": "#555555",
        "PROG_BG": "#e6e6e6", "PROG1": "#9a9a9a", "PROG2": "#bcbcbc",
        "TAB_BG": "#ececec", "TAB_SEL_BG": "#ffffff", "TAB_SEL_FG": "#111111",
        "MENU_BG": "#ffffff", "MENU_SEL": "#ececec", "CHK_BG": "#ffffff", "CHK_ON": "#888888",
        "DOT_IDLE": "#a0a0a0",
    }
    # Tokens written @@KEY@@ become rgba(...) with the opacity-slider alpha (so
    # backgrounds go translucent); tokens written @KEY@ stay opaque hex (text,
    # borders, selection) — i.e. "everything but the text" fades with the slider.
    _THEME_QSS = """
        QWidget { color: @FG@; font-family: "Segoe UI","Helvetica Neue",Arial,sans-serif; }
        QMainWindow { background: transparent; }
        QWidget#rootCentral { background-color: @@WIN_BG@@; }
        QFrame#headerBar { background: transparent; border: none; }
        QLabel#appTitle { font-size: 13pt; font-weight: 800; color: @TITLE_FG@; }
        QLabel#branchChip { color: @FG_DIM@; font-weight: 600; font-size: 9pt; }
        QLabel#activityDot { color: @DOT_IDLE@; font-size: 12pt; }
        QLabel#headerStatus { color: @FG_DIM@; font-size: 9pt; }
        QToolButton#iconBtn { font-size: 13pt; padding: 0 8px; border: none; background: transparent; color: @FG_DIM@; }
        QToolButton#iconBtn:hover { color: @TITLE_FG@; }
        QFrame#card { border: 1px solid @BORDER@; border-radius: 10px; background-color: @@CARD_BG@@; margin-top: 6px; }
        QLabel#cardTitle { color: @TITLE_FG@; padding: 4px 6px; font-weight: 800; font-size: 10pt; }
        QToolButton { background: transparent; border: 1px solid @BORDER@; border-radius: 6px; padding: 6px 10px; font-weight: 600; }
        QToolButton:hover { border-color: @ACCENT@; }
        QToolButton::menu-indicator { image: none; }
        QMenu { background-color: @@MENU_BG@@; border: 1px solid @BORDER@; padding: 4px; }
        QMenu::item { padding: 6px 18px; border-radius: 4px; }
        QMenu::item:selected { background-color: @@MENU_SEL@@; color: @FG@; }
        QPushButton { background-color: @@BTN_BG@@; color: @FG@; border: 1px solid @BTN_BORDER@; border-radius: 6px; padding: 6px 10px; font-size: 9pt; font-weight: 600; text-align: left; }
        QPushButton:hover { border-color: @ACCENT@; background-color: @@BTN_HOVER@@; }
        QPushButton:pressed { background-color: @@MENU_SEL@@; }
        QPushButton:disabled { color: #888888; border-color: @BORDER@; }
        QPushButton::menu-indicator { image: none; }
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background-color: @@IN_BG@@; border: 1px solid @BORDER@; border-radius: 6px; padding: 5px 8px; color: @FG@; }
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid @ACCENT@; }
        QComboBox QAbstractItemView { background-color: @@MENU_BG@@; color: @FG@; selection-background-color: @SEL_BG@; border: 1px solid @BORDER@; }
        QCheckBox { color: @FG@; spacing: 6px; }
        QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px; border: 1px solid @ACCENT@; background: @@CHK_BG@@; }
        QCheckBox::indicator:checked { background: @CHK_ON@; border-color: @CHK_ON@; }
        QTreeWidget, QTableWidget, QListWidget { background-color: @@TREE_BG@@; border: 1px solid @BORDER@; border-radius: 8px; color: @FG@; alternate-background-color: @@TREE_ALT@@; outline: 0; }
        QTreeWidget::item, QTableWidget::item { padding: 3px 2px; }
        QTreeWidget::item:selected, QTableWidget::item:selected, QListWidget::item:selected { background-color: @SEL_BG@; color: @SEL_FG@; }
        QTreeWidget::item:hover, QListWidget::item:hover { background-color: @HOVER_BG@; }
        QHeaderView::section { background-color: @@SEC_BG@@; color: @SEC_FG@; padding: 6px; border: none; border-right: 1px solid @BORDER@; border-bottom: 1px solid @BORDER@; font-weight: 700; }
        QTextEdit, QPlainTextEdit { background-color: @@LOG_BG@@; border: 1px solid @BORDER@; border-radius: 8px; color: @LOG_FG@; font-family: "Cascadia Mono","Consolas",monospace; font-size: 8pt; }
        QTabWidget::pane { border: 1px solid @BORDER@; border-radius: 8px; top: -1px; }
        QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
        QScrollBar::handle:vertical { background: @SCROLL@; border-radius: 5px; min-height: 24px; }
        QScrollBar::handle:vertical:hover { background: @SCROLL_HOVER@; }
        QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
        QScrollBar::handle:horizontal { background: @SCROLL@; border-radius: 5px; min-width: 24px; }
        QScrollBar::handle:horizontal:hover { background: @SCROLL_HOVER@; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QScrollBar::add-page, QScrollBar::sub-page { background: none; }
        QSlider::groove:horizontal { height: 4px; background: @SCROLL@; border-radius: 2px; }
        QSlider::sub-page:horizontal { background: @SCROLL_HOVER@; border-radius: 2px; }
        QSlider::handle:horizontal { width: 12px; margin: -5px 0; border-radius: 6px; background: @FG_DIM@; }
        QSlider::handle:horizontal:hover { background: @FG@; }
        QStatusBar { background: @@ST_BG@@; border-top: 1px solid @BORDER@; color: @ST_FG@; }
        QStatusBar::item { border: none; }
        QLabel#resultChip { font-weight: 700; padding: 0 8px; }
        QProgressBar#opProgress { background: @@PROG_BG@@; border: 1px solid @BORDER@; border-radius: 7px; }
        QProgressBar#opProgress::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 @PROG1@, stop:1 @PROG2@); border-radius: 6px; }
        QTabBar#cardTabBar { background: transparent; spacing: 6px; }
        QTabBar#cardTabBar::tab { padding: 6px 14px; font-weight: 700; font-size: 10pt; color: @FG_DIM@; border: 1px solid @BORDER@; border-radius: 7px; background: @@TAB_BG@@; margin: 0 4px; min-height: 24px; }
        QTabBar#cardTabBar::tab:hover { background: @@HOVER_BG@@; }
        QTabBar#cardTabBar::tab:selected { color: @TAB_SEL_FG@; background: @@TAB_SEL_BG@@; border-color: @ACCENT@; }
        QTabBar::tab { padding: 6px 12px; color: @FG_DIM@; background: @@TAB_BG@@; border: 1px solid @BORDER@; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; }
        QTabBar::tab:selected { color: @TITLE_FG@; background: @@TAB_SEL_BG@@; }
        QToolTip { background: @MENU_BG@; color: @FG@; border: 1px solid @ACCENT@; padding: 4px; }
    """

    def _theme_palette(self, c):
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(c["WIN_BG"]))
        pal.setColor(QPalette.WindowText, QColor(c["FG"]))
        pal.setColor(QPalette.Base, QColor(c["IN_BG"]))
        pal.setColor(QPalette.AlternateBase, QColor(c["TREE_ALT"]))
        pal.setColor(QPalette.ToolTipBase, QColor(c["MENU_BG"]))
        pal.setColor(QPalette.ToolTipText, QColor(c["FG"]))
        pal.setColor(QPalette.Text, QColor(c["FG"]))
        pal.setColor(QPalette.Button, QColor(c["BTN_BG"]))
        pal.setColor(QPalette.ButtonText, QColor(c["FG"]))
        pal.setColor(QPalette.Highlight, QColor(c["SEL_BG"]))
        pal.setColor(QPalette.HighlightedText, QColor(c["SEL_FG"]))
        return pal

    def _build_qss(self, c: dict, alpha: int) -> str:
        """Render the stylesheet: @@KEY@@ -> rgba with `alpha`, @KEY@ -> hex."""
        qss = self._THEME_QSS
        for k, v in c.items():
            h = v.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            qss = qss.replace("@@" + k + "@@", "rgba(%d,%d,%d,%d)" % (r, g, b, alpha))
            qss = qss.replace("@" + k + "@", v)
        return qss

    def _restyle(self):
        alpha = int(round(max(30, min(100, getattr(self, "_opacity_pct", 100))) / 100.0 * 255))
        self.setStyleSheet(self._build_qss(self._theme, alpha))

    def _apply_theme(self, dark: bool):
        self._is_dark = dark
        self._theme = self._DARK_COLORS if dark else self._LIGHT_COLORS
        self.setPalette(self._theme_palette(self._theme))
        self._restyle()
        if hasattr(self, "central"):
            self.central.setStyleSheet("")   # background now comes from #rootCentral
        # sun in dark mode (click -> light), moon in light mode (click -> dark)
        if hasattr(self, "theme_btn"):
            self.theme_btn.setText("☀" if dark else "☾")
        if hasattr(self, "activity_dot") and not getattr(self, "_busy", False):
            self.activity_dot.setStyleSheet("color: %s;" % self._theme["DOT_IDLE"])
        # repaint duplicate highlights with theme-appropriate colours
        if hasattr(self, "tree") and getattr(self, "rows", None) is not None:
            self.on_filter_change()

    def apply_dark_theme(self):
        self._apply_theme(True)

    def apply_light_theme(self):
        self._apply_theme(False)

    def toggle_theme(self):
        self._apply_theme(not self._is_dark)
        self._save_view_settings()

    def set_bg_opacity(self, pct: int):
        """Slider handler: re-render the stylesheet so every background uses the
        new alpha (text/borders stay opaque). 100% = fully opaque."""
        self._opacity_pct = max(30, min(100, int(pct)))
        if hasattr(self, "opacity_value_lbl"):
            self.opacity_value_lbl.setText(f"{self._opacity_pct}%")
        self._restyle()
        self._save_view_settings()

    def _save_view_settings(self):
        try:
            s = self._settings
            s.setValue("theme", "dark" if self._is_dark else "light")
            s.setValue("opacity", int(getattr(self, "_opacity_pct", 100)))
            s.setValue("geometry", self.saveGeometry())
        except Exception:
            pass

    def show_about(self):
        QMessageBox.about(
            self, "About KiCAD Manager",
            f"<b>KiCAD Manager</b><br>Version {APP_VERSION}<br><br>"
            "Drop vendor ZIPs to merge symbols, footprints and 3D models into the "
            "shared library, with one-click git sync.<br><br>"
            "Includes KiCad project tools: bulk rename, net-class sync, and "
            "project-settings sync.<br><br>Built with PyQt5."
        )

    def open_kicad_tools(self):
        """Open the KiCad project tools (rename / net classes / project settings)."""
        try:
            from kicad_tools import KiCadToolsDialog
        except Exception as e:
            QMessageBox.critical(self, "KiCad Tools", f"Could not load KiCad Tools:\n{e}")
            return
        projects_dir = self._settings.value("projects_dir", "") or str(Path(self.cfg["RepoRoot"]).parent)

        def _save(p):
            try:
                self._settings.setValue("projects_dir", p)
            except Exception:
                pass
        dlg = KiCadToolsDialog(self, projects_dir, save_dir_cb=_save)
        dlg.exec_()
        
   
    def handle_dropped_files(self, files: List[Path]):
        """Handle files dropped into drop zone"""
        copied = []
        for f in files:
            try:
                dst = safe_copy_to_downloads(f, Path(self.cfg["Downloads"]))
                self.log.write(f"Copied to downloads: {dst.name}")
                copied.append(dst)
            except Exception as e:
                self.log.write(f"ERROR copying {f}: {e}")
       
        if copied and self.process_on_drop:
            self.do_process_zips()
        elif copied:
            self.refresh_library()
   
    def refresh_library(self):
        """Refresh library contents display"""
        self.rows, self.summary = scan_library(self.cfg)
        self.on_filter_change()
   
    def on_filter_change(self):
        """Apply filter and update tree view"""
        query = self.search_edit.text()
        # type_filter may be a multi-select set from the Format dropdown
        type_filter = getattr(self, 'format_filters', None)
        if type_filter is None:
            # fallback for older UI
            type_filter = "All"

        dup_only = bool(getattr(self, 'chk_dupes', None) and self.chk_dupes.isChecked())
        filtered = filter_rows(self.rows, query, type_filter, dup_only=dup_only)
        self.populate_tree(filtered)

    def on_format_toggled(self, label: str, checked: bool):
        if not hasattr(self, 'format_filters'):
            self.format_filters = set(self.format_checks.keys())
        if checked:
            self.format_filters.add(label)
        else:
            self.format_filters.discard(label)
        self.update_format_btn_text()
        self.on_filter_change()

    def on_format_all_clicked(self):
        # Toggle all checks: if all selected -> unselect all, else select all
        all_selected = all(a.isChecked() for a in self.format_checks.values())
        new_state = not all_selected
        for lbl, act in self.format_checks.items():
            act.blockSignals(True)
            act.setChecked(new_state)
            act.blockSignals(False)

        if new_state:
            self.format_filters = set(self.format_checks.keys())
        else:
            self.format_filters = set()

        self.update_format_btn_text()
        self.on_filter_change()

    def on_format_all_toggled(self, checked: bool):
        # Toggle all checkboxes
        for lbl, act in self.format_checks.items():
            act.blockSignals(True)
            act.setChecked(checked)
            act.blockSignals(False)

        if checked:
            self.format_filters = set(self.format_checks.keys())
        else:
            self.format_filters = set()

        self.update_format_btn_text()
        self.on_filter_change()

    def update_format_btn_text(self):
        if not hasattr(self, 'format_filters'):
            self.format_filters = set(self.format_checks.keys())
        if len(self.format_filters) == 0:
            text = "None"
        elif len(self.format_filters) == len(self.format_checks):
            text = "All"
        elif len(self.format_filters) == 1:
            text = next(iter(self.format_filters))
        else:
            text = f"{len(self.format_filters)} selected"
        self.format_btn.setText(text)
   
    def _selected_rows(self):
        return [(it, it.data(0, Qt.UserRole) or {}) for it in self.tree.selectedItems()]

    def open_in_kicad(self, *args):
        """Open the selected item(s) in KiCad (via the editor associated with the
        file type). Symbols open the shared .kicad_sym in the Symbol Editor."""
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "Open in KiCad", "No item selected.")
            return
        # distinct targets (all symbols share one .kicad_sym -> open it once)
        targets = []
        for it in items:
            t = it.text(0)
            target = Path(self.cfg["SymbolLib"]) if t == "Symbol" else Path(it.text(2))
            if target not in targets:
                targets.append(target)
        if len(targets) > 8:
            if QMessageBox.question(
                self, "Open in KiCad",
                f"Open {len(targets)} items in KiCad?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        if find_kicad_dir() is None:
            QMessageBox.warning(
                self, "Open in KiCad",
                "KiCad does not appear to be installed under Program Files.\n"
                "Opening with the default associated app instead."
            )
        for target in targets:
            if not target.exists():
                self.log.write(f"Open in KiCad: missing {target.name}")
                continue
            try:
                os.startfile(str(target))   # KiCad registers .kicad_mod/.kicad_sym/.step
                self.log.write(f"Open in KiCad: {target.name}")
            except Exception as e:
                self.log.write(f"Open in KiCad failed for {target.name}: {e}")

    def on_remove_duplicates(self):
        """One-click: keep one copy of each duplicated symbol, remove the rest."""
        n = self.summary.get("duplicates", 0)
        if not n:
            QMessageBox.information(self, "Remove Duplicates", "No duplicates found.")
            return
        reply = QMessageBox.question(
            self, "Remove Duplicates",
            f"{n} duplicate row(s) detected.\n\n"
            f"Remove all duplicates, keeping one copy of each? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        removed = dedupe_symbol_library(Path(self.cfg["SymbolLib"]), self.log)
        QMessageBox.information(
            self, "Remove Duplicates",
            f"Removed {removed} duplicate symbol(s)." if removed else "Nothing to remove."
        )
        self.refresh_library()

    def populate_tree(self, rows: List[Dict[str, object]]):
        """Populate tree widget with filtered rows"""
        self.tree.clear()

        is_dark = getattr(self, "_is_dark", True)
        # Neutral slate tint so duplicates stand out without a colour accent.
        dup_bg = QBrush(QColor(58, 62, 70) if is_dark else QColor(224, 228, 236))

        for r in rows:
            item = QTreeWidgetItem([
                str(r["type"]),
                str(r["name"]),
                str(r["path"])
            ])
            # Carry the full row (incl. sym_index) so Delete targets exactly
            # this entry rather than every entry sharing its name.
            item.setData(0, Qt.UserRole, r)
            if r.get("dup"):
                n = r.get("dup_count", 2)
                tip = (f"Duplicate: {n} copies of this {str(r['type']).lower()} exist. "
                       f"Deleting this row removes only this copy.")
                for col in range(self.tree.columnCount()):
                    item.setBackground(col, dup_bg)
                    item.setToolTip(col, tip)
                f = item.font(1)
                f.setBold(True)
                item.setFont(1, f)
            self.tree.addTopLevelItem(item)

        # Update summary label (call out duplicates when present)
        s = self.summary
        dup = s.get("duplicates", 0)
        dup_txt = f", Duplicates: {dup}" if dup else ""
        self.lbl_summary.setText(
            f"Library: {s.get('total', 0)} items "
            f"(Symbols: {s.get('symbols', 0)}, "
            f"Footprints: {s.get('footprints', 0)}, "
            f"Models: {s.get('models', 0)}{dup_txt})"
        )
   
    def on_tree_open(self):
        """Open the selected item(s) with their default app."""
        items = self.tree.selectedItems()
        if not items:
            return
        seen = set()
        for it in items:
            path = Path(it.text(2))
            if str(path) in seen:
                continue
            seen.add(str(path))
            try:
                target = path if path.exists() else path.parent
                os.startfile(str(target))
            except Exception as e:
                self.log.write(f"Open failed: {e}")
   
    def on_tree_delete(self):
        """Delete the selected item(s). Symbols are removed in a single pass so
        deleting several (including duplicates) never hits index-shift bugs."""
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "Delete", "No item selected.")
            return

        sym_to_remove: Dict[int, str] = {}   # sym_index -> name
        sym_fallback: List[str] = []         # names with no index (legacy)
        files = []                           # (name, path)
        for it in items:
            row = it.data(0, Qt.UserRole) or {}
            t = it.text(0)
            name = it.text(1)
            if t == "Symbol":
                idx = row.get("sym_index")
                if idx is None:
                    sym_fallback.append(name)
                else:
                    sym_to_remove[int(idx)] = name
            elif t in ("Footprint", "Model"):
                files.append((name, Path(it.text(2))))

        total = len(items)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Delete {total} selected item(s)?\n\n"
            f"For duplicated symbols, only the selected copies are removed.\n"
            f"This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        errors = []
        # Files first
        for name, path in files:
            try:
                if path.exists():
                    path.unlink()
                    self.log.write(f"Deleted file: {path.name}")
                else:
                    errors.append(f"{name}: file not found")
            except Exception as e:
                errors.append(f"{name}: {e}")
        # Symbols by index (single rewrite)
        if sym_to_remove:
            removed = remove_symbols_by_indices(Path(self.cfg["SymbolLib"]), sym_to_remove, self.log)
            if removed == 0:
                errors.append("symbols could not be removed (library changed — refresh and retry)")
        for nm in sym_fallback:
            if not remove_symbol_by_name(Path(self.cfg["SymbolLib"]), nm, self.log):
                errors.append(f"symbol '{nm}' not found")

        if errors:
            QMessageBox.warning(self, "Delete", "Some items were not deleted:\n- " + "\n- ".join(errors))
        self.refresh_library()
   
    def closeEvent(self, event):
        """Handle window close: stop background work cleanly so no worker
        thread touches the UI after the window is destroyed."""
        self._closing = True
        self._save_view_settings()
        try:
            if hasattr(self, "auto_pull_timer"):
                self.auto_pull_timer.stop()
        except Exception:
            pass
        try:
            self.watcher.stop()
        except Exception:
            pass
        # Let in-flight workers finish (or give up after a short wait) so none
        # outlive the window and emit into a deleted object.
        for t in list(self._workers):
            try:
                t.join(timeout=3.0)
            except Exception:
                pass
        event.accept()


# -----------------------------
# Main
# -----------------------------
def main():
    cfg = load_config()
    save_config(cfg)  # create/refresh config.json
   
    app = QApplication(sys.argv)
    # Use Fusion style for a modern, consistent look across platforms
    try:
        app.setStyle('Fusion')
    except Exception:
        pass
    # Use a clear UI font
    try:
        app.setFont(QFont('Segoe UI', 10))
    except Exception:
        pass

    app.setApplicationName("KiCad Library Manager")
    _icon = resource_path("app_icon.ico")
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))
   
    window = LibraryManagerWindow(cfg)
    window.show()
   
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()