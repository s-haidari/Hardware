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
    import LibraryManager as LM
    comps = schematic_components(sch_path)
    pin_counts = symbol_pin_counts(sch_path)
    fp_dirs = [d for d in (footprint_dirs or []) if d and Path(d).exists()]
    mdl_names = set()
    for d in (model_dirs or []):
        if d and Path(d).exists():
            mdl_names |= {p.name for p in Path(d).glob("*")
                          if p.suffix.lower() in (".step", ".stp", ".wrl")}

    findings = []

    def add(ref, severity, kind, detail):
        findings.append({"ref": ref, "severity": severity, "kind": kind, "detail": detail})

    # unannotated references (R?, U12? …)
    for c in comps:
        if c["ref"].rstrip().endswith("?"):
            add(c["ref"], "error", "unannotated", "reference designator not annotated")
    # duplicate references (ignoring the unannotated ones)
    seen: dict = {}
    for c in comps:
        if not c["ref"].endswith("?"):
            seen.setdefault(c["ref"], 0)
            seen[c["ref"]] += 1
    for ref, n in seen.items():
        if n > 1:
            add(ref, "error", "duplicate_ref", f"{n} components share reference {ref}")
    # no footprint assigned
    for c in comps:
        if not c["footprint"].strip():
            add(c["ref"], "warning", "no_footprint", "no footprint assigned")
    # no manufacturer / MPN (sourcing gap)
    for c in comps:
        ident = LM.part_identity(c["props"])
        if not ident["manufacturer"] and not LM.strict_mpn(c["props"]):
            add(c["ref"], "info", "no_mpn", "no manufacturer / MPN — cannot be sourced")

    # symbol-pin vs footprint-pad mismatch + missing 3D model (best-effort)
    checked = unresolved = 0
    if fp_dirs:
        for c in comps:
            if not c["footprint"].strip():
                continue
            pad_n, models = _footprint_pads_and_models(fp_dirs, c["footprint"])
            if pad_n is None:
                unresolved += 1
                continue
            checked += 1
            pins = pin_counts.get(c["lib_id"])
            if pins and pad_n and pins != pad_n:
                add(c["ref"], "error", "pin_pad_mismatch",
                    f"symbol {pins} pins vs footprint {pad_n} pads ({c['footprint']})")
            if model_dirs is not None:
                if not models:
                    add(c["ref"], "info", "no_3d_model", f"footprint has no 3D model ({c['footprint']})")
                elif mdl_names and not any(Path(m).name in mdl_names for m in models):
                    add(c["ref"], "info", "missing_3d_model",
                        f"3D model not found on disk ({', '.join(Path(m).name for m in models)})")

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
        "project": Path(sch_path).stem,
        "components": len(comps),
        "healthy": max(0, healthy),
        "counts": {"by_severity": by_sev, "by_kind": by_kind},
        "findings": sorted(findings, key=lambda f: ({"error": 0, "warning": 1, "info": 2}[f["severity"]],
                                                    f["kind"], f["ref"])),
        "checked_footprints": checked,
        "unresolved_footprints": unresolved,
    }


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
