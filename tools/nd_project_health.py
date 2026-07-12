"""Project Health Audit — a pure-Python health check for ANY KiCad 6+/7+ project.

No kicad-cli, no NETDECK authority: it reads the schematic (and, when footprint
libraries are pointed at it, the footprints) and reports the problems that bite a
board before fab — unannotated or duplicated reference designators, components with
no footprint, symbol-pin vs footprint-pad count mismatches, missing 3D models, and
parts with no manufacturer / MPN. Reuses the same identity logic that groups the
library and the footprint parser from fp_render.

Everything here is read-only and returns plain dicts, so it is trivial to test and
to surface in any UI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional


# ── schematic parsing (shared shape with the smart BOM) ──────────────────────
def _load_sexpr(path):
    from fp_render import parse_sexpr
    return parse_sexpr(Path(path).read_text(encoding="utf-8", errors="replace"))


def schematic_components(sch_path) -> List[dict]:
    """Real placed components in a .kicad_sch: [{ref, value, footprint, lib_id,
    props}]. Power/virtual symbols (#PWR…, power:*) are excluded."""
    root = _load_sexpr(sch_path)
    if not root or root[0] != "kicad_sch":
        return []
    out = []
    for node in root[1:]:
        if not (isinstance(node, list) and node and node[0] == "symbol"):
            continue
        lib_id, props = "", {}
        for c in node[1:]:
            if not (isinstance(c, list) and c):
                continue
            if c[0] == "lib_id" and len(c) > 1:
                lib_id = c[1]
            elif c[0] == "property" and len(c) > 2:
                props[c[1]] = c[2]
        ref = props.get("Reference", "")
        if not ref or ref.startswith("#") or lib_id.lower().startswith("power:"):
            continue
        out.append({"ref": ref, "value": props.get("Value", ""),
                    "footprint": props.get("Footprint", ""), "lib_id": lib_id, "props": props})
    return out


def _count_pins(node) -> int:
    n = 0
    for c in node:
        if isinstance(c, list) and c:
            n += 1 if c[0] == "pin" else _count_pins(c)
    return n


def symbol_pin_counts(sch_path) -> dict:
    """lib_id -> number of pins, from the (lib_symbols) cache the schematic embeds.
    Pins are counted across the symbol's unit sub-symbols."""
    root = _load_sexpr(sch_path)
    if not root:
        return {}
    libsym = next((c for c in root[1:]
                   if isinstance(c, list) and c and c[0] == "lib_symbols"), None)
    if not libsym:
        return {}
    counts = {}
    for sym in libsym[1:]:
        if isinstance(sym, list) and sym and sym[0] == "symbol" and len(sym) > 1:
            counts[sym[1]] = _count_pins(sym)
    return counts


# ── footprint resolution (best-effort, when libraries are pointed at us) ─────
def _footprint_pads_and_models(fp_dirs, fp_ref):
    """Given 'Nickname:NAME' and candidate footprint directories, return
    (distinct_pad_count, [model_filenames]) or (None, None) if unresolvable."""
    if not fp_ref or ":" not in fp_ref:
        return None, None
    name = fp_ref.split(":", 1)[1]
    for d in fp_dirs:
        cand = Path(d) / f"{name}.kicad_mod"
        if not cand.exists():
            # some libs nest as <Nickname>.pretty/<name>.kicad_mod
            for pretty in Path(d).glob("*.pretty"):
                alt = pretty / f"{name}.kicad_mod"
                if alt.exists():
                    cand = alt
                    break
        if cand.exists():
            text = cand.read_text(encoding="utf-8", errors="replace")
            pads = set(re.findall(r'\(pad\s+"([^"]+)"', text))
            pads.discard("")                       # unnumbered mechanical pads
            models = [Path(m).name for m in re.findall(r'\(model\s+"?([^"\s)]+)', text)]
            return len(pads), models
    return None, None


def audit_schematic(sch_path, footprint_dirs: Optional[list] = None,
                    model_dirs: Optional[list] = None) -> dict:
    """Health findings for a KiCad schematic. Optional footprint_dirs enables the
    symbol-pin vs footprint-pad check; model_dirs enables the missing-3D-model check.

    Returns {project, components, counts, findings:[{ref, severity, kind, detail}],
    checked_footprints, unresolved_footprints}. Severity is 'error' | 'warning' |
    'info'. Read-only."""
    comps = schematic_components(sch_path)
    pin_counts = symbol_pin_counts(sch_path)
    au = _audit_components(comps, pin_counts, footprint_dirs, model_dirs)
    au["project"] = Path(sch_path).stem
    au["sheets"] = 1
    return au


def audit_project(sch_paths, footprint_dirs: Optional[list] = None,
                  model_dirs: Optional[list] = None) -> dict:
    """PROJ-05: health across EVERY schematic sheet in a project — collect all
    components + pin counts first, then run the checks once so duplicates are
    caught project-wide (the root-only audit missed cross-sheet collisions).
    Same result shape as audit_schematic, plus a `sheets` count."""
    comps: list = []
    pin_counts: dict = {}
    n = 0
    seen_sig = set()
    dup_findings = []
    for sp in (sch_paths or []):
        n += 1
        try:
            sheet_comps = schematic_components(sp)
            pin_counts.update(symbol_pin_counts(sp))
        except Exception:  # noqa: BLE001
            continue
        # INTRA-sheet duplicate detection: two components sharing a reference on
        # the SAME sheet is a genuine annotation error. (Cross-sheet base-ref
        # collisions are not reliable and are deliberately not flagged.)
        within: dict = {}
        for c in sheet_comps:
            if not c["ref"].endswith("?"):
                within[c["ref"]] = within.get(c["ref"], 0) + 1
        for ref, k in within.items():
            if k > 1:
                dup_findings.append({"ref": ref, "severity": "error", "kind": "duplicate_ref",
                                     "detail": f"{k} components share reference {ref} on one sheet"})
        # Dedupe a component that appears in more than one sheet file (a reused
        # hierarchical sheet) by identity, so it isn't counted twice.
        for c in sheet_comps:
            sig = (c["ref"], c.get("value", ""), c.get("footprint", ""), c.get("lib_id", ""))
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            comps.append(c)
    au = _audit_components(comps, pin_counts, footprint_dirs, model_dirs,
                           detect_duplicates=False, extra_findings=dup_findings)
    au["project"] = "project"
    au["sheets"] = n
    return au


# ── check registry ───────────────────────────────────────────────────────────
# Each check is a callable `check(ctx) -> None` that reads the shared AuditContext
# and appends findings via ctx.add(...). New checks are added by @register_check
# rather than by editing the audit body, so the audit core stays a stable loop and
# the set of checks is introspectable (CHECK_KINDS). Order of registration is the
# order findings are produced (before the final stable sort), preserving output.
_CHECKS: list = []


def register_check(fn):
    """Register a `check(ctx)` callable into the audit pipeline. Returns the
    function unchanged so it doubles as a decorator."""
    _CHECKS.append(fn)
    return fn


class _AuditContext:
    """Everything a check needs: the collected components, the embedded symbol
    pin counts, the resolved footprint/model directories, and an `add()` sink.
    `detect_duplicates` gates the same-list duplicate pass (the project audit
    runs its own intra-sheet detection instead)."""

    def __init__(self, comps, pin_counts, fp_dirs, model_dirs, mdl_names,
                 detect_duplicates, findings):
        self.comps = comps
        self.pin_counts = pin_counts
        self.fp_dirs = fp_dirs
        self.model_dirs = model_dirs
        self.mdl_names = mdl_names
        self.detect_duplicates = detect_duplicates
        self.findings = findings
        self.checked_footprints = 0
        self.unresolved_footprints = 0

    def add(self, ref, severity, kind, detail):
        self.findings.append({"ref": ref, "severity": severity, "kind": kind, "detail": detail})


@register_check
def _check_unannotated(ctx):
    # unannotated references (R?, U12? …)
    for c in ctx.comps:
        if c["ref"].rstrip().endswith("?"):
            ctx.add(c["ref"], "error", "unannotated", "reference designator not annotated")


@register_check
def _check_duplicate_ref(ctx):
    # duplicate references (ignoring the unannotated ones)
    if not ctx.detect_duplicates:
        return
    seen: dict = {}
    for c in ctx.comps:
        if not c["ref"].endswith("?"):
            seen.setdefault(c["ref"], 0)
            seen[c["ref"]] += 1
    for ref, n in seen.items():
        if n > 1:
            ctx.add(ref, "error", "duplicate_ref", f"{n} components share reference {ref}")


@register_check
def _check_no_footprint(ctx):
    # no footprint assigned
    for c in ctx.comps:
        if not c["footprint"].strip():
            ctx.add(c["ref"], "warning", "no_footprint", "no footprint assigned")


@register_check
def _check_no_mpn(ctx):
    # no manufacturer / MPN (sourcing gap)
    import LibraryManager as LM
    for c in ctx.comps:
        ident = LM.part_identity(c["props"])
        if not ident["manufacturer"] and not LM.strict_mpn(c["props"]):
            ctx.add(c["ref"], "info", "no_mpn", "no manufacturer / MPN — cannot be sourced")


@register_check
def _check_footprint_pads_and_models(ctx):
    # symbol-pin vs footprint-pad mismatch + missing 3D model (best-effort)
    if not ctx.fp_dirs:
        return
    for c in ctx.comps:
        if not c["footprint"].strip():
            continue
        pad_n, models = _footprint_pads_and_models(ctx.fp_dirs, c["footprint"])
        if pad_n is None:
            ctx.unresolved_footprints += 1
            continue
        ctx.checked_footprints += 1
        pins = ctx.pin_counts.get(c["lib_id"])
        if pins and pad_n and pins != pad_n:
            ctx.add(c["ref"], "error", "pin_pad_mismatch",
                    f"symbol {pins} pins vs footprint {pad_n} pads ({c['footprint']})")
        if ctx.model_dirs is not None:
            if not models:
                ctx.add(c["ref"], "info", "no_3d_model", f"footprint has no 3D model ({c['footprint']})")
            elif ctx.mdl_names and not any(Path(m).name in ctx.mdl_names for m in models):
                ctx.add(c["ref"], "info", "missing_3d_model",
                        f"3D model not found on disk ({', '.join(Path(m).name for m in models)})")


def _audit_components(comps, pin_counts, footprint_dirs=None, model_dirs=None,
                      detect_duplicates=True, extra_findings=None) -> dict:
    """The shared check core over an already-collected component list.

    Runs every check registered via @register_check, in registration order, over a
    shared _AuditContext. `detect_duplicates=False` skips the same-list
    duplicate-reference pass — the project audit does its own INTRA-sheet detection
    instead, because a base reference read across hierarchical sheet files is not a
    reliable duplicate signal (KiCad resolves real refs per instance).
    `extra_findings` are merged in."""
    fp_dirs = [d for d in (footprint_dirs or []) if d and Path(d).exists()]
    mdl_names = set()
    for d in (model_dirs or []):
        if d and Path(d).exists():
            mdl_names |= {p.name for p in Path(d).glob("*")
                          if p.suffix.lower() in (".step", ".stp", ".wrl")}

    findings = list(extra_findings or [])
    ctx = _AuditContext(comps, pin_counts, fp_dirs, model_dirs, mdl_names,
                        detect_duplicates, findings)
    for check in _CHECKS:
        check(ctx)
    checked = ctx.checked_footprints
    unresolved = ctx.unresolved_footprints

    # collapse identical findings (duplicate-ref'd components would otherwise repeat
    # the same per-ref note once per instance)
    seen_f, uniq = set(), []
    for f in findings:
        k = (f["ref"], f["kind"], f["detail"])
        if k not in seen_f:
            seen_f.add(k)
            uniq.append(f)
    findings = uniq

    by_sev = {"error": 0, "warning": 0, "info": 0}
    by_kind: dict = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
    healthy = len({c["ref"] for c in comps}) - len({f["ref"] for f in findings})
    return {
        "project": "",
        "components": len(comps),
        "healthy": max(0, healthy),
        "counts": {"by_severity": by_sev, "by_kind": by_kind},
        "findings": sorted(findings, key=lambda f: ({"error": 0, "warning": 1, "info": 2}[f["severity"]],
                                                    f["kind"], f["ref"])),
        "checked_footprints": checked,
        "unresolved_footprints": unresolved,
    }


# ── PROJ-06: safe one-click fixes ────────────────────────────────────────────
# Only deterministic, reversible fixes belong here. A fixer is registered per
# finding-kind in _FIXERS (kind -> callable), so `autofixable()` derives the
# fixable set from the registry — no duplicated literal to drift out of sync.
# The 'unannotated' fixer is registered at the bottom (its `annotate_project`
# callable is defined below); the Library field-fill path lives in the dedicated
# nd_library_fill module, whose fixes are surfaced through the Health panel's
# Fix-All preview, not through this in-file registry.
_FIXERS: dict = {}


def register_fixer(kind, fixer):
    """Register `fixer` as the deterministic auto-fix for `kind`. Returns the
    fixer so it can double as a decorator target."""
    _FIXERS[kind] = fixer
    return fixer


def autofixable_kinds() -> set:
    """The finding-kinds with a registered in-file fixer (the single source of
    truth `autofixable()` filters against)."""
    return set(_FIXERS)


_REF_TOKEN = re.compile(r'"([A-Za-z_]+)\?"')
# Existing annotated refs live in TWO forms: the instance `(reference "R1")` and
# the display `(property "Reference" "R1")`. Seed the used-set from BOTH — missing
# the property form let a new R? reuse an existing number and duplicate a
# designator on a legacy / instances-less file.
_ANNOTATED_REF = re.compile(r'\(\s*reference\s+"([A-Za-z_]+\d+)"')
_PROP_ANNOTATED = re.compile(r'\(property\s+"Reference"\s+"([A-Za-z_]+\d+)"')
_PROP_REF = re.compile(r'\(property\s+"Reference"\s+"([A-Za-z_]+)\?"')


def _used_refs(text: str) -> set:
    return set(_ANNOTATED_REF.findall(text)) | set(_PROP_ANNOTATED.findall(text))


def autofixable(findings) -> list:
    """The subset of audit findings the app can safely auto-fix — i.e. those whose
    `kind` has a registered fixer (see _FIXERS / register_fixer)."""
    fixable = autofixable_kinds()
    return [f for f in (findings or []) if f.get("kind") in fixable]


def _symbol_spans(text: str):
    """(start, end) of every top-level `(symbol ...)` block, by paren matching that
    IGNORES parens inside quoted strings (a stray '(' in a Value/Description would
    otherwise desync the depth counter and truncate the block). Nested sub-symbols
    are skipped because the scan resumes past each match."""
    spans = []
    i = 0
    n = len(text)
    while True:
        j = text.find("(symbol", i)
        if j < 0:
            break
        depth = 0
        k = j
        in_str = False
        escaped = False
        while k < n:
            c = text[k]
            if in_str:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        spans.append((j, k + 1))
        i = k + 1
    return spans


def _annotate_text(text: str, used=None):
    """Assign the next free `<prefix><n>` to every unannotated reference, scoped to
    each symbol block so a symbol's property + instance reference get the SAME
    number. Returns (new_text, symbols_fixed). Pure — no I/O."""
    used = set(used or ())
    out = []
    last = 0
    count = 0
    for a, b in _symbol_spans(text):
        block = text[a:b]
        m = _PROP_REF.search(block)
        if not m:
            continue
        prefix = m.group(1)
        n = 1
        while f"{prefix}{n}" in used:
            n += 1
        used.add(f"{prefix}{n}")
        out.append(text[last:a])
        out.append(block.replace(f'"{prefix}?"', f'"{prefix}{n}"'))
        last = b
        count += 1
    out.append(text[last:])
    return "".join(out), count


def annotate_project(sch_paths, apply: bool = False) -> int:
    """Annotate unannotated references across every sheet, numbering project-wide
    so no two sheets collide. Backs each edited file up (.bak) before writing when
    apply=True; a dry run reports how many WOULD change. Returns the count."""
    paths = [Path(p) for p in (sch_paths or [])]
    used = set()
    for p in paths:                                   # seed with already-numbered refs
        try:
            used |= _used_refs(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    total = 0
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        new, n = _annotate_text(text, used)
        if not n:
            continue
        used |= _used_refs(new)                        # keep the fresh numbers reserved
        if not apply:
            total += n                                 # dry run: report would-change count
            continue
        if new != text:
            try:
                p.with_suffix(p.suffix + ".bak").write_text(text, encoding="utf-8")
                p.write_text(new, encoding="utf-8")
                total += n                             # count only what actually persisted
            except Exception as e:  # noqa: BLE001
                print(f"annotate_project: failed to write {p}: {e}")
    return total


# Register the deterministic fixers. `autofixable()` / autofixable_kinds() derive
# the fixable set from this map, so adding a fixer here (and nothing else) is what
# grows the auto-fixable kinds — the literal that used to drift is gone.
register_fixer("unannotated", annotate_project)


def audit_report_markdown(audit: dict) -> str:
    """A shareable markdown report from an audit_schematic result."""
    s = audit["counts"]["by_severity"]
    L = [f"# Project Health — {audit['project']}", "",
         f"**{audit['healthy']} / {audit['components']} components healthy** — "
         f"{s['error']} errors, {s['warning']} warnings, {s['info']} notes.", ""]
    if audit["unresolved_footprints"]:
        L.append(f"*(pin/pad + 3D checked on {audit['checked_footprints']} footprints; "
                 f"{audit['unresolved_footprints']} not resolvable from the given libraries)*")
        L.append("")
    order = {"error": "Errors", "warning": "Warnings", "info": "Notes"}
    for sev, title in order.items():
        rows = [f for f in audit["findings"] if f["severity"] == sev]
        if rows:
            L += [f"## {title} ({len(rows)})", ""]
            L += [f"- **{f['ref']}** — {f['detail']}" for f in rows]
            L.append("")
    if not audit["findings"]:
        L.append("No issues found.")
    return "\n".join(L) + "\n"
