"""Fill project component fields from the local Library — pure logic (no Qt).

Composes the existing LibraryManager + nd_project_health helpers into a
match → plan → write pipeline: index the Library's parts, match each project
component (exact by symbol/MPN, fuzzy by value+footprint), build a reviewable
plan of per-field changes, and apply a user-selected subset safely into the
`.kicad_sch` (.bak backup + atomic replace).

Property keys written into the PROJECT schematic (NOT LibraryManager's
`_ENRICH_PROPERTY` map, which routes MPN → Value):

    MPN → "MPN", manufacturer → "Manufacturer", datasheet → "Datasheet",
    description → "Description", footprint → "Footprint".

Everything here is read-only until `apply_fill_plan`/`write_fields_to_sheet`,
and every file read/write is utf-8.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

import LibraryManager as LM
import nd_project_health as H


# Field → the property name carried in the PROJECT schematic.
FILL_PROPERTY = {
    "mpn": "MPN",
    "manufacturer": "Manufacturer",
    "datasheet": "Datasheet",
    "description": "Description",
    "footprint": "Footprint",
}


# ── Task 1: Library index for matching ───────────────────────────────────────
def library_parts(cfg: dict) -> List[dict]:
    """Index the Library's symbol file into records used for matching.

    Each record:
        {"name", "mpn", "manufacturer", "datasheet", "description",
         "footprint" (stem, nickname stripped), "value"}
    Empty list when the symbol library is absent/unset.
    """
    raw = (cfg or {}).get("SymbolLib", "")
    if not raw:
        return []
    sym_path = Path(raw)
    if not sym_path.is_file():
        return []
    text = sym_path.read_text(encoding="utf-8", errors="replace")
    out: List[dict] = []
    for block in LM.extract_symbol_blocks(text):
        name = LM.extract_symbol_name(block)
        props = LM.extract_symbol_properties(block)
        ident = LM.part_identity(props, fallback=name)
        out.append({
            "name": name,
            "mpn": LM.strict_mpn(props),
            "manufacturer": ident["manufacturer"],
            "datasheet": ident["datasheet"],
            "description": ident["description"],
            "footprint": LM.symbol_footprint_ref(block) or None,
            "value": (props.get("Value") or None),
        })
    return out


# ── Task 2: matching ─────────────────────────────────────────────────────────
def _norm(s: Optional[str]) -> str:
    """Normalize a value/footprint stem for fuzzy comparison: strip + lowercase."""
    return str(s or "").strip().lower()


def _fp_stem(value: Optional[str]) -> str:
    """Footprint stem with any library nickname stripped ('Lib:Name' -> 'Name')."""
    return LM.footprint_name(value)


def match_component(comp: dict, lib_index: List[dict]) -> dict:
    """Match one project component against the Library index.

    Returns {"ref", "lib_part": dict|None, "confidence", "alternatives"}:
      - "exact"  — the component's lib_id symbol name equals a library part name,
        OR its strict MPN equals a library part's mpn.
      - "verify" — else a part shares the normalized Value AND footprint stem;
        `alternatives` counts the other fuzzy candidates.
      - "none"   — otherwise, lib_part is None.
    """
    ref = comp.get("ref", "")
    props = comp.get("props") or {}

    # Exact — symbol identity.
    lib_id = comp.get("lib_id") or ""
    sym_name = lib_id.split(":")[-1] if lib_id else ""
    if sym_name:
        for part in lib_index:
            if part["name"] == sym_name:
                return {"ref": ref, "lib_part": part, "confidence": "exact",
                        "alternatives": 0}

    # Exact — MPN.
    smpn = LM.strict_mpn(props)
    if smpn:
        for part in lib_index:
            if part["mpn"] and part["mpn"] == smpn:
                return {"ref": ref, "lib_part": part, "confidence": "exact",
                        "alternatives": 0}

    # Fuzzy — value + footprint stem.
    cval = _norm(comp.get("value"))
    cfp = _norm(_fp_stem(comp.get("footprint")))
    candidates: List[dict] = []
    if cval and cfp:
        for part in lib_index:
            if _norm(part["value"]) == cval and _norm(part["footprint"]) == cfp:
                candidates.append(part)
    if candidates:
        return {"ref": ref, "lib_part": candidates[0], "confidence": "verify",
                "alternatives": len(candidates) - 1}

    return {"ref": ref, "lib_part": None, "confidence": "none", "alternatives": 0}


# ── Task 3: build the fill plan ──────────────────────────────────────────────
# A value counts as "blank" (so filling it is a fill, not an overwrite) when it
# is empty or one of KiCad's placeholder tokens.
_BLANKS = {"", "~", "*", "-", "n/a", "na", "none", "value"}


def _is_blank(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in _BLANKS


def _proposed_values(lib_part: dict) -> Dict[str, str]:
    """Field-key -> proposed schematic value (property key applied later)."""
    return {
        "mpn": lib_part.get("mpn"),
        "manufacturer": lib_part.get("manufacturer"),
        "datasheet": lib_part.get("datasheet"),
        "description": lib_part.get("description"),
        "footprint": (LM.qualify_footprint(lib_part["footprint"])
                      if lib_part.get("footprint") else None),
    }


def build_fill_plan(components: List[dict], lib_index: List[dict],
                    sheet_of: dict) -> dict:
    """Turn matched components into a reviewable plan.

    Returns {"items": [FillItem], "summary": {...}} where
        FillItem   = {"ref", "sheet", "match", "changes", "default_selected"}
        FieldChange= {"field", "prop", "old", "new", "kind": "fill"|"overwrite"}

    A FieldChange is proposed for each of mpn/manufacturer/datasheet/description/
    footprint only when the library value is present AND differs from the current
    schematic property value. `kind="fill"` when the old value is blank/placeholder,
    else "overwrite". `default_selected` is True only for an exact match whose
    changes are all fills. `sheet_of` maps ref -> sheet path.
    """
    items: List[dict] = []
    need_review = 0
    no_match = 0

    for comp in (components or []):
        ref = comp.get("ref", "")
        match = match_component(comp, lib_index)
        conf = match["confidence"]
        if conf == "none" or not match["lib_part"]:
            no_match += 1
            continue
        if conf == "verify":
            need_review += 1

        props = comp.get("props") or {}
        proposed = _proposed_values(match["lib_part"])
        changes: List[dict] = []
        for field, new in proposed.items():
            if new is None or not str(new).strip():
                continue
            prop = FILL_PROPERTY[field]
            old = str(props.get(prop, "") or "")
            if old.strip() == str(new).strip():
                continue                                   # already correct
            kind = "fill" if _is_blank(old) else "overwrite"
            changes.append({"field": prop, "prop": prop, "old": old,
                            "new": str(new), "kind": kind, "source": "library"})

        default_selected = (conf == "exact"
                            and bool(changes)
                            and all(c["kind"] == "fill" for c in changes))
        items.append({"ref": ref, "sheet": sheet_of.get(ref, ""),
                      "match": match, "changes": changes,
                      "default_selected": default_selected})

    summary = {
        "components": len(components or []),
        "fields": sum(len(i["changes"]) for i in items),
        "need_review": need_review,
        "no_match": no_match,
    }
    return {"items": items, "summary": summary}


# ── Distributor enrichment (fills identity fields Mouser/LCSC can supply) ─────
_ENRICH_FIELDS = (("manufacturer", "Manufacturer"),
                  ("datasheet", "Datasheet"),
                  ("description", "Description"))


def enrich_plan(plan: dict, components: List[dict], cfg: dict,
                sheet_of: Optional[dict] = None, lookup=None) -> dict:
    """Widen a fill plan with distributor-sourced identity fields. For each component
    that carries a REAL manufacturer part number but still has a blank Manufacturer /
    Datasheet / Description (in the schematic AND not already proposed from the Library),
    look the MPN up on the distributor chain and add a FieldChange tagged
    `source="mouser"`. A component with an MPN but NO Library match gets a fresh plan
    item so it can be completed from the distributor alone.

    Per-MPN cache respects the free-tier quota (identical MPNs = one call); a
    missing/failing provider degrades to library-only with NO error. This performs
    NETWORK calls — run it from an off-thread phase only. Returns the same plan
    (mutated in place and returned); `summary["enriched"]` counts the added fields.
    """
    lk = lookup if lookup is not None else LM.providers_from_config(cfg)
    if lk is None:
        return plan
    sheet_of = sheet_of or {}
    items_by_ref = {it["ref"]: it for it in plan.get("items", [])}
    cache: Dict[str, Optional[dict]] = {}
    enriched = 0
    for comp in (components or []):
        ref = comp.get("ref", "")
        props = comp.get("props") or {}
        mpn = LM.strict_mpn(props)
        if not mpn:
            continue
        it = items_by_ref.get(ref)
        have = {c["prop"] for c in (it["changes"] if it else [])}
        need = [(f, prop) for f, prop in _ENRICH_FIELDS
                if _is_blank(props.get(prop)) and prop not in have]
        if not need:
            continue
        if mpn not in cache:
            try:
                cache[mpn] = lk(mpn)
            except Exception:  # noqa: BLE001 — a dead provider never fails the plan
                cache[mpn] = None
        fetched = cache[mpn]
        if not fetched:
            continue
        new_changes = []
        for f, prop in need:
            val = str(fetched.get(f) or "").strip()
            if val:
                new_changes.append({"field": prop, "prop": prop,
                                    "old": str(props.get(prop, "") or ""),
                                    "new": val, "kind": "fill", "source": "mouser"})
        if not new_changes:
            continue
        if it is None:
            it = {"ref": ref, "sheet": sheet_of.get(ref, ""),
                  "match": {"ref": ref, "lib_part": None, "confidence": "mouser",
                            "alternatives": 0},
                  "changes": [], "default_selected": True}
            plan.setdefault("items", []).append(it)
            items_by_ref[ref] = it
        it["changes"].extend(new_changes)
        enriched += len(new_changes)
    summary = plan.setdefault("summary", {})
    summary["fields"] = sum(len(i["changes"]) for i in plan.get("items", []))
    summary["enriched"] = enriched
    return plan


# ── Passive grouping (fill the data of one, apply to all) + manual merge ──────
_PASSIVE_PREFIXES = LM._BASIC_PREFIXES          # {"R", "C", "L", "FB"}
# Identity fields a passive group / a manual entry can fill, prop-keyed.
_GROUP_FIELDS = (("MPN", "Part Number"), ("Manufacturer", "Manufacturer"),
                 ("Datasheet", "Datasheet"), ("Description", "Description"))


def _ref_prefix(ref) -> str:
    s = str(ref or "")
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    return s[:i].upper()


def passive_groups(components: List[dict]) -> List[dict]:
    """Group identical passives (R/C/L/FB with a value and NO real MPN) by
    (value, footprint-stem) so their shared identity is entered once. Returns
    [{key, label, value, footprint, refs, missing}] sorted largest-group first. A
    passive that already carries a real part number is not grouped (it completes on
    its own); `missing` is the identity props still blank somewhere in the group."""
    groups: Dict[tuple, dict] = {}
    for comp in (components or []):
        ref = comp.get("ref", "")
        if _ref_prefix(ref) not in _PASSIVE_PREFIXES:
            continue
        props = comp.get("props") or {}
        if LM.strict_mpn(props):                # already has a part number -> not fill-once
            continue
        value = str(comp.get("value") or props.get("Value") or "").strip()
        if not value:
            continue
        fp_stem = LM.footprint_name(comp.get("footprint") or props.get("Footprint") or "")
        key = (value.lower(), fp_stem.lower())
        g = groups.setdefault(key, {"value": value, "footprint": fp_stem,
                                    "refs": [], "missing": set()})
        g["refs"].append(ref)
        for prop, _lbl in _GROUP_FIELDS:
            if _is_blank(props.get(prop)):
                g["missing"].add(prop)
    out = []
    for (val_l, fp_l), g in groups.items():
        refs = sorted(g["refs"])
        label = f"{len(refs)}× · {g['value']}" + (f" · {g['footprint']}" if g["footprint"] else "")
        out.append({"key": f"{val_l}\x1f{fp_l}", "value": g["value"],
                    "footprint": g["footprint"], "refs": refs,
                    "missing": sorted(g["missing"]), "label": label})
    out.sort(key=lambda x: (-len(x["refs"]), x["value"].lower()))
    return out


def expand_group_fill(group: dict, field_values: Dict[str, str]) -> Dict[tuple, str]:
    """Fan one group's entered {prop: value} out to {(ref, prop): value} for every ref
    in the group — the fill-once map. Blank values are skipped."""
    out: Dict[tuple, str] = {}
    for ref in group.get("refs", []):
        for prop, val in (field_values or {}).items():
            v = str(val or "").strip()
            if v:
                out[(ref, prop)] = v
    return out


def merge_manual_changes(plan: dict, values: Dict[tuple, str],
                         components: Optional[List[dict]] = None,
                         sheet_of: Optional[dict] = None, source: str = "manual") -> dict:
    """Merge user-entered {(ref, prop): value} into the plan as FieldChanges (creating a
    plan item for a ref with none yet), so preview / apply / backup / re-audit treat manual
    and passive-group fills exactly like library/distributor ones. `components` supplies the
    old value; `sheet_of` supplies the sheet for a fresh item. Returns the plan (mutated)."""
    items_by_ref = {it["ref"]: it for it in plan.get("items", [])}
    props_by_ref = {c.get("ref", ""): (c.get("props") or {}) for c in (components or [])}
    sheet_of = sheet_of or {}
    for (ref, prop), val in (values or {}).items():
        v = str(val or "").strip()
        if not v:
            continue
        it = items_by_ref.get(ref)
        if it is None:
            it = {"ref": ref, "sheet": sheet_of.get(ref, ""),
                  "match": {"ref": ref, "lib_part": None, "confidence": source,
                            "alternatives": 0},
                  "changes": [], "default_selected": True}
            plan.setdefault("items", []).append(it)
            items_by_ref[ref] = it
        old = str(props_by_ref.get(ref, {}).get(prop, "") or "")
        it["changes"] = [c for c in it["changes"] if c["prop"] != prop]   # replace same-prop
        it["changes"].append({"field": prop, "prop": prop, "old": old, "new": v,
                              "kind": "fill" if _is_blank(old) else "overwrite",
                              "source": source})
    plan.setdefault("summary", {})["fields"] = sum(len(i["changes"]) for i in plan.get("items", []))
    return plan


# ── Completion passport (schematic-instance scoped) ──────────────────────────
# The identity/link fields that make a placed component "fully filled". Read from
# the SCHEMATIC instance's own properties (not a Library row) so the passport
# reflects the actual project file. (3D-model + footprint-file DISK resolution are
# layered on by the model-verification step; M0 measures the directly-readable set.)
_COMPLETION_FIELDS = (
    ("footprint", "Footprint"),
    ("mpn", "Part Number"),
    ("manufacturer", "Manufacturer"),
    ("datasheet", "Datasheet"),
    ("description", "Description"),
)


def component_model_status(comp: dict, cfg: dict, cache: Optional[dict] = None) -> str:
    """Whether the component's 3D model resolves on disk:
    'ok'            — the footprint resolves AND carries a (model …) line whose file
                      exists in ModelLib;
    'no_model'      — the footprint resolves but has no model / the model file is missing;
    'unresolved_fp' — the footprint file itself cannot be found in FootprintLib;
    'no_footprint'  — no footprint reference at all.
    `cache` (fp_ref -> status) avoids re-reading a footprint shared by many components."""
    props = comp.get("props") or {}
    fp_ref = comp.get("footprint") or props.get("Footprint") or ""
    if _is_blank(fp_ref):
        return "no_footprint"
    if cache is not None and fp_ref in cache:
        return cache[fp_ref]
    cfg = cfg or {}
    fp_dirs = [cfg["FootprintLib"]] if cfg.get("FootprintLib") else []
    pads, models = H._footprint_pads_and_models(fp_dirs, fp_ref)
    if pads is None:
        status = "unresolved_fp"
    elif not models:
        status = "no_model"
    else:
        mdl_dir = cfg.get("ModelLib")
        status = "no_model"
        if mdl_dir:
            for m in models:
                if (Path(mdl_dir) / Path(m).name).exists():
                    status = "ok"
                    break
    if cache is not None:
        cache[fp_ref] = status
    return status


def component_completion(comp: dict, cfg: Optional[dict] = None,
                         _fp_cache: Optional[dict] = None) -> dict:
    """Per-component completeness passport for a placed schematic instance:
    {ref, items:[{key,label,present}], score, total, missing, is_complete}. A field
    is present when its schematic property is non-blank (MPN via LM.strict_mpn so a
    Value-fallback never counts). When `cfg` is given, a sixth item — 3D Model — is
    added, present only when the footprint's model resolves on disk (M2)."""
    props = comp.get("props") or {}
    fp = comp.get("footprint") or props.get("Footprint") or ""
    present = {
        "footprint": not _is_blank(fp),
        "mpn": bool(LM.strict_mpn(props)),
        "manufacturer": not _is_blank(props.get("Manufacturer")),
        "datasheet": not _is_blank(props.get("Datasheet")),
        "description": not _is_blank(props.get("Description")),
    }
    items = [{"key": k, "label": lbl, "present": present[k]}
             for k, lbl in _COMPLETION_FIELDS]
    if cfg is not None:
        items.append({"key": "model", "label": "3D Model",
                      "present": component_model_status(comp, cfg, _fp_cache) == "ok"})
    score = sum(1 for i in items if i["present"])
    return {"ref": comp.get("ref", ""), "items": items, "score": score,
            "total": len(items),
            "missing": [i["label"] for i in items if not i["present"]],
            "is_complete": score == len(items)}


