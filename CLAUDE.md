# NETDECK — repo instructions

## UI work: read the design rules first
Before changing ANY user-facing UI (PyQt widgets, theme, layout, copy), read and
follow **[docs/design/design-rules.md](docs/design/design-rules.md)**. It is the
guardrail against the "AI-generated look."

**Prime directive:** make it look like a shipping product (Linear, Raycast, Vercel,
native macOS / Windows 11), not a generated mockup. When in doubt, *remove*.

**Hardest never-break rules** (full list + rationale in the doc):
1. Do not box every value in a bordered pill — pills are for status/tags only.
2. Default to zero borders; separate with whitespace and background steps.
3. No cards inside cards; one elevation level per region.
4. No letterspaced UPPERCASE micro-labels sprinkled around.
5. No colored accent bar/rail on a rounded card.
6. Color is meaning, never decoration; never tint a surface with a category hue.
7. Every view has one clear focal point, not equal weight everywhere.

Run the pre-ship checklist in the doc before calling any UI change done.
