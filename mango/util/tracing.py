"""
Opt-in tracing of agent internals with structlog, for debugging agent algorithms.

Every piece of work in a mango agent is started by one of three triggers:

* a **message** arriving in the agent's inbox,
* an **event** (a global or targeted simulation event, or a role event emitted
  on the agent's internal event bus),
* a **scheduled task** becoming due.

When tracing is enabled, mango logs every lifecycle step of these triggers and
of the tasks they start through structlog, using the application's structlog
configuration. While a trigger or task is processed, mango binds its id as
``cause`` and the agent id as ``agent`` with :mod:`structlog.contextvars`, so

* records of tasks scheduled and messages sent from a handler point at the
  trigger that started them, and
* the application's own structlog calls inside handlers and tasks carry the
  same fields and can be read in line with mango's records.

mango logs its records at ``debug`` level, failures (``*.failed``) at
``error``, so a level filter separates them from the application's logs.

Tracing records the process, not the simulation's data: message contents and
events are summarized by their type unless ``include_content`` is set. To
store data, use the recording functions of :mod:`mango.simulation.recording`.

Requires ``structlog`` (``pip install mango-agents[trace]``).

Example::

    import structlog
    from mango import configure_structlog, enable_tracing

    configure_structlog("trace.jsonl")  # or use an own structlog configuration
    enable_tracing()

    log = structlog.get_logger()

    class MyAgent(Agent):
        def handle_message(self, content, meta):
            log.info("price.received", price=content.price)
            # -> {"event": "price.received", "agent": "agent0",
            #     "cause": "message-7", "price": 3.2, ...}
"""

from __future__ import annotations

import inspect
import itertools
import json
import os
import platform
import sys
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

try:
    import structlog
except ImportError:  # pragma: no cover - exercised without the extra
    structlog = None

#: All categories mango can trace.
#:
#: * ``run``: tracing start/stop and container start
#: * ``message``: messages sent and received, and the handlers they reach
#: * ``event``: global, agent and role events, and the handlers they reach
#: * ``task``: scheduled tasks (scheduled, started, cycles, finished)
#: * ``wait``: tasks starting and stopping to wait (timer, awaited future,
#:   condition, ...)
CATEGORIES = frozenset({"run", "message", "event", "task", "wait"})

#: Logger name used for mango's trace records.
LOGGER_NAME = "mango.trace"

#: Meta key carrying the id of the ``message.sent`` record to the receiver.
TRACE_MESSAGE_ID_KEY = "_trace_msg_id"

_CONTENT_REPR_LIMIT = 200

# None while tracing is off; otherwise the enabled categories.
_categories: frozenset[str] | None = None
_include_content = False
_ids = itertools.count(1)
# trace file opened by configure_structlog, closed when it is reconfigured
_configured_file = None

# Clock of the agent whose code is currently executing, read by
# :func:`add_sim_time`. Not a structlog contextvar because the value is
# read at log time, not bound once.
_current_clock: ContextVar[Any] = ContextVar("_mango_trace_clock", default=None)


def _require_structlog():
    if structlog is None:
        raise ImportError(
            "structlog is required for tracing. "
            "Install it with: pip install mango-agents[trace]"
        )
    return structlog


def enable_tracing(
    *,
    categories: Iterable[str] | None = None,
    include_content: bool = False,
) -> None:
    """Start logging mango's trace records through structlog.

    Where the records go is decided by the structlog configuration; see
    :func:`configure_structlog` for a ready-made one. Agents in subprocesses
    (``as_agent_process``) are not traced yet.

    :param categories: subset of :data:`CATEGORIES` to record; all by default
    :param include_content: also record a shortened ``repr`` of message
        contents and events instead of only their type
    """
    global _categories, _include_content
    _require_structlog()
    selected = CATEGORIES if categories is None else frozenset(categories)
    unknown = selected - CATEGORIES
    if unknown:
        raise ValueError(
            f"Unknown trace categories {sorted(unknown)}; "
            f"choose from {sorted(CATEGORIES)}"
        )
    _categories = selected
    _include_content = include_content
    _emit(
        "trace.started",
        "run",
        mango_version=_mango_version(),
        python_version=platform.python_version(),
        pid=os.getpid(),
        categories=sorted(selected),
    )


