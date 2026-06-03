#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad Library Manager - Python Tkinter UI

Workflow:
0. Pull (rebase + autostash) to ensure local repo is up to date.
1. Drop vendor ZIPs into the Drop Zone (or Open Downloads to place them).
2. Process ZIPs (move footprints/symbols/models, merge symbols).
3. Clean leftovers (delete remaining ZIPs/extracted folders in downloads).
4. Stage, Commit & Push to GitHub.

Features:
- Responsive ttk UI (grid-based) that scales cleanly
- Left-aligned workflow buttons (Step 0 → Step 4)
- Drag-and-drop ZIPs into a Drop Zone to copy into downloads/ (tkinterdnd2)
- Scrollable "Library Contents" panel on the right with Search, Filter, Open, Delete
- Equilux dark theme (via ttkthemes)
- Optional file watcher (requires 'watchdog')
- Robust type handling for watchdog (no Pylance error)
- Live log panel with scrollbar

Author: You
"""

from logging import root
import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from tkinter import (
    Tk, Text, END, DISABLED, NORMAL, filedialog, messagebox,
    BooleanVar, StringVar
)
from tkinter import simpledialog
from tkinter import ttk
from typing import Optional, List, TYPE_CHECKING, Dict

# -----------------------------
# Optional drag-and-drop (tkinterdnd2)
# -----------------------------
HAVE_TKDND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAVE_TKDND = True
except Exception:
    HAVE_TKDND = False

# -----------------------------
# Optional watcher (robust handling)
# -----------------------------
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAVE_WATCHDOG = True
except Exception:
    HAVE_WATCHDOG = False

    # Runtime stubs so the module imports cleanly without watchdog installed
    class Observer:  # minimal placeholder
        def __init__(self, *_, **__): pass
        def schedule(self, *_, **__): raise RuntimeError("watchdog is not installed")
        def start(self): raise RuntimeError("watchdog is not installed")
        def stop(self): pass
        def join(self, timeout: Optional[float] = None): pass

    class FileSystemEventHandler:  # minimal placeholder
        def __init__(self, *_, **__): pass

# -----------------------------
# Equilux theme (ttkthemes)
# -----------------------------
HAVE_TTKTHEMES = False
try:
    # ThemedStyle works with an existing Tk/TkinterDnD root
    from ttkthemes.themed_style import ThemedStyle
    HAVE_TTKTHEMES = True
except Exception:
    HAVE_TTKTHEMES = False

# Type-only hints for analyzers (no runtime impact)
if TYPE_CHECKING:
    from watchdog.observers import Observer as _Observer  # noqa: F401
    from watchdog.events import FileSystemEventHandler as _FSEH  # noqa: F401

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
    "PythonExe":    sys.executable  # current python
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
    def __init__(self, text_widget: Text, logfile: Path):
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
        self.text.configure(state=NORMAL)
        self.text.insert(END, line)
        self.text.see(END)
        self.text.configure(state=DISABLED)


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
    """
    Try to extract the symbol name from a '(symbol ...)' block.
    Handles forms like:
      (symbol "Lib:Name" ...)
      (symbol (name "Name") ...)
    Falls back to the first line if parsing fails.
    """
    head = block.splitlines()[0]
    try:
        # Case 1: (symbol "Lib:Name" ...)
        if '(symbol "' in head:
            start = head.index('(symbol "') + len('(symbol "')
            end = head.index('"', start)
            raw = head[start:end]
            name = raw.split(':')[-1]  # prefer part after colon
            return name
        # Case 2: (symbol (name "Name") ...)
        if '(name "' in block:
            start = block.index('(name "') + len('(name "')
            end = block.index('"', start)
            return block[start:end]
    except Exception:
        pass
    return head.strip()

def insert_blocks_into_target(target_text: str, blocks: List[str]) -> str:
    """
    Insert blocks just before the top-level closing paren of the library.
    """
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
    """
    Remove a symbol block by name from the .kicad_sym library.
    Returns True if something was removed.
    """
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

def move_files(part_dir: Path, cfg: Dict[str, str], log: UILog):
    all_files = list(part_dir.rglob("*"))
    files = [p for p in all_files if p.is_file()]

    sym_files   = [p for p in files if p.suffix.lower() == ".kicad_sym"]
    mod_files   = [p for p in files if p.suffix.lower() == ".kicad_mod"]
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
    folder = filedialog.askdirectory(initialdir=cfg["Downloads"], title="Select extracted part folder")
    if not folder:
        return
    folder_path = Path(folder)
    log.write(f"Manual process folder: {folder_path}")
    move_files(folder_path, cfg, log)
    log.write("Done manual processing")
    if refresh_cb:
        refresh_cb()

def clean_leftovers(cfg: Dict[str, str], log: UILog, refresh_cb=None):
    """
    Deletes any remaining *.zip and extracted folders in Downloads.
    Prompts for confirmation before removal.
    """
    downloads = Path(cfg["Downloads"])
    zips = list(downloads.glob("*.zip"))
    dirs = [p for p in downloads.iterdir() if p.is_dir()]
    if not zips and not dirs:
        log.write("Clean: nothing to remove in downloads")
        if refresh_cb:
            refresh_cb()
        return
    msg = (
        f"This will delete {len(zips)} ZIP file(s) and {len(dirs)} folder(s)\n"
        f"in:\n{downloads}\n\nProceed?"
    )
    if not messagebox.askyesno("Confirm Clean Leftovers", msg):
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
# Git commands (button-driven)
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
    # Make pulls resilient: set once (harmless if repeated)
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
    """
    Combined action: Stage all, prompt for commit message, commit, then push.
    """
    default = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    msg = simpledialog.askstring("Commit Message", "Enter commit message:", initialvalue=default)
    if msg is None:
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
    class ZipHandler:  # Friendly placeholder
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
# Helpers: drag-and-drop + safe copy
# -----------------------------
def safe_copy_to_downloads(src_path: Path, downloads: Path) -> Path:
    """Copy src_path to downloads, avoiding overwrite by adding (1), (2), ... suffix."""
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

def parse_dnd_file_list(tk_root: Tk, data: str) -> List[Path]:
    """Robustly parse DND_FILES data into Paths (handles braces/quotes/whitespace)."""
    files = tk_root.tk.splitlist(data)
    return [Path(f) for f in files]

# -----------------------------
# Library scan + filtering + UI binding
# -----------------------------
def scan_library(cfg: Dict[str, str]):
    """
    Scan current library contents.
    Returns (rows, summary) where rows is list of dicts:
    {type: 'Symbol'|'Footprint'|'Model', name: str, path: Path}
    and summary is dict of counts.
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
            # Keep UI resilient even if symbol parsing fails
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
        if tf != "All" and r["type"] != tf:
            continue
        name = str(r["name"]).lower()
        if q and q not in name:
            continue
        out.append(r)
    return out

