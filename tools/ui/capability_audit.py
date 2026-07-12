"""Deterministic backend-vs-UI coverage — the first-pass alarm against the recurring
"forgot a feature that already exists in the code" fault.

Lists every PUBLIC backend function (def not starting with "_") that is NOT referenced
anywhere in the UI, grouped by module. The UI source is the styled features + shell (it
was tools/ui/bare.py until the Phase-3 flip retired the legacy UI). Not every hit is a
real gap — internal helpers legitimately aren't in the UI — but EACH one must be
*accounted for* before a feature is called complete: either surface it, or confirm it's
an internal helper. The authoritative, human-judged map is docs/CAPABILITIES.md.

Run:
    .venv/bin/python tools/ui/capability_audit.py            # human report
    .venv/bin/python tools/ui/capability_audit.py --json     # machine-readable

Exit code is always 0 (it's a report, not a pass/fail — judgment is required per line).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[1]
_BARE = _TOOLS / "ui" / "bare.py"
# The FROZEN parity source-of-truth. bare.py was the live reference the styled features
# were driven to parity against; once every feature reached parity + was drive-audited, the
# bare panel→capability map was frozen here and bare.py deleted (Phase-3 flip). The parity
# gate then locks the styled features against this frozen capability set forever — a
# regression lock that survives without the ~4400-line legacy UI. Regenerate ONLY from a
# restored bare.py (git history) if the contract itself must change.
_BASELINE = _TOOLS / "ui" / "parity_baseline.json"


def _load_baseline():
    """The frozen bare panel→capability map, or None if bare.py is still the live source."""
    if not _BASELINE.exists():
        return None
    return json.loads(_BASELINE.read_text(encoding="utf-8"))

# Names that are obviously not user-facing capabilities even though they're public.
_HELPER_HINT = re.compile(r"^(parse_|extract_|_|make_|to_|from_|is_|has_|get_|iter_|"
                          r"norm|fmt|coerce|render_|summar|classify|default_|build_database|"
                          r"list_|load_|save_config|read_|write_)")


def _ui_text() -> str:
    """The UI source the deterministic alarm scans for backend references. bare.py while it
    exists; after its deletion, the styled UI (every feature + shell module) IS the UI."""
    if _BARE.exists():
        return _BARE.read_text(encoding="utf-8")
    parts = []
    for p in sorted((_TOOLS / "ui").rglob("*.py")):
        if p.name == "capability_audit.py":
            continue
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _modules_imported_by_ui(ui_text: str) -> list[str]:
    """Backend modules bare.py imports (top-level + function-local) that exist on disk."""
    names: set[str] = set()
    for m in re.finditer(r"^\s*import\s+([a-zA-Z_][\w]*)(?:\s+as\s+\w+)?\s*$",
                         ui_text, re.M):
        names.add(m.group(1))
    for m in re.finditer(r"^\s*from\s+([a-zA-Z_][\w]*)\s+import\b", ui_text, re.M):
        names.add(m.group(1))
    out = []
    for n in sorted(names):
        if n in ("sys", "threading", "traceback", "os", "json", "re", "csv", "io"):
            continue
        if (_TOOLS / f"{n}.py").exists():
            out.append(n)
    return out


def _public_funcs(module: str) -> list[str]:
    text = (_TOOLS / f"{module}.py").read_text(encoding="utf-8")
    return re.findall(r"^def ([a-z][a-zA-Z0-9_]*)\(", text, re.M)


def audit() -> dict:
    ui = _ui_text()
    report = {}
    for mod in _modules_imported_by_ui(ui):
        funcs = _public_funcs(mod)
        unref = [f for f in funcs if not re.search(rf"\b{re.escape(f)}\b", ui)]
        # split likely-helper vs likely-capability by name shape (a hint, not a verdict)
        likely_caps = [f for f in unref if not _HELPER_HINT.match(f)]
        report[mod] = {"public": len(funcs), "unreferenced": unref,
                       "likely_capabilities": likely_caps}
    return report


# ── styled-vs-bare PARITY (spec §9) ──────────────────────────────────────────
# The explicit bare-panel ↔ styled-feature pairing. Every *_panel in bare.py must appear
# here (parity() fails loud otherwise).
_PAIRING = {
    "_git_panel": "git",
    "_lib_panel": "library",
    "_proj_panel": "projects",
    "_bench_panel": "bench",
    "_settings_panel": "settings",
}

# Per-feature internal-guard exemptions: bare-side symbols that are NOT user capabilities,
# so a migration is never told to surface them. Each is justified.
_EXEMPT = {
    "git": {
        "have_git",               # a status STRING in the repo card ("found on PATH"), not an action
        "guard_no_corrupt_kicad", # applied IMPLICITLY by nd_git.commit's guarded path (styled uses commit)
    },
    "projects": {
        "mils_to_mm",             # pure mils↔mm conversion helpers, DUPLICATED in ui.util (the
        "mm_to_mils",             # None-safe copies the PCB Setup units toggle actually calls); the
                                  # units toggle IS the surfaced capability, psm's copies are internal.
    },
    "bench": {
        # The single "Write Authority Bundle" action (write_authority, surfaced in the Exports
        # tab) emits EVERY one of these serializers to the output folder in one pass (verified:
        # stm32_authority.write_authority writes .yaml/.json/.tsv/.kicad_sym/.csv/.md/switchmap
        # .json/.h/wiring.md) — the bundle IS the surfaced capability; the individual to_* are
        # the functions it calls, not separately-omitted features.
        "to_csv", "to_kicad_symbol", "to_markdown", "to_switchmap_c", "to_switchmap_json",
        "to_wiring_md", "to_yaml", "serializable", "raw_tsv",
        # STM32-DB provisioning + status: the DB rebuild (build_database + default_cubemx_source)
        # is a one-time machine-setup action surfaced in Settings (Set Up This Machine), not a
        # per-package bench capability; package_count is the DB status stat, surfaced in Settings.
        "build_database", "default_cubemx_source", "package_count",
    },
}

# Bus commands a styled feature emits that the shell (the Services layer) handles by calling
# a backend module — so that module's capabilities are SURFACED via the bus, not the feature
# module. Maps command → the backend modules it satisfies.
_BUS_ALLOW = {
    "app.check_updates": {"nd_updater"},
}


def _module_exists(mod: str) -> bool:
    return (_TOOLS / f"{mod}.py").exists()


def _feature_path(fid: str) -> Path:
    return _TOOLS / "ui" / "features" / f"{fid}.py"


def _module_alias_map(tree: ast.AST) -> dict:
    """Top-level `import X [as Y]` aliases (Y -> X) for backend modules that exist on disk."""
    amap = {}
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                if _module_exists(a.name):
                    amap[a.asname or a.name] = a.name
    return amap


def _local_alias_map(node: ast.AST) -> dict:
    """All `import X [as Y]` aliases anywhere under `node` (function-local imports) — this is
    what resolves bare's per-panel `import nd_git as G` (and `as NG` / `as GIT` elsewhere)."""
    amap = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Import):
            for a in n.names:
                if _module_exists(a.name):
                    amap[a.asname or a.name] = a.name
    return amap


