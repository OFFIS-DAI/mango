"""Declarative dispatch for :class:`mango.agent.role.Role`.

Three decorators that move the mechanical parts of ``setup()`` out of
hand-written boilerplate and into class-level metadata:

* :func:`on_message` — subscribe to a message type with an optional filter.
* :func:`on_event` — subscribe to a co-located event type.
* :func:`periodic` — schedule a periodic task at role-attach time.

A role that uses these decorators does not need to implement
``setup()`` at all unless it has other initialisation logic.  When
``setup()`` is implemented, the decorator wiring runs first (so the
explicit ``setup`` body can override or extend it).

The decorators are pure annotations: they stash configuration on the
decorated method via the attribute name :data:`_MANGO_DISPATCH_META`.
The :class:`mango.agent.role.Role` base class collects them at class
creation time via ``__init_subclass__`` and replays the registrations
through :meth:`RoleContext.subscribe_message` /
:meth:`RoleContext.subscribe_event` /
:meth:`RoleContext.schedule_periodic_task` when the role is bound.

Async coroutine handlers for ``on_message`` are scheduled as instant
tasks automatically — the underlying ``handle_message`` callback is
synchronous, so an async handler would otherwise raise
``RuntimeWarning: coroutine was never awaited``.  This removes the
repeated ``def _wrap(coro_fn): ...`` shim every existing role declares.

Example::

    class Echo(Role):
        @on_message(str, where=lambda self, c, m: c.startswith("ping"))
        async def on_ping(self, content, meta):
            await self.context.send_message("pong", receiver_addr=sender_addr(meta))

        @on_event(MyEvent)
        def on_event(self, event, src):
            ...

        @periodic(every=1.0)
        async def heartbeat(self):
            ...
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Attribute name on a decorated method that carries the dispatch metadata.
_MANGO_DISPATCH_META = "_mango_dispatch_meta"


@dataclass
class _Dispatch:
    """Per-method dispatch metadata collected by the decorators.

    A single method may carry multiple subscriptions (e.g. one
    ``@on_message`` and one ``@on_event``) by stacking decorators —
    each adds an entry to the corresponding list.
    """

    message_subs: list[dict[str, Any]] = field(default_factory=list)
    event_subs: list[dict[str, Any]] = field(default_factory=list)
    periodic: list[dict[str, Any]] = field(default_factory=list)


def _get_or_create_meta(method: Callable) -> _Dispatch:
    meta = getattr(method, _MANGO_DISPATCH_META, None)
    if meta is None:
        meta = _Dispatch()
        try:
            setattr(method, _MANGO_DISPATCH_META, meta)
        except (AttributeError, TypeError):
            # Bound methods and some descriptors are read-only — the
            # decorators below are intended for regular functions on
            # class bodies, where setattr always succeeds.
            raise TypeError(
                "mango dispatch decorators must be applied to plain "
                "functions defined in a class body, not "
                f"{type(method).__name__}"
            ) from None
    return meta


def on_message(
    message_type: type,
    *,
    where: Callable[..., bool] | None = None,
    priority: int = 0,
) -> Callable:
    """Subscribe the decorated method to *message_type*.

    The handler is called as ``handler(self, content, meta)``.  Async
    handlers are scheduled as instant tasks automatically.

    :param message_type: only deliver messages where
        ``isinstance(content, message_type)`` is true.
    :param where: optional extra filter.  Called as
        ``where(self, content, meta) -> bool`` — receives ``self``
        so the filter can read role state (e.g. a sector tag) without
        capturing it in a closure at class-define time.  If ``None``
        any message of *message_type* is accepted.
    :param priority: forwarded to :meth:`RoleContext.subscribe_message`
        (lower runs first).
    """

    def decorator(method: Callable) -> Callable:
        meta = _get_or_create_meta(method)
        meta.message_subs.append(
            {
                "message_type": message_type,
                "where": where,
                "priority": priority,
            }
        )
        return method

    return decorator


def on_event(event_type: type) -> Callable:
    """Subscribe the decorated method to a co-located event type.

    The handler is called as ``handler(self, event, source)`` and runs
    synchronously inside :meth:`RoleContext.emit_event`.
    """

    def decorator(method: Callable) -> Callable:
        meta = _get_or_create_meta(method)
        meta.event_subs.append({"event_type": event_type})
        return method

    return decorator


def periodic(
    every: float | str,
    *,
    only_if: Callable[..., bool] | None = None,
) -> Callable:
    """Schedule the decorated coroutine method as a periodic task.

    :param every: period in seconds, or a string key looked up on the
        role instance at attach time (e.g. ``"poll_period_s"`` reads
        ``role.poll_period_s`` — useful for sector-dependent periods
        configured per role instance).
    :param only_if: optional predicate ``only_if(self) -> bool`` evaluated
        at every firing.  When false the task body is skipped — the
        scheduler still runs but the handler returns early.  This
        replaces the "if not leader: return" guard that every periodic
        coroutine in scare repeats by hand.
    """

    def decorator(method: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(method):
            raise TypeError(
                f"@periodic must decorate an async coroutine function; "
                f"{method.__qualname__} is not a coroutine"
            )
        meta = _get_or_create_meta(method)
        meta.periodic.append({"every": every, "only_if": only_if})
        return method

    return decorator


def collect_dispatch(cls: type) -> dict[str, _Dispatch]:
    """Walk ``cls`` (and bases) and collect every dispatch-decorated method.

    Returns a dict keyed by method name; the same method name can carry
    multiple subscriptions because :class:`_Dispatch` is a list of
    each kind.  Subclass methods *replace* base-class entries with the
    same name (standard MRO), but unrelated method names from a base
    class are still collected — so a base role that declares
    ``@periodic`` decorated methods is inherited by subclasses
    transparently.
    """
    out: dict[str, _Dispatch] = {}
    for klass in reversed(cls.__mro__):
        for name, attr in klass.__dict__.items():
            meta = getattr(attr, _MANGO_DISPATCH_META, None)
            if isinstance(meta, _Dispatch):
                out[name] = meta
    return out


def _bind_async(method: Callable, role: Any) -> Callable:
    """Return a sync callback that schedules *method* as an instant task.

    Used to bridge async ``@on_message`` handlers to the synchronous
    ``subscribe_message`` callback contract.  Mirrors the
    ``def _wrap(coro_fn): ...`` shim every legacy role declares.
    """

    def _sync(content: Any, meta: dict) -> None:
        coro = method(role, content, meta)
        role.context.schedule_instant_task(coro)

    return _sync


def _bind_sync(method: Callable, role: Any) -> Callable:
    """Return a sync callback that calls *method* directly (for sync handlers)."""

    def _sync(content: Any, meta: dict) -> None:
        method(role, content, meta)

    return _sync


def apply_dispatch(role: Any) -> None:
    """Apply every collected dispatch entry on ``role`` to the bound context.

    Called by :class:`Role._bind` after the role context is wired but
    before user-defined ``setup()``.  Splitting this out keeps the
    decoration mechanism orthogonal from the Role base class — tests
    can call ``apply_dispatch(role)`` against a mock context.
    """
    dispatch = collect_dispatch(type(role))
    ctx = role.context
    for name, meta in dispatch.items():
        method = getattr(type(role), name)  # unbound function
        for sub in meta.message_subs:
            mtype = sub["message_type"]
            where = sub["where"]
            priority = sub["priority"]
            if where is None:

                def condition(content, _meta, _mtype=mtype):
                    return isinstance(content, _mtype)
            else:

                def condition(content, _meta, _mtype=mtype, _w=where, _r=role):
                    return isinstance(content, _mtype) and _w(_r, content, _meta)

            if asyncio.iscoroutinefunction(method):
                callback = _bind_async(method, role)
            else:
                callback = _bind_sync(method, role)
            ctx.subscribe_message(role, callback, condition, priority=priority)
        for sub in meta.event_subs:
            etype = sub["event_type"]
            handler = getattr(role, name)
            ctx.subscribe_event(role, etype, handler)
        for sub in meta.periodic:
            every = sub["every"]
            only_if = sub["only_if"]
            if isinstance(every, str):
                delay = getattr(role, every)
            else:
                delay = float(every)
            coro_method = getattr(role, name)
            if only_if is None:
                schedule = coro_method
            else:

                def _gated(_role=role, _coro=coro_method, _gate=only_if):
                    async def _run():
                        if not _gate(_role):
                            return
                        await _coro()

                    return _run()

                schedule = _gated
            ctx.schedule_periodic_task(schedule, delay=delay)