def populate_tree(tree: ttk.Treeview, rows: List[Dict[str, object]], summary: Dict[str, int], lbl_summary: ttk.Label):
    # Clear tree
    for item in tree.get_children():
        tree.delete(item)
    # Insert rows
    for r in rows:
        tree.insert("", "end", values=(r["type"], r["name"], str(r["path"])))
    lbl_summary.config(text=f"Library: {summary['total']} items "
                            f"(Symbols: {summary['symbols']}, "
                            f"Footprints: {summary['footprints']}, "
                            f"Models: {summary['models']})")

# -----------------------------
# Apply Equilux theme
# -----------------------------
def apply_equilux_theme(root: Tk, log: Optional[UILog] = None):
    """
    Apply the Equilux theme via ttkthemes and override text colors to white.
    Also tints classic Tk widgets (Text) to match.
    """
    # Left-align the text inside all step buttons
    style = ttk.Style(root)
    style.configure("Step.TButton", anchor="w", padding=(12, 6))

    try:
        from ttkthemes.themed_style import ThemedStyle  # requires `pip install ttkthemes`
        style = ThemedStyle(root, theme="equilux")
        style.theme_use("equilux")

        # --- Global overrides (affect all ttk widgets unless a widget-specific style overrides it)
        style.configure(
            ".", 
            foreground="#ffffff",          # default text color
            selectforeground="#ffffff"     # text color when selected
        )
        # --- Common widget types (explicit, to ensure consistency across pixmap-based theme)
        for widget_style in (
            "TLabel", "TButton", "TCheckbutton", "TRadiobutton",
            "TEntry", "TCombobox", "Treeview", "TNotebook", "TFrame", "TLabelframe"
        ):
            style.configure(widget_style, foreground="#ffffff")

        # Treeview selection text (map handles state-based colors)
        style.map("Treeview", foreground=[("selected", "#ffffff")])

        # Combobox entry text sometimes uses the fieldforeground option:
        # (Not all Tk builds expose this; harmless if ignored)
        style.configure("TCombobox", fieldforeground="#ffffff")

        # Match window and Text widget to Equilux palette (Equilux window = #373737)
        root.configure(bg="#373737")
        # If you have a classic Tk Text for log, set it after creation too:
        # txt_log.configure(bg="#373737", fg="#ffffff", insertbackground="#ffffff")

        if log:
            log.write("Theme: Equilux applied with white foreground overrides")
    except Exception as e:
        if log:
            log.write(f"WARN: Equilux not applied or overrides failed: {e}")
        # Fallback (still make text white on built-in theme)
        s = ttk.Style(root)
        try:
            s.configure(".", foreground="#ffffff")
        except Exception:
            pass

