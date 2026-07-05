"""NETDECK UI — a clean-slate, plug-in feature architecture.

The shell owns nothing feature-specific. Every workspace is a `Feature` that
self-registers into `ui.feature`'s registry; the shell builds the left nav and
the content stack from that registry alone. Adding a feature is one new module
plus one `register(...)` call; deleting one is removing that module. Sub-features
are `Panel`s inside a `Workspace`, giving the same list-driven modularity at the
sub-tab level.

Layers:
  ui.theme     design tokens (exact Windows 11 / WinUI neutral ladder) + QSS + fonts
  ui.widgets   the shared component kit (tokens, tags, cards, tables, verdict, ...)
  ui.feature   the Feature / Panel / Context contract + the registry
  ui.shell     the QMainWindow shell (native title bar, left nav, theme toggle)
  ui.features  one module per workspace (bench, library, projects, settings)
"""
