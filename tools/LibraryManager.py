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
import subprocess
import threading
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from typing import Optional, List, Dict

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QTextEdit, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QComboBox, QCheckBox, QGroupBox, QFileDialog, QMessageBox, QInputDialog,
    QHeaderView, QFrame, QScrollArea, QSizePolicy, QSplitter, QStyle
    , QToolButton, QMenu
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, QMimeData, QUrl
from PyQt5.QtGui import QPalette, QColor, QDragEnterEvent, QDropEvent, QPainter, QPen, QFont

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
# Configuration (edit defaults)
# -----------------------------
DEFAULTS: Dict[str, str] = {
    "RepoRoot":     r"C:\Users\developer\Documents\GitHub\Hardware",
    "Downloads":    r"C:\Users\developer\Documents\GitHub\Hardware\downloads",
    "Libs":         r"C:\Users\developer\Documents\GitHub\Hardware\libs",
    "SymbolLib":    r"C:\Users\developer\Documents\GitHub\Hardware\libs\MySymbols.kicad_sym",
    "FootprintLib": r"C:\Users\developer\Documents\GitHub\Hardware\libs\MyFootprints.pretty",
    "ModelLib":     r"C:\Users\developer\Documents\GitHub\Hardware\libs\My3DModels",
    "MiscDir":      r"C:\Users\developer\Documents\GitHub\Hardware\misc",
    "LogFile":      r"C:\Users\developer\Documents\GitHub\Hardware\tools\ui_python.log",
    "PythonExe":    sys.executable
}

CONFIG_PATH = Path(DEFAULTS["RepoRoot"], "tools", "config.json")


# -----------------------------
# Utilities / logging
# -----------------------------
def load_config() -> Dict[str, str]:
    cfg = DEFAULTS.copy()
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update(data)
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


class UILog:
    """Thread-safe logger that writes to both file and QTextEdit"""
    def __init__(self, text_widget: QTextEdit, logfile: Path):
        self.text = text_widget
        self.file = logfile
        self.file.parent.mkdir(parents=True, exist_ok=True)
        if not self.file.exists():
            self.file.touch()

    def write(self, msg: str):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}\n"
       
        try:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
       
        # Append to text widget (thread-safe via Qt signal/slot mechanism)
        self.text.append(f"[{ts}] {msg}")
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


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
    total_blocks: List[str] = []
    for src in sources:
        try:
            src_text = read_text(src)
        except Exception as e:
            log.write(f"WARN read symbol {src}: {e}")
            continue
        blocks = extract_symbol_blocks(src_text)
        if blocks:
            total_blocks.extend(blocks)
        elif "(symbol" in src_text:
            total_blocks.append(src_text.strip())
    if not total_blocks:
        log.write("No symbols found in source files.")
        return
    new_text = insert_blocks_into_target(target_text, total_blocks)
    try:
        write_text(target_path, new_text)
        log.write(f"Merged {len(total_blocks)} symbol(s) into {target_path}")
    except Exception as e:
        log.write(f"ERROR writing merged symbols: {e}")

def move_files(part_dir: Path, cfg: Dict[str, str], log: UILog):
    all_files = list(part_dir.rglob("*"))
    files = [p for p in all_files if p.is_file()]

    sym_files = [p for p in files if p.suffix.lower() == ".kicad_sym"]
    mod_files = [p for p in files if p.suffix.lower() == ".kicad_mod"]
    model_files = [p for p in files if p.suffix.lower() in (".step", ".stp", ".wrl")]

    # Merge symbols
    if sym_files:
        merge_symbols(Path(cfg["SymbolLib"]), sym_files, log)

    # Footprints
    for m in mod_files:
        dst = Path(cfg["FootprintLib"], m.name)
        try:
            shutil.copy2(m, dst)
            log.write(f"Move footprint: {m.name}")
        except Exception as e:
            log.write(f"ERROR copy footprint {m}: {e}")

    # 3D models
    for mdl in model_files:
        dst = Path(cfg["ModelLib"], mdl.name)
        try:
            shutil.copy2(mdl, dst)
            log.write(f"Move 3D model: {mdl.name}")
        except Exception as e:
            log.write(f"ERROR copy model {mdl}: {e}")

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

def process_zip(zip_path: Path, cfg: Dict[str, str], log: UILog):
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