# -----------------------------
# Tkinter UI (responsive ttk)
# -----------------------------
def build_ui(cfg: Dict[str, str]):
    # Use TkinterDnD root if available
    if HAVE_TKDND:
        root = TkinterDnD.Tk()
    else:
        root = Tk()

    # Theme + window settings
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # temporary base; Equilux applied below
    except Exception:
        pass
    root.title("KiCad Library Manager")
    root.minsize(1100, 680)

    # Top-level grid: two columns (left controls, right library/log)
    root.columnconfigure(0, weight=0)   # left column fixed-width
    root.columnconfigure(1, weight=1)   # right column grows
    root.rowconfigure(3, weight=1)      # log row grows

    # Header frame
    hdr = ttk.Frame(root, padding=(10, 8))
    hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
    ttk.Label(hdr, text=f"Repo: {cfg['RepoRoot']}").grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Label(hdr, text=f"Downloads: {cfg['Downloads']}").grid(row=1, column=0, sticky="w")

    # Drop zone
    dz = ttk.Labelframe(root, text="Drag ZIP Files Here into Drop Zone", padding=(10, 10))
    dz.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
    dz.columnconfigure(0, weight=1)
    
    dz_canvas = None
    dz_text_id = None
    dz_rect_id = None
    
    def dz_draw_outline(event=None, color="#a6a6a6", dash=(8,6)):
        nonlocal dz_rect_id, dz_text_id
        w = dz_canvas.winfo_width()
        h = dz_canvas.winfo_height()
        dz_canvas.delete("all")
        pad = 12
        dz_rect_id = dz_canvas.create_rectangle(pad, pad, w-pad, h-pad, outline=color, width=2, dash=dash)
        dz_text_id = dz_canvas.create_text(w//2, h//2, text=dz_canvas.cget("Drop ZIP Files Here"), fill=color, font=("Segoe UI", 11, "bold"), justify="center")
    
    def dz_on_enter(_):
        dz_draw_outline(color="#bebebe")
        
    def dz_on_leave(_):
        dz_draw_outline(color="#a6a6a6")
    
    def handle_drop(event):
        files = parse_dnd_file_list(root, event.data)
        copied = []
        for f in files:
            if f.suffix.lower() != ".zip":
                log.write(f"Skip (not a ZIP): {f}")
                continue
            try:
                dst = safe_copy_to_downloads(f, Path(cfg["Downloads"]))
                log.write(f"Copied to downloads: {dst.name}")
                copied.append(dst)
            except Exception as e:
                log.write(f"ERROR {f}: {e}")
            if copied and process_on_drop.get():
                process_existing_zips(cfg, log, refresh_cb=lambda: refresh_library())
        
    process_on_drop = BooleanVar(value=True)
    ttk.Checkbutton(dz, text="Instantaneous Processing for Dropped File", variable=process_on_drop).grid(row=1, column=0, sticky="w", pady=(6, 0))
    
    dz_canvas = Text(dz, height=6, width=10)
    dz_canvas.configure(bg="#373737", fg="#a6a6a6", insertbackground="#a6a6a6", highlightthickness=0, relief="flat", state=DISABLED)
    dz_canvas.grid(row=0, column=0, sticky="ew")
    dz_canvas.bind("<Configure>", dz_draw_outline)
    dz_canvas.bind("<Enter>", dz_on_enter)
    dz_canvas.bind("<Leave>", dz_on_leave)
    
    if HAVE_TKDND:
        dz_canvas.drop_target_register(DND_FILES)
        dz_canvas.dnd_bind('<<Drop>>', handle_drop)
    else:
        ttk.Label(dz, text="Drag-and-Drop requires 'tkinterdnd2'. Click to open Downloads.").grid(row=2, column=0, sticky="w")
        ttk.Button(dz, text="Open Downloads", command=lambda: os.startfile(cfg["Downloads"])).grid(row=2, column=0, sticky="e")
    
    # Left: Workflow frame (left-aligned buttons)
    wf = ttk.Labelframe(root, text="Workflow", padding=(10, 10))
    wf.grid(row=2, column=0, sticky="nsw", padx=10, pady=6)
    for r in range(8):
        wf.rowconfigure(r, weight=0)
    wf.columnconfigure(0, weight=1, uniform="wf")

    # Right top: Library contents
    libf = ttk.Labelframe(root, text="Contents", padding=(10, 8))
    libf.grid(row=2, column=1, sticky="nsew", padx=(0, 10), pady=6)
    libf.columnconfigure(0, weight=1)
    libf.rowconfigure(3, weight=1)

    # Filter row
    filter_frame = ttk.Frame(libf)
    filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    filter_frame.columnconfigure(3, weight=1)

    ttk.Label(filter_frame, text="Type:").grid(row=0, column=0, sticky="w")
    type_var = StringVar(value="All")
    type_combo = ttk.Combobox(
        filter_frame, textvariable=type_var,
        values=("All", "Symbol", "Footprint", "Model"),
        state="readonly", width=12
    )
    type_combo.grid(row=0, column=1, sticky="w", padx=(6, 12))

    ttk.Label(filter_frame, text="Search:").grid(row=0, column=2, sticky="e")
    search_var = StringVar(value="")
    search_entry = ttk.Entry(filter_frame, textvariable=search_var)
    search_entry.grid(row=0, column=3, sticky="ew", padx=(6, 6))

    # Summary + actions
    lbl_summary = ttk.Label(libf, text="Library: 0 items")
    lbl_summary.grid(row=1, column=0, sticky="w", pady=(0, 6))

    btn_actions = ttk.Frame(libf)
    btn_actions.grid(row=2, column=0, sticky="ew", pady=(0, 6))
    btn_actions.columnconfigure(0, weight=1)

    # --- Log panel (classic Tk Text, manually colored to Equilux) ---
    lg = ttk.Labelframe(root, text="Log", padding=(10, 6))
    lg.grid(row=3, column=1, sticky="nsew", padx=(0, 10), pady=(0, 10))
    lg.columnconfigure(0, weight=1)
    lg.rowconfigure(0, weight=1)

    txt_log = Text(lg, wrap="none", state=DISABLED, height=12)
    txt_log.grid(row=0, column=0, sticky="nsew")
    scr = ttk.Scrollbar(lg, command=txt_log.yview)
    scr.grid(row=0, column=1, sticky="ns")
    txt_log.configure(yscrollcommand=scr.set)

    log = UILog(txt_log, Path(cfg["LogFile"]))
    log.write("UI started")

    # Apply Equilux theme now (styles + log widget colors)
    apply_equilux_theme(root, log)
    # Match Text widget to Equilux palette
    txt_log.configure(bg="#373737", fg="#a6a6a6", insertbackground="#a6a6a6", highlightthickness=0, relief="flat")

    wc = WatchController(cfg, log)

    # Drag-and-drop handler
    def handle_drop(event):
        files = parse_dnd_file_list(root, event.data)
        copied = []
        for f in files:
            if f.suffix.lower() != ".zip":
                log.write(f"Skip (not a ZIP): {f}")
                continue
            try:
                dst = safe_copy_to_downloads(f, Path(cfg["Downloads"]))
                log.write(f"Copied to downloads: {dst.name}")
                copied.append(dst)
            except Exception as e:
                log.write(f"ERROR copying {f}: {e}")
        if copied and process_on_drop.get():
            process_existing_zips(cfg, log, refresh_cb=lambda: refresh_library())

    # Register DnD if available; fallback button otherwise
    if HAVE_TKDND:
        dz_canvas.drop_target_register(DND_FILES)
        dz_canvas.dnd_bind('<<Drop>>', handle_drop)
    else:
        ttk.Label(dz, text="Drag-and-Drop requires 'tkinterdnd2'. Click to open Downloads.").grid(row=2, column=0, sticky="w")
        ttk.Button(dz, text="Open Downloads", command=lambda: os.startfile(cfg["Downloads"])).grid(row=2, column=0, sticky="e")

    # --- Library actions ---
    def on_tree_open(event=None):
        sel = tree.selection()
        if not sel:
            return
        values = tree.item(sel[0], "values")
        path = Path(values[2])
        try:
            if path.is_file():
                os.startfile(path)
            else:
                os.startfile(path if path.exists() else path.parent)
        except Exception as e:
            log.write(f"Open failed: {e}")

    def on_tree_delete():
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("Delete", "No item selected.")
            return
        t, n, p = tree.item(sel[0], "values")
        path = Path(p)
        if not messagebox.askyesno("Confirm Delete", f"Delete '{n}' ({t})?\n\nThis action cannot be undone."):
            return
        try:
            if t == "Symbol":
                ok = remove_symbol_by_name(Path(cfg["SymbolLib"]), n, log)
                if not ok:
                    messagebox.showwarning("Delete Symbol", f"Symbol '{n}' not found or could not be removed.")
            elif t in ("Footprint", "Model"):
                if path.exists():
                    path.unlink()
                    log.write(f"Deleted file: {path.name}")
                else:
                    messagebox.showwarning("Delete", f"File not found:\n{path}")
            else:
                messagebox.showwarning("Delete", f"Unsupported type: {t}")
        except Exception as e:
            messagebox.showerror("Delete Failed", str(e))
        refresh_library()

    ttk.Button(btn_actions, text="Refresh Library", command=lambda: refresh_library()).grid(row=0, column=0, sticky="w")
    ttk.Button(btn_actions, text="Open Selected", command=lambda: on_tree_open()).grid(row=0, column=1, sticky="w", padx=(10, 0))
    ttk.Button(btn_actions, text="Delete Selected", command=lambda: on_tree_delete()).grid(row=0, column=2, sticky="w", padx=(10, 0))

    # Treeview with columns
    columns = ("Type", "Name", "Location")
    tree = ttk.Treeview(libf, columns=columns, show="headings", selectmode="browse")
    for col in columns:
        tree.heading(col, text=col)
    tree.column("Type", width=110, anchor="w")
    tree.column("Name", width=260, anchor="w")
    tree.column("Location", width=480, anchor="w")
    tree.grid(row=3, column=0, sticky="nsew")

    scr_tree = ttk.Scrollbar(libf, orient="vertical", command=tree.yview)
    scr_tree.grid(row=3, column=1, sticky="ns")
    tree.configure(yscrollcommand=scr_tree.set)
    tree.bind("<Double-Button-1>", on_tree_open)

    # Right bottom: Log frame already set above

    # Advanced frame under workflow
    adv = ttk.Labelframe(root, text="Advanced", padding=(10, 10))
    adv.grid(row=3, column=0, sticky="news", padx=10, pady=(0, 10))
    adv.columnconfigure(0, weight=1)

    # --- Filter logic + refresh helper ---
    def on_filter_change(*_):
        rows, summary = scan_library(cfg)
        filtered = filter_rows(rows, search_var.get(), type_var.get())
        populate_tree(tree, filtered, summary, lbl_summary)

    type_combo.bind("<<ComboboxSelected>>", on_filter_change)
    search_entry.bind("<KeyRelease>", on_filter_change)

    def refresh_library():
        rows, summary = scan_library(cfg)
        filtered = filter_rows(rows, search_var.get(), type_var.get())
        populate_tree(tree, filtered, summary, lbl_summary)
        
    style = ttk.Style(root)
    style.configure("Step.TButton", anchor="w", padding=(12, 6))

    # --- Workflow buttons (Step 0–4), left-aligned (sticky='w') ---
    wf = ttk.Labelframe(root, text="Workflow", padding=(10, 10))
    wf.grid(row=2, column=0, sticky="nsw", padx=10, pady=6)
    wf.columnconfigure(0, weight=1, uniform="wf")
    wf.grid_columnconfigure(0, minsize=320)
    ttk.Button(wf, text="Step 0: Pull (Rebase + Auto‑Stash)", command=lambda: git_pull(cfg, log), style="Step.TButton").grid(row=0, column=0, sticky="ew", pady=3)
    ttk.Button(wf, text="Step 1: Open Downloads", command=lambda: os.startfile(cfg["Downloads"]), style="Step.TButton").grid(row=1, column=0, sticky="ew", pady=3)
    ttk.Button(wf, text="Step 2: Process ZIPs", command=lambda: process_existing_zips(cfg, log, refresh_cb=refresh_library), style="Step.TButton").grid(row=2, column=0, sticky="ew", pady=3)
    ttk.Button(wf, text="Step 3: Clean Leftovers", command=lambda: clean_leftovers(cfg, log, refresh_cb=refresh_library), style="Step.TButton").grid(row=3, column=0, sticky="ew", pady=3)
    ttk.Button(wf, text="Step 4: Stage, Commit & Push", command=lambda: commit_and_push(cfg, log), style="Step.TButton").grid(row=4, column=0, sticky="ew", pady=8)

    # --- Advanced actions ---
    ttk.Button(adv, text="Pull", command=lambda: git_pull(cfg, log)).grid(row=0, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Push", command=lambda: git_push(cfg, log)).grid(row=1, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Stage & Commit", command=lambda: git_stage_commit(cfg, log)).grid(row=2, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Process Folder", command=lambda: process_folder_dialog(cfg, log, refresh_cb=refresh_library)).grid(row=3, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Start Watcher", command=wc.start).grid(row=4, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Stop Watcher", command=wc.stop).grid(row=5, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Open Libraries", command=lambda: os.startfile(cfg["Libs"])).grid(row=6, column=0, sticky="ew", pady=3)
    ttk.Button(adv, text="Open Log", command=lambda: os.startfile(cfg["LogFile"])).grid(row=7, column=0, sticky="ew", pady=3)

    # Initial population
    refresh_library()

    def on_close():
        wc.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    return root

# -----------------------------
# Merge symbols (placed near UI for clarity; used in move_files)
# -----------------------------
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

# -----------------------------
# Main
# -----------------------------
def main():
    cfg = load_config()
    save_config(cfg)  # create/refresh config.json for collaborators
    app = build_ui(cfg)
    app.mainloop()

if __name__ == "__main__":
    main()