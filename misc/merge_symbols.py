#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_symbols.py
Safely append top-level (symbol ...) blocks from a source .kicad_sym
into a target MySymbols.kicad_sym, preserving valid S-expression structure.
"""

import sys
import os

def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def ensure_target_header(target_path):
    if not os.path.exists(target_path):
        header = '(kicad_symbol_lib (version 20211014) (generator "merge_symbols.py"))\n)\n'
        write_text(target_path, header)

def extract_symbol_blocks(src_text):
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

def insert_blocks_into_target(target_text, blocks):
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
        return f'(kicad_symbol_lib (version 20211014) (generator "merge_symbols.py"))\n{body}\n)\n'
    return target_text[:last_close] + "\n" + "\n".join(blocks) + "\n" + target_text[last_close:]

def main():
    if len(sys.argv) < 3:
        print("Usage: merge_symbols.py <TARGET_MySymbols.kicad_sym> <SOURCE.kicad_sym> [<SOURCE2.kicad_sym> ...]")
        sys.exit(1)
    target = sys.argv[1]
    sources = sys.argv[2:]
    ensure_target_header(target)
    target_text = read_text(target)
    total_blocks = []
    for src in sources:
        src_text = read_text(src)
        blocks = extract_symbol_blocks(src_text)
        if blocks:
            total_blocks.extend(blocks)
        elif "(symbol" in src_text:
            total_blocks.append(src_text.strip())
    if not total_blocks:
        print("No symbols found; target unchanged.")
        sys.exit(0)
    new_text = insert_blocks_into_target(target_text, total_blocks)
    write_text(target, new_text)
    print(f"Merged {len(total_blocks)} symbol(s) into {target}")

if __name__ == "__main__":
    main()