def project_completion(components: List[dict], cfg: Optional[dict] = None) -> dict:
    """Roll up component_completion over every component:
    {total, complete, incomplete_refs, missing_counts:{label:count}, passports:[...]}.
    `missing_counts` powers the verdict chips (how many components miss each field).
    When `cfg` is given, the 3D-model dimension is included (footprints resolved once
    each via a shared cache)."""
    comps = list(components or [])
    fp_cache: dict = {}
    passports = [component_completion(c, cfg, fp_cache) for c in comps]
    miss_counts: Dict[str, int] = {}
    for p in passports:
        for m in p["missing"]:
            miss_counts[m] = miss_counts.get(m, 0) + 1
    return {"total": len(comps),
            "complete": sum(1 for p in passports if p["is_complete"]),
            "incomplete_refs": [p["ref"] for p in passports if not p["is_complete"]],
            "missing_counts": miss_counts,
            "passports": passports}


# ── Task 4: safe .kicad_sch property writer ──────────────────────────────────
def _is_placed_instance(block: str) -> bool:
    """A placed component instance carries a `(lib_id ...)` child; the symbols
    inside the `(lib_symbols)` cache are `(symbol "Name" ...)` and never do.
    `nd_project_health._symbol_spans` returns BOTH as top-level spans, so this
    guard is what keeps the writer off the cache."""
    return "(lib_id" in block