def process_existing_zips(cfg: Dict[str, str], log: UILog, refresh_cb=None):
    zips = list(Path(cfg["Downloads"]).glob("*.zip"))
    if not zips:
        log.write("No ZIPs found in downloads")
        if refresh_cb:
            refresh_cb()
        return
    for z in zips:
        process_zip(z, cfg, log)
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
        proc = subprocess.run(
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
    log.write("Git pull --rebase (auto-stash)...")
    run_git(["config", "pull.rebase", "true"], cfg, log)
    run_git(["config", "rebase.autoStash", "true"], cfg, log)
    run_git(["pull", "--rebase", "--autostash"], cfg, log)

def git_push(cfg: Dict[str, str], log: UILog):
    log.write("Git push...")
    run_git(["push"], cfg, log)

def git_stage_commit(cfg: Dict[str, str], log: UILog, message: Optional[str] = None):
    run_git(["add", "-A"], cfg, log)
    if not message:
        message = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    run_git(["commit", "-m", message], cfg, log)

def commit_and_push(cfg: Dict[str, str], log: UILog):
    """Combined action: Stage all, prompt for commit message, commit, then push"""
    default = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    msg, ok = QInputDialog.getText(None, "Commit Message", "Enter commit message:", text=default)
    if not ok:
        log.write("Commit: canceled by user")
        return
    git_stage_commit(cfg, log, message=msg.strip() or default)
    git_push(cfg, log)


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

    # Symbols
    sym_path = Path(cfg["SymbolLib"])
    if sym_path.exists():
        try:
            text = read_text(sym_path)
            blocks = extract_symbol_blocks(text)
            for b in blocks:
                nm = extract_symbol_name(b)
                rows.append({"type": "Symbol", "name": nm, "path": sym_path})
        except Exception:
            pass

    summary = {
        "symbols": sum(1 for r in rows if r["type"] == "Symbol"),
        "footprints": sum(1 for r in rows if r["type"] == "Footprint"),
        "models": sum(1 for r in rows if r["type"] == "Model"),
        "total": len(rows),
    }
    return rows, summary

def filter_rows(rows: List[Dict[str, object]], query: str, type_filter: str) -> List[Dict[str, object]]:
    q = (query or "").strip().lower()
    tf = type_filter
    out: List[Dict[str, object]] = []
    for r in rows:
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
        # Compact default height so the drop zone doesn't dominate the layout
        self.setMinimumHeight(40)
        self.setMaximumHeight(56)
        self.setFrameStyle(QFrame.Box)
        self.setLineWidth(1)

        # Minimal layout: centered label, no graphics
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setAlignment(Qt.AlignCenter)

        self.label = QLabel("Drop ZIP Files Here")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 10pt; font-weight: 600; color: #606060;")
        layout.addWidget(self.label)

        # Minimal styling: no dashed border, simple background change on hover
        self.default_style = "QFrame { background: transparent; border: none; }"
        self.hover_style = "QFrame { background: #fafafa; border: none; }"
        self.setStyleSheet(self.default_style)
   
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self.hover_style)
   
    def dragLeaveEvent(self, event):
        self.setStyleSheet(self.default_style)
   
    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self.default_style)
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
        # Hide the title label if title is empty to avoid blank header space
        if title:
            outer.addWidget(self.title_lbl)

        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 6, 8, 8)
        self.content_layout.setSpacing(6)
        outer.addWidget(self.content)

    def contentLayout(self):
        return self.content_layout

