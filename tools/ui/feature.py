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
from typing import Callable, Dict, List, Optional

from PyQt5.QtWidgets import QWidget


class EventBus:
    """Tiny pub/sub so features can cooperate without importing one another
    (for example: click a fabric channel -> 'bench.jump_pin', click a part ->
    'library.preview'). A handler that raises is isolated, never breaks emit.

    Each subscription is a ``(fn, owner_ref)`` pair: ``on()`` stores ``owner_ref=None``
    (a process-lifetime singleton), ``on_owned()`` stores a WEAKREF to the owner widget.
    A subscription whose owner has been garbage-collected OR whose C++ half has been
    deleted (``sip.isdeleted``) is DROPPED lazily — pruned on the next ``emit`` (or an
    explicit ``_prune``). This is what keeps a rebuilt panel from leaking dead closures.

    Why weakref + lazy prune, and NOT ``owner.destroyed.connect(...)``: a Python slot
    connected to ``QObject.destroyed`` is invoked by Qt when the owner's C++ object is
    deleted — and when an un-parented owner is finalized by *Python's* garbage collector,
    that invocation re-enters the interpreter mid-collection, a use-after-free that
    SEGFAULTS (it crashed the suite deterministically inside every test that called
    ``gc.collect()``). Weakref cleanup runs in pure Python and never delivers a Qt signal
    during GC, so the crash class is gone; the immediate-unsubscribe contract is preserved
    because a still-referenced-but-``sip``-deleted owner reads as dead at the next emit."""

    def __init__(self) -> None:
        # topic -> [(fn, owner_ref)] ; owner_ref is None (on) or weakref.ref (on_owned).
        self._subs: Dict[str, List[tuple]] = {}

    @staticmethod
    def _dead(owner_ref) -> bool:
        """True if an owned subscription's owner is gone (GC'd) or its C++ half deleted."""
        if owner_ref is None:
            return False                        # owner-less: process-lifetime, never dead
        o = owner_ref()
        if o is None:
            return True                         # Python wrapper garbage-collected
        try:
            from PyQt5 import sip
            return bool(sip.isdeleted(o))       # C++ deleted (sip.delete / deleteLater)
        except Exception:  # noqa: BLE001
            return False

    def _prune(self, topic: Optional[str] = None) -> None:
        """Drop subscriptions whose owner has died. All topics when `topic` is None."""
        topics = (topic,) if topic is not None else tuple(self._subs.keys())
        for t in topics:
            subs = self._subs.get(t)
            if subs:
                self._subs[t] = [(fn, ref) for (fn, ref) in subs if not self._dead(ref)]

    def on(self, topic: str, fn: Callable) -> Callable:
        self._subs.setdefault(topic, []).append((fn, None))
        return fn

    def off(self, topic: str, fn: Callable) -> None:
        """Unsubscribe fn from topic (idempotent — safe if already gone)."""
        subs = self._subs.get(topic)
        if subs:
            self._subs[topic] = [(f, ref) for (f, ref) in subs if f is not fn]

    def on_owned(self, topic: str, fn: Callable, owner) -> Callable:
        """Subscribe fn and auto-drop it when `owner` (a QObject/QWidget) is destroyed or
        garbage-collected. Use this from any panel that is rebuilt (a project/package
        switch drops and recreates it): a bare `on()` would leave the old closure on the
        bus forever, so every later emit fires a growing pile of dead closures over
        deleted C++ widgets (the same leak `register_restyle(fn, owner)` fixed for
        restylers). Owner-less `on()` is only for process-lifetime singletons.

        The owner is tracked by a WEAKREF, never the `destroyed` signal — see the class
        docstring for why (a `destroyed`-connected Python slot segfaults during GC)."""
        import weakref
        try:
            ref = weakref.ref(owner)
        except TypeError:                       # owner not weakly-referenceable
            ref = None                          # degrade to process-lifetime (rare)
        self._subs.setdefault(topic, []).append((fn, ref))
        return fn

    def emit(self, topic: str, *args, **kwargs) -> None:
        subs = self._subs.get(topic)
        if not subs:
            return
        survivors = []
        for fn, ref in list(subs):
            if self._dead(ref):
                continue                        # owner gone -> prune (skip the call)
            survivors.append((fn, ref))
            try:
                fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the rest
                pass
        if len(survivors) != len(subs):
            self._subs[topic] = survivors       # write back the pruned list


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
    category: str = ""            # search grouping label (Ctrl+K groups matches under a
                                  # category eyebrow) AND a second match target, so typing
                                  # the area name surfaces a workspace whose title differs.
                                  # Empty falls back to the default "Workspaces" group.
    enabled: bool = True          # False → an honest DISABLED nav item (greyed, not
                                  # clickable, `disabled_tip` on hover) whose page is
                                  # never built — for a shelved feature, so the nav
                                  # never offers a live row that opens a dead placeholder.
    disabled_tip: str = ""        # hover text explaining why a disabled item is greyed

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