def write_fields_to_sheet(sheet_path, changes_by_ref: Dict[str, Dict[str, str]],
                          *, backup: bool = True) -> int:
    """Write property changes into a `.kicad_sch`, safely.

    `changes_by_ref` = {ref: {prop: new_value, ...}}. For each PLACED top-level
    `(symbol …)` instance whose Reference is a key in `changes_by_ref`, set/insert
    each property via `LibraryManager.set_symbol_property` and splice the block back.
    Writes a `.bak` sibling (when `backup`) then atomically replaces the file — only
    if the text actually changed. Returns the count of components written.

    Never touches the `(lib_symbols)` cache: cache symbols are excluded because they
    have no `(lib_id ...)` child (see `_is_placed_instance`); their bare-prefix
    Reference (e.g. "R") also won't match a real ref key.
    """
    path = Path(sheet_path)
    if not changes_by_ref or not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8")

    # Edit right-to-left so earlier spans' offsets stay valid after splicing.
    spans = H._symbol_spans(text)
    written = 0
    new_text = text
    for a, b in sorted(spans, key=lambda s: s[0], reverse=True):
        block = text[a:b]
        if not _is_placed_instance(block):
            continue
        ref = LM.extract_symbol_properties(block).get("Reference", "")
        props = changes_by_ref.get(ref)
        if not props:
            continue
        edited = block
        for prop, val in props.items():
            edited = LM.set_symbol_property(edited, prop, val)
        if edited != block:
            new_text = new_text[:a] + edited + new_text[b:]
            written += 1

    if new_text == text:
        return 0

    if backup:
        path.with_suffix(path.suffix + ".bak").write_text(text, encoding="utf-8")
    # Atomic: write a temp sibling then os.replace onto the target.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    return written


