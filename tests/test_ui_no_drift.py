"""Feature chrome files must route styling through kit/widgets, not style directly.
Bespoke visual modules are allowlisted (they legitimately paint custom Qt)."""
import re
from pathlib import Path

from tools.ui import theme as _theme_mod

FEAT = Path(__file__).resolve().parents[1] / "tools" / "ui" / "features"

# Radius tokens (the ONE source) — the value lint compares literal radii to these.
T_RADIUS_CONTROL = _theme_mod.RADIUS_CONTROL
T_RADIUS_CONTAINER = _theme_mod.RADIUS_CONTAINER

# bespoke visual modules exempt from the chrome rules (custom painting), AND
# not-yet-migrated files (remove each as it migrates onto kit).
ALLOWLIST = {
    "library_preview.py",       # bespoke symbol/footprint/3D preview painting
    "bench_visuals.py",         # bespoke Bench painting (PinMap, 3-dim legend, flow blocks)
    "projects_visuals.py",      # bespoke Projects container QSS + net-class colour swatch
    "mouser_search.py",         # sourcing session's file (contested)
    # not-yet-migrated (remove as migrated):
    "settings.py", "routing.py",
}

BANNED = [
    (re.compile(r"\.setLetterSpacing\("), "setLetterSpacing (retired)"),
    (re.compile(r"(ui_font|mono_font)\(\s*\d"), "hardcoded font size (use scale_font)"),
    (re.compile(r"\.setStyleSheet\("), "direct setStyleSheet (use kit/widgets)"),
]

# Display uppercasing (ALL-CAPS UI text) is banned — but str.upper() used for
# case-insensitive MATCHING is legitimate logic, not styling. So the .upper() rule
# fires only when the same line produces display text (a label/text constructor),
# and never on a comparison/membership line.
_DISPLAY_UPPER = re.compile(
    r"(setText|setPlaceholderText|QLabel|addItem|"
    r"\b(body|eyebrow|subhead|section_header|page_title|tag|token|net_label|net_token)\()"
    r"[^\n]*\.upper\(\)")
_COMPARISON = re.compile(r"\bin\b|==|!=|\.startswith|\.endswith|\.find\(|\.index\(")

def _feature_files():
    for p in sorted(FEAT.glob("*.py")):
        if p.name == "__init__.py" or p.name in ALLOWLIST:
            continue
        yield p

def test_migrated_features_do_not_style_directly():
    problems = []
    for p in _feature_files():
        src = p.read_text(encoding="utf-8")   # UI source has UTF-8 glyphs (→ · ×); Windows default cp1252 would choke
        lines = src.splitlines()
        for rx, why in BANNED:
            for m in rx.finditer(src):
                line = src[:m.start()].count("\n") + 1
                problems.append(f"{p.name}:{line} — {why}")
        # display-only uppercasing rule
        for i, ln in enumerate(lines, 1):
            if _DISPLAY_UPPER.search(ln) and not _COMPARISON.search(ln):
                problems.append(f"{p.name}:{i} — .upper() on display text (Title Case only)")
    assert not problems, "drift found:\n" + "\n".join(problems)


# ── §0.3 no-drift VALUE lint — hardcoded design values are impossible ─────────
# theme.py (tools/ui/theme.py) is the ONE source of colour + radius. A chrome file
# under features/, widgets.py, or kit.py may NOT bake a raw design VALUE into a
# style string — every colour must come through T.t(...) / T.category(...) and every
# control/container radius through RADIUS_CONTROL / RADIUS_CONTAINER. Object-name-only
# styling (rules live centrally in theme.qss()) is fine, as are the small (<=5px)
# category/status DOT radii (sanctioned data markers, design-rules §1.1) and
# scale_font(...) for type. The same ALLOWLIST of bespoke *_visuals.py files applies.
UI = Path(__file__).resolve().parents[1] / "tools" / "ui"

# Files the VALUE lint governs: every non-allowlisted feature, plus the two kit modules.
def _value_lint_files():
    for p in sorted(FEAT.glob("*.py")):
        if p.name == "__init__.py" or p.name in ALLOWLIST:
            continue
        yield p
    for name in ("widgets.py", "kit.py"):
        yield UI / name

