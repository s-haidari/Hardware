"""ui.feature — the plug-in contract for NETDECK workspaces.

A `Feature` is one left-nav workspace. It carries metadata (id / title / icon /
order) and builds its root widget from a `Context`. Features self-register into a
process-global registry, so:

    * add a feature   -> new module, `register(MyFeature())`
    * remove a feature -> delete the module + its registration
    * reorder the nav  -> change `order`

The shell never hard-codes a feature; it iterates `features()`.

`Panel` + the `Workspace` widget (ui.widgets) give the same list-driven pattern
for sub-tabs, so a workspace's sub-features are just as easy to add or drop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

from PyQt5.QtWidgets import QWidget


class EventBus:
    """Tiny pub/sub so features can cooperate without importing one another
    (for example: click a fabric channel -> 'bench.jump_pin', click a part ->
    'library.preview'). A handler that raises is isolated, never breaks emit."""

    def __init__(self) -> None:
        self._subs: Dict[str, List[Callable]] = {}

    def on(self, topic: str, fn: Callable) -> Callable:
        self._subs.setdefault(topic, []).append(fn)
        return fn

    def emit(self, topic: str, *args, **kwargs) -> None:
        for fn in list(self._subs.get(topic, ())):
            try:
                fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the rest
                pass


@dataclass
class Context:
    """Everything a feature is allowed to reach. Keeps features decoupled from the
    shell internals: they get config, an async/log service, the live theme module,
    and the event bus. Nothing here reaches back into a specific feature."""

    cfg: dict
    services: object              # .run_async(fn, ok=, done_cb=), .log(msg)
    theme: object                 # the ui.theme module (tokens(), is_dark(), ...)
    bus: EventBus = field(default_factory=EventBus)


class Feature:
    """One workspace. Subclass, set the class attributes, implement build()."""

    id: str = ""                  # unique route key
    title: str = ""               # nav label (Title Case)
    icon: str = ""                # ui.widgets icon name
    order: int = 100              # nav ordering (lower first)

    def build(self, ctx: Context) -> QWidget:  # pragma: no cover - interface
        raise NotImplementedError


# ── registry ─────────────────────────────────────────────────────────────────
_REGISTRY: List[Feature] = []


def register(feature: Feature) -> Feature:
    """Add a feature to the nav. Idempotent per id (re-registering replaces)."""
    global _REGISTRY
    _REGISTRY = [f for f in _REGISTRY if f.id != feature.id]
    _REGISTRY.append(feature)
    return feature


def features() -> List[Feature]:
    """Registered features in nav order."""
    return sorted(_REGISTRY, key=lambda f: (f.order, f.title))


def clear() -> None:
    """Drop all registrations (tests build a clean registry)."""
    _REGISTRY.clear()