# ── Task 5: apply the plan ───────────────────────────────────────────────────
class _NullLog:
    """A no-op logger so `apply_fill_plan` and `register_libraries` never need Qt."""

    def write(self, msg):  # noqa: D401
        pass


def apply_fill_plan(plan: dict, selected, cfg: dict, log=None,
                    *, backup: bool = True) -> dict:
    """Apply the user-selected subset of a fill plan to disk.

    `selected` is a set of `(ref, prop)` pairs the user checked. Selected
    FieldChanges are grouped by sheet into `{ref: {prop: new}}` and written with
    `write_fields_to_sheet` per sheet. If any Footprint property was written, the
    Library's footprint table is (re)registered via `LibraryManager.register_libraries`
    so the footprint + 3D model resolve.

    Returns FillResult = {"written_files", "components_changed", "fields_written",
    "backups", "errors"}. Per-file errors are captured, never raised out.
    """
    log = log or _NullLog()
    selected = set(selected or ())

    # Group selected changes by sheet -> {ref: {prop: new}}.
    by_sheet: Dict[str, Dict[str, Dict[str, str]]] = {}
    fields_selected = 0
    footprint_written = False
    for item in plan.get("items", []):
        ref = item["ref"]
        sheet = item.get("sheet") or ""
        for ch in item.get("changes", []):
            if (ref, ch["prop"]) not in selected:
                continue
            if not sheet:
                continue
            by_sheet.setdefault(sheet, {}).setdefault(ref, {})[ch["prop"]] = ch["new"]
            fields_selected += 1
            if ch["prop"] == FILL_PROPERTY["footprint"]:
                footprint_written = True

    written_files: List[str] = []
    backups: List[str] = []
    errors: List[str] = []
    components_changed = 0
    fields_written = 0

    for sheet, changes_by_ref in by_sheet.items():
        try:
            n = write_fields_to_sheet(sheet, changes_by_ref, backup=backup)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{Path(sheet).as_posix()}: {e}")
            continue
        if n:
            written_files.append(sheet)
            components_changed += n
            fields_written += sum(len(p) for p in changes_by_ref.values())
            bak = Path(sheet).with_suffix(Path(sheet).suffix + ".bak")
            if backup and bak.exists():
                backups.append(str(bak))

    if footprint_written and written_files:
        try:
            LM.register_libraries(cfg, log)
        except Exception as e:  # noqa: BLE001
            errors.append(f"register_libraries: {e}")

    return {"written_files": written_files,
            "components_changed": components_changed,
            "fields_written": fields_written,
            "backups": backups,
            "errors": errors}