# A raw hex colour anywhere a value is used (3/4/6/8 digit). A default-param FALLBACK
# (``= "#..."`` / ``color: str = "#..."``) is a helper's last-resort literal, not a
# themed style value — those are the sanctioned exception (svg_icon's neutral tint).
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_HEX_FALLBACK = re.compile(r"=\s*[\"']#[0-9a-fA-F]{3,8}")     # kw / default = "#..."
# rgba()/rgb() literal. Guard/parse logic (``.startswith(\"rgba(\")``) is not a value.
_RGBA = re.compile(r"\brgba?\(")
_RGBA_PARSE = re.compile(r"startswith\(\s*[\"']rgba?\(")
# A control/container radius must be tokenised. A literal 6px/8px radius is a value
# drift; the small dot radii (<=5px) are sanctioned markers and pass.
_RADIUS_LIT = re.compile(r"border-radius\s*:\s*([0-9]+)px")
# `border:` carrying a hardcoded colour (hex) or a solid-px literal with no token.
_BORDER_HEX = re.compile(r"border\s*:[^;}\"']*#[0-9a-fA-F]{3,8}")


def _strip_comment(line: str) -> str:
    """Drop a trailing ``# ...`` comment (naive but sufficient: our style strings do
    not contain a literal ``#`` outside a hex colour, and hex colours are what we WANT
    to catch — so a real ``#rrggbb`` inside code is never mistaken for a comment)."""
    # A '#' that begins a hex colour is 6/3/8 hexdigits; a comment '#' is followed by
    # a space or non-hex. Cut at the first '#' that is NOT the start of a hex literal.
    i = 0
    while True:
        j = line.find("#", i)
        if j == -1:
            return line
        if _HEX.match(line, j):                 # this '#' starts a hex colour — keep scanning past it
            i = j + 1
            continue
        return line[:j]                         # a genuine comment marker


def _is_doc_line(stripped: str) -> bool:
    """A docstring / prose line (starts a triple-quote block or is inside one is
    approximated by the leading-quote heuristic used file-wide below)."""
    return stripped.startswith(('"""', "'''", '"', "'"))


def test_no_hardcoded_design_values_in_chrome():
    problems = []
    for p in _value_lint_files():
        src = p.read_text(encoding="utf-8")     # UI source carries UTF-8 glyphs (→ · ×)
        in_doc = False
        for i, raw in enumerate(src.splitlines(), 1):
            # Track triple-quoted docstring blocks so prose examples (which mention
            # rgba()/#rrggbb to EXPLAIN the token forms) never count as style values.
            tq = raw.count('"""') + raw.count("'''")
            if in_doc:
                if tq % 2 == 1:
                    in_doc = False
                continue
            if tq % 2 == 1:                     # opens a docstring that doesn't close on this line
                in_doc = True
                continue
            code = _strip_comment(raw)
            if not code.strip():
                continue
            # 1) raw hex colour (not a default-param fallback)
            if _HEX.search(code) and not _HEX_FALLBACK.search(code):
                problems.append(f"{p.name}:{i} — hardcoded hex colour (use T.t/T.category)")
            # 2) rgba()/rgb() literal that isn't a parse guard
            if _RGBA.search(code) and not _RGBA_PARSE.search(code):
                problems.append(f"{p.name}:{i} — rgba()/rgb() literal (use a theme token)")
            # 3) border: carrying a hex colour
            if _BORDER_HEX.search(code):
                problems.append(f"{p.name}:{i} — border with a hardcoded colour (use T.t)")
            # 4) control/container radius (6 or 8 px) baked as a literal, not tokenised
            for m in _RADIUS_LIT.finditer(code):
                px = int(m.group(1))
                if px in (T_RADIUS_CONTROL, T_RADIUS_CONTAINER):
                    problems.append(
                        f"{p.name}:{i} — literal border-radius:{px}px "
                        f"(use {{T.RADIUS_CONTROL}}/{{T.RADIUS_CONTAINER}}px)")
    assert not problems, (
        "hardcoded design values found (theme.py is the one source):\n" + "\n".join(problems))
