"""
netclasses.py — read/update the vault netclass standard (the canonical
``net-classes.yaml`` that mirrors the vault page 'Net Class Colors & Styles').

Round-trips with ruamel.yaml so the file's header comments, key order, and
formatting survive an edit — important for a curated standard file.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)  # matches the file's "  - key:" style


def load(path: Path) -> CommentedMap:
    return _yaml.load(path.read_text(encoding="utf-8"))


def dump(data: Any) -> str:
    buf = io.StringIO()
    _yaml.dump(data, buf)
    return buf.getvalue()


def save(path: Path, data: Any) -> None:
    path.write_text(dump(data), encoding="utf-8", newline="\n")


def to_classes(data: CommentedMap) -> list[dict]:
    return [dict(c) for c in data.get("classes", [])]


def replace_classes(data: CommentedMap, classes: list[dict]) -> CommentedMap:
    """Swap in a new ``classes`` list, preserving ``meta`` and the file header."""
    seq = CommentedSeq()
    for c in classes:
        m = CommentedMap()
        for k, v in c.items():
            m[k] = v
        seq.append(m)
    data["classes"] = seq
    return data