def disable_tracing() -> None:
    """Stop logging mango's trace records. No-op if tracing is off."""
    global _categories
    if _categories is None:
        return
    _emit("trace.stopped", "run")
    _categories = None


@contextmanager
def trace_session(**kwargs):
    """Context manager enabling tracing for the enclosed block.

    Accepts the same keyword arguments as :func:`enable_tracing`.
    """
    enable_tracing(**kwargs)
    try:
        yield
    finally:
        disable_tracing()


def is_tracing(category: str | None = None) -> bool:
    """Return whether tracing is on (for *category*, if given)."""
    if _categories is None:
        return False
    return category is None or category in _categories


def add_sim_time(logger, method_name, event_dict):
    """structlog processor adding ``sim_time``: the mango clock time of the
    agent whose handler or task is logging (simulated time under an
    ``ExternalClock``). Leaves records outside agent code unchanged."""
    clock = _current_clock.get()
    if clock is not None and "sim_time" not in event_dict:
        event_dict["sim_time"] = clock.time
    return event_dict


def configure_structlog(
    path: str | os.PathLike | None = None,
    *,
    processors: Iterable[Callable] = (),
    json_output: bool = True,
) -> None:
    """Configure structlog globally for debugging a mango application.

    Optional convenience: applications with an own structlog configuration
    only need ``structlog.contextvars.merge_contextvars`` (and optionally
    :func:`add_sim_time`) in their processor chain instead.

    :param path: file to write to (overwritten); stderr if ``None``
    :param processors: extra processors, run before rendering
    :param json_output: render JSON lines (default), else a console format
    """
    global _configured_file
    structlog = _require_structlog()
    if _configured_file is not None:
        _configured_file.close()
        _configured_file = None
    if path is None:
        file = sys.stderr
    else:
        file = _configured_file = open(path, "w", encoding="utf-8")
    renderer = (
        structlog.processors.JSONRenderer(default=str)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=path is None)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            add_sim_time,
            *processors,
            renderer,
        ],
        logger_factory=structlog.WriteLoggerFactory(file),
        cache_logger_on_first_use=False,
    )


def read_trace(path: str | os.PathLike) -> list[dict]:
    """Load a JSON-lines trace written with :func:`configure_structlog`."""
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Hooks used by mango's internals. All of them return immediately when
# tracing is off.
# ---------------------------------------------------------------------------


def _emit(event: str, category: str, clock=None, level="debug", **fields) -> None:
    if _categories is None or category not in _categories:
        return
    if clock is not None:
        fields["sim_time"] = clock.time
    logger = structlog.get_logger(LOGGER_NAME)
    getattr(logger, level)(event, category=category, **fields)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{next(_ids)}"


def _summary(obj: Any) -> dict:
    summary = {"type": type(obj).__qualname__}
    if _include_content:
        text = repr(obj)
        if len(text) > _CONTENT_REPR_LIMIT:
            text = text[:_CONTENT_REPR_LIMIT] + "..."
        summary["repr"] = text
    return summary


def _handler_name(handler: Callable) -> str:
    # follow the sync bridges mango puts around async handlers
    func = inspect.unwrap(getattr(handler, "__func__", handler))
    func = getattr(func, "__func__", func)
    return getattr(func, "__qualname__", None) or repr(handler)


def _mango_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("mango-agents")
    except Exception:
        return None


def _bind_scope(scope_id: str, agent: str | None, clock) -> None:
    """Make *scope_id* the cause of everything logged in the current context."""
    structlog.contextvars.bind_contextvars(cause=scope_id, agent=agent)
    _current_clock.set(clock)


