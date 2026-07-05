"""Retroactively conform EXISTING objects in a KiCad project to a standard.

KiCad's Board Setup only sets defaults for newly-placed items; existing footprints,
text, and net labels keep whatever size they were drawn at. This module does what
KiCad's "Edit Text & Graphics Properties" global edit does, headlessly and by type,
so a whole project can be brought onto a house standard (e.g. an OSH Park preset).

The user picks which object TYPES to rewrite:
  PCB  — component/board silk text, fab text, copper text (by layer)
  SCH  — schematic text, net labels (label / global_label / hierarchical_label)

Everything is done as targeted in-place edits of the existing font size/thickness
(the file is NOT reformatted), with a dry-run preview of exactly how many objects
each type would change, and an atomic .bak-backed apply. Sizes are millimetres,
KiCad-native.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

# PCB layer sets per category.
_SILK = ('"F.SilkS"', '"B.SilkS"')
_FAB = ('"F.Fab"', '"B.Fab"')
_CU = ('"F.Cu"', '"B.Cu"')

_SIZE_RE = re.compile(r'\(size\s+[-\d.]+\s+[-\d.]+\)')
_THICK_RE = re.compile(r'\(thickness\s+[-\d.]+\)')


def _fmt(v) -> str:
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _span_end(text: str, open_idx: int) -> int:
    """Index just past the ')' matching the '(' at open_idx (string-aware)."""
    depth, i, instr = 0, open_idx, False
    while i < len(text):
        c = text[i]
        if c == '"' and (i == 0 or text[i - 1] != "\\"):
            instr = not instr
        elif not instr:
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return len(text)


def _set_font(block: str, size_mm, thickness_mm) -> tuple:
    """Set the size (and thickness, if the font already carries one) of the FIRST
    (font …) inside `block`. Returns (new_block, changed)."""
    fm = re.search(r"\(font\b", block)
    if not fm:
        return block, False
    fs = fm.start()
    fe = _span_end(block, fs)
    font = block[fs:fe]
    new_font, n = _SIZE_RE.subn(f"(size {_fmt(size_mm)} {_fmt(size_mm)})", font, count=1)
    changed = bool(n)
    if thickness_mm is not None and _THICK_RE.search(new_font):
        new_font = _THICK_RE.sub(f"(thickness {_fmt(thickness_mm)})", new_font, count=1)
        changed = True
    if not changed:
        return block, False
    return block[:fs] + new_font + block[fe:], True


def _rewrite_objects(text, opener_re, size_mm, thickness_mm, layer_filter=None) -> tuple:
    """Rewrite the font of every object whose header matches opener_re (optionally
    restricted by layer_filter(block)). Edits back-to-front so indices stay valid."""
    spans = [(m.start(), _span_end(text, m.start())) for m in re.finditer(opener_re, text)]
    count = 0
    for start, end in reversed(spans):
        block = text[start:end]
        if layer_filter and not layer_filter(block):
            continue
        new_block, changed = _set_font(block, size_mm, thickness_mm)
        if changed:
            text = text[:start] + new_block + text[end:]
            count += 1
    return text, count


def _has_layer(block, layers) -> bool:
    return any(lay in block for lay in layers)


# ── the type catalogue the checklist is built from ───────────────────────────
PCB_TYPES = ("silk", "fab", "copper")
SCH_TYPES = ("text", "labels")
_PCB_LAYERS = {"silk": _SILK, "fab": _FAB, "copper": _CU}


def conform_pcb_text(pcb_text: str, targets: dict) -> tuple:
    """targets: {category: (size_mm, thickness_mm)} over PCB_TYPES. Rewrites fp_text +
    gr_text on the matching layers. Returns (new_text, {category: count})."""
    counts = {}
    for cat in PCB_TYPES:
        if cat not in targets:
            continue
        size, thick = targets[cat]
        layers = _PCB_LAYERS[cat]
        pcb_text, n = _rewrite_objects(pcb_text, r"\((?:fp_text|gr_text)\b", size, thick,
                                       lambda b, ls=layers: _has_layer(b, ls))
        counts[cat] = n
    return pcb_text, counts


def conform_schematic_text(sch_text: str, targets: dict) -> tuple:
    """targets: {'text': (size,thick), 'labels': (size,thick)}. Rewrites schematic text
    and net-label fonts. Returns (new_text, {type: count})."""
    counts = {}
    if "text" in targets:
        size, thick = targets["text"]
        sch_text, n = _rewrite_objects(sch_text, r'\(text\s+"', size, thick)
        counts["text"] = n
    if "labels" in targets:
        size, thick = targets["labels"]
        sch_text, n = _rewrite_objects(
            sch_text, r'\((?:label|global_label|hierarchical_label)\s+"', size, thick)
        counts["labels"] = n
    return sch_text, counts


def _conform_one(path: Path, pcb_targets: dict, sch_targets: dict) -> tuple:
    """(new_text, counts) for a single file; ('' , {}) if nothing applies."""
    text = path.read_text(encoding="utf-8", errors="replace")
    suf = path.suffix.lower()
    if suf == ".kicad_pcb" and pcb_targets:
        return conform_pcb_text(text, pcb_targets)
    if suf == ".kicad_sch" and sch_targets:
        return conform_schematic_text(text, sch_targets)
    return text, {}


def conform_project(files, pcb_targets: dict, sch_targets: dict,
                    timestamp: str, dry_run: bool = True) -> dict:
    """Conform existing objects across a set of .kicad_sch / .kicad_pcb files.

    pcb_targets / sch_targets pick which TYPES change and to what size/thickness.
    dry_run=True (default) computes the change counts WITHOUT writing — for the
    preview. dry_run=False writes each changed file after a .bak backup, atomically:
    every file is staged in memory first and, if any write fails, all are rolled
    back from their backups. Returns {files:[{path, counts, changed}], total, written}.
    """
    staged = []          # (path, new_text, counts)
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        new_text, counts = _conform_one(p, pcb_targets, sch_targets)
        total = sum(counts.values())
        staged.append({"path": str(p), "counts": counts, "changed": total,
                       "_new": new_text if total else None})

    written = False
    if not dry_run:
        backups = []
        try:
            for s in staged:
                if s["_new"] is None:
                    continue
                p = Path(s["path"])
                bak = p.with_suffix(p.suffix + f".{timestamp}.bak")
                shutil.copy2(p, bak)
                backups.append((p, bak, p.read_text(encoding="utf-8", errors="replace")))
                p.write_text(s["_new"], encoding="utf-8", newline="\n")
            written = any(s["_new"] is not None for s in staged)
        except Exception:                    # noqa: BLE001 — roll everything back
            for p, _bak, original in backups:
                try:
                    p.write_text(original, encoding="utf-8", newline="\n")
                except Exception:
                    pass
            raise

    return {"files": [{k: v for k, v in s.items() if k != "_new"} for s in staged],
            "total": sum(s["changed"] for s in staged), "written": written}
