"""App-wide spacing contract (design-rules.md §Spacing).

The five feature panels were swept onto ``T.sp()`` / ``kit.page_layout`` (commits c265948,
719df75): spacing is routed through the one 4px-grid scale, never a scattered raw literal.
This gate locks that in — an AST scan that FAILS if a ``setSpacing(...)`` / ``setContentsMargins(...)``
call in a tokenised panel reintroduces a raw integer equal to a token value (it should be
``T.sp(role)``). Off-grid literals (2 / 3 / 6 / 18 / 22 …) are deliberately allowed — they match
no token and snapping them to grid changes pixels (a separate, render-gated task); 0 is the
universal zero-margin and is always fine.

Bespoke-painting modules are out of scope (they place pixels by hand, not on the section grid):
the ``*_visuals`` painters, ``library_preview`` (the canvas relayout), and ``mouser_search``.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ROOT / "tools" / "ui" / "features"

# The tokenised panels the sweep covered — these must stay literal-free for spacing.
_TOKENISED_PANELS = ("bench.py", "library.py", "projects.py", "git.py", "settings.py")

# The value scale (theme.SPACE): the 4px grid steps + the semantic roles. A raw literal
# equal to any of these SHOULD be T.sp(role); anything else is off-grid and allowed.
_TOKEN_VALUES = {4, 8, 10, 12, 14, 16, 20, 24, 30, 32}

_SPACING_CALLS = {"setContentsMargins", "setSpacing"}


def test_feature_panels_route_spacing_through_sp():
    problems = []
    for name in _TOKENISED_PANELS:
        p = FEATURES / name
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _SPACING_CALLS):
                continue
            for arg in node.args:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                        and not isinstance(arg.value, bool)
                        and arg.value in _TOKEN_VALUES):
                    problems.append(f"{name}:{arg.lineno}  {node.func.attr}(… {arg.value} …)")
    assert not problems, (
        "raw token-value spacing literal in a tokenised panel — route it through T.sp(role) "
        "(design-rules §Spacing; off-grid literals are exempt):\n" + "\n".join(problems))


def test_token_values_match_the_theme_scale():
    # Guard the gate itself: the flagged value set must equal the theme's spacing scale, so a
    # new role added to theme.SPACE can't silently escape the contract.
    from tools.ui import theme as T
    assert _TOKEN_VALUES == set(T.SPACE.values())