@contextmanager
def _trigger(
    kind: str,
    category: str,
    *,
    agent: str | None,
    clock=None,
    cause: str | None = None,
    **fields,
):
    """Scope in which an agent processes a trigger of *kind*.

    Logs ``<kind>.received`` with *cause* (the record of the send or emit
    that produced the trigger, else the currently bound cause) and a summary
    of a ``content`` field; everything logged and scheduled inside the scope
    gets the trigger as its ``cause``. An exception escaping the scope is
    logged as ``<kind>.failed`` and re-raised.

    The context is bound whenever tracing is on, even if *category* is not
    recorded, so application logs keep their ``agent`` and ``cause``.
    """
    if not is_tracing():
        yield
        return
    trigger_id = _new_id(kind)
    if "content" in fields:
        fields["content"] = _summary(fields["content"])
    if cause is None:
        # triggers raised by agent code (role events) are caused by it
        cause = structlog.contextvars.get_contextvars().get("cause")
    _emit(
        f"{kind}.received",
        category,
        clock,
        id=trigger_id,
        agent=agent,
        cause=cause,
        **fields,
    )
    with structlog.contextvars.bound_contextvars(cause=trigger_id, agent=agent):
        clock_token = _current_clock.set(clock)
        try:
            yield
        except Exception as exc:
            _emit(
                f"{kind}.failed",
                category,
                level="error",
                id=trigger_id,
                error=repr(exc),
            )
            raise
        finally:
            _current_clock.reset(clock_token)


def _handler_called(category: str, handler: Callable) -> None:
    """Log that *handler* is invoked for the current trigger."""
    if is_tracing(category):
        _emit("handler.called", category, handler=_handler_name(handler))


def _message_sent(content: Any, sender_id, receiver_addr, kwargs: dict, clock) -> None:
    """Log an outgoing message and tag its meta so the receipt links back."""
    if not is_tracing("message"):
        return
    msg_id = _new_id("msg")
    kwargs[TRACE_MESSAGE_ID_KEY] = msg_id
    _emit(
        "message.sent",
        "message",
        clock,
        id=msg_id,
        sender=sender_id,
        receiver=str(receiver_addr),
        content=_summary(content),
    )


def _event_emitted(event: Any, target: str | None = None) -> str | None:
    """Log an environment event once, before it is dispatched to agents."""
    if not is_tracing("event"):
        return None
    event_id = _new_id("event")
    _emit("event.emitted", "event", id=event_id, target=target, content=_summary(event))
    return event_id


# Scheduled tasks ----------------------------------------------------------


def _task_scheduled(task, owner: str | None, src: Any, process: bool) -> None:
    if not is_tracing("task"):
        return
    task._trace_id = _new_id("task")
    task._trace_owner = owner
    _emit(
        "task.scheduled",
        "task",
        task.clock,
        id=task._trace_id,
        agent=owner,
        task_type=type(task).__name__,
        process=process,
        src=type(src).__qualname__ if src is not None else None,
        **task._trace_info(),
    )


def _task_started(task) -> None:
    """Called inside the task's own asyncio context, so the binding only
    affects this task (and tasks it creates)."""
    if task._trace_id is None or not is_tracing():
        return
    # The record keeps the scheduling trigger as its cause; from here on the
    # task itself is the cause.
    _emit(
        "task.started", "task", task.clock, id=task._trace_id, agent=task._trace_owner
    )
    _bind_scope(task._trace_id, task._trace_owner, task.clock)


def _task_cycle(task, cycle: int) -> None:
    if task._trace_id is not None:
        _emit(
            "task.cycle",
            "task",
            task.clock,
            id=task._trace_id,
            agent=task._trace_owner,
            cycle=cycle,
        )


def _task_done(task, fut) -> None:
    if task._trace_id is None or not is_tracing("task"):
        return
    fields = {}
    if fut.cancelled():
        outcome = "cancelled"
    elif fut.exception() is not None:
        outcome = "failed"
        fields["level"] = "error"
        fields["error"] = repr(fut.exception())
    else:
        outcome = "finished"
    # Done callbacks run outside the task's context, so bind explicitly.
    with structlog.contextvars.bound_contextvars(
        cause=task._trace_id, agent=task._trace_owner
    ):
        _emit(f"task.{outcome}", "task", task.clock, id=task._trace_id, **fields)


def _task_wait(task, waiting: bool) -> None:
    if task._trace_id is not None:
        _emit(
            "task.waiting" if waiting else "task.resumed",
            "wait",
            task.clock,
            id=task._trace_id,
        )