def _backend_symbols(node: ast.AST, amap: dict) -> dict:
    """{module: {attr,...}} for `alias.attr` accesses where alias→a backend module and attr
    is a PUBLIC function of that module. Resolves per-scope aliases (the v2.1 fix)."""
    used: dict = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            mod = amap.get(n.value.id)
            if mod and not n.attr.startswith("_"):
                used.setdefault(mod, set()).add(n.attr)
    out = {}
    for mod, attrs in used.items():
        keep = attrs & set(_public_funcs(mod))
        if keep:
            out[mod] = keep
    return out


def _bus_emits(node: ast.AST) -> set:
    """Command strings passed to `*.bus.emit('cmd', …)` anywhere under `node`."""
    cmds = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "emit" and n.args
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            cmds.add(n.args[0].value)
    return cmds


def _panel_node(bare_tree: ast.AST, name: str):
    for node in ast.walk(bare_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _bare_panels(bare_tree: ast.AST) -> list:
    """Every category `_<cat>_panel` builder in bare.py (the set that must be paired). Excludes
    the bare `_panel(*groups)` LAYOUT helper — a category panel always has a prefix."""
    return sorted(n.name for n in ast.walk(bare_tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name.endswith("_panel") and n.name != "_panel")


def _bare_panel_symbols(panel_name: str) -> dict:
    """Alias-resolved {module: {public attrs}} the bare panel closure references. Reads the
    FROZEN baseline (the source-of-truth after bare.py's deletion); falls back to parsing a
    live bare.py when the baseline is absent (pre-freeze). The alias map is module-level bare
    imports (e.g. `import LibraryManager as LM`) merged with the panel's own local imports
    (`import nd_git as G`) — locals win."""
    base = _load_baseline()
    if base is not None:
        return {mod: set(attrs) for mod, attrs in base["symbols"].get(panel_name, {}).items()}
    bare_tree = ast.parse(_BARE.read_text(encoding="utf-8"))
    node = _panel_node(bare_tree, panel_name)
    if node is None:
        return {}
    amap = {**_module_alias_map(bare_tree), **_local_alias_map(node)}
    return _backend_symbols(node, amap)


def _feature_closure_trees(fid: str, seen: set | None = None) -> list:
    """features/<fid>.py plus every sibling feature module it imports (transitively within
    features/), as parsed ASTs — the styled side is the UNION over this closure (so a
    capability reached via a sibling like library_preview is not a false omission)."""
    seen = seen if seen is not None else set()
    path = _feature_path(fid)
    if fid in seen or not path.exists():
        return []
    seen.add(fid)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    trees = [tree]
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level == 1:
            sibs = [n.module.split(".")[0]] if n.module else [a.name for a in n.names]
            for sib in sibs:
                if _feature_path(sib).exists():
                    trees += _feature_closure_trees(sib, seen)
    return trees


def _styled_feature_symbols(fid: str) -> dict:
    """Alias-resolved {module: {attrs}} the styled feature surfaces = the union over its
    transitive module closure, PLUS every capability its bus emits satisfy (spec §9)."""
    used: dict = {}
    for tree in _feature_closure_trees(fid):
        amap = {**_module_alias_map(tree), **_local_alias_map(tree)}
        for mod, attrs in _backend_symbols(tree, amap).items():
            used.setdefault(mod, set()).update(attrs)
        for cmd in _bus_emits(tree):
            for mod in _BUS_ALLOW.get(cmd, ()):
                if _module_exists(mod):
                    used.setdefault(mod, set()).update(_public_funcs(mod))
    return used


def _check_pairing() -> dict:
    """Assert every bare *_panel is paired and every pairing target is a real feature module.
    Fail loud (spec §9) rather than silently skip an unpaired panel (a false all-clear).
    Panels come from the frozen baseline (post-delete) or a live bare.py (pre-freeze)."""
    base = _load_baseline()
    if base is not None:
        panels = set(base["panels"])
    else:
        bare_tree = ast.parse(_BARE.read_text(encoding="utf-8"))
        panels = set(_bare_panels(bare_tree))
    unpaired = panels - set(_PAIRING)
    if unpaired:
        raise AssertionError(f"unpaired bare panels (add to _PAIRING): {sorted(unpaired)}")
    missing_features = [fid for fid in _PAIRING.values() if not _feature_path(fid).exists()]
    if missing_features:
        raise AssertionError(f"pairing targets a missing feature module: {missing_features}")
    return dict(_PAIRING)


def parity() -> dict:
    """Per-feature styled-vs-bare capability parity (spec §9). For each paired panel:
    {bare user-capability symbols} − {styled transitive closure ∪ bus allowlist} − exempt.
    The omission list a migration drives to zero. Flow/UX parity (a preview flow reusing
    already-surfaced symbols) is NOT measured here — that is the drive_audit gate."""
    _check_pairing()
    report = {}
    for panel_name, fid in _PAIRING.items():
        bare_used = _bare_panel_symbols(panel_name)
        styled_used = _styled_feature_symbols(fid)
        exempt = _EXEMPT.get(fid, set())
        missing = {}
        for mod, attrs in bare_used.items():
            gap = sorted((attrs - styled_used.get(mod, set())) - exempt)
            if gap:
                missing[mod] = gap
        report[fid] = {"missing": missing,
                       "omissions": sum(len(v) for v in missing.values())}
    return report


def main(argv: list[str]) -> int:
    if "--parity" in argv:
        rep = parity()
        if "--json" in argv:
            print(json.dumps(rep, indent=2))
            return 0
        print("STYLED-vs-BARE CAPABILITY PARITY  (bare panel capabilities not surfaced in the styled feature)")
        print("=" * 78)
        for fid, v in rep.items():
            mark = "✓ at parity" if v["omissions"] == 0 else f"⚠ {v['omissions']} omission(s)"
            print(f"\n{fid}: {mark}")
            for mod, syms in v["missing"].items():
                print(f"    {mod}: " + ", ".join(syms))
        print("\n" + "=" * 78)
        print("Drive-audit (not this harness) gates FLOW/UX parity (a preview flow that reuses "
              "already-surfaced\nsymbols is invisible to a symbol delta). Authoritative map: docs/CAPABILITIES.md")
        return 0

    rep = audit()
    if "--json" in argv:
        print(json.dumps(rep, indent=2))
        return 0
    total_unref = sum(len(v["unreferenced"]) for v in rep.values())
    total_caps = sum(len(v["likely_capabilities"]) for v in rep.values())
    print("BACKEND-vs-UI COVERAGE  (unreferenced public functions per module)")
    print("=" * 70)
    for mod, v in rep.items():
        if not v["unreferenced"]:
            continue
        print(f"\n{mod}: {v['public']} public, {len(v['unreferenced'])} not in UI")
        if v["likely_capabilities"]:
            print("  ⚠ LIKELY CAPABILITIES (account for each — surface or confirm helper):")
            for f in v["likely_capabilities"]:
                print(f"      {f}")
        helpers = [f for f in v["unreferenced"] if f not in v["likely_capabilities"]]
        if helpers:
            print("  (likely helpers: " + ", ".join(helpers[:12])
                  + (" …" if len(helpers) > 12 else "") + ")")
    print("\n" + "=" * 70)
    print(f"{total_unref} unreferenced public functions; "
          f"{total_caps} flagged as LIKELY CAPABILITIES to account for.")
    print("Authoritative judged map: docs/CAPABILITIES.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