# -----------------------------
# Main Window
# -----------------------------
class LibraryManagerWindow(QMainWindow):
    # Signals used for thread-safe logging and refresh
    log_signal = pyqtSignal(str)
    pull_done = pyqtSignal()
    def __init__(self, cfg: Dict[str, str]):
        super().__init__()
        self.cfg = cfg
        self.rows = []
        self.summary = {}
        self.process_on_drop = True
       
        self.setWindowTitle("KiCad Library Manager")
        self.setMinimumSize(960, 620)
       
        # Central widget with main layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(12)  # consistent spacing between sections
        main_layout.setContentsMargins(12, 12, 12, 12)

        # --- Header ---
        header = self.create_header()
        main_layout.addWidget(header)

        # --- Drop Zone ---
        drop_group = self.create_drop_zone()
        main_layout.addWidget(drop_group)

        # Initialize log panel first (needed for logger)
        log_group = self.create_log_panel()

        # Connect cross-thread signals
        self.log_signal.connect(self.log.write)
        self.pull_done.connect(self.refresh_library)

        # Initialize watcher (needs log)
        self.watcher = WatchController(self.cfg, self.log)

        # --- Central splitters for robust resizing ---
        central_splitter = QSplitter(Qt.Horizontal)

        # Left column: Workflow (now contains advanced dropdown)
        workflow = self.create_workflow()
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(8)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(workflow)
        left_layout.addStretch()
        left_panel.setMinimumWidth(320)
        central_splitter.addWidget(left_panel)

        # Right column: horizontal splitter for library and log (side-by-side, non-collapsible)
        right_splitter = QSplitter(Qt.Horizontal)
        library = self.create_library_panel()
        right_splitter.addWidget(library)
        right_splitter.addWidget(log_group)
        # Prevent either side from collapsing completely
        try:
            right_splitter.setCollapsible(0, False)
            right_splitter.setCollapsible(1, False)
        except Exception:
            pass
        library.setMinimumWidth(300)
        self.txt_log.setMinimumWidth(300)
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 2)
        right_splitter.setSizes([800, 400])
        central_splitter.addWidget(right_splitter)

        central_splitter.setStretchFactor(0, 0)
        central_splitter.setStretchFactor(1, 1)
        main_layout.addWidget(central_splitter)

        # Apply light theme
        self.apply_light_theme()

        # Start an initial background pull shortly after UI shows
        QTimer.singleShot(250, self.start_initial_pull)

        # Initial library scan (will run after pull completes via refresh)
        self.refresh_library()

        self.log.write("UI started")
   
    def create_header(self) -> CardWidget:
        """Create header with repo info"""
        card = CardWidget("")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = card.contentLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        h = QHBoxLayout()

        # Repo toolbutton with menu (Open / Change)
        self.repo_btn = QToolButton()
        self.repo_btn.setText(f"Repo: {Path(self.cfg['RepoRoot']).name}")
        self.repo_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.repo_btn.setPopupMode(QToolButton.MenuButtonPopup)
        repo_menu = QMenu(self.repo_btn)
        repo_menu.addAction("Open").triggered.connect(lambda: os.startfile(self.cfg['RepoRoot']))
        repo_menu.addAction("Change").triggered.connect(lambda: self.change_path('RepoRoot', self.repo_btn))
        self.repo_btn.setMenu(repo_menu)
        self.repo_btn.setToolTip(self.cfg['RepoRoot'])
        h.addWidget(self.repo_btn)

        # Downloads toolbutton with menu (Open / Change)
        self.dl_btn = QToolButton()
        self.dl_btn.setText(f"Downloads: {Path(self.cfg['Downloads']).name}")
        self.dl_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.dl_btn.setPopupMode(QToolButton.MenuButtonPopup)
        dl_menu = QMenu(self.dl_btn)
        dl_menu.addAction("Open").triggered.connect(lambda: os.startfile(self.cfg['Downloads']))
        dl_menu.addAction("Change").triggered.connect(lambda: self.change_path('Downloads', self.dl_btn))
        self.dl_btn.setMenu(dl_menu)
        self.dl_btn.setToolTip(self.cfg['Downloads'])
        h.addWidget(self.dl_btn)

        h.addStretch()
        layout.addLayout(h)

        return card

    def change_path(self, key: str, btn: QPushButton):
        """Allow user to change a configured path (RepoRoot or Downloads)"""
        start = self.cfg.get(key, DEFAULTS.get(key, ""))
        new = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if not new:
            return
        self.cfg[key] = str(Path(new))
        save_config(self.cfg)
        # Ensure directory exists
        Path(self.cfg[key]).mkdir(parents=True, exist_ok=True)
        btn.setText(f"{ 'Repo' if key=='RepoRoot' else 'Downloads' }: {Path(self.cfg[key]).name}")
        btn.setToolTip(self.cfg[key])

    def start_initial_pull(self):
        """Start a background thread to pull latest from GitHub and refresh library."""
        def _pull():
            try:
                self.log_signal.emit("Auto-pull: fetching latest from GitHub...")

                # Run git commands and forward output via signal (avoid calling UI from this thread)
                def _run_git(args):
                    try:
                        proc = subprocess.run(
                            ["git", "-C", self.cfg["RepoRoot"], *args],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8"
                        )
                        out = proc.stdout or ""
                        for line in out.splitlines():
                            self.log_signal.emit(line)
                        if proc.returncode != 0:
                            self.log_signal.emit(f"ERROR git {' '.join(args)} exit {proc.returncode}")
                    except FileNotFoundError:
                        self.log_signal.emit("ERROR: git not found on PATH. Install Git and retry.")
                    except Exception as e:
                        self.log_signal.emit(f"ERROR running git {' '.join(args)}: {e}")

                _run_git(["config", "pull.rebase", "true"])
                _run_git(["config", "rebase.autoStash", "true"])
                _run_git(["pull", "--rebase", "--autostash"])

                self.log_signal.emit("Auto-pull: finished")
                # Signal main thread to refresh library
                self.pull_done.emit()
            except Exception as e:
                self.log_signal.emit(f"Auto-pull failed: {e}")

        threading.Thread(target=_pull, daemon=True).start()
   
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

        # Process on drop checkbox
        self.chk_process = QCheckBox("Process on drop")
        self.chk_process.setChecked(True)
        self.chk_process.stateChanged.connect(lambda s: setattr(self, 'process_on_drop', bool(s)))
        layout.addWidget(self.chk_process)

        # Fallback button (always visible as alternative)
        btn_open_downloads = QPushButton("Downloads")
        btn_open_downloads.setMaximumHeight(24)
        btn_open_downloads.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_open_downloads.clicked.connect(lambda: os.startfile(self.cfg["Downloads"]))
        layout.addWidget(btn_open_downloads)

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

        # Top row: advanced dropdown placed inside workflow
        top_row = QHBoxLayout()
        top_row.addStretch()
        adv_btn = QToolButton()
        adv_btn.setText("Advanced ▾")
        adv_btn.setPopupMode(QToolButton.InstantPopup)
        adv_menu = QMenu(adv_btn)
        adv_actions = [
            ("Pull", lambda: git_pull(self.cfg, self.log)),
            ("Push", lambda: git_push(self.cfg, self.log)),
            ("Commit (Stage & Commit)", lambda: git_stage_commit(self.cfg, self.log)),
            ("Process Folder...", lambda: process_folder_dialog(self.cfg, self.log, self.refresh_library)),
            ("Start Watcher", lambda: self.watcher.start()),
            ("Stop Watcher", lambda: self.watcher.stop()),
            ("Open Libraries", lambda: os.startfile(self.cfg["Libs"])),
            ("Open Log File", lambda: os.startfile(self.cfg["LogFile"])),
        ]
        for label, cb in adv_actions:
            a = adv_menu.addAction(label)
            a.triggered.connect(lambda checked=False, fn=cb: fn())
        adv_btn.setMenu(adv_menu)
        top_row.addWidget(adv_btn)
        layout.addLayout(top_row)

        btn_style = """
            QPushButton {
                text-align: left;
                padding: 6px 10px;
                font-size: 9pt;
            }
        """

        # Full step labels with clear descriptions
        buttons = [
            ("Step 0: Pull (Rebase + Auto‑Stash)", lambda: git_pull(self.cfg, self.log)),
            ("Step 1: Open Downloads", lambda: os.startfile(self.cfg["Downloads"])),
            ("Step 2: Process ZIPs", lambda: process_existing_zips(self.cfg, self.log, self.refresh_library)),
            ("Step 3: Clean Leftovers", lambda: clean_leftovers(self.cfg, self.log, self.refresh_library)),
            ("Step 4: Stage, Commit & Push", lambda: commit_and_push(self.cfg, self.log)),
        ]

        for text, callback in buttons:
            btn = QPushButton(text)
            btn.setStyleSheet(btn_style)
            btn.setMaximumHeight(34)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(callback)
            layout.addWidget(btn)

        layout.addStretch()
        return card
   
    def create_library_panel(self) -> CardWidget:
        """Create library contents panel with filter/search"""
        group = CardWidget("Contents")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = group.contentLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)
       
        # Filter row
        filter_layout = QHBoxLayout()
       
        filter_layout.addWidget(QLabel("Format:"))
        # Checkbox dropdown for multi-select formats
        self.format_btn = QToolButton()
        self.format_btn.setText("All ▾")
        self.format_btn.setPopupMode(QToolButton.InstantPopup)
        self.format_menu = QMenu(self.format_btn)
        # 'All' action
        self.format_all_action = self.format_menu.addAction("All")
        self.format_all_action.setCheckable(True)
        self.format_all_action.setChecked(True)
        self.format_all_action.triggered.connect(lambda checked: self.on_format_all_toggled(checked))
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
        filter_layout.addWidget(self.format_btn)
        # Initialize filters state
        self.format_filters = set(self.format_checks.keys())
        self.update_format_btn_text()
       
        filter_layout.addSpacing(6)
        filter_layout.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self.on_filter_change)
        self.search_edit.setMaximumHeight(24)
        filter_layout.addWidget(self.search_edit, 1)
       
        layout.addLayout(filter_layout)
       
        # Summary label
        self.lbl_summary = QLabel("Library: 0 items")
        summary_font = self.lbl_summary.font()
        summary_font.setPointSize(8)
        self.lbl_summary.setFont(summary_font)
        layout.addWidget(self.lbl_summary)
       
        # Action buttons
        btn_layout = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.setMaximumHeight(24)
        btn_refresh.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        btn_refresh.clicked.connect(self.refresh_library)
        btn_layout.addWidget(btn_refresh)
       
        btn_open = QPushButton("Open")
        btn_open.setMaximumHeight(24)
        btn_open.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        btn_open.clicked.connect(self.on_tree_open)
        btn_layout.addWidget(btn_open)
       
        btn_delete = QPushButton("Delete")
        btn_delete.setMaximumHeight(24)
        btn_delete.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)
        btn_delete.clicked.connect(self.on_tree_delete)
        btn_layout.addWidget(btn_delete)
       
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
       
        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Format", "Name", "Location"])
        self.tree.setColumnWidth(0, 130)
        self.tree.setColumnWidth(1, 260)
        self.tree.setColumnWidth(2, 420)
        self.tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(10)
        # Prefer sizing Type/Name to contents and let Location stretch
        header = self.tree.header()
        try:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
        except Exception:
            self.tree.header().setStretchLastSection(True)

        self.tree.itemDoubleClicked.connect(self.on_tree_open)
        layout.addWidget(self.tree)

        return group
   
    
   
    def create_log_panel(self) -> CardWidget:
        """Create log panel"""
        group = CardWidget("Log")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = group.contentLayout()
        layout.setContentsMargins(8, 6, 8, 6)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(120)
        self.txt_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.txt_log)

        # Initialize logger
        self.log = UILog(self.txt_log, Path(self.cfg["LogFile"]))

        return group
   
    def apply_light_theme(self):
        """Apply light monochrome theme"""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(245, 245, 245))
        palette.setColor(QPalette.WindowText, QColor(40, 40, 40))
        palette.setColor(QPalette.Base, QColor(250, 250, 250))
        palette.setColor(QPalette.AlternateBase, QColor(240, 240, 240))
        palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ToolTipText, QColor(40, 40, 40))
        palette.setColor(QPalette.Text, QColor(40, 40, 40))
        palette.setColor(QPalette.Button, QColor(240, 240, 240))
        palette.setColor(QPalette.ButtonText, QColor(40, 40, 40))
        palette.setColor(QPalette.BrightText, QColor(100, 100, 100))
        palette.setColor(QPalette.Link, QColor(80, 80, 80))
        palette.setColor(QPalette.Highlight, QColor(200, 200, 200))
        palette.setColor(QPalette.HighlightedText, QColor(40, 40, 40))
       
        self.setPalette(palette)
       
        # Light monochrome stylesheet
        stylesheet = """
            QWidget {
                background-color: #f5f5f5;
                color: #282828;
                font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
            }
            QFrame#card {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                background-color: #ffffff;
                padding: 6px;
                margin-top: 6px;
            }
            QLabel#cardTitle {
                color: #333333;
                padding-left: 6px;
                padding-top: 4px;
                padding-bottom: 4px;
                font-weight: 700;
                font-size: 10pt;
            }
            QToolButton {
                background-color: transparent;
                border: 1px solid #e6e6e6;
                border-radius: 6px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QToolButton::menu-indicator { image: none; }
            QMenu {
                background-color: #ffffff;
                border: 1px solid #e6e6e6;
            }
            QMenu::item {
                padding: 6px 18px;
            }
            QMenu::item:selected {
                background-color: #f3f3f3;
            }
            QPushButton {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #e8e8e8, stop:1 #dcdcdc);
                color: #2a2a2a;
                border: 1px solid #b8b8b8;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 8pt;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #f0f0f0, stop:1 #e5e5e5);
                border: 1px solid #a8a8a8;
            }
            QPushButton:pressed {
                background-color: #d8d8d8;
            }
            QPushButton:disabled {
                background-color: #e0e0e0;
                color: #888888;
            }
            QLineEdit, QComboBox {
                background-color: #ffffff;
                border: 1px solid #c0c0c0;
                border-radius: 3px;
                padding: 4px 6px;
                color: #2a2a2a;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #808080;
            }
            QTreeWidget {
                background-color: #ffffff;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                color: #2a2a2a;
                alternate-background-color: #f5f5f5;
                gridline-color: #e8e8e8;
            }
            QTreeWidget::item:selected {
                background-color: #d0d0d0;
                color: #1a1a1a;
            }
            QTreeWidget::item:hover {
                background-color: #e8e8e8;
            }
            QHeaderView::section {
                background-color: #efefef;
                color: #2a2a2a;
                padding: 4px;
                border: 1px solid #d0d0d0;
                font-weight: 600;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                color: #404040;
            }
            QCheckBox {
                color: #2a2a2a;
            }
            QLabel {
                color: #2a2a2a;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #2a2a2a;
                selection-background-color: #d0d0d0;
                border: 1px solid #c0c0c0;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-top: 4px solid #606060;
                margin-right: 6px;
            }
            QScrollBar:vertical {
                background-color: #f5f5f5;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #c0c0c0;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #a8a8a8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
                height: 0px;
            }
            QScrollBar:horizontal {
                background-color: #f5f5f5;
                height: 12px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background-color: #c0c0c0;
                min-width: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background-color: #a8a8a8;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: none;
                width: 0px;
            }
        """
        self.setStyleSheet(stylesheet)
   
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
            process_existing_zips(self.cfg, self.log, refresh_cb=self.refresh_library)
   
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

        filtered = filter_rows(self.rows, query, type_filter)
        self.populate_tree(filtered)

    def on_format_toggled(self, label: str, checked: bool):
        if not hasattr(self, 'format_filters'):
            self.format_filters = set(self.format_checks.keys())
        if checked:
            self.format_filters.add(label)
        else:
            self.format_filters.discard(label)

        # Update 'All' action state
        all_selected = all(a.isChecked() for a in self.format_checks.values())
        self.format_all_action.blockSignals(True)
        self.format_all_action.setChecked(all_selected)
        self.format_all_action.blockSignals(False)

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
            text = "None ▾"
        elif len(self.format_filters) == len(self.format_checks):
            text = "All ▾"
        elif len(self.format_filters) == 1:
            text = next(iter(self.format_filters)) + " ▾"
        else:
            text = f"{len(self.format_filters)} selected ▾"
        self.format_btn.setText(text)
   
    def populate_tree(self, rows: List[Dict[str, object]]):
        """Populate tree widget with filtered rows"""
        self.tree.clear()
       
        for r in rows:
            item = QTreeWidgetItem([
                str(r["type"]),
                str(r["name"]),
                str(r["path"])
            ])
            self.tree.addTopLevelItem(item)
       
        # Update summary label
        self.lbl_summary.setText(
            f"Library: {self.summary['total']} items "
            f"(Symbols: {self.summary['symbols']}, "
            f"Footprints: {self.summary['footprints']}, "
            f"Models: {self.summary['models']})"
        )
   
    def on_tree_open(self):
        """Open selected item in tree"""
        current = self.tree.currentItem()
        if not current:
            return
       
        path = Path(current.text(2))
        try:
            if path.is_file():
                os.startfile(str(path))
            else:
                os.startfile(str(path if path.exists() else path.parent))
        except Exception as e:
            self.log.write(f"Open failed: {e}")
   
    def on_tree_delete(self):
        """Delete selected item from tree"""
        current = self.tree.currentItem()
        if not current:
            QMessageBox.information(self, "Delete", "No item selected.")
            return
       
        item_type = current.text(0)
        name = current.text(1)
        path = Path(current.text(2))
       
        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            f"Delete '{name}' ({item_type})?\n\nThis action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
       
        if reply != QMessageBox.Yes:
            return
       
        try:
            if item_type == "Symbol":
                ok = remove_symbol_by_name(Path(self.cfg["SymbolLib"]), name, self.log)
                if not ok:
                    QMessageBox.warning(
                        self,
                        "Delete Symbol",
                        f"Symbol '{name}' not found or could not be removed."
                    )
            elif item_type in ("Footprint", "Model"):
                if path.exists():
                    path.unlink()
                    self.log.write(f"Deleted file: {path.name}")
                else:
                    QMessageBox.warning(self, "Delete", f"File not found:\n{path}")
            else:
                QMessageBox.warning(self, "Delete", f"Unsupported type: {item_type}")
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
       
        self.refresh_library()
   
    def closeEvent(self, event):
        """Handle window close event"""
        self.watcher.stop()
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
    # Try to apply a modern material theme if available
    try:
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme='light_cyan.xml')
    except Exception:
        pass

    app.setApplicationName("KiCAD Library Manager")
   
    window = LibraryManagerWindow(cfg)
    window.show()
   
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()