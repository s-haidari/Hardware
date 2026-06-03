#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KiCad Library Manager - Python Tkinter UI

Features:
- Pull / Push buttons (no automatic git)
- Run Import Now (process all ZIPs in downloads)
- Process Folder… (process a specific extracted vendor folder)
- Start/Stop Watcher for new ZIPs (requires watchdog)
- Open Downloads / Open Libs / Open Log
- Live log panel

Rules:
- .kicad_sym -> merged into MySymbols.kicad_sym
- .kicad_mod -> copied to MyFootprints.pretty/
- .step / .stp / .wrl -> copied to My3DModels/
- Unknown files -> moved to misc/
- ZIP + extracted folder -> deleted
- No renaming (keeps vendor filenames)

Paths can be customized via config.json or by editing DEFAULTS below.

Author: You
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from tkinter import Tk, Label, Button, Text, Scrollbar, END, DISABLED, NORMAL, filedialog, messagebox
from tkinter.font import Font
from typing import Optional, List, TYPE_CHECKING

# -----------------------------
# Optional watcher (robust handling)
# -----------------------------
# We try to import watchdog, but if it's not available we:
#  - Provide minimal runtime stubs so names always exist
#  - Guard subclass declaration and usage behind HAVE_WATCHDOG
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAVE_WATCHDOG = True
except Exception:
    HAVE_WATCHDOG = False

    # Runtime stubs so module imports cleanly without watchdog installed
    class Observer:  # minimal placeholder
        def __init__(self, *_, **__):
            pass

        def schedule(self, *_, **__):
            raise RuntimeError("watchdog is not installed")

        def start(self):
            raise RuntimeError("watchdog is not installed")

        def stop(self):
            pass

        def join(self, timeout: Optional[float] = None):
            pass

    class FileSystemEventHandler:  # minimal placeholder
        def __init__(self, *_, **__):
            pass

# Tell type checkers about the *real* types without affecting runtime
if TYPE_CHECKING:
    from watchdog.observers import Observer as _Observer  # noqa: F401
    from watchdog.events import FileSystemEventHandler as _FSEH  # noqa: F401

# -----------------------------
# Configuration (edit defaults)
# -----------------------------

DEFAULTS = {
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

def load_config():
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


def save_config(cfg):
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

def extract_symbol_blocks(src_text: str):
    """
    Returns list of full '(symbol ...)' blocks from a .kicad_sym file.
    Simple balanced-paren scanner, tolerates quoted strings.
    """
    blocks = []
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

def move_files(part_dir: Path, cfg: dict, log: UILog):
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

def process_zip(zip_path: Path, cfg: dict, log: UILog):
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

def process_existing_zips(cfg: dict, log: UILog):
    zips = list(Path(cfg["Downloads"]).glob("*.zip"))
    if not zips:
        log.write("No ZIPs found in downloads")
        return
    for z in zips:
        process_zip(z, cfg, log)

def process_folder_dialog(cfg: dict, log: UILog):
    folder = filedialog.askdirectory(initialdir=cfg["Downloads"], title="Select extracted part folder")
    if not folder:
        return
    folder_path = Path(folder)
    log.write(f"Manual process folder: {folder_path}")
    move_files(folder_path, cfg, log)
    log.write("Done manual processing")

# -----------------------------
# Git commands (button-driven)
# -----------------------------

def run_git(args: List[str], cfg: dict, log: UILog):
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

def git_pull(cfg: dict, log: UILog):
    log.write("Git pull --rebase...")
    run_git(["pull", "--rebase"], cfg, log)

def git_push(cfg: dict, log: UILog):
    log.write("Git push...")
    run_git(["push"], cfg, log)

def git_stage_commit(cfg: dict, log: UILog, message: Optional[str] = None):
    run_git(["add", "-A"], cfg, log)
    if not message:
        message = f"Library update {time.strftime('%Y-%m-%d %H:%M:%S')}"
    run_git(["commit", "-m", message], cfg, log)

# -----------------------------
# Watcher (optional)
# -----------------------------

if HAVE_WATCHDOG:
    class ZipHandler(FileSystemEventHandler):
        def __init__(self, cfg, log):
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
    def __init__(self, cfg, log):
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
# Tkinter UI
# -----------------------------

def build_ui(cfg):
    root = Tk()
    root.title("KiCad Library Manager")
    root.geometry("900x600")

    lbl_repo = Label(root, text=f"Repo: {cfg['RepoRoot']}")
    lbl_repo.place(x=10, y=10)

    lbl_down = Label(root, text=f"Downloads: {cfg['Downloads']}")
    lbl_down.place(x=10, y=35)

    # Buttons row 1
    btn_pull = Button(root, text="Pull", width=10)
    btn_pull.place(x=10, y=70)

    btn_push = Button(root, text="Push", width=10)
    btn_push.place(x=100, y=70)

    btn_commit = Button(root, text="Stage & Commit", width=15)
    btn_commit.place(x=190, y=70)

    btn_import = Button(root, text="Run Import Now", width=15)
    btn_import.place(x=320, y=70)

    btn_proc_folder = Button(root, text="Process Folder…", width=15)
    btn_proc_folder.place(x=450, y=70)

    btn_start = Button(root, text="Start Watcher", width=12)
    btn_start.place(x=580, y=70)

    btn_stop = Button(root, text="Stop Watcher", width=12)
    btn_stop.place(x=680, y=70)

    btn_open_down = Button(root, text="Open Downloads", width=15)
    btn_open_down.place(x=790, y=70)

    btn_open_libs = Button(root, text="Open Libs", width=12)
    btn_open_libs.place(x=10, y=100)

    btn_open_log = Button(root, text="Open Log", width=12)
    btn_open_log.place(x=130, y=100)

    # Log panel
    log_font = Font(family="Consolas", size=10)
    txt_log = Text(root, wrap="none", font=log_font, state=DISABLED)
    txt_log.place(x=10, y=135, width=870, height=430)
    scr = Scrollbar(root, command=txt_log.yview)
    scr.place(x=880, y=135, height=430)
    txt_log.configure(yscrollcommand=scr.set)

    log = UILog(txt_log, Path(cfg["LogFile"]))
    log.write("UI started")

    wc = WatchController(cfg, log)

    # Wire buttons
    btn_pull.configure(command=lambda: git_pull(cfg, log))
    btn_push.configure(command=lambda: git_push(cfg, log))
    btn_commit.configure(command=lambda: git_stage_commit(cfg, log))
    btn_import.configure(command=lambda: process_existing_zips(cfg, log))
    btn_proc_folder.configure(command=lambda: process_folder_dialog(cfg, log))
    btn_start.configure(command=wc.start)
    btn_stop.configure(command=wc.stop)
    btn_open_down.configure(command=lambda: os.startfile(cfg["Downloads"]))
    btn_open_libs.configure(command=lambda: os.startfile(cfg["Libs"]))
    btn_open_log.configure(command=lambda: os.startfile(cfg["LogFile"]))

    def on_close():
        wc.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    return root

def main():
    cfg = load_config()
    save_config(cfg)  # create/refresh config.json for collaborators
    app = build_ui(cfg)
    app.mainloop()

if __name__ == "__main__":
    main()