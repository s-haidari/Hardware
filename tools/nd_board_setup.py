#!/usr/bin/env python3
"""
nd_board_setup.py — read/modify/write the (setup ...) block of a .kicad_pcb.

WHY THIS MODULE EXISTS
----------------------
The codebase audit flagged that solder-mask / solder-paste globals were being
written to `.kicad_pro` (`board.design_settings.solder_mask_clearance` /
`solder_paste_margin`). KiCad IGNORES those keys there — the real board-wide
solder-mask/paste globals physically live in the `.kicad_pcb` S-expression
`(setup ...)` block. This module edits that block directly, so the values
actually take effect.

REAL KiCad (setup ...) KEYS (verified against KiCad 10 demo boards AND the
compiled token table in KiCad's binary — see nd_board_setup report):

  (pad_to_mask_clearance N)            solder-mask expansion (mm)        [BOARD]
  (solder_mask_min_width N)            solder-mask min web width (mm)    [BOARD]
  (pad_to_paste_clearance N)           global solder-paste margin (mm)   [BOARD]
  (pad_to_paste_clearance_ratio R)     global solder-paste margin ratio  [BOARD]
  (grid_origin X Y)                    user grid origin (mm)             [BOARD]
  (aux_axis_origin X Y)                drill/place file origin (mm)      [BOARD]
  (allow_soldermask_bridges_in_footprints yes|no)                       [BOARD]

IMPORTANT NAMING NOTE
---------------------
`solder_mask_margin`, `solder_paste_margin`, and `solder_paste_margin_ratio`
are PAD / FOOTPRINT level keys in KiCad — they are NOT valid (setup ...) keys.
Writing them into (setup ...) would be dead keys. The audit's ProjectSettings
uses those friendly names, so this module accepts them as ALIASES and maps them
onto the correct real board keys before writing:

  solder_mask_clearance      -> pad_to_mask_clearance
  solder_paste_margin        -> pad_to_paste_clearance
  solder_paste_margin_ratio  -> pad_to_paste_clearance_ratio

The FILE only ever receives real KiCad keys. Aliases are a convenience layer.

ALL VALUES ARE IN MILLIMETRES (KiCad's internal / on-disk unit for these keys),
except *_ratio which is a dimensionless fraction (e.g. -0.05 == -5%).

Pure text S-expression editing — no KiCad / pcbnew dependency. String-aware
paren scanning (quoted strings may legally contain parentheses).

SKIPPED (see report): stackup / per-layer editing, and the repeatable
user_via / user_trace_width / user_diff_pair predefined-size lists.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# ═══════════════════════════════════════════════════════════════════
# SUPPORTED KEYS
# ═══════════════════════════════════════════════════════════════════
# Scalar length / ratio values (single numeric argument).
SETUP_NUMERIC_KEYS = {
    "pad_to_mask_clearance",
    "solder_mask_min_width",
    "pad_to_paste_clearance",
    "pad_to_paste_clearance_ratio",
}
# Coordinate-pair values: (key X Y).
SETUP_COORD_KEYS = {
    "grid_origin",
    "aux_axis_origin",
}
# yes/no boolean values.
SETUP_BOOL_KEYS = {
    "allow_soldermask_bridges_in_footprints",
}

SUPPORTED_REAL_KEYS = SETUP_NUMERIC_KEYS | SETUP_COORD_KEYS | SETUP_BOOL_KEYS

# Friendly ProjectSettings names -> real (setup ...) keys. See module docstring.
KEY_ALIASES = {
    "solder_mask_clearance": "pad_to_mask_clearance",
    "solder_paste_margin": "pad_to_paste_clearance",
    "solder_paste_margin_ratio": "pad_to_paste_clearance_ratio",
}

__all__ = [
    "SETUP_NUMERIC_KEYS",
    "SETUP_COORD_KEYS",
    "SETUP_BOOL_KEYS",
    "SUPPORTED_REAL_KEYS",
    "KEY_ALIASES",
    "resolve_key",
    "has_setup_block",
    "validate_kicad_pcb",
    "get_board_setup",
    "set_board_setup",
    "get_board_setup_safe",
    "set_board_setup_safe",
    "read_pcb_text",
    "load_board_setup",
    "save_board_setup",
    "BoardSetupManager",
]


def resolve_key(name: str) -> Optional[str]:
    """Map a friendly/alias key to its real (setup ...) key, or return the name
    unchanged if it is already a supported real key, or None if unsupported."""
    if name in SUPPORTED_REAL_KEYS:
        return name
    return KEY_ALIASES.get(name)


# ═══════════════════════════════════════════════════════════════════
# STRING-AWARE S-EXPRESSION SCANNING
# ═══════════════════════════════════════════════════════════════════
def _snippet(text: str, idx: int, width: int = 40) -> str:
    """A short, single-line context window around `idx` for diagnostics."""
    lo = max(0, idx - width)
    hi = min(len(text), idx + width)
    frag = text[lo:hi].replace("\n", "\\n").replace("\t", "\\t")
    return f"...{frag}..." if (lo > 0 or hi < len(text)) else frag


def _scan_sexpr(text: str, i: int) -> int:
    """Given text[i] == '(', return the index just PAST the matching ')'.

    Respects double-quoted strings (which may contain unbalanced parens) and
    backslash escapes inside them. Raises ValueError on an unbalanced/truncated
    form, with the opening offset and a text snippet so a corrupt .kicad_pcb is
    diagnosable rather than an opaque failure."""
    n = len(text)
    if i >= n or text[i] != "(":
        raise ValueError(f"_scan_sexpr: expected '(' at offset {i}: {_snippet(text, i)!r}")
    open_idx = i
    depth = 0
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError(
        f"_scan_sexpr: unbalanced/truncated S-expression opened at offset "
        f"{open_idx}: {_snippet(text, open_idx)!r}")


def _read_atom(text: str, i: int) -> Tuple[int, int]:
    """Read a single atom starting at i (a quoted string or a bare token).
    Returns (start, end) with text[start:end] being the atom (quotes kept)."""
    n = len(text)
    if text[i] == '"':
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                return i, j + 1
            j += 1
        raise ValueError("_read_atom: unterminated string")
    j = i
    while j < n and (not text[j].isspace()) and text[j] not in "()":
        j += 1
    return i, j


def _children(text: str, open_idx: int):
    """Parse the list beginning at text[open_idx] == '('.

    Returns (head, content_start, close_idx, children) where:
      head          - the first token after '(' (the list's symbol)
      content_start - index just after the head token
      close_idx     - index of this list's matching ')'
      children      - list of (child_open, child_end) for each direct child
                      sub-list (nested atoms are skipped over correctly)."""
    end = _scan_sexpr(text, open_idx)
    close_idx = end - 1
    i = open_idx + 1
    while i < close_idx and text[i].isspace():
        i += 1
    hs, he = _read_atom(text, i)
    head = text[hs:he]
    i = he
    children: List[Tuple[int, int]] = []
    while i < close_idx:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            ce = _scan_sexpr(text, i)
            children.append((i, ce))
            i = ce
            continue
        _, ae = _read_atom(text, i)
        i = ae
    return head, he, close_idx, children


def _outer_open(text: str) -> int:
    i = text.find("(")
    if i < 0:
        raise ValueError("no S-expression found")
    return i


def _find_setup(text: str) -> Optional[Tuple[int, int]]:
    """Return (open_idx, end_idx) of the top-level (setup ...) block, or None."""
    outer = _outer_open(text)
    _, _, _, kids = _children(text, outer)
    for a, b in kids:
        h, _, _, _ = _children(text, a)
        if h == "setup":
            return a, b
    return None


def has_setup_block(pcb_text: str) -> bool:
    """True if the board text contains a top-level (setup ...) block."""
    try:
        return _find_setup(pcb_text) is not None
    except ValueError:
        return False


def validate_kicad_pcb(pcb_text: str) -> Tuple[bool, Optional[str]]:
    """Pre-validate that `pcb_text` is a well-formed top-level (kicad_pcb ...) form.

    Returns (ok, error). Confirms there is an outer S-expression, that it scans
    to a single balanced form (no unbalanced/truncated parens), and that its head
    symbol is ``kicad_pcb``. This is the guard the safe get/set wrappers run first
    so a corrupt board yields a structured error instead of a raise deep in the
    scanner — letting schematic-side (.kicad_pro) settings still save."""
    try:
        outer = _outer_open(pcb_text)
        head, _, _, _ = _children(pcb_text, outer)
    except ValueError as e:
        return False, str(e)
    if head != "kicad_pcb":
        return False, (f"top-level form is ({head} ...), not (kicad_pcb ...) at "
                       f"offset {outer}: {_snippet(pcb_text, outer)!r}")
    return True, None


# ═══════════════════════════════════════════════════════════════════
# VALUE PARSING / FORMATTING
# ═══════════════════════════════════════════════════════════════════
def _unquote(atom: str) -> str:
    if len(atom) >= 2 and atom[0] == '"' and atom[-1] == '"':
        return atom[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return atom


def _atoms_between(text: str, start: int, end: int) -> List[str]:
    """Return the bare/quoted atoms directly between start and end (nested
    sub-lists are skipped)."""
    out: List[str] = []
    i = start
    while i < end:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            i = _scan_sexpr(text, i)
            continue
        s, e = _read_atom(text, i)
        out.append(text[s:e])
        i = e
    return out


def _to_float(atom: str) -> Optional[float]:
    try:
        return float(_unquote(atom))
    except (TypeError, ValueError):
        return None


def _fmt_num(v: Union[int, float]) -> str:
    """Format a number the way KiCad does: integral values as bare ints, else
    up to 6 decimals with trailing zeros trimmed. Normalises -0 -> 0."""
    f = float(v)
    if f == 0.0:
        f = 0.0  # kill negative zero
    if f == int(f):
        return str(int(f))
    s = ("%.6f" % f).rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _as_bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("yes", "true", "1", "on")
    return bool(v)


def _render_node(real_key: str, value) -> str:
    """Render a single '(key ...)' node for a supported real key."""
    if real_key in SETUP_BOOL_KEYS:
        return f"({real_key} {'yes' if _as_bool(value) else 'no'})"
    if real_key in SETUP_COORD_KEYS:
        seq = list(value)
        if len(seq) < 2:
            raise ValueError(f"{real_key} needs 2 coordinates, got {value!r}")
        return f"({real_key} {_fmt_num(seq[0])} {_fmt_num(seq[1])})"
    # numeric / ratio
    return f"({real_key} {_fmt_num(float(value))})"


# ═══════════════════════════════════════════════════════════════════
# GET
# ═══════════════════════════════════════════════════════════════════
def get_board_setup(pcb_text: str, include_aliases: bool = True) -> Dict[str, object]:
    """Return the supported (setup ...) keys found in `pcb_text`.

    Values: numeric keys -> float; *_ratio -> float; coord keys -> (x, y) float
    tuple; bool key -> bool. Keys absent from the board are omitted.

    When include_aliases is True (default) the friendly ProjectSettings names
    (solder_mask_clearance / solder_paste_margin / solder_paste_margin_ratio)
    are ALSO added, mirroring the value of their real counterpart, so callers
    keyed on the audit's field names see them. The file itself only ever holds
    the real KiCad keys — aliases are a read-side convenience. Returns {} when
    there is no (setup ...) block."""
    result: Dict[str, object] = {}
    span = _find_setup(pcb_text)
    if span is None:
        return result
    _, _, _, kids = _children(pcb_text, span[0])
    for a, _b in kids:
        head, content_start, close_idx, _ = _children(pcb_text, a)
        if head in SETUP_NUMERIC_KEYS:
            for atom in _atoms_between(pcb_text, content_start, close_idx):
                f = _to_float(atom)
                if f is not None:
                    result[head] = f
                    break
        elif head in SETUP_BOOL_KEYS:
            atoms = _atoms_between(pcb_text, content_start, close_idx)
            if atoms:
                result[head] = _unquote(atoms[0]).strip().lower() in ("yes", "true")
        elif head in SETUP_COORD_KEYS:
            nums = [f for f in (_to_float(x) for x in
                                _atoms_between(pcb_text, content_start, close_idx))
                    if f is not None]
            if len(nums) >= 2:
                result[head] = (nums[0], nums[1])
    if include_aliases:
        for alias, real in KEY_ALIASES.items():
            if real in result and alias not in result:
                result[alias] = result[real]
    return result


# ═══════════════════════════════════════════════════════════════════
# SET
# ═══════════════════════════════════════════════════════════════════
def _resolve_values(values: Dict[str, object]) -> Dict[str, object]:
    """Normalise an incoming {key: value} dict to {real_key: value}, dropping
    unsupported keys. Canonical real keys win over aliases when both target the
    same real key (so a get(include_aliases=True) round-trips to one node)."""
    resolved: Dict[str, object] = {}
    # First pass: aliases only.
    for k, v in values.items():
        if k in KEY_ALIASES:
            resolved[KEY_ALIASES[k]] = v
    # Second pass: real keys override any alias that resolved to the same key.
    for k, v in values.items():
        if k in SUPPORTED_REAL_KEYS:
            resolved[k] = v
    return resolved


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _line_start(text: str, idx: int) -> int:
    nl = text.rfind("\n", 0, idx)
    return 0 if nl < 0 else nl + 1


def _leading_ws(text: str, line_start_idx: int) -> str:
    j = line_start_idx
    n = len(text)
    while j < n and text[j] in (" ", "\t"):
        j += 1
    return text[line_start_idx:j]


def _detect_indent_unit(text: str) -> str:
    """Return one indentation unit (the leading whitespace of the first
    top-level child of kicad_pcb). Defaults to a single tab."""
    try:
        outer = _outer_open(text)
        _, _, _, kids = _children(text, outer)
        if kids:
            ls = _line_start(text, kids[0][0])
            ws = _leading_ws(text, ls)
            if ws:
                return ws
    except ValueError:
        pass
    return "\t"


def _has_newline_between(text: str, a: int, b: int) -> bool:
    return "\n" in text[a:b]


def set_board_setup(pcb_text: str, values: Dict[str, object]) -> str:
    """Return a new board text with the given setup keys updated in place.

    - Accepts real keys and friendly aliases (see KEY_ALIASES); unsupported
      keys are ignored.
    - Existing keys are replaced in place; absent keys are inserted into the
      (setup ...) block, preserving all other content and formatting.
    - If there is no (setup ...) block, a minimal one is created and inserted
      after the board's layers/general preamble.
    Unsupported / empty input returns the text unchanged."""
    resolved = _resolve_values(values)
    if not resolved:
        return pcb_text

    nl = _detect_newline(pcb_text)
    indent_unit = _detect_indent_unit(pcb_text)
    span = _find_setup(pcb_text)

    if span is None:
        block = _render_setup_block(resolved, indent_unit, nl)
        return _insert_setup_block(pcb_text, block, nl)

    setup_open, _setup_end = span
    _, _, close_idx, kids = _children(pcb_text, setup_open)
    setup_indent = _leading_ws(pcb_text, _line_start(pcb_text, setup_open))
    child_indent = setup_indent + indent_unit
    multiline = _has_newline_between(pcb_text, setup_open, close_idx)

    existing: Dict[str, Tuple[int, int]] = {}
    for a, b in kids:
        h, _, _, _ = _children(pcb_text, a)
        existing[h] = (a, b)

    ops: List[Tuple[int, int, str]] = []  # (start, end, replacement)
    inserts: List[str] = []
    for real_key, val in resolved.items():
        node = _render_node(real_key, val)
        if real_key in existing:
            a, b = existing[real_key]
            ops.append((a, b, node))
        else:
            inserts.append(node)

    if inserts:
        if multiline:
            ins_pos = _line_start(pcb_text, close_idx)
            ins_text = "".join(child_indent + node + nl for node in inserts)
        else:
            # single-line setup: space-separate before the closing paren
            ins_pos = close_idx
            ins_text = "".join(" " + node for node in inserts)
        ops.append((ins_pos, ins_pos, ins_text))

    # Apply right-to-left so earlier indices stay valid (ops never overlap).
    ops.sort(key=lambda o: o[0], reverse=True)
    out = pcb_text
    for a, b, t in ops:
        out = out[:a] + t + out[b:]
    return out


def get_board_setup_safe(pcb_text: str, include_aliases: bool = True) -> Dict[str, object]:
    """Structured-result wrapper around get_board_setup.

    Returns {"ok": True, "value": {..}} on success, or
    {"ok": False, "error": "..."} if the board is corrupt/unparseable — instead
    of raising. Pre-validates the (kicad_pcb ...) form so a truncated/unbalanced
    file is reported, not crashed on. Callers that must keep going even when the
    board is unreadable (e.g. still save schematic-side .kicad_pro settings) use
    this rather than the raising get_board_setup."""
    ok, err = validate_kicad_pcb(pcb_text)
    if not ok:
        return {"ok": False, "error": err}
    try:
        return {"ok": True, "value": get_board_setup(pcb_text, include_aliases=include_aliases)}
    except ValueError as e:  # pragma: no cover - validate_kicad_pcb catches the common cases
        return {"ok": False, "error": str(e)}


def set_board_setup_safe(pcb_text: str, values: Dict[str, object]) -> Dict[str, object]:
    """Structured-result wrapper around set_board_setup.

    Returns {"ok": True, "text": "<new board text>"} on success, or
    {"ok": False, "error": "..."} if the board is corrupt/unparseable — instead
    of raising, so a corrupt .kicad_pcb never aborts a caller that also has valid
    schematic-side settings to persist."""
    ok, err = validate_kicad_pcb(pcb_text)
    if not ok:
        return {"ok": False, "error": err}
    try:
        return {"ok": True, "text": set_board_setup(pcb_text, values)}
    except ValueError as e:  # pragma: no cover - validate_kicad_pcb catches the common cases
        return {"ok": False, "error": str(e)}


def _render_setup_block(resolved: Dict[str, object], indent_unit: str, nl: str) -> str:
    lines = [indent_unit + "(setup"]
    for real_key, val in resolved.items():
        lines.append(indent_unit * 2 + _render_node(real_key, val))
    lines.append(indent_unit + ")")
    return nl.join(lines)


# Priority order of top-level blocks that a (setup ...) normally follows.
_PREAMBLE_PRIORITY = (
    "layers", "general", "paper", "title_block",
    "generator_version", "generator", "version",
)


def _insert_setup_block(pcb_text: str, block: str, nl: str) -> str:
    """Insert a freshly-rendered (setup ...) block after the preamble blocks
    (layers/general/...) of the top-level kicad_pcb list, matching KiCad's
    natural ordering. Falls back to first-child position if none are present."""
    outer = _outer_open(pcb_text)
    _, content_start, _close, kids = _children(pcb_text, outer)

    heads: Dict[str, int] = {}
    for a, b in kids:
        h, _, _, _ = _children(pcb_text, a)
        # keep the LAST occurrence's end index for each head
        heads[h] = b

    insert_at = None
    for name in _PREAMBLE_PRIORITY:
        if name in heads:
            insert_at = heads[name]
            break
    if insert_at is None:
        insert_at = kids[0][1] if kids else content_start

    return pcb_text[:insert_at] + nl + block + pcb_text[insert_at:]


# ═══════════════════════════════════════════════════════════════════
# FILE HELPERS (Path-based load / atomic save)
# ═══════════════════════════════════════════════════════════════════
def read_pcb_text(path: Union[str, Path]) -> str:
    """Read a .kicad_pcb PRESERVING its line endings verbatim.

    Path.read_text uses universal-newline translation, which silently strips
    every ``\\r`` — so a CRLF board would be seen (and rewritten) as LF, corrupting
    line endings on Windows. Opening with newline="" disables translation so the
    file's own CRLF/LF survives round-trip through set_board_setup + _atomic_write.
    """
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _atomic_write(path: Path, text: str, backup: bool = False) -> None:
    """Write `text` to `path` atomically, PRESERVING its line endings verbatim
    (newline="" — no CRLF/LF translation, so a CRLF board stays CRLF on Windows).
    If backup=True and the file exists, a <name>.kicad_pcb.bak copy of the
    ORIGINAL is made first."""
    path = Path(path)
    if backup and path.exists():
        try:
            shutil.copy2(str(path), str(path) + ".bak")
        except Exception as e:  # pragma: no cover - best effort
            print(f"⚠️  Could not write backup for {path.name}: {e}")
    tmp = path.parent / (path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def load_board_setup(pcb_path: Union[str, Path],
                     include_aliases: bool = True) -> Dict[str, object]:
    """Read a .kicad_pcb file and return its supported setup keys."""
    text = Path(pcb_path).read_text(encoding="utf-8")
    return get_board_setup(text, include_aliases=include_aliases)


def save_board_setup(pcb_path: Union[str, Path], values: Dict[str, object],
                     backup: bool = False) -> str:
    """Read a .kicad_pcb file, update the given setup keys, and atomically write
    it back (optionally leaving a .bak). Returns the new file text."""
    p = Path(pcb_path)
    text = read_pcb_text(p)          # preserve CRLF/LF verbatim
    new_text = set_board_setup(text, values)
    _atomic_write(p, new_text, backup=backup)
    return new_text


class BoardSetupManager:
    """Thin stateful wrapper around the (setup ...) block of one .kicad_pcb.

    Typical use:
        mgr = BoardSetupManager(path)
        current = mgr.load()                       # dict of current setup keys
        mgr.set({"solder_paste_margin": -0.05})    # in-memory, alias-aware
        mgr.save(backup=True)                      # atomic write + .bak
    """

    def __init__(self, pcb_path: Union[str, Path]):
        self.path = Path(pcb_path)
        self._text: Optional[str] = None

    @property
    def text(self) -> str:
        if self._text is None:
            self._text = read_pcb_text(self.path)     # preserve CRLF/LF
        return self._text

    def load(self, include_aliases: bool = True) -> Dict[str, object]:
        """(Re)read the file from disk and return its supported setup keys."""
        self._text = read_pcb_text(self.path)          # preserve CRLF/LF
        return get_board_setup(self._text, include_aliases=include_aliases)

    def get(self, include_aliases: bool = True) -> Dict[str, object]:
        """Return the supported setup keys of the current (possibly modified)
        in-memory text without touching disk."""
        return get_board_setup(self.text, include_aliases=include_aliases)

    def set(self, values: Dict[str, object]) -> Dict[str, object]:
        """Apply `values` to the in-memory text (does NOT write). Returns the
        resulting setup keys."""
        self._text = set_board_setup(self.text, values)
        return get_board_setup(self._text)

    def save(self, values: Optional[Dict[str, object]] = None,
             backup: bool = False) -> Path:
        """Optionally apply `values`, then atomically write the text to disk.
        Returns the file path."""
        if values is not None:
            self.set(values)
        _atomic_write(self.path, self.text, backup=backup)
        return self.path


# ═══════════════════════════════════════════════════════════════════
# CLI (inspection helper; no external deps)
# ═══════════════════════════════════════════════════════════════════
def main_cli():  # pragma: no cover - convenience only
    import argparse

    ap = argparse.ArgumentParser(
        description="Read/modify the (setup ...) block of a .kicad_pcb")
    ap.add_argument("pcb", help=".kicad_pcb file")
    ap.add_argument("--set", action="append", default=[], metavar="key=value",
                    help="set a setup key (real name or alias); repeatable")
    ap.add_argument("--backup", action="store_true", help="write a .bak first")
    args = ap.parse_args()

    if not args.set:
        for k, v in load_board_setup(args.pcb).items():
            print(f"{k} = {v}")
        return

    values: Dict[str, object] = {}
    for item in args.set:
        key, _, raw = item.partition("=")
        key = key.strip()
        raw = raw.strip()
        if key in SETUP_BOOL_KEYS:
            values[key] = raw
        elif key in SETUP_COORD_KEYS:
            values[key] = tuple(float(x) for x in raw.replace(",", " ").split())
        else:
            try:
                values[key] = float(raw)
            except ValueError:
                values[key] = raw
    save_board_setup(args.pcb, values, backup=args.backup)
    print("Updated:")
    for k, v in load_board_setup(args.pcb).items():
        print(f"  {k} = {v}")


if __name__ == "__main__":  # pragma: no cover
    main_cli()
